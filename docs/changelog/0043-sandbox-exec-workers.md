# 0043 — macOS sandbox-exec confinement of kernel workers

- **Commit:** pending
- **Date:** 2026-08-09
- **Author:** Claude (with Nikita Fedorov)

## Summary

Kernel worker subprocesses — where all part-script execution happens — are now
confined on macOS by a deny-by-default seatbelt profile (roadmap "OS-level
script sandboxing"): global read, writes only inside the project roots and
temp dir, no network. The boundary hardens without changing the architecture,
since script execution was already isolated to those subprocesses.

## Changes

- **`agentcad/kernel/sandbox.py`** (new): `available()` (darwin +
  `/usr/bin/sandbox-exec` + not disabled via `AGENTCAD_NO_SANDBOX`/config
  `{"sandbox": false}`, env wins in both directions), `build_profile()`
  (empirically minimized allow set: `process-exec*`, `process-fork`,
  `signal self`, `file-read*`, `file-write*` on realpath'd roots +
  tempdir + `/dev/null`, `sysctl-read` — required by `os.uname()` during the
  build123d import — and `deny network*`; each allow documented in-code with
  the observed denial that justified it), `wrap_argv()`, `status()`.
- **KernelClient/KernelPool**: keyword-only `writable_dirs` — `None` keeps
  byte-identical spawn behavior (all existing fixtures unaffected); when set,
  argv is wrapped at construction so every timeout-respawn keeps identical
  confinement, `PYTHONDONTWRITEBYTECODE=1` set, `.sandboxed` exposed.
- **CLI**: `_build_service` passes the real roots (projects dir,
  `~/.agentcad`, tempdir, registered example dirs) — the real app is
  sandboxed by default on macOS.
- **`/api/health`** gains `"sandbox": "active" | "off" | "unsupported"`,
  reflecting the live kernel client's state.
- Docs: architecture trust model rewritten, README + AGENTS.md updated.

## Files

- `agentcad/kernel/sandbox.py`, `agentcad/kernel/client.py`,
  `agentcad/kernel/pool.py`, `agentcad/cli.py`, `agentcad/server/app.py`
- `tests/test_sandbox.py` — 8 darwin-only tests: ping/build under sandbox,
  home-write blocked (probe file proven absent), network blocked (worker
  survives), timeout-respawn under sandbox, env opt-out, status semantics,
  health "active"
- `tests/test_server.py` — health asserts the new field
- `docs/architecture.md`, `README.md`, `AGENTS.md`

## Notes

Verified end-to-end on this machine: a CLI-shaped service (KernelPool,
sandboxed) built parts, wrote mesh caches, exported STEP, and rebuilt a
bundled example — with `~` writes and sockets from scripts failing as
`script_error` and the worker surviving. The allow set was minimized by
removing each candidate and observing failures (mach-lookup, dtracehelper,
file-ioctl, iokit-open all proved unnecessary on Darwin 25.5.0; older macOS
may need extras — the escape hatch is `AGENTCAD_NO_SANDBOX=1`). Sandboxing
confines writes + network; global read remains (documented honestly).
