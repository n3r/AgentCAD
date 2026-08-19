# 0261 — PRD-024: the bench runner (budgeted `ChatEngine`, transcript, `run.json`, `bench run`)

- **Commit:** pending
- **Date:** 2026-08-19
- **Author:** Claude (PRD-024 Task 5)

## Summary
`agentcad bench run` now really runs: the new `agentcad/bench/runner.py` drives
the shipped built-in chat agent over a task in a throwaway cell, bounds it with
three budgets enforced **inside a wrapped Anthropic client** (so `agent/chat.py`
is untouched), writes a path-redacted, image-free `transcript.json` and a
`run.json` holding every non-deterministic fact, and `bench.cli._cmd_run` scores
whatever the agent produced and lays out the results directory of design §8.7 —
including the `bench.json` roster `bench report` reads as its denominator.
This is PRD-024 AC8: a task that exhausts its budget is stopped, flagged
`over_budget`, and **still scored on what it reached**.

## Changes

### `agentcad/bench/runner.py` (new, OCP-free)
- `BudgetedClient` exposes exactly `.messages.create(**kwargs)` — the whole of
  what `ChatEngine` touches — and calls `check()` **before** the inner client:
  wall clock (`time.monotonic`, never `time.time`), tool calls (counted as the
  `tool_use` blocks in the request's own `messages`, so no bus subscription and
  no `chat.py` change), and API turns (`turns + 4`, derived in `tasks.Budgets`).
  A spent ceiling raises `BudgetExhausted(reason)`, which `ChatEngine`'s blanket
  handler (`chat.py:317-336`) catches: the history is repaired, `chat_done`
  fires, the turn ends cleanly and the on-disk state is scoreable.
- `budgeted_client_factory(inner_factory, *, deadline, max_tool_calls,
  max_api_turns)` is what `run_task` installs as the engine's `client_factory`;
  `messages.create` awaits the inner result only when it is awaitable, so the
  same wrapper serves the real `AsyncAnthropic` and a plain scripted test client.
- `run_task(task, *, service, registry, cell, model, api_key, client_factory)`
  prepares the scratch project (`create_project`, or `copytree(starter)` +
  `open_project`), drives **one** engine turn, and returns a frozen `RunOutcome`
  (`over_budget`, `stopped`, `usage`, `transcript`). An outer
  `asyncio.wait_for(..., wall_s + WALL_GRACE_S)` is the backstop for a *tool*
  already in flight — the client cannot preempt one, the same honest limitation
  `agentcad check --budget` documents.
- `stopped ∈ STOPPED` is resolved from the client's own verdict, then a
  recorded `[chat error]` delta, then the engine's own 30-call break;
  `over_budget` is `stopped != "model_ended_turn"`.
- `usage["tool_calls"]` is counted from the **final history**, not from the
  client's last request: the client counts what it is about to send, so a clean
  end of turn would under-report the last round by one or more calls.
- `_RunBus` is a four-line recorder handed to `ChatEngine` instead of the
  service bus: `EventBus.subscribe` is a 256-deep queue that *drops when full*,
  and a runaway turn publishes three events per tool call, so a subscriber
  cannot honestly answer "did this turn die of something other than a budget?".
- `transcript_payload` redacts the **cell** first and then the projects root
  (both their resolved and unresolved spellings) and elides every base64 render
  payload — the image block's `source.data` *and* the `"png_base64"` pair inside
  the JSON text half — to `<image omitted>`, the string the bus event uses.
- `run_json` holds schema, task identity, agent, model, `agentcad`, harness,
  ISO-8601 `Z` timestamps, duration, budgets, usage, `over_budget`, `stopped`,
  host and `"transcript": "transcript.json"`. Nothing here reaches `score.json`,
  which stays timestamp-free and byte-stable (FR6/AC3).
- `require_agent(api_key, client_factory)` refuses a run with neither a key nor
  an injected client **before** anything spawns, so a stray real API call out of
  a test is unreachable rather than unlikely.
- `_refuse_outside_cell` turns §8.1's promise ("the user's projects dir is never
  involved") into a check: the service's projects root must be the cell or live
  inside it.
- `CLIENT_FACTORY` is the single documented test seam — a module attribute, not
  an environment variable (process-global env would clobber a neighbouring
  pytest worker, the same argument that made `_build_service` take `examples`).

### `agentcad/bench/cli.py`
- `_cmd_run` replaces the exit-2 stub: select (`--tasks` glob AND `--set`,
  defaulting to the `core` set), apply `--budget` to the wall clock only, refuse
  and create `--work-dir` before the kernel spawns, build **one** service
  (`bench_service`, examples off) over a throwaway projects root, then per task
  cut a cell under the work root with its own `AgentCADService` over the
  **shared** kernel — deliberately *not* muzzled, because a run measures the
  shipped surface (history snapshots included) in a tree it owns and removes.
- Per task it copies the project out to `<report>/tasks/<category>/<id>/
  submission/` (`scoring.COPY_IGNORE`, so no `.cache`/`exports`/`.history`),
  writes `transcript.json` and `run.json`, scores the copy with `Scorer` and
  writes `score.json`. A task that raises takes down only its own row.
- `<report>/bench.json` carries the run header **and the `tasks` roster keyed by
  task id** — `report.aggregate` reads exactly that as its denominator, so a
  selected task that never scored shows up `missing: true` instead of vanishing.
- Exit 0 when every selected task produced a score, 2 otherwise; **never 1**.
  `--json` puts the header alone on stdout byte-for-byte as written, `--quiet`
  prints nothing.
- New helpers `_budgeted`, `_agent_config`, `_run_one_task`, `_run_lines`,
  `_print_run`; `_NOT_IMPLEMENTED` is gone and `DEFAULT_SET`/`_RUN_ROW` are new.

### Tests
- `tests/test_bench_runner.py` (new, 13 tests, fully offline): a scripted agent
  produces a scoreable project; **AC8** — a runaway agent is stopped at exactly
  `budgets.turns` tool calls, flagged, and its work still scores `built = 1.0`;
  a zero wall budget stops before the inner client is reached; deadlines are
  monotonic; a keyless run is refused, not attempted; the transcript is redacted
  and image-free; `run.json` carries the timestamps; `examples=False` leaves the
  scratch project alone in `list_projects`; and the CLI end-to-end
  (`main()` → argparse → `cmd_bench` → `_cmd_run`) writes the whole §8.7 layout,
  which `report.aggregate` then reads.
- `tests/test_bench_cli.py`: the Task-4 placeholder `test_run_refuses_honestly_
  for_now` is replaced by `test_run_without_an_agent_it_can_drive_is_exit_two`,
  which pins the same process-boundary contract against the real handler
  (deterministic — it deletes `ANTHROPIC_API_KEY` and clears `CLIENT_FACTORY`).

## Files
- `agentcad/bench/runner.py` — new: the budgeted client, `run_task`,
  `transcript_payload`, `run_json`, `RUN_SCHEMA`/`BENCH_SCHEMA`/`STOPPED`/
  `WALL_GRACE_S`, the `CLIENT_FACTORY` seam.
- `agentcad/bench/cli.py` — `_cmd_run` implemented; `_budgeted`,
  `_agent_config`, `_run_one_task`, `_run_lines`, `_print_run`, `DEFAULT_SET`,
  `_RUN_ROW` added; `_NOT_IMPLEMENTED` removed.
- `tests/test_bench_runner.py` — new.
- `tests/test_bench_cli.py` — the `run` stub test replaced.

## Notes
- **Zero edits to `agent/chat.py`.** The budgets work because the engine's
  blanket handler already ends a turn cleanly with a repaired history when the
  client raises. That is the whole design of §8.3 and it is why AC8 costs
  nothing in the product.
- **One cell per task, one kernel per run.** Design §9.4's skeleton shows one
  service and §8.1 asks for a cell per task; those are reconciled the way
  `checks._ephemeral_service` does it — a second `AgentCADService` per cell over
  the *shared* kernel. A kernel pool per task would cost seconds and half a
  gigabyte 25 times over to run the same builds.
- **Serial, and there is no `--jobs`** (design D21). Do not add one.
- The results directory uses `submission/` (design §8.7 and `publish.py`'s
  disclosure key), not `project/`.
- `--budget` moves the **wall** budget only: the tool-call ceiling is what keeps
  a run inside one engine turn, and a flag that could raise it would let one
  invocation measure something the task set does not describe.
- Docs (`docs/bench.md`, `docs/roadmap.md`) and the `bench.yml` workflow are
  Task 11's; nothing here touches them.

## Verification
- `uv run pytest -q tests/test_bench_runner.py` — **13 passed** (17.6 s).
- `uv run pytest -q tests/test_bench_runner.py tests/test_bench_cli.py
  tests/test_chat.py -x` — **49 passed** (40.6 s).
- `make test` — <orchestrator fills>
