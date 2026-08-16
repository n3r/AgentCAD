"""The one safe JSON reader this subpackage uses — and why there is only one.

Every JSON document a package feature reads is **data from somewhere else**:
an `index.json` fetched from a git remote, a `package.json` out of a cache
entry, a `presets.json` in a directory a publisher controls, a receipt on
disk. `json.loads` on hostile input has two failure modes the obvious
`except (OSError, ValueError, UnicodeDecodeError)` does **not** catch:

* **`RecursionError`.** ``json.loads("[" * 200000 + "]" * 200000)`` raises it,
  and `RecursionError` is not a `ValueError`. A ~400 kB document was enough to
  take an unhandled exception straight out through `search`,
  `PackageManager.resolve` and `LocalIndex.entries` — which meant one poisoned
  index in the precedence list stopped every *healthy* index behind it from
  getting its turn. That is the exact failure the "a broken index is a
  warning, never an exception" rule exists to prevent, and eleven call sites
  each restated the caught set slightly differently.
* **Size.** A syntactically valid document has no ceiling of its own: a valid
  126 MB `index.json` cost 1.66 GB RSS to parse (measured by the reviewer).
  Bytes are refused *before* the parse, because after it the memory is
  already spent.

So: one function, one caught set, one ceiling, and callers that only ever have
`ValidationError` to handle. The ceilings are deliberately generous — they
bound a *hostile* document, not a large legitimate one.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..model import ValidationError

#: A `package.json`, `presets.json` or receipt. Kilobytes in practice; this is
#: three orders of magnitude of headroom and still refuses a memory bomb.
MAX_JSON_BYTES = 4 * 1024 * 1024

#: An `index.json` carries every version of every package an index publishes,
#: so it is the one document that legitimately grows.
#:
#: **Measured on the format `agentcad publish` actually writes.** Method: grow
#: the real `catalog/index.json` one package at a time and take the *marginal*
#: bytes, serialised exactly as `_write_index` does it (`indent=2` plus the
#: trailing newline) — so the number includes the package wrapper and the
#: separators a real entry actually costs, rather than a `{version: entry}`
#: pair weighed in isolation. Result over the nine catalog packages:
#: **2 236 B** per entry at `indent=2` (range 1 848–2 603) and **1 124 B**
#: compact. So 32 MB is **~15 000 entries**, or ~29 800 if a publisher
#: minifies — *not* the "~100 000" an earlier version of this comment claimed,
#: which was wrong by 3.4–6.7x and is corrected here rather than quietly
#: dropped. An index that outgrows this wants PRD-005's served registry, not a
#: bigger number: "clone the repo and parse the whole document" is already the
#: wrong shape at 32 MB.
MAX_INDEX_BYTES = 32 * 1024 * 1024

#: A second ceiling on the same document. **For a realistically-shaped index
#: the byte ceiling above is the one that fires** — 50 000 real entries is
#: ~104 MiB and is refused long before it is counted. This one exists for the
#: *pathological* document, where the two ceilings genuinely diverge: a package
#: record can be as small as `"p1":{"versions":{}}` — **20 B compact,
#: measured** — so 32 MB holds **1.68 million** of them, and `search` sorts
#: every one of them on every keystroke (50 000 alone costs ~4 ms per search;
#: 1.68 M is a hang). This ceiling fires on such a document at **1.00 MB**.
#:
#: Two ceilings, two jobs: bytes bound the *parse*, the count bounds the
#: *walk*. Neither is redundant, and neither is the other's backstop.
MAX_INDEX_PACKAGES = 50_000

#: And the third axis, because the first two are both about the *document* and
#: neither bounds one package's version list. A **valid** minimal version entry
#: is **523 B compact (measured)** — derived by stripping a real catalog entry
#: key by key for as long as `format.validate_index` still accepts it, rather
#: than by hand-building one and hoping it is minimal — so 32 MB is **~64 200
#: versions of a single package**, and `format.resolve` walks and parses every
#: one of them on every search (**168 ms measured at 100 000 versions**, per
#: package, per keystroke). 2 000 is far above any real release history and far
#: below where the walk is felt.
MAX_VERSIONS_PER_PACKAGE = 2_000

#: The one caught set. `RecursionError` is the member every hand-written
#: variant of this tuple was missing.
_CAUGHT = (OSError, ValueError, UnicodeDecodeError, RecursionError)


def loads(text, what: str, *, max_bytes: int = MAX_JSON_BYTES):
    """Parse ``text`` (str or bytes), or raise ``ValidationError``.

    Never raises anything else — that is the whole point.
    """
    raw = text.encode("utf-8", "surrogatepass") if isinstance(text, str) else text
    if not isinstance(raw, (bytes, bytearray)):
        raise ValidationError(f"{what} is not text")
    if len(raw) > max_bytes:
        raise ValidationError(
            f"{what} is {len(raw)} bytes; the ceiling is {max_bytes} bytes. "
            f"It was not parsed — a document is refused by size before it is "
            f"read, because after the parse the memory is already spent.")
    try:
        return json.loads(raw)
    except _CAUGHT as exc:
        raise ValidationError(f"{what} is unreadable: {_why(exc)}") from exc


def read(path, what: str, *, max_bytes: int = MAX_JSON_BYTES):
    """Read and parse the file at ``path``, or raise ``ValidationError``.

    The size is taken from the *bytes read*, not from `stat`, so a file that
    grows between the two is still bounded.
    """
    path = Path(path)
    try:
        raw = path.read_bytes()
    except _CAUGHT as exc:
        raise ValidationError(f"{what} is unreadable: {_why(exc)}") from exc
    return loads(raw, what, max_bytes=max_bytes)


def read_object(path, what: str, *, max_bytes: int = MAX_JSON_BYTES) -> dict:
    """:func:`read`, and the document must be a JSON object."""
    doc = read(path, what, max_bytes=max_bytes)
    if not isinstance(doc, dict):
        raise ValidationError(f"{what} must be a JSON object")
    return doc


def read_optional(path, what: str, *, max_bytes: int = MAX_JSON_BYTES):
    """:func:`read`, but ``None`` instead of an exception.

    For the readers whose contract is "never raises" — `cache.read_receipt`
    and the gate's `_presets_for`, where the absence of an answer is itself
    the answer and another stage is where it becomes a row.
    """
    try:
        return read(path, what, max_bytes=max_bytes)
    except ValidationError:
        return None


def _why(exc: BaseException) -> str:
    if isinstance(exc, RecursionError):
        # `str(RecursionError)` is a sentence about the interpreter, not about
        # the document, and a reader needs to know which one is at fault.
        return ("the document nests too deeply to parse (RecursionError) — "
                "this is a malformed or hostile document, not a large one")
    return f"{type(exc).__name__}: {exc}"
