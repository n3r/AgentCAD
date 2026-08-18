# Share links & customizer publishing (PRD-007) — design

- **PRD:** [PRD-007](../../prd/pending/PRD-007-share-links-customizer.md)
- **Date:** 2026-08-18
- **Roadmap position:** **step 3 of the marketplace chain**
  ([roadmap.md](../../roadmap.md), "Sequencing decision — the marketplace
  chain"). Step 1 (PRD-011) and step 2 (PRD-005a) are done; this is the growth
  loop and PRD-031a's own hard dependency.
- **Builds on (completed):** PRD-005a (the hosted core — `server/security.py`'s
  allowlist, the `PREFIX` mount seam, `presence.TokenBucket`, and the recorded
  Decision 2 verdict this design consumes) · PRD-001 (immutable version tags —
  the pin) · PRD-011 (content addressing — the variant cache key) · PRD-004
  (the muzzled ephemeral-service pattern — the visitor-build containment) ·
  PRD-012 (the pure `normalize_params` seam and the config-build path that
  builds without writing the store).
- **Explicitly does NOT depend on PRD-006.** That is the whole risk of this
  slice, and it is the customizer's kernel path — the first anonymous request
  that legitimately reaches `exec()`. Decision 4 pays for it honestly. PRD-005a
  Decision 2 already ruled the shape defensible; this design states the exact
  caps and the residual 006 still owes.
- **Plan:** [2026-08-18-share-links-customizer.md](../plans/2026-08-18-share-links-customizer.md)

---

## Problem

Nothing made in AgentCAD can be shown to anyone without an account. PRD-005a
shipped the hosted instance a stranger can open and *proved its anonymous
surface makes zero kernel calls* (`tests/test_hosted_surface.py`,
`routes_public.py`). PRD-007 is the feature that deliberately opens the first
hole in that invariant: a logged-out visitor moves a slider and the server
rebuilds real B-rep geometry. Everything in this design is downstream of doing
that safely.

Three questions decide the rest, and PRD-005a already answered the first:

1. **Is a bounded-param rebuild of a member-authored script defensible before
   PRD-006?** Yes — PRD-005a Decision 2 recorded the verdict so this PRD would
   not re-derive it: bounded PARAMS values are **data passed to `build(p)`,
   never code**, so a visitor gains no code execution; what they gain is
   *compute against someone else's script*, which the caps below bound. This
   design's job is to state the caps, the param-validation parity, and the
   variant cache precisely, and to name what 006 still owes (memory/pid caps,
   egress denial) as the honest residual.
2. **Where does a published link live,** given it is a capability that outlives
   branch switches and must pin an immutable snapshot? Not project data
   (Decision 3).
3. **Are read-only viewer links and customizer links two features or one?** One
   object with a capability flag, and the anonymous-surface equality test
   covers both (Decision 2, Decision 5).

---

## Architecture at a glance

```
        anonymous (capability token in the path)              authenticated (owner)
                        │                                              │
   ┌────────────────────┴───────────────────────┐        ┌────────────┴───────────────┐
   │ GET /s/<token>            viewer page       │        │ POST /api/share  share_create│
   │ GET /embed/<token>        embed page        │        │ GET  /api/share  share_list  │
   │ GET /s/<token>/model      attribution+keys  │        │ DELETE /api/share/{id}       │
   │ GET /s/<token>/mesh/{key} .acm  (no build)  │        │ (cookie or bearer, member)   │
   │ GET /s/<token>/params     the slider spec   │        └────────────┬───────────────┘
   │ GET /s/<token>/variant?…  THE kernel path   │                     │
   │ GET /s/<token>/download/{fmt}?…             │                     │
   │ GET /s/<token>/script     if show_script    │                     │
   └────────────────────┬───────────────────────┘                     │
                        │  server/security.py: PUBLIC_PREFIXES += "/s/","/embed/"   │
                        │  everything under /s/ and /embed/ validates the token     │
                        ▼                                              ▼
        server/routes_share_public.py  (PREFIX = "")      server/routes_share.py (/api)
                        │                                              │
     ┌──────────────────┴───────────────┐                 core/publications.py
     │ per-link + per-IP TokenBucket     │                   PublicationStore
     │ global BoundedSemaphore(in-flight)│                   <state-dir>/publications/
     │ variant cache (content-addressed) │                     store.json  (atomic JSON + flock)
     └──────────────────┬───────────────┘                     scripts/<sha>.py   (pinned bytes)
                        ▼                                       build/  (a muzzled AgentCADService
             shared kernel pool  ── affinity="share:<pub>"      rooted here: bus.on_publish=None,
             (same workers members use, segregated by affinity) branch_resolver=None, write_guard=None)
```

The visitor path never touches a user's `ProjectStore`, `.history`, or
`ProjectStore.write_guard`. The pinned script is copied **out** of the project
at publish time into a build store under the state dir; every visitor build
runs against that copy in a service muzzled by the PRD-004 recipe
(`checks._ephemeral_service`, `checks.py:801-851`). Isolation is therefore a
property of *construction*, exactly as PRD-005a made identity state unaffected
by `--projects-dir`.

---

## Decision 1 — one `Publication` object with a `customizer` capability flag; a viewer link cannot be escalated

A **viewer link** exposes a rendered part (mesh + metrics + attribution, no
kernel on the visitor path — the `routes_public.py` posture). A **customizer
link** additionally exposes sliders that rebuild. These are the *same object*
with one boolean:

```jsonc
// one record in <state-dir>/publications/store.json
{
  "pub_id":     "a1b2c3d4",              // 8 hex, the management handle
  "token_digest": "sha256:…",           // the URL secret is stored as a digest, never raw
  "scope":      "part",                  // "part" (MVP) | "project" (Phase 2)
  "project":    "rocketry",              // resolved at publish, then irrelevant to the visitor path
  "part_id":    "nozzle",
  "ref":        {"kind": "tag", "name": "v3", "commit": "9f2c…"},   // the pin
  "script_sha": "sha256:…",             // the pinned script bytes, content-addressed (PRD-011)
  "settings": {
    "customizer": true,                  // ← the capability flag (Decision 5's escalation boundary)
    "exports":    ["step", "stl"],       // the export mask; [] = view-only downloads
    "show_script": false,
    "expires":    null,                  // epoch seconds, or null = until revoked
    "config":     null                   // RESERVED for PRD-012 named configurations (FR13)
  },
  "created_by": "nikita",                // attribution (FR4)
  "created":    1755500000,
  "revoked":    false,
  "default_variant_key": "7c1e…",        // the pinned-default mesh cache key, pre-warmed at publish
  "counters":   {"views": 0, "rebuilds": 0, "downloads": 0}
}
```

**Escalation is structural, not a check to remember.** The `/s/<token>/variant`
and `/download` handlers return `404` when `settings.customizer` is false and
the export mask is empty — before touching the builder. A viewer link names a
publication whose `customizer` bit is off; there is no request shape that turns
it on, because the bit lives in the owner-written record, not the request. The
anonymous-surface equality test enumerates the `variant`/`download` routes as
public *templates* regardless of the flag (they exist on the router); the flag
is a per-record 404, one layer in.

*Rejected: two record types.* A `ViewLink` and a `CustomizerLink` would
duplicate the pin, the token, the counters and the export mask, and would make
"the same URL, now with sliders" a migration rather than a field flip. One
object, one capability bit.

---

## Decision 2 — the anonymous surface, route by route (extending PRD-005a Decision 8)

Everything under `/s/` and `/embed/` is public, because the capability token
lives in the path and every handler validates it. The additions to
`server/security.py` are **two prefixes** — nothing else:

```python
PUBLIC_PREFIXES = ("/api/public/", "/api/auth/enrol/", "/js/", "/css/",
                   "/vendor/", "/s/", "/embed/")   # ← the two new entries
```

`is_public` is `startswith` (`security.py:268-278`), so both **must** carry the
trailing slash: `/s` would make `/status` public and `/embed` would make
`/embedding` public. Each gets a negation test in
`test_paths_that_must_not_be_public` (`/s`, `/status`, `/svg`, `/embed`,
`/embedding`).

### Public in hosted mode — the PRD-007 additions

**K** marks a route that can reach `exec()` in the kernel worker. The whole
point of this feature is that exactly two rows carry a **K**, both gated,
capped, and param-validated.

| Method | Path (template) | Kernel | Why it is safe |
|---|---|---|---|
| GET | `/s/{token}` | — | HTML shell off disk; the model streams over the routes below |
| GET | `/embed/{token}` | — | ditto, plus `Content-Security-Policy: frame-ancestors *` and no credentials |
| GET | `/s/{token}/model` | — | attribution, metrics, and the default variant's mesh cache key(s); a JSON read of a pre-warmed sidecar |
| GET | `/s/{token}/mesh/{key}` | — | the `.acm` bytes for a key **already in the variant cache**; 404-if-absent, **never builds** — the `routes_configs.get_mesh_by_key` discipline (`routes_configs.py:179-210`, `:185` "this route never builds") |
| GET | `/s/{token}/params` | — | the inspected `params_spec` (slider bounds/choices); a cached JSON read |
| GET | `/s/{token}/script` | — | the pinned script text iff `settings.show_script`; else 404 |
| GET | `/s/{token}/variant?<p>` | **K** | the customizer rebuild; per-link + per-IP `TokenBucket`, a global in-flight semaphore, `normalize_params` parity, and the content-addressed variant cache in front (Decision 4) |
| GET | `/s/{token}/download/{fmt}?<p>` | **K** | a variant export honouring the mask; same caps; disabled formats 404 before the builder |

Eight route templates. Six make **zero** kernel calls and are covered by the
"viewer reaches zero kernel" test (AC7-style); two are the customizer path and
are covered by the "bounded and param-validated, with a positive control" test
(Decision 8). All eight are added to `EXPECTED_PUBLIC` in
`tests/test_hosted_surface.py`, and the set-equality assertion then fails if a
ninth `/s/` route is ever added without review.

### Management routes — authenticated, `/api`, never public

| Method | Path | Auth | Notes |
|---|---|---|---|
| POST | `/api/share` | member | `share_create`; mints the token, returns `{url, pub_id}` — the secret exactly once |
| GET | `/api/share?project=` | member | `share_list`; the owner's links + coarse counters, never the raw token |
| DELETE | `/api/share/{pub_id}` | member | `share_revoke`; immediate (the store is the authority) |

These sit in a **separate pack** (`routes_share.py`, default `/api` PREFIX,
inert without a `SecurityConfig` — the `routes_auth.py` precedent), because a
route pack carries exactly one `PREFIX` and the public pack mounts at the root
(`PREFIX = ""`). Two packs, one capability, mirroring `routes_auth.py` /
`routes_public.py`.

---

## Decision 3 — publication state lives in the state dir, and the pin is a copy, not a reference

**Not project data.** A share link outlives branch switches, `project_restore`,
and even deleting the source part; a token is a capability, not a path. So the
store lives beside identity state, on the exact PRD-005a derivation:

```
<state-dir>/publications/          # state_dir() == config_path().parent/"state", or $AGENTCAD_STATE_DIR
  store.json                       # {pub_id: record}, atomic write + fcntl.flock + mtime re-read
  scripts/<script_sha>.py          # the pinned script bytes, content-addressed
  build/                           # a project store the muzzled build service is rooted in
    parts/<pub_id>.py              # the pinned script, as a one-part project
    .cache/<key>.acm …             # the variant cache — content-addressed, shared across visitors
```

`core/publications.py::PublicationStore` is shaped exactly like
`core/authstore.py`: four-tuple `(st_mtime_ns, st_size, st_ino)` staleness
(`AGENTS.md` hosted-core gotcha), a random-staging `os.replace` atomic write,
and a reentrant `_scope()` holding a `threading.Lock` **plus** an `fcntl.flock`
on a `.lock` sibling — because `agentcad admin` and a future takedown CLI are a
second writer, the `LocalIndex._index_scope` situation
(`core/packages/indexes.py:502`). `fcntl` imported through `try/except
ImportError`; hosted mode is POSIX, local mode never constructs the store.

**The pin is a copy so a later owner edit cannot change what a live link
serves.** At publish (an authenticated, owner-paced action):

1. Resolve the ref to an immutable commit. Default: the current head is tagged
   (`BranchManager.tag`, `branches.py:637`) or an existing tag is resolved
   (`history.resolve_tag`, `history.py:665`, which peels annotated tags and is
   searched *before* branches — the PRD-001 X1 lesson `checks._resolve_ref`
   already encodes, `checks.py:2050`).
2. Read the part script bytes **at that commit** with a `cat-file blob` — no
   worktree — via the `packet._script_at` / `_blob` primitive
   (`packet.py:1245`, `:1257` → `history._run_bytes`, `history.py:183`), plus
   the manifest blob for the material.
3. Content-address the bytes (`sha256`, which is already the mesh-cache content
   signature, `service.py:654`; or PRD-011 `content.content_id_of`,
   `content.py:148`) → `script_sha`. Write `scripts/<script_sha>.py` and
   `build/parts/<pub_id>.py`.
4. Warm the default variant (Decision 4's build path at default params) and
   record `default_variant_key`.

The record stores `ref.commit` and `script_sha`; a later `write_script` on the
owner's working part changes neither. This is the honest reading of PRD-007
FR3: the default ref is an immutable tag and the link does not drift.

**Isolation checks pass by construction** (the PRD-005a Decision 10 argument,
reused): the store is derived from `config_path().parent`, never from
`--projects-dir`; `AGENTCAD_CONFIG` in a test isolates it for free;
`AgentCADService.__init__` never constructs it, so PRD-004/011 ephemeral
services are unaffected; `tar` of `/data` (which already carries
`/data/state`) is a complete backup because every write is an `os.replace`.

---

## Decision 4 — the customizer containment: what is bounded, and what PRD-006 still owes

This is the sharp part. A logged-out visitor's `GET /s/{token}/variant?…`
reaches `exec()`. Here is every wall around it.

### The visitor supplies data, validated to parity — never code

The query string carries the declared PARAMS only. Server-side validation is
**the authoring path's own logic, reused, not forked**:

- **Reject** wrong types, non-member enum choices, and unknown names with
  `core/service.py::normalize_params(spec, values)` (`service.py:375`) — the
  pure, store-free validator PRD-012 already reuses
  (`tools_configs.py:111`); it raises `ValidationError`, which the route maps
  to a structured `validation_error`.
- **Clamp** out-of-range numerics with warnings inside the build, in
  `worker._resolve_params` (`worker.py:226`) — the exact function every
  authored `build(p)` runs. The customizer builds anyway, so clamp-and-warn
  parity is not re-implemented; it is *inherited* from the build path. The
  returned `warnings` surface on the page.
- The spec fed to both is the cached inspected spec,
  `service._params_spec(script)` (`service.py:945`), whose typed entries
  (`number/int/bool/enum/string` with `min/max/choices/max_len`) are fully
  implemented today (`worker.py:67`, `:485`; `docs/part-authoring.md:42-71`) —
  the PRD's assumed control set needs no new typing work.

There is no `eval` of visitor input anywhere on this path. The visitor does
**not** gain code execution — PRD-005a Decision 2's categorical distinction
from "a stranger uploads a script", which stays refused until PRD-006.

### Four caps, each an existing primitive

| Threat | Cap | Mechanism |
|---|---|---|
| A single expensive build (a param driving an O(n³) loop, a tessellation blow-up) | per-request timeout + kill-and-respawn | the kernel `KernelClient` lifecycle, unchanged (`docs/architecture.md:38-40`) |
| One visitor hammering rebuilds | per-link **and** per-IP token buckets | `TokenBucket.take` (promoted to `core/ratelimit.py`, Decision 6); over-limit → `quota_exceeded` + `retry_after_s`, page degrades to view-only |
| Many visitors exhausting the shared kernel pool (a popular link starving signed-in members) | a **global in-flight semaphore** `BoundedSemaphore(SHARE_MAX_INFLIGHT)`, acquired non-blocking around the *build call only*, sized **below** the pool | over-limit → `quota_exceeded`; and `affinity="share:<pub_id>"` routes visitor builds onto a segregated pool slice (`kernel/pool.py` `_pick`) so a customizer flood cannot poison a member's worker |
| Repeated identical slider stops | the content-addressed variant cache | keyed on `(script_sha, params, density)` via the existing `_cache_key` (`service.py:713`); a second visitor at the same param set is served from disk with **zero** kernel calls — PRD-007 AC2 |

The variant cache is why "popular = cheap": the first visitor at a slider stop
pays one build; everyone after pays a file read. `SHARE_MAX_INFLIGHT` is what
keeps the *worst* case — a coordinated flood of distinct param sets — from
being more than N concurrent builds, and `affinity` keeps those N off the
members' workers.

### Output artifacts

`settings.exports` is a subset of `{step, stl, 3mf}` for MVP (B-rep + mesh via
the existing `service.export` paths), with `drawing` (SVG) and `flat` (flat
pattern) added in Phase 2 where the script defines them. A variant downloads as
`<part>_<hash8>.<ext>` where `hash8` is the variant cache key prefix. A format
absent from the mask is absent from the page **and** 404s at the route before
the builder runs.

### What a malicious visitor can still do, stated plainly

CPU burn, within the caps: distinct param sets, each a bounded build, at the
token-bucket rate, at most `SHARE_MAX_INFLIGHT` at once, on a segregated pool
slice. That is a bounded compute cost the operator accepts.

**What PRD-006 still owes, honestly** (the residual PRD-005a Decision 2 named):
memory caps (a params-driven mesh can still balloon RSS and OOM the host),
process/pid caps (a fork in an author's script), disk budget on the variant
cache, and network-egress denial from the worker on Linux. Until 006, the
mitigation for the extreme is **operator posture**, and this design ships the
knob PRD-005a floated: `AGENTCAD_SHARE_REQUIRE_LOGIN_ABOVE=<n>` (default unset)
makes `/variant` return `401` once a per-IP hourly threshold is crossed,
degrading anonymous customizing to a login wall on a link under attack without
taking the viewer offline. Off by default; documented in `docs/deployment.md`.

---

## Decision 5 — the security posture: what a token grants, and strictly nothing more

A share token names **one publication** — one part, at one immutable ref, read
only. It is not a project handle, not the owner's other parts, not a write, not
the owner's identity. Concretely:

- **The token is a capability, stored as a digest.** Format `shr_<pub_id8>_<secret43>`
  (`secrets.token_urlsafe(32)` ≥ 128 bits); the server stores
  `sha256(token)` and compares with `hmac.compare_digest` — the PRD-005a bearer
  shape (`authstore` tokens), for the same reason JWTs were rejected there:
  the store is the authority, so revocation is immediate. `resolve_share`
  splits on `_`, looks up `pub_id`, and digest-compares — O(1), no scan.
- **In the path, never a query param.** A query-string token lands in access
  logs and `Referer`; the token goes in `/s/<token>`. The share page also sets
  `Referrer-Policy: no-referrer` so a click off the page cannot leak the
  capability, and — unlike the enrolment token, which PRD-005a strips from the
  URL with `history.replaceState` because it is a one-time credential — the
  share token **stays** in the URL, because the URL *is* the shareable artifact.
- **Revoked, expired, and unknown tokens are one indistinguishable 404**, with
  the cache header, exactly as `routes_public._miss` does it
  (`routes_public.py:128-140`) — no existence oracle over what is published.
- **CSRF is moot, because the rebuild is a `GET`.** The one genuinely new
  security question is the customizer POST the PRD imagined: an anonymous
  state-changing request that must also work cross-origin (an embed on
  `forum.example.com` posting to `cad.example.com`). PRD-005a's guard refuses
  anonymous cross-origin unsafe methods (the finding-M1 Origin check,
  `security.py:402-408`), which would break embedding. **This design makes the
  variant and download routes `GET`** (Decision 8's divergence): a variant is a
  *pure read* of a content-addressed artifact — the owner's state never
  changes — so `GET` is honest, `SameSite`/Origin never applies to a safe
  method, cross-origin embedding works by construction, and the response is
  CDN-cacheable. No Origin exemption is added to the guard; the only
  `security.py` change is the two prefixes. (A token-bearing cross-origin
  request is embedding by design; a capability in the URL is not ambient
  authority, so there is nothing for CSRF to steal even if it were a POST — but
  `GET` removes the question entirely.)
- **Abuse & takedown.** A published link is public by definition. The owner
  sees exactly what is published (`share_list` with coarse counters) and can
  `share_revoke` any link instantly. Counters are three coarse integers
  (page-views, rebuilds, downloads), no visitor PII — the popularity signal
  PRD-031 inherits. View counting is the `/s/{token}` page load only (human-
  paced, one store write), not every asset fetch, so a mesh flood does not
  become a write flood.

---

## Decision 6 — promote `TokenBucket` to `core/ratelimit.py`

PRD-007 is `TokenBucket`'s second consumer (PRD-005a's login limiter is the
first) and the roadmap calls for the promotion. The move is a **pure
refactor**:

- `core/ratelimit.py` holds `TokenBucket` verbatim (the class from
  `presence.py:137-217`, unchanged behaviour and constructor
  `TokenBucket(rate=, burst=, clock=, limit=)`).
- `core/presence.py` keeps the name importable with one line —
  `from .ratelimit import TokenBucket` — so nothing in PRD-008 changes shape and
  the presence suite is the regression test.
- `server/security.py` switches its import from `presence` to `ratelimit`; its
  behaviour is byte-identical.
- The existing `TokenBucket` tests move to `tests/test_ratelimit.py`; a new
  test asserts `presence.TokenBucket is ratelimit.TokenBucket`.

The share limiter keys are `share:<pub_id>` and `addr:<client_host>`. The
address is only honest behind the documented reverse proxy — the same caveat as
login, and the same plumbing (`appmode.trusted_proxy`, uvicorn
`forwarded_allow_ips`); a per-visitor limit is only as real as the trusted-proxy
config, and the deployment guide's nginx/Caddy block already sets
`X-Forwarded-For`. Stated in the design so it is not rediscovered.

> **⚠️ Flagged edit to a PRD-008 file.** `presence.py` gains exactly one line (a
> re-export). It is the only touch to a finished PRD-008 module, it changes no
> behaviour, and it is what the roadmap's "007 should promote it" instruction
> asks for. Every other rate-limit consumer imports from `core/ratelimit.py`.

---

## Decision 7 — the visitor build path reuses `_build_with`, muzzled, never touching a user project

The customizer does not re-implement building. A dedicated, long-lived
`AgentCADService` — the **publications build service** — is rooted at
`<state-dir>/publications/build/` and muzzled with the PRD-004 recipe
(`checks._ephemeral_service`, `checks.py:801-851`): `bus.on_publish = None`
(no history snapshots), `store.branch_resolver = None`, `store.write_guard =
None` (both after `build_registry`, the PRD-004 ordering). It shares the main
kernel pool (`service.kernel`).

A variant build is the PRD-012 configuration-build shape
(`_ensure_config_built`, `service.py:751` → `_build_with(record, affinity,
status_key=None, config=…)`, `service.py:804`): a `PartRecord` derived with
`dataclasses.replace` carries the visitor's validated params, `status_key=None`
means **no badge or state is written**, and the result is `{cache_key,
metrics, warnings, lods}` — the PRD's proposed `build_variant → {mesh_key,
metrics}` with no new kernel code. The pinned script is the part's own bytes
(`build/parts/<pub_id>.py`), so `_content_signature` hashes the pinned bytes,
not a working tree.

Because the build store is under the state dir and outside every user project,
the isolation is doubled: even without the muzzles a build here could not reach
a user repo, and with them it writes only its own content-addressed `.cache/`.
The viewer's `/mesh/{key}` route serves those `.acm` files with the
`get_mesh_by_key` 404-if-absent contract (`routes_configs.py:185`) — a pure
file read, no kernel, which is what makes AC "a viewer link reaches zero
kernel" true.

**No `core/gltf.py` in MVP.** The slim viewer bundle reuses `viewport.js`'s
already-exported `parseACM` (`viewport.js:74`) + `buildGeometry` and streams
the `.acm` bytes directly — the format is OCP-free (`acm.py:15`) and the
frontend already parses it. PRD-007's proposed `core/gltf.py` is deferred to
**PRD-017**, which owns the full glTF exporter (materials, units); building a
throwaway converter now only to migrate it later is churn the PRD's own risk
note ("hand ownership to 017 when it lands") already anticipates. This removes a
module from the critical path (Decision 8 divergence). `core/render.py` is the
OCP-free precedent if a server-side raster ever helps a share preview.

---

## Decision 8 — divergences from PRD-007 as written

| PRD-007 says | This design does | Why |
|---|---|---|
| FR8/AC2 — the rebuild is a **POST** | the variant and download routes are **`GET`** with params in the query | A variant is a pure read of a content-addressed artifact (owner state never changes); `GET` makes CSRF moot, makes cross-origin embedding work by construction, and is CDN-cacheable. It also means the only `security.py` change is the two public prefixes — no Origin-check exemption (Decision 5). |
| FR5 / technical approach — stream **glTF**, build `core/gltf.py` now | stream **ACM**, reusing `viewport.js::parseACM`; `core/gltf.py` deferred to PRD-017 | The ACM pipeline is shipped and OCP-free; a minimal glTF module now is build-then-migrate churn the PRD's own risk note assigns to 017 (Decision 7). |
| FR1 — link store is "tenant-scoped records in **PRD-005's storage**", token **HMAC-signed** | records in `<state-dir>/publications/` (the PRD-005a state dir, one shared space), token a **store-backed sha256 digest** | 005-full tenancy does not exist; 005a is the substrate. A digest in the authoritative store gives immediate revocation (the reason 005a rejected JWTs); no signing key is needed because nothing is verified offline. |
| Technical approach — one pack `routes_share.py` | **two** packs: `routes_share.py` (`/api`, authenticated) + `routes_share_public.py` (`PREFIX=""`, anonymous) | A route pack carries one `PREFIX`; the public surface mounts at the root. Mirrors `routes_auth.py`/`routes_public.py`. |
| Technical approach — `build_variant` writes "a shared variant cache with LRU/GC" | the variant cache is the existing content-addressed `.cache/` of a muzzled build service | Reuses `_build_with`'s cache, LOD tiers, metrics/faces sidecars, and export paths unchanged (Decision 7). GC is a disk-budget concern deferred with the other 006 residuals; a simple size-capped sweep is a Phase-2 slice. |
| Risks — "per-deployment policy for requiring login above a threshold" is *open* | shipped as `AGENTCAD_SHARE_REQUIRE_LOGIN_ABOVE`, off by default | It is the honest pre-006 backstop for a link under a distinct-param flood (Decision 4). |
| MVP — project-scope, embeds, drawings, counters all in MVP | MVP is **part-scope view+customizer, STEP/STL, revoke/expiry, counters**; project/assembly scope, embeds-with-sliders, drawings/flat-pattern, script visibility, branch-following are **Phase 2** | Matches the PRD's own MVP paragraph; the assembly viewer needs a whole-project pin, which is a larger containment surface. |

---

## Decision 9 — threat model summary (what this feature adds to PRD-005a's)

PRD-005a's anonymous surface made zero kernel calls. This feature adds exactly
two kernel-reaching anonymous routes and bounds them:

- **The first anonymous `exec()`** is `GET /s/{token}/variant`. Bounded by:
  param-validation parity (no code, only clamped data), per-link + per-IP token
  buckets, a global in-flight semaphore sized below the pool, pool-affinity
  segregation from members, the content-addressed variant cache, and the
  kernel's per-request timeout. Residual: the 006 list (memory/pid/disk/egress),
  named, with the login-gate knob as the pre-006 backstop.
- **The guard carve-out** is two prefixes in one file, defended by the
  set-equality enumeration test — a ninth `/s/` route cannot go public
  unreviewed.
- **Bearer-capability leakage**: a leaked `/s/<token>` URL is view/customize
  access to one pinned part. Mitigated by instant revocation, optional expiry,
  the no-write role, and the export mask; documented plainly ("anyone with the
  link can view").
- **No new residual for the owner's data**: the visitor path never reads or
  writes a user `ProjectStore`, because the pin is a copy under the state dir
  (Decision 3, Decision 7). AC "the owner's manifest/params/history/.cache are
  byte-unchanged after a rebuild flood" is a property of construction.

---

## Surfaces

**Tools (3 new + 1 event),** registered by `core/tools_share.py` **only when a
`SecurityConfig` is present** (the FEM/`whoami` precedent — a share link is
meaningless on a loopback instance with no public origin):

- `share_create {project, scope, part_id?, ref?, customizer?, exports?, show_script?, expires_days?}` → `{url, pub_id}`
- `share_list {project}` → `{links: [{pub_id, scope, part_id, ref, settings, counters, created}]}`
- `share_revoke {project, pub_id}` → post-state `{revoked: true}`
- event `share_changed {project}` on create/revoke.

**Routes.** `routes_share.py` (`/api`, authenticated): the three management
routes above. `routes_share_public.py` (`PREFIX=""`, anonymous): the eight `/s/`
and `/embed/` routes of Decision 2.

**Errors** (the house contract, added as `AppError` subclasses if not already
present): `validation_error` (bad scope/mask/param) → 422, `not_found`
(bad/revoked/expired token, disabled export) → 404, `quota_exceeded` (details
carry `retry_after_s`) → 429 — reusing PRD-005a's `RateLimitedError`.

**Frontend.** A slim standalone bundle `frontend/share.html` + `frontend/js/share.js`
reusing `viewport.js`'s `parseACM`/`buildGeometry`/`showPart` rendering core
plus a param-panel module (no CodeMirror, no inspector, no `TransformControls`
— page weight matters for embeds). The main app gains a "Share…" dialog and a
"Links" panel (`share_list` + revoke) in `frontend/js/`.

**CLI (optional, Phase 2).** `agentcad admin share list|revoke` operating
directly on `PublicationStore(state_dir()/"publications")` — the `agentcad
admin` precedent, for takedown over `docker compose exec`.

**Deployment.** `docs/deployment.md` gains a "Share links & the customizer" row
in "What a stranger can reach" (the two prefixes, the two kernel-reaching
routes and their caps), the `AGENTCAD_SHARE_*` env vars
(`AGENTCAD_SHARE_MAX_INFLIGHT`, `AGENTCAD_SHARE_REQUIRE_LOGIN_ABOVE`), and the
`X-Forwarded-For` note (the per-IP limit is only real behind the trusted proxy).

---

## Acceptance criteria (crisp, testable, in the PRD-005a mould)

- **AC1.** A part share link opens for a logged-out visitor: the viewer,
  metrics, and attribution render with **no auth cookie on any response**
  (browser session against a hosted instance — graded as evidence if no Chrome
  extension is available, the PRD-005a AC3 precedent).
- **AC2.** The customizer slider rebuilds live and metrics update; a **second**
  visitor requesting the same variant is served from the variant cache —
  **exactly one** kernel build for the two requests (test with the
  `kernel_counter` fixture).
- **AC3.** A `GET /s/<token>/download/step` of the visitor's variant succeeds
  when the mask allows; a disabled `stl` is absent from the page **and 404s at
  the route** before any build (test).
- **AC4.** Param-validation parity with the authoring path: an out-of-range
  numeric clamps with a warning, a non-member enum choice is rejected, an
  unknown name is rejected — asserted against the **same** `normalize_params` /
  `_resolve_params` the editor uses (parity test).
- **AC5.** Hammering `/variant` past the per-link limit returns `quota_exceeded`
  with `retry_after_s` and the page degrades to view-only; throughout, the
  **owner's** manifest, params, history, and `.cache/` are byte-unchanged
  (store-equality test) — and a global in-flight flood never exceeds
  `SHARE_MAX_INFLIGHT` concurrent builds (test).
- **AC6.** Revoked and expired links both 404 with **indistinguishable** bodies
  (test), and a viewer-only link (`customizer:false`) 404s on `/variant` before
  the builder (test — the escalation boundary).
- **AC7 (the invariant).** The **viewer** routes (`/s/{token}`, `/model`,
  `/mesh/{key}`, `/params`, `/script`, `/embed/{token}`) make **zero** kernel
  calls over a full sweep (kernel-silence test with a positive control); and the
  **customizer** routes reach the kernel **exactly once per fresh param set,
  zero on a repeat** (the bounded positive-control test).
- **AC8.** A tag-pinned link keeps serving the tagged geometry after the source
  branch moves on and the owner edits the working part (test over PRD-001 refs +
  a `write_script`).
- **AC9 (the surface).** `tests/test_hosted_surface.py`'s set-equality
  enumeration, grown to include the eight `/s/`+`/embed/` templates and their
  negation tests (`/s`, `/status`, `/svg`, `/embed`, `/embedding`), is the only
  guard-exempt surface — it fails when a new route goes public by accident; full
  suite green.
- **AC10.** The embed iframe renders and orbits on a page served from a
  **second** local origin (browser test — the growth-loop clause; graded as
  evidence if no Chrome extension is available).

---

## Risks & open questions

- **The defining risk is Decision 4's:** the customizer is the first anonymous
  `exec()`. It is accepted, capped, and its residual is named — not hidden.
  Until PRD-006 the extreme (a memory balloon) is bounded only by operator
  posture and the login-gate knob.
- **`SHARE_MAX_INFLIGHT` sizing** trades customizer responsiveness against
  member starvation. Default conservative (e.g. 2), overridable; the affinity
  segregation is the second layer.
- **Counter write amplification** — coarse counters under flock; mitigated by
  counting page loads, not asset fetches, and accepting best-effort integers.
- **Founder decisions (18 Aug 2026), now settled:**
  - `/embed/` ships **`frame-ancestors *`** — any site may embed the public,
    auth-free, write-free customizer; the growth loop wins over an allowlist a
    public read-only surface gains little from.
  - **The main app now sends `frame-ancestors 'none'`** — the authenticated
    surface must not be frameable. This is a new, separable hardening of the
    hosted guard; it is a slice-1 item (a response header on the non-share
    routes), and the anonymous-surface equality test must not treat it as a new
    reachable route (it is a header, not a path).
  - Link expiry defaults to **never, revocable** — `expires: null` until the
    owner revokes; per-link `expires_days` stays available as an opt-in and is
    additive, never breaking a link already in the wild.
  - The login-above-N gate ships **off** (`AGENTCAD_SHARE_REQUIRE_LOGIN_ABOVE`
    unset): the in-flight cap + per-link/per-IP buckets + variant cache suffice
    for our own content pre-006, and the knob is the operator's escape hatch,
    not a default posture.
  - Viewer links need **no account** — confirmed against the PRD's non-goals
    (an unlisted capability URL is the whole point).

---

## Naming traps (live collisions)

- **`token`** now has a fourth sense. PRD-005a already named three (agent
  bearer, `TokenBucket`, enrolment). The share capability is a **`share_token`**
  in variable names; the record handle is **`pub_id`**; the rate-limit tokens
  stay `bucket`.
- **`scope`** — a publication's `part|project` scope is a new sense beside the
  package-index `public|private` scope and `locks.write_scope`. Qualify it
  `share_scope` in code.
- **`publish`** — PRD-011 `LocalIndex.publish` is package publishing; PRD-007
  "publish a part" is share creation. The store is `publications`, the tool is
  `share_create` (never `publish`), to keep them apart.
- **`variant`** — a share variant (a visitor's param set) is not a PRD-012
  configuration (a named, owner-authored set), though both build through the
  same `_build_with(status_key=None)` path.
- **`security.py` vs `routes_share_public.py`** — the guard authorises the
  request (is the path public); the pack authorises the *capability* (does the
  token resolve to a live publication). Two layers; the first is coarse
  (default-deny prefix), the second is fine (per-record 404).
