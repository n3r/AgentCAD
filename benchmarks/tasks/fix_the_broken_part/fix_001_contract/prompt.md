The project holds one part, `sensor_mount`, and it does not build: the part
comes back with a script error and the viewport stays empty. Nothing about the
design has changed — the script is simply wrong.

**Fixed** means: `sensor_mount` builds, comes back as **one valid solid**, and
is the part the script was always meant to describe. Keep the part id
`sensor_mount` and keep every dimension below.

The design, unchanged:

- a **60 mm (X) x 40 mm (Y) x 5 mm** plate, its four vertical corners broken
  with **R5**;
- a **Ø16 mm** boss standing **12 mm** above the plate's top face, centred;
- a **Ø8 mm** bore straight through the boss and the plate, on the same centre;
- **two Ø5 mm** through holes in the plate, on the X axis, **44 mm apart**
  (i.e. at X = +22 and X = -22).

Material: 6061 aluminium (unchanged).

Datum: the plate's underside lies on **Z = 0** and the plate is centred on the
origin in X and Y; the 60 mm length runs along **X**, the 40 mm width along
**Y**, and the boss stands up into **+Z**.
