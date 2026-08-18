"""The numeric range clamp, factored so the kernel worker and the share
customizer agree BY CONSTRUCTION rather than by two copies drifting apart.

Two callers need identical clamp semantics:

* ``kernel/worker.py:_resolve_numeric`` clamps a param inside a build (and warns).
* ``core/share_build.py`` clamps a visitor's params **server-side, before the
  variant cache key is computed**, so two out-of-range values that clamp to the
  same geometry coalesce onto one cache entry instead of minting a distinct key
  per request (PRD-007 review finding M-2).

This module imports **no** build123d/OCP, so the server process may import it
(the kernel-boundary rule in CLAUDE.md forbids importing ``worker`` itself).
The clamp is the single piece of range logic both paths share; keep it here.
"""

from __future__ import annotations

import math

#: The param types the numeric clamp applies to.
NUMERIC_TYPES = ("number", "int")


def is_nan(value) -> bool:
    """True for a float NaN. A NaN satisfies neither ``value < mn`` nor
    ``value > mx`` (both comparisons are False), so it would slip past the
    clamp and reach ``build(p)`` as a degenerate value — every caller rejects
    it up front (PRD-007 review finding m-2). ``inf`` is NOT rejected: it
    clamps to ``max`` correctly and that behaviour is intentional."""
    return isinstance(value, float) and math.isnan(value)


def clamp_numeric(entry: dict, value, name: str, ptype: str,
                  warnings: list[str]):
    """Clamp *value* to ``entry['min']``/``entry['max']``, appending a warning
    on each clamp — byte-identical to the worker's historical inline clamp.

    Assumes *value* is already the right Python type (int for ``int``, a finite
    number otherwise); callers coerce and reject NaN before calling."""
    mn, mx = entry.get("min"), entry.get("max")
    if ptype == "int":  # bounds were validated integral; keep the value an int
        mn = None if mn is None else int(mn)
        mx = None if mx is None else int(mx)
    if mn is not None and value < mn:
        warnings.append(f"param {name} clamped to min {mn}")
        value = mn
    if mx is not None and value > mx:
        warnings.append(f"param {name} clamped to max {mx}")
        value = mx
    return value
