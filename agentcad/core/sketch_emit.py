"""The shared sketch emitter: a solved sketch -> idiomatic build123d source.

This module is the **single** emitter for both layers (PRD-009 FR9/AC1). The
GUI used to format its own snippet in `frontend/js/sketcher.js`; an agent had
nothing at all. One emitter, on the server, means the browser and an agent
produce **byte-identical** code for the same spec, and it means the golden test
can rebuild that code through the kernel instead of through a browser.

It runs in the **server** process, so — like `toolkit/sketch.py` — it must
never import build123d/OCP. Emitting build123d *source* is not importing
build123d.

## Three rules, each traceable to a measurement (design Decision 10)

1. **Shared vertex literals.** A junction where two curves meet is formatted
   **once** and referenced by both through a `v<n>` binding. A derived
   endpoint (an arc's `start`/`end`) is formatted from the **solved point**,
   never recomputed by the reader from a rounded centre/radius/angle.
2. **Nine decimals**, up from `fmtNum`'s six. Measured: a centre-parametrized
   arc chain at 6 decimals leaves a **7.58e-7 mm** gap and `make_face()`
   raises *"Face can only be created with closed wires"*; 7 decimals
   (3.7e-8 mm) is the first that closes. Nine leaves two orders of margin and
   still reads as a number a human will edit.
3. **Endpoint-anchored constructors** in a chain — `RadiusArc(start, end, r,
   short_sagitta=...)` and `ThreePointArc` over `CenterArc`. Measured:
   endpoint-anchored closes at **every** precision tested, because the arc and
   both neighbours are literally the same rounded pair.

## The closure gate

Before returning, the emitter measures every junction **from the formatted
literals** — parsed back exactly as the reader will parse them — against every
solved endpoint that literal stands in for, and against the endpoint a
centre-parametrized arc would derive. Above `CLOSURE_TOL_MM` it **refuses to
emit `make_face()`**, raising an `EmitError` naming the junction; on an open
chain, where nothing will fail to close, it reports the same measurement as a
warning. Emitting code that will not rebuild is the one failure this feature
must not produce.

That measurement is a superset of the design's "vertex-to-vertex gap": with
shared literals a chain's vertex-to-vertex gap is zero by construction, so the
honest question is how far the shared literal moved the geometry it stands
for. Both failures the design names — a 6-decimal centre-parametrized arc, and
a solution whose two chained endpoints are 1e-6 mm apart — trip it.

## The entity -> build123d mapping (FR9/FR11)

| entity | emitted |
|---|---|
| line chain | `Polyline(v0, v1, ...)`, or `Line(a, b)` per segment in a mixed chain |
| arc (centre-authored, in a chain) | `RadiusArc(start, end, +-r, short_sagitta=...)` |
| arc (3-point authored) | `ThreePointArc(start, mid, end)` |
| arc sweeping a full turn | `CenterArc(...)` (a `RadiusArc` cannot express it) |
| full circle | `Circle(radius=...)` under `Locations(...)` |
| full ellipse | `Ellipse(x_radius=..., y_radius=..., rotation=...)` under `Locations(...)` |
| elliptical arc | `EllipticalCenterArc(centre, a, b, start_angle=..., arc_size=..., rotation=...)` |
| spline | `Spline(p0, ..., pn)`, with `tangents=` for a pinned end |
| slot, standalone | `SlotCenterToCenter(sep, height, rotation=...)` under `Locations` |
| slot, tied to the sketch | its compiled primitives — two `Line`s, two `RadiusArc`s |

The slot row is the trap: `SlotCenterToCenter` is a BuildSketch **face** at the
origin, not a curve that can join a `BuildLine` chain, so a slot that carries
constraints of its own (or whose primitives share a junction with anything
else) emits as the primitives slice 6 already compiled.

## Round-trip persistence (FR10)

`emit(..., persist="profile")` wraps the code in a **structured block in the
script** — the `push_pull` precedent (`core/tools_facemod.PUSH_PULL_MARKER`),
not a sidecar file, because the PRD's non-goals say the part script is the
only artifact and a script-resident block gets branching, restore, undo, merge
and the proposal diff for free:

    # --- agentcad sketch "profile" (auto-generated; edit or remove freely) ---
    # agentcad-sketch-spec: {"v": 1, "entities": {...}, "constraints": [...]}
    # agentcad-sketch-hash: sha256:...
    def sketch_profile():
        ...
    # --- end agentcad sketch "profile" ---

`parse_blocks(script)` reads them back. **The code is the source of truth for
geometry; the spec block is provenance**, so a hash mismatch is reported
(`diverged`) and never repaired: the sketcher opens read-only and asks. A
block whose spec will not parse is `unverified` — "we cannot tell", which is
never rendered as "there is no sketch" (the PRD-008 rule).
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import re
import tokenize

# Emission precision. `fmtNum`'s 6 is measured unsafe; 7 is the first that
# closes; 9 keeps two orders of margin.
DECIMALS = 9

# The maximum a junction's shared literal may move any endpoint it stands for.
# OCCT's vertex tolerance is ~1e-7 mm; this is two orders inside it.
CLOSURE_TOL_MM = 1e-8

# Endpoints closer than this are one junction. Deliberately far above
# CLOSURE_TOL_MM: whether two endpoints *meet* is a topology question, and
# whether the junction is tight enough to emit is the closure gate's.
JOIN_TOL_MM = 1e-3

STYLES = ("function", "buildline")
ARC_ANCHORS = ("endpoint", "center")

# A sweep this close to a full turn cannot be expressed as a RadiusArc (its
# two endpoints coincide), so it falls back to CenterArc with a warning.
FULL_TURN_TOL_DEG = 1e-9


# ------------------------------------------------------ round-trip block
SPEC_VERSION = 1
DEFAULT_NAME = "profile"
BLOCK_MARKER = ('# --- agentcad sketch "{name}" (auto-generated; edit or '
                'remove freely) ---')
BLOCK_END = '# --- end agentcad sketch "{name}" ---'
SPEC_PREFIX = "# agentcad-sketch-spec: "
HASH_PREFIX = "# agentcad-sketch-hash: "
HASH_ALGO = "sha256"

# The entity sections carried in the persisted spec, in the order they are
# written. Anything else on the spec (`initial`, `drag`, `diagnostics`) is a
# property of one *call*, not of the sketch, and is deliberately not persisted.
PERSIST_KINDS = ("points", "lines", "circles", "arcs", "ellipses", "splines",
                 "slots")

_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_MARKER_RE = re.compile(
    r'^#\s*---\s*agentcad sketch "([A-Za-z_][A-Za-z0-9_]*)"\s*\(auto-generated')
_END_RE = re.compile(
    r'^#\s*---\s*end agentcad sketch "([A-Za-z_][A-Za-z0-9_]*)"')
_DEF_RE = re.compile(r"^def sketch_([A-Za-z_][A-Za-z0-9_]*)\s*\(", re.M)
# What `plane["part"]` may say. It goes into the provenance comment naming the
# part a sketch-on-face was taken from, and the GUI sends `build(p)`; dotted
# attribute access and a bare name are the other shapes a caller has reason to
# send. Anything else — in particular anything with a newline or a quote in it
# — is refused rather than written (see `_plane_header`).
_PART_REF_ATOM = r"[A-Za-z_][A-Za-z0-9_]*(?:\([A-Za-z_][A-Za-z0-9_]*\))?"
_PART_REF_RE = re.compile(rf"^{_PART_REF_ATOM}(?:\.{_PART_REF_ATOM})*$")


class EmitError(ValueError):
    """The emitter refuses to produce code that will not rebuild."""


# ---------------------------------------------------------------- formatting
def fmt(value: float, decimals: int = DECIMALS) -> str:
    """One number, one literal — the only place a coordinate becomes text."""
    x = round(float(value), decimals)
    if x == 0:
        x = 0.0                      # never emit -0.0
    s = f"{x:.{decimals}f}"
    if "." in s:
        s = s.rstrip("0")
        if s.endswith("."):
            s += "0"
    return s


def _pt(xy, decimals: int) -> str:
    return f"({fmt(xy[0], decimals)}, {fmt(xy[1], decimals)})"


def _round_trip(xy, decimals: int) -> tuple[float, float]:
    """The coordinate **as the reader will parse it**, not as we hold it."""
    return float(fmt(xy[0], decimals)), float(fmt(xy[1], decimals))


# ------------------------------------------------------------------ geometry
def _handle_xy(solution: dict, handle: str) -> tuple[float, float]:
    """Resolve any endpoint handle to its solved coordinates.

    Handles a plain point, an arc's virtual handle (`a1.end`), a spline end
    (`sp.start`) and a compiled sub-entity's handle (`slot1.arc_a.end`) — the
    dotted namespace `toolkit.sketch` owns.
    """
    points = solution.get("points") or {}
    if handle in points:
        p = points[handle]
        return float(p["x"]), float(p["y"])
    base, _, attr = handle.rpartition(".")
    arcs = solution.get("arcs") or {}
    if base in arcs:
        arc = arcs[base]
        if attr in ("start", "end"):
            return float(arc[attr]["x"]), float(arc[attr]["y"])
        if attr == "center":
            return float(arc["cx"]), float(arc["cy"])
    ellipses = solution.get("ellipses") or {}
    if base in ellipses:
        e = ellipses[base]
        if attr in ("start", "end") and e.get("bounded"):
            return float(e[attr]["x"]), float(e[attr]["y"])
        if attr == "center":
            return float(e["cx"]), float(e["cy"])
    splines = solution.get("splines") or {}
    if base in splines and attr in ("start", "end"):
        coords = splines[base]["coords"]
        p = coords[0] if attr == "start" else coords[-1]
        return float(p["x"]), float(p["y"])
    circles = solution.get("circles") or {}
    if base in circles and attr == "center":
        c = circles[base]
        return float(c["cx"]), float(c["cy"])
    raise EmitError(
        f"cannot resolve endpoint handle {handle!r} in the solution; the "
        "emitter needs the solved coordinates of every chain endpoint")


def _sweep_deg(arc: dict) -> float:
    """The signed sweep, as the solver reports it (`end` carries it whole)."""
    return float(arc["end_deg"]) - float(arc["start_deg"])


# -------------------------------------------------------------------- plan
def _members(solution: dict, spec: dict) -> list[dict]:
    """Every curve that can join a chain, in a deterministic order.

    **Construction geometry is not a member.** A construction line and every
    projected reference a sketch-on-face brings in (slice 12) constrain the
    sketch and must not appear in its profile — so they are dropped here, at
    the one place that decides what the emitted code contains, rather than
    filtered out of each call site.
    """
    skip = set(solution.get("construction") or ())
    slots = solution.get("slots") or {}
    owner: dict[str, str] = {}
    for name, slot in slots.items():
        for sub in list(slot["arcs"]) + list(slot["sides"]):
            owner[sub] = name
    # A compiled sub-entity inherits its owner's flag. A slot marked
    # construction is listed under its **own** name (`slot1`), and its caps and
    # sides are `slot1.arc_a` / `slot1.side_1`, so without this a construction
    # slot dropped its face form and emitted its curve form instead.
    skip |= {sub for sub, name in owner.items() if name in skip}

    members: list[dict] = []
    for line in spec.get("lines") or []:
        if line["name"] in skip:
            continue
        members.append({"kind": "line", "name": line["name"],
                        "ends": (line["p1"], line["p2"]), "slot": None})
    for name in (solution.get("arcs") or {}):
        if name in skip:
            continue
        members.append({"kind": "arc", "name": name,
                        "ends": (f"{name}.start", f"{name}.end"),
                        "slot": owner.get(name)})
    for name, e in (solution.get("ellipses") or {}).items():
        if not e.get("bounded") or name in skip:
            continue          # a full ellipse is a face, not a chain member
        members.append({"kind": "ellipse", "name": name,
                        "ends": (f"{name}.start", f"{name}.end"),
                        "slot": None})
    for name, spline in (solution.get("splines") or {}).items():
        if name in skip:
            continue
        members.append({"kind": "spline", "name": name,
                        "ends": (spline["points"][0], spline["points"][-1]),
                        "slot": None})
    for name, slot in slots.items():
        if name in skip:
            continue
        cap_a, cap_b = slot["arcs"]
        side_1, side_2 = slot["sides"]
        # `Sketch.slot` builds each side directly on the caps' virtual handles
        # (which is what makes the four junctions structural rather than rows).
        members.append({"kind": "line", "name": side_1,
                        "ends": (f"{cap_a}.end", f"{cap_b}.start"),
                        "slot": name})
        members.append({"kind": "line", "name": side_2,
                        "ends": (f"{cap_b}.end", f"{cap_a}.start"),
                        "slot": name})
    return members


def _junctions(solution: dict, members: list[dict], join_tol: float):
    """Group endpoints into junctions; returns (junction_of, junctions).

    Two endpoints are one junction when they carry the same handle name or
    when their solved coordinates are within `join_tol`. Coordinate proximity
    is what catches a junction tied by a `coincident` constraint rather than
    built on a shared handle — the solver leaves those ~1e-11 mm apart.
    """
    eps: list[tuple[int, int]] = []
    xy: list[tuple[float, float]] = []
    handles: list[str] = []
    for mi, m in enumerate(members):
        for k in (0, 1):
            eps.append((mi, k))
            handles.append(m["ends"][k])
            xy.append(_handle_xy(solution, m["ends"][k]))

    parent = list(range(len(eps)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[max(ri, rj)] = min(ri, rj)

    for i in range(len(eps)):
        for j in range(i + 1, len(eps)):
            if handles[i] == handles[j] or math.dist(xy[i], xy[j]) <= join_tol:
                union(i, j)

    junction_of: dict[tuple[int, int], int] = {}
    junctions: dict[int, dict] = {}
    for i, ep in enumerate(eps):
        root = find(i)
        junction_of[ep] = root
        entry = junctions.setdefault(root, {"xy": xy[i], "endpoints": [],
                                            "handles": []})
        entry["endpoints"].append((ep, xy[i]))
        if handles[i] not in entry["handles"]:
            entry["handles"].append(handles[i])
    return junction_of, junctions


def _chains(members: list[dict], junction_of: dict) -> list[dict]:
    """Maximal chains of connected members (ported from `sketcher.js`).

    Arcs and splines are chain members here, which the JS version could not
    express. Members are walked in declaration order, so the output is
    deterministic.
    """
    incident: dict[int, list[int]] = {}
    for mi in range(len(members)):
        for k in (0, 1):
            incident.setdefault(junction_of[(mi, k)], []).append(mi)

    unused = set(range(len(members)))
    chains: list[dict] = []
    for start in range(len(members)):
        if start not in unused:
            continue
        unused.discard(start)
        seq = [(start, False)]
        head, tail = junction_of[(start, 0)], junction_of[(start, 1)]
        while head != tail:
            nxt = next((mi for mi in incident[tail] if mi in unused), None)
            if nxt is None:
                break
            unused.discard(nxt)
            a, b = junction_of[(nxt, 0)], junction_of[(nxt, 1)]
            if a == tail:
                seq.append((nxt, False))
                tail = b
            else:
                seq.append((nxt, True))
                tail = a
        while head != tail:
            prv = next((mi for mi in incident[head] if mi in unused), None)
            if prv is None:
                break
            unused.discard(prv)
            a, b = junction_of[(prv, 0)], junction_of[(prv, 1)]
            if b == head:
                seq.insert(0, (prv, False))
                head = a
            else:
                seq.insert(0, (prv, True))
                head = b
        chains.append({"seq": seq, "closed": head == tail,
                       "junctions": _walk_junctions(seq, junction_of)})
    return chains


def _walk_junctions(seq, junction_of) -> list[int]:
    """The junction sequence a chain visits, first to last (closed: j0 twice)."""
    out = []
    for mi, rev in seq:
        a, b = junction_of[(mi, 1 if rev else 0)], junction_of[(mi, 0 if rev else 1)]
        if not out:
            out.append(a)
        out.append(b)
    return out


def _slot_is_standalone(name: str, members: list[dict], chains: list[dict],
                        spec: dict) -> bool:
    """Whether a slot may emit as a `SlotCenterToCenter` face.

    Two ways to lose that: a constraint the caller wrote names one of the
    slot's sub-entities (so the slot is positioned against the rest of the
    sketch), or one of its primitives shares a junction with a member that is
    not the slot's. Either way the honest emission is the curve form, because
    a face cannot join a `BuildLine` chain.
    """
    prefix = name + "."
    for con in spec.get("constraints") or []:
        for value in con.values():
            if isinstance(value, str) and value.startswith(prefix):
                return False
    mine = {mi for mi, m in enumerate(members) if m["slot"] == name}
    for chain in chains:
        seq = {mi for mi, _ in chain["seq"]}
        if seq & mine and seq - mine:
            return False
    return True


# ------------------------------------------------------------- closure gate
def junction_gaps(solution: dict, spec: dict, *, decimals: int = DECIMALS,
                  arc_anchor: str = "endpoint",
                  join_tol: float = JOIN_TOL_MM) -> dict[str, float]:
    """Every junction's gap, keyed by the handles that meet there.

    Exposed so the measurement behind the gate can be quoted rather than
    trusted — the design's numbers came from exactly this quantity.
    """
    members = _members(solution, spec)
    _, junctions = _junctions(solution, members, join_tol)
    out: dict[str, float] = {}
    for entry in junctions.values():
        out[_label(entry)] = _gap(solution, members, entry, decimals,
                                  arc_anchor)
    return out


def _label(entry: dict) -> str:
    return "/".join(entry["handles"])


def _gap(solution, members, entry, decimals, arc_anchor) -> float:
    """How far the shared literal moves the endpoints it stands for."""
    lit = _round_trip(entry["xy"], decimals)
    gap = max((math.dist(xy, lit) for _, xy in entry["endpoints"]), default=0.0)
    # An elliptical arc has no endpoint-anchored constructor in build123d, so
    # its endpoints are *always* derived by the reader from rounded literals —
    # measured unconditionally, not only under `arc_anchor="center"`.
    ellipses = solution.get("ellipses") or {}
    for (mi, k), _xy in entry["endpoints"]:
        member = members[mi]
        if member["kind"] != "ellipse" or member["name"] not in ellipses:
            continue
        derived = _ellipse_point(ellipses[member["name"]],
                                 "start" if k == 0 else "end", decimals)
        gap = max(gap, math.dist(derived, lit))
    if arc_anchor != "center":
        return gap
    # A centre-parametrized arc's endpoint is *derived by the reader* from the
    # rounded centre, radius and angles — which is the emission the measured
    # 7.58e-7 mm failure came from.
    arcs = solution.get("arcs") or {}
    for (mi, k), _xy in entry["endpoints"]:
        member = members[mi]
        if member["kind"] != "arc" or member["name"] not in arcs:
            continue
        arc = arcs[member["name"]]
        cx, cy = float(fmt(arc["cx"], decimals)), float(fmt(arc["cy"], decimals))
        r = float(fmt(arc["r"], decimals))
        start = float(fmt(arc["start_deg"], decimals))
        sweep = float(fmt(_sweep_deg(arc), decimals))
        t = math.radians(start if k == 0 else start + sweep)
        derived = (cx + r * math.cos(t), cy + r * math.sin(t))
        gap = max(gap, math.dist(derived, lit))
    return gap


# ------------------------------------------------------------------- emission
def _arc_call(arc: dict, va: str, vb: str, rev: bool, decimals: int,
              arc_anchor: str, warnings: list) -> str:
    name_sweep = _sweep_deg(arc)
    sweep = -name_sweep if rev else name_sweep
    if abs(abs(sweep) - 360.0) <= FULL_TURN_TOL_DEG or abs(sweep) > 360.0:
        warnings.append({
            "code": "arc_full_turn",
            "message": ("an arc sweeping a full turn has coincident endpoints "
                        "and cannot be written as a RadiusArc; emitted as "
                        "CenterArc, which the reader derives from the rounded "
                        "centre, radius and angles"),
        })
        arc_anchor = "center"
    if arc_anchor == "center":
        start = float(arc["start_deg"]) if not rev else float(arc["end_deg"])
        return (f"CenterArc(({fmt(arc['cx'], decimals)}, "
                f"{fmt(arc['cy'], decimals)}), {fmt(arc['r'], decimals)}, "
                f"{fmt(start, decimals)}, {fmt(sweep, decimals)})")
    if arc["authored"] == "three_point":
        t = math.radians((float(arc["start_deg"]) + float(arc["end_deg"])) / 2.0)
        mid = (float(arc["cx"]) + float(arc["r"]) * math.cos(t),
               float(arc["cy"]) + float(arc["r"]) * math.sin(t))
        return f"ThreePointArc({va}, {_pt(mid, decimals)}, {vb})"
    # Measured against build123d 0.11.1: the short sagitta is the minor arc,
    # and the sign of `radius` picks the side the centre falls on.
    short = abs(sweep) <= 180.0
    signed = -arc["r"] if (sweep > 0) == short else arc["r"]
    return (f"RadiusArc({va}, {vb}, {fmt(signed, decimals)}, "
            f"short_sagitta={short})")


def _ellipse_arc_call(e: dict, decimals: int) -> str:
    """`EllipticalCenterArc`, the only bounded-ellipse constructor there is.

    Three measured facts about the pinned build123d 0.11.1 are baked in here:

    1. `start_angle`/`arc_size` are the **eccentric anomaly**, the same
       parameter the solver uses (agreement 8.9e-16 mm), so no conversion.
    2. `arc_size` — **not** `end_angle`. Passing `end_angle` raises
       `UnboundLocalError` in 0.11.1: its deprecation branch reads a
       `direction` that is only bound when `angular_direction` is also passed.
       The working spelling is the signed sweep, which is what the solver
       reports anyway.
    3. There is **no endpoint-anchored elliptical constructor** (`RadiusArc`'s
       counterpart does not exist; `EllipticalStartArc` anchors one end and
       derives the other). So unlike an arc, an elliptical arc's endpoints are
       always *derived by the reader* from the rounded centre, axes, rotation
       and angles — which is why `_gap` measures that derivation for every
       elliptical arc regardless of `arc_anchor`.

    The call is written in the arc's own direction whichever way the chain
    walks it: build123d joins a `BuildLine` on proximity, not on order.
    """
    sweep = float(e["end_deg"]) - float(e["start_deg"])
    return (f"EllipticalCenterArc(({fmt(e['cx'], decimals)}, "
            f"{fmt(e['cy'], decimals)}), {fmt(e['a'], decimals)}, "
            f"{fmt(e['b'], decimals)}, start_angle={fmt(e['start_deg'], decimals)}, "
            f"arc_size={fmt(sweep, decimals)}, "
            f"rotation={fmt(e['rotation'], decimals)})")


def _ellipse_point(e: dict, which: str, decimals: int) -> tuple[float, float]:
    """The endpoint **as the reader will derive it** from the emitted literals."""
    cx, cy = float(fmt(e["cx"], decimals)), float(fmt(e["cy"], decimals))
    a, b = float(fmt(e["a"], decimals)), float(fmt(e["b"], decimals))
    phi = math.radians(float(fmt(e["rotation"], decimals)))
    start = float(fmt(e["start_deg"], decimals))
    sweep = float(fmt(float(e["end_deg"]) - float(e["start_deg"]), decimals))
    t = math.radians(start if which == "start" else start + sweep)
    lx, ly = a * math.cos(t), b * math.sin(t)
    return (cx + lx * math.cos(phi) - ly * math.sin(phi),
            cy + lx * math.sin(phi) + ly * math.cos(phi))


def _spline_call(spline: dict, va: str, vb: str, rev: bool, decimals: int,
                 name: str, warnings: list) -> str:
    coords = [(float(c["x"]), float(c["y"])) for c in spline["coords"]]
    inner = [_pt(c, decimals) for c in coords[1:-1]]
    pts = [va] + inner + [vb] if not rev else [va] + inner[::-1] + [vb]
    pinned = spline.get("end_tangent") or {}
    if not (pinned.get("start") or pinned.get("end")):
        return f"Spline({', '.join(pts)})"
    # build123d takes the two end tangents as a pair, so a spline with one
    # pinned end has its free end pinned to its own control-polygon leg. Say
    # so: measured, a free end drifts up to 44.6 deg from that leg, which is
    # exactly why the pinned end needs `tangents=` at all.
    if not (pinned.get("start") and pinned.get("end")):
        warnings.append({
            "code": "spline_free_end_pinned",
            "message": (f"spline {name!r} has one constrained end tangent; "
                        "build123d takes the pair, so the free end is emitted "
                        "along its own control-polygon leg"),
            "spline": name,
        })
    solved = spline.get("tangents") or {}
    t_start = solved.get("start") or _leg(coords[0], coords[1])
    t_end = solved.get("end") or _leg(coords[-2], coords[-1])
    ts = (float(t_start["x"]), float(t_start["y"])) if isinstance(t_start, dict) \
        else t_start
    te = (float(t_end["x"]), float(t_end["y"])) if isinstance(t_end, dict) \
        else t_end
    if rev:
        ts, te = (-te[0], -te[1]), (-ts[0], -ts[1])
    return (f"Spline({', '.join(pts)}, "
            f"tangents=({_pt(ts, decimals)}, {_pt(te, decimals)}))")


def _leg(a, b) -> tuple[float, float]:
    dx, dy = b[0] - a[0], b[1] - a[1]
    n = math.hypot(dx, dy) or 1.0
    return dx / n, dy / n


def _emit_chain(chain, members, junctions, vname, solution, decimals,
                arc_anchor, warnings) -> list[str]:
    """Vertex bindings first, then one call per member, in walk order."""
    out: list[str] = []
    jids = chain["junctions"]
    unique = list(dict.fromkeys(jids))
    for jid in unique:
        out.append(f"{vname[jid]} = {_pt(junctions[jid]['xy'], decimals)}")

    seq = chain["seq"]
    kinds = [members[mi]["kind"] for mi, _ in seq]
    i = 0
    while i < len(seq):
        if kinds[i] == "line":
            j = i
            while j < len(seq) and kinds[j] == "line":
                j += 1
            run = [vname[jid] for jid in jids[i:j + 1]]
            if j - i == 1:
                out.append(f"Line({run[0]}, {run[1]})")
            else:
                out.append(f"Polyline({', '.join(run)})")
            i = j
            continue
        mi, rev = seq[i]
        member = members[mi]
        va, vb = vname[jids[i]], vname[jids[i + 1]]
        if member["kind"] == "arc":
            out.append(_arc_call(solution["arcs"][member["name"]], va, vb, rev,
                                 decimals, arc_anchor, warnings))
        elif member["kind"] == "ellipse":
            out.append(_ellipse_arc_call(solution["ellipses"][member["name"]],
                                         decimals))
        else:
            out.append(_spline_call(solution["splines"][member["name"]], va, vb,
                                    rev, decimals, member["name"], warnings))
        i += 1
    return out


def _plane_expr(plane: dict, decimals: int) -> str:
    """`Plane(...)` for a sketch on a face — the basis, written out.

    `Plane.XY` is a name; a face's plane is three vectors, and they have to be
    in the script or the coordinates the emitter just wrote mean nothing. The
    basis is the one `kernel/handlers/sketchplane.py` measured with, and the
    caveat above the block says what can move it.

    Every component goes through `fmt`, so nothing but a number can reach the
    output — but a non-numeric one used to escape as a bare `ValueError` (an
    HTTP 500) instead of the `validation_error` contract every other bad input
    gets.
    """
    def vec(key, default):
        v = plane.get(key) or default
        if isinstance(v, (str, bytes)) or len(v) != 3:
            raise EmitError(
                f"plane[{key!r}] must be three numbers, got {v!r}")
        try:
            comps = [fmt(float(c), decimals) for c in v]
        except (TypeError, ValueError) as exc:
            raise EmitError(
                f"plane[{key!r}] must be three numbers, got {v!r}") from exc
        return "(" + ", ".join(comps) + ")"

    return (f"Plane(origin={vec('origin', (0, 0, 0))}, "
            f"x_dir={vec('x_dir', (1, 0, 0))}, "
            f"z_dir={vec('normal', (0, 0, 1))})")


def _plane_header(plane: dict) -> list[str]:
    """The provenance comment, with the caveat inline (design Decision 12).

    Face indices are mesh-order ordinals, and a parameter change that alters
    the part's topology renumbers them — measured in the slice-12 spike, where
    `corner_r: 6.0` turned the enclosure's face 37 from its 5989 mm^2 base
    plate into a 51 mm^2 sliver. Surfaced, not hidden and not silently
    repaired.
    """
    index = plane.get("face_index")
    if index is None:
        return []
    # **The emitter writes only what it formatted.** Both of these come
    # straight from a caller (any agent, MCP or HTTP client) and were
    # interpolated raw: a `part` of `build(p) ---\nimport os\n# ---` put
    # `import os` on line 2 of the generated script, inside a comment that
    # closed itself. A face ordinal is an int and a part reference is a small
    # expression naming the part, so both are *validated* rather than escaped —
    # an escaped newline in a comment would be honest but unreadable, and
    # anything that is not one of these two shapes is a caller bug.
    if isinstance(index, bool) or not isinstance(index, int):
        raise EmitError(
            f"plane['face_index'] must be an integer face ordinal, got "
            f"{index!r}")
    part = plane.get("part") or "build(p)"
    if not isinstance(part, str) or not _PART_REF_RE.match(part):
        raise EmitError(
            f"plane['part'] must name the part the face belongs to (an "
            f"expression like 'build(p)'), got {part!r}")
    return [
        f"# --- agentcad sketch on face {index} of {part} ---",
        "# NOTE: face indices are mesh-order ordinals; a parameter change that",
        "# alters the part's topology can renumber them. Re-pick the face if",
        "# the rebuild moves. The plane's basis below is the one the sketch",
        "# was solved in.",
    ]


def emit(solution: dict, spec: dict, *, style: str = "function",
         decimals: int = DECIMALS, arc_anchor: str = "endpoint",
         closure_tol: float = CLOSURE_TOL_MM,
         join_tol: float = JOIN_TOL_MM, persist=None) -> dict:
    """Emit build123d source for a solved sketch.

    `decimals` and `arc_anchor` are the two knobs the design measured; their
    defaults are the measured-safe values and the unsafe combination is kept
    reachable **only** so the regression test can prove it fails rather than
    assert it from memory.

    `persist` is the round-trip block's name (`True` means `"profile"`): with
    it the code is wrapped in the marker/spec/hash block `parse_blocks` reads
    back. It is **opt-in** — without it the bytes are exactly what every
    caller got before FR10 landed.
    """
    block = None if persist is None or persist is False else block_name(persist)
    if style not in STYLES:
        raise EmitError(f"unknown emit style {style!r}; known: {list(STYLES)}")
    if arc_anchor not in ARC_ANCHORS:
        raise EmitError(
            f"unknown arc anchor {arc_anchor!r}; known: {list(ARC_ANCHORS)}")

    warnings: list[dict] = []
    members = _members(solution, spec)
    junction_of, junctions = _junctions(solution, members, join_tol)
    chains = _chains(members, junction_of)

    # `construction` first, `_slot_is_standalone` second: a construction slot
    # emits nothing *at all*, and asking only whether it could be a face
    # emitted it as one (docs/agent-api.md: "construction: true on ANY entity
    # ... is never emitted"). `_members` drops the construction slot's curve
    # form; this is the other exit.
    construction = set(solution.get("construction") or ())
    face_slots = [name for name in (solution.get("slots") or {})
                  if name not in construction
                  and _slot_is_standalone(name, members, chains, spec)]
    # A slot emitted as a face is no longer a curve: drop the chain its
    # primitives formed (which, by `_slot_is_standalone`, holds nothing else).
    chains = [c for c in chains
              if not all(members[mi]["slot"] in face_slots for mi, _ in c["seq"])]

    # Vertex names are assigned in emission order, so the code reads top down.
    vname: dict[int, str] = {}
    for chain in chains:
        for jid in chain["junctions"]:
            if jid not in vname:
                vname[jid] = f"v{len(vname)}"

    body: list[str] = []
    if chains:
        body.append("with BuildLine():")
        for chain in chains:
            body += ["    " + line for line in
                     _emit_chain(chain, members, junctions, vname, solution,
                                 decimals, arc_anchor, warnings)]
        if any(chain["closed"] for chain in chains):
            _gate(solution, members, junctions, chains, decimals, arc_anchor,
                  closure_tol, warnings)
            body.append("make_face()")
        else:
            _gate(solution, members, junctions, chains, decimals, arc_anchor,
                  closure_tol, warnings, refuse=False)

    for name, circle in (solution.get("circles") or {}).items():
        if name in construction:
            continue
        body.append(f"with Locations(({fmt(circle['cx'], decimals)}, "
                    f"{fmt(circle['cy'], decimals)})):")
        body.append(f"    Circle(radius={fmt(circle['r'], decimals)})")
    for name, e in (solution.get("ellipses") or {}).items():
        if e.get("bounded") or name in construction:
            continue          # already emitted as a chain member
        body.append(f"with Locations(({fmt(e['cx'], decimals)}, "
                    f"{fmt(e['cy'], decimals)})):")
        body.append(f"    Ellipse(x_radius={fmt(e['a'], decimals)}, "
                    f"y_radius={fmt(e['b'], decimals)}, "
                    f"rotation={fmt(e['rotation'], decimals)})")
    for name in face_slots:
        slot = solution["slots"][name]
        c1, c2 = slot["center1"], slot["center2"]
        sep = math.dist((c1["x"], c1["y"]), (c2["x"], c2["y"]))
        mid = ((c1["x"] + c2["x"]) / 2.0, (c1["y"] + c2["y"]) / 2.0)
        rot = math.degrees(math.atan2(c2["y"] - c1["y"], c2["x"] - c1["x"]))
        body.append(f"with Locations({_pt(mid, decimals)}):")
        body.append(f"    SlotCenterToCenter({fmt(sep, decimals)}, "
                    f"{fmt(2.0 * slot['r'], decimals)}, "
                    f"rotation={fmt(rot, decimals)})")

    plane = spec.get("plane") or None
    code = _render(body, style, plane, decimals,
                   func=f"sketch_{block or DEFAULT_NAME}",
                   banner=block is None)
    if block is not None:
        code = wrap_block(block, spec, code)
    return {"code": code, "warnings": warnings, "style": style,
            "plane": plane, "persist": block}


def _gate(solution, members, junctions, chains, decimals, arc_anchor,
          closure_tol, warnings, refuse: bool = True) -> None:
    """Refuse to emit `make_face()` over a junction that will not close."""
    used = {jid for chain in chains for jid in chain["junctions"]}
    closed = {jid for chain in chains if chain["closed"]
              for jid in chain["junctions"]}
    worst: list[tuple[float, str, bool]] = []
    for jid in sorted(used):
        gap = _gap(solution, members, junctions[jid], decimals, arc_anchor)
        if gap > closure_tol:
            worst.append((gap, _label(junctions[jid]), jid in closed))
    if not worst:
        return
    worst.sort(reverse=True)
    fatal = next((w for w in worst if w[2]), None)
    if refuse and fatal is not None:
        gap, label, _ = fatal
        raise EmitError(
            f"refusing to emit make_face(): junction {label} is {gap:.3e} mm "
            f"wide at {decimals} decimals, over the {closure_tol:.0e} mm "
            "closure tolerance — the emitted wire would not close (OCCT: "
            '"Face can only be created with closed wires"). Solve the sketch '
            "to a tighter residual, or raise the emission precision.")
    for gap, label, _ in worst:
        warnings.append({
            "code": "junction_gap",
            "message": (f"junction {label} is {gap:.3e} mm wide at {decimals} "
                        "decimals; the emitted chain is open, so it still "
                        "builds, but the geometry moved by that much"),
            "junction": label, "gap_mm": gap,
        })


def _render(body: list[str], style: str, plane: dict | None = None,
            decimals: int = DECIMALS, func: str = "sketch_profile",
            banner: bool = True) -> str:
    if not body:
        body = ["pass"]
    where = "Plane.XY" if not plane else _plane_expr(plane, decimals)
    header = _plane_header(plane) if plane else []
    if style == "buildline":
        return "\n".join(header + [f"with BuildSketch({where}) as _sk:"]
                         + ["    " + line for line in body]) + "\n"
    # `banner` is the generic auto-generated header. A persisted block writes
    # its own marker line, so the two would read as a duplicate; the *plane*
    # header is kept either way, because it carries the face caveat.
    lead = header or (["# --- agentcad sketch (auto-generated) ---"]
                      if banner else [])
    return "\n".join([
        "", "",
        *lead,
        f"def {func}():",
        f"    with BuildSketch({where}) as _sk:",
        *["        " + line for line in body],
        "    return _sk.sketch",
        "",
    ])


# ------------------------------------------------------ round-trip (FR10)
def _norm(text: str) -> str:
    """The code as a hash sees it.

    Line endings and trailing whitespace are normalised away deliberately: an
    editor that rewrites either on save has not touched the geometry, and
    reporting that as a hand edit would train the user to ignore the banner —
    which is the one thing the banner cannot survive.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(line.rstrip() for line in text.split("\n")).strip("\n")


def block_hash(code: str) -> str:
    """`sha256:<hex>` over the emitted code block, normalised by `_norm`."""
    digest = hashlib.sha256(_norm(code).encode("utf-8")).hexdigest()
    return f"{HASH_ALGO}:{digest}"


def persist_spec(spec: dict) -> dict:
    """The spec **as submitted**, in the shape a caller can post straight back.

    As submitted, not as solved: the block records the input that produced the
    code beneath it. The GUI submits the on-screen (already solved)
    coordinates, so reopening a GUI-authored block re-solves from its own
    solution and lands on the same branch — the property FR10 needs — without
    the emitter inventing a second, rounded copy of the geometry that the code
    already holds.

    `initial`, `drag` and `diagnostics` are properties of one call and are not
    persisted. `plane` is (slice 12): sketch-on-face coordinates without their
    basis are arbitrary.
    """
    entities = {kind: list(spec.get(kind) or []) for kind in PERSIST_KINDS
                if spec.get(kind)}
    out = {"v": SPEC_VERSION, "entities": entities,
           "constraints": list(spec.get("constraints") or [])}
    if spec.get("plane"):
        out["plane"] = spec["plane"]
    return out


def block_name(persist) -> str:
    """Validate a block name — it becomes `def sketch_<name>()`."""
    name = DEFAULT_NAME if persist is True else str(persist or "")
    if not _NAME_RE.match(name):
        raise EmitError(
            f"sketch block name {name!r} is not a Python identifier; it "
            "becomes the emitted function's name (`def sketch_<name>()`) and "
            "the marker the block is found by")
    return name


def wrap_block(name: str, spec: dict, code: str) -> str:
    """Marker, spec, hash, code, end marker — the block a script carries."""
    body = code.strip("\n")
    return "\n".join([
        "", "",
        BLOCK_MARKER.format(name=name),
        SPEC_PREFIX + json.dumps(persist_spec(spec)),
        HASH_PREFIX + block_hash(body),
        body,
        BLOCK_END.format(name=name),
        "",
    ])


def _comment_lines(text: str) -> set[int] | None:
    """The 0-based lines of `text` that hold a real `#` comment, or None.

    A block marker is a **comment**, and `parse_blocks` is a line scanner, so
    a docstring that quotes the marker (this module's own, for one) produced a
    phantom `diverged` block and shifted `next_name` — the next insert named
    around a sketch that does not exist. `tokenize` answers the question
    exactly; a script it cannot tokenize returns None and the scan falls back
    to matching every line, which is the behaviour that shipped.
    """
    try:
        return {tok.start[0] - 1
                for tok in tokenize.generate_tokens(io.StringIO(text).readline)
                if tok.type == tokenize.COMMENT}
    except (SyntaxError, tokenize.TokenError, ValueError):
        return None


def parse_blocks(script: str) -> list[dict]:
    """Read every sketch block out of a part script.

    Returns one entry per block, in script order:

    `{name, status, spec, code, hash, computed_hash, start_line, end_line,
      message}` with `status` in:

    - **`ok`** — the spec parsed and the hash matches the code beneath it.
    - **`diverged`** — the spec parsed and the hash does **not** match: the
      code was hand-edited. The code is the source of truth for geometry, so
      this is reported and never repaired.
    - **`unverified`** — the spec is missing, unreadable, of an unknown
      version, unhashed, or the block has no end marker. "We cannot tell" is
      never rendered as "there is no sketch"; the code is left alone.

    Line numbers are 1-based over the script as given.
    """
    text = (script or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    comments = _comment_lines(text)

    def marker_at(k: int):
        """The block marker on line `k`, if that line really is a comment."""
        if comments is not None and k not in comments:
            return None
        return _MARKER_RE.match(lines[k].strip())

    def end_at(k: int):
        if comments is not None and k not in comments:
            return None
        return _END_RE.match(lines[k].strip())

    out: list[dict] = []
    i = 0
    while i < len(lines):
        marker = marker_at(i)
        if not marker:
            i += 1
            continue
        name = marker.group(1)
        start = i
        j = i + 1
        spec_line = hash_line = None
        while j < len(lines):
            stripped = lines[j].strip()
            if not stripped:
                # A blank line between the marker and its spec is whitespace,
                # and `_norm` deliberately forgives whitespace: being stricter
                # here than the hash this header protects downgraded an intact
                # block to `unverified` over one blank line.
                j += 1
                continue
            if spec_line is None and stripped.startswith(SPEC_PREFIX.strip()):
                spec_line = stripped[len(SPEC_PREFIX.strip()):].strip()
            elif hash_line is None and stripped.startswith(HASH_PREFIX.strip()):
                hash_line = stripped[len(HASH_PREFIX.strip()):].strip()
            else:
                break
            j += 1
        body_start = j
        end = None
        while j < len(lines):
            done = end_at(j)
            if done and done.group(1) == name:
                end = j
                break
            if marker_at(j):
                break              # the next block starts: this one is open
            j += 1
        body_end = end if end is not None else j
        code = "\n".join(lines[body_start:body_end]).strip("\n")
        out.append(_block_status(name, spec_line, hash_line, code, start,
                                 end if end is not None else body_end - 1,
                                 closed=end is not None))
        i = (end + 1) if end is not None else body_end
    return out


def _block_status(name, spec_line, hash_line, code, start, end,
                  closed: bool) -> dict:
    entry = {"name": name, "status": "unverified", "spec": None, "code": code,
             "hash": hash_line, "computed_hash": block_hash(code),
             "start_line": start + 1, "end_line": max(end, start) + 1,
             "message": ""}
    spec, problem = _read_spec(spec_line)
    entry["spec"] = spec
    if not closed:
        # The hash covers the code, not the marker, so a deleted end marker
        # would otherwise read as `ok` over a body that runs to the end of the
        # file — a block whose extent nobody can agree on.
        entry["message"] = (
            f'sketch {name!r} has no `# --- end agentcad sketch "{name}" ---` '
            "marker, so the code it covers cannot be delimited; the code is "
            "left alone")
        return entry
    if problem:
        entry["message"] = problem
        return entry
    if hash_line is None:
        entry["message"] = (
            f"sketch {name!r} records no hash, so a hand edit to its code "
            "cannot be detected. Re-emit the sketch to restore the check.")
        return entry
    if hash_line != entry["computed_hash"]:
        entry["status"] = "diverged"
        entry["message"] = (
            f"the code of sketch {name!r} was edited by hand since the spec "
            "was written (hash mismatch). The code is the source of truth for "
            "geometry: re-solve from the spec to discard the edit, or discard "
            "the spec to keep it. Nothing is overwritten either way.")
        return entry
    entry["status"] = "ok"
    return entry


def _read_spec(spec_line: str | None) -> tuple[dict | None, str]:
    if spec_line is None:
        return None, ("the block carries no `# agentcad-sketch-spec:` line, "
                      "so its constraints cannot be recovered; the code is "
                      "left alone")
    try:
        spec = json.loads(spec_line)
    except ValueError as exc:
        return None, (f"the spec block is unreadable ({exc}); the code is "
                      "left alone")
    if not isinstance(spec, dict) or "entities" not in spec:
        return None, ("the spec block is unreadable (it is not a sketch "
                      "spec object); the code is left alone")
    version = spec.get("v")
    if version != SPEC_VERSION:
        return None, (f"the spec block is version {version!r}, and this "
                      f"AgentCAD writes version {SPEC_VERSION} — it was "
                      "probably written by a newer build; the code is left "
                      "alone")
    return spec, ""


def next_name(script: str, base: str = DEFAULT_NAME) -> str:
    """The next block name that shadows nothing in this script.

    Two blocks named the same would define the same `sketch_<name>()` twice
    and the second would silently win — the `push_pull` counter's reason,
    applied to a function name. Pre-slice-13 inserts carry no block, so plain
    `def sketch_*(` definitions are counted too.
    """
    taken = {b["name"] for b in parse_blocks(script)}
    taken |= set(_DEF_RE.findall((script or "").replace("\r\n", "\n")))
    if base not in taken:
        return base
    n = 2
    while f"{base}{n}" in taken:
        n += 1
    return f"{base}{n}"
