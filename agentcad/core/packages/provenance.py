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

**`header_sha256` covers the block itself**, and it exists because
`script_sha256` deliberately does not: :func:`strip` removes the whole comment
block, so *every* edit confined to it — deleting the security non-claim,
rewriting `index` to name a trusted registry, inserting a comment — used to
read `ok`.

**The digest covers the payload AS PARSED, not the fields this build knows
about.** That distinction is the whole of it: digesting
`{key: payload[key] for key in DIGESTED_FIELDS}` leaves every *other* key
uncovered, so a header could carry `"evil": "…"` — or anything a future format
adds — and still read `ok`. So the material is the parsed payload minus
`header_sha256`, the block's comment lines **verbatim** (indentation included,
because `# note` and `    # note` are different bytes in the consumer's file),
and whether the blank separator line was there. A reformatted block is a
changed block.

It is an **integrity check, not authentication**. There is no secret, so a
determined editor can recompute it, exactly as they can recompute
`script_sha256` — `ok` means *nothing edited this file after it was written*,
which is tamper-evidence and never tamper-proofing. What it buys is that the
block can no longer be edited **silently**, and no document may claim more.
"""

from __future__ import annotations

import hashlib
import io
import json
import tokenize
from collections import namedtuple

from ..model import ValidationError
from . import _json, cache

#: The comment marker. Bare and greppable, and the version number after it is
#: what lets a later format widen the payload without guessing.
MARKER = "agentcad:package"
HEADER_FORMAT = 1

#: Every field the payload carries, in the order the emitter writes them —
#: which is sorted, so two callers passing the same dict in different key
#: orders produce the same bytes.
HEADER_FIELDS = ("content_id", "header_sha256", "index", "name", "part",
                 "preset", "script_sha256", "version")

#: The one field the digest cannot cover, because it *is* the digest.
DIGEST_FIELD = "header_sha256"

#: The fields the emitter writes. Everything else a payload happens to carry is
#: digested too (see :func:`_block_digest`) — this tuple says what `header`
#: emits, never what the digest covers.
DIGESTED_FIELDS = tuple(f for f in HEADER_FIELDS if f != DIGEST_FIELD)

#: The block as it appears in the file: its comment lines **verbatim** (minus
#: the line ending) and whether the blank separator line follows them.
Block = namedtuple("Block", "notes separator")

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

    ``entry`` carries :data:`DIGESTED_FIELDS`; anything else it holds is
    dropped, so a caller cannot smuggle a machine fact into a git-tracked file
    — including ``header_sha256`` itself, which is **computed here** over the
    payload and the note lines. The JSON is written on **one line** with sorted
    keys and fixed separators: the design spec's example wraps for legibility
    in prose, but a wrapped payload would need a continuation grammar in
    :func:`parse` and buy nothing — a comment line has no length limit.

    A pure function of ``entry``, so AC3's byte-identical re-materialisation is
    unchanged: the same package part produces the same block, on any machine,
    forever.
    """
    payload = {key: entry.get(key) for key in DIGESTED_FIELDS}
    payload[DIGEST_FIELD] = _block_digest(payload, _emitted_block())
    return _render(payload)


def _emitted_block() -> Block:
    """The block :func:`_render` is about to write, as the digest sees it."""
    return Block(tuple(f"# {line}" for line in NOTE_LINES), True)


def _render(payload: dict) -> str:
    body = json.dumps(payload, sort_keys=True, separators=(", ", ": "),
                      ensure_ascii=False)
    lines = [f"# {MARKER} {HEADER_FORMAT} {body}"]
    lines += [f"# {line}" for line in NOTE_LINES]
    return "\n".join(lines) + "\n\n"


def _block_digest(payload: dict, block: Block) -> str:
    """`sha256:<hex>` over the payload **as parsed** and the block as written.

    Three things are in the material, and each one was a way to edit the block
    without it showing:

    * **the whole payload**, minus :data:`DIGEST_FIELD` — every key it carries,
      not the ones this build happens to name. Covering `DIGESTED_FIELDS` alone
      left any *other* key free: a header could carry `"evil": "…"` and read
      `ok`, and so could a field a future format adds.
    * **the comment lines verbatim**, indentation and all. `# note` and
      `    # note` are different bytes in the consumer's repository, and the
      note is the copy of the security non-claim that travels with the file — a
      claim anyone can reformat or delete without it showing is not a claim.
    * **whether the blank separator line was there**, because deleting it is an
      edit to the block that nothing else in the material would notice.

    The **body is not** in it, and does not need to be: `script_sha256` is one
    of the covered keys, so the body is transitively covered — an edited script
    fails that comparison, and a header lifted onto a different script fails it
    too. Keeping the body out is what leaves `header` a pure function of its
    one argument, which is what AC3's byte-identical re-materialisation needs.
    """
    covered = {key: value for key, value in (payload or {}).items()
               if key != DIGEST_FIELD}
    material = "\n".join([
        json.dumps(covered, sort_keys=True, separators=(",", ":"),
                   ensure_ascii=False, default=str),
        "\n".join(block.notes),
        "separator" if block.separator else "no-separator",
    ])
    return "sha256:" + hashlib.sha256(material.encode("utf-8")).hexdigest()


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
        # `_json.loads`, never `json.loads`: this payload is a comment line in
        # a file anyone may edit, so a deeply nested value would raise
        # `RecursionError` out of every `get_part` in the project.
        payload = _json.loads(body, "the provenance header payload")
    except ValidationError as exc:
        return {**empty, "format": fmt,
                "malformed": f"unreadable payload: {exc.message}"}
    if not isinstance(payload, dict):
        return {**empty, "format": fmt, "malformed": "payload is not an object"}
    # `payload` is carried **as parsed**, including keys this build does not
    # name: the block digest covers all of them, so `status` has to be able to
    # see them. It is deliberately absent from `describe`, which is the shape
    # that reaches an API consumer.
    return {**empty, **{key: payload.get(key) for key in HEADER_FIELDS},
            "format": fmt, "malformed": None, "payload": payload}


def strip(script: str) -> str:
    """``script`` without the provenance block.

    The block is the marker line, every comment line immediately below it, and
    one blank separator line — exactly what :func:`header` emits. A user who
    edits the block therefore reads as ``modified``, which is correct: the
    header is part of the file, and this function's job is to recover the
    package's own bytes, not to guess which of the user's edits were meant.
    """
    return split(script)[1]


def split(script: str):
    """``(block, body)`` — the block as written and the script without it.

    ``block`` is ``None`` when there is no block; otherwise it is a
    :data:`Block` carrying every comment line **below** the marker
    **verbatim** (line ending stripped, leading whitespace kept) and whether
    the blank separator line followed them. Verbatim because that is what the
    digest covers: un-commenting the lines would make ``# note`` and
    ``    # note`` identical, and they are not identical in the file.

    The marker line itself is not a note — its content is the payload, which
    the digest covers as data.
    """
    if not isinstance(script, str) or MARKER not in script:
        return None, script
    lines = script.splitlines(keepends=True)
    start = next((i for i, line in enumerate(lines)
                  if line.lstrip().startswith("#") and MARKER in line), None)
    if start is None:
        return None, script
    end = start + 1
    notes: list[str] = []
    while end < len(lines) and lines[end].lstrip().startswith("#"):
        notes.append(lines[end].rstrip("\n").rstrip("\r"))
        end += 1
    separator = end < len(lines) and not lines[end].strip()
    if separator:
        end += 1
    return Block(tuple(notes), separator), "".join(lines[:start] + lines[end:])


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
        the script bytes differ from ``script_sha256``, **or the provenance
        block itself differs from ``header_sha256``** — an edited payload, a
        deleted security notice, an inserted comment. Legitimate, reported,
        **never repaired**.
    ``unverified``
        we could not look — no script to compare, no ``script_sha256`` or
        ``header_sha256`` in the header, or a cache entry that is missing or
        does not verify. A fresh clone with a cold cache is exactly this case,
        and calling it ``ok`` would be a claim nobody measured.
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
    claimed_block = head.get(DIGEST_FIELD)
    if not isinstance(expected, str) or script is None:
        return "unverified"
    block, body = split(script)
    if script_sha256(body) != expected:
        return "modified"
    if not isinstance(claimed_block, str):
        # A header that carries no block digest cannot be checked for edits
        # confined to the block, and "we did not look" is the honest answer —
        # never `ok`, which would be a claim nobody measured.
        return "unverified"
    # The payload **as parsed** when we have it: a head built by hand carries
    # only the named fields, and for a canonical block those are the same
    # bytes, so the fallback is exact rather than lenient.
    payload = head.get("payload")
    if not isinstance(payload, dict):
        payload = {key: head.get(key) for key in DIGESTED_FIELDS}
    if _block_digest(payload, block or Block((), False)) != claimed_block:
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
