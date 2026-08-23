"""PRD-027 slice 2 — the search engine, `search_parts`, the search route (FR3).

Three layers, one fixture. `tests/fixtures/search_queries.json` holds thirteen
parts and thirty-seven queries; every non-error case runs twice here — once
through the **pure** `matches()`/`rank()` pair (the shape slice 5's
`query_model.js` port has to reproduce byte for byte) and once through the
**Engine** over a real project on disk, where `kind:package` comes off a real
provenance header, `state` comes off `service._status`, and the memos are
doing their job. A case the two layers disagree about is a parity bug, which
is the whole reason the fixture is a file rather than a list of literals.

The rest is the surface: the `search_parts` tool, the member-only route, the
memo invalidation rule (a script edit re-reads **that** script and no other),
and the 1 000-part latency AC — which is also where "search makes zero kernel
calls" is asserted with a counting kernel rather than claimed.
"""

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agentcad.core import search as search_mod
from agentcad.core.model import NotFoundError, ValidationError
from agentcad.core.search import (
    DEFAULT_LIMIT,
    FIELDS,
    GRAMMAR,
    KINDS,
    MAX_LIMIT,
    NO_EVIDENCE_RANK,
    SNIPPET_CHARS,
    STATES,
    Engine,
    Query,
    Term,
    matches,
    parse,
    rank,
    script_only,
)
from agentcad.core.tools import build_registry
from agentcad.server import security as security_module
from agentcad.server.app import create_app

from .conftest import CountingKernel, make_test_service

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "search_queries.json"
FIXTURE = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
PARTS = FIXTURE["parts"]
CASES = FIXTURE["cases"]
MATCH_CASES = [c for c in CASES if not c.get("error")]
ERROR_CASES = [c for c in CASES if c.get("error")]
MATCHED_ON_CASES = [c for c in MATCH_CASES if c.get("expect_matched_on")]


def _case_id(case) -> str:
    return (case["query"] or "<empty>").replace(" ", "_")


def _row(part: dict) -> dict:
    """One fixture part as the engine's row shape (everything but the script)."""
    return {k: v for k, v in part.items() if k != "script"}


def _pure(query: str):
    """The fixture run through the PURE half: `parse` + `matches` + `rank`.

    Returns ``(ids in rank order, {id: matched_on})``. `sorted` is stable, so
    sorting on the rank alone would already keep manifest order — the index is
    in the key anyway, because the tie-break is part of the contract and not an
    implementation detail of CPython's sort.
    """
    parsed = parse(query)
    hits = []
    for index, part in enumerate(PARTS):
        found = matches(_row(part), part["script"], parsed)
        if found is not None:
            hits.append((rank(found), index, part["id"], found))
    hits.sort(key=lambda hit: (hit[0], hit[1]))
    return [hit[2] for hit in hits], {hit[2]: hit[3] for hit in hits}


# --------------------------------------------------------------- the grammar

def test_empty_query_parses_to_no_terms():
    assert parse("") == Query(())
    assert parse("   ") == Query(())


def test_free_text_terms_are_lowercased_and_unfielded():
    assert parse("M5 Boss") == Query((Term(None, "m5", False),
                                      Term(None, "boss", False)))


def test_field_terms_carry_their_field():
    assert parse("tag:Fastener") == Query((Term("tag", "fastener", False),))
    assert parse("id:GEAR_") == Query((Term("id", "gear_", False),))


def test_a_leading_dash_negates():
    assert parse("-tag:draft") == Query((Term("tag", "draft", True),))
    assert parse("-widget") == Query((Term(None, "widget", True),))


def test_quotes_group_a_phrase_and_a_field_value():
    assert parse('"m5 boss"') == Query((Term(None, "m5 boss", False),))
    assert parse('folder:"Left side"') == Query(
        (Term("folder", "Left side", False),))


def test_a_folder_value_keeps_its_case_because_the_match_is_case_folded():
    # Everything else lowercases; folder is compared by `navigation
    # .folder_matches`, which case-folds both sides itself.
    assert parse("folder:Chassis/Left_side").terms[0].value == "Chassis/Left_side"
    assert parse("tag:Fastener").terms[0].value == "fastener"


def test_a_fully_quoted_token_is_free_text_never_a_field():
    assert parse('"tag:fastener"') == Query((Term(None, "tag:fastener", False),))


def test_an_unknown_field_is_refused_and_names_the_fields():
    with pytest.raises(ValidationError) as exc:
        parse("colour:red")
    assert "colour" in exc.value.message
    assert set(exc.value.details["fields"]) == set(FIELDS)


@pytest.mark.parametrize("query", ["tag:", "material:", "folder:", "id:",
                                   'folder:""', "-tag:", "-", '""', '"  "',
                                   "folder:/", "folder://", 'folder:" / "'])
def test_an_empty_value_is_refused(query):
    # navigation.folder_matches treats an empty query as "matches everything",
    # so a `folder:` that reached it would silently widen the result set to the
    # whole project instead of narrowing it.
    with pytest.raises(ValidationError):
        parse(query)


@pytest.mark.parametrize("query", ['"unterminated', 'folder:"left side'])
def test_an_unterminated_quote_is_refused(query):
    with pytest.raises(ValidationError):
        parse(query)


def test_an_unknown_state_or_kind_value_is_refused():
    with pytest.raises(ValidationError) as state:
        parse("state:building")
    assert set(state.value.details["values"]) == set(STATES)
    with pytest.raises(ValidationError) as kind:
        parse("kind:widget")
    assert set(kind.value.details["values"]) == set(KINDS)


def test_every_grammar_refusal_carries_the_grammar():
    """`GRAMMAR` is one constant, quoted into the tool description AND into
    every refusal — so a caller who got the grammar wrong is handed the rules
    rather than a bare "unknown search field" (design §2)."""
    for query in ["colour:red", "state:building", '"unterminated', "tag:", "-"]:
        with pytest.raises(ValidationError) as exc:
            parse(query)
        assert exc.value.details["grammar"] == GRAMMAR, query


def test_the_grammar_constant_documents_every_field_and_value():
    for token in FIELDS + STATES + KINDS:
        assert token in GRAMMAR, token
    assert GRAMMAR.count("\n") == 0  # one paragraph, quotable anywhere


def test_the_rank_table_orders_the_sources_the_spec_names():
    """id/label > tag > material > folder/state/kind > script > no evidence.

    Pinned **directly**, because three of these pairs cannot be observed
    through result ordering at all: every returned row matched every
    non-negated term, so the folder/state/kind rank contributes the same value
    to every row's `min` and can never break a tie against `script`. The
    fixture pins the pairs that ARE observable (`pla`, `steel`, `rotating`);
    this pins the rest, so no pairwise swap in `RANKS` is silent.
    """
    assert rank(["id"]) == rank(["label"]) < rank(["tag"]) < rank(["material"])
    for field in ("folder", "state", "kind"):
        assert rank(["material"]) < rank([field]) < rank(["script"]), field
    assert rank(["script"]) < NO_EVIDENCE_RANK == rank([])
    # A row is ranked by its BEST source, not its worst or its last.
    assert rank(["tag", "script"]) == rank(["tag"])
    assert rank(["state", "script"]) == rank(["state"])


def test_script_only_is_about_content_never_about_field_terms():
    """The snippet rule (`script_only`), which slice 5 ports verbatim."""
    assert script_only(["script"])
    assert script_only(["state", "script"])          # a filter chip is not content
    assert script_only(["folder", "kind", "script"])
    assert not script_only(["tag", "script"])        # the tag already says why
    assert not script_only(["id", "label", "script"])
    assert not script_only(["material", "script"])
    assert not script_only(["state"])                # no script hit at all
    assert not script_only([])


# ------------------------------------------------- the fixture, pure matching

@pytest.mark.parametrize("case", MATCH_CASES, ids=[_case_id(c) for c in MATCH_CASES])
def test_fixture_case_matches_and_ranks(case):
    ids, _ = _pure(case["query"])
    assert ids == case["expect"], case.get("note", "")


@pytest.mark.parametrize("case", MATCHED_ON_CASES,
                         ids=[_case_id(c) for c in MATCHED_ON_CASES])
def test_fixture_case_reports_the_expected_matched_on(case):
    _, found = _pure(case["query"])
    for part_id, sources in case["expect_matched_on"].items():
        assert found[part_id] == sources


@pytest.mark.parametrize("case", ERROR_CASES, ids=[_case_id(c) for c in ERROR_CASES])
def test_fixture_error_case_is_refused(case):
    with pytest.raises(ValidationError):
        parse(case["query"])


def test_the_fixture_covers_the_whole_grammar():
    """A fixture that stops naming a field stops proving the port agrees."""
    queries = " ".join(c["query"] for c in CASES)
    for field in FIELDS:
        assert f"{field}:" in queries, field
    assert "-tag:" in queries and '"' in queries


# ------------------------------------------------------- the engine, on disk

@pytest.fixture(scope="module")
def fixture_project(kernel, tmp_path_factory):
    """The fixture's thirteen parts as a real project (never built).

    `state` is written straight into `service._status` — the states the fixture
    declares are what a build *would* have left there, and building thirteen
    parts to reach them would make this a kernel test.
    """
    service = make_test_service(tmp_path_factory.mktemp("search") / "projects",
                                kernel)
    service.kernel = CountingKernel(service.kernel)
    service.store.create("demo")
    for part in PARTS:
        service.store.add_part(
            "demo", part["id"], part["label"], part["material"],
            part["script"],
            kind="reference" if part["kind"] == "reference" else "script",
            source="imports/frame.step" if part["kind"] == "reference" else None,
        )
        service.store.update_part_meta("demo", part["id"],
                                       folder=part["folder"], tags=part["tags"])
        if part["state"] != "unbuilt":
            service._status[service._status_key("demo", part["id"])] = {
                "state": part["state"]}
    build_registry(service)  # installs service.search
    return service


@pytest.mark.parametrize("case", MATCH_CASES, ids=[_case_id(c) for c in MATCH_CASES])
def test_engine_agrees_with_the_fixture(fixture_project, case):
    result = fixture_project.search.search("demo", case["query"], limit=MAX_LIMIT)
    assert [p["id"] for p in result["parts"]] == case["expect"], case.get("note")
    assert result["total"] == len(case["expect"])
    assert result["query"] == case["query"]


def test_engine_rows_carry_the_declared_metadata(fixture_project):
    rows = {row["id"]: row for row in fixture_project.search.rows("demo")}
    assert len(rows) == len(PARTS)
    for part in PARTS:
        row = rows[part["id"]]
        for key in ("label", "material", "folder", "tags", "kind", "state"):
            assert row[key] == part[key], (part["id"], key)


def test_kind_package_comes_from_the_real_provenance_header(fixture_project):
    # Not from `packages_lock`, which cannot say which *part* came from where.
    script = fixture_project.store.read_script("demo", "m5_screw")
    assert script.startswith("# agentcad:package ")
    assert fixture_project.search.search("demo", "kind:package")["parts"][0]["id"] \
        == "m5_screw"


def test_a_script_only_hit_carries_a_snippet_around_the_first_match(fixture_project):
    result = fixture_project.search.search("demo", "counterbore")
    part = result["parts"][0]
    assert part["matched_on"] == ["script"]
    assert "counterbore" in part["snippet"]
    assert len(part["snippet"]) <= SNIPPET_CHARS


def test_a_filter_does_not_suppress_the_snippet(fixture_project):
    """The query shape the UI sends most: filter chips plus a word.

    `matched_on` carries the filter's source on EVERY returned row, so testing
    for `["script"]` exactly threw the snippet away exactly when the row's
    only content match was in its script — the one case a snippet exists for.
    """
    for result in (
        fixture_project.search.search("demo", "counterbore",
                                      filters={"state": "ok"}),
        fixture_project.search.search("demo", "state:ok counterbore"),
        fixture_project.search.search("demo", "counterbore",
                                      filters={"folder": "Chassis",
                                               "kind": "script"}),
    ):
        part = result["parts"][0]
        assert part["id"] == "base_plate"
        assert "script" in part["matched_on"]
        assert "counterbore" in part["snippet"]


def test_no_snippet_when_the_script_is_not_the_only_match(fixture_project):
    part = fixture_project.search.search("demo", "shaft helix")["parts"][0]
    assert "script" in part["matched_on"]
    assert "snippet" not in part


def test_a_result_row_carries_the_listing_shape(fixture_project):
    part = fixture_project.search.search("demo", "id:gear_a")["parts"][0]
    assert part == {
        "id": "gear_a", "label": "Gear A", "material": "steel_1018",
        "folder": "Drivetrain/Gears", "tags": ["rotating", "gear"],
        "state": "ok", "kind": "script", "matched_on": ["id"],
    }


def test_limit_truncates_the_page_but_not_the_total(fixture_project):
    result = fixture_project.search.search("demo", "", limit=3)
    assert result["total"] == len(PARTS)
    assert [p["id"] for p in result["parts"]] == [p["id"] for p in PARTS[:3]]


def test_limit_defaults_to_fifty_and_none_means_default(fixture_project):
    assert DEFAULT_LIMIT == 50 and MAX_LIMIT == 500
    assert (fixture_project.search.search("demo", "", limit=None)["total"]
            == len(PARTS))


@pytest.mark.parametrize("limit", [0, -1, MAX_LIMIT + 1, "10", 2.5, True])
def test_a_limit_outside_the_range_is_refused(fixture_project, limit):
    with pytest.raises(ValidationError):
        fixture_project.search.search("demo", "", limit=limit)


def test_an_unknown_project_is_a_notfound(fixture_project):
    with pytest.raises(NotFoundError):
        fixture_project.search.search("nope", "")


# ------------------------------------------------------------------ filters

def test_filters_and_with_the_query(fixture_project):
    result = fixture_project.search.search(
        "demo", "tag:printed", filters={"state": "ok"})
    assert [p["id"] for p in result["parts"]] == ["lid", "spool"]


def test_a_tag_filter_list_ands_every_entry(fixture_project):
    result = fixture_project.search.search(
        "demo", "", filters={"tag": ["rotating", "gear"]})
    assert [p["id"] for p in result["parts"]] == ["gear_a"]


@pytest.mark.parametrize("filters,expected", [
    ({"material": "abs"}, ["housing", "lid"]),
    ({"kind": "reference"}, ["scanned_frame"]),
    ({"folder": "a/b"}, ["ab_widget"]),
    ({"tag": "misc"}, ["abc_widget", "ab_widget"]),
])
def test_each_filter_key_narrows_like_its_term(fixture_project, filters, expected):
    result = fixture_project.search.search("demo", "", filters=filters)
    assert [p["id"] for p in result["parts"]] == expected


@pytest.mark.parametrize("filters", [
    {"colour": "red"},          # unknown key
    {"state": "building"},      # unknown value
    {"tag": ""},                # empty value
    {"tag": []},                # nothing to AND
    {"material": 5},            # not a string
    {"tag": ["ok", 5]},         # not a list of strings
    [],                         # not an object
])
def test_a_malformed_filter_is_refused(fixture_project, filters):
    with pytest.raises(ValidationError):
        fixture_project.search.search("demo", "", filters=filters)


# -------------------------------------------------------------------- memos

def test_search_makes_zero_kernel_calls(fixture_project):
    before = fixture_project.kernel.calls
    for case in MATCH_CASES:
        fixture_project.search.search("demo", case["query"], limit=MAX_LIMIT)
    assert fixture_project.kernel.calls == before


def test_the_row_memo_is_reused_until_the_manifest_changes(fixture_project):
    engine = fixture_project.search
    first = engine.rows("demo")
    assert engine.rows("demo") == first
    # The rows are re-derived, but `state` is NOT memoized with them: it lives
    # in `service._status`, which a build changes without touching a manifest
    # byte.
    key = fixture_project._status_key("demo", "lid")
    fixture_project._status[key] = {"state": "error"}
    try:
        assert [r for r in engine.rows("demo") if r["id"] == "lid"][0]["state"] \
            == "error"
    finally:
        fixture_project._status[key] = {"state": "ok"}


@pytest.fixture
def editable(kernel, tmp_path):
    service = make_test_service(tmp_path / "projects", kernel)
    service.store.create("demo")
    for name, token in (("alpha", "aardvark"), ("beta", "bandicoot")):
        service.store.add_part("demo", name, name.title(), "al6061",
                               f"# {token}\nPARAMS = {{}}\n")
    build_registry(service)
    return service


def test_editing_a_script_invalidates_only_that_memo_entry(editable):
    engine = editable.search
    assert [p["id"] for p in engine.search("demo", "aardvark")["parts"]] == ["alpha"]
    assert [p["id"] for p in engine.search("demo", "bandicoot")["parts"]] == ["beta"]
    cached = {path: id(entry[1]) for path, entry in engine._scripts.items()}
    assert len(cached) == 2

    editable.store.write_script("demo", "alpha", "# armadillo\nPARAMS = {}\n")

    assert engine.search("demo", "aardvark")["total"] == 0
    assert [p["id"] for p in engine.search("demo", "armadillo")["parts"]] == ["alpha"]
    after = {path: id(entry[1]) for path, entry in engine._scripts.items()}
    changed = [path for path in cached if cached[path] != after.get(path)]
    assert [Path(path).name for path in changed] == ["alpha.py"]


def test_the_memo_carries_the_lowered_copy_beside_the_raw_one(editable):
    """A free-text query must not re-lower every script on every call.

    The raw copy is what a snippet quotes, the lowered one is what the matcher
    compares against; both are memoized on the same stamp, so a warm query
    lowers nothing.
    """
    engine = editable.search
    editable.store.write_script("demo", "alpha", "# AARDVARK Housing\n")
    assert engine.search("demo", "aardvark")["total"] == 1
    stamp, raw, lowered = engine._scripts[
        str(editable.store.script_path("demo", "alpha"))]
    assert raw == "# AARDVARK Housing\n" and lowered == raw.lower()
    # And the snippet quotes the RAW bytes, not the lowered ones.
    part = engine.search("demo", "aardvark")["parts"][0]
    assert "AARDVARK" in part["snippet"]


def test_a_row_owns_its_tags_and_cannot_corrupt_the_memo(editable):
    """`rows()` hands out a copy of the memoized tag list — a caller that
    sorts or appends to it must not be editing the cache every later search
    reads."""
    editable.store.update_part_meta("demo", "alpha", tags=["zulu", "alpha"])
    engine = editable.search
    first = [row for row in engine.rows("demo") if row["id"] == "alpha"][0]
    first["tags"].sort()
    first["tags"].append("injected")
    again = [row for row in engine.rows("demo") if row["id"] == "alpha"][0]
    assert again["tags"] == ["zulu", "alpha"]
    assert engine.search("demo", "tag:injected")["total"] == 0


def test_both_memos_are_capped(editable, monkeypatch):
    """Neither memo may grow without bound on a long-lived server."""
    assert search_mod._MAX_ROWS_MEMO > 0 and search_mod._MAX_SCRIPT_MEMO > 0
    monkeypatch.setattr(search_mod, "_MAX_ROWS_MEMO", 2)
    monkeypatch.setattr(search_mod, "_MAX_SCRIPT_MEMO", 2)
    engine = editable.search
    for name in ("p_one", "p_two", "p_three", "p_four"):
        editable.store.create(name)
        editable.store.add_part(name, "only", "Only", "al6061", "# body\n")
        engine.search(name, "body")
        assert len(engine._rows) <= 2
        assert len(engine._scripts) <= 2


def test_a_reference_part_without_a_script_reads_as_empty(editable):
    editable.store.add_part("demo", "frame", "Frame", "al6061", "",
                            kind="reference", source="imports/frame.step")
    assert editable.search.script_text("demo", "frame") == ""
    assert editable.search.search("demo", "kind:reference")["parts"][0]["id"] \
        == "frame"


def test_a_folder_move_shows_up_in_the_next_search(editable):
    assert editable.search.search("demo", "folder:Rack")["total"] == 0
    editable.store.update_part_meta("demo", "alpha", folder="Rack/Top")
    result = editable.search.search("demo", "folder:rack")
    assert [p["id"] for p in result["parts"]] == ["alpha"]


# --------------------------------------------------------------------- tool

def test_the_tool_is_registered_and_quotes_the_grammar(editable):
    registry = build_registry(editable)
    tool = registry.get("search_parts")
    assert tool is not None
    assert GRAMMAR in tool.description
    assert tool.input_schema["required"] == ["project", "query"]
    assert tool.input_schema["properties"]["filters"]["type"] == "object"


def test_register_installs_the_engine_on_the_service(editable):
    assert isinstance(editable.search, Engine)


def test_the_tool_answers_the_documented_payload(editable):
    registry = build_registry(editable)
    result = registry.call("search_parts", {"project": "demo", "query": "alpha"})
    assert set(result) == {"query", "total", "parts"}
    assert [p["id"] for p in result["parts"]] == ["alpha"]


def test_the_tool_refuses_a_bad_query_as_a_validation_error(editable):
    registry = build_registry(editable)
    result = registry.call("search_parts",
                           {"project": "demo", "query": "colour:red"})
    assert result["error"]["type"] == "validation_error"


def test_the_tool_requires_a_query(editable):
    registry = build_registry(editable)
    result = registry.call("search_parts", {"project": "demo"})
    assert result["error"]["type"] == "invalid_arguments"


# -------------------------------------------------------------------- route

@pytest.fixture
def http(editable):
    app = create_app(editable, build_registry(editable),
                     extra_allowed_hosts={"testserver"})
    return TestClient(app, base_url="http://127.0.0.1")


def test_the_route_answers_the_same_payload(http):
    response = http.get("/api/projects/demo/search", params={"q": "alpha"})
    assert response.status_code == 200
    assert [p["id"] for p in response.json()["parts"]] == ["alpha"]


def test_the_route_passes_the_limit_through(http):
    body = http.get("/api/projects/demo/search",
                    params={"q": "", "limit": 1}).json()
    assert body["total"] == 2 and len(body["parts"]) == 1


def test_an_absent_q_is_the_empty_query(http):
    assert http.get("/api/projects/demo/search").json()["total"] == 2


def test_a_bad_query_is_a_422_carrying_the_grammar(http):
    response = http.get("/api/projects/demo/search", params={"q": "colour:red"})
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["type"] == "ValidationError"
    assert error["details"]["grammar"] == GRAMMAR


def test_a_limit_outside_the_range_is_a_422(http):
    assert http.get("/api/projects/demo/search",
                    params={"q": "", "limit": 0}).status_code == 422


def test_an_unknown_project_is_a_404(http):
    assert http.get("/api/projects/nope/search").status_code == 404


def test_the_search_route_is_member_only():
    # Default-deny: nothing was added to PUBLIC_PATHS/PUBLIC_PREFIXES, and this
    # asserts it rather than trusting the omission.
    assert not security_module.is_public("/api/projects/demo/search")


# ------------------------------------------------------- the 1 000-part AC

def _synthetic_project(service, name: str, count: int) -> None:
    """`count` parts written straight into the manifest — never built.

    `add_part` re-reads and re-writes the whole manifest per call, which is
    quadratic and would dominate the number this test exists to measure.
    """
    service.store.create(name)
    manifest = service.store.manifest(name)
    parts_dir = service.store.path_of(name) / "parts"
    filler = "# filler line to make this script a realistic size\n" * 34
    for index in range(count):
        part_id = f"part_{index:04d}"
        manifest["parts"].append({
            "id": part_id, "label": f"Part {index}", "material": "al6061",
            "params": {}, "folder": f"Bay {index // 100}", "tags": ["synthetic"],
        })
        (parts_dir / f"{part_id}.py").write_text(
            f"# token_{index:04d}\nPARAMS = {{}}\n\n\ndef build(p):\n"
            f"    return None\n\n{filler}", encoding="utf-8")
    service.store.save_manifest(name, manifest)


def test_a_thousand_part_project_searches_cold_and_warm_within_budget(
        kernel, tmp_path, capsys):
    service = make_test_service(tmp_path / "projects", kernel)
    counter = CountingKernel(service.kernel)
    service.kernel = counter
    _synthetic_project(service, "big", 1000)
    build_registry(service)
    engine = service.search

    start = time.perf_counter()
    cold = engine.search("big", "token_0777")
    cold_s = time.perf_counter() - start
    start = time.perf_counter()
    warm = engine.search("big", "token_0777")
    warm_s = time.perf_counter() - start
    start = time.perf_counter()
    meta = engine.search("big", "tag:synthetic folder:\"Bay 3\"", limit=500)
    meta_s = time.perf_counter() - start

    assert cold["total"] == 1 and cold["parts"][0]["id"] == "part_0777"
    assert cold["parts"][0]["matched_on"] == ["script"]
    assert warm["parts"] == cold["parts"]
    assert meta["total"] == 100
    assert counter.calls == 0
    with capsys.disabled():
        print(f"\n[PRD-027 AC5] 1000 parts: cold {cold_s * 1000:.1f} ms · "
              f"warm {warm_s * 1000:.1f} ms · metadata-only {meta_s * 1000:.1f} ms")
    assert cold_s < 0.5, f"cold search took {cold_s:.3f}s"
    assert warm_s < 0.1, f"warm search took {warm_s:.3f}s"


def test_the_module_never_imports_the_kernel():
    source = Path(search_mod.__file__).read_text(encoding="utf-8")
    assert "OCP" not in source and "build123d" not in source


def test_a_refusal_never_echoes_a_value_that_is_not_json():
    """C2: `{"tag": NaN}` is a filter object a caller can send — the registry's
    type check only sees the object, not what is inside it — and a NaN echoed
    into `details` raised inside Starlette's `allow_nan=False` serializer, i.e.
    an HTTP 500 in place of a 422 refusal."""
    from agentcad.core.search import _with_filters, field_term

    for value in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(ValidationError) as exc:
            field_term("tag", value)
        json.dumps(exc.value.details, allow_nan=False)
        assert exc.value.details["value"] == repr(value)

        with pytest.raises(ValidationError) as exc:
            _with_filters(parse(""), {"folder": [value]})
        json.dumps(exc.value.details, allow_nan=False)

    # ...and the same guard caps an echo that is merely enormous (M16).
    with pytest.raises(ValidationError) as exc:
        parse("x" * 500 + ' "unterminated')
    assert len(exc.value.details["query"]) <= 201
