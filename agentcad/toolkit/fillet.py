"""Robust fillet: apply the largest radius that actually succeeds.

OCCT fillet failures are the most common way an agent's script dies. This
searches down from the requested radius to the largest working one and reports
what it did, so a script keeps producing geometry instead of raising.
Validated against build123d 0.11.1 / OCCT.
"""

from __future__ import annotations

from typing import Iterable

from build123d import Edge, fillet


def safe_fillet(
    part,
    edges: Iterable[Edge],
    radius: float,
    *,
    min_radius: float = 0.05,
    rel_tol: float = 0.02,
    use_max_fillet_hint: bool = True,
):
    """Fillet ``edges`` at ``radius``; on OCCT failure binary-search the
    largest radius that succeeds. Returns ``(new_part, achieved_radius,
    warning|None)``."""
    edges = list(edges)
    if not edges:
        return part, 0.0, "safe_fillet: no edges given; part unchanged"

    def attempt(r: float):
        try:
            out = fillet(edges, radius=r)
            if not out.is_valid or out.volume <= 0:
                return None
            return out
        except Exception:  # noqa: BLE001 — OCCT raises many types on bad fillets
            return None

    result = attempt(radius)
    if result is not None:
        return result, radius, None

    hi = radius
    if use_max_fillet_hint and hasattr(part, "max_fillet"):
        try:
            hint = part.max_fillet(
                edges, tolerance=max(rel_tol * radius, 0.01), max_iterations=8
            )
            if 0 < hint < hi:
                hi = min(hi, hint * 1.05)
        except Exception:  # noqa: BLE001 — max_fillet often raises where fillet fails
            pass

    lo = min_radius
    lo_part = attempt(lo)
    if lo_part is None:
        return (
            part,
            0.0,
            f"safe_fillet: fillet failed even at minimum radius {min_radius}; "
            "part returned unfilleted (edges may form an impossible network).",
        )

    best, best_part = lo, lo_part
    while hi - lo > max(rel_tol * radius, 1e-3):
        mid = 0.5 * (lo + hi)
        r_part = attempt(mid)
        if r_part is not None:
            best, best_part, lo = mid, r_part, mid
        else:
            hi = mid

    warning = (
        f"safe_fillet: requested radius {radius} failed; applied largest "
        f"working radius {best:.3f} instead."
    )
    return best_part, best, warning
