# 0213 — PRD-007 slice 1: rate-limit promotion and the publication store

- **Commit:** pending
- **Date:** 2026-08-18
- **Author:** Nikita Fedorov

## Summary
The foundations for share links & the customizer (PRD-007): `TokenBucket` is
promoted out of `core/presence.py` into a shared `core/ratelimit.py` (design
Decision 6), and a new `core/publications.py::PublicationStore` holds share
publications in the state dir on the `authstore` shape. No server wiring yet —
this slice is storage plus a pure refactor.

## Changes
- **New `agentcad/core/ratelimit.py`** — `TokenBucket` moved verbatim (constructor
  `TokenBucket(rate=, burst=, clock=, limit=)`, `take`/`forget`), with its
  default constants `RATE_PER_S`/`RATE_BURST`/`MAX_BUCKETS`. Behaviour is
  byte-identical; joins the OCP-free set.
- **`agentcad/core/presence.py`** — the `TokenBucket` class body is replaced by a
  one-line re-export `from .ratelimit import TokenBucket`. Presence keeps its own
  `RATE_PER_S`/`RATE_BURST`/`MAX_BUCKETS` heartbeat constants (values agree with
  ratelimit's). This is the **only** PRD-008 touch and changes no behaviour — the
  presence suite is the regression test.
- **`agentcad/server/security.py`** — the login limiter imports `TokenBucket` from
  `core.ratelimit` instead of `core.presence`; comment updated. No behaviour change.
- **New `agentcad/core/publications.py`** — `PublicationStore` on the `AuthStore`
  shape: `store.json = {"publications": {pub_id: record}}`, atomic random-staged
  `os.replace` writes, `(st_mtime_ns, st_size, st_ino)` staleness cache, a
  reentrant `_scope()` with `threading.RLock` + best-effort `fcntl.flock` on a
  `.lock` sibling. A token is `shr_<pub_id8>_<secret43>` (`secrets.token_urlsafe(32)`
  = 256 bits); only `sha256(token)` is stored and compared with
  `hmac.compare_digest`. `resolve()` returns one indistinguishable `None` for
  revoked / expired / unknown / wrong-secret tokens (no oracle). API:
  `create`/`resolve`/`get`/`list_for`/`revoke`/`bump`/`script_path`/`build_root`.
  `list_for` never emits `token_digest`.

## Files
- `agentcad/core/ratelimit.py` — new module: promoted `TokenBucket`
- `agentcad/core/presence.py` — class removed, re-export added, constants kept
- `agentcad/server/security.py` — import switched to `core.ratelimit`
- `agentcad/core/publications.py` — new module: `PublicationStore`
- `tests/test_ratelimit.py` — new: moved `TokenBucket` cases + `presence.TokenBucket is ratelimit.TokenBucket`
- `tests/test_publications.py` — new: token round-trip, indistinguishable dead tokens, no raw secret / no digest leak, cross-process re-read, OCP-free probe
- `tests/test_presence.py` — the two pure `TokenBucket` cases removed (moved)

## Notes
Verification: `pytest tests/test_ratelimit.py tests/test_publications.py
tests/test_presence.py tests/test_auth_routes.py tests/test_security_guard.py
tests/test_hosted_surface.py` → **155 passed** (2026-08-18). Prior full-suite
baseline was 3689 passed / 1 skipped (changelog 0199; PRD-012 merged after, so
that count predates this branch and is cited as the prior tree's measurement,
not this slice's). The publication store is NOT project data — it derives from
`appmode.state_dir()` and is never constructed by `AgentCADService.__init__`, so
PRD-004/011 ephemeral services and `--projects-dir` isolation are unaffected by
construction. Publishing, the muzzled build service, and the anonymous surface
are later slices.
