"""Deterministic JSON in, deterministic JSON out.

Every bench artefact goes through here so byte-identity (FR6/AC3) is a property
of one module rather than of every call site. ``json.loads`` raises
**RecursionError** on deeply nested input, and ``RecursionError`` is not a
``ValueError`` -- the trap ``core/packages/_json.py`` was written for, restated
here because the bench reads documents (a submission's ``score.json``, a
leaderboard row) that come from somewhere else.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from ..core.model import ValidationError
from ..core.project import ProjectStore


def is_finite_number(value) -> bool:
    """A real, finite number. ``True``/``False`` are ints and are not numbers.

    One definition for all three readers (`scoring`, `report`, `publish`),
    because the rule is the same fact about JSON everywhere it is asked:
    ``json.loads`` parses the bare ``NaN`` / ``Infinity`` literals, so an
    ``isinstance`` test alone lets a non-finite value through — into a
    measurement (where a ``nan`` window comparison is false and the value never
    survives ``allow_nan=False``), into an aggregate (where every
    ``nan < -epsilon`` is false and the gate goes silently green) or into a
    sort key (where every comparison is false and the order stops being
    stable). ``isinstance(True, int)`` is true, so the bool test comes first.
    """
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def round_floats(value, places: int = 6):
    """Recursively round every float. Bools are ints and are left alone.

    ``isinstance(True, int)`` is true, so the bool test comes first or a
    ``True`` in a payload would be answered as the int ``1`` -- which is a
    different JSON document.
    """
    if isinstance(value, bool) or isinstance(value, int):
        return value
    if isinstance(value, float):
        return round(value, places)
    if isinstance(value, dict):
        return {key: round_floats(item, places) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [round_floats(item, places) for item in value]
    return value


def canonical_json(payload: dict) -> bytes:
    """Sorted keys, fixed indent, six decimals, no NaN, trailing newline.

    ``allow_nan=False`` because a NaN serialises as the bare ``NaN`` literal,
    which no strict parser accepts -- and a non-finite measurement is a
    ``status: "error"`` subscore, never a number.
    """
    text = json.dumps(round_floats(payload), sort_keys=True, indent=2,
                      allow_nan=False)
    return (text + "\n").encode()


def write_json(path, payload: dict) -> None:
    """Write *payload* canonically, through the staged-random-name writer."""
    ProjectStore._atomic_write(Path(path), canonical_json(payload))


def read_json(path, *, max_bytes: int = 4 << 20) -> dict:
    """Read a JSON object, refusing by size **before** parsing.

    The caught set is the one ``core/packages/_json.py`` argues for:
    ``RecursionError`` is not a ``ValueError`` and would otherwise escape as
    an unhandled exception out of a reader whose contract is
    ``ValidationError``.
    """
    target = Path(path)
    try:
        size = target.stat().st_size
    except OSError as exc:
        raise ValidationError(f"cannot read {target}: {exc}",
                              {"path": str(target)}) from exc
    if size > max_bytes:
        raise ValidationError(
            f"{target} is {size} bytes, above the {max_bytes}-byte limit; "
            f"it is refused before parsing", {"path": str(target)})
    try:
        value = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError, UnicodeDecodeError) as exc:
        raise ValidationError(f"{target} is not readable JSON: {exc}",
                              {"path": str(target)}) from exc
    if not isinstance(value, dict):
        raise ValidationError(f"{target} must hold a JSON object",
                              {"path": str(target)})
    return value
