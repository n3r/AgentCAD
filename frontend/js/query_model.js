// PRD-027 FR3 — the search query grammar and matcher, in the browser.
//
// This is a PORT, not a variation: `agentcad/core/search.py` is the original
// and every rule here mirrors one there, line for line. The reason the two
// exist is a split of *data*, not of behaviour — the browser has the manifest
// (ids, labels, tags, materials, folders, states, kinds) and answers a filter
// on it in microseconds without a round trip, while the ~2 MB of part scripts
// live on the server and only it can answer free text. `hasFreeText()` is the
// question the filter box asks to decide which half is in charge.
//
// The two must therefore agree row for row AND rank for rank, and that
// agreement is written down in exactly one place: `tests/fixtures/
// search_queries.json`, driven through the Python matcher by
// `tests/test_search.py` and through this one by
// `tests/test_frontend_navigation.py`. A change to either half that the other
// does not make turns that fixture red.
//
// Pure: no DOM, no imports, no I/O. `matches()` takes the script text as an
// argument, so the same function serves the browser (which passes "") and any
// caller that does have the text.

// -------------------------------------------------------------- the tables
//
// Each of these is the Python constant of the same name. They are exported
// because the filter chips and the result list read them (and because a test
// can then assert the tables are equal rather than trusting the reading).

/** The fields a `field:value` term may name, in the order the grammar
 *  sentence lists them. `parse` refuses anything else BY NAME. */
export const FIELDS = ["tag", "material", "state", "kind", "folder", "id",
                       "label"];

/** The build states a part can be in, as the SERVER knows them. There is no
 *  "building": that is a client notion (a request is in flight) and admitting
 *  it would let a query ask the manifest a question only the browser can
 *  answer. */
export const STATES = ["ok", "error", "unbuilt"];

/** What a part is. `package` is not a manifest kind — it is a script part
 *  whose script carries a package provenance header, which the server decides
 *  and `get_project` reports. */
export const KINDS = ["script", "reference", "package"];

/** Where a hit came from, in ranking order. Also the canonical order of a
 *  row's `matched_on`, so two matchers that found the same evidence report it
 *  identically. */
export const SOURCES = ["id", "label", "tag", "material", "folder", "state",
                        "kind", "script"];

/** Rank of a hit by its best source: a name beats a tag, a tag beats a
 *  material, a structured filter beats a body hit, and the body is last. Ties
 *  fall back to manifest order — the caller sorts on `(rank, index)`. */
export const RANKS = {id: 0, label: 0, tag: 1, material: 2,
                      folder: 3, state: 3, kind: 3, script: 4};

/** The sources a free-text term can hit that are NOT the script body. */
const CONTENT_SOURCES = new Set(["id", "label", "tag", "material"]);

/** The rank of a row that matched with no positive evidence — the empty
 *  query, or a query of nothing but negations. Everything sorts above it. */
export const NO_EVIDENCE_RANK = Math.max(...Object.values(RANKS)) + 1;

/** Maximum `/`-separated segments in a folder path (`navigation.py`). */
const MAX_FOLDER_DEPTH = 8;

// A `field:` prefix is a bare identifier — and once a token HAS one, an
// unknown name is REFUSED, not quietly demoted to free text: `http://x` and
// `C:/tmp` are "unknown search field" errors, deliberately (a typo an agent
// cannot see is worse than an error it can). What stays free text is a head
// this pattern does not match at all, like `1:2` or `:x`. The escape hatch for
// a literal colon is quoting the whole token.
const FIELD_RE = /^[A-Za-z_][A-Za-z0-9_]*$/;

// Python's `str.isspace()`, spelled out. JS's `\s` is ALMOST the same set and
// the difference is exactly the characters a paste from a terminal or a PDF
// can carry: Python counts U+001C–U+001F and U+0085 and JS does not, JS counts
// U+FEFF and Python does not. A port whose tokenizer splits on a different set
// of characters than the original is a parity bug nobody would find by
// reading, so the set is written out here rather than borrowed.
const SPACE = "\\t\\n\\v\\f\\r \\u001c-\\u001f\\u0085\\u00a0\\u1680"
  + "\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000";
const SPACE_RE = new RegExp(`[${SPACE}]`);
const LSTRIP_RE = new RegExp(`^[${SPACE}]+`);
const RSTRIP_RE = new RegExp(`[${SPACE}]+$`);

/** Python's `str.strip()` over the same character set. */
function strip(value) {
  return String(value).replace(LSTRIP_RE, "").replace(RSTRIP_RE, "");
}

// ------------------------------------------------------------- the refusals

/** A grammar refusal. `details` rides on the error so a caller can render the
 *  field list or the legal values rather than only a sentence — the same
 *  payload `search._refuse` puts in the Python `ValidationError`. */
function refuse(message, details) {
  const err = new Error(message);
  err.name = "QueryError";
  err.details = details || {};
  return err;
}

// -------------------------------------------------------------- the grammar

/** The query string as `{terms: [{field, value, negate}]}`, or throw.
 *
 *  Refusals are the point: a user who types `colour:red` or `state:building`
 *  must be TOLD, not handed a silently different search. The five are an
 *  unknown field, an unknown `state`/`kind` value, an empty value, an
 *  unterminated quote, and a non-string query. `field` is `null` for free
 *  text, and `value` is lowercased except for `folder`, which keeps the case a
 *  human typed because `folderMatches` case-folds both sides itself.
 */
export function parse(query) {
  if (query === null || query === undefined) return {terms: []};
  if (typeof query !== "string") {
    throw refuse(`query must be a string, got ${typeof query}`, {query});
  }
  return {terms: tokenize(query).map(term)};
}

/** Split on whitespace, honouring double quotes.
 *
 *  A token is a list of `[text, quoted]` pieces rather than a string, because
 *  `folder:"left side"` and `"folder:left side"` are different queries and
 *  only the piece boundaries tell them apart.
 */
function tokenize(query) {
  const tokens = [];
  let pieces = [];
  let buf = [];
  let quoted = false;
  let inQuote = false;
  const flushPiece = () => {
    if (buf.length || quoted) pieces.push([buf.join(""), quoted]);
    buf = [];
    quoted = false;
  };
  const flushToken = () => {
    flushPiece();
    if (pieces.length) tokens.push(pieces);
    pieces = [];
  };
  for (const char of query) {
    if (char === '"') {
      flushPiece();
      inQuote = !inQuote;
      quoted = inQuote;
      continue;
    }
    if (SPACE_RE.test(char) && !inQuote) {
      flushToken();
      continue;
    }
    buf.push(char);
  }
  if (inQuote) {
    throw refuse('unterminated quote in query: every " needs a closing "',
                 {query});
  }
  flushToken();
  return tokens;
}

/** One token's pieces as a term. */
function term(pieces) {
  const [rawHead, headQuoted] = pieces[0];
  const tail = pieces.slice(1).map(([text]) => text).join("");
  if (headQuoted) {
    // A token that OPENS with a quote is free text whatever it contains — the
    // escape hatch for searching a literal colon.
    return freeTerm(rawHead + tail, false, pieces);
  }
  const negate = rawHead.startsWith("-");
  const head = negate ? rawHead.slice(1) : rawHead;
  const colon = head.indexOf(":");
  if (colon < 0) return freeTerm(head + tail, negate, pieces);
  const field = head.slice(0, colon);
  const rest = head.slice(colon + 1);
  if (!FIELD_RE.test(field)) return freeTerm(head + tail, negate, pieces);
  if (!FIELDS.includes(field.toLowerCase())) {
    throw refuse(`unknown search field '${field}'`,
                 {field, fields: [...FIELDS]});
  }
  return fieldTerm(field.toLowerCase(), rest + tail, negate);
}

/** A free-text term. Refused when it is empty or nothing but whitespace.
 *
 *  `value` is NOT stripped — a quoted `" boss"` searches for the space too,
 *  and whoever typed it meant it. What is refused is a term with no content at
 *  all (`-`, `""`), which would otherwise match everything (or, negated,
 *  nothing) and silently decide the whole query.
 */
function freeTerm(value, negate, pieces) {
  if (!strip(value)) {
    throw refuse('empty search term: a \'-\' or a "" with nothing in it '
                 + "matches nothing and hides the rest of the query",
                 {term: pieces.map(([text]) => text).join("")});
  }
  return {field: null, value: value.toLowerCase(), negate};
}

/** Build (and validate) one `field:value` term. Exported because the filter
 *  chips build terms directly, exactly as `Engine.search`'s structured
 *  `filters` do on the server — one grammar, one set of refusals. */
export function fieldTerm(field, value, negate = false) {
  if (!FIELDS.includes(field)) {
    throw refuse(`unknown search field '${field}'`,
                 {field, fields: [...FIELDS]});
  }
  if (typeof value !== "string") {
    throw refuse(`${field} must be a string, got ${typeof value}`,
                 {field, value});
  }
  let v = strip(value);
  if (!v) {
    throw refuse(`${field}: has no value — remove the term or give it one`,
                 {field});
  }
  if (field === "folder") {
    // `folderMatches` strips slashes before splitting, so "/" and "//" parse
    // to the EMPTY prefix and match every folder — the same silent widening an
    // empty value would cause, one character later.
    if (!v.replace(/^[/ ]+/, "").replace(/[/ ]+$/, "")) {
      throw refuse(`folder: has no value — '${v}' is only separators`,
                   {field, value: v});
    }
  } else {
    v = v.toLowerCase();
  }
  if (field === "state" && !STATES.includes(v)) {
    throw refuse(`unknown state '${v}'`, {field, values: [...STATES]});
  }
  if (field === "kind" && !KINDS.includes(v)) {
    throw refuse(`unknown kind '${v}'`, {field, values: [...KINDS]});
  }
  return {field, value: v, negate};
}

/** A PARSED query, whatever the caller had.
 *
 *  `matches` and `tree_model.filterRows` both run this on their argument, for
 *  one reason: an unparsed STRING has no `.terms`, so reading `query.terms ||
 *  []` off it yields the empty query — and the empty query matches EVERY row.
 *  A filter box that silently shows all 1 000 parts because somebody passed
 *  the raw input string is the worst possible failure mode: it looks like a
 *  working filter with a very popular query. So a string is parsed (and may
 *  therefore refuse, which is the point), `null`/`undefined` is the genuine
 *  empty query, and anything else throws rather than matching everything.
 */
export function asQuery(query) {
  if (query && Array.isArray(query.terms)) return query;
  // `parse` handles the string, `null` and `undefined` cases and refuses the
  // rest by type — one refusal, spelled one way.
  return parse(query);
}

/** Does this query need the SERVER? True when any term is free text.
 *
 *  Free text matches script bodies, which the browser does not have — so the
 *  filter box answers a pure `field:value` query itself and debounces a call
 *  to `GET …/search` for anything else. A string is parsed (and may therefore
 *  throw); a parsed query is read directly.
 */
export function hasFreeText(query) {
  return asQuery(query).terms
    .some((t) => t.field === null || t.field === undefined);
}

/** Does this query need the SERVER to answer it *correctly*?
 *
 *  Two reasons, and only two:
 *
 *  * **free text** matches script bodies, which the browser does not have;
 *  * **any `kind:` term**, which is the subtler one. `get_project` reports the
 *    MANIFEST kind, so an installed package part arrives at the browser as
 *    `"script"` — `kind:package` is *derived* server-side from the script's
 *    provenance header, and the browser cannot see a script at all. The
 *    client's answer to a `kind` term is therefore wrong in BOTH directions:
 *    `kind:package` misses every package part, and `-kind:package` keeps
 *    every one of them. So the query goes to the server, and when the answer
 *    for the current query arrives it REPLACES the client's row set rather
 *    than joining it (`tree_model.filterRows`'s `authoritative` option).
 *
 *  A string is parsed (and may therefore throw); a parsed query is read
 *  directly. `hasFreeText` stays exactly what it says on the tin — the
 *  snippet rule and the fixture parity tests both read it.
 */
export function needsServer(query) {
  return asQuery(query).terms.some(
    (t) => t.field === null || t.field === undefined || t.field === "kind");
}

// -------------------------------------------------------------- the matcher

/** `matched_on` for a row, or `null` when the row is out.
 *
 *  **Pure and total over `part`**: the values come off a manifest a hand edit
 *  or a merge can shape, so a non-string label or a `tags` that is not an
 *  array simply does not match — a corrupt entry must not throw in the middle
 *  of a scan over a thousand rows.
 *
 *  `scriptText` is lowered LAZILY: a metadata-only query never touches it, and
 *  the browser passes "" anyway.
 *
 *  An empty query answers `[]` — matched, with no evidence — never `null`.
 *
 *  `query` is a parsed query, or a string this parses (`asQuery`): a raw
 *  string must never read as "no terms", which would match every row.
 */
export function matches(part, query, opts) {
  const q = asQuery(query);
  const raw = (opts && opts.scriptText) || "";
  let lowered = null;
  const script = () => {
    if (lowered === null) lowered = typeof raw === "string" ? raw.toLowerCase() : "";
    return lowered;
  };
  const found = new Set();
  for (const t of q.terms) {
    const hit = sourcesOf(part, script, t);
    if (t.negate) {
      if (hit.size) return null;
    } else if (!hit.size) {
      return null;
    } else {
      for (const s of hit) found.add(s);
    }
  }
  return SOURCES.filter((s) => found.has(s));
}

/** Which fields of `part` this one term hits (an empty set = no hit). */
function sourcesOf(part, script, t) {
  const value = t.value;
  const hit = new Set();
  if (t.field === null || t.field === undefined) {
    if (text(part, "id").includes(value)) hit.add("id");
    if (text(part, "label").includes(value)) hit.add("label");
    if (tagsOf(part).some((tag) => tag.includes(value))) hit.add("tag");
    if (text(part, "material").includes(value)) hit.add("material");
    if (script().includes(value)) hit.add("script");
    return hit;
  }
  if (t.field === "tag") {
    if (tagsOf(part).includes(value)) hit.add("tag");
    return hit;
  }
  if (t.field === "material" || t.field === "state" || t.field === "kind") {
    if (text(part, t.field) === value) hit.add(t.field);
    return hit;
  }
  if (t.field === "folder") {
    if (folderMatches(part && part.folder, value)) hit.add("folder");
    return hit;
  }
  if (text(part, t.field).includes(value)) hit.add(t.field);
  return hit;
}

function text(part, key) {
  const value = part ? part[key] : null;
  return typeof value === "string" ? value.toLowerCase() : "";
}

function tagsOf(part) {
  const tags = part ? part.tags : null;
  if (!Array.isArray(tags)) return [];
  return tags.filter((t) => typeof t === "string").map((t) => t.toLowerCase());
}

/** Does `folder` sit at, or under, `query`? (case-insensitive)
 *
 *  The port of `navigation.folder_matches`. The comparison is SEGMENT-wise,
 *  never a string prefix: `a/b` is under `a` but not under `a/bc`. An empty
 *  query is the empty prefix and matches every folder including root — which
 *  is exactly why `fieldTerm` refuses an empty `folder:` value rather than
 *  letting it silently widen a search to the whole project.
 *
 *  Total over the stored value, strict about the query: a non-string `folder`
 *  reads as root (a merge can shape it), a non-string query throws (a caller
 *  passing the wrong type must not read as "no matches").
 */
export function folderMatches(folder, query) {
  if (typeof query !== "string") {
    throw refuse(`folder query must be a string, got ${typeof query}`,
                 {query});
  }
  const wanted = segments(query).map((s) => s.toLowerCase());
  if (!wanted.length) return true;
  const stored = typeof folder === "string" ? folder : "";
  const have = segments(stored).map((s) => s.toLowerCase());
  return wanted.every((s, i) => have[i] === s);
}

/** A folder path's non-empty segments. Shared with `tree_model.js`'s flatten
 *  so the tree groups on exactly what the matcher compares. */
export function segments(folder) {
  if (typeof folder !== "string") return [];
  return folder.split("/").filter((s) => s !== "");
}

// -------------------------------------------------------------- the ranking

/** The sort rank of one row's evidence — lower sorts first. Ties fall back to
 *  manifest order, which the caller supplies as the second sort key. */
export function rank(matchedOn) {
  let best = NO_EVIDENCE_RANK;
  for (const source of matchedOn || []) {
    if (source in RANKS && RANKS[source] < best) best = RANKS[source];
  }
  return best;
}

/** Is the script body the only CONTENT source in `matchedOn`?
 *
 *  The snippet rule: the row is in the list because a word was found in its
 *  script, and nothing about its name, tags or material says why. Field terms
 *  (`folder`/`state`/`kind`) do not count — they are the same for every
 *  returned row and explain nothing about THIS one, so `state:ok counterbore`
 *  still gets a snippet.
 */
export function scriptOnly(matchedOn) {
  const found = new Set(matchedOn || []);
  if (!found.has("script")) return false;
  for (const source of found) if (CONTENT_SOURCES.has(source)) return false;
  return true;
}

/** Is this a syntactically valid folder path? The `navigation.py` grammar
 *  (1–8 segments, each `[A-Za-z0-9][A-Za-z0-9 _.-]{0,39}`, no leading or
 *  trailing whitespace), as a predicate rather than a refusal — the client
 *  uses it to grey out a dialog's OK button, and the server still validates. */
export function isFolderPath(value) {
  if (typeof value !== "string" || value === "") return false;
  const parts = value.split("/");
  if (parts.length > MAX_FOLDER_DEPTH) return false;
  return parts.every((s) => s === strip(s)
    && /^[A-Za-z0-9][A-Za-z0-9 _.-]{0,39}$/.test(s));
}

// Test seam — the node round-trip imports this and nothing else.
export const __queryModel__ = {parse, asQuery, matches, hasFreeText,
                               needsServer, rank, scriptOnly, fieldTerm,
                               folderMatches, isFolderPath, FIELDS};
