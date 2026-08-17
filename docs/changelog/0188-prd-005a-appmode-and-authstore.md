# 0188 — PRD-005a slice 1: app modes, state paths and the identity store

- **Commit:** pending
- **Date:** 2026-08-17
- **Author:** Claude (with Nikita Fedorov)

## Summary

First slice of [PRD-005a](../prd/in-progress/PRD-005a-hosted-core.md) (hosted
core / "005-lite"), per
[the plan](../superpowers/plans/2026-08-17-hosted-core.md) slice 1: two new
`agentcad/core/` modules that no server imports yet. `appmode.py` resolves the
explicit `local`/`hosted` mode, derives the identity state directory, and owns
the bind interlock; `authstore.py` is the four-document identity store —
accounts, enrolments, browser sessions and agent bearer tokens — written
atomically and serialised in-process *and* across processes. Zero new runtime
dependencies (`hashlib.scrypt`, `secrets`, `hmac`, `fcntl`, `json`,
`threading` are all stdlib), zero geometry imports, no core file edited.

## Changes

- **`agentcad/core/appmode.py` (new).**
  - `resolve_mode(env=None) -> AppMode` reads `AGENTCAD_MODE` (default
    `local`) and **refuses an unrecognised value rather than defaulting** —
    a derived or defaulted mode fails *open* on a typo, which is the one
    failure direction design Decision 3 exists to make impossible.
  - `hosted` requires `AGENTCAD_PUBLIC_ORIGIN` (absolute `http(s)` origin, no
    path; a trailing `/` is stripped) and a session secret from
    `AGENTCAD_SECRET_KEY` (≥32 chars) or a generated `secret.key` persisted
    through `os.open(..., O_CREAT|O_EXCL, 0o600)`. Every refusal names the
    setting at fault and **never echoes secret material**.
  - A pre-existing `secret.key` whose mode has group/other bits is **refused,
    not repaired**: a permission silently widened back is a promise we cannot
    keep about who read it in between.
  - `AppMode` is a frozen dataclass with `hosted`, `origin_host` (port
    stripped, IPv6 brackets kept) and `secure_cookies` (https only — a
    `Secure` cookie on a plain-http staging origin is never sent back, which
    reads to an operator as "login silently does nothing").
  - `state_dir()` = `$AGENTCAD_STATE_DIR` else `config.config_path().parent /
    "state"`, the `AGENTCAD_PACKAGES_DIR` / `AGENTCAD_INDEXES_DIR` derivation
    (FR25) — so every test that sets `AGENTCAD_CONFIG` gets an isolated
    identity store for free, and no `--projects-dir` setting can move it.
  - `check_bind(mode, host)` is the interlock: a non-loopback bind in `local`
    mode raises `ModeError` naming `AGENTCAD_MODE=hosted`.
- **`agentcad/core/authstore.py` (new).** `AuthStore(root)` over
  `users.json` / `enrolments.json` / `sessions.json` / `tokens.json`.
  - **Accounts** are created *disabled* by `add_user`, which returns a
    single-use enrolment token; a duplicate handle is a `ConflictError` rather
    than a silent password reset. `mint_enrolment` re-mints and drops every
    other outstanding link for that handle. Handles are
    `[a-z0-9][a-z0-9._-]{0,31}` — the ceiling is arithmetic, not taste
    (`user:` + handle + `/browser:xxxxxxxx` ≤ `locks.MAX_CLIENT_ID_CHARS`).
  - **Passwords** are `hashlib.scrypt` (n=2^15, r=8, p=1, 16-byte salt,
    parameters stored beside the digest so they can be raised later),
    compared with `hmac.compare_digest`. An unknown *or disabled* handle is
    charged a full dummy scrypt against a fixed salt, so timing does not
    separate "no such account" from "wrong password".
  - **Sessions** are `secrets.token_urlsafe(32)` stored only as a SHA-256
    digest; sliding TTL 14 days, absolute cap 30 days, `last_seen` rewritten
    at most once a day so a busy session does not cost an flock + write per
    request. `resolve_session` reads the **user row live**, so disabling an
    account or changing a role takes effect on the next request; `revoke_*`
    deletes rows, so revocation is immediate (this is why they are not JWTs).
    Provably-dead rows are pruned on write.
  - **Tokens** are `acad_<id8>_<secret43>`, returned once, stored as SHA-256
    digests, optionally expiring, revocable. Deliberately *not* scrypt: with
    256 bits of entropy there is nothing to brute-force, and scrypt would put
    tens of milliseconds on every agent request. `resolve_token` never raises
    (it is reached by an anonymous request carrying an attacker's header) and
    compares even on an unknown id so id-existence is not measurable.
  - **Concurrency** is `_scope()`: a registry-keyed `threading.RLock` plus
    `fcntl.flock` on a `.lock` file **beside** the documents, the
    `LocalIndex._index_scope` precedent (`core/packages/indexes.py`) — because
    `agentcad admin ...` through `docker compose exec` is routinely a second
    writer. A depth counter keeps a nested scope from taking a second `flock`
    (which, being per open file description, would block against its own
    outer one for ever). `fcntl` is imported gracefully so the module stays
    importable on Windows, with the degradation documented rather than hidden.
  - **Writes** stage through `<name>.<random>.tmp` + `os.replace` at mode
    0600, the changelog-0181 idiom: a *fixed* `.tmp` lets two writers
    interleave into one staging file and each replace the mixture into place,
    which is corruption, not a lost update. Reads reuse a parse cached by
    `(st_mtime_ns, st_size, st_ino)`, which is what makes a second process's
    write visible with no restart; every read-modify-write re-reads with
    `fresh=True` so a stale parse can never drop the other writer's row.
  - A corrupt document raises rather than reading as empty — "the file is
    garbage" must not look like "there are no accounts", which the next
    `admin user add` would cheerfully repopulate over the top of.

## Files

- `agentcad/core/appmode.py` — new; modes, state dir, bind interlock
- `agentcad/core/authstore.py` — new; the four identity documents
- `tests/test_appmode.py` — new; 26 tests
- `tests/test_authstore.py` — new; 56 tests

## Notes

- **Measurements, not estimates.** scrypt at n=2^15/r=8/p=1 costs **62.8 ms**
  per hash here (`hashlib.scrypt` ×5, Apple M-series, 2026-08-17); the design
  spec says "~100 ms" and the code comment now carries the measured number.
  The configuration is **below** OWASP's scrypt minimum (n=2^17), and the
  module says so out loud with the reasons: registration is closed and an
  account is already arbitrary code execution on the host (design Decision 1),
  so the password is not the weakest link; login is rate limited per handle
  and per address, which is what NIST SP 800-63B relies on against online
  guessing; n=2^17 is 4× the memory on a documented 2 vCPU / 4 GB floor; and
  the parameters live beside every digest, so raising them re-hashes on next
  login instead of invalidating accounts. `MIN_PASSWORD_CHARS = 8` cites NIST
  SP 800-63B §5.1.1.2; the 1024-character cap exists only so scrypt is never
  handed a megabyte from a 200-byte request body.
- **Every property is tested by its negation**, and the negations were
  *verified* by breaking the implementation on purpose and watching the right
  tests go red — not asserted. Disabling `flock` failed
  `test_concurrent_processes_lose_no_writes` (4 processes × 10 accounts, 40
  expected, writes actually lost); removing the dummy scrypt failed both
  timing tests; accepting a session past its sliding window and honouring a
  revoked token each failed their own test. Two more pin the *mechanism*
  rather than the answer, by monkeypatching `hmac.compare_digest` and
  asserting it was called — an `==` on a digest passes an
  is-the-password-right test and is still a byte-at-a-time oracle.
- The plan's `test_secrets_are_not_stored_raw` uses `bearer.split("_")[2]`,
  which only ever checked a **prefix**: `token_urlsafe`'s alphabet contains
  `_`. The plan's test is kept verbatim and a second test forces an
  underscore-bearing secret (via the `_mint_secret` seam) to check the whole
  string is absent *and* that resolution still works — which a naive
  `split("_")` would break. Every split in the module is `split("_", 2)`.
- The session secret is required hosted configuration but nothing derives from
  it yet: 005-lite's sessions are opaque store-backed rows, not signed
  cookies. Recorded in `_resolve_secret`'s docstring so the next reader does
  not go looking for the signature.
- Divergence from the plan, deliberate: `fcntl` is imported through a
  `try/except ImportError` (the `indexes.py` house pattern) rather than at
  module top unconditionally, so `authstore` and its test module stay
  importable on Windows where the portability job runs. The cross-process test
  is marked `portability` and skipped without `fcntl`.
- Test counts are higher than the plan's projection (26 + 56 = 82 against
  "5 passed" + "9 passed") because the plan's blocks are the seed and the
  quality bar asked for the attacking test behind every property.
- Verification: `.venv/bin/python -m pytest tests/test_appmode.py
  tests/test_authstore.py -q` → **82 passed in 3.03 s**. Prior-tree baseline,
  measured on this branch at `e8645f4` before any change in this slice:
  `make test` → **3316 passed, 1 skipped in 528.05 s** (8 workers, this
  machine). The 0187 entry's 3310/7 was a different tree.
- Sequence numbering: 0185 is used twice in the tree (`0185-prd-005a-design`
  on this branch and `0185-test-suite-parallel-speedup` from the #16 merge,
  which also brought 0186 and 0187), so the next free number is 0188.
