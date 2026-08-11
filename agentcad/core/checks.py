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

Nothing here imports ``OCP`` or build123d, directly or transitively — this is
server-process code and a test asserts it.
"""

from __future__ import annotations

import ast
import hashlib
import re
import time

import agentcad

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
