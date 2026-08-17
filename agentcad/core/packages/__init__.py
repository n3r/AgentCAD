"""The package subsystem (PRD-011): format, content id, cache, lockfile,
indexes, search, provenance and the publish gate.

A package is a **directory** — `package.json`, `parts/*.py`, `presets.json`,
`docs/`, `previews/` — and an index is a directory of package directories with
an `index.json` beside them. There is no archive format, so a git repo is an
index and a package is diffable and reviewable before it is run.

Naming (three words, three things, no overlap): the **cache** is
`~/.agentcad/packages/`, the **index** is where packages are published, and
`packages`/`packages_lock` are the two manifest keys. `registry` in this
codebase already means `ToolRegistry` and is never used for any of them.

Every module here runs in the **server** process and is OCP-free — asserted in
`tests/test_packages_ocp_free.py`, which requires a probe per module. This
`__init__` deliberately carries no code: a facade that re-exports has to be
kept in step with nine modules, and `pkgutil.iter_modules` already sees this
subpackage as `packages` (`ispkg=True`), which the tool-pack loader skips
because it filters on a `tools_` prefix.
"""
