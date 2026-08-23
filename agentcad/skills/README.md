# The shipped skill library

Each subdirectory here is one **core skill** (PRD-029): a versioned markdown
guide an agent loads on demand with `load_skill {name}` instead of carrying it
in every system prompt. A skill is `<name>/SKILL.md` — a strict frontmatter
block (`name`, `description`, `version`, and optionally `triggers`, `license`,
`author`, `requires`) plus a markdown body whose `## ` sections are the
truncation unit — with optional `snippets/*.py` (complete part scripts, which
**must build green**) and `tables/*.json` beside it. A one-file skill may be a
bare `<name>.md`; this `README.md` is not a skill and is ignored by both the
loader and the lint.

Core skills are trusted by construction and held to the `library` lint
profile, so `license` and `author` are required and an over-long body is an
error rather than a warning. Before committing one, run:

```
.venv/bin/agentcad skill lint agentcad/skills/<name> --profile library
uv run pytest tests/test_skills_core_library.py
```

The loader, the layering (`core` < `org` < `project`), the capability gate and
trust live in `agentcad/core/skills.py`; the rules above live in
`agentcad/core/skills_lint.py`. A project's own skills go in
`<project>/skills/` and shadow a core skill of the same name.
