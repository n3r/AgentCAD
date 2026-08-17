import shutil

import pytest

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
def hosted(kernel, tmp_path):
    """`(client, store)` for a hosted app with one enrolled admin, `nikita`."""
    from fastapi.testclient import TestClient

    from agentcad.core.appmode import AppMode
    from agentcad.core.authstore import AuthStore
    from agentcad.core.tools import build_registry
    from agentcad.server import security as security_module
    from agentcad.server.app import create_app
    from agentcad.server.security import SecurityConfig

    service = make_test_service(tmp_path / "projects", kernel)
    counter = CountingKernel(service.kernel)
    service.kernel = counter

    store = AuthStore(tmp_path / "auth")
    store.enrol(store.add_user(ADMIN_HANDLE, role="admin"), ADMIN_PASSWORD)
    cfg = SecurityConfig(mode=AppMode("hosted", HOSTED_ORIGIN, b"k" * 32),
                         store=store)
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
