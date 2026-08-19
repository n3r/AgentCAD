# AgentCAD — hosted image (PRD-005a, PRD-006).
#
# TRUST: an account on this instance can execute arbitrary Python on the host;
# give one only to someone you would give a shell to. A part script IS
# arbitrary Python (agentcad/kernel/worker.py). Since PRD-006 the worker
# confines ITSELF in this image before it imports any geometry — Landlock +
# seccomp applied through ctypes, needing no capability and no bwrap binary
# (verified as uid 10001 under Docker's default seccomp profile): no network,
# writes only under /data/projects, /data/home/.agentcad, the server's work
# root and its own private temp dir, in hosted mode no reads of /data/state
# and nothing under /data/home except /data/home/.agentcad (a write root, so
# readable by construction), and memory/pids/CPU caps around it.
#
# It is still a single-purpose box, not a multi-tenant one: every project on
# the instance is readable and writable to every member's script, which runs
# as this image's uid 10001 (per-project ACLs are PRD-005). See
# docs/deployment.md, "Confinement and quotas".
#
# The image is multi-GB: the OCCT wheels that back build123d are. That is why
# the compose build is not a per-PR CI job (design Decision 12).

# ----------------------------------------------------------------- builder
FROM python:3.12-slim AS builder

# uv resolves and installs from the committed lockfile; --locked makes a
# lockfile that no longer matches pyproject.toml a build failure rather than a
# silent re-resolve.
RUN pip install --no-cache-dir uv==0.5.11

WORKDIR /app
COPY . /app
RUN uv sync --locked --no-dev

# ----------------------------------------------------------------- runtime
FROM python:3.12-slim

# Exactly the OCCT system libraries the Linux CI job already proves are needed
# (.github/workflows/ci.yml, "Install OCCT system libraries (Linux)"), PLUS
# git: the history engine shells out to it (agentcad/core/history.py) and no
# CI step ever had to install it because the runners ship it. Without git
# every project write fails to snapshot.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      libgl1 \
      libglu1-mesa \
      libxrender1 \
      libxcursor1 \
      libxft2 \
      libxinerama1 \
      git \
 && rm -rf /var/lib/apt/lists/*

# A fixed uid/gid so a bind-mounted host directory can be chowned to a number
# the operator can write down (docs/deployment.md says which).
RUN groupadd --gid 10001 agentcad \
 && useradd --uid 10001 --gid 10001 --create-home --home-dir /home/agentcad agentcad

# The venv is an editable install pointing at /app, and `_resources.resource_root()`
# is the parent of the `agentcad` package — so `frontend/`, `examples/` and
# `catalog/` must sit beside it at the SAME path they were installed from.
COPY --from=builder --chown=10001:10001 /app /app
WORKDIR /app
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    HOME=/data/home \
    AGENTCAD_PROJECTS_DIR=/data/projects \
    AGENTCAD_STATE_DIR=/data/state

# Created in the image so a first `docker compose up` against an EMPTY named
# volume inherits these paths and their ownership (Docker seeds a fresh named
# volume from the image). A bind mount does not: chown it yourself, 10001:10001.
RUN mkdir -p /data/home /data/projects /data/state \
 && chown -R 10001:10001 /data

USER agentcad
EXPOSE 8630
CMD ["agentcad", "serve", "--no-open"]
