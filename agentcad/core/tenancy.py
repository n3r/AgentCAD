"""Tenancy: orgs -> workspaces -> projects, and the per-request tenant.

Two things live here, and they are deliberately in one module because the
second is meaningless without the first:

1. :class:`TenancyStore` — the **membership document**, one more atomically
   written JSON file beside PRD-005a's four:
   ``<state>/auth/orgs.json``. Shape (design spec §1)::

       {"orgs": {"<org>": {
           "label": "Acme Robotics",
           "members": {"<handle>": "<org_role>"},
           "workspaces": {"<ws>": {
               "label": "Mechanical",
               "projects": {"<proj>": {"roles": {"<principal>": "<role>"}}}}}}}}

   The only addition to the spec's shape is a workspace ``label``, the
   sibling of the org's: the switcher (slice 8) renders workspaces and would
   otherwise have nothing but the id to show. It is optional everywhere and
   every reader falls back to the id, so a document written without it is
   read identically.

2. The **per-request tenant** — ``tenant_var``, a ContextVar holding
   ``(org, workspace)``, plus the two functions everything else composes from
   it: :func:`qualified` (the tenant-qualified *name* a lock, a claim or an
   event routes on) and :func:`tenant_root` (the tenant-qualified *path*
   ``ProjectStore``'s resolver seam will answer with). This is the
   ``branch_resolver`` precedent exactly: one process, one service, one
   kernel pool, and tenancy arriving as ambient per-request state rather than
   as a fleet of stores (design spec, Decision 2).

**Local mode is the absence of a tenant, everywhere.** No tenant set means
:func:`qualified` returns the bare project name and :func:`tenant_root`
returns ``None`` — byte-for-byte today's behaviour (FR4/AC7). Nothing in this
module is reached at all until something sets ``tenant_var``, which only
hosted mode does.

**Why the lock is authstore's and not a second one.** ``fcntl.flock`` is per
*open file description*, so two file descriptors on one lock file inside one
process block against each other for ever. ``orgs.json`` lives in the same
directory as ``users.json``, so a private guard registry here would deadlock
the first time a tenancy write happened inside an ``AuthStore._scope`` (or
the reverse) — not a theoretical ordering, since granting a role and creating
the account it names belong in one admin operation. So this module **imports
``authstore._guard_for``** and shares the depth-counted guard: same
``threading.RLock``, same single flock handle on the same ``<root>/.lock``,
reentrant across both modules. ``authstore.py`` is not edited to make this
work; the sharing is entirely on this side.

Nothing here imports geometry, and no ``AgentCADService`` constructs it —
tenancy is app-layer state, exactly like identity.
"""

from __future__ import annotations

import json
import os
import re
import secrets
from contextlib import contextmanager
from contextvars import ContextVar, Token
from pathlib import Path

# The lock registry and the lock file NAME are authstore's, on purpose — see
# the module docstring. Nothing else is borrowed: the documents, the cache and
# the validation are this module's own.
from .authstore import LOCK_FILE, _guard_for
from .model import ID_RE, ConflictError, NotFoundError, ValidationError

try:  # pragma: no cover - exercised by the portability suite, not by CI's mac
    import fcntl
except ImportError:  # Windows
    fcntl = None  # type: ignore[assignment]

#: The one document this module owns. Named here so a test can assert it is
#: never the lock file, the way `test_authstore` does for the other four.
ORGS = "orgs.json"

#: Org, workspace and project names all validate through the **existing**
#: ``ID_RE`` grammar (design spec §1: "names validate through the existing
#: ``ID_RE`` grammar per level"). One grammar for all three levels is what
#: makes ``qualified()`` unambiguous: ``a/b/c`` can be split back, because no
#: level may contain a slash, and it is what keeps a tenant path a plain
#: directory name on every filesystem.
NAME_RE = ID_RE

#: A principal is the string the product already carries everywhere — the
#: ``locks.set_client_id`` identity minus its device suffix:
#: ``user:<handle>`` for a person, ``agent:<name>`` for a token. The bounds
#: mirror ``authstore.HANDLE_RE``/``NAME_RE`` (1-32 of the same alphabet), so
#: a principal that can exist as an account can always be written here.
PRINCIPAL_RE = re.compile(r"^(user|agent):[a-z0-9][a-z0-9._-]{0,31}$")

#: The role vocabulary, weakest first. Kept in ``authz`` as ``ROLE_ORDER``
#: (the ladder is authz's contract); imported *from* there would be a cycle,
#: so the store validates against this tuple and `authz` pins the two equal
#: by test.
ROLES = ("view", "comment", "edit", "admin")

#: An **org** role is the member's default role on every project in the org.
#: Same vocabulary, and that is the point: "anya is an editor in acme" needs
#: no second ladder to mean something on a project she has no override on.
#: ``admin`` at org level additionally means *org admin* — the person who may
#: manage members, and (see ``authz.role_of``) an admin on every project in
#: the org regardless of overrides.
ORG_ROLES = ROLES

#: Hosted storage roots are ``<projects_dir>/orgs/<org>/<ws>/<proj>`` (FR5).
#: The single ``orgs`` segment keeps the tenant tree from ever colliding with
#: a local-mode project directory, because ``orgs`` is a legal project name
#: and a flat root could otherwise contain one.
ORGS_DIRNAME = "orgs"


# ------------------------------------------------------------- the tenant

#: ``(org, workspace)``. A 2-tuple rather than a dataclass because it is read
#: on hot paths (every lock check, every event publish) and compared, hashed
#: and unpacked far more often than it is constructed.
Tenant = tuple[str, str]

#: Set per request by ``security.guard`` beside ``set_client_id`` (the
#: sanctioned identity seam), from the session's active workspace, the
#: ``X-Agentcad-Workspace`` header, or a bearer token's scope. ``None`` is
#: local mode and is the default, so every consumer's untenanted branch is
#: the one that runs until hosted mode says otherwise.
tenant_var: ContextVar[Tenant | None] = ContextVar(
    "agentcad_tenant", default=None)


def current_tenant() -> Tenant | None:
    """This request's ``(org, workspace)``, or ``None`` for local mode."""
    return tenant_var.get()


def set_tenant(tenant: Tenant | None) -> Token:
    """Set (or clear, with ``None``) the ambient tenant; returns the reset token.

    **Validates before setting.** The tenant composes into filesystem paths
    (:func:`tenant_root`) and into lock keys (:func:`qualified`), so a caller
    that resolved junk must fail here, loudly, rather than have the junk
    become a directory name three layers down. ``security`` resolves the
    tenant from membership it has already read, so a refusal is a bug and
    reads like one.
    """
    if tenant is None:
        return tenant_var.set(None)
    if (not isinstance(tenant, (tuple, list)) or len(tenant) != 2):
        raise ValidationError(
            "a tenant is an (org, workspace) pair",
            {"tenant": list(tenant) if isinstance(tenant, (tuple, list))
             else tenant if isinstance(tenant, str) else None})
    org, workspace = tenant
    return tenant_var.set((check_name(org, "org"),
                           check_name(workspace, "workspace")))


def reset_tenant(token: Token) -> None:
    """Restore whatever the tenant was before the matching :func:`set_tenant`.

    A pair rather than "set it back to None": ASGI reuses the context of the
    surrounding task in places, and a middleware that reset to ``None``
    instead of to the token would erase an outer tenant it did not set.
    """
    tenant_var.reset(token)


@contextmanager
def tenant_scope(tenant: Tenant | None):
    """``with tenant_scope(("acme", "main")):`` — set, then restore.

    The form tests and wrappers want; ``set_tenant``/``reset_tenant`` stay
    exposed because a middleware sets in one function and resets in another.
    """
    token = set_tenant(tenant)
    try:
        yield
    finally:
        reset_tenant(token)


def qualified(proj: str) -> str:
    """``org/ws/proj`` when a tenant is set, else ``proj`` unchanged.

    The **name** half of tenancy. Turn locks, presence rosters, claim maps,
    undo cursors and event routing all key on a project name today, and two
    orgs may both have a project called ``widget``; without this they would
    share one turn lock and see each other's presence. Applied at the wrapper
    layer (design spec §2), never inside the stores themselves — which is why
    local mode gets its identity function back and nothing downstream learns
    that tenancy exists.

    Not validated: the caller's project name has already been through
    ``validate_id`` at every route and tool boundary, and this runs on the hot
    path of every write check.
    """
    tenant = tenant_var.get()
    if tenant is None:
        return proj
    org, workspace = tenant
    return f"{org}/{workspace}/{proj}"


def tenant_root(projects_dir: Path | str) -> Path | None:
    """``<projects_dir>/orgs/<org>/<ws>`` when a tenant is set, else ``None``.

    The **path** half, and the exact value ``ProjectStore``'s future
    ``root_resolver`` seam returns (slice 4): ``None`` means "today's root",
    which is what makes local mode a property of the resolver answering
    nothing rather than of a branch at every path composition.

    Creates nothing. A resolver that made directories would materialise a
    tenant root for a request that was about to be refused, and the store
    already owns creation of the roots it writes into.
    """
    tenant = tenant_var.get()
    if tenant is None:
        return None
    org, workspace = tenant
    return Path(projects_dir) / ORGS_DIRNAME / org / workspace


# --------------------------------------------------------------- validation

def check_name(value: object, what: str) -> str:
    """An org / workspace / project name, or ``ValidationError``.

    ``model.validate_id``'s grammar with a message that names the level, so
    "invalid workspace" never reads as "invalid project".
    """
    if not isinstance(value, str) or not NAME_RE.match(value):
        raise ValidationError(
            f"invalid {what} name {value!r}: must match [a-z][a-z0-9_]{{0,39}} "
            f"— the same grammar as a project id, so that a tenant is one "
            f"path segment and 'org/ws/proj' can always be split back.",
            {what: value if isinstance(value, str) else None},
        )
    return value


def principal_key(principal: object) -> str:
    """The canonical roles-map key for a principal.

    ``user:nikita`` and ``agent:ci`` pass through; a **bare handle** is read
    as a person (``nikita`` -> ``user:nikita``), because that is what an
    operator types and a grant that silently landed under a second spelling
    would be a role nobody holds. A device suffix is stripped
    (``user:nikita/browser:7f3a1b2c`` -> ``user:nikita``): roles belong to
    people, not to browser tabs, and PRD-005a composes the suffix in exactly
    one place, ``Principal.client_id``.
    """
    if not isinstance(principal, str) or not principal:
        raise ValidationError(
            "a principal is 'user:<handle>', 'agent:<name>' or a bare handle",
            {"principal": principal if isinstance(principal, str) else None})
    key = principal.split("/", 1)[0]
    if ":" not in key:
        key = f"user:{key}"
    if not PRINCIPAL_RE.match(key):
        raise ValidationError(
            f"invalid principal {principal!r}: 'user:<handle>' or "
            f"'agent:<name>', 1-32 characters of a-z, 0-9, dot, underscore or "
            f"hyphen after the prefix.",
            {"principal": principal})
    return key


def handle_of(principal: object) -> str | None:
    """The org-member handle behind a principal, or ``None`` for an agent.

    Membership is a **person's** property: a token is minted by someone, is
    scoped by its own record (FR3), and is never an org member in its own
    right. So ``agent:ci`` has no org default and reaches a project only
    through an explicit per-project grant — which is the conservative
    direction, and is tested as such.
    """
    key = principal_key(principal)
    kind, _, name = key.partition(":")
    return name if kind == "user" else None


def check_role(role: object, *, what: str = "role") -> str:
    if role not in ROLES:
        raise ValidationError(
            f"{what} must be one of {', '.join(ROLES)} (weakest first)",
            {what: role if isinstance(role, str) else None})
    return str(role)


# ------------------------------------------------------------ pure helpers

def _as_dict(value: object) -> dict:
    """``value`` if it is an object, else ``{}``.

    Every read below goes through this. ``orgs.json`` is JSON that a hand
    edit, a restore or a half-finished migration can shape, and PRD-012's
    lesson (``PartRecord.config_params``) is that resolution has to be
    **total over the value**: a malformed row must resolve as *nothing*, not
    raise inside an authorization check. Failing closed is automatic here —
    ``{}`` grants no role — and that is the property, not a side effect.
    """
    return value if isinstance(value, dict) else {}


def _set_in(doc: dict, path: tuple[str, ...], value) -> dict:
    """A copy of *doc* with *path* set, copying every dict on the way down.

    The cached parse is shared with every reader (`_read` hands out the same
    object), so a read-modify-write must never mutate it in place: a write
    that then failed would leave the cache holding a change that is not on
    disk. authstore rebuilds one level with ``{**row, field: value}``; the
    tenancy document is four levels deep, so the same discipline is a
    function.
    """
    if not path:
        return value
    head, rest = path[0], path[1:]
    copy = dict(doc)
    copy[head] = _set_in(_as_dict(copy.get(head)), rest, value)
    return copy


def _del_in(doc: dict, path: tuple[str, ...]) -> dict:
    """A copy of *doc* with the leaf at *path* removed (missing leaf: a copy)."""
    if not path:
        return doc
    head, rest = path[0], path[1:]
    copy = dict(doc)
    if not rest:
        copy.pop(head, None)
        return copy
    copy[head] = _del_in(_as_dict(copy.get(head)), rest)
    return copy


# ------------------------------------------------------------------- store

class TenancyStore:
    """``orgs.json``, behind authstore's lock, with atomic writes.

    Constructed on the **same root** as :class:`~agentcad.core.authstore.AuthStore`
    (``<state>/auth``) — that shared root is what makes the shared guard the
    right one, and it is why this document sits with the identity documents
    rather than in a directory of its own.
    """

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        # 0700 at creation, authstore's reason: a permission widened and
        # narrowed back is a promise we cannot keep about who read it between.
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._guard = _guard_for(self.root.resolve())
        #: ``(mtime_ns, size, inode) -> parsed``. One entry, but kept in the
        #: authstore shape so the two modules read the same way. The stat key
        #: is what makes a **second process's** write (``docker compose exec
        #: agentcad admin org ...``) visible with no restart.
        self._cache: tuple[tuple, dict] | None = None

    # ------------------------------------------------------------- plumbing

    @contextmanager
    def _scope(self):
        """Serialise a read-modify-write in-process **and** across processes.

        Byte-for-byte ``AuthStore._scope``'s body, over the **same guard
        object** — see the module docstring for why sharing is not an
        optimisation but the only correct choice. The flock is advisory and
        best-effort exactly as it is there: no ``fcntl``, or a filesystem that
        refuses it, degrades to the in-process lock alone, which is stated
        rather than hidden.
        """
        guard = self._guard
        with guard.lock:
            outermost = guard.depth == 0
            if outermost and fcntl is not None:
                handle = open(self.root / LOCK_FILE, "a+b")
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                except OSError:
                    handle.close()          # unsupported filesystem: degrade
                    handle = None
                guard.handle = handle
            guard.depth += 1
            try:
                yield
            finally:
                guard.depth -= 1
                if guard.depth == 0 and guard.handle is not None:
                    try:
                        fcntl.flock(guard.handle.fileno(), fcntl.LOCK_UN)
                    finally:
                        guard.handle.close()
                        guard.handle = None

    def _read(self, *, fresh: bool = False) -> dict:
        """Parse ``orgs.json``, reusing the cached parse while its stat is equal.

        ``fresh=True`` for every read a write follows: the stat key is a good
        cross-process discriminator, not a proof, and a read-modify-write from
        a stale parse drops the other writer's row. Inside ``_scope`` being
        exact is free.

        A document that will not parse **raises** rather than reading as
        empty. "Treat garbage as no orgs" would turn a corrupt file into an
        instance where nobody is a member — which the next ``create_org``
        would cheerfully write over the top of, and which an authz check would
        read as a clean deny that hides the real problem.
        """
        path = self.root / ORGS
        try:
            st = os.stat(path)
        except FileNotFoundError:
            self._cache = None
            return {}
        key = (st.st_mtime_ns, st.st_size, st.st_ino)
        if not fresh and self._cache is not None and self._cache[0] == key:
            return self._cache[1]
        raw = path.read_bytes()
        try:
            doc = json.loads(raw.decode("utf-8"))
        except (ValueError, RecursionError, UnicodeDecodeError) as exc:
            # RecursionError is not a ValueError — packages/_json.py's lesson,
            # and the reason it is named here rather than assumed.
            raise ValidationError(
                f"the tenancy document {ORGS} is unreadable: {exc}. Restore it "
                f"from a backup of the state directory.",
                {"document": ORGS},
            ) from exc
        if not isinstance(doc, dict):
            raise ValidationError(
                f"the tenancy document {ORGS} is not an object",
                {"document": ORGS})
        self._cache = (key, doc)
        return doc

    def _write(self, doc: dict) -> None:
        """Atomic, 0600, staged through a **random** name.

        Changelog 0181's lesson, not decoration: a fixed ``.tmp`` lets two
        writers interleave their bytes into one staging file and each
        ``os.replace`` the mixture into place — corruption, not a lost update.
        """
        path = self.root / ORGS
        data = json.dumps(doc, indent=2, sort_keys=True).encode("utf-8")
        tmp = path.with_name(f"{ORGS}.{secrets.token_hex(8)}.tmp")
        try:
            fd = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            try:
                os.write(fd, data)
            finally:
                os.close(fd)
            os.replace(tmp, path)
        except BaseException:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        st = os.stat(path)
        self._cache = ((st.st_mtime_ns, st.st_size, st.st_ino), doc)

    # ------------------------------------------------------------ org reads

    def list_orgs(self) -> list[dict]:
        """``[{id, label, members, workspaces}]``, sorted by id.

        ``members`` and ``workspaces`` are **counts**, not the maps: this is
        the switcher's payload and a person with a hundred projects does not
        need them enumerated to pick an org.
        """
        orgs = _as_dict(self._read().get("orgs"))
        return [
            {
                "id": org,
                "label": _label(_as_dict(row), org),
                "members": len(_as_dict(row.get("members"))),
                "workspaces": len(_as_dict(row.get("workspaces"))),
            }
            for org, row in sorted(orgs.items())
            if isinstance(row, dict)
        ]

    def get_org(self, org: str) -> dict:
        """The org's row, or ``NotFoundError``. A **copy**, so a caller
        holding it cannot edit the cached parse."""
        row = _as_dict(_as_dict(self._read().get("orgs")).get(org))
        if not row:
            raise NotFoundError(f"no org {org!r}", {"org": org})
        return json.loads(json.dumps(row))

    def has_org(self, org: str) -> bool:
        return isinstance(_as_dict(self._read().get("orgs")).get(org), dict)

    def list_members(self, org: str) -> list[dict]:
        """``[{handle, role}]`` sorted by handle. A member whose stored role
        is not in the vocabulary is reported as ``None`` rather than dropped —
        an operator must be able to *see* the row they have to fix."""
        row = self.get_org(org)
        return [
            {"handle": handle, "role": role if role in ORG_ROLES else None}
            for handle, role in sorted(_as_dict(row.get("members")).items())
        ]

    def org_role(self, org: str, handle: str) -> str | None:
        """A member's org role, or ``None`` — for a non-member, an unknown org
        **and a malformed row alike**. Never raises: this is on the
        authorization path, where an exception is a 500 that says the store
        was interesting."""
        try:
            key = handle_of(handle)
        except ValidationError:
            return None
        if key is None:
            return None                      # an agent is never an org member
        row = _as_dict(_as_dict(self._read().get("orgs")).get(org))
        role = _as_dict(row.get("members")).get(key)
        return role if role in ORG_ROLES else None

    def orgs_for(self, principal: str) -> list[str]:
        """Every org a **person** is a member of, sorted. The switcher's list;
        an agent principal answers ``[]`` (see :func:`handle_of`)."""
        handle = handle_of(principal)
        if handle is None:
            return []
        orgs = _as_dict(self._read().get("orgs"))
        return sorted(
            org for org, row in orgs.items()
            if _as_dict(_as_dict(row).get("members")).get(handle) in ORG_ROLES
        )

    # ----------------------------------------------------- workspace reads

    def list_workspaces(self, org: str) -> list[dict]:
        row = self.get_org(org)
        return [
            {"id": ws, "label": _label(_as_dict(entry), ws),
             "projects": len(_as_dict(_as_dict(entry).get("projects")))}
            for ws, entry in sorted(_as_dict(row.get("workspaces")).items())
            if isinstance(entry, dict)
        ]

    def has_workspace(self, org: str, workspace: str) -> bool:
        return isinstance(self._workspace_row(org, workspace), dict)

    def list_projects(self, org: str, workspace: str) -> list[str]:
        """The project names the tenancy document knows about, sorted.

        Membership bookkeeping, **not** the filesystem: a project directory
        with no entry here is invisible to authz (nobody holds a role on it)
        and slice 4's ``list_projects`` intersects the two. Stated because
        "the store lists it" and "tenancy lists it" being the same set is a
        property of the write paths, not of this read.
        """
        row = self._workspace_row(org, workspace)
        if row is None:
            raise NotFoundError(
                f"no workspace {workspace!r} in org {org!r}",
                {"org": org, "workspace": workspace})
        return sorted(k for k in _as_dict(row.get("projects")) if isinstance(k, str))

    def has_project(self, org: str, workspace: str, proj: str) -> bool:
        return isinstance(self._project_row(org, workspace, proj), dict)

    def project_roles(self, org: str, workspace: str, proj: str) -> dict[str, str]:
        """The per-project overrides: ``{principal: role}``, malformed entries
        dropped. ``{}`` for a project with no overrides **and** for one the
        document does not know — the difference is :meth:`has_project`'s to
        report, and an authz read must not raise for either."""
        row = self._project_row(org, workspace, proj)
        if row is None:
            return {}
        return {
            key: role for key, role in _as_dict(row.get("roles")).items()
            if isinstance(key, str) and role in ROLES
        }

    def _workspace_row(self, org: str, workspace: str) -> dict | None:
        row = _as_dict(_as_dict(_as_dict(self._read().get("orgs")).get(org))
                       .get("workspaces")).get(workspace)
        return row if isinstance(row, dict) else None

    def _project_row(self, org: str, workspace: str, proj: str) -> dict | None:
        row = _as_dict(self._workspace_row(org, workspace) or {})
        entry = _as_dict(row.get("projects")).get(proj)
        return entry if isinstance(entry, dict) else None

    # ----------------------------------------------------------- org writes

    def create_org(self, org: str, label: str | None = None,
                   admin: str | None = None) -> None:
        """Create an org, optionally with its first admin.

        ``admin`` exists so the bootstrap is one atomic write: an org created
        empty and then given a member is, in between, an org **nobody** can
        administer, and the window is exactly the one an interrupted
        ``agentcad admin`` run lands in.
        """
        org = check_name(org, "org")
        label = _check_label(label, org)
        handle = handle_of(admin) if admin is not None else None
        if admin is not None and handle is None:
            raise ValidationError(
                "an org admin is a person, not an agent token: pass a handle "
                "or 'user:<handle>'. A token reaches a project through its "
                "own scope and per-project grants (FR3).",
                {"admin": admin})
        with self._scope():
            doc = self._read(fresh=True)
            if org in _as_dict(doc.get("orgs")):
                raise ConflictError(
                    f"org {org!r} already exists. Add members to it rather "
                    f"than recreating it — recreating would silently drop "
                    f"every membership and role it holds.",
                    {"org": org})
            row = {
                "label": label,
                "members": {handle: "admin"} if handle else {},
                "workspaces": {},
            }
            self._write(_set_in(doc, ("orgs", org), row))

    def add_member(self, org: str, handle: str, role: str = "view") -> None:
        """Add a person to an org with their default role.

        ``view`` by default: the weakest rung. A membership that arrived with
        edit rights because the caller omitted an argument is the failure mode
        worth designing against, and the ladder's floor is the only default
        that cannot surprise anybody.
        """
        key = _member_handle(handle)
        role = check_role(role, what="org role")
        with self._scope():
            doc = self._read(fresh=True)
            members = _as_dict(self._org_row(doc, org).get("members"))
            if key in members:
                raise ConflictError(
                    f"{key!r} is already a member of {org!r}; use "
                    f"set_org_role to change their role.",
                    {"org": org, "handle": key})
            self._write(_set_in(doc, ("orgs", org, "members", key), role))

    def set_org_role(self, org: str, handle: str, role: str) -> None:
        """Change a member's org role. The member must exist — a silent
        create here would make a typo'd handle a role held by nobody."""
        key = _member_handle(handle)
        role = check_role(role, what="org role")
        with self._scope():
            doc = self._read(fresh=True)
            if key not in _as_dict(self._org_row(doc, org).get("members")):
                raise NotFoundError(
                    f"{key!r} is not a member of {org!r}",
                    {"org": org, "handle": key})
            self._write(_set_in(doc, ("orgs", org, "members", key), role))

    def remove_member(self, org: str, handle: str) -> None:
        """Remove a person from an org.

        Their **per-project overrides survive**, deliberately: a grant is a
        statement about a project, membership is a statement about an org, and
        silently rewriting every project in the org from a membership change
        would be a mass edit nobody asked for. What it costs is that
        re-adding the person restores their old grants — which is also what an
        operator who removed them by mistake wants. ``authz.role_of`` still
        answers for the overrides, so a deliberate removal that must reach the
        projects revokes there too; the CLI (slice 5) says so.
        """
        key = _member_handle(handle)
        with self._scope():
            doc = self._read(fresh=True)
            if key not in _as_dict(self._org_row(doc, org).get("members")):
                raise NotFoundError(
                    f"{key!r} is not a member of {org!r}",
                    {"org": org, "handle": key})
            self._write(_del_in(doc, ("orgs", org, "members", key)))

    # ---------------------------------------------------- workspace writes

    def create_workspace(self, org: str, workspace: str,
                         label: str | None = None) -> None:
        workspace = check_name(workspace, "workspace")
        label = _check_label(label, workspace)
        with self._scope():
            doc = self._read(fresh=True)
            row = self._org_row(doc, org)
            if workspace in _as_dict(row.get("workspaces")):
                raise ConflictError(
                    f"workspace {workspace!r} already exists in org {org!r}",
                    {"org": org, "workspace": workspace})
            self._write(_set_in(
                doc, ("orgs", org, "workspaces", workspace),
                {"label": label, "projects": {}}))

    def delete_workspace(self, org: str, workspace: str) -> None:
        """Forget a workspace's tenancy entry.

        **Deletes no geometry.** Nothing in this module touches
        ``<projects_dir>``: the document is membership bookkeeping, and a
        store that deleted a customer's parts as a side effect of a role
        edit would be the worst bug this feature could ship. Removing the
        entry makes the workspace unreachable through authz, which is the
        operation an operator is asking for; reclaiming the bytes is a
        separate, explicit act.
        """
        with self._scope():
            doc = self._read(fresh=True)
            self._workspace(doc, org, workspace)        # 404 if absent
            self._write(_del_in(doc, ("orgs", org, "workspaces", workspace)))

    # ------------------------------------------------------ project writes

    def add_project(self, org: str, workspace: str, proj: str) -> None:
        """Register a project under a workspace (roles empty).

        Called by slice 4's create path *after* the store has made the
        directory, so a tenancy entry never points at nothing. Idempotence is
        deliberately absent: a second call conflicts, because "create a
        project that already exists" is a name collision inside the workspace
        (FR5's uniqueness) and answering it with success would let one member
        silently join another's project.
        """
        proj = check_name(proj, "project")
        with self._scope():
            doc = self._read(fresh=True)
            row = self._workspace(doc, org, workspace)
            if proj in _as_dict(row.get("projects")):
                raise ConflictError(
                    f"project {proj!r} already exists in {org}/{workspace}",
                    {"org": org, "workspace": workspace, "project": proj})
            self._write(_set_in(
                doc, ("orgs", org, "workspaces", workspace, "projects", proj),
                {"roles": {}}))

    def remove_project(self, org: str, workspace: str, proj: str) -> None:
        """Forget a project's tenancy entry, roles and all. Deletes no
        geometry, for :meth:`delete_workspace`'s reason."""
        with self._scope():
            doc = self._read(fresh=True)
            self._project(doc, org, workspace, proj)    # 404 if absent
            self._write(_del_in(
                doc,
                ("orgs", org, "workspaces", workspace, "projects", proj)))

    def grant_role(self, org: str, workspace: str, proj: str,
                   principal: str, role: str) -> str:
        """Set a per-project override for *principal*; returns the stored key.

        An override **replaces** whatever was there (no conflict on a second
        grant): the operation an operator runs is "anya is an editor here",
        and making them revoke first would be a footgun with no upside. Both
        directions are legal — an override may raise a viewer to edit or hold
        an org editor down to view. The one thing it cannot do is bind an org
        admin, and that asymmetry lives in ``authz.role_of``, where it can be
        documented once with its reason.
        """
        key = principal_key(principal)
        role = check_role(role)
        with self._scope():
            doc = self._read(fresh=True)
            self._project(doc, org, workspace, proj)    # 404 if absent
            self._write(_set_in(
                doc,
                ("orgs", org, "workspaces", workspace, "projects", proj,
                 "roles", key),
                role))
        return key

    def revoke_role(self, org: str, workspace: str, proj: str,
                    principal: str) -> None:
        """Drop a per-project override.

        The principal falls back to their **org default**, which for a member
        is a real role and not nothing — revoking an override is not the same
        as revoking access, and the CLI's wording (slice 5) says so.
        """
        key = principal_key(principal)
        with self._scope():
            doc = self._read(fresh=True)
            row = self._project(doc, org, workspace, proj)
            if key not in _as_dict(row.get("roles")):
                raise NotFoundError(
                    f"{key!r} holds no role override on {proj!r}",
                    {"org": org, "workspace": workspace, "project": proj,
                     "principal": key})
            self._write(_del_in(
                doc,
                ("orgs", org, "workspaces", workspace, "projects", proj,
                 "roles", key)))

    # ---------------------------------------------- write-path row lookups
    # These raise; the read-path accessors above never do. The split is the
    # point: a write against a workspace that is not there is an operator
    # error worth a 404, while an authorization read against the same
    # workspace is simply "no role".

    def _org_row(self, doc: dict, org: str) -> dict:
        row = _as_dict(doc.get("orgs")).get(org)
        if not isinstance(row, dict):
            raise NotFoundError(f"no org {org!r}", {"org": org})
        return row

    def _workspace(self, doc: dict, org: str, workspace: str) -> dict:
        row = _as_dict(self._org_row(doc, org).get("workspaces")).get(workspace)
        if not isinstance(row, dict):
            raise NotFoundError(
                f"no workspace {workspace!r} in org {org!r}",
                {"org": org, "workspace": workspace})
        return row

    def _project(self, doc: dict, org: str, workspace: str, proj: str) -> dict:
        row = _as_dict(self._workspace(doc, org, workspace).get("projects")).get(proj)
        if not isinstance(row, dict):
            raise NotFoundError(
                f"no project {proj!r} in {org}/{workspace}",
                {"org": org, "workspace": workspace, "project": proj})
        return row


def _label(row: dict, fallback: str) -> str:
    label = row.get("label")
    return label if isinstance(label, str) and label.strip() else fallback


def _check_label(label: object, fallback: str) -> str:
    """A display name. Free text, because a company is called what it is
    called — bounded only so the document cannot be grown without limit by
    a caller who can already write it."""
    if label is None:
        return fallback
    if not isinstance(label, str) or not label.strip() or len(label) > 200:
        raise ValidationError(
            "a label is 1-200 characters of free text",
            {"label": label if isinstance(label, str) else None})
    return label.strip()


def _member_handle(handle: object) -> str:
    """A member key: the bare handle, and never an agent.

    ``members`` is keyed on the handle (design spec §1) while per-project
    ``roles`` are keyed on the full principal — two spellings, one document,
    which is worth stating: an org member is a person by construction, and a
    per-project grant has to be able to name a token.
    """
    key = handle_of(handle)
    if key is None:
        raise ValidationError(
            "org membership is for people: pass a handle or 'user:<handle>'. "
            "An agent token reaches a project through its own scope and "
            "per-project grants (FR3), never through org membership.",
            {"handle": handle if isinstance(handle, str) else None})
    return key
