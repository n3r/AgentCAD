<!-- Reviewer note (stripped from the prompt the agent sees) — how the graded
     clearance windows were derived. Every bound comes from the gap MEASURED
     on the reference placement; the floors are as shipped, the ceilings are
     roughly twice the measurement:

       row                  pair                        measured   window
       gusset_seat          gusset_1 - base_plate_1      2.000 mm   1.0-3.0
       left_bracket_seat    bracket_left - base_plate_1  0.500 mm   0.25-1.0
       right_bracket_seat   bracket_right - base_plate_1 0.500 mm   0.25-1.0
       left_web_gap         bracket_left - gusset_1      0.500 mm   0.25-1.0
       right_web_gap        bracket_right - gusset_1     0.500 mm   0.25-1.0

     The two bracket-to-plate rows are new: the prompt already stated that
     0.5 mm float and nothing measured it. `gusset_seat` reads to the 1 mm
     recess floor rather than the top face (see specs/project.py) and tracks
     the gusset's lower edge one for one — a gusset 1 mm higher measures
     3.000 mm, so the 3.0 mm ceiling reds an edge above about Z = 22.

     The sixth pair, bracket_left to bracket_right (measured 11.000 mm), is
     deliberately unbounded: the brackets do not seat on each other and the
     prompt states no gap between them, so a window there would be a rubric
     invention. Their placement is graded against the plate and the web
     instead. What no pair distance can see is a member slid ALONG a face it
     stays parallel to (the +-40 mm out along X); that is the residual and it
     is disclosed in docs/bench.md. -->

The project holds the three fabricated members of a steel truss node —
`base_plate` (a 300 x 300 x 20 column base plate), `gusset_plate` (the
connection gusset) and `angle_bracket` (a 90 x 90 x 10 L, used twice) — and
**no assembly at all**: the instance list is empty.

Set the node out. Place exactly **four** instances and give them these ids,
because the design specs name them:

| instance id | part |
|---|---|
| `base_plate_1` | `base_plate` |
| `gusset_1` | `gusset_plate` |
| `bracket_left` | `angle_bracket` |
| `bracket_right` | `angle_bracket` |

Requirements:

- `base_plate_1` sits at the **origin**, unrotated. Its underside is **Z = 0**
  and its top face is **Z = 20**.
- `gusset_1` stands **upright** on the plate. Rotate it
  **[90, 0, 0]** (intrinsic XYZ Euler degrees, the project's convention);
  its 10 mm web then lies in a plane parallel to XZ. Position it on the plate's
  centre line so its lower edge keeps a **root gap of at least 1 mm** above the
  plate's top face (Z = 20) — the shipped node puts that edge at **Z = 21**.
- `bracket_left` is rotated **[0, 0, 90]** and `bracket_right` **[0, 0, -90]**.
  Each sits on the plate about **40 mm out from the node centre along X**
  (left at -40, right at +40), floating about **0.5 mm above** the plate's top
  face, with its upright leg lying against one face of the gusset web —
  **on opposite faces**, and each keeping a **root gap of at least 0.25 mm**
  from the web.
- **No two of the four instances may overlap** by any volume. This is a welded
  node: every member is drawn with a root gap, nothing is drawn in contact.

Every root gap the prompt states is graded as a **two-sided window** — a floor
*and* a ceiling, because a member parked clear of the node is not set out:

- `gusset_1` to `base_plate_1`: **1.0 mm to 3.0 mm**. (The closest approach is
  to the plate's 1 mm-deep column-footprint recess under the web, so the
  shipped edge at Z = 21 measures 2 mm; the window moves with the edge.)
- `bracket_left` and `bracket_right` to `base_plate_1`: **0.25 mm to 1.0 mm**
  each — the 0.5 mm float above the top face.
- `bracket_left` and `bracket_right` to `gusset_1`: **0.25 mm to 1.0 mm**
  each — the root gap against the web face.

Do not change any part script and do not change any part's parameters — this
task is the placement only.

Datum: world frame. The base plate's underside is **Z = 0**, it is centred on
the origin in X and Y, and the node builds upward into **+Z**. Rotations are
**intrinsic XYZ Euler degrees**.
