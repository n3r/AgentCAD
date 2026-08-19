"""`bench publish` -- the leaderboard, and the full-disclosure rule as code.

A leaderboard is the one artefact of this feature that is *marketing*, so it is
the one place where a soft rule would be worth the most and cost the most. The
rule is therefore mechanical and fail-closed (design Decision 12): a row that
does not disclose everything is **not rendered dimmed, not footnoted, not
flagged** -- it is rejected, the command exits 1, and **nothing is written**.
There is deliberately no override flag.

Input layout::

    <leaderboard>/rows/<row-id>/row.json
    <leaderboard>/rows/<row-id>/report.json     # `bench report --json-out`

The five rules a row must satisfy, each naming the key it failed on:

1. every key of `REQUIRED_ROW_KEYS` is present, and every string one is
   non-empty (`notes` may be empty; `config` may be `{}` but must be present);
2. `report.json` validates against the design-§10 report schema;
3. the row's `task_set` / `harness` / `agentcad` equal the report's;
4. `submission` and `transcript` are absolute `https://` URLs, or paths that
   stay inside the row's own directory and exist there;
5. the report covers **every** task of the declared set -- a partial run is not
   a leaderboard row, and a task the report itself flags `missing: true` is a
   partial run just as much as an absent one.

**The row's identity is its directory name, never the document's `id`.** That
is `report._score_paths`' ruling one level up: the directory is the one thing a
truncated or mislabelled document cannot lie about. A `row.json` that carries a
disagreeing `id` is rejected rather than silently given two names.

**An unreadable row document is a rejected row (exit 1), not a harness error
(exit 2).** A row we cannot read has disclosed nothing, which is exactly what
rule 1 exists to refuse; exit 2 is reserved for the input the *caller* named --
a leaderboard directory that is not there, an output path that will not take
bytes.

The page is **self-contained by construction**: one inline `<style>`, no
script, no remote asset of any kind, no web font. It carries no clock reading
and no filesystem path, and rows are ordered by `total` descending with ties
broken by id ascending, so republishing the same input produces the same bytes
(the FR6/AC3 discipline, applied to HTML).

OCP-free, like everything under `agentcad/bench/`.
"""
from __future__ import annotations

import datetime
import math
from html import escape as _escape
from pathlib import Path, PurePosixPath

from ..core.model import ValidationError
from ..core.project import ProjectStore
from ._json import canonical_json, read_json
from .report import REPORT_SCHEMA
from .tasks import CATEGORIES

#: `row.json`'s own version.
ROW_SCHEMA = 1

#: The disclosure list of design §12 rule 1 -- what a submitter must state for
#: the row to be publishable at all. `schema` and `id` are the *envelope* and
#: are checked separately: they identify the document, they do not disclose
#: anything about the run.
REQUIRED_ROW_KEYS = ("agent", "model", "agentcad", "harness", "task_set",
                     "date", "submission", "transcript", "config",
                     "harness_command")

#: The subset of `REQUIRED_ROW_KEYS` that must be a **non-empty** string.
#: `harness` is an int and `config` an object, so both are checked by type.
_REQUIRED_STRINGS = ("agent", "model", "agentcad", "task_set", "date",
                     "submission", "transcript", "harness_command")

#: The keys design §10 puts in a report document.
_REPORT_KEYS = ("schema", "task_set", "harness", "agentcad", "agent", "model",
                "n", "total", "categories", "tasks", "warnings")

#: The two row fields rule 4 governs.
_LINK_KEYS = ("submission", "transcript")

#: The scheme a leaderboard link may carry. Plain `http://` is refused with
#: everything else: a benchmark whose evidence is fetched over a channel anyone
#: can rewrite is not evidence.
_URL_PREFIX = "https://"


# ------------------------------------------------------------- small tests

def _is_int(value) -> bool:
    """A real int. `True` is an `int` in Python and is not one here."""
    return isinstance(value, int) and not isinstance(value, bool)


def _finite(value) -> bool:
    """A real, finite number -- `report._finite`'s rule, restated.

    `json.loads` parses the bare `NaN` / `Infinity` literals, so a plain
    `isinstance` test would let a non-finite total into the sort key, where
    every comparison against it is false and the order stops being stable.
    """
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def _nonempty_str(value) -> bool:
    return isinstance(value, str) and value.strip() != ""


# ------------------------------------------------------------- the rules

def _link_problem(key: str, value, base: Path) -> str | None:
    """Rule 4 over one link, or `None` if it holds.

    An `https://` URL is taken as stated -- this module resolves nothing over
    the network, ever. Anything else is read as a path **relative to the row's
    own directory**, must stay inside it, and must exist. That containment
    check is what stops a row publishing `../../../etc/passwd` as its
    "submission", and it is why an absolute path is refused outright rather
    than resolved.
    """
    if not isinstance(value, str) or value.strip() == "":
        return (f"{key} is empty; a leaderboard row must name the artefact "
                f"that reproduces it")
    if value.startswith(_URL_PREFIX):
        return None
    if "://" in value or value.startswith("/") or ":" in value.split("/")[0]:
        return (f"{key} {value!r} is neither an absolute https:// URL nor a "
                f"path inside the row's directory")
    parts = PurePosixPath(value).parts
    if not parts or ".." in parts:
        return (f"{key} {value!r} leaves the row's directory; a relative link "
                f"must stay inside it")
    target = base / Path(*parts)
    if not target.exists():
        return (f"{key} {value!r} does not exist beside row.json; a link that "
                f"cannot be fetched is not a disclosure")
    return None


def _report_problems(report: dict) -> list[str]:
    """Rule 2 -- the report document design §10 specifies, checked by shape."""
    out: list[str] = []
    missing = [key for key in _REPORT_KEYS if key not in report]
    if missing:
        out.append("report.json is missing " + ", ".join(sorted(missing)))
        return out
    if report["schema"] != REPORT_SCHEMA:
        out.append(f"report.json declares schema {report['schema']!r}, this "
                   f"harness publishes schema {REPORT_SCHEMA}")
    for key in ("task_set", "agentcad", "agent", "model"):
        if not _nonempty_str(report[key]):
            out.append(f"report.json's {key} must be a non-empty string")
    for key in ("harness", "n"):
        if not _is_int(report[key]):
            out.append(f"report.json's {key} must be an integer")
    if not _finite(report["total"]):
        out.append("report.json's total must be a finite number")
    if not isinstance(report["categories"], dict):
        out.append("report.json's categories must be an object")
    else:
        for name, row in sorted(report["categories"].items()):
            if not isinstance(row, dict) or not _finite(row.get("total")):
                out.append(f"report.json's category {name} has no finite total")
    if not isinstance(report["tasks"], dict):
        out.append("report.json's tasks must be an object")
    else:
        for name, row in sorted(report["tasks"].items()):
            if not isinstance(row, dict) or not _finite(row.get("total")):
                out.append(f"report.json's task {name} has no finite total")
    if not isinstance(report["warnings"], list):
        out.append("report.json's warnings must be a list")
    return out


def _coverage_problems(report: dict, expected_tasks) -> list[str]:
    """Rule 5 -- every declared task measured, and measured for real.

    A task the report flags `missing: true` is a partial run exactly as much as
    a task whose key is absent: `report.aggregate` writes that flag for a task
    that was in the roster and never scored. Accepting it would let a row buy a
    leaderboard place by not running the hard half -- the same hole
    `report.compare_baseline`'s coverage regression closes for the release
    gate, closed here for the public page.
    """
    tasks = report.get("tasks")
    if not isinstance(tasks, dict):
        return []
    out = []
    for task_id in sorted(set(expected_tasks or ())):
        row = tasks.get(task_id)
        if row is None:
            out.append(f"report.json does not cover {task_id}; a partial run "
                       f"is not a leaderboard row")
        elif isinstance(row, dict) and row.get("missing"):
            out.append(f"report.json flags {task_id} missing; a partial run "
                       f"is not a leaderboard row")
    return out


def row_problems(row: dict, report: dict, base: Path,
                 expected_tasks: list[str]) -> list[str]:
    """Every reason *row* may not be published, in a stable order.

    Empty means publishable. Each sentence names the failing key so the
    submitter can act on it without reading this module -- that is the whole
    point of a mechanical rule: the refusal is a diff against a checklist, not
    a judgement.

    *base* is the row's own directory (rule 4 resolves relative links against
    it); *expected_tasks* is the roster the declared task set requires.
    """
    out: list[str] = []
    if not isinstance(row, dict):
        return ["row.json must hold a JSON object"]

    if row.get("schema") != ROW_SCHEMA:
        out.append(f"row.json declares schema {row.get('schema')!r}, this "
                   f"harness publishes schema {ROW_SCHEMA}")

    # Rule 1 -- presence and non-emptiness.
    for key in REQUIRED_ROW_KEYS:
        if key not in row:
            out.append(f"{key} is missing; the full-disclosure rule is "
                       f"fail-closed and has no override")
    for key in _REQUIRED_STRINGS:
        if key in row and not _nonempty_str(row[key]):
            out.append(f"{key} must be a non-empty string")
    if "harness" in row and not _is_int(row["harness"]):
        out.append("harness must be an integer")
    if "config" in row and not isinstance(row["config"], dict):
        out.append("config must be an object; state {} for 'nothing tuned', "
                   "never omit it")
    if "notes" in row and not isinstance(row["notes"], str):
        out.append("notes must be a string")
    if "date" in row and _nonempty_str(row["date"]):
        try:
            datetime.date.fromisoformat(row["date"])
        except ValueError:
            out.append(f"date {row['date']!r} is not an ISO YYYY-MM-DD date")

    # Rule 2 -- the report document.
    report_problems = _report_problems(report if isinstance(report, dict)
                                       else {})
    out += report_problems

    # Rule 3 -- the row and the report describe the same run. Skipped when the
    # report is malformed: comparing against a field we already refused would
    # bury the real reason under a mismatch it caused.
    if not report_problems:
        for key in ("task_set", "harness", "agentcad"):
            if key in row and row[key] != report.get(key):
                out.append(f"{key} {row[key]!r} does not equal report.json's "
                           f"{report.get(key)!r}; the row and the report must "
                           f"describe the same run")

    # Rule 4 -- the links.
    for key in _LINK_KEYS:
        if key in row:
            problem = _link_problem(key, row[key], base)
            if problem:
                out.append(problem)

    # Rule 5 -- coverage.
    if not report_problems:
        out += _coverage_problems(report, expected_tasks)
    return out


# ------------------------------------------------------------- loading

def load_rows(leaderboard_dir: Path,
              expected_tasks: list[str]) -> tuple[list[dict], list[str]]:
    """`(publishable rows, problems)` for the board at *leaderboard_dir*.

    Never raises for a bad row -- a rejection is a *problem string* the caller
    turns into exit 1. It raises `ValidationError` only for the input the
    caller named: a leaderboard directory that is not a directory.

    A returned row is the `row.json` document with its `id` forced to the
    directory name and its report attached under `report`; that is the shape
    `render_leaderboard` consumes.
    """
    base = Path(leaderboard_dir)
    rows_dir = base / "rows"
    if not base.is_dir():
        raise ValidationError(
            f"{base} is not a leaderboard directory",
            {"path": str(base)})
    if not rows_dir.is_dir():
        raise ValidationError(
            f"{base} has no rows/ directory; a leaderboard is "
            f"rows/<row-id>/row.json + report.json",
            {"path": str(rows_dir)})

    rows: list[dict] = []
    problems: list[str] = []
    for row_dir in sorted(path for path in rows_dir.iterdir()
                          if path.is_dir()):
        row_id = row_dir.name
        found: list[str] = []
        row: dict = {}
        report: dict = {}
        for name, sink in (("row.json", "row"), ("report.json", "report")):
            path = row_dir / name
            if not path.is_file():
                found.append(f"{name} is missing")
                continue
            try:
                doc = read_json(path)
            except ValidationError as exc:
                found.append(f"{name} is unreadable: {exc.message}")
                continue
            if sink == "row":
                row = doc
            else:
                report = doc
        if not found:
            found = row_problems(row, report, row_dir, expected_tasks)
            declared = row.get("id")
            if declared is not None and declared != row_id:
                found.append(f"id {declared!r} does not equal the row's "
                             f"directory name {row_id!r}")
        if found:
            problems += [f"{row_id}: {problem}" for problem in found]
            continue
        rows.append({**row, "id": row_id, "report": report})
    return rows, problems


# ------------------------------------------------------------- rendering

_STYLE = """\
:root { color-scheme: light dark; }
* { box-sizing: border-box; }
body { margin: 0 auto; padding: 2rem 1.25rem 4rem; max-width: 72rem;
       font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
       line-height: 1.55; color: #16181d; background: #fbfbfc; }
h1 { font-size: 1.85rem; margin: 0 0 .25rem; letter-spacing: -.01em; }
h2 { font-size: 1.1rem; margin: 2.5rem 0 .5rem; }
p { margin: .6rem 0; max-width: 62rem; }
.lede { color: #3f4650; }
.wrap { overflow-x: auto; margin: 1rem 0; }
table { border-collapse: collapse; width: 100%; font-size: .9rem; }
th, td { padding: .45rem .6rem; text-align: left; white-space: nowrap;
         border-bottom: 1px solid #dfe2e7; }
th { font-weight: 600; background: #f0f1f4; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
td.total { font-weight: 700; }
tbody tr:hover { background: #f5f6f8; }
code, pre { font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
            font-size: .85em; }
pre { overflow-x: auto; background: #f0f1f4; padding: .6rem .8rem;
      border-radius: 4px; }
dl.row { margin: 0 0 1.25rem; }
dt { font-weight: 600; margin-top: .5rem; }
dd { margin: 0 0 0 1rem; }
footer { margin-top: 3rem; padding-top: 1rem; border-top: 1px solid #dfe2e7;
         color: #3f4650; font-size: .9rem; }
a { color: #1b4fd8; }
@media (prefers-color-scheme: dark) {
  body { color: #e6e8ec; background: #16181d; }
  .lede, footer { color: #b3b9c4; }
  th, td { border-bottom-color: #333842; }
  th, pre { background: #21242b; }
  tbody tr:hover { background: #1d2027; }
  a { color: #8ab0ff; }
}
"""

#: What the number on this page is, and -- just as load-bearing -- what it is
#: not. Kept beside the renderer so a change to the scorer has to walk past it.
_WHAT = (
    "Every number here is measured by a geometry kernel. A run is scored on "
    "six mechanical subscores: whether the part <strong>builds</strong>, "
    "whether every solid is <strong>valid</strong> B-rep, whether the "
    "task&rsquo;s <strong>specs</strong> (PRD-003 assertions) pass, "
    "<strong>geometry</strong> as volumetric IoU against a reference solid "
    "checked into this repository, assembly <strong>interference</strong>, and "
    "<strong>metric windows</strong> (volume, area, mass, counts) declared by "
    "the task."
)

_NOT = (
    "What is <em>not</em> measured: there is <strong>no LLM judging anywhere "
    "in this benchmark</strong>, and <strong>no human panel</strong>. Nothing "
    "on this page is a preference score, an aesthetic rating or a vote. A "
    "candidate that is absent, broken, mesh-only or wrong measures zero &mdash; "
    "the harness never rewards destroying the evidence."
)

_RULE = (
    "A row is published only if it discloses all of: agent, model, AgentCAD "
    "version, harness version, task set, date, the exact command, the run "
    "configuration, and downloadable submission and transcript archives &mdash; "
    "and only if its report covers every task in the set. A row that does not "
    "is rejected outright; it is never dimmed, footnoted or flagged. There is "
    "no override."
)


def _num(value) -> str:
    """A total, fixed at four decimals -- the width `report` prints."""
    return f"{float(value):.4f}" if _finite(value) else "&mdash;"


def _cell(value) -> str:
    return _escape(str(value), quote=True)


def _link(value: str) -> str:
    """An `https://` link becomes an anchor; a relative path stays text.

    The anchor is safe because rule 4 already refused every scheme but
    `https://` -- and a relative path is rendered as text on purpose: it points
    inside the leaderboard directory, not inside wherever this page was
    written, so an anchor would be a link that lies.
    """
    safe = _escape(value, quote=True)
    if value.startswith(_URL_PREFIX):
        return f'<a href="{safe}" rel="noreferrer noopener">{safe}</a>'
    return f"<code>{safe}</code>"


def _report_of(row: dict) -> dict:
    """The report attached to a board row, tolerating a row without one.

    `render_leaderboard` is a public entry point and may be handed a row this
    module did not validate; answering `{}` renders an em-dash rather than
    raising an `AttributeError` out of a sort key.
    """
    report = row.get("report")
    return report if isinstance(report, dict) else {}


def _sort_key(row: dict):
    """Total descending, id ascending -- a total order over the whole board.

    Ties broken by id, so two agents that measure the same do not swap places
    between two publishes of identical input. A non-finite total sorts last
    rather than raising: `row_problems` already refused it, and a renderer
    called directly should still terminate.
    """
    total = _report_of(row).get("total")
    return (0 if _finite(total) else 1,
            -float(total) if _finite(total) else 0.0, str(row.get("id", "")))


def _category_columns(rows: list[dict]) -> list[str]:
    """Every category any row measured, in the canonical order.

    `tasks.CATEGORIES` first (so the columns read the same as the task tree),
    then anything else sorted -- a v2 category shows up rather than vanishing.
    """
    seen: set = set()
    for row in rows:
        seen |= set(_report_of(row).get("categories") or {})
    known = [name for name in CATEGORIES if name in seen]
    return known + sorted(seen - set(known))


def render_leaderboard(rows: list[dict], *, title: str) -> str:
    """The whole page, as one self-contained HTML string.

    *rows* is what `load_rows` returns. Every row-sourced string is HTML-escaped
    on the way in: an agent name and a model name are submitter-controlled text,
    and this page is served to the public.

    Nothing in the output depends on the clock, on the filesystem, or on
    iteration order, so the same input renders the same bytes.
    """
    ordered = sorted(rows, key=_sort_key)
    categories = _category_columns(ordered)
    # (label, right-aligned?) -- the numeric columns are the rank, the five
    # category means and the total.
    head = ([("#", True), ("Row", False), ("Agent", False), ("Model", False),
             ("AgentCAD", False), ("Harness", True), ("Task set", False),
             ("Date", False)]
            + [(name.replace("_", " "), True) for name in categories]
            + [("Total", True), ("Submission", False), ("Transcript", False)])

    lines = [
        "<!doctype html>",
        '<html lang="en">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>{_cell(title)}</title>",
        "<style>",
        _STYLE.rstrip("\n"),
        "</style>",
        "</head>",
        "<body>",
        f"<h1>{_cell(title)}</h1>",
        f'<p class="lede">{_WHAT}</p>',
        f'<p class="lede">{_NOT}</p>',
        f'<p class="lede">{_RULE}</p>',
        '<div class="wrap">',
        "<table>",
        "<thead>",
        "<tr>" + "".join(
            f'<th class="num">{_cell(label)}</th>' if numeric
            else f"<th>{_cell(label)}</th>" for label, numeric in head)
        + "</tr>",
        "</thead>",
        "<tbody>",
    ]

    for rank, row in enumerate(ordered, start=1):
        report = _report_of(row)
        cats = report.get("categories") or {}
        cells = [
            f'<td class="num">{rank}</td>',
            f"<td><code>{_cell(row.get('id', ''))}</code></td>",
            f"<td>{_cell(row.get('agent', ''))}</td>",
            f"<td>{_cell(row.get('model', ''))}</td>",
            f"<td>{_cell(row.get('agentcad', ''))}</td>",
            f'<td class="num">{_cell(row.get("harness", ""))}</td>',
            f"<td>{_cell(row.get('task_set', ''))}</td>",
            f"<td>{_cell(row.get('date', ''))}</td>",
        ]
        for name in categories:
            entry = cats.get(name)
            value = entry.get("total") if isinstance(entry, dict) else None
            cells.append(f'<td class="num">{_num(value)}</td>')
        cells += [
            f'<td class="num total">{_num(report.get("total"))}</td>',
            f"<td>{_link(str(row.get('submission', '')))}</td>",
            f"<td>{_link(str(row.get('transcript', '')))}</td>",
        ]
        lines.append("<tr>" + "".join(cells) + "</tr>")

    if not ordered:
        span = len(head)
        lines.append(f'<tr><td colspan="{span}">No row has been submitted '
                     f"yet.</td></tr>")

    lines += ["</tbody>", "</table>", "</div>"]

    if ordered:
        lines.append("<h2>Disclosure</h2>")
        lines.append("<p>Every row states the command it was produced by and "
                     "the configuration it ran under. Both are part of the "
                     "row, not of this page.</p>")
        for row in ordered:
            config = canonical_json(row.get("config") or {}).decode().rstrip()
            lines += [
                '<dl class="row">',
                f"<dt><code>{_cell(row.get('id', ''))}</code></dt>",
                f"<dd>Command: <code>"
                f"{_cell(row.get('harness_command', ''))}</code></dd>",
                "<dd>Configuration:</dd>",
                f"<dd><pre>{_cell(config)}</pre></dd>",
            ]
            notes = row.get("notes") or ""
            if notes:
                lines.append(f"<dd>Notes: {_cell(notes)}</dd>")
            lines.append("</dl>")

    lines += [
        "<footer>",
        "<p>Any row on this page reproduces from its own submission archive: "
        "<code>agentcad bench score &lt;submission&gt; --task &lt;id&gt;</code>"
        ", one task at a time, against the tasks checked into this "
        "repository. The whole board rebuilds with "
        "<code>agentcad bench publish</code>, which refuses to write anything "
        "at all while a single row is under-disclosed.</p>",
        "</footer>",
        "</body>",
        "</html>",
        "",
    ]
    return "\n".join(lines)


# ------------------------------------------------------------- the command

def publish(leaderboard_dir: Path, out_path: Path, *, title: str,
            expected_tasks: list[str]) -> dict:
    """Validate every row, render the page, write it -- in that order.

    Returns `{"path", "rows", "categories"}`. Raises `ValidationError` naming
    every problem of every rejected row, **before** a byte is written: the
    all-or-nothing property is what makes the disclosure rule mean something,
    because a half-written board is a board that published the good rows and
    quietly dropped the rest.

    The write goes through `ProjectStore._atomic_write` (staged under a random
    name, then `os.replace`), so a concurrent publisher loses rather than
    corrupts.
    """
    rows, problems = load_rows(leaderboard_dir, expected_tasks)
    if problems:
        raise ValidationError(
            "the leaderboard was not written; "
            f"{len(problems)} disclosure problem"
            f"{'' if len(problems) == 1 else 's'}:\n  "
            + "\n  ".join(problems),
            {"problems": problems, "path": str(leaderboard_dir)})
    page = render_leaderboard(rows, title=title)
    target = Path(out_path)
    try:
        ProjectStore._atomic_write(target, page.encode("utf-8"))
    except OSError as exc:
        raise ValidationError(f"cannot write {target}: {exc}",
                              {"path": str(target)}) from exc
    return {"path": str(target), "rows": len(rows),
            "categories": _category_columns(rows)}
