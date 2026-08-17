# Hosted core (PRD-005a / "005-lite") — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: use
> `superpowers:subagent-driven-development` (recommended) or
> `superpowers:executing-plans` to work through this plan slice by slice.
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** ship
[PRD-005a](../../prd/in-progress/PRD-005a-hosted-core.md) as **step 2 of the
marketplace chain** — one deployable hosted AgentCAD, invite-only identity for
browsers and agents, and an enumerated public-read surface that makes zero
kernel calls — per
[the design spec](../specs/2026-08-17-hosted-core-design.md).

**Architecture (one paragraph):** identity lives in the **app layer**, never
in `AgentCADService`, so PRD-004/011 ephemeral services are unaffected by
construction. `agentcad/core/authstore.py` is four atomically-written JSON
documents under `<config-dir>/state/auth/`, serialised in-process and across
processes with `fcntl.flock`. `agentcad/server/security.py` owns the mode
matrix, the `PUBLIC_PATHS` enumeration, principal resolution and the
default-deny guard; `agentcad/server/app.py` is edited exactly twice (the
middleware body plus one `create_app` parameter, and the WebSocket guard call)
and holds no logic of its own. Two route packs land the surface —
`routes_auth.py` (management) and `routes_public.py` (anonymous catalog read,
filtered to indexes whose `scope == "public"`). One two-line change to
`proposals.actor_kind` keeps PRD-008's human-only claims working under
composed principals. Deployment is a Dockerfile plus a one-volume
`compose.yaml`; TLS is bring-your-own with a `--profile proxy` escape hatch.

**Tech stack:** Python 3.12 stdlib only (`hashlib.scrypt`, `secrets`, `hmac`,
`fcntl`, `json`, `threading`) / FastAPI route packs / pytest with
`TestClient`. **No new runtime dependency** — passwords, sessions, tokens and
rate limiting are all stdlib or already in the tree.

---

## Global constraints (encode these in every slice)

- **Only `agentcad/kernel/` may import `OCP`/build123d.** This plan adds
  **zero** kernel files and imports no geometry anywhere.
- **Exactly three edits to existing non-test Python files in the whole plan**,
  and any other diff to an existing non-test file is a design bug — stop and
  re-read the design spec:
  1. `agentcad/server/app.py` — the `security=` parameter, the middleware
     body delegating to `server/security.py`, the WebSocket guard call, and
     the two-line `PREFIX` honouring in `_mount_route_packs` (slices 2 and 6).
  2. `agentcad/core/proposals.py` — two lines in `actor_kind` (slice 2).
  3. `agentcad/cli.py` — `--host`, the `admin` subcommand group, the
     `AGENTCAD_EXAMPLES` skip, and the mode interlock (slices 1, 3, 5).
  Plus `agentcad/agent/mcp_server.py` (three lines, slice 4) and
  `frontend/js/api.js` + `frontend/js/main.js` (slice 3).
- **Do not edit `worker.py`, `tools.py`, `service.py`, `project.py`,
  `locks.py`, `presence.py`, `comments.py`, `branches.py`, `history.py`,
  `merge.py`, `checks.py`, `specs.py` or anything under
  `core/packages/`.** PRD-001–011 are finished; this feature *consumes* them.
  `presence.TokenBucket` is **imported, never re-implemented**.
- **Local mode is the same code path, not a disabled feature.**
  `create_app(service, registry)` with no `security=` must execute exactly
  today's middleware body. AC9 is a property of the diff. A test pins the
  middleware count at one.
- **Default deny.** Anything not in `security.PUBLIC_PATHS` is `401` in hosted
  mode. Never write a per-route `@public` decorator — a route pack author must
  not be able to open the surface from their own file.
- **The anonymous surface makes zero kernel calls.** Any slice that adds a
  public path must extend `tests/test_hosted_surface.py::test_public_surface_makes_no_kernel_calls`.
- **The identity ceiling is arithmetic, not style.**
  `locks.MAX_CLIENT_ID_CHARS == 64` and `check_client_id` **refuses rather
  than truncates** (`agentcad/core/locks.py:81`, `:84-101`). `user:` + handle
  (≤32) + `/browser:` + 8 = ≤54. Handles are `[a-z0-9][a-z0-9._-]{0,31}`.
- **Secrets are never logged, never returned twice, never stored raw.**
  Passwords → `hashlib.scrypt`; session and token secrets → `sha256` digests.
  Comparisons use `hmac.compare_digest`.
- **The trust sentence** — *"an account on this instance can execute arbitrary
  Python on the host; give one only to someone you would give a shell to"* —
  goes into `docs/deployment.md`, the `compose.yaml` header, `agentcad admin
  user add --help`, and the success output of `agentcad admin user add`, in
  the same slice that creates each surface (design spec, Decision 1).
- **Atomic writes everywhere** (`ProjectStore._atomic_write`'s staging + random
  name + `os.replace` shape). Every read-modify-write of a state file holds a
  `threading.Lock` **and** `fcntl.flock`, on the
  `LocalIndex._index_scope` precedent (`core/packages/indexes.py:502`).
- **State path derivation is `config.config_path().parent`**, the
  `AGENTCAD_PACKAGES_DIR` / `AGENTCAD_INDEXES_DIR` pattern
  (`core/packages/cache.py:96-104`, `core/packages/_git.py:98-106`), so every
  test that sets `AGENTCAD_CONFIG` gets an isolated store for free. Never
  under `--projects-dir`, never inside a project or `.history`.
- **Errors are the house contract** `{error: {type, message, details}}`. New
  `AppError` subclasses `AuthError` (401), `PermissionError` → named
  `AuthzError` to avoid shadowing the builtin (403), `RateLimitedError` (429).
- Never `uv sync` / `uv pip install` from a parallel agent; use a scratch venv.
  `TestClient(base_url="http://127.0.0.1")`,
  `create_app(..., extra_allowed_hosts={"testserver"})`.
- **Baseline:** record the real `make test` pass count on this branch before
  slice 1 and cite it in every slice's verification. No unexplained skips.
- **Verification before completion, every slice:** run the named commands and
  cite their real output. "Should pass" is not a result.

---

## Slice map

Eight slices. PRD-011 needed fourteen because it shipped a nine-module
subpackage, a nine-stage gate, three index kinds and a seeded catalog; this
feature ships one storage module, one guard, two route packs and a deployment
artifact set. The ordering is the design's dependency order — **the guard
before anything it protects, hardening before the box that exposes it, public
read only once default-deny is provable, deployment before the CI that smokes
it**:

| # | Slice | Deliverable |
|---|---|---|
| 1 | Modes, state paths, the auth store | `core/authstore.py` + `core/appmode.py`, no server |
| 2 | The security seam | `server/security.py`, the two `app.py` edits, `actor_kind`, the three carrying tests |
| 3 | Enrolment, login, sessions, the UI | `routes_auth.py`, `cli admin user`, `frontend/js/auth.js` |
| 4 | Bearer tokens, `whoami`, remote MCP | `cli admin token`, `core/tools_auth.py`, `mcp_server.py` |
| 5 | Hosted-mode hardening | the seven route-level refusals + the `--host` interlock |
| 6 | Deployment | Dockerfile, `compose.yaml`, `PREFIX` seam, `docs/deployment.md` |
| 7 | Public read | `routes_public.py`, scope filtering, cache headers |
| 8 | CI, docs, PRD/roadmap moves, acceptance | `deploy-smoke.yml`, AGENTS.md gotchas, changelog |

---

## Slice 1 — modes, state paths, and the auth store

**Goal:** a fully tested identity store and mode resolver that no server
imports yet.

### Files

- Create: `agentcad/core/appmode.py`
- Create: `agentcad/core/authstore.py`
- Test: `tests/test_appmode.py`, `tests/test_authstore.py`

### Interfaces

- Consumes: `agentcad.config.config_path` (`agentcad/config.py:16-20`).
- Produces, relied on by slices 2–8:

```python
# agentcad/core/appmode.py
LOCAL = "local"; HOSTED = "hosted"

def state_dir() -> Path: ...
    # $AGENTCAD_STATE_DIR, else config_path().parent / "state"

class ModeError(Exception): ...

@dataclass(frozen=True)
class AppMode:
    name: str                 # "local" | "hosted"
    public_origin: str | None # e.g. "https://cad.example.com", no trailing /
    secret: bytes | None

    @property
    def hosted(self) -> bool: ...
    @property
    def origin_host(self) -> str | None: ...   # "cad.example.com" (no port)
    @property
    def secure_cookies(self) -> bool: ...      # public_origin startswith https://

def resolve_mode(env: Mapping[str, str] | None = None) -> AppMode: ...
    # raises ModeError naming the missing setting

def check_bind(mode: AppMode, host: str) -> None: ...
    # raises ModeError on (local, non-loopback)

# agentcad/core/authstore.py
HANDLE_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,31}$")

class AuthStore:
    def __init__(self, root: Path): ...        # root = state_dir() / "auth"

    # users
    def add_user(self, handle: str, role: str = "member") -> str: ...
        # -> enrolment token (plaintext, shown once); role in {"admin","member"}
    def list_users(self) -> list[dict]: ...     # no digests, ever
    def disable_user(self, handle: str) -> None: ...
    def enrol(self, token: str, password: str) -> str: ...   # -> handle
    def verify_password(self, handle: str, password: str) -> bool: ...

    # sessions
    def create_session(self, handle: str, device: str | None) -> str: ...  # secret
    def resolve_session(self, secret: str) -> dict | None: ...
        # {"handle","role","device"} or None; slides last_seen at day granularity
    def revoke_session(self, secret: str) -> None: ...

    # tokens
    def add_token(self, name: str, role: str = "member",
                  ttl_days: int | None = None) -> str: ...   # "acad_<id8>_<secret43>"
    def list_tokens(self) -> list[dict]: ...
    def revoke_token(self, token_id: str) -> None: ...
    def resolve_token(self, presented: str) -> dict | None: ...  # {"name","role"}
```

### Tasks

- [ ] **Step 1: Write the failing mode tests** — `tests/test_appmode.py`

```python
import pytest
from agentcad.core.appmode import AppMode, ModeError, check_bind, resolve_mode


def test_default_is_local():
    mode = resolve_mode({})
    assert mode.name == "local"
    assert mode.hosted is False


def test_hosted_without_origin_names_the_missing_setting():
    with pytest.raises(ModeError) as exc:
        resolve_mode({"AGENTCAD_MODE": "hosted", "AGENTCAD_SECRET_KEY": "s" * 32})
    assert "AGENTCAD_PUBLIC_ORIGIN" in str(exc.value)


def test_hosted_parses_origin_and_cookie_policy():
    mode = resolve_mode({
        "AGENTCAD_MODE": "hosted",
        "AGENTCAD_PUBLIC_ORIGIN": "https://cad.example.com",
        "AGENTCAD_SECRET_KEY": "s" * 32,
    })
    assert mode.origin_host == "cad.example.com"
    assert mode.secure_cookies is True


def test_local_refuses_a_non_loopback_bind():
    with pytest.raises(ModeError) as exc:
        check_bind(AppMode("local", None, None), "0.0.0.0")
    assert "AGENTCAD_MODE=hosted" in str(exc.value)


def test_hosted_allows_both_binds():
    mode = AppMode("hosted", "https://x.example", b"k" * 32)
    check_bind(mode, "0.0.0.0")
    check_bind(mode, "127.0.0.1")
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `.venv/bin/python -m pytest tests/test_appmode.py -q`
Expected: FAIL — `ModuleNotFoundError: agentcad.core.appmode`.

- [ ] **Step 3: Implement `agentcad/core/appmode.py`**

`resolve_mode` reads `AGENTCAD_MODE` (default `local`), rejects anything but
`local`/`hosted` with a `ModeError` naming both valid values. For `hosted` it
requires `AGENTCAD_PUBLIC_ORIGIN` matching `^https?://[^/]+$` (strip a trailing
slash, reject a path) and a secret from `AGENTCAD_SECRET_KEY` (≥32 chars) or —
when absent — reads/creates `state_dir()/secret.key` with
`secrets.token_bytes(32)` written through `os.open(..., O_CREAT|O_EXCL, 0o600)`.
`state_dir()` returns `Path(os.environ["AGENTCAD_STATE_DIR"])` if set, else
`config_path().parent / "state"`. `check_bind` raises unless the host is in
`{"127.0.0.1", "localhost", "::1", "[::1]"}` or the mode is hosted.

- [ ] **Step 4: Run the mode tests and confirm they pass**

Run: `.venv/bin/python -m pytest tests/test_appmode.py -q`
Expected: 5 passed.

- [ ] **Step 5: Write the failing auth-store tests** — `tests/test_authstore.py`

```python
import time
import pytest
from agentcad.core.authstore import AuthStore


@pytest.fixture
def store(tmp_path):
    return AuthStore(tmp_path / "auth")


def test_enrolment_is_single_use(store):
    token = store.add_user("nikita", role="admin")
    assert store.enrol(token, "correct horse battery") == "nikita"
    with pytest.raises(Exception):
        store.enrol(token, "second try")


def test_password_round_trip_and_no_digest_leaks(store):
    token = store.add_user("anya")
    store.enrol(token, "hunter2hunter2")
    assert store.verify_password("anya", "hunter2hunter2") is True
    assert store.verify_password("anya", "wrong") is False
    listed = store.list_users()
    assert [u["handle"] for u in listed] == ["anya"]
    assert "password" not in listed[0] and "digest" not in repr(listed)


def test_unknown_handle_verifies_false_without_raising(store):
    assert store.verify_password("ghost", "anything") is False


def test_bad_handles_are_refused(store):
    for bad in ("Nikita", "-x", "a" * 33, "has space", ""):
        with pytest.raises(Exception):
            store.add_user(bad)


def test_session_resolve_and_revoke(store):
    token = store.add_user("nikita", role="admin")
    store.enrol(token, "correct horse battery")
    secret = store.create_session("nikita", device="browser:7f3a1b2c")
    assert store.resolve_session(secret) == {
        "handle": "nikita", "role": "admin", "device": "browser:7f3a1b2c"}
    store.revoke_session(secret)
    assert store.resolve_session(secret) is None
    assert store.resolve_session("not-a-session") is None


def test_token_resolve_revoke_and_expiry(store):
    bearer = store.add_token("ci", role="member", ttl_days=7)
    assert bearer.startswith("acad_")
    assert store.resolve_token(bearer) == {"name": "ci", "role": "member"}
    assert store.resolve_token("acad_deadbeef_" + "x" * 43) is None
    token_id = store.list_tokens()[0]["id"]
    store.revoke_token(token_id)
    assert store.resolve_token(bearer) is None


def test_expired_token_does_not_resolve(store, monkeypatch):
    bearer = store.add_token("short", ttl_days=1)
    monkeypatch.setattr(
        "agentcad.core.authstore._now", lambda: time.time() + 2 * 86400)
    assert store.resolve_token(bearer) is None


def test_secrets_are_not_stored_raw(store, tmp_path):
    bearer = store.add_token("ci")
    blob = (tmp_path / "auth" / "tokens.json").read_text()
    assert bearer.split("_")[2] not in blob


def test_a_second_process_writing_is_seen_without_restart(store, tmp_path):
    import subprocess, sys
    store.add_user("first")
    subprocess.run(
        [sys.executable, "-c",
         "from agentcad.core.authstore import AuthStore;"
         f"AuthStore({str(tmp_path / 'auth')!r}).add_user('second')"],
        check=True)
    assert {u["handle"] for u in store.list_users()} == {"first", "second"}
```

- [ ] **Step 6: Run them and confirm they fail**

Run: `.venv/bin/python -m pytest tests/test_authstore.py -q`
Expected: FAIL — `ModuleNotFoundError: agentcad.core.authstore`.

- [ ] **Step 7: Implement `agentcad/core/authstore.py`**

Four documents (`users.json`, `enrolments.json`, `sessions.json`,
`tokens.json`) under `root`, created `0700`. One private helper pair:

```python
@contextmanager
def _scope(self, name: str):
    """Serialise a read-modify-write in-process AND across processes.

    `agentcad admin ...` through `docker compose exec` is routinely a second
    writer while the server holds the same document in memory. This is the
    `LocalIndex._index_scope` situation exactly (core/packages/indexes.py:502);
    the lock file lives beside the documents and is never one of them.
    """
    with self._lock:                       # threading.Lock, one per store
        with open(self._root / ".lock", "a+b") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)
```

`_read(name)` stats the file and re-parses only when `st_mtime_ns`/`st_size`
changed (that is what makes the two-process test pass). `_write(name, doc)`
stages to `<name>.<random>.tmp` and `os.replace`s — the PRD-011 lesson that a
fixed staging name interleaves two writers into one corrupt file
(AGENTS.md, "Package gotchas"). `import fcntl` at module top is fine: the
module is only imported in hosted mode and by these tests, and slice 5 adds
the portability assertion that the *server* imports it lazily.

Passwords: `{"kdf": "scrypt", "n": 32768, "r": 8, "p": 1, "salt": <hex16>,
"digest": <hex64>}`. `verify_password` on an unknown or disabled handle runs
`scrypt` against a fixed dummy salt and returns `False`, so timing does not
separate the two cases. Secrets: `secrets.token_urlsafe(32)`; stored key is
`hashlib.sha256(secret.encode()).hexdigest()`. A bearer is
`f"acad_{token_id}_{secret}"`; `resolve_token` splits on `_`, looks up the id,
then `hmac.compare_digest`s the digest. `_now()` is a module-level
`time.time` indirection so the expiry test can move the clock. Sessions slide
`last_seen` only when the stored value is more than 86 400 s old, so a busy
session does not rewrite the document per request; absolute cap 30 days,
sliding window 14.

- [ ] **Step 8: Run the auth-store tests and confirm they pass**

Run: `.venv/bin/python -m pytest tests/test_authstore.py -q`
Expected: 9 passed.

- [ ] **Step 9: Prove nothing imports geometry**

Run:
```bash
.venv/bin/python -c "
import sys
class Block:
    def find_module(self, name, path=None):
        if name.split('.')[0] in {'OCP', 'build123d'}:
            raise ImportError(name)
sys.meta_path.insert(0, Block())
import agentcad.core.authstore, agentcad.core.appmode
print('ok')"
```
Expected: `ok`.

- [ ] **Step 10: Verification**

Run: `make test` — cite the pass count and confirm it is baseline + 14.

- [ ] **Step 11: Commit**

```bash
git add agentcad/core/appmode.py agentcad/core/authstore.py \
        tests/test_appmode.py tests/test_authstore.py
git commit -m "PRD-005a slice 1: app modes, state paths and the auth store"
```

---

## Slice 2 — the security seam

**Goal:** default-deny authorization exists and is proved, local mode is
byte-identically unchanged, and PRD-008's claims survive composed principals.

### Files

- Create: `agentcad/server/security.py`
- Modify: `agentcad/server/app.py` (the `security=` parameter, the middleware
  body, the WebSocket guard call)
- Modify: `agentcad/core/proposals.py:112-124` (two lines in `actor_kind`)
- Modify: `agentcad/core/model.py` (three new `AppError` subclasses) and
  `agentcad/server/app.py:34-38` (`_ERROR_STATUS` entries)
- Test: `tests/test_security_guard.py`, `tests/test_hosted_surface.py`,
  `tests/test_actor_kind.py`

### Interfaces

- Consumes: `appmode.AppMode`, `authstore.AuthStore` (slice 1);
  `locks.set_client_id`, `locks.check_client_id`
  (`agentcad/core/locks.py:84-111`).
- Produces:

```python
# agentcad/server/security.py
PUBLIC_PATHS: frozenset[str]   # exact paths
PUBLIC_PREFIXES: tuple[str, ...]  # ("/api/public/", "/api/auth/enrol/", "/js/", "/css/", "/vendor/")

@dataclass(frozen=True)
class Principal:
    kind: str        # "user" | "agent"
    name: str        # handle or token name
    role: str        # "admin" | "member"
    device: str | None
    @property
    def client_id(self) -> str: ...   # "user:nikita/browser:7f3a…" | "agent:ci"

@dataclass
class SecurityConfig:
    mode: AppMode
    store: AuthStore
    # agentcad.core.presence.TokenBucket, IMPORTED not re-implemented.
    # Its constructor is TokenBucket(rate=, burst=, clock=, limit=)
    # (agentcad/core/presence.py:173-181); login uses a slower rate than the
    # heartbeat default, e.g. TokenBucket(rate=0.2, burst=5).
    login_rate: TokenBucket = field(
        default_factory=lambda: TokenBucket(rate=0.2, burst=5))

def is_public(path: str) -> bool: ...
def resolve_principal(cfg: SecurityConfig, headers, cookies) -> Principal | None: ...
def guard(cfg: SecurityConfig | None, request) -> JSONResponse | None: ...
def guard_websocket(cfg: SecurityConfig | None, ws) -> bool: ...
def current_principal() -> Principal | None: ...   # ContextVar, for handlers
```

### Tasks

- [ ] **Step 1: Write the failing `actor_kind` test** — `tests/test_actor_kind.py`

```python
from agentcad.core.proposals import actor_kind


def test_local_classification_is_unchanged():
    assert actor_kind("browser") == "human"
    assert actor_kind("browser:7f3a1b2c") == "human"
    assert actor_kind("mcp") == "agent"
    assert actor_kind("chat:main") == "agent"
    assert actor_kind("local") == "agent"


def test_composed_principals_classify_correctly():
    assert actor_kind("user:nikita") == "human"
    assert actor_kind("user:nikita/browser:7f3a1b2c") == "human"
    assert actor_kind("agent:ci") == "agent"
    assert actor_kind("agent:mcp:claude") == "agent"
```

- [ ] **Step 2: Run it and confirm the second test fails**

Run: `.venv/bin/python -m pytest tests/test_actor_kind.py -q`
Expected: FAIL — `assert 'agent' == 'human'` for `user:nikita`.

- [ ] **Step 3: Change `actor_kind`** (`agentcad/core/proposals.py:112-124`)

```python
    if identity.startswith("user:"):
        return "human"
    if identity.startswith("agent:"):
        return "agent"
    return "human" if identity == "browser" or identity.startswith("browser:") else "agent"
```

Extend the docstring in place: the two new lines are the PRD-005 change the
existing docstring already commissions, and without them
`ClaimRegistry.acquire` (`core/locks.py:292-293`) refuses every hosted human a
claim and `_blocking` (`locks.py:398-399`) blocks nobody — PRD-008's
protection silently off.

- [ ] **Step 4: Run it and confirm both tests pass**

Run: `.venv/bin/python -m pytest tests/test_actor_kind.py -q`
Expected: 2 passed.

- [ ] **Step 5: Write the failing claim-parity test** — append to `tests/test_claims.py`

```python
def test_composed_principals_keep_prd008_claim_semantics(claims):
    """AC10: two hosted humans contend; a hosted agent neither takes nor is blocked."""
    alice = "user:alice/browser:aaaaaaaa"
    bob = "user:bob/browser:bbbbbbbb"
    agent = "agent:ci"
    assert claims.acquire("proj", "bracket", alice) is not None
    assert claims.acquire("proj", "bracket", bob) is None
    assert claims.acquire("proj", "bracket", agent) is None      # agents never hold
    assert claims.check("proj", "bracket", agent) is None        # agents never blocked
```

Bind `claims` to the same `ClaimRegistry` fixture the file already uses; keep
the assertion shapes identical to the neighbouring `browser:<nonce>` cases so
the parity is visible in the diff.

- [ ] **Step 6: Run it and confirm it passes** (step 3 already made it true)

Run: `.venv/bin/python -m pytest tests/test_claims.py -q`
Expected: all pass, one more than before.

- [ ] **Step 7: Add the shared hosted fixtures to `tests/conftest.py`**

Slices 2, 3, 4, 5 and 7 all need the same app, so the fixtures live in
`conftest.py` beside `make_test_service` (`tests/conftest.py:82`), not in one
test file:

```python
ORIGIN = "http://testserver"


@pytest.fixture
def hosted(kernel, tmp_path):
    """(client, store) for a hosted app with one enrolled admin, `nikita`."""
    from agentcad.core.appmode import AppMode
    from agentcad.core.authstore import AuthStore
    from agentcad.core.tools import build_registry
    from agentcad.server.app import create_app
    from agentcad.server.security import SecurityConfig

    service = make_test_service(tmp_path / "projects", kernel)
    store = AuthStore(tmp_path / "auth")
    store.enrol(store.add_user("nikita", role="admin"), "correct horse battery")
    cfg = SecurityConfig(mode=AppMode("hosted", ORIGIN, b"k" * 32), store=store)
    app = create_app(service, build_registry(service),
                     extra_allowed_hosts={"testserver"}, security=cfg)
    return TestClient(app, base_url=ORIGIN), store


@pytest.fixture
def hosted_client(hosted):
    return hosted[0]


@pytest.fixture
def hosted_app(hosted_client):
    return hosted_client.app


def login(client):
    """Sign `nikita` in. Slice 2 has no /api/auth/login, so this mints the
    session through the store; slice 3 may switch it to the route."""
    _, store = client._agentcad_hosted        # set by the `hosted` fixture
    client.cookies.set("agentcad_session",
                       store.create_session("nikita", device=None))
```

Import `login` as `_login` in the test modules that use it (slices 3, 5, 7).

- [ ] **Step 8: Write the failing guard tests** — `tests/test_security_guard.py`

```python
import pytest
from fastapi.testclient import TestClient

from agentcad.core.appmode import AppMode
from agentcad.core.authstore import AuthStore
from agentcad.core.tools import build_registry
from agentcad.server.app import create_app
from agentcad.server.security import SecurityConfig

from .conftest import make_test_service

ORIGIN = "http://testserver"


@pytest.fixture
def hosted(kernel, tmp_path):
    service = make_test_service(tmp_path / "projects", kernel)
    store = AuthStore(tmp_path / "auth")
    token = store.add_user("nikita", role="admin")
    store.enrol(token, "correct horse battery")
    cfg = SecurityConfig(mode=AppMode("hosted", ORIGIN, b"k" * 32), store=store)
    app = create_app(service, build_registry(service),
                     extra_allowed_hosts={"testserver"}, security=cfg)
    client = TestClient(app, base_url=ORIGIN)
    return client, store


def test_private_route_is_401_anonymously(hosted):
    client, _ = hosted
    r = client.get("/api/projects")
    assert r.status_code == 401
    assert r.json()["error"]["type"] == "AuthError"


def test_health_is_public_but_trimmed(hosted):
    client, _ = hosted
    body = client.get("/api/health").json()
    assert body == {"status": "ok", "mode": "hosted"}


def test_session_cookie_authenticates(hosted):
    # Slice 2 has no /api/auth/login yet — mint the session through the store,
    # which is also the tighter test: the guard, not the route, is what
    # authenticates.
    client, store = hosted
    client.cookies.set("agentcad_session",
                       store.create_session("nikita", device=None))
    assert client.get("/api/projects").status_code == 200


def test_bearer_authenticates_and_is_origin_exempt(hosted):
    client, store = hosted
    bearer = store.add_token("ci")
    r = client.post("/api/projects", json={"name": "demo"},
                    headers={"Authorization": f"Bearer {bearer}",
                             "Origin": "https://evil.example"})
    assert r.status_code == 201


def test_cookie_post_from_a_foreign_origin_is_403(hosted):
    client, store = hosted
    client.cookies.set("agentcad_session",
                       store.create_session("nikita", device=None))
    r = client.post("/api/projects", json={"name": "x"},
                    headers={"Origin": "https://evil.example"})
    assert r.status_code == 403


def test_a_bare_x_agent_id_is_not_an_identity(hosted):
    client, _ = hosted
    r = client.get("/api/projects", headers={"X-Agent-Id": "mcp"})
    assert r.status_code == 401


def test_wrong_host_is_refused(hosted):
    client, _ = hosted
    r = client.get("/api/health", headers={"Host": "elsewhere.example"})
    assert r.status_code == 403


def test_local_mode_installs_exactly_one_middleware(kernel, tmp_path):
    service = make_test_service(tmp_path / "projects", kernel)
    app = create_app(service, build_registry(service),
                     extra_allowed_hosts={"testserver"})
    assert len(app.user_middleware) == 1
```

- [ ] **Step 9: Write the failing surface tests** — `tests/test_hosted_surface.py`

```python
EXPECTED_PUBLIC = {
    ("GET", "/"),
    ("GET", "/api/health"),
    ("POST", "/api/auth/login"),
    ("GET", "/api/auth/enrol/{token}"),
    ("POST", "/api/auth/enrol/{token}"),
    ("GET", "/api/public/packages"),
    ("GET", "/api/public/packages/{name}"),
    ("GET", "/api/public/packages/{name}/versions/{version}"),
    ("GET", "/api/public/packages/{name}/versions/{version}/preview"),
}


# Routes named in EXPECTED_PUBLIC that this slice has not created yet.
# Slice 3 removes the three /api/auth ones; slice 7 removes the four
# /api/public ones. The final list is written ONCE, here, so the enumeration
# cannot drift slice by slice — and the emptiness assertion below is what
# stops a forgotten removal from passing silently.
NOT_YET_BUILT = {
    ("POST", "/api/auth/login"),
    ("GET", "/api/auth/enrol/{token}"),
    ("POST", "/api/auth/enrol/{token}"),
    ("GET", "/api/public/packages"),
    ("GET", "/api/public/packages/{name}"),
    ("GET", "/api/public/packages/{name}/versions/{version}"),
    ("GET", "/api/public/packages/{name}/versions/{version}/preview"),
}


def test_the_public_surface_is_exactly_this(hosted_app):
    """Fails when a new route pack goes public by accident (PRD-007 AC9, early)."""
    reachable = set()
    for route in hosted_app.routes:
        for method in getattr(route, "methods", ()) or ():
            if method in {"HEAD", "OPTIONS"}:
                continue
            if security.is_public(route.path):
                reachable.add((method, route.path))
    assert reachable == EXPECTED_PUBLIC - NOT_YET_BUILT


def test_static_mounts_are_public(hosted_client):
    """/js, /css and /vendor are Mounts, not Routes, so they cannot appear in
    EXPECTED_PUBLIC — assert them directly or they go untested."""
    assert hosted_client.get("/js/api.js").status_code == 200
    assert hosted_client.get("/").status_code == 200


def test_public_surface_makes_no_kernel_calls(hosted_client, kernel_counter):
    """AC7: nothing anonymous may reach exec() in the worker."""
    for method, path in sorted(EXPECTED_PUBLIC):
        hosted_client.request(method, _fill(path))
    assert kernel_counter.calls == 0
```

`kernel_counter` wraps `service.kernel.request` with a counting proxy;
`_fill` substitutes `din625` / `1.0.0` / a live enrolment token into the
templated paths and skips anything still in `NOT_YET_BUILT`.

- [ ] **Step 10: Run both files and confirm they fail**

Run: `.venv/bin/python -m pytest tests/test_security_guard.py tests/test_hosted_surface.py -q`
Expected: FAIL — `create_app() got an unexpected keyword argument 'security'`.

- [ ] **Step 11: Implement `agentcad/server/security.py`**

`PUBLIC_PATHS` is a literal frozenset of the exact paths in `EXPECTED_PUBLIC`;
`PUBLIC_PREFIXES` covers `/api/public/`, `/api/auth/enrol/`, `/js/`, `/css/`,
`/vendor/`. `is_public(path)` is exact-membership OR prefix.
`resolve_principal` tries `Authorization: Bearer` first (`store.resolve_token`)
then the `agentcad_session` cookie (`store.resolve_session`), and composes the
device suffix from a `check_client_id`-validated `X-Agent-Id` for the cookie
path only. `guard` implements the eight ordered steps of design Decision 7 and
returns `None` to allow. `current_principal()` reads a module ContextVar that
`guard` sets, so `routes_auth.py` and the trimmed health body can read the
role without re-resolving. Never raise out of `guard` — return the structured
`JSONResponse` so an auth failure cannot become a 500.

- [ ] **Step 12: Edit `agentcad/server/app.py`** — the only logic-free diff

```python
def create_app(service, registry, chat_engine=None,
               extra_allowed_hosts=frozenset(), security=None) -> FastAPI:
    ...
    @app.middleware("http")
    async def local_origin_guard(request: Request, call_next):
        if security is not None:
            denied = sec.guard(security, request)
            if denied is not None:
                return denied
            return await call_next(request)
        # --- unchanged local-mode path below this line ---
        allowed, reason = _browser_request_allowed(request.headers, allowed_hosts)
        ...
```

and in `websocket_endpoint`, before `await ws.accept()`:

```python
        if security is not None and not sec.guard_websocket(security, ws):
            await ws.close(code=1008)
            return
```

Add `AuthError`/`AuthzError`/`RateLimitedError` to
`agentcad/core/model.py` and to `_ERROR_STATUS` (`app.py:34-38`) as
401/403/429. Nothing else in `app.py` changes in this slice.

- [ ] **Step 13: Run the tests and confirm they pass**

Run: `.venv/bin/python -m pytest tests/test_security_guard.py tests/test_hosted_surface.py -q`
Expected: all pass, with `NOT_YET_BUILT` holding seven entries.

- [ ] **Step 14: Verification**

Run: `make test` — cite the count; the whole existing suite must be green
with **no edits** to existing test files other than the one appended claim
test. That is AC9.

- [ ] **Step 15: Commit**

```bash
git add agentcad/server/security.py agentcad/server/app.py \
        agentcad/core/proposals.py agentcad/core/model.py \
        tests/test_security_guard.py tests/test_hosted_surface.py \
        tests/test_actor_kind.py tests/test_claims.py
git commit -m "PRD-005a slice 2: default-deny security seam, composed principals"
```

---

## Slice 3 — enrolment, login, sessions and the sign-in UI

**Goal:** a human can be invited from the CLI and sign in from a browser.

### Files

- Create: `agentcad/server/routes_auth.py`
- Create: `frontend/js/auth.js`
- Modify: `agentcad/cli.py` (the `admin` subcommand group: `user add|list|disable`)
- Modify: `frontend/js/api.js:72-96` (401 handling), `frontend/js/main.js`
  (mount the sign-in view), `frontend/index.html` (the view's container)
- Test: `tests/test_auth_routes.py`, `tests/test_cli_admin.py`

### Interfaces

- Consumes: `security.SecurityConfig`, `security.current_principal`,
  `authstore.AuthStore` (slices 1–2).
- Produces: `POST /api/auth/login {handle, password}` → `{principal, role}` +
  `Set-Cookie`; `POST /api/auth/logout` → `{ok: true}`; `GET
  /api/auth/session` → `{principal, kind, role, mode}` or 401; `GET|POST
  /api/auth/enrol/{token}`; admin-only `GET /api/auth/users`,
  `POST /api/auth/users {handle, role}` → `{enrol_url}`.

### Tasks

- [ ] **Step 1: Write the failing route tests** — `tests/test_auth_routes.py`

```python
def test_login_sets_a_session_cookie_and_session_reads_back(hosted):
    client, _ = hosted
    r = client.post("/api/auth/login",
                    json={"handle": "nikita", "password": "correct horse battery"})
    assert r.status_code == 200
    assert "agentcad_session" in r.cookies
    assert client.get("/api/auth/session").json() == {
        "principal": "user:nikita", "kind": "user",
        "role": "admin", "mode": "hosted"}


def test_logout_revokes_immediately(hosted):
    client, _ = hosted
    client.post("/api/auth/login",
                json={"handle": "nikita", "password": "correct horse battery"})
    assert client.post("/api/auth/logout").status_code == 200
    assert client.get("/api/auth/session").status_code == 401


def test_unknown_handle_and_wrong_password_are_indistinguishable(hosted):
    client, _ = hosted
    a = client.post("/api/auth/login", json={"handle": "nikita", "password": "no"})
    b = client.post("/api/auth/login", json={"handle": "ghost", "password": "no"})
    assert a.status_code == b.status_code == 401
    assert a.json() == b.json()


def test_login_is_rate_limited_with_retry_after(hosted):
    client, _ = hosted
    codes = [client.post("/api/auth/login",
                         json={"handle": "nikita", "password": "no"}).status_code
             for _ in range(25)]
    assert 429 in codes
    r = client.post("/api/auth/login", json={"handle": "nikita", "password": "no"})
    assert r.json()["error"]["details"]["retry_after_s"] > 0


def test_enrolment_is_public_single_use_and_signs_you_in(hosted):
    client, store = hosted
    token = store.add_user("anya")
    r = client.post(f"/api/auth/enrol/{token}", json={"password": "hunter2hunter2"})
    assert r.status_code == 200
    assert client.get("/api/auth/session").json()["principal"] == "user:anya"
    fresh = TestClient(client.app, base_url=ORIGIN)
    assert fresh.post(f"/api/auth/enrol/{token}",
                      json={"password": "again"}).status_code == 404


def test_member_is_403_on_admin_routes(hosted):
    client, store = hosted
    token = store.add_user("anya")           # role="member"
    client.post(f"/api/auth/enrol/{token}", json={"password": "hunter2hunter2"})
    r = client.post("/api/auth/users", json={"handle": "mallory"})
    assert r.status_code == 403
    assert r.json()["error"]["type"] == "AuthzError"


def test_no_route_ever_returns_a_digest(hosted):
    client, _ = hosted
    client.post("/api/auth/login",
                json={"handle": "nikita", "password": "correct horse battery"})
    body = client.get("/api/auth/users").text
    assert "digest" not in body and "salt" not in body
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `.venv/bin/python -m pytest tests/test_auth_routes.py -q`
Expected: FAIL — 404 on `/api/auth/login`.

- [ ] **Step 3: Implement `agentcad/server/routes_auth.py`**

A `build_router(service, registry)` pack. It reads its `SecurityConfig` from
`security.current_config()` (a module-level slot `create_app` sets) rather
than from `service`, because identity is app-layer state and must never be a
service attribute. Login takes the bucket **before** the scrypt call, so a
flood cannot spend CPU. `Set-Cookie` uses `httponly=True`,
`samesite="lax"`, `secure=cfg.mode.secure_cookies`, `path="/"`. Admin routes
check `security.current_principal().role == "admin"` and raise `AuthzError`.
The enrol POST creates the session in the same response.

- [ ] **Step 4: Remove the three `/api/auth` entries from `NOT_YET_BUILT`**

Edit `tests/test_hosted_surface.py`. `test_the_public_surface_is_exactly_this`
then proves the three routes really are anonymous-reachable, and
`test_public_surface_makes_no_kernel_calls` starts exercising them.

- [ ] **Step 5: Run the route and surface tests**

Run: `.venv/bin/python -m pytest tests/test_auth_routes.py tests/test_hosted_surface.py -q`
Expected: all pass.

- [ ] **Step 6: Write the failing CLI test** — `tests/test_cli_admin.py`

```python
def test_admin_user_add_prints_an_enrol_url_and_the_trust_sentence(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("AGENTCAD_CONFIG", str(tmp_path / "config.json"))
    from agentcad.cli import main
    monkeypatch.setattr("sys.argv",
                        ["agentcad", "admin", "user", "add", "nikita", "--admin"])
    main()
    out = capsys.readouterr().out
    assert "/api/auth/enrol/" in out
    assert "execute arbitrary Python on the host" in out


def test_admin_user_add_refuses_a_bad_handle(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTCAD_CONFIG", str(tmp_path / "config.json"))
    from agentcad.cli import main
    monkeypatch.setattr("sys.argv", ["agentcad", "admin", "user", "add", "Nikita"])
    with pytest.raises(SystemExit):
        main()
```

- [ ] **Step 7: Run it and confirm it fails**

Run: `.venv/bin/python -m pytest tests/test_cli_admin.py -q`
Expected: FAIL — `invalid choice: 'admin'`.

- [ ] **Step 8: Add the `admin` subcommand group to `agentcad/cli.py`**

`cmd_admin` follows `cmd_check`'s shape. It builds **no service and no
kernel** — it constructs `AuthStore(appmode.state_dir() / "auth")` directly,
which is what makes `docker compose exec` cheap. `user add` prints the enrol
URL (prefixed with `AGENTCAD_PUBLIC_ORIGIN` when set) and the trust sentence
verbatim from the global constraints. `--help` carries the same sentence.

- [ ] **Step 9: Run the CLI tests**

Run: `.venv/bin/python -m pytest tests/test_cli_admin.py -q`
Expected: 2 passed.

- [ ] **Step 10: Add the frontend sign-in path**

`frontend/js/api.js`: in the single `request()` funnel
(`api.js:72-96`), on `res.status === 401` dispatch a
`agentcad:unauthenticated` window event before throwing, so exactly one place
knows. `frontend/js/auth.js`: `session()`, `login(handle, password)`,
`logout()`, `enrol(token, password)` plus a `renderSignIn(container)` view.
`main.js` calls `auth.session()` at boot; on 401 it renders the sign-in view
instead of the workbench, and re-boots on success. The identity chip renders
`principal`. No new vendor, no bundler.

- [ ] **Step 11: Verify in a real browser**

Use the **`run` skill** with `AGENTCAD_MODE=hosted`,
`AGENTCAD_PUBLIC_ORIGIN=http://127.0.0.1:8630`,
`AGENTCAD_CONFIG` and `--projects-dir` pointed at the scratchpad. Create a
user with `agentcad admin user add`, open the enrol URL, set a password, and
screenshot: (a) the sign-in view, (b) the workbench with the handle in the
chip, (c) a lock chip reading `user:<handle>` during an edit.

- [ ] **Step 12: Verification**

Run: `make test` — cite the count.

- [ ] **Step 13: Commit**

```bash
git add agentcad/server/routes_auth.py agentcad/cli.py frontend/js/auth.js \
        frontend/js/api.js frontend/js/main.js frontend/index.html \
        tests/test_auth_routes.py tests/test_cli_admin.py \
        tests/test_hosted_surface.py
git commit -m "PRD-005a slice 3: enrolment, login, sessions and the sign-in view"
```

---

## Slice 4 — bearer tokens, `whoami`, and remote MCP

**Goal:** an agent holds a revocable credential and drives a hosted instance.

### Files

- Create: `agentcad/core/tools_auth.py`
- Modify: `agentcad/cli.py` (the `admin token add|list|revoke` subcommands)
- Modify: `agentcad/agent/mcp_server.py:41-45`, `:66-101`, `:127-131`
- Test: `tests/test_tokens.py`, `tests/test_mcp_remote.py`

### Interfaces

- Consumes: `AuthStore.add_token/list_tokens/revoke_token/resolve_token`
  (slice 1), `security.current_principal` (slice 2).
- Produces: the `whoami` tool → `{principal, kind, role, mode}`;
  `GET|POST|DELETE /api/auth/tokens` (admin only);
  `AGENTCAD_TOKEN` honoured by the MCP proxy.

### Tasks

- [ ] **Step 1: Write the failing token tests** — `tests/test_tokens.py`

```python
def test_a_token_authenticates_and_whoami_answers(hosted):
    client, store = hosted
    bearer = store.add_token("ci", role="member")
    h = {"Authorization": f"Bearer {bearer}"}
    assert client.post("/api/tools/whoami", json={}, headers=h).json() == {
        "principal": "agent:ci", "kind": "agent",
        "role": "member", "mode": "hosted"}


def test_revocation_takes_effect_on_the_next_call(hosted):
    client, store = hosted
    bearer = store.add_token("ci")
    h = {"Authorization": f"Bearer {bearer}"}
    assert client.get("/api/projects", headers=h).status_code == 200
    store.revoke_token(store.list_tokens()[0]["id"])
    assert client.get("/api/projects", headers=h).status_code == 401


def test_the_secret_is_returned_exactly_once(hosted):
    client, _ = hosted
    client.post("/api/auth/login",
                json={"handle": "nikita", "password": "correct horse battery"})
    created = client.post("/api/auth/tokens", json={"name": "ci"}).json()
    assert created["token"].startswith("acad_")
    listed = client.get("/api/auth/tokens").json()["tokens"]
    assert "token" not in listed[0] and "digest" not in listed[0]


def test_whoami_is_not_registered_in_local_mode(kernel, tmp_path):
    service = make_test_service(tmp_path / "projects", kernel)
    names = {t.name for t in build_registry(service).list()}
    assert "whoami" not in names
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `.venv/bin/python -m pytest tests/test_tokens.py -q`
Expected: FAIL — unknown tool `whoami`.

- [ ] **Step 3: Implement `agentcad/core/tools_auth.py`**

`register(registry, service)` registers `whoami` **only when
`security.current_config()` is not None** — the FEM-tools precedent, "register
a tool only if it can run". The handler reads
`security.current_principal()` and returns the four fields. Add the token
management routes to `routes_auth.py` (admin only), returning the plaintext
secret exactly once on create.

- [ ] **Step 4: Run the token tests**

Run: `.venv/bin/python -m pytest tests/test_tokens.py -q`
Expected: 4 passed.

- [ ] **Step 5: Add `admin token add|list|revoke` to `agentcad/cli.py`**

Same shape as `admin user`: direct `AuthStore`, no service, secret printed
once with a "this is the only time it is shown" line.

- [ ] **Step 6: Write the failing MCP test** — `tests/test_mcp_remote.py`

```python
def test_the_proxy_sends_a_bearer_when_configured(monkeypatch):
    monkeypatch.setenv("AGENTCAD_URL", "https://cad.example.com")
    monkeypatch.setenv("AGENTCAD_TOKEN", "acad_deadbeef_" + "x" * 43)
    from agentcad.agent import mcp_server
    headers = mcp_server._client_headers()
    assert headers["Authorization"].startswith("Bearer acad_")
    assert headers["X-Agent-Id"] == "mcp"


def test_a_remote_url_is_never_auto_spawned(monkeypatch):
    monkeypatch.setenv("AGENTCAD_URL", "https://cad.example.com")
    from agentcad.agent import mcp_server
    assert mcp_server._may_autostart() is False
    monkeypatch.setenv("AGENTCAD_URL", "http://127.0.0.1:8630")
    assert mcp_server._may_autostart() is True
```

- [ ] **Step 7: Run it and confirm it fails**

Run: `.venv/bin/python -m pytest tests/test_mcp_remote.py -q`
Expected: FAIL — `module has no attribute '_client_headers'`.

- [ ] **Step 8: Edit `agentcad/agent/mcp_server.py`**

Extract the header dict at `:127-131` into `_client_headers()` adding
`Authorization` when `AGENTCAD_TOKEN` is set; add `_may_autostart()` returning
`False` when `AGENTCAD_URL`'s host is not loopback, and gate the spawn at
`:66-101` on it — auto-starting a local server because a *remote* one is
unreachable is a footgun, not a convenience.

- [ ] **Step 9: Run the MCP tests**

Run: `.venv/bin/python -m pytest tests/test_mcp_remote.py -q`
Expected: 2 passed.

- [ ] **Step 10: Verification**

Run: `make test` — cite the count.

- [ ] **Step 11: Commit**

```bash
git add agentcad/core/tools_auth.py agentcad/server/routes_auth.py \
        agentcad/cli.py agentcad/agent/mcp_server.py \
        tests/test_tokens.py tests/test_mcp_remote.py
git commit -m "PRD-005a slice 4: agent bearer tokens, whoami, remote MCP"
```

---

## Slice 5 — hosted-mode hardening

**Goal:** every carry-over the design's threat model names is closed, with a
test each.

### Files

- Modify: `agentcad/cli.py` (`--host`, `AGENTCAD_HOST`/`AGENTCAD_PORT`, the
  `check_bind` interlock, the `AGENTCAD_EXAMPLES` skip)
- Modify: `agentcad/server/app.py:152-164` (the trimmed health body),
  `:177-180` (`/api/projects/open` refusal)
- Modify: `agentcad/server/routes_presence.py:128-144` (beacon identity
  binding), `agentcad/core/tools_import.py:13-19` (absolute-path refusal)
- Test: `tests/test_hosted_hardening.py`

> **Note on the two-file exception.** `routes_presence.py` and
> `tools_import.py` are *packs*, not cores, and both changes are guarded by
> `if security.current_config() and security.current_config().mode.hosted:` so
> local behaviour is untouched. This is the only place the plan touches a
> PRD-008 file, and it closes a review finding that PRD-008 itself recorded.

### Tasks

- [ ] **Step 1: Write the failing hardening tests** — `tests/test_hosted_hardening.py`

```python
def test_projects_open_is_refused_in_hosted_mode(hosted, tmp_path):
    client, _ = hosted
    _login(client)
    r = client.post("/api/projects/open", json={"path": str(tmp_path)})
    assert r.status_code == 403
    assert "hosted" in r.json()["error"]["message"]


def test_import_cad_file_absolute_path_is_refused(hosted, tmp_path):
    client, _ = hosted
    _login(client)
    r = client.post("/api/tools/import_cad_file",
                    json={"project": "demo", "path": str(tmp_path / "x.step")})
    assert r.status_code == 403


def test_a_beacon_may_not_name_another_principal(hosted):
    client, _ = hosted
    _login(client)                                   # user:nikita/...
    r = client.post("/api/projects/demo/presence",
                    json={"leave": True, "client_id": "user:anya/browser:bbbbbbbb"})
    assert r.status_code == 422


def test_a_beacon_naming_your_own_device_is_accepted(hosted):
    client, _ = hosted
    _login(client)
    r = client.post("/api/projects/demo/presence",
                    json={"leave": True,
                          "client_id": "user:nikita/browser:7f3a1b2c"},
                    headers={"X-Agent-Id": "browser:7f3a1b2c"})
    assert r.status_code == 200


def test_health_is_full_for_a_principal(hosted):
    client, _ = hosted
    _login(client)
    body = client.get("/api/health").json()
    assert {"version", "kernel", "sandbox", "chat_available"} <= set(body)


def test_serve_refuses_a_public_bind_in_local_mode(monkeypatch, capsys):
    from agentcad.cli import main
    monkeypatch.setattr("sys.argv",
                        ["agentcad", "serve", "--host", "0.0.0.0", "--no-open"])
    with pytest.raises(SystemExit):
        main()
    assert "AGENTCAD_MODE=hosted" in capsys.readouterr().err


def test_examples_are_skipped_when_disabled(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENTCAD_EXAMPLES", "0")
    from agentcad import cli
    service = _StubService()
    cli._register_examples(service)
    assert service.opened == []
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `.venv/bin/python -m pytest tests/test_hosted_hardening.py -q`
Expected: seven failures.

- [ ] **Step 3: Implement the seven refusals**

Each is a guarded early return raising the house error. `--host` defaults to
`os.environ.get("AGENTCAD_HOST", "127.0.0.1")` and passes through
`appmode.check_bind(mode, host)` before `uvicorn.run`; the `ModeError` is
caught in `main()` and turned into a `SystemExit(2)` with the message on
stderr. `_register_examples` returns early when
`os.environ.get("AGENTCAD_EXAMPLES", "1") == "0"`.

- [ ] **Step 4: Run the hardening tests**

Run: `.venv/bin/python -m pytest tests/test_hosted_hardening.py -q`
Expected: 7 passed.

- [ ] **Step 5: Add the portability assertion**

`tests/test_portability.py` (or the nearest existing portability module): the
*server* must not import `fcntl` at module scope on a platform without it —
assert `agentcad.server.app` imports cleanly with `fcntl` blocked at
`sys.meta_path`, i.e. `authstore` is imported lazily inside
`create_app`'s caller. Mark it `@pytest.mark.portability`.

- [ ] **Step 6: Verification**

Run: `make test` and `make test-portability` — cite both counts.

- [ ] **Step 7: Commit**

```bash
git add agentcad/cli.py agentcad/server/app.py \
        agentcad/server/routes_presence.py agentcad/core/tools_import.py \
        tests/test_hosted_hardening.py tests/test_portability.py
git commit -m "PRD-005a slice 5: hosted-mode hardening and the bind interlock"
```

---

## Slice 6 — deployment

**Goal:** `docker compose up` serves the real app with persistent state.

### Files

- Create: `Dockerfile`, `compose.yaml`, `.dockerignore`, `.env.example`,
  `docs/deployment.md`
- Modify: `agentcad/server/app.py:401-414` (the two-line `PREFIX` seam)
- Test: `tests/test_deploy_config.py`, `tests/test_route_prefix.py`

### Tasks

- [ ] **Step 1: Write the failing `PREFIX` test** — `tests/test_route_prefix.py`

```python
def test_a_pack_may_declare_its_own_prefix(kernel, tmp_path, monkeypatch):
    """PRD-007 needs /s/<token> at the root; a pack cannot express that today."""
    module = types.ModuleType("agentcad.server.routes_zzprefixprobe")
    module.PREFIX = ""
    router = APIRouter()

    @router.get("/probe")
    def probe():
        return {"ok": True}

    module.router = router
    monkeypatch.setitem(sys.modules, module.__name__, module)
    ...  # register it with the pkgutil walk via a temp file under agentcad/server/
    client = _client(kernel, tmp_path)
    assert client.get("/probe").json() == {"ok": True}


def test_packs_without_a_prefix_still_mount_under_api(kernel, tmp_path):
    client = _client(kernel, tmp_path)
    assert client.get("/api/materials").status_code == 200
```

- [ ] **Step 2: Run it and confirm the first test fails**

Run: `.venv/bin/python -m pytest tests/test_route_prefix.py -q`
Expected: FAIL — 404 on `/probe`.

- [ ] **Step 3: Edit `_mount_route_packs`** (`agentcad/server/app.py:411-414`)

```python
        if router is not None:
            prefix = getattr(module, "PREFIX", "/api")
            app.include_router(router, prefix=prefix)
```

Comment it as PRD-007's seam so the next reader knows why an unused feature
exists.

- [ ] **Step 4: Run both tests**

Run: `.venv/bin/python -m pytest tests/test_route_prefix.py -q`
Expected: 2 passed.

- [ ] **Step 5: Write the `Dockerfile`**

Multi-stage. Builder: `python:3.12-slim`, `uv sync --locked --no-dev`.
Runtime: `python:3.12-slim`, `apt-get install --no-install-recommends libgl1
libglu1-mesa libxrender1 libxcursor1 libxft2 libxinerama1 git` — the six OCCT
libraries the Linux CI job already proves are needed
(`.github/workflows/ci.yml`, "Install OCCT system libraries (Linux)"), **plus
`git`**, which the history engine shells out to (`core/history.py:47-48`) and
which CI never had to install because runners ship it. Non-root `agentcad`
user, `HOME=/data/home`, `WORKDIR /app`, `EXPOSE 8630`,
`CMD ["agentcad", "serve", "--no-open"]`.

- [ ] **Step 6: Write `compose.yaml` and `.env.example`**

One `agentcad` service, one named volume at `/data`, the environment block
from FR24, a `healthcheck` running
`python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8630/api/health')"`,
`restart: unless-stopped`, and a `proxy` profile with Caddy. The file's header
comment carries the trust sentence verbatim.

- [ ] **Step 7: Write the failing config test** — `tests/test_deploy_config.py`

```python
def test_compose_pins_the_invariants():
    doc = _parse_compose(Path("compose.yaml"))          # hand-rolled, no PyYAML
    svc = doc["services"]["agentcad"]
    env = svc["environment"]
    assert env["AGENTCAD_MODE"] == "hosted"
    assert env["AGENTCAD_EXAMPLES"] == "0"
    assert env["AGENTCAD_PROJECTS_DIR"].startswith("/data")
    assert any(v.endswith(":/data") for v in svc["volumes"])
    assert "healthcheck" in svc


def test_no_secret_is_committed():
    text = Path("compose.yaml").read_text()
    assert "AGENTCAD_SECRET_KEY: ${AGENTCAD_SECRET_KEY" in text
    assert "changeme" not in text.lower()


def test_the_trust_sentence_is_in_the_compose_header_and_the_docs():
    sentence = "execute arbitrary Python on the host"
    assert sentence in Path("compose.yaml").read_text()
    assert sentence in Path("docs/deployment.md").read_text()


def test_the_dockerfile_installs_git_and_the_occt_libraries():
    text = Path("Dockerfile").read_text()
    for pkg in ("libgl1", "libglu1-mesa", "libxrender1", "libxcursor1",
                "libxft2", "libxinerama1", "git"):
        assert pkg in text
```

`_parse_compose` is ~20 lines of indentation-aware parsing over the two levels
this file uses — **no PyYAML**, because the global constraints forbid a new
runtime dependency and this is the only YAML in the tree.

- [ ] **Step 8: Run it and confirm it passes**

Run: `.venv/bin/python -m pytest tests/test_deploy_config.py -q`
Expected: 4 passed.

- [ ] **Step 9: Write `docs/deployment.md`**

Sections: the trust statement (verbatim, first, before any instruction) ·
quick start · the full environment table (FR24) · TLS (bring-your-own, with
an nginx and a Caddy snippet; then `--profile proxy`) · sizing (≈0.5 GB per
kernel worker; 2 vCPU / 4 GB floor with `AGENTCAD_KERNEL_POOL_SIZE=1`,
4 vCPU / 8 GB for 3) · creating the first admin over `docker compose exec` ·
minting agent tokens and wiring MCP · backup (`docker compose stop` is *not*
required — every write is an atomic replace; `tar czf` the volume) · restore ·
upgrade · what PRD-006 will change.

- [ ] **Step 10: Build and run it for real**

```bash
docker compose build
docker compose up -d
docker compose exec agentcad agentcad admin user add nikita --admin
curl -s localhost:8630/api/health
```
Cite the real output of all four. Open the enrol URL in a browser and
screenshot the signed-in workbench.

- [ ] **Step 11: Verification**

Run: `make test` — cite the count.

- [ ] **Step 12: Commit**

```bash
git add Dockerfile compose.yaml .dockerignore .env.example \
        docs/deployment.md agentcad/server/app.py \
        tests/test_deploy_config.py tests/test_route_prefix.py
git commit -m "PRD-005a slice 6: Dockerfile, compose, the PREFIX seam, deployment docs"
```

---

## Slice 7 — public read

**Goal:** the anonymous catalog surface exists, is scope-filtered, and still
makes zero kernel calls.

### Files

- Create: `agentcad/server/routes_public.py`
- Test: `tests/test_public_catalog.py`; extend `tests/test_hosted_surface.py`

### Interfaces

- Consumes: `service.packages.indexes` and each index's `.scope`
  (`agentcad/core/packages/indexes.py:105-110`),
  `content.resolve_within` (`core/packages/content.py`, the containment rule
  `routes_packages.py:163` already uses).
- Produces: the four `GET /api/public/packages…` routes.

### Tasks

- [ ] **Step 1: Write the failing public-read tests** — `tests/test_public_catalog.py`

```python
def test_the_bundled_catalog_lists_anonymously(hosted_with_catalog):
    client = hosted_with_catalog
    names = {p["name"] for p in client.get("/api/public/packages").json()["packages"]}
    assert "iso4762" in names


def test_a_version_carries_the_pregenerated_metadata(hosted_with_catalog):
    body = hosted_with_catalog.get(
        "/api/public/packages/din625/versions/1.0.0").json()
    assert body["gate"]["status"] == "green"
    assert body["parts"]["ball_bearing"]["connectors"] == {
        "bore": "cylindrical", "face": "rigid"}
    assert body["previews"] == ["previews/ball_bearing_iso.png"]


def test_a_preview_png_is_served_anonymously(hosted_with_catalog):
    r = hosted_with_catalog.get(
        "/api/public/packages/din625/versions/1.0.0/preview"
        "?path=previews/ball_bearing_iso.png")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.headers["cache-control"] == "public, max-age=300"


def test_a_private_index_is_invisible_and_indistinguishable(hosted_with_private):
    client, private_name = hosted_with_private
    listed = {p["name"] for p in client.get("/api/public/packages").json()["packages"]}
    assert private_name not in listed
    a = client.get(f"/api/public/packages/{private_name}")
    b = client.get("/api/public/packages/does-not-exist-at-all")
    assert a.status_code == b.status_code == 404
    assert a.json() == b.json()


def test_a_preview_path_may_not_escape_the_version_directory(hosted_with_catalog):
    r = hosted_with_catalog.get(
        "/api/public/packages/din625/versions/1.0.0/preview"
        "?path=../../../../etc/passwd")
    assert r.status_code in (404, 422)


def test_a_non_png_preview_is_refused(hosted_with_catalog):
    r = hosted_with_catalog.get(
        "/api/public/packages/din625/versions/1.0.0/preview"
        "?path=parts/ball_bearing.py")
    assert r.status_code == 422


def test_the_authenticated_search_route_still_sees_private_indexes(hosted_with_private):
    client, private_name = hosted_with_private
    _login(client)
    # `search.search` returns {"hits": [...], "indexes": [...], ...} —
    # `hits`, never `results` (agentcad/core/packages/search.py:103).
    hits = client.get("/api/packages/search").json()["hits"]
    assert private_name in {h["name"] for h in hits}
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `.venv/bin/python -m pytest tests/test_public_catalog.py -q`
Expected: 404 on every public path.

- [ ] **Step 3: Implement `agentcad/server/routes_public.py`**

```python
def _public_indexes(service):
    """Indexes an anonymous caller may read.

    `routes_packages.py`'s search and preview walk EVERY configured index,
    including a user's `scope: "private"` git index — exposing them would leak
    it. The scope property is PRD-011's, already load-bearing at publish time
    (indexes.py:490-498); this route pack is the only consumer that filters on
    it for access rather than for policy.
    """
    return [ix for ix in getattr(service, "packages").indexes
            if ix.scope == "public"]
```

Four handlers, all pure file reads. Every miss raises the *same*
`NotFoundError` message so a private package and a nonexistent one are
indistinguishable. The preview handler reuses the exact containment of
`routes_packages.py:160-167` — `.png` suffix check then
`content.resolve_within` — and adds `Cache-Control: public, max-age=300`.
**No handler may touch `service.kernel`, `service.store` or the registry.**

- [ ] **Step 4: Empty `NOT_YET_BUILT` in `tests/test_hosted_surface.py`**

Remove the four `/api/public` entries, leaving `NOT_YET_BUILT = set()`. The
public surface is now complete at nine entries and
`test_public_surface_makes_no_kernel_calls` exercises all of them.

- [ ] **Step 5: Run the public and surface tests**

Run: `.venv/bin/python -m pytest tests/test_public_catalog.py tests/test_hosted_surface.py -q`
Expected: all pass, including
`test_public_surface_makes_no_kernel_calls` with the four new paths exercised.

- [ ] **Step 6: Verification**

Run: `make test` — cite the count.

- [ ] **Step 7: Commit**

```bash
git add agentcad/server/routes_public.py tests/test_public_catalog.py \
        tests/test_hosted_surface.py
git commit -m "PRD-005a slice 7: scope-filtered public catalog read, kernel-free"
```

---

## Slice 8 — CI, documentation, and the acceptance pass

**Goal:** the deployment is smoke-tested in CI, the docs tell the truth, and
every AC is verified with cited output.

### Files

- Create: `.github/workflows/deploy-smoke.yml`
- Modify: `.github/workflows/ci.yml` (the `docker compose config` lint step)
- Modify: `AGENTS.md` (a "Hosted-core gotchas (PRD-005a)" section),
  `docs/architecture.md` (the trust-model section and the process diagram),
  `docs/agent-api.md` (`whoami`, the three new error types, the identity
  paragraph at `:892`), `docs/user-guide.md`, `CLAUDE.md` (the condensed traps)
- Modify: `docs/roadmap.md` (the 005a row + the 005 remainder row),
  `docs/prd/pending/PRD-005-multi-tenant-cloud.md` (the carve-out header)
- Move: `docs/prd/in-progress/PRD-005a-hosted-core.md` →
  `docs/prd/completed/` once every AC is verified
- Create: `docs/changelog/NNNN-hosted-core.md`

### Tasks

- [ ] **Step 1: Add the PR-cheap compose lint to `ci.yml`**

A step in the existing `ubuntu-latest` portability job:
`docker compose -f compose.yaml config --quiet`. Seconds, no build, catches a
malformed compose file on every PR.

- [ ] **Step 2: Write `.github/workflows/deploy-smoke.yml`**

`on: push: branches: [main]`, `schedule` weekly, `workflow_dispatch`.
Explicitly **not** on `pull_request`: the OCCT wheels make the image multi-GB
and the build minutes, which is the same split `ci.yml` and `geometry-ci.yml`
already make. Steps: checkout · `docker compose build` with layer caching ·
`docker compose up -d` · wait for the healthcheck · assert
`/api/health` reports `{"mode": "hosted"}` and nothing else ·
`docker compose exec -T agentcad agentcad admin user add smoke --admin` and
capture the enrol URL · enrol + login with `curl -c/-b` · assert
`GET /api/projects` is 200 with the cookie and 401 without · assert
`GET /api/public/packages` is 200 anonymously · `docker compose down &&
docker compose up -d` and assert the account still exists ·
`docker compose logs` on failure.

- [ ] **Step 3: Run the smoke workflow once via `workflow_dispatch` and cite the run URL**

- [ ] **Step 4: Write the `AGENTS.md` gotcha section**

Every bullet traceable to a decision or a test. At minimum: *`actor_kind` must
classify `user:` as human or every hosted person loses their claims* · *the
public surface is nine entries in one frozenset and default-deny means a new
pack is private* · *`routes_packages.py`'s search/preview walk every index
including private ones — the public read is a separate pack that filters on
`scope`* · *identity state derives from `config.config_path().parent`, never
from `--projects-dir`, which is why ephemeral services are unaffected* ·
*`fcntl.flock` because `docker compose exec` is a second writer* · *tokens are
sha256 not scrypt, and the asymmetry is deliberate* · *`create_app`'s
`security=None` is the same code path, not a disabled feature* ·
*an account is a shell until PRD-006.* Add the condensed version to
`CLAUDE.md`.

- [ ] **Step 5: Update `docs/architecture.md`**

The process diagram gains the hosted variant; the **Trust model** section
(`architecture.md:728-761`) gains a hosted-mode paragraph saying plainly that
Linux has no confinement, that an account is therefore a shell, and that the
anonymous surface is nine kernel-free entries.

- [ ] **Step 6: Update `docs/agent-api.md`**

`whoami`; `auth_error`/`permission_error`/`rate_limited`; and rewrite the
"Identity is self-asserted" paragraph at `:892` to distinguish local mode
(unchanged) from hosted mode (`X-Agent-Id` is not an identity).

- [ ] **Step 7: Write the carve-out header into `PRD-005`**

A block at the top of `docs/prd/pending/PRD-005-multi-tenant-cloud.md`
mapping every FR and AC to *moved to PRD-005a* or *retained*, plus the four
superseded technical-approach lines from design Decision 14 (SQLite, bundled
ACME, the FR2 `actor_kind` caveat, the token/role tools). Update its
`Status:` line to name the remainder.

- [ ] **Step 8: Update `docs/roadmap.md`**

Add a `005a` row to the v4 index (status `completed`, depends on 011 · 008);
reword the `005` row to the remainder; update the chain table's step-2 row to
point at PRD-005a; note that step 3 (PRD-007) inherits `PUBLIC_PATHS`, the
`PREFIX` seam and design Decision 2's verdict.

- [ ] **Step 9: Verify every acceptance criterion with cited output**

Walk AC1–AC11 of PRD-005a in order. Each gets the command and its real
output pasted into the changelog. AC3, AC8 and AC11 additionally need the
browser session and the `docker compose` run from slices 3, 6 and 4.

- [ ] **Step 10: Write `docs/changelog/NNNN-hosted-core.md`**

Next zero-padded sequence, from the actual diff, following
`docs/changelog/README.md`. Include the AC evidence from step 9 and the final
`make test` count.

- [ ] **Step 11: Move the PRD and verify the suite**

```bash
git mv docs/prd/in-progress/PRD-005a-hosted-core.md docs/prd/completed/
make test          # cite the count
make test-portability
```

- [ ] **Step 12: Commit**

```bash
git add -A
git commit -m "PRD-005a completed: hosted core — deploy, identity, public read"
```

---

## Self-review notes (checked against the design spec)

- **Spec coverage.** Decisions 1 and 2 land as documentation obligations
  (slices 3, 6, 8) plus the kernel-silence test (slices 2, 7); Decision 3 →
  slices 1 and 5; Decision 4 → slices 1, 3, 4; Decision 5 → the absence of an
  ACL, pinned by slice 3's admin-vs-member test; Decision 6 → slice 2;
  Decision 7 → slices 2 and 6; Decision 8 → slices 2, 5, 7; Decision 9 →
  slice 5; Decision 10 → slice 1; Decision 11 → slice 6; Decision 12 →
  slices 2 and 8; Decision 13 → slice 8; Decision 14 → the PRD carve-out
  header in slice 8.
- **Interface consistency.** `AuthStore` method names are used identically in
  slices 2, 3 and 4; `security.current_config()` / `current_principal()` are
  defined in slice 2 and consumed in 3, 4, 5 and 7; `appmode.check_bind` is
  defined in slice 1 and consumed in slice 5; `PREFIX` is defined in slice 6
  and consumed by PRD-007, not by this plan.
- **Known gap, deliberate.** `EXPECTED_PUBLIC` is written in full in slice 2
  alongside a `NOT_YET_BUILT` subtrahend of seven, which slice 3 shrinks by
  three and slice 7 empties. Writing the final list exactly once is what stops
  the enumeration drifting slice by slice, and the set equality (rather than a
  subset check) is what stops a forgotten removal from passing silently.
