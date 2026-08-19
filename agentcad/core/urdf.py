"""OCP-free URDF builder + a hand-rolled validator (PRD-013 Decision 6, FR14).

Like ``core/checks.py`` this module imports no OCP/build123d: it turns a resolved
assembly graph — links (mass + inertia + mesh) and joints (type + frames) — into
a robot description string, and validates one with stdlib XML plus structural
asserts. All geometry (transforms, connector frames, the Euler→rpy conversion)
is done in the kernel and handed here as plain numbers; the one bit of math this
module owns is the parallel-axis inertia shift, which is pure linear algebra.

There is no URDF parser in the deps and the PRD forbids adding one, so AC6's
machine-checked half is ``validate_urdf`` (house style — cf.
``checks.validate_report``): well-formedness, then every ``<link>`` has an
``<inertial>`` with positive mass and a symmetric positive-definite inertia,
every ``<joint>`` references existing links and a known type, and the joint
graph is a single tree rooted at ``world`` with no cycles.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import numpy as np

#: URDF joint types this MVP emits or accepts.
VALID_JOINT_TYPES = frozenset(
    {"fixed", "revolute", "continuous", "prismatic", "planar", "floating"})

# g·mm² → kg·m²: g→kg ×1e-3, mm²→m² ×1e-6.
_G_MM2_TO_KG_M2 = 1e-9


def inertia_kg_m2_about_com(tensor_g_mm2, com_mm, mass_g):
    """Shift a mass-inertia tensor from the global origin to the body's COM and
    convert g·mm² → kg·m² (PRD-013 §6.2 correctness step).

    ``analyze_part`` returns the tensor **about the global origin** (the part is
    built at the origin); URDF ``<inertial>`` wants it about the link's COM, with
    ``<inertia>`` expressed there. The parallel-axis theorem in reverse:

        I_com = I_origin − m·(‖c‖²·E₃ − c·cᵀ)

    with ``c`` the COM (mm) and ``m`` the mass (g). Skipping this ships a
    positive-but-WRONG tensor for any off-origin part.
    """
    i_origin = np.asarray(tensor_g_mm2, dtype=float).reshape(3, 3)
    c = np.asarray(com_mm, dtype=float).reshape(3)
    m = float(mass_g)
    shift = m * (float(c @ c) * np.eye(3) - np.outer(c, c))
    i_com = i_origin - shift
    return (i_com * _G_MM2_TO_KG_M2).tolist()


def _fmt(x: float) -> str:
    """Deterministic float formatting for byte-stable golden files. Round
    first, THEN normalize -0.0 -> 0.0 so a near-zero whose sign is FP noise
    (e.g. -3e-19) never flips between '0.0' and '-0.0' across runs."""
    v = round(float(x), 12)
    if v == 0.0:
        v = 0.0
    return repr(v)


def _vec(v) -> str:
    return " ".join(_fmt(x) for x in v)


def build_urdf(robot_name: str, links: list[dict], joints: list[dict]) -> str:
    """Assemble a URDF string from a resolved graph.

    ``links``: ``[{name, mass_kg, com_m:[x,y,z], inertia_com_kg_m2:[[..]*3],
    mesh:"meshes/<name>.stl"}]``.
    ``joints``: ``[{name, type, parent, child, origin_xyz_m, origin_rpy,
    axis?, limit?:{lower,upper,effort,velocity}}]``. A ``world`` link is emitted
    automatically when any joint references it.
    """
    robot = ET.Element("robot", {"name": robot_name})

    needs_world = any(j.get("parent") == "world" or j.get("child") == "world"
                      for j in joints)
    if needs_world:
        ET.SubElement(robot, "link", {"name": "world"})

    for link in links:
        el = ET.SubElement(robot, "link", {"name": link["name"]})
        inertial = ET.SubElement(el, "inertial")
        ET.SubElement(inertial, "origin",
                      {"xyz": _vec(link["com_m"]), "rpy": "0 0 0"})
        ET.SubElement(inertial, "mass", {"value": _fmt(link["mass_kg"])})
        I = link["inertia_com_kg_m2"]
        ET.SubElement(inertial, "inertia", {
            "ixx": _fmt(I[0][0]), "ixy": _fmt(I[0][1]), "ixz": _fmt(I[0][2]),
            "iyy": _fmt(I[1][1]), "iyz": _fmt(I[1][2]), "izz": _fmt(I[2][2])})
        if link.get("mesh"):
            for tag in ("visual", "collision"):
                node = ET.SubElement(el, tag)
                ET.SubElement(node, "origin", {"xyz": "0 0 0", "rpy": "0 0 0"})
                geom = ET.SubElement(node, "geometry")
                ET.SubElement(geom, "mesh", {"filename": link["mesh"]})

    for j in joints:
        el = ET.SubElement(robot, "joint",
                           {"name": j["name"], "type": j["type"]})
        ET.SubElement(el, "origin", {
            "xyz": _vec(j.get("origin_xyz_m", [0, 0, 0])),
            "rpy": _vec(j.get("origin_rpy", [0, 0, 0]))})
        ET.SubElement(el, "parent", {"link": j["parent"]})
        ET.SubElement(el, "child", {"link": j["child"]})
        if j.get("axis") is not None:
            ET.SubElement(el, "axis", {"xyz": _vec(j["axis"])})
        limit = j.get("limit")
        if limit is not None:
            ET.SubElement(el, "limit", {
                "lower": _fmt(limit.get("lower", 0.0)),
                "upper": _fmt(limit.get("upper", 0.0)),
                "effort": _fmt(limit.get("effort", 0.0)),
                "velocity": _fmt(limit.get("velocity", 0.0))})

    ET.indent(robot, space="  ")
    return ET.tostring(robot, encoding="unicode") + "\n"


# ----------------------------------------------------------------- validation

class URDFError(ValueError):
    """A structural URDF defect found by ``validate_urdf``."""


def _spd(matrix) -> bool:
    m = np.asarray(matrix, dtype=float)
    if m.shape != (3, 3):
        return False
    if not np.allclose(m, m.T, atol=1e-12):
        return False
    return bool(np.all(np.linalg.eigvalsh(m) > 0))


def validate_urdf(xml: str) -> None:
    """Raise ``URDFError`` on a malformed or structurally invalid URDF.

    Checks: well-formed XML; root ``<robot>``; every ``<link>`` (bar ``world``)
    carries an ``<inertial>`` with positive mass and an SPD inertia; every
    ``<joint>`` has a known type and references existing parent/child links; the
    joint graph is a single connected tree rooted at ``world`` with no cycles.
    """
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise URDFError(f"not well-formed XML: {exc}") from exc
    if root.tag != "robot":
        raise URDFError(f"root element must be <robot> (got <{root.tag}>)")

    links = {}
    for link in root.findall("link"):
        name = link.get("name")
        if not name:
            raise URDFError("a <link> has no name")
        if name in links:
            raise URDFError(f"duplicate link {name!r}")
        links[name] = link
        if name == "world":
            continue
        inertial = link.find("inertial")
        if inertial is None:
            raise URDFError(f"link {name!r} has no <inertial>")
        mass_el = inertial.find("mass")
        if mass_el is None or float(mass_el.get("value")) <= 0.0:
            raise URDFError(f"link {name!r} has non-positive mass")
        it = inertial.find("inertia")
        if it is None:
            raise URDFError(f"link {name!r} has no <inertia>")
        ixx, ixy, ixz = (float(it.get(k)) for k in ("ixx", "ixy", "ixz"))
        iyy, iyz, izz = (float(it.get(k)) for k in ("iyy", "iyz", "izz"))
        tensor = [[ixx, ixy, ixz], [ixy, iyy, iyz], [ixz, iyz, izz]]
        if not _spd(tensor):
            raise URDFError(
                f"link {name!r} inertia is not symmetric positive-definite "
                "(a tensor left about the origin, not shifted to the COM?)")

    parent_of: dict[str, str] = {}
    joint_names = set()
    for joint in root.findall("joint"):
        jname = joint.get("name")
        if not jname or jname in joint_names:
            raise URDFError(f"missing/duplicate joint name {jname!r}")
        joint_names.add(jname)
        jtype = joint.get("type")
        if jtype not in VALID_JOINT_TYPES:
            raise URDFError(f"joint {jname!r} has unknown type {jtype!r}")
        pel, cel = joint.find("parent"), joint.find("child")
        if pel is None or cel is None:
            raise URDFError(f"joint {jname!r} needs <parent> and <child>")
        parent, child = pel.get("link"), cel.get("link")
        for ref in (parent, child):
            if ref not in links:
                raise URDFError(
                    f"joint {jname!r} references unknown link {ref!r}")
        if child in parent_of:
            raise URDFError(f"link {child!r} has more than one parent joint")
        parent_of[child] = parent

    # Every real link is a child of exactly one joint; the tree is rooted at
    # `world` (or a single unparented link) with no cycles.
    real = [n for n in links if n != "world"]
    for name in real:
        seen = set()
        cur = name
        while cur in parent_of:
            if cur in seen:
                raise URDFError(f"cycle in the joint tree through {cur!r}")
            seen.add(cur)
            cur = parent_of[cur]
        # cur is now a root: it must be `world` or have no inbound joint at all
        if cur != "world" and cur in parent_of:
            raise URDFError(f"link {name!r} does not reach a root")
    if "world" in links:
        roots = {parent_of[c] for c in parent_of} - set(parent_of)
        if roots and roots != {"world"} and "world" not in roots:
            raise URDFError(f"multiple disconnected roots: {sorted(roots)}")
