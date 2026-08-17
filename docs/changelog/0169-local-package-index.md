# 0169 — 2026-08-16 — PRD-011 slice 3: the local index client, resolution, and the offline path

- **Commit:** pending
- **Date:** 2026-08-16
- **Author:** Claude (Opus 5)

## Summary

The install loop closes end to end with no server, no kernel and no network:
`indexes.py` is the one client shape all three index kinds reduce to, and
`manager.py` is `PackageManager` — resolve across indexes in precedence order,
install into the content-verified cache, write both manifest maps, publish
`project_changed`. The offline half is the point of the slice: with the index
deleted, an already-cached package still installs, and the lock entry it
writes is **byte-identical** to the one the online install wrote.

## Changes

- `agentcad/core/packages/indexes.py` (new)
  - `LocalIndex(name, path, scope=None)` with `kind`, `scope`, `refresh()`
    (no-op), `entries()` (parsed **and validated** `index.json`, cached on the
    file's `(mtime_ns, size)`), `versions(name)`, `entry(name, version)`,
    `fetch(name, version)` and `source_of(entry)`.
  - `fetch` resolves the entry's `path` through `content.resolve_within`, so a
    crafted `"../../etc"` cannot make a fetch read outside the index root.
  - `source_of` records the **index-relative** path only — an absolute path is
    a machine fact and a lock entry may not hold one.
  - `scope` prefers the document's own value, falls back to the configured
    one, and defaults to `"public"` — the fail-closed direction for slice 8's
    vendor gate.
  - `load_indexes(config, warnings=None)` builds the configured indexes in
    precedence order and **skips** anything it cannot build (no name, bad
    name, unknown kind, no path, a duplicate name, or a `git`/`cloud` kind
    this build cannot construct yet) with a warning naming it. Never raises:
    one broken index must not make the others unreachable.
- `agentcad/core/packages/manager.py` (new) — `PackageManager(service,
  indexes=None)`
  - `resolve(name, version_req, index=None)` walks the indexes in order, first
    to answer wins, `index=` pins one. Every failure is recorded as
    `{index, reason}` and travels in both the result and the
    `not_found_error`, whose message names the package, the requirement and
    every index tried with why each failed.
  - the offline path: when nothing answers, the highest **cached** version
    that satisfies the requirement *and still verifies*. The lock entry is
    reconstructed from the receipt. A pinned index is not satisfied by another
    index's cache entry.
  - `add(proj, name, version_req, index)` → resolve → `cache.install` (or
    `cache.require` offline) → `lockfile.add` → `store.save_manifest` →
    `bus.publish project_changed`; returns `{project, package, lock, cached,
    offline, tried}`.
  - `remove(proj, name)` drops both entries and leaves the **cache**
    untouched: it is shared by every project, and a materialised part keeps
    building either way (FR6's removal is a warning, not breakage).
  - Nothing is captured at construction — `service.specs`/`branches`/
    `gate_providers` do not exist at `pac`, and a test constructs the manager
    against a service whose `__getattr__` raises.
- `tests/test_packages_index.py` (new) — 42 tests.
- `tests/test_packages_ocp_free.py` — probes for `indexes` and `manager`.

## Files

- `agentcad/core/packages/indexes.py` — new
- `agentcad/core/packages/manager.py` — new
- `tests/test_packages_index.py` — new
- `tests/test_packages_ocp_free.py` — two probes added

## Verification

Targeted:

```
.venv/bin/python -m pytest -q tests/test_packages_index.py
42 passed
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

- **Offline is not a second answer.**
  `test_an_offline_add_writes_a_byte_identical_lock_entry` installs online,
  deletes the whole index directory, adds the same package into a second
  project and compares `json.dumps(..., indent=2)` of both lock entries. That
  equality is what every field in a lock entry being content-determined buys,
  and it is AC4's mechanism.
- **A cached tree that does not verify is not a fallback** — it is the thing
  the verification exists to catch. It is skipped with its reason recorded,
  exactly like an index that failed, and the resulting `not_found_error` says
  `tampered`.
- **An index is data from somewhere else.** A declared `content_id` that does
  not match the fetched tree installs nothing and names both ids (the check
  lives in `cache.install`, so every caller inherits it); a malformed
  `index.json` in the first of two indexes does not stop the second, and its
  reason travels in `tried`.
- `add` is idempotent: a second add of the same requirement leaves
  `project.json` byte-identical, and an add followed by a remove returns the
  manifest to exactly the bytes it had before (FR15, end to end).
- Slice 9 adds `GitIndex` by giving `LocalIndex` a fetch step; `_KINDS` and
  `_PLANNED` in `indexes.py` are where it plugs in.
