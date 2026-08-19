The project already holds the truss gusset part `gusset_plate`, detailed for
M16 bolts. The connection has been re-designed for M20 and the plate has to
follow.

Requirement: every member group — the bottom chord and both diagonals — gets
**four bolt rows at 60 mm pitch** with **Ø22 holes**, and the end distance from
the outer hole centres to the plate edge is **exactly 1.5 x the hole diameter**
(33 mm), which is the code minimum for this connection.

Constraints:

- Change only `hole_d`, `pitch`, `n_rows` and `edge_dist`. `plate_t` stays
  10 mm, `diag_angle_deg` stays 45°, `chord_w` stays 80 mm and `diag_w` stays
  60 mm.
- The plate outline is the convex hull of the three member footprints and is
  derived from those numbers in the script — it must follow the new pattern
  rather than be drawn by hand.
- Note that the part's current parameters are stored in the project manifest
  and are **not** the script's own defaults. Change the stored parameters.

Datum: unchanged. The plate lies flat with its back face on Z = 0, the panel
work point is the origin in X, the bottom chord band runs along X and the plate
extends into +Y only.
