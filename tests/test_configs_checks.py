"""PRD-012 slice 6 (FR6) — one CI build row per configuration.

The build stage grows ``part@config`` rows and nothing else: no new stage, no
new item kind, and a project without configurations produces exactly the rows
it produced before. That last claim is the one worth a test of its own, so it
gets row-set equality against a written-out list rather than a "looks about
right" count.

The budget half is driven deterministically — the deadline is set on the
runner and ``_ensure_config_built`` is watched — because a wall-clock budget
small enough to expire mid-family is a flaky test on a loaded machine, and the
rule being pinned ("the budget is read before EVERY row, and what it stops is
a skip, never a red") is not a timing question.
"""

from __future__ import annotations

import time

import pytest

from agentcad.core import locks
from agentcad.core.branches import pinned_tree_var
from agentcad.core.checks import (
    ITEM_KINDS,
    STAGES,
    CheckRunner,
    validate_report,
)
from agentcad.core.tools import build_registry

from .conftest import (
    BOX_SCRIPT,
    FLANGE_SCRIPT,
    THREE_SIZE_CONFIGS,
    make_test_service,
)

#: One member that cannot build: the per-configuration failure a row has to
#: carry without reddening anything it did not measure.
FRAGILE_SCRIPT = '''\
from build123d import *

PARAMS = {
    "thick": {"default": 10.0, "min": 4.0, "max": 60.0, "unit": "mm",
              "description": "plate thickness"},
}

def build(p):
    if p.thick > 50:
        raise ValueError("thickness above 50 mm is not manufacturable")
    return Box(40, 40, p.thick)
'''

FRAGILE_CONFIGS = {
    "thin": {"params": {"thick": 10.0}, "label": "Thin"},
    "heavy": {"params": {"thick": 55.0}, "label": "Too thick"},
}


@pytest.fixture(autouse=True)
def _reset_context():
    """Identity and the branch pin are ContextVars: rebind them per test."""
    cid = locks.client_id_var.set("local")
    pin = pinned_tree_var.set(None)
    yield
    locks.client_id_var.reset(cid)
    pinned_tree_var.reset(pin)


@pytest.fixture
def stack(kernel, tmp_path):
    """A service, its tool registry and a runner over the two."""
    service = make_test_service(tmp_path / "projects", kernel)
    registry = build_registry(service)
    return service, registry, CheckRunner(service, registry)


def _stage(report: dict, name: str) -> dict:
    return next(stage for stage in report["stages"] if stage["name"] == name)


def _family(service, proj: str = "demo") -> str:
    service.create_project(proj)
    service.create_part(proj, "flange", script=FLANGE_SCRIPT)
    service.store.update_part_entry(proj, "flange", configs=THREE_SIZE_CONFIGS)
    return proj


@pytest.mark.timeout(900)
class TestConfigCheckRows:
    """The build stage over one configured project."""

    def test_a_configured_part_gets_one_row_per_configuration(self, stack):
        service, _registry, runner = stack
        proj = _family(service)

        report = runner.run(proj, stages=("build",))

        stage = _stage(report, "build")
        assert [item["id"] for item in stage["items"]] == [
            "build:flange", "build:flange@s", "build:flange@m",
            "build:flange@l"]
        assert all(item["status"] == "pass" for item in stage["items"]), [
            (item["id"], item["message"]) for item in stage["items"]]
        # No new item kind and no new stage — `STAGES`/`ITEM_KINDS` and their
        # pinned tests are untouched.
        assert all(item["kind"] == "part" for item in stage["items"])
        assert [s["name"] for s in report["stages"]] == list(STAGES)
        assert "config" not in ITEM_KINDS
        assert validate_report(report) == []
        assert report["status"] == "green" and report["exit_code"] == 0

        rows = {item["subject"]: item["details"] for item in stage["items"]}
        assert "config" not in rows["flange"]
        for name in ("s", "m", "l"):
            details = rows[f"flange@{name}"]
            assert details["config"] == name
            assert details["cache_key"] and details["cached"] is False
            assert details["volume_mm3"] > 0 and details["is_valid"] is True
        # Four rows, four distinct cache entries. `m`'s parameters ARE the
        # script's defaults, and it still gets its own key: the service hashes
        # the OVERRIDE map (`{}` for the base, an explicit map for `m`) while
        # the worker resolves the defaults, and making the two agree would move
        # every pre-PRD-012 key (Decision 3).
        keys = {subject: details["cache_key"] for subject, details in
                rows.items()}
        assert len(set(keys.values())) == 4, keys

    def test_a_second_run_reports_the_configuration_rows_as_cached(self, stack):
        service, _registry, runner = stack
        proj = _family(service)

        runner.run(proj, stages=("build",))
        warm = runner.run(proj, stages=("build",))

        stage = _stage(warm, "build")
        assert all(item["details"]["cached"] is True
                   for item in stage["items"])
        assert all("from cache" in item["message"] for item in stage["items"])

    def test_a_member_that_cannot_build_is_a_fail_row_naming_the_config(
            self, stack):
        service, _registry, runner = stack
        service.create_project("demo")
        service.create_part("demo", "plate", script=FRAGILE_SCRIPT)
        service.store.update_part_entry("demo", "plate",
                                        configs=FRAGILE_CONFIGS)

        report = runner.run("demo", stages=("build",))

        stage = _stage(report, "build")
        assert [item["id"] for item in stage["items"]] == [
            "build:plate", "build:plate@thin", "build:plate@heavy"]
        statuses = {item["subject"]: item["status"] for item in stage["items"]}
        assert statuses == {"plate": "pass", "plate@thin": "pass",
                            "plate@heavy": "fail"}
        bad = next(item for item in stage["items"]
                   if item["subject"] == "plate@heavy")
        assert bad["details"]["config"] == "heavy"
        assert bad["details"]["cache_key"], "a failed build still names its key"
        assert "not manufacturable" in bad["error"]["message"]
        assert stage["status"] == "red" and report["exit_code"] == 1
        assert validate_report(report) == []

    # --------------------------------------------------------- the budget

    def test_the_budget_is_read_before_every_configuration_row(
            self, stack, monkeypatch):
        """Below the floor nothing is issued at all: a config build takes no
        ``timeout_s``, so one started with 200 ms left cannot be preempted."""
        service, _registry, runner = stack
        proj = _family(service)
        calls: list = []
        monkeypatch.setattr(service, "_ensure_built",
                            lambda *args: calls.append(args))
        monkeypatch.setattr(service, "_ensure_config_built",
                            lambda *args: calls.append(args))
        runner._deadline = time.monotonic() + 0.2   # positive, under the floor

        stage = runner._stage("build", proj, {"build"}, set(), [], [])

        assert calls == [], "a build was started the budget could not pay for"
        assert [item["subject"] for item in stage["items"]] == [
            "flange", "flange@s", "flange@m", "flange@l"]
        assert all(item["status"] == "skip" and
                   item["reason"] == "budget_exceeded" and item["hint"]
                   for item in stage["items"])
        assert stage["summary"]["errors"] == 0, "a blown budget is never a red"
        assert runner._truncated is True

    def test_a_budget_that_dies_mid_family_still_names_every_member(
            self, stack, monkeypatch):
        """The part is measured, the budget expires during it, and the three
        members it never reached are named rather than dropped — a partial
        report is evidence, a missing one is not."""
        service, _registry, runner = stack
        proj = _family(service)
        configs: list = []

        def spend(_proj, part_id):
            runner._deadline = time.monotonic() + 0.2   # the budget dies here
            return {"ok": True, "cache_key": "k" * 32, "warnings": [],
                    "metrics": {"volume_mm3": 1.0, "mass_g": 1.0,
                                "n_solids": 1, "is_valid": True}}

        monkeypatch.setattr(service, "_ensure_built", spend)
        monkeypatch.setattr(service, "_ensure_config_built",
                            lambda *args: configs.append(args))
        runner._deadline = time.monotonic() + 600.0

        stage = runner._stage("build", proj, {"build"}, set(), [], [])

        assert configs == []
        assert [(item["subject"], item["status"]) for item in stage["items"]] \
            == [("flange", "pass"), ("flange@s", "skip"),
                ("flange@m", "skip"), ("flange@l", "skip")]
        assert runner._truncated is True

    def test_a_full_run_under_a_blown_budget_is_exit_two_and_validates(
            self, stack):
        service, _registry, runner = stack
        proj = _family(service)

        report = runner.run(proj, stages=("build",), budget_s=0.0001)

        assert report["complete"] is False and report["exit_code"] == 2
        assert report["status"] != "red", "a blown budget is harness, not red"
        assert validate_report(report) == []

    def test_a_harness_failure_is_one_error_row_naming_the_configuration(
            self, stack, monkeypatch):
        """The defensive edge `_build_item` has, with the configuration in the
        `report.errors[]` entry: `part` alone is `flange@s`, which a consumer
        grouping by part has to parse to use."""
        service, _registry, runner = stack
        proj = _family(service)

        def built(_proj, _part_id):
            return {"ok": True, "cache_key": "k" * 32, "warnings": [],
                    "metrics": {"volume_mm3": 1.0, "mass_g": 1.0,
                                "n_solids": 1, "is_valid": True}}

        def boom(*_args):
            raise OSError("the script file vanished mid-run")

        monkeypatch.setattr(service, "_ensure_built", built)
        monkeypatch.setattr(service, "_ensure_config_built", boom)
        errors: list[dict] = []

        stage = runner._stage("build", proj, {"build"}, set(), [], errors)

        rows = [item for item in stage["items"] if "@" in item["subject"]]
        assert [item["status"] for item in rows] == ["error"] * 3
        assert all("vanished mid-run" in item["message"] for item in rows)
        assert [entry["part"] for entry in errors] == [
            "flange@s", "flange@m", "flange@l"]
        assert [entry["config"] for entry in errors] == ["s", "m", "l"]
        assert all(entry["stage"] == "build" for entry in errors)

    # ----------------------------------------------------------- the control

    def test_a_project_without_configurations_reports_what_it_did_before(
            self, stack):
        """G5/AC8: row-set equality against a written-out list. The inner loop
        must degenerate to the part itself, with no `@` row and no reordering.
        """
        service, _registry, runner = stack
        service.create_project("plain")
        service.create_part("plain", "cube", script=BOX_SCRIPT)
        service.create_part("plain", "flange", script=FLANGE_SCRIPT)

        report = runner.run("plain", stages=("build",))

        stage = _stage(report, "build")
        assert [item["id"] for item in stage["items"]] == [
            "build:cube", "build:flange"]
        assert all("@" not in item["subject"] for item in stage["items"])
        assert all("config" not in item["details"] for item in stage["items"])
        assert stage["status"] == "green"
        assert validate_report(report) == []
