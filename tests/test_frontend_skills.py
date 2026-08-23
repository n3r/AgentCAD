"""PRD-029 slice 6 — the agent-skills panel's pure model, in node (the
`test_frontend_materials.py` harness shape), plus one live-contract check that
the HTTP surface the view depends on answers what `skills.js` assumes.

`frontend/js/skills_model.js` is pure (no DOM, no imports) so its badge
vocabulary, sort, consent test, chip label, chat-client filter and the three
formatters all run in node exactly as they run in the browser.

The live half is import-guarded: `server/routes_skills.py` is slice 2's, and
this file has to stay green while that lands.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agentcad.core.tools import build_registry
from agentcad.server import security as security_module
from agentcad.server.app import create_app

from .conftest import make_test_service

FRONTEND = Path(__file__).resolve().parents[1] / "frontend"
MODEL = FRONTEND / "js" / "skills_model.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is not installed")

HARNESS = """
import {{ __skillsModel__ }} from {module};
const call = process.env.AGENTCAD_CALL;
const args = JSON.parse(process.env.AGENTCAD_ARGS);
const fn = __skillsModel__[call];
const result = fn(...args);
process.stdout.write(JSON.stringify(result === undefined ? null : result));
"""


def run(call, *args):
    script = HARNESS.format(module=json.dumps(MODEL.as_uri()))
    env = {**os.environ, "AGENTCAD_CALL": call, "AGENTCAD_ARGS": json.dumps(args)}
    out = subprocess.run(
        ["node", "--input-type=module", "--eval", script],
        capture_output=True, text=True, timeout=60, env=env)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def entry(**kw):
    base = {"name": "snap-fits", "description": "Snap-fit design.",
            "layer": "core", "version": "1.0.0", "triggers": [], "requires": [],
            "overrides": None, "trusted": True, "enabled": True, "invalid": None}
    base.update(kw)
    return base


# --------------------------------------------------------------------- badgeFor


def test_badge_for_a_core_skill_is_just_the_layer():
    assert run("badgeFor", entry(), None) == [
        {"text": "core", "cls": "badge-core"}]


def test_badge_for_a_trusted_project_skill_names_the_layer_only():
    got = run("badgeFor", entry(layer="project", trusted=True),
              {"trusted": {"snap-fits": "abc"}, "disabled": []})
    assert got == [{"text": "project", "cls": "badge-project"}]


def test_badge_for_a_project_skill_that_shadows_a_core_one_says_overrides_core():
    got = run("badgeFor", entry(layer="project", trusted=True, overrides="core"),
              None)
    assert got == [{"text": "project", "cls": "badge-project"},
                   {"text": "overrides core", "cls": "badge-overrides"}]


def test_an_untrusted_project_skill_the_trust_map_never_saw_needs_review():
    got = run("badgeFor", entry(layer="project", trusted=False),
              {"trusted": {}, "disabled": []})
    assert got[-1] == {"text": "needs review", "cls": "badge-review"}


def test_an_untrusted_project_skill_with_a_stale_digest_changed_since_trusted():
    """The `git pull` that rewrites an approved skill is the attack the digest
    keying exists for, and the panel has to say WHICH of the two untrusted
    states it is looking at."""
    got = run("badgeFor", entry(layer="project", trusted=False),
              {"trusted": {"snap-fits": "an-older-digest"}, "disabled": []})
    assert got[-1] == {"text": "changed since trusted", "cls": "badge-review"}


def test_without_a_trust_map_the_weaker_claim_is_the_one_made():
    """No map means no evidence that a human ever approved this name — so the
    badge says "needs review", never the stronger "changed"."""
    got = run("badgeFor", entry(layer="project", trusted=False), None)
    assert got[-1] == {"text": "needs review", "cls": "badge-review"}


def test_an_invalid_skill_carries_the_invalid_badge_last():
    got = run("badgeFor",
              entry(layer="project", trusted=False, invalid="missing_key:version"),
              {"trusted": {}, "disabled": []})
    assert [b["cls"] for b in got] == [
        "badge-project", "badge-review", "badge-invalid"]
    assert got[-1]["text"] == "invalid"


def test_a_core_skill_is_never_asked_to_be_reviewed_even_when_trusted_is_false():
    """Core skills are trusted by construction; a payload that says otherwise
    is the server's business, not a review prompt in the UI."""
    got = run("badgeFor", entry(layer="core", trusted=False), None)
    assert [b["cls"] for b in got] == ["badge-core"]


def test_an_unknown_layer_still_gets_a_badge_rather_than_a_blank_row():
    got = run("badgeFor", entry(layer="team"), None)
    assert got == [{"text": "team", "cls": "badge-layer"}]
    org = run("badgeFor", entry(layer="org"), None)
    assert org == [{"text": "org", "cls": "badge-org"}]


def test_badge_for_a_missing_entry_does_not_throw():
    assert run("badgeFor", None, None) == [
        {"text": "unknown", "cls": "badge-layer"}]


# --------------------------------------------------------------------- sortRows


def test_sort_rows_is_name_order_regardless_of_layer():
    rows = [entry(name="snap-fits"), entry(name="brackets", layer="project"),
            entry(name="holes")]
    assert [r["name"] for r in run("sortRows", rows)] == [
        "brackets", "holes", "snap-fits"]


def test_sort_rows_is_stable_for_equal_names_and_tolerates_junk():
    rows = [{"name": "a", "id": 1}, {"name": "a", "id": 2}, {"id": 3}]
    assert [r.get("id") for r in run("sortRows", rows)] == [3, 1, 2]
    assert run("sortRows", None) == []


# ------------------------------------------------------------------ needsConsent


def test_needs_consent_only_for_an_untrusted_project_skill():
    assert run("needsConsent", [entry(), entry(name="b", layer="project",
                                              trusted=False)]) is True
    assert run("needsConsent", [entry(), entry(name="b", layer="project",
                                              trusted=True)]) is False
    assert run("needsConsent", [entry(trusted=False)]) is False  # core
    assert run("needsConsent", []) is False
    assert run("needsConsent", None) is False


# --------------------------------------------------------------------- chipLabel


def test_chip_label_names_the_skill_and_the_layer():
    assert run("chipLabel", {"name": "snap-fits", "layer": "core"}) == \
        "📘 snap-fits · core"
    assert run("chipLabel", {"name": "ours", "layer": "project"}) == \
        "📘 ours · project"


def test_chip_label_without_a_layer_still_names_the_skill():
    assert run("chipLabel", {"name": "ours"}) == "📘 ours"
    assert run("chipLabel", {}) == "📘 skill"
    assert run("chipLabel", None) == "📘 skill"


def test_chip_label_of_an_asset_read_names_the_file_not_the_layer():
    """An asset read is its own budget entry (`agent/chat.py`), so it is its
    own chip: "the agent read this file", not "the agent loaded this guide"."""
    assert run("chipLabel", {"name": "snap-fits", "layer": "core",
                             "asset": "snippets/lid.py"}) == \
        "📎 snap-fits · snippets/lid.py"


# ------------------------------------------------------------------- sessionOf


@pytest.mark.parametrize("client,session", [
    ("chat", "main"), ("chat:main", "main"), ("chat:lane", "lane"),
    ("chat:a_b-1", "a_b-1"),
])
def test_session_of_mirrors_the_engines_own_client_ids(client, session):
    """`agent/chat.py::_call_tool` stamps "chat" for the default lane and
    "chat:<session>" for any other; `core/tools_skills.py` derives the event's
    `session` the same way. This is the third copy of one rule, and the reason
    it is tested in node is that the dock filters chips with it."""
    assert run("sessionOf", client) == session


@pytest.mark.parametrize("client", [
    "mcp", "local", "browser:7f3a1b2c", "chatty", "chat:", "chat:MAIN",
    "chat:x/y", "chat:" + "x" * 33, "", None, 7,
])
def test_session_of_is_null_for_everything_that_is_not_a_chat_lane(client):
    assert run("sessionOf", client) is None


# ------------------------------------------------------------------ isChatClient


@pytest.mark.parametrize("client", ["chat", "chat:main", "chat:browser",
                                    "chat:a_b-1", "chat:" + "x" * 32])
def test_is_chat_client_accepts_the_chat_engines_own_ids(client):
    assert run("isChatClient", client) is True


@pytest.mark.parametrize("client", [
    "mcp", "local", "browser:7f3a1b2c", "chatty", "chat:", "chat:MAIN",
    "chat:x/y", " chat", "chat\nchat", "chat:" + "x" * 33, "", None, 7, ["chat"],
])
def test_is_chat_client_refuses_every_other_client(client):
    """The Skills modal's own preview reads through `load_skill`, so a browser
    id reaching this filter must render no chip in the dock."""
    assert run("isChatClient", client) is False


# ------------------------------------------------------------------ formatAssets


def test_format_assets_renders_paths_with_human_sizes():
    got = run("formatAssets", [
        {"path": "snippets/cantilever_lid.py", "bytes": 2048},
        {"path": "tables/material_strain.json", "bytes": 512},
        {"path": "big.bin", "bytes": 3 * 1024 * 1024},
    ])
    assert got == ["snippets/cantilever_lid.py · 2 kB",
                   "tables/material_strain.json · 512 B",
                   "big.bin · 3 MB"]


def test_format_assets_drops_a_size_it_could_not_measure():
    assert run("formatAssets", [{"path": "a.py"}, {"path": "b.py", "bytes": None}]) \
        == ["a.py", "b.py"]
    assert run("formatAssets", []) == []
    assert run("formatAssets", None) == []


# ---------------------------------------------------------------- provenanceLine


def test_provenance_line_joins_what_exists_and_cuts_the_digest():
    got = run("provenanceLine", {
        "layer": "project", "path": "skills/ours.md", "author": "Team",
        "license": "MIT", "digest": "0123456789abcdef" * 4})
    assert got == "project · skills/ours.md · by Team · MIT · sha256:0123456789ab"


def test_provenance_line_of_a_core_skill_has_no_path():
    got = run("provenanceLine", {"layer": "core", "path": None,
                                 "author": "AgentCAD core",
                                 "license": "Apache-2.0", "digest": "ff" * 32})
    assert got == "core · by AgentCAD core · Apache-2.0 · sha256:ffffffffffff"
    assert run("provenanceLine", None) == ""


# --------------------------------------------------------------- truncationNote


def test_truncation_note_names_the_sections_the_agent_did_not_see():
    got = run("truncationNote", {"truncated": True,
                                 "omitted_sections": ["## Sources", "## Tables"]})
    assert got == "truncated — 2 sections omitted: ## Sources, ## Tables"
    one = run("truncationNote", {"truncated": True, "omitted_sections": ["## X"]})
    assert one == "truncated — 1 section omitted: ## X"


def test_truncation_note_is_empty_when_the_whole_body_came_through():
    assert run("truncationNote", {"truncated": False,
                                  "omitted_sections": []}) == ""
    assert run("truncationNote", None) == ""
    assert run("truncationNote", {"truncated": True, "omitted_sections": []}) == \
        "truncated — the tail of this skill was cut to fit the budget"


# ============================================================ the view's wiring
# Source assertions, not behaviour: these are the four couplings between the
# panel and the rest of the shell that a rename would silently break.


def test_the_skills_panel_is_registered_as_an_action_a_view_and_a_button():
    main = (FRONTEND / "js" / "main.js").read_text(encoding="utf-8")
    row = main.split('id: "agent.skills"', 1)[1].split("A({", 1)[0]
    assert 'group: "Agent"' in row
    assert "skills.open()" in row
    assert 'actions.run("agent.skills", null, { source: "toolbar" })' in main
    button = main.split('getElementById("skills-btn")', 1)[1][:220]
    assert "skills.open()" not in button, \
        "the toolbar button must go through the action registry"
    panel = (FRONTEND / "js" / "skills.js").read_text(encoding="utf-8")
    assert "dialogs.attachLegacy(" in panel and 'view: "skills"' in panel
    assert 'actionId: "agent.skills"' in panel
    index = (FRONTEND / "index.html").read_text(encoding="utf-8")
    assert 'id="skills-btn"' in index and 'id="skills-modal"' in index


def test_the_three_skill_events_reach_the_right_handlers():
    main = (FRONTEND / "js" / "main.js").read_text(encoding="utf-8")
    loaded = main.split('case "skill_loaded":', 1)[1].split("case ", 1)[0]
    assert loaded.strip() == "" or "chat.handleEvent" in loaded
    assert "chat.handleEvent" in main.split(
        'case "skill_unloaded":', 1)[1].split("case ", 1)[0]
    changed = main.split('case "skills_changed":', 1)[1].split("case ", 1)[0]
    assert "skills.refresh()" in changed and "skills.isOpen()" in changed
    chat = (FRONTEND / "js" / "chat.js").read_text(encoding="utf-8")
    assert "isChatClient(ev.client)" in chat, \
        "the chip must filter on the chat engine's own client ids"
    # …and on the LANE: a load in `chat:lane` must not draw a chip in the
    # dock's "main" lane, where its `skill_unloaded` (which carries a session)
    # is filtered out and the chip could never be un-struck.
    assert "sessionOf(ev.client) !== DOCK_SESSION" in chat
    assert "addSkillChip" in chat and "markSkillUnloaded" in chat
    # The existing tool-chip flow is untouched.
    assert 'case "chat_tool_call":' in chat and "addToolChip(" in chat


def test_no_server_string_reaches_the_dom_as_markup():
    """A skill body is third-party text written to be read by a language model.
    It reaches the DOM as `textContent`, always."""
    panel = (FRONTEND / "js" / "skills.js").read_text(encoding="utf-8")
    assert "innerHTML" not in panel
    assert "pre.textContent = payload.content" in panel


# =================================================================== HTTP live

# Slice 2 owns `server/routes_skills.py`. Guarded per test, not with a
# module-level `importorskip`, so the node half above keeps running while that
# lands — and so the live half turns itself on the moment it does.
_HAS_ROUTES = (Path(__file__).resolve().parents[1] / "agentcad" / "server"
               / "routes_skills.py").exists()
live = pytest.mark.skipif(not _HAS_ROUTES,
                          reason="server/routes_skills.py has not landed yet")

SKILL_TEXT = """\
---
name: ours
description: A project skill written by this project's own authors.
version: 1.0.0
license: MIT
author: Test Project
---
# Ours

Body.
"""


def _client(kernel, tmp_path):
    security_module.install(None)
    service = make_test_service(tmp_path / "projects", kernel)
    app = create_app(service, build_registry(service),
                     extra_allowed_hosts={"testserver"})
    return service, TestClient(app, base_url="http://127.0.0.1")


def _project_with_a_skill(service, name="skilled"):
    service.create_project(name)
    root = service.store.path_of(name) / "skills"
    root.mkdir(parents=True, exist_ok=True)
    (root / "ours.md").write_text(SKILL_TEXT, encoding="utf-8")
    return name


@live
def test_the_index_route_carries_every_key_the_view_reads(kernel, tmp_path):
    service, client = _client(kernel, tmp_path)
    proj = _project_with_a_skill(service)
    r = client.get(f"/api/projects/{proj}/skills",
                   headers={"X-Agent-Id": "browser:test"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) >= {"skills", "hidden", "trust"}
    assert isinstance(body["trust"].get("trusted"), dict)
    by_name = {s["name"]: s for s in body["skills"]}
    assert "ours" in by_name, body["skills"]
    ours = by_name["ours"]
    # Exactly the keys `skillRow`/`badgeFor` read off a row.
    assert set(ours) >= {"name", "description", "layer", "version", "triggers",
                         "requires", "overrides", "trusted", "enabled", "invalid"}
    assert ours["layer"] == "project"
    assert ours["trusted"] is False, "a project skill starts untrusted"


@live
def test_a_browser_client_can_trust_and_an_agent_client_cannot(kernel, tmp_path):
    """The panel's whole reason to exist: granting trust is a route refused to
    every non-human client, so no agent surface can approve agent
    instructions."""
    service, client = _client(kernel, tmp_path)
    proj = _project_with_a_skill(service)

    denied = client.post(f"/api/projects/{proj}/skills/ours/trust",
                         headers={"X-Agent-Id": "mcp"})
    assert denied.status_code == 403, denied.text

    ok = client.post(f"/api/projects/{proj}/skills/ours/trust",
                     headers={"X-Agent-Id": "browser:test"})
    assert ok.status_code == 200, ok.text
    assert ok.json()["trusted"] is True

    after = client.get(f"/api/projects/{proj}/skills",
                       headers={"X-Agent-Id": "browser:test"}).json()
    entry_ = {s["name"]: s for s in after["skills"]}["ours"]
    assert entry_["trusted"] is True
    assert after["trust"]["trusted"].get("ours"), \
        "the trust map keys the panel's `changed since trusted` badge"


@live
def test_the_preview_route_carries_the_payload_the_detail_pane_renders(
        kernel, tmp_path):
    service, client = _client(kernel, tmp_path)
    proj = _project_with_a_skill(service)
    headers = {"X-Agent-Id": "browser:test"}
    client.post(f"/api/projects/{proj}/skills/ours/trust", headers=headers)

    r = client.get(f"/api/projects/{proj}/skills/ours", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) >= {"name", "layer", "version", "content", "chars",
                         "truncated", "omitted_sections", "assets", "provenance"}
    assert "Body." in body["content"]
    assert set(body["provenance"]) >= {"layer", "author", "license", "path",
                                       "digest"}
    assert body["provenance"]["path"] == "skills/ours.md"


@live
def test_an_untrusted_preview_is_readable_because_reviewing_is_the_point(
        kernel, tmp_path):
    """The panel exists so a person can decide whether to trust a skill, and
    they cannot decide without reading it. The human read is served
    (`enforce_trust=False`); the index row beside it still says `trusted:
    false`, which is what draws the "needs review" badge and the Trust button.
    An agent asking for the same skill is still refused."""
    service, client = _client(kernel, tmp_path)
    proj = _project_with_a_skill(service)
    human = {"X-Agent-Id": "browser:test"}

    r = client.get(f"/api/projects/{proj}/skills/ours", headers=human)
    assert r.status_code == 200, r.text
    assert "Body." in r.json()["content"]

    row = {s["name"]: s for s in client.get(f"/api/projects/{proj}/skills",
                                            headers=human).json()["skills"]}
    assert row["ours"]["trusted"] is False

    refused = client.get(f"/api/projects/{proj}/skills/ours",
                         headers={"X-Agent-Id": "mcp"})
    assert refused.status_code == 422, refused.text
    err = refused.json()["error"]
    assert err["details"]["reason"] == "skill_untrusted"
    assert err["details"].get("hint"), "the pane renders the hint under the error"
