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
:func:`require` raises unless the answer is `ok`, and it never repairs. A
receipt only says *these bytes are the bytes that were installed*, which is
why :func:`require_verified` also takes the **lock's** content id: the receipt
is written by whoever installed the tree, so two indexes publishing the same
`name@version` with different bytes both produce receipt-verified caches. The
lock is the git-tracked authority, and materialisation is bound to it. The
refusal names both ids:
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
from . import _json, content, format

_STAGING_PREFIX = ".staging-"
_RECEIPTS_DIR = ".receipts"

#: The receipt format. Versioned because the receipt is **load-bearing for the
#: offline path**, not just a note: `PackageManager._resolve_cached`
#: reconstructs a git-tracked lock entry out of it, so a receipt that is merely
#: *present* is not enough — it has to carry what that reconstruction needs.
RECEIPT_SCHEMA = 1

#: What an offline resolve reads back out of a receipt. Without these it writes
#: `index: null, source: null` into `packages` and `packages_lock` and reports
#: success, producing a lock entry no online install would ever have written —
#: which is exactly the byte-identity the offline path is supposed to preserve.
RECEIPT_REQUIRED = ("content_id", "index", "source")


def receipt_problem(receipt) -> str | None:
    """``None`` for a usable receipt, else the `verify` reason naming the fault.

    A receipt is *evidence*, so an unreadable or partial one is treated exactly
    like a tampered tree: refused, never patched up with defaults. An
    unversioned receipt is one this build did not write and cannot vouch for.
    """
    if not isinstance(receipt, dict):
        return "no_receipt"
    if receipt.get("schema") != RECEIPT_SCHEMA:
        return "receipt_schema"
    if not content.is_content_id(receipt.get("content_id")):
        return "no_receipt"
    if any(receipt.get(key) in (None, "") for key in RECEIPT_REQUIRED):
        return "receipt_incomplete"
    return None


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

    Never raises — and it says that because it is now true. It used to catch
    `(OSError, ValueError, UnicodeDecodeError, ValidationError)`, which does
    not include `RecursionError`, so a receipt file nested deeply enough took
    an exception out through `verify` (documented "never raises" too) and out
    through `list_packages`. `_json.read_optional` is the one reader with the
    one caught set.
    """
    try:
        path = receipt_path(name, version)
    except ValidationError:
        return None
    data = _json.read_optional(path, f"the cache receipt for {name}@{version}")
    return data if isinstance(data, dict) else None


def refuse_identity_mismatch(tree, name: str, version: str, *,
                             where: str = "the cached tree") -> dict:
    """The tree's own `package.json`, proven to be the package it is filed as.

    **The index entry is a claim about a tree; the tree is a claim about
    itself, and nothing used to compare them.** Resolution trusts the outer
    `packages.<name>.versions.<version>` keys, the content id proves the bytes
    are the bytes the index meant, and the identity in between was never
    checked — so an index (or a typo) mapping `foo@1.0.0` at a *verified* tree
    whose manifest says `bar@2.0.0` installed cleanly, and `use_part`
    materialised bar's code under foo's provenance header. Every downstream
    check passed, because every downstream check was asking a different
    question.

    Checked at install, and again where the cache is read: a cache populated
    by an earlier build has never been through this.
    """
    manifest = Path(tree) / "package.json"
    if not manifest.is_file():
        # A structured refusal naming the RELATIVE expectation. The generic
        # reader answers "package.json is unreadable: FileNotFoundError:
        # [Errno 2] … /abs/path/package.json" — an errno and a filesystem path
        # in a message an agent may hand back to a user, for a condition that
        # is simply "this is not a package".
        raise ValidationError(
            f"{where} filed as {name}@{version} has no package.json, so it is "
            f"not a package: every package declares its own name and version "
            f"in package.json at its root.",
            {"package": name, "version": version, "expected": "package.json",
             "path": str(tree)},
        )
    doc = _json.read_object(manifest,
                            f"{where} for {name}@{version}: package.json")
    declared_name, declared_version = doc.get("name"), doc.get("version")
    if declared_name != name or declared_version != version:
        raise ValidationError(
            f"{where} filed as {name}@{version} says it is "
            f"{declared_name!r}@{declared_version!r}. A package's own "
            f"package.json is the identity of record; the index entry that "
            f"points at it is not allowed to rename it. Nothing was installed "
            f"or materialised — the index that served this is wrong, or "
            f"pointing at the wrong directory.",
            {"package": name, "version": version,
             "declared_name": declared_name,
             "declared_version": declared_version, "path": str(tree)},
        )
    return doc


def install(src, name: str, version: str, expected_content_id: str, *,
            index: str, source: dict) -> Path:
    """Install ``src`` as ``name@version``, verifying it first.

    The source tree's content id is computed and compared with
    ``expected_content_id`` (the index entry's declared id) **before** a
    single byte is copied; a mismatch is a ``ValidationError`` naming both
    ids. Returns the installed version directory.

    The tree's **own `package.json` must agree** with the identity being
    installed under (see :func:`refuse_identity_mismatch`).
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
    refuse_identity_mismatch(src, name, version, where="the fetched tree")

    target = version_dir(name, version)
    if target.exists():
        report = verify(name, version)
        if report["status"] == "ok" and report["actual"] == expected_content_id:
            # Already installed, byte for byte. The receipt is left alone: it
            # records where these bytes actually came from, which is the first
            # fetch, not this one. (The lock entry records the index that
            # answered *now* — that is the manager's call, not the cache's.)
            return target
        if (report["reason"] in ("no_receipt", "receipt_schema",
                                 "receipt_incomplete")
                and report["actual"] == expected_content_id):
            # An install interrupted between `os.replace` and the receipt
            # write — or a receipt an older build wrote, which this one cannot
            # vouch for. Either way the TREE hashes to the id the index
            # declares, so writing a current receipt finishes that install
            # rather than blessing anything: a tampered tree still fails this
            # comparison and still refuses. This is also the upgrade path for
            # a cache populated before the receipt was versioned — `add` heals
            # it, and nothing else does.
            refuse_identity_mismatch(target, name, version)
            _write_receipt(name, version, content.inventory(target),
                           expected_content_id, index, source)
            return target
        if report["status"] == "ok":
            # The cached tree is INTACT — it verifies against its own receipt —
            # and it is simply a different `name@version` than the one being
            # installed. Two indexes publishing the same identity with
            # different bytes is exactly the C1 scenario, and calling it
            # "does not verify (ok)" both contradicted itself and told the user
            # to go looking for corruption that is not there.
            raise ValidationError(
                f"{name}@{version} is already in the cache at {target}, it "
                f"VERIFIES, and it is a different {name}@{version} than the "
                f"one being installed: the cached tree hashes to "
                f"{report['actual']} and this one hashes to "
                f"{expected_content_id}. Nothing is corrupt — two indexes have "
                f"published the same name and version with different content. "
                f"Refusing to overwrite it: decide which one this machine "
                f"should hold (the index each project's packages_lock names is "
                f"the answer), remove {target}, and add the package again.",
                {"package": name, "version": version, "path": str(target),
                 "cached": report["actual"], "incoming": expected_content_id,
                 "reason": "same_version_different_content", "verify": report},
            )
        raise ValidationError(
            f"{name}@{version} is already in the cache at {target} and does "
            f"not verify ({report['status']}"
            + (f"/{report['reason']}" if report["reason"] else "")
            + (f", first difference {report['first_diff']}"
               if report["first_diff"] else "")
            + "). Refusing to overwrite it: remove that directory and add the "
              "package again.",
            {"package": name, "version": version, "path": str(target),
             "reason": report["reason"], "verify": report},
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
    incomplete = receipt_problem(receipt)
    try:
        entries = content.inventory(path)
    except (ValidationError, OSError):
        expected = (receipt or {}).get("content_id")
        return _report("tampered", "unreadable_tree", expected, None, None)
    actual = content.content_id_of(entries)
    if receipt is not None and incomplete is not None:
        # The TREE may be perfect and the receipt still unusable. An offline
        # resolve reconstructs the lock entry from these fields, so a receipt
        # missing them produces `index: null, source: null` in a git-tracked
        # lock — an offline "success" that cannot reproduce what an online
        # install would have written, which is the one property the offline
        # path exists to have.
        return _report("tampered", incomplete,
                       receipt.get("content_id"), actual, None)
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


def require(name: str, version: str, *, expected_content_id=None) -> Path:
    """The verified cache path, or ``ValidationError``.

    This is the call `use_part` makes on **every** materialisation. It does
    not re-fetch, and it does not repair.
    """
    return require_verified(
        name, version, expected_content_id=expected_content_id)[0]


def require_verified(name: str, version: str, *,
                     expected_content_id=None) -> tuple[Path, str]:
    """``(path, measured_content_id)`` for a cache entry that verifies.

    ``expected_content_id`` is the **authority** the caller is materialising
    against — for `use_part` that is `packages_lock[name].content_id`, which
    is git-tracked and reviewable. Verifying the tree against its own receipt
    is not enough on its own: a receipt is written by whatever installed the
    tree, so two indexes publishing the same `name@version` with *different*
    bytes both produce receipt-verified caches, and the one on disk decides
    what a project gets while the lock's id decides what the header claims.
    Requiring both to agree is what binds the bytes to the lock.

    The id returned is the one **measured from the bytes**, so a caller that
    stamps it into a provenance header is quoting a measurement rather than
    copying a claim.
    """
    report = verify(name, version)
    if report["status"] == "ok":
        measured = report["actual"]
        if expected_content_id is not None and measured != expected_content_id:
            raise ValidationError(
                f"the cached copy of {name}@{version} is not the tree this "
                f"project locked: packages_lock declares "
                f"{expected_content_id}, and the cache at "
                f"{version_dir(name, version)} holds {measured}. It verifies "
                f"against its own receipt, so this is not corruption — it is a "
                f"DIFFERENT {name}@{version}, installed from another index. "
                f"Nothing was materialised and nothing was repaired: remove "
                f"that cache directory and run add_package again, which will "
                f"fetch the version the lock names.",
                {"package": name, "version": version,
                 "expected": expected_content_id, "actual": measured,
                 "path": str(version_dir(name, version)), "verify": report})
        return version_dir(name, version), measured
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
        "schema": RECEIPT_SCHEMA,
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
    tmp = path.with_name(f"{path.name}.{secrets.token_hex(8)}.tmp")
    try:
        tmp.write_text(json.dumps(receipt, indent=2), encoding="utf-8",
                       newline="\n")
        tmp.replace(path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
