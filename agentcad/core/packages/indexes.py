"""Index clients: one shape, three kinds.

An index is a **directory** holding `index.json` and one directory per
published package version. That is the whole format, and it is why a git repo
is an index: clone it and you have a local one. So a git index (slice 9) is
*a local index plus a fetch step*, and a cloud index (PRD-005-lite) is the
same client over a different fetch — one implementation of parsing,
validating, searching and fetching, and the git-ness is forty lines.

The protocol::

    name: str
    kind: str            # "local" | "git" | "cloud"
    scope: str           # "public" | "private"
    refresh() -> None                     # no-op for local
    entries() -> dict                     # the parsed, validated index.json
    fetch(name, version) -> Path          # a directory
    source_of(entry) -> dict              # what the lockfile records

`source_of` exists because a lock entry may hold **only content-determined
values**: a local index records its *index-relative* path, never the absolute
one, or two machines would write different lock bytes for the same package.

Configuration lives in `~/.agentcad/config.json` in precedence order; the
first index that answers a name wins, and `add_package {index}` pins one. A
configuration entry that cannot be built is **skipped with a warning, never
fatal** — one broken index must not make the others unreachable.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..model import NotFoundError, ValidationError
from . import content, format

INDEX_FILE = "index.json"


class LocalIndex:
    """A configured directory. `refresh()` does nothing."""

    kind = "local"

    def __init__(self, name: str, path, scope: str | None = None):
        self.name = name
        self.path = Path(path)
        # The document's own `scope` wins; the configured value is the
        # fallback, and the default is "public" because that is the
        # fail-closed direction for slice 8's vendor gate (which refuses a
        # non-redistributable package into a public index).
        self._configured_scope = scope or "public"
        self._cache: tuple[tuple, dict] | None = None

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<{type(self).__name__} {self.name} at {self.path}>"

    @property
    def index_file(self) -> Path:
        return self.path / INDEX_FILE

    @property
    def scope(self) -> str:
        try:
            declared = self.entries().get("scope")
        except (NotFoundError, ValidationError):
            return self._configured_scope
        return declared if declared in format.INDEX_SCOPES else self._configured_scope

    def refresh(self) -> None:
        """No-op: a local directory is always as fresh as it is."""

    def entries(self) -> dict:
        """The parsed and validated `index.json`.

        Cached on the file's `(mtime_ns, size)`: a publish rewrites it, and
        re-validating a large index on every search would be paid per
        keystroke in the Library dialog.
        """
        path = self.index_file
        try:
            stat = path.stat()
        except OSError as exc:
            raise NotFoundError(
                f"index {self.name!r} has no {INDEX_FILE} at {self.path}"
            ) from exc
        stamp = (stat.st_mtime_ns, stat.st_size)
        if self._cache is not None and self._cache[0] == stamp:
            return self._cache[1]
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeDecodeError) as exc:
            raise ValidationError(
                f"index {self.name!r}: {INDEX_FILE} is unreadable: {exc}"
            ) from exc
        problems = format.validate_index(doc)
        if problems:
            raise ValidationError(
                f"index {self.name!r}: {INDEX_FILE} is invalid: "
                + "; ".join(p["message"] for p in problems[:3])
                + (f" (+{len(problems) - 3} more)" if len(problems) > 3 else ""),
                {"index": self.name, "problems": problems},
            )
        self._cache = (stamp, doc)
        return doc

    def versions(self, name: str) -> dict:
        """`{version: entry}` for ``name``, or `{}`."""
        record = (self.entries().get("packages") or {}).get(name)
        versions = record.get("versions") if isinstance(record, dict) else None
        return versions if isinstance(versions, dict) else {}

    def entry(self, name: str, version: str) -> dict:
        entry = self.versions(name).get(version)
        if not isinstance(entry, dict):
            raise NotFoundError(
                f"index {self.name!r} has no {name}@{version}"
            )
        return entry

    def fetch(self, name: str, version: str) -> Path:
        """The directory holding ``name@version`` inside this index.

        The entry's `path` is data from somewhere else, so it is resolved
        with a containment check: a crafted `"../../etc"` cannot make a fetch
        read outside the index root.
        """
        entry = self.entry(name, version)
        directory = content.resolve_within(
            self.path, entry.get("path"), what=f"{name}@{version} index path"
        )
        if not directory.is_dir():
            raise NotFoundError(
                f"index {self.name!r} declares {name}@{version} at "
                f"{entry.get('path')!r}, but that directory is not there"
            )
        return directory

    def source_of(self, entry: dict) -> dict:
        """What `packages_lock` records for a package from this index.

        The **index-relative** path only: an absolute path is a machine fact,
        and a machine fact in a git-tracked lock breaks byte-identity.
        """
        return {"kind": self.kind, "path": entry.get("path")}


# Kinds this build can construct. `git` arrives with slice 9 and `cloud` with
# PRD-005-lite; until then a configuration naming one is skipped with a
# warning that says so, rather than silently ignored.
_KINDS = {"local": LocalIndex}
_PLANNED = {
    "git": "git indexes are not available in this build",
    "cloud": "cloud indexes are not available in this build",
}


def load_indexes(config: dict, warnings: list | None = None) -> list:
    """Build the configured indexes, in precedence order.

    An entry that cannot be built is skipped and named in ``warnings``. Never
    raises: one broken index must not make the others unreachable.
    """
    warn = warnings if warnings is not None else []
    out = []
    declared = (config or {}).get("indexes")
    if not isinstance(declared, list):
        return out
    seen = set()
    for i, entry in enumerate(declared):
        if not isinstance(entry, dict):
            warn.append(f"indexes[{i}] is not an object; skipped")
            continue
        name = entry.get("name")
        kind = entry.get("kind")
        if not isinstance(name, str) or not format.NAME_RE.match(name):
            warn.append(f"indexes[{i}] has no valid 'name'; skipped")
            continue
        if name in seen:
            warn.append(f"index {name!r} is configured twice; the later one "
                        "is skipped")
            continue
        if kind in _PLANNED:
            warn.append(f"index {name!r}: {_PLANNED[kind]}; skipped")
            continue
        if kind not in _KINDS:
            warn.append(f"index {name!r} has unknown kind {kind!r}; skipped")
            continue
        path = entry.get("path")
        if not isinstance(path, str) or not path:
            warn.append(f"index {name!r} (local) has no 'path'; skipped")
            continue
        seen.add(name)
        out.append(LocalIndex(name, path, scope=entry.get("scope")))
    return out
