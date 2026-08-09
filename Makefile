.PHONY: setup run serve test app dist smoke

setup:
	uv sync

run:
	uv run agentcad open

serve:
	uv run agentcad serve --no-open

test:
	uv run pytest -q

app:
	bash scripts/make_app.sh

dist:
	bash scripts/build_binary.sh

smoke:
	bash scripts/smoke_binary.sh
