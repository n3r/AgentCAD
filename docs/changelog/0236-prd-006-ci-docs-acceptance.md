# 0236 — PRD-006 slice 5: the CI honesty gate and the Landlock probe, the image and compose posture, the docs rewritten around what actually ships, the acceptance file, and the 006b carve-out

- **Commit:** pending
- **Date:** 2026-08-18
- **Author:** Claude

## Summary

The last slice of PRD-006, and the one that makes the previous four legible.
Across `0230`–`0236` (`0213`–`0219` on the branch) this branch turned "only macOS confines part scripts" into
a per-OS contract that is measured rather than claimed: the **Linux worker now
confines itself** with a Landlock ruleset and a seccomp filter applied through
`ctypes` before `import build123d` — no capability, no `bwrap`, verified as uid
10001 inside the shipped image at 0.3 ms — with a **private per-worker temp
dir** closing the `/tmp` cross-worker leak and a **`hosted` read posture** that
puts the session signing key out of a member's reach; quotas became honest
**tiers** (a delegated cgroup v2 subtree, POSIX rlimits where they are real,
Windows job objects, and a parent-side RSS supervisor everywhere, because
`setrlimit(RLIMIT_AS)` is `EINVAL` on Darwin) with the tier in force *named*
instead of a mechanism promised; every response gained a `usage` envelope that
a service-owned meter rolls up per project and per identity behind
`/api/health` and `get_usage`; and per-project disk budgets refuse a write
before the worker makes it. This slice adds the CI that keeps it true (a
Landlock probe step and `AGENTCAD_EXPECT_SANDBOX`/`_QUOTAS`, so a degradation
is **red rather than skipped**), the container posture (`pids_limit`, a sized
`mem_limit`, the commented Model-2 cgroup delegation recipe), the acceptance
file for AC1–AC8, the **PRD-006b** carve-out for Windows AppContainer — and a
documentation pass that rewrites the trust statement in every place it appears,
including the parts of it that got *weaker*: a member's script still runs as
the server user with the whole projects tree readable and writable, so
"an account is a shell" is no longer literally true on Linux, and accounts are
still only for people you trust.

## Changes

### CI — the probe and the honesty gate (design spec, Decision 13)

- **`.github/workflows/ci.yml`** gains a `Sandbox probe (Linux)` step, after
  `uv sync` and before either suite, printing `uname -r`,
  `/sys/kernel/security/lsm` and `_confine.landlock_abi()`. Landlock is a
  boot-time LSM: compiled in is not enough, it must be in the `lsm=` list, and
  the ABI decides whether the worker confines itself at all. The probe is what
  makes a runner-image change diagnosable in one red run rather than a bisect.
- The matrix gains **`expect_sandbox`** — `active` on macos-latest and
  ubuntu-latest, empty on windows-latest (AppContainer is 006b; `unsupported`
  is the honest answer there) — passed to both suites as
  `AGENTCAD_EXPECT_SANDBOX`, with `AGENTCAD_EXPECT_QUOTAS: active` on all
  three. Every containment test asserts *when the live status is active*, so
  without these the suite would be green on a runner that confines nothing.
  **If the runner kernel lacks Landlock in its `lsm=` list, the ubuntu job now
  goes RED by design** — and the probe step's output says why.
- Nothing else in the matrix moved: same OSes, same suites, same timeouts, same
  compose lint.

### The image and the compose posture

- **`Dockerfile`** header: the trust comment now says what the image actually
  does (self-confinement, no capability needed, what is denied) and what it
  still does not (cross-project reach as uid 10001).
- **`compose.yaml`** header rewritten the same way, keeping FR17's sentence
  verbatim and adding the residual after it. New: **`pids_limit: 512`** as the
  container-wide blast radius (generous on purpose — a warm worker runs 15–22
  threads and `RLIMIT_NPROC` is sized from the live uid task count, so a low
  number kills a worker mid-import rather than stopping a fork bomb), a
  commented **`mem_limit`** with the sizing rule, commented `AGENTCAD_QUOTA_*`
  examples, and the **Model-2 cgroup delegation** recipe as three commented
  lines (`cgroup_parent: /agentcad`, the `/sys/fs/cgroup/agentcad:/cg:rw` bind
  mount, `AGENTCAD_CGROUP_DIR: /cg`) beside the host `mkdir`/`subtree_control`/
  `chown` steps. `docker compose config --quiet` is clean; the health
  assertion `deploy-smoke.yml` makes is untouched.

### Documentation

- **`docs/deployment.md`** — the top blockquote is rewritten to state the
  improvement *and* the residual in that order; the env table gains
  `AGENTCAD_QUOTA_<KNOB>` and `AGENTCAD_CGROUP_DIR` and corrects
  `AGENTCAD_NO_SANDBOX` (it opts out of confinement, **not** of the caps);
  Sizing gains the full defaults table plus the rule that the host must be
  sized *above* the cap because the supervisor kills one interval late
  (380–620 MB of overshoot measured at ~4 GB/s); and a new **"Confinement and
  quotas"** section replaces "What PRD-006 will change" with the per-OS
  confinement table, the tier table, the Landlock requirements, the delegated
  cgroup recipe and its `auto` variant's refusals, the live `/api/health`
  payload, and what a breach looks like to a user or an agent.
- **`docs/architecture.md`** — the Trust model gains the **per-OS contract
  table** (confinement mechanism and quota tiers per platform) and the posture
  model; the hosted-mode paragraph stops saying the seatbelt is macOS-only and
  says instead what confinement does and does not separate; the geometry-CI
  trust note at the `--ref` section and the packages backstop sentence are
  corrected the same way; the residuals list names cross-project reach first.
- **`AGENTS.md`** — a new **"Sandboxing & quotas gotchas (PRD-006)"** section
  (no `preexec_fn`; never grant bare `/tmp` and the two different work roots;
  `plan()` must not create the roots it is handed; the `seccomp` op constant is
  1; the low-word signal rule; the probed ABI mask and `TRUNCATE`;
  `clear_refs`; `ru_maxrss` units; `RLIMIT_NPROC` counts tasks;
  `AGENTCAD_NO_SANDBOX` semantics; `AGENTCAD_CGROUP_DIR`'s three modes and
  `auto`'s refusals; why a cgroup makes the supervisor unfireable in tests;
  `KernelClient()` with no args; measured-not-inferred status;
  `denials.classify` needing a traceback; `details.usage` being the kill
  paths' contract; the `fakeowner` trap and `make test-linux`; the
  `AGENTCAD_EXPECT_*` gate; the acceptance file's evidence check). The
  hosted-core section's opening trust paragraph and the Conventions
  security bullet are rewritten to match.
- **`CLAUDE.md`** — the condensed trap block for the same, and the hosted-core
  block's "an account is a shell until PRD-006" corrected.
- **`docs/agent-api.md`** — a Conventions bullet distinguishing a *killed*
  worker from a script that raised: `kernel_crash`'s `details.reason` /
  `tier` / `limit_mb` / `observed_rss_mb`, `details.usage` on every path that
  ends without an answer (and the `cpu_ms: null` reading), and why a
  worker-reported `script_error` carries none.
- **`docs/user-guide.md`** — one paragraph in the failure loop on what the
  error panel says for a denial, a cap and a disk budget, and that it is the
  same banner on purpose.
- **`docs/geometry-ci.md`**, **`docs/packages.md`** — the two remaining "Linux
  is unconfined" claims corrected, with the honest qualifier that a runner's
  kernel decides whether confinement applies.
- **`docs/roadmap.md`** — the 006 row's link now resolves (`in-progress/`) and
  its status says what shipped and what was carved out; a **006b** row is added
  beside it; the sequencing table's step 5 records that 006 was built out of
  order and why.
- **`docs/superpowers/specs/2026-08-18-sandboxing-quotas-design.md`** — one
  **factual correction in place**: Decision 1's seccomp paragraph now states
  that the negative-pid test is an unsigned `JGE 0x80000000` on the **low**
  word of `args[0]`, because the high word of an `int` argument is unspecified
  on both arches (on arm64 `mov w0, #-1` zero-extends, and `os.kill(-1, 9)`
  escaped a high-word check in the shipped image), and io_uring is added to the
  unconditional denials. A new **"Post-build corrections"** section records the
  four things the build changed about the design: the cgroup own-route is
  opt-in by name (`AGENTCAD_CGROUP_DIR=auto`) and refuses root, `RLIMIT_NPROC`
  counts tasks, random request ids close cross-request forgery only, and
  `details["usage"]` is the kill paths' contract.
- **`docs/prd/in-progress/PRD-006-sandboxing-quotas.md`** — a status header
  recording what shipped, the residual in the same breath, the acceptance
  evidence per platform, and the 006b carve-out. The file **stays in
  `in-progress/`**; the move to `completed/` is the close-out commit on `main`.

### New — `docs/prd/pending/PRD-006b-windows-appcontainer.md`

The carve-out, on the 005a/031a letter-suffix precedent so folder-as-status
stays truthful: **FR2's confinement half and AC3's Windows clause**, nothing
else (Windows *quotas* shipped with 006 and are explicitly out of scope). It
states the reason honestly — AppContainer with CPython and OCCT is the
least-trodden path of the three, cannot be exercised on this box, and each
attempt is a Windows-CI round trip — names the `STARTUPINFEX`/`CreateProcessW`
problem as the spike's first question, and records that the founder can fold it
back.

### New — `tests/test_prd006_acceptance.py`

Portability- and slow-marked, ten tests, AC1–AC8:

- **AC1** — the Linux battery is pinned **by name** (ten tests) from every OS,
  so a deletion is visible from a macOS box, plus the assertion that its skip
  is gated on `AGENTCAD_EXPECT_SANDBOX` rather than on the platform; the live
  half (Linux only) asserts the worker's own report and, under the gate,
  `sandboxed is True`.
- **AC2** — the eight pre-PRD seatbelt tests (`git show
  78efcb4^:tests/test_sandbox.py`) still exist by name and the file has grown,
  not shrunk. A move-and-facade refactor is exactly the shape that loses
  coverage silently.
- **AC3** — `sandbox.report()` on a real confined worker: the shape
  unconditionally, `active` + the per-platform mechanism under
  `AGENTCAD_EXPECT_SANDBOX=active`, `mechanism is None` beside a non-active
  status, and the quota tier list ending in `supervisor` under
  `AGENTCAD_EXPECT_QUOTAS=active` (the first test in the suite to consume that
  variable, which `scripts/linux-test.sh` has been setting since slice 2). The
  Windows clause is its own test, runs on every OS through
  `sandbox_windows.build()`, and asserts `unsupported` + the `PRD-006b` note +
  that the PRD it points at exists.
- **AC4–AC7** — pointers, deliberately: the criteria are graded by
  `tests/test_supervisor.py` (real workers, real allocations) and
  `tests/test_usage.py` (two real builds through the meter), and this asserts
  those tests still exist under the names the criteria were signed off against,
  with the `portability` mark that makes CI run them.
- **AC8** — `AGENTCAD_NO_SANDBOX=1` gives confinement `off` **with a reason**
  and quotas still `active`; a stubbed Landlock-less Linux kernel
  (`sandbox_linux.landlock_abi → 0`, all-OS) gives `unsupported` + a warning
  through both `plan()` and `report()`; the CI matrix carries the probe and the
  gate; and `test_ac8_the_full_suite_count_is_cited` is the evidence check on
  this entry.

### One latent order dependency, found by adding a test module

`tests/conftest.py` gains an autouse `_restore_client_identity` fixture that
snapshots `locks.client_id_var` before every test and resets it after.

`client_id_var` is a ContextVar defaulting to `"local"`, and one set at a
test's top level is **never restored** — it survives for the rest of that
xdist worker's process. Every in-process CLI run leaves one: `cmd_check` and
the two package commands call `locks.set_client_id("ci")` inside `cli.py`,
which is correct for a real `agentcad check` and has nowhere to be undone.
Measured with a `pytest_sessionfinish` probe: `tests/test_checks_cli.py` alone
ended the session at `'ci'`, and so did `tests/test_packages_cli.py` and
`tests/test_prd004_acceptance.py`. (`tests/test_checks_gate.py` calls
`set_client_id` directly but does **not** leak — it was the first suspect and
the wrong one.)

Under `-n auto --dist loadscope`, which of those shares a worker with
`tests/test_usage.py` is a scheduling detail — so adding **one unrelated test
module** (the acceptance file) moved a leaker onto gw2 and turned that file's
three "an unattributed record bills `local`" assertions into `ci`. Those tests
had been order-dependent since they were written.

The fix is in `conftest.py` rather than in `cli.py` because **the CLI's call is
right**: a real `agentcad check` process *is* `ci` from that point on, and
scoping it there would change product behaviour to suit a harness. It is a
snapshot-and-restore rather than a pin, so a test that sets an identity on
purpose still sees it for its own duration (`tests/test_branches.py` switches
between `agent_a`/`agent_b` many times inside one test) and only the escape is
closed — which closes the whole class, not just this one module. Re-measured
with the same probe: `tests/test_checks_cli.py` now ends at `'local'`.

### Review round 1 — five documentation claims the code does not support

Found by the task review; each was a sentence a reader would have acted on.

- **`pids_cap` and `cpu_cap` were documented as live `details.reason` values.
  They are not.** The shipped tiers emit exactly one reason, **`memory_cap`**
  (the supervisor's kill, or a delegated cgroup's OOM counter). A *pids*
  breach never kills the worker: both mechanisms (`RLIMIT_NPROC` and the
  cgroup's `pids.max`) make the script's own `fork()` return `EAGAIN`, so it
  arrives as a `script_error` with `details.denied: "process_count"` — better
  than a kill, and why nothing needs to emit `pids_cap`. `cpu_cap` is emitted
  only for a worker killed by `SIGXCPU`, which needs an `RLIMIT_CPU` AgentCAD
  never sets (it is lifetime-cumulative; the wall-clock timeout is the CPU
  backstop), so the branch exists for an *operator's* own limit. Both are now
  documented as **reserved vocabulary** — kept in the docs so an agent's
  handler can be written once — in `docs/agent-api.md` (the conventions
  bullet and the pre-existing `get_usage` line), `docs/deployment.md` and
  `docs/user-guide.md`. The PRD header records this as a **deliberate
  deviation from FR8**, which asked for a pids *kill*.
- **"Ubuntu ≥ 22.04" contradicted the ABI ≥ 3 floor three lines above it**
  (22.04 GA is kernel 5.15 = Landlock ABI 1). `docs/deployment.md` now names
  the **kernel**: 6.2+, so Ubuntu 24.04 or 22.04 with the HWE kernel, and says
  to check both halves — `/sys/kernel/security/lsm` for the list and the
  probe's ABI for the version, because either alone explains an `off`.
- **"no reads of `HOME`" was wrong.** `sandbox_linux._read_roots` appends the
  **write** roots to the hosted read allow-list, so `~/.agentcad` — a write
  root — is readable; and the worker's own `HOME` is its private temp dir, so
  `~` inside a part script is not the server user's home at all. Corrected in
  the `Dockerfile` and `compose.yaml` headers, `docs/deployment.md`'s
  blockquote and confinement table, `docs/architecture.md`, `AGENTS.md` and
  the PRD-006 header to: *nothing under the server user's home except the
  config dir (`~/.agentcad`, which is a write root)*.
  - Following from it, one caveat that was not documented anywhere: because a
    write root is readable, **`AGENTCAD_STATE_DIR` must not be left at its
    `<config-dir>/state` default on a hosted instance** — it would sit inside
    `~/.agentcad` and be readable by a part script. Compose already sets
    `/data/state`; the env table now says why that is load-bearing rather than
    tidy, and `docs/architecture.md` repeats it.
- **The PRD header and one test comment credited CI runs that have not
  happened.** Both now say the ubuntu/windows evidence is *gated* and lands
  with the first green CI run of the PR; the PRD's Status line names AC8's
  three-OS half as the second open item beside AC3's Windows clause.
- **`tests/test_geometry_ci_action.py`'s "unconfined Linux runner" comment**
  now matches the corrected `docs/geometry-ci.md`: the worker does confine
  itself, the runner's kernel decides whether it can, and a runner that cannot
  reports `off` without failing the check — so confinement is a second line
  there, never the argument for `pull_request`.

Two smaller ones in `tests/test_prd006_acceptance.py`: AC2's docstring claimed
"grown" while the assertion was `>= 8` on a 12-test file (the floor is now
**12**, with the four PRD-006 additions named), and the evidence check no
longer addresses changelog entries by **number** — it finds its own entry by
**slug** and "the newest entry" by the numeric value of whatever prefix it
has, at any width, because this repo renumbers changelogs when two branches
collide at merge (`b24ef66` moved 0188–0197 to 0200–0209).

### Deferred minors from earlier reviews

- `kernel/sandbox_macos.py` — the `ps` fallback in `live_uid_process_count`
  names `encoding="utf-8"` (the house rule for every text I/O).
- `tests/test_sandbox.py` — the `sboxed` fixture docstring said "+ system temp
  dir", which is the grant PRD-006 removed.
- `tests/test_quotas.py` — `pytest.raises(Exception)` on the frozen dataclass
  became `dataclasses.FrozenInstanceError`; a typo in the attribute name would
  have passed the old assertion.
- `core/project.py::trim_cache` — the docstring now records that it reads the
  5 s `disk_usage` memo, so a build landing right after another can decline to
  trim; a bounded under-trigger, never an over-delete, because the stale read
  is always the smaller number.
- `cli.py` — `_TRUST_NOTE` (printed by `agentcad admin user add`) no longer
  says Linux has no confinement; `core/appmode.py`'s module docstring no
  longer rests the mode interlock's argument on "an account is a shell".
- `scripts/linux-test.sh` — `tests/test_prd006_acceptance.py` joins the
  default file list, the way slice 3 added `tests/test_supervisor.py`, so the
  acceptance file's Linux half runs under `AGENTCAD_EXPECT_SANDBOX=active`.

## Files

- `.github/workflows/ci.yml` — the probe step; `expect_sandbox` in the matrix;
  `AGENTCAD_EXPECT_SANDBOX`/`AGENTCAD_EXPECT_QUOTAS` on both suites
- `Dockerfile`, `compose.yaml` — trust headers; `pids_limit`; commented
  `mem_limit`, `AGENTCAD_QUOTA_*` and the Model-2 delegation lines
- `docs/deployment.md` — blockquote, env table rows, Sizing caps table, the new
  "Confinement and quotas" section
- `docs/architecture.md` — per-OS contract table, posture model, hosted-mode
  and packages trust paragraphs, residuals
- `AGENTS.md` — the new gotchas section; hosted-core trust paragraph;
  Conventions security bullet
- `CLAUDE.md` — the condensed trap block; hosted-core trap corrected
- `docs/agent-api.md` — the kill-vs-raise conventions bullet
- `docs/user-guide.md` — the error-panel paragraph
- `docs/geometry-ci.md`, `docs/packages.md` — corrected trust sentences
- `docs/roadmap.md` — 006 row + link, new 006b row, step-5 note
- `docs/superpowers/specs/2026-08-18-sandboxing-quotas-design.md` — the seccomp
  correction in place; "Post-build corrections"
- `docs/prd/in-progress/PRD-006-sandboxing-quotas.md` — status header
- `docs/prd/pending/PRD-006b-windows-appcontainer.md` — new
- `tests/test_prd006_acceptance.py` — new
- `tests/conftest.py` — the autouse `_restore_client_identity` fixture
- `tests/test_geometry_ci_action.py` — the corrected trust comment
- `scripts/linux-test.sh` — the acceptance file in the default list
- `agentcad/kernel/sandbox_macos.py`, `agentcad/core/project.py`,
  `agentcad/cli.py`, `agentcad/core/appmode.py`, `tests/test_sandbox.py`,
  `tests/test_quotas.py` — the deferred minors
- `docs/changelog/0236-prd-006-ci-docs-acceptance.md` — this entry (0219 on the branch)

## Notes

- **Verification.** `make test` — **4170 passed, 33 skipped in 521.73s
  (0:08:41)** (macOS 26.6, arm64, full parallel suite; slice 4 was 4161 passed
  / 32 skipped, and this slice adds the acceptance file's ten tests, one of
  which skips off Linux).
  `make test-linux` in `agentcad:local` — **96 passed, 2 skipped in 98.81s**
  (linuxkit 6.12 aarch64, uid 10001, Landlock ABI 6, with
  `AGENTCAD_EXPECT_SANDBOX=active` set by the script, so nothing degraded into
  a skip); slice 3 was 86/2, and the ten added are the acceptance file, which
  on its own there is **10 passed, 0 skipped**. The two skips are unchanged:
  the delegated-cgroup test (no delegated subtree in the default container)
  and one meter case.
  `docker compose -f compose.yaml config --quiet` is clean and
  `uv run ruff check` passes on every file this entry touches.
- **What the 4170 was measured on.** Review round 1 changed only documentation
  and test files afterwards; the one that reaches the whole suite is
  `conftest.py`'s autouse snapshot/restore, which cannot change a verdict
  except by removing the order dependency it exists to remove. Rather than
  assert that, the modules that set an identity were re-run against it —
  `test_usage` + `test_checks_cli` + `test_branches` (113 passed), and
  `test_checks_gate` + `test_checks_api` + `test_packages_cli` +
  `test_packages_publish` + `test_prd004_acceptance` + `test_prd011_acceptance`
  — plus `test_prd006_acceptance` + `test_geometry_ci_action` (46 passed, 1
  skipped) and `test_deploy_config` + `test_prd005a_acceptance` +
  `test_cli_admin` (85 passed) for the docs and compose edits.
- **The gate is the point, and it cuts both ways.** `AGENTCAD_EXPECT_SANDBOX`
  exists because every containment assertion in this branch is conditional on
  the live status — that is what keeps a developer on an old kernel from seeing
  a red suite for a machine problem. The cost of that kindness is that CI must
  set the variable, or a runner that silently stopped confining would be green.
  It is now set in three places (`ci.yml`'s two suites and
  `scripts/linux-test.sh`) and asserted in a fourth
  (`test_ac8_the_ci_matrix_carries_the_honesty_gate`).
- **What the docs deliberately did NOT claim.** The trust statement got
  *better* on Linux and no better at all between members: one account's script
  reads and writes every project on the instance, because per-project ACLs are
  PRD-005 and the confinement is a process boundary, not a tenancy one. Every
  place the old sentence appeared now says both halves in the same breath
  rather than leaving the improvement to imply the rest.
- **Windows CI expects no confinement on purpose.** `expect_sandbox` is empty
  on windows-latest rather than absent-with-a-comment, so the matrix reads as
  three deliberate answers instead of two answers and an omission. When 006b
  lands it becomes `active` and AC3 closes.
- **`tests/test_prd006_acceptance.py` spawns exactly one worker.** The AC3
  fixture is module-scoped and pings once (the report has to come from the
  worker, not the plan); AC1's live half reuses it. Everything else is either
  source inspection or a `plan()` that is released in a `finally`.
- **One pre-existing lint is untouched:** `ruff` reports an unused
  `get_material` import in `agentcad/core/project.py` on this branch with or
  without these changes. It is not this slice's to fix, and fixing it here
  would put an unrelated hunk in a docs commit.
