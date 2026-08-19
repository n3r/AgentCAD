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

Do not change any part script and do not change any part's parameters — this
task is the placement only.

Datum: world frame. The base plate's underside is **Z = 0**, it is centred on
the origin in X and Y, and the node builds upward into **+Z**. Rotations are
**intrinsic XYZ Euler degrees**.
