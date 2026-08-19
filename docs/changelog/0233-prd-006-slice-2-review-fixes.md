# 0233 — PRD-006 slice 2, review fixes: io_uring closed, writable roots created by their owner, honest denial labels

- **Commit:** pending
- **Date:** 2026-08-18
- **Author:** Nikita Fedorov

## Summary
Task-review follow-ups to `de0cfba` (slice 2). Two are real holes — io_uring
walked straight around the seccomp socket rule, and a writable root that did
not exist yet was silently never granted — and the rest tighten claims that
were broader than the code: the denial classifier called every EPERM a network
denial, a refused rlimit cleared the confinement claim, and the changelog said
random request ids close a forgery hole they only half close.

## Changes

### Holes
- **`kernel/_confine.py`: io_uring is denied outright** —
  `io_uring_setup`/`io_uring_enter`/`io_uring_register` (425/426/427, the same
  numbers on x86_64 and aarch64) join `ptrace`/`process_vm_*`/`pidfd_open` in
  the unconditional `RET_ERRNO(EPERM)` list. io_uring is a *submission queue*:
  a script can ask the kernel to open and use a socket from a ring entry, and
  the only syscall the filter would ever see is `io_uring_enter`. seccomp
  cannot inspect ring entries, so the interface has to go or the AF_UNIX rule
  is decorative. Nothing in CPython, numpy, OCP or build123d uses it.
- **`cli._writable_roots` creates the two roots the server owns** (the projects
  dir and `~/.agentcad`). On a fresh install neither need exist — the service
  creates the projects dir *after* `kernel.start()` — and on Linux a Landlock
  rule on a missing path is ENOENT, which costs it twice: the grant is lost
  (every write into the directory is denied once it does appear) and the
  failure lands in the worker's own report, downgrading a genuinely confined
  worker to `off`. A root it cannot create is a stderr warning, not a crash.
  - Deliberately **not** in `sandbox.plan()`, where the review suggested it:
    `plan()` also receives caller-supplied `--work-dir` paths whose acceptance
    is decided later by `CheckRunner._work_dir`, and creating them there
    resurrects the exact bug `test_checks_cli.py::test_a_refused_work_dir_is_
    never_created` pins ("a refused path leaves nothing behind"). Measured:
    the `plan()` version failed that test and its packages-CLI twin.
    `tests/test_sandbox_plan.py` now pins both halves — `plan()` never creates
    a root it is handed, `_writable_roots` creates the two it owns.

### Honest labels
- **`kernel/denials.py`: `classify()` takes the traceback** and a `network`
  answer now requires a frame naming the call (`socket`, `urlopen`, `connect`,
  `getaddrinfo` — `create_connection` matches on `connect`). The seccomp filter
  answers EPERM for a refused `kill` too, and labelling that `network` sends an
  agent to fix a socket that is not in the script. An unattributed EPERM is
  `None`, not a guess.
- **`kernel/worker.py`: `active` is what the preamble applied**, not whether
  the env var was present: `bool(REPORT["landlock_abi"] or REPORT["seccomp"] or
  REPORT["rlimits"])`. A payload whose every stage failed leaves a non-empty
  report and must still classify nothing.
- **`kernel/client.py`: only a `landlock`/`seccomp` stage failure clears
  `sandboxed`** (new `confinement_holds(report)` + `CONFINEMENT_STAGES`). A
  refused rlimit is a quota that did not apply — it belongs in the report and
  in health's warnings, but Landlock and seccomp are still in force, and
  saying `off` understates the confinement as badly as claiming `active` on
  intent would overstate it.
- **The request-id claim is corrected** in `client._request_locked`'s comment
  and in changelog 0232 (branch number 0215): random ids stop a *lingering* forked child (or any
  stale writer) from computing and answering requests it never saw. They do
  **not** stop the running script from forging the response to its own
  in-flight request — it holds fd 1 and can reach the id through the
  interpreter — which is the same trust domain as `build()` returning a fake
  shape, and not worth an fd redesign.

### Smaller
- `kernel/_confine.py`: `syscall`'s `argtypes` is bound **once** in `_libc()`
  (fixed 6-argument signature, unused slots padded with zeros). It was
  re-bound per call on a cached, shared function object, and `landlock_abi()`
  is called from the threaded server through `sandbox_linux.build`.
- `kernel/protocol.py`: the module docstring's response shape now shows
  `usage`, and says `id` is a random 62-bit token the worker echoes.
- `kernel/sandbox.py`: `supported()`'s docstring says why it still answers
  `False` on Linux although Linux workers *are* confined — the legacy string
  cannot claim `active` from intent, and the health object that reads the
  worker's live report is a later slice.

## Files
- `agentcad/kernel/_confine.py` — io_uring in `ARCH` + `_PEEK_SYSCALLS`; one
  fixed `syscall` signature
- `agentcad/kernel/denials.py` — `classify(..., traceback="")`, `_NETWORK_FRAMES`
- `agentcad/kernel/worker.py` — `active` from what was applied; passes the tb
- `agentcad/kernel/client.py` — `confinement_holds()`, `CONFINEMENT_STAGES`,
  the corrected id comment
- `agentcad/cli.py` — `_writable_roots` creates the projects dir and `~/.agentcad`
- `agentcad/kernel/sandbox.py` — the comment saying why `plan()` must NOT;
  `supported()` docstring
- `agentcad/kernel/protocol.py` — `usage` in the documented response shape
- `docs/changelog/0232-…md` (was `0215-…` on the branch) — the corrected forgery claim (and its real hash)
- `tests/test_confine_unit.py` — io_uring denied, both arches, all three numbers
- `tests/test_denials.py` — the socket-frame rule, and an EPERM that is not one
- `tests/test_protocol_ids.py` — `confinement_holds` (rlimit vs landlock vs
  seccomp, and the Linux ABI rule); a preamble that applied nothing labels no
  denials (a real worker, bogus payload)
- `tests/test_sandbox_plan.py` — `plan()` creates nothing; `_writable_roots`
  creates its two and warns on a root it cannot
- `tests/test_sandbox_linux.py` — io_uring under the real filter; a missing
  root's true cost; the kill denial is not labelled `network`; only the
  `sandboxed` claim is gated on `AGENTCAD_EXPECT_SANDBOX`

## Notes
- **The io_uring battery test is not attribution.** Measured in
  `agentcad:local`: Docker's own default profile already answers EPERM for
  425/426/427 with no filter of ours installed. It is not redundant, though —
  with that profile off the syscall is live in the same kernel
  (`io_uring_setup(8, NULL)` answers EFAULT, not ENOSYS), so on a host without
  Docker's profile our filter is the only thing closing it. The unit test,
  which interprets the program's bytes, is the proof that *our* filter denies.
- **A residual, stated rather than fixed:** a legitimately accepted
  `agentcad check --work-dir <path>` that does not exist yet is still ungranted
  on Linux, because the runner creates it after the first worker has spawned.
  Fixing it means the checks CLI accepting the path before `_build_service`,
  which is that command's surgery, not the sandbox's.
  `tests/test_sandbox_linux.py::test_a_root_that_does_not_exist_is_a_lost_grant_
  and_a_reported_failure` documents the cost end to end.
- `make test` — **4073 passed, 24 skipped in 524.79s (0:08:44)** (macOS, full parallel
  suite; slice 2 was 4055/22, so the fixes add 18 tests).
- `make test-linux` — 75 passed, 1 skipped in 28.53 s inside `agentcad:local`;
  `tests/test_sandbox_linux.py` alone: 15 passed in 16.62 s, none skipped.
