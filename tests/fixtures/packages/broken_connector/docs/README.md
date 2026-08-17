# broken_connector

The AC2b fixture: a package whose one part, `bracket`, builds at every
declared extreme and whose `connectors(p, part)` returns an axis the kernel
refuses (`"up"` is not an `Axis`, and not `((point), (direction))` either).

It exists to prove that the `connectors` stage names the **connector** rather
than shrugging at the package: a catalog of parts that cannot mate is a
catalog of pictures.

The publish gate is a correctness gate, not a security boundary.
