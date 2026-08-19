// PRD-026 shell — the command palette's PURE half: the fuzzy score, the
// ranking, the JSON-Schema → form-field derivation, the coercion back into a
// tool body, and the decision about where a tool's result goes.
//
// No DOM, no imports, runs in node — which is what makes the palette's rules
// (a prefix beats a scattered subsequence; a recent entry outranks a section;
// a 4 KB result is a panel and a two-key one is a toast) properties of a value
// a test can read rather than something you have to eyeball in a browser.
//
// Determinism is a requirement, not a nicety: the same query over the same
// entries must produce the same order on every machine, so nothing here reads
// a clock, a locale (`localeCompare` is deliberately NOT used) or a Set's
// iteration order for anything that decides a rank.

// The three sections and their order — the last tie-break before the title.
// Registered dialog views ("Open: …") live in `navigation` rather than in a
// fourth section: they are *places you go*, exactly like a project or a part,
// and a fourth section would have split one idea across two headings.
const SECTIONS = ["actions", "navigation", "tools"];
const SECTION_ORDER = { actions: 0, navigation: 1, tools: 2 };
const HEAD_PER_SECTION = 8;

// Scoring constants. Integers, so a score is exactly comparable and a test can
// assert an ordering without worrying about float drift.
const SCORE_MATCH = 100;       // every matched character
const BONUS_BOUNDARY = 80;     // matched at a word start (or a camelCase hump)
const BONUS_CONSECUTIVE = 60;  // matched right after the previous match
const SCORE_GAP = -3;          // every skipped character
const SHORT_BONUS_MAX = 40;    // "1 char shorter is 1 point better", capped
// Scoring is O(query × text), so the text is bounded — but the bound has to
// clear a real tool description. `set_part_configs` is ~640 characters and it
// is not the longest, so a 200-char cap made the palette unable to find a tool
// by a word in the second half of its own description. 2000 covers every
// description in the registry with room to spare, and at ~20 characters of
// query it is still a 40 000-cell table built only for entries that already
// passed the O(n) subsequence rejection.
const MAX_TEXT = 2000;

const SEPARATORS = " \t\n_-./\\:()[]{}<>,;'\"|+=*#@!?…";

// The three tool arguments the workbench always knows the answer to. A form
// prefills them from the live context (still editable), and `needsForm` counts
// a required one as answered — which is what makes most tools one-Enter.
const PREFILL = {
  project: "projectName",
  part_id: "selectedPart",
  instance_id: "selectedInstance",
};

// ---------------------------------------------------------------- scoring

/** `lower` is the text the DP indexes; `original` is only consulted for the
 *  camelCase hump, and only when `toLowerCase()` did not change the length —
 *  `"\u0130".toLowerCase()` is two characters, and indexing `original` by a
 *  position in `lower` would then read the wrong character (or `undefined`,
 *  which poisons the cell with `NaN`). */
function boundaryBonus(lower, original, j) {
  if (j === 0) return BONUS_BOUNDARY;
  if (SEPARATORS.includes(lower[j - 1])) return BONUS_BOUNDARY;
  if (original) {
    const prev = original[j - 1];
    const cur = original[j];
    // camelCase / snake_Case humps: `partId` scores `i` as a word start.
    if (prev >= "a" && prev <= "z" && cur >= "A" && cur <= "Z") return BONUS_BOUNDARY;
  }
  return 0;
}

/** How well `query` matches `text`. 0 = no match; higher is better.
 *
 *  A subsequence matcher with the two bonuses that make fuzzy search feel
 *  right — a contiguous run and a word start — resolved by the `fzy` dynamic
 *  program rather than a greedy left-to-right scan, because greedy matching
 *  takes the FIRST occurrence of each character and so misses the contiguous
 *  run further along ("ab" in "a-xab" would score as two scattered hits).
 *
 *  A match never scores 0: the result is clamped to at least 1, so "score > 0"
 *  is exactly "the query is a subsequence of the text" and a long title can
 *  never accumulate enough gap penalty to be filtered out as a non-match.
 *
 *  An empty query scores 0 — `rank()` answers the empty query from recents,
 *  and a scorer that said "everything matches equally" would only be a second,
 *  quieter way of expressing that.
 */
export function score(query, text) {
  const q = String(query == null ? "" : query).toLowerCase().trim();
  const original = String(text == null ? "" : text).slice(0, MAX_TEXT);
  const t = original.toLowerCase();
  const m = q.length;
  const n = t.length;
  if (!m || !n || m > n) return 0;

  // Cheap subsequence rejection first: most entries are not a match at all,
  // and this is one pass instead of an m×n table.
  let k = 0;
  for (let j = 0; j < n && k < m; j += 1) if (t[j] === q[k]) k += 1;
  if (k < m) return 0;

  const cased = original.length === n ? original : null;
  const bonus = new Array(n);
  for (let j = 0; j < n; j += 1) bonus[j] = boundaryBonus(t, cased, j);

  // D[j] = best score for a match of q[i] ENDING at t[j]
  // M[j] = best score for matching q[0..i] within t[0..j]
  let prevD = new Array(n).fill(-Infinity);
  let prevM = new Array(n).fill(-Infinity);
  for (let i = 0; i < m; i += 1) {
    const D = new Array(n).fill(-Infinity);
    const M = new Array(n).fill(-Infinity);
    for (let j = 0; j < n; j += 1) {
      if (q[i] === t[j]) {
        let base;
        if (i === 0) {
          base = j * SCORE_GAP + bonus[j];
        } else if (j > 0) {
          base = Math.max(prevM[j - 1] + bonus[j],
                          prevD[j - 1] + BONUS_CONSECUTIVE);
        } else {
          base = -Infinity;
        }
        if (base > -Infinity) D[j] = base + SCORE_MATCH;
      }
      M[j] = Math.max(D[j], j > 0 ? M[j - 1] + SCORE_GAP : -Infinity);
    }
    prevD = D;
    prevM = M;
  }
  const raw = prevM[n - 1];
  if (raw === -Infinity) return 0;
  return Math.max(1, raw + Math.max(0, SHORT_BONUS_MAX - n));
}

/** An entry's score: its title, or a discounted match in what it says about
 *  itself. A title hit must always beat a description hit for the same query,
 *  which is what the 0.4 discount buys — and the `Math.max(1, …)` keeps a
 *  description-only match from rounding away to "no match". */
function scoreEntry(query, entry) {
  const title = score(query, entry.title || "");
  const extra = [entry.description || "",
                 Array.isArray(entry.keywords) ? entry.keywords.join(" ") : ""]
    .filter(Boolean).join(" ");
  const raw = extra ? score(query, extra) : 0;
  const alt = raw > 0 ? Math.max(1, Math.floor(raw * 0.4)) : 0;
  return Math.max(title, alt);
}

/** Filter and order the palette's rows.
 *
 *  Non-empty query: everything the query is a subsequence of, ordered by score
 *  desc, then recency, then section, then title.
 *
 *  Empty query: the recents first (in recency order, whatever section they are
 *  in), then the head of each section — a palette that opens on a blank list
 *  teaches nobody what it can do.
 *
 *  `recents` is most-recent-FIRST (that is what `pushRecent` produces).
 *  Returns the entry objects themselves; nothing here mutates or copies them.
 */
export function rank(query, entries, recents) {
  const list = (Array.isArray(entries) ? entries : []).filter(
    (e) => e && typeof e.id === "string");
  const rec = Array.isArray(recents) ? recents : [];
  const recentIndex = (entry) => {
    const i = rec.indexOf(entry.id);
    return i < 0 ? Number.MAX_SAFE_INTEGER : i;
  };
  const q = String(query == null ? "" : query).trim();

  if (!q) {
    const out = [];
    const seen = new Set();
    for (const id of rec) {
      const hit = list.find((e) => e.id === id);
      if (hit && !seen.has(hit.id)) {
        seen.add(hit.id);
        out.push(hit);
      }
    }
    for (const section of SECTIONS) {
      let n = 0;
      for (const entry of list) {
        if (entry.section !== section || seen.has(entry.id)) continue;
        seen.add(entry.id);
        out.push(entry);
        n += 1;
        if (n >= HEAD_PER_SECTION) break;
      }
    }
    return out;
  }

  const scored = [];
  for (const entry of list) {
    const s = scoreEntry(q, entry);
    if (s > 0) scored.push({ entry, s });
  }
  scored.sort((a, b) => (
    b.s - a.s
    || recentIndex(a.entry) - recentIndex(b.entry)
    || sectionRank(a.entry) - sectionRank(b.entry)
    || compare(a.entry.title, b.entry.title)
    || compare(a.entry.id, b.entry.id)));
  return scored.map((x) => x.entry);
}

function sectionRank(entry) {
  const r = SECTION_ORDER[entry.section];
  return r == null ? SECTIONS.length : r;
}

// Codepoint order, NOT `localeCompare`: the palette must rank the same way on
// every machine, and `localeCompare` is locale- and ICU-version-dependent.
function compare(a, b) {
  const x = String(a == null ? "" : a);
  const y = String(b == null ? "" : b);
  return x < y ? -1 : (x > y ? 1 : 0);
}

// ---------------------------------------------------------------- entries

/** The `GET /api/tools` response, as palette rows. FR6 lives here: the list
 *  *is* the registry's answer — a tool a pack did not register is simply
 *  absent, and nothing in the frontend enumerates tools. */
export function entriesFromTools(payload) {
  const tools = Array.isArray(payload)
    ? payload
    : (payload && Array.isArray(payload.tools) ? payload.tools : []);
  const out = [];
  for (const tool of tools) {
    if (!tool || typeof tool.name !== "string" || !tool.name) continue;
    out.push({
      id: `tool:${tool.name}`,
      section: "tools",
      title: tool.name,
      description: typeof tool.description === "string" ? tool.description : "",
      schema: tool.input_schema && typeof tool.input_schema === "object"
        ? tool.input_schema
        : {},
      tool: tool.name,
    });
  }
  return out;
}

/** `actions.list(ctx)` as palette rows. A `when`-ineligible action never got
 *  here; an `enabled: false` one does, and the palette shows it dimmed —
 *  discoverability is the point of a palette, and a row that vanishes teaches
 *  nothing about why. */
export function entriesFromActions(list) {
  const out = [];
  for (const spec of Array.isArray(list) ? list : []) {
    if (!spec || typeof spec.id !== "string") continue;
    out.push({
      id: spec.id,
      section: "actions",
      title: spec.title || spec.id,
      description: spec.description || spec.group || "",
      keywords: Array.isArray(spec.keywords) ? spec.keywords : [],
      enabled: spec.enabled !== false,
      danger: !!spec.danger,
      action: spec.id,
    });
  }
  return out;
}

/** `dialogs.views(ctx)` as "Open: …" rows (section `navigation`). */
export function entriesFromViews(views) {
  const out = [];
  for (const view of Array.isArray(views) ? views : []) {
    if (!view || typeof view.view !== "string") continue;
    out.push({
      id: `view:${view.view}`,
      section: "navigation",
      title: `Open: ${view.title || view.view}`,
      description: view.description || "",
      view: view.view,
    });
  }
  return out;
}

/** Navigation targets from the store: the other projects, and this project's
 *  parts. The project you are already in is not a place to go. */
export function entriesFromState(store) {
  const s = store || {};
  const out = [];
  for (const project of Array.isArray(s.projects) ? s.projects : []) {
    const name = typeof project === "string" ? project : (project && project.name);
    if (!name || name === s.projectName) continue;
    out.push({
      id: `nav:project:${name}`,
      section: "navigation",
      title: `Open project: ${name}`,
      description: "Project",
      nav: { kind: "project", name },
    });
  }
  for (const part of Array.isArray(s.parts) ? s.parts : []) {
    const id = typeof part === "string" ? part : (part && part.id);
    if (!id) continue;
    out.push({
      id: `nav:part:${id}`,
      section: "navigation",
      title: `Select part: ${id}`,
      description: "Part",
      nav: { kind: "part", name: id },
    });
  }
  return out;
}

// ------------------------------------------------------- schema → the form

function normalizedType(spec) {
  return Array.isArray(spec.type) ? spec.type[0] : spec.type;
}

function fieldType(spec) {
  if (Array.isArray(spec.enum) && spec.enum.length) return "select";
  const type = normalizedType(spec);
  if (type === "number" || type === "integer") return "number";
  if (type === "boolean") return "checkbox";
  if (type === "object" || type === "array") return "json";
  return "text";
}

function prefillValue(name, spec, ctx) {
  const key = PREFILL[name];
  if (key && ctx[key] != null && ctx[key] !== "") return ctx[key];
  const type = fieldType(spec);
  if (spec.default !== undefined) {
    // A `json` field is a textarea: an object default has to arrive as text.
    if (type === "json" && typeof spec.default !== "string") {
      return JSON.stringify(spec.default);
    }
    return spec.default;
  }
  return type === "checkbox" ? false : "";
}

/** The form for one tool: `[{name, label, type, required, value, options?,
 *  step?, help}]`, required args first, then a `{divider: true}`, then the
 *  optional ones (the divider is emitted only when both halves exist).
 *
 *  Within each half the schema's own property order is kept — the tool author
 *  ordered them, and re-sorting would make the form disagree with the docs.
 *  The label is the argument's real name, not a prettified one: this form is
 *  the same call an agent makes, and a user who learns `part_id` here can type
 *  it into the chat.
 */
export function formFields(schema, ctx) {
  const s = schema && typeof schema === "object" ? schema : {};
  const props = s.properties && typeof s.properties === "object"
    ? s.properties : {};
  const required = new Set(Array.isArray(s.required) ? s.required : []);
  const c = ctx || {};
  const req = [];
  const opt = [];
  for (const [name, raw] of Object.entries(props)) {
    const spec = raw && typeof raw === "object" ? raw : {};
    const type = fieldType(spec);
    const field = {
      name,
      label: name,
      type,
      required: required.has(name),
      value: prefillValue(name, spec, c),
      help: typeof spec.description === "string" ? spec.description : "",
    };
    if (type === "select") {
      field.options = spec.enum.map((v) => ({ value: v, label: String(v) }));
    }
    // The normalized type, not the raw one: `{"type": ["integer","null"]}`
    // already renders as a number field, and without this it would lose the
    // multiple-of check `dialogs_model.validate` runs off `step`.
    if (normalizedType(spec) === "integer") field.step = 1;
    (field.required ? req : opt).push(field);
  }
  if (req.length && opt.length) return [...req, { divider: true }, ...opt];
  return [...req, ...opt];
}

/** Does this tool need a form, or can the palette just run it?
 *
 *  True when a required argument has no answer yet. `ctx` is optional: with no
 *  context, any required argument means a form — which is the safe reading and
 *  the one `needsForm(schema)` gives.
 */
export function needsForm(schema, ctx) {
  const s = schema && typeof schema === "object" ? schema : {};
  const required = Array.isArray(s.required) ? s.required : [];
  const props = s.properties && typeof s.properties === "object"
    ? s.properties : {};
  const c = ctx || {};
  for (const name of required) {
    const key = PREFILL[name];
    if (key && c[key] != null && c[key] !== "") continue;
    const spec = props[name];
    if (spec && typeof spec === "object" && spec.default !== undefined) continue;
    return true;
  }
  return false;
}

/** Form values → the JSON body `POST /api/tools/{name}` wants.
 *
 *  Numbers are parsed, `json` fields are `JSON.parse`d (a broken one throws
 *  naming the field, which the dialog shows on its error line), an unchecked
 *  box is an explicit `false`, and an empty optional is OMITTED rather than
 *  sent as `""` — a tool's own default is a better answer than an empty
 *  string, and several of them validate the type.
 */
export function coerce(fields, values) {
  const body = {};
  const all = values || {};
  for (const field of Array.isArray(fields) ? fields : []) {
    if (!field || field.divider || !field.name) continue;
    const raw = all[field.name];
    if (field.type === "checkbox") {
      body[field.name] = !!raw;
      continue;
    }
    const text = raw == null ? "" : String(raw);
    if (text.trim() === "") continue;
    if (field.type === "number") {
      const num = Number(text);
      if (!Number.isFinite(num)) {
        throw new Error(`${field.name}: “${text.trim()}” is not a number`);
      }
      body[field.name] = num;
    } else if (field.type === "json") {
      try {
        body[field.name] = JSON.parse(text);
      } catch (err) {
        throw new Error(`${field.name}: invalid JSON — ${err.message}`);
      }
    } else if (Array.isArray(field.options)) {
      // A <select> hands back a string even when the schema's enum is numeric;
      // send the declared value, not its rendering.
      const hit = field.options.find(
        (o) => String(o && typeof o === "object" ? o.value : o) === text);
      body[field.name] = hit && typeof hit === "object" ? hit.value : text;
    } else {
      body[field.name] = text;
    }
  }
  return body;
}

// --------------------------------------------------------- result routing

function isScalar(value) {
  return value === null || ["string", "number", "boolean"].includes(typeof value);
}

/** Where a tool's answer belongs: `"error"`, `"toast"` or `"panel"`.
 *
 *  A refusal is a 200 with an `{error}` payload, and it goes back to the
 *  dialog that asked — never to a toast that disappears before the user has
 *  read why. Anything small enough to read in passing is a toast; everything
 *  else earns the non-modal result panel.
 */
export function routeResult(result) {
  if (result && typeof result === "object" && !Array.isArray(result)
      && result.error) {
    return "error";
  }
  if (result == null) return "toast";
  let text;
  try {
    text = JSON.stringify(result);
  } catch {
    return "panel";
  }
  if (text == null) return "toast";
  if (text.length <= 120) return "toast";
  if (typeof result === "object" && !Array.isArray(result)) {
    const keys = Object.keys(result);
    if (keys.length <= 3 && keys.every((k) => isScalar(result[k]))) return "toast";
  }
  return "panel";
}

/** The one line a toast shows for a result: its scalar fields, in order. */
export function summarize(result) {
  if (result == null) return "done";
  if (typeof result !== "object") return String(result);
  const parts = [];
  for (const [key, value] of Object.entries(result)) {
    if (isScalar(value)) parts.push(`${key}: ${value}`);
  }
  const text = parts.length ? parts.join(", ") : JSON.stringify(result);
  return text.length > 160 ? `${text.slice(0, 159)}…` : text;
}

/** The message inside an `{error}` payload, or `""` when there is none. */
export function errorMessage(result) {
  const err = result && typeof result === "object" ? result.error : null;
  if (!err) return "";
  if (typeof err === "string") return err;
  return err.message || err.type || "the tool refused";
}

/** `id` to the front of the recents, deduped and capped. Returns a NEW array;
 *  the caller stores it (localStorage) and hands it back next time. */
export function pushRecent(recents, id, max = 20) {
  const list = Array.isArray(recents) ? recents.filter(
    (x) => typeof x === "string" && x) : [];
  if (typeof id !== "string" || !id) return list;
  const limit = Number.isFinite(max) && max > 0 ? Math.floor(max) : 20;
  return [id, ...list.filter((x) => x !== id)].slice(0, limit);
}

// Test seam.
export const __palette__ = {
  score, rank, entriesFromTools, entriesFromActions, entriesFromViews,
  entriesFromState, formFields, needsForm, coerce, routeResult, summarize,
  errorMessage, pushRecent, SECTIONS, SECTION_ORDER, PREFILL,
};
