// Grouped assembly-tree rows for the sidebar (PRD-013 FR5/FR1). Pure data — NO
// DOM, NO imports — so the grouping is unit-tested in node exactly as it runs in
// the browser: the property that must hold is that a pattern collapses to ONE
// row with a `×N` badge, a sub-assembly to ONE read-only row naming its source,
// and a plain part to a plain row.
//
// The raw manifest instances (from get_project) carry `pattern` / `assembly`
// verbatim, so the sidebar groups from them directly; expansion reads member
// ids out of the FLATTENED get_assembly view (`bolt[0]`, `engine/piston[0]`).

/** Row descriptors from the RAW (un-expanded) instance list. Each row:
 *  {id, kind, part?, count?, badge?, expandable?, readonly?, source?, config?}.
 *  `kind` is "part" | "linear" | "polar" | "assembly". */
export function instanceRows(instances) {
  return (instances || []).map((inst) => {
    if (inst.pattern) {
      const count = Number(inst.pattern.count) || 0;
      return {
        id: inst.id,
        kind: inst.pattern.kind,
        part: inst.part,
        count,
        badge: `×${count}`,
        expandable: true,
        subassembly: !!inst.assembly,
        source: inst.assembly ? inst.assembly.project : undefined,
      };
    }
    if (inst.assembly) {
      return {
        id: inst.id,
        kind: "assembly",
        source: inst.assembly.project,
        expandable: true,
        readonly: true,
      };
    }
    return {
      id: inst.id,
      kind: "part",
      part: inst.part,
      config: inst.config || null,
    };
  });
}

/** The expanded member ids of a base id, read from the flattened view. A
 *  pattern member is `<base>[i]`; a sub-assembly member is `<base>/...`. */
export function memberIdsOf(baseId, flattened) {
  if (!baseId) return [];
  const bracket = `${baseId}[`;
  const slash = `${baseId}/`;
  return (flattened || [])
    .map((i) => i.id)
    .filter((id) => id.startsWith(bracket) || id.startsWith(slash));
}

/** A minimal HTML string for the row list — used by the node test to assert the
 *  badge renders; the browser builds real DOM in tree.js and does not call
 *  this. Kept deliberately tiny (no escaping needed for ids/kinds, which are
 *  validated tokens). */
export function rowsHtml(rows) {
  return (rows || [])
    .map((r) => {
      const badge = r.badge ? `<span class="row-badge">${r.badge}</span>` : "";
      const src = r.source ? `<span class="row-id">${r.source}</span>` : "";
      return `<li class="row" data-kind="${r.kind}">` +
        `<span class="row-label">${r.id}</span>${badge}${src}</li>`;
    })
    .join("");
}

// Test seam — the node round-trip imports this and nothing else.
export const __treeModel__ = { instanceRows, memberIdsOf, rowsHtml };
