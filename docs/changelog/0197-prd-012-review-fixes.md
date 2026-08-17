# 0197 — PRD-012 review fixes: total configuration resolution, honest HTTP verdicts, strict bodies

- **Commit:** pending
- **Date:** 2026-08-18
- **Author:** Claude

## Summary
The one fix wave after the whole-branch review of PRD-012 (Configurations).
Three seats produced the list — an Opus final review (`F1`–`F8`), a Codex
review (`C1`–`C5`) and an independent verifier who reproduced or refuted each
finding (`V1`–`V4`) — and this entry applies exactly the consolidated
`MUST` + `SHOULD` set from
`.superpowers/sdd/2026-08-17-configurations/final-review-verified.md`
(`M1`, `M2`, `M3`, `S1`, `S2`, `S4`, `S5`, `S6`, `S7`). `S3` is the
controller's (the suite count in `0196`), and everything under `DEFER` — `D1`
(a dangling instance binding failing the whole `get_assembly`, refuted as an
inconsistency by the unknown-*part* precedent), `D2`, `D3` — is deliberately
untouched.

Three of the fixes change a contract, and each is written down where it lives:
configuration resolution is now **total** over a malformed member; a
configuration write that **landed** is a 200 whatever its `ok`; and a body that
is not a JSON object is a **422**, never `{}` and never a 500.

## Changes

### M1 (F3 + V2, Important) — resolution is total, and the merge reports the damage

`configs` is JSON a merge or a hand edit can shape, and `manifest_merge`
merges a non-object configuration entry **whole**
(`tests/test_manifest_merge.py::test_a_non_dict_configuration_entry_merges_whole`
already asserted the merged `{"m": None}`), so four shapes were reachable
without anyone editing `project.json`: `5`, `None`, `{"label": "M"}`,
`{"params": None}`. All four raised out of `PartRecord.effective_params` /
`config_params`, which `_cache_key_for` reads inside `_ensure_built` — i.e.
**upstream** of every configuration-aware branch the authors guarded. The
measured blast radius was a **500 on `GET /api/projects/{p}/parts/{id}`**, the
browser's first read of a part, plus `/configs/build`, `/parts/{id}/configs`
and `/drawing`.

- `agentcad/core/model.py` — `config_params` reads the entry once, returns
  `{}` for a non-dict and `dict(entry.get("params") or {})` otherwise;
  `effective_params` layers the same guarded read. Total over the **value**,
  still strict about the **name** (an unknown name is still a `KeyError` — a
  programming error, and softening it into a silent `{}` would hide one).
- `agentcad/core/manifest_merge.py` — `config_problems` gains a third kind,
  `malformed_configuration`, for a member that is not an object with a
  `params` map. A **warning**, like `dangling_active_config` and for the same
  reason (it resolves as base, so the project loads) — what it must not do is
  load *silently*. `{"params": {}}` is a legitimate configuration and is not
  reported.
- `agentcad/core/merge.py` — routes the new kind into `report["warnings"]`
  beside `dangling_active_config`. Without this the row existed and reached
  nobody: the filter named one kind explicitly.
- Redundant shape guards dropped by routing through the now-total accessor:
  `tools_drawing.py`'s `dim_table` column union now iterates
  `record.config_params(name)` (the same accessor the rows use, so the two
  cannot disagree about what a member holds), and `tools_configs.py`'s
  rebuild decision reads `before` through `config_params` instead of an
  `isinstance` on the raw entry.

### M2 (F1 + V4, Important) — a landed write is not served as a refusal

`PUT …/active-config` whose rebuild failed wrote the manifest, published
`project_changed`, and then answered **422** with the post-state thrown away
(measured: `persisted active_config: bad1`). The `DELETE` half was identical
(V4). The house answer on the identical failure is `PATCH …/params` → 200 with
`ok: false`.

- `agentcad/server/routes_configs.py` — `_result` now distinguishes a
  **refusal envelope** (`ToolRegistry.call` emits exactly `{"error": …}`, no
  `ok` key) from a **build post-state** (always carries `ok`): the raise is
  gated on `"ok" not in payload`. Safe against every tool in the pack —
  `set_active_config` is the only one that merges a rebuild at the top level;
  `set_part_configs` nests it under `out["rebuild"]`.
- The module docstring's absolute claim ("nothing about a configuration is a
  legitimate HTTP 200 error body") is replaced with the real rule: *a refusal
  raises; a build post-state is a 200 whatever its `ok`*.

No frontend change was needed: `applyRebuildResult` already has a correct
`ok: false` branch, and the success path calls `refreshPartDetail`, so the
switcher now lands on the truth instead of on the stale `state.part` its
`catch` used to repaint from.

### M3 (C3 + V3, Important) — a malformed or empty PATCH no longer unbinds

The only finding in either review where **malformed input silently mutated
persisted state**: `PATCH …/instances/{id}/config` with `[]`, `"bad"` *or an
empty body* each returned 200 and cleared a live binding, because `_json`
folded all three into `{}` and the tool's `config: str | None = None` default
means *unbind*.

- `agentcad/server/routes_configs.py` — `_json` is strict: a parsed body that
  is not an object raises `ValidationError("body must be a JSON object")`
  (422); `{}` is kept **only** for a genuinely absent body (it still reads the
  bytes, not `content-length`, for the chunked-request reason).
- the instance `PATCH` **requires** the `config` key —
  `{"config": null}` is now the only way to express *unbind*, the same shape
  the sibling `PUT active-config` enforces from the other side.
- every other route in the pack was re-checked against the stricter `_json`:
  an empty body still yields `{}`, so `PUT …/configs` still 422s through the
  registry's own missing-required-argument path.

### S1 (F5) — `cached` is measured for a de-duplicated sibling too

`agentcad/core/tools_configs.py`: `built[name] = (result, bool(result.get("ok")))`
for the members that share a cache key with the one actually built. Measured
before: `bad2: ok=False cached=True` while no `.acm`/`.metrics.json` existed —
a claim the tool description and `docs/agent-api.md` both contradict.

### S2 (F8) — AC8a's "byte-identical" sentence is now true

`tests/test_prd012_acceptance.py`: the manifest bytes are captured before
`set_params(name, "flange", {})` and compared as bytes. The parsed comparison
it replaces would have passed a reordered or reformatted file. Verified to
pass on the code as it already stood (1957 B → 1957 B).

### S4 (F2, downgraded to Minor) — say the real export/drawing rule out loud

The *contract* half of F2 is **refuted**: exporting the pure configuration
under `<part>_<config>` while the working state is diverged is Decision 3
("a variant's identity never depends on session state") and Decision 8, and it
is the right contract — a file named after a configuration that quietly
contained someone's unsaved slider drag is the worse failure. Opus's proposed
fix (drop the config when diverged) is **not** taken: it would write the
on-screen geometry under the base filename for a part the user believes is at
L. What was defective is the wording.

- `frontend/js/main.js` — the comment claiming "'export this part' means
  export what is on screen" is corrected to the pure-resolution rule, and the
  export toast appends "— the configuration as declared; your edits are not in
  it" when `state.part.status.diverged`.
- `frontend/js/drawings.js` — `configOf` also returns `diverged` (from the
  loaded part detail, the only piece of state that carries `status`), and the
  preview title reads `flange@m · drawing (configuration as declared — your
  edits are not shown)`. No extra round trip: the flag is already on the
  `get_part` payload.
- `docs/user-guide.md` and `docs/agent-api.md` state the rule, and name the
  base export/drawing as the way to get the working state.

### S5 (F6) — the drawing request is pinned to the part's worker

`agentcad/core/tools_drawing.py`: `affinity=part_id` on the `drawing` request.
Pre-existing omission (`9bfd9e4` had none either), newly consequential:
`dim_table` turns one request into up to eight builds and the browser preview
issues it twice (the POST, then the regenerating GET). This is the house rule
everywhere else that issues repeated builds of one part — `tools_holes` cites
an 11 354 ms → 1 ms measurement for it.

### S6 (C1 + C2 + V1) — non-object bodies are 422s, and the SVG GET raises

**Pre-existing and house-wide, not PRD-012 regressions**: `git show 9bfd9e4`
has the same unguarded `body.get(...)` on all of them, and two of the four
routes (`PUT /assembly`, `PATCH …/params`) are untouched by the branch. Fixed
while the file was open.

- `agentcad/server/app.py` — the export, `PUT /assembly` and `PATCH …/params`
  routes read the body through `routes_configs._json` (imported as
  `_object_body`), so `[]` / `"bad"` / `3` is a 422 instead of an
  `AttributeError` 500.
- `agentcad/server/routes_drawing.py` — the drawing POST does the same, which
  also replaces its `content-length` test with the bytes test.
- V1: the SVG GET no longer returns the tool's error dict at **HTTP 200 with
  `content-type: application/json`** from an endpoint declared to serve SVG —
  it calls `_result`, so a refusal is the typed error (422).
  `drawings.js` already read a JSON error body from a non-OK response.

### S7 (C4-adjacent hardening) — the route validates `config` itself

C4 ("`config` is concatenated into a filesystem path … can read unintended
files") is **refuted** as a live vulnerability: `generate_drawing`'s first
statement is `_record_for`, which refuses an undeclared name, so the
`if "error" in result` branch returned before `suffix` was ever computed
(`file bytes leaked: False`). But the route's own safety must not depend on a
tool three modules away keeping that order.

`agentcad/server/routes_drawing.py` applies `packages.format.CONFIG_RE` with
`fullmatch` before the call — the `_KEY_RE`/`_LOD_RE` precedent in
`routes_configs.py`, and `fullmatch` for the same reason (`$` also matches
*before* a trailing newline).

### Re-review fix — `PUT /assembly` requires `instances` (a regression S6 introduced)

The re-review of this wave found one **new** breakage, and it is S6's: making
`_json` the shared reader gave `PUT /api/projects/{p}/assembly` a `{}` body for
a genuinely absent body, and `service.set_assembly(proj, body.get("instances",
[]))` is a full-list **replace** — so an empty request wiped
`assembly.instances` and answered **200**. Before the wave the same request was
a 500 with no mutation, which is worse to read and better to survive.

`agentcad/server/app.py` now requires the key, mirroring M3 exactly:
`instances is required; send {"instances": []} to clear the assembly` (422),
and the call reads `body["instances"]` rather than a defaulted `.get`. The
principle is the one M3 wrote down — **absence cannot mean "nothing to change"
when the default is the destructive verb** — and it is the second route in the
branch to need it.

The other two routes moved onto the strict reader were re-checked and are
benign for an absent body, now with an assertion each rather than a judgement:
`PATCH …/params` with no body merges an empty override map (200, parameters
unchanged) and `POST …/export` with no body is the export's own
empty-format refusal (422). The browser never calls `PUT /assembly` at all
(`api.js` has only the GET and the per-instance PATCH), so nothing on the
frontend changes.

### House gotchas (`AGENTS.md`, `CLAUDE.md`) — the three facts that now trail the code

Docs-only addendum to the same wave. Three items in `AGENTS.md`'s
"Configuration gotchas (PRD-012)" section described the pre-wave behaviour and
are corrected in place, and `CLAUDE.md`'s condensed PRD-012 trap line mirrors
them:

- **Resolution is total over the value, strict about the name** — a new bullet
  beside the two pure members: the four reachable malformed shapes, why a raise
  there was a 500 on the part's *primary* read (`_cache_key_for` inside
  `_ensure_built`), `malformed_configuration` as a warning through `merge.py`,
  and the standing rule that a call site reading **`label`** off a raw entry
  still needs its own guard (there is no accessor for it).
- **`_result`'s real rule** — a refusal envelope (no `ok` key) raises; a build
  post-state is a 200 whatever its `ok`, because the write already landed.
  `_BODY_ERRORS` is still empty, and the old absolute claim is called out as
  gone.
- **`_json` is strict and shared** — non-object body → 422, `{}` only for a
  genuinely absent body (still the bytes, not `content-length`); the instance
  `PATCH` requires `config`, so `{"config": null}` is the only unbind; the same
  reader is imported by three `app.py` routes and the drawing POST, and the SVG
  GET gates `?config=` with `fullmatch` `CONFIG_RE` and raises rather than
  serving a refusal as JSON at 200.

The existing `config_problems` bullet in `AGENTS.md` gained its third kind, and
the `CLAUDE.md` line gained the `affinity=part_id` pin on the drawing request
(S5).

## Files

- `agentcad/core/model.py` — `config_params`/`effective_params` total over a
  malformed member; docstrings say why.
- `agentcad/core/manifest_merge.py` — `malformed_configuration` kind in
  `config_problems`; docstring now describes three kinds.
- `agentcad/core/merge.py` — the new kind routed into `report["warnings"]`.
- `agentcad/core/tools_configs.py` — `cached` measured for a de-duplicated
  sibling; the rebuild decision reads `before` through `config_params`.
- `agentcad/core/tools_drawing.py` — `affinity=part_id`; the `dim_table`
  column union goes through `config_params`.
- `agentcad/server/routes_configs.py` — `_result` refusal-vs-post-state;
  `_json` strict; instance `PATCH` requires `config`; module docstring rewritten.
- `agentcad/server/routes_drawing.py` — strict body on the POST; `CONFIG_RE`
  gate and `_result` on the SVG GET.
- `agentcad/server/app.py` — export / `PUT assembly` / `PATCH params` read the
  body through the strict reader, and `PUT assembly` additionally **requires**
  its `instances` key (the strict reader made an absent body a silent wipe).
- `frontend/js/main.js`, `frontend/js/drawings.js` — the pure-resolution
  wording, and the diverged note in the toast and the preview title.
- `docs/user-guide.md`, `docs/agent-api.md` — the same rule, for readers.
- `AGENTS.md` — "Configuration gotchas (PRD-012)": a new totality bullet, two
  new `routes_configs` bullets (`_result`, `_json`/PATCH), the third kind on
  the `config_problems` bullet, and the generalised required-key rule the
  assembly regression proved (*absence cannot mean "nothing to change" when
  the default is the destructive verb* — check it before putting any other
  route on the strict reader).
- `CLAUDE.md` — the condensed PRD-012 trap line mirrors the same three facts
  plus `malformed_configuration`, the drawing request's `affinity=part_id`,
  and `PUT /assembly`'s required `instances` key.
- `tests/test_configs.py` — four parametrised malformed shapes at the model,
  and a service-level `get_part` over a hand-written `configs={"m": None}`.
- `tests/test_configs_api.py` — the two 200-post-state route tests (PUT and
  DELETE), the malformed/empty PATCH test whose **state assertion** is the
  load-bearing half, and the failed-twin `cached` assertions.
- `tests/test_configs_drawing.py` — affinity, the SVG GET's 422 refusal, and
  the route-level name grammar (proving the tool is never reached).
- `tests/test_manifest_merge.py`, `tests/test_configs_merge.py` — the new
  `config_problems` kind, and one real two-branch merge proving it warns and
  does not block.
- `tests/test_prd012_acceptance.py` — AC8a compares bytes; AC9 requires the
  diverged branch in both frontend surfaces.
- `tests/test_server.py` — one parametrised route test pinning 422 (not 500)
  for `[]` / `"bad"` / `3` on all four body-reading routes; plus
  `test_an_absent_assembly_body_is_a_422_and_never_wipes_the_assembly` (empty
  body → 422 with the instances **unchanged**, `{"instances": []}` still
  clears at 200) and `test_an_absent_body_is_harmless_on_the_other_strict_
  reader_routes` (the params/export confirmations).

## Notes

- **Two of the four "now-redundant cosmetic guards" the list named are not
  redundant, and were kept.** M1 makes the *params* read total; it says nothing
  about `label`. `tools_drawing.py`'s
  `declared[name].get("label") if isinstance(declared[name], dict)` and
  `tools_configs.py`'s `entry = declared[name] if isinstance(…, dict) else {}`
  both read `label` off the raw entry, so deleting them would reintroduce the
  exact `AttributeError` M1 exists to remove. The two that the model genuinely
  subsumes (the `dim_table` column loop and the rebuild `before` read) were
  dropped.
- **`before` semantics moved by one hair, in the right direction.** A malformed
  prior member now compares as `{}` rather than `None`, so rewriting it into
  `{"params": {}}` correctly decides that no geometry moved and skips the
  rebuild.
- **The `_result` fix is a widening, never a narrowing.** Only a payload that
  carries `ok` stops raising; a refusal envelope has no `ok` and still raises
  with the same status mapping. The route tests assert both halves in one test.
- **`_json`'s strictness reaches four routes outside this pack by import.** It
  is the shared helper the fix list sanctioned rather than a fifth copy of the
  same three lines.
- **Untouched on purpose:** the PRD's FR1, `examples/rocketry/parts/flange.py`,
  `_cache_key`'s payload, and the `_rebuild`/`get_part` signatures.

## Verification

- Re-review fix: `uv run pytest tests/test_server.py tests/test_configs_api.py
  tests/test_service.py -q` — **82 passed**. The new assembly test is RED
  first (`assert 200 == 422` with the body
  `{"instances":[],"total_mass_g":0.0,"bbox":null}` — the wipe, measured), the
  params/export confirmations pass as the re-reviewer judged. Assembly
  neighbours `tests/test_mates.py tests/test_configs_assembly.py
  tests/test_versioning_api.py tests/test_locks.py tests/test_packet.py` —
  **97 passed**. The eight-file focused suite is **265 passed**.
- Docs meta-tests after the `AGENTS.md`/`CLAUDE.md` addendum:
  `uv run pytest tests/test_prd011_acceptance.py tests/test_prd012_acceptance.py
  -q -m ""` — **28 passed**.
- Focused: `uv run pytest tests/test_configs.py tests/test_configs_api.py
  tests/test_configs_drawing.py tests/test_manifest_merge.py
  tests/test_configs_merge.py tests/test_prd012_acceptance.py
  tests/test_server.py tests/test_drawings.py -q -m "" -p no:randomly` —
  **263 passed** in 23s (248 before this wave; 18 of the new assertions were
  red first, one per finding plus parametrisation).
- Neighbours that touch the changed surfaces: `tests/test_checks_ref.py
  tests/test_drawing_holes.py tests/test_locks.py tests/test_mcp.py
  tests/test_packages_tools.py tests/test_pmi.py
  tests/test_prd010_acceptance.py tests/test_tools.py
  tests/test_versioning_api.py tests/test_merge.py
  tests/test_configs_assembly.py tests/test_configs_checks.py
  tests/test_mates.py tests/test_project.py tests/test_packages_ocp_free.py` —
  **277 passed** in 3:20.
- `node --check frontend/js/main.js frontend/js/drawings.js` — clean.
- Full suite: `make test` — 3507 passed, 7 skipped in 8:51 on 8 workers (measured after the re-review fix landed).
