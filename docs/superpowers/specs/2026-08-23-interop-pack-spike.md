# PRD-017 de-risking spike — OCCT interop capability report

Run 2026-08-23 against the repo venv (`/Users/nfedorov/dev/personal/cad_claude/.venv`).
Scripts and outputs live in
`/private/tmp/claude-501/-Users-nfedorov-dev-personal-cad-claude/e34dd7f8-e3b8-48ac-a1c3-0ae0b11663a8/scratchpad/spike/`.
Nothing in the repo was modified; nothing was installed.

| Area | Verdict |
|---|---|
| A. Versions | — (OCCT 7.9.3, build123d 0.11.1) |
| B. AP242 PMI export + self round-trip | **works-with-caveats** (6 hard traps, 3 of them silent-corruption or segfault) |
| C. Structured STEP import | **works** |
| D. 3MF metadata / colors | **works** — better than the PRD assumed; no OPC step needed for the core |
| E. glTF (`RWGltf_CafWriter`) | exists, GLB is byte-deterministic |
| F. usd-core | not installed; installable on this platform, **no linux-aarch64 wheel** |

---

## A. Versions

```
python           : 3.12.4 arm64 macOS-26.6.1-arm64-arm-64bit
build123d        : 0.11.1
cadquery-ocp-novtk: 7.9.3.1.1      <- OCCT 7.9.3
cadquery-ocp-proxy: 7.9.3.1.1
ocpsvg           : 0.6.0
lib3mf           : 2.5.0           <- already in the venv (build123d Mesher backend)
usd-core         : ABSENT
Lib3MF module    : present
pxr (USD)        : ABSENT -> ModuleNotFoundError
```

OCCT confirms itself in the glTF asset header it writes:
`'generator': 'Open CASCADE Technology 7.9 [dev.opencascade.org]'`.

Note `OCP.OCC_VERSION` does **not** exist in this binding and
`OCP.Standard.Standard_Version` is not bound — read the version from the
distribution metadata (`cadquery-ocp-novtk`), not from OCP.

---

## B. STEP AP242 PMI export via XCAF — **WORKS, with six traps**

### B.0 Bottom line

Every one of the **15** `XCAFDimTolObjects_GeomToleranceType` values and **all
but three** dimension types survive a full write→read round trip *with datum
references, diameter/MMC modifiers and plus-minus tolerances* — but only once
six non-obvious preconditions are met. Miss any one and PMI is dropped
silently, scaled by 1000, or the process segfaults.

`AP214IS` carries **zero** PMI (verified: `dims=0 datums=0 geomtols=0` on
read-back) — AP242 is mandatory, not a nicety.

### B.1 The canonical recipe (verified end to end)

Full script: `z_recipe.py`. Actual output:

```
write: IFSelect_ReturnStatus.IFSelect_RetDone
FILE_SCHEMA: FILE_SCHEMA((
'AP242_MANAGED_MODEL_BASED_3D_ENGINEERING_MIM_LF {1 0 10303 442 1 1 4 }'

ROUND TRIP: dims 1/1  datums 3/2  FCFs 3/3
  DIM   value=20.0000 +0.2000/-0.0500
  FCF   flatness         value=0.0500 tov=None      matreq=None datums=[]
  FCF   position         value=0.2000 tov=Diameter  matreq=M    datums=['A', 'B']
  FCF   perpendicularity value=0.1000 tov=None      matreq=None datums=['A']
```

STEP entities emitted:

```
   2 DATUM_FEATURE                              1 FLATNESS_TOLERANCE
   3 DATUM_REFERENCE_COMPARTMENT                2 GEOMETRIC_TOLERANCE_WITH_DATUM_REFERENCE
   2 DATUM_SYSTEM                               1 GEOMETRIC_TOLERANCE_WITH_MODIFIERS
   2 DATUM(                                     1 PERPENDICULARITY_TOLERANCE
   1 DIMENSIONAL_CHARACTERISTIC_REPRESENTATION  1 PLUS_MINUS_TOLERANCE
   1 DIMENSIONAL_SIZE                           1 POSITION_TOLERANCE
   1 TOLERANCE_VALUE                            2 TOLERANCE_ZONE
```

Minimal working code:

```python
from OCP.TDocStd import TDocStd_Document
from OCP.XCAFApp import XCAFApp_Application
from OCP.XCAFDoc import (XCAFDoc_DocumentTool, XCAFDoc_DimTolTool,
                         XCAFDoc_Dimension, XCAFDoc_Datum, XCAFDoc_GeomTolerance)
from OCP.STEPCAFControl import STEPCAFControl_Writer, STEPCAFControl_Reader
from OCP.STEPControl import STEPControl_StepModelType
from OCP.Interface import Interface_Static
from OCP.TopLoc import TopLoc_Location

app = XCAFApp_Application.GetApplication_s()
doc = TDocStd_Document(TCollection_ExtendedString("XmlXCAF")); app.InitDocument(doc)
st  = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
dt  = XCAFDoc_DocumentTool.DimTolTool_s(doc.Main())

shape = solid.wrapped.Located(TopLoc_Location())      # TRAP 1
part  = st.AddShape(shape, False)
faces = [st.AddSubShape(part, f) for f in explore_faces(shape)]

dim = dt.AddDimension()                               # TRAP 4 (need >=1 dim)
o = XCAFDimTolObjects_DimensionObject()
o.SetType(XCAFDimTolObjects_DimensionType_Size_Thickness)
o.SetValue(20.0); o.SetUpperTolValue(0.20); o.SetLowerTolValue(0.05)   # TRAP 5
XCAFDoc_Dimension.Set_s(dim).SetObject(o)
dt.SetDimension(faces[4], dim)

lab = dt.AddDatum(HStr("A"), HStr("A"), HStr("A"))
d = XCAFDimTolObjects_DatumObject(); d.SetName(HStr("A")); d.SetPosition(1)  # TRAP 3
XCAFDoc_Datum.Set_s(lab).SetObject(d)
seq = TDF_LabelSequence(); seq.Append(faces[4]); dt.SetDatum(seq, lab)

g = dt.AddGeomTolerance()
go = XCAFDimTolObjects_GeomToleranceObject()
go.SetType(XCAFDimTolObjects_GeomToleranceType_Position); go.SetValue(0.2)
go.SetTypeOfValue(XCAFDimTolObjects_GeomToleranceTypeValue_Diameter)
go.SetMaterialRequirementModifier(XCAFDimTolObjects_GeomToleranceMatReqModif_M)
XCAFDoc_GeomTolerance.Set_s(g).SetObject(go)
dt.SetGeomTolerance(faces[1], g)
dt.SetDatumToGeomTol(lab, g)

w = STEPCAFControl_Writer()                                    # TRAP 2: writer FIRST
assert Interface_Static.SetCVal_s("write.step.schema", "AP242DIS")
w.SetDimTolMode(True); w.SetColorMode(True); w.SetNameMode(True); w.SetLayerMode(True)
w.Transfer(doc, STEPControl_StepModelType.STEPControl_AsIs)
w.Write(path)

r = STEPCAFControl_Reader()
r.SetGDTMode(True); r.SetColorMode(True); r.SetNameMode(True)   # SetGDTMode, NOT SetDimTolMode
r.ReadFile(path); r.Transfer(doc2)
```

### B.2 The six traps

**TRAP 1 — a located shape produces a *reference* label and null sub-shapes.**
build123d primitives carry a non-identity `TopLoc_Location`. `AddShape()` on
one creates a reference label, and `AddSubShape()` then returns a **null label
for every face**, so `SetDatum` dies with `Standard_Failure: A null Label has
no attribute.` Evidence:

```
loc identity? False
IsReference True  IsSimple False   -> AddSubShape null: True
# after .Located(TopLoc_Location()):
IsReference False IsSimple True    -> AddSubShape null: False
```
Fix: `shape.wrapped.Located(TopLoc_Location())` before `AddShape`.

**TRAP 2 — the STEP statics do not exist until a STEP writer is constructed.**

```
Interface_Static write.step.schema before any writer: '' (ival 0)
SetCVal_s("write.step.schema","AP242DIS") -> returns False, value stays ''
after STEPCAFControl_Writer():             'AP214IS' (ival 4)
set again after ctor:                      'AP242DIS' (ival 5)
```
Setting the schema before constructing the writer is a **silent no-op**, and
you get an AP214 file whose PMI is entirely absent:
`FILE_SCHEMA(('AUTOMOTIVE_DESIGN { 1 0 10303 214 1 1 1 1 }'))` →
round trip `dims=0 datums=0 geomtols=0`.
Construct the writer, *then* set `write.step.schema`, and assert the return
value. Correct header:
`'AP242_MANAGED_MODEL_BASED_3D_ENGINEERING_MIM_LF {1 0 10303 442 1 1 4 }'`.

**TRAP 3 — `DatumObject.SetPosition(1..3)` is load-bearing; without it every
datum-referencing FCF is silently dropped.** This is the single biggest
finding. Same document, same code, only `SetPosition` differing
(`b2_datumpos.py`):

```
### SetPosition = False  -> SURVIVED 7/15
  STEP entities: {CYLINDRICITY, DATUM:3, DATUM_FEATURE:3, FLATNESS,
                  LINE_PROFILE, POSITION, ROUNDNESS, STRAIGHTNESS, SURFACE_PROFILE}
  round-trip datum labels: 0
  Angularity/CircularRunout/Coaxiality/Concentricity/Parallelism/
  Perpendicularity/Symmetry/TotalRunout ..... no
  Position ... survives but datum refs = []      (want ['A'])

### SetPosition = True   -> SURVIVED 15/15
  STEP entities: ... DATUM_REFERENCE_COMPARTMENT:9, DATUM_SYSTEM:9,
                 GEOMETRIC_TOLERANCE_WITH_DATUM_REFERENCE:9, + every *_TOLERANCE
  round-trip datum labels: 9
  every one of the 15 types survives, with its datum refs intact
```
With `SetPosition` set, **all 15 FCF types round-trip**: Angularity,
CircularRunout, CircularityOrRoundness, Coaxiality, Concentricity,
Cylindricity, Flatness, Parallelism, Perpendicularity, Position, ProfileOfLine,
ProfileOfSurface, Straightness, Symmetry, TotalRunout. Without it, no
`GEOMETRIC_TOLERANCE_WITH_DATUM_REFERENCE` is ever written, the `DATUM`
entities become unreferenced orphans, and the eight orientation/location/runout
types are dropped **with no error and no diagnostic**.

**TRAP 4 — a document with no dimension writes tolerance values in METRES.**
Silent ×1000 corruption. Matrix (`b4_unitmatrix.py`), flatness 0.05 mm:

```
 dim?  datum? |    written        unit |  read-back mm
False   False |     5.E-02       METRE |          50.0
False    True |     5.E-02       METRE |          50.0
 True   False |     5.E-02  MILLIMETRE |          0.05
 True    True |     5.E-02  MILLIMETRE |          0.05
```
With no `DIMENSIONAL_SIZE`/`DIMENSIONAL_LOCATION` in the document, OCCT fails
to resolve the model's representation context and mints a fresh
`( LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT($,.METRE.) )` for every geometric
tolerance measure, instead of reusing the model's
`SI_UNIT(.MILLI.,.METRE.)`. Setting `write.step.unit = "MM"` does **not** help
(verified in `b3_units.py`: all four variants still emitted METRE).
Implementer options: (a) refuse to emit FCF-only PMI, (b) always emit at least
one dimension, (c) verify by re-reading. A round-trip assertion in the test
suite catches it; a schema check does not.

**TRAP 5 — `SetLowerTolValue` wants a positive magnitude; the writer negates
it.** Raw STEP `TOLERANCE_VALUE(lower, upper)` for the same measurement
(`b6_signs.py`):

```
XCAF SetLowerTolValue(-0.05)  ->  STEP lower = 5.E-02   (WRONG sign for AP242)
XCAF SetLowerTolValue(+0.05)  ->  STEP lower = -5.E-02  (correct)
both read back as                 -tol = 0.0500
```
So a signed value from our PMI model produces a standards-**incorrect** file
that our own round-trip test would still pass (the reader returns the
magnitude either way). Pass magnitudes. Verified good cases: bilateral
asymmetric, bilateral symmetric, unilateral-plus, unilateral-minus all
round-trip their magnitudes exactly.

**TRAP 6 — three dimension types SEGFAULT `STEPCAFControl_Writer::Transfer`
(exit 139, no Python exception).**

```
Location_WithPath   1 target  exit=139
Location_WithPath   2 targets exit=139
Size_WithPath       1 target  exit=139
Size_WithPath       2 targets exit=139
Location_Oriented   2 targets exit=139
Location_Oriented   1 target  OK
```
These must be blocklisted at the mapping layer (an `fidelity.pmi_skipped`
reason), not discovered in production — the kernel worker would die mid-export.

### B.3 Dimension-type coverage (all 30 types exercised, `b5b_one.py`)

* **Survive with 1 target label:** every `Size_*` type — Thickness,
  Diameter, Radius, SphericalDiameter, SphericalRadius, CurveLength, Angular,
  and all 8 `Size_Toroidal*`. Values and ±tolerances exact.
* **Need TWO target labels:** every `Location_*` type. Written with one target
  they produce a valid `DIMENSIONAL_LOCATION` in the file but **read back as
  nothing** (`NONE`) — a silent loss. With two labels all 11 non-crashing
  `Location_*` variants round-trip exactly.
* **Crash:** `Location_WithPath`, `Size_WithPath`, `Location_Oriented`(2).
* **Angular is in RADIANS on the XCAF side.** `Size_Angular` value 90.0 comes
  back as **5156.62** (= 90 rad in degrees); value π/2 comes back as **90.0**.
  The reader converts rad→deg; the writer does not convert. The *tolerances*
  on an angular dimension are **not** converted (0.5 → 0.5), so the value and
  its tolerance use different units on the way back. Avoid angular PMI in v1
  or convert explicitly and assert.

### B.4 What else survives (`b7_detail.py`)

| Attribute | Survives? |
|---|---|
| dimension value, upper/lower tolerance magnitude | **yes** |
| `SetLowerBound`/`SetUpperBound` (range of sizes) | **yes** (19.9, 20.1) |
| dimension modifiers (`AddModifier`) | **yes** (2 in, 2 out) |
| ISO fit class `SetClassOfTolerance(H7)` | writes `LIMITS_AND_FITS`; getter is out-param, not verified round-trip |
| `SetQualifier(Max)` | **no** — writes `TYPE_QUALIFIER`, reads back `Qualifier_None` |
| geometric tolerance value, `TypeOfValue` (Diameter), material requirement (M) | **yes** |
| geometric tolerance modifiers | partial — 2 in, **1 out** |
| `SetZoneModifier(Projected)` + `SetValueOfZoneModifier(15.0)` | **no** — reads back `None` / 0.0 |
| `SetMaxValueModifier(0.05)` | **no** — `GEOMETRIC_TOLERANCE_WITH_MAXIMUM_TOLERANCE` is written but reads back 0.0 |
| datum name, `Position`, datum modifiers | **yes** |
| **PMI entry identity** (`TDataStd_Name` on the label, `SetSemanticName`) | **NO** |

That last row matters for FR3. Names are **overwritten** by the STEP type
keyword: a dimension labelled `"BORE_H7"` reads back as `'diameter'`; one
labelled `"MODS"` reads back as `'thickness'`; the geometric-tolerance label
reads back as `''` and its `GetSemanticName()` is `None`; a datum label reads
back as `'DGT:Datum'`. Our round-trip test therefore **cannot** re-associate
PMI entries by name — it must match on (type, value, tolerance, target
sub-shape). And `set_part_pmi` ids do not survive to a supplier.

Minor: a two-datum FCF yields **3** datum labels on read-back for 2 written
(one label per datum-system compartment), so assert on datum *names*, not on
label counts.

---

## C. Structured STEP import — **WORKS**

`c_assembly.py` authors 3 unique products + 1 nested sub-assembly (7 NAUOs,
5 top-level occurrences, 4 pin placements), writes AP242, re-reads and walks
the tree. Written file:

```
PRODUCT names: [('TopAssembly',..), ('Bracket',..), ('PinPair',..), ('Pin',..), ('Ball',..)]
NEXT_ASSEMBLY_USAGE_OCCURRENCE count: 7
NAUO ids: [('1','bracket_1'),('4','pinpair_1'),('2','pin_1'),('3','pin_2'),
           ('5','pinpair_2'),('6','ball_1'),('7','ball_2')]
STYLED_ITEM count: 5
length unit: ['.MILLI.,.METRE.']
```

Read back (`[A]`=assembly `[C]`=component `[R]`=reference `[S]`=simple shape,
positions are **composed** transforms):

```
free shapes: 1
0:1:1:1    [A   ] name='TopAssembly'  ref=None      pos=(   0.00,   0.00,   0.00) color=-
  0:1:1:1:1  [CRS ] name='bracket_1'  ref='Bracket' pos=(   0.00,   0.00,   0.00) color=Surf@referred (0.69,0.03,0.02)
  0:1:1:1:2  [ACR ] name='pinpair_1'  ref='PinPair' pos=(   0.00,   0.00,  10.00) color=-
    0:1:1:3:1  [CRS ] name='pin_1'    ref='Pin'     pos=(   0.00,   0.00,  10.00) color=Surf@referred (0.02,0.17,0.79)
    0:1:1:3:2  [CRS ] name='pin_2'    ref='Pin'     pos=(  30.00,   0.00,  10.00) color=Surf@referred (0.02,0.17,0.79)
  0:1:1:1:3  [ACR ] name='pinpair_2'  ref='PinPair' pos=(   0.00,  50.00,  10.00) color=-
    0:1:1:3:1  [CRS ] name='pin_1'    ref='Pin'     pos=(   0.00,  50.00,  10.00) color=Surf@referred (0.02,0.17,0.79)
    0:1:1:3:2  [CRS ] name='pin_2'    ref='Pin'     pos=(   0.00,  80.00,  10.00) color=Surf@referred (0.02,0.17,0.79)
  0:1:1:1:4  [CRS ] name='ball_1'     ref='Ball'    pos=(   5.00,   5.00,  40.00) color=Surf@referred (0.01,0.45,0.07)
  0:1:1:1:5  [CRS ] name='ball_2'     ref='Ball'    pos=(  -5.00,  -5.00,  40.00) color=Surf@self (1.00,0.69,0.00)

--- unique products ---
   0:1:1:2    name='Bracket'  color=Surf@self (0.69,0.03,0.02)
   0:1:1:4    name='Pin'      color=Surf@self (0.02,0.17,0.79)
   0:1:1:5    name='Ball'     color=Surf@self (0.01,0.45,0.07)
```

Composed transform spot-check: `pin_2` has local (30,0,0); `pinpair_2` is a
90° Z-rotation then translate (0,50,10); result **(0, 80, 10)** — correct.
Occurrence names, product names, per-product colors and the **per-occurrence
color override** on `ball_2` all survive. Deduplication is free: 3 unique
products for 7 occurrences.

**The walk that works:**

```python
r = STEPCAFControl_Reader()
r.SetColorMode(True); r.SetNameMode(True); r.SetLayerMode(True)
r.SetGDTMode(True); r.SetMatMode(True); r.SetViewMode(True)
r.ReadFile(path); r.Transfer(doc)
st = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())

free = TDF_LabelSequence(); st.GetFreeShapes(free)

def walk(L, loc_chain):
    ref = TDF_Label()
    is_ref = XCAFDoc_ShapeTool.GetReferredShape_s(L, ref)
    target = ref if is_ref else L                 # <-- PITFALL 1
    if XCAFDoc_ShapeTool.IsAssembly_s(target):
        comps = TDF_LabelSequence()
        XCAFDoc_ShapeTool.GetComponents_s(target, comps)
        for i in range(1, comps.Length() + 1):
            c = comps.Value(i)
            walk(c, loc_chain * XCAFDoc_ShapeTool.GetLocation_s(c))
```

### Pitfalls

1. **Label vs referred label.** A component label carries the *instance* name
   and the *occurrence* color; the **referred** label carries the *product*
   name, geometry and the product color. `IsAssembly_s`/`IsSimpleShape_s` must
   be asked of the **referred** label — a component label answers `False` to
   both. Look up the color on the component label first (occurrence override),
   then fall back to the referred label; `ball_2` above proves the override
   path, every other row proves the fallback.
2. **`XCAFDoc_ColorTool.GetColor` is only bound for `TopoDS_Shape` in OCP.**
   The label overloads live on the **static** `XCAFDoc_ColorTool.GetColor_s(L,
   type, color)`. Calling the instance method with a `TDF_Label` raises
   `TypeError`.
3. **`ColorSurf` vs `ColorGen` precedence.** Query `ColorSurf`, then
   `ColorGen`, then `ColorCurv`, at each of (component label, referred label).
   OCCT writes surface colors as `ColorSurf`; a file authored elsewhere may
   only set `ColorGen`.
4. **`Quantity_Color.Red()/Green()/Blue()` return LINEAR values, not sRGB.**
   `Quantity_Color(0.85, 0.20, 0.15, Quantity_TOC_sRGB).Red()` is `0.6921`.
   Use `col.Values(Quantity_TOC_sRGB)` to get `(0.85, 0.2, 0.15)` back. Storing
   `.Red()` into the manifest would darken every imported color.
5. **Nested instance identity is the path, not the leaf label.** `pin_1`
   under both `pinpair_1` and `pinpair_2` is the *same* label `0:1:1:3:1`.
   Instance ids must be derived from the component-label chain, or two
   occurrences collapse into one (FR8/FR10).
6. **Units.** `xstep.cascade.unit = 'MM'` is the default and the reader honours
   it; the written file declares `SI_UNIT(.MILLI.,.METRE.)`. An inch-authored
   file is converted to mm on read by that static. Do not change it globally —
   it is process-wide, like every `Interface_Static`.
7. Reader flag is `SetGDTMode` (there is no `SetDimTolMode` on the reader; the
   *writer* has `SetDimTolMode`). `read.step.product.mode = 'ON'` and
   `read.step.assembly.level = 'all'` are the defaults you want.

---

## D. 3MF metadata / colors — **WORKS, and the PRD over-estimated the risk**

The repo writes 3MF at `agentcad/kernel/worker.py:476` via
`b3d.Mesher().add_shape(shape, linear_deflection=tolerance)`. `Mesher` is
lib3mf 2.5.0-backed and **already supports** everything FR4 asks for.
**OCP has no 3MF writer at all** (`RWMesh` / `RWGltf` only; no 3MF module in
`OCP.__path__`) — lib3mf via build123d is the only path, and it is the good one.

What `build123d.Mesher` writes today (`d_3mf.py`):

```xml
<model xmlns="…/core/2015/02" unit="millimeter" xml:lang="en-US" …>
  <metadata name="Title" preserve="1">Locator Bracket</metadata>
  <metadata name="Designer" preserve="1">AgentCAD</metadata>
  <metadata name="Description" preserve="1">spike output</metadata>
  <metadata name="CreationDate" preserve="1">2026-08-23</metadata>
  <metadata name="customXMLNS0:PartNumber" preserve="1">AC-0001</metadata>
  <resources>
    <basematerials id="2">
      <base name="Color: (0.85, 0.2, 0.15, 1.0) near 'BROWN3'" displaycolor="#D93326FF"/>
    </basematerials>
    <object id="1" name="base_plate" partnumber="AC-0001" type="model"
            p:UUID="…" pid="2" pindex="0"> <mesh>…</mesh> </object>
    …
  </resources>
  <build><item objectid="1" p:UUID="…"/>…</build>
</model>
```

OPC package is conformant out of the box:
`['3D/3dmodel.model', '[Content_Types].xml', '_rels/.rels']`,
content type `application/vnd.ms-package.3dmanufacturing-3dmodel+xml`,
`.rels` start-part `http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel`
→ `/3D/3dmodel.model`.

So `Title`/`Designer`/`Description`/`CreationDate`, a custom-namespace
`PartNumber`, the `partnumber=` attribute, `unit="millimeter"` and per-object
colors are all available with **no OPC post-processing**.

### D.1 The one real trap: `add_shape(Part)` silently drops name and color

```
[add_shape(Part)]   object {'id':'1','partnumber':'AC-0001',…}      <- no name=, no pid
                    (no basematerials at all)
[add_shape(Solid)]  object {'id':'1','name':'base_plate','partnumber':'AC-0001',
                            'pid':'2','pindex':'0'}
                    basematerials id=2 [{'name':"Color: (0.85,0.2,0.15,1.0)…",
                                         'displaycolor':'#D93326FF'}]
```

`Mesher._add_color` reads `b3d_shape.color`, and `.label`/`.color` are only
picked up when the argument is a `Solid`. The repo passes whatever `build()`
returned — normally a `Part`/`Compound` — so today's 3MF has **no names and no
colors** even if the script set them. FR4 must decompose to solids
(`shape.solids()`) and set `.label`/`.color` per solid before `add_shape`.

### D.2 3MF is NOT byte-deterministic

```
zip sha equal: False
3dmodel.model sha equal: False
p:UUID attributes: 9 in A, 9 in B, shared=0
equal after stripping UUIDs: True
```
lib3mf mints a fresh `p:UUID` (production extension) for every object, every
component and every build item on each write. Nine of them in a two-solid
file. If 3MF is ever content-hashed (share links, CI), the writer needs either
deterministic UUIDs (lib3mf lets you set them) or a post-write normalisation
pass. Zip member timestamps are the second source of drift.

### D.3 OPC post-processing works, with one rule (`d3_opc.py`)

Bisected, lib3mf re-read as the acceptance check:

```
  original file                                OK
  rezipped, byte-identical XML                 OK
  text: extra <metadata> element               OK
  text: add name= to an object                 OK
  text: m:colorgroup + repointed pid/pindex    FAIL Lib3MFException 5
  text: colorgroup + requiredextensions='m'    FAIL Lib3MFException 5
  ElementTree re-serialise, no edits           OK
  ElementTree + register_namespace('p', prod)  OK
```

Narrowed further:

```
  colorgroup added, pid/pindex UNTOUCHED               OK
  pindex repointed only (no colorgroup)                FAIL "A Resource Index is invalid"
  merged into ONE basematerials, pid/pindex repointed  OK
```

So the failure was never the `m:colorgroup` element or the namespace prefix —
it was pointing `pindex="1"` at a `<basematerials>` group that contains a
single `<base>`. build123d emits **one group per shape**, each with exactly one
`<base>`. The working recipe for per-solid colors is: **merge the groups into
one `<basematerials>` with N `<base>` children, then repoint every object's
`pid` to that group and `pindex` to its own index.** That round-trips through
lib3mf with colors intact:
`[('base_plate', (0.851,0.2,0.149,1.0)), ('locator_pin', (0.149,0.451,0.902,1.0))]`.

Also safe: re-zipping, `ElementTree` re-serialisation (the production prefix
becomes `ns1`, which lib3mf accepts), adding `<metadata>`, adding `name=`.
`ET.register_namespace("p", "…/production/2015/06")` restores the `p:` prefix
if you want a cosmetically identical file.

Nothing here needed the OPC route for FR4's stated scope — reserve it for
per-solid color and for stamping metadata build123d does not expose.

---

## E. glTF — `RWGltf_CafWriter` exists

```
e.glb   write ok: True   size=4068  deterministic(2 writes)=True
e.gltf  write ok: True   size=3405  deterministic(2 writes)=False
gltf asset: {'generator': 'Open CASCADE Technology 7.9 [dev.opencascade.org]',
             'version': '2.0', 'extras': {'generator': 'AgentCAD-spike'}}
nodes: [('Asm', None, None), ('plate_1', None, 0), ('plate_2', [30.0,0,0], 1)]
materials: [{"name":"mat_0","pbrMetallicRoughness":
             {"baseColorFactor":[0.69207,0.03310,0.01960,1.0]},"doubleSided":true}]
meshes: [('Plate', 6), ('Plate', 6)]
```

Notes for the pure-Python writer the PRD plans:

* GLB **is** byte-deterministic across writes. The `.gltf` difference is only
  the `buffers[0].uri` (`a_e.bin` vs `b_e.bin`) — deterministic for a fixed
  output name. Nothing timestamped is embedded.
* `baseColorFactor` is **linear**, matching the glTF 2.0 spec — the
  server-side writer must convert our sRGB material colors to linear
  (the same `Quantity_Color` trap as area C, pitfall 4).
* OCCT applies **no** Z-up→Y-up conversion by default (translation `[30,0,0]`
  passes through unchanged); `RWMesh_CoordinateSystem_glTF` is the Y-up target
  if you use the OCCT writer. Our own writer owns this (FR6/AC3).
* It does **not** dedupe: two occurrences of one product produced two meshes
  both named `'Plate'`, 6 primitives each (one per face). A hand-written
  exporter keyed on the ACM cache will be smaller and faster.
* It requires a triangulated shape (`BRepMesh_IncrementalMesh` first) — i.e.
  it would have to run in the kernel process, which is exactly what the PRD
  wants to avoid. No reason to change the plan.

## F. usd-core

Not importable in the venv (`import pxr → ModuleNotFoundError`), as expected.
On PyPI the current release is **usd-core 26.8**, `requires_python
<3.15,>=3.9`, and it ships `usd_core-26.8-cp312-none-macosx_10_15_universal2.whl`
(38 MB) — so it installs cleanly on this machine's Python 3.12 / macOS arm64.
The wheel matrix is cp39–cp314 × {macosx universal2, manylinux_2_27_x86_64,
win_amd64}. **There is no linux-aarch64 wheel**, which matters more than the
size: the hosted Docker image and `make test-linux` both run on arm64 here, so
`agentcad[usd]` would fail to install in exactly the environment the deployment
docs describe unless the image is x86_64. That is an argument for the
extra-gating FR11 already specifies, and the FEM-style "register only when the
dependency imports" guard covers it — but the deployment docs should say
x86_64-only rather than let a user discover it at `pip install` time.

---

## What this means for FR1's scope

**Ship it, with a mapping layer that owns the six traps.** Coverage is far
better than the PRD's risk section assumed — all 15 FCF types and 27 of 30
dimension types survive a self round trip — but the failure modes are the bad
kind: silent drops (trap 3), a silent ×1000 (trap 4), a silent sign inversion
(trap 5) and a process kill (trap 6).

Concretely:

1. **`fidelity.pmi_skipped` has real work to do**, but not for FCF *types* —
   for the three crashing dimension types, for angular dimensions, and for the
   attributes in the B.4 table that do not survive (qualifiers, zone
   modifiers, max-value modifiers, the second geometric-tolerance modifier).
2. **The round-trip test must compare by (type, value, tolerance, target),
   never by name** — PMI entry identity is destroyed by the writer.
3. **Assert `Interface_Static.SetCVal_s(...)` returns True** and assert the
   `FILE_SCHEMA` line in the written file. An AP214 fallback is a total,
   silent PMI loss.
4. **A unit assertion belongs in the round-trip test**, not a schema check —
   the METRE file is perfectly valid AP242.
5. **Blocklist `Location_WithPath` / `Size_WithPath` / two-target
   `Location_Oriented` at the mapping layer.** A segfault in the kernel worker
   on export is a much worse outcome than a `pmi_skipped` row.
6. FreeCAD / commercial-viewer verification (the PRD's other de-risking ask)
   was **not** performed here — this spike is OCCT-vs-OCCT. Given how much of
   the semantic PMI is genuinely present in the file (`DATUM_SYSTEM`,
   `DATUM_REFERENCE_COMPARTMENT`, `GEOMETRIC_TOLERANCE_WITH_DATUM_REFERENCE`,
   `TOLERANCE_ZONE`), viewer readability is plausible but unproven; keep AC1's
   manual screenshot step.

For 3MF, FR4 is cheaper than planned (build123d already writes units,
metadata, part numbers, names and per-object colors) — the work is the
`Part`→`Solid` decomposition and, only for per-solid colors on a multi-solid
part, the merged-`basematerials` OPC pass. Add a determinism note: 3MF cannot
be byte-hashed without normalising the nine random `p:UUID`s.
