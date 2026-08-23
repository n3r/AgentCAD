"""ACM1 → glTF 2.0 / GLB, in pure Python (PRD-017 §6, FR6–FR7).

The mesh cache the browser already streams (``kernel/acm.py``'s ACM1 buffers)
is positions + normals + triangle indices in little-endian float32/uint32 —
which is byte-for-byte what a glTF binary buffer wants. So the exporter runs in
the **server** process: no kernel round trip, no OCP, no new dependency, and no
triangulation work (OCCT's own ``RWGltf_CafWriter`` would need a meshed shape
in the worker, and does not deduplicate).

Three things this module owns, all of them testable without a viewer:

* **Dedup.** Buffer data is emitted once per ``mesh_key`` — 8 screws are 1 mesh
  and 8 nodes. (A ``meshes`` entry is one per *(mesh_key, material)* pair,
  because a glTF primitive carries its material: two instances of one part in
  two colours must not share a primitive. Identical colours — the normal case —
  still collapse to one.)
* **Up axis.** AgentCAD is Z-up, glTF is Y-up. The conversion is **one root
  node** with a fixed −90° X quaternion, never a per-caller flag, and
  ``asset.extras`` says so in the file: ``{"source_up_axis": "+Z",
  "converted_to": "+Y"}``. Instance translations/rotations stay in the
  authored Z-up frame underneath it, so a node's numbers still match the
  manifest.
* **Determinism (FR7).** Sorted keys, sorted meshes/materials/nodes, floats
  rounded to ``FLOAT_DIGITS`` decimals, no timestamp, no generator version, no
  file name inside the JSON (GLB embeds its buffer). Two exports of the same
  state are byte-identical — asserted by sha256 in the tests.

This is the **third** mirror of the ACM1 layout (``kernel/acm.py`` packs it,
``frontend/js/viewport.js`` parses it in the browser). It is deliberately
minimal — header + three slices — and ``kernel/acm.py`` is not imported,
because ``agentcad/kernel`` is the one package allowed to reach OCP and the
server process must stay clear of it.
"""

from __future__ import annotations

import base64
import json
import math
import struct

import numpy as np

from .interop_colors import srgb_to_linear

#: ``-90°`` about X, as a glTF ``[x, y, z, w]`` quaternion: Z-up → Y-up.
ROOT_ROTATION = (-math.sin(math.pi / 4), 0.0, 0.0, math.cos(math.pi / 4))

#: Stated in every file we write, so a consumer never has to guess.
UP_AXIS_EXTRAS = {"source_up_axis": "+Z", "converted_to": "+Y"}

GENERATOR = "AgentCAD"

#: Decimals every float in the JSON is rounded to (``score.json``'s rule).
FLOAT_DIGITS = 6

#: PBR constants by material category (spec §6): metal, or everything else.
METAL_PBR = (0.9, 0.4)
DEFAULT_PBR = (0.0, 0.8)

GLB_MAGIC = b"glTF"
_CHUNK_JSON = 0x4E4F534A
_CHUNK_BIN = 0x004E4942

_ACM_MAGIC = b"ACM1"
_ACM_HEADER = struct.Struct("<4sIIII")

_ARRAY_BUFFER = 34962
_ELEMENT_ARRAY_BUFFER = 34963
_FLOAT = 5126
_UNSIGNED_INT = 5125
_TRIANGLES = 4


class GltfError(ValueError):
    """A malformed item list or mesh buffer — never a partial file."""


def parse_acm(data: bytes) -> dict:
    """The minimal ACM1 read glTF needs: positions, normals, indices.

    Returns the raw little-endian byte slices (they are already exactly what a
    glTF buffer view holds) beside numpy views for the accessor bounds. Edge
    polylines are read past, not returned — glTF meshes are triangles.
    """
    if len(data) < _ACM_HEADER.size:
        raise GltfError("not an ACM1 buffer (too short)")
    magic, nv, nt, _nep, _nel = _ACM_HEADER.unpack_from(data, 0)
    if magic != _ACM_MAGIC:
        raise GltfError("not an ACM1 buffer")
    if nv == 0 or nt == 0:
        raise GltfError("mesh has no triangles")
    off = _ACM_HEADER.size
    pos_bytes = data[off:off + 12 * nv]
    off += 12 * nv
    nrm_bytes = data[off:off + 12 * nv]
    off += 12 * nv
    idx_bytes = data[off:off + 12 * nt]
    off += 12 * nt
    if len(pos_bytes) != 12 * nv or len(nrm_bytes) != 12 * nv \
            or len(idx_bytes) != 12 * nt:
        raise GltfError("truncated ACM1 buffer")
    positions = np.frombuffer(pos_bytes, dtype="<f4").reshape(-1, 3)
    return {
        "vertex_count": int(nv),
        "index_count": int(nt) * 3,
        "positions": pos_bytes,
        "normals": nrm_bytes,
        "indices": idx_bytes,
        "min": [float(v) for v in positions.min(axis=0)],
        "max": [float(v) for v in positions.max(axis=0)],
    }


def _num(value) -> float:
    """One rounding rule for every float in the document (and no ``-0.0``)."""
    out = round(float(value), FLOAT_DIGITS)
    return 0.0 if out == 0.0 else out


def _vec(values) -> list[float]:
    return [_num(v) for v in values]


def _qmul(a, b) -> tuple[float, float, float, float]:
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
        aw * bw - ax * bx - ay * by - az * bz,
    )


def quaternion_from_euler_xyz(rotation_deg) -> tuple[float, float, float, float]:
    """Intrinsic XYZ Euler degrees → a glTF ``[x, y, z, w]`` quaternion.

    The house convention (``service._apply_transform``, build123d ``Location``,
    ``THREE.Euler("XYZ")``) is **R = Rx · Ry · Rz** — the Z rotation hits the
    vector first — so the quaternion composes in exactly that order.
    """
    rx, ry, rz = (math.radians(float(a)) / 2.0 for a in rotation_deg)
    qx = (math.sin(rx), 0.0, 0.0, math.cos(rx))
    qy = (0.0, math.sin(ry), 0.0, math.cos(ry))
    qz = (0.0, 0.0, math.sin(rz), math.cos(rz))
    return _qmul(_qmul(qx, qy), qz)


def _normalized_items(items) -> list[dict]:
    if not items:
        raise GltfError("nothing to export: no built instances")
    out = []
    for raw in items:
        mesh_key = raw.get("mesh_key")
        acm_bytes = raw.get("acm_bytes")
        if not mesh_key or not acm_bytes:
            raise GltfError("every item needs a mesh_key and its ACM1 bytes")
        out.append({
            "instance_id": str(raw.get("instance_id") or mesh_key),
            "mesh_key": str(mesh_key),
            "acm_bytes": acm_bytes,
            "position": list(raw.get("position") or (0.0, 0.0, 0.0)),
            "rotation_deg": list(raw.get("rotation_deg") or (0.0, 0.0, 0.0)),
            "color_hex": raw.get("color_hex") or None,
            "material_category": raw.get("material_category") or None,
        })
    # Nodes are emitted in instance-id order: a stable file for a stable state.
    out.sort(key=lambda item: item["instance_id"])
    return out


def _material_key(item) -> tuple[str, str]:
    return (item["color_hex"] or "", item["material_category"] or "")


def _material_of(key: tuple[str, str]) -> dict:
    color, category = key
    red, green, blue = srgb_to_linear(color)
    metallic, roughness = METAL_PBR if category == "metal" else DEFAULT_PBR
    return {
        # `baseColorFactor` is LINEAR in glTF 2.0 — the sRGB hex we store would
        # render visibly darker if written straight through.
        "name": f"{category or 'material'}_{(color or '').lstrip('#') or 'default'}",
        "pbrMetallicRoughness": {
            "baseColorFactor": [_num(red), _num(green), _num(blue), 1.0],
            "metallicFactor": _num(metallic),
            "roughnessFactor": _num(roughness),
        },
        "doubleSided": True,
    }


def build_document(items) -> tuple[dict, bytes]:
    """``(glTF document without its buffer uri, binary buffer)``."""
    normalized = _normalized_items(items)

    # ---- buffer data: once per mesh_key, in sorted key order ----
    mesh_keys = sorted({item["mesh_key"] for item in normalized})
    by_key = {}
    for item in normalized:
        by_key.setdefault(item["mesh_key"], item["acm_bytes"])

    buffer = bytearray()
    buffer_views: list[dict] = []
    accessors: list[dict] = []
    accessor_set: dict[str, tuple[int, int, int]] = {}
    for key in mesh_keys:
        mesh = parse_acm(by_key[key])
        for payload, target in (
            (mesh["positions"], _ARRAY_BUFFER),
            (mesh["normals"], _ARRAY_BUFFER),
            (mesh["indices"], _ELEMENT_ARRAY_BUFFER),
        ):
            # Every ACM1 slice is a whole number of 4-byte words, so offsets
            # stay 4-byte aligned without padding — asserted, never assumed.
            assert len(buffer) % 4 == 0, "buffer view misaligned"
            buffer_views.append({
                "buffer": 0,
                "byteOffset": len(buffer),
                "byteLength": len(payload),
                "target": target,
            })
            buffer.extend(payload)
        base = len(buffer_views) - 3
        accessors.append({
            "bufferView": base,
            "componentType": _FLOAT,
            "count": mesh["vertex_count"],
            "type": "VEC3",
            "min": _vec(mesh["min"]),
            "max": _vec(mesh["max"]),
        })
        accessors.append({
            "bufferView": base + 1,
            "componentType": _FLOAT,
            "count": mesh["vertex_count"],
            "type": "VEC3",
        })
        accessors.append({
            "bufferView": base + 2,
            "componentType": _UNSIGNED_INT,
            "count": mesh["index_count"],
            "type": "SCALAR",
        })
        triple = (len(accessors) - 3, len(accessors) - 2, len(accessors) - 1)
        accessor_set[key] = triple

    # ---- materials: one per (colour, category) ----
    material_keys = sorted({_material_key(item) for item in normalized})
    material_index = {key: i for i, key in enumerate(material_keys)}
    materials = [_material_of(key) for key in material_keys]

    # ---- meshes: one per (mesh_key, material) — a primitive carries its
    # material, so two colours of one part are two primitives over ONE set of
    # accessors (the buffer data is still emitted once).
    mesh_pairs = sorted({(item["mesh_key"], _material_key(item))
                         for item in normalized})
    mesh_index = {pair: i for i, pair in enumerate(mesh_pairs)}
    meshes = []
    for key, mat_key in mesh_pairs:
        position, normal, indices = accessor_set[key]
        meshes.append({
            "name": key,
            "primitives": [{
                "attributes": {"POSITION": position, "NORMAL": normal},
                "indices": indices,
                "material": material_index[mat_key],
                "mode": _TRIANGLES,
            }],
        })

    # ---- nodes: one root (the up-axis conversion) + one per instance ----
    nodes: list[dict] = [{
        "name": "AgentCAD",
        "rotation": _vec(ROOT_ROTATION),
        "children": list(range(1, len(normalized) + 1)),
    }]
    for item in normalized:
        nodes.append({
            "name": item["instance_id"],
            "mesh": mesh_index[(item["mesh_key"], _material_key(item))],
            "translation": _vec(item["position"]),
            "rotation": _vec(quaternion_from_euler_xyz(item["rotation_deg"])),
        })

    document = {
        "asset": {
            "version": "2.0",
            # No version number and no timestamp: the file is a function of the
            # model state, nothing else (FR7).
            "generator": GENERATOR,
            "extras": dict(UP_AXIS_EXTRAS),
        },
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": nodes,
        "meshes": meshes,
        "materials": materials,
        "accessors": accessors,
        "bufferViews": buffer_views,
        "buffers": [{"byteLength": len(buffer)}],
    }
    return document, bytes(buffer)


def _dumps(document: dict) -> bytes:
    return json.dumps(document, sort_keys=True, separators=(",", ":"),
                      allow_nan=False, ensure_ascii=True).encode("utf-8")


def build_gltf(items, *, bin_uri: str | None = None) -> tuple[bytes, bytes]:
    """``(json_bytes, bin_bytes)`` for a ``.gltf``.

    With *bin_uri* the JSON references that sidecar file; without it (the
    default, and what the exporter writes) the buffer is embedded as a base64
    data URI, so the ``.gltf`` is one self-contained, name-independent file.
    """
    document, buffer = build_document(items)
    if bin_uri is None:
        document["buffers"][0]["uri"] = (
            "data:application/octet-stream;base64,"
            + base64.b64encode(buffer).decode("ascii")
        )
    else:
        document["buffers"][0]["uri"] = bin_uri
    return _dumps(document), buffer


def build_glb(items) -> bytes:
    """A binary glTF container: header + JSON chunk + BIN chunk.

    Chunks are padded to 4 bytes (JSON with spaces, BIN with zeros) exactly as
    the spec requires — a viewer rejects the file otherwise.
    """
    document, buffer = build_document(items)
    payload = _dumps(document)
    json_chunk = payload + b" " * (-len(payload) % 4)
    bin_chunk = buffer + b"\x00" * (-len(buffer) % 4)
    total = 12 + 8 + len(json_chunk) + (8 + len(bin_chunk) if bin_chunk else 0)
    out = bytearray()
    out += GLB_MAGIC + struct.pack("<II", 2, total)
    out += struct.pack("<II", len(json_chunk), _CHUNK_JSON) + json_chunk
    if bin_chunk:
        out += struct.pack("<II", len(bin_chunk), _CHUNK_BIN) + bin_chunk
    assert len(out) == total
    return bytes(out)
