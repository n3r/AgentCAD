// PRD-026 shell — THE action registry. One verb, one id, one place.
//
// Everything that can be done to the workbench registers here: the menu bar
// (slice 4), the command palette (slice 3) and the shortcut table (slice 1)
// all read this and nothing else, so a verb reachable from the toolbar is
// automatically reachable from all three. Toolbar buttons call `run(id)`
// rather than the underlying function, which is what keeps the three in step.
//
// Pure enough to run in node: `state` is a plain store and every DOM read in
// `context()` is guarded, so the registry's rules (duplicate ids throw, `when`
// filters, `enabled` grades) are unit-testable without a browser.

import { state } from "../state.js";
// For ONE question: "is a modal open". Two implementations of that (a DOM query
// here, the overlay stack there) disagreed the moment a non-modal dialog
// existed, and slices 3–4 read `context()` directly. The import is acyclic —
// `dialogs` imports `dialogs_model` and `toast`, neither of which imports this.
import { isModalOpen } from "./dialogs.js";

const registry = new Map(); // id -> spec
const changeFns = new Set();
const runFns = new Set();

/** Register one action.
 *
 *  ```
 *  {id, title, description?, run(ctx), when?(ctx), enabled?(ctx),
 *   shortcut?: "Mod+S" | ["Mod+Y", "Mod+Shift+Z"], menu?: "file/30",
 *   group?, keywords?, danger?}
 *  ```
 *
 *  `when` decides PRESENCE (an action a context cannot host is not listed at
 *  all); `enabled` decides ACTIONABILITY (the menu renders the row disabled so
 *  the map stays stable — spec §4). A duplicate id throws, exactly as
 *  `ToolRegistry` does server-side: two verbs answering to one name is a
 *  programming error, and the second registration would silently win.
 */
export function register(spec) {
  if (!spec || typeof spec.id !== "string" || !spec.id) {
    throw new Error("action needs an id");
  }
  if (typeof spec.run !== "function") {
    throw new Error(`action ${spec.id} needs a run function`);
  }
  if (registry.has(spec.id)) throw new Error(`duplicate action: ${spec.id}`);
  registry.set(spec.id, spec);
  // Fired synchronously and BEFORE anything else can observe the registry, so
  // `shortcuts.js` binds the chord inside `register()` — which is what makes a
  // chord conflict throw from the registration that caused it. A listener that
  // throws (that conflict) UNDOES the registration first: the app is dead
  // either way, but "registered with no chord" is not a state anything should
  // have to reason about.
  try {
    for (const fn of [...changeFns]) fn(spec.id, spec);
  } catch (err) {
    registry.delete(spec.id);
    throw err;
  }
  return spec;
}

export function get(id) {
  return registry.get(id) || null;
}

/** Every registered action, `when` ignored. `list(ctx)` is what a menu or the
 *  palette wants; this is for the shortcut table, which binds a chord whether
 *  or not the action happens to be eligible right now. */
export function all() {
  return [...registry.values()];
}

/** Every action eligible in `ctx`, each with its `enabled` flag. */
export function list(ctx) {
  const context_ = ctx || context();
  const out = [];
  for (const spec of registry.values()) {
    if (spec.when && !spec.when(context_)) continue;
    out.push({ ...spec, enabled: spec.enabled ? !!spec.enabled(context_) : true });
  }
  return out;
}

/** Run an action. Returns whatever `run` returns (usually a promise).
 *
 *  `source` ("palette", "menu", "shortcut", "toolbar", "agent") is passed
 *  through to the run listeners for telemetry — slice 3 turns it into the
 *  `palette_executed` event. An unknown id throws: every call site names a
 *  literal, so a miss is a typo, not a runtime condition.
 */
export function run(id, ctx, opts) {
  const spec = registry.get(id);
  if (!spec) throw new Error(`unknown action: ${id}`);
  const context_ = ctx || context();
  const source = (opts && opts.source) || "unknown";
  for (const fn of [...runFns]) {
    try {
      fn({ id, source, ctx: context_, spec });
    } catch (err) {
      console.error(`action run listener failed for ${id}`, err);
    }
  }
  return spec.run(context_);
}

/** The eligibility context, computed once per query from `state` (+ the few
 *  DOM facts nothing else knows). Guarded so the module still imports in node. */
export function context() {
  const doc = typeof document === "undefined" ? null : document;
  return {
    projectName: state.projectName,
    selectedPart: state.selectedPart,
    selectedInstance: state.selectedInstance,
    mode: state.mode,
    branch: state.branch,
    health: state.health,
    chatAvailable: state.chatAvailable,
    // Whether the assembly has anything in it — read here so an eligibility
    // predicate never has to reach for module state.
    hasInstances: !!(state.project && state.project.assembly
      && state.project.assembly.instances.length),
    // How many parts the sidebar has multi-selected (PRD-027). Here for the
    // same reason `hasInstances` is: the `part.bulk.*` rows are eligible only
    // above one, and a predicate that read `state.selection` itself would
    // grade a synthetic context (the palette's, a test's) differently from
    // the live one.
    selectionSize: state.selection ? state.selection.size : 0,
    // Whether any branch is deletable at all — the one you are on and the
    // default one are refused by the server, so "there are branches" is not
    // the same question as "delete branch… can do anything".
    hasOtherBranches: (state.branches || [])
      .some((b) => !b.is_current && !b.is_default),
    inField: doc ? inField(doc.activeElement) : false,
    modalOpen: isModalOpen(),
    // PRD-005 slice 8. `canEdit` is an AFFORDANCE, not enforcement — the
    // server-side write_guard/registry wrapper (slice 4) is what actually
    // stops a write; this only decides whether the shell offers the control
    // at all. `true` everywhere untenanted (local mode, and a hosted
    // instance with no orgs) — `main.js` computes it from the signed-in
    // principal's role on the CURRENT project (`whoami.roles`, PRD-005 FR6's
    // view<comment<edit<admin ladder) and defaults it open when that is
    // unknown, so a gap here is never a false lockout. `hasOrgs` is the
    // switcher/members/tokens panels' single eligibility gate.
    canEdit: state.canEdit !== false,
    hasOrgs: !!(state.identityOrgs && state.identityOrgs.length),
    sketcherOpen: doc
      ? !!doc.getElementById("sketcher")
        && !doc.getElementById("sketcher").classList.contains("hidden")
      : false,
  };
}

/** True when the element edits text — the test bare-key shortcuts must pass. */
export function inField(el) {
  if (!el || !el.closest) return false;
  if (el.isContentEditable) return true;
  return el.closest("input, textarea, select, .CodeMirror, [contenteditable]") != null;
}

/** Subscribe to registrations. Fires with `(id, spec)` on every `register`. */
export function onChange(fn) {
  changeFns.add(fn);
  return () => changeFns.delete(fn);
}

/** Subscribe to runs — `({id, source, ctx, spec})`. Slice 3's telemetry seam. */
export function onRun(fn) {
  runFns.add(fn);
  return () => runFns.delete(fn);
}

/** Test seam only: forget everything. Never called by the app. */
export function reset() {
  registry.clear();
  changeFns.clear();
  runFns.clear();
}

export const __actions__ = {
  register, get, all, list, run, context, onChange, onRun, reset, inField,
};
