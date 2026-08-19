"""`bench publish` -- the leaderboard page and the full-disclosure rule (AC7).

Offline, kernel-free and parallel-safe: every board is built by hand under
`tmp_path`. The one thing read out of the repo is the checked-in
`benchmarks/leaderboard/` layout, and it is read **never written** -- the same
discipline the other bench suites keep.
"""
from pathlib import Path

import pytest

from agentcad.bench import publish as bench_publish
from agentcad.bench._json import write_json

REPO = Path(__file__).resolve().parents[1]

TASKS = ["model_from_drawing/a", "modify_to_spec/b"]


def _report(total=0.6):
    return {"schema": 1, "task_set": "bench-v1", "harness": 1,
            "agentcad": "0.1.0", "agent": "builtin", "model": "m",
            "n": 2, "total": total,
            "categories": {"model_from_drawing": {"total": total, "n": 1,
                                                  "missing": 0},
                           "modify_to_spec": {"total": total, "n": 1,
                                              "missing": 0}},
            "tasks": {t: {"total": total, "over_budget": False,
                          "missing": False, "subscores": {}} for t in TASKS},
            "warnings": []}


def _row(row_id, **over):
    row = {"schema": 1, "id": row_id, "agent": "AgentCAD built-in chat agent",
           "harness_command": "agentcad bench run --set core",
           "model": "claude-sonnet-5", "agentcad": "0.1.0", "harness": 1,
           "task_set": "bench-v1", "date": "2026-08-19",
           "config": {"kernel_pool_size": 1},
           "submission": "https://example.invalid/s.tar.gz",
           "transcript": "https://example.invalid/t.tar.gz", "notes": ""}
    row.update(over)
    return row


def _board(tmp_path, rows):
    for row in rows:
        # `_total` is fixture scaffolding, not part of the row document: pop it
        # *before* the write so `row.json` on disk is exactly what a submitter
        # would hand in.
        total = row.pop("_total", 0.6)
        out = tmp_path / "rows" / row["id"]
        write_json(out / "row.json", row)
        write_json(out / "report.json", _report(total))
    return tmp_path


# --------------------------------------------------------------- the page

def test_three_rows_render_a_self_contained_page(tmp_path):
    board = _board(tmp_path, [_row("builtin"), _row("claude-mcp"), _row("kcl")])
    out = tmp_path / "index.html"
    result = bench_publish.publish(board, out, title="AgentCAD-Bench",
                                   expected_tasks=TASKS)
    html = out.read_text()
    assert result["rows"] == 3
    lower = html.lower()
    # Self-contained: nothing here can make the browser fetch anything. The
    # only absolute URLs on the page are the rows' own evidence links.
    assert "<script" not in lower and "<link" not in lower
    assert "@import" not in lower and "url(" not in lower
    assert "http://" not in lower and "cdn" not in lower
    assert "https://fonts." not in lower
    # 3 rows x 2 links, each rendered as an anchor (href + text).
    assert lower.count("https://") == 12
    assert html.count("<tr") >= 4                      # header + 3 rows
    for row_id in ("builtin", "claude-mcp", "kcl"):
        assert row_id in html


def test_the_page_says_what_is_measured_and_what_is_not(tmp_path):
    board = _board(tmp_path, [_row("builtin")])
    out = tmp_path / "index.html"
    bench_publish.publish(board, out, title="T", expected_tasks=TASKS)
    html = out.read_text().lower()
    for claim in ("interference", "iou", "specs", "metric window"):
        assert claim in html
    assert "no llm" in html and "no human panel" in html
    assert "agentcad bench score" in html


def test_republishing_the_same_input_is_byte_identical(tmp_path):
    board = _board(tmp_path, [_row("builtin")])
    a, b = tmp_path / "a.html", tmp_path / "b.html"
    bench_publish.publish(board, a, title="T", expected_tasks=TASKS)
    bench_publish.publish(board, b, title="T", expected_tasks=TASKS)
    assert a.read_bytes() == b.read_bytes()


def test_rows_are_ordered_by_total_then_id(tmp_path):
    board = _board(tmp_path, [_row("zzz", _total=0.9), _row("aaa", _total=0.9),
                              _row("mmm", _total=0.5)])
    out = tmp_path / "index.html"
    bench_publish.publish(board, out, title="T", expected_tasks=TASKS)
    html = out.read_text()
    assert html.index("aaa") < html.index("zzz") < html.index("mmm")


def test_row_text_is_html_escaped(tmp_path):
    hostile = '<img src=x onerror="alert(1)">'
    board = _board(tmp_path, [_row("builtin", agent=hostile,
                                   notes="tom & jerry")])
    out = tmp_path / "index.html"
    bench_publish.publish(board, out, title="T & <b>", expected_tasks=TASKS)
    html = out.read_text()
    # The text survives verbatim; every character that could close a tag or
    # open an attribute is escaped, so nothing of it is markup.
    assert "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;" in html
    assert "<img" not in html and 'onerror="' not in html
    assert "tom &amp; jerry" in html
    assert "<b>" not in html and "T &amp; &lt;b&gt;" in html


# ------------------------------------------------- the five fail-closed rules

@pytest.mark.parametrize("mutate, needle", [
    (lambda r: r.pop("submission"), "submission"),
    (lambda r: r.__setitem__("transcript", ""), "transcript"),
    (lambda r: r.pop("config"), "config"),
    (lambda r: r.__setitem__("model", ""), "model"),
    (lambda r: r.__setitem__("task_set", "bench-v0"), "task_set"),
    (lambda r: r.__setitem__("harness", 99), "harness"),
])
def test_each_disclosure_rule_rejects_and_names_itself(tmp_path, mutate, needle):
    row = _row("bad")
    mutate(row)
    board = _board(tmp_path, [row])
    with pytest.raises(Exception) as exc:
        bench_publish.publish(board, tmp_path / "out.html", title="T",
                              expected_tasks=TASKS)
    assert needle in str(exc.value)
    assert not (tmp_path / "out.html").exists()        # nothing partial is written


def test_a_partial_run_is_not_a_row(tmp_path):
    board = _board(tmp_path, [_row("builtin")])
    partial = _report()
    partial["tasks"].pop("modify_to_spec/b")
    write_json(board / "rows" / "builtin" / "report.json", partial)
    with pytest.raises(Exception) as exc:
        bench_publish.publish(board, tmp_path / "out.html", title="T",
                              expected_tasks=TASKS)
    assert "modify_to_spec/b" in str(exc.value)


def test_a_task_flagged_missing_is_a_partial_run_too(tmp_path):
    board = _board(tmp_path, [_row("builtin")])
    partial = _report()
    partial["tasks"]["modify_to_spec/b"] = {"total": 0.0, "over_budget": False,
                                            "missing": True, "subscores": {}}
    write_json(board / "rows" / "builtin" / "report.json", partial)
    with pytest.raises(Exception) as exc:
        bench_publish.publish(board, tmp_path / "out.html", title="T",
                              expected_tasks=TASKS)
    assert "modify_to_spec/b" in str(exc.value)


def test_a_report_of_the_wrong_schema_is_rejected(tmp_path):
    board = _board(tmp_path, [_row("builtin")])
    doc = _report()
    doc["schema"] = 99
    write_json(board / "rows" / "builtin" / "report.json", doc)
    with pytest.raises(Exception) as exc:
        bench_publish.publish(board, tmp_path / "out.html", title="T",
                              expected_tasks=TASKS)
    assert "schema" in str(exc.value)
    assert not (tmp_path / "out.html").exists()


def test_a_row_with_no_report_is_rejected(tmp_path):
    board = _board(tmp_path, [_row("builtin")])
    (board / "rows" / "builtin" / "report.json").unlink()
    with pytest.raises(Exception) as exc:
        bench_publish.publish(board, tmp_path / "out.html", title="T",
                              expected_tasks=TASKS)
    assert "report.json" in str(exc.value)


def test_a_relative_link_must_exist_and_stay_inside_the_row(tmp_path):
    board = _board(tmp_path, [_row("builtin", submission="s.tar.gz")])
    with pytest.raises(Exception) as exc:
        bench_publish.publish(board, tmp_path / "out.html", title="T",
                              expected_tasks=TASKS)
    assert "submission" in str(exc.value)

    (board / "rows" / "builtin" / "s.tar.gz").write_bytes(b"x")
    result = bench_publish.publish(board, tmp_path / "out.html", title="T",
                                   expected_tasks=TASKS)
    assert result["rows"] == 1


@pytest.mark.parametrize("value", ["../escape.tar.gz", "/etc/passwd",
                                   "http://example.invalid/s.tar.gz",
                                   "javascript:alert(1)"])
def test_a_link_that_is_neither_https_nor_a_contained_path_is_refused(
        tmp_path, value):
    board = _board(tmp_path, [_row("builtin", submission=value)])
    with pytest.raises(Exception) as exc:
        bench_publish.publish(board, tmp_path / "out.html", title="T",
                              expected_tasks=TASKS)
    assert "submission" in str(exc.value)


def test_an_id_that_disagrees_with_its_directory_is_rejected(tmp_path):
    """The directory is the identity; a document may not carry a second one."""
    board = _board(tmp_path, [_row("builtin")])
    row = _row("builtin", id="somebody-elses-run")
    write_json(board / "rows" / "builtin" / "row.json", row)
    with pytest.raises(Exception) as exc:
        bench_publish.publish(board, tmp_path / "out.html", title="T",
                              expected_tasks=TASKS)
    assert "somebody-elses-run" in str(exc.value) and "builtin" in str(exc.value)
    assert not (tmp_path / "out.html").exists()


def test_an_unreadable_row_document_is_a_problem_not_a_raise(tmp_path):
    """Exit 1 (nothing disclosed), never exit 2 (the caller named a bad input)."""
    board = _board(tmp_path, [_row("builtin")])
    (board / "rows" / "builtin" / "row.json").write_text("{not json")
    rows, problems = bench_publish.load_rows(board, TASKS)
    assert rows == []
    assert any("builtin" in problem and "row.json" in problem
               for problem in problems)


def test_one_bad_row_stops_the_whole_board(tmp_path):
    """All-or-nothing with more than one row: the good row is not published."""
    board = _board(tmp_path, [_row("good"), _row("bad", submission="")])
    with pytest.raises(Exception) as exc:
        bench_publish.publish(board, tmp_path / "out.html", title="T",
                              expected_tasks=TASKS)
    assert "bad" in str(exc.value) and "submission" in str(exc.value)
    assert not (tmp_path / "out.html").exists()


def test_a_degenerate_https_link_names_no_host(tmp_path):
    board = _board(tmp_path, [_row("builtin", submission="https://")])
    with pytest.raises(Exception) as exc:
        bench_publish.publish(board, tmp_path / "out.html", title="T",
                              expected_tasks=TASKS)
    assert "submission" in str(exc.value)


def test_a_symlink_out_of_the_row_directory_is_refused(tmp_path):
    """The textual check cannot see this one; `resolve()` can."""
    board = _board(tmp_path, [_row("builtin", submission="s.tar.gz")])
    outside = tmp_path / "outside.tar.gz"
    outside.write_bytes(b"x")
    (board / "rows" / "builtin" / "s.tar.gz").symlink_to(outside)
    with pytest.raises(Exception) as exc:
        bench_publish.publish(board, tmp_path / "out.html", title="T",
                              expected_tasks=TASKS)
    assert "submission" in str(exc.value)


def test_load_rows_reports_problems_instead_of_raising(tmp_path):
    board = _board(tmp_path, [_row("good"), _row("bad", model="")])
    rows, problems = bench_publish.load_rows(board, TASKS)
    assert [row["id"] for row in rows] == ["good"]
    assert any("bad" in problem and "model" in problem for problem in problems)


def test_the_checked_in_leaderboard_layout_publishes(tmp_path):
    out = tmp_path / "index.html"
    result = bench_publish.publish(REPO / "benchmarks" / "leaderboard", out,
                                   title="AgentCAD-Bench", expected_tasks=[])
    assert result["rows"] >= 0
    assert out.read_text().startswith("<!doctype html>")


def test_a_leaderboard_directory_that_is_not_there_is_a_harness_error(tmp_path):
    with pytest.raises(Exception) as exc:
        bench_publish.publish(tmp_path / "nope", tmp_path / "out.html",
                              title="T", expected_tasks=TASKS)
    assert "nope" in str(exc.value)


def test_row_problems_is_empty_for_a_complete_row(tmp_path):
    row_dir = tmp_path / "rows" / "builtin"
    row_dir.mkdir(parents=True)
    assert bench_publish.row_problems(_row("builtin"), _report(), row_dir,
                                      TASKS) == []


def test_row_problems_names_every_failing_key_at_once(tmp_path):
    row_dir = tmp_path / "rows" / "bad"
    row_dir.mkdir(parents=True)
    row = _row("bad", model="", harness_command="", date="2026-13-40")
    row.pop("config")
    problems = bench_publish.row_problems(row, _report(), row_dir, TASKS)
    joined = " | ".join(problems)
    for key in ("model", "harness_command", "config", "date"):
        assert key in joined


def test_render_leaderboard_tolerates_a_row_with_no_report():
    page = bench_publish.render_leaderboard([{"id": "x"}], title="T")
    assert "<script" not in page and "&mdash;" in page
