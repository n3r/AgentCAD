"""Tool pack: navigation at scale — part metadata, search, bulk ops (PRD-027).

**Load order.** Tool packs are discovered by `_load_tool_packs` in
``pkgutil.iter_modules`` order, which is alphabetical: this module sorts at
``nav`` — after ``tools_materials`` (so `service.materials` is already the
project-aware resolver by the time anything here reads a material) and before
``tools_packages`` / ``tools_proposals``. That last one matters and is the
``tools_run_checks`` trap in AGENTS.md restated: ``tools_proposals`` sets
``service.gate_providers = []`` unconditionally, so a gate provider registered
from here would be thrown away without a word. **This pack registers none**,
and must not grow one — a navigation concern has nothing to prove about a
package anyway.

**What it does not touch.** Organizing is not building. Nothing in this pack
calls `ensure_mesh` or `_ensure_built`, nothing enters `_cache_key`'s payload,
and `folder`/`tags` are absent from every geometry request — moving a part
between folders must never invalidate its cached mesh.

**Events.** A metadata write publishes `project_changed` (which is what the
history snapshot hook sees — one publish, one snapshot, one undo step) and
then `parts_meta_changed`, the narrow event a client uses to re-file rows
without reloading the whole project. The order is load-bearing: a client that
acts on `parts_meta_changed` must be able to assume the durable write is
already done and already snapshotted.
"""

from __future__ import annotations

from .tools import Tool, schema

#: "this keyword was not passed" — `folder=None` MEANS root, so it cannot
#: double as "leave it alone" (the `active_config` precedent in project.py).
_UNSET = object()

_FOLDER_DOC = (
    "Folder path, '/'-separated, 1-8 segments of "
    "[A-Za-z0-9][A-Za-z0-9 _.-]{0,39} with no leading/trailing space "
    "(e.g. 'Chassis/Left side'). Case is kept as typed and matched "
    "case-insensitively. Send \"\" (or null) to move the part to the root; "
    "OMIT the key to leave the folder unchanged."
)

_TAGS_DOC = (
    "Full replacement tag list. Tags are normalized on write — stripped, "
    "lowercased, de-duplicated keeping first-seen order — and must match "
    "[a-z0-9][a-z0-9_.-]{0,31} afterwards (max 32 per part); a tag that is "
    "still invalid is a validation_error naming it. Send [] to clear all "
    "tags; OMIT the key to leave them unchanged."
)


def register(registry, service) -> None:
    def set_part_meta(project: str, part_id: str, folder=_UNSET,
                      tags: list | None = None) -> dict:
        """Organize one part: its folder and/or its tags (FR1).

        Both fields are independently optional. Omitting one leaves it alone;
        ``folder`` of ``""``/``null`` files the part at the project root, and
        ``tags`` of ``[]`` clears them. Omitting BOTH is a read-back: it
        writes nothing and publishes nothing, because a `project_changed` for
        a change nobody made costs every connected browser a reload and
        (where git is available) asks the history for a snapshot of nothing.
        """
        fields = []
        if folder is not _UNSET:
            fields.append("folder")
        if tags is not None:
            fields.append("tags")
        if not fields:
            record = service.store.get_part(project, part_id)  # 404s honestly
            return {"id": record.id, "folder": record.folder,
                    "tags": list(record.tags)}
        record = service.store.update_part_meta(
            project, part_id,
            **({} if folder is _UNSET else {"folder": folder}),
            tags=tags,
        )
        # project_changed FIRST: it is the publish the history hook snapshots
        # on, so anything reacting to parts_meta_changed can assume the write
        # is durable and already undoable.
        service.bus.publish({"type": "project_changed", "project": project,
                             "part": part_id, "reason": "meta"})
        service.bus.publish({"type": "parts_meta_changed", "project": project,
                             "part_ids": [part_id], "fields": fields})
        return {"id": record.id, "folder": record.folder,
                "tags": list(record.tags)}

    registry.register(Tool(
        "set_part_meta",
        "Set a part's navigation metadata: which folder it is filed under and "
        "its tags. Folders and tags are project organization stored in the "
        "manifest — they never move the part's script (always parts/<id>.py) "
        "and never invalidate its geometry cache. Omit a field to leave it "
        "unchanged; this is one undoable step. "
        f"folder: {_FOLDER_DOC} tags: {_TAGS_DOC}",
        schema(
            {
                "project": {"type": "string", "description": "Project name"},
                "part_id": {"type": "string", "description": "Part id"},
                # A plain "string" type, not ["string", "null"]: the registry's
                # validator looks its type up in a dict (`_TYPE_CHECKS.get`),
                # and a list is unhashable — a type list raises TypeError
                # inside validation rather than being accepted. `null` still
                # reaches the handler (the validator skips the type check for
                # None on an optional argument) and means root, and so does "".
                "folder": {"type": "string", "description": _FOLDER_DOC},
                "tags": {"type": "array", "items": {"type": "string"},
                         "description": _TAGS_DOC},
            },
            ["project", "part_id"],
        ),
        set_part_meta,
    ))

    # --- search (slice 2) ---

    # Function-local, like the thumbnail slice below: this pack's three slices
    # were written concurrently against one file, and a module-header import is
    # the one line all three would have raced on.
    from .search import GRAMMAR, MAX_LIMIT, Engine

    # One engine per service, installed here because this pack is the only
    # thing that loads for every service (the route pack and the tool both read
    # `service.search`). Rebuilding the registry — which the tests do
    # constantly and a hosted server does once — must NOT throw away a warm
    # memo, so an engine already bound to THIS service is kept. The identity
    # check matters: a service copied into an ephemeral check run is a
    # different store with different files, and inheriting its predecessor's
    # memo would answer questions about the wrong tree.
    engine = getattr(service, "search", None)
    if not isinstance(engine, Engine) or engine.service is not service:
        service.search = Engine(service)

    def search_parts(project: str, query: str, filters: dict | None = None,
                     limit: int | None = None) -> dict:
        """Find parts by text and structured filters (FR3).

        A pure read: it scans the manifest and the scripts the service already
        owns, makes no kernel call, and never builds anything — a part that has
        never been built is a result with ``state: "unbuilt"``, not an error
        and not a reason to start a build.
        """
        return service.search.search(project, query, filters=filters,
                                     limit=limit)

    registry.register(Tool(
        "search_parts",
        "Search a project's parts by text and structured filters — the way to "
        "find something in a project too large to list. Free text searches "
        "ids, labels, tags, material ids AND script text; field terms filter "
        "on folder, tags, material, build state and kind. Results are ranked "
        "(name > tag > material > script text) and each row says what it "
        "matched on, plus a snippet when the script text is the only thing "
        "that matched (a filter term does not count). This "
        "reads the manifest and the scripts: it makes no kernel call, builds "
        "nothing, and changes nothing. "
        f"{GRAMMAR} "
        "filters: an optional object ANDed with the query — "
        "{tag: str|[str], material, state, kind, folder} — so structured "
        "filtering needs no quoting; a list ANDs (tag: ['a','b'] means both). "
        f"limit: 1..{MAX_LIMIT}, default 50; `total` counts every match.",
        schema(
            {
                "project": {"type": "string", "description": "Project name"},
                "query": {"type": "string",
                          "description": f"The query. {GRAMMAR}"},
                # "object", never a type list: the registry validator looks the
                # type up in a dict, and a list is unhashable (the `folder`
                # note above). An absent/None filters means "no filters".
                "filters": {"type": "object",
                            "description": "Optional {tag, material, state, "
                                           "kind, folder} object ANDed with "
                                           "the query."},
                "limit": {"type": "integer",
                          "description": f"Max rows to return, 1..{MAX_LIMIT} "
                                         "(default 50). `total` is the full "
                                         "count either way."},
            },
            ["project", "query"],
        ),
        search_parts,
    ))

    # --- thumbnails (slice 3) ---

    # No tool: a thumbnail is a browser asset, not an agent verb (an agent that
    # wants to SEE geometry has `render_view`). All this pack does is make the
    # pre-warm object exist.
    #
    # It is **constructed here and started nowhere.** `build_registry` runs in
    # `checks.py`, `packages/gate.py`, `bench/cli.py`'s per-task loop,
    # `share_build.py` and the MCP/CLI entry points — none of which is an HTTP
    # server, and each of which would otherwise leave an orphaned daemon thread
    # and bus subscriber behind. Worse, a late render from a check's warmer
    # calls `_atomic_write`, which mkdirs — re-creating an `agentcad-check-*`
    # cell the CLI had already deleted. The thread is started by
    # `routes_thumbnails.build_router` instead: route packs are mounted only by
    # `create_app`, so exactly the process that serves `thumb.png` runs it.
    #
    # Reuse rather than replace (the `search.Engine` pattern): `build_registry`
    # is called more than once on one service in several of those callers, and
    # a fresh object each time would strand the running thread of the last one.
    from .thumbnails import ThumbnailWarmer

    warmer = getattr(service, "thumbnails", None)
    if not isinstance(warmer, ThumbnailWarmer) or warmer.service is not service:
        service.thumbnails = ThumbnailWarmer(service)

    # --- bulk (slice 4) ---

    # Function-local, like the two blocks above (this file's three slices were
    # written concurrently). `BulkExecutor` is stateless — a thin binding of
    # `service` to the six ops — so it is constructed per call rather than
    # installed on the service the way `search`/`thumbnails` are: nothing else
    # holds a reference to it, and one less mutable attribute on the service is
    # one less thing a rebuilt registry can strand.
    from .navigation import MAX_BULK, MAX_BULK_EXPORT, OPS, BulkExecutor

    def bulk_part_op(project: str, part_ids: list, op: str,
                     args: dict | None = None) -> dict:
        """Run one operation over many parts as ONE undoable step (FR5).

        Partial success is per-item **validity** only: an unknown id or a part
        whose tags would go over the cap is a ``results`` row with ``ok:
        false``, and the rest of the selection still lands. A refusal of the
        *gesture* — an unknown op or material, a malformed folder or tag, a
        selection over the bound, or a part another human is holding — is an
        error envelope with nothing written.
        """
        return BulkExecutor(service).run(project, part_ids, op, args)

    registry.register(Tool(
        "bulk_part_op",
        "Apply one operation to many parts in a single undoable step — the "
        "way to re-material, re-file, tag, export or delete a selection "
        "without spending one undo entry per part. "
        f"op: one of {', '.join(OPS)}. "
        "args by op: material {material: '<id>'}; tag/untag {tags: [..]} "
        "(added to / removed from each part's existing tags, normalized the "
        "same way set_part_meta normalizes them); folder {folder: str|null} "
        "(null or \"\" files the parts at the root — the key is REQUIRED); "
        "export {format: 'step'|'stl'|'3mf', tolerance?: number}; "
        "delete {force?: bool} (without force a part an assembly instance "
        "still uses is refused per item and names the instances in "
        "error.details.instances; with force those instances are removed in "
        "the same write). "
        f"part_ids: 1..{MAX_BULK} ids, de-duplicated keeping order "
        f"(export is capped at {MAX_BULK_EXPORT} — each one is a kernel round "
        "trip). "
        "Returns {op, ok (every row ok), applied (parts the write touched), "
        "results: [{id, ok, error?, ...}], undo_label}. The five manifest ops "
        "are ONE manifest write, ONE project_changed and ONE undo step "
        "labelled with undo_label; export changes no authored state and has "
        "no undo entry. A part another client has claimed refuses the whole "
        "call — partial success covers per-item validity, not a colleague.",
        schema(
            {
                "project": {"type": "string", "description": "Project name"},
                "part_ids": {
                    "type": "array", "items": {"type": "string"},
                    "description": f"1..{MAX_BULK} part ids "
                                   f"({MAX_BULK_EXPORT} for export); "
                                   "duplicates are collapsed, order kept.",
                },
                "op": {"type": "string",
                       "description": f"One of: {', '.join(OPS)}."},
                # "object", never a JSON type LIST: the registry validator looks
                # the type up in a dict and a list is unhashable (see `folder`
                # above). An absent/None args is the empty object.
                "args": {"type": "object",
                         "description": "Operation arguments; see the "
                                        "description for the shape per op."},
            },
            ["project", "part_ids", "op"],
        ),
        bulk_part_op,
    ))
