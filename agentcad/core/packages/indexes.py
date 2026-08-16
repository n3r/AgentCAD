"""Index clients: one shape, three kinds — and the publisher.

An index is a **directory** holding `index.json` and one directory per
published package version. That is the whole format, and it is why a git repo
is an index: clone it and you have a local one. So :class:`GitIndex` is
*a local index plus a fetch step*, and a cloud index (PRD-005-lite) is the
same client over a different fetch — one implementation of parsing,
validating, searching and fetching, and the git-ness is one class.

**Writing** lives here too, because the index format is this module's:
`LocalIndex.publish` is fail-closed (a gate report that is `publishable`, a
tree that still hashes to what the gate measured, a redistributable vendor, a
version that does not already exist), `LocalIndex.yank` withdraws a version
without deleting a byte, and the module-level :func:`publish` runs the gate
over **every** stage first — it takes no stage subset precisely so a
`skip / not_selected` can never reach the verdict.

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

import hashlib
import json
import secrets
import shutil
from pathlib import Path

from ..model import ConflictError, NotFoundError, ValidationError
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

    # ------------------------------------------------------------ writing

    def publish(self, source, report: dict) -> dict:
        """Copy a **gate-approved** package into this index and record it.

        Fail-closed, in this order, because each refusal must happen before
        the one after it can do damage:

        1. the report is a gate report and it is `publishable` — otherwise a
           `validation_error` carrying the failing rows in `details.checks`,
           PRD-004's shape;
        2. the tree still hashes to the id the report measured. The gate
           re-reads the package after its own stages, but *this* is the window
           nobody else closes — the report is finished and then the tree
           moves. Publishing then would attest a content id nobody measured;
        3. the vendor gate (FR13): a package whose
           `provenance.vendor.redistributable` is `false` may not enter an
           index whose scope is `public`;
        4. **immutability** (FR10): an existing `name@version` — in the
           document *or* on disk — is a `conflict_error` naming it, **even
           when the content id is identical**. A byte comparison would let a
           publisher redefine "identical" later.

        Only then are bytes written: the inventoried files into a staging
        directory and `os.replace`d into `<index>/<name>/<version>/`, then
        `index.json` rewritten atomically. A document the format validator
        would refuse rolls the tree back, so a failed publish leaves the index
        exactly as it was.
        """
        from . import gate as gate_module

        source = Path(source).expanduser().resolve()
        problems = gate_module.validate_gate_report(report)
        if problems:
            raise ValidationError(
                "publish needs a gate report and this is not one: "
                + "; ".join(problems[:3]), {"problems": problems})
        name = report["package"]["name"]
        version = report["package"]["version"]
        if not (isinstance(name, str) and format.NAME_RE.match(name)
                and isinstance(version, str)
                and format.VERSION_RE.match(version)):
            raise ValidationError(
                f"the gate report names {name!r}@{version!r}, which is not a "
                f"publishable package identity")
        if not report.get("publishable"):
            blockers = report.get("blockers") or []
            failing = [item for stage in report.get("stages") or []
                       for item in stage.get("items") or []
                       if item.get("status") in ("fail", "error")
                       or (item.get("status") == "skip"
                           and item.get("id") in blockers)]
            raise ValidationError(
                f"{name}@{version} did not pass the publish gate "
                f"({len(blockers)} blocker(s): {', '.join(blockers[:5])}"
                + (" …" if len(blockers) > 5 else "") + "). Nothing was "
                "published — fix the package and run `agentcad package "
                "validate` until it is green.",
                {"package": name, "version": version, "blockers": blockers,
                 "checks": failing})

        measured = report["package"].get("content_id")
        actual = content.content_id(source)
        if actual != measured:
            raise ValidationError(
                f"{name}@{version} changed between the gate and the publish: "
                f"the report measured {measured}, the directory now hashes to "
                f"{actual}. Nothing was published — re-run the gate, because "
                f"the evidence describes a tree that is no longer on disk.",
                {"package": name, "version": version, "measured": measured,
                 "actual": actual, "path": str(source)})

        doc = _read_json(source / "package.json", "package.json")
        self._refuse_non_redistributable(doc, name, version)
        entries = content.inventory(source)
        ceilings = content.check_ceilings(entries)
        if ceilings:
            raise ValidationError(
                f"{name}@{version} exceeds the published package ceilings: "
                + "; ".join(problem["message"] for problem in ceilings),
                {"problems": ceilings})

        index_doc = self.entries()
        relpath = f"{name}/{version}"
        target = self.path / name / version
        if version in self.versions(name):
            raise ConflictError(
                f"{name}@{version} is already published in index "
                f"{self.name!r}. A version is IMMUTABLE — republishing is "
                f"refused even when the content id is identical, because a "
                f"byte comparison would let a publisher redefine 'identical' "
                f"later. Bump the version.",
                {"index": self.name, "package": name, "version": version})
        if target.exists():
            raise ConflictError(
                f"index {self.name!r} already holds a directory at {relpath} "
                f"with no entry in index.json — a half-published version. "
                f"Nothing was written: inspect {target} and remove it by "
                f"hand if it is debris.",
                {"index": self.name, "path": str(target)})

        entry = _index_entry(report, doc, source, relpath, actual)
        staging = self.path / name / f".staging-{secrets.token_hex(8)}"
        try:
            _copy_inventory(source, staging, entries)
            staging.replace(target)
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        try:
            packages = json.loads(json.dumps(index_doc.get("packages") or {}))
            packages.setdefault(name, {"versions": {}})
            packages[name]["versions"][version] = entry
            written = {**index_doc,
                       "packages": {key: packages[key]
                                    for key in sorted(packages)}}
            problems = format.validate_index(written)
            if problems:
                raise ValidationError(
                    f"publishing {name}@{version} would make index "
                    f"{self.name!r} invalid: "
                    + "; ".join(p["message"] for p in problems[:3]),
                    {"index": self.name, "problems": problems})
            self._write_index(written)
        except BaseException:
            # The tree is only reachable through the document, so removing it
            # is what "nothing was published" means.
            shutil.rmtree(target, ignore_errors=True)
            raise
        return {"index": self.name, "published": f"{name}@{version}",
                "path": str(target), "entry": entry,
                "content_id": actual}

    def yank(self, name: str, version: str, yanked: bool = True) -> dict:
        """Flip `yanked` on a published version. **Deletes nothing.**

        A lockfile naming it keeps resolving (`use_part` never resolves at
        all), a fresh requirement never selects it, and `add_package` on an
        explicitly-named yanked version warns and proceeds. Reversible on
        purpose: an over-eager yank must not need a version bump to undo.
        """
        doc = self.entries()
        entry = ((doc.get("packages") or {}).get(name) or {}).get(
            "versions", {}).get(version)
        if not isinstance(entry, dict):
            raise NotFoundError(
                f"index {self.name!r} has no {name}@{version} to yank",
                {"index": self.name, "package": name, "version": version})
        already = bool(entry.get("yanked")) == bool(yanked)
        written = json.loads(json.dumps(doc))
        written["packages"][name]["versions"][version]["yanked"] = bool(yanked)
        self._write_index(written)
        return {"index": self.name, "package": name, "version": version,
                "yanked": bool(yanked), "already": already}

    def _refuse_non_redistributable(self, doc, name, version) -> None:
        """FR13's confinement, and the reason it is a *mechanism*: a flag the
        publisher checks, not a label nobody enforces."""
        vendor = (doc.get("provenance") or {}).get("vendor") \
            if isinstance(doc.get("provenance"), dict) else None
        if not isinstance(vendor, dict):
            return
        if vendor.get("redistributable") is False and self.scope == "public":
            raise ValidationError(
                f"{name}@{version} declares vendor "
                f"{vendor.get('name')!r} with redistributable: false, and "
                f"index {self.name!r} has scope 'public'. Publish it to an "
                f"index with scope 'private' instead — the flag is what "
                f"confines vendor-derived geometry, and it is checked here "
                f"rather than trusted.",
                {"index": self.name, "scope": self.scope, "package": name,
                 "version": version, "vendor": vendor.get("name")})

    def _write_index(self, doc: dict) -> None:
        from ..project import ProjectStore

        ProjectStore._atomic_write(
            self.index_file, (json.dumps(doc, indent=2) + "\n").encode())
        self._cache = None      # the stamp changed; do not serve the old doc


def publish(index, source, service, *, jobs=None, work_dir=None,
            budget_s=None) -> dict:
    """Run the gate over ``source`` and publish it to ``index``.

    `agentcad package validate` is **report-honest** and this is
    **fail-closed** — PRD-004's exact split between `check` and the gates, for
    the same reason: two audiences, one set of measurements. So publish always
    runs **every** stage; it takes no stage subset, precisely so a
    `skip / not_selected` can never reach the verdict.
    """
    from .gate import GATE_STAGES, PackageGate

    report = PackageGate(service).run(source, stages=GATE_STAGES, jobs=jobs,
                                      work_dir=work_dir, budget_s=budget_s)
    result = index.publish(source, report)
    result["report"] = report
    return result


# ------------------------------------------------------- publishing helpers


def _read_json(path: Path, what: str) -> dict:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        raise ValidationError(f"{what} is unreadable: {exc}") from exc
    if not isinstance(doc, dict):
        raise ValidationError(f"{what} must be a JSON object")
    return doc


def _copy_inventory(src: Path, dst: Path, entries) -> None:
    """Exactly the inventoried files — `cache._copy_inventory`'s rule, for the
    same reason: the index must hold the tree the content id describes and
    nothing else, so an ignored file never lands there and a symlink
    structurally cannot."""
    dst.mkdir(parents=True, exist_ok=True)
    for relpath, _size, _sha in entries:
        target = dst / relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src / relpath, target)


def _index_entry(report: dict, doc: dict, source: Path, relpath: str,
                 content_id: str) -> dict:
    """The published entry.

    Everything a consumer can act on without downloading: the digest is
    **derived from the gate's own measurements** (the inspected PARAMS spec,
    the connectors the kernel reported, the specs that ran), never
    hand-written, and `gate.exempt_skips` records what was *not* measured —
    which is what stops "validated" from becoming a badge.
    """
    build123d = (report.get("host") or {}).get("build123d")
    if not isinstance(build123d, str) or not build123d:
        raise ValidationError(
            "the gate report does not name the build123d version it measured "
            "against, so the index entry cannot declare what this package was "
            "PROVED against. Re-run the gate on a machine whose kernel "
            "answers `ping`.",
            {"host": report.get("host")})
    entry = {
        "content_id": content_id,
        "path": relpath,
        "summary": doc.get("summary"),
        "license": doc.get("license"),
        "disclosure": doc.get("disclosure"),
        "parts": _parts_digest(report, doc),
        "presets": _preset_names(source),
        "previews": _preview_paths(source),
        "gate": {"status": report.get("status"),
                 "exempt_skips": list(report.get("exempt_skips") or []),
                 "agentcad": report.get("agentcad"),
                 "build123d": build123d,
                 "report_id": _report_id(report)},
        "yanked": False,
        # RESERVED (PRD-031 FR2(d) signs the content id). Present-and-empty,
        # so a reader can tell "nothing signed it" from "this format cannot
        # express a signature".
        "signatures": [],
    }
    for key in ("keywords", "standards", "min_agentcad"):
        if doc.get(key) is not None:
            entry[key] = doc[key]
    return entry


def _report_id(report: dict) -> str:
    """`sha256:` over the canonical JSON of the report, minus nothing.

    It is an identity, not a signature: two consumers holding the same report
    can tell it is the same one, and a report that was edited no longer
    matches the id the index published.
    """
    body = json.dumps(report, sort_keys=True, separators=(",", ":"),
                      default=str).encode("utf-8")
    return "sha256:" + hashlib.sha256(body).hexdigest()


def _parts_digest(report: dict, doc: dict) -> dict:
    out = {}
    for part_id in sorted((doc.get("parts") or {})):
        out[part_id] = {"params": _digest_params(report, part_id),
                        "connectors": _digest_connectors(report, part_id),
                        "specs": _digest_specs(report, part_id)}
    return out


def _stage(report: dict, name: str) -> dict:
    for stage in report.get("stages") or []:
        if stage.get("name") == name:
            return stage
    return {}


def _digest_params(report: dict, part_id: str) -> list:
    """The inspected PARAMS spec, as the `contract` stage measured it."""
    params = []
    prefix = f"{part_id}."
    for item in _stage(report, "contract").get("items") or []:
        subject = str(item.get("subject") or "")
        if item.get("kind") != "check" or not subject.startswith(prefix):
            continue
        details = item.get("details") or {}
        spec = {"name": subject[len(prefix):],
                "type": details.get("type") or "number"}
        for key in ("min", "max", "unit", "choices"):
            if details.get(key) is not None:
                spec[key] = details[key]
        params.append(spec)
    return params


def _digest_connectors(report: dict, part_id: str) -> dict:
    for item in _stage(report, "connectors").get("items") or []:
        if item.get("kind") == "part" and item.get("subject") == part_id:
            return dict((item.get("details") or {}).get("connectors") or {})
    return {}


def _digest_specs(report: dict, part_id: str) -> list:
    """The spec NAMES that actually ran for this part, deduplicated across its
    variants — a row's subject is `<variant>:<name>` and its
    `details.part` is the variant, so the two are separated exactly rather
    than by guessing where the colon is."""
    names = set()
    for item in _stage(report, "specs").get("items") or []:
        variant = str((item.get("details") or {}).get("part") or "")
        subject = str(item.get("subject") or "")
        if not variant or not subject.startswith(variant + ":"):
            continue
        if variant.split("@", 1)[0] != part_id:
            continue
        names.add(subject[len(variant) + 1:])
    return sorted(names)


def _preset_names(source: Path) -> list:
    """`<part>.<config>`, sorted. Qualified by part on purpose: two parts may
    legitimately ship a configuration with the same name, and `use_part` takes
    a part and a preset."""
    path = source / "presets.json"
    if not path.is_file():
        return []
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return []
    presets = doc.get("presets") if isinstance(doc, dict) else None
    out = []
    for part_id, configs in (presets or {}).items():
        if isinstance(configs, dict):
            out += [f"{part_id}.{config}" for config in configs]
    return sorted(out)


def _preview_paths(source: Path) -> list:
    directory = source / "previews"
    if not directory.is_dir():
        return []
    return sorted(f"previews/{path.name}" for path in directory.glob("*.png"))


class GitIndex(LocalIndex):
    """A repository at a pinned ref, checked out under
    `~/.agentcad/indexes/<name>/`.

    **A git index is a local index plus a fetch, and that reduction is the
    design**: `refresh()` clones on first use and afterwards fetches and hard-
    resets to the ref, and everything after that — parsing, validating,
    searching, fetching a package directory — is :class:`LocalIndex` over the
    checkout. The git-ness is this class.

    **Failure is data** (design Decision 7). An unreachable remote is a
    *warning*, not an exception: the last good checkout keeps answering,
    ``stale`` becomes true and ``stale_reason`` says why, and every search hit
    carries both. A remote that was **never** cloned is a `not_found_error`
    naming the URL, because there is nothing to answer *with*.

    ``refresh()`` fetches **once per client instance** unless forced. An index
    client is long-lived on the service, and a network fetch per keystroke in
    the Library dialog is not a search; `PackageManager.reload_indexes` mints
    new clients, and `refresh(force=True)` is the explicit re-fetch.
    """

    kind = "git"

    def __init__(self, name: str, url: str, ref: str = "main",
                 scope: str | None = None, root=None):
        from . import _git

        self.url = _git.validate_url(url)
        self.ref = ref if isinstance(ref, str) and ref else "main"
        root = Path(root) if root is not None else _git.indexes_root()
        super().__init__(name, root / name, scope=scope)
        self.stale = False
        self.stale_reason: str | None = None
        self._refreshed = False

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<GitIndex {self.name} {self.url}@{self.ref}>"

    def refresh(self, force: bool = False) -> None:
        from . import _git

        if self._refreshed and not force:
            return
        self._refreshed = True
        if not _git.available():
            self._go_stale("git is not on PATH")
            return
        try:
            if (self.path / ".git").exists():
                _git.fetch(self.path, self.url, self.ref)
            else:
                _git.clone(self.url, self.path, self.ref)
        except (_git.GitError, ValidationError) as exc:
            self._go_stale(str(exc))
            return
        self.stale = False
        self.stale_reason = None
        self._cache = None      # the checkout moved; do not serve the old doc

    def _go_stale(self, why: str) -> None:
        self.stale = True
        self.stale_reason = (
            f"could not refresh {self.url} at {self.ref}: {why}"
            + (" — answering from the last good checkout"
               if self.index_file.is_file() else ""))

    def entries(self) -> dict:
        try:
            return super().entries()
        except NotFoundError as exc:
            raise NotFoundError(
                f"index {self.name!r} has never been cloned from {self.url} "
                f"(ref {self.ref}), so there is nothing to read"
                + (f": {self.stale_reason}" if self.stale_reason else ""),
                {"index": self.name, "url": self.url, "ref": self.ref}
            ) from exc

    def source_of(self, entry: dict) -> dict:
        """What `packages_lock` records. The url and the ref are
        *configuration*, not machine facts, so two machines write the same
        bytes; the path stays index-relative for the same reason it does for a
        local index."""
        return {"kind": self.kind, "url": self.url, "ref": self.ref,
                "path": entry.get("path")}

    # A git index is READ-ONLY through this client. It inherits `publish` and
    # `yank` from `LocalIndex`, and both would write into a checkout that the
    # very next `refresh()` hard-resets — the publish would vanish with no
    # error, which is the worst shape a failure can take.
    def publish(self, source, report: dict) -> dict:
        raise self._read_only("publish to")

    def yank(self, name: str, version: str, yanked: bool = True) -> dict:
        raise self._read_only("yank in")

    def _read_only(self, verb: str) -> ValidationError:
        return ValidationError(
            f"cannot {verb} the git index {self.name!r}: this client's "
            f"checkout is hard-reset to {self.ref} on every refresh, so the "
            f"write would silently vanish. Publish into a clone you control "
            f"(a LOCAL index over your working tree) and push it — that is "
            f"what makes a repository an index.",
            {"index": self.name, "url": self.url, "ref": self.ref})


# Kinds this build can construct. `cloud` arrives with PRD-005-lite; until
# then a configuration naming one is skipped with a warning that says so,
# rather than silently ignored.
_PLANNED = {"cloud": "cloud indexes are not available in this build"}


def merge_bundled(config: dict | None, bundled) -> dict:
    """``config`` with ``bundled`` index entries **appended**.

    The app ships a catalog (`cli._register_catalog`) and it has to answer on
    a fresh install with no config file at all. Appended and never prepended:
    an index the user configured under the same name wins, which is the only
    precedence rule a bundled fallback can honestly have. With nothing bundled
    the document is returned unchanged, so every caller that has no bundled
    entries loads exactly what it loaded before.
    """
    doc = dict(config or {})
    entries = [dict(entry) for entry in (bundled or [])
               if isinstance(entry, dict)]
    if not entries:
        return doc
    declared = list(doc.get("indexes") or [])
    named = {entry.get("name") for entry in declared if isinstance(entry, dict)}
    extra = [entry for entry in entries if entry.get("name") not in named]
    if not extra:
        return doc
    doc["indexes"] = declared + extra
    return doc


def load_indexes(config: dict, warnings: list | None = None, *,
                 git_available=None) -> list:
    """Build the configured indexes, in precedence order.

    An entry that cannot be built is skipped and named in ``warnings``. Never
    raises: one broken index must not make the others unreachable.

    ``git_available`` is the probe for git — `PackageManager` passes
    `service.history.available`, which is already cached, so no new probe is
    made. With no git, **git indexes register nothing and say so**, exactly as
    the versioning and proposals packs self-disable.
    """
    from . import _git

    warn = warnings if warnings is not None else []
    has_git = _git.available if git_available is None else git_available
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
        if kind == "local":
            path = entry.get("path")
            if not isinstance(path, str) or not path:
                warn.append(f"index {name!r} (local) has no 'path'; skipped")
                continue
            seen.add(name)
            out.append(LocalIndex(name, path, scope=entry.get("scope")))
            continue
        if kind == "git":
            if not has_git():
                warn.append(f"index {name!r} is a git index and git is not on "
                            "PATH; skipped")
                continue
            ref = entry.get("ref", "main")
            if not isinstance(ref, str) or not ref:
                warn.append(f"index {name!r} (git) has a 'ref' that is not a "
                            f"non-empty string: {ref!r}; skipped")
                continue
            try:
                index = GitIndex(name, entry.get("url"), ref=ref,
                                 scope=entry.get("scope"))
            except ValidationError as exc:
                warn.append(f"index {name!r} (git): {exc.message}; skipped")
                continue
            seen.add(name)
            out.append(index)
            continue
        warn.append(f"index {name!r} has unknown kind {kind!r}; skipped")
    return out
