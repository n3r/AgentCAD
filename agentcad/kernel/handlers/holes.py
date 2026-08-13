"""Worker handler: harvest a part's hole records off the built shape.

One method, ``hole_records {script, params}`` ->
``{"holes": [...], "warnings": [...], "dropped": n}``.

**Why this is a handler and not a registry drain.** The PRD's original design
had ``toolkit.holes`` append to a module-level registry that the worker drained
after ``build(p)`` returned. Two caches skip ``build(p)`` entirely — the
worker's own 16-entry ``_SHAPE_CACHE`` (``build_shape_ns`` returns the cached
shape without calling ``build``) and the service's ``.metrics.json`` fast path
(which never reaches the kernel at all) — so the registry drains empty and the
records vanish with no error anywhere. The records therefore ride the shape
object (design Decision 4, measured through this worker in changelog 0150) and
this handler simply reads them off it.

**The three answers this handler keeps apart.** ``records()`` returns ``[]``
both for a part with no holes and for a part whose records a raw build123d
operation threw away, so the list alone cannot tell them apart. The
*monotonic, never-reset* ``holes.created()`` counter can: read before and after
the build, its delta is the number of records this build made. Hence

* ``holes: [], dropped: 0`` — the part declares no holes;
* ``holes: [], dropped: n`` — ``n`` records were made and did not arrive, with
  the toolkit's own warning text saying what to do about it;
* a delta of **zero** means ``build(p)`` never ran (a shape-cache hit) and no
  comparison is made at all — which is what makes the check immune to the
  warm-worker contamination the discarded registry design worried about.

``dropped`` is an addition to the design's stated ``{holes, warnings}`` shape.
It is there because the seam has to choose between ``null`` ("declares none")
and ``[]`` ("lost them") on the far side of a JSON pipe, and re-deriving that
from the warning *text* would be parsing prose.

**``measured`` is the second addition, and it is the honest half of the delta.**
A zero delta has two causes and they are not the same fact: this call ran
``build(p)`` and the script made no records, or this call took a shape-cache
hit and the question was never asked. Only the first is evidence. So the
handler reports whether the shape it read came back from the cache — by
identity against ``worker._SHAPE_CACHE``'s current values, which is exact
rather than inferred — and a caller must not persist an *unmeasured* empty
answer as "this part declares no holes". Nothing here can recover the delta of
a build that already happened: the dropped records went with the object that
held them. The seam's answer is to harvest **before** the build so that it is
the call that measures.

**A record is a plain dict, so a record can be residue.** Anything can
``setattr`` the carrier attribute. A record that is not a dict with the keys
every downstream reader indexes into is rejected here as ``contract_error``
naming the offending keys — the ``toolkit/specs`` declaration rule — rather
than becoming a ``KeyError`` in the server or a missing callout in a drawing.
"""

from __future__ import annotations

import json

#: The keys every consumer of a record indexes into: the drawing pack matches
#: on ``d``/``centers``/``count`` and prints ``designation``; the sidecar and
#: the agent surface key on ``id``. Everything else in the record shape
#: (``tap``, ``cbore``, ``csk``, ``instances``, ``pattern``, …) is optional and
#: is validated by whoever reads it.
_REQUIRED: dict[str, tuple] = {
    "id": (str,),
    "family": (str,),
    "standard": (str,),
    "designation": (str,),
    "d": (int, float),
    "count": (int,),
    "positions": (list,),
    "centers": (list,),
}


def _type_names(kinds: tuple) -> str:
    return " or ".join(kind.__name__ for kind in kinds)


def register(toolbox: dict) -> dict:
    build_shape_ns = toolbox["build_shape_ns"]
    WorkerError = toolbox["WorkerError"]
    ERROR_CONTRACT = toolbox["ERROR_CONTRACT"]

    def _validate(found: list) -> None:
        """Structural validation of the harvested records.

        Deliberately about *shape*, never about geometry: whether a record
        describes a hole that is still there is a question `carry()` already
        documents as unanswerable without re-measuring (2.1 ms per instance).
        """
        for index, record in enumerate(found):
            if not isinstance(record, dict):
                raise WorkerError(
                    ERROR_CONTRACT,
                    f"hole record {index} is a {type(record).__name__}, not a "
                    f"dict — the records attribute on the returned part holds "
                    f"something no toolkit.holes helper produced")
            rid = record.get("id")
            where = f"hole record {index}" + (
                f" ({rid!r})" if isinstance(rid, str) else "")
            missing = [key for key in _REQUIRED if key not in record]
            if missing:
                raise WorkerError(
                    ERROR_CONTRACT,
                    f"{where} is missing required key(s) {missing} — every "
                    f"record must be one a toolkit.holes helper produced")
            for key, kinds in _REQUIRED.items():
                value = record[key]
                # bools are ints in Python and a count of True is residue.
                if isinstance(value, bool) or not isinstance(value, kinds):
                    raise WorkerError(
                        ERROR_CONTRACT,
                        f"{where}: key {key!r} must be "
                        f"{_type_names(kinds)}, got "
                        f"{type(value).__name__}")
            try:
                json.dumps(record)
            except (TypeError, ValueError) as exc:
                raise WorkerError(
                    ERROR_CONTRACT,
                    f"{where} is not JSON-safe ({exc}) — a record holding a "
                    f"build123d object cannot cross the kernel pipe; round "
                    f"coordinates into plain floats") from exc

    def hole_records(params: dict) -> dict:
        from agentcad.toolkit import holes

        # The LIVE shape cache, reached through the function the toolbox handed
        # us rather than by importing it. The worker is spawned as
        # `python -m agentcad.kernel.worker`, so it runs as `__main__`, and a
        # handler pack that writes `from ..worker import _SHAPE_CACHE` gets a
        # SECOND, freshly-imported copy of the module whose cache is always
        # empty — measured here (every call then reports itself as a fresh
        # build). Importing a worker *function* is fine and several packs do;
        # importing worker *state* is not. `build_shape_ns.__globals__` is the
        # module dict of the copy that is actually running.
        cache = build_shape_ns.__globals__.get("_SHAPE_CACHE") or {}
        # Real references, not ids: holding them keeps a shape from being
        # freed and its id reused underneath the identity test below. An
        # absent cache degrades to "measured", i.e. to the behaviour of a
        # worker that has no shape cache at all.
        cached_before = list(cache.values())
        # Bracket the build. `created()` is process-wide and never reset, so
        # only the delta across THIS build means anything.
        before = holes.created()
        shape, _values, _warnings, _ns = build_shape_ns(
            params["script"], params.get("params", {}))
        measured = all(shape is not other for other in cached_before)
        found = holes.records(shape)
        _validate(found)
        warning = holes.dropped_records_warning(shape, before)
        # Agrees with `warning` by construction: it is None exactly when this
        # is 0 (a zero delta — the cache-hit case — yields both).
        dropped = max(0, (holes.created() - before) - len(found))
        return {"holes": found,
                "warnings": [warning] if warning else [],
                "dropped": dropped,
                "measured": measured}

    return {"hole_records": hole_records}
