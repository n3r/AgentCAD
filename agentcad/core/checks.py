"""Geometry CI: the report shape, its two renderings and its verdict.

This module is the *reporting* half of ``agentcad check`` (PRD-004). It is
pure — dicts in, dicts, strings and ints out — so the contract every later
slice is written against is provable without building a single solid, and so
the report can be validated by a consumer that has no kernel.

Three rules it is written around:

* **It composes; it never measures.** Every number in a report came from a
  surface that already exists and is reviewed (``service._ensure_built``,
  ``check_interference``, ``SpecRunner.run``, the drawing tools). Nothing here
  computes geometry, and an ``error`` field is the payload a tool returned,
  **verbatim** — the same ``details.traceback``, ``details.line`` and Error
  Doctor ``details.hint`` an agent already knows how to fix (FR7).
* **Rows are ``items``, never ``checks``.** ``checks`` already means three
  things in this codebase: the built-in gate name in
  ``ProposalManager.gates()``, ``report["checks"]`` in every ``SpecRunner``
  report, and the proposals UI's "Checks" tab. A fourth meaning would be a bug
  generator. Likewise ``status`` is the four-value row status and ``state`` is
  the gate's — they are not interchangeable.
* **No new status vocabulary.** :func:`summarize`, :func:`report_status`,
  :func:`group_requirements` and :func:`assign_ids` are imported from
  :mod:`agentcad.core.specs`, not restated. Row statuses are PRD-003's four
  (``pass``/``fail``/``skip``/``error``), summary counts its five, stage and
  report statuses its three (``green``/``red``/``skip``).

And one policy, which is the whole difference between this surface and the
proposal gate (design Decision 6): **a check report is honest.** A skip stays
a skip, with its reason and its hint; ``--strict`` changes only the *derived*
``status`` and ``exit_code`` and records which rows it counted, so a reader can
always tell what was measured from what was demanded. PRD-003's ``specs`` gate
is the fail-closed reading of the same measurements, and it stays that way.

And one containment rule, which is the whole of ``--ref`` (design Decision 5):
**a check never mutates the project it measures.** Checking a ref materializes
the resolved *commit* into a throwaway detached ``git worktree`` and drives a
second, ephemeral :class:`AgentCADService` rooted there — with its event bus
and its branch resolver muzzled, because either one left live would write into
the user's real repository through the link. The price is stated rather than
hidden: a ref check runs on a **cold cache**.

Nothing here imports ``OCP`` or build123d, directly or transitively — this is
server-process code and a test asserts it.
"""

from __future__ import annotations

import ast
import contextlib
import hashlib
import platform
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path

import agentcad

from ..kernel.client import KernelError
from . import specs as _specs
from .history import HistoryError, looks_like_commit
from .model import AppError, NotFoundError, ValidationError
from .specs import assign_ids, group_requirements, report_status, summarize

#: Report format version. A consumer reads this first; :func:`validate_report`
#: refuses anything else.
REPORT_SCHEMA = 1

#: The declared stages, in dependency- and cost-order: ``build`` first
#: (everything else needs shapes, and its cache warms them), ``assembly``
#: second (bounded, and the richest early signal), ``specs`` third (may include
#: FEM), ``drawings`` last (pure regeneration). A run always reports all four —
#: an unselected one is ``skip``/``not_selected``, so a consumer never has to
#: guess whether a stage was green or absent.
STAGES = ("build", "assembly", "specs", "drawings")

#: ``--verify-determinism`` adds a pseudo-stage. It is not in :data:`STAGES`
#: because it is not part of certifying a project — it certifies the *product
#: guarantee* (same script + params ⇒ identical bytes).
DERIVED_STAGES = ("determinism",)

ALL_STAGES = STAGES + DERIVED_STAGES

#: Row statuses (PRD-003's four). ``fail`` is measured and outside the limit;
#: ``error`` is "the check itself broke — we do not know"; ``skip`` is a named
#: structural inability, always with a reason *and* a hint.
ITEM_STATUSES = ("pass", "fail", "skip", "error")

#: Stage and report statuses (PRD-003's three).
STAGE_STATUSES = ("green", "red", "skip")

#: The proposal gate's vocabulary, which is *not* the row's. Kept here so the
#: validator can catch a ``state`` that wandered into a ``status`` slot.
GATE_STATES = ("pass", "fail", "pending", "skipped")

#: What a row is about. Closed on purpose: a typo'd kind is a silent hole in
#: every consumer that groups by it.
ITEM_KINDS = ("part", "instance", "pair", "check", "drawing", "flat_pattern",
              "mate")

#: How the measured tree was named. ``worktree`` is the ordinary path (no
#: ``--ref``); the other three are what ``--ref`` resolved to.
SOURCE_KINDS = ("worktree", "branch", "tag", "commit")

#: How many projects' last reports a :class:`CheckRunner` keeps in memory. The
#: cache exists so ``GET /api/projects/{p}/checks`` can answer without re-running
#: a check; it is per process and deliberately tiny, because a report is a large
#: document and a long-lived server may see many projects. The durable copy is
#: the proposal's ``checks.json`` (slice 6), not this.
LAST_REPORTS = 8

#: How many failure blocks (and skip rows) the markdown renders before it
#: summarizes the rest. ``$GITHUB_STEP_SUMMARY`` is capped at 1 MiB, and a
#: 33-part project with a broken shared import would blow past it.
MAX_RENDERED_FAILURES = 50

#: Keys every report carries. Absence is a validation problem, not a default:
#: a consumer must be able to read a report without guessing.
_REQUIRED_KEYS = ("schema", "agentcad", "project", "source", "started",
                  "finished", "duration_s", "status", "complete", "strict",
                  "strict_failures", "exit_code", "summary", "stages",
                  "requirements", "warnings", "errors", "host")

_SUMMARY_KEYS = ("passed", "failed", "skipped", "errors", "total")

# Presence-scan memo, keyed by sha256(script) — the answer is a property of the
# text alone, so two runners over the same tree agree (``specs._DECLARES_MEMO``
# verbatim, including the bound).
_FLAT_MEMO: dict[str, bool] = {}
_MEMO_LIMIT = 512


def _now() -> str:
    """UTC, ISO-8601, zone-aware, second resolution — ``specs._now``'s reasoning
    verbatim: a report is read by a human, and the trailing ``Z`` is what stops
    a reader from mistaking it for local time."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ------------------------------------------------------------------- rows


def make_item(stage: str, kind: str, subject: str, status: str, message: str,
              *, reason: str | None = None, hint: str | None = None,
              error: dict | None = None, details: dict | None = None,
              requirement: str | None = None, seen: set[str] | None = None,
              warnings: list[str] | None = None) -> dict:
    """One row: ``<stage>:<subject>``, de-duplicated exactly like a spec id.

    *seen* and *warnings* are the caller's accumulators; pass them and a
    repeated subject becomes ``…#2`` with a warning, rather than two rows
    silently merging into one (``assign_ids``' rule, reused). Omit them and the
    id is the plain join.

    A ``skip`` **must** carry both a *reason* and a *hint* — PRD-003's rule,
    enforced here so a malformed skip is a ``ValueError`` a test catches, never
    a row that says "not measured" without saying why or what to do about it.
    """
    if status not in ITEM_STATUSES:
        raise ValueError(f"unknown item status {status!r}; expected one of "
                         f"{', '.join(ITEM_STATUSES)}")
    if kind not in ITEM_KINDS:
        raise ValueError(f"unknown item kind {kind!r}; expected one of "
                         f"{', '.join(ITEM_KINDS)}")
    if status == "skip" and not (reason and hint):
        raise ValueError(f"a skip row ({stage}:{subject}) needs both a reason "
                         f"and a hint")
    if seen is None:
        ident = f"{stage}:{subject}"
    else:
        holder = {"name": subject}
        assign_ids([holder], stage, seen, warnings if warnings is not None
                   else [])
        ident = holder["id"]
    return {"id": ident, "kind": kind, "subject": subject, "status": status,
            "message": message, "reason": reason, "hint": hint,
            "requirement": requirement, "error": error,
            "details": dict(details or {})}


def make_stage(name: str, items: list[dict] | None = None, *,
               duration_s: float = 0.0, reason: str | None = None,
               report: dict | None = None) -> dict:
    """One stage block, summarizing and statusing itself.

    A *reason* means the stage was **explicitly skipped** (``not_selected``,
    ``no_instances``, ``not_declared``, ``budget_exceeded``,
    ``specs_unavailable``, …) and is reported as ``skip`` whatever it holds;
    otherwise the status is :func:`report_status` over its own rows, so a stage
    with nothing to measure is ``skip`` rather than a green nobody earned.

    *report* is the specs stage's ``SpecRunner.run`` document, embedded whole:
    requirement traceability is passed through, never re-derived.
    """
    rows = list(items or [])
    summary = summarize(rows)
    stage = {"name": name,
             "status": "skip" if reason else report_status(summary),
             "reason": reason, "duration_s": duration_s,
             "summary": summary, "items": rows}
    if report is not None:
        stage["report"] = report
    return stage


def finalize_report(project: str, stages: list[dict], *, source: dict,
                    host: dict, started: str, finished: str | None = None,
                    duration_s: float = 0.0, strict: bool = False,
                    complete: bool = True,
                    warnings: list[str] | None = None,
                    errors: list[dict] | None = None) -> dict:
    """Flatten the stages into one ``schema: 1`` document and rule on it.

    The top-level ``summary`` is every stage's rows together, ``status`` is
    :func:`report_status` over it, ``requirements`` is the specs stage's rows
    through :func:`group_requirements`, and ``exit_code`` is :func:`exit_code`.

    ``strict`` does not touch a single row: it records the ids it counted in
    ``strict_failures`` and lets the derived ``status``/``exit_code`` move.
    """
    items = [item for stage in stages for item in stage.get("items") or []]
    summary = summarize(items)
    strict_failures = [item["id"] for item in items
                       if item.get("status") == "skip"] if strict else []
    status = report_status(summary)
    if strict_failures:
        status = "red"
    spec_rows = [item for stage in stages if stage.get("name") == "specs"
                 for item in stage.get("items") or []]
    report = {
        "schema": REPORT_SCHEMA,
        "agentcad": agentcad.__version__,
        "project": project,
        "source": source,
        "started": started,
        "finished": finished if finished is not None else _now(),
        "duration_s": round(float(duration_s), 3),
        "status": status,
        "complete": bool(complete),
        "strict": bool(strict),
        "strict_failures": strict_failures,
        "exit_code": 0,
        "summary": summary,
        "stages": list(stages),
        "requirements": group_requirements(spec_rows),
        "warnings": list(warnings or []),
        "errors": list(errors or []),
        "host": host,
    }
    report["exit_code"] = exit_code(report)
    return report


# ------------------------------------------------------------- the verdict


def exit_code(report: dict) -> int:
    """``0`` green · ``1`` red — the model is wrong · ``2`` harness.

    The whole table (design Decision 6), as one pure function over
    ``(complete, summary, strict, strict_failures)``:

    * ``complete: false`` is **2 regardless of status** — a budget that ran out
      means we could not produce a verdict, so the partial one we did produce
      is evidence, not the answer.
    * any ``fail`` or ``error`` row is **1**. A spec ``error`` ("the check
      itself broke") is deliberately 1 and not 2: it is a fact about the model,
      and a caller must be able to tell "read the report and fix the design"
      from "fix the environment".
    * ``--strict`` meeting any ``skip`` is **1**, via ``strict_failures``.
    """
    if not report.get("complete", True):
        return 2
    summary = report.get("summary") or {}
    if summary.get("failed") or summary.get("errors"):
        return 1
    if report.get("strict") and report.get("strict_failures"):
        return 1
    return 0


# ----------------------------------------------------------- the validator


def _enum(value, allowed: tuple[str, ...], label: str,
          problems: list[str]) -> None:
    if value not in allowed:
        problems.append(f"{label} is {value!r}, expected one of "
                        f"{', '.join(allowed)}")


def _summary_problems(value, label: str) -> list[str]:
    if not isinstance(value, dict):
        return [f"{label} is not an object"]
    problems = []
    for key in _SUMMARY_KEYS:
        count = value.get(key)
        if not isinstance(count, int) or isinstance(count, bool):
            problems.append(f"{label}.{key} is {count!r}, expected an integer")
    return problems


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _item_problems(item, label: str, ids: set[str]) -> list[str]:
    problems: list[str] = []
    if not isinstance(item, dict):
        return [f"{label} is not an object"]
    ident = item.get("id")
    if not isinstance(ident, str) or not ident:
        problems.append(f"{label}.id is {ident!r}, expected a non-empty string")
    elif ident in ids:
        problems.append(f"{label}: duplicate item id {ident!r}")
    else:
        ids.add(ident)
    _enum(item.get("kind"), ITEM_KINDS, f"{label}.kind", problems)
    _enum(item.get("status"), ITEM_STATUSES, f"{label}.status", problems)
    if not isinstance(item.get("message"), str):
        problems.append(f"{label}.message is not a string")
    if item.get("status") == "skip":
        # The one invariant a reader depends on: a skip says why, and what to
        # do about it.
        for key in ("reason", "hint"):
            if not isinstance(item.get(key), str) or not item[key]:
                problems.append(f"{label}: a skip row needs a non-empty "
                                f"{key}")
    error = item.get("error")
    if error is not None:
        if not isinstance(error, dict):
            problems.append(f"{label}.error is not an object")
        else:
            for key in ("type", "message"):
                if not isinstance(error.get(key), str):
                    problems.append(f"{label}.error.{key} is not a string")
            if not isinstance(error.get("details", {}), dict):
                problems.append(f"{label}.error.details is not an object")
    if not isinstance(item.get("details", {}), dict):
        problems.append(f"{label}.details is not an object")
    if "state" in item:
        _enum(item["state"], GATE_STATES, f"{label}.state", problems)
    return problems


def validate_report(report) -> list[str]:
    """Every problem with *report*, in human-readable English (empty = valid).

    Hand-rolled on purpose (AC5): ``jsonschema`` is not a dependency of this
    project and one report shape is not a good enough reason to make it one.
    This function *is* the schema — the published document in ``docs/`` is
    generated against it, so the two cannot drift.
    """
    if not isinstance(report, dict):
        return ["report is not an object"]
    problems: list[str] = []
    for key in _REQUIRED_KEYS:
        if key not in report:
            problems.append(f"missing key {key!r}")

    if report.get("schema") != REPORT_SCHEMA:
        problems.append(f"schema is {report.get('schema')!r}, expected "
                        f"{REPORT_SCHEMA}")
    for key in ("agentcad", "project", "started", "finished"):
        if key in report and not isinstance(report[key], str):
            problems.append(f"{key} is not a string")
    if "duration_s" in report and not _is_number(report["duration_s"]):
        problems.append("duration_s is not a number")
    _enum(report.get("status"), STAGE_STATUSES, "status", problems)
    for key in ("complete", "strict"):
        if key in report and not isinstance(report[key], bool):
            problems.append(f"{key} is not a boolean")
    if report.get("exit_code") not in (0, 1, 2):
        problems.append(f"exit_code is {report.get('exit_code')!r}, expected "
                        f"0, 1 or 2")
    problems += _summary_problems(report.get("summary"), "summary")
    if "state" in report:
        _enum(report["state"], GATE_STATES, "state", problems)

    source = report.get("source")
    if not isinstance(source, dict):
        problems.append("source is not an object")
    else:
        _enum(source.get("kind"), SOURCE_KINDS, "source.kind", problems)
        if not isinstance(source.get("dirty", False), bool):
            problems.append("source.dirty is not a boolean")
    if not isinstance(report.get("host", {}), dict):
        problems.append("host is not an object")

    ids: set[str] = set()
    stages = report.get("stages")
    if not isinstance(stages, list):
        problems.append("stages is not a list")
    else:
        named: set[str] = set()
        for index, stage in enumerate(stages):
            label = f"stages[{index}]"
            if not isinstance(stage, dict):
                problems.append(f"{label} is not an object")
                continue
            name = stage.get("name")
            if name not in ALL_STAGES:
                problems.append(f"{label}: unknown stage name {name!r}; "
                                f"expected one of {', '.join(ALL_STAGES)}")
            elif name in named:
                problems.append(f"{label}: duplicate stage {name!r}")
            named.add(name)
            _enum(stage.get("status"), STAGE_STATUSES, f"{label}.status",
                  problems)
            reason = stage.get("reason")
            if reason is not None and not isinstance(reason, str):
                problems.append(f"{label}.reason is not a string or null")
            if not _is_number(stage.get("duration_s", 0)):
                problems.append(f"{label}.duration_s is not a number")
            problems += _summary_problems(stage.get("summary"),
                                          f"{label}.summary")
            items = stage.get("items")
            if not isinstance(items, list):
                problems.append(f"{label}.items is not a list")
                continue
            for position, item in enumerate(items):
                problems += _item_problems(item, f"{label}.items[{position}]",
                                           ids)

    failures = report.get("strict_failures")
    if not isinstance(failures, list):
        problems.append("strict_failures is not a list")
    else:
        for ident in failures:
            if ident not in ids:
                problems.append(f"strict_failures names {ident!r}, which is "
                                f"no row in this report")

    requirements = report.get("requirements")
    if not isinstance(requirements, dict):
        problems.append("requirements is not an object")
    else:
        for key, block in requirements.items():
            if not isinstance(block, dict):
                problems.append(f"requirements[{key!r}] is not an object")
                continue
            _enum(block.get("status"), ("pass", "fail", "skip"),
                  f"requirements[{key!r}].status", problems)
            if not isinstance(block.get("checks"), list):
                problems.append(f"requirements[{key!r}].checks is not a list")

    if not isinstance(report.get("warnings", []), list):
        problems.append("warnings is not a list")
    harness = report.get("errors", [])
    if not isinstance(harness, list):
        problems.append("errors is not a list")
    else:
        for index, entry in enumerate(harness):
            if not isinstance(entry, dict):
                problems.append(f"errors[{index}] is not an object")
    return problems


# ------------------------------------------------------------ the markdown


def _short(sha) -> str:
    return str(sha)[:7] if sha else ""


def _duration(seconds) -> str:
    return f"{float(seconds or 0.0):.1f} s"


def _capped(rows: list, limit: int = MAX_RENDERED_FAILURES) -> tuple[list, int]:
    return rows[:limit], max(0, len(rows) - limit)


def _more(extra: int) -> list[str]:
    return [f"_+{extra} more — see report.json_", ""] if extra else []


def render_markdown(report: dict) -> str:
    """The human rendering (FR8): a header, the stage table, then every failure
    with its hint, then the skips grouped by reason.

    Valid GitHub-flavoured markdown, and valid as a PR comment body. Rendered
    failures and skips are capped at :data:`MAX_RENDERED_FAILURES` with a
    ``+N more`` line, because ``$GITHUB_STEP_SUMMARY`` is capped at 1 MiB and a
    truncated summary that says so beats a summary GitHub silently drops.
    """
    source = report.get("source") or {}
    host = report.get("host") or {}
    status = report.get("status", "skip")
    lines = [f"# Geometry CI — `{report.get('project')}` — **{status}**", ""]

    facts = [str(source.get("kind") or "worktree")]
    if source.get("ref"):
        facts.append(f"ref `{source['ref']}`")
    if source.get("sha"):
        facts.append(f"sha `{_short(source['sha'])}`")
    if source.get("label"):
        facts.append(f"`{source['label']}`")
    if source.get("host_sha"):
        facts.append(f"commit `{_short(source['host_sha'])}`")
    if source.get("dirty"):
        facts.append("**dirty**")
    # `sys.version` is "3.12.4 (main, …)": the first token is the version, and
    # splitting on an explicit separator never raises on an empty string.
    python = str(host.get("python") or "?").split(" ")[0] or "?"
    facts += [_duration(report.get("duration_s")),
              f"agentcad {report.get('agentcad')}",
              str(host.get("platform") or "?"),
              f"python {python}",
              f"fem: {'yes' if host.get('fem') else 'no'}",
              f"strict: {'yes' if report.get('strict') else 'no'}",
              f"exit {report.get('exit_code')}"]
    lines += [" · ".join(facts), ""]

    if not report.get("complete", True):
        lines += ["> **Incomplete** — the run was cut short (the `--budget` "
                  "ran out); stages it never reached are reported as skipped.",
                  ""]
    failures = report.get("strict_failures") or []
    if failures:
        shown, extra = _capped(failures, 10)
        named = ", ".join(f"`{ident}`" for ident in shown)
        tail = f" (+{extra} more)" if extra else ""
        lines += [f"> **strict** — {len(failures)} skipped row(s) count as "
                  f"failures: {named}{tail}", ""]

    lines += ["| Stage | Status | Pass | Fail | Skip | Error | Total | Time |",
              "|---|---|---:|---:|---:|---:|---:|---|"]
    for stage in report.get("stages") or []:
        summary = stage.get("summary") or {}
        state = stage.get("status", "skip")
        if stage.get("reason"):
            state = f"{state} ({stage['reason']})"
        lines.append(
            f"| {stage.get('name')} | {state} | {summary.get('passed', 0)} | "
            f"{summary.get('failed', 0)} | {summary.get('skipped', 0)} | "
            f"{summary.get('errors', 0)} | {summary.get('total', 0)} | "
            f"{_duration(stage.get('duration_s'))} |")
    lines.append("")

    items = [item for stage in report.get("stages") or []
             for item in stage.get("items") or []]
    lines += _failure_lines(items)
    lines += _skip_lines(items)
    lines += _harness_lines(report)
    return "\n".join(lines).rstrip() + "\n"


def _failure_lines(items: list[dict]) -> list[str]:
    broken = [item for item in items
              if item.get("status") in ("fail", "error")]
    if not broken:
        return []
    shown, extra = _capped(broken)
    lines = ["## Failures", ""]
    for item in shown:
        lines += [f"### `{item.get('id')}` — {item.get('status')}", "",
                  str(item.get("message") or ""), ""]
        error = item.get("error") or {}
        details = error.get("details") or {}
        if error.get("type"):
            where = f" at line {details['line']}" if details.get("line") \
                else ""
            lines += [f"- `{error['type']}`{where}"]
        hint = details.get("hint") or item.get("hint")
        if hint:
            lines += ["", f"> {hint}"]
        lines.append("")
    return lines + _more(extra)


def _skip_lines(items: list[dict]) -> list[str]:
    skipped = [item for item in items if item.get("status") == "skip"]
    if not skipped:
        return []
    grouped: dict[str, list[dict]] = {}
    for item in skipped:
        grouped.setdefault(item.get("reason") or "unspecified", []).append(item)
    lines = ["## Skipped", ""]
    budget = MAX_RENDERED_FAILURES
    for reason, rows in grouped.items():
        lines += [f"**{reason}** ({len(rows)})", ""]
        hint = next((row.get("hint") for row in rows if row.get("hint")), None)
        if hint:
            lines += [f"> {hint}", ""]
        shown, extra = _capped(rows, max(0, budget))
        budget -= len(shown)
        for row in shown:
            lines.append(f"- `{row.get('id')}` — {row.get('message') or ''}")
        lines.append("")
        lines += _more(extra)
    return lines


def _harness_lines(report: dict) -> list[str]:
    lines: list[str] = []
    warnings = report.get("warnings") or []
    if warnings:
        shown, extra = _capped(warnings)
        lines += ["## Warnings", ""]
        lines += [f"- {warning}" for warning in shown]
        lines += [""] + _more(extra)
    errors = report.get("errors") or []
    if errors:
        shown, extra = _capped(errors)
        lines += ["## Harness errors", ""]
        lines += [f"- `{entry.get('type')}` — {entry.get('message')}"
                  for entry in shown]
        lines += [""] + _more(extra)
    return lines


# ------------------------------------------------- the flat_pattern scan


#: The fallback for a script that will not parse: a **line-anchored**
#: ``def flat_pattern(``. Anchoring keeps a comment, a string literal and a
#: method call out; an indented match is accepted, because there is no AST to
#: tell a nested definition apart and the direction to err in is *declaring*
#: (``specs._SPECS_TEXT_RE``'s reasoning). The cost of a false positive is one
#: row on a script that has already failed its build.
_FLAT_TEXT_RE = re.compile(r"^[ \t]*(?:async[ \t]+)?def[ \t]+flat_pattern"
                           r"[ \t]*\(", re.MULTILINE)


def declares_flat_pattern(script: str) -> bool:
    """True iff *script* defines a module-level ``flat_pattern``.

    AST, **never exec**: the kernel worker is the only thing in this system
    that runs a part script, and a *presence* question must not become a reason
    to execute one (``specs.declares_specs``' rule). This is what makes a
    project with no sheet metal cost nothing in the drawings stage — a part
    that does not define it gets no row at all: absent, not green.

    A script that does not parse **fails closed** into the text scan; it has
    already failed its build, so a false positive costs one row.
    """
    if not isinstance(script, str):
        return False
    key = hashlib.sha256(script.encode()).hexdigest()
    hit = _FLAT_MEMO.get(key)
    if hit is not None:
        return hit
    try:
        tree = ast.parse(script)
    except (SyntaxError, ValueError):
        answer = bool(_FLAT_TEXT_RE.search(script))
    else:
        answer = any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                     and node.name == "flat_pattern" for node in tree.body)
    if len(_FLAT_MEMO) > _MEMO_LIMIT:
        _FLAT_MEMO.clear()
    _FLAT_MEMO[key] = answer
    return answer


# ------------------------------------------------------------ the sequencer


#: Hints for the skips the *runner* emits. A skip row always says why and what
#: to do about it (:func:`make_item` enforces it), so each of these is one
#: half of a contract, not decoration.
_BUDGET_HINT = ("the run's --budget ran out before this item was reached; "
                "raise --budget, narrow --stages, or check fewer parts")
_MESH_HINT = ("booleans on an imported STL mesh segfault OCCT, so this "
              "instance was excluded from the pairwise interference check — "
              "an empty pair list is therefore not proof that it clears; "
              "import the part as STEP to include it")
_NOT_SCRIPT_HINT = ("drawings and flat patterns are generated from a part "
                    "script, and a reference (imported) part has none")

_DXF_HINT = ("DXF is not byte-stable: ezdxf stamps $TDCREATE and fresh "
             "$FINGERPRINTGUID/$VERSIONGUID into every document it creates, so "
             "two identical builds produce different bytes. Adopting ezdxf's "
             "fixed-date / CONST_GUID path in the drawing handlers is the "
             "prerequisite before DXF can join this comparison")

#: The kind a whole-stage failure is filed under, when a stage raises something
#: nobody predicted and the runner has to name the stage itself as the subject.
_STAGE_KIND = {"build": "part", "assembly": "instance", "specs": "check",
               "drawings": "drawing", "determinism": "part"}

#: The mesh sidecars a determinism run compares byte for byte. Both are written
#: by ``kernel/worker.py`` through ``acm.py``/``mesh.py``, none of which touch
#: ``datetime``, ``uuid``, ``random`` or ``time()`` — which is *why* they can
#: carry an equality assertion (design Decision 7). ``.metrics.json`` is not
#: here: its numbers are compared as numbers, below.
_MESH_ARTIFACTS = (".acm", ".faces.u32")

#: The scalars a determinism run compares. Exact equality, not a tolerance:
#: the product guarantee is "same script + params ⇒ identical output", and a
#: tolerance would quietly redefine it.
_METRIC_KEYS = ("volume_mm3", "mass_g", "area_mm2")


def _ephemeral_service(work_dir: Path, tree: Path, kernel):
    """A second ``AgentCADService`` over *tree*, sharing *kernel* — muzzled.

    Returns ``(service, registry, project_name)``. The service is rooted at
    *work_dir*, so ``canonical_path_of`` — and therefore ``.cache/`` and
    ``exports/`` — lands inside the throwaway directory and the user's project
    is untouched *by construction* rather than by care.

    The two assignments below are the dangerous part of this whole feature,
    and each is named for the failure it prevents. They are not decoration:
    losing either turns a command whose contract is "never mutates" into one
    that writes to the user's repository.

    The kernel is **shared**, never re-started: a second pool would cost
    another ~3 s per worker and ~0.5 GB of RAM to run the same builds.
    """
    from .service import AgentCADService, EventBus
    from .tools import build_registry

    # Both resolved, because `ProjectStore.open` resolves the path it is given
    # and compares it against `root / <name>`: an unresolved root (macOS hands
    # `/var/…` for `/private/var/…`) makes the store believe a *different*
    # project of that name is already registered.
    work_dir, tree = Path(work_dir).resolve(), Path(tree).resolve()
    service = AgentCADService(Path(work_dir), kernel, EventBus())
    # NON-NEGOTIABLE. `AgentCADService.__init__` installs `_snapshot_on_event`
    # as the bus's pre-fan-out hook, so ANY `project_changed` publish commits a
    # history snapshot into this tree — and in ref mode this tree is a LINKED
    # WORKTREE of the user's `.history` repo, so that commit lands in the
    # user's real repository. A check may not commit.
    service.bus.on_publish = None
    project = service.store.open(tree)
    registry = build_registry(service)
    # NON-NEGOTIABLE, and only meaningful AFTER `build_registry`: the
    # versioning pack constructs a `BranchManager` (git is on PATH), and
    # constructing one installs `store.branch_resolver`. Left installed it
    # would resolve every authored read and write against a
    # `.history/agentcad/` sidecar that does not exist here — and write one.
    # A check runs on exactly one tree; it needs no branch layer.
    service.store.branch_resolver = None
    return service, registry, project


def _byte_diff(left: Path, right: Path) -> int | None:
    """Offset of the first differing byte, or ``None`` when the files are
    identical. A file that is a prefix of the other differs at its own length.

    Streamed in blocks: an ``.acm`` mesh for a 33-part assembly is tens of MB,
    and a determinism guard that needs both copies resident to answer "are
    these equal" would be its own kind of failure.
    """
    block = 1 << 20
    offset = 0
    with left.open("rb") as a, right.open("rb") as b:
        while True:
            chunk_a, chunk_b = a.read(block), b.read(block)
            if chunk_a == chunk_b:
                if not chunk_a:
                    return None
                offset += len(chunk_a)
                continue
            for index in range(min(len(chunk_a), len(chunk_b))):
                if chunk_a[index] != chunk_b[index]:
                    return offset + index
            return offset + min(len(chunk_a), len(chunk_b))


def _compare_builds(key_a: str | None, key_b: str | None, cache_a: Path,
                    cache_b: Path, metrics_a: dict, metrics_b: dict) \
        -> tuple[list[str], list[str]]:
    """``(divergences, what was compared)`` for two builds of one part.

    Three comparisons, in the order a reader would debug them: the content
    hash (``_cache_key_for``), the mesh bytes it addresses, and the scalars the
    kernel measured. "Not deterministic" is not a useful sentence; "the .acm
    differs at byte 41 208" is, so each entry says *which* artefact and *where*.

    The second return value is what makes a green row mean something: an
    artefact neither build wrote is **not** counted as agreement, so a row that
    says ``pass`` also says what it looked at.
    """
    if key_a != key_b:
        return ([f"the cache key differs ({key_a} vs {key_b}) — the same "
                 f"script and parameters hashed to two different content ids"],
                ["cache_key"])
    problems: list[str] = []
    compared = ["cache_key"]
    for suffix in _MESH_ARTIFACTS:
        left, right = cache_a / f"{key_a}{suffix}", cache_b / f"{key_b}{suffix}"
        if not left.is_file() and not right.is_file():
            continue        # neither side writes it for this part: not a fact
        compared.append(suffix)
        if left.is_file() != right.is_file():
            side = "the second" if left.is_file() else "the first"
            problems.append(f"{suffix} was written by one build and not the "
                            f"other ({side} build has none)")
            continue
        offset = _byte_diff(left, right)
        if offset is not None:
            problems.append(f"{suffix} differs at byte {offset} "
                            f"({left.stat().st_size} vs "
                            f"{right.stat().st_size} bytes)")
    for key in _METRIC_KEYS:
        first, second = metrics_a.get(key), metrics_b.get(key)
        compared.append(key)
        if first != second:
            problems.append(f"{key} differs ({first!r} vs {second!r})")
    return problems, compared


def _elapsed(started: float) -> float:
    return round(time.monotonic() - started, 3)


def _payload(exc: BaseException) -> dict:
    """Any exception as the structured payload the tools already return.

    A ``KernelError`` carries its own (``details.traceback``, ``details.line``,
    the Error Doctor's ``details.hint``) and is passed through untouched — FR7
    is literal: a machine consumer of a check report gets exactly what an agent
    calling ``update_part_script`` would have got. An ``AppError`` is named the
    way :meth:`ToolRegistry.call` names it, so one error family reads the same
    on every surface.
    """
    if isinstance(exc, KernelError):
        return exc.to_payload()
    if isinstance(exc, AppError):
        name = type(exc).__name__.replace("Error", "").lower() + "_error"
        return {"type": name, "message": exc.message, "details": exc.details}
    return {"type": type(exc).__name__, "message": str(exc), "details": {}}


class CheckRunner:
    """The four stages, in order, over one project — and nothing else.

    Every stage is a call into a surface that already exists and is reviewed:
    ``service._ensure_built`` per manifest part, ``_resolved_instances`` +
    ``check_interference``, ``SpecRunner.run``, and the registered
    ``generate_drawing`` / ``flat_pattern`` tools. This class orders them,
    budgets them, and turns their results into rows. **It measures nothing**,
    which is why it needs no kernel handler and imports no geometry.

    Two rules the code encodes and a reader should not have to rediscover:

    * **Nothing is captured in ``__init__``.** ``service.specs`` and
      ``service.branches`` are installed by packs that load *after*
      ``tools_run_checks`` (``s`` and ``v`` after ``r``), so they are read
      inside the methods that use them — a runner built at registration time
      would otherwise capture ``None`` forever.
    * **A failing, skipped or errored item is payload, never an exception.**
      The only exceptions that leave :meth:`run` are the harness's own —
      an unknown project (``NotFoundError``) or an unknown stage
      (``ValidationError``) — because those mean *we could not produce a
      verdict*, which is exit 2, not a red report.

    Every completed run leaves two traces behind, in :meth:`run` rather than in
    any one caller, so the CLI, the ``run_checks`` tool and the route behave
    identically: a ``check_finished`` event on the bus, and the report in
    :attr:`last` (bounded to :data:`LAST_REPORTS` projects, in memory only).

    ``budget_s`` is a **deadline read between items**, on ``time.monotonic``
    (an NTP step must never move a budget). The honest limitation, which is
    also in ``--help`` and the docs: ``service._ensure_built`` hard-codes 300 s
    and the drawing tools 120 s, neither taking a ``timeout_s``, so the
    worst-case overshoot is **one in-flight kernel call**. The two assembly
    calls do take one and get exactly what is left.
    """

    def __init__(self, service, registry=None):
        # No `service.specs` / `service.branches` here — see the class
        # docstring; the pack that builds this loads before both of theirs.
        self.service = service
        self._registry = registry
        # Per-run state, reset by `run`: the deadline and whether anything was
        # cut short by it (which is what `complete: false` means).
        self._deadline: float | None = None
        self._truncated = False
        self._min_volume = 0.001
        # The last report per project, this process only (LAST_REPORTS deep).
        # `GET /api/projects/{p}/checks` reads it; nothing persists it.
        self.last: dict[str, dict] = {}

    # ------------------------------------------------------------- the budget

    def _remaining(self) -> float | None:
        """Seconds left, or None when the run is unbounded — the value the
        assembly calls take as their ``timeout_s``."""
        if self._deadline is None:
            return None
        return max(0.0, self._deadline - time.monotonic())

    def _out_of_budget(self) -> bool:
        return self._deadline is not None and time.monotonic() > self._deadline

    def _budget_item(self, stage: str, kind: str, subject: str, seen: set,
                     warnings: list[str]) -> dict:
        """One item the budget never reached, and the flag that makes the
        whole report ``complete: false`` (and therefore exit 2)."""
        self._truncated = True
        return make_item(stage, kind, subject, "skip",
                         "not reached: the run's budget was exhausted before "
                         "this item was measured",
                         reason="budget_exceeded", hint=_BUDGET_HINT,
                         seen=seen, warnings=warnings)

    # ---------------------------------------------------------------- the run

    def run(self, proj: str, *, ref: str | None = None,
            stages: tuple[str, ...] = STAGES, strict: bool = False,
            budget_s: float | None = None, min_volume: float = 0.001,
            verify_determinism: bool = False, sha: str | None = None,
            ref_label: str | None = None,
            work_dir: str | None = None) -> dict:
        """Certify *proj* and answer with one ``schema: 1`` report.

        *stages* selects a subset; the unselected ones still appear, as
        ``skip``/``not_selected``, so a consumer never has to guess whether a
        stage was green or absent. *strict* changes only the derived verdict
        (Decision 6). *sha* and *ref_label* are **provenance** — the host VCS's
        commit and ref name, recorded and never resolved (Decision 9).

        *ref* measures a **commit** rather than the working tree: it is
        resolved (branch, then tag, then commit id), materialized into a
        throwaway detached ``git worktree`` under *work_dir*, and driven
        through a second, ephemeral service — so the caller's files and
        ``.cache/`` are byte-identical afterwards, at the price of a cold
        cache. *verify_determinism* appends the ``determinism`` pseudo-stage:
        every part built a second time into a fresh cache and compared byte
        for byte.
        """
        selected = self._selected(stages)
        started_at = _now()
        started = time.monotonic()
        self._deadline = None if budget_s is None else started + float(budget_s)
        self._truncated = False
        self._min_volume = float(min_volume)

        seen: set[str] = set()
        warnings: list[str] = []
        errors: list[dict] = []
        if ref is None:
            manifest = self.service.store.manifest(proj)  # NotFoundError: → 2
            project = manifest.get("name") or proj
            source = self._source(proj, sha, ref_label)
            blocks = self._measure(self, proj, selected, seen, warnings, errors)
            if verify_determinism:
                blocks.append(self._determinism_stage(
                    self, proj, work_dir, seen, warnings, errors))
        else:
            project, source, blocks = self._run_ref(
                proj, ref, selected, seen, warnings, errors, sha=sha,
                ref_label=ref_label, work_dir=work_dir,
                verify_determinism=verify_determinism)
        report = finalize_report(
            project, blocks, source=source, host=self._host(),
            started=started_at, duration_s=time.monotonic() - started,
            strict=strict, complete=not self._truncated, warnings=warnings,
            errors=errors)
        # Both are here rather than in the tool pack so that the CLI, the tool
        # and the route emit the same event and fill the same cache — one run,
        # one announcement, whoever asked for it (AC9).
        self._remember(proj, report)
        self._publish(proj, ref, report)
        return report

    # ------------------------------------------------- what a run leaves behind

    def _remember(self, proj: str, report: dict) -> None:
        """Keep the last report for *proj*, bounded to :data:`LAST_REPORTS`.

        Insertion-ordered: re-checking a project moves it to the end, so the
        entry evicted is genuinely the least recently produced.
        """
        self.last.pop(proj, None)
        self.last[proj] = report
        while len(self.last) > LAST_REPORTS:
            self.last.pop(next(iter(self.last)))

    def last_report(self, proj: str) -> dict:
        """The last report this process produced for *proj*.

        A :class:`NotFoundError` rather than ``None``: "no check has run here"
        is a 404, and it is not the same answer as a green report.
        """
        report = self.last.get(proj)
        if report is None:
            raise NotFoundError(
                f"no check report for {proj!r} in this process — run a check "
                f"first (the cache is in memory and is not persisted)",
                {"project": proj})
        return report

    def _publish(self, proj: str, ref: str | None, report: dict) -> None:
        """``check_finished`` — for **every** completed run, including a red
        one and a budget-truncated one.

        A UI that only heard about green runs would leave a stale badge exactly
        when it mattered. The payload is the verdict, never the whole report:
        the document goes over HTTP, the event says it is worth fetching.
        Publishing is best effort — a dropped event must not lose a report.
        """
        bus = getattr(self.service, "bus", None)
        if bus is None:
            return
        with contextlib.suppress(Exception):
            bus.publish({"type": "check_finished", "project": proj,
                         "ref": ref, "status": report["status"],
                         "exit_code": report["exit_code"],
                         "summary": report["summary"],
                         "duration_s": report["duration_s"]})

    def _measure(self, runner: "CheckRunner", proj: str, selected: set[str],
                 seen: set, warnings: list[str],
                 errors: list[dict]) -> list[dict]:
        """The four stages, in order, over whichever service *runner* holds.

        Working-tree mode passes ``self``; ref mode passes a runner bound to
        the ephemeral service. The stages themselves cannot tell the
        difference, which is the point: one pipeline, measured twice over.
        """
        return [runner._stage(name, proj, selected, seen, warnings, errors)
                for name in STAGES]

    def _bind(self, service, registry) -> "CheckRunner":
        """A runner over *service*, sharing **this** run's deadline and
        min-volume — a second budget would be a second promise."""
        inner = CheckRunner(service, registry)
        inner._deadline = self._deadline
        inner._min_volume = self._min_volume
        return inner

    def _selected(self, stages) -> set[str]:
        names = tuple(STAGES if stages is None else stages)
        unknown = [name for name in names if name not in STAGES]
        if unknown:
            raise ValidationError(
                f"unknown check stage(s) {', '.join(repr(n) for n in unknown)};"
                f" expected any of {', '.join(STAGES)}",
                {"stages": list(STAGES), "unknown": unknown})
        return set(names)

    def _stage(self, name: str, proj: str, selected: set[str], seen: set,
               warnings: list[str], errors: list[dict]) -> dict:
        """One stage block — dispatched, timed, and never allowed to raise.

        An unexpected exception out of a stage is *this program's* fault as
        much as the model's, so it becomes one ``error`` item plus a
        ``report.errors[]`` entry and the run continues: the stages after it
        still carry information, and a report that stops at the first surprise
        is worth less than one that names it.
        """
        if name not in selected:
            return make_stage(name, reason="not_selected")
        if self._out_of_budget():
            self._truncated = True
            return make_stage(name, reason="budget_exceeded")
        handler = {"build": self._stage_build,
                   "assembly": self._stage_assembly,
                   "specs": self._stage_specs,
                   "drawings": self._stage_drawings}[name]
        started = time.monotonic()
        try:
            return handler(proj, seen, warnings, errors, started)
        except Exception as exc:  # noqa: BLE001 — a stage never propagates
            payload = _payload(exc)
            errors.append({**payload, "stage": name, "fatal": True})
            item = make_item(name, _STAGE_KIND[name], name, "error",
                             f"the {name} stage did not complete: "
                             f"{payload['message']}",
                             error=payload, seen=seen, warnings=warnings)
            return make_stage(name, [item], duration_s=_elapsed(started))

    # -------------------------------------------------------- stage 1: build

    def _stage_build(self, proj: str, seen: set, warnings: list[str],
                     errors: list[dict], started: float) -> dict:
        """One row per manifest part, through the same ``_ensure_built`` that
        ``get_assembly``, ``merge._validate`` and the packet already use — so
        the cache the app warms is the cache a check hits."""
        items = []
        for entry in self.service.store.manifest(proj)["parts"]:
            part_id = entry["id"]
            if self._out_of_budget():
                items.append(self._budget_item("build", "part", part_id, seen,
                                               warnings))
                continue
            items.append(self._build_item(proj, part_id, seen, warnings,
                                          errors))
        return make_stage("build", items, duration_s=_elapsed(started))

    def _build_item(self, proj: str, part_id: str, seen: set,
                    warnings: list[str], errors: list[dict]) -> dict:
        cached = self._is_cached(proj, part_id)
        reference = self._is_reference(proj, part_id)
        try:
            result = self.service._ensure_built(proj, part_id)
        except Exception as exc:  # noqa: BLE001 — `_ensure_built` converts a
            # KernelError into `ok: false` already; this is the defensive edge
            # (a missing script file, an unreadable import) that must still be
            # one row rather than the end of the run.
            payload = _payload(exc)
            errors.append({**payload, "stage": "build", "part": part_id})
            return make_item("build", "part", part_id, "error",
                             f"the build did not complete: "
                             f"{payload['message']}", error=payload,
                             seen=seen, warnings=warnings)
        if not result.get("ok"):
            payload = result.get("error") or {
                "type": "kernel_error", "message": "the build failed",
                "details": {}}
            return make_item("build", "part", part_id, "fail",
                             f"build failed: {payload.get('message')}",
                             error=payload, details={"cached": cached},
                             seen=seen, warnings=warnings)
        warnings.extend(f"{part_id}: {warning}"
                        for warning in result.get("warnings") or [])
        metrics = result.get("metrics") or {}
        details = {"cache_key": result.get("cache_key"),
                   "volume_mm3": metrics.get("volume_mm3"),
                   "mass_g": metrics.get("mass_g"),
                   "n_solids": metrics.get("n_solids"),
                   "is_valid": metrics.get("is_valid"),
                   "cached": cached}
        if metrics.get("is_valid") is False and not reference:
            # PRD divergence, recorded in the design (Decision 3): the kernel
            # reports validity for the whole shape only, so this row says
            # exactly that and carries the per-solid metrics beside it.
            return make_item("build", "part", part_id, "fail",
                             "built, but the kernel reports the shape is not "
                             "valid B-rep geometry",
                             details={**details,
                                      "solids": metrics.get("solids")},
                             seen=seen, warnings=warnings)
        if metrics.get("is_valid") is False:
            # An IMPORTED part's whole-shape flag is not a verdict on a model
            # anybody here authored: `Compound.is_valid` over a 180-solid STEP
            # assembly is routinely false, which is why `test_examples` exempts
            # reference parts from it and `import_cad_file` merely reports it.
            # So the row passes and the fact is raised as a warning — loudly,
            # in both renderings, but not as a red nobody can act on.
            warnings.append(
                f"{part_id}: the imported geometry reports is_valid=false on "
                f"the whole shape ({metrics.get('n_solids')} solids); "
                f"validity is reported for imported parts, never enforced")
        return make_item(
            "build", "part", part_id, "pass",
            f"built{' from cache' if cached else ''} — "
            f"{_number(metrics.get('volume_mm3'))} mm³, "
            f"{_number(metrics.get('mass_g'))} g, "
            f"{metrics.get('n_solids', 0)} solid(s), "
            + ("valid" if metrics.get("is_valid")
               else "is_valid=false (imported: reported, not enforced)"),
            details=details, seen=seen, warnings=warnings)

    def _is_reference(self, proj: str, part_id: str) -> bool:
        try:
            return self.service.store.get_part(proj, part_id).kind == "reference"
        except Exception:  # noqa: BLE001 — an unreadable manifest entry is
            return False   # already the build's problem, not this question's

    def _is_cached(self, proj: str, part_id: str) -> bool:
        """Whether this part's current cache key is already on disk.

        Observed **before** the build, never inferred from it: ``_ensure_built``
        returns the same shape either way, and guessing afterwards would report
        every part as cached.
        """
        try:
            record = self.service.store.get_part(proj, part_id)
            key = self.service._cache_key_for(proj, record)
            return (self.service.store.cache_dir(proj) / f"{key}.acm").is_file()
        except Exception:  # noqa: BLE001 — provenance must never break a row
            return False

    # ----------------------------------------------------- stage 2: assembly

    def _stage_assembly(self, proj: str, seen: set, warnings: list[str],
                        errors: list[dict], started: float) -> dict:
        """The mate pass and the pairwise interference check, through the
        **service methods** — the ``check_interference`` tool has no
        ``timeout_s`` in its schema, so a budget could not reach it.
        """
        if len(self.service.store.instances(proj)) < 2:
            return make_stage("assembly", reason="no_instances",
                              duration_s=_elapsed(started))
        try:
            resolved = self.service._resolved_instances(
                proj, timeout_s=self._remaining())
        except (AppError, KernelError) as exc:
            payload = _payload(exc)
            # A mate that will not resolve is the model being wrong (`fail`);
            # a kernel that broke mid-resolution is "we do not know" (`error`).
            status = "error" if isinstance(exc, KernelError) else "fail"
            if status == "error":
                errors.append({**payload, "stage": "assembly"})
            item = make_item("assembly", "mate", "mates", status,
                             f"the assembly's mates do not resolve: "
                             f"{payload['message']}", error=payload,
                             seen=seen, warnings=warnings)
            return make_stage("assembly", [item], duration_s=_elapsed(started))

        pairs: list[dict] = []
        mesh: list[str] = []
        extra: list[dict] = []
        if self._out_of_budget():
            extra.append(self._budget_item("assembly", "pair", "interference",
                                           seen, warnings))
        else:
            try:
                result = self.service.check_interference(
                    proj, self._min_volume, timeout_s=self._remaining())
            except Exception as exc:  # noqa: BLE001 — one row, not a traceback
                payload = _payload(exc)
                errors.append({**payload, "stage": "assembly"})
                extra.append(make_item(
                    "assembly", "pair", "interference", "error",
                    f"the interference check did not complete: "
                    f"{payload['message']}", error=payload, seen=seen,
                    warnings=warnings))
            else:
                pairs = result.get("pairs") or []
                mesh = result.get("skipped_mesh") or []

        # The instance rows are built AFTER the interference call on purpose:
        # an instance excluded from it is a `skip`, and one instance must have
        # exactly one row (a pass row plus a skip row would be two ids for the
        # same subject, and the second would silently become `…#2`).
        excluded = set(mesh)
        items = [self._instance_item(instance, excluded, seen, warnings)
                 for instance in resolved]
        items += [self._pair_item(pair, seen, warnings) for pair in pairs]
        items += extra
        return make_stage("assembly", items, duration_s=_elapsed(started))

    def _instance_item(self, instance, excluded: set[str], seen: set,
                       warnings: list[str]) -> dict:
        details = {"part": instance.part,
                   "mated": bool(getattr(instance, "mate", None)),
                   "position": list(instance.position),
                   "rotation_deg": list(instance.rotation_deg)}
        if instance.id in excluded:
            return make_item("assembly", "instance", instance.id, "skip",
                             f"placed, but excluded from the interference "
                             f"check: {instance.part} is an imported mesh",
                             reason="mesh_only", hint=_MESH_HINT,
                             details=details, seen=seen, warnings=warnings)
        return make_item("assembly", "instance", instance.id, "pass",
                         f"placed{' by its mate' if details['mated'] else ''} "
                         f"at {_point(instance.position)}", details=details,
                         seen=seen, warnings=warnings)

    def _pair_item(self, pair: dict, seen: set, warnings: list[str]) -> dict:
        a, b = pair.get("a"), pair.get("b")
        volume = pair.get("volume_mm3")
        return make_item("assembly", "pair", f"{a} ↔ {b}", "fail",
                         f"{a} and {b} overlap by {_number(volume)} mm³",
                         details={"a": a, "b": b, "volume_mm3": volume},
                         seen=seen, warnings=warnings)

    # -------------------------------------------------------- stage 3: specs

    def _stage_specs(self, proj: str, seen: set, warnings: list[str],
                     errors: list[dict], started: float) -> dict:
        """``SpecRunner.run`` — all three tiers, unbounded, and *the documented
        exit from every cached refusal* PRD-003 keeps. Its report is embedded
        whole, so requirement traceability is passed through rather than
        re-derived.
        """
        runner = getattr(self.service, "specs", None)   # read HERE, not in init
        if runner is None:
            return make_stage("specs", reason="specs_unavailable",
                              duration_s=_elapsed(started))
        try:
            report = runner.run(proj)
        except Exception as exc:  # noqa: BLE001 — the runner never propagates
            payload = _payload(exc)
            errors.append({**payload, "stage": "specs"})
            item = make_item("specs", "check", "specs", "error",
                             f"the spec run did not complete: "
                             f"{payload['message']}", error=payload, seen=seen,
                             warnings=warnings)
            return make_stage("specs", [item], duration_s=_elapsed(started))
        warnings.extend(report.get("warnings") or [])
        if not report.get("declared"):
            # "A part that declares nothing is absent, not green" travels up
            # one level intact.
            return make_stage("specs", [], reason="not_declared",
                              duration_s=_elapsed(started), report=report)
        items = [self._spec_item(row, seen, warnings)
                 for row in report.get("checks") or []]
        return make_stage("specs", items, duration_s=_elapsed(started),
                          report=report)

    def _spec_item(self, row: dict, seen: set, warnings: list[str]) -> dict:
        """One spec row as one item, unchanged in every field that matters.

        ``measured``, ``limit`` and ``unit`` travel in ``details`` — the row
        keeps its own numbers, so a consumer reading the check report gets the
        same evidence as one reading the spec report.
        """
        status = row.get("status")
        reason, hint = row.get("reason"), row.get("hint")
        if status == "skip" and not (reason and hint):
            # PRD-003 guarantees both; a sidecar written by an older format
            # must degrade into a named row rather than a ValueError that
            # takes the whole stage with it.
            reason = reason or "unspecified"
            hint = hint or ("the spec runner reported a skip with no hint; "
                            "re-run run_specs to re-measure it")
        details = {**(row.get("details") or {}),
                   "measured": row.get("measured"), "limit": row.get("limit"),
                   "unit": row.get("unit"), "scope": row.get("scope"),
                   "part": row.get("part")}
        if row.get("location") is not None:
            details["location"] = row["location"]
        return make_item("specs", "check", row.get("id") or row.get("name", "?"),
                         status, row.get("message") or "", reason=reason,
                         hint=hint, error=row.get("error"), details=details,
                         requirement=row.get("requirement"), seen=seen,
                         warnings=warnings)

    # ----------------------------------------------------- stage 4: drawings

    def _stage_drawings(self, proj: str, seen: set, warnings: list[str],
                        errors: list[dict], started: float) -> dict:
        """The registered tools, not a re-derivation: they carry the "is it
        drawable" guards, the PMI forwarding and the export paths.

        SVG only — DXF is not byte-stable (``ezdxf`` stamps ``$TDCREATE`` and
        fresh GUIDs on every document), so it cannot join a determinism
        assertion and generating it would only double the runtime.
        """
        if self._registry is None:
            return make_stage("drawings", reason="drawings_unavailable",
                              duration_s=_elapsed(started))
        items: list[dict] = []
        for entry in self.service.store.manifest(proj)["parts"]:
            part_id = entry["id"]
            if self._out_of_budget():
                items.append(self._budget_item("drawings", "drawing", part_id,
                                               seen, warnings))
                continue
            if entry.get("kind", "script") != "script":
                items.append(make_item(
                    "drawings", "drawing", part_id, "skip",
                    "no drawing: this is a reference (imported) part",
                    reason="not_script", hint=_NOT_SCRIPT_HINT, seen=seen,
                    warnings=warnings))
                continue
            items.append(self._tool_item(
                "generate_drawing", "drawing", proj, part_id, part_id, seen,
                warnings))
            if not self._declares_flat(proj, part_id):
                continue        # absent, not green: no row at all
            if self._out_of_budget():
                items.append(self._budget_item(
                    "drawings", "flat_pattern", f"{part_id}:flat_pattern",
                    seen, warnings))
                continue
            items.append(self._tool_item(
                "flat_pattern", "flat_pattern", proj, part_id,
                f"{part_id}:flat_pattern", seen, warnings))
        return make_stage("drawings", items, duration_s=_elapsed(started))

    def _declares_flat(self, proj: str, part_id: str) -> bool:
        try:
            script = self.service.store.read_script(proj, part_id)
        except Exception:  # noqa: BLE001 — a script we cannot read has already
            return False   # failed the build stage; it does not fail twice
        return declares_flat_pattern(script)

    def _tool_item(self, tool: str, kind: str, proj: str, part_id: str,
                   subject: str, seen: set, warnings: list[str]) -> dict:
        """One registered tool call as one row.

        ``ToolRegistry.call`` converts ``AppError``/``KernelError`` into an
        ``{"error": {...}}`` payload rather than raising, so the failure path
        here is a dict test — and that payload is carried verbatim.
        """
        result = self._registry.call(tool, {"project": proj,
                                            "part_id": part_id,
                                            "format": "svg"})
        if isinstance(result, dict) and result.get("error"):
            payload = result["error"]
            return make_item("drawings", kind, subject, "fail",
                             f"{tool} failed: {payload.get('message')}",
                             error=payload, seen=seen, warnings=warnings)
        details = {"format": "svg", "path": result.get("path"),
                   "size_bytes": result.get("size_bytes")}
        if result.get("n_bend_lines") is not None:
            details["n_bend_lines"] = result["n_bend_lines"]
        return make_item("drawings", kind, subject, "pass",
                         f"{tool} regenerated "
                         f"({_number(result.get('size_bytes'))} bytes of SVG)",
                         details=details, seen=seen, warnings=warnings)

    # ------------------------------------------------------------- the ref

    def _run_ref(self, proj: str, ref: str, selected: set[str], seen: set,
                 warnings: list[str], errors: list[dict], *, sha: str | None,
                 ref_label: str | None, work_dir: str | None,
                 verify_determinism: bool) -> tuple[str, dict, list[dict]]:
        """Measure a **commit**, and leave the user's project byte-identical.

        The order is load-bearing (design Decision 5): resolve explicitly →
        materialize the resolved sha into a detached worktree → drive an
        ephemeral service rooted in the work dir → tear the worktree down in a
        ``finally``. The report's ``source`` block is stamped out here, by the
        *outer* runner, because the ephemeral service knows nothing about how
        the tree it was handed was named.
        """
        # An unknown project is a NotFoundError here — 404, CLI exit 2.
        canonical = self.service.store.canonical_path_of(proj)
        resolved = self._resolve_ref(proj, canonical, ref, warnings)
        owned = work_dir is None
        # Absolute, always: `history._run` runs git with ``cwd`` set to the
        # project, so a relative --work-dir would put the throwaway worktree
        # *inside the user's project directory* — the one place it may not go.
        root = Path(work_dir).resolve() if work_dir is not None else Path(
            tempfile.mkdtemp(prefix="agentcad-check-")).resolve()
        try:
            root.mkdir(parents=True, exist_ok=True)
            with self._materialized(canonical, resolved["sha"], root,
                                    warnings) as tree:
                service, registry, name = _ephemeral_service(
                    root, tree, self.service.kernel)
                inner = self._bind(service, registry)
                blocks = self._measure(inner, name, selected, seen, warnings,
                                       errors)
                if verify_determinism:
                    blocks.append(self._determinism_stage(
                        inner, name, root, seen, warnings, errors))
                self._truncated = self._truncated or inner._truncated
                project = service.store.manifest(name).get("name") or name
        finally:
            if owned:
                # Only a work dir we created is ours to delete; a caller who
                # passed --work-dir (an actions/cache path, a big disk) keeps it.
                shutil.rmtree(root, ignore_errors=True)
        source = {"kind": resolved["kind"], "ref": ref, "sha": resolved["sha"],
                  "label": ref_label, "host_sha": sha,
                  "dirty": self._ref_dirty(canonical, resolved, warnings)}
        return project, source, blocks

    def _resolve_ref(self, proj: str, canonical: Path, ref: str,
                     warnings: list[str]) -> dict:
        """``{"kind", "ref", "sha"}`` for *ref*: branch, then tag, then commit.

        **Never** ``resolve_ref``: ``git rev-parse`` searches ``refs/tags``
        *before* ``refs/heads``, so a tag named like a branch would silently
        answer for it (PRD-001 X1 — the same reason ``SpecRunner._pinned``
        resolves branches explicitly). A name that is both resolves as the
        **branch** and says so in ``warnings``; ``refs/heads/<x>`` and
        ``refs/tags/<x>`` are accepted for disambiguation.
        """
        history = self.service.history
        if not history.available():
            raise ValidationError(
                "checking a ref needs git on PATH (a ref is materialized from "
                "the project's .history repository); omit --ref to check the "
                "working tree instead", {"ref": ref})
        if not history._has_repo(canonical):
            raise ValidationError(
                f"checking a ref needs git history, and project {proj!r} has "
                f"no .history repository yet — nothing has been snapshotted; "
                f"omit --ref to check the working tree instead",
                {"ref": ref, "project": proj})
        name = str(ref)
        for prefix, kind, resolve in (("refs/heads/", "branch",
                                       history.resolve_branch),
                                      ("refs/tags/", "tag",
                                       history.resolve_tag)):
            if name.startswith(prefix):
                found = resolve(canonical, name[len(prefix):])
                if found:
                    return {"kind": kind, "ref": ref, "sha": found}
                raise NotFoundError(f"{kind} {name[len(prefix):]!r} not found "
                                    f"in project {proj!r}", {"ref": ref})
        branch = history.resolve_branch(canonical, name)
        tag = history.resolve_tag(canonical, name)
        if branch and tag:
            warnings.append(
                f"{ref!r} names both a branch and a tag; the BRANCH was "
                f"checked ({_short(branch)}) — pass 'refs/tags/{ref}' to check "
                f"the tag ({_short(tag)}) instead")
        if branch:
            return {"kind": "branch", "ref": ref, "sha": branch}
        if tag:
            return {"kind": "tag", "ref": ref, "sha": tag}
        if looks_like_commit(name) and history.has_commit(canonical, name):
            return {"kind": "commit", "ref": ref,
                    "sha": self._full_sha(canonical, name) or name}
        raise NotFoundError(
            f"ref {ref!r} not found in project {proj!r}: searched "
            f"refs/heads/{name}, refs/tags/{name} and the project's commit ids",
            {"ref": ref, "searched": ["refs/heads", "refs/tags", "commit"]})

    def _full_sha(self, canonical: Path, commit: str) -> str | None:
        """A short commit id spelled out in full, so ``source.sha`` is one
        thing (40 hex) whatever the caller typed."""
        try:
            result = self.service.history._run(
                canonical, "rev-parse", "--verify", "--quiet",
                f"{commit}^{{commit}}", check=False)
        except HistoryError:
            return None
        return result.stdout.strip() or None

    @contextlib.contextmanager
    def _materialized(self, canonical: Path, sha: str, work_dir: Path,
                      warnings: list[str]):
        """The resolved commit, checked out at ``<work_dir>/<project>/``.

        ``worktree add --detach <sha>`` — **the commit, never the branch
        name**: a branch that is already checked out (its
        ``.history/trees/<b>/``) cannot be checked out a second time, and this
        is ``MergeOrchestrator._stage``'s exact mechanism. ``prune`` runs
        before the add (a killed process leaves an admin entry behind) and
        again after the removal, and every git call goes through
        ``history._run`` — hermetic env, 10 s timeout, never a raw subprocess.

        Teardown is a ``finally`` and it never raises: a worktree that will not
        come off is a ``warnings[]`` entry, because a cleanup problem is not a
        verdict about the user's geometry. ``git worktree prune`` heals a
        leaked registration on the next run.
        """
        history = self.service.history
        tree = Path(work_dir) / canonical.name
        if tree.exists():
            shutil.rmtree(tree, ignore_errors=True)
        try:
            history._run(canonical, "worktree", "prune", check=False)
            added = history._run(canonical, "worktree", "add", "--detach",
                                 str(tree), sha, check=False)
        except HistoryError as exc:
            raise ValidationError(
                f"could not materialize {sha[:8]} for the check: {exc}",
                {"sha": sha}) from exc
        if added.returncode != 0:
            raise ValidationError(
                "could not materialize the ref into a worktree: "
                f"{(added.stderr or '').strip() or (added.stdout or '').strip()}",
                {"sha": sha, "path": str(tree)})
        try:
            yield tree
        finally:
            self._release(canonical, tree, warnings)

    def _release(self, canonical: Path, tree: Path,
                 warnings: list[str]) -> None:
        history = self.service.history
        try:
            removed = history._run(canonical, "worktree", "remove", "--force",
                                   str(tree), check=False)
            if removed.returncode != 0:
                warnings.append(
                    f"the check's temporary worktree at {tree} could not be "
                    f"removed ({(removed.stderr or '').strip()}); it was "
                    f"deleted from disk and `git worktree prune` will forget "
                    f"the registration")
                shutil.rmtree(tree, ignore_errors=True)
            history._run(canonical, "worktree", "prune", check=False)
        except HistoryError as exc:      # a cleanup problem is never a red
            warnings.append(f"the check's temporary worktree at {tree} could "
                            f"not be cleaned up: {exc}")
            shutil.rmtree(tree, ignore_errors=True)

    def _ref_dirty(self, canonical: Path, resolved: dict,
                   warnings: list[str]) -> bool:
        """Whether the checked branch has uncommitted edits on disk.

        A ref check measures the **commit**, so a branch whose working tree has
        been edited since its last snapshot is measured as of that snapshot.
        The runner says so — here, and in a warning — and it deliberately does
        **not** snapshot first: the packet's ``_checkpoint`` may commit because
        it is producing review evidence on the user's behalf; a command whose
        contract is "never mutates" may not.

        Read-only throughout, and best effort: a tag or a commit id has no
        working tree, so there is nothing to be dirty.
        """
        if resolved["kind"] != "branch":
            return False
        branch = str(resolved["ref"]).removeprefix("refs/heads/")
        try:
            tree = self._branch_tree(canonical, branch)
            if tree is None:
                return False
            result = self.service.history._run(tree, "status", "--porcelain",
                                               check=False)
            dirty = bool((result.stdout or "").strip())
        except Exception:  # noqa: BLE001 — provenance, never a verdict
            return False
        if dirty:
            warnings.append(
                f"branch {branch!r} has uncommitted changes in its working "
                f"tree; this check measured its last snapshot "
                f"({_short(resolved['sha'])}), not the files on disk")
        return dirty

    def _branch_tree(self, canonical: Path, branch: str) -> Path | None:
        """The working tree *branch* is checked out in, or ``None``.

        Read-only on purpose: ``BranchManager.tree_of`` would *materialize* a
        missing tree, which is a write, and a check may not make one. So this
        asks git what already exists.

        The **main** worktree is answered from ``symbolic-ref``, not from
        ``worktree list``: AgentCAD's repos are ``--git-dir <project>/.history
        --work-tree <project>``, and git lists the main worktree as its *git
        directory* (``…/.history``) rather than as the project directory. Only
        the linked trees — ``.history/trees/<b>/``, which branching creates —
        are listed at the path they actually live at, and each one holds a
        ``project.json``.
        """
        history = self.service.history
        current = history._run(canonical, "symbolic-ref", "--short", "HEAD",
                               check=False)
        if current.returncode == 0 and current.stdout.strip() == branch:
            return canonical
        result = history._run(canonical, "worktree", "list", "--porcelain",
                              check=False)
        if result.returncode != 0:
            return None
        path: Path | None = None
        for line in (result.stdout or "").splitlines():
            if line.startswith("worktree "):
                path = Path(line[len("worktree "):].strip())
            elif line.startswith("branch ") and path is not None:
                if line[len("branch "):].strip() == f"refs/heads/{branch}" \
                        and (path / "project.json").is_file():
                    return path
        return None

    # ------------------------------------------------------- determinism

    def _determinism_stage(self, runner: "CheckRunner", proj: str,
                           work_dir: str | Path | None, seen: set,
                           warnings: list[str], errors: list[dict]) -> dict:
        """The ``determinism`` pseudo-stage, guarded like a real one.

        It is not in :data:`STAGES` and not selectable with ``--stages``: it
        does not certify the project, it certifies the **product guarantee** —
        same script and parameters ⇒ identical bytes — so it is opt-in
        (``--verify-determinism``) and it is the standing regression guard for
        the one property the whole cache rests on.
        """
        started = time.monotonic()
        if self._out_of_budget():
            self._truncated = True
            return make_stage("determinism", reason="budget_exceeded")
        try:
            return self._determinism(runner, proj, work_dir, seen, warnings,
                                     errors, started)
        except Exception as exc:  # noqa: BLE001 — a stage never propagates
            payload = _payload(exc)
            errors.append({**payload, "stage": "determinism", "fatal": True})
            item = make_item("determinism", "part", "determinism", "error",
                             f"the determinism stage did not complete: "
                             f"{payload['message']}", error=payload, seen=seen,
                             warnings=warnings)
            return make_stage("determinism", [item],
                              duration_s=_elapsed(started))

    def _determinism(self, runner: "CheckRunner", proj: str,
                     work_dir: str | Path | None, seen: set,
                     warnings: list[str], errors: list[dict],
                     started: float) -> dict:
        """Build every part a second time into a **cold** cache and compare.

        The second build runs against a throwaway copy of the measured tree
        with no ``.cache/`` — a cache hit proves nothing, so the copy is the
        only way to make the second build real. The copy carries no git
        (``.history``/``.git`` are excluded), so this side cannot commit
        anywhere even in principle.
        """
        service = runner.service
        tree = Path(service.store.path_of(proj))
        root = Path(tempfile.mkdtemp(
            prefix="agentcad-determinism-",
            dir=str(work_dir) if work_dir is not None else None)).resolve()
        try:
            copy = root / tree.name
            shutil.copytree(tree, copy, ignore=shutil.ignore_patterns(
                ".cache", "exports", ".history", ".git"))
            second, registry, name = _ephemeral_service(root, copy,
                                                        service.kernel)
            items: list[dict] = []
            for entry in service.store.manifest(proj)["parts"]:
                part_id = entry["id"]
                if self._out_of_budget():
                    items.append(self._budget_item("determinism", "part",
                                                   part_id, seen, warnings))
                    continue
                items.append(self._determinism_item(
                    runner, second, registry, proj, name, part_id, entry, seen,
                    warnings, errors))
            items.append(make_item(
                "determinism", "drawing", "dxf", "skip",
                "DXF output was not compared: it is not byte-stable, so an "
                "equality assertion over it would fail on every run",
                reason="not_byte_stable", hint=_DXF_HINT, seen=seen,
                warnings=warnings))
            return make_stage("determinism", items,
                              duration_s=_elapsed(started))
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def _determinism_item(self, runner: "CheckRunner", second, registry,
                          proj: str, mirror: str, part_id: str, entry: dict,
                          seen: set, warnings: list[str],
                          errors: list[dict]) -> dict:
        first = runner.service
        try:
            left = first._ensure_built(proj, part_id)
            right = second._ensure_built(mirror, part_id)
        except Exception as exc:  # noqa: BLE001 — one row, not a traceback
            payload = _payload(exc)
            # The defensive edge `_build_item` also guards (an unreadable
            # script, a copy that lost a file): harness-level, so it is named
            # in `report.errors[]` too. A part that merely *fails* to build is
            # not — the build stage rules on that one.
            errors.append({**payload, "stage": "determinism", "part": part_id})
            return make_item("determinism", "part", part_id, "error",
                             f"determinism could not be measured: "
                             f"{payload['message']}", error=payload, seen=seen,
                             warnings=warnings)
        if not left.get("ok") or not right.get("ok"):
            broken = left if not left.get("ok") else right
            payload = broken.get("error") or {"type": "kernel_error",
                                              "message": "the build failed",
                                              "details": {}}
            # `error`, not `fail`: the part not building is a fact the BUILD
            # stage rules on. Here it means "we do not know whether this part
            # is deterministic", which is exactly what `error` says.
            return make_item("determinism", "part", part_id, "error",
                             f"determinism could not be measured: the part "
                             f"did not build ({payload.get('message')})",
                             error=payload, seen=seen, warnings=warnings)
        diverged, compared = _compare_builds(
            left.get("cache_key"), right.get("cache_key"),
            first.store.cache_dir(proj), second.store.cache_dir(mirror),
            left.get("metrics") or {}, right.get("metrics") or {})
        if entry.get("kind", "script") == "script":
            svg, ok = self._compare_svg(runner, registry, proj, mirror,
                                        part_id, warnings)
            diverged += svg
            if ok:
                compared.append("drawing.svg")
        details = {"cache_key": left.get("cache_key"), "compared": compared,
                   "diverged": diverged}
        if diverged:
            return make_item("determinism", "part", part_id, "fail",
                             "two builds of the same script and parameters "
                             "did not agree: " + "; ".join(diverged),
                             details=details, seen=seen, warnings=warnings)
        return make_item("determinism", "part", part_id, "pass",
                         f"identical across two builds on a cold cache "
                         f"({', '.join(compared)})", details=details,
                         seen=seen, warnings=warnings)

    def _compare_svg(self, runner: "CheckRunner", registry, proj: str,
                     mirror: str, part_id: str,
                     warnings: list[str]) -> tuple[list[str], bool]:
        """The SVG drawing, byte for byte — ``(divergences, compared)``.

        SVG is in because ``handlers/drawing.py`` writes
        ``atomic_write(out, svg.encode())`` with no timestamp and no id; DXF is
        out, with its own row and :data:`_DXF_HINT` saying why. A drawing that
        will not generate produces **no divergence and no row of its own** —
        the drawings stage is where that failure is ruled on — but it does
        produce a warning, so "SVG was not compared" is never silent.
        """
        if runner._registry is None:
            warnings.append(f"{part_id}: no tool registry, so SVG determinism "
                            f"was not measured")
            return [], False
        left = runner._registry.call("generate_drawing", {
            "project": proj, "part_id": part_id, "format": "svg"})
        right = registry.call("generate_drawing", {
            "project": mirror, "part_id": part_id, "format": "svg"})
        for result in (left, right):
            if not isinstance(result, dict) or result.get("error") \
                    or not result.get("path"):
                warnings.append(
                    f"{part_id}: the SVG drawing did not generate, so SVG "
                    f"determinism was not measured (see the drawings stage)")
                return [], False
        offset = _byte_diff(Path(left["path"]), Path(right["path"]))
        if offset is None:
            return [], True
        return [f"the SVG drawing differs at byte {offset}"], True

    # --------------------------------------------------- source and host

    def _source(self, proj: str, sha: str | None,
                ref_label: str | None) -> dict:
        """What was measured, and how it was named.

        Working-tree mode: the caller's branch tree, its ``.history`` head when
        there is one, and whether it has uncommitted edits. Every git touch
        here is **best effort** — a project with no history is an ordinary
        project, and a check must not fail because git is absent.
        """
        return {"kind": "worktree", "ref": None, "sha": self._head(proj),
                "label": ref_label, "host_sha": sha, "dirty": self._dirty(proj)}

    def _tree(self, proj: str):
        """The working tree this run measured (branch-pin aware)."""
        return self.service.store.path_of(proj)

    def _head(self, proj: str) -> str | None:
        history = getattr(self.service, "history", None)
        if history is None:
            return None
        try:
            return history.head(self._tree(proj))
        except Exception:  # noqa: BLE001 — provenance, never a verdict
            return None

    def _dirty(self, proj: str) -> bool:
        history = getattr(self.service, "history", None)
        try:
            path = self._tree(proj)
            if history is None or not history.available() \
                    or not history._has_repo(path):
                return False
            # Through `history._run`: hermetic env, 10 s timeout, never a raw
            # subprocess.
            result = history._run(path, "status", "--porcelain", check=False)
            return bool((result.stdout or "").strip())
        except Exception:  # noqa: BLE001
            return False

    def _host(self) -> dict:
        """The machine, so a report read on another one is interpretable —
        `fem: false` is why a spec row says `fem_extra_missing`, and
        `sandbox`/`pool_size` are why a timing differs."""
        kernel = getattr(self.service, "kernel", None)
        try:
            fem = bool(_specs._fem_available())
        except Exception:  # noqa: BLE001 — an optional extra, never a failure
            fem = False
        return {"platform": platform.system().lower(),
                "python": sys.version,
                "agentcad": agentcad.__version__,
                "fem": fem,
                "sandbox": bool(getattr(kernel, "sandboxed", False)),
                "pool_size": int(getattr(kernel, "size", 1)),
                "kernel_pool": type(kernel).__name__ if kernel else None}


def _number(value) -> str:
    """A measurement for a human, in a message a machine also reads (the
    numbers themselves live in ``details``, typed)."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return "?"
    return f"{float(value):.4g}"


def _point(values) -> str:
    try:
        return "(" + ", ".join(_number(value) for value in values) + ")"
    except TypeError:
        return "(?)"
