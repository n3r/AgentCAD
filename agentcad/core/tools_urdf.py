"""Tool pack: URDF export (PRD-013 Decision 6, FR14 core / FR15).

`export_urdf` turns the resolved assembly graph into a URDF robot description +
one mesh per link, under `exports/urdf/<name>/`. It is OCP-free itself — all the
geometry it needs (mass, inertia, connector frames, transforms) comes from the
kernel through the service; the XML assembly + parallel-axis inertia shift live
in `core/urdf.py`.

Mapping (MVP): instance → `<link>` (mass + COM-shifted inertia + mesh); rigid
mate → `fixed`; revolute → `revolute` with `<limit>` from the connector range
(`continuous` when unbounded); slider → `prismatic` with `<limit>` from
`linear_range`; planar → `planar`; cylindrical/ball → `fixed` + a named warning;
an UNMATED instance → `fixed` child of `world` + a warning (FR15).

Load order: sorts before `tools_versioning`, so any cross-pack seam is read
lazily in the tool body (this pack reads none). No merge gate.
"""

from __future__ import annotations

import math
import re

from . import urdf
from .model import ValidationError
from .tools import Tool, schema


def _sanitize(name: str) -> str:
    """A URDF-safe link/joint name from an assembly id (`stand/engine/p[0]`)."""
    return re.sub(r"[^A-Za-z0-9_]", "_", name)


# connector type → (urdf joint type, degrades?, range key)
_JOINT_MAP = {
    "rigid": ("fixed", False, None),
    "revolute": ("revolute", False, "range"),
    "slider": ("prismatic", False, "linear_range"),
    "planar": ("planar", False, None),
    "cylindrical": ("fixed", True, None),
    "ball": ("fixed", True, None),
}


def register(registry, service) -> None:
    def export_urdf(project: str, name: str | None = None,
                    mesh_format: str = "stl") -> dict:
        if mesh_format not in ("stl",):
            raise ValidationError("mesh_format supports: stl")
        robot_name = _sanitize(name or project)

        resolved = service._resolved_instances(project)
        if not resolved:
            raise ValidationError("assembly has no instances to export")

        names = {inst.id for inst in resolved}
        link_name = {inst.id: _sanitize(inst.id) for inst in resolved}

        out_dir = service.store.exports_dir(project) / "urdf" / robot_name
        mesh_dir = out_dir / "meshes"
        mesh_dir.mkdir(parents=True, exist_ok=True)

        warnings: list[dict] = []
        links: list[dict] = []
        # ---- links: mass + COM-shifted inertia + one mesh each -------------
        for inst in resolved:
            owner = getattr(inst, "origin_project", None) or project
            record = service._record_for(owner, inst.part, inst.config)
            if record.kind != "script":
                warnings.append({"kind": "reference_link_skipped",
                                 "instance": inst.id})
                continue
            script = service.store.read_script(owner, inst.part)
            params = record.effective_params
            density = service.material_density(owner, record.material)
            an = service.kernel.request("analyze", {
                "script": script, "params": params, "kind": "inertia",
                "density_g_cm3": density}, timeout_s=120.0)
            metrics = service.get_metrics(owner, inst.part) if inst.config is None \
                else service._ensure_config_built(owner, inst.part,
                                                  inst.config)["metrics"]
            mass_g = metrics["mass_g"]
            mesh_rel = f"meshes/{link_name[inst.id]}.{mesh_format}"
            service.kernel.request("export", {
                "script": script, "params": params, "format": mesh_format,
                "out_path": str(out_dir / mesh_rel)}, timeout_s=300.0)
            links.append({
                "name": link_name[inst.id],
                "mass_kg": mass_g / 1000.0,
                "com_m": [c / 1000.0 for c in an["center_of_mass"]],
                "inertia_com_kg_m2": urdf.inertia_kg_m2_about_com(
                    an["inertia_tensor_g_mm2"], an["center_of_mass"], mass_g),
                "mesh": mesh_rel,
            })

        # ---- joints: one per native mate; frames from the kernel -----------
        world_pose = {inst.id: (list(inst.position), list(inst.rotation_deg))
                      for inst in resolved}
        joint_reqs = []           # kernel urdf_frames payload, in link order
        joint_meta = []           # parallel [(inst, parent_id)]
        for inst in resolved:
            mate = inst.mate
            native = getattr(inst, "origin_project", None) is None
            if not (mate and native and mate.get("to_instance") in names):
                continue
            parent_id = mate["to_instance"]
            anchor = next(i for i in resolved if i.id == parent_id)
            a_owner = getattr(anchor, "origin_project", None) or project
            a_rec = service._record_for(a_owner, anchor.part, anchor.config)
            if a_rec.kind != "script":
                continue
            ppos, prot = world_pose[parent_id]
            cpos, crot = world_pose[inst.id]
            joint_reqs.append({
                "name": link_name[inst.id],
                "connector": mate["to_connector"],
                "anchor_script": service.store.read_script(a_owner, anchor.part),
                "anchor_params": a_rec.effective_params,
                "parent_position": ppos, "parent_rotation_deg": prot,
                "child_position": cpos, "child_rotation_deg": crot,
            })
            joint_meta.append((inst, parent_id))

        frames = {}
        if joint_reqs:
            res = service.kernel.request(
                "urdf_frames", {"joints": joint_reqs}, timeout_s=300.0)
            frames = {f["name"]: f for f in res["joints"]}

        joints: list[dict] = []
        jointed = set()
        for inst, parent_id in joint_meta:
            f = frames.get(link_name[inst.id])
            if f is None or f.get("type") is None:
                continue
            ctype = f["type"]
            mapping = _JOINT_MAP.get(ctype)
            if mapping is None:
                continue
            jtype, degraded, range_key = mapping
            joint = {
                "name": link_name[inst.id], "parent": link_name[parent_id],
                "child": link_name[inst.id],
                "origin_xyz_m": [v / 1000.0 for v in f["origin_xyz_mm"]],
                "origin_rpy": f["origin_rpy"],
            }
            if f.get("axis") is not None and jtype in (
                    "revolute", "continuous", "prismatic", "planar"):
                joint["axis"] = f["axis"]
            if degraded:
                warnings.append({"kind": "joint_degraded", "instance": inst.id,
                                 "from": ctype, "to": "fixed"})
                joint["type"] = "fixed"
            elif jtype == "revolute":
                rng = f.get("range")
                if rng:
                    joint["type"] = "revolute"
                    joint["limit"] = {
                        "lower": math.radians(min(rng)),
                        "upper": math.radians(max(rng)),
                        "effort": 0.0, "velocity": 0.0}
                else:
                    joint["type"] = "continuous"
            elif jtype == "prismatic":
                rng = f.get("linear_range") or (0.0, 0.0)
                joint["type"] = "prismatic"
                joint["limit"] = {"lower": min(rng) / 1000.0,
                                  "upper": max(rng) / 1000.0,
                                  "effort": 0.0, "velocity": 0.0}
            else:
                joint["type"] = jtype
            joints.append(joint)
            jointed.add(inst.id)

        # ---- unmated links: fixed to world + a warning (FR15) --------------
        built = {ln["name"] for ln in links}
        for inst in resolved:
            if inst.id in jointed or link_name[inst.id] not in built:
                continue
            warnings.append({"kind": "unmated", "instance": inst.id})
            pos, rot = world_pose[inst.id]
            joints.append({
                "name": f"{link_name[inst.id]}__world",
                "type": "fixed", "parent": "world",
                "child": link_name[inst.id],
                "origin_xyz_m": [v / 1000.0 for v in pos],
                # world-frame placement in URDF rpy is approximate here (the
                # base is rarely rotated); the joint carries the position.
                "origin_rpy": [0.0, 0.0, 0.0],
            })

        xml = urdf.build_urdf(robot_name, links, joints)
        (out_dir / "robot.urdf").write_text(xml)
        return {"path": str(out_dir), "links": len(links),
                "joints": len(joints), "warnings": warnings}

    registry.register(Tool(
        "export_urdf",
        "Export the assembly as a URDF robot description + one mesh per link "
        "under exports/urdf/<name>/. Mates map to joints (rigid->fixed, "
        "revolute->revolute with limits, slider->prismatic, planar->planar; "
        "cylindrical/ball degrade to fixed with a warning; an unmated instance "
        "becomes a fixed child of world with a warning). Link inertia is "
        "parallel-axis-shifted to each COM. Returns {path, links, joints, "
        "warnings}.",
        schema(
            {"project": {"type": "string"},
             "name": {"type": "string", "description": "robot name (default: project)"},
             "mesh_format": {"type": "string", "description": "stl"}},
            ["project"],
        ),
        export_urdf,
    ))
