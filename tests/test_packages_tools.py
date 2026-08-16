"""PRD-011 slice 7 — the consumer surface: provenance, search and the tool pack.

This is the slice that makes the feature exist for an agent, and three claims
carry it. Each is tested against its negation.

* **The provenance header is deterministic and immutable, and its status is
  computed on every read.** Two materialisations of the same package produce
  byte-identical files (AC3), a header is read with `tokenize` so a docstring
  quoting the marker is not one, and `ok / modified / version_drift / removed
  / unverified` are derived from the manifest, the script bytes and the cache
  — never stored.
* **`use_part` never touches an index.** It reads the lock, reads the cache,
  verifies **every time**, and copies. That is the whole of AC4 from the
  consumer's side, and the test deletes the index directory to prove it.
* **A project with no packages is byte-identical to a pre-feature one**
  (FR15), and provenance costs **zero kernel calls** on both the `get_part`
  and the `get_project` path.

`remove_package` gets its own attention: it must not touch a single script
byte, because the header is inside the script and the script text is the
rebuild cache key.
"""

import json
import shutil

import pytest

from agentcad.core.model import NotFoundError
from agentcad.core.packages import cache, gate, provenance, search
from agentcad.core.tools import build_registry
from .conftest import make_test_service
from .test_packages_index import make_index, read_index, write_index


def _fixture_root():
    from pathlib import Path
    return Path(__file__).resolve().parent / "fixtures" / "packages"


WIDGET = "widget_good"

PLATE_SCRIPT = '''\
"""A plate with a tapped hole — the anchor AC1 mates a package part onto."""

from build123d import *

PARAMS = {"thick": {"default": 10.0, "min": 5.0, "max": 20.0, "unit": "mm",
                    "description": "plate thickness"}}


def build(p):
    with BuildPart() as plate:
        Box(60, 60, p.thick, align=(Align.CENTER, Align.CENTER, Align.MIN))
        Cylinder(radius=2.5, height=p.thick * 3,
                 align=(Align.CENTER, Align.CENTER, Align.CENTER),
                 mode=Mode.SUBTRACT)
    return plate.part


def connectors(p, part):
    return {"tap": {"type": "rigid", "location": ((0, 0, p.thick), (0, 0, 0))}}
'''


# --------------------------------------------------------------- fixtures


@pytest.fixture
def cache_root(tmp_path, monkeypatch):
    root = tmp_path / "cache"
    monkeypatch.setenv("AGENTCAD_PACKAGES_DIR", str(root))
    monkeypatch.setenv("AGENTCAD_CONFIG", str(tmp_path / "cfg" / "config.json"))
    return root


@pytest.fixture
def index_dir(tmp_path):
    """A published index holding the green gate fixture, copied in."""
    root = tmp_path / "catalog"
    (root / WIDGET).mkdir(parents=True)
    shutil.copytree(_fixture_root() / WIDGET, root / WIDGET / "1.0.0")
    doc = {"format": 1, "name": "agentcad-core", "scope": "public",
           "packages": {WIDGET: {"versions": {}}}, "embeddings": None}
    doc["packages"][WIDGET]["versions"]["1.0.0"] = _entry(root, WIDGET, "1.0.0")
    write_index(root, doc)
    return root


def _entry(index_root, name, version, **overrides):
    from agentcad.core.packages import content
    rel = f"{name}/{version}"
    entry = {
        "content_id": content.content_id(index_root / rel),
        "path": rel,
        "summary": "A bored, chamfered mounting block",
        "keywords": ["fixture", "block", "mount"],
        "standards": ["ISO 9999"],
        "license": "Apache-2.0",
        "disclosure": "agent",
        "parts": {"mount_block": {
            "params": [{"name": "length", "type": "number", "min": 24.0,
                        "max": 80.0, "unit": "mm"},
                       {"name": "bore_d", "type": "number", "min": 3.0,
                        "max": 16.0, "unit": "mm"}],
            "connectors": {"seat": "rigid", "bore": "cylindrical"},
            "specs": ["valid", "not_hollowed_out"]}},
        "presets": ["mount_block.short", "mount_block.wide_16"],
        "previews": ["previews/mount_block_iso.png"],
        "gate": {"status": "green", "exempt_skips": [], "agentcad": "0.1.0",
                 "build123d": "0.11.1", "report_id": "sha256:" + "ab" * 32},
        "yanked": False,
        "signatures": [],
    }
    entry.update(overrides)
    return entry


@pytest.fixture
def rig(tmp_path, kernel, cache_root, index_dir, monkeypatch):
    """A service with the full registry, one project and one local index."""
    from agentcad import config as user_config
    user_config.save_config({"indexes": [
        {"name": "agentcad-core", "kind": "local", "path": str(index_dir)}]})
    service = make_test_service(tmp_path / "projects", kernel)
    service.create_project("rig")
    registry = build_registry(service)
    return service, registry


def _add(rig, name=WIDGET, **kw):
    service, registry = rig
    return registry.call("add_package", {"project": "rig", "name": name, **kw})


def _use(rig, part_id="block", **kw):
    service, registry = rig
    args = {"project": "rig", "package": WIDGET, "part": "mount_block",
            "part_id": part_id}
    args.update(kw)
    return registry.call("use_part", args)


def _script(service, part_id="block", proj="rig"):
    return service.store.read_script(proj, part_id)


# ============================================================ provenance.py


def test_the_header_is_byte_identical_across_two_calls():
    """No timestamp, no client id, no absolute path — AC3's mechanism."""
    entry = {"name": "iso4762", "version": "1.2.0", "part": "cap_screw",
             "preset": "m5x16", "index": "agentcad-core",
             "content_id": "sha256:" + "9f" * 32,
             "script_sha256": "sha256:" + "41" * 32}
    first = provenance.header(entry)
    second = provenance.header(dict(reversed(list(entry.items()))))
    assert first == second
    for machine_fact in ("20", "/Users", "/home", "T0", "Z\n"):
        assert machine_fact not in first.replace("sha256:", "")


def test_the_header_carries_the_security_non_claim():
    """Decision 11, place 7: the copy that lands in the consumer's repo."""
    text = provenance.header({"name": "a", "version": "1.0.0", "part": "p",
                              "preset": None, "index": "i",
                              "content_id": "sha256:" + "0" * 64,
                              "script_sha256": "sha256:" + "0" * 64})
    assert "not a security boundary" in text
    assert "docs/packages.md" in text


def test_parse_reads_every_field_back():
    entry = {"name": "iso4762", "version": "1.2.0", "part": "cap_screw",
             "preset": "m5x16", "index": "agentcad-core",
             "content_id": "sha256:" + "9f" * 32,
             "script_sha256": "sha256:" + "41" * 32}
    head = provenance.parse(provenance.header(entry) + "PARAMS = {}\n")
    for key, value in entry.items():
        assert head[key] == value
    assert head["format"] == provenance.HEADER_FORMAT


def test_a_docstring_quoting_the_marker_is_not_a_header():
    """`tokenize` COMMENT tokens, the `script_blocks`/`sketch_emit` precedent."""
    script = ('"""Docs that mention # agentcad:package 1 {"name": "x"}."""\n'
              "PARAMS = {}\n")
    assert provenance.parse(script) is None


def test_parse_answers_none_for_a_script_with_no_header():
    assert provenance.parse("PARAMS = {}\n\n\ndef build(p):\n    return None\n") is None


def test_a_marker_with_an_unreadable_payload_is_reported_not_swallowed():
    script = "# agentcad:package 1 {not json\nPARAMS = {}\n"
    head = provenance.parse(script)
    assert head is not None and head["malformed"]
    assert head["name"] is None


def test_strip_removes_exactly_the_block_the_header_added():
    body = "PARAMS = {}\n\n\ndef build(p):\n    return None\n"
    entry = {"name": "a", "version": "1.0.0", "part": "p", "preset": None,
             "index": "i", "content_id": "sha256:" + "0" * 64,
             "script_sha256": provenance.script_sha256(body)}
    assert provenance.strip(provenance.header(entry) + body) == body
    assert provenance.strip(body) == body


def test_status_is_ok_when_the_lock_the_bytes_and_the_cache_all_agree(
        rig, cache_root):
    service, _registry = rig
    _add(rig)
    _use(rig)
    manifest = service.store.manifest("rig")
    head = provenance.parse(_script(service))
    assert provenance.status(head, manifest, _script(service)) == "ok"


def test_status_reads_modified_after_a_local_edit(rig):
    """Legitimate, reported, never repaired."""
    service, _registry = rig
    _add(rig)
    _use(rig)
    script = _script(service)
    service.store.write_script("rig", "block", script + "\n# my change\n")
    manifest = service.store.manifest("rig")
    head = provenance.parse(_script(service))
    assert provenance.status(head, manifest, _script(service)) == "modified"


def test_status_reads_version_drift_when_the_lock_moved(rig):
    service, _registry = rig
    _add(rig)
    _use(rig)
    manifest = service.store.manifest("rig")
    manifest["packages_lock"][WIDGET]["version"] = "9.9.9"
    head = provenance.parse(_script(service))
    assert provenance.status(head, manifest, _script(service)) == "version_drift"


def test_status_reads_removed_when_the_dependency_is_gone(rig):
    service, _registry = rig
    _add(rig)
    _use(rig)
    manifest = service.store.manifest("rig")
    manifest.pop("packages_lock")
    head = provenance.parse(_script(service))
    assert provenance.status(head, manifest, _script(service)) == "removed"


def test_status_reads_unverified_when_the_cache_cannot_answer(rig, cache_root):
    """'We did not look' — never 'fine'. A fresh clone with a cold cache is
    exactly this case, and calling it `ok` would be a claim nobody measured."""
    service, _registry = rig
    _add(rig)
    _use(rig)
    manifest = service.store.manifest("rig")
    head = provenance.parse(_script(service))
    shutil.rmtree(cache_root)
    assert provenance.status(head, manifest, _script(service)) == "unverified"


def test_a_header_from_a_newer_format_is_unverified_not_ok(rig):
    """Provenance-header drift: a header this build cannot interpret must not
    be read as agreement. Guessing that the fields still mean what they mean
    today is how a format bump becomes a silent false `ok`."""
    service, _registry = rig
    _add(rig)
    _use(rig)
    manifest = service.store.manifest("rig")
    script = _script(service)
    head = provenance.parse(script)
    assert provenance.status(head, manifest, script) == "ok"
    assert provenance.status({**head, "format": 2}, manifest, script) == \
        "unverified"


def test_removed_wins_over_modified(rig):
    """Both are true; the dependency being gone is the one FR6 reports."""
    service, _registry = rig
    _add(rig)
    _use(rig)
    service.store.write_script("rig", "block", _script(service) + "# edit\n")
    manifest = service.store.manifest("rig")
    manifest.pop("packages_lock")
    head = provenance.parse(_script(service))
    assert provenance.status(head, manifest, _script(service)) == "removed"


def test_status_makes_zero_kernel_calls(rig, monkeypatch):
    """PRD-008's rule: resolution never touches the kernel."""
    service, _registry = rig
    _add(rig)
    _use(rig)
    calls = []
    monkeypatch.setattr(service.kernel, "request",
                        lambda *a, **k: calls.append(a) or {})
    manifest = service.store.manifest("rig")
    provenance.status(provenance.parse(_script(service)), manifest,
                      _script(service))
    provenance.scan(service.store, "rig")
    assert calls == []


def test_scan_lists_every_materialised_part_with_its_status(rig):
    service, _registry = rig
    _add(rig)
    _use(rig, part_id="block")
    _use(rig, part_id="block2")
    service.create_part("rig", "plain", script=PLATE_SCRIPT)
    found = provenance.scan(service.store, "rig")
    assert {row["part"] for row in found} == {"block", "block2"}
    assert {row["status"] for row in found} == {"ok"}
    assert all(row["package"] == WIDGET for row in found)


# ================================================================ search.py


def _hits(result):
    return [(hit["name"], hit["index"]) for hit in result["hits"]]


def test_search_is_honest_about_not_being_semantic(index_dir):
    from agentcad.core.packages import indexes
    result = search.search([indexes.LocalIndex("agentcad-core", index_dir)])
    assert result["semantic"] is False
    assert result["semantic_reason"] == "no_embedding_provider"


def test_search_ranks_exact_name_above_prefix_above_keyword(tmp_path):
    from agentcad.core.packages import indexes
    root = make_index(tmp_path / "cat",
                      packages=(("block", "1.0.0"), ("blockade", "1.0.0"),
                                ("widget", "1.0.0")))
    doc = read_index(root)
    doc["packages"]["widget"]["versions"]["1.0.0"]["keywords"] = ["block"]
    write_index(root, doc)
    index = indexes.LocalIndex("agentcad-core", root)
    names = [hit["name"] for hit in search.search([index], query="block")["hits"]]
    assert names == ["block", "blockade", "widget"]


def test_every_hit_explains_itself(index_dir):
    from agentcad.core.packages import indexes
    index = indexes.LocalIndex("agentcad-core", index_dir)
    hit = search.search([index], query="mount")["hits"][0]
    assert hit["why"], "a search an agent cannot explain is one it cannot correct"
    assert any(why.startswith("keyword:") or why.startswith("text:")
               for why in hit["why"])


def test_search_filters_structurally_on_keywords_and_standards(index_dir):
    from agentcad.core.packages import indexes
    index = indexes.LocalIndex("agentcad-core", index_dir)
    assert _hits(search.search([index], keywords=["block"]))
    assert not _hits(search.search([index], keywords=["nothing-like-this"]))
    assert _hits(search.search([index], standards=["ISO 9999"]))
    assert not _hits(search.search([index], standards=["ISO 0000"]))


def test_a_param_filter_matches_on_range_overlap(index_dir):
    from agentcad.core.packages import indexes
    index = indexes.LocalIndex("agentcad-core", index_dir)
    overlapping = {"name": "length", "min": 10.0, "max": 30.0}
    assert _hits(search.search([index], param=overlapping))
    disjoint = {"name": "length", "min": 100.0, "max": 200.0}
    assert not _hits(search.search([index], param=disjoint))
    assert not _hits(search.search([index], param={"name": "nope"}))


def test_search_skips_a_package_whose_only_versions_are_yanked(index_dir):
    from agentcad.core.packages import indexes
    doc = read_index(index_dir)
    doc["packages"][WIDGET]["versions"]["1.0.0"]["yanked"] = True
    write_index(index_dir, doc)
    index = indexes.LocalIndex("agentcad-core", index_dir)
    assert search.search([index], query=WIDGET)["hits"] == []


def test_a_broken_index_is_a_warning_not_an_exception(tmp_path, index_dir):
    from agentcad.core.packages import indexes
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "index.json").write_text("{not json")
    result = search.search([indexes.LocalIndex("broken", broken),
                            indexes.LocalIndex("agentcad-core", index_dir)])
    assert _hits(result) == [(WIDGET, "agentcad-core")]
    assert any("broken" in warning for warning in result["warnings"])


def test_search_honours_the_limit_deterministically(index_dir):
    from agentcad.core.packages import indexes
    index = indexes.LocalIndex("agentcad-core", index_dir)
    assert len(search.search([index], limit=0)["hits"]) == 0
    assert search.search([index], limit=1)["hits"] == \
        search.search([index], limit=1)["hits"]


# ========================================================== the tool pack


def test_the_pack_registers_no_gate_provider(rig):
    """`tools_proposals.py:51` assigns `gate_providers = []` unconditionally
    and `pac` sorts before `pro`, so a provider appended here would be
    silently discarded. The pack does not have one — deliberately."""
    service, _registry = rig
    providers = getattr(service, "gate_providers", [])
    assert not any(getattr(p, "__name__", None) == "packages" for p in providers)


def test_the_manager_captures_nothing_at_registration():
    """`service.specs`/`branches`/`proposals` do not exist at `pac`."""
    from agentcad.core.packages.manager import PackageManager

    class Bare:
        def __getattr__(self, name):
            raise AssertionError(f"captured {name!r} at construction")

    PackageManager(Bare(), indexes=[])


def test_the_six_tools_are_registered(rig):
    _service, registry = rig
    names = {tool.name for tool in registry.list()}
    assert {"search_packages", "add_package", "remove_package",
            "list_packages", "use_part", "validate_package"} <= names


def test_the_install_and_materialise_descriptions_carry_the_non_claim(rig):
    """Decision 11, places 2, 3 and 4."""
    _service, registry = rig
    for name in ("add_package", "use_part", "validate_package"):
        assert "not a security boundary" in registry.get(name).description


# ------------------------------------------------------------- use_part


def test_use_part_materialises_from_the_cache_with_the_index_deleted(
        rig, index_dir):
    """AC4 from the consumer's side: `use_part` never touches an index."""
    service, _registry = rig
    _add(rig)
    shutil.rmtree(index_dir)
    detail = _use(rig)
    assert detail["id"] == "block"
    assert detail["status"]["state"] == "ok"
    assert detail["package_provenance"]["package"] == WIDGET


def test_materialising_twice_produces_byte_identical_scripts(rig, index_dir):
    """AC3: no timestamp, no client id, no absolute path in the header."""
    service, _registry = rig
    _add(rig)
    _use(rig, part_id="one")
    shutil.rmtree(index_dir)
    _use(rig, part_id="two")
    assert _script(service, "one") == _script(service, "two")


def test_use_part_verifies_the_cache_every_time_and_refuses_a_tamper(
        rig, cache_root):
    """AC3's tamper half: the cached script is edited after the install."""
    service, _registry = rig
    _add(rig)
    target = cache.version_dir(WIDGET, "1.0.0") / "parts" / "mount_block.py"
    target.write_text(target.read_text() + "\n# tampered\n")
    result = _use(rig)
    assert result["error"]["type"] == "validation_error"
    assert "does not match the content id" in result["error"]["message"]


def test_use_part_refuses_a_package_that_is_declared_but_not_locked(rig):
    """A hand-edited manifest. Guessing a version invents a dependency."""
    service, _registry = rig
    _add(rig)
    manifest = service.store.manifest("rig")
    manifest.pop("packages_lock")
    service.store.save_manifest("rig", manifest)
    result = _use(rig)
    assert result["error"]["type"] == "validation_error"
    assert "add_package" in result["error"]["message"]


def test_use_part_refuses_a_package_that_is_not_in_the_project(rig):
    result = _use(rig)
    assert result["error"]["type"] == "notfound_error"


def test_use_part_refuses_an_existing_part_id(rig):
    service, _registry = rig
    _add(rig)
    _use(rig, part_id="block")
    result = _use(rig, part_id="block")
    assert result["error"]["type"] == "conflict_error"
    assert "block" in result["error"]["message"]


def test_use_part_refuses_an_unknown_part_or_preset(rig):
    _add(rig)
    assert _use(rig, part="nope")["error"]["type"] == "notfound_error"
    assert _use(rig, preset="nope")["error"]["type"] == "notfound_error"


def test_use_part_applies_the_presets_parameters(rig):
    service, _registry = rig
    _add(rig)
    detail = _use(rig, preset="short")
    assert detail["params"]["length"] == 24.0
    assert detail["params"]["bore_d"] == 5.0
    assert detail["package_provenance"]["preset"] == "short"


def test_explicit_params_override_the_preset(rig):
    _add(rig)
    detail = _use(rig, preset="short", params={"length": 30.0})
    assert detail["params"]["length"] == 30.0
    assert detail["params"]["bore_d"] == 5.0


def test_a_parameter_the_part_will_not_accept_leaves_no_half_made_part(rig):
    """Source nobody typed: a refused override must not leave a part behind
    that the caller did not ask for and cannot be expected to unpick."""
    service, _registry = rig
    _add(rig)
    result = _use(rig, params={"not_a_parameter": 1.0})
    assert result["error"]["type"] == "validation_error"
    with pytest.raises(NotFoundError):
        service.store.get_part("rig", "block")


# --------------------------------------------------- get_part / get_project


def test_get_part_names_the_package_at_its_version(rig):
    """AC6."""
    service, registry = rig
    _add(rig)
    _use(rig)
    detail = registry.call("get_part", {"project": "rig", "part_id": "block"})
    prov = detail["package_provenance"]
    assert (prov["package"], prov["version"], prov["status"]) == \
        (WIDGET, "1.0.0", "ok")


def test_after_remove_package_the_part_still_builds_and_reads_removed(rig):
    """AC6 / FR6: removal is a WARNING, not breakage."""
    service, registry = rig
    _add(rig)
    _use(rig)
    before = _script(service)
    removed = registry.call("remove_package", {"project": "rig", "name": WIDGET})
    assert removed["materialized_parts"] == ["block"]
    assert _script(service) == before, "removal rewrote a script byte"
    detail = registry.call("get_part", {"project": "rig", "part_id": "block"})
    assert detail["status"]["state"] == "ok"
    assert detail["package_provenance"]["status"] == "removed"


def test_get_part_reads_modified_after_a_local_edit(rig):
    service, registry = rig
    _add(rig)
    _use(rig)
    service.store.write_script("rig", "block", _script(service) + "\n# mine\n")
    detail = registry.call("get_part", {"project": "rig", "part_id": "block"})
    assert detail["package_provenance"]["status"] == "modified"


def test_a_part_with_no_header_carries_no_provenance(rig):
    service, registry = rig
    service.create_part("rig", "plain", script=PLATE_SCRIPT)
    detail = registry.call("get_part", {"project": "rig", "part_id": "plain"})
    assert detail["package_provenance"] is None


def test_get_project_summarises_the_packages(rig):
    service, registry = rig
    _add(rig)
    _use(rig)
    payload = registry.call("get_project", {"project": "rig"})
    assert payload["packages"] == {WIDGET: {"version": "1.0.0",
                                            "provenance_ok": True}}


def test_a_project_with_no_packages_is_byte_identical_to_a_pre_feature_one(rig):
    """FR15: the feature is structurally invisible to a project that does not
    use it — no key, no kernel call, no manifest change."""
    service, registry = rig
    service.create_part("rig", "plain", script=PLATE_SCRIPT)
    before = json.dumps(service.store.manifest("rig"), sort_keys=True)
    calls = []
    real = service.kernel.request
    service.kernel.request = lambda *a, **k: (calls.append(a[0]), real(*a, **k))[1]
    try:
        payload = registry.call("get_project", {"project": "rig"})
        detail = registry.call("get_part", {"project": "rig", "part_id": "plain"})
    finally:
        service.kernel.request = real
    assert payload["packages"] == {}
    assert detail["package_provenance"] is None
    assert json.dumps(service.store.manifest("rig"), sort_keys=True) == before
    # the only kernel work is the part's own (cached) build, exactly as before
    assert all(name != "connectors" for name in calls)


def test_the_wrappers_are_idempotent(rig):
    """`build_registry` may run twice over one service."""
    service, _registry = rig
    _add(rig)
    _use(rig)
    build_registry(service)
    build_registry(service)
    detail = service.get_part("rig", "block")
    assert isinstance(detail["package_provenance"], dict)
    assert list(detail).count("package_provenance") == 1


# ------------------------------------------------------ the other tools


def test_list_packages_reports_the_cache_state_per_package(rig, cache_root):
    _service, registry = rig
    _add(rig)
    listed = registry.call("list_packages", {"project": "rig"})
    assert listed["packages"][WIDGET]["cache"] == "ok"
    assert listed["packages"][WIDGET]["version"] == "1.0.0"
    assert [i["name"] for i in listed["indexes"]] == ["agentcad-core"]
    shutil.rmtree(cache.version_dir(WIDGET, "1.0.0"))
    listed = registry.call("list_packages", {"project": "rig"})
    assert listed["packages"][WIDGET]["cache"] == "missing"


def test_list_packages_without_a_project_lists_the_indexes(rig):
    _service, registry = rig
    listed = registry.call("list_packages", {})
    assert listed["project"] is None
    assert [i["name"] for i in listed["indexes"]] == ["agentcad-core"]


def test_search_packages_answers_through_the_registry(rig):
    _service, registry = rig
    result = registry.call("search_packages", {"query": WIDGET})
    assert [hit["name"] for hit in result["hits"]] == [WIDGET]
    assert result["semantic"] is False


def test_validate_package_delegates_to_the_gate(rig, tmp_path):
    _service, registry = rig
    report = registry.call("validate_package", {
        "path": str(_fixture_root() / WIDGET),
        "stages": ["format", "docs"],
        "work_dir": str(tmp_path / "gatework")})
    assert gate.validate_gate_report(report) == []
    assert {stage["name"] for stage in report["stages"]} == set(gate.GATE_STAGES)
    assert report["note"] == gate.SECURITY_NOTE


def test_validate_package_writes_nothing_into_the_project(rig, tmp_path):
    service, registry = rig
    before = sorted(p.name for p in (service.store.root).iterdir())
    registry.call("validate_package", {
        "path": str(_fixture_root() / WIDGET), "stages": ["format"],
        "work_dir": str(tmp_path / "gatework2")})
    assert sorted(p.name for p in (service.store.root).iterdir()) == before


# ==================================================================== AC1


@pytest.mark.integration
def test_ac1_a_package_part_mates_onto_a_tapped_hole(rig, tmp_path):
    """`add_package` → `use_part` → `set_mate`, with the resolved transform
    and a clean interference check asserted — the walk PRD-011 opens with."""
    service, registry = rig
    _add(rig)
    _use(rig, part_id="screw", preset="short")
    service.create_part("rig", "plate", script=PLATE_SCRIPT)
    service.set_assembly("rig", [
        {"id": "plate1", "part": "plate", "position": [0, 0, 0],
         "rotation_deg": [0, 0, 0]},
        {"id": "screw1", "part": "screw", "position": [0, 0, 0],
         "rotation_deg": [0, 0, 0]},
    ])
    assembly = registry.call("set_mate", {
        "project": "rig", "instance": "screw1", "connector": "seat",
        "to_instance": "plate1", "to_connector": "tap"})
    placed = {i["id"]: i for i in assembly["instances"]}["screw1"]
    assert placed["position"] == pytest.approx([0.0, 0.0, 10.0], abs=1e-6)
    registry.call("set_project_specs", {
        "project": "rig",
        "script": ("from agentcad.toolkit.specs import check_interference_free\n"
                   "SPECS = [check_interference_free()]\n")})
    report = registry.call("run_specs", {"project": "rig"})
    interference = [row for row in report["checks"]
                    if row["name"] == "no_interference"]
    assert interference and interference[0]["status"] == "pass", interference
