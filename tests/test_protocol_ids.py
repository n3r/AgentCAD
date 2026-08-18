"""PRD-006 slice 2 — unguessable request ids, and the usage envelope.

The risk (design spec, "Risks"): a part script may `os.fork()`, and the child
inherits fd 1 — the protocol stream. With a counter for ids, the child knows
the next id and can write a JSON line the client accepts as *the result of a
request the worker has not answered yet*. Confinement does not touch this: the
child is allowed to write to its own stdout. The fix is that the id stops
being predictable — `secrets.randbits(62)` per request, echoed unchanged by
the worker — so a forger has to guess a 62-bit token inside one request.

The same door carries the meter (Decision 6): every response line, result and
error alike, arrives with a `usage` object, and the client keeps the last one.
"""

from __future__ import annotations

import pytest

from agentcad.kernel.client import KernelError

pytestmark = pytest.mark.integration

#: `secrets.randbits(62)` lands below 2**40 with probability 2**-22 — small
#: enough that a run below it is a bug in the generator, not bad luck.
FLOOR = 2 ** 40


def test_request_ids_are_random_not_a_counter(kernel):
    kernel.request("ping", {})
    first = kernel._last_req_id
    kernel.request("ping", {})
    second = kernel._last_req_id

    assert first >= FLOOR and second >= FLOOR
    assert first != second
    assert abs(second - first) > 1        # not the counter it used to be
    assert second < 2 ** 62


def test_the_worker_echoes_the_id_it_was_given(kernel):
    """Matching is by exact id, so a worker that renumbered would deadlock —
    and a client that ignored the id would accept a forged line."""
    result = kernel.request("ping", {})
    assert result["ok"] is True
    assert kernel._last_req_id >= FLOOR


def test_every_response_carries_usage(kernel):
    kernel.request("ping", {})
    usage = kernel.last_usage
    assert set(usage) == {"cpu_ms", "wall_ms", "peak_rss_mb", "rss_mb",
                          "peak_rss_is_lifetime"}
    assert usage["wall_ms"] > 0
    assert usage["cpu_ms"] >= 0


def test_an_error_response_carries_usage_too(kernel, tmp_path):
    """A failed build is the case where a caller most wants to know what the
    attempt cost, so `usage` rides the error line as well as the result one."""
    before = dict(kernel.last_usage or {})
    with pytest.raises(KernelError) as exc_info:
        kernel.request("build", {"script": "raise ValueError('boom')",
                                 "params": {},
                                 "mesh_path": str(tmp_path / "x.acm")})
    assert exc_info.value.type == "script_error"
    assert kernel.last_usage and kernel.last_usage != before


def test_an_unsandboxed_client_reports_an_empty_sandbox_and_no_denials(kernel,
                                                                       tmp_path):
    """`KernelClient()` with no writable dirs and no quotas is the historical
    client: no plan, no `AGENTCAD_CONFINE`, so the preamble is a no-op and the
    worker says so rather than inventing a posture. And with nothing applied,
    a `PermissionError` in a script is NOT labelled a sandbox denial."""
    assert kernel.sandbox_report == {}
    assert kernel.sandboxed is False

    script = ("PARAMS = {}\n"
              "def build(p):\n"
              "    raise PermissionError('[Errno 13] Permission denied: /nope')\n")
    with pytest.raises(KernelError) as exc_info:
        kernel.request("build", {"script": script, "params": {},
                                 "mesh_path": str(tmp_path / "x.acm")})
    assert exc_info.value.type == "script_error"
    assert "denied" not in exc_info.value.details
