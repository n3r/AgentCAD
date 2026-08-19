The project holds one part, `enclosure_base` — the base shell of a snap-fit
electronics enclosure. It builds and it looks right, but the moulder has
rejected it: he measured the shell wall at **1.2 mm** and the tool was cut for
a far thicker one. At 1.2 mm the shell will not fill, and it weighs barely half
what the part is supposed to weigh.

**Fixed** means: `enclosure_base` builds, comes back as **one valid solid**, and
the shell is back at the thickness the tool was cut for. Keep the part id
`enclosure_base`.

Requirement: the **wall and floor thickness is 2.5 mm**. Every other dimension
stays exactly as it is — the outer shell is still **100 mm (X) x 60 mm (Y) x
30 mm (Z)**, the corner radius is still 3 mm, and the bosses, standoffs and
ventilation slots keep their own parameters.

Material: ABS (unchanged).

Datum: the shell's underside lies on **Z = 0**, it is centred on the origin in
X and Y, and it opens upward into **+Z**. The starter already sits on this
datum and the fix must not move it.
