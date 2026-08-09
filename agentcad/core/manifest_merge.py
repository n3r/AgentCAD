"""Structure-aware three-way merge for project.json (pure Python, no I/O).

A line-wise merge of a JSON manifest produces garbage (or, worse, a *clean*
result that nobody authored). This module merges the manifest at CAD key
granularity instead — one independent decision per parameter, per part field,
per instance field, per material — and reports what it could not decide.

Key space (every leaf merges independently)::

    schema_version | name | units | <any other top-level key>   whole value
    parts.<id>                       entry add/remove, else field-wise:
      parts.<id>.label|material|kind|source                     whole value
      parts.<id>.params.<name>                                  per parameter
      parts.<id>.solid_materials.<key>                          per key
      parts.<id>.pmi                                            atomic
    assembly.instances.<id>          entry add/remove, else field-wise:
      …<id>.part|position|rotation_deg|color|mate               whole value
    materials.<id>                                              atomic

``pmi`` is atomic because its frames cross-reference each other by id;
``materials.<id>`` because merging one side's density with the other's yield
strength is over-clever; ``position``/``rotation_deg`` because merging X from
one side and Z from the other yields a placement nobody authored.

Values compare by type *and* JSON shape, so ``6`` and ``6.0`` are different
values — matching how params are stored and how a byte comparison of
project.json would see them.

Referential integrity is deliberately not this module's job: one side deleting
a part while the other adds an instance of it touches no common key, so it
merges clean. The kernel validation pass is the backstop.
"""

from __future__ import annotations

import copy
import json

from .model import ValidationError

CONFLICT_KEYS = ("kind", "key", "base", "ours", "theirs")

_MISSING = object()

_PART_SUBDICTS = ("params", "solid_materials")


def merge_manifests(base: dict, ours: dict, theirs: dict) -> tuple[dict, list[dict]]:
    """Three-way merge of project.json at CAD key granularity.

    ``ours`` is the TARGET branch (what you merge into), ``theirs`` the SOURCE.
    Returns ``(merged manifest, conflicts)``. The merged manifest carries ours'
    value at every conflicted key, so it is always a loadable document —
    resolution (see :func:`apply_choices`) overwrites those keys.
    """
    base, ours, theirs = _as_dict(base), _as_dict(ours), _as_dict(theirs)
    conflicts: list[dict] = []
    merged: dict = {}
    for key in _key_order(ours, theirs):
        value = _merge_section(
            key, _get(base, key), _get(ours, key), _get(theirs, key), conflicts
        )
        if value is not _MISSING:
            merged[key] = value
    return merged, conflicts


def apply_choices(
    merged: dict, conflicts: list[dict], choices: dict
) -> tuple[dict, list[dict]]:
    """Apply resolutions to a merged manifest.

    ``choices`` maps a conflict key to ``{"take": "ours"|"theirs"|"base"}`` or
    ``{"value": <any>}``. Returns ``(new manifest, still-outstanding
    conflicts)``; neither argument is mutated. An unknown key or choice shape
    raises ``ValidationError``.
    """
    if not isinstance(choices, dict):
        raise ValidationError("choices must be an object of {key: {take|value}}")
    outstanding = {c["key"]: c for c in conflicts}
    result = copy.deepcopy(_as_dict(merged))
    for key, choice in choices.items():
        conflict = outstanding.get(key)
        if conflict is None:
            raise ValidationError(
                f"no outstanding conflict at key {key!r}",
                {"outstanding": sorted(outstanding)},
            )
        present, value = _choice_value(key, conflict, choice)
        _write_key(result, key, value, present)
    return result, [c for c in conflicts if c["key"] not in choices]


# ------------------------------------------------------------- merge core


def _merge_section(key, base, ours, theirs, conflicts):
    if key == "parts" and _entry_list(base, ours, theirs):
        return _merge_entry_list("parts", base, ours, theirs, conflicts,
                                 subdicts=_PART_SUBDICTS)
    if key == "assembly" and _keyed(base, ours, theirs):
        return _merge_assembly(base, ours, theirs, conflicts)
    if key == "materials" and _keyed(base, ours, theirs):
        return _merge_entry_dict("materials", base, ours, theirs, conflicts)
    return _merge_atomic(key, base, ours, theirs, conflicts)


def _merge_assembly(base, ours, theirs, conflicts):
    result = {}
    for sub in _key_order(_as_dict(ours), _as_dict(theirs)):
        b, o, t = _get(base, sub), _get(ours, sub), _get(theirs, sub)
        if sub == "instances" and _entry_list(b, o, t):
            value = _merge_entry_list(
                "assembly.instances", b, o, t, conflicts, subdicts=()
            )
        else:
            value = _merge_atomic(f"assembly.{sub}", b, o, t, conflicts)
        if value is not _MISSING:
            result[sub] = value
    return result


def _merge_entry_list(prefix, base, ours, theirs, conflicts, *, subdicts):
    b, o, t = _by_id(base), _by_id(ours), _by_id(theirs)
    result = []
    for eid in _key_order(o, t):
        value = _merge_entry(
            f"{prefix}.{eid}",
            _get(b, eid),
            _get(o, eid),
            _get(t, eid),
            conflicts,
            subdicts,
        )
        if value is not _MISSING:
            result.append(value)
    return result


def _merge_entry_dict(prefix, base, ours, theirs, conflicts):
    """Sections whose entries are atomic (materials)."""
    result = {}
    for eid in _key_order(_as_dict(ours), _as_dict(theirs)):
        value = _merge_atomic(
            f"{prefix}.{eid}", _get(base, eid), _get(ours, eid),
            _get(theirs, eid), conflicts,
        )
        if value is not _MISSING:
            result[eid] = value
    return result


def _merge_entry(key, base, ours, theirs, conflicts, subdicts):
    """One list entry: field-wise when it exists on all three sides, whole-value
    otherwise (add/add, delete/modify — the entry is the conflict unit)."""
    if _MISSING in (base, ours, theirs):
        return _merge_atomic(key, base, ours, theirs, conflicts)
    result = {}
    for field in _key_order(_as_dict(ours), _as_dict(theirs)):
        b, o, t = _get(base, field), _get(ours, field), _get(theirs, field)
        fkey = f"{key}.{field}"
        if field in subdicts and _keyed(b, o, t):
            value = _merge_scalar_dict(fkey, b, o, t, conflicts)
        else:
            value = _merge_atomic(fkey, b, o, t, conflicts)
        if value is not _MISSING:
            result[field] = value
    return result


def _merge_scalar_dict(prefix, base, ours, theirs, conflicts):
    result = {}
    for name in _key_order(_as_dict(ours), _as_dict(theirs)):
        value = _merge_atomic(
            f"{prefix}.{name}", _get(base, name), _get(ours, name),
            _get(theirs, name), conflicts,
        )
        if value is not _MISSING:
            result[name] = value
    return result


def _merge_atomic(key, base, ours, theirs, conflicts):
    """The classic three-way truth table over one whole value. ``_MISSING`` on a
    side means the key is absent there (never added, or deleted)."""
    if _same(ours, theirs):
        return _copy(ours)
    if _same(base, ours):  # only theirs moved
        return _copy(theirs)
    if _same(base, theirs):  # only ours moved
        return _copy(ours)
    conflicts.append(_conflict(key, base, ours, theirs))
    return _copy(ours)


def _conflict(key, base, ours, theirs) -> dict:
    entry = {"kind": "manifest", "key": key}
    if base is not _MISSING:  # omitted for add/add
        entry["base"] = _copy(base)
    entry["ours"] = None if ours is _MISSING else _copy(ours)
    entry["theirs"] = None if theirs is _MISSING else _copy(theirs)
    return entry


# --------------------------------------------------------------- helpers


def _as_dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def _get(container, key):
    if isinstance(container, dict) and key in container:
        return container[key]
    return _MISSING


def _key_order(ours, theirs) -> list:
    """Ours' order, then theirs-only keys in theirs' order — deterministic, and
    stable enough that a tagged manifest round-trips byte-identically."""
    ours, theirs = _as_dict(ours), _as_dict(theirs)
    return list(ours) + [k for k in theirs if k not in ours]


def _by_id(value):
    if value is _MISSING:
        return _MISSING
    out = {}
    for entry in value:
        out.setdefault(entry["id"], entry)
    return out


def _keyed(base, ours, theirs) -> bool:
    """Mergeable key-wise: at least one of ours/theirs present, all present
    values dicts. Otherwise the section falls back to a whole-value merge."""
    if ours is _MISSING and theirs is _MISSING:
        return False
    return all(
        isinstance(v, dict)
        for v in (base, ours, theirs)
        if v is not _MISSING
    )


def _entry_list(base, ours, theirs) -> bool:
    if ours is _MISSING and theirs is _MISSING:
        return False
    for value in (base, ours, theirs):
        if value is _MISSING:
            continue
        if not isinstance(value, list):
            return False
        if not all(
            isinstance(e, dict) and isinstance(e.get("id"), str) for e in value
        ):
            return False
    return True


def _norm(value) -> str:
    if value is _MISSING:
        return "\0missing"
    # the type prefix keeps 6 and 6.0 (and True and 1) distinct values
    return f"{type(value).__name__}:{json.dumps(value, sort_keys=True, default=repr)}"


def _same(a, b) -> bool:
    return _norm(a) == _norm(b)


def _copy(value):
    return value if value is _MISSING else copy.deepcopy(value)


# --------------------------------------------------------- choice writing


def _choice_value(key, conflict, choice) -> tuple[bool, object]:
    if not isinstance(choice, dict):
        raise ValidationError(
            f"choice for {key!r} must be an object "
            "{'take': 'ours'|'theirs'|'base'} or {'value': …}"
        )
    if "value" in choice:
        return True, copy.deepcopy(choice["value"])
    side = choice.get("take")
    if side not in ("ours", "theirs", "base"):
        raise ValidationError(
            f"choice for {key!r} must be {{'take': 'ours'|'theirs'|'base'}} "
            "or {'value': …}"
        )
    value = conflict.get(side)
    if value is None:  # that side deleted the key (or had no base)
        return False, None
    return True, copy.deepcopy(value)


def _write_key(manifest, key, value, present) -> None:
    segs = key.split(".")
    head = segs[0]
    if head == "parts" and len(segs) >= 2:
        _write_entry(
            manifest.setdefault("parts", []), segs[1], segs[2:],
            value, present, _PART_SUBDICTS, key,
        )
        return
    if head == "assembly" and len(segs) >= 3 and segs[1] == "instances":
        assembly = manifest.setdefault("assembly", {})
        _write_entry(
            assembly.setdefault("instances", []), segs[2], segs[3:],
            value, present, (), key,
        )
        return
    if head in ("assembly", "materials") and len(segs) == 2:
        _write_slot(manifest.setdefault(head, {}), segs[1], value, present)
        return
    _write_slot(manifest, key, value, present)


def _write_entry(seq, eid, rest, value, present, subdicts, key) -> None:
    index = next(
        (i for i, e in enumerate(seq) if isinstance(e, dict) and e.get("id") == eid),
        None,
    )
    if not rest:
        if not present:
            if index is not None:
                del seq[index]
        elif index is None:
            seq.append(value)  # restored entries land at the end
        else:
            seq[index] = value
        return
    if index is None:
        raise ValidationError(f"cannot resolve {key!r}: entry {eid!r} is not present")
    entry = seq[index]
    if len(rest) == 2 and rest[0] in subdicts:
        _write_slot(entry.setdefault(rest[0], {}), rest[1], value, present)
    else:
        _write_slot(entry, ".".join(rest), value, present)


def _write_slot(container, key, value, present) -> None:
    if present:
        container[key] = value
    else:
        container.pop(key, None)
