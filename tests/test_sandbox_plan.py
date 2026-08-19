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
import queue
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
              confine=True, pool_size=1):
        self.calls.append(SimpleNamespace(
            argv=list(argv), write_roots=list(write_roots), quotas=quotas,
            posture=posture, server_pid=server_pid, confine=confine,
            pool_size=pool_size))
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


def _no_popen(*args, **kwargs):
    """A `subprocess.Popen` that must never be reached: on Windows it would be
    an **unconfined** worker, which is the one thing the spawn hook exists to
    prevent."""
    raise AssertionError("the client reached subprocess.Popen for a worker the "
                         "backend had already spawned")


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


def test_the_cli_creates_the_projects_root_it_owns_and_grants_no_home(
        monkeypatch, tmp_path):
    """The other half: on a fresh install the projects dir need not exist (the
    service creates it AFTER `kernel.start()`), and a Landlock rule on a
    missing path is ENOENT — the grant is lost and every write into it fails
    once it appears. `cli._writable_roots` owns that directory, so it makes it.

    `~/.agentcad` is **not** a write root wholesale (review I5). Nothing in
    `agentcad/kernel/` or `agentcad/toolkit/` reads or writes the config dir,
    every `load_config()` caller is server-side, and the worker's HOME is its
    private temp dir — so a blanket grant bought nothing and cost the sentence
    the docs want to be able to say: a part script can write nothing under the
    server user's home. The config file carries index definitions and the
    quota knobs; a script that could rewrite it could raise its own caps.

    The one exception, carved out for PRD-007's shared-pool variant builds
    (merged from main), is `<state-dir>/publications/build` — narrow enough
    that a script gains no more than the ability to write its own
    already-public variant mesh, and everything else under `~/.agentcad`
    (`config.json`, `secret.key`, `auth/`) stays ungranted and unmade.
    """
    from agentcad import cli
    from agentcad.core.appmode import state_dir

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    projects = tmp_path / "projects-not-yet" / "nested"
    assert not projects.exists()

    roots = cli._writable_roots(projects)

    assert projects.is_dir()
    assert str(projects) in roots
    build_root = str(state_dir() / "publications" / "build")
    assert build_root in roots
    assert Path(build_root).is_dir()
    assert str(home / ".agentcad") not in roots
    assert str(state_dir()) not in roots
    # Nothing under home is granted except that one publications/build
    # subtree — no blanket `~/.agentcad` grant reappeared.
    assert not any(
        root != build_root and
        (str(home) == root or root.startswith(f"{home}{os.sep}"))
        for root in roots), roots
    # ...and nothing beside that one subtree is created behind the
    # operator's back either.
    assert not (state_dir() / "secret.key").exists()
    assert not (state_dir() / "auth").exists()
    assert not (home / ".agentcad" / "config.json").exists()


def test_writable_roots_grants_the_publications_build_subtree(monkeypatch,
                                                               tmp_path):
    """PRD-007 merge: share-link/customizer variant builds go through the
    SHARED kernel pool into `PublicationStore.build_root()`
    (`core/share_build.py`'s `self._store.build_root()`), which is exactly
    `appmode.state_dir() / "publications" / "build"`. PRD-006 narrowed the
    worker's write roots to exclude the state dir wholesale, so that one
    subtree has to be granted explicitly or every variant build would fail
    with a `PermissionError` under the seatbelt/Landlock confinement.
    """
    from agentcad import cli
    from agentcad.core.appmode import state_dir

    state = tmp_path / "state-elsewhere"
    monkeypatch.setenv("AGENTCAD_STATE_DIR", str(state))
    projects = tmp_path / "projects"

    roots = cli._writable_roots(projects)

    build_root = str(state_dir() / "publications" / "build")
    assert build_root == str(state / "publications" / "build")
    assert build_root in roots
    assert Path(build_root).is_dir()
    assert str(state_dir()) not in roots
    assert str(Path.home() / ".agentcad") not in roots


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
    private_tmp = isolated / "private-tmp"   # not the shared temp: xdist siblings race there
    private_tmp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(private_tmp))
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
    # A private temp root: under xdist a sibling test process creating or
    # releasing its own `agentcad-worker-*` dir in the shared temp between the
    # two globs made this a race, not a measurement.
    private_tmp = isolated / "private-tmp"
    private_tmp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(private_tmp))
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
    """The Windows backend with its Win32 calls stubbed, on any OS.

    Both halves are stubbed, and the AppContainer one has to be even when
    these tests run on Windows CI: a live `CreateAppContainerProfile` would
    leave a real profile behind and a live `icacls` would rewrite the ACLs of
    `sys.prefix` on the runner.
    """
    from agentcad.kernel import sandbox_windows

    calls = SimpleNamespace(created=0, info=[], assigned=[], closed=[],
                            working_set=321 * 1024 * 1024,
                            # The job's process list, and a working set per
                            # OPENED handle: a venv `python.exe` is a launcher
                            # and the Popen handle is its stub, so these two
                            # are what a real sample reads. Empty by default —
                            # the query failing is the fallback path.
                            job_pids=[], working_sets={},
                            # -- the AppContainer half
                            api=True,               # userenv has the symbol
                            icacls="C:\\Windows\\System32\\icacls.exe",
                            profile_hr=0,           # S_OK from CreateAppContainerProfile
                            derived=0,              # ...and how often we fell back
                            sid=0x5100,             # a plausible PSID
                            derived_sid=0x5100,     # ...from the derive path
                            sid_str="S-1-15-2-1-2-3-4",
                            grants=[],              # (path, sid, rights)
                            grant_fails={},         # path -> the icacls tail
                            trees=[], spawned=[])

    def _create():
        calls.created += 1
        return 4242                                   # a plausible HANDLE

    def _create_profile(name, display, description):
        return calls.profile_hr, calls.sid

    def _derive_sid(name):
        calls.derived += 1
        return 0, calls.derived_sid

    def _grant(path, sid_str, rights):
        calls.grants.append((path, sid_str, rights))
        tail = calls.grant_fails.get(path)
        return tail is None, tail or "Successfully processed 1 files"

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sandbox_windows, "_userenv_symbol",
                        lambda name: calls.api)
    monkeypatch.setattr(sandbox_windows, "_icacls", lambda: calls.icacls)
    monkeypatch.setattr(sandbox_windows, "_userenv_create_profile",
                        _create_profile)
    monkeypatch.setattr(sandbox_windows, "_userenv_derive_sid", _derive_sid)
    monkeypatch.setattr(sandbox_windows, "_sid_to_string",
                        lambda psid: calls.sid_str)
    monkeypatch.setattr(sandbox_windows, "acl_grant", _grant)
    monkeypatch.setattr(sandbox_windows, "make_package_tree",
                        lambda tmp_dir, name: calls.trees.append((tmp_dir, name)))
    monkeypatch.setattr(sandbox_windows, "_job_create", _create)
    monkeypatch.setattr(sandbox_windows, "_set_information",
                        lambda job, klass, info: calls.info.append((job, klass, info)))
    monkeypatch.setattr(sandbox_windows, "_assign",
                        lambda job, handle: calls.assigned.append((job, handle)))
    monkeypatch.setattr(sandbox_windows, "_close_handle", calls.closed.append)
    monkeypatch.setattr(sandbox_windows, "_job_process_ids",
                        lambda job: list(calls.job_pids))
    monkeypatch.setattr(sandbox_windows, "_open_process",
                        lambda pid: 1000 + pid)       # a handle per pid
    monkeypatch.setattr(
        sandbox_windows, "_memory_counters",
        lambda handle: SimpleNamespace(
            WorkingSetSize=calls.working_sets.get(handle, calls.working_set)))
    return sandbox_windows, calls


def test_the_windows_plan_caps_with_a_job_object_and_confines_with_an_appcontainer(
        isolated, windows):
    """PRD-006b: the quotas are a job object, the confinement is a package SID.

    The argv is still untouched — a lowbox token is not a wrapper — and the
    `active` here is an **intent**: the worker's own `TokenIsAppContainer`
    is what keeps it (Decision 3), which is why the payload declares the two
    facets the parent really applied.
    """
    module, calls = windows
    plan = _plan([isolated], quotas={"memory_mb": 1024, "pids": 16})
    try:
        assert plan.argv == BASE_ARGV                # nothing wraps it
        # `quotas`: Windows has no rlimit for the worker to apply, but a job
        # object's MemoryError IS a cap being enforced, and `denials.classify`
        # never names a denial no worker reported. `confinement`: the facets
        # the parent applied before the worker ran, the macOS precedent.
        # `appcontainer`: the SID the worker checks its own token against.
        assert json.loads(plan.env["AGENTCAD_CONFINE"]) == {
            "posture": "local", "quotas": ["job_object"],
            "confinement": ["filesystem", "network"],
            "appcontainer": {"sid": calls.sid_str,
                             "name": module.profile_name()}}
        assert plan.confinement == {
            "status": "active", "mechanism": "appcontainer",
            "detail": {"posture": "local", "sid": calls.sid_str}}
        assert plan.posture == "local"

        # Reads before writes, and the write roots are the plan's own — the
        # projects dir it was given, and the private temp dir it made.
        reads = [(path, rights) for path, sid, rights in calls.grants
                 if rights == module.READ_RIGHTS]
        writes = [(path, rights) for path, sid, rights in calls.grants
                  if rights == module.WRITE_RIGHTS]
        assert [path for path, _ in reads] == module._read_roots()
        assert [path for path, _ in writes] == [os.path.realpath(str(isolated)),
                                                os.path.realpath(plan.tmp_dir)]
        assert {sid for _p, sid, _r in calls.grants} == {calls.sid_str}
        assert calls.grants and len(calls.grants) == len(reads) + len(writes)

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


def test_a_windows_sample_measures_the_job_not_the_launcher_stub(isolated,
                                                                 windows):
    """The bug Windows CI found (changelog 0238): a venv `python.exe` is a
    *launcher* that starts the real interpreter as a CHILD. The child inherits
    the job — which is why the commit limit bites and a balloon still gets its
    `MemoryError` — but `GetProcessMemoryInfo` on the `Popen` handle measures
    the stub, and answered ~3.9 MB for a worker with build123d imported. So the
    sample walks the job's process list and takes the **largest** working set:
    the max, never the sum, because the two share their mapped pages."""
    _module, calls = windows
    plan = _plan([isolated], quotas={"memory_mb": 1024})
    try:
        plan.backend.attach(SimpleNamespace(pid=99, _handle=7))
        calls.job_pids = [1, 2]
        calls.working_sets = {1001: 4 * 1024 * 1024,      # the launcher stub
                              1002: 480 * 1024 * 1024}    # the interpreter
        assert plan.backend.rss_bytes(SimpleNamespace(_handle=7)) == \
            480 * 1024 * 1024
        assert calls.closed == [1001, 1002]   # every opened handle, every sample

        # A pid that exits between the query and the open is skipped, not fatal
        # ...and a job with nothing in it falls back to the Popen handle, which
        # under-reports but is never `None` — a sampler that always answered
        # `None` would enforce nothing at all.
        calls.job_pids = []
        assert plan.backend.rss_bytes(SimpleNamespace(_handle=7)) == \
            calls.working_set
        assert plan.backend.rss_bytes(SimpleNamespace(_handle=None)) is None
    finally:
        plan.release()


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
        # MemoryError stays what it is: the machine running out of memory. The
        # payload is still written — the AppContainer is a separate promise,
        # and the worker has a token to check — but it names no quota tier.
        assert "quotas" not in json.loads(plan.env["AGENTCAD_CONFINE"])
        assert json.loads(plan.env["AGENTCAD_CONFINE"])["confinement"] == [
            "filesystem", "network"]
    finally:
        plan.release()


def test_a_windows_profile_that_already_exists_has_its_sid_derived(isolated,
                                                                    windows):
    """Decision 2: the profile outlives the process that made it (and the
    reboot), so `ERROR_ALREADY_EXISTS` is the *normal* answer from the second
    run onwards — and the plan it produces is identical."""
    module, calls = windows
    calls.profile_hr = module.HRESULT_ALREADY_EXISTS
    calls.sid = 0                       # nothing came back from the create
    plan = _plan([isolated])
    try:
        assert calls.derived == 1
        assert plan.confinement["status"] == "active"
        assert plan.confinement["detail"]["sid"] == calls.sid_str
    finally:
        plan.release()


def test_a_windows_acl_grant_that_fails_leaves_the_confinement_off(isolated,
                                                                    windows):
    """Decision 8 on the step most likely to fail on a real machine.

    A root the container cannot reach is a *confinement we cannot vouch for*:
    the worker would either be unable to run (a read root) or able to write
    where it should not (a write root). So it is `off`, the warning names the
    step **and the path**, and — the half that matters — the payload carries
    no facets, so nothing downstream labels an ordinary `EACCES` a denial.
    """
    module, calls = windows
    calls.grant_fails = {os.path.realpath(str(isolated)):
                         "Access is denied. Successfully processed 0 files"}
    plan = _plan([isolated], quotas={"memory_mb": 1024})
    try:
        assert plan.confinement["status"] == "off"
        assert plan.confinement["mechanism"] is None
        assert str(isolated) in plan.confinement["detail"]["reason"]
        warning = next(w for w in plan.warnings if "AppContainer" in w)
        assert os.path.realpath(str(isolated)) in warning
        assert "Access is denied" in warning
        assert json.loads(plan.env["AGENTCAD_CONFINE"]) == {
            "posture": "local", "quotas": ["job_object"]}
        # ...and the quotas are untouched: a confinement that could not be
        # prepared is not a reason to stop capping the machine.
        assert plan.quotas["mechanism"] == "job_object+supervisor"
        # Nothing to spawn into, so the client falls back to `Popen`.
        assert plan.backend.profile is None
        assert plan.backend.spawn(BASE_ARGV, {}) is None
    finally:
        plan.release()


def test_a_windows_write_root_that_does_not_exist_costs_one_grant(isolated,
                                                                   windows):
    """The `landlock_root` precedent (review I2): a root that is not there
    costs its grant, not the confinement. The container landed and is
    *narrower* than intended — saying `off` would be the overstatement in
    reverse — and the write it was meant to permit really will be denied, so
    it is a warning."""
    module, calls = windows
    missing = str(isolated / "not-created-yet")
    plan = sandbox.plan(BASE_ARGV, [str(isolated), missing])
    try:
        assert plan.confinement["status"] == "active"
        assert os.path.realpath(missing) not in [p for p, _s, _r in calls.grants]
        assert any("does not exist" in w and "not-created-yet" in w
                   for w in plan.warnings), plan.warnings
    finally:
        plan.release()


def test_a_windows_profile_that_cannot_be_made_is_off_with_the_hresult(
        isolated, windows):
    module, calls = windows
    calls.profile_hr = 0x80070005                     # E_ACCESSDENIED
    calls.sid = 0
    plan = _plan([isolated])
    try:
        assert plan.confinement["status"] == "off"
        assert "0x80070005" in plan.confinement["detail"]["reason"]
        assert any("0x80070005" in warning for warning in plan.warnings)
        assert calls.grants == []          # nothing was granted to nobody
    finally:
        plan.release()


def test_windows_without_the_appcontainer_api_is_unsupported(isolated, windows):
    """Below Windows 8 there is no `CreateAppContainerProfile` (and a machine
    with no `icacls` cannot grant the SID a path). That is `unsupported`, not
    `off`: there is no switch for the operator to look for — and the payload
    goes back to the quota-only one PRD-006 shipped."""
    module, calls = windows
    calls.api = False
    plan = _plan([isolated], quotas={"memory_mb": 1024})
    try:
        assert module.supported() is False
        assert plan.confinement["status"] == "unsupported"
        assert plan.confinement["mechanism"] is None
        assert "Windows 8" in plan.confinement["detail"]["reason"]
        assert json.loads(plan.env["AGENTCAD_CONFINE"]) == {
            "posture": "local", "quotas": ["job_object"]}
        assert plan.quotas["mechanism"] == "job_object+supervisor"
    finally:
        plan.release()
    calls.api, calls.icacls = True, None
    assert module.supported() is False                # no icacls, no grants


def test_the_windows_opt_out_is_off_and_keeps_the_quotas(isolated, windows,
                                                          monkeypatch):
    """`AGENTCAD_NO_SANDBOX` opts out of the **confinement**. The job object
    stays: a runaway script may not take the machine down whether or not the
    operator trusts it with the filesystem."""
    monkeypatch.setenv("AGENTCAD_NO_SANDBOX", "1")
    _module, calls = windows
    plan = _plan([isolated], quotas={"memory_mb": 1024})
    try:
        assert plan.confinement == {"status": "off", "mechanism": None,
                                    "detail": {"reason": "AGENTCAD_NO_SANDBOX"}}
        assert plan.quotas["mechanism"] == "job_object+supervisor"
        assert plan.quotas["limits"]["memory_mb"] == 1024
        # No profile, no ACLs, no facets: nothing was confined and nothing
        # says it was.
        assert calls.grants == [] and plan.backend.profile is None
        assert json.loads(plan.env["AGENTCAD_CONFINE"]) == {
            "posture": "local", "quotas": ["job_object"]}
    finally:
        plan.release()


def test_the_windows_backend_spawns_the_worker_itself_and_skips_the_attach(
        isolated, windows, monkeypatch):
    """Decision 1 and Decision 4. `subprocess` cannot pass a lowbox token, so
    the backend spawns; and because that spawn assigns the job while the
    process is still **suspended**, `attach()` has nothing left to do — the
    006 race where a worker ran its first milliseconds unquotaed is closed."""
    module, calls = windows

    class _Confined:
        def __init__(self, argv, env, *, sid, job=None, cwd=None):
            calls.spawned.append(SimpleNamespace(argv=list(argv), env=env,
                                                 sid=sid, job=job))
            self.pid, self._handle, self.job_assigned = 99, 7, True

    monkeypatch.setattr(module, "ConfinedProcess", _Confined)
    plan = _plan([isolated], quotas={"memory_mb": 1024})
    try:
        proc = plan.backend.spawn(plan.argv, {"TEMP": plan.tmp_dir})
        assert isinstance(proc, _Confined)
        assert calls.spawned[0].argv == BASE_ARGV
        assert calls.spawned[0].sid == calls.sid          # the PSID, not its text
        assert calls.spawned[0].job == plan.backend.job
        plan.backend.attach(proc)
        assert calls.assigned == []                       # already in the job
        assert plan.backend.attached is True              # ...and sampling knows
    finally:
        plan.release()


def test_a_windows_spawn_that_fails_falls_back_and_says_so(isolated, windows,
                                                            monkeypatch):
    """A spawn that raised must not take the server down with it: the worker
    starts the ordinary way, the warning says it is NOT confined, and the
    worker's own report (which will say `appcontainer: false`) is what turns
    health from `active` to `off`."""
    module, _calls = windows

    def _boom(*args, **kwargs):
        raise OSError("CreateProcessW failed: WinError 5: Access is denied")

    monkeypatch.setattr(module, "ConfinedProcess", _boom)
    plan = _plan([isolated])
    try:
        assert plan.backend.spawn(plan.argv, {}) is None
        warning = next(w for w in plan.backend.warnings if "spawn" in w)
        assert "NOT confined" in warning and "WinError 5" in warning
    finally:
        plan.release()


def test_prepare_tmp_makes_the_package_tree_and_regrants_the_private_dir(
        isolated, windows):
    """Round 1's finding, in the product: the lowbox token redirects `%TEMP%`
    into `%LOCALAPPDATA%\\Packages\\<name>\\AC\\Temp`, the plan points
    `LOCALAPPDATA` at the private temp dir, and **nothing else creates that
    path** — so the first `tempfile` call inside the container raised
    `FileNotFoundError`. Both the tree and the ACE live *in* the directory, so
    both are redone at every spawn: `stop()` removes it."""
    _module, calls = windows
    plan = _plan([isolated])
    try:
        assert plan.env["LOCALAPPDATA"] == plan.tmp_dir
        assert plan.env["TEMP"] == plan.env["TMP"] == plan.tmp_dir
        assert plan.env["USERPROFILE"] == plan.env["APPDATA"] == plan.tmp_dir
        before = len(calls.grants)
        calls.trees.clear()
        assert plan.prepare_tmp() == plan.tmp_dir
        assert calls.trees == [(plan.tmp_dir, plan.backend.profile.name)]
        assert calls.grants[before:] == [(plan.tmp_dir, calls.sid_str,
                                          "(OI)(CI)M")]
    finally:
        plan.release()


class _EchoProc:
    """Just enough of the `Popen` surface for `_ensure_started`'s first ping.

    It answers whatever request it is written with the sandbox report it was
    built with, which is what lets the client's spawn hook be exercised
    end-to-end without a Windows box.
    """

    def __init__(self, sandbox_report: dict) -> None:
        self._sandbox = sandbox_report
        self._queue: queue.Queue = queue.Queue()
        self.stdin = self                     # write()/flush() are below
        self.stdout = self._reader()
        self.stderr = iter(())
        self.pid = 4242
        self._handle = 7
        self.returncode = None
        self.job_assigned = True
        self.requests: list[dict] = []

    def write(self, line: str) -> None:
        request = json.loads(line)
        self.requests.append(request)
        self._queue.put(json.dumps(
            {"id": request["id"],
             "result": {"ok": True, "sandbox": self._sandbox}}) + "\n")

    def flush(self) -> None:
        pass

    def _reader(self):
        while True:
            line = self._queue.get()
            if line is None:
                return
            yield line

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode

    def kill(self) -> None:
        self.returncode = -9
        self._queue.put(None)                 # EOF for the drain thread


def test_the_client_spawns_through_the_backend_before_it_reaches_popen(
        isolated, windows, monkeypatch):
    """Decision 1's one line in the client, and Decision 3's honesty on top.

    `subprocess.Popen` is made to raise: reaching it at all would mean an
    unconfined worker on Windows. What comes back from the hook is used as the
    process — drained, pinged, killed — and `sandboxed` is decided by the
    report the *worker* sent, not by the plan that intended it.
    """
    from agentcad.kernel import client as client_module
    from agentcad.kernel.client import KernelClient

    module, calls = windows
    live = {"posture": "local", "quotas": ["job_object"],
            "confinement": ["filesystem", "network"], "appcontainer": True,
            "appcontainer_sid": calls.sid_str, "rlimits": [], "failures": []}
    proc = _EchoProc(live)
    monkeypatch.setattr(
        module, "ConfinedProcess",
        lambda argv, env, *, sid, job=None, cwd=None: proc)
    monkeypatch.setattr(client_module.subprocess, "Popen", _no_popen)

    kernel = KernelClient(writable_dirs=[str(isolated)],
                          quotas={"memory_mb": 1024})
    try:
        kernel.start()
        assert kernel._proc is proc
        assert proc.requests[0]["method"] == "ping"
        assert kernel.sandbox_report == live
        assert kernel.sandboxed is True
        # The private temp dir was prepared (tree + ACE) before the spawn.
        assert calls.trees and calls.trees[-1][0] == kernel._plan.tmp_dir

        body = sandbox.report(kernel)
        assert body["status"] == "active"
        assert body["mechanism"] == "appcontainer"
        assert body["confinement"]["detail"]["sid"] == calls.sid_str
        assert body["confinement"]["detail"]["appcontainer"] is True
        assert body["quotas"]["mechanism"] == "job_object+supervisor"
        assert body["warnings"] == []
    finally:
        kernel.stop()


def test_a_windows_worker_outside_its_container_clears_the_claim(isolated,
                                                                  windows,
                                                                  monkeypatch):
    """The other half of the same rule: the plan intended an AppContainer, the
    worker looked at its own token and said no. `active` becomes `off`, the
    mechanism goes with it, and the warning says the check is what failed —
    the confinement is applied by the parent, so "could not apply" would send
    the reader to the wrong place."""
    from agentcad.kernel import client as client_module
    from agentcad.kernel.client import KernelClient

    module, calls = windows
    live = {"posture": "local", "confinement": ["filesystem", "network"],
            "appcontainer": False, "appcontainer_sid": None,
            "failures": [{"stage": "appcontainer",
                          "error": "OSError: OpenProcessToken: WinError 5"}]}
    proc = _EchoProc(live)
    monkeypatch.setattr(
        module, "ConfinedProcess",
        lambda argv, env, *, sid, job=None, cwd=None: proc)
    monkeypatch.setattr(client_module.subprocess, "Popen", _no_popen)

    kernel = KernelClient(writable_dirs=[str(isolated)],
                          quotas={"memory_mb": 1024})
    try:
        kernel.start()
        assert kernel.sandboxed is False
        body = sandbox.report(kernel)
        assert body["status"] == "off"
        assert body["mechanism"] is None
        assert body["confinement"]["detail"]["appcontainer"] is False
        assert any("could not read its own token" in warning
                   for warning in body["warnings"]), body["warnings"]
        # ...and the caps are untouched by any of it.
        assert body["quotas"]["mechanism"] == "job_object+supervisor"
    finally:
        kernel.stop()


def test_confinement_holds_on_win32_wants_the_workers_own_token(monkeypatch):
    """`confinement_holds` is the rule read on its own. On Windows the parent
    declares the facets only when it really spawned through the AppContainer,
    so their presence is what makes `appcontainer: true` a *requirement*
    rather than an extra."""
    from agentcad.kernel.client import confinement_holds

    monkeypatch.setattr(sys, "platform", "win32")
    intended = {"posture": "local", "quotas": ["job_object"],
                "confinement": ["filesystem", "network"]}
    assert confinement_holds({**intended, "appcontainer": True}) is True
    assert confinement_holds({**intended, "appcontainer": False}) is False
    # A preamble that never looked (an old worker, a token that could not be
    # read) is not evidence either.
    assert confinement_holds(intended) is False
    # A quota-only payload claimed no confinement, so there is nothing here to
    # clear: `client.sandboxed` is already False from the plan.
    assert confinement_holds({"posture": "local",
                              "quotas": ["job_object"]}) is True


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
        # No parent-declared `confinement` on Linux (independent re-review,
        # post-F1): unlike macOS's parent-applied seatbelt, the Linux worker
        # CAN and DOES self-report `landlock_abi`/`seccomp` from actually
        # applying them, so a parent-declared facet list here would be a
        # second, unconditional claim that survives a stage failing inside
        # the worker — exactly the honesty rule M3 exists to close.
        assert "confinement" not in payload
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


def test_the_fork_budget_scales_with_the_pool_and_is_remeasured_each_spawn(
        isolated, linux, monkeypatch):
    """Review C2. ``RLIMIT_NPROC`` is a **per-uid** ceiling that the kernel
    checks against the *calling* process's own limit, so one number computed
    once in ``KernelClient.__init__`` and handed to every pool slot is wrong
    for all but the first: a warm worker runs 15-22 threads, and the third
    worker of a three-worker pool died inside ``import build123d``.

    The cap is therefore the live task count **measured at each spawn** plus
    ``pids_headroom x pool_size`` — which still bounds a fork bomb, at
    ``headroom x pool_size`` extra tasks across the pool.
    """
    live = [40]
    monkeypatch.setattr(linux, "live_uid_process_count", lambda: live[0])

    plan = _plan([isolated], quotas={"pids_headroom": 7}, pool_size=3)
    try:
        assert plan.pool_size == 3
        snapshot = json.loads(plan.env["AGENTCAD_CONFINE"])
        assert snapshot["rlimits"]["RLIMIT_NPROC"] == [61, 61]   # 40 + 7 x 3

        # Two consecutive spawns with the uid busier in between: the second
        # measures the machine as it is, not as it was at construction.
        first = json.loads(plan.spawn_env()["AGENTCAD_CONFINE"])
        live[0] = 95
        second = json.loads(plan.spawn_env()["AGENTCAD_CONFINE"])
        assert first["rlimits"]["RLIMIT_NPROC"] == [61, 61]
        assert second["rlimits"]["RLIMIT_NPROC"] == [116, 116]   # 95 + 7 x 3

        # `plan.env` is the construction-time snapshot and stays one — health
        # and every other test read it as such.
        assert json.loads(plan.env["AGENTCAD_CONFINE"]) == snapshot
        # ...and nothing ELSE in the payload moves: a respawn has to come back
        # under identical roots, posture and seccomp target.
        assert ({k: v for k, v in second.items() if k != "rlimits"}
                == {k: v for k, v in snapshot.items() if k != "rlimits"})
        # The address-space cap is not a live measurement, so it does not drift.
        assert second["rlimits"]["RLIMIT_AS"] == snapshot["rlimits"]["RLIMIT_AS"]
    finally:
        plan.release()


def test_a_lone_client_is_one_pool_slot_and_a_backend_may_refresh_nothing(
        isolated, backend):
    """The default is `pool_size=1` — a lone `KernelClient` is unchanged — and
    a backend with nothing to re-measure (the Windows one, a test double) makes
    `spawn_env()` exactly the construction-time environment."""
    plan = _plan([isolated])
    try:
        assert plan.pool_size == 1
        assert backend.calls[0].pool_size == 1
        assert plan.spawn_env() == plan.env
        assert plan.spawn_env() is not plan.env      # a copy, never the object
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


def test_a_lost_root_grant_does_not_clear_the_confinement_claim(isolated,
                                                                 backend):
    """Review I2. A Landlock rule on a path that does not exist is ENOENT: the
    grant is lost, but the **ruleset landed** and the process is confined —
    more narrowly than intended, not less. Filing that under `landlock` told
    health the worker was unconfined, and under `AGENTCAD_EXPECT_SANDBOX=active`
    it turned one missing directory into a red CI job.

    It is still a failure: it stays in `failures` and it reaches `warnings`,
    because the write it was meant to permit really will be denied.
    """
    from agentcad.kernel.client import confinement_holds

    live = {"landlock_abi": 6, "seccomp": "seccomp(2)",
            "rlimits": ["RLIMIT_AS"],
            "failures": [{"stage": "landlock_root",
                          "error": "/srv/absent: ENOENT"}]}
    assert confinement_holds(live) is True
    # ...while a ruleset-level failure still clears it, on every platform.
    assert confinement_holds(
        {**live, "failures": [{"stage": "landlock", "error": "EOPNOTSUPP"}]}
    ) is False

    plan = _plan([isolated])
    try:
        body = sandbox.report(SimpleNamespace(_plan=plan, sandbox_report=live,
                                              sandboxed=True))
        assert body["status"] == "active"
        assert body["mechanism"] == "fake"
        warning = next(w for w in body["warnings"] if "/srv/absent" in w)
        # Named as what it is: a lost grant, not a sandbox that failed.
        assert "lost a Landlock grant" in warning
        assert "could not apply" not in warning
    finally:
        plan.release()


def test_report_never_disagrees_with_the_kernels_own_sandboxed_flag(isolated,
                                                                     backend):
    """Review M1. `client.sandboxed` is this same rule applied at ping time,
    and it is what every other reader consults. The gap it closes: a worker
    that answered `ping` with **no `sandbox` object at all** leaves the live
    report empty, so the plan's `active` stood here while `client.sandboxed`
    was already False — two health facts contradicting each other.
    """
    plan = _plan([isolated])
    try:
        body = sandbox.report(SimpleNamespace(_plan=plan, sandbox_report={},
                                              sandboxed=False))
        assert body["status"] == "off"
        assert body["mechanism"] is None      # never named beside `off`
        assert any("did not report" in w for w in body["warnings"]), \
            body["warnings"]
        # An object with no such attribute is not a denial: the intent stands.
        assert sandbox.report(
            SimpleNamespace(_plan=plan, sandbox_report={}))["status"] == "active"
    finally:
        plan.release()


def test_report_drops_the_rlimit_tier_when_the_worker_applied_none(isolated,
                                                                    backend):
    """Review M2. `mechanism` is read as a promise, so a tier is dropped the
    moment the worker says it did not apply it — `setrlimit` can be refused (a
    lower hard limit already in force, Darwin's EINVAL) and naming `rlimit`
    over an empty `rlimits` list claims a cap nothing is enforcing."""
    plan = _plan([isolated])
    try:
        plan.quotas["mechanism"] = "cgroup+rlimit+supervisor"
        live = {"landlock_abi": 6, "seccomp": "seccomp(2)", "rlimits": [],
                "failures": []}
        body = sandbox.report(SimpleNamespace(_plan=plan, sandbox_report=live,
                                              sandboxed=True))
        assert body["quotas"]["mechanism"] == "cgroup+supervisor"
        assert body["quotas"]["status"] == "active"
        assert any("no rlimits" in w for w in body["warnings"])
        # The plan itself is not rewritten — this is a *report*.
        assert plan.quotas["mechanism"] == "cgroup+rlimit+supervisor"

        # The last tier going leaves the quotas honestly off.
        plan.quotas["mechanism"] = "rlimit"
        body = sandbox.report(SimpleNamespace(_plan=plan, sandbox_report=live,
                                              sandboxed=True))
        assert body["quotas"] == {**plan.quotas, "mechanism": None,
                                  "status": "off"}

        # A worker that DID apply them keeps the tier.
        plan.quotas["mechanism"] = "rlimit+supervisor"
        body = sandbox.report(SimpleNamespace(
            _plan=plan, sandboxed=True,
            sandbox_report={**live, "rlimits": ["RLIMIT_AS"]}))
        assert body["quotas"]["mechanism"] == "rlimit+supervisor"
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
    # Windows is a real capability question since PRD-006b: `userenv` has to
    # export `CreateAppContainerProfile` (Windows 8+) and `icacls` has to be
    # there to grant the SID its roots. Asserted as *the same answer* rather
    # than a constant, because this test also runs on the windows-latest job,
    # where it is `True` and on this dev box it is `False`.
    from agentcad.kernel import sandbox_windows

    monkeypatch.setattr(sys, "platform", "win32")
    assert sandbox.supported() is sandbox_windows.supported()
    monkeypatch.setattr(sandbox_windows, "_userenv_symbol", lambda name: False)
    assert sandbox.supported() is False


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


def test_serve_stops_the_kernel_when_the_registry_or_the_app_fails(
        monkeypatch, tmp_path):
    """`build_registry` and `create_app` used to run OUTSIDE the try, so a
    broken tool pack or a missing frontend asset leaked one ~0.5 GB worker per
    pool slot, its private temp dir, and the work root — once per attempt,
    which in a restart loop is every few seconds."""
    from agentcad import cli

    stopped: list[bool] = []
    work_root = tmp_path / "work-root"
    work_root.mkdir()
    service = SimpleNamespace(
        kernel=SimpleNamespace(stop=lambda: stopped.append(True)),
        work_root=work_root)
    monkeypatch.setenv("AGENTCAD_CONFIG", str(tmp_path / "cfg.json"))
    monkeypatch.delenv("AGENTCAD_MODE", raising=False)
    monkeypatch.setattr(cli, "_build_service", lambda *a, **k: service)

    def _boom(svc):
        raise RuntimeError("a tool pack raised at registration")

    monkeypatch.setattr("agentcad.core.tools.build_registry", _boom)
    args = SimpleNamespace(host="127.0.0.1", port=8630,
                           projects_dir=str(tmp_path / "projects"),
                           no_open=True)

    with pytest.raises(RuntimeError):
        cli.cmd_serve(args, open_browser=False)

    assert stopped == [True]
    assert not work_root.exists(), "the work root outlived the server"


def test_serve_maps_a_bad_quota_to_exit_two(monkeypatch, tmp_path, capsys):
    """`quotas.resolve()` names the key and the layer in a ValueError; the
    reader is an operator staring at a server that will not start, so it gets
    the repo's `error: …` + exit 2, not a traceback."""
    from agentcad import cli

    monkeypatch.setenv("AGENTCAD_CONFIG", str(tmp_path / "cfg.json"))
    monkeypatch.delenv("AGENTCAD_MODE", raising=False)
    monkeypatch.setenv("AGENTCAD_QUOTA_MEMORY_MB", "lots")
    # Nothing with a side effect may run first: a refused start leaves no work
    # root behind and spawns no worker.
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path / "temp"))
    (tmp_path / "temp").mkdir()
    args = SimpleNamespace(host="127.0.0.1", port=8630,
                           projects_dir=str(tmp_path / "projects"),
                           no_open=True)

    with pytest.raises(SystemExit) as exit_info:
        cli.cmd_serve(args, open_browser=False)

    assert exit_info.value.code == 2
    assert "AGENTCAD_QUOTA_MEMORY_MB" in capsys.readouterr().err
    assert list((tmp_path / "temp").iterdir()) == []


def test_serve_warns_loudly_when_a_hosted_kernel_is_not_confined(monkeypatch,
                                                                  tmp_path,
                                                                  capsys):
    """Decision 8: a hosted instance that failed to confine its workers says
    so at startup — and keeps booting, because the deploy-smoke job must go on
    proving the compose image starts and the operator reads `/api/health`."""
    import uvicorn

    from agentcad import cli

    service = SimpleNamespace(kernel=SimpleNamespace(stop=lambda: None),
                              work_root=None)
    monkeypatch.setenv("AGENTCAD_CONFIG", str(tmp_path / "cfg.json"))
    monkeypatch.setenv("AGENTCAD_MODE", "hosted")
    monkeypatch.setenv("AGENTCAD_PUBLIC_ORIGIN", "https://cad.example.com")
    monkeypatch.setattr(cli, "_build_service", lambda *a, **k: service)
    monkeypatch.setattr(cli, "_make_chat_engine", lambda svc, reg: None)
    monkeypatch.setattr("agentcad.core.tools.build_registry",
                        lambda svc: object())
    monkeypatch.setattr("agentcad.server.app.create_app",
                        lambda *a, **k: object())
    monkeypatch.setattr(sandbox, "report", lambda kernel: {
        "status": "off", "warnings": ["the worker could not apply landlock: "
                                      "EOPNOTSUPP"]})
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: None)
    args = SimpleNamespace(host="0.0.0.0", port=8630,
                           projects_dir=str(tmp_path / "projects"),
                           no_open=True)

    cli.cmd_serve(args, open_browser=False)      # never fatal

    err = capsys.readouterr().err
    assert err.startswith("WARNING:") or "\nWARNING:" in err
    assert "confinement is off" in err and "EOPNOTSUPP" in err


def test_serve_says_nothing_about_confinement_in_local_mode(monkeypatch,
                                                             tmp_path, capsys):
    import uvicorn

    from agentcad import cli

    service = SimpleNamespace(kernel=SimpleNamespace(stop=lambda: None))
    monkeypatch.setenv("AGENTCAD_CONFIG", str(tmp_path / "cfg.json"))
    monkeypatch.delenv("AGENTCAD_MODE", raising=False)
    monkeypatch.setattr(cli, "_build_service", lambda *a, **k: service)
    monkeypatch.setattr(cli, "_make_chat_engine", lambda svc, reg: None)
    monkeypatch.setattr("agentcad.core.tools.build_registry",
                        lambda svc: object())
    monkeypatch.setattr("agentcad.server.app.create_app",
                        lambda *a, **k: object())
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: None)
    args = SimpleNamespace(host="127.0.0.1", port=8630,
                           projects_dir=str(tmp_path / "projects"),
                           no_open=True)

    cli.cmd_serve(args, open_browser=False)

    assert "WARNING" not in capsys.readouterr().err


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
        # macOS has no landlock/seccomp keys (the worker applies neither
        # itself), but it DOES declare the facets the seatbelt — wrapped
        # around the argv by the parent — genuinely enforces, so a seatbelt
        # denial can still answer `details.denied`.
        assert set(payload) == {"rlimits", "confinement"}
        assert payload["confinement"] == ["filesystem", "network"]
    finally:
        plan.release()


def test_the_macos_fork_budget_scales_with_the_pool_and_is_remeasured(
        isolated, monkeypatch):
    """macOS runs review C2's formula too: the live uid count, measured at
    every spawn, plus ``pids_headroom x pool_size``. It counts *processes*
    rather than tasks here — the right measure on Darwin — and the scaling is
    what stops one pool slot spending the budget the next one needs.

    Pure, and so all-OS: the libproc walk is stubbed out.
    """
    live = [200]
    monkeypatch.setattr(sandbox_macos, "live_uid_process_count",
                        lambda: live[0])
    quotas = resolve({"pids_headroom": 9})
    assert sandbox_macos._rlimits(quotas, 1) == {"RLIMIT_NPROC": [209, 209]}
    assert sandbox_macos._rlimits(quotas, 3) == {"RLIMIT_NPROC": [227, 227]}

    mac = sandbox_macos.MacBackend(quotas, 3)
    assert json.loads(mac.refresh()["AGENTCAD_CONFINE"]) == {
        "rlimits": {"RLIMIT_NPROC": [227, 227]}}
    live[0] = 260
    assert json.loads(mac.refresh()["AGENTCAD_CONFINE"]) == {
        "rlimits": {"RLIMIT_NPROC": [287, 287]}}

    # No fork budget at all: nothing to re-measure, so `spawn_env()` leaves the
    # construction-time environment exactly as it was.
    off = resolve({"pids_headroom": 0})
    assert sandbox_macos._rlimits(off, 3) == {}
    assert sandbox_macos.MacBackend(off, 3).refresh() == {}
    assert sandbox_macos.MacBackend().refresh() == {}


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
