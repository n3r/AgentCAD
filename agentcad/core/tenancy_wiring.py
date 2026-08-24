"""Where tenancy meets the running service: the wrappers, and no core edits.

``core/tenancy.py`` owns the model and the ambient ``(org, workspace)``;
``core/authz.py`` owns the ladder. Neither is reached by anything until this
module installs the wrappers that consult them, which is the whole reason it
exists as a separate file: the *decisions* stay in two small modules that
nothing imports on a hot path, and the *plumbing* — capture the current
callable, wrap it, put it back, mark it with a sentinel — lives here, once.

What gets installed (design spec §2 and §3):

0. **``ProjectStore.create``** — ``edit`` in the workspace before a
   project may be made in it, and the tenancy document's entry for it
   afterwards. The one write the write guard below cannot see.
1. **``ProjectStore.root_resolver``** — the storage half. Every path the store
   composes lands under ``<root>/orgs/<org>/<ws>/`` while a tenant is set, so
   two orgs' ``widget`` projects are two directories and neither can address
   the other's.
2. **``ProjectStore.lock_key``** — the *name* half, and deliberately broader
   than the design's "the write-guard wrapper re-keys the turnlock". Turn
   locks, per-part claims, presence rosters, undo stacks, build badges, the
   search index and the navigation roll-up **all** key on this one function's
   answer (see its call sites), so qualifying it here is what makes a
   cross-tenant name collision impossible rather than making it impossible in
   one of seven places.
3. **The write guard** — ``edit`` on every persistent mutation, in the
   ``presence.ensure_claim_guard`` shape, and re-installed from
   ``tools_versioning.install_write_guard``'s own seam because that function
   *replaces* the guard rather than wrapping it (PRD-008's lesson, paid for
   once already).
4. **``ToolRegistry.call``** — the floor for the tool surface, which is the
   HTTP tool route, the chat engine and MCP at once, because all three call
   the same registry object; and, outside the floor, ``audit.tap_registry``,
   so every mutating call by any of those three lands one row in the org's
   audit log (FR12). The tap is **outermost** on purpose: a refused call is
   recorded with ``outcome: "permission_error"``, and "who tried what" is
   exactly the question an audit log is read for.
5. **``EventBus.publish``/``subscribe``** — the publish side stamps
   ``event["tenant"]``; the subscribe side binds the *subscriber's* tenant at
   subscribe time and drops foreign events on the way into that subscriber's
   queue. See :func:`_install_bus` for why the filter lives on the queue.
6. **``routes_sync``'s two seams** — slice 2 left ``require_role`` and
   ``resolve_project`` as ``None`` module attributes; they become
   ``authz.require`` and the tenant-rooted path here.

**Everything no-ops without a tenant.** Every wrapper's first act is to read
``tenancy.current_tenant()`` and, on ``None``, to do exactly what the callable
it wrapped did. Local mode never sets one; a hosted instance with no orgs
never resolves one. That is AC7, and it is a property of these functions
rather than of a test we have to keep passing.

Idempotent, and reversible: :func:`install` may be called twice (it is, in
tests) and :func:`uninstall` restores every module-level attribute this file
touched, which matters because two of them — ``tools_versioning``'s function
and ``routes_sync``'s seams — are process-global while a service is not.
"""

from __future__ import annotations

import sys
import weakref
from pathlib import Path
from typing import Callable

from . import audit, authz, locks, tenancy
from .model import AppError, AuthError, NotFoundError

# --------------------------------------------------------------- sentinels
#
# One attribute name per seam, the `tools_structure` idiom: a wrapper marks
# itself, and installing again finds the mark and returns. Distinct names so
# that finding one seam already wrapped never makes us skip another.
_ROOT = "_agentcad_tenant_root"
_LOCK_KEY = "_agentcad_tenant_lock_key"
_WRITE_GUARD = "_agentcad_tenant_write_guard"
_INSTALL_WRITE_GUARD = "_agentcad_tenant_install_write_guard"
_CREATE = "_agentcad_tenant_create"
_CALL = "_agentcad_tenant_call"
_PUBLISH = "_agentcad_tenant_publish"
_SUBSCRIBE = "_agentcad_tenant_subscribe"


# ------------------------------------------------------------ tool floors

#: Tools that read, measure, render or export and change no authored state.
#: They take the ``view`` floor; **everything not named here takes ``edit``**,
#: which is the direction a mistake has to fail in — a tool added tomorrow is
#: refused to a viewer until somebody decides otherwise, rather than reachable
#: by one until somebody notices.
#:
#: "Changes no authored state" is the test, not "writes no bytes": a drawing,
#: an export, a check report and a proposal packet are *derived* data, and a
#: viewer who can read the geometry can already produce them. What they may
#: not do is change the manifest, a script, a branch, a release or a package —
#: and if one of these tools ever tried, the write guard below would refuse it
#: anyway, which is what makes this list a policy rather than a promise.
READ_ONLY_TOOLS: frozenset[str] = frozenset({
    # projects, parts, assemblies
    "list_projects", "get_project", "get_part", "get_assembly", "get_metrics",
    "get_mesh_summary", "get_turn", "get_usage", "get_history", "part_template",
    "search_parts", "face_info", "sketch_plane", "get_part_pmi",
    # analysis (kernel reads: they build, they do not author)
    "analyze_part", "check_interference", "sweep_motion", "tolerance_stackup",
    "fem_static", "fem_modal", "fem_thermal", "solve_sketch",
    # rendering and derived artefacts
    "render_view", "generate_drawing", "get_drawing_fields", "flat_pattern",
    "export_part", "export_assembly", "export_bom", "export_urdf",
    # bill of materials, releases, versions, history
    "get_bom", "get_release", "list_releases", "list_versions", "branch_list",
    "project_history", "merge_status",
    # configurations, specs, checks
    "list_configs", "list_specs", "get_project_specs", "run_specs",
    "run_checks",
    # review surfaces that only read
    "list_comments", "proposal_get", "proposal_list", "proposal_render",
    "list_notifications",
    # materials, packages, skills
    "list_materials", "get_material", "find_materials", "hole_standards",
    "list_packages", "search_packages", "list_skills", "load_skill",
    # NB: ``validate_package`` and ``package_from_step`` are NOT here. Both take
    # a caller-supplied host path and write/scaffold at ``dest`` — a filesystem
    # primitive, not "derived data a viewer could already produce" — so they
    # fall through to the ``edit`` floor with every other authoring tool.
    # the shell
    "ui_open",
    # hosted-only packs (PRD-005a/005 slice 5, PRD-007): the reads
    "list_members", "sync_status", "share_list",
})

#: Tools that manage **who may do things** — the top rung. Named rather than
#: defaulted because ``edit`` would be wrong in the dangerous direction: an
#: editor who could grant themselves ``admin`` is not an editor.
ADMIN_TOOLS: frozenset[str] = frozenset({
    "grant_role", "revoke_role", "create_agent_token", "revoke_agent_token",
})

#: Checked at **no** floor. ``whoami`` is how a principal discovers which
#: workspace they are in and what they hold there, so refusing it to a
#: principal who holds nothing would be a riddle with the answer inside it.
#: It reads identity, never a project. This set is the one exception to
#: default-deny in this file and is deliberately one entry long.
NO_FLOOR_TOOLS: frozenset[str] = frozenset({"whoami"})

#: The review rung. ``authz``'s ladder says ``comment`` "adds review threads
#: and proposals", so the whole of that surface sits here — a reviewer who may
#: not move a millimetre of geometry may still open a proposal, review it and
#: resolve a thread. ``proposal_merge`` is **not** here: merging changes the
#: target branch's geometry, and that is ``edit``.
COMMENT_TOOLS: frozenset[str] = frozenset({
    "add_comment", "resolve_thread", "reopen_thread",
    "proposal_create", "proposal_update", "proposal_review", "proposal_packet",
})

#: Refused outright while a tenant is set, whatever the caller's role.
#: ``open_project`` registers an **absolute path** in a process-global map that
#: has no tenant in it (``ProjectStore._external``), so one org's admin could
#: otherwise publish a directory into every other org's namespace — or reach
#: outside the projects tree entirely. CLAUDE.md records it as a known FR19
#: gap because ``core/tools.py`` has no unregister seam; a registry wrapper is
#: the seam it lacked, and this is the second lock on the door (the store
#: ignores ``_external`` under a tenant too).
TENANT_FORBIDDEN: frozenset[str] = frozenset({"open_project"})


def floor_of(tool: str) -> str:
    """The role a tool needs: ``view``, ``comment``, ``admin``, else ``edit``."""
    if tool in READ_ONLY_TOOLS:
        return "view"
    if tool in COMMENT_TOOLS:
        return "comment"
    if tool in ADMIN_TOOLS:
        return "admin"
    return "edit"


def _is_mutating(name: str) -> bool:
    """Does a call to *name* deserve an audit row? The floor table answers.

    A tool changes authored state exactly when it needs more than ``view`` —
    the same map the write floor consults, so the audit log and the RBAC floor
    can never disagree about what a mutation is. ``whoami`` (the one
    :data:`NO_FLOOR_TOOLS` entry) reads identity and is excluded, because
    ``floor_of`` would otherwise default it to ``edit`` and log an identity read
    as an action.
    """
    return name not in NO_FLOOR_TOOLS and floor_of(name) != "view"


# ------------------------------------------------------------------ install

def install(service, registry=None, *, config=None) -> None:
    """Install every wrapper. Idempotent; safe on a service with no tenancy.

    *config* is a zero-argument callable answering the ``SecurityConfig``
    (default: ``server.security.current_config``) — a callable rather than the
    config itself because the module-level slot is set and cleared around
    every hosted app, and a captured config would outlive the app it belongs
    to.
    """
    resolver = _config_resolver(config)
    if getattr(service, "store", None) is None:
        # cmd_serve's own tests drive it with a stub SimpleNamespace service
        # (kernel only) — "safe on a service with no tenancy" includes one
        # with nothing to wire at all.
        return
    _install_root_resolver(service.store)
    _install_lock_key(service.store)
    _install_create(service.store, resolver)
    _install_write_guard(service, resolver)
    _install_install_write_guard(service, resolver)
    _install_bus(service.bus)
    if registry is not None:
        _install_registry(registry, resolver)
    _install_sync_seams(service, resolver)


def uninstall(service=None, registry=None) -> None:
    """Undo :func:`install` as far as process-global state goes.

    The per-object wrappers (store, bus, registry) die with their objects, and
    a test that wants them gone builds a new service. What genuinely has to be
    put back is the module-level state: ``tools_versioning.install_write_guard``
    and ``routes_sync``'s two seams, both of which would otherwise outlive the
    app that installed them and be found by the next test in the worker.
    """
    _restore_install_write_guard()
    _restore_sync_seams()
    for owner, attr, sentinel in (
            (getattr(service, "store", None), "lock_key", _LOCK_KEY),
            (getattr(service, "store", None), "create", _CREATE),
            (getattr(service, "bus", None), "publish", _PUBLISH),
            (getattr(service, "bus", None), "subscribe", _SUBSCRIBE),
            (registry, "call", _CALL)):
        if owner is None:
            continue
        current = getattr(owner, attr, None)
        inner = getattr(current, "_agentcad_inner", None)
        if getattr(current, sentinel, False) and inner is not None:
            try:
                delattr(owner, attr)        # back to the class's own method
            except AttributeError:
                setattr(owner, attr, inner)
    store = getattr(service, "store", None)
    if store is None:
        return
    if getattr(store.root_resolver, _ROOT, False):
        store.root_resolver = None
    if getattr(store.write_guard, _WRITE_GUARD, False):
        store.write_guard = getattr(store.write_guard, "_agentcad_inner", None)


def _config_resolver(config) -> Callable[[], object]:
    if config is not None:
        return config

    def current():
        from ..server import security          # lazy: core must not need it
        return security.current_config()

    return current


# --------------------------------------------------------------- the store

def _install_root_resolver(store) -> None:
    """``<root>/orgs/<org>/<ws>``, or ``None`` for "today's root"."""
    if getattr(store.root_resolver, _ROOT, False):
        return

    def root_resolver() -> Path | None:
        return tenancy.tenant_root(store.root)

    setattr(root_resolver, _ROOT, True)
    store.root_resolver = root_resolver


def _install_lock_key(store) -> None:
    """Qualify every keyed piece of per-project state with the tenant.

    ``store.lock_key`` is the single funnel: turn locks, undo stacks, presence,
    claims, build badges, the search index and navigation all key on it. The
    branch-aware answer is preserved and *then* qualified, so a branch working
    tree inside org A and the same-named one inside org B stay distinct too.
    """
    inner = store.lock_key
    if getattr(inner, _LOCK_KEY, False):
        return

    def lock_key(proj: str) -> str:
        return tenancy.qualified(inner(proj))

    setattr(lock_key, _LOCK_KEY, True)
    lock_key._agentcad_inner = inner
    lock_key.__doc__ = inner.__doc__
    store.lock_key = lock_key


def _install_create(store, config) -> None:
    """``edit`` in the workspace before a project may be created in it.

    The one write the write guard cannot see: ``write_guard`` fires on
    ``save_manifest`` and ``write_script``, both of which take an *existing*
    project, so ``ProjectStore.create`` — reached by ``POST /api/projects``,
    by ``create_project`` and by the chat and MCP surfaces — would otherwise
    let a viewer add projects to a workspace they may only read. Checked at
    the workspace level (``proj=None``), because the project it is about does
    not exist yet.
    """
    inner = store.create
    if getattr(inner, _CREATE, False):
        return

    def create(name: str) -> Path:
        tenant = tenancy.current_tenant()
        document = None if tenant is None else _tenancy_store(config)
        if document is not None:
            authz.require(document, "edit", locks.current_client_id(),
                          tenant[0], tenant[1])
        path = inner(name)
        if document is not None:
            # Register the project in the tenancy document *after* the store
            # made the directory, so an entry never points at nothing
            # (`TenancyStore.add_project`'s own contract). The entry is what
            # per-project grants hang off; membership still comes from the org
            # default, so a project with no entry is readable and a grant on it
            # is what needs the row.
            try:
                document.add_project(tenant[0], tenant[1], name)
            except AppError:
                pass            # a stale entry from a removed directory
        return path

    setattr(create, _CREATE, True)
    create._agentcad_inner = inner
    create.__doc__ = inner.__doc__
    store.create = create


# ---------------------------------------------------------- the write guard

def _install_write_guard(service, config) -> None:
    """``edit`` before every persistent mutation — the third choke point.

    The ``ensure_claim_guard`` shape with one deliberate difference: the authz
    check runs **before** the guard it wrapped, not after. A caller who may
    never write at all should be told that, rather than be told that somebody
    else holds the turn — an answer that invites them to wait for something
    that will never help. Ordering only differs while a tenant is set, so no
    existing error changes.

    **Where this ends up in the chain, and why either end is correct.** In an
    app it is the claim guard that lands outermost (``routes_presence``
    installs it when the route packs mount, after this); after a later
    ``install_write_guard`` rebuild, this one is. Both are right, because
    ``ensure_claim_guard``'s wrapper calls *its* previous guard **first** — so
    a refused principal is refused before any claim is taken, whichever way
    round the two sit. (Wrapping ``presence.ensure_claim_guard`` to force one
    order was tried and removed: ``routes_presence`` binds the name with a
    module-level ``from … import``, so the wrapper would be bypassed by the
    one caller that matters and pinned stale by the module cache.)

    Defense in depth, not the primary control: the tool registry and the read
    floor refuse first and with better messages. This is what catches the HTTP
    routes that mutate without going through a tool, and any path a future
    slice adds without remembering.
    """
    store = service.store
    previous = store.write_guard
    if getattr(previous, _WRITE_GUARD, False):
        return

    def guard(proj: str) -> None:
        tenant = tenancy.current_tenant()
        if tenant is not None:
            document = _tenancy_store(config)
            if document is not None:
                authz.require(document, "edit", locks.current_client_id(),
                              tenant[0], tenant[1], proj)
        if previous is not None:
            previous(proj)

    setattr(guard, _WRITE_GUARD, True)
    guard._agentcad_inner = previous
    if getattr(previous, "_claims_installed", False):
        # Carry PRD-008's marker across this wrapper. `ensure_claim_guard`
        # reads it off whatever `write_guard` currently is, and it is called
        # again from `routes_presence.build_router` (route packs mount after
        # tool packs) — without the marker it would find "no claim guard" and
        # install a SECOND one on top of ours, running every claim check
        # twice. The check is in the chain; the marker says so.
        guard._claims_installed = True
    store.write_guard = guard


def _install_install_write_guard(service, config) -> None:
    """Re-install the authz guard whenever the versioning pack rebuilds it.

    ``tools_versioning.install_write_guard`` **replaces** ``write_guard``; it
    is called on every registry build and from ``branch_switch``. PRD-008 paid
    for this once (the claim check vanished until the next heartbeat), so this
    wrapper exists for the same reason and takes the same shape — except that
    it is bound to *this* service by weak reference. A module-level wrapper
    that re-installed for any service would attach itself to
    ``checks.py``'s ephemeral one, which PRD-004 requires to end with
    ``write_guard is None``, and would keep a kernel pool alive between tests.
    """
    from . import tools_versioning

    inner = tools_versioning.install_write_guard
    if getattr(inner, _INSTALL_WRITE_GUARD, False):
        return
    reference = weakref.ref(service)

    def install_write_guard(target) -> None:
        inner(target)
        if target is reference():
            _install_write_guard(target, config)

    setattr(install_write_guard, _INSTALL_WRITE_GUARD, True)
    install_write_guard._agentcad_inner = inner
    install_write_guard.__doc__ = inner.__doc__
    tools_versioning.install_write_guard = install_write_guard


def _restore_install_write_guard() -> None:
    from . import tools_versioning

    current = tools_versioning.install_write_guard
    inner = getattr(current, "_agentcad_inner", None)
    if getattr(current, _INSTALL_WRITE_GUARD, False) and inner is not None:
        tools_versioning.install_write_guard = inner


# ------------------------------------------------------------- the registry

def _install_registry(registry, config) -> None:
    """The floor for the tool surface — HTTP, chat and MCP in one wrapper.

    All three dispatch through the same ``ToolRegistry`` object, so wrapping
    ``call`` on the instance covers them without any of them knowing. The
    refusal is built here rather than raised because ``ToolRegistry.call``
    answers refusals as ``{"error": …}`` payloads and a raise would become a
    500 on the chat and MCP surfaces, which have no exception handler.

    The audit tap goes on **outside** the floor (see :func:`_tap_audit`), so
    the row records the outcome the caller actually got — ``"ok"``, the
    refusal's ``permission_error``, or ``raised:<Class>``.
    """
    inner = registry.call
    if getattr(inner, _CALL, False):
        return

    def call(name: str, args: dict) -> dict:
        refusal = _refuse_tool(name, args, config)
        return refusal if refusal is not None else inner(name, args)

    setattr(call, _CALL, True)
    call._agentcad_inner = inner
    call.__doc__ = inner.__doc__
    registry.call = _tap_audit(call, config)


def _tap_audit(call, config):
    """``call`` with ``audit.tap_registry`` around it — one row per mutation.

    This is the wiring the tap was built for (changelog 0348 shipped it
    "tested and deliberately not installed anywhere yet"), and this is the
    place for it: ``install`` is the one seam that holds the registry every
    surface shares, and every property the tap needs is already true here —
    the org comes from the tenant ContextVar, so **local mode writes nothing**,
    and the sentinel below makes a second ``install`` a no-op.

    The sentinels are re-stamped on the outer callable rather than left to
    ``functools.wraps`` (which does copy ``__dict__``, and so does carry them
    across today): ``uninstall`` and the idempotence guard both read them off
    whatever ``registry.call`` currently is, and neither should depend on an
    implementation detail of the standard library.

    **What counts as a mutation is the floor table, not a name heuristic.** The
    tap is handed :func:`_is_mutating`, so "does this deserve a row" and "does a
    viewer need `edit` for this" are the *same* answer over the *same* map —
    ``audit.is_mutating_tool``'s prefix guess disagreed with it (``branch_list``,
    ``project_history``, ``run_checks``, ``export_bom`` are ``view``-floored
    reads it logged as actions). ``audit.py`` stays floor-agnostic; the floor
    knowledge lives here, with the floor.
    """
    tapped = audit.tap_registry(call, _AuditSink(config),
                                is_mutating=_is_mutating)
    if tapped is call:                       # already tapped: nothing to mark
        return call
    setattr(tapped, _CALL, True)
    tapped._agentcad_inner = getattr(call, "_agentcad_inner", call)
    return tapped


class _AuditSink:
    """The org's audit log, resolved **per row** instead of at install time.

    :func:`audit.tap_registry` wants an object with ``append(org, row)``.
    Handing it a real :class:`~agentcad.core.audit.AuditLog` would mean
    building one while installing, and that is wrong twice over:

    * the security config lives in a process-global slot that is set and
      cleared around every hosted app (:func:`_config_resolver` says so), so a
      captured log would name the state directory of an app that has gone; and
    * constructing one **creates** ``<state>/audit/`` — a directory a local
      ``agentcad serve`` must never grow, because the tap is installed there
      too and AC7 is "local mode is unchanged", not "local mode is unchanged
      except for a database".

    Resolved lazily it is never touched at all without a tenant: ``_record``
    returns before ``append`` when the org is ``None``, which is every call in
    local mode. ``audit.for_auth_store`` is the same accessor ``routes_auth``
    uses, and it goes through ``audit.shared`` — one log object per state
    directory per process, not one per call.
    """

    def __init__(self, config) -> None:
        self._config = config
        self._warned = False

    def append(self, org, row=None, **fields):
        log = self._log()
        return None if log is None else log.append(org, row, **fields)

    def _log(self):
        """The audit log behind the current security config, or ``None``.

        Never raises, ``_tenancy_store``'s reason turned one notch further:
        this one is not even an authorization path — it is bookkeeping on the
        way out of a call that has already happened, and a raise here would
        turn a broken audit backend into a failed CAD write. The tap's own
        contract (swallow a storage failure, warn on stderr) covers what
        happens after this returns a log; this covers not being able to find
        one at all.

        **No config is not a failure** — it is local mode, and it returns
        before the ``except``. Anything else is: an instance that has orgs and
        cannot open their databases is recording nothing, and an audit log
        that goes quiet without saying so is worse than no audit log, so it
        says so — once, because this runs per call.
        """
        try:
            cfg = self._config()
            store = None if cfg is None else getattr(cfg, "store", None)
            return None if store is None else audit.for_auth_store(store)
        except Exception as exc:            # noqa: BLE001 — see the docstring
            if not self._warned:
                self._warned = True
                print(f"agentcad: the audit log is unavailable "
                      f"({type(exc).__name__}: {exc}); mutating tool calls "
                      f"are NOT being recorded", file=sys.stderr)
            return None


def _refuse_tool(name: str, args: dict, config) -> dict | None:
    """``None`` to allow, else the error payload ``ToolRegistry.call`` shapes."""
    tenant = tenancy.current_tenant()
    if tenant is None or name in NO_FLOOR_TOOLS:
        return None
    document = _tenancy_store(config)
    if document is None:
        return None
    project = args.get("project") if isinstance(args, dict) else None
    project = project if isinstance(project, str) and project else None
    try:
        if name in TENANT_FORBIDDEN:
            raise authz.PermissionError(
                f"{name!r} is not available in a workspace: it registers a "
                f"directory outside the workspace's own storage. Use "
                f"import_cad_file, or a package.",
                {"required": "admin", "project": project,
                 "principal_role": None})
        authz.require(document, floor_of(name), locks.current_client_id(),
                      tenant[0], tenant[1], project)
    except AppError as exc:
        return {"error": {"type": _error_type(exc), "message": exc.message,
                          "details": exc.details}}
    return None


def _error_type(exc: AppError) -> str:
    """``ToolRegistry.call``'s derivation, copied because it is the wire
    contract: ``PermissionError`` -> ``permission_error`` (FR6)."""
    return type(exc).__name__.replace("Error", "").lower() + "_error"


# ------------------------------------------------------------------ the bus

def _install_bus(bus) -> None:
    """Stamp events with their tenant; filter them into subscribers by tenant.

    **Why the filter is on the queue and not on the route.** The ``/ws`` route
    lives in ``app.py``, which this feature does not touch, and it does two
    things this design needs: it calls ``bus.subscribe()`` *inside the
    connection's own context* (right after ``guard_websocket`` resolved that
    connection's tenant), and it then only ever calls ``q.get``. So the
    subscribe wrapper reads the ContextVar once, at subscribe time, and binds
    the answer to that connection for its lifetime — which is exactly right,
    because a socket has no per-message request to re-read a tenant from.

    The queue that ``EventBus`` created is the one the bus keeps in its
    subscriber list and the one ``unsubscribe`` is called with, so the wrapper
    must not replace it with a proxy. It replaces the queue's **bound
    ``put_nowait``** instead: ``publish`` calls it, the instance attribute
    wins, and identity, ``get``, ``get_nowait`` and the ``queue.Full`` contract
    are all untouched. The ``_WS_STOP`` sentinel ``app.py`` uses to wake a
    disconnected client is not a dict and is never filtered.

    Delivery rule: an event reaches a subscriber when the event carries **no**
    tenant (nothing published one — the local-mode case, and every event
    published outside a request context) or when it carries exactly this
    subscriber's. An untenanted subscriber on a hosted instance therefore sees
    no tenant's events, which is the safe direction.
    """
    publish_inner = bus.publish
    if not getattr(publish_inner, _PUBLISH, False):

        def publish(event: dict) -> None:
            stamp = _stamp(tenancy.current_tenant())
            if stamp is not None and isinstance(event, dict) \
                    and event.get("tenant") is None:
                # A copy: the caller's dict is theirs, and several call sites
                # build one event and publish it twice. The acting principal
                # rides alongside the tenant — PRD-005's "project_changed gains
                # the acting principal": `client` says *who*, in the workspace
                # `tenant` names, caused this event. Both are stamped together
                # and only when a tenant is set, so local mode is untouched
                # (AC7) and a re-published, already-tenanted event keeps its own.
                event = {**event, "tenant": stamp,
                         "client": locks.current_client_id()}
            publish_inner(event)

        setattr(publish, _PUBLISH, True)
        publish._agentcad_inner = publish_inner
        publish.__doc__ = publish_inner.__doc__
        bus.publish = publish

    subscribe_inner = bus.subscribe
    if not getattr(subscribe_inner, _SUBSCRIBE, False):

        def subscribe():
            q = subscribe_inner()
            stamp = _stamp(tenancy.current_tenant())
            put = q.put_nowait

            def put_nowait(item) -> None:
                if isinstance(item, dict):
                    marked = item.get("tenant")
                    if marked is not None and marked != stamp:
                        return              # another tenant's event
                put(item)

            q.put_nowait = put_nowait
            q.agentcad_tenant = stamp
            return q

        setattr(subscribe, _SUBSCRIBE, True)
        subscribe._agentcad_inner = subscribe_inner
        subscribe.__doc__ = subscribe_inner.__doc__
        bus.subscribe = subscribe


def _stamp(tenant) -> str | None:
    """``"org/ws"`` — the wire spelling of a tenant, and ``qualified``'s
    prefix. A string because it rides JSON to the browser."""
    return None if tenant is None else f"{tenant[0]}/{tenant[1]}"


# ------------------------------------------------------------- sync seams

_sync_installed = False


def _install_sync_seams(service, config) -> None:
    """Wire slice 2's ``require_role`` and ``resolve_project``.

    Git URLs carry their own ``/{org}/{ws}/{proj}.git``, so **neither seam
    reads the ContextVar**: the URL is the address, and the roles document is
    what says whether this principal may be at it. That is also why a refusal
    here is a 403 with the required role rather than a 404 — the message names
    no project that exists, so it is not an oracle either way.
    """
    global _sync_installed
    from ..server import routes_sync

    def require_role(role: str, org: str, ws: str, proj: str) -> None:
        who = _principal()
        if who is None:
            raise AuthError("authentication required")
        document = _tenancy_store(config)
        if document is None:
            raise AuthError("authentication required")
        authz.require(document, role, who, org, ws, proj)

    def resolve_project(org: str, ws: str, proj: str) -> Path:
        root = (Path(service.store.root) / tenancy.ORGS_DIRNAME / org / ws
                / proj)
        if not (root / "project.json").is_file():
            raise NotFoundError("no such project")
        return root

    routes_sync.require_role = require_role
    routes_sync.resolve_project = resolve_project
    _sync_installed = True


def _restore_sync_seams() -> None:
    global _sync_installed
    if not _sync_installed:
        return
    from ..server import routes_sync

    routes_sync.require_role = None
    routes_sync.resolve_project = None
    _sync_installed = False


# ---------------------------------------------------------------- helpers

def _principal() -> str | None:
    """The request's principal as the roles document spells it."""
    from ..server import security

    who = security.current_principal()
    if who is not None:
        return who.client_id
    return None


def _tenancy_store(config):
    """The orgs document behind the current security config, or ``None``.

    ``None`` means "no hosted configuration", which every caller reads as "not
    our business" — the same branch ``guard`` takes for local mode. It never
    raises: these are authorization paths, and a raise inside one is a 500
    where a deny belongs.
    """
    try:
        cfg = config()
        return None if cfg is None else cfg.tenancy()
    except Exception:                       # noqa: BLE001 — see the docstring
        return None
