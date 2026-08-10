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
import shutil
import struct
import time
from pathlib import Path

import pytest

from agentcad.core import locks
from agentcad.core.branches import pinned_tree_var
from agentcad.core.model import ConflictError, ValidationError
from agentcad.core.packet import (
    PacketBuilder,
    assembly_delta,
    changed_parts,
    metric_delta,
    params_delta,
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
        assert section["params_diff"]["added"] == [{"name": "s", "value": 24.0}]
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
