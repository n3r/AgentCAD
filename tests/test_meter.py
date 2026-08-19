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
