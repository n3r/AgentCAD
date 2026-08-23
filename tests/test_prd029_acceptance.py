"""PRD-029 Agent skills & knowledge packs — acceptance, AC1–AC7.

One test (or a small, named group) per criterion, graded against the shipped
surface rather than a stub built for the occasion. Where a slice's own suite
already proves the exact same claim, this file restates it *compactly* rather
than duplicating the case list — the house rule from
`tests/test_prd012_acceptance.py` / `tests/test_prd026_acceptance.py`.

| AC | Test |
|---|---|
| AC1 | `test_ac1_a_snap_fit_lid_loads_the_skill_and_builds_green` |
| AC2 | `test_ac2_list_skills_ranks_the_sheet_metal_skill_first`, `test_ac2_an_oversized_skill_loads_truncated_with_its_sections_intact` |
| AC3 | `test_ac3_a_project_skill_overrides_the_core_one_on_both_surfaces` |
| AC4 | `test_ac4_a_fem_requiring_skill_is_hidden_without_the_capability` |
| AC5 | `test_ac5_the_bench_delta_report_prints_the_with_and_without_scores` |
| AC6 | `tests/test_skills_branching.py` (a whole file: git is involved) |
| AC7 | `test_ac7_part_template_still_returns_the_contract`, `test_ac7_the_full_suite_count_is_cited` (full coverage: `tests/test_part_template_compat.py`) |

Four things worth reading before you believe them:

* **AC1 runs the real tool loop.** The Anthropic client is scripted
  (`tests/test_chat.py::FakeAnthropic` — no network), but everything after it
  is production code: `ChatEngine` builds the system prompt from the real
  library, calls the registered `load_skill`, which publishes the real
  `skill_loaded` on the real bus, and then feeds the skill's OWN shipped
  snippet to the registered `create_part`, which builds it in a real kernel.
  The assertion the PRD asks for ("the produced part builds green") is
  therefore a kernel verdict, not a mock's.
* **The chip is not asserted here.** `skill_loaded` → a `.skill-chip` in the
  dock is `frontend/js/chat.js`, graded by `tests/test_frontend_skills.py`
  (node harness + `isChatClient`) and browser-verified in slice 6's report.
  What this file owns is the event that chip is drawn from — including its
  `client`, which is the field the chip filters on.
* **AC5 runs the bench, offline.** It drives the real `agentcad bench run`
  twice (`--skills none`, `--skills snap-fits`) and then the real `bench
  report --baseline`, with `tests/test_bench_skills.py`'s scripted client —
  ~16 s, no network, no API key. It used to `read_text()` that file and grep
  for `"--skills"`, which is a claim about *source text* and passes against a
  commented-out test.
* **AC7's count guard reads the newest changelog.** "Full suite green" is a
  claim about a *run*; recomputing it here would mean running the full suite
  from inside itself (the PRD-004 AC10 / PRD-026 AC7 precedent).
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agentcad.agent.chat import ChatEngine
from agentcad.core import skills as sk
from agentcad.core.service import EventBus
from agentcad.core.skills import SkillBudget, split_sections
from agentcad.core.tools import build_registry
from agentcad.server.app import create_app

from .conftest import make_test_service
from .test_chat import FakeAnthropic, _drain, _response, _text, _tool_use
from .test_skills_library import write_skill
from .test_skills_tools import _UnusedKernel

PROJECT = "prd029"
REPO = Path(__file__).resolve().parent.parent
CHANGELOG = REPO / "docs" / "changelog"


def _dry_stack(tmp_path):
    """Service + registry + bus with a kernel that must never be called.

    Every criterion except AC1 is a filesystem read over `service.skills`; a
    kernel touch in one of them would be a bug worth failing on.
    """
    bus = EventBus()
    service = make_test_service(tmp_path / "projects", _UnusedKernel(), bus)
    service.create_project(PROJECT)
    return service, build_registry(service), bus


def _project_skills(service, proj: str = PROJECT) -> Path:
    root = service.store.path_of(proj) / "skills"
    root.mkdir(parents=True, exist_ok=True)
    return root


# ============================================================ AC1

def test_ac1_a_snap_fit_lid_loads_the_skill_and_builds_green(tmp_path, kernel):
    """**AC1** — "with the snap-fit skill present, the agent asked for a
    snap-fit lid loads the skill (event logged, chip shown) and the produced
    part builds green".

    The scripted turn is three rounds: `load_skill {snap-fits}`, then
    `create_part` with the skill's own `snippets/cantilever_lid.py`, then a
    text reply. Content assertions are kept loose, exactly as the PRD asks —
    what is pinned is the *mechanism*: the audit event with its layer and its
    client, the loaded-set echo reaching the model's next system prompt, and
    a green kernel build.
    """
    bus = EventBus()
    service = make_test_service(tmp_path / "projects", kernel, bus)
    service.create_project(PROJECT)
    registry = build_registry(service)

    # The script the agent "copies out of the skill": read through the library
    # (the same bytes `load_skill {asset: …}` would hand it), before the bus
    # queue is subscribed so this read cannot be mistaken for the turn's.
    script = service.skills.load(
        "snap-fits", PROJECT, "snippets/cantilever_lid.py")["content"]
    assert "def build(p)" in script

    fake = FakeAnthropic([
        _response([
            _text("A snap-fit lid — let me read the snap-fits skill first."),
            _tool_use("tu_skill", "load_skill",
                      {"project": PROJECT, "name": "snap-fits"}),
        ], stop_reason="tool_use"),
        _response([
            _tool_use("tu_part", "create_part",
                      {"project": PROJECT, "part_id": "lid",
                       "script": script}),
        ], stop_reason="tool_use"),
        _response([_text("Built `lid`: a cantilever snap-fit lid.")],
                  stop_reason="end_turn"),
    ])
    engine = ChatEngine(registry, bus, skills=service.skills,
                        budget=SkillBudget(), api_key="test-key",
                        client_factory=lambda: fake)
    queue = bus.subscribe()

    async def main():
        info = await engine.start_turn(
            PROJECT, "a snap-fit lid for the prototyping enclosure")
        await asyncio.wait_for(engine._tasks[info["turn_id"]], timeout=55)

    asyncio.run(main())

    # 1. The load is on the audit bus, with the provenance the chip renders.
    loaded = [e for e in _drain(queue) if e["type"] == "skill_loaded"]
    assert len(loaded) == 1, loaded
    event = loaded[0]
    assert event["name"] == "snap-fits"
    assert event["layer"] == "core"
    # `client` and `session` are what `chat.js` filters on to draw the chip: a
    # browser read never reaches the tool at all, and a load in another chat
    # lane draws no chip in this dock.
    assert event["client"] == "chat"
    assert event["session"] == "main" and event["asset"] is None
    assert event["project"] == PROJECT and event["chars"] > 0

    # 2. The loaded set reaches the model on the very next request.
    assert "Loaded this session:" not in fake.messages.calls[0]["system"]
    assert "Loaded this session: snap-fits" in fake.messages.calls[1]["system"]
    assert [s["name"] for s in engine.loaded_skills(PROJECT)] == ["snap-fits"]

    # 3. The part the skill's own snippet produced builds green, in a real
    #    kernel, with real volume.
    part = registry.call("get_part", {"project": PROJECT, "part_id": "lid"})
    assert "error" not in part, part
    assert part["status"]["state"] == "ok", part["status"]
    assert part["metrics"]["volume_mm3"] > 0, part["metrics"]


# ============================================================ AC2

def test_ac2_list_skills_ranks_the_sheet_metal_skill_first(tmp_path):
    """**AC2**, first half — `list_skills {query: "sheet"}` ranks the
    sheet-metal skill first. Deterministic keyword scoring, not embeddings:
    "sheet" is a trigger and a name substring of exactly one shipped skill.
    """
    _, registry, _ = _dry_stack(tmp_path)

    out = registry.call("list_skills", {"query": "sheet"})

    assert out["matched"] is True
    assert out["skills"][0]["name"] == "sheet-metal"


@pytest.mark.parametrize("query,first", [
    ("make a snap fit lid", "snap-fits"),
    ("a bracket for a NEMA 17 motor", "brackets-and-mounts"),
    ("sheet", "sheet-metal"),
])
def test_ac2_a_task_phrasing_ranks_the_right_skill_first(tmp_path, query,
                                                          first):
    """**AC2**, first half, as an agent would actually phrase it. A one-word
    query is the easy case; what the criterion is worth is a sentence, where
    substring scoring answers with whatever skill happens to contain "a" or
    "it". The ranking is over TOKEN SETS (`core/skills.py::search`), so short
    noise words score nothing and the content words decide.
    """
    _, registry, _ = _dry_stack(tmp_path)

    out = registry.call("list_skills", {"query": query})

    assert out["matched"] is True
    assert out["skills"][0]["name"] == first, [
        e["name"] for e in out["skills"][:5]]


def test_ac2_an_oversized_skill_loads_truncated_with_its_sections_intact(
        tmp_path):
    """**AC2**, second half — `load_skill` returns capped content with intact
    sections. Truncation is *structural*: whole `## ` sections in order while
    the running total fits, the rest named in `omitted_sections`, and nothing
    cut mid-line — so what the agent gets is a byte-exact PREFIX of the
    source, never a mangled one.
    """
    service, registry, _ = _dry_stack(tmp_path)
    cap = service.skills.budget.max_skill_chars

    # Five ~7 kB sections against the 24 000-char cap: three fit, two do not.
    body = "The preamble is always kept.\n\n" + "".join(
        f"## Section {i}\n\n" + f"Line {i} of section {i}.\n" * 700
        for i in range(5)
    )
    assert len(body) > cap
    write_skill(_project_skills(service), "big-guide", body=body)
    service.skills.trust(PROJECT, "big-guide")

    out = registry.call("load_skill",
                        {"project": PROJECT, "name": "big-guide"})

    assert out["truncated"] is True
    assert 0 < out["chars"] <= cap
    assert out["chars"] == len(out["content"])

    sections = split_sections(body)
    kept_count = len(split_sections(out["content"])) - 1  # minus the preamble
    assert 1 <= kept_count < len(sections) - 1, kept_count
    # Byte-intact: the payload IS the source's first `kept_count` sections.
    assert out["content"] == "".join(
        text for _, text in sections[:kept_count + 1])
    # …and every heading that did not make it is named, in order.
    assert out["omitted_sections"] == [
        heading for heading, _ in sections[kept_count + 1:]]


# ============================================================ AC3

def test_ac3_a_project_skill_overrides_the_core_one_on_both_surfaces(tmp_path):
    """**AC3** — a project-layer skill overrides a core skill of the same name
    and is labeled project-provenance.

    Two surfaces, one answer: the tool an agent calls and the route the Skills
    modal renders (`frontend/js/skills_model.js::badgeFor` turns `overrides`
    into the "overrides core" badge — its own cases are in
    `tests/test_frontend_skills.py`). `enclosures` is a real shipped core
    skill, so this is a genuine shadow, not a fixture pair.
    """
    service, registry, _ = _dry_stack(tmp_path)
    assert "enclosures" in {e["name"] for e in service.skills.index()}

    write_skill(_project_skills(service), "enclosures",
                description="How WE do enclosures here.",
                body="# House enclosures\n\nOur rules.\n")
    service.skills.trust(PROJECT, "enclosures")

    entries = registry.call("list_skills", {"project": PROJECT})["skills"]
    matching = [e for e in entries if e["name"] == "enclosures"]
    assert len(matching) == 1, "the shadowed core entry must not be listed too"
    entry = matching[0]
    assert entry["layer"] == "project"
    assert entry["overrides"] == "core"
    assert entry["trusted"] is True
    assert entry["description"] == "How WE do enclosures here."

    app = create_app(service, registry, None,
                     extra_allowed_hosts={"testserver"})
    client = TestClient(app, base_url="http://127.0.0.1")
    body = client.get(f"/api/projects/{PROJECT}/skills").json()
    route_entry = next(e for e in body["skills"] if e["name"] == "enclosures")
    assert route_entry == entry

    # And the body an agent (or the modal's preview) gets is the project's.
    loaded = registry.call("load_skill",
                           {"project": PROJECT, "name": "enclosures"})
    assert loaded["layer"] == "project"
    assert "House enclosures" in loaded["content"]
    assert loaded["provenance"]["path"] == "skills/enclosures/SKILL.md"


# ============================================================ AC4

def test_ac4_a_fem_requiring_skill_is_hidden_without_the_capability(
        tmp_path, monkeypatch):
    """**AC4** — a skill declaring `requires: [fem]` is absent from the index
    without the extra.

    `fem-workflow` is a *shipped* core skill, so this grades the house
    capability rule on the real library rather than on a fixture. The gate
    fails closed on both ends: absent → hidden and refused; present → listed
    and loadable.
    """
    _, registry, _ = _dry_stack(tmp_path)

    monkeypatch.setitem(sk.CAPABILITIES, "fem", lambda: False)
    out = registry.call("list_skills", {})
    assert "fem-workflow" not in [e["name"] for e in out["skills"]]
    hidden = next(e for e in out["hidden"] if e["name"] == "fem-workflow")
    assert hidden["requires"] == ["fem"]

    refusal = registry.call("load_skill", {"name": "fem-workflow"})
    assert refusal["error"]["type"] == "validation_error"
    details = refusal["error"]["details"]
    assert details["reason"] == "skill_unavailable"
    assert details["missing"] == ["fem"]

    # With the capability present it is an ordinary skill again.
    monkeypatch.setitem(sk.CAPABILITIES, "fem", lambda: True)
    out = registry.call("list_skills", {})
    assert "fem-workflow" in [e["name"] for e in out["skills"]]
    assert "fem-workflow" not in [e["name"] for e in out["hidden"]]
    loaded = registry.call("load_skill", {"name": "fem-workflow"})
    assert "error" not in loaded and loaded["chars"] > 0


# ============================================================ AC5

@pytest.mark.timeout(1800)
def test_ac5_the_bench_delta_report_prints_the_with_and_without_scores(
        tmp_path, monkeypatch, capsys):
    """**AC5** — "bench delta report runs for one core skill and prints
    with/without scores".

    This RUNS it: the same scripted task twice through the real CLI —
    `--skills none`, then `--skills snap-fits` — then `bench report
    --baseline`, which is the delta report. Offline throughout (the agent
    arrives through `runner.CLIENT_FACTORY`, the module-level test seam), and
    the scripted client is `tests/test_bench_skills.py`'s, so the two files
    grade one behaviour and not two spellings of it.

    It used to `read_text()` that file and grep for `"--skills"`, which passes
    against a commented-out test.
    """
    from agentcad.bench import runner as bench_runner
    from agentcad.bench import tasks as bench_tasks

    from .test_bench_skills import OUT, SEED, _baseline_from, _run, _scripted

    task = bench_tasks.load_task(SEED)
    without: list = []
    with_skill: list = []
    a, b = tmp_path / "without", tmp_path / "with"

    monkeypatch.setattr(bench_runner, "CLIENT_FACTORY",
                        _scripted(without, task))
    assert _run(["bench", "run", "--tasks", SEED, "--skills", "none",
                 "--model", "fake-model", "--report", str(a), "--quiet"]) == 0
    monkeypatch.setattr(bench_runner, "CLIENT_FACTORY",
                        _scripted(with_skill, task))
    assert _run(["bench", "run", "--tasks", SEED, "--skills", "snap-fits",
                 "--model", "fake-model", "--report", str(b), "--quiet"]) == 0

    # The selection is provenance on both runs — "one core skill", recorded.
    assert json.loads((a / OUT / "run.json").read_text())["skills"] == {
        "mode": "none", "names": []}
    assert json.loads((b / OUT / "run.json").read_text())["skills"] == {
        "mode": "only", "names": ["snap-fits"]}

    # The delta report itself: B measured against A as the baseline.
    assert _run(["bench", "report", str(a), "--quiet",
                 "--json-out", str(a / "report.json")]) == 0
    baseline = _baseline_from(json.loads((a / "report.json").read_text()))
    (a / "baseline.json").write_text(json.dumps(baseline), encoding="utf-8")

    capsys.readouterr()
    assert _run(["bench", "report", str(b), "--baseline",
                 str(a / "baseline.json"),
                 "--json-out", str(b / "report.json")]) == 0
    out = capsys.readouterr().out
    assert "baseline ok" in out
    doc = json.loads((b / "report.json").read_text())
    delta = doc["baseline"]["task_deltas"][0]
    assert delta["task"] == SEED
    # Both scores and the difference between them — what AC5 asks be printed.
    assert delta["baseline"] == baseline["tasks"][SEED]
    assert delta["measured"] == doc["tasks"][SEED]["total"]
    assert delta["delta"] == round(delta["measured"] - delta["baseline"], 6)

    # And when the skill run is WORSE than the baseline, that is a red exit
    # with both numbers on stderr — a delta report nobody can ignore.
    strict = dict(baseline, total=baseline["total"] + 0.5,
                  categories={name: value + 0.5
                              for name, value in baseline["categories"].items()})
    (a / "strict.json").write_text(json.dumps(strict), encoding="utf-8")
    capsys.readouterr()
    assert _run(["bench", "report", str(b), "--baseline",
                 str(a / "strict.json")]) == 1
    err = capsys.readouterr().err
    assert "total:" in err and "(-0.5000)" in err


# ============================================================ AC6
# `tests/test_skills_branching.py` — a whole file, because it needs git.


# ============================================================ AC7

def test_ac7_part_template_still_returns_the_contract(tmp_path):
    """**AC7**, first half — `part_template` still returns a valid contract
    payload after the cheat-sheet migration (FR9).

    A compact restatement; the full compatibility surface (the sheet's size
    after the migration, every promoted heading gone from it, every listed
    skill a real core name) is `tests/test_part_template_compat.py`.
    """
    service = make_test_service(tmp_path / "projects", _UnusedKernel())

    payload = service.part_template()

    assert {"template", "cheatsheet", "skills", "hint"} <= set(payload)
    assert "CONTRACT" in payload["cheatsheet"]
    assert "PARAMS" in payload["template"] and "def build(p)" in payload["template"]
    library = {entry["name"] for entry in service.skills.index()}
    assert payload["skills"] and all(
        entry["name"] in library for entry in payload["skills"])
    assert "load_skill" in payload["hint"]


def test_ac7_the_full_suite_count_is_cited():
    """**AC7**, second half — "full suite green" is a claim about a *run*, and
    the evidence is a `make test` count on the record in the newest changelog
    entry (the PRD-004 AC10 / PRD-011 AC8 / PRD-026 AC7 precedent).
    Recomputing the number here would mean running the full suite from inside
    itself, and `--collect-only` counts cases, not what `make test` reports.
    """
    latest = max(CHANGELOG.glob("0[0-9][0-9][0-9]-*.md"))
    text = latest.read_text(encoding="utf-8")
    assert "make test" in text, \
        f"{latest.name} is the newest changelog entry and cites no `make test`"
    assert re.search(r"\b\d{3,6}\s+passed\b", text.replace(",", "")), \
        f"{latest.name} does not cite a `make test` suite count"
