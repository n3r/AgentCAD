"""AgentCAD-Bench: kernel-scored agentic-CAD evaluations (PRD-024).

OCP-free by contract: nothing in this package may import build123d or OCP.
The only geometry this feature adds lives in agentcad/kernel/handlers/bench.py.
"""
from __future__ import annotations

#: The scorer's own version. Bump it whenever a subscore's computation changes:
#: two scores are comparable only when (task_set, task_version, harness) agree.
HARNESS_VERSION = 1
