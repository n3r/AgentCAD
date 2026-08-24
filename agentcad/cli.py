"""AgentCAD command-line interface.

Commands:
    agentcad serve [--host H] [--port N] [--projects-dir P] [--no-open]
    agentcad open                    # serve + open the browser
    agentcad mcp [--remote URL [--token T]]   # MCP stdio server (a proxy)
    agentcad login <url> [--token T] # store an instance token, 0600
    agentcad clone <url> [dest]      # clone a hosted project (PRD-005)
    agentcad push|pull|status [dir]  # sync a clone with its instance
    agentcad new <name>              # create a project
    agentcad export <project> <part> --format step|stl|3mf [--config NAME] [-o OUT]
    agentcad check [--project P] [--ref REF] [--report R] [--md M]
    agentcad admin user|token add|list|... / admin enrol  # hosted identity
    agentcad package validate <dir> [--strict] [--report R] [--budget S]
    agentcad publish <dir> --index NAME [--yank name@version] [--budget S]
    agentcad materials lint <path>… [--profile library|user] [--json]
    agentcad skill new <name> (--project P | --dir D)
    agentcad skill lint [<path>…] [--core] [--profile library|user] [--json]
"""

from __future__ import annotations

import argparse
import os
import signal
import sys
import threading
import time
import webbrowser
from pathlib import Path

import agentcad
from ._resources import resource_root
from ._spawn import worker_argv  # noqa: F401 — re-exported; kernel spawn helper
from .config import get_port

DEFAULT_PROJECTS_DIR = Path.home() / "AgentCAD" / "projects"


def _projects_dir(args) -> Path:
    """`--projects-dir`, else `$AGENTCAD_PROJECTS_DIR`, else the default.

    The environment layer is FR24's: the container mounts one volume and points
    every command at `/data/projects` without overriding the image's command.
    It is applied in **one** helper rather than at each call site so `agentcad
    new` inside `docker compose exec` cannot land in a different tree from the
    one the server serves — which is the whole failure this replaces.
    """
    override = getattr(args, "projects_dir", None) or os.environ.get(
        "AGENTCAD_PROJECTS_DIR")
    return Path(override) if override else DEFAULT_PROJECTS_DIR


def _build_service(projects_dir: Path, extra_writable: list[str] | None = None,
                   *, posture: str | None = None, examples: bool = True):
    """The service every command shares: one warm kernel, no server.

    *extra_writable* appends to the sandbox's writable roots and must be known
    **here**, before the workers spawn: the seatbelt profile is fixed at spawn,
    so a `agentcad check --work-dir` outside the system temp dir cannot be
    granted afterwards. The default leaves the roots byte-identical for every
    other caller.

    Quotas (PRD-006) are resolved here too, for the same reason: they are
    fixed at construction so every respawn of a killed worker is capped
    identically. *posture* defaults to the deployment mode's — `hosted` on a
    hosted instance, `local` otherwise.

    Two more things are wired here because nowhere else can be:

    * the **work root** — one server-wide `agentcad-work-*` directory, granted
      to the workers and exposed as ``service.work_root``. Since PRD-006 the
      shared temp dir is not a writable root (Decision 1: never grant bare
      temp), so a check run or a package gate that materializes its cell under
      `tempfile.gettempdir()` would hand the confined worker a directory it
      cannot write into. This is the one temp root they share, and whoever
      builds the service owns removing it (`_release_work_root`).
    * the **usage meter** — it is the kernel's ``on_usage`` hook, so it has to
      exist before the kernel does; the service gets it afterwards.

    ``examples=False`` is the bench's (PRD-024): a task derived from a bundled
    example must not be solvable by opening that example. The default keeps
    ``check``, ``serve``, ``export`` and ``package`` byte-identical, and it is a
    parameter rather than ``AGENTCAD_EXAMPLES=0`` because that variable is
    process-global and a bench run inside a pytest worker would clobber a
    neighbour.
    """
    import tempfile

    from .config import get_kernel_pool_size
    from .core.service import AgentCADService, EventBus
    from .core.usage import UsageMeter
    from .kernel import quotas as quotas_mod
    from .kernel import sandbox
    from .kernel.client import KernelClient
    from .kernel.pool import KernelPool

    # Resolved FIRST, before anything with a side effect: a misconfigured quota
    # is a `ValueError` naming the key and the layer, and a refused start
    # should leave neither a directory nor a worker behind.
    quotas = quotas_mod.resolve()
    posture = posture or sandbox.default_posture()
    size = get_kernel_pool_size()
    # Created before `kernel.start()`: a Landlock grant on a missing path is
    # ENOENT, and `mkdtemp` both makes the directory and makes its name
    # unguessable (0700, so no other user can plant anything in it).
    work_root = Path(tempfile.mkdtemp(prefix="agentcad-work-"))
    meter = UsageMeter()
    try:
        writable = _writable_roots(projects_dir) + [str(work_root)]
        if extra_writable:
            writable += [str(root) for root in extra_writable]
        if size == 1:
            kernel = KernelClient(writable_dirs=writable, quotas=quotas,
                                  posture=posture, on_usage=meter.record)
        else:
            kernel = KernelPool(size=size, writable_dirs=writable,
                                quotas=quotas, posture=posture,
                                on_usage=meter.record)
        kernel.start()
    except BaseException:
        # Nothing is running yet (or the spawn itself failed and cleaned up
        # after itself), so the work root is the only thing to take back.
        _remove_work_root(work_root)
        raise
    try:
        service = AgentCADService(projects_dir, kernel, EventBus())
        service.work_root = work_root
        # What the confined workers may write to, kept so a caller can *check*
        # it: `cmd_serve` refuses a hosted state dir that lies inside one
        # (PRD-006 FR5 — a part script that can read `secret.key` can forge a
        # session). Nothing else reads it; the kernel already has it.
        service.writable_roots = list(writable)
        service.usage = meter
        service.store.disk_budget_mb = quotas.disk_mb or None
        if examples:
            _register_examples(service)
        _register_catalog(service)
    except BaseException:
        # The workers are already running: anything that raises between here
        # and the return would leave one process per worker (~0.5 GB each)
        # behind, with nobody holding a reference to stop them — and the work
        # root would outlive the run that made it.
        try:
            kernel.stop()
        except Exception:  # noqa: BLE001 — the original failure is the answer
            pass
        _remove_work_root(work_root)
        raise
    return service


def _remove_work_root(root) -> None:
    """Remove a work root this process created. Never raises."""
    import shutil

    if root is None:
        return
    shutil.rmtree(root, ignore_errors=True)


def _release_work_root(service) -> None:
    """Remove the work root `_build_service` gave *service*, if it has one.

    Every command that builds a service calls this in the same ``finally``
    that stops the kernel: the directory is one this process made with
    ``mkdtemp``, so removing it breaks nobody's "never delete a directory it
    did not create" contract — a caller's ``--work-dir`` is a different path
    and is never touched. Tolerant of a service that has no work root (a stub
    in a test, a service built by hand).
    """
    _remove_work_root(getattr(service, "work_root", None))


def _accept_work_dir(raw, refuse) -> str | None:
    """Resolve a caller's ``--work-dir``, accept or refuse it, then create it.

    All three, **before** ``_build_service`` (review I1). The work dir is added
    to the sandbox's writable roots, and on Linux a Landlock rule on a path
    that does not exist is ENOENT: the grant is silently lost, the worker
    reports the failure, and every part then fails with a ``PermissionError``
    instead of producing a verdict. So an accepted work dir has to exist before
    the workers spawn — and the only way to create one honestly is to have
    accepted it first, because "a refused path leaves nothing behind" is a
    promise with a test on it (`test_checks_cli.py`,
    `test_packages_cli.py`).

    *refuse* is the overlap guard, already bound to whatever this command must
    not overlap. It raises ``ValidationError`` — an ``AppError``, which every
    caller already maps to its own exit code.

    The runner's contract is untouched: an accepted directory created here is
    still never *deleted* by the run. Everything a run writes goes into one
    subdirectory it made itself, and the caller's directory is left as it was.

    Absolute, always: ``history._run`` runs git with ``cwd`` set to the
    project, so a relative work dir would materialize a ``--ref`` worktree
    inside the user's project.
    """
    if not raw:
        return None
    root = Path(raw).expanduser().resolve()
    refuse(root)
    root.mkdir(parents=True, exist_ok=True)
    return str(root)


def _writable_roots(projects_dir: Path) -> list[str]:
    """Directories the sandboxed kernel workers may write to: the projects
    dir (part .cache meshes, exports/), each registered example project, and
    the one state-dir subtree PRD-007's shared-pool builds need.

    The **system temp dir is not among them** (PRD-006 Decision 1): granting
    it made every worker able to read and write every other worker's scratch,
    and the whole point of the private per-worker `agentcad-worker-*` dir is
    that it is the only temp root a script can reach. The one shared scratch a
    *run* still needs — `agentcad check` and the package gate materialize a
    work cell when no `--work-dir` is given — is the server-wide work root
    `_build_service` creates and grants by name.

    **`~/.agentcad` (the state dir itself) is not among them** (review I5). It
    was, and nothing justified granting the whole thing: no module under
    `agentcad/kernel/` or `agentcad/toolkit/` reads or writes the config dir,
    every `load_config()` caller is server-side, and the worker's `HOME` is
    its own private temp dir — so a blanket grant bought nothing and cost the
    one sentence the docs most want to be able to say, that a part script can
    write **nothing under the server user's home**. The config file holds
    index definitions and quota knobs, and a script that could rewrite them
    could raise its own caps; `secret.key` and `auth/` sit in the same tree
    and must stay unreadable to a hosted member (FR5).

    **`<state-dir>/publications/build` IS granted**, and only that one
    subtree (PRD-007 merge). Share-link/customizer variant builds go through
    the SHARED kernel pool into `PublicationStore.build_root()` —
    `agentcad/core/share_build.py`'s `self._store.build_root()`, which is
    exactly `appmode.state_dir() / "publications" / "build"` — so a
    confined worker producing a variant mesh has to be able to write there.
    It is safe to narrow the grant to this one subtree rather than the state
    dir: the scripts pinned under `publications/scripts/` are already public
    (that is the whole point of a share link), so a worker reading or writing
    its own build cell exposes nothing that was not already exposed, while
    `secret.key` and `auth/` are siblings of `publications/`, not beneath it,
    and stay outside every writable and hosted-readable root. This is also
    why the hosted read allow-list can expose only this one subtree of the
    state dir rather than the state dir whole.

    The projects dir and this subtree are **created here** when absent: both
    are the server's own, and on Linux a Landlock grant on a missing path is
    ENOENT (see the comment below). Everything the CALLER supplied — a
    `--work-dir` that may still be refused — is granted as given and left
    alone."""
    from .core.appmode import state_dir  # read at call time: monkeypatch.setenv

    roots = [str(projects_dir), str(state_dir() / "publications" / "build")]
    # Both may be ABSENT on a fresh install — the service creates the
    # projects dir after `kernel.start()`, and the publications build root
    # is created lazily by whatever first builds a variant. A Landlock rule
    # on a missing path is ENOENT (PRD-006): the grant is silently lost, so
    # every write into the directory fails once it does appear. Both are the
    # server's own directories, so making them here is safe — unlike doing
    # it in `sandbox.plan()`, which also receives caller-supplied
    # `--work-dir` paths that may still be refused ("a refused path leaves
    # nothing behind").
    for owned in roots:
        try:
            os.makedirs(owned, exist_ok=True)
        except OSError as exc:
            print(f"warning: could not create the writable root {owned!r}: "
                  f"{exc}; kernel writes there will be denied", file=sys.stderr)
    examples = resource_root() / "examples"
    if examples.is_dir():
        for child in sorted(examples.iterdir()):
            if (child / "project.json").is_file():
                roots.append(str(child))
    return roots


def _register_examples(service) -> None:
    # FR22. In a container the bundled examples live in a read-only image
    # layer that `_writable_roots` nevertheless grants writes into, so edits
    # vanish on redeploy and `.cache` builds land in an ephemeral layer.
    # `AGENTCAD_EXAMPLES=0` (the compose default) skips them. Default "1", so
    # every local install is unchanged.
    if os.environ.get("AGENTCAD_EXAMPLES", "1") == "0":
        return
    examples = resource_root() / "examples"
    if not examples.is_dir():
        return
    for child in sorted(examples.iterdir()):
        if (child / "project.json").is_file():
            try:
                service.store.open(child)
            except Exception as exc:  # noqa: BLE001 — a broken example must not block startup
                print(f"warning: could not open example {child.name}: {exc}", file=sys.stderr)


#: The bundled catalog's index name. It is the name `agentcad publish --index`
#: takes for the seed catalog, and the name a user's own configuration would
#: have to reuse to shadow it.
CATALOG_INDEX = "agentcad-core"


def bundled_index_entries() -> list[dict]:
    """Index configuration entries for the catalog the app ships with.

    One entry today: `catalog/` at the resource root, resolved exactly as
    `examples/` is, so a frozen bundle and a source checkout agree. Empty when
    the catalog is absent — it is **data**, and deleting it degrades the
    product to "no packages configured" rather than breaking a code path.
    """
    catalog = resource_root() / "catalog"
    if not (catalog / "index.json").is_file():
        return []
    return [{"name": CATALOG_INDEX, "kind": "local", "path": str(catalog),
             "scope": "public"}]


def _register_catalog(service) -> None:
    """Register the bundled `catalog/` as a local package index.

    `_register_examples`' precedent, one layer up: the examples are projects
    the store opens, this is an index `service.packages` reads. It is a
    *declaration*, not a client — `PackageManager.reload_indexes` appends it
    after whatever the user configured, so the bundled catalog answers on a
    fresh install with **no network and no config file** while a user index of
    the same name still wins.

    A catalog directory with no `index.json` is a warning, never a startup
    failure.
    """
    entries = bundled_index_entries()
    if not entries:
        catalog = resource_root() / "catalog"
        if catalog.exists():
            print(f"warning: bundled catalog at {catalog} has no index.json; "
                  "no packages will be offered", file=sys.stderr)
        return
    service.bundled_indexes = entries


def _make_chat_engine(service, registry):
    try:
        from .agent.chat import ChatEngine
    except ImportError:
        return None
    from .core.skills import SkillBudget

    # The service's own library, so the chat sees exactly the skills every
    # other surface does; the budget is config/env-driven (PRD-029 §3).
    return ChatEngine(registry, service.bus, skills=service.skills,
                      budget=SkillBudget.from_config())


def _resolve_mode_or_exit():
    """``AppMode`` from the environment, or exit 2 naming the setting.

    A misconfigured hosted instance must **not** fall back to local: a server
    that quietly served an unauthenticated API because a variable was
    misspelled is the one failure this design will not have.
    """
    from .core.appmode import ModeError, resolve_mode

    try:
        return resolve_mode()
    except ModeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


def _security_config(mode=None):
    """A ``SecurityConfig`` in hosted mode, ``None`` in local mode.

    ``None`` is not "auth disabled": ``create_app`` runs the same middleware
    body it always has, so a local `agentcad serve` is byte-identically what
    it was. The imports are **lazy** on purpose — ``authstore`` reaches for
    ``fcntl``, and a local run on Windows must never touch it.
    """
    from .core.appmode import HOSTED, ensure_state_dir

    mode = mode if mode is not None else _resolve_mode_or_exit()
    if mode.name != HOSTED:
        return None

    from .core.authstore import AuthStore
    from .server.security import SecurityConfig

    return SecurityConfig(mode=mode, store=AuthStore(ensure_state_dir() / "auth"))


def _serve_bind(args, mode) -> tuple[str, int]:
    """Where to listen, subject to Decision 3's interlock.

    `--host` wins over `AGENTCAD_HOST` wins over loopback; likewise `--port`,
    `AGENTCAD_PORT`, the stored config. The environment layer is what lets the
    container configure the bind without overriding the image's command.

    The interlock is checked **here**, before `_build_service`, so a refusal
    costs nothing: building the service first would spawn ~0.5 GB of kernel
    worker per pool slot and then exit.
    """
    from .core.appmode import ModeError, check_bind, trusted_proxy

    host = args.host or os.environ.get("AGENTCAD_HOST") or "127.0.0.1"
    # The interlock first, before anything with a side effect: `get_port()`
    # WRITES the config file when no port is stored yet, and a refused start
    # should leave nothing behind. `trusted_proxy()` is validated in the same
    # breath so a dangerous `AGENTCAD_TRUSTED_PROXY=*` is a clean exit here
    # rather than a traceback later at `uvicorn.run`.
    try:
        check_bind(mode, host)
        if mode.hosted:
            trusted_proxy()
    except ModeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    given = args.port or os.environ.get("AGENTCAD_PORT")
    try:
        port = int(given) if given else get_port()
    except (TypeError, ValueError):
        print(f"error: AGENTCAD_PORT must be a port number, not {given!r}",
              file=sys.stderr)
        raise SystemExit(2) from None
    return host, port


def cmd_serve(args, open_browser: bool) -> None:
    import uvicorn

    from .core import tenancy_wiring
    from .core.tools import build_registry
    from .server import security as security_module
    from .server.app import create_app

    mode = _resolve_mode_or_exit()
    host, port = _serve_bind(args, mode)
    projects_dir = _projects_dir(args)
    security = _security_config(mode)
    # Installed here rather than only inside `create_app`, because a tool pack
    # decides at REGISTRATION time whether its tool can run (the FEM
    # precedent) and `build_registry` runs before `create_app`. Without this
    # line `whoami` would exist in every test and in no real hosted server.
    security_module.install(security)
    try:
        service = _build_service(projects_dir)
    except ValueError as exc:
        # `quotas.resolve()` refuses a non-numeric knob with a ValueError that
        # names both the key and the layer it came from — the reader is an
        # operator staring at a server that will not start, so it gets the
        # same `error: …` + exit 2 shape as a bad AGENTCAD_MODE, not a
        # traceback with the message buried in it.
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc

    # Everything from here on is inside the cleanup: since PRD-006 the service
    # owns a work root and one private `agentcad-worker-*` dir per pool slot,
    # so a failure in `build_registry` or `create_app` — a broken tool pack, a
    # missing frontend asset — used to leak half a gigabyte of worker and two
    # directories per attempt.
    #
    # Ctrl-C arrives as a KeyboardInterrupt and takes the `finally` below.
    # `docker stop` does not: uvicorn's `capture_signals` **restores the
    # previous SIGTERM handler and re-raises the signal** once it has shut
    # down gracefully (uvicorn/server.py, "trigger the expected behaviour
    # now"), so with the default handler in place the process dies *inside*
    # `uvicorn.run` with exit 143 and no `finally` ever runs — measured.
    # Ours is the handler it restores, and turning that re-raise into a
    # SystemExit puts both paths through the same cleanup.
    previous_term = None
    try:
        _refuse_state_dir_in_a_write_root(mode, service)
        registry = build_registry(service)
        # PRD-005 FR5/FR6: the tenancy wrappers (store root resolver, lock-key
        # qualification, the write-guard and tool-registry floors, the event
        # tenant stamp/filter, the sync seams). Installed HERE because this is
        # the one place that holds the service, the registry and the security
        # config at once — and after `build_registry`, because the versioning
        # pack replaces `write_guard` while registering. Every wrapper no-ops
        # until a request carries a tenant, so a local `serve` is unchanged.
        tenancy_wiring.install(service, registry)
        chat_engine = _make_chat_engine(service, registry)
        app = create_app(service, registry, chat_engine, security=security)
        _warn_if_unconfined(mode, service.kernel)

        # What to *open* and what to *print* are the loopback URL; what to bind
        # is `host`. On a hosted instance the address a person types is the
        # configured public origin, not the interface.
        local_url = f"http://127.0.0.1:{port}"
        url = mode.public_origin or local_url
        if open_browser and not args.no_open:
            threading.Timer(1.0, lambda: webbrowser.open(local_url)).start()
        print(f"AgentCAD {agentcad.__version__} — {url} "
              f"(listening on {host}:{port})")
        if threading.current_thread() is threading.main_thread():
            previous_term = signal.signal(
                signal.SIGTERM, lambda signum, frame: sys.exit(128 + signum))
        uvicorn.run(app, host=host, port=port, log_level="warning",
                    **_uvicorn_proxy_kwargs(mode))
    finally:
        if previous_term is not None:
            signal.signal(signal.SIGTERM, previous_term)
        service.kernel.stop()
        _release_work_root(service)


def _within(inner: Path, outer: Path) -> bool:
    """Whether *inner* is *outer* itself or somewhere beneath it. Lexical, on
    already-resolved paths — the same idiom as `core/checks.py`."""
    try:
        return Path(inner).is_relative_to(Path(outer))
    except (TypeError, ValueError):     # pragma: no cover - defensive
        return False


def _refuse_state_dir_in_a_write_root(mode, service) -> None:
    """Refuse to serve a hosted instance whose state dir a part script can write.

    The hosted read posture (FR5) exists to keep ``<state-dir>/secret.key``
    out of a member's reach: the same uid runs the server and the workers, so
    DAC cannot express it and Landlock is what does. A state dir placed
    **inside a writable root** defeats that from the other side — the root is
    granted write access explicitly, so the file is readable and rewritable
    however narrow the read allow-list is, and whoever can read the session
    secret can forge any session.

    Fatal rather than a warning, and unlike `_warn_if_unconfined`: that one
    reports a platform that cannot confine (nothing the operator can fix by
    editing a variable), while this is one misplaced path with an exact
    remedy, and serving anyway would be serving a hosted instance whose
    accounts are already forgeable. Exit 2, the repo's "your configuration is
    wrong" code.

    Local mode is not checked: it is one trusted user on loopback, and there
    is no session to forge from another account. A service with no
    ``writable_roots`` (a stub in a test, a service built by hand) is left
    alone rather than guessed at.
    """
    if not getattr(mode, "hosted", False):
        return
    roots = getattr(service, "writable_roots", None)
    if not roots:
        return
    from .core.appmode import ensure_state_dir

    state = Path(ensure_state_dir()).resolve()
    for root in roots:
        resolved = Path(root).resolve()
        if _within(state, resolved):
            print(f"error: AGENTCAD_STATE_DIR ({state}) lies inside a "
                  f"kernel-writable root ({resolved}); part scripts could "
                  f"read secret.key — set AGENTCAD_STATE_DIR outside the "
                  f"projects tree", file=sys.stderr)
            raise SystemExit(2)


def _warn_if_unconfined(mode, kernel) -> None:
    """One loud line on stderr when a HOSTED instance is not confining workers.

    Never fatal, by design (Decision 8): the deploy-smoke job must keep proving
    that the compose image boots, and an operator who reads `/api/health` gets
    the same facts in a structured form. What must not happen is a hosted
    instance running arbitrary part scripts unconfined **silently**.

    Defensive about the report itself — a startup warning that crashed the
    server it was warning about would be the worst possible trade.
    """
    if not getattr(mode, "hosted", False):
        return
    from .kernel import sandbox

    try:
        report = sandbox.report(kernel)
    except Exception as exc:  # noqa: BLE001 — a warning must not end the boot
        print(f"WARNING: could not determine kernel confinement status: "
              f"{exc!r}", file=sys.stderr)
        return
    if report.get("status") == "active":
        return
    reasons = "; ".join(report.get("warnings") or []) or "no reason reported"
    print(f"WARNING: kernel confinement is {report.get('status')} on this "
          f"hosted instance — a part script is arbitrary Python and is NOT "
          f"contained: {reasons}", file=sys.stderr)


def _uvicorn_proxy_kwargs(mode) -> dict:
    """How uvicorn should treat ``X-Forwarded-For`` for this mode.

    **Local mode: off.** A single-user loopback tool has no proxy in front of
    it, and uvicorn's default is to trust `127.0.0.1` — which in local mode is
    the client itself. Left on, a local page could set its own forwarded
    address; there is nothing here that reads it today, but "the loopback
    client cannot spoof its address" is cheaper to keep true than to reason
    about, so the header is not parsed at all.

    **Hosted mode: on, bounded to the trusted proxy** (`appmode.trusted_proxy`,
    default `127.0.0.1`). This is what makes the login rate limit's
    `(handle, address)` key name the real client rather than the reverse proxy
    the deployment guide puts in front — without it, every internet client
    shares the proxy's address and the per-handle half collapses to a
    site-wide lockout (review finding M3, round 2, changelog 0198). It is set
    **explicitly** rather than left to uvicorn's default so a version bump that
    flipped `proxy_headers` cannot silently change a security property, and so
    the value is one we validated (`*` is refused). It is safe even with no
    proxy and a direct public bind: the immediate peer is then the public
    client, which is not in the trusted set, so `X-Forwarded-For` is ignored
    and the socket peer stands.
    """
    from .core.appmode import trusted_proxy

    if not mode.hosted:
        return {"proxy_headers": False, "forwarded_allow_ips": ""}
    return {"proxy_headers": True, "forwarded_allow_ips": trusted_proxy()}


def cmd_mcp(args) -> None:
    """The MCP stdio server. ``--remote`` points it at a hosted instance.

    There is nothing to build for the remote case and that is the design, not a
    shortcut: ``agent/mcp_server.py`` has **always** been a proxy — it mirrors
    ``GET /api/tools`` and turns every ``call_tool`` into
    ``POST /api/tools/{name}`` — so "run the MCP server against a hosted
    instance" is entirely a question of which base URL and which bearer it
    uses. Both are already inputs (``AGENTCAD_URL``, ``AGENTCAD_TOKEN``);
    PRD-005 FR10 makes them flags, resolves the token from ``agentcad login``
    when it is not given, and leaves local mode byte-identical.

    Two honest limits, stated because an agent should know them:

    * **Every tool call is a network round trip.** A local run talks to
      127.0.0.1; this one crosses the internet on every call, and a build that
      takes 300 ms locally takes 300 ms plus the round trip here.
    * A remote instance is never auto-started. ``mcp_server._may_autostart``
      already refuses to start a *local* server because a *remote* one is
      unreachable — quietly driving an empty machine that is not the one the
      operator configured is a worse outcome than an error.
    """
    from .agent.mcp_server import run_mcp_server

    if args.remote:
        from .core import sync

        try:
            instance = sync.instance_of(args.remote)
        except ValueError as exc:
            print(f"agentcad mcp: {exc}", file=sys.stderr)
            raise SystemExit(2) from None
        token = (args.token or sync.token_for(instance)
                 or os.environ.get("AGENTCAD_TOKEN") or "").strip()
        if not token:
            print(f"agentcad mcp: no token for {instance}. Run "
                  f"`agentcad login {instance}` (which stores it 0600), pass "
                  f"--token, or set AGENTCAD_TOKEN.", file=sys.stderr)
            raise SystemExit(2)
        # The proxy reads both from the environment, so this is where a
        # `--token` on the argv stops being visible: it is not passed on to any
        # child process argv, and `agentcad login` avoids the argv entirely.
        os.environ["AGENTCAD_URL"] = instance
        os.environ["AGENTCAD_TOKEN"] = token
    run_mcp_server()


def cmd_worker(args) -> None:
    # Hidden subcommand: run the kernel worker loop in THIS process. It exists
    # so a frozen (PyInstaller) bundle can re-exec its own executable as the
    # worker (see agentcad._spawn.worker_argv). Imported lazily because the
    # worker module imports build123d/OCP, which the server process must never
    # load — this branch only ever runs in the dedicated worker subprocess.
    from .kernel.worker import main as worker_main

    worker_main()


def cmd_new(args) -> None:
    from .core.project import ProjectStore

    store = ProjectStore(_projects_dir(args))
    path = store.create(args.name)
    print(f"created {path}")


def cmd_export(args) -> None:
    service = _build_service(_projects_dir(args))
    try:
        project = args.project
        if _is_path(project):
            project = service.open_project(project)["name"]
        result = service.export_part(project, args.part, args.format,
                                     config=args.config)
        out = result["path"]
        if args.output:
            import shutil

            shutil.copy(out, args.output)
            out = args.output
        print(f"exported {out} ({result['size_bytes']} bytes)")
    finally:
        service.kernel.stop()
        _release_work_root(service)


def _is_path(project: str) -> bool:
    """Whether a project argument is a **path** rather than a project name.

    A separator of either kind, a leading dot, or a drive spec. Windows is why
    this is not simply ``"/" in project``: an absolute ``C:\\Users\\...``
    contains no forward slash at all, so ``agentcad check --project <abs path>``
    did not recognise it as a path, never added the project to the kernel's
    writable roots, and — once PRD-006b gave Windows a real confinement — every
    build failed with ``PermissionError: [WinError 5]`` writing its ``.cache/``
    (PR #24, CI round 1). On macOS and Linux such an argument always carried a
    ``/``, so the gap was invisible until something enforced it.

    Both separators are tested on both platforms, deliberately: ``os.sep`` alone
    would make ``a\\b`` a name on POSIX, and a Windows path handed to a POSIX
    tool (or the reverse, through a config file or a CI matrix) should be read
    the same way by both.
    """
    if not project:
        return False
    if project.startswith("."):
        return True
    if any(separator and separator in project
           for separator in ("/", "\\", os.sep, os.altsep)):
        return True
    # A drive spec — `C:`, `C:\\x`, and the drive-relative `C:x`. None of them
    # is a project name (names have no colon), and the last two would otherwise
    # slip through on a POSIX box reading a Windows argument.
    return len(project) > 1 and project[1] == ":"


def _check_stages(value: str | None) -> tuple[str, ...]:
    """``--stages build,assembly`` as a validated tuple.

    Validated *here*, before the kernel starts: a typo'd stage name should cost
    a millisecond and a usage line, not three seconds of worker spawn. The
    derived ``determinism`` stage is deliberately not selectable — it certifies
    the product guarantee, not the project, and has its own flag.
    """
    from .core.checks import STAGES

    if value is None:
        return STAGES
    names = tuple(name.strip() for name in value.split(",") if name.strip())
    unknown = [name for name in names if name not in STAGES]
    if not names or unknown:
        named = ", ".join(repr(name) for name in unknown) or "an empty list"
        raise ValueError(f"unknown --stages value: {named}; expected a "
                         f"comma-separated subset of {', '.join(STAGES)}")
    return names


def _finite_arg(flag: str, why: str):
    """An ``argparse`` ``type`` for a limit: finite, non-negative, or exit 2.

    ``type=float`` accepts ``nan`` and ``inf``, and a non-finite limit is not a
    loose limit — it is **no limit at all**, silently: every comparison with
    NaN is false, so ``--budget nan`` switches off the deadline it configures
    and ``--min-volume nan`` makes every ``volume > min_volume`` false, which
    reports a genuinely interfering assembly as green (review C9).

    Refused here, before the kernel starts, because an invocation the user can
    still fix should cost a usage line rather than three seconds of worker
    spawn. ``core.checks`` refuses the same values again, for the tool and the
    route.
    """
    import math

    def parse(value: str) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise argparse.ArgumentTypeError(
                f"{flag} must be a number; got {value!r}") from None
        if not math.isfinite(number) or number < 0:
            raise argparse.ArgumentTypeError(
                f"{flag} must be a finite, non-negative number; got {value!r} "
                f"({why})")
        return number

    return parse


def _write_check_outputs(args, report: dict) -> list[str] | None:
    """Write ``--report``/``--md``; None means "could not", which is exit 2.

    Atomic, like every other report this codebase writes: a CI job that reads a
    half-written ``report.json`` is worse than one that reads none.
    """
    import json

    from .core.checks import render_markdown
    from .core.project import ProjectStore

    targets: list[tuple[Path, bytes]] = []
    if args.report:
        targets.append((Path(args.report).expanduser(),
                        (json.dumps(report, indent=2) + "\n").encode()))
    if args.md:
        targets.append((Path(args.md).expanduser(),
                        render_markdown(report).encode()))
    written: list[str] = []
    for path, data in targets:
        try:
            ProjectStore._atomic_write(path, data)
        except OSError as exc:
            print(f"agentcad check: could not write {path}: {exc}",
                  file=sys.stderr)
            return None
        written.append(str(path))
    return written


def _can_post(runner) -> bool:
    """Whether this run could post to a proposal at all.

    The runner owns the answer (proposals are a git feature and
    ``tools_proposals`` self-disables without one); the ``getattr`` is for a
    runner that does not know the question — an embedder's stand-in, or a
    ``CheckRunner`` from a version before posting existed.
    """
    ask = getattr(runner, "can_post", None)
    return bool(ask) and bool(ask())


def _post_note(message: str) -> None:
    """Posting notes go to stderr **even under ``--quiet``**: ``--quiet`` says
    the exit code is the answer about the *check*, and "you asked me to post
    this and I did not" is not something an exit code can say."""
    print(f"agentcad check: {message}", file=sys.stderr)


def _post_check(runner, project: str, args, report: dict,
                pid: str | None) -> int | None:
    """Post the report to a proposal. Returns an exit-code override, or None.

    ``--auto-proposal`` matches **active** proposals whose source is the branch
    that was checked: none is a warning (most checks are not about a proposal),
    and more than one is exit 2 — guessing which proposal a verdict belongs to
    is worse than refusing to say.
    """
    from .core.model import AppError

    if pid is None and args.auto_proposal:
        matches = runner.matching_proposals(project, report)
        branch = runner.measured_branch(project, report)
        if not matches:
            _post_note(f"--auto-proposal: no active proposal has source "
                       f"{branch!r}; nothing was posted")
            return None
        if len(matches) > 1:
            ids = ", ".join(str(row.get("id")) for row in matches)
            _post_note(f"--auto-proposal: {len(matches)} active proposals "
                       f"share source {branch!r} ({ids}); refusing to guess — "
                       f"pass --proposal ID")
            return 2
        pid = str(matches[0].get("id"))
    if pid is None:
        return None
    try:
        receipt = runner.post_to_proposal(project, pid, report)
    except AppError as exc:
        _post_note(f"could not post to proposal {pid}: {exc.message}")
        return 2
    _post_note(f"posted to proposal {pid}: {receipt['status']} "
               f"(exit {receipt['exit_code']}) — {receipt['path']}")
    return None


_CHECK_ROW = "  {:<10} {:<6} {:>5} {:>5} {:>5} {:>6} {:>6}  {:>7}"


def _check_lines(report: dict, written: list[str]) -> list[str]:
    """The stage table and what went wrong, for a human reading stderr."""
    source = report.get("source") or {}
    facts = [str(source.get("kind") or "worktree")]
    for key, label in (("ref", "ref"), ("label", "label")):
        if source.get(key):
            facts.append(f"{label} {source[key]}")
    for key, label in (("sha", "sha"), ("host_sha", "commit")):
        if source.get(key):
            facts.append(f"{label} {str(source[key])[:7]}")
    if source.get("dirty"):
        facts.append("dirty")
    if report.get("strict"):
        facts.append("strict")
    lines = [f"{report.get('project')} — {' · '.join(facts)}",
             _CHECK_ROW.format("stage", "status", "pass", "fail", "skip",
                               "error", "total", "time")]
    for stage in report.get("stages") or []:
        summary = stage.get("summary") or {}
        row = _CHECK_ROW.format(
            str(stage.get("name")), str(stage.get("status")),
            summary.get("passed", 0), summary.get("failed", 0),
            summary.get("skipped", 0), summary.get("errors", 0),
            summary.get("total", 0),
            f"{float(stage.get('duration_s') or 0.0):.1f} s")
        if stage.get("reason"):
            row += f"  ({stage['reason']})"
        lines.append(row)

    broken = [item for stage in report.get("stages") or []
              for item in stage.get("items") or []
              if item.get("status") in ("fail", "error")]
    if broken:
        lines.append("failures:")
        lines += _check_named(f"{item.get('id')} — {item.get('message')}"
                              for item in broken)
    if report.get("strict_failures"):
        lines.append(f"strict: {len(report['strict_failures'])} skipped row(s) "
                     f"count as failures")
    if report.get("warnings"):
        lines.append("warnings:")
        lines += _check_named(report["warnings"])
    if report.get("errors"):
        lines.append("harness errors:")
        lines += _check_named(f"{entry.get('type')}: {entry.get('message')}"
                              for entry in report["errors"])
    lines += [f"wrote {path}" for path in written]
    return lines


def _check_named(messages, limit: int = 20) -> list[str]:
    """Bullet lines, capped — a 33-part project with a broken shared import
    would otherwise bury the verdict under its own failures."""
    rows = [str(message).splitlines()[0][:200] if str(message) else ""
            for message in messages]
    shown, extra = rows[:limit], max(0, len(rows) - limit)
    lines = [f"  - {row}" for row in shown]
    if extra:
        lines.append(f"  - (+{extra} more — see the report)")
    return lines


def _check_verdict(report: dict) -> str:
    summary = report.get("summary") or {}
    counts = (f"{summary.get('passed', 0)} passed, "
              f"{summary.get('failed', 0)} failed, "
              f"{summary.get('skipped', 0)} skipped, "
              f"{summary.get('errors', 0)} errors "
              f"of {summary.get('total', 0)}")
    tail = "" if report.get("complete", True) else " — INCOMPLETE (budget)"
    return (f"check: {report.get('status')} — {report.get('project')} · "
            f"{counts} in {float(report.get('duration_s') or 0.0):.1f} s "
            f"(exit {report.get('exit_code')}){tail}")


def _print_check(args, report: dict, written: list[str]) -> None:
    import json

    if args.quiet:
        return
    for line in _check_lines(report, written):
        print(line, file=sys.stderr)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(_check_verdict(report))


#: FR17's trust statement, verbatim in four places: `docs/deployment.md`, the
#: `compose.yaml` header, `agentcad admin user add --help`, and the success
#: output of `agentcad admin user add`. Four, on the PRD-011 precedent that put
#: "the gate is not a security boundary" in eight — because a sentence in one
#: place is a sentence nobody reads.
TRUST_SENTENCE = (
    "an account on this instance can execute arbitrary Python on the host; "
    "give one only to someone you would give a shell to"
)


def _trust_sentence_capitalized() -> str:
    """`TRUST_SENTENCE` as its own sentence.

    **Not `str.capitalize()`**, which lower-cases everything after the first
    character and turns "arbitrary Python" into "arbitrary python" — the
    language is a proper noun, and this string is the one FR17 puts in front of
    every person who is about to be handed an account.
    """
    return TRUST_SENTENCE[0].upper() + TRUST_SENTENCE[1:]

_TRUST_NOTE = (
    f"WARNING: {TRUST_SENTENCE}.\n"
    "A part script is arbitrary Python (agentcad/kernel/worker.py). Since "
    "PRD-006 the Linux worker confines itself (no network, writes only under "
    "the projects tree, no reads of the state dir), but it still runs as the "
    "server user and every project on this instance is readable and writable "
    "to it. Registration is therefore closed, and roles are not a security "
    "boundary between members."
)


def _auth_store():
    """The identity store, with **no service and no kernel**.

    That is what makes `docker compose exec agentcad agentcad admin ...` cheap
    and what lets it work while the server is down or wedged: the state files
    are the authority, the writes are atomic, and `fcntl.flock` is what keeps
    this process and a running server from clobbering each other.
    """
    from .core.appmode import ensure_state_dir
    from .core.authstore import AuthStore

    # `ensure_state_dir`, not `state_dir`: this is usually the FIRST thing to
    # create the directory (the admin CLI runs before any server), and
    # AuthStore's own `parents=True` would leave the parent 0755.
    return AuthStore(ensure_state_dir() / "auth")


def _enrol_url(token: str) -> str:
    import os

    origin = (os.environ.get("AGENTCAD_PUBLIC_ORIGIN") or "").rstrip("/")
    return f"{origin}/api/auth/enrol/{token}"


def cmd_admin(args) -> None:
    """`agentcad admin user add|list|disable` and `agentcad admin enrol`.

    Errors become `SystemExit(2)` with the message on stderr; success prints
    and returns, so `main()` does not exit non-zero on a good run.
    """
    from .core.model import AppError

    try:
        _dispatch_admin(args)
    except AppError as exc:
        print(f"error: {exc.message}", file=sys.stderr)
        raise SystemExit(2) from exc


def _dispatch_admin(args) -> None:
    store = _auth_store()
    action = f"{getattr(args, 'admin_command', '')}:{getattr(args, 'admin_action', '')}"

    if action == "user:add":
        role = "admin" if args.admin else "member"
        token = store.add_user(args.handle, role=role)
        print(f"created {args.handle!r} as {role} (disabled until enrolled)")
        print(f"enrolment link (single-use, 7 days): {_enrol_url(token)}")
        print()
        print(_TRUST_NOTE)
        return

    if action == "user:list":
        users = store.list_users()
        if not users:
            print("no accounts yet — create one with: agentcad admin user add <handle>")
            return
        print(f"{'HANDLE':<34}{'ROLE':<8}{'STATE':<12}")
        for row in users:
            state = ("disabled" if row["disabled"]
                     else "active" if row["enrolled"] else "pending")
            print(f"{row['handle']:<34}{row['role']:<8}{state:<12}")
        return

    if action == "user:disable":
        store.disable_user(args.handle)
        dropped = store.revoke_sessions_for(args.handle)
        print(f"disabled {args.handle!r}; {dropped} session(s) revoked")
        return

    if action == "token:add":
        role = "admin" if args.admin else "member"
        token = store.add_token(args.name, role=role, ttl_days=args.ttl_days)
        print(f"created token {args.name!r} as {role}"
              + (f", expiring in {args.ttl_days} day(s)" if args.ttl_days else ""))
        print(token)
        print()
        # Only the SHA-256 digest is stored, so this is not a policy — it is
        # the only moment the secret exists anywhere outside this terminal.
        print("This is the only time the token is shown. Give it to the agent "
              "as AGENTCAD_TOKEN; if it is lost, revoke it and mint another.")
        return

    if action == "token:list":
        tokens = store.list_tokens()
        if not tokens:
            print("no tokens yet — create one with: "
                  "agentcad admin token add <name>")
            return
        print(f"{'ID':<12}{'NAME':<34}{'ROLE':<8}{'STATE':<10}")
        for row in tokens:
            expires = row.get("expires")
            state = ("revoked" if row["revoked"]
                     else "expired" if expires and expires <= time.time()
                     else "active")
            print(f"{row['id']:<12}{row['name']:<34}{row['role']:<8}{state:<10}")
        return

    if action == "token:revoke":
        store.revoke_token(args.token_id)
        print(f"revoked token {args.token_id!r}; it stops authenticating on "
              f"its next use")
        return

    if getattr(args, "admin_command", "") == "org":
        _admin_org(args)
        return

    if action == "audit:query":
        _admin_audit_query(args)
        return

    if action == "audit:backup":
        _admin_audit_backup(args)
        return

    if getattr(args, "admin_command", "") == "enrol":
        token = store.mint_enrolment(args.handle)
        print(f"new enrolment link for {args.handle!r} "
              f"(single-use, 7 days; any earlier link is now dead):")
        print(_enrol_url(token))
        print()
        print(_TRUST_NOTE)
        return

    raise SystemExit(2)


# --------------------------------------------------------- orgs (PRD-005 FR5)

def _tenancy_store():
    """The orgs document, with **no service and no kernel** — `_auth_store`'s
    property, for `_auth_store`'s reason.

    Rooted on `<state>/auth`, the SAME directory `AuthStore` uses and not a
    sibling of it: `orgs.json` joins the identity documents so that it shares
    their `fcntl.flock` registry rather than deadlocking against it
    (`SecurityConfig.tenancy` composes exactly this path from `store.root`).
    Passing `<state>` here instead would write a second, unread document.
    """
    from .core.appmode import ensure_state_dir
    from .core.tenancy import TenancyStore

    return TenancyStore(ensure_state_dir() / "auth")


def _admin_org(args) -> None:
    """`agentcad admin org add|workspace add|member add` — the bootstrap.

    An org, a workspace inside it and a member of it are the three writes that
    turn a PRD-005a instance into a tenanted one, and until this existed the
    only way to make them was a `python -c` one-liner over `docker compose
    exec` (which is precisely what `.github/workflows/deploy-smoke.yml` did,
    and says it did). They belong to the CLI for the same reason `user add`
    does: the person doing it has shell access and no session, the server may
    not be running, and the state files are the authority.

    **This creates no directory under `<projects_dir>`.** A workspace is an
    entry in a document; the storage root beneath it is made by the first
    write a member makes inside it (`tenancy_wiring`'s root resolver), which
    is why `admin org` needs neither a projects dir nor a service.
    """
    from .core.tenancy import ROLES

    action = getattr(args, "admin_action", "")
    sub = getattr(args, "admin_org_action", "")
    tenants = _tenancy_store()

    if action == "add":
        tenants.create_org(args.org, label=args.label, admin=args.admin)
        made = f"created org {args.org!r}"
        if args.admin:
            print(f"{made} with {args.admin!r} as its admin")
        else:
            # An org with no admin can be administered by nobody until a
            # member is added, so the omission is worth a word rather than a
            # silent success.
            print(f"{made} (no admin yet — "
                  f"`agentcad admin org member add {args.org} <handle> "
                  f"--role admin`)")
        print(f"next: agentcad admin org workspace add {args.org} <workspace>")
        return

    if action == "workspace" and sub == "add":
        tenants.create_workspace(args.org, args.workspace, label=args.label)
        print(f"created workspace {args.org}/{args.workspace}")
        print(f"members of {args.org!r} address it with "
              f"`X-Agentcad-Workspace: {args.org}/{args.workspace}` (or with "
              f"nothing at all, if it is their only one)")
        return

    if action == "member" and sub == "add":
        tenants.add_member(args.org, args.handle, args.role)
        print(f"added {args.handle!r} to {args.org!r} as {args.role}")
        print(f"that is their DEFAULT role on every project in the org "
              f"({' < '.join(ROLES)}); a per-project grant overrides it "
              f"(`grant_role`), and an org admin outranks any override.")
        return

    raise SystemExit(2)


# ------------------------------------------------------- audit (PRD-005 FR12)

def _audit_log():
    """The audit databases, with **no service and no kernel**.

    `_auth_store`'s property, for its reason: `docker compose exec agentcad
    agentcad admin audit …` has to work while the server is running *and*
    while it is down. It works while it is running because the store is SQLite
    in WAL mode with a 30 s busy timeout — the second-writer case that mode
    exists for (`core/audit.py`) — not because of any lock this process takes.
    """
    from .core.appmode import ensure_state_dir
    from .core.audit import shared

    return shared(ensure_state_dir())


def _admin_audit_query(args) -> None:
    import json

    from .core.audit import parse_time

    log = _audit_log()
    org = args.org
    if not log.path_for(org).exists():
        print(f"no audit log for org {org!r} yet "
              f"(auth events live in the '_instance' log)")
        return
    rows = log.query(org,
                     principal=args.principal, project=args.project,
                     action=args.action,
                     since=parse_time(args.since, "--since"),
                     until=parse_time(args.until, "--until"),
                     limit=args.limit)
    if args.json:
        print(json.dumps({"org": org, "rows": rows,
                          "total": log.count(org)}, indent=2))
        return
    if not rows:
        print(f"no matching rows in {org!r} ({log.count(org)} total)")
        return
    print(f"{'WHEN (UTC)':<21}{'PRINCIPAL':<34}{'ACTION':<22}"
          f"{'PROJECT':<18}{'OUTCOME':<18}")
    for row in rows:
        print(f"{_audit_when(row['ts']):<21}{row['principal'][:33]:<34}"
              f"{row['action'][:21]:<22}{(row['project'] or '-')[:17]:<18}"
              f"{row['outcome'][:17]:<18}")
    print()
    print(f"{len(rows)} row(s) of {log.count(org)} in {org!r}, newest first.")


def _audit_when(ts: float) -> str:
    from datetime import datetime, timezone

    return datetime.fromtimestamp(float(ts), timezone.utc).strftime(
        "%Y-%m-%d %H:%M:%S")


def _admin_audit_backup(args) -> None:
    """`VACUUM INTO`, and the reason it is not `cp`.

    A WAL database keeps recent commits in its `-wal` sidecar: the PRD-005
    spike copied a 50-row audit database and the copy answered `no such table:
    audit`. This writes a checkpointed, integrity-checked single file while the
    server keeps writing, which is what makes it a backup of a *running*
    instance — and it is the step `docs/deployment.md` adds to the tar-the-
    volume story, which stays true for the identity JSON and was never true for
    this.
    """
    log = _audit_log()
    dest = Path(args.dest)
    if not log.path_for(args.org).exists():
        print(f"error: no audit log for org {args.org!r}", file=sys.stderr)
        raise SystemExit(2)
    log.vacuum_into(args.org, dest)
    print(f"backed up {args.org!r} -> {dest} "
          f"({dest.stat().st_size} bytes, integrity: "
          f"{log.integrity(args.org)})")


def cmd_check(args) -> int:
    """`agentcad check` — certify a project and answer with an exit code.

    Headless by construction: one service, one warm kernel, the tool registry —
    **no FastAPI app, no port, no chat engine and no API key**. The kernel is
    stopped in a ``finally``, because a crashed run must not leave workers (or,
    with ``--ref``, a registered git worktree) behind.

    The exit code is the API (design Decision 6): ``0`` green · ``1`` red, the
    model is wrong · ``2`` harness, we could not produce a verdict. A blown
    ``--budget`` is 2 **with the partial report written** — evidence beats
    silence — and so is an unwritable report path, an unknown project and any
    unexpected exception. Everything a check *measured* is payload: a failing
    part is a red report, never a traceback.

    Identity is ``ci`` so a run never collides with a human's per-client
    checkout, and so a proposal post classifies as an agent action.

    ``--proposal``/``--auto-proposal`` attach the report to a change proposal,
    where it becomes that proposal's ``checks`` gate. The target is resolved
    *before* the run (a mistyped id must not cost a full rebuild) and the post
    happens *after* the report files are written, from the report exactly as it
    was measured. Refusing to post is exit 2 — except when the project has no
    proposals at all (no git), which is a warning: the check itself still ran.
    """
    from .core import locks
    from .core.checks import CheckRunner, refuse_work_dir_overlap
    from .core.model import AppError
    from .core.tools import build_registry

    try:
        stages = _check_stages(args.stages)
    except ValueError as exc:
        print(f"agentcad check: {exc}", file=sys.stderr)
        return 2

    # Setup is INSIDE the mapping: creating the work dir and starting the kernel
    # are as able to fail as the run itself (an unwritable --work-dir, a
    # projects dir that is a file), and a traceback out of here would be process
    # exit 1 — the code reserved for "the model is wrong".
    service = None
    post_to = args.proposal
    try:
        # Accepted, created and granted before the kernel spawns — in that
        # order (review I1). The overlap refusal has to run here, not only
        # inside the run: a `--work-dir` is a writable root, a Landlock rule on
        # a path that does not exist is ENOENT, and the grant is lost with it.
        # So the CLI creates an accepted one, and creating it is safe precisely
        # because it has been accepted first ("a refused path leaves nothing
        # behind"). `CheckRunner._work_dir` asks the same question again with
        # the authoritative canonical path, and the runner still never deletes
        # a directory it did not make.
        projects_root = _projects_dir(args)
        canonical = (Path(args.project).expanduser().resolve()
                     if _is_path(args.project)
                     else Path(projects_root).expanduser() / args.project)
        extra_writable: list[str] = []
        work_dir = _accept_work_dir(
            args.work_dir,
            lambda root: refuse_work_dir_overlap(root, canonical,
                                                 projects_root))
        if work_dir:
            extra_writable.append(work_dir)
        # A project given as a path is the CI case (`--project .` on a checkout)
        # and it lives nowhere `_writable_roots` guessed, so the kernel could not
        # write its `.cache/` — every part would fail to build with a
        # PermissionError instead of a verdict. It is known here, before the
        # workers spawn, which is the only moment the seatbelt profile can still
        # be widened.
        if _is_path(args.project):
            extra_writable.append(str(canonical))

        service = _build_service(
            projects_root,
            extra_writable=extra_writable or None)
        locks.set_client_id("ci")
        registry = build_registry(service)
        project = args.project
        if _is_path(project):
            project = service.open_project(project)["name"]
        # `service.checks` is the tool pack's runner once slice 5 lands; until
        # then (and for a bare service) build one over the same registry.
        runner = getattr(service, "checks", None) or CheckRunner(service, registry)
        if (post_to or args.auto_proposal) and not _can_post(runner):
            # No git, no proposals, nothing to post to. A warning rather than
            # exit 2: the check itself ran and its verdict is honest, and this
            # is exactly the CI-runner case (a checkout has no .history repo).
            _post_note("--proposal/--auto-proposal: this project has no "
                       "proposals (they need git history); the report will "
                       "not be posted")
            post_to, args.auto_proposal = None, False
        elif post_to:
            # Resolved BEFORE the kernel measures anything: a mistyped id or a
            # merged proposal should cost a millisecond, not a full rebuild.
            runner.post_target(project, post_to)
        report = runner.run(
            project, ref=args.ref, stages=stages, strict=args.strict,
            budget_s=args.budget, min_volume=args.min_volume,
            verify_determinism=args.verify_determinism, sha=args.sha,
            ref_label=args.ref_label, work_dir=work_dir)
    except AppError as exc:
        print(f"agentcad check: {exc.message}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 — any harness failure is exit 2
        print(f"agentcad check: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    finally:
        # Tolerant of a partial construction: setup may have failed before
        # there was a kernel to stop, and a kernel that will not stop must not
        # replace the verdict with its own traceback.
        if service is not None:
            try:
                service.kernel.stop()
            except Exception as exc:  # noqa: BLE001
                print(f"agentcad check: the kernel did not stop cleanly: "
                      f"{exc}", file=sys.stderr)
            _release_work_root(service)

    # Everything after the run is under the SAME exit-code mapping as the run
    # itself: writing the report, posting it and printing it can all fail (an
    # unreadable proposals index, an audit append that will not write), and a
    # traceback out of here would exit 1 — the code reserved for "the model is
    # wrong". The report is written first, so a post that fails still leaves
    # the evidence on disk.
    try:
        written = _write_check_outputs(args, report)
        if written is None:
            return 2
        # Posted AFTER the files are written, and from the report exactly as it
        # was measured: the copy on disk and the copy in the proposal are the
        # same document, because a check never edits a verdict it has already
        # produced.
        override = _post_check(runner, project, args, report, post_to)
        _print_check(args, report, written)
    except AppError as exc:
        print(f"agentcad check: {exc.message}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 — any harness failure is exit 2
        print(f"agentcad check: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    return override if override is not None else int(report.get("exit_code", 2))


_PACKAGE_ROW = "  {:<11} {:<6} {:>5} {:>5} {:>5} {:>6} {:>6}  {:>7}"


def _package_lines(report: dict, written: list[str]) -> list[str]:
    """The stage table and what went wrong, for a human reading stderr."""
    package = report.get("package") or {}
    facts = [f"{package.get('name')}@{package.get('version')}"]
    if package.get("content_id"):
        facts.append(str(package["content_id"]))
    if report.get("strict"):
        facts.append("strict")
    lines = [" · ".join(facts),
             _PACKAGE_ROW.format("stage", "status", "pass", "fail", "skip",
                                 "error", "total", "time")]
    for stage in report.get("stages") or []:
        summary = stage.get("summary") or {}
        row = _PACKAGE_ROW.format(
            str(stage.get("name")), str(stage.get("status")),
            summary.get("passed", 0), summary.get("failed", 0),
            summary.get("skipped", 0), summary.get("errors", 0),
            summary.get("total", 0),
            f"{float(stage.get('duration_s') or 0.0):.1f} s")
        if stage.get("reason"):
            row += f"  ({stage['reason']})"
        lines.append(row)

    broken = [item for stage in report.get("stages") or []
              for item in stage.get("items") or []
              if item.get("status") in ("fail", "error")]
    if broken:
        lines.append("failures:")
        lines += _check_named(f"{item.get('id')} — {item.get('message')}"
                              for item in broken)
    if report.get("exempt_skips"):
        # What was NOT measured, named — that is what stops "validated" from
        # becoming a badge.
        lines.append("not measured (exempt from the publish verdict):")
        lines += _check_named(report["exempt_skips"])
    if report.get("blockers") and not broken:
        lines.append("blocking publication:")
        lines += _check_named(report["blockers"])
    if report.get("strict_failures"):
        lines.append(f"strict: {len(report['strict_failures'])} skipped row(s) "
                     f"count as failures")
    if report.get("warnings"):
        lines.append("warnings:")
        lines += _check_named(report["warnings"])
    if report.get("errors"):
        lines.append("harness errors:")
        lines += _check_named(f"{entry.get('type')}: {entry.get('message')}"
                              for entry in report["errors"])
    lines += [f"wrote {path}" for path in written]
    return lines


def _package_verdict(report: dict) -> str:
    package = report.get("package") or {}
    summary = report.get("summary") or {}
    counts = (f"{summary.get('passed', 0)} passed, "
              f"{summary.get('failed', 0)} failed, "
              f"{summary.get('skipped', 0)} skipped, "
              f"{summary.get('errors', 0)} errors "
              f"of {summary.get('total', 0)}")
    tail = "" if report.get("complete", True) else " — INCOMPLETE (budget)"
    return (f"package validate: {report.get('status')} — "
            f"{package.get('name')}@{package.get('version')} · {counts} in "
            f"{float(report.get('duration_s') or 0.0):.1f} s · publishable: "
            f"{'yes' if report.get('publishable') else 'no'} "
            f"(exit {report.get('exit_code')}){tail}")


def cmd_package(args) -> int:
    if args.package_command == "validate":
        return cmd_package_validate(args)
    print("agentcad package: expected a subcommand (validate)",
          file=sys.stderr)
    return 2


def cmd_package_validate(args) -> int:
    """`agentcad package validate <dir>` — run the publish gate over a package.

    ``cmd_check``'s shape exactly, for the same reasons: setup is **inside**
    the exit-code mapping (an unwritable ``--work-dir`` or a projects dir that
    is a file is exit 2, not a traceback), the kernel is stopped in a
    ``finally`` so a crashed run leaves no workers, and the identity is ``ci``
    so a run never collides with a human's per-client checkout.

    The exit code is the API: ``0`` green · ``1`` red — **the package is
    wrong** · ``2`` harness — we could not produce a verdict. A blown
    ``--budget`` is 2 with the partial report written, because evidence beats
    silence.

    ``--report`` writes the JSON document `gate.validate_gate_report` accepts,
    which is PRD-004's report shape with the gate's `package`, `note` and
    verdict beside it.
    """
    import json

    from .core import locks
    from .core.model import AppError
    from .core.packages.gate import (SECURITY_NOTE, PackageGate,
                                     refuse_work_dir_overlap)

    service = None
    try:
        # Accepted, created and granted before the workers spawn (review I1):
        # a `--work-dir` is a writable root, and a Landlock grant on a path
        # that does not exist is ENOENT — the grant is lost and every part
        # fails with a PermissionError instead of producing a verdict.
        # Creating it is safe only after the overlap guard has accepted it, so
        # both happen here; `PackageGate._work_root` asks again inside the run.
        projects_root = _projects_dir(args)
        source = Path(args.path).expanduser().resolve()
        extra_writable: list[str] = []
        work_dir = _accept_work_dir(
            args.work_dir,
            lambda root: refuse_work_dir_overlap(root, projects_root, source))
        if work_dir:
            extra_writable.append(work_dir)
        service = _build_service(
            projects_root,
            extra_writable=extra_writable or None)
        locks.set_client_id("ci")
        report = PackageGate(service).run(
            args.path, strict=args.strict,
            work_dir=work_dir, budget_s=args.budget)
    except AppError as exc:
        print(f"agentcad package validate: {exc.message}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 — any harness failure is exit 2
        print(f"agentcad package validate: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return 2
    finally:
        if service is not None:
            try:
                service.kernel.stop()
            except Exception as exc:  # noqa: BLE001
                print(f"agentcad package validate: the kernel did not stop "
                      f"cleanly: {exc}", file=sys.stderr)
            _release_work_root(service)

    try:
        written: list[str] = []
        if args.report:
            path = Path(args.report).expanduser()
            try:
                from .core.project import ProjectStore

                ProjectStore._atomic_write(
                    path, (json.dumps(report, indent=2) + "\n").encode())
            except OSError as exc:
                print(f"agentcad package validate: could not write {path}: "
                      f"{exc}", file=sys.stderr)
                return 2
            written.append(str(path))
        for line in _package_lines(report, written):
            print(line, file=sys.stderr)
        # Once, at the end, above the verdict — never a footnote nobody reads.
        print(SECURITY_NOTE, file=sys.stderr)
        print(_package_verdict(report))
    except Exception as exc:  # noqa: BLE001 — any harness failure is exit 2
        print(f"agentcad package validate: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return 2
    return int(report.get("exit_code", 2))


def cmd_publish(args) -> int:
    """`agentcad publish <dir> --index <name>` — gate, then publish.

    ``cmd_package_validate``'s shape, with the same exit-code API: ``0``
    published (or yanked) · ``1`` **refused** — the gate is red, the version
    already exists, the vendor flag forbids it, or the tree moved · ``2``
    harness, we could not produce a verdict. A refusal is a verdict, which is
    why it is 1 and not 2.

    `agentcad package validate` is report-honest and this is **fail-closed**:
    it runs **every** stage and takes no stage subset, precisely so a
    `skip / not_selected` can never reach the verdict.

    ``--yank name@version`` measures nothing, so it **starts no kernel** — it
    reads the index, flips a flag and rewrites the document.
    """
    from .core import locks
    from .core.model import AppError
    from .core.packages import indexes as index_module
    from .core.packages.gate import SECURITY_NOTE, refuse_work_dir_overlap

    service = None
    try:
        from . import config as user_config

        loader_warnings: list[str] = []
        # The bundled catalog is an index like any other here: `publish
        # --index agentcad-core` is how the seed catalog's entries are
        # written, and `index.json` is a build product of the gate.
        configured = index_module.load_indexes(
            index_module.merge_bundled(user_config.load_config(),
                                       bundled_index_entries()),
            loader_warnings)
        index = next((i for i in configured if i.name == args.index), None)
        if index is None:
            for warning in loader_warnings:
                print(f"agentcad publish: {warning}", file=sys.stderr)
            print(f"agentcad publish: no index named {args.index!r} is "
                  f"configured (configured: "
                  f"{[i.name for i in configured]}). Add one to "
                  f"{user_config.config_path()}.", file=sys.stderr)
            return 2
        if args.yank:
            name, sep, version = str(args.yank).partition("@")
            if not sep or not name or not version:
                print("agentcad publish: --yank takes name@version, e.g. "
                      "--yank iso4762@1.2.0", file=sys.stderr)
                return 2
            result = index.yank(name, version)
            print(f"publish: yanked {name}@{version} in index "
                  f"{index.name!r}"
                  + (" (it was already yanked)" if result["already"] else "")
                  + " — nothing was deleted; a lockfile naming it keeps "
                    "resolving")
            return 0
        if not args.path:
            print("agentcad publish: expected a package directory (or "
                  "--yank name@version)", file=sys.stderr)
            return 2

        # Accepted, created and granted before the workers spawn — see
        # `cmd_package_validate` and `_accept_work_dir` (review I1).
        projects_root = _projects_dir(args)
        source = Path(args.path).expanduser().resolve()
        extra_writable: list[str] = []
        work_dir = _accept_work_dir(
            args.work_dir,
            lambda root: refuse_work_dir_overlap(root, projects_root, source))
        if work_dir:
            extra_writable.append(work_dir)
        service = _build_service(
            projects_root,
            extra_writable=extra_writable or None)
        locks.set_client_id("ci")
        result = index_module.publish(index, args.path, service,
                                      work_dir=work_dir,
                                      budget_s=args.budget)
    except AppError as exc:
        # A refusal IS a verdict — the gate was red, the version exists, the
        # vendor flag forbids it, or the tree moved — so it is exit 1, and the
        # evidence is printed rather than summarised away.
        report = (exc.details or {}).get("checks")
        if report:
            print("failures:", file=sys.stderr)
            for item in report[:20]:
                print(f"  - {item.get('id')} — {item.get('message')}",
                      file=sys.stderr)
        # An INCOMPLETE run has no failing rows at all, so the block above
        # prints nothing and the refusal used to say "0 blocker(s)". The
        # warnings ARE the evidence in that case — a budget that ran out, a
        # tree that moved — so they are printed rather than dropped.
        for warning in (exc.details or {}).get("warnings") or []:
            print(f"  ! {warning}", file=sys.stderr)
        print(f"agentcad publish: {exc.message}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 — any harness failure is exit 2
        print(f"agentcad publish: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    finally:
        if service is not None:
            try:
                service.kernel.stop()
            except Exception as exc:  # noqa: BLE001
                print(f"agentcad publish: the kernel did not stop cleanly: "
                      f"{exc}", file=sys.stderr)
            _release_work_root(service)

    for line in _package_lines(result["report"], []):
        print(line, file=sys.stderr)
    print(SECURITY_NOTE, file=sys.stderr)
    print(f"publish: {result['published']} → index {result['index']!r} · "
          f"{result['content_id']} · gate "
          f"{result['report'].get('status')}")
    return 0


def cmd_materials(args) -> int:
    if args.materials_command == "lint":
        return cmd_materials_lint(args)
    print("agentcad materials: expected a subcommand (lint)", file=sys.stderr)
    return 2


def cmd_materials_lint(args) -> int:
    """`agentcad materials lint <path>…` — the material card lint (PRD-028).

    Deliberately thin: every rule lives in `core/materials_lint.py`, which is
    pure, so the community repo's CI runs exactly what this prints. No
    service, no kernel — linting a card must never build anything.

    The exit code is the API: ``0`` clean · ``1`` at least one error · ``2``
    usage — no paths, or a path we could not read (a file that exists and is
    broken JSON is an *error*, not a usage failure).
    """
    import json

    from .core.materials_lint import has_errors, lint_paths

    if not args.paths:
        print("agentcad materials lint: expected at least one path",
              file=sys.stderr)
        return 2
    try:
        findings = lint_paths(args.paths, args.profile)
    except OSError as exc:
        print(f"agentcad materials lint: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps([f.to_dict() for f in findings], indent=2))
    else:
        for f in findings:
            where = f"{f.file or '-'}:{f.id}"
            if f.property:
                where += f".{f.property}"
            print(f"{f.level} {where} {f.code}: {f.message}")
        errors = sum(1 for f in findings if f.level == "error")
        print(f"{errors} errors, {len(findings) - errors} warnings")
    return 1 if has_errors(findings) else 0


def cmd_skill(args) -> int:
    if args.skill_command == "new":
        return cmd_skill_new(args)
    if args.skill_command == "lint":
        return cmd_skill_lint(args)
    print("agentcad skill: expected a subcommand (new, lint)", file=sys.stderr)
    return 2


def cmd_skill_new(args) -> int:
    """`agentcad skill new <name> (--project P | --dir D)` — the scaffold.

    `--project` resolves through `_projects_dir`, the same helper `serve` and
    every other project command use, so an author cannot scaffold into a tree
    the server is not serving. The skill lands at
    `<projects>/<project>/skills/<name>/SKILL.md`, which is inside the
    project's working tree and therefore git-tracked, branched and merged like
    a part script.
    """
    from .core.skills_lint import scaffold

    if bool(args.project) == bool(args.dir):
        print("agentcad skill new: exactly one of --project or --dir is "
              "required", file=sys.stderr)
        return 2
    if args.project:
        root = _projects_dir(args) / args.project
        if not (root / "project.json").is_file():
            print(f"agentcad skill new: no project {args.project!r} under "
                  f"{_projects_dir(args)}", file=sys.stderr)
            return 2
        target = root / "skills"
    else:
        target = Path(args.dir)
    try:
        path = scaffold(target, args.name)
    except (ValueError, FileExistsError, OSError) as exc:
        print(f"agentcad skill new: {exc}", file=sys.stderr)
        return 2
    print(f"wrote {path}")
    print(f"edit it, then run: agentcad skill lint {path.parent}")
    return 0


def cmd_skill_lint(args) -> int:
    """`agentcad skill lint [<path>…] [--core]` — the skill lint (PRD-029).

    Thin by design: every rule lives in `core/skills_lint.py`, which is pure,
    so the core-library test, a future marketplace gate and this command all
    run the same function. A path may be one skill directory, a flat
    `<name>.md`, or a `skills/` directory holding many.

    Exit codes are the API: ``0`` clean · ``1`` at least one error · ``2``
    usage (no paths at all).
    """
    import json

    from .core.skills import CORE_DIR
    from .core.skills_lint import has_errors, lint_paths

    paths = list(args.paths)
    profile = args.profile or ("library" if args.core else "user")
    if args.core:
        paths.append(str(CORE_DIR))
    if not paths:
        print("agentcad skill lint: expected at least one path (or --core)",
              file=sys.stderr)
        return 2
    findings = lint_paths(paths, profile)

    if args.json:
        print(json.dumps([f.to_dict() for f in findings], indent=2))
    else:
        for finding in findings:
            print(finding.row())
        errors = sum(1 for f in findings if f.level == "error")
        print(f"{errors} errors, {len(findings) - errors} warnings "
              f"({profile} profile, {len(paths)} path(s): "
              f"{', '.join(paths)})")
    return 1 if has_errors(findings) else 0


# ==========================================================================
# PRD-005 slice 6 — the sync CLI (FR8-client/FR10).
#
# `login`, `clone`, `push`, `pull`, `status` and the plumbing `credential`
# helper. Everything they do lives in `core/sync.py`; what is here is argument
# handling, printing and the exit code — plus the one thing only a CLI can do,
# which is build a real service with a warm kernel when a divergent pull has to
# drive the PRD-001 merge machinery.
#
# Exit codes, the `check` convention: 0 it worked · 1 a refusal the user can
# act on (the server said no, a merge has conflicts, a branch diverged) ·
# 2 the harness could not answer (a bad URL, no git, an unreachable instance).
# ==========================================================================

def _sync_error(command: str, exc) -> int:
    """Print a sync failure the way the server said it, then exit 1.

    The ``remote:`` lines are printed **first and verbatim**: the pre-receive
    hook's ``agentcad: refs/heads/master diverged — pull and merge, never
    force`` is the actionable sentence, and re-wording it here would be
    inventing a second, worse error message (see `core/sync.remote_lines`).
    """
    for line in getattr(exc, "remote", []) or []:
        print(f"{command}: remote: {line}", file=sys.stderr)
    print(f"{command}: {exc}", file=sys.stderr)
    return 1


def _sync_target(args) -> Path:
    """The project directory a sync command works on (default: the cwd)."""
    return Path(getattr(args, "dir", None) or ".").expanduser().resolve()


def _not_a_project(command: str, target: Path) -> bool:
    """True (having said so) when *target* has no history repo.

    Separated from the ``SyncError`` path so it can be **exit 2**: "you are in
    the wrong directory" is a usage mistake, not a refusal by the other side,
    and a script that treats every non-zero exit as "the server said no" should
    not be told those two apart only by reading English.
    """
    if (target / ".history").is_dir():
        return False
    print(f"{command}: {target} is not an AgentCAD project (no .history/); "
          "clone one with `agentcad clone <url>` or run this inside a project",
          file=sys.stderr)
    return True


def cmd_login(args) -> int:
    """Store an instance token, after proving it works."""
    from .core import sync

    token = (args.token or os.environ.get("AGENTCAD_TOKEN") or "").strip()
    if not token:
        # Read from stdin when it is a pipe, so a token can arrive without ever
        # being on an argv (where `ps` shows it to every user on the box) or in
        # a shell history file.
        if not sys.stdin.isatty():
            token = sys.stdin.readline().strip()
    if not token:
        print("agentcad login: no token. Pass --token, pipe it on stdin, or "
              "set AGENTCAD_TOKEN. Mint one on the instance with "
              "`agentcad admin token add <name>`.", file=sys.stderr)
        return 2
    try:
        result = sync.login(args.url, token)
    except ValueError as exc:
        print(f"agentcad login: {exc}", file=sys.stderr)
        return 2
    except sync.SyncError as exc:
        return _sync_error("agentcad login", exc)
    who = result["principal"] or "an unauthenticated local instance"
    print(f"signed in to {result['instance']} as {who}")
    print(f"token stored 0600 in {sync.config_path()}")
    if result["insecure"]:
        print("warning: that instance is plain http, so the token crosses the "
              "network in the clear on every push", file=sys.stderr)
    return 0


def cmd_clone(args) -> int:
    """Clone a hosted project into the ProjectHistory layout."""
    from .core import sync

    try:
        parsed = sync.parse_repo_url(args.url)
    except ValueError as exc:
        print(f"agentcad clone: {exc}", file=sys.stderr)
        return 2
    dest = (Path(args.dest).expanduser() if args.dest
            else _projects_dir(args) / parsed["project"])
    if not sync.token_for(args.url) and not args.token:
        print(f"agentcad clone: not signed in to {parsed['instance']}; run "
              f"`agentcad login {parsed['instance']}` first (a public instance "
              f"still clones — this is a warning, not a refusal)",
              file=sys.stderr)
    try:
        result = sync.clone(args.url, dest, token=args.token)
    except sync.SyncError as exc:
        return _sync_error("agentcad clone", exc)
    print(f"cloned {result['project']} into {result['path']} "
          f"(branch {result['branch']})")
    return 0


def cmd_push(args) -> int:
    """Push every branch and tag. Never forces, never deletes."""
    from .core import sync

    target = _sync_target(args)
    if _not_a_project("agentcad push", target):
        return 2
    try:
        result = sync.push(target)
    except sync.SyncError as exc:
        return _sync_error("agentcad push", exc)
    if not result["updated"]:
        print(f"everything up to date on {result['remote']}")
        return 0
    for update in result["updated"]:
        print(f"  {update['flag']} {update['ref']}  {update['summary']}")
    print(f"pushed {len(result['updated'])} ref(s) to {result['remote']}")
    return 0


def cmd_pull(args) -> int:
    """Fetch, fast-forward what can fast-forward, merge what cannot.

    The kernel is started **lazily and only for a divergence**: a fetch that
    finds nothing to merge should cost a network round trip, not three seconds
    of worker spawn. When it is needed, the service is built over the clone's
    parent directory with the project itself added to the sandbox's writable
    roots (`cmd_check`'s reasoning: the merge validation pass builds parts, and
    a confined worker that cannot write `.cache/` fails every one of them).
    """
    from .core import sync

    target = _sync_target(args)
    if _not_a_project("agentcad pull", target):
        return 2
    state: dict = {}

    def merger(branch: str, remote_ref: str) -> dict:
        if "service" not in state:
            service = _build_service(target.parent,
                                     extra_writable=[str(target)])
            state["service"] = service
            from .core.tools import build_registry

            build_registry(service)     # installs branches + merges (PRD-001)
            state["proj"] = service.open_project(str(target))["name"]
        return sync.merge_diverged(state["service"], state["proj"], branch,
                                   remote_ref, allow_invalid=args.allow_invalid)

    try:
        result = sync.pull(target, merger=None if args.no_merge else merger)
    except sync.SyncError as exc:
        return _sync_error("agentcad pull", exc)
    except Exception as exc:                    # noqa: BLE001 — harness exit
        print(f"agentcad pull: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    finally:
        service = state.get("service")
        if service is not None:
            try:
                service.kernel.stop()
            except Exception as exc:            # noqa: BLE001
                print(f"agentcad pull: the kernel did not stop cleanly: {exc}",
                      file=sys.stderr)
            _release_work_root(service)
    return _print_pull(result)


def _print_pull(result: dict) -> int:
    moved = 0
    for entry in result["branches"]:
        if entry["action"] == "fast_forward":
            moved += 1
            print(f"  {entry['branch']}: fast-forwarded {entry['behind']} "
                  f"commit(s)")
        elif entry["action"] == "merged" and "error" not in (entry["merge"] or {}):
            moved += 1
            merge = entry["merge"]
            what = ("already up to date" if merge.get("already_up_to_date")
                    else f"merged as {str(merge.get('commit') or '')[:8]}")
            print(f"  {entry['branch']}: {what}")
    conflicted = result["conflicts"]
    for entry in conflicted:
        details = (entry["merge"]["error"].get("details") or {})
        print(f"agentcad pull: {entry['merge']['error']['message']}",
              file=sys.stderr)
        for conflict in details.get("conflicts") or []:
            key = conflict.get("path") or conflict.get("key")
            print(f"  {conflict.get('kind', 'file'):<9} {key}", file=sys.stderr)
        if details.get("hint"):
            print(f"{details['hint']}", file=sys.stderr)
    if result["diverged"]:
        print("agentcad pull: diverged from the server: "
              + ", ".join(result["diverged"])
              + ". Nothing was changed — re-run without --no-merge to merge "
                "them (the merge is staged and kernel-validated; your branch "
                "moves only when it lands).", file=sys.stderr)
    if conflicted or result["diverged"]:
        print("Nothing was overwritten.", file=sys.stderr)
        return 1
    if not moved:
        print("already up to date")
    return 0


def cmd_status(args) -> int:
    """Ahead/behind per branch against the server."""
    from .core import sync

    target = _sync_target(args)
    if _not_a_project("agentcad status", target):
        return 2
    try:
        report = sync.status(target, fetch=args.fetch)
    except sync.SyncError as exc:
        return _sync_error("agentcad status", exc)
    if args.json:
        import json as _json

        print(_json.dumps(report, indent=2))
        return 0
    print(f"{report['path']}  ->  {report['remote'] or '(no remote)'}")
    for entry in report["branches"]:
        detail = ""
        if entry["ahead"] or entry["behind"]:
            detail = f"  (ahead {entry['ahead']}, behind {entry['behind']})"
        print(f"  {entry['branch']:<24} {entry['state']}{detail}")
    if report["tags"]:
        print(f"  tags: {', '.join(report['tags'])}")
    if report["dirty"]:
        print(f"  uncommitted: {len(report['dirty'])} file(s)")
    return 1 if report["diverged"] else 0


def cmd_credential(args) -> int:
    """``agentcad credential <get|store|erase>`` — git's credential protocol.

    Git runs this; a human never does. It reads a ``key=value`` query on stdin
    and, for a host we are signed in to, writes back a username and the token
    — which is why no AgentCAD git call ever puts a token on an argv or in a
    URL, both of which the PRD-005 spike measured leaking (§A9).

    Stdout is the protocol: nothing else may be printed here.
    """
    from .core import sync

    return sync.credential_main(args.action, sys.stdin, sys.stdout)


# ===================== end of the PRD-005 slice 6 block ====================


def main() -> None:
    parser = argparse.ArgumentParser(prog="agentcad", description="Agentic-first CAD")
    parser.add_argument("--version", action="version", version=agentcad.__version__)
    # metavar hides the internal `worker` subcommand from usage/help.
    sub = parser.add_subparsers(
        dest="command",
        metavar="{serve,open,mcp,new,export,check,bench,package,publish,admin,"
                "materials,skill,login,clone,push,pull,status}")

    for name in ("serve", "open"):
        p = sub.add_parser(name, help=f"{name} the AgentCAD server")
        p.add_argument("--port", type=int, default=None,
                       help="listen port (default: $AGENTCAD_PORT, else the "
                            "stored config, else 8630)")
        p.add_argument("--host", default=None,
                       help="listen address (default: $AGENTCAD_HOST, else "
                            "127.0.0.1). Binding a non-loopback interface "
                            "requires AGENTCAD_MODE=hosted: in local mode the "
                            "server has no authentication at all")
        p.add_argument("--projects-dir", default=None,
                       help="projects root (default: $AGENTCAD_PROJECTS_DIR, "
                            "else ~/AgentCAD/projects)")
        p.add_argument("--no-open", action="store_true")

    p = sub.add_parser("mcp", help="run the MCP stdio server",
                       description="Expose AgentCAD's tools to an MCP client "
                                   "over stdio. Without --remote it proxies a "
                                   "local server (starting one if needed); "
                                   "with it, a hosted instance.")
    p.add_argument("--remote", default=None, metavar="URL",
                   help="proxy this hosted instance instead of a local server "
                        "(PRD-005 FR10). Every tool call is a network round "
                        "trip, and a remote instance is never auto-started")
    p.add_argument("--token", default=None, metavar="TOKEN",
                   help="bearer token for --remote (default: the one "
                        "`agentcad login` stored, else $AGENTCAD_TOKEN). "
                        "NOTE: a token on the command line is visible in `ps` "
                        "to every user on this machine — prefer `agentcad "
                        "login`, which never puts it on an argv")

    # Hidden: kernel worker loop (used by frozen bundles to re-exec themselves).
    sub.add_parser("worker")

    p = sub.add_parser("new", help="create a new project")
    p.add_argument("name")
    p.add_argument("--projects-dir", default=None)

    p = sub.add_parser("export", help="export a part")
    p.add_argument("project", help="project name or path")
    p.add_argument("part")
    p.add_argument("--format", default="step", choices=["step", "stl", "3mf"])
    p.add_argument("--config", default=None,
                   help="export one declared configuration (pure resolution) "
                        "to exports/<part>_<config>.<format>")
    p.add_argument("-o", "--output", default=None)
    p.add_argument("--projects-dir", default=None)

    p = sub.add_parser(
        "check", help="certify a project: build, assembly, specs, drawings",
        description="Rebuild every part, re-resolve the assembly, evaluate the "
                    "design specs and regenerate the drawings — headless, with "
                    "no server. Exit 0 green, 1 red, 2 harness.")
    p.add_argument("--project", default=".",
                   help="project name or path (default: the current directory)")
    p.add_argument("--projects-dir", default=None)
    p.add_argument("--ref", default=None,
                   help="check this branch/tag/commit instead of the working "
                        "tree (materialized into a throwaway worktree)")
    p.add_argument("--stages", default=None,
                   metavar="build,assembly,specs,drawings",
                   help="comma-separated subset of the stages to run")
    p.add_argument("--report", default=None, metavar="PATH",
                   help="write the JSON report here")
    p.add_argument("--md", default=None, metavar="PATH",
                   help="write the markdown summary here "
                        "($GITHUB_STEP_SUMMARY, a PR comment)")
    p.add_argument("--strict", action="store_true",
                   help="count skipped rows as failures (rows keep their "
                        "status; only the verdict moves). A row marked "
                        "strict_exempt — an unconditional skip, today only the "
                        "DXF determinism row — is never counted")
    p.add_argument("--verify-determinism", action="store_true",
                   help="build every part a second time on a cold cache and "
                        "compare the artefacts byte for byte")
    p.add_argument("--budget", default=None, metavar="SECONDS",
                   type=_finite_arg("--budget",
                                    "a NaN deadline is never in the past, so "
                                    "it bounds nothing"),
                   help="deadline read before every item and every kernel "
                        "call; a build (300 s) or drawing (120 s) already in "
                        "flight cannot be preempted, so the worst case is one "
                        "such call")
    p.add_argument("--min-volume", default=0.001, metavar="MM3",
                   type=_finite_arg("--min-volume",
                                    "every comparison with NaN is false, so a "
                                    "real overlap would report green"),
                   help="interference volume below which an overlap is noise")
    p.add_argument("--work-dir", default=None, metavar="DIR",
                   help="where --ref materializes its worktree, in a unique "
                        "subdirectory it creates and cleans up (default: a "
                        "temp dir, deleted afterwards). It may not be, hold or "
                        "sit inside the project")
    group = p.add_mutually_exclusive_group()
    group.add_argument("--proposal", default=None, metavar="ID",
                       help="post the report to this proposal; it becomes that "
                            "proposal's checks gate")
    group.add_argument("--auto-proposal", action="store_true",
                       help="post to the one active proposal whose source is "
                            "the branch that was checked (none: a warning; "
                            "more than one: exit 2)")
    p.add_argument("--sha", default=None,
                   help="provenance: the host VCS commit this run measured")
    p.add_argument("--ref-label", default=None,
                   help="provenance: the host VCS ref name (e.g. $GITHUB_REF_NAME)")
    out = p.add_mutually_exclusive_group()
    out.add_argument("--quiet", action="store_true",
                     help="print nothing; the exit code is the answer")
    out.add_argument("--json", action="store_true",
                     help="print the report to stdout instead of the summary")

    # Lazy: `agentcad serve` pays nothing for a package it never touches, and
    # `agentcad/bench/` imports neither OCP nor build123d.
    from .bench.cli import add_bench_parser
    add_bench_parser(sub)

    p = sub.add_parser(
        "package", help="work with parts packages (PRD-011)",
        description="Package authoring commands. The publish gate is a "
                    "CORRECTNESS gate, not a security boundary.")
    package_sub = p.add_subparsers(dest="package_command",
                                   metavar="{validate}")
    v = package_sub.add_parser(
        "validate", help="run the publish gate over a package directory",
        description="Validate a package directory: its manifest and ceilings, "
                    "its part contracts, every declared configuration, every "
                    "parameter extreme, its specs, its connectors, its "
                    "previews and its docs — headless, with no server, in a "
                    "throwaway cell that never touches a project of yours. "
                    "Exit 0 green, 1 the package is wrong, 2 harness.")
    v.add_argument("path", help="the package directory (holding package.json)")
    v.add_argument("--projects-dir", default=None)
    v.add_argument("--strict", action="store_true",
                   help="count skipped rows as failures (rows keep their "
                        "status; only the verdict moves). A row marked "
                        "strict_exempt — a skip that is a fact about this "
                        "machine or about the world, never about the package "
                        "— is never counted")
    v.add_argument("--report", default=None, metavar="PATH",
                   help="write the JSON report here")
    v.add_argument("--work-dir", default=None, metavar="DIR",
                   help="where the gate materialises its throwaway cell, in a "
                        "unique subdirectory it creates and cleans up "
                        "(default: a temp dir). It may not be, hold or sit "
                        "inside the projects root or the package directory")
    v.add_argument("--budget", default=None, metavar="SECONDS",
                   type=_finite_arg("--budget",
                                    "a NaN deadline is never in the past, so "
                                    "it bounds nothing"),
                   help="deadline read before every stage and every kernel "
                        "call; a build already in flight cannot be preempted")

    p = sub.add_parser(
        "publish", help="publish a validated package to an index (PRD-011)",
        description="Run the publish gate over a package directory and, only "
                    "if it is green, copy it into a configured index and "
                    "record the entry. Fail-closed: every stage runs, a "
                    "version is IMMUTABLE (republishing is a conflict even "
                    "when the content id is identical), and a package whose "
                    "vendor is not redistributable may not enter a public "
                    "index. The publish gate is a CORRECTNESS gate, not a "
                    "security boundary. Exit 0 published, 1 refused, "
                    "2 harness.")
    p.add_argument("path", nargs="?", default=None,
                   help="the package directory (holding package.json); omit "
                        "it only with --yank")
    p.add_argument("--index", required=True, metavar="NAME",
                   help="the configured index to publish into")
    p.add_argument("--yank", default=None, metavar="NAME@VERSION",
                   help="withdraw a published version instead of publishing: "
                        "flips 'yanked' and DELETES NOTHING — a lockfile "
                        "naming it keeps resolving, a fresh requirement never "
                        "selects it, and naming it explicitly warns and "
                        "proceeds")
    p.add_argument("--projects-dir", default=None)
    p.add_argument("--work-dir", default=None, metavar="DIR",
                   help="where the gate materialises its throwaway cell "
                        "(default: a temp dir). It may not be, hold or sit "
                        "inside the projects root or the package directory")
    p.add_argument("--budget", default=None, metavar="SECONDS",
                   type=_finite_arg("--budget",
                                    "a NaN deadline is never in the past, so "
                                    "it bounds nothing"),
                   help="deadline read before every stage and every kernel "
                        "call; an exhausted budget is a harness exit, never a "
                        "publish")

    p = sub.add_parser(
        "admin", help="manage hosted-mode accounts and the audit log",
        description="Create, list and disable accounts on a hosted instance, "
                    "and query or back up the audit log (PRD-005 FR12). "
                    "Operates directly on the state files, so it "
                    "works over `docker compose exec` with no running server "
                    "and starts no kernel. " + _trust_sentence_capitalized() + ".")
    admin_sub = p.add_subparsers(dest="admin_command",
                                 metavar="{user,token,enrol,org,audit}")

    user_p = admin_sub.add_parser(
        "user", help="create, list and disable accounts",
        description=_trust_sentence_capitalized() + ".")
    user_sub = user_p.add_subparsers(dest="admin_action",
                                     metavar="{add,list,disable}")

    a = user_sub.add_parser(
        "add", help="create an account and print a single-use enrolment link",
        description="Creates a DISABLED account and prints a single-use, "
                    "7-day enrolment URL; the invitee sets their password "
                    "there and lands signed in. Nothing sends email, so this "
                    "works air-gapped.\n\n"
                    "WARNING: " + TRUST_SENTENCE + ".",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    a.add_argument("handle", help="[a-z0-9][a-z0-9._-]{0,31}")
    a.add_argument("--admin", action="store_true",
                   help="also manage users and tokens")

    user_sub.add_parser("list", help="list accounts (never a digest)")

    a = user_sub.add_parser("disable",
                            help="disable an account and revoke its sessions")
    a.add_argument("handle")

    token_p = admin_sub.add_parser(
        "token", help="mint, list and revoke agent bearer tokens",
        description="Bearer tokens are how an agent, a CI job or a remote MCP "
                    "client authenticates (AGENTCAD_TOKEN). A token is shown "
                    "once and stored only as a SHA-256 digest. "
                    + _trust_sentence_capitalized() + ".")
    token_sub = token_p.add_subparsers(dest="admin_action",
                                       metavar="{add,list,revoke}")

    a = token_sub.add_parser(
        "add", help="mint a bearer token and print it once",
        description="Prints `acad_<id>_<secret>` ONCE — only its digest is "
                    "stored, so a lost token is revoked and replaced, never "
                    "recovered.\n\n"
                    "WARNING: " + TRUST_SENTENCE + ".",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    a.add_argument("name", help="[a-z0-9][a-z0-9._-]{0,31}; composes as "
                                "agent:<name> in claims, presence and history")
    a.add_argument("--admin", action="store_true",
                   help="mint with the admin role (note: admin ROUTES still "
                        "require a signed-in person, never a token)")
    a.add_argument("--ttl-days", type=int, default=None, metavar="N",
                   help="expire the token after N days (default: never)")

    token_sub.add_parser("list", help="list tokens (never a secret or a digest)")

    a = token_sub.add_parser("revoke",
                             help="revoke a token by id (see `token list`)")
    a.add_argument("token_id", metavar="ID")

    a = admin_sub.add_parser(
        "enrol", help="re-mint an enrolment link for an existing account",
        description="The recovery path: a lost invitation or a forgotten "
                    "password. Any earlier outstanding link for the handle "
                    "stops working.")
    a.add_argument("handle")

    org_p = admin_sub.add_parser(
        "org", help="create orgs and workspaces, and add members (PRD-005 FR5)",
        description="Tenancy is orgs -> workspaces -> projects. These three "
                    "writes are the bootstrap of a hosted instance: an org, a "
                    "workspace inside it, and the people who belong to it. "
                    "They touch the state files only — no project directory "
                    "is created, no service and no kernel starts — so this "
                    "works over `docker compose exec` before anyone signs in. "
                    "Per-project roles are granted from the running instance "
                    "(the `grant_role` tool), not from here.")
    org_sub = org_p.add_subparsers(dest="admin_action",
                                   metavar="{add,workspace,member}")

    a = org_sub.add_parser(
        "add", help="create an org, optionally with its first admin",
        description="`--admin` is part of the SAME atomic write: an org "
                    "created empty and given a member afterwards is, in "
                    "between, an org nobody can administer — which is exactly "
                    "the state an interrupted bootstrap leaves behind.")
    a.add_argument("org", help="[a-z][a-z0-9_]{0,39}")
    a.add_argument("--label", default=None, metavar="TEXT",
                   help="display name (default: the org name)")
    a.add_argument("--admin", default=None, metavar="HANDLE",
                   help="an existing account's handle, made org admin")

    ws_p = org_sub.add_parser(
        "workspace", help="manage the workspaces inside an org")
    ws_sub = ws_p.add_subparsers(dest="admin_org_action", metavar="{add}")
    a = ws_sub.add_parser("add", help="create a workspace inside an org")
    a.add_argument("org")
    a.add_argument("workspace", help="[a-z][a-z0-9_]{0,39}")
    a.add_argument("--label", default=None, metavar="TEXT")

    member_p = org_sub.add_parser(
        "member", help="manage who belongs to an org")
    member_sub = member_p.add_subparsers(dest="admin_org_action",
                                         metavar="{add}")
    a = member_sub.add_parser(
        "add", help="add a person to an org with a default role",
        description="The role is the member's DEFAULT on every project in the "
                    "org; a per-project grant overrides it in either "
                    "direction, and an org admin outranks any override. A "
                    "token is never an org member — it reaches a project "
                    "through its own scope plus an explicit grant.")
    a.add_argument("org")
    a.add_argument("handle", help="an account handle, or 'user:<handle>'")
    a.add_argument("--role", default="view",
                   choices=["view", "comment", "edit", "admin"],
                   help="default role in the org (weakest first; "
                        "default: view)")

    audit_p = admin_sub.add_parser(
        "audit", help="query and back up the audit log (PRD-005 FR12)",
        description="The audit log is one SQLite database per org under "
                    "<state>/audit/. Auth events (sign-ins, enrolments, token "
                    "mints) are instance-wide and live in the '_instance' "
                    "log; a tenant's tool activity lives in its own org's. "
                    "Reads the files directly, so it works over `docker "
                    "compose exec` with the server running.")
    audit_sub = audit_p.add_subparsers(dest="admin_action",
                                       metavar="{query,backup}")

    a = audit_sub.add_parser(
        "query", help="print audit rows, newest first",
        description="Filters compose (AND). --since/--until take epoch "
                    "seconds, an ISO-8601 time, or a window like 7d/24h/30m. "
                    "--principal matches a person and every device they used: "
                    "'user:anya' also matches "
                    "'user:anya/browser:7f3a1b2c'.")
    a.add_argument("org", help="an org name, or '_instance' for auth events")
    a.add_argument("--principal", default=None,
                   help="user:<handle>, agent:<name>, or 'chat'")
    a.add_argument("--project", default=None)
    a.add_argument("--action", default=None,
                   help="e.g. login, create_agent_token, set_part_params")
    a.add_argument("--since", default=None, metavar="WHEN")
    a.add_argument("--until", default=None, metavar="WHEN")
    a.add_argument("--limit", type=int, default=50, metavar="N")
    a.add_argument("--json", action="store_true", help="machine-readable rows")

    a = audit_sub.add_parser(
        "backup", help="write a consistent copy of one org's audit log",
        description="Uses SQLite's VACUUM INTO, NOT a file copy: a WAL "
                    "database keeps recent commits in its -wal sidecar, so "
                    "`cp audit.db` can lose every row (measured in the PRD-005 "
                    "spike). Safe to run against a live server; refuses an "
                    "existing destination.")
    a.add_argument("org")
    a.add_argument("dest", help="destination file (must not exist)")

    p = sub.add_parser(
        "materials", help="work with material cards (PRD-028)",
        description="Material library commands. The lint is what the shipped "
                    "library and a contributed card are both held to.")
    materials_sub = p.add_subparsers(dest="materials_command",
                                     metavar="{lint}")
    lint_p = materials_sub.add_parser(
        "lint", help="lint material cards",
        description="Lint material card files, directories of them, or a "
                    "project.json (whose `materials` section is a user layer "
                    "whatever profile is asked for). Exit 0 clean, 1 errors, "
                    "2 usage.")
    lint_p.add_argument("paths", nargs="*", metavar="PATH",
                        help="card file, directory of card files, or a "
                             "project.json")
    lint_p.add_argument("--profile", default="library",
                        choices=["library", "user"],
                        help="library (default): every rule an error, every "
                             "property must cite its source. user: v1 flat "
                             "entries are fine and an uncited property is a "
                             "warning")
    lint_p.add_argument("--json", action="store_true",
                        help="print the findings as a JSON list")

    p = sub.add_parser(
        "skill", help="author agent skills (PRD-029)",
        description="Skills are loadable markdown guides an agent reads on "
                    "demand. `new` scaffolds one, `lint` is the gate the "
                    "shipped library and a contributed skill are both held "
                    "to.")
    skill_sub = p.add_subparsers(dest="skill_command", metavar="{new,lint}")
    new_p = skill_sub.add_parser(
        "new", help="scaffold a skill directory",
        description="Write <target>/<name>/SKILL.md plus a snippets/ "
                    "directory. Refuses to overwrite.")
    new_p.add_argument("name", help="skill name ([a-z][a-z0-9-]{1,47})")
    new_p.add_argument("--project", default=None,
                       help="write into <projects-dir>/<project>/skills/")
    new_p.add_argument("--dir", default=None,
                       help="write into this directory instead")
    new_p.add_argument("--projects-dir", default=None,
                       help="projects root (default: $AGENTCAD_PROJECTS_DIR, "
                            "else ~/AgentCAD/projects)")
    skill_lint_p = skill_sub.add_parser(
        "lint", help="lint skills",
        description="Lint a skill directory, a flat <name>.md, or a skills/ "
                    "directory of them. Exit 0 clean, 1 errors, 2 usage.")
    skill_lint_p.add_argument("paths", nargs="*", metavar="PATH")
    skill_lint_p.add_argument("--core", action="store_true",
                              help="also lint the shipped library "
                                   "(agentcad/skills/), at the library "
                                   "profile unless --profile says otherwise")
    skill_lint_p.add_argument("--profile", default=None,
                              choices=["library", "user"],
                              help="library: license/author required and an "
                                   "over-long body is an error. user "
                                   "(default): those are warnings")
    skill_lint_p.add_argument("--json", action="store_true",
                              help="print the findings as a JSON list")

    # ------------------------------------------------ PRD-005 sync (slice 6)

    p = sub.add_parser(
        "login", help="store a token for a hosted instance (PRD-005)",
        description="Verify a bearer token against an instance and store it "
                    "in ~/.agentcad/sync.json, mode 0600. Every git call this "
                    "CLI makes then authenticates through a credential helper "
                    "— the token is never in a URL, never in a config file "
                    "inside a clone, and never on a command line.")
    p.add_argument("url", help="the instance, e.g. https://cad.example.com")
    p.add_argument("--token", default=None, metavar="TOKEN",
                   help="the bearer (default: stdin if it is a pipe, else "
                        "$AGENTCAD_TOKEN). A token passed here is visible in "
                        "`ps`; piping it is not")

    p = sub.add_parser(
        "clone", help="clone a hosted project (PRD-005)",
        description="Clone <instance>/git/<org>/<workspace>/<project>.git into "
                    "a real AgentCAD project directory — the history repo "
                    "lands at <dest>/.history, exactly where the app expects "
                    "it, and never as a .git/ inside the project.")
    p.add_argument("url", help="the sync url ending in .git")
    p.add_argument("dest", nargs="?", default=None,
                   help="where to put it (default: <projects-dir>/<project>)")
    p.add_argument("--projects-dir", default=None)
    p.add_argument("--token", default=None, metavar="TOKEN",
                   help="sign in to the instance first (see `agentcad login`, "
                        "which is the way that keeps it off `ps`)")

    p = sub.add_parser(
        "push", help="push branches and tags to the instance (PRD-005)",
        description="Push every local branch and tag. Never forces, never "
                    "deletes: a divergence is refused by the server with the "
                    "message this command prints verbatim, and the fix is "
                    "`agentcad pull`.")
    p.add_argument("dir", nargs="?", default=None,
                   help="the project directory (default: the current one)")

    p = sub.add_parser(
        "pull", help="fetch and merge the instance's work (PRD-005)",
        description="Fetch, fast-forward what can fast-forward, and merge what "
                    "cannot through the kernel-validated merge the UI uses. "
                    "Conflicts are listed and the branch is left exactly as it "
                    "was; nothing here resets or overwrites. Exit 0 clean, "
                    "1 conflicts or an unmerged divergence, 2 harness.")
    p.add_argument("dir", nargs="?", default=None,
                   help="the project directory (default: the current one)")
    p.add_argument("--no-merge", action="store_true",
                   help="fast-forward only: report a divergence instead of "
                        "merging it (starts no kernel)")
    p.add_argument("--allow-invalid", action="store_true",
                   help="land a merge whose validation pass failed, with the "
                        "failures recorded in the merge commit")

    p = sub.add_parser(
        "status", help="ahead/behind against the instance (PRD-005)",
        description="Per-branch ahead/behind counts against origin, plus tags "
                    "and uncommitted files. Offline unless --fetch. Exit 1 "
                    "when a branch has diverged.")
    p.add_argument("dir", nargs="?", default=None,
                   help="the project directory (default: the current one)")
    p.add_argument("--fetch", action="store_true",
                   help="ask the server first (the default is a purely local "
                        "comparison against what was last fetched)")
    p.add_argument("--json", action="store_true")

    # Hidden, like `worker`: git runs this, a human never does.
    p = sub.add_parser("credential")
    p.add_argument("action", nargs="?", default="get")

    args = parser.parse_args()
    if args.command == "admin":
        cmd_admin(args)
    elif args.command in ("serve", "open"):
        cmd_serve(args, open_browser=args.command == "open")
    elif args.command == "mcp":
        cmd_mcp(args)
    elif args.command == "worker":
        cmd_worker(args)
    elif args.command == "new":
        cmd_new(args)
    elif args.command == "export":
        cmd_export(args)
    elif args.command == "check":
        raise SystemExit(cmd_check(args))
    elif args.command == "bench":
        from .bench.cli import cmd_bench
        raise SystemExit(cmd_bench(args))
    elif args.command == "package":
        raise SystemExit(cmd_package(args))
    elif args.command == "publish":
        raise SystemExit(cmd_publish(args))
    elif args.command == "materials":
        raise SystemExit(cmd_materials(args))
    elif args.command == "skill":
        raise SystemExit(cmd_skill(args))
    elif args.command == "login":               # PRD-005 slice 6 ↓
        raise SystemExit(cmd_login(args))
    elif args.command == "clone":
        raise SystemExit(cmd_clone(args))
    elif args.command == "push":
        raise SystemExit(cmd_push(args))
    elif args.command == "pull":
        raise SystemExit(cmd_pull(args))
    elif args.command == "status":
        raise SystemExit(cmd_status(args))
    elif args.command == "credential":
        raise SystemExit(cmd_credential(args))  # PRD-005 slice 6 ↑
    else:
        parser.print_help()
