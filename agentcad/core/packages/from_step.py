"""`package_from_step` — wrap a vendor STEP as a reference-part package (FR13).

The McMaster path. A supplier hands you a STEP file; this scaffolds the
directory a publish gate can measure and an index can carry: the file under
``imports/``, a ``reference`` part entry pointing at it, a ``package.json``
whose ``provenance.vendor`` records where it came from and — the mechanical
part — ``redistributable: false``, a ``docs/README.md`` stub naming the vendor
and the terms, and a rendered preview.

**What it does not do: infer connectors.** It reports the imported solid's
planar and cylindrical faces as *candidates* (through the kernel's
``reference_faces`` handler) and the author — human or agent — writes the
``connectors`` function. Deciding which cylinder is "the shaft" from an
unlabelled vendor solid is a research problem, not a slice; the design spec
records it as divergence 7 and PRD-032 is where it belongs.

**Licensing.** ``redistributable: false`` is not a label: ``LocalIndex.
publish`` refuses such a package into an index whose ``scope`` is ``public``
(slice 8), which is the mechanism behind FR13's confinement. Vendor-derived
geometry stays in personal and organisation indexes, and legal review precedes
any public seeding. The generated README says so, in the package, where the
person who publishes it will read it.

**The gate is a correctness gate, not a security boundary** — a STEP is data
rather than code, but the package it lands in is still installed and executed
by the ordinary package machinery, so the non-claim travels here too.

Containment: the scaffold writes into the destination directory and into one
throwaway cell (:func:`gate._ephemeral_service`'s rule, reused rather than
re-derived) and nowhere else. It never opens a user project.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path

from ..model import ConflictError, NotFoundError, ValidationError
from . import content
from . import format as pkgformat

#: What this tool wraps. STEP and BREP are B-reps: exact metrics, real faces,
#: booleans. **STL is refused** — it loads as one welded mesh face with no
#: surface, so it has no faces to suggest connectors from, and its booleans
#: segfault OCCT (AGENTS.md, and `kernel/handlers/reference.py`). A mesh is
#: importable into a project; it is not publishable as a package part.
SUPPORTED_EXTS = (".step", ".stp", ".brep")
MESH_EXTS = (".stl",)

#: The scaffolded package's part kind and where its file lands.
IMPORTS_DIR = "imports"
PREVIEW_SIZE = (320, 240)

#: A package README under this many characters is a stub the gate refuses
#: (`gate.MIN_README_CHARS`). The generated one clears it comfortably, because
#: it has real things to say — but the number is imported rather than restated
#: so the two cannot drift.
_README_TEMPLATE = """\
# {name}

{summary}

**Vendor part.** This package wraps geometry supplied by **{vendor}**{part_no}.
{url_line}It is an *imported solid*, not a parametric script: it has no PARAMS,
no `build(p)` and no `connectors(p, part)`, and the publish gate measures it by
building the shipped file once.

## Licensing — read before publishing

Terms: {terms}

`provenance.vendor.redistributable` is **false** in `package.json`, and that is
mechanical rather than decorative: publishing this package into an index whose
scope is `public` is refused. Vendor-derived geometry belongs in a personal or
organisation index, and legal review precedes any public seeding of it.

## Connectors

This package declares no connectors, because a reference part cannot: a
`connectors(p, part)` function lives in a script. `package_from_step` reported
the candidates below from the imported solid's own B-rep faces — they are
*suggestions*, and nothing infers which one is a mounting face or a shaft.

{candidates}

To give this part connectors, author a script part that imports or rebuilds the
geometry and declares them, and publish that.

## Provenance

- Source file: `{source}`
- Content: {n_solids} solid(s), {volume} mm³, bounding box {bbox}

The publish gate is a CORRECTNESS gate, not a security boundary: it proves that
the geometry loads and measures, and nothing about intent. See
`docs/packages.md`.
"""


def _candidate_lines(candidates: dict) -> str:
    faces = (candidates or {}).get("faces") or []
    if not faces:
        return "_No face candidates were reported._"
    lines = ["| face | kind | area mm² | geometry |", "|---|---|---|---|"]
    for row in faces[:12]:
        if row.get("kind") == "planar":
            geometry = (f"normal {_vec(row.get('normal'))} at "
                        f"{_vec(row.get('center'))}")
        elif row.get("kind") == "cylindrical":
            geometry = (f"⌀{2 * float(row.get('radius_mm') or 0):.3f} axis "
                        f"{_vec(row.get('axis_direction'))} through "
                        f"{_vec(row.get('axis_origin'))}")
        else:
            geometry = "—"
        lines.append(f"| {row.get('index')} | {row.get('kind')} | "
                     f"{float(row.get('area_mm2') or 0):.2f} | {geometry} |")
    if candidates.get("truncated"):
        lines.append(f"\n_The {candidates.get('n_faces')} faces are reported "
                     f"largest-first and truncated at "
                     f"{candidates.get('limit')}._")
    return "\n".join(lines)


def _vec(values) -> str:
    if not isinstance(values, (list, tuple)) or len(values) != 3:
        return "?"
    return "(" + ", ".join(f"{float(v):.3f}" for v in values) + ")"


def _round(values) -> str:
    if not isinstance(values, (list, tuple)):
        return "?"
    return "[" + ", ".join(f"{float(v):.2f}" for v in values) + "]"


# ------------------------------------------------------------------ the flow


def scaffold(service, *, source, dest, name, part, version="1.0.0",
             vendor, part_number=None, url=None, terms=None,
             summary=None, license=None, authors=None, disclosure="human",
             keywords=None, standards=None, work_dir=None) -> dict:
    """Write a reference-part package for *source* at *dest*.

    Returns ``{path, package, part, candidates, metrics, warnings, note}``.
    Every refusal is one of the house three errors and happens **before** a
    byte is written, so a rejected call leaves no half-scaffolded directory.
    """
    from .gate import SECURITY_NOTE, _ephemeral_service

    src = _accept_source(source)
    name = _accept(name, pkgformat.NAME_RE, "package name")
    version = _accept(version, pkgformat.VERSION_RE, "version (X.Y.Z)")
    part = _accept(part, pkgformat.PART_ID_RE, "part id")
    if not str(vendor or "").strip():
        raise ValidationError(
            "vendor is required: a package that wraps somebody else's geometry "
            "records whose it is — that record is what the public-index "
            "refusal and any later legal review both read")
    root = _accept_dest(dest)

    warnings: list[str] = []
    stem = _safe_name(src)
    # Measure and render in a cell of our own, then write the package. The
    # order matters: a STEP that will not load is a refusal, not a package
    # directory somebody has to clean up.
    measured, candidates, preview, warnings = _measure(
        service, src, _ephemeral_service, work_dir)

    root.mkdir(parents=True, exist_ok=True)
    (root / IMPORTS_DIR).mkdir(exist_ok=True)
    (root / "docs").mkdir(exist_ok=True)
    (root / "previews").mkdir(exist_ok=True)
    shutil.copyfile(src, root / IMPORTS_DIR / stem)
    if preview is not None:
        (root / "previews" / f"{part}_iso.png").write_bytes(preview)
    else:
        warnings.append(
            "no preview could be rendered, so previews/ is empty and the gate "
            "will fail its format stage: render one and drop it in")

    doc = {
        "format": pkgformat.PACKAGE_FORMAT,
        "name": name,
        "version": version,
        "summary": summary or f"{vendor} {part_number or part} (vendor STEP)",
        "keywords": list(keywords or ["vendor", "cots"]),
        "standards": list(standards or []),
        "license": license or "vendor-terms",
        "authors": list(authors or [{"name": vendor}]),
        "disclosure": disclosure,
        "provenance": {"vendor": {
            "name": vendor,
            **({"part_number": part_number} if part_number else {}),
            **({"url": url} if url else {}),
            **({"terms": terms} if terms else {}),
            # NOT a label. `LocalIndex.publish` refuses this package into a
            # `public` index — that refusal is FR13's confinement mechanism.
            "redistributable": False,
        }},
        "parts": {part: {
            "kind": "reference",
            "source": f"{IMPORTS_DIR}/{stem}",
            "label": part_number or part,
            "summary": summary or f"{vendor} {part_number or part}",
        }},
    }
    # newline pinned: this tree is CONTENT-HASHED, and Windows text mode would
    # translate \n -> \r\n, giving a scaffold whose content id can never
    # survive a git round trip (PR #15's windows-latest lesson).
    (root / "package.json").write_text(
        json.dumps(doc, indent=2) + "\n", encoding="utf-8", newline="\n")
    (root / "docs" / "README.md").write_text(_README_TEMPLATE.format(
        name=name,
        summary=doc["summary"],
        vendor=vendor,
        part_no=f", part number `{part_number}`" if part_number else "",
        url_line=f"Source: {url}\n" if url else "",
        terms=terms or "not recorded — ask the vendor and fill this in before "
                       "publishing anywhere",
        candidates=_candidate_lines(candidates),
        source=f"{IMPORTS_DIR}/{stem}",
        n_solids=measured.get("n_solids"),
        volume=f"{float(measured.get('volume_mm3') or 0):,.2f}",
        bbox=_round((measured.get("bbox") or {}).get("max")),
    ), encoding="utf-8", newline="\n")

    problems = pkgformat.validate_package_manifest(doc, root=root)
    if problems:            # pragma: no cover — the scaffold builds this doc
        raise ValidationError(
            "the scaffolded package.json does not validate: "
            + "; ".join(p["message"] for p in problems),
            {"problems": problems})

    return {
        "path": str(root),
        "package": {"name": name, "version": version,
                    "content_id": content.content_id(root)},
        "part": part,
        "kind": "reference",
        "candidates": candidates,
        "metrics": measured,
        "warnings": warnings,
        "note": SECURITY_NOTE,
    }


def _measure(service, src: Path, ephemeral, work_dir):
    """Build the imported file once and render it, in a throwaway cell.

    The cell is the gate's containment rule reused: a second, ephemeral
    service rooted under a temp directory with all three seams nulled, sharing
    the caller's warm kernel. Nothing here touches a user project.
    """
    from .gate import GATE_PROJECT

    root = Path(work_dir).expanduser().resolve() if work_dir else \
        Path(tempfile.gettempdir())
    root.mkdir(parents=True, exist_ok=True)
    cell = Path(tempfile.mkdtemp(
        prefix=f"agentcad-from-step-{os.getpid()}-", dir=str(root))).resolve()
    warnings: list[str] = []
    try:
        scratch, registry, _proj = ephemeral(cell, service.kernel)
        dest = scratch.store.imports_dir(GATE_PROJECT, write=True) / _safe_name(src)
        shutil.copyfile(src, dest)
        scratch.store.add_part(GATE_PROJECT, "vendor", "vendor solid",
                               "al6061", "", kind="reference",
                               source=dest.name)
        result = scratch._rebuild(GATE_PROJECT, "vendor")
        if not result.get("ok"):
            payload = result.get("error") or {}
            raise ValidationError(
                f"{src.name} does not load in this kernel, so it cannot be "
                f"packaged: {payload.get('message') or 'the build failed'}",
                {"source": str(src), "error": payload})
        metrics = dict(result.get("metrics") or {})
        warnings += [f"{src.name}: {w}" for w in result.get("warnings") or []]
        if metrics.get("is_valid") is False:
            # PRD-004's rule, and the gate's: reported, never enforced. OCCT
            # calls the shipped 180-solid rocketry STEP invalid.
            warnings.append(
                f"{src.name}: the imported geometry reports is_valid=false on "
                f"the whole shape ({metrics.get('n_solids')} solids); validity "
                f"is reported for imported parts, never enforced")
        candidates = _candidates(scratch, dest, warnings)
        preview = _preview(registry, GATE_PROJECT, "vendor", warnings)
        return metrics, candidates, preview, warnings
    finally:
        shutil.rmtree(cell, ignore_errors=True)


def _candidates(scratch, source_path: Path, warnings: list[str]) -> dict:
    """The imported solid's own B-rep faces, largest first.

    Through the kernel's ``reference_faces`` handler, because ``face_info``
    takes a *script* and a reference part has none — and because the
    mesh-derived alternative (PRD-008's ``anchors.signature_table``) is empty
    for a reference part: the reference build path writes no ``.faces.u32``
    sidecar, and an area-weighted normal over a closed cylinder nearly cancels
    so a cylinder's axis is not in it anyway.
    """
    try:
        return scratch.kernel.request("reference_faces",
                                      {"source_path": str(source_path)})
    except Exception as exc:  # noqa: BLE001 — suggestions never fail a scaffold
        warnings.append(f"no face candidates could be read: {exc}")
        return {"n_faces": None, "kinds": {}, "faces": [], "truncated": False}


def _preview(registry, project: str, part_id: str, warnings: list[str]):
    import base64

    try:
        result = registry.call("render_view", {
            "project": project, "part_id": part_id, "view": "iso",
            "width": PREVIEW_SIZE[0], "height": PREVIEW_SIZE[1]})
    except Exception as exc:  # noqa: BLE001 — a missing preview is a warning
        warnings.append(f"no preview could be rendered: {exc}")
        return None
    encoded = result.get("png_base64")
    if isinstance(encoded, str):
        return base64.b64decode(encoded)
    path = result.get("path")
    try:
        return Path(path).read_bytes()
    except (OSError, TypeError):        # pragma: no cover — defensive
        warnings.append("the renderer returned no readable PNG")
        return None


# --------------------------------------------------------------- validation


def _accept(value, regex, what: str) -> str:
    if not isinstance(value, str) or not regex.match(value):
        raise ValidationError(
            f"{value!r} is not a {what}: it must match {regex.pattern}")
    return value


def _safe_name(src: Path) -> str:
    """The basename, lower-cased extension. A basename cannot traverse — the
    same security boundary `imports.safe_import_name` draws, and the same
    reason."""
    return Path(src.name).stem + Path(src.name).suffix.lower()


def _accept_source(source) -> Path:
    src = Path(str(source)).expanduser()
    if not src.is_absolute():
        raise ValidationError(
            f"source must be an absolute path to the vendor file, got "
            f"{source!r}")
    src = src.resolve()
    if not src.is_file():
        raise NotFoundError(f"no file at {src}", {"source": str(src)})
    ext = src.suffix.lower()
    if ext in MESH_EXTS:
        raise ValidationError(
            f"{src.name} is a mesh (STL). A mesh imports into a project but it "
            f"cannot be packaged: it loads as ONE welded triangulation face "
            f"with no surface, so it has no planar or cylindrical faces to "
            f"suggest connectors from, and booleans on it segfault OCCT — a "
            f"consumer could measure and display it and do nothing else. "
            f"Package a STEP or a BREP.",
            {"source": str(src), "supported": list(SUPPORTED_EXTS)})
    if ext not in SUPPORTED_EXTS:
        raise ValidationError(
            f"unsupported vendor format {ext!r}; package_from_step wraps "
            f"{', '.join(SUPPORTED_EXTS)}",
            {"source": str(src), "supported": list(SUPPORTED_EXTS)})
    size = src.stat().st_size
    if size > content.MAX_FILE_BYTES:
        # The published per-file ceiling, refused HERE with the number rather
        # than three steps later inside the gate's inventory check. It is not
        # raised for this case: a ceiling is part of the format, every consumer
        # enforces it on install, and a version published above it would be
        # uninstallable by a client that pinned the old one.
        raise ValidationError(
            f"{src.name} is {size:,} bytes, above the published per-file "
            f"ceiling of {content.MAX_FILE_BYTES:,}. A package is a directory "
            f"whose files every consumer re-hashes on every materialisation, "
            f"and the ceiling is what bounds that cost — simplify or split the "
            f"vendor model, or keep it as a project import rather than a "
            f"package.",
            {"source": str(src), "bytes": size,
             "max_bytes": content.MAX_FILE_BYTES})
    return src


def _accept_dest(dest) -> Path:
    root = Path(str(dest)).expanduser().resolve()
    if root.exists() and any(root.iterdir()):
        raise ConflictError(
            f"{root} already exists and is not empty: scaffolding into it "
            f"would overwrite files somebody wrote. Pass a new directory — a "
            f"package version is immutable, so a fix is a new version in a new "
            f"directory.",
            {"path": str(root)})
    return root
