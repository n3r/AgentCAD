"""Core domain types and application errors."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")


class AppError(Exception):
    """Base for expected application errors (mapped to HTTP 4xx)."""

    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}


class NotFoundError(AppError):
    pass


class ValidationError(AppError):
    pass


class ConflictError(AppError):
    pass


@dataclass
class ParamSpec:
    name: str
    default: float | int | bool | str
    type: str = "number"  # "number" | "int" | "bool" | "enum" | "string"
    min: float | None = None  # number/int only
    max: float | None = None  # number/int only
    choices: list[str | float] | None = None  # enum only
    max_len: int | None = None  # string only
    unit: str | None = None
    description: str | None = None


@dataclass
class PartRecord:
    id: str
    label: str
    material: str
    params: dict[str, float | int | bool | str] = field(default_factory=dict)
    kind: str = "script"  # "script" | "reference"
    source: str | None = None  # reference parts only: project-relative import path
    solid_materials: dict[str, str] | None = None  # solid label/index -> material id
    # Named parameter sets (PRD-012): {name: {params, label?, description?}},
    # the schema PRD-011 froze. Insertion order IS family order — never sorted.
    configs: dict[str, dict] | None = None
    active_config: str | None = None  # a declared name, or None = base

    def to_manifest(self) -> dict:
        data = {
            "id": self.id,
            "label": self.label,
            "material": self.material,
            "params": self.params,
        }
        if self.kind != "script":
            data["kind"] = self.kind
        if self.source is not None:
            data["source"] = self.source
        if self.solid_materials:
            data["solid_materials"] = self.solid_materials
        # Written only when set (the solid_materials precedent), so a project
        # without configurations serializes byte-identically to a pre-PRD-012
        # one — the guarantee is this conditional, not a schema version.
        if self.configs:
            data["configs"] = self.configs
        if self.active_config:
            data["active_config"] = self.active_config
        return data

    def config_params(self, name: str) -> dict:
        """Pure-config resolution (defaults < config): a COPY of the declared
        configuration's params, ignoring ``active_config`` and the explicit
        overrides — so a variant's identity never depends on session state.

        An unknown name raises KeyError: every tool boundary validates
        membership first, so reaching here with one is a programming error.
        "defaults <" needs no code — ``worker._resolve_params`` fills every
        unset name from ``PARAMS[name]["default"]``.
        """
        return dict((self.configs or {})[name]["params"])

    @property
    def effective_params(self) -> dict:
        """The working state (defaults < active config < explicit overrides).

        This is what every geometry request resolves to; ``params`` keeps
        meaning *explicit overrides* wherever the manifest is read or written
        (resolving inside the store would make the next ``set_params`` bake the
        configuration into the overrides). An ``active_config`` the map no
        longer declares resolves as base — silently here, loudly in the merge
        report.
        """
        base: dict = {}
        if (
            self.active_config
            and self.configs
            and self.active_config in self.configs
        ):
            base = dict(self.configs[self.active_config].get("params") or {})
        base.update(self.params)
        return base


@dataclass
class InstanceSpec:
    id: str
    part: str
    position: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    rotation_deg: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    color: str | None = None
    mate: dict | None = None  # optional declarative mate; resolves to transform
    # A declared configuration of `part` (PRD-012), resolved purely; None means
    # the part's live working state.
    config: str | None = None

    def to_manifest(self) -> dict:
        data = {
            "id": self.id,
            "part": self.part,
            "position": self.position,
            "rotation_deg": self.rotation_deg,
        }
        if self.color:
            data["color"] = self.color
        if self.mate:
            data["mate"] = self.mate
        if self.config:
            data["config"] = self.config
        return data


def validate_id(value: str, what: str = "id") -> str:
    if not isinstance(value, str) or not ID_RE.match(value):
        raise ValidationError(
            f"invalid {what} {value!r}: must match [a-z][a-z0-9_]{{0,39}}"
        )
    return value


def validate_vec3(value, what: str) -> list[float]:
    if (
        not isinstance(value, (list, tuple))
        or len(value) != 3
        or any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in value)
    ):
        raise ValidationError(f"{what} must be a list of 3 numbers")
    return [float(v) for v in value]
