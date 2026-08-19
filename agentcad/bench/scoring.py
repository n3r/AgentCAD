"""The bench scorer: six subscores, one copy, one deterministic `score.json`.

Design §3, §4 and §6. Three rules carry the whole module and every one of them
is a rule about *honesty*, not about arithmetic:

1. **Nothing is measured in place.** A submission is copied into a work
   cell and opened through a muzzled ephemeral service
   (`checks._ephemeral_service`), so the candidate's directory is untouched
   *by construction*: no history commit, no `.history/agentcad/` sidecar, no
   materialised branch tree, and `.cache/` lands in the cell. The run removes
   only the cell it created.

2. **`error` is the HARNESS failing to measure. Everything the candidate
   caused is a measured zero.** `not_applicable` is declared by `task.json`
   (weight 0) and never by a run. A candidate that is absent, broken, deleted,
   mesh-only, unbuildable or simply wrong is *measured*, and it measures
   **zero** with `status: "ok"` and a `reason`. Only budget truncation, a
   kernel that is gone, and a genuinely unexpected exception class are `error`.

   Getting this backwards is the one exploit this whole module has to close.
   Excluded subscores are renormalised away, so a run-decided exclusion lets a
   candidate **raise its total by destroying evidence**: delete the part
   script, break the assembly, hand back an STL. Every `error` arm below is
   therefore guarded by :func:`_blames_harness`, and every candidate-caused
   failure names itself in `detail["reason"]` instead.

3. **The rubric is injected into the copy, it RE-BINDS `SPECS`, only its own
   rows are scored, and a skip the candidate induced is a failure.**
   Whatever the candidate declared for itself is discarded, because the last
   module-level binding wins; `<copy>/specs.py` is replaced or **deleted** so
   a candidate-authored project block never scores;
   and the `specs` denominator counts only the rows the injected blocks own, so
   a candidate cannot dilute a failing rubric with ten filler parts that each
   declare a trivially-true check of their own. A `skip` normally leaves the
   denominator ("we did not measure"), but :data:`CANDIDATE_SKIP_REASONS` — a
   `clearance` check skipped `mesh_only`, an `interference_free` check skipped
   `no_instances` — is the candidate making a declared check unmeasurable, and
   it is scored as a failure for the same reason rule 2 exists.

The module is OCP-free by contract: it never imports build123d or OCP, and the
one mesh question it has to ask (`is this side an STL?`) is answered by suffix
here rather than by importing `kernel.refload`, which does import build123d.

`score.json` carries **no timestamp, no host, no path, no duration and no
client id** — the packages-provenance rule. Everything non-deterministic
lives in the sibling `run.json` the runner writes (design §8.6). That, plus
`round(x, 6)` applied recursively before serialisation, is the whole of AC3.
"""
from __future__ import annotations

import shutil
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

import agentcad

from ..core.checks import (_ephemeral_service, _payload, _within,
                           default_work_root, refuse_work_dir_overlap)
from ..core.model import AppError, ValidationError
from ..core.specs import _slack
from ..kernel.client import KernelError
from ..kernel.protocol import (ERROR_CONTRACT, ERROR_CRASH, ERROR_SCRIPT,
                               ERROR_TIMEOUT)
from . import HARNESS_VERSION
from ._json import is_finite_number as _finite
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

#: `specs` skip reasons the **candidate** induced, which the bench scores as
#: failures (design §4.3's skip rule, narrowed by an orchestrator ruling).
#:
#: A `skip` is normally "we did not measure", and PRD-003 keeps such rows out
#: of every fraction — right, when the reason is the machine. Two of
#: `core/specs.py`'s reasons are not about the machine at all:
#:
#: * ``mesh_only`` (`specs.py:1170`) — a `clearance` check whose side is an
#:   imported mesh. The candidate chose to hand back a mesh.
#: * ``no_instances`` (`specs.py:1120`) — a project-scope `interference_free`
#:   or `clearance` check with fewer than two instances placed. The candidate
#:   chose not to build the assembly the rubric measures.
#:
#: Left out of the fraction, either one **pays** a candidate for making a
#: declared check unmeasurable — the same exploit `_blames_harness` closes one
#: level up. So they are counted, and counted as failures.
#:
#: The rest stay out, because none of them is the candidate's doing:
#: ``fem_extra_missing`` is this machine without the `[fem]` extra,
#: ``unsupported_scope`` is the *rubric author* declaring a part-scope check at
#: project scope, and ``deferred`` is a rebuild-tier row a full run never
#: emits. (A budget row is an `error`, not a skip — `specs._budget_row`.)
CANDIDATE_SKIP_REASONS = ("mesh_only", "no_instances")

#: The kernel failures that are **ours**. A timeout is the budget or a hung
#: worker; a crash is a worker that is gone. Everything else a worker reports —
#: a script that raised, a contract the item did not meet, an OCCT failure
#: building the candidate's own geometry — is the candidate being wrong, and a
#: candidate being wrong is a zero, never an exclusion.
_HARNESS_KERNEL_ERRORS = (ERROR_TIMEOUT, ERROR_CRASH)

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

#: Everything a candidate's own files can throw on the way into the cell —
#: **and it is a wide net on purpose**, because every one of these was measured
#: escaping `score()` as a traceback:
#:
#: * a non-UTF-8 byte in a part script is a `UnicodeDecodeError`, which is a
#:   `ValueError` and **not** an `OSError`;
#: * a `parts/<id>.py` that is a directory or a dangling symlink comes out of
#:   `copytree` as `shutil.Error`;
#: * a submission that is not a project at all is an `AppError`;
#: * a `project.json` that is valid JSON but structurally wrong throws from
#:   whichever reader trusts it first — `parts: [{"label": "a"}]` is a
#:   `KeyError` out of `store.part_ids`, `assembly: "nope"` an `AttributeError`
#:   inside `store.open`. `ProjectStore` validates that the document parses and
#:   names a project; it does not validate the shape of everything under it.
#:
#: The width costs us the ability to tell a harness bug in `_prepare` from a
#: malformed submission, and that trade is deliberate: it buys the guarantee
#: that no manifest a candidate can author is worth an `error` — which,
#: renormalised away, is worth points (rule 2). The net covers `_prepare`
#: only; the measurement itself still classifies with `_blames_harness`.
_PREPARE_FAILURES = (AppError, OSError, shutil.Error, KeyError, TypeError,
                     AttributeError, ValueError)

#: The subscores that read a build result. Nothing outside this tuple can make
#: the scorer build a part.
_BUILD_READERS = ("built", "valid", "geometry", "metrics")

#: How a metric key is read off a build result's `metrics`. `volume_mm3` and
#: friends are top-level; the six derived ones come out of `bbox` and
#: `center_of_mass`, which is the whole reason these tables exist.
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
                f"input — pass a directory elsewhere, or omit --work-dir for "
                f"a temp dir",
                {"work_dir": str(resolved), "path": str(path)})


# ------------------------------------------------------- rubric injection

def inject_rubric(task: Task, copy_root: Path) -> list[str]:
    """Write the task's rubric into the **copy**; return the parts it touched.

    The project-scope block replaces `<copy>/specs.py` outright — and when the
    task ships none, the file is **deleted**. That deletion is load-bearing: a
    candidate that authors its own `specs.py` would otherwise have its
    self-declared project checks evaluated, and (before the ownership filter in
    :meth:`Scorer._specs`) scored. The rubric is the whole of what is scored,
    on every submission, or it is not a rubric.

    A part block is **appended** to the candidate's script, behind
    :data:`BLOCK_HEADER`, and it re-binds `SPECS` — the last module-level
    binding wins, so the candidate's own declarations are dropped (design
    §3.1).

    A `specs.parts` entry naming a part the copy does not have is **not** an
    error and not a skip that hides anything: it is a missing part, which the
    `built` subscore already reports as a zero, and its absence from the
    returned list is what stops its rows counting for anyone.
    """
    copy_root = Path(copy_root)
    project_block = copy_root / "specs.py"
    if task.specs_project_path is not None:
        project_block.write_text(
            task.specs_project_path.read_text(encoding="utf-8"),
            encoding="utf-8")
    else:
        project_block.unlink(missing_ok=True)
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


def metric_of(metrics: dict, key: str):
    """One metric key off a build result's ``metrics``, or ``None``.

    ``None`` means *this build does not carry that number* — a window on it is
    unsatisfied, which is the honest answer and not an error.
    """
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
    value = metrics.get(key)
    # A non-finite measurement is not a number, and "we have no number" is the
    # honest answer — `allow_nan=False` would refuse to serialise the other.
    return value if value is None or _finite(value) else None


def window_satisfied(window, measured) -> bool:
    """Inclusive on both bounds, with `specs._slack`'s tolerance.

    A measurement is never exact to the last ulp, and a window authored as
    ``max: 6.05`` means 6.05 is inside it. A missing measurement is never
    satisfied — "we have no number" is not "the number is fine".
    """
    if measured is None or not _finite(measured):
        return False
    value = float(measured)
    if window.min is not None and value < window.min - _slack(window.min):
        return False
    if window.max is not None and value > window.max + _slack(window.max):
        return False
    return True


def interference_fraction(checked: int, pair_count: int,
                          skipped: int) -> float:
    """``clean_pairs / C(checked, 2)`` — the whole of the `interference` value.

    Two rules, both deliberate:

    * **An unmeasurable pair is not a clean pair.** Every pair touching an
      instance the kernel skipped as a mesh counts against the candidate, or
      exporting one STL into an assembly would launder an overlap into a pass.
    * **Fewer than two instances is 0.0, not "not applicable".** The task
      weighted `interference` above zero, which means it asked for an assembly;
      handing back one part (or none) fails to answer, and applicability is the
      task's call and never the run's.
    """
    total_pairs = checked * (checked - 1) // 2
    if total_pairs <= 0:
        return 0.0
    measurable_n = max(0, checked - skipped)
    measurable = measurable_n * (measurable_n - 1) // 2
    clean = measurable - pair_count
    return max(0.0, min(1.0, clean / total_pairs))


def _blames_harness(exc: BaseException) -> bool:
    """Whether *exc* is **us** failing to measure, rather than the candidate
    being wrong.

    The single most consequential predicate in the module (rule 2). A
    `KernelError` is ours only when it is a timeout (the budget, or a hung
    worker) or a crash (the worker is gone); a script error, a contract error
    and an OCCT failure over the candidate's own geometry are all facts about
    the candidate. An `AppError` — `NotFoundError` for a deleted script,
    `ValidationError` for a manifest that does not agree with itself — is
    always the candidate's. Anything else is an exception class we did not
    anticipate, and an unanticipated exception is ours until it is understood.
    """
    if isinstance(exc, KernelError):
        return exc.type in _HARNESS_KERNEL_ERRORS
    return not isinstance(exc, AppError)


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
    """Replace each ``(path, token)`` needle in every string of *value*.

    The last line of the AC3 defence, and the only one that can catch a path a
    message we did not write put there: `ProjectStore._read_manifest`'s refusal
    is literally ``f"{path} is not a project"`` and *path* is this run's
    randomly-named cell, `load_windows` names the task tree in an `OSError`,
    and the `iou` handler's ``iou unavailable: …`` can wrap a `refload` failure
    naming the reference STEP. Two runs would differ in exactly the strings a
    reader has no use for — and the other three are paths the design's rule 5
    forbids even when they are stable.

    Longest needle first, so a submission nested inside the projects root is
    labelled as the submission rather than half-rewritten as the root.
    """
    ordered = tuple(sorted(((str(path), token) for path, token in needles
                            if str(path)),
                           key=lambda pair: len(pair[0]), reverse=True))
    return _apply_needles(value, ordered)


def _apply_needles(value, ordered: tuple):
    if isinstance(value, str):
        for needle, token in ordered:
            value = value.replace(needle, token)
        return value
    if isinstance(value, dict):
        return {key: _apply_needles(item, ordered)
                for key, item in value.items()}
    if isinstance(value, list):
        return [_apply_needles(item, ordered) for item in value]
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
            try:
                # The copy and the injection are INSIDE the guard: they read
                # the candidate's own files, and a candidate's own files are
                # not required to be readable.
                service, proj, injected = self._prepare(cell, task, submission)
            except _PREPARE_FAILURES as exc:
                # A submission we cannot even open is a candidate that measures
                # zero — never a harness error, or an agent that produced
                # nothing at all would outscore one that produced something
                # wrong (rule 2).
                notes.append(f"the submission could not be opened as an "
                             f"AgentCAD project: {_error(exc)['message']}")
                subscores = {name: self._zeroed(task, name, "unreadable")
                             for name in SUBSCORES}
            else:
                subscores = self._measure(service, task, proj, injected,
                                          deadline, notes)
            # Both spellings of the cell: `mkdtemp` hands back the path it was
            # given a parent for, while everything downstream resolves it, and
            # on macOS those differ (`/var/…` vs `/private/var/…`).
            needles = ((cell.resolve(), "<cell>"), (cell, "<cell>"),
                       (task.root, "<task>"), (submission, "<submission>"),
                       (projects_root, "<projects>"))
            return _scrub(self._document(task, subscores, notes), needles)
        finally:
            shutil.rmtree(cell, ignore_errors=True)

    def _prepare(self, cell: Path, task: Task, submission: Path):
        """``(muzzled service, project name, injected part ids)``.

        Every step here can fail on the candidate's own bytes, so the whole
        thing is one guarded unit and its failure is a zeroed score.
        """
        tree = cell / "candidate" / task.target_project
        shutil.copytree(submission, tree,
                        ignore=shutil.ignore_patterns(*COPY_IGNORE))
        injected = inject_rubric(task, tree)
        # NON-NEGOTIABLE, all three nullings inside `_ephemeral_service`: a
        # `project_changed` publish would commit a history snapshot, a live
        # `branch_resolver` would write a `.history/agentcad/` sidecar into
        # the copy, and `write_guard` would materialise a branch tree.
        service, _registry, proj = _ephemeral_service(cell, tree,
                                                      self.service.kernel)
        # Probe the manifest's SHAPE once, here, inside the guard. `store.open`
        # only checks that `project.json` parses and carries a name, so a
        # structurally wrong document survives it and detonates in the first
        # reader that trusts it — historically at `_build_all`'s `part_ids`,
        # which sits outside every guard there is.
        service.store.part_ids(proj)
        return service, proj, injected

    def _measure(self, service, task: Task, proj: str, injected: list,
                 deadline, notes) -> dict:
        """The six subscores over one prepared, muzzled cell.

        The builds happen **once**, here, and every subscore reads the same
        results: injection is already done, so one build per part serves
        `built`, `valid`, `geometry` and `metrics` alike.
        """
        # A build is a kernel call, and §4.7 is literal: a subscore the task
        # zeroed is never measured. When no subscore reads a build result, no
        # part is built at all.
        builds = (self._build_all(service, task, proj, deadline, notes)
                  if any(self._scored(task, name) for name in _BUILD_READERS)
                  else {})
        return {
            "built": self._built(task, builds),
            "valid": self._valid(task, builds),
            "specs": self._specs(service, task, proj, injected, deadline,
                                 notes),
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
        if not self._scored(task, name):
            return self._not_applicable(task, name)
        return self._subscore(task, name, 0.0, "ok", {"reason": reason})

    def _not_applicable(self, task: Task, name: str) -> dict:
        return {"value": 0.0, "weight": 0.0, "status": "not_applicable",
                "detail": {"reason": "weight_zero"}}

    def _scored(self, task: Task, name: str) -> bool:
        """Whether this subscore is measured at all.

        A zero weight short-circuits **before any measurement** — no kernel
        call, no `check_interference`, no spec run, no build. The task decides;
        a run never does (design §4.7).
        """
        return bool(task.weights.get(name))

    # -- built / valid -----------------------------------------------------

    def _build_all(self, service, task: Task, proj: str, deadline=None,
                   notes: list | None = None) -> dict:
        """One entry per target part: what happened when we built it.

        ``state`` is ``"ok"`` (built), ``"failed"`` (the candidate's doing —
        not in the manifest, no script on disk, a script that raised, a build
        that timed out, geometry OCCT could not make) or ``"error"`` (**ours**
        — budget truncation, a dead worker, an exception class we did not
        anticipate).

        The split is `_blames_harness`, and it is the fix for the sharpest
        version of the exploit rule 2 exists for: deleting `parts/<id>.py`
        makes `_ensure_built` raise `NotFoundError` out of `store.read_script`,
        which as a bare `except Exception` marked four subscores `error` and
        renormalised the candidate's whole score onto the one subscore left.

        **A build never raises a `KernelError`** — `service._build_with`
        catches it and answers ``{"ok": False, "error": payload}`` — so the
        classification for a build is made *here*, from the payload's type,
        rather than by `_blames_harness` (which still governs the defensive
        `except` below, and the kernel calls the other subscores make
        themselves):

        * **a timeout is the candidate's**, and it is named `build_timeout`
          rather than folded into `build_failed`. Nothing here shortens the
          build's own ceiling, so with no ``--budget`` in force the timeout is
          a fact about how long the candidate's script takes to run — a
          measured zero, exactly like a script that raises. The one exception
          is a ``--budget`` that has already expired: that timeout is *our*
          deadline truncating the measurement (`checks._budget_broke`'s rule,
          restated for a call whose ceiling we do not own), and truncation is
          an `error` everywhere else in this module.
        * **a crash is ours**: rule 2 names "a kernel that is gone" as a
          harness failure, and `_blames_harness` already answers it that way
          for `iou` and `check_interference`.
        """
        try:
            present = set(service.store.part_ids(proj))
        except Exception as exc:  # noqa: BLE001 — belt and braces behind
            # `_prepare`'s probe: a manifest we cannot even enumerate is a
            # candidate with no parts, never a subscore nobody scores.
            return {part_id: {"state": "failed", "result": None,
                              "reason": _error(exc)["type"]}
                    for part_id in task.target_parts}
        out: dict[str, dict] = {}
        for part_id in task.target_parts:
            if part_id not in present:
                out[part_id] = {"state": "failed", "result": None,
                                "reason": "missing_from_manifest"}
                continue
            try:
                result = service._ensure_built(proj, part_id)
            except Exception as exc:  # noqa: BLE001 — `_ensure_built` turns a
                # KernelError into `ok: false` already; this is
                # `CheckRunner._build_item`'s defensive edge (a deleted script,
                # an unreadable import), which is the candidate's doing unless
                # `_blames_harness` says otherwise.
                payload = _error(exc)
                if _blames_harness(exc):
                    out[part_id] = {"state": "error", "result": None,
                                    "error": payload}
                else:
                    out[part_id] = {"state": "failed", "result": None,
                                    "reason": payload["type"],
                                    "error": payload}
                continue
            if result.get("ok"):
                out[part_id] = {"state": "ok", "result": result,
                                "reason": None}
                continue
            out[part_id] = self._failed_build(result, part_id, deadline, notes)
        return out

    def _failed_build(self, result: dict, part_id: str, deadline,
                      notes: list | None) -> dict:
        """One not-``ok`` build result, classified. See :meth:`_build_all`."""
        error = result.get("error") or {}
        # Trimmed to `_error`'s two keys: a `KernelError` payload's `details`
        # holds a traceback, a traceback names files, and a file name in
        # `score.json` is both a path leak and a determinism break.
        payload = {"type": error.get("type"),
                   "message": error.get("message") or ""}
        if error.get("type") == ERROR_CRASH:
            return {"state": "error", "result": None, "error": payload}
        if error.get("type") == ERROR_TIMEOUT:
            remaining = self._remaining(deadline)
            if remaining is not None and remaining <= 0.0:
                if notes is not None:
                    notes.append("the budget ran out while a target part was "
                                 "building; the build-derived subscores are "
                                 "excluded")
                return {"state": "error", "result": None, "error": payload}
            return {"state": "failed", "result": result,
                    "reason": "build_timeout", "error": payload}
        return {"state": "failed", "result": result, "reason": "build_failed"}

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
        return self._subscore(
            task, "built", value, "ok",
            {"parts": total, "failed": failed,
             "reasons": {part: builds[part].get("reason") for part in failed}})

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

    def _owned_rows(self, report: dict, task: Task, injected: list) -> list:
        """Only the rows the **injected rubric** owns.

        Read off `report["parts"][pid]["checks"]` and
        `report["project_checks"]["checks"]` rather than parsed out of the id
        prefix, so a part legitimately named `project` cannot be mistaken for
        project scope.

        Without this filter the denominator is every declared check in the
        project, and a candidate dilutes a failing rubric by adding parts that
        each declare a trivially-true check of their own: measured on the seed,
        nine such fillers moved a real 0.667 to 0.917.
        """
        owned: set = set()
        parts = report.get("parts") or {}
        for part_id in injected:
            owned.update((parts.get(part_id) or {}).get("checks") or [])
        if task.specs_project_path is not None:
            owned.update((report.get("project_checks") or {}).get("checks")
                         or [])
        return [row for row in report.get("checks") or []
                if row.get("id") in owned]

    def _specs(self, service, task: Task, proj: str, injected: list, deadline,
               notes: list) -> dict:
        """One `SpecRunner.run` over the injected rubric, scoring only the
        rows that rubric owns.

        `service.specs` is read **here**, never captured in `__init__`: the
        runner belongs to the ephemeral service `build_registry` just built,
        and that is `CheckRunner._stage_specs`' rule verbatim.

        **The report is never embedded** — `_report` stamps a `generated`
        timestamp, and one timestamp anywhere in the body would end AC3.
        """
        if not self._scored(task, "specs"):
            return self._not_applicable(task, "specs")
        declared = bool(task.specs_part_paths) or \
            task.specs_project_path is not None
        if not declared:
            # The task weights `specs` and ships no rubric. The loader refuses
            # that bundle, so reaching here is an authoring defect and ours.
            return self._subscore(task, "specs", 0.0, "error",
                                  {"reason": "no_rubric_declared"})
        attached = bool(injected) or task.specs_project_path is not None
        if not attached:
            # The rubric exists and none of it attached: the candidate has no
            # `parts/<id>.py` to append to — it deleted the part, or handed
            # back a mesh reference part. That is the candidate's doing and it
            # is a zero. As an `error` it was the same exploit as Critical 2:
            # a mesh-only candidate measured 0.1875 and scored 0.2083.
            return self._subscore(task, "specs", 0.0, "ok",
                                  {"reason": "no_rubric_attached",
                                   "passed": 0, "failed": [], "skipped": [],
                                   "errors": [], "total": 0})
        runner = getattr(service, "specs", None)
        if runner is None:
            return self._subscore(task, "specs", 0.0, "error",
                                  {"reason": "specs_unavailable"})
        try:
            report = runner.run(proj, deadline=deadline)
        except Exception as exc:  # noqa: BLE001 — classified, never blanket.
            if not _blames_harness(exc):
                # The run refused over the candidate's own tree — a part whose
                # script file it deleted is a `NotFoundError` straight out of
                # `store.read_script`. Deleting a file may not buy an
                # exclusion, so it is a zero with the refusal named.
                return self._subscore(task, "specs", 0.0, "ok",
                                      {"reason": "spec_run_refused",
                                       "error": _error(exc), "passed": 0,
                                       "failed": [], "skipped": [],
                                       "errors": [], "total": 0})
            # The rubric attached and the run is OURS to complete, so a
            # timeout or a dead worker is exactly what `error` is for.
            return self._subscore(task, "specs", 0.0, "error",
                                  {"error": _error(exc)})
        rows = self._owned_rows(report, task, injected)
        buckets: dict[str, list] = {"pass": [], "fail": [], "skip": [],
                                    "error": []}
        induced: list = []
        for row in rows:
            status = row.get("status")
            if status == "skip" and \
                    row.get("reason") in CANDIDATE_SKIP_REASONS:
                # The candidate made a declared check unmeasurable: a failure,
                # not a row that quietly leaves the denominator.
                status = "fail"
                induced.append(row.get("id"))
            buckets.setdefault(status, []).append(row.get("id"))
        # `filter(None, …)`: `assign_ids` stamps every row, but a `None` in a
        # list being sorted against strings is a TypeError, and one malformed
        # row may not be the end of a whole score.
        detail = {
            "passed": len(buckets["pass"]),
            "failed": sorted(filter(None, buckets["fail"])),
            "skipped": sorted(filter(None, buckets["skip"])),
            "errors": sorted(filter(None, buckets["error"])),
            "total": len(rows),
        }
        if induced:
            # Named separately so a reader can tell a measured failure from a
            # check the candidate made unmeasurable.
            detail["skipped_as_failed"] = sorted(filter(None, induced))
        # A skip is "we did not measure", so it is out of the denominator
        # entirely: scoring it as a pass would launder a machine-specific gap
        # into a green, and scoring it as a fail would punish a candidate for
        # this machine's missing extra. PRD-003's own rule.
        denominator = (len(buckets["pass"]) + len(buckets["fail"])
                       + len(buckets["error"]))
        if denominator == 0:
            # The rubric attached and produced nothing measurable — the part
            # did not build, or every row skipped. Still the candidate's.
            return self._subscore(task, "specs", 0.0, "ok",
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
        """Per part, and the mean over **every** target part.

        `detail` is `{"parts": {...}}` — nested, so a part legitimately named
        `error` cannot collide with the `error` key an excluded subscore adds.
        A mesh-only part contributes 0.0 like any other unmeasurable part;
        `status` is `skipped_mesh` only when *every* target part is mesh-only,
        because one STL among four modelled parts is a fact about one part.
        """
        if not self._scored(task, "geometry"):
            return self._not_applicable(task, "geometry")
        parts = sorted(task.target_parts)
        if not any(part in task.reference_steps for part in parts):
            # The loader refuses a task that weights `geometry` and names no
            # STEP, so this is an authoring defect and therefore ours.
            return self._subscore(task, "geometry", 0.0, "error",
                                  {"reason": "no_reference_steps",
                                   "parts": {}})
        rows: dict = {}
        scores: list[float] = []
        mesh: list[str] = []
        for part_id in parts:
            row, failure = self._geometry_part(service, task, proj, builds,
                                               part_id, deadline, notes)
            if failure is not None:
                return self._subscore(task, "geometry", 0.0, "error",
                                      {"parts": rows, "error": failure})
            rows[part_id] = row
            scores.append(float(row.get("iou") or 0.0))
            if row.get("reason") == "candidate_is_a_mesh" or \
                    row.get("skipped_mesh"):
                mesh.append(part_id)
        detail: dict = {"parts": rows}
        if mesh:
            detail["skipped_mesh"] = sorted(mesh)
        status = "skipped_mesh" if len(mesh) == len(parts) else "ok"
        return self._subscore(task, "geometry", sum(scores) / len(scores),
                              status, detail)

    def _geometry_part(self, service, task: Task, proj: str, builds: dict,
                       part_id: str, deadline, notes: list):
        """``(row, harness failure or None)`` for one part's IoU."""
        if part_id not in task.reference_steps:
            return {"iou": 0.0, "reason": "no_reference_step"}, None
        build = builds.get(part_id) or {}
        if build.get("state") == "error":
            # The build was a HARNESS failure (a dead worker, or a deadline
            # that expired mid-build): there is no candidate shape, and 0.0
            # would report a measurement nobody took. `built`, `valid` and
            # `metrics` all answer `error` for the same row.
            error = build.get("error") or {}
            return None, {"type": error.get("type") or "kernel_error",
                          "message": error.get("message")
                          or "the target part's build failed in the harness",
                          "stage": "build", "part": part_id}
        if build.get("state") != "ok":
            # Not `error`: a candidate that did not build is wrong, and a wrong
            # candidate is measured at zero (rule 2).
            return ({"iou": 0.0,
                     "reason": build.get("reason") or "build_failed"}, None)
        try:
            item = self._candidate_item(service, proj, part_id)
        except AppError as exc:
            return {"iou": 0.0, "reason": _error(exc)["type"]}, None
        source = item.get("source")
        if source and Path(source).suffix.lower() in MESH_SUFFIXES:
            # Zero and INCLUDED, never excluded: being handed a mesh when a
            # model was asked for is a fact about the candidate (FR5/AC4).
            return {"iou": 0.0, "reason": "candidate_is_a_mesh"}, None
        timeout_s, remaining = self._timeout(deadline, IOU_TIMEOUT_S)
        try:
            result = service.kernel.request(
                "iou",
                {"candidate": item,
                 "reference": {"source": str(task.reference_steps[part_id])},
                 "align": task.frame.align,
                 "rotations_deg": ([list(rot) for rot
                                    in task.frame.rotations_deg]
                                   or [[0.0, 0.0, 0.0]])},
                timeout_s=timeout_s, affinity=task.id)
        except KernelError as exc:
            if exc.type in (ERROR_SCRIPT, ERROR_CONTRACT):
                # The candidate's own script or item is wrong: a zero.
                return {"iou": 0.0, "reason": exc.type}, None
            # A timeout, a dead worker, or an OCCT boolean that failed over a
            # shape that BUILT fine: the measurement is ours and it did not
            # happen (design §4.4, FR7).
            if self._budget_broke(exc, remaining, IOU_TIMEOUT_S):
                notes.append("the budget ran out during the geometry "
                             "measurement; the subscore is excluded")
            return None, {"type": exc.type, "message": exc.message,
                          "stage": (exc.details or {}).get("stage"),
                          "part": part_id}
        if result.get("status") == "skipped_mesh":
            return ({"iou": 0.0, "reason": "candidate_is_a_mesh",
                     "skipped_mesh": result.get("skipped_mesh")}, None)
        iou = result.get("iou")
        if not _finite(iou):
            return None, {"type": "kernel_error", "part": part_id,
                          "message": "iou returned a non-finite value",
                          "stage": "intersect"}
        return ({key: result.get(key) for key in
                 ("iou", "intersection_mm3", "union_mm3",
                  "candidate_volume_mm3", "reference_volume_mm3", "align",
                  "rotation_deg")}, None)

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
        except Exception as exc:  # noqa: BLE001 — classified, never blanket.
            if not _blames_harness(exc):
                # The candidate's OWN assembly would not resolve: a part that
                # raises, an instance naming a part that is gone, a mate that
                # cannot close. Measured, and it measures zero — as a blanket
                # `error` this was worth 0.2 of a total on the seed.
                return self._subscore(task, "interference", 0.0, "ok",
                                      {"reason": "assembly_unresolved",
                                       "error": _error(exc), "checked": 0,
                                       "pairs": [], "skipped_mesh": []})
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
        if checked < 2:
            detail["reason"] = "no_pairs"
        return self._subscore(
            task, "interference",
            interference_fraction(checked, len(pairs), len(skipped)), "ok",
            detail)

    # -- metrics -----------------------------------------------------------

    def _metrics(self, task: Task, builds: dict) -> dict:
        if not self._scored(task, "metrics"):
            return self._not_applicable(task, "metrics")
        if task.metrics_path is None:
            return self._subscore(task, "metrics", 0.0, "error",
                                  {"reason": "no_window_document"})
        try:
            windows = load_windows(task.metrics_path)
        except (AppError, OSError) as exc:
            # The window document is the TASK's, so an unreadable one is an
            # authoring defect and ours.
            return self._subscore(task, "metrics", 0.0, "error",
                                  {"error": _error(exc)})
        if not windows:
            return self._subscore(task, "metrics", 0.0, "error",
                                  {"reason": "no_windows"})
        broken = sorted({window.part for window in windows
                         if (builds.get(window.part) or {}).get("state")
                         == "error"})
        if broken:
            # Only a HARNESS build failure gets here: `_build_all` marks
            # everything the candidate caused `failed`, and a failed part's
            # windows are simply unsatisfied.
            return self._subscore(task, "metrics", 0.0, "error",
                                  {"reason": "build_error", "parts": broken})
        failed, passed = [], 0
        for window in windows:
            build = builds.get(window.part) or {}
            metrics = (build.get("result") or {}).get("metrics") or {}
            measured = (metric_of(metrics, window.metric)
                        if build.get("state") == "ok" else None)
            if measured is not None and not _finite(measured):
                # A non-finite measurement is not a number: `allow_nan=False`
                # would refuse to serialise it, and a bare `NaN` literal is not
                # JSON any strict parser accepts. It is also not a *window*
                # failure — it is a measurement we do not have — so the window
                # is unsatisfied and the number never enters the document.
                measured = None
            if window_satisfied(window, measured):
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


__all__ = ["SCORE_SCHEMA", "IOU_TIMEOUT_S", "SUBSCORE_STATUSES",
           "CANDIDATE_SKIP_REASONS",
           "BLOCK_HEADER", "Scorer", "inject_rubric", "interference_fraction",
           "metric_of", "refuse_scoring_overlap", "total_of",
           "window_satisfied"]
