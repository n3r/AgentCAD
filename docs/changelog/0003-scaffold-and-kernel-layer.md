# 0003 — Scaffold and kernel layer (worker, ACM1 mesher, client)

- **Commit:** 17ce061
- **Date:** 2026-08-08
- **Author:** Claude Fable 5

## Summary
Lands the project scaffold (packaging, config, empty package tree) and the full
kernel layer: a warm build123d/OCCT worker subprocess, its line-JSON protocol, a
dependency-free ACM1 mesh codec, an OCP tessellator, and a supervising client
that enforces timeouts and respawns on hangs/crashes. Ships with kernel and mesh
test suites.

## Changes
- **Packaging:** `pyproject.toml` (package `agentcad` 0.1.0, deps build123d/
  fastapi/uvicorn/anthropic/mcp/httpx, dev pytest+pytest-timeout, `agentcad`
  console script, pytest timeout=120), `Makefile` (`setup/run/serve/test/app`),
  `.gitignore`, and a committed `uv.lock`. `agentcad/__init__.py` sets
  `__version__ = "0.1.0"`; `cli.py` is a version-printing stub.
- **Config** (`agentcad/config.py`): `load_config`/`save_config` (atomic
  tmp+`os.replace`) at `~/.agentcad/config.json`, overridable via
  `AGENTCAD_CONFIG`; `get_port()` returns a persisted valid port or allocates and
  persists `DEFAULT_PORT = 8630`; corrupt JSON recovers to default.
- **Protocol** (`kernel/protocol.py`): one-JSON-object-per-line framing over
  stdin/stdout; error-type constants (`script_error`, `contract_error`,
  `kernel_error`, plus client-synthesized `timeout`/`kernel_crash`); `WorkerError`
  with `to_payload()`. Methods: ping/build/export/export_assembly/interference/
  shutdown.
- **ACM1 codec** (`kernel/acm.py`): little-endian `pack`/`parse`/`read` for the
  binary mesh buffer (magic, u32 counts, f32 positions/normals, u32 indices, u32
  polyline lengths, f32 edge points); no OCP dependency so any process can read it.
- **Tessellation** (`kernel/mesh.py`): OCP `BRepMesh_IncrementalMesh` → per-face
  triangulation (faces not shared, preserving hard edges), per-vertex accumulated
  normals, reversed-face winding/normal flip, and tangential-deflection edge
  polylines → ACM1 bytes.
- **Worker** (`kernel/worker.py`): imports build123d once; `exec`s scripts in a
  fresh namespace with stdout redirected to stderr; validates the `PARAMS`/
  `build(p)` contract, resolves+clamps params (collecting warnings), rejects
  unknown params and bad return types as `contract_error`; extracts the failing
  line for script errors; a 16-entry shape LRU keyed by `sha256(script|params)`;
  computes metrics (volume/area/mass/bbox/COM/validity/counts), exports step/stl/
  3mf, and pairwise interference via `&`.
- **Client** (`kernel/client.py`): `KernelClient` spawns `python -m
  agentcad.kernel.worker`, drains stdout/stderr on threads, serializes one request
  at a time under a lock, enforces per-request timeouts (default 60 s, 180 s first
  ping), and kills+respawns on timeout/EOF/broken pipe, raising `KernelError` with
  a stderr tail.
- **Tests:** `tests/conftest.py` (session kernel fixture + plate/box scripts),
  `tests/test_kernel.py` (metrics, param override/clamp, error shapes, timeout
  recovery, step/stl/3mf export, interference volume, rotation semantics via STL,
  byte-identical determinism), `tests/test_mesh.py` (ACM1 counts/normals/bbox/
  round-trip), `tests/test_config.py`.

## Files
- `pyproject.toml`, `Makefile`, `.gitignore`, `uv.lock` — packaging/tooling
- `agentcad/__init__.py`, `agentcad/config.py`, `agentcad/cli.py` — package + config + stub CLI
- `agentcad/kernel/{protocol,acm,mesh,worker,client}.py` — kernel layer
- empty `__init__.py` for `agent/`, `core/`, `kernel/`, `server/`, `tests/`
- `tests/{conftest,test_kernel,test_mesh,test_config}.py` — kernel/mesh/config tests

## Notes
The server process never imports OCCT — all geometry lives in the worker, so a
script that hangs or crashes the kernel is killed and respawned without taking
down the app. Determinism (same script+params → byte-identical mesh) is asserted
by test. The CLI here is only a stub; real commands land in 0005.
