"""PRD-006 slice 3 — the parent-side supervisor, against a real worker.

The acceptance criteria a *cap* has to meet, driven end to end: a part script
that allocates until the machine hurts is killed **and named** (AC4), a script
that hangs is timed out with the wall clock it burned attached (AC5), and a
breach on one pool worker does not disturb the sibling building beside it
(AC6). Everything here spawns actual workers and allocates actual memory;
nothing is monkeypatched.

The tier under test is the supervisor — the parent sampling the child's RSS in
its own request loop (design spec, Decision 5), which is the only memory tier
macOS has at all (`RLIMIT_AS`/`DATA`/`RSS` are `EINVAL` on Darwin). So every
client here is built with the two *other* memory tiers switched off:
`address_space_mb="off"` (on Linux `RLIMIT_AS` would turn the balloon into a
recoverable `MemoryError` before the sampler ever saw the RSS — that path is
`tests/test_sandbox_linux.py::test_rlimit_as_makes_balloon_recoverable`) and
`AGENTCAD_CGROUP_DIR=off` (a host with a delegated cgroup would OOM-kill first
and answer `tier: "cgroup"` — `test_cgroup_tier_when_delegated`).
"""

from __future__ import annotations

import json
import queue
import sys
import threading
from types import SimpleNamespace

import pytest

from agentcad.kernel.client import KernelClient, KernelError
from agentcad.kernel.pool import KernelPool
from agentcad.kernel.quotas import resolve

pytestmark = [
    pytest.mark.integration,
    pytest.mark.portability,
    pytest.mark.slow,
    pytest.mark.skipif(
        sys.platform == "win32",
        reason="the balloon is sized for a POSIX worker; the Windows job "
               "object is tests/test_sandbox_windows.py"),
]

BOX = """\
from build123d import *

PARAMS = {}

def build(p):
    with BuildPart() as part:
        Box(10, 10, 10)
    return part.part
"""


def _script(body: str) -> str:
    """A part script whose `build()` runs *body* and then returns a box."""
    indented = "\n".join("    " + line for line in body.strip("\n").splitlines())
    return ("from build123d import *\n\nPARAMS = {}\n\ndef build(p):\n"
            f"{indented}\n"
            "    with BuildPart() as part:\n        Box(10, 10, 10)\n"
            "    return part.part\n")


#: 1.5 GB, every page touched — `bytearray` is lazily zero-filled, so an
#: untouched allocation would grow the address space and never the RSS the
#: supervisor samples. The sleep holds the pages resident: a strided store over
#: 1.5 GB takes well under one 0.25 s sampling interval, so without it the
#: script can allocate, breach and free between two samples (measured).
BALLOON = _script("""
import time
b = bytearray(1_500_000_000)
b[::4096] = b"\\x01" * (len(b) // 4096 + 1)
time.sleep(30)
""")

HANG = _script("import time\ntime.sleep(5)")

SLOW = _script("import time\ntime.sleep(2)")


def _quotas(memory_mb: int):
    return resolve({"memory_mb": memory_mb, "address_space_mb": "off"},
                   env={}, config={})


def _client(root, memory_mb: int, **kwargs) -> KernelClient:
    """A capped client whose only memory tier is the supervisor."""
    patch = pytest.MonkeyPatch()
    patch.setenv("AGENTCAD_CGROUP_DIR", "off")
    try:
        return KernelClient(writable_dirs=[str(root)],
                            quotas=_quotas(memory_mb), **kwargs)
    finally:
        patch.undo()


def _build(client, script, mesh_path, **kwargs):
    return client.request("build", {"script": script, "params": {},
                                    "mesh_path": str(mesh_path)}, **kwargs)


@pytest.fixture(scope="module")
def baseline_mb(tmp_path_factory) -> float:
    """What a warm worker occupies after one Box build, measured here.

    The cap has to be expressed relative to this: the spike measured 451-482 MB
    for a warm worker and 499 MB after a build+export, and a cap inside that
    band would kill an innocent build (a 512 MB cap did exactly that in the
    spike's control run). Measuring rather than hard-coding also keeps the test
    honest across architectures — the absolute numbers differ on x86-64.
    """
    root = tmp_path_factory.mktemp("baseline")
    client = _client(root, memory_mb=8192)
    client.start()
    try:
        _build(client, BOX, root / "box.acm")
        rss = client.last_usage["rss_mb"]
    finally:
        client.stop()
    assert rss and rss > 50, f"implausible worker RSS: {rss!r}"
    return rss


@pytest.mark.timeout(600)
def test_a_ballooning_script_is_killed_named_and_the_worker_comes_back(
        baseline_mb, tmp_path):
    """AC4. The kill is the easy half; the naming is the point.

    `kernel_crash` on its own tells a reader the worker died, which is what
    they already knew. `details.reason == "memory_cap"` with the cap and the
    RSS that broke it tells them to shrink the part or raise the quota, and
    `details.usage` gives them the request that did it.
    """
    cap = int(baseline_mb) + 300
    records: list[dict] = []
    client = _client(tmp_path, memory_mb=cap, on_usage=records.append)
    client.start()
    try:
        good = tmp_path / "box.acm"
        _build(client, BOX, good)
        before = good.read_bytes()

        with pytest.raises(KernelError) as exc_info:
            _build(client, BALLOON, tmp_path / "balloon.acm", timeout_s=300)
        err = exc_info.value

        # The breach reaches the meter too (review I3): the request that cost
        # the most is exactly the one that never answered, and emitting only on
        # the success path left it out of every roll-up.
        breach = records[-1]
        assert breach["method"] == "build" and breach["ok"] is False
        assert breach["usage"]["cpu_ms"] is None
        assert breach["usage"]["wall_ms"] > 0
        assert breach["usage"]["peak_rss_mb"] > cap

        assert err.type == "kernel_crash"
        assert err.details["reason"] == "memory_cap"
        assert err.details["tier"] == "supervisor"
        assert err.details["limit_mb"] == cap
        assert err.details["observed_rss_mb"] > cap
        usage = err.details["usage"]
        assert usage["wall_ms"] > 0
        assert usage["peak_rss_mb"] >= err.details["observed_rss_mb"]
        assert usage["peak_rss_is_lifetime"] is False
        assert "memory cap" in err.message and str(cap) in err.message

        # ...and the next build succeeds on a respawned worker, with the
        # previous good geometry untouched on disk (a breach is not a
        # corruption: `_status` is not written on error paths).
        result = _build(client, BOX, tmp_path / "box2.acm")
        assert result["metrics"]["volume_mm3"] == pytest.approx(1000.0, rel=1e-6)
        assert good.read_bytes() == before
        assert (tmp_path / "box2.acm").read_bytes() == before
    finally:
        client.stop()


@pytest.mark.timeout(300)
def test_a_timeout_carries_the_wall_clock_it_burned(tmp_path):
    """AC5. The worker died before it could report its own usage, so the
    parent reports what the parent can see: the elapsed wall clock and the
    peak RSS its own samples observed. `cpu_ms` is `None` rather than 0 —
    "not measurable from here" is not "no CPU was spent"."""
    client = _client(tmp_path, memory_mb=4096)
    client.start()
    try:
        with pytest.raises(KernelError) as exc_info:
            _build(client, HANG, tmp_path / "hang.acm", timeout_s=1)
        err = exc_info.value

        assert err.type == "timeout"
        usage = err.details["usage"]
        # Measured before the kill, so it is the request's wall clock and not
        # the teardown's: `_kill` waits up to five seconds for the process to
        # die, and a timeout that reported 6 000 ms for a 1 s budget would be
        # the first number a reader stopped believing.
        assert 1000 <= usage["wall_ms"] < 3000
        assert usage["cpu_ms"] is None
        assert usage["peak_rss_is_lifetime"] is False
        assert usage["peak_rss_mb"] > 0        # the supervisor's own samples
        assert client.request("ping", {})["ok"] is True
    finally:
        client.stop()


@pytest.mark.timeout(600)
def test_a_breach_on_one_pool_worker_leaves_its_sibling_alone(baseline_mb,
                                                              tmp_path):
    """AC6/FR9. Each `KernelClient` owns its own process, its own lock and its
    own breach state, so this needs no code of its own — which is exactly why
    it needs a test: nothing would otherwise notice the day a supervisor kill
    started reaching across the pool."""
    cap = int(baseline_mb) + 300
    patch = pytest.MonkeyPatch()
    patch.setenv("AGENTCAD_CGROUP_DIR", "off")
    try:
        pool = KernelPool(size=2, writable_dirs=[str(tmp_path)],
                          quotas=_quotas(cap), timeout_s=300)
    finally:
        patch.undo()

    # `hash()` of a str is salted per process, so the two affinities that land
    # on different workers have to be found at run time, not written down.
    keys = [f"part-{index}" for index in range(32)]
    quiet = next(k for k in keys if hash(k) % 2 == 0)
    loud = next(k for k in keys if hash(k) % 2 == 1)

    outcome: dict[str, object] = {}

    def _quiet():
        try:
            outcome["quiet"] = pool.request(
                "build", {"script": SLOW, "params": {},
                          "mesh_path": str(tmp_path / "quiet.acm")},
                affinity=quiet)
        except BaseException as exc:       # recorded, asserted on below
            outcome["quiet"] = exc

    def _loud():
        try:
            outcome["loud"] = pool.request(
                "build", {"script": BALLOON, "params": {},
                          "mesh_path": str(tmp_path / "loud.acm")},
                affinity=loud)
        except BaseException as exc:
            outcome["loud"] = exc

    try:
        threads = [threading.Thread(target=_quiet), threading.Thread(target=_loud)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=480)
        assert not any(thread.is_alive() for thread in threads)

        assert isinstance(outcome["loud"], KernelError)
        assert outcome["loud"].details["reason"] == "memory_cap"
        assert outcome["quiet"]["metrics"]["volume_mm3"] == pytest.approx(
            1000.0, rel=1e-6)
        assert (tmp_path / "quiet.acm").read_bytes()[:4] == b"ACM1"
    finally:
        pool.stop()


def test_the_sampling_interval_is_floored_and_the_cap_is_opt_in(tmp_path):
    """No worker: the two numbers the request loop runs on.

    `sample_interval_s` is operator-settable and `"off"` resolves it to `0.0`.
    Used as a queue timeout that is a spin on one core — a supervisor that
    burns a CPU to watch for a CPU quota — so it is floored, and a falsy value
    means "the default", not "as fast as possible".
    """
    def _interval(**over):
        client = KernelClient(writable_dirs=[str(tmp_path)],
                              quotas=resolve(over, env={}, config={}))
        try:
            return client._supervision()
        finally:
            client.stop()

    assert _interval(sample_interval_s="off") == (0.25, 2048 * 1024 * 1024)
    assert _interval(sample_interval_s=0.001)[0] == 0.05
    assert _interval(sample_interval_s=2)[0] == 2
    # The cap is opt-in: with the knob off the loop still runs the timeout, and
    # samples nothing at all.
    assert _interval(memory_mb="off") == (0.25, None)
    # ...and a client with no plan is the historical loop, byte for byte.
    assert KernelClient()._supervision() == (0.5, None)


@pytest.mark.timeout(300)
def test_a_worker_that_dies_reports_the_crash_with_what_it_cost(tmp_path):
    """The third path with no worker report to carry usage (breach, timeout,
    crash — the global constraint names all three). The worker took its own
    meter down with it, so the parent reports what the parent saw."""
    client = _client(tmp_path, memory_mb=4096)
    client.start()
    try:
        with pytest.raises(KernelError) as exc_info:
            _build(client, _script("import os\nos._exit(9)"),
                   tmp_path / "gone.acm")
        err = exc_info.value

        assert err.type == "kernel_crash"
        assert err.details["usage"]["wall_ms"] > 0
        assert err.details["usage"]["cpu_ms"] is None
        assert "stderr_tail" in err.details
        assert client.request("ping", {})["ok"] is True
    finally:
        client.stop()


@pytest.mark.timeout(300)
def test_a_timeout_and_a_crash_both_reach_the_usage_hook(tmp_path):
    """Review I3. `core/usage.py` documents ``cpu_ms: None`` records for
    exactly these paths — a kill, a timeout, a crash — yet the hook only ever
    fired on the success path, so the meter's ``errors`` counter could not rise
    and a 60 s timeout contributed nothing at all to the wall clock it burned.

    The record is the parent's own measurement: the worker took its meter down
    with it, so ``cpu_ms`` is ``None`` rather than 0.
    """
    records: list[dict] = []
    client = _client(tmp_path, memory_mb=4096, on_usage=records.append,
                     name="worker-3")
    client.start()
    try:
        with pytest.raises(KernelError):
            _build(client, HANG, tmp_path / "hang.acm", timeout_s=1)
        timed_out = records[-1]
        assert timed_out["method"] == "build" and timed_out["ok"] is False
        assert timed_out["worker"] == "worker-3"
        assert timed_out["usage"]["cpu_ms"] is None
        assert 1000 <= timed_out["usage"]["wall_ms"] < 3000

        with pytest.raises(KernelError):
            _build(client, _script("import os\nos._exit(9)"),
                   tmp_path / "gone.acm")
        crashed = records[-1]
        assert crashed["method"] == "build" and crashed["ok"] is False
        assert crashed["usage"]["cpu_ms"] is None
        assert crashed["usage"]["wall_ms"] > 0
    finally:
        client.stop()


def test_an_unreachable_worker_is_metered_before_it_is_mourned():
    """The fourth kill path (review I3): the write itself failed, so nothing
    was ever asked. No worker — the broken pipe is driven directly."""
    client = KernelClient()
    records: list[dict] = []
    client._on_usage = records.append

    def _broken(line):
        raise BrokenPipeError(32, "Broken pipe")

    client._proc = SimpleNamespace(
        stdin=SimpleNamespace(write=_broken, flush=lambda: None),
        poll=lambda: 1)

    with pytest.raises(KernelError) as exc_info:
        client._request_locked("build", {}, 5.0)

    assert exc_info.value.type == "kernel_crash"
    assert len(records) == 1
    assert records[0]["method"] == "build" and records[0]["ok"] is False
    assert records[0]["usage"]["cpu_ms"] is None
    assert records[0]["usage"] == exc_info.value.details["usage"]


def test_a_response_with_no_usage_leaves_the_last_one_standing():
    """No worker: the merge path, driven directly.

    `last_usage` answers "what did the last request cost". A response with no
    usage envelope has nothing to say about that, and overwriting it with `{}`
    would turn "nothing to say" into "it cost nothing" — which is what a meter
    would then roll up.
    """
    client = KernelClient()
    client.last_usage = {"cpu_ms": 12.5, "wall_ms": 30.0}
    records: list[dict] = []
    client._on_usage = records.append

    class _Answering(queue.Queue):
        """Answers whatever id the request just chose.

        `block=False` is the loop's own drain of stale lines and must still
        run dry, or the request never gets as far as asking.
        """

        def get(self, block=True, timeout=None):
            if not block:
                raise queue.Empty
            return json.dumps({"id": client._last_req_id,
                               "result": {"ok": True}}) + "\n"

    client._lines = _Answering()
    client._proc = SimpleNamespace(
        stdin=SimpleNamespace(write=lambda line: None, flush=lambda: None),
        poll=lambda: None)

    assert client._request_locked("ping", {}, 5.0) == {"ok": True}
    assert client.last_usage == {"cpu_ms": 12.5, "wall_ms": 30.0}
    assert records == [{"method": "ping", "usage": {}, "ok": True,
                        "worker": None}]


@pytest.mark.timeout(300)
def test_the_usage_hook_sees_every_request_that_answered(tmp_path):
    """The seam Slice 4's meter plugs into: one record per answered request,
    carrying the merged usage (the worker's own numbers plus the supervisor's
    observed peak) and which worker produced it."""
    records: list[dict] = []
    client = _client(tmp_path, memory_mb=4096, on_usage=records.append,
                     name="worker-7")
    client.start()
    try:
        _build(client, BOX, tmp_path / "box.acm")
        assert [record["method"] for record in records] == ["ping", "build"]
        last = records[-1]
        assert last["ok"] is True
        assert last["worker"] == "worker-7"
        assert last["usage"] is client.last_usage
        assert last["usage"]["cpu_ms"] > 0 and last["usage"]["wall_ms"] > 0
        # The parent's samples merge into the worker's own peak, which is what
        # gives macOS a per-request peak it cannot measure from inside.
        assert last["usage"]["peak_rss_mb"] > 0
        assert last["usage"]["peak_rss_is_lifetime"] is False

        # A script error is still a request that answered: `ok` is False and
        # the usage is real (this is what the meter counts as an error).
        with pytest.raises(KernelError):
            _build(client, _script("raise ValueError('nope')"),
                   tmp_path / "bad.acm")
        assert records[-1]["method"] == "build" and records[-1]["ok"] is False
        assert records[-1]["usage"]["wall_ms"] > 0
    finally:
        client.stop()


@pytest.mark.timeout(300)
def test_a_hook_that_raises_never_fails_a_build(tmp_path, capsys):
    """A metering bug is not a geometry failure. It is not silent either:
    the first one prints a line to stderr, where the crash tail already goes.
    """
    def _boom(record):
        raise RuntimeError("the meter is broken")

    client = _client(tmp_path, memory_mb=4096, on_usage=_boom)
    client.start()
    try:
        result = _build(client, BOX, tmp_path / "box.acm")
        assert result["metrics"]["volume_mm3"] == pytest.approx(1000.0, rel=1e-6)
    finally:
        client.stop()
    assert "the meter is broken" in capsys.readouterr().err
