// Agent skills panel — pure data model. NO DOM, NO imports (the same
// discipline as `tree_model.js` and `materials_model.js`): every function here
// is a plain transform over the JSON `GET /api/projects/{p}/skills` and the
// `skill_loaded`/`skill_unloaded` bus events already carry, so the properties
// worth a unit test (an index entry becomes the right provenance badges, a
// digest-keyed trust map distinguishes "never reviewed" from "changed since
// you reviewed it", a bus `client` is or is not the chat engine) run in node
// exactly as they run in the browser.
//
// The vocabulary mirrors `agentcad/core/skills.py` (`_entry`'s keys, `load`'s
// payload, the two event shapes of spec §11) — this file invents no second
// source of truth.

/** `LAYER_ORDER` in `core/skills.py`, as badge classes. The `org` layer is
 *  deferred (it needs PRD-005's org store) but the loader's layer list already
 *  names it, so the badge does too rather than falling through to "unknown". */
export const LAYER_BADGE = {
  core: "badge-core",
  org: "badge-org",
  project: "badge-project",
};

/** A bus `client` that is the CHAT ENGINE and nothing else.
 *
 *  `agent/chat.py` runs its tools under `locks.set_client_id("chat")` for the
 *  default session and `"chat:<session>"` for any other, and a session id is
 *  the house `[a-z0-9_-]{1,32}` slug. Anchored on both ends on purpose: the
 *  browser's own identity is `browser:<8 hex>` and MCP's is `mcp`, so an
 *  agent's read on another surface must render NO chip here. (A human's own
 *  preview in the Skills panel never reaches the bus at all — that read
 *  bypasses `load_skill`, see `server/routes_skills.py`.) */
const CHAT_CLIENT_RE = /^chat(:[a-z0-9_-]{1,32})?$/;

/** The chat LANE behind a client id, or null when it is not chat's.
 *
 *  `"chat"` -> `"main"` (the engine's `DEFAULT_SESSION`), `"chat:<s>"` ->
 *  `"<s>"`. This is the browser's copy of `core/tools_skills.chat_session`,
 *  which stamps the same value on the `skill_loaded` event; the dock draws a
 *  chip only for its own lane, because `skill_unloaded` has always carried a
 *  session and another lane's is filtered out before the chip could ever be
 *  un-struck. */
export function sessionOf(client) {
  if (!isChatClient(client)) return null;
  const colon = client.indexOf(":");
  return colon === -1 ? "main" : client.slice(colon + 1);
}

/** One index entry (+ the trust document the same payload carries) -> the
 *  badges to draw, in reading order: the layer, then what it shadows, then
 *  whether a human still has to look at it, then whether it parses at all.
 *
 *  The two untrusted states are DIFFERENT facts and the spec insists on
 *  saying which: a name absent from `trust.trusted` was never reviewed, while
 *  a name present there with a stale digest is a skill that CHANGED after a
 *  human approved it — which is the `git pull` attack the digest keying
 *  exists for. Without the trust map the honest answer is the weaker one
 *  ("needs review"), never the stronger claim. */
export function badgeFor(entry, trust) {
  const e = entry || {};
  const out = [];
  const layer = e.layer ? String(e.layer) : "unknown";
  out.push({ text: layer, cls: LAYER_BADGE[layer] || "badge-layer" });
  if (e.overrides) {
    out.push({ text: `overrides ${e.overrides}`, cls: "badge-overrides" });
  }
  if (layer === "project" && !e.trusted) {
    const known =
      !!trust && !!trust.trusted &&
      Object.prototype.hasOwnProperty.call(trust.trusted, e.name);
    out.push({
      text: known ? "changed since trusted" : "needs review",
      cls: "badge-review",
    });
  }
  if (e.invalid) out.push({ text: "invalid", cls: "badge-invalid" });
  return out;
}

/** Index entries in name order, stably.
 *
 *  The server already sorts by name (`_effective` sorts the record dict), so
 *  this is a re-assertion, not a re-ordering — and deliberately NOT
 *  "project layer first": the list is the EFFECTIVE index, where a project
 *  skill that shadows a core one occupies the core one's row. Sorting by
 *  layer would move that row and make the override look like a second entry.
 *  Ties (two entries with one name — impossible after layering, possible in a
 *  hand-built test payload) keep their input order. */
export function sortRows(entries) {
  return (entries || [])
    .map((row, i) => ({ row, i }))
    .sort((a, b) => {
      const an = a.row && a.row.name ? String(a.row.name) : "";
      const bn = b.row && b.row.name ? String(b.row.name) : "";
      const cmp = an.localeCompare(bn);
      return cmp === 0 ? a.i - b.i : cmp;
    })
    .map((x) => x.row);
}

/** Does this project ship agent instructions nobody has looked at yet?
 *  Drives the modal's one-line consent banner (spec §6). Core skills are
 *  trusted by construction, so only the project layer can ever say yes. */
export function needsConsent(entries) {
  return (entries || []).some(
    (e) => e && e.layer === "project" && !e.trusted);
}

/** A `skill_loaded` (or `skill_unloaded`) event -> the chat dock's chip text.
 *  The layer is part of the label, not a tooltip: "which instructions just
 *  entered the agent's context, and who wrote them" is the whole point of
 *  the chip.
 *
 *  An `asset` read is a different fact and gets a different chip: the agent
 *  read one file OUT of a skill (a snippet, a table) rather than loading the
 *  guide, so the label names the file instead of the layer — and the two are
 *  separate budget entries in `agent/chat.py`, evicted separately. */
export function chipLabel(ev) {
  const e = ev || {};
  const name = e.name ? String(e.name) : "skill";
  if (e.asset) return `📎 ${name} · ${String(e.asset)}`;
  const layer = e.layer ? String(e.layer) : "";
  return layer ? `📘 ${name} · ${layer}` : `📘 ${name}`;
}

/** True only for the chat engine's own client ids. See `CHAT_CLIENT_RE`. */
export function isChatClient(client) {
  return typeof client === "string" && CHAT_CLIENT_RE.test(client);
}

function formatBytes(n) {
  if (!Number.isFinite(n) || n < 0) return "";
  if (n < 1024) return `${Math.round(n)} B`;
  if (n < 1024 * 1024) return `${Math.round((n / 1024) * 10) / 10} kB`;
  return `${Math.round((n / (1024 * 1024)) * 10) / 10} MB`;
}

/** `load`'s `assets: [{path, bytes}]` -> display lines. A missing/NaN size is
 *  dropped rather than rendered as `0 B` — "we could not stat it" is not
 *  "it is empty". */
export function formatAssets(assets) {
  return (assets || []).map((a) => {
    const path = a && a.path ? String(a.path) : "(unnamed)";
    const size = a ? formatBytes(a.bytes) : "";
    return size ? `${path} · ${size}` : path;
  });
}

/** `load`'s `provenance` -> the one line under the preview heading. Every
 *  field is optional (a core skill has no project-relative `path`; an invalid
 *  one has no meta at all), so this joins what exists and never prints a
 *  label with an empty value. The digest is cut to 12 hex chars — enough to
 *  compare against `trust.trusted[name]` by eye, and it is INTEGRITY, not
 *  authentication. */
export function provenanceLine(prov) {
  const p = prov || {};
  const parts = [];
  if (p.layer) parts.push(String(p.layer));
  if (p.path) parts.push(String(p.path));
  if (p.author) parts.push(`by ${p.author}`);
  if (p.license) parts.push(String(p.license));
  if (p.digest) parts.push(`sha256:${String(p.digest).slice(0, 12)}`);
  return parts.join(" · ");
}

/** `load`'s `{truncated, omitted_sections}` -> the note above the preview, or
 *  "" when the whole body came through. Naming the omitted headings is the
 *  point: a reader has to be able to tell that the agent saw less than the
 *  file holds, and WHICH part it did not see. */
export function truncationNote(payload) {
  const p = payload || {};
  if (!p.truncated) return "";
  const omitted = Array.isArray(p.omitted_sections) ? p.omitted_sections : [];
  if (!omitted.length) {
    return "truncated — the tail of this skill was cut to fit the budget";
  }
  const n = omitted.length;
  return `truncated — ${n} section${n === 1 ? "" : "s"} omitted: ` +
    omitted.map(String).join(", ");
}

// Test seam — the node round-trip imports this and nothing else.
export const __skillsModel__ = {
  LAYER_BADGE,
  badgeFor,
  sortRows,
  needsConsent,
  chipLabel,
  isChatClient,
  sessionOf,
  formatAssets,
  provenanceLine,
  truncationNote,
};
