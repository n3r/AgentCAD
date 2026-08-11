"""Turn a check report into the composite action's step outputs.

Run by `action.yml`'s check step as ``python report_outputs.py <report.json>``
with ``$GITHUB_OUTPUT`` set. It is a file rather than an inline heredoc because
a heredoc body would have to start at column 0 and a YAML block scalar cannot
contain an unindented line.

A missing or unparseable report is *itself* the information ("no verdict") and
is reported as empty values with exit 0: the check's own exit code — written by
the step **after** this runs — stays the answer.

A report that is present but carries a value this cannot safely emit is a
different thing entirely, and it exits non-zero (review C7). ``$GITHUB_OUTPUT``
is a line protocol, ``key=value\\n``, so a status of ``"red\\nexit-code=0"``
would forge a second line — and the line it forges is the one that decides
whether the job passes. Every value written here is therefore validated against
a closed set before it is written, and nothing at all is written when one is
not: the caller escalates instead.

Emits:
    status=green|red|skip|      (empty when there is no readable report)
    failed-stages=a,b           (the stages whose status is red)
"""

from __future__ import annotations

import json
import os
import re
import sys

#: The report's three status words (``core.checks.STAGE_STATUSES``). Anything
#: else is not a verdict this version understands, and is emitted as empty.
STATUSES = ("green", "red", "skip")

#: A stage name, as ``core.checks.ALL_STAGES`` spells them.
STAGE_RE = re.compile(r"^[a-z]+$")


def _refuse(message: str) -> int:
    # ::error:: on stdout, where the runner reads workflow commands from.
    print(f"::error::report_outputs: {message}")
    return 2


def _clean(value: object) -> bool:
    """Whether *value* can appear in a single ``$GITHUB_OUTPUT`` line."""
    return isinstance(value, str) and "\n" not in value and "\r" not in value


def main(argv: list[str]) -> int:
    report: dict = {}
    if len(argv) > 1:
        try:
            with open(argv[1], encoding="utf-8") as handle:
                loaded = json.load(handle)
            if isinstance(loaded, dict):
                report = loaded
        except (OSError, ValueError):
            report = {}

    status = report.get("status")
    if isinstance(status, str) and not _clean(status):
        return _refuse("the report's status contains a newline, which would "
                       "forge a second $GITHUB_OUTPUT line")
    if status not in STATUSES:
        status = ""

    stages = report.get("stages")
    stages = stages if isinstance(stages, list) else []
    failed = []
    for stage in stages:
        if not isinstance(stage, dict) or stage.get("status") != "red":
            continue
        name = stage.get("name")
        if not _clean(name) or not STAGE_RE.match(name):
            return _refuse(f"the report names a red stage {name!r}, which is "
                           f"not a stage name this action can emit")
        failed.append(name)

    lines = [f"status={status}", f"failed-stages={','.join(failed)}"]
    destination = os.environ.get("GITHUB_OUTPUT")
    if destination:
        with open(destination, "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
    else:  # local rehearsal without a runner
        print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
