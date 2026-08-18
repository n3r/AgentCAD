// Change proposals (PRD-002): the browser half of "an agent proposes, a human
// decides". A wide master/detail modal — the proposal list on the left, and on
// the right the header, the review actions and five tabs over the generated
// review packet. Wired like versions.js/merge.js: close button, backdrop
// click, Escape, and every node built with createElement + textContent.
//
// Conventions the server states and this UI must not get backwards:
// OLD = the target branch (what the change lands in), NEW = the source branch
// (the proposal's work). Renders, metrics and geometry all use that naming.
//
// The error contract is the merge one, twice over: POST …/merge answers HTTP
// 200 with an {"error": {"type": "merge_conflict"}} body — handed to merge.js's
// existing conflict modal — while a merge blocked by the kernel validation
// pass is a 422 ApiError carrying details.validation, retryable with
// allow_invalid. Everything else throws.
//
// Packet failures are NOT errors, they are data (FR8): build.<side>.ok false
// carries the script error, geom_diff.available false carries a reason,
// metrics.center_of_mass is null for imported meshes, and warnings/errors are
// arrays. Every renderer below draws rows from what is actually present.

import { api, ApiError } from "./api.js";
import { state, setState, onKeys } from "./state.js";
import { relTime } from "./versions.js";
import * as merge from "./merge.js";
import * as viewport from "./viewport.js";
import * as comments from "./comments.js";

const STATES = [
  "draft", "open", "approved", "changes_requested", "merged", "closed",
];
// Non-terminal states: what the toolbar badge counts.
const ACTIVE = ["draft", "open", "approved", "changes_requested"];
const STATE_LABEL = {
  draft: "draft",
  open: "open",
  approved: "approved",
  changes_requested: "changes requested",
  merged: "merged",
  closed: "closed",
};
const TABS = [
  ["overview", "Overview"],
  ["files", "Files"],
  ["geometry", "Geometry"],
  ["checks", "Checks"],
  ["audit", "Audit"],
];

let actions = null;
let overlayEl, titleEl, listEl, detailEl, newBtn, closeBtn;
let countEl, buttonEl, legendEl, legendPartEl, legendClearBtn;

let filter = null;      // state filter, or null for "all"
let selectedId = null;  // proposal id shown in the detail pane
let detail = null;      // proposal_get payload {proposal, gates, audit, packet}
let packet = null;      // the full review packet, or null
let packetError = null; // an ApiError message from the packet fetch
let packetBusy = false; // the packet GET is in flight (cold = multi-second)
let blocked = null;     // a validation report from a refused merge
let activeTab = "overview";
let busy = false;       // guards double-submit on every mutating action
let loadSeq = 0;        // drops out-of-order detail/packet responses
let overlayPart = null; // part whose diff overlay is on the viewport

export function init(a) {
  actions = a;
  overlayEl = document.getElementById("proposals-modal");
  titleEl = document.getElementById("proposals-title");
  listEl = document.getElementById("proposals-list");
  detailEl = document.getElementById("proposals-detail");
  newBtn = document.getElementById("proposals-new");
  closeBtn = document.getElementById("proposals-close");
  countEl = document.getElementById("proposals-count");
  buttonEl = document.getElementById("proposals-btn");
  legendEl = document.getElementById("diff-legend");
  legendPartEl = document.getElementById("diff-legend-part");
  legendClearBtn = document.getElementById("diff-legend-clear");

  closeBtn.addEventListener("click", close);
  newBtn.addEventListener("click", () => {
    selectedId = null;
    detail = null;
    renderList();
    renderCreate();
  });
  overlayEl.addEventListener("click", (e) => {
    if (e.target === overlayEl) close();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && isOpen()) close();
  });
  legendClearBtn.addEventListener("click", clearOverlay);
  // The overlay describes one part: clearContent() already dropped the meshes
  // when the selection moved, so the legend must not outlive them.
  onKeys(["selectedPart", "mode", "projectName", "project"], () => {
    // clearContent() already dropped the meshes when the selection moved or
    // the part rebuilt; the legend must not outlive them.
    if (overlayPart && state.selectedPart !== overlayPart) hideLegend();
    else if (overlayPart && state.mode !== "part") hideLegend();
  });
  // A thread opened on a hunk has to show up on the hunk immediately, and the
  // chips are drawn from state.comments rather than from a local count.
  onKeys(["comments"], () => {
    if (isOpen() && activeTab === "files") renderTab();
  });
}

export function isOpen() {
  return overlayEl && !overlayEl.classList.contains("hidden");
}

export async function open() {
  if (!state.projectName) {
    actions.toast("Open a project first", "error");
    return;
  }
  overlayEl.classList.remove("hidden");
  titleEl.textContent = `${state.projectName} · proposals`;
  renderDetailMessage("Select a proposal.");
  await refresh();
  // Reopening keeps the proposal that was being read: refresh() only picks a
  // default when nothing is selected.
  if (selectedId) loadDetail(selectedId);
}

/** Open the modal straight onto one proposal and one tab — what a thread
 *  anchored to a diff hunk focuses to. */
export async function openTo(id, tab) {
  if (!state.projectName) {
    actions.toast("Open a project first", "error");
    return;
  }
  overlayEl.classList.remove("hidden");
  titleEl.textContent = `${state.projectName} · proposals`;
  await refresh();
  await loadDetail(String(id));
  // After loadDetail, not before: selecting a different proposal resets the
  // tab to Overview on purpose, so asking for one has to come afterwards.
  if (tab && TABS.some(([key]) => key === tab) && activeTab !== tab) {
    activeTab = tab;
    renderDetail();
  }
}

function close() {
  overlayEl.classList.add("hidden");
  listEl.textContent = "";
  detailEl.textContent = "";
}

// ------------------------------------------------------------- the badge

/** Refresh the toolbar button and its count. Hides the button when the server
 *  has no proposal routes (no git ⇒ the tool pack registered nothing). */
export async function refreshCount() {
  const proj = state.projectName;
  if (!proj) {
    hideButton();
    return;
  }
  let payload;
  try {
    payload = await api.listProposals(proj);
  } catch {
    hideButton();
    return;
  }
  if (proj !== state.projectName) return;
  setState({ proposals: payload });
  buttonEl.classList.remove("hidden");
  const counts = payload.counts || {};
  const active = ACTIVE.reduce((sum, name) => sum + (counts[name] || 0), 0);
  countEl.textContent = String(active);
  countEl.classList.toggle("hidden", active === 0);
  buttonEl.title = active
    ? `${active} open change proposal${active === 1 ? "" : "s"}`
    : "Change proposals";
}

function hideButton() {
  if (!buttonEl) return;
  buttonEl.classList.add("hidden");
  countEl.classList.add("hidden");
}

// ------------------------------------------------------------ the events

/** proposal_changed {project, id, state, reason}. main.js has already checked
 *  the project. */
export function handleEvent(ev) {
  refreshCount();
  if (!isOpen()) return;
  refresh();
  // A packet finishing generation elsewhere, or another client's review, must
  // land in the open detail pane too.
  if (selectedId && ev.id === selectedId) loadDetail(selectedId, ev.reason === "packet");
}

// -------------------------------------------------------------- the list

async function refresh() {
  const proj = state.projectName;
  let payload;
  try {
    payload = await api.listProposals(proj, filter);
  } catch (err) {
    listEl.textContent = "";
    listEl.appendChild(note(`Proposals unavailable: ${errorText(err)}`, "err"));
    return;
  }
  if (proj !== state.projectName || !isOpen()) return;
  setState({ proposals: payload });
  renderList();
  if (!selectedId) {
    const first = (payload.proposals || [])[0];
    if (first) loadDetail(first.id);
  }
}

function renderList() {
  const payload = state.proposals || { proposals: [], counts: {} };
  const counts = payload.counts || {};
  listEl.textContent = "";

  const filters = div("prop-filters");
  const total = STATES.reduce((sum, name) => sum + (counts[name] || 0), 0);
  filters.appendChild(filterChip(null, `all ${total}`));
  for (const name of STATES) {
    if (!counts[name]) continue;
    filters.appendChild(filterChip(name, `${STATE_LABEL[name]} ${counts[name]}`));
  }
  listEl.appendChild(filters);

  const rows = payload.proposals || [];
  if (!rows.length) {
    listEl.appendChild(
      note(
        filter
          ? `No ${STATE_LABEL[filter]} proposals.`
          : "No proposals yet. “New proposal…” opens one for review from a " +
            "branch — the packet (diffs, metric deltas, matched renders, " +
            "added/removed volume) is generated on first view."
      )
    );
    return;
  }
  for (const row of rows) {
    const item = document.createElement("button");
    item.type = "button";
    item.className = "prop-item";
    if (row.id === selectedId) item.classList.add("active");

    const head = div("prop-item-head");
    head.appendChild(dot(row.state));
    const id = span("prop-id", `#${row.id}`);
    const title = span("prop-item-title", row.title || "(untitled)");
    head.append(id, title);
    item.appendChild(head);

    const meta = div("prop-item-meta");
    meta.appendChild(chip(row.state));
    meta.appendChild(span("prop-branches", `${row.source} → ${row.target}`));
    item.appendChild(meta);

    const foot = div("prop-item-meta");
    foot.appendChild(kindBadge(row.author, row.author_kind));
    foot.appendChild(span("prop-dim", ago(row.updated || row.created)));
    if (row.reviews) {
      foot.appendChild(
        span("prop-dim", `${row.reviews} review${row.reviews === 1 ? "" : "s"}`)
      );
    }
    item.appendChild(foot);

    item.addEventListener("click", () => loadDetail(row.id));
    listEl.appendChild(item);
  }
}

function filterChip(name, label) {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "prop-filter";
  if (filter === name) btn.classList.add("active");
  btn.textContent = label;
  btn.addEventListener("click", () => {
    filter = name;
    refresh();
  });
  return btn;
}

// ------------------------------------------------------------ the detail

async function loadDetail(id, refetchPacket = false) {
  const proj = state.projectName;
  const seq = ++loadSeq;
  if (id !== selectedId) {
    packet = null;
    packetError = null;
    blocked = null;
    activeTab = "overview";
  }
  selectedId = id;
  renderList();
  renderDetailMessage("Loading…");
  let payload;
  try {
    payload = await api.getProposal(proj, id);
  } catch (err) {
    if (seq !== loadSeq) return;
    renderDetailMessage(`Could not load proposal #${id}: ${errorText(err)}`, "err");
    return;
  }
  if (seq !== loadSeq || proj !== state.projectName) return;
  detail = payload;
  setState({ proposal: payload });
  renderDetail();
  // A null summary means no packet on disk yet (lazy generation); a stale one
  // regenerates on this same GET. Either way the plain fetch is right — only
  // the explicit Regenerate button asks for regenerate=1.
  if (packet == null || refetchPacket) loadPacket(seq, false);
}

/** The packet is generated lazily on first view and regenerated by this same
 *  GET when a branch head moved, so a cold call takes seconds — hence the
 *  spinner. A frozen (post-merge) packet is served as-is and never regenerated
 *  (asking for that is a 409), so no regenerate affordance is offered for one. */
async function loadPacket(seq, regenerate = false) {
  const proj = state.projectName;
  const id = selectedId;
  packetBusy = true;
  packetError = null;
  renderTab();
  let payload;
  try {
    payload = await api.getPacket(proj, id, regenerate);
  } catch (err) {
    if (seq !== loadSeq) return;
    packetBusy = false;
    packetError = errorText(err);
    renderTab();
    return;
  }
  if (seq !== loadSeq || proj !== state.projectName) return;
  packetBusy = false;
  packet = payload;
  renderTab();
}

function renderDetailMessage(message, kind) {
  detailEl.textContent = "";
  detailEl.appendChild(note(message, kind));
}

function renderDetail() {
  detailEl.textContent = "";
  if (!detail) return;
  const proposal = detail.proposal || {};

  const head = div("prop-head");
  const line = div("prop-head-line");
  line.appendChild(span("prop-id", `#${proposal.id}`));
  line.appendChild(span("prop-title", proposal.title || "(untitled)"));
  line.appendChild(chip(proposal.state));
  head.appendChild(line);

  const meta = div("prop-head-meta");
  meta.appendChild(
    span("prop-branches", `${proposal.source} → ${proposal.target}`)
  );
  meta.appendChild(span("prop-dim", "(new → old)"));
  meta.appendChild(kindBadge(proposal.author, proposal.author_kind));
  meta.appendChild(span("prop-dim", `opened ${ago(proposal.created)}`));
  if ((proposal.merge || {}).commit) {
    meta.appendChild(
      span("prop-dim", `merged as ${String(proposal.merge.commit).slice(0, 8)}`)
    );
  }
  head.appendChild(meta);
  head.appendChild(renderActions(proposal));
  detailEl.appendChild(head);

  const nav = div("prop-tabs");
  for (const [key, label] of TABS) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "prop-tab";
    if (key === activeTab) btn.classList.add("active");
    btn.textContent = label;
    btn.addEventListener("click", () => {
      activeTab = key;
      renderDetail();
    });
    nav.appendChild(btn);
  }
  detailEl.appendChild(nav);

  const pane = div("prop-pane");
  pane.id = "prop-pane";
  detailEl.appendChild(pane);
  renderTab();
}

function renderActions(proposal) {
  const row = div("prop-actions");
  const st = proposal.state;
  const gates = detail.gates || [];
  const failing = gates.find((g) => g.state === "fail");

  if (st === "draft") {
    row.appendChild(button("Open for review", () => setProposalState("open")));
  }
  if (st === "changes_requested") {
    row.appendChild(button("Re-request review", () => setProposalState("open")));
  }
  if (["open", "approved", "changes_requested"].includes(st)) {
    row.appendChild(button("Approve", () => review("approve")));
    row.appendChild(button("Request changes", () => review("request_changes")));
    row.appendChild(button("Comment", () => review("comment")));
    const mergeBtn = button("Merge", (btn) => doMerge(false, btn));
    // A disabled button is a HINT — the service enforces the policy, and
    // refuses even when this UI would have allowed it (FR11).
    mergeBtn.disabled = !!failing;
    mergeBtn.title = failing
      ? `${failing.name}: ${failing.summary}`
      : "Run the gates and land this proposal";
    row.appendChild(mergeBtn);
  }
  if (st === "closed") {
    row.appendChild(button("Reopen", () => setProposalState("open")));
  } else if (st !== "merged") {
    row.appendChild(button("Close", () => setProposalState("closed")));
  }
  if (st !== "merged") {
    row.appendChild(button("Edit…", editPrompt));
  }
  const frozen = !!(detail.packet || {}).frozen || !!(packet || {}).frozen;
  if (!frozen) {
    row.appendChild(
      button("Regenerate packet", () => loadPacket(loadSeq, true))
    );
  } else {
    row.appendChild(
      span("prop-dim", "packet frozen at merge")
    );
  }
  return row;
}

// --------------------------------------------------------------- the tabs

// PRD-008: the hover affordance and the existing-thread chips on the diff
// rows PRD-002 stamped `data-part`/`data-hunk`/`data-line` onto for exactly
// this. Re-applied after EVERY renderTab, because #prop-pane is rebuilt on
// each tab click and on every proposal_changed — an affordance attached once
// would survive precisely one render.
let hoverBtn = null;

function decorateDiffs(pane) {
  if (!pane || !selectedId) return;
  const groups = comments.hunkThreads(selectedId);
  for (const row of pane.querySelectorAll(".diff-line.diff-hunk")) {
    const file = row.closest(".diff-block").dataset.file;
    const threads = groups.get(`${file}\u0000${row.dataset.hunk}`);
    if (!threads || !threads.length) continue;
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "diff-thread-chip";
    chip.textContent = `💬 ${threads.length}`;
    chip.title = threads
      .map((th) => `${th.author}: ${(th.comments[0] || {}).body || ""}`)
      .join("\n");
    chip.addEventListener("click", (e) => {
      e.stopPropagation();
      comments.showThread(threads[0].id);
    });
    row.appendChild(chip);
  }
  // ONE shared hover button, moved between rows, rather than one per line: a
  // large diff is thousands of rows and this pane is rebuilt constantly.
  pane.addEventListener("mouseover", (e) => {
    const row = e.target.closest && e.target.closest(".diff-line");
    if (!row || !row.dataset.hunk) return;
    // Rows before the first @@ carry data-hunk="-1": there is no hunk there to
    // anchor to, so no affordance is offered.
    if (Number(row.dataset.hunk) < 0) return;
    if (!hoverBtn) {
      hoverBtn = document.createElement("button");
      hoverBtn.type = "button";
      hoverBtn.className = "diff-comment-btn";
      hoverBtn.textContent = "💬";
      hoverBtn.title = "Comment on this hunk";
      hoverBtn.addEventListener("click", (ev) => {
        ev.stopPropagation();
        const host = hoverBtn.parentElement;
        if (!host) return;
        comments.openComposer(
          {
            kind: "proposal_hunk",
            proposal: String(selectedId),
            file: host.closest(".diff-block").dataset.file,
            hunk: Number(host.dataset.hunk),
          },
          {
            label: `#${selectedId} · ${host.closest(".diff-block").dataset.file}` +
              ` hunk ${host.dataset.hunk}`,
            at: { x: ev.clientX, y: ev.clientY },
          }
        );
      });
    }
    if (hoverBtn.parentElement !== row) row.appendChild(hoverBtn);
  });
}

function renderTab() {
  const pane = document.getElementById("prop-pane");
  if (!pane) return;
  pane.textContent = "";
  if (activeTab === "checks") return renderChecks(pane);
  if (activeTab === "audit") return renderAudit(pane);

  // The remaining three read the packet.
  if (packetBusy) {
    const wrap = div("prop-loading");
    const spin = document.createElement("span");
    spin.className = "spinner";
    wrap.appendChild(spin);
    wrap.appendChild(
      span(
        "prop-dim",
        "Generating the review packet — the first view builds both sides, " +
          "diffs the geometry and renders the pair."
      )
    );
    pane.appendChild(wrap);
    return;
  }
  if (packetError) {
    pane.appendChild(note(`Packet unavailable: ${packetError}`, "err"));
    return;
  }
  if (!packet) {
    pane.appendChild(note("No review packet yet."));
    return;
  }
  if (activeTab === "overview") return renderOverview(pane);
  if (activeTab === "files") {
    renderFiles(pane);
    decorateDiffs(pane);
    return;
  }
  if (activeTab === "geometry") return renderGeometry(pane);
}

function renderOverview(pane) {
  const proposal = (detail || {}).proposal || {};
  if (proposal.description) {
    pane.appendChild(section("Description", note(proposal.description)));
  }
  pane.appendChild(packetHeader());

  const summary = packet.summary || {};
  const bits = [
    `${summary.parts_changed || 0} part${summary.parts_changed === 1 ? "" : "s"} changed`,
    `${summary.parts_added || 0} added`,
    `${summary.parts_removed || 0} removed`,
    `${summary.instances_changed || 0} instance change${summary.instances_changed === 1 ? "" : "s"}`,
  ];
  if (summary.mass_delta_g != null) {
    bits.push(`${signed(summary.mass_delta_g, 2)} g total mass`);
  }
  pane.appendChild(note(bits.join(" · ")));

  for (const part of packet.parts || []) {
    const block = div("prop-part");
    block.appendChild(partHeading(part));
    for (const side of ["old", "new"]) {
      const build = (part.build || {})[side];
      if (!build || build.ok) continue;
      block.appendChild(
        note(
          `${side} side does not build: ` +
            ((build.error && (build.error.message || build.error.type)) ||
              "part absent on this side"),
          "err"
        )
      );
    }
    if (part.metrics) block.appendChild(metricsTable(part.metrics));
    block.appendChild(renderPair(part));
    pane.appendChild(block);
  }

  const assembly = packet.assembly;
  if (assembly && assembly.changed) pane.appendChild(assemblyBlock(assembly));
  const manifest = packet.manifest || {};
  const scalars = manifest.scalars_changed || [];
  const materials = manifest.materials_changed || [];
  if (scalars.length || materials.length) {
    const list = document.createElement("ul");
    list.className = "prop-bullets";
    for (const row of scalars) {
      list.appendChild(
        li(`${row.key}: ${json(row.old)} → ${json(row.new)}`)
      );
    }
    for (const row of materials) {
      list.appendChild(
        li(`material ${row.id}: ${json(row.old)} → ${json(row.new)}`)
      );
    }
    pane.appendChild(section("Project settings", list));
  }
  for (const entry of packet.binary || []) {
    const sides = entry.sides || {};
    pane.appendChild(
      note(
        `${entry.path} — binary: ` +
          ["old", "new"]
            .map((side) =>
              sides[side]
                ? `${side} ${Number(sides[side].bytes).toLocaleString("en-US")} B ` +
                  `${String(sides[side].sha256).slice(0, 12)}`
                : `${side} absent`
            )
            .join(" · ")
      )
    );
  }
  appendDegradation(pane);
}

function renderFiles(pane) {
  pane.appendChild(packetHeader());
  const parts = packet.parts || [];
  if (!parts.length) {
    pane.appendChild(note("No part changed on either side."));
    return;
  }
  for (const part of parts) {
    const block = div("prop-part");
    block.appendChild(partHeading(part));
    const diff = part.script_diff;
    if (!diff) {
      block.appendChild(note("No script change — the manifest changed only."));
    } else if (diff.truncated || diff.unified == null) {
      block.appendChild(
        note(
          `${diff.path} — diff too large to show; the file is on the branch.`,
          "err"
        )
      );
    } else {
      block.appendChild(
        note(
          `${diff.path} · +${diff.added_lines} −${diff.removed_lines}`
        )
      );
      block.appendChild(diffBlock(part.part, diff));
    }
    const params = part.params_diff || {};
    const rows = [
      ...(params.added || []).map((r) => [r.name, "added", undefined, r.value]),
      ...(params.removed || []).map((r) => [r.name, "removed", r.value, undefined]),
      ...(params.changed || []).map((r) => [r.name, r.field, r.old, r.new]),
    ];
    if (rows.length) {
      const table = document.createElement("table");
      table.className = "prop-table";
      table.appendChild(
        tr(["parameter", "field", "old", "new"], "th")
      );
      for (const [name, field, before, after] of rows) {
        table.appendChild(tr([name, field, json(before), json(after)]));
      }
      block.appendChild(section("PARAMS", table));
    }
    pane.appendChild(block);
  }
}

function renderGeometry(pane) {
  pane.appendChild(packetHeader());
  pane.appendChild(
    note(
      "Volumes are kernel booleans on the two builds: red is material the " +
        "proposal removes, green is material it adds. “Show in viewport” " +
        "selects the part and draws the volumes translucent over it."
    )
  );
  let any = false;
  for (const part of packet.parts || []) {
    const geom = part.geom_diff;
    if (!geom) continue;
    any = true;
    const block = div("prop-part");
    block.appendChild(partHeading(part));
    if (geom.unchanged) {
      block.appendChild(
        note("Identical geometry — the content hash matched, so no kernel work was done.")
      );
    } else if (!geom.available) {
      block.appendChild(
        note(
          `No geometric diff: ${geom.reason || "unavailable"}` +
            (geom.skipped === "mesh"
              ? " (an imported mesh has no surfaces to subtract)"
              : ""),
          "err"
        )
      );
    } else {
      const nums = div("prop-geom-nums");
      nums.appendChild(
        span("prop-geom removed", `− ${num(geom.removed_mm3, 1)} mm³ removed`)
      );
      nums.appendChild(
        span("prop-geom added", `+ ${num(geom.added_mm3, 1)} mm³ added`)
      );
      block.appendChild(nums);
      if (geom.added_mesh || geom.removed_mesh) {
        const row = div("prop-actions");
        row.appendChild(
          button("Show in viewport", (btn) => showOverlay(part, btn))
        );
        block.appendChild(row);
      } else {
        block.appendChild(note("No diff mesh — both volumes are zero."));
      }
    }
    pane.appendChild(block);
  }
  if (!any) pane.appendChild(note("No part geometry to compare."));
}

function renderChecks(pane) {
  const gates = (detail || {}).gates || [];
  for (const gate of gates) {
    const row = div("prop-gate");
    row.appendChild(gateChip(gate.state));
    const body = div("prop-gate-body");
    body.appendChild(span("prop-gate-name", gate.name));
    body.appendChild(span("prop-dim", gate.summary || ""));
    const report = (gate.details || {}).validation;
    if (report) body.appendChild(merge.reportBlock(report));
    row.appendChild(body);
    pane.appendChild(row);
  }
  const reviews = ((detail || {}).proposal || {}).reviews || [];
  if (reviews.length) {
    const list = document.createElement("ul");
    list.className = "prop-bullets";
    for (const review of reviews) {
      list.appendChild(
        li(
          `${review.actor} (${review.actor_kind}) · ${review.verdict}` +
            (review.stale ? " · stale (older source head)" : "") +
            (review.summary ? ` — ${review.summary}` : "")
        )
      );
    }
    pane.appendChild(section("Reviews", list));
  }
  if (blocked) {
    const box = div("prop-blocked");
    box.appendChild(
      note(
        "The merged state failed the kernel validation pass, so nothing " +
          "landed. Fix the source branch and merge again, or land it anyway — " +
          "the failures are recorded in the merge commit and in the audit log.",
        "err"
      )
    );
    box.appendChild(merge.reportBlock(blocked));
    const row = div("prop-actions");
    row.appendChild(
      button("Merge anyway (allow_invalid)", (btn) => doMerge(true, btn))
    );
    box.appendChild(row);
    pane.appendChild(box);
  }
}

function renderAudit(pane) {
  const entries = (detail || {}).audit || [];
  if (!entries.length) {
    pane.appendChild(note("No audit entries."));
    return;
  }
  const table = document.createElement("table");
  table.className = "prop-table";
  table.appendChild(tr(["#", "when", "actor", "action", "details"], "th"));
  for (const entry of entries) {
    const row = tr([
      String(entry.seq ?? ""),
      entry.ts || "",
      `${entry.actor || ""} (${entry.actor_kind || "?"})`,
      entry.action || "",
      json(entry.details),
    ]);
    table.appendChild(row);
  }
  pane.appendChild(table);
}

// -------------------------------------------------------- packet fragments

function packetHeader() {
  const wrap = div("prop-packet-head");
  if (!packet.generated) {
    // The frozen record that no packet existed when the decision was made:
    // there are no heads, no timing and no parts to describe, only the note.
    wrap.appendChild(span("prop-dim", packet.note || "no review packet"));
    wrap.appendChild(span("prop-flag", "frozen"));
    return wrap;
  }
  wrap.appendChild(
    span(
      "prop-dim",
      `packet generated ${ago(packet.generated)} by ${packet.generated_by} · ` +
        `${packet.elapsed_ms} ms · ${String(packet.target_head).slice(0, 8)} → ` +
        `${String(packet.source_head).slice(0, 8)}`
    )
  );
  if (packet.frozen) {
    wrap.appendChild(span("prop-flag", "frozen"));
    if (packet.stale_at_merge) {
      // It was frozen, but it was already behind: the commits it describes
      // are not the commits that merged.
      const chip = span("prop-flag stale", "stale at merge");
      chip.title =
        "This packet was generated against earlier commits than the ones the " +
        "merge landed — the source moved after it was measured";
      wrap.appendChild(chip);
    }
  } else if (packet.stale) {
    wrap.appendChild(span("prop-flag stale", "stale"));
  }
  return wrap;
}

function partHeading(part) {
  const head = div("prop-part-head");
  head.appendChild(span("prop-part-name", part.part));
  head.appendChild(span("prop-flag", part.change));
  for (const by of part.changed_by || []) {
    head.appendChild(span("prop-dim", by));
  }
  return head;
}

function metricsTable(metrics) {
  const table = document.createElement("table");
  table.className = "prop-table";
  table.appendChild(tr(["metric", "old", "new", "Δ", "%"], "th"));
  for (const [key, digits] of [
    ["volume_mm3", 1],
    ["mass_g", 2],
    ["area_mm2", 1],
  ]) {
    const row = metrics[key] || {};
    table.appendChild(
      tr([
        key,
        num(row.old, digits),
        num(row.new, digits),
        signed(row.delta, digits),
        row.pct == null ? "—" : `${signed(row.pct, 2)}%`,
      ])
    );
  }
  const com = metrics.center_of_mass;
  table.appendChild(
    tr([
      "center_of_mass",
      vec(com && com.old),
      vec(com && com.new),
      com && com.delta ? vec(com.delta, true) : "—",
      // build_reference reports an imported mesh's CoM as the bbox centre, so
      // the packet reports none at all rather than dressing it as a property.
      com == null ? "n/a (mesh)" : "",
    ])
  );
  const bbox = metrics.bbox || {};
  table.appendChild(
    tr([
      "bbox size",
      bboxSize(bbox.old),
      bboxSize(bbox.new),
      bbox.size_delta_mm ? vec(bbox.size_delta_mm, true) : "—",
      "",
    ])
  );
  return table;
}

/** The before/after pair. They are frame-matched 640×480 iso renders, so
 *  superimposing them is exact — hover cross-fades old into new. */
function renderPair(part) {
  const renders = part.renders || {};
  const wrap = div("prop-renders");
  if (!renders.old && !renders.new) {
    wrap.appendChild(note("No renders for this part."));
    return wrap;
  }
  if (renders.old && renders.new) {
    const stack = div("prop-render");
    stack.title = "hover to cross-fade old → new";
    stack.append(img(renders.old, "old"), img(renders.new, "new", "prop-render-top"));
    stack.appendChild(span("prop-render-label", "old ⇄ new (hover)"));
    wrap.appendChild(stack);
    return wrap;
  }
  const side = renders.old ? "old" : "new";
  const single = div("prop-render");
  single.appendChild(img(renders[side], side));
  single.appendChild(span("prop-render-label", `${side} only`));
  wrap.appendChild(single);
  return wrap;
}

function img(src, alt, className) {
  const el = document.createElement("img");
  el.src = src;
  el.alt = `${alt} render`;
  el.loading = "lazy";
  if (className) el.className = className;
  return el;
}

/** The unified diff as plain DOM — one node per line, carrying the
 *  part/hunk/line attributes PRD-008 will anchor comment threads to. Not a
 *  CodeMirror merge view: the merge addon is not vendored and the frontend is
 *  offline-only. */
function diffBlock(partId, diff) {
  const block = div("diff-block");
  block.dataset.part = partId;
  // The DIFF PATH, not the part id: a proposal_hunk anchor names the file the
  // packet's script diffs are keyed by, and `data-part` alone cannot spell it.
  block.dataset.file = diff.path || "";
  let hunk = -1;
  const lines = String(diff.unified || "").split("\n");
  if (lines.length && lines[lines.length - 1] === "") lines.pop();
  lines.forEach((text, index) => {
    const row = div("diff-line");
    if (text.startsWith("@@")) {
      hunk += 1;
      row.classList.add("diff-hunk");
    } else if (text.startsWith("+")) {
      row.classList.add("diff-add");
    } else if (text.startsWith("-")) {
      row.classList.add("diff-del");
    } else if (text.startsWith("\\")) {
      row.classList.add("diff-meta");
    } else {
      row.classList.add("diff-ctx");
    }
    row.dataset.part = partId;
    row.dataset.hunk = String(hunk);
    row.dataset.line = String(index);
    row.textContent = text || " ";
    block.appendChild(row);
  });
  return block;
}

function assemblyBlock(assembly) {
  const list = document.createElement("ul");
  list.className = "prop-bullets";
  for (const row of assembly.instances_added || []) {
    list.appendChild(li(`added ${row.id} (${row.part})`));
  }
  for (const row of assembly.instances_removed || []) {
    list.appendChild(li(`removed ${row.id} (${row.part})`));
  }
  for (const row of assembly.instances_moved || []) {
    list.appendChild(
      li(
        `moved ${row.id}: ${vec(row.old.position)} → ${vec(row.new.position)} · ` +
          `${vec(row.old.rotation_deg)}° → ${vec(row.new.rotation_deg)}°`
      )
    );
  }
  for (const row of assembly.mates_changed || []) {
    list.appendChild(li(`mate ${row.id}: ${json(row.old)} → ${json(row.new)}`));
  }
  // The packet has carried `configs_changed` since PRD-012 and this block
  // never showed it: a rebinding-only proposal rendered an Assembly section
  // with nothing in it. "base" is the unbound state (`config` absent).
  for (const row of assembly.configs_changed || []) {
    list.appendChild(
      li(`config ${row.id}: ${row.old || "base"} → ${row.new || "base"}`)
    );
  }
  const mass = assembly.total_mass_g || {};
  if (mass.delta != null) {
    list.appendChild(
      li(
        `total mass ${num(mass.old, 2)} → ${num(mass.new, 2)} g ` +
          `(${signed(mass.delta, 2)} g)`
      )
    );
  }
  return section("Assembly", list);
}

function appendDegradation(pane) {
  const warnings = packet.warnings || [];
  const errors = packet.errors || [];
  if (!warnings.length && !errors.length) return;
  const list = document.createElement("ul");
  list.className = "prop-bullets";
  for (const text of warnings) list.appendChild(li(text));
  for (const entry of errors) {
    list.appendChild(
      li(
        `${entry.part || "project"} · ${entry.stage}: ` +
          ((entry.error && (entry.error.message || entry.error.type)) || "failed")
      )
    );
  }
  pane.appendChild(section("Warnings", list));
}

// ------------------------------------------------------------- the actions

async function review(verdict) {
  if (busy || !detail) return;
  let summary = null;
  if (verdict !== "approve") {
    summary = prompt(
      verdict === "comment" ? "Comment:" : "What needs to change?"
    );
    if (summary === null) return;
  }
  busy = true;
  try {
    await api.reviewProposal(state.projectName, selectedId, verdict, summary || undefined);
    actions.toast(`Recorded ${verdict.replace("_", " ")} on #${selectedId}`);
  } catch (err) {
    actions.toast(`Review failed: ${errorText(err)}`, "error");
  } finally {
    busy = false;
  }
  await loadDetail(selectedId);
  await refresh();
}

async function setProposalState(next) {
  if (busy || !detail) return;
  busy = true;
  try {
    await api.updateProposal(state.projectName, selectedId, { state: next });
    actions.toast(`Proposal #${selectedId} is now ${STATE_LABEL[next]}`);
  } catch (err) {
    actions.toast(`Update failed: ${errorText(err)}`, "error");
  } finally {
    busy = false;
  }
  await loadDetail(selectedId);
  await refresh();
}

async function editPrompt() {
  if (busy || !detail) return;
  const proposal = detail.proposal || {};
  const title = prompt("Title:", proposal.title || "");
  if (title === null) return;
  const description = prompt("Description:", proposal.description || "");
  if (description === null) return;
  busy = true;
  try {
    await api.updateProposal(state.projectName, selectedId, { title, description });
  } catch (err) {
    actions.toast(`Update failed: ${errorText(err)}`, "error");
  } finally {
    busy = false;
  }
  await loadDetail(selectedId);
  await refresh();
}

/** The gate, then PRD-001's merge. Three outcomes, all of them the server's:
 *  a landed merge, a `merge_conflict` body at HTTP 200 (handed to the existing
 *  conflict modal), or a 422 whose details.validation is the blocked kernel
 *  report — which the Checks tab shows with a "land anyway" button. */
async function doMerge(allowInvalid, btn) {
  if (busy || !detail) return;
  busy = true;
  const label = btn ? btn.textContent : "";
  if (btn) {
    btn.disabled = true;
    btn.textContent = "Merging…";
  }
  let res = null;
  try {
    res = await api.mergeProposal(state.projectName, selectedId, allowInvalid);
  } catch (err) {
    const details = err instanceof ApiError ? err.error.details || {} : {};
    if (details.validation) {
      blocked = details.validation;
      activeTab = "checks";
      actions.toast("Merge blocked by the kernel validation pass", "error");
    } else {
      actions.toast(`Merge failed: ${errorText(err)}`, "error");
    }
  } finally {
    busy = false;
    if (btn) {
      btn.disabled = false;
      btn.textContent = label;
    }
  }
  if (res && res.error) {
    if (res.error.type === "merge_conflict") {
      actions.toast(
        "Conflicts — resolve them, then merge the proposal again",
        "error"
      );
      close();
      await merge.checkStaged();
      return;
    }
    actions.toast(`Merge failed: ${res.error.message || "error"}`, "error");
  } else if (res) {
    blocked = null;
    actions.toast(
      res.fast_forward
        ? `Fast-forwarded ${res.target} to ${res.source}`
        : `Merged #${selectedId} as ${String(res.commit || "").slice(0, 8)}`
    );
    packet = null; // the merged packet is frozen: refetch it
  }
  await loadDetail(selectedId, true);
  await refresh();
  refreshCount();
}

// ------------------------------------------------------------ the overlay

async function showOverlay(part, btn) {
  const geom = part.geom_diff || {};
  const wanted = [
    ["removed", geom.removed_mesh],
    ["added", geom.added_mesh],
  ].filter(([, url]) => !!url);
  if (!wanted.length) return;
  if (btn) {
    btn.disabled = true;
    btn.textContent = "Loading…";
  }
  const meshes = [];
  try {
    for (const [kind, url] of wanted) {
      meshes.push([kind, (await api.getDiffMesh(url)).buffer]);
    }
  } catch (err) {
    actions.toast(`Diff mesh unavailable: ${errorText(err)}`, "error");
    return;
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = "Show in viewport";
    }
  }
  close();
  await actions.selectPart(part.part);
  const key = `${selectedId}:${(packet || {}).generated}`;
  let shown = 0;
  for (const [kind, buffer] of meshes) {
    if (viewport.showDiffOverlay(part.part, buffer, key, kind)) shown += 1;
  }
  if (!shown) {
    actions.toast(
      `${part.part} is not on stage — open it in part mode to see the overlay`,
      "error"
    );
    return;
  }
  overlayPart = part.part;
  legendPartEl.textContent = `${part.part} · proposal #${selectedId}`;
  legendEl.classList.remove("hidden");
}

function clearOverlay() {
  viewport.clearDiffOverlay();
  hideLegend();
}

function hideLegend() {
  overlayPart = null;
  if (legendEl) legendEl.classList.add("hidden");
}

// ------------------------------------------------------------ the create form

async function renderCreate() {
  detailEl.textContent = "";
  const head = div("prop-head");
  head.appendChild(span("prop-title", "New proposal"));
  head.appendChild(
    note(
      "A proposal reviews one branch into another. The target defaults to the " +
        "project's default branch — not to the branch you happen to be on."
    )
  );
  detailEl.appendChild(head);

  let branches = [];
  let current = null;
  try {
    const payload = await api.listBranches(state.projectName);
    branches = payload.branches || [];
    current = payload.current;
  } catch (err) {
    detailEl.appendChild(note(`Branches unavailable: ${errorText(err)}`, "err"));
    return;
  }
  const form = div("prop-form");
  const sourceSel = select(branches.map((b) => b.name));
  const targetSel = select(branches.map((b) => b.name));
  const def = branches.find((b) => b.is_default);
  targetSel.value = def ? def.name : current;
  const other = branches.find((b) => b.name !== targetSel.value);
  sourceSel.value = current && current !== targetSel.value
    ? current
    : other
      ? other.name
      : targetSel.value;

  const title = document.createElement("input");
  title.type = "text";
  title.placeholder = "What does this change do?";
  const description = document.createElement("textarea");
  description.rows = 3;
  description.placeholder = "Why (optional)";
  const draft = document.createElement("input");
  draft.type = "checkbox";
  draft.id = "prop-draft";
  const draftLabel = document.createElement("label");
  draftLabel.htmlFor = "prop-draft";
  draftLabel.textContent = "open as a draft";

  form.appendChild(formRow("Source (new)", sourceSel));
  form.appendChild(formRow("Target (old)", targetSel));
  form.appendChild(formRow("Title", title));
  form.appendChild(formRow("Description", description));
  const draftRow = div("prop-form-row");
  draftRow.append(draft, draftLabel);
  form.appendChild(draftRow);

  const row = div("prop-actions");
  row.appendChild(
    button("Create proposal", async (btn) => {
      if (busy) return;
      if (sourceSel.value === targetSel.value) {
        actions.toast("Pick two different branches", "error");
        return;
      }
      if (!title.value.trim()) {
        actions.toast("Give the proposal a title", "error");
        return;
      }
      busy = true;
      btn.disabled = true;
      btn.textContent = "Creating…";
      let created = null;
      try {
        created = await api.createProposal(state.projectName, {
          source: sourceSel.value,
          target: targetSel.value,
          title: title.value.trim(),
          description: description.value.trim() || undefined,
          draft: draft.checked,
        });
      } catch (err) {
        actions.toast(`Create failed: ${errorText(err)}`, "error");
      } finally {
        busy = false;
        btn.disabled = false;
        btn.textContent = "Create proposal";
      }
      if (!created) return;
      actions.toast(`Opened proposal #${created.proposal.id}`);
      await refresh();
      await loadDetail(created.proposal.id);
      refreshCount();
    })
  );
  form.appendChild(row);
  detailEl.appendChild(form);
}

function formRow(label, control) {
  const row = div("prop-form-row");
  const el = document.createElement("label");
  el.textContent = label;
  row.append(el, control);
  return row;
}

function select(names) {
  const el = document.createElement("select");
  for (const name of names) {
    const option = document.createElement("option");
    option.value = name;
    option.textContent = name;
    el.appendChild(option);
  }
  return el;
}

// ------------------------------------------------------------------ atoms

function div(className) {
  const el = document.createElement("div");
  el.className = className;
  return el;
}

function span(className, text) {
  const el = document.createElement("span");
  el.className = className;
  el.textContent = text == null ? "" : String(text);
  return el;
}

function note(text, kind) {
  const el = div(kind === "err" ? "prop-note err" : "prop-note");
  el.textContent = text;
  return el;
}

function section(heading, body) {
  const wrap = div("prop-section");
  const title = document.createElement("h4");
  title.textContent = heading;
  wrap.append(title, body);
  return wrap;
}

function li(text) {
  const el = document.createElement("li");
  el.textContent = text;
  return el;
}

function tr(cells, tag = "td") {
  const row = document.createElement("tr");
  for (const value of cells) {
    const cell = document.createElement(tag);
    cell.textContent = value == null ? "" : String(value);
    row.appendChild(cell);
  }
  return row;
}

function button(label, run) {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "tb-btn";
  btn.textContent = label;
  btn.addEventListener("click", () => run(btn));
  return btn;
}

function chip(stateName) {
  const el = span(`prop-chip state-${stateName}`, STATE_LABEL[stateName] || stateName);
  return el;
}

function gateChip(gateState) {
  return span(`gate-chip gate-${gateState}`, gateState);
}

function dot(stateName) {
  const el = span(`prop-dot state-${stateName}`, "");
  el.title = STATE_LABEL[stateName] || stateName;
  el.setAttribute("aria-hidden", "true");
  return el;
}

function kindBadge(author, kind) {
  const el = span(`prop-kind ${kind === "human" ? "human" : "agent"}`, "");
  el.textContent = `${author} · ${kind === "human" ? "human" : "agent"}`;
  return el;
}

function num(value, digits) {
  if (value == null || Number.isNaN(Number(value))) return "—";
  return Number(value).toLocaleString("en-US", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function signed(value, digits) {
  if (value == null || Number.isNaN(Number(value))) return "—";
  const text = num(Math.abs(value), digits);
  return `${Number(value) < 0 ? "−" : "+"}${text}`;
}

function vec(values, isDelta) {
  if (!Array.isArray(values)) return "—";
  return values
    .map((v) => (isDelta ? signed(v, 2) : num(v, 2)))
    .join(", ");
}

function bboxSize(box) {
  if (!box || !box.min || !box.max) return "—";
  return box.max.map((hi, axis) => num(hi - box.min[axis], 2)).join(" × ");
}

function json(value) {
  if (value === undefined) return "—";
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

/** Proposal timestamps are zone-aware UTC server-side (``...Z``), so relTime
 *  can parse them directly. */
function ago(iso) {
  return iso ? relTime(iso) : "";
}

function errorText(err) {
  return err instanceof ApiError ? err.error.message : String(err);
}
