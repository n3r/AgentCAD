"""Worker handler pack: interop exports (PRD-017).

``export_step_pmi`` writes a STEP AP242 file carrying the part's PMI (our
``core/pmi.py`` model mapped to XCAF by ``_pmi_map``, which owns the six traps
the de-risking spike found). ``read_step_pmi`` reads PMI back out — the
round-trip half of the export's contract and what the test suite asserts on:
PMI entry identity does not survive the writer (labels are overwritten by the
STEP type keyword), so entries are matched by (type, value, tolerance, target)
and datums by *name*, never by label count (a two-datum FCF reads back as three
datum labels).

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
from OCP.STEPCAFControl import STEPCAFControl_Reader
from OCP.TDF import TDF_LabelSequence
from OCP.TopAbs import TopAbs_FACE
from OCP.TopoDS import TopoDS
from OCP.XCAFDoc import (XCAFDoc_Datum, XCAFDoc_Dimension,
                         XCAFDoc_DimTolTool, XCAFDoc_DocumentTool,
                         XCAFDoc_GeomTolerance, XCAFDoc_ShapeTool)

from . import _pmi_map


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


def register(toolbox: dict) -> dict:
    build_shape = toolbox["build_shape"]
    WorkerError = toolbox["WorkerError"]
    ERROR_CONTRACT = toolbox["ERROR_CONTRACT"]
    ERROR_KERNEL = toolbox["ERROR_KERNEL"]

    def _shape_for(params: dict):
        """Script part or reference part, the same two sources
        ``worker._item_shape`` resolves."""
        source = params.get("source_path")
        if source:
            from ..refload import load_reference

            shape, kind = load_reference(source)
            if kind == "mesh":
                raise WorkerError(
                    ERROR_CONTRACT,
                    "STEP+PMI export needs a B-rep; this reference part is "
                    "mesh-only (STL)",
                )
            return shape
        if not params.get("script"):
            raise WorkerError(
                ERROR_CONTRACT, "export_step_pmi needs 'script' or 'source_path'")
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

    return {"export_step_pmi": export_step_pmi, "read_step_pmi": read_step_pmi}
