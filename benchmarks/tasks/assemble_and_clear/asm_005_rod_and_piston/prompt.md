The project holds the five parts of one piston-and-connecting-rod set —
`piston`, `wrist_pin`, `rod_body` (the upper big-end half and the blade),
`rod_cap` (the lower big-end half) and `rod_bolt_pair` — and **no assembly at
all**: the instance list is empty.

Assemble the set. Place exactly **five** instances and give them these ids,
because the design specs name them:

| instance id | part |
|---|---|
| `rod_1` | `rod_body` |
| `rod_cap_1` | `rod_cap` |
| `rod_bolts_1` | `rod_bolt_pair` |
| `piston_1` | `piston` |
| `wrist_pin_1` | `wrist_pin` |

All five parts are drawn on the **same local frame convention**, which is why
this set assembles with **no rotations at all**: the big-end bore axis is
**Y through the local origin**, and the rod's joint face is the plane
**Z = 0**. Read each part's docstring and parameters — they say so.

Requirements:

- `rod_1` sits at the **origin**, unrotated. It is the datum.
- `rod_cap_1` closes the big-end bore from below, unrotated, **dropped 0.05 mm
  along the rod axis** (i.e. in -Z) from the body's joint face. That gap is the
  bearing shell's crush height and it is modelled, not assumed.
- `rod_bolts_1` takes the **rod's own pose** — same position, same rotation —
  because the bolt pair is drawn in the rod's frame.
- `piston_1` and `wrist_pin_1` both sit at the rod's **small-end centre**,
  unrotated. The small end is the rod's `length` parameter up the **+Z** axis
  from the big-end centre.
- The wrist pin is a **floating** pin: it must stay clear of the rod's small
  end by at least **0.05 mm** (the small-end bore is the pin plus 0.25) and
  clear of the piston bosses too.
- **No two of the five instances may overlap** by any volume.

Do not change any part script and do not change any part's parameters — this
task is the placement only.

Datum: world frame, **no rotations anywhere**. The rod's big-end bore axis is
**Y** through the origin, its joint face is **Z = 0**, and the small end,
piston and pin are up the **+Z** axis.
