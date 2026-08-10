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
  raised (the ``metrics.json`` precedent).
* **The report degrades, it never raises.** A failing check, a broken
  predicate, an unknown instance id, a ``specs.py`` that will not execute and a
  ``KernelError`` mid-measurement are all *payload*. The only things that raise
  are the ordinary argument errors: ``NotFoundError`` for an unknown
  project/part/branch, ``ValidationError`` for a ref that is not a branch or a
  ref on a project with no git.
* **``service.branches`` is read inside the methods, never in ``__init__``** —
  the tool pack that constructs the runner sorts before the versioning pack, so
  the seam does not exist yet at construction time.
"""

from __future__ import annotations

import ast
import hashlib
import json
import time
from contextlib import contextmanager
from pathlib import Path

from ..kernel.client import KernelError
from .model import NotFoundError, ValidationError
from .project import ProjectStore
from .tools_stackup import compute_stackup

#: Sidecar format version. A stored document at any other version is discarded.
SPEC_RESULT_VERSION = 1

#: Wall-clock budget for the proposal gate (Slice 5 applies it).
GATE_BUDGET_S = 30.0

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


def declares_specs(script: str) -> bool:
    """True iff *script* binds a top-level name ``SPECS``.

    AST, never exec: the kernel worker is the only thing in this system that
    runs a part script, and a *presence* question must not become a reason to
    execute one (the rule ``packet.params_spec`` already follows for
    ``PARAMS``). A script that does not parse is ``False`` — it fails its build
    with a line number anyway.
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
        answer = False
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
    plus the ``part`` the service (and only the service) knows."""
    return {"index": index, "name": declaration["name"],
            "kind": declaration["kind"], "scope": declaration["scope"],
            "part": part, "requirement": declaration.get("requirement"),
            "limit": declaration.get("limit") or {},
            "unit": _UNITS.get(declaration["kind"]), "measured": None,
            "location": None, "message": "", "details": {}}


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

    # ------------------------------------------------------------ paths

    def specs_path(self, proj: str) -> Path:
        """The project's ``specs.py``. ``path_of``, never ``canonical_path_of``:
        specs are authored state and ride the caller's branch."""
        return self.service.store.path_of(proj) / "specs.py"

    def project_script(self, proj: str) -> str | None:
        """The ``specs.py`` text, or None. Discovery is presence (``is_file``),
        not convention: a second root-level module is not a spec file."""
        path = self.specs_path(proj)
        try:
            return path.read_text(encoding="utf-8") if path.is_file() else None
        except OSError:
            return None

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
        not a missing resource.
        """
        self.service.store.manifest(proj)          # NotFoundError: bad project
        return self._project_state(proj, self.project_script(proj))

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
                pass
            state = self._project_state(proj, None)
        self.service.bus.publish(
            {"type": "project_changed", "project": proj, "reason": "specs"})
        return state

    # ----------------------------------------------------- declarations

    def _declare(self, script: str, scope: str, affinity: str) -> dict:
        """``spec_declare``, memoized by ``sha256(scope, script)``.

        The handler never builds, so this is safe on a part that does not
        build at all (AC6) — and cheap enough to call per report.
        """
        key = hashlib.sha256(f"{scope}\0{script}".encode()).hexdigest()
        hit = self._declaration_cache.get(key)
        if hit is None:
            hit = self.service.kernel.request(
                "spec_declare", {"script": script, "scope": scope},
                timeout_s=300.0, affinity=affinity)
            if len(self._declaration_cache) > 256:
                self._declaration_cache.clear()
            self._declaration_cache[key] = hit
        return hit

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
            script = self.project_script(proj)
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
                    cache_key: str | None = None):
        """``(payload, cached, sidecar_path)`` for one part's shape tier.

        ``(None, False, None)`` means the part declares nothing — which is not
        the same as "not evaluated" and must never be reported as green.
        """
        store = self.service.store
        record = store.get_part(proj, part_id)
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

        result = self.service.kernel.request(
            "spec_eval",
            {"script": script, "params": record.params,
             "density_g_cm3": self.service.material_density(
                 proj, record.material),
             "densities": self.service._solid_densities(proj, record) or None,
             # null = every declaration, so nothing is dropped: the tiers this
             # call cannot measure come back as named skips.
             "indices": None},
            timeout_s=300.0, affinity=part_id)
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
                 warnings: list[str]) -> dict:
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
            declared = self._declare(script, "part", part_id)["declared"]
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

    def _eval_fem(self, proj: str, part_id: str, declaration: dict) -> dict:
        """``check_fem_static``: one 600 s request, or an honest skip (FR8)."""
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
                "params": record.params,
                "fixed_face": options.get("fixed_face"),
                "load_face": options.get("load_face"),
                "load_N": options.get("load_N"),
                "load_dir": [0, 0, -1]}
        modulus = self._youngs_mpa(proj, record.material)
        if modulus is not None:
            args["E_mpa"] = modulus      # otherwise the kernel's steel default
        try:
            result = self.service.kernel.request(
                "fem_static", args, timeout_s=600.0, affinity=part_id)
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

    def _youngs_mpa(self, proj: str, material_id: str) -> float | None:
        """The part material's Young's modulus, when the catalog has one —
        ``fem_modal``'s convention, minus its hard error: a spec that cannot
        find E should still measure, with the solver's own default."""
        try:
            resolve = getattr(self.service.materials, "resolve", None)
            if callable(resolve):
                material = resolve(proj, material_id)
            else:
                from .materials import get_material

                material = get_material(material_id)
            return None if material.E_gpa is None else material.E_gpa * 1000.0
        except Exception:  # noqa: BLE001 — a missing material is not this
            return None    #                check's error to raise

    # -------------------------------------------------------- tier 2

    def _project_key(self, proj: str, script: str) -> str:
        """Content key for the assembly tier: the specs text plus every placed
        instance's identity, part cache key and resolved transform. Moving one
        instance changes every clearance, so the whole placement is the key."""
        rows = []
        for instance in self.service._resolved_instances(proj):
            try:
                record = self.service.store.get_part(proj, instance.part)
                part_key = self.service._cache_key_for(proj, record)
            except (NotFoundError, OSError):
                part_key = "missing"
            rows.append([instance.id, instance.part, part_key,
                         [float(v) for v in instance.position],
                         [float(v) for v in instance.rotation_deg]])
        payload = json.dumps({"specs": script, "instances": sorted(rows),
                              "version": SPEC_RESULT_VERSION}, sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()[:32]

    def _instance_item(self, proj: str, instance) -> dict:
        record = self.service.store.get_part(proj, instance.part)
        item = self.service._shape_item(proj, record, instance)
        item["name"] = instance.id
        return item

    def _eval_interference(self, proj: str, declaration: dict) -> dict:
        minimum = (declaration.get("limit") or {}).get("min_volume_mm3", 0.001)
        try:
            result = self.service.check_interference(proj, min_volume=minimum)
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
            return {"status": "fail", "measured": measured, "details": details,
                    "message": f"{len(pairs)} interfering pair(s): {named}"}
        return {"status": "pass", "measured": measured, "details": details,
                "message": f"no two of {result['checked']} instances overlap"}

    def _eval_clearance(self, proj: str, declaration: dict) -> dict:
        options = declaration.get("options") or {}
        minimum = (declaration.get("limit") or {}).get("min_mm")
        resolved = {i.id: i for i in self.service._resolved_instances(proj)}
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
            result = self.service.kernel.request(
                "clearance",
                {"a": items["a"], "b": items["b"], "min_mm": minimum},
                timeout_s=300.0, affinity=proj)
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

    def _eval_stackup(self, proj: str, declaration: dict) -> dict:
        options = declaration.get("options") or {}
        within = (declaration.get("limit") or {}).get("within_mm")
        try:
            # compute_stackup, not registry.call("tolerance_stackup"): the
            # stackup pack sorts after the specs pack, so the tool may not be
            # registered yet — and a check must not depend on that order.
            result = compute_stackup(self.service, proj, options["axis"],
                                     options["from_instance"],
                                     options["to_instance"])
        except (NotFoundError, ValidationError) as exc:
            return _error_row(str(exc), getattr(exc, "details", None) or {})
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

    def _eval_project_check(self, proj: str, declaration: dict) -> dict:
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
            return evaluator(proj, declaration)
        except Exception as exc:  # noqa: BLE001 — the report degrades, never raises
            return _error_row(f"{type(exc).__name__}: {exc}")

    def _project_block(self, proj: str, seen: set, warnings: list[str],
                       errors: list[dict]) -> list[dict]:
        script = self.project_script(proj)
        if script is None:
            return []
        try:
            declared = self._declare(script, "project", proj)
        except KernelError as exc:
            errors.append({"scope": "project", "path": "specs.py",
                           "error": exc.to_payload()})
            return []
        rows = declared.get("declared", [])
        warnings.extend(declared.get("warnings") or [])
        if not rows:
            return []
        records = [_record(row, index, None) for index, row in enumerate(rows)]
        assign_ids(records, "project", seen, warnings)

        try:
            sidecar = (self.service.store.cache_dir(proj)
                       / f"{self._project_key(proj, script)}.projspecs.json")
            stored = _read_sidecar(sidecar) or {}
        except Exception:  # noqa: BLE001 — an unkeyable assembly (a dangling
            sidecar, stored = None, {}   # mate) evaluates uncached, never raises
        cached = stored.get("checks")
        cached = cached if isinstance(cached, dict) else {}
        dirty = False
        for index, (declaration, record) in enumerate(zip(rows, records)):
            row = cached.get(str(index))
            if row is None:
                row = self._eval_project_check(proj, declaration)
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
            ref: str | None = None) -> dict:
        """The full report (FR7, FR12): all three tiers, every scope.

        Unlike a rebuild this evaluates the assembly and expensive tiers too —
        that is the whole difference between the two surfaces.
        """
        with self._pinned(proj, ref):
            return self._report(proj, part_id, ref)

    def _part_block(self, proj: str, part_id: str, seen: set,
                    warnings: list[str], errors: list[dict]) -> dict | None:
        try:
            payload, cached, sidecar = self._shape_tier(proj, part_id)
        except KernelError as exc:
            residue = self._residue(proj, part_id, exc, seen, warnings)
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
        dirty = False
        for record in checks:
            if record.get("kind") not in EXPENSIVE_TIER:
                continue
            index = record.get("index")
            row = fem_cache.get(str(index))
            if row is None:
                declaration = declarations[index] \
                    if isinstance(index, int) and index < len(declarations) else {}
                row = self._eval_fem(proj, part_id, declaration)
                if row["status"] in ("pass", "fail"):
                    fem_cache[str(index)] = row
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
        return {"status": report_status(summary), "summary": summary,
                "checks": checks, "requirements": group_requirements(checks),
                "cached": cached, "warnings": warnings}

    def _report(self, proj: str, part_id: str | None, ref: str | None) -> dict:
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
            block = self._part_block(proj, pid, seen, warnings, errors)
            if block is None:
                continue                    # declares nothing: not in the report
            parts[pid] = {
                "status": block["status"], "summary": block["summary"],
                "cached": block["cached"],
                "checks": [check["id"] for check in block["checks"]]}
            flat.extend(block["checks"])

        # Project scope is the assembly's, so a per-part report never runs it.
        project_checks = [] if part_id is not None else \
            self._project_block(proj, seen, warnings, errors)
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
