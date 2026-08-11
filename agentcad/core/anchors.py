"""Anchor evidence at creation, anchor *status* at read time (PRD-008 FR1-FR3).

A thread's anchor is immutable: it is what the author pointed at. Where that
target is *now* is never stored — it is computed on every read, here, into one
of four states:

``ok``
    the anchor still points at what it pointed at, by identity.
``moved``
    re-matched at a different address; the result carries the new address and
    the score that earned it. The stored anchor is unchanged.
``orphaned``
    the target is gone, or no candidate cleared the tolerance. The thread stays
    readable and resolvable and keeps its last-known anchor.
``unverified``
    **we did not look.** An unbuilt part, no git, a frozen packet. This is a
    fourth fact, not a synonym for "fine": rendering it as ``ok`` is exactly how
    a UI ends up drawing a pin on a stale ordinal.

Two rules govern everything below.

**Orphan, never mis-pin.** A comment pointing at the wrong face is worse than a
comment pointing at nothing, so an ambiguous best match is an orphan and a
low-confidence line remap is an orphan. Loosening a tolerance to make a pin
appear is the one change this module must never take.

**Resolution never calls the kernel and never forces a build.** Listing threads
on a 40-part project must not rebuild 40 parts. Face signatures are derived in
*this* process from the two files a build already wrote — ``<key>.acm`` and the
``<key>.faces.u32`` sidecar — with NumPy. ``agentcad.kernel.acm`` states in its
own docstring that it has no OCP dependency, and ``core/packet.py``,
``core/service.py`` and ``core/tools_vision.py`` already read it from the server
process. Nothing here imports OCP or build123d, directly or transitively.

Two traps this module encodes:

* **``n_faces`` has two definitions, and only one of them defines an ordinal.**
  ``metrics["n_faces"]`` is ``len(shape.faces())``, which build123d
  deduplicates by hash; the ``.faces.u32`` sidecar comes from the raw
  ``TopExp_Explorer`` walk that ``mesh.py`` tessellates in, and does not. The
  design warns the metric can therefore be *smaller* on a compound or a
  shared-face shape — **we could not reproduce that**: the two agreed on all 14
  shapes measured, including a compound of two coincident boxes (changelog
  0113). Face indices are still validated against ``max(sidecar) + 1`` —
  :func:`sidecar_face_count` — because the sidecar is what an ordinal *is*, not
  because the metric was caught disagreeing.
* **A mesh-derived signature is not the B-rep.** Measured against ``face_info``
  on a cylinder: on a *planar* face the normal and centroid agree to 1e-6 and
  the tessellated area is 0.5% low (chord error, always low, never high). On
  the *closed curved* side face the areas still agree to 0.13% — and the
  normals do not agree at all, because an area-weighted normal over a surface
  that wraps a full turn very nearly cancels, while ``face_info`` reports a
  single ``normal_at(0.5, 0.5)`` sample. No tolerance reconciles those two.
  It is survivable because the matcher only ever compares a mesh-derived
  signature against another mesh-derived signature at the same
  ``MESH_TOLERANCE``: a consistent estimator beats an accurate one. On such
  faces the estimator is merely *wobbly*, which costs candidacy and yields an
  orphan — never a mis-pin. The payload labels these numbers as mesh-derived
  for the same reason, and ``face_info`` remains the tool for inspecting one
  face exactly.
* **A ``proposal_hunk`` anchor reads the persisted ``packet.json``, and only
  that.** ``service.packets.packet(...)`` regenerates a stale packet — which
  rebuilds geometry on both sides of the proposal and can move the proposal's
  own state — so validating or reading one comment would rewrite the evidence
  it comments on. :func:`stored_packet` is the one door, and a packet that is
  absent, frozen or unreadable is answered as a *state*, never built.
"""

from __future__ import annotations

import ast
import difflib
import hashlib
import json
import struct
import threading
from pathlib import Path

import numpy as np

from ..kernel import acm
from .model import NotFoundError, ValidationError
from .project import ProjectStore

RESOLUTION = ("ok", "moved", "orphaned", "unverified")

# Statuses that must explain themselves. ``moved`` carries the new address
# instead, which is the explanation.
_NEEDS_REASON = ("moved", "orphaned", "unverified")
_NEEDS_HINT = ("orphaned", "unverified")

# --------------------------------------------------------------- tolerances
#
# MEASURED, not guessed (risk R1's spike; every number is in
# docs/changelog/0113-prd008-anchor-resolution.md). 11 bundled parts across
# construction/fasteners/prototyping/rocketry x every numeric parameter x
# +1%/+10%/+30% = 91 rebuild pairs and 3 206 face pairs, ground truth
# established independently of this matcher: a chain of <=2% parameter steps,
# each step matched by mutual-nearest-neighbour on ABSOLUTE centroids (reliable
# at that step size, and using none of the features below), composed.
#
# Two findings set these values, and both contradict the design's guesses:
#
# 1. A face ordinal is NOT stable across a parameter change. 90.5% of faces
#    kept their ordinal, but prototyping/enclosure_lid renumbered 20 of its 44
#    faces for a 1% change. The matcher is load-bearing, not a fallback.
# 2. Filtering candidates by area MAKES the matcher mis-pin. Every rival a
#    filter removes is a rival that would have tripped the ambiguity check, so
#    the design's `AREA_REL` filter turned orphans into wrong answers: at the
#    design's five constants the sweep produced 63 mis-pins on faces whose
#    identity is known, plus 42 matches to faces that no longer existed. Area
#    is therefore a *final gate on the winner*, never a candidacy filter, and
#    the ambiguity margin does the safety work.
#
# At the values below the shipped code measured ZERO mis-pins over the 2 537
# face pairs whose truth is known, resolving 69.2% of them (1 756: 66.5% at
# +1%, 71.8% at +10%, 69.7% at +30%) and orphaning the other 30.8%. Of the 669
# faces the change destroyed, 657 were orphaned and 12 matched something.
# Loosening any of these to make a pin appear is the one change this module
# must never take.

# True pairs went as low as dot 0.9724 (a rotating face under a 30% angle
# change); rivals below this are a different surface. Kept deliberately loose:
# a true face filtered OUT of the pool is a face that cannot trip the ambiguity
# check, which is how a lone rival becomes a mis-pin.
NORMAL_DOT = 0.99
# The normalized-bbox centroid moved at most 0.1615 for a true pair (p99
# 0.1528 at +30%). Candidacy radius, generous on purpose for the same reason.
UVW_DIST = 0.15
# Best-minus-runner-up. The design guessed 0.05, which mis-pinned 60 faces;
# 0.15 still mis-pinned one; 0.20 mis-pinned none. This constant, not the
# feature filters, is what "orphan, never mis-pin" rests on.
AMBIGUITY_MARGIN = 0.20
# A FINAL GATE on the winner, not a filter: a face whose share of the shape's
# tessellated area differs by more than this is refused even when it is the
# only candidate (measured: two such matches in the run, one of them a 1 000x
# jump, both on faces that no longer existed). It compares the *fraction* of
# total area, not mm^2, for the same reason ``bbox_uvw`` exists: a parameter
# that scales the part multiplies every absolute area and moves no fraction.
AREA_REL = 0.5
#
# The design's fifth constant, ``STICKY_MARGIN = 0.02`` — keep the stored
# ordinal when it scores within 0.02 of the winner — is deliberately NOT
# implemented. It is unreachable: the measured ambiguity margin is 0.20, so any
# rival close enough to be a sticky tie has already made the match ambiguous,
# and an ambiguous match is an orphan. It changed no outcome in the spike
# because it cannot. Dead code that looks like a safety net is worse than no
# safety net.
# A partially-surviving line range below this mapped fraction is an orphan,
# never a low-quality "moved".
LINE_CONFIDENCE_MIN = 0.6

# The context lines a script_range anchor keeps on each side, used to
# disambiguate a snippet that occurs more than once.
CONTEXT_LINES = 3

_SIG_SUFFIX = ".facesig.json"

_TABLE_LOCK = threading.RLock()
_TABLE_CACHE: dict[str, list[dict]] = {}
_TABLE_CACHE_MAX = 64


# --------------------------------------------------------------- the result


def make_resolution(status: str, **fields) -> dict:
    """The one constructor for a resolution block.

    Enforces the vocabulary at the point of construction (PRD-003's
    ``make_item`` precedent): a status outside :data:`RESOLUTION` is a
    ``ValueError``, and so is an unexplained non-``ok`` status. A caller who
    cannot say *why* a thread lost its target has not resolved anything.
    """
    if status not in RESOLUTION:
        raise ValueError(
            f"unknown resolution status {status!r}; expected one of "
            f"{', '.join(RESOLUTION)}"
        )
    if status in _NEEDS_REASON and not fields.get("reason"):
        raise ValueError(f"resolution status {status!r} requires a reason")
    if status in _NEEDS_HINT and not fields.get("hint"):
        raise ValueError(
            f"resolution status {status!r} requires a hint: a reader told only "
            "that something is wrong will render it as 'fine'"
        )
    return {"status": status,
            **{key: value for key, value in fields.items() if value is not None}}


# ------------------------------------------------------------ the face table


def sidecar_face_count(face_ids: bytes | np.ndarray) -> int:
    """``max(sidecar) + 1`` — the face-index authority (R2).

    NOT ``metrics.n_faces``: that is ``len(shape.faces())``, deduplicated by
    hash, and it is a *count of distinct faces*, not the length of the walk
    ordinals are positions in. The two happened to agree on every shape we
    measured; that is not a reason to validate against the one that is only
    incidentally right.
    """
    ids = _face_ids(face_ids)
    if ids is None or ids.size == 0:
        return 0
    return int(ids.max()) + 1


def face_table(acm_bytes: bytes, face_ids: bytes | np.ndarray) -> list[dict]:
    """One row per face ordinal, from the mesh and its triangle→face sidecar.

    Per face: ``area`` (Σ triangle areas), ``centroid`` (area-weighted mean of
    triangle centroids), ``normal`` (normalized area-weighted sum of triangle
    normals) and ``bbox_uvw`` (the centroid inside the *whole shape's* bounding
    box as three fractions in [0, 1]; a degenerate axis maps to 0.5).

    An ordinal the tessellator emitted no triangle for still consumes its slot,
    with ``present: False`` — ordinals are explorer positions, so dropping one
    would shift every ordinal after it. A mesh and a sidecar that disagree
    about the triangle count are not a table: ``[]``, and the caller answers
    ``unverified``.
    """
    ids = _face_ids(face_ids)
    if ids is None or ids.size == 0:
        return []
    try:
        mesh = acm.parse(acm_bytes)
    except (ValueError, struct.error):
        return []
    positions = np.asarray(mesh["positions"], dtype=np.float64)
    indices = np.asarray(mesh["indices"], dtype=np.int64)
    if indices.shape[0] != ids.size or positions.shape[0] == 0:
        return []

    tri = positions[indices]                       # (nt, 3, 3)
    edge1 = tri[:, 1] - tri[:, 0]
    edge2 = tri[:, 2] - tri[:, 0]
    cross = np.cross(edge1, edge2)
    areas = 0.5 * np.linalg.norm(cross, axis=1)
    centroids = tri.mean(axis=1)

    n_faces = int(ids.max()) + 1
    face_area = np.bincount(ids, weights=areas, minlength=n_faces)
    face_centroid = np.stack(
        [np.bincount(ids, weights=areas * centroids[:, ax], minlength=n_faces)
         for ax in range(3)],
        axis=1,
    )
    # Triangle normals are already area-scaled (|cross| == 2 * area), so a
    # plain sum is the area-weighted sum.
    face_normal = np.stack(
        [np.bincount(ids, weights=cross[:, ax], minlength=n_faces)
         for ax in range(3)],
        axis=1,
    )

    lo = positions.min(axis=0)
    hi = positions.max(axis=0)
    span = hi - lo

    rows = []
    for index in range(n_faces):
        area = float(face_area[index])
        if area <= 0.0:
            rows.append({"index": index, "present": False, "area": 0.0,
                         "centroid": [0.0, 0.0, 0.0],
                         "normal": [0.0, 0.0, 0.0],
                         "bbox_uvw": [0.5, 0.5, 0.5]})
            continue
        centroid = face_centroid[index] / area
        normal = face_normal[index]
        length = float(np.linalg.norm(normal))
        unit = (normal / length) if length > 0 else np.zeros(3)
        uvw = [
            float((centroid[ax] - lo[ax]) / span[ax]) if span[ax] > 1e-12 else 0.5
            for ax in range(3)
        ]
        rows.append({
            "index": index,
            "present": True,
            "area": area,
            "centroid": [float(v) for v in centroid],
            "normal": [float(v) for v in unit],
            "bbox_uvw": uvw,
        })
    return rows


def _face_ids(face_ids: bytes | np.ndarray) -> np.ndarray | None:
    if isinstance(face_ids, np.ndarray):
        return face_ids.astype(np.int64, copy=False)
    if not isinstance(face_ids, (bytes, bytearray, memoryview)):
        return None
    raw = bytes(face_ids)
    if not raw or len(raw) % 4:
        return None
    return np.frombuffer(raw, dtype="<u4").astype(np.int64)


# ------------------------------------------------------- cached tables per part


def mesh_key(service, proj: str, part: str) -> str | None:
    """The cache key a build *would* use, computed without building.

    ``service._cache_key_for`` hashes the script text, the params and the
    densities — it never touches the kernel. Returns ``None`` for a part that
    is not in the manifest.
    """
    try:
        record = service.store.get_part(proj, part)
        return service._cache_key_for(proj, record)
    except (NotFoundError, ValidationError, OSError):
        # A part that is gone, a script file that is gone, a material the
        # project no longer defines: all "no key", all resolved as a state by
        # the caller rather than raised out of somebody's comment list.
        return None


def signature_table(service, proj: str, part: str,
                    key: str | None = None) -> tuple[str | None, list[dict]]:
    """``(cache_key, face_table)`` for a part, or ``(key, [])`` when the part
    has not been built at its current inputs.

    Never calls ``service.mesh_info`` or ``service._ensure_built`` — both
    build, and a comment list must stay a cheap read (design Decision 4). The
    table is memoized in-process by cache key and persisted beside the mesh as
    ``<key>.facesig.json``, which PRD-004's determinism stage does not compare
    (it names an explicit ``(".acm", ".faces.u32")`` tuple).
    """
    key = mesh_key(service, proj, part) if key is None else key
    if not key:
        return None, []
    with _TABLE_LOCK:
        cached = _TABLE_CACHE.get(key)
    if cached is not None:
        return key, cached

    cache = service.store.cache_dir(proj)
    mesh_path = cache / f"{key}.acm"
    sidecar_path = cache / f"{key}.faces.u32"
    sig_path = cache / f"{key}{_SIG_SUFFIX}"

    table: list[dict] = []
    if sig_path.is_file():
        try:
            stored = json.loads(sig_path.read_text(encoding="utf-8"))
            if isinstance(stored, list):
                table = stored
        except (OSError, json.JSONDecodeError):
            table = []
    if not table:
        if not (mesh_path.is_file() and sidecar_path.is_file()):
            return key, []
        try:
            table = face_table(mesh_path.read_bytes(), sidecar_path.read_bytes())
        except OSError:
            return key, []
        if table:
            _persist(sig_path, table)
    if table:
        with _TABLE_LOCK:
            if len(_TABLE_CACHE) >= _TABLE_CACHE_MAX:
                _TABLE_CACHE.clear()
            _TABLE_CACHE[key] = table
    return key, table


def _persist(path: Path, table: list[dict]) -> None:
    try:
        ProjectStore._atomic_write(path, json.dumps(table).encode())
    except OSError:
        pass  # a read-only or full cache directory must not break a listing


def forget_tables() -> None:
    """Drop the in-process table cache (tests, and a long-lived process that
    has churned through many cache keys)."""
    with _TABLE_LOCK:
        _TABLE_CACHE.clear()


# ------------------------------------------------------------- the face matcher


def signature_of(row: dict, key: str, n_faces: int,
                 whole_area: float | None = None) -> dict:
    """The stored evidence for a face.

    Two halves, on purpose. What a *human or an agent* reads: the absolute
    centroid, normal and tessellated area ("the 41.9 mm² face at z = 60") —
    these are what ``face_info`` would show and what a payload is for. What the
    *matcher* uses: ``bbox_uvw`` and ``area_frac``, both scale-invariant,
    because a parameter that scales the part moves every absolute number and
    neither of these.
    """
    total = row["area"] if not whole_area else whole_area
    return {
        "centroid": [round(v, 6) for v in row["centroid"]],
        "normal": [round(v, 6) for v in row["normal"]],
        "area_mm2": round(row["area"], 6),
        "area_frac": round(row["area"] / total, 9) if total > 0 else 0.0,
        "bbox_uvw": [round(v, 6) for v in row["bbox_uvw"]],
        "n_faces": n_faces,
        "mesh_key": key,
    }


def total_area(table: list[dict]) -> float:
    return float(sum(row["area"] for row in table if row.get("present")))


def match_face(signature: dict,
               table: list[dict]) -> tuple[dict | None, float, float, str | None]:
    """``(row, score, margin, refusal)`` — the winner, or why there is none.

    Candidacy is normal + normalized position only. Both radii are deliberately
    generous: every rival that stays in the pool is a rival that can trip the
    ambiguity check, and the spike measured that *narrowing* candidacy is what
    produces mis-pins. The winner must then clear the runner-up by
    :data:`AMBIGUITY_MARGIN` and survive the area gate.

    ``refusal`` is ``"no_candidate"``, ``"ambiguous"`` or ``"area_mismatch"``
    when the answer is "no face", and ``None`` when ``row`` is the answer. Six
    identical faces of a cube are genuinely indistinguishable by this signature
    once the part rotates, and the contract says orphan, never guess.

    The stored ordinal is deliberately not an input: see the note on
    ``STICKY_MARGIN`` above — under the measured ambiguity margin a tie-break
    toward it cannot fire, so taking it would only imply an influence it does
    not have. Whether the winner *is* the stored ordinal is the caller's
    question, and the difference between ``ok`` and ``moved``.
    """
    normal = np.asarray(signature.get("normal") or [0.0, 0.0, 0.0], dtype=float)
    uvw = np.asarray(signature.get("bbox_uvw") or [0.5, 0.5, 0.5], dtype=float)
    frac = signature.get("area_frac")
    area = float(signature.get("area_mm2") or 0.0)
    whole = total_area(table)

    scored: list[tuple[float, dict, float]] = []
    for row in table:
        if not row.get("present"):
            continue
        dot = float(np.dot(normal, np.asarray(row["normal"], dtype=float)))
        if dot < NORMAL_DOT:
            continue
        theirs_uvw = np.asarray(row["bbox_uvw"], dtype=float)
        dist = float(np.linalg.norm(uvw - theirs_uvw))
        if dist > UVW_DIST:
            continue
        if frac is None:
            # A signature from before ``area_frac`` existed: fall back to the
            # absolute comparison, which is right whenever nothing scaled.
            mine, theirs = area, float(row["area"])
        else:
            mine = float(frac)
            theirs = float(row["area"]) / whole if whole > 0 else 0.0
        area_rel = abs(theirs - mine) / max(mine, theirs, 1e-12)
        score = (0.5 * dot
                 + 0.3 * (1.0 - min(area_rel, 1.0))
                 + 0.2 * (1.0 - dist / UVW_DIST))
        scored.append((score, row, area_rel))

    if not scored:
        return None, 0.0, 0.0, "no_candidate"
    scored.sort(key=lambda item: (-item[0], item[1]["index"]))
    best_score, best, best_area_rel = scored[0]
    runner_up = scored[1][0] if len(scored) > 1 else 0.0
    margin = best_score - runner_up
    if len(scored) > 1 and margin < AMBIGUITY_MARGIN:
        return None, best_score, margin, "ambiguous"
    if best_area_rel > AREA_REL:
        return None, best_score, margin, "area_mismatch"
    return best, best_score, margin, None


# ---------------------------------------------------------- script snippets


def snippet_of(text: str, start: int, end: int) -> dict:
    """The evidence a ``script_range`` anchor stores: the exact lines, their
    sha256, and :data:`CONTEXT_LINES` lines of context each side.

    The snippet is capped at ``MAX_SNIPPET_LINES``/``MAX_SNIPPET_BYTES``; a
    range longer than the cap keeps its head, which is enough for the exact
    search that wins AC3 and bounds what a thread document can cost.
    """
    from .comments import MAX_SNIPPET_BYTES, MAX_SNIPPET_LINES

    lines = text.splitlines()
    body = lines[start - 1:end][:MAX_SNIPPET_LINES]
    while body and len("\n".join(body).encode("utf-8")) > MAX_SNIPPET_BYTES:
        body.pop()
    snippet = "\n".join(body)
    return {
        "snippet": snippet,
        "snippet_sha256": hashlib.sha256(snippet.encode("utf-8")).hexdigest(),
        "before": "\n".join(lines[max(0, start - 1 - CONTEXT_LINES):start - 1]),
        "after": "\n".join(lines[end:end + CONTEXT_LINES]),
    }


def find_snippet(lines: list[str], snippet: list[str], before: list[str],
                 after: list[str]) -> list[int]:
    """Every 0-based offset where *snippet* occurs exactly, narrowed by the
    stored context when it occurs more than once.

    Exact string work only, no heuristics: this is where AC3 is won.
    """
    if not snippet:
        return []
    span = len(snippet)
    hits = [i for i in range(len(lines) - span + 1)
            if lines[i:i + span] == snippet]
    if len(hits) <= 1:
        return hits
    scored = []
    for offset in hits:
        head = lines[max(0, offset - len(before)):offset]
        tail = lines[offset + span:offset + span + len(after)]
        scored.append(((head == before) + (tail == after), offset))
    best = max(score for score, _ in scored)
    if best == 0:
        return hits
    return [offset for score, offset in scored if score == best]


def line_map(old: list[str], new: list[str], start: int,
             end: int) -> tuple[int, int, float] | None:
    """Map the 1-based inclusive range ``[start, end]`` from *old* to *new*.

    ``difflib`` opcodes: ``equal`` blocks map by offset. A range wholly inside
    an equal block maps exactly (confidence 1.0); a range that survives
    partially maps over the span its surviving lines cover, with the mapped
    fraction as the confidence. A range with no surviving line returns
    ``None`` — the caller orphans it.
    """
    mapping: dict[int, int] = {}
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(
            None, old, new, autojunk=False).get_opcodes():
        if tag == "equal":
            for offset in range(i2 - i1):
                mapping[i1 + offset] = j1 + offset
    wanted = [index for index in range(start - 1, end)]
    mapped = [mapping[index] for index in wanted if index in mapping]
    if not mapped:
        return None
    return min(mapped) + 1, max(mapped) + 1, len(mapped) / len(wanted)


# --------------------------------------------------------------- validation


def validate_face(service, proj: str, fields: dict) -> dict:
    """FR1's face rules, against the sidecar — never against a metric.

    Refuses an unbuilt part (with the hint that says what to do about it), a
    reference part (an imported STL is one welded mesh face with no surface,
    and ``handlers/reference.py`` reports a hardcoded ``n_faces: 1``), and an
    index outside ``max(sidecar) + 1``. Stores the signature the matcher will
    re-identify the face by.
    """
    part = _require_part(service, proj, fields.get("part"))
    record = service.store.get_part(proj, part)
    if record.kind != "script":
        raise ValidationError(
            f"part {part!r} is a {record.kind} part: an imported mesh is one "
            "welded face with no surface, so it cannot carry a face anchor",
            {"part": part, "kind": record.kind},
        )
    index = fields.get("face_index")
    if isinstance(index, bool) or not isinstance(index, int) or index < 0:
        raise ValidationError(
            "anchor.face_index must be a non-negative integer",
            {"face_index": index},
        )
    key, table = signature_table(service, proj, part)
    if not table:
        raise ValidationError(
            f"part {part!r} has no built mesh at its current parameters, so "
            "there is no face to anchor to — build the part first",
            {"part": part, "hint": "call get_part or rebuild_part, then retry"},
        )
    if index >= len(table):
        raise ValidationError(
            f"face_index {index} is out of range: part {part!r} has "
            f"{len(table)} faces",
            {"part": part, "face_index": index, "n_faces": len(table)},
        )
    row = table[index]
    if not row["present"]:
        raise ValidationError(
            f"face {index} of part {part!r} has no tessellated area, so there "
            "is nothing to point at",
            {"part": part, "face_index": index},
        )
    return {
        "part": part,
        "face_index": index,
        "signature": signature_of(row, key or "", len(table), total_area(table)),
    }


def validate_script_range(service, proj: str, fields: dict) -> dict:
    """FR1's line rules, against the current script, plus the evidence tier 1
    resolves with: the exact snippet, its sha256 and its context."""
    part = _require_part(service, proj, fields.get("part"))
    record = service.store.get_part(proj, part)
    if record.kind != "script":
        raise ValidationError(
            f"part {part!r} is a {record.kind} part and has no script",
            {"part": part, "kind": record.kind},
        )
    start = _line_number(fields.get("start"), "anchor.start")
    end = _line_number(fields.get("end"), "anchor.end")
    text = service.store.read_script(proj, part)
    total = len(text.splitlines())
    if end < start:
        raise ValidationError(
            f"anchor.end ({end}) is before anchor.start ({start})",
            {"start": start, "end": end},
        )
    if end > total:
        raise ValidationError(
            f"lines {start}-{end} run past the end of part {part!r}'s script "
            f"({total} lines)",
            {"part": part, "start": start, "end": end, "lines": total},
        )
    return {"part": part, "start": start, "end": end,
            **snippet_of(text, start, end)}


def validate_proposal_hunk(service, proj: str, fields: dict) -> dict:
    """FR1's hunk rules, against the **persisted** ``packet.json`` only.

    A hunk anchor points at a *measurement*, so the measurement must already
    exist: an absent packet is a ``validation_error`` telling the caller to
    build one, never a build. Captures the evidence Decision 8 re-maps by —
    the hunk's header byte-for-byte, and the generation it was read from.
    """
    store = _proposal_store(service)
    if store is None:
        raise ValidationError(
            "this server has no change proposals (the pack needs git), so "
            "there is no review packet to anchor a thread to",
            {"hint": "anchor to a script_range on the part instead"},
        )
    pid = _proposal_id(fields.get("proposal"))
    try:
        store.load(proj, pid)
    except (NotFoundError, ValidationError) as exc:
        raise ValidationError(f"unknown proposal {pid!r}",
                              {"proposal": pid}) from exc
    packet = stored_packet(service, proj, pid)
    if packet is None:
        raise ValidationError(
            f"proposal {pid} has no review packet on disk, so there is no "
            "hunk to point at",
            {"proposal": pid,
             "hint": "call proposal_packet first — a thread anchors to a diff "
                     "that has been measured, and reading one never builds a "
                     "packet"},
        )
    diffs = packet_diffs(packet)
    file = fields.get("file")
    if not isinstance(file, str) or file not in diffs:
        raise ValidationError(
            f"proposal {pid}'s review packet has no script diff for {file!r}",
            {"proposal": pid, "file": file, "files": sorted(diffs)},
        )
    diff = diffs[file]
    if diff.get("truncated") or diff.get("unified") is None:
        raise ValidationError(
            f"the diff for {file} in proposal {pid} was truncated (too large "
            "to keep), so its hunks carry no reviewable text",
            {"proposal": pid, "file": file, "truncated": True},
        )
    hunks = [hunk for hunk in (diff.get("hunks") or []) if isinstance(hunk, dict)]
    index = fields.get("hunk")
    if isinstance(index, bool) or not isinstance(index, int) \
            or not 0 <= index < len(hunks):
        raise ValidationError(
            f"anchor.hunk must be a hunk index of {file} in proposal {pid}: "
            f"0 <= hunk < {len(hunks)}",
            {"proposal": pid, "file": file, "hunk": index, "hunks": len(hunks)},
        )
    header = hunks[index].get("header")
    if not isinstance(header, str) or not header:
        raise ValidationError(
            f"hunk {index} of {file} in proposal {pid} has no header to "
            "identify it by",
            {"proposal": pid, "file": file, "hunk": index},
        )
    return {"proposal": pid, "file": file, "hunk": index,
            "hunk_header": header, "generation": packet.get("generation") or ""}


def _proposal_id(value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValidationError(
            "anchor.proposal must be a proposal id like '3'",
            {"proposal": value},
        )
    pid = str(value).strip()
    if not pid:
        raise ValidationError("anchor.proposal must be a proposal id like '3'",
                              {"proposal": value})
    return pid


def _proposal_store(service):
    """PRD-002's ``ProposalStore``, or ``None`` when the proposals pack is not
    installed (it self-disables without git, and comments do not)."""
    return getattr(getattr(service, "proposals", None), "store", None)


def stored_packet(service, proj: str, pid: str) -> dict | None:
    """The review packet exactly as PRD-002 persisted it, or ``None``.

    ``packet.json`` and nothing else. **Never**
    ``service.packets.packet(...)``: that regenerates a stale packet, which
    rebuilds the geometry on both sides of the proposal and can move the
    proposal's own state — so validating or reading one comment would rewrite
    the evidence it is commenting on.
    """
    store = _proposal_store(service)
    if store is None:
        return None
    try:
        data = json.loads(
            store.packet_path(proj, pid).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, NotFoundError, ValidationError):
        return None
    return data if isinstance(data, dict) else None


def packet_diffs(packet: dict) -> dict[str, dict]:
    """``{path: script_diff}`` over the packet's part rows — the files a
    ``proposal_hunk`` anchor may name."""
    diffs: dict[str, dict] = {}
    for part in packet.get("parts") or []:
        diff = part.get("script_diff") if isinstance(part, dict) else None
        if isinstance(diff, dict) and isinstance(diff.get("path"), str):
            diffs[diff["path"]] = diff
    return diffs


def _require_part(service, proj: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError("anchor.part must be a non-empty string")
    known = service.store.part_ids(proj)
    if value not in known:
        raise ValidationError(f"unknown part {value!r}",
                              {"part": value, "parts": known})
    return value


def _line_number(value: object, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValidationError(f"{what} must be a 1-based line number",
                              {"value": value})
    return value


# --------------------------------------------------------------- resolution


def read_context(service, proj: str) -> dict:
    """What every anchor in one page resolves *against*: the reader's current
    branch and its head.

    Resolved once per listing, never per thread — a 40-thread page must not be
    40 git calls (design Decision 4's cost budget).
    """
    branch, head = "", ""
    branches = getattr(service, "branches", None)
    history = getattr(service, "history", None)
    try:
        if branches is not None:
            branch = branches.current(proj) or ""
        if history is not None and history.available():
            canonical = service.store.canonical_path_of(proj)
            head = (history.resolve_branch(canonical, branch) if branch
                    else history.head(service.store.path_of(proj))) or ""
    except Exception:                                  # noqa: BLE001
        # Provenance is context, and a broken git layer must never turn a
        # comment list into a 500. An empty head degrades tier 2 to
        # ``unverified``, which is the honest answer.
        pass
    return {"branch": branch, "head": head,
            "root": service.store.path_of(proj)}


def resolve(service, proj: str, anchor: object,
            context: dict | None = None) -> dict:
    """The current status of one stored anchor. Never raises, never builds.

    Every result carries ``against: {branch, head}`` — what it was resolved
    against — because a thread authored on another branch that resolves
    ``orphaned`` here would be telling the reader their face was cut away when
    they merely switched branches (design Decision 7).
    """
    context = read_context(service, proj) if context is None else context
    against = {"branch": context.get("branch", ""),
               "head": context.get("head", "")}
    if not isinstance(anchor, dict):
        return make_resolution(
            "unverified", reason="malformed_anchor",
            hint="the stored anchor is not an object; nothing can be resolved",
            against=against)
    handler = _RESOLVERS.get(anchor.get("kind"))
    if handler is None:
        return make_resolution(
            "unverified", reason="unknown_kind",
            hint=f"anchor kind {anchor.get('kind')!r} has no resolver in this "
                 "build", against=against)
    try:
        result = handler(service, proj, anchor, context)
    except Exception as exc:                           # noqa: BLE001
        # A listing is a read: a surprise in one thread's anchor must not
        # take the page down, and "we could not look" is exactly what
        # ``unverified`` means.
        result = make_resolution(
            "unverified", reason="resolver_failed",
            hint=f"{type(exc).__name__}: {exc}")
    result["against"] = against
    return result


def _elsewhere(anchor: dict, context: dict, what: str) -> dict | None:
    """Decision 7: a target missing here while the anchor names another branch
    is ``unverified``, not ``orphaned``."""
    authored = anchor.get("branch") or ""
    current = context.get("branch") or ""
    if authored and current and authored != current:
        return make_resolution(
            "unverified", reason="other_branch",
            hint=f"{what} does not exist on {current!r}; this thread was "
                 f"authored on {authored!r} — switch branches to resolve it")
    return None


def _resolve_part(service, proj, anchor, context) -> dict:
    part = anchor.get("part")
    if part in service.store.part_ids(proj):
        return make_resolution("ok")
    return _elsewhere(anchor, context, f"part {part!r}") or make_resolution(
        "orphaned", reason="part_removed",
        hint=f"part {part!r} is no longer in this project")


def _resolve_instance(service, proj, anchor, context) -> dict:
    instance = anchor.get("instance")
    known = [i["id"] for i in
             service.store.manifest(proj)["assembly"]["instances"]]
    if instance in known:
        return make_resolution("ok")
    return _elsewhere(anchor, context, f"instance {instance!r}") or \
        make_resolution("orphaned", reason="instance_removed",
                        hint=f"assembly instance {instance!r} is no longer "
                             "placed in this project")


def _resolve_param(service, proj, anchor, context) -> dict:
    part, param = anchor.get("part"), anchor.get("param")
    if part not in service.store.part_ids(proj):
        return _elsewhere(anchor, context, f"part {part!r}") or make_resolution(
            "orphaned", reason="part_removed",
            hint=f"part {part!r} is no longer in this project")
    names = _param_names(service, proj, part)
    if names is None:
        return make_resolution(
            "unverified", reason="spec_unavailable",
            hint=f"part {part!r}'s parameter spec is not statically readable "
                 "and is not cached; open the part to load it")
    if param in names:
        return make_resolution("ok")
    return make_resolution(
        "orphaned", reason="param_removed",
        hint=f"parameter {param!r} is no longer declared by part {part!r}")


def _param_names(service, proj: str, part: str) -> set[str] | None:
    """The part's parameter names without a kernel call, or ``None``.

    ``service._params_spec`` runs the ``inspect`` handler, which is a kernel
    round trip — forbidden here (R8). Two cheap authorities instead: the
    spec cache, if this process has already inspected exactly this script, and
    otherwise a static ``ast`` read of a literal module-level ``PARAMS`` dict
    (parsed, never executed). A script that computes its ``PARAMS`` at import
    time and has not been inspected is honestly ``unverified``.
    """
    try:
        text = service.store.read_script(proj, part)
    except Exception:                                  # noqa: BLE001
        return None
    cache = getattr(service, "_spec_cache", None)
    if isinstance(cache, dict):
        spec = cache.get(hashlib.sha256(text.encode()).hexdigest())
        if isinstance(spec, dict):
            return set(spec)
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return None
    for node in tree.body:
        targets = getattr(node, "targets", [])
        if not (isinstance(node, ast.Assign) and len(targets) == 1):
            continue
        target = targets[0]
        if not (isinstance(target, ast.Name) and target.id == "PARAMS"):
            continue
        try:
            value = ast.literal_eval(node.value)
        except (ValueError, SyntaxError):
            return None
        if isinstance(value, dict):
            return {key for key in value if isinstance(key, str)}
    return None


_REFUSAL_HINTS = {
    "no_candidate": "no face of the current geometry matches this signature — "
                    "the face it named was most likely cut away",
    "ambiguous": "two faces match this signature equally well, and pointing at "
                 "the wrong one is worse than pointing at nothing",
    "area_mismatch": "the only matching face is a very different size, which is "
                     "a different face wearing the same orientation",
}


def _resolve_face(service, proj, anchor, context) -> dict:
    part = anchor.get("part")
    index = anchor.get("face_index")
    signature = anchor.get("signature") or {}
    if part not in service.store.part_ids(proj):
        return _elsewhere(anchor, context, f"part {part!r}") or make_resolution(
            "orphaned", reason="part_removed",
            hint=f"part {part!r} is no longer in this project")
    record = service.store.get_part(proj, part)
    if record.kind != "script":
        return make_resolution(
            "orphaned", reason="not_a_script_part",
            hint=f"part {part!r} is now a {record.kind} part: an imported mesh "
                 "carries no B-rep faces")
    key, table = signature_table(service, proj, part)
    if not table:
        return make_resolution(
            "unverified", reason="part_not_built",
            hint=f"part {part!r} has no mesh cached at its current parameters; "
                 "listing threads never rebuilds, so build the part to resolve "
                 "this anchor")
    if key and key == signature.get("mesh_key") and isinstance(index, int) \
            and 0 <= index < len(table) and table[index]["present"]:
        # The geometry is byte-identical to what the author pointed at: there
        # is nothing to re-match.
        return make_resolution("ok", confidence=1.0, face_index=index,
                               n_faces=len(table))
    if not signature.get("normal"):
        return make_resolution(
            "unverified", reason="no_signature",
            hint="this anchor predates face signatures; re-create the thread "
                 "to make it resolvable")
    stored = index if isinstance(index, int) else -1
    row, score, margin, refusal = match_face(signature, table)
    if row is None:
        return make_resolution("orphaned", reason=refusal,
                               hint=_REFUSAL_HINTS[refusal],
                               confidence=round(score, 4),
                               margin=round(margin, 4),
                               n_faces=len(table))
    status = "ok" if row["index"] == stored else "moved"
    return make_resolution(
        status, face_index=row["index"], confidence=round(score, 4),
        margin=round(margin, 4), n_faces=len(table),
        reason=None if status == "ok" else "rematched_by_signature")


def _resolve_script_range(service, proj, anchor, context) -> dict:
    part = anchor.get("part")
    start, end = anchor.get("start"), anchor.get("end")
    if part not in service.store.part_ids(proj):
        return _elsewhere(anchor, context, f"part {part!r}") or make_resolution(
            "orphaned", reason="part_removed",
            hint=f"part {part!r} is no longer in this project")
    record = service.store.get_part(proj, part)
    if record.kind != "script":
        return make_resolution(
            "orphaned", reason="not_a_script_part",
            hint=f"part {part!r} is now a {record.kind} part and has no script")
    text = service.store.read_script(proj, part)
    lines = text.splitlines()
    snippet = (anchor.get("snippet") or "").splitlines()
    if not snippet or not isinstance(start, int) or not isinstance(end, int):
        return make_resolution(
            "unverified", reason="no_snippet",
            hint="this anchor stored no snippet; re-create the thread to make "
                 "it resolvable")

    # --- tier 1: exact snippet, no git, no heuristics. AC3 is won here.
    #
    # The reported range keeps the anchor's own length rather than the
    # snippet's: a range longer than MAX_SNIPPET_LINES stored a truncated
    # snippet, and reporting the truncation as the range would silently shrink
    # the comment's target every time it is read.
    span = end - start
    if lines[start - 1:start - 1 + len(snippet)] == snippet:
        return make_resolution("ok", start=start, end=end, confidence=1.0)
    hits = find_snippet(lines, snippet, (anchor.get("before") or "").splitlines(),
                        (anchor.get("after") or "").splitlines())
    if len(hits) == 1:
        first = hits[0] + 1
        return make_resolution("moved", reason="snippet_found_verbatim",
                               start=first, end=min(first + span, len(lines)),
                               confidence=1.0)

    # --- tier 2: a real line map against the blob at the anchor's own head.
    old = _blob_lines(service, proj, part, anchor.get("head") or "",
                      context.get("root"))
    if old is None:
        reason = "no_git" if not _git_available(service) else (
            "head_unreachable" if anchor.get("head") else "no_head")
        return make_resolution(
            "unverified", reason=reason,
            hint="the script as it was when this thread was opened is not "
                 "readable, so the range cannot be remapped — we did not look, "
                 "so this is not an orphan")
    mapped = line_map(old, lines, start, end)
    if mapped is None:
        return make_resolution(
            "orphaned", reason="lines_removed",
            hint=f"lines {start}-{end} of part {part!r} were deleted or "
                 "rewritten; the thread keeps its last-known range")
    new_start, new_end, confidence = mapped
    if confidence < LINE_CONFIDENCE_MIN:
        return make_resolution(
            "orphaned", reason="low_confidence",
            hint=f"only {confidence:.0%} of lines {start}-{end} survive the "
                 "edit — a partial remap would point at the wrong code",
            confidence=round(confidence, 4))
    status = "ok" if (new_start, new_end) == (start, end) else "moved"
    return make_resolution(
        status, start=new_start, end=new_end, confidence=round(confidence, 4),
        reason=None if status == "ok" else "remapped_by_diff")


def _resolve_proposal_hunk(service, proj, anchor, context) -> dict:
    """Decision 8's table, read off the persisted packet.

    The header is the identity and the index is not: a regeneration renumbers
    hunks freely, so a re-map is a byte-identical ``header`` match *within the
    new generation*, unique or nothing. Three deliberate choices:

    * **A frozen packet is ``unverified``, whatever its generation says.** The
      diff it describes is history the moment a merge lands, and this thread is
      the record of a review of exactly that; answering ``ok`` would invite a
      UI to open a live diff that no longer exists.
    * **A new generation is ``moved``, even to the same index.** A generation
      is one measurement; a different one measured different commits, so
      claiming the reviewed text is unchanged would be a claim nobody checked.
    * **Two matching headers orphan.** Same rule as the face matcher:
      ambiguity is an orphan, never a guess.

    Decision 7's cross-branch rule does not apply here — a proposal is
    branch-free storage that *names* its two branches, so it is equally
    readable from either one.
    """
    from .proposals import TERMINAL

    pid = str(anchor.get("proposal") or "")
    file = anchor.get("file")
    index = anchor.get("hunk")
    header = anchor.get("hunk_header")
    store = _proposal_store(service)
    if store is None:
        return make_resolution(
            "unverified", reason="proposals_unavailable",
            hint="this server has no change proposals (the pack needs git), so "
                 "the review packet this thread points at cannot be read — we "
                 "did not look, so this is not an orphan")
    try:
        proposal = store.load(proj, pid)
    except (NotFoundError, ValidationError):
        return make_resolution(
            "orphaned", reason="proposal_removed",
            hint=f"proposal {pid} is no longer in this project; the thread "
                 "keeps the hunk it was opened on")
    packet = stored_packet(service, proj, pid)
    if packet is None:
        return make_resolution(
            "orphaned", reason="packet_missing",
            hint=f"proposal {pid}'s review packet is not on disk any more, and "
                 "resolving a comment never builds one")
    state = proposal.get("state")
    if packet.get("frozen") or state in TERMINAL:
        return make_resolution(
            "unverified", reason="packet_frozen",
            hint=f"proposal {pid} is {state} and its review packet is frozen: "
                 "the diff this thread reviewed is history now and is never "
                 "measured again",
            generation=packet.get("generation") or None)
    diffs = packet_diffs(packet)
    diff = diffs.get(file) if isinstance(file, str) else None
    if diff is None:
        return make_resolution(
            "orphaned", reason="file_not_in_diff",
            hint=f"{file} is no longer part of what proposal {pid} changes")
    hunks = [hunk for hunk in (diff.get("hunks") or []) if isinstance(hunk, dict)]
    generation = packet.get("generation") or ""
    if (generation and generation == anchor.get("generation")
            and isinstance(index, int) and 0 <= index < len(hunks)
            and hunks[index].get("header") == header):
        return make_resolution("ok", hunk=index, generation=generation,
                               confidence=1.0)
    if not isinstance(header, str) or not header:
        return make_resolution(
            "unverified", reason="no_header",
            hint="this anchor stored no hunk header, so there is nothing to "
                 "re-match the regenerated packet against; re-create the "
                 "thread on the current packet")
    matches = [hunk for hunk in hunks if hunk.get("header") == header]
    if len(matches) == 1:
        found = matches[0].get("index")
        if isinstance(found, bool) or not isinstance(found, int):
            found = hunks.index(matches[0])
        return make_resolution(
            "moved", reason="hunk_remapped_by_header", hunk=found,
            generation=generation, confidence=1.0,
            new_start=matches[0].get("new_start"))
    hint = (
        f"{file}'s diff in proposal {pid} no longer contains the hunk this "
        "thread was opened on, so it was rewritten by a regeneration"
        if not matches else
        f"{file}'s diff now contains that hunk header {len(matches)} times, "
        "and pointing at the wrong one is worse than pointing at nothing"
    )
    return make_resolution("orphaned", reason="hunk_regenerated", hint=hint,
                           generation=generation)


def _git_available(service) -> bool:
    history = getattr(service, "history", None)
    try:
        return bool(history is not None and history.available())
    except Exception:                                  # noqa: BLE001
        return False


def _blob_lines(service, proj: str, part: str, head: str,
                root: Path | None) -> list[str] | None:
    """The part's script as it was at *head*, or ``None`` when we cannot look.

    Through ``history._run_bytes`` — hermetic env, 10 s timeout — never a raw
    ``subprocess``, and undecoded, because a script is content and the text
    path replaces every byte it cannot decode.
    """
    if not head or not _git_available(service):
        return None
    path = root if root is not None else service.store.path_of(proj)
    try:
        result = service.history._run_bytes(
            path, "cat-file", "blob", f"{head}:parts/{part}.py", check=False)
    except Exception:                                  # noqa: BLE001
        return None
    if result.returncode != 0 or not result.stdout:
        return None
    try:
        return result.stdout.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        return None


_RESOLVERS = {
    "part": _resolve_part,
    "face": _resolve_face,
    "param": _resolve_param,
    "script_range": _resolve_script_range,
    "instance": _resolve_instance,
    "proposal_hunk": _resolve_proposal_hunk,
}
