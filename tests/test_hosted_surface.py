"""PRD-005a: the anonymous surface, enumerated and proved kernel-free.

The three tests the design leans on (Decision 12):

1. **The enumeration.** Walk the fully-mounted hosted app and assert the set
   of anonymous-reachable routes equals a literal list. It fails when a route
   pack goes public by accident — PRD-007 AC9's test, delivered early because
   it is what makes PRD-007 safe to write.
2. **Kernel silence.** Exercise every public route with the kernel
   instrumented and assert zero requests (FR16 / AC7).
3. **Local mode unchanged.** In `test_security_guard.py`, where the guard
   lives.

`EXPECTED_PUBLIC` is written **once, in full**, with a `NOT_YET_BUILT`
subtrahend that later slices shrink. Set *equality* rather than a subset check
is what stops a forgotten removal from passing silently, and writing the final
list once is what stops the enumeration drifting slice by slice.
"""

from __future__ import annotations

import re

import pytest

from agentcad.server import security

from .conftest import flatten_routes

#: `{proj}`, `{part_id}`, `{name}`, ... — filled so a templated route can be
#: swept too.
_TEMPLATE = re.compile(r"\{[^}]*\}")
#: `{name:path}` -> `{name}`
_CONVERTER = re.compile(r":[a-z]+\}")

EXPECTED_PUBLIC = {
    ("GET", "/"),
    ("GET", "/api/health"),
    ("POST", "/api/auth/login"),
    ("GET", "/api/auth/enrol/{token}"),
    ("POST", "/api/auth/enrol/{token}"),
    ("GET", "/api/public/packages"),
    ("GET", "/api/public/packages/{name}"),
    ("GET", "/api/public/packages/{name}/versions/{version}"),
    ("GET", "/api/public/packages/{name}/versions/{version}/preview"),
    # PRD-031a slice 1 — the kernel-free market data routes. Added to
    # `routes_public.py` (whose zero-kernel invariant they keep literally true)
    # in the SAME change that mounts them. `search` is declared BEFORE `{name}`
    # so Starlette does not bind `{name} == "search"`; all three read only the
    # pre-generated `index.json` digest, `scope: public` filtered, refresh-free.
    ("GET", "/api/public/packages/search"),
    ("GET", "/api/public/packages/{name}/versions/{version}/script/{part}"),
    ("GET", "/api/public/packages/{name}/versions/{version}/params/{part}"),
    # PRD-031a slice 2 — the two customizer routes that DO reach exec(), in the
    # new `routes_market.py` pack. Added here in the SAME change that mounts
    # them (never staged through NOT_YET_BUILT, which stays `== set()`). Both
    # flow through PRD-007's containment reused verbatim via
    # `ShareBuilder.build_catalog_variant` and the shared
    # `service.customizer_guard`; the fixed export set `{step,stl,3mf}` gates
    # download before the builder.
    ("GET", "/api/public/packages/{name}/versions/{version}/parts/{part}/variant"),
    ("GET",
     "/api/public/packages/{name}/versions/{version}/parts/{part}/download/{fmt}"),
    # PRD-007 share links (design Decision 2). These SIX viewer routes make
    # ZERO kernel calls. The two customizer routes that DO reach exec()
    # (/variant, /download) join this set in PRD-007 slice 4, mounted in the
    # same change — NOT staged through NOT_YET_BUILT, because
    # `test_prd005a_acceptance.py::test_ac2...` asserts `NOT_YET_BUILT == set()`
    # (the 005a surface is "finished"), so a non-empty subtrahend would turn
    # that acceptance test red for a whole slice. Growing EXPECTED_PUBLIC and
    # mounting the route together keeps every tree green and stays reviewable.
    ("GET", "/s/{token}"),
    ("GET", "/embed/{token}"),
    ("GET", "/s/{token}/model"),
    ("GET", "/s/{token}/mesh/{key}"),
    ("GET", "/s/{token}/params"),
    ("GET", "/s/{token}/script"),
    # The two customizer routes that DO reach exec() — added in PRD-007 slice 4
    # in the SAME change that mounts them (see the comment above). Both are
    # gated (customizer flag / export mask), capped (per-link + per-IP buckets,
    # a global in-flight semaphore) and param-validated to the authoring path's
    # parity; the set-equality below is what stops a NINTH /s/ route going
    # public unreviewed.
    ("GET", "/s/{token}/variant"),
    ("GET", "/s/{token}/download/{fmt}"),
}

# Routes named in EXPECTED_PUBLIC that a slice has not created yet. Kept EMPTY:
# `test_prd005a_acceptance.py` hard-asserts it, so PRD-007 does not stage the
# customizer templates here — slice 4 adds them to EXPECTED_PUBLIC and mounts
# them in one change.
NOT_YET_BUILT: set[tuple[str, str]] = set()

BUILT_PUBLIC = EXPECTED_PUBLIC - NOT_YET_BUILT


def _routes(app) -> set[tuple[str, str]]:
    return {(method, path) for method, path in flatten_routes(app)
            if method not in {"HEAD", "OPTIONS", "WS"}}


def _reachable(app) -> set[tuple[str, str]]:
    return {(method, path) for method, path in _routes(app)
            if security.is_public(path)}


def test_the_route_walk_actually_sees_the_route_packs(hosted_app):
    """The enumeration is only worth anything if the walk is complete, and a
    naive `app.routes` walk is **not**: FastAPI 0.141 leaves each
    `include_router` as one opaque `_IncludedRouter` with `path = None`, so
    the obvious loop sees the 23 routes declared in `app.py` and none of the
    ~60 in the packs — silently passing while a pack goes public.

    Cross-checked against `app.openapi()`, which FastAPI builds by its own
    independent traversal: if its internals move, this fails loudly instead of
    quietly under-reporting.
    """
    walked = _routes(hosted_app)
    assert len(walked) > 60, f"the walk found only {len(walked)} routes"

    documented = {
        (method.upper(), path)
        for path, operations in hosted_app.openapi()["paths"].items()
        for method in operations
        if method.upper() not in {"HEAD", "OPTIONS"}
    }
    # Starlette keeps the converter in the template (`{name:path}`, which
    # `routes_versioning` needs because a branch name may contain "/"); the
    # OpenAPI schema normalises it away.
    normalise = {(m, _CONVERTER.sub("}", p)) for m, p in walked}
    assert documented <= normalise, documented - normalise
    # Some packs will always be present; name a few so an empty-ish walk that
    # still cleared the count above cannot pass.
    for expected in (("GET", "/api/materials"),
                     ("POST", "/api/projects/{proj}/render"),
                     ("GET", "/api/packages/search")):
        assert expected in walked, expected


def test_the_public_surface_is_exactly_this(hosted_app):
    """Fails when a new route pack goes public by accident (PRD-007 AC9, early)."""
    assert _reachable(hosted_app) == BUILT_PUBLIC


def test_every_other_route_answers_401_anonymously(hosted_client, hosted_app):
    """AC2's other half: the enumeration says what `is_public` *believes*;
    this says what the running app actually does. A route that `is_public`
    calls private but that answers anyway is the failure the first test alone
    would miss."""
    checked = 0
    for method, path in sorted(_routes(hosted_app) - _reachable(hosted_app)):
        # Templated paths are filled rather than skipped: the guard runs in
        # the middleware, BEFORE routing, so the refusal does not depend on
        # the project or part existing.
        response = hosted_client.request(method, _TEMPLATE.sub("demo", path))
        assert response.status_code == 401, f"{method} {path}"
        assert response.json()["error"]["type"] == "AuthError"
        checked += 1
    # The real inventory is ~70 mounted routes; a sweep that suddenly found a
    # handful would mean the walk broke, not that the surface shrank.
    assert checked >= 60, f"the sweep only reached {checked} routes"


def test_even_a_route_that_does_not_exist_is_401_rather_than_404(hosted_client):
    """The guard is middleware, so it answers before routing. A 404 here
    would be a free map of which paths exist."""
    assert hosted_client.get("/api/no/such/route").status_code == 401
    assert hosted_client.post("/api/projects/../../etc/passwd").status_code == 401


def test_the_openapi_schema_is_not_anonymously_readable(hosted_client):
    """FastAPI mounts /openapi.json, /docs and /redoc by default; the route
    inventory of a private instance is reconnaissance, and default-deny is
    what covers them with no action by anybody."""
    for path in ("/openapi.json", "/docs", "/redoc"):
        assert hosted_client.get(path).status_code == 401, path


def test_static_mounts_are_public(hosted_client):
    """/js, /css and /vendor are Mounts, not Routes, so they cannot appear in
    EXPECTED_PUBLIC — assert them directly or they go untested."""
    assert hosted_client.get("/js/api.js").status_code == 200
    assert hosted_client.get("/").status_code == 200


@pytest.mark.parametrize("path", [
    "/api/publicity",            # a prefix is not a substring
    "/api/public",               # ...nor is the bare stem
    "/api/auth/enrolment",
    "/api/auth/session",
    "/api/auth/logout",
    "/api/auth/users",
    "/api/auth/tokens",
    "/jsx/evil.js",
    "/api/projects",
    "/api/packages/search",      # walks EVERY index, including private ones
    # PRD-007's trailing-slash gotcha: `/s/` and `/embed/` are public, but the
    # bare stems must not make `/status`, `/svg` or `/embedding` public.
    "/s",
    "/status",
    "/svg",
    "/embed",
    "/embedding",
])
def test_paths_that_must_not_be_public(path):
    """`routes_packages.py`'s search and preview iterate every configured
    index, including `scope: "private"` ones, so exposing them would leak a
    private index. The public catalog read is a separate, scope-filtered pack
    — the single most important detail of design Decision 8."""
    assert security.is_public(path) is False


def test_there_is_no_per_route_public_decorator():
    """Default deny means the allowlist is a literal in ONE file. A pack
    author must not be able to open the anonymous surface from their own
    module, so there is deliberately no opt-in seam to find."""
    assert not any(name.lower().startswith(("public_route", "mark_public",
                                            "allow_anonymous"))
                   for name in dir(security))
    assert isinstance(security.PUBLIC_PATHS, frozenset)


# ----------------------------------------------------------- kernel silence

def _fill(path: str) -> str:
    return (path
            .replace("{name}", "din625")
            .replace("{version}", "1.0.0")
            .replace("{token}", "not-a-real-enrolment-token"))


def test_public_surface_makes_no_kernel_calls(hosted_with_catalog,
                                              kernel_counter):
    """AC7: nothing anonymous may reach exec() in the worker.

    Driven against a client with the bundled catalog configured, so the four
    `/api/public/packages…` routes actually *serve* rather than 404 — a
    kernel-silence proof over a surface that answered "not found" everywhere
    would be silence about nothing. The 200s are asserted below.
    """
    client = hosted_with_catalog
    before = kernel_counter.calls
    answered = {}
    for method, path in sorted(BUILT_PUBLIC):
        answered[(method, path)] = client.request(method, _fill(path)).status_code
    for asset in ("/js/api.js", "/css/app.css", "/"):
        answered[("GET", asset)] = client.get(asset).status_code
    assert kernel_counter.calls == before, kernel_counter.seen

    # The routes that must have really run. `POST /api/auth/login` (no body)
    # and the enrolment reads (a token that does not exist) are exercised for
    # kernel silence but cannot be 200s.
    served = {path for (method, path), status in answered.items()
              if status == 200}
    assert {"/", "/api/health", "/js/api.js", "/css/app.css",
            "/api/public/packages",
            "/api/public/packages/{name}",
            "/api/public/packages/{name}/versions/{version}",
            } <= served, answered
    # The preview needs a `?path=`, which `_fill` cannot supply.
    assert client.get(
        "/api/public/packages/din625/versions/1.0.0/preview"
        "?path=previews/ball_bearing_iso.png").status_code == 200
    assert kernel_counter.calls == before, kernel_counter.seen


def test_the_kernel_counter_actually_counts(hosted_client, kernel_counter):
    """The positive control, without which `calls == 0` would pass just as
    happily with a broken counter — which is exactly the shape of green the
    PRD-011 review kept finding."""
    from .conftest import BOX_SCRIPT, login

    login(hosted_client)
    hosted_client.post("/api/projects", json={"name": "demo"})
    before = kernel_counter.calls
    r = hosted_client.post("/api/projects/demo/parts",
                           json={"id": "box", "script": BOX_SCRIPT})
    assert r.status_code == 201, r.text
    assert kernel_counter.calls > before


def test_an_anonymous_request_to_a_kernel_route_is_refused_before_the_handler(
        hosted_client, kernel_counter):
    """The routes that would reach `exec()` are refused by the guard, so the
    kernel never sees the request at all — not "refused inside the handler"."""
    from .conftest import BOX_SCRIPT

    before = kernel_counter.calls
    for method, path, body in (
        ("POST", "/api/projects/demo/parts", {"id": "x", "script": BOX_SCRIPT}),
        ("PUT", "/api/projects/demo/parts/box", {"script": BOX_SCRIPT}),
        ("POST", "/api/tools/inspect_script", {"script": BOX_SCRIPT}),
        ("GET", "/api/projects/demo/parts/box/mesh", None),
        ("GET", "/api/projects/demo/assembly", None),
    ):
        response = hosted_client.request(method, path, json=body)
        assert response.status_code == 401, f"{method} {path}"
    assert kernel_counter.calls == before, kernel_counter.seen
