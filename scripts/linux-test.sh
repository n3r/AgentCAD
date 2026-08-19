#!/bin/sh
# Run the Linux sandbox tests inside the shipped image, from a macOS dev box.
#
# The tree is COPIED into the container's own filesystem, never bind-mounted:
# Docker Desktop's `fakeowner` virtiofs mounts are not Landlock-coherent (the
# spike measured grants having no effect *and* reads being denied there), while
# overlayfs — which is what /tmp inside the container is — behaves correctly.
#
# The image predates this branch, so PYTHONPATH shadows the baked-in package
# for the server-side imports AND for the worker the client spawns
# (`sys.executable -u -m agentcad.kernel.worker` inherits the environment).
#
#   make test-linux                        # the Slice-2 battery + the units
#   sh scripts/linux-test.sh tests/x.py    # or any explicit file list
#   AGENTCAD_LINUX_IMAGE=agentcad:pr sh scripts/linux-test.sh
set -eu

IMAGE=${AGENTCAD_LINUX_IMAGE:-agentcad:local}
REPO=$(cd "$(dirname "$0")/.." && pwd)
TESTS=${*:-tests/test_sandbox_linux.py tests/test_supervisor.py tests/test_confine_unit.py tests/test_denials.py tests/test_meter.py tests/test_protocol_ids.py tests/test_prd006_acceptance.py}
WORK=/tmp/agentcad-work

exec docker run --rm -v "$REPO":/src:ro \
  -e AGENTCAD_EXPECT_SANDBOX=active -e AGENTCAD_EXPECT_QUOTAS=active \
  -w /tmp "$IMAGE" sh -c "
    set -eu
    mkdir -p $WORK
    # tar rather than cp -r: / is not writable by uid 10001, and the host
    # .venv is a macOS one that must not shadow the image's.
    tar -C /src -cf - --exclude=./.venv --exclude=./.git . | tar -C $WORK -xf -
    cd $WORK
    # The image's venv ships no pip (uv installed into it); ensurepip is in
    # the base interpreter and puts one there.
    python -m ensurepip --default-pip >/dev/null 2>&1 || true
    python -m pip install -q --disable-pip-version-check pytest pytest-timeout
    PYTHONPATH=$WORK exec python -m pytest -q -p no:cacheprovider $TESTS
  "
