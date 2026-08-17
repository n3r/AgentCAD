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

import threading
from contextlib import contextmanager
from pathlib import Path

from ... import config as user_config
from ..model import NotFoundError, ValidationError
from . import cache, format, indexes as index_module, lockfile

#: One lock per project directory, shared by **every** `PackageManager` in this
#: process. Keyed by the resolved path rather than by the manager or the
#: project name: two managers over one service is the ordinary case (the tool
#: pack builds one, a test or a second caller builds another), and they must
#: contend on the same lock or the serialization is decorative.
_manifest_locks: dict[str, threading.RLock] = {}
_registry_lock = threading.Lock()


@contextmanager
def manifest_scope(store, proj: str):
    """Serialize a read-modify-write of one project's manifest.

    `add` and `remove` read `project.json`, edit two maps and save it back.
    Unserialized, two concurrent adds each read the *pre* state and the second
    save drops the first package — both callers having been told they
    succeeded. Reentrant, because `add` calls `remove`-shaped helpers and a
    caller may already hold it.

    **In-process only, and that is the honest boundary.** It makes the server
    safe (every route, tool and MCP call shares one process) and it does
    nothing for two `agentcad` CLI processes writing one project. The
    cross-process case for *projects* is PRD-008's territory — the store's
    write guard and the branch checkout — and inventing a second file-lock
    protocol here would be a third opinion about it. What this feature does
    lock across processes is `index.json`, where publishing genuinely is a
    multi-process CLI action (`LocalIndex._index_scope`).
    """
    try:
        key = str(Path(store.path_of(proj)).resolve())
    except Exception:      # noqa: BLE001 — an unknown project fails downstream
        key = f"{id(store)}:{proj}"
    with _registry_lock:
        lock = _manifest_locks.get(key)
        if lock is None:
            lock = _manifest_locks[key] = threading.RLock()
    with lock:
        yield


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

        **`service.bundled_indexes` is appended after the configured ones**, so
        the catalog that ships with the app answers on a fresh install with no
        network and no config file (`cli._register_catalog` is what sets it,
        on `_register_examples`' precedent). Appended, never prepended: an
        index the *user* configured under the same name wins, which is the
        only precedence rule a bundled fallback can honestly have. A service
        that has no such attribute — every test service, and `checks.py`'s
        ephemeral one — loads exactly what it loaded before.
        """
        self.warnings = []
        history = getattr(self._service, "history", None)
        self._indexes = index_module.load_indexes(
            self._configuration(), self.warnings,
            git_available=getattr(history, "available", None),
        )
        return self._indexes

    def _configuration(self) -> dict:
        """The index configuration: the user's file, then the bundled ones."""
        return index_module.merge_bundled(
            user_config.load_config(),
            getattr(self._service, "bundled_indexes", None),
        )

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
        # `(index name, version)` pairs a REACHABLE index says are withdrawn.
        # The cache fallback has to know them or a yank is defeated by any warm
        # cache — see `_resolve_cached`.
        #
        # **Qualified by index, and that is the fix.** A bare set of version
        # strings let index A's yank veto index B's identically-versioned
        # package: a cache entry installed from B, which never withdrew
        # anything, became unresolvable the moment A yanked its own 1.0.0. A
        # yank is a statement a publisher makes about *their* package, and it
        # binds their package only.
        withdrawn: set[tuple] = set()
        for candidate in candidates:
            resolution = self._resolve_in(candidate, name, requirement, tried,
                                          withdrawn)
            if resolution is not None:
                resolution["tried"] = tried
                return resolution
        offline = self._resolve_cached(name, requirement, tried,
                                       pinned_index=index, withdrawn=withdrawn)
        if offline is not None:
            offline["tried"] = tried
            return offline
        detail = "; ".join(f"{t['index']}: {t['reason']}" for t in tried)
        raise NotFoundError(
            f"no index or cache entry provides {name} {requirement}"
            + (f" — tried {detail}" if detail else " (no index is configured)"),
            {"package": name, "version_req": requirement, "tried": tried},
        )

    def _resolve_in(self, index, name, requirement, tried,
                    withdrawn: set | None = None) -> dict | None:
        try:
            index.refresh()
            versions = index.versions(name)
        except (NotFoundError, ValidationError) as exc:
            # A broken or unreachable index is DATA: the next one still gets
            # its turn, and the reason travels to the caller.
            tried.append({"index": index.name, "reason": str(exc)})
            return None
        if withdrawn is not None:
            withdrawn.update(
                (index.name, version) for version, entry in versions.items()
                if isinstance(entry, dict) and entry.get("yanked"))
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
                        pinned_index=None, withdrawn=None) -> dict | None:
        """The offline path: the highest cached version that satisfies the
        requirement **and whose tree still verifies**.

        A cached tree that does not verify is not a fallback — it is the thing
        the verification exists to catch — so it is skipped with its reason
        recorded, exactly like an index that failed. And a caller who *pinned*
        an index gets only cache entries that index installed: a pin is a
        statement about provenance, and answering it from another index's
        download would quietly break it.

        **A version a reachable index has YANKED is skipped here too, for a
        range.** The cache exists for "no index answered", not for "the index
        answered no": without this, any machine that had installed the version
        once would keep resolving it after the publisher withdrew it, and the
        yank would only bind the machines that never had it. An
        explicitly-named version still resolves, exactly as it does online — a
        lock entry naming a yanked version has to keep re-installing.
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
                                        f"{report['status']}"
                                        + (f" ({report['reason']})"
                                           if report.get("reason") else "")})
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
            # **Whose yank?** The receipt records which index this tree came
            # from, so a withdrawal only binds the entry it is about. Matching
            # on the version alone let index A's yank suppress a cache entry
            # that came from index B — B never withdrew anything, and its
            # package became unresolvable because a *different* publisher
            # withdrew a coincidentally equal version number.
            origin = receipt.get("index")
            yanked = bool(withdrawn and (origin, version) in withdrawn)
            if yanked and not format.VERSION_RE.match(requirement):
                tried.append({
                    "index": "cache",
                    "reason": f"{name}@{version} is cached from index "
                              f"{origin!r}, which has YANKED it, and "
                              f"{requirement} is a range — name the version "
                              f"explicitly if you must have it",
                })
                continue
            # Either no reachable index withdrew this entry's own version, or
            # one did and the caller named it explicitly. In the second case
            # the answer is `yanked: True` and the same warning the online path
            # raises: reporting `False` about a version we have just been told
            # is withdrawn is the cache quietly disagreeing with the index it
            # consulted, which is the one thing this fallback may not do.
            if yanked:
                self.warnings.append(
                    f"{name}@{version} is YANKED in index {origin!r} and you "
                    f"named it explicitly, so it was resolved from the cache "
                    f"anyway. The publisher withdrew it — move off it when you "
                    f"can.")
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
                "yanked": yanked,
            }
        return None

    # ------------------------------------------------------------- install

    def add(self, proj: str, name: str, version_req=None, index=None) -> dict:
        """Resolve, install into the cache, and record both manifest maps.

        **An omitted argument does not overwrite a declared one.** ``None``
        means *the caller did not say*, and for a package this project already
        declares, what it already declares is the answer: a re-add that
        silently rewrote `version_req` to `"*"` turned a deliberate `~1.0.0`
        pin into "give me anything", jumped the lock a major version and
        flipped every part materialised from it to `version_drift` — with no
        message anywhere. The Library dialog's Add button sends exactly `{name,
        index}`, so this was one click away from any project with a pin.
        Absent is not `"*"`, and it is not "any index" either.

        The declared requirement is read **before** the resolve, because it is
        the requirement that has to be resolved; and any change to either
        declaration travels back in `requirement_change` so a caller that did
        mean to widen a pin can see that it did.

        The whole read-modify-write runs under :func:`manifest_scope`. Two
        concurrent adds each used to read the *pre* manifest and the second
        save dropped the first package, with both callers told they had
        succeeded — and, because the store staged every write through one
        fixed `project.json.tmp`, they could interleave into that file and
        leave the manifest **corrupt** rather than merely short one entry (see
        `ProjectStore._atomic_write`).

        Publishes `project_changed` — an ordinary store write, so the history
        snapshot and the undo entry are free and no new event type exists.
        """
        service = self._service
        with manifest_scope(service.store, proj):
            declared = lockfile.requirement_for(service.store.manifest(proj),
                                                name) or {}
            requirement = version_req or declared.get("version_req") or "*"
            index = index or declared.get("index")
            # Warnings raised BY THIS ADD (a yanked version named explicitly)
            # travel in the result. `self.warnings` also carries the
            # index-loading warnings, so the slice is taken rather than the
            # whole list.
            watermark = len(self.warnings)
            resolution = self.resolve(name, requirement, index=index)
            if not resolution["offline"]:
                cached = cache.install(
                    resolution["path"], name, resolution["version"],
                    resolution["content_id"],
                    index=resolution["index"], source=resolution["source"],
                )
            else:
                cached = cache.require(
                    name, resolution["version"],
                    expected_content_id=resolution.get("content_id"))

            manifest = service.store.manifest(proj)
            lockfile.add(manifest, name, requirement, resolution["index"],
                         resolution)
            service.store.save_manifest(proj, manifest)
        service.bus.publish({"type": "project_changed", "project": proj})
        now = lockfile.requirement_for(manifest, name) or {}
        change = {key: {"from": declared.get(key), "to": now.get(key)}
                  for key in ("version_req", "index")
                  if declared and declared.get(key) != now.get(key)}
        return {
            "project": proj,
            "package": now,
            "lock": lockfile.entry_for(manifest, name),
            "cached": str(cached),
            "offline": resolution["offline"],
            "yanked": bool(resolution.get("yanked")),
            # `None` when nothing moved, so a caller tests one key rather than
            # diffing two maps to find out whether its own call changed a
            # declaration it did not mention.
            "requirement_change": change or None,
            "tried": resolution["tried"],
            "warnings": self.warnings[watermark:],
        }

    def remove(self, proj: str, name: str, *, scan=None) -> dict:
        """Drop a package from both maps.

        The cache is **not** touched: it is shared by every project, and a
        materialised part keeps building either way — FR6's removal is a
        warning, not breakage.

        Serialized against `add` by the same :func:`manifest_scope`: a remove
        racing an add is the same lost update in the other direction.
        """
        service = self._service
        with manifest_scope(service.store, proj):
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
