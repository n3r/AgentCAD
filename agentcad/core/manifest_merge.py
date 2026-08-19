"""Structure-aware three-way merge for project.json (pure Python, no I/O).

A line-wise merge of a JSON manifest produces garbage (or, worse, a *clean*
result that nobody authored). This module merges the manifest at CAD key
granularity instead — one independent decision per parameter, per part field,
per instance field, per material — and reports what it could not decide.

Key space (every leaf merges independently)::

    schema_version | name | units | <any other top-level key>   whole value
    parts.<id>                       entry add/remove, else field-wise:
      parts.<id>.label|material|kind|source                     whole value
      parts.<id>.active_config                                  whole value
      parts.<id>.params.<name>                                  per parameter
      parts.<id>.solid_materials.<key>                          per key
      parts.<id>.bom.<field>                                    per field
      parts.<id>.configs.<name>      entry add/remove, else field-wise:
        …<name>.label|description                               whole value
        …<name>.params.<param>                                  per parameter
      parts.<id>.pmi                                            atomic
    assembly.instances.<id>          entry add/remove, else field-wise:
      …<id>.part|position|rotation_deg|color|mate|config        whole value
    materials.<id>                                              atomic
    packages.<name>                                             atomic
    packages_lock.<name>                                        atomic
    releases.<rev>                                              atomic

``pmi`` is atomic because its frames cross-reference each other by id;
``materials.<id>`` because merging one side's density with the other's yield
strength is over-clever; ``position``/``rotation_deg`` because merging X from
one side and Z from the other yields a placement nobody authored; and the two
package maps (PRD-011) because merging one side's ``version`` with the other's
``content_id`` yields a lock entry nobody authored and that verifies against
nothing. Per *name* they merge key-wise, so two branches adding two different
packages merge clean — which is the whole reason the maps carry only
content-determined values.

``parts.<id>.configs`` (PRD-012) goes the other way, and the contrast is the
argument: a lock entry is *content-determined*, so half of one verifies against
nothing, while a configuration is a **set of independent parameter values** —
exactly what makes ``parts.<id>.params`` merge per key, one level deeper. So
the map merges per NAME (two branches adding two different configurations of
one part merge clean, FR12) and a configuration present on all three sides
merges per FIELD, with its ``params`` per parameter. Add/add of the same name
and delete/modify still conflict on the whole configuration, and a map entry
that is not a dict (a hand edit, an authored null) merges whole rather than
being silently rewritten to ``{}``. ``active_config`` and an instance's
``config`` are single selections, so they are whole values.

Values compare by type *and* JSON shape, so ``6`` and ``6.0`` are different
values — matching how params are stored and how a byte comparison of
project.json would see them.

Two encodings in the conflict payload are load-bearing:

* **absence is not null.** A side that has no such key OMITS its entry
  (``"ours"`` simply is not there); a side that authored a JSON ``null``
  reports ``null``. Conflating them made ``take: "ours"`` on an authored null
  delete the key.
* **the key is a display string, the path is the truth.** ``key`` is the
  dotted address a caller resolves against, but ids may contain dots
  (``solid_materials.wall.inner``), so every conflict also carries ``path``:
  the exact segments :func:`apply_choices` writes through. Nothing re-splits
  a key on ``.``.

Referential integrity is deliberately not this module's job: one side deleting
a part while the other adds an instance of it touches no common key, so it
merges clean. The kernel validation pass is the backstop.
"""

from __future__ import annotations

import copy
import json

from .model import ValidationError

CONFLICT_KEYS = ("kind", "key", "path", "base", "ours", "theirs")

_MISSING = object()

_PART_SUBDICTS = ("params", "solid_materials", "bom")

# Part fields that are a MAP of name -> entry, each entry merged field-wise
# with the listed fields merged key-wise (PRD-012 configs).
_PART_ENTRY_DICTS = {"configs": ("params",)}

# Top-level maps whose ENTRIES merge key-wise and are themselves atomic.
# ``_write_path`` has to know the same set (it reads ``*_ENTRY_DICTS`` at its
# len==2 branch), or a resolution writes a bogus flat key
# (``"packages.iso4762"``) instead of into the map.
#
# ``releases`` (PRD-015 FR6) joins them: a release record is a coherent unit
# keyed by its revision letter, exactly like a material or a package entry. Two
# branches cutting DIFFERENT revs (A and B) merge clean; a same-rev edit
# conflicts on the whole record — which is what "append-only, never rewrites"
# means at merge granularity.
_ENTRY_DICTS = ("materials", "packages", "packages_lock", "releases")

# `assembly` sub-maps (PRD-013) whose ENTRIES merge key-wise and are atomic per
# name — exactly the `_ENTRY_DICTS` discipline, one level down. An interface
# export or a coupling is a small content-determined record; merging one side's
# `instance` with another's `connector` is a record nobody authored, so two
# branches adding DIFFERENT names merge clean and a same-name edit conflicts on
# the whole record. `_write_path` must know the same set or a resolution of
# `assembly.interface.<name>` falls through to a bogus flat key.
_ASSEMBLY_ENTRY_DICTS = ("interface", "couplings")


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


def conflict_path(conflict: dict) -> tuple:
    """The exact segments a conflict addresses.

    Recorded by the merge; the dotted ``key`` is only its display form, and an
    id containing a dot makes the two disagree.
    """
    path = conflict.get("path")
    if isinstance(path, (list, tuple)) and path:
        return tuple(str(seg) for seg in path)
    return tuple(str(conflict.get("key", "")).split("."))


def apply_choices(
    merged: dict, conflicts: list[dict], choices: dict
) -> tuple[dict, list[dict]]:
    """Apply resolutions to a merged manifest.

    ``choices`` maps a conflict key to ``{"take": "ours"|"theirs"|"base"}`` or
    ``{"value": <any>}``. Returns ``(new manifest, still-outstanding
    conflicts)``; neither argument is mutated. An unknown key or choice shape
    raises ``ValidationError``.

    ``take`` reads the chosen side off the recorded conflict entry: a side the
    entry does not carry has no value there, so taking it REMOVES the key,
    while a side carrying ``null`` writes that null.
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
        # Through the RECORDED segments, never by re-splitting the key: an id
        # with a dot in it would otherwise land in a bogus flat field.
        _write_path(result, conflict_path(conflict), value, present, key)
    return result, [c for c in conflicts if c["key"] not in choices]


# ------------------------------------------------------------- merge core


def _merge_section(key, base, ours, theirs, conflicts):
    if key == "parts" and _entry_list(base, ours, theirs):
        return _merge_entry_list(("parts",), base, ours, theirs, conflicts,
                                 subdicts=_PART_SUBDICTS,
                                 entry_dicts=_PART_ENTRY_DICTS)
    if key == "assembly" and _keyed(base, ours, theirs):
        return _merge_assembly(base, ours, theirs, conflicts)
    if key in _ENTRY_DICTS and _keyed(base, ours, theirs):
        return _merge_entry_dict((key,), base, ours, theirs, conflicts)
    return _merge_atomic((key,), base, ours, theirs, conflicts)


def _merge_assembly(base, ours, theirs, conflicts):
    result = {}
    for sub in _key_order(_as_dict(ours), _as_dict(theirs)):
        b, o, t = _get(base, sub), _get(ours, sub), _get(theirs, sub)
        if sub == "instances" and _entry_list(b, o, t):
            # An instance has neither kind of sub-map: every field of it is a
            # whole value (the docstring's table), and the two descriptors are
            # threaded rather than read as globals so it stays that way.
            value = _merge_entry_list(
                ("assembly", "instances"), b, o, t, conflicts, subdicts=(),
                entry_dicts={},
            )
        elif sub in _ASSEMBLY_ENTRY_DICTS and _keyed(b, o, t):
            # interface/couplings: a map of NAME -> atomic record (PRD-013).
            value = _merge_entry_dict(("assembly", sub), b, o, t, conflicts)
        else:
            value = _merge_atomic(("assembly", sub), b, o, t, conflicts)
        if value is not _MISSING:
            result[sub] = value
    return result


def _merge_entry_list(prefix, base, ours, theirs, conflicts, *, subdicts,
                      entry_dicts):
    b, o, t = _by_id(base), _by_id(ours), _by_id(theirs)
    result = []
    for eid in _key_order(o, t):
        value = _merge_entry(
            (*prefix, eid),
            _get(b, eid),
            _get(o, eid),
            _get(t, eid),
            conflicts,
            subdicts,
            entry_dicts,
        )
        if value is not _MISSING:
            result.append(value)
    return result


def _merge_entry_dict(prefix, base, ours, theirs, conflicts):
    """Sections whose entries are atomic (materials)."""
    result = {}
    for eid in _key_order(_as_dict(ours), _as_dict(theirs)):
        value = _merge_atomic(
            (*prefix, eid), _get(base, eid), _get(ours, eid),
            _get(theirs, eid), conflicts,
        )
        if value is not _MISSING:
            result[eid] = value
    return result


def _merge_entry(segs, base, ours, theirs, conflicts, subdicts, entry_dicts):
    """One list entry: field-wise when it exists on all three sides, whole-value
    otherwise (add/add, delete/modify — the entry is the conflict unit).

    ``subdicts`` and ``entry_dicts`` are the caller's descriptors, never module
    globals: which fields of *this* kind of entry are a scalar map and which are
    a map of sub-entries is a property of the entry, and an instance has
    neither.
    """
    if _MISSING in (base, ours, theirs):
        return _merge_atomic(segs, base, ours, theirs, conflicts)
    result = {}
    for field in _key_order(_as_dict(ours), _as_dict(theirs)):
        b, o, t = _get(base, field), _get(ours, field), _get(theirs, field)
        if field in subdicts and _keyed(b, o, t):
            value = _merge_scalar_dict((*segs, field), b, o, t, conflicts)
        elif field in entry_dicts and _keyed(b, o, t):
            value = _merge_keyed_entries(
                (*segs, field), b, o, t, conflicts,
                subdicts=entry_dicts[field],
            )
        else:
            value = _merge_atomic((*segs, field), b, o, t, conflicts)
        if value is not _MISSING:
            result[field] = value
    return result


def _merge_keyed_entries(prefix, base, ours, theirs, conflicts, *, subdicts):
    """A map of NAME -> entry (parts.<id>.configs): per name, then per field,
    with ``subdicts`` (``params``) merged per parameter."""
    result = {}
    for name in _key_order(_as_dict(ours), _as_dict(theirs)):
        b, o, t = _get(base, name), _get(ours, name), _get(theirs, name)
        # `_entry_list`'s guard, at map level: a non-dict entry (a hand-edited
        # `"m": 5`, an authored null) must merge WHOLE, or `_merge_entry`'s
        # `_as_dict` silently rewrites it to `{}` — a clean merge that loses data.
        if _keyed(b, o, t):
            # `entry_dicts={}`: a configuration has no map of sub-entries of its
            # own, so the recursion is exactly one level deep.
            value = _merge_entry((*prefix, name), b, o, t, conflicts, subdicts,
                                 {})
        else:
            value = _merge_atomic((*prefix, name), b, o, t, conflicts)
        if value is not _MISSING:
            result[name] = value
    return result


def _merge_scalar_dict(prefix, base, ours, theirs, conflicts):
    result = {}
    for name in _key_order(_as_dict(ours), _as_dict(theirs)):
        value = _merge_atomic(
            (*prefix, name), _get(base, name), _get(ours, name),
            _get(theirs, name), conflicts,
        )
        if value is not _MISSING:
            result[name] = value
    return result


def _merge_atomic(segs, base, ours, theirs, conflicts):
    """The classic three-way truth table over one whole value. ``_MISSING`` on a
    side means the key is absent there (never added, or deleted)."""
    if _same(ours, theirs):
        return _copy(ours)
    if _same(base, ours):  # only theirs moved
        return _copy(theirs)
    if _same(base, theirs):  # only ours moved
        return _copy(ours)
    conflicts.append(_conflict(segs, base, ours, theirs))
    return _copy(ours)


def _conflict(segs, base, ours, theirs) -> dict:
    """One conflict entry.

    A side with no such key is OMITTED — absence and an authored ``null`` are
    different answers, and ``take`` must be able to tell them apart. ``path``
    carries the exact segments; ``key`` is their dotted display form.
    """
    entry = {"kind": "manifest", "key": ".".join(segs), "path": list(segs)}
    for name, value in (("base", base), ("ours", ours), ("theirs", theirs)):
        if value is not _MISSING:
            entry[name] = _copy(value)
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
    if side not in conflict:
        # That side has no such key — it deleted it, never had it, or (for
        # "base") both branches added it. Taking that side removes the key.
        return False, None
    return True, copy.deepcopy(conflict[side])


def _write_path(manifest, segs, value, present, key) -> None:
    head = segs[0]
    if head == "parts" and len(segs) >= 2:
        _write_entry(
            manifest.setdefault("parts", []), segs[1], segs[2:],
            value, present, _PART_SUBDICTS, _PART_ENTRY_DICTS, key,
        )
        return
    if head == "assembly" and len(segs) >= 3 and segs[1] == "instances":
        assembly = manifest.setdefault("assembly", {})
        # `{}`: the merge writes no sub-map inside an instance, so neither does
        # a resolution — the two descriptors travel together (see `_merge_entry`).
        _write_entry(
            assembly.setdefault("instances", []), segs[2], segs[3:],
            value, present, (), {}, key,
        )
        return
    if (head == "assembly" and len(segs) == 3
            and segs[1] in _ASSEMBLY_ENTRY_DICTS):
        # assembly.interface.<name> / assembly.couplings.<name>: into the map,
        # never a flat "assembly.interface.name" key (the module's docstring
        # warning) — the per-name-atomic sibling of the packages branch.
        assembly = manifest.setdefault("assembly", {})
        _write_slot(assembly.setdefault(segs[1], {}), segs[2], value, present)
        return
    if head in ("assembly", *_ENTRY_DICTS) and len(segs) == 2:
        _write_slot(manifest.setdefault(head, {}), segs[1], value, present)
        return
    _write_slot(manifest, head if len(segs) == 1 else ".".join(segs),
                value, present)


def _write_entry(seq, eid, rest, value, present, subdicts, entry_dicts,
                 key) -> None:
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
    elif len(rest) >= 2 and rest[0] in entry_dicts:
        _write_keyed_entry(entry.setdefault(rest[0], {}), rest[1], rest[2:],
                           value, present, entry_dicts[rest[0]], key)
    else:
        _write_slot(entry, rest[0] if len(rest) == 1 else ".".join(rest),
                    value, present)


def _write_keyed_entry(container, name, rest, value, present, subdicts, key):
    """Into parts.<id>.configs.<name>[.field[.param]] — through the recorded
    segments, never a dotted flat key (a config name may contain '-').

    The merge records exactly two shapes inside a configuration: a whole field
    (``rest`` is one segment) and a parameter of a ``subdicts`` map (two, the
    first being ``params``). Anything deeper cannot come from a conflict this
    module produced, so it is **refused** rather than joined into a flat
    ``"params.a.b"`` key — writing a key nobody can read back is the failure
    this whole function exists to prevent.
    """
    if not rest:
        _write_slot(container, name, value, present)
        return
    inner = container.get(name)
    if not isinstance(inner, dict):
        raise ValidationError(
            f"cannot resolve {key!r}: configuration {name!r} is not present")
    if len(rest) == 1:
        _write_slot(inner, rest[0], value, present)
    elif len(rest) == 2 and rest[0] in subdicts:
        _write_slot(inner.setdefault(rest[0], {}), rest[1], value, present)
    else:
        raise ValidationError(
            f"cannot resolve {key!r}: {'.'.join(rest)} is not addressable "
            f"inside configuration {name!r} (a field, or "
            f"{'|'.join(subdicts)}.<name>)")


def _write_slot(container, key, value, present) -> None:
    if present:
        container[key] = value
    else:
        container.pop(key, None)


def package_problems(manifest: dict) -> list[dict]:
    """Post-merge damage the key-wise merge cannot see: a **hybrid dependency**.

    ``packages`` and ``packages_lock`` merge as two independent maps, each
    atomic *per package name* — which is right, and not enough. The two maps
    are one fact in two halves: `packages.foo` is what was asked for and
    `packages_lock.foo` is what that request resolved to. Resolving the
    requirement from **theirs** and the lock from **ours** is a clean merge of
    each map and a dependency **no branch ever authored** — a `^2.0.0`
    requirement pinned to a `1.0.0` lock, or a declaration pinning `corp` over
    a lock that came from `agentcad-core`. Nothing downstream catches it:
    `use_part` reads only the lock, and the lock verifies against its own
    content id perfectly well.

    So it is checked where PRD-001 already checks a merged manifest for
    structural damage — `merge._integrity`'s call site — and it returns the
    same problem shape, so a violation surfaces as an ordinary merge failure
    rather than a new mechanism. Silent is the one thing it must not be.

    Reported, per package:

    ``package_requirement_violated``
        the locked version does not satisfy the declared requirement.
    ``package_index_mismatch``
        the declaration pins an index the lock entry did not come from.
    ``package_lock_orphan``
        a lock entry whose declaration is gone — one side removed the package
        and the other side's lock survived.
    """
    from .packages import format as pkgformat

    declared = manifest.get("packages") if isinstance(manifest, dict) else None
    locked = manifest.get("packages_lock") if isinstance(manifest, dict) else None
    declared = declared if isinstance(declared, dict) else {}
    locked = locked if isinstance(locked, dict) else {}

    problems: list[dict] = []
    for name in sorted(locked):
        entry = locked.get(name)
        if not isinstance(entry, dict):
            continue
        request = declared.get(name)
        if not isinstance(request, dict):
            problems.append({
                "kind": "package_lock_orphan", "package": name,
                "message": f"packages_lock.{name} survived a merge in which "
                           f"packages.{name} was removed: the project is "
                           f"locked to a dependency it no longer declares",
            })
            continue
        version = entry.get("version")
        requirement = request.get("version_req") or "*"
        try:
            satisfied = (isinstance(version, str)
                         and pkgformat.satisfies(version, requirement))
        except ValidationError:
            satisfied = False
        if not satisfied:
            problems.append({
                "kind": "package_requirement_violated", "package": name,
                "version": version, "version_req": requirement,
                "message": f"packages.{name} asks for {requirement} and "
                           f"packages_lock.{name} holds {version}: the two "
                           f"halves came from different branches, so this is a "
                           f"dependency nobody authored — re-resolve it with "
                           f"add_package",
            })
        pinned, origin = request.get("index"), entry.get("index")
        if pinned and origin and pinned != origin:
            problems.append({
                "kind": "package_index_mismatch", "package": name,
                "index": origin, "declared_index": pinned,
                "message": f"packages.{name} pins index {pinned!r} and "
                           f"packages_lock.{name} was resolved from "
                           f"{origin!r}: a pin is a statement about "
                           f"provenance, and this merge silently changed it",
            })
    return problems


def config_problems(manifest: dict) -> list[dict]:
    """Post-merge damage the key-wise merge cannot see: a **dangling selection**.

    ``parts.<id>.configs`` merges per name and a *selection* of one of those
    names — ``parts.<id>.active_config``, ``assembly.instances.<id>.config`` —
    is a whole value in another key. One branch removing a configuration while
    the other selects it touches no common key, so the merge is clean by design
    and the selection now names nothing. It is the shape of damage
    :func:`package_problems` exists for, and the tool choke points cannot see
    it: each side was valid where it was written.

    Three kinds, and the difference is deliberate (Decision 9):

    ``dangling_instance_config``
        an instance bound to a configuration the merged part no longer
        declares. **Blocking**, like ``dangling_instance``: the binding is the
        instance's whole parameter set, so it resolves to nothing.
    ``dangling_active_config``
        a part whose ``active_config`` is gone. A **warning**: an unknown
        active configuration resolves as base (Decision 3), so the project is
        loadable and someone only has to re-pick.
    ``malformed_configuration``
        a *member* that is not an object with a ``params`` map. The same
        clean-merge damage one level down: ``_merge_entry`` takes a non-object
        configuration entry **whole**, so one branch replacing ``m`` with a
        null (or a scalar) is enough. A **warning** for the same reason as the
        one above — ``PartRecord.config_params`` resolves it as an empty
        configuration, so the project loads; what it must not do is load
        *silently*, because the member now holds no parameters at all.

    Silent on a project with no configurations, on ``{}``, and on a healthy
    family. An instance whose *part* is missing is skipped —
    ``merge._integrity``'s ``dangling_instance`` already says so, and saying it
    twice in two vocabularies is worse than saying it once.
    """
    parts: dict[str, dict] = {}
    entries = manifest.get("parts") if isinstance(manifest, dict) else None
    for entry in entries if isinstance(entries, list) else []:
        if isinstance(entry, dict) and isinstance(entry.get("id"), str):
            parts.setdefault(entry["id"], entry)

    problems: list[dict] = []
    for pid, entry in parts.items():
        for name, member in _declared(entry).items():
            # `{"params": {}}` is a legitimate configuration (defaults,
            # nothing overridden) and must not be reported; a member with no
            # params map at all is the damage.
            if isinstance(member, dict) and isinstance(member.get("params"),
                                                       dict):
                continue
            problems.append({
                "kind": "malformed_configuration", "part": pid,
                "config": name,
                "message": f"part {pid!r} declares a configuration {name!r} "
                           f"that is not an object with a params map, so it "
                           f"holds no parameters and resolves as the part's "
                           f"base: the key-wise merge takes a non-object "
                           f"configuration entry whole, so one branch "
                           f"replacing the member is enough — re-declare it "
                           f"with set_part_configs or remove it",
            })
        active = entry.get("active_config")
        if isinstance(active, str) and active not in _declared(entry):
            problems.append({
                "kind": "dangling_active_config", "part": pid,
                "config": active,
                "message": f"part {pid!r} has active_config {active!r}, which "
                           f"the merged project no longer declares: one branch "
                           f"removed the configuration and the other left it "
                           f"selected, so the part resolves to its base "
                           f"parameters until someone re-picks",
            })

    assembly = manifest.get("assembly") if isinstance(manifest, dict) else None
    instances = assembly.get("instances") if isinstance(assembly, dict) else None
    for inst in instances if isinstance(instances, list) else []:
        if not isinstance(inst, dict):
            continue
        name = inst.get("config")
        if not isinstance(name, str):
            continue
        entry = parts.get(inst.get("part"))
        if entry is None:
            continue  # `dangling_instance` already reports the missing part
        if name not in _declared(entry):
            problems.append({
                "kind": "dangling_instance_config",
                "instance": inst.get("id"), "part": inst.get("part"),
                "config": name,
                "message": f"instance {inst.get('id')!r} is bound to "
                           f"configuration {name!r} of part "
                           f"{inst.get('part')!r}, which the merged project no "
                           f"longer declares: one branch removed the "
                           f"configuration and the other kept the binding, so "
                           f"this instance resolves to nothing — re-bind it "
                           f"with set_instance_config or restore the "
                           f"configuration",
            })
    return problems


def structure_problems(manifest: dict) -> list[dict]:
    """Post-merge referential damage in the PRD-013 assembly maps (Decision 1.5).

    The key-wise merge of ``assembly.interface`` / ``assembly.couplings`` is
    correct but blind to cross-key references: one branch deleting an instance
    while the other exports (or couples) it touches no common key, so the merge
    is clean and the export now names nothing. Both kinds are **warnings** —
    the export/coupling resolves to nothing but the project still loads (the
    PRD-012 blocking/warning split; a pattern is one instance field and is
    covered by the ordinary instance merge, so there is no ``pattern_conflict``).

    Silent on a project with no interface/couplings, on ``{}``, and on a healthy
    assembly.
    """
    assembly = manifest.get("assembly") if isinstance(manifest, dict) else None
    if not isinstance(assembly, dict):
        return []
    instances = assembly.get("instances")
    ids = {
        i.get("id") for i in (instances if isinstance(instances, list) else [])
        if isinstance(i, dict)
    }
    problems: list[dict] = []

    interface = assembly.get("interface")
    for name, spec in (interface.items()
                       if isinstance(interface, dict) else []):
        inst = spec.get("instance") if isinstance(spec, dict) else None
        if inst not in ids:
            problems.append({
                "kind": "dangling_interface", "interface": name,
                "instance": inst,
                "message": f"interface export {name!r} names instance "
                           f"{inst!r}, which the merged project no longer has: "
                           f"one branch removed the instance and the other kept "
                           f"the export, so it resolves to nothing — re-point "
                           f"or remove it with set_assembly_interface",
            })

    couplings = assembly.get("couplings")
    for name, spec in (couplings.items()
                       if isinstance(couplings, dict) else []):
        if not isinstance(spec, dict):
            continue
        for side in ("a_instance", "b_instance"):
            inst = spec.get(side)
            if inst is not None and inst not in ids:
                problems.append({
                    "kind": "dangling_coupling", "coupling": name,
                    "instance": inst,
                    "message": f"coupling {name!r} names instance {inst!r} "
                               f"({side}), which the merged project no longer "
                               f"has: re-point or remove it",
                })
    return problems


def _declared(entry: dict) -> dict:
    configs = entry.get("configs")
    return configs if isinstance(configs, dict) else {}
