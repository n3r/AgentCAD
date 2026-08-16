"""The publish gate: nine stages over a package directory, measuring nothing
new.

`agentcad package validate` and (slice 8) `agentcad publish` are this module.
Every stage is a call into a surface that already exists and is reviewed — the
slice-1 validators, the kernel's `inspect` and `connectors` handlers, the
service's `set_params`/`_rebuild`, PRD-003's `SpecRunner` and the server-side
renderer — so this file *orders* measurements and turns them into rows. It
computes no geometry, and it holds no vocabulary of its own: rows are
PRD-004's `items`, statuses its four, stage and report statuses its three, and
`make_item` / `make_stage` / `finalize_report` / `exit_code` are imported from
`core/checks.py` rather than restated.

**The gate is a CORRECTNESS gate, not a security boundary.** It proves that
geometry builds, that specs pass and that connectors mate. It proves nothing
about intent: a package is Python, and `use_part` copies it into the project
where the next rebuild executes it in the kernel worker with the user's
privileges. :data:`SECURITY_NOTE` is that sentence, it is a top-level field of
every report so it travels with every copy of the evidence, and PRD-006 is the
deferred backstop (design Decision 11).

Containment (design Decision 9c), which is the part of this feature that could
damage a user:

* A run materialises into ``<work-dir>/agentcad-package-<pid>-<rand>/``, made
  with ``mkdtemp``, and **deletes only that**. A caller's ``--work-dir`` is
  left exactly as it was, and one that is, holds or sits inside the projects
  root **or the package source directory** is refused with both paths named.
* The stages drive a **second, ephemeral** :class:`AgentCADService` rooted in
  that cell, sharing the warm kernel. Its three seams are nulled —
  `bus.on_publish`, `store.branch_resolver`, `store.write_guard` — and unlike
  PRD-004's runner **this one really writes**: a run adds one part per
  variant, sets parameters, sets an assembly and renders, dozens of guarded
  store writes, so the write guard is live here and nulling it is
  load-bearing rather than prophylactic (`checks._ephemeral_service` predicted
  exactly this).
* **No user project is ever opened**, which is a structural claim and not a
  careful one: the gate creates its own project inside the cell and never
  learns the name of another.

The gate's claim about a package, stated once: *every part builds at each
parameter's own declared range and at every configuration the package ships,
its specs hold there, and every connector it declares resolves.* Never "every
combination" — see :func:`variants`.

The report is a PRD-004 document with three additions — `package`, `note` and
the verdict (`publishable` / `exempt_skips` / `blockers`). Its stage names are
not `checks.ALL_STAGES`, so :func:`validate_gate_report` is
`checks.validate_report` **minus exactly that vocabulary difference** and plus
the gate's own keys; see its docstring.
"""

from __future__ import annotations

import ast
import os
import platform
import shutil
import struct
import sys
import tempfile
import time
import zlib
from collections import namedtuple
from pathlib import Path

import agentcad

from ...kernel.client import KernelError
from .. import checks
from ..checks import exit_code, finalize_report, make_item, make_stage
# `_finite` is imported rather than re-derived: it is the one place that
# refuses a NaN limit, and PRD-004 paid for that reasoning once already (a NaN
# deadline is never in the past, so it bounds nothing).
from ..checks import _finite
from ..model import AppError, NotFoundError, ValidationError
from . import _json, content, format as pkgformat

#: The nine stages, in dependency- and cost-order. A run always reports all
#: nine — an unselected one is `skip`/`not_selected` — so a consumer never has
#: to guess whether a stage was green or absent.
GATE_STAGES = ("format", "contract", "presets", "build", "specs",
               "connectors", "previews", "docs", "policy")

#: What this build measures. Slice 4 shipped three of the nine and reported
#: the rest as `skip`/`not_implemented`; slice 5 closed the gap, and the
#: constant stays because a stage that is *declared* and not *implemented*
#: must never silently read as measured.
IMPLEMENTED_STAGES = GATE_STAGES

#: Closed set. Each member is a fact about the **world**, never about the
#: package's correctness (design Decision 10), so a `skip` carrying one may
#: not block a publish. A member that was a fact about the package would let a
#: broken package publish, which is the whole failure mode this set exists to
#: avoid. Rows carrying one are `strict_exempt`, so `--strict` leaves them
#: alone too: a warning nothing can ever clear teaches readers to ignore
#: warnings. A row exempted here is recorded in `report["exempt_skips"]` as
#: `<stage>:<reason>` — the same shape a stage-level exemption gets, because
#: slice 8 publishes that list into the index entry and a consumer must not
#: have to parse two shapes.
PUBLISH_SKIP_EXEMPT = ("fem_extra_missing", "no_policy_configured",
                       "string_param_unbounded", "no_connectors_declared",
                       "reference_part")

#: Stage-level skips that do not block a publish, as **(stage, reason)**. A
#: stage skipped for any other reason — `not_selected`, `budget_exceeded`,
#: `not_implemented` — **was not measured**, and "we did not look" may not read
#: as "publishable". The two members are *legitimate absences*: a package may
#: ship no configurations and no SPECS, exactly as `no_connectors_declared`
#: says a plain solid may declare no connectors. Both are recorded in
#: `report["exempt_skips"]` as `<stage>:<reason>`, so a consumer reads what
#: was not measured rather than inferring it.
#:
#: **Pairs, not bare reasons.** Membership used to be tested against the reason
#: alone, so any *other* stage that ever emitted `not_declared` would have been
#: exempted by a string it does not own — a latent hole that cost nothing to
#: close and would have been silent when it opened.
STAGE_SKIP_EXEMPT = (("presets", "no_presets_declared"),
                     ("specs", "not_declared"))

#: The non-claim, in the words design Decision 11 fixes. It is a top-level
#: report field, it is in every tool description, the CLI prints it above the
#: verdict, and `provenance.header` puts it in the consumer's own repository.
SECURITY_NOTE = (
    "The publish gate is a CORRECTNESS gate, not a security boundary: it "
    "proves that the geometry builds, that the specs pass and that the "
    "connectors mate. Package scripts run in your kernel worker with your "
    "privileges. See docs/packages.md."
)

#: The scratch project the cell holds. One name, so a stage never has to be
#: told which project it is measuring.
GATE_PROJECT = "pkg_gate"

#: Where the package snapshot lives inside the cell. Leading dot, so it can
#: never be mistaken for (or collide with) a project the ephemeral service
#: makes — a project id is `^[a-z][a-z0-9_]{0,39}$`.
SNAPSHOT_DIR = ".package-snapshot"

#: A README shorter than this is a stub, not documentation. The floor is 200
#: characters because the smallest README this repository ships is
#: `examples/prototyping/README.md` at 3 061 bytes (the others run to 6 864),
#: so 200 is ~15x below real content and only ever refuses `# name`.
MIN_README_CHARS = 200

#: Where a package's documentation lives (design Decision 1's layout).
README_PATH = "docs/README.md"

_NUMERIC = ("number", "int")

#: The renders the previews stage produces. Small on purpose: a preview is a
#: listing thumbnail, and the gate renders one per part per run.
PREVIEW_SIZE = (320, 240)

#: The bundled probe: a 2 mm cube with a single **rigid** connector at its
#: origin. The moving side of a mate must be rigid (the anchor connector
#: carries the DOF), so a part that declares only non-rigid connectors has
#: nothing of its own to mate *with* — the probe is what makes its connectors
#: testable at all. It is a script, not geometry: this module imports no
#: kernel.
#
# Written as a concatenation of quoted lines rather than a triple-quoted
# block on purpose: `tests/test_packages_ocp_free.py`'s static scan reads this
# file line by line and would (rightly) flag a line *starting* with a
# build123d import, even inside a string. The probe's import belongs to the
# worker, never to this process.
PROBE_SCRIPT = (
    '"""The publish gate\'s mate probe: a 2 mm cube with one rigid '
    'connector."""\n'
    "\n"
    "from build123d import Box\n"
    "\n"
    "PARAMS = {}\n"
    "\n"
    "\n"
    "def build(p):\n"
    "    return Box(2.0, 2.0, 2.0)\n"
    "\n"
    "\n"
    "def connectors(p, part):\n"
    '    return {"tip": {"type": "rigid", '
    '"location": ((0, 0, 0), (0, 0, 0))}}\n'
)

#: One variant: the row subject, the parameter overrides, and a human label.
Variant = namedtuple("Variant", "id params label")

#: What a published numeric parameter must declare. `handle_inspect` makes all
#: four optional, so without this the gate's "builds at every parameter's min
#: and max" claim would be vacuous on an unbounded parameter (Decision 9a).
NUMERIC_REQUIRED = ("min", "max", "unit", "description")


# --------------------------------------------------------------- the cell


def _ephemeral_service(cell: Path, kernel):
    """A second :class:`AgentCADService` rooted in *cell*, sharing *kernel*.

    Returns ``(service, registry, project)``. The three assignments below are
    the dangerous part of this feature and each is named for the failure it
    prevents; the order is load-bearing, because `build_registry` is what
    installs the last two.

    The kernel is **shared, never restarted**: a second pool costs ~3 s and
    ~0.5 GB per worker to run the same builds.
    """
    from ..service import AgentCADService, EventBus
    from ..tools import build_registry

    cell = Path(cell).resolve()
    service = AgentCADService(cell, kernel, EventBus())
    # NON-NEGOTIABLE. `AgentCADService.__init__` installs `_snapshot_on_event`
    # as the bus's pre-fan-out hook, so any `project_changed` publish commits a
    # git snapshot — and the gate publishes one per `create_part` and per
    # `set_params`, dozens per run, into a throwaway directory.
    service.bus.on_publish = None
    service.create_project(GATE_PROJECT)
    registry = build_registry(service)
    # NON-NEGOTIABLE, and only meaningful AFTER `build_registry`: the
    # versioning pack constructs a `BranchManager` when git is on PATH, and
    # constructing one installs `store.branch_resolver`. Left installed it
    # resolves every read and write against a `.history/agentcad/` sidecar
    # that does not exist here — and writes one.
    service.store.branch_resolver = None
    # NON-NEGOTIABLE, and the seam PRD-004 predicted would one day be live:
    # `checks._ephemeral_service` records that its write guard is "inert BY
    # ACCIDENT. One future write inside a stage would make it live." **This is
    # that write.** The gate calls `create_part` and `set_params` dozens of
    # times per run, each of which is a guarded store write, and the guard's
    # first act is `branches.ensure_checkout`, which materialises a branch
    # working tree in the repository the store belongs to.
    service.store.write_guard = None
    return service, registry, GATE_PROJECT


def _within(inner: Path, outer: Path) -> bool:
    try:
        return Path(inner).is_relative_to(Path(outer))
    except (TypeError, ValueError):     # pragma: no cover - defensive
        return False


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _elapsed(started: float) -> float:
    return round(time.monotonic() - started, 3)


def _payload(exc: BaseException) -> dict:
    """Any exception as the structured payload the tools already return —
    `checks._payload`'s rule: a `KernelError` travels **verbatim**, with the
    same traceback, line and Error-Doctor hint an agent already knows how to
    read."""
    if isinstance(exc, KernelError):
        return exc.to_payload()
    if isinstance(exc, AppError):
        name = type(exc).__name__.replace("Error", "").lower() + "_error"
        return {"type": name, "message": exc.message, "details": exc.details}
    return {"type": "internal_error", "message": f"{type(exc).__name__}: {exc}",
            "details": {}}


# ------------------------------------------------------- the variant matrix


def variants(part: str, params_spec: dict | None,
             presets: dict | None = None) -> list[Variant]:
    """The variants of one part: the default, then **one parameter at a time**,
    then every declared configuration.

    ===============  =====================================================
    `number` / `int` `min` and `max` (each, on its own)
    `bool`           `True` and `False`
    `enum`           every choice
    `string`         nothing — the space is unbounded, and the `build` stage
                     says so in a `skip / string_param_unbounded` row
    ===============  =====================================================

    Count: ``1 + Σ|sweep(param)| + |presets|`` — a **sum, never a product**,
    and a test asserts the arithmetic rather than the intention. The corner
    cross-product is deliberately out of scope (design Decision 9a):
    parameters are routinely mutually constrained (a wall thicker than a bore
    radius), so demanding every corner would redden *correct* content, which
    is the worse failure. An author who wants a corner declares it **as a
    preset**, and the gate builds every preset.

    The gate's claim is therefore exactly: *each parameter's own range, and
    every configuration the package ships.* Never "every combination", and no
    document may say otherwise.
    """
    out = [Variant(f"{part}@default", {}, "defaults")]
    for name, spec in (params_spec or {}).items():
        for value, tag in _sweep(spec):
            out.append(Variant(f"{part}@{name}={tag}", {name: value},
                               f"{name} at {tag}"))
    for config, entry in (presets or {}).items():
        params = entry.get("params") if isinstance(entry, dict) else None
        label = entry.get("label") if isinstance(entry, dict) else None
        out.append(Variant(f"{part}@preset:{config}", dict(params or {}),
                           label or config))
    return out


def _sweep(spec: dict) -> list[tuple]:
    ptype = spec.get("type", "number")
    if ptype in _NUMERIC:
        # A bound the part did not declare is a `contract` failure, not a
        # variant this function can invent.
        return [(spec[key], key) for key in ("min", "max")
                if spec.get(key) is not None]
    if ptype == "bool":
        return [(True, "true"), (False, "false")]
    if ptype == "enum":
        return [(choice, str(choice)) for choice in spec.get("choices") or []]
    return []


def unswept(params_spec: dict | None) -> list[str]:
    """The parameters whose space the sweep does **not** cover — today only
    strings, whose domain is unbounded."""
    return [name for name, spec in (params_spec or {}).items()
            if spec.get("type") == "string"]


# ------------------------------------------------------------- the verdict


def verdict(stages: list[dict]) -> dict:
    """``{publishable, exempt_skips, blockers}`` over the stage blocks.

    Fail-closed and pure. A `fail` or an `error` blocks. A `skip` blocks
    unless its reason is in :data:`PUBLISH_SKIP_EXEMPT`. A whole stage that
    was skipped blocks unless its `(name, reason)` is in
    :data:`STAGE_SKIP_EXEMPT` — which is what makes `validate --stages format`
    honest: a subset run cannot answer "publishable", because it did not look.

    **A stage that produced NO rows and gave no reason blocks too**, and that
    is the one rule here that is not obvious. Both branches used to key off
    `stage["reason"]`, so `make_stage(name, [])` — reason `None`, zero items —
    was invisible: it contributed nothing to `blockers`, nothing to
    `exempt_skips`, and nothing to the summary, so the report said green and
    the stage had measured *nothing*. `presets.json = {"format": 1, "presets":
    {}}` reached it. A stage that measured nothing is a stage that did not
    look, whether it says so or not; if the absence is legitimate the stage
    says which absence, and that reason is what makes it exempt and disclosed.

    **Every entry in `exempt_skips` is `<stage>:<reason>`**, row-level and
    stage-level alike. Slice 8 publishes this list into the index entry, where
    it is what stops "validated" from becoming a badge — so it is one shape,
    and it names where the measurement was not made.
    """
    blockers: list[str] = []
    exempt: list[str] = []
    for stage in stages:
        name = str(stage.get("name"))
        reason = stage.get("reason")
        rows = stage.get("items") or []
        if reason and (name, reason) not in STAGE_SKIP_EXEMPT:
            blockers.append(name)
        elif reason:
            exempt.append(f"{name}:{reason}")
        elif not rows:
            blockers.append(name)
        for item in rows:
            status = item.get("status")
            if status in ("fail", "error"):
                blockers.append(str(item.get("id")))
            elif status == "skip":
                if item.get("reason") in PUBLISH_SKIP_EXEMPT:
                    # `<stage>:<reason>`, the SAME shape a stage-level exempt
                    # skip gets. Slice 8 copies this list verbatim into the
                    # published index entry's `gate.exempt_skips`, so it is a
                    # format: one shape, parseable, and it says *where* the
                    # measurement was not made. (Slice 5 emitted a bare reason
                    # here and `<stage>:<reason>` above — two shapes in one
                    # list, which is what this qualification fixes.)
                    exempt.append(f"{stage.get('name')}:{item.get('reason')}")
                else:
                    blockers.append(str(item.get("id")))
    return {"publishable": not blockers,
            "exempt_skips": sorted(set(exempt)),
            "blockers": blockers}


# ----------------------------------------------------------- the validator


def validate_gate_report(report) -> list[str]:
    """Every problem with a gate report, in English (empty = valid).

    A gate report **is** a PRD-004 report — same `schema`, same rows, same
    summary, same verdict arithmetic — with one deliberate difference:
    :data:`GATE_STAGES` is not `checks.ALL_STAGES`. `checks.validate_report`
    is the schema and may not be edited (it belongs to PRD-004), so this
    function runs it and subtracts **exactly** the unknown-stage-name problems
    it raises for names this module does declare, then applies the gate's own
    stage-name rule and checks the three additional keys.

    **The subtraction is by exact message, and that coupling is deliberate
    but sharp.** The strings this function builds must match
    `checks.validate_report`'s unknown-stage-name problem **character for
    character**, including the `', '.join(checks.ALL_STAGES)` tail. If PRD-004
    rewords that message, nothing is silently dropped — the subtraction simply
    stops matching and every gate report starts failing validation, which
    would take `publish` down with it. That is loud by design (a pattern match
    would have *quietly* let an unknown stage through), and it is contained by
    `test_the_report_is_a_prd004_report_apart_from_its_stage_names`, which
    reconstructs the message from `checks.ALL_STAGES` and goes red in the test
    suite before it can go red in a publisher. **If you are editing
    `checks.validate_report`'s message: this function and that test are the
    two places to update, and the test will tell you.**

    Duplicate stage names are a problem in their own right: a report carrying
    `format` twice satisfies every per-index check and lets `_stage()` — which
    returns the *first* match — digest one copy while the verdict reads both.
    """
    problems = checks.validate_report(report)
    if not isinstance(report, dict):
        return problems
    stages = report.get("stages")
    subtract: set[str] = set()
    if isinstance(stages, list):
        seen: set = set()
        for index, stage in enumerate(stages):
            name = stage.get("name") if isinstance(stage, dict) else None
            if name in GATE_STAGES:
                subtract.add(
                    f"stages[{index}]: unknown stage name {name!r}; expected "
                    f"one of {', '.join(checks.ALL_STAGES)}")
            else:
                problems.append(
                    f"stages[{index}]: unknown gate stage {name!r}; expected "
                    f"one of {', '.join(GATE_STAGES)}")
            if name in seen:
                problems.append(
                    f"stages[{index}]: stage {name!r} appears more than once; "
                    f"a report has exactly one block per stage")
            seen.add(name)
    problems = [problem for problem in problems if problem not in subtract]

    package = report.get("package")
    if not isinstance(package, dict):
        problems.append("package is not an object")
    else:
        for key in ("name", "version", "content_id"):
            if key not in package:
                problems.append(f"package is missing {key!r}")
    if not isinstance(report.get("note"), str) or not report.get("note"):
        problems.append("note is not a non-empty string")
    if not isinstance(report.get("publishable"), bool):
        problems.append("publishable is not a boolean")
    for key in ("exempt_skips", "blockers"):
        if not isinstance(report.get(key), list):
            problems.append(f"{key} is not a list")
    return problems


# ---------------------------------------------------------------- the gate


class PackageGate:
    """The gate. **Stateless** — everything a run needs lives on the
    :class:`_Run` it creates, so one gate object can serve two concurrent
    callers without sharing a deadline or a report.

    Nothing is read off the service at construction: the tool pack loads at
    `pac`, before `tools_proposals` (`p`), `tools_specs` (`s`) and
    `tools_versioning` (`v`), so `service.specs`, `service.branches` and
    `service.gate_providers` do not exist yet and a gate that captured them
    would capture nothing forever.
    """

    def __init__(self, service):
        self._service = service

    def run(self, path, *, stages=GATE_STAGES, strict: bool = False,
            work_dir: str | None = None,
            budget_s: float | None = None) -> dict:
        """Measure the package at *path* and answer with one report.

        Everything the gate *measured* is payload: a package whose parts do
        not build is a red report, never an exception. The exceptions that do
        leave here are the harness's own — an unknown directory, an unknown
        stage, an unusable `--work-dir`, a non-finite budget — because those
        mean *we could not produce a verdict*, which is exit 2.
        """
        selected = _selected(stages)
        budget = (None if budget_s is None
                  else _finite(budget_s, "budget_s", "--budget"))
        source = Path(path).expanduser().resolve()
        if not source.is_dir():
            raise NotFoundError(
                f"no package directory at {source}: a package is a directory "
                f"holding package.json, parts/ and docs/README.md",
                {"path": str(source)})

        started_at, started = _now(), time.monotonic()
        root = self._work_root(work_dir, source)
        owned = work_dir is None
        cell = Path(tempfile.mkdtemp(
            prefix=f"agentcad-package-{os.getpid()}-", dir=str(root))).resolve()
        try:
            run = _Run(source, selected,
                       deadline=None if budget is None else started + budget,
                       # PRD-031 FR2(b)'s seam, read at run time off the
                       # caller's service (never captured at construction).
                       policy=getattr(self._service, "package_policy", None))
            blocks = run.measure(cell, self._service.kernel)
        finally:
            # Only what this run created. A caller who passed --work-dir keeps
            # the directory itself and everything that was already in it.
            shutil.rmtree(cell, ignore_errors=True)
            if owned:
                shutil.rmtree(root, ignore_errors=True)

        report = finalize_report(
            run.package_name or source.name, blocks,
            source={"kind": "worktree", "dirty": False},
            host=_host(self._service.kernel), started=started_at,
            duration_s=time.monotonic() - started, strict=strict,
            complete=not run.truncated, warnings=run.warnings,
            errors=run.errors)
        report["package"] = {"name": run.package_name,
                             "version": run.package_version,
                             "content_id": run.content_id}
        report["note"] = SECURITY_NOTE
        ruling = verdict(blocks)
        report.update(ruling)
        report["publishable"] = ruling["publishable"] and report["complete"]
        return report

    # ---------------------------------------------------------- the work dir

    def _work_root(self, work_dir: str | None, source: Path) -> Path:
        """The caller's ``--work-dir``, resolved and proven not to overlap
        anything it may not, or a temp dir this run owns.

        Created only after it is accepted, so a refused path never leaves a
        directory behind.
        """
        if work_dir is None:
            return Path(tempfile.mkdtemp(prefix="agentcad-package-")).resolve()
        root = Path(work_dir).expanduser().resolve()
        self._refuse_overlap(root, source)
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _refuse_overlap(self, root: Path, source: Path) -> None:
        """Refuse a work dir that is, holds or lives inside the projects root
        or the package source directory.

        `checks._refuse_overlap` plus one path. The projects root is the
        catastrophic case (a gate that writes there writes into the user's
        work); the package directory is the one PRD-004 did not have — a cell
        inside it would change the very content id the gate is attesting to,
        and the teardown would delete part of the package.
        """
        root = Path(root).resolve()
        projects = Path(self._service.store.root).resolve()
        for label, path in (("the projects root", projects),
                            ("the package directory", source.resolve())):
            if root == path or _within(path, root) or _within(root, path):
                raise ValidationError(
                    f"--work-dir {root} overlaps {label} {path}: the gate "
                    f"materialises a throwaway cell under the work dir and "
                    f"deletes it afterwards, so it must not be, contain or "
                    f"sit inside either — pass a directory elsewhere, or omit "
                    f"--work-dir for a temp dir",
                    {"work_dir": str(root), "projects_root": str(projects),
                     "package_dir": str(source)})


def _selected(stages) -> set[str]:
    names = tuple(GATE_STAGES if stages is None else stages)
    unknown = [name for name in names if name not in GATE_STAGES]
    if unknown:
        raise ValidationError(
            f"unknown gate stage(s) {', '.join(repr(n) for n in unknown)}; "
            f"expected any of {', '.join(GATE_STAGES)}",
            {"stages": list(GATE_STAGES), "unknown": unknown})
    return set(names)


def _host(kernel) -> dict:
    """The machine, so a report read on another one is interpretable.

    `build123d` is here and not in `checks._host` because a package's real
    compatibility surface is the pinned kernel stack, not the app version
    (design Decision 2): the index entry records what the package was *proved*
    against, and only the kernel knows it.
    """
    from .. import specs as _specs

    try:
        fem = bool(_specs._fem_available())
    except Exception:  # noqa: BLE001 — an optional extra, never a failure
        fem = False
    host = {"platform": platform.system().lower(),
            "python": sys.version,
            "agentcad": agentcad.__version__,
            "build123d": None,
            "fem": fem,
            "sandbox": bool(getattr(kernel, "sandboxed", False)),
            "pool_size": int(getattr(kernel, "size", 1)),
            "kernel_pool": type(kernel).__name__ if kernel else None}
    try:
        host["build123d"] = kernel.request("ping", {}).get("build123d")
    except Exception:  # noqa: BLE001 — provenance must never break a run
        pass
    return host


# ------------------------------------------------------------------ a run


class _Run:
    """One gate run: the state, the stages, and no other run's policy."""

    def __init__(self, source: Path, selected: set[str], *,
                 deadline: float | None, policy=None):
        #: Where the package really lives. `self.source` becomes the snapshot
        #: inside the cell once `_read_package` has taken one, and every stage
        #: reads `self.source`; this is what the moving-target check re-hashes.
        self.origin = source
        self.source = source
        self.selected = selected
        self.deadline = deadline
        self.policy = policy
        self.truncated = False
        self.seen: set[str] = set()
        self.warnings: list[str] = []
        self.errors: list[dict] = []
        self.doc: dict | None = None
        self.doc_error: str | None = None
        self.inventory: list | None = None
        self.inventory_error: str | None = None
        self.content_id: str | None = None
        self.package_name: str | None = None
        self.package_version: str | None = None
        self.specs: dict[str, dict] = {}      # part id -> inspected PARAMS spec
        self.spec_errors: dict[str, dict] = {}   # part id -> inspect payload
        self.service = None
        self.registry = None
        self.scratch: dict[str, str] = {}     # package part id -> scratch part
        self.variant_of: dict[str, str] = {}  # scratch part id -> row subject
        self.probe: str | None = None         # the probe part, made on demand
        self.reference_scratch: set[str] = set()   # scratch ids of imports

    # ------------------------------------------------------------- driving

    def measure(self, cell: Path, kernel) -> list[dict]:
        self._read_package(cell)
        self.service, self.registry, _proj = _ephemeral_service(cell, kernel)
        blocks = [self._stage(name) for name in GATE_STAGES]
        self._refuse_a_moving_target()
        return blocks

    def _refuse_a_moving_target(self) -> None:
        """The **origin** is re-hashed after the stages, and an origin that
        moved makes the report **incomplete**.

        With the snapshot in place this is no longer what makes the id
        trustworthy — the stages read the snapshot, and the published id is the
        snapshot's, so a tree that moves mid-run can no longer make the report
        describe bytes nobody measured. What it still catches is the thing a
        *publisher* has to know: the directory it is about to publish is not
        the directory the gate copied. `publish` re-hashes the source too and
        refuses on its own, so this is the earlier, more explanatory half of
        the same refusal. The re-hash costs ~1 ms on a realistic package and
        ~67 ms at the published ceiling (changelog 0168).
        """
        if self.content_id is None:
            return
        try:
            after = content.content_id(self.origin)
        except ValidationError as exc:
            after = None
            self.warnings.append(f"the package tree could not be re-read after "
                                 f"the run: {exc}")
        if after == self.content_id:
            return
        self.truncated = True
        self.warnings.append(
            f"the package directory changed while the gate was running: the "
            f"gate measured {self.content_id} and {self.origin} now hashes to "
            f"{after}. The rows above describe the snapshot the gate took, "
            f"which is no longer what is on disk, so this report is not a "
            f"verdict for that directory — re-run it.")

    def _stage(self, name: str) -> dict:
        started = time.monotonic()
        if name not in self.selected:
            return make_stage(name, reason="not_selected")
        if name not in IMPLEMENTED_STAGES:
            return make_stage(name, reason="not_implemented")
        if self._out_of_budget():
            self.truncated = True
            return make_stage(name, reason="budget_exceeded")
        try:
            return getattr(self, f"_stage_{name}")(started)
        except Exception as exc:  # noqa: BLE001 — one row, never the end of a run
            payload = _payload(exc)
            self.errors.append({**payload, "stage": name})
            item = self._item(name, "check", name, "error",
                              f"the {name} stage did not complete: "
                              f"{payload['message']}", error=payload)
            return make_stage(name, [item], duration_s=_elapsed(started))

    def _item(self, stage, kind, subject, status, message, **kwargs) -> dict:
        return make_item(stage, kind, subject, status, message,
                         seen=self.seen, warnings=self.warnings, **kwargs)

    def _out_of_budget(self) -> bool:
        return self.deadline is not None and time.monotonic() > self.deadline

    def _budget_item(self, stage: str, kind: str, subject: str) -> dict:
        self.truncated = True
        return self._item(
            stage, kind, subject, "skip", "not measured: the budget ran out",
            reason="budget_exceeded",
            hint="raise --budget, or select fewer stages")

    # -------------------------------------------------------------- reading

    def _read_package(self, cell: Path) -> None:
        """**Snapshot the package into the cell**, then read `package.json`
        and the inventory off the snapshot.

        This is the fix for a TOCTOU the gate used to have wide open: the id
        was hashed once at the start and every stage then read the tree
        *live*, so a file swapped in after the hash and swapped back before
        the closing re-hash was measured by the stages and invisible to both
        endpoint comparisons (`_refuse_a_moving_target` compares endpoints, not
        the interval). The published id then belonged to bytes no stage read.

        Copying first and hashing the copy closes it structurally rather than
        carefully: **the id is the id of the bytes the stages consumed**,
        because there is only one tree they can consume and nothing outside
        this process can reach it. The cell is already private, already
        `mkdtemp`-made, already deleted afterwards, and already proven not to
        overlap the package directory, so the snapshot costs one copy of a
        tree the inventory has just read anyway.

        The copy is skipped when the tree breaks the published ceilings: those
        are already a red `format` row, and copying up to a hostile amount of
        data to report a size problem would be the wrong trade. In that one
        case the stages read the origin, exactly as they used to — and the
        report is red either way.

        Both read failures are recorded rather than raised: the `format` stage
        is where they become rows, and `contract` still has to be able to say
        that it could not run.
        """
        self._snapshot(cell)
        try:
            self.doc = _json.read_object(self.source / "package.json",
                                         "package.json")
        except ValidationError as exc:
            self.doc_error = exc.message
        else:
            name, version = self.doc.get("name"), self.doc.get("version")
            self.package_name = name if isinstance(name, str) else None
            self.package_version = version if isinstance(version, str) else None

    def _snapshot(self, cell: Path) -> None:
        """Copy the inventoried files into the cell and read from there."""
        try:
            entries = content.inventory(self.origin)
        except ValidationError as exc:
            self.inventory_error = str(exc)
            return
        if content.check_ceilings(entries):
            # Over the ceilings: a red `format` row, and not a tree to copy.
            self.inventory = entries
            self.content_id = content.content_id_of(entries)
            return
        snapshot = Path(cell) / SNAPSHOT_DIR
        try:
            _copy_inventory(self.origin, snapshot, entries)
            self.inventory = content.inventory(snapshot)
        except (ValidationError, OSError) as exc:
            self.inventory_error = (
                f"the package could not be copied into the gate's work cell: "
                f"{exc}")
            return
        self.source = snapshot
        self.content_id = content.content_id_of(self.inventory)

    def _declared_parts(self) -> dict:
        """Every part the manifest declares **usably** — a `script` part with a
        `file`, or a `reference` part (FR13) with a `source`. An entry missing
        its own kind's key is already a `format` row and is not measured twice.
        """
        parts = (self.doc or {}).get("parts")
        if not isinstance(parts, dict):
            return {}
        return {pid: entry for pid, entry in parts.items()
                if isinstance(entry, dict)
                and pkgformat.part_payload(entry)[1] is not None}

    def _script_parts(self) -> dict:
        return {pid: entry for pid, entry in self._declared_parts().items()
                if pkgformat.part_kind(entry) == "script"}

    def _is_reference(self, entry: dict) -> bool:
        return pkgformat.part_kind(entry) == "reference"

    def _part_source(self, part_id: str, entry: dict) -> str:
        """The part script's text, or a `ValidationError` naming the file."""
        path = content.resolve_within(self.source, entry["file"],
                                      what=f"parts.{part_id}.file")
        try:
            return path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise ValidationError(
                f"{entry['file']} cannot be read: {exc}") from exc

    def _reference_item(self, stage: str, part_id: str, what: str,
                        hint: str) -> dict:
        """The one row shape a reference part contributes to a stage that only
        a script can satisfy. `reference_part` is in
        :data:`PUBLISH_SKIP_EXEMPT` — a package of imported vendor geometry is
        a legitimate package, and the absence of a script is a fact about the
        *kind*, never about the package's correctness."""
        return self._item(
            stage, "part", part_id, "skip",
            f"{part_id} is a reference part (imported geometry, no script), "
            f"so {what}",
            reason="reference_part", hint=hint, strict_exempt=True)

    # -------------------------------------------------------- stage: format

    def _stage_format(self, started: float) -> dict:
        """`package.json`, the inventory, the ceilings, the docs floor and the
        previews — pure data, one row per problem, each naming the file."""
        items: list[dict] = []
        if self.doc_error is not None:
            items.append(self._item("format", "check", "package.json", "fail",
                                    self.doc_error))
        else:
            problems = pkgformat.validate_package_manifest(self.doc,
                                                           root=self.source)
            for problem in problems:
                items.append(self._item(
                    "format", "check", problem.get("field") or "package.json",
                    "fail", problem["message"],
                    details={"code": problem.get("code"),
                             "field": problem.get("field")}))
            if not problems:
                items.append(self._item(
                    "format", "check", "package.json", "pass",
                    f"{self.package_name}@{self.package_version} — "
                    f"{len(self._declared_parts())} part(s), "
                    f"licence {self.doc.get('license')}, disclosure "
                    f"{self.doc.get('disclosure')}"))
        items += self._format_inventory()
        items += self._format_docs()
        items += self._format_previews()
        items += self._format_part_files()
        return make_stage("format", items, duration_s=_elapsed(started))

    def _format_inventory(self) -> list[dict]:
        if self.inventory is None:
            return [self._item("format", "check", "inventory", "fail",
                               self.inventory_error or "the tree cannot be read")]
        problems = content.check_ceilings(self.inventory)
        if problems:
            return [self._item("format", "check",
                               problem.get("field") or "ceilings", "fail",
                               problem["message"],
                               details={"code": problem["code"]})
                    for problem in problems]
        total = sum(size for _p, size, _s in self.inventory)
        return [self._item(
            "format", "check", "inventory", "pass",
            f"{len(self.inventory)} file(s), {total} bytes, {self.content_id}",
            details={"files": len(self.inventory), "bytes": total,
                     "content_id": self.content_id})]

    def _format_docs(self) -> list[dict]:
        path = self.source / README_PATH
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return [self._item(
                "format", "check", README_PATH, "fail",
                f"{README_PATH} is missing: a published package documents "
                f"itself, and the README is the first thing a consumer reads")]
        if len(text.strip()) < MIN_README_CHARS:
            return [self._item(
                "format", "check", README_PATH, "fail",
                f"{README_PATH} is {len(text.strip())} characters; a README "
                f"under {MIN_README_CHARS} is a stub, not documentation")]
        return [self._item("format", "check", README_PATH, "pass",
                           f"{README_PATH} — {len(text.strip())} characters")]

    def _format_previews(self) -> list[dict]:
        previews = sorted(
            path.relative_to(self.source).as_posix()
            for path in (self.source / "previews").glob("*.png")
        ) if (self.source / "previews").is_dir() else []
        if not previews:
            return [self._item(
                "format", "check", "previews", "fail",
                "no previews/*.png: a package ships a rendered preview per "
                "part, which is what a search hit and the Library dialog show")]
        return [self._item("format", "check", "previews", "pass",
                           f"{len(previews)} preview(s): {', '.join(previews)}",
                           details={"previews": previews})]

    def _format_part_files(self) -> list[dict]:
        """One row per declared part, measured against **the inventory** — and
        one row per inventoried script no part declares.

        The inventory is the tree the content id is computed over, and it is
        the only thing a consumer receives: `_copy_inventory` copies exactly
        it into the cache and into the index. So "the file is on disk" is the
        wrong question, and asking it was a real hole in both directions:

        * a declared part at `parts/x.tmp` passed every stage — the gate
          inspected it, built it, ran its specs — while `content.IGNORED`
          excluded it from the id, so publish shipped a **scriptless package
          advertised green**, with the parts digest of a script that is not in
          it;
        * an *undeclared* `parts/y.py` shipped inside the id with no stage
          ever opening it: code a consumer receives and the gate never claims
          anything about.

        Both are `fail`, and both name the path. The rule to keep is one
        sentence: **the gate measures what ships.**
        """
        items = []
        # A tree that could not be inventoried at all (a symlink in it, an
        # unreadable file) is ALREADY one honest `format` row from
        # `_format_inventory`. Comparing every declared part against an empty
        # set here would add one spurious "not in the package's content" row
        # per part, all of them naming the wrong cause.
        known = self.inventory is not None
        inventoried = {path for path, _size, _sha in (self.inventory or [])}
        declared_paths: set[str] = set()
        for part_id, entry in self._declared_parts().items():
            key, relpath = pkgformat.part_payload(entry)
            try:
                path = content.resolve_within(self.source, relpath,
                                              what=f"parts.{part_id}.{key}")
            except ValidationError:
                continue
            declared_paths.add(relpath)
            if known and relpath not in inventoried:
                code, why = self._why_not_inventoried(relpath, path,
                                                      inventoried)
                items.append(self._item(
                    "format", "part", f"parts.{part_id}", "fail",
                    f"{relpath} is declared by {part_id!r} and is NOT in the "
                    f"package's content, so it is not hashed, not cached and "
                    f"not published — the gate would prove a script the "
                    f"package does not ship. {why}",
                    details={key: relpath, "kind": pkgformat.part_kind(entry),
                             "inventoried": False, "cause": code}))
                continue
            if not path.is_file():
                # Already a `parts.<id>.<key>` problem from the manifest
                # validator, and two rows for one fact would be two ids for one
                # subject.
                continue
            items.append(self._item(
                "format", "part", f"parts.{part_id}", "pass",
                f"{relpath} — {path.stat().st_size} bytes "
                f"({pkgformat.part_kind(entry)})",
                details={key: relpath, "kind": pkgformat.part_kind(entry),
                         "inventoried": True}))
        for relpath in sorted(inventoried - declared_paths):
            if not relpath.startswith(pkgformat.PART_FILE_DIR) \
                    or not relpath.endswith(".py"):
                continue
            items.append(self._item(
                "format", "check", relpath, "fail",
                f"{relpath} ships inside this package's content id and no "
                f"part declares it, so no stage ever opened it: it is not "
                f"inspected, not built, not spec-checked and not policy-"
                f"checked, yet a consumer receives it. Declare it in "
                f"package.json parts, or delete it",
                details={"path": relpath, "declared": False}))
        return items

    # ------------------------------------------------------ stage: contract

    def _why_not_inventoried(self, relpath: str, path: Path,
                             inventoried: set) -> tuple[str, str]:
        """``(code, sentence)`` — why a declared file is not in the inventory.

        There are several ways to be absent from a tree the inventory just
        walked, and they need different fixes. Naming the ignore patterns
        unconditionally was right for the case that motivated the row and wrong
        for the others: a part that is simply missing would have been told to
        rename itself, which does nothing.

        A **symlink** is deliberately not one of the answers: `content.
        inventory` refuses any symlink outright, so a symlinked part fails the
        whole inventory and `_format_inventory` reports it by name before this
        function is reached. This is only ever called when the inventory *was*
        read.
        """
        if content.is_ignored(relpath):
            return "ignored_pattern", (
                f"It matches a pattern the content id excludes "
                f"({', '.join(content.IGNORED)}). Rename it to a path the "
                f"content id covers.")
        folded = {entry.lower() for entry in inventoried}
        if relpath.lower() in folded:
            near = sorted(entry for entry in inventoried
                          if entry.lower() == relpath.lower())
            return "case_mismatch", (
                f"The tree holds {', '.join(near)} — the same name in a "
                f"different case. This filesystem is case-insensitive and the "
                f"content id is not, so a consumer on a case-sensitive one "
                f"gets a package with no such file. Make the two agree.")
        if not path.exists():
            return "absent", (
                "There is no such file in the package directory. Add it, or "
                "point parts at the file you meant.")
        return "not_inventoried", (
            "It is on disk but the inventory did not record it. Re-run the "
            "gate; if it persists, the file is unreadable to this process.")

    # ------------------------------------------------------ stage: contract

    def _stage_contract(self, started: float) -> dict:
        """One `inspect` per part, and the package standard on top of it.

        `inspect` answers PARAMS-and-`build` in **one** call and cannot report
        the second when the first is missing, so a part contributes one
        contract row carrying the kernel's own message rather than two rows
        this module would have to invent.
        """
        parts = self._declared_parts()
        if not parts:
            return make_stage("contract", reason="no_parts_declared",
                              duration_s=_elapsed(started))
        items: list[dict] = []
        for part_id, entry in parts.items():
            if self._out_of_budget():
                items.append(self._budget_item("contract", "part", part_id))
                continue
            if self._is_reference(entry):
                items.append(self._reference_item(
                    "contract", part_id,
                    "there is no PARAMS dict and no build(p) to inspect: the "
                    "build stage builds it once, from the imported file",
                    "a vendor solid has no parameters — to publish a "
                    "parametric part, author a script instead of importing "
                    "one"))
                continue
            items += self._contract_part(part_id, entry)
        return make_stage("contract", items, duration_s=_elapsed(started))

    def _spec_for(self, part_id: str, entry: dict):
        """``(params_spec, error_payload)`` — one `inspect` per part per run.

        Cached both ways: four stages need the spec (`contract` reports on it,
        `presets` validates against it, `build` enumerates variants from it,
        `connectors` needs the part to load at all), and a package with a
        broken script must not be inspected once per stage.
        """
        if part_id in self.specs:
            return self.specs[part_id], None
        if part_id in self.spec_errors:
            return None, self.spec_errors[part_id]
        try:
            script = self._part_source(part_id, entry)
            result = self.service.kernel.request("inspect", {"script": script})
        except (ValidationError, KernelError) as exc:
            payload = _payload(exc)
            self.spec_errors[part_id] = payload
            return None, payload
        spec = result.get("params_spec") or {}
        self.specs[part_id] = spec
        return spec, None

    def _contract_part(self, part_id: str, entry: dict) -> list[dict]:
        spec, error = self._spec_for(part_id, entry)
        if error is not None:
            return [self._item(
                "contract", "part", part_id, "fail",
                f"{entry['file']} does not meet the part contract: "
                f"{error['message']}", error=error,
                details={"file": entry["file"]})]
        items = [self._item(
            "contract", "part", part_id, "pass",
            f"{entry['file']} declares PARAMS ({len(spec)}) and build(p)",
            details={"file": entry["file"], "params": sorted(spec)})]
        for name, declared in spec.items():
            items.append(self._contract_param(part_id, name, declared))
        return items

    def _contract_param(self, part_id: str, name: str, declared: dict) -> dict:
        subject = f"{part_id}.{name}"
        ptype = declared.get("type", "number")
        if ptype in _NUMERIC:
            missing = [key for key in NUMERIC_REQUIRED
                       if declared.get(key) in (None, "")]
            if missing:
                return self._item(
                    "contract", "check", subject, "fail",
                    f"the {ptype} parameter {name!r} does not declare "
                    f"{', '.join(missing)} — the gate's claim that a package "
                    f"builds at every parameter's min and max is vacuous "
                    f"without it. Add them to PARAMS[{name!r}]",
                    details={"type": ptype, "missing": missing})
            return self._item(
                "contract", "check", subject, "pass",
                f"{name}: {ptype} in [{declared['min']}, {declared['max']}] "
                f"{declared['unit']}",
                details={"type": ptype, "min": declared.get("min"),
                         "max": declared.get("max"),
                         "unit": declared.get("unit")})
        detail = {"type": ptype}
        if ptype == "enum":
            detail["choices"] = declared.get("choices")
        return self._item(
            "contract", "check", subject, "pass",
            f"{name}: {ptype}"
            + (f" of {declared.get('choices')}" if ptype == "enum" else ""),
            details=detail)

    # ------------------------------------------------------- stage: presets

    def _stage_presets(self, started: float) -> dict:
        """Every declared configuration, validated against the inspected spec
        **and applied** through `service.set_params` on a scratch part.

        Two checks and not one, because they catch different things: applying
        is the truth about what the service accepts (unknown names, wrong
        types, a script that does not load), while `validate_configuration`
        catches the range and enum violations `set_params` deliberately does
        not — it stores a numeric value raw and the *worker clamps it at
        build*, so an out-of-range preset would otherwise apply quietly and
        publish a configuration nobody can reach.
        """
        path = self.source / "presets.json"
        if not path.is_file():
            return make_stage("presets", reason="no_presets_declared",
                              duration_s=_elapsed(started))
        try:
            doc = _json.read(path, "presets.json")
        except ValidationError as exc:
            item = self._item("presets", "check", "presets.json", "fail",
                              exc.message)
            return make_stage("presets", [item], duration_s=_elapsed(started))

        declared = self._declared_parts()
        items = [self._item("presets", "check",
                            problem.get("field") or "presets.json", "fail",
                            problem["message"],
                            details={"code": problem.get("code"),
                                     "field": problem.get("field")})
                 for problem in pkgformat.validate_presets(doc, declared)]
        presets = doc.get("presets") if isinstance(doc, dict) else None
        for part_id, configs in (presets or {}).items():
            if part_id not in declared or not isinstance(configs, dict):
                continue        # already a row from `validate_presets`
            for name, entry in configs.items():
                if self._out_of_budget():
                    items.append(self._budget_item("presets", "check",
                                                   f"{part_id}:{name}"))
                    continue
                if self._is_reference(declared[part_id]):
                    # A FAIL, not a skip: the package declares a configuration
                    # that can never be applied, which is a mistake in the
                    # package and not a fact about the world.
                    items.append(self._item(
                        "presets", "check", f"{part_id}:{name}", "fail",
                        f"configuration {name!r} names {part_id!r}, which is a "
                        f"reference part: imported geometry has no PARAMS, so "
                        f"there is nothing for a configuration to set",
                        details={"kind": "reference"}))
                    continue
                items.append(self._preset_item(part_id, name, entry))
        if not items:
            # A `presets.json` that declares no configuration measures exactly
            # as much as no `presets.json` at all, and it must SAY so: a stage
            # block with zero rows and no reason is invisible to the verdict,
            # to `exempt_skips` and to the summary, which is how
            # `{"format": 1, "presets": {}}` used to publish green through a
            # stage that had looked at nothing.
            return make_stage("presets", reason="no_presets_declared",
                              duration_s=_elapsed(started))
        return make_stage("presets", items, duration_s=_elapsed(started))

    def _preset_item(self, part_id: str, name: str, entry) -> dict:
        subject = f"{part_id}:{name}"
        # The **inspected** spec, not whatever the `contract` stage happened to
        # leave behind: `_spec_for` is cached, so a full run pays nothing, and
        # `validate --stages presets` still checks ranges instead of quietly
        # degrading to a shape check.
        spec, _error = self._spec_for(part_id, self._declared_parts()[part_id])
        problems = pkgformat.validate_configuration(entry, spec)
        params = entry.get("params") if isinstance(entry, dict) else None
        applied, built, error = False, None, None
        if isinstance(params, dict):
            try:
                scratch = self._scratch_part(part_id)
            except (AppError, KernelError) as exc:
                error = _payload(exc)
            else:
                try:
                    result = self.service.set_params(
                        self.service_project, scratch, params)
                except (AppError, KernelError) as exc:
                    error = _payload(exc)
                else:
                    applied, built = True, bool(result.get("ok"))
                finally:
                    self._clear_params(scratch, params, applied)
        if problems or error is not None:
            message = "; ".join(
                [problem["message"] for problem in problems]
                + ([error["message"]] if error else []))
            return self._item(
                "presets", "check", subject, "fail",
                f"configuration {name!r} of {part_id!r} is not usable: "
                f"{message}", error=error,
                details={"params": params, "built": built,
                         "problems": [p.get("code") for p in problems]})
        if applied and built is False:
            # `set_params` accepted the values and the rebuild it triggered did
            # not produce geometry. The row used to read "applied and built"
            # with `built: false` sitting in its own details — a `pass` whose
            # message contradicted its data, and the reason `validate --stages
            # presets` could answer green about a package that never builds.
            return self._item(
                "presets", "check", subject, "fail",
                f"configuration {name!r} of {part_id!r} applies but does not "
                f"build: {params} was accepted by set_params and the rebuild "
                f"produced no geometry. A configuration nobody can build is "
                f"not a configuration this package ships — run the build stage "
                f"for the kernel's own message",
                details={"params": params, "built": False,
                         "label": entry.get("label") if isinstance(entry, dict)
                         else None})
        return self._item(
            "presets", "check", subject, "pass",
            f"{name}: {params} applied and built"
            if applied else f"{name}: {params} validated against the spec",
            details={"params": params, "built": built,
                     "label": entry.get("label") if isinstance(entry, dict)
                     else None})

    @property
    def service_project(self) -> str:
        return GATE_PROJECT

    def _scratch_part(self, part_id: str) -> str:
        """The scratch part carrying this package part's script at its
        **declared defaults**, created once.

        Written through `store.add_part` rather than `service.create_part`,
        because `create_part` returns `get_part`, which builds: creating a
        dozen variant parts through it would serialise a dozen builds before
        the fan-out could see them, and the whole point of Task 2 is that the
        build phase makes no manifest writes and every build goes through
        `_rebuild`.

        The gate never opens a user project: this is a part of the gate's own
        project, inside the cell.
        """
        existing = self.scratch.get(part_id)
        if existing is not None:
            return existing
        entry = self._declared_parts()[part_id]
        if self._is_reference(entry):
            scratch = self._add_reference_scratch(part_id, entry)
        else:
            script = self._part_source(part_id, entry)
            scratch = self._add_scratch(part_id, "src", f"{part_id}@default",
                                        script)
        self.scratch[part_id] = scratch
        return scratch

    def _add_reference_scratch(self, part_id: str, entry: dict) -> str:
        """The scratch part for a REFERENCE part: the declared file copied into
        the cell's own `imports/` directory, then an ordinary reference part
        over it.

        The copy is what makes the build real. `_rebuild` hands the worker
        `store.imports_dir(proj) / source`, so the imported file has to be
        inside the gate's own project — which is also the containment rule
        doing its job: the gate reads the package directory and writes only
        inside the cell.
        """
        source = content.resolve_within(self.source, entry["source"],
                                        what=f"parts.{part_id}.source")
        name = Path(entry["source"]).name
        dest = self.service.store.imports_dir(self.service_project,
                                              write=True) / name
        shutil.copyfile(source, dest)
        scratch = _scratch_id(part_id, "ref", self.variant_of)
        self.service.store.add_part(self.service_project, scratch,
                                    f"{part_id}@default", "al6061", "",
                                    kind="reference", source=name)
        self.variant_of[scratch] = f"{part_id}@default"
        self.reference_scratch.add(scratch)
        return scratch

    def _add_scratch(self, part_id: str, suffix: str, subject: str,
                     script: str, params: dict | None = None) -> str:
        scratch = _scratch_id(part_id, suffix, self.variant_of)
        self.service.store.add_part(self.service_project, scratch, subject,
                                    "al6061", script)
        if params:
            # Raw, by design: the worker's `_resolve_params` coerces and
            # clamps every override at build time, and these values come
            # either from the spec's own declaration or from a configuration
            # the `presets` stage has already validated against it.
            self.service.store.update_part_entry(self.service_project, scratch,
                                                 params=dict(params))
        self.variant_of[scratch] = subject
        return scratch

    def _probe_part(self) -> str:
        """The bundled probe, created once per run and only if some part needs
        it (design Decision 9b)."""
        if self.probe is None:
            self.probe = self._add_scratch("probe", "mate", "probe",
                                           PROBE_SCRIPT)
        return self.probe

    def _clear_params(self, scratch: str, params: dict, applied: bool) -> None:
        """Drop the overrides a configuration set, so the next one is
        validated from the part's **declared defaults**.

        `set_params` merges, so without this the second configuration would be
        applied on top of the first and a preset that only sets `grade` would
        silently inherit the previous preset's `size`. Nothing is written when
        the application was refused: `set_params` validates before it writes.
        """
        if not applied or not params:
            return
        try:
            self.service.set_params(self.service_project, scratch,
                                    {name: None for name in params})
        except (AppError, KernelError) as exc:     # pragma: no cover — defensive
            self.warnings.append(
                f"{scratch}: could not clear a preset's overrides: {exc}")


    # --------------------------------------------------------- stage: build

    def _stage_build(self, started: float) -> dict:
        """Every variant of every part, built through `service._rebuild`, in
        `plan` order, one at a time.

        **Serial, and the parallel path was deleted rather than kept behind a
        flag** (changelog 0181). The plan pre-registered "under 1.5x on a
        3-worker pool, delete it" and three independent measurements came in at
        1.08x, 1.40x and 1.17x, against an Amdahl ceiling of 1.42x. Worse, the
        distribution was never reproducible — `KernelPool._pick` routes on
        `hash(affinity) % size` and `hash(str)` is `PYTHONHASHSEED`-randomised,
        so each process drew a different assignment and any measured speedup
        was a sample rather than a property. And it cost determinism where it
        mattered most: under `--budget`, `jobs=1` and `jobs=4` disagreed on
        `complete` and therefore on `publishable` (reproduced 3x). The safety
        premise in the deleted docstring was false too — a preset whose
        parameters equal a swept variant's produces the same cache key, so two
        threads could collide on one key.

        The parts are still created **first** (store writes, no kernel), which
        keeps the build phase free of manifest writes.
        """
        parts = self._declared_parts()
        if not parts:
            return make_stage("build", reason="no_parts_declared",
                              duration_s=_elapsed(started))
        items: list[dict] = []
        plan: list[tuple[Variant, str]] = []
        for part_id, entry in parts.items():
            if self._is_reference(entry):
                # A vendor solid has exactly one variant: itself. There is no
                # sweep because there are no parameters, and the row still
                # proves the thing that matters — that the shipped file loads
                # in this kernel and measures.
                try:
                    scratch = self._scratch_part(part_id)
                except (AppError, OSError) as exc:
                    items.append(self._item(
                        "build", "part", part_id, "fail",
                        f"the imported file {entry['source']!r} could not be "
                        f"staged for a build: {exc}", error=_payload(exc)
                        if isinstance(exc, AppError) else None))
                    continue
                plan.append((Variant(f"{part_id}@default", {}, "the imported "
                                     "solid"), scratch))
                continue
            spec, error = self._spec_for(part_id, entry)
            if error is not None:
                items.append(self._item(
                    "build", "part", part_id, "fail",
                    f"cannot enumerate the variants of {part_id!r}: the part "
                    f"does not load ({error['message']})", error=error))
                continue
            for name in unswept(spec):
                items.append(self._item(
                    "build", "check", f"{part_id}.{name}", "skip",
                    f"the string parameter {name!r} is built at its default "
                    f"only: a string's domain is unbounded, so the gate's "
                    f"range claim does not cover it",
                    reason="string_param_unbounded",
                    hint="declare the values that matter as presets — the "
                         "gate builds every one",
                    strict_exempt=True))
            for variant in variants(part_id, spec, self._presets_for(part_id)):
                plan.append((variant, self._variant_part(part_id, variant)))
        results = {scratch: self._build_one(scratch) for _v, scratch in plan}
        items += [self._build_item(variant, scratch, results.get(scratch))
                  for variant, scratch in plan]
        self._report_indifferent_parameters(plan, results)
        return make_stage("build", items, duration_s=_elapsed(started))

    def _report_indifferent_parameters(self, plan, results) -> None:
        """Warn when a swept parameter moves **no** measurable geometry.

        The specs ceiling, generalised (Codex #9): a spec reads the *built
        object*, so a `build(p)` that ignores its parameters entirely — always
        returning the M5x16 — passes every spec at every variant, because every
        variant is the same solid. Specs cannot see parameters. **The gate
        can**: it chose the parameter values, so it knows two variants that
        should differ, and it has both measurements.

        **Reported, never enforced**, and that is a deliberate limit rather
        than timidity. A hard `fail` needs an escape hatch for a legitimately
        cosmetic parameter (an enum that only changes a thread's appearance),
        and there is nowhere to declare one: `handle_inspect` normalises the
        PARAMS spec and drops unknown keys, so a `"geometric": false` marker
        would be a kernel change. Reddening correct third-party content with no
        way to say "this is intended" is the worse failure — the same reasoning
        that keeps `is_valid=false` reported-not-enforced for imported geometry
        a few lines below.

        Measured before shipping: across all nine catalog packages and their
        **16 swept parameters, zero** produced identical geometry at both
        extremes, so this warns on nothing that is currently correct.
        """
        volumes: dict[tuple, set] = {}
        for variant, scratch in plan:
            part, _, rest = variant.id.partition("@")
            name, sep, _tag = rest.partition("=")
            if not sep or not name:
                continue          # the default and the presets are not a sweep
            metrics = (results.get(scratch) or {}).get("metrics") or {}
            volume = metrics.get("volume_mm3")
            if isinstance(volume, (int, float)) and not isinstance(volume, bool):
                volumes.setdefault((part, name), set()).add(round(volume, 6))
        for (part, name), measured in sorted(volumes.items()):
            if len(measured) == 1:
                self.warnings.append(
                    f"{part}.{name}: every variant of this parameter built to "
                    f"the same volume ({measured.pop():,.3f} mm³), so the gate "
                    f"could not observe it doing anything. A spec cannot catch "
                    f"this — specs read the built object, and every variant is "
                    f"the same object. Check that build(p) actually reads "
                    f"p.{name}; if the parameter is deliberately cosmetic, this "
                    f"warning is expected.")

    def _variant_part(self, part_id: str, variant: Variant) -> str:
        """The scratch part for one variant.

        The **default** variant is the part `_scratch_part` already made: two
        parts with the same script and the same (empty) overrides are one
        part, and creating a second would report every spec twice and build
        the same cache key twice.
        """
        if not variant.params:
            return self._scratch_part(part_id)
        entry = self._declared_parts()[part_id]
        script = self._part_source(part_id, entry)
        suffix = variant.id.split("@", 1)[1]
        return self._add_scratch(part_id, suffix, variant.id, script,
                                 variant.params)

    def _build_one(self, scratch: str) -> dict:
        """One variant. The budget is read immediately before the kernel call,
        because that is where the seconds are."""
        if self._out_of_budget():
            return {"ok": None, "budget": True}
        try:
            return self.service._rebuild(self.service_project, scratch)
        except Exception as exc:  # noqa: BLE001 — one row, never the run
            return {"ok": False, "error": _payload(exc)}

    def _build_item(self, variant: Variant, scratch: str, result) -> dict:
        if result is None or result.get("budget"):
            return self._budget_item("build", "part", variant.id)
        if not result.get("ok"):
            payload = result.get("error") or {
                "type": "kernel_error", "message": "the build failed",
                "details": {}}
            # Verbatim: the same traceback, line and Error-Doctor hint an
            # agent already knows how to read.
            return self._item(
                "build", "part", variant.id, "fail",
                f"build failed at {variant.label}: {payload.get('message')}",
                error=payload, details={"params": variant.params})
        metrics = result.get("metrics") or {}
        details = {"params": variant.params,
                   "volume_mm3": metrics.get("volume_mm3"),
                   "mass_g": metrics.get("mass_g"),
                   "n_solids": metrics.get("n_solids"),
                   "is_valid": metrics.get("is_valid"),
                   "cache_key": result.get("cache_key")}
        volume = metrics.get("volume_mm3")
        if not isinstance(volume, (int, float)) or volume <= 0:
            # A `pass` with a note would be a package that publishes nothing.
            return self._item(
                "build", "part", variant.id, "fail",
                f"built to {volume!r} mm³ at {variant.label}: an empty solid "
                f"is not a part", details=details)
        if metrics.get("is_valid") is False:
            if scratch in self.reference_scratch:
                # **Reported, never enforced** for imported geometry, exactly as
                # PRD-004's build stage does it: OCCT calls the shipped
                # `examples/rocketry` STEP invalid over its 180 solids, which is
                # also why `tests/test_examples.py` exempts reference parts. A
                # red here would redden correct vendor content and there is
                # nothing the packager could do about it.
                self.warnings.append(
                    f"{variant.id}: the imported geometry reports "
                    f"is_valid=false on the whole shape "
                    f"({metrics.get('n_solids')} solids); validity is reported "
                    f"for imported parts, never enforced")
            else:
                return self._item(
                    "build", "part", variant.id, "fail",
                    f"built at {variant.label}, but the kernel reports the "
                    f"shape is not valid B-rep geometry", details=details)
        self.warnings.extend(f"{variant.id}: {warning}"
                             for warning in result.get("warnings") or [])
        valid = "valid" if metrics.get("is_valid") is not False \
            else "is_valid=false (reported, not enforced: imported geometry)"
        return self._item(
            "build", "part", variant.id, "pass",
            f"{variant.label}: {_number(metrics.get('volume_mm3'))} mm³, "
            f"{_number(metrics.get('mass_g'))} g, "
            f"{metrics.get('n_solids', 0)} solid(s), {valid}",
            details=details)

    def _presets_for(self, part_id: str) -> dict:
        """This part's declared configurations, or `{}`.

        Read straight from `presets.json` — a document the `presets` stage
        reports on but does not own, so `--stages build` still builds every
        configuration.
        """
        path = self.source / "presets.json"
        if not path.is_file():
            return {}
        # `read_optional`: the `presets` stage is where an unreadable document
        # becomes a row, and this reader must not raise on the way there.
        doc = _json.read_optional(path, "presets.json")
        presets = doc.get("presets") if isinstance(doc, dict) else None
        configs = (presets or {}).get(part_id) if isinstance(presets, dict) \
            else None
        return configs if isinstance(configs, dict) else {}

    # --------------------------------------------------------- stage: specs

    def _stage_specs(self, started: float) -> dict:
        """`SpecRunner.run` over the scratch project — which by now holds one
        part per **variant**, so a package's specs are evaluated at every
        extreme it declares and not only at its default.

        PRD-003's rows are folded in with their statuses intact; the only
        thing this adds is the subject, which is renamed from the scratch part
        id to the variant a reader recognises.
        """
        runner = getattr(self.service, "specs", None)   # read HERE, not in init
        if runner is None:
            return make_stage("specs", reason="specs_unavailable",
                              duration_s=_elapsed(started))
        report = runner.run(self.service_project)
        self.warnings.extend(report.get("warnings") or [])
        if not report.get("declared"):
            # "A part that declares nothing is absent, not green" travels up
            # one level intact.
            return make_stage("specs", [], reason="not_declared",
                              duration_s=_elapsed(started), report=report)
        items = [self._spec_item(row) for row in report.get("checks") or []]
        return make_stage("specs", items, duration_s=_elapsed(started),
                          report=report)

    def _spec_item(self, row: dict) -> dict:
        status = row.get("status")
        reason, hint = row.get("reason"), row.get("hint")
        if status == "skip" and not (reason and hint):
            reason = reason or "unspecified"
            hint = hint or ("the spec runner reported a skip with no hint; "
                            "re-run run_specs to re-measure it")
        subject = self.variant_of.get(row.get("part"), row.get("part") or "?")
        details = {**(row.get("details") or {}),
                   "measured": row.get("measured"), "limit": row.get("limit"),
                   "unit": row.get("unit"), "scope": row.get("scope"),
                   "part": subject}
        return self._item(
            "specs", "check", f"{subject}:{row.get('name') or row.get('id')}",
            status, row.get("message") or "", reason=reason, hint=hint,
            error=row.get("error"), details=details,
            requirement=row.get("requirement"),
            # A world-fact skip (today: no `[fem]` extra on this machine) is
            # publish-exempt, so it is strict-exempt too — the same set, by
            # design (design Decision 10).
            strict_exempt=status == "skip" and reason in PUBLISH_SKIP_EXEMPT)

    # ---------------------------------------------------- stage: connectors

    def _stage_connectors(self, started: float) -> dict:
        """One `connectors` call per part, then **one** scratch assembly.

        This feature is the kernel `connectors` handler's first server-side
        consumer: nothing in `core/` or the frontend has ever called it.

        The assembly is the smoke test (design Decision 9b): an anchor
        instance of the part plus one instance mated onto each declared
        connector — the part itself when it declares a rigid connector (the
        moving side must be rigid, because the anchor carries the DOF),
        otherwise the bundled probe. One `get_assembly` resolves the whole set
        in a single `resolve_mates` round trip.
        """
        parts = self._declared_parts()
        if not parts:
            return make_stage("connectors", reason="no_parts_declared",
                              duration_s=_elapsed(started))
        items: list[dict] = []
        declared: dict[str, dict] = {}
        for part_id, entry in parts.items():
            if self._out_of_budget():
                items.append(self._budget_item("connectors", "part", part_id))
                continue
            if self._is_reference(entry):
                items.append(self._reference_item(
                    "connectors", part_id,
                    "it declares no connectors: a connectors(p, part) function "
                    "lives in a script, and imported geometry has none",
                    "package_from_step reports the imported solid's planar and "
                    "cylindrical faces as candidates; an author turns them "
                    "into a connectors() function in a script part"))
                continue
            _spec, error = self._spec_for(part_id, entry)
            if error is not None:
                items.append(self._item(
                    "connectors", "part", part_id, "fail",
                    f"{part_id} does not load, so its connectors cannot be "
                    f"read: {error['message']}", error=error))
                continue
            try:
                result = self.service.kernel.request(
                    "connectors", {"script": self._part_source(part_id, entry),
                                   "params": {}})
            except (KernelError, ValidationError) as exc:
                payload = _payload(exc)
                # The kernel's message names the offending connector, which is
                # what makes this row actionable.
                items.append(self._item(
                    "connectors", "part", part_id, "fail",
                    f"{part_id}: {payload['message']}", error=payload))
                continue
            connectors = result.get("connectors") or {}
            if not connectors:
                items.append(self._item(
                    "connectors", "part", part_id, "skip",
                    f"{part_id} declares no connectors",
                    reason="no_connectors_declared",
                    hint="declare connectors(p, part) if this part is meant "
                         "to mate; a plain solid legitimately declares none",
                    strict_exempt=True))
                continue
            items.append(self._item(
                "connectors", "part", part_id, "pass",
                f"{part_id} declares " + ", ".join(
                    f"{name} ({spec.get('type')})"
                    for name, spec in connectors.items()),
                details={"connectors": {name: spec.get("type")
                                        for name, spec in connectors.items()}}))
            declared[part_id] = connectors
        if declared:
            items += self._mate_smoke(declared)
        return make_stage("connectors", items, duration_s=_elapsed(started))

    def _mate_smoke(self, declared: dict) -> list[dict]:
        instances: list[dict] = []
        anchors: dict[str, dict] = {}
        targets: list[tuple] = []
        for part_id, connectors in declared.items():
            anchor_part = self._scratch_part(part_id)
            anchor_id = f"anchor_{len(anchors) + 1}"
            anchors[anchor_id] = {"id": anchor_id, "part": anchor_part,
                                  "position": [0, 0, 0],
                                  "rotation_deg": [0, 0, 0]}
            instances.append(anchors[anchor_id])
            # The moving side must be rigid, so the mover is the part itself
            # when it declares a rigid connector and the bundled probe when it
            # does not.
            rigid = next((name for name, spec in connectors.items()
                          if spec.get("type") == "rigid"), None)
            for name in connectors:
                mover = {"id": f"mate_{len(targets) + 1}",
                         "part": anchor_part if rigid else self._probe_part(),
                         "position": [0, 0, 0], "rotation_deg": [0, 0, 0],
                         "mate": {"connector": rigid or "tip",
                                  "to_instance": anchor_id,
                                  "to_connector": name}}
                instances.append(mover)
                targets.append((part_id, name, mover, anchor_id))
        resolved, _error = self._resolve(instances)
        if resolved is not None:
            return [self._connector_item(part_id, name,
                                         resolved.get(mover["id"]))
                    for part_id, name, mover, _anchor in targets]
        # The batch failed, so attribution is now worth N round trips: the
        # green path stays **one** `resolve_mates` call, and the cost of
        # naming the culprit is only ever paid by a package already wrong.
        items = []
        for part_id, name, mover, anchor_id in targets:
            one, error = self._resolve([anchors[anchor_id], mover])
            if one is not None:
                items.append(self._connector_item(part_id, name,
                                                  one.get(mover["id"])))
            else:
                items.append(self._item(
                    "connectors", "mate", f"{part_id}.{name}", "fail",
                    f"connector {name!r} of {part_id} does not resolve: "
                    f"{error['message']}", error=error))
        return items

    def _resolve(self, instances: list[dict]):
        """One `set_assembly` — which **returns** `get_assembly`, so this is a
        single `resolve_mates` round trip and not two.

        ``({instance id: entry}, None)`` on success, ``(None, payload)`` when
        the resolver refused.
        """
        try:
            assembly = self.service.set_assembly(self.service_project,
                                                 instances)
        except (AppError, KernelError) as exc:
            return None, _payload(exc)
        return ({entry.get("id"): entry
                 for entry in assembly.get("instances") or []}, None)

    def _connector_item(self, part_id: str, name: str, entry) -> dict:
        if not isinstance(entry, dict):     # pragma: no cover — defensive
            return self._item("connectors", "mate", f"{part_id}.{name}", "fail",
                              f"connector {name!r} of {part_id} resolved to "
                              f"nothing")
        return self._item(
            "connectors", "mate", f"{part_id}.{name}", "pass",
            f"{name} mates: the probe lands at "
            f"{_point(entry.get('position'))} rot "
            f"{_point(entry.get('rotation_deg'))}",
            details={"position": entry.get("position"),
                     "rotation_deg": entry.get("rotation_deg")})

    # ------------------------------------------------------ stage: previews

    def _stage_previews(self, started: float) -> dict:
        """Render each part server-side, and check the **shipped** PNG exists
        and parses.

        There is deliberately **no pixel comparison**: the renderer is allowed
        to improve, and a gate that diffed images would redden correct content
        the day a shading constant changed. What is asserted is that the
        package ships a preview per part and that it is a real PNG.
        """
        parts = self._declared_parts()
        if not parts:
            return make_stage("previews", reason="no_parts_declared",
                              duration_s=_elapsed(started))
        items: list[dict] = []
        for part_id, entry in parts.items():
            items.append(self._shipped_preview(part_id))
            if self._out_of_budget():
                items.append(self._budget_item("previews", "part", part_id))
                continue
            items.append(self._render(part_id, entry))
        return make_stage("previews", items, duration_s=_elapsed(started))

    def _shipped_preview(self, part_id: str) -> dict:
        subject = f"previews/{part_id}"
        candidates = sorted((self.source / "previews").glob(f"{part_id}*.png")) \
            if (self.source / "previews").is_dir() else []
        if not candidates:
            return self._item(
                "previews", "check", subject, "fail",
                f"no previews/{part_id}*.png: every part ships a rendered "
                f"preview, which is what a search hit and the Library dialog "
                f"show before anything is installed")
        problems = [f"{path.name}: {_png_problem(path)}" for path in candidates
                    if _png_problem(path)]
        if problems:
            return self._item("previews", "check", subject, "fail",
                              "; ".join(problems))
        return self._item(
            "previews", "check", subject, "pass",
            f"{len(candidates)} shipped preview(s) parse: "
            f"{', '.join(p.name for p in candidates)}")

    def _render(self, part_id: str, entry: dict) -> dict:
        subject = f"render:{part_id}"
        if self.registry is None:       # pragma: no cover — defensive
            return self._item("previews", "check", subject, "skip",
                              "no tool registry", reason="renderer_unavailable",
                              hint="build the registry before running the gate")
        try:
            scratch = self._scratch_part(part_id)
            result = self.registry.call("render_view", {
                "project": self.service_project, "part_id": scratch,
                "view": "iso", "width": PREVIEW_SIZE[0],
                "height": PREVIEW_SIZE[1]})
        except (AppError, KernelError) as exc:
            payload = _payload(exc)
            return self._item(
                "previews", "check", subject, "fail",
                f"{part_id} cannot be rendered: {payload['message']}",
                error=payload)
        return self._item(
            "previews", "check", subject, "pass",
            f"{part_id} renders at {PREVIEW_SIZE[0]}x{PREVIEW_SIZE[1]} "
            f"(the pixels are not compared with the shipped preview: renderer "
            f"drift would redden correct content)",
            details={"view": "iso", "width": result.get("width"),
                     "height": result.get("height")})

    # ---------------------------------------------------------- stage: docs

    def _stage_docs(self, started: float) -> dict:
        """The README names the parts, and every part documents itself."""
        parts = self._declared_parts()
        items = [self._docs_readme(parts)]
        for part_id, entry in parts.items():
            items.append(self._docs_part(part_id, entry))
        return make_stage("docs", items, duration_s=_elapsed(started))

    def _docs_readme(self, parts: dict) -> dict:
        try:
            text = (self.source / README_PATH).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return self._item("docs", "check", README_PATH, "fail",
                              f"{README_PATH} is missing")
        missing = [part_id for part_id in parts if part_id not in text]
        if missing:
            return self._item(
                "docs", "check", README_PATH, "fail",
                f"{README_PATH} never mentions {', '.join(sorted(missing))}: a "
                f"package documents the parts it ships",
                # The ids travel as DATA as well as in the sentence: a row a
                # reader can only act on by parsing English is a row an agent
                # cannot act on at all, and this loop is the one PRD-011 exists
                # to shorten.
                details={"missing": sorted(missing), "path": README_PATH})
        return self._item("docs", "check", README_PATH, "pass",
                          f"{README_PATH} documents {len(parts)} part(s)")

    def _docs_part(self, part_id: str, entry: dict) -> dict:
        missing = []
        if not str(entry.get("summary") or "").strip():
            missing.append("a summary in package.json")
        if self._is_reference(entry):
            # There is no module, so there can be no module docstring. The
            # summary and the README are what a reader of a vendor solid gets,
            # and both are still required.
            if missing:
                return self._item(
                    "docs", "part", part_id, "fail",
                    f"{part_id} has no summary in package.json — a reference "
                    f"part ships no script, so the summary and the README are "
                    f"the only documentation it can have")
            return self._item(
                "docs", "part", part_id, "pass",
                f"{part_id}: summary present (a reference part has no module "
                f"to carry a docstring)")
        try:
            source = self._part_source(part_id, entry)
            docstring = ast.get_docstring(ast.parse(source))
        except (ValidationError, SyntaxError, ValueError):
            docstring = None
        if not (docstring or "").strip():
            missing.append("a module docstring")
        if missing:
            return self._item(
                "docs", "part", part_id, "fail",
                f"{part_id} has no {' and no '.join(missing)} — a package part "
                f"is read before it is run, and this is what a reader sees "
                f"first")
        return self._item("docs", "part", part_id, "pass",
                          f"{part_id}: summary and module docstring present")

    # -------------------------------------------------------- stage: policy

    def _stage_policy(self, started: float) -> dict:
        """`service.package_policy` if one is installed, else one honest skip.

        This is the seam PRD-031 FR2(b) plugs its static AST gate into
        (import allowlist, no `exec`/`eval`/dynamic import). **This feature
        ships the seam and no policy**: an allowlist that is wrong is worse
        than one that is absent and labelled, and freezing which toolkit
        imports a package may use before a single third-party package exists
        would be exactly that.
        """
        policy = getattr(self.service, "package_policy", None) or self.policy
        if policy is None:
            item = self._item(
                "policy", "check", "policy", "skip",
                "no package policy is configured, so no source policy was "
                "applied — the gate proves that geometry builds, never that "
                "a script is safe to run",
                reason="no_policy_configured",
                hint="install service.package_policy (PRD-031 FR2(b)) to have "
                     "package sources statically checked here",
                strict_exempt=True)
            return make_stage("policy", [item], duration_s=_elapsed(started))
        items: list[dict] = []
        for part_id, entry in self._declared_parts().items():
            if self._is_reference(entry):
                items.append(self._reference_item(
                    "policy", part_id,
                    "there is no source for a source policy to read — an "
                    "imported solid is data, not code",
                    "a policy for imported geometry (size, solid count, "
                    "vendor terms) would be a different check; this seam "
                    "reads scripts"))
                continue
            try:
                source = self._part_source(part_id, entry)
                rows = policy.check(source, entry.get("file")) or []
            except Exception as exc:  # noqa: BLE001 — a policy is third-party
                payload = _payload(exc)
                self.errors.append({**payload, "stage": "policy"})
                items.append(self._item(
                    "policy", "check", part_id, "error",
                    f"the package policy raised on {part_id}: "
                    f"{payload['message']}", error=payload))
                continue
            if not rows:
                # A clean policy returns nothing, and "nothing" must still be a
                # row: a stage that emits no rows blocks the verdict (see
                # `verdict`), and it should say *the policy read this part and
                # had no finding* rather than leave a hole a reader has to
                # interpret.
                items.append(self._item(
                    "policy", "check", part_id, "pass",
                    f"{part_id}: the package policy read {entry.get('file')} "
                    f"and reported nothing"))
                continue
            items += [self._policy_item(part_id, row) for row in rows]
        if not items:
            return make_stage("policy", reason="no_parts_declared",
                              duration_s=_elapsed(started))
        return make_stage("policy", items, duration_s=_elapsed(started))

    def _policy_item(self, part_id: str, row) -> dict:
        """One policy row, re-shaped through `make_item` so a third-party
        policy cannot emit a row that breaks every consumer of the report."""
        row = row if isinstance(row, dict) else {}
        status = row.get("status") if row.get("status") in checks.ITEM_STATUSES \
            else "error"
        reason = row.get("reason") or ("unspecified" if status == "skip" else None)
        hint = row.get("hint") or ("the package policy did not say"
                                   if status == "skip" else None)
        return self._item(
            "policy", "check", f"{part_id}:{row.get('subject') or 'policy'}",
            status, str(row.get("message") or ""), reason=reason, hint=hint,
            details=row.get("details") if isinstance(row.get("details"), dict)
            else None)


def _copy_inventory(src: Path, dst: Path, entries) -> None:
    """Exactly the inventoried files — `cache._copy_inventory`'s rule, for the
    same reason: the snapshot must be the tree the content id describes and
    nothing else, so an ignored file never lands in it and a symlink
    structurally cannot (the inventory refused one before we got here)."""
    dst.mkdir(parents=True, exist_ok=True)
    for relpath, _size, _sha in entries:
        target = dst / relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src / relpath, target)


def _number(value) -> str:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return "?"
    return f"{value:,.2f}"


def _point(values) -> str:
    if not isinstance(values, (list, tuple)) or len(values) != 3:
        return "?"
    return "(" + ", ".join(f"{float(v):.2f}" for v in values) + ")"


def _png_problem(path: Path) -> str | None:
    """``None`` when *path* is a PNG this reader can parse, else why not.

    A real parse and not an extension check: the signature, an `IHDR` whose
    CRC matches and whose dimensions are positive, and an `IEND` at the end.
    No image library — Pillow is not a dependency of this project and one
    preview check is not a good enough reason to make it one.
    """
    try:
        data = path.read_bytes()
    except OSError as exc:
        return f"unreadable ({exc})"
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        return "not a PNG (bad signature)"
    if len(data) < 33:
        return "truncated before its header"
    length, kind = struct.unpack(">I4s", data[8:16])
    if kind != b"IHDR" or length != 13:
        return "no IHDR chunk"
    if zlib.crc32(data[12:29]) != struct.unpack(">I", data[29:33])[0]:
        return "the IHDR chunk is corrupt (CRC mismatch)"
    width, height = struct.unpack(">II", data[16:24])
    if not width or not height:
        return f"has no pixels ({width}x{height})"
    if not data.rstrip().endswith(b"IEND\xaeB`\x82"):
        return "truncated before IEND"
    return None


def _scratch_id(part_id: str, suffix: str, taken) -> str:
    """A project part id for a scratch variant: `ID_RE`, unique, and readable.

    Part ids are `^[a-z][a-z0-9_]{0,39}$`, while package part ids, parameter
    names and configuration names may carry characters (and lengths) that are
    not — so the readable id is the row's *subject*, and this is only the
    filename behind it.
    """
    raw = f"{part_id}_{suffix}".lower()
    cleaned = "".join(ch if ch.isalnum() else "_" for ch in raw).strip("_")
    if not cleaned or not cleaned[0].isalpha():
        cleaned = f"p_{cleaned}"
    candidate = cleaned[:40]
    taken = set(taken)
    index = 2
    while candidate in taken:
        tail = f"_{index}"
        candidate = cleaned[:40 - len(tail)] + tail
        index += 1
    return candidate
