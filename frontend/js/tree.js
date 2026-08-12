// Left sidebar: parts list + assembly instances. Pure render from state;
// all mutations go through the actions object provided by main.js.

import { state, onKeys } from "./state.js";
import { clientId } from "./api.js";

let actions = null;
let partsList = null;
let instancesList = null;

export const INSTANCE_PALETTE = [
  "#8f9aa6", "#a68d6e", "#7d9b8a", "#9184a1", "#a1786e",
  "#6e8ba1", "#9aa16e", "#a16e93",
];

export function instanceColor(inst, index) {
  return inst.color || INSTANCE_PALETTE[index % INSTANCE_PALETTE.length];
}

export function init(a) {
  actions = a;
  partsList = document.getElementById("parts-list");
  instancesList = document.getElementById("instances-list");
  document.getElementById("add-part-btn").addEventListener("click", () => {
    actions.addPart();
  });
  document.getElementById("assembly-head").addEventListener("click", () => {
    actions.selectAssembly(null);
  });
  onKeys(
    ["project", "assembly", "selectedPart", "selectedInstance", "mode",
     "rebuilding", "partKinds", "presence"],
    render
  );
  render();
}

function render() {
  renderParts();
  renderInstances();
}

function renderParts() {
  partsList.textContent = "";
  const parts = state.project ? state.project.parts : [];
  if (!parts.length) {
    const li = document.createElement("li");
    li.className = "side-empty";
    li.textContent = state.project ? "No parts yet — press +" : "No project open";
    partsList.appendChild(li);
    return;
  }
  for (const part of parts) {
    const li = document.createElement("li");
    li.className = "row";
    if (state.mode === "part" && part.id === state.selectedPart) {
      li.classList.add("selected");
    }
    li.tabIndex = 0;

    const label = document.createElement("span");
    label.className = "row-label";
    label.textContent = part.label || part.id;
    label.title = `${part.id} · ${part.material || ""}`;
    li.appendChild(label);

    const kindInfo = state.partKinds[part.id];
    if (kindInfo && kindInfo.kind === "reference") {
      const badge = document.createElement("span");
      badge.className = "row-badge";
      badge.textContent = "ref";
      badge.title = kindInfo.source
        ? `imported reference · ${kindInfo.source}`
        : "imported reference";
      li.appendChild(badge);
    }

    // Presence and claims, rendered FROM STATE like everything else here —
    // this list is cleared and rebuilt on every relevant change, so an
    // indicator poked in imperatively would survive exactly until the next
    // rebuild. "presence" is in this module's onKeys for the same reason.
    const claim = claimOn(part.id);
    if (claim) {
      const chip = document.createElement("span");
      chip.className = "row-claim";
      chip.textContent = "editing";
      chip.title =
        `${labelOf(claim.holder)} has ${part.id} open for editing. ` +
        "A soft claim: it expires on its own, it only ever binds two humans, " +
        "and it can always be overridden.";
      li.appendChild(chip);
    } else {
      const watchers = othersOn(part.id);
      if (watchers.length) {
        const d = dot("presence");
        d.title = watchers
          .map((c) => `${c.label} — ${c.focus && c.focus.surface}`)
          .join("\n");
        li.appendChild(d);
      }
    }

    if (state.rebuilding.has(part.id)) {
      li.appendChild(dot("building"));
    } else if (part.state === "error") {
      const d = dot("error");
      d.title = "Last rebuild failed";
      li.appendChild(d);
    }

    const del = document.createElement("button");
    del.type = "button";
    del.className = "row-del";
    del.textContent = "×";
    del.title = `Delete part ${part.id}`;
    del.setAttribute("aria-label", `Delete part ${part.id}`);
    del.addEventListener("click", (e) => {
      e.stopPropagation();
      actions.deletePart(part.id);
    });
    li.appendChild(del);

    li.addEventListener("click", (e) => {
      if (e.target.closest(".row-del")) return;
      actions.selectPart(part.id);
    });
    li.addEventListener("keydown", (e) => {
      // Enter on the delete button activates the button; don't also let it
      // bubble here and select the row.
      if (e.target !== li) return;
      if (e.key === "Enter") actions.selectPart(part.id);
    });
    partsList.appendChild(li);
  }
}

function renderInstances() {
  instancesList.textContent = "";
  const instances = state.project
    ? state.project.assembly.instances
    : [];
  if (!instances.length) {
    const li = document.createElement("li");
    li.className = "side-empty";
    li.textContent = "No instances";
    instancesList.appendChild(li);
    return;
  }
  instances.forEach((inst, i) => {
    const li = document.createElement("li");
    li.className = "row";
    if (state.mode === "assembly" && inst.id === state.selectedInstance) {
      li.classList.add("selected");
    }
    li.tabIndex = 0;

    const swatch = document.createElement("span");
    swatch.className = "row-swatch";
    swatch.style.background = instanceColor(inst, i);
    li.appendChild(swatch);

    const label = document.createElement("span");
    label.className = "row-label";
    label.textContent = inst.id;
    li.appendChild(label);

    const ref = document.createElement("span");
    ref.className = "row-id";
    ref.textContent = inst.part;
    li.appendChild(ref);

    li.addEventListener("click", () => actions.selectAssembly(inst.id));
    li.addEventListener("keydown", (e) => {
      if (e.target !== li) return;
      if (e.key === "Enter") actions.selectAssembly(inst.id);
    });
    instancesList.appendChild(li);
  });
}

function dot(kind) {
  const d = document.createElement("span");
  d.className = `row-dot ${kind}`;
  return d;
}

// state.presence is read directly rather than through presence.js: that module
// already imports INSTANCE_PALETTE from here, and closing the cycle for three
// one-line lookups would be a real fragility for no gain.

function claimOn(partId) {
  const claims = (state.presence && state.presence.claims) || {};
  const claim = claims[partId];
  return claim && claim.holder !== clientId ? claim : null;
}

/** Other clients whose focus names this part. */
function othersOn(partId) {
  const clients = (state.presence && state.presence.clients) || [];
  return clients.filter(
    (c) => c.id !== clientId && c.focus && c.focus.part_id === partId
  );
}

function labelOf(id) {
  const clients = (state.presence && state.presence.clients) || [];
  const found = clients.find((c) => c.id === id);
  return (found && found.label) || id || "someone";
}
