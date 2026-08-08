"""AgentCAD command-line interface.

Commands:
    agentcad serve [--port N] [--projects-dir P] [--no-open]
    agentcad open                    # serve + open the browser
    agentcad mcp                     # MCP stdio server (proxies the HTTP API)
    agentcad new <name>              # create a project
    agentcad export <project> <part> --format step|stl|3mf [-o OUT]
"""

from __future__ import annotations

import argparse
import sys
import threading
import webbrowser
from pathlib import Path

import agentcad
from .config import get_port

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PROJECTS_DIR = Path.home() / "AgentCAD" / "projects"


def _build_service(projects_dir: Path):
    from .core.service import AgentCADService, EventBus
    from .kernel.client import KernelClient

    kernel = KernelClient()
    kernel.start()
    service = AgentCADService(projects_dir, kernel, EventBus())
    _register_examples(service)
    return service


def _register_examples(service) -> None:
    examples = REPO_ROOT / "examples"
    if not examples.is_dir():
        return
    for child in sorted(examples.iterdir()):
        if (child / "project.json").is_file():
            try:
                service.store.open(child)
            except Exception as exc:  # noqa: BLE001 — a broken example must not block startup
                print(f"warning: could not open example {child.name}: {exc}", file=sys.stderr)


def _make_chat_engine(service, registry):
    try:
        from .agent.chat import ChatEngine
    except ImportError:
        return None
    return ChatEngine(registry, service.bus)


def cmd_serve(args, open_browser: bool) -> None:
    import uvicorn

    from .core.tools import build_registry
    from .server.app import create_app

    port = args.port or get_port()
    projects_dir = Path(args.projects_dir or DEFAULT_PROJECTS_DIR)
    service = _build_service(projects_dir)
    registry = build_registry(service)
    chat_engine = _make_chat_engine(service, registry)
    app = create_app(service, registry, chat_engine)

    url = f"http://127.0.0.1:{port}"
    if open_browser and not args.no_open:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    print(f"AgentCAD {agentcad.__version__} — {url}")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


def cmd_mcp(args) -> None:
    from .agent.mcp_server import run_mcp_server

    run_mcp_server()


def cmd_new(args) -> None:
    from .core.project import ProjectStore

    store = ProjectStore(Path(args.projects_dir or DEFAULT_PROJECTS_DIR))
    path = store.create(args.name)
    print(f"created {path}")


def cmd_export(args) -> None:
    service = _build_service(Path(args.projects_dir or DEFAULT_PROJECTS_DIR))
    try:
        project = args.project
        if "/" in project or project.startswith("."):
            project = service.open_project(project)["name"]
        result = service.export_part(project, args.part, args.format)
        out = result["path"]
        if args.output:
            import shutil

            shutil.copy(out, args.output)
            out = args.output
        print(f"exported {out} ({result['size_bytes']} bytes)")
    finally:
        service.kernel.stop()


def main() -> None:
    parser = argparse.ArgumentParser(prog="agentcad", description="Agentic-first CAD")
    parser.add_argument("--version", action="version", version=agentcad.__version__)
    sub = parser.add_subparsers(dest="command")

    for name in ("serve", "open"):
        p = sub.add_parser(name, help=f"{name} the AgentCAD server")
        p.add_argument("--port", type=int, default=None)
        p.add_argument("--projects-dir", default=None)
        p.add_argument("--no-open", action="store_true")

    sub.add_parser("mcp", help="run the MCP stdio server")

    p = sub.add_parser("new", help="create a new project")
    p.add_argument("name")
    p.add_argument("--projects-dir", default=None)

    p = sub.add_parser("export", help="export a part")
    p.add_argument("project", help="project name or path")
    p.add_argument("part")
    p.add_argument("--format", default="step", choices=["step", "stl", "3mf"])
    p.add_argument("-o", "--output", default=None)
    p.add_argument("--projects-dir", default=None)

    args = parser.parse_args()
    if args.command in ("serve", "open"):
        cmd_serve(args, open_browser=args.command == "open")
    elif args.command == "mcp":
        cmd_mcp(args)
    elif args.command == "new":
        cmd_new(args)
    elif args.command == "export":
        cmd_export(args)
    else:
        parser.print_help()
