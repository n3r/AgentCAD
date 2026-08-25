# PRD-018 De-risking SPIKE — Report

Repo: `/Users/nfedorov/dev/personal/cad_claude` · all proofs run against the
repo venv / scratch venvs; no repo files edited, no `uv sync`, no git mutation.

Proof artifacts (scratchpad `spike018/`):
- `test_spike_generate_loop.py` — PASSES on `.venv/bin/pytest` (real kernel, no network)
- `pdf_proof.py` — pypdfium2 rasterize + native text extract, PASSES
- `page1_150dpi.png`, `nopil_page.png` — valid PNGs produced

---

## A. Fake-client loop harness (FR14) — VERDICT: **WORKS** (out of the box)

The `ChatEngine(client_factory=…)` seam fully supports a scripted, deterministic
multi-tool loop. My throwaway test scripted a fake client through
`create_part → render_view → get_metrics → stop` against the **real registry and
real kernel** (build123d actually ran); it asserted tool order, event firing,
the vision block, and kernel metrics. Output:

```
SPIKE OK: tool order = ['create_part', 'render_view', 'get_metrics'] | volume = 1920.0 | image bytes(b64) = 10692
1 passed in 3.70s
```

### The exact minimal fake-client contract an orchestrator test needs
`ChatEngine` calls exactly one async method: `client.messages.create(**kwargs)`,
awaited, returning an object with `.content` (a list of blocks) and
`.stop_reason` (unused by the loop — it stops purely on "no `tool_use` blocks").
Blocks are duck-typed via `_block_to_dict` (`model_dump` / `vars` / dict), so
`types.SimpleNamespace` is enough. Minimal shape (lifted from
`tests/test_chat.py`):

```python
class FakeMessages:
    def __init__(self, responses): self._r = list(responses); self.calls = []
    async def create(self, **kwargs):        # <-- the ONLY method required
        self.calls.append(kwargs)
        return self._r.pop(0)
class FakeAnthropic:
    def __init__(self, responses): self.messages = FakeMessages(responses)

# a scripted round:
SimpleNamespace(content=[
    SimpleNamespace(type="text", text="…"),
    SimpleNamespace(type="tool_use", id="tu_1", name="create_part",
                    input={"project": P, "part_id": X, "script": SRC}),
], stop_reason="tool_use")
```

- **tool_use block**: `type="tool_use"`, `id`, `name`, `input` (dict → passed to
  `registry.call(name, input)`).
- **tool_result flowing back**: the engine appends
  `{"role":"user","content":[{"type":"tool_result","tool_use_id":id,"content":…}]}`
  automatically — the fake does NOT construct these; it only sees them re-sent as
  `create(**kwargs)["messages"]` on the next round, so the next scripted response
  can react to prior results.
- **termination**: a response whose blocks contain no `tool_use` (e.g. text-only,
  `stop_reason="end_turn"`) ends the loop. Also a hard stop at
  `MAX_TOOL_CALLS_PER_TURN = 30`.

**Nothing is missing** for a multi-tool script. The one caveat for PRD-018:
`generate.py` should NOT reuse `ChatEngine` unmodified — `ChatEngine` is a
single-turn conversational loop with a 30-call ceiling and no budget/spec state
machine. The reusable pieces are precisely: (1) the `client_factory` seam,
(2) `_block_to_dict`, (3) `_render_tool_result` (vision re-entry, see B),
(4) `_call_tool` tenant/identity threading, (5) the history-repair invariant.
The orchestrator wants its OWN loop that drives these with the budget/termination
logic of FR4 — but it is testable with the **identical** fake-client contract
above. That is the FR14 unknown, resolved: **no network needed, no new test
infra needed.**

---

## B. render_view vision re-entry (FR3 "look") — VERDICT: **WORKS TODAY**

`render_view` returns `png_base64` (`tools_vision.py:99`). `chat.py`'s
`_render_tool_result` (chat.py:152–180) detects a dict with a string
`png_base64` and rewrites the `tool_result` content into a **two-block list** —
a real Anthropic image block + a text block with the base64 stripped:

```python
[
  {"type": "image",
   "source": {"type": "base64", "media_type": "image/png", "data": <b64>}},
  {"type": "text",  "text": <json of result minus png_base64>},
]
```

My test confirmed this block is present in the transcript with real PNG bytes
(10 692 b64 chars) and that the bus event scrubs it (`png_base64:"<image
omitted>"`). So the model genuinely *sees* the render as vision — this is NOT a
gap. Any tool returning `png_base64` gets the same treatment (the existing
`test_tool_result_with_png_becomes_image_content` pins it). **Implication for
PRD-018:** the PDF-rasterization path (C) should return its page image under the
same `png_base64` key to ride this exact seam for free.

---

## C. PDF rasterization dependency — VERDICT: **WORKS, use pypdfium2**

**Recommendation: `pypdfium2` for rasterization + raw text; add `pdfplumber`
only if structured *tables* are needed. Reject pymupdf (AGPL) and poppler
(GPL).**

Evidence (scratch venv, macOS arm64, Python 3.12):

| Property | pypdfium2 5.13.0 |
|---|---|
| License | **BSD-3-Clause + Apache-2.0** (permissive — clean) |
| Python deps | **none** (`requires_dist: None`) |
| Install size | ~8 MB (7.1 MB bundled pdfium binary) |
| Rasterize p1 @150dpi | **39.7 ms** → 1241×1754 RGB, valid PNG |
| Native text extract | **YES** (`page.get_textpage().get_text_range()`) — no pdfplumber needed for raw text |

**Cross-platform wheels (all prebuilt, `py3-none-*` = ABI-agnostic, one wheel per
platform for all Py3):** macOS `arm64` + `x86_64`; manylinux `aarch64`,
`x86_64`, `armv7l`, `i686`, `ppc64le`, `s390x`, `riscv64`; musllinux (Alpine)
`aarch64`/`x86_64`/…; Windows `win32`/`amd64`/`arm64`. Covers the deployment
matrix (macOS dev, linux-aarch64/x86_64 Docker, Windows) with **no compiler**.

**Best finding — no Pillow needed for rasterization.** pypdfium2 renders to a
numpy array via `bitmap.to_numpy()`, and the repo *already* ships a
dependency-free PNG encoder (`agentcad/core/render.py::encode_png`, numpy+zlib
only; numpy is already the sole binary dep in `pyproject.toml`). Proven in a
`pypdfium2 + numpy` (no-Pillow) venv:

```
numpy buffer: (417, 417, 3) uint8
PNG via repo encode_png: 1440 bytes, header b'\x89PNG\r\n\x1a\n'
```

So the MVP PDF path adds **exactly one** new server dep (`pypdfium2`) and reuses
`encode_png`. Pillow enters only if you add `pdfplumber`.

**Existing repo rasterization capability:** NONE for *input* PDFs. What exists:
- `core/render.py` — hand-rolled numpy/zlib software renderer, mesh→PNG (reuse its encoder).
- `kernel/handlers/_pdf.py` — a deterministic **output**-only vector-PDF backend
  (drawing display-list → PDF, no reportlab/cairo). It lives in the **kernel**
  and only *writes* PDFs; it cannot read/raster one. PRD-018's rasterizer must
  live **server-side** (FR1 "rasterized server-side"; kernel-OCP-purity trap) —
  a separate concern from `_pdf.py`.

**Text vs tables (FR1/FR10):** pypdfium2 gives raw text runs (proven: extracted
"Bolt square: 31.0 mm … M3"). For *table structure* (cells/columns) use
`pdfplumber` 0.11.10 — pure-python `py3-none-any`, **MIT** (via pdfminer.six MIT +
pypdfium2), deps `pdfminer.six + Pillow + pypdfium2`. **Do not use pymupdf/fitz
(AGPL-3.0)** and **not poppler/pdftoppm (GPL)** — both fail the "license-clean,
server-process" bar in the PRD's open question.

---

## D. Budget/termination + half-write integrity (AC3) — VERDICT: **WORKS-WITH-CAVEATS**, seam identified

**What an abandoned candidate leaves behind (the risk is real):** `create_part`
(`service.py:315`) does a `store.add_part` then publishes `project_changed`,
which triggers `_snapshot_on_event` (`service.py:176`) → a git history commit.
So a candidate that writes a part and is then abandoned **does** leave (a) a part
in the manifest and (b) history snapshot(s). FR4's "never a half-written project
state" is therefore an active requirement, not a freebie.

**Cleanup seam (verified clean):** `delete_part` (`service.py:465`) removes the
manifest entry, the `_status` key, and every `_config_status` key under it, then
publishes. My test asserted `delete_part → {"deleted": PART}` and that the part
is fully gone from `get_project`. So the seam exists and is atomic under
`self._lock`.

**MVP cleanup contract (no PRD-001 required):**
1. Each candidate `n` works on a scratch `part_id` (e.g. `__gen_<gen_id>_<n>`).
2. The loop runs its `create_part`/`update_part_script` iterations on that id.
3. On terminate: exactly one candidate is **accepted** → it is renamed/copied to
   the user's target id and its provenance stamped; **every** other scratch id
   (including the accepted one's scratch name) is `delete_part`'d.
4. Budget exhaustion returns best-so-far metadata (`spec_green:false`, named
   failing checks) but still deletes all scratch parts → no orphan.

Caveat: because each write commits a history snapshot, the scratch-part churn is
visible in history until deleted (delete also snapshots). That is acceptable for
MVP (undoable, attributable) but means "no orphaned part" is about the **live
manifest**, not about zero history entries. Worth stating explicitly in the spec.

**Branch-per-candidate (Phase 2):** branch infra **is present** —
`core/branches.py` + `core/_worktree.py`, and tools `branch_create` /
`branch_switch` / `branch_delete` / `merge_branch` (`tools_versioning.py`). So
the `gen/<id>/<n>` branch path (FR12) is buildable when chosen; the scratch-id
fallback above is the MVP that needs none of it. Either way `delete_part` /
`branch_delete` is the teardown primitive.

---

## E. Knowledge-pack standards grounding (FR10) — VERDICT: **WORKS** (already shipped)

PRD-029 packs are **not prose-only** — they already carry machine-readable data
files. `agentcad/skills/brackets-and-mounts/tables/nema.json` exists today and
contains exactly the NEMA-17 numbers PRD-018 AC2 needs:

```json
{ "frame": "NEMA 17", "flange_mm": 42.3, "bolt_square_mm": 31.0,
  "pilot_d_mm": 22.0, "screw": "M3", "clearance_d_mm": 3.4, "shaft_d_mm": 5.0 }
```

Siblings: `fits-and-clearances/tables/iso286.json`, `snap-fits/tables/
material_strain.json`. Pack layout is `SKILL.md` (frontmatter + prose) **plus**
`snippets/*.py` and `tables/*.json` assets.

**Deterministic read path (no free-prompting):** the orchestrator's
intent-normalization step reads the table server-side, not via the model:
`SkillLibrary.load(name="brackets-and-mounts", asset="tables/nema.json")`
returns the file **verbatim** (`skills.py::_read_asset`, exposed to agents as the
`load_skill` tool with an `asset=` arg). Server-side the orchestrator calls
`library.load(...asset=...)["content"]` and `json.loads` it, then looks up
`frames[frame=="NEMA 17"]` and places `bolt_square_mm=31.0`, `pilot_d_mm=22.0`,
`clearance_d_mm=3.4` directly into the intent record + generated PARAMS — the
model never types the numbers, satisfying the "cite, don't invent" risk
mitigation. The intent record cites `pack=brackets-and-mounts,
table=tables/nema.json`.

**Smallest real example** (already in-repo, no new data needed for AC2). If a
future standard needs a *new* table with no natural skill home, add a
`standards/` pack (same format: a `SKILL.md` stub + `tables/*.json`); the loader
(`scan_layer`, digest-hashed assets) already treats any `tables/*.json` as a
first-class asset. **No new grounding mechanism to build** — the data model and
the deterministic loader both exist.

---

## Bottom-line decisions for the implementer

1. **Loop-test harness:** the `client_factory` fake-client contract (one async
   `messages.create` returning `SimpleNamespace(content=[...])`) is sufficient
   and proven with the real kernel; build `generate.py` as its own budgeted loop
   reusing `_render_tool_result` + `_block_to_dict`, tested with that fake.
2. **PDF dep:** `pypdfium2` only (BSD/Apache, zero-Python-dep, all-platform
   wheels, native text); rasterize via `bitmap.to_numpy()` → reuse
   `render.py::encode_png` (no Pillow). Add `pdfplumber` (MIT) only for table
   structure. Never pymupdf/poppler. Return the page under `png_base64` to reuse
   the vision seam.
3. **Grounding:** load standards from existing `tables/*.json` skill assets via
   `SkillLibrary.load(asset=…)` server-side; NEMA-17 data already ships.
4. **Half-write integrity:** scratch `part_id` per candidate + `delete_part` on
   non-accept for MVP; branch infra (`branches.py` + `branch_create`) is present
   for the Phase-2 `gen/<id>/<n>` path.
