"""ToolRegistry: the agent-facing tool surface, defined once.

The MCP server and the built-in chat agent both render their tool lists from
this registry, so the two surfaces cannot drift. Handlers return JSON-able
dicts; expected failures come back as ``{"error": {...}}`` payloads so agents
can read and react to them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ..kernel.client import KernelError
from .model import AppError
from .service import AgentCADService

_TYPE_CHECKS = {
    "string": lambda v: isinstance(v, str),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
}


@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict
    handler: Callable[..., dict]


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"duplicate tool {tool.name!r}")
        self._tools[tool.name] = tool

    def list(self) -> list[Tool]:
        return list(self._tools.values())

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def call(self, name: str, args: dict) -> dict:
        tool = self._tools.get(name)
        if tool is None:
            return {"error": {"type": "unknown_tool", "message": f"no tool named {name!r}"}}
        problem = self._validate(tool.input_schema, args or {})
        if problem:
            return {"error": {"type": "invalid_arguments", "message": problem}}
        try:
            return tool.handler(**(args or {}))
        except AppError as exc:
            return {
                "error": {
                    "type": type(exc).__name__.replace("Error", "").lower() + "_error",
                    "message": exc.message,
                    "details": exc.details,
                }
            }
        except KernelError as exc:
            return {"error": exc.to_payload()}

    @staticmethod
    def _validate(schema: dict, args: dict) -> str | None:
        props = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in args:
                return f"missing required argument {key!r}"
        required = set(schema.get("required", []))
        for key, value in args.items():
            if key not in props:
                return f"unexpected argument {key!r}"
            # None on an optional argument means "omitted" — skip type-checking
            # so callers can pass a uniform payload with null defaults.
            if value is None and key not in required:
                continue
            expected = props[key].get("type")
            check = _TYPE_CHECKS.get(expected)
            if check and not check(value):
                return f"argument {key!r} must be of type {expected}"
        return None


def _schema(properties: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": properties, "required": required}

_PROJ = {"type": "string", "description": "Project name"}
_PART = {"type": "string", "description": "Part id"}


def build_registry(service: AgentCADService) -> ToolRegistry:
    registry = ToolRegistry()
    reg = registry.register

    reg(Tool(
        "list_projects",
        "List all known projects with their part counts.",
        _schema({}, []),
        lambda: {"projects": service.list_projects()},
    ))
    reg(Tool(
        "create_project",
        "Create a new empty project. Name must match [a-z][a-z0-9_]{0,39}.",
        _schema({"name": {"type": "string", "description": "Project name"}}, ["name"]),
        lambda name: service.create_project(name),
    ))
    reg(Tool(
        "open_project",
        "Open an existing project directory by absolute path (e.g. a bundled example).",
        _schema({"path": {"type": "string", "description": "Absolute path to a project directory"}}, ["path"]),
        lambda path: service.open_project(path),
    ))
    reg(Tool(
        "get_project",
        "Get a project's manifest: parts (with build state), assembly instances, materials.",
        _schema({"project": _PROJ}, ["project"]),
        lambda project: service.get_project(project),
    ))
    reg(Tool(
        "create_part",
        "Create a new part. Omit 'script' to start from the default template. "
        "Returns the part with metrics; call part_template first to learn the script contract.",
        _schema(
            {
                "project": _PROJ,
                "part_id": _PART,
                "label": {"type": "string", "description": "Human-readable label"},
                "script": {"type": "string", "description": "Part script (build123d, PARAMS + build(p))"},
                "material": {"type": "string", "description": "Material id (see get_project.materials)"},
            },
            ["project", "part_id"],
        ),
        lambda project, part_id, label=None, script=None, material="al6061":
            service.create_part(project, part_id, label, script, material),
    ))
    reg(Tool(
        "get_part",
        "Get a part: script, parameter specs, current values, build status, metrics.",
        _schema({"project": _PROJ, "part_id": _PART}, ["project", "part_id"]),
        lambda project, part_id: service.get_part(project, part_id),
    ))
    reg(Tool(
        "update_part_script",
        "Replace a part's script and rebuild. On failure you get the traceback with the "
        "failing line; the previous good geometry is kept. You may also change label/material.",
        _schema(
            {
                "project": _PROJ,
                "part_id": _PART,
                "script": {"type": "string", "description": "New full script text"},
                "label": {"type": "string"},
                "material": {"type": "string"},
            },
            ["project", "part_id"],
        ),
        lambda project, part_id, script=None, label=None, material=None:
            _with_hint(service.update_part(project, part_id, script, label, material)),
    ))
    reg(Tool(
        "set_params",
        "Set parameter values (merged with existing overrides) and rebuild. "
        "Numeric values are clamped to the spec's min/max with warnings; typed "
        "values (bool/enum/string) must match their spec. Unknown names are "
        "rejected before anything is written; a null value removes an override.",
        _schema(
            {
                "project": _PROJ,
                "part_id": _PART,
                "values": {"type": "object", "description": "Map of param name to value: "
                           "numbers, booleans, enum choices, or strings per the part's params_spec"},
            },
            ["project", "part_id", "values"],
        ),
        lambda project, part_id, values: _with_hint(service.set_params(project, part_id, values)),
    ))
    reg(Tool(
        "delete_part",
        "Delete a part (fails while assembly instances reference it).",
        _schema({"project": _PROJ, "part_id": _PART}, ["project", "part_id"]),
        lambda project, part_id: (service.delete_part(project, part_id), {"deleted": part_id})[1],
    ))
    reg(Tool(
        "get_metrics",
        "Get a part's geometry metrics: volume, mass, area, bbox, center of mass, validity.",
        _schema({"project": _PROJ, "part_id": _PART}, ["project", "part_id"]),
        lambda project, part_id: service.get_metrics(project, part_id),
    ))
    reg(Tool(
        "get_mesh_summary",
        "Get mesh statistics (vertex/triangle/edge counts and bbox) without the binary buffer.",
        _schema({"project": _PROJ, "part_id": _PART}, ["project", "part_id"]),
        lambda project, part_id: service.mesh_summary(project, part_id),
    ))
    reg(Tool(
        "export_part",
        "Export a part to exports/<part_id>.<format>. Formats: step, stl, 3mf.",
        _schema(
            {
                "project": _PROJ,
                "part_id": _PART,
                "format": {"type": "string", "description": "step | stl | 3mf"},
                "tolerance": {"type": "number", "description": "Mesh tolerance for stl/3mf (mm, default 0.05)"},
            },
            ["project", "part_id", "format"],
        ),
        lambda project, part_id, format, tolerance=0.05:
            service.export_part(project, part_id, format, tolerance),
    ))
    reg(Tool(
        "get_assembly",
        "Get assembly instances with per-instance mass and the rolled-up mass/bbox.",
        _schema({"project": _PROJ}, ["project"]),
        lambda project: service.get_assembly(project),
    ))
    reg(Tool(
        "set_assembly",
        "Replace the assembly instance list. Each instance: {id, part, position [x,y,z] mm, "
        "rotation_deg [rx,ry,rz] intrinsic XYZ Euler, color '#rrggbb' optional}.",
        _schema(
            {
                "project": _PROJ,
                "instances": {"type": "array", "description": "Full replacement instance list"},
            },
            ["project", "instances"],
        ),
        lambda project, instances: service.set_assembly(project, instances),
    ))
    reg(Tool(
        "check_interference",
        "Boolean-intersect every instance pair and report overlaps above min_volume mm^3.",
        _schema(
            {
                "project": _PROJ,
                "min_volume": {"type": "number", "description": "Overlap threshold in mm^3 (default 0.001)"},
            },
            ["project"],
        ),
        lambda project, min_volume=0.001: service.check_interference(project, min_volume),
    ))
    reg(Tool(
        "export_assembly",
        "Export the whole assembly (instances placed by their transforms). Formats: step, stl.",
        _schema(
            {"project": _PROJ, "format": {"type": "string", "description": "step | stl"}},
            ["project", "format"],
        ),
        lambda project, format: service.export_assembly(project, format),
    ))
    reg(Tool(
        "part_template",
        "Get the part script contract, a starter template, and a build123d cheat-sheet. "
        "Call this before writing your first script.",
        _schema({}, []),
        lambda: service.part_template(),
    ))

    _load_tool_packs(registry, service)
    return registry


def _load_tool_packs(registry: "ToolRegistry", service: AgentCADService) -> None:
    """Discover agentcad/core/tools_*.py and let each register its v2 tools.

    Extension point: each module exports ``register(registry, service)``. A
    pack may skip registration (e.g. FEM tools when the optional extra is not
    installed) so agents never see a tool that cannot run.
    """
    import importlib
    import pkgutil

    import agentcad.core as core_pkg

    for info in pkgutil.iter_modules(core_pkg.__path__):
        if not info.name.startswith("tools_"):
            continue
        module = importlib.import_module(f"agentcad.core.{info.name}")
        register = getattr(module, "register", None)
        if callable(register):
            register(registry, service)


def _with_hint(result: dict) -> dict:
    if not result.get("ok") and "hint" not in result:
        result = {
            **result,
            "hint": "Read details.traceback and details.line; call part_template for the "
                    "contract and common failure modes; the previous geometry is unchanged.",
        }
    return result


# Public aliases for tool packs (agentcad/core/tools_*.py).
schema = _schema
with_hint = _with_hint
