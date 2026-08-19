"""Resource quotas for kernel workers: the knobs, their defaults, the resolver.

A part script is arbitrary Python inside the worker, so "how much of this
machine may it take" is configuration, not a constant. This module owns that
configuration and nothing else: it computes a frozen :class:`Quotas` in the
**server** process, once, and hands it to the kernel client at construction,
so every worker — and every respawn of a killed one — is capped identically.
Which mechanism actually enforces a knob is the platform backend's business
(``sandbox_macos``/``sandbox_linux``/``sandbox_windows``); a knob resolved
here may be enforced by a cgroup, an rlimit, a job object or the supervisor,
and health names the tier in effect rather than promising one.

Deliberately importable from server code: no ``OCP``/build123d, and no OS
call — this is a dict, a dataclass and a parser.

**Layering** (FR12), lowest to highest:

1. :data:`DEFAULTS` — the measured floors (see the design spec, Decision 3: a
   warm worker is 451-482 MB RSS and 1.3-1.9 GiB of address space, so 2048 MB
   of RSS and 3x that of address space are caps a real build never meets).
2. the instance config file, ``~/.agentcad/config.json`` ``{"quotas": {...}}``.
3. ``AGENTCAD_QUOTA_<KNOB>`` environment variables — env wins over the file so
   a container can cap a deployment without editing a mounted config.
4. the ``overrides`` argument — the slot PRD-005's per-tenant limits plug into
   without a signature change.

**``pids_headroom`` is not a process cap** — it is the *slack* above what the
uid is already running. ``RLIMIT_NPROC`` is a per-uid ceiling that the kernel
checks against the calling process's own limit, so the backends compute it as
``live uid task count, measured at each spawn + pids_headroom x pool_size``.
What that bounds is the *extra* tasks a fork bomb can create: at most
``pids_headroom x pool_size`` of them across the whole pool. Scaling by the
pool size is not generosity — without it the third worker of a three-worker
pool forks into a budget its siblings have already spent and dies inside
``import build123d`` (measured, review C2). The hard per-worker process cap is
``pids``, and that one is the cgroup's.

**Values** are numbers, or ``"off"``/``0`` to switch a knob off (``0`` on
``address_space_mb`` means *auto*, not off — see :func:`resolve`). An unknown
key is ignored at every layer; a value that is not a number is a
:class:`ValueError` naming both the key and the layer it came from, because
the reader is an operator staring at a server that will not start.
"""

from __future__ import annotations

import math
import os
from dataclasses import asdict, dataclass
from typing import Any, Mapping

#: knob -> default. Also the inventory: a key absent here is unknown at every
#: layer, and every key here is settable from every layer.
DEFAULTS: dict[str, Any] = {
    "memory_mb": 2048,          # cgroup memory.max / supervisor cap / job-object commit
    "address_space_mb": 0,      # 0 = auto (3 x memory_mb); Linux RLIMIT_AS only
    "pids": 128,                # cgroup pids.max / job-object active processes
    "pids_headroom": 64,        # RLIMIT_NPROC = live uid count at spawn + headroom x pool size
    "cpu_percent": 400,         # cgroup cpu.max / job-object rate; None -> no CPU quota
    "sample_interval_s": 0.25,  # supervisor
    "disk_mb": 2048,            # per-project budget (.cache + exports + imports)
}

#: ``AGENTCAD_QUOTA_MEMORY_MB=4096``.
ENV_PREFIX = "AGENTCAD_QUOTA_"

#: Knobs that count something whole (megabytes, processes, percent). Only
#: ``sample_interval_s`` is a duration, and only it may be fractional.
_INT_KEYS = frozenset(DEFAULTS) - {"sample_interval_s"}

#: The one string that switches a knob off, at any layer.
OFF = "off"


@dataclass(frozen=True)
class Quotas:
    """The resolved caps. Frozen: they are read at every spawn and on every
    supervisor sample, and a mutable copy would be a way to raise a cap at
    runtime from inside the process the cap exists to contain.

    ``0`` on any cap (and ``None`` on ``cpu_percent``) means *that knob is
    off*, never "cap at zero" — the backends check before they apply.
    """

    memory_mb: int
    address_space_mb: int   # resolved (never 0 unless disabled by an explicit "off")
    pids: int
    pids_headroom: int
    cpu_percent: int | None
    sample_interval_s: float
    disk_mb: int

    def limits(self) -> dict:
        """What health publishes: every cap a script can breach.

        ``sample_interval_s`` is deliberately absent — it is how often the
        supervisor *looks*, not a limit anyone is subject to, and publishing
        it as one would invite reading a 0.25 next to six caps as a cap.
        """
        data = asdict(self)
        data.pop("sample_interval_s", None)
        return data


def resolve(overrides: Mapping[str, Any] | None = None, *,
            env: Mapping[str, str] | None = None,
            config: Mapping[str, Any] | None = None) -> Quotas:
    """Resolve the four layers into one :class:`Quotas`.

    *env* defaults to ``os.environ`` and *config* to the loaded instance
    config file; pass either explicitly (including ``{}``) to resolve without
    an ambient layer. *overrides* is the highest layer.

    Two knobs read a value specially:

    * ``cpu_percent`` — ``0`` or ``"off"`` resolves to ``None``: no CPU quota
      at all (which is what macOS always gets; ``RLIMIT_CPU`` is lifetime
      cumulative and the wall-clock timeout is the real backstop).
    * ``address_space_mb`` — ``0`` means **auto**: ``3 x memory_mb``. It is a
      deliberately loose Linux-only ``RLIMIT_AS`` that turns a runaway virtual
      reservation into a recoverable ``MemoryError``, not a cap, so "unset"
      has to track memory rather than switch it off. An explicit ``"off"``
      resolves it to ``0`` and the backend applies no ``RLIMIT_AS``.
    """
    env = os.environ if env is None else env
    if config is None:
        from ..config import load_config

        config = load_config()

    # None in `values` means "explicitly switched off", which is not the same
    # as 0 for address_space_mb — hence the sentinel rather than a plain zero.
    values: dict[str, Any] = dict(DEFAULTS)

    block = config.get("quotas") if isinstance(config, Mapping) else None
    if isinstance(block, Mapping):
        for key in DEFAULTS:
            if key in block:
                values[key] = _coerce(key, block[key], "config file")

    for key in DEFAULTS:
        name = f"{ENV_PREFIX}{key.upper()}"
        raw = env.get(name)
        if raw is None or not str(raw).strip():
            continue  # an unset or blank variable is not a setting
        values[key] = _coerce(key, raw, f"env {name}")

    if isinstance(overrides, Mapping):
        for key in DEFAULTS:
            if key in overrides:
                values[key] = _coerce(key, overrides[key], "overrides")

    memory_mb = int(values["memory_mb"] or 0)
    space = values["address_space_mb"]
    if space is None:            # explicit "off"
        address_space_mb = 0
    elif not space:              # 0 -> auto, tracking the resolved memory cap
        address_space_mb = 3 * memory_mb
    else:
        address_space_mb = int(space)
    cpu = values["cpu_percent"]
    return Quotas(
        memory_mb=memory_mb,
        address_space_mb=address_space_mb,
        pids=int(values["pids"] or 0),
        pids_headroom=int(values["pids_headroom"] or 0),
        cpu_percent=None if not cpu else int(cpu),
        sample_interval_s=float(values["sample_interval_s"] or 0.0),
        disk_mb=int(values["disk_mb"] or 0),
    )


def enforcement(quotas: Quotas, tiers: list[str]) -> dict:
    """The quotas half of the sandbox status object, from the tiers a backend
    can actually apply: ``{"status", "mechanism", "limits"}``.

    ``status`` is ``active`` only when *something* enforces something. A
    backend that resolved caps but has no tier to put them behind reports
    ``off`` and publishes the limits anyway, so health never claims a cap that
    nothing is watching (design spec, Decision 8). ``mechanism`` names the
    tiers in tier order, joined with ``+`` (``"cgroup+supervisor"``,
    ``"rlimit+supervisor"``, ``"supervisor"``, ``"job_object+supervisor"``).
    """
    return {
        "status": "active" if tiers else "off",
        "mechanism": "+".join(tiers) if tiers else None,
        "limits": quotas.limits(),
    }


def _coerce(key: str, raw: Any, layer: str) -> int | float | None:
    """One value from one layer -> a number, or ``None`` for "switched off"."""
    if raw is None:
        return None
    if isinstance(raw, str):
        text = raw.strip()
        if text.lower() == OFF:
            return None
        try:
            number = float(text)
        except ValueError:
            raise ValueError(_refusal(key, raw, layer)) from None
    elif isinstance(raw, bool) or not isinstance(raw, (int, float)):
        # `True` is an `int` in Python, so `{"cpu_percent": true}` in a config
        # file would otherwise cap CPU at 1%.
        raise ValueError(_refusal(key, raw, layer))
    else:
        number = float(raw)
    if not math.isfinite(number):
        raise ValueError(_refusal(key, raw, layer))
    if number < 0:
        raise ValueError(
            f"quota {key!r} ({layer}) must not be negative: {raw!r}. "
            f'Use 0 or "off" to switch the knob off.')
    if key in _INT_KEYS:
        if not number.is_integer():
            raise ValueError(
                f"quota {key!r} ({layer}) must be a whole number: {raw!r}")
        return int(number)
    return number


def _refusal(key: str, raw: Any, layer: str) -> str:
    return (f"quota {key!r} ({layer}) must be a number or \"off\", "
            f"not {raw!r}. Known quotas: {', '.join(sorted(DEFAULTS))}.")
