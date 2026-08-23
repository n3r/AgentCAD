"""Worker handler pack for structured (assembly-aware) CAD import — PRD-017 FR8.

Two handlers over one XCAF walk:

* ``inspect_cad_tree {source_path}`` — read-only. Returns the file's unique
  products, its **flattened** occurrence list (composed transforms, FR10 v1),
  the nested tree (informational) and counts.
* ``import_structured {source_path, out_dir}`` — the same walk, plus one
  ``.brep`` per unique product written into ``out_dir`` so each product can
  land as a plain reference part through the existing, tested ``refload``
  pipeline (no new cache-key axis, exact B-rep preserved).

The walk is the spike's (``docs/superpowers/specs/2026-08-23-interop-pack-spike.md``
section C) verbatim, including its seven pitfalls:

1. **Referred label, not the component label.** ``IsAssembly_s`` /
   ``IsSimpleShape_s`` must be asked of the *referred* label — a component
   label answers ``False`` to both. The component label carries the instance
   name and the occurrence colour override; the referred label carries the
   product name, its geometry and the product colour.
2. **``XCAFDoc_ColorTool.GetColor`` is bound only for ``TopoDS_Shape`` in
   OCP**; the label overloads are the *static* ``GetColor_s(label, type,
   color)``. The instance method raises ``TypeError`` on a ``TDF_Label``.
3. **Colour precedence is ``ColorSurf → ColorGen → ColorCurv``**, asked at
   each of (component, referred). OCCT writes surface colours as
   ``ColorSurf``; files authored elsewhere may only set ``ColorGen``.
4. **``Quantity_Color.Red()/Green()/Blue()`` return LINEAR values.**
   ``Values(Quantity_TOC_sRGB)`` is the only correct read — storing ``.Red()``
   would silently darken every imported colour.
5. **Instance identity is the component-label PATH, not the leaf label.** One
   product's label is shared by all of its occurrences (``pin_1`` under two
   different sub-assemblies is the same label), so occurrence names are
   de-duplicated by parent prefix and then by deterministic numbering.
6. **``xstep.cascade.unit`` is process-global** and already defaults to ``MM``
   (the reader converts an inch-authored file for us). It is never touched.
7. The reader flag is ``SetGDTMode`` — there is no ``SetDimTolMode`` on the
   reader (that one is on the *writer*).

Rotations come out in this repo's house convention — intrinsic XYZ Euler
degrees — via ``gp_Quaternion.GetEulerAngles(gp_Intrinsic_XYZ)``, which is
byte-for-byte what ``build123d.Location.orientation`` does, so a pose read
here re-places exactly through ``b3d.Location(position, rotation_deg)``.
"""

from __future__ import annotations

import math
import os
import re
from pathlib import Path

from OCP.BRepTools import BRepTools
from OCP.gp import gp_EulerSequence
from OCP.IFSelect import IFSelect_ReturnStatus
from OCP.Quantity import Quantity_Color, Quantity_TOC_sRGB
from OCP.STEPCAFControl import STEPCAFControl_Reader
from OCP.TCollection import TCollection_AsciiString, TCollection_ExtendedString
from OCP.TDataStd import TDataStd_Name
from OCP.TDF import TDF_Label, TDF_LabelSequence, TDF_Tool
from OCP.TDocStd import TDocStd_Document
from OCP.TopLoc import TopLoc_Location
from OCP.XCAFApp import XCAFApp_Application
from OCP.XCAFDoc import (
    XCAFDoc_ColorCurv,
    XCAFDoc_ColorGen,
    XCAFDoc_ColorSurf,
    XCAFDoc_ColorTool,
    XCAFDoc_DocumentTool,
    XCAFDoc_ShapeTool,
)

# Only STEP carries a product structure. BREP is a single shape and STL is a
# mesh; both are the flat import path's business (the server picks).
STRUCTURED_EXTS = {".step", ".stp"}

_COLOR_TYPES = (XCAFDoc_ColorSurf, XCAFDoc_ColorGen, XCAFDoc_ColorCurv)


# ----------------------------------------------------------------- XCAF utils


def _new_doc() -> TDocStd_Document:
    app = XCAFApp_Application.GetApplication_s()
    doc = TDocStd_Document(TCollection_ExtendedString("XmlXCAF"))
    app.InitDocument(doc)
    return doc


def _label_name(label: TDF_Label) -> str | None:
    attr = TDataStd_Name()
    if not label.IsNull() and label.FindAttribute(TDataStd_Name.GetID_s(), attr):
        text = attr.Get().ToExtString()
        return text or None
    return None


def _entry(label: TDF_Label) -> str:
    text = TCollection_AsciiString()
    TDF_Tool.Entry_s(label, text)
    return text.ToCString()


def _color_of(label: TDF_Label) -> str | None:
    """sRGB hex of a label's own colour, ColorSurf → ColorGen → ColorCurv.

    Pitfalls 2/3/4: the static overload, the precedence, and
    ``Values(Quantity_TOC_sRGB)`` rather than the linear ``.Red()``.
    """
    if label.IsNull():
        return None
    color = Quantity_Color()
    for color_type in _COLOR_TYPES:
        if XCAFDoc_ColorTool.GetColor_s(label, color_type, color):
            r, g, b = color.Values(Quantity_TOC_sRGB)
            return "#%02x%02x%02x" % tuple(
                max(0, min(255, int(round(c * 255.0)))) for c in (r, g, b)
            )
    return None


def _clean(value: float) -> float:
    """Round away float noise and normalize ``-0.0`` to ``0.0``."""
    return round(float(value), 9) + 0.0


def _pose(loc: TopLoc_Location) -> tuple[list[float], list[float]]:
    """Composed placement → (position, intrinsic-XYZ Euler degrees)."""
    trsf = loc.Transformation()
    translation = trsf.TranslationPart()
    angles = trsf.GetRotation().GetEulerAngles(gp_EulerSequence.gp_Intrinsic_XYZ)
    return (
        [_clean(translation.X()), _clean(translation.Y()), _clean(translation.Z())],
        [_clean(math.degrees(a)) for a in angles],
    )


def _sanitize(name: str | None) -> str:
    """Filename-safe fragment: lowercase ``[a-z0-9_]`` only."""
    slug = re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_")
    return slug or "product"


# ---------------------------------------------------------------- the walk


class _Walk:
    """Accumulates products / occurrences / tree over one XCAF document."""

    def __init__(self) -> None:
        # The XCAF document is pinned here for the walk's whole lifetime: a
        # product's TopoDS_Shape is refcounted independently, but relying on
        # that while `import_structured` writes .brep files *after* the read
        # frame has returned is a lifetime argument nobody should have to make.
        self.doc: TDocStd_Document | None = None
        self.products: list[dict] = []
        self.shapes: list[object] = []
        self._by_entry: dict[str, dict] = {}
        self._per_product_count: dict[int, int] = {}
        self.occurrences: list[dict] = []
        self.tree: list[dict] = []
        self.unnamed_products = 0
        self.unnamed_occurrences = 0
        self.skipped = 0

    def _product(self, label: TDF_Label) -> dict:
        key = _entry(label)
        product = self._by_entry.get(key)
        if product is None:
            name = _label_name(label)
            if not name:
                self.unnamed_products += 1
                name = f"product_{len(self.products) + 1}"
            product = {"index": len(self.products), "name": name,
                       "color": _color_of(label)}
            self._by_entry[key] = product
            self.products.append(product)
            self.shapes.append(XCAFDoc_ShapeTool.GetShape_s(label))
        return product

    def visit(self, label: TDF_Label, chain: TopLoc_Location,
              parents: list[str], root: bool = False) -> dict | None:
        referred = TDF_Label()
        is_ref = XCAFDoc_ShapeTool.GetReferredShape_s(label, referred)
        # PITFALL 1: ask the REFERRED label what it is.
        target = referred if is_ref else label
        instance_name = _label_name(label)

        if XCAFDoc_ShapeTool.IsAssembly_s(target):
            own = instance_name or _label_name(target) or "assembly"
            node = {"name": own, "children": []}
            # The root free shape's own name is not part of an occurrence path
            # (it would prefix every single instance in the file).
            child_parents = [] if root else [*parents, own]
            components = TDF_LabelSequence()
            XCAFDoc_ShapeTool.GetComponents_s(target, components)
            for i in range(1, components.Length() + 1):
                component = components.Value(i)
                child = self.visit(
                    component,
                    chain * XCAFDoc_ShapeTool.GetLocation_s(component),
                    child_parents,
                )
                if child is not None:
                    node["children"].append(child)
            return node

        if not XCAFDoc_ShapeTool.IsSimpleShape_s(target):
            self.skipped += 1
            return None

        product = self._product(target)
        seq = self._per_product_count.get(product["index"], 0) + 1
        self._per_product_count[product["index"]] = seq
        name = instance_name
        if not name:
            self.unnamed_occurrences += 1
            name = f"{product['name']}_{seq}"
        position, rotation = _pose(chain)
        # PITFALL 3 (occurrence half): the component label's colour overrides
        # the product colour; fall back to the referred label's.
        color = _color_of(label) or product["color"]
        self.occurrences.append({
            "product_index": product["index"],
            "name": name,
            "path": [*parents, name],
            "position": position,
            "rotation_deg": rotation,
            "color": color,
        })
        return {"name": name, "product_index": product["index"], "children": []}

    def resolve_names(self) -> int:
        """PITFALL 5: two occurrences of one product under different parents
        share a label and therefore a name. Prefix by path, then number."""
        renamed = 0
        counts: dict[str, int] = {}
        for occ in self.occurrences:
            counts[occ["name"]] = counts.get(occ["name"], 0) + 1
        for occ in self.occurrences:
            if counts[occ["name"]] > 1:
                occ["name"] = "_".join(occ["path"])
                renamed += 1
        used: dict[str, int] = {}
        for occ in self.occurrences:
            seen = used.get(occ["name"], 0) + 1
            used[occ["name"]] = seen
            if seen > 1:
                occ["name"] = f"{occ['name']}_{seen}"
                renamed += 1
        return renamed


def register(toolbox: dict) -> dict:
    WorkerError = toolbox["WorkerError"]
    ERROR_CONTRACT = toolbox["ERROR_CONTRACT"]

    def _refuse(message: str) -> "Exception":
        return WorkerError(ERROR_CONTRACT, message)

    def _read(source_path: str) -> tuple[TDocStd_Document, TDF_LabelSequence]:
        path = Path(source_path)
        ext = path.suffix.lower()
        if ext not in STRUCTURED_EXTS:
            raise _refuse(
                f"structured import needs a STEP file (got {ext!r}); "
                f"supported: {', '.join(sorted(STRUCTURED_EXTS))}"
            )
        if not path.is_file():
            raise _refuse(f"source file not found: {source_path}")

        reader = STEPCAFControl_Reader()
        reader.SetColorMode(True)
        reader.SetNameMode(True)
        reader.SetLayerMode(True)
        reader.SetGDTMode(True)  # PITFALL 7 — the reader has no SetDimTolMode
        reader.SetMatMode(True)
        reader.SetViewMode(True)
        # PITFALL 6: xstep.cascade.unit stays at its process-global MM default.
        try:
            status = reader.ReadFile(str(path))
        except Exception as exc:  # noqa: BLE001 — OCCT throws many types
            raise _refuse(f"could not read STEP file {path.name}: {exc}") from exc
        # IFSelect_RetDone is the only success value; a garbage file returns
        # RetVoid/RetError here rather than raising.
        if status != IFSelect_ReturnStatus.IFSelect_RetDone:
            raise _refuse(
                f"could not read STEP file {path.name}: reader returned "
                f"{str(status).split('.')[-1]}"
            )
        doc = _new_doc()
        try:
            transferred = reader.Transfer(doc)
        except Exception as exc:  # noqa: BLE001
            raise _refuse(
                f"could not transfer STEP file {path.name}: {exc}") from exc
        if not transferred:
            raise _refuse(f"STEP file {path.name} transferred no shapes")
        shape_tool = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
        free = TDF_LabelSequence()
        shape_tool.GetFreeShapes(free)
        if free.Length() == 0:
            raise _refuse(f"STEP file {path.name} contains no shapes")
        return doc, free

    def _inspect(source_path: str) -> tuple[_Walk, list[str]]:
        doc, free = _read(source_path)
        walk = _Walk()
        walk.doc = doc
        for i in range(1, free.Length() + 1):
            node = walk.visit(free.Value(i), TopLoc_Location(), [], root=True)
            if node is not None:
                walk.tree.append(node)
        renamed = walk.resolve_names()

        warnings: list[str] = []
        if walk.unnamed_products:
            warnings.append(
                f"{walk.unnamed_products} product(s) had no name in the file; "
                "generated names were used")
        if walk.unnamed_occurrences:
            warnings.append(
                f"{walk.unnamed_occurrences} occurrence(s) had no name in the "
                "file; named after their product")
        if renamed:
            warnings.append(
                f"{renamed} occurrence name(s) were qualified to stay unique "
                "(the same product occurs under more than one parent)")
        if walk.skipped:
            warnings.append(
                f"{walk.skipped} label(s) carried neither an assembly nor a "
                "shape and were skipped")
        if not walk.products:
            raise _refuse(f"no products found in {Path(source_path).name}")
        return walk, warnings

    def _payload(walk: _Walk, warnings: list[str]) -> dict:
        return {
            "products": walk.products,
            "occurrences": walk.occurrences,
            "tree": walk.tree,
            "counts": {"products": len(walk.products),
                       "occurrences": len(walk.occurrences)},
            "warnings": warnings,
        }

    def inspect_cad_tree(params: dict) -> dict:
        walk, warnings = _inspect(params["source_path"])
        return _payload(walk, warnings)

    def import_structured(params: dict) -> dict:
        source_path = params["source_path"]
        out_dir = params.get("out_dir")
        if not out_dir:
            raise _refuse("import_structured needs an 'out_dir'")
        walk, warnings = _inspect(source_path)

        directory = Path(out_dir)
        directory.mkdir(parents=True, exist_ok=True)
        stem = _sanitize(Path(source_path).stem)
        for product, shape in zip(walk.products, walk.shapes):
            name = f"{stem}__{product['index']}_{_sanitize(product['name'])}.brep"
            target = directory / name
            # Same tmp+os.replace shape as the toolbox's atomic_write; BRepTools
            # writes to a path itself, so the bytes never round-trip memory.
            tmp = target.with_name(target.name + ".tmp")
            try:
                BRepTools.Write_s(shape, str(tmp))
            except Exception as exc:  # noqa: BLE001
                raise _refuse(
                    f"could not write product {product['name']!r}: {exc}") from exc
            os.replace(tmp, target)
            product["file"] = name  # basename only — never an absolute path
        return _payload(walk, warnings)

    return {
        "inspect_cad_tree": inspect_cad_tree,
        "import_structured": import_structured,
    }
