"""The two additive manifest maps: `packages` and `packages_lock`.

::

    "packages":      {"iso4762": {"version_req": "^1.2.0",
                                  "index": "agentcad-core"}}
    "packages_lock": {"iso4762": {"version": "1.2.0",
                                  "content_id": "sha256:9f3c…",
                                  "index": "agentcad-core",
                                  "source": {"kind": "git", "url": "…",
                                             "ref": "main"}}}

Three properties are load-bearing and each one is a test:

* **Nothing in either map is a timestamp, a path or a machine fact.** Both
  are git-tracked, so two branches that add `iso4762@1.2.0` from the same
  index must write byte-identical entries and merge clean; a lock carrying
  `installed_at` would conflict on every concurrent add and would break
  AC3's byte-identical re-materialisation. Machine facts live in the cache
  receipt, which is never committed.
* **Keys are written sorted.** Two clients that add the same two packages in
  opposite orders produce the same bytes.
* **The last removal removes the key itself.** A project that ends up with no
  packages is byte-identical to one that never had any (FR15) — which is what
  makes the whole feature invisible to a project that does not use it.

Entries are merged **atomically per package name** by `manifest_merge` (see
its key-space table): merging one side's version with the other side's
content id yields a lock entry nobody authored and that verifies against
nothing.
"""

from __future__ import annotations

import copy

from ..model import NotFoundError

PACKAGES_KEY = "packages"
LOCK_KEY = "packages_lock"


def read(manifest: dict) -> dict:
    """A copy of the declared-dependency map (`packages`)."""
    return copy.deepcopy(_map(manifest, PACKAGES_KEY))


def read_lock(manifest: dict) -> dict:
    """A copy of the resolution map (`packages_lock`)."""
    return copy.deepcopy(_map(manifest, LOCK_KEY))


def entry_for(manifest: dict, name: str) -> dict | None:
    """The lock entry for ``name``, or ``None``.

    ``None`` for a package that is declared but not locked is the honest
    answer and the one `use_part` fails closed on: guessing a version there
    would be inventing a dependency.
    """
    entry = _map(manifest, LOCK_KEY).get(name)
    return copy.deepcopy(entry) if isinstance(entry, dict) else None


def requirement_for(manifest: dict, name: str) -> dict | None:
    entry = _map(manifest, PACKAGES_KEY).get(name)
    return copy.deepcopy(entry) if isinstance(entry, dict) else None


def add(manifest: dict, name: str, version_req: str, index: str,
        resolved: dict) -> dict:
    """Record ``name`` in both maps. Mutates and returns ``manifest``.

    ``resolved`` is the manager's resolution: ``{version, content_id,
    source}``. Any other key it carries (a receipt's `fetched_at`, a local
    path) is **dropped here** — this function is the one place that decides
    what a lock entry may contain, so an offline install and an online one
    cannot write different bytes.

    A falsy ``version_req`` **keeps an existing declaration** and only falls
    back to ``"*"`` when there is none. Absent is not "any version": rewriting
    a declared `~1.0.0` to `"*"` because the caller passed nothing destroys a
    deliberate pin, and it does it silently. `PackageManager.add` reads the
    declaration before it resolves, so it never reaches here empty; this is the
    same rule stated where the write happens, for the next caller.
    """
    packages = dict(_map(manifest, PACKAGES_KEY))
    lock = dict(_map(manifest, LOCK_KEY))
    existing = packages.get(name) if isinstance(packages.get(name), dict) else {}
    packages[name] = {
        "version_req": (version_req or existing.get("version_req") or "*"),
        "index": index,
    }
    lock[name] = {
        "version": resolved["version"],
        "content_id": resolved["content_id"],
        "index": index,
        "source": copy.deepcopy(resolved.get("source")),
    }
    manifest[PACKAGES_KEY] = _sorted(packages)
    manifest[LOCK_KEY] = _sorted(lock)
    return manifest


def remove(manifest: dict, name: str, *, scan=None) -> list[str]:
    """Drop ``name`` from both maps; returns the materialised part ids whose
    provenance now reads ``removed``.

    ``scan`` is the seam slice 7 fills with `provenance.scan`; with no scan
    the answer is an empty list rather than a guess. Removing a package
    **never touches a script byte** — the provenance header lives inside the
    script and the script text is the rebuild cache key, so rewriting headers
    to express a removal would re-key and rebuild every materialised part.
    """
    packages = dict(_map(manifest, PACKAGES_KEY))
    lock = dict(_map(manifest, LOCK_KEY))
    if name not in packages and name not in lock:
        raise NotFoundError(
            f"package {name!r} is not in this project "
            f"(declared: {sorted(packages)})"
        )
    affected = list(scan()) if scan is not None else []
    packages.pop(name, None)
    lock.pop(name, None)
    _write(manifest, PACKAGES_KEY, packages)
    _write(manifest, LOCK_KEY, lock)
    return affected


# --------------------------------------------------------------- helpers


def _map(manifest, key) -> dict:
    value = manifest.get(key) if isinstance(manifest, dict) else None
    return value if isinstance(value, dict) else {}


def _sorted(entries: dict) -> dict:
    return {name: entries[name] for name in sorted(entries)}


def _write(manifest, key, entries) -> None:
    if entries:
        manifest[key] = _sorted(entries)
    else:
        manifest.pop(key, None)
