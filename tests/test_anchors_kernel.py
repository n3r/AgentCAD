"""PRD-008 slice 2 against real geometry: AC2, R2 and R3.

Everything here needs a kernel build, so the module is ``slow`` (and
``integration``: it crosses the worker process boundary). The pure logic —
``face_table``, the matcher, both script_range tiers, the four-state
constructor — is in ``tests/test_anchors.py`` and needs none of this.

Sections: 1. AC2, a face anchor across a parameter change · 2. AC2, a face
that was cut away · 3. R2, the two meanings of ``n_faces`` · 4. R3, a
mesh-derived signature versus ``face_info``.
"""

from __future__ import annotations

import pytest

from agentcad.core import anchors
from agentcad.core.comments import CommentManager
from agentcad.core.service import AgentCADService, EventBus

pytestmark = [pytest.mark.slow, pytest.mark.integration]

BOSS = '''\
import build123d as b3d

PARAMS = {
    "plate_w": {"default": 40.0, "min": 20.0, "max": 80.0, "unit": "mm"},
    "boss_h":  {"default": 10.0, "min": 4.0,  "max": 20.0, "unit": "mm"},
}


def build(p):
    plate = b3d.Box(p.plate_w, 40, 10)
    boss = b3d.Cylinder(radius=8, height=p.boss_h).moved(
        b3d.Location((0, 0, 5 + p.boss_h / 2)))
    return plate + boss
'''

NO_BOSS = '''\
import build123d as b3d

PARAMS = {
    "plate_w": {"default": 40.0, "min": 20.0, "max": 80.0, "unit": "mm"},
    "boss_h":  {"default": 10.0, "min": 4.0,  "max": 20.0, "unit": "mm"},
}


def build(p):
    return b3d.Box(p.plate_w, 40, 10)
'''

# The same plate with a boss so wide that its top face is nearly the plate top:
# same normal, same normalized position, and an area share close enough to slip
# through a 50% gate. This is the shape the mis-pin was found on.
WIDE_BOSS = '''\
import build123d as b3d

PARAMS = {
    "plate_w": {"default": 40.0, "min": 20.0, "max": 80.0, "unit": "mm"},
    "boss_r":  {"default": 19.0, "min": 4.0,  "max": 19.5, "unit": "mm"},
}


def build(p):
    plate = b3d.Box(p.plate_w, 40, 10)
    boss = b3d.Cylinder(radius=p.boss_r, height=10).moved(
        b3d.Location((0, 0, 10)))
    return plate + boss
'''

WIDE_NO_BOSS = WIDE_BOSS.replace(
    "    boss = b3d.Cylinder(radius=p.boss_r, height=10).moved(\n"
    "        b3d.Location((0, 0, 10)))\n    return plate + boss\n",
    "    return plate\n")
assert "boss" not in WIDE_NO_BOSS.split("def build")[1]

# The verifier's geometry: a *pad* wide enough that the plate top left behind
# is also inside the tightened lone-candidate area bar. r=20 on a 40 mm plate
# reproduced at area_rel 0.237, confidence 0.9289 — inside LONE_AREA_REL.
PAD = '''\
import build123d as b3d

PARAMS = {
    "plate_w": {"default": 40.0, "min": 20.0, "max": 80.0, "unit": "mm"},
    "pad_r":   {"default": 20.0, "min": 4.0,  "max": 20.0, "unit": "mm"},
    "pad_h":   {"default": 1.0,  "min": 1.0,  "max": 8.0,  "unit": "mm"},
}


def build(p):
    plate = b3d.Box(p.plate_w, p.plate_w, 10)
    pad = b3d.Cylinder(radius=p.pad_r, height=p.pad_h).moved(
        b3d.Location((0, 0, 5 + p.pad_h / 2)))
    return plate + pad
'''

PAD_GONE = PAD.replace(
    "    pad = b3d.Cylinder(radius=p.pad_r, height=p.pad_h).moved(\n"
    "        b3d.Location((0, 0, 5 + p.pad_h / 2)))\n    return plate + pad\n",
    "    return plate\n")
assert "pad =" not in PAD_GONE.split("def build")[1]

# The same story with a SQUARE pad, which is the shape the plate top under it
# also has. This one is the residue: nothing in a mesh-derived signature tells
# a 38 mm square apart from the 40 mm square beneath it.
SQUARE_PAD = '''\
import build123d as b3d

PARAMS = {"plate_w": {"default": 40.0, "min": 20.0, "max": 80.0, "unit": "mm"}}


def build(p):
    plate = b3d.Box(p.plate_w, p.plate_w, 10)
    pad = b3d.Box(38, 38, 1).moved(b3d.Location((0, 0, 5.5)))
    return plate + pad
'''

SQUARE_PAD_GONE = SQUARE_PAD.replace(
    "    pad = b3d.Box(38, 38, 1).moved(b3d.Location((0, 0, 5.5)))\n"
    "    return plate + pad\n", "    return plate\n")
assert "pad =" not in SQUARE_PAD_GONE.split("def build")[1]

TWO_SOLIDS = '''\
import build123d as b3d

PARAMS = {"size": {"default": 10.0}}


def build(p):
    a = b3d.Box(p.size, p.size, p.size)
    b = b3d.Box(p.size, p.size, p.size).moved(b3d.Location((p.size * 2, 0, 0)))
    return b3d.Compound(children=[a, b])
'''

COINCIDENT = '''\
import build123d as b3d

PARAMS = {"size": {"default": 10.0}}


def build(p):
    return b3d.Compound(children=[b3d.Box(p.size, p.size, p.size),
                                  b3d.Box(p.size, p.size, p.size)])
'''

CYLINDER = '''\
import build123d as b3d

PARAMS = {"d": {"default": 20.0}}


def build(p):
    return b3d.Cylinder(radius=p.d / 2, height=30)
'''


@pytest.fixture
def demo(kernel, tmp_path):
    bus = EventBus()
    service = AgentCADService(tmp_path / "projects", kernel, bus)
    bus.on_publish = None                 # no git snapshots: not what this tests
    service.create_project("demo")
    service.store.add_part("demo", "boss", "Boss", "al6061", BOSS)
    assert service.get_part("demo", "boss")["status"]["state"] == "ok"
    anchors.forget_tables()
    return service, CommentManager(service)


def _top_of_boss(service, part="boss"):
    """The boss's top face, identified the way ``tests/test_facemod.py``
    identifies a face — by normal and position, never by a hardcoded ordinal,
    because an ordinal is exactly what this slice proved unstable."""
    _key, table = anchors.signature_table(service, "demo", part)
    up = [row for row in table
          if row["present"] and row["normal"][2] > 0.99]
    return max(up, key=lambda row: row["centroid"][2])


# ------------------------- 1. AC2: a face anchor across a parameter change


def test_a_face_anchor_survives_a_parameter_change(demo):
    """**AC2**, first half. ``plate_w`` widens the plate without touching the
    boss; the thread must still point at the boss's top face, whether or not
    the ordinal survived — and the identity is checked geometrically, not by
    trusting the resolver's own answer."""
    service, manager = demo
    face = _top_of_boss(service)
    thread = manager.create(
        "demo", {"kind": "face", "part": "boss", "face_index": face["index"]},
        "this boss needs a fillet")
    assert thread["resolution"]["status"] == "ok"

    service.set_params("demo", "boss", {"plate_w": 60.0})
    view = manager.get("demo", thread["id"])
    resolution = view["resolution"]

    assert resolution["status"] in ("ok", "moved"), resolution
    _key, table = anchors.signature_table(service, "demo", "boss")
    resolved = table[resolution["face_index"]]
    expected = _top_of_boss(service)
    assert resolved["index"] == expected["index"], resolution
    # and the stored anchor is untouched: it is evidence, not a cursor
    stored = manager.store.load("demo", thread["id"])["anchor"]
    assert stored["face_index"] == face["index"]
    assert "resolution" not in stored


def test_a_moved_ordinal_is_reported_as_moved_not_ok(demo):
    """The status is about identity, not about the number: the same face at a
    new ordinal is ``moved``, and the payload carries the new ordinal so a pin
    is drawn in the right place."""
    service, manager = demo
    face = _top_of_boss(service)
    thread = manager.create(
        "demo", {"kind": "face", "part": "boss", "face_index": face["index"]},
        "look")

    service.set_params("demo", "boss", {"boss_h": 16.0})
    resolution = manager.get("demo", thread["id"])["resolution"]

    assert resolution["status"] in ("ok", "moved")
    if resolution["status"] == "moved":
        assert resolution["face_index"] != face["index"]
        assert resolution["reason"] == "rematched_by_signature"
    assert resolution["confidence"] > 0.5


def test_an_unchanged_part_takes_the_byte_identical_fast_path(demo):
    """The cache key is content-addressed, so an unchanged part needs no
    matching at all — and no face table."""
    service, manager = demo
    face = _top_of_boss(service)
    thread = manager.create(
        "demo", {"kind": "face", "part": "boss", "face_index": face["index"]},
        "look")

    resolution = manager.get("demo", thread["id"])["resolution"]
    assert resolution == {"status": "ok", "confidence": 1.0,
                          "face_index": face["index"],
                          "n_faces": resolution["n_faces"],
                          "against": resolution["against"]}


# ---------------------------------------- 2. AC2: a face that was cut away


def test_a_face_that_was_cut_away_is_orphaned(demo):
    """**AC2**, second half. The boss is deleted from the script; the thread
    stays readable, keeps its last-known anchor, and says so."""
    service, manager = demo
    face = _top_of_boss(service)
    thread = manager.create(
        "demo", {"kind": "face", "part": "boss", "face_index": face["index"]},
        "this boss needs a fillet")

    service.update_part("demo", "boss", script=NO_BOSS)
    view = manager.get("demo", thread["id"])

    assert view["resolution"]["status"] == "orphaned"
    assert view["resolution"]["reason"] in ("no_candidate", "ambiguous",
                                            "area_mismatch")
    assert view["resolution"]["hint"]
    assert view["anchor"]["face_index"] == face["index"]   # last-known anchor
    assert view["comments"][0]["body"] == "this boss needs a fillet"
    assert manager.list("demo")["counts"]["orphaned"] == 1


def test_a_cut_away_face_does_not_re_pin_onto_the_survivor_under_it(demo):
    """The mis-pin the ambiguity margin could not catch.

    ``match_face`` only ran its ambiguity check when there were *two* or more
    candidates, so a face with exactly one survivor in the pool was accepted
    for being the only one left — and reported a margin of ``best - 0``, i.e.
    near-maximal confidence. Widen the boss until its top face is the same
    size, orientation and normalized position as the plate top hiding beneath
    it, cut the boss away, and the thread moved onto the plate at 0.87.

    A lone candidate now has to clear an absolute area bar
    (:data:`anchors.LONE_AREA_REL`), because with no rival there is nothing
    else left to corroborate it.
    """
    service, manager = demo
    service.update_part("demo", "boss", script=WIDE_BOSS)
    anchors.forget_tables()
    face = _top_of_boss(service)
    thread = manager.create(
        "demo", {"kind": "face", "part": "boss", "face_index": face["index"]},
        "this boss needs a fillet")
    assert thread["resolution"]["status"] == "ok"

    service.update_part("demo", "boss", script=WIDE_NO_BOSS)
    resolution = manager.get("demo", thread["id"])["resolution"]

    assert resolution["status"] == "orphaned", resolution
    assert resolution["reason"] == "area_mismatch", resolution
    assert resolution["hint"]


def test_a_cut_away_pad_does_not_re_pin_at_the_sizes_the_area_bar_misses(demo):
    """The same class again, at the sizes the *tightened area bar* still lets
    through — which is how the verification round showed the first fix did not
    close what it claimed to.

    A 20 mm pad on a 40 mm plate leaves a plate top whose share of the area is
    0.24 away from the pad top's, comfortably inside ``LONE_AREA_REL`` (0.30),
    and the thread moved onto it at confidence 0.9289. Reproduced at
    (r, h) = (20, 1), (20, 2), (19.5, 1) and (20, 4); refused at (19, 1) and
    (18, 2) only because those clear the area bar.

    What separates them is not a size at all: the pad top is a disc bounded by
    one cylindrical wall, and the plate top is a square bounded by four sides.
    """
    service, manager = demo
    for radius, height in ((20.0, 1.0), (20.0, 2.0), (19.5, 1.0), (20.0, 4.0)):
        part = f"pad{int(radius * 10)}_{int(height * 10)}"
        service.store.add_part("demo", part, part, "al6061", PAD)
        service.set_params("demo", part, {"pad_r": radius, "pad_h": height})
        assert service.get_part("demo", part)["status"]["state"] == "ok"
        anchors.forget_tables()
        face = _top_of_boss(service, part)
        thread = manager.create(
            "demo", {"kind": "face", "part": part,
                     "face_index": face["index"]}, "chamfer this pad")
        assert thread["resolution"]["status"] == "ok"

        service.update_part("demo", part, script=PAD_GONE)
        resolution = manager.get("demo", thread["id"])["resolution"]
        assert resolution["status"] == "orphaned", (radius, height, resolution)
        assert resolution["reason"] == "topology_mismatch", resolution
        assert resolution["hint"]


def test_a_square_pad_the_shape_of_the_face_under_it_still_re_pins(demo):
    """**The residue, on purpose.** This class is narrowed, not closed.

    Delete a 38 mm square pad from a 40 mm square plate and the face left
    behind has the same normal, the same normalized position, four neighbors
    just like the pad top, the same square outline and a share of the area
    0.13 away — every feature a mesh-derived signature has. The thread moves
    onto the plate top and reports it as ``moved``, which is a mis-pin.

    It is measured (4 of 327 destroyed faces in the deletion sweep, all of
    this shape) and it is what every surface that quotes a rate says out loud:
    a cut-away face can still re-pin, so confirm with ``face_info`` before
    acting on an expensive decision. A test that asserts the honest outcome is
    worth more than a comment claiming it cannot happen.
    """
    service, manager = demo
    service.store.add_part("demo", "sq", "Square pad", "al6061", SQUARE_PAD)
    assert service.get_part("demo", "sq")["status"]["state"] == "ok"
    anchors.forget_tables()
    face = _top_of_boss(service, "sq")
    thread = manager.create(
        "demo", {"kind": "face", "part": "sq", "face_index": face["index"]},
        "chamfer this pad")

    service.update_part("demo", "sq", script=SQUARE_PAD_GONE)
    resolution = manager.get("demo", thread["id"])["resolution"]

    assert resolution["status"] == "moved", resolution
    assert resolution["reason"] == "rematched_by_signature"


def test_an_orphaned_thread_is_still_listable_and_resolvable(demo):
    """FR3: never silently dropped, never re-pointed."""
    service, manager = demo
    face = _top_of_boss(service)
    thread = manager.create(
        "demo", {"kind": "face", "part": "boss", "face_index": face["index"]},
        "look")
    service.update_part("demo", "boss", script=NO_BOSS)

    listed = manager.list("demo", anchor_status="orphaned")
    assert [row["id"] for row in listed["threads"]] == [thread["id"]]
    assert manager.resolve("demo", thread["id"])["state"] == "resolved"
    assert manager.reply("demo", thread["id"], "fixed")["comments"][1]["body"] \
        == "fixed"


def test_a_face_index_out_of_range_is_refused_at_creation(demo):
    """FR1: a bad anchor is a validation_error, never a stored orphan."""
    from agentcad.core.model import ValidationError

    service, manager = demo
    _key, table = anchors.signature_table(service, "demo", "boss")
    with pytest.raises(ValidationError) as exc:
        manager.create("demo", {"kind": "face", "part": "boss",
                                "face_index": len(table)}, "look")
    assert exc.value.details["n_faces"] == len(table)


# ------------------------------------- 3. R2: the two meanings of n_faces


def test_the_sidecar_is_the_face_index_authority(demo):
    """R2, measured. ``metrics.n_faces`` is ``len(shape.faces())``, which
    build123d deduplicates by hash, and the design warns it can be smaller
    than the sidecar's count on a compound or a shared-face shape.

    **We could not reproduce a divergence.** Across the 11 bundled example
    parts, a two-solid compound and a compound of two *coincident* boxes, the
    two numbers agreed every time (see changelog 0113). The sidecar stays the
    authority because it is what a face ordinal is *defined* by — the explorer
    walk `mesh.py` tessellates in — not because the metric was observed to
    disagree. This test documents the measurement rather than the folklore.
    """
    service, manager = demo
    service.store.add_part("demo", "two", "Two", "al6061", TWO_SOLIDS)
    service.store.add_part("demo", "twin", "Twin", "al6061", COINCIDENT)

    for part, solids in (("boss", 1), ("two", 2), ("twin", 2)):
        detail = service.get_part("demo", part)
        assert detail["status"]["state"] == "ok", detail["status"]["error"]
        _key, table = anchors.signature_table(service, "demo", part)
        assert detail["metrics"]["n_solids"] == solids
        assert len(table) == detail["metrics"]["n_faces"], part
        # and an index at the top of the sidecar's range is anchorable
        assert manager.create(
            "demo", {"kind": "face", "part": part,
                     "face_index": len(table) - 1}, "edge case")


# --------------------------- 4. R3: the mesh signature versus face_info


def test_a_planar_face_signature_agrees_with_face_info(demo, kernel):
    """R3, planar. The mesh-derived normal and centroid are the B-rep's, and
    the tessellated area is within the chord error."""
    service, _manager = demo
    service.store.add_part("demo", "cyl", "Cyl", "al6061", CYLINDER)
    assert service.get_part("demo", "cyl")["status"]["state"] == "ok"
    _key, table = anchors.signature_table(service, "demo", "cyl")

    cap = next(row for row in table if row["normal"][2] > 0.99)
    info = kernel.request("face_info", {"script": CYLINDER, "params": {},
                                        "face_index": cap["index"]})
    assert info["normal"] == pytest.approx(cap["normal"], abs=1e-6)
    assert info["center"] == pytest.approx(cap["centroid"], abs=1e-3)
    # 314.159 mm^2 of circle, tessellated by chords: ~0.5% low, never high
    assert cap["area"] < info["area_mm2"]
    assert abs(cap["area"] - info["area_mm2"]) / info["area_mm2"] < 0.01


def test_a_closed_curved_face_diverges_and_the_matcher_knows_it(demo, kernel):
    """R3, curved — the divergence the design predicted, measured.

    On the *closed* side face of a cylinder the area-weighted normal very
    nearly cancels (the surface wraps a full turn), so its direction is
    numerically meaningless, while ``face_info`` reports ``normal_at(0.5,
    0.5)`` — a single sample. The two disagree completely, and no tolerance
    could reconcile them.

    That is survivable exactly because the matcher only ever compares a
    mesh-derived signature with another mesh-derived signature at the same
    ``MESH_TOLERANCE``: a consistent estimator beats an accurate one. Its
    failure mode on such faces is a wobbly normal, which costs candidacy and
    produces an *orphan* — never a mis-pin. The absolute numbers in the
    payload are labelled mesh-derived for this reason.
    """
    import numpy as np

    service, _manager = demo
    service.store.add_part("demo", "cyl", "Cyl", "al6061", CYLINDER)
    assert service.get_part("demo", "cyl")["status"]["state"] == "ok"
    _key, table = anchors.signature_table(service, "demo", "cyl")

    side = next(row for row in table if abs(row["normal"][2]) < 0.5)
    info = kernel.request("face_info", {"script": CYLINDER, "params": {},
                                        "face_index": side["index"]})
    assert abs(float(np.dot(side["normal"], info["normal"]))) < 0.95
    # the areas still agree: a tessellated cylinder is a hair small, not wrong
    assert abs(side["area"] - info["area_mm2"]) / info["area_mm2"] < 0.01
    # and the mesh centroid is on the axis, where a closed face's really is
    assert side["centroid"][0] == pytest.approx(0.0, abs=1e-3)
