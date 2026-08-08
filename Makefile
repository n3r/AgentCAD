.PHONY: setup run serve test app

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
