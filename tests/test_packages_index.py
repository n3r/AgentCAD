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

import json
import queue
import shutil

import pytest

from agentcad.core.model import NotFoundError, ValidationError
from agentcad.core.packages import cache, content, indexes, lockfile
from agentcad.core.packages.manager import PackageManager
from .conftest import make_test_service

PART_SCRIPT = "PARAMS = {}\n\n\ndef build(p):\n    return None\n"


# --------------------------------------------------------------- builders


def package_tree(root, name="iso4762", body=PART_SCRIPT):
    (root / "parts").mkdir(parents=True, exist_ok=True)
    (root / "parts" / "cap_screw.py").write_text(body)
    (root / "package.json").write_text(json.dumps({"name": name}) + "\n")
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
