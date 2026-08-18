"""PRD-006 slice 2 — request ids, the usage envelope, and what the client
concludes from the worker's report.

The risk (design spec, "Risks"): a part script may `os.fork()`, and the child
inherits fd 1 — the protocol stream. With a counter for ids, a **lingering**
child (or any stale writer) can compute the ids of requests it never saw — a
later build, an export, another part's request — and answer them. Random ids
end that: an id it did not observe is a 62-bit guess.

What they deliberately do NOT close: the running script can still forge the
response to its own in-flight request, since it holds fd 1 and can reach the
id through the interpreter. That is the same trust domain as `build()` simply
returning a fake shape, and no fd redesign is warranted for it.

The same door carries the meter (Decision 6): every response line, result and
error alike, arrives with a `usage` object, and the client keeps the last one.
"""

from __future__ import annotations

import sys

import pytest

from agentcad.kernel.client import KernelError, confinement_holds

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


# ------------------------------- what the client concludes from the report

def test_a_refused_rlimit_does_not_clear_the_confinement_claim():
    """A quota that did not apply is a quota problem. It belongs in the report
    and in health's warnings, but Landlock and seccomp are still in force, and
    saying `off` there understates the confinement as badly as overstating it
    would overstate it."""
    report = {"posture": "local", "rlimits": [], "landlock_abi": 6,
              "seccomp": "seccomp(2)",
              "failures": [{"stage": "rlimits", "error": "not applied: RLIMIT_AS"}]}
    assert confinement_holds(report) is True


@pytest.mark.parametrize("stage", ["landlock", "seccomp"])
def test_a_failed_confinement_stage_clears_the_claim(stage):
    report = {"posture": "local", "rlimits": ["RLIMIT_AS"], "landlock_abi": 6,
              "seccomp": "seccomp(2)",
              "failures": [{"stage": stage, "error": "EPERM"}]}
    assert confinement_holds(report) is False


def test_on_linux_a_missing_landlock_abi_clears_the_claim():
    """Linux confinement IS Landlock plus seccomp; with no ABI there is no
    claim to make, however clean the rest of the report looks."""
    report = {"posture": "local", "rlimits": ["RLIMIT_AS"],
              "landlock_abi": None, "seccomp": "seccomp(2)", "failures": []}
    assert confinement_holds(report) is (sys.platform != "linux")
    assert confinement_holds({"landlock_abi": 6, "seccomp": "seccomp(2)",
                              "failures": []}) is True


def test_a_preamble_that_applied_nothing_classifies_no_denials(monkeypatch,
                                                                tmp_path):
    """`details.denied` keys on what the preamble ACTUALLY applied, not on the
    variable merely being present: a payload whose every stage was a no-op
    leaves a non-empty report, and an ordinary `PermissionError` under it is
    still an ordinary script bug."""
    from agentcad.kernel.client import KernelClient

    monkeypatch.setenv("AGENTCAD_CONFINE", '{"posture": "local", "rlimits": {}}')
    client = KernelClient()          # no plan: the env is inherited as-is
    client.start()
    try:
        assert client.sandbox_report == {"posture": "local", "rlimits": [],
                                         "landlock_abi": None, "seccomp": None,
                                         "failures": []}
        script = ("PARAMS = {}\n"
                  "def build(p):\n"
                  "    raise PermissionError('[Errno 13] Permission denied')\n")
        with pytest.raises(KernelError) as exc_info:
            client.request("build", {"script": script, "params": {},
                                     "mesh_path": str(tmp_path / "x.acm")})
        assert exc_info.value.type == "script_error"
        assert "denied" not in exc_info.value.details
    finally:
        client.stop()
