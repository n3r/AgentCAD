"""PRD-005a slice 1: the two deployment modes and their binding interlock.

The interlock is the load-bearing part and the reason mode is *explicit*: a
mode derived from "is auth configured" fails **open** on a typo, which is the
one failure direction that must be impossible (design spec, Decision 3).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from agentcad.core.appmode import (
    DEFAULT_TRUSTED_PROXY,
    HOSTED,
    LOCAL,
    AppMode,
    ModeError,
    check_bind,
    resolve_mode,
    state_dir,
    trusted_proxy,
)


# ------------------------------------------------------------------- modes


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


# ------------------------------------------- fail-closed, in every direction


def test_an_unknown_mode_is_refused_rather_than_defaulted():
    """A typo must not silently downgrade to `local` — that is the failure
    direction Decision 3 exists to make impossible."""
    with pytest.raises(ModeError) as exc:
        resolve_mode({"AGENTCAD_MODE": "hostde"})
    assert "local" in str(exc.value) and "hosted" in str(exc.value)


def test_hosted_refuses_a_short_secret_naming_the_setting():
    with pytest.raises(ModeError) as exc:
        resolve_mode({
            "AGENTCAD_MODE": "hosted",
            "AGENTCAD_PUBLIC_ORIGIN": "https://cad.example.com",
            "AGENTCAD_SECRET_KEY": "short",
        })
    assert "AGENTCAD_SECRET_KEY" in str(exc.value)


@pytest.mark.parametrize("origin", [
    "cad.example.com",                 # no scheme
    "ftp://cad.example.com",           # wrong scheme
    "https://cad.example.com/app",     # a path, not an origin
    "https://",                        # no host
    "https://cad.example.com/",        # trailing slash is stripped, not a path
])
def test_origin_grammar(origin):
    env = {"AGENTCAD_MODE": "hosted", "AGENTCAD_PUBLIC_ORIGIN": origin,
           "AGENTCAD_SECRET_KEY": "s" * 32}
    if origin.endswith("com/"):
        assert resolve_mode(env).public_origin == "https://cad.example.com"
        return
    with pytest.raises(ModeError) as exc:
        resolve_mode(env)
    assert "AGENTCAD_PUBLIC_ORIGIN" in str(exc.value)


def test_http_origin_does_not_ask_for_secure_cookies():
    """A `Secure` cookie on a plain-http origin is never sent back, which
    reads to the operator as "login silently does nothing"."""
    mode = resolve_mode({
        "AGENTCAD_MODE": "hosted",
        "AGENTCAD_PUBLIC_ORIGIN": "http://127.0.0.1:8630",
        "AGENTCAD_SECRET_KEY": "s" * 32,
    })
    assert mode.secure_cookies is False
    assert mode.origin_host == "127.0.0.1"       # the port is not part of it


def test_origin_host_strips_the_port_and_keeps_ipv6_brackets():
    assert AppMode(HOSTED, "https://cad.example.com:8443", b"k").origin_host \
        == "cad.example.com"
    assert AppMode(HOSTED, "http://[::1]:8630", b"k").origin_host == "[::1]"


def test_local_mode_carries_no_origin_and_no_secret():
    mode = resolve_mode({"AGENTCAD_MODE": "local"})
    assert (mode.public_origin, mode.secret, mode.secure_cookies) == (None, None, False)
    assert mode.origin_host is None


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1", "[::1]"])
def test_local_allows_every_loopback_spelling(host):
    check_bind(AppMode(LOCAL, None, None), host)


# --------------------------------------------------------- the secret file


def test_a_generated_secret_is_persisted_0600_and_stable(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTCAD_STATE_DIR", str(tmp_path / "state"))
    env = {"AGENTCAD_MODE": "hosted",
           "AGENTCAD_PUBLIC_ORIGIN": "https://cad.example.com"}
    first = resolve_mode(env)
    assert first.secret is not None and len(first.secret) >= 32
    key = tmp_path / "state" / "secret.key"
    assert key.is_file()
    assert oct(key.stat().st_mode & 0o777) == "0o600"
    assert resolve_mode(env).secret == first.secret   # stable across restarts


def test_trusted_proxy_defaults_to_loopback_and_refuses_wildcard():
    """`AGENTCAD_TRUSTED_PROXY` decides whose `X-Forwarded-For` uvicorn believes,
    which is what makes the login limiter's address the real client behind the
    documented proxy (review finding M3, round 2). The default is the local
    proxy; `*` is refused because it re-opens the forgery it exists to prevent."""
    assert trusted_proxy({}) == DEFAULT_TRUSTED_PROXY == "127.0.0.1"
    assert trusted_proxy({"AGENTCAD_TRUSTED_PROXY": ""}) == "127.0.0.1"
    assert trusted_proxy({"AGENTCAD_TRUSTED_PROXY": "10.0.0.0/8"}) == "10.0.0.0/8"
    assert trusted_proxy(
        {"AGENTCAD_TRUSTED_PROXY": "10.0.0.1, 10.0.0.2"}) == "10.0.0.1, 10.0.0.2"
    # Refused by MEANING, not spelling: uvicorn 0.52.1 reads 0.0.0.0/0 and
    # ::/0 as trust-everyone, identical to `*` (review m11). A bounded CIDR is
    # fine; a prefixlen-0 network in any position is not.
    for danger in ("*", "127.0.0.1, *", "0.0.0.0/0", "::/0",
                   "127.0.0.1, 0.0.0.0/0", " ::/0 "):
        with pytest.raises(ModeError, match="AGENTCAD_TRUSTED_PROXY"):
            trusted_proxy({"AGENTCAD_TRUSTED_PROXY": danger})
    # A bounded CIDR and a bare IP both survive — refusing them would be wrong.
    assert trusted_proxy({"AGENTCAD_TRUSTED_PROXY": "0.0.0.0/1"}) == "0.0.0.0/1"


def test_the_state_directory_itself_is_0700_however_it_was_created(
        tmp_path, monkeypatch):
    """Review finding m2: the *directory* holding the secret and the password
    hashes was 0755 on a real first boot.

    `mkdir(parents=True, mode=0o700)` sets the final component only, and
    `agentcad admin user add` builds `AuthStore(state_dir()/"auth")` first —
    so `state` was born as an intermediate parent at 0755 and `exist_ok=True`
    left it there. `ensure_state_dir` is what repairs it, and this test
    reproduces that exact order.
    """
    import stat as stat_mod

    from agentcad.core.appmode import ensure_state_dir
    from agentcad.core.authstore import AuthStore

    monkeypatch.setenv("AGENTCAD_STATE_DIR", str(tmp_path / "state"))
    AuthStore(state_dir() / "auth")                  # the admin CLI's order
    assert stat_mod.S_IMODE((tmp_path / "state").stat().st_mode) & 0o077

    assert ensure_state_dir() == tmp_path / "state"
    assert oct(stat_mod.S_IMODE((tmp_path / "state").stat().st_mode)) == "0o700"
    # And on a directory it creates itself.
    monkeypatch.setenv("AGENTCAD_STATE_DIR", str(tmp_path / "fresh"))
    assert oct(stat_mod.S_IMODE(ensure_state_dir().stat().st_mode)) == "0o700"


def test_state_dir_follows_the_config_path_so_tests_are_isolated(tmp_path, monkeypatch):
    """FR25: the derivation that makes every AGENTCAD_CONFIG-setting test get
    an isolated identity store for free, and that keeps identity out of
    --projects-dir."""
    monkeypatch.delenv("AGENTCAD_STATE_DIR", raising=False)
    monkeypatch.setenv("AGENTCAD_CONFIG", str(tmp_path / "cfg" / "config.json"))
    assert state_dir() == tmp_path / "cfg" / "state"
    monkeypatch.setenv("AGENTCAD_STATE_DIR", str(tmp_path / "elsewhere"))
    assert state_dir() == tmp_path / "elsewhere"


def test_resolve_mode_reads_the_process_environment_by_default(monkeypatch):
    monkeypatch.delenv("AGENTCAD_MODE", raising=False)
    assert resolve_mode().name == LOCAL
    monkeypatch.setenv("AGENTCAD_MODE", "nonsense")
    with pytest.raises(ModeError):
        resolve_mode()


def test_no_secret_material_appears_in_a_mode_error(tmp_path, monkeypatch):
    """An error message is logged and shown; it must never carry the key."""
    monkeypatch.setenv("AGENTCAD_STATE_DIR", str(tmp_path / "state"))
    with pytest.raises(ModeError) as exc:
        resolve_mode({"AGENTCAD_MODE": "hosted",
                      "AGENTCAD_SECRET_KEY": "s3cr3t-" + "z" * 40})
    assert "s3cr3t" not in str(exc.value)


def test_the_mode_is_frozen(monkeypatch):
    """`AppMode` is passed into the guard and read on every request; a mutable
    one would be a way to turn hosting off at runtime."""
    mode = AppMode(HOSTED, "https://x.example", b"k" * 32)
    with pytest.raises(Exception):
        mode.name = LOCAL           # type: ignore[misc]


def test_check_bind_accepts_a_wildcard_only_in_hosted(tmp_path):
    for host in ("0.0.0.0", "::", "192.168.1.10", "cad.example.com"):
        with pytest.raises(ModeError):
            check_bind(AppMode(LOCAL, None, None), host)
        check_bind(AppMode(HOSTED, "https://x.example", b"k"), host)


def test_state_dir_is_not_derived_from_the_projects_dir(monkeypatch, tmp_path):
    """FR25's containment, asserted rather than assumed: no projects-dir
    setting may move identity state, which is what keeps PRD-004/011's
    ephemeral services and `git add -A` away from it."""
    monkeypatch.delenv("AGENTCAD_STATE_DIR", raising=False)
    monkeypatch.setenv("AGENTCAD_CONFIG", str(tmp_path / "cfg" / "config.json"))
    monkeypatch.setenv("AGENTCAD_PROJECTS_DIR", str(tmp_path / "projects"))
    resolved = state_dir()
    assert Path(os.environ["AGENTCAD_PROJECTS_DIR"]) not in resolved.parents
    assert resolved == tmp_path / "cfg" / "state"
