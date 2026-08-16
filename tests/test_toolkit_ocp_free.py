"""PRD-010 slice 1 — the OCP-free assertion scaffold for `agentcad/toolkit/`.

Only `agentcad/kernel/` may import `OCP`/build123d; the server process must
never. Most toolkit modules are kernel-side (they *are* part-script geometry
vocabulary), but a few run in the **server**: `sketch.py` solves constraints
for the browser, `specs.py` builds declarations, and PRD-010 adds a third,
`hole_standards.py`, because the server's `hole_standards` tool reads the
tables. A module that quietly grows a `build123d` import stops being loadable
there, and the failure surfaces far from the cause — so each one is imported in
a **fresh interpreter with `OCP`/`build123d` blocked at `sys.meta_path`** and
asserted to load anyway.

Adding a module is one line in `OCP_FREE`. `test_ocp_free_list_matches_the_tree`
is what stops the list from silently drifting: it classifies every toolkit
module by its imports and demands the two sets agree, so a new OCP-free module
cannot be added without a probe and a kernel-side module cannot be listed here
by mistake.
"""

import re
import subprocess
import sys
from pathlib import Path

import pytest

TOOLKIT = Path(__file__).resolve().parents[1] / "agentcad" / "toolkit"

# module -> a smoke expression over `mod` that must hold once it is imported.
# The expression is what makes this a real load rather than a bare `import`:
# a module whose dependencies are missing can import and then fail on use.
OCP_FREE: dict[str, str] = {
    "agentcad.toolkit": '"specs" in mod.__all__',
    "agentcad.toolkit.hole_standards": 'mod.clearance("M5")["d"] == 5.5',
    "agentcad.toolkit.sketch": "mod.Sketch is not None",
    "agentcad.toolkit.specs": "mod.SPEC_FORMAT == 1",
}

_PROBE = '''
import importlib
import sys


class _Blocked:
    """Refuse OCP/build123d so an accidental kernel import is a hard error."""

    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in ("OCP", "build123d"):
            raise ImportError("blocked kernel import: " + name)
        return None


sys.meta_path.insert(0, _Blocked())
mod = importlib.import_module({module!r})
assert {expr}, "smoke expression failed: " + {expr_msg!r}
assert "OCP" not in sys.modules and "build123d" not in sys.modules
print("ok")
'''

# A kernel-side toolkit module is one whose source imports the geometry kernel.
_KERNEL_IMPORT = re.compile(
    r"^\s*(?:from|import)\s+(?:OCP|build123d)\b", re.MULTILINE)


def _module_name(path: Path) -> str:
    if path.name == "__init__.py":
        return "agentcad.toolkit"
    return f"agentcad.toolkit.{path.stem}"


@pytest.mark.integration
@pytest.mark.portability
@pytest.mark.parametrize("module", sorted(OCP_FREE))
def test_module_imports_with_no_geometry_kernel_available(module):
    repo = Path(__file__).resolve().parents[1]
    probe = _PROBE.format(module=module, expr=OCP_FREE[module],
                          expr_msg=OCP_FREE[module])
    proc = subprocess.run([sys.executable, "-c", probe], cwd=repo,
                          capture_output=True, text=True, timeout=180)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip().endswith("ok")


def test_ocp_free_list_matches_the_tree():
    """The list above is the inventory, and it must stay the whole inventory.

    A new server-side toolkit module added without a probe, or a listed module
    that has grown a kernel import, both fail here — with the module named —
    rather than at some later import in the server process.
    """
    free, kernel_side = set(), set()
    for path in sorted(TOOLKIT.glob("*.py")):
        target = kernel_side if _KERNEL_IMPORT.search(
            path.read_text(encoding="utf-8")) else free
        target.add(_module_name(path))
    assert free == set(OCP_FREE), (
        f"toolkit modules with no kernel import but no probe: "
        f"{sorted(free - set(OCP_FREE))}; listed here but kernel-side: "
        f"{sorted(set(OCP_FREE) - free)}"
    )
    # Sanity: the classifier finds the kernel-side modules it should, so an
    # empty `free` set can never be mistaken for agreement.
    assert {"agentcad.toolkit.fillet", "agentcad.toolkit.sheetmetal"} <= kernel_side
