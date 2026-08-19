// Materials browser — pure data model. NO DOM, NO imports (same discipline as
// tree_model.js): every function here is a plain transform over the JSON the
// server already returns, so the one property worth a unit test (a filter
// panel becomes the right query object, a row list becomes the right tree
// counts, a pinned set becomes the right compare columns, a basis maps to the
// right badge, a sort is stable with missing values last) runs in node
// exactly as it runs in the browser.
//
// The property vocabulary mirrors `agentcad/core/materials.py::PROPERTY_UNITS`
// byte-for-byte (15 keys, one canonical unit each) — this file does not
// invent a second source of truth, it copies the one the server already
// validates against.

/** `agentcad/core/materials.py::PROPERTY_UNITS`, copied. */
export const PROPERTY_UNITS = {
  density_g_cm3: "g/cm3",
  E_gpa: "GPa",
  yield_mpa: "MPa",
  ultimate_mpa: "MPa",
  elongation_pct: "%",
  cte_um_m_k: "um/(m*K)",
  k_w_m_k: "W/(m*K)",
  max_service_temp_c: "C",
  cost_usd_kg: "USD/kg",
  poisson_ratio: "-",
  cp_j_kg_k: "J/(kg*K)",
  shear_modulus_gpa: "GPa",
  compressive_mpa: "MPa",
  bending_mpa: "MPa",
  E_perp_gpa: "GPa",
};

/** Display labels for the property keys above — the compare table and the
 *  detail pane's property rows both read this rather than the raw key. */
export const PROPERTY_LABELS = {
  density_g_cm3: "Density",
  E_gpa: "E",
  yield_mpa: "Yield",
  ultimate_mpa: "Ultimate",
  elongation_pct: "Elongation",
  cte_um_m_k: "CTE",
  k_w_m_k: "Thermal conductivity",
  max_service_temp_c: "Max service temp",
  cost_usd_kg: "Cost",
  poisson_ratio: "Poisson's ratio",
  cp_j_kg_k: "Specific heat",
  shear_modulus_gpa: "Shear modulus",
  compressive_mpa: "Compressive",
  bending_mpa: "Bending",
  E_perp_gpa: "E (perpendicular)",
};

/** The six filter-bar numeric fields (Decision 10): min/max density, min E,
 *  min yield, min max-service-temp, max cost — the `_min`/`_max` grammar keys
 *  they translate to. */
export const NUMERIC_FILTER_KEYS = [
  "density_g_cm3_min",
  "density_g_cm3_max",
  "E_gpa_min",
  "yield_mpa_min",
  "max_service_temp_c_min",
  "cost_usd_kg_max",
];

const BASES = ["typical", "minimum", "characteristic"];

/** `agentcad/core/materials_query.py::CONSTRAINT_PROCESSES` keys, copied for
 *  the process chip strip (order is display order, not meaningful). */
export const PROCESS_KEYS = [
  "cnc", "weld", "fdm", "sla", "sls", "mjf", "dmls", "im", "sheet", "casting",
];

/** A UI filter-bar snapshot -> the `GET /api/materials` query shape:
 *  `{category?, subcategory?, filter}`. Numeric fields become `filter`'s
 *  `_min`/`_max` keys (blank/non-finite omitted); `process`/`basis` ride
 *  `filter` too; `category`/`subcategory` are top-level query params, not
 *  `filter` keys (routes_materials.py reads them separately). */
export function filterToQuery(uiFilters) {
  const f = uiFilters || {};
  const filter = {};
  for (const key of NUMERIC_FILTER_KEYS) {
    const raw = f[key];
    if (raw === "" || raw === null || raw === undefined) continue;
    const num = typeof raw === "number" ? raw : Number(raw);
    if (!Number.isFinite(num)) continue;
    filter[key] = num;
  }
  if (f.process) filter.process = f.process;
  if (f.basis) filter.basis = f.basis;
  const out = { filter };
  if (f.category) out.category = f.category;
  if (f.subcategory) out.subcategory = f.subcategory;
  return out;
}

/** Summary rows (`GET /api/materials`'s `materials[]`) -> tree counts:
 *  `{category: {count, subcategories: {name: count}}}`. A row with no
 *  category never happens (the schema requires it); a row with no
 *  subcategory counts toward the category only. */
export function treeCounts(rows) {
  const out = {};
  for (const row of rows || []) {
    const cat = row && row.category;
    if (!cat) continue;
    if (!out[cat]) out[cat] = { count: 0, subcategories: {} };
    out[cat].count += 1;
    const sub = row.subcategory;
    if (sub) {
      out[cat].subcategories[sub] = (out[cat].subcategories[sub] || 0) + 1;
    }
  }
  return out;
}

/** Trim trailing float noise without inventing precision — the shipped
 *  values are already 2-3 sig figs, this just undoes IEEE-754 artifacts
 *  (2.0999999999999996 -> 2.1). */
function fmtNum(v) {
  if (!Number.isFinite(v)) return String(v);
  const r = Math.round(v * 1e6) / 1e6;
  return String(r);
}

/** One property object (`get_material`'s `properties[key]` shape:
 *  `{value|range, unit, basis, source, T_c?, table?, as_of?}`) -> a display
 *  string. `T_c` is shown only when it departs the 20 C default — a card
 *  with no `T_c` (or exactly 20) means "room temperature", which needs no
 *  label. */
export function formatProperty(prop) {
  if (!prop) return "—";
  let base;
  if (prop.range) {
    base = `${fmtNum(prop.range[0])}–${fmtNum(prop.range[1])}`;
  } else if (prop.value !== undefined && prop.value !== null) {
    base = fmtNum(prop.value);
  } else {
    return "—";
  }
  const unit = prop.unit ? ` ${prop.unit}` : "";
  const hasT = prop.T_c !== undefined && prop.T_c !== null;
  const t = hasT && Number(prop.T_c) !== 20 ? ` @ ${fmtNum(prop.T_c)}°C` : "";
  return `${base}${unit}${t}`;
}

/** Full material records (`get_material` payloads, each carrying
 *  `properties`) + the property keys to show -> side-by-side compare rows:
 *  `[{key, label, unit, values: [...]}]`, one row per key, one value per
 *  record in the SAME order as `records`. A record missing the property is
 *  `"—"`, never a blank cell (a blank reads as "loading", not "absent"). */
export function compareRows(records, keys) {
  return (keys || []).map((key) => ({
    key,
    label: PROPERTY_LABELS[key] || key,
    unit: PROPERTY_UNITS[key] || "",
    values: (records || []).map((rec) => {
      const prop = rec && rec.properties && rec.properties[key];
      return prop ? formatProperty(prop) : "—";
    }),
  }));
}

/** A property's `basis` -> `{text, cls}` for the badge. Anything outside the
 *  three known bases (most notably `null`/`undefined`, which is what an
 *  uncited property's caller passes) reads as "uncited" — never a guessed
 *  basis, per the schema's own honesty rule (a card names its evidence or
 *  says nothing). */
export function basisBadge(basis) {
  if (BASES.includes(basis)) {
    return { text: basis, cls: `mat-badge-${basis}` };
  }
  return { text: "uncited", cls: "mat-badge-uncited" };
}

/** Stable sort of table rows by `key`. Numbers compare numerically, strings
 *  with `localeCompare`; a row missing the sort key (null/undefined/"")
 *  always sorts LAST regardless of `dir` — "missing" is not a value, so
 *  reversing direction must not walk it to the top. Ties (including every
 *  pair of missing rows) keep their original relative order. */
export function sortRows(rows, key, dir) {
  const factor = dir === "desc" ? -1 : 1;
  return (rows || [])
    .map((row, i) => ({ row, i }))
    .sort((a, b) => {
      const av = a.row ? a.row[key] : undefined;
      const bv = b.row ? b.row[key] : undefined;
      const aMissing = av === null || av === undefined || av === "";
      const bMissing = bv === null || bv === undefined || bv === "";
      if (aMissing && bMissing) return a.i - b.i;
      if (aMissing) return 1;
      if (bMissing) return -1;
      let cmp;
      if (typeof av === "number" && typeof bv === "number") cmp = av - bv;
      else cmp = String(av).localeCompare(String(bv));
      if (cmp === 0) return a.i - b.i;
      return cmp * factor;
    })
    .map((x) => x.row);
}

// Test seam — the node round-trip imports this and nothing else.
export const __materialsModel__ = {
  PROPERTY_UNITS,
  PROPERTY_LABELS,
  NUMERIC_FILTER_KEYS,
  PROCESS_KEYS,
  filterToQuery,
  treeCounts,
  formatProperty,
  compareRows,
  basisBadge,
  sortRows,
};
