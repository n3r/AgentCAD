# Share links & customizer publishing (PRD-007) — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: use
> `superpowers:subagent-driven-development` (recommended) or
> `superpowers:executing-plans` to work through this plan slice by slice.
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** ship [PRD-007](../../prd/pending/PRD-007-share-links-customizer.md) as
**step 3 of the marketplace chain** — read-only viewer links and published
customizers with real, bounded kernel rebuilds emitting B-rep artifacts — per
[the design spec](../specs/2026-08-18-share-links-customizer-design.md).

**Architecture (one paragraph):** a `Publication` is a capability that lives in
the **state dir**, never in project data, shaped exactly like PRD-005a's auth
store (`core/publications.py`: atomic JSON + `fcntl.flock` + mtime re-read).
Publishing pins an immutable snapshot by resolving a PRD-001 tag and copying the
part script bytes out with a `cat-file blob` read (`packet._script_at`), into a
one-part build store under the state dir that a **muzzled** `AgentCADService`
(the PRD-004 `checks._ephemeral_service` recipe) builds against — so the visitor
path never touches a user `ProjectStore`. The anonymous surface is two new
prefixes in `server/security.py` (`/s/`, `/embed/`) and one root-mounted route
pack (`routes_share_public.py`, `PREFIX=""`); a viewer link serves
content-addressed `.acm` with **zero** kernel calls (the `get_mesh_by_key`
404-if-absent discipline), and the customizer's `GET /s/<token>/variant` is the
one bounded anonymous `exec()` — param-validated with the authoring path's own
`normalize_params`/`_resolve_params`, rate-limited by a promoted
`core/ratelimit.TokenBucket`, and capped by a global in-flight semaphore with
pool-affinity segregation. Management (`share_create/list/revoke`) is an
authenticated `/api` pack + tools registered only in hosted mode. The frontend
is a slim standalone bundle reusing `viewport.js`'s `parseACM` rendering core.

**Tech stack:** Python 3.12 stdlib only (`secrets`, `hmac`, `hashlib`, `fcntl`,
`json`, `threading`) / FastAPI route packs / pytest with `TestClient`. **No new
runtime dependency.** No new kernel files; **no `OCP`/build123d import
anywhere** in this plan.

---

## Global constraints (encode these in every slice)

- **Only `agentcad/kernel/` may import `OCP`/build123d.** This plan adds zero
  kernel files and imports no geometry. The viewer streams ACM (OCP-free,
  `acm.py:15`); no `core/gltf.py` is built (deferred to PRD-017, design
  Decision 7).
- **The visitor path never touches a user `ProjectStore`.** The pin is a copy
  under `<state-dir>/publications/`; builds run in a service muzzled by the
  PRD-004 recipe (`bus.on_publish=None`, `store.branch_resolver=None`,
  `store.write_guard=None`, the last two **after** `build_registry`). AC5/AC8
  are properties of construction.
- **Default deny stays default deny.** The only `server/security.py` change is
  two entries in `PUBLIC_PREFIXES` (`"/s/"`, `"/embed/"`), each **with its
  trailing slash** (`security.py:80-86`; `is_public` is `startswith`). No
  per-route `@public` decorator, ever. Every added public route grows
  `tests/test_hosted_surface.py::EXPECTED_PUBLIC` in the same slice.
- **The customizer rebuild is a `GET`** (design Decision 5/8), so the guard's
  CSRF Origin check never applies and no Origin exemption is added. Params ride
  the query string.
- **Reuse the authoring validators, do not fork them.** Reject with
  `core/service.py::normalize_params` (`service.py:375`); clamp-and-warn comes
  from the build via `worker._resolve_params` (`worker.py:226`). The spec is
  `service._params_spec` (`service.py:945`).
- **Reuse the build path, do not re-implement it.** Variants build through
  `_build_with(record, affinity="share:<pub_id>", status_key=None)`
  (`service.py:804`) on the muzzled publications service — the PRD-012
  `_ensure_config_built` shape (`service.py:751`). No new kernel code.
- **State path derivation is `appmode.state_dir()`** (`config_path().parent`),
  never `--projects-dir`. Setting `AGENTCAD_CONFIG` isolates the store in every
  test. `AgentCADService.__init__` never constructs it — PRD-004/011 ephemeral
  services stay unaffected.
- **Tokens are unguessable and stored as digests.** `secrets.token_urlsafe(32)`
  (≥128 bits); store `sha256`; compare with `hmac.compare_digest`. The token is
  in the URL **path**, never a query param. Revoked/expired/unknown → one
  indistinguishable 404 with `Cache-Control` (the `routes_public._miss` shape,
  `routes_public.py:128-140`).
- **`presence.TokenBucket` is promoted, not re-implemented** (design Decision
  6): moved to `core/ratelimit.py`, re-exported from `presence.py` (one line,
  behaviour byte-identical). Do not edit any other PRD-008/001/002/003/004/011
  file.
- **Errors are the house contract** `{error: {type, message, details}}`:
  `validation_error` (422), `not_found` (404), `quota_exceeded` (429 with
  `details.retry_after_s`, reusing PRD-005a's `RateLimitedError`).
- **Hosted-only registration order** (the PRD-005a gotcha): `security.install`
  before `build_registry`; tools register only when
  `security.current_config()` is not None; routers capture their config at
  **mount** time, not per request.
- Never `uv sync` / `uv pip install` from a parallel agent; scratch venv only.
  `TestClient(base_url="http://127.0.0.1")` /
  `create_app(..., extra_allowed_hosts={"testserver"})`; the `hosted`,
  `hosted_client`, `hosted_app`, `kernel_counter`, `hosted_with_catalog` and
  `login` fixtures already exist in `tests/conftest.py:148-337`.
- **Baseline:** record the real `make test` pass count on this branch before
  slice 1 and cite it in every slice's verification. No unexplained skips.
- **Verification before completion, every slice:** run the named commands and
  cite their real output. "Should pass" is not a result.

---

## Slice map

**Six slices.** PRD-005a needed eight because it built identity storage, a
guard, auth routes, tokens, hardening, deployment, public read *and* CI from
nothing. PRD-007 inherits 005a's guard, allowlist, deployment and CI, and
001/011/012's pin + build + validate machinery, so it ships one storage module
(plus a `TokenBucket` move), one pin/publish path, two route packs, one slim
frontend bundle, and docs. The ordering is the design's dependency order —
**the store before the thing it holds, publishing before the viewer that reads
a publication, the provably kernel-free viewer before the kernel-reaching
customizer, the UI after the API it drives, docs and acceptance last**:

| # | Slice | Deliverable |
|---|---|---|
| 1 | Foundations | `core/ratelimit.py` (+ `presence` re-export), `core/publications.py` — no server |
| 2 | Publishing | `routes_share.py`, `core/tools_share.py`, the pin + muzzled build service, `share_create/list/revoke` |
| 3 | The viewer link (kernel-free) | `routes_share_public.py` viewer routes, the two `security.py` prefixes, the surface + kernel-silence tests |
| 4 | The customizer rebuild + caps | `/variant` + `/download`, param parity, buckets + semaphore + affinity, export mask, the login-gate knob |
| 5 | Frontend & embed | `share.html` + `js/share.js` (reusing `parseACM`), the embed frame-ancestors, the Share dialog + Links panel |
| 6 | Docs, acceptance, moves | `deployment.md`, AGENTS.md gotchas, PRD/roadmap moves, changelog, AC verification |

---

## Slice 1 — foundations: the rate-limit module and the publication store

**Goal:** a promoted `TokenBucket` and a fully tested publication store that no
server imports yet.

### Files
- Create: `agentcad/core/ratelimit.py`, `agentcad/core/publications.py`
- Modify: `agentcad/core/presence.py` (one re-export line — the only PRD-008
  touch, design Decision 6)
- Test: `tests/test_ratelimit.py`, `tests/test_publications.py`
- Move: the existing `TokenBucket` tests out of the presence test module into
  `tests/test_ratelimit.py`.

### Interfaces
```python
# agentcad/core/ratelimit.py  — TokenBucket verbatim from presence.py:137-217
class TokenBucket:
    def __init__(self, rate=..., burst=..., clock=time.time, limit=...): ...
    def take(self, who: str) -> bool: ...
    def forget(self, who: str) -> None: ...

# agentcad/core/presence.py
from .ratelimit import TokenBucket   # keep the name importable, behaviour identical

# agentcad/core/publications.py
SHARE_TOKEN_RE = ...                  # shr_<id8>_<secret>
class PublicationStore:
    def __init__(self, root: Path): ...          # root = state_dir() / "publications"
    def create(self, *, scope, project, part_id, ref, script_sha,
               settings, created_by, default_variant_key) -> tuple[str, str]:
        ...                                       # -> (pub_id, share_token)  [token shown once]
    def resolve(self, share_token: str) -> dict | None:   # live record or None (revoked/expired/unknown)
    def get(self, pub_id: str) -> dict | None:
    def list_for(self, created_by: str, project: str) -> list[dict]:  # no token_digest, ever
    def revoke(self, pub_id: str, by: str) -> None:
    def bump(self, pub_id: str, field: str, n: int = 1) -> None:      # coarse counter, best-effort
    def script_path(self, script_sha: str) -> Path:                  # scripts/<sha>.py
    def build_root(self) -> Path:                                    # build/
```

### Tasks
- [ ] **Step 1:** Write `tests/test_ratelimit.py` — move the presence
  `TokenBucket` cases here unchanged, and add
  `test_presence_reexports_the_same_object` asserting
  `presence.TokenBucket is ratelimit.TokenBucket`. Run; confirm the move fails
  to import until step 2.
- [ ] **Step 2:** Create `core/ratelimit.py` with `TokenBucket` moved verbatim;
  replace the class body in `presence.py` with `from .ratelimit import
  TokenBucket`. Run `pytest tests/test_ratelimit.py tests/test_presence.py -q`
  → both green, presence behaviour unchanged (the presence suite is the
  regression test for the move).
- [ ] **Step 3:** Write `tests/test_publications.py` (failing):
```python
import time, pytest
from agentcad.core.publications import PublicationStore

@pytest.fixture
def store(tmp_path):
    return PublicationStore(tmp_path / "publications")

def _mk(store, **over):
    kw = dict(scope="part", project="p", part_id="nozzle",
              ref={"kind": "tag", "name": "v1", "commit": "9f2c"},
              script_sha="sha256:abc", settings={"customizer": True,
              "exports": ["step"], "show_script": False, "expires": None,
              "config": None}, created_by="nikita", default_variant_key="k")
    kw.update(over)
    return store.create(**kw)

def test_token_round_trips_and_names_one_record(store):
    pub_id, token = _mk(store)
    assert token.startswith("shr_")
    rec = store.resolve(token)
    assert rec["pub_id"] == pub_id and rec["part_id"] == "nozzle"

def test_unknown_revoked_and_expired_all_resolve_to_none(store):
    pub_id, token = _mk(store)
    assert store.resolve("shr_deadbeef_" + "x" * 40) is None
    store.revoke(pub_id, by="nikita")
    assert store.resolve(token) is None
    _, tok2 = _mk(store, settings={"customizer": False, "exports": [],
                  "show_script": False, "expires": int(time.time()) - 1,
                  "config": None})
    assert store.resolve(tok2) is None

def test_list_never_leaks_the_digest(store):
    _mk(store)
    listed = store.list_for("nikita", "p")
    assert listed and "token_digest" not in repr(listed)

def test_the_secret_is_not_stored_raw(store, tmp_path):
    _, token = _mk(store)
    blob = (tmp_path / "publications" / "store.json").read_text()
    assert token.split("_", 2)[2] not in blob

def test_a_second_process_write_is_seen_without_restart(store, tmp_path):
    import subprocess, sys
    _mk(store)
    subprocess.run([sys.executable, "-c",
        "from agentcad.core.publications import PublicationStore;"
        f"s=PublicationStore({str(tmp_path/'publications')!r});"
        "s.bump(next(iter(s._read('store').get('publications',{}))), 'views')"],
        check=True)
    # the running store re-reads on mtime change
    assert store.list_for("nikita", "p")[0]["counters"]["views"] == 1
```
- [ ] **Step 4:** Implement `core/publications.py` on the `authstore` shape:
  `store.json` = `{"publications": {pub_id: record}}`; `_read`/`_write` with the
  `(st_mtime_ns, st_size, st_ino)` staleness cache and random-staging
  `os.replace`; a reentrant `_scope()` (`threading.Lock` + `fcntl.flock` on a
  `.lock` sibling, nesting-depth guarded). `create` mints
  `shr_{pub_id}_{secrets.token_urlsafe(32)}`, stores `sha256(token)`, writes the
  record, returns the plaintext once. `resolve` splits the token, looks up
  `pub_id`, `hmac.compare_digest`s the digest, and returns `None` for
  revoked/expired/unknown — one path, no oracle. `import fcntl` through
  `try/except ImportError`.
- [ ] **Step 5:** Prove no geometry import (the 005a meta-path block): import
  `core.publications`, `core.ratelimit` with `OCP`/`build123d` blocked → `ok`.
- [ ] **Step 6:** `make test` — cite the count (baseline + the new tests, minus
  the moved ones which net to zero). Commit.

---

## Slice 2 — publishing: the pin, the muzzled build service, and the management API

**Goal:** an authenticated member turns a part at a version into a token, and
the default variant is pre-warmed — with the visitor build machinery proven to
never touch the owner's project.

### Files
- Create: `agentcad/server/routes_share.py` (management, default `/api` PREFIX),
  `agentcad/core/tools_share.py`, `agentcad/core/share_build.py` (the pin +
  muzzled build service).
- Test: `tests/test_share_publish.py`, `tests/test_share_isolation.py`.

### Interfaces
```python
# agentcad/core/share_build.py
class ShareBuilder:
    """Pins a part at a ref into <state-dir>/publications/ and builds variants
    against a muzzled AgentCADService rooted there. Constructed once, lazily."""
    def __init__(self, store: PublicationStore, kernel): ...
    def pin(self, service, project, part_id, ref) -> tuple[str, str, str]:
        # -> (script_sha, resolved_commit, default_variant_key); reads the ref
        #    with packet._script_at, writes scripts/<sha>.py + build/parts/<pub>.py,
        #    warms the default variant. NEVER writes `service`'s store.
    def build_variant(self, pub_id, params) -> dict:   # {cache_key, metrics, warnings, ok}
    def mesh_path(self, cache_key) -> Path | None:     # 404-if-absent, never builds
    def export_variant(self, pub_id, params, fmt) -> Path:
```

### Tasks
- [ ] **Step 1:** Write `tests/test_share_isolation.py` (failing) — the
  load-bearing containment proof:
```python
def test_publishing_and_flooding_variants_never_writes_the_owner_project(hosted, tmp_path):
    client, _ = hosted
    _login(client)
    # create a project + part, tag it
    ... create "demo"/"box" with BOX_SCRIPT, POST a version tag "v1" ...
    before = _snapshot_tree(owner_project_dir)          # bytes of manifest + parts + .cache + .history
    r = client.post("/api/share", json={"project": "demo", "scope": "part",
                    "part_id": "box", "ref": "v1", "customizer": True,
                    "exports": ["step"]})
    assert r.status_code == 201
    # (slice 4 floods /variant; here assert the publish alone is inert on the owner tree)
    assert _snapshot_tree(owner_project_dir) == before
```
- [ ] **Step 2:** Write `tests/test_share_publish.py` (failing): `share_create`
  returns `{url, pub_id}` and a `/s/<token>` URL; `share_list` shows the link
  with zeroed counters and **no token**; `share_revoke` flips `revoked` and a
  later resolve is `None`; publishing a **branch** ref without a tag auto-tags
  and pins the commit; publishing an unknown part → `not_found`; a member
  publishing is allowed, an anonymous caller is `401` (the guard).
- [ ] **Step 3:** Implement `share_build.ShareBuilder.pin`: resolve the ref with
  `checks._resolve_ref`'s discipline (`resolve_branch` then `resolve_tag`, never
  `resolve_ref`); if the ref is a branch, `BranchManager.tag` a version first
  (design Decision 3). Read `parts/<id>.py` + `project.json` at the commit with
  `packet._script_at`/`_blob` (`packet.py:1245`, `:1257`). `script_sha =
  "sha256:" + sha256(bytes)`. Write `scripts/<sha>.py` and
  `build/parts/<pub_id>.py`; construct/reuse the muzzled build service
  (`checks._ephemeral_service` recipe — the three nulled seams, the last two
  after `build_registry`); warm the default variant via
  `_build_with(record, affinity=f"share:{pub_id}", status_key=None)`.
- [ ] **Step 4:** Implement `routes_share.py` (`build_router`, captures
  `security.current_config()` at mount time). `POST /api/share` reads the
  member principal (`security.current_principal()`), validates scope/exports
  (whitelist body keys — no `**body`), calls `ShareBuilder.pin` +
  `PublicationStore.create`, returns `{url: f"{origin}/s/{token}", pub_id}` 201.
  `GET /api/share?project=` → `store.list_for(principal.name, project)`.
  `DELETE /api/share/{pub_id}` → `store.revoke`. Publishes `share_changed`.
- [ ] **Step 5:** Implement `core/tools_share.py`: `register(registry, service)`
  adds `share_create/list/revoke` **only when `security.current_config()` is not
  None** (the `whoami` precedent). Thin wrappers over the same store + builder.
- [ ] **Step 6:** Run both test files; then `make test`. The surface test is
  untouched this slice (management routes are `/api`, private). Cite counts;
  commit.

---

## Slice 3 — the viewer link: the kernel-free anonymous surface

**Goal:** a logged-out visitor renders a pinned part with **zero** kernel calls,
and the anonymous surface grows by exactly the viewer routes — provably.

### Files
- Create: `agentcad/server/routes_share_public.py` (`PREFIX = ""`).
- Modify: `agentcad/server/security.py` (two `PUBLIC_PREFIXES` entries).
- Modify: `tests/test_hosted_surface.py` (`EXPECTED_PUBLIC` + negation params).
- Test: `tests/test_share_viewer.py`.

### Tasks
- [ ] **Step 1:** Add `"/s/"` and `"/embed/"` to `security.PUBLIC_PREFIXES`
  (`security.py:80`). Add `/s`, `/status`, `/svg`, `/embed`, `/embedding` to
  `test_paths_that_must_not_be_public` (they must stay `False` — the trailing-
  slash gotcha).
- [ ] **Step 2:** Grow `EXPECTED_PUBLIC` in `tests/test_hosted_surface.py` with
  the eight `/s/`+`/embed/` templates (Decision 2 table); keep the set-equality
  assertion. `NOT_YET_BUILT` gets the two customizer templates
  (`/s/{token}/variant`, `/s/{token}/download/{fmt}`) so slice 4 removes them —
  the 005a `NOT_YET_BUILT` discipline (`test_hosted_surface.py:48-55`).
- [ ] **Step 3:** Write `tests/test_share_viewer.py` (failing): a published
  link's `GET /s/<token>` returns the shell (200, **no `Set-Cookie`**);
  `/model` returns attribution + metrics + `default_variant_key`; `/mesh/{key}`
  returns the `.acm` bytes for the default key and **404s for an absent key
  without building**; `/params` returns the typed spec; `/script` is 404 when
  `show_script` is false and the pinned text when true; a revoked/expired/
  unknown token 404s indistinguishably on every route; **no viewer route
  increments `kernel_counter`**.
- [ ] **Step 4:** Implement `routes_share_public.py` (`build_router`, `PREFIX =
  ""`, config captured at mount). Every handler `store.resolve(token)` → 404 on
  `None` (the `_miss` shape + `Cache-Control`). `/mesh/{key}` reads
  `ShareBuilder.mesh_path(key)` and serves bytes or 404 — **never builds** (the
  `routes_configs.get_mesh_by_key` contract). `/model` reads the metrics
  sidecar for `default_variant_key`. `/s/{token}` and `/embed/{token}` return
  the static shell; the embed response sets `Content-Security-Policy:
  frame-ancestors *` and `Referrer-Policy: no-referrer`, `/s/` sets
  `Referrer-Policy: no-referrer`. `/s/{token}` bumps the `views` counter (page
  load only). Increment nothing on asset fetches.
- [ ] **Step 5:** Extend
  `test_hosted_surface.py::test_public_surface_makes_no_kernel_calls` to sweep
  the six viewer routes against a published link and assert
  `kernel_counter.calls` unchanged, with the positive control already present.
- [ ] **Step 6:** Run `tests/test_share_viewer.py tests/test_hosted_surface.py`;
  then `make test`. Cite counts; commit.

---

## Slice 4 — the customizer rebuild and its caps

**Goal:** the first anonymous `exec()`, bounded: param-validated, rate-limited,
concurrency-capped, cached, and export-masked.

### Files
- Modify: `agentcad/server/routes_share_public.py` (`/variant`, `/download`).
- Modify: `agentcad/core/share_build.py` (`build_variant`, `export_variant`,
  the semaphore).
- Modify: `tests/test_hosted_surface.py` (empty `NOT_YET_BUILT`).
- Test: `tests/test_share_customizer.py`.

### Tasks
- [ ] **Step 1:** Write `tests/test_share_customizer.py` (failing), the AC
  battery:
```python
def test_a_fresh_variant_builds_once_and_a_repeat_is_cached(share_link, kernel_counter):
    c, token = share_link                      # customizer:true, exports:["step"]
    before = kernel_counter.calls
    a = c.get(f"/s/{token}/variant", params={"expansion_ratio": 12})
    assert a.status_code == 200 and a.json()["metrics"]["mass_g"] > 0
    mid = kernel_counter.calls
    b = c.get(f"/s/{token}/variant", params={"expansion_ratio": 12})   # same → cache
    assert b.json()["mesh_key"] == a.json()["mesh_key"]
    assert kernel_counter.calls == mid == before + 1          # exactly one build (AC2/AC7)

def test_param_parity(share_link):
    c, token = share_link
    r = c.get(f"/s/{token}/variant", params={"expansion_ratio": 1e9})  # out of range
    assert any("clamp" in w for w in r.json()["warnings"])              # clamped, not rejected
    assert c.get(f"/s/{token}/variant", params={"nope": 1}).status_code == 422
    assert c.get(f"/s/{token}/variant",
                 params={"an_enum": "not-a-member"}).status_code == 422

def test_over_the_limit_is_quota_exceeded_and_degrades(share_link):
    c, token = share_link
    codes = [c.get(f"/s/{token}/variant", params={"expansion_ratio": i}).status_code
             for i in range(40)]
    assert 429 in codes
    r = next(r for r in [c.get(f"/s/{token}/variant", params={"expansion_ratio": 999})]
             if r.status_code == 429)
    assert r.json()["error"]["details"]["retry_after_s"] > 0

def test_export_mask(share_link):
    c, token = share_link                       # exports = ["step"]
    assert c.get(f"/s/{token}/download/step", params={"expansion_ratio": 12}).status_code == 200
    assert c.get(f"/s/{token}/download/stl",  params={"expansion_ratio": 12}).status_code == 404

def test_viewer_only_link_cannot_rebuild(viewer_link):
    c, token = viewer_link                      # customizer:false
    assert c.get(f"/s/{token}/variant", params={"expansion_ratio": 12}).status_code == 404

def test_the_owner_tree_is_byte_unchanged_after_a_flood(share_link, owner_tree):
    c, token = share_link
    before = owner_tree.snapshot()
    for i in range(30):
        c.get(f"/s/{token}/variant", params={"expansion_ratio": i})
    assert owner_tree.snapshot() == before      # AC5
```
- [ ] **Step 2:** Implement `ShareBuilder.build_variant(pub_id, params)`:
  load the cached spec (`_params_spec`), **reject** with
  `service.normalize_params(spec, params)` (`service.py:375`) → `ValidationError`
  → the route maps to `validation_error`; then build via
  `_build_with(record, affinity=f"share:{pub_id}", status_key=None)`, whose
  `worker._resolve_params` clamps and returns `warnings`. Wrap the build call in
  a module-level `BoundedSemaphore(SHARE_MAX_INFLIGHT)` (non-blocking
  `acquire(blocking=False)`; on failure raise `RateLimitedError` with
  `retry_after_s`). Return `{mesh_key: cache_key, metrics, warnings, ok}`.
- [ ] **Step 3:** Implement the `/variant` and `/download/{fmt}` GET handlers:
  `store.resolve(token)` → 404; `settings.customizer` false → 404
  (`/variant`); `fmt not in settings.exports` → 404 (`/download`) **before**
  building; take the per-link `TokenBucket(f"share:{pub_id}")` and per-IP
  `TokenBucket(f"addr:{client_host}")` (the promoted `ratelimit.TokenBucket`),
  over-limit → `quota_exceeded` 429; the optional
  `AGENTCAD_SHARE_REQUIRE_LOGIN_ABOVE` gate → 401 when a per-IP hourly count is
  crossed (design Decision 4). `/download` streams
  `ShareBuilder.export_variant(...)` named `<part>_<hash8>.<ext>`. Bump
  `rebuilds`/`downloads` counters. The client host resolution honours
  `appmode.trusted_proxy` (the login precedent).
- [ ] **Step 4:** Empty `NOT_YET_BUILT` in `test_hosted_surface.py` (the two
  customizer templates are now live); the set-equality test now proves all
  eight `/s/`+`/embed/` routes are the intended anonymous surface. Extend the
  kernel-silence test's *positive* half: a fresh variant increments the counter
  by exactly one, a repeat by zero.
- [ ] **Step 5:** Run `tests/test_share_customizer.py
  tests/test_hosted_surface.py`; then `make test`. Cite counts; commit.

---

## Slice 5 — the frontend: slim viewer, customizer, embed, and the owner dialogs

**Goal:** the visitor page and embed work in a real browser, and the owner can
create and revoke links from the app.

### Files
- Create: `frontend/share.html`, `frontend/js/share.js`, `frontend/js/share-viewport.js`.
- Modify: `frontend/js/main.js` + a "Share…" dialog and a "Links" panel;
  `frontend/js/api.js` (the `share_*` calls).
- Test: `tests/test_share_frontend.py` (the shell serves; the bundle imports
  `parseACM`), plus a Node round-trip if the param panel warrants one.

### Tasks
- [ ] **Step 1:** `share-viewport.js` re-exports `parseACM`/`buildGeometry`/
  `showPart`/`showAssembly` from `viewport.js` (`viewport.js:74-444`) minus the
  editor-only `TransformControls`/CodeMirror — page weight matters for embeds.
- [ ] **Step 2:** `share.js` reads `/s/<token>/model` + `/params`, renders the
  viewport and a param panel (number→slider clamped to min/max, int→stepped,
  enum→dropdown, bool→checkbox, string→text with `max_len` — the
  `inspector.js:470-587` control mapping, without the editor), calls
  `/variant?…` on change with local debounce, updates mesh + metrics, and shows
  the retry-after banner + view-only degrade on 429. `Download <fmt>` hits
  `/download`. The share token stays in the URL (it is the artifact — unlike the
  enrolment token 005a strips with `replaceState`).
- [ ] **Step 3:** `share.html` is the self-contained shell served by
  `/s/{token}` and `/embed/{token}` (the embed variant hides the chrome and is
  framed). No external assets (CSP-clean).
- [ ] **Step 4:** The main app: a "Share…" dialog (scope, ref/version,
  customizer on/off, export mask, show-script, expiry → `share_create`, URL
  copied) and a "Links" panel (`share_list` with counters + revoke buttons).
- [ ] **Step 5:** Browser verification with the **`run` skill** in
  `AGENTCAD_MODE=hosted` (scratchpad `AGENTCAD_CONFIG`/`--projects-dir`, never
  `~/AgentCAD`): publish a part, open `/s/<token>` logged out, move a slider,
  download STEP, and load `/embed/<token>` in an iframe on a second local
  origin. Screenshot each. **If the Chrome extension is unavailable (as in the
  005a sessions), grade AC1/AC10 as evidence** — cite the served HTML, the
  `kernel_counter` deltas, and the response headers — on the PRD-005a AC3
  precedent.
- [ ] **Step 6:** `make test`; cite counts; commit.

---

## Slice 6 — docs, acceptance, and the PRD/roadmap moves

**Goal:** the operator- and contributor-facing docs are true, the ACs are
verified, and the status is truthful in the same commit.

### Files
- Modify: `docs/deployment.md`, `AGENTS.md`, `docs/roadmap.md`,
  `docs/agent-api.md` (the `share_*` tools).
- Move: `docs/prd/pending/PRD-007-…` → `docs/prd/completed/…` (or leave in
  `in-progress/` per the founder's commit), with the status note.
- Create: `docs/changelog/NNNN-share-links-customizer.md`.

### Tasks
- [ ] **Step 1:** `docs/deployment.md`: add the two `/s/`+`/embed/` prefixes and
  the two kernel-reaching routes to "What a stranger can reach"
  (`deployment.md:208`), the `AGENTCAD_SHARE_MAX_INFLIGHT` /
  `AGENTCAD_SHARE_REQUIRE_LOGIN_ABOVE` env vars, and the `X-Forwarded-For` note
  (the per-IP limit is only real behind the trusted proxy).
- [ ] **Step 2:** `AGENTS.md`: a "Share-links gotchas (PRD-007)" section — the
  `GET`-not-`POST` rebuild and why; the copy-not-reference pin; the muzzled
  build service; the trailing-slash prefix rule for `/s/`; the four `token`
  senses and the `share_token`/`pub_id` naming; the promoted `TokenBucket`.
- [ ] **Step 3:** `docs/agent-api.md`: document `share_create/list/revoke` +
  `share_changed`.
- [ ] **Step 4:** Fold the design's Decision 8 divergences back into PRD-007
  (the `GET` rebuild, ACM-not-glTF, state-dir storage, the digest token, two
  packs, part-scope MVP) so the PRD matches what shipped. Update the `Status:`
  line, move the file, and update the roadmap index row — **same commit**.
- [ ] **Step 5:** Run the full acceptance battery and record each AC's evidence
  (AC1/AC10 graded as evidence if no browser). `make test` — cite the final
  count. Write `docs/changelog/NNNN-…` from the real diff. Commit.

---

## What this plan deliberately leaves to a later PRD/slice

- **glTF export** (`core/gltf.py`) — PRD-017 (design Decision 7).
- **Project/assembly-scope links, embeds-with-sliders polish,
  drawings/flat-pattern exports, script-visibility UI, branch-following ("live")
  links, named-configuration customizer** — PRD-007 Phase 2/3 (the PRD's own
  phasing).
- **Memory/pid/disk/egress caps and variant-cache GC** — PRD-006 (the honest
  residual, design Decision 4); a size-capped cache sweep can land as a small
  Phase-2 slice before then.
- **Discovery, listings, attribution pages** — PRD-031a, which inherits this
  page's counters and pinned publications as its substrate.
