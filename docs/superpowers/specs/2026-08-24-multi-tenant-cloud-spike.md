# PRD-005 de-risking spike — git sync, auth dependencies, audit store

**Date:** 2026-08-24 · **Environment:** macOS (Darwin 25.6.0, Apple M-series),
git 2.50.1 (Apple Git-155), Python 3.12.4, repo venv fastapi 0.141.1 /
starlette 1.5.0 / httpx 0.28.1 / uvicorn 0.52.1.
**Scratch root:** `/private/tmp/claude-501/-Users-nfedorov-dev-personal-cad-claude/e34dd7f8-e3b8-48ac-a1c3-0ae0b11663a8/scratchpad/spike005/`
No repo file was edited, no repo git command was run, nothing was installed into
the repo venv.

## Verdicts at a glance

| Area | Verdict |
|---|---|
| A. Git smart-HTTP round trip | **works — with four load-bearing caveats** (§A2, §A3, §A5, §A6) |
| A′. Which invocation (`http-backend` CGI vs direct `--stateless-rpc`) | **`git http-backend` CGI wins.** Direct mode silently downgrades to protocol v0 and needs hand-written version-dependent pkt-line framing |
| A″. FR9 "divergence surfaced, never overwritten" | **BROKEN BY DEFAULT.** Three separate holes; all closable with one `pre-receive` hook (§A5) |
| B. WebAuthn passkeys CI-testable without a browser | **works** — full ceremony round trip in 2–3 ms, no browser, no network (§B3) |
| B′. `soft-webauthn` for the authenticator half | **broken** — unmaintained, forces `cryptography<45`, conflicts with `webauthn>=3` (§B2) |
| B″. OIDC code+PKCE without authlib | **works** — 55 lines on `httpx` + `pyjwt`, both **already repo dependencies** (§B4) |
| C. Audit store | **sqlite3 WAL wins** on the axis that matters (queries: 1.4 ms vs 177 ms); JSONL wins on append and backup simplicity (§C) |
| D. 005a local accounts as the no-extra path | **sufficient** — no change needed (§D) |

---

# A. Git smart-HTTP round trip

## A0. The layout, verified

`agentcad/core/history.py` gives every project a **non-bare** repo whose
`GIT_DIR` is `<project>/.history` and whose **work tree is the project
directory itself** (`_exec`, lines 198–204). Note the PRD brief says
`.history/agentcad/` — that is wrong; `.history/agentcad/comments/` is the
review-thread store *inside* the GIT_DIR, and the GIT_DIR is `.history`.

Replicating it (`lab_setup.sh`) produces:

```
=== config ===
[core]
	repositoryformatversion = 0
	filemode = true
	bare = false
	logallrefupdates = true
	worktree = …/lab/server/proj          <-- ABSOLUTE PATH, written by `git init`
=== is-bare === false
=== GIT_DIR contents ===
agentcad  COMMIT_EDITMSG  config  description  HEAD  hooks  index  info  logs  objects  refs
=== receive.denyCurrentBranch default === (unset -> default 'refuse')
=== receive.denyNonFastForwards default === (unset)
```

Two facts implementers must carry:

* `core.worktree` is an **absolute path** baked into the config by `git init`.
  A hosted project directory that is ever moved or renamed silently breaks
  every git call. Re-write it on any relocation, or set it at call time only.
* Default branch here is **`master`** (no `init.defaultBranch`, and `HOME` is
  redirected into the GIT_DIR so a user's global config cannot change it).

## A1. Both prototypes work

`gitserver.py` implements both, behind one auth gate, for a projects root.

CGI mode, `git http-backend` with
`GIT_PROJECT_ROOT=<project>`, `PATH_INFO=/.history/<gitpath>`,
`GIT_HTTP_EXPORT_ALL=1`:

```
no-auth: 401
--- with bearer ---
001e# service=git-upload-pack
00000114 1df2b44… HEAD^@multi_ack thin-pack side-band … symref=HEAD:refs/heads/master
--- headers ---
HTTP/1.1 200 OK
expires: Fri, 01 Jan 1980 00:00:00 GMT
cache-control: no-cache, max-age=0, must-revalidate
content-type: application/x-git-upload-pack-advertisement
```

Full round trip (`roundtrip.sh`, direct mode, port 8732) — clone, commit,
push, worktree materialised server-side:

```
########## 1a) clone with http.extraHeader Bearer ##########
    exit=0 ; file: parts ; file: project.json ; tags: v1.0
    derived .cache present? no-good            <-- .cache/ is excluded, never travels
########## 2) commit locally in clone A and push ##########
    To http://127.0.0.1:8732/proj.git
       b004f19..34a06c5  HEAD -> master
    exit=0
########## 2b) server-side state after push ##########
    34a06c5… commit	refs/heads/master
    worktree files: a.py  b.py                 <-- materialised by checkout -f
########## 4) read-only principal push (expect 403) ##########
    error: RPC failed; HTTP 403
########## 5) pull back into clone A ##########
    exit=0
```

## A2. CAVEAT 1 — `receive.denyCurrentBranch=updateInstead` is STRUCTURALLY UNUSABLE here

The obvious answer to "the branch is checked out" is `updateInstead`. It is
rejected **even against a provably clean work tree**:

```
########## A) receive.denyCurrentBranch=updateInstead — CLEAN worktree ##########
     ! [remote rejected] HEAD -> master (Working directory has unstaged changes)
```

…while `git status --porcelain` on that same tree prints nothing, and the
three commands `push_to_deploy()` runs pass when invoked by hand. `GIT_TRACE=1`
gives the root cause:

```
trace: run_command: cd …/proj/.history;
  GIT_DIR=…/proj/.history/.
  GIT_WORK_TREE=…/proj/.history          <-- !!! the GIT_DIR, not the project
  git diff-files --quiet --ignore-submodules --
```

`receive-pack` derives the main work tree by **stripping a trailing `/.git`
from the GIT_DIR path** (git's `get_main_worktree()`), and *ignores
`core.worktree`* for that purpose. Our GIT_DIR ends in `/.history`, nothing is
stripped, so the work tree resolves to the GIT_DIR itself, where none of the
tracked files exist → "unstaged changes", forever. Confirmed independently:

```
=== git worktree list (GIT_DIR=.history) ===
…/lab2/srv/proj/.history  da88ef2 [master]      <-- should be …/lab2/srv/proj
=== control: the same layout with GIT_DIR named .git ===
…/lab2/ctl  0000000 [master]                    <-- correct
```

**Ruling:** `receive.denyCurrentBranch = ignore` + the server doing its own
materialisation. There is no third option short of renaming `.history`.

Bonus (and the reason a `post-receive` hook *is* viable): a hook runs with
`cwd = GIT_DIR`, `GIT_DIR=.`, **`GIT_WORK_TREE` unset**, and there
`git rev-parse --show-toplevel` *does* honour `core.worktree` and answers the
project directory correctly:

```
=== post-receive hook env for the .history layout ===
  cwd=…/lab2/srv/proj/.history
  GIT_DIR=.
  GIT_WORK_TREE=<unset>
  GIT_QUARANTINE_PATH=<unset>
  naive 'git checkout -f' would run against: …/lab2/srv/proj    <-- correct
```

So `worktree list` and `rev-parse --show-toplevel` disagree about this layout.
Never rely on the former.

## A3. CAVEAT 2 — with `ignore`, refs advance and the work tree does NOT

```
=== worktree AFTER an 'ignore' push with no materialization ===
a.py
  (b.py absent => refs advanced but the work tree is stale)
=== explicit materialize with --work-tree ===
real 0m0.034s   -> a.py b.py
```

Materialisation must be an explicit, always-run step. It is cheap
(`scale_bench.sh`, a 305-file / 19 MB / 500-commit project):

| operation | cost |
|---|---|
| `checkout -f master --`, already current | **0.03 s** ×3 |
| `checkout -f master --`, 1 file changed | **0.02–0.04 s** ×3 |
| `reset -q --hard master` | 0.04 s |
| `read-tree -u --reset master` | 0.03 s |
| **cold** (work tree wiped, 300 files restored) | **0.14 s** |

End-to-end inside the server, measured per push including process spawn:
**69–165 ms** (tiny project) and **73–165 ms** (19 MB project).

Two properties that make `checkout -f` the right verb:

```
=== does 'checkout -f' delete UNTRACKED derived data (.cache/, exports/)? ===
  .cache/mesh.bin present: YES-good
  exports/a.step present : YES-good
```

`checkout -f` is not `clean -fdx` — derived data survives, which is exactly
what FR8 ("derived data never syncs") wants. But it **does clobber
uncommitted tracked edits**:

```
    a.py content: x = 1   <-- live server-side edit 'x = 999' CLOBBERED
```

AgentCAD commits on every `project_changed`, so the window is small but real.
Materialisation must run **inside `write_guard`/the project's write scope**,
and should refuse (or snapshot first) when `status --porcelain -uno` is
non-empty.

## A4. Concurrency is safe; the loser is told correctly

Two simultaneous pushes to the same branch, both modes:

```
  p1:    c6e2d91..b26ac8e  master -> master
  p2: remote: error: cannot lock ref 'refs/heads/master': is at b26ac8e… but expected c6e2d91…
  p2:  ! [remote rejected] master -> master (failed to update ref)
  server master: b26ac8e ; worktree part_020 tail: # p1
```

git's ref transaction is the serialisation point. Nothing extra is needed for
ref safety — but the *materialisation* step is outside it, so two pushes that
both win on different branches can race the checkout. Wrap it in the project
write lock.

## A5. CAVEAT 3 — FR9 IS BROKEN BY DEFAULT: three holes

**Hole 1 — force-push overwrites silently.** `receive.denyNonFastForwards`
defaults to *false*:

```
########## E) --force from divergent clone B, DEFAULT receive.denyNonFastForwards ##########
    denyNonFastForwards = (unset/default false)
     + 93f2e71...2f870df HEAD -> master (forced update)
    exit=0
    server master: 2f870df   (was 93f2e71)      <-- OVERWRITTEN
```

Setting it to `true` fixes branches:

```
    remote: error: denying non-fast-forward refs/heads/master (you should pull first)
     ! [remote rejected] HEAD -> master (non-fast-forward)
```

**Hole 2 — `denyNonFastForwards=true` does NOT protect tags.** git's check is
explicitly skipped for `refs/tags/*`:

```
########## 4) TAG rewrite (v1.0 -> an unrelated commit) ##########
    client v1.0 -> 39a409e
     + b004f19...39a409e v1.0 -> v1.0 (forced update)
    server v1.0 -> 39a409e
```

**Hole 3 — `receive.denyDeletes=true` does NOT protect tags either.** It is
`refs/heads/`-only:

```
########## A) TAG DELETE with receive.denyDeletes=true (expect: NOT blocked) ##########
    server ann-1 -> 0d2cb8d
     - [deleted]         ann-1
    server ann-1 -> GONE
```

PRD-015 ships release tags. A client `git push --force --delete` erases a
hosted release with two of the three knobs already set. **All three holes close
with one `pre-receive` hook** (`tag_hole.sh` §B; ~20 lines):

```bash
#!/bin/bash
zero=0000000000000000000000000000000000000000
rc=0
while read -r old new ref; do
  case "$ref" in
    refs/tags/*)
      [ "$old" != "$zero" ] && { echo "agentcad: $ref already exists on the server; tags are immutable" >&2; rc=1; }
      [ "$new"  = "$zero" ] && { echo "agentcad: refusing to delete $ref" >&2; rc=1; } ;;
    refs/heads/*)
      if [ "$new" = "$zero" ]; then
        echo "agentcad: refusing to delete $ref (delete a branch in the UI)" >&2; rc=1
      elif [ "$old" != "$zero" ] && ! git merge-base --is-ancestor "$old" "$new"; then
        echo "agentcad: $ref diverged — pull and merge, never force" >&2; rc=1
      fi ;;
    *) echo "agentcad: refusing to update $ref (only refs/heads/* and refs/tags/*)" >&2; rc=1 ;;
  esac
done
exit $rc
```

Proof it holds, and that the messages reach the human:

```
########## C) tag rewrite is now refused ##########
    remote: agentcad: refs/tags/v1.1 already exists on the server; tags are immutable
     ! [remote rejected] v1.1 -> v1.1 (pre-receive hook declined)
########## E) branch delete is now refused (with a humane message) ##########
    remote: agentcad: refusing to delete refs/heads/scratch (delete a branch in the UI)
########## F) forced branch rewrite refused, and the WHOLE push is atomic ##########
    remote: agentcad: refs/heads/forcetest diverged — pull and merge, never force
     ! [remote rejected] forcetest -> forcetest (pre-receive hook declined)
     ! [remote rejected] goodbranch -> goodbranch (pre-receive hook declined)
########## G) a normal fast-forward push still works ##########
       34a06c5..f5e4703  master -> master
```

Note F: **`pre-receive` is all-or-nothing** — one bad ref rejects the whole
push. That is the right semantics for "surface divergence", but if per-ref
partial acceptance is wanted, use an `update` hook instead.

Also verified: the FR9 recovery path is the ordinary one.

```
########## I) pull the divergence back and merge ##########
    Merge made by the 'ort' strategy.  parts/b.py | 1 + ; parts/d.py | 1 +
    conflicted:
    *   1d3e61c Merge branch 'master' of http://…/proj
    |\   | * 93f2e71 add d  | * d7b31cc add part b
    * | 2f870df divergent c
```

## A6. CAVEAT 4 — tags only travel if you ask correctly

```
########## 1) LIGHTWEIGHT vs ANNOTATED tag under --follow-tags ##########
    * [new tag]  ann-1 -> ann-1
    server tags: ann-1 v1.0                     <-- light-1 and v1.1 did NOT travel
    after --tags: ann-1 light-1 v1.0 v1.1
```

`--follow-tags` carries **annotated tags only**. AgentCAD's own tags are
annotated (`core/branches.py:656` uses `tag -a`), so `--follow-tags` is
sufficient today — but `agentcad push` should send an explicit refspec pair
(`refs/heads/*:refs/heads/*` + `refs/tags/*:refs/tags/*`) rather than depend on
that, and must **never** prefix `+`.

## A7. Which invocation wins: `git http-backend` (CGI)

Performance is a wash (`perf_concurrency.sh`, 19 MB / 500-commit project):

| | CGI (8741) | direct (8742) |
|---|---|---|
| clone | 1.37 s | 1.58 s |
| push 20 commits | 0.54 s | 0.49 s |
| push a 6 MB import | 0.81 s | 0.87 s |
| 20 × `info/refs` | 1.00 s (50 ms ea.) | 1.12 s (56 ms ea.) |

The decider is correctness. **git 2.50 clients default to protocol v2**, and
the advertisement framing is version-dependent — which the direct path makes
you implement yourself and my first cut got wrong, silently:

```
=== which git protocol did the clones actually negotiate? ===
  port 8741 (CGI):    version 2
  port 8742 (direct): multi_ack          <-- SILENT v0 DOWNGRADE
```

Because (a) `Git-Protocol: version=2` must be forwarded into the child's
`GIT_PROTOCOL` env for the **advertise** call too, and (b) under v2 the
`# service=…` pkt-line + flush prefix must **not** be emitted:

```
=== CGI mode (forwards GIT_PROTOCOL) ===
000eversion 2
001cagent=git/2.50.1-Darwin
0013ls-refs=unborn
…                                        <-- NO "# service=" header at all
=== DIRECT mode without forwarding (my prototype) ===
001e# service=git-upload-pack
00000114 518f213… HEAD^@multi_ack thin-pack …     <-- v0
```

Fixing direct mode takes exactly this:

```python
if proto and "version=2" in proto:
    payload = proc.stdout                       # v2: raw, no service header
else:
    payload = pkt(b"# service=%s\n" % service.encode()) + FLUSH + proc.stdout
```

after which it is correct:

```
=== direct mode AFTER the fix ===
version 2 ; files cloned: 300
=== and a push still works over v2 ===
   518f213..bf5c5d6  master -> master
```

That is a version-numbered wire detail this project would own forever, in
exchange for avoiding ~25 lines of CGI header parsing. **Recommendation: CGI.**
`git http-backend` tracks protocol versions for free, and the "avoids CGI
parsing" argument is weaker than it looks — the parse is one
`partition(b"\r\n\r\n")` plus a `Status:` line, and you must drop
`Content-Length` before handing headers to Starlette.

## A8. Exposure surface — safe in both modes

Routing only the three smart endpoints makes everything else unreachable by
construction:

```
  /proj.git/agentcad/comments/index.json -> 404      (both modes)
  /proj.git/config -> 404 ; /proj.git/HEAD -> 404 ; /proj.git/hooks/pre-receive -> 404
  /../../etc.git -> 404 ; /..%2f..%2fetc.git -> 404 ; /a/b.git -> 404
```

And even at a hypothetical wildcard mount, `git-http-backend` with
`GIT_HTTP_EXPORT_ALL=1` serves only a fixed allowlist — it opens the dumb
protocol (`HEAD`, `info/refs`, `objects/**`) but still refuses everything else:

```
PATH_INFO=/.history/HEAD                        -> 200 "ref: refs/heads/master"
PATH_INFO=/.history/objects/info/packs          -> 200
PATH_INFO=/.history/config                      -> Status: 404 Not Found
PATH_INFO=/.history/agentcad/comments/index.json-> Status: 404 Not Found
PATH_INFO=/.history/hooks/pre-receive           -> Status: 404 Not Found
PATH_INFO=/.history/info/exclude                -> Status: 404 Not Found
```

So the comments store is safe either way. Prefer the three explicit routes
anyway: it keeps the dumb protocol off and keeps project-name validation in
Python.

## A9. Auth: the credential helper wins, decisively

Four patterns tried (`auth_patterns.sh`, `auth_patterns2.sh`). All four
*authenticate*; three leak.

```
########## P1) http.extraHeader on the command line ##########
/…/git -c http.extraHeader=Authorization: Bearer tok-good clone -q http://127.0.0.1:8732/proj.git …
    processes whose argv contains the token: 2
########## P2) URL-embedded token ##########
    remote.origin.url recorded in the clone:
      http://x-access-token:tok-good@127.0.0.1:8732/proj.git      <-- on disk, in every clone
########## P3) credential helper ##########
    clone OK
    remote.origin.url: http://127.0.0.1:8732/proj.git
    files in the clone containing the token: 0
```

And an unscoped `http.extraHeader` is re-sent across a **same-host** redirect
(it is dropped cross-host):

```
=== A: SAME-host redirect (127.0.0.1 -> 127.0.0.1), unscoped extraHeader ===
    {'path': 'info/refs', 'authorization': 'Bearer SAMEHOST'}     <-- leaked
=== B: CROSS-host redirect (localhost -> 127.0.0.1) ===
    {'path': 'info/refs', 'authorization': None}                  <-- dropped
=== P5b) same redirect with the CREDENTIAL HELPER ===
    {'path': 'info/refs', 'authorization': None}                  <-- never offered
```

git only offers helper credentials **after a 401 challenge from the final
URL**, keyed by `protocol://host`, so a redirect target gets nothing.

The helper also gives the *better* error, which is the opposite of what you'd
guess — the server's 401 body reaches the user:

```
########## P4) WRONG token — what the user actually sees ##########
    remote: authentication required
    fatal: Authentication failed for 'http://127.0.0.1:8732/proj.git/'
########## P4b) credential.helper='' + a one-shot bad header ##########
    fatal: could not read Username for 'http://127.0.0.1:8732': terminal prompts disabled
```

**Recommendation for `agentcad push`:** ship `agentcad credential` as a git
credential helper reading a mode-0600 config file, and persist
`credential.helper = agentcad credential` in the clone's own config. Minimal
working helper (proven; `venv-auth/bin/python`-shebanged in the spike):

```python
#!/usr/bin/env python3
import json, os, sys
if len(sys.argv) < 2 or sys.argv[1] != "get":
    sys.exit(0)                     # store/erase: agentcad owns the file
q = {}
for line in sys.stdin:
    k, _, v = line.strip().partition("=")
    if k:
        q[k] = v
try:
    cfg = json.load(open(os.path.join(os.environ["HOME"], "agentcad.json")))
except OSError:
    sys.exit(0)
entry = cfg.get("remotes", {}).get(f"{q.get('protocol','https')}://{q.get('host','')}")
if not entry:
    sys.exit(0)                     # silence => git falls through to its prompt
sys.stdout.write("username=agentcad\npassword=%s\n" % entry["token"])
```

Server side accepts Basic with the token as the *password* (any username), and
`Bearer` for non-git API calls. Always set `GIT_TERMINAL_PROMPT=0` when
shelling out, or a missing token hangs the CLI on a prompt.

## A10. FR10 — the clone incantation

`git clone --separate-git-dir` is wrong: it leaves a `.git` **pointer file** in
the project, precisely what `history.py`'s docstring forbids.

```
########## C1) git clone --separate-git-dir ##########
    layout: .git .history parts project.json
    .git is a: FILE -> gitdir: …/c1/.history
```

The winner (`clone_layout.sh` C2) — clone bare *into* `.history`, then flip it:

```bash
git clone --bare "$URL" "$PROJ/.history"
git --git-dir="$PROJ/.history" config core.bare false
git --git-dir="$PROJ/.history" config core.worktree "$PROJ"
git --git-dir="$PROJ/.history" config remote.origin.fetch '+refs/heads/*:refs/remotes/origin/*'
printf '.cache/\nexports/\n.history/\n*.tmp\n' > "$PROJ/.history/info/exclude"
git --git-dir="$PROJ/.history" --work-tree="$PROJ" checkout -f master --
```

Proven to yield a fully functional AgentCAD project:

```
    layout: .history parts project.json
    tracked: parts/a.py parts/b.py parts/n.py parts/p3.py project.json
    branch:  master ; remotes: origin http://127.0.0.1:8732/proj.git (fetch)
=== C2b) fetch works ===
    * [new branch] master -> origin/master ; origin/master: 069399e
=== C2c) snapshot / worktrees work ===
    committed: parts/local.py | 1 + ; .cache excluded: 0 matches
    worktree add: Preparing worktree (new branch 'feat')
=== C2d) push the local edit back ===
       069399e..728a68d  master -> master
```

Two traps in that sequence: `--bare` sets `remote.origin.fetch` to
`+refs/heads/*:refs/heads/*` (must be rewritten to `refs/remotes/origin/*`),
and `info/exclude` must be written **before** the first status/snapshot or
`.history/` itself shows up as untracked (`status: [?? .history/]`).

## A11. Streaming, and what the prototype does not do

Sniffing what git actually sends:

```
GET  /big.git/info/refs?service=git-upload-pack  git-protocol=version=2  body=0B
POST /big.git/git-upload-pack     content-length=184     body=184B
POST /big.git/git-receive-pack    content-length=4       body=4B
POST /big.git/git-receive-pack    transfer-encoding=chunked  content-length=None  body=3002467B
```

* A real push arrives **chunked with no `Content-Length`**. `await
  request.body()` works but buffers the whole pack in RAM, and
  `subprocess.run(capture_output=True)` buffers the whole clone response too.
  Production must stream stdin/stdout (`asyncio.create_subprocess_exec` +
  `StreamingResponse`) and enforce a max body size, or a large import is a
  memory DoS.
* `Content-Encoding: gzip` was **not** observed from git 2.50 on any request.
  Keep the defensive `gzip.decompress` branch, but do not rely on it.
* Set `CONTENT_LENGTH` for the CGI child from the bytes you actually have,
  never from the client's header.

## A12. `git-http-backend` availability

macOS (Apple Git 2.50.1) — present:

```
$ git --exec-path
/Applications/Xcode.app/Contents/Developer/usr/libexec/git-core
$ ls $(git --exec-path)/git-http-backend
…/git-http-backend
OK
```

The repo's `Dockerfile` (`python:3.12-slim` + `apt-get install … git`) gets
Debian's `git` package, which ships `/usr/lib/git-core/git-http-backend`;
git-for-windows ships `mingw64/libexec/git-core/git-http-backend.exe`.
**Neither was executed in this spike** (no Docker daemon available). Add a
one-line startup probe — `Path(git --exec-path)/("git-http-backend" +
(".exe" if os.name=="nt" else ""))` must exist — and assert it in the Linux
leg of CI and in `windows-probe.yml`. Cheap insurance; a missing backend would
otherwise present as "sync just 500s".

## A. Recommended design (one paragraph)

Serve three routes per project — `GET /{proj}.git/info/refs`,
`POST /{proj}.git/git-upload-pack`, `POST /{proj}.git/git-receive-pack` —
behind the PRD-005a auth layer, each dispatching to `git http-backend` with
`GIT_PROJECT_ROOT=<tenant project dir>`, `PATH_INFO=/.history/<gitpath>`,
`GIT_HTTP_EXPORT_ALL=1`, and `GIT_PROTOCOL` forwarded from the `Git-Protocol`
header; stream stdin/stdout. On the hosted repo set
`receive.denyCurrentBranch=ignore`, `receive.denyNonFastForwards=true`,
`receive.denyDeletes=true` **and** install the `pre-receive` guard above (the
knobs alone leave the two tag holes open). After a successful `receive-pack`,
materialise inside the project's write scope with
`git --git-dir=.history --work-tree=<proj> checkout -f <default branch> --`
(20–140 ms), refusing or snapshotting first if the tree is dirty. Client side:
`agentcad clone` uses the `--bare`-into-`.history` recipe, `agentcad push`
sends `refs/heads/*:refs/heads/*` + `refs/tags/*:refs/tags/*` with no `+`, and
authentication is a git credential helper (`agentcad credential`), never
`http.extraHeader` and never a token in the URL.

---

# B. Auth dependency strategy

## B1. Weight

Measured in a scratch venv (`venv-auth`, `venv-authlib`, `venv-pyjwtcrypto`).

| package | new transitive deps | notable |
|---|---|---|
| `webauthn` 3.0.0 | `cbor2` 6.1.4, `pyasn1` 0.6.4, `pyasn1_modules` 0.4.2, `pyOpenSSL` 26.4.0, `typing_extensions`, + `cryptography`/`cffi`/`pycparser` | `cryptography>=49.0.0` (resolved 50.0.0) |
| `pyjwt[crypto]` 2.13.0 | `cryptography`, `cffi`, `pycparser` | — |
| `Authlib` 1.7.2 | `cryptography`, `cffi`, `pycparser`, `joserfc` 1.7.4 | — |

```
webauthn==3.0.0
├── cbor2 [required: >=6.1.2, installed: 6.1.4]
├── cryptography [required: >=49.0.0, installed: 50.0.0]
│   └── cffi [required: >=2.0.0, installed: 2.1.1] └── pycparser
├── pyasn1 [required: >=0.6.2, installed: 0.6.4]
├── pyasn1_modules [required: >=0.4.2, installed: 0.4.2]
└── pyOpenSSL [required: >=26.3.0, installed: 26.4.0]
```

**Decisive:** `pyjwt[crypto]` and `cryptography` are **already hard
dependencies of this repo** — `mcp 2.0.0` requires `pyjwt[crypto]>=2.10.1`:

```
=== where do pyjwt / cryptography come from in the REPO venv? ===
  PyJWT 2.13.0 ; cryptography 50.0.0 ; cffi 2.1.1
    required by PyJWT 2.13.0: cryptography>=3.4.0; extra == "crypto"
    required by mcp 2.0.0: pyjwt[crypto]>=2.10.1
```

So the *only* genuinely new weight for passkeys is ~5.1 MB:

```
  cryptography            12.94 MB   (already present)
  pyasn1_modules           1.83 MB   NEW
  cbor2                    1.18 MB   NEW
  cffi                     1.04 MB   (already present)
  pyasn1                   0.83 MB   NEW
  pyOpenSSL                0.46 MB   NEW
  pycparser                0.43 MB   (already present)
  webauthn                 0.43 MB   NEW
  typing_extensions        0.35 MB   NEW
```

Import cost (fresh interpreter, min of 7; bare interpreter startup 32.7 ms):

```
  jwt                                       66.7 ms
  jwt.algorithms                            63.5 ms
  cryptography.hazmat…asymmetric.rsa        17.2 ms
  webauthn                                 104.9 ms
  cbor2                                      8.5 ms
  httpx                                     62.0 ms
  authlib.jose                              69.3 ms
  authlib.integrations.httpx_client        138.1 ms
```

`import webauthn` costs ~105 ms of interpreter start-up. Import it **lazily**,
inside the passkey route handlers, not at pack-load time.

## B2. `soft-webauthn` is broken — do not use it

```
ERROR: pip's dependency resolver …
pyopenssl 26.4.0 requires cryptography<51,>=49.0.0, but you have cryptography 44.0.3 which is incompatible.
webauthn 3.0.0 requires cryptography>=49.0.0, but you have cryptography 44.0.3 which is incompatible.
… (and its fido2 1.2.0 pin) fido2 1.2.0 requires cryptography!=35,<45,>=2.6, but you have cryptography 50.0.0
```

`soft-webauthn` 0.1.4 drags in `fido2<...` pins that **downgrade
`cryptography` to 44.0.3**, which conflicts with `webauthn>=3` *and* with the
repo's existing 50.0.0. Rejected.

## B3. Passkeys ARE CI-testable — proof

`webauthn_roundtrip.py` pairs duo-labs `webauthn` (relying party) with a
~70-line virtual ES256 authenticator built from `cryptography` + `cbor2`
(COSE key, `authData` flags/`rpIdHash`/signCount, `fmt:"none"` attestation).
Full registration **and** authentication, plus the four negative cases a real
test suite needs:

```
REGISTRATION verified
  credential_id      : NAvqZvqLsgozalAHcbSpGgXz…
  public key (COSE)  : 77 bytes
  sign_count         : 0
  user_verified      : True
  attestation fmt    : none
  elapsed            : 2.1 ms
AUTHENTICATION verified
  new sign_count     : 1
  user_verified      : True
  elapsed            : 3.0 ms
NEGATIVE cases
  wrong origin      : rejected (InvalidAuthenticationResponse)
  stale challenge   : rejected (InvalidAuthenticationResponse)
  sign-count regress: rejected (InvalidAuthenticationResponse)
  tampered signature: rejected (InvalidAuthenticationResponse)

webauthn 3.0.0 — full ceremony round trip, zero browser, zero network
```

2–3 ms per ceremony, deterministic, offline. FR1's passkey half is fully
CI-coverable; the browser is needed only for a manual acceptance pass.

## B4. OIDC without authlib — proof

`oidc_pkce.py` runs a throwaway IdP (discovery doc, `/authorize`, `/token`,
JWKS) and drives the whole flow with `httpx` + `pyjwt` only:

```
authorize URL: http://127.0.0.1:8761/authorize?response_type=code&client_id=agentcad-hosted&redirect_uri=https%…
ID token VERIFIED (RS256 via JWKS):
   iss             http://127.0.0.1:8761
   sub             idp-user-42
   aud             agentcad-hosted
   email           nikita@example.com
   email_verified  True
   name            Nikita Fedorov
   full flow      155.5 ms (discovery + authorize + token + JWKS + verify)

NEGATIVE cases
   PKCE mismatch : rejected (PKCE mismatch)
   state mismatch: rejected (state mismatch)
   alg=none      : rejected (InvalidAlgorithmError)
   expired       : rejected (ExpiredSignatureError)

OidcClient (the whole RP): 55 non-blank lines, imports = httpx, jwt, hashlib, base64, secrets, urllib.parse
```

Minimal ID-token validation is exactly: fetch discovery (assert
`issuer` equals the configured issuer), fetch JWKS, select the key by `kid`,
`jwt.decode(..., algorithms=["RS256"], audience=client_id, issuer=issuer,
options={"require": ["exp","iat","iss","aud","sub"]})`, then compare `nonce`
with `secrets.compare_digest`. `jwt.PyJWKClient` does the JWKS fetch and
caching for you — but note it uses **`urllib.request.urlopen`, not httpx**
(confirmed: `uses urllib: True`, `uses httpx: False`), so it will not honour an
httpx proxy/timeout/CA config. For a hosted service behind a proxy, fetch the
JWKS yourself with httpx and hand `jwt.PyJWK` the parsed dict.

## B. Dependency ruling

* **Hand-roll OIDC.** `authlib` buys ~55 lines and costs a dependency plus
  `joserfc` and a 138 ms import. `httpx` and `pyjwt[crypto]` are already here.
  **Do not add authlib.**
* **Take `webauthn`.** The WebAuthn wire format (CBOR/COSE/attestation
  statement parsing) is genuinely not worth hand-rolling. 5 new packages,
  ~5.1 MB.
* Put **only `webauthn` (and nothing else)** behind
  `agentcad[cloud]`; guard the passkey routes with an `importorskip`-style
  availability check, exactly like the existing `[fem]` extra. Import it
  lazily — 105 ms.
* **Do not take `soft-webauthn`.** Ship the ~70-line virtual authenticator
  from `webauthn_roundtrip.py` as a test fixture instead; it has zero
  dependencies beyond `cryptography` + `cbor2` (both arriving with `webauthn`).

---

# C. Audit store — JSONL+flock vs sqlite3 WAL

`audit_bench.py`, 100 000 rows of the FR12 record shape
(`{ts, principal, action, project, args_digest, outcome}`), 7 principals,
20 projects.

```
=== (a) single-process append, 100,000 rows ===
  JSONL + flock :    0.84 s  (  118,989 rows/s)
  sqlite WAL    :    4.84 s  (   20,669 rows/s)

=== (b) CONCURRENT appends: 8 processes x 5,000 rows ===
  JSONL + flock :   0.68 s  (   59,208 rows/s)  lines=40000  corrupt=0
  sqlite WAL    :   2.17 s  (   18,460 rows/s)  rows=40000
  integrity_check: ok

=== (c) admin queries over 100k rows (best of 3) ===
  JSONL  scan: principal=user:nikita                      177.0 ms  (1000 rows)
  sqlite idx : principal=user:nikita                        1.4 ms  (1000 rows)
  JSONL  scan: project=proj-07 AND 6h window              171.9 ms  (1000 rows)
  sqlite idx : project=proj-07 AND 6h window                1.5 ms  (1000 rows)
  JSONL  scan: last 1000 overall (tail)                   194.4 ms  (1000 rows)
  sqlite idx : last 1000 overall                            1.2 ms  (1000 rows)
  JSONL  scan: COUNT by principal (full aggregate)        196.6 ms  (100000 rows)
  sqlite     : COUNT(*) GROUP BY principal                  6.0 ms  (7 rows)

=== (e) on-disk size for 100,000 rows ===
  JSONL  :  16.38 MB
  sqlite :  17.58 MB (db + wal)

=== (f) durability-honest variants ===
  JSONL + flock + fsync/row :   0.46 s (  21,781 rows/s)
  sqlite WAL synchronous=FULL:   1.01 s (   9,885 rows/s)
```

Both are far above any plausible audit rate: even the slowest configuration
(sqlite `synchronous=FULL`) sustains **9 885 events/s**, and a mutating
AgentCAD action is one row. **Append throughput is not a decision axis.**
Query latency is, and it is a **126×** gap that grows linearly with retention.

Crash and backup behaviour (`(d)`):

```
  JSONL: a half-written trailing record is dropped by the 'must end in \n' rule
         -> 1 usable row(s), 0 exceptions
  sqlite: `cp audit.db` alone is UNUSABLE -> no such table: audit
          (all 50 rows are still in -wal)  <-- backup pitfall
  sqlite: `VACUUM INTO` recovers 50/50 rows, integrity=ok
  JSONL: `cp audit.jsonl` at any instant is a valid prefix, always.
```

**Recommendation: sqlite3 (stdlib) in WAL mode**, with these settings and
guardrails:

* `PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL; PRAGMA
  busy_timeout=30000` and `sqlite3.connect(..., timeout=30.0)`. The
  `docker compose exec` second-writer case is exactly what `busy_timeout`
  exists for; 8 concurrent writers produced **40 000/40 000 rows and
  `integrity_check: ok`** with no retry logic of my own.
* Indexes `(ts)`, `(principal, ts)`, `(project, ts)` — that is what buys the
  1.4 ms.
* **The backup story is the cost, and it is the reason PRD-005a chose JSON.**
  `authstore.py`'s docstring says "`tar` of the volume is a correct backup
  precisely because every write here is an `os.replace`" — that stops being
  true the moment a WAL database is in the volume. `docs/deployment.md`'s
  backup section must gain an explicit `VACUUM INTO` (or `.backup`) step, and
  the compose docs must say that copying `audit.db` alone loses data.
* Keep it a **separate file** (`<state>/audit/audit.db`), never mixed with the
  four `authstore` JSON documents, so identity backup semantics are unchanged
  and the "zero SQLite in this repo" invariant breaks in exactly one, argued
  place — which is what PRD-005a's Decision 14 note explicitly permits ("If
  this PRD's audit log needs SQLite, it may introduce it for the audit log").
* If a JSONL fallback is ever wanted (air-gapped `grep`-ability), the
  crash rule is the one measured above: **a record is only valid if its line
  terminates in `\n`**; a torn trailing line is dropped, never parsed.

---

# D. The no-extra path: 005a local accounts stay sufficient

`agentcad/core/authstore.py` already delivers everything FR1 requires of a
self-hosted instance with no external IdP and no `[cloud]` extra: closed
registration with admin-minted 7-day enrolment tokens, scrypt-hashed passwords
(n=2¹⁵, r=8, p=1, 16-byte salt, parameters stored *beside* each digest so they
can be raised with a re-hash on next login rather than invalidating accounts),
SHA-256-digested 256-bit session and bearer secrets, sliding-14-day /
absolute-30-day sessions, and per-handle rate limiting with a full dummy scrypt
charged to unknown or disabled handles so login is not a user-enumeration
oracle. The below-OWASP n=2¹⁵ choice is argued in the module rather than
hidden, and the argument holds under PRD-005: registration is closed, an
account on the instance is already arbitrary code execution on the host, and
online guessing is bounded by rate limiting per NIST SP 800-63B — so the
password is nowhere near the weakest link. The two-layer `threading.RLock` +
`fcntl.flock` locking already handles the `docker compose exec` second-writer
case that this spike's audit benchmark also had to answer. Nothing in FR1's
"a self-hosted instance works with local accounts + passkeys alone" requires
touching it: passkeys become a *second* credential type hanging off the
existing `users.json` handle (a `credentials` list beside `password`), OIDC
becomes a *third*, and an instance that configures neither keeps working
exactly as it does today. The one adjacent change PRD-005 already knows about
stands: `actor_kind` must read `user:` as human (`core/proposals.py:112-124`).

---

# Pitfall checklist for implementers

1. `receive.denyCurrentBranch=updateInstead` **cannot work** with a GIT_DIR
   named `.history` — use `ignore` + explicit materialisation (§A2).
2. `git worktree list` reports the wrong main work tree for this layout; never
   build on it. `git rev-parse --show-toplevel` is correct (§A2).
3. Materialise with `checkout -f <branch> --` **and pass `--work-tree`
   explicitly** — a hook's inherited env is not enough (§A2, §A3).
4. `checkout -f` clobbers uncommitted tracked edits. Hold the project write
   lock; refuse or snapshot a dirty tree first (§A3).
5. `receive.denyNonFastForwards` defaults to **false** — force-push overwrites
   silently (§A5).
6. `denyNonFastForwards` and `denyDeletes` are **`refs/heads/`-only**. Tags can
   be rewritten and deleted with both set. Use the `pre-receive` hook (§A5).
7. `pre-receive` is all-or-nothing across the whole push; `update` is per-ref.
   Pick deliberately (§A5).
8. `--follow-tags` carries annotated tags only (`branches.py:656` is `tag -a`,
   so today it is fine — but send explicit refspecs) (§A6).
9. If you skip `http-backend`: forward `Git-Protocol` into the child env for
   the **advertise** call, and emit the `# service=` pkt-line **only** for
   protocol v0/v1. Getting this wrong is a silent v0 downgrade, not an error
   (§A7).
10. Push bodies arrive **chunked with no `Content-Length`**. Stream, cap the
    body size, and set the CGI `CONTENT_LENGTH` from real bytes (§A11).
11. Never put a token on argv (`ps`) or in a remote URL (every clone's
    config). Use a git credential helper; set `GIT_TERMINAL_PROMPT=0` (§A9).
12. `git clone --separate-git-dir` leaves a `.git` pointer file in the project.
    Clone `--bare` into `.history` and rewrite `core.bare`, `core.worktree`,
    `remote.origin.fetch`, then write `info/exclude` **before** the first
    status (§A10).
13. `core.worktree` is an absolute path in every project's config — moving or
    renaming a hosted project directory breaks git silently (§A0).
14. `import webauthn` costs ~105 ms; import it lazily (§B1).
15. `jwt.PyJWKClient` fetches with `urllib`, not `httpx` — no shared proxy/CA
    config (§B4).
16. A WAL sqlite file invalidates "`tar` the volume is a correct backup".
    `VACUUM INTO` before archiving (§C).
17. `git-http-backend` was verified present on macOS only. Probe for it at
    startup and assert it in the Linux and Windows CI legs (§A12).

# Artifacts

| file | what it is |
|---|---|
| `gitserver.py` | both smart-HTTP prototypes behind one auth gate, with the v2 framing fix |
| `lab_setup.sh` | replicates the `.history` GIT_DIR layout |
| `roundtrip.sh` | clone / commit / push / non-ff / 403 / pull |
| `deny_modes.sh` | the `denyCurrentBranch` / `denyNonFastForwards` matrix |
| `update_instead.sh`, `worktree_bug.sh` | isolation and root cause of §A2 |
| `refs_and_v2.sh`, `tag_hole.sh` | tags, deletes, the `pre-receive` guard |
| `scale_bench.sh`, `perf_concurrency.sh` | materialisation and transfer costs |
| `auth_patterns.sh`, `auth_patterns2.sh`, `redirect_probe.py` | the four auth patterns and the redirect leak |
| `clone_layout.sh` | the FR10 clone recipe |
| `sniff.py` | the headers git actually sends |
| `webauthn_roundtrip.py` | full passkey ceremony + virtual authenticator |
| `oidc_pkce.py` | OIDC code+PKCE on httpx + pyjwt, with a throwaway IdP |
| `audit_bench.py` | the JSONL vs sqlite benchmark |
