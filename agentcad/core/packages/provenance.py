"""The materialised part's provenance header: emit, parse, and status-on-read.

`use_part` copies a package's script **into the project** (design Decision 5)
and prepends this block::

    # agentcad:package 1 {"content_id": "sha256:…", "index": "agentcad-core", …}
    # The publish gate is a CORRECTNESS gate, not a security boundary — this
    # script runs in your kernel worker with your privileges. See
    # docs/packages.md.

Three rules, each of them load-bearing and each of them a test:

* **Deterministic.** No timestamp, no client id, no absolute path. AC3 demands
  byte-identical re-materialisation from cache, and a machine fact here would
  break it on the second install *and* make two branches that add the same
  package conflict. Machine facts live in the cache receipt, which is never
  committed.
* **Read with `tokenize`.** The marker is a *comment*, so a docstring quoting
  it is not a header — the `core/sketch_emit._comment_lines` precedent, which
  paid for that lesson once already.
* **The header is immutable; its status is computed on every read.** PRD-008's
  anchor rule, for the same reason: a stored status is a claim that goes stale
  silently. :func:`status` answers one of five, from the manifest, the script
  bytes and the cache — and makes **zero kernel calls**.

`script_sha256` covers the package's script bytes **without** this block, so a
local edit is detectable and reportable — never repaired. That is also why
`remove_package` does not touch a script byte: the header is inside the script,
and the script text is the rebuild cache key (`service._cache_key`), so
rewriting headers to express a removal would re-key and rebuild every
materialised part.
"""

from __future__ import annotations

import hashlib
import io
import json
import tokenize

from . import cache

#: The comment marker. Bare and greppable, and the version number after it is
#: what lets a later format widen the payload without guessing.
MARKER = "agentcad:package"
HEADER_FORMAT = 1

#: Every field the payload carries, in the order the emitter writes them —
#: which is sorted, so two callers passing the same dict in different key
#: orders produce the same bytes.
HEADER_FIELDS = ("content_id", "index", "name", "part", "preset",
                 "script_sha256", "version")

#: Decision 11, place 7: the copy of the non-claim that ends up in the
#: consumer's own repository. Wrapped by hand so the emitted block is stable
#: regardless of anyone's formatter.
NOTE_LINES = (
    "The publish gate is a CORRECTNESS gate, not a security boundary — this",
    "script runs in your kernel worker with your privileges. See",
    "docs/packages.md.",
)

#: The five statuses, in the order :func:`status` decides them.
STATUSES = ("removed", "version_drift", "modified", "unverified", "ok")


def header(entry: dict) -> str:
    """The provenance block for one materialisation, ending in a blank line.

    ``entry`` carries :data:`HEADER_FIELDS`; anything else it holds is
    dropped, so a caller cannot smuggle a machine fact into a git-tracked
    file. The JSON is written on **one line** with sorted keys and fixed
    separators: the design spec's example wraps for legibility in prose, but a
    wrapped payload would need a continuation grammar in :func:`parse` and buy
    nothing — a comment line has no length limit.
    """
    payload = {key: entry.get(key) for key in HEADER_FIELDS}
    body = json.dumps(payload, sort_keys=True, separators=(", ", ": "),
                      ensure_ascii=False)
    lines = [f"# {MARKER} {HEADER_FORMAT} {body}"]
    lines += [f"# {line}" for line in NOTE_LINES]
    return "\n".join(lines) + "\n\n"


def parse(script: str):
    """The header payload, or ``None`` when the script has none.

    A marker that is present but unreadable answers a dict carrying
    ``malformed`` and ``None`` for every field, rather than ``None``: "there
    is a provenance claim here and we cannot read it" is a different fact from
    "there is none", and collapsing the two would hide exactly the tampering
    this module exists to surface.
    """
    if not isinstance(script, str) or MARKER not in script:
        # The cheap gate. It is also what keeps `get_project` free for a
        # project that uses no packages (FR15): no tokenize, no JSON, no read.
        return None
    line = _marker_comment(script)
    if line is None:
        return None
    rest = line.split(MARKER, 1)[1].strip()
    version, _, body = rest.partition(" ")
    empty = {key: None for key in HEADER_FIELDS}
    try:
        fmt = int(version)
    except ValueError:
        return {**empty, "format": None, "malformed": f"unreadable format {version!r}"}
    try:
        payload = json.loads(body)
    except ValueError as exc:
        return {**empty, "format": fmt, "malformed": f"unreadable payload: {exc}"}
    if not isinstance(payload, dict):
        return {**empty, "format": fmt, "malformed": "payload is not an object"}
    return {**empty, **{key: payload.get(key) for key in HEADER_FIELDS},
            "format": fmt, "malformed": None}


def strip(script: str) -> str:
    """``script`` without the provenance block.

    The block is the marker line, every comment line immediately below it, and
    one blank separator line — exactly what :func:`header` emits. A user who
    edits the block therefore reads as ``modified``, which is correct: the
    header is part of the file, and this function's job is to recover the
    package's own bytes, not to guess which of the user's edits were meant.
    """
    if not isinstance(script, str) or MARKER not in script:
        return script
    lines = script.splitlines(keepends=True)
    start = next((i for i, line in enumerate(lines)
                  if line.lstrip().startswith("#") and MARKER in line), None)
    if start is None:
        return script
    end = start + 1
    while end < len(lines) and lines[end].lstrip().startswith("#"):
        end += 1
    if end < len(lines) and not lines[end].strip():
        end += 1
    return "".join(lines[:start] + lines[end:])


def script_sha256(text: str) -> str:
    """``sha256:<hex>`` over UTF-8 bytes — the same spelling as a content id,
    because a reader should not have to learn two."""
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def status(head, manifest, script=None, *, verify=None) -> str:
    """One of :data:`STATUSES`, computed — never stored.

    The order is the answer's precedence and it is deliberate:

    ``removed``
        no lock entry. FR6's warning: the part is a project file and it still
        builds. It wins over ``modified`` because "the dependency is gone" is
        the fact the caller has to act on.
    ``version_drift``
        the lock holds a version the header does not name.
    ``modified``
        the script bytes differ from ``script_sha256``. Legitimate, reported,
        **never repaired**.
    ``unverified``
        we could not look — no script to compare, no ``script_sha256`` in the
        header, or a cache entry that is missing or does not verify. A fresh
        clone with a cold cache is exactly this case, and calling it ``ok``
        would be a claim nobody measured.
    ``ok``
        the lock has this package at this version, the bytes match, and the
        cached tree verifies.

    ``verify`` is the plan's third argument (``cache``) as an injectable seam:
    a scan verifies each package once and reuses the answer.
    """
    if not isinstance(head, dict):
        return "unverified"
    if head.get("format") != HEADER_FORMAT:
        # A header this build cannot interpret — a newer agentcad wrote it, or
        # it is malformed. "We did not look" is the honest answer; guessing
        # that the fields still mean what they mean today is how a format bump
        # turns into a silent false `ok`.
        return "unverified"
    name, version = head.get("name"), head.get("version")
    if not isinstance(name, str) or not isinstance(version, str):
        return "unverified"
    entry = (manifest.get("packages_lock") if isinstance(manifest, dict)
             else None)
    entry = entry.get(name) if isinstance(entry, dict) else None
    if not isinstance(entry, dict):
        return "removed"
    locked = entry.get("version")
    if locked != version:
        return "version_drift"
    expected = head.get("script_sha256")
    if not isinstance(expected, str) or script is None:
        return "unverified"
    if script_sha256(strip(script)) != expected:
        return "modified"
    check = verify or cache.verify
    if check(name, locked).get("status") != "ok":
        return "unverified"
    return "ok"


def describe(head, manifest, script=None, *, verify=None) -> dict | None:
    """The ``package_provenance`` block `get_part` reports, or ``None``.

    Flat, small and named after the question a reader has: *which package is
    this, and do we still agree with it?*
    """
    if not isinstance(head, dict):
        return None
    return {
        "package": head.get("name"),
        "version": head.get("version"),
        "part": head.get("part"),
        "preset": head.get("preset"),
        "index": head.get("index"),
        "content_id": head.get("content_id"),
        "status": status(head, manifest, script, verify=verify),
        "malformed": head.get("malformed"),
    }


def memoized_verify():
    """`cache.verify` with an answer per ``(name, version)``.

    A project holding twelve screws from one package must not re-hash that
    tree twelve times, and a caller that already verified may hand its memo in
    so the whole of `get_project` costs one hash per package.
    """
    memo: dict[tuple, dict] = {}

    def verify(name, version):
        key = (name, version)
        if key not in memo:
            memo[key] = cache.verify(name, version)
        return memo[key]

    return verify


def scan(store, proj: str, *, verify=None, manifest=None) -> list[dict]:
    """Every materialised part in ``proj``, with its provenance and status.

    Zero kernel calls; one manifest read, one script read per part, and one
    cache verification per **package**. A part whose script has no marker
    costs a substring test.
    """
    manifest = store.manifest(proj) if manifest is None else manifest
    verify = verify or memoized_verify()

    out: list[dict] = []
    for entry in manifest.get("parts") or []:
        part_id = entry.get("id")
        if not isinstance(part_id, str) or entry.get("kind") == "reference":
            continue
        try:
            script = store.read_script(proj, part_id)
        except Exception:  # noqa: BLE001 — a scan reports, it never breaks a read
            continue
        head = parse(script)
        if head is None:
            continue
        # The row's subject is the PROJECT part; the package's own part id
        # moves to `package_part` so the two never collide silently.
        described = describe(head, manifest, script, verify=verify)
        described["package_part"] = described.pop("part")
        out.append({"part": part_id, **described})
    return out


def _marker_comment(script: str) -> str | None:
    """The first ``# agentcad:package …`` **comment** line, via `tokenize`.

    A script that will not tokenize (the state a user is in while repairing
    one) falls back to a line scan, on `sketch_emit._comment_lines`' rule: the
    fallback is the behaviour that shipped before the token check existed, and
    it is strictly better than answering "no header" about a file that has
    one.
    """
    try:
        for tok in tokenize.generate_tokens(io.StringIO(script).readline):
            if tok.type == tokenize.COMMENT and MARKER in tok.string:
                return tok.string
    except (SyntaxError, tokenize.TokenError, ValueError, IndentationError):
        for line in script.splitlines():
            if line.lstrip().startswith("#") and MARKER in line:
                return line.strip()
    return None
