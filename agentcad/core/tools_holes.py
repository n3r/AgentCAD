"""Tool pack: the ISO/ANSI hole standards, hole-on-face, and the rebuild seam.

`hole_standards {family?, size?, std?}` answers out of the vendored tables in
`agentcad/toolkit/hole_standards.py` — no kernel call, no geometry, no OCP —
so a UI size picker and a part script use the same numbers the drilled hole
used. It is registered **unconditionally**: a pure-data tool can always run.

`add_holes {project, part_id, points, family, size, plane|face_index, …}` is
the script-editing half (FR14): it appends a marked, counter-suffixed block
that rebinds `build` and calls the matching `toolkit.holes` helper — the
`tools_facemod.push_pull` pattern exactly, so the script stays the source of
truth and the edit is visible, editable and composable.

`install_rebuild_holes` is the other half: it wraps `service._rebuild` and
`service.get_part` so a part's hole records reach every client, and persists
them in a `.cache/<key>.holes.json` sidecar.

Why a sidecar and not "just read them off the shape"
----------------------------------------------------
Measured (changelog 0150): a rebuild whose `.acm` and `.metrics.json` are
already present makes **zero** kernel calls, and so does `get_part` on a built
part. There is no shape to read an attribute off, at any price. The sidecar is
therefore mandatory, not an optimisation, and it follows `.specs.json` exactly:
same directory, same content-addressed key, atomic write, and a versioned
reader that `unlink()`s anything it cannot use.

Three answers, kept apart
-------------------------
| `holes` | means |
|---|---|
| key **absent** | not harvested: the build failed, or the harvest did |
| `null` | the part declares no holes |
| `[]` | records were created and did not reach the returned part |
| `[...]` | the records |

`null` comes from the worker's `created()` **delta** being zero, never from an
empty list: `holes.records()` returns `[]` both for "no holes" and for "a raw
build123d operation dropped them", and reporting the second as "declares none"
is the exact silence this design exists to prevent.

**Those four states are an invariant on read, not just on write.** `[]` is
inseparable from `dropped > 0` and a warning saying so; `null` is inseparable
from `dropped: 0`. A stored document in any other combination is a state the
writer cannot produce — a hand edit, a partial write, a copied `.cache/` — and
`_read_sidecar` discards it rather than serving it. It also compares the
**cache key the file itself stores** (never compared before, so a sidecar
describing a different build of the part was served as if it described this
one) and runs every record through `hole_standards.validate_record`, the one
contract the worker raises on and the drawing skips on.

Load order and routing
----------------------
This pack loads at `h`, which is **before** `tools_proposals` (`p`),
`tools_specs` (`s`) and `tools_versioning` (`v`). It therefore never reads
`service.branches` / `service.specs` / `service.gate_providers` in
`register()`; the seam reads everything it needs inside its methods, and it
wraps `_rebuild`/`get_part` — methods later packs only wrap, never replace —
so a second `build_registry()` cannot disarm it.

The harvest passes **`affinity=part_id`**, and that is a measured requirement,
not tidiness: `KernelPool._pick` round-robins an *unkeyed* request, so a
harvest that omits the affinity can land on a worker whose `_SHAPE_CACHE` has
never seen the script and silently pay a full cold build — measured at
**11 354 ms** on `engine/intake_manifold` against **1 ms** keyed.
"""

from __future__ import annotations

import functools
import json
from pathlib import Path

from ..kernel.client import KernelError
from ..kernel.protocol import ERROR_CONTRACT
from .model import ValidationError
from .project import ProjectStore
from .script_blocks import apply_generated_block, next_build_alias
from .tools import Tool, schema, with_hint

#: Sidecar format. Bump it and every stored file is discarded on read. Bumped
#: to 2 when records gained `verify`/`dropped`/`designation_base` and their
#: `count` became the number of instances that demonstrably removed material,
#: and to 3 when they gained `provenance` — in both cases a stored document is
#: not *wrong*, it is a weaker claim, and there is no way to tell from the file
#: which it is. A stale sidecar is one keyed kernel round trip to replace.
HOLES_SIDECAR_VERSION = 3

#: The wrapper marker, on the wrapper itself — the `install_rebuild_specs`
#: precedent: "is this already wrapped?" is answered by the method, not by a
#: flag on the service that a second service could disagree with.
_WRAPPED = "_agentcad_holes_wrapper"

DESCRIPTION = (
    "Look up ISO or ASME hole standards: clearance diameters (ISO 273 fine/"
    "medium/coarse, ASME B18.2.8 close/normal/loose — either spelling is "
    "accepted), thread pitch and tap drill (ISO 261/262, or the Unified inch "
    "UNC/UNF series with its number/letter/fraction drill designations), and "
    "counterbore/countersink geometry from the fastener head standards (ISO "
    "4762, ISO 10642, ASME B18.3). Omit everything to list the families and "
    "their tabulated sizes; give family+size for the row. Clearance returns "
    "all three fits at once — millimetres under `fits`, the table's own unit "
    "under `fits_native`. Every answer carries the standard, the revision, and "
    "the sources that back THAT row (the file's list is their union), with "
    "`corroborated` (false when only one source backs it) and `conflicts` (a "
    "source disagreement that was resolved rather than dropped). Lengths are "
    "millimetres, with the table's own unit alongside in `*_native` — an ASME "
    "row is inches and its designation prints inches. Counterbore answers "
    "name the head dimensions (the standard part) separately from the bore (a "
    "documented shop clearance rule, since the published counterbore charts "
    "disagree). Data only — it drills nothing."
)


def _sidecar_path(service, proj: str, key: str) -> Path:
    return service.store.cache_dir(proj) / f"{key}.holes.json"


def _sidecar_problem(stored, key: str) -> str | None:
    """Why a stored harvest may not be used, or None.

    Four checks, and the first three used to be absent:

    1. **the version**, as before;
    2. **the embedded cache key**, which the writer has always stored and no
       reader ever compared. A `.holes.json` whose `cache_key` is not the key
       being asked for describes *different bytes* — a restored history, a
       hand-copied `.cache/`, a half-finished rename — and answering from it is
       serving one part's holes for another's. A mismatch is a recompute, not
       an acceptance;
    3. **every record**, against `hole_standards.validate_record` — the same
       contract the worker raises on and the drawing skips on, so residue
       cannot enter through the file instead of through the shape;
    4. **the four-state invariant** the module docstring's table declares.
       `holes: []` means "records were created and did not arrive" and is
       therefore inseparable from `dropped > 0` and a warning saying so;
       `holes: null` means "declares none" and is inseparable from `dropped:
       0`. A hand-written `{"holes": [], "dropped": 0, "warnings": []}` is a
       fifth state the writer cannot produce, and it used to be accepted and
       reported as the second — a part with lost records, silently, with no
       warning anywhere.

    `key` is required, not optional: the only caller resolves it *before* it
    can name the file, because the key is the filename. An optional key would
    be a way to read a sidecar without checking the one thing check 2 exists
    for.
    """
    from agentcad.toolkit import hole_standards

    if not isinstance(stored, dict):
        return "not a JSON object"
    if stored.get("version") != HOLES_SIDECAR_VERSION:
        return (f"version {stored.get('version')!r} is not "
                f"{HOLES_SIDECAR_VERSION}")
    stored_key = stored.get("cache_key")
    if stored_key != key:
        return (f"cache_key {stored_key!r} is not the key being read ({key!r}) "
                f"— it describes a different build of this part")
    found = stored.get("holes")
    if found is not None:
        if not isinstance(found, list):
            return f"`holes` is a {type(found).__name__}, not a list or null"
        for index, record in enumerate(found):
            problem = hole_standards.validate_record(
                record, where=f"stored hole record {index}")
            if problem is not None:
                return problem
    dropped = stored.get("dropped")
    if isinstance(dropped, bool) or not isinstance(dropped, int) or dropped < 0:
        return f"`dropped` must be an integer >= 0, got {dropped!r}"
    warnings = stored.get("warnings")
    if (not isinstance(warnings, list)
            or not all(isinstance(text, str) for text in warnings)):
        return f"`warnings` must be a list of strings, got {warnings!r}"
    if found is None and dropped:
        return (f"`holes` is null (the part declares none) but `dropped` is "
                f"{dropped}; those are two different answers")
    if found == [] and not dropped:
        return ("`holes` is an empty list with `dropped` 0 — the writer emits "
                "null for 'declares none' and a list only when records were "
                "made, so this state cannot have been harvested")
    if dropped and not warnings:
        return (f"`dropped` is {dropped} but no warning says so; the harvest "
                f"emits them together")
    return None


def _read_sidecar(path: Path, key: str) -> dict | None:
    """A stored harvest, or None when there is nothing usable there.

    A corrupt, hand-edited, stale-format or **inconsistent** sidecar is
    discarded and recomputed, never raised (`core/specs.py`, and `_rebuild`'s
    own `metrics.json` handling): a crash mid-write must not make a part
    unreadable, and a file this reader cannot vouch for must not become a
    manufacturing callout. Recomputing costs one keyed kernel round trip;
    accepting costs a wrong drawing.

    This is **not** an authentication check and saying so matters: a part
    script already runs arbitrary code, and anything that can write into
    `.cache/` can write a *consistent* document too. What is closed is the
    stale or self-contradicting file, which is what a restore, a copy or a
    half-finished write actually produces.
    """
    if not path.is_file():
        return None
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        stored = None
    if _sidecar_problem(stored, key) is not None:
        try:
            path.unlink()
        except OSError:
            pass
        return None
    return stored


def _write_sidecar(path: Path, payload: dict) -> None:
    try:
        ProjectStore._atomic_write(path, json.dumps(payload).encode())
    except OSError:
        pass      # a cache that cannot be written is a slow read, not a bug


def _payload(key: str, result: dict) -> dict | None:
    """The worker's answer as the stored/reported document, or None when the
    worker could not answer the question.

    The one decision here is `null` vs `[]`, and it is made on the **delta**
    the worker measured, never on the emptiness of the list.

    `None` is the third case and it is the honest one: an *unmeasured* empty
    answer (the harvest took a shape-cache hit, so `build(p)` never ran during
    it) cannot tell "declares none" from "a raw operation dropped them". That
    is reported by absence — "not harvested" — and deliberately **not
    persisted**, so a later harvest that does run the build can answer for
    real. The seam harvests before the build precisely so this is the rare
    path.
    """
    found = result.get("holes") or []
    dropped = int(result.get("dropped") or 0)
    if not found and not dropped and not result.get("measured", True):
        return None
    return {"version": HOLES_SIDECAR_VERSION, "cache_key": key,
            "holes": found if (found or dropped) else None,
            "warnings": list(result.get("warnings") or []),
            "dropped": dropped}


def _may_declare_holes(script) -> bool:
    """Whether this script could possibly carry hole records.

    Records come from `agentcad.toolkit` helpers and from nowhere else, so a
    script whose text never mentions `agentcad` has none — **definitively**,
    which is more than the harvest can say when it takes a shape-cache hit, and
    it costs no kernel call to say it. That matters because most parts are this
    case: without it, the ordinary hole-less part would answer "not harvested"
    whenever any other surface built it first.

    A presence question, on the text, never by executing the script (the
    `declares_specs` / `packet.params_spec` rule). It fails **closed**: an
    unreadable script is harvested rather than declared empty, and the check is
    a deliberate over-approximation — a script that imports `safe_fillet` and
    drills nothing is still harvested, at the price of one cache-hit round trip
    (measured at 1 ms).
    """
    if not isinstance(script, str):
        return True
    return "agentcad" in script


def _holes_for(service, proj: str, part_id: str, cache_key: str | None, *,
               harvest: bool, script: str | None = None) -> dict | None:
    """The `holes` document for one part, or None when there is none to give.

    `harvest=False` (the `get_part` side) reads the sidecar and **never calls
    the kernel**: a read must not trigger a build it did not already need.
    `script` is the text when the caller already has it — `get_part` runs on
    every UI poll and has just read it, and a second read per poll is a file
    read nobody needs.
    """
    record = service.store.get_part(proj, part_id)
    none_declared = {"version": HOLES_SIDECAR_VERSION, "cache_key": cache_key,
                     "holes": None, "warnings": [], "dropped": 0}
    if record.kind != "script":
        # A mesh has no script, so it cannot declare records. That is
        # "declares none" as a fact, not as an absence, and it costs nothing.
        return none_declared
    if script is None:
        script = service.store.read_script(proj, part_id)
    if not _may_declare_holes(script):
        return none_declared
    key = cache_key or service._cache_key_for(proj, record)
    path = _sidecar_path(service, proj, key)
    # The key is compared, not merely stored. The path already contains it, so
    # a mismatch means the file's own header disagrees with where it sits —
    # which is exactly what a restored or hand-copied `.cache/` looks like.
    stored = _read_sidecar(path, key)
    if stored is not None:
        return stored
    if not harvest:
        return None
    result = service.kernel.request(
        "hole_records",
        {"script": script, "params": record.effective_params},
        # affinity=part_id keeps the harvest on the worker that just built this
        # part; unkeyed it round-robins onto a cold shape cache (see the module
        # docstring's 11 354 ms / 1 ms measurement).
        timeout_s=300.0, affinity=part_id)
    payload = _payload(key, result)
    if payload is not None:
        _write_sidecar(path, payload)
    return payload


def _preharvest_ok(service, proj: str, part_id: str) -> bool:
    """Whether to harvest *before* the rebuild rather than after it.

    **Why before at all.** The delta that distinguishes "declares none" from
    "a raw operation dropped the records" is only meaningful for the call that
    actually ran `build(p)` — and with the harvest running *after* the build,
    the harvest is always a `_SHAPE_CACHE` hit, its delta is always 0, and the
    drop check would be dead code everywhere but its own unit test — measured
    on three example parts, the harvest-second call reports `measured: false`
    on every one of them. Harvesting first makes the harvest the call that
    builds; `handle_build` then takes the cache hit and pays only for
    tessellation, so the rebuild's whole kernel time moves by **+3.0%** on
    `prototyping/enclosure_base` (191 -> 197 ms), **-0.2%** on
    `engine/intake_manifold` (12.02 -> 12.00 s) and **+17.5%** on
    `construction/gusset_plate` (70 -> 83 ms — 13 ms of fixed round-trip, on a
    part small enough for it to show).

    **Why not always.** A script that fails — or worse, hangs — would be built
    twice per rebuild, and a doubled 300 s timeout plus a worker respawn is a
    real cost this repo has already paid once (`core/specs.py`'s negative
    cache). So a part whose last rebuild ERRORED is harvested afterwards
    instead, which costs one cache hit and degrades only the drop check, only
    on the first rebuild after the script is fixed.
    """
    try:
        status = service._status.get(service._status_key(proj, part_id)) or {}
    except Exception:                                          # noqa: BLE001
        return True
    return status.get("state") != "error"


def _attempt(fn):
    """`(payload, error)` — the seam never lets an exception escape, and never
    silently swallows one either."""
    try:
        return fn(), None
    except Exception as exc:                                   # noqa: BLE001
        return None, exc


def _failure_warning(exc: Exception) -> str:
    payload = exc.to_payload() if hasattr(exc, "to_payload") else {
        "type": type(exc).__name__, "message": str(exc)}
    return (f"holes: the hole-record harvest failed "
            f"({payload.get('type')}: {payload.get('message')}), so this "
            f"build carries no hole records. The geometry is unaffected.")


def install_rebuild_holes(service) -> None:
    """Attach a part's hole records to every rebuild result and to `get_part`.

    **Why a wrapper and not a `service.py` edit.** The extension-point contract
    forbids editing the service core to add a feature;
    `tools_specs.install_rebuild_specs` and `tools_versioning`'s write guard
    are the precedent.

    **Why `_rebuild` and not the three rebuild-returning tools.** `update_part`,
    `set_params` and `set_solid_materials` all end in `self._rebuild(...)`,
    `_ensure_built` calls it on a miss, and the browser's
    `PATCH /api/projects/{p}/parts/{id}/params` route calls `service.set_params`
    **directly** — wrapping the tools would miss the UI entirely.

    **Nothing raises out of either wrapper.** A harvest that fails leaves the
    key *absent* — never `null`, which means "declares none" — and appends a
    warning naming the failure, because a rebuild whose geometry landed must
    not be reported as broken by a metadata problem, and a metadata problem
    must not be silent either.

    Idempotent: wrapping twice would harvest twice on every rebuild.
    """
    rebuild = service._rebuild
    if not getattr(rebuild, _WRAPPED, False):

        @functools.wraps(rebuild)
        def _rebuild(proj: str, part_id: str) -> dict:
            payload = error = None
            harvested = False
            if _preharvest_ok(service, proj, part_id):
                # Before the build, so the harvest is the call that runs
                # `build(p)` and its delta can see a dropped record. Costs
                # nothing when the sidecar is already there.
                payload, error = _attempt(
                    lambda: _holes_for(service, proj, part_id, None,
                                       harvest=True))
                harvested = error is None
            result = rebuild(proj, part_id)
            # A FAILED rebuild carries no key at all: there is no shape to
            # harvest, and `null` there would claim the part declares none.
            if not isinstance(result, dict) or not result.get("ok"):
                return result
            key = result.get("cache_key")
            stale = payload is not None and payload.get("cache_key") not in (
                None, key)
            if not harvested or stale:
                # No pre-harvest, or the part changed under us between the two
                # calls: measure again against the key the build actually used.
                payload, error = _attempt(
                    lambda: _holes_for(service, proj, part_id, key,
                                       harvest=True))
            if payload is None:
                # Absent, both ways — but a harvest that FAILED says so, while
                # one that simply could not measure (a shape-cache hit with no
                # records) is not a fault and must not shout.
                if error is not None:
                    result["warnings"] = list(result.get("warnings") or []) + [
                        _failure_warning(error)]
                return result
            result["holes"] = payload["holes"]
            if payload.get("warnings"):
                result["warnings"] = (list(result.get("warnings") or [])
                                      + list(payload["warnings"]))
            return result

        setattr(_rebuild, _WRAPPED, True)
        service._rebuild = _rebuild

    get_part = service.get_part
    if not getattr(get_part, _WRAPPED, False):

        @functools.wraps(get_part)
        def _get_part(proj: str, part_id: str) -> dict:
            detail = get_part(proj, part_id)
            if not isinstance(detail, dict):
                return detail
            # A part that does not build carries NO holes key, exactly as a
            # failed rebuild does: there is no shape those records describe,
            # the build error is already the message to act on, and `null`
            # there would claim the part declares none (the
            # `install_rebuild_specs` rule).
            if (detail.get("status") or {}).get("state") != "ok":
                return detail
            try:
                payload = _holes_for(service, proj, part_id, None,
                                     harvest=False,
                                     script=detail.get("script"))
            except Exception:                                 # noqa: BLE001
                return detail
            if payload is None:
                return detail          # nothing harvested: the key is absent
            detail["holes"] = payload["holes"]
            status = detail.get("status")
            if payload.get("warnings") and isinstance(status, dict):
                # A copy, never `+=`: `status["warnings"]` is the very list the
                # service keeps in `_status`, and appending to it would grow by
                # one on every read.
                status["warnings"] = (list(status.get("warnings") or [])
                                      + list(payload["warnings"]))
            return detail

        setattr(_get_part, _WRAPPED, True)
        service.get_part = _get_part


# ------------------------------------------------------- add_holes (FR14)

#: The block marker, counted to suffix the saved previous `build` so chained
#: edits never shadow each other (the `push_pull` precedent).
ADD_HOLES_MARKER = (
    "# --- agentcad hole wizard (auto-generated; edit or remove freely) ---"
)

#: family -> the `toolkit.holes` helper it calls, and the keyword arguments
#: that helper actually takes. A family is never interpolated from the caller's
#: string: it is a key into this table, so nothing but one of these five names
#: can reach the generated source.
_FAMILIES: dict[str, tuple[str, ...]] = {
    "clearance": ("fit", "std", "depth"),
    "tapped": ("std", "depth"),
    "counterbore": ("fit", "std", "depth"),
    "countersink": ("fit", "std", "depth"),
    "drilled": ("std", "depth"),
}
#: `drilled` is spelled `holes.drill` — the record's family name and the
#: function name differ because a record says what the hole *is*.
_HELPERS = {name: ("drill" if name == "drilled" else name) for name in _FAMILIES}

#: Mirrors `toolkit.holes._NAMED_PLANES`, which cannot be imported here — this
#: module is server-side and `toolkit.holes` imports build123d.
#: `tests/test_tools_holes.py` asserts the two sets agree.
NAMED_PLANES = ("top", "bottom", "front", "back", "left", "right")

_ADD_HOLES_BLOCK = """

{marker}
{caveat}from agentcad.toolkit import holes as _agentcad_holes
{imports}{alias} = build


def build(p):
    _agentcad_part = {alias}(p)
    _agentcad_part, _agentcad_recs, _agentcad_warn = _agentcad_holes.{helper}(
{args}
    )
    return _agentcad_part
"""

_PLANE_CAVEAT = (
    "# The plane below is the basis face {index} had when this block was\n"
    "# written. Face indices are mesh-order ordinals and a parameter change\n"
    "# that alters the part's topology renumbers them — the plane does not\n"
    "# follow the face. Re-pick the face if the geometry moves.\n"
)


def _number(value, name: str) -> str:
    """A caller's number as a Python float literal, or a `ValidationError`.

    Everything that reaches the generated script goes through here or through
    a table lookup: a coordinate is a float and `repr(float)` cannot be
    anything but a number, so there is no string for a crafted value to hide
    in (PRD-009's `import os`-in-a-comment lesson).
    """
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{name} must be a number, got {value!r}") from exc
    if number != number or number in (float("inf"), float("-inf")):
        raise ValidationError(f"{name} must be finite, got {value!r}")
    return repr(number)


def _points_literal(points) -> tuple[str, list[list[float]]]:
    """`[(u, v), …]` as source, plus the parsed points for the echo."""
    if isinstance(points, (str, bytes)) or not isinstance(points, (list, tuple)):
        raise ValidationError(
            "points must be a list of [u, v] pairs in the plane's own "
            f"coordinates, got {type(points).__name__}")
    if not points:
        raise ValidationError("points must name at least one hole position")
    parsed, literals = [], []
    for i, point in enumerate(points):
        if (isinstance(point, (str, bytes))
                or not isinstance(point, (list, tuple)) or len(point) != 2):
            raise ValidationError(
                f"points[{i}] must be a [u, v] pair, got {point!r}")
        u = _number(point[0], f"points[{i}][0]")
        v = _number(point[1], f"points[{i}][1]")
        literals.append(f"({u}, {v})")
        parsed.append([float(point[0]), float(point[1])])
    return "[" + ", ".join(literals) + "]", parsed


def _plane_literal(plane, face_index, resolve) -> tuple[str, str, bool, int | None]:
    """`(expression, caveat, needs_Plane_import, face_index)`.

    A named plane stays a **name** in the script — it is a predicate
    re-evaluated on every rebuild (`holes.resolve_plane`), which is the stable
    reference. A picked face becomes a *literal basis*, because the ordinal is
    not stable and the coordinates are: the `sketch_plane` emitted-header
    precedent, caveat and all.
    """
    if face_index is not None:
        if isinstance(face_index, bool) or not isinstance(face_index, int):
            raise ValidationError(
                f"face_index must be an integer face ordinal, got "
                f"{face_index!r}")
        if face_index < 0:
            raise ValidationError(
                f"face_index must be >= 0, got {face_index}")
        info = resolve(face_index)
        basis = ", ".join(
            f"{key}=({', '.join(_number(c, f'the face plane {key}') for c in info[key])})"
            for key in ("origin", "x_dir"))
        z = ", ".join(_number(c, "the face plane normal") for c in info["normal"])
        expr = f"_agentcad_Plane({basis}, z_dir=({z}))"
        return expr, _PLANE_CAVEAT.format(index=face_index), True, face_index
    name = "top" if plane is None else plane
    if not isinstance(name, str) or name.lower() not in NAMED_PLANES:
        raise ValidationError(
            f"plane must be one of {list(NAMED_PLANES)} (or pass a face_index "
            f"to use a picked face's own basis), got {plane!r}")
    return repr(name.lower()), "", False, None


def _hole_call_args(family: str, size, fit, std, depth) -> tuple[list[str], dict]:
    """The keyword arguments for one family, validated against the tables.

    Validation is a *lookup*, not a regex: `size` reaches the script only after
    `hole_standards` has found the row the geometry will use, so a size the
    tables do not have fails here — naming the known sizes — instead of
    becoming a `script_error` on the next rebuild.
    """
    from agentcad.toolkit import hole_standards as tables

    accepted = _FAMILIES[family]
    args: list[str] = []
    echo: dict = {}
    try:
        std_name = tables.check_std("iso" if std is None else std)
        if family == "drilled":
            # No table row: the caller states millimetres, and the record
            # carries no `size` because no standard supplied the number.
            diameter = _number(size, "size")
            # The table families are validated by the lookup itself — a row
            # exists or it does not. `drilled` has no row, so it needs the
            # same `> 0` guard `depth` has: without one a zero or negative
            # diameter is emitted into the script and only fails at rebuild,
            # by which time it is on disk.
            if float(size) <= 0:
                raise ValidationError(
                    f"size must be > 0 mm for a drilled hole, got {size!r}")
            echo["diameter_mm"] = float(size)
        else:
            row = tables.lookup(family=family, size=size, std=std_name)
            echo["size"] = row.get("size") or str(size).strip().upper()
            # The provenance of the number this hole will be cut at, over the
            # SAME rows the record will merge — `merge_provenance`, not one
            # lookup. `add_holes` used to echo a single `lookup()`, which for a
            # seat family is the HEAD row, so the flagship disputed ANSI
            # `#8 normal` clearance cell reported `corroborated: true` to the
            # agent while the record it was about to write said `false` with a
            # conflict. Two surfaces describing one hole must not disagree, and
            # the comment that used to sit here said "the clearance row only",
            # which was the opposite of what the code did.
            rows = [row]
            if family in ("counterbore", "countersink"):
                rows = [tables.clearance(size, fit=fit or "medium",
                                         std=std_name), row]
            echo["provenance"] = tables.merge_provenance(*rows)
        if "fit" in accepted:
            fit_name = tables.canonical_fit("medium" if fit is None else fit,
                                            std_name)
            args.append(f"fit={fit_name!r}")
            echo["fit"] = fit_name
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    args.append(f"std={std_name!r}")
    echo["std"] = std_name
    if depth is not None:
        if "depth" not in accepted:
            raise ValidationError(f"{family} holes take no depth")
        literal = _number(depth, "depth")
        if float(depth) <= 0:
            raise ValidationError(f"depth must be > 0 mm, got {depth!r}")
        args.append(f"depth={literal}")
        echo["depth_mm"] = float(depth)
    return args, echo


def _size_literal(family: str, size) -> str:
    """The size argument as source. Already validated by `_hole_call_args`;
    `drilled` takes millimetres, every other family takes the designation."""
    if family == "drilled":
        return _number(size, "size")
    return repr(str(size).strip().upper())


def register(registry, service) -> None:
    # The only thing read off `service` here is the two bound methods the seam
    # wraps. No cross-pack seam (`branches`, `specs`, `gate_providers`) is
    # touched at registration — this pack loads before every pack that installs
    # one.
    install_rebuild_holes(service)

    def hole_standards(family: str | None = None, size: str | None = None,
                       std: str | None = None) -> dict:
        from agentcad.toolkit import hole_standards as tables
        try:
            return tables.lookup(family=family, size=size,
                                 std=(std or "iso"))
        except ValueError as exc:
            # A bad argument is a caller error, not a 500: the registry maps
            # AppError subclasses to structured payloads, and ValueError would
            # otherwise escape into the server.
            raise ValidationError(str(exc)) from exc

    def _script_part(project: str, part_id: str):
        record = service.store.get_part(project, part_id)
        if record.kind != "script":
            raise ValidationError(
                "add_holes works on script parts only (an imported reference "
                "has no script to append to)")
        return record, service.store.read_script(project, part_id)

    def add_holes(project: str, part_id: str, points, family: str, size,
                  plane: str | None = None, face_index: int | None = None,
                  fit: str | None = None, std: str | None = None,
                  depth: float | None = None) -> dict:
        record, script = _script_part(project, part_id)
        key = str(family).strip().lower()
        if key not in _FAMILIES:
            raise ValidationError(
                f"family must be one of {list(_FAMILIES)}, got {family!r}")
        if plane is not None and face_index is not None:
            raise ValidationError(
                "pass plane OR face_index, not both — they are two different "
                "answers to 'which face'")
        points_src, parsed = _points_literal(points)

        def resolve(index: int) -> dict:
            try:
                return service.kernel.request(
                    "sketch_plane",
                    {"script": script, "params": record.effective_params,
                     "face_index": index},
                    timeout_s=300.0,     # may rebuild the shape from scratch
                )
            except KernelError as exc:
                # A face that is out of range or not planar is a *caller*
                # error, not a kernel fault: `handlers/sketchplane.py` reports
                # it as `contract_error`, and push_pull's precedent is that
                # the tool surface answers this as a validation error naming
                # the face. A crash or a timeout is left exactly as it came.
                if exc.type == ERROR_CONTRACT:
                    raise ValidationError(exc.message, exc.details) from exc
                raise

        plane_src, caveat, needs_plane, resolved_face = _plane_literal(
            plane, face_index, resolve)
        args, echo = _hole_call_args(key, size, fit, std, depth)
        call = [
            "        _agentcad_part,",
            f"        {points_src},",
            f"        {_size_literal(key, size)},",
            f"        plane={plane_src},",
        ] + [f"        {arg}," for arg in args]
        block = _ADD_HOLES_BLOCK.format(
            marker=ADD_HOLES_MARKER,
            caveat=caveat,
            imports=("from build123d import Plane as _agentcad_Plane\n"
                     if needs_plane else ""),
            # Allocated against the aliases ALREADY IN THE SCRIPT — not off
            # this marker's count — so `add_holes` cannot collide with a
            # `push_pull` block, with itself after a middle block is deleted,
            # or with any future pack. See `script_blocks`: a collision is a
            # self-recursing `build`, not a harmless shadow.
            alias=next_build_alias(script),
            helper=_HELPERS[key],
            args="\n".join(call),
        )
        result = apply_generated_block(
            service, project, part_id, script,
            script.rstrip("\n") + block)
        return {
            **with_hint(result),
            "family": key,
            "points": parsed,
            "count": len(parsed),
            "plane": plane_src if resolved_face is None else "face",
            "face_index": resolved_face,
            **echo,
        }

    registry.register(Tool(
        "add_holes",
        "Drill standard holes into a script part: appends a marked, editable "
        "block to the script that calls the matching agentcad.toolkit.holes "
        "helper, then rebuilds through the normal path. `points` are [u, v] "
        "pairs in the target plane's own coordinates. Name the plane "
        "('top'|'bottom'|'front'|'back'|'left'|'right' — a predicate "
        "re-evaluated on every rebuild) or give a picked `face_index`, which "
        "is resolved NOW into a literal Plane basis with the renumbering "
        "caveat written beside it. Sizes come from the same tables "
        "`hole_standards` answers from; family 'drilled' takes a diameter in "
        "mm instead of a designation. Composable: repeated calls append "
        "further blocks. The build result carries the hole records.",
        schema(
            {
                "project": {"type": "string", "description": "Project name"},
                "part_id": {"type": "string", "description": "Part id"},
                "points": {"type": "array",
                           "description": "Hole positions as [u, v] pairs in "
                                          "the plane's own coordinates",
                           "items": {"type": "array",
                                     "items": {"type": "number"}}},
                "family": {"type": "string",
                           "description": "clearance | tapped | counterbore | "
                                          "countersink | drilled"},
                "size": {"type": "string",
                         "description": "Designation (M5, 1/4, #10); for "
                                        "family 'drilled', the diameter in mm"},
                "plane": {"type": "string",
                          "description": "top (default) | bottom | front | "
                                         "back | left | right"},
                "face_index": {"type": "integer",
                               "description": "Mesh-order B-rep face index of "
                                              "a picked planar face; mutually "
                                              "exclusive with plane"},
                "fit": {"type": "string",
                        "description": "fine/close | medium/normal | "
                                       "coarse/loose (clearance-based "
                                       "families)"},
                "std": {"type": "string", "description": "iso (default) | ansi"},
                "depth": {"type": "number",
                          "description": "Blind depth in mm; omit to drill "
                                         "through"},
            },
            ["project", "part_id", "points", "family", "size"],
        ),
        add_holes,
    ))

    registry.register(Tool(
        "hole_standards",
        DESCRIPTION,
        schema(
            {
                "family": {"type": "string",
                           "description": "clearance | tapped | counterbore | "
                                          "countersink (cbore/csk/thread also "
                                          "accepted)"},
                "size": {"type": "string",
                         "description": "Size designation in the standard's "
                                        "own vocabulary: M5 for iso, #10 or "
                                        "1/4 for ansi"},
                "std": {"type": "string", "description": "iso (default) | ansi"},
            },
            [],
        ),
        hole_standards,
    ))
