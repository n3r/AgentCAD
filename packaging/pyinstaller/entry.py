"""PyInstaller entry point for the AgentCAD bundle.

The bundled executable is the full `agentcad` CLI: `agentcad serve`,
`agentcad mcp`, etc. — plus the hidden `worker` subcommand, which is how the
frozen KernelClient re-execs this same executable as its kernel worker
subprocess (there is no `python` inside the bundle to run `-m
agentcad.kernel.worker`).
"""

import multiprocessing
import sys

from agentcad.cli import main

if __name__ == "__main__":
    # Standard PyInstaller hygiene: if any dependency ever spawns via
    # multiprocessing, the child re-exec of this binary must short-circuit
    # here instead of starting a second server. No-op otherwise.
    multiprocessing.freeze_support()
    sys.exit(main())
