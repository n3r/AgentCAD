.PHONY: setup run serve test test-fast test-pr test-full test-portability test-sequential test-linux app dist smoke

# ?= so a small machine (or CI) can override: make test PYTEST_PARALLEL="-n 2 --dist loadscope"
# --no-loadscope-reorder: xdist's count-descending queue sort sends the
# heaviest (few-test) engine classes to the queue tail, where a late start
# sets the suite's wall; collection order starts them early instead.
PYTEST_PARALLEL ?= -n auto --maxprocesses=8 --dist loadscope --no-loadscope-reorder

setup:
	uv sync

run:
	uv run agentcad open

serve:
	uv run agentcad serve --no-open

test: test-full

test-fast:
	uv run pytest -q $(PYTEST_PARALLEL) -m "not slow"

test-pr:
	uv run pytest -q $(PYTEST_PARALLEL) -m "not exhaustive"

test-full:
	uv run pytest -q $(PYTEST_PARALLEL)

test-portability:
	uv run pytest -q $(PYTEST_PARALLEL) -m portability

test-sequential:
	uv run pytest -q

# The Linux confinement loop on a macOS dev box: copies the tree into the
# shipped image (Landlock is not coherent over Docker Desktop's bind mounts)
# and runs the Linux battery inside it. Needs the `agentcad:local` image —
# see scripts/linux-test.sh.
test-linux:
	sh scripts/linux-test.sh

app:
	bash scripts/make_app.sh

dist:
	bash scripts/build_binary.sh

smoke:
	bash scripts/smoke_binary.sh
