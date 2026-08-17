"""PRD-005a slice 3: `agentcad admin user ...`.

The admin CLI is what makes registration *closed*: accounts are minted by an
operator with shell access, never by a form. It therefore has to work over
`docker compose exec` with no running server — which means no service, no
kernel, and direct access to the state files under an `fcntl.flock`.
"""

from __future__ import annotations

import json

import pytest


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    """`AGENTCAD_CONFIG` is what isolates the identity store (FR25), exactly
    as it already isolates the package cache and the index checkouts."""
    monkeypatch.setenv("AGENTCAD_CONFIG", str(tmp_path / "config.json"))
    monkeypatch.delenv("AGENTCAD_STATE_DIR", raising=False)
    monkeypatch.delenv("AGENTCAD_PUBLIC_ORIGIN", raising=False)
    return tmp_path


def _run(monkeypatch, *argv):
    from agentcad.cli import main

    monkeypatch.setattr("sys.argv", ["agentcad", *argv])
    main()


def _store():
    from agentcad.core.appmode import state_dir
    from agentcad.core.authstore import AuthStore

    return AuthStore(state_dir() / "auth")


# ------------------------------------------------------------- user add


def test_admin_user_add_prints_an_enrol_url_and_the_trust_sentence(
        tmp_path, monkeypatch, capsys):
    _run(monkeypatch, "admin", "user", "add", "nikita", "--admin")
    out = capsys.readouterr().out
    assert "/api/auth/enrol/" in out
    assert "execute arbitrary Python on the host" in out


def test_admin_user_add_refuses_a_bad_handle(tmp_path, monkeypatch):
    with pytest.raises(SystemExit) as exc:
        _run(monkeypatch, "admin", "user", "add", "Nikita")
    assert exc.value.code != 0


def test_the_printed_token_actually_enrols(tmp_path, monkeypatch, capsys):
    """The negation of "it printed something that looks like a URL"."""
    _run(monkeypatch, "admin", "user", "add", "nikita", "--admin")
    out = capsys.readouterr().out
    token = out.split("/api/auth/enrol/", 1)[1].split()[0].strip()
    store = _store()
    assert store.enrol(token, "correct horse battery") == "nikita"
    assert store.verify_password("nikita", "correct horse battery") is True
    assert store.list_users()[0]["role"] == "admin"


def test_without_admin_the_account_is_a_member(tmp_path, monkeypatch, capsys):
    _run(monkeypatch, "admin", "user", "add", "anya")
    capsys.readouterr()
    assert _store().list_users()[0]["role"] == "member"


def test_the_enrol_url_uses_the_public_origin_when_configured(
        tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("AGENTCAD_PUBLIC_ORIGIN", "https://cad.example.com")
    _run(monkeypatch, "admin", "user", "add", "nikita")
    assert "https://cad.example.com/api/auth/enrol/" in capsys.readouterr().out


def test_a_duplicate_handle_exits_nonzero_and_is_not_a_password_reset(
        tmp_path, monkeypatch, capsys):
    _run(monkeypatch, "admin", "user", "add", "nikita")
    token = capsys.readouterr().out.split("/api/auth/enrol/", 1)[1].split()[0]
    _store().enrol(token, "correct horse battery")

    with pytest.raises(SystemExit) as exc:
        _run(monkeypatch, "admin", "user", "add", "nikita")
    assert exc.value.code != 0
    assert _store().verify_password("nikita", "correct horse battery") is True


# ------------------------------------------------------- list and disable


def test_admin_user_list_shows_accounts_and_never_a_digest(
        tmp_path, monkeypatch, capsys):
    _run(monkeypatch, "admin", "user", "add", "nikita", "--admin")
    token = capsys.readouterr().out.split("/api/auth/enrol/", 1)[1].split()[0]
    _store().enrol(token, "correct horse battery")

    _run(monkeypatch, "admin", "user", "list")
    out = capsys.readouterr().out
    assert "nikita" in out and "admin" in out
    for leak in ("digest", "salt", "scrypt", "correct horse battery"):
        assert leak not in out, leak


def test_admin_user_disable_takes_effect_and_ends_sessions(
        tmp_path, monkeypatch, capsys):
    _run(monkeypatch, "admin", "user", "add", "anya")
    token = capsys.readouterr().out.split("/api/auth/enrol/", 1)[1].split()[0]
    store = _store()
    store.enrol(token, "hunter2hunter2")
    secret = store.create_session("anya", None)

    _run(monkeypatch, "admin", "user", "disable", "anya")
    capsys.readouterr()
    fresh = _store()
    assert fresh.verify_password("anya", "hunter2hunter2") is False
    assert fresh.resolve_session(secret) is None


def test_disabling_an_unknown_handle_exits_nonzero(tmp_path, monkeypatch):
    with pytest.raises(SystemExit) as exc:
        _run(monkeypatch, "admin", "user", "disable", "ghost")
    assert exc.value.code != 0


def test_admin_enrol_remints_a_link_and_invalidates_the_old_one(
        tmp_path, monkeypatch, capsys):
    """The recovery path: a lost invitation, or a forgotten password."""
    _run(monkeypatch, "admin", "user", "add", "anya")
    first = capsys.readouterr().out.split("/api/auth/enrol/", 1)[1].split()[0]

    _run(monkeypatch, "admin", "enrol", "anya")
    second = capsys.readouterr().out.split("/api/auth/enrol/", 1)[1].split()[0]
    assert second != first

    store = _store()
    with pytest.raises(Exception):
        store.enrol(first, "hunter2hunter2")
    assert store.enrol(second, "hunter2hunter2") == "anya"


# ------------------------------------------------------------- properties


def test_the_admin_cli_never_starts_a_kernel_or_a_service(
        tmp_path, monkeypatch, capsys):
    """This is what makes `docker compose exec agentcad agentcad admin ...`
    cheap: no ~0.5 GB worker, no project scan, no port. It also means the
    command works while the server is down."""
    from agentcad import cli

    def explode(*args, **kwargs):
        raise AssertionError("the admin CLI must not build a service")

    monkeypatch.setattr(cli, "_build_service", explode)
    _run(monkeypatch, "admin", "user", "add", "nikita")
    assert "/api/auth/enrol/" in capsys.readouterr().out


def test_the_state_lands_under_the_config_dir_not_the_projects_dir(
        tmp_path, monkeypatch, capsys):
    _run(monkeypatch, "admin", "user", "add", "nikita")
    capsys.readouterr()
    users = tmp_path / "state" / "auth" / "users.json"
    assert users.is_file()
    assert "nikita" in json.loads(users.read_text())


def test_the_state_files_are_not_world_readable(tmp_path, monkeypatch, capsys):
    _run(monkeypatch, "admin", "user", "add", "nikita")
    capsys.readouterr()
    root = tmp_path / "state" / "auth"
    assert not (root.stat().st_mode & 0o077)
    assert not ((root / "users.json").stat().st_mode & 0o077)


def test_the_help_carries_the_trust_sentence(tmp_path, monkeypatch, capsys):
    """FR17's third of four places (docs/deployment.md and compose.yaml are
    slice 6; the success output is above)."""
    with pytest.raises(SystemExit):
        _run(monkeypatch, "admin", "user", "add", "--help")
    assert "execute arbitrary Python on the host" in capsys.readouterr().out
