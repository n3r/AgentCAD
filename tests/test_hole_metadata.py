"""PRD-010 slice 5 — the hole-metadata pipeline: shape -> handler -> sidecar.

The feature under test is a chain whose failure mode is **silence**. Slice 4's
records ride on the built shape; this slice carries them out to a rebuild
result, a `get_part` and a durable sidecar, and every link can lose them
without anyone noticing:

* the worker's `_SHAPE_CACHE` returns a cached shape without running
  `build(p)` — the regression **AC7b** exists to catch, and the one the PRD's
  original registry design could not see at all;
* the service's `.metrics.json` fast path makes **zero** kernel calls
  (measured, changelog 0150), so records that live only on a shape in a worker
  are unreachable — hence the sidecar;
* `KernelPool` round-robins an unkeyed request, so a harvest that forgets
  `affinity=part_id` lands on a cold worker and silently pays a full rebuild
  (measured: 11.4 s on `engine/intake_manifold` against 1 ms keyed).

Three answers are therefore kept distinct throughout, and the tests below
assert each one separately:

| `holes` | means |
|---|---|
| key **absent** | not harvested — the build failed, or the harvest itself did |
| `null` | the part declares no holes |
| `[]` | records WERE created and did not reach the returned part (dropped) |
| `[...]` | the records |
"""

import json

import pytest

from agentcad.core.tools import build_registry
from agentcad.core.tools_holes import (
    HOLES_SIDECAR_VERSION,
    install_rebuild_holes,
)

from .conftest import make_test_service

# Two holes on one plate: one clearance group of 2, one tapped group of 1.
HOLED = '''\
from build123d import Box
from agentcad.toolkit import holes

PARAMS = {"t": {"default": 10.0, "min": 4.0, "max": 30.0, "unit": "mm",
                "description": "plate thickness"}}

def build(p):
    part = Box(120, 80, p.t)
    part, _r, _w = holes.clearance(part, [(30, 20), (-30, 20)], "M5")
    part, _r, _w = holes.tapped(part, [(0, 0)], "M6", depth=4)
    return part
'''

# A different part with different holes, for the AC7 cross-contamination test.
OTHER = '''\
from build123d import Box
from agentcad.toolkit import holes

PARAMS = {"w": {"default": 60.0, "min": 20.0, "max": 200.0, "unit": "mm",
                "description": "plate width"}}

def build(p):
    part = Box(p.w, 40, 8)
    part, _r, _w = holes.clearance(part, [(0, 0)], "M12", fit="coarse")
    return part
'''

PLAIN = '''\
from build123d import Box

PARAMS = {"s": {"default": 20.0, "min": 5.0, "max": 50.0, "unit": "mm",
                "description": "cube edge"}}

def build(p):
    return Box(p.s, p.s, p.s)
'''

# Drills, then performs a RAW build123d operation, which returns a new object
# with none of the original's attributes (measured, changelog 0150).
#
# `%s` is a per-test marker. The worker's `_SHAPE_CACHE` is keyed on the script
# TEXT and the session-scoped kernel outlives every test, so two tests sharing
# one script share one cached shape — and the second one's harvest would take a
# cache hit, never run `build(p)`, and be unable to measure the drop. Giving
# each test its own text is what makes "the harvest is the call that builds"
# true in the test as it is in the seam.
DROPS = '''\
from build123d import Box, Pos
from agentcad.toolkit import holes

PARAMS = {"s": {"default": 10.0, "min": 5.0, "max": 30.0, "unit": "mm",
                "description": "plate thickness"}}

def build(p):   # %s
    part = Box(120, 80, p.s)
    part, _r, _w = holes.clearance(part, [(30, 20), (-30, 20)], "M5")
    return part - Pos(60, 40, 0) * Box(10, 10, 40)
'''

BROKEN = '''\
PARAMS = {"s": {"default": 1.0, "min": 0.5, "max": 2.0, "unit": "mm"}}

def build(p):
    raise ValueError("this part does not build")
'''

# A hand-written record: a dict on the carrier attribute that no helper made.
RESIDUE = '''\
from build123d import Box
from agentcad.toolkit import holes

PARAMS = {"s": {"default": 10.0, "min": 5.0, "max": 30.0, "unit": "mm"}}

def build(p):
    part = Box(40, 40, p.s)
    setattr(part, holes.ATTR, [{"id": "h0", "family": "clearance"}])
    return part
'''


def _records(kernel, script, params=None):
    return kernel.request("hole_records",
                          {"script": script, "params": params or {}})


@pytest.fixture
def demo(kernel, tmp_path):
    service = make_test_service(tmp_path / "projects", kernel)
    service.create_project("demo")
    service.create_part("demo", "holed", script=HOLED)
    registry = build_registry(service)
    return service, registry


def _counting(service, monkeypatch) -> list:
    """Every kernel request as (method, affinity) — the harvest's routing is
    part of its contract, not an implementation detail (see the module
    docstring's 11.4 s measurement)."""
    seen: list = []
    original = service.kernel.request

    def counting(method, params, timeout_s=None, affinity=None):
        seen.append((method, affinity))
        return original(method, params, timeout_s=timeout_s, affinity=affinity)

    monkeypatch.setattr(service.kernel, "request", counting)
    return seen


# ------------------------------------------------------ the handler pack


@pytest.mark.integration
def test_the_handler_returns_the_records_the_helpers_made(kernel):
    result = _records(kernel, HOLED)
    assert [record["id"] for record in result["holes"]] == ["h0", "h1"]
    assert result["holes"][0]["designation"] == "⌀5.5"
    assert result["holes"][0]["count"] == 2
    assert result["holes"][1]["family"] == "tapped"
    assert result["warnings"] == []
    assert result["dropped"] == 0


@pytest.mark.integration
def test_ac7_two_different_parts_on_one_warm_worker_do_not_mix(kernel):
    """**AC7.** The original design drained a module-level registry, so the
    property below was a discipline. Records riding the shape make it
    structural — there is no shared mutable state to contaminate."""
    first = _records(kernel, HOLED)
    second = _records(kernel, OTHER)
    third = _records(kernel, HOLED)

    assert [r["size"] for r in first["holes"]] == ["M5", "M6"]
    assert [r["size"] for r in second["holes"]] == ["M12"]
    assert third["holes"] == first["holes"]


@pytest.mark.integration
def test_ac7b_the_same_part_twice_on_a_warm_worker_is_identical(kernel):
    """**AC7b** — the criterion AC7 cannot observe.

    The second call hits the worker's `_SHAPE_CACHE`, which returns the cached
    shape **without running `build(p)`**. A registry-drain harvest returns
    nothing here and says so to nobody; the shape carries its records, so this
    is identical by construction. The delta is 0 on the second call precisely
    because `build(p)` did not run, and a 0 delta must never be read as
    "records were dropped".
    """
    first = _records(kernel, HOLED, {"t": 12.0})
    second = _records(kernel, HOLED, {"t": 12.0})

    # The test is only AC7b if the second call really was the cache-hit path,
    # so it says so rather than assuming it.
    assert first["measured"] is True and second["measured"] is False
    assert first["holes"] == second["holes"]
    assert len(first["holes"]) == 2
    assert first["dropped"] == second["dropped"] == 0
    assert second["warnings"] == []


@pytest.mark.integration
def test_a_part_with_no_holes_reports_no_records_and_no_warning(kernel):
    result = _records(kernel, PLAIN)
    assert result["holes"] == []
    assert result["dropped"] == 0
    assert result["warnings"] == []


@pytest.mark.integration
def test_a_raw_operation_after_the_helpers_is_reported_as_dropped(kernel):
    """`[]` with `dropped: 1` is not `[]` with `dropped: 0`: one is a part
    that declares no holes, the other is a part whose records a raw build123d
    operation threw away. The delta is the only thing that can tell them
    apart."""
    result = _records(kernel, DROPS % "handler")
    assert result["holes"] == []
    assert result["dropped"] == 1
    assert result["measured"] is True
    assert len(result["warnings"]) == 1
    assert "did not reach the returned part" in result["warnings"][0]


@pytest.mark.integration
def test_a_shape_cache_hit_reports_that_it_measured_nothing(kernel):
    """The delta's honest half. The second call returns the cached shape
    without running `build(p)`, so its zero delta is not evidence of anything —
    and saying "dropped: 0, measured: true" there would turn a part that lost
    its records into a part that declares none."""
    script = DROPS % "cache-hit"
    first = _records(kernel, script)
    second = _records(kernel, script)

    assert first["measured"] is True and first["dropped"] == 1
    assert second["measured"] is False and second["dropped"] == 0
    assert second["holes"] == []            # identical, and it means less


@pytest.mark.integration
def test_a_hand_written_record_is_a_contract_error_naming_the_key(kernel):
    """A record is a plain dict, so anything can put one on the shape. A
    residue record must fail loudly here rather than KeyError in the server or
    in the drawing pack."""
    from agentcad.kernel.client import KernelError

    with pytest.raises(KernelError) as exc:
        _records(kernel, RESIDUE)
    assert exc.value.type == "contract_error"
    assert "designation" in str(exc.value)
    assert "hole record" in str(exc.value)


# ----------------------------------------------------------- the seam


@pytest.mark.integration
def test_a_rebuild_carries_the_records_and_writes_the_sidecar(demo):
    service, _registry = demo
    result = service._rebuild("demo", "holed")

    assert result["ok"] is True
    assert [record["id"] for record in result["holes"]] == ["h0", "h1"]

    sidecar = (service.store.cache_dir("demo")
               / f"{result['cache_key']}.holes.json")
    stored = json.loads(sidecar.read_text(encoding="utf-8"))
    assert stored["version"] == HOLES_SIDECAR_VERSION
    assert stored["cache_key"] == result["cache_key"]
    assert stored["holes"] == result["holes"]
    assert stored["warnings"] == [] and stored["dropped"] == 0


@pytest.mark.integration
def test_the_service_cache_hit_answers_from_the_sidecar_with_no_kernel_call(
        demo, monkeypatch):
    """The measurement that made the sidecar mandatory (changelog 0150): on the
    `.metrics.json` fast path the kernel is **not called at all**, so records
    that live only on a shape in a worker are unreachable at any price."""
    service, _registry = demo
    first = service._rebuild("demo", "holed")

    calls = _counting(service, monkeypatch)
    second = service._rebuild("demo", "holed")

    assert calls == []                       # neither a build nor a harvest
    assert second["holes"] == first["holes"]


@pytest.mark.integration
def test_the_harvest_is_routed_by_affinity(demo, monkeypatch):
    """`KernelPool._pick` round-robins an **unkeyed** request, so a harvest
    without `affinity=part_id` can land on a worker whose shape cache never saw
    the script — measured at 11.4 s on `engine/intake_manifold` against 1 ms
    keyed. The affinity is part of the contract."""
    service, _registry = demo
    calls = _counting(service, monkeypatch)
    service.set_params("demo", "holed", {"t": 12.0})   # a fresh cache key

    assert ("build", "holed") in calls
    assert ("hole_records", "holed") in calls


@pytest.mark.integration
def test_a_corrupt_sidecar_is_discarded_and_re_harvested(demo):
    service, _registry = demo
    first = service._rebuild("demo", "holed")
    sidecar = (service.store.cache_dir("demo")
               / f"{first['cache_key']}.holes.json")
    sidecar.write_text("{not json", encoding="utf-8")

    detail = service.get_part("demo", "holed")
    assert "holes" not in detail              # nothing usable to report
    assert not sidecar.exists()               # and the garbage is gone

    again = service._rebuild("demo", "holed")
    assert again["holes"] == first["holes"]
    assert json.loads(sidecar.read_text(encoding="utf-8"))["version"] \
        == HOLES_SIDECAR_VERSION


@pytest.mark.integration
def test_a_version_mismatched_sidecar_is_discarded(demo):
    service, _registry = demo
    first = service._rebuild("demo", "holed")
    sidecar = (service.store.cache_dir("demo")
               / f"{first['cache_key']}.holes.json")
    sidecar.write_text(json.dumps({"version": 99, "holes": [{"id": "old"}]}),
                       encoding="utf-8")

    assert "holes" not in service.get_part("demo", "holed")
    assert not sidecar.exists()


@pytest.mark.integration
def test_a_part_with_no_holes_rebuilds_to_null(demo, monkeypatch):
    """A script that never mentions `agentcad` cannot hold a record, so `null`
    here is **definitive** and costs no kernel call — which is the difference
    between "declares none" and the "not harvested" a warm shape cache would
    otherwise force onto every ordinary part."""
    service, _registry = demo
    service.create_part("demo", "plain", script=PLAIN)
    calls = _counting(service, monkeypatch)
    result = service._rebuild("demo", "plain")

    assert result["holes"] is None            # declares none, not "unknown"
    assert service.get_part("demo", "plain")["holes"] is None
    assert [method for method, _ in calls if method == "hole_records"] == []


@pytest.mark.integration
def test_dropped_records_are_an_empty_list_and_a_warning_not_null(demo):
    """The three-way contract at the seam: `[]` + a warning is a part that lost
    its records, and it must not read as `null` ("declares none")."""
    service, _registry = demo
    service.create_part("demo", "drops", script=DROPS % "seam")
    result = service._rebuild("demo", "drops")

    assert result["holes"] == []
    assert result["holes"] is not None
    assert any("did not reach the returned part" in w
               for w in result["warnings"])

    detail = service.get_part("demo", "drops")
    assert detail["holes"] == []
    assert any("did not reach the returned part" in w
               for w in detail["status"]["warnings"])
    # ...and reading twice must not grow the stored warnings list.
    assert (service.get_part("demo", "drops")["status"]["warnings"]
            == detail["status"]["warnings"])


@pytest.mark.integration
def test_an_unmeasurable_harvest_is_absent_and_is_not_persisted(demo, kernel):
    """The one thing the delta cannot see, made visible instead of guessed.

    Here another surface builds the script first, so the seam's harvest takes a
    worker shape-cache hit: `build(p)` never runs during it, the delta is 0,
    and "no records" is **not** evidence that the part declares none. The key
    is therefore absent (not `null`), and nothing is written to the sidecar, so
    a later harvest that does run the build can still answer for real.
    """
    service, _registry = demo
    script = DROPS % "unmeasurable"
    assert kernel.request("hole_records",
                          {"script": script, "params": {}})["measured"] is True

    service.create_part("demo", "warm", script=script)
    result = service._rebuild("demo", "warm")

    assert result["ok"] is True
    assert "holes" not in result           # unknown, and it says so
    sidecar = (service.store.cache_dir("demo")
               / f"{result['cache_key']}.holes.json")
    assert not sidecar.exists()


@pytest.mark.integration
def test_a_failed_rebuild_has_no_holes_key_at_all(demo):
    service, _registry = demo
    service.create_part("demo", "broken", script=BROKEN)
    result = service._rebuild("demo", "broken")

    assert result["ok"] is False
    assert "holes" not in result
    assert "holes" not in service.get_part("demo", "broken")


@pytest.mark.integration
def test_get_part_reads_the_sidecar_and_calls_no_kernel(demo, monkeypatch):
    service, _registry = demo
    built = service._rebuild("demo", "holed")

    calls = _counting(service, monkeypatch)
    detail = service.get_part("demo", "holed")

    assert calls == []
    assert detail["holes"] == built["holes"]


@pytest.mark.integration
def test_get_part_on_a_never_built_part_has_no_holes_key(demo, monkeypatch):
    """`get_part` reads the sidecar; it never triggers a kernel call it did not
    already need. A part whose `_ensure_built` fails has no records and says
    so by absence."""
    service, _registry = demo
    service.create_part("demo", "broken", script=BROKEN)
    detail = service.get_part("demo", "broken")
    assert "holes" not in detail


@pytest.mark.integration
def test_a_reference_part_declares_no_holes(demo, tmp_path):
    """A mesh has no script, so it cannot declare records — `null` is the true
    answer, and it costs no kernel call to give."""
    from build123d import Box, export_stl

    service, registry = demo
    stl = tmp_path / "block.stl"
    export_stl(Box(10, 10, 10), str(stl))
    result = registry.call("import_cad_file", {
        "project": "demo", "source": str(stl), "part_id": "block"})
    assert "error" not in result, result

    detail = service.get_part("demo", "block")
    assert detail["holes"] is None


@pytest.mark.integration
def test_the_harvest_failing_leaves_the_key_absent_and_warns(demo, monkeypatch):
    """A harvest that raises must never raise out of the wrapper, must not
    report `null` (which would mean "declares none"), and must not be silent."""
    service, _registry = demo
    original = service.kernel.request

    def failing(method, params, timeout_s=None, affinity=None):
        if method == "hole_records":
            raise RuntimeError("worker went away")
        return original(method, params, timeout_s=timeout_s, affinity=affinity)

    monkeypatch.setattr(service.kernel, "request", failing)
    result = service._rebuild("demo", "holed")

    assert result["ok"] is True               # the geometry still landed
    assert "holes" not in result
    assert any("worker went away" in w for w in result["warnings"])


# ------------------------------------------------------- the seam's hygiene


@pytest.mark.integration
def test_install_rebuild_holes_is_idempotent(demo, monkeypatch):
    service, _registry = demo
    rebuild, get_part = service._rebuild, service.get_part

    install_rebuild_holes(service)
    install_rebuild_holes(service)

    assert service._rebuild is rebuild
    assert service.get_part is get_part

    calls = _counting(service, monkeypatch)
    result = service.set_params("demo", "holed", {"t": 14.0})
    assert len(result["holes"]) == 2
    assert sum(1 for method, _ in calls if method == "hole_records") == 1


@pytest.mark.integration
def test_the_seam_survives_a_second_build_registry(demo):
    """`tools_holes` loads at `h`, before `tools_proposals` (`p`),
    `tools_specs` (`s`) and `tools_versioning` (`v`) — every one of which
    rewires a bound service method. Building the registry again must not disarm
    the harvest."""
    service, _registry = demo
    build_registry(service)
    build_registry(service)

    result = service._rebuild("demo", "holed")
    assert len(result["holes"]) == 2
    assert service.get_part("demo", "holed")["holes"] == result["holes"]


@pytest.mark.integration
def test_the_pack_reads_no_cross_pack_seam_at_registration(tmp_path, kernel):
    """It loads before the packs that install `branches`/`specs`/
    `gate_providers`, so reading one in `register()` would be an AttributeError
    on a real service — asserted by registering this pack alone."""
    from agentcad.core import tools_holes
    from agentcad.core.tools import ToolRegistry

    service = make_test_service(tmp_path / "projects", kernel)
    for name in ("branches", "specs", "gate_providers", "merges"):
        assert not hasattr(service, name), name
    registry = ToolRegistry()
    tools_holes.register(registry, service)
    assert {tool.name for tool in registry.list()} >= {"hole_standards"}
    assert getattr(service._rebuild, "_agentcad_holes_wrapper", False) is True


# --------- a no-op instance has to reach the user, not just the record

#: A script that drills into its own void, spelled the way every bundled part
#: spells a hole call: `part, _r, _w = ...`. The helper's warning about the
#: no-op instance goes into `_w` and nowhere else, so the *record* is the only
#: thing that survives the script — which is why the harvest re-reads the drop
#: off it. `%s` gives each test its own script text (see `DROPS`).
CUTS_AIR = '''\
from build123d import Box, BuildPart, Mode
from agentcad.toolkit import holes

PARAMS = {"t": {"default": 10.0, "min": 4.0, "max": 30.0, "unit": "mm",
                "description": "frame thickness"}}

def build(p):   # %s
    with BuildPart() as builder:
        Box(100, 100, p.t)
        Box(60, 60, p.t, mode=Mode.SUBTRACT)
    part, _r, _w = holes.drill(builder.part, [(40, 0), (0, 0)], 10.0)
    return part
'''


@pytest.mark.integration
def test_an_instance_that_removed_nothing_is_warned_about_on_the_harvest(
        kernel):
    """The record cannot be the only place a drop is reported.

    A part script that discards the helper's warning — which is how every
    bundled part is written — would otherwise carry a silently reduced count
    and no message anywhere. The harvest reads the drop back off the record and
    puts it in the warnings the rebuild result and `get_part` already carry.
    """
    result = _records(kernel, CUTS_AIR % "air")

    record = result["holes"][0]
    assert record["count"] == 1
    assert [row["i"] for row in record["dropped"]] == [1]
    warning = next(w for w in result["warnings"] if "h0" in w)
    assert "1 removed no material" in warning
    assert "counts only the 1 that did" in warning
    # `dropped` at the top level is the OTHER kind — records lost by an
    # operation that did not carry them — and stays 0 here.
    assert result["dropped"] == 0


@pytest.mark.integration
def test_a_record_whose_designation_drifted_is_a_contract_error(kernel):
    """The harvest raises the shared validator's verdict, and the validator
    re-derives the callout from the record's own numbers. A carrier whose text
    and numbers have come apart is residue, whatever its key list looks like."""
    forged = '''\
from build123d import Box
from agentcad.toolkit import holes

PARAMS = {"s": {"default": 10.0, "min": 5.0, "max": 30.0, "unit": "mm"}}

def build(p):
    part = Box(40, 40, p.s)
    part, recs, _w = holes.drill(part, [(0, 0)], 6.8)
    forged = dict(recs[0])
    forged["designation"] = "M8\\u00d71.25 - 6H"
    setattr(part, holes.ATTR, [forged])
    return part
'''
    from agentcad.kernel.client import KernelError

    with pytest.raises(KernelError) as excinfo:
        _records(kernel, forged)
    assert excinfo.value.type == "contract_error"
    assert "own numbers spell" in excinfo.value.message
