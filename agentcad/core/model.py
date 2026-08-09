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
        return data


@dataclass
class InstanceSpec:
    id: str
    part: str
    position: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    rotation_deg: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    color: str | None = None
    mate: dict | None = None  # optional declarative mate; resolves to transform

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
