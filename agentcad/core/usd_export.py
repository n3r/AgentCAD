"""ACM1 → OpenUSD, in pure ``pxr``, behind the ``agentcad[usd]`` extra
(PRD-017 §10, FR11).

Same input as the glTF writer (``core/gltf.py``): the ACM1 mesh buffers the
browser already streams, plus the one colour map (``core/interop_colors.py``).
So this runs in the **server** process — no kernel round trip, no OCP, no
tessellation work — and the two writers cannot disagree about which mesh a
part has or what colour it is.

Four decisions this module makes, all of them stated here because each one is
silently wrong in some other file format's convention:

* **``.usda`` (text), not ``.usdc``.** The PRD asks for a stage a
  usdchecker-equivalent validation accepts and FR7 asks for a *reproducible*
  export. Crate is smaller; text is diffable, reviewable in a PR, and byte
  comparable — two exports of one state are identical bytes (asserted). Any
  consumer can `usdcat` it to crate; nobody can diff a crate file.
* **Units and up axis are DECLARED, never converted.** ``metersPerUnit =
  0.001`` and ``upAxis = "Z"`` — USD carries both natively, so an AgentCAD
  millimetre stays the number the manifest holds and a Z-up model stays Z-up.
  (The glTF writer has to rotate, because glTF is Y-up by fiat; that is the
  difference, and it is why this file has no root rotation.)
* **``primvars:displayColor`` is LINEAR.** USD's displayColor feeds the
  rendering pipeline as linear-light (it is the fallback for
  ``UsdPreviewSurface.diffuseColor``, which is linear), so the sRGB hex we
  store goes through ``srgb_to_linear`` exactly as glTF's
  ``baseColorFactor`` does. DCC interchange does sometimes author raw sRGB
  numbers there; writing our hex straight through would make every export
  read ~2× too bright/dark depending on which end assumes what, and it would
  make the same model differ between our glTF and our USD. One convention,
  stated in the docs.
* **One ``xformOp:transform`` matrix, not ``xformOp:rotateXYZ``.** Our
  rotations are *intrinsic* XYZ Euler degrees (R = Rx·Ry·Rz applied to column
  vectors — ``service._apply_transform``, build123d ``Location``,
  ``THREE.Euler("XYZ")``). USD's ``rotateXYZ`` composes ``xRot * yRot * zRot``
  on **row** vectors (``usdGeom/xformOp.cpp``, ``GetOpTransform``), which is
  ``Rz·Ry·Rx`` in our column convention — the **reverse** of ours, and the two
  agree only when at most one angle is non-zero. Rather than bet a silent
  3-axis error on a convention argument, the pose is written as a
  single matrix built from the **same quaternion the glTF writer uses**
  (``gltf.quaternion_from_euler_xyz``) through ``Gf``'s own
  ``SetRotate``/``SetTranslateOnly`` — one op, no ordering left to get wrong.

Structure of the stage (dedup is composition, not repetition)::

    #usda 1.0 ( defaultPrim = "AgentCAD"; metersPerUnit = 0.001; upAxis = "Z" )
    def Xform "AgentCAD" {
        class "Meshes" {              # abstract: a library, never rendered
            def Mesh "Mesh_<key>" { points, normals, faceVertexCounts/Indices }
        }
        def Mesh "<instance>" ( prepend references = </AgentCAD/Meshes/...> ) {
            matrix4d xformOp:transform ; color3f[] primvars:displayColor
        }
    }

Eight screws are **one** Mesh's worth of points and eight instance prims, each
carrying only its pose and its colour. The library lives *under* the default
prim (an internal reference out of the default prim's subtree is the thing
that breaks when somebody references our layer) and is a ``class``, so it is
abstract: ``Usd.Stage.Traverse`` skips it and no renderer draws the prototypes
at the origin.

``pxr`` is imported **lazily, inside the writer** — this module imports fine
without the extra, which is what lets ``tools_xchange`` ask
``usd_available()`` at registration time. OCP-free: server-process code
(probe in ``tests/test_interop_usd.py``).
"""

from __future__ import annotations

import math
import re

import numpy as np

from . import gltf
from .interop_colors import srgb_to_linear
from .model import ValidationError

#: Millimetres, declared. AgentCAD's own unit, carried rather than converted.
METERS_PER_UNIT = 0.001

#: Z, declared. USD supports "Y" and "Z"; we are Z-up and say so.
UP_AXIS = "Z"

#: The stage's default prim, and the root Xform every instance hangs from.
ROOT_PRIM = "AgentCAD"

#: The abstract (``class``) scope holding one Mesh per unique ``mesh_key``.
MESH_SCOPE = "Meshes"

#: Prefix for a mesh prim's name — a ``mesh_key`` is a hex digest and may
#: start with a digit, which is not a legal USD identifier.
MESH_PREFIX = "Mesh_"

#: What we stamp as the writer. No version and no timestamp: the file is a
#: function of the model state and nothing else (FR7).
GENERATOR = "AgentCAD"

#: The extension the exporter writes (text stage — see the module docstring).
SUFFIX = ".usda"

#: Decimals a colour is rounded to, so the text is tidy and stable.
COLOR_DIGITS = 6

_IDENT_RE = re.compile(r"[^A-Za-z0-9_]")


class UsdError(ValidationError):
    """USD was asked for without the extra that can write it.

    A malformed item list raises ``gltf.GltfError`` instead: both writers
    normalize their input through the same function, so they refuse the same
    things for the same reasons.

    An ``AppError`` (``ValidationError``) and not a bare ``RuntimeError``: the
    condition is the caller's request, and a ``RuntimeError`` escaped both
    ``ToolRegistry.call`` and FastAPI's ``AppError`` handler as a 500.
    """


def usd_available() -> bool:
    """True when ``pxr`` is importable — the ``fem_available()`` twin.

    ``find_spec`` rather than ``import``: this is called at tool-registration
    time, and pulling ~40 MB of USD into every server process to answer a
    yes/no question would be a strange way to make a tool list.
    """
    import importlib.util

    return importlib.util.find_spec("pxr") is not None


def _prim_name(raw: str, used: set[str], *, prefix: str = "") -> str:
    """A legal, unique, deterministic USD prim name for *raw*.

    USD identifiers are ``[A-Za-z_][A-Za-z0-9_]*``; instance ids are authored
    strings (``bolt-1``, ``sub.a``) and mesh keys are hex digests. Sanitizing
    can collide (``a-1`` and ``a.1``), so a collision takes a numbered suffix —
    deterministic because the caller feeds this in sorted order.
    """
    name = _IDENT_RE.sub("_", str(raw)) or "_"
    name = prefix + name
    if not (name[0].isalpha() or name[0] == "_"):
        name = "_" + name
    candidate, n = name, 1
    while candidate in used:
        n += 1
        candidate = f"{name}_{n}"
    used.add(candidate)
    return candidate


def _color(color_hex: str | None) -> tuple[float, float, float]:
    return tuple(round(c, COLOR_DIGITS)          # type: ignore[return-value]
                 for c in srgb_to_linear(color_hex or ""))


def _finite3(values, what: str) -> tuple[float, float, float]:
    """Three floats, refused if any of them is NaN or an infinity.

    ``Gf`` takes a NaN without complaint and ``ExportToString`` writes the
    literal ``nan`` into the stage — a file ``Usd.Stage.Open`` then rejects,
    long after the export reported success. The refusal belongs at the input.
    """
    numbers = tuple(float(v) for v in values)
    if not all(math.isfinite(n) for n in numbers):
        raise UsdError(f"{what} is not finite ({list(values)!r})")
    return numbers                               # type: ignore[return-value]


def _mesh_arrays(acm_bytes: bytes) -> dict:
    """ACM1 → the four arrays a ``UsdGeomMesh`` wants, plus its extent.

    ``gltf.parse_acm`` is the reader (one ACM1 mirror in the server process,
    not two); the byte slices it returns are little-endian float32/uint32,
    which is what ``Vt`` arrays hold — the copies below are dtype/shape work,
    never a re-encoding.
    """
    mesh = gltf.parse_acm(acm_bytes)
    points = np.frombuffer(mesh["positions"], dtype="<f4").reshape(-1, 3)
    normals = np.frombuffer(mesh["normals"], dtype="<f4").reshape(-1, 3)
    indices = np.frombuffer(mesh["indices"], dtype="<u4")
    triangles = mesh["index_count"] // 3
    return {
        # `np.array` and not the view: `np.frombuffer` hands back a read-only
        # window onto the ACM bytes, and Vt's numpy bridge wants an owned array.
        "points": np.array(points, dtype=np.float32),
        "normals": np.array(normals, dtype=np.float32),
        "indices": indices.astype(np.int32),
        "counts": np.full(triangles, 3, dtype=np.int32),
        "min": mesh["min"],
        "max": mesh["max"],
    }


def build_usd_text(items) -> str:
    """The whole stage as ``.usda`` text — the deterministic serialization.

    *items* is the list ``core/gltf.py`` consumes (``instance_id``,
    ``mesh_key``, ``acm_bytes``, ``position`` mm, ``rotation_deg`` intrinsic
    XYZ, ``color_hex``), normalized by the same function so both writers sort
    instances identically and default the same fields.
    """
    if not usd_available():
        raise UsdError(
            "USD export requires the optional extra: pip install "
            "'agentcad[usd]' (no wheel exists for linux-aarch64)"
        )
    # Lazily, and only here: importing pxr is ~40 MB the server does not pay
    # for on any other path.
    from pxr import Gf, Sdf, Usd, UsdGeom, Vt

    # `_normalized_items` is the glTF writer's own normalization (sorted by
    # instance id, defaulted pose, refusal on an item with no mesh) — the
    # `tools_xchange` imports `tools_drawing._drawing_version` precedent, not a
    # new coupling: one input contract for the two mesh writers.
    normalized = gltf._normalized_items(items)

    # The `.usda` in the identifier is the file FORMAT, not a path: an
    # anonymous layer takes its format from the tag's extension, and only a
    # text layer can `ExportToString`.
    stage = Usd.Stage.CreateInMemory("agentcad.usda")
    UsdGeom.SetStageMetersPerUnit(stage, METERS_PER_UNIT)
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)

    root = UsdGeom.Xform.Define(stage, Sdf.Path(f"/{ROOT_PRIM}"))
    stage.SetDefaultPrim(root.GetPrim())

    # ---- the mesh library: one Mesh per unique key, in sorted key order ----
    scope_path = Sdf.Path(f"/{ROOT_PRIM}/{MESH_SCOPE}")
    UsdGeom.Scope.Define(stage, scope_path)
    by_key: dict[str, bytes] = {}
    for item in normalized:
        by_key.setdefault(item["mesh_key"], item["acm_bytes"])

    used: set[str] = {MESH_SCOPE}
    mesh_paths: dict[str, Sdf.Path] = {}
    for key in sorted(by_key):
        arrays = _mesh_arrays(by_key[key])
        path = scope_path.AppendChild(_prim_name(key, used, prefix=MESH_PREFIX))
        mesh = UsdGeom.Mesh.Define(stage, path)
        mesh.CreatePointsAttr(Vt.Vec3fArray.FromNumpy(arrays["points"]))
        mesh.CreateNormalsAttr(Vt.Vec3fArray.FromNumpy(arrays["normals"]))
        # Our normals are one per point, indexed by faceVertexIndices.
        mesh.SetNormalsInterpolation(UsdGeom.Tokens.vertex)
        mesh.CreateFaceVertexCountsAttr(Vt.IntArray.FromNumpy(arrays["counts"]))
        mesh.CreateFaceVertexIndicesAttr(
            Vt.IntArray.FromNumpy(arrays["indices"]))
        # Without this a renderer treats the triangles as a subdivision cage
        # and smooths the model — the USD equivalent of the mesh-shading bug.
        mesh.CreateSubdivisionSchemeAttr(UsdGeom.Tokens.none)
        mesh.CreateExtentAttr(Vt.Vec3fArray([Gf.Vec3f(*arrays["min"]),
                                             Gf.Vec3f(*arrays["max"])]))
        # Same call the glTF writer makes (`doubleSided: true`): a tessellated
        # shell viewed from inside should not disappear.
        mesh.CreateDoubleSidedAttr(True)
        mesh.GetPrim().SetCustomDataByKey("agentcad:mesh_key", key)
        mesh_paths[key] = path

    # `class`, not `def`: the library is a set of prototypes, and an abstract
    # prim is neither traversed by `Usd.Stage.Traverse` nor drawn at the
    # origin. It has to happen AFTER the meshes are defined — `DefinePrim`
    # walks ancestors and would put the specifier back to `def`.
    stage.GetPrimAtPath(scope_path).SetSpecifier(Sdf.SpecifierClass)

    # ---- one instance prim per item, referencing its prototype ----
    for item in normalized:
        prim_path = Sdf.Path(f"/{ROOT_PRIM}").AppendChild(
            _prim_name(item["instance_id"], used))
        gprim = UsdGeom.Mesh.Define(stage, prim_path)
        prim = gprim.GetPrim()
        # An internal reference: the points are authored once, in the library.
        prim.GetReferences().AddInternalReference(mesh_paths[item["mesh_key"]])

        quaternion = gltf.quaternion_from_euler_xyz(item["rotation_deg"])
        qx, qy, qz, qw = quaternion
        matrix = Gf.Matrix4d(1.0)
        matrix.SetRotate(Gf.Quatd(qw, qx, qy, qz))
        matrix.SetTranslateOnly(Gf.Vec3d(*_finite3(
            item["position"], f"instance {item['instance_id']!r} position")))
        gprim.AddTransformOp().Set(matrix)

        color = gprim.CreateDisplayColorPrimvar(UsdGeom.Tokens.constant)
        color.Set(Vt.Vec3fArray([Gf.Vec3f(*_color(item["color_hex"]))]))
        prim.SetCustomDataByKey("agentcad:instance_id", item["instance_id"])

    layer = stage.GetRootLayer()
    # Provenance with nothing in it that changes between two runs of one state.
    layer.customLayerData = {"creator": GENERATOR}
    return layer.ExportToString()


def build_usd(items) -> bytes:
    """The stage as bytes — ``gltf.build_glb``'s twin.

    Bytes rather than a path on purpose: ``tools_xchange`` owns the atomic
    write (tmp + ``os.replace``) for every format it writes itself, so a
    killed export never leaves a torn file where a whole one used to be.
    """
    return build_usd_text(items).encode("utf-8")
