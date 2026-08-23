"""PRD-026 Workbench shell acceptance — AC1-AC7 + the `ui_open` agent surface.

One test (or a small, named group) per criterion, graded against the shipped
surface rather than a stub built for the occasion. Where a unit test already
proves the exact same claim (slices 1-5's own suites in
`tests/test_frontend_shell.py` / `tests/test_tools_ui.py`), this file
restates it *compactly* rather than duplicating the full case list — the
house rule from `tests/test_prd012_acceptance.py` / `tests/test_prd014_acceptance.py`.

| AC | Test |
|---|---|
| AC1 | `test_ac1_no_native_dialogs_remain`, `test_ac1_the_index_html_prd026_comment_is_gone` |
| AC2 | `test_ac2_check_interference_presence_follows_the_live_registry` |
| AC3 | `test_ac3_a_fixture_tool_reaches_the_palette_with_no_frontend_change` |
| AC4 | `test_ac4_layout_state_round_trips_and_workspace_keys_are_isolated` |
| AC5 | `test_ac5_a_conflicting_shortcut_registration_throws`, `test_ac5_f_mod_s_mod_z_are_registered_chords`, `test_ac5_every_registered_chord_is_in_the_user_guide_table` |
| AC6 | `test_ac6_dialog_markup_passes_the_static_a11y_pass`, `test_ac6_palette_and_menubar_markup_carry_their_aria_roles` |
| AC7 | `test_ac7_the_full_suite_count_is_cited` |
| agent surface | `test_ui_open_reaches_a_subscribed_queue_as_the_agent` (the `ui_open` tool → bus → a subscribed queue, Python-only, no WebSocket needed — the thing that makes AC1-AC6 a *human* palette and this one an *agent* surface over the same registry) |

Three things worth reading before you believe them:

* **AC1's grep is imported, not re-spelled.** `tests/test_frontend_shell.py`'s
  `NATIVE_DIALOG_RE` is the enforcement of the ban (four spellings of the
  global object, a lookbehind that excludes `dialogs.prompt(`, comment lines
  skipped) — a second, slightly different regex here would be a second
  ban with its own bugs. This file imports it and re-runs it, plus the one
  thing slice 2's own test does not check on its own: that the
  `index.html` comment which used to *promise* the ban ("PRD-026 … has not
  landed") is gone.
* **AC5's "every registered chord is documented" check is source-derived,
  not hand-typed.** It regexes `frontend/js/main.js` /
  `frontend/js/shell/layout.js` / `frontend/js/shell/palette.js` for every
  `chord:`/`shortcut:` string literal — the same three files that own every
  live binding plus the sketcher's two *declared* (non-bound) cheat-sheet
  rows — so a new binding lands in this test's scope automatically; only the
  chord → doc-text mapping (spelled `Cmd`/`Ctrl`, not the app's own `⌘`
  glyph) is maintained by hand, exactly like `ID_RE`/`BRANCH_RE`/`TAG_RE`'s
  one-spelling rule in `tests/test_frontend_shell.py`.
* **AC6 does not re-run slice 1/2's full a11y suite.** It grades one
  representative of each dialog *shape* (form, danger confirm, non-modal
  panel) plus the menu bar's and palette's ARIA roles, and says in a comment
  where the keyboard-only focus-trap/restore walkthrough (the behavioural
  half AC6 also asks for) is actually on the record: a real Playwright
  session against the installed Chrome, in slice 2 report §5 / §"Browser
  re-verification".
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest

from agentcad.core.tools import Tool, build_registry

from .conftest import make_test_service
from .test_frontend_shell import (
    FRONTEND_JS,
    INDEX,
    MAIN,
    NATIVE_DIALOG_RE,
    SHELL,
    _native_dialog_hits,
    _tools_payload,
    run_js,
    run_palette,
)

REPO = Path(__file__).resolve().parents[1]
CHANGELOG = REPO / "docs" / "changelog"
USER_GUIDE = REPO / "docs" / "user-guide.md"
LAYOUT_JS = SHELL / "layout.js"
PALETTE_JS = SHELL / "palette.js"
PRD_NAME = "PRD-026-workbench-shell.md"

_needs_node = pytest.mark.skipif(shutil.which("node") is None,
                                 reason="node is not installed")


def _find_prd() -> Path:
    """A PRD moves between `pending/in-progress/completed` at merge, so a
    hard-coded directory would go red for the whole review window — the
    `tests/test_prd012_acceptance.py._find_prd` precedent."""
    prd_root = REPO / "docs" / "prd"
    for stage in ("in-progress", "pending", "completed"):
        candidate = prd_root / stage / PRD_NAME
        if candidate.is_file():
            return candidate
    found = sorted(prd_root.rglob(PRD_NAME))
    assert found, f"{PRD_NAME} is not anywhere under {prd_root}"
    return found[0]


PRD = _find_prd()


# ============================================================ AC1

def test_ac1_no_native_dialogs_remain():
    """**AC1** — `grep -rn "window.prompt\\|window.confirm\\|window.alert"
    frontend/js` returns nothing. Re-runs the exact regex
    `tests/test_frontend_shell.py::test_no_native_dialogs_remain` enforces,
    imported rather than re-spelled (see the module docstring)."""
    files = sorted(FRONTEND_JS.rglob("*.js"))
    assert len(files) > 20, "the glob stopped finding the frontend"
    hits = [hit for path in files for hit in _native_dialog_hits(path)]
    assert hits == [], "native browser dialogs survived:\n" + "\n".join(hits)
    # The regex itself: every spelling of the global object is covered.
    assert NATIVE_DIALOG_RE.search("window.confirm(")
    assert NATIVE_DIALOG_RE.search("globalThis.alert(")
    assert not NATIVE_DIALOG_RE.search("dialogs.prompt(")


def test_ac1_the_index_html_prd026_comment_is_gone():
    """The promise slice 0's `index.html` carried ("PRD-026 … has not
    landed") is what AC1 actually replaces; a grep alone would stay green
    even if that stale comment survived."""
    index = INDEX.read_text(encoding="utf-8")
    assert "has not landed" not in index, \
        "the index.html promise that PRD-026 has not landed is still there"


# ============================================================ AC2

def test_ac2_check_interference_presence_follows_the_live_registry(kernel,
                                                                   tmp_path):
    """**AC2** — ⌘K's palette entry for `check_interference` is the live
    registry's answer: present when the tool is registered, absent when a
    registry response is built without it — never a frontend-side list.

    Compact restatement of
    `tests/test_frontend_shell.py::test_ac2_check_interference_is_in_the_palette_because_the_registry_has_it`
    (real server, real registry, real `GET /api/tools`).
    """
    payload = _tools_payload(kernel, tmp_path)
    names = {t["name"] for t in payload["tools"]}
    assert "check_interference" in names, "the fixture assumes the real pack"

    entries = run_palette(f"out(pm.entriesFromTools({json.dumps(payload)}));")
    assert "tool:check_interference" in {e["id"] for e in entries}

    filtered = {"tools": [t for t in payload["tools"]
                          if t["name"] != "check_interference"]}
    entries = run_palette(f"out(pm.entriesFromTools({json.dumps(filtered)}));")
    ids = {e["id"] for e in entries}
    assert "tool:check_interference" not in ids
    assert len(ids) == len(payload["tools"]) - 1


# ============================================================ AC3

def test_ac3_a_fixture_tool_reaches_the_palette_with_no_frontend_change(
        kernel, tmp_path):
    """**AC3** — a newly registered tool (a test fixture pack, registered
    into the app's own `ToolRegistry`, never referenced by any frontend
    file) appears in the palette with its name and description and is
    findable by a fuzzy query — the parity test. No frontend change was
    made to make this pass; the palette's only tool source is
    `GET /api/tools`.
    """
    payload = _tools_payload(kernel, tmp_path, extra=Tool(
        name="fixture_widget_ac3",
        description="A tool no pack ships — registered only by this test.",
        input_schema={"type": "object",
                      "properties": {"project": {"type": "string"}},
                      "required": ["project"]},
        handler=lambda project: {"ok": True},
    ))
    entries = run_palette(f"out(pm.entriesFromTools({json.dumps(payload)}));")
    by_id = {e["id"]: e for e in entries}
    assert "tool:fixture_widget_ac3" in by_id
    row = by_id["tool:fixture_widget_ac3"]
    assert row["title"] == "fixture_widget_ac3"
    assert row["description"] == \
        "A tool no pack ships — registered only by this test."

    ranked = run_palette(
        f"out(pm.rank('widget', pm.entriesFromTools({json.dumps(payload)}), [])"
        ".map((e) => e.id));")
    assert "tool:fixture_widget_ac3" in ranked


# ============================================================ AC4

@_needs_node
def test_ac4_layout_state_round_trips_and_workspace_keys_are_isolated():
    """**AC4** — panel state (size + collapsed) serializes, persists (the
    `localStorage` shape `layout.js` actually writes/reads through
    `serialize`/`deserialize`) and round-trips; two workspaces get two
    distinct storage keys, so switching workspaces (PRD-025) can never leak
    one's panel sizes into the other's.

    Model-level, per the brief — the drag/keyboard-nudge/reload *browser*
    half is on record as a slice 3/4 gap (`list_connected_browsers` → `[]`
    in both sessions) and is graded honestly in the PRD's Acceptance record
    rather than claimed here.
    """
    got = run_js("""
      const before = lm.toggle(lm.defaultState(), "sidebar");
      const persisted = JSON.stringify(lm.serialize(before));
      const restored = lm.deserialize(persisted, { height: 900 });
      out({
        roundTripCollapsed: restored.sidebar.collapsed === before.sidebar.collapsed,
        roundTripSize: restored.sidebar.size === before.sidebar.size,
        // A value clamp survives the round trip too: hand-edit the JSON to
        // something off-screen and deserialize must still land in range.
        clampedOnReadBack: lm.deserialize(
            JSON.stringify({ ...lm.serialize(lm.defaultState()),
                            inspector: { size: 99999, collapsed: false } }),
            { height: 900 }).inspector.size <= lm.LIMITS.inspector.max,
        keyDefault: lm.key("default"),
        keyOther: lm.key("test-workspace-42"),
      });
    """)
    assert got["roundTripCollapsed"] is True
    assert got["roundTripSize"] is True
    assert got["clampedOnReadBack"] is True
    assert got["keyDefault"] != got["keyOther"], \
        "two workspaces must not share one storage key"
    assert got["keyDefault"] == "agentcad.layout.default"
    assert got["keyOther"] == "agentcad.layout.test-workspace-42"


# ============================================================ AC5

@_needs_node
def test_ac5_a_conflicting_shortcut_registration_throws():
    """**AC5** — a second binding on a chord already bound in the same
    scope throws `ShortcutConflictError` (always, not only in dev — a
    conflict is a programming error caught at registration time)."""
    got = run_js("""
      const t = new sc.Table();
      t.bind({ chord: "Mod+S", id: "part.save-script" });
      let threw = false, name = null, msg = "";
      try {
        t.bind({ chord: "Mod+S", id: "some.other.action" });
      } catch (err) {
        threw = true; name = err.name; msg = err.message;
      }
      out({ threw, name, msg });
    """)
    assert got["threw"] is True
    assert got["name"] == "ShortcutConflictError"
    assert "Mod+S" in got["msg"]
    assert "part.save-script" in got["msg"]
    assert "some.other.action" in got["msg"]


def test_ac5_f_mod_s_mod_z_are_registered_chords():
    """**AC5** — the "?" cheat-sheet's live shortcut map includes
    F/Cmd+S/Cmd+Z (the PRD's own wording; the shell spells the modifier
    `Mod`, resolved to Cmd on macOS / Ctrl elsewhere at dispatch time)."""
    main = MAIN.read_text(encoding="utf-8")
    for chord in ('"F"', '"Mod+S"', '"Mod+Z"'):
        assert chord in main, f"{chord} is not registered in main.js"


#: Every raw chord `main.js`/`layout.js`/`palette.js` bind or declare, mapped
#: to the substring(s) that must appear inside the user guide's shortcut
#: table for it to count as documented. The doc spells modifiers `Cmd`/`Ctrl`
#: (not the app's own `⌘`/`⇧` glyphs), so this mapping — like
#: `ID_RE`/`BRANCH_RE`/`TAG_RE`'s one-spelling rule
#: (`tests/test_frontend_shell.py` M1) — is maintained by hand and is the
#: THING this test protects: if it drifts from what's shipped, the source
#: scan below catches it, not this table.
CHORD_TO_DOC_TEXT = {
    "F": ["**F**"],
    "G": ["**G**"],
    "R": ["**R**"],
    "Mod+Z": ["Cmd+Z", "Ctrl+Z"],
    "Mod+Y": ["Cmd+Y", "Ctrl+Y"],
    "Mod+Shift+Z": ["Shift+Cmd+Z", "Shift+Ctrl+Z"],
    "Mod+S": ["Cmd+S", "Ctrl+S"],
    "Mod+N": ["Cmd+N", "Ctrl+N"],
    "Mod+K": ["Cmd+K", "Ctrl+K"],
    # PRD-027 navigation: the dashboard and the sidebar filter box.
    "Mod+Shift+O": ["Cmd+Shift+O", "Ctrl+Shift+O"],
    "/": ["**/**"],
    "Mod+B": ["Cmd+B", "Ctrl+B"],
    "Shift+Mod+B": ["Shift+Cmd+B", "Ctrl+Shift+B"],
    "Mod+J": ["Cmd+J", "Ctrl+J"],
    "?": ["**?**"],
    # Declared-only (the sketcher's modal-mode rows, not live bindings) —
    # spec §6: "declared data, not live bindings."
    "Escape": ["Esc"],
    "Delete": ["**Delete**"],
}


def _registered_chords() -> set[str]:
    pattern = re.compile(r'(?:chord|shortcut):\s*"([^"]+)"')
    chords = set()
    for path in (MAIN, LAYOUT_JS, PALETTE_JS):
        chords |= set(pattern.findall(path.read_text(encoding="utf-8")))
    return chords


def test_ac5_every_registered_chord_is_in_the_user_guide_table():
    """**AC5 / design §6** — "the user guide's shortcut table is
    regenerated from the same data by hand in the docs slice and a test
    asserts every registered chord appears in it." The chord set is scanned
    from source (see `_registered_chords`), not hand-copied, so a shortcut
    added later is caught here automatically — only its *documented
    spelling* needs a matching entry in `CHORD_TO_DOC_TEXT` above.
    """
    chords = _registered_chords()
    assert chords, "the source scan found no chords — check the regex/paths"
    assert chords == set(CHORD_TO_DOC_TEXT), (
        "a shortcut was added/removed without updating CHORD_TO_DOC_TEXT: "
        f"in source but unmapped: {chords - set(CHORD_TO_DOC_TEXT)}; "
        f"mapped but not in source any more: {set(CHORD_TO_DOC_TEXT) - chords}")

    guide = USER_GUIDE.read_text(encoding="utf-8")
    table = guide.split("## Keyboard shortcuts", 1)[1].split("## Troubleshooting", 1)[0]
    for chord, needles in CHORD_TO_DOC_TEXT.items():
        for needle in needles:
            assert needle in table, \
                f"{chord!r} (expecting {needle!r}) is missing from the " \
                "Keyboard shortcuts table"


# ============================================================ AC6

@_needs_node
def test_ac6_dialog_markup_passes_the_static_a11y_pass():
    """**AC6** — the automated a11y check (design §8: role/aria-modal/
    aria-labelledby → an existing id, `<label for>` per field, text on
    every button) over one representative of each dialog *shape*: a
    schema-generated **form** (new-part-like), a danger **confirm**
    (delete-part-like) and a **non-modal** panel (the tool-result panel).

    The keyboard-only focus-trap/restore walkthrough this AC also asks for
    is a DOM behaviour `dialogs_model.markup` cannot express — it is on
    record as a real Playwright session against the installed Chrome in
    slice 2 report §5 / "Browser re-verification" (Tab cycling inside the
    trap, a danger dialog opening focused on Cancel, focus restored to the
    opener on close, Esc belonging to the topmost modal only).
    """
    got = run_js("""
      const form = dm.markup({
        uid: 1, view: "new-part", title: "New part",
        fields: [
          { name: "id", label: "Id", type: "text", required: true,
            pattern: "[a-z][a-z0-9_]{0,39}" },
          { name: "label", label: "Label", type: "text" },
        ],
        buttons: [{ id: "cancel", label: "Cancel" },
                  { id: "ok", label: "Create", kind: "primary",
                   submits: true }],
      });
      const confirm = dm.markup({
        uid: 2, view: "delete-part", title: 'Delete part "gusset"?',
        body: "Deletes gusset and its script file.",
        note: "Also removes 1 assembly instance: gusset_1",
        danger: true,
        buttons: [{ id: "cancel", label: "Cancel" },
                  { id: "ok", label: "Delete", kind: "danger",
                   submits: true }],
      });
      const nonmodal = dm.markup({
        uid: 3, view: "tool-result", title: "check_interference",
        modal: false, width: "wide", body: "{...}",
        buttons: [{ id: "close", label: "Close" }],
      });
      out({ form, confirm, nonmodal });
    """)

    def check(entry, *, expect_modal):
        html = entry["html"]
        assert 'role="dialog"' in html
        assert f'aria-modal="{"true" if expect_modal else "false"}"' in html
        title_id = re.search(r'aria-labelledby="([^"]+)"', html).group(1)
        assert f'id="{title_id}"' in html, "aria-labelledby points at no id"
        for fid in re.findall(r'<label for="([^"]+)"', html):
            assert re.search(rf'<(input|select|textarea)[^>]*id="{re.escape(fid)}"',
                             html), f"label for={fid!r} has no control"
        for inner in re.findall(r"<button[^>]*>(.*?)</button>", html, re.S):
            assert inner.strip(), "a button has no visible text"

    check(got["form"], expect_modal=True)
    assert 'id="dlg-f-1-id"' in got["form"]["html"]
    check(got["confirm"], expect_modal=True)
    assert "dlg danger" in got["confirm"]["html"]
    assert "Also removes 1 assembly instance" in got["confirm"]["html"]
    check(got["nonmodal"], expect_modal=False)
    assert "nonmodal" in got["nonmodal"]["html"]


@_needs_node
def test_ac6_palette_and_menubar_markup_carry_their_aria_roles():
    """**AC6**, the other two surfaces the design's static pass names
    (§8: "the menubar has role=menubar/menu/menuitem; the listbox has
    role=listbox/option").

    The menu bar's markup is a pure function (`menu_model.markup`), graded
    directly. The palette's combobox/listbox roles are assembled at runtime
    by `palette.js` (it is the ⌘K *dialog*'s body, not a second pure markup
    function), so those three roles are graded at the source — the same
    division `tests/test_frontend_shell.py`'s
    `test_index_html_hosts_the_menubar_right_after_the_brand` draws between
    a pure-model a11y pass and a source-level one for the static host.
    """
    tree = [{"menu": "file", "label": "File", "items": [
        {"id": "a", "title": "New project…", "shortcutLabel": "⌘N",
         "danger": False, "enabled": True, "separatorBefore": False},
        {"id": "b", "title": "Delete part…", "shortcutLabel": None,
         "danger": True, "enabled": False, "separatorBefore": True},
    ]}]
    got = run_js(f"out(mm.markup({json.dumps(tree)}));")
    assert 'role="menu"' in got and 'role="menuitem"' in got
    assert 'aria-haspopup="menu"' in got
    assert 'aria-disabled="true"' in got, "a disabled row must not vanish"
    for inner in re.findall(r"<button[^>]*>(.*?)</button>", got, re.S):
        assert inner.strip(), "a menu row has no visible text"

    palette = PALETTE_JS.read_text(encoding="utf-8")
    assert 'setAttribute("role", "combobox")' in palette
    assert 'setAttribute("role", "listbox")' in palette
    assert 'setAttribute("role", "option")' in palette
    assert 'aria-activedescendant' in palette


# ============================================================ AC7

def test_ac7_the_full_suite_count_is_cited():
    """**AC7** — "full suite green" is a claim about a *run*; the evidence
    is a `make test` count on the record in the newest changelog entry (the
    PRD-004 AC10 / PRD-011 AC8 / PRD-012 AC8 precedent). Recomputing the
    number here would mean running the full suite from inside itself, and
    `--collect-only` counts cases, not what `make test` reports.
    """
    latest = max(CHANGELOG.glob("0[0-9][0-9][0-9]-*.md"))
    text = latest.read_text(encoding="utf-8")
    assert "make test" in text, \
        f"{latest.name} is the newest changelog entry and cites no `make test`"
    assert re.search(r"\b\d{3,6}\s+passed\b", text.replace(",", "")), \
        f"{latest.name} does not cite a `make test` suite count"


# ============================================================ agent surface

def test_ui_open_reaches_a_subscribed_queue_as_the_agent(kernel, tmp_path):
    """The AC that proves the agent surface: `ui_open` is a **tool call**
    (the same registry AC2/AC3 prove the palette mirrors), and it reaches a
    connected browser as an ordinary bus event — Python-only, no WebSocket
    required, because `EventBus.subscribe()` IS what `/ws` hands each
    client.

    Full case coverage (delivered_to 0/1/2, the rate limit, every refusal
    class) is `tests/test_tools_ui.py`; this is the end-to-end shape the
    PRD's "Agent surface" section describes: "put a view in front of the
    human instead of describing where to click."
    """
    from agentcad.core import tools_ui

    tools_ui._reset_bucket()
    service = make_test_service(tmp_path / "projects", kernel)
    registry = build_registry(service)

    queue = service.bus.subscribe()
    result = registry.call("ui_open", {"view": "part-settings",
                                       "args": {"part_id": "plate"}})
    assert result["ok"] is True
    assert result["delivered_to"] == 1

    event = queue.get_nowait()
    assert event == {"type": "ui_open", "view": "part-settings",
                     "args": {"part_id": "plate"}, "by": "agent"}

    # An unknown view refuses (a `ValidationError` subclass) and publishes
    # nothing — an agent cannot make the shell open a view that was never
    # registered, which is what keeps `ui_open` from being a second,
    # ungoverned surface.
    refused = registry.call("ui_open", {"view": "Not A Valid View"})
    assert refused["error"]["type"] == "validation_error"
    assert queue.empty()
    tools_ui._reset_bucket()


# ==================================================== the record itself

def test_the_roadmap_link_resolves_to_the_prd_where_it_actually_lives():
    """The house meta-test: the roadmap's row for this PRD links to the
    folder the PRD is actually in."""
    roadmap = (REPO / "docs" / "roadmap.md").read_text(encoding="utf-8")
    row = next(line for line in roadmap.splitlines()
               if line.startswith("| [026]"))
    match = re.search(r"\((prd/[^)]+\.md)\)", row)
    assert match, f"the roadmap row for PRD-026 carries no link: {row}"
    assert (REPO / "docs" / match.group(1)).is_file(), \
        f"the roadmap link {match.group(1)} does not resolve"
    assert (REPO / "docs" / match.group(1)) == PRD, \
        f"the roadmap points at {match.group(1)} but the PRD is at {PRD}"


def test_the_prd_status_and_acceptance_record_are_on_the_page():
    """The status line and the AC1-AC7 evidence table this slice adds must
    actually be in the PRD file, not just in this test's imagination."""
    text = PRD.read_text(encoding="utf-8")
    # The status line moves with the PRD's lifecycle ("in progress —
    # acceptance" while the branch is open, "completed — merged in PR #29"
    # after the close-out); what this guard pins is that a status line
    # exists and the acceptance evidence stayed on the page.
    assert "**Status:**" in text
    assert ("in progress — acceptance" in text
            or "completed — merged in PR #29" in text)
    assert "Acceptance record" in text
    assert "Shipped vs. deferred" in text
    for ac in ("AC1", "AC2", "AC3", "AC4", "AC5", "AC6", "AC7"):
        assert ac in text
    # FR2's corrected count (slice 2 report §1).
    assert "21 sites" in text
