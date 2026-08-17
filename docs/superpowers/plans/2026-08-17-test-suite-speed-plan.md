# Test-Suite Wall-Clock Speedup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cut the full `make test` gate from 1609.9 s to ≤ ~600 s (predicted 450–545 s) on a 10-core machine by splitting the examples suite into `loadscope`-schedulable, per-part-parametrized classes and raising the xdist worker count.

**Architecture:** `tests/test_examples.py` is restructured from one parametrized module (one 1080 s scheduling unit) into per-example test classes plus four generated engine sweep-chunk classes; every part-level check is its own parametrized test, which is load-bearing — xdist's loadscope refills a worker at pending ≤ 2 *tests* and (v3.8, on by default) reorders the queue by test count descending, so 1-test classes would serialize on one worker at the queue tail. The Makefile's `PYTEST_PARALLEL` becomes `-n auto --maxprocesses=8 --dist loadscope` (overridable via `?=`). CI is untouched.

**Tech Stack:** pytest 9.1.1 (pyproject floor `>=8`), pytest-xdist 3.8 (`--maxprocesses` applies to `-n auto`, which resolves to physical cores via psutil), pytest-timeout, uv.

**Reviewed:** 22-agent adversarial workflow, 2026-08-17 — 14 confirmed findings folded in (see spec's review record).

## Global Constraints

- `make test` stays the complete gate: no test deleted, no marker tier changed; engine classes keep `exhaustive`, module keeps `integration, slow, timeout(900)`.
- Do not touch `.github/workflows/ci.yml` (spec: CI bump is follow-up, validated by CI itself; the split costs the nightly job ~137 s CPU of duplicated cold builds — accounted in the spec).
- Do not loosen any wall-clock budget assert (`test_sketch_bench.py` FR6, packet "warm under 10 s"). The `test_packages_gate` module `timeout(600)` is contention *headroom* in the existing convention, not a budget change.
- Copytree of examples must keep ignoring `.cache`/`exports`; committed examples are never touched.
- Every commit carries a `docs/changelog/NNNN-<slug>.md` entry (next number: 0185) and the `Co-Authored-By: Claude <noreply@anthropic.com>` trailer.
- Baseline (this machine, 2026-08-17): **3229 passed, 7 skipped in 1609.90 s** wall (`-n 2 --dist loadscope`); engine tests: sweep 713.97 s, defaults 137.41 s, STEP 80.27 s, interference 69.04 s. Example part counts: construction 3, engine 33, fasteners 3, prototyping 2, rocketry 4.

---

### Task 1: Restructure tests/test_examples.py into loadscope-schedulable, per-part classes

**Files:**
- Modify: `tests/test_examples.py` (full rewrite below)
- Modify: `tests/test_packages_gate.py` (add `pytest.mark.timeout(600)` to its module `pytestmark`)

**Interfaces:**
- Consumes: `tests/conftest.py::make_test_service(projects_dir, kernel, bus=None)`, session `kernel` fixture; `examples/*/project.json` manifests (`{"parts": [{"id": ...}, ...]}`).
- Produces: `TestConstruction/TestFasteners/TestPrototyping/TestRocketry` (build+assembly+sweep), `TestEngineCore` (build+assembly), generated `TestEngineSweep0..3`, `test_every_example_is_covered`. Task 2's Makefile change relies on these being separate loadscope units with > 2 tests each.

- [ ] **Step 1: Rewrite `tests/test_examples.py`** with this exact content:

```python
"""Integration tests over every bundled example project.

Each example under examples/*/project.json must rebuild all parts (valid,
positive volume) at defaults and at every param's min and max, pass an
interference check, and export the assembly as STEP.

Scheduling is deliberate: xdist ``--dist loadscope`` schedules per class
(not per module), so each example gets its own test class, and the huge
engine example additionally splits its extremes sweep into
``ENGINE_SWEEP_CHUNKS`` generated round-robin part-chunk classes. The
per-part parametrization is load-bearing, not cosmetic: loadscope refills a
worker whenever its pending-test count drops to <= 2 and (xdist 3.8,
default on) sorts the queue by test count descending, so a 1-test class
would sort to the queue tail and let a second engine unit pile onto the
same worker. ``test_every_example_is_covered`` derives coverage from the
classes themselves, so a new example, a deleted class, or a botched
rebalance goes red. The engine example is exhaustive scheduled coverage;
smaller examples remain in the per-PR suite.
"""

import json
import shutil
from pathlib import Path

import pytest

from .conftest import make_test_service

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"

EXAMPLE_DIRS = sorted(
    child for child in (EXAMPLES_DIR.iterdir() if EXAMPLES_DIR.is_dir() else [])
    if (child / "project.json").is_file()
)

ENGINE_SWEEP_CHUNKS = 4

pytestmark = [
    pytest.mark.integration,
    pytest.mark.slow,
    pytest.mark.skipif(not EXAMPLE_DIRS, reason="no example projects present yet"),
    # the engine example (63 instances, real thread geometry) legitimately
    # needs minutes for its extremes sweep, interference, and STEP export
    pytest.mark.timeout(900),
]


def _part_ids(example: str) -> list[str]:
    manifest = json.loads((EXAMPLES_DIR / example / "project.json").read_text())
    return sorted(entry["id"] for entry in manifest["parts"])


def pytest_generate_tests(metafunc):
    # Per-part parametrization, chunked by the class's PART_CHUNK/PART_OF.
    # Ids come from the committed project.json at collection time, so every
    # xdist worker collects the identical test ids (a hard xdist requirement).
    if "part_id" in metafunc.fixturenames and metafunc.cls is not None:
        cls = metafunc.cls
        ids = _part_ids(cls.EXAMPLE)[cls.PART_CHUNK :: cls.PART_OF]
        metafunc.parametrize("part_id", ids)


class _ExampleBase:
    EXAMPLE: str  # subclasses pin the examples/<name> directory to open
    PART_CHUNK = 0  # this class covers sorted(part_ids)[PART_CHUNK::PART_OF]
    PART_OF = 1

    @pytest.fixture(scope="class")
    def service(self, kernel, tmp_path_factory):
        # Class-scoped, not module-scoped: two classes over the same example
        # (engine core + sweep chunks) may share a worker, and one service
        # cannot open two projects with the same name.
        return make_test_service(tmp_path_factory.mktemp("projects"), kernel)

    @pytest.fixture(scope="class")
    def example(self, service, tmp_path_factory):
        # Copy the example into a temp dir first: the tests mutate params and
        # write caches, and we must never touch the committed example on disk.
        src = EXAMPLES_DIR / self.EXAMPLE
        assert (src / "project.json").is_file(), f"missing example {src}"
        dest = tmp_path_factory.mktemp("ex") / src.name
        shutil.copytree(src, dest, ignore=shutil.ignore_patterns(".cache", "exports"))
        detail = service.open_project(str(dest))
        return service, detail["name"]


class _BuildAndAssemblyTests(_ExampleBase):
    def test_part_builds_valid_at_defaults(self, example, part_id):
        service, name = example
        detail = service.get_part(name, part_id)
        assert detail["status"]["state"] == "ok", (
            f"{name}/{part_id}: {detail['status']['error']}"
        )
        assert detail["metrics"]["volume_mm3"] > 0
        if detail.get("kind") == "reference":
            return  # imported mesh/B-rep: no script, no param spec
        assert detail["metrics"]["is_valid"] is True
        assert detail["params_spec"], f"{name}/{part_id} has no PARAMS"
        for pname, spec in detail["params_spec"].items():
            assert spec["description"], f"{name}/{part_id}.{pname} missing description"
            if spec.get("type") in (None, "number", "int"):
                assert spec["min"] is not None, f"{name}/{part_id}.{pname} missing min"
                assert spec["max"] is not None, f"{name}/{part_id}.{pname} missing max"
                assert spec["unit"], f"{name}/{part_id}.{pname} missing unit"

    def test_assembly_present_and_interference_clean(self, example):
        service, name = example
        assembly = service.get_assembly(name)
        assert len(assembly["instances"]) >= 2, f"{name}: assembly needs >= 2 instances"
        assert all(i["state"] == "ok" for i in assembly["instances"])
        result = service.check_interference(name)
        assert result["pairs"] == [], f"{name}: interference {result['pairs']}"

    def test_assembly_exports_step(self, example):
        service, name = example
        result = service.export_assembly(name, "step")
        assert result["size_bytes"] > 1000


class _SweepTest(_ExampleBase):
    def test_part_builds_at_param_extremes(self, example, part_id):
        service, name = example
        project = service.get_project(name)
        entry = next(p for p in project["parts"] if p["id"] == part_id)
        detail = service.get_part(name, part_id)
        if detail.get("kind") == "reference":
            return  # no params to sweep
        baseline = dict(entry["params"])
        for pname, spec in detail["params_spec"].items():
            ptype = spec.get("type") or "number"
            if ptype in ("number", "int"):
                sweep = (spec["min"], spec["max"])
            elif ptype == "bool":
                sweep = (True, False)
            elif ptype == "enum":
                sweep = tuple(spec["choices"])
            else:  # string: only the default is guaranteed buildable
                sweep = (spec["default"],)
            for value in sweep:
                result = service.set_params(name, part_id, {pname: value})
                assert result["ok"], (
                    f"{name}/{part_id}.{pname}={value}: {result.get('error')}"
                )
                assert result["metrics"]["volume_mm3"] > 0
            result = service.set_params(
                name, part_id, {pname: baseline.get(pname, spec["default"])}
            )
            assert result["ok"], (
                f"{name}/{part_id}.{pname} restore to baseline failed: "
                f"{result.get('error')}"
            )


# Base order is load-bearing: pytest collects inherited tests in reverse MRO,
# so (_SweepTest, _BuildAndAssemblyTests) runs defaults/interference/STEP
# before the sweep, as the old single-module ordering did.
class TestConstruction(_SweepTest, _BuildAndAssemblyTests):
    EXAMPLE = "construction"


class TestFasteners(_SweepTest, _BuildAndAssemblyTests):
    EXAMPLE = "fasteners"


class TestPrototyping(_SweepTest, _BuildAndAssemblyTests):
    EXAMPLE = "prototyping"


class TestRocketry(_SweepTest, _BuildAndAssemblyTests):
    EXAMPLE = "rocketry"


@pytest.mark.exhaustive
class TestEngineCore(_BuildAndAssemblyTests):
    EXAMPLE = "engine"


# Generated so a rebalance is a one-constant change that can never silently
# drop a chunk; test_every_example_is_covered pins the tiling.
for _chunk in range(ENGINE_SWEEP_CHUNKS):
    globals()[f"TestEngineSweep{_chunk}"] = type(
        f"TestEngineSweep{_chunk}",
        (_SweepTest,),
        {
            "EXAMPLE": "engine",
            "PART_CHUNK": _chunk,
            "PART_OF": ENGINE_SWEEP_CHUNKS,
            "pytestmark": [pytest.mark.exhaustive],
        },
    )
del _chunk


def _classes(base):
    return [
        cls
        for cls in globals().values()
        if isinstance(cls, type)
        and issubclass(cls, base)
        and cls.__name__.startswith("Test")
    ]


def test_every_example_is_covered():
    """A new example, a deleted class, or a botched rebalance must go red."""
    on_disk = {path.name for path in EXAMPLE_DIRS}
    assert on_disk == {cls.EXAMPLE for cls in _classes(_BuildAndAssemblyTests)}
    assert on_disk == {cls.EXAMPLE for cls in _classes(_SweepTest)}
    tiling = {
        (cls.PART_CHUNK, cls.PART_OF)
        for cls in _classes(_SweepTest)
        if cls.EXAMPLE == "engine"
    }
    assert tiling == {(i, ENGINE_SWEEP_CHUNKS) for i in range(ENGINE_SWEEP_CHUNKS)}
    for path in EXAMPLE_DIRS:
        # per-part parametrize would turn an empty manifest into silent skips
        assert _part_ids(path.name), f"{path.name}: example has no parts"
```

- [ ] **Step 2: Add contention headroom to `tests/test_packages_gate.py`** — its 55 s setup-heavy test sits under the global 120 s pytest-timeout. In its module `pytestmark` list (near line 37, where `pytest.mark.slow` is applied), add:

```python
pytest.mark.timeout(600),
```

with the comment `# headroom against 8-worker contention; the 120 s default is a 2-worker number`.

- [ ] **Step 3: Verify collection arithmetic** (part counts: 3+33+3+2+4):

Run: `uv run pytest tests/test_examples.py --collect-only -q | tail -3`
Expected: **101 tests** (1 coverage + small classes 8+8+6+10 + TestEngineCore 35 + sweeps 33).

Run: `uv run pytest tests/test_examples.py --collect-only -q -m "not exhaustive" | tail -3`
Expected: **33 tests** (engine's 68 excluded).

- [ ] **Step 4: Verify collection ORDER inside a combined class** (the reverse-MRO assumption):

Run: `uv run pytest "tests/test_examples.py::TestPrototyping" --collect-only -q`
Expected: `test_part_builds_valid_at_defaults[...]` ids listed BEFORE `test_part_builds_at_param_extremes[...]`. If not, swap the base order on the four combined classes and re-check.

- [ ] **Step 5: Run the non-engine examples for real** (rocketry ≈ 60 s dominates):

Run: `uv run pytest tests/test_examples.py -n 4 --dist loadscope -m "not exhaustive" -q`
Expected: 33 passed; proves class-scoped `service`/`example` fixtures and `pytest_generate_tests` work under xdist.

### Task 2: Raise the parallelism default and update docs

**Files:**
- Modify: `Makefile:3` — `PYTEST_PARALLEL = -n 2 --dist loadscope` → `PYTEST_PARALLEL ?= -n auto --maxprocesses=8 --dist loadscope`
- Modify: `AGENTS.md` testing bullet (near line 1461, "Two xdist workers run by module scope…") AND the quick-start command comments near lines 28/30 ("two-worker suite")
- Modify: `README.md:150` ("two-worker suite")

**Interfaces:**
- Consumes: Task 1's class split (without it, 8 workers hit the 1080 s module ceiling).
- Produces: `make test`/`test-fast`/`test-pr`/`test-portability` inherit the new default; `PYTEST_PARALLEL` env/make-var override.

- [ ] **Step 1: Edit Makefile line 3** to:

```make
# ?= so a small machine (or CI) can override: make test PYTEST_PARALLEL="-n 2 --dist loadscope"
PYTEST_PARALLEL ?= -n auto --maxprocesses=8 --dist loadscope
```

- [ ] **Step 2: Update AGENTS.md** — replace the two-worker testing bullet with (adjust to the exact existing text found):

```markdown
- xdist workers (`-n auto`, physical cores, capped at 8 — override via
  `PYTEST_PARALLEL`) run by `loadscope`: one scheduling unit per module, or
  per class where a module defines test classes. Each worker holds one
  session-scoped `kernel` fixture (`tests/conftest.py`) that amortizes the
  warm import. `tests/test_examples.py` is deliberately class-per-example
  with the engine sweep split into generated part-chunk classes, each
  per-part parametrized — 1-test classes would defeat xdist's refill
  watermark and queue reorder (see the module docstring) — so the examples
  spread across workers instead of pinning ~18 minutes to one.
```

and rewrite the two quick-start comments (lines ~28/30) to describe the auto-scaled suite instead of "two-worker suite".

- [ ] **Step 3: Zero-stale-hits gate**:

Run: `grep -rniE "two.worker|-n 2" AGENTS.md README.md Makefile`
Expected: no hits (except any historical-changelog quotes inside AGENTS.md, which there are none of today).

- [ ] **Step 4: Sanity-run a cheap target**:

Run: `make test-fast 2>&1 | tail -3`
Expected: green (`-m "not slow"` tier), header shows 8 workers (gw0..gw7).

### Task 3: Full verification, twice, with contention watch

**Files:** none (measurement only)

- [ ] **Step 1: Full gate, run 1**:

Run: `PYTEST_ADDOPTS="--durations=40" /usr/bin/time make test 2>&1 | tail -60`
Expected: **3310 passed** (3229 − 20 old examples + 101 new), 7 skipped, wall ≤ ~600 s.

- [ ] **Step 2: Full gate, run 2** (flake + variance watch): same command.
Expected: same counts; wall within ~15 % of run 1. In BOTH runs confirm zero failures/timeouts in: `test_sketch_bench.py` (FR6 16 ms/250 ms), `test_packet.py::…warm_under_ten_seconds`, `test_prd002_acceptance.py::test_ac2_packet_generates_warm_under_10s`, and grep the output for `Failed: Timeout` / `Timeout >` — any timeout kill of a previously-passing test is a contention flake.

- [ ] **Step 3: If a budget test or timeout flaked** — apply the ladder in order, re-running Steps 1–2 after each rung: (a) `--maxprocesses=6`; (b) keep 8 for the bulk but add a serial tail phase to `test-full` (`uv run pytest -q tests/test_sketch_bench.py tests/test_packet.py tests/test_prd002_acceptance.py` after the parallel run, with those files `--ignore`d in the parallel invocation — noting the tail adds its own ~100 s wall). Record which rung was needed.

- [ ] **Step 4: If a sweep chunk exceeds ~286 s** total (sum its per-part durations from `--durations=40`), bump `ENGINE_SWEEP_CHUNKS` (classes are generated; the coverage test pins the tiling) and re-run Task 1 Step 3 expecting the same 101/33 counts, then re-verify.

### Task 4: Changelog and commit

**Files:**
- Create: `docs/changelog/0185-test-suite-parallel-speedup.md` (follow `docs/changelog/README.md` template; summarize measured before/after, the class split and why per-part parametrization is load-bearing, the Makefile default, the override knob, the packages_gate timeout headroom, CI untouched + the nightly-CI cost accounting, the review record, budget-test watch outcome)
- Include: spec + plan docs, `tests/test_examples.py`, `tests/test_packages_gate.py`, `Makefile`, `AGENTS.md`, `README.md`

- [ ] **Step 1: Write the changelog entry from the actual diff** with the real measured numbers from Task 3.
- [ ] **Step 2: Commit everything as one change**:

```bash
git add tests/test_examples.py tests/test_packages_gate.py Makefile AGENTS.md README.md docs/changelog/0185-test-suite-parallel-speedup.md docs/superpowers/specs/2026-08-17-test-suite-speed-design.md docs/superpowers/plans/2026-08-17-test-suite-speed-plan.md
git commit -m "Split examples into per-part loadscope classes; raise xdist workers

Full gate: 1609.9s -> <measured>s on 10 cores. test_examples.py was one
1080s scheduling unit; the engine sweep now runs as four generated
part-chunk classes, per-part parametrized to stay above xdist's refill
watermark and queue reorder.

Co-Authored-By: Claude <noreply@anthropic.com>"
```

## Execution notes (deviations, with measurements)

- Task 3 Step 4's rebalance rung fired on run 1: **3310 passed, 7 skipped,
  630.97 s** — green, counts exact, no budget/timeout flakes, but above the
  ≤ 600 s bar. `--durations=40` showed why: round-robin at K=4 clustered
  stud_set (177.0 s) and intake_manifold (139.1 s) into one ~410 s Sweep3
  unit, and xdist's default count-descending reorder started it mid-queue —
  it ended exactly at the wall. Fix: `ENGINE_SWEEP_CHUNKS` 4 → 6 (one
  constant, tiling assert keeps it honest) and `--no-loadscope-reorder`
  appended to `PYTEST_PARALLEL` (queue runs in collection order, so
  `test_examples.py` starts early). Collection stays 101/33.
- pytest 9 deprecates class-scoped fixtures as instance methods; `service`/
  `example` are `@classmethod` fixtures (warning-free).

## Self-review notes

- Spec coverage: design §1 → Task 1 Step 1; §2 → Task 2; §3 → Task 1 Step 2; §4 → Task 2 Steps 2–3; risk gates → Task 3; changelog → Task 4. Follow-ups (CI bump, second-tier W-reduction) intentionally have no task.
- All 14 confirmed review findings are addressed: watermark/reorder (per-part parametrize), literal coverage set (class-derived + tiling), chunk generation (type() loop), restore assert, reverse-MRO base order (+ Step 4 empirical check), packages_gate timeout, stale-doc grep gate incl. AGENTS.md:28/30 + README:150, corrected predictions/counts, timeout-kill watch, rebalance-without-dropping (generated classes), pytest version wording.
- Names consistent: `PART_CHUNK`/`PART_OF`, `ENGINE_SWEEP_CHUNKS`, `_part_ids`, `_classes`, class-scoped `service`/`example`.
