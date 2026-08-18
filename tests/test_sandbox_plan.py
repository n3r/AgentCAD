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
    def can_sample(self): return True
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


def test_plan_never_creates_a_writable_root_it_was_handed(isolated, backend):
    """`plan()` receives CALLER-supplied paths whose acceptance is decided
    elsewhere: `agentcad check --work-dir <project>/scratch` is refused by
    `CheckRunner._work_dir`, and "a refused path leaves nothing behind" is a
    promise with a test on it (`test_checks_cli.py`). Creating roots here would
    resurrect exactly that bug, one layer down — so the directory is granted as
    given and whoever owns it makes it (see the `_writable_roots` test below).
    """
    missing = isolated / "would-be-refused"
    plan = _plan([missing])
    try:
        assert not missing.exists(), "plan() created a caller-supplied root"
        assert os.path.realpath(str(missing)) in backend.calls[0].write_roots
    finally:
        plan.release()


def test_the_cli_creates_the_two_writable_roots_the_server_owns(monkeypatch,
                                                                tmp_path):
    """The other half: on a fresh install neither the projects dir (the service
    creates it AFTER `kernel.start()`) nor `~/.agentcad` need exist, and a
    Landlock rule on a missing path is ENOENT — the grant is lost, every write
    into it fails once it appears, and the failure downgrades a genuinely
    confined worker to `off`. `cli._writable_roots` owns both, so it makes both.
    """
    from agentcad import cli

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    projects = tmp_path / "projects-not-yet" / "nested"
    assert not projects.exists() and not (home / ".agentcad").exists()

    roots = cli._writable_roots(projects)

    assert projects.is_dir() and (home / ".agentcad").is_dir()
    assert str(projects) in roots and str(home / ".agentcad") in roots


def test_a_root_the_cli_cannot_create_warns_instead_of_crashing(monkeypatch,
                                                                 tmp_path,
                                                                 capsys):
    """A projects dir under a plain file cannot be made. The server still
    starts — with the roots that did work — and says why."""
    from agentcad import cli

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    blocker = tmp_path / "a-file"
    blocker.write_text("not a directory", encoding="utf-8")

    roots = cli._writable_roots(blocker / "under-a-file")

    assert str(blocker / "under-a-file") in roots      # granted, just absent
    assert "under-a-file" in capsys.readouterr().err


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


def test_a_failure_after_the_backend_was_built_releases_it_too(isolated,
                                                                backend,
                                                                monkeypatch):
    """Until a `SandboxPlan` exists nobody holds any of this: not the temp
    dir, not the job handle a Windows backend opened, not the cgroup a Linux
    one created. So "a plan() that raises leaves nothing behind" has to hold
    for every step, not just for a backend that blew up on the way in."""
    def _boom(*args, **kwargs):
        raise RuntimeError("no plan today")

    monkeypatch.setattr(sandbox, "SandboxPlan", _boom)
    before = set(Path(tempfile.gettempdir()).glob(sandbox.TMP_PREFIX + "*"))
    with pytest.raises(RuntimeError):
        _plan([isolated])
    assert backend.released == 1
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
        # Decision 8: the supervisor is platform-independent in its logic but
        # not in its one measurement, and this backend cannot take it — so the
        # caps are published and NO tier is named. An armed, blind sampler
        # would be a promise nothing can keep.
        assert plan.quotas == {"status": "off", "mechanism": None,
                               "limits": plan.quotas_obj.limits()}
        assert plan.backend.can_sample() is False
        # the null backend still answers the whole protocol, so the client and
        # the supervisor need no platform branches of their own
        assert plan.backend.rss_bytes(SimpleNamespace(pid=os.getpid())) is None
        assert plan.backend.explain_exit(None, -9) is None
        plan.backend.attach(None)
    finally:
        plan.release()


# --------------------------------------------------------- the Windows plan
#
# The job object is created and configured in the SERVER process, at plan
# time — which is what lets `quotas.mechanism` name `job_object` honestly
# (a mechanism string is a promise; at plan time the job either exists with
# its limits written, or the tier reports itself off). That also makes the
# shape assertable anywhere: the five Win32 entry points are module-level
# functions, so a macOS box can stub the boundary and drive the rest.

@pytest.fixture
def windows(monkeypatch):
    """The Windows backend with its Win32 calls stubbed, on any OS."""
    from agentcad.kernel import sandbox_windows

    calls = SimpleNamespace(created=0, info=[], assigned=[], closed=[],
                            working_set=321 * 1024 * 1024)

    def _create():
        calls.created += 1
        return 4242                                   # a plausible HANDLE

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sandbox_windows, "_job_create", _create)
    monkeypatch.setattr(sandbox_windows, "_set_information",
                        lambda job, klass, info: calls.info.append((job, klass, info)))
    monkeypatch.setattr(sandbox_windows, "_assign",
                        lambda job, handle: calls.assigned.append((job, handle)))
    monkeypatch.setattr(sandbox_windows, "_close_handle", calls.closed.append)
    monkeypatch.setattr(
        sandbox_windows, "_memory_counters",
        lambda handle: SimpleNamespace(WorkingSetSize=calls.working_set))
    return sandbox_windows, calls


def test_the_windows_plan_caps_with_a_job_object_and_confines_with_nothing(
        isolated, windows):
    """Decision 7: the quotas are real, the confinement is `unsupported` and
    says why. `off` would suggest a switch the operator could flip."""
    module, calls = windows
    plan = _plan([isolated], quotas={"memory_mb": 1024, "pids": 16})
    try:
        assert plan.argv == BASE_ARGV                # nothing wraps it
        # The one payload key: Windows has no rlimit for the worker to apply,
        # but a job object's MemoryError IS a cap being enforced, and
        # `denials.classify` never names a denial no worker reported.
        assert json.loads(plan.env["AGENTCAD_CONFINE"]) == {
            "posture": "local", "quotas": ["job_object"]}
        assert plan.confinement == {
            "status": "unsupported", "mechanism": None,
            "detail": {"posture": "local",
                       "note": "AppContainer confinement is PRD-006b"}}
        assert plan.quotas["status"] == "active"
        assert plan.quotas["mechanism"] == "job_object+supervisor"

        job, klass, info = calls.info[0]
        assert klass == module.JobObjectExtendedLimitInformation
        flags = info.BasicLimitInformation.LimitFlags
        assert flags & module.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        assert flags & module.JOB_OBJECT_LIMIT_PROCESS_MEMORY
        assert flags & module.JOB_OBJECT_LIMIT_ACTIVE_PROCESS
        assert info.ProcessMemoryLimit == 1024 * 1024 * 1024
        assert info.BasicLimitInformation.ActiveProcessLimit == 16
        # ...and the CPU rate control, a hard cap on a share of the MACHINE
        _job, klass, rate = calls.info[1]
        assert klass == module.JobObjectCpuRateControlInformation
        assert rate.ControlFlags == (module.JOB_OBJECT_CPU_RATE_CONTROL_ENABLE
                                     | module.JOB_OBJECT_CPU_RATE_CONTROL_HARD_CAP)
        assert 1 <= rate.CpuRate <= module.CPU_RATE_MAX

        # The process joins the job after Popen, never through a preexec_fn.
        plan.backend.attach(SimpleNamespace(pid=99, _handle=7))
        assert calls.assigned == [(job, 7)]
        assert plan.backend.rss_bytes(SimpleNamespace(_handle=7)) == 321 * 1024 * 1024
        assert plan.backend.explain_exit(None, -9) is None
    finally:
        plan.release()
    assert calls.closed == [job]     # KILL_ON_JOB_CLOSE takes survivors with it


def test_a_windows_job_object_that_cannot_be_made_is_not_claimed(isolated,
                                                                  windows,
                                                                  monkeypatch):
    """Decision 8, on the tier that needs no operator action at all: if the
    job object could not be created, `mechanism` must not say `job_object`."""
    module, _calls = windows

    def _refused():
        raise OSError(5, "Access is denied")

    monkeypatch.setattr(module, "_job_create", _refused)
    plan = _plan([isolated])
    try:
        assert plan.quotas["mechanism"] == "supervisor"
        assert plan.quotas["status"] == "active"      # the sampler still runs
        assert any("job-object" in warning for warning in plan.warnings)
        assert plan.backend.job is None
        # ...and with no job there is no cap to tell the worker about, so its
        # MemoryError stays what it is: the machine running out of memory.
        assert "AGENTCAD_CONFINE" not in plan.env
    finally:
        plan.release()


def test_the_windows_opt_out_stays_unsupported(isolated, windows, monkeypatch):
    """`AGENTCAD_NO_SANDBOX` opts out of a confinement. Windows has none, so
    there is nothing to opt out of and `off` would be a lie in the other
    direction — the operator would go looking for the switch."""
    monkeypatch.setenv("AGENTCAD_NO_SANDBOX", "1")
    plan = _plan([isolated])
    try:
        assert plan.confinement["status"] == "unsupported"
        assert plan.quotas["mechanism"] == "job_object+supervisor"
    finally:
        plan.release()


def test_an_unknown_platform_with_the_opt_out_is_still_unsupported(isolated,
                                                                   monkeypatch):
    monkeypatch.setattr(sys, "platform", "sunos5")
    monkeypatch.setenv("AGENTCAD_NO_SANDBOX", "1")
    plan = _plan([isolated])
    try:
        assert plan.confinement["status"] == "unsupported"
        assert "sunos5" in plan.confinement["detail"]["reason"]
    finally:
        plan.release()


# ----------------------------------------------------------- the Linux plan
#
# The payload the worker's preamble reads is decided in the SERVER process, so
# it can be asserted on any OS: `sys.platform` picks the backend, and the two
# functions that genuinely need Linux (the Landlock probe and the /proc walk)
# are stubbed. The real thing runs in `tests/test_sandbox_linux.py`, inside
# the shipped image.

@pytest.fixture
def linux(monkeypatch):
    """The Linux backend, made answerable on a macOS dev box."""
    from agentcad.kernel import sandbox_linux

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(sandbox_linux, "landlock_abi", lambda: 6)
    monkeypatch.setattr(sandbox_linux, "live_uid_process_count", lambda: 40)
    monkeypatch.setattr(sandbox_linux, "platform",
                        SimpleNamespace(machine=lambda: "aarch64"))
    return sandbox_linux


def test_the_linux_plan_confines_in_process_and_leaves_the_argv_alone(isolated,
                                                                      linux):
    """There is no wrapper binary on Linux: the worker restricts itself from
    the payload, which is the only way this works inside the shipped image
    (bwrap needs `unshare`, denied by Docker's default seccomp profile)."""
    plan = _plan([isolated])
    try:
        assert plan.argv == BASE_ARGV
        assert plan.confinement == {
            "status": "active", "mechanism": "landlock+seccomp",
            "detail": {"landlock_abi": 6, "posture": "local"}}
        payload = json.loads(plan.env["AGENTCAD_CONFINE"])
        assert set(payload) == {"posture", "rlimits", "landlock", "seccomp"}
        assert payload["posture"] == "local"
        assert payload["landlock"]["read_roots"] == ["/"]   # the local posture
        assert payload["landlock"]["write_roots"] == [
            os.path.realpath(str(isolated)), os.path.realpath(plan.tmp_dir)]
        assert payload["landlock"]["extra_files"] == [
            "/dev/null", "/proc/self/clear_refs"]
        assert payload["seccomp"] == {"server_pid": os.getpid()}
        assert plan.quotas["mechanism"] == "rlimit+supervisor"
    finally:
        plan.release()


def test_the_linux_rlimits_are_the_address_space_and_the_fork_budget(isolated,
                                                                     linux):
    plan = _plan([isolated], quotas={"memory_mb": 1024, "pids_headroom": 7})
    try:
        rlimits = json.loads(plan.env["AGENTCAD_CONFINE"])["rlimits"]
        # address_space_mb defaults to 3 x memory_mb, in bytes
        assert rlimits["RLIMIT_AS"] == [3 * 1024 * 1024 * 1024] * 2
        assert rlimits["RLIMIT_NPROC"] == [47, 47]      # 40 live + 7 headroom
    finally:
        plan.release()

    plan = _plan([isolated], quotas={"address_space_mb": "off",
                                     "pids_headroom": 0})
    try:
        assert json.loads(plan.env["AGENTCAD_CONFINE"])["rlimits"] == {}
        assert plan.quotas["mechanism"] == "supervisor"  # no rlimit tier left
    finally:
        plan.release()


def test_the_hosted_posture_narrows_reads_to_an_allow_list(isolated, linux):
    """FR5's cloud posture: a member's script may no longer read the state dir
    (and so may no longer forge a session), while everything a Python process
    needs to run is still readable."""
    plan = _plan([isolated], posture="hosted")
    try:
        from agentcad._resources import resource_root

        payload = json.loads(plan.env["AGENTCAD_CONFINE"])
        roots = payload["landlock"]["read_roots"]
        assert "/" not in roots
        assert "/usr" in roots and "/etc" in roots
        # `/proc` is on the allow-list but does not exist on a macOS dev box,
        # and a rule on a missing path is a failure in the worker's own
        # report — so the list is filtered to what is actually there.
        assert ("/proc" in roots) == os.path.isdir("/proc")
        assert sys.prefix in roots and str(resource_root()) in roots
        assert os.path.realpath(str(isolated)) in roots  # its own project
        assert plan.tmp_dir in roots or os.path.realpath(plan.tmp_dir) in roots
        assert all(os.path.exists(root) for root in roots)
        assert plan.posture == "hosted"
    finally:
        plan.release()


def test_opting_out_drops_the_confinement_keys_but_keeps_the_caps(isolated,
                                                                  linux,
                                                                  monkeypatch):
    monkeypatch.setenv("AGENTCAD_NO_SANDBOX", "1")
    plan = _plan([isolated])
    try:
        payload = json.loads(plan.env["AGENTCAD_CONFINE"])
        assert set(payload) == {"posture", "rlimits"}
        assert payload["rlimits"]["RLIMIT_NPROC"] == [104, 104]
        assert plan.confinement["status"] == "off"
        assert plan.quotas["status"] == "active"
    finally:
        plan.release()


@pytest.mark.parametrize("abi,machine,expected", [
    (0, "aarch64", "no Landlock"),
    (2, "x86_64", "ABI 2 < 3"),
    (6, "riscv64", "unknown machine"),
])
def test_a_kernel_or_machine_that_cannot_confine_says_so(isolated, linux,
                                                          abi, machine,
                                                          expected):
    """Decision 8: `unsupported` names its reason. The payload still ships —
    `landlock_apply` refuses below the ABI floor by itself, and seccomp is
    worth having on a kernel whose Landlock is too old."""
    linux.landlock_abi = lambda: abi
    linux.platform = SimpleNamespace(machine=lambda: machine)
    plan = _plan([isolated])
    try:
        assert plan.confinement["status"] == "unsupported"
        assert expected in plan.confinement["detail"]["reason"]
        assert any(expected in warning for warning in plan.warnings)
        assert "landlock" in json.loads(plan.env["AGENTCAD_CONFINE"])
    finally:
        plan.release()


# ------------------------------------------------------------ the cgroup tier
#
# The tier is Linux-only in effect but pure file I/O in code — mkdir, four
# writes and a read — so a fake delegated directory exercises all of it on any
# OS. What CANNOT be faked (the kernel actually OOM-killing the worker) is
# `tests/test_sandbox_linux.py::test_cgroup_tier_when_delegated`, which needs
# a real delegated cgroup and skips without one.

@pytest.fixture
def delegated(tmp_path, monkeypatch):
    """A directory shaped like a cgroup v2 subtree the operator delegated."""
    root = tmp_path / "cg"
    root.mkdir()
    (root / "cgroup.controllers").write_text("cpuset cpu io memory pids\n",
                                             encoding="utf-8")
    (root / "cgroup.subtree_control").write_text("memory pids cpu\n",
                                                 encoding="utf-8")
    monkeypatch.setenv(sandbox_linux_module().CGROUP_ENV, str(root))
    return root


def sandbox_linux_module():
    from agentcad.kernel import sandbox_linux

    return sandbox_linux


def test_a_delegated_cgroup_becomes_the_first_quota_tier(isolated, linux,
                                                          delegated):
    """Decision 4: opt-in by delegation. When the operator did the work, the
    worker gets a real kernel-enforced cap and health says which one."""
    plan = _plan([isolated], quotas={"memory_mb": 512, "pids": 16,
                                     "cpu_percent": 200})
    try:
        assert plan.quotas["mechanism"] == "cgroup+rlimit+supervisor"
        assert plan.warnings == []
        # The worker applies none of this, and is told about it anyway: a
        # `pids.max` breach arrives in the script as a `BlockingIOError`, and
        # `denials.classify` names nothing without a reported live cap.
        assert json.loads(plan.env["AGENTCAD_CONFINE"])["quotas"] == ["cgroup"]
        worker = plan.backend.cg_dir
        assert Path(worker).parent == delegated
        assert (Path(worker) / "memory.max").read_text(
            encoding="utf-8") == str(512 * 1024 * 1024)
        # Load-bearing: with swap at `max` the spike's 400 MB allocation under
        # a 200 MB cap swapped instead of dying.
        assert (Path(worker) / "memory.swap.max").read_text(
            encoding="utf-8") == "0"
        assert (Path(worker) / "pids.max").read_text(encoding="utf-8") == "16"
        assert (Path(worker) / "cpu.max").read_text(
            encoding="utf-8") == "200000 100000"

        # The parent places the pid after Popen — never a preexec_fn.
        plan.backend.attach(SimpleNamespace(pid=4321))
        assert (Path(worker) / "cgroup.procs").read_text(
            encoding="utf-8") == "4321"
    finally:
        plan.release()


def test_the_cgroup_names_the_oom_kill_that_the_return_code_cannot(isolated,
                                                                    linux,
                                                                    delegated):
    """A kernel OOM kill, the supervisor's kill and a timeout kill all leave
    `returncode == -9`. Only the counter delta tells them apart."""
    plan = _plan([isolated], quotas={"memory_mb": 512})
    try:
        worker = Path(plan.backend.cg_dir)
        (worker / "memory.events").write_text("low 0\nmax 39\noom_kill 0\n",
                                              encoding="utf-8")
        plan.backend.attach(SimpleNamespace(pid=4321))     # records oom_kill 0
        assert plan.backend.explain_exit(None, -9) is None

        (worker / "memory.events").write_text("low 0\nmax 39\noom_kill 1\n",
                                              encoding="utf-8")
        assert plan.backend.explain_exit(None, -9) == {"reason": "memory_cap",
                                                        "tier": "cgroup"}
    finally:
        plan.release()


def test_a_cgroup_dir_that_is_not_one_falls_back_and_says_so(isolated, linux,
                                                              tmp_path,
                                                              monkeypatch):
    """The operator asked for the tier and did not get it: that is worth a
    warning naming the directory. (A machine that delegated nothing at all is
    *not* — see the next test.)"""
    plain = tmp_path / "not-a-cgroup"
    plain.mkdir()
    monkeypatch.setenv(sandbox_linux_module().CGROUP_ENV, str(plain))
    plan = _plan([isolated])
    try:
        assert plan.quotas["mechanism"] == "rlimit+supervisor"
        assert plan.backend.cg_dir is None
        assert any(str(plain) in warning for warning in plan.warnings), plan.warnings
    finally:
        plan.release()


@pytest.mark.parametrize("value", [None, "off"])
def test_no_delegated_cgroup_is_the_normal_state_and_not_a_warning(isolated,
                                                                    linux,
                                                                    monkeypatch,
                                                                    value):
    """The shipped container and every developer box land here — unset. The
    tier is opt-in, so this is not a failure to report: warning on it would
    train an operator to ignore the field."""
    module = sandbox_linux_module()
    if value is None:
        monkeypatch.delenv(module.CGROUP_ENV, raising=False)
    else:
        monkeypatch.setenv(module.CGROUP_ENV, value)
    plan = _plan([isolated])
    try:
        assert plan.quotas["mechanism"] == "rlimit+supervisor"
        assert plan.warnings == []
    finally:
        plan.release()


posix_user_only = pytest.mark.skipif(
    os.name != "posix" or getattr(os, "geteuid", lambda: 0)() == 0,
    reason="the own-cgroup route is refused outright for root and off POSIX")


def test_the_own_cgroup_route_is_not_probed_unless_asked_for(monkeypatch):
    """Decision 4 is opt-in **by delegation**, and this is the route that can
    move the server's own pids — so with `AGENTCAD_CGROUP_DIR` unset nothing
    reads `/proc/self/cgroup` at all, let alone writes anything."""
    module = sandbox_linux_module()
    looked: list[int] = []
    monkeypatch.delenv(module.CGROUP_ENV, raising=False)
    monkeypatch.setattr(module, "_own_cgroup_path",
                        lambda: looked.append(1) or "/delegated")
    warnings: list[str] = []
    assert module.CgroupTier.probe(warnings) is None
    assert looked == [] and warnings == []


@posix_user_only
def test_the_root_cgroup_is_never_taken_for_a_delegated_one(monkeypatch):
    """`0::/` is a CI runner and a developer laptop, not a delegated subtree.
    Enabling controllers or moving pids around up there is a machine-wide
    change nobody asked for — and the refusal is reported, not swallowed."""
    module = sandbox_linux_module()
    monkeypatch.setenv(module.CGROUP_ENV, module.CGROUP_AUTO)
    monkeypatch.setattr(module, "_own_cgroup_path", lambda: "/")
    warnings: list[str] = []
    assert module.CgroupTier.probe(warnings) is None
    assert any("root cgroup" in warning for warning in warnings), warnings


@posix_user_only
def test_an_undelegated_own_cgroup_is_refused_before_anything_is_touched(
        monkeypatch, tmp_path):
    """The dangerous half of `=auto`: a cgroup we merely *sit in* is not one
    that was given to us. Everything is checked first — ownership, write
    access, the controllers — so a refusal leaves the machine exactly as it
    was: no `server` leaf, no migrated pids, no `subtree_control` write.
    """
    module = sandbox_linux_module()
    root = tmp_path / "sys"
    own = root / "shared.slice"
    own.mkdir(parents=True)
    procs = f"{os.getpid()}\n4242\n"
    (own / "cgroup.procs").write_text(procs, encoding="utf-8")
    # A shared slice: no memory/pids to delegate.
    (own / "cgroup.controllers").write_text("cpuset io\n", encoding="utf-8")
    (own / "cgroup.subtree_control").write_text("\n", encoding="utf-8")

    monkeypatch.setenv(module.CGROUP_ENV, module.CGROUP_AUTO)
    monkeypatch.setattr(module, "CGROUP_ROOT", str(root))
    monkeypatch.setattr(module, "_own_cgroup_path", lambda: "/shared.slice")
    warnings: list[str] = []

    assert module.CgroupTier.probe(warnings) is None
    assert any("memory" in warning and str(own) in warning
               for warning in warnings), warnings
    # ...and nothing survived the refusal.
    assert not (own / "server").exists()
    assert (own / "cgroup.procs").read_text(encoding="utf-8") == procs
    assert (own / "cgroup.subtree_control").read_text(encoding="utf-8") == "\n"
    assert sorted(entry.name for entry in own.iterdir()) == [
        "cgroup.controllers", "cgroup.procs", "cgroup.subtree_control"]


@posix_user_only
def test_an_own_cgroup_owned_by_somebody_else_is_refused(monkeypatch):
    """Delegation means the subtree was handed to *us*; a directory owned by
    another uid was not. (Root is refused before this check even runs: it
    passes `W_OK` on every cgroup on the machine, which is activation by
    capability — the thing Decision 4 rules out.)

    `/tmp` stands in for the cgroup: a real directory, really owned by root,
    so the refusal is measured rather than mocked.
    """
    module = sandbox_linux_module()
    if os.stat("/tmp").st_uid == os.geteuid():
        pytest.skip("/tmp belongs to this user here; nothing to prove")
    monkeypatch.setenv(module.CGROUP_ENV, module.CGROUP_AUTO)
    monkeypatch.setattr(module, "CGROUP_ROOT", "/")
    monkeypatch.setattr(module, "_own_cgroup_path", lambda: "/tmp")
    warnings: list[str] = []

    assert module.CgroupTier.probe(warnings) is None
    assert any("was not delegated to us" in warning
               for warning in warnings), warnings


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


# ------------------------------------------------------------ the health object

def test_report_answers_for_a_client_that_has_no_plan_at_all():
    """`KernelClient()` — the historical form, and the test suite's session
    fixture. There is nothing confining it and nothing capping it, and health
    has to say that without raising on the missing plan."""
    from agentcad.kernel.client import KernelClient

    body = sandbox.report(KernelClient())
    assert body["status"] in ("off", "unsupported")
    assert body["mechanism"] is None
    assert body["posture"] == "local"
    assert body["confinement"] == {"status": body["status"], "mechanism": None,
                                   "detail": {}}
    assert body["quotas"] == {"status": "off", "mechanism": None, "limits": {}}
    assert body["warnings"] == []


def test_report_keeps_the_intent_until_a_worker_has_answered(isolated, backend):
    """A plan that has not spawned anything yet has no report to disagree with.
    Reporting `off` there would make a starting server look broken."""
    plan = _plan([isolated])
    try:
        body = sandbox.report(SimpleNamespace(_plan=plan, sandbox_report=None,
                                              sandboxed=True))
        assert body["status"] == "active"
        assert body["mechanism"] == "fake"
        assert body["quotas"] == plan.quotas
        assert body["warnings"] == ["a backend warning"]
    finally:
        plan.release()


def test_report_downgrades_when_the_worker_says_the_preamble_failed(isolated,
                                                                     backend):
    """Decision 8, the whole point: `active` is never claimed from intent. The
    failure travels into `warnings`, and the mechanism goes with the claim —
    naming one beside `off` would say something is still in force."""
    plan = _plan([isolated])
    try:
        live = {"landlock_abi": 0, "seccomp": None,
                "failures": [{"stage": "landlock", "error": "EOPNOTSUPP"}]}
        body = sandbox.report(SimpleNamespace(_plan=plan, sandbox_report=live,
                                              sandboxed=False))
        assert body["status"] == "off"
        assert body["mechanism"] is None
        assert body["confinement"]["detail"]["landlock_abi"] == 0
        assert any("landlock" in w and "EOPNOTSUPP" in w
                   for w in body["warnings"]), body["warnings"]
    finally:
        plan.release()


def test_report_keeps_active_when_only_a_quota_stage_failed(isolated, backend):
    """A refused rlimit is a cap that did not apply — it belongs in warnings,
    and it says nothing at all about Landlock and seccomp. Letting it clear the
    confinement would understate it as badly as overstating it does."""
    plan = _plan([isolated])
    try:
        live = {"landlock_abi": 6, "seccomp": "seccomp(2)",
                "rlimits": ["RLIMIT_NPROC"],
                "failures": [{"stage": "rlimit", "error": "EINVAL"}]}
        body = sandbox.report(SimpleNamespace(_plan=plan, sandbox_report=live,
                                              sandboxed=True))
        assert body["status"] == "active"
        assert body["confinement"]["detail"]["seccomp"] == "seccomp(2)"
        assert body["confinement"]["detail"]["rlimits"] == ["RLIMIT_NPROC"]
        assert any("rlimit" in w and "EINVAL" in w for w in body["warnings"])
    finally:
        plan.release()


def test_report_reads_a_pool_through_its_plan_property(isolated, backend):
    """A pool has no `_plan` of its own; worker 0 speaks for all of them,
    because they are constructed identically."""
    plan = _plan([isolated])
    try:
        pool = SimpleNamespace(plan=plan, sandbox_report={},
                               sandboxed=True)
        assert sandbox.report(pool)["quotas"] == plan.quotas
        assert sandbox.report(pool)["posture"] == plan.posture
    finally:
        plan.release()


def test_report_surfaces_a_backend_failure_that_happened_after_the_plan(
        isolated, backend):
    """A cgroup that refused the pid, a job object that refused the assignment:
    both happen at `attach()`, long after `plan.warnings` was copied."""
    plan = _plan([isolated])
    try:
        plan.backend.warnings.append("the cgroup quota tier is off: EPERM")
        warnings = sandbox.report(SimpleNamespace(_plan=plan))["warnings"]
        assert warnings == ["a backend warning",
                            "the cgroup quota tier is off: EPERM"]
    finally:
        plan.release()


# ------------------------------------------------- the unchanged public names

def test_supported_answers_for_the_platform_not_for_a_worker(monkeypatch):
    """The legacy strings (`/api/health`'s `sandbox`, `agentcad check`'s
    environment block) are a *capability*: could a new worker be confined here.
    Linux answers yes since the worker confines itself — what a RUNNING worker
    actually got is `report()`, from its own ping."""
    from agentcad.kernel import _confine

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(sandbox.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(_confine, "landlock_abi", lambda: 6)
    assert sandbox.supported() is True
    monkeypatch.setattr(_confine, "landlock_abi", lambda: 2)
    assert sandbox.supported() is False       # below the TRUNCATE floor
    monkeypatch.setattr(_confine, "landlock_abi", lambda: 6)
    monkeypatch.setattr(sandbox.platform, "machine", lambda: "riscv64")
    assert sandbox.supported() is False       # no seccomp syscall table
    monkeypatch.setattr(sys, "platform", "win32")
    assert sandbox.supported() is False       # Decision 7: PRD-006b


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
    "agentcad.kernel.sandbox_linux": 'mod.HOSTED_READ_ROOTS[0] == "/usr"',
    # Imported on every OS by `sandbox.plan` only on Windows — but the ctypes
    # structures are defined at import time, so this also proves they are
    # portable enough to be *read* anywhere (which is what lets the plan-shape
    # test above run on a macOS dev box).
    "agentcad.kernel.sandbox_windows":
        "mod.JobObjectExtendedLimitInformation == 9 "
        "and mod.JOBOBJECT_EXTENDED_LIMIT_INFORMATION().ProcessMemoryLimit == 0",
    # The three the WORKER imports before build123d — if one of them ever
    # imported a geometry kernel, the preamble would confine a process that
    # had already loaded 500 MB of OCCT, which is the opposite of the point.
    "agentcad.kernel._confine":
        "mod.LANDLOCK_ABI_MASK[3] & mod.FS_TRUNCATE and mod.landlock_abi() >= 0",
    "agentcad.kernel._preamble": 'mod.ENV == "AGENTCAD_CONFINE" and mod.REPORT == {}',
    "agentcad.kernel._meter": 'mod.Meter().finish()["wall_ms"] >= 0',
    "agentcad.kernel.denials":
        'mod.classify("MemoryError", "", active=True) == "memory"',
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


@pytest.mark.integration
@pytest.mark.portability
def test_the_preamble_records_a_quota_tier_it_did_not_apply_itself():
    """The Windows half of Decision 9, assertable anywhere.

    A job object is installed by the *parent*, so the worker applies nothing —
    and yet a breach lands in the script as a bare `MemoryError`, which
    `denials.classify` refuses to name unless this worker reported a live cap.
    Driven in a subprocess because the preamble is deliberately once-per-process
    (`_APPLIED`), and applying it inside pytest would leave every later test
    running against a worker that thinks it is confined.
    """
    repo = Path(__file__).resolve().parents[1]
    probe = ("import json\n"
             "from agentcad.kernel import _preamble\n"
             "print(json.dumps(_preamble.apply_from_env()))\n")
    payload = json.dumps({"posture": "local", "quotas": ["job_object"]})
    proc = subprocess.run(
        [sys.executable, "-c", probe], cwd=repo, capture_output=True,
        text=True, timeout=180, env={**os.environ, "AGENTCAD_CONFINE": payload})
    assert proc.returncode == 0, proc.stderr
    report = json.loads(proc.stdout)
    assert report["quotas"] == ["job_object"]
    assert report["rlimits"] == [] and report["failures"] == []
    assert "quotas=job_object" in proc.stderr


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
