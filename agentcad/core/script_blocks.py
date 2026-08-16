"""Naming for the auto-generated blocks the script-edit tool packs append.

Several packs (`tools_holes.add_holes`, `tools_facemod.push_pull`) edit a part
by APPENDING a marked block that saves the current ``build`` under a private
name and rebinds ``build`` to a wrapper calling it. The saved name is resolved
as a **module global at call time**, so two blocks that pick the same name do
not merely shadow each other — the later binding makes the earlier wrapper call
*itself*, and the part rebuilds into a `RecursionError` that is written to disk
before anyone can see it.

Each pack used to number its own name off ``script.count(OWN_MARKER)``. That is
wrong twice over: two packs count independently and both start at 0, and the
marker tells the reader to "edit or remove freely", so deleting a middle block
walks the count backwards onto a name that is still live.

The fix is to derive the name from **the bindings the script actually has**,
which is the only thing that can answer "is this name free?". `next_build_alias`
scans for every ``_agentcad_prev_build_*`` already present — whoever wrote it,
in whatever order, after whatever deletions — and returns one that is not.
"""

from __future__ import annotations

import re

#: The prefix every pack's saved-previous-build binding shares. It is scanned
#: for, so a new pack joins the allocation simply by using this name.
BUILD_ALIAS_PREFIX = "_agentcad_prev_build_"

_ALIAS_RE = re.compile(
    r"\b" + re.escape(BUILD_ALIAS_PREFIX) + r"([A-Za-z0-9_]*)"
)


def existing_build_aliases(script: str) -> set[str]:
    """Every ``_agentcad_prev_build_*`` name that appears in `script`.

    Deliberately a plain textual scan rather than an AST walk: the script may
    not parse (that is exactly the state a user is in when they are repairing
    one), and a name that appears anywhere at all — even in a comment or a
    string — is a name this allocator should not hand out again.
    """
    return {BUILD_ALIAS_PREFIX + suffix
            for suffix in _ALIAS_RE.findall(script)}


def next_build_alias(script: str) -> str:
    """A ``_agentcad_prev_build_N`` name no binding in `script` already uses.

    Numbering stays dense-from-the-top for readability (the *n*-th surviving
    block usually reads as *n*), but correctness does not depend on that: the
    only guarantee made — and the only one needed — is that the returned name
    is absent from `script`.
    """
    taken = existing_build_aliases(script)
    n = 0
    while f"{BUILD_ALIAS_PREFIX}{n}" in taken:
        n += 1
    return f"{BUILD_ALIAS_PREFIX}{n}"


def apply_generated_block(
    service, project: str, part_id: str, before: str, after: str
) -> dict:
    """Persist a tool-generated script edit, reverting it if it does not build.

    `service.update_part` writes first and rebuilds second, and deliberately
    does **not** roll back: a human editing their own script must be able to
    save a broken state — that is how they repair it, and `get_part.status`
    exists to report it.

    Source *nobody typed* is the opposite case. When a tool appends a block and
    that block fails to build, the user is left holding text they did not write
    and cannot be expected to unpick, in place of a part that worked a moment
    ago. So the packs revert their own append and say so, leaving the part
    exactly as they found it. The error is still returned — the call failed,
    and reporting it as anything else would be the quiet failure again.
    """
    result = service.update_part(project, part_id, script=after)
    if result.get("ok") is not False:
        return result
    restored = service.update_part(project, part_id, script=before)
    return {
        **result,
        "rolled_back": True,
        # `True` only when the part is genuinely back to its previous state;
        # if even the *old* script no longer builds the caller must be told,
        # because then the revert did not restore a working part.
        "restored": restored.get("ok") is not False,
    }
