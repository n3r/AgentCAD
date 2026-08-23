"""Worker handler pack: interop exports (PRD-017).

``export_step_pmi`` writes a STEP AP242 file carrying the part's PMI (our
``core/pmi.py`` model mapped to XCAF by ``_pmi_map``, which owns the six traps
the de-risking spike found). ``read_step_pmi`` reads PMI back out — the
round-trip half of the export's contract and what the test suite asserts on:
PMI entry identity does not survive the writer (labels are overwritten by the
STEP type keyword), so entries are matched by (type, value, tolerance, target)
and datums by *name*, never by label count (a two-datum FCF reads back as three
datum labels).

``export_3mf_rich`` (slice 5, FR4–FR5) writes 3MF with per-solid names and
colours and model metadata. The one trap it owns is the spike's D.1:
``Mesher.add_shape(Part)`` **silently drops both** ``.label`` and ``.color``
(``Mesher`` reads them only off a ``Solid``), so today's plain 3MF has no names
and no colours even when the script set them. This handler decomposes to
``shape.solids()`` and stamps each solid before it is added. ``CreationDate``
is whatever the server passed — the resolved *version* date, never a wall
clock, because a 3MF is already non-deterministic enough (lib3mf mints a fresh
``p:UUID`` per object per write; spike D.2).

``export_step_structured`` (slice 5, FR2) writes a real STEP **assembly**: one
XCAF product per unique part, one component per instance, names on both,
colours through ``XCAFDoc_ColorTool``, AP242. It is the mirror image of
``interop_import``'s walk, and it inherits two traps of its own beyond
``_pmi_map``'s six:

* a product's shape must be a **``TopoDS_Solid`` when it has exactly one
  solid** — added as a single-solid ``TopoDS_Compound`` the product colour
  still survives, but every *per-occurrence* colour override is dropped by the
  writer (measured: 2 styled items, the override absent from the file);
* a genuinely multi-solid product keeps its compound, and then its colour is
  written **per solid** — which our own ``inspect_cad_tree`` reports as
  ``color: None`` on the product, because on re-read those styles land on
  sub-shape labels. Per-occurrence overrides on a multi-solid product are lost.
  Recorded here rather than worked around: the fix is a product per (part,
  colour) pair, which would inflate the product count the round trip asserts.

Parts without PMI keep the plain ``export`` path — this pack adds methods, it
replaces none.
"""

from __future__ import annotations

import contextlib
import os
import sys
from pathlib import Path

from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepGProp import BRepGProp
from OCP.GeomAbs import GeomAbs_SurfaceType
from OCP.GProp import GProp_GProps
from OCP.Interface import Interface_Static
from OCP.Quantity import Quantity_Color, Quantity_TOC_sRGB
from OCP.STEPCAFControl import STEPCAFControl_Reader, STEPCAFControl_Writer
from OCP.STEPControl import STEPControl_StepModelType
from OCP.TCollection import TCollection_ExtendedString
from OCP.TDataStd import TDataStd_Name
from OCP.TDF import TDF_LabelSequence
from OCP.TopAbs import TopAbs_FACE, TopAbs_SOLID
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS
from OCP.XCAFDoc import (XCAFDoc_ColorSurf, XCAFDoc_Datum, XCAFDoc_Dimension,
                         XCAFDoc_DimTolTool, XCAFDoc_DocumentTool,
                         XCAFDoc_GeomTolerance, XCAFDoc_ShapeTool)

from . import _pmi_map

#: 3MF core metadata this handler knows how to stamp: our key → the 3MF name.
#: ``part_number`` is not a core-spec name, so it rides a custom namespace (and
#: the per-object ``partnumber=`` attribute, which IS core).
METADATA_NAMES = {
    "title": "Title",
    "designer": "Designer",
    "description": "Description",
    "creation_date": "CreationDate",
    "part_number": "PartNumber",
}

#: Namespace for the one metadata entry the 3MF core spec has no name for.
METADATA_NS = "http://agentcad.dev/2026/03/metadata"


def _type_name(enum, prefix: str) -> str:
    """``XCAFDimTolObjects_GeomToleranceType_Flatness`` → ``flatness``; the
    lowercase suffix matches ``core/pmi.py``'s own type names for FCFs."""
    text = str(enum).split(".")[-1]
    _, _, suffix = text.partition(prefix)
    return (suffix or text).lower()


def _describe_face(shape) -> dict | None:
    if shape is None or shape.IsNull() or shape.ShapeType() != TopAbs_FACE:
        return None
    face = TopoDS.Face_s(shape)
    adaptor = BRepAdaptor_Surface(face)
    props = GProp_GProps()
    BRepGProp.SurfaceProperties_s(face, props)
    center = props.CentreOfMass()
    surface_type = adaptor.GetType()
    out = {
        "kind": "other",
        "area": float(props.Mass()),
        "center": [center.X(), center.Y(), center.Z()],
    }
    if surface_type == GeomAbs_SurfaceType.GeomAbs_Plane:
        out["kind"] = "plane"
    elif surface_type == GeomAbs_SurfaceType.GeomAbs_Cylinder:
        out["kind"] = "cylinder"
        out["diameter"] = 2.0 * float(adaptor.Cylinder().Radius())
    return out


def _targets_of(label) -> list[dict]:
    """The sub-shapes a dimension/tolerance label is attached to, described
    geometrically — the only stable identity a PMI entry has after a round
    trip."""
    first, second = TDF_LabelSequence(), TDF_LabelSequence()
    if not XCAFDoc_DimTolTool.GetRefShapeLabel_s(label, first, second):
        return []
    out = []
    for sequence in (first, second):
        for i in range(1, sequence.Length() + 1):
            described = _describe_face(
                XCAFDoc_ShapeTool.GetShape_s(sequence.Value(i)))
            if described is not None:
                out.append(described)
    return out


def _datum_names(labels) -> list[str]:
    names = []
    for i in range(1, labels.Length() + 1):
        obj = XCAFDoc_Datum.Set_s(labels.Value(i)).GetObject()
        name = obj.GetName() if obj is not None else None
        if name is not None:
            names.append(name.ToCString())
    return names


# ------------------------------------------------------------------ colours


def _rgb01(color) -> tuple[float, float, float] | None:
    """``"#rrggbb"``/``"#rgb"`` → sRGB floats in [0, 1]; anything else None.

    Never raises: ``core/interop_colors.normalize_hex`` already refuses
    malformed colours server-side, and a colour is not worth failing an export
    that has already built the geometry.
    """
    if not isinstance(color, str):
        return None
    text = color.strip().lower()
    if not text.startswith("#"):
        return None
    body = text[1:]
    if len(body) == 3:
        body = "".join(c * 2 for c in body)
    if len(body) != 6:
        return None
    try:
        return tuple(int(body[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    except ValueError:
        return None


def _quantity_color(color: str) -> Quantity_Color | None:
    """An **sRGB** ``Quantity_Color``. The ``Quantity_TOC_sRGB`` argument is
    the mirror of the importer's ``Values(Quantity_TOC_sRGB)`` read: with the
    default (linear) type every exported colour would be brightened by exactly
    the factor the import-side trap darkens it."""
    rgb = _rgb01(color)
    if rgb is None:
        return None
    return Quantity_Color(rgb[0], rgb[1], rgb[2], Quantity_TOC_sRGB)


# ------------------------------------------------------------------- shapes


def _ocp_solids(ocp_shape) -> list:
    """Every ``TopoDS_Solid`` of a raw OCP shape, in explorer order."""
    solids = []
    explorer = TopExp_Explorer(ocp_shape, TopAbs_SOLID)
    while explorer.More():
        solids.append(TopoDS.Solid_s(explorer.Current()))
        explorer.Next()
    return solids


def _product_shape(shape):
    """The OCP shape to register as one XCAF product.

    De-located (``_pmi_map``'s trap 1: a located shape yields a reference label
    with null sub-shape labels) and, when the part is a single solid wrapped in
    a compound — which every build123d ``Part`` is — **unwrapped to that
    solid**. The unwrap is load-bearing: with a single-solid compound product,
    OCCT's STEP writer drops every per-occurrence colour override (measured on
    this build), while the same document with a ``TopoDS_Solid`` product
    round-trips the override intact.
    """
    ocp = _pmi_map.delocated(shape.wrapped)
    if ocp.ShapeType() == TopAbs_SOLID:
        return ocp
    solids = _ocp_solids(ocp)
    if len(solids) == 1:
        return _pmi_map.delocated(solids[0])
    return ocp


def _write_assembly_ap242(doc, path: str) -> None:
    """Write an XCAF **assembly** document as AP242.

    ``_pmi_map.write_ap242``'s trap 2 (construct the writer, THEN set
    ``write.step.schema``, and assert the setter) applies verbatim and is
    repeated here rather than shared, because this writer also needs
    ``write.step.assembly``: both statics are process-wide, only exist once a
    writer has been constructed, and are restored in ``finally`` so a later
    ``b3d.export_step`` in this worker is unaffected.
    """
    with contextlib.redirect_stdout(sys.stderr):  # OCCT prints transfer stats
        writer = STEPCAFControl_Writer()
        previous_schema = Interface_Static.CVal_s("write.step.schema")
        previous_assembly = Interface_Static.IVal_s("write.step.assembly")
        if not Interface_Static.SetCVal_s("write.step.schema", "AP242DIS"):
            raise RuntimeError(
                "Interface_Static.SetCVal_s('write.step.schema', 'AP242DIS') "
                "returned False — the file would be AP214")
        if not Interface_Static.SetIVal_s("write.step.assembly", 1):
            raise RuntimeError(
                "Interface_Static.SetIVal_s('write.step.assembly', 1) returned "
                "False — the product structure would be flattened")
        try:
            writer.SetColorMode(True)
            writer.SetNameMode(True)
            writer.SetLayerMode(True)
            if not writer.Transfer(doc,
                                   STEPControl_StepModelType.STEPControl_AsIs):
                raise RuntimeError("STEPCAFControl_Writer.Transfer failed")
            status = writer.Write(str(path))
        finally:
            Interface_Static.SetCVal_s("write.step.schema",
                                       previous_schema or "AP214IS")
            Interface_Static.SetIVal_s("write.step.assembly",
                                       previous_assembly)
    if str(status) != "IFSelect_ReturnStatus.IFSelect_RetDone":
        raise RuntimeError(f"STEP write failed: {status}")


def register(toolbox: dict) -> dict:
    build_shape = toolbox["build_shape"]
    build_shape_ns = toolbox["build_shape_ns"]
    WorkerError = toolbox["WorkerError"]
    ERROR_CONTRACT = toolbox["ERROR_CONTRACT"]
    ERROR_KERNEL = toolbox["ERROR_KERNEL"]

    def _shape_for(params: dict, what: str = "export_step_pmi",
                   brep_only: bool = True):
        """Script part or reference part, the same two sources
        ``worker._item_shape`` resolves.

        ``brep_only`` is the PMI/STEP rule (a welded STL mesh carries no
        surfaces to hang a datum — or a product — on); 3MF is a mesh format and
        takes either.
        """
        source = params.get("source_path")
        if source:
            from ..refload import load_reference

            shape, kind = load_reference(source)
            if kind == "mesh" and brep_only:
                raise WorkerError(
                    ERROR_CONTRACT,
                    "STEP+PMI export needs a B-rep; this reference part is "
                    "mesh-only (STL)",
                )
            return shape
        if not params.get("script"):
            raise WorkerError(
                ERROR_CONTRACT, f"{what} needs 'script' or 'source_path'")
        shape, _values, _warnings = build_shape(
            params["script"], params.get("params", {}))
        return shape

    def export_step_pmi(params: dict) -> dict:
        shape = _shape_for(params)
        pmi = params.get("pmi") or {}
        target = Path(params["out_path"])
        target.parent.mkdir(parents=True, exist_ok=True)
        # Same atomic shape as worker._export_shape: a suffixed temp file (the
        # writer sniffs the extension) then os.replace, so a killed export
        # never leaves a torn — or worse, an AP214 — file at the target path.
        tmp = target.with_name(f".{target.stem}.tmp{target.suffix}")
        try:
            doc = _pmi_map.new_document()
            mapped = _pmi_map.map_pmi(doc, shape, pmi, name=params.get("name"))
            _pmi_map.write_ap242(doc, str(tmp))
            schema = _pmi_map.assert_ap242_header(str(tmp))
        except _pmi_map.PmiRefusal as refusal:
            tmp.unlink(missing_ok=True)
            raise WorkerError(ERROR_CONTRACT, refusal.reason) from refusal
        except Exception:
            tmp.unlink(missing_ok=True)
            raise
        os.replace(tmp, target)
        return {
            "path": str(target),
            "size_bytes": target.stat().st_size,
            "schema": schema,
            "pmi_attached": mapped["attached"],
            "pmi_skipped": mapped["skipped"],
            "pmi_notes": mapped["notes"],
        }

    # ------------------------------------------------------------- 3MF v2

    def _labelled_solids(shape, labels=None, name: str | None = None) -> list:
        """``[(label, solid)]`` — the SAME naming ``_metrics`` uses, so a
        ``solid_colors`` key that matched ``solid_materials`` still matches
        here: the script's ``SOLID_LABELS`` by index, else the solid's own
        build123d label, else ``solid_<i>`` (0-based, like ``get_metrics``).
        A single-solid part with nothing else to go on takes *name* (the part's
        label), because ``solid_0`` is a poor thing to read in a slicer.

        A mesh-only reference part has no solids; it is added whole, as one
        object, rather than refused — 3MF is a mesh format.
        """
        solids = shape.solids()
        if not solids:
            return [(name or "shape_0", shape)]
        out = []
        for i, solid in enumerate(solids):
            label = None
            if labels and i < len(labels):
                label = labels[i]
            label = label or getattr(solid, "label", "")
            if not label and len(solids) == 1 and name:
                label = name
            out.append((label or f"solid_{i}", solid))
        return out

    def _script_solid_labels(params: dict) -> list | None:
        """``SOLID_LABELS`` read from the part's own script — the optional
        contract addition ``worker._solid_labels`` reads for ``get_metrics``.

        The server cannot supply these (they live in the script, not the
        manifest), and without them a ``solid_colors`` map keyed the way
        ``set_solid_materials`` documents would silently miss every label.
        Advisory, exactly as in the metrics path: a malformed value is ignored
        rather than failing an export.
        """
        if params.get("source_path") or not params.get("script"):
            return None
        _shape, _values, _warnings, ns = build_shape_ns(
            params["script"], params.get("params", {}))
        labels = ns.get("SOLID_LABELS")
        if isinstance(labels, list) and all(isinstance(x, str) for x in labels):
            return labels
        return None

    def _solid_color(index: int, label: str, solid_colors: dict,
                     default_color) -> str | None:
        """Label match > index match > the part-wide default > no colour.

        The precedence ``set_solid_materials`` documents, one level deeper: a
        part with neither per-solid colours nor a default is written with no
        ``<basematerials>`` at all (``colors: "none"``), which is what a
        slicer's own default expects.
        """
        if label in solid_colors:
            return solid_colors[label]
        if str(index) in solid_colors:
            return solid_colors[str(index)]
        return default_color

    def _mesher_objects(mesher, entries, tolerance, part_number, b3d):
        """Stamp ``.label``/``.color`` on each solid BEFORE ``add_shape``.

        Spike D.1, the whole reason this handler exists: ``Mesher._add_color``
        and the name setter read those attributes only when the argument is a
        ``Solid`` — ``add_shape(Part)`` explodes the compound itself and
        silently writes an object with neither a ``name=`` nor a
        ``<basematerials>`` group.
        """
        coloured = 0
        for name, solid, color in entries:
            solid.label = name
            rgb = _rgb01(color)
            if rgb is not None:
                solid.color = b3d.Color(*rgb)
                coloured += 1
            if part_number:
                mesher.add_shape(solid, linear_deflection=tolerance,
                                 part_number=part_number)
            else:
                mesher.add_shape(solid, linear_deflection=tolerance)
        return coloured

    def export_3mf_rich(params: dict) -> dict:
        b3d = toolbox["b3d"]
        place = toolbox["place"]
        target = Path(params["out_path"])
        tolerance = float(params.get("tolerance", 0.05))
        metadata = params.get("metadata") or {}
        if not isinstance(metadata, dict):
            raise WorkerError(ERROR_CONTRACT, "metadata must be an object")
        unknown = sorted(set(metadata) - set(METADATA_NAMES))
        if unknown:
            raise WorkerError(
                ERROR_CONTRACT,
                f"unknown metadata key(s) {', '.join(unknown)}; known: "
                + ", ".join(METADATA_NAMES))
        solid_colors = params.get("solid_colors") or {}
        if not isinstance(solid_colors, dict):
            raise WorkerError(ERROR_CONTRACT, "solid_colors must be an object")
        default_color = params.get("default_color")

        # Two modes, one writer: a PART is its own solids; an ASSEMBLY is one
        # object per instance (placed, named by instance id, coloured by the
        # instance's own colour), which is what `export_assembly {format:
        # "3mf"}` sends.
        entries: list[tuple] = []
        items = params.get("items")
        if items is not None:
            if not isinstance(items, list) or not items:
                raise WorkerError(
                    ERROR_CONTRACT, "export_3mf_rich 'items' must be a "
                                    "non-empty list")
            for item in items:
                shape = _shape_for(item, "export_3mf_rich", brep_only=False)
                shape = place(shape, item.get("position") or [0, 0, 0],
                              item.get("rotation_deg") or [0, 0, 0])
                name = str(item.get("name") or f"instance_{len(entries)}")
                color = item.get("color") or default_color
                solids = _labelled_solids(shape)
                for index, (_label, solid) in enumerate(solids):
                    entries.append((name if len(solids) == 1
                                    else f"{name}_{index}", solid, color))
        else:
            shape = _shape_for(params, "export_3mf_rich", brep_only=False)
            for index, (label, solid) in enumerate(
                    _labelled_solids(shape, _script_solid_labels(params),
                                     params.get("name"))):
                entries.append(
                    (label, solid,
                     _solid_color(index, label, solid_colors, default_color)))

        part_number = metadata.get("part_number")
        mesher = b3d.Mesher(unit=b3d.Unit.MM)
        with contextlib.redirect_stdout(sys.stderr):  # OCCT meshing chatter
            coloured = _mesher_objects(mesher, entries, tolerance, part_number,
                                       b3d)
            # Metadata AFTER the shapes: `add_meta_data` mints a components
            # object to hang the group off when the model has none yet, so
            # stamping first would add a stray resource to every file.
            stamped = []
            for key, name in METADATA_NAMES.items():
                value = metadata.get(key)
                if value is None or value == "":
                    continue
                if not isinstance(value, str):
                    raise WorkerError(
                        ERROR_CONTRACT, f"metadata.{key} must be a string")
                namespace = METADATA_NS if key == "part_number" else ""
                mesher.add_meta_data(namespace, name, value, "xs:string", True)
                stamped.append(name)
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_name(f".{target.stem}.tmp{target.suffix}")
            try:
                mesher.write(str(tmp))
            except BaseException:
                tmp.unlink(missing_ok=True)
                raise
        os.replace(tmp, target)
        return {
            "path": str(target),
            "size_bytes": target.stat().st_size,
            "objects": len(entries),
            "colors": "per_solid" if coloured else "none",
            "metadata_stamped": stamped,
        }

    # ------------------------------------------------- structured STEP (FR2)

    def export_step_structured(params: dict) -> dict:
        b3d = toolbox["b3d"]
        items = params.get("items")
        if not isinstance(items, list) or not items:
            raise WorkerError(
                ERROR_CONTRACT,
                "export_step_structured needs a non-empty 'items' list")
        target = Path(params["out_path"])
        target.parent.mkdir(parents=True, exist_ok=True)

        doc = _pmi_map.new_document()
        shape_tool = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
        color_tool = XCAFDoc_DocumentTool.ColorTool_s(doc.Main())

        products: dict[str, object] = {}
        for item in items:
            key = str(item.get("part_id") or item.get("name") or "")
            if not key:
                raise WorkerError(
                    ERROR_CONTRACT,
                    "every export_step_structured item needs a 'part_id'")
            if key in products:
                continue            # dedup: N occurrences, ONE built shape
            # `brep_only=False`: a mesh-only (STL) reference part lands as the
            # faceted shell it is, exactly as it does in today's fused export.
            # Refusing it here would make `structured: true` reject assemblies
            # the flat path exports fine.
            shape = _shape_for(item, "export_step_structured", brep_only=False)
            label = shape_tool.AddShape(_product_shape(shape), False)
            TDataStd_Name.Set_s(
                label,
                TCollection_ExtendedString(str(item.get("part_name") or key)))
            color = _quantity_color(item.get("part_color") or
                                    item.get("color") or "")
            if color is not None:
                color_tool.SetColor(label, color, XCAFDoc_ColorSurf)
            products[key] = label

        root = shape_tool.NewShape()
        TDataStd_Name.Set_s(
            root, TCollection_ExtendedString(str(params.get("name")
                                                 or "assembly")))
        for item in items:
            key = str(item.get("part_id") or item.get("name"))
            # The house rotation convention end to end: `b3d.Location(pos,
            # rot)` is intrinsic-XYZ degrees, exactly what `worker._place`
            # applies and what the importer reads back out of the composed
            # transform via GetEulerAngles(gp_Intrinsic_XYZ).
            location = b3d.Location(tuple(item.get("position") or [0, 0, 0]),
                                    tuple(item.get("rotation_deg")
                                          or [0, 0, 0])).wrapped
            component = shape_tool.AddComponent(root, products[key], location)
            TDataStd_Name.Set_s(
                component,
                TCollection_ExtendedString(str(item.get("name") or key)))
            color = _quantity_color(item.get("color") or "")
            if color is not None:
                color_tool.SetColor(component, color, XCAFDoc_ColorSurf)
        shape_tool.UpdateAssemblies()

        tmp = target.with_name(f".{target.stem}.tmp{target.suffix}")
        try:
            _write_assembly_ap242(doc, str(tmp))
            schema = _pmi_map.assert_ap242_header(str(tmp))
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        os.replace(tmp, target)
        return {
            "path": str(target),
            "size_bytes": target.stat().st_size,
            "schema": schema,
            "products": len(products),
            "occurrences": len(items),
        }

    def read_step_pmi(params: dict) -> dict:
        path = params["path"]
        if not Path(path).is_file():
            raise WorkerError(ERROR_CONTRACT, f"file not found: {path}")
        doc = _pmi_map.new_document()
        with contextlib.redirect_stdout(sys.stderr):  # OCCT prints on read
            reader = STEPCAFControl_Reader()
            # SetGDTMode is the reader's flag — there is no SetDimTolMode on
            # the reader (that one is the writer's). Without it PMI is skipped.
            reader.SetGDTMode(True)
            reader.SetColorMode(True)
            reader.SetNameMode(True)
            status = reader.ReadFile(str(path))
            if str(status) != "IFSelect_ReturnStatus.IFSelect_RetDone":
                raise WorkerError(ERROR_KERNEL, f"STEP read failed: {status}")
            if not reader.Transfer(doc):
                raise WorkerError(ERROR_KERNEL, f"STEP transfer failed: {path}")
        dimtol_tool = XCAFDoc_DocumentTool.DimTolTool_s(doc.Main())

        dim_labels = TDF_LabelSequence()
        dimtol_tool.GetDimensionLabels(dim_labels)
        dims = []
        for i in range(1, dim_labels.Length() + 1):
            label = dim_labels.Value(i)
            obj = XCAFDoc_Dimension.Set_s(label).GetObject()
            dims.append({
                "type": _type_name(obj.GetType(), "DimensionType_"),
                "value": float(obj.GetValue()),
                "plus": float(obj.GetUpperTolValue()),
                "minus": float(obj.GetLowerTolValue()),
                "targets": _targets_of(label),
            })

        datum_labels = TDF_LabelSequence()
        dimtol_tool.GetDatumLabels(datum_labels)
        # Datum NAMES, deduplicated: a two-datum FCF reads back as three datum
        # labels (one per datum-system compartment), so label counts lie.
        datums = sorted(set(_datum_names(datum_labels)))

        fcf_labels = TDF_LabelSequence()
        dimtol_tool.GetGeomToleranceLabels(fcf_labels)
        fcfs = []
        for i in range(1, fcf_labels.Length() + 1):
            label = fcf_labels.Value(i)
            obj = XCAFDoc_GeomTolerance.Set_s(label).GetObject()
            referenced = TDF_LabelSequence()
            XCAFDoc_DimTolTool.GetDatumOfTolerLabels_s(label, referenced)
            fcfs.append({
                "type": _type_name(obj.GetType(), "GeomToleranceType_"),
                "value": float(obj.GetValue()),
                "zone": _type_name(obj.GetTypeOfValue(),
                                   "GeomToleranceTypeValue_"),
                "datums": sorted(set(_datum_names(referenced))),
                "targets": _targets_of(label),
            })

        return {
            "path": str(path),
            "schema": _pmi_map.schema_of(str(path)),
            "dims": dims,
            "datums": datums,
            "fcf": fcfs,
        }

    return {
        "export_step_pmi": export_step_pmi,
        "read_step_pmi": read_step_pmi,
        "export_3mf_rich": export_3mf_rich,
        "export_step_structured": export_step_structured,
    }
