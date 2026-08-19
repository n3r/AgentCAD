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


PRELUDE = f"""
import * as actions from {_uri("actions.js")};
import * as sc from {_uri("shortcuts_model.js")};
import * as dm from {_uri("dialogs_model.js")};
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
                           ("toast", "toast.js")]
    ) + "const out = (v) => process.stdout.write(JSON.stringify(v));\n"
    got = run_js(
        "out({actions: Object.keys(actions), scm: Object.keys(scm),"
        " shortcuts: Object.keys(shortcuts), dm: Object.keys(dm),"
        " dialogs: Object.keys(dialogs), toast: Object.keys(toast)});",
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
    # `help.palette`/`Mod+K` is slice 3's — no placeholder may squat the chord.
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
