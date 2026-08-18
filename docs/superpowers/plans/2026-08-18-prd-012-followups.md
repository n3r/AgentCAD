# PRD-012 follow-ups — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Close the four items PRD-012's whole-branch review ledgered as
"deferred, not blocking" (changelogs 0209 / 0211): a landed write served as a
refusal when an `AppError` fires before the build; the drawing POST answering
refusals as `200 {"error"}` while the GET raises (and both destroying the
kernel error type); the proposal packet's `_summary` (and the browser) ignoring
rebinding-only and mates-only assembly changes; and the AC9 frame-time figures
measured on software GL.

**Architecture (one paragraph):** (1) The build path itself becomes total over
pre-build refusals: `service._rebuild` / `_build_with` convert an `AppError`
raised before the kernel is reached (script file gone, entry gone, unknown
material, resolver refusal) into the same `{ok: False, error}` post-state a
`KernelError` produces — `_status` written when a key exists, `rebuild_failed`
published, a dedicated hint — so all five "landed write + rebuild" call sites
(`set_active_config`, `set_part_configs`, `service.set_params`,
`service.update_part`, `set_solid_materials`) stop leaking a refusal after a
write, and `routes_configs`'s "same answer `PATCH …/params` gives" argument is
true for every failure class. `set_active_config` reads its post-state inside
the lock. The wire type mapping (`NotFoundError → notfound_error`) moves into
`model.error_type()` and is pinned against the registry's. (2) Both drawing
routes go through one `_drawing_result`: `AppError`-class refusals raise via
`_RAISE` (404/422, type intact), kernel-class failures re-raise as
`KernelError` (the app's 502 handler, kernel type intact) — the POST stops
200-ing refusals, the GET stops renaming a worker crash "ValidationError".
(3) `packet._summary` counts distinct touched instance ids (added ∪ removed ∪
moved ∪ mates ∪ configs) and adds `mates_changed`/`configs_changed` counters;
`proposals.py`'s stub grows the same keys; `proposals.js` renders the
`configs_changed` rows it never showed. (4) The AC9 frame-time probe re-run
headed on the real GPU with a vsync-relative criterion — already measured in
this session; the changelog records it.

**Tech stack:** Python 3.12 / uv / pytest; Playwright (system Python,
`channel="chrome"`) for item 4.

**Spec:** PRD-012 design spec Decisions 5–8
(`docs/superpowers/specs/2026-08-17-configurations-design.md`), the review
record `docs/changelog/0209-prd-012-review-fixes.md`, and the critical plan
review `.superpowers/sdd/2026-08-18-prd-012-followups/plan-review.md` (P1–P17;
this v2 folds every Blocking and Should-fix in).

## Global constraints

- Only `agentcad/kernel/` may import OCP/build123d. The kernel never sees a
  configuration.
- `_rebuild(proj, part_id)` keeps its exact signature (pack-wrapped); this plan
  changes what it *returns* on a pre-build `AppError`, not how it is called.
  Nothing enters `_cache_key`'s payload. `_status` stays 2-tuple keyed;
  `_config_status` separate.
- Route packs: an `AppError`-class refusal raises through `_result` (4xx, type
  intact); a kernel-class failure is the app's 502 `KernelError` (type intact);
  a build post-state (has `ok`) is a 200 whatever its `ok`.
- The changelog `docs/changelog/0212-prd-012-followups.md` is written from the
  diff and cites "`make test` — N passed, M skipped" (the newest-entry guard
  in `tests/test_prd012_acceptance.py::test_ac8_the_full_suite_count_is_cited`
  requires the words `make test` and `passed`; the digits guard is on 0208).
- `docs/changelog/0207-configs-ui.md` gets ONE pointer line under Notes
  ("frame time re-measured on GPU — see 0212"); nothing else in it changes.
- No existing test is edited (P15: no test pins the drawing POST's old shape).
- Subagents: no mutating git, no `uv sync`; `uv run pytest`.
- Vocabulary: configuration; the wire type of a `NotFoundError` is
  `notfound_error` (the registry's mapping), never a new spelling.

## Out of scope (deliberately)

`_config_status` growth; the memo write outside `service._lock`; per-part
de-duplication; `""` as `active_config`; `configs: {}` residue in the pure
merge; the bare-string merge warning; the pre-existing lock-free instance RMW
in `tools_mates`/`routes_assembly2`; `get_assembly` raising on a dangling
binding (a contract decision, not a follow-up); the TOCTOU where an `AppError`
fires *after* `rebuild_started` (every current source runs before that
publish); PRD-005a's `GET /api/auth/session` 404 console error in local mode
(observed during item 4 — a 005a follow-up, recorded in the changelog Notes).

---

## Item 1 — the build path is total over pre-build refusals (all five call sites)

> **Superseded at implementation.** The conversion below is described as living
> inside `service._rebuild` / `_build_with`. It does **not**: per the code
> review's Critical (R1), it lives in a new public seam
> `service.rebuild_after_write(proj, part_id)`, used by the five **write**
> sites only, and `_rebuild` / `_build_with` are byte-identical to `main`.
> `_rebuild` is also the READ paths' build (`_ensure_built` ← `get_metrics` /
> `mesh_info` / `ensure_mesh` / `mesh_summary` / `get_assembly`, plus `packet`,
> `checks`, `merge`) and three of those callers re-raise an `ok: false` as a
> `KernelError`, so making it total answered a missing script file with a 502
> on the first call and a 404 on the second, split `get_assembly` by whether an
> instance carried a configuration, and moved `checks._build_item`'s row from
> `error` to `fail`. **The read paths keep raising.** Everything else in this
> item — `error_type`, `_refused_build`, `_REBUILD_REFUSED_HINT`, the five call
> sites, the in-lock post-state read — landed as written. See
> `docs/changelog/0212-prd-012-followups.md` ("Fix round 1").

**Files:** `agentcad/core/model.py` (`error_type(exc) -> str`),
`agentcad/core/service.py` (`_rebuild`, `_build_with`, `_refused_build`),
`agentcad/core/tools_configs.py` (`set_active_config` post-state read moves
inside the lock), `agentcad/server/routes_configs.py` (docstring qualifier),
`docs/agent-api.md` (one sentence), tests: `tests/test_configs.py`,
`tests/test_configs_api.py`, `tests/test_service.py`.

**Shapes:**

```python
# model.py — the registry's wire mapping, in one place both can pin
def error_type(exc: AppError) -> str:
    """`NotFoundError` -> "notfound_error", `ValidationError` -> "validation_error",
    `ConflictError` -> "conflict_error", `RateLimitedError` -> "ratelimited_error" —
    the spelling ToolRegistry.call has always put on the wire."""
    return type(exc).__name__.replace("Error", "").lower() + "_error"

# service.py — one arm beside the existing KernelError arm
_REBUILD_REFUSED_HINT = (
    "the change was saved; the rebuild could not run because the part could "
    "not be read (see error.message — a missing script file, an unknown "
    "material, or a resolver refusal). Fix that, then rebuild (re-select the "
    "configuration or call get_part)."
)

def _refused_build(self, proj, part_id, exc, *, status_key, key=None, config=None) -> dict:
    payload = {"type": error_type(exc), "message": exc.message, "details": exc.details}
    if status_key is not None:
        self._status[status_key] = {"state": "error", "cache_key": key,
                                    "metrics": self._status.get(status_key, {}).get("metrics"),
                                    "warnings": [], "error": payload}
    self.bus.publish({"type": "rebuild_failed", "project": proj, "part": part_id,
                      "error": payload, **({"config": config} if config is not None else {})})
    return {"ok": False, "error": payload, "hint": _REBUILD_REFUSED_HINT}
```

- `_rebuild(proj, part_id)`: `try: record = self.store.get_part(...) except AppError as exc: return self._refused_build(proj, part_id, exc, status_key=self._status_key(proj, part_id))`, then `_build_with` as today.
- `_build_with(...)`: wrap the pre-kernel section (density, cache key, sidecar
  read, build params) in `try/except AppError as exc: return self._refused_build(..., status_key=status_key, key=<key if computed else None>, config=config)`.
  The existing `KernelError` arm is untouched. `rebuild_started` is published
  where it is today; because every `AppError` source precedes it, no
  `rebuild_started` is orphaned (state it in a comment; the TOCTOU is out of
  scope).
- `_ensure_config_built` memoizes the `ok: False` result exactly as it does
  for a kernel failure (already the case — verify with a test).
- `set_active_config`: take `after = service.store.get_part(...)` **inside**
  `manifest_scope` right after the write; compute divergence from it; publish;
  then `result = with_hint(service._rebuild(project, part_id))` (`with_hint`
  leaves the refused arm's own hint alone and still decorates a kernel
  failure as today).
- `routes_configs.py` docstring: the precedent sentence becomes true for both
  failure classes; say "a pre-build refusal is a build post-state too".
- `docs/agent-api.md`: one sentence in the Conventions/rebuild-result area:
  "a rebuild result with `ok: false` and no `details.traceback` is a pre-build
  refusal (script missing, unknown material) — its `hint` says so".

**Blast radius (verified in the plan review):** `gate.py:1489`,
`from_step.py:273`, `tools_solids.py:44` read `result["ok"]`; `get_part` reads
`read_script` itself before `_ensure_built`, so a missing script still 404s
there; no test pins the current refusals (`grep -rn "script file missing" tests/`
is empty). Confirm the same greps before editing and list what you find in the
report.

**Tests (TDD, real failures — a monkeypatched `_rebuild` may be a THIRD case,
never the only one):**
1. Script file vanished: `service.store.script_path(proj, id).unlink()`, then
   `set_active_config` (tool) → 200-shaped `{ok: False, error.type ==
   "notfound_error", hint == _REBUILD_REFUSED_HINT, active_config == name}`;
   the manifest holds the new `active_config`; exactly one `project_changed`
   and one `rebuild_failed` on the bus; `get_project`'s part row `state ==
   "error"`. Route: `PUT …/active-config` → **200**, `DELETE` → **200**.
2. Entry vanished between write and rebuild: monkeypatch `service.store.get_part`
   to raise `NotFoundError` on its Nth call (after the write) → tool returns
   the refused post-state, not an exception; route 200.
3. `service.set_params` on a part whose script file is gone → `{ok: False,
   error.type == "notfound_error"}` and `PATCH …/params` → 200 (the
   docstring's precedent, now true); `update_part`, `set_part_configs` (its
   nested `rebuild`), `set_solid_materials` — one assertion each that a
   missing script yields the refused post-state rather than an exception.
4. `error_type` pin: for each `AppError` subclass, `error_type(exc)` equals the
   `error.type` `registry.call` puts on the wire for a tool raising it.
5. Regression: a plain `KernelError` failure still writes `_status`, publishes
   `rebuild_failed`, and returns the traceback hint (unchanged behaviour).

## Item 2 — one `_drawing_result` for both drawing routes

**Files:** `agentcad/server/routes_drawing.py`, `frontend/js/drawings.js`
(comments + dead branches), tests: `tests/test_configs_drawing.py`.

**Change** (P5, P6, P7):

```python
from ..kernel.client import KernelError
from ..kernel.protocol import (ERROR_CONTRACT, ERROR_CRASH, ERROR_KERNEL,
                               ERROR_SCRIPT, ERROR_TIMEOUT)

#: A kernel failure is not a bad request: `_RAISE`'s default would answer 422
#: and rename it "ValidationError"; the house answer for a KernelError is
#: app.py's 502 with the kernel's own type intact.
_KERNEL_TYPES = {ERROR_SCRIPT, ERROR_CONTRACT, ERROR_KERNEL, ERROR_TIMEOUT, ERROR_CRASH}

def _drawing_result(payload: dict) -> dict:
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict) and error.get("type") in _KERNEL_TYPES:
        raise KernelError(error["type"], error.get("message", ""), error.get("details"))
    return _result(payload)
```

POST: `return _drawing_result(registry.call("generate_drawing", {...}))`.
GET: replace its `_result(...)` with `_drawing_result(...)`. Module docstring:
the two classes and their statuses. `generate_drawing` returns no `ok`
post-state (verified), so every `{"error"}` it yields is one of the two.

`frontend/js/drawings.js`: rewrite the two comments that state the old
contract ("The tool route returns {error:...} at HTTP 200 on failure") to
"the POST raises like every other pack route; the `catch` below is the error
path", and delete the now-unreachable `if (gen && gen.error)` / `if (result &&
result.error)` branches. `node --check` it.

**Tests:** POST `format: "gif"` → 422 `validation_error`; POST unknown part →
404 `notfound_error`; POST undeclared `config` → 422; POST whose kernel build
fails (`FRAGILE_SCRIPT` with its raising configuration active) → **502** with
`error.type` the type the worker actually emits (read `kernel/protocol.py`;
`contract_error` for a raising `build`) and `details.traceback` present; GET
parity for all four (the GET's kernel case is the behaviour change: 422→502).

## Item 3 — the summary counts what the reviewer can see

**Files:** `agentcad/core/packet.py` (`_summary`), `agentcad/core/proposals.py`
(~1408 summary stub), `frontend/js/proposals.js` (`assemblyBlock` ~970–999),
`docs/agent-api.md` (one sentence at the packet's `summary`), tests:
`tests/test_packet.py`, `tests/test_proposals.py` (stub shape).

**Change** (P8, P9, P10):

```python
added, removed, moved = (delta.get(k) or [] for k in ("instances_added", "instances_removed", "instances_moved"))
mates = delta.get("mates_changed") or []
configs = delta.get("configs_changed") or []
touched = {row["id"] for row in (*added, *removed, *moved, *mates, *configs)}
"instances_changed": len(touched),   # distinct instances, whatever changed about them
"mates_changed": len(mates),
"configs_changed": len(configs),
```

`proposals.py` stub: `"mates_changed": 0, "configs_changed": 0`. `proposals.js`
`assemblyBlock`: beside the mates loop,
`for (const row of assembly.configs_changed || []) list.appendChild(li(`config ${row.id}: ${row.old || "base"} → ${row.new || "base"}`));`
`node --check`.

**Tests:** rebinding-only delta → `instances_changed == 1`, `configs_changed
== 1`, `assembly.changed is True`; mates-only → `instances_changed == 1`,
`mates_changed == 1`; one instance moved + re-mated + rebound →
`instances_changed == 1`, `mates_changed == 1`, `configs_changed == 1` (the
distinct-id case); unchanged → zeros; the two producers agree on the key set
(`set(stub["summary"]) == set(_summary([], None))`); existing summary
assertions extended, not replaced.

## Item 4 — frame time on a real GPU (measured; record it)

Already measured in this session (Playwright `channel="chrome"`, headed,
frontmost, `uv run agentcad serve --no-open --projects-dir <scratch>/projects`,
project `cfgdemo` at the 3-configuration stage — no editing/reset/binding
steps run, P12). Numbers, from
`/private/tmp/claude-501/-Users-nfedorov--supacode-repos-cad-claude-parallel/763899f3-3439-4251-a572-2dce24917b58/scratchpad/item4-measurement.md`:

- renderer: `gl.RENDERER = "WebKit WebGL"`, unmasked `ANGLE (Apple, ANGLE Metal
  Renderer: Apple M1 Max, Unspecified Version)` (both strings, P13)
- control `about:blank`: avg 8.3 / p50 8.3 / max 10.2 ms (the 120 Hz vsync
  interval)
- part stage: 8.3 / 8.3 / 10.4 ms · assembly stage (`flange_a@s` +
  `flange_b@l`): 8.3 / 8.3 / 10.3 ms, idle / static / post-drag alike

**Criterion (P11):** median within ~1 ms of the vsync interval and no frame
above 2× it → **PASS**; 0207's 50–85 ms was swiftshader. **Deliverable:** the
numbers, both renderer strings, the criterion and the verdict in
`0212`'s Notes; one pointer line under 0207's Notes. No code.

## Verification

`uv run pytest tests/test_configs.py tests/test_configs_api.py tests/test_configs_drawing.py tests/test_service.py tests/test_solids.py tests/test_drawings.py tests/test_drawing_holes.py tests/test_packet.py tests/test_proposals.py tests/test_prd002_acceptance.py tests/test_server.py tests/test_specs.py tests/test_holes.py -q`
green (the last two cover the `_rebuild` wrappers), `node --check` on the two
JS files, then `make test` green (count into 0212).

## Rollback / landing

One commit on `prd-012-followups`, PR against `main`, CI green, merge; no
close-out commit (no PRD moves).
