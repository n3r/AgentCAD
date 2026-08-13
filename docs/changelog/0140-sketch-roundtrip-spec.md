# 0140 — PRD-009 slice 13: round-trip spec persistence and divergence (FR10)

- **Commit:** pending
- **Date:** 2026-08-12
- **Author:** Nikita Fedorov

## Summary

A sketch inserted into a part script now carries its **whole constraint spec**
with it, and reopening the sketcher reads it back. The spec rides in the
script — the `push_pull` precedent, not a sidecar — behind a hash over the
emitted code, so a hand edit to that code is **detected and reported, never
silently overwritten**. The GUI grows the banner slice 10 had nowhere to put:
in sync, *diverged* (read-only, two explicit choices), or *unverified*.

```python
# --- agentcad sketch "profile" (auto-generated; edit or remove freely) ---
# agentcad-sketch-spec: {"v": 1, "entities": {...}, "constraints": [...]}
# agentcad-sketch-hash: sha256:33353fb12d7a921ecb1f1f9a3b11d03760b12bda60b2b…
def sketch_profile():
    with BuildSketch(Plane.XY) as _sk:
        ...
    return _sk.sketch
# --- end agentcad sketch "profile" ---
```

## The three rules this slice is arranged around

1. **The code is the source of truth for geometry; the spec block is
   provenance.** The hash covers the code, so if they disagree the *code* is
   what the user last meant and the sketcher refuses to touch it until asked.
2. **"We cannot tell" is never "there is no sketch".** A spec that will not
   parse, a block with no hash, a block with no end marker → `unverified`,
   with the reason, and the code left alone (the PRD-008 rule applied to a
   comment block).
3. **Two blocks never shadow each other.** The block name *is* the emitted
   function's name (`def sketch_<name>()`), and the server picks the next free
   one — counting pre-FR10 `def sketch_*(` definitions too, since those carry
   no block.

## Changes

- **`agentcad/core/sketch_emit.py`**
  - `emit(..., persist="<name>")` — **opt-in**. Without it the bytes are
    exactly what every caller got before this slice (asserted), so slice 7's
    byte-identity tests and every agent that just wants code are untouched.
    With it the code is wrapped in marker / spec / hash / end marker and the
    function is named after the block.
  - `persist_spec(spec)` — the spec **as submitted**, in the shape a caller
    posts straight back (`{v, entities, constraints, plane}`). `initial`,
    `drag` and `diagnostics` are properties of one *call* and are not
    persisted. **`plane` is** (slice 12): sketch-on-face coordinates without
    their basis are arbitrary, so a block that dropped it would reopen a
    meaningless sketch. As submitted, not as solved — the GUI submits the
    on-screen (already solved) coordinates, so a GUI block re-solves from its
    own solution and lands on the same branch, without the emitter minting a
    second rounded copy of geometry the code already holds.
  - `block_hash(code)` — `sha256:<hex>` over the code with line endings and
    trailing whitespace normalised away. An editor that rewrites either on
    save has not touched the geometry, and a banner that cries wolf is a
    banner nobody reads.
  - `parse_blocks(script)` — every block, in script order, as
    `{name, status, spec, code, hash, computed_hash, start_line, end_line,
    message}` with `status ∈ {ok, diverged, unverified}`.
  - `next_name(script)` — `profile`, `profile2`, … skipping every name the
    script already uses.
- **`agentcad/core/tools_sketch.py`** — `persist` on `solve_sketch`, with the
  schema entry and the description of the block, the hash and the divergence
  rule. An invalid name is an `EmitError` → `validation_error`, like every
  other emission refusal.
- **`agentcad/server/routes_sketch.py`** — `persist` whitelisted (explicit
  keys, never `**body`), and a new **`POST /api/sketch/blocks`**
  `{script} -> {blocks, next_name}`.
- **`frontend/js/sketcher.js`**
  - Opening the sketcher looks for blocks in the open part's script (through
    `editor.getScript()`, so unsaved edits count) and **never clobbers**: a
    canvas with work on it is left alone.
  - One block loads straight in; several show a picker row per block, each
    labelled with its status.
  - `specToModel` is the inverse of `entitiesSpec()`, and carries the two
    things slices 11–12 handed on: **`construction`** (a reference that
    re-parsed as real geometry would emit the part's own boundary back into
    it) and **`plane`**. Name counters are re-derived from the loaded names,
    so `p1`/`ln1` do not collide with themselves.
  - **The divergence banner**, red, with the design's two choices —
    *Re-solve from the spec* and *Discard the spec* — and a **read-only
    latch** underneath it: every entity tool, every constraint button, the
    drag path, delete and Insert are disabled until the user answers. `Clear`
    drops the latch with the model, and never touches the script.
  - `Insert → script` asks the server for the next free block name and passes
    it as `persist`, so a second insert is `sketch_profile2()` and the first
    block still resolves.
- **`frontend/js/api.js`** — `api.sketchBlocks(script)`.
- **`frontend/css/app.css`** — `.sk-banner` (neutral / warn / err) and
  `.sk-locked`.

## Files

- `agentcad/core/sketch_emit.py` — `persist`, `persist_spec`, `block_hash`,
  `wrap_block`, `parse_blocks`, `next_name`, `block_name`, the FR10 section of
  the module docstring
- `agentcad/core/tools_sketch.py`, `agentcad/server/routes_sketch.py`
- `frontend/js/sketcher.js`, `frontend/js/api.js`, `frontend/css/app.css`
- `tests/test_sketch_roundtrip.py` — **new**, 27 tests
- `docs/changelog/0140-sketch-roundtrip-spec.md` — this entry

## Verification

```
uv run pytest -q tests/test_sketch_roundtrip.py                 27 passed
uv run pytest -q tests/test_sketch_*.py tests/test_sketch.py   297 passed
node --check frontend/js/sketcher.js frontend/js/api.js         clean
```

The round trip is asserted **as bytes**: emit → parse → re-solve → re-emit is
byte-identical, including for a spec with arcs, an ellipse, a spline and a
slot, for a sketch-on-face spec (plane and caveat included) and for one with
construction geometry. A hand edit is `diverged`; a corrupt spec line, a
missing hash and a missing end marker are each `unverified` with a reason; two
blocks in one script parse independently and a hand edit to one leaves the
other `ok`.

**Real browser** (headless Chrome for Testing via Playwright, SwiftShader
WebGL, scratch server on port 52713 with a scratch projects dir — the user's
8630 was never touched, and the server was stopped afterwards). Every step
driven through the real handlers:

```
draw a closed 40x30 profile, Insert    toast 'sketch inserted — call sketch_profile() from build(p)'
                                       block written: marker + spec + hash + def + end marker
Clear, close, reopen                   banner 'editing sketch “profile” from the script — its spec
                                       and the code are in sync'  ·  66 SVG nodes restored  ·  6 DOF
hand-edit '20.0, 15.0' -> '20.0, 19.0'
Clear, close, reopen                   banner (sk-banner err) 'the emitted code was edited by hand,
                                       so it no longer matches the saved spec. The code is the
                                       source of truth for geometry — nothing here has been
                                       overwritten.'  buttons ['Re-solve from the spec',
                                       'Discard the spec']
                                       READ-ONLY: sketcher .sk-locked, Line disabled, Insert disabled
'Re-solve from the spec'               latch off, banner gone, geometry editable
Insert again                           toast 'sketch inserted — call sketch_profile2() from build(p)
                                       — the earlier block “profile” is still in the script, remove
                                       it if this replaces it'
                                       script: 2 markers, def sketch_profile / def sketch_profile2
Clear, reopen                          picker '2 saved sketches in this script:'
                                       buttons ['profile (diverged)', 'profile2', '✕']
open 'profile2'                        banner ok, geometry restored
Save & Rebuild                         error None · volume 40800 mm^3
                                       (60x60x10 = 36000, plus the round-tripped 40x30 profile
                                        extruded 4 mm = 4800)

CONSOLE ERRORS: NONE
```

Screenshots: `s13-a-inserted`, `s13-b-reopened`, `s13-c-divergence`,
`s13-d-resolved`, `s13-e-picker`, `s13-f-rebuilt`.

`make test` for this slice is reported with slice 14 in
`docs/changelog/0141-prd-009-completed.md`, which lands alongside it: **1769
passed, 1 skipped**, run in chunks (this sandbox caps a foreground command at
600 s and `test_parts_build_at_param_extremes[engine]` alone is ~890 s).

## Notes

- **The banner appends; it does not rewrite.** "Re-solve from the spec"
  discards the hand edit *in the sketcher*, and the next Insert writes a
  **new** block rather than replacing the diverged one — the toast says so.
  Replacing a range of the editor buffer needs an `editor.js` API this plan
  may not add (the permitted file list is the plan's, and `editor.js` is not
  on it), and appending is the behaviour FR10's "never silently overwrite"
  actually asks for: the user's text survives until they delete it. Recorded
  as a divergence from the design's parenthetical "(discards the hand edit)".
- **No new tool, and the route calls core directly.** `parse_blocks` is a
  pure text function — no project, no store, no kernel — and the PRD is
  explicit that the solver surface grows *keys*, not sibling tools. The
  route-pack precedent for calling straight into core is `routes_undo.py` and
  `routes_presence.py`. An agent that wants the same answer either reads the
  one-line JSON comment itself or imports `parse_blocks`; the tool description
  says so.
- **The hash covers the code, not the block.** That is what makes "the code
  was hand-edited" a precise claim — but it also means a deleted *end marker*
  would leave the hash matching over a body that runs to the end of the file,
  which is why a block with no end marker is `unverified` on its own account
  rather than by hash.
- **A slot's `initial` lesson applies here too:** the persisted spec is
  all-or-nothing. It is the spec as submitted, whole, so a reopened sketch
  cannot be a partially-seeded one.
- The GUI reads the script from CodeMirror, so a block inserted and not yet
  saved is still found on reopen — which is the state a user is actually in
  between "Insert" and "Save & Rebuild".
