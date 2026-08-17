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

from ..kernel.client import KernelError
from . import locks
from .model import ConflictError, NotFoundError, ValidationError
from .packages import format as pkgformat
from .packages.manager import manifest_scope
from .service import _divergence
from .specs import declares_specs
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
        # The lock spans the whole read-modify-write, not just the write: the
        # FR11 referential check reads the part entry AND the instance list, and
        # a `set_assembly` landing between that read and this write would bind
        # an instance to a configuration this call is removing. Reentrant, so
        # `update_part_entry`'s own scope nests for free.
        with manifest_scope(service.store, project):
            record, spec = _spec_for(project, part_id)
            problems = pkgformat.validate_configurations(configs, spec)
            if problems:
                raise ValidationError(
                    "invalid configurations: "
                    + "; ".join(p["message"] for p in problems),
                    {"problems": problems, "part_id": part_id},
                )
            # Normalized on write (int/float coercion, enum canonicalization),
            # so {"n": 3} and {"n": 3.0} are one configuration and one cache
            # key. A comprehension over `configs.items()` preserves the
            # caller's order — a family is not a lockfile, and this order is
            # what the switcher and the dimension table display.
            normalized = {
                name: {**entry,
                       "params": service.normalize_params(spec, entry["params"])}
                for name, entry in configs.items()
            }

            # FR11: a full replace may not orphan a binding. Both referrer
            # kinds are reported at once (the `remove_part` payload shape) — a
            # caller fixing one at a time would otherwise pay two round trips.
            removed = set(record.configs or {}) - set(normalized)
            bound = _referrers(project, part_id, removed)
            active_hit = bool(record.active_config) and \
                record.active_config in removed
            if bound or active_hit:
                names = sorted(
                    set(bound) | ({record.active_config} if active_hit else set())
                )
                reasons = [f"{name} by instance(s) {', '.join(ids)}"
                           for name, ids in sorted(bound.items())]
                if active_hit:
                    reasons.append("active_config")
                raise ConflictError(
                    f"configuration(s) {', '.join(names)} of part {part_id!r} "
                    "are referenced: " + "; ".join(reasons),
                    {"part": part_id,
                     "configs": names,
                     "instances": sorted({i for ids in bound.values()
                                          for i in ids}),
                     "active_config": active_hit},
                )

            # `write_scope` is belt and braces: `update_part_entry` opens the
            # same scope itself (and it nests safely), but naming the part here
            # keeps the claim guard right even if a future write on this path
            # goes through a store method that has no scope of its own.
            with locks.write_scope(part_id):
                # An emptied map POPS the key (G5: a part that no longer has a
                # family is byte-identical to one that never had one).
                service.store.update_part_entry(project, part_id,
                                                configs=normalized)
        service.bus.publish({"type": "project_changed", "project": project,
                             "part": part_id, "reason": "configs"})

        out = {"part_id": part_id, "configs": normalized,
               "active_config": record.active_config}
        active = record.active_config
        # Through `config_params`, which is total: a hand-edited or merged
        # member that is not an object with a params map resolves as an empty
        # configuration, so it needs no shape guard here and a rewrite of it
        # into `{"params": {}}` correctly moves no geometry.
        before = (record.config_params(active)
                  if active and active in (record.configs or {}) else None)
        if active and active in normalized and \
                normalized[active]["params"] != before:
            # The visible geometry moved, so the post-state a caller needs next
            # is the build. Editing a member nobody activated costs nothing.
            out["rebuild"] = with_hint(service._rebuild(project, part_id))
        return out

    # ---------------------------------------------------------- list_configs

    def _configured(project: str) -> list:
        """The project's configured SCRIPT parts, in manifest order. One
        definition for both readers, so `list_configs` and `build_configs`
        cannot disagree about what "configured" means (a reference part cannot
        hold a family — the store refuses to bind one — so a hand-edited
        `configs` on one is not a family either)."""
        return [
            service.store.get_part(project, entry["id"])
            for entry in service.store.manifest(project)["parts"]
            if entry.get("configs") and entry.get("kind", "script") == "script"
        ]

    def list_configs(project: str, part_id: str | None = None) -> dict:
        whole_project = part_id is None
        if part_id is not None:
            records = [service.store.get_part(project, part_id)]
        else:
            # Project-wide: the configured parts, and only those — an
            # unconfigured part is `configs: {}` on `get_part`, not a row here.
            records = _configured(project)
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
        if whole_project and not parts:
            # Never an empty list with no reason (the `build_configs` rule).
            return {"parts": [], "warnings": ["no configured parts"]}
        return {"parts": parts}

    # --------------------------------------------------------- build_configs

    def _spec_results(runner, project: str, part_id: str,
                      name: str) -> dict | None:
        """One configuration's SHAPE-tier spec results.

        The derived record carries both halves of the identity into
        ``_shape_tier`` at once — the sidecar key is that record's cache key
        and the ``spec_eval`` params are its ``effective_params`` — which is
        what stops a config-keyed sidecar from ever holding the base
        measurement. The assembly tier is deliberately absent: an assembly is
        not per configuration, and answering it here would mean measuring the
        whole project once per member.

        A ``KernelError`` is DATA, exactly as a failed build is: a member whose
        script will not evaluate is one row carrying its error, never the loss
        of the matrix.

        Only reached for a member that BUILT. A configuration whose build just
        failed would fail ``spec_eval`` for the same reason and at the same
        cost — a second kernel round trip, up to a 300 s timeout — to restate
        the error the row already carries in ``error``.
        """
        derived = service._record_for(project, part_id, name)
        try:
            payload, cached, _sidecar = runner._shape_tier(
                project, part_id, record=derived)
        except KernelError as exc:
            return {"error": exc.to_payload()}
        if payload is None:                     # declares nothing after all
            return None
        return {"checks": payload["checks"], "cached": cached}

    def _rows(project: str, record, wanted) -> list[dict]:
        """One row per requested configuration, in family order.

        Serial and de-duplicated by cache key: every requested member's pure
        key is computed first, each distinct key is built once, and the row is
        fanned back out across the names that share it. A fan-out over the
        kernel pool was measured at 1.08x-1.40x against a pre-registered 1.5x
        bar in PRD-011 and deleted; two configurations sharing a key would also
        race the worker's fixed-name staging file.

        A part that declares SPECS also gets ``spec_results`` per row — read
        through ``getattr(service, "specs", None)``, because this pack sorts at
        ``con`` and the specs pack may simply not be loaded.
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
                # ...and the de-duplicated sibling reports the same measurement,
                # not a blanket True: when the shared build FAILED there is no
                # `.acm` and no `.metrics.json`, so claiming a cache hit would
                # contradict the tool description and `docs/agent-api.md`.
                built[name] = (result, bool(result.get("ok")))

        # Read once per part, not per member: `declares_specs` is a memoized
        # AST scan, but the script is a file read.
        runner = getattr(service, "specs", None)
        declares = bool(names) and runner is not None and declares_specs(
            service.store.read_script(project, record.id))

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
            if declares and row["ok"]:
                spec_results = _spec_results(runner, project, record.id, name)
                if spec_results is not None:
                    row["spec_results"] = spec_results
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
            return {"part_id": part_id, **_part_rows(project, record, wanted)}

        records = _configured(project)
        if not records:
            return {"parts": [], "warnings": ["no configured parts"]}
        declared: dict = {}
        for record in records:
            declared.update(record.configs or {})
        _refuse_unknown(f"project {project!r}", declared, wanted)
        return {"parts": [{"part_id": record.id,
                           **_part_rows(project, record, wanted)}
                          for record in records]}

    def _part_rows(project: str, record, wanted) -> dict:
        """``{configs: [rows], warnings?}`` — an empty matrix always says why.

        Project-wide, a part that declares none of the requested names is the
        only way rows can be empty while the family is not (a single-part call
        refuses an undeclared name outright), and reading `configs: []` with no
        reason is what makes a caller suspect the build rather than the filter.
        """
        rows = _rows(project, record, wanted)
        if rows:
            return {"configs": rows}
        if not (record.configs or {}):
            reason = f"part {record.id!r} declares no configurations"
        elif wanted:
            reason = (f"part {record.id!r} declares none of the requested "
                      f"configurations: {', '.join(wanted)}")
        else:
            reason = "no configurations requested"
        return {"configs": rows, "warnings": [reason]}

    # ----------------------------------------------------- set_active_config

    def set_active_config(project: str, part_id: str, config: str | None = None,
                          keep_overrides: bool = False) -> dict:
        # The lock spans the read too: `cleared_overrides` reports the map that
        # was there, and a `set_params` landing between the read and the write
        # would be reported as cleared while its value survived (or the other
        # way round). Reentrant, so the store's own scope nests for free.
        with manifest_scope(service.store, project):
            record = _named(project, part_id, config)
            # Switching to a configuration is *loading that variant*: the
            # explicit overrides go, so the inspector shows pure M and nobody
            # has to ask why width is 12. Only a real CHANGE of the active
            # configuration clears them, though — re-selecting what is already
            # active (or DELETEing the active configuration of a part already at
            # base) must not silently drop a caller's `set_params` values.
            # `keep_overrides` is the deliberate other choice, and it leaves the
            # part immediately "M — modified".
            changing = (config or None) != record.active_config
            clearing = changing and not keep_overrides
            cleared = dict(record.params) if clearing else {}
            # `write_scope` is belt and braces: `update_part_entry` opens the
            # same scope itself (and it nests safely), but naming the part here
            # keeps the claim guard right even if a future write on this path
            # goes through a store method that has no scope of its own.
            with locks.write_scope(part_id):
                service.store.update_part_entry(
                    project, part_id,
                    active_config=config or None,
                    params={} if clearing else None,
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
        # Read-modify-write of the whole instance list, so it takes BOTH locks
        # that serialize one: `manifest_scope` (against the configuration tools
        # and the package manager) and `service._lock` (against
        # `service.set_assembly`, which serializes the identical read-all /
        # write-all on it). Outer-to-inner is manifest_scope then `_lock`, the
        # order the packages path already implies; both are reentrant.
        # It takes no `write_scope` — a claim is a *part* claim, and the store's
        # whole-manifest writes deliberately have none.
        # NOT covered, and out of PRD-012's scope: `tools_mates.
        # _set_instance_mate` and `routes_assembly2.patch_instance` do the same
        # read-all/write-all with no lock at all (a deferred follow-up).
        with manifest_scope(service.store, project), service._lock:
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
        "metrics, warnings, error?}, plus spec_results {checks, cached} when "
        "the part declares SPECS (shape tier — an assembly is not per "
        "configuration). Serial and de-duplicated by cache key, so "
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
        "overrides when the active configuration changes, unless "
        "keep_overrides: true — which layers them on top instead and leaves the "
        "part diverged. Re-selecting the configuration that is already active "
        "changes nothing and keeps the overrides. Returns the rebuild plus "
        "{part_id, active_config, diverged, diverged_params, "
        "cleared_overrides}.",
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
