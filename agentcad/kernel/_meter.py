"""What one kernel request cost: CPU, wall clock, and resident memory.

Every worker response now carries a ``usage`` object beside its ``result`` or
``error`` (design spec, Decision 6). This is the worker half: it measures, it
never decides. The client merges the supervisor's own samples into it, the
service rolls it up per project, and health publishes the totals.

Two platform traps live here, and both are why this is a module rather than
three lines in ``worker.main``:

* ``ru_maxrss`` is **bytes on macOS and KiB on Linux**. A meter that picks one
  reports the other 1024x wrong.
* ``ru_maxrss``/``VmHWM`` are *lifetime* high-water marks, and a kernel worker
  is warm across many requests — so the peak of a 40 ms build would otherwise
  be the 500 MB OCCT import that preceded it. On Linux writing ``5`` to
  ``/proc/self/clear_refs`` resets both, which turns the number into a real
  per-request peak; where that is not possible the reading is still published
  but flagged ``peak_rss_is_lifetime``.

Deliberately importable from server code and from any OS: no ``OCP``/build123d,
and every platform call is lazy and degrades to ``None``.
"""

from __future__ import annotations

import ctypes
import os
import struct
import sys
import time
from types import SimpleNamespace

try:                              # POSIX only; Windows has no `resource`
    import resource as _resource
except ImportError:               # pragma: no cover - exercised on Windows CI
    _resource = None

#: ``echo 5 > /proc/self/clear_refs`` clears the referenced bits *and* resets
#: the peak accounting (``VmHWM`` and ``ru_maxrss``). Measured at 6 us on a
#: 25 MB process in the spike; it runs once per request.
CLEAR_REFS = "/proc/self/clear_refs"
CLEAR_REFS_PEAK_RESET = "5\n"

_KB = 1024.0
_MB = 1024.0 * 1024.0


class Meter:
    """One request's usage. ``start()`` before the handler, ``finish()`` after.

    Not reusable and not thread-safe: the worker handles one request at a time
    on one thread, and a shared meter would attribute one script's peak to
    another's build.
    """

    def __init__(self) -> None:
        self.t0 = time.perf_counter()
        self.cpu0 = _cpu_seconds()
        #: Whether the per-request peak really was reset (Linux only). The
        #: response's ``peak_rss_is_lifetime`` is this, inverted.
        self.peak_reset_ok = False

    def start(self) -> None:
        self.t0 = time.perf_counter()
        self.cpu0 = _cpu_seconds()
        self.peak_reset_ok = False
        if sys.platform == "linux":
            try:
                with open(CLEAR_REFS, "w", encoding="utf-8") as handle:
                    handle.write(CLEAR_REFS_PEAK_RESET)
                self.peak_reset_ok = True
            except OSError:
                # Not fatal, and not silent: the flag below says the peak is a
                # lifetime figure. (Under Landlock this needs the explicit
                # file rule `_confine` adds for exactly this write.)
                self.peak_reset_ok = False

    def finish(self) -> dict:
        wall_ms = (time.perf_counter() - self.t0) * 1000.0
        cpu_ms = max(0.0, (_cpu_seconds() - self.cpu0) * 1000.0)
        peak_mb, rss_mb, lifetime = _memory_mb(self.peak_reset_ok)
        return {
            "cpu_ms": round(cpu_ms, 3),
            "wall_ms": round(wall_ms, 3),
            "peak_rss_mb": None if peak_mb is None else round(peak_mb, 3),
            "rss_mb": None if rss_mb is None else round(rss_mb, 3),
            "peak_rss_is_lifetime": lifetime,
        }


# ------------------------------------------------------------------ the CPU

def _cpu_seconds() -> float:
    """User + system CPU of this process, in seconds."""
    if _resource is None:  # pragma: no cover - Windows
        return time.process_time()
    usage = _resource.getrusage(_resource.RUSAGE_SELF)
    return float(usage.ru_utime) + float(usage.ru_stime)


# --------------------------------------------------------------- the memory

def rusage_peak_mb() -> float | None:
    """``ru_maxrss`` as megabytes, in the unit this platform actually uses.

    macOS reports **bytes**, Linux **kibibytes** (`getrusage(2)`, and the BSD
    heritage behind the difference). Getting this wrong is a 1024x error in a
    number a memory quota is judged against.
    """
    if _resource is None:  # pragma: no cover - Windows
        return None
    maxrss = float(_resource.getrusage(_resource.RUSAGE_SELF).ru_maxrss)
    if sys.platform == "darwin":
        return maxrss / _MB
    return maxrss / _KB


def proc_status_mb(field: str) -> float | None:
    """One ``kB`` field of ``/proc/self/status`` (``VmHWM``, ``VmRSS``) in MB."""
    try:
        with open("/proc/self/status", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith(field + ":"):
                    return float(line.split()[1]) / _KB
    except (OSError, IndexError, ValueError):
        return None
    return None


def _memory_mb(peak_reset_ok: bool) -> tuple[float | None, float | None, bool]:
    """``(peak_rss_mb, rss_mb, peak_is_lifetime)`` for this platform."""
    if sys.platform == "linux":
        peak = proc_status_mb("VmHWM")
        rss = proc_status_mb("VmRSS")
        if peak is not None:
            return peak, rss, not peak_reset_ok
        return rusage_peak_mb(), rss, True
    if sys.platform == "darwin":
        return rusage_peak_mb(), _macos_rss_mb(), True
    counters = _windows_memory_counters()
    if counters is None:
        return None, None, True
    return counters[0] / _MB, counters[1] / _MB, True


def _macos_rss_mb() -> float | None:
    """Resident size now, via libproc — the same call the supervisor samples
    the worker with, pointed at this process."""
    from .sandbox_macos import MacBackend

    rss = MacBackend().rss_bytes(SimpleNamespace(pid=os.getpid()))
    return None if rss is None else rss / _MB


def _windows_memory_counters() -> tuple[float, float] | None:  # pragma: no cover
    """``(PeakWorkingSetSize, WorkingSetSize)`` in bytes, via psapi."""
    if sys.platform != "win32":
        return None
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
    except (OSError, AttributeError):
        return None
    # PROCESS_MEMORY_COUNTERS: cb, PageFaultCount (u32 each), then eight
    # size_t fields; PeakWorkingSetSize is the first of them.
    size = 8 + 8 * ctypes.sizeof(ctypes.c_size_t)
    buffer = ctypes.create_string_buffer(size)
    struct.pack_into("=I", buffer, 0, size)
    ok = psapi.GetProcessMemoryInfo(kernel32.GetCurrentProcess(), buffer, size)
    if not ok:
        return None
    fmt = "=Q" if ctypes.sizeof(ctypes.c_size_t) == 8 else "=I"
    peak = struct.unpack_from(fmt, buffer, 8)[0]
    working = struct.unpack_from(fmt, buffer, 8 + ctypes.sizeof(ctypes.c_size_t))[0]
    return float(peak), float(working)
