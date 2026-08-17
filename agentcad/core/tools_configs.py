"""Tool pack: configurations (PRD-012).

Five tools — ``set_part_configs``, ``list_configs``, ``build_configs``,
``set_active_config``, ``set_instance_config`` — each validating before it
writes, publishing ``project_changed`` exactly once *after* its write (which is
what makes the history snapshot and the undo step land on the new state), and
returning post-state rather than a bare OK.

The pack sorts at ``con``, before ``drawing`` / ``holes`` / ``packages`` /
``proposals`` / ``specs`` / ``vision``: at ``register()`` time
``service.specs``, ``service.packages`` and ``service.gate_providers`` do not
exist yet, so nothing here reads a later pack's seam at registration (and
nothing ever appends to ``gate_providers`` — ``tools_proposals`` resets it
unconditionally, the ``tools_run_checks`` trap).

Word choice: the object is a **configuration**, the field is ``configs``, and
``preset`` names only a configuration a *package* publishes.
"""

from __future__ import annotations

from . import locks
from .model import ConflictError, NotFoundError, ValidationError
from .packages import format as pkgformat
from .packages.manager import manifest_scope
from .service import _divergence
from .tools import Tool, schema, with_hint

_NO_SCRIPT = ("cannot set configurations: the part script does not currently "
              "load — fix the script first (see get_part.status)")


def register(registry, service) -> None:
    def _spec_for(project: str, part_id: str):
        """The stored record and its PARAMS spec, or a refusal.

        A reference part has no parameters at all, and a script that does not
        load cannot be checked against anything — a family validated against a
        spec we could not read would be persisted unchecked, which is exactly
        what `set_params` refuses (and with this wording)."""
        record = service.store.get_part(project, part_id)
        if record.kind != "script":
            raise ValidationError(
                f"part {part_id!r} is a reference/imported part and has no "
                "parameters to configure"
            )
        spec = service._params_spec(service.store.read_script(project, part_id))
        if spec is None:
            raise ValidationError(_NO_SCRIPT)
        return record, spec

    def _referrers(project: str, part_id: str, names) -> dict[str, list[str]]:
        """``{config name: [instance ids]}`` for the names bound by an assembly
        instance of ``part_id``. Empty ``names`` costs no manifest read."""
        out: dict[str, list[str]] = {}
        if not names:
            return out
        for inst in service.store.instances(project):
            if inst.part == part_id and inst.config in names:
                out.setdefault(inst.config, []).append(inst.id)
        return out

    def _named(project: str, part_id: str, config):
        """The record, with ``config`` checked for membership (``None`` = base)."""
        record = service.store.get_part(project, part_id)
        if config is None:
            return record
        if not isinstance(config, str):
            raise ValidationError(
                "config must be a configuration name (a string)",
                {"got": type(config).__name__},
            )
        declared = record.configs or {}
        if config not in declared:
            raise ValidationError(
                f"part {part_id!r} declares no configuration {config!r} "
                f"(declares {sorted(declared)})",
                {"declared": sorted(declared)},
            )
        return record

    # ------------------------------------------------------ set_part_configs

    def set_part_configs(project: str, part_id: str, configs: dict) -> dict:
        if not isinstance(configs, dict):
            raise ValidationError(
                "configs must be an object of name -> configuration")
        record, spec = _spec_for(project, part_id)
        problems = pkgformat.validate_configurations(configs, spec)
        if problems:
            raise ValidationError(
                "invalid configurations: "
                + "; ".join(p["message"] for p in problems),
                {"problems": problems, "part_id": part_id},
            )
        # Normalized on write (int/float coercion, enum canonicalization), so
        # {"n": 3} and {"n": 3.0} are one configuration and one cache key.
        # A comprehension over `configs.items()` preserves the caller's order —
        # a family is not a lockfile, and this order is what the switcher and
        # the dimension table display.
        normalized = {
            name: {**entry,
                   "params": service.normalize_params(spec, entry["params"])}
            for name, entry in configs.items()
        }

        # FR11: a full replace may not orphan a binding. Both referrer kinds
        # are reported at once (the `remove_part` payload shape) — a caller
        # fixing one at a time would otherwise pay two round trips.
        removed = set(record.configs or {}) - set(normalized)
        bound = _referrers(project, part_id, removed)
        active_hit = bool(record.active_config) and record.active_config in removed
        if bound or active_hit:
            names = sorted(
                set(bound) | ({record.active_config} if active_hit else set())
            )
            reasons = [f"{name} by instance(s) {', '.join(ids)}"
                       for name, ids in sorted(bound.items())]
            if active_hit:
                reasons.append("active_config")
            raise ConflictError(
                f"configuration(s) {', '.join(names)} of part {part_id!r} are "
                "referenced: " + "; ".join(reasons),
                {"part": part_id,
                 "configs": names,
                 "instances": sorted({i for ids in bound.values() for i in ids}),
                 "active_config": active_hit},
            )

        with manifest_scope(service.store, project), locks.write_scope(part_id):
            # An emptied map POPS the key (G5: a part that no longer has a
            # family is byte-identical to one that never had one).
            service.store.update_part_entry(project, part_id,
                                            configs=normalized)
        service.bus.publish({"type": "project_changed", "project": project,
                             "part": part_id, "reason": "configs"})

        out = {"part_id": part_id, "configs": normalized,
               "active_config": record.active_config}
        active = record.active_config
        if active and active in normalized and \
                normalized[active]["params"] != \
                (record.configs or {}).get(active, {}).get("params"):
            # The visible geometry moved, so the post-state a caller needs next
            # is the build. Editing a member nobody activated costs nothing.
            out["rebuild"] = with_hint(service._rebuild(project, part_id))
        return out

    # ---------------------------------------------------------- list_configs

    def list_configs(project: str, part_id: str | None = None) -> dict:
        if part_id is not None:
            records = [service.store.get_part(project, part_id)]
        else:
            # Project-wide: the configured parts, and only those — an
            # unconfigured part is `configs: {}` on `get_part`, not a row here.
            records = [
                service.store.get_part(project, entry["id"])
                for entry in service.store.manifest(project)["parts"]
                if entry.get("configs")
            ]
        instances = service.store.instances(project)
        parts = []
        for record in records:
            referrers: dict[str, list[str]] = {}
            for inst in instances:
                if inst.part == record.id and inst.config:
                    referrers.setdefault(inst.config, []).append(inst.id)
            diverged, diverged_params = _divergence(record)
            parts.append({
                "part_id": record.id,
                "configs": record.configs or {},
                "active_config": record.active_config,
                "diverged": diverged,
                "diverged_params": diverged_params,
                "referrers": referrers,
            })
        return {"parts": parts}

    # --------------------------------------------------------- build_configs

    def _rows(project: str, record, wanted) -> list[dict]:
        """One row per requested configuration, in family order.

        Serial and de-duplicated by cache key: every requested member's pure
        key is computed first, each distinct key is built once, and the row is
        fanned back out across the names that share it. A fan-out over the
        kernel pool was measured at 1.08x-1.40x against a pre-registered 1.5x
        bar in PRD-011 and deleted; two configurations sharing a key would also
        race the worker's fixed-name staging file.
        """
        declared = record.configs or {}
        names = [n for n in declared if wanted is None or n in wanted]
        cache = service.store.cache_dir(project)
        keys = {
            name: service._cache_key_for(
                project, service._record_for(project, record.id, name))
            for name in names
        }
        groups: dict[str, list[str]] = {}
        for name in names:
            groups.setdefault(keys[name], []).append(name)

        built: dict[str, tuple[dict, bool]] = {}
        for key, group in groups.items():
            # `cached` is measured, not assumed: the artifacts of this key
            # either existed before we asked (a memo or sidecar hit) or this
            # call produced them.
            cached = (cache / f"{key}.acm").is_file() and \
                (cache / f"{key}.metrics.json").is_file()
            result = service._ensure_config_built(project, record.id, group[0])
            built[group[0]] = (result, cached)
            for name in group[1:]:
                built[name] = (result, True)

        rows = []
        for name in names:
            result, cached = built[name]
            entry = declared[name] if isinstance(declared[name], dict) else {}
            row = {
                "name": name,
                "label": entry.get("label"),
                "ok": bool(result.get("ok")),
                "cached": cached,
                # A failed build returns no key; the row still carries the one
                # its params hash to, so a caller can correlate the failure.
                "cache_key": result.get("cache_key") or keys[name],
                "metrics": result.get("metrics"),
                "warnings": result.get("warnings") or [],
            }
            if not row["ok"]:
                row["error"] = result.get("error")
            rows.append(row)
        return rows

    def _refuse_unknown(subject: str, declared: dict, wanted) -> None:
        unknown = [name for name in (wanted or []) if name not in declared]
        if unknown:
            raise ValidationError(
                f"{subject} declares no configuration(s): "
                f"{', '.join(unknown)}",
                {"unknown": unknown, "declared": sorted(declared)},
            )

    def build_configs(project: str, part_id: str | None = None,
                      configs: list | None = None) -> dict:
        wanted = None
        if configs is not None:
            if not isinstance(configs, list) or \
                    any(not isinstance(name, str) for name in configs):
                raise ValidationError(
                    "configs must be a list of configuration names")
            wanted = list(configs)

        if part_id is not None:
            record = service.store.get_part(project, part_id)
            _refuse_unknown(f"part {part_id!r}", record.configs or {}, wanted)
            return {"part_id": part_id, "configs": _rows(project, record, wanted)}

        records = [
            service.store.get_part(project, entry["id"])
            for entry in service.store.manifest(project)["parts"]
            if entry.get("configs") and entry.get("kind", "script") == "script"
        ]
        if not records:
            return {"parts": [], "warnings": ["no configured parts"]}
        declared: dict = {}
        for record in records:
            declared.update(record.configs or {})
        _refuse_unknown(f"project {project!r}", declared, wanted)
        return {"parts": [{"part_id": record.id,
                           "configs": _rows(project, record, wanted)}
                          for record in records]}

    # ----------------------------------------------------- set_active_config

    def set_active_config(project: str, part_id: str, config: str | None = None,
                          keep_overrides: bool = False) -> dict:
        record = _named(project, part_id, config)
        # Switching to a configuration is *loading that variant*: the explicit
        # overrides go, so the inspector shows pure M and nobody has to ask why
        # width is 12. `keep_overrides` is the deliberate other choice, and it
        # leaves the part immediately "M — modified".
        cleared = {} if keep_overrides else dict(record.params)
        with manifest_scope(service.store, project), locks.write_scope(part_id):
            service.store.update_part_entry(
                project, part_id,
                active_config=config or None,
                params=None if keep_overrides else {},
            )
        service.bus.publish({"type": "project_changed", "project": project,
                             "part": part_id, "reason": "active_config"})
        result = with_hint(service._rebuild(project, part_id))
        after = service.store.get_part(project, part_id)
        diverged, diverged_params = _divergence(after)
        return {**result,
                "part_id": part_id,
                "active_config": after.active_config,
                "diverged": diverged,
                "diverged_params": diverged_params,
                "cleared_overrides": cleared}

    # --------------------------------------------------- set_instance_config

    def set_instance_config(project: str, instance: str,
                            config: str | None = None) -> dict:
        """Bind ONE instance (``set_mate``/``clear_mate`` are the precedent):
        ``set_assembly`` is a full-list replace and would silently unbind
        everything a caller forgot to repeat."""
        if config is not None and not isinstance(config, str):
            raise ValidationError(
                "config must be a configuration name (a string)",
                {"got": type(config).__name__},
            )
        # Read-modify-write of the whole instance list, so it takes the
        # manifest lock; it takes no `write_scope` — a claim is a *part* claim,
        # and the store's whole-manifest writes deliberately have none.
        with manifest_scope(service.store, project):
            instances = service.store.instances(project)
            target = next((i for i in instances if i.id == instance), None)
            if target is None:
                raise NotFoundError(
                    f"instance {instance!r} not found in project {project!r}")
            target.config = config       # None unbinds: back to the live state
            service.store.set_instances(project, instances)   # validates
        service.bus.publish({"type": "project_changed", "project": project,
                             "part": target.part,
                             "reason": "instance_config"})
        return service.get_assembly(project)

    # -------------------------------------------------------------- registry

    registry.register(Tool(
        "set_part_configs",
        "Replace a part's configurations — its named parameter sets (S/M/L "
        "sizes, variants). Map of name -> {params: {name: value}, label?, "
        "description?}; names are lowercase [a-z0-9][a-z0-9_-]{0,31} and the "
        "map's order is the family order shown everywhere. This is a FULL "
        "replace: an omitted name is removed, and {} clears the family. A "
        "declared configuration is range- and enum-strict (unlike set_params, "
        "which stores an override raw and lets the worker clamp it) and its "
        "values are normalized on write. Removing a configuration an assembly "
        "instance is bound to, or the part's active_config, is refused "
        "(conflict_error names every referrer).",
        schema(
            {
                "project": {"type": "string", "description": "Project name"},
                "part_id": {"type": "string", "description": "Part id"},
                "configs": {"type": "object",
                            "description": "name -> {params, label?, "
                                           "description?} ({} clears)"},
            },
            ["project", "part_id", "configs"],
        ),
        set_part_configs,
    ))
    registry.register(Tool(
        "list_configs",
        "List the configurations of one part (part_id) or of every configured "
        "part in the project, with the active configuration, whether the "
        "working state diverges from it (and under which parameters), and the "
        "assembly instances bound to each name (referrers — check these before "
        "removing one).",
        schema(
            {
                "project": {"type": "string", "description": "Project name"},
                "part_id": {"type": "string",
                            "description": "One part (default: every "
                                           "configured part)"},
            },
            ["project"],
        ),
        list_configs,
    ))
    registry.register(Tool(
        "build_configs",
        "Build a part's configurations (or every configured part's) and return "
        "one row per configuration: {name, label, ok, cached, cache_key, "
        "metrics, warnings, error?}. Serial and de-duplicated by cache key, so "
        "two configurations with identical parameters cost one build. A "
        "configuration that fails to build is a row with ok: false and error, "
        "never a refusal of the whole call. Rows are in family order; the "
        "working state and the part's build badge are untouched.",
        schema(
            {
                "project": {"type": "string", "description": "Project name"},
                "part_id": {"type": "string",
                            "description": "One part (default: every "
                                           "configured part)"},
                "configs": {"type": "array",
                            "description": "Configuration names (default: all "
                                           "declared)"},
            },
            ["project"],
        ),
        build_configs,
    ))
    registry.register(Tool(
        "set_active_config",
        "Load one of a part's configurations into its working state (omit "
        "config to return to base). Clears the part's explicit parameter "
        "overrides — pass keep_overrides: true to layer them on top instead, "
        "which leaves the part diverged. Returns the rebuild plus {part_id, "
        "active_config, diverged, diverged_params, cleared_overrides}.",
        schema(
            {
                "project": {"type": "string", "description": "Project name"},
                "part_id": {"type": "string", "description": "Part id"},
                "config": {"type": "string",
                           "description": "Declared configuration name "
                                          "(omit for base)"},
                "keep_overrides": {"type": "boolean",
                                   "description": "Keep the explicit parameter "
                                                  "overrides (default false)"},
            },
            ["project", "part_id"],
        ),
        set_active_config,
    ))
    registry.register(Tool(
        "set_instance_config",
        "Bind one assembly instance to a declared configuration of its part "
        "(omit config to unbind, which returns the instance to the part's live "
        "working state). A bound instance resolves purely — the part's own "
        "overrides do not reach it. Returns the assembly.",
        schema(
            {
                "project": {"type": "string", "description": "Project name"},
                "instance": {"type": "string",
                             "description": "Assembly instance id"},
                "config": {"type": "string",
                           "description": "Declared configuration of the "
                                          "instance's part (omit to unbind)"},
            },
            ["project", "instance"],
        ),
        set_instance_config,
    ))
