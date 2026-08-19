# benchmarks/tasks/modify_to_spec/mts_005_m10_clamp/specs/parts/clamp_plate.py
#
# The rubric. This block is appended to the END of the candidate's part script,
# so it must RE-BIND `SPECS` (never `+=`): the last module-level binding wins,
# and any `SPECS` the candidate authored for itself is discarded. Every
# constructor is imported under a `_bench_` alias because the candidate's own
# module namespace is in scope here — an alias makes a same-named module-level
# function in the candidate script irrelevant.
from agentcad.toolkit.specs import (
    check_bbox as _bench_check_bbox,
    check_that as _bench_check_that,
    check_valid as _bench_check_valid,
    check_wall as _bench_check_wall,
)

# `footprint` and `plate_thickness` are the two rows the 40 x 40 x 8 starter
# fails; `envelope` is the row that stops "make it bigger still".
#
# What `check_wall` reads here, said plainly: on a square plate with one
# central hole the thinnest section IS the plate, so the sampler returns the
# THICKNESS — 10.0 mm on the reference, 8.0 mm on the starter. The floor of
# 9.5 is therefore the 10 mm the requirement asks for, in measured terms with
# 0.5 mm of sampling slack, and it says **nothing whatever about the bore**:
# the ring of material around a Ø11 hole in a 64 mm plate is 26.5 mm and never
# the minimum. The Ø11.0 clearance requirement is measured by the
# `clearance_bore` window in `reference/metrics.json` (`volume_mm3` in
# 39828.5..39914.0, which is the reference's 39872.32 with the adjacent
# half-millimetre bores — Ø10.5 at 39956.75 and Ø11.5 at 39783.97 — outside
# it), because with the bbox pinned to 64 x 64 x 10 the residual volume IS the
# hole. The interference half of this task is the `interference` subscore,
# which measures the whole assembly; nothing here duplicates it.
SPECS = [
    _bench_check_valid(name="valid", requirement="MTS-005"),
    _bench_check_that(lambda part, metrics:
                      metrics["bbox"]["max"][0] - metrics["bbox"]["min"][0]
                      >= 63.5
                      and metrics["bbox"]["max"][1] - metrics["bbox"]["min"][1]
                      >= 63.5,
                      name="footprint", requirement="MTS-005"),
    _bench_check_wall(min_mm=9.5, grid=4, name="plate_thickness",
                      requirement="MTS-005"),
    # The bore, measured directly rather than inferred: a circular edge of
    # radius 5.5 is an M10 medium-fit clearance hole and nothing else on this
    # part is round except the R4 corner fillets. `check_that` is handed the
    # real shape, so this asks the geometry the question the requirement asks,
    # and it is robust to a candidate whose corner fillet differs — which a
    # volume band is not. The `clearance_bore` metric window measures the same
    # requirement by the independent route (with the bbox pinned, the residual
    # volume IS the hole), so a wrong bore costs a specs row AND a window.
    _bench_check_that(lambda part, metrics:
                      any(abs(edge.radius - 5.5) <= 0.15
                          for edge in part.edges()
                          if edge.geom_type.name == "CIRCLE"),
                      name="clearance_bore", requirement="MTS-005"),
    _bench_check_bbox(within_mm=(64.5, 64.5, 10.5), name="envelope",
                      requirement="MTS-005"),
]
