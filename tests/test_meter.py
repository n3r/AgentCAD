"""PRD-006 slice 2 — `kernel/_meter.py`: what one request cost.

The trap this file exists for is `ru_maxrss`: it is **bytes on macOS and KiB
on Linux**, so a meter written on one platform reports the other's memory
1024x wrong — and a memory quota reported 1024x wrong is worse than none. The
unit branch is asserted directly on both platforms.

The second thing worth a test is the honesty flag. On Linux the meter resets
`VmHWM` through `/proc/self/clear_refs`, so `peak_rss_mb` is a genuine
*per-request* peak; everywhere else (and on a Linux where the write failed) it
is the process's lifetime high-water mark, and `peak_rss_is_lifetime` says so
rather than letting a caller read a warm worker's history as this build's cost.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from agentcad.kernel._meter import Meter, rusage_peak_mb

KEYS = {"cpu_ms", "wall_ms", "peak_rss_mb", "rss_mb", "peak_rss_is_lifetime"}


def test_finish_reports_every_key_with_a_usable_type():
    meter = Meter()
    meter.start()
    sum(i * i for i in range(200_000))       # buy a measurable slice of CPU
    usage = meter.finish()

    assert set(usage) == KEYS
    assert isinstance(usage["cpu_ms"], float) and usage["cpu_ms"] >= 0
    assert isinstance(usage["wall_ms"], float) and usage["wall_ms"] > 0
    assert isinstance(usage["peak_rss_is_lifetime"], bool)
    for key in ("peak_rss_mb", "rss_mb"):
        assert usage[key] is None or isinstance(usage[key], float)
    if usage["peak_rss_mb"] is not None:
        # A CPython running a test session, not a fantasy in the wrong unit.
        assert 4 < usage["peak_rss_mb"] < 64 * 1024


def test_the_peak_is_per_request_only_where_it_was_actually_reset():
    """`peak_rss_is_lifetime` is False *only* on Linux and *only* when the
    `clear_refs` write landed — it is the flag a caller reads before believing
    `peak_rss_mb` belongs to this request."""
    meter = Meter()
    meter.start()
    usage = meter.finish()
    if sys.platform == "linux" and meter.peak_reset_ok:
        assert usage["peak_rss_is_lifetime"] is False
    else:
        assert usage["peak_rss_is_lifetime"] is True


def test_finish_without_start_still_answers():
    """The worker meters every response, including the ones from a request it
    could not even parse; a meter that raised there would turn a bad line into
    a dead worker."""
    usage = Meter().finish()
    assert set(usage) == KEYS
    assert usage["cpu_ms"] >= 0


def test_ru_maxrss_is_bytes_on_macos_and_kibibytes_on_linux(monkeypatch):
    """One mebibyte, spelled both ways: 1_048_576 is 1 MB of bytes on Darwin
    and 1024 MB of KiB on Linux."""
    import resource

    monkeypatch.setattr(resource, "getrusage",
                        lambda who: SimpleNamespace(ru_maxrss=1_048_576,
                                                    ru_utime=0.0, ru_stime=0.0))
    if sys.platform == "darwin":
        assert rusage_peak_mb() == 1.0
    elif sys.platform == "linux":
        assert rusage_peak_mb() == 1024.0
    else:  # pragma: no cover - Windows has no `resource` module
        pytest.skip("no getrusage on this platform")


@pytest.mark.skipif(sys.platform != "linux", reason="a Linux /proc reader")
def test_the_linux_peak_comes_from_proc_status():
    """`VmHWM`/`VmRSS` are the per-request peak and the end-of-request size;
    the rusage fallback is only for a `/proc` that could not be read."""
    from agentcad.kernel import _meter

    meter = Meter()
    meter.start()
    usage = meter.finish()
    assert usage["peak_rss_mb"] >= usage["rss_mb"] > 0
    # `finish()` rounds to 3 decimals for a stable JSON line; the raw reader
    # does not, so compare to the kilobyte rather than exactly.
    assert _meter.proc_status_mb("VmHWM") == pytest.approx(usage["peak_rss_mb"],
                                                           abs=0.002)


def test_a_windows_sampler_that_cannot_answer_is_not_fatal(monkeypatch):
    """`_memory_mb` tolerates a `None` from the Windows sampler, and
    `finish()` still returns a complete usage object.

    The contract this pins is PRD-006b's round-1 finding: inside an
    AppContainer `psapi.GetProcessMemoryInfo` was reached with a truncated
    pseudo-handle, strict handle checking turned that into a structured
    `STATUS_INVALID_HANDLE`, ctypes re-raised it as `OSError`, and it escaped
    `Meter.finish()` — which runs after EVERY request, so the worker died on
    the way out of a build that had already succeeded. The sampler now answers
    `None` instead of raising, and `None` has to mean "no number", never "no
    response line". Runs on every platform: `sys.platform` is what selects the
    branch, so faking it is enough.
    """
    from agentcad.kernel import _meter

    monkeypatch.setattr(_meter.sys, "platform", "win32")
    monkeypatch.setattr(_meter, "_windows_memory_counters", lambda: None)

    assert _meter._memory_mb(False) == (None, None, True)

    meter = Meter()
    meter.start()
    usage = meter.finish()
    assert set(usage) == KEYS
    assert usage["peak_rss_mb"] is None and usage["rss_mb"] is None
    assert usage["peak_rss_is_lifetime"] is True
    assert usage["wall_ms"] > 0


def test_the_windows_sampler_swallows_a_raising_psapi(monkeypatch):
    """The guard is the point: a psapi call that RAISES must still be `None`.

    Asserted against `_windows_memory_counters` itself (with the platform
    faked and the inner call replaced by the exception the container really
    produced), because that is the function whose old contract — "raise" —
    was the bug.
    """
    from agentcad.kernel import _meter

    def boom():
        raise OSError(-1073741816, "Windows Error 0xc0000008")

    monkeypatch.setattr(_meter.sys, "platform", "win32")
    monkeypatch.setattr(_meter, "_psapi_counters", boom)

    assert _meter._windows_memory_counters() is None
    assert _meter._memory_mb(False) == (None, None, True)
