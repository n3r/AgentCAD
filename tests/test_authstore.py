"""PRD-005a slice 1: the identity store.

Four atomically-written JSON documents under ``<state-dir>/auth/``, serialised
in-process **and** across processes. No kernel, no server, no geometry.

Every property here is tested by its negation as well as its statement — an
expired session ACCEPTED, a revoked token honoured, a disabled account still
signing in, two concurrent writers losing one. Those are the failures a
reviewer attacks, so they are the assertions that exist.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time

import pytest

from agentcad.core import authstore as authstore_mod
from agentcad.core.authstore import (
    ABSOLUTE_SESSION_S,
    SLIDING_SESSION_S,
    AuthStore,
)
from agentcad.core.model import NotFoundError, ValidationError

HAS_FLOCK = authstore_mod.fcntl is not None


@pytest.fixture
def store(tmp_path):
    return AuthStore(tmp_path / "auth")


# ------------------------------------------------------- users & enrolment


def test_enrolment_is_single_use(store):
    token = store.add_user("nikita", role="admin")
    assert store.enrol(token, "correct horse battery") == "nikita"
    with pytest.raises(Exception):
        store.enrol(token, "second try")


def test_enrolment_revokes_every_existing_session_for_that_handle(store):
    """Review finding M4: a password reset signs the handle out everywhere.

    `agentcad admin enrol <handle>` re-mints a link for an account that
    already exists, and the reason an operator runs it is that the password
    was lost **or stolen**. Leaving the old sessions live let an attacker's
    cookie outlive the recovery by up to `ABSOLUTE_SESSION_S` (30 days), which
    is the opposite of what the path is for — and the opposite of what anyone
    expects a password reset to do.
    """
    store.enrol(store.add_user("anya"), "hunter2hunter2")
    store.enrol(store.add_user("nikita"), "correct horse battery")
    stolen = store.create_session("anya", device=None)
    other = store.create_session("nikita", device=None)
    assert store.resolve_session(stolen) is not None

    store.enrol(store.mint_enrolment("anya"), "a brand new password")

    assert store.resolve_session(stolen) is None
    # Only that handle's: a reset must not sign the whole instance out.
    assert store.resolve_session(other) is not None


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


def test_a_duplicate_handle_is_refused_rather_than_reset(store):
    """Otherwise `admin user add nikita` on an existing account is a silent
    password reset by anyone who can run the CLI twice."""
    store.enrol(store.add_user("nikita"), "correct horse battery")
    with pytest.raises(Exception):
        store.add_user("nikita")
    assert store.verify_password("nikita", "correct horse battery") is True


def test_an_unenrolled_account_cannot_sign_in(store):
    """add_user creates a DISABLED account; the enrolment is what enables it."""
    store.add_user("nikita")
    assert store.verify_password("nikita", "") is False
    assert store.list_users()[0]["disabled"] is True


def test_a_disabled_account_is_refused_even_with_the_right_password(store):
    store.enrol(store.add_user("anya"), "hunter2hunter2")
    assert store.verify_password("anya", "hunter2hunter2") is True
    store.disable_user("anya")
    assert store.verify_password("anya", "hunter2hunter2") is False


def test_disabling_an_unknown_handle_is_a_not_found(store):
    with pytest.raises(NotFoundError):
        store.disable_user("ghost")


def test_an_expired_enrolment_token_is_refused(store, monkeypatch):
    token = store.add_user("late")
    monkeypatch.setattr(authstore_mod, "_now",
                        lambda: time.time() + authstore_mod.ENROL_TTL_S + 60)
    with pytest.raises(NotFoundError):
        store.enrol(token, "correct horse battery")


def test_an_unknown_enrolment_token_is_a_not_found(store):
    with pytest.raises(NotFoundError):
        store.enrol("not-a-token", "correct horse battery")


def test_a_too_short_password_is_refused_after_the_token_is_checked(store):
    """Order matters: a bad token must 404 before the password is judged, or
    the response tells a stranger that their guessed token was real."""
    with pytest.raises(NotFoundError):
        store.enrol("not-a-token", "x")
    token = store.add_user("anya")
    with pytest.raises(ValidationError):
        store.enrol(token, "short")
    assert store.enrol(token, "long enough now") == "anya"   # still unused


def test_an_absurd_password_is_refused_rather_than_hashed(store):
    """scrypt on a 10 MB password is a CPU-DoS with a 200-byte request body.
    NIST SP 800-63B asks for at least 64 accepted characters; the cap is well
    above that and well below "an attack"."""
    token = store.add_user("anya")
    with pytest.raises(ValidationError):
        store.enrol(token, "x" * 10_000_000)


def test_an_unknown_role_is_refused(store):
    with pytest.raises(ValidationError):
        store.add_user("nikita", role="superuser")


# ------------------------------------------------------------------ sessions


def test_session_resolve_and_revoke(store):
    token = store.add_user("nikita", role="admin")
    store.enrol(token, "correct horse battery")
    secret = store.create_session("nikita", device="browser:7f3a1b2c")
    assert store.resolve_session(secret) == {
        "handle": "nikita", "role": "admin", "device": "browser:7f3a1b2c"}
    store.revoke_session(secret)
    assert store.resolve_session(secret) is None
    assert store.resolve_session("not-a-session") is None


def test_a_session_past_the_sliding_window_is_refused(store, monkeypatch):
    """The negation: an idle session ACCEPTED after its window is the bug."""
    store.enrol(store.add_user("nikita"), "correct horse battery")
    secret = store.create_session("nikita", device=None)
    monkeypatch.setattr(authstore_mod, "_now",
                        lambda: time.time() + SLIDING_SESSION_S + 60)
    assert store.resolve_session(secret) is None


def test_a_busy_session_slides_but_never_past_the_absolute_cap(store, monkeypatch):
    store.enrol(store.add_user("nikita"), "correct horse battery")
    secret = store.create_session("nikita", device=None)
    born = time.time()

    # Used every 10 days: the sliding window never lapses...
    for day in (10, 20, 29):
        monkeypatch.setattr(authstore_mod, "_now", lambda d=day: born + d * 86400)
        assert store.resolve_session(secret) is not None, f"day {day}"

    # ...and the absolute cap still ends it.
    monkeypatch.setattr(authstore_mod, "_now",
                        lambda: born + ABSOLUTE_SESSION_S + 60)
    assert store.resolve_session(secret) is None


def test_last_seen_is_rewritten_at_most_once_a_day(store, monkeypatch):
    """A busy session must not rewrite the whole document on every request:
    that is a fsync and an flock per API call."""
    store.enrol(store.add_user("nikita"), "correct horse battery")
    secret = store.create_session("nikita", device=None)
    path = store.root / "sessions.json"
    born = time.time()

    monkeypatch.setattr(authstore_mod, "_now", lambda: born + 60)
    before = path.stat().st_mtime_ns
    for _ in range(20):
        assert store.resolve_session(secret) is not None
    assert path.stat().st_mtime_ns == before, "a warm session rewrote the store"

    monkeypatch.setattr(authstore_mod, "_now", lambda: born + 86400 + 60)
    assert store.resolve_session(secret) is not None
    assert path.stat().st_mtime_ns != before, "an old session never slid"


def test_disabling_an_account_kills_its_live_sessions(store):
    """Resolution reads the user row live, so `admin user disable` is
    immediate rather than "on their next login"."""
    store.enrol(store.add_user("anya"), "hunter2hunter2")
    secret = store.create_session("anya", device=None)
    assert store.resolve_session(secret) is not None
    store.disable_user("anya")
    assert store.resolve_session(secret) is None


def test_a_session_carries_the_current_role_not_a_frozen_one(store):
    store.enrol(store.add_user("anya", role="member"), "hunter2hunter2")
    secret = store.create_session("anya", device=None)
    assert store.resolve_session(secret)["role"] == "member"
    store.set_role("anya", "admin")
    assert store.resolve_session(secret)["role"] == "admin"


def test_a_session_for_an_unknown_handle_is_refused(store):
    with pytest.raises(NotFoundError):
        store.create_session("ghost", device=None)


def test_session_secrets_are_stored_as_digests_only(store):
    store.enrol(store.add_user("nikita"), "correct horse battery")
    secret = store.create_session("nikita", device=None)
    blob = (store.root / "sessions.json").read_text()
    assert secret not in blob
    assert "nikita" in blob                      # the row itself is there


def test_two_sessions_are_independent(store):
    """Logging out of one browser must not sign the other one out."""
    store.enrol(store.add_user("nikita"), "correct horse battery")
    a = store.create_session("nikita", device="browser:aaaaaaaa")
    b = store.create_session("nikita", device="browser:bbbbbbbb")
    assert a != b
    store.revoke_session(a)
    assert store.resolve_session(a) is None
    assert store.resolve_session(b)["device"] == "browser:bbbbbbbb"


def test_revoking_an_unknown_session_is_silent(store):
    store.revoke_session("not-a-session")       # logout must never 500


# -------------------------------------------------------------------- tokens


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


def test_a_token_with_no_ttl_does_not_expire(store, monkeypatch):
    bearer = store.add_token("forever")
    monkeypatch.setattr(authstore_mod, "_now", lambda: time.time() + 3650 * 86400)
    assert store.resolve_token(bearer)["name"] == "forever"


def test_secrets_are_not_stored_raw(store, tmp_path):
    """`split("_", 2)`, and the maxsplit is the whole point.

    The plan's `bearer.split("_")[2]` checked only the secret's first
    underscore-free *fragment*, because `token_urlsafe`'s alphabet contains
    `_` — and roughly once in sixty-four that fragment is a single character,
    at which point the assertion is "does the letter `0` appear in a
    64-hex-digit digest", which it does. That is not a weaker test, it is a
    **randomly red** one: it took the full suite down at
    `assert '0' not in '{...}'` on the third consecutive run of this branch.
    With the maxsplit, `[2]` is the entire secret, underscores and all —
    which is also the module's own idiom (every split in `authstore` is
    `split("_", 2)`).
    """
    bearer = store.add_token("ci")
    blob = (tmp_path / "auth" / "tokens.json").read_text()
    secret = bearer.split("_", 2)[2]
    assert len(secret) >= 40, secret          # the whole thing, not a fragment
    assert secret not in blob


def test_the_whole_token_secret_is_absent_even_when_it_contains_underscores(
        store, monkeypatch):
    """`token_urlsafe`'s alphabet includes `_`, so the plan's
    `bearer.split("_")[2]` only ever checked a PREFIX. Force the awkward case
    and check the whole secret — and that resolution still works, which is
    what a naive `split("_")` would break."""
    monkeypatch.setattr(authstore_mod, "_mint_secret",
                        lambda: "aa_bb_cc" + "z" * 35)
    bearer = store.add_token("ci")
    assert bearer.count("_") == 4
    assert "aa_bb_cc" + "z" * 35 not in (store.root / "tokens.json").read_text()
    assert store.resolve_token(bearer) == {"name": "ci", "role": "member"}


def test_a_valid_id_with_a_wrong_secret_is_refused(store):
    """The negation of "the digest is compared": an attacker who reads
    tokens.json learns the id, and the id alone must be worth nothing."""
    bearer = store.add_token("ci")
    token_id = store.list_tokens()[0]["id"]
    assert store.resolve_token(f"acad_{token_id}_" + "x" * 43) is None
    assert store.resolve_token(bearer) is not None


@pytest.mark.parametrize("junk", [
    "", "acad_", "acad_x", "bearer_x_y", "acad__" + "x" * 43,
    "acad_" + "x" * 4000, "acad_\x00_x", "not a token at all",
])
def test_malformed_bearers_resolve_to_none_rather_than_raising(store, junk):
    """resolve_token is reached by an anonymous request with an attacker's
    header; an exception here is a 500 that says the header was interesting."""
    assert store.resolve_token(junk) is None


def test_list_tokens_never_carries_a_digest_or_a_secret(store):
    store.add_token("ci")
    listed = store.list_tokens()
    assert set(listed[0]) == {"id", "name", "role", "created", "expires", "revoked"}


def test_revoking_a_token_twice_is_silent_and_unknown_ids_are_not_found(store):
    store.add_token("ci")
    token_id = store.list_tokens()[0]["id"]
    store.revoke_token(token_id)
    store.revoke_token(token_id)
    with pytest.raises(NotFoundError):
        store.revoke_token("deadbeef")


def test_two_tokens_of_the_same_name_are_distinct_credentials(store):
    """Names are labels, not keys — revoking one must not revoke the other."""
    first, second = store.add_token("ci"), store.add_token("ci")
    ids = [t["id"] for t in store.list_tokens()]
    assert len(set(ids)) == 2
    store.revoke_token(ids[0])
    assert (store.resolve_token(first) is None) != (store.resolve_token(second) is None)


# ------------------------------------------------ hashing and constant time


def test_the_password_digest_is_scrypt_with_recorded_parameters(store):
    """Parameters are stored beside the digest so they can be raised later
    without invalidating every account."""
    store.enrol(store.add_user("nikita"), "correct horse battery")
    row = json.loads((store.root / "users.json").read_text())["nikita"]["password"]
    assert row["kdf"] == "scrypt"
    assert (row["n"], row["r"], row["p"]) == (32768, 8, 1)
    assert len(bytes.fromhex(row["salt"])) == 16
    assert len(bytes.fromhex(row["digest"])) == 32


def test_the_password_is_not_recoverable_from_the_document(store):
    store.enrol(store.add_user("nikita"), "correct horse battery")
    blob = (store.root / "users.json").read_text()
    assert "correct horse battery" not in blob


def test_an_unknown_handle_costs_the_same_as_a_wrong_password(store):
    """A cheap unknown-handle path is a user-enumeration oracle: the attacker
    learns which handles exist by timing alone. The dummy scrypt is what
    closes it, and this is the test that fails if somebody removes it.

    `min` of several samples rather than a mean: the minimum is the run that
    was least interrupted by the scheduler, which is the stable statistic on
    an 8-way-parallel test host.
    """
    store.enrol(store.add_user("nikita"), "correct horse battery")

    def best(handle: str) -> float:
        samples = []
        for _ in range(5):
            start = time.perf_counter()
            assert store.verify_password(handle, "wrong password entirely") is False
            samples.append(time.perf_counter() - start)
        return min(samples)

    known, unknown = best("nikita"), best("ghost")
    assert 0.4 < unknown / known < 2.5, (known, unknown)


def test_a_disabled_account_also_costs_a_full_scrypt(store):
    """Otherwise "disabled" is distinguishable from "never existed"."""
    store.enrol(store.add_user("anya"), "hunter2hunter2")
    store.disable_user("anya")
    start = time.perf_counter()
    assert store.verify_password("anya", "hunter2hunter2") is False
    assert time.perf_counter() - start > 0.005


def test_verification_uses_a_constant_time_comparison(monkeypatch, store):
    """Pin the mechanism, not just the answer: a `==` on the digest is a
    byte-at-a-time oracle against an offline attacker who can time us."""
    store.enrol(store.add_user("nikita"), "correct horse battery")
    calls = []
    real = authstore_mod.hmac.compare_digest
    monkeypatch.setattr(authstore_mod.hmac, "compare_digest",
                        lambda a, b: calls.append(1) or real(a, b))
    assert store.verify_password("nikita", "correct horse battery") is True
    assert calls, "verify_password did not use hmac.compare_digest"


def test_token_resolution_uses_a_constant_time_comparison(monkeypatch, store):
    bearer = store.add_token("ci")
    calls = []
    real = authstore_mod.hmac.compare_digest
    monkeypatch.setattr(authstore_mod.hmac, "compare_digest",
                        lambda a, b: calls.append(1) or real(a, b))
    assert store.resolve_token(bearer) is not None
    assert calls, "resolve_token did not use hmac.compare_digest"


def test_secrets_have_at_least_256_bits_of_entropy(store):
    """Why tokens are sha256 and not scrypt: there is nothing to brute-force.
    The claim is only true while the secret really is this long."""
    store.enrol(store.add_user("nikita"), "correct horse battery")
    session = store.create_session("nikita", device=None)
    bearer = store.add_token("ci").split("_", 2)[2]
    for secret in (session, bearer):
        assert len(secret) >= 43           # token_urlsafe(32) -> 43 chars


# ------------------------------------------------------- files and locking


def test_the_store_directory_and_its_documents_are_not_world_readable(store):
    store.enrol(store.add_user("nikita"), "correct horse battery")
    store.add_token("ci")
    assert not (store.root.stat().st_mode & 0o077)
    for name in ("users.json", "enrolments.json", "tokens.json"):
        mode = (store.root / name).stat().st_mode
        assert not (mode & 0o077), name


def test_a_write_never_leaves_a_fixed_staging_name_behind(store):
    """Changelog 0181's corruption class: a fixed `<name>.tmp` let two
    writers interleave into ONE staging file and each replace the mixture into
    place. The staging name carries randomness, and nothing is left over."""
    store.add_user("nikita")
    store.add_user("anya")
    leftovers = [p.name for p in store.root.iterdir() if p.name.endswith(".tmp")]
    assert leftovers == []


def test_a_corrupt_document_is_refused_rather_than_silently_emptied(store):
    """An empty read on a parse error would make "the file is garbage" look
    like "there are no accounts" — i.e. an unenrollable instance that a fresh
    `admin user add` would happily repopulate over the top."""
    store.add_user("nikita")
    (store.root / "users.json").write_text("{ not json")
    with pytest.raises(Exception):
        store.list_users()


def test_a_second_process_writing_is_seen_without_restart(store, tmp_path):
    subprocess.run(
        [sys.executable, "-c",
         "from agentcad.core.authstore import AuthStore;"
         f"AuthStore({str(tmp_path / 'auth')!r}).add_user('second')"],
        check=True)
    store.add_user("first")
    assert {u["handle"] for u in store.list_users()} == {"first", "second"}


def test_concurrent_threads_lose_no_writes(store):
    """The in-process half of `_scope`. Without the threading lock the
    read-modify-write windows overlap and the last writer wins."""
    errors: list[BaseException] = []

    def add(base: int) -> None:
        try:
            for i in range(10):
                store.add_user(f"u{base}{i}")
        except BaseException as exc:            # noqa: BLE001 — reported below
            errors.append(exc)

    threads = [threading.Thread(target=add, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert len(store.list_users()) == 80


@pytest.mark.portability
@pytest.mark.skipif(not HAS_FLOCK, reason="fcntl.flock is POSIX-only")
def test_concurrent_processes_lose_no_writes(tmp_path):
    """The cross-process half, which is the one `docker compose exec` needs:
    the admin CLI is routinely a second writer while the server holds the same
    document. Without flock these interleave and writes vanish.
    """
    root = tmp_path / "auth"
    AuthStore(root)                       # create the directory up front
    script = (
        "import sys;"
        "from agentcad.core.authstore import AuthStore;"
        "s = AuthStore(sys.argv[1]);"
        "[s.add_user('p%ss%s' % (sys.argv[2], i)) for i in range(10)]"
    )
    procs = [subprocess.Popen([sys.executable, "-c", script, str(root), str(n)])
             for n in range(4)]
    assert [p.wait(timeout=120) for p in procs] == [0, 0, 0, 0]
    assert len(AuthStore(root).list_users()) == 40


def test_the_lock_file_is_never_one_of_the_documents(store):
    """`_index_scope`'s rule: the lock lives beside the data, never inside it,
    so a document is never the thing being flocked."""
    store.add_user("nikita")
    names = {p.name for p in store.root.iterdir()}
    assert ".lock" in names
    assert authstore_mod.DOCUMENTS.isdisjoint({".lock"})


def test_the_module_imports_no_geometry():
    """Server-side identity code is OCP-free by construction, not by care."""
    assert "OCP" not in sys.modules or True     # the probe below is the real test
    out = subprocess.run(
        [sys.executable, "-c",
         "import sys\n"
         "class Block:\n"
         "    def find_module(self, name, path=None):\n"
         "        if name.split('.')[0] in {'OCP', 'build123d'}:\n"
         "            raise ImportError(name)\n"
         "sys.meta_path.insert(0, Block())\n"
         "import agentcad.core.authstore, agentcad.core.appmode\n"
         "print('ok')"],
        capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "ok"


def test_the_store_survives_a_state_dir_that_already_exists(tmp_path):
    (tmp_path / "auth").mkdir(parents=True)
    os.chmod(tmp_path / "auth", 0o700)
    store = AuthStore(tmp_path / "auth")
    store.add_user("nikita")
    assert [u["handle"] for u in store.list_users()] == ["nikita"]
