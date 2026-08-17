"""PRD-005a slice 5: the carry-overs a non-loopback bind opens (FR18-FR22).

Design Decision 9 lists eight things loopback was silently providing. Six of
them are the guard (slice 2). The rest are here, and every one is written with
its **negation** beside it: a refusal that also fires in local mode would be a
regression dressed as hardening, and an interlock that refused nothing would be
a comment.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap

import pytest

from .conftest import login as _login, make_test_service


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    """No test in this file may touch a real `~/.agentcad`.

    `config.get_port()` **writes** the config file when no port is stored yet,
    and several tests here drive the CLI's bind resolution.
    """
    monkeypatch.setenv("AGENTCAD_CONFIG", str(tmp_path / "config.json"))
    monkeypatch.delenv("AGENTCAD_STATE_DIR", raising=False)
    monkeypatch.delenv("AGENTCAD_PROJECTS_DIR", raising=False)


def _project(client, name="demo"):
    r = client.post("/api/projects", json={"name": name})
    assert r.status_code in (201, 409), r.text
    return name


def _local_client(kernel, tmp_path):
    """A local-mode app: the control group for every refusal below."""
    from fastapi.testclient import TestClient

    from agentcad.core.tools import build_registry
    from agentcad.server import security as security_module
    from agentcad.server.app import create_app

    security_module.install(None)
    service = make_test_service(tmp_path / "local-projects", kernel)
    app = create_app(service, build_registry(service),
                     extra_allowed_hosts={"testserver"})
    return TestClient(app, base_url="http://127.0.0.1"), service


# ------------------------------------------------- FR19: /api/projects/open


def test_projects_open_is_refused_in_hosted_mode(hosted, tmp_path):
    """It registers ANY absolute path on the server as a project. On a
    loopback box that could only reach the operator's own disk; on a hosted one
    it is `/etc` as a project tree."""
    client, _ = hosted
    _login(client)
    r = client.post("/api/projects/open", json={"path": str(tmp_path)})
    assert r.status_code == 403
    body = r.json()["error"]
    assert body["type"] == "AuthzError"
    assert "hosted" in body["message"]


def test_projects_open_still_works_in_local_mode(kernel, tmp_path):
    """The negation. Opening a bundled example by path is how the local
    product ships, and hardening must not cost it."""
    client, service = _local_client(kernel, tmp_path)
    created = service.store.create("openable")
    r = client.post("/api/projects/open", json={"path": str(created)})
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "openable"


# --------------------------------------------- FR19: import_cad_file (path)


def test_import_cad_file_absolute_path_is_refused_in_hosted_mode(hosted, tmp_path):
    """A tool refusal is an error PAYLOAD, not an HTTP status: `ToolRegistry.
    call` converts every `AppError` into `{"error": {...}}` with a 200 so
    agents can read and react to it (`core/tools.py`). Asserting 403 here
    would be asserting against the house contract."""
    client, _ = hosted
    _login(client)
    _project(client)
    step = tmp_path / "secret.step"
    step.write_text("ISO-10303-21;")
    r = client.post("/api/tools/import_cad_file",
                    json={"project": "demo", "source": str(step),
                          "part_id": "leak"})
    assert r.status_code == 200, r.text
    error = r.json()["error"]
    assert error["type"] == "authz_error"
    assert "hosted" in error["message"]
    # And nothing was ingested: the refusal is before the read, not after.
    assert client.get("/api/projects/demo").json()["parts"] == []


def test_a_relative_uploaded_filename_is_still_importable_in_hosted_mode(hosted):
    """The negation. The refusal is about reading the SERVER's disk, not about
    importing at all — a file already uploaded into the project's imports/ dir
    must still work, or hosted mode loses the feature."""
    client, _ = hosted
    _login(client)
    _project(client)
    r = client.post("/api/tools/import_cad_file",
                    json={"project": "demo", "source": "nope.step",
                          "part_id": "x"})
    error = r.json()["error"]
    # It gets past the hosted guard and fails on the real thing: no such
    # uploaded file. `authz_error` here would mean the refusal was too broad.
    assert error["type"] == "validation_error", error
    assert "upload" in error["message"]


def test_import_cad_file_absolute_path_still_works_in_local_mode(kernel, tmp_path):
    client, service = _local_client(kernel, tmp_path)
    service.store.create("demo")
    r = client.post("/api/tools/import_cad_file",
                    json={"project": "demo", "source": str(tmp_path / "no.step"),
                          "part_id": "x"})
    # Reaches the ingest and fails there (the file does not exist) rather than
    # being refused by mode.
    assert r.json()["error"]["type"] != "authz_error", r.text


# ------------------------------------------------------- FR20: the beacon


def test_a_beacon_may_not_name_another_principal(hosted):
    client, _ = hosted
    _login(client)                                   # user:nikita
    _project(client)
    r = client.post("/api/projects/demo/presence",
                    json={"leave": True,
                          "client_id": "user:anya/browser:bbbbbbbb"})
    assert r.status_code == 422
    assert r.json()["error"]["type"] == "ValidationError"


def test_a_beacon_naming_your_own_device_is_accepted(hosted):
    client, _ = hosted
    _login(client)
    _project(client)
    r = client.post("/api/projects/demo/presence",
                    json={"leave": True,
                          "client_id": "user:nikita/browser:7f3a1b2c"},
                    headers={"X-Agent-Id": "browser:7f3a1b2c"})
    assert r.status_code == 200, r.text


def test_a_beacon_naming_your_bare_principal_is_accepted(hosted):
    """The device suffix is optional: `pagehide` fires from a tab that may
    never have sent an `X-Agent-Id`."""
    client, _ = hosted
    _login(client)
    _project(client)
    r = client.post("/api/projects/demo/presence",
                    json={"leave": True, "client_id": "user:nikita"})
    assert r.status_code == 200, r.text


def test_an_agent_beacon_may_not_name_a_person(hosted):
    client, store = hosted
    _project(_login(client))
    bearer = store.add_token("ci")
    r = client.post("/api/projects/demo/presence",
                    json={"leave": True, "client_id": "user:nikita"},
                    headers={"Authorization": f"Bearer {bearer}"})
    assert r.status_code == 422


def test_a_beacon_prefix_collision_is_not_enough(hosted):
    """`user:nikita2` starts with `user:nikita` as a STRING and is a different
    person. The namespace boundary is the `/`, not the prefix."""
    client, _ = hosted
    _login(client)
    _project(client)
    r = client.post("/api/projects/demo/presence",
                    json={"leave": True, "client_id": "user:nikita2"})
    assert r.status_code == 422


def test_a_local_app_built_after_a_hosted_one_does_not_inherit_the_rule(
        hosted, kernel, tmp_path):
    """`security.current_config()` falls back to a PROCESS-GLOBAL slot, so a
    router reading it per request would make a local app enforce the hosted
    beacon rule the moment a hosted app existed in the same process. The rule
    is captured at mount time instead — `create_app` installs before it mounts
    packs — and this is the test that would catch a regression to per-request
    reads. One app per process in production; the suite builds both constantly.
    """
    from fastapi.testclient import TestClient

    from agentcad.core.tools import build_registry
    from agentcad.server.app import create_app

    # `hosted` leaves its config installed for the duration of the test.
    service = make_test_service(tmp_path / "after", kernel)
    service.store.create("demo")
    app = create_app(service, build_registry(service),
                     extra_allowed_hosts={"testserver"})
    client = TestClient(app, base_url="http://127.0.0.1")
    r = client.post("/api/projects/demo/presence",
                    json={"leave": True, "client_id": "browser:deadbeef"})
    assert r.status_code == 200, r.text


def test_a_local_beacon_may_still_name_any_identity(kernel, tmp_path):
    """The negation, and PRD-008's documented behaviour: in local mode the
    header is self-asserted anyway, so the body is no worse. Tightening it
    here would break the `pagehide` beacon the browser actually sends."""
    client, service = _local_client(kernel, tmp_path)
    service.store.create("demo")
    r = client.post("/api/projects/demo/presence",
                    json={"leave": True, "client_id": "browser:deadbeef"})
    assert r.status_code == 200, r.text


# ----------------------------------------------------------- FR21: health


def test_health_is_trimmed_without_a_principal(hosted):
    client, _ = hosted
    assert client.get("/api/health").json() == {"status": "ok", "mode": "hosted"}


def test_health_is_full_for_a_principal(hosted):
    client, _ = hosted
    _login(client)
    body = client.get("/api/health").json()
    assert {"version", "kernel", "sandbox", "chat_available"} <= set(body)


# ------------------------------------------- FR22 / Decision 3: the CLI


def test_serve_refuses_a_public_bind_in_local_mode(monkeypatch, capsys):
    """The interlock. It must fire BEFORE the kernel pool spawns, or a refusal
    costs half a gigabyte and a stray worker."""
    from agentcad import cli

    def explode(*args, **kwargs):                     # pragma: no cover
        raise AssertionError("the bind interlock must refuse before the "
                             "service (and its ~0.5 GB workers) are built")

    monkeypatch.setattr(cli, "_build_service", explode)
    monkeypatch.delenv("AGENTCAD_MODE", raising=False)
    monkeypatch.setattr("sys.argv",
                        ["agentcad", "serve", "--host", "0.0.0.0", "--no-open"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
    assert "AGENTCAD_MODE=hosted" in capsys.readouterr().err


def test_a_loopback_bind_is_still_fine_in_local_mode(monkeypatch):
    from agentcad import cli
    from agentcad.core.appmode import AppMode

    args = _serve_args(host=None, port=None)
    monkeypatch.delenv("AGENTCAD_HOST", raising=False)
    monkeypatch.delenv("AGENTCAD_PORT", raising=False)
    host, _port = cli._serve_bind(args, AppMode("local", None, None))
    assert host == "127.0.0.1"


def test_hosted_mode_allows_a_public_bind(monkeypatch):
    from agentcad import cli
    from agentcad.core.appmode import AppMode

    mode = AppMode("hosted", "https://cad.example", b"k" * 32)
    monkeypatch.delenv("AGENTCAD_PORT", raising=False)
    host, _port = cli._serve_bind(_serve_args(host="0.0.0.0"), mode)
    assert host == "0.0.0.0"


def test_the_bind_is_configurable_by_environment(monkeypatch):
    """The container sets `AGENTCAD_HOST`/`AGENTCAD_PORT` rather than
    overriding the image's command."""
    from agentcad import cli
    from agentcad.core.appmode import AppMode

    monkeypatch.setenv("AGENTCAD_HOST", "0.0.0.0")
    monkeypatch.setenv("AGENTCAD_PORT", "9001")
    host, port = cli._serve_bind(_serve_args(),
                                 AppMode("hosted", "https://x.example", b"k" * 32))
    assert (host, port) == ("0.0.0.0", 9001)


def test_an_explicit_flag_beats_the_environment(monkeypatch):
    from agentcad import cli
    from agentcad.core.appmode import AppMode

    monkeypatch.setenv("AGENTCAD_HOST", "0.0.0.0")
    monkeypatch.setenv("AGENTCAD_PORT", "9001")
    mode = AppMode("hosted", "https://x.example", b"k" * 32)
    assert cli._serve_bind(_serve_args(host="127.0.0.1", port=9999),
                           mode) == ("127.0.0.1", 9999)


def test_a_nonsense_port_in_the_environment_is_refused(monkeypatch):
    from agentcad import cli
    from agentcad.core.appmode import AppMode

    monkeypatch.setenv("AGENTCAD_PORT", "not-a-port")
    with pytest.raises(SystemExit):
        cli._serve_bind(_serve_args(),
                        AppMode("hosted", "https://x.example", b"k" * 32))


def test_uvicorn_trusts_the_proxy_only_in_hosted_mode(monkeypatch):
    """Review finding M3 (round 2): the login limit's `(handle, address)` key
    is only real when uvicorn resolves the client from `X-Forwarded-For`.

    Hosted → `proxy_headers` on, bounded to the trusted proxy (default
    loopback). Local → off, so a loopback page cannot set its own forwarded
    address. Set explicitly rather than left to uvicorn's default, so a version
    bump cannot silently flip the security property.
    """
    from agentcad import cli
    from agentcad.core.appmode import AppMode

    monkeypatch.delenv("AGENTCAD_TRUSTED_PROXY", raising=False)
    hosted = cli._uvicorn_proxy_kwargs(AppMode("hosted", "https://x.example", b"k" * 32))
    assert hosted == {"proxy_headers": True, "forwarded_allow_ips": "127.0.0.1"}

    local = cli._uvicorn_proxy_kwargs(AppMode("local", None, None))
    assert local["proxy_headers"] is False
    assert not local["forwarded_allow_ips"]        # empty: no peer is trusted

    monkeypatch.setenv("AGENTCAD_TRUSTED_PROXY", "10.0.0.0/8")
    hosted2 = cli._uvicorn_proxy_kwargs(AppMode("hosted", "https://x.example", b"k" * 32))
    assert hosted2["forwarded_allow_ips"] == "10.0.0.0/8"


def test_serve_refuses_a_wildcard_trusted_proxy(monkeypatch, capsys):
    """`AGENTCAD_TRUSTED_PROXY=*` lets any client forge the address the login
    limiter keys on, so the start is refused (clean exit 2, not a traceback),
    and before the kernel pool spawns."""
    from agentcad import cli

    def explode(*args, **kwargs):                     # pragma: no cover
        raise AssertionError("a dangerous trusted-proxy must refuse before the "
                             "service is built")

    monkeypatch.setattr(cli, "_build_service", explode)
    monkeypatch.setenv("AGENTCAD_MODE", "hosted")
    monkeypatch.setenv("AGENTCAD_PUBLIC_ORIGIN", "https://cad.example.com")
    monkeypatch.setenv("AGENTCAD_TRUSTED_PROXY", "*")
    monkeypatch.setattr("sys.argv",
                        ["agentcad", "serve", "--host", "0.0.0.0", "--no-open"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
    assert "AGENTCAD_TRUSTED_PROXY" in capsys.readouterr().err


def _serve_args(host=None, port=None):
    import argparse

    return argparse.Namespace(host=host, port=port, projects_dir=None,
                              no_open=True)


# ---------------------------------------------------- FR22: the examples


class _StubStore:
    def __init__(self):
        self.opened = []

    def open(self, path):
        self.opened.append(path)
        return {"name": path.name}


class _StubService:
    def __init__(self):
        self.store = _StubStore()

    @property
    def opened(self):
        return self.store.opened


def test_examples_are_skipped_when_disabled(monkeypatch):
    from agentcad import cli

    monkeypatch.setenv("AGENTCAD_EXAMPLES", "0")
    service = _StubService()
    cli._register_examples(service)
    assert service.opened == []


def test_examples_are_registered_by_default(monkeypatch):
    """The negation: the local product opens the bundled examples at startup
    and that is how a new user first sees geometry."""
    from agentcad import cli

    monkeypatch.delenv("AGENTCAD_EXAMPLES", raising=False)
    service = _StubService()
    cli._register_examples(service)
    assert service.opened, "the bundled examples stopped being registered"


def test_the_projects_dir_follows_the_environment(monkeypatch, tmp_path):
    """FR24 enumerates `AGENTCAD_PROJECTS_DIR` and the compose file sets it;
    without this it would be a documented variable nothing reads."""
    from agentcad import cli

    monkeypatch.setenv("AGENTCAD_PROJECTS_DIR", str(tmp_path / "vol"))
    args = _serve_args()
    assert cli._projects_dir(args) == tmp_path / "vol"
    args.projects_dir = str(tmp_path / "flag")
    assert cli._projects_dir(args) == tmp_path / "flag"


# -------------------------------------------------------------- portability


@pytest.mark.portability
def test_the_server_imports_without_fcntl():
    """`fcntl` is POSIX-only and `authstore` needs it for the cross-process
    lock, but importing the *server* on a platform without it must still work
    — otherwise local mode on Windows dies at import time for a hosted-only
    feature. The mechanism is the guarded `try: import fcntl` in
    `core/authstore.py`, not laziness in `server/app.py`; this test pins the
    property either way.
    """
    program = textwrap.dedent("""
        import sys

        class Block:
            def find_spec(self, name, path=None, target=None):
                if name.split(".")[0] == "fcntl":
                    raise ImportError("fcntl is unavailable on this platform")
                return None

        sys.modules.pop("fcntl", None)
        sys.meta_path.insert(0, Block())
        import agentcad.server.app
        import agentcad.server.security
        import agentcad.core.authstore as a
        assert a.fcntl is None, a.fcntl
        print("ok")
    """)
    proc = subprocess.run([sys.executable, "-c", program],
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "ok"


@pytest.mark.portability
def test_the_store_still_serialises_in_process_without_fcntl(tmp_path):
    """The documented degradation, asserted rather than assumed: with no
    `fcntl` the in-process lock still holds and the store still works. A
    silent crash here would be the Windows local-mode failure above."""
    program = textwrap.dedent(f"""
        import sys

        class Block:
            def find_spec(self, name, path=None, target=None):
                if name.split(".")[0] == "fcntl":
                    raise ImportError("no fcntl")
                return None

        sys.modules.pop("fcntl", None)
        sys.meta_path.insert(0, Block())
        from agentcad.core.authstore import AuthStore
        store = AuthStore({str(tmp_path / "auth")!r})
        token = store.add_user("nikita")
        assert store.enrol(token, "correct horse battery") == "nikita"
        assert store.verify_password("nikita", "correct horse battery") is True
        print("ok")
    """)
    proc = subprocess.run([sys.executable, "-c", program],
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "ok"
