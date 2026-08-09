# PRDs — product requirement documents

One PRD per roadmap feature. The index with statuses lives in
[docs/roadmap.md](../roadmap.md); the evidence base is
[docs/market_research.md](../market_research.md).

## Conventions

- **Location = status.** `docs/prd/pending/` → not started; move the file to
  `docs/prd/in-progress/` when a feature enters its design/spec cycle, and to
  `docs/prd/shipped/` when its acceptance criteria are verified. The move is
  the status change; the `Status:` line in the file and the roadmap index row
  must be updated in the same commit.
- **Naming:** `PRD-NNN-<kebab-slug>.md`, zero-padded, stable forever.
  Numbers are allocated in the roadmap index — never reused, never renumbered.
- **A PRD fixes the *what* and the *why*,** with enough concreteness to be
  testable — not the build steps. When a feature is picked up, it still goes
  through the house process (superpowers brainstorming → design spec →
  implementation plan in `docs/superpowers/specs|plans/`); the PRD is that
  process's input, and any divergence discovered during design is folded back
  into the PRD before implementation starts.
- **Grounding:** competitive claims cite `market_research.md`; capability
  claims about today's product cite the real tool/route/file names
  (see `docs/agent-api.md`, `AGENTS.md`).

## Template

```markdown
# PRD-NNN — <Title>

- **Status:** pending
- **Phase:** v4 | v5 | v6 — <track name>
- **Created:** YYYY-MM-DD
- **Origin:** competitive analysis (Aug 2026) | founder idea #N (Aug 2026) | both
- **Depends on:** PRD-… (hard) · PRD-… (soft)
- **Related:** PRD-…

## Problem & motivation
What's broken or missing, for whom, and the competitive evidence that it
matters (link market_research.md sections).

## Users & jobs
Human roles and agent roles, each with the job this feature does for them.

## Goals
G1… — numbered, outcome-shaped.

## Non-goals
Explicit exclusions with one-line reasons.

## Experience
The workflow as a narrative: the human path (UI surfaces touched) and the
agent path (tool calls), including how the two hand off to each other.

## Functional requirements
FR1… — numbered, testable "musts". Group with subheadings if long.

## Agent surface
New/changed tools with sketch signatures, new events, error types. Follows
the house contract: structured errors, post-state returns, registered only
when runnable.

## Technical approach
How it lands on the architecture: which extension points (worker handler
pack / tool pack / route pack / toolkit module), service seams, manifest or
storage changes, kernel implications, frontend modules. Sketch, not plan.

## MVP & phasing
The smallest shippable slice, then later slices in order.

## Acceptance criteria
Verifiable "done when" statements — each one checkable by a test, a CI run,
or a browser session.

## Risks & open questions
Honest list; each risk with a mitigation direction.

## Competitive references
Who does this today, what we deliberately do differently.
```
