"""The published documents: `package.json`, `index.json`, `presets.json`,
the version-requirement grammar, and the frozen configuration schema.

Everything here is a *published* contract. It will be depended upon by pinned
consumers, so two rules run through the whole module:

* **Unknown keys are problems, not ignored.** A format that silently swallows
  a typo teaches authors that the typo worked. `licence` is not `license`.
* **A validator returns problems; it does not raise.** One call reports
  everything wrong with a document, because the gate turns each problem into
  a report row and an author fixing them one exception at a time is the loop
  this feature exists to shorten. The one exception is the semver layer,
  where "this is not a version" has no partial answer.

Version grammar, hand-rolled (no new runtime dependency, on PRD-004's
precedent):

===============  =====================================================
`1.2.3`          exactly that version
`^1.2.3`         `>=1.2.3, <2.0.0`
`~1.2.3`         `>=1.2.3, <1.3.0`
`*` / omitted    the highest non-yanked version
===============  =====================================================

**`^0.x.y` is `>=0.x.y, <1.0.0` here.** npm's rule differs (it treats a `0.x`
caret as `~`), and a reader will assume npm — so it is stated here and pinned
by a test. Prereleases are out of v1 entirely: they need a precedence rule
(`1.0.0-rc.1 < 1.0.0`) and a policy on whether a range may resolve to one,
both of which can be added later by widening the grammar and neither of which
can be un-guessed once published.
"""

from __future__ import annotations

import re

from ..model import ID_RE, ValidationError
from .content import (IGNORED, is_content_id, is_ignored, is_safe_relpath,
                      problem, resolve_within)

PACKAGE_FORMAT = 1
INDEX_FORMAT = 1
PRESETS_FORMAT = 1

NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,39}$")
# Leading zeros are refused so that one version has exactly one spelling:
# "01.2.3" and "1.2.3" would otherwise be two index keys that `compare` calls
# equal. (The design spec writes the grammar as `\d+\.\d+\.\d+`; that is its
# shorthand, and semver's own grammar forbids the leading zero for this
# reason.)
VERSION_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
CONFIG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
# A package's part ids become project part ids on `use_part`, so they are the
# project's id grammar and not a second one.
PART_ID_RE = ID_RE

DISCLOSURES = ("human", "agent", "hybrid")
INDEX_SCOPES = ("public", "private")
GATE_STATUSES = ("green", "red", "skip")

#: A package part is a **script** (`parts/<id>.py`, the default and the only
#: kind before FR13) or a **reference** — an imported vendor solid under
#: `imports/`, which has no script at all. The two words are the project
#: manifest's own (`ProjectStore.add_part(kind=…, source=…)`), not a second
#: vocabulary: a materialised package part is an ordinary project part, so it
#: had better be described in the project's language.
PART_KINDS = ("script", "reference")
DEFAULT_PART_KIND = "script"

#: Where each kind's payload lives. A `file` outside `parts/` or a `source`
#: outside `imports/` is refused, so a package cannot declare a part that
#: reaches sideways into its own tree.
PART_FILE_DIR = "parts/"
PART_SOURCE_DIR = "imports/"

PACKAGE_REQUIRED = frozenset({
    "format", "name", "version", "summary", "license", "authors",
    "disclosure", "parts",
})
PACKAGE_OPTIONAL = frozenset({
    "keywords", "standards", "min_agentcad", "provenance", "remix", "requires",
})

INDEX_REQUIRED = frozenset({"format", "name", "scope", "packages"})
INDEX_OPTIONAL = frozenset({"embeddings"})

INDEX_ENTRY_REQUIRED = frozenset({
    "content_id", "path", "summary", "license", "disclosure", "parts",
    "presets", "previews", "gate", "yanked", "signatures",
})
INDEX_ENTRY_OPTIONAL = frozenset({"keywords", "standards", "min_agentcad"})

CONFIG_KEYS = ("params", "label", "description")

# The refusal an author of a PRD-012-flavoured flat config map has to read.
_FLAT_CONFIG_NOTE = (
    "a configuration wraps its parameters in 'params' — a flat "
    "{name: {param: value}} map is ambiguous, because a part may declare a "
    "parameter called 'label'"
)


# ------------------------------------------------------- package.json


def validate_package_manifest(doc, *, root=None) -> list[dict]:
    """Problems with a `package.json` document.

    ``root`` is the package directory: when given, every declared part file
    must exist inside it. Without it only the document's shape is checked —
    which is what a consumer reading an index entry can do.
    """
    if not isinstance(doc, dict):
        return [problem("wrong_type", "package.json must be a JSON object")]
    out: list[dict] = []
    out += _unknown_keys(doc, PACKAGE_REQUIRED | PACKAGE_OPTIONAL, "package.json")
    out += _missing(doc, PACKAGE_REQUIRED)

    out += _format_field(doc, "format", PACKAGE_FORMAT, "package.json")
    out += _pattern(doc, "name", NAME_RE, "a package name")
    out += _pattern(doc, "version", VERSION_RE, "a version (X.Y.Z)")
    out += _non_empty_str(doc, "summary")
    out += _list_of_str(doc, "keywords")
    out += _list_of_str(doc, "standards")
    out += _non_empty_str(doc, "license")
    out += _authors(doc)
    out += _enum(doc, "disclosure", DISCLOSURES)
    out += _pattern(doc, "min_agentcad", VERSION_RE, "a version (X.Y.Z)")
    out += _provenance(doc)
    out += _parts(doc, root)
    out += _remix(doc)
    out += _requires(doc)
    return out


def _authors(doc) -> list[dict]:
    value = doc.get("authors")
    if value is None and "authors" not in doc:
        return []
    if not isinstance(value, list) or not value:
        return [problem("wrong_type",
                        "authors must be a non-empty list of objects",
                        field="authors")]
    out = []
    for i, author in enumerate(value):
        field = f"authors[{i}]"
        if not isinstance(author, dict):
            # An author is an object, never a bare string: a string cannot
            # grow a verified identity or a publisher id without a migration.
            out.append(problem("wrong_type",
                               f"{field} must be an object with a 'name'",
                               field=field))
            continue
        out += _unknown_keys(author, {"name", "email", "url"}, field, prefix=field)
        out += _missing(author, {"name"}, prefix=field)
        out += _non_empty_str(author, "name", prefix=field)
        out += _non_empty_str(author, "email", prefix=field)
        out += _non_empty_str(author, "url", prefix=field)
    return out


def _provenance(doc) -> list[dict]:
    value = doc.get("provenance")
    if "provenance" not in doc or value is None:
        return []
    if not isinstance(value, dict):
        return [problem("wrong_type", "provenance must be an object",
                        field="provenance")]
    out = _unknown_keys(value, {"generator", "vendor"}, "provenance",
                        prefix="provenance")
    generator = value.get("generator")
    if generator is not None:
        if not isinstance(generator, dict):
            out.append(problem("wrong_type",
                               "provenance.generator must be an object",
                               field="provenance.generator"))
        else:
            out += _unknown_keys(generator, {"name", "version"},
                                 "provenance.generator",
                                 prefix="provenance.generator")
            out += _missing(generator, {"name", "version"},
                            prefix="provenance.generator")
            out += _non_empty_str(generator, "name", prefix="provenance.generator")
            out += _non_empty_str(generator, "version",
                                  prefix="provenance.generator")
    vendor = value.get("vendor")
    if vendor is not None:
        if not isinstance(vendor, dict):
            out.append(problem("wrong_type",
                               "provenance.vendor must be an object or null",
                               field="provenance.vendor"))
        else:
            keys = {"name", "part_number", "url", "terms", "redistributable"}
            out += _unknown_keys(vendor, keys, "provenance.vendor",
                                 prefix="provenance.vendor")
            out += _missing(vendor, {"name", "redistributable"},
                            prefix="provenance.vendor")
            for key in ("name", "part_number", "url", "terms"):
                out += _non_empty_str(vendor, key, prefix="provenance.vendor")
            # Mechanical, not a label: slice 8's publisher refuses a `false`
            # into a public index, so its type is enforced here.
            if "redistributable" in vendor and not isinstance(
                    vendor["redistributable"], bool):
                out.append(problem(
                    "wrong_type",
                    "provenance.vendor.redistributable must be true or false",
                    field="provenance.vendor.redistributable"))
    return out


def _parts(doc, root) -> list[dict]:
    value = doc.get("parts")
    if "parts" not in doc:
        return []
    if not isinstance(value, dict) or not value:
        return [problem("wrong_type",
                        "parts must be a non-empty object of part id -> entry",
                        field="parts")]
    out = []
    for part_id, entry in value.items():
        field = f"parts.{part_id}"
        if not PART_ID_RE.match(str(part_id)):
            out.append(problem(
                "bad_value",
                f"part id {part_id!r} must match {PART_ID_RE.pattern}",
                field=field))
        if not isinstance(entry, dict):
            out.append(problem("wrong_type", f"{field} must be an object",
                               field=field))
            continue
        out += _unknown_keys(entry, {"file", "label", "summary", "kind",
                                     "source"}, field, prefix=field)
        out += _non_empty_str(entry, "label", prefix=field)
        out += _non_empty_str(entry, "summary", prefix=field)
        out += _part_payload(entry, field, root)
    return out


def _part_payload(entry, field, root) -> list[dict]:
    """The one key a part kind is defined by, and nothing from the other kind.

    A script part carries `file`; a reference part (FR13) carries `source` and
    **must not** carry `file` — a reference part has no script, and an entry
    holding both would leave every reader to pick one.
    """
    kind = entry.get("kind", DEFAULT_PART_KIND)
    if kind not in PART_KINDS:
        return [problem(
            "bad_value",
            f"{field}.kind must be one of {list(PART_KINDS)}, got {kind!r}",
            field=f"{field}.kind")]
    key = "file" if kind == DEFAULT_PART_KIND else "source"
    other = "source" if key == "file" else "file"
    directory = PART_FILE_DIR if key == "file" else PART_SOURCE_DIR
    out = _missing(entry, {key}, prefix=field)
    if other in entry:
        out.append(problem(
            "bad_value",
            f"{field}.{other} is not a key of a {kind!r} part — a {kind} part "
            f"is declared by {field}.{key}",
            field=f"{field}.{other}"))
    value = entry.get(key)
    if value is None:
        return out
    if not isinstance(value, str):
        return out + [problem("wrong_type", f"{field}.{key} must be a string",
                              field=f"{field}.{key}")]
    if not is_safe_relpath(value) or not value.startswith(directory):
        return out + [problem(
            "bad_value",
            f"{field}.{key} must be a relative path inside {directory}: "
            f"{value!r}", field=f"{field}.{key}")]
    if is_ignored(value):
        # A part file the CONTENT ID does not cover is a part the package does
        # not ship: `content.IGNORED` excludes it from the inventory, so it is
        # not hashed, not copied into the cache and not published — while every
        # gate stage would happily read it off the author's disk and prove it.
        # The gate catches this against the inventory as well; refusing it here
        # means the manifest is wrong at the manifest layer, where the author
        # is looking.
        return out + [problem(
            "bad_value",
            f"{field}.{key} matches a path the content id ignores "
            f"({', '.join(IGNORED)}), so this file would not be part of the "
            f"package: {value!r}", field=f"{field}.{key}")]
    if root is None:
        return out
    try:
        path = resolve_within(root, value, what=f"{field}.{key}")
    except ValidationError as exc:
        return out + [problem("bad_value", str(exc), field=f"{field}.{key}")]
    if not path.is_file():
        out.append(problem(
            "missing_file",
            f"{field}.{key} does not exist in the package: {value}",
            field=f"{field}.{key}"))
    return out


def part_kind(entry) -> str:
    """``"script"`` | ``"reference"`` for a declared part entry.

    Absent means ``script``: every package published before FR13 declares no
    ``kind``, and their content ids may not move (a version is immutable).
    """
    kind = entry.get("kind") if isinstance(entry, dict) else None
    return kind if kind in PART_KINDS else DEFAULT_PART_KIND


def part_payload(entry) -> tuple[str, str] | tuple[None, None]:
    """``(key, relpath)`` — ``("file", "parts/x.py")`` or
    ``("source", "imports/x.step")`` — or ``(None, None)`` when the entry does
    not declare its own kind's key (which the validator has already reported)."""
    key = "file" if part_kind(entry) == DEFAULT_PART_KIND else "source"
    value = entry.get(key) if isinstance(entry, dict) else None
    return (key, value) if isinstance(value, str) else (None, None)


def _remix(doc) -> list[dict]:
    """RESERVED (design Decision 2): validated when present, never written by
    this feature. The slot has to exist now or a future fork is a format
    change that breaks every pinned consumer."""
    value = doc.get("remix")
    if "remix" not in doc or value is None:
        return []
    if not isinstance(value, dict):
        return [problem("wrong_type", "remix must be an object or null",
                        field="remix")]
    out = _unknown_keys(value, {"of"}, "remix", prefix="remix")
    out += _missing(value, {"of"}, prefix="remix")
    of = value.get("of")
    if of is None:
        return out
    if not isinstance(of, dict):
        return out + [problem("wrong_type", "remix.of must be an object",
                              field="remix.of")]
    out += _unknown_keys(of, {"name", "version", "content_id"}, "remix.of",
                         prefix="remix.of")
    out += _missing(of, {"name", "version", "content_id"}, prefix="remix.of")
    out += _pattern(of, "name", NAME_RE, "a package name", prefix="remix.of")
    out += _pattern(of, "version", VERSION_RE, "a version (X.Y.Z)",
                    prefix="remix.of")
    if "content_id" in of and not is_content_id(of["content_id"]):
        out.append(problem("bad_value",
                           "remix.of.content_id must be 'sha256:<64 hex>'",
                           field="remix.of.content_id"))
    return out


def _requires(doc) -> list[dict]:
    """RESERVED: `{"extras": ["fem"]}`. Nothing enforces it yet — the gate's
    exempt-skip rule reports a missing extra honestly instead."""
    value = doc.get("requires")
    if "requires" not in doc or value is None:
        return []
    if not isinstance(value, dict):
        return [problem("wrong_type", "requires must be an object or null",
                        field="requires")]
    out = _unknown_keys(value, {"extras"}, "requires", prefix="requires")
    out += _list_of_str(value, "extras", prefix="requires")
    return out


# ---------------------------------------------------------- index.json


def validate_index(doc) -> list[dict]:
    """Problems with an `index.json` document.

    An index is a *build product* of the publisher (slice 8), never
    hand-edited — which is exactly why it is validated on read: a hand-edit
    is what this catches.
    """
    if not isinstance(doc, dict):
        return [problem("wrong_type", "index.json must be a JSON object")]
    out = _unknown_keys(doc, INDEX_REQUIRED | INDEX_OPTIONAL, "index.json")
    out += _missing(doc, INDEX_REQUIRED)
    out += _format_field(doc, "format", INDEX_FORMAT, "index.json")
    out += _pattern(doc, "name", NAME_RE, "an index name")
    out += _enum(doc, "scope", INDEX_SCOPES)
    out += _embeddings(doc)

    packages = doc.get("packages")
    if "packages" in doc and not isinstance(packages, dict):
        return out + [problem("wrong_type", "packages must be an object",
                              field="packages")]
    for name, record in (packages or {}).items():
        field = f"packages.{name}"
        if not NAME_RE.match(str(name)):
            out.append(problem("bad_value",
                               f"package name {name!r} must match "
                               f"{NAME_RE.pattern}", field=field))
        if not isinstance(record, dict):
            out.append(problem("wrong_type", f"{field} must be an object",
                               field=field))
            continue
        out += _unknown_keys(record, {"versions"}, field, prefix=field)
        out += _missing(record, {"versions"}, prefix=field)
        versions = record.get("versions")
        if versions is None:
            continue
        if not isinstance(versions, dict):
            out.append(problem("wrong_type", f"{field}.versions must be an "
                               "object of version -> entry",
                               field=f"{field}.versions"))
            continue
        for version, entry in versions.items():
            vfield = f"{field}.versions.{version}"
            if not VERSION_RE.match(str(version)):
                out.append(problem(
                    "bad_value",
                    f"{vfield}: {version!r} is not a version (X.Y.Z)",
                    field=vfield))
            out += _index_entry(entry, vfield)
    return out


def _index_entry(entry, field) -> list[dict]:
    if not isinstance(entry, dict):
        return [problem("wrong_type", f"{field} must be an object", field=field)]
    out = _unknown_keys(entry, INDEX_ENTRY_REQUIRED | INDEX_ENTRY_OPTIONAL,
                        field, prefix=field)
    out += _missing(entry, INDEX_ENTRY_REQUIRED, prefix=field)
    if "content_id" in entry and not is_content_id(entry["content_id"]):
        out.append(problem(
            "bad_value",
            f"{field}.content_id must be 'sha256:<64 lowercase hex>', "
            f"got {entry['content_id']!r}",
            field=f"{field}.content_id"))
    path = entry.get("path")
    if "path" in entry and (not isinstance(path, str) or not is_safe_relpath(path)):
        out.append(problem(
            "bad_value",
            f"{field}.path must be a relative path inside the index: {path!r}",
            field=f"{field}.path"))
    out += _non_empty_str(entry, "summary", prefix=field)
    out += _non_empty_str(entry, "license", prefix=field)
    out += _enum(entry, "disclosure", DISCLOSURES, prefix=field)
    out += _pattern(entry, "min_agentcad", VERSION_RE, "a version (X.Y.Z)",
                    prefix=field)
    out += _list_of_str(entry, "keywords", prefix=field)
    out += _list_of_str(entry, "standards", prefix=field)
    out += _list_of_str(entry, "presets", prefix=field)
    out += _list_of_str(entry, "previews", prefix=field)
    out += _parts_digest(entry.get("parts"), f"{field}.parts")
    out += _gate_record(entry.get("gate"), f"{field}.gate")
    if "yanked" in entry and not isinstance(entry["yanked"], bool):
        out.append(problem("wrong_type", f"{field}.yanked must be true or false",
                           field=f"{field}.yanked"))
    # RESERVED (031 FR2(d) signs the content id). Present-and-empty, so that a
    # reader can tell "this format has a slot and nothing signed it" from
    # "this format cannot express a signature".
    if "signatures" in entry:
        signatures = entry["signatures"]
        if not isinstance(signatures, list):
            out.append(problem("wrong_type", f"{field}.signatures must be a list",
                               field=f"{field}.signatures"))
        elif signatures:
            out.append(problem(
                "bad_value",
                f"{field}.signatures is reserved and must be empty",
                field=f"{field}.signatures"))
    return out


def _parts_digest(parts, field) -> list[dict]:
    """The digest that lets an agent pick a package without downloading it.
    Derived at publish from the gate's own measurements, never hand-written."""
    if parts is None:
        return []
    if not isinstance(parts, dict):
        return [problem("wrong_type", f"{field} must be an object", field=field)]
    out = []
    for part_id, digest in parts.items():
        pfield = f"{field}.{part_id}"
        if not isinstance(digest, dict):
            out.append(problem("wrong_type", f"{pfield} must be an object",
                               field=pfield))
            continue
        out += _unknown_keys(digest, {"params", "connectors", "specs"}, pfield,
                             prefix=pfield)
        params = digest.get("params")
        if params is not None:
            if not isinstance(params, list):
                out.append(problem("wrong_type", f"{pfield}.params must be a list",
                                   field=f"{pfield}.params"))
            else:
                for i, spec in enumerate(params):
                    sfield = f"{pfield}.params[{i}]"
                    if not isinstance(spec, dict):
                        out.append(problem("wrong_type",
                                           f"{sfield} must be an object",
                                           field=sfield))
                        continue
                    out += _unknown_keys(
                        spec,
                        {"name", "type", "default", "min", "max", "unit",
                         "description", "choices", "max_len"},
                        sfield, prefix=sfield)
                    out += _missing(spec, {"name", "type"}, prefix=sfield)
        connectors = digest.get("connectors")
        if connectors is not None:
            if not isinstance(connectors, dict) or not all(
                isinstance(k, str) and isinstance(v, str)
                for k, v in connectors.items()
            ):
                out.append(problem(
                    "wrong_type",
                    f"{pfield}.connectors must map connector name -> kind",
                    field=f"{pfield}.connectors"))
        out += _list_of_str(digest, "specs", prefix=pfield)
    return out


def _gate_record(gate, field) -> list[dict]:
    """What was MEASURED, including which skips were exempted — that is what
    stops "validated" from becoming a badge."""
    if gate is None:
        return []
    if not isinstance(gate, dict):
        return [problem("wrong_type", f"{field} must be an object", field=field)]
    keys = {"status", "exempt_skips", "agentcad", "build123d", "report_id"}
    out = _unknown_keys(gate, keys, field, prefix=field)
    out += _missing(gate, keys, prefix=field)
    out += _enum(gate, "status", GATE_STATUSES, prefix=field)
    out += _list_of_str(gate, "exempt_skips", prefix=field)
    out += _non_empty_str(gate, "agentcad", prefix=field)
    out += _non_empty_str(gate, "build123d", prefix=field)
    if "report_id" in gate and not is_content_id(gate["report_id"]):
        out.append(problem("bad_value",
                           f"{field}.report_id must be 'sha256:<64 hex>'",
                           field=f"{field}.report_id"))
    return out


def _embeddings(doc) -> list[dict]:
    """Optional and honestly degraded (design Decision 8): MVP registers no
    provider, so this is `null` in every index this feature writes."""
    value = doc.get("embeddings")
    if "embeddings" not in doc or value is None:
        return []
    if not isinstance(value, dict):
        return [problem("wrong_type", "embeddings must be an object or null",
                        field="embeddings")]
    out = _unknown_keys(value, {"model", "dim", "vectors"}, "embeddings",
                        prefix="embeddings")
    out += _missing(value, {"model", "dim", "vectors"}, prefix="embeddings")
    out += _non_empty_str(value, "model", prefix="embeddings")
    if "dim" in value and (not isinstance(value["dim"], int)
                           or isinstance(value["dim"], bool)
                           or value["dim"] <= 0):
        out.append(problem("bad_value", "embeddings.dim must be a positive int",
                           field="embeddings.dim"))
    if "vectors" in value and not isinstance(value["vectors"], dict):
        out.append(problem("wrong_type", "embeddings.vectors must be an object",
                           field="embeddings.vectors"))
    return out


# -------------------------------------------- configurations and presets


def validate_configuration(entry, params_spec) -> list[dict]:
    """Problems with ONE configuration — `{params, label?, description?}`.

    This is the schema PRD-012's `parts.<id>.configs` must adopt entry for
    entry (design Decision 4): one object, one validator, one word for it.
    `preset` names only *where* a configuration lives.

    ``params_spec`` is the normalized PARAMS spec from the kernel's `inspect`
    (`{name: {type, default, min, max, unit, description, choices?,
    max_len?}}`) and may be ``None``, which checks the entry's shape only —
    the gate passes the real spec once `inspect` has run.
    """
    if not isinstance(entry, dict):
        return [problem("wrong_type", "a configuration must be an object "
                        f"{{{', '.join(CONFIG_KEYS)}}}")]
    out = _unknown_keys(entry, set(CONFIG_KEYS), "configuration",
                        note=_FLAT_CONFIG_NOTE)
    out += _missing(entry, {"params"})
    out += _non_empty_str(entry, "label")
    out += _non_empty_str(entry, "description")

    params = entry.get("params")
    if "params" not in entry:
        return out
    if not isinstance(params, dict):
        return out + [problem("wrong_type",
                              "configuration params must be an object of "
                              "parameter -> value", field="params")]
    for name, value in params.items():
        field = f"params.{name}"
        if not _is_json_scalar(value):
            out.append(problem(
                "wrong_type",
                f"{field} must be a JSON scalar (number, string or bool), "
                f"got {type(value).__name__}", field=field))
            continue
        if params_spec is None:
            continue
        spec = params_spec.get(name) if isinstance(params_spec, dict) else None
        if spec is None:
            out.append(problem(
                "bad_value",
                f"unknown parameter {name!r} — the part declares "
                f"{sorted(params_spec) if isinstance(params_spec, dict) else []}",
                field=field))
            continue
        out += _against_spec(name, value, spec, field)
    return out


def validate_configurations(configs, params_spec) -> list[dict]:
    """Problems with a whole ``configs`` map — ``parts.<id>.configs`` (PRD-012).

    Name grammar (``CONFIG_RE``) per key, then ``validate_configuration`` per
    entry with each field re-prefixed ``configs.<name>.<field>``. The presets
    loop below is the same loop over a different container, so the two cannot
    drift on what a configuration is; only the field prefix differs.

    ``params_spec`` is the kernel-normalized PARAMS spec (or ``None`` for a
    shape-only check), exactly as ``validate_configuration`` takes it.
    """
    if not isinstance(configs, dict):
        return [problem("wrong_type",
                        "configs must be an object of name -> configuration")]
    out = []
    for name, entry in configs.items():
        cfield = f"configs.{name}"
        if not CONFIG_RE.match(str(name)):
            out.append(problem(
                "bad_value",
                f"configuration name {name!r} must match {CONFIG_RE.pattern}",
                field=cfield))
        for item in validate_configuration(entry, params_spec):
            item = dict(item)
            item["field"] = (
                f"{cfield}.{item['field']}" if item.get("field") else cfield
            )
            out.append(item)
    return out


def validate_presets(doc, parts) -> list[dict]:
    """Problems with a `presets.json` document.

    ``parts`` is the package's declared part ids: a preset naming a part the
    package does not ship is a problem, not a silently dead entry.
    """
    if not isinstance(doc, dict):
        return [problem("wrong_type", "presets.json must be a JSON object")]
    known = set(parts or ())
    out = _unknown_keys(doc, {"format", "presets"}, "presets.json")
    out += _missing(doc, {"format", "presets"})
    out += _format_field(doc, "format", PRESETS_FORMAT, "presets.json")
    presets = doc.get("presets")
    if "presets" not in doc:
        return out
    if not isinstance(presets, dict):
        return out + [problem("wrong_type",
                              "presets must be an object of part -> "
                              "configurations", field="presets")]
    for part_id, configs in presets.items():
        field = f"presets.{part_id}"
        if part_id not in known:
            out.append(problem(
                "bad_value",
                f"preset part {part_id!r} is not declared in package.json "
                f"(declares {sorted(known)})", field=field))
        if not isinstance(configs, dict):
            out.append(problem("wrong_type", f"{field} must be an object of "
                               "name -> configuration", field=field))
            continue
        for name, entry in configs.items():
            cfield = f"{field}.{name}"
            if not CONFIG_RE.match(str(name)):
                out.append(problem(
                    "bad_value",
                    f"configuration name {name!r} must match "
                    f"{CONFIG_RE.pattern}", field=cfield))
            for item in validate_configuration(entry, None):
                item = dict(item)
                item["field"] = (
                    f"{cfield}.{item['field']}" if item.get("field") else cfield
                )
                out.append(item)
    return out


def _against_spec(name, value, spec, field) -> list[dict]:
    ptype = spec.get("type", "number")
    if ptype == "bool":
        if not isinstance(value, bool):
            return [problem("wrong_type", f"{field} must be true or false",
                            field=field)]
        return []
    if ptype == "string":
        if not isinstance(value, str):
            return [problem("wrong_type", f"{field} must be a string",
                            field=field)]
        max_len = spec.get("max_len")
        if isinstance(max_len, int) and len(value) > max_len:
            return [problem("bad_value",
                            f"{field} is longer than max_len {max_len}",
                            field=field)]
        return []
    if ptype == "enum":
        choices = spec.get("choices") or []
        if not any(_same_scalar(value, choice) for choice in choices):
            return [problem("bad_value",
                            f"{field}: {value!r} is not one of {choices!r}",
                            field=field)]
        return []
    # number / int
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        # JSON says True == 1; the params contract does not.
        return [problem("wrong_type", f"{field} must be a number", field=field)]
    if ptype == "int" and float(value) != int(value):
        return [problem("wrong_type", f"{field} must be a whole number",
                        field=field)]
    out = []
    low, high = spec.get("min"), spec.get("max")
    if isinstance(low, (int, float)) and not isinstance(low, bool) and value < low:
        out.append(problem("bad_value", f"{field}: {value} is below min {low}",
                           field=field))
    if isinstance(high, (int, float)) and not isinstance(high, bool) and value > high:
        out.append(problem("bad_value", f"{field}: {value} is above max {high}",
                           field=field))
    return out


# ----------------------------------------------------------------- semver


def parse_version(text) -> tuple[int, int, int]:
    """`"1.2.3"` -> `(1, 2, 3)`; anything else raises ``ValidationError``."""
    if not isinstance(text, str) or not VERSION_RE.match(text):
        raise ValidationError(
            f"{text!r} is not a version: v1 packages are X.Y.Z with no "
            "prerelease and no leading zeros"
        )
    major, minor, patch = text.split(".")
    return int(major), int(minor), int(patch)


def compare(a: str, b: str) -> int:
    """-1 / 0 / 1, numerically (so `1.10.0` is above `1.9.0`)."""
    va, vb = parse_version(a), parse_version(b)
    return (va > vb) - (va < vb)


def satisfies(version, requirement) -> bool:
    """Does ``version`` satisfy ``requirement``? See the module docstring for
    the grammar — in particular `^0.x` is NOT npm's `^0.x`."""
    value = parse_version(version)
    low, high = _requirement_range(requirement)
    if low is None:
        return True
    return low <= value < high


def resolve(versions, requirement, allow_yanked: bool = False) -> str | None:
    """The **highest non-yanked** version satisfying ``requirement``.

    ``versions`` is either an iterable of version strings or a mapping of
    version -> index entry (whose ``yanked`` flag is then honoured). A key
    that is not a version is skipped rather than fatal: an index is data from
    somewhere else, and one bad key must not make a package unresolvable.
    """
    yanked_of = {
        key: bool(entry.get("yanked") if isinstance(entry, dict) else False)
        for key, entry in versions.items()
    } if hasattr(versions, "items") else {}
    best = None
    for version in list(versions):
        try:
            if not satisfies(version, requirement):
                continue
        except ValidationError:
            continue
        if yanked_of.get(version) and not allow_yanked:
            continue
        if best is None or compare(version, best) > 0:
            best = version
    return best


def _requirement_range(requirement):
    """`(low, high)` as version tuples, or `(None, None)` for `*`."""
    if requirement is None or requirement in ("", "*"):
        return None, None
    if not isinstance(requirement, str):
        raise ValidationError(f"{requirement!r} is not a version requirement")
    if requirement[0] in "^~":
        low = parse_version(requirement[1:])
        if requirement[0] == "^":
            high = (low[0] + 1, 0, 0)
        else:
            high = (low[0], low[1] + 1, 0)
        return low, high
    low = parse_version(requirement)
    return low, (low[0], low[1], low[2] + 1)


# ---------------------------------------------------------------- helpers


def _unknown_keys(doc, allowed, where, *, prefix=None, note=None) -> list[dict]:
    out = []
    for key in doc:
        if key in allowed:
            continue
        message = (
            f"unknown key {key!r} in {where} — known keys are "
            f"{sorted(allowed)}"
        )
        if note:
            message = f"{message}; {note}"
        out.append(problem("unknown_key", message, field=_field(prefix, key)))
    return out


def _missing(doc, required, *, prefix=None) -> list[dict]:
    return [
        problem("missing_field", f"{_field(prefix, key)} is required",
                field=_field(prefix, key))
        for key in sorted(required)
        if key not in doc
    ]


def _format_field(doc, key, expected, where) -> list[dict]:
    if key not in doc:
        return []
    value = doc[key]
    if not isinstance(value, int) or isinstance(value, bool):
        return [problem("wrong_type", f"{where} {key} must be the integer "
                        f"{expected}", field=key)]
    if value != expected:
        return [problem("bad_value", f"unsupported {where} {key} {value}; this "
                        f"AgentCAD reads {expected}", field=key)]
    return []


def _pattern(doc, key, regex, what, *, prefix=None) -> list[dict]:
    if key not in doc or doc[key] is None:
        return []
    value = doc[key]
    field = _field(prefix, key)
    if not isinstance(value, str):
        return [problem("wrong_type", f"{field} must be a string", field=field)]
    if not regex.match(value):
        return [problem("bad_value", f"{field} must be {what}: {value!r} does "
                        f"not match {regex.pattern}", field=field)]
    return []


def _non_empty_str(doc, key, *, prefix=None) -> list[dict]:
    if key not in doc:
        return []
    value = doc[key]
    field = _field(prefix, key)
    if not isinstance(value, str):
        return [problem("wrong_type", f"{field} must be a string", field=field)]
    if not value.strip():
        return [problem("bad_value", f"{field} must not be empty", field=field)]
    return []


def _list_of_str(doc, key, *, prefix=None) -> list[dict]:
    if key not in doc or doc[key] is None:
        return []
    value = doc[key]
    field = _field(prefix, key)
    if not isinstance(value, list) or not all(
        isinstance(v, str) and v.strip() for v in value
    ):
        return [problem("wrong_type", f"{field} must be a list of non-empty "
                        "strings", field=field)]
    return []


def _enum(doc, key, allowed, *, prefix=None) -> list[dict]:
    if key not in doc:
        return []
    value = doc[key]
    field = _field(prefix, key)
    if value not in allowed:
        return [problem("bad_value",
                        f"{field} must be one of {list(allowed)}, got {value!r}",
                        field=field)]
    return []


def _field(prefix, key) -> str:
    return f"{prefix}.{key}" if prefix else key


def _is_json_scalar(value) -> bool:
    return isinstance(value, (bool, int, float, str))


def _same_scalar(a, b) -> bool:
    if isinstance(a, bool) != isinstance(b, bool):
        return False
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return float(a) == float(b)
    return a == b
