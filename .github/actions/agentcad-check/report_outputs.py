"""Turn a check report into the composite action's step outputs.

Run by `action.yml`'s check step as ``python report_outputs.py <report.json>``
with ``$GITHUB_OUTPUT`` set. It is a file rather than an inline heredoc because
a heredoc body would have to start at column 0 and a YAML block scalar cannot
contain an unindented line.

It never fails the step: a missing or unparseable report is *itself* the
information ("no verdict"), and the check's own exit code — saved before this
runs — stays the answer.

Emits:
    status=green|red|skip|      (empty when there is no readable report)
    failed-stages=a,b           (the stages whose status is red)
"""

from __future__ import annotations

import json
import os
import sys


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

    stages = report.get("stages")
    stages = stages if isinstance(stages, list) else []
    failed = ",".join(
        str(stage.get("name"))
        for stage in stages
        if isinstance(stage, dict) and stage.get("status") == "red"
    )
    status = report.get("status")
    status = status if isinstance(status, str) else ""

    lines = [f"status={status}", f"failed-stages={failed}"]
    destination = os.environ.get("GITHUB_OUTPUT")
    if destination:
        with open(destination, "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
    else:  # local rehearsal without a runner
        print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
