// Instanced-render grouping + id mapping for the assembly viewport (PRD-013
// FR8). Pure data — NO THREE import — so the picking round-trip is unit-tested
// in node exactly as it runs in the browser: the one property that must not
// regress is that a click on an InstancedMesh maps its `instanceId` back to the
// expanded assembly id (`bolt[3]`, `stand/engine/piston[0]`).
//
// The viewport uploads ONE geometry per (part, rep-tier) group and N per-member
// transforms via THREE.InstancedMesh; this module decides the groups and the
// (groupIndex, localInstanceId) -> expanded-id table.

/** Group flattened assembly items by (partId, geometry key), preserving
 *  first-seen order (deterministic). Each item:
 *  {instanceId, partId, key, position, rotationDeg, color, buffer?}.
 *  Returns [{partId, key, buffer, members:[item...]}]. */
export function groupInstances(items) {
  const groups = [];
  const byKey = new Map();
  for (const it of items) {
    const gk = `${it.partId}:${it.key}`;
    let g = byKey.get(gk);
    if (!g) {
      g = { partId: it.partId, key: it.key, buffer: it.buffer ?? null, members: [] };
      byKey.set(gk, g);
      groups.push(g);
    }
    g.members.push(it);
  }
  return groups;
}

/** Build the picking index: `idForInstance["<groupIndex>:<localInstanceId>"]`
 *  -> the expanded assembly id. THREE gives a raycast hit `object` (the
 *  InstancedMesh, tagged with its group index) and `instanceId` (the local
 *  member index); this table turns that pair back into the id the rest of the
 *  UI selects on. */
export function buildInstanceIndex(items) {
  const groups = groupInstances(items);
  const idForInstance = {};
  groups.forEach((g, gi) => {
    g.members.forEach((m, li) => {
      idForInstance[`${gi}:${li}`] = m.instanceId;
    });
  });
  return { groups, idForInstance };
}

/** Total instance + group counts for the HUD. */
export function instanceCounts(items) {
  const groups = groupInstances(items);
  return { instances: items.length, geometries: groups.length };
}

// Test seam — the node round-trip imports this and nothing else.
export const __instanceIndex__ = {
  groupInstances,
  buildInstanceIndex,
  instanceCounts,
};
