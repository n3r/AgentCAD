"""Tool pack: parts packages — search, install, materialise (PRD-011).

Installs one seam, ``service.packages``
(:class:`~agentcad.core.packages.manager.PackageManager`), exposes six tools,
and attaches provenance to ``get_part`` / ``get_project`` by **wrapping** the
bound methods (``tools_specs.install_rebuild_specs`` is the precedent).

**THIS PACK REGISTERS NO GATE PROVIDER — deliberately, permanently.**
``tools._load_tool_packs`` walks ``pkgutil.iter_modules`` **alphabetically**
and ``tools_proposals.py:51`` assigns ``service.gate_providers = []``
**unconditionally**. ``tools_packages`` sorts at ``pac``, which is *before*
``pro`` — so anything this module appended to ``gate_providers`` would be
silently discarded: no error, no warning, no gate. That is the trap that
forced ``tools_run_checks.py`` over ``tools_checks.py``, and a test in
``tests/test_packages_tools.py`` pins the prohibition.

The publish gate is not a merge gate: it gates ``publish``, a CLI action on a
*directory*, not a proposal merge. A materialised package part is an **ordinary
part**, so PRD-004's ``checks`` gate already rebuilds it, runs its specs and
re-resolves the assembly on every proposal. If a ``packages`` gate is ever
wanted (the plausible one: "every dependency in this proposal is locked and
verifies"), **the escape hatch is named so nobody rediscovers it the hard
way**: it goes in a *second* pack called ``tools_publish.py`` (``pub`` sorts
after ``pro``), or is installed lazily from ``server/routes_packages.py``,
which is the ``routes_presence`` claim-guard precedent. It never goes in this
file.

**Nothing later is captured at registration.** At ``pac``, ``service.specs``
(``s``), ``service.branches`` (``v``), ``service.proposals`` and
``service.gate_providers`` (``p``) **do not exist**. Every one of them is read
*inside* a method, behind ``getattr(service, …, None)``.

**The publish gate is a CORRECTNESS gate, not a security boundary.** A package
is Python; ``use_part`` copies it into the project and the next rebuild
executes it in the kernel worker with the user's privileges. Every description
below that installs or runs package code says so (design Decision 11, places
2, 3 and 4).
"""

from __future__ import annotations

import functools
import json

from .model import ConflictError, NotFoundError, ValidationError
from .packages import cache, content, gate, lockfile, provenance, search
from .packages.manager import PackageManager
from .tools import Tool, schema

_PROJ = {"type": "string", "description": "Project name"}

#: Decision 11's sentence, shortened faithfully for a tool description.
_NON_CLAIM = (
    "The publish gate is a CORRECTNESS gate, not a security boundary: it "
    "proves that the geometry builds, that the specs pass and that the "
    "connectors mate, and nothing about intent. A package is Python and it "
    "runs in your kernel worker with your privileges. See docs/packages.md."
)

#: The five provenance statuses, quoted wherever one is returned.
_STATUSES = (
    "provenance status is computed on EVERY read, never stored: 'ok' (the "
    "lock has this package at this version, the script bytes match the "
    "header and the cached tree verifies), 'modified' (you edited the script "
    "— legitimate, reported, never repaired), 'version_drift' (the lock holds "
    "another version), 'removed' (no dependency entry — a WARNING, not "
    "breakage: the part is a project file and it still builds) and "
    "'unverified' (we did not look — no cache entry to compare against, which "
    "is not 'fine')."
)

# The wrapper marker. An attribute on the method rather than a flag on the
# service, so "is this already wrapped?" is answered by the method itself.
_WRAPPED = "_agentcad_packages_wrapper"


# ------------------------------------------------------------ the wrappers


def install_provenance(service) -> None:
    """Attach package provenance to ``get_part`` and ``get_project``.

    **Why a wrapper and not a ``service.py`` edit.** The extension-point
    contract forbids editing the service core to add a feature;
    ``tools_specs.install_rebuild_specs`` and
    ``tools_versioning.install_write_guard`` are the precedent.

    Composes with ``tools_specs``, which loads at ``s`` and wraps *after* us,
    so its wrapper calls ours. Idempotent by attribute marker, because
    ``build_registry`` may run twice over one service and a second wrapper
    would re-read every script.

    **Zero kernel calls on both paths**, and a project that declares no
    packages pays a dict lookup: the manifest key is absent, so nothing is
    read, tokenized or hashed (FR15 is structural, not careful).
    """
    get_part = service.get_part
    if not getattr(get_part, _WRAPPED, False):

        @functools.wraps(get_part)
        def _get_part(proj: str, part_id: str) -> dict:
            detail = get_part(proj, part_id)
            if not isinstance(detail, dict):
                return detail
            script = detail.get("script")
            head = provenance.parse(script) if isinstance(script, str) else None
            # Present-and-null, never absent: "this part came from no package"
            # is an answer, and a missing key would read as "not evaluated".
            detail["package_provenance"] = (
                None if head is None
                else provenance.describe(head, service.store.manifest(proj),
                                         script))
            return detail

        setattr(_get_part, _WRAPPED, True)
        service.get_part = _get_part

    get_project = service.get_project
    if not getattr(get_project, _WRAPPED, False):

        @functools.wraps(get_project)
        def _get_project(proj: str) -> dict:
            payload = get_project(proj)
            if isinstance(payload, dict):
                payload["packages"] = _packages_summary(service, proj)
            return payload

        setattr(_get_project, _WRAPPED, True)
        service.get_project = _get_project


def _packages_summary(service, proj: str) -> dict:
    """``{name: {version, provenance_ok}}`` — the summary, never the detail.

    The detail (the requirement, the index, the cache state, what is stale)
    lives in ``list_packages``; this is what a project header needs.
    ``provenance_ok`` is the conjunction of *the cached tree verifies* and
    *every materialised part attributed to this package reads ``ok``*, which
    is the one bit a caller acts on: "should I look closer?"
    """
    manifest = service.store.manifest(proj)
    lock = lockfile.read_lock(manifest)
    if not lock:
        return {}
    verify = provenance.memoized_verify()
    troubled = {row["package"] for row in
                provenance.scan(service.store, proj, verify=verify,
                                manifest=manifest)
                if row["status"] != "ok"}
    out = {}
    for name in sorted(lock):
        version = lock[name].get("version")
        ok = (name not in troubled
              and isinstance(version, str)
              and verify(name, version).get("status") == "ok")
        out[name] = {"version": version, "provenance_ok": bool(ok)}
    return out


# ------------------------------------------------------------ materialising


def _locked(service, proj: str, name: str) -> dict:
    """The lock entry for ``name``, or a fail-closed refusal.

    A package in ``packages`` with no ``packages_lock`` entry is a
    hand-edited manifest. Guessing a version there would be **inventing a
    dependency**, so the refusal names the fix instead.
    """
    manifest = service.store.manifest(proj)
    entry = lockfile.entry_for(manifest, name)
    if entry is not None:
        return entry
    if lockfile.requirement_for(manifest, name) is not None:
        raise ValidationError(
            f"{name!r} is declared in this project but has no packages_lock "
            f"entry, so there is no version to materialise. Run add_package "
            f"for {name!r} — guessing one would invent a dependency.",
            {"project": proj, "package": name})
    raise NotFoundError(
        f"{name!r} is not a package of this project "
        f"(installed: {sorted(lockfile.read(manifest))}). Run add_package "
        f"first.",
        {"project": proj, "package": name})


def _package_doc(tree, name: str) -> dict:
    try:
        doc = json.loads((tree / "package.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        raise ValidationError(
            f"the cached copy of {name} has an unreadable package.json: {exc}",
            {"package": name, "path": str(tree)}) from exc
    if not isinstance(doc, dict):
        raise ValidationError(f"the cached copy of {name} has a package.json "
                              f"that is not an object", {"package": name})
    return doc


def _preset_params(tree, name: str, part: str, preset: str | None) -> dict:
    if preset is None:
        return {}
    path = tree / "presets.json"
    doc = {}
    if path.is_file():
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeDecodeError) as exc:
            raise ValidationError(
                f"the cached copy of {name} has an unreadable presets.json: "
                f"{exc}", {"package": name}) from exc
    configs = ((doc.get("presets") or {}).get(part)
               if isinstance(doc, dict) else None)
    entry = configs.get(preset) if isinstance(configs, dict) else None
    if not isinstance(entry, dict):
        raise NotFoundError(
            f"{name} {part!r} has no preset {preset!r} "
            f"(declared: {sorted(configs or {})})",
            {"package": name, "part": part, "preset": preset})
    params = entry.get("params")
    return dict(params) if isinstance(params, dict) else {}


def materialize(service, proj: str, name: str, part: str, part_id: str,
                preset: str | None = None, params: dict | None = None) -> dict:
    """Copy a package part into the project, header first.

    **No index, no network, ever** — the lock says which version, the cache
    holds it, and :func:`cache.require` re-verifies the **whole tree** before a
    byte is copied. That unconditional re-verification is AC3's tamper half and
    AC4's offline half at once, and it is affordable because of the published
    ceilings (~1 ms on a realistic package).

    Re-materialising the same package part produces **byte-identical** bytes:
    the header carries no timestamp, no client id and no absolute path.
    """
    entry = _locked(service, proj, name)
    version = entry.get("version")
    if not isinstance(version, str):
        raise ValidationError(
            f"the lock entry for {name!r} names no version", {"package": name})
    try:
        service.store.get_part(proj, part_id)
    except NotFoundError:
        pass
    else:
        raise ConflictError(
            f"this project already has a part called {part_id!r}. Pass a "
            f"different part_id — materialising over it would destroy a part "
            f"you may have edited.",
            {"project": proj, "part_id": part_id})

    tree = cache.require(name, version)
    doc = _package_doc(tree, name)
    declared = (doc.get("parts") or {}).get(part) if isinstance(doc, dict) else None
    if not isinstance(declared, dict) or not isinstance(declared.get("file"), str):
        raise NotFoundError(
            f"{name}@{version} declares no part {part!r} "
            f"(declared: {sorted((doc.get('parts') or {}))})",
            {"package": name, "version": version, "part": part})
    source = content.resolve_within(tree, declared["file"],
                                    what=f"parts.{part}.file")
    try:
        body = source.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ValidationError(
            f"{name}@{version}: {declared['file']} cannot be read: {exc}",
            {"package": name, "part": part}) from exc

    overrides = _preset_params(tree, name, part, preset)
    overrides.update(params or {})
    head = provenance.header({
        "name": name, "version": version, "part": part, "preset": preset,
        "index": entry.get("index"), "content_id": entry.get("content_id"),
        "script_sha256": provenance.script_sha256(body)})
    # The public, locked, guarded path — `create_part` then `set_params` —
    # even though it builds twice (once at the package's defaults, once at the
    # preset's). `set_params` is what VALIDATES the preset's names and types
    # against the script's own PARAMS spec before a byte reaches the manifest,
    # and a package from an unvalidated local index is exactly the case that
    # needs it. One extra build, once per materialised part, is the price.
    service.create_part(proj, part_id,
                        label=declared.get("label") or part_id,
                        script=head + body)
    if overrides:
        try:
            service.set_params(proj, part_id, overrides)
        except BaseException:
            # Source nobody typed: a preset the part cannot accept must not
            # leave a half-materialised part behind — `script_blocks.
            # apply_generated_block`'s rule, for the same reason.
            try:
                service.delete_part(proj, part_id)
            except Exception:  # noqa: BLE001 — the original failure is the answer
                pass
            raise
    return service.get_part(proj, part_id)


# --------------------------------------------------------------- reporting


def list_installed(service, proj: str | None) -> dict:
    """What is installed, what the indexes are, and what we could not verify.

    Deliberately does **not** `refresh()` a git index: this is the call the
    UI makes on every project open, and a network fetch per keystroke is not
    a listing. ``latest`` therefore reports what the last refresh knows —
    `search_packages` is the surface that refreshes.
    """
    manager = service.packages
    indexes = manager.indexes
    listed = {
        "project": proj,
        "packages": {},
        "indexes": [{"name": index.name, "kind": index.kind,
                     "scope": index.scope,
                     "stale": bool(getattr(index, "stale", False)),
                     "stale_reason": getattr(index, "stale_reason", None)}
                    for index in indexes],
        "warnings": list(manager.warnings),
    }
    if proj is None:
        return listed
    manifest = service.store.manifest(proj)
    declared, lock = lockfile.read(manifest), lockfile.read_lock(manifest)
    verify = provenance.memoized_verify()
    rows = provenance.scan(service.store, proj, verify=verify,
                           manifest=manifest)
    for name in sorted(set(declared) | set(lock)):
        entry = lock.get(name) or {}
        version = entry.get("version")
        report = (verify(name, version) if isinstance(version, str)
                  else {"status": "missing"})
        latest = _latest_known(indexes, name)
        listed["packages"][name] = {
            "version": version,
            "version_req": (declared.get(name) or {}).get("version_req"),
            "index": entry.get("index"),
            "content_id": entry.get("content_id"),
            "source": entry.get("source"),
            "cache": report["status"],
            "cache_reason": report.get("reason"),
            "latest": latest,
            "stale": bool(latest and version and latest != version),
            "parts": sorted(row["part"] for row in rows
                            if row["package"] == name),
        }
    return listed


def _latest_known(indexes, name: str):
    """The highest non-yanked version any configured index carries, or
    ``None`` when none does — which is also the honest answer offline."""
    from .packages import format as pkgformat

    best = None
    for index in indexes:
        try:
            versions = index.versions(name)
        except (NotFoundError, ValidationError):
            continue
        found = pkgformat.resolve(versions, "*")
        if found and (best is None or pkgformat.compare(found, best) > 0):
            best = found
    return best


# ---------------------------------------------------------------- the pack


def register(registry, service) -> None:
    # Always constructed. Package resolution needs no git and no kernel, and
    # every service attribute this pack does not own is read inside a method.
    service.packages = PackageManager(service)
    install_provenance(service)

    def search_packages(query: str | None = None, index: str | None = None,
                        keywords: list | None = None,
                        standards: list | None = None,
                        param: dict | None = None,
                        limit: int | None = None) -> dict:
        manager = service.packages
        return search.search(manager.indexes, query=query, index=index,
                             keywords=keywords, standards=standards,
                             param=param,
                             limit=search.DEFAULT_LIMIT if limit is None
                             else limit)

    def add_package(project: str, name: str, version_req: str | None = None,
                    index: str | None = None) -> dict:
        return service.packages.add(project, name, version_req, index)

    def remove_package(project: str, name: str) -> dict:
        def affected():
            return sorted(row["part"] for row in
                          provenance.scan(service.store, project)
                          if row["package"] == name)

        return service.packages.remove(project, name, scan=affected)

    def list_packages(project: str | None = None) -> dict:
        return list_installed(service, project)

    def use_part(project: str, package: str, part: str, part_id: str,
                 preset: str | None = None, params: dict | None = None) -> dict:
        return materialize(service, project, package, part, part_id,
                           preset=preset, params=params)

    def validate_package(path: str, strict: bool = False,
                         stages: list | None = None, jobs: int | None = None,
                         work_dir: str | None = None,
                         budget_s: float | None = None) -> dict:
        return gate.PackageGate(service).run(
            path, stages=gate.GATE_STAGES if stages is None else stages,
            strict=strict, jobs=jobs, work_dir=work_dir, budget_s=budget_s)

    registry.register(Tool(
        "search_packages",
        "Search the configured package indexes. Runs entirely in the server "
        "process over parsed index.json documents — no kernel call, no "
        "download, no network beyond an index refresh. Structured filters: "
        "'keywords' and 'standards' are AND filters, and 'param' is {name, "
        "min?, max?} matching a part whose declared range for that parameter "
        "OVERLAPS the interval. Ranking is deterministic and explainable — "
        "exact name (100) > name prefix (80) > standards (70) > keyword (60) "
        "> summary substring (40) > part or param name (30), ties broken on "
        "name then version descending — and every hit carries 'why': a search "
        "you cannot explain is one you cannot correct. Returns {hits: "
        "[{name, version, index, kind, summary, keywords, standards, license, "
        "disclosure, parts, presets, previews, gate, score, why, stale}], "
        "semantic, semantic_reason, indexes, warnings}. SEMANTIC SEARCH IS "
        "OPTIONAL AND HONESTLY DEGRADED: this build registers no embedding "
        "provider, so 'semantic' is always false with reason "
        "'no_embedding_provider' — present-and-false so you can tell that "
        "keyword search is what you got. A yanked version is never a hit. A "
        "broken or unreachable index is a warning, never an error: one bad "
        "index must not make the others unsearchable.",
        schema({"query": {"type": "string",
                          "description": "Free text; omit to list"},
                "index": {"type": "string",
                          "description": "Restrict to one configured index"},
                "keywords": {"type": "array",
                             "description": "AND filter on keywords"},
                "standards": {"type": "array",
                              "description": "AND filter on standards"},
                "param": {"type": "object",
                          "description": "{name, min?, max?} range overlap"},
                "limit": {"type": "integer", "description": "Max hits (20)"}},
               []),
        search_packages,
    ))
    registry.register(Tool(
        "add_package",
        "Install a package into a project: resolve the requirement against "
        "the configured indexes in precedence order, verify the fetched "
        "tree's content id against the one the index declares, install it "
        "into the content-verified cache (~/.agentcad/packages), and record "
        "both manifest maps — 'packages' (what you asked for) and "
        "'packages_lock' (what you got). Neither map holds a timestamp, a "
        "path or any other machine fact, so two branches that add the same "
        "package write byte-identical entries and merge clean. version_req is "
        "X.Y.Z, ^X.Y.Z (>=X.Y.Z <X+1.0.0), ~X.Y.Z (>=X.Y.Z <X.Y+1.0) or * "
        "(the highest non-yanked); 'index' pins one index explicitly. OFFLINE "
        "IS NOT A SECOND ANSWER: with no index reachable this resolves from "
        "the cache and reconstructs a lock entry byte-identical to the one an "
        "online install would have written. A content-id mismatch installs "
        "NOTHING and names both ids — it never silently re-fetches. Returns "
        "{project, package, lock, cached, offline, tried, warnings}; an "
        "unresolvable name is a not_found_error naming every index tried and "
        "why each failed. " + _NON_CLAIM,
        schema({"project": _PROJ,
                "name": {"type": "string", "description": "Package name"},
                "version_req": {"type": "string",
                                "description": "X.Y.Z | ^X.Y.Z | ~X.Y.Z | *"},
                "index": {"type": "string",
                          "description": "Pin one configured index"}},
               ["project", "name"]),
        add_package,
    ))
    registry.register(Tool(
        "remove_package",
        "Drop a package from a project's 'packages' and 'packages_lock' maps. "
        "IT DOES NOT TOUCH ONE SCRIPT BYTE and it does not touch the cache. "
        "Parts you materialised from it keep building — they are ordinary "
        "project files — and their provenance simply starts reading "
        "'removed', which is FR6's warning and not breakage. (The header "
        "lives inside the script and the script text is the rebuild cache "
        "key, so rewriting headers to express a removal would re-key and "
        "rebuild every materialised part.) The cache is shared by every "
        "project, so it is left alone too. Returns {project, removed, "
        "materialized_parts} — the part ids whose provenance now reads "
        "'removed'.",
        schema({"project": _PROJ,
                "name": {"type": "string", "description": "Package name"}},
               ["project", "name"]),
        remove_package,
    ))
    registry.register(Tool(
        "list_packages",
        "List a project's installed packages and the configured indexes. Per "
        "package: {version, version_req, index, content_id, source, cache, "
        "cache_reason, latest, stale, parts}. 'cache' is a REAL "
        "re-verification of the cached tree — 'ok' | 'tampered' | 'missing' — "
        "not a receipt lookup, and a broken entry is reported rather than "
        "raised so one bad package never hides the rest. 'latest' is what the "
        "last index refresh knows (this call deliberately does not fetch; use "
        "search_packages to refresh), and 'stale' is latest != version. Omit "
        "'project' to list only the configured indexes. 'warnings' names any "
        "index configuration that could not be built — including git indexes "
        "on a machine with no git.",
        schema({"project": _PROJ}, []),
        list_packages,
    ))
    registry.register(Tool(
        "use_part",
        "Materialise a package part INTO the project as an ordinary part: "
        "the script is copied in under an immutable provenance header, so the "
        "project builds with no cache, no index and no network — which is "
        "what makes a package part work in CI, in a bare clone and in a "
        "proposal diff. THIS CALL NEVER TOUCHES AN INDEX OR THE NETWORK: it "
        "reads packages_lock, re-verifies the WHOLE cached tree (every time — "
        "a receipt is a claim, and the tampered file is the thing we are "
        "looking for), and copies. Optional 'preset' applies a shipped "
        "configuration's parameters and 'params' overrides them one by one. "
        "Re-materialising the same package part is byte-identical: the header "
        "carries no timestamp, no client id and no absolute path. Returns the "
        "ordinary get_part payload plus 'package_provenance' — " + _STATUSES +
        " A package declared with no packages_lock entry is REFUSED "
        "(fail-closed: guessing a version invents a dependency), a part_id "
        "already in the project is a conflict_error, and a cached tree that "
        "does not verify is a validation_error that repairs nothing. " +
        _NON_CLAIM,
        schema({"project": _PROJ,
                "package": {"type": "string", "description": "Package name"},
                "part": {"type": "string",
                         "description": "Part id inside the package"},
                "part_id": {"type": "string",
                            "description": "Id for the new project part"},
                "preset": {"type": "string",
                           "description": "A shipped configuration name"},
                "params": {"type": "object",
                           "description": "Parameter overrides"}},
               ["project", "package", "part", "part_id"]),
        use_part,
    ))
    registry.register(Tool(
        "validate_package",
        "Run the publish gate over a package DIRECTORY and return the report "
        "— no publish, no install, and no side effect outside the gate's own "
        "throwaway cell (it creates its own scratch project under a temp dir "
        "and never opens one of yours). Nine stages: format (the manifest, "
        "the ceilings, the docs floor, the shipped previews), contract (each "
        "part's PARAMS and build), presets (every shipped configuration "
        "against the inspected spec), build (every part at EACH parameter's "
        "own min and max plus every configuration — a sum, never the cross "
        "product), specs (PRD-003's checks over every variant), connectors "
        "(every declared connector mated in one assembly round trip), "
        "previews, docs and policy. Returns a PRD-004 report — {status, "
        "summary, stages[].items[], exit_code, warnings, errors} — plus "
        "'package', 'note' and the verdict {publishable, exempt_skips, "
        "blockers}. THIS IS THE AUTHORING LOOP: read details in "
        "stages[].items[], fix the package, validate again. 'stages' takes a "
        "subset for a fast iteration, and an unselected stage makes "
        "'publishable' false — a subset run did not look, so it cannot say "
        "the package is publishable. " + _NON_CLAIM,
        schema({"path": {"type": "string",
                         "description": "The package directory"},
                "strict": {"type": "boolean",
                           "description": "Count skips as failures"},
                "stages": {"type": "array",
                           "description": "Subset of the nine stages"},
                "jobs": {"type": "integer",
                         "description": "Parallel variant builds"},
                "work_dir": {"type": "string",
                             "description": "Where the throwaway cell goes"},
                "budget_s": {"type": "number",
                             "description": "Deadline in seconds"}},
               ["path"]),
        validate_package,
    ))
