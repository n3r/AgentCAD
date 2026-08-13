"""Tool pack: the ISO/ANSI hole standards, and the hole-metadata rebuild seam.

`hole_standards {family?, size?, std?}` answers out of the vendored tables in
`agentcad/toolkit/hole_standards.py` — no kernel call, no geometry, no OCP —
so a UI size picker and a part script use the same numbers the drilled hole
used. It is registered **unconditionally**: a pure-data tool can always run.

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

from .model import ValidationError
from .project import ProjectStore
from .tools import Tool, schema

#: Sidecar format. Bump it and every stored file is discarded on read.
HOLES_SIDECAR_VERSION = 1

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
    "all three fits at once. Every answer carries the standard, the revision "
    "and the two published sources it was transcribed from. Lengths are "
    "millimetres, with the table's own unit alongside in `*_native` — an ASME "
    "row is inches and its designation prints inches. Counterbore answers "
    "name the head dimensions (the standard part) separately from the bore (a "
    "documented shop clearance rule, since the published counterbore charts "
    "disagree). Data only — it drills nothing."
)


def _sidecar_path(service, proj: str, key: str) -> Path:
    return service.store.cache_dir(proj) / f"{key}.holes.json"


def _read_sidecar(path: Path) -> dict | None:
    """A stored harvest, or None when there is nothing usable there.

    A corrupt, hand-edited or stale-format sidecar is **discarded and
    recomputed**, never raised (`core/specs.py:434-485`, and `_rebuild`'s own
    `metrics.json` handling): a crash mid-write must not make a part
    unreadable, and a file whose `holes` is not a list-or-null would otherwise
    put residue into a drawing.
    """
    if not path.is_file():
        return None
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        stored = None
    usable = (isinstance(stored, dict)
              and stored.get("version") == HOLES_SIDECAR_VERSION
              and (stored.get("holes") is None
                   or isinstance(stored.get("holes"), list)))
    if not usable:
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
    stored = _read_sidecar(path)
    if stored is not None:
        return stored
    if not harvest:
        return None
    result = service.kernel.request(
        "hole_records",
        {"script": script, "params": record.params},
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
