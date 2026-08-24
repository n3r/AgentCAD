"""PRD-005 slice 5: the audit log (FR12).

The store, the digest, the taps and the two properties nobody can afford to
get wrong: **secrets never enter a row**, and **`cp` is not a backup**. The
last one is the PRD-005 spike's measured finding (§C) reproduced as a
regression test, because the day somebody "simplifies" `vacuum_into` to a file
copy is the day a restored audit log is empty and says so to nobody.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import threading

import pytest

from agentcad.core import audit
from agentcad.core.audit import AuditLog, args_digest, canonical_args
from agentcad.core.model import ValidationError


@pytest.fixture
def log(tmp_path):
    return AuditLog(tmp_path / "state")


ROW = {"principal": "user:nikita", "action": "set_part_params",
       "project": "widget", "outcome": "ok"}


# ------------------------------------------------------------------- store


def test_a_row_lands_and_comes_back(log):
    log.append("acme", ROW)
    rows = log.query("acme")
    assert len(rows) == 1
    assert rows[0]["principal"] == "user:nikita"
    assert rows[0]["action"] == "set_part_params"
    assert rows[0]["project"] == "widget"
    assert rows[0]["outcome"] == "ok"
    assert rows[0]["ts"] > 0


def test_each_org_gets_its_own_database(log):
    log.append("acme", ROW)
    log.append("initech", {**ROW, "principal": "user:anya"})
    assert [r["principal"] for r in log.query("acme")] == ["user:nikita"]
    assert [r["principal"] for r in log.query("initech")] == ["user:anya"]
    assert log.orgs() == ["acme", "initech"]
    assert log.path_for("acme").name == "acme.db"


def test_local_mode_writes_nothing(log, tmp_path):
    """No tenant means no org, and no org means no disk touched at all —
    the FR4/AC7 rule the whole feature is built on."""
    assert log.append(None, ROW) is None
    assert log.append("", ROW) is None
    assert log.orgs() == []


def test_the_pragmas_are_the_ones_the_spike_measured(log):
    log.append("acme", ROW)
    conn = log._connection("acme")                       # noqa: SLF001
    assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 30000
    # 1 == NORMAL
    assert conn.execute("PRAGMA synchronous").fetchone()[0] == 1


def test_the_three_indexes_exist(log):
    """`(ts)`, `(principal, ts)`, `(project, ts)` are what buy the 1.4 ms the
    spike measured against JSONL's 177 ms. Without them this store is slower
    than the JSONL it beat."""
    log.append("acme", ROW)
    names = {row[0] for row in log._connection("acme").execute(   # noqa: SLF001
        "SELECT name FROM sqlite_master WHERE type='index'").fetchall()}
    assert {"audit_ts", "audit_principal_ts", "audit_project_ts"} <= names


def test_an_org_name_cannot_escape_the_audit_directory(log):
    """`<state>/audit/<org>.db` composes a caller string into a path."""
    # `""` is not here: a falsy org is local mode and returns before any of
    # this (`test_local_mode_writes_nothing`).
    for bad in ("../../etc/passwd", "acme/../../x", "/tmp/evil", "Acme",
                "a.b", "_secret", "acme.db"):
        with pytest.raises(ValidationError):
            log.append(bad, ROW)


def test_the_instance_org_is_allowed_and_no_real_org_can_take_it(log):
    from agentcad.core.model import ID_RE

    log.append(audit.INSTANCE_ORG, {**ROW, "action": "login"})
    assert log.query(audit.INSTANCE_ORG)[0]["action"] == "login"
    assert not ID_RE.match(audit.INSTANCE_ORG)


def test_a_row_without_an_action_is_refused(log):
    with pytest.raises(ValidationError):
        log.append("acme", {"principal": "user:nikita"})


def test_long_values_are_clamped_not_refused(log):
    """An audit row is never the reason a write fails."""
    log.append("acme", {**ROW, "principal": "user:" + "x" * 400,
                        "action": "a" * 200})
    row = log.query("acme")[0]
    assert len(row["principal"]) == audit.MAX_PRINCIPAL_CHARS
    assert len(row["action"]) == audit.MAX_ACTION_CHARS


def test_the_kind_is_computed_on_read_never_stored(log):
    """`proposals.actor_kind` is the one place this product decides what a
    person is; a stored copy could disagree with the identity it describes."""
    log.append("acme", {**ROW, "principal": "user:nikita/browser:7f3a1b2c"})
    log.append("acme", {**ROW, "principal": "agent:ci"})
    log.append("acme", {**ROW, "principal": "chat"})
    kinds = {r["principal"]: r["kind"] for r in log.query("acme")}
    assert kinds == {"user:nikita/browser:7f3a1b2c": "human",
                     "agent:ci": "agent", "chat": "agent"}
    columns = {row[1] for row in log._connection("acme").execute(  # noqa: SLF001
        "PRAGMA table_info(audit)").fetchall()}
    assert "kind" not in columns


# ------------------------------------------------------------------ digest


def test_secrets_never_reach_the_digest():
    """The value under a secret-shaped key is redacted BEFORE hashing, so two
    calls that differ only in their secret digest identically — which is the
    proof that the secret is not in the input."""
    a = args_digest({"name": "ci", "token": "acad_1_aaaaaaaa"})
    b = args_digest({"name": "ci", "token": "acad_1_bbbbbbbb"})
    assert a == b
    assert a != args_digest({"name": "ci"})


@pytest.mark.parametrize("key", [
    "token", "AGENTCAD_TOKEN", "password", "new_password", "secret",
    "client_secret", "api_key", "authorization", "cookie", "private_key",
    "passphrase", "credential",
])
def test_every_secret_shaped_key_is_redacted(key):
    assert canonical_args({key: "hunter2"})[key] == audit.REDACTED


def test_redaction_reaches_nested_structures():
    got = canonical_args({"body": {"users": [{"password": "hunter2"}]}})
    assert got["body"]["users"][0]["password"] == audit.REDACTED


def test_the_digest_is_stable_and_order_independent():
    assert args_digest({"a": 1, "b": 2}) == args_digest({"b": 2, "a": 1})
    assert args_digest(None) is None
    assert len(args_digest({})) == 64


def test_the_digest_is_total_over_its_input():
    """It is computed on the way to recording a call that already happened, so
    an unserialisable argument must not raise."""
    assert args_digest({"obj": object(), "deep": _nested(40)})


def _nested(depth: int):
    value = {"leaf": 1}
    for _ in range(depth):
        value = {"next": value}
    return value


# ------------------------------------------------------------------ query


def _seed(log):
    base = 1_000_000.0
    rows = [
        ("user:nikita/browser:aaaa", "set_part_params", "widget", "ok"),
        ("user:anya", "create_part", "widget", "ok"),
        ("agent:ci", "set_part_params", "bracket", "permission_error"),
        ("chat", "delete_part", "widget", "ok"),
    ]
    for index, (principal, action, project, outcome) in enumerate(rows):
        log.append("acme", {"ts": base + index, "principal": principal,
                            "action": action, "project": project,
                            "outcome": outcome})
    return base


def test_query_filters_compose(log):
    base = _seed(log)
    assert len(log.query("acme")) == 4
    assert len(log.query("acme", project="widget")) == 3
    assert len(log.query("acme", action="set_part_params")) == 2
    assert len(log.query("acme", project="widget",
                         action="set_part_params")) == 1
    assert len(log.query("acme", since=base + 2)) == 2
    assert len(log.query("acme", until=base + 2)) == 2
    assert len(log.query("acme", since=base + 1, until=base + 3)) == 2


def test_a_principal_filter_matches_every_device_that_person_used(log):
    _seed(log)
    assert len(log.query("acme", principal="user:nikita")) == 1
    assert len(log.query("acme", principal="user:anya")) == 1
    assert len(log.query("acme", principal="agent:ci")) == 1


def test_a_principal_filter_does_not_treat_underscore_as_a_wildcard(log):
    """LIKE reads `_` as "any character", and handles may contain one."""
    log.append("acme", {**ROW, "principal": "user:a_b/browser:x"})
    log.append("acme", {**ROW, "principal": "user:axb/browser:x"})
    assert len(log.query("acme", principal="user:a_b")) == 1


def test_rows_come_back_newest_first_and_paginate(log):
    base = _seed(log)
    rows = log.query("acme", limit=2)
    assert [r["ts"] for r in rows] == [base + 3, base + 2]
    assert [r["ts"] for r in log.query("acme", limit=2, offset=2)] == [
        base + 1, base]
    assert log.count("acme") == 4


def test_the_limit_is_clamped_not_unbounded(log):
    _seed(log)
    assert len(log.query("acme", limit=10 ** 9)) == 4      # no exception
    assert audit.MAX_LIMIT == 1000


# -------------------------------------------------------------- retention


def test_retention_prunes_only_what_is_older_than_the_window(tmp_path):
    log = AuditLog(tmp_path / "state")                     # retention off
    now = audit._now()                                     # noqa: SLF001
    log.append("acme", {**ROW, "ts": now - 30 * 86400})
    log.append("acme", {**ROW, "ts": now - 1 * 86400})
    log.retention_days = 7
    assert log.prune("acme") == 1
    assert len(log.query("acme")) == 1
    assert log.prune("acme") == 0                          # idempotent


def test_retention_is_off_by_default(tmp_path):
    log = AuditLog(tmp_path / "state")
    log.append("acme", {**ROW, "ts": audit._now() - 3650 * 86400})  # noqa: SLF001
    assert log.prune("acme") == 0
    assert len(log.query("acme")) == 1


def test_the_retention_env_knob_fails_towards_more_history(monkeypatch):
    monkeypatch.delenv(audit.RETENTION_ENV, raising=False)
    assert audit.retention_from_env() is None
    monkeypatch.setenv(audit.RETENTION_ENV, "nonsense")
    assert audit.retention_from_env() is None
    monkeypatch.setenv(audit.RETENTION_ENV, "0")
    assert audit.retention_from_env() is None
    monkeypatch.setenv(audit.RETENTION_ENV, "30")
    assert audit.retention_from_env() == 30.0


def test_append_prunes_opportunistically_at_most_once_an_interval(tmp_path):
    """There is no daemon to hang a timer on (this process may be a CLI that
    lives for 40 ms), so pruning rides an append — rate limited, or a busy
    instance would run a DELETE on every event."""
    seeder = AuditLog(tmp_path / "state")                  # retention off
    old = audit._now() - 10 * 86400                        # noqa: SLF001
    for _ in range(3):
        seeder.append("acme", {**ROW, "ts": old})

    log = AuditLog(tmp_path / "state", retention_days=1)
    calls = []
    real = log.prune
    log.prune = lambda org, days=None: (calls.append(org), real(org, days))[1]
    log.append("acme", ROW)                     # prunes the three old rows
    log.append("acme", ROW)                     # inside the interval: no prune
    assert calls == ["acme"]
    assert log.count("acme") == 2


# ------------------------------------------------------------ concurrency


def test_two_threads_appending_lose_nothing(log):
    """Each thread opens its OWN connection (Python's `check_same_thread` is
    kept as a guard rather than disabled), and WAL + busy_timeout is what makes
    that correct with no lock of our own."""
    errors: list[BaseException] = []

    def worker(name):
        try:
            for index in range(50):
                log.append("acme", {**ROW, "principal": name,
                                    "action": f"call_{index}"})
        except BaseException as exc:            # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(f"agent:t{i}",))
               for i in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert log.count("acme") == 100
    assert log.integrity("acme") == "ok"


def test_a_second_process_appends_into_the_same_database(log, tmp_path):
    """`docker compose exec agentcad agentcad admin …` while the server runs is
    a supported second writer — the case `busy_timeout` exists for. This holds
    an open connection in THIS process (as a running server would) while
    another process writes 50 rows."""
    log.append("acme", ROW)                     # our connection stays open
    code = (
        "from agentcad.core.audit import AuditLog;"
        f"log = AuditLog({str(tmp_path / 'state')!r});"
        "[log.append('acme', {'principal': 'agent:other', 'action': 'x',"
        " 'outcome': 'ok'}) for _ in range(50)]"
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True,
                          text=True, timeout=120)
    assert done.returncode == 0, done.stderr
    assert log.count("acme") == 51
    assert log.integrity("acme") == "ok"
    assert len(log.query("acme", principal="agent:other", limit=100)) == 50


# ---------------------------------------------------------------- backup


def test_cp_loses_rows_and_vacuum_into_recovers_them(log, tmp_path):
    """The spike's §C finding, as a regression test.

    A WAL database keeps recent commits in its `-wal` sidecar until a
    checkpoint; SQLite auto-checkpoints at 1000 pages and on the last
    connection close, and this test does neither (50 rows, connection held
    open). So the plain copy is missing rows — usually *every* row, with the
    table itself still unborn — and `VACUUM INTO` has all of them.
    """
    import shutil

    for index in range(50):
        log.append("acme", {**ROW, "action": f"call_{index}"})
    source = log.path_for("acme")

    naive = tmp_path / "naive.db"
    shutil.copy2(source, naive)
    conn = sqlite3.connect(str(naive))
    try:
        try:
            copied = conn.execute("SELECT COUNT(*) FROM audit").fetchone()[0]
        except sqlite3.DatabaseError:
            copied = 0                          # "no such table: audit"
    finally:
        conn.close()
    assert copied < 50, "cp of a WAL database is not a backup"

    good = log.vacuum_into("acme", tmp_path / "backup.db")
    conn = sqlite3.connect(str(good))
    try:
        assert conn.execute("SELECT COUNT(*) FROM audit").fetchone()[0] == 50
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        conn.close()


def test_a_backup_never_overwrites_an_existing_file(log, tmp_path):
    log.append("acme", ROW)
    dest = tmp_path / "backup.db"
    log.vacuum_into("acme", dest)
    with pytest.raises(ValidationError):
        log.vacuum_into("acme", dest)


def test_the_database_is_created_0600(log):
    log.append("acme", ROW)
    mode = os.stat(log.path_for("acme")).st_mode & 0o777
    assert mode == 0o600, oct(mode)


# ------------------------------------------------------------ time parsing


def test_parse_time_accepts_the_three_spellings():
    assert audit.parse_time(None) is None
    assert audit.parse_time("") is None
    assert audit.parse_time(1234.5) == 1234.5
    assert audit.parse_time("1234.5") == 1234.5
    assert audit.parse_time("2026-08-24") == pytest.approx(1787529600.0)
    assert audit.parse_time("2026-08-24T00:00:00Z") == pytest.approx(1787529600.0)
    now = audit._now()                                     # noqa: SLF001
    assert audit.parse_time("7d") == pytest.approx(now - 7 * 86400, abs=5)
    assert audit.parse_time("30m") == pytest.approx(now - 1800, abs=5)


def test_parse_time_refuses_junk_by_name():
    with pytest.raises(ValidationError) as excinfo:
        audit.parse_time("last tuesday", "since")
    assert "since" in str(excinfo.value.message)


# ------------------------------------------------------------- the tap


class _Registry:
    """A stand-in for `ToolRegistry.call` with the house error envelope."""

    def __init__(self):
        self.calls = []

    def call(self, name, args=None):
        self.calls.append((name, args))
        if name == "boom":
            raise RuntimeError("kaboom")
        if name == "refused":
            return {"error": {"type": "permission_error", "message": "no"}}
        return {"ok": True}


def _tapped(log, org="acme", principal="user:nikita"):
    registry = _Registry()
    call = audit.tap_registry(registry.call, log, org=lambda: org,
                              principal=lambda: principal)
    return registry, call


def test_the_tap_records_one_row_per_mutating_call(log):
    registry, call = _tapped(log)
    call("set_part_params", {"project": "widget", "part_id": "p1"})
    rows = log.query("acme")
    assert len(rows) == 1
    assert rows[0]["action"] == "set_part_params"
    assert rows[0]["project"] == "widget"
    assert rows[0]["outcome"] == "ok"
    assert rows[0]["args_digest"] == args_digest(
        {"project": "widget", "part_id": "p1"})
    assert registry.calls == [("set_part_params",
                               {"project": "widget", "part_id": "p1"})]


def test_the_tap_does_not_record_reads(log):
    _registry, call = _tapped(log)
    for name in ("get_part", "list_projects", "whoami", "sync_status",
                 "search_parts", "check_interference"):
        call(name, {"project": "widget"})
    assert log.query("acme") == []


def test_an_unclassified_tool_is_recorded(log):
    """The default leans towards recording: an over-recorded read is noise, an
    unrecorded write is the failure that matters."""
    _registry, call = _tapped(log)
    call("frobnicate", {"project": "widget"})
    assert len(log.query("acme")) == 1


def test_the_tap_records_the_refusal_type_as_the_outcome(log):
    _registry, call = _tapped(log)
    call("refused", {"project": "widget"})
    assert log.query("acme")[0]["outcome"] == "permission_error"


def test_the_tap_records_a_raise_and_re_raises_it(log):
    _registry, call = _tapped(log)
    with pytest.raises(RuntimeError):
        call("boom", {"project": "widget"})
    assert log.query("acme")[0]["outcome"] == "raised:RuntimeError"


def test_the_tap_returns_the_result_object_unchanged(log):
    registry = _Registry()
    sentinel = {"ok": True, "marker": object()}
    registry.call = lambda name, args=None: sentinel
    call = audit.tap_registry(registry.call, log, org=lambda: "acme")
    assert call("create_part", {}) is sentinel


def test_the_tap_is_idempotent(log):
    """The `_WRAPPED` sentinel: a registry rebuilt behind a re-installed
    wrapper must not record twice."""
    _registry, call = _tapped(log)
    again = audit.tap_registry(call, log)
    assert again is call
    call("create_part", {"project": "widget"})
    assert len(log.query("acme")) == 1


def test_the_tap_writes_nothing_in_local_mode(log):
    registry = _Registry()
    call = audit.tap_registry(registry.call, log)      # real tenant resolver
    call("create_part", {"project": "widget"})
    assert log.orgs() == []                            # no tenant, no database


def test_the_tap_resolves_the_org_from_the_tenant(log):
    from agentcad.core.tenancy import tenant_scope

    registry = _Registry()
    call = audit.tap_registry(registry.call, log, principal=lambda: "chat")
    with tenant_scope(("acme", "main")):
        call("create_part", {"project": "widget"})
    assert [r["principal"] for r in log.query("acme")] == ["chat"]


def test_a_broken_backend_does_not_break_the_product(log, capsys, monkeypatch):
    """Stated in `tap_registry`'s docstring and pinned here: an audit database
    that has gone read-only must not stop a CAD write. It must be loud."""
    def boom(*args, **kwargs):
        raise sqlite3.OperationalError("attempt to write a readonly database")

    monkeypatch.setattr(log, "append", boom)
    _registry, call = _tapped(log)
    assert call("create_part", {"project": "widget"}) == {"ok": True}
    assert "audit append failed" in capsys.readouterr().err


def test_the_tap_prefers_the_hosted_principal_over_the_ambient_client_id(log):
    """`current_principal_id`'s three sources, in order. This is what makes
    AC6's three kinds distinguishable at all."""
    from agentcad.core import locks
    from agentcad.server import security as sec

    locks.set_client_id("chat")
    assert audit.current_principal_id() == "chat"
    token = sec._principal_var.set(                       # noqa: SLF001
        sec.Principal(kind="agent", name="ci", role="member", via="bearer"))
    try:
        assert audit.current_principal_id() == "agent:ci"
    finally:
        sec._principal_var.reset(token)                   # noqa: SLF001
    assert audit.current_principal_id() == "chat"


def test_ac6_three_principal_kinds_are_distinguished_on_one_project(log):
    """AC6. A person's browser, the built-in chat agent and a bearer token act
    on the same project; the log tells all three apart.

    The chat dock genuinely is a third thing: it sets `locks.set_client_id
    ("chat")` inside its executor (`agent/chat.py::_call_tool`) and has no HTTP
    principal at all, so an audit that only read `security.current_principal()`
    would record it as whoever was signed in — or as nobody.
    """
    from agentcad.core import locks
    from agentcad.core.tenancy import tenant_scope
    from agentcad.server import security as sec

    registry = _Registry()
    call = audit.tap_registry(registry.call, log)

    with tenant_scope(("acme", "main")):
        person = sec.Principal(kind="user", name="nikita", role="admin",
                               device="browser:7f3a1b2c", via="cookie")
        token = sec._principal_var.set(person)            # noqa: SLF001
        try:
            call("set_part_params", {"project": "widget"})
        finally:
            sec._principal_var.reset(token)               # noqa: SLF001

        locks.set_client_id("chat")
        call("set_part_params", {"project": "widget"})

        agent = sec.Principal(kind="agent", name="ci", role="member",
                              via="bearer")
        token = sec._principal_var.set(agent)             # noqa: SLF001
        try:
            call("set_part_params", {"project": "widget"})
        finally:
            sec._principal_var.reset(token)               # noqa: SLF001

    rows = log.query("acme", project="widget")
    assert {(r["principal"], r["kind"]) for r in rows} == {
        ("user:nikita/browser:7f3a1b2c", "human"),
        ("chat", "agent"),
        ("agent:ci", "agent"),
    }


# ------------------------------------------------------- the admin CLI
#
# `agentcad admin audit …` reads the state files directly — no service, no
# kernel, no running server — which is `_auth_store`'s property and the reason
# `docker compose exec` works while the server is up.


@pytest.fixture
def cli_state(tmp_path, monkeypatch):
    """`AGENTCAD_CONFIG` isolates the state dir, `test_cli_admin`'s idiom."""
    monkeypatch.setenv("AGENTCAD_CONFIG", str(tmp_path / "config.json"))
    monkeypatch.delenv("AGENTCAD_STATE_DIR", raising=False)
    from agentcad.core.appmode import ensure_state_dir

    return AuditLog(ensure_state_dir())


def _cli(monkeypatch, *argv):
    from agentcad.cli import main

    monkeypatch.setattr("sys.argv", ["agentcad", *argv])
    main()


def test_admin_audit_query_prints_rows(cli_state, monkeypatch, capsys):
    cli_state.append("acme", {**ROW, "principal": "agent:ci"})
    _cli(monkeypatch, "admin", "audit", "query", "acme")
    out = capsys.readouterr().out
    assert "agent:ci" in out and "set_part_params" in out and "widget" in out
    assert "1 row(s) of 1" in out


def test_admin_audit_query_filters_and_json(cli_state, monkeypatch, capsys):
    cli_state.append("acme", {**ROW, "principal": "agent:ci"})
    cli_state.append("acme", {**ROW, "principal": "user:anya",
                              "project": "bracket"})
    _cli(monkeypatch, "admin", "audit", "query", "acme",
         "--principal", "agent:ci", "--json")
    payload = __import__("json").loads(capsys.readouterr().out)
    assert [row["project"] for row in payload["rows"]] == ["widget"]
    assert payload["total"] == 2


def test_admin_audit_query_on_an_org_with_no_log_says_so(cli_state,
                                                         monkeypatch, capsys):
    _cli(monkeypatch, "admin", "audit", "query", "ghost")
    assert "no audit log for org" in capsys.readouterr().out
    assert cli_state.orgs() == []           # and it did not create one


def test_admin_audit_backup_uses_vacuum_into(cli_state, tmp_path, monkeypatch,
                                             capsys):
    for index in range(20):
        cli_state.append("acme", {**ROW, "action": f"call_{index}"})
    dest = tmp_path / "audit-2026-08-24.db"
    _cli(monkeypatch, "admin", "audit", "backup", "acme", str(dest))
    assert "integrity: ok" in capsys.readouterr().out
    conn = sqlite3.connect(str(dest))
    try:
        assert conn.execute("SELECT COUNT(*) FROM audit").fetchone()[0] == 20
    finally:
        conn.close()


def test_admin_audit_backup_refuses_an_unknown_org(cli_state, tmp_path,
                                                   monkeypatch):
    with pytest.raises(SystemExit) as excinfo:
        _cli(monkeypatch, "admin", "audit", "backup", "ghost",
             str(tmp_path / "x.db"))
    assert excinfo.value.code != 0


def test_admin_audit_backup_refuses_an_existing_destination(cli_state, tmp_path,
                                                            monkeypatch):
    cli_state.append("acme", ROW)
    dest = tmp_path / "taken.db"
    dest.write_bytes(b"")
    with pytest.raises(SystemExit) as excinfo:
        _cli(monkeypatch, "admin", "audit", "backup", "acme", str(dest))
    assert excinfo.value.code == 2          # an AppError, not a traceback


# ------------------------------------------------------------- instances


def test_shared_returns_one_log_per_state_dir(tmp_path):
    first = audit.shared(tmp_path / "state")
    assert audit.shared(tmp_path / "state") is first
    assert audit.shared(tmp_path / "other") is not first


def test_for_auth_store_puts_the_audit_beside_identity_not_inside_it(tmp_path):
    from agentcad.core.authstore import AuthStore

    store = AuthStore(tmp_path / "state" / "auth")
    log = audit.for_auth_store(store)
    assert log.root == (tmp_path / "state" / "audit")
    assert not str(log.root).startswith(str(store.root))
