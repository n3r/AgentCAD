"""The built-in runner: `ChatEngine` driven from a **budgeted client**.

Design §8. Three things here are load-bearing and each is written down where
it is easy to lose.

**Budgets live in the client, not in the engine** (§8.3). `ChatEngine` has no
wall clock and a hard-coded ``MAX_TOOL_CALLS_PER_TURN = 30``
(`agent/chat.py:50`). Rather than add a bench-shaped parameter to a product
class, the runner wraps the Anthropic client: :class:`BudgetedClient` checks
three ceilings *before* it calls the inner client and raises
:class:`BudgetExhausted` when one is spent. That exception surfaces inside
``ChatEngine._run_turn_locked``'s blanket handler (`chat.py:317-336`), which
repairs the history (`_repair_history`), publishes one ``chat_delta`` and
fires ``chat_done`` in its ``finally``. **The turn ends cleanly, the
transcript stays Messages-API-valid, and whatever the agent already wrote to
disk is scoreable** — which is AC8, bought with zero edits to `chat.py`.

**One engine turn per task.** The loader refuses ``turns >
MAX_TOOL_CALLS_PER_TURN`` (§2 rule 3), so the engine's own limit is never what
stops a run and no continuation logic exists. That is the PRD's design, not a
workaround: the bench measures the product surface as it ships, and if 30 tool
calls is too tight that is a *product* finding raised in `chat.py`, after
which the bench measures the change.

**Nothing non-deterministic reaches `score.json`.** Timestamps, durations, the
model id, the agent name, the host and the usage counters all live in
``run.json`` (:func:`run_json`); `score.json` stays byte-identical across two
runs of the same submission (FR6/AC3), and the two documents sit side by side.

This module is OCP-free like the rest of `agentcad/bench/`, and it imports
`anthropic` only through `ChatEngine`'s own default factory — a run with no
key and no injected client is **refused** (:func:`require_agent`) rather than
attempted, so a stray API call out of a test is unreachable rather than
unlikely.
"""
from __future__ import annotations

import asyncio
import inspect
import platform
import re
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import agentcad

from ..core.model import ValidationError
from . import HARNESS_VERSION
#: What never travels out of a run's cell into a published submission —
#: `scoring.COPY_IGNORE`'s list, reused rather than restated: `.cache` and
#: `exports` are derived, and `.history` is a whole git repository.
from .scoring import COPY_IGNORE
from .tasks import Task, prompt_text

#: ``run.json``'s version.
RUN_SCHEMA = 1
#: ``bench.json``'s version — the run header `bench report` reads
#: (`report.HEADER_SCHEMA`). Declared here because this module is what writes it.
BENCH_SCHEMA = 1
#: ``transcript.json``'s version.
TRANSCRIPT_SCHEMA = 1

#: Seconds added to a task's wall budget before the **outer** `wait_for` fires.
#: The client wrapper refuses the next *API* call, but it cannot preempt a
#: **tool** already in flight — a kernel build that hangs is exactly the
#: limitation `agentcad check --budget` documents (`checks.py:1216-1221`), and
#: this is its backstop, not its replacement.
#:
#: **What it actually bounds, stated honestly.** `wait_for` cancels the turn's
#: task, but a tool call is running in an executor thread that cancellation
#: cannot reach; `asyncio.run` then joins that thread on the way out. So the
#: real ceiling on `run_task` is
#: ``wall_s + WALL_GRACE_S + the in-flight kernel request's own timeout``
#: (`KernelClient(timeout_s=60.0)`, and `asyncio`'s executor join gives up
#: after `THREAD_JOIN_TIMEOUT = 300 s` on 3.12). A run is therefore bounded,
#: but not by this number alone — and a task's `wall_s` is not a hard stop for
#: the *process*, only for the *conversation*.
WALL_GRACE_S = 30.0

#: Every value ``run.json``'s ``stopped`` may take (design §8.6).
STOPPED = ("model_ended_turn", "wall_clock", "tool_calls", "api_turns", "error")

#: The chat session a run drives. `ChatEngine.DEFAULT_SESSION`'s value, spelled
#: out so this module does not import `chat.py` at import time.
SESSION = "main"

#: What replaces a render's base64 payload in a written transcript. The exact
#: string the bus event already uses (`chat.py:113-115`), so a reader who has
#: seen one recognises the other.
IMAGE_PLACEHOLDER = "<image omitted>"

#: A ``"png_base64": "…"`` pair inside an embedded JSON **string**. A tool
#: result reaches the history as text, so eliding the dict key alone would
#: leave a 2 MB blob in the one artefact that exists to be read.
_PNG_JSON_RE = re.compile(r'"png_base64"\s*:\s*"(?:[^"\\]|\\.)*"')

#: The one test seam for the CLI. ``bench run`` calls this when it is not
#: ``None`` instead of building an Anthropic client, which is what lets
#: `tests/test_bench_runner.py` drive the **whole** `main()` → argparse →
#: `cmd_bench` → `_cmd_run` path offline. It is a module attribute rather than
#: an environment variable on purpose: `os.environ` is process-global and a
#: bench test inside a pytest worker would clobber a neighbour (the same
#: argument that made `_build_service` take ``examples=False``).
CLIENT_FACTORY: Callable[[], Any] | None = None


# ------------------------------------------------------------- the client

class BudgetExhausted(Exception):
    """The next API call is refused because a budget is spent.

    Raised **inside** ``messages.create``, so `ChatEngine`'s blanket handler
    catches it: the history is repaired, one ``chat_delta`` is published and
    ``chat_done`` fires in the ``finally``. The turn ends cleanly and whatever
    the agent already wrote to disk is scoreable — which is AC8.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(f"bench budget exhausted: {reason}")
        self.reason = reason


def _count_tool_uses(messages) -> int:
    """``tool_use`` blocks across *messages* — the tool-call count.

    Counted from the conversation itself, so it needs no bus subscription and
    no `chat.py` change: every tool call the engine has issued is a block in
    the history it hands the client.
    """
    total = 0
    for message in messages or ():
        content = (message.get("content") if isinstance(message, dict)
                   else None)
        if not isinstance(content, list):
            continue
        total += sum(1 for block in content
                     if isinstance(block, dict)
                     and block.get("type") == "tool_use")
    return total


class _BudgetedMessages:
    """``client.messages`` — the one attribute `ChatEngine` touches."""

    def __init__(self, owner: "BudgetedClient") -> None:
        self._owner = owner

    async def create(self, **kwargs):
        owner = self._owner
        owner.check(kwargs.get("messages"))
        result = owner.inner.messages.create(**kwargs)
        # Awaited only when it is awaitable: the real client is
        # `AsyncAnthropic`, and a test's scripted client is a plain callable.
        # Insisting on one or the other would either put `async def` in every
        # fixture or make the wrapper unusable in production.
        if inspect.isawaitable(result):
            result = await result
        return result


class BudgetedClient:
    """An Anthropic client that refuses the next call once a budget is spent.

    Exposes exactly ``.messages.create(**kwargs)`` — the whole of what
    `ChatEngine` uses — so it is a wrapper and not a fork.
    """

    def __init__(self, inner, *, deadline, max_tool_calls, max_api_turns):
        self.inner = inner
        #: `time.monotonic`, never `time.time`: an NTP step must not move a
        #: budget (`checks.py:1208-1210`).
        self.deadline = deadline
        self.max_tool_calls = int(max_tool_calls)
        self.max_api_turns = int(max_api_turns)
        self.api_turns = 0
        self.tool_calls = 0
        self.stopped: str | None = None
        self.messages = _BudgetedMessages(self)

    def check(self, request_messages) -> None:
        """Raise :class:`BudgetExhausted` when a ceiling is reached.

        Order matters and is the order of the design's list: the wall clock is
        the one budget a caller can feel, so it is named first when two are
        spent at once.

        **The tool-call cap is a soft one, by construction.** The check happens
        between API calls, and one assistant message may carry several
        ``tool_use`` blocks — `ChatEngine` executes *all* of them before asking
        for the next response (`chat.py:266-305`). So a round can carry the
        count past ``max_tool_calls``; what is guaranteed is that no *further*
        model turn is bought once it is reached. Tightening that would mean
        interrupting a round mid-flight, which is the one thing that would make
        the on-disk state unscoreable — the opposite of AC8.
        """
        self.tool_calls = _count_tool_uses(request_messages)
        if self.deadline is not None and time.monotonic() > self.deadline:
            self.stopped = "wall_clock"
        elif self.tool_calls >= self.max_tool_calls:
            self.stopped = "tool_calls"
        elif self.api_turns >= self.max_api_turns:
            self.stopped = "api_turns"
        if self.stopped:
            raise BudgetExhausted(self.stopped)
        self.api_turns += 1


def budgeted_client_factory(inner_factory, *, deadline, max_tool_calls,
                            max_api_turns):
    """A ``client_factory`` for `ChatEngine` that wraps *inner_factory*'s client.

    `ChatEngine` constructs its client **once** and caches it
    (`chat.py:229-230`), so the wrapper built here is the one that sees every
    call of the turn and its counters are the turn's counters.
    """
    def factory() -> BudgetedClient:
        return BudgetedClient(inner_factory(), deadline=deadline,
                              max_tool_calls=max_tool_calls,
                              max_api_turns=max_api_turns)

    return factory


# ------------------------------------------------------------ the outcome

@dataclass(frozen=True)
class RunOutcome:
    """What one task's turn produced, apart from the files on disk.

    ``over_budget`` is ``stopped != "model_ended_turn"`` — AC8's flag — and it
    is stored rather than derived so a reader of a `RunOutcome` never has to
    know that rule to answer the question.
    """

    over_budget: bool
    stopped: str
    usage: dict
    transcript: list


# ------------------------------------------------------------ the refusal

def require_agent(api_key, client_factory) -> None:
    """Refuse a run that has neither a key nor an injected client.

    Without this, `run_task` would hand ``api_key="bench"`` to a real
    `AsyncAnthropic` and a mis-configured CI job would discover the problem as
    a 401 **after** spawning a kernel — or, worse, a test would reach the
    network. `ChatEngine.available` is already ``False`` without a key
    (`chat.py:164`); this is the same rule, raised early and with the fix in
    the sentence.
    """
    if client_factory is None and not api_key:
        raise ValidationError(
            "the built-in agent needs an Anthropic API key: set "
            "ANTHROPIC_API_KEY and run again",
            {"fix": "export ANTHROPIC_API_KEY=…, or drive AgentCAD from "
                    "Claude Code via 'agentcad mcp' and score the result with "
                    "'agentcad bench score'"})


# ------------------------------------------------------------- the run

def _refuse_outside_cell(service, cell) -> None:
    """Refuse a service whose projects root is not inside *cell*.

    §8.1's promise — *"the user's projects dir is never involved"* — is worth
    more as a **check** than as a sentence. `run_task` creates a project,
    hands an agent the ordinary write tools and lets it run arbitrary part
    scripts; the one thing that keeps that off a person's work is where the
    service is rooted, and the caller is the only one who knows. So the caller
    says it twice, in the *cell* it will delete and in the *service* it built,
    and a disagreement is refused before anything is created rather than
    discovered afterwards in someone's projects directory.
    """
    cell = Path(cell).resolve()
    projects_root = Path(service.store.root).resolve()
    if projects_root != cell and cell not in projects_root.parents:
        raise ValidationError(
            f"a bench run writes through a service rooted inside its own "
            f"throwaway cell; {projects_root} is not inside {cell}",
            {"cell": str(cell), "projects_root": str(projects_root)})


def _prepare_project(task: Task, service) -> str:
    """The scratch project for *task*, created or copied in. Returns its name.

    A task with no ``starter`` gets `service.create_project` (`service.py:202`);
    one with a starter gets the starter copied to ``<projects>/<target>`` and
    `service.open_project` (`service.py:207`), which reads the name out of the
    copied manifest. The **manifest's** name is what the tools then address,
    and it is the name returned here — never a name this module invents,
    because a starter that calls itself something else is the starter's claim
    to make.
    """
    projects_root = Path(service.store.root)
    if task.starter_dir is None:
        service.create_project(task.target_project)
        return task.target_project
    tree = projects_root / task.target_project
    shutil.copytree(task.starter_dir, tree,
                    ignore=shutil.ignore_patterns(*COPY_IGNORE))
    return service.open_project(str(tree))["name"]


class _RunBus:
    """The bus a run's `ChatEngine` publishes onto: a recorder, not a fan-out.

    `EventBus.subscribe` hands back a **256-deep queue that drops when full**
    (`service.py:100-104`), and a runaway turn publishes three events per tool
    call — so a subscriber is exactly the wrong instrument for "did this turn
    die of something other than a budget?". `ChatEngine` only ever calls
    ``bus.publish``, so a four-line recorder answers it exactly, keeps a run's
    chat chatter out of the service bus (whose `on_publish` hook snapshots
    history), and stays honest when 200 events arrive.
    """

    #: The prefix `ChatEngine`'s blanket handler puts on its one delta
    #: (`chat.py:328-335`). A turn that ends with one of these ended badly.
    ERROR_PREFIX = "[chat error] "

    def __init__(self) -> None:
        self.on_publish = None
        self.errors: list = []

    def publish(self, event: dict) -> None:
        if event.get("type") != "chat_delta":
            return
        text = event.get("text")
        if isinstance(text, str) and text.startswith(self.ERROR_PREFIX):
            self.errors.append(text[len(self.ERROR_PREFIX):])


def run_task(task: Task, *, service, registry, cell, model,
             api_key: str | None = None, client_factory=None,
             quiet: bool = False) -> RunOutcome:
    """Drive one task through one `ChatEngine` turn and report what happened.

    *service* and *registry* are the surface the agent acts through — the
    ordinary product tools, over a projects root inside *cell* — and *cell* is
    the throwaway directory the caller created and will remove. Nothing here
    touches the user's projects dir, by construction rather than by care.

    The turn is bounded twice: the client refuses the next **API** call once a
    ceiling is spent, and an outer `wait_for` is the backstop for a **tool**
    already in flight. Both end with a scoreable directory; neither raises.
    """
    from ..agent.chat import MAX_TOOL_CALLS_PER_TURN, ChatEngine

    require_agent(api_key, client_factory)
    _refuse_outside_cell(service, cell)
    project = _prepare_project(task, service)

    started = time.monotonic()
    deadline = started + float(task.budgets.wall_s)
    tracker: dict = {}
    bus = _RunBus()

    def inner_factory():
        # `ChatEngine`'s own default when nothing is injected, so the real
        # client stays defined in exactly one place (`chat.py:167-170`) and a
        # change there reaches the bench without a second edit. `engine` is
        # bound by the time this runs — the factory is called inside the turn.
        return (client_factory() if client_factory is not None
                else engine._default_client_factory())

    budgeted = budgeted_client_factory(
        inner_factory, deadline=deadline, max_tool_calls=task.budgets.turns,
        max_api_turns=task.budgets.api_turns)

    def factory():
        client = budgeted()
        tracker["client"] = client
        return client

    engine = ChatEngine(registry, bus, model=model,
                        api_key=api_key or "bench-injected-client",
                        client_factory=factory)

    async def drive() -> None:
        turn = await engine.start_turn(project, prompt_text(task), SESSION)
        # Captured before the first await after `start_turn`: the task's own
        # done-callback pops it out of `_tasks` (`chat.py:203`).
        task_handle = engine._tasks[turn["turn_id"]]
        await asyncio.wait_for(task_handle,
                               timeout=float(task.budgets.wall_s) + WALL_GRACE_S)

    stopped = "model_ended_turn"
    try:
        asyncio.run(drive())
    except (asyncio.TimeoutError, TimeoutError):
        stopped = "wall_clock"
    except Exception as exc:  # noqa: BLE001 — a run never raises; it reports.
        if not quiet:
            print(f"bench: {task.id} ended with {type(exc).__name__}: {exc}",
                  file=sys.stderr)
        stopped = "error"

    transcript = engine.history(project, SESSION)
    # `wait_for` cancels the turn with a `CancelledError`, which is a
    # `BaseException` on 3.12 and so slips past `chat.py`'s `except Exception`:
    # on that path `_repair_history` never ran and the history can end on an
    # assistant `tool_use` with no `tool_result` — a transcript the Messages
    # API would reject, in the one artefact that exists to be read and replayed.
    # `history()` hands back a copy, so this repairs what we are about to write
    # and leaves the engine's own state alone; it is idempotent (it returns
    # immediately unless the last message is a dangling assistant turn), which
    # is why it is not conditioned on `stopped`. A deliberate use of a private
    # staticmethod: re-implementing its ten lines would be a second definition
    # of "valid transcript", and `chat.py` may not be edited.
    ChatEngine._repair_history(transcript)
    # The **history's** count, not the client's: the client counts the blocks
    # in the request it is about to send, so the last round's calls are missing
    # from it whenever no further request follows (a clean end of turn, or the
    # engine's own 30-call break).
    tool_calls = _count_tool_uses(transcript)
    client = tracker.get("client")
    if stopped == "model_ended_turn":
        if client is not None and client.stopped:
            stopped = client.stopped
        elif bus.errors:
            stopped = "error"
        elif tool_calls >= min(task.budgets.turns, MAX_TOOL_CALLS_PER_TURN):
            # Only reachable when the task's own budget IS the engine's limit:
            # below it the client refuses first. The engine broke its loop on
            # `calls >= MAX_TOOL_CALLS_PER_TURN` (`chat.py:306-316`) without a
            # further request, so nothing else would have noticed.
            stopped = "tool_calls"
    return RunOutcome(
        over_budget=stopped != "model_ended_turn",
        stopped=stopped,
        usage={"wall_s": round(time.monotonic() - started, 3),
               "tool_calls": tool_calls,
               "api_turns": getattr(client, "api_turns", 0)},
        transcript=transcript)


# -------------------------------------------------------- the transcript

def _redact(value, needles):
    """Every string in *value*, with each needle replaced by its placeholder."""
    if isinstance(value, dict):
        return {key: _redact(item, needles) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item, needles) for item in value]
    if isinstance(value, str):
        for needle, placeholder in needles:
            value = value.replace(needle, placeholder)
        return value
    return value


def _elide_images(value):
    """Replace every base64 render payload with :data:`IMAGE_PLACEHOLDER`.

    Two shapes, because a render reaches the history in two:
    `_render_tool_result` (`chat.py:101-129`) turns a ``png_base64`` result
    into a real **image block** for the model, and the JSON half of the same
    result travels as **text**. A transcript is for reading, and a 2 MB blob
    in a published artefact is not.
    """
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if key == "png_base64" and isinstance(item, str):
                out[key] = IMAGE_PLACEHOLDER
            elif (key == "data" and isinstance(item, str)
                  and value.get("type") == "base64"):
                out[key] = IMAGE_PLACEHOLDER
            else:
                out[key] = _elide_images(item)
        return out
    if isinstance(value, list):
        return [_elide_images(item) for item in value]
    if isinstance(value, str):
        return _PNG_JSON_RE.sub(f'"png_base64": "{IMAGE_PLACEHOLDER}"', value)
    return value


def transcript_payload(task: Task, messages, *, cell, projects_root) -> dict:
    """``transcript.json``: the turn's messages, path-redacted and image-free.

    `ChatEngine.history` already returns plain JSON-safe dicts (`chat.py:175`),
    so there is no serialisation helper here and none is added. Two transforms
    before writing:

    * **path redaction** — the **cell** first, then the projects root. The
      order is deliberate and not longest-prefix: in a real run the projects
      root sits *inside* the cell, so redacting the cell first answers both
      and yields the one placeholder a reader can locate (``<cell>/projects/…``)
      instead of two that look unrelated. Both the resolved and the unresolved
      spelling of each root is replaced, because `mkdtemp` hands back
      ``/var/…`` where everything downstream says ``/private/var/…``.
    * **image elision** — see :func:`_elide_images`.

    **The task root is deliberately not a needle** (unlike `Scorer.score`'s
    `<task>`, which redacts it). Nothing a run produces can contain it: assets
    reach the model as inlined text named by their path *relative* to the task
    directory (`tasks.prompt_text`), and the agent is never told where the
    bundle lives. What is left to redact is the machine's own scratch — the
    cell and the projects root — and redacting a published task's public path
    on top of that would hide *which* task a transcript belongs to.
    """
    cell = Path(cell)
    projects_root = Path(projects_root)
    needles: list = []
    for path, placeholder in ((cell.resolve(), "<cell>"), (cell, "<cell>"),
                              (projects_root.resolve(), "<projects>"),
                              (projects_root, "<projects>")):
        text = str(path)
        if text and text not in {needle for needle, _ in needles}:
            needles.append((text, placeholder))
    return {
        "schema": TRANSCRIPT_SCHEMA,
        "task": task.id,
        "session": SESSION,
        "project": task.target_project,
        "messages": _redact(_elide_images(list(messages or ())), needles),
    }


# ----------------------------------------------------------- the run doc

def _now() -> str:
    """UTC, ISO-8601, trailing ``Z`` — `checks._now`'s formatter verbatim."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def run_json(task: Task, outcome: RunOutcome, *, agent: str, model: str,
             started: str, finished: str) -> dict:
    """``run.json`` — **where everything non-deterministic lives** (§8.6).

    Timestamps, durations, the model, the agent, the host and the usage
    counters are all here and none of them is in `score.json`, which is what
    lets two runs of the same submission produce byte-identical scores (AC3)
    while the provenance is still on disk beside them.
    """
    usage = dict(outcome.usage or {})
    return {
        "schema": RUN_SCHEMA,
        "task": task.id,
        "task_set": task.task_set,
        "task_version": task.version,
        "category": task.category,
        "agent": agent,
        "model": model,
        "agentcad": agentcad.__version__,
        "harness": HARNESS_VERSION,
        "started": started,
        "finished": finished,
        "duration_s": float(usage.get("wall_s") or 0.0),
        "budgets": {"wall_s": float(task.budgets.wall_s),
                    "turns": int(task.budgets.turns),
                    "api_turns": int(task.budgets.api_turns)},
        "usage": usage,
        "over_budget": bool(outcome.over_budget),
        "stopped": outcome.stopped,
        "host": {"platform": sys.platform,
                 "python": platform.python_version()},
        "transcript": "transcript.json",
    }


__all__ = ["BENCH_SCHEMA", "BudgetExhausted", "BudgetedClient",
           "CLIENT_FACTORY", "IMAGE_PLACEHOLDER", "RUN_SCHEMA", "RunOutcome",
           "SESSION", "STOPPED", "TRANSCRIPT_SCHEMA", "WALL_GRACE_S",
           "budgeted_client_factory", "require_agent", "run_json", "run_task",
           "transcript_payload"]
