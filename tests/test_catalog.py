"""PRD-011 slice 10 — the seeded catalog: real content behind the registry.

`catalog/` at the repo root is three things at once, and each one is a claim
this module tests rather than asserts:

* **The default local index.** `cli._register_catalog` declares it and
  `PackageManager` appends it *after* whatever the user configured, so a fresh
  install searches and installs with **no network and no config file** while a
  user's own `agentcad-core` still wins.
* **The same bytes a git index serves.** A package is a directory and its id
  is a canonical tree digest, so cloning this repository and pointing a
  `GitIndex` at `catalog/` must produce the identical entries and the
  identical content ids. That is the dogfood test, and it is what makes
  "publish this repo and it is an index" a measurement.
* **Content the gate refereed.** Every package here is green through the real
  `PackageGate` with no non-exempt skips. *The gate is the curation*: a
  package that will not pass does not go in the catalog, and the failure is
  the work item — never a loosened check.

`index.json` is a **build product** of `agentcad publish`. The only part of it
anybody typed is the four-line empty document that names the index and its
scope; every version entry is derived from the gate's own measurements, and
`test_every_published_entry_matches_the_tree_on_disk` is what catches a
hand-edit.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from agentcad.core.packages import content, indexes
from agentcad.core.packages.gate import PackageGate
from agentcad.core.packages.manager import PackageManager
from agentcad.core.tools import build_registry
from .conftest import make_test_service

pytestmark = pytest.mark.slow

REPO = Path(__file__).resolve().parents[1]
CATALOG = REPO / "catalog"

#: The one skip every catalog package is allowed to carry: there is no policy
#: module in this build (design Decision 11 ships the seam and no policy).
ALLOWED_EXEMPT_SKIPS = {"policy:no_policy_configured"}


def catalog_document() -> dict:
    """The bundled index document, or an empty one when there is no catalog.

    Tolerant on purpose: the catalog is *data* and deleting it must degrade
    the product rather than break a code path — including this module's
    collection, which reads it to parametrise. A missing catalog then fails
    `test_the_catalog_carries_the_cots_starter_set` loudly instead of
    erroring out of `--collect-only` with a traceback.
    """
    path = CATALOG / "index.json"
    if not path.is_file():
        return {"format": 1, "name": "agentcad-core", "scope": "public",
                "packages": {}, "embeddings": None}
    return json.loads(path.read_text(encoding="utf-8"))


def published() -> list[tuple[str, str]]:
    """Every `(name, version)` the bundled index publishes."""
    doc = catalog_document()
    return sorted((name, version)
                  for name, record in (doc.get("packages") or {}).items()
                  for version in (record.get("versions") or {}))


PUBLISHED = published()


# --------------------------------------------------------------- fixtures


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    """A cache, an index checkout root and a config file in `tmp_path`, so no
    test here can read or write the developer's real `~/.agentcad`."""
    monkeypatch.setenv("AGENTCAD_PACKAGES_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("AGENTCAD_INDEXES_DIR", str(tmp_path / "indexes"))
    monkeypatch.setenv("AGENTCAD_CONFIG", str(tmp_path / "cfg" / "config.json"))
    return tmp_path


@pytest.fixture
def service(tmp_path, kernel, isolated_config):
    return make_test_service(tmp_path / "projects", kernel)


@pytest.fixture
def registry(service):
    return build_registry(service)


def bundled(service):
    """What `cli._register_catalog` does, without starting a CLI."""
    from agentcad.cli import bundled_index_entries

    service.bundled_indexes = bundled_index_entries()
    return service


# ================================================== the index is a document


def test_the_bundled_index_validates():
    problems = indexes.format.validate_index(catalog_document())
    assert problems == [], problems


def test_the_catalog_publishes_at_least_the_cap_screw_package():
    assert ("iso4762", "1.0.0") in PUBLISHED


def test_the_catalog_carries_the_cots_starter_set():
    """FR12, named entry by named entry: ISO 4762/4014/7380 fasteners and
    threaded inserts, DIN 625 bearings, 2020/3030 extrusions, NEMA 17/23
    motor outlines. Each with connectors, specs on its interface dimensions
    and docs — which the gate test above is what proves."""
    published = {name for name, _version in PUBLISHED}
    assert published >= {
        "iso4762", "iso4014", "iso7380", "thread_insert",
        "din625", "extrusion_2020", "extrusion_3030", "nema17", "nema23",
    }


@pytest.mark.parametrize("name,version", PUBLISHED, ids=lambda v: str(v))
def test_every_catalog_part_declares_connectors_and_specs(name, version):
    """A COTS package with no connectors is a solid nobody can place, and one
    with no specs is a shape nobody measured. FR12 asks for both on every
    entry, so the index digest — which is derived from the gate's own
    measurements — has to show both."""
    entry = catalog_document()["packages"][name]["versions"][version]
    assert entry["parts"], name
    for part_id, digest in entry["parts"].items():
        assert digest["connectors"], f"{name}.{part_id} declares no connectors"
        assert digest["specs"], f"{name}.{part_id} declares no specs"
        assert digest["params"], f"{name}.{part_id} declares no parameters"


@pytest.mark.parametrize("name,version", PUBLISHED, ids=lambda v: str(v))
def test_every_published_entry_matches_the_tree_on_disk(name, version):
    """A hand-edited package (or a hand-edited entry) is caught here.

    The content id is a canonical tree digest, so this compares the published
    claim against the bytes in the repository — the single check that keeps
    `index.json` honest as a build product.
    """
    entry = catalog_document()["packages"][name]["versions"][version]
    tree = CATALOG / entry["path"]
    assert tree.is_dir(), f"{entry['path']} is published but not in the tree"
    assert content.content_id(tree) == entry["content_id"]


def test_editing_one_byte_of_a_catalog_package_changes_its_content_id(tmp_path):
    """The negation of the test above: if a tree edit did not move the id, the
    match would prove nothing."""
    name, version = PUBLISHED[0]
    entry = catalog_document()["packages"][name]["versions"][version]
    copy = tmp_path / "copy"
    shutil.copytree(CATALOG / entry["path"], copy)
    assert content.content_id(copy) == entry["content_id"]
    readme = copy / "docs" / "README.md"
    readme.write_text(readme.read_text(encoding="utf-8") + " ", encoding="utf-8")
    assert content.content_id(copy) != entry["content_id"]


@pytest.mark.parametrize("name,version", PUBLISHED, ids=lambda v: str(v))
def test_every_catalog_package_is_apache_2_0(name, version):
    """The founder decision, pinned: the seed catalog ships one licence, and
    the format requires a non-empty one on every package."""
    entry = catalog_document()["packages"][name]["versions"][version]
    doc = json.loads((CATALOG / entry["path"] / "package.json")
                     .read_text(encoding="utf-8"))
    assert doc["license"] == "Apache-2.0"
    assert entry["license"] == "Apache-2.0"


@pytest.mark.parametrize("name,version", PUBLISHED, ids=lambda v: str(v))
def test_every_published_entry_records_what_the_gate_measured(name, version):
    entry = catalog_document()["packages"][name]["versions"][version]
    assert entry["gate"]["status"] == "green"
    assert entry["gate"]["build123d"], "the real compatibility key is missing"
    assert set(entry["gate"]["exempt_skips"]) <= ALLOWED_EXEMPT_SKIPS
    assert entry["yanked"] is False


# ============================================== the gate is the curation


@pytest.mark.parametrize("name,version", PUBLISHED, ids=lambda v: str(v))
def test_every_catalog_package_passes_the_gate(name, version, service,
                                               registry):
    """The dogfood run that stops the seed catalog from rotting.

    Green with no non-exempt skips, over **every** stage — the same nine
    `agentcad publish` runs, so this is the publish verdict and not a subset
    of it.
    """
    entry = catalog_document()["packages"][name]["versions"][version]
    report = PackageGate(service).run(CATALOG / entry["path"])
    assert report["status"] == "green", [
        item for stage in report["stages"] for item in stage["items"]
        if item["status"] in ("fail", "error")]
    assert report["publishable"] is True, report["blockers"]
    assert set(report["exempt_skips"]) <= ALLOWED_EXEMPT_SKIPS
    assert report["summary"]["failed"] == 0
    assert report["summary"]["errors"] == 0
    assert report["package"]["content_id"] == entry["content_id"]


def test_a_wrong_iso_table_row_reddens_the_head_diameter_spec(tmp_path, service,
                                                              registry):
    """The negation of the `iso4762` SPECS claim.

    The specs measure the built solid against the published ISO 4762 table,
    so moving one row of that table must turn the check red. Without this
    test "the head diameter matches the standard" would be a sentence the
    suite never contradicts.
    """
    source = tmp_path / "iso4762"
    shutil.copytree(CATALOG / "iso4762" / "1.0.0", source)
    script = source / "parts" / "cap_screw.py"
    text = script.read_text(encoding="utf-8")
    assert '"dk": 8.72' in text
    script.write_text(text.replace('"dk": 8.72', '"dk": 9.72'), encoding="utf-8")

    report = PackageGate(service).run(
        source, stages=("format", "contract", "build", "specs"))
    failures = [item["id"] for stage in report["stages"]
                for item in stage["items"] if item["status"] == "fail"]
    assert "specs:cap_screw@default:head_diameter_iso4762" in failures
    # The head *height* row is measured against an untouched column, so it
    # stays green: one wrong number reddens one check, not the stage wholesale.
    assert "specs:cap_screw@default:head_height_iso4762" not in failures


def test_a_screw_whose_size_cannot_be_established_fails_rather_than_passes(
        tmp_path, service, registry):
    """`_row` answers `None` for a shape that is not a bd_warehouse fastener,
    and every predicate must read that as a failure — "we could not measure"
    is not "it is fine"."""
    source = tmp_path / "iso4762"
    shutil.copytree(CATALOG / "iso4762" / "1.0.0", source)
    script = source / "parts" / "cap_screw.py"
    text = script.read_text(encoding="utf-8")
    patched = text.replace(
        "    return threads.cap_screw(p.size, p.length, simple=p.thread == \"cosmetic\")",
        "    return Box(10, 10, 10)")
    assert patched != text
    script.write_text(patched, encoding="utf-8")

    report = PackageGate(service).run(
        source, stages=("format", "contract", "build", "specs"))
    failures = [item["id"] for stage in report["stages"]
                for item in stage["items"] if item["status"] == "fail"]
    assert "specs:cap_screw@default:head_diameter_iso4762" in failures
    assert "specs:cap_screw@default:length_under_head" in failures


def test_a_bearing_that_ignores_its_designation_reddens_its_specs(
        tmp_path, service, registry):
    """The negation of the `din625` SPECS claim, and the check that stops them
    being tautologies.

    The first version of these predicates picked the table row by matching the
    *built* `D` and `B`, so the `D` and `B` checks compared the geometry with
    the row it had just been used to select and the bore check compared the
    table with itself: a build wired to produce a 608 whatever its parameter
    said published **77 of 77 spec rows green**. The row now comes from the
    `designation` parameter, which is what makes this sabotage red.
    """
    source = tmp_path / "din625"
    shutil.copytree(CATALOG / "din625" / "1.0.0", source)
    script = source / "parts" / "ball_bearing.py"
    text = script.read_text(encoding="utf-8")
    sabotage = "def _row(p):\n    return DIN625[p.designation]\n"
    assert sabotage in text
    script.write_text(
        text.replace(sabotage, 'def _row(p):\n    return DIN625["608"]\n'),
        encoding="utf-8")

    report = PackageGate(service).run(
        source, stages=("format", "contract", "build", "specs"))
    failures = {item["id"] for stage in report["stages"]
                for item in stage["items"] if item["status"] == "fail"}
    for check in ("bore_din625", "outside_diameter_din625", "width_din625",
                  "ring_faces_din625"):
        assert f"specs:ball_bearing@designation=623:{check}" in failures
    # ...and the one designation the sabotaged build really does produce stays
    # green: this reddens a wrong bearing, not every bearing.
    assert not [f for f in failures if "designation=608" in f], sorted(failures)


@pytest.mark.parametrize("package,square", [("extrusion_2020", "section_20x20"),
                                            ("extrusion_3030", "section_30x30")])
def test_a_severed_extrusion_profile_reddens_the_connectivity_spec(
        package, square, tmp_path, service, registry):
    """The negation of `one_connected_solid`.

    Cutting each T-channel as the plain rectangle the profile's own constants
    describe severs every diagonal web — `slot_inner / 2` is larger than
    `size / 2 - slot_depth` — and leaves five loose pieces that still measure
    20 x 20 (30 x 30), still have their centre bore and still have four slot
    openings. That shipped green. It must not again.
    """
    source = tmp_path / package
    shutil.copytree(CATALOG / package / "1.0.0", source)
    script = source / "parts" / "extrusion.py"
    text = script.read_text(encoding="utf-8")
    script.write_text(text + '''

def _channel_void():
    """SABOTAGE: the rectangular T-channel that severs the webs."""
    half = SIZE / 2.0
    return [(half, SLOT_OPEN / 2.0), (half - LIP_T, SLOT_OPEN / 2.0),
            (half - LIP_T, SLOT_INNER / 2.0),
            (half - SLOT_DEPTH, SLOT_INNER / 2.0),
            (half - SLOT_DEPTH, -SLOT_INNER / 2.0),
            (half - LIP_T, -SLOT_INNER / 2.0),
            (half - LIP_T, -SLOT_OPEN / 2.0), (half, -SLOT_OPEN / 2.0)]
''', encoding="utf-8")

    report = PackageGate(service).run(
        source, stages=("format", "contract", "build", "specs"))
    failures = {item["id"] for stage in report["stages"]
                for item in stage["items"] if item["status"] == "fail"}
    assert "specs:extrusion@default:one_connected_solid" in failures
    # The three checks that were green on the five-piece profile still are:
    # the envelope, the bore and the length say nothing about connectivity.
    for blind in (square, "centre_bore", "length_and_origin"):
        assert f"specs:extrusion@default:{blind}" in {
            item["id"] for stage in report["stages"]
            for item in stage["items"] if item["status"] == "pass"}


def test_the_hex_bolt_shank_spec_measures_the_shank(tmp_path, service,
                                                    registry):
    """`shank_full_length_root` is the spec that pins what `iso4014` really
    builds: a shank threaded end to end, drawn at the thread's basic minor
    diameter, because the pinned bd_warehouse can build nothing else.

    Told to expect the *nominal* diameter instead, it has to redden — a check
    that passed either way would be pinning nothing.
    """
    source = tmp_path / "iso4014"
    shutil.copytree(CATALOG / "iso4014" / "1.0.0", source)
    script = source / "parts" / "hex_bolt.py"
    text = script.read_text(encoding="utf-8")
    sabotage = "        return float(diameter) - 1.0825 * float(pitch)"
    assert sabotage in text
    script.write_text(text.replace(sabotage, "        return float(diameter)"),
                      encoding="utf-8")

    report = PackageGate(service).run(
        source, stages=("format", "contract", "build", "specs"))
    failures = {item["id"] for stage in report["stages"]
                for item in stage["items"] if item["status"] == "fail"}
    assert "specs:hex_bolt@default:shank_full_length_root" in failures
    assert "specs:hex_bolt@default:across_flats_iso4014" not in failures


# ============================== registration: no config file, no network


def test_the_bundled_catalog_is_registered_on_a_fresh_service(service):
    """No config file exists in this test's home at all."""
    from agentcad import config as user_config

    assert not user_config.config_path().exists()
    manager = PackageManager(bundled(service))
    assert [index.name for index in manager.indexes] == ["agentcad-core"]
    assert manager.indexes[0].path == CATALOG
    assert manager.warnings == []


def test_a_service_without_the_bundled_declaration_sees_no_indexes(service):
    """The registration is a declaration on the service, so nothing that does
    not opt in — every other test service, `checks.py`'s ephemeral one — has
    its index list changed by this slice."""
    assert PackageManager(service).indexes == []


def test_a_user_index_of_the_same_name_wins_over_the_bundled_one(
        service, tmp_path):
    """Appended, never prepended."""
    from agentcad import config as user_config

    mine = tmp_path / "mine"
    mine.mkdir()
    (mine / "index.json").write_text(json.dumps(
        {"format": 1, "name": "agentcad-core", "scope": "private",
         "packages": {}, "embeddings": None}), encoding="utf-8")
    user_config.save_config({"indexes": [
        {"name": "agentcad-core", "kind": "local", "path": str(mine)}]})
    manager = PackageManager(bundled(service))
    assert [index.name for index in manager.indexes] == ["agentcad-core"]
    assert manager.indexes[0].path == mine


def test_a_user_index_under_another_name_keeps_precedence_over_the_catalog(
        service, tmp_path):
    from agentcad import config as user_config

    mine = tmp_path / "mine"
    mine.mkdir()
    (mine / "index.json").write_text(json.dumps(
        {"format": 1, "name": "acme", "scope": "private", "packages": {},
         "embeddings": None}), encoding="utf-8")
    user_config.save_config({"indexes": [
        {"name": "acme", "kind": "local", "path": str(mine)}]})
    manager = PackageManager(bundled(service))
    assert [index.name for index in manager.indexes] == ["acme", "agentcad-core"]


def test_search_finds_the_cap_screw_and_says_why(service, registry):
    bundled(service)
    result = registry.call("search_packages", {"query": "cap screw"})
    hits = {hit["name"]: hit for hit in result["hits"]}
    assert "iso4762" in hits, result
    hit = hits["iso4762"]
    assert hit["index"] == "agentcad-core"
    assert hit["version"] == "1.0.0"
    assert hit["why"], "a search an agent cannot explain is one it cannot correct"
    assert "ISO 4762" in hit["standards"]
    assert result["semantic"] is False
    assert result["semantic_reason"] == "no_embedding_provider"


def test_a_standards_search_finds_the_package_by_its_standard(service, registry):
    bundled(service)
    result = registry.call("search_packages", {"standards": ["ISO 4762"]})
    assert [hit["name"] for hit in result["hits"]] == ["iso4762"]


def test_a_missing_catalog_is_a_warning_and_not_a_failure(monkeypatch, tmp_path,
                                                          capsys):
    """The catalog is data: deleting it degrades the product to "no packages
    configured" and breaks no code path (`_register_examples`' contract)."""
    from agentcad import cli

    empty = tmp_path / "no_resources"
    empty.mkdir()
    monkeypatch.setattr(cli, "resource_root", lambda: empty)
    assert cli.bundled_index_entries() == []

    class Bare:
        pass

    svc = Bare()
    cli._register_catalog(svc)
    assert not hasattr(svc, "bundled_indexes")

    (empty / "catalog").mkdir()
    cli._register_catalog(svc)
    assert not hasattr(svc, "bundled_indexes")
    assert "has no index.json" in capsys.readouterr().err


def test_the_frozen_bundle_ships_the_catalog():
    """`catalog/` joins `examples/` in the PyInstaller data files, or a frozen
    app has an index that is not there."""
    spec = (REPO / "packaging" / "pyinstaller" / "agentcad.spec").read_text(
        encoding="utf-8")
    assert '"catalog"' in spec and 'REPO_ROOT / "catalog"' in spec


# ================================ the dogfood: a repo IS this index


_IDENTITY = ("-c", "user.email=catalog@agentcad.test", "-c",
             "user.name=Catalog", "-c", "commit.gpgsign=false",
             "-c", "init.defaultBranch=main")


@pytest.mark.portability
@pytest.mark.skipif(shutil.which("git") is None, reason="git is not on PATH")
def test_the_catalog_serves_byte_identically_through_a_git_index(
        tmp_path, isolated_config):
    """A git index is a local index plus a fetch — so cloning a repository
    that carries `catalog/` must serve the identical document and the
    identical trees.

    The "remote" is a scratch bare repository holding a copy of `catalog/`;
    nothing is committed to this project's own history.
    """
    work = tmp_path / "remote_work"
    shutil.copytree(CATALOG, work)
    subprocess.run(["git", *_IDENTITY, "init"], cwd=work, check=True,
                   capture_output=True)
    subprocess.run(["git", *_IDENTITY, "add", "-A"], cwd=work, check=True,
                   capture_output=True)
    subprocess.run(["git", *_IDENTITY, "commit", "-m", "seed catalog"],
                   cwd=work, check=True, capture_output=True)
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "clone", "--bare", str(work), str(bare)],
                   check=True, capture_output=True)

    remote = indexes.GitIndex("agentcad-core", bare.as_uri(), ref="main")
    remote.refresh()
    local = indexes.LocalIndex("agentcad-core", CATALOG)
    assert remote.entries() == local.entries()
    for name, version in PUBLISHED:
        served = remote.fetch(name, version)
        entry = local.entry(name, version)
        assert content.content_id(served) == entry["content_id"]
        assert content.content_id(served) == content.content_id(
            local.fetch(name, version))


# ==================================================================== AC1


PLATE_SCRIPT = '''\
"""Bolt-down plate with a PRD-010 tapped M5 hole — AC1's anchor.

The tapped hole's seat is the top face at the hole centre, so a cap screw
mated onto it lands where a real fastener would.

Note the two size vocabularies this line straddles: PRD-010's hole standards
name the thread `"M5"` while bd_warehouse (and therefore the `iso4762`
package's `size` enum) names it `"M5-0.8"`. They are the same thread.
"""

from build123d import *

from agentcad.toolkit import holes

PARAMS = {"thick": {"default": 10.0, "min": 5.0, "max": 20.0, "unit": "mm",
                    "description": "Plate thickness"}}


def build(p):
    with BuildPart() as plate:
        Box(60, 60, p.thick, align=(Align.CENTER, Align.CENTER, Align.MIN))
    tapped, _records, _warnings = holes.tapped(plate.part, [(0, 0)], "M5",
                                               depth=p.thick)
    return tapped


def connectors(p, part):
    return {"bolt_seat": {"type": "rigid",
                          "location": ((0, 0, p.thick), (0, 0, 0))}}
'''


@pytest.mark.integration
def test_ac1_a_catalog_cap_screw_mates_onto_a_tapped_hole(tmp_path, kernel,
                                                          isolated_config):
    """AC1, on the real catalog package and a **copy** of the bundled
    prototyping example: `add_package` → `use_part` with the `m5x16` preset →
    `set_mate` onto a PRD-010 tapped hole → the resolved transform and a clean
    interference check.
    """
    projects = tmp_path / "projects"
    projects.mkdir()
    shutil.copytree(REPO / "examples" / "prototyping", projects / "prototyping")
    service = make_test_service(projects, kernel)
    bundled(service)
    registry = build_registry(service)
    service.store.open(projects / "prototyping")

    added = registry.call("add_package", {"project": "prototyping",
                                          "name": "iso4762"})
    assert added["lock"]["version"] == "1.0.0"
    assert added["lock"]["index"] == "agentcad-core"

    used = registry.call("use_part", {
        "project": "prototyping", "package": "iso4762", "part": "cap_screw",
        "part_id": "cap_screw_m5x16", "preset": "m5x16"})
    assert used["package_provenance"]["status"] == "ok"
    assert used["package_provenance"]["package"] == "iso4762"
    assert used["params"]["size"] == "M5-0.8"
    assert used["params"]["length"] == 16.0

    service.create_part("prototyping", "bolt_plate", script=PLATE_SCRIPT)
    service.set_assembly("prototyping", [
        {"id": "plate_1", "part": "bolt_plate", "position": [0, 0, 0],
         "rotation_deg": [0, 0, 0]},
        {"id": "screw_1", "part": "cap_screw_m5x16", "position": [0, 0, 0],
         "rotation_deg": [0, 0, 0]},
    ])
    assembly = registry.call("set_mate", {
        "project": "prototyping", "instance": "screw_1",
        "connector": "head_seat", "to_instance": "plate_1",
        "to_connector": "bolt_seat"})
    placed = {i["id"]: i for i in assembly["instances"]}["screw_1"]
    assert placed["position"] == pytest.approx([0.0, 0.0, 10.0], abs=1e-6)

    # Interference-free, and the reason is measured rather than lucky: a
    # cosmetic-thread cap screw's shank is drawn at the thread **root**
    # diameter (⌀4.134 for M5, not the nominal ⌀5.000), and `holes.tapped`
    # bores the **tap drill** (⌀4.2). The screw drops into the hole it is for.
    interference = registry.call("check_interference", {"project": "prototyping"})
    pairs = {tuple(sorted((p["a"], p["b"]))) for p in interference["pairs"]}
    assert ("plate_1", "screw_1") not in pairs, interference["pairs"]

    # The negation, because "no interference" is only evidence if the check
    # can report some: switch the same screw to a **real** thread and its
    # flanks reach the nominal ⌀5.000, which is what thread engagement in a
    # ⌀4.2 tapped hole means. Same mate, same position, a different number.
    service.set_params("prototyping", "cap_screw_m5x16", {"thread": "real"})
    engaged = registry.call("check_interference", {"project": "prototyping"})
    overlaps = {tuple(sorted((p["a"], p["b"]))): p for p in engaged["pairs"]}
    assert ("plate_1", "screw_1") in overlaps, engaged["pairs"]
    assert overlaps[("plate_1", "screw_1")]["volume_mm3"] > 1.0


# ==================================== the COTS set has to work TOGETHER


BRACKET_SCRIPT = '''\
"""A bearing bracket: a plate that bolts to a T-slot face and carries a
bearing on its outer side. The interoperability anchor between two catalog
packages that were authored separately."""

from build123d import *

PARAMS = {"thick": {"default": 5.0, "min": 3.0, "max": 10.0, "unit": "mm",
                    "description": "Plate thickness"}}


def build(p):
    return Box(40, 30, p.thick, align=(Align.CENTER, Align.CENTER, Align.MIN))


def connectors(p, part):
    return {
        "mount": {"type": "rigid", "location": ((0, 0, 0), (0, 0, 0))},
        "bearing_seat": {"type": "rigid",
                         "location": ((0, 0, p.thick), (0, 0, 0))},
    }
'''


@pytest.mark.integration
def test_a_bearing_mounts_on_an_extrusion_through_a_bracket(tmp_path, kernel,
                                                            isolated_config):
    """FR12's real claim: the COTS set is not nine packages, it is a kit.

    A `din625` 608 bearing → a bracket → an `extrusion_2020` bar, three
    instances and two mates, resolving through connectors that two separately
    authored packages declared. Interference-clean, and the numbers are
    asserted rather than the absence: the bracket lands on the slot face and
    the bearing lands on the bracket.
    """
    projects = tmp_path / "projects"
    projects.mkdir()
    service = make_test_service(projects, kernel)
    bundled(service)
    registry = build_registry(service)
    service.create_project("rig")

    for name in ("din625", "extrusion_2020"):
        added = registry.call("add_package", {"project": "rig", "name": name})
        assert "error" not in added, added
    registry.call("use_part", {"project": "rig", "package": "extrusion_2020",
                               "part": "extrusion", "part_id": "bar",
                               "preset": "l100"})
    registry.call("use_part", {"project": "rig", "package": "din625",
                               "part": "ball_bearing", "part_id": "bearing",
                               "preset": "b608"})
    service.create_part("rig", "bracket", script=BRACKET_SCRIPT)

    service.set_assembly("rig", [
        {"id": "bar_1", "part": "bar", "position": [0, 0, 0],
         "rotation_deg": [0, 0, 0]},
        {"id": "bracket_1", "part": "bracket", "position": [0, 0, 0],
         "rotation_deg": [0, 0, 0]},
        {"id": "bearing_1", "part": "bearing", "position": [0, 0, 0],
         "rotation_deg": [0, 0, 0]},
    ])
    registry.call("set_mate", {
        "project": "rig", "instance": "bracket_1", "connector": "mount",
        "to_instance": "bar_1", "to_connector": "slot_x_pos"})
    assembly = registry.call("set_mate", {
        "project": "rig", "instance": "bearing_1", "connector": "face",
        "to_instance": "bracket_1", "to_connector": "bearing_seat"})
    assert "error" not in assembly, assembly

    placed = {i["id"]: i for i in assembly["instances"]}
    # The bracket's mount face lands on the bar's +X slot face (x = 10) at
    # mid-length (z = 50); the bearing lands 5 mm further out, on the plate.
    assert placed["bracket_1"]["position"] == pytest.approx([10.0, 0.0, 50.0],
                                                            abs=1e-6)
    assert placed["bearing_1"]["position"] == pytest.approx([15.0, 0.0, 50.0],
                                                            abs=1e-6)

    interference = registry.call("check_interference", {"project": "rig"})
    assert interference["pairs"] == [], interference["pairs"]


# ======================== the cosmetic-vs-real claim the README makes


@pytest.mark.integration
def test_cosmetic_and_real_threads_are_dimensionally_identical(service):
    """The README says switching the `thread` parameter "does not move a
    mate". That is a measurable claim: same bounding box, different volume."""
    service.create_project("threadcheck")
    script = (CATALOG / "iso4762" / "1.0.0" / "parts" / "cap_screw.py").read_text(
        encoding="utf-8")
    service.create_part("threadcheck", "screw", script=script)

    service.set_params("threadcheck", "screw", {"thread": "cosmetic"})
    cosmetic = service.get_part("threadcheck", "screw")["metrics"]
    service.set_params("threadcheck", "screw", {"thread": "real"})
    real = service.get_part("threadcheck", "screw")["metrics"]

    for side in ("min", "max"):
        assert real["bbox"][side] == pytest.approx(cosmetic["bbox"][side],
                                                   abs=1e-6)
    assert real["volume_mm3"] != pytest.approx(cosmetic["volume_mm3"], rel=1e-3)
