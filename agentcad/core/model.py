"""Core domain types and application errors."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")


class AppError(Exception):
    """Base for expected application errors (mapped to HTTP 4xx)."""

    def __init__(self, message: str, details: dict | None = None,
                 headers: dict | None = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}
        #: Response headers the refusal must carry, for the callers where a
        #: header is part of the answer rather than decoration. Raising
        #: discards the handler's own `Response`, so setting one there and
        #: then raising loses it — which is how PRD-005a's anonymous catalog
        #: 404s went out with no `Cache-Control` while the flood argument
        #: depended on them being cacheable (review finding m1). Empty for
        #: every other error, so nothing else changes.
        self.headers = dict(headers or {})


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


class ServiceUnavailableError(AppError):
    """The instance cannot serve this request in its current configuration (503).

    Distinct from a rate limit: a 429 clears when a slot frees, a 503 here is a
    standing condition an operator must fix. PRD-007 raises it when the share
    customizer is asked to build on a single-worker kernel pool — running the
    anonymous build would starve members of their only worker, so the request
    refuses and names the knob to fix (``AGENTCAD_KERNEL_POOL_SIZE``).

    Its wire ``type`` via ``error_type`` is ``serviceunavailable_error``.
    """


def error_type(exc: AppError) -> str:
    """The wire ``type`` of an application error.

    ``NotFoundError`` -> ``"notfound_error"``, ``ValidationError`` ->
    ``"validation_error"``, ``ConflictError`` -> ``"conflict_error"``,
    ``RateLimitedError`` -> ``"ratelimited_error"`` — the spelling
    ``ToolRegistry.call`` has always put on the wire.

    It lives here so the two producers can pin each other: ``service`` needs it
    to synthesize a refusal it caught rather than let propagate, and a *copy*
    of the registry's mapping with no test would drift (P16). Do not spell a
    new one — ``notfound_error``, not ``not_found_error``.
    """
    return type(exc).__name__.replace("Error", "").lower() + "_error"


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

        **Total over the VALUE, strict about the NAME.** ``configs`` is JSON a
        merge or a hand edit can shape, and the key-wise merge takes a
        non-object entry whole, so ``5``, ``None``, ``{"label": "M"}`` and
        ``{"params": None}`` are all reachable without anyone editing
        project.json. A member that carries no params map holds no parameters,
        so it resolves as an empty configuration — loudly in the merge report
        (``manifest_merge.config_problems``), never as an exception out of a
        geometry read. Raising here was a **500 on the part's primary read**:
        ``effective_params`` is read by ``_cache_key_for`` inside
        ``_ensure_built``, upstream of every configuration-aware branch.
        """
        entry = (self.configs or {})[name]      # KeyError for an unknown NAME
        if not isinstance(entry, dict):
            return {}
        return dict(entry.get("params") or {})

    @property
    def effective_params(self) -> dict:
        """The working state (defaults < active config < explicit overrides).

        This is what every geometry request resolves to; ``params`` keeps
        meaning *explicit overrides* wherever the manifest is read or written
        (resolving inside the store would make the next ``set_params`` bake the
        configuration into the overrides). An ``active_config`` the map no
        longer declares resolves as base — silently here, loudly in the merge
        report — and so does one that names a member which is not an object
        with a params map (see :meth:`config_params`).
        """
        base: dict = {}
        if (
            self.active_config
            and self.configs
            and self.active_config in self.configs
        ):
            entry = self.configs[self.active_config]
            base = dict(entry.get("params") or {}) if isinstance(entry, dict) \
                else {}
        base.update(self.params)
        return base


@dataclass
class InstanceSpec:
    id: str
    # "" for a sub-assembly reference (PRD-013), which names a source project
    # via `assembly` instead of a part; a plain part instance always sets it.
    part: str = ""
    position: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    rotation_deg: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    color: str | None = None
    mate: dict | None = None  # optional declarative mate; resolves to transform
    # A declared configuration of `part` (PRD-012), resolved purely; None means
    # the part's live working state.
    config: str | None = None
    # PRD-013 assembly v2 (additive, old files load): `pattern` repeats a part
    # (or a sub-assembly) N times from ONE authored instance; `assembly` makes
    # the instance a cross-project sub-assembly reference (no `part`). At most
    # one of {part-only, pattern+part, assembly} — validated in set_instances.
    pattern: dict | None = None
    assembly: dict | None = None
    # Transient (never persisted): the project a sub-assembly member is BUILT
    # from. Set only on flattened members produced by cross-project resolution
    # so a consumer builds its geometry against the source, not the parent.
    # `to_manifest` omits it — an authored instance never carries it.
    origin_project: str | None = None

    def to_manifest(self) -> dict:
        # An assembly (sub-assembly) instance carries no part; a part instance
        # always writes its part id. Emitting `"part": ""` for a sub-assembly
        # would fail the store's unknown-part check on the next read.
        data = {
            "id": self.id,
            "part": self.part,
            "position": self.position,
            "rotation_deg": self.rotation_deg,
        }
        if self.assembly:
            # A sub-assembly reference has no part of its own; drop the empty
            # placeholder so the on-disk entry is honest (and byte-clean).
            data.pop("part")
        if self.color:
            data["color"] = self.color
        if self.mate:
            data["mate"] = self.mate
        if self.config:
            data["config"] = self.config
        if self.pattern:
            data["pattern"] = self.pattern
        if self.assembly:
            data["assembly"] = self.assembly
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
