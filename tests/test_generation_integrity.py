"""PRD-018 integrity regressions — the four reviewers' exploits, closed.

The generation loop and the accept path must NOT trust a candidate's
self-reported specs: the frozen intent contract is re-derived server-side and
re-measured against the candidate's built geometry via the real kernel
``spec_eval``. These tests drive the FakeMessages harness against the REAL
kernel (build123d runs, no network) and re-attack each hole:

* **(a)** the LANDING exploit — a candidate keeps a frozen ``check_that``'s
  name but neuters its predicate (``return True``); it is caught at terminate
  (``spec_green: false``, the frozen violation named) AND cannot land at accept;
* **(b)** the TOCTOU — accept binds to the candidate's IMMUTABLE recorded
  bytes, so a scratch part mutated after termination never lands;
* **(c)** measurement-sabotage is fail-closed — a candidate whose ``build()``
  wipes the server's injected measurement probes (so nothing can be measured)
  is NOT green;
* **(d)** no false positive — a genuinely-compliant candidate still terminates
  green and accepts cleanly;
* **(e)** the frozen gate is un-forgeable — a candidate that monkeypatches
  ``agentcad.toolkit.specs`` in-worker cannot weaken it (the server owns the
  verdict; the probe ``measured`` is kernel-computed from the real shape).
"""

from __future__ import annotations

import pytest

from agentcad.agent.generate import evaluate_frozen_specs
from agentcad.agent.intent import (
    frozen_needs_wall,
    frozen_specs,
    frozen_verdict,
    normalize_intent,
)
from agentcad.core import tools_generate
from agentcad.core.tools import build_registry

from .conftest import make_test_service
from .test_tools_generate import (
    FakeAnthropic,
    _create,
    _response,
    _text,
)

PROJECT = "genproj"

# A NEMA 17 mount needs a >=31 mm bolt-square footprint (grounded from the
# shipped table). This candidate is a 10 mm cube — far too small — but it keeps
# the frozen check's NAME and neuters its predicate to `return True`, so its own
# run_specs is green. Only a server-side re-measurement catches it.
NEUTERED_NEMA = '''\
from build123d import Box
from agentcad.toolkit.specs import check_valid, check_that

PARAMS = {}
SPECS = [
    check_valid(name="valid"),
    check_that(lambda part, metrics: True,
               name="motor_face_mount_covers_bolt_square"),
]

def build(p):
    return Box(10, 10, 10)
'''

# A real NEMA 17 mount: a 42 mm plate spans the 31 mm bolt square.
GOOD_NEMA = '''\
from build123d import Box
from agentcad.toolkit.specs import check_valid

PARAMS = {}
SPECS = [check_valid(name="valid")]

def build(p):
    return Box(42, 42, 5)
'''

# A candidate whose own specs are green, but whose build() WIPES the server's
# injected measurement probes the moment it sees them — measurement sabotage.
# Its own run_specs (no probes) stays green, so the frozen re-check runs; the
# probe eval then finds nothing to measure and must fail-closed.
CONDITIONAL_WIPE_NEMA = '''\
from build123d import Box
from agentcad.toolkit.specs import check_valid, check_that

PARAMS = {}
SPECS = [
    check_valid(name="valid"),
    check_that(lambda part, metrics: True,
               name="motor_face_mount_covers_bolt_square"),
]

def build(p):
    _s = globals().get("SPECS") or []
    if any(isinstance(x, dict)
           and str(x.get("name", "")).startswith("__fzprobe") for x in _s):
        globals()["SPECS"] = []          # wipe ONLY the server's probe eval
    return Box(10, 10, 10)
'''

# A candidate that POISONS agentcad.toolkit.specs in the worker before it
# declares — an attempt to weaken the frozen gate. It cannot: the server's
# probes are literal dicts and the verdict is computed server-side. The
# geometry is still a 10 mm cube for a NEMA 17 mount.
POISON_NEMA = '''\
import agentcad.toolkit.specs as _s
_neutered = {"spec": 1, "kind": "valid", "scope": "part", "name": "x",
             "limit": {}, "requirement": None, "options": {}}
_s.check_that = lambda *a, **k: dict(_neutered)
_s.check_bbox = lambda *a, **k: dict(_neutered)
_s.declaration_problem = lambda v: None
from build123d import Box
from agentcad.toolkit.specs import check_valid

PARAMS = {}
SPECS = [check_valid(name="valid")]

def build(p):
    return Box(10, 10, 10)
'''


def _writes_then_ends(script):
    """A CLIENT_FACTORY whose model writes *script* once, then ends its turn —
    for a candidate that is green on its own specs but fails the frozen contract
    (the loop does not terminate green, so a second, terminating turn follows)."""
    def factory():
        return FakeAnthropic([
            _response([_create(script)]),
            _response([_text("stopping")], stop_reason="end_turn"),
        ])
    return factory


def _factory(script):
    """A CLIENT_FACTORY: a fresh fake that writes *script* once and lets the
    loop's mechanical measure + frozen re-check terminate it."""
    def factory():
        return FakeAnthropic([_response([_create(script)])])
    return factory


def _use_fake(monkeypatch, factory):
    monkeypatch.setattr(tools_generate, "CLIENT_FACTORY", factory)


@pytest.fixture()
def genstack(tmp_path, kernel, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    service = make_test_service(tmp_path / "projects", kernel)
    registry = build_registry(service)
    assert "error" not in registry.call("create_project", {"name": PROJECT})
    return service, registry


# ============================================ (a) the LANDING exploit (Blocker 1)

def test_neutered_check_that_is_caught_at_terminate_and_at_accept(genstack,
                                                                  monkeypatch):
    service, registry = genstack
    _use_fake(monkeypatch, _writes_then_ends(NEUTERED_NEMA))

    result = registry.call("generate_part",
                           {"project": PROJECT, "prompt": "Mount for a NEMA 17"})
    cand = result["candidates"][0]

    # Its own specs pass, but the server re-measured the frozen bolt-square
    # predicate against the real 10 mm cube: it is NOT spec_green.
    assert cand["spec_green"] is False
    assert cand["frozen_ok"] is False
    assert any("covers_bolt_square" in v for v in cand["frozen_violations"]), \
        cand["frozen_violations"]

    # And it cannot be laundered into a green landing at accept.
    accepted = registry.call("accept_candidate",
                             {"project": PROJECT,
                              "generation_id": result["generation_id"],
                              "candidate": 0, "part_id": "mount"})
    assert accepted["error"]["type"] == "validation_error"
    assert accepted["error"]["details"]["frozen_violations"]
    assert not any(e["id"] == "mount"
                   for e in service.store.manifest(PROJECT)["parts"])


# ================================================ (b) the TOCTOU (Blocker 2)

def test_accept_binds_to_recorded_bytes_not_the_mutated_scratch(genstack,
                                                                monkeypatch):
    service, registry = genstack
    _use_fake(monkeypatch, _factory(GOOD_NEMA))

    result = registry.call("generate_part",
                           {"project": PROJECT, "prompt": "Mount for a NEMA 17"})
    cand = result["candidates"][0]
    assert cand["spec_green"] is True and cand["frozen_ok"] is True
    scratch = cand["scratch_id"]

    # A user mutates the LIVE scratch part to spec-red geometry (a 10 mm cube)
    # AFTER the candidate settled — the classic TOCTOU move.
    mutated = registry.call("update_part_script",
                            {"project": PROJECT, "part_id": scratch,
                             "script": NEUTERED_NEMA})
    assert "error" not in mutated, mutated

    # Accept binds to the RECORDED bytes, re-measures them, and lands THOSE —
    # the mutation has no effect.
    accepted = registry.call("accept_candidate",
                             {"project": PROJECT,
                              "generation_id": result["generation_id"],
                              "candidate": 0, "part_id": "mount"})
    assert "error" not in accepted, accepted
    landed = service.get_part(PROJECT, "mount")
    assert landed["script"] == GOOD_NEMA           # the good recorded bytes
    assert landed["generated"]["spec_green"] is True
    # The landed geometry really spans the 31 mm bolt square.
    box = service.get_metrics(PROJECT, "mount")["bbox"]
    assert box["max"][0] - box["min"][0] >= 31.0


def test_accept_refuses_when_recorded_bytes_are_frozen_red(genstack, monkeypatch):
    """The other half of the TOCTOU ruling (FR8): if the recorded bytes
    themselves are frozen-red, accept refuses rather than landing green."""
    service, registry = genstack
    _use_fake(monkeypatch, _writes_then_ends(NEUTERED_NEMA))
    result = registry.call("generate_part",
                           {"project": PROJECT, "prompt": "Mount for a NEMA 17"})
    accepted = registry.call("accept_candidate",
                             {"project": PROJECT,
                              "generation_id": result["generation_id"],
                              "candidate": 0, "part_id": "mount"})
    assert accepted["error"]["type"] == "validation_error"


# ================================ (c) measurement sabotage is fail-closed (B3)

def test_measurement_sabotage_is_fail_closed(genstack, monkeypatch):
    """A candidate cannot buy green by DENYING the server a measurement. This
    one is green on its own specs (so the frozen re-check runs) but its build()
    wipes the injected probes the instant it sees them — so the geometry cannot
    be measured. A missing measurement is fail-closed, never a pass (the
    skip-is-not-a-pass rule, at the measurement layer)."""
    service, registry = genstack
    _use_fake(monkeypatch, _writes_then_ends(CONDITIONAL_WIPE_NEMA))

    result = registry.call("generate_part",
                           {"project": PROJECT, "prompt": "Mount for a NEMA 17"})
    cand = result["candidates"][0]
    assert cand["spec_green"] is False
    assert cand["frozen_ok"] is False
    assert cand["frozen_violations"]        # named: the measurement was denied

    accepted = registry.call("accept_candidate",
                             {"project": PROJECT,
                              "generation_id": result["generation_id"],
                              "candidate": 0, "part_id": "mount"})
    assert accepted["error"]["type"] == "validation_error"


def test_verdict_fail_closes_on_a_missing_measurement_unit():
    """The unit under (c): with the NEMA bolt-square frozen, a measurement dict
    that carries no geometry (an errored/empty ``frozen_measure`` result) is a
    violation, not a silent pass."""
    intent = normalize_intent("Mount for a NEMA 17")
    assert frozen_specs(intent)              # there IS something to measure
    verdict = frozen_verdict(intent, {})     # …but no metric came back
    assert verdict["frozen_ok"] is False
    assert verdict["frozen_violations"]


# =================================================== (d) no false positive

def test_a_genuinely_compliant_candidate_terminates_and_accepts_green(genstack,
                                                                     monkeypatch):
    service, registry = genstack
    _use_fake(monkeypatch, _factory(GOOD_NEMA))

    result = registry.call("generate_part",
                           {"project": PROJECT, "prompt": "Mount for a NEMA 17"})
    cand = result["candidates"][0]
    assert cand["terminal_state"] == "spec_green"
    assert cand["spec_green"] is True
    assert cand["frozen_ok"] is True
    assert cand["frozen_violations"] == []
    assert cand["content_sha256"] and len(cand["content_sha256"]) == 64

    accepted = registry.call("accept_candidate",
                             {"project": PROJECT,
                              "generation_id": result["generation_id"],
                              "candidate": 0, "part_id": "mount"})
    assert "error" not in accepted, accepted
    assert service.get_part(PROJECT, "mount")["generated"]["spec_green"] is True


# ============================= (e) the frozen gate is un-forgeable

def test_in_worker_module_poisoning_cannot_weaken_the_frozen_gate():
    """A candidate that monkeypatches ``agentcad.toolkit.specs`` (the
    constructors AND ``declaration_problem``) before it declares cannot weaken
    the frozen gate: ``frozen_measure`` never reads the candidate's SPECS at all
    — it builds the UNMODIFIED script and reports kernel-computed metrics — and
    the verdict is evaluated server-side. So the 10 mm cube is still measured
    against the 31 mm bolt square and still caught.

    Runs on a DEDICATED, throwaway kernel: the poisoning mutates worker module
    state (a latent worker-namespace isolation gap that is wave-2's sandbox
    domain, not this integrity fix), so it must not touch the shared session
    worker other suites build against.
    """
    from agentcad.kernel.client import KernelClient

    intent = normalize_intent("Mount for a NEMA 17")
    specs = frozen_specs(intent)
    assert specs, "the NEMA 17 prompt must produce a frozen spec to measure"

    worker = KernelClient()
    worker.start()
    try:
        out = worker.request(
            "frozen_measure",
            {"script": POISON_NEMA, "params": {}, "density_g_cm3": 2.7,
             "densities": None, "need_wall": frozen_needs_wall(specs)},
            timeout_s=60.0)
    finally:
        worker.stop()

    verdict = frozen_verdict(intent, out)
    assert verdict["frozen_ok"] is False
    assert any("covers_bolt_square" in v for v in verdict["frozen_violations"])


# ==== (f) the WAVE-3 exploit: build() branches on globals()["SPECS"] ====

# The exploit the adversarial verifier PROVED: an earlier frozen design appended
# a probe ``SPECS`` block to the candidate script before building, so build()
# could read ``globals()["SPECS"]``, notice the ``__fzprobe`` probe names, and
# return COMPLIANT geometry *while being measured* — while returning its real,
# frozen-violating geometry (a 200 mm cube for a 60x40x20 envelope, or here a
# 10 mm cube for a >=31 mm NEMA bolt square) in normal use. The fix measures the
# UNMODIFIED recorded bytes, so build() sees no probe and has no signal to
# branch on: its real (violating) geometry is what gets measured.
PROBE_DETECTING_NEMA = '''\
from build123d import Box
from agentcad.toolkit.specs import check_valid, check_that

PARAMS = {}
SPECS = [
    check_valid(name="valid"),
    check_that(lambda part, metrics: True,
               name="motor_face_mount_covers_bolt_square"),
]

def build(p):
    _s = globals().get("SPECS") or []
    probed = any(isinstance(x, dict)
                 and str(x.get("name", "")).startswith("__fzprobe")
                 for x in _s)
    if probed:
        return Box(42, 42, 5)     # compliant ONLY while a probe is observed
    return Box(10, 10, 10)        # the real, frozen-violating geometry
'''


class _FakeService:
    """Minimal service surface ``evaluate_frozen_specs`` uses: a kernel and a
    material-density lookup. Lets the exploit run on a DEDICATED worker."""

    def __init__(self, kernel):
        self.kernel = kernel

    def material_density(self, project, material):
        return 2.7


def test_probe_detecting_build_is_caught_because_the_recorded_bytes_are_measured():
    """The wave-3 merge gate. A candidate whose build() serves compliant
    geometry only when it detects the server's probe is caught: the frozen path
    measures the UNMODIFIED recorded bytes, build() sees no probe, and its real
    10 mm cube is measured against the 31 mm NEMA bolt square -> frozen_ok False.
    A genuinely-compliant candidate is NOT a false positive.

    Runs on a DEDICATED KernelClient (a fresh worker), never the shared session
    fixture.
    """
    from agentcad.kernel.client import KernelClient

    intent = normalize_intent("Mount for a NEMA 17")
    worker = KernelClient()
    worker.start()
    try:
        service = _FakeService(worker)

        # The exploit: build() would forge a pass under the old probe scheme.
        verdict = evaluate_frozen_specs(
            service, PROJECT, PROBE_DETECTING_NEMA, {}, "al6061",
            intent.to_dict(), affinity="exploit")
        assert verdict["frozen_ok"] is False, \
            "the real 10 mm cube must be measured, not the probe-time 42 mm plate"
        assert any("covers_bolt_square" in v
                   for v in verdict["frozen_violations"]), \
            verdict["frozen_violations"]

        # No false positive: a genuinely-compliant 42 mm plate still passes.
        good = evaluate_frozen_specs(
            service, PROJECT, GOOD_NEMA, {}, "al6061",
            intent.to_dict(), affinity="good")
        assert good["frozen_ok"] is True, good["frozen_violations"]
    finally:
        worker.stop()
