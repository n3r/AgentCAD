"""The content id: a canonical file-tree digest, plus the size ceilings.

FR2 asks for "the sha256 of its canonical archive". There is no archive — a
package is a directory (design Decision 1) — and hashing one would have been
the wrong number anyway: **tar is not byte-stable across producers** (member
ordering, mtimes, uid/gid, format), the same property that forced PRD-004's
determinism stage to compare SVG and never DXF. So identity is computed from
the *content listing*::

    content_id = "sha256:" + sha256(
        "".join(f"{posix_relpath}\\0{sha256_hex(file_bytes)}\\n"
                for path in sorted(files))
    )

No mtimes, no modes, no uids, no directory-walk order. The same number falls
out of a directory, a git checkout of that directory and a copy of it in the
cache — which is the property that makes "verify from cache" mean something.

Each entry binds a path to *its own* hash: a digest over the sorted hashes
alone would call two packages with swapped file contents identical.

Symlinks are refused outright rather than followed or recorded. A followed
link makes the listing depend on something outside the tree, a recorded one
would need a fourth field, and either way two paths for one file make the id
a function of how the tree was built.

The ceilings — 50 MB per package, 5 MB per file, 500 files — are part of the
published format. They bound a registry's disk and a malicious index's ability
to fill it, and (the load-bearing reason) they make it affordable to re-verify
the *whole* tree on every materialisation instead of trusting a receipt.
"""

from __future__ import annotations

import fnmatch
import hashlib
import os
import re
from pathlib import Path

from ..model import ValidationError

# Fixed and published: a package that ships an extra file has a different id,
# but a build artefact or an editor droppings file must not change identity
# between the publisher's tree and the consumer's checkout.
IGNORED = (".git/", "__pycache__/", "*.pyc", ".DS_Store", "*.tmp")

MAX_PACKAGE_BYTES = 50 * 1024 * 1024
MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_FILES = 500

CONTENT_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")

_READ_CHUNK = 1024 * 1024


def problem(code: str, message: str, field: str | None = None, **extra) -> dict:
    """The one shape every validator in this subpackage returns.

    A *problem* is data, never an exception: the gate turns each one into a
    PRD-004 report row (`items`, never `checks`), and a CLI prints them. The
    `code` is stable and matchable; the `message` is for a human or an agent
    and names the offending value.
    """
    entry = {"code": code, "message": message}
    if field is not None:
        entry["field"] = field
    entry.update(extra)
    return entry


def is_ignored(relpath: str) -> bool:
    """True for a path excluded from the identity by :data:`IGNORED`.

    A trailing-slash pattern matches a *directory* anywhere in the path; the
    others are `fnmatch` globs over the basename.
    """
    parts = relpath.split("/")
    if is_ignored_dir(parts[:-1]):
        return True
    return any(
        fnmatch.fnmatch(parts[-1], pattern)
        for pattern in IGNORED if not pattern.endswith("/")
    )


def is_ignored_dir(components) -> bool:
    """True when any of ``components`` is an ignored directory name."""
    names = set(components)
    return any(
        pattern[:-1] in names for pattern in IGNORED if pattern.endswith("/")
    )


def inventory(root: str | Path) -> list[tuple[str, int, str]]:
    """`[(posix_relpath, size_bytes, sha256_hex)]`, sorted by path.

    Raises ``ValidationError`` — and only that — for a missing root, a root
    that is not a directory, any symlink inside it (file or directory), and a
    file that cannot be read. Sizes and counts are not errors; they come back
    from :func:`check_ceilings` as problems.
    """
    root = Path(root)
    if not root.exists():
        raise ValidationError(f"package directory not found: {root}")
    if not root.is_dir():
        raise ValidationError(f"not a package directory: {root}")

    entries: list[tuple[str, int, str]] = []
    for dirpath, dirnames, filenames in os.walk(root):
        here = Path(dirpath)
        # Prune ignored directories in place so we never descend into .git/.
        dirnames[:] = sorted(d for d in dirnames if not is_ignored_dir([d]))
        for name in sorted(dirnames):
            path = here / name
            if path.is_symlink():
                raise ValidationError(
                    f"symlink in package: {_rel(root, path)} — packages are "
                    "plain directories; copy the target in instead"
                )
        for name in sorted(filenames):
            path = here / name
            rel = _rel(root, path)
            if path.is_symlink():
                raise ValidationError(
                    f"symlink in package: {rel} — packages are plain "
                    "directories; copy the target in instead"
                )
            if is_ignored(rel):
                continue
            try:
                entries.append((rel, path.stat().st_size, _sha256_file(path)))
            except OSError as exc:
                # One error type out of this function, so every caller has one
                # thing to catch (`cache.verify` must never propagate either).
                raise ValidationError(
                    f"cannot read {rel} in {root}: {exc}"
                ) from exc
    entries.sort()
    return entries


def content_id(root: str | Path) -> str:
    """`"sha256:<hex>"` over the canonical listing of ``root``."""
    return content_id_of(inventory(root))


def content_id_of(entries) -> str:
    """The content id of an already-computed inventory."""
    listing = "".join(f"{path}\0{sha}\n" for path, _size, sha in entries)
    return "sha256:" + hashlib.sha256(listing.encode()).hexdigest()


def is_content_id(value) -> bool:
    return isinstance(value, str) and CONTENT_ID_RE.match(value) is not None


def check_ceilings(entries) -> list[dict]:
    """Problems for a tree that exceeds the published ceilings.

    Takes an inventory rather than a path so the same numbers are enforced at
    publish and at install, and so the caller pays for one walk.
    """
    problems: list[dict] = []
    total = 0
    for path, size, _sha in entries:
        total += size
        if size > MAX_FILE_BYTES:
            problems.append(problem(
                "file_too_large",
                f"{path} is {size} bytes; the per-file ceiling is "
                f"{MAX_FILE_BYTES} bytes",
                field=path,
            ))
    if len(entries) > MAX_FILES:
        problems.append(problem(
            "too_many_files",
            f"the package holds {len(entries)} files; the ceiling is "
            f"{MAX_FILES}",
        ))
    if total > MAX_PACKAGE_BYTES:
        problems.append(problem(
            "package_too_large",
            f"the package is {total} bytes; the ceiling is "
            f"{MAX_PACKAGE_BYTES} bytes",
        ))
    return problems


def first_difference(expected, actual) -> str | None:
    """The first path (in sorted order) where two inventories disagree.

    An added path, a removed path and a changed hash are all differences —
    the point is to name *something* a human can go and look at, so a
    tampered cache entry's error message is actionable.
    """
    left = {path: sha for path, _size, sha in expected}
    right = {path: sha for path, _size, sha in actual}
    for path in sorted(set(left) | set(right)):
        if left.get(path) != right.get(path):
            return path
    return None


def is_safe_relpath(value) -> bool:
    """The lexical half of :func:`resolve_within` — no filesystem access.

    An `index.json` entry's ``path`` and a `package.json` part ``file`` are
    validated by a consumer that may not have the tree yet, so the check that
    a path is relative, POSIX-separated and free of ``..`` has to stand on its
    own.
    """
    try:
        _lexical_parts(value, "path")
    except ValidationError:
        return False
    return True


def resolve_within(root: str | Path, relpath, *, what: str = "path") -> Path:
    """Resolve ``relpath`` under ``root``, refusing anything that escapes it.

    Refused: an absolute path, a ``..`` segment, an empty or ``.`` path, a
    Windows drive or backslash, and — after resolution — any path that lands
    outside ``root``, which is how a symlink escapes. Both sides are resolved
    first because macOS hands back ``/private/var`` for ``/var``.
    """
    parts = _lexical_parts(relpath, what)
    root_resolved = Path(root).resolve()
    candidate = (root_resolved / "/".join(parts)).resolve()
    if candidate != root_resolved and root_resolved not in candidate.parents:
        raise ValidationError(
            f"{what} escapes the package directory: {relpath!r}"
        )
    return candidate


# --------------------------------------------------------------- helpers


def _lexical_parts(relpath, what: str) -> list[str]:
    if not isinstance(relpath, str) or not relpath.strip():
        raise ValidationError(f"{what} must be a non-empty relative path")
    if relpath.startswith("/") or "\\" in relpath or re.match(r"^[A-Za-z]:", relpath):
        raise ValidationError(
            f"{what} must be relative and POSIX-separated: {relpath!r}"
        )
    parts = [p for p in relpath.split("/") if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts):
        raise ValidationError(
            f"{what} must stay inside the directory it is relative to: "
            f"{relpath!r}"
        )
    return parts


def _rel(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(_READ_CHUNK)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()
