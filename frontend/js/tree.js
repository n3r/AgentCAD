// Left sidebar: parts list + assembly instances. Pure render from state;
// all mutations go through the actions object provided by main.js.

import { state, onKeys } from "./state.js";
import { clientId } from "./api.js";
import { instanceRows, memberIdsOf } from "./tree_model.js";

let actions = null;
let partsList = null;
let instancesList = null;
// Which grouped rows (patterns / sub-assemblies) are expanded to their members.
const expanded = new Set();

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

    // A configured part wears the configuration it is showing (or a neutral
    // `cfg` at base) — get_project carries both fields, so this costs no
    // fetch. A part with no family gets no badge at all.
    const cfgNames = Object.keys(part.configs || {});
    if (cfgNames.length) {
      const badge = document.createElement("span");
      badge.className = "row-badge";
      badge.textContent = part.active_config || "cfg";
      badge.title =
        `${cfgNames.length} configuration${cfgNames.length === 1 ? "" : "s"}` +
        ` · active: ${part.active_config || "base"}`;
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
  // The flattened (expanded) view, for member rows when a group is expanded.
  const flattened =
    (state.assembly && state.assembly.instances) || [];
  // Grouped rows: a pattern / sub-assembly is ONE row (tree_model), a plain
  // part a plain row. Keep the raw instance beside each row for its colour.
  const rows = instanceRows(instances);
  rows.forEach((row, i) => {
    const inst = instances[i];
    if (row.expandable) {
      renderGroupRow(row, inst, i, flattened);
    } else {
      instancesList.appendChild(instanceRow(inst, i));
    }
  });
}

// A single leaf instance row (a plain part, or one expanded member).
function instanceRow(inst, colorIndex, opts = {}) {
  const li = document.createElement("li");
  li.className = "row";
  if (opts.member) li.classList.add("row-member");
  if (state.mode === "assembly" && inst.id === state.selectedInstance) {
    li.classList.add("selected");
  }
  li.tabIndex = 0;

  const swatch = document.createElement("span");
  swatch.className = "row-swatch";
  swatch.style.background = instanceColor(inst, colorIndex);
  li.appendChild(swatch);

  const label = document.createElement("span");
  label.className = "row-label";
  label.textContent = inst.id;
  li.appendChild(label);

  if (inst.part) {
    const ref = document.createElement("span");
    ref.className = "row-id";
    // `part@config` for a bound instance: two instances of one part showing
    // different geometry is the whole point of a binding, and the part id
    // alone cannot say which is which.
    ref.textContent = inst.config ? `${inst.part}@${inst.config}` : inst.part;
    if (inst.config) ref.title = `${inst.part}, configuration ${inst.config}`;
    li.appendChild(ref);
  }

  li.addEventListener("click", () => actions.selectAssembly(inst.id));
  li.addEventListener("keydown", (e) => {
    if (e.target !== li) return;
    if (e.key === "Enter") actions.selectAssembly(inst.id);
  });
  return li;
}

// A grouped row: a pattern (×N badge) or a sub-assembly (read-only), with a
// disclosure triangle that reveals its expanded members from the flattened
// view. Selecting the group selects its first member on stage.
function renderGroupRow(row, inst, colorIndex, flattened) {
  const li = document.createElement("li");
  li.className = "row row-group";
  li.tabIndex = 0;
  const isOpen = expanded.has(row.id);

  const twist = document.createElement("span");
  twist.className = "row-twist";
  twist.textContent = isOpen ? "▾" : "▸";
  twist.setAttribute("aria-label", isOpen ? "collapse" : "expand");
  twist.addEventListener("click", (e) => {
    e.stopPropagation();
    if (isOpen) expanded.delete(row.id);
    else expanded.add(row.id);
    renderInstances();
  });
  li.appendChild(twist);

  const swatch = document.createElement("span");
  swatch.className = "row-swatch";
  swatch.style.background = instanceColor(inst, colorIndex);
  li.appendChild(swatch);

  const label = document.createElement("span");
  label.className = "row-label";
  label.textContent = row.id;
  li.appendChild(label);

  if (row.count != null) {
    const badge = document.createElement("span");
    badge.className = "row-badge";
    badge.textContent = row.badge;              // "×N"
    badge.title = `${row.kind} pattern · ${row.count} members`;
    li.appendChild(badge);
  }
  if (row.kind === "assembly") {
    const badge = document.createElement("span");
    badge.className = "row-badge";
    badge.textContent = "sub";
    badge.title = row.source
      ? `sub-assembly · source ${row.source} (read-only)`
      : "sub-assembly (read-only)";
    li.appendChild(badge);
    if (row.source) {
      const open = document.createElement("button");
      open.type = "button";
      open.className = "row-open-src";
      open.textContent = "open";
      open.title = `Open source project ${row.source}`;
      open.setAttribute("aria-label", `Open source project ${row.source}`);
      open.addEventListener("click", (e) => {
        e.stopPropagation();
        if (actions.loadProject) actions.loadProject(row.source);
      });
      li.appendChild(open);
    }
  }

  // Selecting the group highlights its first member (a group is not itself a
  // pickable body).
  const members = memberIdsOf(row.id, flattened);
  li.addEventListener("click", () => {
    if (members.length) actions.selectAssembly(members[0]);
  });
  li.addEventListener("keydown", (e) => {
    if (e.target !== li) return;
    if (e.key === "Enter" && members.length) actions.selectAssembly(members[0]);
  });
  instancesList.appendChild(li);

  if (isOpen) {
    const byId = new Map(flattened.map((m) => [m.id, m]));
    members.forEach((mid, k) => {
      const member = byId.get(mid) || { id: mid };
      // Read-only for a sub-assembly's internals; patterns select their member.
      instancesList.appendChild(
        instanceRow(member, colorIndex + k, { member: true }));
    });
  }
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
