"""Sketch-solver benchmark — the FR6 measurement harness (PRD-009).

A "staircase" of ``n_seg`` alternating horizontal/vertical lines, each carrying
an H/V constraint and a distance: a well-conditioned, exactly-constrained
sketch of the shape a real profile has. ``n_seg = 50`` is the FR6 size
(50 entities), and the two numbers FR6 names are measured here:

- **cold p50** — solved from the jittered starting coordinates;
- **warm-drag p50** — seeded at the previous solution with one coordinate
  nudged 0.4 mm, which is what a drag frame actually is.

This module is marked ``slow`` (``make test-fast`` skips it). Run it with
``uv run pytest -q tests/test_sketch_bench.py -s`` to see the table.

FR6's inequalities are asserted at `n_seg = 50` since the analytic-Jacobian
rewrite (slice 2). The prototype that preceded it measured **50.48 ms cold /
50.51 ms warm-drag** there — 3.2x over the 16 ms budget — which is why the
baseline was written down before the rewrite rather than after.
"""

import copy
import random
import statistics
import time

import pytest

from agentcad.toolkit.sketch import solve_sketch

pytestmark = pytest.mark.slow

N_SEGMENTS = (10, 25, 50)
REPS = 9  # >= 7 repetitions per the plan; p50 over all of them
DRAG_NUDGE_MM = 0.4

# FR6: a 50-entity sketch re-solves warm at <= 16 ms p50 and cold at <= 250 ms.
FR6_N_SEG = 50
FR6_WARM_MS = 16.0
FR6_COLD_MS = 250.0


def staircase(n_seg: int, jitter: float = 2.0, seed: int = 1) -> dict:
    """`n_seg` alternating H/V lines, each dimensioned. Exactly constrained."""
    rnd = random.Random(seed)
    points = [{"name": "p0", "x": 0.0, "y": 0.0, "fixed": True}]
    lines: list[dict] = []
    cons: list[dict] = []
    x = y = 0.0
    for i in range(n_seg):
        horiz = i % 2 == 0
        if horiz:
            x += 10.0
        else:
            y += 7.0
        points.append({"name": f"p{i + 1}",
                       "x": x + rnd.uniform(-jitter, jitter),
                       "y": y + rnd.uniform(-jitter, jitter)})
        lines.append({"name": f"l{i}", "p1": f"p{i}", "p2": f"p{i + 1}"})
        cons.append({"type": "horizontal" if horiz else "vertical", "ln": f"l{i}"})
        cons.append({"type": "distance", "p": f"p{i}", "q": f"p{i + 1}",
                     "d": 10.0 if horiz else 7.0})
    return {"points": points, "lines": lines, "circles": [], "constraints": cons}


def seeded_at(spec: dict, solution: dict) -> dict:
    """A copy of `spec` whose starting coordinates are the solved ones."""
    out = copy.deepcopy(spec)
    for p in out["points"]:
        q = solution["points"][p["name"]]
        p["x"], p["y"] = q["x"], q["y"]
    for c in out["circles"]:
        c["r"] = solution["circles"][c["name"]]["r"]
    return out


def nudged(spec: dict, dx: float = DRAG_NUDGE_MM) -> dict:
    """One free coordinate moved — a single drag frame's perturbation."""
    out = copy.deepcopy(spec)
    for p in out["points"]:
        if not p.get("fixed"):
            p["x"] += dx
            break
    return out


def p50_ms(spec: dict, reps: int = REPS) -> tuple[float, dict]:
    times = []
    result = None
    for _ in range(reps):
        t0 = time.perf_counter()
        result = solve_sketch(spec)
        times.append((time.perf_counter() - t0) * 1e3)
    return statistics.median(times), result


@pytest.fixture(scope="module")
def bench_rows() -> list[dict]:
    """Measure once; every test in this module reads the same table."""
    rows = []
    for n_seg in N_SEGMENTS:
        spec = staircase(n_seg)
        cold_ms, cold = p50_ms(spec)
        warm_ms, warm = p50_ms(nudged(seeded_at(spec, cold)))
        rows.append({
            "n_seg": n_seg,
            "n_params": cold["n_params"],
            "n_residuals": cold["n_residuals"],
            "nfev": cold["nfev"],
            "cold_ms": cold_ms,
            "warm_ms": warm_ms,
            "cold_ok": cold["ok"],
            "warm_ok": warm["ok"],
            "max_residual": max(cold["max_residual"], warm["max_residual"]),
        })
    print("\n=== sketch solver: staircase benchmark "
          f"({REPS} reps, p50) ===")
    print(f"{'n_seg':>6} {'params':>7} {'res':>5} {'nfev':>5} "
          f"{'cold p50':>10} {'warm-drag p50':>14}")
    for r in rows:
        print(f"{r['n_seg']:>6} {r['n_params']:>7} {r['n_residuals']:>5} "
              f"{r['nfev']:>5} {r['cold_ms']:>9.2f}ms {r['warm_ms']:>13.2f}ms")
    return rows


@pytest.mark.parametrize("n_seg", N_SEGMENTS)
def test_staircase_solves_cold_and_warm(bench_rows, n_seg):
    """The benchmark sketches must actually solve, or the timings are noise."""
    row = next(r for r in bench_rows if r["n_seg"] == n_seg)
    assert row["cold_ok"], row
    assert row["warm_ok"], row
    assert row["max_residual"] < 1e-9, row


def test_benchmark_table_is_printed(bench_rows):
    assert [r["n_seg"] for r in bench_rows] == list(N_SEGMENTS)
    assert all(r["cold_ms"] > 0 and r["warm_ms"] > 0 for r in bench_rows)


def test_fr6_warm_drag_budget(bench_rows):
    """FR6, the interactive half. Headroom note: measured 2.9-3.2 ms against a
    16 ms budget on an M1 Max — 5x, comfortably past the 3x this test exists to
    keep. A regression that eats the headroom without crossing the budget still
    shows up in the printed table."""
    row = next(r for r in bench_rows if r["n_seg"] == FR6_N_SEG)
    assert row["warm_ms"] <= FR6_WARM_MS, (
        f"FR6 warm-drag budget missed at n_seg={FR6_N_SEG}: measured "
        f"{row['warm_ms']:.2f} ms p50 (budget {FR6_WARM_MS} ms). The usual "
        "cause is a residual whose `df` was dropped, so scipy fell back to "
        "finite differences.")


def test_fr6_cold_solve_budget(bench_rows):
    """FR6, the from-scratch half."""
    row = next(r for r in bench_rows if r["n_seg"] == FR6_N_SEG)
    assert row["cold_ms"] <= FR6_COLD_MS, (
        f"FR6 cold budget missed at n_seg={FR6_N_SEG}: measured "
        f"{row['cold_ms']:.2f} ms p50 (budget {FR6_COLD_MS} ms).")
