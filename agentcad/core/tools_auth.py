"""Tool pack: ``whoami`` — the agent-facing half of hosted identity (FR11).

Registered **only when this process is serving a hosted app**. That is the
FEM-tools precedent (``tools_analysis.register`` registers nothing when
``fem_available()`` is false): an agent must never be offered a tool that
cannot run, and in local mode there is no principal to report — a ``whoami``
answering "local" would be a tool whose only content is that the feature is
off.

The one wrinkle is *when* that decision is made. ``build_registry`` runs in the
caller **before** ``create_app``, so the app's ``SecurityConfig`` has to be
installed before the registry is built; ``cli.cmd_serve`` and the test fixture
both do that explicitly, and the comment there says why.

**On the layering.** This is the one ``core/`` module that reads something from
``server/``. Identity is app-layer state by design (Decision 10) and this pack
is its agent-facing projection, so the dependency points the way it does on
purpose. It is kept honest by never *importing* the server at all: the module
is looked up in ``sys.modules`` and, when it is absent, this pack does nothing.
That is exactly the headless case (``agentcad check``, the publish gate), which
therefore pays nothing — not even the FastAPI import — to learn that it has no
hosted config.
"""

from __future__ import annotations

import sys

from .model import AuthError
from .tools import Tool, schema

_SECURITY_MODULE = "agentcad.server.security"


def register(registry, service) -> None:
    module = sys.modules.get(_SECURITY_MODULE)
    cfg = module.current_config() if module is not None else None
    if cfg is None or not cfg.mode.hosted:
        return

    mode_name = cfg.mode.name

    def whoami() -> dict:
        """Who the *current request* is — never who built this registry."""
        who = module.current_principal()
        if who is None:
            # Unreachable through HTTP (the guard answers 401 first), but the
            # chat engine and a future in-process caller reach the registry
            # without a request, and "None" must not render as a principal.
            raise AuthError("authentication required")
        return {
            # The COMPOSED principal — the same string `locks.current_client_id`
            # carries and every claim, roster row and history trailer renders.
            # A bare handle here would be a second spelling of identity.
            "principal": who.client_id,
            "kind": who.kind,
            "role": who.role,
            "mode": mode_name,
        }

    registry.register(Tool(
        "whoami",
        "Who this instance thinks you are: the composed principal "
        "(user:<handle>/<device> or agent:<name>), whether you are a person or "
        "an agent, your role, and the deployment mode. Hosted mode only.",
        schema({}, []),
        whoami,
    ))
