"""Tool pack: ``get_usage`` — kernel resource roll-ups (PRD-006, Decision 6).

The same numbers ``/api/health`` publishes, reachable from an agent surface
and with a ``since`` window, so "why is this instance slow" is answerable
without shell access to the box.

The meter is installed by ``cli._build_service`` (it has to exist *before* the
kernel, because it is the kernel's ``on_usage`` hook). A service built any
other way — a library caller, ``tests/conftest.make_test_service``, a check
runner's ephemeral service — has none, and this tool answers an **empty**
snapshot rather than a 500: "nothing has been metered here" is the truth, and
an AttributeError is not a better way to say it.
"""

from __future__ import annotations

from . import usage as usage_module
from .tools import Tool, schema


def register(registry, service) -> None:
    def get_usage(project: str | None = None,
                  since: float | None = None) -> dict:
        # Read off the service at CALL time, never captured at registration:
        # the CLI sets `service.usage` after `build_registry` in one command
        # and before it in another.
        meter = getattr(service, "usage", None)
        if meter is None:
            meter = usage_module.UsageMeter()
        return meter.snapshot(project, since)

    registry.register(Tool(
        "get_usage",
        "Kernel resource usage roll-ups (CPU ms, wall ms, peak RSS) per "
        "project and per client identity, since an optional unix time. These "
        "are measurements, not limits: the caps that refuse work are the "
        "kernel's quotas (a breach arrives as a kernel_crash with "
        "details.reason) and the per-project disk budget. Only this server "
        "process is counted, and only the most recent requests are retained — "
        "the answer says so when a 'since' reaches past them.",
        schema({
            "project": {"type": "string",
                        "description": "Only this project's usage"},
            "since": {"type": "number",
                      "description": "Unix time; omit for everything this "
                                     "server has metered"},
        }, []),
        get_usage,
    ))
