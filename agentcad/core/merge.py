"""Branch merge orchestration: three-way merge, staging, validation, commit.

A merge here is a real git merge — two parents, native refs, readable by any
git client — but the *content* merge is ours: part scripts go through
``git merge-tree --write-tree`` (textual, conflict markers), while
``project.json`` is **always** re-merged by ``manifest_merge`` at CAD key
granularity, whether or not git thought it merged cleanly. A line-wise merge of
a JSON manifest is either garbage or, worse, a clean result nobody authored.

Not everything tracked is text: ``imports/`` holds STL/STEP payloads. Those are
read, staged and resolved as raw BYTES — a binary conflict carries sizes and
digests instead of sides, takes ``ours``/``theirs``/``base`` and nothing else,
and never sees a conflict marker. Taking a side that does not exist (the branch
deleted the path) removes the path from the merged tree.

``ours`` is the TARGET branch (what you merge into) and ``theirs`` the SOURCE,
matching ``git merge <source>``. Conflicts are *returned* as a
``{"error": {"type": "merge_conflict", …}}`` payload, never raised: the tool
registry derives error types from exception class names, and FR7 fixes the
type string. Nothing outside ``.history/agentcad/`` is touched while conflicts
are outstanding — the merge is staged in a detached worktree until it is
resolved (``resolve_merge``) or discarded (``merge_abort``).

Before a merge lands — fast-forward included, FR9 has no exemption — the merged
tree is validated by the real kernel: changed parts rebuild, mates re-resolve,
and interference is re-checked. Validation runs through the *ordinary* service
methods with the branch resolver pinned to the merged worktree
(``BranchManager.pinned``), so the mesh cache, the kernel pool and the mates
resolver are reused verbatim — a part already built on either branch is a cache
hit. Failures block by default; ``allow_invalid`` lands the merge with the
failures recorded in the commit message and returned to the caller.

Finalization is a ``commit-tree`` with both parents plus a compare-and-swap
``update-ref``: a commit that landed on the target while the merge was staged
fails the swap and surfaces as a ``conflict_error`` instead of silently
clobbering it. The CAS guards the REF; the target branch's **turn lock is held
across validation and finalization** to guard the BYTES, because the
``reset --hard`` that syncs the tree would otherwise destroy a write that
arrived while the kernel was busy. And because ``update-ref`` has to precede
that sync, a failed sync rolls the ref back under its own CAS — a branch never
ends up pointing at a commit its working tree never received.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from ..kernel.client import KernelError
from . import locks
from .history import HistoryError
from .manifest_merge import apply_choices, merge_manifests
from .model import AppError, ConflictError, NotFoundError, ValidationError
from .project import ProjectStore

# Interference pairs grow quadratically; above this the validation pass reports
# ``skipped: "instances"`` rather than spending minutes of boolean work.
MERGE_INTERFERENCE_MAX_INSTANCES = 40

# git merge-tree --write-tree (the plumbing this module is built on) landed in
# git 2.38 (2022). Branches and tags work on older git; only merging does not.
_MIN_GIT = (2, 38)

# Per side of a script conflict, and for the diff3 body, in the returned
# payload only — the staged file on disk always carries the full text.
_MAX_BODY_BYTES = 256 * 1024

# git's own binary heuristic: a NUL in the first 8000 bytes. We add "does not
# decode as UTF-8", because everything we merge textually is read as UTF-8.
_BINARY_SNIFF_BYTES = 8000

# A resolution whose chosen side does not exist: the path is REMOVED from the
# merged tree. "Take the side that deleted it" means delete it.
_DELETED = object()

_HINT = (
    'Resolve with resolve_merge {choices: {"<path|key>": {"take": '
    '"ours"|"theirs"|"base"}}}; scripts also accept {"content": "…"} and '
    "manifest keys {\"value\": …}, while binary files take a side only. "
    "Taking a side that deleted the file deletes it. ours = the target "
    "branch, theirs = the source. merge_abort discards the staged merge."
)


class MergeOrchestrator:
    """Stateless-per-call driver; the only durable state is
    ``.history/agentcad/merge.json`` plus its staged worktree."""

    def __init__(self, service) -> None:
        self.service = service
        self.store: ProjectStore = service.store
        self.history = service.history
        self.branches = service.branches
        self._lock = threading.RLock()

    # ------------------------------------------------------------ public api

    def merge(self, proj: str, source: str, target: str | None = None,
              allow_invalid: bool = False) -> dict:
        with self._lock:
            canonical = self.branches._ensure_history(proj)
            self._require_merge_tree(canonical)
            source = self._branch(proj, canonical, source, "source")
            target = self._branch(
                proj, canonical, target or self.branches.current(proj), "target"
            )
            if source == target:
                raise ValidationError(
                    f"cannot merge branch {source!r} into itself"
                )
            state = self._load_state(canonical)
            resolved: dict = {}
            if state is not None:
                if (state["source"], state["target"]) != (source, target):
                    raise ConflictError(
                        f"a merge of {state['source']!r} into "
                        f"{state['target']!r} is already staged; complete it "
                        "with resolve_merge or discard it with merge_abort",
                        {"merge_id": state["id"], "source": state["source"],
                         "target": state["target"]},
                    )
                if self._heads_moved(canonical, state):
                    # The staged tree is stale. Re-merging from the current
                    # heads would silently drop the resolutions recorded
                    # against the old ones, so say so and let the caller decide
                    # (merge_abort, then merge again).
                    recorded = len(state.get("resolved") or {})
                    raise ConflictError(
                        f"branch {state['target']!r} or {state['source']!r} "
                        "moved since this merge was staged; its "
                        f"{recorded} recorded resolution"
                        f"{'' if recorded == 1 else 's'} no longer apply — "
                        "discard it with merge_abort and merge again",
                        {"merge_id": state["id"], "source": state["source"],
                         "target": state["target"], "resolved": recorded},
                    )
                resolved = dict(state.get("resolved") or {})
            return self._merge(proj, canonical, source, target,
                               allow_invalid=allow_invalid, resolved=resolved,
                               state=state)

    def resolve(self, proj: str, choices: dict) -> dict:
        with self._lock:
            canonical = self.branches._ensure_history(proj)
            state = self._load_state(canonical)
            if state is None:
                raise ConflictError(
                    "no merge is staged for this project; start one with "
                    "merge_branch"
                )
            if not isinstance(choices, dict):
                raise ValidationError(
                    "choices must be an object of {path-or-key: choice}"
                )
            outstanding = {self._conflict_key(c) for c in state["conflicts"]}
            unknown = sorted(set(choices) - outstanding)
            if unknown:
                # Validate before anything is re-staged: a bad call must leave
                # the staged merge exactly as it was.
                raise ValidationError(
                    f"no outstanding conflict at {unknown[0]!r}",
                    {"outstanding": sorted(outstanding)},
                )
            if self._heads_moved(canonical, state):
                raise ConflictError(
                    f"branch {state['target']!r} or {state['source']!r} moved "
                    "since this merge was staged; abort and merge again",
                    {"merge_id": state["id"]},
                )
            merged_choices = {**(state.get("resolved") or {}), **choices}
            return self._merge(
                proj, canonical, state["source"], state["target"],
                allow_invalid=bool(state.get("allow_invalid")),
                resolved=merged_choices, state=state,
            )

    def abort(self, proj: str) -> dict:
        with self._lock:
            canonical = self.store.canonical_path_of(proj)
            state = self._load_state(canonical)
            if state is None:
                return {"aborted": False, "merge": None}
            self._discard(canonical, state)
            return {"aborted": True, "merge_id": state["id"],
                    "source": state["source"], "target": state["target"]}

    def status(self, proj: str) -> dict:
        canonical = self.store.canonical_path_of(proj)
        state = self._load_state(canonical)
        return {"merge": None if state is None else self._summary(state)}

    # ------------------------------------------------------------ the merge

    def _merge(self, proj, canonical, source, target, *, allow_invalid,
               resolved, state) -> dict:
        target_tree = self.branches.tree_of(proj, target)
        source_tree = self.branches.tree_of(proj, source)
        self._check_turn(proj, target_tree)
        self._require_clean(target_tree, target)
        self._require_clean(source_tree, source)

        target_head = self.history.resolve_branch(canonical, target)
        source_head = self.history.resolve_branch(canonical, source)
        base = self._merge_base(canonical, target, source)
        if base == source_head:
            return {"already_up_to_date": True, "source": source,
                    "target": target, "commit": target_head,
                    "validation": None}
        if base == target_head:
            return self._fast_forward(
                proj, canonical, source, target, target_tree, source_tree,
                target_head, source_head, allow_invalid=allow_invalid,
            )

        tree_oid, stages = self._merge_tree(canonical, target_head, source_head)
        conflicts, bodies = self._file_conflicts(
            canonical, stages, target, source
        )
        merged, manifest_conflicts = merge_manifests(
            self._manifest_at(canonical, base, f"merge base {base[:8]}"),
            self._manifest_at(canonical, target_head, target),
            self._manifest_at(canonical, source_head, source),
        )
        conflicts.extend(manifest_conflicts)

        known = {self._conflict_key(c) for c in conflicts}
        unknown = sorted(set(resolved) - known)
        if unknown:
            raise ValidationError(
                f"no outstanding conflict at {unknown[0]!r}",
                {"outstanding": sorted(known)},
            )
        # Validate every choice BEFORE the staged worktree is rebuilt, so a
        # malformed resolution leaves the staged merge exactly as it was.
        for conflict in conflicts:
            if conflict["kind"] != "manifest" and conflict["path"] in resolved:
                self._resolved_content(
                    conflict, bodies, resolved[conflict["path"]]
                )
        merged, _outstanding_manifest = apply_choices(
            merged, manifest_conflicts,
            {k: v for k, v in resolved.items()
             if k in {c["key"] for c in manifest_conflicts}},
        )
        outstanding = [
            c for c in conflicts
            if self._conflict_key(c) not in resolved
        ]

        staged = self._stage(
            proj, canonical, target_head, tree_oid, merged, conflicts,
            bodies, resolved, state,
        )
        final_tree = staged["tree"]
        state = {
            "id": staged["id"],
            "source": source,
            "target": target,
            "base": base,
            "target_head": target_head,
            "source_head": source_head,
            "tree": final_tree,
            "dir": str(staged["dir"]),
            "by": locks.current_client_id(),
            "created": state["created"] if state else _now(),
            "allow_invalid": bool(allow_invalid),
            "conflicts": outstanding,
            "resolved": resolved,
        }
        self._save_state(canonical, state)
        if outstanding:
            return self._conflict_payload(state)

        with self._holding_target(proj, target_tree):
            report = self._validate(
                proj, canonical, staged["dir"], target_tree, target_head,
                final_tree, merged, target_ref=target,
            )
            if not report["ok"] and not allow_invalid:
                report["blocked"] = True
                raise ValidationError(
                    f"merge of {source!r} into {target!r} failed validation; "
                    "fix the source branch or re-run with allow_invalid: true",
                    {"merge_id": state["id"], "source": source,
                     "target": target, "validation": report},
                )
            report["blocked"] = False
            return self._finalize(proj, canonical, state, report, target_tree)

    def _fast_forward(self, proj, canonical, source, target, target_tree,
                      source_tree, target_head, source_head, *,
                      allow_invalid) -> dict:
        """The target has nothing of its own: move the ref and the tree.

        Validated all the same — FR9 has no fast-forward exemption. "A state
        that already existed" is not "a state that was validated": an edit
        persists before its rebuild fails, so a branch can carry a script that
        does not build, and a fast-forward used to land it with
        ``validation: null``. Nothing is staged here — the source branch's own
        worktree already *is* the merged tree.
        """
        merged = self._manifest_at(canonical, source_head, source)
        with self._holding_target(proj, target_tree):
            report = self._validate(
                proj, canonical, source_tree, target_tree, target_head,
                source_head, merged, target_ref=target,
            )
            if not report["ok"] and not allow_invalid:
                report["blocked"] = True
                raise ValidationError(
                    f"merge of {source!r} into {target!r} failed validation; "
                    "fix the source branch or re-run with allow_invalid: true",
                    {"source": source, "target": target, "fast_forward": True,
                     "validation": report},
                )
            report["blocked"] = False
            self._verify_clean(target_tree, target)
            cas = self._run(canonical, "update-ref", f"refs/heads/{target}",
                            source_head, target_head, check=False)
            if cas.returncode != 0:
                raise ConflictError(
                    f"branch {target!r} moved while merging; try again",
                    {"expected": target_head},
                )
            self._land(canonical, target, target_tree, source_head, target_head)
            with self.branches.pinned(proj, target_tree):
                # source_head's first parent is the previous commit on the
                # SOURCE branch — a state the target never had. Undo must
                # return the target to where it was before the fast-forward.
                self.service.undo_cursor.on_snapshot(
                    proj, source_head, f"merge {source} into {target}",
                    undo_to=target_head,
                )
                project = self.service.get_project(proj)
        self._publish(proj, source, target, source_head, report)
        return {"merged": True, "fast_forward": True, "source": source,
                "target": target, "commit": source_head,
                "previous": target_head, "conflicts_resolved": 0,
                "validation": report, "project": project}

    def _finalize(self, proj, canonical, state, report, target_tree) -> dict:
        source, target = state["source"], state["target"]
        # Immediately before the swap: the tree about to be 'reset --hard'
        # must still be the one that was validated. The CAS below guards the
        # REF; this guards the BYTES in the tree.
        self._verify_clean(target_tree, target)
        message = self._commit_message(state, report)
        commit = self._run(
            canonical, "commit-tree", state["tree"],
            "-p", state["target_head"], "-p", state["source_head"],
            "-m", message,
        ).stdout.strip()
        cas = self._run(canonical, "update-ref", f"refs/heads/{target}",
                        commit, state["target_head"], check=False)
        if cas.returncode != 0:
            raise ConflictError(
                f"branch {target!r} moved since this merge was staged; "
                "abort and merge again",
                {"merge_id": state["id"], "expected": state["target_head"]},
            )
        self._land(canonical, target, target_tree, commit,
                   state["target_head"])
        # Only now: both the ref AND the tree are the merge result.
        self._discard(canonical, state)
        with self.branches.pinned(proj, target_tree):
            self.service.undo_cursor.on_snapshot(
                proj, commit, f"merge {source} into {target}"
            )
            project = self.service.get_project(proj)
        self._publish(proj, source, target, commit, report)
        return {
            "merged": True,
            "fast_forward": False,
            "source": source,
            "target": target,
            "commit": commit,
            "parents": [state["target_head"], state["source_head"]],
            "conflicts_resolved": len(state.get("resolved") or {}),
            "validation": report,
            "project": project,
        }

    def _publish(self, proj, source, target, commit, report) -> None:
        self.service.bus.publish(
            {"type": "project_changed", "project": proj, "reason": "merge"}
        )
        self.service.bus.publish(
            {"type": "merge_completed", "project": proj, "source": source,
             "target": target, "commit": commit, "validation": report}
        )

    @staticmethod
    def _commit_message(state, report) -> str:
        lines = [
            f"merge {state['source']} into {state['target']}",
            "",
            f"Merged-by: {locks.current_client_id()}",
            f"Conflicts-resolved: {len(state.get('resolved') or {})}",
        ]
        if report["ok"]:
            lines.append("Validation: ok")
        else:
            lines.append(f"Validation: FAILED (allow_invalid) — {_summarize(report)}")
        return "\n".join(lines) + "\n"

    # --------------------------------------------------------------- staging

    def _stage(self, proj, canonical, target_head, tree_oid, merged, conflicts,
               bodies, resolved, state) -> dict:
        """Materialize the merged tree in a detached worktree under
        ``.history/agentcad/``. Nothing outside that directory is written, so a
        conflicted merge is never partially applied (FR7)."""
        if state is not None:
            self._discard(canonical, state, keep_state=True)
        merge_id = uuid.uuid4().hex[:8]
        staged = self._agentcad_dir(canonical) / f"merge-{merge_id}"
        self._run(canonical, "worktree", "prune", check=False)
        if staged.exists():
            shutil.rmtree(staged, ignore_errors=True)
        staged.parent.mkdir(parents=True, exist_ok=True)
        result = self._run(canonical, "worktree", "add", "--detach",
                           str(staged), target_head, check=False)
        if result.returncode != 0:
            raise ValidationError(
                "could not stage the merge: "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )
        self._run(staged, "read-tree", "-u", "--reset", tree_oid)

        # The manifest is ours, not git's: overwrite whatever the tree merge
        # produced with the structure-aware result.
        ProjectStore._atomic_write(
            staged / "project.json", json.dumps(merged, indent=2).encode()
        )
        for conflict in conflicts:
            if conflict["kind"] == "manifest":
                continue
            path = staged / conflict["path"]
            choice = resolved.get(conflict["path"])
            if choice is not None:
                content = self._resolved_content(conflict, bodies, choice)
            else:
                # Unresolved: the full diff3 body for text, and whatever the
                # tree merge produced for binary (which never gets markers).
                content = (bodies.get(conflict["path"]) or {}).get("merged")
            if content is _DELETED:
                # 'git add -A' below turns the removal into a tree deletion.
                path.unlink(missing_ok=True)
                continue
            if content is None:
                continue
            ProjectStore._atomic_write(path, content)

        self._run(staged, "add", "-A")
        tree = self._run(staged, "write-tree").stdout.strip()
        return {"id": merge_id, "dir": staged, "tree": tree}

    @staticmethod
    def _resolved_content(conflict, bodies, choice):
        """Bytes to write for a resolved path, or ``_DELETED``.

        Bytes, never text: a resolution may name a side of a binary file
        (``imports/*.stl``), which must be copied through verbatim.
        """
        path = conflict["path"]
        is_binary = conflict["kind"] == "binary"
        if not isinstance(choice, dict):
            raise ValidationError(
                f"choice for {path!r} must be an object "
                "{'take': 'ours'|'theirs'|'base'} or {'content': '…'}"
            )
        if "content" in choice:
            if is_binary:
                raise ValidationError(
                    f"{path!r} is a binary file; hand-written content is not "
                    "accepted — resolve it with "
                    "{'take': 'ours'|'theirs'|'base'}",
                    {"path": path, "kind": "binary"},
                )
            content = choice["content"]
            if not isinstance(content, str):
                raise ValidationError(
                    f"content for {path!r} must be a string"
                )
            return content.encode()
        side = choice.get("take")
        if side not in ("ours", "theirs", "base"):
            raise ValidationError(
                f"choice for {path!r} must be "
                "{'take': 'ours'|'theirs'|'base'}"
                + ("" if is_binary else " or {'content': '…'}")
            )
        sides = (bodies.get(path) or {}).get("sides") or {}
        data = sides.get(side)
        if data is not None:
            return data
        if side == "base":
            # No stage-1 blob: both branches ADDED this path, so there is no
            # base to take. Taking it would leave the conflicted staged file.
            valid = sorted(name for name, body in sides.items()
                           if body is not None)
            raise ValidationError(
                f"conflict at {path!r} has no base version (both branches "
                f"added it); choose one of {valid}",
                {"path": path, "valid": valid},
            )
        # The chosen branch deleted this path: taking that side deletes it.
        return _DELETED

    # ------------------------------------------------------------ validation

    def _validate(self, proj, canonical, staged, target_tree, target_head,
                  final_tree, merged, *, target_ref=None) -> dict:
        report = {
            "ok": True, "blocked": False, "built": [], "failures": [],
            "integrity": [], "warnings": [],
            "interference": {"checked": 0, "new_pairs": [], "skipped": None},
        }
        changed = self._changed_parts(canonical, target_head, final_tree,
                                      merged, target_ref)
        with self.branches.pinned(proj, staged):
            for part_id in changed:
                cached = self._is_cached(proj, part_id)
                try:
                    built = self.service._ensure_built(proj, part_id)
                except KernelError as exc:
                    built = {"ok": False, "error": exc.to_payload()}
                except AppError as exc:
                    built = {"ok": False, "error": {
                        "type": type(exc).__name__.replace("Error", "").lower()
                                + "_error",
                        "message": exc.message, "details": exc.details}}
                if built["ok"]:
                    report["built"].append({"part": part_id, "cached": cached})
                else:
                    report["failures"].append(
                        {"part": part_id, "error": built["error"]}
                    )
            # Shape first: an instance of a missing part is meaningless if
            # the document is not a project at all (FR9 backstop for a
            # manifest that was hand-edited or half-deleted upstream).
            report["integrity"] = _manifest_shape(merged) + _integrity(merged)
            if not report["integrity"] and not report["failures"]:
                try:
                    self.service._resolved_instances(proj)
                except (AppError, KernelError) as exc:
                    report["integrity"].append(
                        {"kind": "mate_error",
                         "message": getattr(exc, "message", str(exc))}
                    )
            self._check_interference(proj, staged, target_tree, merged, report)
        report["ok"] = not (report["failures"] or report["integrity"]
                            or report["interference"]["new_pairs"])
        return report

    def _check_interference(self, proj, staged, target_tree, merged, report) -> None:
        info = report["interference"]
        instances = (merged.get("assembly") or {}).get("instances") or []
        info["checked"] = len(instances)
        if report["failures"] or report["integrity"]:
            info["skipped"] = "validation"
            return
        if len(instances) > MERGE_INTERFERENCE_MAX_INSTANCES:
            # An accepted cap (pairs grow quadratically) — but never a silent
            # one: an ok:true report that skipped a check has to say so.
            info["skipped"] = "instances"
            report["warnings"].append(
                f"interference skipped: {len(instances)} instances > "
                f"{MERGE_INTERFERENCE_MAX_INSTANCES}; check the merged "
                "assembly by hand"
            )
            return
        if len(instances) < 2:
            info["skipped"] = "instances"
            return
        after = self.service.check_interference(proj)
        info["checked"] = after.get("checked", len(instances))
        if not after["pairs"]:
            return
        # Only NEWLY INTRODUCED overlaps block: a project that already
        # interferes must stay mergeable (the AC4 wording is "would introduce").
        with self.branches.pinned(proj, target_tree):
            try:
                before = self.service.check_interference(proj)["pairs"]
            except (AppError, KernelError):
                before = []
        seen = {frozenset((p["a"], p["b"])) for p in before}
        info["new_pairs"] = [
            p for p in after["pairs"] if frozenset((p["a"], p["b"])) not in seen
        ]

    def _changed_parts(self, canonical, target_head, final_tree, merged,
                       target_ref=None) -> list[str]:
        """Parts the merge changes relative to the target: script bytes from
        git, manifest entries (params, material, …) from the driver's output."""
        result = self._run(canonical, "diff", "--name-only", target_head,
                           final_tree, check=False)
        changed = {
            path[len("parts/"):-len(".py")]
            for path in result.stdout.split()
            if path.startswith("parts/") and path.endswith(".py")
        }
        before = {e["id"]: e for e in self._manifest_at(
            canonical, target_head, target_ref).get("parts", [])}
        present = set()
        for entry in merged.get("parts", []):
            present.add(entry["id"])
            if entry != before.get(entry["id"]):
                changed.add(entry["id"])
        return sorted(changed & present)

    def _is_cached(self, proj, part_id) -> bool:
        try:
            record = self.store.get_part(proj, part_id)
            key = self.service._cache_key_for(proj, record)
        except (AppError, OSError):
            return False
        return (self.store.cache_dir(proj) / f"{key}.acm").is_file()

    # -------------------------------------------------------------- conflicts

    def _file_conflicts(self, canonical, stages, target, source):
        """Conflicted non-manifest paths as payload entries, plus each side's
        raw bytes (for resolution by ``take``).

        Sides are read as BYTES. A path any side of which is binary gets a
        ``binary`` conflict: no diff3 body, no per-side text, and only
        ``take`` resolves it — decoding an STL to build conflict markers would
        commit UTF-8 replacement garbage as a "successful" merge.
        """
        conflicts, bodies = [], {}
        for path in sorted(stages):
            if path == "project.json":
                continue  # always re-merged by the manifest driver
            entry = stages[path]
            sides = {
                "base": self._blob_bytes(canonical, entry.get(1)),
                "ours": self._blob_bytes(canonical, entry.get(2)),
                "theirs": self._blob_bytes(canonical, entry.get(3)),
            }
            if any(_is_binary(body) for body in sides.values()):
                bodies[path] = {"sides": sides, "merged": None}
                conflicts.append(_binary_conflict(path, sides))
                continue
            text = {name: (None if body is None else body.decode("utf-8"))
                    for name, body in sides.items()}
            marked = self._marked(text, target, source)
            bodies[path] = {"sides": sides, "merged": marked.encode()}
            is_script = path.startswith("parts/") and path.endswith(".py")
            conflict = {
                "kind": "script" if is_script else "file",
                "path": path,
                "merged": marked,
                "truncated": False,
            }
            if is_script:
                conflict["part"] = path[len("parts/"):-len(".py")]
            if len(marked.encode()) > _MAX_BODY_BYTES:
                # Elided from the payload only: the staged file on disk still
                # carries the full diff3 text.
                conflict["merged"] = None
                conflict["truncated"] = True
            for side in ("base", "ours", "theirs"):
                body = sides[side]
                if body is not None and len(body) > _MAX_BODY_BYTES:
                    conflict["truncated"] = True
                else:
                    conflict[side] = text[side]
            conflicts.append(conflict)
        return conflicts, bodies

    def _marked(self, sides, target, source) -> str:
        """Conflict-marked text with --diff3 labels, so ours/theirs are named
        by their branches and the base section is visible."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            files = {}
            for side in ("ours", "base", "theirs"):
                files[side] = root / side
                files[side].write_text(sides[side] or "", encoding="utf-8")
            result = self._run(
                root, "merge-file", "--diff3",
                "-L", target, "-L", "base", "-L", source, "-p",
                str(files["ours"]), str(files["base"]), str(files["theirs"]),
                check=False,
            )
            return result.stdout

    def _conflict_payload(self, state) -> dict:
        conflicts = state["conflicts"]
        return {"error": {
            "type": "merge_conflict",
            "message": (
                f"merge of {state['source']!r} into {state['target']!r} has "
                f"{len(conflicts)} conflict{'s' if len(conflicts) != 1 else ''}"
            ),
            "details": {
                "merge_id": state["id"],
                "source": state["source"],
                "target": state["target"],
                "base": state["base"],
                "outstanding": len(conflicts),
                "conflicts": conflicts,
                "hint": _HINT,
            },
        }}

    @staticmethod
    def _conflict_key(conflict) -> str:
        return conflict["key"] if conflict["kind"] == "manifest" else conflict["path"]

    @staticmethod
    def _summary(state) -> dict:
        return {
            "id": state["id"],
            "source": state["source"],
            "target": state["target"],
            "base": state["base"],
            "by": state.get("by"),
            "created": state.get("created"),
            "outstanding": len(state["conflicts"]),
            "conflicts": state["conflicts"],
            "resolved": sorted(state.get("resolved") or {}),
            "hint": _HINT,
        }

    # ----------------------------------------------------------- staged state

    def _agentcad_dir(self, canonical: Path) -> Path:
        return canonical / ".history" / "agentcad"

    def _state_file(self, canonical: Path) -> Path:
        return self._agentcad_dir(canonical) / "merge.json"

    def _load_state(self, canonical: Path) -> dict | None:
        try:
            state = json.loads(
                self._state_file(canonical).read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError):
            return None
        return state if isinstance(state, dict) and state.get("id") else None

    def _save_state(self, canonical: Path, state: dict) -> None:
        ProjectStore._atomic_write(
            self._state_file(canonical), json.dumps(state, indent=2).encode()
        )

    def _discard(self, canonical: Path, state: dict, *,
                 keep_state: bool = False) -> None:
        staged = Path(state["dir"])
        if staged.exists():
            self._run(canonical, "worktree", "remove", "--force", str(staged),
                      check=False)
            if staged.exists():
                shutil.rmtree(staged, ignore_errors=True)
        self._run(canonical, "worktree", "prune", check=False)
        # The staged tree was its own lock key, so the validation pass left
        # build state keyed on a directory that no longer exists.
        self.service._forget_status(str(staged))
        if not keep_state:
            self._state_file(canonical).unlink(missing_ok=True)

    def _heads_moved(self, canonical: Path, state: dict) -> bool:
        return (
            self.history.resolve_branch(canonical, state["target"])
            != state["target_head"]
            or self.history.resolve_branch(canonical, state["source"])
            != state["source_head"]
        )

    # -------------------------------------------------------------- plumbing

    def _run(self, path: Path, *args: str, check: bool = True):
        try:
            return self.history._run(path, *args, check=check)
        except HistoryError as exc:
            raise ValidationError(str(exc)) from exc

    def _git_version(self, path: Path) -> tuple[int, int]:
        result = self._run(path, "version", check=False)
        match = re.search(r"(\d+)\.(\d+)", result.stdout)
        return (int(match.group(1)), int(match.group(2))) if match else (0, 0)

    def _require_merge_tree(self, canonical: Path) -> None:
        version = self._git_version(canonical)
        if version < _MIN_GIT:
            raise ValidationError(
                "merging requires git 2.38 or newer (for "
                "'git merge-tree --write-tree'); this server has "
                f"{version[0]}.{version[1]}. Branches, tags and history work "
                "on older git.",
                {"required": "2.38", "found": f"{version[0]}.{version[1]}"},
            )

    def _branch(self, proj: str, canonical: Path, name, role: str) -> str:
        if not isinstance(name, str) or not name:
            raise ValidationError(f"{role} branch must be a branch name")
        if name not in {b["name"] for b in self.history.branches(canonical)}:
            raise NotFoundError(f"branch {name!r} not found")
        return name

    def _merge_base(self, canonical: Path, target: str, source: str) -> str:
        # refs/heads/…, not the bare names: a tag shadowing a branch would
        # otherwise pick the tag's commit as one side of the base.
        result = self._run(canonical, "merge-base", f"refs/heads/{target}",
                           f"refs/heads/{source}", check=False)
        base = result.stdout.strip()
        if result.returncode != 0 or not base:
            raise ValidationError(
                f"branches {target!r} and {source!r} have unrelated histories"
            )
        return base

    def _merge_tree(self, canonical: Path, ours: str, theirs: str):
        """(merged tree oid, {path: {stage: oid}}) from
        ``git merge-tree --write-tree -z``: exit 0 clean, 1 conflicts, >1 error.
        Only the tree oid and the conflicted-file section are parsed; the
        trailing informational messages are opaque by design."""
        result = self._run(canonical, "merge-tree", "--write-tree", "-z",
                           ours, theirs, check=False)
        if result.returncode > 1:
            raise ValidationError(
                "git merge-tree failed: "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )
        return _parse_merge_tree(result.stdout)

    def _blob_bytes(self, canonical: Path, oid: str | None) -> bytes | None:
        """A blob's exact bytes, or None when the stage is absent."""
        if not oid:
            return None
        try:
            result = self.history._run_bytes(
                canonical, "cat-file", "blob", oid, check=False
            )
        except HistoryError as exc:
            raise ValidationError(str(exc)) from exc
        return result.stdout if result.returncode == 0 else None

    def _blob(self, canonical: Path, oid: str | None) -> str | None:
        """A blob decoded as UTF-8; None for an absent stage or binary
        content (which must never round-trip through ``str``)."""
        data = self._blob_bytes(canonical, oid)
        if data is None:
            return None
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            return None

    def _manifest_at(self, canonical: Path, commit: str,
                     ref: str | None = None) -> dict:
        """The manifest at a commit, or ``{}`` when the commit has none.

        ``{}`` means "no project.json at all" — a legitimate orphan or empty
        base — and NEVER "it did not parse". A manifest that exists but is
        unreadable used to read as {} too, which the key-wise merge takes as
        *this side deleted everything*: it merges clean, passes validation,
        and blows up on the next get_project. So it is a refusal, naming the
        ref and the file.
        """
        result = self._run(canonical, "cat-file", "blob",
                           f"{commit}:project.json", check=False)
        if result.returncode != 0:
            return {}
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            problem = str(exc)
        else:
            if isinstance(data, dict):
                return data
            problem = f"its top level is a {type(data).__name__}, not an object"
        where = ref or commit[:8]
        raise ValidationError(
            f"project.json at {where!r} is not a readable manifest "
            f"({problem}); a merge cannot tell an unreadable manifest from a "
            "deleted one — fix or restore it before merging",
            {"ref": where, "commit": commit, "file": "project.json"},
        )

    def _turn_key(self, proj: str, target_tree: Path) -> str:
        """A merge writes the TARGET branch, so it answers to the target's turn
        lock — not the caller's branch. Pinning the resolver is how we ask the
        store for the target's key under its own rules."""
        with self.branches.pinned(proj, target_tree):
            return self.store.lock_key(proj)

    def _check_turn(self, proj: str, target_tree: Path) -> None:
        turnlock = getattr(self.service, "turnlock", None)
        if turnlock is None:
            return
        turnlock.check(self._turn_key(proj, target_tree),
                       locks.current_client_id())

    @contextmanager
    def _holding_target(self, proj: str, target_tree: Path):
        """HOLD the target branch's turn across validation and finalization.

        Checking the turn once, at the top of a merge, guarantees nothing: the
        validation pass takes seconds, and any write that lands on the target
        tree in the meantime is destroyed by the ``reset --hard`` that
        finalizes the merge — the compare-and-swap only notices a moved REF,
        not changed bytes. Holding the turn makes a competing writer fail with
        the ordinary "project is locked by …" conflict instead.

        A caller that already holds the turn keeps it afterwards; one that did
        not gets it released again.

        The hold carries the ordinary TTL, so a validation pass that outlives
        it frees the turn and someone else may legitimately take it. Releasing
        then raises — over a body that has already landed its merge (ref moved,
        tree synced, event published). A lock that is no longer ours is not
        this merge's problem: the release cannot be the thing that reports a
        completed merge as a conflict.
        """
        turnlock = getattr(self.service, "turnlock", None)
        if turnlock is None:
            yield
            return
        key = self._turn_key(proj, target_tree)
        holder = locks.current_client_id()
        existing = turnlock.get(key)
        turnlock.acquire(key, holder)   # ConflictError when someone else has it
        try:
            yield
        finally:
            if existing is None or existing.get("holder") != holder:
                try:
                    turnlock.release(key, holder)
                except ConflictError:
                    pass  # our hold expired and another client took the turn

    def _verify_clean(self, tree: Path, branch: str) -> None:
        """Assert (never snapshot) that a tree is unmodified. Used at the very
        end of a merge, where a snapshot would move the ref out from under the
        compare-and-swap."""
        result = self._run(tree, "status", "--porcelain", check=False)
        if result.returncode != 0 or result.stdout.strip():
            raise ConflictError(
                f"branch {branch!r} was written while this merge was being "
                "validated; nothing landed — merge again",
                {"branch": branch, "status": result.stdout.strip()[:2000]},
            )

    def _land(self, canonical: Path, branch: str, tree: Path, commit: str,
              previous: str) -> None:
        """Bring ``tree`` to a ref that has already moved — and put the ref
        back when it cannot.

        ``update-ref`` has to precede ``reset --hard`` (the CAS is the whole
        concurrency story), which means a failed reset leaves the branch
        pointing at a commit its working tree never received: the caller sees
        an error, but merge_abort can no longer restore anything. Roll the ref
        back under its own CAS, keep the staged merge, and report the state
        that really holds.
        """
        try:
            self._sync_tree(tree, commit)
        except AppError as exc:
            rollback = self._run(canonical, "update-ref",
                                 f"refs/heads/{branch}", previous, commit,
                                 check=False)
            restored = rollback.returncode == 0
            tail = (
                "; the branch was left where it was and the merge is still "
                "staged — retry or abort it"
                if restored else
                f"; WARNING: {branch!r} now points at {commit[:8]} but its "
                "working tree does not — restore it by hand"
            )
            raise ValidationError(
                f"branch {branch!r} could not be moved to {commit[:8]}: "
                f"{getattr(exc, 'message', exc)}{tail}",
                {"branch": branch, "commit": commit, "previous": previous,
                 "ref_restored": restored},
            ) from exc

    def _require_clean(self, tree: Path, branch: str) -> None:
        """Every mutation snapshots itself, so a tree that is still dirty after
        one more snapshot has something git cannot commit."""
        self.history.snapshot(tree, f"checkpoint before merging {branch}")
        result = self._run(tree, "status", "--porcelain", check=False)
        if result.stdout.strip():
            raise ConflictError(
                f"branch {branch!r} has uncommitted changes; snapshot or "
                "revert them before merging",
                {"branch": branch, "status": result.stdout.strip()[:2000]},
            )

    def _sync_tree(self, tree: Path, commit: str) -> None:
        result = self._run(tree, "reset", "--hard", commit, check=False)
        if result.returncode != 0:
            raise ValidationError(
                f"merge commit {commit[:8]} landed but its working tree could "
                f"not be updated: {result.stderr.strip()}"
            )


# ----------------------------------------------------------------- helpers


def _is_binary(data: bytes | None) -> bool:
    """git's heuristic (a NUL in the first 8000 bytes) plus "not UTF-8"."""
    if not data:
        return False
    if b"\0" in data[:_BINARY_SNIFF_BYTES]:
        return True
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return True
    return False


def _binary_conflict(path: str, sides: dict) -> dict:
    """A conflict carrying no text at all: size and digest per side, so a
    caller can tell the versions apart without ever seeing the bytes."""
    return {
        "kind": "binary",
        "path": path,
        "sides": {
            name: (None if body is None else {
                "bytes": len(body),
                "sha256": hashlib.sha256(body).hexdigest(),
            })
            for name, body in sides.items()
        },
        "truncated": True,
        "hint": ("binary file: resolve with {\"take\": \"ours\"|\"theirs\""
                 "|\"base\"}; hand-written content is not accepted"),
    }


def _parse_merge_tree(stdout: str):
    fields = stdout.split("\0")
    tree = fields[0].strip()
    stages: dict[str, dict[int, str]] = {}
    for field in fields[1:]:
        if not field:
            break  # end of the conflicted-file section; messages follow
        head, _, path = field.partition("\t")
        columns = head.split()
        if len(columns) != 3 or not path:
            continue
        _mode, oid, stage = columns
        stages.setdefault(path, {})[int(stage)] = oid
    return tree, stages


def _manifest_shape(manifest: dict) -> list[dict]:
    """Is the merged document still a project?

    ``get_project`` reads it seconds after the merge lands, so "loadable" is
    part of what the validation pass promises. A key-wise merge cannot produce
    most of this on its own — but a side whose manifest was hand-edited (or
    whose required key one side deleted) can.
    """
    if not isinstance(manifest, dict) or not manifest:
        return [{"kind": "manifest_invalid",
                 "message": "the merged project.json is empty"}]
    problems = []
    if not isinstance(manifest.get("name"), str) or not manifest["name"]:
        problems.append({"kind": "manifest_invalid",
                         "message": "the merged project.json has no 'name'"})
    parts = manifest.get("parts", [])
    if not isinstance(parts, list):
        problems.append({"kind": "manifest_invalid",
                         "message": "'parts' is not a list"})
    else:
        seen = set()
        for entry in parts:
            if not isinstance(entry, dict) or not isinstance(entry.get("id"), str):
                problems.append({"kind": "manifest_invalid",
                                 "message": "a part entry has no string 'id'"})
                break
            if entry["id"] in seen:
                problems.append({
                    "kind": "manifest_invalid",
                    "message": f"duplicate part id {entry['id']!r}"})
                break
            seen.add(entry["id"])
    assembly = manifest.get("assembly")
    if assembly is not None and not isinstance(assembly, dict):
        problems.append({"kind": "manifest_invalid",
                         "message": "'assembly' is not an object"})
    elif not isinstance((assembly or {}).get("instances", []), list):
        problems.append({"kind": "manifest_invalid",
                         "message": "'assembly.instances' is not a list"})
    return problems


def _integrity(manifest: dict) -> list[dict]:
    """Structural damage a clean key-wise merge can still do: an instance of a
    part the other side deleted, or a mate to an instance that is gone."""
    parts = {e.get("id") for e in manifest.get("parts", []) if isinstance(e, dict)}
    instances = (manifest.get("assembly") or {}).get("instances") or []
    ids = {i.get("id") for i in instances if isinstance(i, dict)}
    problems = []
    for inst in instances:
        if not isinstance(inst, dict):
            continue
        if inst.get("part") not in parts:
            problems.append({"kind": "dangling_instance",
                             "instance": inst.get("id"),
                             "part": inst.get("part")})
        mate = inst.get("mate")
        if isinstance(mate, dict) and mate.get("to_instance") not in ids:
            problems.append({"kind": "dangling_mate",
                             "instance": inst.get("id"),
                             "to_instance": mate.get("to_instance")})
    return problems


def _summarize(report: dict) -> str:
    bits = []
    for failure in report["failures"]:
        bits.append(f"build {failure['part']}")
    for problem in report["integrity"]:
        detail = problem.get("instance") or problem.get("message") or ""
        bits.append(f"{problem['kind']} {detail}".strip())
    for pair in report["interference"]["new_pairs"]:
        bits.append(
            f"interference {pair['a']}<->{pair['b']} "
            f"{pair.get('volume_mm3', 0.0):.1f} mm^3"
        )
    return "; ".join(bits) or "unknown"


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
