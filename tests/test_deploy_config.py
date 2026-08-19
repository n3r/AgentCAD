"""PRD-005a slice 6: the deployment artefacts, checked without a Docker daemon.

Design Decision 12: everything that matters runs without Docker. The image
build is a multi-GB job and cannot be a per-PR gate, so what a PR *can* prove
is that the compose file still pins the invariants, that no secret was
committed, that the trust sentence is in both of the places FR17 assigns to
this slice, and that the Dockerfile still installs the seven system packages
the product needs. The build itself is the smoke workflow's job.

`_parse_compose` is ~25 lines of indentation-aware parsing rather than PyYAML,
because the global constraint is zero new dependencies and this is the only
YAML in the tree.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
COMPOSE = ROOT / "compose.yaml"
DOCKERFILE = ROOT / "Dockerfile"
DOCKERIGNORE = ROOT / ".dockerignore"
ENV_EXAMPLE = ROOT / ".env.example"
DEPLOYMENT_DOC = ROOT / "docs" / "deployment.md"

TRUST = "execute arbitrary Python on the host"


# ------------------------------------------------------------- the parser


def _scalar(text: str):
    text = text.strip()
    if text.startswith("["):
        return json.loads(text)
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        return text[1:-1]
    return text


def _parse(lines, start, indent):
    """(value, next_index) for the block at *indent*. Dicts and lists only."""
    if lines[start][1].startswith("- "):
        items = []
        i = start
        while i < len(lines) and lines[i][0] == indent \
                and lines[i][1].startswith("- "):
            items.append(_scalar(lines[i][1][2:]))
            i += 1
        return items, i
    node: dict = {}
    i = start
    while i < len(lines) and lines[i][0] == indent:
        key, _, rest = lines[i][1].partition(":")
        key = key.strip()
        i += 1
        if rest.strip():
            node[key] = _scalar(rest)
        elif i < len(lines) and lines[i][0] > indent:
            node[key], i = _parse(lines, i, lines[i][0])
        else:
            node[key] = None
    return node, i


def _parse_compose(path: Path) -> dict:
    lines = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        lines.append((len(raw) - len(raw.lstrip(" ")), raw.strip()))
    doc, _ = _parse(lines, 0, 0)
    return doc


def test_the_parser_reads_the_shapes_this_file_uses(tmp_path):
    """The positive control: a parser that returned `{}` would make every
    assertion below vacuously true, which is the exact shape of green this
    repository keeps catching."""
    sample = tmp_path / "sample.yaml"
    sample.write_text(
        "services:\n"
        "  a:\n"
        "    image: x:1\n"
        "    environment:\n"
        "      K: v\n"
        "    volumes:\n"
        "      - vol:/data\n"
        "    healthcheck:\n"
        '      test: ["CMD", "true"]\n'
        "volumes:\n"
        "  vol:\n"
    )
    doc = _parse_compose(sample)
    assert doc["services"]["a"]["image"] == "x:1"
    assert doc["services"]["a"]["environment"] == {"K": "v"}
    assert doc["services"]["a"]["volumes"] == ["vol:/data"]
    assert doc["services"]["a"]["healthcheck"]["test"] == ["CMD", "true"]
    assert "vol" in doc["volumes"]


# ------------------------------------------------------------- the compose


@pytest.fixture(scope="module")
def service():
    return _parse_compose(COMPOSE)["services"]["agentcad"]


def test_compose_pins_the_invariants(service):
    env = service["environment"]
    assert env["AGENTCAD_MODE"] == "hosted"
    assert env["AGENTCAD_EXAMPLES"] == "0"
    assert env["AGENTCAD_PROJECTS_DIR"].startswith("/data")
    assert env["AGENTCAD_STATE_DIR"].startswith("/data")
    assert any(v.endswith(":/data") for v in service["volumes"])
    assert "healthcheck" in service


def test_the_container_binds_every_interface_and_is_therefore_hosted(service):
    """The interlock read from the other side: the container must bind
    `0.0.0.0`, which `check_bind` refuses unless the mode is hosted. The two
    settings have to agree in this file or the container will not start."""
    env = service["environment"]
    assert env["AGENTCAD_HOST"] == "0.0.0.0"
    assert env["AGENTCAD_MODE"] == "hosted"


def test_the_kernel_pool_is_pinned_not_floated(service):
    """`max(1, min(3, cores//3))` on a big host would be 3 workers at ~0.5 GB
    each on a box the docs say can be 4 GB. Pinning is the safe default —
    pinned, not floated to a host-dependent value.

    The pinned value is **2**, not 1, as of PRD-007: the share customizer
    reserves one worker for signed-in members (effective in-flight cap =
    pool_size - 1), so it needs at least 2 to run at all — a 1-worker pool
    answers `/variant`/`/download` with a `503`. Two workers is ~1 GB RSS,
    within the documented 2 vCPU / 4 GB floor (deployment.md), so this is
    still the memory-safe pin the test guards, just at the value the
    customizer requires. A viewer-only deployment can set it back to 1."""
    assert service["environment"]["AGENTCAD_KERNEL_POOL_SIZE"] == "2"


def test_the_healthcheck_actually_hits_the_health_route(service):
    probe = service["healthcheck"]["test"]
    assert probe[0] == "CMD"
    assert "/api/health" in " ".join(probe)


def test_the_healthcheck_sends_the_configured_host_header(service):
    """Found by actually running it: a probe to `127.0.0.1` sends
    `Host: 127.0.0.1`, and the hosted guard requires `Host` to equal the
    configured public origin's host — so the naive probe is a `403` and the
    container reports **unhealthy** while serving perfectly. The probe has to
    dial the loopback interface and *say* it is the public origin.
    """
    probe = " ".join(service["healthcheck"]["test"])
    assert "AGENTCAD_PUBLIC_ORIGIN" in probe
    assert "'Host'" in probe
    assert "127.0.0.1" in probe, "probe the loopback, not the published name"


def test_no_secret_is_committed():
    text = COMPOSE.read_text(encoding="utf-8")
    assert "AGENTCAD_SECRET_KEY: ${AGENTCAD_SECRET_KEY" in text
    assert "changeme" not in text.lower()
    # A literal key of any length would defeat the interpolation above.
    assert "acad_" not in text


def test_the_env_example_is_a_template_and_not_a_secret():
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    assert "AGENTCAD_PUBLIC_ORIGIN=" in text
    assert text.count("AGENTCAD_SECRET_KEY=\n") == 1, "the example must be blank"


def test_dot_env_is_never_built_into_the_image():
    """`.env` holds the session key. The build context must not carry it into
    a layer, where `docker history` would."""
    ignored = DOCKERIGNORE.read_text(encoding="utf-8").splitlines()
    assert ".env" in [line.strip() for line in ignored]
    assert ".venv/" in [line.strip() for line in ignored]


def test_dot_env_is_never_committed_either():
    """The quick start is literally `cp .env.example .env`, and the file it
    makes holds `AGENTCAD_SECRET_KEY`. Keeping it out of the image
    (`.dockerignore`) and out of the repository (`.gitignore`) are the same
    requirement; only the first was covered, and an operator who follows the
    documented steps is exactly the person who would push it."""
    ignored = [line.strip() for line
               in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()]
    assert ".env" in ignored
    # ...and not by a blanket rule that would also swallow the template the
    # docs tell people to copy.
    assert ".env.example" not in ignored
    assert ENV_EXAMPLE.is_file()


def test_the_trust_sentence_is_in_the_compose_header_and_the_docs():
    assert TRUST in COMPOSE.read_text(encoding="utf-8")
    assert TRUST in DEPLOYMENT_DOC.read_text(encoding="utf-8")


def test_the_trust_sentence_is_the_first_thing_in_the_deployment_doc():
    """FR17 is about it being unmissable, not about it being present. A page
    that explains the quick start first and the danger on page three has not
    told anybody anything."""
    text = DEPLOYMENT_DOC.read_text(encoding="utf-8")
    assert text.index(TRUST) < text.index("docker compose up")


# ------------------------------------------------------------ the image


def test_the_dockerfile_installs_git_and_the_occt_libraries():
    text = DOCKERFILE.read_text(encoding="utf-8")
    for pkg in ("libgl1", "libglu1-mesa", "libxrender1", "libxcursor1",
                "libxft2", "libxinerama1", "git"):
        assert pkg in text, pkg


def test_the_dockerfile_installs_from_the_lockfile():
    """`--locked` makes a lockfile that drifted from pyproject.toml a build
    failure rather than a silent re-resolve into different versions than the
    test suite ever ran against — build123d's version is pinned for a reason."""
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert "uv sync --locked --no-dev" in text


def test_the_image_does_not_run_as_root():
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert "USER agentcad" in text
    assert text.rindex("USER agentcad") > text.rindex("apt-get")


def test_the_image_keeps_the_frontend_beside_the_package():
    """`_resources.resource_root()` is the PARENT of the `agentcad` package, so
    `frontend/`, `examples/` and `catalog/` must be at that same path or the UI
    404s and the bundled catalog disappears. Copying `/app` wholesale is what
    guarantees it; a `pip install agentcad` into site-packages would not."""
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert "COPY --from=builder" in text and "/app /app" in text
    for excluded in ("frontend", "examples", "catalog"):
        assert excluded not in DOCKERIGNORE.read_text(encoding="utf-8")


def test_the_state_and_projects_dirs_are_on_the_volume():
    text = DOCKERFILE.read_text(encoding="utf-8")
    for var in ("HOME=/data/home", "AGENTCAD_PROJECTS_DIR=/data/projects",
                "AGENTCAD_STATE_DIR=/data/state"):
        assert var in text, var


# ------------------------------------ the state dir may not be kernel-writable


@pytest.fixture
def security_slot_cleared():
    """`cmd_serve` installs a real `SecurityConfig` in a process-global slot
    (it has to: tool registration is not inside a request). Clear it after,
    or the next test in this worker builds a LOCAL app that believes it is
    hosted — the same cleanup `conftest.hosted` does."""
    yield
    from agentcad.server import security as security_module

    security_module.install(None)


def _serve_with(monkeypatch, tmp_path, projects, state):
    """`cmd_serve` up to the point of serving, with the kernel stubbed out.

    Returns the recorded stderr through capsys at the call site; the service is
    a stand-in carrying only what the guard reads (`writable_roots`) plus the
    two attributes the `finally` touches.
    """
    import uvicorn

    from agentcad import cli

    monkeypatch.setenv("AGENTCAD_CONFIG", str(tmp_path / "cfg.json"))
    monkeypatch.setenv("AGENTCAD_MODE", "hosted")
    monkeypatch.setenv("AGENTCAD_PUBLIC_ORIGIN", "https://cad.example.com")
    monkeypatch.setenv("AGENTCAD_STATE_DIR", str(state))
    service = SimpleNamespace(
        kernel=SimpleNamespace(stop=lambda: None), work_root=None,
        writable_roots=[str(projects)])
    monkeypatch.setattr(cli, "_build_service", lambda *a, **k: service)
    monkeypatch.setattr(cli, "_make_chat_engine", lambda svc, reg: None)
    monkeypatch.setattr("agentcad.core.tools.build_registry",
                        lambda svc: object())
    monkeypatch.setattr("agentcad.server.app.create_app",
                        lambda *a, **k: object())
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: None)
    args = SimpleNamespace(host="0.0.0.0", port=8630,
                           projects_dir=str(projects), no_open=True)
    return cli.cmd_serve, args


def test_a_hosted_state_dir_inside_a_write_root_refuses_to_serve(
        monkeypatch, tmp_path, capsys, security_slot_cleared):
    """Review I6. FR5's hosted read posture exists to keep
    `<state-dir>/secret.key` out of a member's reach — but a state dir placed
    **inside a writable root** is defeated from the other side: that root is
    granted write access explicitly, so the session secret is readable and
    rewritable however narrow the read allow-list is, and whoever reads it can
    forge any session.

    Fatal, not a warning: this is one misplaced path with an exact remedy, and
    serving anyway would be serving a hosted instance whose accounts are
    already forgeable.
    """
    projects = tmp_path / "data" / "projects"
    projects.mkdir(parents=True)
    state = projects / "state"

    serve, args = _serve_with(monkeypatch, tmp_path, projects, state)
    with pytest.raises(SystemExit) as exit_info:
        serve(args, open_browser=False)

    assert exit_info.value.code == 2
    err = capsys.readouterr().err
    assert "AGENTCAD_STATE_DIR" in err
    assert str(state.resolve()) in err and str(projects.resolve()) in err
    assert "secret.key" in err


def test_the_compose_layout_serves(monkeypatch, tmp_path, capsys,
                                   security_slot_cleared):
    """The shipped layout is the passing case: `/data/state` is a **sibling**
    of `/data/projects`, not a child, so nothing here refuses it."""
    root = tmp_path / "data"
    projects = root / "projects"
    projects.mkdir(parents=True)
    state = root / "state"

    serve, args = _serve_with(monkeypatch, tmp_path, projects, state)
    serve(args, open_browser=False)          # no SystemExit

    assert "AGENTCAD_STATE_DIR" not in capsys.readouterr().err


def test_hosted_default_state_dir_with_the_new_root_is_not_refused(
        monkeypatch, tmp_path, capsys):
    """PRD-007 merge: `_writable_roots` now grants
    `<state-dir>/publications/build` for the shared-pool variant builds
    (`core/share_build.py`). That subtree is a CHILD of the state dir, not a
    container of it, so the ordinary hosted layout — state dir at its default
    location, projects dir elsewhere, both plus the new subtree among the
    granted roots — must not trip the guard."""
    from agentcad import cli

    monkeypatch.setenv("AGENTCAD_CONFIG", str(tmp_path / "cfg.json"))
    monkeypatch.delenv("AGENTCAD_STATE_DIR", raising=False)
    projects = tmp_path / "projects"
    roots = cli._writable_roots(projects)
    build_root = str(Path(tmp_path / "state" / "publications" / "build"))
    assert build_root in roots
    service = SimpleNamespace(writable_roots=roots)

    cli._refuse_state_dir_in_a_write_root(SimpleNamespace(hosted=True),
                                          service)
    assert capsys.readouterr().err == ""


def test_hosted_state_dir_inside_the_projects_dir_is_still_refused(
        monkeypatch, tmp_path, capsys):
    """The new publications/build root does not weaken the existing guard: a
    state dir placed inside the projects tree is refused exactly as before,
    even once `_writable_roots` grants a second root alongside it."""
    from agentcad import cli

    projects = tmp_path / "data" / "projects"
    state = projects / "state"
    monkeypatch.setenv("AGENTCAD_CONFIG", str(tmp_path / "cfg.json"))
    monkeypatch.setenv("AGENTCAD_STATE_DIR", str(state))
    roots = cli._writable_roots(projects)
    service = SimpleNamespace(writable_roots=roots)

    with pytest.raises(SystemExit) as exit_info:
        cli._refuse_state_dir_in_a_write_root(SimpleNamespace(hosted=True),
                                              service)
    assert exit_info.value.code == 2
    assert "AGENTCAD_STATE_DIR" in capsys.readouterr().err


def test_local_mode_is_not_checked(monkeypatch, tmp_path):
    """One trusted user on loopback, and no session to forge from another
    account: the guard is a hosted-mode rule, and local mode is unchanged."""
    from agentcad import cli

    projects = tmp_path / "projects"
    projects.mkdir()
    monkeypatch.setenv("AGENTCAD_STATE_DIR", str(projects / "state"))
    service = SimpleNamespace(writable_roots=[str(projects)])

    cli._refuse_state_dir_in_a_write_root(SimpleNamespace(hosted=False),
                                          service)
    # ...and a service that never recorded its roots is left alone rather
    # than guessed at.
    cli._refuse_state_dir_in_a_write_root(SimpleNamespace(hosted=True),
                                          SimpleNamespace())


# ------------------------------------------------------------- the doc


@pytest.mark.parametrize("variable", [
    "AGENTCAD_MODE", "AGENTCAD_PUBLIC_ORIGIN", "AGENTCAD_SECRET_KEY",
    "AGENTCAD_STATE_DIR", "AGENTCAD_PROJECTS_DIR", "AGENTCAD_HOST",
    "AGENTCAD_PORT", "AGENTCAD_KERNEL_POOL_SIZE", "AGENTCAD_EXAMPLES",
    "AGENTCAD_CONFIG", "AGENTCAD_PACKAGES_DIR", "AGENTCAD_INDEXES_DIR",
    "AGENTCAD_NO_SANDBOX", "AGENTCAD_URL", "AGENTCAD_AGENT_ID",
    "AGENTCAD_TOKEN",
])
def test_every_fr24_variable_is_documented(variable):
    """FR24: configuration is environment-only and enumerated in ONE place. A
    variable the code reads and the table omits is a variable nobody sets."""
    assert variable in DEPLOYMENT_DOC.read_text(encoding="utf-8")


@pytest.mark.parametrize("topic", ["Sizing", "Backup", "Restore", "Upgrade",
                                   "TLS"])
def test_fr27_topics_are_covered(topic):
    assert topic in DEPLOYMENT_DOC.read_text(encoding="utf-8")


def test_the_backup_procedure_does_not_require_stopping_the_server():
    """Every write is an atomic replace, so an archive taken live is a set of
    complete files. Telling operators to stop the service would be a worse
    promise than the storage actually makes."""
    text = DEPLOYMENT_DOC.read_text(encoding="utf-8")
    assert "No downtime and no quiescing" in text
