.PHONY: setup run serve test test-fast test-pr test-full test-portability test-sequential app dist smoke

PYTEST_PARALLEL = -n 2 --dist loadscope

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

app:
	bash scripts/make_app.sh

dist:
	bash scripts/build_binary.sh

smoke:
	bash scripts/smoke_binary.sh
