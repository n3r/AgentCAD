# widget_good

The green fixture for the PRD-011 publish gate: one parametric part that
builds at every declared extreme, mates on both of its connectors, and passes
its own SPECS.

## Parts

### `mount_block` — mounting block

A rectangular block with a through bore and (optionally) a chamfered top edge.

| parameter | type | range | unit |
|---|---|---|---|
| `length` | number | 24 – 80 | mm |
| `bore_d` | number | 3 – 16 | mm |
| `grade` | enum | `std`, `wide` | — |
| `chamfered` | bool | — | — |

Connectors: `seat` (rigid, the bottom face centre) and `bore` (cylindrical,
the through-bore axis). The seat is rigid deliberately — the moving side of a
mate must be, because the anchor connector carries the DOF.

Presets: `short` and `wide_16`. `wide_16` is a *corner* — the longest, widest
block with the largest bore — which the gate's one-at-a-time sweep never
reaches on its own. Declaring it as a configuration is how an author asks for
corner coverage; the gate builds every preset.

## Trust

The publish gate is a correctness gate, not a security boundary. It proves
that this geometry builds, that its specs pass and that its connectors mate.
It proves nothing about intent: this script runs in your kernel worker with
your privileges. See `docs/packages.md`.
