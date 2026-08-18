"""Kernel resource metering: the usage scope and the service's roll-up meter.

Since PRD-006 every worker response carries what it cost — CPU ms, wall ms and
a per-request peak RSS — and :class:`~agentcad.kernel.client.KernelClient`
hands each record to an ``on_usage`` hook. This module is what the server does
with them (design Decision 6): a **scope** that says which project a kernel
call belongs to, and a **meter** that rolls the records up per project and per
client identity for ``/api/health`` and the ``get_usage`` tool.

Two things this module is deliberately not:

* **It is not a quota.** Nothing here refuses anything. The caps that bite are
  the kernel's (memory, pids, CPU) and the store's disk budget; this only
  *answers the question* "what has been spent, and by whom" — which is the
  question an operator has when one tenant's box is slow, and the one FR11
  publishes.
* **It is not on the kernel's critical path.** ``record`` takes a plain lock
  around a dict update and returns; it never touches the pool, never blocks a
  worker, and never raises for a shape it did not expect. A metering bug that
  turned a green build red would be a far worse defect than a missing row.

The scope is a :class:`contextvars.ContextVar`, exactly like
``locks.client_id_var``, and is set in three additive places: the HTTP
middleware (from the request path), ``ToolRegistry.call`` (from the tool's
``project`` argument) and the service's own build/export/interference paths
(authoritative — they know the project for certain). ContextVars reach sync
endpoints through anyio's context copy, which is the trick ``client_id_var``
has relied on since PRD-008.

OCP-free and import-cheap: this module runs in the server process.
"""

from __future__ import annotations

import contextvars
import re
import threading
import time
from collections import deque
from contextlib import contextmanager
from urllib.parse import unquote

from . import locks

#: The project a kernel call is being made *for*, or ``None`` for work that
#: belongs to no project (a ``ping``, an ``inspect`` of a script that has not
#: been saved yet, a package gate's throwaway cell).
scope_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "agentcad_usage_scope", default=None)

#: ``/api/projects/{project}/...``. Anchored, and one segment only.
_PROJECT_PATH = re.compile(r"^/api/projects/([^/]+)")

#: Path segments in the project position that are *routes*, not project ids.
#: ``/api/projects/open`` registers a directory as a project; a usage row named
#: ``open`` would be a lie, and a made-up project id is worse than none.
_NOT_A_PROJECT = frozenset({"open"})

#: What a roll-up counts. ``peak_rss_mb`` is a maximum, everything else a sum.
_BLANK = {"requests": 0, "errors": 0, "cpu_ms": 0.0, "wall_ms": 0.0,
          "peak_rss_mb": None, "last_at": None}


@contextmanager
def scoped(project: str | None):
    """Bill kernel calls made in this block to *project*.

    A token reset, not an assignment: a nested scope (a check runner's
    ephemeral service inside a tool call) must restore the caller's, or one
    build's cost lands on the next.
    """
    token = scope_var.set(project)
    try:
        yield
    finally:
        scope_var.reset(token)


def project_from_path(path: str | None) -> str | None:
    """The project id an HTTP path names, or ``None``.

    URL-decoded, because a project id may be percent-encoded in the path and a
    row called ``a%20b`` would not join with the one the service reports.
    """
    match = _PROJECT_PATH.match(path or "")
    if match is None:
        return None
    project = unquote(match.group(1))
    if not project or project in _NOT_A_PROJECT:
        return None
    return project


def _number(value) -> float | None:
    """*value* as a float, or ``None`` for anything that is not a number.

    ``None`` is a real answer from the client: a request that never came back
    (a kill, a timeout) reports ``cpu_ms: None`` because the worker's own meter
    died with it, and "not measurable from here" is not "no CPU was spent". A
    meter that summed those as zeros would under-bill exactly the requests that
    cost the most, so they are skipped rather than counted.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _accumulate(row: dict, record: dict) -> None:
    row["requests"] += 1
    if not record["ok"]:
        row["errors"] += 1
    for field in ("cpu_ms", "wall_ms"):
        value = record[field]
        if value is not None:
            row[field] += value
    peak = record["peak_rss_mb"]
    if peak is not None and (row["peak_rss_mb"] is None
                             or peak > row["peak_rss_mb"]):
        row["peak_rss_mb"] = peak
    if row["last_at"] is None or record["at"] > row["last_at"]:
        row["last_at"] = record["at"]


def _rounded(row: dict) -> dict:
    return {"requests": row["requests"], "errors": row["errors"],
            "cpu_ms": round(row["cpu_ms"], 1),
            "wall_ms": round(row["wall_ms"], 1),
            "peak_rss_mb": row["peak_rss_mb"],
            "last_at": row["last_at"]}


class UsageMeter:
    """Thread-safe roll-ups of kernel usage, keyed by ``(project, identity)``.

    *keep_recent* bounds a ring of the individual records, which is what makes
    ``since`` answerable at all. It is deliberately small: this is an
    operator's dashboard, not an audit log (that is PRD-005's, and the docs say
    so). A ``since`` older than the ring is answered with what is retained
    **and a warning that says the window is short** — quietly under-reporting
    would be the dishonest option.
    """

    def __init__(self, keep_recent: int = 2000):
        self._lock = threading.Lock()
        self._rows: dict[tuple[str | None, str], dict] = {}
        self._recent: deque[dict] = deque(maxlen=keep_recent)

    # ------------------------------------------------------------ recording

    def record(self, event) -> None:
        """Take one ``on_usage`` payload. Never raises, never blocks.

        The project comes from :data:`scope_var` and the identity from
        ``locks.current_client_id()`` — both read *here*, in the calling
        thread's context, because the kernel client emits the record inside the
        request that made it.
        """
        if not isinstance(event, dict):
            return
        usage = event.get("usage")
        if not isinstance(usage, dict):
            usage = {}
        try:
            identity = locks.current_client_id()
        except Exception:  # noqa: BLE001 — an unset ContextVar must not lose a row
            identity = "local"
        record = {
            "at": time.time(),
            "project": scope_var.get(),
            "identity": identity or "local",
            "method": event.get("method"),
            "worker": event.get("worker"),
            "ok": bool(event.get("ok", True)),
            "cpu_ms": _number(usage.get("cpu_ms")),
            "wall_ms": _number(usage.get("wall_ms")),
            "peak_rss_mb": _number(usage.get("peak_rss_mb")),
        }
        key = (record["project"], record["identity"])
        with self._lock:
            self._recent.append(record)
            row = self._rows.get(key)
            if row is None:
                row = self._rows[key] = dict(_BLANK)
            _accumulate(row, record)

    # --------------------------------------------------------- aggregation

    def _cells(self, since: float | None, project: str | None):
        """``([(project, identity, counters)], oldest_at)`` under the filters.

        With no ``since`` the answer comes from the roll-ups, which are
        complete for the life of the process. With one it is recomputed from
        the retained ring, which is the only thing that remembers *when*.
        """
        with self._lock:
            if since is None:
                cells = [(key[0], key[1], dict(row))
                         for key, row in self._rows.items()]
            else:
                accumulated: dict[tuple, dict] = {}
                for record in self._recent:
                    if record["at"] < since:
                        continue
                    key = (record["project"], record["identity"])
                    row = accumulated.get(key)
                    if row is None:
                        row = accumulated[key] = dict(_BLANK)
                    _accumulate(row, record)
                cells = [(key[0], key[1], row)
                         for key, row in accumulated.items()]
            oldest = self._recent[0]["at"] if self._recent else None
            kept, capacity = len(self._recent), self._recent.maxlen
        if project is not None:
            cells = [cell for cell in cells if cell[0] == project]
        return cells, {"kept": kept, "capacity": capacity, "oldest_at": oldest}

    @staticmethod
    def _rank(cells, index: int, name: str, top: int) -> list[dict]:
        merged: dict = {}
        for cell in cells:
            row = merged.get(cell[index])
            if row is None:
                row = merged[cell[index]] = dict(_BLANK)
            for field in ("requests", "errors", "cpu_ms", "wall_ms"):
                row[field] += cell[2][field]
            peak = cell[2]["peak_rss_mb"]
            if peak is not None and (row["peak_rss_mb"] is None
                                     or peak > row["peak_rss_mb"]):
                row["peak_rss_mb"] = peak
            last = cell[2]["last_at"]
            if last is not None and (row["last_at"] is None
                                     or last > row["last_at"]):
                row["last_at"] = last
        ranked = sorted(merged.items(),
                        key=lambda item: (item[1]["cpu_ms"],
                                          item[1]["requests"]),
                        reverse=True)
        return [{name: value, **_rounded(row)} for value, row in ranked[:top]]

    @staticmethod
    def _totals(cells) -> dict:
        total = dict(_BLANK)
        for cell in cells:
            for field in ("requests", "errors", "cpu_ms", "wall_ms"):
                total[field] += cell[2][field]
            peak = cell[2]["peak_rss_mb"]
            if peak is not None and (total["peak_rss_mb"] is None
                                     or peak > total["peak_rss_mb"]):
                total["peak_rss_mb"] = peak
            last = cell[2]["last_at"]
            if last is not None and (total["last_at"] is None
                                     or last > total["last_at"]):
                total["last_at"] = last
        return {**_rounded(total),
                "projects": len({cell[0] for cell in cells}),
                "identities": len({cell[1] for cell in cells})}

    # ------------------------------------------------------------- the views

    def totals(self) -> dict:
        """Everything this process has spent in the kernel."""
        return self._totals(self._cells(None, None)[0])

    def by_project(self, since: float | None = None,
                   top: int = 20) -> list[dict]:
        """The costliest projects first. ``project`` is ``None`` for kernel
        work that belongs to no project."""
        return self._rank(self._cells(since, None)[0], 0, "project", top)

    def by_identity(self, since: float | None = None,
                    top: int = 20) -> list[dict]:
        """The costliest client identities first (``browser``, ``chat``, an
        agent's ``X-Agent-Id``, ``ci``, ``local``)."""
        return self._rank(self._cells(since, None)[0], 1, "identity", top)

    def health(self) -> dict:
        """The ``usage`` object in ``/api/health``'s authenticated body."""
        return {"totals": self.totals(), "projects": self.by_project()}

    def snapshot(self, project: str | None = None,
                 since: float | None = None) -> dict:
        """The ``get_usage`` payload: totals, projects and identities."""
        cells, window = self._cells(since, project)
        warnings = []
        if since is not None and window["oldest_at"] is not None \
                and since < window["oldest_at"]:
            warnings.append(
                f"only the last {window['kept']} kernel requests are retained, "
                f"so this answer starts at {window['oldest_at']:.0f} rather "
                f"than the requested since={since:.0f}")
        return {
            "project": project,
            "since": since,
            "totals": self._totals(cells),
            "projects": self._rank(cells, 0, "project", 20),
            "identities": self._rank(cells, 1, "identity", 20),
            "window": window,
            "warnings": warnings,
        }
