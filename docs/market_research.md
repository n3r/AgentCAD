# Market Research — AgentCAD and the CAD Landscape

*August 2026. Compiled from primary-source web research (vendor docs, release
announcements, pricing pages, user forums, funding news, academic benchmarks)
plus the current state of this repo (v0.1 → v5, 107-tool agent surface). This
document is the evidence base; the conclusions it feeds live in
[roadmap.md](roadmap.md) and the PRDs under [prd/](prd/). Part I is the
competitive landscape; Part II holds the deep dives commissioned for specific
PRDs (materials data, native CAD import, marketplaces, physics engines).
Sources are linked per section.*

## The one-paragraph read

Every serious CAD vendor spent 2025–26 racing to add AI to architectures that
fight it: Onshape is *promising* permissioned agents and a FeatureScript MCP
server through an early-access program, Autodesk ships MCP servers and demos
"Neural CAD," Dassault previews virtual companions, PTC's Creo "Automate" is
an alpha still fighting hallucinations. Meanwhile the AI-native startups (Zoo,
AdamCAD, Backflip) have the right instincts — several are converging on
exactly AgentCAD's architecture, with AdamCAD's founders publicly conceding
build123d is the stronger substrate — but none has meaningful engineering
depth (no drawings, GD&T, sheet metal, or simulation anywhere among them), and
open-source CAD has no cloud story at all (Ondsel's shutdown is the cautionary
tale). AgentCAD holds a genuinely unusual position: real OCCT B-rep depth
*and* a natively agent-first architecture. What it lacks is the entire cloud
axis — hosting, identity, sharing, branch/merge collaboration, review — plus
a handful of table-stakes modeling features and any generation front door.
The roadmap conclusion: ship the collaboration model incumbents structurally
cannot copy (change-based, kernel-refereed, human+agent) before their
bolted-on agents normalize, then add daily-driver depth and the ecosystem
loops on top of it.

## Where AgentCAD stands today

What exists (v3, verified against the repo): parts as build123d Python
scripts with typed PARAMS; kernel-refereed rebuilds with structured,
hint-enriched errors; assemblies with rigid/revolute/cylindrical mates and
driven-DOF motion sweeps with interference checking; STEP/BREP/STL import; 2D
drawings with detected dimensions and hole callouts; PMI/GD&T with tolerance
stack-ups (worst-case + RSS); sheet metal with flat patterns; surfacing
helpers with curvature verification; linear-static/modal/thermal FEM; 30
engineering materials; a GUI sketcher and face push/pull that *emit script
edits*; server-side renders so agents can see the model; git-backed
undo/history; per-project turn locks and concurrent multi-agent chat
sessions; mesh LOD; macOS-sandboxed script execution; a 107-tool surface
exposed identically over MCP, built-in chat, and REST.

What does not exist: any network deployment beyond `127.0.0.1`; any concept
of a second human user; branches, merge, or review (history is linear);
sharing of any kind; text/image-to-part generation; a parts library or
package ecosystem; configurations; assembly drawings/BOMs; DFM/costing;
CAM or slicer handoff; arcs/splines in the sketcher.

## The landscape

### Cloud-native CAD: Onshape (the collaboration benchmark)

Onshape is the existence proof that cloud-native CAD wins teams: a document is
a database of immutable "microversions" (per-edit deltas against stable
feature IDs), which yields simultaneous same-model editing, follow mode,
per-user undo, feature-anchored comments, and a complete audit trail. Its
killer sales pitch is **PDM built in** — release workflows assign revisions
against immutable versions with no vault and no check-in/out, and releasing is
non-blocking. Breadth kept growing through 2025–26: CAM Studio, in-assembly
linear-static simulation, Render Studio, MBD, frames with cut lists, mature
configurations. Pricing runs free (public documents) → ~$1,500 → ~$2,500/user/yr;
2M+ users; FIRST robotics is a de-facto standard; PTC courts A&D startups
with free Professional seats.

The load-bearing weaknesses, from its own forums and docs:

- **Branch/merge is shallower than it looks.** Merging is per-tab with three
  strategies (keep/merge/replace) — no cherry-pick, no reviewable conflict
  resolution; on conflicting edits "the from workspace wins." Last-writer-wins
  by design, because binary database deltas cannot be reviewed by a human.
- **No offline mode, architecturally.** The #1 recurring complaint, and
  unfixable for them.
- **API access is metered per company** (a "85 requests/day" tier change
  caused a forum uproar) — a structural mismatch with API-hungry agent
  workflows.
- **Extensibility is jailed**: FeatureScript is a proprietary DSL with no
  network access and no automation reach; third-party apps live in iframe
  tabs and cannot extend the modeling core.
- Drawings and large assemblies are persistently slow ("drawings so slow they
  are unusable" is a real thread title).

On AI: AI Advisor (guidance chat) shipped Oct 2025; **Onshape Labs** (July
2026) promises AI agents with defined, revocable permissions, an AI drawing
checker, and a FeatureScript MCP server marketed as "Text-to-Code-to-CAD."
PTC's public thesis — that captured design history gives AI something to
learn from — is a direct validation of AgentCAD's bet, implemented as a
bolt-on. Contrast: Fusion Team is file-sync collaboration with component
locking (not cloud-native); 3DEXPERIENCE xDesign is browser CAD under a
heavyweight PLM platform.

Sources: [Onshape "Under the Hood"](https://www.onshape.com/en/blog/under-the-hood-how-collaboration-works),
[merging semantics](https://cad.onshape.com/help/Content/Document/merging.htm),
[release management](https://www.onshape.com/en/features/release-management),
[API limits](https://onshape-public.github.io/docs/auth/limits/) and [the 85/day thread](https://forum.onshape.com/discussion/27917/new-api-limits-85-requests-per-day-per-company),
[PTC Onshape Labs](https://www.ptc.com/en/news/2026/onshapelabs),
[AI Advisor](https://www.ptc.com/en/news/2025/ptc-announces-latest-onshape-ai-advisor-release),
[2026 features](https://www.onshape.com/en/blog/assembly-mirroring-mbd-adaptive-cutting-cam-linear-static-simulation-slack-integration),
[pricing](https://www.onshape.com/en/pricing),
[drawings-slow thread](https://forum.onshape.com/discussion/15067/drawings-so-slow-they-are-unusable).

### The desktop incumbents: Fusion, SolidWorks, Creo, NX

**Fusion** (~$680/yr, free personal tier) owns makers and prototyping startups
because CAM is bundled — design-to-toolpath in one tool — plus PCB, sim, and
render. Its 2025 AI shipped for real: AutoConstrain, Automated Drawings,
Automated Modeling. At AU 2025 Autodesk announced "Neural CAD" foundation
models (editable B-rep from text/sketch/image, "soon") and an agentic
Assistant, and it now publishes **two official MCP servers**. Weaknesses:
degrades above ~500-component assemblies, no configurations depth, weak
drawing standards, no weldments, chronic stability complaints.

**SolidWorks** (~7.5M users) remains the mid-market default and defines
"professional depth": configurations/design tables, weldments with cut lists
(unmatched), Hole Wizard + Toolbox, decades of drawing templates, routing,
mold tools, large-assembly modes. SW2026 ships one-click AI drawing
generation. Dassault's 3DX World 2026 went "all-in on AI" (Aura, Leo, Marie
companions) — all gated behind the 3DEXPERIENCE platform whose forced
adoption and pricing users resent. Automation remains 1990s COM/VBA;
community MCP servers literally fall back to generating VBA macros —
the architecture fights agents.

**Creo** has top-end parametric power and best-in-class real-time simulation
(embedded Ansys); its 2026 "Triple A" AI ladder (Advise shipped, Assist beta,
Automate alpha) is honest about still fighting hallucinations. **NX** is the
broadest high-end system, now reachable as SaaS (NX X, from ~$247/mo);
Solid Edge 2026 adds AI auto-drawings (~80% of views/dimensions) and magnetic
snap mating.

For AgentCAD's target users (rocketry, robotics, construction, prototyping
startups, serious makers), the incumbent features that actually matter:
configurations, standard content (fasteners/hole standards), drawing-standards
depth, weldments/frames, large-assembly semantics, and CAM access. The
enterprise verticals (mold/die, ship structures, harness routing, ITAR PLM)
do not.

Sources: [Fusion AI features](https://www.engineering.com/3-new-ai-features-in-autodesk-fusion/),
[Neural CAD at AU 2025](https://www.engineering.com/autodesk-introduces-neural-cad-at-au-2025/),
[Fusion MCP servers](https://www.engineering.com/autodesk-announces-fusion-mcp-servers-and-more-ai-updates/),
[SW 2026](https://www.solidworks.com/product/whats-new),
[3DX World 2026 AI](https://hawkridgesys.com/blog/3dx-world-solidworks-ai),
[what you lose leaving SolidWorks](https://cadshift.com/blog/switching-from-solidworks-to-fusion-360-what-you-lose/),
[SolidWorks MCP (VBA fallback)](https://andrewbartels1.com/SolidworksMCP-python/),
[Creo Triple-A AI](https://develop3d.com/cad/creo-ai-ptc-advances-its-roadmap),
[Solid Edge 2026](https://news.siemens.com/en-us/siemens-designcenter-solid-edge-2026/),
[NX X](https://blogs.sw.siemens.com/nx-design/benefits-of-cloud-saas-nx-x-cad/).

### AI-native CAD: Zoo, AdamCAD, Backflip, and the research frontier

**Zoo** (ex-KittyCAD) is the closest competitor: proprietary GPU geometry
engine, the KCL language, Design Studio (v1 May 2025), Text-to-CAD API, and —
since Jan 2026 — **Zookeeper**, a Plan→Act→Observe agent that writes,
executes, and debugs KCL with engine feedback (visual snapshots, mass), plus
multimodal input since Mar 2026. It is architecturally the nearest thing to
AgentCAD — and the differences are instructive: the engine and DSL are
proprietary; there are no drawings, PMI/GD&T, sheet metal, or simulation; no
published evals; funding is thin (~$10M, still seed-stage as of Aug 2026);
and their marketing line "no mechanical engineer ever has to touch KCL"
treats code as an implementation detail rather than the shared artifact —
the opposite of a review-centric model.

**AdamCAD** (YC, $4.1M) rode text-to-CAD virality (1M+ models generated) on
an open-source LLM→OpenSCAD tier plus an OpenCascade copilot; its founders
publicly conceded CadQuery/build123d are more powerful and roadmapped them —
the market converging on AgentCAD's substrate. **Backflip** ($30M, NEA/a16z)
GA'd scan/mesh→parametric-CAD-with-feature-trees in Aug 2026; impressive but
"none were without error" per hands-on reviews — a reverse-engineering
adjunct, not an authoring system. **Spectral SGS-1** generates B-rep STEP via
diffusion (research preview; no assemblies, thin walls fail). **Meshy/Tripo**
raised enormous rounds for text→mesh, which is not CAD (no parametrics, no
exact surfaces, no tolerances).

The academic frontier confirms the architecture bet: Embodied CAD shows
solver-grounded iterative agents beat single-pass generation ("reliable
industrial CAD modeling requires more than syntactically valid code");
Text2CAD-Bench and MUSE document cascading failures of one-shot generation on
real engineering criteria; the BenchCAD lineage reached ~92% IoU by scaling
*code* corpora. Meanwhile 9+ community MCP servers (Fusion, FreeCAD, Blender,
Rhino, Onshape) plus Autodesk's official ones prove agent-driven CAD is now a
category — and nearly all of them are GUI/API drivers with no validation
loop and no structured, agent-legible errors.

Sources: [Zoo Design Studio v1](https://zoo.dev/blog/zoo-design-studio-v1),
[Zookeeper](https://zoo.dev/research/zookeeper),
[Zoo Mar 2026](https://zoo.dev/blog/whats-new-mar-2026),
[Adam launch + critique](https://news.ycombinator.com/item?id=48572553),
[Adam/OpenSCAD analysis](https://www.developersdigest.tech/blog/adam-ai-cad-yc-w25-open-source-text-to-cad),
[Backflip GA](https://www.businesswire.com/news/home/20260803007022/en/Backflip-AI-Launches-CAD-Copilot-That-Transforms-3D-Scans-Into-Engineer-Quality-Editable-CAD-Models),
[Backflip hands-on](https://www.engineering.com/backflips-back-is-the-mesh-to-cad-ai-real-this-time/),
[SGS-1](https://www.spectrallabs.ai/research/SGS-1),
[Embodied CAD](https://arxiv.org/pdf/2606.31252),
[Text2CAD-Bench](https://arxiv.org/abs/2605.18430),
[MUSE](https://arxiv.org/html/2605.28579),
[9 CAD MCP servers](https://snyk.io/articles/9-mcp-servers-for-computer-aided-drafting-cad-with-ai/).

### Open-source CAD: FreeCAD, code-CAD, and the Ondsel lesson

**FreeCAD** (1.0 Nov 2024; 1.1 Mar 2026; ~850k–1.25M users) finally mitigated
topological naming and integrated assemblies, but remains short of commercial
polish (surfacing, drawings, UX), has no company behind it, and no cloud.
**Ondsel** — the VC-funded attempt to build a business on FreeCAD plus a
closed cloud layer (Lens) — shut down Oct 2024 after finding hobbyist
enthusiasm but no commercial adoption. The autopsy matters: a cheaper
SolidWorks is not a wedge (a seat is ~3% of an engineer's cost); polish flows
upstream and erases itself; hobbyists validate loudly and pay nothing; and
the closed cloud layer died with the company. Its survey still found **>75%
of FreeCAD users willing to pay** — for reliability, not features.

Code-CAD: **OpenSCAD** (CSG/mesh only, no B-rep/STEP) matters mostly as
culture — Thingiverse Customizer and Bambu MakerWorld's Parametric Model
Maker prove millions will *consume* parametric models through sliders.
**CadQuery** (5.6k stars) and **build123d** (2.8k stars, the momentum choice
AgentCAD sits on) are the Python B-rep substrate; PartCAD's "package manager
for CAD" remains embryonic (483 stars, registry "in progress") — npm-for-parts
is unclaimed territory. **chili3d** (4.7k stars, OCCT 8 via WASM in the
browser, AGPL app + LGPL kernel) validates browser-hosted OCCT but has no
parametrics, scripts, or collaboration. The kernel graveyard (Fornjot
abandoned, CADmium archived) says: reuse OCCT; don't write kernels. McMaster-
Carr remains the de-facto standard-parts infrastructure engineers design
around. Licensing is clean for us: OCCT is LGPL-with-exception (server-side
SaaS triggers no distribution obligations; browser-WASM would).

Sources: [FreeCAD 1.1](https://blog.freecad.org/2026/03/25/freecad-version-1-1-released/),
[Ondsel post-mortem coverage](https://hackaday.com/2024/11/12/the-end-of-ondsel-and-reflecting-on-the-commercial-prospects-for-freecad/),
[Ondsel goodbye](https://www.ondsel.com/blog/goodbye/),
[survey pt3](https://www.ondsel.com/blog/freecad-user-survey-results-part-3/),
[build123d](https://github.com/gumyr/build123d),
[PartCAD](https://github.com/partcad/partcad),
[chili3d](https://github.com/xiangechen/chili3d),
[MakerWorld PMM](https://all3dp.com/4/bambu-labs-parametric-model-maker-brings-openscad-to-makerworld/),
[OCCT license](https://dev.opencascade.org/resources/download/occt-public-license).

### The workflow ring: sim, review, DFM/quoting, print, CAM

CAD never lives alone, and the surrounding workflow is where 2025–26 moved
fastest:

- **Design review is a proven AI wedge**: CoLab raised a $72M Series C after
  launching AutoReview (an AI review agent; 47k-engineer waitlist) on top of
  its pin-feedback-on-CAD product. Review today is screenshots in slides.
- **Quote + DFM inside CAD just became table stakes**: Siemens invested ~$50M
  in Xometry (May 2026) to embed instant quoting/DFM in NX; Xometry add-ins
  already live in SolidWorks/Fusion/Onshape; Protolabs ships free DFM with
  every quote. Per-process DFM rules (CNC corner radii and tool access, 3DP
  min-wall/overhangs, sheet bend/hole-to-bend, IM wall uniformity/draft) are
  stable, published, and automatable by a geometry kernel *before* upload.
- **Simulation is going agentic**: SimScale markets agentic setup→run→
  evaluate ("Engineering AI via API," Summer 2026); Ansys Discovery does
  GPU real-time sim while modeling; physics-AI surrogates raised mega-rounds
  (PhysicsX $300M, Neural Concept $100M) — and surrogates are trained on
  exactly the mass-generated labeled geometry a script-native CAD can produce.
- **Print handoff is scriptable**: 3MF became an ISO standard (June 2025);
  the Prusa/Bambu/Orca slicer lineage all ship headless CLIs; server-side
  slicing services already exist.
- **CAM**: Fusion's integrated CAM is a decade-deep moat with a post-processor
  zoo; no OSS CAM at professional quality. The minimum credible manufacturing
  handoff for a new platform is clean STEP + PMI + standards-correct drawings
  + DFM-checked instant quotes — *not* toolpaths.
- **nTop** (implicit modeling, effectively $10k+/seat) proves engineers pay
  for geometry B-rep can't do (lattices/TPMS) and that headless automation
  (nTop Automate) is an enterprise SKU. **Shapr3D** proves adaptive
  direct+parametric UX sells. **Valispace** (requirements↔engineering
  budgets) was absorbed into Altium — that traceability niche is ownerless.
- **Formats**: a next-gen cloud CAD must speak STEP (authoritative), 3MF
  (print), glTF (web review), and increasingly USD; STL is legacy.

Sources: [CoLab $72M](https://www.startuphub.ai/ai-news/funding-round/2025/colab-raises-72m-to-advance-its-ai-engineering-platform/),
[Siemens+Xometry](https://www.engineering.com/siemens-and-xometry-to-bring-instant-quoting-dfm-analysis-to-designcenter/),
[Xometry add-ins](https://www.xometry.com/cad-add-ins/),
[Protolabs DFM](https://www.protolabs.com/resources/design-tips/navigating-manufacturing-analysis/),
[SimScale](https://www.simscale.com/), [Ansys Discovery 2025 R2](https://ansys.synopsys.com/blog/whats-new-ansys-discovery-2025-r2),
[3MF ISO](https://www.polyvia3d.com/formats/3mf),
[Bambu CLI](https://printago.io/blog/bambu-studio-cli-reference),
[nTop Automate](https://www.ntop.com/software/ntop-automate/),
[Valispace→Altium](https://www.aviaspace-bremen.de/en/2024/05/22/elevating-engineering-with-valispaces-acquisition-by-altium/),
[OpenUSD 1.0](https://aousd.org/news/core-spec-announcement/).

## The 2025–26 convergence (why now)

Five independent signals say the agent-CAD window is open but closing:

1. **Incumbents announced our thesis.** Onshape Labs' "Text-to-Code-to-CAD"
   FeatureScript MCP, Autodesk's official MCP servers + agentic Assistant,
   Dassault's companions, Creo Automate. All bolted onto architectures whose
   model isn't reviewable code — but shipping within 6–18 months.
2. **AI-native startups validated demand** (Adam's 1M generated models,
   Zookeeper) while proving none of them will have engineering depth soon.
3. **Research settled the method**: kernel-grounded iterative code generation
   beats one-shot everything; validation is the bottleneck. AgentCAD's
   architecture is the reference implementation of the winning approach.
4. **The workflow ring is agentizing** (CoLab AutoReview, SimScale Engineering
   AI, Xometry-in-NX): each neighbor is automating its slice; the platform
   that owns the *model + validation loop* can orchestrate all of them.
5. **Open source has a vacuum**: no OSS cloud CAD, no OSS text-to-CAD, a dead
   Ondsel, an unclaimed parts registry, and >75% of FreeCAD users saying
   they'd pay for something better.

## Gap matrix

Verdicts: **build** (table stakes we must have), **build-differentiated**
(build it the human+agent way, not the incumbent way), **integrate** (partner
or connect, don't build), **skip** (deliberately out).

| Capability | AgentCAD today | Best in class | Verdict |
|---|---|---|---|
| Hosted multi-tenant cloud, orgs, permissions | none (localhost) | Onshape | build |
| Share links / embedded viewer / publish-with-sliders | none | Onshape free, MakerWorld | build |
| Branch/merge + named versions | linear git snapshots | Onshape (last-writer-wins) | build-differentiated (semantic merge, real conflicts) |
| Change review (proposals, diffs, approvals) | none | CoLab (bolt-on), nobody in-CAD | build-differentiated (CAD pull requests) |
| Release management / revisions | none | Onshape built-in PDM | build (on git substrate) |
| Anchored comments / review threads | none | Onshape | build |
| Real-time co-presence | turn locks | Onshape | build (per-part concurrency + presence first; same-file CRDT later) |
| Executable design specs / requirements traceability | implicit in tests | Valispace (orphaned) | build-differentiated (design-tests, unclaimed) |
| Geometry CI on changes | none | nobody | build-differentiated (unclaimed) |
| Text/image→part generation | none | Zoo Zookeeper | build-differentiated (kernel-grounded loop, open stack) |
| Public agentic-CAD evals | none | nobody publishes | build-differentiated (first mover wins narrative) |
| Sketcher completeness (arcs/splines) | points/lines/circles | every incumbent | build |
| Patterns / hole wizard / standard features | partial (toolkit) | SolidWorks | build |
| Configurations / design tables | typed PARAMS only | SolidWorks, Onshape | build |
| Standard parts + fastener libraries | bd_warehouse threads | SolidWorks Toolbox, McMaster | build-differentiated (agent-validated open registry) |
| Weldments / frames + cut lists | none | SolidWorks ("unmatched") | build (high target-fit) |
| Assembly drawings, BOM, balloons, title blocks | part drawings only | SolidWorks | build |
| Structured BOM + exports | assembly mass only | every PLM-lite | build |
| Large-assembly semantics (1k+ instances) | mesh LOD only | NX, SolidWorks modes | build |
| Richer joints, exploded views, URDF | 3 mate types | Onshape (URDF), FreeCAD 1.x | build |
| Interop: STEP AP242 PMI, 3MF, glTF, USD | STEP/STL/3MF basic | NX/HOOPS ecosystem | build |
| DFM checks + cost models | none | Protolabs/Xometry (post-upload) | build-differentiated (open rule packs, pre-quote) |
| Instant quotes | none | Xometry-in-NX | integrate (APIs) |
| Slicer/print pipeline | 3MF export only | Bambu/Prusa/Orca CLIs | integrate (orchestrate CLIs) |
| High-fidelity simulation (CFD, nonlinear) | static/modal/thermal | SimScale, Ansys | integrate (burst to APIs) |
| CAM / toolpaths | none | Fusion | integrate via STEP+PMI handoff; skip building |
| Scan/mesh→parametric | STL import (mesh-only) | Backflip | integrate (import assist) |
| Implicit modeling / lattices | none | nTop | skip (interop later if demanded) |
| VR concepting | none | Gravity Sketch | skip |
| Own geometry kernel / own DSL / B-rep foundation model | OCCT + Python (correct) | Zoo's engine+KCL | skip (their mistake, not ours) |

## Where AgentCAD wins (structural advantages)

1. **The model is reviewable.** Onshape's deltas are binary database
   operations; SolidWorks' are a proprietary file; Zoo hides its DSL from
   engineers. AgentCAD's model is Python that humans and frontier models
   both read natively — which makes diffs, cherry-picks, blame, review, and
   *real* merge conflicts possible. Nobody else can ship a CAD pull request.
2. **The kernel already referees.** Benchmarks (MUSE, Embodied CAD) show
   validation is the bottleneck in agent-driven CAD; AgentCAD's structured
   error contract + metrics-after-every-mutation is the reference shape of
   the winning approach, and extends naturally to CI, design-tests, DFM
   packs, and standards linting.
3. **Agents are first-class, not bolted on.** Identity, turn semantics,
   sessions, vision (render_view), and one tool registry across MCP/chat/UI
   already exist — what PTC is promising for late 2026, shipped natively.
4. **Headless and license-free by birth.** Per-seat GUI licensing and COM
   bridges make agent fleets economically impossible on incumbent stacks;
   Onshape meters the API. Open source + self-host + compute-metered cloud
   is a wedge their business models can't follow.
5. **Local-first with cloud sync beats cloud-only.** Onshape's unfixable
   complaint (offline) and the A&D/air-gap niche are free wins for a
   git-substrate system that runs on a laptop.
6. **Open engineering depth no AI-native rival has.** Drawings, GD&T,
   stack-ups, sheet metal, FEM — already here, already agent-accessible.
   That combination (depth × agent-nativeness) is currently unique.

## Business-model guardrails (the Ondsel constraint)

Not a business plan — three constraints the evidence imposes on the roadmap:

- **Sell what local/desktop fundamentally can't do**: hosted collaboration,
  shared review, fleets of agents, burst compute (FEM/studies/renders).
  Never sell a polished shell (it upstreams away) or "cheaper CAD" (price
  isn't the wedge; a seat is ~3% of an engineer's cost).
- **Keep the cloud layer open source** so trust survives the company
  (Ondsel's closed Lens died with it; KiCad's institutional-anchor arc is
  the proven OSS funding shape). Monetize hosting and metered compute/agent
  hours — never per-seat licenses (hostile to agent fleets), never metered
  APIs (Onshape's 85/day own-goal), never public-documents free tiers.
- **Hobbyists validate, teams pay.** The free local-first tier and the
  publish/customize channel are the top of the funnel; the paying customer
  is the team whose human+agent workflow is impossible anywhere else.

## What we deliberately will not build

- **A geometry kernel** (Fornjot/CADmium graveyard; Zoo's seed-years sink) —
  OCCT + pinned build123d, upstream fixes.
- **A proprietary CAD language** — Python *is* the interface; KCL and
  FeatureScript both demonstrate the DSL tax (fights LLM priors, fragments
  ecosystem, invites "engineers never touch it" positioning).
- **CAM/toolpathing** — decade-deep safety-critical moat; handoff is
  STEP + PMI + drawings + DFM-checked quotes.
- **A B-rep foundation model** — capital-intensive, data-poor, error-ridden;
  integrate Backflip/SGS-1-class models as import assists when useful.
- **Mesh-generation text-to-3D** — not CAD; wrong artifact for engineering.
- **Enterprise PLM ceremony, vault PDM, per-seat licensing, iframe app
  stores, one-shot generation as the primary UX, mouse-first feature-parity
  with SolidWorks** — each is a documented incumbent liability, not an asset.
- **Same-file CRDT co-editing as the first collaboration step** — per-part
  concurrency + proposals deliver the value earlier; live co-editing of one
  script is a later polish, not the foundation.

---

# Part II — Deep dives (August 2026)

Commissioned to ground specific PRDs. Same method: primary sources, linked.

## Materials data (grounds PRD-028)

**Commercial sources are license walls, not ingestion options.** MatWeb
(115k+ datasheets) contractually forbids cataloging its database — personal
extracts are capped at 500 materials, never for public release; its business
is licensing data *into* CAD/FEA tools. MakeItFrom's EULA is personal and
non-transferable. UL Prospector (45,000+ Yellow Cards for plastics) is
$49–119/user/month, supplier-fed, non-redistributable. Ansys Granta's
MaterialUniverse is the model for the *shape* of a credible library: ~4,000
**generic** records with property ranges — not 45k supplier grades. MMPDS
(FAA-accepted statistical aerospace allowables) is per-seat and strictly
non-redistributable — link out, never ingest.

**Legal nuance.** Under *Feist* (US), property values are uncopyrightable
facts; but the EU Database Directive's sui generis right protects any
substantial-investment database against extraction regardless, and site ToS
bind as contract. Practical rule: hand-collect values fact-by-fact from
multiple primary sources into our own selection/arrangement, with per-value
citations; bulk extraction of any single database is off-limits.

**Genuinely open:** NIST (public domain: TRC alloy data, WebBook, cryogenic
DB); standards-defined values (EN 338 timber classes — C24 ⇒ f_m,k 24 MPa,
E_0,mean 11 GPa, ρ_k 350 kg/m³; EN 206/ACI 318 concrete classes; ASTM/EN
alloy grade minima); manufacturer datasheets cited fact-by-fact. FreeCAD
ships ~100 core material cards plus the community **Supplemental-Materials**
repo (data CC-BY-SA-4.0, PR-gated) — the direct OSS precedent for
crowd-maintained engineering materials with a validation gate.

**What tools ship:** SolidWorks ~500 materials + ~100 curves (overflow via
Matereality's paid portal); Fusion a modest library + an additive process
library; Onshape small built-in + company libraries; nTop starter set only.
All disclaim "reference only — verify with your supplier"; temperature
dependence is tabulated (T, value) pairs with linear interpolation
(SolidWorks/Ansys convention). Per-calculation needs: static FEM E/ν/ρ/σ_y;
modal E/ν/ρ; thermal k/c_p/CTE/T_max; cost $/kg×ρ; sheet-metal K-factor;
process suitability (machinability, weldability, UL94, melt/glass temps).

Sources: [MatWeb ToU](https://www.matweb.com/reference/terms.aspx) ·
[MatWeb licensing](https://www.matweb.com/services/databaselicense.aspx) ·
[Feist](https://supreme.justia.com/cases/federal/us/499/340/) ·
[EU database right](https://europa.eu/youreurope/business/running-business/intellectual-property/database-protection/index_en.htm) ·
[Granta Selector datasheet](https://www.ansys.com/content/dam/product/materials/granta-selector/granta-selector-product-datasheet.pdf) ·
[MMPDS](https://www.mmpds.org/) · [NIST TRC](https://trc.nist.gov/metals_data/) ·
[FreeCAD Supplemental-Materials](https://github.com/FreeCAD/Supplemental-Materials) ·
[EN 338 C24 values](https://www.dataholz.eu/fileadmin/dataholz/media/baustoffe/Datenblaetter_en/vh_en_01.pdf) ·
[SolidWorks ~500 materials](https://www.cati.com/blog/access-to-more-materials-than-ever/) ·
[SW temperature-dependent properties](https://help.solidworks.com/2016/english/solidworks/cworks/t_Defining_Temperature-Dependent_Material.htm) ·
[UL Prospector](https://www.ulprospector.com/plastics/en/yellowcards) · [JAHM MPDB](https://www.jahm.com/).

## Native CAD import (grounds PRD-032, extends PRD-017)

**Commercial translators:** HOOPS Exchange reads 30+ formats but runs
~$33k/yr + ≥$20k/yr distribution — impossible in an OSS distribution.
CAD Exchanger: per-format SDK licensing plus a **Cloud API priced per
conversion** — the realistic hosted-connector route. Datakit: à-la-carte
per-format. ODA: membership model, but its gratis **ODA File Converter**
binary is what FreeCAD shells out to for DWG⇄DXF — the canonical
OSS-compatible pattern (external process, no linking). Autodesk APS Model
Derivative: cloud translation of ~60 formats including SLDPRT, CATPart,
NX/Creo .prt, JT, Rhino at ~0.1 flex token per simple job.

**Open-source reality:** OCCT 7.8+ itself reads STEP AP203/214/242 (XCAF
names/colors/some PMI), IGES, BREP, glTF 2.0 (incl. Draco), OBJ, STL, VRML,
PLY; 3MF via lib3mf (BSD) and DXF via ezdxf (MIT) are cheap adds. Parasolid
is a hard wall (closed spec; SLDPRT's geometry is an embedded Parasolid
stream — GitHub "SLDPRT readers" parse metadata, not B-rep). JT is ISO 14306
nominally, but the practical toolkit is gated behind Siemens' program.
LibreDWG (GPLv3) is the fully-free DWG fallback.

**Competitors:** Onshape translates server-side (Parasolid kernel helps for
SLDPRT/NX/Solid Edge): SOLIDWORKS 1999–2026, CATIA V5, NX, Creo, Inventor,
JT, Rhino, Parasolid, STEP, glTF, 3MF — and imports arrive with an **empty
feature list** ("parametric history wouldn't make sense transplanted").
Fusion imports similarly via cloud translation. Nobody translates feature
history; even $50k SDKs deliver dumb solids. STEP AP242 is genuinely the
best neutral target (B-rep, assemblies, semantic PMI, persistent IDs) — but
many exporters drop semantic PMI to graphics, and no STEP flavor carries
sketches, constraints, or features.

Sources: [HOOPS pricing](https://platform.softwareone.com/product/hoops-exchange/PCP-5549-7832) ·
[CAD Exchanger](https://cadexchanger.com/products/sdk/) ·
[ODA pricing](https://www.opendesign.com/pricing) ·
[APS Model Derivative pricing](https://aps.autodesk.com/blog/forge-pricing-explained-3-what-does-each-forge-api-cost) ·
[OCCT 7.8 formats](https://github.com/Open-Cascade-SAS/OCCT/discussions/1210) ·
[Parasolid](https://en.wikipedia.org/wiki/Parasolid) ·
[JT Open Toolkit](https://plm.sw.siemens.com/en-US/plm-components/jt/jt-open-toolkit/) ·
[Onshape supported formats](https://cad.onshape.com/help/Content/translation.htm) ·
[Onshape on lost history](https://forum.onshape.com/discussion/17328/when-importing-parts-from-solidworks-is-it-still-impossible-to-import-their-sketches-features) ·
[Fusion formats](https://help.autodesk.com/view/fusion360/ENU/?guid=TPD-SUPPORTED-FILE-FORMATS) ·
[AP242 PMI interop](https://www.ap242.org/geometry-assembly-pmi-interoperability.html).

## Marketplaces & community platforms (grounds PRD-031, informs PRD-007/011/029)

**Mechanics 2025–26:** Bambu **MakerWorld** is the growth engine — points
redeemable for gift cards/cash (Exclusive Program gated to 2,000+ prints;
fairness complaints), rampant points-farming forced a June 2025 rewards
overhaul; its **Parametric Model Maker** (server-executed OpenSCAD pinned to
the 2021 release, auto-built slider customizers) proves code-as-model at
consumer scale. **Printables**: non-cash Prusameters + contests + heavy
curation; AI-generated entries banned. Paid takes: Cults3D 20% (creator
80%), MyMiniFactory ~30% base, TurboSquid 40→80% with exclusivity, CGTrader
60–85%. **GrabCAD Library**: 7M+ engineers, 4M+ files — exposure-only, no
validation, static dumb files. **Thingiverse** decayed through serial
acquisitions and was sold to MyMiniFactory (Feb 2026); **Shapeways**' 2024
Chapter 7 lost user files. Lesson: contribution persists where rewards are
real and curation is funded; exposure-only communities decay.

**The engineering gap:** McMaster-Carr (700k parts, in-house-modeled),
TraceParts (100M+ models from parametric masters), CADENAS — all closed,
vendor-fed. **Nobody offers community-contributed, kernel-validated
parametric code components with STEP/drawing outputs and standards
metadata.** That lane is open.

**Code-as-model safety precedents:** VS Code Marketplace signs at publish,
scans continuously — and still removed 110 malicious extensions in a year
(name-reuse hijacks included): scanning alone is porous. npm's
**Shai-Hulud** worm (Sept 2025) self-propagated through 500+ packages via
publish tokens and install scripts (CISA alert; GitHub moved to trusted
publishing). Hugging Face pairs scanning with a structural fix —
**safetensors, a data-only format**. The equivalent structural move for CAD
code: marketplace models execute only in the server-side sandboxed kernel;
consumers get parameters and generated artifacts, never local execution.

Sources: [MakerWorld points overhaul](https://www.fabbaloo.com/news/bambu-lab-overhauls-makerworld-points-system-to-curb-abuse-and-reward-quality) ·
[Exclusive Model Program](https://blog.bambulab.com/exclusive-model-program-cash-rewards-and-copyright-support/) ·
[PMM/OpenSCAD reference](https://mindflakes.com/posts/2026/05/04/makerworld-pmm-openscad-reference/) ·
[Prusa rewards](https://www.prusa3d.com/p/prusa-reward-program/) ·
[MMF acquires Thingiverse](https://www.tomshardware.com/3d-printing/myminifactory-acquires-thingiverse-to-save-3d-printing-file-sharing-from-ai-thingverse-has-eight-million-users-and-2-5-million-things) ·
[Shapeways bankruptcy](https://www.cgchannel.com/2024/07/3d-printing-firm-shapeways-files-for-bankruptcy/) ·
[Cults upload terms](https://cults3d.com/en/upload) ·
[GrabCAD](https://resources.grabcad.com/company/) · [TraceParts](https://www.traceparts.com/en) ·
[VS Marketplace security](https://developer.microsoft.com/blog/security-and-trust-in-visual-studio-marketplace/) ·
[Shai-Hulud (Wiz)](https://www.wiz.io/blog/shai-hulud-npm-supply-chain-attack) ·
[CISA alert](https://www.cisa.gov/news-events/alerts/2025/09/23/widespread-supply-chain-compromise-impacting-npm-ecosystem) ·
[Protect AI × Hugging Face](https://huggingface.co/blog/pai-6-month).

## Physics & motion (grounds PRD-030)

**Engines:** **MuJoCo** (DeepMind) — Apache-2.0, pip wheel, monthly
releases (v3.11.0 Jul 2026); hinge/slide/ball/free joints; **closed chains
via MJCF equality constraints** (loops URDF cannot express); tunable soft
convex contact; `mj_inverse` answers motor sizing directly; MJX/Warp for
GPU scale. **Newton** (NVIDIA+DeepMind+Disney, Linux Foundation): 1.0 at
GTC Mar 2026, robotics/RL-focused — watch, don't adopt. **PhysX 5**: BSD
with GPU kernels open (Mar 2025) but no standalone Python — poor fit.
**Bullet/PyBullet**: maintenance mode (3.2.7, Jan 2025) — fading.
**Rapier**: Rust/WASM, Python "under development", game-grade. **Project
Chrono**: true engineering multibody + FEA coupling, but conda-only SWIG
API, slow cadence. **Drake** (TRI): pip, monthly, **hydroelastic contact**
(most credible continuous contact forces short of FEA), IK/trajectory
optimization — the engineering-grade second tier.

**Incumbents:** SolidWorks Motion = embedded **ADAMS**: motors, springs, 3D
contact, torque/power-vs-time, joint reactions exported as FEM loads — the
canonical workflow. Fusion motion is kinematic-only (Event Simulation is
extension-gated explicit dynamics). Onshape ships no dynamics; instead PTC
connected Onshape to **NVIDIA Isaac Sim** (GTC Mar 2026), carrying mates
into Isaac joints — validating the CAD-mates→sim bridge our URDF export
anticipates. What engineers actually run: range-of-motion/clearance, cam/
linkage force transmission, motor sizing over a duty cycle, contact events,
peak loads → static FEM, vibration via modal.

**Recommendation adopted (PRD-030):** MuJoCo first (poses + dynamics +
inverse dynamics in one Apache-2.0 dependency that fits the sandboxed
worker); convex-decomposed collision proxies (CoACD-class), never raw
tessellation; worst-case-frame extraction into static FEM with inertia
relief; Drake as the contact-fidelity tier later; do not build flexible-
body/transient FEA/fatigue (burst or defer).

Sources: [MuJoCo releases](https://github.com/google-deepmind/mujoco/releases) ·
[MuJoCo Warp](https://github.com/google-deepmind/mujoco_warp) ·
[Newton 1.0](https://vnrobo.com/en/blog/nvidia-newton-physics-engine) ·
[PhysX open source](https://www.phoronix.com/news/NVIDIA-OSS-PhysX-Flow-GPU) ·
[bullet3](https://github.com/bulletphysics/bullet3) ·
[Project Chrono](https://projectchrono.org/) ·
[Drake releases](https://drake.mit.edu/release_notes/release_notes.html) ·
[Drake hydroelastic](https://github.com/RobotLocomotion/drake/blob/master/tutorials/hydroelastic_contact_basics.ipynb) ·
[SW Motion motor torque](https://www.goengineer.com/blog/solidworks-motion-study-analysis-motor-torque-and-power) ·
[Onshape↔Isaac Sim](https://www.ptc.com/en/news/2026/ptc-announces-onshape-nvidia-isaac-sim-workflow).

---

*Full per-cluster source lists live with each section above. The forward
feature plan built on this research is [roadmap.md](roadmap.md); the
detailed requirements live in the [PRDs](prd/).*
