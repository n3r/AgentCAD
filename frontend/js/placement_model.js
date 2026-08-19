// Pure model helpers for the placement card's PRD-013 additions: the per-joint
// DOF editor and the pattern-spec builder. NO DOM, NO imports — so the two
// decisions that are easy to get wrong (which DOF fields a mate shows, and the
// exact pattern payload a set_pattern call carries) are unit-tested in node.
//
// A mate stores only {connector, to_instance, to_connector, params}; the
// connector TYPE is not on the instance. But the resolved `params` vocabulary
// is type-specific — {position} for a slider, {u,v,spin} for a planar, {angle}
// for a revolute — so the editor is chosen from the params keys.

/** The DOF editor for a mate, or null (rigid / unknown → no editor). Returns
 *  {kind, fields:[{key, label, unit, value}]}; `key` is the set_mate `dof` key. */
export function dofEditor(mate) {
  const params = (mate && mate.params) || {};
  if ("u" in params || "v" in params || "spin" in params) {
    return {
      kind: "planar",
      fields: [
        { key: "u_mm", label: "U", unit: "mm", value: num(params.u) },
        { key: "v_mm", label: "V", unit: "mm", value: num(params.v) },
        { key: "spin_deg", label: "Spin", unit: "deg", value: num(params.spin) },
      ],
    };
  }
  if ("position" in params) {
    return {
      kind: "slider",
      fields: [
        { key: "offset_mm", label: "Offset", unit: "mm",
          value: num(params.position) },
      ],
    };
  }
  if ("angle" in params) {
    return {
      kind: "revolute",
      fields: [
        { key: "angle_deg", label: "Angle", unit: "deg",
          value: num(params.angle) },
      ],
    };
  }
  return null;
}

/** The pattern spec for a set_pattern call. Linear carries `step_mm`, polar
 *  `angle_step_deg`; count is coerced to an integer >= 1. */
export function patternSpec(kind, count, spacing) {
  const spec = { kind, count: Math.max(1, Math.round(Number(count) || 1)) };
  const s = Number(spacing) || 0;
  if (kind === "polar") spec.angle_step_deg = s;
  else spec.step_mm = s;
  return spec;
}

/** The current pattern's editor values from a raw instance, or null. */
export function patternDraft(inst) {
  const p = inst && inst.pattern;
  if (!p) return { kind: "linear", count: 2, spacing: 10, active: false };
  return {
    kind: p.kind,
    count: Number(p.count) || 2,
    spacing: p.kind === "polar" ? num(p.angle_step_deg) : num(p.step_mm),
    active: true,
  };
}

function num(v) {
  return Number.isFinite(Number(v)) ? Number(v) : 0;
}

// Test seam.
export const __placementModel__ = { dofEditor, patternSpec, patternDraft };
