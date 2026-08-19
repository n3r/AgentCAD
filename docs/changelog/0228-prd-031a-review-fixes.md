# 0228 — 2026-08-18 — PRD-031a review fixes: the honest count, the identity tripwire, two recorded residuals

## Summary

Two independent reviews of PRD-031a (security/containment axis, and
correctness/data-integrity axis). The security review returned **SHIP** with no
defects — the one-shared-kernel-path invariant held under attack: both the
market `/variant` and `/download` provably take PRD-007's process-global
in-flight semaphore, the per-IP `TokenBucket` is genuinely shared (draining it
via `/s/` throttles `/market`), the IP key is proxy-resolved (the M3 lesson
holds), browse/search/script/params/mesh are provably zero-kernel, and a
`scope: private` index is byte-identical to nonexistent on all seven market
routes including `market_install`. The correctness review returned
CHANGES-REQUIRED on one HIGH — a shipped placeholder count — and recorded two
LOW residuals. This entry closes them.

## Fixes

- **The close-out changelog shipped `PLACEHOLDER_COUNT`** (0227) — the build
  agent looped on a suite-completion event it could not receive and never
  filled the real number, and the count-citation guard was fooled because the
  changelog quoted the *prior* tree's `4074 passed` as provenance. Filled with
  the measured **4068 passed, 1 skipped**, and tightened
  `test_the_newest_changelog_cites_a_make_test_count` to refuse a residual
  `PLACEHOLDER`/`TODO`/`TKTK` sentinel — a count that is still a placeholder is
  not a measurement.

- **A PRD-008 identity regression the full suite caught** (and the build
  agent's incomplete run missed): `test_presence.py`'s tripwire counts the
  `X-Agent-Id` headers in `api.js`, and slice 5's two hand-rolled public market
  fetches (`marketScript`, `marketMesh`) carried the browser identity, pushing
  the count 6→8. Fixed by dropping the identity header from those anonymous
  public reads — the server ignores `X-Agent-Id` on the public surface (the
  customizer guard keys on the resolved IP), and an anonymous browse must not
  carry a per-profile browser fingerprint. `marketVariant` rides the shared
  `request()` path where the header is universal and inert on a public route.
  The count returns to 6 and the identity surface stays minimal.

## Recorded residuals (reviewer LOW findings — acceptable for 031a's seeded scope)

- **The digest's param spec is not re-bound to the pinned script bytes at the
  anonymous customizer read.** The listing spec comes from `index.json` and the
  script bytes from `index.fetch`, independently; the binding is enforced at
  *publish* time (`indexes.publish` verifies the content id and generates the
  digest from the gate report atomically), not re-checked on the anonymous
  read. For the immutable, shipped bundled catalog the two are consistent
  (spot-checked: extrusion/nema/iso digest ranges equal the real `PARAMS`), so
  this is acceptable for the seeded scope — but the guarantee is "catalog
  integrity at publish," not a runtime check. A drift (corruption, hand-edit,
  partial regen) would let the customizer advertise a stale spec while building
  a different script. Worth a runtime content-id verification when 031b opens
  third-party publishing.
- **The frontend "Add to library" hardcodes `index: "agentcad-core"`** while
  the `market_install` agent tool resolves the seeded index dynamically. It
  works as shipped (the bundled catalog *is* `agentcad-core`) but would break a
  differently-named public catalog. A browser-path (AC9-evidence) nicety, not a
  machine-tested path.

## Files

- `docs/changelog/0227-*.md` — the real `make test` count replaces the
  placeholder
- `tests/test_prd031a_acceptance.py` — the count guard refuses a sentinel
- `frontend/js/api.js` — public market reads drop the identity header
- `docs/changelog/0228-prd-031a-review-fixes.md` — this entry

## Notes

`make test` — **4068 passed, 1 skipped**. The full run measured 4067 passed
with the one identity-count failure above; fixing it makes the green total
4068 (this entry adds no tests — it strengthens one assertion and removes two
header lines). CI on clean runners is the authoritative validation. The
security-axis SHIP verdict stands unchanged by these fixes: nothing here
touches the semaphore, the shared guard, the proxy-resolved IP, param parity,
or the `scope: public` filter.
