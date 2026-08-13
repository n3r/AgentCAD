"""Round-trip spec persistence and divergence detection (PRD-009 slice 13, FR10).

A sketch emitted into a part script has to **reopen with its constraint spec
intact**, and the one thing it must never do is silently overwrite a hand
edit. The spec therefore rides in the script itself as a structured block —
the `push_pull` precedent (`core/tools_facemod.PUSH_PULL_MARKER`), not a
sidecar file — with a hash over the emitted code beneath it:

    # --- agentcad sketch "profile" (auto-generated; edit or remove freely) ---
    # agentcad-sketch-spec: {"v": 1, "entities": {...}, "constraints": [...]}
    # agentcad-sketch-hash: sha256:...
    def sketch_profile():
        ...
    # --- end agentcad sketch "profile" ---

**The code is the source of truth for geometry; the spec block is
provenance.** So the three outcomes of a parse are `ok` (hash matches),
`diverged` (the code was hand-edited — the sketcher opens read-only and asks)
and `unverified` (the spec is unreadable or unhashed — "we cannot tell", never
rendered as "no sketch"). Everything below is asserted on those three, plus
the two properties slices 11-12 handed on: **`plane` and `construction` must
survive the trip**, because a sketch-on-face without its basis is meaningless
and construction geometry that re-parses as real geometry would emit the
part's own boundary back into it.

This module is deliberately OCP-free and fast — the rebuild-through-the-kernel
half of emission is `tests/test_sketch_emit.py`'s job.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from agentcad.core import tools_sketch
from agentcad.core.sketch_emit import (
    SPEC_PREFIX, SPEC_VERSION, EmitError, block_hash, emit, next_name,
    parse_blocks,
)
from agentcad.core.tools import ToolRegistry, build_registry
from agentcad.server.app import create_app
from agentcad.toolkit.sketch import solve_sketch

from .conftest import make_test_service

ENTITY_KINDS = ("points", "lines", "circles", "arcs", "ellipses", "splines",
                "slots")

# A plane in the shape `sketch_plane` returns one, without needing a kernel:
# the emitter reads these keys and nothing else.
FACE_PLANE = {
    "origin": [30.0, 30.0, 10.0], "x_dir": [1.0, 0.0, 0.0],
    "y_dir": [0.0, 1.0, 0.0], "normal": [0.0, 0.0, 1.0],
    "face_index": 5, "part": "build(p)",
}


# --------------------------------------------------------------------------
# specs
# --------------------------------------------------------------------------
def square_spec() -> dict:
    """A closed line chain with non-round junctions."""
    return {
        "points": [{"name": "a", "x": 0.0, "y": 0.0, "fixed": True},
                   {"name": "b", "x": 30.0, "y": 1.0},
                   {"name": "c", "x": 31.0, "y": 25.0},
                   {"name": "d", "x": 1.0, "y": 24.0}],
        "lines": [{"name": "ab", "p1": "a", "p2": "b"},
                  {"name": "bc", "p1": "b", "p2": "c"},
                  {"name": "cd", "p1": "c", "p2": "d"},
                  {"name": "da", "p1": "d", "p2": "a"}],
        "constraints": [
            {"type": "horizontal", "ln": "ab"},
            {"type": "vertical", "ln": "bc"},
            {"type": "horizontal", "ln": "cd"},
            {"type": "vertical", "ln": "da"},
            {"type": "distance", "p": "a", "q": "b", "d": 30.7183},
            {"type": "distance", "p": "b", "q": "c", "d": 24.3319},
        ],
    }


def construction_spec() -> dict:
    """The same square plus a construction line the profile is symmetric about.

    The construction line constrains and must **not** be emitted — slice 12's
    rule, which only holds through a round trip if the flag survives the JSON.
    """
    spec = square_spec()
    spec["points"] += [{"name": "m", "x": 0.0, "y": 12.0},
                       {"name": "n", "x": 31.0, "y": 12.0}]
    spec["lines"] += [{"name": "axis", "p1": "m", "p2": "n",
                       "construction": True}]
    return spec


def curvy_spec() -> dict:
    """Every entity kind the round trip has to carry through JSON."""
    return {
        "points": [{"name": "cL", "x": 0.0, "y": 0.0, "fixed": True},
                   {"name": "cR", "x": 40.0, "y": 0.5},
                   {"name": "ce", "x": 5.0, "y": 40.0},
                   {"name": "s1", "x": -20.0, "y": 30.0},
                   {"name": "s2", "x": -10.0, "y": 36.0},
                   {"name": "s3", "x": 0.0, "y": 30.0}],
        "arcs": [{"name": "L", "center": "cL", "r": 18.3691,
                  "start_deg": 90.0, "end_deg": 270.0},
                 {"name": "R", "center": "cR", "r": 7.2143,
                  "start_deg": 270.0, "end_deg": 450.0}],
        "ellipses": [{"name": "e1", "center": "ce", "a": 12.3457, "b": 5.4321,
                      "rotation": 17.0}],
        "splines": [{"name": "sp", "points": ["s1", "s2", "s3"]}],
        "slots": [{"name": "sl", "c1": "cL", "c2": "cR", "width": 9.4271}],
        "lines": [{"name": "top", "p1": "L.start", "p2": "R.end"},
                  {"name": "bot", "p1": "R.start", "p2": "L.end"}],
        "constraints": [
            {"type": "tangent", "a": "top", "b": "L"},
            {"type": "tangent", "a": "top", "b": "R"},
        ],
    }


def solved(spec: dict) -> dict:
    out = solve_sketch(spec)
    assert out["ok"] is True, out["diagnostics"]
    return out


def block_of(spec: dict, name: str = "profile", **kw) -> str:
    return emit(solved(spec), spec, persist=name, **kw)["code"]


def entities_of(spec: dict) -> dict:
    return {k: spec.get(k, []) for k in ENTITY_KINDS}


SCRIPT_HEAD = '''\
import build123d as b3d
from build123d import *

PARAMS = {"t": {"default": 3.0, "min": 1.0, "max": 10.0, "unit": "mm"}}


def build(p):
    return extrude(sketch_profile(), amount=p.t)
'''


# --------------------------------------------------------------------------
# the block
# --------------------------------------------------------------------------
def test_a_persisted_block_carries_the_marker_the_spec_and_the_hash():
    code = block_of(square_spec())
    lines = [l for l in code.splitlines() if l.strip()]
    assert lines[0] == ('# --- agentcad sketch "profile" (auto-generated; '
                        'edit or remove freely) ---')
    assert lines[1].startswith("# agentcad-sketch-spec: ")
    assert lines[2].startswith("# agentcad-sketch-hash: sha256:")
    assert lines[-1] == '# --- end agentcad sketch "profile" ---'
    assert "def sketch_profile():" in code

    spec = json.loads(lines[1].split("# agentcad-sketch-spec: ", 1)[1])
    assert spec["v"] == SPEC_VERSION
    assert [p["name"] for p in spec["entities"]["points"]] == ["a", "b", "c",
                                                              "d"]
    assert len(spec["constraints"]) == 6


def test_persistence_is_opt_in_and_the_old_emission_is_byte_identical():
    """FR3-adjacent: every caller that does not ask for a block must get
    exactly the bytes it got before slice 13."""
    spec = square_spec()
    sol = solved(spec)
    plain = emit(sol, spec)["code"]
    assert "agentcad-sketch-spec" not in plain
    assert plain == emit(sol, spec, persist=None)["code"]
    assert plain.startswith("\n\n# --- agentcad sketch (auto-generated) ---")


def test_the_block_name_becomes_the_function_name():
    code = block_of(square_spec(), "profile2")
    assert "def sketch_profile2():" in code
    assert 'agentcad sketch "profile2"' in code


def test_a_block_name_that_is_not_an_identifier_is_refused():
    sol = solved(square_spec())
    for bad in ("two words", "1st", "pro-file", "profile.a", ""):
        with pytest.raises(EmitError):
            emit(sol, square_spec(), persist=bad)


# --------------------------------------------------------------------------
# parse
# --------------------------------------------------------------------------
def test_a_parsed_block_reports_ok_with_the_spec_and_the_code():
    script = SCRIPT_HEAD + block_of(square_spec())
    blocks = parse_blocks(script)
    assert len(blocks) == 1
    b = blocks[0]
    assert b["name"] == "profile"
    assert b["status"] == "ok"
    assert b["spec"]["constraints"] == square_spec()["constraints"]
    assert "def sketch_profile():" in b["code"]
    assert script.splitlines()[b["start_line"] - 1].startswith(
        '# --- agentcad sketch "profile"')
    assert script.splitlines()[b["end_line"] - 1] == \
        '# --- end agentcad sketch "profile" ---'


def test_a_script_with_no_block_parses_to_nothing():
    assert parse_blocks(SCRIPT_HEAD) == []
    assert parse_blocks("") == []


def test_emit_parse_resolve_reemit_is_byte_identical():
    """The round trip, as bytes. Anything that reorders a dict, reformats a
    float or drops a default breaks here rather than in a user's script."""
    first = block_of(square_spec())
    block = parse_blocks(SCRIPT_HEAD + first)[0]
    spec = {**block["spec"]["entities"],
            "constraints": block["spec"]["constraints"]}
    again = emit(solved(spec), spec, persist=block["name"])["code"]
    assert again == first


def test_the_parsed_spec_solves_to_the_same_coordinates():
    """Reopening must land on the same branch, not merely on *a* solution."""
    spec = square_spec()
    original = solved(spec)
    block = parse_blocks(block_of(spec))[0]
    reopened = solve_sketch({**block["spec"]["entities"],
                             "constraints": block["spec"]["constraints"]})
    for name, p in original["points"].items():
        assert reopened["points"][name]["x"] == pytest.approx(p["x"], abs=1e-9)
        assert reopened["points"][name]["y"] == pytest.approx(p["y"], abs=1e-9)


def test_every_entity_kind_survives_the_round_trip():
    spec = curvy_spec()
    first = block_of(spec)
    block = parse_blocks(first)[0]
    ents = block["spec"]["entities"]
    assert [a["name"] for a in ents["arcs"]] == ["L", "R"]
    assert [e["name"] for e in ents["ellipses"]] == ["e1"]
    assert ents["splines"][0]["points"] == ["s1", "s2", "s3"]
    assert ents["slots"][0]["width"] == pytest.approx(9.4271)
    respec = {**ents, "constraints": block["spec"]["constraints"]}
    assert emit(solved(respec), respec, persist=block["name"])["code"] == first


# --------------------------------------------------------------------------
# what slices 11-12 handed on: the plane and the construction flag
# --------------------------------------------------------------------------
def test_the_plane_survives_the_round_trip():
    """A sketch-on-face that reopens without its basis is meaningless: the
    coordinates are in a plane nobody recorded."""
    spec = {**square_spec(), "plane": FACE_PLANE}
    first = block_of(spec)
    assert "agentcad sketch on face 5 of build(p)" in first
    assert "BuildSketch(Plane(origin=" in first

    block = parse_blocks(first)[0]
    assert block["spec"]["plane"] == FACE_PLANE
    respec = {**block["spec"]["entities"],
              "constraints": block["spec"]["constraints"],
              "plane": block["spec"]["plane"]}
    assert emit(solved(respec), respec, persist=block["name"])["code"] == first
    assert "mesh-order ordinals" in first        # the caveat rides along


def test_construction_geometry_survives_the_round_trip():
    spec = construction_spec()
    first = block_of(spec)
    # the construction line is not emitted as geometry...
    assert first.count("Polyline(") + first.count("Line(") >= 1
    block = parse_blocks(first)[0]
    axis = [l for l in block["spec"]["entities"]["lines"] if l["name"] == "axis"]
    # ...but it IS in the spec, still flagged
    assert axis and axis[0]["construction"] is True
    respec = {**block["spec"]["entities"],
              "constraints": block["spec"]["constraints"]}
    resolved = solved(respec)
    assert "axis" in resolved["construction"]
    assert emit(resolved, respec, persist=block["name"])["code"] == first


# --------------------------------------------------------------------------
# divergence
# --------------------------------------------------------------------------
def test_a_hand_edit_to_the_code_is_divergence_and_the_spec_is_not_overwritten():
    script = SCRIPT_HEAD + block_of(square_spec())
    edited = script.replace("30.7183", "35.0")
    assert edited != script

    block = parse_blocks(edited)[0]
    assert block["status"] == "diverged"
    assert block["spec"] is not None          # still readable, still ours
    assert block["hash"] != block["computed_hash"]
    assert "hand" in block["message"] or "edited" in block["message"]
    # parsing is a read: the script is untouched
    assert parse_blocks(edited)[0]["code"] in edited


def test_an_edit_outside_the_block_is_not_divergence():
    script = SCRIPT_HEAD + block_of(square_spec()) + "\n# a comment of my own\n"
    assert parse_blocks(script)[0]["status"] == "ok"
    moved = "# header\n" + script
    assert parse_blocks(moved)[0]["status"] == "ok"


def test_the_hash_ignores_line_endings_and_trailing_whitespace():
    """An editor that normalises whitespace on save is not a hand edit."""
    script = SCRIPT_HEAD + block_of(square_spec())
    crlf = script.replace("\n", "\r\n")
    assert parse_blocks(crlf)[0]["status"] == "ok"
    padded = "\n".join(l + "   " for l in script.splitlines())
    assert parse_blocks(padded)[0]["status"] == "ok"


def test_block_hash_is_stable_and_prefixed():
    h = block_hash("def sketch_profile():\n    pass\n")
    assert h.startswith("sha256:") and len(h) == len("sha256:") + 64
    assert h == block_hash("def sketch_profile():   \n    pass")


# --------------------------------------------------------------------------
# unverified — "we cannot tell" is never "nothing here"
# --------------------------------------------------------------------------
def test_a_corrupt_spec_line_is_unverified_and_the_code_is_untouched():
    script = SCRIPT_HEAD + block_of(square_spec())
    corrupt = script.replace('"constraints"', '"constraint', 1)
    block = parse_blocks(corrupt)[0]
    assert block["status"] == "unverified"
    assert block["spec"] is None
    assert "def sketch_profile():" in block["code"]
    assert block["code"] in corrupt
    assert "unreadable" in block["message"]


def test_a_missing_hash_line_is_unverified_not_ok():
    script = block_of(square_spec())
    without = "\n".join(l for l in script.splitlines()
                        if not l.startswith("# agentcad-sketch-hash:"))
    block = parse_blocks(without)[0]
    assert block["status"] == "unverified"
    assert block["spec"] is not None
    assert block["computed_hash"] is not None and block["hash"] is None


def test_a_block_with_no_end_marker_is_unverified():
    script = block_of(square_spec())
    truncated = "\n".join(l for l in script.splitlines()
                          if not l.startswith("# --- end agentcad sketch"))
    block = parse_blocks(truncated)[0]
    assert block["status"] == "unverified"
    assert "end" in block["message"]


def test_an_unknown_spec_version_is_unverified():
    script = block_of(square_spec()).replace('{"v": 2,', '{"v": 99,', 1)
    block = parse_blocks(script)[0]
    assert block["status"] == "unverified"
    assert "version" in block["message"]
    assert block["spec"] is None


def test_a_spec_that_is_not_an_object_is_unverified():
    script = block_of(square_spec())
    broken = "\n".join(
        "# agentcad-sketch-spec: [1, 2, 3]"
        if l.startswith("# agentcad-sketch-spec:") else l
        for l in script.splitlines())
    assert parse_blocks(broken)[0]["status"] == "unverified"


# --------------------------------------------------------------------------
# two blocks in one script (the push_pull counter precedent)
# --------------------------------------------------------------------------
def test_two_blocks_in_one_script_do_not_shadow_each_other():
    script = (SCRIPT_HEAD + block_of(square_spec(), "profile")
              + block_of(curvy_spec(), "profile2"))
    blocks = parse_blocks(script)
    assert [b["name"] for b in blocks] == ["profile", "profile2"]
    assert all(b["status"] == "ok" for b in blocks)
    # different function names: the second does not shadow the first
    assert "def sketch_profile():" in script
    assert "def sketch_profile2():" in script
    assert blocks[0]["spec"] != blocks[1]["spec"]
    # a hand edit inside one does not implicate the other
    edited = script.replace("30.7183", "35.0")
    got = parse_blocks(edited)
    assert [b["status"] for b in got] == ["diverged", "ok"]


def test_next_name_skips_the_names_already_in_the_script():
    assert next_name("") == "profile"
    assert next_name(SCRIPT_HEAD) == "profile"
    one = SCRIPT_HEAD + block_of(square_spec(), "profile")
    assert next_name(one) == "profile2"
    two = one + block_of(square_spec(), "profile2")
    assert next_name(two) == "profile3"
    # a diverged or unreadable block still owns its name
    assert next_name(two.replace("30.7183", "35.0")) == "profile3"


# --------------------------------------------------------------------------
# the surfaces: the tool and the route
# --------------------------------------------------------------------------
def _tool(spec: dict, **kwargs) -> dict:
    registry = ToolRegistry()
    tools_sketch.register(registry, None)
    return registry.get("solve_sketch").handler(
        entities=entities_of(spec), constraints=spec["constraints"], **kwargs)


def test_the_tool_emits_a_persisted_block():
    out = _tool(square_spec(), emit="function", persist="profile3")
    assert 'agentcad sketch "profile3"' in out["emit"]["code"]
    assert out["emit"]["persist"] == "profile3"
    assert parse_blocks(out["emit"]["code"])[0]["status"] == "ok"


def test_the_tool_refuses_a_bad_block_name_as_a_validation_error():
    from agentcad.core.model import ValidationError
    with pytest.raises(ValidationError):
        _tool(square_spec(), emit="function", persist="two words")


@pytest.mark.integration
def test_the_route_round_trips_a_block(tmp_path):
    """The GUI's two calls, end to end: solve+persist, then parse the script
    it pasted into."""
    spec = square_spec()
    service = make_test_service(tmp_path / "projects", None)
    client = TestClient(create_app(service, build_registry(service)),
                        base_url="http://127.0.0.1")
    solved_res = client.post("/api/sketch/solve", json={
        "entities": entities_of(spec), "constraints": spec["constraints"],
        "emit": "function", "persist": "profile",
    }).json()
    code = solved_res["emit"]["code"]
    assert 'agentcad sketch "profile"' in code

    script = SCRIPT_HEAD + code
    blocks = client.post("/api/sketch/blocks", json={"script": script}).json()
    assert blocks["next_name"] == "profile2"
    assert [b["status"] for b in blocks["blocks"]] == ["ok"]
    assert blocks["blocks"][0]["spec"]["entities"]["points"][0]["name"] == "a"

    diverged = client.post("/api/sketch/blocks",
                           json={"script": script.replace("30.7183", "1.0")}
                           ).json()
    assert diverged["blocks"][0]["status"] == "diverged"
    # an empty body is not an error — a part with no script has no blocks
    assert client.post("/api/sketch/blocks", json={}).json() == {
        "blocks": [], "next_name": "profile"}


def test_the_emitter_stays_free_of_build123d():
    """The parse and the hash run in the **server** process, like the rest of
    `sketch_emit` — asserted where the new entry points are, not inherited."""
    import subprocess
    import sys
    code = ("import sys; from agentcad.core.sketch_emit import parse_blocks; "
            "parse_blocks('# --- agentcad sketch \"p\" (auto-generated; edit "
            "or remove freely) ---'); "
            "assert 'OCP' not in sys.modules and 'build123d' not in sys.modules; "
            "print('clean')")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, check=True)
    assert out.stdout.strip() == "clean"


def test_the_tool_description_documents_the_block_and_its_divergence_rule():
    registry = ToolRegistry()
    tools_sketch.register(registry, None)
    text = registry.get("solve_sketch").description
    assert "agentcad-sketch-spec" in text
    for phrase in ("hash", "diverg", "source of truth"):
        assert phrase in text.lower(), f"the description never mentions {phrase}"


# --------------------------------------------------------------------------
# the error contract on the two remaining raw paths (review P7)
# --------------------------------------------------------------------------
def test_a_constraint_with_the_wrong_kwargs_is_a_validation_error():
    """`parse_sketch` calls `fn(**kw)`, so a missing or misspelled keyword
    escaped as a bare `TypeError` — an HTTP 500 — instead of the
    `validation_error` every other malformed spec gets. The kwarg surface
    roughly doubled when `tangent`, `symmetric`, `equal_length` and
    `concentric` landed, so this is the most likely spec mistake there is."""
    from agentcad.core.model import ValidationError
    from agentcad.toolkit.sketch import SketchError, solve_sketch
    cases = [
        {"type": "concentric", "c1": "C1", "c2": "C2"},   # a/b, not c1/c2
        {"type": "distance", "p": "a", "q": "b"},         # no `d`
        {"type": "horizontal", "line": "l1"},             # `ln`
    ]
    for con in cases:
        spec = {"points": [{"name": "a", "x": 0.0, "y": 0.0},
                           {"name": "b", "x": 10.0, "y": 0.0}],
                "lines": [{"name": "l1", "p1": "a", "p2": "b"}],
                "circles": [{"name": "C1", "center": "a", "r": 4.0},
                            {"name": "C2", "center": "b", "r": 4.0}],
                "constraints": [con]}
        with pytest.raises(SketchError) as exc:
            solve_sketch(spec)
        assert con["type"] in str(exc.value)
        assert "constraint 0" in str(exc.value)
        with pytest.raises(ValidationError):
            _tool(spec)


@pytest.mark.integration
def test_the_blocks_route_types_its_one_argument(tmp_path):
    """`/api/sketch/blocks` bypasses the registry (`parse_blocks` is a pure
    text function), so it also bypassed the registry's type check: a
    `{"script": 123}` reached `str.replace` and came back as a 500."""
    service = make_test_service(tmp_path / "projects", None)
    client = TestClient(create_app(service, build_registry(service)),
                        base_url="http://127.0.0.1")
    for bad in (123, {"a": 1}, ["def f(): pass"]):
        res = client.post("/api/sketch/blocks", json={"script": bad})
        assert res.status_code == 422, (bad, res.status_code, res.text)
        assert res.json()["error"]["type"] == "ValidationError"
    # and the shapes that are not an error stay that way
    assert client.post("/api/sketch/blocks", json={"script": None}).json() == {
        "blocks": [], "next_name": "profile"}


# --------------------------------------------------------------------------
# `parse_blocks` is a text scanner, and text has string literals (review P13)
# --------------------------------------------------------------------------
def test_a_marker_inside_a_string_literal_is_not_a_block():
    """A docstring or a string constant that *quotes* the marker produced a
    phantom `diverged` block and shifted `next_name`, so the next insert was
    named around a sketch that does not exist."""
    from agentcad.core.sketch_emit import next_name, parse_blocks
    script = '\n'.join([
        "HELP = '''",
        '# --- agentcad sketch "profile" (auto-generated; edit or remove freely) ---',
        "how a sketch block looks",
        "'''",
        "",
        "def build(p):",
        "    return None",
    ])
    assert parse_blocks(script) == []
    assert next_name(script) == "profile"


def test_a_blank_line_between_the_marker_and_the_spec_is_tolerated():
    """`_norm` deliberately forgives an editor that rewrites whitespace; the
    block scanner was stricter than the hash it protects, so one blank line
    downgraded an intact block to `unverified`."""
    from agentcad.core.sketch_emit import parse_blocks
    spec = square_spec()
    code = _tool(spec, emit="function", persist="profile")["emit"]["code"]
    assert parse_blocks(code)[0]["status"] == "ok"
    lines = code.split("\n")
    at = next(i for i, line in enumerate(lines)
              if line.startswith("# --- agentcad sketch"))
    spaced = "\n".join(lines[:at + 1] + [""] + lines[at + 1:])
    assert parse_blocks(spaced)[0]["status"] == "ok", parse_blocks(spaced)[0]


# --------------------------------------------------------------------------
# review 2, C11: `ok` has to mean "this spec produced this code"
# --------------------------------------------------------------------------
def test_editing_only_the_spec_comment_is_a_divergence():
    """The hash covered the code and nothing else, so a block whose *spec* had
    been rewritten still read `ok` — and the sketcher then opened a sketch that
    has nothing to do with the geometry beneath it (review 2, C11)."""
    script = SCRIPT_HEAD + block_of(square_spec())
    edited = "\n".join(
        _with_moved_point(l) if l.startswith("# agentcad-sketch-spec:") else l
        for l in script.splitlines())
    assert edited != script
    block = parse_blocks(edited)[0]
    assert block["status"] == "diverged", block
    assert "spec" in block["message"]


def _with_moved_point(line: str) -> str:
    """The spec comment with one entity coordinate rewritten — a hand edit to
    the provenance, with the code beneath it untouched."""
    spec = json.loads(line[len(SPEC_PREFIX):])
    spec["entities"]["points"][1]["x"] = 99.0
    return SPEC_PREFIX + json.dumps(spec)


def mirror_spec(cy: float) -> dict:
    """Two solutions, and `initial` is what picks one: `c` is held 25 from
    both `a` and `b`, so it can sit above or below the axis."""
    return {
        "points": [{"name": "a", "x": 0.0, "y": 0.0, "fixed": True},
                   {"name": "b", "x": 30.0, "y": 0.0, "fixed": True},
                   {"name": "c", "x": 15.0, "y": cy}],
        "lines": [{"name": "ab", "p1": "a", "p2": "b"},
                  {"name": "bc", "p1": "b", "p2": "c"},
                  {"name": "ca", "p1": "c", "p2": "a"}],
        "constraints": [{"type": "distance", "p": "a", "q": "c", "d": 25.0},
                        {"type": "distance", "p": "b", "q": "c", "d": 25.0}],
    }


def test_a_block_reopens_on_the_branch_its_code_was_emitted_from():
    """`persist_spec` stored the coordinates the caller *submitted* and dropped
    `initial`, so a sketch emitted on the branch `initial` selected reopened on
    the other one — while reporting `ok`. The block now records the solution
    its own code was written from, as an `initial`, so re-solving it lands
    where the code is."""
    spec = mirror_spec(+10.0)                     # submitted: above the axis
    spec["initial"] = {"points": {"c": {"x": 15.0, "y": -10.0}}}
    sol = solved(spec)
    assert sol["points"]["c"]["y"] < 0.0, sol["points"]   # emitted: below
    script = SCRIPT_HEAD + emit(sol, spec, persist="profile")["code"]
    block = parse_blocks(script)[0]
    assert block["status"] == "ok", block["message"]
    again = solve_sketch({**block["spec"]["entities"],
                          "constraints": block["spec"]["constraints"],
                          "initial": block["spec"].get("initial")})
    assert again["points"]["c"]["y"] == pytest.approx(sol["points"]["c"]["y"],
                                                      abs=1e-9)


def test_the_recorded_initial_is_the_solution_not_the_submission():
    spec = mirror_spec(+10.0)
    spec["initial"] = {"points": {"c": {"x": 15.0, "y": -10.0}}}
    sol = solved(spec)
    block = parse_blocks(emit(sol, spec, persist="profile")["code"])[0]
    seeded = block["spec"]["initial"]["points"]["c"]
    assert seeded["y"] == pytest.approx(sol["points"]["c"]["y"], abs=1e-12)
    # and the entities are still the submission, which is what FR10 records
    assert block["spec"]["entities"]["points"][2]["y"] == 10.0


# --------------------------------------------------------------------------
# review 2, C12: unreadable must mean `unverified`, not `ok`
# --------------------------------------------------------------------------
BROKEN_SPECS = {
    "entities_not_an_object": '{"v": 1, "entities": [1, 2]}',
    "kind_not_a_list": '{"v": 1, "entities": {"points": "not-a-list"}}',
    "entity_not_an_object": '{"v": 1, "entities": {"points": ["p1"]}}',
    "entity_without_a_name": '{"v": 1, "entities": {"points": [{"x": 1}]}}',
    "unknown_entity_kind": '{"v": 1, "entities": {"widgets": []}}',
    "constraints_not_a_list": '{"v": 1, "entities": {}, "constraints": {}}',
    "constraint_not_an_object": '{"v": 1, "entities": {}, "constraints": [7]}',
    "constraint_without_a_type": ('{"v": 1, "entities": {}, '
                                  '"constraints": [{"p": "a"}]}'),
    "plane_not_an_object": '{"v": 1, "entities": {}, "plane": 3}',
    "initial_not_an_object": '{"v": 1, "entities": {}, "initial": []}',
}


@pytest.mark.parametrize("case", sorted(BROKEN_SPECS))
def test_a_semantically_broken_spec_is_unverified(case):
    """`_read_spec` checked that the JSON was an object with an `entities` key
    and the right version, and nothing else — so
    `"entities": {"points": "not-a-list"}` came back `ok` and the browser threw
    a `TypeError` out of `.map()` (review 2, C12). Unreadable means
    `unverified`, at the one place that decides."""
    script = block_of(square_spec())
    broken = "\n".join(
        f"# agentcad-sketch-spec: {BROKEN_SPECS[case]}"
        if l.startswith("# agentcad-sketch-spec:") else l
        for l in script.splitlines())
    block = parse_blocks(broken)[0]
    assert block["status"] == "unverified", block
    assert block["spec"] is None
    assert block["message"]


def test_a_readable_spec_is_still_ok():
    """The narrowing: the validation must not reject what the emitter writes,
    for any entity kind."""
    for build in (square_spec, curvy_spec, construction_spec):
        block = parse_blocks(block_of(build()))[0]
        assert block["status"] == "ok", (build.__name__, block["message"])
