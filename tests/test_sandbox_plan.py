"""PRD-006 slice 1 — the sandbox facade: `sandbox.plan()` and `SandboxPlan`.

`kernel/sandbox.py` stopped being "the macOS seatbelt" and became the seam:
it decides the private per-worker temp dir, the child's environment and the
posture, then asks a **platform backend** to confine the process and to say
which quota tier it can enforce. The backend is chosen by `sys.platform`;
`sandbox_macos` is the only one that exists in this slice, so Linux and
Windows fall to a `NullBackend` that reports confinement `unsupported` and
leaves quotas to the (Slice 3) supervisor.

These tests are all-OS: the platform and the backend are monkeypatched, so
they run identically on a macOS dev box and in Linux/Windows CI. The real
seatbelt runs live in `tests/test_sandbox.py` (darwin-only), and the handful
of genuinely macOS-specific units at the bottom of this file are skipped
elsewhere.
"""

from __future__ import annotations

import json
import os
import signal
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from agentcad.kernel import sandbox, sandbox_macos
from agentcad.kernel.quotas import Quotas, resolve

BASE_ARGV = ["/usr/bin/python3", "-u", "-m", "agentcad.kernel.worker"]


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    """No developer config, no ambient opt-out, no ambient mode or quotas."""
    monkeypatch.setenv("AGENTCAD_CONFIG", str(tmp_path / "no-such-config.json"))
    for name in ("AGENTCAD_NO_SANDBOX", "AGENTCAD_MODE", "AGENTCAD_PUBLIC_ORIGIN"):
        monkeypatch.delenv(name, raising=False)
    for name in list(os.environ):
        if name.startswith("AGENTCAD_QUOTA_"):
            monkeypatch.delenv(name, raising=False)
    return tmp_path


class _Backend:
    """A stand-in platform backend that honours the contract in `sandbox.py`.

    It records every `build()` call so a test can assert what the facade
    handed down (the write roots above all), and it behaves the way a real
    backend must when the operator has opted out of confinement.
    """

    def __init__(self):
        self.calls: list[SimpleNamespace] = []
        self.warnings = ["a backend warning"]
        self.attached = None
        self.released = 0

    def build(self, argv, write_roots, quotas, posture, server_pid, *,
              confine=True):
        self.calls.append(SimpleNamespace(
            argv=list(argv), write_roots=list(write_roots), quotas=quotas,
            posture=posture, server_pid=server_pid, confine=confine))
        wrapped = ["/fake/confine", *argv] if confine else list(argv)
        confinement = (
            {"status": "active", "mechanism": "fake",
             "detail": {"posture": posture}}
            if confine else
            {"status": "off", "mechanism": None, "detail": {}})
        report = {"status": "active", "mechanism": "fake+supervisor",
                  "limits": quotas.limits()}
        return wrapped, {"AGENTCAD_CONFINE": "{}"}, confinement, report, self

    # --- the Backend protocol
    def attach(self, proc): self.attached = proc
    def rss_bytes(self, proc): return None
    def explain_exit(self, proc, returncode): return None
    def release(self): self.released += 1


@pytest.fixture
def backend(monkeypatch):
    """A recording backend installed as the platform's, on every OS."""
    rec = _Backend()
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(sandbox_macos, "build", rec.build)
    return rec


def _plan(writable=None, **kwargs):
    return sandbox.plan(BASE_ARGV, [str(w) for w in (writable or [])], **kwargs)


# ------------------------------------------------------- the private temp dir

def test_the_worker_gets_a_private_temp_dir_and_is_pointed_at_it(isolated,
                                                                 backend):
    plan = _plan([isolated])
    try:
        tmp = Path(plan.tmp_dir)
        assert tmp.is_dir() and tmp.name.startswith("agentcad-worker-")
        assert tmp.resolve().parent == Path(tempfile.gettempdir()).resolve()
        # 0700: the whole point is that a sibling worker cannot read it.
        assert stat.S_IMODE(tmp.stat().st_mode) == 0o700
        for name in ("TMPDIR", "TEMP", "TMP", "XDG_CACHE_HOME", "HOME"):
            assert plan.env[name] == plan.tmp_dir, name
        assert plan.env["PYTHONDONTWRITEBYTECODE"] == "1"
    finally:
        plan.release()


def test_the_shared_system_temp_dir_is_never_a_write_root(isolated, backend):
    """The leak this exists to close: granting `tempfile.gettempdir()`
    wholesale lets one worker's script write into another's scratch."""
    plan = _plan([isolated])
    try:
        roots = backend.calls[0].write_roots
        assert roots == [os.path.realpath(str(isolated)),
                         os.path.realpath(plan.tmp_dir)]
        assert os.path.realpath(tempfile.gettempdir()) not in roots
    finally:
        plan.release()


def test_release_removes_the_temp_dir_and_frees_the_backend(isolated, backend):
    plan = _plan([isolated])
    tmp = Path(plan.tmp_dir)
    (tmp / "scratch.acm").write_text("x", encoding="utf-8")
    plan.release()
    assert not tmp.exists()
    assert backend.released == 1
    plan.release()  # idempotent: stop() after a crash must not raise
    assert not tmp.exists()


def test_wipe_tmp_empties_the_dir_but_keeps_it_for_the_respawn(isolated,
                                                               backend):
    """A killed worker's scratch is dropped immediately; the directory itself
    survives because the respawned worker's env still points at it."""
    plan = _plan([isolated])
    try:
        tmp = Path(plan.tmp_dir)
        (tmp / "a.acm").write_text("x", encoding="utf-8")
        (tmp / "sub").mkdir()
        (tmp / "sub" / "b.acm").write_text("x", encoding="utf-8")
        plan.wipe_tmp()
        assert tmp.is_dir() and list(tmp.iterdir()) == []
    finally:
        plan.release()


def test_prepare_tmp_recreates_a_removed_dir(isolated, backend):
    """`stop()` releases the plan; a client that is started again must not
    spawn a worker whose `$TMPDIR` does not exist."""
    plan = _plan([isolated])
    plan.release()
    plan.prepare_tmp()
    try:
        tmp = Path(plan.tmp_dir)
        assert tmp.is_dir() and stat.S_IMODE(tmp.stat().st_mode) == 0o700
    finally:
        plan.release()


def test_a_backend_that_raises_does_not_leak_the_temp_dir(isolated, monkeypatch):
    """The directory exists before the backend runs, and nobody holds the plan
    if the backend explodes — so `plan()` is what has to clean it up."""
    def _boom(*args, **kwargs):
        raise RuntimeError("no seatbelt today")

    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(sandbox_macos, "build", _boom)
    before = set(Path(tempfile.gettempdir()).glob(sandbox.TMP_PREFIX + "*"))
    with pytest.raises(RuntimeError):
        _plan([isolated])
    assert set(Path(tempfile.gettempdir()).glob(sandbox.TMP_PREFIX + "*")) == before


# ------------------------------------------------------------------ opting out

def test_the_env_kill_switch_drops_confinement_but_not_quotas(isolated,
                                                              backend,
                                                              monkeypatch):
    """`AGENTCAD_NO_SANDBOX` opts out of *confinement*. Quotas are not a
    confinement — a runaway script still may not take the machine down."""
    monkeypatch.setenv("AGENTCAD_NO_SANDBOX", "1")
    plan = _plan([isolated])
    try:
        assert plan.confinement["status"] == "off"
        assert "AGENTCAD_NO_SANDBOX" in plan.confinement["detail"]["reason"]
        assert plan.argv == BASE_ARGV          # unwrapped
        assert backend.calls[0].confine is False
        assert plan.quotas["status"] == "active"
        assert plan.quotas["limits"]["memory_mb"] == 2048
    finally:
        plan.release()


def test_the_config_opt_out_is_reported_as_such(isolated, backend, monkeypatch):
    (isolated / "cfg.json").write_text('{"sandbox": false}', encoding="utf-8")
    monkeypatch.setenv("AGENTCAD_CONFIG", str(isolated / "cfg.json"))
    plan = _plan([isolated])
    try:
        assert plan.confinement["status"] == "off"
        assert "config" in plan.confinement["detail"]["reason"]
        assert plan.quotas["status"] == "active"
    finally:
        plan.release()


# ----------------------------------------------------------- platform switch

def test_an_unknown_platform_is_unsupported_rather_than_a_crash(isolated,
                                                                monkeypatch):
    monkeypatch.setattr(sys, "platform", "sunos5")
    plan = _plan([isolated])
    try:
        assert plan.confinement["status"] == "unsupported"
        assert plan.confinement["mechanism"] is None
        assert "sunos5" in plan.confinement["detail"]["reason"]
        assert plan.argv == BASE_ARGV
        assert plan.quotas == {"status": "active", "mechanism": "supervisor",
                               "limits": plan.quotas_obj.limits()}
        # the null backend answers the whole protocol, so the client and the
        # (Slice 3) supervisor need no platform branches of their own
        assert plan.backend.rss_bytes(SimpleNamespace(pid=os.getpid())) is None
        assert plan.backend.explain_exit(None, -9) is None
        plan.backend.attach(None)
    finally:
        plan.release()


@pytest.mark.parametrize("platform", ["linux", "win32"])
def test_linux_and_windows_are_unsupported_until_their_slices_land(isolated,
                                                                   monkeypatch,
                                                                   platform):
    """Documents the state of this slice, and fails the day a backend module
    appears without the facade being pointed at it."""
    monkeypatch.setattr(sys, "platform", platform)
    plan = _plan([isolated])
    try:
        assert plan.confinement["status"] == "unsupported"
        assert plan.quotas["mechanism"] == "supervisor"
    finally:
        plan.release()


# ----------------------------------------------------------------- the quotas

def test_the_plan_carries_both_the_report_and_the_resolved_object(isolated,
                                                                  backend):
    """The supervisor needs `sample_interval_s` and `memory_mb` as numbers;
    health needs the dict. Both travel on the plan."""
    plan = _plan([isolated])
    try:
        assert isinstance(plan.quotas_obj, Quotas)
        assert plan.quotas["limits"] == plan.quotas_obj.limits()
        assert plan.quotas_obj.sample_interval_s == 0.25
        assert backend.calls[0].quotas is plan.quotas_obj
    finally:
        plan.release()


def test_quotas_may_be_given_as_a_dataclass_or_as_overrides(isolated, backend):
    resolved = resolve({"memory_mb": 512}, env={}, config={})
    plan = _plan([isolated], quotas=resolved)
    try:
        assert plan.quotas_obj is resolved
    finally:
        plan.release()

    plan = _plan([isolated], quotas={"memory_mb": 256})
    try:
        assert plan.quotas_obj.memory_mb == 256
    finally:
        plan.release()


# ---------------------------------------------------------------- the posture

def test_default_posture_follows_the_deployment_mode(monkeypatch, tmp_path):
    monkeypatch.delenv("AGENTCAD_MODE", raising=False)
    assert sandbox.default_posture() == "local"
    monkeypatch.setenv("AGENTCAD_MODE", "hosted")
    monkeypatch.setenv("AGENTCAD_PUBLIC_ORIGIN", "https://cad.example.com")
    monkeypatch.setenv("AGENTCAD_STATE_DIR", str(tmp_path / "state"))
    assert sandbox.default_posture() == "hosted"


def test_a_broken_mode_does_not_stop_a_worker_spawning(monkeypatch):
    """`resolve_mode` refuses an unrecognised AGENTCAD_MODE — that refusal
    belongs to server startup, not to the read posture of a kernel worker,
    which falls back to the stricter-to-explain `local`."""
    monkeypatch.setenv("AGENTCAD_MODE", "nonsense")
    assert sandbox.default_posture() == "local"


def test_the_posture_reported_is_the_one_in_effect(isolated, backend):
    plan = _plan([isolated], posture="hosted")
    try:
        assert backend.calls[0].posture == "hosted"
        assert plan.posture == "hosted"  # the fake backend applies what it is asked
        assert plan.warnings == ["a backend warning"]
    finally:
        plan.release()


# ------------------------------------------------- the unchanged public names

def test_wrap_argv_and_status_keep_their_semantics(monkeypatch, isolated):
    monkeypatch.setattr(sandbox, "available", lambda: False)
    assert sandbox.wrap_argv(BASE_ARGV, []) == BASE_ARGV
    assert sandbox.wrap_argv(BASE_ARGV, ["/tmp/x"]) == BASE_ARGV
    monkeypatch.setattr(sandbox, "supported", lambda: False)
    assert sandbox.status() == "unsupported"
    assert sandbox.status(True) == "unsupported"
    monkeypatch.setattr(sandbox, "supported", lambda: True)
    assert sandbox.status(True) == "active"
    assert sandbox.status(False) == "off"
    assert sandbox.status() == "off"  # available() is False above


def test_build_profile_grants_exactly_the_roots_it_is_given():
    """It is re-exported from `sandbox` (importers, `tests/test_sandbox.py`)
    and it no longer appends the system temp dir behind the caller's back.

    The root is deliberately outside the temp tree: a pytest `tmp_path` lives
    *under* `gettempdir()`, so it could not tell a grant of the root apart
    from a grant of the shared parent.
    """
    assert sandbox.build_profile is sandbox_macos.build_profile
    profile = sandbox.build_profile(["/opt/agentcad-roots-probe"])
    assert '(allow file-write* (subpath "/opt/agentcad-roots-probe"))' in profile
    assert os.path.realpath(tempfile.gettempdir()) not in profile
    assert profile.count("(allow file-write* (subpath ") == 1
    assert profile.startswith("(version 1)\n(deny default)")
    assert "(deny network*)" in profile


# ------------------------------------------------- the plan has to be released

def test_serve_stops_the_kernel_so_the_private_dirs_go(monkeypatch, tmp_path):
    """A plan owns a directory, so somebody has to own the plan. Every other
    command already stops its kernel in a `finally`; `agentcad serve` did not,
    and would have leaked one `agentcad-worker-*` dir per pool slot per run.
    """
    import uvicorn

    from agentcad import cli

    stopped: list[bool] = []
    service = SimpleNamespace(
        kernel=SimpleNamespace(stop=lambda: stopped.append(True)))
    monkeypatch.setenv("AGENTCAD_CONFIG", str(tmp_path / "cfg.json"))
    monkeypatch.delenv("AGENTCAD_MODE", raising=False)
    monkeypatch.setattr(cli, "_build_service", lambda *a, **k: service)
    monkeypatch.setattr(cli, "_make_chat_engine", lambda svc, reg: None)
    monkeypatch.setattr("agentcad.core.tools.build_registry", lambda svc: object())
    monkeypatch.setattr("agentcad.server.app.create_app",
                        lambda *a, **k: object())
    args = SimpleNamespace(host="127.0.0.1", port=8630,
                           projects_dir=str(tmp_path / "projects"), no_open=True)

    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: None)
    cli.cmd_serve(args, open_browser=False)
    assert stopped == [True]

    # ...and when the server dies of something, too (Ctrl-C reaches uvicorn's
    # handler, which re-raises SIGINT -> KeyboardInterrupt here).
    def _boom(app, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(uvicorn, "run", _boom)
    with pytest.raises(KeyboardInterrupt):
        cli.cmd_serve(args, open_browser=False)
    assert stopped == [True, True]


def test_serve_survives_the_sigterm_uvicorn_re_raises(monkeypatch, tmp_path):
    """`docker stop` is the case a plain `finally` cannot catch: uvicorn shuts
    down gracefully, restores the **previous** SIGTERM handler and re-raises
    the signal, so with the default handler the process dies inside
    `uvicorn.run` (measured: exit 143, no cleanup). `cmd_serve` installs the
    handler uvicorn restores, and puts it back afterwards.
    """
    import signal
    import uvicorn

    from agentcad import cli

    stopped: list[bool] = []
    service = SimpleNamespace(
        kernel=SimpleNamespace(stop=lambda: stopped.append(True)))
    monkeypatch.setenv("AGENTCAD_CONFIG", str(tmp_path / "cfg.json"))
    monkeypatch.delenv("AGENTCAD_MODE", raising=False)
    monkeypatch.setattr(cli, "_build_service", lambda *a, **k: service)
    monkeypatch.setattr(cli, "_make_chat_engine", lambda svc, reg: None)
    monkeypatch.setattr("agentcad.core.tools.build_registry", lambda svc: object())
    monkeypatch.setattr("agentcad.server.app.create_app", lambda *a, **k: object())
    args = SimpleNamespace(host="127.0.0.1", port=8630,
                           projects_dir=str(tmp_path / "projects"), no_open=True)

    def _re_raised_sigterm(app, **kwargs):
        # What `uvicorn.server.capture_signals` does on the way out.
        signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)

    before = signal.getsignal(signal.SIGTERM)
    monkeypatch.setattr(uvicorn, "run", _re_raised_sigterm)
    with pytest.raises(SystemExit) as exit_info:
        cli.cmd_serve(args, open_browser=False)

    assert exit_info.value.code == 128 + signal.SIGTERM  # the conventional 143
    assert stopped == [True]
    assert signal.getsignal(signal.SIGTERM) is before


# ------------------------------------------------------------ the OCP-free set

_PROBE = '''
import importlib
import sys


class _Blocked:
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in ("OCP", "build123d"):
            raise ImportError("blocked kernel import: " + name)
        return None


sys.meta_path.insert(0, _Blocked())
mod = importlib.import_module({module!r})
assert {expr}, "smoke expression failed"
assert "OCP" not in sys.modules and "build123d" not in sys.modules
print("ok")
'''

#: module -> a smoke expression that must hold once it is imported. These run
#: in the **server** process (the client builds a plan before it spawns
#: anything), so an accidental geometry import would break the server, far
#: from its cause. Same probe pattern as `tests/test_toolkit_ocp_free.py`.
OCP_FREE = {
    "agentcad.kernel.quotas": 'mod.DEFAULTS["memory_mb"] == 2048',
    "agentcad.kernel.sandbox": 'mod.status() in ("active", "off", "unsupported")',
    "agentcad.kernel.sandbox_macos": 'mod.SANDBOX_EXEC.endswith("sandbox-exec")',
}


@pytest.mark.integration
@pytest.mark.portability
@pytest.mark.parametrize("module", sorted(OCP_FREE))
def test_module_imports_with_no_geometry_kernel_available(module):
    repo = Path(__file__).resolve().parents[1]
    probe = _PROBE.format(module=module, expr=OCP_FREE[module])
    proc = subprocess.run([sys.executable, "-c", probe], cwd=repo,
                          capture_output=True, text=True, timeout=180)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip().endswith("ok")


# --------------------------------------------------------- the macOS backend

darwin_only = pytest.mark.skipif(
    sys.platform != "darwin", reason="the seatbelt backend is macOS-only")


@darwin_only
def test_the_macos_plan_wraps_the_argv_and_grants_the_private_temp(isolated):
    plan = sandbox.plan(BASE_ARGV, [str(isolated)])
    try:
        assert plan.argv[:2] == [sandbox_macos.SANDBOX_EXEC, "-p"]
        assert plan.argv[3:] == BASE_ARGV
        profile = plan.argv[2]
        assert os.path.realpath(plan.tmp_dir) in profile
        assert plan.confinement == {"status": "active", "mechanism": "seatbelt",
                                    "detail": {"posture": "local"}}
        assert plan.quotas["mechanism"] == "rlimit+supervisor"
    finally:
        plan.release()


@darwin_only
def test_the_macos_plan_emits_the_rlimit_payload_for_the_preamble(isolated):
    """Slice 2's worker preamble reads `AGENTCAD_CONFINE`; this slice only
    emits it. `RLIMIT_NPROC` is per-uid, so a fixed number kills the worker at
    import — it is the live count at spawn plus the configured headroom."""
    live = sandbox_macos.live_uid_process_count()
    plan = sandbox.plan(BASE_ARGV, [str(isolated)], quotas={"pids_headroom": 7})
    try:
        payload = json.loads(plan.env["AGENTCAD_CONFINE"])
        soft, hard = payload["rlimits"]["RLIMIT_NPROC"]
        assert soft == hard and soft >= live  # live count + 7, live may drift
        assert set(payload) == {"rlimits"}    # no confinement keys on macOS
    finally:
        plan.release()


@darwin_only
def test_the_hosted_posture_is_named_as_unavailable_on_macos(isolated):
    plan = sandbox.plan(BASE_ARGV, [str(isolated)], posture="hosted")
    try:
        assert plan.posture == "local"  # what is actually in effect
        assert any("macOS" in w and "local" in w for w in plan.warnings)
    finally:
        plan.release()


@darwin_only
def test_live_uid_process_count_is_plausible():
    count = sandbox_macos.live_uid_process_count()
    assert isinstance(count, int)
    assert 1 < count < 8192


@darwin_only
def test_rss_bytes_reads_this_process(isolated):
    rss = sandbox_macos.MacBackend().rss_bytes(SimpleNamespace(pid=os.getpid()))
    assert isinstance(rss, int)
    assert 4 * 1024 * 1024 < rss < 8 * 1024 ** 3  # a CPython, not a fantasy


@darwin_only
def test_rss_bytes_of_a_dead_process_is_none():
    assert sandbox_macos.MacBackend().rss_bytes(SimpleNamespace(pid=0)) is None


@darwin_only
def test_explain_exit_names_the_cpu_cap_and_nothing_else():
    mac = sandbox_macos.MacBackend()
    assert mac.explain_exit(None, -signal.SIGXCPU) == {"reason": "cpu_cap",
                                                       "tier": "rlimit"}
    assert mac.explain_exit(None, -signal.SIGKILL) is None
    assert mac.explain_exit(None, 0) is None
