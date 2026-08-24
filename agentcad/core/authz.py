"""RBAC: the view < comment < edit < admin ladder, and the one refusal.

Two functions and an error. :func:`role_of` answers *what role does this
principal hold on this project*, :func:`require` turns that into either
nothing at all or a ``permission_error``, and everything that enforces
anything in PRD-005 — the read guard, the tool-registry wrapper, the
write-guard wrapper (design spec §3) — is a call to :func:`require` at a
different choke point. Enforcement lives in the wrappers; the *decision*
lives here, once, so three surfaces cannot drift into three answers.

**The ladder.** ``view`` reads, ``comment`` adds review threads and
proposals, ``edit`` changes geometry, ``admin`` changes who may do the
above. It is a total order (a rung includes every rung below it), which is
why a check is a comparison and not a set membership — and why a floor is a
single word rather than a permission list nobody would keep in sync.

**Membership decides; this module only reads it.** Nothing here writes,
which is what lets the same call sit on a hot read path.
"""

from __future__ import annotations

from .model import AuthzError
from .tenancy import TenancyStore, handle_of, principal_key

#: Weakest first. The index in this tuple **is** the rank; there is no second
#: table to disagree with it.
ROLE_ORDER: tuple[str, ...] = ("view", "comment", "edit", "admin")

_RANK = {role: index for index, role in enumerate(ROLE_ORDER)}


class PermissionError(AuthzError):      # noqa: A001 — see the docstring
    """A valid principal without the role this action needs.

    **Wire contract**, because two mechanisms fix it and neither is optional:

    * ``type`` on the tool surface is ``permission_error``, and it is
      *derived* — ``model.error_type`` (and the copy in ``ToolRegistry.call``)
      is ``type(exc).__name__.replace("Error", "").lower() + "_error"``. That
      is why this class is spelled ``PermissionError`` and not
      ``PermissionDeniedError``: the name **is** the wire string, and FR6
      fixes the wire string at ``permission_error``.
    * HTTP status is ``403``, and it is *inherited* — ``app.py``'s
      ``_ERROR_STATUS`` walk is ``isinstance``-based, so subclassing
      ``AuthzError`` (already mapped to 403) gives the status with **no core
      edit at all**. ``app.py`` and ``model.py`` are untouched by PRD-005.

    ``details`` names ``{required, project, principal_role}`` — the required
    rung, the project it is required on, and the rung actually held (``None``
    for a principal with no role at all). A refusal that only said "forbidden"
    would leave a person with no idea whether to ask for a grant or to check
    which workspace they are in.

    **The builtin collision is deliberate and contained.** ``core/model.py``
    records why ``AuthzError`` was *not* called this
    (``PermissionError`` is a real builtin the codebase catches around
    filesystem work, and shadowing it in a module everything imports would be
    a trap). The trade is different here: ``model.error_type`` derives the
    wire string from the class name, so the only way to spell
    ``permission_error`` without editing a core is this name. It is contained
    two ways — this module does no filesystem work and catches no ``OSError``
    family, and :data:`PermissionDeniedError` is exported as the alias
    importers should use, so no *other* module has to shadow the builtin in
    its own namespace to raise this.
    """


#: The name to import. ``from ..core.authz import PermissionDeniedError``
#: raises exactly the class above (it *is* the class) without shadowing the
#: builtin in the importing module — which is the whole point. The wire
#: string stays ``permission_error`` because it is derived from
#: ``__name__``, and ``__name__`` is ``PermissionError``.
PermissionDeniedError = PermissionError


def rank(role: str | None) -> int:
    """The rung's index, or ``-1`` for ``None`` and for anything unknown.

    ``-1`` and not an exception: this is reached from :func:`require` with
    whatever ``orgs.json`` held, and an unreadable role must be *less* than
    ``view`` — below the ladder, never above it. Failing closed is the
    property; the arithmetic is how it is achieved.
    """
    return _RANK.get(role, -1) if isinstance(role, str) else -1


def at_least(role: str | None, floor: str) -> bool:
    """Does *role* include *floor*? ``False`` for ``None`` or nonsense.

    An unknown **floor** is a programming error and raises: a typo'd floor
    that silently answered ``True`` would open the very check it is spelled
    into.
    """
    if floor not in _RANK:
        raise ValueError(
            f"unknown role floor {floor!r}; one of {', '.join(ROLE_ORDER)}")
    return rank(role) >= _RANK[floor]


def role_of(store: TenancyStore, principal: str,
            org: str, workspace: str, proj: str | None = None) -> str | None:
    """The role *principal* holds on ``org/workspace/proj``, or ``None``.

    **Precedence**, highest first:

    1. **Org admin wins outright.** A member whose org role is ``admin`` is
       ``admin`` on every project in the org, and a per-project override
       cannot hold them down. Not a convenience: an org admin may rewrite the
       override in one call, so honouring a demotion would be a restriction
       that is not one — a fiction the members panel would render as a
       guarantee. The honest model is that org admin is a *floor*, and it is
       stated here rather than discovered.
    2. **The per-project override**, in either direction. This is the whole
       of FR6's "per-project overrides": it may raise an org viewer to
       ``edit`` on one project, or hold an org editor to ``view`` on another.
    3. **The org default** — the member's org role, which is what makes
       "anya is an editor in acme" mean something on a project nobody has
       touched with a grant.
    4. Otherwise ``None``: not a member, no override. **An agent principal
       has no step 3** — a token is never an org member (``tenancy.handle_of``
       says why), so ``agent:ci`` reaches a project only through an explicit
       grant. Conservative on purpose: a token that inherited an org default
       would silently widen with every membership change.

    With ``proj=None`` this answers the **org-level** question (steps 1, 3
    and 4) — what ``list_members`` and the org-admin tools check.

    Never raises. It is called from a request guard on every read, and a
    malformed document, an unknown org or a nonsense principal all resolve to
    ``None``, which denies. Failing closed is not an accident of the code
    path; ``tenancy``'s readers are total over the value for this reason.
    """
    try:
        key = principal_key(principal)
    except Exception:                       # noqa: BLE001 — a bad principal denies
        return None
    handle = handle_of(key)

    org_role = store.org_role(org, handle) if handle else None
    if org_role == "admin":
        return "admin"
    if proj is not None:
        override = store.project_roles(org, workspace, proj).get(key)
        if override in _RANK:
            return override
    return org_role


def can(store: TenancyStore, floor: str, principal: str,
        org: str, workspace: str, proj: str | None = None) -> bool:
    """The boolean form. ``require`` is the one that refuses; this is for a
    caller shaping a response (which buttons a viewer gets), where a raise
    would be control flow."""
    return at_least(role_of(store, principal, org, workspace, proj), floor)


def require(store: TenancyStore, floor: str, principal: str,
            org: str, workspace: str, proj: str | None = None) -> str:
    """Return the effective role, or raise :class:`PermissionError` (403).

    Argument order is ``(store, floor, principal, ...)`` — the floor second so
    a call site reads as the sentence it is: *require edit of nikita on
    acme/main/widget*.

    The message names the rung required, the rung held and the project,
    because the three questions a refused person asks are "what do I need",
    "what do I have" and "on what". It does **not** name whether the project
    exists: a principal with no role on it is told the same thing whether it
    is another org's project or their own with no grant, which is what keeps
    a cross-tenant probe from being an existence oracle (FR5's "no
    cross-tenant path reachable").
    """
    role = role_of(store, principal, org, workspace, proj)
    if at_least(role, floor):
        return role                          # at_least(None, ...) is False
    where = proj if proj is not None else f"{org}/{workspace}"
    raise PermissionError(
        f"{principal_or_anonymous(principal)} needs {floor!r} on {where!r} "
        f"and holds {role!r}. An admin of org {org!r} can grant it.",
        {"required": floor, "project": proj, "principal_role": role},
    )


def principal_or_anonymous(principal: object) -> str:
    """A principal safe to interpolate into a refusal message.

    The refusal is rendered back to the caller, so an unbounded or
    unprintable principal must not travel through it verbatim.
    """
    if not isinstance(principal, str) or not principal.strip():
        return "an anonymous caller"
    return repr(principal[:64])
