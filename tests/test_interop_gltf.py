"""PRD-017 slice 4 — the pure halves: ACM1→glTF/GLB and the colour map.

No kernel here: ACM1 buffers are synthesized from the documented layout (the
same three slices ``kernel/acm.py`` packs), so these tests run in milliseconds
and fail on *our* arithmetic rather than on a build. The kernel-backed,
end-to-end half is ``tests/test_xchange_pack.py``.

What is actually being pinned:

* **AC3 (machine half)** — a GLB is structurally valid (magic, chunk types,
  4-byte alignment, accessor bounds) and two builds of one state are
  byte-identical.
* **The two conversions nobody sees until a viewer looks wrong**: Z-up→Y-up
  (one root node, not a per-caller flag) and intrinsic-XYZ Euler→quaternion
  (R = Rx·Ry·Rz — the house convention, hand-computed here, not borrowed from
  the implementation).
* **sRGB→linear**: ``baseColorFactor`` is linear in glTF 2.0; storing our sRGB
  hex straight through is the classic silent-darkening bug.
"""

import json
import math
import struct
import subprocess
import sys
from hashlib import sha256
from pathlib import Path

import pytest

from agentcad.core import gltf, interop_colors, usd_export
from agentcad.core.materials import CATEGORIES, SUBCATEGORIES
from agentcad.core.model import ValidationError

REPO = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------- fixtures


def make_acm(positions, normals=None, indices=None) -> bytes:
    """An ACM1 buffer, written straight from the layout in `kernel/acm.py`'s
    docstring — this test must not import the module it is checking against."""
    normals = normals if normals is not None else [(0.0, 0.0, 1.0)] * len(positions)
    indices = indices if indices is not None else [(0, 1, 2)]
    out = bytearray()
    out += struct.pack("<4sIIII", b"ACM1", len(positions), len(indices), 0, 0)
    for vec in positions:
        out += struct.pack("<3f", *vec)
    for vec in normals:
        out += struct.pack("<3f", *vec)
    for tri in indices:
        out += struct.pack("<3I", *tri)
    return bytes(out)


TRI = make_acm([(0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (0.0, 20.0, 5.0)])
QUAD = make_acm(
    [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 1.0, 0.0), (0.0, 1.0, 0.0)],
    indices=[(0, 1, 2), (0, 2, 3)],
)


def item(instance_id="a", mesh_key="k1", acm=TRI, position=(0, 0, 0),
         rotation=(0, 0, 0), color="#b0b6bd", category="metal") -> dict:
    return {
        "instance_id": instance_id, "mesh_key": mesh_key, "acm_bytes": acm,
        "position": list(position), "rotation_deg": list(rotation),
        "color_hex": color, "material_category": category,
    }


def parse_glb(blob: bytes) -> tuple[dict, bytes]:
    """Reader half of the container, written independently of the writer."""
    magic, version, total = struct.unpack_from("<4sII", blob, 0)
    assert magic == b"glTF" and version == 2
    assert total == len(blob)
    offset, doc, binary = 12, None, b""
    while offset < len(blob):
        length, kind = struct.unpack_from("<II", blob, offset)
        chunk = blob[offset + 8:offset + 8 + length]
        if kind == 0x4E4F534A:
            doc = json.loads(chunk.decode("utf-8"))
        elif kind == 0x004E4942:
            binary = chunk
        assert length % 4 == 0, "every GLB chunk is 4-byte aligned"
        offset += 8 + length
    assert doc is not None
    return doc, binary


def quat_matrix(q):
    x, y, z, w = q
    return [
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ]


def apply(matrix, vec):
    return [sum(matrix[r][c] * vec[c] for c in range(3)) for r in range(3)]


def euler_matrix_xyz(rotation_deg):
    """R = Rx . Ry . Rz, multiplied out by hand — the convention
    ``service._apply_transform`` documents, derived here independently."""
    rx, ry, rz = (math.radians(a) for a in rotation_deg)

    def mul(a, b):
        return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)]
                for i in range(3)]

    x = [[1, 0, 0], [0, math.cos(rx), -math.sin(rx)], [0, math.sin(rx), math.cos(rx)]]
    y = [[math.cos(ry), 0, math.sin(ry)], [0, 1, 0], [-math.sin(ry), 0, math.cos(ry)]]
    z = [[math.cos(rz), -math.sin(rz), 0], [math.sin(rz), math.cos(rz), 0], [0, 0, 1]]
    return mul(mul(x, y), z)


# ------------------------------------------------------------ ACM parsing


def test_parse_acm_reads_the_three_slices():
    mesh = gltf.parse_acm(TRI)
    assert mesh["vertex_count"] == 3
    assert mesh["index_count"] == 3
    assert mesh["min"] == [0.0, 0.0, 0.0]
    assert mesh["max"] == [10.0, 20.0, 5.0]
    assert len(mesh["positions"]) == 36 and len(mesh["normals"]) == 36
    assert len(mesh["indices"]) == 12


def test_parse_acm_refuses_anything_that_is_not_a_mesh():
    with pytest.raises(gltf.GltfError):
        gltf.parse_acm(b"NOPE" + TRI[4:])
    with pytest.raises(gltf.GltfError):
        gltf.parse_acm(TRI[:30])          # truncated
    with pytest.raises(gltf.GltfError):
        gltf.parse_acm(make_acm([], indices=[])[:20])   # no triangles


# ---------------------------------------------------- GLB structural valid


def test_glb_container_is_structurally_valid():
    doc, binary = parse_glb(gltf.build_glb([item()]))
    assert doc["asset"]["version"] == "2.0"
    assert doc["scene"] == 0 and doc["scenes"] == [{"nodes": [0]}]
    # the buffer has no uri in a GLB: the BIN chunk IS the buffer
    assert "uri" not in doc["buffers"][0]
    assert doc["buffers"][0]["byteLength"] <= len(binary)
    for view in doc["bufferViews"]:
        assert view["byteOffset"] % 4 == 0
        assert view["byteOffset"] + view["byteLength"] <= len(binary)
        assert view["target"] in (34962, 34963)
    for accessor in doc["accessors"]:
        view = doc["bufferViews"][accessor["bufferView"]]
        width = {"VEC3": 3, "SCALAR": 1}[accessor["type"]]
        assert accessor["count"] * width * 4 == view["byteLength"]
    primitive = doc["meshes"][0]["primitives"][0]
    assert primitive["mode"] == 4
    assert set(primitive["attributes"]) == {"POSITION", "NORMAL"}
    position = doc["accessors"][primitive["attributes"]["POSITION"]]
    assert position["min"] == [0.0, 0.0, 0.0]        # accessor bounds, exact
    assert position["max"] == [10.0, 20.0, 5.0]
    assert position["componentType"] == 5126
    assert doc["accessors"][primitive["indices"]]["componentType"] == 5125


def test_the_bin_chunk_holds_the_acm_vertex_bytes_verbatim():
    """No re-encoding: ACM1 is little-endian float32/uint32, which is what a
    glTF buffer view holds, so the export is a copy."""
    _doc, binary = parse_glb(gltf.build_glb([item()]))
    assert binary[:36] == TRI[20:56]


def test_a_gltf_json_embeds_its_buffer_and_a_named_sidecar_is_referenced():
    payload, binary = gltf.build_gltf([item()])
    doc = json.loads(payload)
    assert doc["buffers"][0]["uri"].startswith(
        "data:application/octet-stream;base64,")
    assert doc["buffers"][0]["byteLength"] == len(binary)
    named, _ = gltf.build_gltf([item()], bin_uri="box.bin")
    assert json.loads(named)["buffers"][0]["uri"] == "box.bin"


# ------------------------------------------------------------------ dedup


def test_meshes_are_deduplicated_by_mesh_key():
    """8 screws = 1 mesh, 8 nodes (spec §6)."""
    items = [item(instance_id=f"s{i}", position=(i * 10, 0, 0))
             for i in range(8)]
    doc, _ = parse_glb(gltf.build_glb(items))
    assert len(doc["meshes"]) == 1
    assert len(doc["accessors"]) == 3          # position, normal, indices
    assert len(doc["bufferViews"]) == 3
    assert len(doc["materials"]) == 1
    assert len(doc["nodes"]) == 9              # one root + eight instances
    assert doc["nodes"][0]["children"] == list(range(1, 9))
    assert [n["name"] for n in doc["nodes"][1:]] == [f"s{i}" for i in range(8)]
    assert {n["mesh"] for n in doc["nodes"][1:]} == {0}


def test_two_mesh_keys_are_two_meshes():
    doc, _ = parse_glb(gltf.build_glb(
        [item(instance_id="a", mesh_key="k1", acm=TRI),
         item(instance_id="b", mesh_key="k2", acm=QUAD)]))
    assert len(doc["meshes"]) == 2
    assert len(doc["accessors"]) == 6
    assert sorted(m["name"] for m in doc["meshes"]) == ["k1", "k2"]


def test_one_part_in_two_colours_shares_its_buffer_data():
    """A glTF primitive carries its material, so two colours cannot share one
    primitive — but the vertex data is still written once."""
    doc, _ = parse_glb(gltf.build_glb([
        item(instance_id="a", color="#ff0000"),
        item(instance_id="b", color="#00ff00"),
    ]))
    assert len(doc["materials"]) == 2
    assert len(doc["meshes"]) == 2
    assert len(doc["bufferViews"]) == 3        # the dedup that matters
    assert len(doc["accessors"]) == 3
    primitives = [m["primitives"][0] for m in doc["meshes"]]
    assert primitives[0]["attributes"] == primitives[1]["attributes"]


# ----------------------------------------------------------- determinism


def test_two_builds_of_one_state_are_byte_identical():
    """AC3's machine half (the PRD-014 sha idiom): no timestamp, no ordering
    drift, one rounding rule."""
    items = [item(instance_id="b", mesh_key="k2", acm=QUAD),
             item(instance_id="a", mesh_key="k1", position=(1 / 3, 0, 0))]
    first = gltf.build_glb(items)
    second = gltf.build_glb(items)
    assert sha256(first).hexdigest() == sha256(second).hexdigest()
    assert first == gltf.build_glb(list(reversed(items))), \
        "item order must not reach the file (nodes sort by instance id)"
    assert gltf.build_gltf(items)[0] == gltf.build_gltf(items)[0]


def test_the_file_carries_no_timestamp_or_version_string():
    doc, _ = parse_glb(gltf.build_glb([item()]))
    assert doc["asset"]["generator"] == "AgentCAD"
    assert "copyright" not in doc["asset"]


# ------------------------------------------------------------- transforms


def test_the_root_node_converts_z_up_to_y_up_and_says_so():
    doc, _ = parse_glb(gltf.build_glb(
        [item(position=(0, 0, 10), rotation=(0, 0, 0))]))
    root = doc["nodes"][0]
    assert doc["asset"]["extras"] == {"source_up_axis": "+Z",
                                      "converted_to": "+Y"}
    assert root["rotation"] == pytest.approx(
        [-0.7071068, 0.0, 0.0, 0.7071068], abs=1e-6)
    assert "mesh" not in root       # the root is the conversion, nothing else

    # A part 10 mm up our +Z axis lands 10 mm up glTF's +Y after the root.
    child = doc["nodes"][1]
    world = apply(quat_matrix(root["rotation"]), child["translation"])
    # 1e-5 mm, not 0: the quaternion in the file is rounded to
    # `gltf.FLOAT_DIGITS` decimals like every other float, which is ~6 nm of
    # arc error on a 10 mm arm. That is the rounding budget, stated.
    assert world == pytest.approx([0.0, 10.0, 0.0], abs=1e-5)
    # ... and the child's own numbers are still the authored Z-up ones.
    assert child["translation"] == [0.0, 0.0, 10.0]


@pytest.mark.parametrize("rotation", [
    [90, 0, 0], [0, 90, 0], [0, 0, 90], [30, 45, 60], [-15, 0, 120],
])
def test_node_rotation_is_the_house_intrinsic_xyz_convention(rotation):
    """R = Rx·Ry·Rz (`service._apply_transform`): the Z rotation hits the
    vector first. Compared against a hand-multiplied matrix, no scipy."""
    quaternion = gltf.quaternion_from_euler_xyz(rotation)
    assert sum(c * c for c in quaternion) == pytest.approx(1.0, abs=1e-12)
    expected = euler_matrix_xyz(rotation)
    got = quat_matrix(quaternion)
    for row in range(3):
        assert got[row] == pytest.approx(expected[row], abs=1e-9)


def test_a_rotated_instance_writes_that_quaternion_into_its_node():
    doc, _ = parse_glb(gltf.build_glb([item(rotation=(0, 0, 90))]))
    assert doc["nodes"][1]["rotation"] == pytest.approx(
        [0.0, 0.0, 0.707107, 0.707107], abs=1e-6)


# ----------------------------------------------------------------- colours


def test_base_color_factor_is_linear_not_srgb():
    doc, _ = parse_glb(gltf.build_glb([item(color="#808080")]))
    factor = doc["materials"][0]["pbrMetallicRoughness"]["baseColorFactor"]
    # 0x80/255 = 0.50196 sRGB -> 0.21586 linear. Writing 0.50196 here is the
    # silent-darkening bug this assertion exists for.
    assert factor[:3] == pytest.approx([0.215861] * 3, abs=1e-6)
    assert factor[3] == 1.0


def test_pbr_constants_follow_the_category():
    metal, _ = parse_glb(gltf.build_glb([item(category="metal")]))
    pbr = metal["materials"][0]["pbrMetallicRoughness"]
    assert (pbr["metallicFactor"], pbr["roughnessFactor"]) == (0.9, 0.4)
    plastic, _ = parse_glb(gltf.build_glb([item(category="polymer")]))
    pbr = plastic["materials"][0]["pbrMetallicRoughness"]
    assert (pbr["metallicFactor"], pbr["roughnessFactor"]) == (0.0, 0.8)


def test_an_empty_item_list_refuses():
    with pytest.raises(gltf.GltfError):
        gltf.build_glb([])
    with pytest.raises(gltf.GltfError):
        gltf.build_glb([{"instance_id": "a"}])


# --------------------------------------------------- the refusal CONTRACT


def test_a_gltf_refusal_is_an_app_error_not_a_bare_valueerror():
    """`GltfError` is a `ValidationError`, so the refusal reaches a caller as a
    4xx envelope. As a bare `ValueError` it escaped `ToolRegistry.call` AND
    FastAPI's `AppError` handler, and a malformed export answered 500 —
    "something broke in the server" for an input the caller can fix."""
    assert issubclass(gltf.GltfError, ValidationError)
    assert issubclass(gltf.EmptyMeshError, gltf.GltfError)
    assert issubclass(usd_export.UsdError, ValidationError)
    with pytest.raises(ValidationError):
        gltf.build_glb([])
    error = pytest.raises(gltf.GltfError, gltf.parse_acm, b"NOPE").value
    assert error.message and error.details == {}


def test_an_empty_mesh_is_its_own_refusal_class():
    """The two callers answer it differently — a part export refuses, an
    assembly export skips the member — so it needs its own class, and the
    header-only probe has to agree with the parser."""
    empty = make_acm([], indices=[])
    with pytest.raises(gltf.EmptyMeshError):
        gltf.parse_acm(empty)
    assert gltf.has_triangles(empty) is False
    assert gltf.has_triangles(TRI) is True
    assert gltf.has_triangles(b"NOPE" + TRI[4:]) is False
    assert gltf.has_triangles(b"") is False


# ----------------------------------------------------- non-finite numbers


def test_a_non_finite_pose_is_refused_and_names_the_instance():
    """glTF JSON has no NaN or Infinity literal. Without the guard the failure
    was a `ValueError` out of `json.dumps` naming nothing — after the export
    had already spent the caller's disk budget."""
    for bad in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(gltf.GltfError) as exc:
            gltf.build_glb([item(instance_id="bolt", position=(bad, 0, 0))])
        assert "bolt" in str(exc.value)
        with pytest.raises(gltf.GltfError) as exc:
            gltf.build_glb([item(instance_id="bolt", rotation=(0, bad, 0))])
        assert "bolt" in str(exc.value)


def test_a_non_finite_rotation_never_reaches_the_quaternion():
    """`math.sin(inf)` is a ValueError and `math.sin(nan)` is a silent NaN that
    reaches the file as a broken quaternion — refuse the input, not either."""
    with pytest.raises(gltf.GltfError):
        gltf.quaternion_from_euler_xyz((float("inf"), 0, 0))
    with pytest.raises(gltf.GltfError):
        gltf.quaternion_from_euler_xyz((0, 0, float("nan")))


def test_a_non_finite_vertex_is_refused_by_the_parser():
    nan_mesh = make_acm([(0.0, 0.0, 0.0), (float("nan"), 0.0, 0.0),
                         (0.0, 1.0, 0.0)])
    with pytest.raises(gltf.GltfError) as exc:
        gltf.parse_acm(nan_mesh)
    assert "non-finite" in str(exc.value)


# ------------------------------------------------------- accessor bounds


def test_accessor_bounds_actually_bound_the_serialized_buffer():
    """The JSON is rounded to six decimals and the buffer is not, so an
    ordinary round can put the declared `min` *above* the smallest vertex (or
    `max` below the largest) by up to 5e-7 — which a glTF validator reports as
    an accessor value outside its declared bounds. The two ends round
    outwards.

    The fixture is chosen so plain rounding gets it wrong: 0.0000005 rounds to
    0.000001 (up, past the value) and 9.9999995 rounds to 10.0 (down, under
    it) at six decimals.
    """
    mesh = make_acm([(0.0000005, -0.0000005, 0.0),
                     (9.9999995, 1.0, 2.0),
                     (5.0, 3.0, 4.0)])
    doc, binary = parse_glb(gltf.build_glb([item(acm=mesh)]))
    accessor = doc["accessors"][0]
    points = struct.unpack("<9f", binary[:36])
    for axis in range(3):
        column = points[axis::3]
        assert accessor["min"][axis] <= min(column)
        assert accessor["max"][axis] >= max(column)
    # ...and it is still six-decimal, deterministic text (no float noise)
    assert gltf.build_glb([item(acm=mesh)]) == gltf.build_glb([item(acm=mesh)])
    for value in (*accessor["min"], *accessor["max"]):
        assert round(value, 6) == value
        assert str(value) != "-0.0"


# ------------------------------------------------------------ colour map


class _Record:
    def __init__(self, material):
        self.material = material


def test_explicit_instance_colour_wins_over_the_category():
    record = _Record("al6061")
    assert interop_colors.color_for(record) != "#ff0000"
    assert interop_colors.color_for(record, {"color": "#FF0000"}) == "#ff0000"
    assert interop_colors.color_for(record, {"color": "#f00"}) == "#ff0000"
    # a malformed colour degrades to the category, never raises
    assert interop_colors.color_for(record, {"color": "red"}) == \
        interop_colors.color_for(record)


def test_the_category_map_is_used_and_metals_split_by_subcategory():
    assert interop_colors.category_of("al6061") == "metal"
    assert interop_colors.color_for(_Record("al6061")) == \
        interop_colors.METAL_SUBCATEGORY_COLORS["aluminum"]
    assert interop_colors.color_for(_Record("abs")) == \
        interop_colors.CATEGORY_COLORS["polymer"]


def test_an_unknown_or_absent_material_is_the_viewport_default():
    assert interop_colors.color_for(_Record("unobtainium")) == "#98a2ad"
    assert interop_colors.color_for(None) == interop_colors.DEFAULT_COLOR
    assert interop_colors.category_for(None) is None


def test_a_solid_material_overrides_the_parts_own():
    record = _Record("abs")
    assert interop_colors.color_for(record, solid_material="al6061") == \
        interop_colors.METAL_SUBCATEGORY_COLORS["aluminum"]
    assert interop_colors.category_for(record, "al6061") == "metal"


def test_the_category_map_is_closed_over_the_library():
    """A new category (or metal subcategory) in `materials.py` without a colour
    here would fall back to neutral and nobody would notice."""
    assert set(interop_colors.CATEGORY_COLORS) == set(CATEGORIES)
    assert set(interop_colors.METAL_SUBCATEGORY_COLORS) == \
        set(SUBCATEGORIES["metal"])
    for value in list(interop_colors.CATEGORY_COLORS.values()) + \
            list(interop_colors.METAL_SUBCATEGORY_COLORS.values()):
        assert interop_colors.normalize_hex(value) == value


def test_srgb_to_linear_matches_the_spec_curve():
    assert interop_colors.srgb_to_linear("#000000") == (0.0, 0.0, 0.0)
    assert interop_colors.srgb_to_linear("#ffffff") == pytest.approx((1, 1, 1))
    # below the knee the curve is linear (c / 12.92)
    assert interop_colors.srgb_to_linear("#0a0a0a")[0] == pytest.approx(
        (10 / 255) / 12.92, abs=1e-12)
    # and a broken colour falls back rather than raising mid-file
    assert interop_colors.srgb_to_linear("nope") == \
        interop_colors.srgb_to_linear(interop_colors.DEFAULT_COLOR)


# --------------------------------------------------------------- OCP-free

#: module -> a smoke expression that must hold once it is imported with
#: OCP/build123d blocked (the `tests/test_packages_ocp_free.py` idiom).
OCP_FREE = {
    "agentcad.core.gltf": 'mod.GLB_MAGIC == b"glTF"',
    "agentcad.core.interop_colors": 'mod.DEFAULT_COLOR == "#98a2ad"',
    "agentcad.core.tools_xchange": '"glb" in mod.PART_FORMATS',
}

_PROBE = '''
import importlib
import sys


class _Blocked:
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in ("OCP", "build123d"):
            raise ImportError("blocked kernel import: " + name)
        return None


sys.meta_path.insert(0, _Blocked())
mod = importlib.import_module({module!r})
assert {expr}, "smoke expression failed: " + {expr_msg!r}
assert "OCP" not in sys.modules and "build123d" not in sys.modules
print("ok")
'''


@pytest.mark.integration
@pytest.mark.portability
@pytest.mark.parametrize("module", sorted(OCP_FREE))
def test_the_interop_server_modules_are_ocp_free(module):
    """glTF is written in the SERVER process from the mesh cache — if one of
    these grew an OCP import it would stop loading there, and the failure would
    surface far from the cause."""
    source = _PROBE.format(module=module, expr=OCP_FREE[module],
                           expr_msg=OCP_FREE[module])
    proc = subprocess.run([sys.executable, "-c", source], cwd=REPO,
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip().endswith("ok")
