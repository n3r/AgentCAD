# Changelog

One file per commit, capturing what changed and why in more detail than a
commit message. **Every commit must add an entry here, staged with the
change** (see the "Changelog" rule in `AGENTS.md`).

## Convention

- **Filename:** `NNNN-<slug>.md` — `NNNN` is a zero-padded sequence number
  (highest existing + 1), `<slug>` is a short kebab-case summary of the change.
  Sequence order is authoritative history order; the commit hash is recorded
  inside the file (it isn't known until the commit is made, so it may be filled
  in as `pending` when writing pre-commit and updated, or left as the branch's
  intent for backfilled entries).
- **Write from the real diff**, not from memory or the commit subject alone.
- Keep it factual and specific: name the modules/files, the behavior change,
  and any gotcha a future reader would want.

## Template

```markdown
# NNNN — <one-line summary>

- **Commit:** <short hash | pending>
- **Date:** YYYY-MM-DD
- **Author:** <name>

## Summary
One or two sentences: what this change is and why it exists.

## Changes
- Bullet per notable change, grounded in what the diff actually does.
- Behavior/contract changes, new modules, new tools/routes, schema changes.

## Files
- `path/to/file` — what changed there

## Notes
Rationale, trade-offs, gotchas, follow-ups, or "none".
```

Entries are historical records — don't rewrite past entries except to fix a
factual error.
