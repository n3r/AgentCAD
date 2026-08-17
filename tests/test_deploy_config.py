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
    each on a box the docs say can be 4 GB. Pinning is the safe default."""
    assert service["environment"]["AGENTCAD_KERNEL_POOL_SIZE"] == "1"


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
