// PRD-027 FR6 — the all-projects dashboard: the first screen, and the way back
// to it.
//
// A full-pane view, NOT a modal (design §6). Two reasons, both load-bearing:
// it is a *place* rather than a decision, so `dialogs.isModalOpen()` must stay
// false while it is up or every global shortcut dies behind it; and it covers
// the workspace without unmounting it, so closing it costs no reload and the
// viewport's WebGL context survives — hiding `#workspace` instead would tear
// down and rebuild the scene every time somebody glanced at the project list.
//
// It owns Esc the same way `shell/contextmenu.js` does: an ELEMENT-scoped
// capture listener on `#dashboard`, never a document-level one. The pane fills
// the screen and takes focus when it opens, so every keystroke while it is up
// is inside it; when it is closed the listener is attached to a hidden element
// and cannot fire at all. A document listener would be a second Esc owner
// racing the dialog stack for the rest of the session.
//
// Everything it shows comes from `GET /api/dashboard`, which is kernel-free
// and build-free by contract: `mass_g` is `null` (rendered "—") the moment one
// part of a project is unbuilt, rather than a partial sum somebody would read
// as the project's mass, and `thumb` is a URL only when a file already exists
// to answer from. The card is therefore an honest summary of what the server
// already knows, and opening the app can never start a build.

import { state, setState } from "./state.js";
import { api } from "./api.js";
import * as dialogs from "./shell/dialogs.js";

let actions = null;
let host = null;
let gridEl = null;
let restoreFocus = null;

export function init(a) {
  actions = a;
  host = document.getElementById("dashboard");
  if (!host) return;
  host.addEventListener("keydown", onKey, true);
  dialogs.register("dashboard", () => open(), {
    title: "All projects…",
    description: "Every project on this server, with parts, mass and what is "
                 + "failing to build",
    actionId: "project.dashboard",
  });
}

/** Is the dashboard covering the workspace right now? */
export function isOpen() {
  return !!state.dashboardOpen;
}

/** Show the dashboard and (re)load the listing. Idempotent. */
export async function open() {
  if (!host) return;
  if (!isOpen()) restoreFocus = document.activeElement;
  host.classList.remove("hidden");
  setState({dashboardOpen: true});
  renderShell();
  host.focus();
  await reload();
}

/** Hide it. `Esc` and every card that opens a project come through here.
 *
 *  Refuses to close with NO project open: the dashboard is the whole app at
 *  that point, and an Esc that dismissed it would leave an empty workbench
 *  with no way back except the menu. */
export function close() {
  if (!host || !isOpen()) return false;
  if (!state.projectName) return false;
  host.classList.add("hidden");
  setState({dashboardOpen: false});
  if (restoreFocus && typeof restoreFocus.focus === "function"
      && restoreFocus.isConnected) {
    // `preventScroll` (X10): what the dashboard was opened from is often a
    // tree row, and a virtualized row that scrolled out while the dashboard
    // was up would drag the whole list to itself on the way back.
    restoreFocus.focus({preventScroll: true});
  }
  restoreFocus = null;
  return true;
}

function onKey(e) {
  if (e.key !== "Escape" || !isOpen()) return;
  if (!state.projectName) return;      // nothing to go back to — see close()
  e.preventDefault();
  e.stopPropagation();
  close();
}

// ------------------------------------------------------------ the formatting
//
// Three pure functions, exported and node-tested: they are the strings a
// person reads off every card, and "—" versus "0 g" is exactly the kind of
// honesty a screenshot cannot grade.

/** A project's mass, formatted, or `"—"` when the server said it does not
 *  know. `null` is NOT zero: it means at least one part is unbuilt, and a
 *  partial sum shown as the total is a number somebody would act on. */
export function formatMass(grams) {
  if (typeof grams !== "number" || !Number.isFinite(grams)) return "—";
  if (grams === 0) return "0 g";
  if (grams >= 1000) return `${(grams / 1000).toFixed(2)} kg`;
  if (grams >= 10) return `${grams.toFixed(0)} g`;
  return `${grams.toFixed(2)} g`;
}

/** `3 minutes ago` from an ISO timestamp, or `""` when there is none.
 *
 *  `now` is an argument so the rounding is testable without freezing a clock,
 *  and a timestamp in the FUTURE (a clock skew between a server and a laptop
 *  is ordinary) reads as "just now" rather than "in 3 minutes", which would
 *  look like a bug in the app rather than in the clock. */
export function relativeTime(iso, now) {
  if (!iso) return "";
  const then = Date.parse(iso);
  if (!Number.isFinite(then)) return "";
  const at = Number.isFinite(now) ? now : Date.now();
  const seconds = Math.max(0, Math.round((at - then) / 1000));
  if (seconds < 45) return "just now";
  const units = [
    [60, "minute"], [60, "hour"], [24, "day"], [7, "week"],
  ];
  let value = seconds;
  let name = "second";
  for (const [step, next] of units) {
    if (value < step) break;
    value = Math.floor(value / step);
    name = next;
  }
  if (name === "week" && value >= 5) {
    const months = Math.floor(value / 4.345);
    return months >= 12
      ? `${Math.floor(months / 12)} year${Math.floor(months / 12) === 1 ? "" : "s"} ago`
      : `${months} month${months === 1 ? "" : "s"} ago`;
  }
  return `${value} ${name}${value === 1 ? "" : "s"} ago`;
}

/** The one line of counts under a card's name. */
export function countsLine(project) {
  const p = project || {};
  const parts = Number(p.n_parts) || 0;
  const instances = Number(p.n_instances) || 0;
  return `${parts} part${parts === 1 ? "" : "s"} · `
    + `${instances} instance${instances === 1 ? "" : "s"}`;
}

// --------------------------------------------------------------- rendering

function renderShell() {
  host.textContent = "";
  const head = document.createElement("header");
  head.className = "dash-head";

  const title = document.createElement("h1");
  title.className = "dash-title";
  title.textContent = "Projects";
  head.appendChild(title);

  const spacer = document.createElement("div");
  spacer.className = "dash-spacer";
  head.appendChild(spacer);

  const closeBtn = document.createElement("button");
  closeBtn.type = "button";
  closeBtn.className = "tb-btn dash-close";
  closeBtn.textContent = "Close";
  closeBtn.title = "Back to the workbench (Esc)";
  // Disabled rather than absent with no project open: a verb that vanishes
  // when it does not apply teaches the user it does not exist.
  closeBtn.disabled = !state.projectName;
  closeBtn.addEventListener("click", () => close());
  head.appendChild(closeBtn);

  host.appendChild(head);

  gridEl = document.createElement("div");
  gridEl.className = "dash-grid";
  host.appendChild(gridEl);

  const loading = document.createElement("p");
  loading.className = "dash-empty";
  loading.textContent = "Loading projects…";
  gridEl.appendChild(loading);
}

async function reload() {
  if (!gridEl) return;
  let payload;
  try {
    payload = await api.dashboard();
  } catch (err) {
    gridEl.textContent = "";
    const msg = document.createElement("p");
    msg.className = "dash-empty";
    msg.textContent = `Could not list projects: ${err.message}`;
    gridEl.appendChild(msg);
    gridEl.appendChild(newProjectCard());
    gridEl.appendChild(openPathCard());
    return;
  }
  renderGrid(payload.projects || []);
}

function renderGrid(projects) {
  gridEl.textContent = "";
  const now = Date.now();
  for (const project of projects) gridEl.appendChild(projectCard(project, now));
  gridEl.appendChild(newProjectCard());
  gridEl.appendChild(openPathCard());
}

function projectCard(project, now) {
  const card = document.createElement("button");
  card.type = "button";
  card.className = "dash-card";
  if (project.name === state.projectName) card.classList.add("current");
  card.title = project.path || project.name;
  card.addEventListener("click", () => openProject(project.name));

  card.appendChild(hero(project));

  const body = document.createElement("div");
  body.className = "dash-body";

  const name = document.createElement("div");
  name.className = "dash-name";
  name.textContent = project.name;
  body.appendChild(name);

  const counts = document.createElement("div");
  counts.className = "dash-counts";
  counts.textContent = countsLine(project);
  body.appendChild(counts);

  const meta = document.createElement("div");
  meta.className = "dash-meta";
  const mass = document.createElement("span");
  mass.className = "dash-mass";
  mass.textContent = formatMass(project.mass_g);
  mass.title = project.mass_g == null
    ? "Unknown: at least one part has never been built. The dashboard never "
      + "builds to find out."
    : "Total mass of the built parts";
  meta.appendChild(mass);

  const when = relativeTime(project.last_modified, now);
  if (when) {
    const time = document.createElement("span");
    time.className = "dash-when";
    time.textContent = when;
    time.title = project.last_modified;
    meta.appendChild(time);
  }
  body.appendChild(meta);

  const failing = Number(project.failing) || 0;
  if (failing) {
    const badge = document.createElement("span");
    badge.className = "dash-failing";
    badge.textContent = `${failing} failing`;
    badge.title = `${failing} part${failing === 1 ? "" : "s"} failed to build`;
    body.appendChild(badge);
  }

  card.appendChild(body);
  return card;
}

/** The card's hero image, or the placeholder. The server only names a `thumb`
 *  when a file already exists to answer from, so a null one is the ordinary
 *  case for a project nobody has built — not an error. */
function hero(project) {
  if (!project.thumb) return heroPlaceholder();
  const img = document.createElement("img");
  img.className = "dash-hero";
  img.loading = "lazy";
  img.decoding = "async";
  img.alt = "";
  img.src = project.thumb;
  img.addEventListener("error", () => {
    if (img.parentNode) img.parentNode.replaceChild(heroPlaceholder(), img);
  }, {once: true});
  return img;
}

function heroPlaceholder() {
  const span = document.createElement("span");
  span.className = "dash-hero dash-hero-empty";
  span.setAttribute("aria-hidden", "true");
  span.textContent = "▣";
  return span;
}

function actionCard(label, hint, run) {
  const card = document.createElement("button");
  card.type = "button";
  card.className = "dash-card dash-card-action";
  card.addEventListener("click", run);
  const glyph = document.createElement("span");
  glyph.className = "dash-hero dash-hero-empty";
  glyph.setAttribute("aria-hidden", "true");
  glyph.textContent = "+";
  card.appendChild(glyph);
  const body = document.createElement("div");
  body.className = "dash-body";
  const name = document.createElement("div");
  name.className = "dash-name";
  name.textContent = label;
  const counts = document.createElement("div");
  counts.className = "dash-counts";
  counts.textContent = hint;
  body.append(name, counts);
  card.appendChild(body);
  return card;
}

// Both run the EXISTING actions rather than reimplementing their dialogs: one
// "New project…" verb, reachable from the File menu, the palette, the project
// menu and here.
function newProjectCard() {
  return actionCard("New project…", "Create an empty project",
                    () => runProjectAction("project.new"));
}

function openPathCard() {
  return actionCard("Open by path…", "A project directory not in the list",
                    () => runProjectAction("project.open-path"));
}

/** Run one of the two project-making actions and then get out of the way (X8).
 *
 *  The card used to fire the action and forget it, so on a FIRST RUN — where
 *  the dashboard is the whole app and `close()` refuses while no project is
 *  open — the user created a project and went on looking at the dashboard,
 *  with their new workbench behind it. Awaiting it is what makes "did this
 *  actually open a project?" answerable: the name changed, so hide; it did
 *  not (the dialog was cancelled, or the create failed and toasted), so stay
 *  and refresh the listing instead, which is the honest outcome either way.
 */
async function runProjectAction(id) {
  const before = state.projectName;
  try {
    await actions.runAction(id);
  } catch {
    /* the action toasts its own failure; the dashboard just stays up */
  }
  if (state.projectName && state.projectName !== before) hideForProject();
  else await reload();
}

async function openProject(name) {
  if (name !== state.projectName) await actions.loadProject(name);
  hideForProject();
}

/** Hide, having just opened a project.
 *
 *  Not `close()`: that one refuses while `state.projectName` is null, which
 *  is precisely the state a first run clicks these cards in — the project
 *  exists by the time we get here, but the guard reads as a refusal to a
 *  reader and the ordering has bitten before. It also does not restore focus
 *  to whatever the dashboard was opened from, because the workbench behind it
 *  is a different project now. */
function hideForProject() {
  host.classList.add("hidden");
  setState({dashboardOpen: false});
  restoreFocus = null;
}
