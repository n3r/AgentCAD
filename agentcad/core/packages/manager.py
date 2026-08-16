"""`PackageManager` — the façade that becomes `service.packages`.

This module owns *resolution* and *installation*: which index answers a name,
which version a requirement selects, and what gets written into the project
manifest. Materialisation (`use_part`), search and the publish gate live in
their own modules.

Two rules run through it:

* **Nothing here is captured at registration.** The tool pack loads at `pac`,
  before `tools_proposals` (`p`), `tools_specs` (`s`) and `tools_versioning`
  (`v`), so `service.specs`, `service.branches` and `service.gate_providers`
  do not exist yet. Every attribute of the service is read *inside* a method.
* **Offline is not a second answer.** When no index can be reached, a
  requirement resolves from the **cache**, and the lock entry is
  reconstructed from the receipt — which is byte-identical to the one an
  online install would have written, because every field in a lock entry is
  content-determined. A test pins that equality rather than describing it.

`use_part` (slice 7) never touches an index at all: it reads the lock, reads
the cache, verifies and copies. That is the whole of AC4.
"""

from __future__ import annotations

from pathlib import Path

from ... import config as user_config
from ..model import NotFoundError, ValidationError
from . import cache, format, indexes as index_module, lockfile


class PackageManager:
    def __init__(self, service, indexes=None):
        self._service = service
        self._indexes = list(indexes) if indexes is not None else None
        self.warnings: list[str] = []

    # ------------------------------------------------------------ indexes

    @property
    def indexes(self) -> list:
        """The configured indexes, in precedence order.

        Loaded once and kept; :meth:`reload_indexes` re-reads the config.
        Warnings from the load are kept on the manager so `list_packages` can
        report a misconfigured index instead of it silently not existing.
        """
        if self._indexes is None:
            self.reload_indexes()
        return self._indexes

    def reload_indexes(self) -> list:
        """Re-read the configuration and mint new index clients.

        The git probe is borrowed from `service.history`, which already caches
        `shutil.which("git")` — the design's rule that no new probe is added.
        A service without one (a bare stub in a test) falls back to
        `_git.available`.
        """
        self.warnings = []
        history = getattr(self._service, "history", None)
        self._indexes = index_module.load_indexes(
            user_config.load_config(), self.warnings,
            git_available=getattr(history, "available", None),
        )
        return self._indexes

    def index_named(self, name: str):
        for index in self.indexes:
            if index.name == name:
                return index
        raise NotFoundError(
            f"no index named {name!r} is configured "
            f"(configured: {[i.name for i in self.indexes]})"
        )

    # ----------------------------------------------------------- resolving

    def resolve(self, name: str, version_req=None, index=None) -> dict:
        """Resolve ``name`` at ``version_req`` to one installable version.

        Walks the configured indexes in precedence order; the first that
        answers wins. ``index=`` pins one. Falls back to the **cache** when no
        index answers, so an install that has been done once keeps working
        with the index deleted (AC4).

        Returns::

            {"name", "version", "content_id", "index", "source", "entry",
             "path", "offline", "tried"}

        ``path`` is where to copy from and is ``None`` offline (it is already
        in the cache). Raises ``NotFoundError`` naming the package, the
        requirement and **every index tried with why each failed**.
        """
        requirement = version_req or "*"
        self._check_requirement(name, requirement)
        candidates = ([self.index_named(index)] if index else list(self.indexes))
        tried: list[dict] = []
        for candidate in candidates:
            resolution = self._resolve_in(candidate, name, requirement, tried)
            if resolution is not None:
                resolution["tried"] = tried
                return resolution
        offline = self._resolve_cached(name, requirement, tried,
                                       pinned_index=index)
        if offline is not None:
            offline["tried"] = tried
            return offline
        detail = "; ".join(f"{t['index']}: {t['reason']}" for t in tried)
        raise NotFoundError(
            f"no index or cache entry provides {name} {requirement}"
            + (f" — tried {detail}" if detail else " (no index is configured)"),
            {"package": name, "version_req": requirement, "tried": tried},
        )

    def _resolve_in(self, index, name, requirement, tried) -> dict | None:
        try:
            index.refresh()
            versions = index.versions(name)
        except (NotFoundError, ValidationError) as exc:
            # A broken or unreachable index is DATA: the next one still gets
            # its turn, and the reason travels to the caller.
            tried.append({"index": index.name, "reason": str(exc)})
            return None
        if not versions:
            tried.append({"index": index.name,
                          "reason": f"does not carry {name!r}"})
            return None
        version = format.resolve(versions, requirement)
        yanked = False
        if version is None:
            yanked_only = format.resolve(versions, requirement, allow_yanked=True)
            # An EXPLICITLY NAMED yanked version warns and proceeds (design
            # Decision 10): a yank says "do not start here", not "you may
            # never have this" — a project pinned to it, or restoring one, has
            # to be able to re-install exactly what its lock records. A RANGE
            # is the opposite case and still refuses: `^1.0.0` is the caller
            # asking us to choose, and choosing a yanked version is what the
            # flag exists to prevent.
            if yanked_only is not None and format.VERSION_RE.match(requirement):
                version, yanked = yanked_only, True
                self.warnings.append(
                    f"{name}@{version} is YANKED in index {index.name!r} and "
                    f"you named it explicitly, so it was installed anyway. "
                    f"The publisher withdrew it — move off it when you can.")
            else:
                reason = (
                    f"only yanked versions of {name!r} match {requirement}"
                    if yanked_only else
                    f"carries {name!r} at {sorted(versions)}, none matching "
                    f"{requirement}"
                )
                tried.append({"index": index.name, "reason": reason})
                return None
        entry = versions[version]
        try:
            path = index.fetch(name, version)
        except (NotFoundError, ValidationError) as exc:
            tried.append({"index": index.name, "reason": str(exc)})
            return None
        return {
            "name": name,
            "version": version,
            "content_id": entry.get("content_id"),
            "index": index.name,
            "source": index.source_of(entry),
            "entry": entry,
            "path": path,
            "offline": False,
            "yanked": yanked,
        }

    def _resolve_cached(self, name, requirement, tried, *,
                        pinned_index=None) -> dict | None:
        """The offline path: the highest cached version that satisfies the
        requirement **and whose tree still verifies**.

        A cached tree that does not verify is not a fallback — it is the thing
        the verification exists to catch — so it is skipped with its reason
        recorded, exactly like an index that failed. And a caller who *pinned*
        an index gets only cache entries that index installed: a pin is a
        statement about provenance, and answering it from another index's
        download would quietly break it.
        """
        versions = cache.cached_versions(name)
        if not versions:
            return None
        for version in sorted(versions, key=format.parse_version, reverse=True):
            if not format.satisfies(version, requirement):
                continue
            report = cache.verify(name, version)
            if report["status"] != "ok":
                tried.append({"index": "cache",
                              "reason": f"{name}@{version} is cached but "
                                        f"{report['status']}"})
                continue
            receipt = cache.read_receipt(name, version) or {}
            if pinned_index is not None and receipt.get("index") != pinned_index:
                tried.append({
                    "index": "cache",
                    "reason": f"{name}@{version} is cached from index "
                              f"{receipt.get('index')!r}, not the pinned "
                              f"{pinned_index!r}",
                })
                continue
            return {
                "name": name,
                "version": version,
                # Reconstructed from the receipt, which recorded exactly what
                # the online install recorded — so the lock entry is byte for
                # byte the one an online resolve would have written.
                "content_id": receipt.get("content_id"),
                "index": receipt.get("index"),
                "source": receipt.get("source"),
                "entry": None,
                "path": None,
                "offline": True,
                "yanked": False,
            }
        return None

    # ------------------------------------------------------------- install

    def add(self, proj: str, name: str, version_req=None, index=None) -> dict:
        """Resolve, install into the cache, and record both manifest maps.

        Publishes `project_changed` — an ordinary store write, so the history
        snapshot and the undo entry are free and no new event type exists.
        """
        service = self._service
        requirement = version_req or "*"
        # Warnings raised BY THIS ADD (a yanked version named explicitly)
        # travel in the result. `self.warnings` also carries the index-loading
        # warnings, so the slice is taken rather than the whole list.
        watermark = len(self.warnings)
        resolution = self.resolve(name, requirement, index=index)
        if not resolution["offline"]:
            cached = cache.install(
                resolution["path"], name, resolution["version"],
                resolution["content_id"],
                index=resolution["index"], source=resolution["source"],
            )
        else:
            cached = cache.require(name, resolution["version"])

        manifest = service.store.manifest(proj)
        lockfile.add(manifest, name, requirement, resolution["index"], resolution)
        service.store.save_manifest(proj, manifest)
        service.bus.publish({"type": "project_changed", "project": proj})
        return {
            "project": proj,
            "package": lockfile.requirement_for(manifest, name),
            "lock": lockfile.entry_for(manifest, name),
            "cached": str(cached),
            "offline": resolution["offline"],
            "yanked": bool(resolution.get("yanked")),
            "tried": resolution["tried"],
            "warnings": self.warnings[watermark:],
        }

    def remove(self, proj: str, name: str, *, scan=None) -> dict:
        """Drop a package from both maps.

        The cache is **not** touched: it is shared by every project, and a
        materialised part keeps building either way — FR6's removal is a
        warning, not breakage.
        """
        service = self._service
        manifest = service.store.manifest(proj)
        affected = lockfile.remove(manifest, name, scan=scan)
        service.store.save_manifest(proj, manifest)
        service.bus.publish({"type": "project_changed", "project": proj})
        return {"project": proj, "removed": name, "materialized_parts": affected}

    # ------------------------------------------------------------- helpers

    @staticmethod
    def _check_requirement(name, requirement) -> None:
        try:
            format.satisfies("0.0.0", requirement)
        except ValidationError as exc:
            raise ValidationError(
                f"{requirement!r} is not a version requirement for {name!r}: "
                "use X.Y.Z, ^X.Y.Z, ~X.Y.Z or *"
            ) from exc

    def cache_path(self, name: str, version: str) -> Path:
        return cache.version_dir(name, version)
