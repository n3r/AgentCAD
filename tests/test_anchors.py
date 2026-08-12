"""PRD-008 slice 2: anchor resolution without a kernel.

Everything up to section 7 is pure domain logic over bytes and strings — a
hand-built ACM buffer, a script's text, a fabricated old/new line pair — so it
carries no marker; only section 8 needs a real git blob and is marked there.
The geometry that needs a real build lives in ``tests/test_anchors_kernel.py``
(``slow``).

Sections: 1. the four-state vocabulary · 2. ``face_table`` over a synthetic
mesh · 3. the face matcher · 4. script_range tier 1 (AC3) · 5. the tier-2 line
map · 6. dispatch, the manifest-only kinds and the cross-branch rule ·
7. import purity · 8. tier 2 against a real git blob (the only cases here that
need git, marked ``integration``/``portability``).
"""

from __future__ import annotations

import hashlib
import pathlib
import shutil
import subprocess
import sys

import numpy as np
import pytest

from agentcad.core import anchors
from agentcad.kernel import acm

# --------------------------------------------------------------- fixtures


def cube_mesh(size: float = 10.0, origin=(0.0, 0.0, 0.0)) -> tuple[bytes, bytes]:
    """An axis-aligned box as ACM1 + face sidecar: 8 vertices, 6 faces, 12
    triangles, outward winding, one face ordinal per pair of triangles.

    Hand-built rather than kernel-built so the expected areas, normals and
    bbox fractions are known exactly (a tessellation would only be known
    approximately).
    """
    sx, sy, sz = (size, size, size) if isinstance(size, (int, float)) else size
    ox, oy, oz = origin
    verts = np.array(
        [
            [ox, oy, oz], [ox + sx, oy, oz], [ox + sx, oy + sy, oz],
            [ox, oy + sy, oz], [ox, oy, oz + sz], [ox + sx, oy, oz + sz],
            [ox + sx, oy + sy, oz + sz], [ox, oy + sy, oz + sz],
        ],
        dtype="<f4",
    )
    tris = np.array(
        [
            [0, 3, 2], [0, 2, 1],      # face 0: -Z
            [4, 5, 6], [4, 6, 7],      # face 1: +Z
            [0, 1, 5], [0, 5, 4],      # face 2: -Y
            [3, 7, 6], [3, 6, 2],      # face 3: +Y
            [0, 4, 7], [0, 7, 3],      # face 4: -X
            [1, 2, 6], [1, 6, 5],      # face 5: +X
        ],
        dtype="<u4",
    )
    face_ids = np.repeat(np.arange(6, dtype="<u4"), 2)
    buffer = acm.pack(
        verts,
        np.zeros_like(verts),
        tris,
        np.zeros(0, dtype="<u4"),
        np.zeros((0, 3), dtype="<f4"),
    )
    return buffer, face_ids.tobytes()


SCRIPT = """\
from build123d import *

PARAMS = {"wall": {"default": 2.0}}


def build(p):
    with BuildPart() as part:
        Box(20, 20, 20)
        Hole(radius=p.wall)
    return part.part
"""


# ------------------------------------------- 1. the four-state vocabulary


def test_the_vocabulary_is_the_four_states():
    assert anchors.RESOLUTION == ("ok", "moved", "orphaned", "unverified")


def test_ok_is_the_only_status_that_may_omit_a_reason():
    assert anchors.make_resolution("ok")["status"] == "ok"
    assert "reason" not in anchors.make_resolution("ok")
    for status in ("moved", "orphaned", "unverified"):
        with pytest.raises(ValueError):
            anchors.make_resolution(status)


def test_unverified_and_orphaned_must_carry_a_hint():
    """``unverified`` means *we did not look*; a reader who is told nothing
    about how to find out will render it as "fine"."""
    for status in ("orphaned", "unverified"):
        with pytest.raises(ValueError):
            anchors.make_resolution(status, reason="no_git")
        assert anchors.make_resolution(status, reason="no_git",
                                       hint="install git")

    # ``moved`` is self-explaining: it carries the new address instead.
    moved = anchors.make_resolution("moved", reason="rematched")
    assert moved["status"] == "moved"


def test_an_unknown_status_is_refused():
    with pytest.raises(ValueError):
        anchors.make_resolution("fine")


# ------------------------------------- 2. face_table over a synthetic mesh


def test_face_table_measures_a_known_cube_exactly():
    buffer, face_ids = cube_mesh(10.0)
    table = anchors.face_table(buffer, face_ids)

    assert len(table) == 6
    for row in table:
        assert row["present"] is True
        assert row["area"] == pytest.approx(100.0)
    normals = [tuple(round(c, 6) for c in row["normal"]) for row in table]
    assert normals == [
        (0.0, 0.0, -1.0), (0.0, 0.0, 1.0), (0.0, -1.0, 0.0),
        (0.0, 1.0, 0.0), (-1.0, 0.0, 0.0), (1.0, 0.0, 0.0),
    ]
    assert table[0]["centroid"] == pytest.approx([5.0, 5.0, 0.0])
    assert table[1]["bbox_uvw"] == pytest.approx([0.5, 0.5, 1.0])
    assert table[4]["bbox_uvw"] == pytest.approx([0.0, 0.5, 0.5])


def test_bbox_uvw_is_scale_invariant():
    """The one property that makes a scaling parameter survivable: absolute
    centroids move, normalized ones do not (design Decision 5)."""
    small = anchors.face_table(*cube_mesh(10.0))
    large = anchors.face_table(*cube_mesh(70.0))

    for a, b in zip(small, large):
        assert a["bbox_uvw"] == pytest.approx(b["bbox_uvw"])
        assert a["centroid"] != pytest.approx(b["centroid"])


def test_a_degenerate_bbox_axis_maps_to_the_middle():
    buffer, face_ids = cube_mesh((20.0, 20.0, 0.0))
    table = anchors.face_table(buffer, face_ids)

    for row in table:
        assert row["bbox_uvw"][2] == pytest.approx(0.5)


def test_a_face_with_no_triangles_still_consumes_its_ordinal():
    """``mesh.py`` numbers faces by the explorer walk, not by the triangles
    it emitted, so an ordinal with no triangles is a hole in the table — not
    a shift of every ordinal after it."""
    buffer, face_ids = cube_mesh(10.0)
    ids = np.frombuffer(face_ids, dtype="<u4").copy()
    ids[ids >= 3] += 1              # ordinal 3 now has no triangles
    table = anchors.face_table(buffer, ids.tobytes())

    assert len(table) == 7
    assert table[3] == {"index": 3, "present": False, "area": 0.0,
                        "centroid": [0.0, 0.0, 0.0], "normal": [0.0, 0.0, 0.0],
                        "bbox_uvw": [0.5, 0.5, 0.5]}
    assert table[4]["present"] is True


def test_an_empty_or_truncated_sidecar_is_not_a_table():
    buffer, face_ids = cube_mesh(10.0)
    assert anchors.face_table(buffer, b"") == []
    assert anchors.face_table(buffer, face_ids[:8]) == []
    assert anchors.face_table(b"nope", face_ids) == []


def test_n_faces_comes_from_the_sidecar_not_from_a_metric():
    """R2: ``metrics.n_faces`` is ``len(shape.faces())``, deduplicated by
    build123d; the sidecar is the authority (design Decision 3's trap)."""
    buffer, face_ids = cube_mesh(10.0)
    assert anchors.sidecar_face_count(face_ids) == 6
    assert anchors.sidecar_face_count(b"") == 0


# ------------------------------------------------------ 3. the face matcher


def _sig(row, table=None, key="k"):
    table = [row] if table is None else table
    return anchors.signature_of(row, key, len(table), anchors.total_area(table))


def _table(size=10.0, origin=(0.0, 0.0, 0.0)):
    return anchors.face_table(*cube_mesh(size, origin))


def test_an_unchanged_shape_matches_every_face_to_itself():
    table = _table()
    for row in table:
        best, score, margin, refusal = anchors.match_face(
            _sig(row, table), table)
        assert refusal is None
        assert best["index"] == row["index"]
        assert score > 0.9


def test_a_uniformly_scaled_shape_still_matches():
    """AC2's mechanism: a parameter that scales the part moves every absolute
    centroid and every absolute area, and moves neither ``bbox_uvw`` nor
    ``area_frac`` — which is why the matcher compares those two and not the
    millimetres (design Decision 5, corrected by the slice-2 spike: the
    design's absolute-area filter is not scale-invariant)."""
    before, after = _table(10.0), _table(60.0)

    for row in before:
        best, _score, _margin, refusal = anchors.match_face(
            _sig(row, before), after)
        assert refusal is None, row["index"]
        assert best["index"] == row["index"]


def test_a_symmetric_face_pair_is_orphaned_not_guessed():
    """Two faces of the same orientation at the same normalized position are
    genuinely indistinguishable by this signature, so the answer is 'no'."""
    table = _table()
    twin = dict(table[0])
    twin["index"] = 6
    best, _score, margin, refusal = anchors.match_face(
        _sig(table[0], table), table + [twin])

    assert best is None
    assert refusal == "ambiguous"
    assert margin < anchors.AMBIGUITY_MARGIN


def test_a_face_that_was_cut_away_is_orphaned():
    table = _table()
    gone = _sig(table[1], table)             # the +Z face
    remaining = [row for row in table if row["index"] != 1]

    best, _score, _margin, refusal = anchors.match_face(gone, remaining)
    assert best is None
    assert refusal == "no_candidate"


def test_the_area_gate_refuses_a_lone_candidate_of_the_wrong_size():
    """Area is a gate on the winner, never a candidacy filter: filtering
    rivals out is what removes the ambiguity signal (spike R1)."""
    table = _table()
    signature = _sig(table[1], table)
    signature["area_frac"] = 0.001           # the anchor named a tiny face

    best, _score, _margin, refusal = anchors.match_face(signature, [table[1]])
    assert best is None
    assert refusal == "area_mismatch"


def test_a_lone_candidate_must_clear_the_tighter_absolute_area_bar():
    """The ambiguity margin is what "orphan, never mis-pin" rests on — and it
    cannot fire at all when exactly one candidate survives. Such a winner was
    accepted for being the only one left, and reported ``margin = best - 0``,
    i.e. near-maximal confidence, for it. With no rival to corroborate it, the
    one remaining piece of evidence has to be strong on its own.
    """
    # Every face of a cube has a unique normal, so each is the only candidate
    # for its own signature — the case the ambiguity margin cannot reach.
    table = _table()
    frac = _sig(table[1], table)["area_frac"]

    signature = _sig(table[1], table)
    signature["area_frac"] = frac * 0.65       # 35% off the stored share
    assert anchors.LONE_AREA_REL < 0.35 < anchors.AREA_REL
    best, _score, _margin, refusal = anchors.match_face(signature, table)
    assert best is None and refusal == "area_mismatch"

    # Inside the bar it still matches: this is a gate, not a refusal to answer.
    signature["area_frac"] = frac * 0.95
    best, _score, _margin, refusal = anchors.match_face(signature, table)
    assert refusal is None and best["index"] == 1


def test_a_signature_without_area_frac_falls_back_to_millimetres():
    """Nothing has written such a signature, but a stored anchor outlives the
    code that wrote it, and an old anchor must degrade rather than crash."""
    table = _table()
    signature = _sig(table[1], table)
    signature.pop("area_frac")

    best, _score, _margin, refusal = anchors.match_face(signature, table)
    assert refusal is None and best["index"] == 1


def test_the_measured_constants_are_the_spike_s_and_not_the_design_s_guesses():
    """The design shipped five placeholders and told the implementer to
    measure them (risk R1). Four moved and the fifth was deleted as
    unreachable — see changelog 0113."""
    assert anchors.NORMAL_DOT == 0.99        # design guessed 0.985
    assert anchors.UVW_DIST == 0.15          # design guessed 0.15
    assert anchors.AMBIGUITY_MARGIN == 0.20  # design guessed 0.05: 60 mis-pins
    assert anchors.AREA_REL == 0.5           # design guessed 0.25, as a filter
    assert anchors.LONE_AREA_REL == 0.30     # the review's mis-pin: 0.43
    assert not hasattr(anchors, "STICKY_MARGIN")


# ------------------------------------------- 4. script_range tier 1 (AC3)


def test_snippet_evidence_is_the_lines_and_their_context():
    evidence = anchors.snippet_of(SCRIPT, 7, 8)

    assert evidence["snippet"] == (
        "    with BuildPart() as part:\n        Box(20, 20, 20)")
    assert evidence["before"].endswith("def build(p):")
    assert "Hole" in evidence["after"]
    assert evidence["snippet_sha256"] == hashlib.sha256(
        evidence["snippet"].encode()).hexdigest()


def test_a_snippet_is_found_after_an_insert_above_it():
    """**AC3**: an edit above a line comment moves its lines, and tier 1 finds
    them exactly — no git, no heuristics, no confidence below 1.0."""
    lines = SCRIPT.splitlines()
    evidence = anchors.snippet_of(SCRIPT, 7, 8)
    edited = lines[:3] + ["", "# a new comment", "WALL_MIN = 1.0"] + lines[3:]

    hits = anchors.find_snippet(edited, evidence["snippet"].splitlines(),
                                evidence["before"].splitlines(),
                                evidence["after"].splitlines())
    assert hits == [9]                       # 0-based: line 10, was line 7


def test_a_duplicated_snippet_is_disambiguated_by_its_context():
    body = ["a = 1", "b = 2", "MARK", "c = 3", "z = 9", "b = 2", "MARK", "c = 4"]
    hits = anchors.find_snippet(body, ["MARK"], ["b = 2"], ["c = 4"])

    assert hits == [6]


def test_an_undisambiguated_duplicate_stays_ambiguous():
    body = ["MARK", "MARK"]
    assert anchors.find_snippet(body, ["MARK"], [], []) == [0, 1]


def test_a_deleted_snippet_is_found_nowhere():
    assert anchors.find_snippet(["a = 1"], ["gone"], [], []) == []


def test_a_lone_survivor_the_context_contradicts_is_not_a_match():
    """K3 — the same shape as the face matcher's lone-candidate mis-pin.

    Two ``MARK`` lines, told apart only by their context; the thread anchors
    the first; the first is deleted. "Exactly one copy left" used to skip the
    context check entirely, so the surviving *unrelated* occurrence came back
    as a verbatim hit and the resolver reported ``moved`` at confidence 1.0 —
    a wrong pin with maximal confidence, which is precisely the outcome this
    module exists to refuse.
    """
    before, after = ["b = 2"], ["c = 3"]
    body = ["a = 1", "b = 2", "MARK", "c = 3", "z = 9", "y = 8", "MARK", "c = 4"]
    assert anchors.find_snippet(body, ["MARK"], before, after) == [2]

    body.pop(2)                                   # delete the anchored one
    assert anchors.find_snippet(body, ["MARK"], before, after) == []


def test_a_lone_survivor_one_side_of_the_context_corroborates_is_a_match():
    """The other half: a real move keeps at least one side of its context, and
    orphaning that would trade one wrong answer for a hundred missing ones."""
    body = ["a = 1", "b = 2", "MARK", "c = 3"]
    moved = ["x = 0", "y = 0", "a = 1", "b = 2", "MARK", "print('new')"]
    assert anchors.find_snippet(moved, ["MARK"], ["b = 2"], ["c = 3"]) == [4]


def test_a_lone_survivor_with_no_stored_context_is_still_a_match():
    """An anchor at the very top and bottom of a file stores no context, and
    an anchor written before context existed stores none either. There is
    nothing to corroborate, and inventing a refusal out of that would orphan
    every one of them."""
    assert anchors.find_snippet(["MARK"], ["MARK"], [], []) == [0]


# ------------------------------------------- 5. the tier-2 line map


def test_the_line_map_carries_a_range_across_an_insert():
    old = ["a", "b", "c", "d"]
    new = ["x", "y", "a", "b", "c", "d"]

    assert anchors.line_map(old, new, 2, 3) == (4, 5, 1.0)


def test_a_partially_surviving_range_reports_the_surviving_fraction():
    old = ["a", "b", "c", "d"]
    new = ["a", "b", "REPLACED", "d"]

    start, end, confidence = anchors.line_map(old, new, 2, 4)
    assert (start, end) == (2, 4)
    assert confidence == pytest.approx(2 / 3)


def test_a_wholly_deleted_range_maps_to_nothing():
    assert anchors.line_map(["a", "b", "c"], ["a", "c"], 2, 2) is None


# ---------------------------------- 6. dispatch and the cross-branch rule


class _NoKernel:
    """A kernel client that fails the test if anything asks it to build.

    Risk R8: ``list_comments`` on a project of unbuilt parts must be a cheap
    read, so resolution must never reach the pool.
    """

    def __init__(self):
        self.calls = []

    def request(self, op, params, timeout_s=None):
        self.calls.append(op)
        raise AssertionError(f"resolution called the kernel: {op}")


@pytest.fixture
def unbuilt(tmp_path):
    """A project with a real manifest and a real script, and no build."""
    from agentcad.core.service import AgentCADService, EventBus

    kernel = _NoKernel()
    bus = EventBus()
    service = AgentCADService(tmp_path / "projects", kernel, bus)
    bus.on_publish = None
    service.create_project("demo")
    service.store.add_part("demo", "box", "Box", "al6061", SCRIPT)
    return service


def test_manifest_only_kinds_resolve_from_the_manifest_alone(unbuilt):
    ok = anchors.resolve(unbuilt, "demo", {"kind": "part", "part": "box"})
    assert ok["status"] == "ok"
    assert "against" in ok

    gone = anchors.resolve(unbuilt, "demo", {"kind": "part", "part": "nope"})
    assert gone["status"] == "orphaned"
    assert gone["reason"] == "part_removed"
    assert gone["hint"]
    assert unbuilt.kernel.calls == []


def test_a_param_anchor_reads_the_spec_statically_without_the_kernel(unbuilt):
    assert anchors.resolve(unbuilt, "demo",
                           {"kind": "param", "part": "box",
                            "param": "wall"})["status"] == "ok"
    gone = anchors.resolve(unbuilt, "demo",
                           {"kind": "param", "part": "box", "param": "depth"})
    assert (gone["status"], gone["reason"]) == ("orphaned", "param_removed")
    assert unbuilt.kernel.calls == []


def test_a_face_anchor_on_an_unbuilt_part_is_unverified_not_orphaned(unbuilt):
    """**R8**: a listing may not force a 300 s build, so 'we did not look' is
    the only honest answer — and it is not 'fine'."""
    result = anchors.resolve(
        unbuilt, "demo",
        {"kind": "face", "part": "box", "face_index": 3,
         "signature": {"normal": [0, 0, 1], "bbox_uvw": [0.5, 0.5, 1.0],
                       "area_mm2": 4.0, "mesh_key": "stale"}})

    assert result["status"] == "unverified"
    assert result["reason"] == "part_not_built"
    assert "build" in result["hint"]
    assert unbuilt.kernel.calls == []


def test_a_target_missing_here_but_authored_elsewhere_is_unverified(unbuilt):
    """Decision 7: telling a reader their face was cut away when they merely
    switched branches is a lie the four-state model exists to avoid."""
    result = anchors.resolve(
        unbuilt, "demo", {"kind": "part", "part": "nope", "branch": "feat/x"},
        context={"branch": "main", "head": "", "root": None})

    assert result["status"] == "unverified"
    assert result["reason"] == "other_branch"
    assert "feat/x" in result["hint"]
    assert result["against"] == {"branch": "main", "head": ""}


def test_a_param_missing_from_a_part_that_exists_here_is_still_other_branch(
        unbuilt):
    """K10 — the cross-branch check only ran when the whole PART was missing.

    A part that exists on both branches but declares the parameter on only one
    of them is the ordinary shape of a branch: the reader is told the
    parameter was *removed*, which is a claim about their branch made from an
    anchor that was never about their branch. "We are not looking at the
    anchor's branch" has to be decided before absence is classified at all.
    """
    elsewhere = {"branch": "main", "head": "", "root": None}
    ok = anchors.resolve(unbuilt, "demo",
                         {"kind": "param", "part": "box", "param": "wall",
                          "branch": "feat/x"}, context=elsewhere)
    assert ok["status"] == "ok"          # present here: nothing to classify

    gone = anchors.resolve(unbuilt, "demo",
                           {"kind": "param", "part": "box", "param": "depth",
                            "branch": "feat/x"}, context=elsewhere)
    assert (gone["status"], gone["reason"]) == ("unverified", "other_branch")
    assert "feat/x" in gone["hint"] and "depth" in gone["hint"]
    assert unbuilt.kernel.calls == []


def test_a_script_range_lost_on_another_branch_is_other_branch(unbuilt,
                                                               monkeypatch):
    """The same rule for the script anchor's own orphan verdicts: the lines
    are gone from *this* branch, which says nothing about the branch the
    thread was opened on. Tier 2 is forced to run (there is no repo here) so
    the verdict under test is a real ``lines_removed``, not a "we did not
    look"."""
    monkeypatch.setattr(anchors, "_blob_lines",
                        lambda *a, **k: SCRIPT.splitlines())
    anchor = {"kind": "script_range", "part": "box", "start": 7, "end": 8,
              **anchors.snippet_of(SCRIPT, 7, 8), "head": "cafe1234",
              "branch": "feat/x"}
    unbuilt.store.write_script("demo", "box", "print('rewritten')\n")

    same = anchors.resolve(unbuilt, "demo", anchor,
                           {"branch": "feat/x", "head": "", "root": None})
    assert (same["status"], same["reason"]) == ("orphaned", "lines_removed")

    other = anchors.resolve(unbuilt, "demo", anchor,
                            {"branch": "main", "head": "", "root": None})
    assert (other["status"], other["reason"]) == ("unverified", "other_branch")
    assert "feat/x" in other["hint"]


def test_script_range_tier_1_resolves_without_git(unbuilt):
    evidence = anchors.snippet_of(SCRIPT, 7, 8)
    anchor = {"kind": "script_range", "part": "box", "start": 7, "end": 8,
              **evidence}
    context = {"branch": "", "head": "", "root": None}

    assert anchors.resolve(unbuilt, "demo", anchor,
                           context)["status"] == "ok"

    lines = SCRIPT.splitlines()
    unbuilt.store.write_script(
        "demo", "box",
        "\n".join(lines[:3] + ["", "# inserted"] + lines[3:]) + "\n")
    moved = anchors.resolve(unbuilt, "demo", anchor, context)
    assert moved["status"] == "moved"
    assert (moved["start"], moved["end"]) == (9, 10)
    assert moved["confidence"] == 1.0
    assert unbuilt.kernel.calls == []


def test_without_git_a_lost_snippet_is_unverified_never_orphaned(unbuilt):
    """We did not look, so we must not claim. Tier 2 needs the blob at the
    anchor's head; without git there is no blob."""
    anchor = {"kind": "script_range", "part": "box", "start": 7, "end": 8,
              **anchors.snippet_of(SCRIPT, 7, 8), "head": ""}
    unbuilt.store.write_script("demo", "box", "print('rewritten')\n")

    result = anchors.resolve(unbuilt, "demo", anchor,
                             {"branch": "", "head": "", "root": None})
    assert result["status"] == "unverified"
    assert result["reason"] in ("no_git", "no_head")
    assert result["hint"]


def test_a_broken_anchor_never_raises_out_of_a_listing(unbuilt):
    for anchor in (None, "face", {"kind": "nonsense"}, {}):
        result = anchors.resolve(unbuilt, "demo", anchor)
        assert result["status"] == "unverified"
        assert result["reason"] and result["hint"]


def test_a_whole_listing_resolves_without_touching_the_kernel(unbuilt):
    """**R8** at the seam an outside reader would test: ``list`` resolves
    every thread (``counts`` is whole-project) and still costs no build."""
    from agentcad.core.comments import CommentManager

    manager = CommentManager(unbuilt)
    manager.create("demo", {"kind": "part", "part": "box"}, "a")
    manager.create("demo", {"kind": "script_range", "part": "box",
                            "start": 6, "end": 8}, "b")
    # (a ``param`` anchor is deliberately absent: *creating* one inspects the
    # script through the kernel — once, content-hash cached — which slice 1
    # accepted. Resolving one never does, which is what this asserts.)

    listed = manager.list("demo")
    assert [row["resolution"]["status"] for row in listed["threads"]] == [
        "ok", "ok"]
    assert listed["counts"] == {"open": 2, "resolved": 0, "orphaned": 0}
    assert unbuilt.kernel.calls == []

    # the cheapest listing computes nothing and therefore claims nothing
    bare = manager.list("demo", resolve_anchors=False)
    assert "resolution" not in bare["threads"][0]
    assert "orphaned" not in bare["counts"]


def test_the_stored_anchor_carries_its_evidence_and_never_the_callers(unbuilt):
    """A signature or a snippet a client can assert is not evidence: both are
    derived here, like ``branch``/``head``."""
    from agentcad.core.comments import CommentManager

    manager = CommentManager(unbuilt)
    thread = manager.create(
        "demo", {"kind": "script_range", "part": "box", "start": 6, "end": 8,
                 "snippet": "TOTALLY MADE UP", "before": "lies"}, "b")

    anchor = manager.store.load("demo", thread["id"])["anchor"]
    assert anchor["snippet"].startswith("def build(p):")
    assert anchor["before"] and "lies" not in anchor["before"]
    assert anchor["snippet_sha256"]


# -------------------------------------------------------- 7. import purity


def test_importing_anchors_does_not_pull_in_ocp():
    """Only ``agentcad/kernel/`` may import OCP/build123d; a face signature is
    derived from files, in this process."""
    code = ("import sys; import agentcad.core.anchors; "
            "print('OCP' in sys.modules, 'build123d' in sys.modules)")
    repo = pathlib.Path(__file__).resolve().parent.parent
    out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, check=True, cwd=str(repo))
    assert out.stdout.strip() == "False False"
# ------------------------------- 8. tier 2 against a real git blob

_GIT = [
    pytest.mark.integration,
    pytest.mark.portability,
    pytest.mark.skipif(shutil.which("git") is None, reason="git not found on PATH"),
]


def _needs_git(fn):
    for mark in _GIT:
        fn = mark(fn)
    return fn


@_needs_git
def test_tier_2_remaps_through_the_blob_at_the_anchors_head(unbuilt):
    """Tier 1 fails when the snippet itself was edited; tier 2 then reads the
    script *as it was* at the anchor's head and maps the range through
    ``difflib`` opcodes. This is the only path that touches git, and it goes
    through ``history._run_bytes``, never a raw subprocess."""
    root = unbuilt.store.path_of("demo")
    head = unbuilt.history.snapshot(root, "seed")
    assert head, "the fixture needs a real commit to read a blob from"

    anchor = {"kind": "script_range", "part": "box", "start": 7, "end": 9,
              **anchors.snippet_of(SCRIPT, 7, 9), "head": head}
    lines = SCRIPT.splitlines()
    edited = (["# added", "# above"] + lines[:7]
              + ["        Box(30, 20, 20)"] + lines[8:])
    unbuilt.store.write_script("demo", "box", "\n".join(edited) + "\n")
    context = {"branch": "", "head": head, "root": root}

    result = anchors.resolve(unbuilt, "demo", anchor, context)
    assert result["status"] == "moved"
    assert result["reason"] == "remapped_by_diff"
    assert (result["start"], result["end"]) == (9, 11)
    assert result["confidence"] == 0.6667   # rounded, 2 of 3 lines
    assert unbuilt.kernel.calls == []


@_needs_git
def test_a_range_whose_lines_were_deleted_is_orphaned(unbuilt):
    root = unbuilt.store.path_of("demo")
    head = unbuilt.history.snapshot(root, "seed")
    anchor = {"kind": "script_range", "part": "box", "start": 7, "end": 9,
              **anchors.snippet_of(SCRIPT, 7, 9), "head": head}
    lines = SCRIPT.splitlines()
    unbuilt.store.write_script(
        "demo", "box", "\n".join(lines[:6] + ["    return None"]) + "\n")

    result = anchors.resolve(unbuilt, "demo", anchor,
                             {"branch": "", "head": head, "root": root})
    assert result["status"] == "orphaned"
    assert result["reason"] == "lines_removed"
    assert result["hint"]


@_needs_git
def test_an_unreachable_head_is_unverified_not_orphaned(unbuilt):
    """A garbage-collected commit or a deleted branch means we could not
    look — which is not the same as looking and finding nothing."""
    root = unbuilt.store.path_of("demo")
    unbuilt.history.snapshot(root, "seed")
    anchor = {"kind": "script_range", "part": "box", "start": 7, "end": 9,
              **anchors.snippet_of(SCRIPT, 7, 9), "head": "0" * 40}
    unbuilt.store.write_script("demo", "box", "print('rewritten')\n")

    result = anchors.resolve(unbuilt, "demo", anchor,
                             {"branch": "", "head": "", "root": root})
    assert result["status"] == "unverified"
    assert result["reason"] == "head_unreachable"
