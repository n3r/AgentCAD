# Roadmap

v0.1 delivers the full vertical slice: script-as-model parts on a real OCCT
B-rep kernel, projects/assemblies, a browser UI, and the dual agent surface
(MCP + built-in chat). The items below are deliberate non-goals of v0.1,
roughly ordered by expected value.

## Platform

- **Windows / Linux.** The architecture is already portable: pure Python,
  OCP wheels exist for all three platforms, and the UI is a browser app. The
  work is packaging (installer scripts, a `make app` equivalent per
  platform) and CI to prove it. The only mac-specific artifact today is the
  `.app` wrapper script.
- **Single-binary distribution.** Today the repo + `uv` is the install
  story. A bundled distribution (briefcase/PyInstaller or a Tauri shell)
  would remove the toolchain prerequisite.
- **OS-level script sandboxing.** Part scripts currently run with user
  privileges (documented trust model). A macOS `sandbox-exec` /
  seatbelt-style profile around the kernel worker would harden the boundary
  without changing the architecture, since all script execution is already
  confined to one subprocess.

## Modeling

- **STEP import** — build123d's `import_step` makes this cheap; the design
  question is how imported (non-parametric) bodies coexist with script
  parts. Likely: a `reference` part kind usable in assemblies and booleans.
- **Boolean/enum/string parameters** — `PARAMS` is numeric-only in v0.1.
- **Assembly mates/constraints** — instances are posed by explicit
  transforms. A constraint solver (mate faces, concentric, distance) is the
  natural next step; `check_interference` already provides the validation
  half.
- **Multi-solid part semantics** — a part returning a `Compound` renders and
  exports, but per-solid materials/metrics are not tracked.
- **2D drawings** — projected views with dimensions (build123d has
  projection primitives; the hard part is dimension placement).

## Agents

- **Richer geometric feedback tools** — cross-sections, distance-between
  queries, draft/wall-thickness analysis; the more the kernel can measure,
  the tighter the agent loop.
- **Multi-agent sessions** — the service layer is already shared-state; a
  turn-locking scheme would let several agents (or agent + human) work one
  project concurrently.
- **Vision feedback** — screenshot-of-viewport as a tool result so agents
  can see what they built, not just measure it.

## Application

- **Undo/redo** — the store does atomic writes; a git-backed project history
  (every rebuild = commit) would give time travel nearly for free.
- **Mesh streaming for huge parts** — ACM1 is a single buffer today;
  chunking + LOD would help >1M-triangle models.
- **Multi-user / collaboration** — out of scope until there's a concrete
  need; the localhost-only bind is a security stance, not a limitation of
  the service layer.
