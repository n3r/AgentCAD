"""The review packet (PRD-002 slice 4): the pure deltas and the builder.

Section 1 is plain dicts — no git, no kernel, no service — because the four
delta functions are pure by contract. Section 2 drives the real builder over
the two branch worktrees PRD-001 maintains, so it carries ``integration`` +
``portability`` and skips without git; the cases that build geometry are
``slow``.

The budget case (AC2) measures a *warm* regeneration on a copy of
``examples/rocketry`` — the example is never mutated in place.
"""

from __future__ import annotations

import base64
import json
import re
import shutil
import struct
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agentcad.core import locks
from agentcad.core.branches import pinned_tree_var
from agentcad.core.model import ConflictError, NotFoundError, ValidationError
from agentcad.core.packet import (
    PacketBuilder,
    assembly_delta,
    changed_parts,
    metric_delta,
    params_delta,
    params_spec,
)
from agentcad.core.service import AgentCADService, EventBus
from agentcad.core.tools import build_registry
from agentcad.kernel import acm

from .conftest import BOX_SCRIPT

_GIT = [
    pytest.mark.integration,
    pytest.mark.portability,
    pytest.mark.skipif(shutil.which("git") is None, reason="git not found on PATH"),
]

ROCKETRY = Path(__file__).resolve().parent.parent / "examples" / "rocketry"

CUBE = '''\
from build123d import *
PARAMS = {"s": {"default": 20.0, "min": 5.0, "max": 50.0, "unit": "mm",
                "description": "cube edge"}}
def build(p):
    return Box(p.s, p.s, p.s)
'''

CUBE_HOLE = CUBE.replace(
    "    return Box(p.s, p.s, p.s)\n",
    "    return Box(p.s, p.s, p.s) - Cylinder(3.0, p.s * 2)\n",
)

BROKEN_SCRIPT = CUBE.replace("return Box(p.s, p.s, p.s)", "return no_such_name")

HOLE_MM3 = 3.14159265358979 * 3.0 ** 2 * 20.0


@pytest.fixture(autouse=True)
def _reset_context():
    """Identity and the pin are ContextVars: rebind them per test."""
    cid = locks.client_id_var.set("local")
    pin = pinned_tree_var.set(None)
    yield
    locks.client_id_var.reset(cid)
    pinned_tree_var.reset(pin)


# ------------------------------------------------------- 1. the pure deltas


def _manifest(*entries) -> dict:
    return {"schema_version": 1, "name": "demo", "parts": list(entries)}


def _entry(pid: str, **overrides) -> dict:
    entry = {"id": pid, "label": pid, "material": "alu6061", "params": {}}
    entry.update(overrides)
    return entry


def test_changed_parts_classifies_added_removed_and_modified():
    rows = changed_parts(
        _manifest(_entry("box"), _entry("gone")),
        _manifest(_entry("box", params={"size": 12.0}), _entry("fresh")),
        {"fresh"},
    )
    assert rows == [
        {"part": "box", "change": "modified", "changed_by": ["params"]},
        {"part": "fresh", "change": "added", "changed_by": ["script"]},
        {"part": "gone", "change": "removed", "changed_by": []},
    ]


def test_changed_parts_separates_script_bytes_from_manifest_edits():
    rows = changed_parts(
        _manifest(_entry("box"), _entry("pin")),
        _manifest(_entry("box"), _entry("pin", label="Pin v2")),
        {"box"},
    )
    assert rows == [
        {"part": "box", "change": "modified", "changed_by": ["script"]},
        {"part": "pin", "change": "modified", "changed_by": ["manifest"]},
    ]


def test_a_changed_script_for_a_part_on_neither_side_is_ignored():
    assert changed_parts(_manifest(), _manifest(), {"ghost"}) == []


def test_params_delta_lists_added_removed_and_changed_values():
    delta = params_delta(
        _entry("box", params={"size": 10.0, "gone": 2.0}),
        _entry("box", params={"size": 12.0, "fresh": 1.0}),
    )
    assert delta["added"] == [{"name": "fresh", "value": 1.0}]
    assert delta["removed"] == [{"name": "gone", "value": 2.0}]
    assert delta["changed"] == [
        {"name": "size", "field": "value", "old": 10.0, "new": 12.0}
    ]


def test_params_delta_comparison_is_type_qualified():
    """6 and 6.0 are different values — exactly how ``_normalize_param``
    stores them, and the comparison ``manifest_merge._norm`` makes."""
    delta = params_delta(_entry("b", params={"n": 6}),
                         _entry("b", params={"n": 6.0}))
    assert delta["changed"] == [
        {"name": "n", "field": "value", "old": 6, "new": 6.0}
    ]
    assert params_delta(_entry("b", params={"n": 6}),
                        _entry("b", params={"n": 6}))["changed"] == []


def test_params_delta_reports_spec_fields_one_row_each():
    delta = params_delta(
        _entry("b", params={"wall": {"default": 2.0, "min": 1.0, "unit": "mm"}}),
        _entry("b", params={"wall": {"default": 1.6, "min": 1.0, "unit": "in",
                                     "description": "wall"}}),
    )
    assert delta["changed"] == [
        {"name": "wall", "field": "default", "old": 2.0, "new": 1.6},
        {"name": "wall", "field": "unit", "old": "mm", "new": "in"},
        {"name": "wall", "field": "description", "old": None, "new": "wall"},
    ]


def test_params_delta_reports_script_declaration_changes():
    """FR4: a default or a max edited in the script changes no manifest
    override at all, so the structured diff used to be empty while the script
    diff was full."""
    old = 'PARAMS = {"wall": {"default": 2.0, "min": 1.0, "max": 5.0}}\n'
    new = 'PARAMS = {"wall": {"default": 1.6, "min": 1.0, "max": 8.0},\n' \
          '          "fresh": {"default": 1.0}}\n'

    delta = params_delta(_entry("b"), _entry("b"),
                         params_spec(old), params_spec(new))

    assert delta["changed"] == [
        {"name": "wall", "field": "spec.default", "old": 2.0, "new": 1.6,
         "source": "spec"},
        {"name": "wall", "field": "spec.max", "old": 5.0, "new": 8.0,
         "source": "spec"},
    ]
    assert delta["added"] == [
        {"name": "fresh", "value": {"default": 1.0}, "source": "spec"}]
    assert delta["removed"] == []


def test_params_delta_still_reports_overrides_beside_the_declaration():
    delta = params_delta(
        _entry("b", params={"wall": 2.0}), _entry("b", params={"wall": 3.0}),
        params_spec('PARAMS = {"wall": {"default": 2.0}}\n'),
        params_spec('PARAMS = {"wall": {"default": 2.0}}\n'),
    )
    assert delta["changed"] == [
        {"name": "wall", "field": "value", "old": 2.0, "new": 3.0}]


def test_params_spec_reads_a_declaration_without_executing_the_script():
    """The kernel is the only thing that runs a script; a review packet must
    never become a reason to."""
    spec = params_spec(
        'raise SystemExit("this script must not run")\n'
        'PARAMS = {"s": {"default": 3.0, "type": "number"}}\n'
    )
    assert spec == {"s": {"default": 3.0, "type": "number"}}
    # not a literal, so it is not guessed at
    assert params_spec('SIZE = 3.0\nPARAMS = {"s": {"default": SIZE}}\n') == {}
    assert params_spec("def build(p):\n    return None\n") == {}
    assert params_spec("this is not python") == {}
    assert params_spec(None) == {}


def _instance(iid: str, part: str = "box", pos=(0.0, 0.0, 0.0),
              rot=(0.0, 0.0, 0.0), mate=None) -> dict:
    entry = {"id": iid, "part": part, "position": list(pos),
             "rotation_deg": list(rot)}
    if mate is not None:
        entry["mate"] = mate
    return entry


def _assembly(instances, mass: float = 0.0) -> dict:
    return {"instances": instances, "total_mass_g": mass, "bbox": None}


def test_assembly_delta_reports_added_removed_and_moved_instances():
    delta = assembly_delta(
        _assembly([_instance("a"), _instance("b"), _instance("gone")], 10.0),
        _assembly([_instance("a"), _instance("b", pos=(5.0, 0.0, 0.0)),
                   _instance("fresh")], 12.5),
    )
    assert delta["changed"] is True
    assert delta["instances_added"] == [{"id": "fresh", "part": "box"}]
    assert delta["instances_removed"] == [{"id": "gone", "part": "box"}]
    assert delta["instances_moved"] == [{
        "id": "b", "part": "box",
        "old": {"position": [0.0, 0.0, 0.0], "rotation_deg": [0.0, 0.0, 0.0]},
        "new": {"position": [5.0, 0.0, 0.0], "rotation_deg": [0.0, 0.0, 0.0]},
    }]
    assert delta["total_mass_g"] == {"old": 10.0, "new": 12.5, "delta": 2.5,
                                     "pct": 25.0}
    assert delta["renders"] is None


def test_assembly_delta_tracks_mates_added_changed_and_cleared():
    old_mate = {"to_instance": "a", "connector": "top"}
    new_mate = {"to_instance": "a", "connector": "bottom"}
    delta = assembly_delta(
        _assembly([_instance("a"), _instance("b", mate=old_mate),
                   _instance("c", mate=old_mate)]),
        _assembly([_instance("a", mate=new_mate),
                   _instance("b", mate=new_mate), _instance("c")]),
    )
    assert delta["mates_changed"] == [
        {"id": "a", "old": None, "new": new_mate},
        {"id": "b", "old": old_mate, "new": new_mate},
        {"id": "c", "old": old_mate, "new": None},
    ]
    assert delta["instances_moved"] == []
    assert delta["changed"] is True


def test_assembly_delta_pct_is_null_when_the_old_mass_is_zero():
    delta = assembly_delta(_assembly([], 0.0), _assembly([], 4.0))
    assert delta["total_mass_g"] == {"old": 0.0, "new": 4.0, "delta": 4.0,
                                     "pct": None}
    assert delta["changed"] is True


def test_an_untouched_assembly_reports_no_change():
    same = _assembly([_instance("a")], 8.0)
    delta = assembly_delta(same, json.loads(json.dumps(same)))
    assert delta["changed"] is False
    assert delta["instances_added"] == delta["instances_removed"] == []
    assert delta["instances_moved"] == delta["mates_changed"] == []
    assert delta["total_mass_g"]["delta"] == 0.0


def _metrics(volume=1000.0, mass=2.7, area=600.0, com=(0.0, 0.0, 5.0),
             bbox=((-5.0, -5.0, 0.0), (5.0, 5.0, 10.0)), **extra) -> dict:
    metrics = {
        "volume_mm3": volume, "mass_g": mass, "area_mm2": area,
        "center_of_mass": list(com),
        "bbox": {"min": list(bbox[0]), "max": list(bbox[1])},
    }
    metrics.update(extra)
    return metrics


def test_metric_delta_reports_old_new_delta_and_pct():
    delta = metric_delta(_metrics(), _metrics(volume=900.0, mass=2.43))
    assert delta["volume_mm3"] == {"old": 1000.0, "new": 900.0,
                                   "delta": -100.0, "pct": -10.0}
    assert delta["mass_g"]["delta"] == pytest.approx(-0.27)
    assert delta["area_mm2"]["delta"] == 0.0


def test_metric_delta_pct_is_null_when_the_old_value_is_zero():
    delta = metric_delta(_metrics(volume=0.0), _metrics(volume=50.0))
    assert delta["volume_mm3"] == {"old": 0.0, "new": 50.0, "delta": 50.0,
                                   "pct": None}


def test_metric_delta_center_of_mass_is_per_axis():
    delta = metric_delta(_metrics(), _metrics(com=(1.0, 0.0, 4.0)))
    assert delta["center_of_mass"] == {
        "old": [0.0, 0.0, 5.0], "new": [1.0, 0.0, 4.0],
        "delta": [1.0, 0.0, -1.0],
    }


def test_metric_delta_bbox_reports_both_boxes_and_the_size_delta():
    delta = metric_delta(
        _metrics(),
        _metrics(bbox=((-5.0, -5.0, 0.0), (5.0, 5.0, 14.0))),
    )
    assert delta["bbox"]["old"] == {"min": [-5.0, -5.0, 0.0],
                                    "max": [5.0, 5.0, 10.0]}
    assert delta["bbox"]["size_delta_mm"] == [0.0, 0.0, 4.0]


def test_metric_delta_reports_null_for_an_absent_side():
    delta = metric_delta(None, _metrics())
    assert delta["volume_mm3"] == {"old": None, "new": 1000.0, "delta": None,
                                   "pct": None}
    assert delta["center_of_mass"] == {"old": None, "new": [0.0, 0.0, 5.0],
                                       "delta": None}
    assert delta["bbox"] == {"old": None,
                             "new": {"min": [-5.0, -5.0, 0.0],
                                     "max": [5.0, 5.0, 10.0]},
                             "size_delta_mm": None}


def test_metric_delta_drops_the_center_of_mass_of_a_mesh_reference():
    """``build_reference`` reports an STL's bbox CENTER as its center of mass.
    Presenting a delta of that as a mass property would be a lie."""
    delta = metric_delta(_metrics(mesh=True), _metrics(mesh=True, com=(9.0, 0.0, 0.0)))
    assert delta["center_of_mass"] is None
    assert delta["volume_mm3"]["delta"] == 0.0


# ------------------------------------------------------------ 2. the builder


def _on(service, client: str, branch: str, proj: str = "demo") -> None:
    locks.set_client_id(client)
    if service.branches.current(proj) != branch:
        service.branches.switch(proj, branch)


def _script(registry, part: str, text: str, proj: str = "demo") -> dict:
    return registry.call("update_part_script",
                         {"project": proj, "part_id": part, "script": text})


def _png_size(data: bytes) -> tuple[int, int]:
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", data[16:24])


class _CountingKernel:
    """Records every method that reaches the kernel (AC4's assertion)."""

    def __init__(self, inner) -> None:
        self._inner = inner
        self.methods: list[str] = []

    def request(self, method, params, **kwargs):
        self.methods.append(method)
        return self._inner.request(method, params, **kwargs)

    def __getattr__(self, name):
        return getattr(self._inner, name)


class TestPacketBuilder:
    pytestmark = _GIT

    @pytest.fixture
    def stack(self, kernel, tmp_path):
        """The real service (NOT make_test_service — the packet needs history
        snapshots and the versioning pack's ``service.branches``)."""
        service = AgentCADService(tmp_path / "projects", kernel, EventBus())
        registry = build_registry(service)
        assert getattr(service, "branches", None) is not None
        return service, registry

    @pytest.fixture
    def demo(self, stack):
        service, registry = stack
        assert "error" not in registry.call("create_project", {"name": "demo"})
        assert "error" not in registry.call(
            "create_part",
            {"project": "demo", "part_id": "box", "script": BOX_SCRIPT})
        service.branches.create("demo", "feat")
        return service, registry

    def _propose(self, registry, source: str = "feat", proj: str = "demo") -> str:
        locks.set_client_id("chat:main")
        result = registry.call(
            "proposal_create",
            {"project": proj, "source": source, "title": "Thinner wall"})
        assert "error" not in result, result
        return result["proposal"]["id"]

    # ------------------------------------------------------------- content

    @pytest.mark.slow
    def test_the_packet_reports_both_sides_from_the_branch_worktrees(self, demo):
        service, registry = demo
        _on(service, "agent_a", "feat")
        assert "error" not in _script(registry, "box", CUBE)
        assert "error" not in registry.call(
            "set_params", {"project": "demo", "part_id": "box",
                           "values": {"s": 24.0}})
        pid = self._propose(registry)

        queue = service.bus.subscribe()
        try:
            packet = PacketBuilder(service).packet("demo", pid)
        finally:
            service.bus.unsubscribe(queue)

        canonical = service.store.canonical_path_of("demo")
        assert packet["proposal"] == pid and packet["ok"] is True
        assert packet["stale"] is False and packet["frozen"] is False
        assert packet["source"] == "feat" and packet["target"] == "master"
        assert packet["source_head"] == service.history.resolve_branch(
            canonical, "feat")
        assert packet["target_head"] == service.history.resolve_branch(
            canonical, "master")
        assert packet["generated_by"] == "chat:main"

        section = packet["parts"][0]
        assert section["part"] == "box" and section["change"] == "modified"
        assert section["changed_by"] == ["script", "params"]
        assert section["script_diff"]["path"] == "parts/box.py"
        assert "+    return Box(p.s, p.s, p.s)" in section["script_diff"]["unified"]
        assert section["script_diff"]["added_lines"] > 0
        assert section["script_diff"]["hunks"][0]["header"].startswith("@@")
        added = section["params_diff"]["added"]
        assert added[0] == {"name": "s", "value": 24.0}  # the override
        # …and the script's own PARAMS declaration, which renamed size -> s
        assert [r["name"] for r in added if r.get("source") == "spec"] == ["s"]
        assert [(r["name"], r.get("source"))
                for r in section["params_diff"]["removed"]] == [("size", "spec")]
        assert section["build"] == {"old": {"ok": True}, "new": {"ok": True}}
        assert section["metrics"]["volume_mm3"]["new"] == pytest.approx(24.0 ** 3)
        assert section["geom_diff"]["available"] is True
        assert section["geom_diff"]["unchanged"] is False
        assert packet["summary"]["parts_changed"] == 1

        # the packet is persisted, audited and announced
        stored = json.loads(
            service.proposals.store.packet_path("demo", pid)
            .read_text(encoding="utf-8"))
        assert stored["source_head"] == packet["source_head"]
        actions = [e["action"] for e in service.proposals.store.audit("demo", pid)]
        assert actions == ["created", "packet_generated"]
        events = []
        while not queue.empty():
            events.append(queue.get_nowait())
        assert [e for e in events
                if e["type"] == "proposal_changed"][-1]["reason"] == "packet"
        assert service.proposals.get("demo", pid)["packet"]["ok"] is True

    def test_the_packet_stamp_is_zone_aware_utc(self, demo):
        """Slice 6 fold-back: ``generated`` carries the ``Z`` designator, like
        every proposal/audit stamp. Nothing else about the shape moves. An
        unchanged branch pair is the cheapest packet there is — no part rows,
        so no kernel work."""
        _service, registry = demo
        pid = self._propose(registry)

        packet = PacketBuilder(_service).packet("demo", pid)

        stamp = packet["generated"]
        assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", stamp), stamp
        parsed = datetime.fromisoformat(stamp)
        assert parsed.utcoffset() == timedelta(0)
        assert abs((datetime.now(timezone.utc) - parsed).total_seconds()) < 120

    @pytest.mark.slow
    def test_an_instance_move_does_no_per_part_kernel_work(self, demo, monkeypatch):
        """AC4 — the content-hash short circuit: the assembly delta is there,
        the parts are byte-identical, and the kernel is never asked."""
        service, registry = demo
        assert "error" not in registry.call(
            "set_assembly",
            {"project": "demo",
             "instances": [{"id": "box_1", "part": "box",
                            "position": [0, 0, 0], "rotation_deg": [0, 0, 0]}]})
        _on(service, "agent_a", "feat")
        assert "error" not in registry.call(
            "set_assembly",
            {"project": "demo",
             "instances": [{"id": "box_1", "part": "box",
                            "position": [5, 0, 0], "rotation_deg": [0, 0, 0]}]})
        # ...plus a manifest-only part edit, so the short circuit is exercised
        # on a part row and not merely by an empty list.
        assert "error" not in registry.call(
            "update_part_script",
            {"project": "demo", "part_id": "box", "label": "Box v2"})
        pid = self._propose(registry)

        counter = _CountingKernel(service.kernel)
        monkeypatch.setattr(service, "kernel", counter)
        packet = PacketBuilder(service).packet("demo", pid)

        assert not {"build", "build_reference", "geom_diff"} & set(counter.methods), \
            counter.methods
        assert packet["assembly"]["changed"] is True
        assert packet["assembly"]["instances_moved"][0]["id"] == "box_1"
        section = packet["parts"][0]
        assert section["changed_by"] == ["manifest"]
        assert section["geom_diff"] == {
            "available": True, "unchanged": True, "added_mm3": 0.0,
            "removed_mm3": 0.0, "added_mesh": None, "removed_mesh": None,
            "skipped": None,
        }

    @pytest.mark.slow
    def test_an_unbuildable_side_degrades_honestly(self, demo):
        """AC7 — the failing part carries the structured script error and the
        rest of the packet is intact."""
        service, registry = demo
        _on(service, "agent_a", "feat")
        _script(registry, "box", BROKEN_SCRIPT)  # written even though it fails
        pid = self._propose(registry)

        packet = PacketBuilder(service).packet("demo", pid)

        section = packet["parts"][0]
        assert packet["ok"] is True
        assert section["build"]["old"]["ok"] is True
        assert section["build"]["new"]["ok"] is False
        error = section["build"]["new"]["error"]
        assert error["type"] and error["details"]["traceback"]
        assert error["details"]["line"]
        assert section["metrics"]["volume_mm3"]["new"] is None
        assert section["metrics"]["volume_mm3"]["old"] > 0
        assert section["geom_diff"]["available"] is False
        assert section["renders"]["old"] and section["renders"]["new"] is None
        assert section["script_diff"]["unified"]

    @pytest.mark.slow
    def test_a_drilled_hole_reports_the_removed_volume_and_writes_a_mesh(self, demo):
        service, registry = demo
        assert "error" not in _script(registry, "box", CUBE)
        service.branches.create("demo", "hole")
        _on(service, "agent_a", "hole")
        assert "error" not in _script(registry, "box", CUBE_HOLE)
        pid = self._propose(registry, source="hole")

        packet = PacketBuilder(service).packet("demo", pid)

        diff = packet["parts"][0]["geom_diff"]
        assert diff["available"] is True and diff["unchanged"] is False
        assert diff["removed_mm3"] == pytest.approx(HOLE_MM3, rel=0.01)
        assert diff["added_mm3"] == 0.0
        assert diff["added_mesh"] is None
        assert diff["removed_mesh"] == (
            f"/api/projects/demo/proposals/{pid}/diff/box/removed.acm")
        mesh = service.proposals.store.asset_dir("demo", pid, "diff") / "box.removed.acm"
        assert mesh.read_bytes()[:4] == b"ACM1"
        assert len(acm.read(mesh)["indices"]) > 0

    @pytest.mark.slow
    def test_both_renders_share_one_frame(self, demo):
        service, registry = demo
        assert "error" not in _script(registry, "box", CUBE)
        service.branches.create("demo", "bigger")
        _on(service, "agent_a", "bigger")
        assert "error" not in registry.call(
            "set_params", {"project": "demo", "part_id": "box",
                           "values": {"s": 40.0}})
        pid = self._propose(registry, source="bigger")

        packet = PacketBuilder(service).packet("demo", pid)

        section = packet["parts"][0]
        renders = section["renders"]
        assert renders["view"] == "iso" and (renders["width"], renders["height"]) \
            == (640, 480)
        assert renders["old"] == f"/api/projects/demo/proposals/{pid}/render/old/box"
        assert renders["new"] == f"/api/projects/demo/proposals/{pid}/render/new/box"
        assets = service.proposals.store.asset_dir("demo", pid, "renders")
        sizes = set()
        for side in ("old", "new"):
            data = (assets / f"box.{side}.iso.png").read_bytes()
            sizes.add(_png_size(data))
        assert sizes == {(640, 480)}
        # the frame is the union of both world bboxes, inflated so a
        # silhouette never touches the edge
        boxes = section["metrics"]["bbox"]
        for axis in range(3):
            lo = min(boxes["old"]["min"][axis], boxes["new"]["min"][axis])
            hi = max(boxes["old"]["max"][axis], boxes["new"]["max"][axis])
            assert renders["frame"]["min"][axis] < lo
            assert renders["frame"]["max"][axis] > hi

    @pytest.mark.slow
    def test_the_assembly_renders_on_demand_from_either_side(self, demo):
        """The packet carries no assembly render (they are the expensive kind);
        ``proposal_render`` with no part draws one when a reviewer asks."""
        service, registry = demo
        instances = [{"id": "box_1", "part": "box", "position": [0, 0, 0],
                      "rotation_deg": [0, 0, 0]}]
        assert "error" not in registry.call(
            "set_assembly", {"project": "demo", "instances": instances})
        _on(service, "agent_a", "feat")
        moved = [{**instances[0], "position": [30, 0, 0]}]
        assert "error" not in registry.call(
            "set_assembly", {"project": "demo", "instances": moved})
        pid = self._propose(registry)

        builder = PacketBuilder(service)
        assert builder.packet("demo", pid)["assembly"]["renders"] is None
        image = builder.render("demo", pid, "new", view="front")
        assert _png_size(base64.b64decode(image["png_base64"])) == (640, 480)
        assert image["part"] is None and image["view"] == "front"

    def test_a_changed_import_is_reported_by_size_and_digest(self, demo):
        """Binary paths never reach a diff or a boolean — size + digest only,
        the contract ``merge._binary_conflict`` already uses."""
        service, registry = demo
        tree = service.branches.tree_of("demo", "feat")
        imports = tree / "imports"
        imports.mkdir(parents=True, exist_ok=True)
        (imports / "blob.stl").write_bytes(b"solid\x00binary payload")
        service.history.snapshot(tree, "add an import")
        pid = self._propose(registry)

        packet = PacketBuilder(service).packet("demo", pid)

        assert packet["parts"] == []
        assert len(packet["binary"]) == 1
        entry = packet["binary"][0]
        assert entry["path"] == "imports/blob.stl"
        assert entry["sides"]["old"] is None
        assert entry["sides"]["new"]["bytes"] == 20
        assert len(entry["sides"]["new"]["sha256"]) == 64
        assert "payload" not in json.dumps(entry)

    # ------------------------------------------------- caching and refusals

    @pytest.mark.slow
    def test_a_persisted_packet_is_served_until_a_head_moves(self, demo):
        service, registry = demo
        _on(service, "agent_a", "feat")
        assert "error" not in _script(registry, "box", CUBE)
        pid = self._propose(registry)
        builder = PacketBuilder(service)
        builder.packet("demo", pid)

        # A sentinel written into the persisted packet survives a read and is
        # gone after a real regeneration.
        path = service.proposals.store.packet_path("demo", pid)
        stored = json.loads(path.read_text(encoding="utf-8"))
        path.write_text(json.dumps({**stored, "sentinel": True}), encoding="utf-8")

        assert builder.packet("demo", pid)["sentinel"] is True
        assert "sentinel" not in builder.packet("demo", pid, regenerate=True)

        path.write_text(json.dumps({**stored, "sentinel": True}), encoding="utf-8")
        (service.branches.tree_of("demo", "feat") / "notes.txt").write_text(
            "note\n", encoding="utf-8")
        service.history.snapshot(service.branches.tree_of("demo", "feat"), "note")

        assert service.proposals.get("demo", pid)["packet"]["stale"] is True
        regenerated = builder.packet("demo", pid)  # on view
        assert "sentinel" not in regenerated
        assert regenerated["stale"] is False
        assert regenerated["source_head"] == service.history.resolve_branch(
            service.store.canonical_path_of("demo"), "feat")

    @pytest.mark.slow
    def test_a_script_only_params_change_reaches_the_structured_diff(self, demo):
        """FR4 end to end: both sides declare the same parameter, the source
        moves its default and its max, and no manifest override changes."""
        service, registry = demo
        tweaked = CUBE.replace('"default": 20.0', '"default": 16.0') \
                      .replace('"max": 50.0', '"max": 60.0')
        _on(service, "agent_a", "feat")
        assert "error" not in _script(registry, "box", tweaked)
        _on(service, "browser", "master")
        assert "error" not in _script(registry, "box", CUBE)
        pid = self._propose(registry)

        packet = PacketBuilder(service).packet("demo", pid)

        section = packet["parts"][0]
        assert section["changed_by"] == ["script"]  # no override moved
        assert section["params_diff"]["changed"] == [
            {"name": "s", "field": "spec.default", "old": 20.0, "new": 16.0,
             "source": "spec"},
            {"name": "s", "field": "spec.max", "old": 50.0, "new": 60.0,
             "source": "spec"},
        ]

    @pytest.mark.slow
    def test_a_source_commit_mid_build_forces_a_rebuild(self, demo, monkeypatch):
        """C4: the heads are read up front and the metrics, renders and
        booleans afterwards, off the live worktrees. A commit that lands in
        between used to produce a packet whose numbers came from one revision
        and whose label named another."""
        service, registry = demo
        _on(service, "agent_a", "feat")
        assert "error" not in _script(registry, "box", CUBE)
        pid = self._propose(registry)
        canonical = service.store.canonical_path_of("demo")
        tree = service.branches.tree_of("demo", "feat")

        commits = []
        inner = PacketBuilder._renders

        def committing(self, *args, **kwargs):
            result = inner(self, *args, **kwargs)
            if not commits:  # once: the second pass must see a still head
                (tree / "notes.txt").write_text("note\n", encoding="utf-8")
                service.history.snapshot(tree, "note")
                commits.append(service.history.resolve_branch(canonical, "feat"))
            return result

        monkeypatch.setattr(PacketBuilder, "_renders", committing)
        packet = PacketBuilder(service).packet("demo", pid)

        assert commits, "the hook never fired"
        assert packet["source_head"] == commits[0]  # rebuilt against the new head
        assert packet["stale"] is False

    @pytest.mark.slow
    def test_a_source_that_keeps_moving_is_marked_stale_not_mislabelled(
            self, demo, monkeypatch):
        """The honest fallback when one rebuild is not enough: the packet says
        it is already out of date rather than labelling mixed evidence with a
        head it no longer describes."""
        service, registry = demo
        _on(service, "agent_a", "feat")
        assert "error" not in _script(registry, "box", CUBE)
        pid = self._propose(registry)
        tree = service.branches.tree_of("demo", "feat")

        commits = []
        inner = PacketBuilder._renders

        def committing(self, *args, **kwargs):
            result = inner(self, *args, **kwargs)
            commits.append(len(commits))
            (tree / "notes.txt").write_text(f"note {len(commits)}\n",
                                            encoding="utf-8")
            service.history.snapshot(tree, "note")
            return result

        monkeypatch.setattr(PacketBuilder, "_renders", committing)
        packet = PacketBuilder(service).packet("demo", pid)

        assert len(commits) == 2  # measured twice, then persisted honestly
        assert packet["stale"] is True
        assert any("moved while this packet" in w for w in packet["warnings"])

    @pytest.mark.slow
    def test_a_failed_git_read_is_a_named_error_not_empty_evidence(
            self, demo, monkeypatch):
        """C8: a `git diff` whose return code nobody checked became "no script
        changed", and the packet still said ok."""
        service, registry = demo
        _on(service, "agent_a", "feat")
        assert "error" not in _script(registry, "box", CUBE)
        pid = self._propose(registry)
        inner = service.history._run

        def failing(path, *args, **kwargs):
            if args[:2] == ("diff", "--no-color"):
                return subprocess.CompletedProcess(
                    ["git", *args], 128, "", "fatal: bad object HEAD")
            return inner(path, *args, **kwargs)

        monkeypatch.setattr(service.history, "_run", failing)
        packet = PacketBuilder(service).packet("demo", pid)

        assert packet["ok"] is False
        fatal = [e for e in packet["errors"] if e.get("fatal")]
        assert [e["stage"] for e in fatal] == ["script_diffs"]
        assert fatal[0]["command"].startswith("git diff --unified=3")
        assert "bad object" in fatal[0]["error"]["message"]
        assert packet["parts"][0]["script_diff"] is None

    @pytest.mark.slow
    def test_a_manifest_that_cannot_be_read_is_not_a_deleted_project(
            self, demo, monkeypatch):
        """The same lie one step out: a ``cat-file`` that failed — the ref is
        gone, the object is corrupt, the side deleted ``project.json`` — came
        back as ``{}``, which the delta reads as "this side removed every
        part"."""
        service, registry = demo
        _on(service, "agent_a", "feat")
        assert "error" not in _script(registry, "box", CUBE)
        pid = self._propose(registry)
        canonical = service.store.canonical_path_of("demo")
        head = service.history.resolve_branch(canonical, "feat")
        inner = service.history._run

        def failing(path, *args, **kwargs):
            if args[:2] == ("cat-file", "blob") \
                    and args[2] == f"{head}:project.json":
                return subprocess.CompletedProcess(
                    ["git", *args], 128,
                    "", f"fatal: path 'project.json' does not exist in {head}")
            return inner(path, *args, **kwargs)

        monkeypatch.setattr(service.history, "_run", failing)
        packet = PacketBuilder(service).packet("demo", pid)

        assert packet["ok"] is False
        fatal = [e for e in packet["errors"] if e.get("fatal")]
        assert [e["stage"] for e in fatal] == ["manifest"]
        assert "project.json" in fatal[0]["command"]
        assert fatal[0]["ref"] == "feat"
        assert any("could not be read" in w for w in packet["warnings"])

    @pytest.mark.slow
    def test_a_regeneration_owns_the_whole_diff_directory(self, demo):
        """C9: regeneration only removed the meshes of the parts it processed,
        so a part a later packet no longer has kept serving the previous
        generation's geometry from its predictable URL."""
        service, registry = demo
        _on(service, "agent_a", "feat")
        assert "error" not in _script(registry, "box", CUBE_HOLE)
        pid = self._propose(registry)
        builder = PacketBuilder(service)
        assert builder.packet("demo", pid)["parts"][0]["geom_diff"]["available"]
        diff_dir = service.proposals.store.asset_dir("demo", pid, "diff")
        assert list(diff_dir.glob("box.*.acm"))

        # the source goes back to what the target has: the proposal now
        # changes nothing, so the packet has no part rows at all
        _on(service, "agent_a", "feat")
        assert "error" not in _script(registry, "box", BOX_SCRIPT)
        assert builder.packet("demo", pid)["parts"] == []

        assert list(diff_dir.glob("*")) == []
        with pytest.raises(NotFoundError):
            service.packets.diff_mesh_path("demo", pid, "box", "removed")

    def test_a_frozen_packet_refuses_to_regenerate(self, demo):
        """FR12: the evidence a decision was made on is never regenerated."""
        service, registry = demo
        pid = self._propose(registry)
        path = service.proposals.store.packet_path("demo", pid)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"proposal": pid, "ok": True,
                                    "frozen": True, "parts": []}),
                        encoding="utf-8")
        builder = PacketBuilder(service)

        assert builder.packet("demo", pid)["frozen"] is True
        with pytest.raises(ConflictError) as excinfo:
            builder.packet("demo", pid, regenerate=True)
        assert excinfo.value.details["id"] == pid

    def _approve_and_merge(self, registry, pid: str, proj: str = "demo") -> dict:
        locks.set_client_id("browser")
        assert "error" not in registry.call(
            "proposal_review",
            {"project": proj, "id": pid, "verdict": "approve"})
        merged = registry.call("proposal_merge", {"project": proj, "id": pid})
        assert "error" not in merged, merged
        return merged

    def test_a_merged_proposal_serves_the_frozen_absence_of_a_packet(self, demo):
        """FR12 for the packet that was never generated: a build started now
        would measure the merged target and whatever else has landed on it and
        publish that as this proposal's evidence. The absence is frozen
        instead, and it says so."""
        service, registry = demo
        pid = self._propose(registry)
        self._approve_and_merge(registry, pid)
        builder = PacketBuilder(service)

        packet = builder.packet("demo", pid)

        assert packet["frozen"] is True and packet["stale"] is False
        assert packet["generated"] is None and packet["ok"] is False
        assert packet["parts"] == [] and packet["source_head"] is None
        assert "no review packet was generated" in packet["note"]
        with pytest.raises(ConflictError) as excinfo:
            builder.packet("demo", pid, regenerate=True)
        assert excinfo.value.details["id"] == pid
        actions = [e["action"]
                   for e in service.proposals.store.audit("demo", pid)]
        assert "packet_generated" not in actions

    @pytest.mark.slow
    def test_a_packet_frozen_behind_the_commits_that_merged_says_so(self, demo):
        """C2: freezing sets ``stale: false`` (a pinned packet cannot be stale
        against today's heads) and that used to swallow the one staleness that
        matters — the evidence describes older commits than the ones that
        landed."""
        service, registry = demo
        _on(service, "agent_a", "feat")
        assert "error" not in _script(registry, "box", CUBE)
        pid = self._propose(registry)
        builder = PacketBuilder(service)
        generated = builder.packet("demo", pid)
        tree = service.branches.tree_of("demo", "feat")
        (tree / "notes.txt").write_text("note\n", encoding="utf-8")
        service.history.snapshot(tree, "note")  # the source moves on

        self._approve_and_merge(registry, pid)

        frozen = builder.packet("demo", pid)
        assert frozen["frozen"] is True and frozen["stale"] is False
        assert frozen["stale_at_merge"] is True
        assert frozen["source_head"] == generated["source_head"]
        assert service.proposals.get("demo", pid)["packet"] == {
            "generated": generated["generated"], "stale": False,
            "stale_at_merge": True, "ok": True, "frozen": True}

    @pytest.mark.slow
    def test_a_packet_that_described_what_merged_is_not_stale_at_merge(self, demo):
        service, registry = demo
        _on(service, "agent_a", "feat")
        assert "error" not in _script(registry, "box", CUBE)
        pid = self._propose(registry)
        builder = PacketBuilder(service)
        builder.packet("demo", pid)

        self._approve_and_merge(registry, pid)

        assert builder.packet("demo", pid)["stale_at_merge"] is False
        assert service.proposals.get(
            "demo", pid)["packet"]["stale_at_merge"] is False

    @pytest.mark.slow
    def test_a_terminal_proposal_is_never_measured_again(self, demo):
        """A closed proposal keeps the packet it had — a moved head does not
        regenerate it, and nothing re-checkpoints the branch worktrees on its
        behalf."""
        service, registry = demo
        pid = self._propose(registry)
        builder = PacketBuilder(service)
        generated = builder.packet("demo", pid)["generated"]

        locks.set_client_id("browser")
        assert "error" not in registry.call(
            "proposal_update", {"project": "demo", "id": pid, "state": "closed"})
        _on(service, "agent_a", "feat")
        assert "error" not in _script(registry, "box", CUBE)

        assert builder.packet("demo", pid)["generated"] == generated
        with pytest.raises(ConflictError) as excinfo:
            builder.packet("demo", pid, regenerate=True)
        assert excinfo.value.details["state"] == "closed"

    @pytest.mark.slow
    def test_a_build_that_loses_the_race_to_a_merge_is_discarded(
            self, demo, monkeypatch):
        """``packet.json`` and ``proposal.json`` have ONE writer order. A build
        that was overtaken by the merge may neither unfreeze the evidence the
        decision was made on nor hand ``proposal.json`` back its pre-merge
        state — so it is thrown away and the frozen packet is served."""
        service, registry = demo
        _on(service, "agent_a", "feat")
        assert "error" not in _script(registry, "box", CUBE)
        pid = self._propose(registry)
        builder = PacketBuilder(service)
        builder.packet("demo", pid)  # the packet the merge will freeze

        ready, release = threading.Event(), threading.Event()
        inner = PacketBuilder._persist

        def waiting(self, proj, pid_, packet):
            ready.set()
            release.wait(120)
            return inner(self, proj, pid_, packet)

        monkeypatch.setattr(PacketBuilder, "_persist", waiting)
        out: dict = {}

        def rebuild() -> None:
            locks.set_client_id("chat:main")
            try:
                out["packet"] = builder.packet("demo", pid, regenerate=True)
            except Exception as exc:  # noqa: BLE001 — reported by the test
                out["error"] = exc

        thread = threading.Thread(target=rebuild)
        thread.start()
        try:
            assert ready.wait(300), "the build never reached its persist"
            _on(service, "browser", "master")
            merged = self._approve_and_merge(registry, pid)
        finally:
            release.set()
            thread.join(300)

        assert "error" not in out, out
        stored = json.loads(
            service.proposals.store.packet_path("demo", pid)
            .read_text(encoding="utf-8"))
        assert stored["frozen"] is True  # never unfrozen by the late writer
        detail = service.proposals.get("demo", pid)
        assert detail["proposal"]["state"] == "merged"  # never reverted
        assert detail["proposal"]["merge"]["commit"] == merged["commit"]
        assert detail["packet"]["frozen"] is True
        assert out["packet"]["frozen"] is True  # the loser serves the evidence
        assert [e["action"] for e in detail["audit"]].count(
            "packet_generated") == 1

    @pytest.mark.slow
    def test_a_merge_cannot_interleave_a_packets_read_modify_write(
            self, demo, monkeypatch):
        """The other direction of the same race: while the packet's
        read-modify-write of ``proposal.json`` is in flight, a merge waits for
        it instead of landing between the read and the write (which used to
        leave the merged proposal back at 'approved' with ``merge: null``)."""
        service, registry = demo
        pid = self._propose(registry)
        builder = PacketBuilder(service)
        builder.packet("demo", pid)
        locks.set_client_id("browser")
        assert "error" not in registry.call(
            "proposal_review",
            {"project": "demo", "id": pid, "verdict": "approve"})

        writing, release = threading.Event(), threading.Event()
        packet_thread: dict = {}
        inner = type(service.proposals.store).save

        def blocking(self, proj, proposal):
            if threading.current_thread() is packet_thread.get("thread"):
                writing.set()
                release.wait(120)
            return inner(self, proj, proposal)

        monkeypatch.setattr(type(service.proposals.store), "save", blocking)
        merged: dict = {}

        def rebuild() -> None:
            locks.set_client_id("chat:main")
            builder.packet("demo", pid, regenerate=True)

        def merge() -> None:
            locks.set_client_id("browser")
            merged["result"] = registry.call(
                "proposal_merge", {"project": "demo", "id": pid})

        thread = threading.Thread(target=rebuild)
        packet_thread["thread"] = thread
        thread.start()
        assert writing.wait(300), "the build never reached its save"
        merger = threading.Thread(target=merge)
        merger.start()
        try:
            # The merge is behind the packet's write, not interleaved with it.
            merger.join(1.5)
            assert merger.is_alive(), "the merge did not wait for the packet"
        finally:
            release.set()
            thread.join(300)
            merger.join(300)

        assert "error" not in merged["result"], merged
        detail = service.proposals.get("demo", pid)
        assert detail["proposal"]["state"] == "merged"
        assert detail["packet"]["frozen"] is True

    @pytest.mark.slow
    def test_two_concurrent_builds_produce_one_packet_with_whole_assets(
            self, demo, monkeypatch):
        """Two builds of one proposal wrote over each other's renders and diff
        meshes (and each other's fixed-name .tmp files), so a packet could
        publish a URL for a file the other build had already unlinked. Builds
        of the same proposal are serialized, and the second caller gets the
        build it waited for."""
        service, registry = demo
        _on(service, "agent_a", "feat")
        assert "error" not in _script(registry, "box", CUBE_HOLE)
        pid = self._propose(registry)
        builder = PacketBuilder(service)

        rendering, release = threading.Event(), threading.Event()
        inner = PacketBuilder._renders

        def waiting(self, *args, **kwargs):
            result = inner(self, *args, **kwargs)
            rendering.set()
            release.wait(120)
            return result

        monkeypatch.setattr(PacketBuilder, "_renders", waiting)
        results: list[dict] = []
        errors: list[Exception] = []

        def build() -> None:
            locks.set_client_id("chat:main")
            try:
                results.append(builder.packet("demo", pid, regenerate=True))
            except Exception as exc:  # noqa: BLE001 — reported by the test
                errors.append(exc)

        first = threading.Thread(target=build)
        first.start()
        assert rendering.wait(300), "the first build never rendered"
        second = threading.Thread(target=build)
        second.start()
        try:
            release.set()
        finally:
            first.join(300)
            second.join(300)

        assert not errors, errors
        assert len(results) == 2
        assert results[0]["generated"] == results[1]["generated"]
        assert builder._slot("demo", pid)["builds"] == 1  # one build, not two
        for packet in results:
            section = packet["parts"][0]
            assert section["geom_diff"]["available"] is True
            assert section["geom_diff"]["removed_mesh"]
            assert service.packets.diff_mesh_path(
                "demo", pid, "box", "removed").is_file()
            for side in ("old", "new"):
                assert section["renders"][side]
                assert (service.proposals.store.asset_dir("demo", pid, "renders")
                        / f"box.{side}.iso.png").is_file()

    @pytest.mark.slow
    def test_a_side_that_cannot_be_read_is_not_reported_as_an_absent_part(
            self, demo, monkeypatch):
        """FR8 degrades honestly, and 'honestly' rules out the loudest number
        in the packet: a checkout that could not be READ is not a part that is
        not THERE, and must never become a whole-part added/removed volume."""
        service, registry = demo
        _on(service, "agent_a", "feat")
        assert "error" not in _script(registry, "box", CUBE)
        pid = self._propose(registry)

        reads: list[str] = []
        inner = service.store.get_part

        def failing(proj, part_id):
            reads.append(part_id)
            if len(reads) == 1:  # the old side, read first
                raise OSError("the branch worktree went away")
            return inner(proj, part_id)

        monkeypatch.setattr(service.store, "get_part", failing)
        packet = PacketBuilder(service).packet("demo", pid)

        section = packet["parts"][0]
        assert packet["ok"] is True
        assert section["build"]["old"]["ok"] is False
        assert "present" not in section["build"]["old"]
        assert section["build"]["old"]["error"]["message"]
        assert section["geom_diff"]["available"] is False
        assert "unreadable" in section["geom_diff"]["reason"]
        assert "added_mm3" not in section["geom_diff"]
        # the readable side still reports its own numbers; the delta does not
        # pretend the unreadable side measured zero
        assert section["metrics"]["volume_mm3"]["old"] is None
        assert section["metrics"]["volume_mm3"]["new"] == pytest.approx(20.0 ** 3)
        assert section["metrics"]["volume_mm3"]["delta"] is None
        assert [e["stage"] for e in packet["errors"]] == ["build"]

    @pytest.mark.slow
    def test_an_on_demand_render_is_written_to_the_path_it_reports(self, demo):
        """``path`` named a file nobody had written for every view but the
        packet's own iso pair. Draw it once, then serve it from there."""
        service, registry = demo
        assert "error" not in registry.call(
            "set_assembly",
            {"project": "demo",
             "instances": [{"id": "box_1", "part": "box",
                            "position": [0, 0, 0], "rotation_deg": [0, 0, 0]}]})
        _on(service, "agent_a", "feat")
        assert "error" not in _script(registry, "box", CUBE)
        pid = self._propose(registry)
        builder = PacketBuilder(service)

        for image in (builder.render("demo", pid, "new", part="box",
                                     view="front"),
                      builder.render("demo", pid, "old", view="top")):
            path = Path(image["path"])
            assert path.is_file(), image["path"]
            assert path.read_bytes() == base64.b64decode(image["png_base64"])
            assert _png_size(path.read_bytes()) == (640, 480)

    @pytest.mark.slow
    def test_a_frozen_packet_only_serves_the_renders_taken_with_it(self, demo):
        """FR12 again: a view the frozen packet never had would be drawn from
        today's branches and shown as the evidence of a past decision. Refused
        like a regeneration; the stored pair is still served."""
        service, registry = demo
        _on(service, "agent_a", "feat")
        assert "error" not in _script(registry, "box", CUBE)
        pid = self._propose(registry)
        builder = PacketBuilder(service)
        builder.packet("demo", pid)
        _on(service, "browser", "master")
        self._approve_and_merge(registry, pid)
        assert builder.packet("demo", pid)["frozen"] is True

        image = builder.render("demo", pid, "new", part="box")
        assert _png_size(base64.b64decode(image["png_base64"])) == (640, 480)

        with pytest.raises(ConflictError) as excinfo:
            builder.render("demo", pid, "new", part="box", view="front")
        assert excinfo.value.details["view"] == "front"
        with pytest.raises(ConflictError):
            builder.render("demo", pid, "old")  # no assembly render was taken

    def test_a_dirty_branch_tree_that_cannot_be_snapshotted_is_refused(
            self, demo, monkeypatch):
        """Never a packet pinned to heads that do not describe the measured
        bytes (the ``BranchManager._checkpoint`` rule)."""
        service, registry = demo
        pid = self._propose(registry)
        tree = service.branches.tree_of("demo", "feat")
        (tree / "parts" / "box.py").write_text(CUBE, encoding="utf-8")
        monkeypatch.setattr(service.history, "snapshot", lambda *a, **k: None)

        with pytest.raises(ConflictError) as excinfo:
            PacketBuilder(service).packet("demo", pid)
        assert "feat" in str(excinfo.value)
        assert not service.proposals.store.packet_path("demo", pid).exists()

    def test_an_unreadable_manifest_on_a_ref_is_a_validation_error(self, demo):
        service, registry = demo
        pid = self._propose(registry)
        tree = service.branches.tree_of("demo", "feat")
        (tree / "project.json").write_text("{not json", encoding="utf-8")
        service.history.snapshot(tree, "break the manifest")

        with pytest.raises(ValidationError) as excinfo:
            PacketBuilder(service).packet("demo", pid)
        assert excinfo.value.details["ref"] == "feat"
        assert excinfo.value.details["file"] == "project.json"

    # ---------------------------------------------------------- the budget

    @pytest.mark.slow
    @pytest.mark.timeout(900)
    @pytest.mark.skipif(not (ROCKETRY / "project.json").is_file(),
                        reason="rocketry example not present")
    def test_ac2_the_rocketry_packet_generates_warm_under_ten_seconds(
            self, stack, tmp_path):
        service, registry = stack
        dest = tmp_path / "ex" / "rocketry"
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(ROCKETRY, dest,
                        ignore=shutil.ignore_patterns(".cache", "exports"))
        opened = registry.call("open_project", {"path": str(dest)})
        assert "error" not in opened, opened
        proj = opened["name"]

        service.branches.create(proj, "nozzle-thinner")
        _on(service, "chat:main", "nozzle-thinner", proj)
        nozzle = (dest / "parts" / "nozzle.py").read_text(encoding="utf-8")
        assert "error" not in registry.call(
            "update_part_script",
            {"project": proj, "part_id": "nozzle",
             "script": nozzle + "\n# thinner wall for the mass budget\n"})
        assert "error" not in registry.call(
            "set_params",
            {"project": proj, "part_id": "nozzle", "values": {"wall": 2.6}})
        created = registry.call(
            "proposal_create",
            {"project": proj, "source": "nozzle-thinner",
             "title": "Thin the nozzle wall to 2.6 mm"})
        assert "error" not in created, created
        pid = created["proposal"]["id"]

        builder = PacketBuilder(service)
        builder.packet(proj, pid)  # cold: warms both sides' caches
        started = time.monotonic()
        packet = builder.packet(proj, pid, regenerate=True)
        elapsed = time.monotonic() - started

        section = next(p for p in packet["parts"] if p["part"] == "nozzle")
        assert section["script_diff"]["unified"]
        assert section["params_diff"]["changed"] == [
            {"name": "wall", "field": "value", "old": 3.0, "new": 2.6}]
        assert section["metrics"]["mass_g"]["delta"] < 0
        assert section["renders"]["old"] and section["renders"]["new"]
        assert section["renders"]["frame"]["min"]
        assert section["geom_diff"]["available"] is True
        assert section["geom_diff"]["removed_mm3"] > 0
        assert elapsed < 10.0, f"warm packet took {elapsed:.2f}s"
