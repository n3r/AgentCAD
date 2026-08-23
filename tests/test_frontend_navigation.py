"""PRD-027 Navigation at scale — the browser's pure models, in node.

Four things live here that a screenshot cannot grade and Python cannot reach:

* **`query_model.js` is a port of `core/search.py`'s grammar and matcher**, and
  the two must agree row for row *and rank for rank* — the browser answers
  every metadata-only filter itself and only calls the server when the query
  has free text. `tests/fixtures/search_queries.json` is the one place that
  agreement is written down, so this module drives the *same* file through the
  JS half that `tests/test_search.py` drives through the Python half, case for
  case (including the `needs_script` ones: the pure matcher DOES match script
  text when it is given some — what the browser lacks is the text, not the
  code). A second pass runs every case that does NOT need script text with an
  EMPTY script, which is exactly what the live filter box does.
* **`tree_model.js`'s folder flatten, filter and selection rules** — folders
  first and alphabetical, parts in manifest order, a collapsed folder hiding
  its descendants, a filter that bubbles a hit up to its ancestors and forces
  them open, and the Finder click/Cmd/Shift table.
* **`virtual_model.js`'s window** — 10 000 rows must render a window of a few
  dozen, and the two spacers plus the rendered rows must add up to the exact
  scroll height or the scrollbar lies.
* **`shell/contextmenu.js`'s markup** — the static accessibility pass
  (`role="menu"`, a `role="menuitem"` per row, `aria-disabled` rather than a
  vanished row, and escaped labels).

Every module is pure ES and node-importable: an accidental top-level
`document`/`window` reference fails the import test, which is the whole point
of the pure/DOM split (`tests/test_frontend_shell.py`'s precedent).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend" / "js"
SHELL = FRONTEND / "shell"

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "search_queries.json"
FIXTURE = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
PARTS = FIXTURE["parts"]
CASES = FIXTURE["cases"]
MATCH_CASES = [c for c in CASES if not c.get("error")]
ERROR_CASES = [c for c in CASES if c.get("error")]
MATCHED_ON_CASES = [c for c in MATCH_CASES if c.get("expect_matched_on")]
META_ONLY_CASES = [c for c in MATCH_CASES if not c.get("needs_script")]

#: Every JS file this slice creates or changes. `node --check` parses each one,
#: so a syntax error is a red test rather than a blank sidebar in the browser.
JS_FILES = [FRONTEND / "query_model.js", FRONTEND / "virtual_model.js",
            FRONTEND / "tree_model.js", SHELL / "contextmenu.js"]

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is not installed")


def _uri(path: Path) -> str:
    return json.dumps(path.resolve().as_uri())


PRELUDE = f"""
import * as qm from {_uri(FRONTEND / "query_model.js")};
import * as vm from {_uri(FRONTEND / "virtual_model.js")};
import * as tm from {_uri(FRONTEND / "tree_model.js")};
import * as cm from {_uri(SHELL / "contextmenu.js")};
const out = (v) => process.stdout.write(JSON.stringify(v));
const parts = {json.dumps(PARTS)};
"""


def run_js(body: str, prelude: str = PRELUDE, env: dict | None = None) -> object:
    """Run `body` in node with this slice's models imported; return its JSON."""
    proc = subprocess.run(["node", "--input-type=module", "--eval",
                           prelude + body],
                          capture_output=True, text=True, timeout=120,
                          env=env)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def _case_id(case) -> str:
    return (case["query"] or "<empty>").replace(" ", "_")


# --------------------------------------------------------------- the modules

def test_every_navigation_module_imports_in_node_and_exports_its_contract():
    """The interface slice 6 is written against, proved by import.

    A top-level `document`/`window` reference in any of the four fails this —
    `contextmenu.js` is the one with a DOM half, and it must still import.
    """
    got = run_js("out({qm: Object.keys(qm), vm: Object.keys(vm),"
                 " tm: Object.keys(tm), cm: Object.keys(cm)});")
    for name in ("FIELDS", "parse", "matches", "hasFreeText", "rank",
                 "scriptOnly", "__queryModel__"):
        assert name in got["qm"], f"query_model.{name} is missing"
    for name in ("window", "__virtualModel__"):
        assert name in got["vm"], f"virtual_model.{name} is missing"
    for name in ("instanceRows", "memberIdsOf", "rowsHtml", "folderTree",
                 "filterRows", "instanceTree", "selectionAfter", "persistTree",
                 "readTree", "treeKey", "isFolderPath", "__treeModel__"):
        assert name in got["tm"], f"tree_model.{name} is missing"
    for name in ("init", "open", "close", "isOpen", "markup"):
        assert name in got["cm"], f"contextmenu.{name} is missing"


@pytest.mark.parametrize("path", JS_FILES, ids=[p.name for p in JS_FILES])
def test_the_javascript_parses(path):
    proc = subprocess.run(["node", "--check", str(path)],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr


# ------------------------------------------------------- the parity of parse

@pytest.fixture(scope="module")
def parity():
    """Every fixture case through `query_model`, in ONE node run.

    One process for forty-two cases rather than forty-two processes: the
    per-case granularity that matters is in the assertions below, and a node
    spawn per parametrized case costs more than the whole rest of this file.

    Returns `{query: {ids, matched_on, error?, ...}}` twice over — once with
    each part's script text (the server's view, and the fixture's) and once
    with an empty script (the live filter box's view).
    """
    got = run_js("""
      const cases = JSON.parse(process.env.AGENTCAD_CASES);
      const run = (query, withScript) => {
        let parsed;
        try { parsed = qm.parse(query); }
        catch (err) { return {error: String(err && err.message || err)}; }
        const hits = [];
        parts.forEach((part, index) => {
          const found = qm.matches(part, parsed,
            {scriptText: withScript ? part.script : ""});
          if (found !== null) hits.push({rank: qm.rank(found), index, found,
                                         id: part.id});
        });
        hits.sort((a, b) => (a.rank - b.rank) || (a.index - b.index));
        const matched = {};
        const only = {};
        for (const h of hits) { matched[h.id] = h.found;
                                only[h.id] = qm.scriptOnly(h.found); }
        return {ids: hits.map((h) => h.id), matched_on: matched,
                script_only: only, free_text: qm.hasFreeText(parsed),
                terms: parsed.terms.length};
      };
      const withScript = {}; const metaOnly = {};
      for (const c of cases) {
        withScript[c.query] = run(c.query, true);
        metaOnly[c.query] = run(c.query, false);
      }
      out({withScript, metaOnly, count: cases.length});
    """, env={**os.environ, "AGENTCAD_CASES": json.dumps(CASES)})
    assert got["count"] == len(CASES)
    return got


@pytest.mark.parametrize("case", MATCH_CASES, ids=[_case_id(c) for c in MATCH_CASES])
def test_fixture_case_matches_and_ranks(parity, case):
    got = parity["withScript"][case["query"]]
    assert "error" not in got, got.get("error")
    assert got["ids"] == case["expect"], case.get("note", "")


@pytest.mark.parametrize("case", MATCHED_ON_CASES,
                         ids=[_case_id(c) for c in MATCHED_ON_CASES])
def test_fixture_case_reports_the_expected_matched_on(parity, case):
    found = parity["withScript"][case["query"]]["matched_on"]
    for part_id, sources in case["expect_matched_on"].items():
        assert found[part_id] == sources


@pytest.mark.parametrize("case", ERROR_CASES, ids=[_case_id(c) for c in ERROR_CASES])
def test_fixture_error_case_is_refused(parity, case):
    got = parity["withScript"][case["query"]]
    assert got.get("error"), f"{case['query']!r} was not refused"


@pytest.mark.parametrize("case", META_ONLY_CASES,
                         ids=[_case_id(c) for c in META_ONLY_CASES])
def test_a_metadata_only_case_needs_no_script_text(parity, case):
    """What the live filter box computes, with no script in the browser.

    Every case the fixture does NOT mark `needs_script` must come out the same
    with an empty script — that is the contract that lets the browser answer a
    filter locally instead of round-tripping to the server.
    """
    assert parity["metaOnly"][case["query"]]["ids"] == case["expect"]


@pytest.mark.parametrize(
    "case", [c for c in MATCH_CASES if c.get("needs_script")],
    ids=[_case_id(c) for c in MATCH_CASES if c.get("needs_script")])
def test_a_script_case_is_flagged_as_needing_the_server(parity, case):
    """`hasFreeText` is what the browser asks before calling the search route.

    A case whose answer depends on script text MUST have free text in it —
    otherwise the browser would answer it locally, from metadata it has, and
    silently miss every script hit.
    """
    assert parity["withScript"][case["query"]]["free_text"] is True


def test_has_free_text_is_false_for_a_pure_field_query(parity):
    for query in ("tag:fastener", "-tag:fastener",
                  "tag:printed state:ok", ""):
        assert parity["withScript"][query]["free_text"] is False, query


def test_a_script_only_hit_is_reported_as_such(parity):
    """`scriptOnly` is the snippet rule — a FIELD term does not suppress it."""
    assert parity["withScript"]["counterbore"]["script_only"]["base_plate"]
    assert parity["withScript"]["state:ok counterbore"]["script_only"]["base_plate"]
    assert not parity["withScript"]["shaft helix"]["script_only"]["shaft"]


def test_the_ranking_table_is_the_python_one():
    got = run_js("out({ranks: qm.RANKS, sources: qm.SOURCES,"
                 " none: qm.NO_EVIDENCE_RANK, fields: qm.FIELDS});")
    assert got["ranks"] == {"id": 0, "label": 0, "tag": 1, "material": 2,
                            "folder": 3, "state": 3, "kind": 3, "script": 4}
    assert got["sources"] == ["id", "label", "tag", "material", "folder",
                              "state", "kind", "script"]
    assert got["none"] == 5
    assert got["fields"] == ["tag", "material", "state", "kind", "folder",
                             "id", "label"]


def test_a_non_identifier_head_stays_free_text_but_an_unknown_field_is_refused():
    """`1:2` is free text; `http://x` is a typo the agent must be told about."""
    got = run_js("""
      const one = (q) => { try { return {terms: qm.parse(q).terms}; }
                           catch (err) { return {error: err.message}; } };
      out({free: one("1:2"), colon: one(":x"), quoted: one('"http://x"'),
           bare: one("http://x"), upper: one("TAG:fastener")});
    """)
    assert got["free"]["terms"] == [{"field": None, "value": "1:2",
                                     "negate": False}]
    assert got["colon"]["terms"] == [{"field": None, "value": ":x",
                                      "negate": False}]
    assert got["quoted"]["terms"] == [{"field": None, "value": "http://x",
                                       "negate": False}]
    assert "unknown search field" in got["bare"]["error"]
    assert got["upper"]["terms"] == [{"field": "tag", "value": "fastener",
                                      "negate": False}]


def test_a_corrupt_row_does_not_throw_mid_scan():
    """Total over the row, like the Python matcher: a hand edit or a merge can
    put a number in `label` or a string in `tags`, and a filter over a thousand
    parts must not die on one of them."""
    got = run_js("""
      const row = {id: "x", label: 7, tags: "nope", material: null,
                   folder: 12, state: "ok", kind: "script"};
      out({free: qm.matches(row, qm.parse("x"), {}),
           tag: qm.matches(row, qm.parse("tag:a"), {}),
           folder: qm.matches(row, qm.parse("folder:a"), {}),
           empty: qm.matches(row, qm.parse(""), {})});
    """)
    assert got["free"] == ["id"]
    assert got["tag"] is None and got["folder"] is None
    assert got["empty"] == []


# ------------------------------------------------------------- the folder tree

@pytest.fixture(scope="module")
def tree():
    """`folderTree` over the fixture's fifteen parts, in a few shapes."""
    return run_js("""
      const flat = (rows) => rows.map((r) => r.kind === "folder"
        ? ["f", r.path, r.depth, r.count, r.collapsed]
        : ["p", r.id, r.depth]);
      out({
        plain: flat(tm.folderTree(parts)),
        collapsed: flat(tm.folderTree(parts, {collapsed: ["Chassis"]})),
        insensitive: flat(tm.folderTree(parts, {collapsed: ["cHaSsIs"]})),
        empty: flat(tm.folderTree(parts, {emptyFolders: ["Ideas",
                                                         "Chassis/Spares",
                                                         "Fasteners"]})),
        none: flat(tm.folderTree([])),
        names: tm.folderTree(parts).filter((r) => r.kind === "folder")
          .map((r) => r.name),
        carries: tm.folderTree(parts).find((r) => r.kind === "part").part.id,
      });
    """)


def test_folders_come_first_alphabetically_then_parts_in_manifest_order(tree):
    assert tree["plain"] == [
        ["f", "A", 0, 2, False],
        ["f", "A/b", 1, 1, False],
        ["p", "ab_widget", 2],
        ["f", "A/bc", 1, 1, False],
        ["p", "abc_widget", 2],
        ["f", "Chassis", 0, 3, False],
        ["f", "Chassis/Left side", 1, 1, False],
        ["p", "bracket_l", 2],
        ["f", "Chassis/Right side", 1, 1, False],
        ["p", "bracket_r", 2],
        ["p", "base_plate", 1],
        ["f", "Drivetrain", 0, 4, False],
        ["f", "Drivetrain/Gears", 1, 2, False],
        ["p", "gear_a", 2],
        ["p", "gear_b", 2],
        ["p", "shaft", 1],
        ["p", "rotating_base", 1],
        ["f", "Enclosure", 0, 2, False],
        ["f", "Enclosure/Top", 1, 1, False],
        ["p", "lid", 2],
        ["p", "housing", 1],
        ["f", "Fasteners", 0, 2, False],
        ["p", "m5_screw", 1],
        ["p", "m3_screw", 1],
        ["f", "Printing", 0, 1, False],
        ["p", "spool", 1],
        ["p", "scanned_frame", 0],
    ]


def test_a_folder_row_names_its_last_segment_and_a_part_row_carries_its_part(tree):
    assert tree["names"] == ["A", "b", "bc", "Chassis", "Left side",
                             "Right side", "Drivetrain", "Gears", "Enclosure",
                             "Top", "Fasteners", "Printing"]
    assert tree["carries"] == "ab_widget"


def test_a_collapsed_folder_hides_every_descendant(tree):
    rows = tree["collapsed"]
    assert ["f", "Chassis", 0, 3, True] in rows
    assert not [r for r in rows if r[1].startswith("Chassis/")]
    assert not [r for r in rows if r[1] in ("bracket_l", "bracket_r",
                                            "base_plate")]
    # everything outside Chassis is untouched
    assert ["p", "gear_a", 2] in rows and ["p", "scanned_frame", 0] in rows
    # a folder is matched case-insensitively (it is stored verbatim, ruling 9)
    assert tree["insensitive"] == rows


def test_an_empty_folder_renders_until_a_part_names_it(tree):
    rows = tree["empty"]
    assert ["f", "Ideas", 0, 0, False] in rows
    assert ["f", "Chassis/Spares", 1, 0, False] in rows
    # `Fasteners` already holds two parts — it is not duplicated, and it keeps
    # the count its parts give it.
    assert len([r for r in rows if r[0] == "f" and r[1] == "Fasteners"]) == 1
    assert ["f", "Fasteners", 0, 2, False] in rows
    assert tree["none"] == []


def test_a_thousand_part_project_flattens_to_a_row_per_part_plus_its_folders():
    """AC5's model half: the flatten is linear and does not blow up."""
    got = run_js("""
      const many = [];
      for (let i = 0; i < 1000; i += 1) {
        many.push({id: `p${i}`, label: `P ${i}`, folder: `F${i % 25}`,
                   tags: [], state: "ok", kind: "script"});
      }
      const rows = tm.folderTree(many);
      out({rows: rows.length,
           folders: rows.filter((r) => r.kind === "folder").length,
           collapsed: tm.folderTree(many,
             {collapsed: Array.from({length: 25}, (_, i) => `F${i}`)}).length});
    """)
    assert got["rows"] == 1025 and got["folders"] == 25
    assert got["collapsed"] == 25


# ---------------------------------------------------------------- the filter

def test_filter_keeps_a_hits_ancestors_and_forces_them_open():
    got = run_js("""
      const flat = (r) => r.rows.map((x) => x.kind === "folder"
        ? ["f", x.path, x.collapsed] : ["p", x.id]);
      const hit = tm.filterRows(parts, qm.parse("gear"),
        {collapsed: ["Drivetrain", "Drivetrain/Gears", "Chassis"]});
      const none = tm.filterRows(parts, qm.parse("tag:nothing"), {});
      const all = tm.filterRows(parts, qm.parse(""),
        {collapsed: ["Chassis"]});
      out({hit: {rows: flat(hit), total: hit.total, shown: hit.shown},
           none: {rows: flat(none), total: none.total, shown: none.shown},
           all: {total: all.total, shown: all.shown,
                 chassis: all.rows.find((r) => r.path === "Chassis").collapsed,
                 rows: all.rows.length},
           matched: hit.rows.filter((r) => r.kind === "part")
             .map((r) => [r.id, r.matchedOn])});
    """)
    assert got["hit"]["rows"] == [
        ["f", "Drivetrain", False],
        ["f", "Drivetrain/Gears", False],
        ["p", "gear_a"], ["p", "gear_b"],
    ]
    assert got["hit"]["total"] == 15 and got["hit"]["shown"] == 2
    assert got["matched"] == [["gear_a", ["id", "label", "tag"]],
                              ["gear_b", ["id", "label", "tag"]]]
    # No hit anywhere: no folder survives, and the counts still tell the truth.
    assert got["none"]["rows"] == []
    assert got["none"]["total"] == 15 and got["none"]["shown"] == 0
    # An empty query is not a filter: the persisted collapse still applies.
    assert got["all"]["chassis"] is True
    assert got["all"]["total"] == 15 and got["all"]["shown"] == 15


def test_filter_unions_the_servers_answer_with_the_clients():
    """`opts.ids` ADDS rows, it never removes them.

    The browser has no script text, so a script-only hit — the exact thing the
    server is asked about — matches NOTHING on the client. Intersecting the two
    answers would therefore drop every row the server was called for and answer
    `shown: 0` for a query the server just said had a hit. The union is also
    what makes the debounce feel instant: the client's own matches are on
    screen from the keystroke and the server's script hits join them.
    """
    got = run_js("""
      const say = (r) => ({
        ids: r.rows.filter((x) => x.kind === "part").map((x) => x.id),
        evidence: Object.fromEntries(r.rows.filter((x) => x.kind === "part")
          .map((x) => [x.id, x.matchedOn])),
        shown: r.shown, total: r.total});
      out({
        // A `needs_script` fixture case, WITHOUT the scripts: everything the
        // browser can see says no, and the server says base_plate.
        scriptOnly: say(tm.filterRows(parts, qm.parse("counterbore"),
                                      {ids: ["base_plate"]})),
        // Both sides contribute: the client knows gear_a/gear_b by name, only
        // the server can know shaft matched in its script body.
        both: say(tm.filterRows(parts, qm.parse("gear"),
                                {ids: ["gear_a", "shaft", "no_such_part"]})),
        // The server has not answered yet (or answered nothing): the client's
        // own matches still stand.
        clientAlone: say(tm.filterRows(parts, qm.parse("gear"), {ids: []})),
      });
    """)
    assert got["scriptOnly"]["ids"] == ["base_plate"]
    assert got["scriptOnly"]["evidence"] == {"base_plate": ["script"]}
    assert got["scriptOnly"]["shown"] == 1 and got["scriptOnly"]["total"] == 15
    # Manifest order inside the flatten: Gears is a folder under Drivetrain, so
    # its two parts come before Drivetrain's own `shaft` row.
    assert got["both"]["ids"] == ["gear_a", "gear_b", "shaft"]
    assert got["both"]["evidence"] == {
        "gear_a": ["id", "label", "tag"],     # the client's own evidence wins
        "gear_b": ["id", "label", "tag"],
        "shaft": ["script"],                  # kept by the server alone
    }
    assert got["both"]["shown"] == 3
    assert got["clientAlone"]["ids"] == ["gear_a", "gear_b"]


def test_an_unparsed_query_string_is_parsed_never_matched_as_empty():
    """A raw string has no `.terms`, and `terms || []` is the EMPTY query —
    which matches every row. A filter box that silently shows all 1 000 parts
    looks like a working filter with a very popular query, so both entry points
    parse a string and refuse anything else by type."""
    got = run_js("""
      const attempt = (fn) => { try { return {ok: fn()}; }
                                catch (err) { return {error: err.message}; } };
      const row = parts[9];   // gear_a
      out({
        matchString: qm.matches(row, "tag:gear", {}),
        matchMiss: qm.matches(row, "tag:printed", {}),
        matchRefuses: attempt(() => qm.matches(row, "colour:red", {})),
        matchType: attempt(() => qm.matches(row, 7, {})),
        matchEmpty: qm.matches(row, "", {}),
        matchNull: qm.matches(row, null, {}),
        filterString: (() => { const r = tm.filterRows(parts, "gear", {});
          return {ids: r.rows.filter((x) => x.kind === "part")
            .map((x) => x.id), shown: r.shown}; })(),
        filterRefuses: attempt(() => tm.filterRows(parts, "colour:red", {})),
        filterType: attempt(() => tm.filterRows(parts, 7, {})),
      });
    """)
    assert got["matchString"] == ["tag"]
    assert got["matchMiss"] is None
    assert "unknown search field" in got["matchRefuses"]["error"]
    assert "query must be a string" in got["matchType"]["error"]
    # `null`/`""` really are the empty query — matched, with no evidence.
    assert got["matchEmpty"] == [] and got["matchNull"] == []
    assert got["filterString"] == {"ids": ["gear_a", "gear_b"], "shown": 2}
    assert "unknown search field" in got["filterRefuses"]["error"]
    assert "query must be a string" in got["filterType"]["error"]


def test_the_tree_builders_are_total_over_a_missing_parts_list():
    """`get_project` is a network payload; a render one tick early hands these
    `undefined`. An empty tree is the honest answer — a thrown "not iterable"
    is a blank sidebar with a console trace."""
    got = run_js("""
      const attempt = (fn) => { try { return fn(); }
                                catch (err) { return `THREW ${err.message}`; } };
      out({folderObj: attempt(() => tm.folderTree({})),
           folderNull: attempt(() => tm.folderTree(null)),
           folderNone: attempt(() => tm.folderTree()),
           instObj: attempt(() => tm.instanceTree({})),
           instNull: attempt(() => tm.instanceTree(null)),
           filterObj: attempt(() => tm.filterRows({}, qm.parse("gear"), {}))});
    """)
    for name in ("folderObj", "folderNull", "folderNone", "instObj",
                 "instNull"):
        assert got[name] == [], (name, got[name])
    assert got["filterObj"] == {"rows": [], "total": 0, "shown": 0}


def test_filter_matches_script_text_when_the_caller_supplies_it():
    got = run_js("""
      const scripts = {}; for (const p of parts) scripts[p.id] = p.script;
      const r = tm.filterRows(parts, qm.parse("counterbore"), {scripts});
      out(r.rows.filter((x) => x.kind === "part").map((x) => x.id));
    """)
    assert got == ["base_plate"]


# ------------------------------------------------------------ instance trees

def test_instance_tree_folders_the_prd013_rows():
    got = run_js("""
      const insts = [
        {id: "bolt", part: "m6", folder: "Hardware",
         pattern: {kind: "polar", count: 8}},
        {id: "engine", assembly: {project: "engine_src"}, folder: "Drive"},
        {id: "plate", part: "base"},
      ];
      const rows = tm.instanceTree(insts, {collapsed: ["Hardware"]});
      out(rows.map((r) => r.kind === "folder"
        ? ["f", r.path, r.count, r.collapsed]
        : ["i", r.id, r.depth, r.instance.kind, r.instance.badge || null,
           r.instance.readonly || false]));
    """)
    assert got == [
        ["f", "Drive", 1, False],
        ["i", "engine", 1, "assembly", None, True],
        ["f", "Hardware", 1, True],
        ["i", "plate", 0, "part", None, False],
    ]


# -------------------------------------------------------------- the selection

@pytest.fixture(scope="module")
def selection():
    return run_js("""
      const visible = ["a", "b", "c", "d", "e"];
      const call = (cur, anchor, clicked, mods) => {
        const r = tm.selectionAfter(new Set(cur), anchor, visible, clicked,
                                    mods || {});
        return {sel: [...r.selection].sort(), anchor: r.anchor,
                primary: r.primary};
      };
      const frozen = new Set(["a"]);
      const after = tm.selectionAfter(frozen, "a", visible, "c", {});
      out({
        click: call([], null, "b", {}),
        clickReplaces: call(["a", "c"], "a", "b", {}),
        metaAdds: call(["a"], "a", "c", {meta: true}),
        metaToggles: call(["a", "b"], "a", "b", {meta: true}),
        shiftRange: call(["b"], "b", "d", {shift: true}),
        shiftBackwards: call(["d"], "d", "b", {shift: true}),
        shiftNoAnchor: call(["a"], null, "c", {shift: true}),
        shiftUnknownAnchor: call([], "zz", "c", {shift: true}),
        shiftMetaUnions: call(["e"], "b", "c", {shift: true, meta: true}),
        offscreen: call(["a"], "a", "zz", {}),
        untouched: [...frozen],
      });
    """)


def test_a_plain_click_replaces_the_selection_and_sets_the_primary(selection):
    assert selection["click"] == {"sel": ["b"], "anchor": "b", "primary": "b"}
    assert selection["clickReplaces"] == {"sel": ["b"], "anchor": "b",
                                          "primary": "b"}
    assert selection["offscreen"] == {"sel": ["zz"], "anchor": "zz",
                                      "primary": "zz"}


def test_a_modifier_click_grows_the_set_and_leaves_the_primary_alone(selection):
    """Design §7: `selectedPart` stays "the primary"; only a plain click moves
    it. `primary: null` is "do not change it", never "clear it"."""
    assert selection["metaAdds"] == {"sel": ["a", "c"], "anchor": "c",
                                     "primary": None}
    assert selection["metaToggles"] == {"sel": ["a"], "anchor": "b",
                                        "primary": None}


def test_shift_ranges_over_the_visible_order_from_the_anchor(selection):
    assert selection["shiftRange"] == {"sel": ["b", "c", "d"], "anchor": "b",
                                       "primary": None}
    assert selection["shiftBackwards"] == {"sel": ["b", "c", "d"],
                                           "anchor": "d", "primary": None}
    assert selection["shiftMetaUnions"] == {"sel": ["b", "c", "e"],
                                            "anchor": "b", "primary": None}


def test_shift_without_a_usable_anchor_is_a_plain_click(selection):
    assert selection["shiftNoAnchor"] == {"sel": ["c"], "anchor": "c",
                                          "primary": "c"}
    assert selection["shiftUnknownAnchor"] == {"sel": ["c"], "anchor": "c",
                                               "primary": "c"}


def test_the_caller_s_set_is_never_mutated(selection):
    assert selection["untouched"] == ["a"]


# ---------------------------------------------------------- the virtual window

def test_a_ten_thousand_row_tree_renders_a_few_dozen_rows():
    got = run_js("""
      const total = 10000, rowHeight = 28, viewportHeight = 600;
      const at = (scrollTop, overscan) => vm.window(
        {scrollTop, viewportHeight, rowHeight, total,
         ...(overscan === undefined ? {} : {overscan})});
      out({top: at(0), middle: at(5000), end: at(10 * 1000 * 1000),
           tight: at(5000, 0),
           empty: vm.window({scrollTop: 0, viewportHeight: 600, rowHeight: 28,
                             total: 0}),
           degenerate: vm.window({scrollTop: 0, viewportHeight: 600,
                                  rowHeight: 0, total: 10}),
           junk: vm.window({})});
    """)
    height = 10000 * 28
    for name in ("top", "middle", "end", "tight"):
        w = got[name]
        rows = w["end"] - w["start"]
        # 600 / 28 = 21.4 visible rows; the window is that plus the overscan on
        # each side, and never the whole list.
        assert rows <= 30 + 2 * 8, (name, rows)
        assert w["padTop"] + rows * 28 + w["padBottom"] == height, name
        assert 0 <= w["start"] <= w["end"] <= 10000, name
    assert got["top"]["start"] == 0 and got["top"]["padTop"] == 0
    assert got["middle"]["start"] == 178 - 8
    assert got["tight"]["start"] == 178          # overscan 0 is honoured
    assert got["end"]["end"] == 10000 and got["end"]["padBottom"] == 0
    assert got["end"]["start"] > 9900            # clamped, not past the end
    assert got["empty"] == {"start": 0, "end": 0, "padTop": 0, "padBottom": 0}
    assert got["degenerate"] == {"start": 0, "end": 0, "padTop": 0,
                                 "padBottom": 0}
    assert got["junk"] == {"start": 0, "end": 0, "padTop": 0, "padBottom": 0}


# ------------------------------------------------------- the persisted state

def test_read_tree_clamps_to_valid_folder_paths():
    got = run_js("""
      const many = Array.from({length: 600}, (_, i) => `F${i}`);
      out({
        good: tm.readTree(JSON.stringify(
          {collapsed: ["Chassis", "Chassis/Left side", "Chassis"],
           emptyFolders: ["Ideas"]})),
        bad: tm.readTree(JSON.stringify({collapsed:
          ["bad//path", " lead", "trail ", "/root", "a/b/c/d/e/f/g/h/i",
           "x".repeat(41), "ok.name-1", 7, null, {}, "über"],
          emptyFolders: "nope"})),
        big: tm.readTree(JSON.stringify({collapsed: many})).collapsed.length,
        junk: tm.readTree("{not json"),
        missing: tm.readTree(null),
        notObject: tm.readTree("[1,2]"),
        roundTrip: tm.readTree(tm.persistTree("demo",
          {collapsed: ["Chassis"], emptyFolders: ["Ideas"]})),
        key: tm.treeKey("demo"),
      });
    """)
    assert got["good"] == {"collapsed": ["Chassis", "Chassis/Left side"],
                           "emptyFolders": ["Ideas"]}
    assert got["bad"] == {"collapsed": ["ok.name-1"], "emptyFolders": []}
    assert got["big"] == 500
    for name in ("junk", "missing", "notObject"):
        assert got[name] == {"collapsed": [], "emptyFolders": []}, name
    assert got["roundTrip"] == {"collapsed": ["Chassis"],
                                "emptyFolders": ["Ideas"]}
    assert got["key"] == "agentcad.tree.demo"


def test_is_folder_path_is_the_navigation_grammar():
    got = run_js("""
      const ok = ["Chassis", "chassis/left side", "A/b", "a.b-c_1",
                  "a/b/c/d/e/f/g/h"];
      const bad = ["", "/a", "a/", "a//b", " a", "a ", "a/b/c/d/e/f/g/h/i",
                   "x".repeat(41), ".hidden", "a\\\\b", "a\\nb", null, 7];
      out({ok: ok.map(tm.isFolderPath), bad: bad.map(tm.isFolderPath)});
    """)
    assert got["ok"] == [True] * 5
    assert got["bad"] == [False] * 13


# ------------------------------------------------------------ the context menu

def test_context_menu_markup_passes_the_static_accessibility_pass():
    got = run_js("""
      const html = cm.markup([
        {id: "rename", label: "Rename…", run() {}},
        {id: "export", label: "Export…", disabled: true, run() {}},
        {id: "delete", label: "Delete <b>now</b>…", danger: true, run() {}},
      ], "Part actions");
      out({html,
           items: (html.match(/role="menuitem"/g) || []).length,
           tabindex: (html.match(/tabindex="-1"/g) || []).length});
    """)
    html = got["html"]
    assert got["items"] == 3 and got["tabindex"] == 3
    assert '<ul class="menu ctx-menu" role="menu"' in html
    assert 'aria-label="Part actions"' in html
    assert '<li class="menu-item" role="menuitem" tabindex="-1" data-id="rename"' in html
    assert 'class="menu-item danger"' in html
    assert 'data-id="export"' in html and 'aria-disabled="true"' in html
    # A label is escaped — it is a part label, which a human types.
    assert "&lt;b&gt;now&lt;/b&gt;" in html and "<b>now</b>" not in html


def test_a_failing_menu_verb_is_reported_never_lost():
    """Every verb this menu carries is async, so `try/catch` is not enough.

    `Rename…`, `Delete…` and `Export…` all await a dialog and then a tool call;
    a bare `try { item.run() } catch` catches only the synchronous throw and
    lets every real failure become an unhandled rejection — a menu item that
    silently does nothing, with the reason in a console nobody opens.
    """
    got = run_js("""
      const unhandled = [];
      process.on("unhandledRejection", (err) => unhandled.push(String(err)));
      const seen = [];
      const report = (err, item) => seen.push(`${item.id}: ${err.message}`);
      const run = cm.__contextMenu__.runItem;
      const rejects = run({id: "del", label: "Delete…",
        run: async () => { throw new Error("boom"); }}, report);
      const throws = run({id: "ren",
        run: () => { throw new Error("sync"); }}, report);
      const fine = run({id: "exp", run: async () => 42}, report);
      const noVerb = run({id: "sep"}, report);
      // The DEFAULT reporter (console + toast) must survive a rejection with
      // no document — `toast()` is a no-op before boot, and this is what runs
      // in the browser.
      const dflt = run({id: "d", label: "D",
        run: async () => { throw new Error("quiet"); }});
      await Promise.all([rejects, fine, dflt]);
      await new Promise((r) => setTimeout(r, 20));
      out({seen, unhandled, throws, noVerb,
           returnsAPromise: typeof rejects.then === "function"});
    """)
    # Both reported — the SYNCHRONOUS throw first, because it reports inside
    # `runItem` while the rejection reports a microtask later. (Order is not a
    # promise to callers; that both arrive is.)
    assert got["seen"] == ["ren: sync", "del: boom"]
    assert got["unhandled"] == []            # nothing escaped
    assert got["throws"] is None and got["noVerb"] is None
    assert got["returnsAPromise"] is True    # a caller can await the verb


def test_esc_is_taken_and_tab_is_let_through():
    """The two keys that leave the menu, and the difference between them.

    Esc is consumed (preventDefault + stopPropagation) — that is the whole
    reason the listener sits on `window` in the capture phase, ahead of
    `dialogs.js`'s document listener. Tab is the WAI-ARIA menu pattern: close
    and get out of the way, WITHOUT preventDefault, so the browser's own Tab
    walks on from the row `close()` just restored focus to.
    """
    got = run_js("""
      const key = (k) => {
        const e = {key: k, prevented: false, stopped: false,
                   preventDefault() { this.prevented = true; },
                   stopPropagation() { this.stopped = true; },
                   target: null};
        // A closed menu: `close()` is idempotent, and neither branch touches
        // `document`, so the key table runs without a DOM.
        cm.__contextMenu__.onKey(e, {el: {contains: () => true}, items: []});
        return {prevented: e.prevented, stopped: e.stopped};
      };
      out({esc: key("Escape"), tab: key("Tab"), open: cm.isOpen()});
    """)
    assert got["esc"] == {"prevented": True, "stopped": True}
    assert got["tab"] == {"prevented": False, "stopped": False}
    assert got["open"] is False


def test_context_menu_markup_defaults_its_label_and_survives_no_items():
    got = run_js("""
      out({empty: cm.markup([]), noLabel: cm.markup([{id: "a", label: "A"}]),
           closed: cm.isOpen()});
    """)
    assert 'role="menu"' in got["empty"] and "<li" not in got["empty"]
    assert 'aria-label="Actions"' in got["noLabel"]
    assert got["closed"] is False
