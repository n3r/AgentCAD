"""PRD-006 acceptance — sandboxing and quotas, AC1–AC8.

One test (or one honest pointer) per criterion, each naming it in its
docstring, each graded against the shipped surface rather than a stub built
for the occasion.

| AC | Test |
|---|---|
| AC1 | `test_ac1_the_malicious_battery_is_named_and_gated` + `test_ac1_a_linux_worker_reports_itself_confined` |
| AC2 | `test_ac2_the_seatbelt_regressions_survived_the_refactor` |
| AC3 | `test_ac3_a_confined_kernel_reports_active_with_its_mechanism` + `test_ac3_windows_reports_unsupported_and_names_prd_006b` |
| AC4–AC7 | `test_ac4_to_ac7_are_graded_by_the_supervisor_and_usage_suites` (pointers) |
| AC8 | `test_ac8_the_opt_out_drops_confinement_and_keeps_the_caps` + `test_ac8_a_kernel_that_cannot_confine_says_unsupported_not_active` + `test_ac8_the_full_suite_count_is_cited` |

Three of them are worth reading before you believe them:

* **The whole file is gated on `AGENTCAD_EXPECT_SANDBOX`, and that is the
  point** (design spec, Decision 13). Every containment assertion holds *when
  the live status is `active`*, and the environment variable turns "not
  active" from a skip into a failure. `ci.yml` sets it on the macOS and ubuntu
  jobs and leaves it empty on Windows, where `unsupported` is the honest
  answer; `scripts/linux-test.sh` sets it too. Without it, a runner whose
  kernel lost Landlock would go green while confining nothing — which is
  exactly the silent degradation AC8 forbids.
* **AC4–AC7 are pointers, deliberately.** They are graded by tests that spawn
  real workers and allocate real memory (`tests/test_supervisor.py`) or drive
  two real builds through the meter (`tests/test_usage.py`). Re-running that
  work here would double a slow suite to restate a verdict; what this file
  grades instead is that those tests still exist under the names the criteria
  were signed off against.
* **AC8's "full suite green" is a claim about a *run*.** So it stays an
  evidence check on the newest changelog entry (the PRD-004 AC10 / PRD-011
  AC8 / PRD-012 AC8 precedent) rather than a number recomputed from inside the
  suite it is counting.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from agentcad.kernel import sandbox
from agentcad.kernel.client import KernelClient
from agentcad.kernel.quotas import resolve

pytestmark = [pytest.mark.portability, pytest.mark.slow]

REPO = Path(__file__).resolve().parent.parent
TESTS = Path(__file__).resolve().parent
CHANGELOG = REPO / "docs" / "changelog"

BASE_ARGV = ["/usr/bin/python3", "-u", "-m", "agentcad.kernel.worker"]

#: Well above the 451-482 MB a warm worker occupies (so nothing here is a
#: balloon test by accident) and small enough that `address_space_mb`'s auto
#: 3x is still a real cap.
QUOTAS = {"memory_mb": 1024, "pids_headroom": 64}


def _expect(name: str) -> str | None:
    """`AGENTCAD_EXPECT_SANDBOX` / `_QUOTAS`, read at call time.

    Not at import: a module-level read would freeze the answer for the whole
    xdist worker, and these tests are the ones that must not lie about what
    the environment asked for.
    """
    value = os.environ.get(f"AGENTCAD_EXPECT_{name}", "").strip()
    return value or None


def _source(name: str) -> str:
    return (TESTS / name).read_text(encoding="utf-8")


def _defines(source: str, function: str) -> bool:
    return re.search(rf"^\s*def {re.escape(function)}\(", source,
                     re.MULTILINE) is not None


def _numbered(directory: Path) -> list[tuple[int, Path]]:
    """`(number, path)` for every numbered changelog entry, any prefix width.

    `max()` over the *filenames* orders lexically, so a five-digit sequence
    would sort below a four-digit one the day the repo needs one. The number
    is what "newest" means here, so parse it.
    """
    entries = []
    for path in directory.glob("*.md"):
        match = re.match(r"(\d+)-", path.name)
        if match:
            entries.append((int(match.group(1)), path))
    assert entries, f"no numbered changelog entries under {directory}"
    return entries


@pytest.fixture(scope="module")
def confined(tmp_path_factory):
    """A real kernel worker, planned and confined the way the app plans one.

    Module-scoped because it costs an OCCT import; `pytest.MonkeyPatch` rather
    than the fixture for the same reason `tests/test_sandbox_linux.py` uses
    one — the sandbox decision is made at construction, so the environment
    only has to be right for that single call.
    """
    if sys.platform == "win32":
        pytest.skip("Windows confinement is PRD-006b; AC3's Windows clause is "
                    "its own test below")
    root = tmp_path_factory.mktemp("prd006-ac")
    patch = pytest.MonkeyPatch()
    patch.setenv("AGENTCAD_CONFIG", str(root / "no-such-config.json"))
    patch.delenv("AGENTCAD_NO_SANDBOX", raising=False)
    try:
        client = KernelClient(writable_dirs=[str(root)],
                              quotas=resolve(QUOTAS, env={}, config={}))
    finally:
        patch.undo()
    client.start()
    client.request("ping", {})   # the report is what the WORKER says it applied
    yield client
    client.stop()


# ==================================================================== AC1

def test_ac1_the_malicious_battery_is_named_and_gated():
    """**AC1** — "a network attempt, a write outside project roots, a fork
    bomb and a memory balloon each fail as a structured error naming the
    violation; the worker respawns and the next build succeeds".

    The battery itself is `tests/test_sandbox_linux.py`, which drives an
    actual worker with actual part scripts and runs on the ubuntu CI job and
    locally through `make test-linux`. This test runs on every OS and grades
    two things a Linux-only file cannot grade about itself: that the battery
    still exists under the names the criterion was signed off against (a
    deletion would otherwise be invisible from macOS), and that its skip is
    gated on `AGENTCAD_EXPECT_SANDBOX` rather than on the platform — a battery
    that skips itself when the sandbox degrades proves nothing.
    """
    source = _source("test_sandbox_linux.py")
    for name in ("test_network_is_denied",
                 "test_write_outside_roots_is_denied",
                 "test_rlimit_nproc_stops_a_fork_loop",
                 "test_rlimit_as_makes_a_balloon_recoverable",
                 "test_private_tmp_is_the_only_temp",
                 "test_kill_broadcast_is_denied",
                 "test_fork_child_inherits",
                 "test_io_uring_is_denied",
                 "test_hosted_posture_hides_state_dir",
                 "test_a_normal_build_still_works_confined"):
        assert _defines(source, name), f"the Linux battery lost {name}"
    assert "AGENTCAD_EXPECT_SANDBOX" in source, (
        "the battery's skip is no longer gated on the honesty variable")


@pytest.mark.skipif(sys.platform != "linux",
                    reason="AC1's containment runs on Linux; this is its "
                           "live-status half")
def test_ac1_a_linux_worker_reports_itself_confined(confined):
    """**AC1, the live half** — the battery above asserts containment; this
    asserts that the thing containing it is really there, from the worker's
    own mouth rather than from the plan's intention.

    With `AGENTCAD_EXPECT_SANDBOX=active` (ubuntu CI and `make test-linux`) a
    degraded worker fails here. Without it the report is still checked and
    only the `sandboxed` claim is skipped, so a developer on a kernel without
    Landlock gets a useful message instead of a red suite.
    """
    report = confined.sandbox_report
    assert report["landlock_abi"] >= 3, report
    assert report["seccomp"] in ("seccomp(2)", "prctl"), report
    assert report["failures"] == [], report
    if _expect("SANDBOX") != "active":
        pytest.skip(f"AGENTCAD_EXPECT_SANDBOX is unset; the live report above "
                    f"passed and `sandboxed` is {confined.sandboxed!r}")
    assert confined.sandboxed is True


# ==================================================================== AC2

def test_ac2_the_seatbelt_regressions_survived_the_refactor():
    """**AC2** — "the same battery stays contained on macOS (seatbelt
    regression tests keep passing)".

    PRD-006 moved the v3 profile out of `kernel/sandbox.py` into
    `kernel/sandbox_macos.py` and put a facade in front of it, which is
    exactly the shape of change that quietly loses coverage: the tests still
    pass because they no longer test the same thing, or because they are no
    longer there. So this pins the eight pre-PRD tests **by name** — the set
    `git show 78efcb4^:tests/test_sandbox.py` defines — and pins the file's
    current size as the floor: PRD-006 added four (the private-temp-dir
    coverage and the preamble-inside-the-seatbelt check), so twelve is what
    must still be there, not eight.

    Whether they pass is `tests/test_sandbox.py`'s job, on macOS, on every
    run; a deletion is what no passing suite can report.
    """
    source = _source("test_sandbox.py")
    for name in ("test_ping_under_sandbox",
                 "test_build_writes_mesh_inside_root",
                 "test_write_outside_roots_denied",
                 "test_network_denied_worker_survives",
                 "test_timeout_kills_and_recovers_sandboxed",
                 "test_env_kill_switch_disables",
                 "test_status_reflects_availability",
                 "test_health_reports_active_for_sandboxed_kernel"):
        assert _defines(source, name), f"a pre-PRD seatbelt test is gone: {name}"
    defined = len(re.findall(r"^def test_", source, re.MULTILINE))
    assert defined >= 12, (
        f"tests/test_sandbox.py defines {defined} tests; it had 8 before "
        f"PRD-006 and 12 after, and this floor is the deletion guard")


# ==================================================================== AC3

@pytest.mark.skipif(sys.platform == "win32",
                    reason="the Windows clause is the next test, and is 006b's")
def test_ac3_a_confined_kernel_reports_active_with_its_mechanism(confined):
    """**AC3** — "`/api/health` reports `sandbox: active` with the mechanism".

    Graded on `sandbox.report(kernel)`, which is the object `/api/health`
    publishes verbatim (`server/app.py`), so this is the health payload and
    not a paraphrase of it. Two halves, gated separately, because they are two
    different claims: `AGENTCAD_EXPECT_SANDBOX=active` asks whether the worker
    is *confined*, `AGENTCAD_EXPECT_QUOTAS=active` whether it is *capped*, and
    `AGENTCAD_NO_SANDBOX=1` switches off the first without touching the
    second.

    The shape is asserted unconditionally: a health object that lost a facet
    is a bug whatever the platform answered.
    """
    report = sandbox.report(confined)
    assert set(report) == {"status", "mechanism", "posture", "confinement",
                           "quotas", "warnings"}
    assert report["status"] == report["confinement"]["status"], (
        "the top-level status must stay the CONFINEMENT's — the historical "
        "meaning of health's `sandbox` field")
    assert report["posture"] in ("local", "hosted")
    assert set(report["quotas"]) == {"status", "mechanism", "limits"}
    assert report["quotas"]["limits"]["memory_mb"] == 1024

    if _expect("SANDBOX") == "active":
        assert report["status"] == "active", report
        expected = {"darwin": "seatbelt", "linux": "landlock+seccomp"}
        assert report["mechanism"] == expected[sys.platform], report
        assert report["confinement"]["mechanism"] == report["mechanism"]
    else:
        # Still honest when it is not active: a mechanism named beside `off`
        # would claim something is in force.
        assert report["status"] in ("active", "off", "unsupported"), report
        if report["status"] != "active":
            assert report["mechanism"] is None, report

    if _expect("QUOTAS") == "active":
        assert report["quotas"]["status"] == "active", report
        # The tier list, in tier order. The supervisor is the one tier every
        # platform has, so it is always the last word.
        assert report["quotas"]["mechanism"].endswith("supervisor"), report


def test_ac3_windows_reports_unsupported_and_names_prd_006b(tmp_path,
                                                            monkeypatch):
    """**AC3, the Windows clause** — and the reason this PRD does not close it.

    AppContainer with CPython and OCCT is the least-trodden path of the three,
    cannot be exercised on the box this was built on, and each attempt is a
    Windows-CI round trip — so it is carved out as **PRD-006b** and health
    says `unsupported` rather than implying a switch nobody flipped
    (design spec, Decisions 7 and 12). The Windows *quotas* did ship: the job
    object is a real tier and `sandbox_windows.py` is exercised live on the
    windows-latest job.

    Runs on every OS: the Win32 entry points live behind `open_job()`, and
    what is asserted here is the confinement the backend reports before any of
    them is called. The live job object is exercised by
    `tests/test_sandbox_windows.py` **on the windows-latest CI job** — gated
    there, not run here, and not yet observed green: that evidence lands with
    the first green CI run of this branch's PR.
    """
    from agentcad.kernel import sandbox_windows

    monkeypatch.setenv("AGENTCAD_CONFIG", str(tmp_path / "no-such-config.json"))
    monkeypatch.delenv("AGENTCAD_NO_SANDBOX", raising=False)
    _argv, _env, confinement, quotas, backend = sandbox_windows.build(
        BASE_ARGV, [str(tmp_path)], resolve(QUOTAS, env={}, config={}),
        "local", os.getpid())
    try:
        assert confinement["status"] == "unsupported"
        assert confinement["mechanism"] is None
        assert "PRD-006b" in confinement["detail"]["note"]
        # ...and the caps are published anyway. `unsupported` confinement is
        # not "nothing is bounded".
        assert quotas["limits"]["memory_mb"] == 1024
        if sys.platform == "win32" and _expect("QUOTAS") == "active":
            # Only on Windows is this a claim about a live job object; the
            # same call on macOS/Linux cannot open one, and `enforcement()`
            # would then name a tier nothing installed.
            assert quotas["status"] == "active", quotas
            assert quotas["mechanism"].startswith("job_object"), quotas
    finally:
        backend.release()

    prd = REPO / "docs" / "prd" / "pending" / "PRD-006b-windows-appcontainer.md"
    assert prd.is_file(), "the carve-out PRD the note points at does not exist"
    assert "folder-as-status" in prd.read_text(encoding="utf-8")


# =============================================================== AC4 – AC7

def test_ac4_to_ac7_are_graded_by_the_supervisor_and_usage_suites():
    """**AC4** (a 4 GiB script on a capped worker is killed, named
    `memory_cap`, carries `details.usage`, and the previous geometry
    survives), **AC5** (the timeout still fires and now carries
    `details.usage`), **AC6** (a breach on one pool worker leaves its sibling
    alone) and **AC7** (two projects' builds produce distinguishable
    roll-ups).

    All four are graded by tests that do the real thing — spawn workers,
    allocate memory until the supervisor kills, run a two-worker pool, drive
    two real builds through the meter — and re-running that work from here
    would double a slow suite to restate a verdict. What this grades is that
    they still exist, under the names the criteria were signed off against,
    with the marks that make CI run them.
    """
    supervisor = _source("test_supervisor.py")
    for name in (
            # AC4, AC5, AC6 in that order.
            "test_a_ballooning_script_is_killed_named_and_the_worker_comes_back",
            "test_a_timeout_carries_the_wall_clock_it_burned",
            "test_a_breach_on_one_pool_worker_leaves_its_sibling_alone"):
        assert _defines(supervisor, name), f"test_supervisor.py lost {name}"
    assert "pytest.mark.portability" in supervisor

    usage = _source("test_usage.py")
    # AC7 — and it is a real build, not a synthesised usage record.
    assert _defines(usage, "test_two_projects_are_two_rows_through_a_real_build")
    assert _defines(usage, "test_records_roll_up_per_project")
    assert "pytest.mark.portability" in usage


# ==================================================================== AC8

@pytest.mark.skipif(sys.platform == "win32",
                    reason="there is no Windows confinement to opt out of; "
                           "the backend keeps saying `unsupported`")
def test_ac8_the_opt_out_drops_confinement_and_keeps_the_caps(tmp_path,
                                                              monkeypatch):
    """**AC8, first half** — "`AGENTCAD_NO_SANDBOX=1` still opts out".

    And the half of that sentence which is easy to get wrong: it opts out of
    the **confinement**, not of the **quotas**. A runaway script may not take
    the machine down whether or not the operator trusts it with the
    filesystem, so the caps and the rlimit payload still travel. The report
    also has to say *why* it is off — an `off` with no reason is
    indistinguishable from a platform that never had a sandbox.
    """
    monkeypatch.setenv("AGENTCAD_CONFIG", str(tmp_path / "no-such-config.json"))
    monkeypatch.setenv("AGENTCAD_NO_SANDBOX", "1")
    monkeypatch.delenv("AGENTCAD_MODE", raising=False)
    for name in list(os.environ):
        if name.startswith("AGENTCAD_QUOTA_"):
            monkeypatch.delenv(name, raising=False)

    plan = sandbox.plan(BASE_ARGV, [str(tmp_path)], quotas=QUOTAS)
    try:
        assert plan.confinement["status"] == "off"
        assert plan.confinement["mechanism"] is None
        assert "AGENTCAD_NO_SANDBOX" in plan.confinement["detail"]["reason"]
        assert plan.argv == BASE_ARGV          # unwrapped
        assert plan.quotas["status"] == "active"
        assert plan.quotas["limits"]["memory_mb"] == 1024
    finally:
        plan.release()


def test_ac8_a_kernel_that_cannot_confine_says_unsupported_not_active(
        tmp_path, monkeypatch):
    """**AC8, second half** — "an unconfinable environment reports `off` +
    warning, never `active`" (FR13).

    The environment simulated is the one that actually happens: a Linux host
    whose kernel has no Landlock, or has it below ABI 3 — the floor, because
    `LANDLOCK_ACCESS_FS_TRUNCATE` (bit 14, ABI 3) is what keeps every
    truncating `open(path, "w")` inside a write root from being a false
    denial. Below it the design refuses to ship a half-model.

    Runs on every OS by picking the Linux backend with `sys.platform` and
    stubbing the two functions that genuinely need Linux — the same technique
    `tests/test_sandbox_plan.py` uses. The name patched is
    `sandbox_linux.landlock_abi`, not `_confine.landlock_abi`: the backend
    binds it at import (`from ._confine import ... landlock_abi`), so patching
    the source module would leave the live reference untouched and the test
    would pass while proving nothing.
    """
    from agentcad.kernel import sandbox_linux

    monkeypatch.setenv("AGENTCAD_CONFIG", str(tmp_path / "no-such-config.json"))
    monkeypatch.delenv("AGENTCAD_NO_SANDBOX", raising=False)
    monkeypatch.delenv("AGENTCAD_MODE", raising=False)
    monkeypatch.setenv("AGENTCAD_CGROUP_DIR", "off")
    for name in list(os.environ):
        if name.startswith("AGENTCAD_QUOTA_"):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(sandbox_linux, "live_uid_process_count", lambda: 40)
    monkeypatch.setattr(sandbox_linux, "platform",
                        SimpleNamespace(machine=lambda: "aarch64"))
    monkeypatch.setattr(sandbox_linux, "landlock_abi", lambda: 0)

    plan = sandbox.plan(BASE_ARGV, [str(tmp_path)], quotas=QUOTAS)
    try:
        assert plan.confinement["status"] == "unsupported"
        assert plan.confinement["mechanism"] is None
        assert "Landlock" in plan.confinement["detail"]["reason"]
        assert any("Landlock" in warning for warning in plan.warnings), \
            plan.warnings
        # A plan-only report answers the same way, so health cannot be told
        # `active` by a kernel that cannot confine.
        report = sandbox.report(SimpleNamespace(_plan=plan, sandbox_report={},
                                                sandboxed=False))
        assert report["status"] == "unsupported"
        assert report["mechanism"] is None
        assert any("Landlock" in warning for warning in report["warnings"])
        # ...and the caps still apply on that kernel.
        assert plan.quotas["status"] == "active"
    finally:
        plan.release()


def test_ac8_the_full_suite_count_is_cited():
    """**AC8, third half** — "full suite green on the three-OS CI matrix" is a
    claim about a *run*, so this is the evidence check that a count is on the
    record in the close-out changelog (the PRD-004 AC10 / PRD-008 AC9 /
    PRD-011 AC8 / PRD-012 AC8 precedent).

    It stays an evidence check deliberately: recomputing the number would mean
    running the full suite from inside the full suite, and `--collect-only`
    counts *cases*, which is not what `make test` reports.

    The number is required **immediately before the word `passed`** rather
    than anywhere in the file: every changelog entry's own title is a
    four-digit number, so "the file contains a long digit string" is satisfied
    by an entry that cites nothing. The literal placeholder ("N passed") is red
    here on purpose, so the close-out cannot forget to fill it in.

    Neither the entry nor "the newest entry" is addressed by its **number**:
    this repo renumbers changelogs when two branches collide at merge
    (`b24ef66` moved 0188-0197 to 0200-0209), so a hardcoded `0219-` would be
    a test that fails for a rename it should not care about. The entry is
    found by its **slug**, and the newest one by the numeric value of whatever
    prefix it has, at any width.
    """
    matches = sorted(CHANGELOG.glob("*-prd-006-ci-docs-acceptance.md"))
    assert len(matches) == 1, (
        f"expected exactly one PRD-006 close-out changelog entry, found "
        f"{[m.name for m in matches]}")
    entry = matches[0]
    text = entry.read_text(encoding="utf-8")
    assert "make test" in text
    assert re.search(r"\b\d{4,6}\s+passed\b", text.replace(",", "")), \
        "the close-out entry does not cite a `make test` suite count"

    latest = max(_numbered(CHANGELOG))[1]
    if latest != entry:
        recent = latest.read_text(encoding="utf-8")
        assert "make test" in recent and "passed" in recent, (
            f"{latest.name} is the newest changelog entry and cites no suite "
            "count; every entry that lands work must cite one")


def test_ac8_the_ci_matrix_carries_the_honesty_gate():
    """**AC8, the mechanism that makes the rest of this file mean anything.**

    Every containment assertion above is conditional on the live status, so
    without `AGENTCAD_EXPECT_SANDBOX` a runner that silently stopped confining
    would go green. The variable is what turns that into a red, and the probe
    step is what says why in the same run — so both are pinned here rather
    than trusted to survive the next workflow edit.
    """
    ci = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "Sandbox probe (Linux)" in ci
    assert "/sys/kernel/security/lsm" in ci
    assert "landlock_abi()" in ci
    assert ci.count("AGENTCAD_EXPECT_SANDBOX") >= 2, \
        "both suites must carry the gate"
    assert ci.count("AGENTCAD_EXPECT_QUOTAS: active") >= 2
    # macOS and ubuntu expect confinement; windows-latest deliberately does
    # not, because `unsupported` is 006b's honest answer there.
    assert ci.count("expect_sandbox: active") == 2, ci
    assert 'expect_sandbox: ""' in ci
