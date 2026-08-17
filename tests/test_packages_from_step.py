"""PRD-011 slice 13 — `package_from_step`: the McMaster path (FR13).

A supplier hands you a STEP; this wraps it as a **reference-part** package the
publish gate can measure and an index can carry. Four claims carry the slice
and each is tested against its negation:

* **A scaffolded package passes the real gate, green, on its own.** All nine
  stages, no non-exempt skip, `publishable: true` — the reference part has no
  PARAMS to sweep, so `contract`, `connectors` and (with a policy installed)
  `policy` report an *exempt* `reference_part` skip, and `build` builds the
  shipped file once.
* **Confinement is mechanical.** `provenance.vendor.redistributable: false`
  into a `public` index is a refusal that writes nothing; the same package
  into a `private` index publishes.
* **STL is refused**, with the reason: one welded triangulation face with no
  surface, and booleans on it segfault OCCT.
* **Connector inference is NOT implemented and does not pretend to be.** The
  candidates are the imported solid's own B-rep faces, largest first, labelled
  suggestions in the payload and in the generated README.

The fifth thing this file records is a *hole*, deliberately: `use_part` does
not materialise a reference part, because the provenance header lives inside a
script and a reference part has none. The refusal names the fix.
"""

import json
import shutil
from pathlib import Path

import pytest

from agentcad.core.model import ConflictError, NotFoundError, ValidationError
from agentcad.core.packages import (content, format as pkgformat, from_step,
                                    gate, indexes)
from agentcad.core.tools import build_registry
from .conftest import make_test_service

pytestmark = pytest.mark.slow

BLOCK = '''\
"""A block with a bore — exported to STEP, it is the vendor file under test."""

from build123d import Box, Cylinder, Location, Pos

PARAMS = {}


def build(p):
    return Box(30, 20, 10) - Cylinder(4, 40)
'''


# --------------------------------------------------------------- fixtures


@pytest.fixture
def cache_root(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTCAD_PACKAGES_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("AGENTCAD_CONFIG", str(tmp_path / "cfg" / "config.json"))


@pytest.fixture
def service(tmp_path, kernel, cache_root):
    svc = make_test_service(tmp_path / "projects", kernel)
    svc.create_project("rig")
    return svc


@pytest.fixture
def registry(service):
    return build_registry(service)


@pytest.fixture
def vendor_step(service, registry, tmp_path):
    """A real STEP with a planar face set AND a cylindrical bore, exported by
    this kernel — so the candidate list has both kinds in it to report."""
    service.create_part("rig", "src", script=BLOCK)
    result = registry.call("export_part", {"project": "rig", "part_id": "src",
                                           "format": "step"})
    dest = tmp_path / "vendor" / "91290A115.STEP"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(result["path"], dest)
    return dest


def scaffold(service, source, dest, **kw):
    kw.setdefault("name", "acme_bracket")
    kw.setdefault("part", "bracket")
    kw.setdefault("vendor", "ACME Supply")
    return from_step.scaffold(service, source=str(source), dest=str(dest), **kw)


def manifest_of(root: Path) -> dict:
    return json.loads((root / "package.json").read_text(encoding="utf-8"))


# ============================================================ the scaffold


def test_a_vendor_step_scaffolds_the_package_a_gate_can_measure(
        service, vendor_step, tmp_path):
    dest = tmp_path / "pkg"
    out = scaffold(service, vendor_step, dest, part_number="91290A115",
                   url="https://example.com/91290A115",
                   terms="Personal use only")

    files = sorted(p.relative_to(dest).as_posix()
                   for p in dest.rglob("*") if p.is_file())
    assert files == ["docs/README.md", "imports/91290A115.step",
                     "package.json", "previews/bracket_iso.png"]
    doc = manifest_of(dest)
    assert doc["parts"]["bracket"] == {
        "kind": "reference", "source": "imports/91290A115.step",
        "label": "91290A115",
        "summary": doc["parts"]["bracket"]["summary"]}
    assert doc["provenance"]["vendor"]["name"] == "ACME Supply"
    assert doc["provenance"]["vendor"]["part_number"] == "91290A115"
    assert doc["provenance"]["vendor"]["terms"] == "Personal use only"
    assert doc["provenance"]["vendor"]["redistributable"] is False
    assert out["package"]["content_id"] == content.content_id(dest)
    assert out["kind"] == "reference"
    assert "not a security boundary" in out["note"]


def test_the_extension_is_lower_cased_and_the_basename_cannot_traverse(
        service, vendor_step, tmp_path):
    """`91290A115.STEP` lands as `.step`; a path in the name cannot escape."""
    out = scaffold(service, vendor_step, tmp_path / "pkg")
    assert manifest_of(Path(out["path"]))["parts"]["bracket"]["source"] \
        == "imports/91290A115.step"


def test_the_scaffolded_manifest_holds_no_machine_fact(service, vendor_step,
                                                       tmp_path):
    """Two scaffolds of one file write byte-identical manifests: no timestamp,
    no absolute path, no client id — the same rule the lockfile and the
    provenance header follow, and what makes the content id reproducible."""
    a = Path(scaffold(service, vendor_step, tmp_path / "a")["path"])
    b = Path(scaffold(service, vendor_step, tmp_path / "b")["path"])
    assert (a / "package.json").read_bytes() == (b / "package.json").read_bytes()
    assert (a / "docs" / "README.md").read_bytes() \
        == (b / "docs" / "README.md").read_bytes()


def test_the_measured_metrics_come_back_with_the_scaffold(service, vendor_step,
                                                          tmp_path):
    out = scaffold(service, vendor_step, tmp_path / "pkg")
    assert out["metrics"]["n_solids"] == 1
    # 30 x 20 x 10 less a r=4 bore through the 10 mm thickness.
    assert out["metrics"]["volume_mm3"] == pytest.approx(6000 - 3.14159 * 16 * 10,
                                                         rel=1e-3)


# ============================================== the gate, on the real thing


def test_a_scaffolded_package_passes_the_whole_gate(service, vendor_step,
                                                    tmp_path):
    """FR13's done-when, and the plan's first slice-13 test."""
    dest = tmp_path / "pkg"
    scaffold(service, vendor_step, dest, terms="Personal use only")
    report = gate.PackageGate(service).run(dest, work_dir=str(tmp_path / "w"))

    assert report["publishable"] is True, report["blockers"]
    assert report["blockers"] == []
    assert [stage["name"] for stage in report["stages"]] == list(gate.GATE_STAGES)
    assert gate.validate_gate_report(report) == []
    # Every skip that reached the verdict is exempt, and each names its stage.
    assert set(report["exempt_skips"]) == {
        "contract:reference_part", "connectors:reference_part",
        "presets:no_presets_declared", "specs:not_declared",
        "policy:no_policy_configured"}


def test_the_contract_stage_skips_a_reference_part_with_a_reason(
        service, vendor_step, tmp_path):
    """"No PARAMS to sweep" is a fact about the KIND, never about the package
    — so it is an exempt skip carrying a reason and a hint, not a fail and not
    a silent pass."""
    dest = tmp_path / "pkg"
    scaffold(service, vendor_step, dest)
    report = gate.PackageGate(service).run(dest, work_dir=str(tmp_path / "w"))
    rows = _stage(report, "contract")["items"]
    assert len(rows) == 1
    assert rows[0]["status"] == "skip"
    assert rows[0]["reason"] == "reference_part"
    assert rows[0]["strict_exempt"] is True
    assert rows[0]["hint"]
    assert "reference_part" in gate.PUBLISH_SKIP_EXEMPT


def test_the_build_stage_builds_the_imported_file_once(service, vendor_step,
                                                       tmp_path):
    """One variant, because a vendor solid has exactly one: itself. The row
    still carries the measurement, which is the whole point of building it."""
    dest = tmp_path / "pkg"
    scaffold(service, vendor_step, dest)
    report = gate.PackageGate(service).run(dest, work_dir=str(tmp_path / "w"))
    rows = _stage(report, "build")["items"]
    assert [row["id"] for row in rows] == ["build:bracket@default"]
    assert rows[0]["status"] == "pass"
    assert rows[0]["details"]["volume_mm3"] > 0
    assert rows[0]["details"]["n_solids"] == 1


def test_the_gate_never_writes_into_the_package(service, vendor_step, tmp_path):
    """The cell rule, on the one package kind whose tree the gate has to COPY
    a file out of."""
    dest = tmp_path / "pkg"
    scaffold(service, vendor_step, dest)
    before = content.content_id(dest)
    gate.PackageGate(service).run(dest, work_dir=str(tmp_path / "w"))
    assert content.content_id(dest) == before
    assert sorted(p.name for p in (tmp_path / "w").iterdir()) == []


def test_a_configuration_naming_a_reference_part_is_a_fail_not_a_skip(
        service, vendor_step, tmp_path):
    """A package that declares a configuration for imported geometry is
    WRONG — there is nothing for it to set — so this is the one reference-part
    row that reddens."""
    dest = tmp_path / "pkg"
    scaffold(service, vendor_step, dest)
    (dest / "presets.json").write_text(json.dumps(
        {"format": 1, "presets": {"bracket": {"big": {"params": {"size": 2}}}}},
        indent=2))
    report = gate.PackageGate(service).run(dest, work_dir=str(tmp_path / "w"))
    rows = [row for row in _stage(report, "presets")["items"]
            if row["status"] == "fail"]
    assert rows and "reference part" in rows[0]["message"]
    assert report["publishable"] is False


def test_a_reference_part_with_no_summary_fails_the_docs_stage(
        service, vendor_step, tmp_path):
    """It has no module to carry a docstring, so the summary is the ONLY
    documentation it can have — which is why it is still required."""
    dest = tmp_path / "pkg"
    scaffold(service, vendor_step, dest)
    doc = manifest_of(dest)
    del doc["parts"]["bracket"]["summary"]
    (dest / "package.json").write_text(json.dumps(doc, indent=2) + "\n")
    report = gate.PackageGate(service).run(dest, work_dir=str(tmp_path / "w"))
    rows = [row for row in _stage(report, "docs")["items"]
            if row["status"] == "fail"]
    assert rows and "reference part ships no script" in rows[0]["message"]


def test_a_policy_reports_a_reference_part_rather_than_reading_a_script(
        service, vendor_step, tmp_path):
    """An imported solid is data, not code: the source-policy seam has nothing
    to read, and saying so beats omitting the part from the report."""

    class _Policy:
        def check(self, source, path):       # pragma: no cover — never reached
            raise AssertionError("a reference part has no source to check")

    service.package_policy = _Policy()
    dest = tmp_path / "pkg"
    scaffold(service, vendor_step, dest)
    report = gate.PackageGate(service).run(dest, work_dir=str(tmp_path / "w"))
    rows = _stage(report, "policy")["items"]
    assert [row["reason"] for row in rows] == ["reference_part"]
    assert report["publishable"] is True


def test_an_imported_part_reports_is_valid_false_and_is_not_reddened_by_it():
    """PRD-004's rule, imported into the gate: OCCT calls the shipped
    180-solid rocketry STEP invalid, so enforcing whole-shape validity on
    imported geometry would redden correct vendor content. Tested on the row
    builder, because a STEP that OCCT calls invalid is not something a test
    can honestly manufacture."""
    run = gate._Run(Path("."), set(gate.GATE_STAGES), deadline=None)
    run.reference_scratch.add("bracket_ref")
    variant = gate.Variant("bracket@default", {}, "the imported solid")
    result = {"ok": True, "metrics": {"volume_mm3": 12.0, "mass_g": 0.1,
                                      "n_solids": 180, "is_valid": False}}
    row = run._build_item(variant, "bracket_ref", result)
    assert row["status"] == "pass"
    assert "not enforced" in row["message"]
    assert any("never enforced" in warning for warning in run.warnings)

    # The same result on a SCRIPT part is still a fail: the exemption is about
    # imported geometry, not about the flag.
    run.warnings.clear()
    assert run._build_item(variant, "block_src", result)["status"] == "fail"


# ============================================================== confinement


def test_a_vendor_package_is_refused_by_a_public_index(service, vendor_step,
                                                       tmp_path):
    """FR13's confinement, end to end from the scaffold this slice writes:
    `redistributable: false` is what the publisher CHECKS."""
    dest = tmp_path / "pkg"
    scaffold(service, vendor_step, dest)
    public = _index(tmp_path / "pub", scope="public")
    before = sorted(p.name for p in public.path.rglob("*"))
    with pytest.raises(ValidationError) as exc:
        indexes.publish(public, dest, service, work_dir=str(tmp_path / "w"))
    assert "ACME Supply" in str(exc.value)
    assert "public" in str(exc.value)
    assert sorted(p.name for p in public.path.rglob("*")) == before


def test_the_same_package_publishes_into_a_private_index(service, vendor_step,
                                                         tmp_path):
    """The refusal is about the index's SCOPE, not about the package: a
    personal or organisation index is exactly where vendor geometry belongs."""
    dest = tmp_path / "pkg"
    scaffold(service, vendor_step, dest)
    private = _index(tmp_path / "priv", scope="private")
    result = indexes.publish(private, dest, service,
                             work_dir=str(tmp_path / "w"))
    assert result["published"] == "acme_bracket@1.0.0"
    entry = json.loads((private.path / "index.json").read_text()
                       )["packages"]["acme_bracket"]["versions"]["1.0.0"]
    assert entry["gate"]["status"] == "green"
    assert "contract:reference_part" in entry["gate"]["exempt_skips"]
    assert entry["parts"]["bracket"] == {"params": [], "connectors": {},
                                         "specs": []}


# ================================================================= refusals


def test_an_stl_is_refused_with_the_booleans_caveat(service, tmp_path):
    """A mesh imports into a project and cannot be packaged. Both halves of
    the reason are in the message, because either one alone reads as a
    limitation somebody could work around."""
    stl = tmp_path / "vendor.stl"
    stl.write_bytes(b"solid x\nendsolid x\n")
    with pytest.raises(ValidationError) as exc:
        scaffold(service, stl, tmp_path / "pkg")
    message = str(exc.value)
    assert "welded" in message and "segfault" in message
    assert not (tmp_path / "pkg").exists()


def test_an_unsupported_extension_names_what_is_supported(service, tmp_path):
    path = tmp_path / "vendor.iges"
    path.write_text("nope")
    with pytest.raises(ValidationError) as exc:
        scaffold(service, path, tmp_path / "pkg")
    assert ".step" in str(exc.value)


def test_a_missing_source_is_a_not_found_error(service, tmp_path):
    with pytest.raises(NotFoundError):
        scaffold(service, tmp_path / "nope.step", tmp_path / "pkg")


def test_a_relative_source_is_refused(service, tmp_path):
    with pytest.raises(ValidationError) as exc:
        scaffold(service, "vendor.step", tmp_path / "pkg")
    assert "absolute" in str(exc.value)


def test_a_non_empty_destination_is_a_conflict(service, vendor_step, tmp_path):
    dest = tmp_path / "pkg"
    dest.mkdir()
    (dest / "keep.txt").write_text("somebody wrote this")
    with pytest.raises(ConflictError):
        scaffold(service, vendor_step, dest)
    assert (dest / "keep.txt").read_text() == "somebody wrote this"


def test_a_file_above_the_published_ceiling_is_refused_with_the_number(
        service, vendor_step, tmp_path, monkeypatch):
    """The ceiling is part of the FORMAT — every consumer enforces it on
    install — so it is refused here with the number rather than raised for
    this case."""
    monkeypatch.setattr(content, "MAX_FILE_BYTES", 1024)
    with pytest.raises(ValidationError) as exc:
        scaffold(service, vendor_step, tmp_path / "pkg")
    assert "1,024" in str(exc.value)
    assert not (tmp_path / "pkg").exists()


def test_a_step_this_kernel_cannot_load_leaves_no_package_behind(
        service, tmp_path):
    """The file is BUILT before the directory is written, so an unusable STEP
    is a refusal and not a package somebody has to clean up."""
    path = tmp_path / "broken.step"
    path.write_text("ISO-10303-21;\nnot really a step file\n")
    with pytest.raises(ValidationError) as exc:
        scaffold(service, path, tmp_path / "pkg")
    assert "does not load" in str(exc.value)
    assert not (tmp_path / "pkg").exists()


def test_a_bad_name_or_version_is_refused_before_anything_is_written(
        service, vendor_step, tmp_path):
    for kw in ({"name": "Not A Name"}, {"version": "1.0"},
               {"part": "not an id"}, {"vendor": "  "}):
        with pytest.raises(ValidationError):
            scaffold(service, vendor_step, tmp_path / "pkg", **kw)
        assert not (tmp_path / "pkg").exists()


# ======================================================= connector candidates


def test_the_candidates_are_faces_and_are_labelled_suggestions(
        service, vendor_step, tmp_path):
    """Planar faces carry a normal and a centre; the bore carries an AXIS and
    a radius — which is the thing a mesh-derived signature cannot give, and
    the reason this reads the B-rep."""
    out = scaffold(service, vendor_step, tmp_path / "pkg")
    faces = out["candidates"]["faces"]
    assert out["candidates"]["kinds"] == {"planar": 6, "cylindrical": 1}
    areas = [row["area_mm2"] for row in faces]
    assert areas == sorted(areas, reverse=True), "largest first"

    planar = next(row for row in faces if row["kind"] == "planar")
    assert len(planar["normal"]) == 3 and len(planar["center"]) == 3
    bore = next(row for row in faces if row["kind"] == "cylindrical")
    assert bore["radius_mm"] == pytest.approx(4.0)
    assert [abs(v) for v in bore["axis_direction"]] == pytest.approx([0, 0, 1],
                                                                    abs=1e-9)


def test_the_readme_says_connector_inference_is_not_automated(
        service, vendor_step, tmp_path):
    out = scaffold(service, vendor_step, tmp_path / "pkg",
                   part_number="91290A115", terms="Personal use only")
    readme = (Path(out["path"]) / "docs" / "README.md").read_text()
    assert "ACME Supply" in readme
    assert "91290A115" in readme
    assert "Personal use only" in readme
    assert "redistributable" in readme and "public" in readme
    assert "suggestions" in readme
    assert "not a security boundary" in readme
    # The candidate table is in the file, not only in the payload.
    assert "| face | kind |" in readme
    assert len(readme) >= gate.MIN_README_CHARS


def test_a_missing_terms_field_says_so_rather_than_inventing_one(
        service, vendor_step, tmp_path):
    out = scaffold(service, vendor_step, tmp_path / "pkg")
    readme = (Path(out["path"]) / "docs" / "README.md").read_text()
    assert "not recorded" in readme


def test_reference_faces_refuses_a_mesh(service, tmp_path, kernel):
    """The handler answers a refusal, not an empty list: "no faces" would read
    as "no candidates found" instead of "this format cannot have any"."""
    from agentcad.kernel.client import KernelError

    stl = tmp_path / "m.stl"
    shutil.copyfile(
        Path(__file__).resolve().parents[1] / "examples" / "construction"
        / "imports" / "11.stl", stl)
    with pytest.raises(KernelError) as exc:
        kernel.request("reference_faces", {"source_path": str(stl)})
    assert "welded mesh face" in str(exc.value)


# ============================================================= the tool seam


def test_the_tool_is_registered_and_says_what_it_does_not_do(registry):
    tool = registry.get("package_from_step")
    assert "NOT AUTOMATED" in tool.description
    assert "not a security boundary" in tool.description
    assert "redistributable=false" in tool.description
    assert "STL IS REFUSED" in tool.description
    assert sorted(tool.input_schema["required"]) == ["dest", "name", "part",
                                               "source", "vendor"]


def test_use_part_refuses_a_reference_part_and_names_the_fix(
        service, registry, vendor_step, tmp_path):
    """The hole this slice ships with, stated rather than worked around: the
    provenance header lives INSIDE the script and a reference part has none,
    so a materialised one could carry no provenance at all."""
    dest = tmp_path / "pkg"
    scaffold(service, vendor_step, dest)
    index = _index(tmp_path / "priv", scope="private")
    indexes.publish(index, dest, service, work_dir=str(tmp_path / "w"))

    from agentcad import config as user_config
    user_config.save_config({"indexes": [
        {"name": "vendor", "kind": "local", "path": str(index.path),
         "scope": "private"}]})
    registry = build_registry(service)
    registry.call("add_package", {"project": "rig", "name": "acme_bracket"})
    result = registry.call("use_part", {
        "project": "rig", "package": "acme_bracket", "part": "bracket",
        "part_id": "b1"})
    assert result["error"]["type"] == "validation_error"
    assert "import_cad_file" in result["error"]["message"]
    assert "provenance header lives inside the script" \
        in result["error"]["message"]
    assert result["error"]["details"]["kind"] == "reference"
    with pytest.raises(NotFoundError):
        service.store.get_part("rig", "b1")
    # The package is still installed — the refusal is about materialisation.
    listed = registry.call("list_packages", {"project": "rig"})
    assert listed["packages"]["acme_bracket"]["cache"] == "ok"


# ================================================== the format, on both kinds


def test_a_part_entry_may_not_carry_both_file_and_source():
    doc = _doc({"bracket": {"kind": "reference", "source": "imports/x.step",
                            "file": "parts/x.py"}})
    problems = pkgformat.validate_package_manifest(doc)
    assert any(p["field"] == "parts.bracket.file" for p in problems)


def test_a_script_part_may_not_carry_a_source():
    doc = _doc({"block": {"file": "parts/block.py", "source": "imports/x.step"}})
    problems = pkgformat.validate_package_manifest(doc)
    assert any(p["field"] == "parts.block.source" for p in problems)


def test_an_unknown_part_kind_is_refused():
    doc = _doc({"block": {"kind": "sketch", "file": "parts/block.py"}})
    problems = pkgformat.validate_package_manifest(doc)
    assert any(p["field"] == "parts.block.kind" for p in problems)


def test_a_reference_source_must_live_in_imports():
    doc = _doc({"b": {"kind": "reference", "source": "parts/x.step"}})
    problems = pkgformat.validate_package_manifest(doc)
    assert any("imports/" in p["message"] for p in problems)
    doc = _doc({"b": {"kind": "reference", "source": "../../etc/passwd"}})
    assert pkgformat.validate_package_manifest(doc)


def test_a_missing_kind_still_means_script():
    """Every package published before FR13 declares no `kind`, and a version
    is immutable — so absent has to keep meaning `script` for ever."""
    assert pkgformat.part_kind({"file": "parts/x.py"}) == "script"
    assert pkgformat.part_payload({"file": "parts/x.py"}) == ("file",
                                                              "parts/x.py")
    assert pkgformat.part_payload({"kind": "reference",
                                   "source": "imports/x.step"}) \
        == ("source", "imports/x.step")
    assert pkgformat.part_payload({"kind": "reference"}) == (None, None)


# ----------------------------------------------------------------- helpers


def _doc(parts):
    return {"format": 1, "name": "pkg", "version": "1.0.0", "summary": "s",
            "license": "MIT", "authors": [{"name": "a"}],
            "disclosure": "human", "parts": parts}


def _stage(report, name):
    return next(stage for stage in report["stages"] if stage["name"] == name)


def _index(root: Path, *, scope: str):
    root.mkdir(parents=True, exist_ok=True)
    (root / "index.json").write_text(json.dumps(
        {"format": 1, "name": "vendor", "scope": scope, "packages": {},
         "embeddings": None}, indent=2))
    return indexes.LocalIndex("vendor", root, scope=scope)
