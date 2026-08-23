"""`core/skills.py` — the format, the layers, search, truncation, trust.

No kernel, no service: the library is pure (a `ProjectStore` for the project
layer and nothing else), so every test here is filesystem-only.
"""

import json
from pathlib import Path

import pytest

from agentcad.core import skills as sk
from agentcad.core.model import NotFoundError, ValidationError
from agentcad.core.project import ProjectStore

SPEC_EXAMPLE = """\
---
name: snap-fits
description: Cantilever and annular snap-fit design — deflection, strain, lengths, ratios, FDM/injection rules.
triggers: [snap, snap-fit, cantilever, clip, latch, lid]
version: 1.0.0
license: Apache-2.0
author: AgentCAD core
requires: []
---
# Snap-fits

Body.
"""


def write_skill(root: Path, name: str, *, description="A test skill.",
                version="1.0.0", triggers=(), requires=(), body="Body.\n",
                flat=False, license_="Apache-2.0", author="AgentCAD core",
                extra_lines=()) -> Path:
    """Write a well-formed skill under `root` and return its SKILL.md path."""
    lines = ["---", f"name: {name}", f"description: {description}",
             f"version: {version}"]
    if triggers:
        lines.append("triggers: [" + ", ".join(triggers) + "]")
    if requires:
        lines.append("requires: [" + ", ".join(requires) + "]")
    if license_:
        lines.append(f"license: {license_}")
    if author:
        lines.append(f"author: {author}")
    lines.extend(extra_lines)
    lines.append("---")
    text = "\n".join(lines) + "\n" + body
    root.mkdir(parents=True, exist_ok=True)
    if flat:
        path = root / f"{name}.md"
        path.write_text(text, encoding="utf-8")
        return path
    path = root / name / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def core_dir(tmp_path) -> Path:
    d = tmp_path / "core-skills"
    d.mkdir()
    return d


@pytest.fixture
def store(tmp_path) -> ProjectStore:
    s = ProjectStore(tmp_path / "projects")
    s.create("proj")
    return s


def project_skills(store: ProjectStore, proj: str = "proj") -> Path:
    d = store.path_of(proj) / "skills"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ------------------------------------------------------------- frontmatter

def test_spec_example_round_trips():
    meta, body = sk.parse_frontmatter(SPEC_EXAMPLE)
    assert meta["name"] == "snap-fits"
    assert meta["version"] == "1.0.0"
    assert meta["triggers"] == ["snap", "snap-fit", "cantilever", "clip",
                                "latch", "lid"]
    assert meta["requires"] == []
    assert body.startswith("# Snap-fits")


def test_nothing_is_coerced():
    meta, _ = sk.parse_frontmatter(
        "---\nversion: 1.0\nsafe: no\nn: 010\n---\nbody\n")
    assert meta == {"version": "1.0", "safe": "no", "n": "010"}


def test_block_lists_and_quoted_scalars():
    meta, body = sk.parse_frontmatter(
        "---\n"
        "triggers:\n  - a\n  - 'b: two'\n"
        'description: "a colon: inside"\n'
        "# a comment\n"
        "\n"
        "---\nthe body\n")
    assert meta["triggers"] == ["a", "b: two"]
    assert meta["description"] == "a colon: inside"
    assert body == "the body\n"


def test_inline_list_may_be_empty_or_quoted():
    meta, _ = sk.parse_frontmatter(
        "---\nrequires: []\ntriggers: ['a b', \"c\"]\n---\n")
    assert meta["requires"] == []
    assert meta["triggers"] == ["a b", "c"]


@pytest.mark.parametrize("text", [
    "no frontmatter at all\n",
    "---\nname: a\nbody with no close\n",
    "---\nname: a\nname: b\n---\n",
    "---\nName: a\n---\n",
    "---\nnot a key line\n---\n",
    "---\n- orphan item\n---\n",
])
def test_malformed_frontmatter_raises(text):
    with pytest.raises(sk.SkillFormatError):
        sk.parse_frontmatter(text)


def test_unknown_key_is_kept_in_extra(core_dir):
    write_skill(core_dir, "alpha", extra_lines=["maturity: draft"])
    rec = sk.SkillLibrary(core_dir=core_dir).records()["alpha"]
    assert rec.invalid is None
    assert dict(rec.meta.extra)["maturity"] == "draft"


# ------------------------------------------------------------------ layers

def test_layering_project_shadows_core(core_dir, store):
    write_skill(core_dir, "alpha")
    write_skill(core_dir, "beta")
    proj = project_skills(store)
    write_skill(proj, "beta")
    write_skill(proj, "gamma", flat=True)

    lib = sk.SkillLibrary(store, core_dir=core_dir)
    entries = lib.index("proj")
    assert [e["name"] for e in entries] == ["alpha", "beta", "gamma"]
    by_name = {e["name"]: e for e in entries}
    assert by_name["alpha"]["layer"] == "core"
    assert by_name["beta"]["layer"] == "project"
    assert by_name["beta"]["overrides"] == "core"
    assert by_name["alpha"]["overrides"] is None
    assert lib.records("proj")["gamma"].dir is None
    # Without a project only the core layer is visible.
    assert [e["name"] for e in lib.index()] == ["alpha", "beta"]


def test_directory_form_wins_over_the_flat_form(core_dir):
    write_skill(core_dir, "delta", description="the directory")
    write_skill(core_dir, "delta", description="the flat file", flat=True)
    rec = sk.SkillLibrary(core_dir=core_dir).records()["delta"]
    assert rec.meta.description == "the directory"
    assert rec.dir is not None


def test_readme_and_strays_are_not_skills(core_dir):
    write_skill(core_dir, "alpha")
    (core_dir / "README.md").write_text("# not a skill\n", encoding="utf-8")
    (core_dir / "notes.txt").write_text("hi\n", encoding="utf-8")
    (core_dir / "no-frontmatter.md").write_text("# nope\n", encoding="utf-8")
    (core_dir / "Bad_Name").mkdir()
    assert list(sk.SkillLibrary(core_dir=core_dir).records()) == ["alpha"]


def test_only_restricts_the_index(core_dir):
    write_skill(core_dir, "alpha")
    write_skill(core_dir, "beta")
    lib = sk.SkillLibrary(core_dir=core_dir, only=frozenset({"alpha"}))
    assert [e["name"] for e in lib.index()] == ["alpha"]
    with pytest.raises(NotFoundError):
        lib.resolve("beta")


def test_missing_layers_are_empty_not_an_error(tmp_path, store):
    lib = sk.SkillLibrary(store, core_dir=tmp_path / "nope")
    assert lib.index("proj") == []
    assert lib.compact_index("proj") == ""


# ----------------------------------------------------------- capabilities

def test_capability_gate_hides_and_refuses(core_dir):
    write_skill(core_dir, "fem-workflow", requires=["fem"])
    write_skill(core_dir, "typo-skill", requires=["nope"])
    write_skill(core_dir, "plain")
    lib = sk.SkillLibrary(core_dir=core_dir, capabilities=lambda: frozenset())
    assert [e["name"] for e in lib.index()] == ["plain"]
    hidden = {h["name"]: h for h in lib.hidden()}
    assert set(hidden) == {"fem-workflow", "typo-skill"}
    assert hidden["fem-workflow"]["reason"] == "capability"
    for name in ("fem-workflow", "typo-skill"):
        with pytest.raises(ValidationError) as exc:
            lib.resolve(name)
        assert exc.value.details["reason"] == "skill_unavailable"


def test_present_capability_is_visible(core_dir):
    write_skill(core_dir, "fem-workflow", requires=["fem"])
    lib = sk.SkillLibrary(core_dir=core_dir,
                          capabilities=lambda: frozenset({"fem"}))
    assert [e["name"] for e in lib.index()] == ["fem-workflow"]
    assert lib.hidden() == []


def test_available_capabilities_is_the_closed_set():
    caps = sk.available_capabilities()
    assert caps <= set(sk.CAPABILITIES)
    assert {"specs", "sketch", "holes", "sheetmetal"} <= caps


# ---------------------------------------------------------------- search

def test_search_ranks_by_name_then_trigger_then_description(core_dir):
    write_skill(core_dir, "sheet-metal", description="Bends and flanges.",
                triggers=["bend", "flange"])
    write_skill(core_dir, "enclosures",
                description="Boxes made from sheet or print.")
    lib = sk.SkillLibrary(core_dir=core_dir)
    entries, matched = lib.search("sheet")
    assert matched is True
    assert [e["name"] for e in entries] == ["sheet-metal", "enclosures"]
    assert lib.search("sheet")[0] == entries  # deterministic


def test_exact_name_beats_a_trigger(core_dir):
    write_skill(core_dir, "holes", description="ISO holes.")
    write_skill(core_dir, "aaa-first", description="Nothing.",
                triggers=["holes"])
    entries, matched = sk.SkillLibrary(core_dir=core_dir).search("holes")
    assert matched is True
    assert [e["name"] for e in entries] == ["holes", "aaa-first"]


def test_search_without_a_hit_returns_the_full_index(core_dir):
    write_skill(core_dir, "alpha")
    write_skill(core_dir, "beta")
    entries, matched = sk.SkillLibrary(core_dir=core_dir).search("zzz")
    assert matched is False
    assert [e["name"] for e in entries] == ["alpha", "beta"]


def test_project_layer_breaks_a_score_tie(core_dir, store):
    write_skill(core_dir, "aaa", description="latch design", triggers=["clip"])
    write_skill(project_skills(store), "zzz", description="latch design",
                triggers=["clip"])
    lib = sk.SkillLibrary(store, core_dir=core_dir)
    entries, _ = lib.search("clip", "proj")
    assert [e["name"] for e in entries] == ["zzz", "aaa"]


# ------------------------------------------------------------ truncation

def build_sectioned_body(n=6, chars=2000) -> str:
    parts = ["Preamble line.\n\n"]
    for i in range(n):
        parts.append(f"## Section {i}\n\n" + ("x" * chars) + "\n\n")
    return "".join(parts)


def test_split_sections_ignores_headings_inside_a_fence():
    body = ("intro\n\n```python\n## not a heading\n```\n\n## Real\n\ntext\n")
    sections = sk.split_sections(body)
    assert [h for h, _ in sections] == ["", "Real"]
    assert "".join(t for _, t in sections) == body


def test_truncate_keeps_whole_sections_in_order():
    body = build_sectioned_body()
    text, truncated, omitted = sk.truncate_sections(body, 5000)
    assert truncated is True
    assert text.startswith("Preamble line.")
    assert "## Section 0" in text and "## Section 1" in text
    assert "## Section 2" not in text
    assert omitted == [f"Section {i}" for i in range(2, 6)]
    assert body.startswith(text)


def test_truncate_keeps_the_preamble_and_names_an_oversized_section():
    body = "Preamble.\n\n## Big\n\n" + "y" * 9000 + "\n"
    text, truncated, omitted = sk.truncate_sections(body, 100)
    assert text == "Preamble.\n\n"
    assert truncated is True
    assert omitted == ["Big"]


def test_truncate_leaves_a_short_body_alone():
    body = "Preamble.\n\n## One\n\ntext\n"
    assert sk.truncate_sections(body, 5000) == (body, False, [])


# ---------------------------------------------------------------- loading

def test_load_returns_content_and_provenance(core_dir, store):
    write_skill(core_dir, "alpha", body="Preamble.\n\n## One\n\ntext\n")
    lib = sk.SkillLibrary(store, core_dir=core_dir)
    payload = lib.load("alpha")
    assert payload["name"] == "alpha"
    assert payload["layer"] == "core"
    assert payload["version"] == "1.0.0"
    assert payload["content"].startswith("Preamble.")
    assert payload["chars"] == len(payload["content"])
    assert payload["truncated"] is False
    assert payload["omitted_sections"] == []
    assert payload["assets"] == []
    assert payload["provenance"]["layer"] == "core"
    assert payload["provenance"]["license"] == "Apache-2.0"
    assert payload["provenance"]["path"] is None
    assert payload["provenance"]["digest"] == lib.records()["alpha"].digest


def test_load_truncates_at_the_budget(core_dir):
    write_skill(core_dir, "alpha", body=build_sectioned_body())
    lib = sk.SkillLibrary(core_dir=core_dir,
                          budget=sk.SkillBudget(max_skill_chars=5000))
    payload = lib.load("alpha")
    assert payload["truncated"] is True
    assert payload["omitted_sections"] == [f"Section {i}" for i in range(2, 6)]


def test_load_lists_and_returns_assets(core_dir):
    write_skill(core_dir, "alpha")
    snip = core_dir / "alpha" / "snippets" / "x.py"
    snip.parent.mkdir()
    snip.write_text("PARAMS = {}\n", encoding="utf-8")
    lib = sk.SkillLibrary(core_dir=core_dir)
    assert lib.load("alpha")["assets"] == [
        {"path": "snippets/x.py", "bytes": snip.stat().st_size}]
    assert lib.load("alpha", asset="snippets/x.py")["content"] == "PARAMS = {}\n"


@pytest.mark.parametrize("asset", ["../SKILL.md", "/etc/passwd",
                                   "snippets/../../SKILL.md", ""])
def test_asset_traversal_is_refused(core_dir, asset):
    write_skill(core_dir, "alpha")
    with pytest.raises(ValidationError):
        sk.SkillLibrary(core_dir=core_dir).load("alpha", asset=asset)


def test_asset_symlink_out_of_the_dir_is_refused(core_dir, tmp_path):
    write_skill(core_dir, "alpha")
    secret = tmp_path / "secret.txt"
    secret.write_text("s3cret\n", encoding="utf-8")
    link = core_dir / "alpha" / "escape.py"
    link.symlink_to(secret)
    lib = sk.SkillLibrary(core_dir=core_dir)
    with pytest.raises((ValidationError, NotFoundError)):
        lib.load("alpha", asset="escape.py")
    assert [a["path"] for a in lib.load("alpha")["assets"]] == []


def test_missing_asset_is_not_found(core_dir):
    write_skill(core_dir, "alpha")
    with pytest.raises(NotFoundError):
        sk.SkillLibrary(core_dir=core_dir).load("alpha", asset="snippets/x.py")


def test_unknown_skill_is_not_found(core_dir):
    lib = sk.SkillLibrary(core_dir=core_dir)
    with pytest.raises(NotFoundError) as exc:
        lib.resolve("nope")
    assert exc.value.details["reason"] == "skill_not_found"
    with pytest.raises(NotFoundError):
        lib.resolve("NOT A NAME")


# ------------------------------------------------------- broken skill files

def test_invalid_project_skill_is_listed_and_refused(core_dir, store):
    proj = project_skills(store)
    (proj / "broken").mkdir()
    (proj / "broken" / "SKILL.md").write_text("no frontmatter\n",
                                              encoding="utf-8")
    lib = sk.SkillLibrary(store, core_dir=core_dir)
    entry = lib.index("proj")[0]
    assert entry["name"] == "broken"
    assert entry["invalid"]
    with pytest.raises(ValidationError) as exc:
        lib.resolve("broken", "proj")
    assert exc.value.details["reason"] == "skill_invalid"


def test_an_invalid_project_skill_still_shadows_core(core_dir, store):
    write_skill(core_dir, "alpha", description="the core one")
    proj = project_skills(store)
    (proj / "alpha").mkdir()
    (proj / "alpha" / "SKILL.md").write_text("---\nname: b\n---\n",
                                         encoding="utf-8")
    entries = sk.SkillLibrary(store, core_dir=core_dir).index("proj")
    assert [e["name"] for e in entries] == ["alpha"]
    assert entries[0]["layer"] == "project"
    assert entries[0]["overrides"] == "core"
    assert entries[0]["invalid"]


@pytest.mark.parametrize("payload", [
    b"\xff\xfe not utf-8 at all\n",
    b"x" * (sk.MAX_SKILL_FILE_BYTES + 1),
])
def test_hostile_bytes_become_an_invalid_record(core_dir, payload):
    (core_dir / "alpha").mkdir()
    (core_dir / "alpha" / "SKILL.md").write_bytes(payload)
    rec = sk.SkillLibrary(core_dir=core_dir).records()["alpha"]
    assert rec.invalid


def test_crlf_and_bom_are_tolerated(core_dir):
    body = "---\r\nname: alpha\r\ndescription: d\r\nversion: 1.0.0\r\n---\r\nbody\r\n"
    (core_dir / "alpha").mkdir()
    (core_dir / "alpha" / "SKILL.md").write_bytes(
        b"\xef\xbb\xbf" + body.encode("utf-8"))
    rec = sk.SkillLibrary(core_dir=core_dir).records()["alpha"]
    assert rec.invalid is None
    assert rec.meta.description == "d"
    assert "\r" not in rec.body


# ----------------------------------------------------------------- trust

def test_project_skills_need_trust(core_dir, store):
    write_skill(project_skills(store), "pskill", body="first body\n")
    lib = sk.SkillLibrary(store, core_dir=core_dir)
    assert lib.index("proj")[0]["trusted"] is False
    with pytest.raises(ValidationError) as exc:
        lib.load("pskill", "proj")
    assert exc.value.details["reason"] == "skill_untrusted"

    lib.trust("proj", "pskill")
    assert lib.index("proj")[0]["trusted"] is True
    assert lib.load("pskill", "proj")["content"].startswith("first body")

    # Editing the file revokes trust: it is keyed by content digest.
    write_skill(project_skills(store), "pskill",
                body="a rewritten body, materially different\n")
    assert lib.index("proj")[0]["trusted"] is False
    with pytest.raises(ValidationError):
        lib.load("pskill", "proj")

    lib.trust("proj", "pskill")
    lib.untrust("proj", "pskill")
    assert lib.index("proj")[0]["trusted"] is False


def test_core_skills_are_trusted_by_construction(core_dir, store):
    write_skill(core_dir, "alpha")
    lib = sk.SkillLibrary(store, core_dir=core_dir)
    assert lib.index("proj")[0]["trusted"] is True
    assert lib.load("alpha", "proj")["content"]


def test_trust_state_lives_in_the_history_dir(core_dir, store):
    write_skill(project_skills(store), "pskill")
    lib = sk.SkillLibrary(store, core_dir=core_dir)
    lib.trust("proj", "pskill")
    path = (store.canonical_path_of("proj") / ".history" / "agentcad"
            / "skills" / "trust.json")
    assert path.is_file()
    state = json.loads(path.read_text(encoding="utf-8"))
    assert state["version"] == 1
    assert set(state["trusted"]) == {"pskill"}


def test_disabled_skills_are_hidden_and_refused(core_dir, store):
    write_skill(core_dir, "alpha")
    lib = sk.SkillLibrary(store, core_dir=core_dir)
    lib.set_enabled("proj", "alpha", False)
    assert lib.index("proj") == []
    assert lib.hidden("proj") == [{"name": "alpha", "layer": "core",
                                   "requires": [], "reason": "disabled"}]
    with pytest.raises(ValidationError) as exc:
        lib.resolve("alpha", "proj")
    assert exc.value.details["reason"] == "skill_disabled"
    lib.set_enabled("proj", "alpha", True)
    assert [e["name"] for e in lib.index("proj")] == ["alpha"]


def test_a_corrupt_trust_file_reads_as_empty(core_dir, store):
    write_skill(project_skills(store), "pskill")
    lib = sk.SkillLibrary(store, core_dir=core_dir)
    lib.trust("proj", "pskill")
    path = (store.canonical_path_of("proj") / ".history" / "agentcad"
            / "skills" / "trust.json")
    path.write_text("{not json", encoding="utf-8")
    assert lib.trust_state("proj") == {"version": 1, "trusted": {},
                                       "disabled": []}
    assert lib.index("proj")[0]["trusted"] is False
    # …and the next approval rebuilds it.
    lib.trust("proj", "pskill")
    assert lib.index("proj")[0]["trusted"] is True


def test_trust_of_an_unknown_skill_is_not_found(core_dir, store):
    lib = sk.SkillLibrary(store, core_dir=core_dir)
    with pytest.raises(NotFoundError):
        lib.trust("proj", "nope")


# --------------------------------------------------------- compact index

def test_compact_index_lines_and_overflow(core_dir):
    for i in range(45):
        write_skill(core_dir, f"s{i:02d}", description="d" * 200)
    lib = sk.SkillLibrary(core_dir=core_dir)
    lines = lib.compact_index(limit=40).splitlines()
    assert len(lines) == 41
    assert lines[0] == "- s00 — " + "d" * 120
    assert lines[-1] == "…and 5 more: call list_skills {query}"
    assert lib.compact_index(limit=100).count("\n") == 44


# ------------------------------------------------------------ the budget

def test_budget_from_config(monkeypatch, tmp_path):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"skills": {"max_loaded": 7}}), encoding="utf-8")
    monkeypatch.setenv("AGENTCAD_CONFIG", str(cfg))
    monkeypatch.delenv("AGENTCAD_SKILLS_MAX_LOADED", raising=False)
    assert sk.SkillBudget.from_config().max_loaded == 7
    monkeypatch.setenv("AGENTCAD_SKILLS_MAX_LOADED", "2")
    assert sk.SkillBudget.from_config().max_loaded == 2


def test_service_exposes_the_library(tmp_path, kernel):
    from tests.conftest import make_test_service

    service = make_test_service(tmp_path / "projects", kernel)
    service.create_project("p")
    assert isinstance(service.skills, sk.SkillLibrary)
    assert service.skills.index("p") == service.skills.index()


def test_an_empty_frontmatter_value_is_a_string_not_a_list():
    meta, _ = sk.parse_frontmatter(
        "---\nnotes:\ntriggers:\n  - one\nauthor:\n---\nbody\n")
    assert meta["notes"] == ""
    assert meta["author"] == ""
    assert meta["triggers"] == ["one"]


def test_an_empty_triggers_key_is_no_triggers(core_dir):
    (core_dir / "bare-skill").mkdir()
    (core_dir / "bare-skill" / "SKILL.md").write_text(
        "---\nname: bare-skill\ndescription: d\nversion: 1.0.0\n"
        "triggers:\nrequires:\n---\nbody\n", encoding="utf-8")
    rec = sk.SkillLibrary(core_dir=core_dir).records()["bare-skill"]
    assert rec.invalid is None
    assert rec.meta.triggers == () and rec.meta.requires == ()


def test_a_symlink_loop_does_not_hang_the_asset_walk(core_dir):
    write_skill(core_dir, "loop-skill")
    (core_dir / "loop-skill" / "back").symlink_to(core_dir / "loop-skill",
                                                  target_is_directory=True)
    assert sk.SkillLibrary(core_dir=core_dir).load("loop-skill")["assets"] == []


# ------------------------------------------------- the cap is a hard bound

def test_an_over_long_preamble_is_cut_to_the_cap(core_dir):
    """A heading-less body must not be a way around the budget."""
    body = "line of preamble\n" * 6000        # ~100 000 chars, no headings
    assert len(body) > 100_000
    text, truncated, omitted = sk.truncate_sections(body, 5000)
    assert len(text) <= 5000 + len("\n```")
    assert truncated is True
    assert omitted == ["(preamble cut)"]
    assert body.startswith(text)               # a prefix, cut at a line end
    assert text.endswith("\n")

    write_skill(core_dir, "huge-skill", body=body)
    payload = sk.SkillLibrary(core_dir=core_dir,
                              budget=sk.SkillBudget(max_skill_chars=5000)
                              ).load("huge-skill")
    assert payload["truncated"] is True
    assert payload["chars"] <= 5000 + len("\n```")
    assert payload["omitted_sections"][0] == "(preamble cut)"


def test_an_over_long_preamble_still_names_every_heading(core_dir):
    body = "x" * 9000 + "\n\n## One\n\ntext\n\n## Two\n\ntext\n"
    text, truncated, omitted = sk.truncate_sections(body, 100)
    assert truncated is True
    assert omitted == ["(preamble cut)", "One", "Two"]
    assert len(text) <= 100 + sk.PREAMBLE_CUT_SLACK


def test_a_cut_inside_a_fence_closes_it(core_dir):
    body = "intro\n\n```python\n" + "value = 1\n" * 500 + "```\n"
    text, truncated, _ = sk.truncate_sections(body, 500)
    assert truncated is True
    assert text.endswith("```")
    assert text.count("```") % 2 == 0          # the block is terminated
    assert len(text) <= 500 + sk.PREAMBLE_CUT_SLACK


@pytest.mark.parametrize("body", [
    "x" * 50_000,                               # not one line boundary
    "\n" * 50_000,                              # nothing but line boundaries
    "```python\n" + "y = 2\n" * 5_000,          # one unterminated fence
    "~~~~\n" + "z\n" * 5_000,                   # a four-char tilde marker
    "````\n" + "z\n" * 5_000,                   # a four-char backtick marker
    "## Heading\n\n" + "w" * 50_000,            # over-long FIRST section
    "p\n\n## A\n\n" + "q" * 50_000 + "\n\n## B\n\nshort\n",
    "",
])
def test_load_never_exceeds_the_budget(core_dir, body):
    budget = sk.SkillBudget(max_skill_chars=1000)
    write_skill(core_dir, "bounded-skill", body=body)
    payload = sk.SkillLibrary(core_dir=core_dir, budget=budget
                              ).load("bounded-skill")
    assert payload["chars"] == len(payload["content"])
    assert payload["chars"] <= budget.max_skill_chars + 4
