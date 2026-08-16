"""PRD-010 slices 9 and 10 — `toolkit.features`: ribs, bosses, draft.

Two measured facts drive every assertion in here, both taken through the
kernel worker (changelogs 0155 and 0156):

* **A rib that misses the part is a silent success.** Fusing a rib solid 25 mm
  above `prototyping/enclosure_base` took 50.9 ms, raised nothing, reported
  `is_valid True` and *increased* the volume by the rib's full 960 mm^3 — the
  volume delta looks exactly like a rib that worked. Only the solid count (2,
  not 1) and the engagement probe (`part & rib` = 0 mm^3) tell them apart, so
  those are what the guard measures and what these tests assert.
* **Draft fails silently far more often than it raises.** Sweeping 0.25 -> 60
  degrees, the dominant failure is `draft()` *returning* a shape with
  `is_valid False` and a plausible positive volume (a shelled box at 1 deg:
  32421 mm^3, invalid). Only the extreme angles raise. So `features.draft`
  validates every attempt and these tests assert the result is always valid.
"""

import math

import pytest


def _plate(w=100.0, d=60.0, t=5.0):
    from build123d import Box

    return Box(w, d, t)


# ----------------------------------------------------------------- rib

def test_rib_volume_matches_the_hand_built_solid():
    from agentcad.toolkit import features

    part = _plate()
    out, warning = features.rib(part, [(-40, 0), (40, 0)], 3.0, to=8.0)
    # 80 x 3 x 8 = 1920 mm^3, measured exact through the worker (delta - hand
    # = 0.0 on the plate, -4.4e-11 on enclosure_base).
    assert out.volume - part.volume == pytest.approx(80 * 3 * 8, rel=1e-9)
    assert len(out.solids()) == 1 and out.is_valid
    assert warning is None


def test_rib_follows_a_multi_segment_profile():
    from agentcad.toolkit import features

    part = _plate()
    out, warning = features.rib(part, [(-30, -10), (0, -10), (0, 10)], 2.0,
                                to=8.0)
    assert out.volume - part.volume == pytest.approx((30 + 20) * 2 * 8,
                                                     rel=1e-6)
    assert len(out.solids()) == 1 and out.is_valid
    assert warning is None


def test_a_rib_that_misses_the_part_warns_and_names_the_floating_solid():
    """The silent-failure case: OCCT fuses it, reports valid, and the volume
    delta is the rib's full volume. Two solids is the only geometric tell."""
    from build123d import Plane

    from agentcad.toolkit import features

    part = _plate()
    above = Plane(origin=(0, 0, 30), x_dir=(1, 0, 0), z_dir=(0, 0, 1))
    out, warning = features.rib(part, [(-40, 0), (40, 0)], 3.0, to=8.0,
                                plane=above)
    assert out.volume - part.volume == pytest.approx(1920.0)  # looks fine!
    assert out.is_valid                                       # ... and valid
    assert len(out.solids()) == 2                             # but floating
    assert warning is not None
    assert "does not reach" in warning or "floating" in warning
    assert "2 disjoint solids" in warning


def test_rib_to_part_trims_to_the_envelope_and_says_so():
    """`to="part"` intersects the part's own bounding solid. On a convex part
    that envelope IS the part, so the rib lands entirely inside existing
    material and adds nothing — measured 0.0 mm^3 on a plate. The mode is
    approximate and always says which trim it used."""
    from agentcad.toolkit import features

    part = _plate()
    out, warning = features.rib(part, [(-40, 0), (40, 0)], 3.0, to="part")
    assert out.volume == pytest.approx(part.volume)
    assert warning is not None and "to=part" in warning
    assert "added no material" in warning


def test_rib_to_part_fills_the_envelope_of_a_shelled_part():
    from build123d import Axis, Box, Plane, offset

    from agentcad.toolkit import features

    box = Box(60, 40, 20)
    shelled = offset(box, amount=-2, openings=box.faces().sort_by(Axis.Z)[-1])
    floor = Plane(origin=(0, 0, -8), x_dir=(1, 0, 0), z_dir=(0, 0, 1))
    out, warning = features.rib(shelled, [(-20, 0), (20, 0)], 2.0, to="part",
                                plane=floor)
    # the rib runs from the floor to the top of the bounding box (z = +10),
    # i.e. 18 mm of it above the seat, minus the 2 mm already inside the floor
    assert out.volume - shelled.volume == pytest.approx(40 * 2 * 18, rel=1e-6)
    assert len(out.solids()) == 1 and out.is_valid
    assert warning is not None and "to=part" in warning


def test_rib_draft_tapers_the_extrusion_and_removes_material():
    from agentcad.toolkit import features

    part = _plate()
    plain, _ = features.rib(part, [(-40, 0), (40, 0)], 3.0, to=8.0)
    drafted, _ = features.rib(part, [(-40, 0), (40, 0)], 3.0, to=8.0,
                              draft_deg=3.0)
    assert drafted.volume < plain.volume
    assert len(drafted.solids()) == 1 and drafted.is_valid


def test_rib_carries_hole_records_forward():
    from agentcad.toolkit import features, holes

    part = _plate()
    part, records, _ = holes.clearance(part, [(0, 20)], "M5")
    out, _warning = features.rib(part, [(-40, 0), (40, 0)], 3.0, to=8.0)
    assert [r["id"] for r in holes.records(out)] == [records[0]["id"]]


@pytest.mark.parametrize("kwargs,argument", [
    ({"thickness": 0.0, "to": 8.0}, "thickness"),
    ({"thickness": -1.0, "to": 8.0}, "thickness"),
    ({"thickness": 3.0, "to": 0.0}, "to"),
    ({"thickness": 3.0, "to": -2.0}, "to"),
    ({"thickness": 3.0, "to": "envelope"}, "to"),
    ({"thickness": 3.0, "to": 8.0, "draft_deg": 90.0}, "draft_deg"),
])
def test_rib_degenerate_arguments_raise_naming_the_argument(kwargs, argument):
    from agentcad.toolkit import features

    with pytest.raises(ValueError, match=argument):
        features.rib(_plate(), [(-40, 0), (40, 0)], **kwargs)


def test_rib_needs_at_least_two_profile_points():
    from agentcad.toolkit import features

    with pytest.raises(ValueError, match="profile"):
        features.rib(_plate(), [(0, 0)], 3.0, to=8.0)


# ---------------------------------------------------------------- boss

def test_boss_volume_and_single_solid():
    import math

    from agentcad.toolkit import features

    part = _plate(60, 60, 10)
    out, warning = features.boss(part, (0, 0), 8.0, 6.0)
    assert out.volume - part.volume == pytest.approx(math.pi * 16 * 6,
                                                     rel=1e-4)
    assert len(out.solids()) == 1 and out.is_valid
    assert warning is None


def test_boss_with_a_tapped_hole_produces_a_record():
    from agentcad.toolkit import features, holes

    part = _plate(60, 60, 10)
    out, warning = features.boss(part, (10, 0), 8.0, 6.0, hole="M3")
    records = holes.records(out)
    assert len(records) == 1
    record = records[0]
    assert record["family"] == "tapped" and record["size"] == "M3"
    assert record["d"] == pytest.approx(2.5)      # ISO M3 tap drill
    assert record["tap"]["pitch"] == pytest.approx(0.5)
    assert record["depth_mm"] == pytest.approx(6.0)   # the boss height
    assert record["centers"][0] == pytest.approx([10.0, 0.0, 11.0])
    assert len(out.solids()) == 1 and out.is_valid
    assert warning is None


def test_boss_hole_depth_can_be_stated():
    from agentcad.toolkit import features, holes

    part = _plate(60, 60, 10)
    out, _warning = features.boss(part, (0, 0), 9.0, 6.0, hole="M4",
                                  hole_depth=10.0)
    record = holes.records(out)[0]
    assert record["depth_mm"] == pytest.approx(10.0)
    assert record["thru"] is False


def test_boss_draft_removes_material_and_stays_one_solid():
    from agentcad.toolkit import features

    part = _plate(60, 60, 10)
    plain, _ = features.boss(part, (0, 0), 8.0, 6.0)
    drafted, _ = features.boss(part, (0, 0), 8.0, 6.0, draft_deg=5.0)
    assert drafted.volume < plain.volume
    assert len(drafted.solids()) == 1 and drafted.is_valid


def test_a_boss_that_misses_the_part_warns():
    from build123d import Plane

    from agentcad.toolkit import features

    part = _plate(60, 60, 10)
    above = Plane(origin=(0, 0, 40), x_dir=(1, 0, 0), z_dir=(0, 0, 1))
    out, warning = features.boss(part, (0, 0), 8.0, 6.0, plane=above)
    assert len(out.solids()) == 2
    assert warning is not None and "disjoint solids" in warning


@pytest.mark.parametrize("kwargs,argument", [
    ({"d": 0.0, "h": 6.0}, "d"),
    ({"d": 8.0, "h": 0.0}, "h"),
    ({"d": 8.0, "h": 6.0, "draft_deg": 45.0}, "draft_deg"),
])
def test_boss_degenerate_arguments_raise_naming_the_argument(kwargs, argument):
    from agentcad.toolkit import features

    with pytest.raises(ValueError, match=argument):
        features.boss(_plate(60, 60, 10), (0, 0), **kwargs)


def test_boss_carries_existing_hole_records_and_appends_its_own():
    from agentcad.toolkit import features, holes

    part = _plate(60, 60, 10)
    part, _records, _ = holes.clearance(part, [(-20, 0)], "M5")
    out, _warning = features.boss(part, (20, 0), 8.0, 6.0, hole="M3")
    families = [r["family"] for r in holes.records(out)]
    assert families == ["clearance", "tapped"]


# --------------------------------------------------------------- draft

def _sides(part):
    """Every face whose normal is perpendicular to the pull direction — the
    selector a caller writes as "all faces not parallel to the neutral
    plane"."""
    out = []
    for face in part.faces():
        try:
            normal = face.normal_at(face.center())
        except Exception:  # noqa: BLE001 — a face with no defined normal
            continue
        if abs(normal.Z) < 1e-6:
            out.append(face)
    return out


def _shelled_box():
    from build123d import Axis, Box, offset

    box = Box(40, 30, 20)
    return offset(box, amount=-2, openings=box.faces().sort_by(Axis.Z)[-1])


def test_a_plain_box_drafts_at_the_requested_angle():
    from build123d import Box, Plane

    from agentcad.toolkit import features

    part = Box(40, 30, 20)
    out, achieved, warning = features.draft(
        part, _sides(part), 10.0, Plane.XY.offset(-10))
    assert achieved == pytest.approx(10.0)
    assert warning is None
    assert out.is_valid and out.volume < part.volume
    assert len(out.solids()) == 1


def test_a_face_selector_callable_is_accepted():
    from build123d import Box, Plane

    from agentcad.toolkit import features

    part = Box(40, 30, 20)
    out, achieved, warning = features.draft(
        part, _sides, 10.0, Plane.XY.offset(-10))
    assert achieved == pytest.approx(10.0) and warning is None
    assert out.is_valid


def test_a_shelled_box_comes_back_at_the_achievable_angle_with_a_warning():
    """Measured through the worker (changelog 0156): a shelled box t=2 is ok
    at 2.5 deg and fails at 3, so the search must land in [2.5, 3)."""
    from build123d import Plane

    from agentcad.toolkit import features

    part = _shelled_box()
    out, achieved, warning = features.draft(
        part, _sides(part), 5.0, Plane.XY.offset(-10))
    assert 2.0 <= achieved < 3.0
    assert out.is_valid and out.volume > 0
    assert warning is not None
    assert "5" in warning and f"{achieved:.3f}" in warning
    assert "8 face" in warning


def test_draft_never_returns_the_invalid_shape_occt_hands_back():
    """The dominant failure is not an exception: `draft()` *returns* a shape
    with `is_valid False` and a plausible volume (a shelled box at 1 deg
    measured 32421 mm^3, invalid). The raw call is asserted here so the
    helper's validation cannot be mistaken for belt-and-braces."""
    from build123d import Plane, draft as b3d_draft

    from agentcad.toolkit import features

    part = _shelled_box()
    raw = b3d_draft(_sides(part), neutral_plane=Plane.XY.offset(-10), angle=5.0)
    assert raw.volume > 0 and not raw.is_valid       # OCCT says nothing

    out, achieved, _warning = features.draft(
        part, _sides(part), 5.0, Plane.XY.offset(-10))
    assert out.is_valid and achieved < 5.0


def test_a_draft_that_fails_even_at_min_angle_returns_the_part_unchanged():
    from build123d import Plane

    from agentcad.toolkit import features

    part = _shelled_box()
    out, achieved, warning = features.draft(
        part, _sides(part), 10.0, Plane.XY.offset(-10), min_angle=5.0)
    assert achieved == 0.0
    assert out.volume == pytest.approx(part.volume)
    assert warning is not None
    assert "5" in warning and "unchanged" in warning
    # the failing angle AND what OCCT said about it — which is nothing
    assert "no message" in warning or "invalid" in warning


def test_draft_refuses_face_indices():
    from build123d import Box, Plane

    from agentcad.toolkit import features

    part = Box(40, 30, 20)
    with pytest.raises(ValueError, match="faces"):
        features.draft(part, [0, 2, 4], 5.0, Plane.XY.offset(-10))


def test_draft_with_no_faces_is_a_warning_not_a_raise():
    from build123d import Box, Plane

    from agentcad.toolkit import features

    part = Box(40, 30, 20)
    out, achieved, warning = features.draft(
        part, [], 5.0, Plane.XY.offset(-10))
    assert achieved == 0.0 and out is part
    assert warning is not None and "no faces" in warning


def test_draft_carries_hole_records_forward():
    from build123d import Box, Plane

    from agentcad.toolkit import features, holes

    part = Box(40, 30, 20)
    part, records, _ = holes.clearance(part, [(0, 0)], "M5")
    out, achieved, _warning = features.draft(
        part, _sides(part), 5.0, Plane.XY.offset(-10))
    assert achieved > 0
    assert [r["id"] for r in holes.records(out)] == [records[0]["id"]]


@pytest.mark.parametrize("angle,argument", [
    (0.0, "angle_deg"),
    (-5.0, "angle_deg"),
    (95.0, "angle_deg"),
])
def test_draft_degenerate_angle_raises_naming_the_argument(angle, argument):
    from build123d import Box, Plane

    from agentcad.toolkit import features

    part = Box(40, 30, 20)
    with pytest.raises(ValueError, match=argument):
        features.draft(part, _sides(part), angle, Plane.XY.offset(-10))


# ---------------------------------------------- the Error Doctor pattern

def test_error_doctor_diagnoses_the_empty_message_draft_failure():
    """Triggered for real, per `error_doctor.py`'s rule: OCCT raises
    `Standard_Failure` with an EMPTY message out of `BRepOffsetAPI_DraftAngle`,
    so the traceback is the only thing there is to match on."""
    from build123d import Plane, draft as b3d_draft

    from agentcad.kernel.error_doctor import diagnose_exception

    part = _shelled_box()
    with pytest.raises(Exception) as caught:  # noqa: PT011 — OCCT type
        b3d_draft(_sides(part), neutral_plane=Plane.XY.offset(-10), angle=40.0)
    assert str(caught.value) == ""            # the measured empty message
    entry = diagnose_exception(caught.value)
    assert entry is not None and entry["id"] == "draft_angle_too_large"
    assert "features.draft" in entry["fix"]


def test_error_doctor_diagnoses_the_build123d_draft_angle_error():
    from agentcad.kernel.error_doctor import diagnose_text

    entry = diagnose_text(
        "DraftAngleError",
        "Draft operation failed. Use `err.face` and `err.problematic_shape` "
        "for more information.", "")
    assert entry is not None and entry["id"] == "draft_angle_too_large"


def test_a_floating_feature_reports_its_engaged_volume_exactly():
    """The escalation: when the fuse leaves a solid behind, the helper pays for
    the `&` probe (~2 ms, changelog 0149) and prints the number that names the
    failure — 0 mm^3 of engagement under a volume delta that looks like
    success."""
    from build123d import Plane

    from agentcad.toolkit import features

    part = _plate()
    above = Plane(origin=(0, 0, 30), x_dir=(1, 0, 0), z_dir=(0, 0, 1))
    _out, warning = features.rib(part, [(-40, 0), (40, 0)], 3.0, to=8.0,
                                 plane=above)
    assert "engages 0 mm^3" in warning


# ---------- R18: the "how much of it arrived" check, which ribs never ran

def _padded_plate():
    """A 100x100x10 plate (z in [-5, 5]) carrying a 40x40x10 pad on top, so
    the plane at z = 5 already has material standing on part of it."""
    from build123d import Box, Pos

    return Box(100, 100, 10) + Pos(0, 0, 5) * Box(40, 40, 10)


def test_a_boss_that_lands_partly_inside_existing_material_says_so():
    """**Regression.** `_fuse_warnings` grows a second check when it is given
    the `seed`: what the instances *contain* against what actually *arrived*.
    `patterns` passed the seed and got the check; `features.rib` and
    `features.boss` did not, so a rib or a boss half-buried in material it
    overlaps fused quietly and only the volume knew.
    """
    from build123d import Plane

    from agentcad.toolkit import features

    part = _padded_plate()
    seat = Plane(origin=(0, 0, 5), z_dir=(0, 0, 1))
    before = part.volume
    out, warning = features.boss(part, (25, 0), 20.0, 10.0, plane=seat)

    added = out.volume - before
    contained = math.pi * 10.0 ** 2 * 10.0
    assert added < contained - 1.0, (added, contained)
    assert warning is not None, "a boss that loses material fused in silence"
    assert "overlap each other or existing material" in warning


def test_a_rib_that_lands_partly_inside_existing_material_says_so():
    from build123d import Plane

    from agentcad.toolkit import features

    part = _padded_plate()
    seat = Plane(origin=(0, 0, 5), z_dir=(0, 0, 1))
    out, warning = features.rib(part, [(-40, 0), (40, 0)], 6.0, to=10.0,
                                plane=seat)

    assert out.volume < part.volume + 80 * 6.0 * 10.0
    assert warning is not None, "a rib that loses material fused in silence"
    assert "overlap each other or existing material" in warning


def test_a_boss_on_clear_stock_still_says_nothing():
    """The check must not fire on correct construction."""
    from build123d import Plane

    from agentcad.toolkit import features

    part = _padded_plate()
    seat = Plane(origin=(0, 0, 5), z_dir=(0, 0, 1))
    out, warning = features.boss(part, (40, 40), 12.0, 8.0, plane=seat)

    assert warning is None, warning
    assert out.volume == pytest.approx(
        part.volume + math.pi * 6.0 ** 2 * 8.0, rel=1e-6)
