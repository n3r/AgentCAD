"""PRD-026 Workbench shell — slice 1 (actions, shortcuts, dialogs, toast) in node.

The shell's pure models (`shell/actions.js`, `shell/shortcuts_model.js`,
`shell/dialogs_model.js`) carry the rules a screenshot cannot grade: a duplicate
action id throws, a chord normalises one way and one way only, a second binding
on the same chord throws naming BOTH ids, and the dialog markup satisfies the
static accessibility pass (role/aria-modal/aria-labelledby → an id that exists,
a `<label for>` per field, text on every button, `aria-invalid` +
`aria-describedby` on an errored field).

They are pure ES modules with no DOM access, so node runs them exactly as the
browser does — the `tests/test_frontend_tree.py` harness, widened to run a
snippet instead of one fixed call. The DOM modules (`dialogs.js`,
`shortcuts.js`, `toast.js`) are *imported* here too: the global constraint is
that every shell module is node-importable, and an accidental top-level
`document` reference is exactly the regression this catches.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

SHELL = Path(__file__).resolve().parents[1] / "frontend" / "js" / "shell"

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is not installed")


def _uri(name: str) -> str:
    return json.dumps((SHELL / name).as_uri())


def _juri(name: str) -> str:
    """A `frontend/js/<name>` module URL, resolved the way node resolves the
    shell's own `../state.js` — a different spelling would be a SECOND module
    instance and the store's subscriptions would not fire."""
    return json.dumps((SHELL.parent / name).resolve().as_uri())


PRELUDE = f"""
import * as actions from {_uri("actions.js")};
import * as sc from {_uri("shortcuts_model.js")};
import * as dm from {_uri("dialogs_model.js")};
import * as mm from {_uri("menu_model.js")};
import * as lm from {_uri("layout_model.js")};
const out = (v) => process.stdout.write(JSON.stringify(v));
"""


def run_js(body: str, prelude: str = PRELUDE) -> object:
    """Run `body` in node with the shell models imported; return its JSON."""
    proc = subprocess.run(["node", "--input-type=module", "--eval",
                           prelude + body],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


# ------------------------------------------------------------ node-importable

def test_every_shell_module_imports_in_node_and_exports_its_contract():
    """The interface later slices are written against, proved by import.

    A top-level `document`/`window` reference in any of the six modules fails
    this — which is the whole point of the pure/DOM split.
    """
    prelude = "".join(
        f'import * as {alias} from {_uri(mod)};\n'
        for alias, mod in [("actions", "actions.js"),
                           ("scm", "shortcuts_model.js"),
                           ("shortcuts", "shortcuts.js"),
                           ("dm", "dialogs_model.js"),
                           ("dialogs", "dialogs.js"),
                           ("toast", "toast.js"),
                           ("mm", "menu_model.js"),
                           ("menu", "menu.js"),
                           ("lm", "layout_model.js"),
                           ("layout", "layout.js")]
    ) + "const out = (v) => process.stdout.write(JSON.stringify(v));\n"
    got = run_js(
        "out({actions: Object.keys(actions), scm: Object.keys(scm),"
        " shortcuts: Object.keys(shortcuts), dm: Object.keys(dm),"
        " dialogs: Object.keys(dialogs), toast: Object.keys(toast),"
        " mm: Object.keys(mm), menu: Object.keys(menu),"
        " lm: Object.keys(lm), layout: Object.keys(layout)});",
        prelude=prelude)
    for name in ("register", "get", "list", "run", "context", "onChange"):
        assert name in got["actions"], f"actions.{name} is missing"
    for name in ("normalize", "fromEvent", "label", "Table",
                 "ShortcutConflictError"):
        assert name in got["scm"], f"shortcuts_model.{name} is missing"
    for name in ("init", "list", "declare"):
        assert name in got["shortcuts"], f"shortcuts.{name} is missing"
    for name in ("markup", "validate", "focusables"):
        assert name in got["dm"], f"dialogs_model.{name} is missing"
    for name in ("init", "open", "confirm", "prompt", "form", "close",
                 "setError", "isModalOpen", "register", "views", "openView",
                 "attachLegacy", "setEmitter", "stack"):
        assert name in got["dialogs"], f"dialogs.{name} is missing"
    for name in ("init", "toast", "dismiss"):
        assert name in got["toast"], f"toast.{name} is missing"
    for name in ("tree", "markup"):
        assert name in got["mm"], f"menu_model.{name} is missing"
    for name in ("init", "attach"):
        assert name in got["menu"], f"menu.{name} is missing"
    for name in ("LIMITS", "clamp", "serialize", "deserialize", "toggle",
                 "responsiveDefaults", "key"):
        assert name in got["lm"], f"layout_model.{name} is missing"
    for name in ("init", "toggle"):
        assert name in got["layout"], f"layout.{name} is missing"


# ------------------------------------------------------------------- actions

def test_a_duplicate_action_id_throws_at_registration():
    got = run_js("""
      actions.register({id: "part.new", title: "New part…", run() {}});
      let msg = null;
      try { actions.register({id: "part.new", title: "again", run() {}}); }
      catch (err) { msg = err.message; }
      out({msg});
    """)
    assert got["msg"] == "duplicate action: part.new"


def test_list_drops_when_false_entries_and_reports_enabled_for_menus():
    """`when` decides presence, `enabled` decides actionability.

    The menu bar (slice 4) renders a disabled row rather than a vanishing one,
    so the two questions cannot be the same predicate.
    """
    got = run_js("""
      actions.register({id: "a.always", title: "A", run: () => 1});
      actions.register({id: "b.project", title: "B", run: () => 2,
                        when: (c) => !!c.projectName});
      actions.register({id: "c.part", title: "C", run: () => 3,
                        enabled: (c) => !!c.selectedPart});
      const none = actions.list({}).map((e) => [e.id, e.enabled]);
      const some = actions.list({projectName: "p"}).map((e) => [e.id, e.enabled]);
      out({none, some});
    """)
    assert got["none"] == [["a.always", True], ["c.part", False]]
    assert got["some"] == [["a.always", True], ["b.project", True],
                           ["c.part", False]]


def test_run_passes_the_context_and_the_source_and_refuses_an_unknown_id():
    got = run_js("""
      let seen = null;
      actions.register({id: "x.go", title: "Go", run: (ctx) => { seen = ctx; return "ran"; }});
      const runs = [];
      actions.onRun((info) => runs.push([info.id, info.source]));
      const value = actions.run("x.go", {projectName: "demo"}, {source: "palette"});
      let msg = null;
      try { actions.run("nope"); } catch (err) { msg = err.message; }
      out({value, seen, runs, msg});
    """)
    assert got["value"] == "ran"
    assert got["seen"] == {"projectName": "demo"}
    assert got["runs"] == [["x.go", "palette"]]
    assert got["msg"] == "unknown action: nope"


def test_onchange_fires_on_register_so_menus_and_shortcuts_can_rebind():
    got = run_js("""
      const seen = [];
      const off = actions.onChange((id) => seen.push(id));
      actions.register({id: "one", title: "1", run() {}});
      off();
      actions.register({id: "two", title: "2", run() {}});
      out({seen});
    """)
    assert got["seen"] == ["one"]


# ----------------------------------------------------------- shortcuts model

@pytest.mark.parametrize("raw,want", [
    ("mod+shift+k", "Mod+Shift+K"),
    ("shift+mod+z", "Mod+Shift+Z"),
    ("Mod+S", "Mod+S"),
    ("cmd+k", "Mod+K"),
    ("ctrl+alt+shift+mod+a", "Mod+Ctrl+Alt+Shift+A"),
    ("f", "F"),
    ("escape", "Escape"),
    ("arrowup", "ArrowUp"),
    ("f1", "F1"),
    ("?", "?"),
    ("mod+enter", "Mod+Enter"),
    # The separator is also a key, and both spellings survive the round trip.
    ("+", "+"),
    ("mod++", "Mod++"),
    ("shift++", "Shift++"),
])
def test_normalize_is_canonical(raw, want):
    got = run_js("out({chord: sc.normalize(%s)});" % json.dumps(raw))
    assert got["chord"] == want


def test_normalize_refuses_an_empty_chord():
    got = run_js("""
      let msg = null;
      try { sc.normalize("  "); } catch (err) { msg = err.message; }
      out({msg});
    """)
    assert "empty" in (got["msg"] or "")


@pytest.mark.parametrize("event,platform,want", [
    ({"key": "k", "metaKey": True}, "MacIntel", "Mod+K"),
    ({"key": "k", "ctrlKey": True}, "MacIntel", "Ctrl+K"),
    ({"key": "k", "ctrlKey": True}, "Linux x86_64", "Mod+K"),
    ({"key": "z", "metaKey": True, "shiftKey": True}, "MacIntel", "Mod+Shift+Z"),
    # `?` is Shift+/ on a US layout: the browser reports the character, and the
    # chord is the character — never `Shift+?`.
    ({"key": "?", "shiftKey": True}, "MacIntel", "?"),
    ({"key": "f"}, "MacIntel", "F"),
    ({"key": "Escape"}, "Win32", "Escape"),
    ({"key": "ArrowUp", "shiftKey": True}, "Win32", "Shift+ArrowUp"),
    # A bare modifier press is not a chord.
    ({"key": "Shift", "shiftKey": True}, "MacIntel", None),
    ({"key": "Meta", "metaKey": True}, "MacIntel", None),
    ({"key": "AltGraph", "altKey": True}, "Linux x86_64", None),
    # `+` is the chord separator AND an ordinary key. Round-tripping the chord
    # through a parser that splits on "+" made every `+` typed in the editor —
    # and every ⌘+ browser zoom — throw out of the document keydown listener.
    ({"key": "+"}, "MacIntel", "+"),
    ({"key": "+", "shiftKey": True}, "MacIntel", "+"),
    ({"key": "+", "metaKey": True}, "MacIntel", "Mod++"),
    ({"key": "+", "ctrlKey": True}, "Linux x86_64", "Mod++"),
    ({"key": "=", "metaKey": True}, "MacIntel", "Mod+="),
])
def test_from_event_maps_mod_per_platform(event, platform, want):
    got = run_js("out({chord: sc.fromEvent(%s, %s)});"
                 % (json.dumps(event), json.dumps(platform)))
    assert got["chord"] == want


@pytest.mark.parametrize("chord,platform,want", [
    ("Mod+K", "MacIntel", "⌘K"),
    ("Mod+Shift+B", "MacIntel", "⇧⌘B"),
    ("Mod+K", "Win32", "Ctrl+K"),
    ("Mod+Shift+B", "Win32", "Ctrl+Shift+B"),
    ("F", "MacIntel", "F"),
    ("?", "Win32", "?"),
    ("Escape", "Win32", "Esc"),
    ("Mod++", "MacIntel", "⌘+"),
    ("Mod++", "Win32", "Ctrl++"),
])
def test_label_reads_like_the_platform(chord, platform, want):
    got = run_js("out({label: sc.label(%s, %s)});"
                 % (json.dumps(chord), json.dumps(platform)))
    assert got["label"] == want


def test_a_second_binding_on_one_chord_throws_naming_both_ids():
    """AC5. Registration is static code, so a conflict is a programming error
    and it is loud in every build, not only in dev."""
    got = run_js("""
      const t = new sc.Table();
      t.bind({chord: "Mod+S", id: "part.save-script"});
      let name = null, msg = null;
      try { t.bind({chord: "mod+s", id: "sheet.save"}); }
      catch (err) { name = err.name; msg = err.message; }
      out({name, msg, n: t.list().length});
    """)
    assert got["name"] == "ShortcutConflictError"
    assert "part.save-script" in got["msg"] and "sheet.save" in got["msg"]
    assert "Mod+S" in got["msg"]
    assert got["n"] == 1


def test_the_same_chord_in_a_different_scope_is_not_a_conflict():
    got = run_js("""
      const t = new sc.Table();
      t.bind({chord: "Escape", id: "a"});
      t.bind({chord: "Escape", id: "b", scope: "modal-safe"});
      out({global: t.lookup("Escape").id,
           modal: t.lookup("Escape", "modal-safe").id,
           miss: t.lookup("Mod+Q"),
           list: t.list().map((e) => [e.chord, e.id, e.scope])});
    """)
    assert got["global"] == "a" and got["modal"] == "b"
    assert got["miss"] is None
    assert got["list"] == [["Escape", "a", "global"],
                           ["Escape", "b", "modal-safe"]]


# --------------------------------------------------------- shortcut dispatch

# The three dispatch rules are the highest-risk logic in the slice — each one
# is a v0.1 behaviour that must survive the move out of `setupKeys`. They are
# reachable in node because the handler duck-types its target and every DOM
# read in the modules it calls is guarded.
DISPATCH = f"""
import * as actions from {_uri("actions.js")};
import * as shortcuts from {_uri("shortcuts.js")};
const out = (v) => process.stdout.write(JSON.stringify(v));
const ran = [];
let prevented = 0;
const fakeTarget = (sel) => ({{ closest: (q) => (q.split(", ").some(
  (s) => sel.includes(s.trim())) ? {{}} : null) }});
const press = (chord, opts = {{}}) => {{
  shortcuts.__shortcutsDispatch__.onKeyDown({{
    key: chord.key, metaKey: !!chord.metaKey, ctrlKey: !!chord.ctrlKey,
    shiftKey: !!chord.shiftKey, altKey: !!chord.altKey,
    target: opts.in ? fakeTarget(opts.in) : null,
    preventDefault: () => {{ prevented += 1; }},
  }});
}};
"""


def test_a_bare_key_acts_outside_a_field_and_types_inside_one():
    got = run_js("""
      shortcuts.init({actions, dialogs: {isModalOpen: () => false},
                      platform: "MacIntel"});
      actions.register({id: "view.fit", title: "Fit", shortcut: "F",
                        run: () => ran.push("fit")});
      press({key: "f"});
      const outside = [ran.length, prevented];
      press({key: "f"}, {in: "input"});
      press({key: "f"}, {in: ".CodeMirror"});
      out({outside, ran: ran.length, prevented});
    """, prelude=DISPATCH)
    assert got["outside"] == [1, 1]
    # In a field the key types: it neither ran the action nor ate the keystroke.
    assert got["ran"] == 1 and got["prevented"] == 1


def test_an_ordinary_plus_keystroke_does_not_blow_up_the_listener():
    """C1's real symptom: `fromEvent` is the FIRST statement of an untried
    document listener, so a chord it cannot build is an uncaught error on every
    `+` typed into the Python editor and every ⌘+ browser zoom."""
    got = run_js("""
      shortcuts.init({actions, dialogs: {isModalOpen: () => false},
                      platform: "MacIntel"});
      actions.register({id: "view.fit", title: "Fit", shortcut: "F",
                        run: () => ran.push("fit")});
      const errors = [];
      for (const ev of [{key: "+"}, {key: "+", shiftKey: true},
                        {key: "+", metaKey: true}, {key: "=", metaKey: true},
                        {key: "AltGraph", altKey: true}]) {
        try { press(ev); } catch (err) { errors.push(err.message); }
      }
      out({errors, ran, prevented});
    """, prelude=DISPATCH)
    assert got["errors"] == []
    assert got["ran"] == [] and got["prevented"] == 0


def test_a_plus_chord_can_actually_be_bound_and_dispatched():
    """…and the fix is not "swallow the error": `+` is a bindable chord."""
    got = run_js("""
      shortcuts.init({actions, dialogs: {isModalOpen: () => false},
                      platform: "MacIntel"});
      actions.register({id: "view.zoom-in", title: "Zoom in", shortcut: "Mod++",
                        run: () => ran.push("zoom")});
      press({key: "+", metaKey: true});
      out({ran, prevented, list: shortcuts.list().map((r) => [r.chord, r.label])});
    """, prelude=DISPATCH)
    assert got["ran"] == ["zoom"] and got["prevented"] == 1
    assert got["list"] == [["Mod++", "⌘+"]]


def test_mod_s_defers_to_codemirror_and_fires_everywhere_else():
    """`main.js`'s one hand-written exception, migrated as a binding predicate:
    the editor binds its own Cmd+S, so ours must not eat the keystroke."""
    got = run_js("""
      shortcuts.init({actions, dialogs: {isModalOpen: () => false},
                      platform: "MacIntel"});
      actions.register({id: "part.save-script", title: "Save",
                        shortcut: {chord: "Mod+S", when: (c) => !c.inCodeMirror},
                        run: () => ran.push("save")});
      press({key: "s", metaKey: true}, {in: ".CodeMirror"});
      const deferred = [ran.length, prevented];
      press({key: "s", metaKey: true}, {in: "input"});
      press({key: "s", metaKey: true});
      out({deferred, ran, prevented});
    """, prelude=DISPATCH)
    assert got["deferred"] == [0, 0], "Cmd+S was stolen from CodeMirror"
    # A modifier chord still fires in an ordinary text input.
    assert got["ran"] == ["save", "save"] and got["prevented"] == 2


def test_undo_declines_inside_a_field_so_native_text_undo_survives():
    got = run_js("""
      shortcuts.init({actions, dialogs: {isModalOpen: () => false},
                      platform: "MacIntel"});
      actions.register({id: "edit.undo", title: "Undo",
                        shortcut: {chord: "Mod+Z", when: (c) => !c.inField},
                        run: () => ran.push("undo")});
      press({key: "z", metaKey: true}, {in: "textarea"});
      const inField = [ran.length, prevented];
      press({key: "z", metaKey: true});
      out({inField, ran: ran.length, prevented});
    """, prelude=DISPATCH)
    assert got["inField"] == [0, 0]
    assert got["ran"] == 1 and got["prevented"] == 1


def test_an_open_modal_silences_every_binding_that_is_not_modal_safe():
    got = run_js("""
      shortcuts.init({actions, dialogs: {isModalOpen: () => true},
                      platform: "MacIntel"});
      actions.register({id: "view.fit", title: "Fit", shortcut: "F",
                        run: () => ran.push("fit")});
      actions.register({id: "help.close", title: "Close",
                        shortcut: {chord: "G", scope: "modal-safe"},
                        run: () => ran.push("close")});
      press({key: "f"});
      press({key: "g"});
      out({ran, prevented});
    """, prelude=DISPATCH)
    assert got["ran"] == ["close"]
    assert got["prevented"] == 1


def test_enabled_false_still_swallows_the_keystroke_unlike_when_false():
    """The distinction ⌘S depends on.

    `when` is presence and is consulted BEFORE `preventDefault`, so a
    `when`-gated ⌘S with no part selected would fall through to the browser's
    "Save page as…" — which v0.1 always suppressed outside CodeMirror.
    `enabled` greys the menu row without giving the keystroke away.
    """
    got = run_js("""
      shortcuts.init({actions, dialogs: {isModalOpen: () => false},
                      platform: "MacIntel"});
      actions.register({id: "part.save-script", title: "Save",
                        shortcut: {chord: "Mod+S", when: (c) => !c.inCodeMirror},
                        enabled: (c) => !!c.selectedPart,
                        run: () => ran.push("save")});
      press({key: "s", metaKey: true});
      out({ran, prevented,
           enabled: actions.list({}).map((e) => e.enabled)});
    """, prelude=DISPATCH)
    assert got["prevented"] == 1, "the browser Save-page dialog would have opened"
    assert got["ran"] == ["save"]     # the run body carries its own guard
    assert got["enabled"] == [False]  # …and the menu still greys the row


def test_a_when_false_action_leaves_the_keystroke_to_the_browser():
    """`G`/`R` did nothing without an editable instance selected — and, just as
    importantly, did not swallow the key."""
    got = run_js("""
      shortcuts.init({actions, dialogs: {isModalOpen: () => false},
                      platform: "MacIntel"});
      actions.register({id: "view.gizmo.translate", title: "Move", shortcut: "G",
                        when: (c) => !!c.selectedInstance,
                        run: () => ran.push("gizmo")});
      press({key: "g"});
      out({ran, prevented});
    """, prelude=DISPATCH)
    assert got["ran"] == [] and got["prevented"] == 0


def test_the_cheat_sheet_lists_bound_and_declared_rows_with_platform_labels():
    got = run_js("""
      shortcuts.init({actions, dialogs: {isModalOpen: () => false},
                      platform: "MacIntel"});
      actions.register({id: "part.save-script", title: "Save & rebuild",
                        group: "Parts", shortcut: "Mod+S", run() {}});
      shortcuts.declare({chord: "Escape", title: "Close the sketch",
                         group: "While sketching"});
      out(shortcuts.list());
    """, prelude=DISPATCH)
    assert got[0] == {"chord": "Mod+S", "label": "⌘S",
                      "actionId": "part.save-script", "title": "Save & rebuild",
                      "group": "Parts", "scope": "global"}
    assert got[1]["group"] == "While sketching" and got[1]["actionId"] is None
    assert got[1]["declaredOnly"] is True


# ------------------------------------------------------------- dialogs model

SPEC = """{
  uid: 7, view: "new-part", title: "New part", danger: false,
  body: "Create a part in <demo>",
  fields: [
    {name: "id", label: "Part id", type: "text", required: true,
     pattern: "^[a-z][a-z0-9_]{0,39}$", help: "lower snake case"},
    {name: "count", label: "Count", type: "number", value: 2, min: 1, max: 9},
    {name: "kind", label: "Kind", type: "select",
     options: ["script", {value: "ref", label: "Reference"}]},
    {name: "hidden", label: "Hidden", type: "checkbox"},
    {name: "notes", label: "Notes", type: "textarea"},
    {name: "extra", label: "Extra", type: "json"}
  ]
}"""


def _markup(spec: str = SPEC, extra: str = "") -> dict:
    return run_js(f"const m = dm.markup({spec});{extra} out(m);")


def test_markup_passes_the_static_a11y_pass():
    """AC6's static half — the contract the browser then has to honour."""
    got = _markup()
    html = got["html"]
    assert 'role="dialog"' in html and 'aria-modal="true"' in html
    title_id = re.search(r'aria-labelledby="([^"]+)"', html).group(1)
    assert f'id="{title_id}"' in html, "aria-labelledby points at no element"
    body_id = re.search(r'aria-describedby="([^"]+)"', html).group(1)
    assert f'id="{body_id}"' in html
    # Every field carries a <label for> naming a control that exists.
    labelled = re.findall(r'<label for="([^"]+)"', html)
    assert len(labelled) == 6, labelled
    for fid in labelled:
        assert re.search(rf'<(input|select|textarea)[^>]*id="{re.escape(fid)}"',
                         html), f"no control with id {fid}"
    # Every button has text.
    for inner in re.findall(r"<button[^>]*>(.*?)</button>", html, re.S):
        assert inner.strip(), "a button in the dialog has no text"
    # …and the returned ids are what dialogs.js binds to.
    assert got["ids"]["title"] == title_id
    assert set(got["ids"]["fields"]) == {"id", "count", "kind", "hidden",
                                         "notes", "extra"}
    assert got["ids"]["fields"]["id"]["error"] in html


def test_markup_escapes_everything_it_interpolates():
    got = run_js("""
      out(dm.markup({uid: 1, view: "x", title: "<img src=x onerror=1>",
                     body: "a & b",
                     fields: [{name: "n", label: "</label><script>", type: "text",
                               value: '" onfocus="evil()'}]}));
    """)
    html = got["html"]
    assert "<img" not in html and "<script>" not in html
    assert "&lt;img" in html and "&amp;" in html
    assert 'onfocus="evil()' not in html


def test_markup_marks_the_danger_variant_and_the_agent_attribution():
    got = run_js("""
      out({danger: dm.markup({uid: 2, view: "delete-part", title: "Delete",
                              danger: true,
                              buttons: [{id: "cancel", label: "Cancel"},
                                        {id: "delete", label: "Delete", kind: "danger",
                                         submits: true}]}).html,
           agent: dm.markup({uid: 3, view: "x", title: "X",
                             attribution: "opened by agent"}).html,
           plain: dm.markup({uid: 4, view: "x", title: "X"}).html});
    """)
    assert "dlg danger" in got["danger"] or 'class="dlg danger' in got["danger"]
    assert "dlg-btn danger" in got["danger"]
    assert 'class="dlg-attribution"' in got["agent"]
    assert "opened by agent" in got["agent"]
    assert "dlg-attribution" not in got["plain"]


def test_markup_defaults_to_cancel_and_ok():
    got = run_js("""
      const m = dm.markup({uid: 5, view: "x", title: "X"});
      out({labels: [...m.html.matchAll(/<button[^>]*>(.*?)<\\/button>/g)].map((x) => x[1]),
           ids: Object.keys(m.ids.buttons)});
    """)
    assert got["labels"] == ["Cancel", "OK"]
    assert got["ids"] == ["cancel", "ok"]


def test_a_node_body_gets_an_empty_container_that_aria_describedby_names():
    """The "?" cheat-sheet passes a DOM node, not text: the markup must leave a
    block-legal hole for it and still point `aria-describedby` at it."""
    got = run_js("""
      out(dm.markup({uid: 9, view: "shortcuts", title: "Keyboard shortcuts",
                     bodyNode: true}));
    """)
    body_id = got["ids"]["body"]
    assert f'<div class="dlg-text" id="{body_id}"></div>' in got["html"]
    assert f'aria-describedby="{body_id}"' in got["html"]


def test_markup_ids_are_unique_between_dialogs():
    got = run_js("""
      out({a: dm.markup({view: "x", title: "X"}).ids.title,
           b: dm.markup({view: "x", title: "X"}).ids.title});
    """)
    assert got["a"] != got["b"]


def test_an_errored_field_is_announced_not_only_coloured():
    got = run_js("""
      out(dm.markup({uid: 6, view: "x", title: "X",
                     fields: [{name: "id", label: "Id", type: "text"}],
                     errors: {id: "Required"}}));
    """)
    html = got["html"]
    assert 'aria-invalid="true"' in html
    err_id = got["ids"]["fields"]["id"]["error"]
    assert f'id="{err_id}"' in html
    described = re.search(r'<input[^>]*aria-describedby="([^"]*)"', html).group(1)
    assert err_id in described.split()
    assert "Required" in html


@pytest.mark.parametrize("values,want", [
    ({"id": "", "n": 3}, {"id": "Required"}),
    ({"id": "Nope", "n": 3}, {"id": "Invalid format"}),
    # The fixture pattern below is written HTML-`pattern`-shaped (UNanchored) —
    # the shape spec §1.4 hands slice 2. `RegExp.test` is a substring match, so
    # unanchored this string would have been accepted as a valid part id.
    ({"id": "Nope ok_1", "n": 3}, {"id": "Invalid format"}),
    ({"id": "ok_1 tail!", "n": 3}, {"id": "Invalid format"}),
    ({"id": "ok_1", "n": 0}, {"n": "Must be at least 1"}),
    ({"id": "ok_1", "n": 99}, {"n": "Must be at most 10"}),
    ({"id": "ok_1", "n": "abc"}, {"n": "Must be a number"}),
    ({"id": "ok_1", "n": 3}, {}),
])
def test_validate_applies_required_pattern_and_range(values, want):
    got = run_js("""
      const fields = [
        {name: "id", label: "Id", type: "text", required: true,
         pattern: "[a-z][a-z0-9_]{0,39}"},
        {name: "n", label: "N", type: "number", min: 1, max: 10}
      ];
      out(dm.validate(fields, %s));
    """ % json.dumps(values))
    assert got["errors"] == want
    assert got["valid"] is (want == {})


def test_validate_parses_json_fields_and_runs_custom_validators():
    got = run_js("""
      const fields = [
        {name: "j", label: "J", type: "json"},
        {name: "k", label: "K", type: "text",
         validate: (v, all) => (v === all.j ? "must differ" : null)}
      ];
      out({bad: dm.validate(fields, {j: "{not json", k: "x"}),
           custom: dm.validate(fields, {j: "same", k: "same"}),
           good: dm.validate(fields, {j: '{"a": 1}', k: "x"})});
    """)
    assert "j" in got["bad"]["errors"] and "JSON" in got["bad"]["errors"]["j"]
    assert got["custom"]["errors"]["k"] == "must differ"
    assert got["good"]["valid"] is True


def test_a_required_checkbox_must_actually_be_checked():
    got = run_js("""
      const fields = [{name: "ack", label: "Ack", type: "checkbox", required: true}];
      out({off: dm.validate(fields, {ack: false}).errors,
           on: dm.validate(fields, {ack: true}).errors});
    """)
    assert got["off"] == {"ack": "Required"}
    assert got["on"] == {}


@pytest.mark.parametrize("current,backwards,want", [
    (0, False, 1), (2, False, 0), (0, True, 2), (2, True, 1), (-1, False, 0),
    (-1, True, 2),
])
def test_focusables_wraps_in_both_directions(current, backwards, want):
    got = run_js("out({i: dm.focusables(['a','b','c'], %d, %s)});"
                 % (current, "true" if backwards else "false"))
    assert got["i"] == want


def test_focusables_on_an_empty_trap_is_minus_one():
    got = run_js("out({i: dm.focusables([], 0, false)});")
    assert got["i"] == -1


# ------------------------------------------------------------------- toasts

# The two things `toast()` was widened for beyond the v0.1 four-liner: an `id`
# that REPLACES rather than stacks, and one action button.
TOASTS = f"""
const made = [];
const node = (tag) => {{
  const el = {{
    tag, className: "", dataset: {{}}, attrs: {{}}, children: [], text: "",
    type: "", parent: null, listeners: {{}},
    set textContent(v) {{ this.text = v; }},
    get textContent() {{ return this.text; }},
    setAttribute(k, v) {{ this.attrs[k] = v; }},
    appendChild(child) {{ child.parent = this; this.children.push(child); }},
    addEventListener(name, fn) {{ this.listeners[name] = fn; }},
    remove() {{
      if (!this.parent) return;
      this.parent.children = this.parent.children.filter((c) => c !== this);
      this.parent = null;
    }},
  }};
  made.push(el);
  return el;
}};
const host = node("div");
globalThis.document = {{ createElement: node, getElementById: () => host }};
// Timers are RECORDED, not run: asserting the delay the module asked for is
// both better coverage than waiting 8 s for it and the difference between a
// 4 s test file and a 12 s one.
const timers = [];
globalThis.setTimeout = (fn, ms) => {{ timers.push(ms); return timers.length; }};
globalThis.clearTimeout = () => {{}};
const toasts = await import({_uri("toast.js")});
const out = (v) => process.stdout.write(JSON.stringify(v));
const shown = () => host.children.map(
  (c) => [c.className, c.children[0].text, c.attrs.role]);
"""


def test_a_toast_with_an_id_replaces_itself_instead_of_stacking():
    got = run_js("""
      toasts.toast("Rebuilding…", "info", {id: "rebuild"});
      toasts.toast("Rebuilding…", "info", {id: "rebuild"});
      const one = shown();
      toasts.toast("Failed", "error", {id: "rebuild"});
      const replaced = shown();
      toasts.toast("Another", "error");
      out({one, replaced, both: shown().length, timers,
           dismissed: toasts.dismiss("rebuild"), after: shown().length,
           missing: toasts.dismiss("rebuild")});
    """, prelude=TOASTS)
    assert got["one"] == [["toast", "Rebuilding…", "status"]]
    assert got["replaced"] == [["toast error", "Failed", "alert"]]
    assert got["both"] == 2
    assert got["dismissed"] is True and got["after"] == 1
    assert got["missing"] is False
    # v0.1's two lifetimes, unchanged: 4 s, and 8 s for an error.
    assert got["timers"] == [4000, 4000, 8000, 8000]


def test_a_toast_action_runs_once_and_takes_the_toast_with_it():
    got = run_js("""
      let ran = 0;
      toasts.toast("Deleted plate", "ok",
                   {id: "undo", action: {label: "Undo", run: () => { ran += 1; }}});
      const el = host.children[0];
      const button = el.children[1];
      button.listeners.click();
      out({label: button.textContent, cls: button.className, ran,
           left: host.children.length});
    """, prelude=TOASTS)
    assert got["label"] == "Undo" and got["cls"] == "toast-action"
    assert got["ran"] == 1
    assert got["left"] == 0, "the toast outlived the action it offered"


# ------------------------------------------------------- the overlay stack

# `dialogs.js` reaches for `document` only inside functions, so a ~15-line stub
# installed BEFORE the import is enough to drive the parts that are pure
# bookkeeping — the stack, the registry, and the Esc path. The dialog markup
# itself still needs a real browser (and is covered statically above).
STACK = f"""
globalThis.document = {{
  activeElement: null,
  body: {{ appendChild() {{}} }},
  getElementById: () => null,
  createElement: () => ({{ id: "", appendChild() {{}} }}),
  addEventListener() {{}},
  querySelector: () => null,
}};
const dialogs = await import({_uri("dialogs.js")});
const out = (v) => process.stdout.write(JSON.stringify(v));
const overlay = {{ querySelector: () => null, contains: () => false }};
const esc = () => dialogs.__dialogsDispatch__.onKeyDown(
  {{ key: "Escape", preventDefault() {{}}, stopPropagation() {{}} }});
"""


def test_a_legacy_overlay_leaves_the_stack_when_esc_closes_it():
    """I4: the Esc path pops the stack ITSELF.

    If it only called the adopter's `onClose`, an adopter that forgot to route
    back through `notifyClose` would strand the entry — `isModalOpen()` true
    forever, every global shortcut dead, and Esc "closing" a dialog that is
    already gone.
    """
    got = run_js("""
      let closed = 0;
      const handle = dialogs.attachLegacy(overlay,
        {view: "versions", onClose: () => { closed += 1; }});
      handle.notifyOpen();
      const open_ = {stack: dialogs.stack(), modal: dialogs.isModalOpen()};
      esc();
      out({open_, closed, after: dialogs.stack(),
           modal: dialogs.isModalOpen(), isOpen: handle.isOpen()});
    """, prelude=STACK)
    assert [e["view"] for e in got["open_"]["stack"]] == ["versions"]
    assert got["open_"]["modal"] is True
    assert got["closed"] == 1
    assert got["after"] == [] and got["modal"] is False
    assert got["isOpen"] is False


def test_notify_close_is_idempotent_so_an_adopter_may_also_call_it():
    got = run_js("""
      let closed = 0;
      const handle = dialogs.attachLegacy(overlay,
        {view: "merge", onClose: () => { closed += 1; handle.notifyClose(); }});
      handle.notifyOpen();
      esc();
      handle.notifyClose();
      handle.notifyClose();
      out({closed, stack: dialogs.stack()});
    """, prelude=STACK)
    assert got["closed"] == 1 and got["stack"] == []


def test_esc_belongs_to_the_topmost_modal_not_merely_the_topmost_overlay():
    """I3: a non-modal panel must not swallow the sketcher's Esc from across
    the screen — it owns Esc only while focus is inside it."""
    got = run_js("""
      const owner = (entries, inside) =>
        dialogs.__dialogsDispatch__.escOwner(entries, inside);
      const modal = {id: "m", modal: true};
      const panel = {id: "p", modal: false};
      out({
        panelOnTopUnfocused: (owner([modal, panel], () => false) || {}).id,
        panelOnTopFocused: (owner([modal, panel], (e) => e.id === "p") || {}).id,
        modalOnTop: (owner([panel, modal], () => false) || {}).id,
        onlyPanelUnfocused: owner([panel], () => false),
        empty: owner([], () => true),
      });
    """, prelude=STACK)
    # A non-modal on top keeps its hands off unless it holds focus…
    assert got["panelOnTopUnfocused"] == "m"
    assert got["panelOnTopFocused"] == "p"
    assert got["modalOnTop"] == "m"
    # …and with nothing eligible, Esc is left to whoever else wants it.
    assert got["onlyPanelUnfocused"] is None
    assert got["empty"] is None


def test_the_view_registry_refuses_an_unknown_view_and_a_duplicate():
    got = run_js("""
      let opened = null;
      dialogs.register("shortcuts", (args) => { opened = args; return "sheet"; },
                       {title: "Keyboard shortcuts", description: "d"});
      let dup = null;
      try { dialogs.register("shortcuts", () => {}); }
      catch (err) { dup = err.message; }
      const ok = await dialogs.openView("shortcuts", {a: 1}, {by: "agent"});
      const miss = await dialogs.openView("no-such-view", {}, {by: "agent"});
      out({dup, ok, miss, opened,
           views: dialogs.views({}).map((v) => [v.view, v.title, v.agentOpenable])});
    """, prelude=STACK)
    assert got["dup"] == "duplicate dialog view: shortcuts"
    assert got["ok"] == {"ok": True, "view": "shortcuts", "result": "sheet"}
    assert got["opened"] == {"a": 1}
    assert got["miss"] == {"ok": False, "reason": "unknown_view",
                           "view": "no-such-view"}
    assert got["views"] == [["shortcuts", "Keyboard shortcuts", True]]


def test_views_are_filtered_by_their_own_when_predicate():
    got = run_js("""
      dialogs.register("configs", () => {}, {title: "Configurations",
                                             when: (c) => !!c.selectedPart});
      dialogs.register("shortcuts", () => {}, {title: "Keys"});
      out({none: dialogs.views({}).map((v) => v.view),
           part: dialogs.views({selectedPart: "plate"}).map((v) => v.view)});
    """, prelude=STACK)
    assert got["none"] == ["shortcuts"]
    assert sorted(got["part"]) == ["configs", "shortcuts"]


# ------------------------------------------------------- the wiring in main.js

MAIN = Path(__file__).resolve().parents[1] / "frontend" / "js" / "main.js"
INDEX = Path(__file__).resolve().parents[1] / "frontend" / "index.html"


def test_main_wires_the_shell_and_the_panel_di_object_is_renamed():
    """The registry owns the name `actions`; the panel DI object is `panelApi`.

    Both live in `main.js` and a stray `tree.init(actions)` would hand panels
    the action registry instead of their API — a silent, total breakage.
    """
    main = MAIN.read_text(encoding="utf-8")
    assert 'import * as actions from "./shell/actions.js";' in main
    assert 'import * as dialogs from "./shell/dialogs.js";' in main
    assert 'import * as shortcuts from "./shell/shortcuts.js";' in main
    assert 'from "./shell/toast.js";' in main
    assert "const panelApi = {" in main
    for panel in ("tree", "inspector", "chat", "placement", "drawings",
                  "sketcher", "versions", "merge", "proposals", "library",
                  "configs", "comments"):
        assert f"{panel}.init(panelApi)" in main, panel
    assert "dialogs.init(" in main and "shortcuts.init(" in main
    # The primitives that moved out are gone from main.js.
    for gone in ("function setupKeys(", "function modalOpen(",
                 "function toast(", "function setupClaimDialog("):
        assert gone not in main, f"{gone!r} is still in main.js"


def test_the_shell_registers_the_shortcuts_that_exist_today():
    """Migration, not invention: every chord the v0.1 `setupKeys` honoured is
    declared on an action, and nothing else is bound."""
    main = MAIN.read_text(encoding="utf-8")
    for chord, action in [('"F"', '"view.fit"'), ('"G"', '"view.gizmo.translate"'),
                          ('"R"', '"view.gizmo.rotate"'), ('"Mod+Z"', '"edit.undo"'),
                          ('"Mod+S"', '"part.save-script"'), ('"?"', '"help.shortcuts"')]:
        assert chord in main and action in main, (chord, action)
    assert '"Mod+Y"' in main and '"Mod+Shift+Z"' in main   # both redo chords
    # `help.palette`/`Mod+K` belongs to `shell/palette.js`, which registers it
    # itself — `main.js` must not squat the chord with a second binding.
    assert '"Mod+K"' not in main
    # ⌘S must reach `preventDefault` with no part selected (v0.1 always
    # suppressed the browser's Save-page dialog outside CodeMirror), so the
    # part precondition is `enabled:`, never `when:`.
    save = main.split('id: "part.save-script"', 1)[1].split("A({", 1)[0]
    assert "enabled: hasPart" in save and "when: hasPart" not in save


def test_index_html_hosts_the_dialog_layer():
    index = INDEX.read_text(encoding="utf-8")
    assert 'id="dialog-host"' in index
    assert index.index('id="dialog-host"') < index.index('id="toasts"')


# ============================================================================
# Slice 4 — the menu bar and the layout manager
# ============================================================================
#
# `menu_model.js` turns `actions.list(ctx)` into a fixed-order File/Edit/View/
# Model/Help tree (separators on a >=10 order gap, empty menus omitted) and a
# markup string the static a11y pass can read without a DOM. `layout_model.js`
# is the clamp/serialise/collapse rules behind the three resizable panels —
# every one of them a rule a screenshot cannot grade either.

MENU_ACTIONS = """
      const A = (id, menu, extra) => ({id, title: id, menu, run(){},
                                       enabled: true, ...extra});
"""


def test_tree_orders_menus_fixed_and_items_by_the_numeric_order():
    got = run_js(MENU_ACTIONS + """
      const list = [
        A("help.shortcuts", "help/10"),
        A("view.fit", "view/10"),
        A("edit.undo", "edit/10"),
        A("file.new", "file/10"),
        A("file.open", "file/11"),
        A("model.sketch", "model/10"),
      ];
      out({menus: mm.tree(list, "").map((m) => m.menu)});
    """)
    assert got["menus"] == ["file", "edit", "view", "model", "help"]


def test_tree_omits_an_empty_menu_and_drops_actions_with_no_menu_field():
    got = run_js(MENU_ACTIONS + """
      const list = [A("file.new", "file/10"), A("x.hidden", undefined),
                    A("y.bad", "not-a-menu-field")];
      out({menus: mm.tree(list, "").map((m) => m.menu)});
    """)
    assert got["menus"] == ["file"]


def test_tree_draws_a_separator_on_a_ten_point_gap_never_before_the_first():
    got = run_js(MENU_ACTIONS + """
      const list = [A("a", "file/10"), A("b", "file/20"), A("c", "file/21")];
      out({seps: mm.tree(list, "").flatMap((m) => m.items)
                   .map((i) => [i.id, i.separatorBefore])});
    """)
    # a: first item, never a separator. b: gap of 10 from a -> separator.
    # c: gap of 1 from b -> none.
    assert got["seps"] == [["a", False], ["b", True], ["c", False]]


def test_tree_carries_enabled_danger_and_the_platform_shortcut_label():
    got = run_js(MENU_ACTIONS + """
      const list = [A("part.delete", "edit/10",
                      {danger: true, enabled: false, shortcut: "Mod+Z"})];
      out({item: mm.tree(list, "MacIntel")[0].items[0]});
    """)
    item = got["item"]
    assert item["danger"] is True
    assert item["enabled"] is False
    assert item["shortcutLabel"] == "⌘Z"


def test_tree_reads_the_first_chord_out_of_a_binding_object_or_a_list():
    got = run_js(MENU_ACTIONS + """
      const obj = A("edit.undo", "edit/10", {shortcut: {chord: "Mod+Z"}});
      const arr = A("edit.redo", "edit/11",
                    {shortcut: [{chord: "Mod+Y"}, "Mod+Shift+Z"]});
      const none = A("edit.noop", "edit/12", {shortcut: null});
      out({labels: mm.tree([obj, arr, none], "Win32")
                     .flatMap((m) => m.items).map((i) => i.shortcutLabel)});
    """)
    assert got["labels"] == ["Ctrl+Z", "Ctrl+Y", None]


def test_markup_is_the_menubar_a11y_shape_menu_menuitem_and_aria_disabled():
    got = run_js(MENU_ACTIONS + """
      const list = [A("file.new", "file/10"),
                    A("part.delete", "edit/10", {enabled: false, danger: true})];
      out({html: mm.markup(mm.tree(list, ""))});
    """)
    html = got["html"]
    assert 'role="menu"' in html
    # Two menus (file, edit), each with one trigger button + one item row.
    assert html.count('role="menuitem"') == 4
    assert 'aria-haspopup="menu"' in html
    assert 'aria-disabled="true"' in html
    assert 'data-action="part.delete"' in html
    # Every trigger button and every row has visible text (not just an icon).
    for m in re.finditer(r"<button[^>]*>(.*?)</button>", html, re.S):
        assert re.sub(r"<[^>]+>", "", m.group(1)).strip()


def test_markup_escapes_titles_and_labels():
    got = run_js(MENU_ACTIONS + """
      const list = [A("x", "file/10", {title: '<script>&"\\''})];
      out({html: mm.markup(mm.tree(list, ""))});
    """)
    assert "<script>" not in got["html"]
    assert "&lt;script&gt;" in got["html"]


def test_markup_on_an_empty_tree_is_the_empty_string():
    got = run_js("out({html: mm.markup([])});")
    assert got["html"] == ""


# ------------------------------------------------- menu.js DOM (fix round 1)
#
# C1: `closeWrap` (outside-click, Esc, and "a row just ran") never reset the
# module-level `openMenuName`, only `toggle()` did — so the click that should
# REOPEN a menu after any of those three closes was silently absorbed as a
# no-op "close" of an already-closed menu. There is no jsdom in this repo, so
# this is a hand-built DOM stub (the `dialogs.js` STACK precedent, widened):
# each simulated `render()` call gets a FRESH wrap/button/menu triple (never
# reused), exactly like a real `hostEl.innerHTML = markup(...)` produces
# fresh nodes every time — which is what makes firing a click on a STALE
# node impossible here, same as in the browser, and is why this drives the
# real `attach()`/`closeWrap()`/`toggle()`/`render()` chain through the
# public API (`init`, plus synthetic `click` events) rather than poking
# `openMenuName` directly.
MENU_DOM = f"""
function makeEl(tag) {{
  const listeners = {{}};
  const classes = new Set();
  const el = {{
    tag, dataset: {{}}, attrs: {{}}, isConnected: true,
    classList: {{
      add: (c) => classes.add(c),
      remove: (c) => classes.delete(c),
      contains: (c) => classes.has(c),
      toggle(c, force) {{
        const want = force === undefined ? !classes.has(c) : force;
        if (want) classes.add(c); else classes.delete(c);
      }},
    }},
    setAttribute(k, v) {{ el.attrs[k] = String(v); }},
    getAttribute(k) {{ return k in el.attrs ? el.attrs[k] : null; }},
    addEventListener(name, fn) {{ (listeners[name] = listeners[name] || []).push(fn); }},
    _fire(name, evt) {{ for (const fn of (listeners[name] || [])) fn(evt); }},
    focus() {{}},
  }};
  return el;
}}
function makeWrap(name) {{
  const wrap = makeEl("div");
  wrap.dataset.menu = name;
  const btn = makeEl("button");
  btn.setAttribute("aria-haspopup", "menu");
  const menu = makeEl("div");
  menu.classList.add("menu");
  menu.classList.add("hidden");           // fresh markup always starts closed
  const item = makeEl("button");
  item.dataset.action = "project.new";
  item.closest = (sel) => (sel === "[data-action]" ? item : null);
  wrap.querySelector = (sel) => {{
    if (sel.includes("aria-haspopup")) return btn;
    if (sel.includes(".menu")) return menu;
    return null;
  }};
  wrap.contains = (other) => other === wrap || other === btn
    || other === menu || other === item;
  wrap._btn = btn; wrap._menu = menu; wrap._item = item;
  return wrap;
}}
let lastWrap = null;
const host = makeEl("nav");
host.querySelectorAll = (sel) => {{
  if (sel !== ".menu-wrap") return [];
  lastWrap = makeWrap("file");            // a brand-new node, every render()
  return [lastWrap];
}};
const docListeners = {{}};
globalThis.document = {{
  getElementById: () => host,
  addEventListener(name, fn) {{ (docListeners[name] = docListeners[name] || []).push(fn); }},
  _fire(name, ev) {{ for (const fn of (docListeners[name] || [])) fn(ev); }},
}};
const actionsStub = {{
  list: () => [{{id: "project.new", title: "New project…", menu: "file/10",
                run() {{}}, enabled: true}}],
  context: () => ({{}}),
  onChange() {{}},
  run(id) {{ actionsStub.ran = id; }},
}};
const evt = () => ({{ stopPropagation() {{}}, preventDefault() {{}} }});
const menuMod = await import({_uri("menu.js")});
const out = (v) => process.stdout.write(JSON.stringify(v));
"""


def test_c1_closing_a_menu_by_running_a_row_resets_open_tracking_so_it_reopens():
    got = run_js("""
      menuMod.init({actions: actionsStub, host});
      const closedInitially = menuMod.__menuBar__.__openMenuName__();

      // 1. Click the trigger: opens (a fresh wrap each render — `lastWrap`
      //    now points at the live node).
      lastWrap._btn._fire("click", evt());
      const openedName = menuMod.__menuBar__.__openMenuName__();
      const openedHidden = lastWrap._menu.classList.contains("hidden");

      // 2. Run the one row in it — the primary path (open a menu, run a
      //    command). This is `attach()`'s delegated click listener, fired on
      //    the WRAP (that's where it's registered), with the item as target.
      lastWrap._fire("click", {...evt(), target: lastWrap._item});
      const afterRunName = menuMod.__menuBar__.__openMenuName__();
      const afterRunHidden = lastWrap._menu.classList.contains("hidden");

      // 3. Click the SAME trigger again — before the C1 fix this computed
      //    `opening = openMenuName !== name` as false (stale name still
      //    matched) and silently no-opped instead of reopening.
      lastWrap._btn._fire("click", evt());
      const reopenedName = menuMod.__menuBar__.__openMenuName__();
      const reopenedHidden = lastWrap._menu.classList.contains("hidden");

      out({closedInitially, openedName, openedHidden, afterRunName,
           afterRunHidden, reopenedName, reopenedHidden, ran: actionsStub.ran});
    """, prelude=MENU_DOM)
    assert got["closedInitially"] is None
    assert got["openedName"] == "file" and got["openedHidden"] is False
    assert got["ran"] == "project.new"
    # The fix: running the row closes it AND clears the tracked open name.
    assert got["afterRunName"] is None
    assert got["afterRunHidden"] is True
    # The fix, proven end-to-end: the very next click on the trigger reopens
    # it — no second click needed.
    assert got["reopenedName"] == "file"
    assert got["reopenedHidden"] is False


def test_c1_outside_click_and_esc_also_reset_open_tracking_and_allow_a_reopen():
    """The other two `closeWrap` call sites (`menu.js`'s global `click`/
    `keydown` listeners, installed once by `installGlobal()` on the first
    `attach()`) get the identical fix "running a row" already proved above —
    this fires them directly to be sure."""
    got = run_js("""
      menuMod.init({actions: actionsStub, host});

      // Outside click.
      lastWrap._btn._fire("click", evt());
      const openedForOutside = menuMod.__menuBar__.__openMenuName__();
      document._fire("click", {...evt(), target: makeEl("div")});
      const afterOutsideName = menuMod.__menuBar__.__openMenuName__();
      const afterOutsideHidden = lastWrap._menu.classList.contains("hidden");
      lastWrap._btn._fire("click", evt());   // reopens, proving no stuck state
      const reopenedAfterOutside = menuMod.__menuBar__.__openMenuName__();

      // Esc.
      const escOpenWrap = lastWrap;
      document._fire("keydown", {...evt(), key: "Escape"});
      const afterEscName = menuMod.__menuBar__.__openMenuName__();
      const afterEscHidden = escOpenWrap._menu.classList.contains("hidden");
      lastWrap._btn._fire("click", evt());   // reopens too
      const reopenedAfterEsc = menuMod.__menuBar__.__openMenuName__();

      out({openedForOutside, afterOutsideName, afterOutsideHidden,
           reopenedAfterOutside, afterEscName, afterEscHidden, reopenedAfterEsc});
    """, prelude=MENU_DOM)
    assert got["openedForOutside"] == "file"
    assert got["afterOutsideName"] is None and got["afterOutsideHidden"] is True
    assert got["reopenedAfterOutside"] == "file"
    assert got["afterEscName"] is None and got["afterEscHidden"] is True
    assert got["reopenedAfterEsc"] == "file"


# --------------------------------------------------------------- layout model

def test_clamp_pins_a_pixel_panel_into_its_fixed_range():
    got = run_js("""
      out({tooSmall: lm.clamp("sidebar", 10, {height: 900}),
           tooBig: lm.clamp("sidebar", 9999, {height: 900}),
           fine: lm.clamp("sidebar", 300, {height: 900})});
    """)
    assert got == {"tooSmall": 160, "tooBig": 480, "fine": 300}


def test_clamp_pins_chat_to_a_fraction_of_viewport_height():
    got = run_js("""
      out({capped: lm.clamp("chat", 5000, {height: 1000}),
           tiny: lm.clamp("chat", 5000, {height: 100}),
           fine: lm.clamp("chat", 300, {height: 1000})});
    """)
    assert got["capped"] == 600          # 0.6 * 1000
    assert got["tiny"] == 120            # min wins over 0.6 * 100 = 60
    assert got["fine"] == 300


def test_clamp_falls_back_to_the_default_for_garbage_before_clamping():
    got = run_js("""
      out({nan: lm.clamp("inspector", NaN, {height: 900}),
           str: lm.clamp("inspector", "banana", {height: 900}),
           undef: lm.clamp("inspector", undefined, {height: 900})});
    """)
    assert got == {"nan": 326, "str": 326, "undef": 326}


def test_clamp_refuses_an_unknown_panel():
    got = run_js("""
      let msg = null;
      try { lm.clamp("nope", 100, {height: 900}); }
      catch (err) { msg = err.message; }
      out({msg});
    """)
    assert "unknown layout panel" in got["msg"]


def test_deserialize_on_garbage_returns_defaults_sidebar_open_chat_closed():
    got = run_js("""
      out({
        nullish: lm.deserialize(null, {height: 900}),
        notObject: lm.deserialize(42, {height: 900}),
        brokenJson: lm.deserialize("{not json", {height: 900}),
        emptyObject: lm.deserialize({}, {height: 900}),
      });
    """)
    for state in got.values():
        assert state["sidebar"] == {"size": 216, "collapsed": False}
        assert state["inspector"] == {"size": 326, "collapsed": False}
        assert state["chat"] == {"size": 264, "collapsed": True}


def test_deserialize_drops_unknown_keys_clamps_and_coerces_collapsed():
    got = run_js("""
      out({state: lm.deserialize(JSON.stringify({
        sidebar: {size: 9999, collapsed: "yes"},
        bogusPanel: {size: 1},
        inspector: {size: NaN},
      }), {height: 900})});
    """)
    state = got["state"]
    assert state["sidebar"] == {"size": 480, "collapsed": True}
    assert state["inspector"] == {"size": 326, "collapsed": False}
    assert "bogusPanel" not in state


def test_serialize_deserialize_round_trips():
    got = run_js("""
      const state = {sidebar: {size: 250, collapsed: false},
                     inspector: {size: 400, collapsed: true},
                     chat: {size: 300, collapsed: false}};
      const json = JSON.stringify(lm.serialize(state));
      out({back: lm.deserialize(json, {height: 900}), json});
    """)
    assert got["back"]["sidebar"] == {"size": 250, "collapsed": False}
    assert got["back"]["inspector"] == {"size": 400, "collapsed": True}
    assert got["back"]["chat"] == {"size": 300, "collapsed": False}


def test_toggle_flips_only_the_named_panel_and_is_pure():
    got = run_js("""
      const state = lm.deserialize(null, {height: 900});
      const next = lm.toggle(state, "sidebar");
      out({before: state.sidebar.collapsed, after: next.sidebar.collapsed,
           untouched: next.inspector.collapsed === state.inspector.collapsed,
           stateUnchanged: state.sidebar.collapsed});
    """)
    assert got["before"] is False and got["after"] is True
    assert got["untouched"] is True
    assert got["stateUnchanged"] is False   # `state` itself was not mutated


def test_toggle_refuses_an_unknown_panel():
    got = run_js("""
      let msg = null;
      try { lm.toggle(lm.deserialize(null, {height: 900}), "nope"); }
      catch (err) { msg = err.message; }
      out({msg});
    """)
    assert "unknown layout panel" in got["msg"]


@pytest.mark.parametrize("width,inspector,sidebar", [
    (1400, False, False),
    (1099, True, False),
    (900, True, False),
    (799, True, True),
    (400, True, True),
])
def test_responsive_defaults_at_the_1100_and_800_thresholds(width, inspector, sidebar):
    got = run_js("out({r: lm.responsiveDefaults(%d)});" % width)
    assert got["r"] == {"inspectorCollapsed": inspector, "sidebarCollapsed": sidebar}


def test_key_is_namespaced_per_workspace():
    got = run_js('out({a: lm.key("default"), b: lm.key("bench-2")});')
    assert got == {"a": "agentcad.layout.default", "b": "agentcad.layout.bench-2"}


# ----------------------------------------------- layout.js DOM (fix round 1)
#
# C2: `loadState()` folded a migrated `agentcad.chat.open` value into the
# in-memory state but never persisted it before deleting the only other place
# it lived — so a reload with no panel interaction in between lost the
# preference silently. This drives `layout.js`'s real `init()` twice against
# one shared, stateful `localStorage` stub to simulate "a reload with nothing
# touched in between", the exact scenario the bug needed.
LAYOUT_DOM = f"""
function makeEl(tag) {{
  const classes = new Set();
  const el = {{
    tag, id: "", style: {{}}, dataset: {{}}, attrs: {{}},
    classList: {{
      add: (c) => classes.add(c),
      remove: (c) => classes.delete(c),
      contains: (c) => classes.has(c),
      toggle(c, force) {{
        const want = force === undefined ? !classes.has(c) : force;
        if (want) classes.add(c); else classes.delete(c);
      }},
    }},
    setAttribute(k, v) {{ el.attrs[k] = String(v); }},
    getAttribute(k) {{ return k in el.attrs ? el.attrs[k] : null; }},
    addEventListener() {{}},
    after() {{}}, before() {{}},
  }};
  return el;
}}
const sidebar = makeEl("aside"); sidebar.id = "sidebar";
const inspector = makeEl("aside"); inspector.id = "inspector";
const chatDock = makeEl("section"); chatDock.id = "chat-dock";
const byId = {{sidebar, inspector, "chat-dock": chatDock}};
globalThis.document = {{
  getElementById: (id) => byId[id] || null,
  createElement: () => makeEl("div"),
}};
globalThis.window = {{ innerWidth: 1400, innerHeight: 900, addEventListener() {{}} }};
// A plain object backs BOTH `init()` calls below — this is what makes the
// second one a faithful "reload": nothing but localStorage survives.
const store = {{}};
globalThis.localStorage = {{
  getItem: (k) => (k in store ? store[k] : null),
  setItem: (k, v) => {{ store[k] = String(v); }},
  removeItem: (k) => {{ delete store[k]; }},
}};
const layout = await import({_uri("layout.js")});
const out = (v) => process.stdout.write(JSON.stringify(v));
"""


def test_c2_a_migrated_chat_open_preference_survives_a_reload_untouched():
    got = run_js("""
      localStorage.setItem("agentcad.chat.open", "1");   // user had it OPEN
      const first = layout.init({workspace: "default"});
      const persistedAfterFirstInit = localStorage.getItem("agentcad.layout.default");
      const legacyGoneAfterFirstInit = localStorage.getItem("agentcad.chat.open");
      // "Reload": a brand-new `init()` call, same backing localStorage, with
      // NO panel resized/toggled in between (the exact scenario the bug
      // needed — anything that called `toggle()`/dragged a handle would
      // have `persist()`d and masked it).
      const second = layout.init({workspace: "default"});
      out({firstChatCollapsed: first.chat.collapsed, persistedAfterFirstInit,
           legacyGoneAfterFirstInit, secondChatCollapsed: second.chat.collapsed});
    """, prelude=LAYOUT_DOM)
    # legacy "1" means chat was OPEN, i.e. collapsed: false.
    assert got["firstChatCollapsed"] is False
    # The fix: the migration is durably written before the legacy key dies.
    assert got["persistedAfterFirstInit"] is not None
    assert json.loads(got["persistedAfterFirstInit"])["chat"]["collapsed"] is False
    assert got["legacyGoneAfterFirstInit"] is None
    # The fix, proven end-to-end: a second `init()` with nothing else touched
    # reads the ALREADY-migrated preference, not `defaultState()`'s
    # chat-closed default.
    assert got["secondChatCollapsed"] is False


def test_c2_no_legacy_key_present_is_not_treated_as_a_migration():
    """No `agentcad.chat.open` at all (a fresh profile, or the second
    workspace finding it already deleted) must not write anything — the new
    key stays exactly `defaultState()` until something explicitly changes
    it, and `localStorage.setItem` is never called."""
    got = run_js("""
      let sets = 0;
      const realSet = localStorage.setItem;
      localStorage.setItem = (k, v) => { sets += 1; realSet(k, v); };
      const state = layout.init({workspace: "default"});
      out({chatCollapsed: state.chat.collapsed, sets});
    """, prelude=LAYOUT_DOM)
    assert got["chatCollapsed"] is True   # defaultState()'s chat default
    assert got["sets"] == 0


# ------------------------------------------------------- the wiring in main.js

def test_main_wires_the_menu_bar_and_the_layout_manager():
    main = MAIN.read_text(encoding="utf-8")
    assert 'import * as menu from "./shell/menu.js";' in main
    assert 'import * as layout from "./shell/layout.js";' in main
    assert "menu.init({" in main and "layout.init({" in main
    # menu.init/layout.init run after registerActions() (the first render
    # needs a populated registry) and after shortcuts.init() (so the three
    # toggle actions' chords bind through the same onChange subscription as
    # everything else — a conflict would throw from THAT registration).
    tail = main.split("registerActions();", 1)[1]
    assert "shortcuts.init({" not in tail  # already ran, before registerActions()
    assert tail.index("menu.init({") < tail.index("layout.init({")
    # setupMenus()'s body is now a call into shell/menu.js's attach(), not the
    # hand-rolled outside-click/Esc/roving listeners it used to install.
    body = main.split("function setupMenus() {", 1)[1].split("\n}\n", 1)[0]
    assert "menu.attach(" in body
    assert "document.addEventListener" not in body


def test_layout_registers_the_three_toggle_actions_at_the_documented_chords():
    layout_js = (SHELL / "layout.js").read_text(encoding="utf-8")
    for action_id, chord, order in [
        ("view.sidebar.toggle", "Mod+B", "view/30"),
        ("view.inspector.toggle", "Shift+Mod+B", "view/31"),
        ("view.chat.toggle", "Mod+J", "view/32"),
    ]:
        assert f'id: "{action_id}"' in layout_js
        assert f'"{chord}"' in layout_js
        assert f'"{order}"' in layout_js


def test_index_html_hosts_the_menubar_right_after_the_brand():
    index = INDEX.read_text(encoding="utf-8")
    assert '<nav id="menubar" role="menubar">' in index
    assert index.index('id="brand"') < index.index('id="menubar"')
    # Right after the brand: nothing but the closing </div> of #brand and this
    # comment sits between them.
    between = index.split('id="brand"', 1)[1].split('id="menubar"', 1)[0]
    assert "menu-wrap" not in between


# ============================================================================
# Slice 3 — the command palette, result routing and the UX-events client
# ============================================================================
#
# `palette_model.js` is the pure half: the fuzzy score, the ranking, the
# JSON-Schema → form-field derivation, the coercion back to a tool body and the
# result-routing decision. Every rule below is one a screenshot cannot grade,
# and the AC2/AC3 parity tests at the bottom prove the palette's tool source is
# the LIVE registry response rather than anything enumerated in the frontend.

PALETTE = f"""
import * as pm from {_uri("palette_model.js")};
const out = (v) => process.stdout.write(JSON.stringify(v));
"""


def run_palette(body: str) -> object:
    return run_js(body, prelude=PALETTE)


# ------------------------------------------------------------- node-importable

def test_the_slice_3_modules_import_in_node_and_export_their_contract():
    prelude = "".join(
        f'import * as {alias} from {_uri(mod)};\n'
        for alias, mod in [("pm", "palette_model.js"),
                           ("palette", "palette.js"),
                           ("events", "events.js")]
    ) + "const out = (v) => process.stdout.write(JSON.stringify(v));\n"
    got = run_js("out({pm: Object.keys(pm), palette: Object.keys(palette),"
                 " events: Object.keys(events)});", prelude=prelude)
    for name in ("score", "rank", "entriesFromTools", "entriesFromActions",
                 "entriesFromState", "entriesFromViews", "formFields",
                 "needsForm", "coerce", "routeResult", "summarize",
                 "pushRecent", "__palette__"):
        assert name in got["pm"], f"palette_model.{name} is missing"
    for name in ("init", "open", "close"):
        assert name in got["palette"], f"palette.{name} is missing"
    for name in ("init", "emit"):
        assert name in got["events"], f"events.{name} is missing"


# --------------------------------------------------------------- fuzzy scoring

def test_score_is_zero_when_the_query_is_not_a_subsequence():
    got = run_palette("""
      out({miss: pm.score("zzz", "New part…"),
           empty: pm.score("", "New part…"),
           nothing: pm.score("part", ""),
           tooLong: pm.score("new part please", "New part…")});
    """)
    assert got == {"miss": 0, "empty": 0, "nothing": 0, "tooLong": 0}


def test_score_prefers_a_contiguous_word_start_over_a_scattered_subsequence():
    got = run_palette("""
      out({tight: pm.score("part", "New part…"),
           scattered: pm.score("part", "Proposals: rate it"),
           tighter: pm.score("fit", "Fit view"),
           looser: pm.score("fit", "Configuration fitting helper")});
    """)
    assert got["scattered"] > 0, "a scattered subsequence still matches"
    assert got["tight"] > got["scattered"]
    assert got["looser"] > 0
    assert got["tighter"] > got["looser"], "shorter + word-start must win"


def test_score_is_case_insensitive_and_deterministic():
    got = run_palette("""
      const a = pm.score("PART", "New part…");
      const b = pm.score("part", "New PART…");
      out({a, b, again: pm.score("PART", "New part…")});
    """)
    assert got["a"] == got["b"] == got["again"] > 0


# ------------------------------------------------------------------- ranking

RANK_ENTRIES = """
const entries = [
  {id: "tool:x", section: "tools", title: "x"},
  {id: "nav:x", section: "navigation", title: "x"},
  {id: "act:x", section: "actions", title: "x"},
];
"""


def test_rank_breaks_a_score_tie_by_recent_then_section_then_title():
    got = run_palette(RANK_ENTRIES + """
      const bare = pm.rank("x", entries, []).map((e) => e.id);
      const recent = pm.rank("x", entries, ["tool:x"]).map((e) => e.id);
      out({bare, recent});
    """)
    # No recents: the section order decides — actions, navigation, tools.
    assert got["bare"] == ["act:x", "nav:x", "tool:x"]
    # A recent id outranks the section order, and the rest keep it.
    assert got["recent"] == ["tool:x", "act:x", "nav:x"]


def test_rank_filters_out_everything_the_query_does_not_match():
    got = run_palette("""
      const entries = [
        {id: "a", section: "actions", title: "New part…"},
        {id: "b", section: "actions", title: "Fit view"},
        {id: "c", section: "tools", title: "create_part",
         description: "Add a part to a project"},
      ];
      out({ids: pm.rank("part", entries, []).map((e) => e.id)});
    """)
    assert "b" not in got["ids"], "'Fit view' has no 'part' subsequence"
    assert set(got["ids"]) == {"a", "c"}


def test_rank_matches_a_description_and_keywords_but_ranks_them_under_a_title():
    got = run_palette("""
      const entries = [
        {id: "title", section: "actions", title: "Import CAD file…"},
        {id: "desc", section: "actions", title: "Zzz",
         description: "import a CAD file"},
        {id: "kw", section: "actions", title: "Yyy", keywords: ["import"]},
      ];
      out({ids: pm.rank("import", entries, []).map((e) => e.id)});
    """)
    assert got["ids"][0] == "title"
    assert set(got["ids"]) == {"title", "desc", "kw"}


def test_rank_on_an_empty_query_is_recents_first_then_the_head_of_each_section():
    got = run_palette("""
      const entries = [];
      for (let i = 0; i < 10; i += 1) {
        entries.push({id: `a${i}`, section: "actions", title: `a${i}`});
        entries.push({id: `t${i}`, section: "tools", title: `t${i}`});
      }
      entries.push({id: "n0", section: "navigation", title: "n0"});
      out({ids: pm.rank("", entries, ["t7", "a9"]).map((e) => e.id)});
    """)
    ids = got["ids"]
    # Recents first, in recency order, whatever section they belong to.
    assert ids[:2] == ["t7", "a9"]
    # Then the first 8 of each section, actions › navigation › tools, and a
    # recent entry is never listed twice.
    assert ids[2:10] == [f"a{i}" for i in range(8)]
    assert ids[10] == "n0"
    # `t7` is already listed as a recent, so the tools head skips over it and
    # still shows eight — a recent entry is never listed twice.
    assert ids[11:19] == ["t0", "t1", "t2", "t3", "t4", "t5", "t6", "t8"]
    assert len(ids) == len(set(ids))


# ------------------------------------------------------------------- entries

def test_entries_from_tools_reads_the_registry_response_shape():
    got = run_palette("""
      const payload = {tools: [
        {name: "create_part", description: "Add a part",
         input_schema: {type: "object", properties: {project: {type: "string"}},
                        required: ["project"]}},
        {name: "no_schema", description: ""},
        {description: "nameless — skipped"},
      ]};
      out(pm.entriesFromTools(payload));
    """)
    assert [e["id"] for e in got] == ["tool:create_part", "tool:no_schema"]
    assert got[0]["section"] == "tools"
    assert got[0]["title"] == "create_part"
    assert got[0]["description"] == "Add a part"
    assert got[0]["schema"]["required"] == ["project"]
    assert got[1]["schema"] == {}, "a tool with no schema still lists"


def test_entries_from_actions_views_and_state():
    got = run_palette("""
      const acts = pm.entriesFromActions([
        {id: "view.fit", title: "Fit view", group: "View", enabled: true},
        {id: "part.delete", title: "Delete part…", enabled: false, danger: true},
      ]);
      const views = pm.entriesFromViews([
        {view: "shortcuts", title: "Keyboard shortcuts", description: "Every chord"},
      ]);
      const nav = pm.entriesFromState({
        projects: [{name: "demo"}, {name: "other"}],
        parts: [{id: "plate"}],
        projectName: "demo",
      });
      out({acts, views, nav});
    """)
    assert [a["id"] for a in got["acts"]] == ["view.fit", "part.delete"]
    assert got["acts"][0]["section"] == "actions"
    assert got["acts"][1]["enabled"] is False and got["acts"][1]["danger"] is True
    assert got["views"][0]["id"] == "view:shortcuts"
    assert got["views"][0]["title"] == "Open: Keyboard shortcuts"
    assert got["views"][0]["section"] == "navigation"
    # The project you are already in is not a place to go.
    assert [n["id"] for n in got["nav"]] == ["nav:project:other", "nav:part:plate"]
    assert got["nav"][0]["title"] == "Open project: other"
    assert got["nav"][1]["title"] == "Select part: plate"


# ------------------------------------------------------ schema → form fields

SCHEMA = """
const schema = {
  type: "object",
  properties: {
    project: {type: "string", description: "Project name"},
    part_id: {type: "string"},
    name: {type: "string"},
    grade: {type: "string", enum: ["std", "wide"]},
    count: {type: "integer", description: "How many"},
    ratio: {type: "number"},
    force: {type: "boolean"},
    params: {type: "object"},
    ids: {type: "array"},
  },
  required: ["project", "part_id", "name"],
};
const ctx = {projectName: "demo", selectedPart: "plate", selectedInstance: null};
"""


def test_form_fields_puts_required_first_behind_a_divider_and_prefills_from_ctx():
    got = run_palette(SCHEMA + "out(pm.formFields(schema, ctx));")
    names = [f.get("name", "—divider—") for f in got]
    assert names[:3] == ["project", "part_id", "name"]
    assert names[3] == "—divider—" and got[3]["divider"] is True
    assert names[4:] == ["grade", "count", "ratio", "force", "params", "ids"]
    by_name = {f["name"]: f for f in got if not f.get("divider")}
    assert by_name["project"]["value"] == "demo"      # ← ctx.projectName
    assert by_name["part_id"]["value"] == "plate"     # ← ctx.selectedPart
    assert by_name["name"]["value"] == ""             # nothing to prefill from
    assert by_name["project"]["required"] is True
    assert by_name["grade"]["required"] is False
    assert by_name["project"]["help"] == "Project name"


def test_form_fields_maps_every_json_schema_type():
    got = run_palette(SCHEMA + "out(pm.formFields(schema, ctx));")
    by_name = {f["name"]: f for f in got if not f.get("divider")}
    assert by_name["name"]["type"] == "text"
    assert by_name["grade"]["type"] == "select"
    assert by_name["grade"]["options"] == [{"value": "std", "label": "std"},
                                           {"value": "wide", "label": "wide"}]
    assert by_name["count"]["type"] == "number" and by_name["count"]["step"] == 1
    assert by_name["ratio"]["type"] == "number" and "step" not in by_name["ratio"]
    assert by_name["force"]["type"] == "checkbox" and by_name["force"]["value"] is False
    assert by_name["params"]["type"] == "json"
    assert by_name["ids"]["type"] == "json"


def test_form_fields_with_no_optional_args_has_no_divider():
    got = run_palette("""
      out(pm.formFields({type: "object", properties: {a: {type: "string"}},
                         required: ["a"]}, {}));
    """)
    assert [f["name"] for f in got] == ["a"]


def test_form_fields_stringifies_an_object_default_for_its_json_field():
    got = run_palette("""
      out(pm.formFields({type: "object",
        properties: {params: {type: "object", default: {w: 1}}}}, {}));
    """)
    assert got[0]["type"] == "json" and got[0]["value"] == '{"w":1}'


def test_needs_form_is_false_only_when_every_required_arg_is_prefilled():
    got = run_palette(SCHEMA + """
      out({
        // `name` is required and nothing prefills it.
        full: pm.needsForm(schema, ctx),
        // Only the three prefillable args are required here.
        prefilled: pm.needsForm(
          {properties: schema.properties, required: ["project", "part_id"]}, ctx),
        // …and the same schema with no context at all.
        noCtx: pm.needsForm(
          {properties: schema.properties, required: ["project", "part_id"]}, {}),
        none: pm.needsForm({type: "object", properties: {}}, ctx),
        bare: pm.needsForm({}, ctx),
        defaulted: pm.needsForm(
          {properties: {n: {type: "integer", default: 3}}, required: ["n"]}, {}),
      });
    """)
    assert got == {"full": True, "prefilled": False, "noCtx": True,
                   "none": False, "bare": False, "defaulted": False}


# ------------------------------------------------------------------- coerce

def test_coerce_parses_numbers_and_json_and_omits_empty_optionals():
    got = run_palette("""
      const fields = [
        {name: "project", type: "text", required: true},
        {divider: true},
        {name: "count", type: "number", required: false},
        {name: "ratio", type: "number", required: false},
        {name: "force", type: "checkbox", required: false},
        {name: "keep", type: "checkbox", required: false},
        {name: "params", type: "json", required: false},
        {name: "note", type: "text", required: false},
        {name: "grade", type: "select", required: false,
         options: [{value: 2, label: "2"}, {value: 3, label: "3"}]},
      ];
      out(pm.coerce(fields, {project: "demo", count: "7", ratio: "",
                             force: true, params: '{"w": 1}', note: "  ",
                             grade: "3"}));
    """)
    assert got == {"project": "demo", "count": 7, "force": True,
                   "keep": False, "params": {"w": 1}, "grade": 3}
    assert "ratio" not in got, "an empty optional number is omitted, not NaN"
    assert "note" not in got, "a whitespace-only optional string is omitted"


def test_coerce_refuses_a_broken_json_field_by_name():
    got = run_palette("""
      let message = null;
      try {
        pm.coerce([{name: "params", type: "json", required: true}],
                  {params: "{oops"});
      } catch (err) { message = err.message; }
      out({message});
    """)
    assert "params" in got["message"] and "JSON" in got["message"]


# ------------------------------------------------------------ result routing

def test_route_result_sends_a_refusal_to_the_dialog_a_summary_to_a_toast():
    got = run_palette("""
      const big = {rows: []};
      for (let i = 0; i < 40; i += 1) big.rows.push({i, name: `row-${i}`});
      out({
        refusal: pm.routeResult({error: {type: "validation_error", message: "no"}}),
        tiny: pm.routeResult({ok: true}),
        threeScalars: pm.routeResult({ok: true, part: "plate", volume: 1234.5}),
        // 4 scalar keys, but the JSON is still under 120 chars.
        fourShort: pm.routeResult({a: 1, b: 2, c: 3, d: 4}),
        // Long, but ≤ 3 keys and all of them scalar: the second rule reads it.
        longScalar: pm.routeResult({ok: true, message: "x".repeat(400)}),
        // Long, and one key is a nested object: neither rule saves it.
        nested: pm.routeResult({ok: true, a: {b: "x".repeat(200)}, c: 2}),
        // Small enough to read at a glance, whatever its shape.
        smallNested: pm.routeResult({ok: true, a: {b: 1}, c: 2, d: 3, e: 4}),
        big: pm.routeResult(big),
        nothing: pm.routeResult(null),
      });
    """)
    assert got["refusal"] == "error"
    assert got["tiny"] == "toast"
    assert got["threeScalars"] == "toast"
    assert got["fourShort"] == "toast", "under 120 chars is a toast whatever the keys"
    assert got["longScalar"] == "toast", "≤ 3 scalar keys is the second rule"
    assert got["nested"] == "panel"
    assert got["smallNested"] == "toast", "under 120 chars is the first rule"
    assert got["big"] == "panel"
    assert got["nothing"] == "toast"


def test_summarize_reads_the_scalars_and_the_refusal_reads_its_message():
    got = run_palette("""
      out({
        scalars: pm.summarize({ok: true, part: "plate", volume: 12}),
        error: pm.errorMessage({error: {type: "validation_error",
                                        message: "no such part"}}),
        typed: pm.errorMessage({error: {type: "kernel_error"}}),
        none: pm.errorMessage({ok: true}),
      });
    """)
    assert got["scalars"] == "ok: true, part: plate, volume: 12"
    assert got["error"] == "no such part"
    assert got["typed"] == "kernel_error"
    assert got["none"] == ""


def test_push_recent_moves_to_the_front_dedupes_and_caps():
    got = run_palette("""
      let r = [];
      for (const id of ["a", "b", "c", "a"]) r = pm.pushRecent(r, id);
      const capped = pm.pushRecent(["1", "2", "3"], "4", 3);
      out({r, capped, untouched: pm.pushRecent(["a"], "")});
    """)
    assert got["r"] == ["a", "c", "b"]
    assert got["capped"] == ["4", "1", "2"]
    assert got["untouched"] == ["a"], "an empty id is not a recent"


# ----------------------------------------------- AC2 / AC3: the LIVE registry

def _tools_payload(kernel, tmp_path, extra=None):
    """`GET /api/tools` off a real app, optionally with a fixture tool."""
    from fastapi.testclient import TestClient

    from agentcad.core.tools import Tool, build_registry
    from agentcad.server.app import create_app

    from .conftest import make_test_service

    service = make_test_service(tmp_path / "projects", kernel)
    registry = build_registry(service)
    if extra is not None:
        registry.register(extra)
    app = create_app(service, registry, extra_allowed_hosts={"testserver"})
    with TestClient(app, base_url="http://127.0.0.1") as client:
        response = client.get("/api/tools")
    assert response.status_code == 200
    return response.json()


def test_ac3_a_tool_registered_into_the_live_registry_reaches_the_palette(
        kernel, tmp_path):
    """AC3 parity: nothing frontend-side enumerates tools.

    A tool no pack in this repo ships is registered into the app's registry,
    served by the real `GET /api/tools`, and piped verbatim into
    `palette_model.entriesFromTools` — the palette's only tool source.
    """
    from agentcad.core.tools import Tool

    payload = _tools_payload(kernel, tmp_path, extra=Tool(
        name="fixture_widget",
        description="A tool no pack ships — registered only by this test.",
        input_schema={"type": "object",
                      "properties": {"project": {"type": "string"},
                                     "count": {"type": "integer"}},
                      "required": ["project"]},
        handler=lambda project, count=1: {"ok": True},
    ))
    entries = run_palette(f"out(pm.entriesFromTools({json.dumps(payload)}));")
    by_id = {e["id"]: e for e in entries}
    assert "tool:fixture_widget" in by_id, "a live-registry tool must be listed"
    row = by_id["tool:fixture_widget"]
    assert row["title"] == "fixture_widget"
    assert row["description"] == "A tool no pack ships — registered only by this test."
    assert row["schema"]["required"] == ["project"]
    # It is findable by the query a user would type, too.
    ranked = run_palette(
        f"out(pm.rank('widget', pm.entriesFromTools({json.dumps(payload)}), [])"
        ".map((e) => e.id));")
    assert "tool:fixture_widget" in ranked


def test_ac2_check_interference_is_in_the_palette_because_the_registry_has_it(
        kernel, tmp_path):
    """AC2 half: presence follows the registry, in both directions."""
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


# ------------------------------------------------------- the wiring in main.js

def test_the_palette_owns_mod_k_and_the_help_palette_action():
    palette = (SHELL / "palette.js").read_text(encoding="utf-8")
    assert '"help.palette"' in palette
    assert '"Mod+K"' in palette, "the palette registers its own chord"
    assert 'view: "palette"' in palette
    assert 'source: "palette"' in palette
    # FR6: the tool list is the registry's answer, never a frontend list.
    assert "listTools" in palette


def test_main_wires_the_palette_the_events_client_and_ui_open():
    main = MAIN.read_text(encoding="utf-8")
    assert 'import * as palette from "./shell/palette.js";' in main
    assert 'import * as events from "./shell/events.js";' in main
    assert "events.init({" in main and "palette.init({" in main
    assert "dialogs.setEmitter(events.emit)" in main
    # The agent's landing site, and the three telemetry types the browser
    # publishes and must not re-handle.
    assert 'case "ui_open":' in main
    assert 'dialogs.openView(ev.view, ev.args || {}, { by: "agent" })' in main
    for ignored in ("dialog_opened", "dialog_submitted", "palette_executed"):
        assert f'case "{ignored}":' in main, ignored
    # `palette_executed` for an ACTION comes from the registry's run listener,
    # so a menu/toolbar/shortcut run is never mistaken for a palette one.
    assert 'source === "palette"' in main


def test_api_exposes_the_two_endpoints_the_shell_needs():
    api = (Path(__file__).resolve().parents[1] / "frontend" / "js" / "api.js"
           ).read_text(encoding="utf-8")
    assert 'listTools: () => request("GET", "/api/tools")' in api
    assert 'postUiEvent: (body) => request("POST", "/api/ui/events", body)' in api


def test_index_html_has_the_palette_affordance():
    index = INDEX.read_text(encoding="utf-8")
    assert 'id="palette-btn"' in index
    assert "Command palette" in index


def test_the_palette_button_reads_its_label_from_the_shortcut_table():
    """M11: `index.html` cannot know whether this browser renders `⌘K` or
    `Ctrl+K`, and a Windows user shown a Command glyph learns the wrong key."""
    main = MAIN.read_text(encoding="utf-8")
    body = main.split("function setupPaletteButton()", 1)
    assert len(body) == 2, "setupPaletteButton is missing"
    body = body[1].split("\n}\n", 1)[0]
    assert 'shortcuts.list().find((r) => r.actionId === "help.palette")' in body
    assert "btn.textContent = row.label" in body
    assert "aria-keyshortcuts" in body
    assert "setupPaletteButton();" in main


def test_the_palette_css_lands_in_the_one_stylesheet_with_tokens_only():
    css = (Path(__file__).resolve().parents[1] / "frontend" / "css" / "app.css"
           ).read_text(encoding="utf-8")
    block = css.split("/* palette (PRD-026 slice 3)", 1)
    assert len(block) == 2, "the palette CSS block is missing"
    # …up to the next top-level section comment, so this reads OUR block only.
    body = block[1].split("\n/* ---", 1)[0].split("\n/* ===", 1)[0]
    for selector in (".palette-input", ".palette-list", ".palette-option",
                     ".palette-option.active", ".palette-badge", ".palette-kbd",
                     ".dlg-result pre"):
        assert selector in body, selector
    # Token-only colours: no raw hex in the palette block.
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", body), "raw hex in the palette CSS"


# ------------------------------------------------- the UX events client

EVENTS = f"""
const posted = [];
let fail = false;
const api = {{postUiEvent: (b) => {{
  posted.push(b);
  return fail ? Promise.reject(new Error("offline")) : Promise.resolve({{ok: true}});
}}}};
const events = await import({_uri("events.js")});
const out = (v) => process.stdout.write(JSON.stringify(v));
"""


def test_the_events_client_filters_to_the_routes_allow_list():
    """A 422 on every dialog open is a console people learn to ignore, so the
    client refuses the body the route would refuse."""
    got = run_js("""
      out({
        ok: events.body("palette_executed", {action: "part.new", tool: "x"}),
        // the shape `dialogs.setEmitter(events.emit)` calls it with
        oneObject: events.body({type: "dialog_opened", view: "palette"}),
        unknownType: events.body("something_else", {view: "x"}),
        noType: events.body({view: "x"}),
        extraKey: events.body("dialog_opened", {view: "x", secret: "y"}),
        nonString: events.body("dialog_opened", {view: 7}),
        clipped: events.body("dialog_opened", {view: "v".repeat(200)}).view.length,
      });
    """, prelude=EVENTS)
    assert got["ok"] == {"type": "palette_executed", "action": "part.new", "tool": "x"}
    assert got["oneObject"] == {"type": "dialog_opened", "view": "palette"}
    assert got["unknownType"] is None and got["noType"] is None
    assert got["extraKey"] == {"type": "dialog_opened", "view": "x"}
    assert got["nonString"] == {"type": "dialog_opened"}
    assert got["clipped"] == 80


def test_emit_is_fire_and_forget_and_a_dead_server_never_reaches_the_ui():
    got = run_js("""
      const before = [];
      events.emit("dialog_opened", {view: "early"});   // no init() yet
      before.push(posted.length);
      events.init({api});
      events.emit("dialog_opened", {view: "palette"});
      fail = true;
      let threw = null;
      try { events.emit("palette_executed", {action: "part.new"}); }
      catch (err) { threw = String(err); }
      await new Promise((r) => setTimeout(r, 0));
      out({before, posted, threw});
    """, prelude=EVENTS)
    assert got["before"] == [0], "no init() means no post, not a crash"
    assert got["posted"] == [{"type": "dialog_opened", "view": "palette"},
                             {"type": "palette_executed", "action": "part.new"}]
    assert got["threw"] is None, "a rejected post must never surface"


# ------------------------------------------------------ the palette in a DOM

# `palette.js` touches `document`/`window`/`localStorage` only inside `open()`,
# so a stub installed before the import drives the real module: the render, the
# arrow keys, the Enter that runs an action through the registry, and the two
# tool paths. `dialogs`/`actions`/`api` are injected, so this exercises the
# palette itself and nothing underneath it.
PALETTE_UI = f"""
const classList = (el) => ({{
  add(c) {{ el._cls.add(c); el.className = [...el._cls].join(" "); }},
  remove(c) {{ el._cls.delete(c); el.className = [...el._cls].join(" "); }},
  toggle(c, on) {{ if (on === undefined ? el._cls.has(c) : on === false) this.remove(c);
                   else this.add(c); }},
  contains(c) {{ return el._cls.has(c); }},
}});
const node = (tag) => {{
  const el = {{
    tag, id: "", _cls: new Set(), className: "", dataset: {{}}, attrs: {{}},
    children: [], _text: "", value: "", placeholder: "", type: "",
    autocomplete: "", spellcheck: true, parent: null, listeners: {{}},
    set textContent(v) {{ this._text = String(v); this.children = []; }},
    get textContent() {{ return this._text; }},
    setAttribute(k, v) {{ this.attrs[k] = v; }},
    removeAttribute(k) {{ delete this.attrs[k]; }},
    getAttribute(k) {{ return this.attrs[k]; }},
    appendChild(c) {{ c.parent = this; this.children.push(c); return c; }},
    append(...cs) {{ for (const c of cs) this.appendChild(c); }},
    addEventListener(n, fn) {{ (this.listeners[n] = this.listeners[n] || []).push(fn); }},
    contains(other) {{
      for (let p = other; p; p = p.parent) if (p === this) return true;
      return false;
    }},
    closest(sel) {{
      for (let p = this; p; p = p.parent) {{
        if (sel === "[data-index]" && p.dataset.index !== undefined) return p;
      }}
      return null;
    }},
    focus() {{ globalThis.document.activeElement = this; }},
    select() {{}},
    scrollIntoView() {{}},
    remove() {{}},
  }};
  el.classList = classList(el);
  return el;
}};
globalThis.document = {{
  activeElement: null, body: {{appendChild() {{}}}},
  createElement: node, getElementById: () => null, addEventListener() {{}},
  querySelector: () => null,
}};
const winKeys = [];
globalThis.window = {{
  addEventListener(n, fn) {{ if (n === "keydown") winKeys.push(fn); }},
  removeEventListener(n, fn) {{
    const i = winKeys.indexOf(fn); if (i >= 0) winKeys.splice(i, 1);
  }},
}};
globalThis.localStorage = {{
  _s: {{}},
  getItem(k) {{ return this._s[k] == null ? null : this._s[k]; }},
  setItem(k, v) {{ this._s[k] = String(v); }},
}};

// ---- injected shell ----
const ran = [];
const toasts = [];
const emitted = [];
const navigated = [];
let actionList = [{{id: "view.fit", title: "Fit view", enabled: true}}];
const actions = {{
  registered: [],
  register(spec) {{ this.registered.push(spec); return spec; }},
  get: (id) => null,
  list: () => actionList,
  context: () => ({{projectName: "demo", selectedPart: "plate",
                   selectedInstance: null}}),
  onChange() {{}}, onRun() {{}},
  run(id, ctx, opts) {{ ran.push([id, opts && opts.source]); }},
}};
let resolveOpen = null;
const openedSpecs = [];
let formSpec = null;
let formResolve = null;
const openedViews = [];
const dialogs = {{
  open(spec) {{
    openedSpecs.push(spec);
    const p = new Promise((r) => {{ resolveOpen = r; }});
    p.handle = {{id: "h1", errors: [], setError(m) {{ p.handle.errors.push(m); }}}};
    return p;
  }},
  close() {{ if (resolveOpen) resolveOpen({{ok: false}}); resolveOpen = null; }},
  form(spec) {{ formSpec = spec; return new Promise((r) => {{ formResolve = r; }}); }},
  views: () => [{{view: "shortcuts", title: "Keyboard shortcuts", description: ""}}],
  register() {{}},
  openView(view) {{ openedViews.push(view); return Promise.resolve({{ok: true}}); }},
}};
let toolResult = {{ok: true, part: "plate"}};
const calls = [];
let listToolsCalls = 0;
let listToolsFails = false;
const api = {{
  listTools() {{
    listToolsCalls += 1;
    if (listToolsFails) return Promise.reject(new Error("offline"));
    return Promise.resolve({{tools: [
      {{name: "list_parts", description: "Every part in a project",
        input_schema: {{type: "object",
                       properties: {{project: {{type: "string"}}}},
                       required: ["project"]}}}},
      {{name: "create_part", description: "Add a part",
        input_schema: {{type: "object",
                       properties: {{project: {{type: "string"}},
                                    id: {{type: "string"}}}},
                       required: ["project", "id"]}}}},
      {{name: "list_projects", description: "Every project",
        input_schema: {{type: "object",
                       properties: {{limit: {{type: "integer"}}}}}}}},
    ]}});
  }},
  callTool(name, body) {{ calls.push([name, body]); return Promise.resolve(toolResult); }},
}};
const store = await import({_juri("state.js")});
const appState = {{projects: [{{name: "demo"}}, {{name: "other"}}],
                  project: {{parts: [{{id: "plate"}}]}},
                  projectName: "demo", connected: true}};
const palette = await import({_uri("palette.js")});
const tick = () => new Promise((r) => setTimeout(r, 0));
const boot = () => palette.init({{
  actions, dialogs, api, toast: (m, k) => toasts.push([m, k]),
  events: {{emit: (t, p) => emitted.push([t, p])}},
  shortcuts: {{list: () => [{{actionId: "view.fit", label: "F"}}]}},
  state: appState,
  loadProject: (n) => navigated.push(["project", n]),
  selectPart: (n) => navigated.push(["part", n]),
}});
const rowsOf = () => {{
  const root = openedSpecs[openedSpecs.length - 1].body;
  const list = root.children[1];
  return list.children.map((c) => [c._text || c.children[0]._text,
                                   c.classList.contains("active")]);
}};
const inputOf = () => openedSpecs[openedSpecs.length - 1].body.children[0];
const key = (k, extra) => winKeys[0](Object.assign(
  {{key: k, target: inputOf(), preventDefault() {{}}, stopPropagation() {{}}}},
  extra || {{}}));
const out = (v) => process.stdout.write(JSON.stringify(v));
"""


def test_the_palette_registers_mod_k_and_merges_all_four_sources():
    got = run_js("""
      boot();
      const spec = actions.registered[0];
      palette.open();                     // resolves only when it CLOSES
      await tick();                       // the tool fetch lands
      out({spec: {id: spec.id, shortcut: spec.shortcut, menu: spec.menu},
           view: openedSpecs[0].view, width: openedSpecs[0].width,
           modal: openedSpecs[0].modal !== false,
           rows: rowsOf().map((r) => r[0]),
           combobox: inputOf().attrs.role,
           controls: inputOf().attrs["aria-controls"],
           active: inputOf().attrs["aria-activedescendant"]});
    """, prelude=PALETTE_UI)
    assert got["spec"] == {"id": "help.palette", "shortcut": "Mod+K",
                           "menu": "help/10"}
    assert got["view"] == "palette" and got["width"] == "wide"
    assert got["modal"] is True
    # actions › navigation (registered views, then projects, then parts) › tools
    assert got["rows"] == ["Fit view", "Open: Keyboard shortcuts",
                           "Open project: other", "Select part: plate",
                           "list_parts", "create_part", "list_projects"]
    assert got["combobox"] == "combobox"
    assert got["controls"] == "palette-list"
    assert got["active"] == "palette-opt-0"


def test_arrows_move_the_selection_and_enter_runs_through_the_registry():
    """Enter is intercepted at WINDOW capture, so the dialog stack never sees
    it as a submit — and the run carries `source: "palette"`, which is what
    `main.js` turns into exactly one `palette_executed`."""
    got = run_js("""
      boot();
      let stopped = 0, prevented = 0;
      const spy = (k) => winKeys[0]({key: k, target: inputOf(),
        preventDefault: () => { prevented += 1; },
        stopPropagation: () => { stopped += 1; }});
      palette.open();
      await tick();
      spy("ArrowDown"); spy("ArrowDown");
      const moved = rowsOf().map((r) => r[1]);
      spy("ArrowUp");
      const back = rowsOf().findIndex((r) => r[1]);
      // wrap: one Up from the top lands on the last row
      spy("ArrowUp"); spy("ArrowUp");
      const wrapped = rowsOf().findIndex((r) => r[1]);
      const total = rowsOf().length;
      inputOf().value = "fit";
      inputOf().listeners.input[0]();
      const filtered = rowsOf().map((r) => r[0]);
      spy("Enter");
      await tick();
      out({moved, back, wrapped, total, filtered, ran, prevented, stopped,
           listenerLeft: winKeys.length});
    """, prelude=PALETTE_UI)
    assert got["moved"][2] is True and got["moved"][0] is False
    assert got["back"] == 1
    assert got["wrapped"] == got["total"] - 1, "the listbox wraps"
    assert got["filtered"] == ["Fit view"], "the query filters the merged list"
    assert got["ran"] == [["view.fit", "palette"]]
    assert got["prevented"] == got["stopped"] == 6
    assert got["listenerLeft"] == 0, "the window listener died with the palette"


def test_a_tool_whose_required_args_the_context_answers_runs_on_enter():
    got = run_js("""
      boot();
      palette.open();
      await tick();
      inputOf().value = "list_parts";
      inputOf().listeners.input[0]();
      key("Enter");
      await tick(); await tick();
      const small = {calls: JSON.parse(JSON.stringify(calls)), toasts, emitted,
                     form: formSpec};
      out(small);
    """, prelude=PALETTE_UI)
    # `project` is required and `ctx.projectName` answers it: no form.
    assert got["form"] is None
    assert got["calls"] == [["list_parts", {"project": "demo"}]]
    assert got["toasts"] == [["list_parts: ok: true, part: plate", "success"]]
    # A tool is not run through the action registry, so the palette emits.
    assert got["emitted"] == [["palette_executed", {"action": "tool:list_parts"}]]


def test_a_tool_with_an_unanswered_required_arg_opens_a_generated_form():
    got = run_js("""
      boot();
      palette.open();
      await tick();
      inputOf().value = "create_part";
      inputOf().listeners.input[0]();
      key("Enter");
      await tick();
      const fields = formSpec.fields.map((f) => [f.name, f.type, f.required,
                                                 f.value]);
      // The refusal path: the tool answers {error} and the form stays open.
      let shown = null;
      toolResult = {error: {type: "validation_error", message: "id is taken"}};
      const kept = await formSpec.onSubmit({project: "demo", id: "plate"},
                                           {setError: (m) => { shown = m; }});
      out({view: formSpec.view, title: formSpec.title, fields, kept, shown,
           calls: JSON.parse(JSON.stringify(calls))});
    """, prelude=PALETTE_UI)
    assert got["view"] == "tool:create_part" and got["title"] == "create_part"
    assert got["fields"] == [["project", "text", True, "demo"],
                             ["id", "text", True, ""]]
    assert got["kept"] is False, "a refusal keeps the form open"
    assert got["shown"] == "id is taken"
    assert got["calls"] == [["create_part", {"project": "demo", "id": "plate"}]]


# ---------------------------------------- the formFields → dialogs.form seam

def test_a_divider_renders_as_a_separator_and_never_as_a_stray_input():
    """Fix round 1, I1 — the one contract that crosses the slice-1/slice-3
    boundary: what the DIALOG renderer does with `formFields`' output.

    `formFields` emits `{divider: true}` between a tool's required and optional
    arguments, which is most of the registry. Without a divider branch it fell
    through `renderField`'s generic `else` and produced an unlabeled, focusable
    `<input type="text">` under `ids.fields["undefined"]` — an extra Tab stop
    with no accessible name that silently discarded whatever was typed into it.
    """
    prelude = (f'import * as pm from {_uri("palette_model.js")};\n'
               f'import * as dm from {_uri("dialogs_model.js")};\n'
               "const out = (v) => process.stdout.write(JSON.stringify(v));\n")
    got = run_js("""
      const fields = pm.formFields({type: "object", properties: {
        project: {type: "string"}, id: {type: "string"},
        count: {type: "integer"}, flag: {type: "boolean"},
      }, required: ["project", "id"]}, {});
      const {html, ids} = dm.markup({uid: 9, view: "tool:x", title: "x", fields});
      out({shape: fields.map((f) => (f.divider ? "DIVIDER" : f.name)),
           html, fieldKeys: Object.keys(ids.fields)});
    """, prelude=prelude)
    assert got["shape"] == ["project", "id", "DIVIDER", "count", "flag"]
    html = got["html"]
    # The separator is drawn…
    assert 'class="dlg-divider" role="separator"' in html
    # …and it is not a control: no nameless field wrapper, no `undefined` ids,
    # no unlabeled input, and no empty `<label>`.
    assert 'data-field=""' not in html
    assert "undefined" not in html
    assert 'name=""' not in html
    assert "></label>" not in html
    assert got["fieldKeys"] == ["project", "id", "count", "flag"]


def test_the_dialog_never_reads_a_divider_as_a_value_or_validates_one():
    """The other half of I1: `dialogs.js` filters dividers out of the entry's
    field list, so `readValues`/`refreshValidity`/`validate` never see one."""
    dialogs_js = (SHELL / "dialogs.js").read_text(encoding="utf-8")
    assert ".filter((f) => f && !f.divider && f.name)" in dialogs_js
    got = run_js("""
      // A divider must not be able to make a form invalid, either.
      out(dm.validate([{name: "a", required: true}, {divider: true}],
                      {a: "x"}));
    """)
    assert got == {"errors": {}, "valid": True}


# ------------------------------------------- the three behaviours from I2

def test_shift_enter_forces_the_form_on_a_tool_that_needs_no_arguments():
    """The brief's `Shift+Enter` rule: a no-required-args tool runs on Enter,
    but its optional arguments must stay reachable."""
    got = run_js("""
      boot();
      palette.open();
      await tick();
      inputOf().value = "list_projects";
      inputOf().listeners.input[0]();
      key("Enter", {shiftKey: true});
      await tick();
      const forced = formSpec && {view: formSpec.view,
        fields: formSpec.fields.map((f) => [f.name, f.type, f.required])};
      const afterShift = {calls: calls.length, form: forced};

      // …and a plain Enter on the same tool runs it with no form at all.
      formSpec = null;
      palette.open();
      await tick();
      inputOf().value = "list_projects";
      inputOf().listeners.input[0]();
      key("Enter");
      await tick(); await tick();
      out({afterShift, plainForm: formSpec,
           calls: JSON.parse(JSON.stringify(calls))});
    """, prelude=PALETTE_UI)
    assert got["afterShift"]["calls"] == 0, "Shift+Enter asks before it runs"
    assert got["afterShift"]["form"]["view"] == "tool:list_projects"
    assert got["afterShift"]["form"]["fields"] == [["limit", "number", False]]
    assert got["plainForm"] is None, "plain Enter runs it immediately"
    assert got["calls"] == [["list_projects", {}]]


def test_a_large_result_opens_the_non_modal_tool_result_panel():
    got = run_js("""
      boot();
      const big = {rows: []};
      for (let i = 0; i < 40; i += 1) big.rows.push({i, name: `row-${i}`});
      toolResult = big;
      palette.open();
      await tick();
      inputOf().value = "list_parts";
      inputOf().listeners.input[0]();
      key("Enter");
      await tick(); await tick();
      const panel = openedSpecs[openedSpecs.length - 1];
      const body = panel.body;
      const pre = body.children.find((c) => c.tag === "pre");
      const copy = body.children.find((c) => c.tag === "button");
      out({view: panel.view, modal: panel.modal, width: panel.width,
           title: panel.title, wrapClass: body.className,
           pretty: pre.textContent.split("\\n").length > 5,
           parsed: JSON.parse(pre.textContent).rows.length,
           copyLabel: copy.textContent, copyClass: copy.className,
           toasts});
    """, prelude=PALETTE_UI)
    assert got["view"] == "tool-result"
    assert got["modal"] is False, "the result panel must not trap the workbench"
    assert got["width"] == "wide" and got["title"] == "list_parts"
    assert got["wrapClass"] == "dlg-result"
    assert got["pretty"] is True, "the JSON is pretty-printed, not one line"
    assert got["parsed"] == 40, "the panel carries the whole result"
    assert got["copyLabel"] == "Copy JSON"
    assert got["toasts"] == [], "a panelled result does not ALSO toast"


def test_a_huge_or_circular_result_is_capped_instead_of_pasted_whole():
    """M8: the panel is by definition the large-result path, so it is the one
    place a multi-MB payload becomes a multi-MB text node."""
    got = run_js("""
      const cyclic = {a: 1}; cyclic.self = cyclic;
      const huge = {blob: "x".repeat(200000)};
      const capped = palette.resultText(huge);
      out({circular: palette.resultText(cyclic).text.slice(0, 20),
           circularTruncated: palette.resultText(cyclic).truncated,
           truncated: capped.truncated, length: capped.text.length,
           small: palette.resultText({ok: true})});
    """, prelude=PALETTE_UI)
    assert got["circular"].startswith("[this result cannot")
    assert got["circularTruncated"] is False
    assert got["truncated"] is True
    assert got["length"] == 64 * 1024
    assert got["small"] == {"text": '{\n  "ok": true\n}', "truncated": False}


def test_the_tool_cache_is_dropped_on_the_connected_rising_edge():
    """A socket that dropped and came back may have come back to a different
    process with a different set of packs loaded."""
    got = run_js("""
      appState.connected = false;
      boot();
      palette.open();
      await tick();
      const first = listToolsCalls;

      // A change that is not a rising edge refetches nothing.
      store.setState({connected: false});
      await tick();
      const flat = listToolsCalls;

      // The rising edge does.
      appState.connected = true;
      store.setState({connected: true});
      await tick(); await tick();
      const risen = listToolsCalls;

      // Still connected: no further fetch, and the rows are still there.
      store.setState({connected: true});
      await tick();
      out({first, flat, risen, again: listToolsCalls,
           rows: rowsOf().length});
    """, prelude=PALETTE_UI)
    assert got["first"] == 1, "the first open fetches once"
    assert got["flat"] == 1, "a non-rising change refetches nothing"
    assert got["risen"] == 2, "the reconnect drops the cache and refetches"
    assert got["again"] == 2, "a repeated `connected: true` is not an edge"
    assert got["rows"] > 0


def test_a_failed_tool_list_says_so_once_instead_of_looking_empty():
    """M15: an empty Tools section must not be indistinguishable from
    "we could not ask"."""
    got = run_js("""
      listToolsFails = true;
      boot();
      palette.open();
      await tick(); await tick();
      out({toasts, rows: rowsOf().map((r) => r[0])});
    """, prelude=PALETTE_UI)
    assert got["toasts"] == [
        ["Tool list unavailable — the palette is showing UI actions only", "warn"]]
    # …and the palette is still fully usable for everything that is not a tool.
    assert "Fit view" in got["rows"]


def test_a_run_is_recorded_the_same_way_whatever_the_tools_arity_or_outcome():
    """M1/M2 — ONE rule: a row counts as run when the verb was actually
    invoked, including when it then failed (the user did run it, and a refusal
    is a result). A form the user CANCELS invoked nothing, so it is neither
    remembered nor emitted. Before this, a refused no-form run was counted and
    a refused form run was not — the same failure, two answers."""
    got = run_js("""
      boot();
      toolResult = {error: {type: "validation_error", message: "no"}};

      // (a) a refused NO-FORM run
      palette.open();
      await tick();
      inputOf().value = "list_parts";
      inputOf().listeners.input[0]();
      key("Enter");
      await tick(); await tick();
      const refusedImmediate = {emitted: emitted.length,
                                recent: JSON.parse(localStorage.getItem(
                                  "agentcad.palette.recent"))};

      // (b) a refused FORM run — same failure, and now the same bookkeeping
      palette.open();
      await tick();
      inputOf().value = "create_part";
      inputOf().listeners.input[0]();
      key("Enter");
      await tick();
      const kept = await formSpec.onSubmit({project: "demo", id: "plate"},
                                           {setError() {}});
      const refusedForm = {emitted: emitted.length, kept};

      // (c) a CANCELLED form: nothing ran, nothing is recorded
      formSpec = null;
      palette.open();
      await tick();
      inputOf().value = "create_part";
      inputOf().listeners.input[0]();
      key("Enter");
      await tick();
      const before = emitted.length;
      formResolve(null);                       // the user pressed Cancel
      await tick();
      out({refusedImmediate, refusedForm, cancelled: emitted.length - before,
           emitted, recent: JSON.parse(localStorage.getItem(
             "agentcad.palette.recent"))});
    """, prelude=PALETTE_UI)
    assert got["refusedImmediate"]["emitted"] == 1, "a refusal is still a run"
    assert got["refusedImmediate"]["recent"] == ["tool:list_parts"]
    assert got["refusedForm"]["emitted"] == 2, "…whatever the tool's arity"
    assert got["refusedForm"]["kept"] is False, "and the form still stays open"
    assert got["cancelled"] == 0, "a cancelled form invoked nothing"
    assert got["emitted"] == [["palette_executed", {"action": "tool:list_parts"}],
                              ["palette_executed", {"action": "tool:create_part"}]]
    # Most recent first, and the cancelled row is not in there at all.
    assert got["recent"] == ["tool:create_part", "tool:list_parts"]


def test_a_word_late_in_a_long_tool_description_is_still_findable():
    """M3: real tool descriptions run past 600 characters (`set_part_configs`
    is ~640), and a 200-char scoring cap made the palette — the DISCOVERY
    surface — unable to find a tool by a word in the second half of its own
    description. The clamp to >= 1 is what keeps such a match a match at all;
    it ranks last among matches, which is the honest answer."""
    got = run_palette("""
      const long = "z".repeat(250) + " widget";
      const longer = "x".repeat(600) + " preset here";
      out({late: pm.score("widget", long),
           later: pm.score("preset", longer),
           // …and a match past the (raised) cap is still honestly absent
           beyond: pm.score("needle", "y".repeat(2100) + " needle"),
           ranked: pm.rank("widget", [
             {id: "a", section: "tools", title: "zzz", description: long},
             {id: "b", section: "tools", title: "widget_maker"},
           ], []).map((e) => e.id)});
    """)
    assert got["late"] > 0 and got["later"] > 0
    assert got["beyond"] == 0, "the cap is raised, not removed"
    assert got["ranked"] == ["b", "a"], "a title hit still beats a late one"


def test_score_survives_a_lowercase_that_changes_the_texts_length():
    """M4: `"İ".toLowerCase()` is TWO characters, so a bonus table indexed off
    the original text desyncs from the lowercased one it is read against —
    `bonus[j]` becomes `undefined` and poisons the cell with `NaN`, silently
    dropping a match. The camel-hump lane is skipped when the lengths differ."""
    got = run_palette("""
      out({turkish: pm.score("ist", "İstanbul"),
           inner: pm.score("sta", "İstanbul"),
           // the camel hump still scores when the lengths DO agree
           camel: pm.score("pi", "partId") > pm.score("pi", "pxxxxi")});
    """)
    assert got["turkish"] > 0 and got["inner"] > 0
    assert got["camel"] is True


def test_an_integer_arg_keeps_its_step_when_the_schema_is_a_type_union():
    """M5: `{"type": ["integer", "null"]}` already renders as a number field;
    without the normalized type it lost `step`, and with it the multiple-of
    check `dialogs_model.validate` runs."""
    got = run_palette("""
      const f = pm.formFields({properties: {
        n: {type: ["integer", "null"]}, r: {type: ["number", "null"]},
      }}, {});
      out(f.map((x) => [x.name, x.type, x.step === undefined ? null : x.step]));
    """)
    assert got == [["n", "number", 1], ["r", "number", None]]


# ============================================================================
# Slice 2 — every native prompt/confirm is gone; the legacy modals adopt the shell
# ============================================================================
#
# AC1 is a grep, and it is the whole point of the slice: a native `prompt()` is
# a modal the shell does not know about, cannot theme, cannot trap focus in and
# cannot let an agent open. The rest of this section grades the two things the
# grep cannot see — that each replaced site names a *view* (so `ui_open`, the
# palette and the UX events can reach it) and that the adopted overlays really
# move the shell's stack.

FRONTEND_JS = Path(__file__).resolve().parents[1] / "frontend" / "js"

# `\b` is not enough: it would match `dialogs.prompt(`. The lookbehind is what
# makes this a test of NATIVE calls — a member call is somebody's method. All
# four spellings of the global object are covered, because this regex IS the
# enforcement of AC1 and `globalThis.confirm(` is the same native dialog.
NATIVE_DIALOG_RE = re.compile(
    r"(?<![.\w])(?:(?:window|self|globalThis|top)\.)?(prompt|confirm|alert)\s*\(")


def _native_dialog_hits(path: Path) -> list[str]:
    hits = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith(("//", "*", "/*")):
            continue           # prose about the ban is not a violation of it
        for match in NATIVE_DIALOG_RE.finditer(line):
            before = line[:match.start()]
            # `export function confirm(` in shell/dialogs.js IS the replacement.
            if re.search(r"function\s+$", before):
                continue
            hits.append(f"{path}:{lineno}: {stripped}")
    return hits


def test_no_native_dialogs_remain():
    """AC1: not one `prompt(`/`confirm(`/`alert(` anywhere in `frontend/js`."""
    files = sorted(FRONTEND_JS.rglob("*.js"))
    assert len(files) > 20, "the glob stopped finding the frontend"
    hits = [hit for path in files for hit in _native_dialog_hits(path)]
    assert hits == [], "native browser dialogs survived:\n" + "\n".join(hits)


# The spec §1.4 mapping, as data: every site the plan names became a dialog and
# every dialog carries the view name the registry, the palette and the
# `dialog_opened` event key on.
SLICE2_VIEWS = {
    "discard-edits": "main.js",
    "new-part": "main.js",
    "delete-part": "main.js",
    "import-part-id": "main.js",
    "new-project": "main.js",
    "open-project": "main.js",
    "new-branch": "main.js",
    "delete-branch": "main.js",
    "new-version": "versions.js",
    "restore-version": "versions.js",
    "merge-abort": "merge.js",
    "review-summary": "proposals.js",
    "edit-proposal": "proposals.js",
    "market-add-to-project": "market.js",
    "sketch-number": "sketcher.js",
}

# The adopted `.modal-overlay`s (`index.html`), each with the module that owns
# it. All NINE — which is what lets `isModalOpen()` drop its DOM fallback.
# `materials` is the ninth: PRD-028 landed it on `main` while this branch was in
# flight, the merge adopted it by hand, and nothing here graded that until the
# closure test below existed (final review, I2).
ADOPTED_MODALS = {
    "bom": "bom.js",
    "releases": "releases.js",
    "drawing": "drawings.js",
    "versions": "versions.js",
    "share": "share-links.js",
    "merge": "merge.js",
    "proposals": "proposals.js",
    "library": "library.js",
    "configs": "configs.js",
    "notifications": "comments.js",
    "materials": "materials.js",
}

# `<div id="…" class="modal-overlay …">` — the ten-overlay sweep the reviewer
# did by hand, as a test.
MODAL_OVERLAY_RE = re.compile(r'<div\s+id="([\w-]+)"\s+class="modal-overlay')

# Overlay ids that are deliberately NOT on the stack, each with its reason.
# EMPTY today (`#claim-modal`, the last one, was dead markup and is deleted),
# and the mechanism is kept exactly for that reason: the next overlay someone
# adds must either adopt or be named here on purpose.
OVERLAY_EXEMPT: dict[str, str] = {}

# Views reachable from the palette's "Open: …" rows and (where flagged) from
# `ui_open`. A view is registered only when opening it OUT OF CONTEXT is a
# coherent request — `sketch-number` names an entity you are mid-way through
# drawing, so it is a dialog with a view and deliberately no registry row.
REGISTERED_VIEWS = {
    "new-part": "main.js",
    "delete-part": "main.js",
    "new-project": "main.js",
    "open-project": "main.js",
    "new-branch": "main.js",
    "delete-branch": "main.js",
    "new-version": "versions.js",
    "new-proposal": "proposals.js",
    "edit-proposal": "proposals.js",
    "market-add-to-project": "market.js",
    "merge-abort": "merge.js",
}

NOT_REGISTERED = ["discard-edits", "import-part-id", "restore-version",
                  "review-summary", "sketch-number"]


@pytest.mark.parametrize("view,module", sorted(SLICE2_VIEWS.items()))
def test_every_replaced_call_site_names_its_view(view, module):
    source = (FRONTEND_JS / module).read_text(encoding="utf-8")
    assert f'view: "{view}"' in source, f"{module} has no {view} dialog"


@pytest.mark.parametrize("view,module", sorted(REGISTERED_VIEWS.items()))
def test_every_openable_view_is_registered(view, module):
    source = (FRONTEND_JS / module).read_text(encoding="utf-8")
    assert f'dialogs.register("{view}"' in source, f"{view} is not registered"


@pytest.mark.parametrize("view,module", sorted(ADOPTED_MODALS.items()))
def test_every_legacy_overlay_is_adopted_and_registered(view, module):
    """Adoption is three things in one call: the stack entry, the registry row
    and the deletion of the module's own Escape listener."""
    source = (FRONTEND_JS / module).read_text(encoding="utf-8")
    assert "dialogs.attachLegacy(" in source, f"{module} did not adopt"
    assert f'view: "{view}"' in source, f"{module} adopted under another name"
    assert "notifyOpen()" in source and "notifyClose()" in source, module
    # The module-level Escape listener is what adoption REPLACES; an
    # element-scoped one (comments.js's composer popover) is not a modal.
    assert 'document.addEventListener("keydown"' not in source, (
        f"{module} still owns a document-level Escape listener")


@pytest.mark.parametrize("view", NOT_REGISTERED)
def test_a_mid_flow_dialog_is_not_offered_as_an_openable_view(view):
    """The other half of the rule: a registry row with nothing behind it is a
    menu that lies, so a dialog that needs context nobody can pass gets none."""
    for path in FRONTEND_JS.rglob("*.js"):
        assert f'dialogs.register("{view}"' not in path.read_text(
            encoding="utf-8"), f"{view} is not openable out of context"


def test_every_modal_overlay_in_index_html_is_adopted_or_named_exempt():
    """The CLOSURE of `ADOPTED_MODALS` — the invariant `isModalOpen()`'s deleted
    DOM fallback rests on.

    Without this, the list is hand-maintained: PRD-028 added a tenth overlay on
    `main`, the merge adopted it correctly by hand, and nothing in the suite
    would have complained if it hadn't — an unadopted overlay is silently
    `isModalOpen() === false`, so `F`/`G`/`R`/`?` fire behind it.
    """
    ids = set(MODAL_OVERLAY_RE.findall(INDEX.read_text(encoding="utf-8")))
    assert len(ids) >= 9, f"the overlay sweep stopped finding them: {ids}"
    adopted = {f"{view}-modal" for view in ADOPTED_MODALS}
    unknown = sorted(ids - adopted - set(OVERLAY_EXEMPT))
    assert unknown == [], (
        "these `.modal-overlay`s are on no dialog stack — adopt them with "
        f"`dialogs.attachLegacy` or name them in OVERLAY_EXEMPT: {unknown}")
    missing = sorted(adopted - ids)
    assert missing == [], f"ADOPTED_MODALS names overlays index.html lacks: {missing}"
    assert "claim-modal" not in ids, "the dead claim modal markup is back"


def test_the_modal_overlay_dom_fallback_is_gone_now_that_all_nine_are_adopted():
    """Slice 1 left `isModalOpen()` querying `.modal-overlay:not(.hidden)`
    because the hand-rolled overlays were not on the stack. They are now, so
    the fallback would only paper over an adopter that forgot `notifyOpen`."""
    source = (FRONTEND_JS / "shell" / "dialogs.js").read_text(encoding="utf-8")
    body = source.split("export function isModalOpen()", 1)[1].split("}", 1)[0]
    assert ".modal-overlay" not in body
    assert "stackEntries.some" in body
    index = INDEX.read_text(encoding="utf-8")
    assert "has not landed" not in index, "the index.html promise is still there"
    # And nobody hides an adopted overlay behind the stack's back any more.
    comments = (FRONTEND_JS / "comments.js").read_text(encoding="utf-8")
    assert '.modal-overlay:not(.hidden)' not in comments
    assert "dialogs.closeModals()" in comments


def test_the_delete_branch_action_is_registered_now_that_it_has_a_picker():
    """Slice 1 could not register `model.branches.delete`: an argument-free
    delete names the branch you are on, which the server always refuses."""
    main = MAIN.read_text(encoding="utf-8")
    assert '"model.branches.delete"' in main
    row = main.split('id: "model.branches.delete"', 1)[1].split("A({", 1)[0]
    assert "danger: true" in row
    assert "c.hasOtherBranches" in row, "the row must vanish with nothing to delete"
    assert "deleteBranchPrompt()" in row


def test_the_space_bar_does_not_blow_up_the_global_keydown_listener():
    """Found in the browser, not in a unit test: `fromEvent` spelled the space
    bar `" "` (a truthy string), `Table.lookup` handed it to `normalize`, and
    `parseChord` trimmed it to nothing and threw "empty shortcut chord" out of
    an untried document listener — on EVERY space keystroke in the app. Same
    family as the `+` keystroke (0271 review round 1)."""
    got = run_js("""
      out({fromEvent: sc.fromEvent({key: " "}, "MacIntel"),
           shifted: sc.fromEvent({key: " ", metaKey: true}, "MacIntel"),
           normalize: sc.normalize("space"),
           macLabel: sc.label("Space", "MacIntel"),
           pcLabel: sc.label("Mod+Space", "Win32")});
    """)
    assert got["fromEvent"] == "Space"
    assert got["shifted"] == "Mod+Space"
    assert got["normalize"] == "Space"
    assert got["macLabel"] == "␣" and got["pcLabel"] == "Ctrl+Space"


def test_a_space_keystroke_reaches_the_shipped_listener_without_throwing():
    """The regression as the user meets it: the shipped `onKeyDown`, a bare
    space, nothing bound to it — and no exception."""
    got = run_js("""
      shortcuts.reset();
      let threw = null;
      try {
        press({key: " "});
      } catch (err) { threw = String(err && err.message); }
      out({threw, prevented});
    """, prelude=DISPATCH)
    assert got["threw"] is None, got["threw"]
    assert got["prevented"] == 0, "an unbound space is the browser's"


def test_new_part_takes_the_mod_n_chord_the_spec_ruled_on():
    """Spec ruling 3. It is `when`-gated, not `enabled`-gated: with no project
    there is nothing to create, so the chord declines and the browser keeps
    ⌘N rather than us swallowing a keystroke we cannot act on."""
    main = MAIN.read_text(encoding="utf-8")
    row = main.split('id: "part.new"', 1)[1].split("A({", 1)[0]
    assert 'shortcut: "Mod+N"' in row
    assert "when: hasProject" in row and "enabled:" not in row


# ---------------------------------------------- behaviour, against the DOM stub

# Enough of a document for the three things worth driving in node: the delete
# confirm's blast-radius text, the branch picker's option list, and an adopted
# overlay moving the stack. Every element records what was written into it, so
# the assertions read the strings a user would.
SLICE2_DOM = f"""
const nodes = [];
const make = (tag) => {{
  const el = {{
    tag, id: "", className: "", value: "", checked: false, disabled: false,
    dataset: {{}}, attrs: {{}}, children: [], text: "", innerHTML: "",
    parent: null, listeners: {{}}, offsetParent: {{}},
    get isConnected() {{ return true; }},
    set textContent(v) {{ this.text = v; }},
    get textContent() {{ return this.text; }},
    setAttribute(k, v) {{ this.attrs[k] = v; }},
    removeAttribute(k) {{ delete this.attrs[k]; }},
    appendChild(c) {{ c.parent = this; this.children.push(c); return c; }},
    addEventListener(n, fn) {{ this.listeners[n] = fn; }},
    focus() {{ globalThis.document.activeElement = this; }},
    contains() {{ return false; }},
    remove() {{
      if (!this.parent) return;
      this.parent.children = this.parent.children.filter((c) => c !== this);
      this.parent = null;
    }},
    querySelector() {{ return null; }},
    querySelectorAll() {{ return []; }},
    get firstElementChild() {{ return this.children[0] || null; }},
    classList: {{
      _s: new Set(),
      add(c) {{ this._s.add(c); }}, remove(c) {{ this._s.delete(c); }},
      toggle(c, on) {{ if (on) this._s.add(c); else this._s.delete(c); }},
      contains(c) {{ return this._s.has(c); }},
    }},
  }};
  nodes.push(el);
  return el;
}};
// `open()` parses its markup string through innerHTML, which this stub cannot
// do — so the host records the HTML instead and the assertions read THAT.
const host = make("div");
let lastHtml = "";
Object.defineProperty(host, "appendChild", {{
  value(child) {{ child.parent = host; host.children.push(child); return child; }},
}});
const frag = make("div");
Object.defineProperty(frag, "innerHTML", {{
  get() {{ return lastHtml; }},
  set(v) {{ lastHtml = v; this.children = [make("div")]; }},
}});
globalThis.document = {{
  activeElement: null,
  body: {{ appendChild() {{}} }},
  getElementById: () => host,
  createElement: () => frag,
  addEventListener() {{}},
  querySelector: () => null,
}};
globalThis.setTimeout = (fn, ms) => 1;
globalThis.clearTimeout = () => {{}};
const dialogs = await import({_uri("dialogs.js")});
const out = (v) => process.stdout.write(JSON.stringify(v));
"""


def test_the_delete_part_confirm_names_the_assembly_instances_it_takes():
    """Spec §1.4: the blast radius is named BEFORE the click, not discovered
    after it. The dialog is built here exactly the way `main.js` builds it."""
    got = run_js("""
      const instances = [{id: "left"}, {id: "right"}, {id: "spare"}];
      dialogs.confirm({
        view: "delete-part",
        title: 'Delete part \\u201cplate\\u201d?',
        body: "Deletes plate and its script file.",
        note: `Also removes ${instances.length} assembly instance`
          + `${instances.length === 1 ? "" : "s"}: `
          + instances.map((i) => i.id).join(", "),
        danger: true, confirmLabel: "Delete part",
      });
      out({html: lastHtml, stack: dialogs.stack()});
    """, prelude=SLICE2_DOM)
    assert "Also removes 3 assembly instances: left, right, spare" in got["html"]
    assert "Deletes plate and its script file." in got["html"]
    assert 'class="dlg narrow danger"' in got["html"]
    assert ">Delete part<" in got["html"]
    assert [e["view"] for e in got["stack"]] == ["delete-part"]


def test_the_branch_picker_offers_every_branch_but_the_current_and_default():
    """The server refuses both, and a row that can only fail is not a choice."""
    got = run_js("""
      const branches = [
        {name: "main", is_default: true, is_current: false},
        {name: "wip", is_default: false, is_current: true},
        {name: "spike", is_default: false, is_current: false},
        {name: "old", is_default: false, is_current: false},
      ];
      const deletable = branches.filter((b) => !b.is_current && !b.is_default);
      dialogs.form({
        view: "delete-branch", title: "Delete a branch", danger: true,
        fields: [{name: "branch", label: "Branch", type: "select",
                  options: deletable.map((b) => b.name),
                  value: deletable[0].name, required: true}],
        buttons: [{id: "cancel", label: "Cancel"},
                  {id: "delete", label: "Delete branch", kind: "danger",
                   submits: true}],
      });
      out({html: lastHtml});
    """, prelude=SLICE2_DOM)
    html = got["html"]
    assert '<option value="spike" selected>' in html
    assert '<option value="old">' in html
    assert ">main<" not in html and ">wip<" not in html


def test_an_adopted_overlay_moves_the_stack_and_carries_its_when_predicate():
    """Adoption is what makes `isModalOpen()` true behind a legacy modal now
    that the DOM fallback is gone — and the `when` an adopter declares has to
    reach the registry, or every adopted view is offered unconditionally."""
    got = run_js("""
      const overlay = {querySelector: () => null, contains: () => false};
      let open_ = false;
      const legacy = dialogs.attachLegacy(overlay, {
        view: "merge", title: "Staged merge…",
        isOpen: () => open_, onClose: () => { open_ = false; },
        open: () => { open_ = true; },
        when: (c) => !!c.staged,
      });
      const before = dialogs.isModalOpen();
      open_ = true; legacy.notifyOpen();
      const during = {modal: dialogs.isModalOpen(),
                      stack: dialogs.stack().map((e) => [e.view, e.kind])};
      open_ = false; legacy.notifyClose();
      out({before, during, after: dialogs.isModalOpen(),
           offered: dialogs.views({staged: true}).map((v) => v.view),
           hidden: dialogs.views({staged: false}).map((v) => v.view)});
    """, prelude=SLICE2_DOM)
    assert got["before"] is False
    assert got["during"] == {"modal": True, "stack": [["merge", "legacy"]]}
    assert got["after"] is False
    assert got["offered"] == ["merge"] and got["hidden"] == []


def test_close_modals_pops_a_legacy_entry_through_its_own_close():
    """`comments.showThread()` used to hide the covering overlay by hand; with
    the overlay on the stack that stranded the entry — `isModalOpen()` true
    forever and every global shortcut dead."""
    got = run_js("""
      const overlay = {querySelector: () => null, contains: () => false};
      let closed = 0;
      const legacy = dialogs.attachLegacy(overlay,
        {view: "proposals", onClose: () => { closed += 1; }});
      legacy.notifyOpen();
      const n = dialogs.closeModals();
      out({n, closed, stack: dialogs.stack(), modal: dialogs.isModalOpen(),
           isOpen: legacy.isOpen()});
    """, prelude=SLICE2_DOM)
    assert got == {"n": 1, "closed": 1, "stack": [], "modal": False,
                   "isOpen": False}


def test_a_number_prompt_resolves_a_string_so_the_sketcher_coerces_it():
    """Slice 1 decision 9. `askNumber` in `sketcher.js` is the one place that
    does the coercion, and `""` (blank = leave the semi-axis free) must survive
    it as `""` and not become `0`."""
    sketcher = (FRONTEND_JS / "sketcher.js").read_text(encoding="utf-8")
    body = sketcher.split("async function askNumber(", 1)[1].split("\n}", 1)[0]
    assert 'if (answer === "") return "";' in body
    assert "return Number(answer);" in body
    # The ellipse is one form with two OPTIONAL fields, not two prompts.
    ellipse = sketcher.split("Ellipse radii", 1)[1].split("});", 1)[0]
    assert ellipse.count("required: false") == 2
    assert '{ name: "a"' in ellipse and '{ name: "b"' in ellipse


def test_the_discard_guard_is_awaited_at_every_call_site():
    """`confirmDiscardEdits` returns a PROMISE now: an un-awaited call is
    truthy always, which silently discards the user's unsaved script."""
    main = MAIN.read_text(encoding="utf-8")
    calls = re.findall(r"[^\s(]*\s*confirmDiscardEdits\(", main)
    assert calls, "the guard vanished"
    for line in main.splitlines():
        if "confirmDiscardEdits(" in line and "async function" not in line:
            assert "await confirmDiscardEdits(" in line, line.strip()


# ============================================================================
# Slice 2 — fix round 1
# ============================================================================

# I1. The space-bar fix closed one instance of a family. The family is
# structural: `fromEvent` builds a chord from whatever the browser called the
# key, and `Table.lookup` handed that straight back to `normalize` — an
# AUTHOR-facing parser that throws on shapes only an author could get wrong —
# from inside an untried document listener. Patching `keyName` one key at a
# time would have kept finding new ones (`Super` was the next).

# Every UI Events "modifier key" value, plus the grab-bag the reviewer named.
KEY_SWEEP = [
    "Alt", "AltGraph", "CapsLock", "Control", "Fn", "FnLock", "Hyper", "Meta",
    "NumLock", "OS", "ScrollLock", "Shift", "Super", "Symbol", "SymbolLock",
    "Dead", "Process", "Unidentified", "Spacebar", " ", "+", "=", "?",
    "Escape", "Tab", "Enter", "Backspace", "Delete", "ArrowUp", "F5",
    "a", "Z", "1", "ø", "İ", "",
]


def test_no_key_a_browser_can_report_throws_out_of_the_keydown_listener():
    """I1, the sweep: every key value × every modifier combination, driven
    through the SHIPPED `fromEvent` → `Table.lookup` with the scope index
    populated (so `lookup` really reaches the parser). Nothing may throw."""
    got = run_js("""
      const table = new sc.Table();
      table.bind({chord: "Mod+S", id: "part.save-script"});   // the scope exists
      const keys = %s;
      const bad = [];
      const chords = {};
      for (const key of keys) {
        for (const mods of [{}, {metaKey: true}, {altKey: true},
                            {shiftKey: true}, {ctrlKey: true},
                            {metaKey: true, altKey: true, shiftKey: true}]) {
          for (const platform of ["MacIntel", "Win32"]) {
            let chord = null;
            try {
              chord = sc.fromEvent({key, ...mods}, platform);
              table.lookup(chord, "global");
              table.lookup(chord, "modal-safe");
            } catch (err) {
              bad.push([key, JSON.stringify(mods), platform, chord,
                        String(err && err.message)]);
            }
          }
        }
        chords[key] = sc.fromEvent({key}, "MacIntel");
      }
      out({bad, chords});
    """ % json.dumps(KEY_SWEEP))
    assert got["bad"] == [], "these keystrokes throw out of the listener"
    # And the two that used to: a bare modifier is not a chord at all, the
    # space bar is `Space`.
    for modifier in ("Super", "Hyper", "Meta", "Control", "Fn", "CapsLock",
                     "Dead", "Unidentified"):
        assert got["chords"][modifier] is None, modifier
    assert got["chords"][" "] == "Space"


def test_lookup_answers_a_miss_where_normalize_would_have_thrown():
    """The structural half, stated directly: `canonical` is the machine path
    and a chord it cannot parse is a MISS. `normalize` still throws, because a
    binding an author wrote wrong must fail the build."""
    got = run_js("""
      const table = new sc.Table();
      const row = table.bind({chord: "mod+s", id: "part.save-script"});
      let normalizeThrew = null;
      try { sc.normalize("Mod+Super"); }
      catch (err) { normalizeThrew = String(err.message); }
      out({
        stored: row.chord,
        hit: (table.lookup("Mod+S") || {}).id,
        garbage: [table.lookup("Mod+Super"), table.lookup(" "),
                  table.lookup(""), table.lookup(null), table.lookup("Shift")],
        canonical: [sc.canonical("mod+s"), sc.canonical("Mod+Super"),
                    sc.canonical("")],
        normalizeThrew,
        unknownScope: table.lookup("Mod+S", "nope"),
      });
    """)
    assert got["stored"] == "Mod+S" and got["hit"] == "part.save-script"
    assert got["garbage"] == [None, None, None, None, None]
    assert got["canonical"] == ["Mod+S", None, None]
    assert got["normalizeThrew"] is not None, "normalize must still throw"
    assert got["unknownScope"] is None


# I2. Adopting `#merge-modal` put the stack's Tab trap over a live CodeMirror
# (merge.js:409 opens it `readOnly: false` on Python part scripts). The
# listener is in the CAPTURE phase, so it took Tab before CodeMirror saw it.

TAB_DOM = f"""
globalThis.document = {{
  activeElement: null,
  body: {{ appendChild() {{}} }},
  getElementById: () => null,
  createElement: () => ({{ id: "", appendChild() {{}} }}),
  addEventListener() {{}},
  querySelector: () => null,
}};
const dialogs = await import({_uri("dialogs.js")});
const out = (v) => process.stdout.write(JSON.stringify(v));
const focusable = (name) => ({{
  name, offsetParent: {{}},
  focus() {{ globalThis.document.activeElement = this; }},
}});
const buttons = [focusable("cancel"), focusable("ok")];
const overlay = {{
  querySelector: () => null,
  querySelectorAll: () => buttons,
  contains: () => true,
}};
// A target whose `closest` answers the way the real DOM would for a node
// inside (or outside) a CodeMirror.
const target = (sel) => ({{
  closest: (q) => (q.split(", ").some((s) => sel.includes(s.trim()))
    ? {{}} : null),
}});
const tab = (t, shift) => {{
  let prevented = 0;
  dialogs.__dialogsDispatch__.onKeyDown({{
    key: "Tab", shiftKey: !!shift, target: t,
    preventDefault() {{ prevented += 1; }}, stopPropagation() {{}},
  }});
  return prevented;
}};
"""


def test_tab_indents_in_a_code_editor_inside_an_adopted_modal():
    """I2: the merge conflict resolver is Python, where Tab-to-indent is not a
    nicety. `input`/`textarea` stay trapped — they do not consume Tab, so for
    them the trap is the only thing keeping focus inside the dialog."""
    got = run_js("""
      const legacy = dialogs.attachLegacy(overlay, {view: "merge"});
      legacy.notifyOpen();
      out({
        codemirror: tab(target(".CodeMirror")),
        contenteditable: tab(target("[contenteditable]")),
        shiftInCodemirror: tab(target(".CodeMirror"), true),
        textarea: tab(target("textarea")),
        button: tab(target("button")),
        noTarget: tab(null),
        focused: (globalThis.document.activeElement || {}).name,
      });
    """, prelude=TAB_DOM)
    assert got["codemirror"] == 0, "the trap swallowed Tab in the editor"
    assert got["contenteditable"] == 0 and got["shiftInCodemirror"] == 0
    # Everything else still cycles.
    assert got["textarea"] == 1 and got["button"] == 1 and got["noTarget"] == 1
    assert got["focused"] in ("cancel", "ok")


# I3. `delete-branch` is the one destructive dialog with a field, and it was a
# stray Enter (the palette runs its rows on keydown) from deleting a working
# tree. Both halves are fixed: focus, and the form's own validity.


# A stub with enough of a selector engine to answer the three queries
# `focusFirst` makes (`#<field id>`, `[data-submits]`, `[data-btn]:not(…)`).
# It builds its nodes from the markup string `dialogs_model` produced, so the
# ids under test are the shipped ones and not a second guess at them.
FOCUS_DOM = f"""
const nodes = new Map();
const node = (id) => {{
  if (!nodes.has(id)) {{
    nodes.set(id, {{
      id, disabled: false, value: "", checked: false, textContent: "",
      dataset: {{}},
      setAttribute() {{}}, removeAttribute() {{}},
      addEventListener() {{}},
      classList: {{ toggle() {{}}, add() {{}}, remove() {{}},
                   contains: () => false }},
      querySelector: () => null, querySelectorAll: () => [],
      focus() {{ globalThis.document.activeElement = this; }},
      remove() {{}},
    }});
  }}
  return nodes.get(id);
}};
let lastHtml = "";
const buttonsIn = (html) => [...html.matchAll(/<button[^>]*>/g)].map((m) => ({{
  id: /id="([^"]+)"/.exec(m[0])[1], submits: m[0].includes("data-submits"),
}}));
const makeOverlay = (html) => {{
  const buttons = buttonsIn(html);
  const el = {{
    querySelector(sel) {{
      if (sel.startsWith("#")) {{
        const id = sel.slice(1);
        return html.includes(`id="${{id}}"`) ? node(id) : null;
      }}
      if (sel === "[data-submits]") {{
        const b = buttons.find((x) => x.submits);
        return b ? node(b.id) : null;
      }}
      if (sel === "[data-btn]:not([data-submits])") {{
        const b = buttons.find((x) => !x.submits);
        return b ? node(b.id) : null;
      }}
      if (sel === "[data-btn]") return buttons.length ? node(buttons[0].id) : null;
      if (sel === ".dlg") return node("dlg-el");
      return null;
    }},
    querySelectorAll: () => [],
    addEventListener() {{}},
    contains: () => false,
    remove() {{}},
  }};
  return el;
}};
const frag = {{
  set innerHTML(v) {{ lastHtml = v; this._el = makeOverlay(v); }},
  get innerHTML() {{ return lastHtml; }},
  get firstElementChild() {{ return this._el; }},
}};
globalThis.document = {{
  activeElement: null,
  body: {{ appendChild() {{}} }},
  getElementById: () => ({{ appendChild() {{}} }}),
  createElement: () => frag,
  addEventListener() {{}},
  querySelector: () => null,
}};
const dialogs = await import({_uri("dialogs.js")});
const out = (v) => process.stdout.write(JSON.stringify(v));
const focused = () => (globalThis.document.activeElement || {{}}).id || null;
"""


def test_a_danger_dialog_with_fields_still_opens_on_its_safe_button():
    got = run_js("""
      dialogs.form({
        view: "delete-branch", title: "Delete a branch", danger: true,
        fields: [{name: "branch", label: "Branch", type: "select",
                  options: [{value: "", label: "— choose a branch —"}, "old"],
                  value: "", required: true}],
        buttons: [{id: "cancel", label: "Cancel"},
                  {id: "delete", label: "Delete branch", kind: "danger",
                   submits: true}],
      });
      const safe = focused();
      dialogs.form({
        view: "new-part", title: "New part",
        fields: [{name: "id", label: "Part id", required: true}],
      });
      out({danger: safe, ordinary: focused()});
    """, prelude=FOCUS_DOM)
    assert got["danger"] == "dlg-btn-1-cancel", (
        "a danger dialog must not open on a submittable field")
    # An ordinary form still opens on its first field.
    assert got["ordinary"] == "dlg-f-2-id"


def test_the_branch_picker_opens_unchosen_so_enter_cannot_submit_it():
    """The second half: even with focus elsewhere, the form itself must be
    invalid until somebody deliberately picks a branch."""
    main = MAIN.read_text(encoding="utf-8")
    picker = main.split("view: \"delete-branch\",\n    title: \"Delete a branch\"",
                        1)[1].split("});", 1)[0]
    assert 'value: ""' in picker and "required: true" in picker
    assert "choose a branch" in picker
    assert "deletable[0].name" not in picker, "the picker is preseeded again"
    # …and an empty required select really is invalid, so the primary is off.
    got = run_js("""
      out(dm.validate(
        [{name: "branch", type: "select", required: true}], {branch: ""}));
    """)
    assert got["valid"] is False and got["errors"]["branch"] == "Required"


# M-items.


def test_close_modals_counts_what_left_the_stack_and_spares_a_non_modal():
    """M3: the count is now `before - after`, taken from the stack rather than
    from inside the loop, so it can only report what actually closed. The
    non-modal is the discriminating case — a "get this out of the way" verb
    must not sweep away a floating tool result nobody said was in the way."""
    got = run_js("""
      dialogs.open({view: "tool-result", modal: false, title: "Result"});
      const overlay = {querySelector: () => null, contains: () => false};
      const a = dialogs.attachLegacy(overlay, {view: "configs", onClose(){}});
      const b = dialogs.attachLegacy(overlay, {view: "library", onClose(){}});
      a.notifyOpen(); b.notifyOpen();
      out({closed: dialogs.closeModals(),
           left: dialogs.stack().map((e) => e.view),
           again: dialogs.closeModals()});
    """, prelude=FOCUS_DOM)
    assert got["closed"] == 2
    assert got["left"] == ["tool-result"], "the non-modal was swept away"
    assert got["again"] == 0, "closing nothing reported closing something"


def test_a_non_numeric_step_is_no_step_rather_than_nan_arithmetic():
    """M7: `step: "any"` is the HTML spelling of "free decimal" (a millimetre
    field wants it) and it used to reach the multiple-of check and pass by
    accident — `(num - base) / "any"` is NaN and `NaN > 1e-9` is false."""
    got = run_js("""
      const check = (step, v) => dm.validate(
        [{name: "r", type: "number", min: 0, step}], {r: v}).errors.r || null;
      out({any: check("any", 12.5), missing: check(undefined, 12.5),
           zero: check(0, 12.5), junk: check("wat", 12.5),
           real: check(0.5, 12.25), realOk: check(0.5, 12.5)});
    """)
    assert got["any"] is None and got["missing"] is None
    assert got["zero"] is None and got["junk"] is None
    # A real step is still enforced.
    assert got["real"] == "Must be a multiple of 0.5" and got["realOk"] is None


def test_the_identifier_rules_have_exactly_one_spelling_in_the_frontend():
    """M1: `market.js` hand-copied the part-id pattern into a dialog spec —
    correct by luck, and exactly the drift the slice claimed to have avoided.
    Every rule now comes from `frontend/js/patterns.js`."""
    patterns = (FRONTEND_JS / "patterns.js").read_text(encoding="utf-8")
    for name, source in [("ID_RE", "[a-z][a-z0-9_]{0,39}"),
                         ("BRANCH_RE", "[a-z0-9][a-z0-9_/-]{0,63}"),
                         ("TAG_RE", "[a-z0-9][a-z0-9._/-]{0,63}")]:
        assert f"export const {name} = /^{source}$/;" in patterns, name
    for module in ("main.js", "market.js", "versions.js"):
        source = (FRONTEND_JS / module).read_text(encoding="utf-8")
        assert 'from "./patterns.js"' in source, module
        # No module re-spells a rule it imports.
        assert "[a-z][a-z0-9_]{0,39}" not in source, module
        assert "[a-z0-9][a-z0-9_" not in source, module
    # `bare()` strips the anchors `dialogs_model.validate` adds back.
    got = run_js("""
      out({id: p.bare(p.ID_RE), branch: p.bare(p.BRANCH_RE),
           tag: p.bare(p.TAG_RE),
           rejects: dm.validate([{name: "id", pattern: p.bare(p.ID_RE)}],
                                {id: "Bad Id!"}).valid,
           accepts: dm.validate([{name: "id", pattern: p.bare(p.ID_RE)}],
                                {id: "gusset_plate"}).valid});
    """, prelude=PRELUDE + f'import * as p from {_juri("patterns.js")};\n')
    assert got["id"] == "[a-z][a-z0-9_]{0,39}"
    assert got["branch"] == "[a-z0-9][a-z0-9_/-]{0,63}"
    assert got["tag"] == "[a-z0-9][a-z0-9._/-]{0,63}"
    assert got["rejects"] is False and got["accepts"] is True


# =====================================================================
# The final whole-branch review's fix wave (I1, I3, I4, m2, m3, m4, m5, D1).
# =====================================================================

def test_the_sketchers_delete_binding_is_dead_behind_a_modal():
    """I1 — the one real data-loss path the branch shipped with.

    `onKey` is a document-level listener that returned early only on `!open`
    and on an `input`/`textarea`/`.CodeMirror` target. `dialogs.onKeyDown`
    swallows Escape (capture + `stopPropagation`) but has NO Delete/Backspace
    branch, and a dialog `<button>` is not an `input` — so open the sketcher,
    select two entities, run the `distance` constraint, Tab to Cancel, press
    Backspace, and `deleteSelection()` silently destroyed the selection behind
    a dialog the user was about to cancel. Backspace-on-a-button is a reflex.
    """
    source = (FRONTEND_JS / "sketcher.js").read_text(encoding="utf-8")
    body = source.split("function onKey(e) {", 1)[1].split("\n}\n", 1)[0]
    guard = body.index("dialogs.isModalOpen()")
    assert guard < body.index('e.key === "Escape"'), (
        "the modal guard must come before every key branch")
    assert guard < body.index('e.key === "Delete"')
    assert "return" in body[guard:guard + 60], "the guard must RETURN"
    # And the docstring that claimed this was already true is now honest.
    assert "the Delete binding cannot fire" not in source
    assert "dialogs.isModalOpen()" in source.split(
        "async function askNumber", 1)[0].rsplit("/**", 1)[1]


# A DOM stub with enough of a browser to run BOTH dialog paths at once: the
# `open()` primitive (its markup is captured, so the attribution chip is
# readable) and an adopted legacy overlay (its `.modal-head` collects the chip
# `notifyOpen` inserts). `getElementById` answers null, so `toast()` no-ops and
# a refusal costs no timer.
ATTRIB_DOM = f"""
const htmls = [];
const parsed = (html) => {{
  const el = makeNode("div");
  el.querySelector = (sel) => {{
    if (sel === ".dlg") return makeNode("div");
    if (sel.startsWith("#")) {{
      return html.includes(`id="${{sel.slice(1)}}"`) ? makeNode("div") : null;
    }}
    return null;
  }};
  return el;
}};
function makeNode(tag) {{
  const n = {{
    tag, id: "", className: "", textContent: "", value: "", disabled: false,
    dataset: {{}}, attrs: {{}}, children: [], parent: null, _el: null,
    classList: {{ add() {{}}, remove() {{}}, toggle() {{}},
                 contains: () => false }},
    setAttribute(k, v) {{ n.attrs[k] = v; }},
    removeAttribute(k) {{ delete n.attrs[k]; }},
    addEventListener() {{}}, focus() {{}},
    appendChild(c) {{ c.parent = n; n.children.push(c); return c; }},
    insertBefore(c) {{ c.parent = n; n.children.unshift(c); return c; }},
    remove() {{
      if (!n.parent) return;
      n.parent.children = n.parent.children.filter((c) => c !== n);
      n.parent = null;
    }},
    querySelector: () => null,
    querySelectorAll: () => [],
    contains: () => false,
    get firstElementChild() {{ return n._el; }},
    set innerHTML(v) {{ htmls.push(v); n._el = parsed(v); }},
    get innerHTML() {{ return ""; }},
  }};
  return n;
}}
globalThis.document = {{
  activeElement: null,
  body: {{ appendChild() {{}} }},
  getElementById: () => null,
  createElement: (tag) => makeNode(tag),
  addEventListener() {{}},
  querySelector: () => null,
}};
const dialogs = await import({_uri("dialogs.js")});
const out = (v) => process.stdout.write(JSON.stringify(v));
// An adopted `.modal-overlay`: `.modal-head` is where the chip lands.
const legacyOverlay = () => {{
  const head = makeNode("header");
  head.querySelector = (sel) => (sel === ".dlg-attribution"
    ? (head.children[0] || null) : null);
  const ov = makeNode("div");
  ov.head = head;
  ov.querySelector = (sel) => {{
    if (sel === ".modal-head") return head;
    if (sel === ".modal-head .dlg-attribution") return head.children[0] || null;
    return null;
  }};
  return ov;
}};
const chipsOf = (ov) => ov.head.children.map((c) => [c.className, c.textContent]);
"""


def test_open_view_refuses_a_view_whose_own_when_is_false():
    """I3 — `when` gated the menu but not the door.

    `views(ctx)` filtered the palette's "Open: …" rows on it; `openView` checked
    the registry and `agentOpenable` and nothing else, so `ui_open {view:
    "merge"}` on a clean branch ran `merge.openPicker()` — which STARTS a new
    merge, as merge.js's own comment says.
    """
    got = run_js("""
      let ran = 0;
      dialogs.register("merge", () => { ran += 1; return "picker"; },
                       {title: "Staged merge…", when: (c) => !!c.staged});
      dialogs.register("plain", () => { ran += 1; return "plain"; },
                       {title: "No precondition"});
      dialogs.setContext(() => ({}));
      const refusedAgent = await dialogs.openView("merge", {}, {by: "agent"});
      const refusedUser = await dialogs.openView("merge", {}, {by: "user"});
      const plain = await dialogs.openView("plain", {}, {by: "agent"});
      const ranWhileForbidden = ran;
      dialogs.setContext(() => ({staged: true}));
      const allowed = await dialogs.openView("merge", {}, {by: "agent"});
      out({refusedAgent, refusedUser, plain, ranWhileForbidden, allowed, ran});
    """, prelude=ATTRIB_DOM)
    assert got["refusedAgent"] == {"ok": False, "reason": "not_available",
                                  "view": "merge"}
    # A USER route (the palette's own view branch) is refused identically.
    assert got["refusedUser"] == got["refusedAgent"]
    assert got["ranWhileForbidden"] == 1, "only `plain` should have run"
    assert got["plain"]["ok"] is True
    assert got["allowed"] == {"ok": True, "view": "merge", "result": "picker"}
    assert got["ran"] == 2


def test_a_view_with_no_context_injected_is_still_refused_rather_than_guessed():
    """The default context is `{}` — every `when` reading a project or a part is
    false, so an un-wired shell refuses instead of opening blind."""
    got = run_js("""
      dialogs.register("configs", () => "opened",
                       {when: (c) => !!c.selectedPart});
      const refused = await dialogs.openView("configs", {}, {by: "agent"});
      dialogs.setContext();                      // reset to the default
      const still = await dialogs.openView("configs", {}, {by: "agent"});
      out({refused, still});
    """, prelude=ATTRIB_DOM)
    assert got["refused"]["reason"] == "not_available"
    assert got["still"]["reason"] == "not_available"


def test_an_adopted_legacy_modal_shows_the_opened_by_agent_chip():
    """I4 — the attribution the user guide, `agent-api.md` and PRD-026's
    `ui_open` abuse mitigation all promise did not exist for ANY of the nine
    adopted legacy views: `pendingAttribution` is consumed by the `open()`
    primitive, and a legacy overlay never goes through it.

    Note the opener AWAITS before `notifyOpen()` — `materials.open()` fetches
    the catalog first — so a synchronously-scoped flag would have missed it.
    """
    got = run_js("""
      const ov = legacyOverlay();
      let handle;
      handle = dialogs.attachLegacy(ov, {
        view: "materials", title: "Materials…",
        open: async () => { await Promise.resolve(); handle.notifyOpen();
                            return "opened"; },
        onClose: () => handle.notifyClose(),
      });
      const res = await dialogs.openView("materials", {}, {by: "agent"});
      const opened = {res, attr: ov.attrs["data-agent-opened"] || null,
                      chips: chipsOf(ov)};
      handle.notifyClose();
      out({opened, attrAfter: ov.attrs["data-agent-opened"] || null,
           chipsAfter: chipsOf(ov)});
    """, prelude=ATTRIB_DOM)
    assert got["opened"]["res"] == {"ok": True, "view": "materials",
                                   "result": "opened"}
    assert got["opened"]["attr"] == "1"
    assert got["opened"]["chips"] == [["dlg-attribution", "opened by agent"]]
    # The chip is a property of THIS open, not of the overlay.
    assert got["attrAfter"] is None and got["chipsAfter"] == []


def test_a_user_opened_legacy_modal_carries_no_agent_chip():
    got = run_js("""
      const ov = legacyOverlay();
      let handle;
      handle = dialogs.attachLegacy(ov, {
        view: "library", open: async () => { handle.notifyOpen(); },
        onClose: () => handle.notifyClose(),
      });
      await dialogs.openView("library", {}, {by: "user"});
      const viaOpenView = {attr: ov.attrs["data-agent-opened"] || null,
                           chips: chipsOf(ov)};
      handle.notifyClose();
      // …and neither does the toolbar button's direct call.
      handle.notifyOpen();
      out({viaOpenView, direct: {attr: ov.attrs["data-agent-opened"] || null,
                                 chips: chipsOf(ov)}});
    """, prelude=ATTRIB_DOM)
    assert got["viaOpenView"] == {"attr": None, "chips": []}
    assert got["direct"] == {"attr": None, "chips": []}


def test_an_unrelated_dialog_opened_during_a_slow_agent_opener_is_not_stamped():
    """m10, whose window the merge widened from "a synchronous opener" to "a
    ~0.5 MB catalog fetch": `pendingAttribution` used to be cleared in a
    `finally` AFTER the awaited opener, so any unrelated `dialogs.open()` in
    that window was falsely stamped "opened by agent"."""
    got = run_js("""
      let release;
      const gate = new Promise((r) => { release = r; });
      const ov = legacyOverlay();
      let handle;
      handle = dialogs.attachLegacy(ov, {
        view: "materials",
        open: async () => { await gate; handle.notifyOpen(); return "late"; },
        onClose: () => handle.notifyClose(),
      });
      const pending = dialogs.openView("materials", {}, {by: "agent"});
      // The agent's opener is still awaiting its fetch. Something the user did
      // opens a dialog of its own.
      dialogs.open({view: "new-part", title: "New part"});
      const unrelated = htmls[htmls.length - 1];
      release();
      const res = await pending;
      out({res, unrelatedStamped: unrelated.includes("opened by agent"),
           legacyStamped: ov.attrs["data-agent-opened"] || null,
           chips: chipsOf(ov)});
    """, prelude=ATTRIB_DOM)
    assert got["unrelatedStamped"] is False, (
        "an unrelated dialog was credited to the agent")
    # The view the agent really did ask for is still stamped, awaited opener
    # and all.
    assert got["res"]["ok"] is True
    assert got["legacyStamped"] == "1"
    assert got["chips"] == [["dlg-attribution", "opened by agent"]]


def test_a_shell_dialog_opened_by_an_agent_still_gets_its_chip():
    """The other side of the m10 narrowing: `pendingAttribution` is cleared as
    soon as the opener YIELDS, so an opener that opens its dialog synchronously
    — every `dialogs.register` view in the tree — must still be stamped."""
    got = run_js("""
      dialogs.register("new-part", () => dialogs.open({view: "new-part",
                                                       title: "New part"}));
      // Not awaited: `open()`'s promise settles only when the dialog CLOSES,
      // and the stamp is decided in the opener's synchronous prefix.
      dialogs.openView("new-part", {}, {by: "agent"});
      const agent = htmls[htmls.length - 1];
      dialogs.openView("new-part", {}, {by: "user"});
      const user = htmls[htmls.length - 1];
      out({agent: agent.includes("opened by agent"),
           user: user.includes("opened by agent")});
    """, prelude=ATTRIB_DOM)
    assert got["agent"] is True, "the shell dialog lost its attribution chip"
    assert got["user"] is False


def test_the_tab_trap_belongs_to_the_topmost_modal_not_the_topmost_overlay():
    """m5 — with slice 3's non-modal tool-result panel above a modal, the Tab
    branch read `top()`, found `modal: false` and simply stopped trapping, so
    Tab walked straight out of the modal underneath. Mirrors `escOwner`."""
    got = run_js("""
      const owner = (entries) => dialogs.__dialogsDispatch__.trapOwner(entries);
      const modal = {id: "m", modal: true};
      const panel = {id: "p", modal: false};
      out({panelOnTop: (owner([modal, panel]) || {}).id,
           modalOnTop: (owner([panel, modal]) || {}).id,
           onlyPanel: owner([panel]), empty: owner([])});
    """, prelude=ATTRIB_DOM)
    assert got["panelOnTop"] == "m", "the modal underneath still owns Tab"
    assert got["modalOnTop"] == "m"
    assert got["onlyPanel"] is None and got["empty"] is None


def test_the_materials_modal_is_on_the_map_like_every_other_workbench_modal():
    """m2 — Materials was the one adopted modal with no action row: absent from
    the Model menu and from the palette's *action* section, while the user guide
    calls the menu "always a true map of what the app can do"."""
    main = MAIN.read_text(encoding="utf-8")
    row = main.split('id: "model.materials"', 1)[1].split("A({", 1)[0]
    assert 'menu: "model/22"' in row, "Materials is not in the Model menu"
    assert "when: hasProject" in row
    assert "materials.open()" in row
    # …and the toolbar button goes through the registry, like `setupLibrary()`.
    assert 'actions.run("model.materials", null, { source: "toolbar" })' in main
    button = main.split('getElementById("materials-btn")', 1)[1][:220]
    assert "materials.open()" not in button, (
        "the toolbar button still calls the module directly")
    # The `#materials` deep link is deliberately NOT routed: it runs after the
    # project load settles and is PRD-028's own entry point.
    # PRD-028's inspector Browse… path is untouched.
    assert "panelApi.openMaterials" in main


def test_the_two_fire_and_forget_open_view_sites_catch_their_rejection():
    """m3 — `openView` is `async` with a `try/finally` and no `catch`, so a
    throwing opener rejected into nobody at both call sites. `palette.js`
    already made this argument for the tool path."""
    main = MAIN.read_text(encoding="utf-8")
    ui_open = main.split('case "ui_open":', 1)[1].split("case ", 1)[0]
    assert ".catch(" in ui_open, "`ui_open` still drops a rejection"
    palette = (SHELL / "palette.js").read_text(encoding="utf-8")
    view_branch = palette.split("if (row.view) {", 1)[1].split("} else if", 1)[0]
    assert ".catch(" in view_branch, "the palette's view row drops a rejection"


def test_a_view_an_action_already_offers_gets_no_second_palette_row():
    """m4 — `library`, `versions`, `configs`, `proposals`, `drawing` and now
    `materials` each had an action row AND an "Open: …" view row: the same verb
    twice, under near-identical titles. The action row wins (it carries the
    group, the keywords and the shortcut label)."""
    got = run_palette("""
      const views = [
        {view: "library", title: "Parts library…", actionId: "model.library"},
        {view: "merge", title: "Staged merge…", actionId: null},
        {view: "share", title: "Share a part…"},
      ];
      out({filtered: pm.entriesFromViews(views, new Set(["model.library"]))
             .map((r) => r.view),
           asArray: pm.entriesFromViews(views, ["model.library"])
             .map((r) => r.view),
           noActions: pm.entriesFromViews(views).map((r) => r.view),
           junk: pm.entriesFromViews(views, "model.library").map((r) => r.view)});
    """)
    assert got["filtered"] == ["merge", "share"]
    assert got["asArray"] == ["merge", "share"]
    # With no action rows passed, nothing is suppressed — the old behaviour.
    assert got["noActions"] == ["library", "merge", "share"]
    assert got["junk"] == ["library", "merge", "share"]


def test_every_modal_with_an_action_row_declares_its_action_id():
    """The other half of m4: the suppression is only as good as the pairing."""
    for module, action in [("library.js", "model.library"),
                           ("versions.js", "model.versions"),
                           ("configs.js", "model.configs"),
                           ("proposals.js", "model.proposals"),
                           ("drawings.js", "model.drawing"),
                           ("materials.js", "model.materials")]:
        source = (FRONTEND_JS / module).read_text(encoding="utf-8")
        assert f'actionId: "{action}"' in source, module
        assert f'id: "{action}"' in MAIN.read_text(encoding="utf-8"), action


def test_a_hostile_button_id_or_kind_cannot_break_out_of_its_attribute():
    """D1 (second verifier) — `b.id` reached an `id=` attribute and `b.kind` a
    `class=` attribute unescaped, while the SAME `b.id` was escaped two lines
    down in `data-btn=`. Unreachable today (every `buttons[]` value in the tree
    is a source literal), but this is the primitive the PRD advertises for other
    PRDs to compose, and the module's docstring claims everything is escaped."""
    got = run_js(r"""
      const m = dm.markup({uid: 1, title: "T", buttons: [
        {id: 'x" onclick="alert(1)', kind: 'y" onmouseover="alert(1)',
         label: "L"},
        {id: "ok", kind: "primary", label: "OK", submits: true},
      ]});
      out({html: m.html, ids: m.ids.buttons});
    """)
    # The measurement is whether the payload survives with its QUOTES intact —
    # `onclick=&quot;` inside an attribute value is text, not a handler.
    assert 'onclick="alert(1)"' not in got["html"], got["html"]
    assert 'onmouseover="alert(1)"' not in got["html"], got["html"]
    assert "&quot;" in got["html"], "nothing was escaped at all"
    # The id is COERCED, not escaped: an escaped id is no longer an id, and
    # `dialogs.js` looks these up with `querySelector("#…")`.
    assert got["ids"]['x" onclick="alert(1)'] == "dlg-btn-1-xonclickalert1"
    # The label and `data-btn` are still escaped, and the legitimate button is
    # untouched: `primary` is one of the three kinds the stylesheet has.
    assert 'class="dlg-btn primary"' in got["html"]
    assert 'id="dlg-btn-1-ok"' in got["html"]
    # The hostile kind is dropped entirely rather than escaped into a class.
    assert 'class="dlg-btn"' in got["html"]


def test_a_hostile_uid_cannot_break_out_of_the_ids_it_seeds():
    """Same shape: `uid` seeds `dlg-overlay-`, `dlg-`, `dlg-title-` and the
    `aria-labelledby`/`aria-describedby` that point at them. `dialogs.js` only
    ever passes its own `seq`, so this is defence in depth — and an id is
    coerced to an id, because an ESCAPED id is no longer an id."""
    got = run_js(r"""
      const hostile = dm.markup({uid: '1" onload="alert(1)', title: "T"});
      const ok = dm.markup({uid: 7, title: "T"});
      out({hostile: hostile.html, ids: hostile.ids, ok: ok.ids.dialog});
    """)
    assert "onload=" not in got["hostile"], got["hostile"]
    assert got["ids"]["dialog"] == "dlg-1onloadalert1"
    assert f'id="{got["ids"]["dialog"]}"' in got["hostile"]
    assert f'aria-labelledby="{got["ids"]["title"]}"' in got["hostile"]
    # The normal path is byte-identical to before.
    assert got["ok"] == "dlg-7"


def test_a_hostile_menu_name_cannot_break_out_of_the_menu_ids():
    """D1's fourth site: `menu_model.markup`'s `m.menu` reached `id=`,
    `aria-controls=` and `aria-labelledby=` raw. `tree()` sources names from the
    fixed `MENU_ORDER`, so this too is defence in depth."""
    got = run_js(r"""
      const html = mm.markup([
        {menu: 'f" onmouseover="alert(1)', label: "File", items: []},
        {menu: "view", label: "View", items: []},
      ]);
      out({html});
    """)
    assert 'onmouseover="alert(1)"' not in got["html"], got["html"]
    assert "&quot;" in got["html"], "the menu name was not escaped"
    # A real menu name is unchanged, ids and all.
    assert 'id="menubar-view-btn"' in got["html"]
    assert 'aria-controls="menubar-view-menu"' in got["html"]
