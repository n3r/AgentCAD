"""The bench scorer: six subscores, one copy, one deterministic `score.json`.

Design §3, §4 and §6. Three rules carry the whole module and every one of them
is a rule about *honesty*, not about arithmetic:

1. **Nothing is measured in place.** A submission is copied into a work
   cell and opened through a muzzled ephemeral service
   (`checks._ephemeral_service`), so the candidate's directory is untouched
   *by construction*: no history commit,
   no `.history/agentcad/` sidecar, no materialised branch tree, and `.cache/`
   lands in the cell. The run removes only the cell it created.

2. **`error` means the harness failed to measure; `not_applicable` is declared
   by `task.json` (weight 0) and never by a run.** A candidate that is absent,
   broken, mesh-only or simply wrong is *measured*, and it measures **zero**.
   Getting this backwards would reward destroying evidence: excluded subscores
   are renormalised away, so a run-decided exclusion would let a candidate
   raise its total by making a subscore unmeasurable (delete the part, break
   the build, hand back a mesh). Nothing at run time may promote a subscore to
   `not_applicable`.

3. **The rubric is injected into the copy and it RE-BINDS `SPECS`.** Whatever
   the candidate declared for itself is discarded, because the last
   module-level binding wins. That is what stops an agent inflating its
   `specs` subscore with checks it wrote itself. The loader refuses a rubric
   that uses `SPECS +=` for exactly this reason.

The module is OCP-free by contract: it never imports build123d or OCP, and the
one mesh question it has to ask (`is this side an STL?`) is answered by suffix
here rather than by importing `kernel.refload`, which does import build123d.

`score.json` carries **no timestamp, no host, no path, no duration and no
client id** — the packages-provenance rule. Everything non-deterministic
lives in the sibling `run.json` the runner writes (design §8.6). That, plus
`round(x, 6)` applied recursively before serialisation, is the whole of AC3.
"""
from __future__ import annotations

import math
import shutil
import tempfile
import time
from itertools import combinations
from pathlib import Path
from types import SimpleNamespace

import agentcad

from ..core.checks import (_ephemeral_service, _payload, _within,
                           default_work_root, refuse_work_dir_overlap)
from ..core.model import AppError, ValidationError
from ..core.specs import _slack
from ..kernel.client import KernelError
from ..kernel.protocol import ERROR_TIMEOUT
from . import HARNESS_VERSION
from ._json import round_floats
from .tasks import SUBSCORES, Task, load_windows, tasks_root

#: `score.json`'s schema. Bumped when a *field* moves; `harness` is bumped when
#: a subscore's computation changes, and the two are different questions.
SCORE_SCHEMA = 1

#: The flat ceiling one `iou` call gets. The kernel client's own default is
#: 60 s, which is a build timeout, not a two-sided boolean's — every call site
#: here passes this explicitly. Under a budget the call is handed
#: `min(IOU_TIMEOUT_S, remaining)` instead, and a timeout on *that* is budget
#: truncation rather than a fact about the geometry (`checks._budget_broke`).
IOU_TIMEOUT_S = 300.0

#: The flat ceiling one `check_interference` call gets, `service`'s own.
INTERFERENCE_TIMEOUT_S = 600.0

#: Below this, a kernel call buys nothing but a timeout: the remaining budget
#: is reported as truncation instead (`checks._MIN_CALL_S`, one size up).
_MIN_CALL_S = 1.0

SUBSCORE_STATUSES = ("ok", "skipped_mesh", "error", "not_applicable")

#: What a mesh side looks like from a module that may not import build123d.
#: Mirrors `kernel.refload._MESH_EXTS`; the loader already refuses a `.stl`
#: *reference*, so this only ever fires on the candidate side.
MESH_SUFFIXES = (".stl",)

#: What never travels into the copy. `.cache`/`exports` are the golden-example
#: tests' discipline; `.history` is added because a submission's git sidecar is
#: not part of the submission and copying it would carry a whole repository.
COPY_IGNORE = (".cache", "exports", ".history")

#: The line that separates the candidate's script from the injected rubric. It
#: names the bench so a human reading a failed candidate's script in a work
#: cell knows immediately which half they authored.
BLOCK_HEADER = "# --- agentcad-bench: task rubric (appended by the scorer) ---"

#: How a metric key is read off a build result's `metrics`. `volume_mm3` and
#: friends are top-level; the six derived ones come out of `bbox` and
#: `center_of_mass`, which is the whole reason this table exists.
#: The subscores that read a build result. Nothing outside this tuple can make
#: the scorer build a part.
_BUILD_READERS = ("built", "valid", "geometry", "metrics")

_BBOX_AXES = {"bbox_x_mm": 0, "bbox_y_mm": 1, "bbox_z_mm": 2}
_COM_AXES = {"com_x_mm": 0, "com_y_mm": 1, "com_z_mm": 2}


# --------------------------------------------------------------- refusals

def refuse_scoring_overlap(root, submission, task_root, projects_root) -> None:
    """Refuse a `--work-dir` that overlaps anything the run must not touch.

    `checks.refuse_work_dir_overlap` answers it for the submission and the
    projects root — the catastrophic case, where the cell is named after the
    project and materialising there deletes the user's work. This adds the two
    inputs a bench run also reads and may never write: the **task directory**
    and the shipped **`benchmarks/` tree**, the packages gate's
    `_refuse_overlap` precedent (which likewise covers the package directory,
    not only the project).

    ``root`` of ``None`` means "no `--work-dir`": there is nothing to refuse.
    """
    if root is None:
        return
    refuse_work_dir_overlap(root, submission, projects_root)
    resolved = Path(root).resolve()
    for label, path in (("the task directory", Path(task_root).resolve()),
                        ("the bench task tree", tasks_root().resolve())):
        if resolved == path or _within(path, resolved) or \
                _within(resolved, path):
            raise ValidationError(
                f"--work-dir {resolved} overlaps {label} {path}: a bench run "
                f"materializes a throwaway copy under the work dir and "
                f"deletes it afterwards, and the task bundle is a read-only "
                f"input — "
                f"pass a directory elsewhere, or omit --work-dir for a temp "
                f"dir",
                {"work_dir": str(resolved), "path": str(path)})


# ------------------------------------------------------- rubric injection

def inject_rubric(task: Task, copy_root: Path) -> list[str]:
    """Write the task's rubric into the **copy**; return the parts it touched.

    The project-scope block replaces `<copy>/specs.py` outright. A part block
    is **appended** to the candidate's script, behind :data:`BLOCK_HEADER`,
    and it re-binds `SPECS` — the last module-level binding wins, so the
    candidate's own declarations are dropped (design §3.1).

    A `specs.parts` entry naming a part the copy does not have is **not** an
    error and not a skip that hides anything: it is a missing part, which the
    `built` subscore already reports as a zero.
    """
    copy_root = Path(copy_root)
    if task.specs_project_path is not None:
        (copy_root / "specs.py").write_text(
            task.specs_project_path.read_text(encoding="utf-8"),
            encoding="utf-8")
    touched: list[str] = []
    for part_id, block in sorted(task.specs_part_paths.items()):
        script = copy_root / "parts" / f"{part_id}.py"
        if not script.is_file():
            continue
        script.write_text(
            f"{script.read_text(encoding='utf-8')}\n\n{BLOCK_HEADER}\n"
            f"{block.read_text(encoding='utf-8')}\n", encoding="utf-8")
        touched.append(part_id)
    return touched


# ----------------------------------------------------------- the arithmetic

def total_of(subscores: dict) -> tuple[float, dict]:
    """``(total, weights_effective)`` over the *included* subscores.

    `error` and `not_applicable` are excluded and the remaining weights are
    renormalised, so a reader can reproduce the arithmetic from the published
    `weights_effective` alone without knowing the rule. Every subscore excluded
    (``W == 0``) is a total of ``0.0`` and no verdict — `bench score` turns
    that into exit 2, which is `checks.exit_code`'s meaning of 2 exactly.
    """
    included = {name: row for name, row in subscores.items()
                if row["status"] not in ("error", "not_applicable")}
    weight = sum(row["weight"] for row in included.values())
    if weight <= 0.0:
        return 0.0, {}
    effective = {name: row["weight"] / weight
                 for name, row in included.items()}
    total = sum(row["value"] * effective[name]
                for name, row in included.items())
    return total, effective


def _finite(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) \
        and math.isfinite(value)


def _error(exc: BaseException) -> dict:
    """One failure, as ``{"type", "message"}`` — and deliberately **no**
    ``details``.

    `checks._payload` carries the whole structured payload, which is right
    there and wrong here: a `KernelError`'s ``details`` holds a traceback, a
    traceback names files, and a file name in `score.json` is both a path
    (design §6 rule 5) and — when it names the work cell — a string that
    changes on every run. Byte-identity cannot survive it.
    """
    payload = _payload(exc)
    return {"type": payload["type"], "message": payload["message"]}


def _scrub(value, needles: tuple):
    """Replace every *needle* found in any string of *value* with ``<cell>``.

    The last line of the AC3 defence, and the only one that can catch a path a
    message we did not write put there: `ProjectStore._read_manifest`'s refusal
    is literally ``f"{path} is not a project"``, and *path* is this run's
    randomly-named cell. One run's `score.json` would differ from the next's in
    exactly the string a reader has no use for.
    """
    if isinstance(value, str):
        for needle in needles:
            if needle:
                value = value.replace(needle, "<cell>")
        return value
    if isinstance(value, dict):
        return {key: _scrub(item, needles) for key, item in value.items()}
    if isinstance(value, list):
        return [_scrub(item, needles) for item in value]
    return value


# --------------------------------------------------------------- the scorer

class Scorer:
    """Scores one submission against one task, on a copy, in a work cell.

    The service handed in supplies **the kernel** (shared, never restarted: a
    second pool would cost seconds and half a gigabyte to run the same builds)
    and the projects root the overlap refusal is asked about. It is never the
    service the candidate is measured through — that one is built per run, over
    the cell, and muzzled.
    """

    def __init__(self, service, registry=None):
        self.service = service
        # Accepted for symmetry with the other bench entry points and kept for
        # a caller that wants to introspect one; the scorer builds its own
        # registry per cell, because the registry a candidate is measured
        # through must be rooted at the cell and not at the caller's tree.
        self.registry = registry

    # ------------------------------------------------------------ lifecycle

    def score(self, task: Task, submission, *, budget_s: float | None = None,
              work_dir: str | None = None) -> dict:
        """The `score.json` payload for *submission* under *task*.

        `CheckRunner._run_ref`'s shape: refuse an overlapping work dir, cut a
        cell, copy, inject, open a muzzled service, measure, and remove **only
        the cell we created** in a `finally`.
        """
        submission = Path(submission).expanduser().resolve()
        projects_root = Path(self.service.store.root).resolve()
        refuse_scoring_overlap(work_dir, submission, task.root, projects_root)
        parent = work_dir or default_work_root(self.service)
        cell = Path(tempfile.mkdtemp(prefix="agentcad-bench-", dir=parent))
        deadline = None if budget_s is None else time.monotonic() + budget_s
        notes: list[str] = []
        try:
            tree = cell / "candidate" / task.target_project
            if submission.is_dir():
                shutil.copytree(submission, tree,
                                ignore=shutil.ignore_patterns(*COPY_IGNORE))
                inject_rubric(task, tree)
            # NON-NEGOTIABLE, all three nullings inside `_ephemeral_service`: a
            # `project_changed` publish would commit a history snapshot, a live
            # `branch_resolver` would write a `.history/agentcad/` sidecar into
            # the copy, and `write_guard` would materialise a branch tree.
            try:
                service, _registry, proj = _ephemeral_service(
                    cell, tree, self.service.kernel)
            except (AppError, OSError) as exc:
                # A submission that is not a readable AgentCAD project is a
                # candidate that measures zero — never a harness error, or an
                # agent that produced nothing at all would score better than
                # one that produced something wrong (rule 2).
                notes.append(f"the submission is not a readable AgentCAD "
                             f"project: {_error(exc)['message']}")
                subscores = {name: self._zeroed(task, name, "unreadable")
                             for name in SUBSCORES}
            else:
                subscores = self._measure(service, task, proj, deadline, notes)
            # Both spellings: `mkdtemp` hands back the path it was given a
            # parent for, while everything downstream resolves it, and on macOS
            # those differ (`/var/folders/...` vs `/private/var/folders/...`).
            needles = (str(cell.resolve()), str(cell))
            return _scrub(self._document(task, subscores, notes),
                          needles)
        finally:
            shutil.rmtree(cell, ignore_errors=True)

    def _measure(self, service, task: Task, proj: str, deadline,
                 notes) -> dict:
        """The six subscores over one prepared, muzzled cell.

        The builds happen **once**, here, and every subscore reads the same
        results: injection is already done, so one build per part serves
        `built`, `valid`, `geometry` and `metrics` alike.
        """
        # A build is a kernel call, and §4.7 is literal: a subscore the task
        # zeroed is never measured. When no subscore reads a build result, no
        # part is built at all.
        builds = (self._build_all(service, task, proj)
                  if any(self._scored(task, name) for name in _BUILD_READERS)
                  else {})
        return {
            "built": self._built(task, builds),
            "valid": self._valid(task, builds),
            "specs": self._specs(service, task, proj, deadline, notes),
            "geometry": self._geometry(service, task, proj, builds, deadline,
                                       notes),
            "interference": self._interference(service, task, proj, deadline,
                                               notes),
            "metrics": self._metrics(task, builds),
        }

    def _document(self, task: Task, subscores: dict,
                  notes: list) -> dict:
        total, effective = total_of(subscores)
        if not effective:
            notes.append("every subscore was excluded from the total, so no "
                         "verdict could be produced")
        payload = {
            "schema": SCORE_SCHEMA,
            "agentcad": agentcad.__version__,
            "harness": HARNESS_VERSION,
            "task": task.id,
            "task_set": task.task_set,
            "task_version": task.version,
            "category": task.category,
            "total": total,
            "weights_effective": effective,
            "subscores": subscores,
            "notes": sorted(set(notes)),
        }
        # One rounding, applied recursively, so the dict a caller reads is the
        # document `write_json` will emit — byte-identity is not a property of
        # the writer alone (FR6/AC3).
        return round_floats(payload)

    # ------------------------------------------------------------- subscores

    def _subscore(self, task: Task, name: str, value: float, status: str,
                  detail: dict) -> dict:
        weight = float(task.weights[name])
        return {"value": max(0.0, min(1.0, float(value))), "weight": weight,
                "status": status, "detail": detail}

    def _zeroed(self, task: Task, name: str, reason: str) -> dict:
        """A measured zero — the D5 answer for a candidate we could not open.

        `not_applicable` when, and only when, the *task* zeroed the weight.
        """
        if not task.weights.get(name):
            return self._not_applicable(task, name)
        return self._subscore(task, name, 0.0, "ok", {"reason": reason})

    def _not_applicable(self, task: Task, name: str) -> dict:
        return {"value": 0.0, "weight": 0.0, "status": "not_applicable",
                "detail": {"reason": "weight_zero"}}

    def _scored(self, task: Task, name: str) -> bool:
        """Whether this subscore is measured at all.

        A zero weight short-circuits **before any measurement** — no kernel
        call, no `check_interference`, no spec run. The task decides; a run
        never does (design §4.7).
        """
        return bool(task.weights.get(name))

    # -- built / valid -----------------------------------------------------

    def _build_all(self, service, task: Task, proj: str) -> dict:
        """One entry per target part: what happened when we built it.

        ``state`` is ``"ok"`` (built), ``"failed"`` (built and said no, or is
        not in the manifest at all) or ``"error"`` (`_ensure_built` *raised*).
        A part absent from the copy's manifest is a **failure**, never an
        error: it is the most obvious way a candidate can be wrong, and calling
        it an error would exclude the subscore and reward the deletion.
        """
        present = set(service.store.part_ids(proj))
        out: dict[str, dict] = {}
        for part_id in task.target_parts:
            if part_id not in present:
                out[part_id] = {"state": "failed", "result": None,
                                "reason": "missing"}
                continue
            try:
                result = service._ensure_built(proj, part_id)
            except Exception as exc:  # noqa: BLE001 — `_ensure_built` turns a
                # KernelError into `ok: false` already; this is
                # `CheckRunner._build_item`'s defensive edge (an unreadable
                # import, a missing script file), which must be one honest
                # `error` rather than the end of the run.
                out[part_id] = {"state": "error", "result": None,
                                "error": _error(exc)}
                continue
            out[part_id] = {
                "state": "ok" if result.get("ok") else "failed",
                "result": result,
                "reason": None if result.get("ok") else "build_failed"}
        return out

    def _built(self, task: Task, builds: dict) -> dict:
        if not self._scored(task, "built"):
            return self._not_applicable(task, "built")
        errors = [{"part": part, "message": row["error"]["message"]}
                  for part, row in sorted(builds.items())
                  if row["state"] == "error"]
        if errors:
            return self._subscore(task, "built", 0.0, "error",
                                  {"errors": errors})
        failed = sorted(part for part, row in builds.items()
                        if row["state"] != "ok")
        total = len(task.target_parts)
        value = 0.0 if not total else 1.0 - len(failed) / total
        return self._subscore(task, "built", value, "ok",
                              {"parts": total, "failed": failed})

    def _valid(self, task: Task, builds: dict) -> dict:
        """`CheckRunner._build_item`'s validity rule, minus its
        imported-geometry escape: a bench candidate that imports a mesh is
        measured, not forgiven.
        """
        if not self._scored(task, "valid"):
            return self._not_applicable(task, "valid")
        if any(row["state"] == "error" for row in builds.values()):
            return self._subscore(
                task, "valid", 0.0, "error",
                {"errors": sorted(part for part, row in builds.items()
                                  if row["state"] == "error")})
        invalid, solids = [], {}
        for part, row in sorted(builds.items()):
            metrics = (row["result"] or {}).get("metrics") or {}
            if row["state"] != "ok" or metrics.get("is_valid") is not True:
                invalid.append(part)
            if row["state"] == "ok":
                solids[part] = metrics.get("n_solids")
        total = len(task.target_parts)
        value = 0.0 if not total else 1.0 - len(invalid) / total
        return self._subscore(task, "valid", value, "ok",
                              {"invalid": invalid, "n_solids": solids})

    # -- specs -------------------------------------------------------------

    def _specs(self, service, task: Task, proj: str, deadline,
               notes: list) -> dict:
        """One `SpecRunner.run` over the injected rubric.

        `service.specs` is read **here**, never captured in `__init__`: the
        runner belongs to the ephemeral service `build_registry` just built,
        and that is `CheckRunner._stage_specs`' rule verbatim.

        **The report is never embedded** — `_report` stamps a `generated`
        timestamp, and one timestamp anywhere in the body would end AC3.
        """
        if not self._scored(task, "specs"):
            return self._not_applicable(task, "specs")
        runner = getattr(service, "specs", None)
        if runner is None:
            return self._subscore(task, "specs", 0.0, "error",
                                  {"reason": "specs_unavailable"})
        try:
            report = runner.run(proj, deadline=deadline)
        except Exception as exc:  # noqa: BLE001 — a spec run that did not
            # complete is the harness failing to measure, which is exactly what
            # `error` is for.
            return self._subscore(task, "specs", 0.0, "error",
                                  {"error": _error(exc)})
        buckets: dict[str, list] = {"pass": [], "fail": [], "skip": [],
                                    "error": []}
        for row in report.get("checks") or []:
            buckets.setdefault(row.get("status"), []).append(row.get("id"))
        # `filter(None, …)`: `assign_ids` stamps every row, but a `None` in a
        # list being sorted against strings is a TypeError, and one malformed
        # row may not be the end of a whole score.
        detail = {
            "passed": len(buckets["pass"]),
            "failed": sorted(filter(None, buckets["fail"])),
            "skipped": sorted(filter(None, buckets["skip"])),
            "errors": sorted(filter(None, buckets["error"])),
            "total": len(report.get("checks") or []),
        }
        # A skip is "we did not measure", so it is out of the denominator
        # entirely: scoring it as a pass would launder a machine-specific gap
        # into a green, and scoring it as a fail would punish a candidate for
        # this machine's missing extra. PRD-003's own rule.
        # Counted from the buckets, never from `detail`: the lists there are
        # id lists and a row with no id would silently leave the denominator.
        denominator = (len(buckets["pass"]) + len(buckets["fail"])
                       + len(buckets["error"]))
        if denominator == 0:
            return self._subscore(task, "specs", 0.0, "error",
                                  {**detail, "reason": "nothing_measured"})
        return self._subscore(task, "specs", detail["passed"] / denominator,
                              "ok", detail)

    # -- geometry ----------------------------------------------------------

    def _remaining(self, deadline) -> float | None:
        return None if deadline is None else deadline - time.monotonic()

    def _timeout(self, deadline, ceiling: float) -> tuple[float, float | None]:
        """``(timeout_s, remaining)`` for one kernel call under *deadline*."""
        remaining = self._remaining(deadline)
        if remaining is None:
            return ceiling, None
        return max(_MIN_CALL_S, min(ceiling, remaining)), remaining

    def _candidate_item(self, service, proj: str, part_id: str) -> dict:
        """The worker item for the candidate side, `service._shape_item`'s.

        The placement is the identity: `iou` aligns with the task's own frame,
        and an assembly instance's position has nothing to do with a part's
        geometry score.
        """
        record = service.store.get_part(proj, part_id)
        placed = SimpleNamespace(position=[0.0, 0.0, 0.0],
                                 rotation_deg=[0.0, 0.0, 0.0])
        return service._shape_item(proj, record, placed)

    def _geometry(self, service, task: Task, proj: str, builds: dict, deadline,
                  notes: list) -> dict:
        if not self._scored(task, "geometry"):
            return self._not_applicable(task, "geometry")
        parts = sorted(part for part in task.target_parts
                       if part in task.reference_steps)
        if not parts:
            return self._subscore(task, "geometry", 0.0, "error",
                                  {"reason": "no_reference_steps"})
        detail: dict = {}
        scores: list[float] = []
        for part_id in parts:
            if builds.get(part_id, {}).get("state") != "ok":
                # Not `error`: a candidate that did not build is wrong, and a
                # wrong candidate is measured at zero (rule 2).
                detail[part_id] = {
                    "iou": 0.0,
                    "reason": builds.get(part_id, {}).get("reason")
                    or "build_failed"}
                scores.append(0.0)
                continue
            try:
                item = self._candidate_item(service, proj, part_id)
            except AppError as exc:
                detail[part_id] = {"iou": 0.0,
                                   "reason": _error(exc)["type"]}
                scores.append(0.0)
                continue
            source = item.get("source")
            if source and Path(source).suffix.lower() in MESH_SUFFIXES:
                # Zero and INCLUDED, never excluded: being handed a mesh when a
                # model was asked for is a fact about the candidate (FR5/AC4).
                return self._subscore(
                    task, "geometry", 0.0, "skipped_mesh",
                    {**detail, part_id: {"iou": 0.0,
                                         "reason": "candidate_is_a_mesh"}})
            timeout_s, remaining = self._timeout(deadline, IOU_TIMEOUT_S)
            try:
                result = service.kernel.request(
                    "iou",
                    {"candidate": item,
                     "reference": {"source": str(
                         task.reference_steps[part_id])},
                     "align": task.frame.align,
                     "rotations_deg": ([list(rot) for rot
                                        in task.frame.rotations_deg]
                                       or [[0.0, 0.0, 0.0]])},
                    timeout_s=timeout_s, affinity=task.id)
            except KernelError as exc:
                if self._budget_broke(exc, remaining, IOU_TIMEOUT_S):
                    notes.append("the budget ran out during the geometry "
                                 "measurement; the subscore is excluded")
                return self._subscore(
                    task, "geometry", 0.0, "error",
                    {**detail,
                     "error": {"type": exc.type, "message": exc.message,
                               "stage": (exc.details or {}).get("stage")}})
            if result.get("status") == "skipped_mesh":
                return self._subscore(
                    task, "geometry", 0.0, "skipped_mesh",
                    {**detail,
                     part_id: {"iou": 0.0,
                               "skipped_mesh": result.get("skipped_mesh")}})
            iou = result.get("iou")
            if not _finite(iou):
                return self._subscore(
                    task, "geometry", 0.0, "error",
                    {**detail, "error": {
                        "type": "kernel_error",
                        "message": f"iou returned a non-finite value for "
                                   f"{part_id}", "stage": "intersect"}})
            detail[part_id] = {
                key: result.get(key) for key in
                ("iou", "intersection_mm3", "union_mm3",
                 "candidate_volume_mm3", "reference_volume_mm3", "align",
                 "rotation_deg")}
            scores.append(float(iou))
        return self._subscore(task, "geometry", sum(scores) / len(scores),
                              "ok", detail)

    def _budget_broke(self, exc: BaseException, remaining: float | None,
                      ceiling: float) -> bool:
        """`checks._budget_broke`: a timeout on a call handed *less* than its
        own ceiling is the deadline running out mid-call, not a fact about the
        geometry."""
        return (remaining is not None and remaining < ceiling
                and isinstance(exc, KernelError) and exc.type == ERROR_TIMEOUT)

    # -- interference ------------------------------------------------------

    def _interference(self, service, task: Task, proj: str, deadline,
                      notes: list) -> dict:
        if not self._scored(task, "interference"):
            return self._not_applicable(task, "interference")
        timeout_s, remaining = self._timeout(deadline, INTERFERENCE_TIMEOUT_S)
        try:
            result = service.check_interference(proj, min_volume=0.001,
                                                timeout_s=timeout_s)
        except Exception as exc:  # noqa: BLE001 — an assembly the kernel could
            # not resolve is the harness failing to measure.
            if self._budget_broke(exc, remaining, INTERFERENCE_TIMEOUT_S):
                notes.append("the budget ran out during the interference "
                             "measurement; the subscore is excluded")
            return self._subscore(task, "interference", 0.0, "error",
                                  {"error": _error(exc)})
        checked = int(result.get("checked") or 0)
        pairs = sorted(({"a": pair.get("a"), "b": pair.get("b"),
                         "volume_mm3": pair.get("volume_mm3")}
                        for pair in result.get("pairs") or []),
                       key=lambda pair: (pair["a"] or "", pair["b"] or ""))
        skipped = sorted(result.get("skipped_mesh") or [])
        detail = {"checked": checked, "pairs": pairs, "skipped_mesh": skipped}
        total_pairs = len(list(combinations(range(checked), 2)))
        if total_pairs == 0:
            # Fewer than two instances under a non-zero interference weight:
            # the task asked for an assembly and got none. That is a zero, not
            # a `not_applicable` — the task decides applicability, never a run.
            return self._subscore(task, "interference", 0.0, "ok",
                                  {**detail, "reason": "no_pairs"})
        # An unmeasurable pair is not a clean pair: every pair touching a
        # skipped mesh instance is counted against the candidate.
        measurable = len(list(combinations(range(checked - len(skipped)), 2)))
        clean = measurable - len(pairs)
        return self._subscore(task, "interference", clean / total_pairs, "ok",
                              detail)

    # -- metrics -----------------------------------------------------------

    def _metric_of(self, metrics: dict, key: str):
        if key in _BBOX_AXES:
            box = metrics.get("bbox") or {}
            low, high = box.get("min") or [], box.get("max") or []
            index = _BBOX_AXES[key]
            if len(low) <= index or len(high) <= index:
                return None
            return float(high[index]) - float(low[index])
        if key in _COM_AXES:
            com = metrics.get("center_of_mass") or []
            index = _COM_AXES[key]
            return None if len(com) <= index else float(com[index])
        return metrics.get(key)

    def _metrics(self, task: Task, builds: dict) -> dict:
        if not self._scored(task, "metrics"):
            return self._not_applicable(task, "metrics")
        if task.metrics_path is None:
            return self._subscore(task, "metrics", 0.0, "error",
                                  {"reason": "no_window_document"})
        try:
            windows = load_windows(task.metrics_path)
        except (AppError, OSError) as exc:
            return self._subscore(task, "metrics", 0.0, "error",
                                  {"error": _error(exc)})
        if not windows:
            return self._subscore(task, "metrics", 0.0, "error",
                                  {"reason": "no_windows"})
        if any(builds.get(window.part, {}).get("state") == "error"
               for window in windows):
            return self._subscore(
                task, "metrics", 0.0, "error",
                {"reason": "build_error",
                 "parts": sorted({window.part for window in windows
                                  if builds.get(window.part,
                                                {}).get("state") == "error"})})
        failed, passed = [], 0
        for window in windows:
            build = builds.get(window.part) or {}
            metrics = (build.get("result") or {}).get("metrics") or {}
            measured = (self._metric_of(metrics, window.metric)
                        if build.get("state") == "ok" else None)
            if measured is not None and not _finite(measured):
                # A non-finite measurement is not a number: `allow_nan=False`
                # would refuse to serialise it, and a bare `NaN` literal is not
                # JSON any strict parser accepts.
                return self._subscore(
                    task, "metrics", 0.0, "error",
                    {"reason": "non_finite_measurement",
                     "window": window.name, "metric": window.metric})
            if measured is not None and self._satisfied(window, measured):
                passed += 1
                continue
            failed.append({"name": window.name,
                           "measured": (float(measured)
                                        if measured is not None else None),
                           "min": window.min, "max": window.max})
        failed.sort(key=lambda row: row["name"])
        return self._subscore(task, "metrics", passed / len(windows), "ok",
                              {"passed": passed, "total": len(windows),
                               "failed": failed})

    def _satisfied(self, window, measured: float) -> bool:
        """Inclusive on both bounds, with `specs._slack`'s tolerance.

        A measurement is never exact to the last ulp, and a window authored as
        `max: 6.05` means 6.05 is inside it.
        """
        value = float(measured)
        if window.min is not None and value < window.min - _slack(window.min):
            return False
        if window.max is not None and value > window.max + _slack(window.max):
            return False
        return True


__all__ = ["SCORE_SCHEMA", "IOU_TIMEOUT_S", "SUBSCORE_STATUSES",
           "BLOCK_HEADER", "Scorer", "inject_rubric", "refuse_scoring_overlap",
           "total_of"]
