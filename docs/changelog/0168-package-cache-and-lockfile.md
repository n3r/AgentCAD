# 0168 — 2026-08-16 — PRD-011 slice 2: the content-verified cache, receipts, the lockfile, and two manifest-merge heads

- **Commit:** pending
- **Date:** 2026-08-16
- **Author:** Claude (Opus 5)

## Summary

Slice 1's pure functions learn to persist. `cache.py` owns
`~/.agentcad/packages/<name>/<version>/` with a **sibling** receipt, an atomic
install, and verification that never raises and never repairs; `lockfile.py`
owns the two additive manifest maps and the rule that neither may hold a
machine fact. `core/manifest_merge.py` gains the two key-wise heads in the
same commit that introduces the keys, because `apply_choices` is broken for
them until both sides know the names — the plan's only edit to a PRD-001
module.

## Changes

- `agentcad/core/packages/cache.py` (new)
  - `root()` — `AGENTCAD_PACKAGES_DIR`, else beside `config.config_path()`, so
    the `AGENTCAD_CONFIG` override tests already set keeps the cache out of a
    real home directory.
  - `install(src, name, version, expected_content_id, *, index, source)` —
    inventory → ceilings → **content id compared before a byte is copied** →
    copy the inventoried files into `<name>/.staging-<rand>/` → `os.replace`
    into place → receipt last. A copy that raises removes the staging
    directory. Re-installing a version that already verifies is a no-op;
    installing over one that does *not* verify is **refused**, naming the path
    and the fix. The one exception is an install interrupted between
    `os.replace` and the receipt write: when the cached tree hashes to exactly
    the id the index declares, the receipt is written and the install
    finishes. That is completing an install, not blessing one — a tampered
    tree fails the same comparison and is still refused, which is a test.
  - `verify(name, version)` → `{status, reason, expected, actual,
    first_diff}`; `status` is `ok`/`tampered`/`missing` and it **never
    raises**. No receipt, an unreadable receipt, or a tree that cannot be
    inventoried (a planted symlink) are all `tampered` — with no expected hash
    there is nothing to compare, and "we did not look" is not "fine".
  - `require(name, version)` — the call `use_part` will make on every
    materialisation. Raises unless `ok`, naming both ids, the first differing
    path and the fix. It does not re-fetch and does not repair.
  - `read_receipt`, `cached_versions`, `receipt_path`, `version_dir` — the
    last two validate name and version, because both become path segments.
- `agentcad/core/packages/lockfile.py` (new) — `read`, `read_lock`,
  `entry_for`, `requirement_for`, `add`, `remove`. `add` builds the entries
  itself and **drops every key `resolved` carries that is not
  content-determined**, so an offline install cannot write different bytes
  from an online one. Both maps are written in sorted key order; removing the
  last entry removes the key itself (FR15). `remove` takes the `scan=` seam
  slice 7 fills with `provenance.scan` and returns `[]` without one.
- `agentcad/core/manifest_merge.py` (edited, additive) — `_ENTRY_DICTS =
  ("materials", "packages", "packages_lock")` now drives both `_merge_section`
  (entries merge key-wise per package name, each entry **atomic**) and
  `_write_path` (or a resolution writes a bogus flat `"packages.iso4762"`
  key). Module docstring key-space table updated with both heads and the
  reason.
- `tests/test_packages_cache.py` (new) — 43 tests.
- `tests/test_manifest_merge.py` (extended) — 7 tests for the new heads (71 → 78).
- `tests/test_packages_ocp_free.py` — probes for `cache` and `lockfile`.

## Measurement

The claim that pays for re-verifying the **whole** tree on every
materialisation is that the ceilings bound it. Measured on the development
machine (median of 5, `sha256` over the canonical listing):

| tree | cost |
|---|---|
| 8 files / 40 kB (a realistic `iso4762`) | **1.1 ms** |
| 500 files / 50 MB (the published ceiling) | **67 ms** |

A kernel rebuild costs seconds, so a receipt fast path keyed on
`(size, mtime_ns)` — the fallback the design spec's risk list names — is not
needed and would be strictly weaker. The numbers are in `cache.py`'s
docstring.

## Files

- `agentcad/core/packages/cache.py` — new
- `agentcad/core/packages/lockfile.py` — new
- `agentcad/core/manifest_merge.py` — two key-wise heads + docstring
- `tests/test_packages_cache.py` — new
- `tests/test_manifest_merge.py` — PRD-011 section appended
- `tests/test_packages_ocp_free.py` — two probes added

## Divergence from the plan

- **The receipt carries an `inventory` key** in addition to the spec's
  `{content_id, index, source, fetched_at, bytes, files}`. Naming the *first
  differing path* of a tampered entry — which the plan requires — is
  impossible without the expected per-file listing, and an index publishes one
  content id, not a file list. The receipt is machine-local and never
  committed, so recording the listing costs nothing a consumer can see. Where
  a receipt has no listing, `first_diff` is `None` rather than a guess.
- **`install`'s mismatch error names both ids but no differing path.** Same
  reason from the other side: at install there is no expected listing to diff
  against, only the declared id. Stated in the message rather than implied.

## Verification

Targeted:

```
.venv/bin/python -m pytest -q tests/test_packages_cache.py tests/test_manifest_merge.py
121 passed
```

Full suite, with the whole of PRD-011 slices 1–3 in the tree:

```
.venv/bin/python -m pytest -q -n 2 --dist loadscope -rs
2763 passed, 1 skipped in 25:06
```

Baseline on this branch before slice 1 was **2527 passed, 1 skipped** (2528
collected); the three slices add **236** tests. `make test` is that command
(`test-full`). The single skip is pre-existing and explained —
`tests/test_analysis.py:166: agentcad[fem] installed; the 501 fallback is
unreachable`. The number is cited in all three of this sequence's entries
because the three slices were built and verified as one run; nothing between
them changes the count.

## Notes

- **The receipt is a sibling, never inside the version directory** — a file
  inside the tree is part of the content its own id attests to.
- The copy is per-file over the inventory, not `shutil.copytree`: the cache
  then holds exactly the tree the content id describes, ignored files never
  land there, and a symlink structurally cannot.
- **Entries merge atomically per package name** for the same reason
  `materials.<id>` does: one side's `version` with the other's `content_id` is
  a lock entry nobody authored and that verifies against nothing. Two branches
  adding *different* packages still merge clean, which is the whole point of
  the two heads.
- Tests deliberately attack the claims: a flipped byte, an added and a removed
  file, a deleted receipt, a corrupt receipt, a symlink planted in the cache, a
  copy that raises on its second file, a second install over a tampered entry,
  and a `require` with `cache.install` monkeypatched to fail if it is ever
  called.
