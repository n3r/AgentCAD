"""PRD-011 — every module under `agentcad/core/packages/` is OCP-free.

Only `agentcad/kernel/` may import `OCP`/build123d; the server process must
never. The package subsystem is server-process code end to end — it reads
manifests, hashes trees, talks to indexes and (from slice 4) *sequences*
kernel calls through a service, which is not the same as importing geometry.
A module that quietly grows a `build123d` import stops being loadable there
and the failure surfaces far from the cause.

So each module is imported in a **fresh interpreter with `OCP`/`build123d`
blocked at `sys.meta_path`** (the `tests/test_checks.py::_NO_KERNEL_PROBE`
pattern) and asserted to load anyway, with a smoke expression that makes it a
real load rather than a bare import.

`test_the_probe_list_matches_the_tree` is what stops the list from drifting:
**every** module in the subpackage must have a probe, because — unlike
`agentcad/toolkit/`, which is a mix — there is no such thing as a kernel-side
module here. `__init__.py` carries no code and needs no probe of its own: it
is executed by every one of these imports.
"""

import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PACKAGES = REPO / "agentcad" / "core" / "packages"

# module -> a smoke expression over `mod` that must hold once it is imported.
OCP_FREE: dict[str, str] = {
    "agentcad.core.packages.content": "mod.MAX_FILES == 500",
    "agentcad.core.packages.format": 'mod.satisfies("1.9.0", "^1.2.3") is True',
    "agentcad.core.packages.cache":
        'mod.receipt_path("iso4762", "1.0.0").name == "1.0.0.json"',
    "agentcad.core.packages.lockfile": 'mod.LOCK_KEY == "packages_lock"',
    # `gate` sequences kernel calls *through a service* and imports
    # `KernelError` from `kernel.client` — the module that spawns workers, not
    # one that imports geometry — exactly as `checks.py` does.
    "agentcad.core.packages.gate":
        'mod.GATE_STAGES[0] == "format" and len(mod.GATE_STAGES) == 9',
    "agentcad.core.packages.indexes": 'mod.LocalIndex.kind == "local"',
    "agentcad.core.packages.manager":
        "mod.PackageManager(None, indexes=[]).indexes == []",
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


@pytest.mark.integration
@pytest.mark.portability
@pytest.mark.parametrize("module", sorted(OCP_FREE))
def test_the_module_imports_with_no_geometry_kernel_available(module):
    expr = OCP_FREE[module]
    source = _PROBE.format(module=module, expr=expr, expr_msg=expr)
    proc = subprocess.run(
        [sys.executable, "-c", source],
        cwd=REPO, capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip().endswith("ok")


def test_the_probe_list_matches_the_tree():
    """A new module in the subpackage needs a probe. There is no kernel-side
    module here, so the two sets are equal — not merely overlapping."""
    on_disk = set()
    for path in sorted(PACKAGES.glob("*.py")):
        if path.name == "__init__.py":
            continue
        on_disk.add(f"agentcad.core.packages.{path.stem}")
    assert on_disk == set(OCP_FREE), (
        "modules without a probe: " + str(sorted(on_disk - set(OCP_FREE)))
        + "; probes for modules that do not exist: "
        + str(sorted(set(OCP_FREE) - on_disk))
    )


def test_no_module_in_the_subpackage_imports_the_geometry_kernel_in_source():
    """A cheap static backstop for the probes above: it also catches an
    import hidden inside a function, which a top-level import check would
    miss and which would only fail at call time."""
    offenders = []
    for path in sorted(PACKAGES.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith(("import ", "from ")) and (
                "build123d" in stripped or stripped.split()[1].split(".")[0] == "OCP"
            ):
                offenders.append(f"{path.name}: {stripped}")
    assert offenders == []
