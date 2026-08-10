"""The review packet: what a proposed change actually does, as evidence.

A packet is one JSON document (``packet.json``) plus PNG and ACM assets beside
it under the proposal directory. It answers the reviewer's questions in the
order they are asked: which parts changed and how (script diff, PARAMS diff),
what that did to the numbers (metric deltas), what it did to the assembly, what
it looks like (a frame-matched before/after render pair), and how much material
was actually added or removed (kernel booleans).

**Both sides come from PRD-001's branch worktrees**, not from temp checkouts:
``branches.tree_of`` materializes a live tree per branch and ``branches.pinned``
points the store's resolver at one, which is exactly what the merge validation
pass does. Every measurement therefore runs through the *ordinary* service
methods — so the canonical, content-addressed ``.cache/`` makes every part whose
``(script, params, density, tolerance)`` tuple is unchanged free on both sides.
That is the whole cost model: the packet scales with the change, not with the
project.

**Nothing here imports OCP or build123d.** Geometry work goes to the kernel
over ``service.kernel.request`` (``geom_diff``, from ``kernel/handlers/diff.py``)
and rendering to the pure-numpy rasterizer in ``core/render.py``.

**Every per-part stage degrades on its own** (FR8): an unbuildable side yields
``build.<side>.ok: false`` with the structured script error and leaves that
side's metrics, render and geometric diff null; a boolean failure is
``geom_diff.available: false``; anything unexpected lands in ``errors`` and the
packet still returns ``ok: true``. ``ok`` is false only when no packet could be
produced at all. A red packet section is never an error response — the request
succeeded, the geometry did not.

**Head pinning** (the MVP half of FR9): the packet records both branch heads and
is served from disk while they hold. When either moves it is marked ``stale``
and regenerated on view; once the proposal merges, ``ProposalManager`` freezes
it and it is never regenerated again (FR12) — the evidence a decision was made
on has to keep saying what it said. That holds even when there was nothing to
freeze: a **terminal** proposal is never measured again, because the packet a
late viewer would get describes the merged target and whatever else has landed
since, under this proposal's name. Merging records the absence of a packet as a
frozen one that says so.
"""

from __future__ import annotations

import base64
import hashlib
import json
import threading
import time
import urllib.parse
from pathlib import Path

from ..kernel import acm
from ..kernel.client import KernelError
from . import locks
from .model import (
    AppError,
    ConflictError,
    NotFoundError,
    ValidationError,
    validate_id,
)
from .project import ProjectStore
from .proposals import TERMINAL
from .proposals import _now as proposal_now
from .render import VIEWS, render_acm

# Renders are smaller than ``render_view``'s 800x600 default: the rasterizer is
# pure Python, so resolution is the dominant cost knob in AC2's 10 s budget.
RENDER_VIEW = "iso"
RENDER_WIDTH = 640
RENDER_HEIGHT = 480

# The union of both bboxes is inflated so a silhouette never touches the frame
# edge; both sides render with the SAME frame, which is what makes the pair
# superimposable (FR6).
_FRAME_INFLATE = 0.02

_MAX_DIFF_BYTES = 256 * 1024  # merge.py's body cap, reused
_BINARY_SNIFF_BYTES = 8000
_DIFF_TIMEOUT_S = 300.0  # the build timeout: a boolean is a build-sized cost
_SIDES = ("old", "new")  # old = target = ours, new = source = theirs
_DIFF_KINDS = ("added", "removed")

# Parameter-spec fields compared field-by-field when a manifest stores a spec
# dict rather than an override value.
_PARAM_FIELDS = ("default", "min", "max", "type", "unit", "choices",
                 "description")

_MISSING = object()


def _norm(value) -> str:
    """Type-qualified JSON — the comparison ``manifest_merge._norm`` makes.

    The type prefix keeps ``6`` and ``6.0`` (and ``True`` and ``1``) distinct,
    which is exactly how ``service._normalize_param`` stores them: reporting a
    param as unchanged because ``6 == 6.0`` would hide a real edit.
    """
    if value is _MISSING:
        return "\0missing"
    return f"{type(value).__name__}:{json.dumps(value, sort_keys=True, default=repr)}"


# --------------------------------------------------------- the pure deltas


def changed_parts(old_manifest: dict, new_manifest: dict,
                  changed_scripts: set[str]) -> list[dict]:
    """Parts the proposal touches, classified.

    The union of (a) parts whose ``parts/<id>.py`` bytes differ between the two
    refs and (b) parts whose manifest entry differs — ``merge._changed_parts``'s
    rule, re-implemented rather than shared because that one works against a
    staged tree oid and the merge path must not move in this PRD.

    ``changed_by`` names the evidence: ``script`` (the file's bytes),
    ``params`` (the entry's overrides) and ``manifest`` (the rest of the entry:
    label, material, kind, …). A part in ``changed_scripts`` that no manifest
    knows about is not a part.
    """
    old = _parts_by_id(old_manifest)
    new = _parts_by_id(new_manifest)
    rows = []
    for part_id in sorted(set(old) | set(new)):
        old_entry, new_entry = old.get(part_id), new.get(part_id)
        changed_by = []
        if part_id in changed_scripts:
            changed_by.append("script")
        if old_entry is not None and new_entry is not None:
            if _norm(old_entry.get("params") or {}) \
                    != _norm(new_entry.get("params") or {}):
                changed_by.append("params")
            if _rest(old_entry) != _rest(new_entry):
                changed_by.append("manifest")
        if old_entry is None:
            change = "added"
        elif new_entry is None:
            change = "removed"
        elif changed_by:
            change = "modified"
        else:
            continue
        rows.append({"part": part_id, "change": change,
                     "changed_by": changed_by})
    return rows


def params_delta(old_entry: dict, new_entry: dict) -> dict:
    """``{added, removed, changed}`` for one part's parameters.

    A manifest stores parameter *overrides* (name -> scalar), so the usual
    ``changed`` row is ``{"name", "field": "value", "old", "new"}``. An entry
    whose parameter is a spec dict is compared field by field instead, one row
    per differing field, in the declaration order of ``_PARAM_FIELDS``.
    """
    old = (old_entry or {}).get("params") or {}
    new = (new_entry or {}).get("params") or {}
    added = [{"name": name, "value": new[name]}
             for name in sorted(set(new) - set(old))]
    removed = [{"name": name, "value": old[name]}
               for name in sorted(set(old) - set(new))]
    changed = []
    for name in sorted(set(old) & set(new)):
        before, after = old[name], new[name]
        if _norm(before) == _norm(after):
            continue
        if isinstance(before, dict) and isinstance(after, dict):
            for field in _PARAM_FIELDS:
                lhs = before.get(field, _MISSING)
                rhs = after.get(field, _MISSING)
                if _norm(lhs) == _norm(rhs):
                    continue
                changed.append({
                    "name": name, "field": field,
                    "old": None if lhs is _MISSING else lhs,
                    "new": None if rhs is _MISSING else rhs,
                })
        else:
            changed.append({"name": name, "field": "value",
                            "old": before, "new": after})
    return {"added": added, "removed": removed, "changed": changed}


def assembly_delta(old_asm: dict, new_asm: dict) -> dict:
    """Instance and mate changes between two ``get_assembly`` payloads.

    The payloads carry *resolved* transforms, so an instance whose mate anchor
    moved reports as moved — comparing authored positions would call that "no
    change".
    """
    old = _instances_by_id(old_asm)
    new = _instances_by_id(new_asm)
    added = [{"id": iid, "part": new[iid].get("part")}
             for iid in sorted(set(new) - set(old))]
    removed = [{"id": iid, "part": old[iid].get("part")}
               for iid in sorted(set(old) - set(new))]
    moved, mates = [], []
    for iid in sorted(set(old) & set(new)):
        before, after = old[iid], new[iid]
        if not _same_placement(before, after):
            moved.append({
                "id": iid, "part": after.get("part"),
                "old": _placement(before), "new": _placement(after),
            })
        if _norm(before.get("mate")) != _norm(after.get("mate")):
            mates.append({"id": iid, "old": before.get("mate"),
                          "new": after.get("mate")})
    mass = _scalar_delta((old_asm or {}).get("total_mass_g"),
                         (new_asm or {}).get("total_mass_g"))
    return {
        "changed": bool(added or removed or moved or mates or mass["delta"]),
        "instances_added": added,
        "instances_removed": removed,
        "instances_moved": moved,
        "mates_changed": mates,
        "total_mass_g": mass,
        "renders": None,  # assembly renders are on demand (proposal_render)
    }


def metric_delta(old: dict | None, new: dict | None) -> dict:
    """``{old, new, delta, pct}`` per scalar, plus a per-axis center-of-mass
    delta and both bounding boxes with their per-axis size change.

    A part present on one side only reports ``null`` for the absent side. A
    **mesh-kind reference** (imported STL) reports no center of mass at all:
    ``build_reference`` fills that field with the bbox CENTER, and presenting a
    delta of that as a mass property would be a lie.
    """
    old = old if isinstance(old, dict) else None
    new = new if isinstance(new, dict) else None
    delta = {
        name: _scalar_delta((old or {}).get(name), (new or {}).get(name))
        for name in ("volume_mm3", "mass_g", "area_mm2")
    }
    if (old or {}).get("mesh") or (new or {}).get("mesh"):
        delta["center_of_mass"] = None
    else:
        delta["center_of_mass"] = _vector_delta(
            (old or {}).get("center_of_mass"), (new or {}).get("center_of_mass")
        )
    delta["bbox"] = _bbox_delta((old or {}).get("bbox"), (new or {}).get("bbox"))
    return delta


def _parts_by_id(manifest: dict) -> dict[str, dict]:
    return {entry["id"]: entry
            for entry in (manifest or {}).get("parts") or []
            if isinstance(entry, dict) and isinstance(entry.get("id"), str)}


def _instances_by_id(assembly: dict) -> dict[str, dict]:
    return {entry["id"]: entry
            for entry in (assembly or {}).get("instances") or []
            if isinstance(entry, dict) and isinstance(entry.get("id"), str)}


def _rest(entry: dict) -> dict:
    return {key: value for key, value in entry.items() if key != "params"}


def _placement(instance: dict) -> dict:
    return {"position": list(instance.get("position") or [0.0, 0.0, 0.0]),
            "rotation_deg": list(instance.get("rotation_deg") or [0.0, 0.0, 0.0])}


def _same_placement(old: dict, new: dict) -> bool:
    before, after = _placement(old), _placement(new)
    for key in ("position", "rotation_deg"):
        lhs, rhs = before[key], after[key]
        if len(lhs) != len(rhs):
            return False
        # A resolved transform is float arithmetic: compare with a tolerance
        # far below anything a CAD edit means.
        if any(abs(float(a) - float(b)) > 1e-9 for a, b in zip(lhs, rhs)):
            return False
    return True


def _scalar_delta(old, new) -> dict:
    old = float(old) if isinstance(old, (int, float)) and not isinstance(old, bool) \
        else None
    new = float(new) if isinstance(new, (int, float)) and not isinstance(new, bool) \
        else None
    if old is None or new is None:
        return {"old": old, "new": new, "delta": None, "pct": None}
    delta = new - old
    # pct is null at zero rather than infinite: "+inf %" is not information.
    pct = None if old == 0 else round(delta / old * 100.0, 3)
    return {"old": old, "new": new, "delta": delta, "pct": pct}


def _vector_delta(old, new) -> dict:
    old = list(old) if isinstance(old, (list, tuple)) else None
    new = list(new) if isinstance(new, (list, tuple)) else None
    if old is None or new is None or len(old) != len(new):
        return {"old": old, "new": new, "delta": None}
    return {"old": old, "new": new,
            "delta": [float(b) - float(a) for a, b in zip(old, new)]}


def _bbox_delta(old, new) -> dict:
    old = old if isinstance(old, dict) else None
    new = new if isinstance(new, dict) else None
    if old is None or new is None:
        return {"old": old, "new": new, "size_delta_mm": None}
    # Per-axis SIZE change, not a six-number corner delta: a box that grew
    # 4 mm taller says something; six signed corner offsets do not.
    return {"old": old, "new": new,
            "size_delta_mm": [_size(new, axis) - _size(old, axis)
                              for axis in range(3)]}


def _size(bbox: dict, axis: int) -> float:
    return float(bbox["max"][axis]) - float(bbox["min"][axis])


def _union_frame(boxes: list[dict]) -> dict | None:
    """The inflated union of world bboxes — one camera for both sides."""
    boxes = [b for b in boxes if isinstance(b, dict) and b.get("min") and b.get("max")]
    if not boxes:
        return None
    lo = [min(float(b["min"][axis]) for b in boxes) for axis in range(3)]
    hi = [max(float(b["max"][axis]) for b in boxes) for axis in range(3)]
    pad = [max((hi[axis] - lo[axis]) * _FRAME_INFLATE, 1e-6) for axis in range(3)]
    return {"min": [lo[axis] - pad[axis] for axis in range(3)],
            "max": [hi[axis] + pad[axis] for axis in range(3)]}


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


def _error_payload(exc: Exception) -> dict:
    """The structured error shape every surface uses, from an exception."""
    if isinstance(exc, KernelError):
        return exc.to_payload()
    if isinstance(exc, AppError):
        return {
            "type": type(exc).__name__.replace("Error", "").lower() + "_error",
            "message": exc.message,
            "details": exc.details,
        }
    return {"type": "internal_error", "message": str(exc), "details": {}}


# ------------------------------------------------------------ the builder


class PacketBuilder:
    """Generates, persists and serves one proposal's review packet."""

    def __init__(self, service) -> None:
        self.service = service
        # One build slot per (project, proposal): concurrent builds of the
        # same packet would race over the same render PNGs and diff meshes —
        # the first builder's URLs pointing at the second's half-written
        # files. A waiting caller returns the build it waited for.
        self._slots: dict[tuple[str, str], dict] = {}
        self._slots_lock = threading.Lock()

    # ----------------------------------------------------------- public api

    def packet(self, proj: str, pid: str, regenerate: bool = False) -> dict:
        """The packet for a proposal, generated lazily and regenerated on view.

        Served from disk while both heads hold; regenerated when either moved
        (``stale``) or when ``regenerate`` is passed. A **frozen** packet — the
        evidence a merge decision was made on — is served as it stands and
        refuses ``regenerate`` with a ``conflict_error`` (FR12).

        A proposal in a TERMINAL state is never measured again, whether or not
        a packet was frozen for it: the branches have moved on, so a packet
        built now would describe the merged target and other people's commits
        and present them as this proposal's change. Merging records the
        absence of a packet as a frozen one that says so; anything else
        terminal (a closed proposal, a hand-mangled directory) refuses.
        """
        proposal = self._reconcile(proj, pid)
        stored = self.load(proj, proposal)
        state = proposal.get("state")
        if stored is not None and stored.get("frozen"):
            if regenerate:
                raise ConflictError(
                    f"the review packet for proposal {pid} is frozen: it is "
                    "the evidence the merge decision was made on and is never "
                    "regenerated",
                    {"id": pid, "state": state},
                )
            return stored
        if state in TERMINAL:
            if stored is None or regenerate:
                raise ConflictError(
                    f"proposal {pid} is {state}: its review packet is frozen "
                    "or was never generated, and one is never produced "
                    "afterwards — it would measure the branches as they are "
                    "now, not the change that was decided on",
                    {"id": pid, "state": state, "packet": stored is not None},
                )
            return stored
        if stored is not None and not stored.get("stale") and not regenerate:
            return stored

        slot = self._slot(proj, pid)
        seen = slot["builds"]
        with slot["lock"]:
            if slot["builds"] != seen:
                # Someone else built this packet while we queued: theirs is as
                # fresh as ours would be, and its assets are the ones on disk.
                fresh = self.load(proj, proposal)
                if fresh is not None:
                    return fresh
            return self.build(proj, proposal)

    def load(self, proj: str, proposal: dict) -> dict | None:
        """The persisted packet with ``stale`` recomputed against the branches'
        current heads, or None when there is none."""
        try:
            data = json.loads(
                self._store().packet_path(proj, proposal["id"])
                .read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(data, dict):
            return None
        canonical = self.service.store.canonical_path_of(proj)
        moved = any(
            data.get(f"{role}_head") != self.service.history.resolve_branch(
                canonical, proposal.get(role) or "")
            for role in ("source", "target")
        )
        return {**data, "stale": False if data.get("frozen") else moved}

    def build(self, proj: str, proposal: dict) -> dict:
        """Measure both sides and persist the packet, its renders and its diff
        meshes."""
        started = time.monotonic()
        branches = self._branches()
        pid = proposal["id"]
        source, target = proposal["source"], proposal["target"]
        canonical = self.service.store.canonical_path_of(proj)

        trees = {"old": branches.tree_of(proj, target),
                 "new": branches.tree_of(proj, source)}
        # Checkpoint first, THEN read the heads: a packet whose pinned heads do
        # not describe the bytes it measured is worse than no packet.
        self._checkpoint(trees["old"], target)
        self._checkpoint(trees["new"], source)
        heads = {"old": self._head(canonical, target, "target"),
                 "new": self._head(canonical, source, "source")}
        manifests = {
            side: self._manifest_at(canonical, heads[side], name)
            for side, name in (("old", target), ("new", source))
        }

        paths = self._changed_paths(canonical, heads["old"], heads["new"])
        diffs = self._script_diffs(canonical, heads["old"], heads["new"])
        scripts = {path[len("parts/"):-len(".py")] for path in paths
                   if path.startswith("parts/") and path.endswith(".py")}

        warnings: list[str] = []
        errors: list[dict] = []
        rows = changed_parts(manifests["old"], manifests["new"], scripts)
        parts = [
            self._part_section(proj, pid, row, manifests, trees, diffs,
                               warnings, errors)
            for row in rows
        ]
        assembly = self._assembly_section(proj, trees, errors)
        actor = locks.current_client_id()
        packet = {
            "proposal": pid,
            "ok": True,
            "stale": False,
            "frozen": False,
            # Zone-aware UTC, the one stamp format the feature uses (FR13's
            # audit entries come from the same helper).
            "generated": proposal_now(),
            "generated_by": actor,
            "elapsed_ms": 0,
            "source": source,
            "target": target,
            "source_head": heads["new"],
            "target_head": heads["old"],
            "base": self._merge_base(canonical, target, source),
            "summary": _summary(parts, assembly),
            "parts": parts,
            "assembly": assembly,
            "manifest": _manifest_section(manifests["old"], manifests["new"]),
            "binary": self._binary_section(canonical, paths, heads),
            "warnings": warnings,
            "errors": errors,
        }
        packet["elapsed_ms"] = int((time.monotonic() - started) * 1000)
        if not self._persist(proj, pid, packet):
            # The proposal reached a terminal state while we measured: this
            # build describes a decision that has already been made on other
            # evidence, so it is thrown away rather than published over it.
            frozen = self.load(proj, self._store().load(proj, pid))
            if frozen is not None:
                return frozen
            raise ConflictError(
                f"proposal {pid} was closed out while its review packet was "
                "being generated; the packet was discarded rather than "
                "published after the decision",
                {"id": pid},
            )
        return packet

    def render(self, proj: str, pid: str, side: str, part: str | None = None,
               view: str = RENDER_VIEW) -> dict:
        """One render as image content (the MCP/chat path) — a packet carries
        render URLs because the transports lift exactly one ``png_base64``.

        Every render is WRITTEN to the ``path`` it reports, so that path names
        a file that exists (an on-demand view is drawn once and then served
        from disk like the packet's own). A **frozen** packet serves only the
        renders stored with it: drawing a view it never had would draw today's
        branches and label them with the decision's date, so an absent one is
        a ``conflict_error``, exactly like regenerating it (FR12).
        """
        if side not in _SIDES:
            raise ValidationError(f"side must be one of: {', '.join(_SIDES)}",
                                  {"allowed": list(_SIDES)})
        if view not in VIEWS:
            raise ValidationError(f"view must be one of: {', '.join(VIEWS)}")
        proposal = self._store().load(proj, pid)
        packet = self.packet(proj, pid)
        stored = self._render_path(proj, pid, part or "assembly", side, view)

        if packet.get("frozen"):
            if not stored.is_file():
                raise ConflictError(
                    f"the review packet for proposal {pid} is frozen: only "
                    "the renders taken with it can be served, and there is no "
                    f"{view} render of "
                    f"{'the assembly' if part is None else repr(part)} "
                    f"({side} side)",
                    {"id": pid, "part": part, "side": side, "view": view},
                )
            png = stored.read_bytes()
            return _render_result(stored, view, side, part, png)

        branch = proposal["target"] if side == "old" else proposal["source"]
        tree = self._branches().tree_of(proj, branch)
        if part is None:
            frame = self._assembly_frame(proj, proposal)
            png = self._render_assembly(proj, tree, frame, view)
        else:
            section = next((s for s in packet.get("parts") or []
                            if s.get("part") == part), None)
            if section is None:
                raise NotFoundError(
                    f"proposal {pid} does not change part {part!r}",
                    {"id": pid, "part": part})
            if not (section.get("build") or {}).get(side, {}).get("ok"):
                raise NotFoundError(
                    f"the {side} side of part {part!r} does not build, so it "
                    "has no render",
                    {"id": pid, "part": part, "side": side})
            frame = (section.get("renders") or {}).get("frame")
            if view == RENDER_VIEW and stored.is_file():
                return _render_result(stored, view, side, part,
                                      stored.read_bytes())
            png = self._render_part(proj, part, tree, frame, view)
        ProjectStore._atomic_write(stored, png)
        return _render_result(stored, view, side, part, png)

    def diff_mesh_path(self, proj: str, pid: str, part: str, kind: str) -> Path:
        """The ACM1 diff mesh a packet URL points at (the REST asset route)."""
        if kind not in _DIFF_KINDS:
            raise ValidationError(
                f"kind must be one of: {', '.join(_DIFF_KINDS)}",
                {"allowed": list(_DIFF_KINDS)})
        path = self._diff_path(proj, pid, _part_name(part), kind)
        if not path.is_file():
            raise NotFoundError(
                f"proposal {pid} has no {kind} geometry for part {part!r}",
                {"id": pid, "part": part, "kind": kind})
        return path

    # --------------------------------------------------------- per-part work

    def _part_section(self, proj: str, pid: str, row: dict, manifests: dict,
                      trees: dict, diffs: dict, warnings: list,
                      errors: list) -> dict:
        part_id = row["part"]
        entries = {side: _parts_by_id(manifests[side]).get(part_id) or {}
                   for side in _SIDES}
        section = {
            "part": part_id,
            "change": row["change"],
            "changed_by": row["changed_by"],
            "script_diff": diffs.get(f"parts/{part_id}.py"),
            "params_diff": params_delta(entries["old"], entries["new"]),
            "build": {},
            "metrics": None,
            "geom_diff": None,
            "renders": None,
        }
        states = {}
        for side in _SIDES:
            try:
                states[side] = self._side_state(proj, part_id, trees[side])
            except Exception as exc:  # noqa: BLE001 — one part never aborts
                # UNREADABLE, not absent. "The part is not on this side" is a
                # measurement (and makes the geometric diff report the whole
                # part as added or removed); a checkout that could not be read
                # measures nothing at all, and saying otherwise invents the
                # loudest number in the packet.
                states[side] = {"present": True, "ok": False,
                                "unreadable": True, "metrics": None,
                                "error": _error_payload(exc)}
                errors.append({"part": part_id, "stage": "build",
                               "error": _error_payload(exc)})
            section["build"][side] = _build_status(states[side])

        section["metrics"] = metric_delta(states["old"].get("metrics"),
                                          states["new"].get("metrics"))
        if any((state.get("metrics") or {}).get("mesh") for state in states.values()):
            warnings.append(
                f"{part_id}: imported mesh (STL) — its 'center of mass' is the "
                "bounding-box center, so no delta is reported")
        section["geom_diff"] = self._geom_diff(proj, pid, part_id, states, errors)
        section["renders"] = self._renders(proj, pid, part_id, states, trees,
                                           warnings, errors)
        return section

    def _side_state(self, proj: str, part_id: str, tree: Path) -> dict:
        """Everything one side knows about a part: its record, its cache key
        (the short-circuit identity), its kernel item and its build result."""
        with self._branches().pinned(proj, tree):
            try:
                record = self.service.store.get_part(proj, part_id)
            except NotFoundError:
                return {"present": False}
            state = {
                "present": True,
                "kind": record.kind,
                # Content hash of (script/import, params, density, tolerance):
                # equal on both sides means the geometry is byte-identical, so
                # no boolean can find anything (AC4).
                "cache_key": self.service._cache_key_for(proj, record),
                "item": self._item(proj, record),
            }
            built = self.service._ensure_built(proj, part_id)
            state["ok"] = bool(built.get("ok"))
            state["metrics"] = built.get("metrics") if built.get("ok") else None
            state["error"] = None if built.get("ok") else built.get("error")
            return state

    def _item(self, proj: str, record) -> dict:
        """A kernel item for one side, the way ``service._shape_item`` builds
        one (minus placement — a diff is about the part, not the instance)."""
        if record.kind == "reference":
            return {"source": str(self.service.store.imports_dir(proj)
                                  / Path(record.source or "").name)}
        return {"script": self.service.store.read_script(proj, record.id),
                "params": record.params}

    def _geom_diff(self, proj: str, pid: str, part_id: str, states: dict,
                   errors: list) -> dict:
        for side in _SIDES:
            if states[side].get("unreadable"):
                return {"available": False, "unchanged": False,
                        "reason": f"the {side} side is unreadable",
                        "error": states[side].get("error"), "skipped": None}
        keys = {side: states[side].get("cache_key") for side in _SIDES}
        if keys["old"] and keys["old"] == keys["new"]:
            # The short circuit, in the SERVICE and not the kernel: identical
            # content hashes cannot differ geometrically, so no worker is asked.
            return {"available": True, "unchanged": True, "added_mm3": 0.0,
                    "removed_mm3": 0.0, "added_mesh": None,
                    "removed_mesh": None, "skipped": None}
        for side in _SIDES:
            if states[side].get("present") and not states[side].get("ok"):
                return {"available": False, "unchanged": False,
                        "reason": f"the {side} side does not build",
                        "skipped": None}
        if any((states[side].get("metrics") or {}).get("mesh") for side in _SIDES):
            # An imported STL is one welded Face: an OCCT boolean on it
            # segfaults the worker, so it is never attempted.
            return {"available": False, "unchanged": False,
                    "reason": "imported mesh geometry cannot take part in "
                              "booleans", "skipped": "mesh"}

        paths = {kind: self._diff_path(proj, pid, part_id, kind)
                 for kind in _DIFF_KINDS}
        for path in paths.values():
            path.unlink(missing_ok=True)  # never serve a previous generation
        params = {
            "old": states["old"].get("item"),
            "new": states["new"].get("item"),
            "added_path": str(paths["added"]),
            "removed_path": str(paths["removed"]),
            # The tolerance the part's own meshes are built at, so an overlay
            # of the difference has the same facet size as the shapes it is
            # drawn over (the handler's default is the same 0.1).
            "tolerance": _mesh_tolerance(),
        }
        try:
            result = self.service.kernel.request(
                "geom_diff", params, timeout_s=_DIFF_TIMEOUT_S,
                affinity=part_id,
            )
        except KernelError as exc:
            errors.append({"part": part_id, "stage": "geom_diff",
                           "error": exc.to_payload()})
            return {"available": False, "unchanged": False,
                    "reason": "boolean failed", "error": exc.to_payload(),
                    "skipped": None}
        if result.get("skipped_mesh"):
            return {"available": False, "unchanged": False,
                    "reason": "imported mesh geometry cannot take part in "
                              "booleans", "skipped": "mesh"}
        return {
            "available": True,
            "unchanged": False,
            "added_mm3": float(result.get("added_mm3") or 0.0),
            "removed_mm3": float(result.get("removed_mm3") or 0.0),
            # A zero-volume side writes NO file: null tells the UI there is
            # nothing to overlay.
            "added_mesh": self._diff_url(proj, pid, part_id, "added")
            if result.get("added_triangles") else None,
            "removed_mesh": self._diff_url(proj, pid, part_id, "removed")
            if result.get("removed_triangles") else None,
            "skipped": None,
        }

    def _renders(self, proj: str, pid: str, part_id: str, states: dict,
                 trees: dict, warnings: list, errors: list) -> dict:
        frame = _union_frame([(states[side].get("metrics") or {}).get("bbox")
                              for side in _SIDES])
        renders = {"view": RENDER_VIEW, "width": RENDER_WIDTH,
                   "height": RENDER_HEIGHT, "frame": frame,
                   "old": None, "new": None}
        for side in _SIDES:
            if not states[side].get("ok"):
                continue
            try:
                png = self._render_part(proj, part_id, trees[side], frame,
                                        RENDER_VIEW)
                ProjectStore._atomic_write(
                    self._render_path(proj, pid, part_id, side, RENDER_VIEW),
                    png,
                )
            except Exception as exc:  # noqa: BLE001 — a render is evidence,
                # not a precondition: the numbers still stand without it.
                warnings.append(f"{part_id}: {side} render failed: {exc}")
                errors.append({"part": part_id, "stage": "render",
                               "error": _error_payload(exc)})
                continue
            renders[side] = self._render_url(proj, pid, part_id, side)
        return renders

    def _render_part(self, proj: str, part_id: str, tree: Path,
                     frame: dict | None, view: str) -> bytes:
        with self._branches().pinned(proj, tree):
            mesh = acm.read(self.service.ensure_mesh(proj, part_id))
        return render_acm(
            [{"positions": mesh["positions"], "normals": mesh["normals"],
              "indices": mesh["indices"], "transform": None, "color": None}],
            view=view, width=RENDER_WIDTH, height=RENDER_HEIGHT, frame=frame,
        )

    def _render_assembly(self, proj: str, tree: Path, frame: dict | None,
                         view: str) -> bytes:
        meshes = []
        with self._branches().pinned(proj, tree):
            for inst in self.service._resolved_instances(proj):
                try:
                    mesh = acm.read(self.service.ensure_mesh(proj, inst.part))
                except (KernelError, AppError):
                    continue  # an unbuildable instance is skipped, not fatal
                meshes.append({
                    "positions": mesh["positions"], "normals": mesh["normals"],
                    "indices": mesh["indices"],
                    "transform": (inst.position, inst.rotation_deg),
                    "color": inst.color,
                })
        if not meshes:
            raise NotFoundError("no assembly instance could be built")
        return render_acm(meshes, view=view, width=RENDER_WIDTH,
                          height=RENDER_HEIGHT, frame=frame)

    def _assembly_frame(self, proj: str, proposal: dict) -> dict | None:
        branches = self._branches()
        boxes = []
        for role in ("target", "source"):
            tree = branches.tree_of(proj, proposal[role])
            with branches.pinned(proj, tree):
                try:
                    boxes.append(self.service.get_assembly(proj).get("bbox"))
                except (KernelError, AppError):
                    continue
        return _union_frame(boxes)

    def _assembly_section(self, proj: str, trees: dict, errors: list) -> dict | None:
        sides = {}
        for side in _SIDES:
            try:
                with self._branches().pinned(proj, trees[side]):
                    sides[side] = self.service.get_assembly(proj)
            except Exception as exc:  # noqa: BLE001 — FR8: degrade, never abort
                errors.append({"part": None, "stage": "assembly",
                               "error": _error_payload(exc)})
                return None
        return assembly_delta(sides["old"], sides["new"])

    # ------------------------------------------------------------------ git

    def _checkpoint(self, tree: Path, branch: str) -> None:
        """Snapshot a branch tree, and REFUSE when it is still dirty.

        ``ProjectHistory.snapshot`` is exception-free by contract and returns
        None both for "nothing to commit" and for "git failed"; the difference
        is whether the tree is dirty afterwards. Mirrors
        ``BranchManager._checkpoint`` (which is not reachable from here).
        """
        self.service.history.snapshot(tree, f"checkpoint before reviewing {branch}")
        result = self.service.history._run(tree, "status", "--porcelain",
                                           check=False)
        if result.returncode != 0 or result.stdout.strip():
            raise ConflictError(
                f"branch {branch!r} has uncommitted changes that could not be "
                "snapshotted; a review packet must describe committed bytes",
                {"branch": branch, "tree": str(tree),
                 "status": result.stdout.strip()[:2000]},
            )

    def _head(self, canonical: Path, branch: str, role: str) -> str:
        head = self.service.history.resolve_branch(canonical, branch)
        if not head:
            raise NotFoundError(f"branch {branch!r} not found",
                                {"role": role, "branch": branch})
        return head

    def _merge_base(self, canonical: Path, target: str, source: str) -> str | None:
        # refs/heads/…, not the bare names: a tag shadowing a branch would
        # otherwise answer for it (PRD-001 X1).
        result = self.service.history._run(
            canonical, "merge-base", f"refs/heads/{target}",
            f"refs/heads/{source}", check=False)
        base = result.stdout.strip()
        return base if result.returncode == 0 and base else None

    def _manifest_at(self, canonical: Path, commit: str, ref: str) -> dict:
        """The manifest at a ref, with ``merge._manifest_at``'s strictness: a
        ``project.json`` that exists but does not parse is a refusal, never
        ``{}`` — reading it as empty would report the whole project deleted."""
        result = self.service.history._run(
            canonical, "cat-file", "blob", f"{commit}:project.json", check=False)
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
        raise ValidationError(
            f"project.json at {ref!r} is not a readable manifest ({problem}); "
            "fix or restore it before the change can be reviewed",
            {"ref": ref, "commit": commit, "file": "project.json"},
        )

    def _changed_paths(self, canonical: Path, old: str, new: str) -> list[str]:
        result = self.service.history._run(
            canonical, "diff", "--name-only", old, new, check=False)
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]

    def _script_diffs(self, canonical: Path, old: str, new: str) -> dict:
        """``{path: {path, unified, added_lines, removed_lines, truncated,
        hunks}}`` for ``parts/``.

        ``hunks`` exists so PRD-008 can anchor a thread to a hunk without
        depending on line numbers surviving a regeneration; nothing in MVP
        reads it beyond the UI's data attributes.
        """
        result = self.service.history._run(
            canonical, "diff", "--no-color", "--unified=3", old, new, "--",
            "parts/", check=False)
        diffs: dict[str, dict] = {}
        current: dict | None = None
        in_body = False  # a body line may itself start with '+++ '/'--- '
        for line in result.stdout.splitlines():
            if line.startswith("diff --git "):
                current, in_body = None, False
                continue
            if not in_body and (line.startswith("+++ b/")
                                or line.startswith("--- a/")):
                path = line[6:].strip()
                current = diffs.setdefault(path, {
                    "path": path, "unified": "", "added_lines": 0,
                    "removed_lines": 0, "truncated": False, "hunks": [],
                })
                continue
            if current is None:
                continue
            if line.startswith("@@"):
                in_body = True
                current["hunks"].append(_hunk(line, len(current["hunks"])))
            elif line.startswith("+"):
                current["added_lines"] += 1
            elif line.startswith("-"):
                current["removed_lines"] += 1
            current["unified"] += line + "\n"
        for diff in diffs.values():
            if len(diff["unified"].encode()) > _MAX_DIFF_BYTES:
                diff["unified"] = None
                diff["truncated"] = True
        return diffs

    def _binary_section(self, canonical: Path, paths: list[str],
                        heads: dict) -> list[dict]:
        """Changed non-text paths as size + digest per side — never the bytes.

        The contract ``merge._binary_conflict`` already uses: a reviewer can
        tell the versions apart without an STL ever reaching a diff, a boolean
        or the payload.
        """
        entries = []
        for path in paths:
            if path.startswith("parts/") and path.endswith(".py"):
                continue
            if path == "project.json":
                continue  # reported as manifest/params deltas instead
            sides = {side: self._blob(canonical, heads[side], path)
                     for side in _SIDES}
            if not any(_is_binary(body) for body in sides.values()):
                continue
            entries.append({
                "path": path,
                "sides": {
                    side: (None if body is None else {
                        "bytes": len(body),
                        "sha256": hashlib.sha256(body).hexdigest(),
                    })
                    for side, body in sides.items()
                },
            })
        return entries

    def _blob(self, canonical: Path, commit: str, path: str) -> bytes | None:
        result = self.service.history._run_bytes(
            canonical, "cat-file", "blob", f"{commit}:{path}", check=False)
        return result.stdout if result.returncode == 0 else None

    # ------------------------------------------------------------ locations

    def _asset_dir(self, proj: str, pid: str, kind: str) -> Path:
        return self._store().asset_dir(proj, pid, kind)

    def _render_path(self, proj: str, pid: str, part: str, side: str,
                     view: str) -> Path:
        return self._asset_dir(proj, pid, "renders") \
            / f"{_part_name(part)}.{side}.{view}.png"

    def _diff_path(self, proj: str, pid: str, part: str, kind: str) -> Path:
        return self._asset_dir(proj, pid, "diff") / f"{_part_name(part)}.{kind}.acm"

    def _render_url(self, proj: str, pid: str, part: str, side: str) -> str:
        return f"{self._base_url(proj, pid)}/render/{side}/{_quote(part)}"

    def _diff_url(self, proj: str, pid: str, part: str, kind: str) -> str:
        return f"{self._base_url(proj, pid)}/diff/{_quote(part)}/{kind}.acm"

    @staticmethod
    def _base_url(proj: str, pid: str) -> str:
        return f"/api/projects/{_quote(proj)}/proposals/{_quote(pid)}"

    # ------------------------------------------------------------ internals

    def _persist(self, proj: str, pid: str, packet: dict) -> bool:
        """Hand the finished packet to the lifecycle, which owns the only lock
        that orders ``packet.json`` and ``proposal.json`` against a merge.
        False when the build lost that race and was discarded."""
        recorded = self._proposals().record_packet(proj, pid, packet)
        slot = self._slot(proj, pid)
        with self._slots_lock:
            slot["builds"] += 1
        return recorded

    def _slot(self, proj: str, pid: str) -> dict:
        with self._slots_lock:
            return self._slots.setdefault(
                (proj, pid), {"lock": threading.Lock(), "builds": 0})

    def _reconcile(self, proj: str, pid: str) -> dict:
        """The proposal, with a staged merge that has since landed finalized
        first — so a packet view during that window sees a merged proposal
        (and its frozen packet) rather than regenerating one."""
        return self._proposals().reconcile(proj, pid)

    def _proposals(self):
        proposals = getattr(self.service, "proposals", None)
        if proposals is None:
            raise ValidationError("proposals unavailable: git not found on PATH")
        return proposals

    def _store(self):
        return self._proposals().store

    def _branches(self):
        branches = getattr(self.service, "branches", None)
        if branches is None:
            raise ValidationError("proposals unavailable: git not found on PATH")
        return branches


def _mesh_tolerance() -> float:
    """The service's mesh tolerance, imported at call time: ``service`` loads
    the tool packs, which load this module."""
    from .service import MESH_TOLERANCE

    return float(MESH_TOLERANCE)


def _render_result(path: Path, view: str, side: str, part: str | None,
                   png: bytes) -> dict:
    return {
        "path": str(path),
        "width": RENDER_WIDTH,
        "height": RENDER_HEIGHT,
        "view": view,
        "side": side,
        "part": part,
        "png_base64": base64.b64encode(png).decode("ascii"),
    }


def _build_status(state: dict) -> dict:
    if not state.get("present"):
        return {"ok": False, "present": False}
    if state.get("ok"):
        return {"ok": True}
    return {"ok": False, "error": state.get("error")}


def _hunk(header: str, index: int) -> dict:
    """``@@ -12,6 +12,8 @@`` -> the anchor PRD-008 will hang threads off."""
    old_start = new_start = 0
    for token in header.split("@@")[1].split():
        try:
            value = int(token[1:].split(",")[0])
        except (ValueError, IndexError):
            continue
        if token.startswith("-"):
            old_start = value
        elif token.startswith("+"):
            new_start = value
    return {"index": index, "header": header.strip(), "old_start": old_start,
            "new_start": new_start}


def _summary(parts: list[dict], assembly: dict | None) -> dict:
    changes = [part["change"] for part in parts]
    moved = assembly or {}
    return {
        "parts_changed": changes.count("modified"),
        "parts_added": changes.count("added"),
        "parts_removed": changes.count("removed"),
        "instances_changed": len(moved.get("instances_added") or [])
        + len(moved.get("instances_removed") or [])
        + len(moved.get("instances_moved") or []),
        "mass_delta_g": (moved.get("total_mass_g") or {}).get("delta"),
    }


def _manifest_section(old: dict, new: dict) -> dict:
    """Project-level manifest changes that are not part or assembly edits."""
    scalars = []
    for key in sorted(set(old) | set(new)):
        if key in ("parts", "assembly", "materials"):
            continue
        if _norm(old.get(key, _MISSING)) != _norm(new.get(key, _MISSING)):
            scalars.append({"key": key, "old": old.get(key),
                            "new": new.get(key)})
    old_materials = old.get("materials") or {}
    new_materials = new.get("materials") or {}
    materials = [
        {"id": mid, "old": old_materials.get(mid), "new": new_materials.get(mid)}
        for mid in sorted(set(old_materials) | set(new_materials))
        if _norm(old_materials.get(mid, _MISSING))
        != _norm(new_materials.get(mid, _MISSING))
    ]
    return {"scalars_changed": scalars, "materials_changed": materials}


def _part_name(part: str) -> str:
    """Asset filenames come from URL path segments: whitelist them."""
    return validate_id(part, "part id")


def _quote(value: str) -> str:
    return urllib.parse.quote(str(value), safe="")
