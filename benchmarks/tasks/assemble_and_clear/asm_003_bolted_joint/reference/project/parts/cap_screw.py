# Copied from examples/fasteners/parts/cap_screw.py for bench task
# assemble_and_clear/asm_003_bolted_joint.
# A derived task copies the script INTO the bundle: the runner registers no
# examples, so a run can never read the answer.
# The rubric is injected from the bundle's specs/, so this script declares no
# SPECS of its own.
"""Socket-head cap screw (ISO 4762), built with agentcad.toolkit.threads.

A catalog fastener: the M8x1.25 size is fixed and only the length under the
head is parametric. It is built with a *simple* (cosmetic) thread, which is
what you want for assembly / fit views — fast and light. Switch to
``simple=False`` for a real ISO thread on a manufacturing drawing (far
heavier: roughly 9k triangles per thread).

Origin: the under-head bearing face is at local z = 0, the head rises to +z
and the threaded shank runs down to z = -length. In the assembly the screw is
placed with its bearing face on the clamp-plate top face.
"""

from build123d import *  # noqa: F401 — standard part-script preamble

from agentcad.toolkit import threads

SIZE = "M8-1.25"

PARAMS = {
    "length": {"default": 13.0, "min": 8.0, "max": 40.0, "unit": "mm",
               "description": "Screw length under the head"},
}


def build(p):
    return threads.cap_screw(SIZE, p.length, simple=True)
