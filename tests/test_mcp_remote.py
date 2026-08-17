"""PRD-005a slice 4: the MCP proxy against a *remote* hosted instance (AC11).

Two properties, and both are about not being surprising. A configured
`AGENTCAD_TOKEN` must actually ride every proxied call, and a remote
`AGENTCAD_URL` must never cause a **local** server to be spawned — auto-starting
one machine because another is unreachable is a footgun that silently answers
from the wrong instance.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.portability


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ("AGENTCAD_URL", "AGENTCAD_TOKEN", "AGENTCAD_AGENT_ID"):
        monkeypatch.delenv(name, raising=False)


def _mcp():
    from agentcad.agent import mcp_server

    return mcp_server


# ------------------------------------------------------------------ headers


def test_the_proxy_sends_a_bearer_when_configured(monkeypatch):
    monkeypatch.setenv("AGENTCAD_URL", "https://cad.example.com")
    monkeypatch.setenv("AGENTCAD_TOKEN", "acad_deadbeef_" + "x" * 43)
    headers = _mcp()._client_headers()
    assert headers["Authorization"].startswith("Bearer acad_")
    assert headers["X-Agent-Id"] == "mcp"


def test_no_authorization_header_exists_without_a_token():
    """Local mode must send exactly what it always sent. An empty or absent
    `AGENTCAD_TOKEN` is not `Bearer ` — a blank credential would be a 401 on a
    hosted box and noise on a local one."""
    assert "Authorization" not in _mcp()._client_headers()


def test_a_blank_token_is_treated_as_absent(monkeypatch):
    monkeypatch.setenv("AGENTCAD_TOKEN", "   ")
    assert "Authorization" not in _mcp()._client_headers()


def test_the_agent_id_is_still_overridable(monkeypatch):
    """`X-Agent-Id` stays the local turn-locking identity; in hosted mode the
    guard demotes it to a *device* under the token's principal, but the proxy's
    behaviour is unchanged either way."""
    monkeypatch.setenv("AGENTCAD_AGENT_ID", "claude")
    assert _mcp()._client_headers()["X-Agent-Id"] == "claude"


def test_the_serving_client_actually_uses_those_headers(monkeypatch):
    """The header dict is only worth extracting if the client is built from
    it — otherwise this whole file tests a helper nobody calls."""
    import inspect

    source = inspect.getsource(_mcp()._serve)
    assert "_client_headers()" in source


# --------------------------------------------------------------- autostart


def test_a_remote_url_is_never_auto_spawned(monkeypatch):
    monkeypatch.setenv("AGENTCAD_URL", "https://cad.example.com")
    assert _mcp()._may_autostart() is False
    monkeypatch.setenv("AGENTCAD_URL", "http://127.0.0.1:8630")
    assert _mcp()._may_autostart() is True


def test_the_default_local_url_still_auto_spawns():
    """The convenience that makes `claude mcp add agentcad` a one-liner is the
    thing this must not break."""
    assert _mcp()._may_autostart() is True


@pytest.mark.parametrize("url,expected", [
    ("http://localhost:8630", True),
    ("http://[::1]:8630", True),
    ("http://127.0.0.1", True),
    ("https://cad.example.com", False),
    ("http://10.0.0.4:8630", False),
    ("http://127.0.0.1.evil.example", False),   # a prefix is not a host
])
def test_loopback_is_decided_by_host_not_by_substring(monkeypatch, url, expected):
    monkeypatch.setenv("AGENTCAD_URL", url)
    assert _mcp()._may_autostart() is expected


def test_ensure_server_refuses_to_spawn_for_a_remote_url(monkeypatch, capsys):
    """The behaviour, not just the predicate: an unreachable remote instance
    must produce a message about *that instance*, never a local server."""
    mcp = _mcp()
    monkeypatch.setenv("AGENTCAD_URL", "https://cad.example.com")
    monkeypatch.setattr(mcp, "_health_ok", lambda base: False)

    def explode(*args, **kwargs):                     # pragma: no cover
        raise AssertionError("a remote URL must never spawn a local server")

    monkeypatch.setattr(mcp.subprocess, "Popen", explode)
    assert mcp._ensure_server(mcp._base_url()) is False
    assert "cad.example.com" in capsys.readouterr().err
