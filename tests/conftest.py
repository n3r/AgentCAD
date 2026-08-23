import shutil
from pathlib import Path

import pytest

from agentcad.core import locks
from agentcad.core.service import AgentCADService, EventBus
from agentcad.kernel.client import KernelClient

PLATE_SCRIPT = '''\
from build123d import *

PARAMS = {
    "width":  {"default": 80.0, "min": 10.0, "max": 300.0, "unit": "mm",
               "description": "Plate width"},
    "hole_d": {"default": 12.0, "min": 1.0,  "max": 50.0,  "unit": "mm",
               "description": "Center hole diameter"},
}

def build(p):
    with BuildPart() as part:
        Box(p.width, 60, 8)
        with Locations((0, 0, 4)):
            Cylinder(radius=15, height=20, align=(Align.CENTER, Align.CENTER, Align.MIN))
        Hole(radius=p.hole_d / 2)
        with Locations((30, 20, 0), (-30, 20, 0), (30, -20, 0), (-30, -20, 0)):
            Hole(radius=3)
        fillet(part.edges().filter_by(Axis.Z), radius=2)
    return part.part
'''

BOX_SCRIPT = '''\
from build123d import *

PARAMS = {"size": {"default": 10.0, "min": 1.0, "max": 100.0, "unit": "mm"}}

def build(p):
    with BuildPart() as part:
        Box(p.size, p.size, p.size)
    return part.part
'''

# Numeric enum whose choices are ints and whose build needs a real int
# (range(p.n)): a caller-supplied 3.0 must canonicalize to the declared 3.
NUMERIC_ENUM_SCRIPT = '''\
import build123d as b3d

PARAMS = {
    "n": {"default": 2, "type": "enum", "choices": [2, 3, 4], "description": "hole count"},
}

def build(p):
    part = b3d.Box(24, 12, 6)
    for i in range(p.n):
        hole = b3d.Cylinder(1, 20).moved(b3d.Location((i * 5 - 5, 0, 0)))
        part = part - hole
    return part
'''

# One parameter of every supported type (number/bool/enum/string/int).
TYPED_SCRIPT = '''\
import build123d as b3d

PARAMS = {
    "size": {"default": 20.0, "min": 10.0, "max": 40.0, "unit": "mm", "description": "cube edge"},
    "holes": {"default": True, "type": "bool", "description": "drill the hole"},
    "grade": {"default": "std", "type": "enum", "choices": ["std", "wide"], "description": "width grade"},
    "label": {"default": "acme", "type": "string", "max_len": 10, "description": "engraving text"},
    "n": {"default": 2, "type": "int", "min": 1, "max": 4, "description": "hole count"},
}

def build(p):
    w = p.size * (2.0 if p.grade == "wide" else 1.0)
    part = b3d.Box(w, p.size, p.size)
    if p.holes:
        for i in range(p.n):
            hole = b3d.Cylinder(2, p.size * 2).moved(b3d.Location((i * 4 - 2, 0, 0)))
            part = part - hole
    assert isinstance(p.label, str)
    return part
'''


# A flange-like part: plate with a central bore and a bolt circle. Every
# parameter carries a unit and a description, so it is also the fixture for
# anything that reads a normalized PARAMS spec (PRD-012 configurations).
FLANGE_SCRIPT = '''\
from build123d import *

PARAMS = {
    "outer_d":  {"default": 140.0, "min": 40.0, "max": 400.0, "unit": "mm", "description": "OD"},
    "bore_d":   {"default": 80.0,  "min": 10.0, "max": 300.0, "unit": "mm", "description": "bore"},
    "thick":    {"default": 14.0,  "min": 4.0,  "max": 60.0,  "unit": "mm", "description": "thickness"},
    "n_bolts":  {"default": 8.0,   "min": 3.0,  "max": 16.0,  "unit": "ct", "description": "bolt count"},
    "bolt_d":   {"default": 9.0,   "min": 3.0,  "max": 30.0,  "unit": "mm", "description": "bolt hole dia"},
    "bc_d":     {"default": 118.0, "min": 20.0, "max": 360.0, "unit": "mm", "description": "bolt circle dia"},
}

def build(p):
    with BuildPart() as part:
        Cylinder(radius=p.outer_d / 2, height=p.thick)
        Cylinder(radius=p.bore_d / 2, height=p.thick, mode=Mode.SUBTRACT)
        with PolarLocations(radius=p.bc_d / 2, count=int(p.n_bolts)):
            Hole(radius=p.bolt_d / 2)
    return part.part
'''

# A three-member size family for FLANGE_SCRIPT, in family (insertion) order —
# the configuration map shape PRD-011 froze: {name: {params, label?}}.
THREE_SIZE_CONFIGS = {
    "s": {"params": {"outer_d": 100.0, "bore_d": 50.0, "bc_d": 80.0},
          "label": "Small"},
    "m": {"params": {"outer_d": 140.0, "bore_d": 80.0, "bc_d": 118.0},
          "label": "Medium"},
    "l": {"params": {"outer_d": 200.0, "bore_d": 120.0, "bc_d": 170.0},
          "label": "Large"},
}


def make_test_service(projects_dir, kernel, bus=None):
    """Build a service without synchronous git snapshots for unrelated tests."""
    bus = bus if bus is not None else EventBus()
    service = AgentCADService(projects_dir, kernel, bus)
    bus.on_publish = None
    return service


def clone_test_service(source_projects, dest_projects, kernel, bus=None):
    """Copy a prepared project tree so mutating tests retain isolation."""
    shutil.copytree(source_projects, dest_projects)
    return make_test_service(dest_projects, kernel, bus)


@pytest.fixture(autouse=True)
def _restore_client_identity():
    """Undo any `client_id_var` a test leaves set, for **every** test.

    `locks.client_id_var` is a ContextVar with a default of `"local"`, and a
    ContextVar set at a test's top level is never restored — it survives for
    the rest of that xdist worker's process. Two kinds of test set one and do
    not put it back: a few set it directly (`tests/test_checks_gate.py`), and
    every in-process CLI run does it inside `cli.py` itself (`cmd_check` and
    the two package commands call `locks.set_client_id("ci")`, which is
    correct for a real `agentcad check` and has nowhere to be undone).
    Measured with a `pytest_sessionfinish` probe: `tests/test_checks_cli.py`
    alone ends the session with `client_id_var == "ci"`, and so do
    `tests/test_packages_cli.py` and `tests/test_prd004_acceptance.py`.

    That leaks into whatever module `--dist loadscope` happens to schedule
    next on the same worker, and "happens to" is the problem: adding one
    unrelated test module moved a leaker onto the worker running
    `tests/test_usage.py`, whose three identity tests assert the default and
    got `"ci"` instead. They had been order-dependent since they were written.

    This is a *snapshot and restore*, not a pin: a test that sets an identity
    on purpose still sees it for its own duration (`tests/test_branches.py`
    switches between `agent_a`/`agent_b` many times inside one test), and only
    the escape is closed. It is here rather than in `cli.py` because the CLI's
    call is right — a real `agentcad check` process *is* `ci` from that point
    on — so the fix belongs to the harness that reuses one process for
    thousands of tests, not to the product.
    """
    token = locks.client_id_var.set(locks.client_id_var.get())
    yield
    locks.client_id_var.reset(token)


@pytest.fixture(autouse=True)
def _thumbnail_warmer_off(monkeypatch):
    """Default PRD-027's background thumbnail warmer OFF for the whole suite.

    `tools_navigation.register` starts a daemon thread per service, and
    `build_registry` runs in dozens of test modules — so without this a session
    accumulates one never-stopped thread per registry and renders a 192² PNG on
    every build it happens to observe. Nothing an assertion can see changes:
    the thumbnail routes render on demand from meshes that already exist, so
    the warmer only ever decides whether the first read pays for the render.

    A test that *wants* the default path (there are two, in
    `test_thumbnails.py`) deletes the variable itself — this is a default, not
    a pin, and it is the same knob the geometry-CI and bench services use.
    """
    monkeypatch.setenv("AGENTCAD_THUMBNAILS", "off")


@pytest.fixture(scope="session")
def kernel():
    client = KernelClient()
    client.start()
    yield client
    client.stop()


# --------------------------------------------------------------- PRD-005a
# Hosted-mode fixtures. Slices 2, 3, 4, 5 and 7 all drive the same app, so
# they live here beside `make_test_service` rather than in one test file.

#: The public origin every hosted test is configured for. `TestClient` sends
#: `Host: testserver` for this base_url, which is what the guard compares
#: against `AppMode.origin_host`.
HOSTED_ORIGIN = "http://testserver"

ADMIN_HANDLE = "nikita"
ADMIN_PASSWORD = "correct horse battery"


class CountingKernel:
    """A transparent proxy that counts `request` calls.

    AC7 is "no anonymous request reaches `exec()` in the worker", and the only
    honest way to assert that is to count at the one door every kernel call
    goes through. Everything else forwards, so `service.kernel.alive` and
    `.sandboxed` still answer for the health body.
    """

    def __init__(self, inner):
        self._inner = inner
        self.calls = 0
        self.seen: list[str] = []

    def request(self, op, *args, **kwargs):
        self.calls += 1
        self.seen.append(op)
        return self._inner.request(op, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._inner, name)


@pytest.fixture
def hosted(kernel, tmp_path, monkeypatch):
    """`(client, store)` for a hosted app with one enrolled admin, `nikita`."""
    from fastapi.testclient import TestClient

    from agentcad.core.appmode import AppMode
    from agentcad.core.authstore import AuthStore
    from agentcad.core.tools import build_registry
    from agentcad.server import security as security_module
    from agentcad.server.app import create_app
    from agentcad.server.security import SecurityConfig

    # Slice 7 gave the hosted app a route that reads `service.packages`, which
    # resolves its index configuration through `config.load_config()` — the
    # REAL `~/.agentcad/config.json` unless this is set. Without it a hosted
    # test would load the developer's own indexes (git ones included, which
    # shell out to git), so the isolation is here rather than in the one test
    # file that noticed.
    monkeypatch.setenv("AGENTCAD_CONFIG", str(tmp_path / "cfg" / "config.json"))

    service = make_test_service(tmp_path / "projects", kernel)
    counter = CountingKernel(service.kernel)
    service.kernel = counter

    store = AuthStore(tmp_path / "auth")
    store.enrol(store.add_user(ADMIN_HANDLE, role="admin"), ADMIN_PASSWORD)
    cfg = SecurityConfig(mode=AppMode("hosted", HOSTED_ORIGIN, b"k" * 32),
                         store=store)
    # Installed BEFORE the registry is built, exactly as `cli.cmd_serve` does
    # it: a tool pack decides at registration time whether its tool can run
    # (the FEM precedent), and `whoami` can only run in hosted mode. Building
    # the registry first would leave a real hosted server without the tool
    # while every route test still passed.
    security_module.install(cfg)
    app = create_app(service, build_registry(service),
                     extra_allowed_hosts={"testserver"}, security=cfg)
    client = TestClient(app, base_url=HOSTED_ORIGIN)
    client.agentcad_store = store
    client.agentcad_service = service
    client.agentcad_kernel = counter
    client.agentcad_config = cfg
    try:
        yield client, store
    finally:
        # The module-level slot `create_app` sets is process-global by design
        # (tool registration and the CLI are not inside a request). Clear it,
        # or the next test in this worker builds a LOCAL app that still
        # believes it is hosted.
        security_module.install(None)


@pytest.fixture
def hosted_client(hosted):
    return hosted[0]


@pytest.fixture
def hosted_app(hosted_client):
    return hosted_client.app


@pytest.fixture
def kernel_counter(hosted_client):
    return hosted_client.agentcad_kernel


@pytest.fixture
def hosted_with_catalog(hosted_client):
    """The hosted app with the bundled `catalog/` configured (scope: public).

    `cli._register_catalog`'s declaration, made without starting a CLI — the
    `tests/test_catalog.py::bundled` precedent. `reload_indexes()` is explicit
    because `PackageManager.indexes` caches on first read.
    """
    from agentcad.cli import bundled_index_entries

    service = hosted_client.agentcad_service
    service.bundled_indexes = bundled_index_entries()
    service.packages.reload_indexes()
    assert [ix.name for ix in service.packages.indexes] == ["agentcad-core"]
    return hosted_client


PRIVATE_PACKAGE = "acme-internal"


def configure_private_index(client, root, package_name=PRIVATE_PACKAGE,
                            document_scope="private"):
    """Configure a `scope: "private"` local index carrying *package_name*.

    *document_scope* is what the index **document** declares, separately from
    the `scope: "private"` written into the operator's config. They agree by
    default, which is why the original leak tests passed over review finding
    M2; pass `document_scope="public"` for the disagreement — a third party's
    `index.json` claiming to be public over an operator who said private.

    Derived from the bundled catalog's own `din625` entry — copied directory
    and all — so it is a genuinely valid index rather than a hand-written
    document that might be rejected for an unrelated reason and let a leak
    test pass by accident. Configured FIRST, so it also wins precedence: a
    public read that walked `indexes` in order without filtering on scope
    would serve it.
    """
    import json
    import os
    import shutil

    from agentcad import config as user_config
    from agentcad._resources import resource_root

    # This writes a CONFIG FILE, and `config.config_path()` falls back to the
    # developer's real `~/.agentcad/config.json`. Refuse rather than trust the
    # caller: a helper that silently reconfigured somebody's own indexes would
    # be found long after the test that did it.
    override = os.environ.get("AGENTCAD_CONFIG")
    assert override, "set AGENTCAD_CONFIG before configuring an index"
    real = Path.home() / ".agentcad"
    assert real not in Path(override).resolve().parents, override

    catalog = resource_root() / "catalog"
    doc = json.loads((catalog / "index.json").read_text(encoding="utf-8"))
    entry = dict(doc["packages"]["din625"]["versions"]["1.0.0"])
    entry["path"] = f"{package_name}/1.0.0"
    entry["summary"] = "internal only, never anonymously readable"

    root = Path(root)
    shutil.copytree(catalog / "din625" / "1.0.0", root / package_name / "1.0.0")
    doc["name"] = "acme"
    doc["scope"] = document_scope
    doc["packages"] = {package_name: {"versions": {"1.0.0": entry}}}
    (root / "index.json").write_text(json.dumps(doc, indent=2), encoding="utf-8")

    user_config.save_config({"indexes": [
        {"name": "acme", "kind": "local", "path": str(root),
         "scope": "private"}]})
    service = client.agentcad_service
    service.packages.reload_indexes()
    assert [ix.name for ix in service.packages.indexes] == ["acme",
                                                            "agentcad-core"]
    return package_name


@pytest.fixture
def hosted_with_private(hosted_with_catalog, tmp_path):
    """`(client, private_name)`: the public catalog **plus** a private index."""
    name = configure_private_index(hosted_with_catalog,
                                   tmp_path / "private-index")
    return hosted_with_catalog, name


def login(client, handle=ADMIN_HANDLE, password=ADMIN_PASSWORD):
    """Sign `handle` in and leave the session cookie on `client`.

    Slice 2 has no `/api/auth/login`, so this mints the session through the
    store — which is also the tighter assertion: the *guard*, not the route,
    is what authenticates. Slice 3's route tests drive the real endpoint.
    """
    store = client.agentcad_store
    client.cookies.set("agentcad_session", store.create_session(handle, None))
    return client


def flatten_routes(app) -> set[tuple[str, str]]:
    """Every `(method, full_path)` the app actually serves.

    **Not** `[(m, r.path) for r in app.routes]`, and the difference is the
    whole point. FastAPI 0.141 does not flatten `include_router`: each route
    pack lands as one opaque `_IncludedRouter` whose `path` is `None`, so a
    naive walk of `app.routes` sees only the 23 routes declared in `app.py`
    itself and **none** of the ~60 in the sixteen packs — which is exactly the
    population the anonymous-surface enumeration exists to police. A test
    written that way passes while a pack quietly goes public.

    Websocket routes come back as method `"WS"`; `Mount`s (the static dirs)
    have no methods and are asserted directly instead.
    """
    def walk(routes, prefix=""):
        found: set[tuple[str, str]] = set()
        for route in routes:
            context = getattr(route, "include_context", None)
            if context is not None:                 # FastAPI's _IncludedRouter
                found |= walk(context.included_router.routes,
                              prefix + (getattr(context, "prefix", "") or ""))
                continue
            path = getattr(route, "path", None)
            if path is None:
                continue
            methods = getattr(route, "methods", None)
            if methods is None:
                nested = getattr(route, "routes", None)
                if nested:
                    found |= walk(nested, prefix + path)
                else:
                    found.add(("WS", prefix + path))
                continue
            for method in methods:
                found.add((method, prefix + path))
        return found

    return walk(app.routes)
