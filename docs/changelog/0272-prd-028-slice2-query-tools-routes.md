# 0272 — PRD-028 slice 2: the materials query engine, `find_materials`/`get_material`, and the routes

- **Commit:** pending
- **Date:** 2026-08-20
- **Author:** Nikita Fedorov (orchestrated with Claude)

## Summary
Adds the pure query engine spec §6 describes (constraint grammar, ranking,
nearest-relaxation on an impossible query) and wires it into two new tools
(`find_materials`, `get_material`) plus a filterable `list_materials`, with
the matching `GET /api/materials`, `GET /api/materials/{id}` and
`POST /api/materials/find` routes.

## Changes
- **`core/materials_query.py`** (new, pure — no I/O, no kernel):
  - `CONSTRAINT_PROCESSES` — the ten `process` constraint values to their
    `Material.process` path (`"cnc" -> ("machinability",)`, `"fdm" ->
    ("printable", "fdm")`, `"sheet" -> ("sheet",)`, checked for presence
    rather than a rating).
  - `Constraints` (frozen) + `parse_constraints`/`normalize_constraints`:
    validate the `<property>_min`/`_max`/`category`/`subcategory`/`process`/
    `basis` grammar; unknown key -> `ValidationError` listing every known
    grammar key; `_min > _max` for one property -> `ValidationError`; bool is
    rejected as a numeric bound.
  - `qualifies(material, constraints) -> dict | None` — a range satisfies
    `_min` by its lower bound and `_max` by its upper bound; a material
    missing the property never qualifies; `basis` restricts to constraining
    properties carrying it; returns the `{key: {value|range, unit, basis,
    source}}` evidence dict on a pass.
  - `rank(rows, prefer) -> rows` — validates `prefer`, attaches `score`
    (sum of per-key normalized min/max position over the qualifying set,
    rounded to 4 dp; a material missing a preferred property scores 1.0 on
    it), stable tie-break `(category, subcategory, id)`; no `prefer` -> that
    same order with no score.
  - `nearest_relaxation(catalog, constraints) -> {drop, count} | None` —
    leave-one-out over the property/process/basis constraints (not
    category/subcategory, which are identity filters); `None` at <= 1
    droppable constraint or when no single drop admits more than the
    baseline; ties broken lexicographically.
  - `row(material, constraining, score=None)`, `find(catalog, require, prefer,
    category, limit=10)` composing all of the above; zero qualifying records
    raises `ValidationError("no material satisfies the constraints",
    {nearest_relaxation, tried})`; `limit` is validated to an int in [1, 50].
- **`core/tools_materials.py`**:
  - `find_materials {require?, prefer?, category?, limit? (default 10, max
    50), project?}` -> `{materials: [rows], count, constraints, caveat}`.
  - `get_material {id, project?}` -> `to_payload(full=True)` + `caveat`;
    reuses `MaterialLibrary.resolve`'s `ValidationError` for an unknown id
    (`"unknown material 'x'"`, `details.known` = every id).
  - `list_materials` gains `category?`, `subcategory?`, `filter?` (the same
    constraint grammar), filters the catalog through `qualifies`, and
    re-orders to `(category, subcategory or "", id)` (was `(category, id)`).
    `count`/`library_version`/`project_library_version`/`warnings`/`caveat`/
    `global_error` are unchanged in shape.
  - The three tool descriptions spell out the constraint grammar (built once,
    at module scope, from `PROPERTY_UNITS`/`CONSTRAINT_PROCESSES` so the doc
    text cannot drift from the code that enforces it).
- **`server/routes_materials.py`**:
  - `GET /materials?project&category&subcategory&filter=<json>` — `filter` is
    JSON-decoded query-side; invalid JSON or a non-object -> `ValidationError`
    -> 422.
  - `GET /materials/{material_id}?project`, `POST /materials/find` (JSON body
    = the tool args; a non-object body -> 422 via the shared `_json` body
    reader).
  - Both new GET routes and the POST route go through
    `routes_configs._result` (imported the way `app.py` already imports its
    body reader from that module): a tool refusal (`{"error": …}`, no `ok`
    key) is *raised* as the mapped `AppError` rather than returned as a 200
    body, so an unknown material id and a zero-result `find_materials` both
    answer **422** (there is no `NotFoundError` in this refusal path — see
    Notes). `PUT /projects/{proj}/materials` is byte-for-byte unchanged: it
    still returns `registry.call(...)` directly, so its own validation
    errors are still a 200 `{"error": …}` body — an existing inconsistency
    this slice does not touch.
  - Registers no new anonymous surface: neither new path is added to
    `security.PUBLIC_PATHS`/`PUBLIC_PREFIXES`, so the existing
    `test_hosted_surface.py` enumeration sweep covers them automatically
    (every route not named public is asserted 401 anonymously).
- **`docs/agent-api.md`** — the Materials table: rewrote the `list_materials`
  row for the v2 payload/params, added `find_materials` and `get_material`
  rows spelling out the full grammar, and reworded `set_project_materials`'s
  Arguments-column note for the v2 card shape.

## Files
- `agentcad/core/materials_query.py` — new, the pure query engine
- `agentcad/core/tools_materials.py` — `find_materials`, `get_material`,
  filtered/re-ordered `list_materials`
- `agentcad/server/routes_materials.py` — the three new routes
- `docs/agent-api.md` — Materials section rewritten
- `tests/test_materials_query.py` — new, 29 pure tests
- `tests/test_materials_tools.py` — new, 17 tool/route/gating tests
- `docs/changelog/0272-prd-028-slice2-query-tools-routes.md` — this file

## Verification
Targeted commands actually run in this slice (the orchestrator adds the
full-suite count in the close-out changelog):

- `.venv/bin/python -m pytest -q tests/test_materials.py
  tests/test_materials_query.py tests/test_materials_tools.py
  tests/test_materials_lint.py` — **109 passed**.
- `.venv/bin/python -m pytest -q -n 4 --dist loadscope tests/test_materials*.py
  tests/test_hosted*.py tests/test_security*.py tests/test_tools*.py
  tests/test_server*.py` — **289 passed**.
- `.venv/bin/python -m pytest -q tests/test_route_prefix.py` — **4 passed**
  (asserts `GET /api/materials` still 200s under the pack-prefix seam).

Not run here: the full suite (two other slices were running it concurrently
per the plan's non-negotiables) and `uv sync`/`uv pip`/`git` — none were
invoked.

## Notes
- **Deviation/clarification on the 404 vs 422 question the plan flagged**:
  `get_material`'s unknown-id refusal is a `ValidationError` (it reuses
  `MaterialLibrary.resolve`'s existing error rather than introducing a new
  `NotFoundError`), and the house convention `routes_configs._result` +
  `app.py`'s `_ERROR_STATUS` already establishes (`list_configs` and every
  other `_result`-wrapped GET) maps that to **422**, not 404. Keeping that
  convention rather than inventing a `NotFoundError` for this one route
  avoided a second, inconsistent error taxonomy for "the id you named does
  not exist" across the API.
- **`filter` query-param encoding**: `GET /api/materials?filter=<json>` reads
  `filter` as a plain query-string value and `json.loads`s it — so a client
  must URL-encode the JSON object (`?filter=%7B%22category%22%3A%22metal%22%7D`),
  the same convention `routes_drawing`'s `?sections=`/`?details=` already use
  for JSON-in-a-query-param.
- `rank`'s normalization is **linear min-max over the qualifying set's own
  values** for each preferred property (0 = the set's best point, 1 = its
  worst), not an ordinal percentile — the spec's "normalized rank (0 best …
  1 worst)" reads either way; min-max was chosen because it is what "the
  material's point among the qualifying set" cashes out to arithmetically,
  and it is what the pure tests in `test_materials_query.py` pin. A tie
  (every qualifying material shares one value on a preferred key) contributes
  0 to every candidate's score for that key rather than dividing by zero.
- `find_materials`'s `category` argument and `require.category` are both
  accepted; the named argument wins if both are given (not independently
  tested — an edge case the grammar allows but no caller has a reason to hit).
- Ran against the real, still-growing shipped catalog (curation is a
  concurrent slice): every count assertion in the new tests reads
  `len(...)`/`>= 1` off the live catalog rather than a hard-coded number, per
  the orchestrator's instruction.
