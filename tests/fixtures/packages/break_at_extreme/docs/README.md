# break_at_extreme

The AC2a fixture: a package whose one part, `strut`, builds happily at its
default and **raises at `length=max`**.

Everything else about it is correct — the manifest validates, the part
declares `PARAMS` and `build(p)`, every numeric parameter carries its bounds,
unit and description — so the only thing the gate can be red about is the
extreme. That is the point: a gate that only built the default would call this
package green, and the catalog's promise ("it is in the registry" means
something measured) would be worth nothing.

The publish gate is a correctness gate, not a security boundary.
