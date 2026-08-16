"""`add_holes` — the hole-on-face script edit (PRD-010 FR14, slice 13).

The contract under test is the `tools_facemod.push_pull` one: the tool
*appends source*, so everything it writes has to be either a validated table
key or a number, the block has to be syntactically valid Python that rebuilds,
and two calls must compose rather than shadow each other.

The security half is not decoration. A crafted identifier interpolated into a
generated script put `import os` on line 2 of one once (PRD-009's last gotcha,
`sketch_emit._plane_header`), so every string that reaches the output here is
a key into a table this module owns, and every number goes through
`repr(float(...))`.
"""

from __future__ import annotations

import re

import pytest

from agentcad.core.tools import build_registry
from agentcad.core.tools_holes import (
    ADD_HOLES_MARKER,
    NAMED_PLANES,
    _FAMILIES,
)

from .conftest import make_test_service

PLATE_SCRIPT = '''\
from build123d import *

PARAMS = {"t": {"default": 10.0, "min": 2.0, "max": 40.0, "unit": "mm"}}

def build(p):
    with BuildPart() as part:
        Box(80, 60, p.t)
    return part.part
'''

CYL_SCRIPT = '''\
from build123d import *

PARAMS = {"r": {"default": 12.0, "min": 1.0, "max": 50.0, "unit": "mm"}}

def build(p):
    return Solid.make_cylinder(p.r, 20)
'''


@pytest.fixture
def demo(kernel, tmp_path):
    service = make_test_service(tmp_path / "projects", kernel)
    service.create_project("demo")
    service.create_part("demo", "plate", script=PLATE_SCRIPT)
    return service


def _call(service, **params):
    return build_registry(service).call("add_holes",
                                        {"project": "demo", **params})


def _top_face(service, part_id="plate"):
    """The +Z planar face ordinal, found the way the GUI finds it."""
    registry = build_registry(service)
    first = registry.call("face_info", {"project": "demo", "part_id": part_id,
                                        "face_index": 0})
    assert "error" not in first, first
    best, best_z = None, None
    for i in range(first["n_faces"]):
        info = first if i == 0 else registry.call(
            "face_info", {"project": "demo", "part_id": part_id,
                          "face_index": i})
        if not info["planar"] or info["normal"][2] < 0.999:
            continue
        if best is None or info["center"][2] > best_z:
            best, best_z = i, info["center"][2]
    assert best is not None
    return best


# ------------------------------------------------------- the appended block

def test_the_appended_block_is_valid_python_and_rebuilds(demo):
    before = demo.get_metrics("demo", "plate")["volume_mm3"]
    res = _call(demo, part_id="plate", points=[[20, 10], [-20, 10]],
                family="clearance", size="M5")
    assert res.get("ok") is True, res
    assert res["count"] == 2 and res["size"] == "M5" and res["fit"] == "medium"

    script = demo.store.read_script("demo", "plate")
    assert script.count(ADD_HOLES_MARKER) == 1
    compile(script, "plate.py", "exec")          # syntactically valid Python
    assert "holes.clearance(" in script
    assert "_agentcad_prev_build_0" in script

    # ISO 273 medium for M5 is 5.5 mm; two through holes in 10 mm of stock.
    removed = before - res["metrics"]["volume_mm3"]
    assert removed == pytest.approx(2 * 3.14159265 * 2.75 ** 2 * 10, rel=1e-3)


def test_the_build_result_carries_the_hole_records(demo):
    res = _call(demo, part_id="plate", points=[[0, 0]], family="tapped",
                size="M6", depth=8)
    assert res.get("ok") is True, res
    holes = res.get("holes")
    assert isinstance(holes, list) and len(holes) == 1
    assert holes[0]["family"] == "tapped"
    assert holes[0]["designation"].startswith("M6×1")
    assert holes[0]["count"] == 1


def test_two_calls_compose_and_do_not_shadow_each_other(demo):
    assert _call(demo, part_id="plate", points=[[20, 10]],
                 family="clearance", size="M5").get("ok") is True
    second = _call(demo, part_id="plate", points=[[-20, -10]],
                   family="clearance", size="M5")
    assert second.get("ok") is True, second
    script = demo.store.read_script("demo", "plate")
    assert script.count(ADD_HOLES_MARKER) == 2
    assert "_agentcad_prev_build_0" in script
    assert "_agentcad_prev_build_1" in script
    # Both holes survived: a shadowed previous build would lose the first.
    assert len(second["holes"]) == 2


def test_a_named_plane_stays_a_name_in_the_script(demo):
    """A name is a *predicate* re-evaluated every rebuild — the stable
    reference. Freezing it into a literal basis would reintroduce the very
    staleness the face path has to carry a caveat about."""
    res = _call(demo, part_id="plate", points=[[0, 0]], family="clearance",
                size="M5", plane="bottom")
    assert res.get("ok") is True, res
    script = demo.store.read_script("demo", "plate")
    assert "plane='bottom'" in script
    assert "_agentcad_Plane" not in script


def test_the_named_plane_list_matches_the_toolkits(demo):
    """This module cannot import `toolkit.holes` (it imports build123d), so
    the six names are duplicated. This is the assertion that keeps the copy
    honest."""
    from agentcad.toolkit import holes

    assert set(NAMED_PLANES) == set(holes._NAMED_PLANES)


# ---------------------------------------------------------- the picked face

def test_a_picked_face_emits_the_sketch_plane_basis_with_the_caveat(demo):
    top = _top_face(demo)
    registry = build_registry(demo)
    basis = registry.call("sketch_plane", {"project": "demo",
                                           "part_id": "plate",
                                           "face_index": top})
    assert "error" not in basis, basis

    res = _call(demo, part_id="plate", points=[[10, 5]], family="clearance",
                size="M5", face_index=top)
    assert res.get("ok") is True, res
    assert res["face_index"] == top and res["plane"] == "face"

    script = demo.store.read_script("demo", "plate")
    compile(script, "plate.py", "exec")
    assert "_agentcad_Plane(origin=" in script
    # THE emitted basis is `sketch_plane`'s, component for component.
    for key, arg in (("origin", "origin"), ("x_dir", "x_dir"),
                     ("normal", "z_dir")):
        rendered = f"{arg}=({', '.join(repr(float(c)) for c in basis[key])})"
        assert rendered in script, (rendered, script)
    # and the renumbering caveat travels with the code
    assert "mesh-order ordinals" in script


def test_a_non_planar_face_is_a_validation_error_and_touches_nothing(demo):
    demo.create_part("demo", "cyl", script=CYL_SCRIPT)
    registry = build_registry(demo)
    side = next(i for i in range(3)
                if not registry.call("face_info",
                                     {"project": "demo", "part_id": "cyl",
                                      "face_index": i})["planar"])
    res = _call(demo, part_id="cyl", points=[[0, 0]], family="clearance",
                size="M5", face_index=side)
    assert res["error"]["type"] == "validation_error"
    assert ADD_HOLES_MARKER not in demo.store.read_script("demo", "cyl")


def test_an_out_of_range_face_is_a_validation_error(demo):
    res = _call(demo, part_id="plate", points=[[0, 0]], family="clearance",
                size="M5", face_index=999)
    assert res["error"]["type"] == "validation_error"
    assert "out of range" in res["error"]["message"]


def test_plane_and_face_index_together_are_refused(demo):
    res = _call(demo, part_id="plate", points=[[0, 0]], family="clearance",
                size="M5", plane="top", face_index=0)
    assert res["error"]["type"] == "validation_error"


# ------------------------------------------------------------- the refusals

@pytest.mark.parametrize("bad", [
    {"family": "slotted", "size": "M5"},              # not a family
    {"family": "clearance", "size": "M4.5"},          # not in ISO 273
    {"family": "clearance", "size": "M5", "fit": "snug"},
    {"family": "clearance", "size": "M5", "std": "din"},
    {"family": "clearance", "size": "M5", "depth": -3},
    {"family": "drilled", "size": "not-a-number"},
])
def test_a_request_the_tables_cannot_answer_is_refused(demo, bad):
    res = _call(demo, part_id="plate", points=[[0, 0]], **bad)
    assert res["error"]["type"] == "validation_error", res
    assert ADD_HOLES_MARKER not in demo.store.read_script("demo", "plate")


@pytest.mark.parametrize("points", [
    [], "M5", [[0]], [[0, 0, 0]], [[0, "x"]], [[0, 0], [1, float("inf")]],
])
def test_bad_points_are_refused(demo, points):
    """`invalid_arguments` is the registry's schema layer answering first for
    a wholly wrong type; everything it lets through this tool refuses itself.
    Both are structured caller errors, and neither writes a line."""
    res = _call(demo, part_id="plate", points=points, family="clearance",
                size="M5")
    assert res["error"]["type"] in ("validation_error", "invalid_arguments"), res
    assert ADD_HOLES_MARKER not in demo.store.read_script("demo", "plate")


def test_a_crafted_identifier_cannot_reach_the_generated_source(demo):
    """The PRD-009 gotcha, as a test. Every hostile shape a caller can send —
    a size, a fit, a standard, a plane name, a coordinate — is refused before
    the emitter runs, because each one is a key into a table or a float."""
    payload = "M5'); import os; os.system('true'); ('"
    for field in ("size", "fit", "std", "plane"):
        res = _call(demo, part_id="plate", points=[[0, 0]],
                    family="clearance",
                    **{"size": "M5", field: payload})
        assert res["error"]["type"] == "validation_error", (field, res)
    res = _call(demo, part_id="plate", points=[[payload, 0]],
                family="clearance", size="M5")
    assert res["error"]["type"] == "validation_error"
    res = _call(demo, part_id="plate", points=[[0, 0]], family=payload,
                size="M5")
    assert res["error"]["type"] == "validation_error"
    script = demo.store.read_script("demo", "plate")
    assert "import os" not in script and ADD_HOLES_MARKER not in script


def test_an_unknown_part_is_a_structured_error(demo):
    res = _call(demo, part_id="missing", points=[[0, 0]], family="clearance",
                size="M5")
    assert res["error"]["type"] == "notfound_error", res


def test_a_numeric_string_coordinate_is_parsed_not_interpolated(demo):
    """A JSON client that sends `"20"` gets a hole at 20 mm, and the script
    gets `20.0` — the emitter writes `repr(float(...))`, never the caller's
    text, so leniency about the input type costs nothing at the output."""
    res = _call(demo, part_id="plate", points=[["20", "10"]],
                family="clearance", size="M5")
    assert res.get("ok") is True, res
    assert res["points"] == [[20.0, 10.0]]
    assert "[(20.0, 10.0)]" in demo.store.read_script("demo", "plate")


# ----------------------------------------------------------- every family

@pytest.mark.parametrize("family", sorted(_FAMILIES))
def test_every_family_emits_a_block_that_rebuilds(demo, family):
    # `drilled` has no table row, so its "size" is a diameter in millimetres —
    # a string here only because the tool's schema types the field once.
    size = "8" if family == "drilled" else "M6"
    depth = 6 if family in ("tapped",) else None
    params = {"part_id": "plate", "points": [[0, 0]], "family": family,
              "size": size}
    if depth is not None:
        params["depth"] = depth
    res = _call(demo, **params)
    assert res.get("ok") is True, (family, res)
    script = demo.store.read_script("demo", "plate")
    compile(script, "plate.py", "exec")
    helper = "drill" if family == "drilled" else family
    assert f"holes.{helper}(" in script
    assert res["metrics"]["volume_mm3"] > 0


# ------------------------------------- R2: the saved-previous-build name

def _push_pull(service, **params):
    return build_registry(service).call("push_pull",
                                        {"project": "demo", **params})


def test_add_holes_and_push_pull_do_not_mint_the_same_global(demo):
    """**Regression.** Both packs saved the previous `build` under
    `_agentcad_prev_build_{n}` with `n` counted off *their own* marker, so the
    first block of each pack claimed `_agentcad_prev_build_0`. The name is
    resolved as a module global at *call* time, so the second block rebound it
    to the first block's wrapper and `build` recursed into itself —
    `RecursionError`, persisted, with only a toast.
    """
    assert _call(demo, part_id="plate", points=[[20, 10]],
                 family="clearance", size="M5").get("ok") is True
    res = _push_pull(demo, part_id="plate", face_index=0, distance_mm=2.0)
    assert res.get("ok") is True, res
    script = demo.store.read_script("demo", "plate")
    names = re.findall(r"^_agentcad_prev_build_\w+ = build$", script,
                       flags=re.MULTILINE)
    assert len(names) == len(set(names)), script


def test_push_pull_then_add_holes_do_not_mint_the_same_global(demo):
    """Order-symmetric: the collision does not depend on who goes first."""
    assert _push_pull(demo, part_id="plate", face_index=0,
                      distance_mm=2.0).get("ok") is True
    res = _call(demo, part_id="plate", points=[[20, 10]],
                family="clearance", size="M5")
    assert res.get("ok") is True, res
    script = demo.store.read_script("demo", "plate")
    names = re.findall(r"^_agentcad_prev_build_\w+ = build$", script,
                       flags=re.MULTILINE)
    assert len(names) == len(set(names)), script


def test_deleting_a_middle_block_does_not_make_the_next_name_collide(demo):
    """The marker says "edit or remove freely", so removal is a *supported*
    edit. A per-marker count regresses onto a name that is still live."""
    for uv in ([20, 10], [0, 10], [-20, 10]):
        assert _call(demo, part_id="plate", points=[uv],
                     family="clearance", size="M5").get("ok") is True
    script = demo.store.read_script("demo", "plate")
    blocks = script.split(ADD_HOLES_MARKER)
    # drop the middle block, keeping the head and the first and last blocks
    trimmed = blocks[0] + ADD_HOLES_MARKER + blocks[1] \
        + ADD_HOLES_MARKER + blocks[3]
    demo.store.write_script("demo", "plate", trimmed)
    assert demo._rebuild("demo", "plate").get("ok") is True

    res = _call(demo, part_id="plate", points=[[0, -10]],
                family="clearance", size="M5")
    assert res.get("ok") is True, res
    script = demo.store.read_script("demo", "plate")
    names = re.findall(r"^_agentcad_prev_build_\w+ = build$", script,
                       flags=re.MULTILINE)
    assert len(names) == len(set(names)), script
    # the two surviving blocks plus the new one; a self-recursing or shadowed
    # wrapper loses holes (or blows the stack)
    assert len(res["holes"]) == 3


# -------------------------------------------- R10: a diameter must be > 0

@pytest.mark.parametrize("size", ["0", "-4", "0.0", "-0.5"])
def test_a_non_positive_drilled_diameter_is_refused(demo, size):
    """**Regression.** `depth` had a `> 0` guard and `size` did not, so a
    drilled diameter of 0 or less was written into the script and only *then*
    failed in the kernel — and the write is not undone by `update_part`."""
    before = demo.store.read_script("demo", "plate")
    res = _call(demo, part_id="plate", points=[[0, 0]], family="drilled",
                size=size)
    assert res["error"]["type"] == "validation_error", res
    assert "> 0" in res["error"]["message"]
    assert demo.store.read_script("demo", "plate") == before
    assert ADD_HOLES_MARKER not in demo.store.read_script("demo", "plate")


# ------------------------------- R2b: a block that does not build is undone

def test_a_generated_block_that_fails_to_build_is_rolled_back(demo):
    """A tool appends source the user never typed. `update_part` writes before
    it rebuilds and deliberately does not roll back — a human must be able to
    save a broken script of their own. Generated text is the other case: if it
    does not build, the pack puts the script back rather than leaving the user
    holding an unbuildable part they did not author.
    """
    before = demo.store.read_script("demo", "plate")
    good = demo.get_metrics("demo", "plate")["volume_mm3"]
    # push/pull the top face 1000 mm *into* a 10 mm plate: the face passes
    # through the far side and the solid is consumed.
    res = _push_pull(demo, part_id="plate", face_index=0, distance_mm=-1000.0)
    assert res.get("ok") is False, res
    assert res.get("rolled_back") is True and res.get("restored") is True
    assert demo.store.read_script("demo", "plate") == before
    assert demo.get_metrics("demo", "plate")["volume_mm3"] == good


# ------------------------------------- the allocator itself, in isolation

def test_next_build_alias_never_returns_a_name_the_script_already_binds():
    """The one guarantee the allocator makes, stated directly.

    Numbering densely from the top is a readability nicety; being absent from
    the script is the correctness property, and it has to survive names
    written by another pack and gaps left by a deleted block.
    """
    from agentcad.core.script_blocks import (
        existing_build_aliases,
        next_build_alias,
    )

    assert next_build_alias("def build(p):\n    return None\n") == \
        "_agentcad_prev_build_0"

    # a gap left by a deleted middle block: 1 is free, 2 is live
    gappy = "_agentcad_prev_build_0 = build\n_agentcad_prev_build_2 = build\n"
    alias = next_build_alias(gappy)
    assert alias not in existing_build_aliases(gappy)
    assert alias == "_agentcad_prev_build_1"

    # and the allocated name is never handed out twice
    script = ""
    seen = set()
    for _ in range(5):
        alias = next_build_alias(script)
        assert alias not in seen
        seen.add(alias)
        script += f"{alias} = build\n"


def test_a_name_is_taken_even_where_it_only_appears_in_a_comment():
    """A textual scan, deliberately: the script may not parse — that is
    exactly the state a user repairing one is in — and a name mentioned
    anywhere is a name not worth reusing."""
    from agentcad.core.script_blocks import next_build_alias

    assert next_build_alias("# see _agentcad_prev_build_0 above\n") == \
        "_agentcad_prev_build_1"
    assert next_build_alias("def build(p:  # unclosed\n") == \
        "_agentcad_prev_build_0"


# ------------------------------------------- R5: the persisted sidecar

def _record(**overrides) -> dict:
    """A record shaped exactly as `toolkit.holes` produces one, so the sidecar
    tests below fail on the field they change and on nothing else."""
    from agentcad.toolkit import hole_standards

    record = {
        "id": "h0", "family": "clearance", "standard": "iso", "size": "M5",
        "fit": "medium", "d": 5.5, "tap": None, "cbore": None, "csk": None,
        "provenance": hole_standards.merge_provenance(
            hole_standards.clearance("M5")),
        "count": 1, "positions": [[0.0, 0.0]], "centers": [[0.0, 0.0, 5.0]],
        "axis": [0.0, 0.0, -1.0],
        "plane": {"origin": [0.0, 0.0, 5.0], "z_dir": [0.0, 0.0, 1.0],
                  "x_dir": [1.0, 0.0, 0.0]},
        "depth_mm": None, "thru": True, "removed_mm3": 237.6,
        "instances": [], "verify": "bbox", "dropped": [], "pattern": None,
    }
    record.update(overrides)
    record["designation"] = hole_standards.designation_for_record(record)
    record["designation_base"] = hole_standards.designation_for_record(
        {**record, "thru": True, "depth_mm": None})
    assert hole_standards.validate_record(record) is None
    return record


def _sidecar(**overrides) -> dict:
    from agentcad.core.tools_holes import HOLES_SIDECAR_VERSION

    payload = {"version": HOLES_SIDECAR_VERSION, "cache_key": "abc",
               "holes": [_record()], "warnings": [], "dropped": 0}
    payload.update(overrides)
    return payload


def test_a_well_formed_sidecar_is_used():
    from agentcad.core.tools_holes import _sidecar_problem

    assert _sidecar_problem(_sidecar(), "abc") is None
    assert _sidecar_problem(
        _sidecar(holes=None), "abc") is None            # declares none
    assert _sidecar_problem(
        _sidecar(holes=[], dropped=2, warnings=["lost them"]), "abc") is None


@pytest.mark.parametrize("payload,fragment", [
    # The embedded key has always been written and was never compared, so a
    # `.holes.json` describing a DIFFERENT build of this part was served as if
    # it described this one.
    (dict(cache_key="other"), "is not the key being read"),
    # The impossible fifth state: `holes: []` means "records were created and
    # did not arrive", which cannot coexist with `dropped: 0` and no warning.
    # It used to be accepted and reported as exactly that, silently.
    (dict(holes=[], dropped=0), "cannot have been harvested"),
    (dict(holes=[], dropped=2, warnings=[]), "no warning says so"),
    (dict(holes=None, dropped=3, warnings=["x"]), "two different answers"),
    (dict(warnings="boom"), "must be a list of strings"),
    (dict(dropped=-1), "must be an integer >= 0"),
    (dict(dropped=True), "must be an integer >= 0"),
    (dict(holes="nope"), "not a list or null"),
    (dict(version=0), "is not"),
])
def test_an_inconsistent_sidecar_is_discarded_and_recomputed(payload, fragment):
    """`_read_sidecar` checked the version and that `holes` was list-or-null,
    and nothing else — not the cache key it stores, not the records, not the
    four-state invariant its own docstring declares. Recomputing costs one
    keyed kernel round trip; accepting costs a wrong drawing."""
    from agentcad.core.tools_holes import _sidecar_problem

    problem = _sidecar_problem(_sidecar(**payload), "abc")
    assert problem is not None and fragment in problem


@pytest.mark.parametrize("record,fragment", [
    ({"nope": 1}, "missing required key"),
    ("not a dict", "not a dict"),
])
def test_a_stored_record_goes_through_the_shared_validator(record, fragment):
    """One contract, three readers. A record that the worker's harvest would
    raise on must not reach a drawing by way of the cache instead."""
    from agentcad.core.tools_holes import _sidecar_problem

    problem = _sidecar_problem(_sidecar(holes=[record]), "abc")
    assert problem is not None and fragment in problem


def test_a_stored_record_whose_designation_drifted_is_discarded():
    """The cross-check that makes the contract more than a key list: a stored
    callout has to be what the stored numbers spell."""
    from agentcad.core.tools_holes import _sidecar_problem

    drifted = _record()
    drifted["designation"] = "M8×1.25 - 6H ↧12"
    problem = _sidecar_problem(_sidecar(holes=[drifted]), "abc")
    assert "own numbers spell" in problem


def test_the_sidecar_file_is_unlinked_when_it_cannot_be_used(tmp_path):
    """Discarded means gone: leaving it would re-pay the validation on every
    read and leave a file a later reader might treat differently."""
    import json

    from agentcad.core.tools_holes import _read_sidecar

    path = tmp_path / "abc.holes.json"
    path.write_text(json.dumps(_sidecar(cache_key="other")), encoding="utf-8")
    assert _read_sidecar(path, "abc") is None
    assert not path.exists()

    path.write_text(json.dumps(_sidecar()), encoding="utf-8")
    assert _read_sidecar(path, "abc") is not None
    assert path.exists()


# ------------------------------- provenance out through the tool surface

def test_add_holes_echoes_the_provenance_of_the_row_it_drilled(demo):
    """**Regression.** `add_holes` looked the row up and kept `size`, throwing
    the provenance away — so an agent drilling the single-sourced ISO 10642
    seat or the *adjudicated* ANSI `#8 normal` clearance hole was told neither,
    and the label the tables carry stopped at the table.
    """
    res = _call(demo, part_id="plate", points=[[0, 0]],
                family="countersink", size="M8")
    assert res.get("ok") is True, res
    provenance = res["provenance"]
    assert provenance["corroborated"] is False
    assert any("10642" in text for text in provenance["sources"])

    res = _call(demo, part_id="plate", points=[[30, 0]], family="clearance",
                size="#8", std="ansi", fit="normal")
    provenance = res["provenance"]
    assert provenance["corroborated"] is False
    assert provenance["conflicts"] and "0.190" in provenance["conflicts"][0]


def test_add_holes_claims_no_provenance_for_a_drilled_hole(demo):
    """A stated millimetre has no publication behind it, and saying it does
    would put a standard's name on a number the standard never supplied."""
    res = _call(demo, part_id="plate", points=[[0, 0]], family="drilled",
                size="18.0")
    assert res.get("ok") is True, res
    assert "provenance" not in res and res["diameter_mm"] == 18.0


def test_the_echo_and_the_record_never_disagree_about_provenance(demo):
    """**Regression.** The echo looked up ONE row and reported it, which for a
    seat family is the fastener HEAD row — so the flagship disputed ANSI
    `#8 normal` clearance cell reported `corroborated: True, conflicts: 0` to
    the agent while the record the same call was about to write said `False`
    with a conflict. Two surfaces describing one hole must not disagree.

    (The inline comment there claimed the echo covered "the clearance row
    only", which was the opposite of what the code did — the kind of comment
    that stops a reader from checking.)
    """
    from build123d import Box

    from agentcad.core.tools_holes import _hole_call_args
    from agentcad.toolkit import holes

    for family in ("counterbore", "countersink", "clearance"):
        _args, echo = _hole_call_args(family, "#8", "normal", "ansi", None)
        _out, records, _warning = getattr(holes, family)(
            Box(80, 80, 30), [(0, 0)], "#8", std="ansi", fit="normal")
        assert echo["provenance"] == records[0]["provenance"], family
        assert echo["provenance"]["corroborated"] is False
        assert echo["provenance"]["conflicts"], family
