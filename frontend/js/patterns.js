// The identifier rules the SERVER enforces, spelled once for the browser.
//
// Each of these is the client-side twin of a regex in `agentcad/core`, and the
// only reason it exists here is to refuse a bad value before the round trip —
// never to decide anything the server does not. Keeping them in one module is
// the point: PRD-026 turned every `prompt()` into a dialog whose field carries
// a `pattern`, and a hand-copied pattern in a dialog spec is exactly how the
// field and the server drift apart. (`market.js` had one, three characters
// different from being wrong.)
//
// PURE: no DOM, no imports — importable from node and from any module here.

/** Part ids and project names — `core/model.ID_RE`, and (as
 *  `packages/format.PART_ID_RE`) the id a package part takes in a project. */
export const ID_RE = /^[a-z][a-z0-9_]{0,39}$/;

/** Branch names — `core/branches._BRANCH_RE`. */
export const BRANCH_RE = /^[a-z0-9][a-z0-9_/-]{0,63}$/;

/** Version (annotated tag) names — `core/history._REF_RE`. */
export const TAG_RE = /^[a-z0-9][a-z0-9._/-]{0,63}$/;

/** A dialog field's `pattern` is a STRING, and `dialogs_model.validate`
 *  anchors it itself (`^(?:…)$`) because that is what HTML's `pattern`
 *  attribute does. So a spec passes `bare(ID_RE)`, never a second spelling. */
export function bare(re) {
  return re.source.replace(/^\^/, "").replace(/\$$/, "");
}

// Test seam.
export const __patterns__ = { ID_RE, BRANCH_RE, TAG_RE, bare };
