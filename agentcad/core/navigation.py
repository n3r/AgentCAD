"""Navigation metadata grammar: part/instance folders and part tags (PRD-027).

Folders and tags are **manifest metadata**, not directories — a part's script
stays at ``parts/<id>.py`` whatever folder it is filed under, so organizing a
project never rewrites a path, breaks a package materialisation, or moves a
byte in git that a human did not ask to move (design §1, Risk 1).

The two grammars are deliberately asymmetric, and the asymmetry is the whole
of the rule (ruling 9):

* a **folder** is a *display name*. It is stored verbatim — case, spaces and
  all — and validated strictly, because a segment with a stray trailing space
  is a folder that looks identical to another one in the tree and sorts apart
  from it. Matching is case-insensitive, per segment (see `folder_matches`).
* a **tag** is an *identifier*. It is normalized on write — stripped,
  lowercased, de-duplicated preserving first-seen order — because a tag is
  typed over and over and ``M5``/``m5``/`` m5 `` must be one tag, not three.
  What is still invalid *after* normalization (an inner space, ``#``, ``:``)
  is a `ValidationError` naming it, never a silent drop.

This module imports nothing but `model` and the stdlib **at module level**:
the store, the tool pack, the routes and (from slice 2) the search engine all
validate through it, so it must sit below every one of them. The slice-4
additions below (`BulkExecutor`, `dashboard`) do need three things from higher
up — `packages.manager.manifest_scope`, `thumbnails.has_thumb` and the kernel
client's `KernelError` — and every one of them is imported **inside the
function that uses it**, because `project.py` imports this module at its top
and `thumbnails.py` imports `project.py`: a module-level import of either
would be an import cycle, not a style question.
"""

from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from .model import AppError, ValidationError, error_type

#: One folder segment: starts alphanumeric, then up to 39 more of
#: ``[A-Za-z0-9 _.-]``. Applied with ``fullmatch`` (never ``match``): with
#: ``match`` a trailing newline slips past the ``$``.
FOLDER_SEGMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.-]{0,39}$")

#: One tag, AFTER stripping and lowercasing.
TAG_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,31}$")

#: Maximum ``/``-separated segments in a folder path.
MAX_FOLDER_DEPTH = 8

#: Maximum tags on one part (counted after de-duplication).
MAX_TAGS = 32

#: Characters of one scalar an error may echo back.
_MAX_ECHO = 200
#: Entries of one list or object an error may echo back.
_MAX_ECHO_ITEMS = 20
#: How far into a caller's nesting an echo descends before summarizing.
_MAX_ECHO_DEPTH = 3


def _safe(value, _depth: int = 0):
    """A caller's value, made safe to put in an error message or ``details``.

    Two hazards, one helper — and both were real 500s or amplifiers:

    * **NaN and Infinity are not JSON.** Starlette serializes every response
      with ``allow_nan=False``, so a ``float("nan")`` echoed back — nested one
      level down inside a list or an object, where the tool registry's
      shallow type check never looks — raised inside the serializer and turned
      an honest 200 refusal envelope into an HTTP **500**. Every float
      therefore goes out as its ``repr`` (``"nan"``, ``"inf"``, ``"2.5"``): a
      string is what the reader wanted anyway, and it is the only spelling
      that survives the round trip for all of them.
    * **An echo is an amplifier.** A caller who sends a megabyte of tag, or a
      selection of ten thousand ids, must not get it back in the message *and*
      again in ``details``. Scalars are capped at :data:`_MAX_ECHO`
      characters, containers at :data:`_MAX_ECHO_ITEMS` entries (the rest
      counted, never dropped silently), and nesting at
      :data:`_MAX_ECHO_DEPTH`.

    Structure is preserved where it is cheap to preserve: a list stays a list
    and an object an object, so a client can still read ``details.args.tags``
    rather than a string blob. ``None``, ``bool`` and ordinary ``int`` are
    returned untouched — they are already JSON, already small, and several
    callers compare against them.
    """
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        # `json.dumps` renders an int with `str()`, which raises above
        # CPython's 4 300-digit conversion limit: the same 500, another door.
        return value if value.bit_length() <= 256 \
            else f"<int, {value.bit_length()} bits>"
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, str):
        return value if len(value) <= _MAX_ECHO else value[:_MAX_ECHO] + "…"
    if isinstance(value, (list, tuple)):
        if _depth >= _MAX_ECHO_DEPTH:
            return f"<list of {len(value)}>"
        out = [_safe(item, _depth + 1) for item in value[:_MAX_ECHO_ITEMS]]
        if len(value) > _MAX_ECHO_ITEMS:
            out.append(f"… and {len(value) - _MAX_ECHO_ITEMS} more")
        return out
    if isinstance(value, dict):
        if _depth >= _MAX_ECHO_DEPTH:
            return f"<object with {len(value)} keys>"
        items = list(value.items())[:_MAX_ECHO_ITEMS]
        out = {str(key)[:_MAX_ECHO]: _safe(item, _depth + 1)
               for key, item in items}
        if len(value) > _MAX_ECHO_ITEMS:
            out["…"] = f"and {len(value) - _MAX_ECHO_ITEMS} more"
        return out
    try:
        text = str(value)
    except Exception:  # noqa: BLE001 — a __str__ that raises is still an echo
        return f"<unprintable {type(value).__name__}>"
    return text if len(text) <= _MAX_ECHO else text[:_MAX_ECHO] + "…"


def normalize_folder(value) -> str | None:
    """Validate a folder path and return it verbatim, or ``None`` for root.

    ``None`` and ``""`` both mean root — the tree has no separate "no folder"
    and "empty folder" state, and a client that clears a text input sends the
    empty string. Everything else must be a ``/``-joined path of 1..8
    segments matching :data:`FOLDER_SEGMENT_RE` with no leading or trailing
    whitespace in any segment.

    Nothing is stripped or case-folded: a folder is a name a human typed and
    will read back. That is exactly why `" Pistons"` is refused rather than
    quietly repaired — the repaired value would differ from what the caller
    believes it wrote, and two spellings of one folder would coexist in the
    tree.
    """
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, str):
        raise ValidationError(
            f"invalid folder {_safe(value)!r}: must be a string or null",
            {"folder": _safe(value)},
        )
    if value == "":
        return None
    segments = value.split("/")
    if len(segments) > MAX_FOLDER_DEPTH:
        raise ValidationError(
            f"invalid folder {_safe(value)!r}: at most {MAX_FOLDER_DEPTH} "
            f"segments (got {len(segments)})",
            {"folder": _safe(value)},
        )
    for segment in segments:
        if segment != segment.strip():
            raise ValidationError(
                f"invalid folder {_safe(value)!r}: segment "
                f"{_safe(segment)!r} has leading or trailing whitespace",
                {"folder": _safe(value), "segment": _safe(segment)},
            )
        if not FOLDER_SEGMENT_RE.fullmatch(segment):
            raise ValidationError(
                f"invalid folder {_safe(value)!r}: segment "
                f"{_safe(segment)!r} must match "
                r"[A-Za-z0-9][A-Za-z0-9 _.-]{0,39}",
                {"folder": _safe(value), "segment": _safe(segment)},
            )
    return value


def normalize_tags(value) -> list[str]:
    """Normalize a list of tags: strip, lowercase, de-duplicate, validate.

    Order is first-seen (a tag list is something a human reads, not a set),
    and the de-duplication happens *before* the :data:`MAX_TAGS` count so
    pasting the same tag twice never costs a slot.

    ``None`` is NOT accepted. Every caller in this PRD uses ``None`` to mean
    "leave the tags alone" and ``[]`` to mean "clear them"; making this
    function turn ``None`` into ``[]`` would let one missing guard silently
    erase a part's tags.
    """
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)):
        raise ValidationError(
            f"invalid tags {_safe(value)!r}: must be an array of strings",
            {"tags": _safe(value)},
        )
    out: list[str] = []
    seen: set[str] = set()
    for raw in value:
        if isinstance(raw, bool) or not isinstance(raw, str):
            raise ValidationError(
                f"invalid tag {_safe(raw)!r}: must be a string",
                {"tag": _safe(raw)})
        tag = raw.strip().lower()
        if not TAG_RE.fullmatch(tag):
            raise ValidationError(
                f"invalid tag {_safe(raw)!r}: must match "
                "[a-z0-9][a-z0-9_.-]{0,31} after stripping and lowercasing",
                {"tag": _safe(raw)},
            )
        if tag in seen:
            continue
        seen.add(tag)
        out.append(tag)
    if len(out) > MAX_TAGS:
        raise ValidationError(
            f"too many tags: {len(out)} (max {MAX_TAGS})",
            {"count": len(out), "max": MAX_TAGS},
        )
    return out


def folder_matches(folder: str | None, query: str) -> bool:
    """Does ``folder`` sit at, or under, ``query``? (case-insensitive)

    The comparison is **segment-wise**, never a string prefix: ``"a/b"`` is
    under ``"a"`` but not under ``"a/bc"``. An empty query is the empty
    prefix and matches every folder including root — search's ``folder:``
    term inherits that, so a blank value never silently means "root only".

    **Total over the stored value, strict about the query** (the
    `PartRecord.config_params` discipline). ``folder`` comes off a manifest a
    hand edit or a merge can shape, so a non-string one is read as root and
    simply does not match — a corrupt entry must not 500 the search that is
    scanning a thousand parts. ``query`` comes from a caller, so a non-string
    one is a `ValidationError`: returning ``False`` for it would silently drop
    every row from a result set and read as "no matches" rather than "you
    passed the wrong type".
    """
    if not isinstance(query, str):
        raise ValidationError(
            f"folder query must be a string, got {type(query).__name__}",
            {"query": _safe(query)},
        )
    wanted = [s.lower() for s in query.strip("/").split("/") if s != ""]
    if not wanted:
        return True
    stored = folder if isinstance(folder, str) else ""
    have = [s.lower() for s in stored.split("/") if s != ""]
    return have[:len(wanted)] == wanted


# =========================================================== bulk operations

#: The six things a selection of parts can be asked to do (design §4, FR5).
#: Five of them are manifest writes and land as **one** undo step; ``export``
#: is not a mutation at all and deliberately leaves no history entry.
OPS = ("material", "tag", "untag", "folder", "export", "delete")

#: Ids per bulk call. A ceiling, not a performance number: a manifest op is one
#: read-modify-write whatever the count, and this is what stops a typo'd
#: selection from turning into a 10 000-entry edit map.
MAX_BULK = 500

#: Ids per **export** call. Each one is a kernel round trip that may rebuild the
#: shape from scratch (`service.export_part` allows itself 300 s), so the bound
#: is an order of magnitude tighter than the manifest ops'.
MAX_BULK_EXPORT = 50


def _payload(exc: AppError) -> dict:
    """An application error as the wire shape every client already parses.

    ``notfound_error`` / ``validation_error`` / ``conflict_error`` — the
    spelling `model.error_type` owns and `ToolRegistry.call` has always put on
    the wire. The design spec writes these rows' ``error.type`` as
    ``"NotFoundError"``/``"ConflictError"``; that is the exception *class*
    name, and using it here would give one payload two spellings depending on
    whether the failure was per-item or whole-call. The house spelling wins —
    ``error_type``'s docstring is explicit ("Do not spell a new one").
    """
    return {"type": error_type(exc), "message": exc.message,
            "details": exc.details}


def _bulk_ids(part_ids, limit: int) -> list[str]:
    """De-duplicate (order kept) and bound a bulk selection.

    De-duplication is first-seen and silent: a selection is a set of rows a
    human clicked, and the same part reached twice through two folders is one
    part, not an error. The *count* that is bounded is the de-duplicated one,
    for the same reason.
    """
    if isinstance(part_ids, (str, bytes)) or not isinstance(
            part_ids, (list, tuple)):
        raise ValidationError(
            f"part_ids must be an array of strings, got "
            f"{type(part_ids).__name__}", {"part_ids": _safe(part_ids)})
    for part_id in part_ids:
        if isinstance(part_id, bool) or not isinstance(part_id, str) \
                or not part_id:
            raise ValidationError(
                f"invalid part id {_safe(part_id)!r}: must be a non-empty "
                "string", {"part_id": _safe(part_id)})
    ids = list(dict.fromkeys(part_ids))
    if not ids or len(ids) > limit:
        raise ValidationError(
            f"part_ids must name 1..{limit} parts (got {len(ids)})",
            {"count": len(ids), "max": limit})
    return ids


class BulkExecutor:
    """One gesture over many parts — and, for the five mutating ops, **one
    undo step** (design §4, ruling 4).

    The shape every op answers with::

        {"op", "ok": <every row ok>, "applied": <rows the call carried out>,
         "results": [{"id", "ok", "error"?, ...}], "undo_label": str | None}

    Two kinds of failure, and keeping them apart is the whole design:

    * **Per-item validity** — an unknown id, a part whose tag list would go
      over the cap, a part an assembly still uses — is a ``results`` row with
      ``ok: false`` and an ``error`` payload. The rest of the selection still
      lands. ``ok`` is then ``false`` and ``applied`` says how many parts the
      write actually touched.

      A row's ``ok`` is about the **write**, and only the write. The
      ``material`` op rebuilds each touched part afterwards, and a part whose
      script does not build comes back ``ok: true`` with ``rebuilt: false``
      and the build error: the material IS in the manifest, the publish went
      out and one Cmd+Z takes it back, so saying ``ok: false`` about it would
      be false in the one direction that matters.
    * **A refusal of the gesture** — an unknown op, a material that does not
      exist, a malformed folder or tag, a selection over the bound, or a part
      another human is holding (PRD-008 claim, ruling 5) — **raises**, before
      any write. Partial success is for validity, not for stepping on a
      colleague.

    The counting invariants, which are what the tests pin: every manifest op is
    **one** `save_manifest` and **one** ``project_changed`` publish. A bulk op
    composed out of N single-part service calls would be N git snapshots and N
    presses of Cmd+Z to take back one gesture, which is the defect this class
    exists to not have.
    """

    def __init__(self, service) -> None:
        self.service = service

    # -------------------------------------------------------------- entry

    def run(self, proj: str, part_ids, op: str, args=None) -> dict:
        """Run *op* over *part_ids*. See the class docstring for the shape."""
        if not isinstance(op, str) or op not in OPS:
            raise ValidationError(
                f"unknown bulk op {_safe(op)!r}: one of {', '.join(OPS)}",
                {"op": _safe(op), "known": list(OPS)})
        if args is None:
            args = {}
        if not isinstance(args, dict):
            raise ValidationError(
                f"args must be an object, got {type(args).__name__}",
                {"args": _safe(args)})
        # The bound is read from the op, so the 50-id export ceiling is applied
        # before anything looks at a part.
        ids = _bulk_ids(part_ids,
                        MAX_BULK_EXPORT if op == "export" else MAX_BULK)
        if op == "export":
            return self._export(proj, ids, args)
        if op == "delete":
            return self._delete(proj, ids, args)
        return self._meta(proj, ids, op, args)

    # ------------------------------------------- material / tag / untag / folder

    def _meta(self, proj: str, ids: list[str], op: str, args: dict) -> dict:
        # Function-local by necessity, not by taste: `project.py` imports this
        # module at its top, and `packages.manager` sits well above it.
        from .packages.manager import manifest_scope

        service = self.service
        store = service.store
        # EVERYTHING through the write is inside both locks, outer to inner —
        # the order `update_parts_meta`'s docstring names (`manifest_scope`
        # against the configuration tools and the package manager,
        # `service._lock` against set_params/update_part). The *planning* is in
        # here too, not only the write: `tag`/`untag` merge against the tag
        # list they read, and a base read outside the lock is a lost update —
        # a concurrent `set_part_meta` between the read and the save would be
        # clobbered by a merged list computed from the pre-state. Reading the
        # manifest under the lock also makes "the part existed when we planned
        # and was gone when we wrote" impossible, which is what keeps a missing
        # part a per-item row rather than a `NotFoundError` for the whole call.
        # Both locks are reentrant, and a refusal raised in here has written
        # nothing.
        with manifest_scope(store, proj), service._lock:
            results, edits, fields = self._plan(proj, ids, op, args)
            if edits:
                store.update_parts_meta(proj, edits)

        applied = len(edits)
        label = f"bulk {op} ×{applied}" if applied else None
        if edits:
            # `project_changed` FIRST and with **no `part`**: the snapshot
            # message becomes the undo label, and "project_changed (bulk
            # material x6)" is what has to read back as "Undo bulk material x6".
            service.bus.publish({"type": "project_changed", "project": proj,
                                 "reason": label})
            service.bus.publish({"type": "parts_meta_changed",
                                 "project": proj, "part_ids": list(edits),
                                 "fields": fields})
            if op == "material":
                # AFTER the publish, per part: material feeds `_cache_key`
                # through `service.material_density`, so a written material
                # with no rebuild leaves the mesh, the badge and the mass
                # computed against the OLD density. These publish `rebuild_*`
                # only — never a second `project_changed`, which is what keeps
                # the whole gesture at one undo step. Outside the locks, like
                # `service.update_part`'s own rebuild: a build under
                # `service._lock` would serialize the whole service behind it.
                rows = {row["id"]: row for row in results}
                for part_id in edits:
                    post = service.rebuild_after_write(proj, part_id)
                    row = rows[part_id]
                    # `ok` is about the WRITE, and the write landed: the
                    # material is in the manifest, the publish went out, and
                    # one Cmd+Z takes it back. A failed rebuild is a fact
                    # about the part's script, reported as `rebuilt: false`
                    # plus the build error — flipping `ok` said "this part was
                    # not changed" about a part that WAS changed, and every
                    # reader of the row (the bulk bar's applied count, an
                    # agent's retry loop) believed it.
                    row["rebuilt"] = bool(post.get("ok"))
                    if not post.get("ok"):
                        row["error"] = post.get("error")
        return {"op": op, "ok": all(row["ok"] for row in results),
                "applied": applied, "results": results, "undo_label": label}

    def _plan(self, proj: str, ids: list[str], op: str,
              args: dict) -> tuple[list[dict], dict[str, dict], list[str]]:
        """``(results, edits, fields)`` for a metadata op — **writes nothing**.

        Called with both locks held (see `_meta`). Raises for anything wrong
        with the *gesture*; records anything wrong with one *part* as a row.
        """
        store = self.service.store
        manifest = store.manifest(proj)          # unknown project -> NotFound
        entries = {p["id"]: p for p in manifest["parts"]}

        # ---- the gesture's own arguments, validated once, writing nothing.
        material: str | None = None
        folder: str | None = None
        wanted: set[str] = set()
        ordered: list[str] = []
        if op == "material":
            material = args.get("material")
            if not isinstance(material, str) or not material:
                raise ValidationError(
                    'bulk material needs {"material": "<id>"}',
                    {"args": _safe(args)})
            # The same check `update_part_entry` makes on the single-part path,
            # so a bulk change can never accept a material a single one refuses.
            store._validate_material(manifest, material)
            fields = ["material"]
        elif op == "folder":
            # The key must be PRESENT: `folder: null` MEANS root, so an omitted
            # key cannot double as it (the `{"config": null}` precedent).
            if "folder" not in args:
                raise ValidationError(
                    'bulk folder needs a "folder" key (null or "" means '
                    'root)', {"args": _safe(args)})
            folder = normalize_folder(args["folder"])
            fields = ["folder"]
        else:
            ordered = normalize_tags(args.get("tags") or [])
            if not ordered:
                raise ValidationError(
                    f'bulk {op} needs a non-empty "tags" array',
                    {"args": _safe(args)})
            wanted = set(ordered)
            fields = ["tags"]

        # ---- per part: an edit, or a row saying why not.
        results: list[dict] = []
        edits: dict[str, dict] = {}
        for part_id in ids:
            entry = entries.get(part_id)
            if entry is None:
                results.append({
                    "id": part_id, "ok": False,
                    "error": {"type": "notfound_error",
                              "message": f"part {_safe(part_id)!r} not found",
                              "details": {"part": _safe(part_id)}}})
                continue
            try:
                if op == "material":
                    edit = {"material": material}
                elif op == "folder":
                    edit = {"folder": folder}
                else:
                    # The stored list is normalized on write, but a hand edit or
                    # a merge can put anything in a manifest — so the merged
                    # list is re-normalized here, where a failure is ONE row's
                    # problem. Inside `update_parts_meta` the same failure would
                    # refuse the whole bulk.
                    current = list(entry.get("tags") or [])
                    if op == "tag":
                        merged = current + ordered
                    else:
                        merged = [t for t in current
                                  if not (isinstance(t, str)
                                          and t.strip().lower() in wanted)]
                    edit = {"tags": normalize_tags(merged)}
            except AppError as exc:
                results.append({"id": part_id, "ok": False,
                                "error": _payload(exc)})
                continue
            edits[part_id] = edit
            results.append({"id": part_id, "ok": True, **edit})
        return results, edits, fields

    # ------------------------------------------------------------- delete

    def _assert_unclaimed(self, proj: str, ids: list[str]) -> None:
        """Refuse the gesture if another human holds any part it would delete.

        `remove_parts` reaches `save_manifest` — and therefore `write_guard` —
        **outside any `write_scope`**, so the guard saw ``current_write_part()
        is None`` and a PRD-008 claim on one of the selected parts never
        refused: a bulk delete walked through a colleague's held part and took
        its script with it. `update_parts_meta` already runs the guard once per
        id inside that id's scope before its first mutation, and this is the
        same preflight for the delete path — the *whole* call refuses (ruling
        5: partial success covers per-item validity, not stepping on a
        colleague), and it runs inside the caller's locks having written
        nothing.

        Only ids that are actually in the project are checked. An unknown id
        holds nothing, and turning it into a claim refusal would replace an
        honest per-item ``notfound_error`` row with a whole-call conflict.
        """
        from . import locks

        store = self.service.store
        if store.write_guard is None:
            return
        known = {p["id"] for p in store.manifest(proj)["parts"]}
        for part_id in ids:
            if part_id in known:
                with locks.write_scope(part_id):
                    store.write_guard(proj)

    def _delete(self, proj: str, ids: list[str], args: dict) -> dict:
        from .packages.manager import manifest_scope

        service = self.service
        force = args.get("force", False)
        if not isinstance(force, bool):
            raise ValidationError(
                f"delete force must be a boolean, got {type(force).__name__}",
                {"force": _safe(force)})
        with manifest_scope(service.store, proj), service._lock:
            self._assert_unclaimed(proj, ids)
            outcome = service.store.remove_parts(proj, ids, force=force)
            # The eviction `service.delete_part` does, part for part: the badge
            # AND every configuration build state of that part (a
            # `_config_status` key is the `_status` key plus the name).
            for part_id in outcome["removed"]:
                prefix = service._status_key(proj, part_id)
                service._status.pop(prefix, None)
                for key in [k for k in service._config_status
                            if k[:2] == prefix]:
                    service._config_status.pop(key, None)

        removed = set(outcome["removed"])
        dropped = outcome["instances_removed"]
        results = [
            {"id": part_id, "ok": True,
             "instances_removed": dropped.get(part_id, [])}
            if part_id in removed else
            {"id": part_id, "ok": False, "error": outcome["errors"][part_id]}
            for part_id in ids
        ]
        applied = len(outcome["removed"])
        label = f"bulk delete ×{applied}" if applied else None
        if applied:
            # No `parts_meta_changed`: the parts are gone, there is no row left
            # to re-file. `part` is omitted for the same reason `_meta` omits
            # it — the label is about the gesture, not about one part.
            service.bus.publish({"type": "project_changed", "project": proj,
                                 "reason": label})
        return {"op": "delete", "ok": all(row["ok"] for row in results),
                "applied": applied, "results": results, "undo_label": label}

    # ------------------------------------------------------------- export

    def _export(self, proj: str, ids: list[str], args: dict) -> dict:
        from ..kernel.client import KernelError

        service = self.service
        service.store.manifest(proj)             # unknown project -> NotFound
        fmt = args.get("format")
        if not isinstance(fmt, str) or not fmt:
            raise ValidationError(
                'bulk export needs {"format": "step|stl|3mf"}',
                {"args": _safe(args)})
        service._check_format(fmt)
        extra: dict = {}
        tolerance = args.get("tolerance")
        if tolerance is not None:
            if isinstance(tolerance, bool) or not isinstance(
                    tolerance, (int, float)):
                raise ValidationError(
                    "export tolerance must be a number",
                    {"tolerance": _safe(tolerance)})
            # A deflection is a positive length in millimetres. NaN, ±inf and
            # anything ≤ 0 are refused HERE rather than handed to the worker:
            # a non-finite one is not JSON (it would 500 the response on the
            # way back out, `_safe`'s first hazard), and a zero or negative
            # one asks the tessellator for infinite refinement — 50 kernel
            # round trips that each run to their 300 s ceiling.
            if not math.isfinite(tolerance) or tolerance <= 0:
                raise ValidationError(
                    "export tolerance must be a finite number greater than 0",
                    {"tolerance": _safe(tolerance)})
            extra["tolerance"] = float(tolerance)

        results: list[dict] = []
        applied = 0
        for part_id in ids:
            try:
                out = service.export_part(proj, part_id, fmt, **extra)
            except AppError as exc:
                results.append({"id": part_id, "ok": False,
                                "error": _payload(exc)})
                continue
            except KernelError as exc:
                # The kernel-class payload, type intact — a script that fails
                # to build is one row's failure, not the call's.
                results.append({"id": part_id, "ok": False,
                                "error": exc.to_payload()})
                continue
            applied += 1
            results.append({"id": part_id, "ok": True,
                            "path": out.get("path"),
                            "size_bytes": out.get("size_bytes")})
        # No publish and no label: an export writes into `exports/`, changes no
        # authored state, and must not cost the user an undo entry (ruling 4).
        return {"op": "export", "ok": all(row["ok"] for row in results),
                "applied": applied, "results": results, "undo_label": None}


# ================================================================= dashboard

def _mtime_iso(path: Path) -> str | None:
    """``project.json``'s mtime as ISO-8601 UTC, or None if it cannot be read."""
    try:
        stamp = path.stat().st_mtime
    except OSError:
        return None
    return datetime.fromtimestamp(stamp, tz=timezone.utc).isoformat()


def dashboard(service) -> dict:
    """Every project as one card's worth of facts (design §6, FR6).

    ``{"projects": [{name, path, n_parts, n_instances, mass_g|None, failing,
    last_modified|None, thumb|None}]}``.

    **Kernel-free and build-free by contract**, and that is a *product*
    decision, not an optimization: this is the first screen, it runs over
    however many projects a person has, and a listing that could start a build
    would make opening the app cost minutes of CPU on a laptop. So it reads
    exactly two things — the manifest on disk and the service's in-memory
    ``_status`` — and it opens no part script, calls no kernel op and renders
    no pixel.

    What it can therefore only answer honestly:

    * ``mass_g`` is the sum of the built parts' metrics, and **None the moment
      one part is not built ok with metrics** (ruling 8). A partial sum would
      be a number a person would read as the project's mass and act on. A
      project with no parts is ``0.0`` — nothing is unknown about it.
    * ``failing`` counts ``_status`` error states, iterating the *manifest*, so
      a stale badge for a part that has since been deleted is not counted.
    * ``thumb`` is a URL when `thumbnails.has_thumb` says a file already
      exists to answer from — `is_file` checks, never a render. It is a hint:
      a stale mesh makes it optimistic, and the cost of being wrong is an
      ``<img>`` that 404s into its placeholder.
    """
    # Function-local: `thumbnails` imports `project`, which imports this module.
    from .thumbnails import has_thumb

    projects = []
    for row in service.list_projects():
        name = row["name"]
        try:
            manifest = service.store.manifest(name)
        except AppError:
            # `list_projects` already skips a corrupt manifest; this covers the
            # narrow race where one is deleted or broken between the two reads.
            continue
        parts = manifest.get("parts") or []
        instances = (manifest.get("assembly") or {}).get("instances") or []
        # Hoisted: `_status_key` calls `store.lock_key`, which resolves the
        # working tree — once per project, not once per part.
        lock_key = service.store.lock_key(name)
        mass = 0.0
        known = True
        failing = 0
        for entry in parts:
            status = service._status.get((lock_key, entry.get("id"))) or {}
            if status.get("state") == "error":
                failing += 1
            value = (status.get("metrics") or {}).get("mass_g") \
                if status.get("state") == "ok" else None
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                mass += float(value)
            else:
                known = False
        projects.append({
            "name": name,
            "path": row["path"],
            "n_parts": len(parts),
            "n_instances": len(instances),
            "mass_g": mass if known else None,
            "failing": failing,
            "last_modified": _mtime_iso(Path(row["path"]) / "project.json"),
            # `quote(..., safe="")`: `list_projects` reports a DIRECTORY
            # name, and nothing validates that one — a project folder called
            # "my proj" (or one with a `#`, or a `?`) is listed verbatim, and
            # interpolating it raw produced a URL the browser either mangled
            # or truncated at the fragment. Encoded here rather than in
            # `dashboard.js` because the payload's contract is a URL, not a
            # name (`api.projectThumbUrl` encodes the same way for the same
            # reason), and a client that already has the string must not have
            # to know it needs repairing.
            "thumb": (f"/api/projects/{quote(name, safe='')}/thumb.png"
                      if has_thumb(service, name) else None),
        })
    return {"projects": projects}
