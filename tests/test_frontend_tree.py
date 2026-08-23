"""PRD-013 Assembly v2 — slice 7: the tree sidebar's grouped rows, in node.

The one property worth a unit test (invisible to a screenshot, unreachable from
Python): a pattern instance collapses to ONE row carrying a `xN` badge, a
sub-assembly to ONE read-only row naming its source, and a plain part to a plain
row. `frontend/js/tree_model.js` is pure — no DOM, and since PRD-027 slice 5
one import (`query_model.js`, pure too) — so its row model runs in node exactly
as in the browser. The folder/filter/selection half PRD-027 added to the same
module is tested in `tests/test_frontend_navigation.py`; these five cases stay
here because they pin the PRD-013 grouping the folder tree carries along.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

FRONTEND = Path(__file__).resolve().parents[1] / "frontend" / "js"
TREE_MODEL = FRONTEND / "tree_model.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is not installed")

HARNESS = """
import {{ __treeModel__ }} from {module};
const instances = JSON.parse(process.env.AGENTCAD_INSTANCES);
const flattened = JSON.parse(process.env.AGENTCAD_FLAT || "[]");
const rows = __treeModel__.instanceRows(instances);
process.stdout.write(JSON.stringify({{
  rows,
  html: __treeModel__.rowsHtml(rows),
  members: __treeModel__.memberIdsOf(process.env.AGENTCAD_BASE || "", flattened),
}}));
"""


def run(instances, flattened=None, base=""):
    script = HARNESS.format(module=json.dumps(TREE_MODEL.as_uri()))
    env = {**os.environ, "AGENTCAD_INSTANCES": json.dumps(instances),
           "AGENTCAD_FLAT": json.dumps(flattened or []),
           "AGENTCAD_BASE": base}
    out = subprocess.run(
        ["node", "--input-type=module", "--eval", script],
        capture_output=True, text=True, timeout=60, env=env)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_pattern_is_one_row_with_a_count_badge():
    out = run([{"id": "bolt", "part": "m6",
                "pattern": {"kind": "polar", "count": 8}}])
    assert len(out["rows"]) == 1
    row = out["rows"][0]
    assert row["count"] == 8 and row["badge"] == "×8"  # ×8
    assert row["expandable"] is True and row["kind"] == "polar"
    assert "×8" in out["html"]


def test_subassembly_is_one_readonly_row_naming_its_source():
    out = run([{"id": "engine", "assembly": {"project": "engine_src"}}])
    row = out["rows"][0]
    assert row["kind"] == "assembly" and row["source"] == "engine_src"
    assert row["expandable"] is True and row["readonly"] is True


def test_plain_part_is_a_plain_row():
    out = run([{"id": "base", "part": "plate", "config": "m"}])
    row = out["rows"][0]
    assert row["kind"] == "part" and row["part"] == "plate"
    assert row.get("expandable") in (False, None)
    assert row["config"] == "m"


def test_member_ids_of_a_pattern_come_from_the_flattened_view():
    flat = [{"id": "bolt[0]"}, {"id": "bolt[1]"}, {"id": "bolt[2]"},
            {"id": "other"}]
    out = run([{"id": "bolt", "part": "m6",
                "pattern": {"kind": "linear", "count": 3}}],
              flattened=flat, base="bolt")
    assert out["members"] == ["bolt[0]", "bolt[1]", "bolt[2]"]


def test_subassembly_members_use_the_slash_namespace():
    flat = [{"id": "engine/piston[0]"}, {"id": "engine/piston[1]"},
            {"id": "stand/foot"}]
    out = run([{"id": "engine", "assembly": {"project": "e"}}],
              flattened=flat, base="engine")
    assert out["members"] == ["engine/piston[0]", "engine/piston[1]"]
