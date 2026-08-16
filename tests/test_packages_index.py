"""PRD-011 slice 3 — the local index client, resolution, and the offline path.

This closes the install loop end to end with no server, no kernel and no
network. Two claims carry the weight and both are tested against their
negation:

* **precedence and failure-as-data** — the first index that answers wins, a
  broken index is skipped with a reason rather than making the others
  unreachable, and an unresolvable name names every index tried and why;
* **offline is not a second answer** — with the index deleted, `add_package`
  resolves from the cache and writes a lock entry that is **byte-identical**
  to the one the online install wrote, because every field in it is
  content-determined.
"""

import contextlib
import json
import queue
import shutil
import time

import pytest

from agentcad.core.model import NotFoundError, ValidationError
from agentcad.core.packages import _git, cache, content, indexes, lockfile
from agentcad.core.packages.manager import PackageManager
from .conftest import BOX_SCRIPT, make_test_service

PART_SCRIPT = "PARAMS = {}\n\n\ndef build(p):\n    return None\n"


# --------------------------------------------------------------- builders


def package_tree(root, name="iso4762", body=PART_SCRIPT, version=None):
    # The tree's package.json names BOTH halves of its identity: `cache.install`
    # proves the tree agrees with the `name@version` it is being filed under
    # (Codex #6), so a fixture that omits its version is not installable — and
    # the version is taken from the directory, which is where these fixtures
    # already encode it (`<name>/<version>/`).
    (root / "parts").mkdir(parents=True, exist_ok=True)
    (root / "parts" / "cap_screw.py").write_text(body)
    (root / "package.json").write_text(json.dumps(
        {"name": name, "version": version or root.name}) + "\n")
    (root / "docs").mkdir(exist_ok=True)
    (root / "docs" / "README.md").write_text(f"# {name}\n")
    return root


def entry_for(index_root, rel_path, **overrides):
    entry = {
        "content_id": content.content_id(index_root / rel_path),
        "path": rel_path,
        "summary": "socket-head cap screws",
        "keywords": ["fastener"],
        "standards": ["ISO 4762"],
        "license": "Apache-2.0",
        "disclosure": "agent",
        "parts": {"cap_screw": {"params": [], "connectors": {}, "specs": []}},
        "presets": [],
        "previews": [],
        "gate": {"status": "green", "exempt_skips": [], "agentcad": "0.1.0",
                 "build123d": "0.11.1", "report_id": "sha256:" + "ab" * 32},
        "yanked": False,
        "signatures": [],
    }
    entry.update(overrides)
    return entry


def make_index(root, name="agentcad-core", scope="public",
               packages=(("iso4762", "1.0.0"),), body=PART_SCRIPT):
    """A published index directory: `index.json` plus one directory per
    package version. (Slice 8 builds these with `agentcad publish`; here the
    fixture stands in for it.)"""
    root.mkdir(parents=True, exist_ok=True)
    doc = {"format": 1, "name": name, "scope": scope, "packages": {},
           "embeddings": None}
    for pkg_name, version in packages:
        rel = f"{pkg_name}/{version}"
        package_tree(root / rel, pkg_name, body)
        doc["packages"].setdefault(pkg_name, {"versions": {}})
        doc["packages"][pkg_name]["versions"][version] = entry_for(root, rel)
    write_index(root, doc)
    return root


def write_index(root, doc):
    (root / "index.json").write_text(json.dumps(doc, indent=2))


def read_index(root):
    return json.loads((root / "index.json").read_text())


@pytest.fixture
def cache_root(tmp_path, monkeypatch):
    root = tmp_path / "cache"
    monkeypatch.setenv("AGENTCAD_PACKAGES_DIR", str(root))
    monkeypatch.setenv("AGENTCAD_CONFIG", str(tmp_path / "cfg" / "config.json"))
    return root


@pytest.fixture
def service(tmp_path, kernel):
    svc = make_test_service(tmp_path / "projects", kernel)
    svc.create_project("rig")
    return svc


def manager(service, *index_objs):
    return PackageManager(service, indexes=list(index_objs))


# ------------------------------------------------------------ local index


def test_a_local_index_parses_and_validates_its_document(tmp_path):
    root = make_index(tmp_path / "catalog")
    index = indexes.LocalIndex("agentcad-core", root)
    assert index.kind == "local"
    assert index.entries()["name"] == "agentcad-core"
    assert list(index.versions("iso4762")) == ["1.0.0"]
    assert index.versions("nothing") == {}


def test_an_invalid_index_document_raises_rather_than_answering_nonsense(tmp_path):
    root = make_index(tmp_path / "catalog")
    doc = read_index(root)
    doc["packages"]["iso4762"]["versions"]["1.0.0"]["content_id"] = "nope"
    write_index(root, doc)
    index = indexes.LocalIndex("agentcad-core", root)
    with pytest.raises(ValidationError) as exc:
        index.entries()
    assert "agentcad-core" in str(exc.value)


def test_an_unreadable_or_absent_index_document_is_not_found(tmp_path):
    missing = indexes.LocalIndex("gone", tmp_path / "nowhere")
    with pytest.raises(NotFoundError):
        missing.entries()
    root = tmp_path / "broken"
    root.mkdir()
    (root / "index.json").write_text("{not json")
    with pytest.raises(ValidationError):
        indexes.LocalIndex("broken", root).entries()


def test_entries_are_cached_on_the_file_stamp_and_reread_after_a_write(tmp_path):
    root = make_index(tmp_path / "catalog")
    index = indexes.LocalIndex("agentcad-core", root)
    first = index.entries()
    assert index.entries() is first          # same object: no re-parse
    doc = read_index(root)
    doc["packages"]["din625"] = {"versions": {}}
    write_index(root, doc)
    assert "din625" in index.entries()["packages"]


def test_fetch_returns_the_package_directory(tmp_path):
    root = make_index(tmp_path / "catalog")
    index = indexes.LocalIndex("agentcad-core", root)
    assert index.fetch("iso4762", "1.0.0") == (root / "iso4762" / "1.0.0").resolve()
    with pytest.raises(NotFoundError):
        index.fetch("iso4762", "9.9.9")


@pytest.mark.parametrize("path", ["../outside", "/etc", "iso4762/../../outside"])
def test_fetch_refuses_an_entry_whose_path_escapes_the_index_root(tmp_path, path):
    root = make_index(tmp_path / "catalog")
    (tmp_path / "outside").mkdir(exist_ok=True)
    doc = read_index(root)
    doc["packages"]["iso4762"]["versions"]["1.0.0"]["path"] = path
    write_index(root, doc)
    index = indexes.LocalIndex("agentcad-core", root)
    with pytest.raises(ValidationError):
        # the document is refused by validation, and — were it not — the
        # containment check refuses the fetch. Both are the same answer.
        index.fetch("iso4762", "1.0.0")


def test_fetch_reports_a_declared_directory_that_is_not_there(tmp_path):
    root = make_index(tmp_path / "catalog")
    shutil.rmtree(root / "iso4762" / "1.0.0")
    index = indexes.LocalIndex("agentcad-core", root)
    with pytest.raises(NotFoundError) as exc:
        index.fetch("iso4762", "1.0.0")
    assert "iso4762/1.0.0" in str(exc.value)


def test_source_of_records_the_index_relative_path_only(tmp_path):
    """An absolute path is a machine fact and a lock entry may not hold one."""
    root = make_index(tmp_path / "catalog")
    index = indexes.LocalIndex("agentcad-core", root)
    entry = index.entry("iso4762", "1.0.0")
    assert index.source_of(entry) == {"kind": "local", "path": "iso4762/1.0.0"}
    assert str(tmp_path) not in json.dumps(index.source_of(entry))


def test_scope_comes_from_the_document_and_falls_back_to_the_configuration(tmp_path):
    private = make_index(tmp_path / "acme", name="acme", scope="private")
    assert indexes.LocalIndex("acme", private).scope == "private"
    empty = tmp_path / "nothing"
    # No document to read: the configured value, and "public" by default —
    # the fail-closed direction for the vendor gate.
    assert indexes.LocalIndex("x", empty, scope="private").scope == "private"
    assert indexes.LocalIndex("x", empty).scope == "public"


# ------------------------------------------------------- configuration


def test_load_indexes_builds_in_precedence_order(tmp_path):
    config = {"indexes": [
        {"name": "agentcad-core", "kind": "local", "path": str(tmp_path / "a")},
        {"name": "acme", "kind": "local", "path": str(tmp_path / "b"),
         "scope": "private"},
    ]}
    built = indexes.load_indexes(config)
    assert [i.name for i in built] == ["agentcad-core", "acme"]
    assert built[1]._configured_scope == "private"


@pytest.mark.parametrize("entry,expected", [
    ({"kind": "local", "path": "/x"}, "name"),
    ({"name": "Bad Name", "kind": "local", "path": "/x"}, "name"),
    ({"name": "acme", "kind": "sftp", "path": "/x"}, "unknown kind"),
    ({"name": "acme", "kind": "local"}, "path"),
    # `git` became a real kind in slice 9; `cloud` is the one still planned.
    ({"name": "acme", "kind": "cloud", "url": "https://x"}, "not available"),
    ("not-an-object", "not an object"),
])
def test_load_indexes_skips_a_broken_entry_with_a_warning(entry, expected):
    warnings = []
    assert indexes.load_indexes({"indexes": [entry]}, warnings) == []
    assert expected in " ".join(warnings)


def test_load_indexes_skips_a_duplicate_name(tmp_path):
    warnings = []
    built = indexes.load_indexes({"indexes": [
        {"name": "acme", "kind": "local", "path": str(tmp_path / "a")},
        {"name": "acme", "kind": "local", "path": str(tmp_path / "b")},
    ]}, warnings)
    assert [i.path for i in built] == [tmp_path / "a"]
    assert "configured twice" in " ".join(warnings)


def test_no_indexes_configured_is_an_empty_list_not_an_error():
    assert indexes.load_indexes({}) == []
    assert indexes.load_indexes({"indexes": "nope"}) == []


# ---------------------------------------------------------------- resolve


def test_resolve_walks_indexes_in_precedence_order(tmp_path, cache_root, service):
    first = make_index(tmp_path / "one", name="one")
    second = make_index(tmp_path / "two", name="two")
    mgr = manager(service, indexes.LocalIndex("one", first),
                  indexes.LocalIndex("two", second))
    assert mgr.resolve("iso4762")["index"] == "one"


def test_resolve_pins_an_index_when_asked(tmp_path, cache_root, service):
    first = make_index(tmp_path / "one", name="one")
    second = make_index(tmp_path / "two", name="two")
    mgr = manager(service, indexes.LocalIndex("one", first),
                  indexes.LocalIndex("two", second))
    assert mgr.resolve("iso4762", index="two")["index"] == "two"
    with pytest.raises(NotFoundError):
        mgr.resolve("iso4762", index="three")


def test_resolve_picks_the_highest_matching_non_yanked_version(
        tmp_path, cache_root, service):
    root = make_index(tmp_path / "catalog",
                      packages=[("iso4762", "1.0.0"), ("iso4762", "1.2.0"),
                                ("iso4762", "2.0.0")])
    mgr = manager(service, indexes.LocalIndex("agentcad-core", root))
    assert mgr.resolve("iso4762", "^1.0.0")["version"] == "1.2.0"
    assert mgr.resolve("iso4762")["version"] == "2.0.0"

    doc = read_index(root)
    doc["packages"]["iso4762"]["versions"]["1.2.0"]["yanked"] = True
    write_index(root, doc)
    assert mgr.resolve("iso4762", "^1.0.0")["version"] == "1.0.0"


def test_a_requirement_matched_only_by_a_yanked_version_says_so(
        tmp_path, cache_root, service):
    root = make_index(tmp_path / "catalog", packages=[("iso4762", "1.0.0")])
    doc = read_index(root)
    doc["packages"]["iso4762"]["versions"]["1.0.0"]["yanked"] = True
    write_index(root, doc)
    mgr = manager(service, indexes.LocalIndex("agentcad-core", root))
    with pytest.raises(NotFoundError) as exc:
        mgr.resolve("iso4762", "^1.0.0")
    assert "only yanked" in str(exc.value)


def test_resolve_names_every_index_it_tried_and_why(tmp_path, cache_root, service):
    empty = make_index(tmp_path / "one", name="one", packages=[])
    other = make_index(tmp_path / "two", name="two",
                       packages=[("din625", "1.0.0")])
    mgr = manager(service, indexes.LocalIndex("one", empty),
                  indexes.LocalIndex("two", other))
    with pytest.raises(NotFoundError) as exc:
        mgr.resolve("iso4762", "^1.0.0")
    tried = exc.value.details["tried"]
    assert [t["index"] for t in tried] == ["one", "two"]
    assert all("iso4762" in t["reason"] for t in tried)
    assert "^1.0.0" in str(exc.value)


def test_a_malformed_index_does_not_stop_the_others_and_is_named(
        tmp_path, cache_root, service):
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "index.json").write_text('{"format": 1}')
    good = make_index(tmp_path / "good", name="good")
    mgr = manager(service, indexes.LocalIndex("broken", broken),
                  indexes.LocalIndex("good", good))
    resolution = mgr.resolve("iso4762")
    assert resolution["index"] == "good"
    assert resolution["tried"][0]["index"] == "broken"
    assert "invalid" in resolution["tried"][0]["reason"]


def test_resolve_refuses_a_requirement_it_cannot_parse(
        tmp_path, cache_root, service):
    root = make_index(tmp_path / "catalog")
    mgr = manager(service, indexes.LocalIndex("agentcad-core", root))
    with pytest.raises(ValidationError):
        mgr.resolve("iso4762", ">=1.0.0")


# -------------------------------------------------------------------- add


def events_of(service):
    q = service.bus.subscribe()
    return q


def drain(q):
    out = []
    while True:
        try:
            out.append(q.get_nowait())
        except queue.Empty:
            return out


def test_add_installs_the_tree_and_writes_both_maps(tmp_path, cache_root, service):
    root = make_index(tmp_path / "catalog")
    mgr = manager(service, indexes.LocalIndex("agentcad-core", root))
    q = events_of(service)

    result = mgr.add("rig", "iso4762", "^1.0.0")

    manifest = service.store.manifest("rig")
    assert manifest["packages"] == {
        "iso4762": {"version_req": "^1.0.0", "index": "agentcad-core"}
    }
    lock = manifest["packages_lock"]["iso4762"]
    assert lock["version"] == "1.0.0"
    assert lock["content_id"] == content.content_id(root / "iso4762" / "1.0.0")
    assert lock["source"] == {"kind": "local", "path": "iso4762/1.0.0"}
    assert result["offline"] is False
    assert cache.verify("iso4762", "1.0.0")["status"] == "ok"
    assert (cache_root / "iso4762" / "1.0.0" / "parts" / "cap_screw.py").is_file()

    published = [e for e in drain(q) if e["type"] == "project_changed"]
    assert len(published) == 1 and published[0]["project"] == "rig"


def test_add_refuses_an_index_entry_whose_content_id_is_a_lie(
        tmp_path, cache_root, service):
    """The index is data from elsewhere. If it declares one hash and the tree
    hashes to another, nothing is installed and both ids are named."""
    root = make_index(tmp_path / "catalog")
    (root / "iso4762" / "1.0.0" / "parts" / "cap_screw.py").write_text("# swapped\n")
    mgr = manager(service, indexes.LocalIndex("agentcad-core", root))
    with pytest.raises(ValidationError) as exc:
        mgr.add("rig", "iso4762")
    assert "content id mismatch" in str(exc.value)
    assert cache.verify("iso4762", "1.0.0")["status"] == "missing"
    assert "packages" not in service.store.manifest("rig")


def test_adding_the_same_package_twice_is_idempotent(tmp_path, cache_root, service):
    root = make_index(tmp_path / "catalog")
    mgr = manager(service, indexes.LocalIndex("agentcad-core", root))
    first = mgr.add("rig", "iso4762", "^1.0.0")
    before = json.dumps(service.store.manifest("rig"), indent=2)
    second = mgr.add("rig", "iso4762", "^1.0.0")
    assert first["lock"] == second["lock"]
    assert json.dumps(service.store.manifest("rig"), indent=2) == before


# ---------------------------------------------------------------- offline


def test_an_offline_add_writes_a_byte_identical_lock_entry(
        tmp_path, cache_root, service):
    """AC3/AC4: with the index gone, the cache answers — and the entry it
    writes is the same bytes, because every field in it is
    content-determined."""
    root = make_index(tmp_path / "catalog")
    mgr = manager(service, indexes.LocalIndex("agentcad-core", root))
    online = mgr.add("rig", "iso4762", "^1.0.0")

    shutil.rmtree(root)
    service.create_project("rig2")
    offline = mgr.add("rig2", "iso4762", "^1.0.0")

    assert offline["offline"] is True
    assert json.dumps(online["lock"], indent=2) == json.dumps(
        offline["lock"], indent=2)
    assert json.dumps(online["package"]) == json.dumps(offline["package"])


def test_offline_resolution_needs_a_cached_tree_that_still_verifies(
        tmp_path, cache_root, service):
    root = make_index(tmp_path / "catalog")
    mgr = manager(service, indexes.LocalIndex("agentcad-core", root))
    mgr.add("rig", "iso4762", "^1.0.0")
    shutil.rmtree(root)
    (cache_root / "iso4762" / "1.0.0" / "parts" / "cap_screw.py").write_text("#\n")

    service.create_project("rig2")
    with pytest.raises(NotFoundError) as exc:
        mgr.add("rig2", "iso4762", "^1.0.0")
    reasons = " ".join(t["reason"] for t in exc.value.details["tried"])
    assert "tampered" in reasons


def test_offline_resolution_honours_the_requirement(tmp_path, cache_root, service):
    root = make_index(tmp_path / "catalog",
                      packages=[("iso4762", "1.0.0"), ("iso4762", "2.0.0")])
    mgr = manager(service, indexes.LocalIndex("agentcad-core", root))
    mgr.add("rig", "iso4762", "1.0.0")
    mgr.add("rig", "iso4762", "2.0.0")
    shutil.rmtree(root)
    service.create_project("rig2")
    assert mgr.add("rig2", "iso4762", "^1.0.0")["lock"]["version"] == "1.0.0"


def test_a_pinned_index_is_not_satisfied_by_another_indexs_cache_entry(
        tmp_path, cache_root, service):
    root = make_index(tmp_path / "catalog", name="agentcad-core")
    mgr = manager(service, indexes.LocalIndex("agentcad-core", root),
                  indexes.LocalIndex("acme", tmp_path / "acme"))
    mgr.add("rig", "iso4762")
    shutil.rmtree(root)
    service.create_project("rig2")
    with pytest.raises(NotFoundError):
        mgr.add("rig2", "iso4762", index="acme")


def test_neither_index_nor_cache_is_not_found_naming_both(
        tmp_path, cache_root, service):
    mgr = manager(service, indexes.LocalIndex("agentcad-core",
                                              tmp_path / "nowhere"))
    with pytest.raises(NotFoundError) as exc:
        mgr.add("rig", "iso4762", "^1.0.0")
    assert "iso4762" in str(exc.value) and "^1.0.0" in str(exc.value)
    assert "packages" not in service.store.manifest("rig")


def test_with_no_indexes_configured_the_cache_still_answers(
        tmp_path, cache_root, service):
    root = make_index(tmp_path / "catalog")
    mgr = manager(service, indexes.LocalIndex("agentcad-core", root))
    mgr.add("rig", "iso4762")

    bare = manager(service)          # no indexes at all
    service.create_project("rig2")
    assert bare.add("rig2", "iso4762")["offline"] is True


# ----------------------------------------------------------------- remove


def test_remove_drops_both_entries_and_publishes(tmp_path, cache_root, service):
    root = make_index(tmp_path / "catalog")
    mgr = manager(service, indexes.LocalIndex("agentcad-core", root))
    mgr.add("rig", "iso4762")
    before = json.dumps(service.store.manifest("rig"), indent=2)
    q = events_of(service)

    result = mgr.remove("rig", "iso4762")

    assert result["materialized_parts"] == []
    manifest = service.store.manifest("rig")
    assert "packages" not in manifest and "packages_lock" not in manifest
    assert before != json.dumps(manifest, indent=2)
    assert len([e for e in drain(q) if e["type"] == "project_changed"]) == 1
    # the cache is shared by every project and is deliberately untouched
    assert cache.verify("iso4762", "1.0.0")["status"] == "ok"


def test_removing_a_package_that_is_not_installed_is_not_found(
        tmp_path, cache_root, service):
    with pytest.raises(NotFoundError):
        manager(service).remove("rig", "iso4762")


def test_a_project_that_never_used_packages_is_byte_identical_after_add_remove(
        tmp_path, cache_root, service):
    """FR15, end to end through the manager."""
    root = make_index(tmp_path / "catalog")
    mgr = manager(service, indexes.LocalIndex("agentcad-core", root))
    before = json.dumps(service.store.manifest("rig"), indent=2)
    mgr.add("rig", "iso4762")
    mgr.remove("rig", "iso4762")
    assert json.dumps(service.store.manifest("rig"), indent=2) == before


def test_the_manager_captures_no_late_loading_service_attribute(
        tmp_path, cache_root, service):
    """`tools_packages` loads at `pac`, before `tools_specs` (`s`) and
    `tools_versioning` (`v`). Constructing the manager must not read an
    attribute that does not exist yet."""

    class Bare:
        def __getattr__(self, name):
            raise AssertionError(f"PackageManager read service.{name} at "
                                 "construction time")

    PackageManager(Bare())
    PackageManager(Bare(), indexes=[])


def test_the_manager_reads_its_indexes_from_the_user_config(
        tmp_path, cache_root, service, monkeypatch):
    from agentcad import config as user_config

    root = make_index(tmp_path / "catalog")
    user_config.save_config({"indexes": [
        {"name": "agentcad-core", "kind": "local", "path": str(root)},
        {"name": "broken", "kind": "cloud", "url": "https://x"},
    ]})
    mgr = PackageManager(service)
    assert [i.name for i in mgr.indexes] == ["agentcad-core"]
    assert "not available" in " ".join(mgr.warnings)
    assert mgr.resolve("iso4762")["index"] == "agentcad-core"


def test_lockfile_and_manifest_agree_after_an_add(tmp_path, cache_root, service):
    root = make_index(tmp_path / "catalog")
    mgr = manager(service, indexes.LocalIndex("agentcad-core", root))
    result = mgr.add("rig", "iso4762", "^1.0.0")
    manifest = service.store.manifest("rig")
    assert lockfile.entry_for(manifest, "iso4762") == result["lock"]
    assert lockfile.requirement_for(manifest, "iso4762") == result["package"]


# ================ hostile documents and hostile urls (review fixes, cl 0181)


def test_a_json_bomb_in_one_index_leaves_the_next_index_answering(tmp_path,
                                                                  service):
    """**C2.** `json.loads` raises `RecursionError` on a deeply nested
    document, and `RecursionError` is not a `ValueError` — so the
    `(OSError, ValueError, UnicodeDecodeError)` every reader restated by hand
    did not catch it. A ~400 kB `index.json` took an unhandled exception
    straight out through `LocalIndex.entries`, `search.search` and
    `PackageManager.resolve`, which meant one poisoned index stopped every
    healthy index BEHIND it from getting its turn.

    "A broken index is a warning, never an exception" is the rule; this is it
    holding against the input that broke it.
    """
    from agentcad.core.packages import search

    evil = tmp_path / "evil"
    evil.mkdir()
    (evil / "index.json").write_text("[" * 200_000 + "]" * 200_000)
    good = make_index(tmp_path / "good")

    poisoned = indexes.LocalIndex("evil", evil)
    with pytest.raises(ValidationError) as info:
        poisoned.entries()
    assert "nests too deeply" in info.value.message

    clients = [poisoned, indexes.LocalIndex("agentcad-core", good)]
    result = search.search(clients, query=None)
    assert [hit["name"] for hit in result["hits"]] == ["iso4762"]
    assert any("evil" in warning for warning in result["warnings"])
    assert manager(service, *clients).resolve("iso4762", "*")["index"] \
        == "agentcad-core"


def test_an_index_document_over_the_byte_ceiling_is_refused_unparsed(tmp_path):
    """Bytes are refused BEFORE the parse: after it the memory is already
    spent, and a valid 126 MB document measured 1.66 GB RSS."""
    from agentcad.core.packages import _json

    root = tmp_path / "huge"
    root.mkdir()
    (root / "index.json").write_text(
        '{"format": 1, "pad": "' + "x" * (_json.MAX_INDEX_BYTES + 1) + '"}')
    with pytest.raises(ValidationError) as info:
        indexes.LocalIndex("huge", root).entries()
    assert "the ceiling is" in info.value.message


def test_a_control_character_in_an_index_path_is_reported_not_raised(tmp_path,
                                                                     service):
    """**m9.** A NUL is not a `..` and escapes nothing, but `os.stat` raises
    `ValueError: embedded null character` — an exception nothing here catches,
    because every caller catches `ValidationError`. It took `resolve` down
    before the next, healthy index got its turn (and was an unauthenticated
    500 on the preview route)."""
    from agentcad.core.packages import content, format as pkgformat

    assert content.is_safe_relpath("widget/\x001.0.0") is False
    bad = make_index(tmp_path / "hostile", name="hostile")
    doc = read_index(bad)
    doc["packages"]["iso4762"]["versions"]["1.0.0"]["path"] = "iso4762/\x001.0.0"
    write_index(bad, doc)
    assert pkgformat.validate_index(doc), "the document must be refused"

    good = make_index(tmp_path / "good")
    clients = [indexes.LocalIndex("hostile", bad),
               indexes.LocalIndex("agentcad-core", good)]
    assert manager(service, *clients).resolve("iso4762", "*")["index"] \
        == "agentcad-core"


@pytest.mark.parametrize("url", [
    "ssh://-oProxyCommand=x/repo.git",
    "git+ssh://-oProxyCommand=x/repo.git",
    "ssh://git@-oProxyCommand=x/repo.git",
    "ssh:///repo.git",
])
def test_an_ssh_host_that_would_be_read_as_an_option_is_refused(url):
    """**m12.** Checking that the whole url does not start with `-` is not
    enough: `ssh://-oProxyCommand=…/x.git` starts with `s`, passes every other
    rule, and git hands `-oProxyCommand=…` to **ssh**, where a leading `-` is
    an option. The `--` separator in `_git.run` protects git's own argv and
    does nothing for the arguments git passes on."""
    from agentcad.core.packages import _git

    with pytest.raises(ValidationError) as info:
        _git.validate_url(url)
    assert "host component" in info.value.message


def test_a_scp_like_url_with_an_option_shaped_host_is_refused_too():
    """`git@-oProxyCommand=x:repo.git` is refused by the scp-like grammar
    itself; the point is that it does not reach git either way."""
    from agentcad.core.packages import _git

    for url in ("git@-oProxyCommand=x:repo.git", "git@-h:repo.git"):
        with pytest.raises(ValidationError):
            _git.validate_url(url)


@pytest.mark.parametrize("url", [
    "ssh://git@example.com/repo.git",
    "git@example.com:org/repo.git",
    "https://example.com/repo.git",
    "file:///srv/index",
    "/srv/index",
])
def test_a_legitimate_url_still_passes(url):
    from agentcad.core.packages import _git

    assert _git.validate_url(url) == url


def test_a_ref_that_would_be_read_as_an_option_is_refused():
    """`--branch <ref>` sits BEFORE the `--` separator, so a ref starting with
    `-` is an option to git — the same class of hole, one line to close."""
    from agentcad.core.packages import _git

    with pytest.raises(ValidationError):
        _git.validate_ref("--upload-pack=touch /tmp/x")
    assert _git.validate_ref("main") == "main"


def test_the_cache_fallback_does_not_call_a_yanked_version_unyanked(
        tmp_path, cache_root, service):
    """**m14.** The offline resolution hard-coded `yanked: False`, including
    for a version it had just been told a REACHABLE index withdrew (the
    explicitly-named case, which still resolves by design). The cache quietly
    disagreeing with the index it consulted is the one thing this fallback may
    not do."""
    root = make_index(tmp_path / "catalog")
    mgr = manager(service, indexes.LocalIndex("agentcad-core", root))
    mgr.add("rig", "iso4762", "1.0.0")

    doc = read_index(root)
    doc["packages"]["iso4762"]["versions"]["1.0.0"]["yanked"] = True
    write_index(root, doc)
    shutil.rmtree(root / "iso4762")      # the index answers, the tree is gone

    resolution = mgr.resolve("iso4762", "1.0.0")
    assert resolution["offline"] is True
    assert resolution["yanked"] is True
    assert any("YANKED" in warning for warning in mgr.warnings)


def test_the_package_count_ceiling_fires_where_the_byte_ceiling_cannot(tmp_path):
    """Round 2, item 2. The two ceilings are not redundant, and the numbers in
    `_json.py` are measured rather than guessed.

    For a realistically-shaped index the BYTE ceiling is the one that fires
    (2 178 B/entry at `indent=2`, so 50 000 entries is ~104 MB and never gets
    counted). The COUNT ceiling exists for the pathological document, where a
    package record is 23 B and 32 MB would hold 1.46 M of them — which `search`
    sorts on every keystroke. This builds that document at just over the count
    and well under the bytes, so only one ceiling can be doing the work.
    """
    from agentcad.core.packages import _json

    root = tmp_path / "pathological"
    root.mkdir()
    doc = {"format": 1, "name": "agentcad-core", "scope": "public",
           "embeddings": None,
           "packages": {f"p{i}": {"versions": {}}
                        for i in range(_json.MAX_INDEX_PACKAGES + 1)}}
    text = json.dumps(doc, separators=(",", ":"))
    (root / "index.json").write_text(text)
    assert len(text.encode()) < _json.MAX_INDEX_BYTES, \
        "the byte ceiling must NOT be what refuses this document"

    with pytest.raises(ValidationError) as info:
        indexes.LocalIndex("agentcad-core", root).entries()
    assert "declares" in info.value.message and "packages" in info.value.message
    assert info.value.details["packages"] == _json.MAX_INDEX_PACKAGES + 1


# ================================= round 3 (Codex #3 tail, #4, #6, #10a, #12)


def test_a_yank_in_one_index_does_not_veto_another_indexs_package(
        tmp_path, cache_root, service):
    """**Codex #3, tail.** Withdrawal was tracked by version STRING, so index
    A yanking its own `1.0.0` suppressed a warm cache entry installed from
    index B — which never withdrew anything. A yank is a statement a publisher
    makes about *their* package; it binds their package only."""
    a = make_index(tmp_path / "a", name="index_a")
    b = make_index(tmp_path / "b", name="index_b", body=PART_SCRIPT + "# b\n")

    mgr = manager(service, indexes.LocalIndex("index_b", b))
    mgr.add("rig", "iso4762", "*")
    assert cache.verify("iso4762", "1.0.0")["status"] == "ok"

    doc = read_index(a)
    doc["packages"]["iso4762"]["versions"]["1.0.0"]["yanked"] = True
    write_index(a, doc)
    shutil.rmtree(b / "iso4762")          # B unfetchable -> the cache answers

    both = manager(service, indexes.LocalIndex("index_a", a),
                   indexes.LocalIndex("index_b", b))
    resolution = both.resolve("iso4762", "^1.0.0")
    assert resolution["offline"] is True
    assert resolution["index"] == "index_b"
    assert resolution["yanked"] is False


def test_a_yank_still_binds_the_index_the_cache_entry_came_from(
        tmp_path, cache_root, service):
    """The other direction, so the fix is not just permissiveness: the index
    that DID withdraw it still suppresses its own cache entry for a range."""
    a = make_index(tmp_path / "a", name="index_a")
    mgr = manager(service, indexes.LocalIndex("index_a", a))
    mgr.add("rig", "iso4762", "*")
    doc = read_index(a)
    doc["packages"]["iso4762"]["versions"]["1.0.0"]["yanked"] = True
    write_index(a, doc)
    shutil.rmtree(a / "iso4762")

    again = manager(service, indexes.LocalIndex("index_a", a))
    with pytest.raises(NotFoundError):
        again.resolve("iso4762", "^1.0.0")
    named = again.resolve("iso4762", "1.0.0")     # explicit still resolves
    assert named["yanked"] is True


def test_the_tree_must_agree_with_the_identity_it_is_filed_under(
        tmp_path, cache_root, service):
    """**Codex #6.** Resolution trusts the index's outer `name`/`version` keys
    and the content id proves the bytes; nothing compared them with the tree's
    OWN package.json. An index mapping `foo@1.0.0` at a verified tree that says
    `bar@2.0.0` installed fine and materialised bar's code under foo's
    provenance."""
    root = tmp_path / "liar"
    rel = "iso4762/1.0.0"
    package_tree(root / rel, name="iso4762")
    doc = json.loads((root / rel / "package.json").read_text())
    doc["name"], doc["version"] = "somethingelse", "9.9.9"
    (root / rel / "package.json").write_text(json.dumps(doc) + "\n")
    write_index(root, {"format": 1, "name": "liar", "scope": "public",
                       "embeddings": None,
                       "packages": {"iso4762": {"versions": {
                           "1.0.0": entry_for(root, rel)}}}})

    mgr = manager(service, indexes.LocalIndex("liar", root))
    with pytest.raises(ValidationError) as info:
        mgr.add("rig", "iso4762", "*")
    assert "says it is 'somethingelse'@'9.9.9'" in info.value.message
    assert cache.verify("iso4762", "1.0.0")["status"] == "missing"


def test_a_receipt_that_cannot_reconstruct_a_lock_entry_is_not_ok(
        tmp_path, cache_root, service):
    """**Codex #4.** `read_receipt` accepted any object, so a receipt carrying
    only `content_id` verified — and the offline path then wrote
    `index: null, source: null` into both git-tracked maps and reported
    success. An offline install that cannot reproduce the online lock entry is
    the one thing the offline path may not be."""
    root = make_index(tmp_path / "catalog")
    mgr = manager(service, indexes.LocalIndex("agentcad-core", root))
    mgr.add("rig", "iso4762", "*")
    receipt = cache.receipt_path("iso4762", "1.0.0")
    receipt.write_text(json.dumps(
        {"content_id": cache.read_receipt("iso4762", "1.0.0")["content_id"]}))

    report = cache.verify("iso4762", "1.0.0")
    assert report["status"] == "tampered"
    assert report["reason"] in ("receipt_schema", "receipt_incomplete")

    shutil.rmtree(root / "iso4762")       # force the offline path
    offline = manager(service, indexes.LocalIndex("agentcad-core", root))
    with pytest.raises(NotFoundError):
        offline.resolve("iso4762", "*")

    # And `add` HEALS it: the tree still hashes to what the index declares.
    root2 = make_index(tmp_path / "catalog2", name="agentcad-core")
    healed = manager(service, indexes.LocalIndex("agentcad-core", root2))
    healed.add("rig", "iso4762", "*")
    assert cache.verify("iso4762", "1.0.0")["status"] == "ok"
    assert cache.read_receipt("iso4762", "1.0.0")["schema"] == cache.RECEIPT_SCHEMA


def test_two_concurrent_adds_both_land(tmp_path, cache_root, service,
                                       monkeypatch):
    """**Codex #10a.** `add` read the manifest, edited two maps and saved it
    back, unserialized: two concurrent adds each read the PRE state and the
    second save dropped the first package, with both callers told they had
    succeeded.

    Two things are asserted, because either alone passes for the wrong reason:

    * the two threads really did run together (a start barrier that must not
      time out) — otherwise this is a sequential test wearing threads;
    * `manifest_scope` was never occupied by both at once, and both packages
      landed. Occupancy is the property; "no package was lost" alone can hold
      by luck on a fast machine.

    Note the shape this test *cannot* have: forcing an interleave **inside**
    `add` (both threads reading before either writes) is now impossible by
    construction — that is exactly what the lock prevents — so an earlier
    version of this test, which gated on a barrier inside `store.manifest`,
    could only ever time out. The barrier is on entry instead.
    """
    import threading

    from agentcad.core.packages import manager as manager_module

    alpha = make_index(tmp_path / "ia", name="core",
                       packages=(("alpha", "1.0.0"),))
    beta = make_index(tmp_path / "ib", name="core2",
                      packages=(("beta", "1.0.0"),))

    real_scope = manager_module.manifest_scope
    occupancy = {"now": 0, "max": 0}
    counter_lock = threading.Lock()

    @contextlib.contextmanager
    def counting_scope(store, proj):
        with real_scope(store, proj):
            with counter_lock:
                occupancy["now"] += 1
                occupancy["max"] = max(occupancy["max"], occupancy["now"])
            try:
                # Widen the critical section so an unserialized implementation
                # would overlap here with near-certainty.
                time.sleep(0.05)
                yield
            finally:
                with counter_lock:
                    occupancy["now"] -= 1

    monkeypatch.setattr(manager_module, "manifest_scope", counting_scope)

    started = threading.Barrier(2)
    broke: list[str] = []
    errors: list[Exception] = []

    def go(index_root, index_name, name):
        mgr = manager(service, indexes.LocalIndex(index_name, index_root))
        try:
            started.wait(timeout=5)
        except threading.BrokenBarrierError:
            broke.append(name)
        try:
            mgr.add("rig", name, "*")
        except Exception as exc:      # noqa: BLE001 — recorded, not raised
            errors.append(exc)

    threads = [threading.Thread(target=go, args=(alpha, "core", "alpha")),
               threading.Thread(target=go, args=(beta, "core2", "beta"))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert broke == [], f"the threads never ran together: {broke}"
    assert errors == [], [str(e) for e in errors]
    assert occupancy["max"] == 1, (
        f"{occupancy['max']} adds held the manifest at once — the "
        f"read-modify-write is not serialized")
    manifest = service.store.manifest("rig")
    assert sorted(manifest["packages"]) == ["alpha", "beta"]
    assert sorted(manifest["packages_lock"]) == ["alpha", "beta"]


def test_one_package_may_not_declare_unbounded_versions(tmp_path):
    """The third resource axis (Codex #12's tail): neither the byte ceiling nor
    the package count bounds ONE package's version list, and `format.resolve`
    parses every version of a package on every search — 182 ms at 100 000
    versions, measured."""
    from agentcad.core.packages import _json

    root = tmp_path / "many"
    root.mkdir()
    entry = {"content_id": "sha256:" + "0" * 64, "path": "p/1.0.0",
             "summary": "s", "license": "MIT", "disclosure": "agent",
             "parts": {}, "presets": [], "previews": [], "yanked": False,
             "signatures": [],
             "gate": {"status": "green", "exempt_skips": [],
                      "agentcad": "0.1.0", "build123d": "0.11.1",
                      "report_id": "sha256:" + "a" * 64}}
    versions = {f"1.0.{i}": entry
                for i in range(_json.MAX_VERSIONS_PER_PACKAGE + 1)}
    (root / "index.json").write_text(json.dumps(
        {"format": 1, "name": "many", "scope": "public", "embeddings": None,
         "packages": {"p": {"versions": versions}}}, separators=(",", ":")))
    with pytest.raises(ValidationError) as info:
        indexes.LocalIndex("many", root).entries()
    assert "declares" in info.value.message and "versions" in info.value.message
    assert info.value.details["package"] == "p"


def test_concurrent_writers_never_leave_a_corrupt_document(tmp_path):
    """The sharper half of the concurrent-add finding, hammered at its source.

    `ProjectStore._atomic_write` staged every write through a FIXED
    `<name>.tmp`, so two concurrent writers opened the **same** staging file,
    interleaved their bytes, and each `os.replace`d the mixture into place —
    `json.loads` then failed with "Extra data". `os.replace` was atomic all
    along; the file it replaced *from* was shared.

    Two writers with **distinct large documents** and a reader spinning
    alongside: every successful read must parse and must be one writer's whole
    document, never a blend. Asserted against behaviour rather than against
    `inspect.getsource`, so a future rewrite that reintroduces a shared staging
    name fails here instead of passing a substring check.
    """
    import threading

    from agentcad.core.project import ProjectStore

    target = tmp_path / "concurrent" / "project.json"
    first = json.dumps({"who": "A", "pad": "a" * 60_000}).encode()
    second = json.dumps({"who": "B", "pad": "b" * 90_000}).encode()
    corrupt: list[str] = []
    write_errors: list[str] = []
    stop = threading.Event()

    def write(payload):
        for _ in range(50):
            try:
                ProjectStore._atomic_write(target, payload)
            except Exception as exc:      # noqa: BLE001 — recorded, not raised
                write_errors.append(repr(exc))

    def read():
        while not stop.is_set():
            try:
                raw = target.read_bytes()
            except FileNotFoundError:
                continue
            if not raw:
                continue
            try:
                doc = json.loads(raw)
            except Exception as exc:      # noqa: BLE001 — this IS the failure
                corrupt.append(f"{type(exc).__name__}: {exc}"[:80])
                continue
            if doc.get("who") not in ("A", "B"):
                corrupt.append(f"blended document: who={doc.get('who')!r}")

    writers = [threading.Thread(target=write, args=(first,)),
               threading.Thread(target=write, args=(second,))]
    reader = threading.Thread(target=read, daemon=True)
    for thread in writers:
        thread.start()
    reader.start()
    for thread in writers:
        thread.join()
    stop.set()
    reader.join(timeout=2)

    assert corrupt == [], corrupt[:5]
    assert write_errors == [], write_errors[:5]
    assert json.loads(target.read_bytes())["who"] in ("A", "B")
    assert sorted(target.parent.glob("*.tmp")) == [], "staging files survived"


def test_a_failed_atomic_write_leaves_no_staging_file(tmp_path):
    """The failure path the random name made possible to clean up: the staging
    file is this writer's alone, so removing it cannot destroy another's."""
    from agentcad.core.project import ProjectStore

    target = tmp_path / "fail" / "project.json"
    with pytest.raises(TypeError):
        ProjectStore._atomic_write(target, None)
    assert sorted(p.name for p in target.parent.iterdir()) == []


# ============ the hybrid through a REAL merge (Codex #13, changelog 0183)


def _merge_rig(tmp_path, kernel):
    """A service with history, branches and merges live — `make_test_service`
    nulls `bus.on_publish`, and without those snapshots there is no branch to
    merge."""
    from agentcad import config as user_config
    from agentcad.core import locks
    from agentcad.core.branches import pinned_tree_var
    from agentcad.core.service import AgentCADService, EventBus
    from agentcad.core.tools import build_registry

    root = make_index(tmp_path / "idx", name="idxa",
                      packages=(("iso4762", "1.0.0"), ("iso4762", "2.0.0")))
    user_config.save_config({"indexes": [
        {"name": "idxa", "kind": "local", "path": str(root)}]})
    locks.set_client_id("local")
    pinned_tree_var.set(None)
    service = AgentCADService(tmp_path / "projects", kernel, EventBus())
    return service, build_registry(service), locks


@pytest.mark.integration
def test_a_real_merge_blocks_on_the_package_hybrid(tmp_path, kernel,
                                                   cache_root, monkeypatch):
    """**Codex #13, end to end.** One branch widens the *requirement* and the
    other keeps the *lock* — each map merges cleanly on its own, and the pair
    is a dependency no branch authored. It has to reach a human through the
    surface PRD-001 already uses for structural merge damage, so this drives
    `merge_branch` and reads `validation.integrity` rather than grepping the
    orchestrator's source."""
    if not _git.available():
        pytest.skip("git is not on PATH")
    service, registry, locks = _merge_rig(tmp_path, kernel)

    registry.call("create_project", {"name": "demo"})
    registry.call("create_part", {"project": "demo", "part_id": "box",
                                  "script": BOX_SCRIPT})
    added = registry.call("add_package", {"project": "demo", "name": "iso4762",
                                          "version_req": "~1.0.0"})
    assert "error" not in added, added
    assert service.store.manifest("demo")["packages_lock"]["iso4762"]["version"] \
        == "1.0.0"
    service.branches.create("demo", "feat")

    # feat widens the requirement only — the half a cross-resolution keeps.
    locks.set_client_id("agent_a")
    service.branches.switch("demo", "feat")
    manifest = service.store.manifest("demo")
    manifest["packages"]["iso4762"]["version_req"] = "^2.0.0"
    service.store.save_manifest("demo", manifest)
    service.bus.publish({"type": "project_changed", "project": "demo"})

    # master keeps the 1.0.0 lock, and edits a part so the merge is real work.
    locks.set_client_id("agent_b")
    service.branches.switch("demo", "master")
    registry.call("update_part_script", {
        "project": "demo", "part_id": "box",
        "script": BOX_SCRIPT.replace("p.size, p.size, p.size",
                                     "p.size, p.size, p.size * 2")})
    out = registry.call("merge_branch", {"project": "demo", "source": "feat"})

    assert "error" in out, out
    assert out["error"]["type"] == "validation_error"
    integrity = (out["error"]["details"] or {})["validation"]["integrity"]
    row = next(r for r in integrity
               if r["kind"] == "package_requirement_violated")
    assert row["package"] == "iso4762"
    assert row["version"] == "1.0.0" and row["version_req"] == "^2.0.0"
    assert "nobody authored" in row["message"]


@pytest.mark.integration
def test_an_ordinary_merge_of_a_project_with_packages_is_clean(
        tmp_path, kernel, cache_root):
    """The false-positive guard that matters most: a package present and
    consistent on both sides must merge with `integrity: []`. A check that
    reddens correct merges would be worse than the hybrid it catches."""
    if not _git.available():
        pytest.skip("git is not on PATH")
    service, registry, locks = _merge_rig(tmp_path, kernel)

    registry.call("create_project", {"name": "demo"})
    registry.call("create_part", {"project": "demo", "part_id": "box",
                                  "script": BOX_SCRIPT})
    registry.call("add_package", {"project": "demo", "name": "iso4762",
                                  "version_req": "~1.0.0"})
    service.branches.create("demo", "feat")
    locks.set_client_id("agent_a")
    service.branches.switch("demo", "feat")
    registry.call("update_part_script", {
        "project": "demo", "part_id": "box",
        "script": BOX_SCRIPT.replace("p.size, p.size, p.size",
                                     "p.size, p.size, p.size * 2")})
    locks.set_client_id("agent_b")
    service.branches.switch("demo", "master")
    out = registry.call("merge_branch", {"project": "demo", "source": "feat"})

    assert "error" not in out, out
    assert (out.get("validation") or {}).get("integrity") == []


@pytest.mark.integration
def test_a_remove_versus_a_bump_conflicts_at_both_maps_with_no_extra_row(
        tmp_path, kernel, cache_root):
    """One branch removes the package, the other bumps it: a legitimate,
    already-detected conflict at **both** maps. The new check must not add a
    third opinion on top of it — the merge is refused before a hybrid could
    exist."""
    if not _git.available():
        pytest.skip("git is not on PATH")
    service, registry, locks = _merge_rig(tmp_path, kernel)

    registry.call("create_project", {"name": "d2"})
    registry.call("create_part", {"project": "d2", "part_id": "box",
                                  "script": BOX_SCRIPT})
    registry.call("add_package", {"project": "d2", "name": "iso4762",
                                  "version_req": "~1.0.0"})
    service.branches.create("d2", "feat")
    locks.set_client_id("agent_a")
    service.branches.switch("d2", "feat")
    registry.call("remove_package", {"project": "d2", "name": "iso4762"})
    locks.set_client_id("agent_b")
    service.branches.switch("d2", "master")
    registry.call("add_package", {"project": "d2", "name": "iso4762",
                                  "version_req": "^2.0.0"})
    out = registry.call("merge_branch", {"project": "d2", "source": "feat"})

    assert out["error"]["type"] == "merge_conflict"
    keys = {c["key"] for c in (out["error"]["details"] or {})["conflicts"]}
    assert {"packages.iso4762", "packages_lock.iso4762"} <= keys
    # Refused as a conflict, so there is no integrity report to pollute.
    assert (out["error"]["details"] or {}).get("validation") is None
