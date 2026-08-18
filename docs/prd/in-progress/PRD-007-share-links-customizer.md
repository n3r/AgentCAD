# PRD-007 — Share links, embedded viewer, and customizer publishing

- **Status:** pending
- **Phase:** v4 — collaborative core
- **Created:** 2026-08-09
- **Origin:** both — competitive analysis + founder idea #1e (Aug 2026)
- **Depends on:** PRD-005 (hard — identity, tenancy, a public host) ·
  PRD-006 (hard — quotas and rate limits on stranger-driven rebuilds)
- **Related:** PRD-031 (the marketplace grows from this seed), PRD-001
  (links pin tags), PRD-011 (registry funnel), PRD-012 (configs in the
  customizer), PRD-017 (glTF exporter)

> **Design note (2026-08-18).** This PRD now rides the shipped hosted core
> (PRD-005a), not the deferred PRD-005/006, and the design spot-checked its
> assumptions against the real tree. The
> [design spec](../../superpowers/specs/2026-08-18-share-links-customizer-design.md)
> and [plan](../../superpowers/plans/2026-08-18-share-links-customizer.md)
> record the following divergences to fold in when the feature is picked up
> (PRD stays `pending` until then): the customizer rebuild is a **`GET`** of a
> content-addressed variant, not a POST — it is a pure read (owner state never
> changes), which makes CSRF moot and cross-origin embedding work by
> construction (settles FR6's guard question); publication/link state lives in
> the **PRD-005a state dir** (`<state-dir>/publications/`, one shared space),
> not "PRD-005's storage", with a **store-backed sha256** capability token
> rather than an HMAC-signed one (immediate revocation, the reason 005a rejected
> JWTs); the immutable pin is a **copy** of the script bytes at a resolved tag
> into a **muzzled build service** under the state dir, so the visitor path
> never touches a user `ProjectStore`; the viewer streams the shipped **ACM**
> format (reusing `viewport.js::parseACM`) and **`core/gltf.py` is deferred to
> PRD-017** to avoid build-then-migrate churn (matching this PRD's own risk
> note); the surface is **two** route packs (`routes_share.py` at `/api`,
> `routes_share_public.py` at the root) because a pack carries one `PREFIX`; and
> PRD-006 is **not required for our own content** (005a Decision 2). The one
> open founder call folded from design: `/embed/` `frame-ancestors` default
> (open `*` vs a per-publication allowlist) and the default link expiry.

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
  share `affinity=` key (the pool-routing seam) so visitor load can be
  segregated; results live in a shared variant cache with LRU/GC.
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

## Risks & open questions

- **Stranger compute** is the existential risk — a popular link is a
  free rebuild farm. Defense in depth: the variant cache (popular =
  cheap), token buckets, PRD-006 CPU/memory/wall caps, pool-affinity
  segregation. Open: a per-deployment policy for requiring login above a
  rebuild threshold.
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
