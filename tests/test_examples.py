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

# 6, measured: at 4, round-robin clustered stud_set (177 s) with
# intake_manifold (139 s) into one ~410 s chunk that set the suite's wall
ENGINE_SWEEP_CHUNKS = 6

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
    @classmethod
    def service(cls, kernel, tmp_path_factory):
        # Class-scoped, not module-scoped: two classes over the same example
        # (engine core + sweep chunks) may share a worker, and one service
        # cannot open two projects with the same name.
        return make_test_service(tmp_path_factory.mktemp("projects"), kernel)

    @pytest.fixture(scope="class")
    @classmethod
    def example(cls, service, tmp_path_factory):
        # Copy the example into a temp dir first: the tests mutate params and
        # write caches, and we must never touch the committed example on disk.
        src = EXAMPLES_DIR / cls.EXAMPLE
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
