"""The content-verified package cache: `~/.agentcad/packages/`.

Layout::

    ~/.agentcad/packages/<name>/<version>/            the extracted tree
    ~/.agentcad/packages/<name>/.receipts/<version>.json

**The receipt is a sibling, never inside the version directory.** A file
inside the tree is part of the content the tree's own id attests to, so a
receipt stored there would change the number it records.

The receipt is also where every **machine fact** lives — when it was fetched,
which index answered, what the source was. None of that may reach
`packages_lock`, which is git-tracked: a timestamp in a lock entry breaks
byte-identical re-materialisation (AC3) and makes two branches adding the same
package conflict on every add.

**Verification, and what it refuses to do.** :func:`verify` re-hashes the
whole cached tree and compares it with the receipt's `content_id`; it *never*
raises, because `list_packages` has to be able to report a broken entry.
:func:`require` raises unless the answer is `ok`, and it never repairs:
silently re-downloading over a mismatch is how a compromise becomes invisible,
so the refusal names the expected id, the actual id, the first differing path
and the fix. Re-hashing the whole tree on every materialisation is affordable
exactly because of the published ceilings (`content.MAX_PACKAGE_BYTES`):
measured at **67 ms** for a tree at the full 500-file / 50 MB ceiling and
**1.1 ms** for a realistic 8-file / 40 kB package (median of 5, dev machine —
changelog 0168). A kernel rebuild costs seconds, so this is not a cost a user
can see.

**The install is atomic and one-way.** The tree is copied into
`<name>/.staging-<rand>/` and `os.replace`d into place, receipt last; a copy
that raises leaves nothing behind. Installing over an entry that already
verifies is a no-op, and installing over one that does *not* is refused rather
than silently overwritten — the fix Decision 6 spells out is "remove the cache
entry and re-add", and a re-add that quietly repaired a tampered tree would
make that sentence a lie.
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
from datetime import datetime, timezone
from pathlib import Path

from ... import config
from ..model import ValidationError
from . import content, format

_STAGING_PREFIX = ".staging-"
_RECEIPTS_DIR = ".receipts"


def root() -> Path:
    """The cache root.

    `AGENTCAD_PACKAGES_DIR` overrides it outright; otherwise it sits beside
    `config.json`, so the `AGENTCAD_CONFIG` override every test already sets
    keeps the cache out of a real home directory too.
    """
    override = os.environ.get("AGENTCAD_PACKAGES_DIR")
    if override:
        return Path(override)
    return config.config_path().parent / "packages"


def package_dir(name: str) -> Path:
    return root() / _checked_name(name)


def version_dir(name: str, version: str) -> Path:
    return root() / _checked_name(name) / _checked_version(version)


def receipt_path(name: str, version: str) -> Path:
    return (root() / _checked_name(name) / _RECEIPTS_DIR
            / f"{_checked_version(version)}.json")


def cached_versions(name: str) -> list[str]:
    """Installed versions of ``name``, sorted as strings.

    Only real `X.Y.Z` directories: `.receipts` and any abandoned staging
    directory are not versions.
    """
    directory = package_dir(name)
    if not directory.is_dir():
        return []
    return sorted(
        child.name for child in directory.iterdir()
        if child.is_dir() and format.VERSION_RE.match(child.name)
    )


def read_receipt(name: str, version: str) -> dict | None:
    """The receipt, or ``None`` if it is absent or unreadable.

    Never raises: a caller asking "what do we know about this entry" must not
    be the caller that discovers the JSON is corrupt.
    """
    try:
        path = receipt_path(name, version)
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError, ValidationError):
        return None
    return data if isinstance(data, dict) else None


def install(src, name: str, version: str, expected_content_id: str, *,
            index: str, source: dict) -> Path:
    """Install ``src`` as ``name@version``, verifying it first.

    The source tree's content id is computed and compared with
    ``expected_content_id`` (the index entry's declared id) **before** a
    single byte is copied; a mismatch is a ``ValidationError`` naming both
    ids. Returns the installed version directory.
    """
    src = Path(src)
    entries = content.inventory(src)          # raises on a symlink
    problems = content.check_ceilings(entries)
    if problems:
        raise ValidationError(
            f"{name}@{version} exceeds the published package ceilings: "
            + "; ".join(p["message"] for p in problems),
            {"problems": problems},
        )
    actual = content.content_id_of(entries)
    if actual != expected_content_id:
        raise ValidationError(
            f"content id mismatch for {name}@{version}: the index declares "
            f"{expected_content_id}, the fetched tree at {src} hashes to "
            f"{actual}. Nothing was installed.",
            {"package": name, "version": version,
             "expected": expected_content_id, "actual": actual},
        )

    target = version_dir(name, version)
    if target.exists():
        report = verify(name, version)
        if report["status"] == "ok" and report["actual"] == expected_content_id:
            # Already installed, byte for byte. The receipt is left alone: it
            # records where these bytes actually came from, which is the first
            # fetch, not this one. (The lock entry records the index that
            # answered *now* — that is the manager's call, not the cache's.)
            return target
        if (report["reason"] == "no_receipt"
                and report["actual"] == expected_content_id):
            # An install interrupted between `os.replace` and the receipt
            # write. The tree hashes to the id the index declares, so writing
            # the receipt finishes that install rather than blessing anything:
            # a tampered tree still fails this comparison and still refuses.
            _write_receipt(name, version, content.inventory(target),
                           expected_content_id, index, source)
            return target
        raise ValidationError(
            f"{name}@{version} is already in the cache at {target} and does "
            f"not verify ({report['status']}"
            + (f", first difference {report['first_diff']}"
               if report["first_diff"] else "")
            + "). Refusing to overwrite it: remove that directory and add the "
              "package again.",
            {"package": name, "version": version, "path": str(target),
             "verify": report},
        )

    staging = package_dir(name) / f"{_STAGING_PREFIX}{secrets.token_hex(8)}"
    try:
        _copy_inventory(src, staging, entries)
        staging.replace(target)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    _write_receipt(name, version, entries, actual, index, source)
    return target


def verify(name: str, version: str) -> dict:
    """``{"status", "reason", "expected", "actual", "first_diff"}``.

    ``status`` is one of ``ok`` / ``tampered`` / ``missing``. **Never raises**
    — a broken cache entry is data, and `list_packages` reports on every entry
    including the broken ones.

    An entry whose receipt is gone or unreadable is ``tampered``, not ``ok``:
    with no receipt there is no expected hash, and "we did not look" is not
    "fine". A cached tree that cannot even be inventoried (a symlink planted
    in it, an unreadable file) is ``tampered`` for the same reason.
    """
    try:
        path = version_dir(name, version)
    except ValidationError:
        # Not a name/version this cache can ever hold: there is no such entry,
        # which is exactly what `missing` says.
        return _report("missing", "invalid_name", None, None, None)
    if not path.is_dir():
        return _report("missing", "not_installed", None, None, None)
    receipt = read_receipt(name, version)
    try:
        entries = content.inventory(path)
    except (ValidationError, OSError):
        expected = (receipt or {}).get("content_id")
        return _report("tampered", "unreadable_tree", expected, None, None)
    actual = content.content_id_of(entries)
    if receipt is None or not content.is_content_id(receipt.get("content_id")):
        return _report("tampered", "no_receipt", None, actual, None)
    expected = receipt["content_id"]
    if expected == actual:
        return _report("ok", None, expected, actual, None)
    # Naming the differing path needs the EXPECTED listing, which only the
    # receipt has (an index publishes one id, not a file list). Without it we
    # say so with `None` rather than pointing at an innocent file.
    recorded = receipt.get("inventory")
    first_diff = (content.first_difference(recorded, entries)
                  if isinstance(recorded, list) else None)
    return _report("tampered", "content_id_mismatch", expected, actual, first_diff)


def require(name: str, version: str) -> Path:
    """The verified cache path, or ``ValidationError``.

    This is the call `use_part` makes on **every** materialisation. It does
    not re-fetch, and it does not repair.
    """
    report = verify(name, version)
    if report["status"] == "ok":
        return version_dir(name, version)
    path = version_dir(name, version)
    if report["status"] == "missing":
        raise ValidationError(
            f"{name}@{version} is not in the cache ({path}). Run add_package "
            f"for {name!r} first — materialising a part never touches the "
            "network, so it can only use what is already cached.",
            {"package": name, "version": version, "verify": report},
        )
    where = f" (first difference: {report['first_diff']})" if report["first_diff"] else ""
    raise ValidationError(
        f"the cached copy of {name}@{version} does not match the content id "
        f"it was installed under{where}. Expected {report['expected']}, found "
        f"{report['actual']}. Nothing has been repaired: remove {path} and run "
        f"add_package for {name!r} again.",
        {"package": name, "version": version, "path": str(path),
         "verify": report},
    )


# --------------------------------------------------------------- helpers


def _checked_name(name) -> str:
    """A package name is a path segment here, so it is validated where it
    becomes one — a `..` in either half would otherwise address a directory
    outside the cache."""
    if not isinstance(name, str) or not format.NAME_RE.match(name):
        raise ValidationError(f"{name!r} is not a package name")
    return name


def _checked_version(version) -> str:
    if not isinstance(version, str) or not format.VERSION_RE.match(version):
        raise ValidationError(f"{version!r} is not a version (X.Y.Z)")
    return version


def _report(status, reason, expected, actual, first_diff) -> dict:
    return {"status": status, "reason": reason, "expected": expected,
            "actual": actual, "first_diff": first_diff}


def _copy_inventory(src: Path, dst: Path, entries) -> None:
    """Copy exactly the inventoried files.

    Not `shutil.copytree`: the cache must hold the tree the content id
    describes and nothing else, so ignored files never land there and a
    symlink cannot (the inventory refuses one before we get here).
    """
    dst.mkdir(parents=True, exist_ok=True)
    for relpath, _size, _sha in entries:
        target = dst / relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src / relpath, target)


def _write_receipt(name, version, entries, content_id, index, source) -> None:
    """Machine-local metadata, written last.

    `inventory` is carried in addition to the counts because naming the *first
    differing path* of a tampered entry is impossible without the expected
    listing, and a receipt is never published — so recording it costs nothing
    a consumer can see.
    """
    receipt = {
        "content_id": content_id,
        "index": index,
        "source": source,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "bytes": sum(size for _p, size, _s in entries),
        "files": len(entries),
        "inventory": [[path, size, sha] for path, size, sha in entries],
    }
    path = receipt_path(name, version)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    tmp.replace(path)
