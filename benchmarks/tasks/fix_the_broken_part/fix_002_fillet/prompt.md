The project holds one part, `shelf_bracket`, and it does not build. The build
fails inside the kernel with

```
ValueError: Failed creating a fillet with radius of 12.0, try a smaller value
or use max_fillet() to find the largest valid fillet radius
```

**Fixed** means: `shelf_bracket` builds, comes back as **one valid solid**, and
carries the breaks listed below. Keep the part id `shelf_bracket`.

The bracket, unchanged:

- an L section: a **70 mm** horizontal leg along **X** and a **55 mm** upright
  leg along **Z**, both **6 mm** thick, **40 mm** wide;
- an **R8** fillet on the inside heel, where the two legs meet;
- an **R4** break on each of the two free leg ends — the end of the horizontal
  leg and the top of the upright.

Material: A36 steel (unchanged).

Datum: the outside heel corner is at the **origin**; the horizontal leg runs
into **+X**, the upright leg into **+Z**, and the 40 mm width into **+Y**.
