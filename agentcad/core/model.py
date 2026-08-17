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


class AuthError(AppError):
    """No usable credential (401). PRD-005a hosted mode only.

    Deliberately says nothing about *why*: "no such handle", "wrong password"
    and "expired session" are one answer, because the differences are a user
    enumeration oracle.
    """


class AuthzError(AppError):
    """A valid principal that may not do this (403).

    Named ``AuthzError`` rather than ``PermissionError`` on purpose — the
    builtin of that name is a real exception this codebase catches around
    filesystem work, and shadowing it in ``core.model`` would be a trap.
    """


class RateLimitedError(AppError):
    """Too many attempts (429). Carries ``details.retry_after_s``."""


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
