"""PRD-013 Assembly v2 — slice 5: instanced-render id mapping, in node.

The one property of the InstancedMesh path that is NOT visible in a screenshot
and NOT reachable from Python: a raycast hit's `instanceId` must map back to the
expanded assembly id, so the existing click-select contract does not regress at
scale. `frontend/js/instancing.js` is pure (no THREE), so its grouping + id
table run in node exactly as in the browser.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

FRONTEND = Path(__file__).resolve().parents[1] / "frontend" / "js"
INSTANCING = FRONTEND / "instancing.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is not installed")

HARNESS = """
import {{ __instanceIndex__ }} from {module};
const items = JSON.parse(process.env.AGENTCAD_ITEMS);
const idx = __instanceIndex__.buildInstanceIndex(items);
process.stdout.write(JSON.stringify({{
  idForInstance: idx.idForInstance,
  groups: idx.groups.length,
  counts: __instanceIndex__.instanceCounts(items),
}}));
"""


def run_index(items: list[dict]) -> dict:
    script = HARNESS.format(module=json.dumps(INSTANCING.as_uri()))
    out = subprocess.run(
        ["node", "--input-type=module", "--eval", script],
        capture_output=True, text=True, timeout=60,
        env={**os.environ, "AGENTCAD_ITEMS": json.dumps(items)})
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def _pattern_of(n: int, part="bolt", key="K"):
    return [{"instanceId": f"{part}[{i}]", "partId": part, "key": key,
             "position": [i * 10, 0, 0], "rotationDeg": [0, 0, 0],
             "color": "#888"} for i in range(n)]


def test_instanced_pick_maps_to_expanded_id():
    out = run_index(_pattern_of(8))
    # one geometry upload for the whole pattern; member 3 -> "bolt[3]"
    assert out["groups"] == 1
    assert out["idForInstance"]["0:3"] == "bolt[3]"
    assert out["counts"] == {"instances": 8, "geometries": 1}


def test_distinct_parts_are_distinct_groups():
    items = _pattern_of(3, part="a", key="Ka") + _pattern_of(2, part="b", key="Kb")
    out = run_index(items)
    assert out["groups"] == 2
    assert out["idForInstance"]["0:2"] == "a[2]"
    assert out["idForInstance"]["1:1"] == "b[1]"
    assert out["counts"]["instances"] == 5


def test_namespaced_subassembly_ids_round_trip():
    items = [{"instanceId": "stand/engine/piston[0]", "partId": "piston",
              "key": "K", "position": [0, 0, 0], "rotationDeg": [0, 0, 0]}]
    out = run_index(items)
    assert out["idForInstance"]["0:0"] == "stand/engine/piston[0]"
