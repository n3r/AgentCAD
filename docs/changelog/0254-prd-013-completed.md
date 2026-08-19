# 0254 — 2026-08-19 — PRD-013 closed out: Assembly v2 MVP ships, the roadmap's first daily-driver-depth PRD

## Summary

Bookkeeping after PR #23 (Assembly v2) merged to main. The PRD moves to
`docs/prd/completed/` and its roadmap row flips to **completed (PR #23)** — 013
is the **first of the demoted v5 "daily-driver depth" tier (013/014/015/017) to
ship**, pulled forward because its structure (sub-assemblies, patterns, joints,
URDF) is what PRD-014 assembly drawings, PRD-015 BOM roll-ups, and PRD-030
motion/dynamics all build on.

## What shipped (MVP)

- **Instance patterns** (FR5–6): linear and polar patterns expand through the
  single expansion point in `core/mates.py` (`<id>` → `<id>[0..count-1]`, base
  absent), replace-not-add.
- **Cross-project sub-assemblies** (FR1–4): an instance can reference another
  project's assembly and mate to it through an interface connector; resolution
  reads the source read-only (never reaches `write_guard`).
- **Slider & planar joints** (FR10–11) on top of the existing rigid/revolute.
- **Simplified representations** (FR7–8): a scipy `ConvexHull` proxy mesh per
  instance (lazy import, kernel-side) for large instance counts.
- **URDF export** (FR14 core): instance → `<link>` with mass + COM-shifted
  inertia + mesh; mates → joints (rigid→fixed, revolute→revolute w/ limits,
  slider→prismatic; planar/cylindrical/ball degrade to fixed + a warning); an
  unmated instance → fixed child of `world`. Includes the parallel-axis inertia
  fix (OCCT's `matrix_of_inertia` is about the **COM**, not the origin).

Delivered as a method-wrapping `tools_structure` pack (no `service.py` edit) and
kernel-only OCP, per the extension-point contract.

## Deferred to Phase 2 (recorded, not dropped)

Ball/gear couplings, exploded views, and interference **broad-phase** (FR9) —
the MVP interference check is exact O(N²) pairwise, which is honest for the
example scales. These are noted in the PRD status header and the roadmap row.

## The CI detour (changelogs 0252–0253)

PR #23's ubuntu legs (`construction`/`fasteners` geometry checks + the
ubuntu-portability pytest) hung 30 minutes and were cancelled, macOS/Windows
green — a Linux-only symptom that first read as a PRD-013 × PRD-006-confinement
interaction. A branch-local diagnostic **disproved** that: the re-run went fully
green, the geometry check runs in 1.3 s with confinement not even active, and
all three hung jobs shared one signature — `apt-get update` stalling on
`archive.ubuntu.com noble-security InRelease` for ~1773 s. It was a **stalled
Ubuntu apt mirror**, an intermittent infra flake that had cancelled a `main` run
too. Fixed by hardening both CI workflows' apt step (`Acquire::Retries` +
`http`/`https` `Timeout`, retry loop); the diagnostic scaffolding was reverted.

## Changes

- `docs/prd/in-progress/PRD-013-assembly-v2.md` → `docs/prd/completed/`, status
  "completed — merged to main in PR #23 (MVP; Phase-2 items deferred)".
- `docs/roadmap.md`: the 013 row → **completed (PR #23)** with the MVP/Phase-2
  split spelled out; the "demoted behind that chain" note updated (013 is DONE,
  the first of its tier to ship).

## Notes

Two independent subagent reviews returned **SHIP**; review fixes (the
inertia-frame correction, planar-URDF degrade-to-fixed) landed in 0251/earlier.

`make test` — **4444 passed, 30 skipped** on the committed main tree. The run
measured 4435 passed with 9 failures, all of them the
`…full_suite_count_is_cited` / `…cites_a_make_test_count` guards reading THIS
entry's own not-yet-filled count (self-referential — they pass the moment this
number lands); filling it makes the green total 4444. No `test_supervisor.py`
contention flakes this run (they were parallel-checkout noise; see 0252). Suite
growth across the merge (PRD-013 + PRD-006): the branch's 013 work plus the
merged 006 tree land at 4444. CI on the clean three-OS matrix — green on PR #23
— is the authoritative validation.
