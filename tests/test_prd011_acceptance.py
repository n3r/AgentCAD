"""PRD-011 acceptance — the parts library and package registry, AC1–AC9.

One test per criterion, each naming it in its docstring, each on a **copy** of
whatever it touches. The ACs are graded against the shipped catalog and the
shipped fixtures, never against a stub built for the occasion.

Two of them deserve a note before you read them:

* **AC7 is a browser session, and a test cannot be one.** What is gradeable in
  the suite is the *evidence*: the routes the dialog calls exist and are
  mounted, the frontend module exists and carries the visible non-claim, and
  the changelog entry records the session with its console output. The session
  itself is in changelogs 0177 and 0178 (driven twice — once on a one-package
  catalog, again on all nine).
* **AC9 is not in the PRD.** It was adopted at the design review: *an agent
  takes a deliberately broken package from a red gate report to green with no
  human intervention, driven only by the report's structured content.* It is
  the flagship loop this whole feature exists to shorten, so it is graded like
  a criterion rather than asserted in prose. Read
  `test_ac9_an_agent_takes_a_red_package_green_from_the_report_alone` and the
  honesty note above it before believing it.
"""

import json
import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest


def _rmtree_repo(path) -> None:
    """Delete a git directory on any OS (the `test_checks_ref` idiom): git
    marks `objects/` read-only and Windows refuses to unlink a read-only file
    (WinError 5). Clear the bit and retry."""
    def _retry(func, target, _exc):
        os.chmod(target, stat.S_IWRITE)
        func(target)

    shutil.rmtree(path, onexc=_retry)


from agentcad.core.model import ConflictError, NotFoundError, ValidationError
from agentcad.core.packages import (cache, content, format as pkgformat, gate,
                                    indexes)
from agentcad.core.tools import build_registry
from .conftest import make_test_service

pytestmark = pytest.mark.slow

REPO = Path(__file__).resolve().parents[1]
CATALOG = REPO / "catalog"
FIXTURES = REPO / "tests" / "fixtures" / "packages"
CHANGELOG = REPO / "docs" / "changelog"
FRONTEND = REPO / "frontend"
PRD_NAME = "PRD-011-parts-library-registry.md"

_IDENTITY = ("-c", "user.name=t", "-c", "user.email=t@e", "-c",
             "commit.gpgsign=false", "-c", "init.defaultBranch=main")


def _find_prd() -> Path:
    """Locate the PRD wherever it currently lives.

    A PRD moves from `in-progress/` to `completed/` at **merge**, not when the
    build finishes, so a test that hard-codes one directory is red for the
    whole review window. PRD-010's close-out hit exactly that (changelog
    0164), and this is its fix, copied deliberately.
    """
    prd_root = REPO / "docs" / "prd"
    for stage in ("in-progress", "completed", "pending"):
        candidate = prd_root / stage / PRD_NAME
        if candidate.is_file():
            return candidate
    found = sorted(prd_root.rglob(PRD_NAME))
    assert found, f"{PRD_NAME} is not anywhere under {prd_root}"
    return found[0]


PRD = _find_prd()


# --------------------------------------------------------------- fixtures


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    """A cache, an index checkout root and a config file under `tmp_path`, so
    nothing here can read or write the developer's real `~/.agentcad`."""
    monkeypatch.setenv("AGENTCAD_PACKAGES_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("AGENTCAD_INDEXES_DIR", str(tmp_path / "indexes"))
    monkeypatch.setenv("AGENTCAD_CONFIG", str(tmp_path / "cfg" / "config.json"))
    return tmp_path


@pytest.fixture
def service(tmp_path, kernel, isolated_config):
    svc = make_test_service(tmp_path / "projects", kernel)
    svc.bundled_indexes = _bundled()
    return svc


@pytest.fixture
def registry(service):
    return build_registry(service)


def _bundled():
    from agentcad.cli import bundled_index_entries

    return bundled_index_entries()


def _copy(src: Path, dest: Path) -> Path:
    shutil.copytree(src, dest)
    return dest


def _stage(report, name):
    return next(s for s in report["stages"] if s["name"] == name)


def _failures(report):
    return [row for stage in report["stages"]
            for row in (stage.get("items") or [])
            if row["status"] in ("fail", "error")]


PLATE_SCRIPT = '''\
"""A plate with a PRD-010 tapped M5 hole — AC1's anchor.

PRD-010's hole standards name the thread "M5"; bd_warehouse (and therefore the
`iso4762` package's `size` enum) names it "M5-0.8". Same thread, two
vocabularies.
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


# =================================================================== AC1


@pytest.mark.integration
def test_ac1_a_catalog_cap_screw_mates_into_the_prototyping_example(
        tmp_path, kernel, isolated_config):
    """**AC1** — `add_package` (iso4762) + `use_part` (the `m5x16` preset)
    mates a real cap screw into the prototyping example via its connector,
    end to end, on a **copy** of the bundled example.

    The interference check is asserted in both directions, because "no
    interference" is only evidence when the check can report some: a
    **cosmetic** screw's shank is drawn at the thread root (⌀4.134 for M5) and
    drops into a ⌀4.2 tap-drilled hole, while the **real** thread reaches the
    nominal ⌀5.000 and overlaps it — which is what engagement is.
    """
    projects = tmp_path / "projects"
    projects.mkdir()
    _copy(REPO / "examples" / "prototyping", projects / "prototyping")
    service = make_test_service(projects, kernel)
    service.bundled_indexes = _bundled()
    registry = build_registry(service)
    service.store.open(projects / "prototyping")

    added = registry.call("add_package", {"project": "prototyping",
                                          "name": "iso4762"})
    assert added["lock"]["version"] == "1.0.0"

    used = registry.call("use_part", {
        "project": "prototyping", "package": "iso4762", "part": "cap_screw",
        "part_id": "screw", "preset": "m5x16"})
    assert used["params"]["size"] == "M5-0.8"
    assert used["params"]["length"] == 16.0
    assert used["package_provenance"]["status"] == "ok"

    service.create_part("prototyping", "bolt_plate", script=PLATE_SCRIPT)
    service.set_assembly("prototyping", [
        {"id": "plate_1", "part": "bolt_plate", "position": [0, 0, 0],
         "rotation_deg": [0, 0, 0]},
        {"id": "screw_1", "part": "screw", "position": [0, 0, 0],
         "rotation_deg": [0, 0, 0]},
    ])
    assembly = registry.call("set_mate", {
        "project": "prototyping", "instance": "screw_1",
        "connector": "head_seat", "to_instance": "plate_1",
        "to_connector": "bolt_seat"})
    placed = {i["id"]: i for i in assembly["instances"]}["screw_1"]
    assert placed["position"] == pytest.approx([0.0, 0.0, 10.0], abs=1e-6)

    clear = registry.call("check_interference", {"project": "prototyping"})
    assert ("plate_1", "screw_1") not in {
        tuple(sorted((p["a"], p["b"]))) for p in clear["pairs"]}, clear["pairs"]

    service.set_params("prototyping", "screw", {"thread": "real"})
    engaged = registry.call("check_interference", {"project": "prototyping"})
    overlaps = {tuple(sorted((p["a"], p["b"]))): p for p in engaged["pairs"]}
    assert ("plate_1", "screw_1") in overlaps, engaged["pairs"]


# =================================================================== AC2


@pytest.mark.integration
def test_ac2a_a_variant_that_breaks_at_an_extreme_fails_validate_and_publish(
        service, tmp_path):
    """**AC2, first half** — a package whose build raises at a parameter
    extreme fails `agentcad package validate` **and** publish, with the
    failing check named in `details.checks`.

    The fixture builds at its default and at `length=min`; only `length=max`
    raises. A gate that built the default alone would call it green, which is
    the entire reason the extremes are swept.
    """
    source = _copy(FIXTURES / "break_at_extreme", tmp_path / "pkg")
    report = gate.PackageGate(service).run(source, work_dir=str(tmp_path / "w"))

    assert report["publishable"] is False
    ids = [row["id"] for row in _failures(report)]
    assert ids == ["build:strut@length=max"], ids
    row = _failures(report)[0]
    assert row["error"]["type"], "the kernel's payload does not travel"
    assert "Traceback" in json.dumps(row["error"])
    # The variants either side of it are green: the gate is not simply red.
    passed = {r["id"] for r in _stage(report, "build")["items"]
              if r["status"] == "pass"}
    assert {"build:strut@default", "build:strut@length=min"} <= passed

    index = _index(tmp_path / "idx")
    before = _tree_hash(index.path)
    with pytest.raises(ValidationError) as exc:
        indexes.publish(index, source, service, work_dir=str(tmp_path / "w2"))
    named = [item["id"] for item in exc.value.details["checks"]]
    assert "build:strut@length=max" in named
    assert _tree_hash(index.path) == before, "a red gate wrote into the index"


@pytest.mark.integration
def test_ac2b_a_broken_connector_fails_validate_and_publish(service, tmp_path):
    """**AC2, second half** — a package whose `connectors` returns a bad axis
    fails the `connectors` stage, naming the connector, and blocks publish.

    It builds at every extreme, so nothing but mating it could have caught it
    — this feature is the kernel `connectors` handler's first server-side
    consumer.
    """
    source = _copy(FIXTURES / "broken_connector", tmp_path / "pkg")
    report = gate.PackageGate(service).run(source, work_dir=str(tmp_path / "w"))

    assert report["publishable"] is False
    ids = [row["id"] for row in _failures(report)]
    assert ids == ["connectors:bracket"], ids
    assert "pivot" in json.dumps(_failures(report)[0])
    assert _stage(report, "build")["status"] == "green"

    index = _index(tmp_path / "idx")
    with pytest.raises(ValidationError) as exc:
        indexes.publish(index, source, service, work_dir=str(tmp_path / "w2"))
    assert "connectors:bracket" in [i["id"] for i in exc.value.details["checks"]]


# =================================================================== AC3


@pytest.mark.integration
def test_ac3_re_materialisation_is_byte_identical_and_a_tampered_cache_is_refused(
        service, registry, tmp_path):
    """**AC3** — with only the cache populated (the index unreachable),
    `use_part` re-materialises **byte-identically**; a tampered cache entry is
    detected by hash and refused.

    Both halves in one test because they are one claim: the bytes are a
    function of the cached content and of nothing else, which is exactly what
    makes a hash a verification rather than a receipt.
    """
    service.create_project("rig")
    registry.call("add_package", {"project": "rig", "name": "iso4762"})

    first = registry.call("use_part", {
        "project": "rig", "package": "iso4762", "part": "cap_screw",
        "part_id": "s1", "preset": "m5x16"})
    assert first["package_provenance"]["status"] == "ok"

    # Every index disappears. `use_part` reads the lock and the cache and
    # never touches an index, so this changes nothing at all.
    service.bundled_indexes = []
    service.packages.reload_indexes()
    assert service.packages.indexes == []
    second = registry.call("use_part", {
        "project": "rig", "package": "iso4762", "part": "cap_screw",
        "part_id": "s2", "preset": "m5x16"})
    assert service.store.read_script("rig", "s1") \
        == service.store.read_script("rig", "s2")
    assert second["package_provenance"]["status"] == "ok"

    # Tamper: one byte in the cached script. `materialize` is called directly
    # because `registry.call` turns an `AppError` into a payload, and what is
    # being graded is the refusal.
    script = cache.version_dir("iso4762", "1.0.0") / "parts" / "cap_screw.py"
    script.write_text(script.read_text() + "\n# tampered\n")
    assert cache.verify("iso4762", "1.0.0")["status"] == "tampered"
    with pytest.raises(ValidationError) as exc:
        _materialize(service, "rig", "s3")
    assert "parts/cap_screw.py" in str(exc.value)
    with pytest.raises(NotFoundError):
        service.store.get_part("rig", "s3")


def _materialize(service, proj, part_id):
    from agentcad.core.tools_packages import materialize

    return materialize(service, proj, "iso4762", "cap_screw", part_id,
                       preset="m5x16")


# =================================================================== AC4


@pytest.mark.portability
@pytest.mark.integration
def test_ac4_a_git_index_serves_search_and_install_and_survives_its_own_death(
        service, registry, tmp_path, isolated_config):
    """**AC4** — a git-hosted index added by URL serves search and install,
    and the install keeps working offline from the cache after the index
    disappears.

    The "remote" is a bare repository in `tmp_path` reached over `file://`, so
    the test is hermetic: no network, and nothing is skipped for lacking one.
    """
    from agentcad import config as user_config
    from agentcad.core.packages import _git

    if not _git.available():
        pytest.skip("git is not on PATH")

    work = _copy(CATALOG, tmp_path / "remote_work")
    subprocess.run(["git", *_IDENTITY, "init"], cwd=work, check=True,
                   capture_output=True)
    subprocess.run(["git", *_IDENTITY, "add", "-A"], cwd=work, check=True,
                   capture_output=True)
    subprocess.run(["git", *_IDENTITY, "commit", "-m", "catalog"], cwd=work,
                   check=True, capture_output=True)
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "clone", "--bare", str(work), str(bare)],
                   check=True, capture_output=True)

    user_config.save_config({"indexes": [
        {"name": "remote", "kind": "git", "url": bare.as_uri(), "ref": "main"}]})
    service.bundled_indexes = []          # the git index is the ONLY source
    service.packages.reload_indexes()
    service.create_project("rig")

    hits = registry.call("search_packages", {"query": "cap screw"})["hits"]
    assert "iso4762" in [hit["name"] for hit in hits]
    online = registry.call("add_package", {"project": "rig", "name": "iso4762"})
    assert online["offline"] is False

    # The remote is deleted outright. `use_part` reads the lock and the cache
    # and never touches an index, so it does not even notice.
    _rmtree_repo(bare)
    used = registry.call("use_part", {
        "project": "rig", "package": "iso4762", "part": "cap_screw",
        "part_id": "s1", "preset": "m5x16"})
    assert used["package_provenance"]["status"] == "ok"

    # With the remote gone the LAST GOOD CHECKOUT keeps answering — that is
    # `GitIndex.refresh`'s designed degradation, and it is why the next step
    # deletes the checkout too rather than claiming the cache was used here.
    service.create_project("rig2")
    service.packages.reload_indexes()
    still_served = registry.call("add_package", {"project": "rig2",
                                                 "name": "iso4762"})
    assert still_served["offline"] is False

    # Now nothing local is left of the index either: the *cache* is the only
    # thing that knows this package, and the lock entry it reconstructs from
    # the receipt is byte-identical to the online one. That equality is what
    # every field of a lock entry being content-determined buys.
    _rmtree_repo(tmp_path / "indexes")
    service.create_project("rig3")
    service.packages.reload_indexes()
    offline = registry.call("add_package", {"project": "rig3",
                                            "name": "iso4762"})
    assert offline["offline"] is True
    assert json.dumps(offline["lock"], indent=2, sort_keys=True) \
        == json.dumps(online["lock"], indent=2, sort_keys=True)


# =================================================================== AC5


@pytest.mark.integration
def test_ac5_a_version_is_immutable_and_a_yank_never_breaks_a_lockfile(
        service, registry, tmp_path):
    """**AC5** — republishing an existing version is a `conflict_error`; a
    yanked version still resolves from an existing lockfile.

    The republish is refused **even though the content id is identical**: a
    byte comparison would let a publisher redefine "identical" later.
    """
    from agentcad import config as user_config

    source = _copy(FIXTURES / "widget_good", tmp_path / "pkg")
    index = _index(tmp_path / "idx")
    indexes.publish(index, source, service, work_dir=str(tmp_path / "w"))

    before = _tree_hash(index.path)
    with pytest.raises(ConflictError) as exc:
        indexes.publish(index, source, service, work_dir=str(tmp_path / "w2"))
    assert "widget_good@1.0.0" in str(exc.value)
    assert _tree_hash(index.path) == before

    user_config.save_config({"indexes": [
        {"name": "vendor", "kind": "local", "path": str(index.path)}]})
    service.bundled_indexes = []
    service.packages.reload_indexes()
    service.create_project("rig")
    registry.call("add_package", {"project": "rig", "name": "widget_good"})

    index.yank("widget_good", "1.0.0")
    service.packages.reload_indexes()
    # The lock still names it, so the project keeps working…
    used = registry.call("use_part", {
        "project": "rig", "package": "widget_good", "part": "mount_block",
        "part_id": "b1"})
    assert used["package_provenance"]["status"] == "ok"
    # …while a FRESH range never selects it — including from the warm cache
    # this very test just filled, which is the half writing AC5 found missing
    # (changelog 0180): the cache is for "no index answered", not for "the
    # index answered no".
    service.create_project("rig2")
    result = registry.call("add_package", {"project": "rig2",
                                           "name": "widget_good",
                                           "version_req": "^1.0.0"})
    assert result["error"]["type"] == "notfound_error"
    assert "YANKED" in json.dumps(result["error"])

    # Named explicitly, it still installs: a yank says "do not start here",
    # never "you may never have this".
    named = registry.call("add_package", {"project": "rig2",
                                          "name": "widget_good",
                                          "version_req": "1.0.0"})
    assert named["lock"]["version"] == "1.0.0"


# =================================================================== AC6


@pytest.mark.integration
def test_ac6_provenance_names_the_package_and_degrades_to_a_warning(
        service, registry, tmp_path):
    """**AC6** — `get_part` of a materialised part names `package@version`;
    after `remove_package` the part **still builds** and its provenance warns.

    Three of the five statuses in one walk, because the point is that the
    status is *computed on every read* and never stored: nothing below writes
    a status anywhere.
    """
    service.create_project("rig")
    registry.call("add_package", {"project": "rig", "name": "iso4762"})
    registry.call("use_part", {
        "project": "rig", "package": "iso4762", "part": "cap_screw",
        "part_id": "s1", "preset": "m5x16"})

    detail = service.get_part("rig", "s1")
    head = detail["package_provenance"]
    assert (head["package"], head["version"]) == ("iso4762", "1.0.0")
    assert head["status"] == "ok"
    assert head["preset"] == "m5x16"

    # `removed` — FR6's warning, not breakage.
    removed = registry.call("remove_package", {"project": "rig",
                                               "name": "iso4762"})
    assert removed["materialized_parts"] == ["s1"]
    assert service.store.read_script("rig", "s1"), "the script was rewritten"
    rebuilt = service._rebuild("rig", "s1")
    assert rebuilt["ok"] is True, "a removed package broke a project part"
    assert service.get_part("rig", "s1")["package_provenance"]["status"] \
        == "removed"

    # `modified` — a local edit is legitimate, reported, never repaired.
    registry.call("add_package", {"project": "rig", "name": "iso4762"})
    path = service.store.script_path("rig", "s1")
    path.write_text(path.read_text() + "\n# my edit\n")
    assert service.get_part("rig", "s1")["package_provenance"]["status"] \
        == "modified"


# =================================================================== AC7


def test_ac7_the_library_dialog_and_its_routes_exist_and_carry_the_non_claim():
    """**AC7, the gradeable half** — the browser session itself is in
    changelogs 0177 and 0178 (driven twice: once against a one-package
    catalog, again against all nine, zero console errors and zero failed
    requests both times). What a test can grade is that the surface those
    sessions drove is still here and still says the thing it must say.
    """
    library = (FRONTEND / "js" / "library.js").read_text(encoding="utf-8")
    index = (FRONTEND / "index.html").read_text(encoding="utf-8")
    assert "library-modal" in index
    assert "searchPackages" in library and "usePackagePart" in library
    # Decision 11, place 8: visible text in the install affordance, never a
    # tooltip — a claim nobody can read is a claim nobody made.
    assert "your kernel" in index and "privileges" in index

    routes = (REPO / "agentcad" / "server" / "routes_packages.py").read_text()
    for path in ("/packages/search", "/projects/{proj}/packages",
                 "/packages/{name}/versions/{version}/preview"):
        assert path in routes, f"the dialog's route {path} is gone"

    entry = (CHANGELOG / "0177-package-routes-and-library-ui.md").read_text()
    assert "ERROR COUNT: 0" in entry
    latest = (CHANGELOG / "0178-cots-starter-set.md").read_text()
    assert "ERROR COUNT: 0" in latest and "FAILED REQUESTS: 0" in latest


# =================================================================== AC8


def test_ac8_the_ocp_free_guarantee_covers_every_new_module():
    """**AC8, second half** — the no-OCP-outside-kernel guarantee holds for
    all new modules (the import-hygiene test).

    This asserts the *coverage*: `tests/test_packages_ocp_free.py` runs a
    fresh interpreter per module with `OCP`/`build123d` blocked at
    `sys.meta_path`, and its list is compared with the directory, so a module
    added without a probe is a red test rather than a silent gap. What is
    checked here is that the list has not been narrowed to make that pass.
    """
    from tests import test_packages_ocp_free as probe

    on_disk = {f"agentcad.core.packages.{path.stem}"
               for path in (REPO / "agentcad" / "core" / "packages").glob("*.py")
               if path.stem != "__init__"}
    assert set(probe.OCP_FREE) == on_disk, "a module has no OCP-free probe"
    assert "agentcad.core.tools_packages" in probe.EXTRA_OCP_FREE
    # The one module of this feature that is ALLOWED to import OCP, and the
    # only one: the worker handler pack slice 13 added.
    handler = (REPO / "agentcad" / "kernel" / "handlers" / "reffaces.py")
    assert handler.is_file() and "from OCP" in handler.read_text()


def test_ac8_the_full_suite_count_is_cited():
    """**AC8, first half** — "full suite green" is a claim about a *run*, so
    this is the evidence check that a count is on the record in the close-out
    changelog (the PRD-004 AC10 / PRD-008 AC9 / PRD-009 AC6 / PRD-010 AC8
    precedent).

    It stays an evidence check deliberately: recomputing the number would mean
    running the full suite from inside the full suite, and `--collect-only`
    counts *cases*, which is not what `make test` reports.
    """
    entry = CHANGELOG / "0180-prd-011-docs-and-acceptance.md"
    assert entry.is_file(), "the PRD-011 close-out changelog entry is missing"
    text = entry.read_text(encoding="utf-8")
    assert "make test" in text and "passed" in text
    assert any(token.isdigit() and len(token) >= 4
               for token in text.replace(",", " ").split()), \
        "the close-out entry does not cite a suite count"

    latest = max(CHANGELOG.glob("0[0-9][0-9][0-9]-*.md"))
    if latest != entry:
        recent = latest.read_text(encoding="utf-8")
        assert "make test" in recent and "passed" in recent, (
            f"{latest.name} is the newest changelog entry and cites no suite "
            "count; every entry that lands work must cite one")


# =================================================================== AC9


#: The three ways `widget_good` is broken for AC9, and the *row* each one
#: produces. Every fix below is derived from that row's structured fields —
#: never from the English in `message`.
_BREAKAGES = ("specs:mount_block@length=min:not_hollowed_out",
              "presets:presets.mount_blockk",
              "docs:docs/README.md")


def _break_the_package(pkg: Path) -> None:
    """Three faults the gate names precisely, none of them a syntax error.

    * a **spec that fails only at an extreme** — the declared volume floor is
      above what the family's own shortest variant can reach, so the default
      passes and `length=min` does not;
    * a **preset naming a part the package does not ship** (`mount_blockk`);
    * a **README that stopped naming its part**.
    """
    part = pkg / "parts" / "mount_block.py"
    part.write_text(part.read_text().replace("min_mm3=1000.0", "min_mm3=8000.0"))
    doc = json.loads((pkg / "presets.json").read_text())
    doc["presets"]["mount_blockk"] = doc["presets"].pop("mount_block")
    (pkg / "presets.json").write_text(json.dumps(doc, indent=2) + "\n")
    readme = pkg / "docs" / "README.md"
    readme.write_text(readme.read_text().replace("mount_block", "the block"))


def _agent_pass(pkg: Path, report: dict) -> list[str]:
    """Play the agent: one mechanical fix per failing ROW.

    The rule this function obeys, and the whole point of AC9: it may read
    `report` and it may read the files `report` names, and **it may not know
    anything about this package**. No fixture id, no parameter name and no
    file path is written into it — every one is taken out of a row.
    """
    declared = _declared_parts(report)
    applied = []
    for stage in report["stages"]:
        for row in stage.get("items") or []:
            if row["status"] not in ("fail", "error"):
                continue
            name = stage["name"]
            if name == "specs":
                applied.append(_fix_spec_limit(pkg, report, row))
            elif name == "presets":
                applied.append(_fix_preset_part(pkg, row, declared))
            elif name == "docs":
                applied.append(_fix_readme(pkg, row))
            else:
                raise AssertionError(
                    f"the agent has no mechanical fix for {row['id']}: "
                    f"{row['message']}")
    return applied


def _declared_parts(report) -> list[str]:
    """The package's part ids, from the `format` stage's own rows."""
    return sorted(row["id"].split("parts.", 1)[1]
                  for row in _stage(report, "format")["items"]
                  if row["kind"] == "part" and "parts." in row["id"])


def _part_file(report, part_id: str) -> str:
    """The declared file of one part, from the `format` row's `details`."""
    for row in _stage(report, "format")["items"]:
        if row["id"].endswith(f"parts.{part_id}"):
            return row["details"]["file"]
    raise AssertionError(f"no format row names the file of {part_id!r}")


def _fix_spec_limit(pkg: Path, report, row) -> str:
    """The declared limit is above what the package's own extremes reach.

    Everything the edit needs is in the row: `details.limit` is the
    constructor keyword **and** its value (`{"min_mm3": 8000.0}`),
    `details.measured` is what the geometry actually produced, and
    `details.part` names the variant — whose part id is the text before `@`,
    which the `format` stage maps to a file.

    Choosing to move the *limit* rather than the parameter's range is the
    agent's one judgement here, and it is a judgement (a package could
    equally narrow the range). What AC9 grades is that the report carries
    everything the chosen fix needs, unambiguously, without reading English.
    """
    check = row["id"].rsplit(":", 1)[1]
    failing = [r for r in _stage(report, "specs")["items"]
               if r["status"] == "fail" and r["id"].endswith(f":{check}")]
    worst = min(r["details"]["measured"] for r in failing)
    (keyword, declared), = row["details"]["limit"].items()
    part_id = row["details"]["part"].split("@", 1)[0]
    path = pkg / _part_file(report, part_id)
    text = path.read_text()
    old = f"{keyword}={declared}"
    assert text.count(old) == 1, (
        f"{old!r} is not a unique literal in {path.name}: the mechanical "
        f"rewrite would be ambiguous")
    # Below the smallest measured value, so the check passes at every variant
    # it just failed at rather than at this one only.
    path.write_text(text.replace(old, f"{keyword}={float(int(worst))}"))
    return f"{path.name}: {old} -> {keyword}={float(int(worst))}"


def _fix_preset_part(pkg: Path, row, declared: list[str]) -> str:
    """A configuration names a part id the package does not declare.

    `details.field` is `presets.<bad id>`; the declared ids come from the
    `format` stage. With exactly one declared part the rename is determined.
    """
    bad = row["details"]["field"].split("presets.", 1)[1]
    assert len(declared) == 1, "the rename is only determined for one part"
    path = pkg / "presets.json"
    doc = json.loads(path.read_text())
    doc["presets"][declared[0]] = doc["presets"].pop(bad)
    path.write_text(json.dumps(doc, indent=2) + "\n")
    return f"presets.json: {bad} -> {declared[0]}"


def _fix_readme(pkg: Path, row) -> str:
    """The README does not name every part. `details.missing` is the list and
    `details.path` is the file — both fields exist *because writing this test
    found the row carrying them only in its sentence* (changelog 0180)."""
    path = pkg / row["details"]["path"]
    missing = row["details"]["missing"]
    path.write_text(path.read_text()
                    + "\n## Parts\n\n"
                    + "".join(f"- `{part_id}`\n" for part_id in missing))
    return f"{path.name}: documented {', '.join(missing)}"


@pytest.mark.integration
def test_ac9_an_agent_takes_a_red_package_green_from_the_report_alone(
        service, tmp_path):
    """**AC9** (adopted at the design review, not in the PRD) — an agent takes
    a deliberately broken package from a **red gate report to green with no
    human intervention**, driven only by the report's structured content.

    The loop is the real one: `validate` → read `stages[].items[]` → fix →
    `validate` again → publish. `_agent_pass` is the agent, and it is written
    under one rule: it may read the report and the files the report names, and
    it knows nothing else about this package — no part id, no parameter name
    and no file path appears in it.

    **What this proves and what it does not.** It proves the rows are
    *addressable*: each names its subject, its file, its measured value and
    its declared value as data, so an edit can be constructed without parsing
    English. It does not prove an agent will pick the *right* fix — the spec
    row admits two consistent repairs (move the limit or narrow the range) and
    choosing between them is engineering judgement, which the report cannot
    and should not make. Writing this test is also what found the `docs` row
    carrying its missing part ids **only in its sentence**; that row now
    carries `details.missing`, and if it did not, this test could not be
    written honestly.
    """
    pkg = _copy(FIXTURES / "widget_good", tmp_path / "pkg")
    _break_the_package(pkg)
    gate_ = gate.PackageGate(service)

    red = gate_.run(pkg, work_dir=str(tmp_path / "w"))
    assert red["publishable"] is False
    assert sorted(row["id"] for row in _failures(red)) == sorted(_BREAKAGES), \
        "the gate did not name the three faults precisely"

    applied = _agent_pass(pkg, red)
    assert len(applied) == 3, applied

    green = gate_.run(pkg, work_dir=str(tmp_path / "w"))
    assert green["publishable"] is True, green["blockers"]
    assert _failures(green) == []
    # One pass, not a loop that converged by accident.
    assert gate_.run(pkg, work_dir=str(tmp_path / "w"))["publishable"] is True

    # And the repaired package is publishable in the literal sense too.
    index = _index(tmp_path / "idx")
    result = indexes.publish(index, pkg, service, work_dir=str(tmp_path / "w2"))
    assert result["published"] == "widget_good@1.0.0"


# ==================================================== the PRD's own record


def test_the_prd_records_the_divergences_the_design_measured():
    """The design spec's eight "fold back" items are decisions a reader of the
    PRD has to find in the PRD, not only in a spec nobody opens."""
    text = PRD.read_text(encoding="utf-8")
    assert any(f"**Status:** {state}" in text
               for state in ("implemented", "completed")), \
        "the PRD's status is not a post-implementation one"
    for needle in (
            "content listing",   # FR2: there is no archive
            "cross product",     # FR9: one parameter at a time, plus presets
            "drawings",          # FR9: the gate does not check drawings
            "PRD-012",           # FR1: the frozen configuration schema
            "not a security boundary",
            "reference part",    # FR13's shape, and use_part's refusal
    ):
        assert needle in text, f"the PRD does not record {needle!r}"
    for ac in ("AC1", "AC2", "AC3", "AC4", "AC5", "AC6", "AC7", "AC8"):
        assert ac in text


def test_the_roadmap_link_resolves_to_the_prd_where_it_actually_lives():
    roadmap = (REPO / "docs" / "roadmap.md").read_text(encoding="utf-8")
    row = next(line for line in roadmap.splitlines()
               if line.startswith("| [011]"))
    match = re.search(r"\((prd/[^)]+\.md)\)", row)
    assert match, f"the roadmap row for PRD-011 carries no link: {row}"
    assert (REPO / "docs" / match.group(1)).is_file(), \
        f"the roadmap link {match.group(1)} does not resolve"


def test_the_documentation_states_the_trust_boundary_where_it_matters():
    """Decision 11's standing rule: the publish gate is **not** a security
    boundary, said everywhere the docs describe publishing or installing."""
    packages = (REPO / "docs" / "packages.md").read_text(encoding="utf-8")
    # First screen, not an appendix.
    assert "not a security boundary" in packages[:4000]
    for needle in ("PRD-006", "signatures", "content id", "packages_lock",
                   "yank"):
        assert needle in packages, f"docs/packages.md does not cover {needle!r}"
    agents = (REPO / "AGENTS.md").read_text(encoding="utf-8")
    assert "PRD-011" in agents and "gate_providers" in agents
    api = (REPO / "docs" / "agent-api.md").read_text(encoding="utf-8")
    for tool in ("search_packages", "add_package", "use_part",
                 "validate_package", "package_from_step"):
        assert tool in api, f"docs/agent-api.md does not document {tool}"


def test_prd012s_fr1_carries_the_frozen_configuration_schema():
    """The amendment this feature owes PRD-012: its FR1 must specify the
    **wrapped** entry, because the flat map is ambiguous the day a part
    declares a parameter called `label` — and slice 1's
    `validate_configuration` is the one validator both features use."""
    prd012 = next((REPO / "docs" / "prd").rglob("PRD-012-configurations.md"))
    text = prd012.read_text(encoding="utf-8")
    fr1 = text.split("- FR1.", 1)[1].split("- FR2.", 1)[0]
    assert "configs: {name → configuration}" in fr1, \
        "PRD-012 FR1 still specifies the flat configuration map"
    for needle in ("params", "label", "description", "Amended by PRD-011",
                   "validate_configuration"):
        assert needle in fr1, f"the amended FR1 does not mention {needle!r}"
    # The same document a package ships validates through the same function.
    entry = {"params": {"length": 16.0}, "label": "M5 × 16"}
    assert pkgformat.validate_configuration(entry, None) == []
    assert pkgformat.validate_configuration({"width": 10}, None), \
        "the flat shape is accepted"


# ----------------------------------------------------------------- helpers


def _index(root: Path, *, name="vendor", scope="private"):
    root.mkdir(parents=True, exist_ok=True)
    (root / "index.json").write_text(json.dumps(
        {"format": 1, "name": name, "scope": scope, "packages": {},
         "embeddings": None}, indent=2))
    return indexes.LocalIndex(name, root, scope=scope)


def _tree_hash(root: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    for path in sorted(p for p in Path(root).rglob("*") if p.is_file()):
        digest.update(str(path.relative_to(root)).encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
    return digest.hexdigest()
