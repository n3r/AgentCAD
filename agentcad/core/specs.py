"""``SpecRunner``: three evaluation tiers, one report, and the result cache.

Declaration is data (``agentcad/toolkit/specs.py``) and measurement is the
kernel's job (``agentcad/kernel/handlers/specs.py``); this module is the
orchestration between them — the review packet's discipline applied one layer
up. **It imports no geometry kernel**: every measurement is a
``service.kernel.request``, so predicates and part scripts run only in the
confined worker, never in the server process.

The three tiers (design Decision 3) and what each one costs:

===========  =========================================  =====================
tier         checks                                     when
===========  =========================================  =====================
1 — shape    valid, mass, volume, bbox, wall, that      every rebuild
2 — assembly interference_free, clearance, stackup      ``run`` only
3 — expensive fem_static                                ``run`` only
===========  =========================================  =====================

A rebuild is what an engineer does while dragging a slider, so tiers 2 and 3
are reported there as ``{"status": "skip", "reason": "deferred"}`` — visible
and named, never silent.

Four rules this file is written around:

* **Zero added work for a part that declares nothing.** :func:`declares_specs`
  is an ``ast.parse`` presence scan, memoized by content hash; a spec-less part
  reaches no kernel call at all, and :meth:`SpecRunner.tier1` returns ``None``
  ("none declared", which is not "not evaluated").
* **The cache key already covers specs.** ``SPECS`` lives in the script text,
  which is what ``service._cache_key_for`` hashes, so
  ``.cache/<cache_key>.specs.json`` sits beside ``.metrics.json`` and is
  invalidated for free. A corrupt sidecar is discarded and recomputed, never
  raised (the ``metrics.json`` precedent). **A failed evaluation is cached
  too** — see :meth:`SpecRunner._shape_tier`: the same script and params
  produce the same ``contract_error``, and the UI re-reads a part on every
  ``rebuild_finished``, so caching only the successes made every read pay a
  fresh ``spec_eval``. ``run_specs`` is the one surface that ignores a cached
  failure.
* **The report degrades, it never raises.** A failing check, a broken
  predicate, an unknown instance id, a ``specs.py`` that will not execute and a
  ``KernelError`` mid-measurement are all *payload*. The only things that raise
  are the ordinary argument errors: ``NotFoundError`` for an unknown
  project/part/branch, ``ValidationError`` for a ref that is not a branch or a
  ref on a project with no git.
* **``service.branches`` is read inside the methods, never in ``__init__``** —
  the tool pack that constructs the runner sorts before the versioning pack, so
  the seam does not exist yet at construction time.
* **The proposal gate is fail-closed** (:meth:`SpecRunner.gate_provider`). A
  declared check that failed, errored, was never evaluated, or was *skipped for
  any reason at all* is RED, and ``allow_invalid`` does not waive it. It never
  returns ``pending`` either — PRD-002's ``merge()`` blocks only a ``fail``, so
  a source head that moved mid-evaluation is a ``fail`` that says to retry.
  That is the deliberate divergence from PRD-002's default — where a provider
  outage degrades to ``pending`` — and it is the divergence PRD-002's as-built
  note reserved for this PRD.
"""

from __future__ import annotations

import ast
import hashlib
import json
import math
import re
import time
from contextlib import contextmanager
from pathlib import Path

from ..kernel.client import KernelError
from .model import NotFoundError, ValidationError
from .project import ProjectStore
from .tools_stackup import compute_stackup

#: Sidecar format version. A stored document at any other version is discarded.
SPEC_RESULT_VERSION = 1

#: Wall-clock budget for the proposal gate. It is a **deadline**, not a
#: between-parts courtesy: every kernel call made under it — the measurements
#: through :meth:`SpecRunner._kernel`, ``check_interference``, and the mate
#: pass behind ``_resolved_instances`` — asks for the time the budget has left
#: rather than its own (120 s / 300 s / 600 s) ceiling, and the deadline is
#: re-read between checks. On exhaustion the scopes and checks that were not
#: reached are reported as ``unevaluated`` — which is RED, never a silent
#: green.
GATE_BUDGET_S = 30.0

#: Floor for a budgeted kernel timeout. A request is never issued with a
#: non-positive timeout (the caller raises :data:`_ERROR_BUDGET` first); this
#: only keeps a call that starts with milliseconds left from being a timeout by
#: construction.
_MIN_KERNEL_TIMEOUT_S = 0.5

#: ``KernelError.type`` for a call the gate budget refused to start. It travels
#: as a kernel error because that is what every caller of a measurement already
#: degrades honestly (``_residue``), and its ``details.reason`` is what
#: :meth:`SpecRunner.evaluate_specs` reads back to say *why* the gate is red.
_ERROR_BUDGET = "budget_exceeded"

#: Marker for a *cached declaration failure* in ``_declaration_cache``. A
#: leading dunder-ish name no ``spec_declare`` payload can collide with.
_DECLARE_ERROR = "__declare_error__"

#: How many entries the gate memo keeps (verdicts, plus the advisory
#: specs.py-changed flags, under commit-shaped keys). Small on purpose: an
#: entry is a whole report, and a handful covers every proposal a reviewer has
#: open. Bounded because a long-lived server would otherwise hold one report
#: per commit it ever gated.
_GATE_MEMO_LIMIT = 32

#: The synthetic kind stamped on a check the gate budget never reached. It is
#: deliberately not one of the declared kinds: nothing was measured.
_UNEVALUATED = "unevaluated"

#: Kinds the kernel measures from one built shape.
SHAPE_TIER = ("valid", "mass", "volume", "bbox", "wall", "that")
#: Kinds measured over the placed assembly.
ASSEMBLY_TIER = ("interference_free", "clearance", "stackup")
#: Kinds whose cost is unbounded.
EXPENSIVE_TIER = ("fem_static",)

# Presence-scan memo, keyed by sha256(script). Module-level because the answer
# is a property of the text alone — two runners over the same tree agree.
_DECLARES_MEMO: dict[str, bool] = {}
_MEMO_LIMIT = 512


def _now() -> str:
    """UTC, ISO-8601, zone-aware. Second resolution: a report is read by a
    human, and the trailing ``Z`` is what stops a reader from mistaking it for
    local time (``proposals._now``'s reasoning verbatim)."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ------------------------------------------------------------ presence scan

def _binds_specs(body) -> bool:
    """True iff a statement in *body* binds the module-level name ``SPECS``.

    Recurses into ``if``/``for``/``while``/``try``/``with`` — a conditionally
    or loop-built ``SPECS`` still binds the name — but never into a function or
    a class, where the binding is local and invisible to the module namespace.
    """
    for node in body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "SPECS":
                    return True
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            target = node.target
            if isinstance(target, ast.Name) and target.id == "SPECS":
                return True
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            if isinstance(node.target, ast.Name) and node.target.id == "SPECS":
                return True
        # Nested blocks: a function/class body is deliberately not one.
        for field in ("body", "orelse", "finalbody"):
            nested = getattr(node, field, None)
            if nested and not isinstance(
                    node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if _binds_specs(nested):
                    return True
        for handler in getattr(node, "handlers", []) or []:
            if _binds_specs(handler.body):
                return True
    return False


#: The fallback for a script that will not parse: a **line-anchored** binding
#: of ``SPECS``, in any of the three forms :func:`_binds_specs` recognizes
#: (``SPECS =``, ``SPECS: list =``, ``SPECS +=``). Anchoring to the start of a
#: line (whitespace only in front) is what keeps a comment (``# SPECS = …``), a
#: string literal (``x = "SPECS = …"``) and an attribute (``m.SPECS =``) out; an
#: indented match — inside a function, or inside a triple-quoted block — is
#: accepted, because there is no AST to tell the two apart and the direction to
#: err in is *declaring*. The cost of a false positive is one red row on a
#: script that already fails its build; the cost of a false negative is a
#: declared spec the gate never measures.
_SPECS_TEXT_RE = re.compile(r"^[ \t]*SPECS[ \t]*(?::[^=\n]+)?(?:\+)?=",
                            re.MULTILINE)


def declares_specs(script: str) -> bool:
    """True iff *script* binds a top-level name ``SPECS``.

    AST, never exec: the kernel worker is the only thing in this system that
    runs a part script, and a *presence* question must not become a reason to
    execute one (the rule ``packet.params_spec`` already follows for
    ``PARAMS``).

    **A script that does not parse fails closed.** It has no AST to scan, and
    answering "declares nothing" made the proposal gate classify a part that
    visibly binds ``SPECS`` as spec-less and skip it entirely — a declared
    check that never becomes red. So the text is scanned for the binding
    instead (:data:`_SPECS_TEXT_RE`); the part is then evaluated, its build
    fails, and the ``script_error`` is reported as a red row.
    """
    if not isinstance(script, str):
        return False
    key = hashlib.sha256(script.encode()).hexdigest()
    hit = _DECLARES_MEMO.get(key)
    if hit is not None:
        return hit
    try:
        tree = ast.parse(script)
    except (SyntaxError, ValueError):
        answer = bool(_SPECS_TEXT_RE.search(script))
    else:
        answer = _binds_specs(tree.body)
    if len(_DECLARES_MEMO) > _MEMO_LIMIT:
        _DECLARES_MEMO.clear()
    _DECLARES_MEMO[key] = answer
    return answer


# ------------------------------------------------- grouping, ids, summaries

def summarize(checks: list[dict]) -> dict:
    """Counts by status. ``total`` is every record, whatever its status."""
    counts = {"passed": 0, "failed": 0, "skipped": 0, "errors": 0,
              "total": len(checks)}
    key = {"pass": "passed", "fail": "failed", "skip": "skipped",
           "error": "errors"}
    for check in checks:
        slot = key.get(check.get("status"))
        if slot:
            counts[slot] += 1
    return counts


def report_status(summary: dict) -> str:
    """``green`` / ``red`` / ``skip``.

    Skips never make a report red — they are data, with a reason and a hint
    (G4). ``skip`` at report level means nothing was declared at all.
    """
    if summary["failed"] or summary["errors"]:
        return "red"
    return "skip" if summary["total"] == 0 else "green"


def group_requirements(checks: list[dict]) -> dict:
    """FR12: requirement string -> ``{status, checks: [id, …]}``.

    Only requirements at least one check carries appear — a requirement with
    zero checks does not exist to us. The string is opaque (an id or a URL) and
    is never parsed, resolved or validated against anything.
    """
    grouped: dict[str, list[dict]] = {}
    for check in checks:
        requirement = check.get("requirement")
        if not requirement:
            continue
        grouped.setdefault(requirement, []).append(check)
    out = {}
    for requirement, rows in grouped.items():
        statuses = {row.get("status") for row in rows}
        if statuses & {"fail", "error"}:
            status = "fail"
        elif "pass" in statuses:
            status = "pass"
        else:
            status = "skip"
        out[requirement] = {"status": status,
                            "checks": [row["id"] for row in rows]}
    return out


def assign_ids(records: list[dict], prefix: str, seen: set,
               warnings: list[str]) -> list[dict]:
    """Stamp ``<prefix>:<name>`` on each record, de-duplicating with ``#2``.

    The id is the join key every other section of the report uses, so a
    duplicate name must never silently merge two rows into one.
    """
    for record in records:
        base = f"{prefix}:{record['name']}"
        ident, index = base, 1
        while ident in seen:
            index += 1
            ident = f"{base}#{index}"
        if ident != base:
            warnings.append(
                f"duplicate spec name {record['name']!r} in {prefix}; the "
                f"second one is reported as {ident}")
        seen.add(ident)
        record["id"] = ident
    return records


# ---------------------------------------------------------- record helpers

#: Unit reported beside ``measured`` so a UI never has to guess. The shape-tier
#: units are the kernel pack's; these are the project tier's.
_UNITS = {"mass": "g", "volume": "mm3", "bbox": "mm", "wall": "mm",
          "clearance": "mm", "stackup": "mm", "interference_free": "mm3"}

_FEM_HINT = ("install the optional [fem] extra (gmsh, scikit-fem, meshio) to "
             "evaluate this check")
_MESH_HINT = ("a mesh (STL) reference has no B-rep faces to measure against; "
              "import the part as STEP to include it")
_SCOPE_HINT = ("a part-scope check belongs in the part script, where there is "
               "one built shape to measure")


def _slack(limit: float) -> float:
    """Comparison tolerance: a measurement is never exact to the last ulp."""
    return max(1e-9, abs(limit) * 1e-9)


def _fmt(value: float) -> str:
    return f"{value:.4g}"


def _record(declaration: dict, index: int, part: str | None) -> dict:
    """The empty record for one declaration — the kernel's ``_record`` shape,
    plus the ``part`` the service (and only the service) knows.

    ``.get`` throughout: ``is_declaration`` now validates the whole emitted
    shape, but this is the *server* process and a format drift (or a sidecar
    written by an older version) must degrade into a named row rather than a
    ``KeyError`` that becomes a 500 for the whole tool.
    """
    kind = declaration.get("kind") or "unknown"
    return {"index": index,
            "name": declaration.get("name") or f"spec_{index}",
            "kind": kind,
            "scope": declaration.get("scope") or ("part" if part else "project"),
            "part": part, "requirement": declaration.get("requirement"),
            "limit": declaration.get("limit") or {},
            "unit": _UNITS.get(kind), "measured": None,
            "location": None, "message": "", "details": {}}


def _non_finite_limit(limit: dict | None) -> str | None:
    """The name of the first non-finite bound in *limit*, or None.

    The constructors reject one where the argument is read; a hand-written
    ``SPECS`` entry reaches an evaluator directly, and NaN satisfies every
    ordered comparison — a limit that cannot fail is not a limit.
    """
    for key, value in (limit or {}).items():
        numbers = value if isinstance(value, (list, tuple)) else [value]
        for number in numbers:
            if isinstance(number, float) and not math.isfinite(number):
                return key
    return None


def _error_row(message: str, details: dict | None = None) -> dict:
    """'The check itself broke' — never a ``fail``, which means measured and
    outside limit, and never a build failure."""
    return {"status": "error", "measured": None, "message": message,
            "details": dict(details or {})}


def _skip_row(reason: str, hint: str, message: str) -> dict:
    """A named, structural inability to measure. Always carries a hint, and is
    never a failure (G4)."""
    return {"status": "skip", "reason": reason, "hint": hint,
            "measured": None, "message": message}


def _named(records: list[dict], limit: int = 3) -> str:
    """The first few check ids, so a one-line gate summary says *which*."""
    names = [str(record.get("id") or record.get("name"))
             for record in records[:limit]]
    more = len(records) - len(names)
    return ", ".join(names) + (f" (+{more} more)" if more > 0 else "")


def _gate_wording(source: str | None, verdict: dict,
                  counts: dict) -> tuple[str, str]:
    """``(state, summary)`` for the ``specs`` gate — the *only* place the
    fail-closed policy is turned into words.

    Every red summary names its exit, because the gate is a hard block: a
    measurement failure names the failing checks and says that ``allow_invalid``
    does not waive this gate, and an unmeasured one names ``run_specs``.

    **This gate never returns ``pending``.** PRD-002's ``merge()`` refuses a
    ``fail`` and nothing else, so ``pending`` — the state a source head that
    moved mid-evaluation produced — was merge-*permissive*: an external git
    process can advance a branch regardless of the turn lock, and the merge
    then landed content no verdict had ever measured. A moved head is therefore
    a ``fail`` whose summary says to retry; the verdict is not memoized, so the
    retry is a real re-evaluation. ``pending`` remains defined in PRD-002 for
    other providers; this one simply has no use for it.
    """
    status = verdict["status"]
    if status == "pending":
        return "fail", (
            f"{source!r} moved while its design specs were being evaluated, so "
            "nothing here was measured against its current head — source moved "
            "during evaluation, retry: read the proposal again")
    if status == "skip":
        return "skipped", f"{source!r} declares no design specs"
    # There are no skips left to name: _gate_row has already turned every one
    # of them into a failure, reason and all.
    if status != "red":
        return "pass", (f"{counts['passed']} of {counts['total']} design "
                        f"specs met on {source!r}")
    if verdict["reason"] == "budget_exceeded":
        return "fail", (
            f"the design specs on {source!r} were not fully evaluated within "
            f"{GATE_BUDGET_S:.0f} s, so this gate is red: run run_specs on "
            "that branch to populate the caches, then read it again")
    if verdict["errors"] and not verdict["failures"]:
        return "fail", (
            f"{counts['errors']} design spec(s) on {source!r} could not be "
            f"measured ({_named(verdict['errors'])}); an unmeasured spec is "
            "not evidence of green — run run_specs on that branch")
    return "fail", (
        f"{counts['failed']} of {counts['total']} design specs fail on "
        f"{source!r}: {_named(verdict['failures'] + verdict['errors'])}"
        ". allow_invalid does not waive this gate — fix the geometry or the "
        "spec on the source branch")


def _fem_available() -> bool:
    """Whether the optional FEM extra can run here.

    Deliberately a module-level indirection: the import is lazy (the extra is
    optional and ``core`` must import without it) and a test can flip it to
    exercise the skip path on a machine that *has* the extra.
    """
    from ..kernel.handlers.fem import fem_available

    return fem_available()


# ------------------------------------------------------------ the sidecars

def _read_sidecar(path: Path) -> dict | None:
    """A stored result document, or None when there is nothing usable.

    A corrupt or stale-format sidecar is *discarded and recomputed*, never
    raised — a crash mid-write must not make a part unreadable
    (``_rebuild``'s ``metrics.json`` precedent).
    """
    if not path.is_file():
        return None
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        stored = None
    if not isinstance(stored, dict) or stored.get("version") != SPEC_RESULT_VERSION:
        try:
            path.unlink()
        except OSError:
            pass
        return None
    return stored


def _read_error(exc: Exception) -> KernelError:
    """An unreadable ``specs.py`` as the same shape a failed declaration has.

    A ``KernelError`` because that is what every caller of a declaration
    already degrades honestly, and because the *cause* — a permission, a
    directory in its place, an unreadable encoding — belongs in the message a
    reviewer reads.
    """
    reason = getattr(exc, "strerror", None) or str(exc) or type(exc).__name__
    return KernelError("read_error",
                       f"specs.py could not be read ({reason})",
                       {"path": "specs.py", "reason": reason})


def _cached_error(payload: dict) -> KernelError:
    """A stored ``KernelError.to_payload()`` back as the exception.

    Reads only the three fields it wrote: a sidecar is a file on disk, and a
    hand-edited one must not reach ``KernelError(**anything)``.
    """
    return KernelError(str(payload.get("type") or "kernel_error"),
                       str(payload.get("message") or "spec evaluation failed"),
                       dict(payload.get("details") or {}))


def _write_sidecar(path: Path, payload: dict) -> None:
    try:
        ProjectStore._atomic_write(path, json.dumps(payload).encode())
    except OSError:
        pass          # a cache that cannot be written is a slow run, not a bug


class SpecRunner:
    """Evaluates declared specs over a project, in tiers, and reports.

    Constructed once per service by the tool pack (Slice 4) and reachable as
    ``service.specs``. Every seam it needs beyond ``store``/``kernel`` —
    ``service.branches`` above all — is read *inside* a method, because the
    pack that installs it sorts before the pack that provides them.
    """

    def __init__(self, service):
        self.service = service
        # Declarations depend on the script text alone, so one cache serves
        # every branch and every project. Named _declaration_cache because
        # service._spec_cache already means the PARAMS spec cache.
        self._declaration_cache: dict[str, dict] = {}
        # (project, ref, head) -> verdict. The gate runs on EVERY proposal_get
        # (PRD-002 caches none), so a repeated read at an unmoved head must
        # cost nothing. Insertion-ordered, so the oldest key is the LRU victim.
        self._gate_memo: dict[tuple, dict] = {}

    # ------------------------------------------------------------ paths

    def specs_path(self, proj: str) -> Path:
        """The project's ``specs.py``. ``path_of``, never ``canonical_path_of``:
        specs are authored state and ride the caller's branch."""
        return self.service.store.path_of(proj) / "specs.py"

    def project_script(self, proj: str) -> str | None:
        """The ``specs.py`` text, or None when there is no such file.

        Discovery is presence (``exists``), not convention: a second
        root-level module is not a spec file.

        **Anything at that path that cannot be read raises.** Swallowing the
        ``OSError`` made "there is no spec file" and "there is one and we could
        not read it" the same answer — the quietest possible way to lose a
        declared spec, and green in a gate. Every caller turns it into a named
        error instead. ``exists``, not ``is_file``, for the same reason: a
        *directory* named ``specs.py`` is not an absent spec file, it is an
        unreadable one (``IsADirectoryError`` on the read below).
        """
        path = self.specs_path(proj)
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8")

    # --------------------------------------------------- the specs.py writer

    def _project_state(self, proj: str, script: str | None) -> dict:
        """Post-state for the project spec file: the text and what it declares.

        A file that will not execute is reported, never raised — the same rule
        ``declarations`` follows, so a broken ``specs.py`` is fixable through
        the same surface that wrote it.

        The field is ``declaration_error`` and **not** ``error``: a top-level
        ``error`` key is the tool-envelope failure marker everywhere in this
        repo (``ToolRegistry.call`` produces it, ``routes_*._result`` raises on
        it, and callers test ``"error" not in result``), so a *reported*
        declaration failure must not wear that name.
        """
        state = {"path": "specs.py", "exists": script is not None,
                 "script": script, "declared": 0, "specs": [],
                 "declaration_error": None, "warnings": []}
        if script is None:
            return state
        try:
            declared = self._declare(script, "project", proj)
        except KernelError as exc:
            state["declaration_error"] = exc.to_payload()
            return state
        rows = [dict(row) for row in declared.get("declared", [])]
        state["specs"] = rows
        state["declared"] = len(rows)
        # A part-scope constructor found in specs.py is a warning, not an
        # error: it is reported here rather than dropped.
        state["warnings"] = list(declared.get("warnings") or [])
        return state

    def read_project_specs(self, proj: str) -> dict:
        """``specs.py`` with its declarations, or an honest absence.

        A project with no ``specs.py`` is ``{"script": None, "specs": []}`` —
        not a 404: "this project declares no project-scope specs" is an answer,
        not a missing resource. A file that exists but cannot be *read* is the
        opposite answer: ``exists`` stays true and the failure is reported as
        the declaration error it is.
        """
        self.service.store.manifest(proj)          # NotFoundError: bad project
        try:
            return self._project_state(proj, self.project_script(proj))
        except (OSError, UnicodeDecodeError) as exc:
            state = self._project_state(proj, None)
            state["exists"] = True
            state["declaration_error"] = _read_error(exc).to_payload()
            return state

    def write_project_specs(self, proj: str, script: str) -> dict:
        """Write ``specs.py`` and report what it declares (FR2, Decision 8).

        Unconditional, like ``update_part_script``: a broken file is written
        and reported, because you must be able to save one in order to fix it.
        An empty script deletes the file — an empty module and no module mean
        the same thing, and only one of them keeps ``exists`` honest.

        The store's ``write_guard`` fires only for ``write_script`` /
        ``save_manifest`` / ``imports_dir``, so a pack writing its own file
        calls it explicitly: it materializes the caller's branch tree (so the
        write cannot land on the default branch) and enforces the turn lock.
        Publishing ``project_changed`` afterwards is what snapshots the file
        into git — a mutating pack needs no per-call history hook.
        """
        store = self.service.store
        store.manifest(proj)                       # NotFoundError: bad project
        if store.write_guard is not None:
            store.write_guard(proj)
        path = self.specs_path(proj)
        text = script if isinstance(script, str) else ""
        if text.strip():
            ProjectStore._atomic_write(path, text.encode())
            state = self._project_state(proj, text)
        else:
            try:
                path.unlink()
            except FileNotFoundError:
                pass          # already gone is the post-state we were asked for
            except OSError as exc:
                # Anything else — a read-only checkout, a permission, a
                # directory in its place — must be a structured refusal naming
                # the path, never a post-state claiming the file is gone.
                raise ValidationError(
                    f"specs.py could not be deleted ({exc.strerror or exc})",
                    {"path": "specs.py", "project": proj}) from exc
            state = self._project_state(proj, None)
        self.service.bus.publish(
            {"type": "project_changed", "project": proj, "reason": "specs"})
        return state

    # -------------------------------------------------- budgeted kernel calls

    def _budgeted(self, normal: float, deadline: float, method: str) -> float:
        """What *method* may ask for with this deadline left, or a refusal.

        With nothing left the request is not issued at all: a
        :data:`_ERROR_BUDGET` ``KernelError`` is raised, which every
        measurement path already degrades into an honest ``error`` record.
        """
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise KernelError(
                _ERROR_BUDGET,
                f"the {GATE_BUDGET_S:.0f} s spec gate budget was exhausted "
                f"before {method} could run; run run_specs on this branch "
                "to populate the caches, then read the gate again",
                {"reason": "budget_exceeded", "budget_s": GATE_BUDGET_S,
                 "method": method})
        return max(_MIN_KERNEL_TIMEOUT_S, min(normal, remaining))

    def _kernel(self, method: str, params: dict, *, normal: float,
                affinity: str | None, deadline: float | None) -> dict:
        """One ``kernel.request`` under the gate budget.

        The budget is worthless if the call it wraps may run for ten times its
        length, so every request made under a *deadline* asks for what the
        deadline has left instead of its own ceiling (``spec_eval``'s 300 s,
        ``fem_static``'s 600 s).
        """
        timeout = (normal if deadline is None
                   else self._budgeted(normal, deadline, method))
        return self.service.kernel.request(method, params, timeout_s=timeout,
                                           affinity=affinity)

    def _mate_timeout(self, deadline: float | None) -> float | None:
        """The mate pass's share of the budget, or None when unbounded.

        ``resolve_mates`` is the one kernel call the gate reaches through
        ``service._resolved_instances`` rather than through :meth:`_kernel`,
        and it has its own flat 120 s ceiling — four times the whole budget. On
        a mated assembly with project specs that was ``proposal_get`` blocking
        for minutes while ``proposal_merge`` held the source turn lock, so the
        deadline is threaded through it on exactly the same terms.
        """
        if deadline is None:
            return None
        try:
            from .mates import RESOLVE_TIMEOUT_S
        except ImportError:      # the same optional seam _resolved_instances
            return None          # keeps: no module, no request to bound
        return self._budgeted(RESOLVE_TIMEOUT_S, deadline, "resolve_mates")

    # ----------------------------------------------------- declarations

    def _declare(self, script: str, scope: str, affinity: str,
                 deadline: float | None = None) -> dict:
        """``spec_declare``, memoized by ``sha256(scope, script)``.

        The handler never builds, so this is safe on a part that does not
        build at all (AC6) — and cheap enough to call per report.

        The **failure** is memoized on the same key, for the same reason the
        sidecar caches one: a ``SPECS`` that will not declare is a property of
        the text, ``_residue`` asks for the declarations of exactly the script
        that just failed to evaluate, and the browser re-reads a part on every
        rebuild. A failure that a deadline may have caused is never cached (it
        is a property of the budget, not of the script), and ``run_specs``
        drops the cached ones before it re-evaluates.
        """
        key = hashlib.sha256(f"{scope}\0{script}".encode()).hexdigest()
        hit = self._declaration_cache.get(key)
        if hit is None:
            try:
                hit = self._kernel("spec_declare",
                                   {"script": script, "scope": scope},
                                   normal=300.0, affinity=affinity,
                                   deadline=deadline)
            except KernelError as exc:
                if deadline is None:
                    self._remember_declaration(key, {_DECLARE_ERROR:
                                                     exc.to_payload()})
                raise
            self._remember_declaration(key, hit)
        if _DECLARE_ERROR in hit:
            raise _cached_error(hit[_DECLARE_ERROR])
        return hit

    def _remember_declaration(self, key: str, value: dict) -> None:
        if len(self._declaration_cache) > 256:
            self._declaration_cache.clear()
        self._declaration_cache[key] = value

    def _declaring_parts(self, proj: str, part_id: str | None):
        """(part id, script) for every scripted part that binds ``SPECS``.

        The presence scan is the whole of FR5's "zero added work": a part that
        declares nothing never reaches a kernel call.
        """
        store = self.service.store
        for entry in store.manifest(proj)["parts"]:
            pid = entry["id"]
            if part_id is not None and pid != part_id:
                continue
            record = store.get_part(proj, pid)
            if record.kind != "script":
                continue          # a reference part has no script to declare in
            script = store.read_script(proj, pid)
            if declares_specs(script):
                yield pid, script

    def declarations(self, proj: str, part_id: str | None = None) -> dict:
        """Every declaration, with no evaluation and no build (FR7, AC6).

        A file that will not execute becomes an ``errors[]`` entry rather than
        an exception, so one broken ``specs.py`` never hides the part specs.
        """
        store = self.service.store
        store.manifest(proj)                      # NotFoundError: unknown project
        if part_id is not None:
            store.get_part(proj, part_id)         # NotFoundError: unknown part

        parts, errors, warnings, flat = {}, [], [], []
        seen: set[str] = set()
        for pid, script in self._declaring_parts(proj, part_id):
            try:
                declared = self._declare(script, "part", pid)
            except KernelError as exc:
                errors.append({"scope": "part", "part": pid,
                               "error": exc.to_payload()})
                continue
            rows = [dict(row) for row in declared.get("declared", [])]
            assign_ids(rows, pid, seen, warnings)
            warnings.extend(declared.get("warnings") or [])
            parts[pid] = {"specs": rows}
            flat.extend(rows)

        project_specs = {"path": "specs.py", "exists": False, "specs": []}
        if part_id is None:
            try:
                script = self.project_script(proj)
            except (OSError, UnicodeDecodeError) as exc:
                script = None
                project_specs["exists"] = True
                errors.append({"scope": "project", "path": "specs.py",
                               "error": _read_error(exc).to_payload()})
            else:
                project_specs["exists"] = script is not None
            if script is not None:
                try:
                    declared = self._declare(script, "project", proj)
                except KernelError as exc:
                    errors.append({"scope": "project", "path": "specs.py",
                                   "error": exc.to_payload()})
                else:
                    rows = [dict(row) for row in declared.get("declared", [])]
                    assign_ids(rows, "project", seen, warnings)
                    warnings.extend(declared.get("warnings") or [])
                    project_specs["specs"] = rows
                    flat.extend(rows)

        requirements: dict[str, list[str]] = {}
        for row in flat:
            requirement = row.get("requirement")
            if requirement:
                requirements.setdefault(requirement, []).append(row["id"])
        return {"project": proj, "declared": len(flat), "parts": parts,
                "project_specs": project_specs, "requirements": requirements,
                "errors": errors, "warnings": warnings}

    # -------------------------------------------------------- tier 1

    def _shape_tier(self, proj: str, part_id: str,
                    cache_key: str | None = None,
                    deadline: float | None = None, refresh: bool = False, *,
                    record=None):
        """``(payload, cached, sidecar_path)`` for one part's shape tier.

        ``(None, False, None)`` means the part declares nothing — which is not
        the same as "not evaluated" and must never be reported as green.

        **The sidecar caches the failure as well as the result.** A
        ``contract_error`` (``SPECS = "hello"``), a script that will not build
        and a predicate that hangs are all deterministic in the one thing this
        key hashes — the script and its params — and every ``get_part``
        re-issues this call, so caching only the successes meant a broken part
        paid a fresh ``spec_eval`` (and, for a hang, a 300 s timeout plus a
        worker respawn) on *every read*. A cached failure re-raises the stored
        ``KernelError``, so the caller's ``_residue`` path is unchanged.

        Two escapes keep that honest: a failure measured under a *deadline* is
        never cached (the budget, not the script, may have caused it), and
        *refresh* — which is ``run_specs``, the unbounded surface — ignores a
        cached failure and measures again.

        *record* (keyword-only, PRD-012) evaluates a **derived** record — a
        configuration's pure parameter map — instead of the stored one. Both
        halves of the identity move together on purpose: the sidecar key
        becomes that record's cache key and the ``spec_eval`` params become its
        ``effective_params``, so a config-keyed sidecar can never end up
        holding the base measurement. Passing the record rather than a name is
        what keeps this method ignorant of configurations.
        """
        store = self.service.store
        record = record if record is not None else store.get_part(proj, part_id)
        if record.kind != "script":
            return None, False, None
        script = store.read_script(proj, part_id)
        if not declares_specs(script):
            return None, False, None

        key = cache_key or self.service._cache_key_for(proj, record)
        sidecar = store.cache_dir(proj) / f"{key}.specs.json"
        stored = _read_sidecar(sidecar)
        if stored is not None and isinstance(stored.get("checks"), list):
            return stored, True, sidecar
        if stored is not None and not refresh \
                and isinstance(stored.get("error"), dict):
            raise _cached_error(stored["error"])

        try:
            result = self._kernel(
                "spec_eval",
                {"script": script, "params": record.effective_params,
                 "density_g_cm3": self.service.material_density(
                     proj, record.material),
                 "densities": self.service._solid_densities(proj, record)
                 or None,
                 # null = every declaration, so nothing is dropped: the tiers
                 # this call cannot measure come back as named skips.
                 "indices": None},
                normal=300.0, affinity=part_id, deadline=deadline)
        except KernelError as exc:
            if deadline is None:
                _write_sidecar(sidecar, {"version": SPEC_RESULT_VERSION,
                                         "cache_key": key,
                                         "error": exc.to_payload()})
            raise
        payload = {"version": SPEC_RESULT_VERSION, "cache_key": key,
                   "checks": result.get("checks", []),
                   "declared": result.get("declared", []),
                   "warnings": result.get("warnings", []), "tiers": {}}
        _write_sidecar(sidecar, payload)
        return payload, False, sidecar

    def _decorate(self, records: list[dict], part_id: str, seen: set,
                  warnings: list[str]) -> list[dict]:
        """Join the worker's records to this part: they carry ``index``, the
        service adds ``part`` and the ``<part>:<name>`` id."""
        rows = [dict(record, part=part_id) for record in records]
        return assign_ids(rows, part_id, seen, warnings)

    def _residue(self, proj: str, part_id: str, exc: KernelError, seen: set,
                 warnings: list[str], deadline: float | None = None) -> dict:
        """A ``KernelError`` from ``spec_eval`` as *data*.

        Structural ``SPECS`` problems (``SPECS = "hello"``) raise
        ``contract_error`` in the worker, and failing a rebuild over a broken
        *assertion* would take away the geometry you need in order to fix it.
        So every declared check becomes an ``error`` record — named, if the
        declarations can still be read without building.
        """
        payload = exc.to_payload()
        details = dict(payload.get("details") or {})
        try:
            script = self.service.store.read_script(proj, part_id)
            declared = self._declare(script, "part", part_id,
                                     deadline)["declared"]
        except (KernelError, NotFoundError, OSError, KeyError):
            declared = []
        records = [
            {**_record(declaration, index, part_id),
             **_error_row(payload["message"], details)}
            for index, declaration in enumerate(declared)
        ]
        if not records:
            records = [{**_record(
                {"name": "specs", "kind": "declaration", "scope": "part",
                 "requirement": None, "limit": {}}, 0, part_id),
                **_error_row(payload["message"], details)}]
        assign_ids(records, part_id, seen, warnings)
        summary = summarize(records)
        return {"status": "error", "error": payload, "summary": summary,
                "checks": records, "requirements": group_requirements(records),
                "cached": False, "warnings": warnings}

    def tier1(self, proj: str, part_id: str,
              build_result: dict | None = None) -> dict | None:
        """The rebuild summary: the shape tier and nothing else (FR5).

        ``None`` means the part declares no specs — an explicit "none
        declared", distinguishable from "not evaluated". Tiers 2 and 3 are in
        the record list as ``skip``/``deferred``, because a 600 s solve inside
        a slider drag is not "without friction".

        *build_result* is the rebuild's own payload: its ``cache_key`` is the
        key the geometry was written under, so the spec sidecar can never be
        joined to a different build than the one that just landed.
        """
        cache_key = (build_result or {}).get("cache_key")
        warnings: list[str] = []
        try:
            payload, cached, _sidecar = self._shape_tier(proj, part_id, cache_key)
        except KernelError as exc:
            return self._residue(proj, part_id, exc, set(), warnings)
        if payload is None:
            return None
        warnings.extend(payload.get("warnings") or [])
        checks = self._decorate(payload["checks"], part_id, set(), warnings)
        summary = summarize(checks)
        return {"status": report_status(summary), "summary": summary,
                "checks": checks, "requirements": group_requirements(checks),
                "cached": cached, "warnings": warnings}

    # -------------------------------------------------------- tier 3

    def _eval_fem(self, proj: str, part_id: str, declaration: dict,
                  deadline: float | None = None) -> dict:
        """``check_fem_static``: one 600 s request, or an honest skip (FR8).

        Under the gate budget the request gets what the budget has left, not
        600 s: a cold FEM source must not make ``proposal_get`` block for ten
        minutes while ``proposal_merge`` holds the source's turn lock.
        """
        bad = _non_finite_limit(declaration.get("limit"))
        if bad is not None:
            return _error_row(
                f"{bad} is not a finite number, so this budget can never be "
                "breached; declare it with check_fem_static")
        if not _fem_available():
            return _skip_row(
                "fem_extra_missing", _FEM_HINT,
                "FEM is not available on this machine, so the budget was not "
                "measured")
        options = declaration.get("options") or {}
        if not options.get("fixed_face") or not options.get("load_face"):
            # The record survived but its declaration did not (a sidecar written
            # by an older format): say so rather than send a half-request.
            return _error_row(
                "the fem_static declaration could not be read; re-run after "
                "the part rebuilds")
        store = self.service.store
        record = store.get_part(proj, part_id)
        args = {"script": store.read_script(proj, part_id),
                "params": record.effective_params,
                "fixed_face": options.get("fixed_face"),
                "load_face": options.get("load_face"),
                "load_N": options.get("load_N"),
                "load_dir": [0, 0, -1]}
        modulus = self._youngs_mpa(proj, record.material)
        if modulus is not None:
            args["E_mpa"] = modulus      # otherwise the kernel's steel default
        try:
            result = self._kernel("fem_static", args, normal=600.0,
                                  affinity=part_id, deadline=deadline)
        except KernelError as exc:
            payload = exc.to_payload()
            return _error_row(payload["message"],
                              {**(payload.get("details") or {}),
                               "error_type": payload["type"]})
        displacement = float(result["max_disp_mm"])
        von_mises = float(result["max_von_mises_mpa"])
        limit = declaration.get("limit") or {}
        breaches = []
        if limit.get("max_disp_mm") is not None and \
                displacement > limit["max_disp_mm"] + _slack(limit["max_disp_mm"]):
            breaches.append(
                f"displacement {_fmt(displacement)} mm exceeds "
                f"{_fmt(limit['max_disp_mm'])} mm")
        if limit.get("max_vm_mpa") is not None and \
                von_mises > limit["max_vm_mpa"] + _slack(limit["max_vm_mpa"]):
            breaches.append(
                f"von Mises {_fmt(von_mises)} MPa exceeds "
                f"{_fmt(limit['max_vm_mpa'])} MPa")
        measured = {"max_disp_mm": displacement, "max_vm_mpa": von_mises}
        details = {"n_nodes": result.get("n_nodes"),
                   "n_tets": result.get("n_tets"), "note": result.get("note"),
                   "E_mpa": args.get("E_mpa")}
        if breaches:
            return {"status": "fail", "measured": measured, "details": details,
                    "message": "; ".join(breaches)}
        return {"status": "pass", "measured": measured, "details": details,
                "message": f"displacement {_fmt(displacement)} mm and von "
                           f"Mises {_fmt(von_mises)} MPa are within budget"}

    def _fem_material_key(self, proj: str, part_id: str) -> str:
        """The material properties the FEM solver actually consumes, as a key
        fragment for the cached ``fem`` rows.

        The sidecar those rows live in is filed under the *part cache key*,
        which covers the script, the params and the material **density** — but
        :meth:`_eval_fem` also sends ``E_mpa``, and displacement scales with
        1/E. An E-only material change left the key unmoved and reused
        physically stale evidence. E is the whole list: the solver's Poisson
        ratio is its own constant (``_fem_impl``'s ``nu`` default), and this
        layer never sends one.
        """
        try:
            record = self.service.store.get_part(proj, part_id)
        except (NotFoundError, OSError):
            return "unknown"
        modulus = self._youngs_mpa(proj, record.material)
        return "default" if modulus is None else f"E{modulus:.10g}"

    def _youngs_mpa(self, proj: str, material_id: str) -> float | None:
        """The part material's Young's modulus, when the catalog has one —
        ``fem_modal``'s convention, minus its hard error: a spec that cannot
        find E should still measure, with the solver's own default.

        Read through the FEM tools' own resolver at **20 °C** (PRD-028 FR4), so
        the key below is the modulus the solver would consume. A point-only
        material is unaffected (``Property.at`` ignores the temperature without
        a table), and a material whose E *table* moves away from 20 °C leaves
        the memo alone — which is right: ``_eval_fem`` sends no temperature.
        """
        try:
            from .tools_analysis import resolve_property  # deferred: tool pack

            entry = resolve_property(self.service, proj, material_id, "E_gpa",
                                     20.0)
            return None if entry is None else entry["value"] * 1000.0
        except Exception:  # noqa: BLE001 — a missing material is not this
            return None    #                check's error to raise

    # -------------------------------------------------------- tier 2

    def _project_key(self, proj: str, script: str,
                     deadline: float | None = None) -> str:
        """Content key for the assembly tier — over **every** input the three
        project checks read, not only the ones ``clearance`` reads.

        The specs text, plus each placed instance's identity, part cache key
        (the *instance's* — a configuration binding moves it, PRD-012) and
        resolved transform (moving one instance changes every clearance, so
        the whole placement is the key), plus the two inputs ``check_stackup``
        consumes that live in the manifest rather than in a script: the **mate
        graph** (it is what the stack path is walked over, and two chains can
        resolve to the same transforms) and each referenced part's **PMI
        dims** (the tolerances that are summed). Neither moves a part cache key,
        so a loosened tolerance would otherwise reuse the verdict measured
        against the tight one.
        """
        pmi = {entry["id"]: entry.get("pmi")
               for entry in self.service.store.manifest(proj)["parts"]}
        mates = {instance.id: instance.mate
                 for instance in self.service.store.instances(proj)
                 if getattr(instance, "mate", None)}
        rows = []
        for instance in self.service._resolved_instances(
                proj, timeout_s=self._mate_timeout(deadline)):
            try:
                # The instance's OWN record: a bound instance keys on its
                # configuration's cache key, so an assembly verdict measured at
                # S is never reused at L. Two configurations with the same
                # override map are one geometry and legitimately share it.
                record = self.service._record_for(
                    proj, instance.part, instance.config)
                part_key = self.service._cache_key_for(proj, record)
            except (NotFoundError, ValidationError, OSError):
                # ValidationError too: a stale binding (a configuration the
                # part no longer declares) degrades THAT row to "missing" —
                # escaping here would reach the caller's bare `except` and
                # disable assembly-tier caching for the whole project.
                part_key = "missing"
            rows.append([instance.id, instance.part, part_key,
                         [float(v) for v in instance.position],
                         [float(v) for v in instance.rotation_deg]])
        payload = json.dumps({"specs": script, "instances": sorted(rows),
                              "mates": mates,
                              "pmi": {pid: pmi.get(pid) for pid in sorted(
                                  {row[1] for row in rows})},
                              "version": SPEC_RESULT_VERSION}, sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()[:32]

    def _instance_item(self, proj: str, instance) -> dict:
        # A bound instance is measured at its configuration's size (Decision 7).
        record = self.service._record_for(proj, instance.part, instance.config)
        item = self.service._shape_item(proj, record, instance)
        item["name"] = instance.id
        return item

    def _eval_interference(self, proj: str, declaration: dict,
                           deadline: float | None = None) -> dict:
        minimum = (declaration.get("limit") or {}).get("min_volume_mm3", 0.001)
        timeout = None
        if deadline is not None:
            # ``check_interference`` asks for 600 s (pairs grow quadratically);
            # under the gate it gets what the budget has left instead.
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return self._budget_row()
            timeout = max(_MIN_KERNEL_TIMEOUT_S, min(600.0, remaining))
        try:
            result = self.service.check_interference(proj, min_volume=minimum,
                                                     timeout_s=timeout)
        except KernelError as exc:
            payload = exc.to_payload()
            return _error_row(payload["message"], payload.get("details"))
        if result["checked"] < 2:
            return _skip_row(
                "no_instances",
                "place at least two instances to check for interference",
                f"{result['checked']} instance(s) placed: nothing to overlap")
        pairs = result["pairs"]
        details = {"pairs": pairs, "checked": result["checked"]}
        if result.get("skipped_mesh"):
            details["skipped_mesh"] = result["skipped_mesh"]
        measured = max((pair["volume_mm3"] for pair in pairs), default=0.0)
        if pairs:
            named = ", ".join(f"{p['a']}/{p['b']}" for p in pairs[:3])
            message = f"{len(pairs)} interfering pair(s): {named}"
            # A pair the kernel could not boolean at all (``degenerate``, see
            # kernel/handlers/_bop.py) is already IN ``pairs``: the worker
            # fails closed rather than reporting a clean assembly it cannot
            # vouch for. Its ``volume_mm3`` is 0.0 and means nothing, so the
            # count is said out loud instead of hiding inside ``measured``.
            unmeasurable = sum(1 for p in pairs if p.get("degenerate"))
            if unmeasurable:
                message += (f"; {unmeasurable} indeterminate (OCCT could not "
                            "boolean the pair — counted as interfering, "
                            "fail-closed)")
            return {"status": "fail", "measured": measured, "details": details,
                    "message": message}
        return {"status": "pass", "measured": measured, "details": details,
                "message": f"no two of {result['checked']} instances overlap"}

    def _eval_clearance(self, proj: str, declaration: dict,
                        deadline: float | None = None) -> dict:
        options = declaration.get("options") or {}
        minimum = (declaration.get("limit") or {}).get("min_mm")
        try:
            resolved = {i.id: i for i in self.service._resolved_instances(
                proj, timeout_s=self._mate_timeout(deadline))}
        except KernelError as exc:
            # The budget refusing to start the mate pass, reported like every
            # other measurement the deadline stopped.
            payload = exc.to_payload()
            return _error_row(payload["message"], payload.get("details"))
        items = {}
        for side in ("a", "b"):
            name = options.get(side)
            if name not in resolved:
                # The PRD's rename risk: honest, named, and red at a boundary.
                return _error_row(
                    f"instance {name!r} is not in the assembly",
                    {"missing": name, "known": sorted(resolved)})
            try:
                items[side] = self._instance_item(proj, resolved[name])
            except NotFoundError as exc:
                return _error_row(str(exc), {"instance": name})
        try:
            result = self._kernel(
                "clearance",
                {"a": items["a"], "b": items["b"], "min_mm": minimum},
                normal=300.0, affinity=proj, deadline=deadline)
        except KernelError as exc:
            payload = exc.to_payload()
            return _error_row(payload["message"], payload.get("details"))
        if result.get("skipped_mesh"):
            sides = [options.get(side) for side in result["skipped_mesh"]]
            return _skip_row(
                "mesh_only", _MESH_HINT,
                f"{', '.join(str(s) for s in sides)} is an imported mesh, so "
                "the distance was not measured")
        distance = float(result["distance_mm"])
        details = {"point_a": result.get("point_a"),
                   "point_b": result.get("point_b")}
        row = {"measured": distance, "details": details,
               "location": result.get("point_a")}
        if result.get("ok"):
            return {**row, "status": "pass",
                    "message": f"{options['a']} to {options['b']} is "
                               f"{_fmt(distance)} mm, at or above the "
                               f"{_fmt(minimum)} mm minimum"}
        return {**row, "status": "fail",
                "message": f"{options['a']} to {options['b']} is "
                           f"{_fmt(distance)} mm, below the "
                           f"{_fmt(minimum)} mm minimum"}

    def _eval_stackup(self, proj: str, declaration: dict,
                      deadline: float | None = None) -> dict:
        # The tolerances are manifest arithmetic, but the nominal is read off
        # the RESOLVED placement, so a mated assembly costs one resolve_mates
        # — which the deadline bounds like every other call under the gate.
        options = declaration.get("options") or {}
        within = (declaration.get("limit") or {}).get("within_mm")
        try:
            # compute_stackup, not registry.call("tolerance_stackup"): the
            # stackup pack sorts after the specs pack, so the tool may not be
            # registered yet — and a check must not depend on that order.
            result = compute_stackup(self.service, proj, options["axis"],
                                     options["from_instance"],
                                     options["to_instance"],
                                     timeout_s=self._mate_timeout(deadline))
        except (NotFoundError, ValidationError) as exc:
            return _error_row(str(exc), getattr(exc, "details", None) or {})
        except KernelError as exc:
            # The budget refusing to start the mate pass, reported like every
            # other measurement the deadline stopped.
            payload = exc.to_payload()
            return _error_row(payload["message"], payload.get("details"))
        worst = result["worst_case"]
        measured = max(abs(worst["plus"]), abs(worst["minus"]))
        details = {"worst_case": worst, "rss": result["rss"],
                   "nominal_mm": result["nominal_mm"], "path": result["path"],
                   "warnings": result["warnings"]}
        if measured > within + _slack(within):
            return {"status": "fail", "measured": measured, "details": details,
                    "message": f"worst-case stack-up {_fmt(measured)} mm "
                               f"exceeds {_fmt(within)} mm"}
        return {"status": "pass", "measured": measured, "details": details,
                "message": f"worst-case stack-up {_fmt(measured)} mm is "
                           f"within {_fmt(within)} mm"}

    def _eval_project_check(self, proj: str, declaration: dict,
                            deadline: float | None = None) -> dict:
        bad = _non_finite_limit(declaration.get("limit"))
        if bad is not None:
            # A NaN bound passes every comparison in every evaluator below, so
            # it is rejected before one runs (the constructors reject it at
            # declaration; this is the hand-written path).
            return _error_row(
                f"{bad} is not a finite number, so this check can never fail; "
                "declare it with one of agentcad.toolkit.specs' constructors")
        if declaration.get("scope") != "project":
            return _skip_row(
                "unsupported_scope", _SCOPE_HINT,
                f"{declaration['kind']} is a part-scope check and cannot be "
                "evaluated over the assembly")
        evaluator = {"interference_free": self._eval_interference,
                     "clearance": self._eval_clearance,
                     "stackup": self._eval_stackup}.get(declaration["kind"])
        if evaluator is None:
            return _skip_row(
                "unsupported_scope", _SCOPE_HINT,
                f"{declaration['kind']} has no project-scope measurement")
        try:
            return evaluator(proj, declaration, deadline)
        except Exception as exc:  # noqa: BLE001 — the report degrades, never raises
            return _error_row(f"{type(exc).__name__}: {exc}")

    def _declaration_row(self, message: str, details: dict | None,
                         part: str | None, prefix: str, seen: set,
                         warnings: list[str]) -> dict:
        """One synthetic ``declaration`` check row for a spec module that could
        not be read or executed.

        The fail-closed rule needs a *check*, not an ``errors[]`` entry:
        ``report_status`` and the proposal gate are both computed from the
        check rows alone, so a ``specs.py`` that never declared anything used
        to leave a report green and a merge unblocked.
        """
        row = {**_record({"name": "specs", "kind": "declaration",
                          "scope": "part" if part else "project",
                          "requirement": None, "limit": {}}, 0, part),
               **_error_row(message, details)}
        assign_ids([row], prefix, seen, warnings)
        return row

    def _project_block(self, proj: str, seen: set, warnings: list[str],
                       errors: list[dict],
                       deadline: float | None = None) -> list[dict]:
        try:
            script = self.project_script(proj)
        except (OSError, UnicodeDecodeError) as exc:
            # An existing specs.py we cannot read is NOT "no specs.py": it is a
            # declared scope we did not measure, which is red.
            failure = _read_error(exc)
            errors.append({"scope": "project", "path": "specs.py",
                           "error": failure.to_payload()})
            return [self._declaration_row(failure.message,
                                          failure.details, None, "project",
                                          seen, warnings)]
        if script is None:
            return []
        try:
            declared = self._declare(script, "project", proj, deadline)
        except KernelError as exc:
            payload = exc.to_payload()
            errors.append({"scope": "project", "path": "specs.py",
                           "error": payload})
            # The same rule one step later: a specs.py that will not execute
            # declared nothing, and an empty project scope used to read as
            # "nothing to check" in both the status and the gate.
            return [self._declaration_row(
                f"specs.py could not be declared: {payload['message']}",
                {**(payload.get("details") or {}), "path": "specs.py",
                 "error_type": payload["type"]},
                None, "project", seen, warnings)]
        rows = declared.get("declared", [])
        warnings.extend(declared.get("warnings") or [])
        if not rows:
            return []
        records = [_record(row, index, None) for index, row in enumerate(rows)]
        assign_ids(records, "project", seen, warnings)

        try:
            sidecar = (self.service.store.cache_dir(proj)
                       / f"{self._project_key(proj, script, deadline)}"
                         ".projspecs.json")
            stored = _read_sidecar(sidecar) or {}
        except Exception:  # noqa: BLE001 — an unkeyable assembly (a dangling
            sidecar, stored = None, {}   # mate) evaluates uncached, never raises
        cached = stored.get("checks")
        cached = cached if isinstance(cached, dict) else {}
        dirty = False
        for index, (declaration, record) in enumerate(zip(rows, records)):
            row = cached.get(str(index))
            if row is None:
                # Between checks, not only between scopes: one clearance over a
                # large assembly can outlast the whole budget on its own.
                row = self._budget_row() if self._out_of_budget(deadline) \
                    else self._eval_project_check(proj, declaration, deadline)
                # Only a real verdict is cached: a skip can be machine-specific
                # (a missing extra) and an error is usually transient.
                if row["status"] in ("pass", "fail"):
                    cached[str(index)] = row
                    dirty = True
            record.update(row)
        if dirty and sidecar is not None:
            _write_sidecar(sidecar, {"version": SPEC_RESULT_VERSION,
                                     "checks": cached})
        return records

    # ---------------------------------------------------------- the report

    @contextmanager
    def _pinned(self, proj: str, ref: str | None):
        """Evaluate under ``ref``'s working tree (PRD-002's exact mechanism).

        A named ref is resolved with ``history.resolve_branch``: ``rev-parse``
        searches tags before branches, so a tag named like a branch would
        otherwise answer for it (PRD-001 X1).
        """
        if ref is None:
            yield
            return
        branches = getattr(self.service, "branches", None)
        history = getattr(self.service, "history", None)
        if branches is None or history is None or not history.available():
            raise ValidationError(
                "specs are versioned by git, which is not available",
                {"ref": ref})
        canonical = self.service.store.canonical_path_of(proj)
        if history.resolve_branch(canonical, ref) is None:
            if history.resolve_ref(canonical, ref) is not None:
                raise ValidationError(f"ref {ref!r} is a tag, not a branch",
                                      {"ref": ref})
            raise NotFoundError(
                f"branch {ref!r} not found in project {proj!r}", {"ref": ref})
        with branches.pinned(proj, branches.tree_of(proj, ref)):
            yield

    def run(self, proj: str, part_id: str | None = None,
            ref: str | None = None, deadline: float | None = None) -> dict:
        """The full report (FR7, FR12): all three tiers, every scope.

        Unlike a rebuild this evaluates the assembly and expensive tiers too —
        that is the whole difference between the two surfaces.

        It is also **the exit from every cached refusal** this module keeps,
        which is what makes those caches safe: it re-measures a cached failure
        rather than re-raising it, and afterwards it drops the memoized
        ``budget_exceeded`` verdicts for this project so the next gate read is
        measured against the sidecars this run just warmed. Every "run
        run_specs on that branch" in a gate summary is this sentence.

        *deadline* is a ``time.monotonic`` wall-clock bound, threaded straight
        into :meth:`_report` — the same mechanism the proposal gate uses, and
        the only thing this parameter does. ``run_specs`` passes ``None`` and
        is unbounded by design (an engineer asking for a full report has asked
        for the cost); ``agentcad check --budget`` passes what is left of its
        budget, because a promise to bound a run cannot exempt its most
        expensive stage. A refusal measured under a deadline is never cached,
        so a bounded run still leaves this method the honest exit it is.
        """
        self._forget_declaration_failures()
        with self._pinned(proj, ref):
            report = self._report(proj, part_id, ref, deadline=deadline,
                                  refresh=True)
        self._forget_budget_verdicts(proj)
        return report

    def _forget_declaration_failures(self) -> None:
        """Drop the cached ``spec_declare`` failures, so this run re-measures
        them. Successes stay: they are keyed by the script text."""
        for key, value in list(self._declaration_cache.items()):
            if _DECLARE_ERROR in value:
                self._declaration_cache.pop(key, None)

    def _forget_budget_verdicts(self, proj: str) -> None:
        """Drop *proj*'s memoized ``budget_exceeded`` verdicts, after the run
        that supersedes them. Only three-part (verdict) keys: the
        ``specs.py``-changed flags are git facts and keep theirs."""
        for key, verdict in list(self._gate_memo.items()):
            if len(key) == 3 and key[0] == proj \
                    and verdict.get("reason") == "budget_exceeded":
                self._gate_memo.pop(key, None)

    def _part_block(self, proj: str, part_id: str, seen: set,
                    warnings: list[str], errors: list[dict],
                    deadline: float | None = None,
                    refresh: bool = False) -> dict | None:
        try:
            payload, cached, sidecar = self._shape_tier(
                proj, part_id, deadline=deadline, refresh=refresh)
        except KernelError as exc:
            residue = self._residue(proj, part_id, exc, seen, warnings,
                                    deadline)
            errors.append({"scope": "part", "part": part_id,
                           "error": residue["error"]})
            return residue
        if payload is None:
            return None
        warnings.extend(payload.get("warnings") or [])
        checks = self._decorate(payload["checks"], part_id, seen, warnings)

        declarations = payload.get("declared") or []
        tiers = payload.get("tiers") if isinstance(payload.get("tiers"), dict) \
            else {}
        fem_cache = tiers.get("fem") if isinstance(tiers.get("fem"), dict) else {}
        material = self._fem_material_key(proj, part_id)
        dirty = False
        for record in checks:
            if record.get("kind") not in EXPENSIVE_TIER:
                continue
            index = record.get("index")
            slot = f"{index}|{material}"
            row = fem_cache.get(slot)
            if row is None:
                declaration = declarations[index] \
                    if isinstance(index, int) and index < len(declarations) else {}
                row = self._budget_row() if self._out_of_budget(deadline) \
                    else self._eval_fem(proj, part_id, declaration, deadline)
                if row["status"] in ("pass", "fail"):
                    fem_cache[slot] = row
                    dirty = True
            # The deferred record carried reason/hint; a measured one has none.
            record.pop("reason", None)
            record.pop("hint", None)
            record.update(row)
        if dirty and sidecar is not None:
            tiers["fem"] = fem_cache
            payload["tiers"] = tiers
            _write_sidecar(sidecar, payload)

        summary = summarize(checks)
        # list(warnings): the caller's accumulator keeps growing as later parts
        # are read, and a block must report the warnings it was built with.
        return {"status": report_status(summary), "summary": summary,
                "checks": checks, "requirements": group_requirements(checks),
                "cached": cached, "warnings": list(warnings)}

    def _out_of_budget(self, deadline: float | None) -> bool:
        """``time.monotonic``, never wall-clock: a budget must not be moved by
        an NTP step in the middle of a merge."""
        return deadline is not None and time.monotonic() > deadline

    def _budget_row(self) -> dict:
        """One check the budget did not reach. ``error``, never ``skip``: a
        declared check we did not measure is 'we do not know'."""
        return _error_row(
            f"not evaluated: the {GATE_BUDGET_S:.0f} s spec gate budget was "
            "exhausted before this check was measured. Run run_specs on this "
            "branch to populate the caches, then read the gate again.",
            {"reason": "budget_exceeded", "budget_s": GATE_BUDGET_S})

    def _unevaluated(self, prefix: str, part_id: str | None, seen: set,
                     warnings: list[str], errors: list[dict]) -> list[dict]:
        """One synthetic ``error`` record for a scope the budget never reached.

        Fail-closed (design Decision 7): a declared-but-unmeasured spec is not
        evidence of green, so this is an ``error`` — 'we do not know', which is
        not 'it is fine'. It is one record rather than one per declaration
        because naming them individually costs the very kernel round trip the
        budget just ran out of.
        """
        message = (
            f"not evaluated: the {GATE_BUDGET_S:.0f} s spec gate budget was "
            "exhausted before this scope was reached. Run run_specs on this "
            "branch to populate the caches, then read the gate again.")
        row = {**_record({"name": "specs", "kind": _UNEVALUATED,
                          "scope": "part" if part_id else "project",
                          "requirement": None, "limit": {}}, 0, part_id),
               **_error_row(message, {"reason": "budget_exceeded",
                                      "budget_s": GATE_BUDGET_S})}
        assign_ids([row], prefix, seen, warnings)
        errors.append({"scope": row["scope"], "part": part_id,
                       "reason": "budget_exceeded",
                       "error": {"type": "budget_exceeded", "message": message,
                                 "details": {"budget_s": GATE_BUDGET_S}}})
        return [row]

    def _unevaluated_part(self, proj: str, part_id: str, seen: set,
                          warnings: list[str],
                          errors: list[dict]) -> dict | None:
        """The budget's stand-in for :meth:`_part_block`.

        Still ``None`` for a part that declares nothing: the presence scan is
        free, and a spec-less part is not 'unmeasured', it is silent.
        """
        store = self.service.store
        try:
            record = store.get_part(proj, part_id)
            if record.kind != "script" or not declares_specs(
                    store.read_script(proj, part_id)):
                return None
        except (NotFoundError, OSError):
            return None
        rows = self._unevaluated(part_id, part_id, seen, warnings, errors)
        return {"status": "red", "summary": summarize(rows), "checks": rows,
                "requirements": group_requirements(rows), "cached": False,
                "warnings": list(warnings)}

    def _report(self, proj: str, part_id: str | None, ref: str | None,
                deadline: float | None = None,
                refresh: bool = False) -> dict:
        """*deadline* is :meth:`evaluate_specs`'s wall-clock budget, and the one
        :meth:`run` was given (``run_specs`` still passes ``None`` and is
        unbounded by design — an engineer asking for a full report has asked
        for the cost; ``agentcad check --budget`` passes its remainder).

        *refresh* is ``run``'s: the one surface that re-measures a cached
        failure instead of re-raising it."""
        store = self.service.store
        manifest = store.manifest(proj)            # NotFoundError: bad project
        if part_id is not None:
            store.get_part(proj, part_id)          # NotFoundError: bad part

        seen: set[str] = set()
        warnings: list[str] = []
        errors: list[dict] = []
        flat: list[dict] = []
        parts: dict[str, dict] = {}
        for entry in manifest["parts"]:
            pid = entry["id"]
            if part_id is not None and pid != part_id:
                continue
            block = self._unevaluated_part(proj, pid, seen, warnings, errors) \
                if self._out_of_budget(deadline) \
                else self._part_block(proj, pid, seen, warnings, errors,
                                      deadline, refresh)
            if block is None:
                continue                    # declares nothing: not in the report
            parts[pid] = {
                "status": block["status"], "summary": block["summary"],
                "cached": block["cached"],
                "checks": [check["id"] for check in block["checks"]]}
            flat.extend(block["checks"])

        # Project scope is the assembly's, so a per-part report never runs it.
        if part_id is not None:
            project_checks: list[dict] = []
        elif self._out_of_budget(deadline) and self.specs_path(proj).is_file():
            project_checks = self._unevaluated("project", None, seen, warnings,
                                               errors)
        else:
            project_checks = self._project_block(proj, seen, warnings, errors,
                                                 deadline)
        flat.extend(project_checks)

        summary = summarize(flat)
        project_summary = summarize(project_checks)
        return {
            "project": proj, "ref": ref, "generated": _now(),
            "status": report_status(summary), "summary": summary,
            "checks": flat, "parts": parts,
            "project_checks": {
                "status": report_status(project_summary),
                "summary": project_summary,
                "checks": [check["id"] for check in project_checks]},
            "requirements": group_requirements(flat),
            "declared": len(flat), "warnings": warnings, "errors": errors,
        }

    # ------------------------------------------ the proposal gate (FR11)

    def _head_of(self, proj: str, ref: str | None) -> str | None:
        """The commit a verdict would be about, or None when git cannot say.

        Deliberately quiet: this is read twice around an evaluation purely to
        detect movement, and a git hiccup there must degrade the *verdict*
        (through :meth:`evaluate_specs`), not raise from inside it.
        """
        history = getattr(self.service, "history", None)
        if history is None or not history.available():
            return None
        try:
            canonical = self.service.store.canonical_path_of(proj)
            if ref is None:
                return history.head(canonical)
            return history.resolve_branch(canonical, ref)
        except Exception:  # noqa: BLE001 — "no head we can name" is the answer
            return None

    def _memo_get(self, key: tuple) -> dict | None:
        """Both memos are one dict: the keys are commit-shaped and disjoint,
        and one bound is easier to reason about than two."""
        hit = self._gate_memo.pop(key, None)
        if hit is not None:
            self._gate_memo[key] = hit         # re-insert: LRU touch
        return hit

    def _memo_put(self, key: tuple, value: dict) -> None:
        self._gate_memo[key] = value
        while len(self._gate_memo) > _GATE_MEMO_LIMIT:
            oldest = next(iter(self._gate_memo), None)
            if oldest is None:            # emptied under us: nothing to evict
                return
            self._gate_memo.pop(oldest, None)

    def _verdict_branch(self, proj: str, ref: str | None) -> tuple:
        """``(branch, memoizable)`` — the ref a verdict is actually *about*.

        ``ref=None`` means "the caller's tree", which is what the report is
        read from (it runs unpinned, through the store's branch resolver). The
        head it is stamped with, and the key it is filed under, must therefore
        be the CALLER's branch — not the canonical repo head, which is the
        default branch's and would file one client's verdict under a commit it
        never measured, then hand it to every other client on every branch.

        Without the branch layer there is one tree per project and the
        canonical head *is* the caller's, so ``None`` is both the branch and a
        memoizable answer. When the layer exists but cannot say which branch
        this client is on, the verdict is produced and returned — and simply
        not memoized, because there is no key it would be honest under.
        """
        if ref is not None:
            return ref, True
        branches = getattr(self.service, "branches", None)
        if branches is None:
            return None, True
        try:
            return branches.current(proj), True
        except Exception:  # noqa: BLE001 — an unreadable checkout is not a raise
            return None, False

    def evaluate_specs(self, proj: str, ref: str | None = None) -> dict:
        """Gate-shaped status for any ref (FR11). ``ref=None`` is the caller's.

        ``{"available", "status": green|red|skip|pending, "ref", "head",
        "checked_at", "summary", "failures", "skips", "errors", "reason"}``.

        ``available`` says whether a verdict for ``head`` was produced:
        ``False`` only for ``pending``, where the head moved out from under the
        measurement and the honest answer is "ask again", not a verdict wearing
        a commit it did not measure. The head is read **before and after**, as
        the review packet does one layer up — both times for the branch
        :meth:`_verdict_branch` resolved, which for ``ref=None`` is the
        caller's own.

        Bounded three ways, in order of how much they save: the shared
        canonical ``.cache/`` (an unchanged part is a disk read on both sides
        of a branch), the per-head memo below, and :data:`GATE_BUDGET_S` —
        whose exhaustion is *red*, with ``reason == "budget_exceeded"`` and a
        summary naming ``run_specs``, because a spec that was not measured is
        not evidence of green.

        The memo key is ``(project, branch, head)``. The design named
        ``(project, source_head, declaration_hash)``; for a branch the head
        **is** the declaration hash — PRD-001 snapshots every write, so an
        edited ``SPECS`` moves it — and computing a separate one would mean
        materializing the ref's tree first, which is exactly the work the memo
        exists to skip. Nothing is memoized without a head (no git, no key),
        without a branch we can name, or for a ``pending`` result.

        A **budget_exceeded verdict IS memoized**, deliberately: it is red with
        a stable reason for that head, and re-paying an exhausted 30 s budget
        on every ``proposal_get`` is the outcome the memo exists to prevent.
        The gate therefore stays red-with-the-budget-reason until the head
        moves or ``run_specs`` — unbounded, and named in the summary — warms
        the sidecars and drops the verdict (:meth:`_forget_budget_verdicts`).
        Fail-closed either way: the memo can only keep a red, never make one
        green.
        """
        branch, memoizable = self._verdict_branch(proj, ref)
        head = self._head_of(proj, branch)
        key = (proj, branch, head)
        if head is not None and memoizable:
            hit = self._memo_get(key)
            if hit is not None:
                return dict(hit)

        deadline = time.monotonic() + GATE_BUDGET_S
        with self._pinned(proj, ref):
            report = self._report(proj, None, ref, deadline=deadline)

        checks = [self._gate_row(check) for check in report["checks"]]
        summary = summarize(checks)
        by_status = {status: [c for c in checks if c.get("status") == status]
                     for status in ("fail", "skip", "error")}
        reason = "budget_exceeded" if any(
            c.get("kind") == _UNEVALUATED
            or (c.get("details") or {}).get("reason") == "budget_exceeded"
            for c in checks) else None
        verdict = {
            "available": True, "status": report_status(summary), "ref": ref,
            "head": head, "checked_at": _now(), "summary": summary,
            "failures": by_status["fail"], "skips": by_status["skip"],
            "errors": by_status["error"], "reason": reason,
        }
        after = self._head_of(proj, branch)
        if head is not None and after != head:
            return {**verdict, "available": False, "status": "pending",
                    "reason": "head_moved", "moved_to": after}
        if head is not None and memoizable:
            self._memo_put(key, verdict)
        return dict(verdict)

    def _gate_row(self, check: dict) -> dict:
        """One report record as the **gate** sees it.

        The one divergence, and it has no exceptions: **every skip on a
        declared check is a ``fail`` here**, whatever its reason —
        ``fem_extra_missing`` on a machine without the extra, ``mesh_only`` on
        an STL side, ``unsupported_scope`` on a project check declared in a
        part script, ``no_instances``, and whatever reason is added next.

        A report is read by an engineer, who is better served by the named skip
        and its hint. A gate decides a merge, and "declared but not measured"
        is precisely the hole it exists to close: a skip that passed would mean
        swapping a STEP reference for an STL, or reviewing on a machine without
        ``[fem]``, silently satisfies a declared check. The reason and hint ride
        along in ``details`` (plus ``skipped_in_report``) so the proposals UI
        can still say why, and the reason is named in the message so a one-line
        gate summary is actionable.
        """
        if check.get("status") != "skip":
            return check
        reason = check.get("reason") or "not_measured"
        details = dict(check.get("details") or {})
        details.update({"reason": reason, "hint": check.get("hint"),
                        "skipped_in_report": True})
        return {**check, "status": "fail", "details": details,
                "message": f"{check.get('message') or 'not measured'} — "
                           f"declared but not measured ({reason}), and an "
                           "unmeasured spec cannot pass this gate"}

    def _specs_py_changed(self, proj: str, target: str | None,
                          source: str | None,
                          source_head: str | None = None) -> bool:
        """Does this branch pair differ in the root ``specs.py``?

        One ``git diff --name-only``, through ``history._run`` — never a raw
        ``subprocess``, which would miss the hermetic environment. It closes a
        review hole nothing else covers: ``packet.py`` builds diff rows only
        for ``parts/*.py`` and ``merge._validate`` only revalidates changed
        parts, so a proposal that **weakens a spec** would otherwise be
        invisible. A full ``specs`` packet section is a ``packet.py`` change
        and out of scope; this flag costs one git call.

        Memoized on the commit **pair**, and given the source head the verdict
        already resolved: the gate runs on every ``proposal_get``, so a warm
        read must not pay three git round trips for an advisory flag.
        """
        history = getattr(self.service, "history", None)
        if history is None or not target or not source:
            return False
        try:
            canonical = self.service.store.canonical_path_of(proj)
            head_t = history.resolve_branch(canonical, target)
            head_s = source_head or history.resolve_branch(canonical, source)
            if not head_t or not head_s:
                return False
            key = (proj, head_t, head_s, "specs.py")
            hit = self._memo_get(key)
            if hit is not None:
                return bool(hit["changed"])
            # Three dots: merge-base(target, source)..source. The question is
            # "did THIS PROPOSAL touch specs.py", and a plain two-dot diff also
            # reports every change the TARGET made since the branch point —
            # a target that adds a specs.py would flag every open proposal.
            result = history._run(canonical, "diff", "--name-only",
                                  f"{head_t}...{head_s}", "--", "specs.py",
                                  check=False)
            changed = bool((result.stdout or "").strip())
            self._memo_put(key, {"changed": changed})
            return changed
        except Exception:  # noqa: BLE001 — an advisory flag never raises
            return False

    def gate_provider(self):
        """PRD-002's ``service.gate_providers`` entry — **fail-closed**.

        The closure is named ``specs`` on purpose, twice over: the manager
        replaces the built-in gate of the same name (so there is one ``specs``
        gate, not two), and its own except-branch names a gate after the
        provider function, so even a bug in here cannot produce a differently
        named gate.

        It is evaluated against the proposal's **source branch**, not a merge
        preview: "will the merged result be green" needs a staged merge tree
        that does not exist yet and is PRD-004's question, while "is the
        proposed state green" is the one a reviewer actually asks.

        State mapping (PRD-003 owns these):

        ==========  =====================================================
        ``skipped`` the source ref declares no specs at all
        ``pass``    everything declared was measured; nothing failed, errored
                    or was skipped
        ``fail``    anything failed, errored, was skipped, or could not be
                    evaluated — including a kernel error, a source branch that
                    will not build, a ``specs.py`` that will not declare, an
                    exhausted gate budget, and a source head that moved
                    mid-evaluation (retry)
        ==========  =====================================================

        ``pending`` is deliberately **not** in that table. PRD-002's ``merge()``
        blocks a ``fail`` and nothing else, so every state this provider can
        return that is not a measured green has to be ``fail`` — a ``pending``
        specs gate would have merged content no verdict ever measured.

        PRD-002 refuses a merge on any ``fail`` and ``allow_invalid`` cannot
        waive a provider gate — it is the caller's statement about the
        *kernel's* verdict on geometry and must not come to mean two things.
        A declared-but-unmeasured spec is therefore a hard block whose only
        exit is a commit on the source branch a reviewer can see.
        """
        runner = self

        def specs(project: str, proposal: dict) -> dict:
            source = proposal.get("source")
            target = proposal.get("target")
            details = {"status": None, "summary": None, "failures": [],
                       "skips": [], "errors": [], "ref": source,
                       "source_head": None, "specs_py_changed": False,
                       "reason": None}
            if not source:
                # ``evaluate_specs(project, None)`` would measure the CALLER's
                # tree, which is not what this proposal is about. Red, because
                # a proposal we cannot locate is not one we measured.
                details["reason"] = "no_source"
                return {"name": "specs", "state": "fail",
                        "summary": "this proposal names no source branch, so "
                                   "its design specs could not be evaluated",
                        "details": details}
            try:
                verdict = runner.evaluate_specs(project, source)
            except Exception as exc:  # noqa: BLE001 — the provider always
                # answers: ProposalManager's fallback degrades to ``pending``,
                # which is precisely the not-fail-closed outcome this gate
                # exists to prevent.
                payload = exc.to_payload() if hasattr(exc, "to_payload") else {
                    "type": type(exc).__name__, "message": str(exc),
                    "details": dict(getattr(exc, "details", None) or {})}
                details.update({"status": "red", "reason": "evaluation_failed",
                                "error": payload})
                return {"name": "specs", "state": "fail",
                        "summary": f"the design specs on {source!r} could not "
                                   f"be evaluated ({payload['message']}); run "
                                   "run_specs on that branch and try again",
                        "details": details}

            counts = verdict["summary"]
            details.update({
                "status": verdict["status"], "summary": counts,
                "failures": verdict["failures"], "skips": verdict["skips"],
                "errors": verdict["errors"], "ref": source,
                "source_head": verdict["head"], "reason": verdict["reason"],
                "specs_py_changed": runner._specs_py_changed(
                    project, target, source, verdict["head"])})
            state, summary = _gate_wording(source, verdict, counts)
            return {"name": "specs", "state": state, "summary": summary,
                    "details": details}

        return specs
