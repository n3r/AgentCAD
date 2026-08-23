"""PRD-027 Navigation at scale — AC1-AC6, graded against the shipped surface.

One test (or a small named group) per criterion. Where a slice's own suite
already proves the exact same claim — `tests/test_navigation_meta.py`,
`test_search.py`, `test_thumbnails.py`, `test_tools_navigation.py`,
`test_routes_navigation.py`, `test_frontend_navigation.py` — this file
restates it *compactly* on the real surface rather than duplicating the case
list (the `tests/test_prd012_acceptance.py` / `test_prd026_acceptance.py`
house rule).

| AC | Test |
|---|---|
| AC1 | `test_ac1_the_engine_example_organizes_and_the_structure_survives_a_reopen` |
| AC2 | `test_ac2_state_error_finds_the_one_broken_part` (the machine half; the browser half is evidence-graded — see below) |
| AC3 | `test_ac3_a_script_change_mints_a_new_thumb_under_a_new_key` |
| AC4 | `test_ac4_six_parts_one_material_change_one_undo_step` |
| AC5 | `test_ac5_a_thousand_part_tree_flattens_and_windows_to_a_few_dozen_rows` (the model half; the frame time is evidence-graded) |
| AC6 | `test_ac6_*` — the three tools registered and documented, the four routes served and member-only, the tool-count line measured against the live registry, the docs describing the shipped surface, and the newest changelog citing a `make test` count |

Three things worth reading before you believe them:

* **AC1 runs on a copy, and "reload" is a second service.** The engine
  example is copied into a `tmp_path` projects dir — never the repo's own
  `examples/engine`, which every other suite builds from. "Reload" is then a
  brand-new `ProjectStore`/`AgentCADService` opened on that directory: the
  folders and tags have to come back out of `project.json`'s bytes, because
  there is no in-memory state left to come out of. The whole test is
  **kernel-free** — organizing and searching never build, so a `CountingKernel`
  that must not move is part of the claim.
* **AC2 and AC5 are each half a criterion here.** AC2 asks for "<100 ms in the
  browser" and AC5 for "scrolls at interactive rate"; neither is a thing a
  pytest process can measure honestly. What *is* on the record is slice 6's
  browser session (`docs/changelog/0327-prd-027-slice6-frontend.md`): typing
  `err` filtered 34 rows to one in **1.7 ms** around the input event, and a
  1 009-row tree rendered **43 `<li>`** with the tree's own rAF callback at
  **0.49 ms/frame** — with the honest note that wall-clock per scroll step met
  16.7 ms only with the SwiftShader viewport out of the loop. This file grades
  the halves a process can: the query returns the right row, and the models
  produce a bounded window.
* **The tool-count guard is measured, not typed.** `docs/agent-api.md`'s
  headline number is compared against `len(build_registry(service).list())` on
  a live service, so the count cannot drift again without a red test. It drifted
  badly before this PRD (the docs said 85 while the registry registered 104).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agentcad.core import thumbnails
from agentcad.core.service import AgentCADService, EventBus
from agentcad.core.tools import build_registry
from agentcad.server import security
from agentcad.server.app import create_app

from .conftest import BOX_SCRIPT, CountingKernel, flatten_routes, make_test_service

REPO = Path(__file__).resolve().parents[1]
CHANGELOG = REPO / "docs" / "changelog"
AGENT_API = REPO / "docs" / "agent-api.md"
USER_GUIDE = REPO / "docs" / "user-guide.md"
ARCHITECTURE = REPO / "docs" / "architecture.md"
PRD_NAME = "PRD-027-project-navigation-scale.md"

#: The three tools this PRD adds. One spelling, read by four tests.
NAV_TOOLS = ("set_part_meta", "search_parts", "bulk_part_op")

#: Every route template the two new packs mount, under `app.py`'s `/api`
#: prefix. Every one is member-only; none joins the anonymous allowlist.
NAV_ROUTES = (
    ("GET", "/api/projects/{proj}/search"),
    ("GET", "/api/dashboard"),
    ("GET", "/api/projects/{proj}/parts/{part_id}/thumb.png"),
    ("GET", "/api/projects/{proj}/thumb.png"),
)

_needs_node = pytest.mark.skipif(shutil.which("node") is None,
                                 reason="node is not installed")
_needs_git = pytest.mark.skipif(shutil.which("git") is None,
                                reason="git not found on PATH")


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


# ==================================================================== AC1

#: Real ids out of `examples/engine/project.json` (33 parts, 65 instances).
#: Hard-coded on purpose: if the example is re-authored and a bolt set is
#: renamed, this test should say so rather than quietly organize nothing.
ENGINE_FOLDERS = {
    "Block": ("engine_block", "main_cap", "crankshaft", "flywheel",
              "crank_pulley"),
    "Pistons": ("piston", "wrist_pin", "rod_body", "rod_cap"),
    "Fasteners": ("main_bolt_set", "flywheel_bolt_set", "rod_bolt_pair",
                  "stud_set", "head_bolt_set", "cover_bolt_set",
                  "intake_nut_set", "exhaust_nut_set", "timing_bolt_set",
                  "pan_bolt_set"),
}
FASTENERS = ENGINE_FOLDERS["Fasteners"]


def test_ac1_the_engine_example_organizes_and_the_structure_survives_a_reopen(
        kernel, tmp_path):
    """**AC1** — folders on the bundled engine example, a reload, and
    `search_parts {query: "tag:fastener"}` returning *exactly* the tagged set.

    "Reload" is the strong reading: a second `AgentCADService` over a second
    `ProjectStore` on the same directory, so the only thing carrying the
    folders and tags across is `project.json` on disk.

    Kernel-free by assertion. Metadata and search read the manifest and the
    scripts the service already owns; a project of 33 parts is organized and
    searched without one build — which is what makes organizing cheap enough
    to do while you think.
    """
    projects = tmp_path / "projects"
    projects.mkdir()
    shutil.copytree(REPO / "examples" / "engine", projects / "engine")

    counter = CountingKernel(kernel)
    service = make_test_service(projects, counter)
    registry = build_registry(service)

    on_disk = {p["id"] for p in registry.call("get_project",
                                              {"project": "engine"})["parts"]}
    for folder, ids in ENGINE_FOLDERS.items():
        assert set(ids) <= on_disk, f"{folder}: the example lost a part id"

    for folder, ids in ENGINE_FOLDERS.items():
        for part_id in ids:
            args = {"project": "engine", "part_id": part_id, "folder": folder}
            if folder == "Fasteners":
                args["tags"] = ["fastener"]
            result = registry.call("set_part_meta", args)
            assert "error" not in result, result
            assert result["folder"] == folder

    # --- reload: a new store, a new service, the same directory -------------
    reopened = make_test_service(projects, counter)
    reloaded = build_registry(reopened)
    parts = {p["id"]: p for p in
             reloaded.call("get_project", {"project": "engine"})["parts"]}
    for folder, ids in ENGINE_FOLDERS.items():
        for part_id in ids:
            assert parts[part_id]["folder"] == folder, part_id
    assert {p["id"] for p in parts.values() if p["tags"] == ["fastener"]} \
        == set(FASTENERS)
    # Untouched parts are untouched: no folder invented, no tag list grown.
    untouched = on_disk - {i for ids in ENGINE_FOLDERS.values() for i in ids}
    assert untouched, "the fixture organizes every part; widen it"
    for part_id in untouched:
        assert parts[part_id]["folder"] is None and parts[part_id]["tags"] == []

    found = reloaded.call("search_parts", {"project": "engine",
                                           "query": "tag:fastener"})
    assert "error" not in found, found
    assert found["total"] == len(FASTENERS)
    assert {row["id"] for row in found["parts"]} == set(FASTENERS)
    assert all(row["matched_on"] == ["tag"] for row in found["parts"])

    # The folder is a search axis too, and it is a *prefix* match on whole
    # segments — the tree's drag-move target and the query language are the
    # same index.
    under = reloaded.call("search_parts", {"project": "engine",
                                           "query": "folder:Fasteners"})
    assert {row["id"] for row in under["parts"]} == set(FASTENERS)

    assert counter.calls == 0, \
        "organizing and searching a project must never reach the kernel"


# ==================================================================== AC2

BROKEN_SCRIPT = '''\
from build123d import *

PARAMS = {}


def build(p):
    raise RuntimeError("this part is deliberately broken")
'''


@pytest.mark.integration
def test_ac2_state_error_finds_the_one_broken_part(kernel, tmp_path):
    """**AC2**, machine half — one broken part in a project, and the query the
    filter box sends (`state:error`) returns exactly it.

    The browser half of AC2 — "type `err`, <100 ms, and fixing the script
    clears the dot with no refresh" — is evidence-graded in
    `docs/changelog/0327-prd-027-slice6-frontend.md`: a real Chrome session
    measured **1.7 ms** around the input event to narrow 34 rows to one, and
    the error dot clearing live off `rebuild_finished`. What this test grades
    is the claim underneath it: `state` is a *live* build fact, not a manifest
    field, so it is right immediately after a rebuild and wrong never.
    """
    service = make_test_service(tmp_path / "projects", kernel)
    registry = build_registry(service)
    service.create_project("demo")
    service.store.add_part("demo", "err_bracket", "Err Bracket", "al6061",
                           BROKEN_SCRIPT)
    service.store.add_part("demo", "good_plate", "Good Plate", "al6061",
                           BOX_SCRIPT)

    # Before anything is built, nothing is in error — `unbuilt` is its own
    # state, and a listing must not slander a part it has never run.
    assert registry.call("search_parts", {"project": "demo",
                                          "query": "state:error"})["total"] == 0

    for part_id in ("err_bracket", "good_plate"):
        registry.call("get_part", {"project": "demo", "part_id": part_id})

    found = registry.call("search_parts", {"project": "demo",
                                           "query": "state:error"})
    assert [row["id"] for row in found["parts"]] == ["err_bracket"]
    assert found["parts"][0]["state"] == "error"

    # The free-text query the human actually types reaches the same row.
    typed = registry.call("search_parts", {"project": "demo", "query": "err"})
    assert [row["id"] for row in typed["parts"]] == ["err_bracket"]

    # Fix the script, rebuild, and the row leaves the error set — no cache to
    # invalidate, because `state` was never cached with the manifest rows.
    assert "error" not in registry.call(
        "update_part_script",
        {"project": "demo", "part_id": "err_bracket", "script": BOX_SCRIPT})
    assert registry.call("search_parts", {"project": "demo",
                                          "query": "state:error"})["total"] == 0
    assert registry.call("search_parts", {"project": "demo",
                                          "query": "state:ok"})["total"] == 2


# ==================================================================== AC3

@pytest.mark.integration
def test_ac3_a_script_change_mints_a_new_thumb_under_a_new_key(kernel,
                                                               tmp_path):
    """**AC3** — a built part has a thumbnail addressed by its build cache
    key, and a script change moves both the key and the file.

    `get_project`'s `thumb_key` is the handle the tree row's `<img>` is built
    from (`…/thumb.png?k=<thumb_key>`), which is what buys the `immutable`
    cache header: the client names the content it wants, so a rebuild cannot
    serve it something stale — it mints a *different* URL.
    """
    service = make_test_service(tmp_path / "projects", kernel)
    registry = build_registry(service)
    service.create_project("demo")
    service.store.add_part("demo", "box", "Box", "al6061", BOX_SCRIPT)

    def thumb_key() -> str | None:
        parts = registry.call("get_project", {"project": "demo"})["parts"]
        return next(p["thumb_key"] for p in parts if p["id"] == "box")

    assert thumb_key() is None, "an unbuilt part previews nothing"

    registry.call("get_part", {"project": "demo", "part_id": "box"})
    key1 = thumb_key()
    assert key1 and re.fullmatch(r"[0-9a-f]{32}", key1)

    cache = service.store.cache_dir("demo")
    png1, served = thumbnails.part_thumb(service, "demo", "box")
    assert served == key1
    assert thumbnails.thumb_path(cache, key1).is_file()
    assert png1.startswith(b"\x89PNG")

    # A real script change — a new content hash, a new key, a new thumbnail
    # beside the old one. The box becomes a slab rather than just a bigger
    # cube on purpose: the thumbnail is rendered **fit to frame**, so a
    # uniform scale change produces byte-identical pixels (measured). That is
    # exactly why the identity of a thumbnail is its *key* and never its
    # bytes — and why a client can be handed an `immutable` response.
    changed = BOX_SCRIPT.replace("Box(p.size, p.size, p.size)",
                                 "Box(p.size, p.size * 2, p.size)")
    assert changed != BOX_SCRIPT
    assert "error" not in registry.call(
        "update_part_script",
        {"project": "demo", "part_id": "box", "script": changed})

    key2 = thumb_key()
    assert key2 and key2 != key1
    png2, served2 = thumbnails.part_thumb(service, "demo", "box")
    assert served2 == key2
    assert thumbnails.thumb_path(cache, key2).is_file()
    assert png2 != png1
    # The old thumb is content-addressed derived data: it stays until the
    # janitor sweeps it, so a client still holding key1 gets a 200, not a 404.
    assert thumbnails.thumb_path(cache, key1).is_file()


# ==================================================================== AC4

@pytest.mark.integration
@_needs_git
def test_ac4_six_parts_one_material_change_one_undo_step(kernel, tmp_path):
    """**AC4** — six parts, one `bulk_part_op material`, ONE history entry,
    and one `undo` that puts all six back.

    Restated here through the registry because the counting is the whole
    point: a bulk gesture composed of six service calls would be six
    `project_changed` publishes, six git snapshots and six undo entries, and
    the human would press ⌘Z six times to get back. The exhaustive case list
    (per-item partial success, the other five ops, the pre-write refusals) is
    `tests/test_tools_navigation.py`.
    """
    bus = EventBus()
    service = AgentCADService(tmp_path / "projects", kernel, bus)
    registry = build_registry(service)
    assert "error" not in registry.call("create_project", {"name": "demo"})
    parts = ["main_bolt_set", "head_bolt_set", "rod_bolt_pair",
             "pan_bolt_set", "timing_bolt_set", "flywheel_bolt_set"]
    for part_id in parts:
        service.store.add_part("demo", part_id, part_id, "al6061", BOX_SCRIPT)
    # One baseline snapshot, so `undo` has a state to go back to.
    assert "error" not in registry.call(
        "set_part_meta", {"project": "demo", "part_id": parts[0],
                          "folder": "Fasteners", "tags": ["fastener"]})

    before = registry.call("get_history", {"project": "demo"})
    assert before.get("available") is True
    depth = len(before["undo"])

    queue = bus.subscribe()
    result = registry.call("bulk_part_op", {
        "project": "demo", "part_ids": parts, "op": "material",
        "args": {"material": "steel_a36"}})
    assert result["ok"] is True and result["applied"] == 6
    assert result["undo_label"] == "bulk material ×6"
    assert all(row["ok"] for row in result["results"])

    published = []
    while not queue.empty():
        published.append(queue.get_nowait())
    assert len([e for e in published if e["type"] == "project_changed"]) == 1
    meta = [e for e in published if e["type"] == "parts_meta_changed"]
    assert len(meta) == 1
    assert meta[0]["part_ids"] == parts and meta[0]["fields"] == ["material"]

    after = registry.call("get_history", {"project": "demo"})
    assert len(after["undo"]) == depth + 1, \
        "six per-part writes would be six undo entries"
    assert after["undo"][0] == "project_changed (bulk material ×6)"

    for part_id in parts:
        assert service.store.get_part("demo", part_id).material == "steel_a36"
    assert "error" not in registry.call("undo", {"project": "demo"})
    for part_id in parts:
        assert service.store.get_part("demo", part_id).material == "al6061"


# ==================================================================== AC5

@_needs_node
def test_ac5_a_thousand_part_tree_flattens_and_windows_to_a_few_dozen_rows():
    """**AC5**, model half — 1 000 parts flatten to 1 000 rows plus their
    folders, and the virtual window over them is a few dozen rows wide.

    Virtualization is the only reason a 1 000-row tree can be interactive at
    all, and it is a property of two pure functions: `tree_model.folderTree`
    (linear, collapse-aware) and `virtual_model.window` (bounded, and its two
    spacers plus the rendered rows sum to the exact scroll height, or the
    scrollbar lies about where you are).

    The browser measurement is evidence-graded in
    `docs/changelog/0327-prd-027-slice6-frontend.md`: 1 009 rows rendered
    **43 `<li>`**, the tree's own rAF callback measured **0.49 ms/frame**
    (median of 5x50), and the wall-clock per scroll step met the 16.7 ms bar
    with the WebGL viewport out of the loop and not with SwiftShader in it —
    stated there rather than claimed as 60 fps.

    `import * as virtual`: a named `window` import shadows the browser global.
    """
    frontend = REPO / "frontend" / "js"
    prelude = (
        f'import * as tm from {json.dumps((frontend / "tree_model.js").as_uri())};\n'
        f'import * as virtual from {json.dumps((frontend / "virtual_model.js").as_uri())};\n'
        'const out = (v) => process.stdout.write(JSON.stringify(v));\n'
    )
    body = """
      const many = [];
      for (let i = 0; i < 1000; i += 1) {
        many.push({id: `p${i}`, label: `P ${i}`, folder: `F${i % 25}`,
                   tags: [], state: "ok", kind: "script"});
      }
      const rows = tm.folderTree(many);
      const rowHeight = 28, viewportHeight = 600;
      const at = (scrollTop) => virtual.window(
        {scrollTop, viewportHeight, rowHeight, total: rows.length});
      out({rows: rows.length,
           folders: rows.filter((r) => r.kind === "folder").length,
           collapsed: tm.folderTree(many,
             {collapsed: Array.from({length: 25}, (_, i) => `F${i}`)}).length,
           windows: [at(0), at(7000), at(1e9)]});
    """
    proc = subprocess.run(["node", "--input-type=module", "--eval",
                           prelude + body],
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    got = json.loads(proc.stdout)

    assert got["rows"] == 1025 and got["folders"] == 25
    assert got["collapsed"] == 25, "collapsing 25 folders hides 1 000 parts"

    height = 1025 * 28
    for window in got["windows"]:
        rendered = window["end"] - window["start"]
        # 600 / 28 = 21.4 visible rows, plus the default overscan of 8 a side.
        assert rendered <= 60, rendered
        assert window["padTop"] + rendered * 28 + window["padBottom"] == height
        assert 0 <= window["start"] <= window["end"] <= 1025
    assert got["windows"][-1]["end"] == 1025          # clamped at the bottom


# ==================================================================== AC6

@pytest.fixture
def nav_service(kernel, tmp_path):
    return make_test_service(tmp_path / "projects", kernel)


def test_ac6_the_three_navigation_tools_are_registered_and_documented(
        nav_service):
    """A tool an agent cannot discover is a tool that does not exist, and one
    the reference does not carry is one no agent will reach for."""
    registered = {tool.name for tool in build_registry(nav_service).list()}
    assert set(NAV_TOOLS) <= registered

    api = AGENT_API.read_text(encoding="utf-8")
    for tool in NAV_TOOLS:
        assert tool in api, f"docs/agent-api.md does not document {tool}"


def test_ac6_every_navigation_route_is_served_and_member_only(nav_service):
    """The four new routes exist on the real app, and not one of them is
    reachable without a credential.

    `flatten_routes`, never `[r.path for r in app.routes]`: FastAPI leaves
    each `include_router` opaque, so the naive walk sees 23 of 83 routes and
    would pass while a whole pack went public.
    """
    app = create_app(nav_service, build_registry(nav_service),
                     extra_allowed_hosts={"testserver"})
    served = flatten_routes(app)
    for method, path in NAV_ROUTES:
        assert (method, path) in served, f"{method} {path} is not mounted"
        assert not security.is_public(path), \
            f"{path} joined the anonymous surface"

    api = AGENT_API.read_text(encoding="utf-8")
    for _method, path in NAV_ROUTES:
        assert path in api, f"docs/agent-api.md does not document {path}"


def test_ac6_the_documented_tool_count_is_the_live_registry_count(nav_service):
    """The headline in `docs/agent-api.md` is compared with the registry, so
    it cannot drift silently again.

    It had drifted badly: the docs said "85 tools (88 with the optional
    `[fem]` extra)" while `build_registry` registered **104** before this PRD
    and 107 with it (109 once PRD-029's two skill tools merged in). The number in the prose is the **no-extras** count; the
    `[fem]` number is that plus the three tools `tools_analysis` registers
    only when `skfem` imports. A *hosted* server additionally registers
    `whoami` (`tools_auth`), which `make_test_service` is not — so this test
    measures the local surface, exactly like the sentence it grades.
    """
    from agentcad.core.specs import _fem_available

    live = len(build_registry(nav_service).list())
    api = " ".join(AGENT_API.read_text(encoding="utf-8").split())
    match = re.search(r"(\d+) tools \((\d+) with the optional", api)
    assert match, "docs/agent-api.md no longer states a tool count"
    documented, with_fem = int(match.group(1)), int(match.group(2))

    expected = documented + (3 if _fem_available() else 0)
    assert live == expected, (
        f"docs/agent-api.md says {documented} tools"
        f"{' (+3 for the [fem] extra, which is installed)' if _fem_available() else ''}"
        f" but build_registry registers {live}"
    )
    assert with_fem == documented + 3, \
        "the [fem] extra adds exactly fem_static, fem_modal and fem_thermal"

    # The same number, spelled in every other place a reader may land on
    # first. This tuple IS the enforcement — a doc that states the count and is
    # not listed here is a doc free to go stale, which is exactly how 85 stood
    # against 104 for four PRDs. Add a row when you add a spelling.
    for path, needles in (
        (ARCHITECTURE, (f"**{documented} tools**",
                        f"({with_fem} with the optional")),
        (USER_GUIDE, (f"{documented}-tool surface",)),
        (REPO / "README.md", (f"**{documented} tools**",
                              f"{documented}-tool agent surface")),
        (REPO / "AGENTS.md", (f"the {documented}/{with_fem} agent tools",)),
        (REPO / "docs" / "roadmap.md", (f"{documented}/{with_fem} today",)),
        # Both of market_research's mentions, pinned separately: they are
        # two different phrasings and fixing one is not fixing the other.
        (REPO / "docs" / "market_research.md",
         (f"{documented}-tool agent surface", f"a {documented}-tool surface")),
    ):
        text = " ".join(path.read_text(encoding="utf-8").split())
        for needle in needles:
            assert needle in text, f"{path.name} does not carry {needle!r}"


def test_ac6_the_documentation_describes_the_shipped_navigation_surface():
    """The docs half, graded on the things a reader has to be able to find:
    the query grammar an agent must type correctly, the event a client must
    react to, the sidebar and dashboard a human uses, the modules and the
    cache a contributor has to know about, and the traps."""
    api = AGENT_API.read_text(encoding="utf-8")
    for needle in ("parts_meta_changed", "matched_on", "snippet",
                   "undo_label", "thumb_key", "cache_key", "immutable"):
        assert needle in api, f"docs/agent-api.md does not cover {needle!r}"
    # The grammar paragraph is the tool's own constant, quoted verbatim: an
    # agent reading the page and an agent reading `GET /api/tools` must be
    # given the same sentence, not two paraphrases that drift.
    from agentcad.core.search import GRAMMAR

    assert GRAMMAR in " ".join(api.split()), \
        "docs/agent-api.md does not quote search.GRAMMAR verbatim"

    guide = USER_GUIDE.read_text(encoding="utf-8")
    assert "## Dashboard" in guide
    for needle in ("dashboard", "Folders", "Tags", "bulk", "thumbnail"):
        assert needle in guide, f"docs/user-guide.md does not cover {needle!r}"
    # The two chords slice 6 added to the table (PRD-026's chord test owns the
    # table itself; this only asserts the filter shortcut is findable here).
    assert "| **/** |" in guide
    assert "Cmd+Shift+O" in guide

    architecture = ARCHITECTURE.read_text(encoding="utf-8")
    for needle in ("core/navigation.py", "core/search.py",
                   "core/thumbnails.py", "routes_navigation.py",
                   "routes_thumbnails.py", "tools_navigation.py",
                   ".cache/<key>.thumb.png", "asm-"):
        assert needle in architecture, \
            f"docs/architecture.md does not cover {needle!r}"

    for path in (REPO / "AGENTS.md", REPO / "CLAUDE.md"):
        text = path.read_text(encoding="utf-8")
        assert "PRD-027" in text, f"{path.name} has no PRD-027 trap block"
        for needle in ("_TRIMMABLE", "AGENTCAD_THUMBNAILS",
                       "update_parts_meta"):
            assert needle in text, f"{path.name} does not warn about {needle!r}"


def test_ac6_the_full_suite_count_is_cited():
    """**AC6** — "full suite green" is a claim about a *run*; the evidence is
    a `make test` count on the record in the newest changelog entry (the
    PRD-004 AC10 / PRD-012 AC8 / PRD-026 AC7 precedent). Recomputing the
    number here would mean running the full suite from inside itself, and
    `--collect-only` counts cases, not what `make test` reports.
    """
    latest = max(CHANGELOG.glob("0[0-9][0-9][0-9]-*.md"))
    text = latest.read_text(encoding="utf-8")
    assert "make test" in text, \
        f"{latest.name} is the newest changelog entry and cites no `make test`"
    assert re.search(r"\b\d{3,6}\s+passed\b", text.replace(",", "")), \
        f"{latest.name} does not cite a `make test` suite count"


# ==================================================== the record itself

def test_the_roadmap_link_resolves_to_the_prd_where_it_actually_lives():
    """The house meta-test: the roadmap's row for this PRD links to the folder
    the PRD is actually in, and the two move in the same commit."""
    roadmap = (REPO / "docs" / "roadmap.md").read_text(encoding="utf-8")
    row = next(line for line in roadmap.splitlines()
               if line.startswith("| [027]"))
    match = re.search(r"\((prd/[^)]+\.md)\)", row)
    assert match, f"the roadmap row for PRD-027 carries no link: {row}"
    assert (REPO / "docs" / match.group(1)) == PRD, \
        f"the roadmap points at {match.group(1)} but the PRD is at {PRD}"
