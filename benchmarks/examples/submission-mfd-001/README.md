# Example submission — `model_from_drawing/mfd_001_spacer_plate`

This directory is an **AgentCAD project as an external agent hands it in**: a
`project.json` and one part script under `parts/`, nothing else. It is the
worked case for the external-agent walkthrough in
[`docs/bench.md`](../../../docs/bench.md#submitting-from-outside-the-repo) and
the fixture behind PRD-024's AC6.

Score it exactly the way a submitter would:

```bash
uv run agentcad bench score benchmarks/examples/submission-mfd-001 \
    --task model_from_drawing/mfd_001_spacer_plate --out /tmp/mfd-001
```

```
model_from_drawing/mfd_001_spacer_plate — model_from_drawing · task set bench-v1 v1 · harness 1 · agentcad 0.1.0
  subscore      status             value  weight   contrib
  built         ok                1.0000    0.15    0.1500
  geometry      ok                0.9919    0.50    0.4959
  interference  not_applicable    0.0000    0.00         —
  metrics       ok                1.0000    0.15    0.1500
  specs         ok                1.0000    0.10    0.1000
  valid         ok                1.0000    0.10    0.1000
bench score: model_from_drawing/mfd_001_spacer_plate — 0.9959 over 5 subscore(s)
```

**It is deliberately not perfect, and it is deliberately not the reference.**
Two small, entirely realistic readings of the drawing differ from it:

* the corner rounds are **R4**, not the R5 the sheet calls out;
* the holes are **Ø6.6** — the ISO 273 *medium* clearance for M6 — instead of
  the Ø6.0 THRU the sheet dimensions.

Everything else (the 80 × 50 × 6 envelope, the 60 × 30 hole pattern, the
`al6061` material, and the datum: bottom face on Z = 0, centred on the origin,
80 mm along +X) matches. The result is the honest shape of a good-but-not-exact
answer: every other subscore is 1.0 and the whole deviation lands on
`geometry`, where 143 mm³ of extra hole and 46 mm³ of missing corner cost
0.0081 of IoU against a 23 239 mm³ union. `score.json`'s
`subscores.geometry.detail.parts.spacer_plate` reports all four numbers.

Nothing in this directory declares `SPECS`: the scorer appends the *task's*
rubric to the copy it measures and re-binds `SPECS`, so a submission's own
checks are discarded. A submission that shipped a `specs.py` would be measuring
itself.
