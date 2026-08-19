"""The BOM builder (PRD-015 FR1-3): a bill of materials rolled up from the
assembly, priced, and provenance-stamped — **pure Python, zero kernel calls,
and no OCP/build123d import**.

Two disciplines make the zero-kernel promise real (design Decision 1/2):

* **Count-only enumeration.** :func:`count_leaves` walks
  ``manifest["assembly"]["instances"]`` structurally. A pattern contributes
  ``count`` (its placement is irrelevant to a count), a sub-assembly recurses
  into the *source* project's instances multiplying multiplicity through, and a
  plain instance is one leaf. It never composes a transform, so it never calls
  ``kernel.request("resolve_assembly")`` the way ``mates.expand`` must — which
  is exactly why the BOM does not reuse ``_resolved_instances``.
* **Cached-metrics peek.** Mass is read from ``service._status`` /
  ``service._config_status`` **directly** (the way ``get_project`` does), never
  through ``_ensure_built`` / ``get_metrics`` — those build. Staleness is a pure
  ``_cache_key_for`` recomputation compared to the memoised key: no memo is
  ``unbuilt``, a key mismatch is ``stale``, a match is ``built``. The BOM
  renders regardless and names the unbuilt/stale parts in ``warnings``.

Determinism: lines are numbered by a sorted key ``(origin_project, part_id,
config)`` and totals are always summed over the flat grouping, so a flat and an
indented BOM of the same project carry byte-identical totals.
"""

from __future__ import annotations

from dataclasses import dataclass

from .model import NotFoundError, ValidationError

STRUCTURES = ("flat", "indented")


@dataclass
class Leaf:
    """One counted occurrence of a part in the assembly tree.

    ``multiplicity`` is the product of every pattern ``count`` and the number of
    sub-assembly levels above it; ``origin_project`` is the project whose script
    builds it (a screw inside sub-assembly B counts as B's part); ``level`` is
    the indent depth (0 = an instance of the root project).
    """

    origin_project: str
    part_id: str
    config: str | None
    multiplicity: int
    level: int
    path: str


# ------------------------------------------------------------ enumeration


def count_leaves(service, proj: str) -> list[Leaf]:
    """The count-only structural walk. Zero kernel calls.

    Patterns multiply by ``count``; sub-assemblies recurse into the source
    project's instances carrying multiplicity through; a cross-project cycle
    (identity is the canonical path, mirroring ``mates._expand_subassembly``)
    raises ``ValidationError`` with ``details.cycle``.
    """
    cpath = str(service.store.canonical_path_of(proj))
    out: list[Leaf] = []
    _walk(service, proj, 1, 0, "", [(proj, cpath)], out)
    return out


def _walk(service, proj, mult, level, prefix, stack, out):
    manifest = service.store.manifest(proj)
    assembly = manifest.get("assembly") or {}
    instances = assembly.get("instances") or []
    for inst in instances:
        if not isinstance(inst, dict):
            continue
        inst_id = inst.get("id")
        pattern = inst.get("pattern")
        count = (int(pattern["count"])
                 if isinstance(pattern, dict) and pattern.get("count") is not None
                 else 1)
        node_mult = mult * count
        node_path = f"{prefix}{inst_id}"
        ref = inst.get("assembly")
        if isinstance(ref, dict):
            source = _source_name(service, ref.get("project"))
            spath = str(service.store.canonical_path_of(source))
            if any(p == spath for _, p in stack):
                names = [n for n, _ in stack] + [source]
                raise ValidationError("assembly cycle: " + " -> ".join(names),
                                      {"cycle": names})
            _walk(service, source, node_mult, level + 1, node_path + "/",
                  stack + [(source, spath)], out)
        else:
            out.append(Leaf(origin_project=proj, part_id=inst.get("part"),
                            config=inst.get("config"), multiplicity=node_mult,
                            level=level, path=node_path))


def _source_name(service, ref) -> str:
    """A sub-assembly reference (a known project name or an absolute path) to a
    project name, opening an external directory READ-ONLY (mirrors
    ``mates._source_name`` — only read accessors ever touch the source)."""
    try:
        service.store.manifest(ref)
        return ref
    except NotFoundError:
        return service.store.open(ref)


# --------------------------------------------------------------- the BOM


def build_bom(service, proj: str, structure: str = "flat",
              config: str | None = None) -> dict:
    """Build the BOM for ``proj``.

    ``structure="flat"`` groups leaves by ``(origin_project, part_id, config)``
    and sums ``qty``; ``structure="indented"`` returns one line per occurrence
    in tree order, each carrying its ``level`` (the item ordinal is still the
    group's, so the two structures agree on numbering).

    ``config`` is an optional assembly-wide configuration: it is applied to a
    leaf only when the leaf's instance binds no configuration of its own AND the
    part actually declares ``config`` (an instance binding always wins, and a
    part that does not declare it is untouched).
    """
    if structure not in STRUCTURES:
        raise ValidationError(
            f"structure must be one of {STRUCTURES}", {"got": structure})

    leaves = count_leaves(service, proj)

    # Flat grouping drives numbering AND totals (so both structures agree).
    groups: dict[tuple, int] = {}
    for leaf in leaves:
        key = (leaf.origin_project, leaf.part_id,
               _effective_config(service, leaf, config))
        groups[key] = groups.get(key, 0) + leaf.multiplicity

    sorted_keys = sorted(groups, key=lambda k: (k[0], k[1], k[2] or ""))
    item_of = {key: i + 1 for i, key in enumerate(sorted_keys)}

    # Resolve each unique unit ONCE, in sorted order (deterministic warnings).
    unit: dict[tuple, dict] = {}
    warnings: list[dict] = []
    for key in sorted_keys:
        unit[key] = _unit_info(service, *key, warnings)

    if structure == "flat":
        lines = []
        for key in sorted_keys:
            line = _line(key, groups[key], unit[key])
            line["item"] = item_of[key]
            lines.append(line)
    else:
        lines = []
        for leaf in leaves:
            key = (leaf.origin_project, leaf.part_id,
                   _effective_config(service, leaf, config))
            line = _line(key, leaf.multiplicity, unit[key])
            line["item"] = item_of[key]
            line["level"] = leaf.level
            lines.append(line)

    return {
        "structure": structure,
        "lines": lines,
        "totals": _totals(groups, unit),
        "warnings": warnings,
    }


def _effective_config(service, leaf: Leaf, config_arg: str | None) -> str | None:
    """The configuration a leaf resolves under: its own instance binding, else
    the assembly-wide ``config_arg`` where the part declares it, else None."""
    if leaf.config is not None:
        return leaf.config
    if config_arg is None:
        return None
    try:
        record = service.store.get_part(leaf.origin_project, leaf.part_id)
    except Exception:  # noqa: BLE001 — a missing part is not this tool's error
        return None
    if record.kind == "script" and record.configs and config_arg in record.configs:
        return config_arg
    return None


def _line(key: tuple, qty: int, info: dict) -> dict:
    origin_project, part_id, config = key
    unit_cost = info["unit_cost_usd"]
    return {
        "item": None,                       # filled by the caller
        "origin_project": origin_project,
        "part_id": part_id,
        "part_number": info["part_number"],
        "label": info["label"],
        "config": config,
        "material": info["material"],
        "unit_mass_g": info["unit_mass_g"],
        "unit_cost_usd": unit_cost,
        "ext_cost_usd": (unit_cost * qty if unit_cost is not None else None),
        "qty": qty,
        "source": info["source"],
        "cost_source": info["cost_source"],
        "mass_source": info["mass_source"],
    }


def _totals(groups: dict[tuple, int], unit: dict[tuple, dict]) -> dict:
    """Summed over the flat grouping ALWAYS — so a flat and an indented BOM of
    the same project carry byte-identical totals (float addition is not
    associative, so per-occurrence summation would drift)."""
    mass = 0.0
    cost = 0.0
    for key, qty in groups.items():
        info = unit[key]
        if info["unit_mass_g"] is not None:
            mass += info["unit_mass_g"] * qty
        if info["unit_cost_usd"] is not None:
            cost += info["unit_cost_usd"] * qty
    return {"mass_g": mass, "cost_usd": cost}


# ----------------------------------------------------------- unit resolution


def _unit_info(service, origin_project, part_id, config, warnings) -> dict:
    """Everything a line needs about one unique ``(project, part, config)`` — the
    record's label/material, the peeked mass, the cost, and provenance."""
    record = service._record_for(origin_project, part_id, config)
    unit_mass_g, mass_source = _mass_for(service, origin_project, part_id, config)
    if mass_source in ("unbuilt", "stale"):
        warnings.append({
            "kind": f"mass_{mass_source}",
            "project": origin_project, "part": part_id, "config": config,
        })
    unit_cost, cost_source = _cost_for(service, origin_project, record,
                                       unit_mass_g)
    part_number, source = _provenance(service, origin_project, record)
    return {
        "label": record.label,
        "material": record.material,
        "unit_mass_g": unit_mass_g,
        "mass_source": mass_source,
        "unit_cost_usd": unit_cost,
        "cost_source": cost_source,
        "part_number": part_number,
        "source": source,
    }


def _mass_for(service, proj, part_id, config):
    """``(unit_mass_g, mass_source)`` read from the metrics memo WITHOUT building.

    Peeks ``_status`` (working state) or ``_config_status`` (a bound
    configuration) exactly as ``get_project`` reads a badge, and detects
    staleness by recomputing the pure ``_cache_key_for`` and comparing it to the
    memoised key.
    """
    record = service._record_for(proj, part_id, config)
    current_key = service._cache_key_for(proj, record)
    if config is None:
        memo = service._status.get(service._status_key(proj, part_id))
    else:
        memo = service._config_status.get(
            service._config_status_key(proj, part_id, config))
    if memo is None:
        return None, "unbuilt"
    metrics = memo.get("metrics") or {}
    mass = metrics.get("mass_g")
    if memo.get("cache_key") != current_key:
        # Last-known mass (if any) with an honest stale flag.
        return (mass if isinstance(mass, (int, float)) else None), "stale"
    if isinstance(mass, (int, float)):
        return mass, "built"
    # A memo that matches the key but carries no mass (an errored build).
    return None, "unbuilt"


def _cost_for(service, proj, record, unit_mass_g):
    """``(unit_cost_usd, cost_source)``: manual override wins, else a material
    estimate ``mass_g * cost_usd_kg / 1000`` when the material carries a cost,
    else none (design Decision 2 / FR3)."""
    bom = _bom_field(service, proj, record.id)
    manual = bom.get("unit_cost_usd")
    if isinstance(manual, (int, float)) and not isinstance(manual, bool):
        return float(manual), "manual"
    material = _material(service, proj, record.material)
    if (material is not None and material.cost_usd_kg is not None
            and unit_mass_g is not None):
        return unit_mass_g * material.cost_usd_kg / 1000.0, "material_estimate"
    return None, "none"


def _provenance(service, proj, record):
    """``(part_number, source)``.

    A manual ``bom`` field wins; a reference part shows its import ``source``; a
    package part with no override inherits ``part_number``/``url`` from its
    package's ``provenance.vendor`` (best-effort file I/O — degrades to blank).
    """
    bom = _bom_field(service, proj, record.id)
    part_number = bom.get("part_number")
    source = bom.get("url")

    if source is None and record.kind == "reference":
        source = record.source

    if part_number is None or source is None:
        vendor = _package_vendor(service, proj, record)
        if vendor:
            if part_number is None:
                part_number = vendor.get("part_number")
            if source is None:
                source = vendor.get("url")

    return (part_number if isinstance(part_number, str) else None,
            source if isinstance(source, str) else None)


def _bom_field(service, proj, part_id) -> dict:
    """The raw ``parts[i]["bom"]`` map, read directly (no ``PartRecord``
    change), or ``{}``."""
    manifest = service.store.manifest(proj)
    for entry in manifest.get("parts") or []:
        if isinstance(entry, dict) and entry.get("id") == part_id:
            bom = entry.get("bom")
            return bom if isinstance(bom, dict) else {}
    return {}


def _material(service, proj, material_id):
    """The full ``Material`` (for ``cost_usd_kg``): via the project-aware
    resolver when installed, else the builtin table. None on any failure."""
    if not material_id:
        return None
    resolver = getattr(service, "materials", None)
    resolve = getattr(resolver, "resolve", None)
    if callable(resolve):
        try:
            return resolve(proj, material_id)
        except Exception:  # noqa: BLE001 — a bad material never breaks the BOM
            return None
    try:
        from .materials import get_material
        return get_material(material_id)
    except Exception:  # noqa: BLE001
        return None


def _package_vendor(service, proj, record):
    """The ``provenance.vendor`` map of the package a part came from, or None.

    Best-effort: parses the ``# agentcad:package`` header off the script and
    reads the cached package's ``package.json``. Any failure (no header, cold
    cache, unreadable json) degrades to no inheritance. File I/O only.
    """
    if record.kind != "script":
        return None
    try:
        script = service.store.read_script(proj, record.id)
    except Exception:  # noqa: BLE001
        return None
    try:
        from .packages import provenance
        head = provenance.parse(script)
    except Exception:  # noqa: BLE001
        return None
    if not head:
        return None
    name, version = head.get("name"), head.get("version")
    if not (isinstance(name, str) and isinstance(version, str)):
        return None
    try:
        import json

        from .packages import cache as pkgcache
        manifest_path = pkgcache.version_dir(name, version) / "package.json"
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        vendor = (data.get("provenance") or {}).get("vendor")
        return vendor if isinstance(vendor, dict) else None
    except Exception:  # noqa: BLE001
        return None
