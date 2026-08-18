# PRD-007 — Share links, embedded viewer, and customizer publishing

- **Status:** implemented — on branch `prd-007-share-links-customizer`, AC1–AC9
  verified (`tests/test_prd007_acceptance.py`), the two browser ACs (AC1 the
  logged-out page, AC7 the embed) **graded as evidence** rather than driven — no
  Chrome extension was available (`list_connected_browsers` → `[]`), the same
  posture PRD-005a's AC3 took. See "Verification levels" and "Residual gaps"
  below; evidence is in changelogs 0213–0218.
- **Phase:** v4 — collaborative core
- **Created:** 2026-08-09
- **Origin:** both — competitive analysis + founder idea #1e (Aug 2026)
- **Depends on (as built):** PRD-005a (hard — the shipped hosted core: the
  guard's allowlist, the `PREFIX` mount seam, identity, the trusted-proxy
  address) · PRD-001 (the immutable tag pin) · PRD-011 (content addressing —
  the variant cache key) · PRD-004 (the muzzled ephemeral-service recipe) ·
  PRD-012 (the pure `normalize_params` seam and the config-build path).
  **PRD-006 is *not* required for our own content** (005a Decision 2): a
  bounded-param rebuild passes DATA to `build(p)`, never code. The memory/pid/
  disk/egress caps 006 owes are the honest residual below.
- **Related:** PRD-031 (the marketplace grows from this seed), PRD-001
  (links pin tags), PRD-011 (registry funnel), PRD-012 (configs in the
  customizer), PRD-017 (glTF exporter)

> **Design divergences, folded in (2026-08-18 — now implemented).** This PRD
> rides the shipped hosted core (PRD-005a), not the deferred PRD-005/006. The
> [design spec](../../superpowers/specs/2026-08-18-share-links-customizer-design.md)
> and [plan](../../superpowers/plans/2026-08-18-share-links-customizer.md) record
> these divergences from the text below, and they are **what shipped**: the
> customizer rebuild is a **`GET`** of a content-addressed variant, not a POST —
> a pure read (owner state never changes), which makes CSRF moot and cross-origin
> embedding work by construction (settles FR6's guard question, and supersedes
> FR8/AC2's "POST"); publication/link state lives in the **PRD-005a state dir**
> (`<state-dir>/publications/`, one shared space), not "PRD-005's storage", with
> a **store-backed `sha256`** capability token rather than an HMAC-signed one
> (immediate revocation, the reason 005a rejected JWTs — supersedes FR1's
> "HMAC-signed"); the immutable pin is a **copy** of the script bytes at a
> resolved tag into a **muzzled build service** under the state dir, so the
> visitor path never touches a user `ProjectStore`; the viewer streams the
> shipped **ACM** format (reusing `viewport.js::parseACM`) and **`core/gltf.py`
> is deferred to PRD-017** to avoid build-then-migrate churn (supersedes FR5's
> "glTF", matching this PRD's own risk note); the surface is **two** route packs
> (`routes_share.py` at `/api`, `routes_share_public.py` at the root) because a
> pack carries one `PREFIX`; a disabled export or a `customizer:false` link
> answers **`404`** before the builder (the design's structural escalation
> boundary, in place of FR11's "403"); and the MVP is part-scope. The settled
> founder calls: `/embed/` ships **`frame-ancestors *`** (any site may embed the
> public customizer) while every other hosted response ships
> **`frame-ancestors 'none'`**; link expiry defaults to **never, revocable**; the
> login-above-N gate ships **off** (`AGENTCAD_SHARE_REQUIRE_LOGIN_ABOVE`); viewer
> links need **no account**.

## Problem & motivation

Nothing made in AgentCAD can be shown to anyone. The server binds
`127.0.0.1`, there is no second-user concept, and "sharing" means
exporting a STEP file into a chat thread (market_research.md, "Where
AgentCAD stands today"). Sharing is the entry ticket to cloud CAD:
Onshape's free tier built a 2M+ user funnel on public documents
("Cloud-native CAD: Onshape"), and the gap matrix scores "share links /
embedded viewer / publish-with-sliders" a plain **build**.

The differentiated half is the customizer. Thingiverse's Customizer and
Bambu MakerWorld's Parametric Model Maker prove that millions will
*consume* parametric models through sliders ("Open-source CAD") — but
both cap out at OpenSCAD meshes: STL in, STL out, no engineering
artifacts. Every AgentCAD part already carries typed, bounded PARAMS and
builds on a real B-rep kernel, so our published variant downloads as
STEP, flat patterns, and toleranced drawings — engineering artifacts no
mesh customizer can emit. Founder idea #1e frames this as the seed of the
community loop, and the business-model guardrails make publish/customize
the top of the funnel — while explicitly rejecting Onshape's forced-public
free tier: here sharing is opt-in, per link ("Business-model
guardrails"). PRD-031's marketplace is this page grown up; this PRD lays
the substrate it inherits.

## Users & jobs

- **Publisher (human):** turn a part or project into a URL — for a forum
  post, a client, a teammate without an account — with control over what
  the link exposes.
- **Visitor (logged out):** view a real model in the browser, tweak it
  within the author's bounds, download *their* variant — zero install,
  zero login.
- **Forum/docs author:** embed a live, orbitable model in a page.
- **Design agent:** publish as a task outcome ("share the nozzle for the
  review") and read published scripts as reuse substrate.
- **The future marketplace (PRD-031):** inherit links, counters, and
  attribution as its listing/popularity substrate.

## Goals

- G1. Any project/part at any version is shareable as a URL that renders
  in any browser, logged out, no install.
- G2. Customizer mode: typed PARAMS become a bounded playground —
  sliders, dropdowns, checkboxes — with real kernel rebuilds of the
  visitor's variant.
- G3. Variants download as engineering artifacts (STEP/3MF/STL, drawings,
  flat patterns) under the link's export policy.
- G4. Sharing is safe by construction: capability tokens, view-only role,
  server-validated params, quotas and rate limits, and owner state that
  is provably immutable to visitors.
- G5. Embeds work on third-party pages, and every page carries
  attribution — the marketplace seed.

## Non-goals

- Discovery, search, profiles, ratings, monetization — PRD-031 (this PRD
  ends at the link; the schema reserves fields instead of growing
  features).
- Editing or commenting via links — review needs real identity (PRD-005
  roles, PRD-008 threads).
- Anonymous write access of any kind.
- A public gallery of all shares — links are unlisted capabilities;
  opt-in listing is PRD-031.

## Experience

**Publisher path.** "Share…" on a project or part opens a dialog: scope
(this part / whole project), ref (a version tag by default — links don't
drift; optionally "follow branch," labeled live), customizer on/off,
export mask (none / STL / STEP / 3MF / drawing / flat pattern), script
visibility, expiry. Create → URL copied. A Links panel lists active links
with view/rebuild/download counters and revoke buttons.

**Visitor path.** `/s/<token>` renders server-side: the Three.js viewer
streaming glTF, attribution ("<org>/<project> @ <tag>"), a metrics strip
(mass, bbox, material), a drawings tab when published. With customizer
on, the PARAMS panel renders each typed param — number → slider clamped
to min/max, int → stepped, enum → dropdown, bool → checkbox, string →
text with max_len. Rebuild → progress → updated mesh and metrics.
"Download STEP" delivers *their* variant. Over the rate limit, the page
degrades to view-only with a plain retry-after message.

**Embed.** `<iframe src=".../embed/<token>">` — the viewer plus optional
compact sliders, on any third-party page.

**Agent path.** `share_create {project, scope, part_id?, ref?,
customizer?, exports?}` → `{url}` — the agent drops the URL into chat, a
proposal, or a forum post. Where the publisher enabled script visibility,
the published script is fetchable — reuse substrate for any agent that
can read a URL, feeding PRD-011 and PRD-031.

## Functional requirements

**Links & access**
- FR1. `share_create` mints a link record `{token_id, scope:
  project|part, part_id?, ref, settings, created_by, expires?}`; the URL
  token is unguessable (≥128-bit) and HMAC-signed; the server resolves it
  to role=view on exactly that scope and ref — a share principal in
  PRD-005's model with no other rights, ever.
- FR2. `share_list` / `share_revoke`; revocation is immediate; revoked,
  expired, and unknown tokens all answer 404 indistinguishably (no
  oracle).
- FR3. The default ref is an immutable tag (PRD-001); `ref: <branch>`
  opts into following the head and the page labels it "live".
- FR4. Link creation and revocation are audited (who, when — the same
  attribution plumbing as PRD-002).

**Viewer page**
- FR5. `/s/<token>` server-renders a self-contained page: glTF meshes,
  instance transforms and colors for assemblies, metrics, attribution; no
  login, no owner cookies, fully functional logged out.
- FR6. `/embed/<token>` is iframe-embeddable on third-party origins. The
  main app's Host-allowlist/same-origin guard
  (`server/app._browser_request_allowed`) stays intact — share routes are
  an explicitly enumerated public surface with their own relaxed
  frame-ancestors, no-credentials policy.
- FR7. Script visibility is per link: on → the part script renders
  read-only on the page; off → the script is never served through any
  share endpoint.

**Customizer**
- FR8. The playground exposes exactly the typed PARAMS
  (number/int/bool/enum/string with min/max/choices/max_len);
  server-side validation is identical to `set_params` semantics — clamp
  numerics with warnings, reject wrong types, non-member choices, and
  unknown names.
- FR9. Variant rebuilds are ephemeral: keyed by the existing content hash
  (script, params, density, tolerance), built in the shared kernel pool,
  and cached so repeat variants cost nothing; the owner's project state,
  params, history, and cache are never touched by any visitor action.
- FR10. Rebuild quotas: per-link and per-IP token buckets plus PRD-006
  worker caps (CPU-seconds, memory, wall clock); over limit returns a
  structured `quota_exceeded` with `retry_after_s`, and the page degrades
  to view-only — never a blank error.
- FR11. Variant exports honor the link's mask: STEP/STL/3MF via the
  existing export paths, drawing SVG and flat pattern where the script
  defines them — of the visitor's variant, named `<part>_<hash8>.<ext>`;
  disabled formats are absent from the page and 403 at the route.
- FR12. Per-link counters (views, rebuilds, downloads) are visible to the
  owner — coarse integers only, no visitor PII (the popularity signal
  PRD-031 inherits).
- FR13. Forward-compat: once PRD-012 lands, a link may expose named
  configurations instead of raw params; the link schema reserves the
  field now.

## Agent surface

New tools: `share_create {project, scope, part_id?, ref?, customizer?,
exports?, show_script?, expires_days?}` · `share_list {project}` ·
`share_revoke {project, token_id}`.
New event: `share_changed {project}`.
Errors: `validation_error` (bad scope/mask), `not_found` (bad/revoked/
expired token), `quota_exceeded` (details carry `retry_after_s`).
Visitor-facing endpoints speak the same structured-error contract, so the
page — and any agent reading a published link — gets machine-readable
failures.

## Technical approach

- **Route pack** `agentcad/server/routes_share.py`: the management API
  (authenticated, tenant-scoped) plus the public surface (`/s/`,
  `/embed/`, variant rebuild and export endpoints) — the only routes
  exempted from the same-origin guard, enumerated in one place for
  PRD-005's security review.
- **Link store**: tenant-scoped records in PRD-005's storage; HMAC over a
  server secret; token → record lookup, then the permission layer
  enforces role=view.
- **glTF path**: `agentcad/core/gltf.py` converts cached ACM1 meshes
  (positions/normals/indices are already there) to binary glTF with no
  kernel involvement — the `render.py` discipline. PRD-017's full
  exporter (materials, units metadata) later absorbs this module: one
  module, two consumers.
- **Variant builds**: a service method `build_variant(project, part_id,
  ref, params) -> {mesh_key, metrics}` resolves the script at the ref,
  validates params, and builds through the normal kernel pool under a
  share `affinity=` key (the pool-routing seam, for cache warmth — **not**
  isolation); member starvation is prevented by reserving `pool_size - 1`
  workers for the anonymous cap, not by the affinity. Results live in a
  shared variant cache with LRU/GC.
  `ProjectStore` is never written on the visitor path — no
  `write_guard` interaction at all.
- **Rate limiting**: middleware on the public routes (token bucket per
  link and per client IP), enforcement wired to PRD-006's metering.
- **Frontend**: a slim standalone bundle reusing `viewport.js`'s
  rendering core plus a param-panel module — no CodeMirror, no
  inspector; page weight matters for embeds. Share dialog + Links panel
  in the main app.

## MVP & phasing

- **MVP:** part-scope view links pinned to tags; the viewer page (glTF +
  metrics + attribution); the customizer with validated rebuilds,
  per-link and per-IP rate limits, and the variant cache; STEP/STL
  export gating; the share dialog and `share_*` tools; revoke and
  expiry.
- **Phase 2:** project-scope links (assembly viewer), embeds, drawings
  and flat-pattern tabs, script visibility, counters, branch-following
  links.
- **Phase 3:** config-mode customizer (PRD-012); the page grows listing
  metadata and opt-in discovery — the handoff line to PRD-031.

## Acceptance criteria

- AC1. A rocketry nozzle link opens for a logged-out visitor: viewer,
  metrics, and attribution render with no auth cookies on the response
  (browser session against a deployed instance — the roadmap's
  done-when).
- AC2. The expansion-ratio slider rebuilds live and metrics update; a
  second visitor requesting the same variant is served from the variant
  cache — exactly one kernel build for the two requests (test).
- AC3. STEP of the visitor's variant downloads when the mask allows;
  a disabled STL is absent from the page and 403 at the route (test).
- AC4. Param validation parity with `set_params`: out-of-range clamps
  with a warning, a non-member enum choice is rejected, an unknown name
  is rejected (parity test against the same fixtures).
- AC5. Hammering rebuilds past the per-link limit returns
  `quota_exceeded` with `retry_after_s`; throughout, the owner's
  manifest, params, history, and `.cache/` are byte-unchanged (test
  asserting store equality).
- AC6. Revoked and expired links both 404 with indistinguishable bodies
  (test).
- AC7. The embed iframe renders and orbits on a page served from a
  second local origin (browser test — the roadmap's embed clause).
- AC8. A tag-pinned link keeps serving the tagged geometry after the
  source branch moves on (test over PRD-001 refs).
- AC9. The share routes are provably the only guard-exempt surface (a
  test enumerating exemptions fails when a new route goes public by
  accident); full suite green.

## Verification levels

What each criterion was graded against, so a reader never guesses whether
"verified" meant a unit test or a running system. Evidence is in changelogs
0213–0218; `tests/test_prd007_acceptance.py` carries one test per criterion.

| AC | Direct test | Real server | Real browser |
|---|---|---|---|
| AC1 logged-out page renders, no auth cookie | yes (shell 200, no `Set-Cookie`; `/model` metrics + attribution, zero kernel) | via `TestClient` | **no — graded as evidence** |
| AC2 slider rebuilds; a repeat is one build for two requests | yes (`kernel_counter`: fresh builds once, repeat zero; a distinct set is the positive control) | — | — |
| AC3 STEP downloads under the mask; a disabled STL 404s before build | yes (200 STEP; `stl` 404, counter unchanged) | — | — |
| AC4 param parity (clamp / reject enum / reject unknown) | yes (against the same `normalize_params` / `_resolve_params` the editor uses) | — | — |
| AC5 over-limit `quota_exceeded` + owner tree byte-unchanged; in-flight cap | yes (per-link 429 with `retry_after_s`; owner snapshot equality; the semaphore consulted, with a positive control) | — | — |
| AC6 revoked == expired == unknown 404; `customizer:false` 404s `/variant` | yes (indistinguishable bodies; the escalation boundary, counter unchanged) | — | — |
| AC7 the embed frames on a second origin | yes (`frame-ancestors *` on `/embed/`, `'none'` on the app) | — | **no — graded as evidence** |
| AC8 tag-pinned link keeps serving after the branch moves + the owner edits | yes (`test_share_publish.py`, over PRD-001 refs + a `write_script`) | — | — |
| AC9 the share routes are provably the only new guard-exempt surface | yes (the set-equality enumeration grown to the eight `/s/`+`/embed/` templates, `NOT_YET_BUILT == set()`) | — | — |

**The two browser ACs are not verified, and nothing here claims they are.**
`list_connected_browsers` → `[]` (the same as PRD-005a's three sessions), so the
viewer page, a slider drag, a download and the embedded iframe were **never
rendered by a browser**. What *is* verified is every HTTP contract those views
consume, the served HTML's shape, the `kernel_counter` deltas and the response
headers, plus the JavaScript parsing (`node --check`). The criteria are
unchanged and unmet at the visual level; they are the first thing a reviewer with
a browser should close.

## Residual gaps (recorded, not fixed)

- **Peak memory is uncapped until PRD-006 — the defining residual.** A
  params-driven mesh can still balloon RSS and OOM the host. What *is* bounded:
  a visitor gains no code execution (bounded PARAMS are data, not code), the
  per-request timeout kills a runaway build, per-link + per-IP token buckets
  bound the rate, a global in-flight semaphore
  (`AGENTCAD_SHARE_MAX_INFLIGHT`) bounds concurrency with its effective size
  clamped to `pool_size - 1` so a member's worker is always reserved (a
  single-worker pool refuses the customizer with `503`; pool affinity
  `share:<pub>` is cache-warmth routing, **not** segregation), and the
  content-addressed variant cache — keyed on the **clamped** params so
  out-of-range floods coalesce — makes "popular = cheap". What is **not**
  bounded: memory, process/pid, **variant-cache disk** (a distinct-in-range
  flood still builds and fills it), and worker network egress — all PRD-006's.
  Until then the operator's backstop for a link under a distinct-param flood is
  `AGENTCAD_SHARE_REQUIRE_LOGIN_ABOVE` (off by default).
- **Project/assembly-scope links, embeds-with-sliders polish, drawings/flat
  pattern exports, script-visibility UI beyond the raw route, branch-following
  ("live") links, and the config-mode customizer (PRD-012)** are Phase 2/3, per
  the phasing above; the record and schema reserve the fields (`config` on the
  settings, `scope`).
- **A project-custom material is not copied into the content-addressed build
  project**, so the pin falls back to the default material's density for such a
  part (changelog 0214). Phase 2 copies the manifest's materials section.
- **The variant cache has no GC yet** — a size-capped sweep is a small Phase-2
  slice, deferred with the other 006 disk-budget residuals.
- **The two browser ACs (AC1, AC7) are graded as evidence, not driven** — see
  Verification levels. `tests/test_share_frontend.py` grades what a headless run
  can; the visual pass awaits a connected browser.

## Risks & open questions

- **Stranger compute** is the existential risk — a popular link is a
  free rebuild farm. Defense in depth: the variant cache (popular = cheap,
  keyed on clamped params so out-of-range floods coalesce), token buckets, the
  in-flight cap's `pool_size - 1` **worker reservation** (a member's worker is
  never occupied by anonymous builds; a single-worker pool refuses), and PRD-006
  CPU/memory/wall caps. Residual: a distinct-in-range flood still builds and the
  variant-cache disk is unbounded until 006. Open: a per-deployment policy for
  requiring login above a rebuild threshold (shipped as the login-gate knob).
- **Guard carve-out** — any mistake in the public-route enumeration
  widens the attack surface; single-file enumeration plus the AC9 test,
  reviewed in PRD-005's security pass.
- **Bearer-capability leakage** — a leaked URL is access. Mitigated by
  expiry defaults, instant revocation, per-link export masks, and the
  no-write role; documented plainly: anyone with the link can view.
- **glTF duplication with PRD-017** — build the minimal ACM→glTF here,
  hand ownership to 017 when it lands.
- **Marketplace gravity** — every review of this feature will ask for
  search, profiles, payments. The line is drawn at the link; PRD-031
  owns the rest.

## Competitive references

Onshape's free tier (public documents) built the 2M-user funnel — and its
forced-public model is exactly what our guardrails reject; sharing here
is opt-in per link (market_research.md, "Cloud-native CAD: Onshape",
"Business-model guardrails"). Thingiverse Customizer and Bambu
MakerWorld's Parametric Model Maker prove slider-consumption at mass
scale, capped at OpenSCAD meshes ("Open-source CAD"). GrabCAD's dead
Workbench left the share-engineering-models vacuum (roadmap.md, §4.7). We
differ: B-rep artifacts out (STEP, flat patterns, toleranced drawings),
bounds enforced by the same typed-PARAMS validation as the editing
surface, real kernel rebuilds rather than pre-baked mesh swaps — and
every published script doubles as agent-readable reuse substrate feeding
PRD-011 and PRD-031.
