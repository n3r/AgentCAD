"""PRD-012 slice 4 — an assembly instance bound to a configuration, end to end.

Decision 7: the store validates the binding, the *record* resolves it, and the
mesh is content-addressed. Every per-instance geometry site obtains
``service._record_for(proj, inst.part, inst.config)`` and hands the derived
record to the existing record-driven helper, so two instances of one part can
be two different sizes in one assembly — with two masses, two cache keys and
two meshes.

The module-level tests are kernel-free on purpose (the service is built with
``kernel=None``, so anything that would need a worker fails loudly rather than
passing on a mock): a cache key, a spec key, a stack-up warning and an
assembly delta are all pure functions of the manifest. ``TestBoundAssembly``
is the geometry half and builds one flange size family plus a mated hinge for
real, warming both in a class-scoped template that every test clones.
"""

from __future__ import annotations

import queue
from pathlib import Path

import pytest

from agentcad.core.materials import DEFAULT_MATERIAL
from agentcad.core.model import InstanceSpec
from agentcad.core.packet import assembly_delta
from agentcad.core.specs import SpecRunner
from agentcad.core.tools import build_registry
from agentcad.core.tools_stackup import compute_stackup

from .conftest import (
    FLANGE_SCRIPT,
    THREE_SIZE_CONFIGS,
    clone_test_service,
    make_test_service,
)

# A hinge plate whose revolute connector rides on a *configured* parameter:
# the connector sits 1 mm above the plate top, so `t` moves it. This is what
# makes the mate tests discriminating — a resolution that ignored the binding
# would put the flap at the default height.
HINGE_BASE_SCRIPT = '''\
from build123d import *

PARAMS = {"t": {"default": 10.0, "min": 1.0, "max": 60.0, "unit": "mm",
                "description": "plate thickness"}}

def build(p):
    with BuildPart() as part:
        Box(40, 40, p.t)
    return part.part

def connectors(p, part):
    return {"hinge": {"type": "revolute", "axis": ((0, 0, p.t / 2 + 1), (0, 0, 1))}}
'''

# `low` is the script default; `high` lifts the hinge by (40 - 10) / 2 = 15 mm.
HINGE_CONFIGS = {"low": {"params": {"t": 10.0}, "label": "Low"},
                 "high": {"params": {"t": 40.0}, "label": "High"}}

HINGE_LIFT_MM = (40.0 - 10.0) / 2

FLAP_SCRIPT = '''\
from build123d import *

PARAMS = {"l": {"default": 30.0, "min": 5.0, "max": 100.0, "unit": "mm",
                "description": "flap length"}}

def build(p):
    with BuildPart() as part:
        Box(p.l, 4, 4)
    return part.part

def connectors(p, part):
    return {"root": {"type": "rigid", "location": ((-p.l / 2, 0, -2), (0, 0, 0))}}
'''

FLAP_MATE = {"connector": "root", "to_instance": "b1",
             "to_connector": "hinge", "params": {"angle": 0.0}}

# The pure resolution of each flange size (defaults < config, no overrides).
S_PARAMS = {"outer_d": 100.0, "bore_d": 50.0, "bc_d": 80.0}
L_PARAMS = {"outer_d": 200.0, "bore_d": 120.0, "bc_d": 170.0}


def _drain(q: queue.Queue) -> list[dict]:
    out = []
    while True:
        try:
            out.append(q.get_nowait())
        except queue.Empty:
            return out


# ------------------------------------------------------- kernel-free fixtures


@pytest.fixture
def family(tmp_path):
    """A real store with one configured flange part and no geometry at all."""
    svc = make_test_service(tmp_path / "projects", None)
    svc.create_project("demo")
    svc.store.add_part("demo", "flange", "Flange", DEFAULT_MATERIAL,
                       FLANGE_SCRIPT)
    svc.store.update_part_entry("demo", "flange", configs=THREE_SIZE_CONFIGS)
    return svc


def _bind(svc, config, iid="i1"):
    """Place one instance of `flange`, bound to `config` (None = unbound)."""
    svc.store.set_instances(
        "demo", [InstanceSpec(id=iid, part="flange", config=config)])


def _stale_binding(svc, proj: str, iid: str, name: str = "xl") -> None:
    """Point one instance at a configuration its part does not declare.

    `set_instances` refuses this, so it is written straight to the manifest —
    a key-wise merge (theirs removes the configuration, ours keeps the
    binding) is the real-world producer, and every reader has to degrade
    rather than take the whole project down with it.
    """
    manifest = svc.store.manifest(proj)
    for entry in manifest["assembly"]["instances"]:
        if entry["id"] == iid:
            entry["config"] = name
    svc.store.save_manifest(proj, manifest)


# ----------------------------------------------- specs: the per-instance item


def test_a_spec_instance_item_carries_the_bound_configurations_params(family):
    """`_instance_item` is what `clearance` measures; a silent fall-through to
    the working state would measure the wrong size."""
    runner = SpecRunner(family)
    item = runner._instance_item(
        "demo", InstanceSpec(id="i1", part="flange", config="s"))
    assert item["params"] == S_PARAMS
    assert item["name"] == "i1"
    base = runner._instance_item("demo", InstanceSpec(id="i1", part="flange"))
    assert base["params"] == {}


def test_the_assembly_spec_key_changes_when_an_instance_is_rebound(family):
    """"A spec cache key covers every input the check reads": an assembly-tier
    verdict measured at S must not be reused at L."""
    runner = SpecRunner(family)
    script = "SPECS = []\n"
    keys = {}
    for name in (None, "s", "l"):
        _bind(family, name)
        keys[name] = runner._project_key("demo", script)
    assert len(set(keys.values())) == 3

    # Two configurations with the same override map are one geometry, so they
    # legitimately share the key (nothing new enters the payload).
    configs = dict(family.store.get_part("demo", "flange").configs)
    configs["s2"] = {"params": dict(configs["s"]["params"]), "label": "S2"}
    family.store.update_part_entry("demo", "flange", configs=configs)
    _bind(family, "s2")
    assert runner._project_key("demo", script) == keys["s"]


def test_a_stale_binding_degrades_one_assembly_key_row(family):
    """`_project_key`'s caller wraps it in a bare `except Exception` and then
    evaluates UNCACHED, so an escaping ValidationError would silently disable
    assembly-tier caching for the whole project. One row goes to "missing"."""
    runner = SpecRunner(family)
    script = "SPECS = []\n"
    _bind(family, "s")
    good = runner._project_key("demo", script)
    _stale_binding(family, "demo", "i1")
    stale = runner._project_key("demo", script)
    assert len(stale) == 32 and stale != good

# ------------------------------------------------------------- stack-up


def test_tolerance_stackup_warns_that_the_nominal_is_per_configuration(family):
    """Per-config PMI is a stated non-goal, so a mixed answer is named rather
    than silently produced."""
    _bind(family, "l")
    result = compute_stackup(family, "demo", "z", "i1", "i1")
    assert ("instance i1 (part flange) is bound to configuration 'l': "
            "tolerances are per part, the nominal is per configuration"
            ) in result["warnings"]


def test_an_unbound_path_instance_gets_no_configuration_warning(family):
    _bind(family, None)
    result = compute_stackup(family, "demo", "z", "i1", "i1")
    assert not any("configuration" in w for w in result["warnings"])
    assert result["warnings"] == [
        "instance i1 (part flange) has no height tolerance"]


# ------------------------------------------------------- the assembly delta


def _entry(iid: str, config: str | None = None, mass: float = 10.0) -> dict:
    entry = {"id": iid, "part": "flange", "position": [0.0, 0.0, 0.0],
             "rotation_deg": [0.0, 0.0, 0.0], "mass_g": mass, "state": "ok"}
    if config is not None:
        entry["config"] = config
    return entry


def _asm(entries: list[dict], total: float = 10.0) -> dict:
    return {"instances": entries, "total_mass_g": total, "bbox": None}


def test_assembly_delta_reports_a_rebinding_with_unchanged_mass():
    """`config` is treated like `mate`: a rebinding whose mass happens not to
    move is still a change, and the packet must say so."""
    delta = assembly_delta(_asm([_entry("i1", "s"), _entry("i2")]),
                           _asm([_entry("i1", "l"), _entry("i2", "m")]))
    assert delta["configs_changed"] == [
        {"id": "i1", "old": "s", "new": "l"},
        {"id": "i2", "old": None, "new": "m"},
    ]
    assert delta["changed"] is True
    assert delta["total_mass_g"]["delta"] == 0.0
    assert delta["instances_moved"] == [] and delta["mates_changed"] == []


def test_assembly_delta_reports_an_unbinding_and_ignores_an_untouched_one():
    delta = assembly_delta(_asm([_entry("i1", "s")]), _asm([_entry("i1")]))
    assert delta["configs_changed"] == [{"id": "i1", "old": "s", "new": None}]
    assert delta["changed"] is True

    same = _asm([_entry("i1", "s")])
    unchanged = assembly_delta(same, _asm([_entry("i1", "s")]))
    assert unchanged["configs_changed"] == []
    assert unchanged["changed"] is False


# ------------------------------------------------------------ the real thing


@pytest.mark.timeout(600)
class TestBoundAssembly:
    """One flange size family and one mated hinge, built for real.

    The template project is made once per class *with its configuration builds
    already warm*, so every clone inherits the `.cache` entries and each test
    pays for its own manifest, not for the geometry.
    """

    @pytest.fixture(scope="class")
    @classmethod
    def bound_projects(cls, kernel, tmp_path_factory):
        projects = tmp_path_factory.mktemp("configs_assembly_projects")
        svc = make_test_service(projects, kernel)
        svc.create_project("asm")
        svc.create_part("asm", "flange", script=FLANGE_SCRIPT)
        svc.store.update_part_entry("asm", "flange", configs=THREE_SIZE_CONFIGS)
        svc.create_part("asm", "hinge_base", script=HINGE_BASE_SCRIPT)
        svc.store.update_part_entry("asm", "hinge_base", configs=HINGE_CONFIGS)
        svc.create_part("asm", "flap", script=FLAP_SCRIPT)
        for part, names in (("flange", ("s", "l")),
                            ("hinge_base", ("low", "high"))):
            for name in names:
                assert svc._ensure_config_built("asm", part, name)["ok"]
        return projects

    @pytest.fixture
    def demo(self, kernel, tmp_path, bound_projects):
        return clone_test_service(bound_projects, tmp_path / "projects", kernel)

    @pytest.fixture
    def registry(self, demo):
        return build_registry(demo)

    # ------------------------------------------------------- get_assembly

    def test_each_bound_instance_reports_its_own_mass_and_mesh_key(self, demo):
        """AC6: two instances of one part, two sizes, two masses, two meshes —
        and the unbound instance still reports the working state's mesh."""
        asm = demo.set_assembly("asm", [
            {"id": "s1", "part": "flange", "position": [0, 0, 0],
             "config": "s"},
            {"id": "l1", "part": "flange", "position": [0, 400, 0],
             "config": "l"},
            {"id": "base1", "part": "flange", "position": [0, -400, 0]},
        ])
        by_id = {entry["id"]: entry for entry in asm["instances"]}
        assert [entry["state"] for entry in asm["instances"]] == ["ok"] * 3
        assert by_id["s1"]["config"] == "s" and by_id["l1"]["config"] == "l"
        assert "config" not in by_id["base1"]

        assert by_id["s1"]["mass_g"] < by_id["base1"]["mass_g"] \
            < by_id["l1"]["mass_g"]
        assert asm["total_mass_g"] == pytest.approx(
            sum(by_id[i]["mass_g"] for i in ("s1", "l1", "base1")))

        keys = {i: by_id[i]["mesh_key"] for i in ("s1", "l1", "base1")}
        assert len(set(keys.values())) == 3
        for iid, name in (("s1", "s"), ("l1", "l")):
            built = demo._ensure_config_built("asm", "flange", name)
            assert keys[iid] == built["cache_key"]
            assert (demo.store.cache_dir("asm") / f"{keys[iid]}.acm").is_file()
        # An unbound instance is byte-for-byte what it was before PRD-012.
        assert keys["base1"] == demo.mesh_info("asm", "flange")["key"]

    def test_a_second_get_assembly_publishes_nothing(self, demo):
        """The livelock guard: with one status slot per part, two instances
        bound to different configurations would republish `rebuild_finished`
        on alternate reads and drive the browser's refresh loop forever."""
        demo.set_assembly("asm", [
            {"id": "s1", "part": "flange", "config": "s"},
            {"id": "l1", "part": "flange", "position": [0, 400, 0],
             "config": "l"},
            {"id": "base1", "part": "flange", "position": [0, -400, 0]},
        ])
        events = demo.bus.subscribe()
        second = demo.get_assembly("asm")
        assert [e["state"] for e in second["instances"]] == ["ok"] * 3
        assert _drain(events) == []

    # --------------------------------------------------- check_interference

    def test_interference_measures_each_instance_at_its_own_size(self, demo):
        """The S pair is 10 mm apart at S and would overlap by 30 mm at the
        default size, so this fails in both directions if the binding is
        dropped."""
        demo.set_assembly("asm", [
            {"id": "s1", "part": "flange", "position": [0, 0, 0],
             "config": "s"},
            {"id": "s2", "part": "flange", "position": [110, 0, 0],
             "config": "s"},
            {"id": "l1", "part": "flange", "position": [0, 400, 0],
             "config": "l"},
            {"id": "l2", "part": "flange", "position": [0, 400, 5],
             "config": "l"},
        ])
        result = demo.check_interference("asm")
        assert result["checked"] == 4
        assert [{pair["a"], pair["b"]} for pair in result["pairs"]] == \
            [{"l1", "l2"}]
        assert result["pairs"][0]["volume_mm3"] > 1000.0

    # ---------------------------------------------------- export_assembly

    def test_export_assembly_sends_each_instances_configuration(
            self, demo, monkeypatch):
        demo.set_assembly("asm", [
            {"id": "s1", "part": "flange", "position": [0, 0, 0],
             "config": "s"},
            {"id": "l1", "part": "flange", "position": [0, 400, 0],
             "config": "l"},
            {"id": "base1", "part": "flange", "position": [0, -400, 0]},
        ])
        recorder = _Recorder(demo.kernel)
        monkeypatch.setattr(demo, "kernel", recorder)
        result = demo.export_assembly("asm", "stl")
        assert result["size_bytes"] > 500

        params = [item.get("params")
                  for item in recorder.params_of("export_assembly")["items"]]
        assert params == [S_PARAMS, L_PARAMS, {}]

    # -------------------------------------------------------------- mates

    def test_a_mated_connector_moves_with_the_bound_configuration(self, demo):
        """Task 3: the anchor's connector rides a configured parameter, so
        rebinding the anchor moves the part mated to it."""
        heights = {}
        for name in ("low", "high"):
            demo.set_assembly("asm", [
                {"id": "b1", "part": "hinge_base", "position": [0, 0, 0],
                 "config": name},
                {"id": "f1", "part": "flap", "mate": FLAP_MATE},
            ])
            resolved = {i.id: i for i in demo._resolved_instances("asm")}
            heights[name] = resolved["f1"].position[2]
        assert heights["high"] - heights["low"] == pytest.approx(
            HINGE_LIFT_MM, abs=1e-6)

    def test_sweep_motion_drives_a_config_bound_anchor(self, demo, registry):
        demo.set_assembly("asm", [
            {"id": "b1", "part": "hinge_base", "position": [0, 0, 0],
             "config": "high"},
            {"id": "f1", "part": "flap", "mate": FLAP_MATE},
        ])
        expected = {i.id: i for i in demo._resolved_instances("asm")}
        result = registry.call("sweep_motion", {
            "project": "asm", "instance": "f1",
            "angle_range": [0, 30], "samples": 3,
        })
        assert "error" not in result, result
        assert result["clear"] is True
        assert len(result["frames"]) == 3
        # The sweep re-resolves in the kernel from the items the tool builds:
        # the flap rides the HIGH hinge, not the default one.
        assert result["frames"][0]["f1"]["position"][2] == pytest.approx(
            expected["f1"].position[2], abs=1e-6)

    # ---------------------------------------------------------- render_view

    def test_render_view_of_a_part_writes_the_configured_file(self, registry):
        renders = {}
        for name in (None, "s", "l"):
            args = {"project": "asm", "part_id": "flange", "view": "top"}
            if name is not None:
                args["config"] = name
            result = registry.call("render_view", args)
            assert "error" not in result, result
            renders[name] = result
        # Base naming unchanged; a configuration is a middle segment.
        assert Path(renders[None]["path"]).name == "flange_top.png"
        assert "config" not in renders[None]
        for name in ("s", "l"):
            path = Path(renders[name]["path"])
            assert path.name == f"flange_{name}_top.png"
            assert path.parent.name == "renders" and path.is_file()
            assert renders[name]["config"] == name
        # Three sizes, three images (the render is of the built geometry).
        assert len({r["png_base64"] for r in renders.values()}) == 3

    def test_render_view_refuses_a_configuration_it_cannot_serve(
            self, registry):
        unknown = registry.call("render_view",
                                {"project": "asm", "part_id": "flange",
                                 "config": "xl"})
        assert unknown["error"]["type"] == "validation_error"
        assert "xl" in unknown["error"]["message"]
        # An assembly render takes each instance's own binding, so a top-level
        # configuration there is a question this tool cannot answer.
        assembly = registry.call("render_view",
                                 {"project": "asm", "config": "s"})
        assert assembly["error"]["type"] == "validation_error"
        assert "part_id" in assembly["error"]["message"]

    def test_render_view_of_an_assembly_renders_every_binding(
            self, demo, registry, monkeypatch):
        demo.set_assembly("asm", [
            {"id": "s1", "part": "flange", "position": [0, 0, 0],
             "config": "s"},
            {"id": "l1", "part": "flange", "position": [0, 400, 0],
             "config": "l"},
            {"id": "base1", "part": "flange", "position": [0, -400, 0]},
        ])
        seen = []
        real = demo.ensure_mesh

        def spy(proj, part_id, *, config=None):
            seen.append((part_id, config))
            return real(proj, part_id, config=config)

        monkeypatch.setattr(demo, "ensure_mesh", spy)
        result = registry.call("render_view", {"project": "asm"})
        assert "error" not in result, result
        assert seen == [("flange", "s"), ("flange", "l"), ("flange", None)]
        assert result["path"].endswith("assembly_iso.png")
        assert len(result["png_base64"]) > 1000


    def test_render_view_skips_an_instance_whose_binding_went_stale(
            self, demo, registry):
        """A configuration the part no longer declares is one unbuildable
        instance, not a failed image — `packet._render_assembly` degrades the
        same way."""
        demo.set_assembly("asm", [
            {"id": "s1", "part": "flange", "position": [0, 0, 0],
             "config": "s"},
            {"id": "base1", "part": "flange", "position": [0, -400, 0]},
        ])
        _stale_binding(demo, "asm", "s1")
        result = registry.call("render_view", {"project": "asm"})
        assert "error" not in result, result
        assert result["skipped"] == ["s1"]
        assert len(result["png_base64"]) > 1000


class _Recorder:
    """A kernel proxy that records requests without touching the shared
    session client (the `kernel` fixture is session-scoped)."""

    def __init__(self, inner):
        self.inner = inner
        self.calls: list[tuple[str, dict]] = []

    def request(self, method, params, **kwargs):
        self.calls.append((method, params))
        return self.inner.request(method, params, **kwargs)

    def params_of(self, method: str) -> dict:
        return next(p for m, p in self.calls if m == method)

    def __getattr__(self, name):
        return getattr(self.inner, name)
