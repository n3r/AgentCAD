"""Tool pack: the tenant surface — agent tokens, roles, membership, sync (FR3/FR6).

Registered **only when this process is serving a hosted app**, the ``whoami``
precedent (``tools_auth.py``) verbatim: the module is looked up in
``sys.modules`` rather than imported, so a headless run (``agentcad check``,
the publish gate, a library embedding) pays nothing — not even the FastAPI
import — to discover that it has no hosted configuration, and an agent is never
offered a tool that cannot run.

**What is new here and what is not.** PRD-005a deliberately kept token minting
off the token-authenticated surface (its Decision 14: "minting credentials from
the same authenticated HTTP surface those credentials unlock is a
privilege-escalation shape worth avoiding *while there is no audit log*"). The
audit log now exists (``core/audit.py``), which is the condition the PRD itself
named, so the mint moves onto the tool surface — with two properties that make
it a promotion rather than a hole:

* every mutating tool here **audits itself** (action, project, argument digest,
  outcome) before it returns, and
* the floor is an **org admin**, which ``authz.role_of`` can only answer for a
  *person*: an agent principal has no org default (``tenancy.handle_of`` — a
  token is never an org member), so a bearer token structurally cannot reach
  ``admin`` at org level and structurally cannot mint, grant or revoke. 005a's
  "a token drives the product, a human manages accounts" survives as a
  *derived* property of the RBAC model rather than as a second rule that could
  drift from it.

**On the tool-layer audit.** The general "every mutating tool writes a row" tap
is ``audit.tap_registry``, and it belongs to the registry wrapper slice 4
installs at the serve seam. It is built and tested but **not installed
anywhere yet**, so this pack records its own writes directly rather than
waiting for it. When the wrapper lands, these rows are the same shape it
produces; the duplication to watch for is one row per action, which is why the
actions here are named for the tool.

**``whoami`` is extended, not replaced.** ``tools_auth`` registers it and
``ToolRegistry.register`` refuses a duplicate name, so this pack wraps the
registered handler in place (captured original + ``_WRAPPED`` sentinel — the
``tools_structure``/``EventBus`` idiom). The extra keys appear **only on an
instance that has at least one org**: with no tenancy document the payload is
byte-for-byte 005a's ``{principal, kind, role, mode}``, which is the same
"absence of a tenant is the old behaviour" rule the rest of PRD-005 is built
on, and is why 005a's equality assertions still hold.
"""

from __future__ import annotations

import functools
import sys

from . import audit as audit_mod
from . import tenancy
from .authstore import check_token_scope
from .authz import PermissionDeniedError, can, require
from .model import AuthError, NotFoundError, ValidationError
from .tools import Tool, schema

_SECURITY_MODULE = "agentcad.server.security"

#: Marks the wrapped ``whoami`` handler so a second registry build (or a pack
#: loaded twice in one process) cannot wrap the wrapper.
_WRAPPED = "_agentcad_cloud_wrapped"

#: What ``require`` prints for an **org-level** question when no single
#: workspace answers it. ``authz.role_of`` ignores the workspace with
#: ``proj=None``, so this only ever reaches a refusal message, where "acme/*"
#: reads as "anywhere in acme".
ANY_WORKSPACE = "*"

#: ``sync_status``'s answer until slice 6 lands the client half. Stated in the
#: payload rather than only in this docstring, because the reader who needs it
#: is an agent looking at the tool's output.
SYNC_STUB_NOTE = (
    "local-only: remote comparison is wired in slice 6 (PRD-005 FR8-client). "
    "`remote: null` means this instance has not been told about a remote, not "
    "that the project is in sync with one."
)


def register(registry, service) -> None:
    module = sys.modules.get(_SECURITY_MODULE)
    cfg = module.current_config() if module is not None else None
    if cfg is None or not cfg.mode.hosted:
        return

    store = cfg.store                                   # AuthStore
    # The same root, so the shared depth-counted guard is the same one and a
    # tenancy write inside an identity write cannot deadlock (tenancy's module
    # docstring has the whole argument).
    tenants = tenancy.TenancyStore(store.root)
    log = audit_mod.for_auth_store(store)

    # -------------------------------------------------------------- helpers

    def _who():
        who = module.current_principal()
        if who is None:
            # Unreachable over HTTP (the guard answers 401 first); the chat
            # engine and an in-process caller reach the registry with no
            # request, and `None` must not render as a principal.
            raise AuthError("authentication required")
        return who

    def _tenant(org, workspace, *, require_workspace: bool):
        """Resolve ``(org, workspace)`` for this call, or refuse.

        The request's tenant wins when there is one (slice 4 sets it from the
        session's active workspace, the ``X-Agentcad-Workspace`` header or a
        token's scope). **An argument that disagrees with it is a refusal, not
        an override** — otherwise every tool here would be a way to act in
        another tenant by naming it, which is precisely what the tenant
        resolver exists to prevent.

        With no tenant resolved (which is every request until slice 4 lands,
        and every API client that names its tenant explicitly), the arguments
        answer: the org is required, and the workspace is required unless the
        org has exactly one.
        """
        current = tenancy.current_tenant()
        if current is not None:
            if (org and org != current[0]) or (workspace and workspace != current[1]):
                raise PermissionDeniedError(
                    f"this request is in {current[0]}/{current[1]}; it cannot "
                    f"act in {org or current[0]}/{workspace or current[1]}.",
                    {"required": "admin", "project": None,
                     "principal_role": None})
            return current
        if not org:
            raise ValidationError(
                "name the org: this request carries no tenant, so `org` is "
                "how the tool knows which one you mean.", {"org": None})
        org = tenancy.check_name(org, "org")
        if workspace:
            return org, tenancy.check_name(workspace, "workspace")
        try:
            spaces = tenants.list_workspaces(org)
        except NotFoundError:
            # **Not re-raised.** A 404 here would tell a caller who holds
            # nothing in this org whether it exists, while a real org answers
            # `permission_error` from the floor below — the existence oracle
            # FR5 forbids, and the reason `authz.require`'s own message never
            # says whether a project exists either. An unknown org resolves
            # like an org with no workspaces and is refused by the floor.
            spaces = []
        if len(spaces) == 1:
            return org, spaces[0]["id"]
        if not require_workspace:
            return org, ANY_WORKSPACE
        raise ValidationError(
            f"name the workspace: org {org!r} does not resolve to exactly one.",
            # The ids are deliberately NOT listed: this refusal is reachable
            # by a caller whose floor has not been checked yet.
            {"org": org})

    def _audit(action, *, org, project=None, args=None, outcome="ok"):
        audit_mod.record(log, org, action, principal=_who().client_id,
                         project=project, args=args, outcome=outcome)

    # --------------------------------------------------------------- whoami

    _extend_whoami(registry, module, store, tenants)

    # --------------------------------------------------------------- tokens

    def create_agent_token(name: str, org: str, projects: list, role: str,
                           workspace: str | None = None,
                           ttl_days: int | None = None) -> dict:
        """Mint a scoped bearer. The secret is in this response and nowhere else."""
        who = _who()
        org, ws = _tenant(org, workspace, require_workspace=False)
        require(tenants, "admin", who.client_id, org, ws)
        scope = check_token_scope(
            {"org": org, "projects": projects, "role": role,
             "workspace": None if ws == ANY_WORKSPACE else ws})

        # Names are labels in `authstore` (two tokens may share one, and a test
        # pins that) but they compose into ONE principal, `agent:<name>`, which
        # is what the tenancy grants below are keyed on. A second live token of
        # the same name would silently union the two scopes' reach, so the
        # refusal is here, at the only door that writes grants.
        live = store.live_tokens_named(name if isinstance(name, str) else "")
        if live:
            raise ValidationError(
                f"a live token named {name!r} already exists (id "
                f"{live[0]['id']}). Both would speak as the one principal "
                f"'agent:{name}' and their reach would silently union — revoke "
                f"it first, or pick another name.",
                {"name": name, "existing": [row["id"] for row in live]})

        # Every project is checked BEFORE anything is written: a mint whose
        # grants then failed would hand back a live secret that reaches
        # nothing, and the secret is shown once.
        for qualified in scope["projects"]:
            proj_ws, proj = qualified.split("/", 1)
            if not tenants.has_project(org, proj_ws, proj):
                raise NotFoundError(
                    f"no project {proj!r} in {org}/{proj_ws}",
                    {"org": org, "workspace": proj_ws, "project": proj})

        principal = f"agent:{name}"
        for qualified in scope["projects"]:
            proj_ws, proj = qualified.split("/", 1)
            tenants.grant_role(org, proj_ws, proj, principal, scope["role"])
        secret = store.add_token(
            name, role="member",
            ttl_days=int(ttl_days) if isinstance(ttl_days, (int, float)) else None,
            scope=scope)
        token_id = secret.split("_", 2)[1]   # the secret's alphabet includes "_"
        row = store.get_token(token_id) or {}
        _audit("create_agent_token", org=org,
               args={"name": name, "org": org, "workspace": ws,
                     "projects": scope["projects"], "role": scope["role"],
                     "ttl_days": ttl_days})
        return {
            "id": token_id,
            "name": name,
            # The composed principal, because that is the string the grants,
            # the presence roster, the claim map and the history trailer all
            # carry — a bare name here would be a second spelling of identity.
            "principal": principal,
            "role": row.get("role", "member"),
            "scope": scope,
            "expires": row.get("expires"),
            "token": secret,
            "note": "this is the only time the token is shown",
            "granted": scope["projects"],
        }

    def revoke_agent_token(token_id: str) -> dict:
        """Revoke a scoped token and drop the grants it was minted with."""
        row = store.get_token(token_id)
        if row is None:
            raise NotFoundError(f"no token {token_id!r}", {"id": token_id})
        scope = row.get("scope")
        if not scope:
            # An unscoped token is instance-wide (005a semantics), so no org
            # admin owns it and there is no tenant floor that could authorize
            # this. Refused rather than quietly widened: the instance
            # administrator's own surface is where it is revoked.
            raise ValidationError(
                f"token {token_id!r} has no scope: it is an instance-wide "
                f"token, revoked by an instance administrator with `agentcad "
                f"admin token revoke {token_id}` or DELETE "
                f"/api/auth/tokens/{token_id}.",
                {"id": token_id})
        who = _who()
        org, ws = _tenant(scope["org"], scope.get("workspace"),
                          require_workspace=False)
        require(tenants, "admin", who.client_id, org, ws)
        store.revoke_token(token_id)
        dropped = []
        # Only when no OTHER live token speaks as this principal: revoking one
        # credential must not silently disarm another that is still valid.
        if not store.live_tokens_named(row["name"]):
            for qualified in scope.get("projects") or []:
                proj_ws, _, proj = str(qualified).partition("/")
                try:
                    tenants.revoke_role(org, proj_ws, proj,
                                        f"agent:{row['name']}")
                except NotFoundError:
                    continue                 # already gone; not an error
                dropped.append(qualified)
        _audit("revoke_agent_token", org=org, args={"token_id": token_id})
        return {"id": token_id, "revoked": True, "grants_revoked": dropped,
                "note": "it stops authenticating on its next use"}

    # ---------------------------------------------------------------- roles

    def grant_role(project: str, principal: str, role: str,
                   org: str | None = None,
                   workspace: str | None = None) -> dict:
        who = _who()
        org, ws = _tenant(org, workspace, require_workspace=True)
        require(tenants, "admin", who.client_id, org, ws, project)
        key = tenants.grant_role(org, ws, project, principal,
                                 tenancy.check_role(role))
        _audit("grant_role", org=org, project=project,
               args={"project": project, "principal": key, "role": role})
        return {"org": org, "workspace": ws, "project": project,
                "principal": key, "role": role}

    def revoke_role(project: str, principal: str, org: str | None = None,
                    workspace: str | None = None) -> dict:
        who = _who()
        org, ws = _tenant(org, workspace, require_workspace=True)
        require(tenants, "admin", who.client_id, org, ws, project)
        tenants.revoke_role(org, ws, project, principal)
        key = tenancy.principal_key(principal)
        _audit("revoke_role", org=org, project=project,
               args={"project": project, "principal": key})
        return {"org": org, "workspace": ws, "project": project,
                "principal": key, "revoked": True,
                # The override goes; the org default stays. Said in the payload
                # because "revoked" on its own reads as "access removed", and
                # for a member it is not.
                "note": "the per-project override is gone; this principal "
                        "falls back to their org default role"}

    # ----------------------------------------------------------- membership

    def list_members(org: str, workspace: str | None = None) -> dict:
        who = _who()
        org, ws = _tenant(org, workspace, require_workspace=False)
        require(tenants, "view", who.client_id, org, ws)
        payload = {
            "org": org,
            "workspace": None if ws == ANY_WORKSPACE else ws,
            "members": tenants.list_members(org),
            "workspaces": tenants.list_workspaces(org),
        }
        if can(tenants, "admin", who.client_id, org, ws):
            # Tokens are a credential inventory, so only an admin sees them —
            # and never a digest or a secret, which `list_tokens` cannot return.
            payload["tokens"] = [
                dict(row) for row in store.list_tokens()
                if (row.get("scope") or {}).get("org") == org]
        return payload

    # ------------------------------------------------------------ sync stub

    def sync_status(project: str, org: str | None = None,
                    workspace: str | None = None) -> dict:
        """The shape slice 6 fills in. Documented as a stub, in the payload."""
        tenant = tenancy.current_tenant()
        if tenant is not None or org:
            org, ws = _tenant(org, workspace, require_workspace=True)
            require(tenants, "view", _who().client_id, org, ws, project)
        else:
            # A hosted instance with no tenancy at all: there is no org, so
            # there is no role to require and no audit row to write. Same rule
            # as everywhere else in PRD-005 — no tenant, old behaviour.
            org = ws = None
        return {"project": project, "org": org, "workspace": ws,
                "remote": None, "ahead": None, "behind": None,
                "note": SYNC_STUB_NOTE}

    # ------------------------------------------------------------- registry

    registry.register(Tool(
        "create_agent_token",
        "Mint a bearer token scoped to an org, a list of projects and a role "
        "(view|comment|edit|admin). The secret is returned ONCE. Requires org "
        "admin, which only a signed-in person can hold — a token cannot mint a "
        "token. The scope is also written as per-project grants for "
        "agent:<name>, so revoking the token removes its reach.",
        schema(
            {
                "name": {"type": "string",
                         "description": "composes as agent:<name>"},
                "org": {"type": "string"},
                "projects": {"type": "array",
                             "description": "project ids, or '<workspace>/"
                                            "<project>' to span workspaces"},
                "role": {"type": "string",
                         "description": "view|comment|edit|admin"},
                "workspace": {"type": "string",
                              "description": "default workspace for "
                                             "unqualified project ids"},
                "ttl_days": {"type": "integer",
                             "description": "expire after N days (default: "
                                            "never)"},
            },
            ["name", "org", "projects", "role"],
        ),
        create_agent_token,
    ))
    registry.register(Tool(
        "revoke_agent_token",
        "Revoke a scoped agent token by id and drop the per-project grants it "
        "was minted with (kept if another live token shares its name). Takes "
        "effect on the token's next request. Requires org admin.",
        schema({"token_id": {"type": "string"}}, ["token_id"]),
        revoke_agent_token,
    ))
    registry.register(Tool(
        "grant_role",
        "Grant a principal (user:<handle>, agent:<name> or a bare handle) a "
        "per-project role: view < comment < edit < admin. Replaces any "
        "existing override; may raise or lower against the org default. "
        "Requires admin on the project.",
        schema(
            {
                "project": {"type": "string"},
                "principal": {"type": "string"},
                "role": {"type": "string",
                         "description": "view|comment|edit|admin"},
                "org": {"type": "string"},
                "workspace": {"type": "string"},
            },
            ["project", "principal", "role"],
        ),
        grant_role,
    ))
    registry.register(Tool(
        "revoke_role",
        "Drop a principal's per-project role override; they fall back to their "
        "org default (which for a member is a real role, not nothing). "
        "Requires admin on the project.",
        schema(
            {
                "project": {"type": "string"},
                "principal": {"type": "string"},
                "org": {"type": "string"},
                "workspace": {"type": "string"},
            },
            ["project", "principal"],
        ),
        revoke_role,
    ))
    registry.register(Tool(
        "list_members",
        "The org's members and their default roles, its workspaces, and — for "
        "an org admin — the scoped tokens minted in it (never a secret). "
        "Requires view in the org.",
        schema({"org": {"type": "string"}, "workspace": {"type": "string"}},
               ["org"]),
        list_members,
    ))
    registry.register(Tool(
        "sync_status",
        "Whether this project has a git remote and how it compares. STUB: "
        "remote comparison lands in slice 6; today it reports remote: null "
        "with a note saying so.",
        schema(
            {"project": {"type": "string"}, "org": {"type": "string"},
             "workspace": {"type": "string"}},
            ["project"],
        ),
        sync_status,
    ))


# ---------------------------------------------------------------- whoami

def _extend_whoami(registry, module, store, tenants) -> None:
    """Add the tenancy half to the ``whoami`` ``tools_auth`` registered.

    In place, because ``ToolRegistry.register`` refuses a duplicate name and
    there is no unregister seam (the ``open_project`` note in ``CLAUDE.md``).
    The captured original is called first and its payload is never rewritten —
    only extended — so the 005a contract is a subset of this one by
    construction.
    """
    tool = registry.get("whoami")
    if tool is None or getattr(tool.handler, _WRAPPED, False):
        return
    base = tool.handler

    @functools.wraps(base)
    def whoami() -> dict:
        payload = base()
        if not tenants.list_orgs():
            # No tenancy on this instance: byte-for-byte 005a's four keys.
            return payload
        who = module.current_principal()
        principal = payload.get("principal") or ""
        scope = (store.scope_for_principal(who.name)
                 if who is not None and who.kind == "agent" else None)
        org, workspace = _current_scope(tenants, principal, scope)
        return {
            **payload,
            "org": org,
            "workspace": workspace,
            "orgs": (tenants.orgs_for(principal) if org is None
                     else sorted({*tenants.orgs_for(principal), org})),
            "roles": _roles_in(tenants, principal, org, workspace),
            # What the token was minted for, so its holder can see it without
            # an admin. `None` for a person and for a legacy token alike.
            "scope": scope,
        }

    setattr(whoami, _WRAPPED, True)
    tool.handler = whoami
    tool.description += (
        " In an org, it also reports your org and workspace, the orgs you "
        "belong to, your role on each project there, and (for a scoped token) "
        "the scope it was minted with.")


def _current_scope(tenants, principal: str, scope: dict | None):
    """``(org, workspace)`` for a ``whoami`` payload, or ``(None, None)``.

    Precedence: the request's tenant (slice 4), then a bearer's own token
    scope, then — for a person who belongs to exactly one org with exactly one
    workspace — the only answer there is. The last step is a convenience for
    the frontend session and is deliberately conservative: with two orgs (or
    two workspaces) it answers ``None`` rather than picking, because a switcher
    rendering the wrong one is worse than a switcher rendering none.
    """
    tenant = tenancy.current_tenant()
    if tenant is not None:
        return tenant
    if scope:
        return scope.get("org"), scope.get("workspace")
    orgs = tenants.orgs_for(principal)
    if len(orgs) != 1:
        return None, None
    spaces = tenants.list_workspaces(orgs[0])
    return orgs[0], (spaces[0]["id"] if len(spaces) == 1 else None)


def _roles_in(tenants, principal: str, org, workspace) -> dict:
    """``{project: role}`` for the resolved workspace — only the projects this
    principal can actually reach, so the frontend can render affordances
    without a second round trip per project."""
    from .authz import role_of

    if not org or not workspace or not tenants.has_workspace(org, workspace):
        return {}
    roles = {}
    for proj in tenants.list_projects(org, workspace):
        role = role_of(tenants, principal, org, workspace, proj)
        if role:
            roles[proj] = role
    return roles
