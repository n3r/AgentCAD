The project already holds the thrust chamber part `nozzle` at its current
design. Modify it to meet a new mass budget.

Requirement: the chamber must weigh **less than 900 g**. It weighs about
1078 g today.

Constraints:

- `wall` is the only parameter you may change. `chamber_d`, `chamber_l`,
  `throat_d` and `expansion_ratio` must keep their current values — the
  expansion ratio and the whole inner contour are frozen, so the overall
  length stays 199.19 mm.
- The wall requirement is stated in *measured* terms, the way the part's own
  design intent states it: `check_wall(min_mm=0.8, grid=4)` must stay green.
  That sampler lands on the chamfered exit lip, not the barrel, so it reads
  roughly a third of `wall` — thinning too far breaches it.
- Move `wall` in 0.5 mm steps, and leave it as **thick** as the mass budget
  allows. A chamber thinner than it needs to be is a rejected design.

Datum: unchanged. The injector-interface rim lies on Z = 0, the engine axis is
the Z axis, the chamber runs down -Z to the exit and the part is centred on the
origin in X and Y.
