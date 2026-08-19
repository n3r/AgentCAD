"""PRD-013 Assembly v2 — slice 8: the placement card's DOF editor + pattern
spec, in node.

Two decisions are easy to get subtly wrong and invisible to a screenshot: which
DOF fields a mate shows (chosen from its resolved `params` vocabulary, not a
type field the instance does not carry) and the exact pattern payload a
`set_pattern` call sends. `frontend/js/placement_model.js` is pure, so both run
in node.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

FRONTEND = Path(__file__).resolve().parents[1] / "frontend" / "js"
MODEL = FRONTEND / "placement_model.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is not installed")

HARNESS = """
import {{ __placementModel__ as m }} from {module};
const inp = JSON.parse(process.env.AGENTCAD_IN);
process.stdout.write(JSON.stringify({{
  dof: m.dofEditor(inp.mate),
  patternLinear: m.patternSpec("linear", 3.4, 12),
  patternPolar: m.patternSpec("polar", 8, 45),
  draft: m.patternDraft(inp.inst || {{}}),
}}));
"""


def run(mate=None, inst=None):
    script = HARNESS.format(module=json.dumps(MODEL.as_uri()))
    out = subprocess.run(
        ["node", "--input-type=module", "--eval", script],
        capture_output=True, text=True, timeout=60,
        env={**os.environ,
             "AGENTCAD_IN": json.dumps({"mate": mate, "inst": inst})})
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_slider_mate_shows_one_offset_field():
    out = run(mate={"connector": "foot", "params": {"position": 12}})
    assert out["dof"]["kind"] == "slider"
    keys = [f["key"] for f in out["dof"]["fields"]]
    assert keys == ["offset_mm"]
    assert out["dof"]["fields"][0]["value"] == 12


def test_planar_mate_shows_u_v_spin_fields():
    out = run(mate={"params": {"u": 10, "v": 5, "spin": 0}})
    assert out["dof"]["kind"] == "planar"
    keys = [f["key"] for f in out["dof"]["fields"]]
    assert keys == ["u_mm", "v_mm", "spin_deg"]


def test_rigid_mate_has_no_dof_editor():
    out = run(mate={"connector": "seat"})   # no params
    assert out["dof"] is None


def test_pattern_spec_linear_and_polar_payloads():
    out = run()
    # count coerced to an integer >= 1; linear carries step_mm, polar angle_step
    assert out["patternLinear"] == {"kind": "linear", "count": 3,
                                    "step_mm": 12}
    assert out["patternPolar"] == {"kind": "polar", "count": 8,
                                   "angle_step_deg": 45}


def test_pattern_draft_reads_existing_polar():
    out = run(inst={"pattern": {"kind": "polar", "count": 6,
                                "angle_step_deg": 60}})
    assert out["draft"] == {"kind": "polar", "count": 6, "spacing": 60,
                            "active": True}
