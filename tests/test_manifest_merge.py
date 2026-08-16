"""Structure-aware three-way merge of project.json (pure — no kernel, no git).

The truth table, the key granularity and the conflict payload shape are the
contract Slice 3's merge orchestrator consumes verbatim.
"""

import copy
import json

import pytest

from agentcad.core.manifest_merge import (
    CONFLICT_KEYS,
    apply_choices,
    merge_manifests,
)
from agentcad.core.model import ValidationError

# --------------------------------------------------------------- builders


def part(pid, **fields):
    entry = {"id": pid, "label": pid, "material": "stainless_316", "params": {}}
    entry.update(fields)
    return entry


def instance(iid, part_id, **fields):
    entry = {
        "id": iid,
        "part": part_id,
        "position": [0.0, 0.0, 0.0],
        "rotation_deg": [0.0, 0.0, 0.0],
    }
    entry.update(fields)
    return entry


def manifest(parts=(), instances=(), **extra):
    doc = {
        "schema_version": 1,
        "name": "proj",
        "units": "mm",
        "parts": [copy.deepcopy(p) for p in parts],
        "assembly": {"instances": [copy.deepcopy(i) for i in instances]},
    }
    doc.update(copy.deepcopy(extra))
    return doc


def triple(doc):
    """(base, ours, theirs) — three independent copies of one manifest."""
    return copy.deepcopy(doc), copy.deepcopy(doc), copy.deepcopy(doc)


def sample():
    return manifest(
        parts=[part("flange", params={"bolt_d": 6.0, "thick": 14.0})],
        instances=[instance("flange_1", "flange")],
        materials={"custom_al": {"density_g_cm3": 2.70, "yield_mpa": 276.0}},
    )


def entry_of(seq, eid):
    return next(e for e in seq if e["id"] == eid)


def ids(seq):
    return [e["id"] for e in seq]


def keys_of(conflicts):
    return [c["key"] for c in conflicts]


# ------------------------------------------------------- the truth table

def _w_units(doc, v):
    doc["units"] = v


def _r_units(doc):
    return doc["units"]


def _w_label(doc, v):
    entry_of(doc["parts"], "flange")["label"] = v


def _r_label(doc):
    return entry_of(doc["parts"], "flange")["label"]


def _w_param(doc, v):
    entry_of(doc["parts"], "flange")["params"]["bolt_d"] = v


def _r_param(doc):
    return entry_of(doc["parts"], "flange")["params"]["bolt_d"]


def _w_position(doc, v):
    entry_of(doc["assembly"]["instances"], "flange_1")["position"] = v


def _r_position(doc):
    return entry_of(doc["assembly"]["instances"], "flange_1")["position"]


def _w_material(doc, v):
    doc["materials"]["custom_al"] = v


def _r_material(doc):
    return doc["materials"]["custom_al"]


KEY_CLASSES = [
    ("units", _w_units, _r_units, ("mm", "cm", "in")),
    ("parts.flange.label", _w_label, _r_label, ("base", "ours", "theirs")),
    ("parts.flange.params.bolt_d", _w_param, _r_param, (6.0, 8.0, 5.0)),
    (
        "assembly.instances.flange_1.position",
        _w_position,
        _r_position,
        ([0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [2.0, 0.0, 0.0]),
    ),
    (
        "materials.custom_al",
        _w_material,
        _r_material,
        (
            {"density_g_cm3": 2.70},
            {"density_g_cm3": 2.80},
            {"density_g_cm3": 2.90},
        ),
    ),
]

KEY_IDS = [case[0] for case in KEY_CLASSES]


def prepare(write, values):
    base, ours, theirs = triple(sample())
    write(base, copy.deepcopy(values[0]))
    write(ours, copy.deepcopy(values[0]))
    write(theirs, copy.deepcopy(values[0]))
    return base, ours, theirs


@pytest.mark.parametrize("key,write,read,values", KEY_CLASSES, ids=KEY_IDS)
def test_both_sides_make_the_same_edit_is_clean(key, write, read, values):
    base, ours, theirs = prepare(write, values)
    write(ours, copy.deepcopy(values[1]))
    write(theirs, copy.deepcopy(values[1]))

    merged, conflicts = merge_manifests(base, ours, theirs)

    assert conflicts == []
    assert read(merged) == values[1]


@pytest.mark.parametrize("key,write,read,values", KEY_CLASSES, ids=KEY_IDS)
def test_ours_only_edit_wins(key, write, read, values):
    base, ours, theirs = prepare(write, values)
    write(ours, copy.deepcopy(values[1]))

    merged, conflicts = merge_manifests(base, ours, theirs)

    assert conflicts == []
    assert read(merged) == values[1]


@pytest.mark.parametrize("key,write,read,values", KEY_CLASSES, ids=KEY_IDS)
def test_theirs_only_edit_wins(key, write, read, values):
    base, ours, theirs = prepare(write, values)
    write(theirs, copy.deepcopy(values[2]))

    merged, conflicts = merge_manifests(base, ours, theirs)

    assert conflicts == []
    assert read(merged) == values[2]


@pytest.mark.parametrize("key,write,read,values", KEY_CLASSES, ids=KEY_IDS)
def test_both_sides_differ_conflicts_at_that_key(key, write, read, values):
    base, ours, theirs = prepare(write, values)
    write(ours, copy.deepcopy(values[1]))
    write(theirs, copy.deepcopy(values[2]))

    merged, conflicts = merge_manifests(base, ours, theirs)

    assert len(conflicts) == 1
    assert conflicts[0] == {
        "kind": "manifest",
        "key": key,
        "path": key.split("."),
        "base": values[0],
        "ours": values[1],
        "theirs": values[2],
    }
    assert list(conflicts[0]) == [k for k in CONFLICT_KEYS if k in conflicts[0]]
    # the merged document always carries ours' value, so it stays loadable
    assert read(merged) == values[1]


# ------------------------------------------------------------- FR8 cases

def test_fr8_disjoint_parts_both_land():
    base, ours, theirs = triple(
        manifest(parts=[part("a", params={"x": 1.0}), part("b", params={"y": 2.0})])
    )
    entry_of(ours["parts"], "a")["params"]["x"] = 9.0
    entry_of(ours["parts"], "a")["label"] = "A"
    entry_of(theirs["parts"], "b")["params"]["y"] = 7.0
    entry_of(theirs["parts"], "b")["material"] = "inconel718"

    merged, conflicts = merge_manifests(base, ours, theirs)

    assert conflicts == []
    assert entry_of(merged["parts"], "a")["params"]["x"] == 9.0
    assert entry_of(merged["parts"], "a")["label"] == "A"
    assert entry_of(merged["parts"], "b")["params"]["y"] == 7.0
    assert entry_of(merged["parts"], "b")["material"] == "inconel718"


def test_fr8_script_edit_leaves_manifest_untouched_no_invented_conflict():
    # A rewrote parts/a.py (not the manifest at all); B changed that part's size.
    base, ours, theirs = triple(manifest(parts=[part("a", params={"size": 10.0})]))
    entry_of(theirs["parts"], "a")["params"]["size"] = 12.0

    merged, conflicts = merge_manifests(base, ours, theirs)

    assert conflicts == []
    assert entry_of(merged["parts"], "a")["params"]["size"] == 12.0


# ---------------------------------------------- add / delete across sections

def _parts_add(doc, eid, marker):
    doc["parts"].append(part(eid, params={"x": marker}))


def _parts_del(doc, eid):
    doc["parts"] = [e for e in doc["parts"] if e["id"] != eid]


def _parts_edit(doc, eid, marker):
    entry_of(doc["parts"], eid)["params"]["x"] = marker


def _parts_get(doc, eid):
    return next((e for e in doc["parts"] if e["id"] == eid), None)


def _inst_add(doc, eid, marker):
    doc["assembly"]["instances"].append(
        instance(eid, "flange", position=[marker, 0.0, 0.0])
    )


def _inst_del(doc, eid):
    doc["assembly"]["instances"] = [
        e for e in doc["assembly"]["instances"] if e["id"] != eid
    ]


def _inst_edit(doc, eid, marker):
    entry_of(doc["assembly"]["instances"], eid)["position"] = [marker, 0.0, 0.0]


def _inst_get(doc, eid):
    return next((e for e in doc["assembly"]["instances"] if e["id"] == eid), None)


def _mat_add(doc, eid, marker):
    doc.setdefault("materials", {})[eid] = {"density_g_cm3": marker}


def _mat_del(doc, eid):
    doc.get("materials", {}).pop(eid, None)


def _mat_edit(doc, eid, marker):
    doc["materials"][eid]["density_g_cm3"] = marker


def _mat_get(doc, eid):
    return (doc.get("materials") or {}).get(eid)


SECTIONS = [
    ("parts", "widget", _parts_add, _parts_del, _parts_edit, _parts_get),
    ("assembly.instances", "widget_1", _inst_add, _inst_del, _inst_edit, _inst_get),
    ("materials", "custom_ti", _mat_add, _mat_del, _mat_edit, _mat_get),
]

SECTION_IDS = [case[0] for case in SECTIONS]


@pytest.mark.parametrize("prefix,eid,add,delete,edit,get", SECTIONS, ids=SECTION_IDS)
def test_add_add_identical_is_clean(prefix, eid, add, delete, edit, get):
    base, ours, theirs = triple(sample())
    add(ours, eid, 1.0)
    add(theirs, eid, 1.0)

    merged, conflicts = merge_manifests(base, ours, theirs)

    assert conflicts == []
    assert get(merged, eid) is not None


@pytest.mark.parametrize("prefix,eid,add,delete,edit,get", SECTIONS, ids=SECTION_IDS)
def test_add_add_divergent_conflicts_on_the_whole_entry(
    prefix, eid, add, delete, edit, get
):
    base, ours, theirs = triple(sample())
    add(ours, eid, 1.0)
    add(theirs, eid, 2.0)

    merged, conflicts = merge_manifests(base, ours, theirs)

    assert len(conflicts) == 1
    conflict = conflicts[0]
    assert conflict["key"] == f"{prefix}.{eid}"
    assert "base" not in conflict  # add/add has no base
    assert conflict["ours"] == get(ours, eid)
    assert conflict["theirs"] == get(theirs, eid)
    assert get(merged, eid) == get(ours, eid)


@pytest.mark.parametrize("prefix,eid,add,delete,edit,get", SECTIONS, ids=SECTION_IDS)
def test_add_on_one_side_only_lands(prefix, eid, add, delete, edit, get):
    base, ours, theirs = triple(sample())
    add(theirs, eid, 3.0)

    merged, conflicts = merge_manifests(base, ours, theirs)

    assert conflicts == []
    assert get(merged, eid) == get(theirs, eid)


@pytest.mark.parametrize("prefix,eid,add,delete,edit,get", SECTIONS, ids=SECTION_IDS)
def test_delete_delete_and_delete_unchanged(prefix, eid, add, delete, edit, get):
    base, ours, theirs = triple(sample())
    add(base, eid, 1.0)
    add(ours, eid, 1.0)
    add(theirs, eid, 1.0)

    both = merge_manifests(
        base, _without(ours, delete, eid), _without(theirs, delete, eid)
    )
    assert both[1] == []
    assert get(both[0], eid) is None

    one = merge_manifests(base, _without(ours, delete, eid), theirs)
    assert one[1] == []
    assert get(one[0], eid) is None


@pytest.mark.parametrize("prefix,eid,add,delete,edit,get", SECTIONS, ids=SECTION_IDS)
def test_delete_modify_conflicts_on_the_whole_entry(
    prefix, eid, add, delete, edit, get
):
    base, ours, theirs = triple(sample())
    add(base, eid, 1.0)
    add(ours, eid, 1.0)
    add(theirs, eid, 1.0)
    delete(ours, eid)
    edit(theirs, eid, 5.0)

    merged, conflicts = merge_manifests(base, ours, theirs)

    assert len(conflicts) == 1
    conflict = conflicts[0]
    assert conflict["key"] == f"{prefix}.{eid}"
    assert conflict["base"] == get(base, eid)
    assert "ours" not in conflict  # the deleting side: absent, never null
    assert conflict["theirs"] == get(theirs, eid)
    assert get(merged, eid) is None  # merged carries ours' value: deleted


@pytest.mark.parametrize("prefix,eid,add,delete,edit,get", SECTIONS, ids=SECTION_IDS)
def test_modify_delete_conflicts_with_theirs_null(prefix, eid, add, delete, edit, get):
    base, ours, theirs = triple(sample())
    add(base, eid, 1.0)
    add(ours, eid, 1.0)
    add(theirs, eid, 1.0)
    edit(ours, eid, 5.0)
    delete(theirs, eid)

    merged, conflicts = merge_manifests(base, ours, theirs)

    assert len(conflicts) == 1
    assert conflicts[0]["key"] == f"{prefix}.{eid}"
    assert "theirs" not in conflicts[0]  # the deleting side
    assert conflicts[0]["ours"] == get(ours, eid)
    assert get(merged, eid) == get(ours, eid)


def _without(doc, delete, eid):
    doc = copy.deepcopy(doc)
    delete(doc, eid)
    return doc


# ------------------------------------------------------- atomic sections

def test_pmi_conflicts_as_one_key_even_for_disjoint_sublists():
    base, ours, theirs = triple(
        manifest(
            parts=[
                part(
                    "flange",
                    pmi={
                        "dims": [{"id": "d1", "kind": "linear", "target": "width"}],
                        "datums": [{"id": "a", "face": "top"}],
                    },
                )
            ]
        )
    )
    entry_of(ours["parts"], "flange")["pmi"]["dims"].append(
        {"id": "d2", "kind": "diameter", "target": 9.0}
    )
    entry_of(theirs["parts"], "flange")["pmi"]["datums"].append(
        {"id": "b", "face": "bottom"}
    )

    merged, conflicts = merge_manifests(base, ours, theirs)

    assert keys_of(conflicts) == ["parts.flange.pmi"]
    assert entry_of(merged["parts"], "flange")["pmi"] == (
        entry_of(ours["parts"], "flange")["pmi"]
    )


def test_material_entry_conflicts_as_one_key_for_different_properties():
    base, ours, theirs = triple(sample())
    ours["materials"]["custom_al"]["density_g_cm3"] = 2.80
    theirs["materials"]["custom_al"]["yield_mpa"] = 300.0

    merged, conflicts = merge_manifests(base, ours, theirs)

    assert keys_of(conflicts) == ["materials.custom_al"]
    assert merged["materials"]["custom_al"] == ours["materials"]["custom_al"]


def test_vectors_are_atomic_never_blended():
    base, ours, theirs = triple(sample())
    _w_position(ours, [5.0, 0.0, 0.0])
    _w_position(theirs, [0.0, 0.0, 9.0])

    merged, conflicts = merge_manifests(base, ours, theirs)

    assert keys_of(conflicts) == ["assembly.instances.flange_1.position"]
    assert _r_position(merged) == [5.0, 0.0, 0.0]


def test_solid_materials_merge_per_key():
    base, ours, theirs = triple(
        manifest(parts=[part("flange", solid_materials={"0": "steel", "1": "steel"})])
    )
    entry_of(ours["parts"], "flange")["solid_materials"]["0"] = "inconel718"
    entry_of(theirs["parts"], "flange")["solid_materials"]["1"] = "al6061"

    merged, conflicts = merge_manifests(base, ours, theirs)

    assert conflicts == []
    assert entry_of(merged["parts"], "flange")["solid_materials"] == {
        "0": "inconel718",
        "1": "al6061",
    }


def test_int_and_float_are_distinct_values():
    base, ours, theirs = triple(manifest(parts=[part("a", params={"n": 5})]))
    entry_of(ours["parts"], "a")["params"]["n"] = 6
    entry_of(theirs["parts"], "a")["params"]["n"] = 6.0

    merged, conflicts = merge_manifests(base, ours, theirs)

    assert keys_of(conflicts) == ["parts.a.params.n"]
    assert conflicts[0]["ours"] == 6
    assert conflicts[0]["theirs"] == 6.0
    value = entry_of(merged["parts"], "a")["params"]["n"]
    assert isinstance(value, int) and not isinstance(value, bool)


def test_int_to_float_one_sided_edit_is_a_real_change():
    base, ours, theirs = triple(manifest(parts=[part("a", params={"n": 6})]))
    entry_of(ours["parts"], "a")["params"]["n"] = 6.0

    merged, conflicts = merge_manifests(base, ours, theirs)

    assert conflicts == []
    assert isinstance(entry_of(merged["parts"], "a")["params"]["n"], float)


# ------------------------------------------------- ordering and purity

def test_ordering_is_ours_then_theirs_only_additions():
    base, ours, theirs = triple(manifest(parts=[part("a"), part("b")]))
    ours["parts"] = [entry_of(ours["parts"], "b"), entry_of(ours["parts"], "a")]
    theirs["parts"].append(part("c"))
    theirs["parts"].append(part("d"))

    merged, conflicts = merge_manifests(base, ours, theirs)

    assert conflicts == []
    assert ids(merged["parts"]) == ["b", "a", "c", "d"]


def test_merge_is_deterministic_and_does_not_mutate_inputs():
    base, ours, theirs = triple(sample())
    entry_of(ours["parts"], "flange")["params"]["bolt_d"] = 8.0
    theirs["parts"].append(part("nozzle", params={"throat_d": 30.0}))
    theirs["units"] = "cm"
    snapshot = json.dumps([base, ours, theirs], sort_keys=True)

    first, first_conflicts = merge_manifests(base, ours, theirs)
    second, second_conflicts = merge_manifests(base, ours, theirs)

    assert json.dumps([base, ours, theirs], sort_keys=True) == snapshot
    assert json.dumps(first) == json.dumps(second)
    assert first_conflicts == second_conflicts


def test_merged_document_shares_no_state_with_inputs():
    base, ours, theirs = triple(sample())
    theirs["parts"].append(part("nozzle", params={"throat_d": 30.0}))

    merged, _ = merge_manifests(base, ours, theirs)
    entry_of(merged["parts"], "nozzle")["params"]["throat_d"] = 99.0
    entry_of(merged["parts"], "flange")["params"]["bolt_d"] = 99.0

    assert entry_of(theirs["parts"], "nozzle")["params"]["throat_d"] == 30.0
    assert entry_of(ours["parts"], "flange")["params"]["bolt_d"] == 6.0


def test_merging_a_merge_result_is_stable():
    base, ours, theirs = triple(sample())
    entry_of(ours["parts"], "flange")["params"]["bolt_d"] = 8.0
    theirs["units"] = "cm"

    merged, conflicts = merge_manifests(base, ours, theirs)
    again, again_conflicts = merge_manifests(merged, merged, merged)

    assert conflicts == []
    assert again_conflicts == []
    assert again == merged


# ------------------------------------------------- edges and forward compat

def test_unknown_top_level_section_merges_whole_value():
    base, ours, theirs = triple(
        manifest(drawings={"d1": {"scale": 1.0}, "d2": {"scale": 1.0}})
    )
    theirs["drawings"]["d1"]["scale"] = 2.0

    merged, conflicts = merge_manifests(base, ours, theirs)

    assert conflicts == []
    assert merged["drawings"] == theirs["drawings"]

    base2, ours2, theirs2 = triple(
        manifest(drawings={"d1": {"scale": 1.0}, "d2": {"scale": 1.0}})
    )
    ours2["drawings"]["d1"]["scale"] = 3.0
    theirs2["drawings"]["d2"]["scale"] = 4.0

    merged2, conflicts2 = merge_manifests(base2, ours2, theirs2)

    assert keys_of(conflicts2) == ["drawings"]
    assert merged2["drawings"] == ours2["drawings"]


def test_empty_and_missing_sections():
    assert merge_manifests({}, {}, {}) == ({}, [])

    merged, conflicts = merge_manifests(
        {}, {}, {"name": "proj", "parts": [part("a")]}
    )
    assert conflicts == []
    assert ids(merged["parts"]) == ["a"]

    bare = {"schema_version": 1, "name": "proj", "units": "mm"}
    merged, conflicts = merge_manifests(
        bare, bare, {"schema_version": 1, "name": "proj", "units": "cm"}
    )
    assert conflicts == []
    assert merged == {"schema_version": 1, "name": "proj", "units": "cm"}
    assert "parts" not in merged


def test_clean_merge_can_still_break_references():
    # ours deletes the part, theirs adds an instance of it: neither side touched
    # the other's key, so the driver is clean by design. The Slice 3 validation
    # pass is the backstop.
    base, ours, theirs = triple(manifest(parts=[part("flange")]))
    ours["parts"] = []
    theirs["assembly"]["instances"].append(instance("flange_1", "flange"))

    merged, conflicts = merge_manifests(base, ours, theirs)

    assert conflicts == []
    assert merged["parts"] == []
    assert ids(merged["assembly"]["instances"]) == ["flange_1"]


def test_part_field_add_and_remove():
    base, ours, theirs = triple(manifest(parts=[part("a")]))
    entry_of(ours["parts"], "a")["source"] = "a.step"
    entry_of(theirs["parts"], "a")["kind"] = "reference"

    merged, conflicts = merge_manifests(base, ours, theirs)

    assert conflicts == []
    entry = entry_of(merged["parts"], "a")
    assert entry["source"] == "a.step"
    assert entry["kind"] == "reference"

    base2, ours2, theirs2 = triple(
        manifest(parts=[part("a", kind="reference", source="a.step")])
    )
    entry_of(ours2["parts"], "a").pop("source")
    merged2, conflicts2 = merge_manifests(base2, ours2, theirs2)
    assert conflicts2 == []
    assert "source" not in entry_of(merged2["parts"], "a")


def test_param_delete_versus_edit_conflicts_at_the_param_key():
    base, ours, theirs = triple(manifest(parts=[part("a", params={"x": 1.0})]))
    entry_of(ours["parts"], "a")["params"].pop("x")
    entry_of(theirs["parts"], "a")["params"]["x"] = 2.0

    merged, conflicts = merge_manifests(base, ours, theirs)

    assert keys_of(conflicts) == ["parts.a.params.x"]
    assert "ours" not in conflicts[0]  # ours deleted it
    assert entry_of(merged["parts"], "a")["params"] == {}


def test_multiple_conflicts_are_all_reported():
    base, ours, theirs = triple(sample())
    ours["units"] = "cm"
    theirs["units"] = "in"
    _w_param(ours, 8.0)
    _w_param(theirs, 5.0)
    _w_position(ours, [1.0, 0.0, 0.0])
    _w_position(theirs, [2.0, 0.0, 0.0])

    _, conflicts = merge_manifests(base, ours, theirs)

    assert sorted(keys_of(conflicts)) == [
        "assembly.instances.flange_1.position",
        "parts.flange.params.bolt_d",
        "units",
    ]
    assert all(c["kind"] == "manifest" for c in conflicts)
    assert all(set(c) <= set(CONFLICT_KEYS) for c in conflicts)


# --------------------------------------------------------- apply_choices

def conflicted():
    base, ours, theirs = triple(sample())
    _w_param(ours, 8.0)
    _w_param(theirs, 5.0)
    ours["units"] = "cm"
    theirs["units"] = "in"
    merged, conflicts = merge_manifests(base, ours, theirs)
    return merged, conflicts


def test_apply_choices_take_theirs_and_base():
    merged, conflicts = conflicted()

    resolved, remaining = apply_choices(
        merged,
        conflicts,
        {
            "parts.flange.params.bolt_d": {"take": "theirs"},
            "units": {"take": "base"},
        },
    )

    assert remaining == []
    assert _r_param(resolved) == 5.0
    assert resolved["units"] == "mm"
    # the input document is left untouched
    assert _r_param(merged) == 8.0


def test_apply_choices_explicit_value():
    merged, conflicts = conflicted()

    resolved, remaining = apply_choices(
        merged, conflicts, {"parts.flange.params.bolt_d": {"value": 12.0}}
    )

    assert keys_of(remaining) == ["units"]
    assert _r_param(resolved) == 12.0


def test_apply_choices_partial_returns_remaining_conflicts():
    merged, conflicts = conflicted()

    resolved, remaining = apply_choices(merged, conflicts, {"units": {"take": "ours"}})

    assert keys_of(remaining) == ["parts.flange.params.bolt_d"]
    assert resolved["units"] == "cm"

    final, still = apply_choices(
        resolved, remaining, {"parts.flange.params.bolt_d": {"take": "theirs"}}
    )
    assert still == []
    assert _r_param(final) == 5.0


def test_apply_choices_restores_and_removes_whole_entries():
    base, ours, theirs = triple(sample())
    ours["parts"] = []
    entry_of(theirs["parts"], "flange")["label"] = "kept"
    merged, conflicts = merge_manifests(base, ours, theirs)
    assert keys_of(conflicts) == ["parts.flange"]

    restored, remaining = apply_choices(
        merged, conflicts, {"parts.flange": {"take": "theirs"}}
    )
    assert remaining == []
    assert entry_of(restored["parts"], "flange")["label"] == "kept"

    dropped, _ = apply_choices(merged, conflicts, {"parts.flange": {"take": "ours"}})
    assert dropped["parts"] == []


def test_apply_choices_rejects_unknown_key_and_bad_shape():
    merged, conflicts = conflicted()

    with pytest.raises(ValidationError):
        apply_choices(merged, conflicts, {"parts.nope.params.x": {"take": "ours"}})

    with pytest.raises(ValidationError):
        apply_choices(merged, conflicts, {"units": {"take": "mine"}})

    with pytest.raises(ValidationError):
        apply_choices(merged, conflicts, {"units": "ours"})


def test_apply_choices_no_choices_is_identity():
    merged, conflicts = conflicted()

    resolved, remaining = apply_choices(merged, conflicts, {})

    assert resolved == merged
    assert remaining == conflicts


# ------------------------------- X12: an absent side is not an authored null
#
# ``None`` used to encode both "this side has no such key" and "this side
# authored a JSON null", so ``take: ours`` on an authored null DELETED the key.
# The encoding is now presence-based: an absent side OMITS its entry.


def _param_conflict(ours_params, theirs_params, base_params=None):
    base_params = {"x": 1.0} if base_params is None else base_params
    base, ours, theirs = triple(
        manifest(parts=[part("a", params=copy.deepcopy(base_params))])
    )
    entry_of(ours["parts"], "a")["params"] = copy.deepcopy(ours_params)
    entry_of(theirs["parts"], "a")["params"] = copy.deepcopy(theirs_params)
    return merge_manifests(base, ours, theirs)


def _params(doc):
    return entry_of(doc["parts"], "a")["params"]


def test_x12_an_absent_side_is_omitted_from_the_conflict():
    merged, conflicts = _param_conflict({}, {"x": 2.0})

    assert keys_of(conflicts) == ["parts.a.params.x"]
    assert "ours" not in conflicts[0]      # ours deleted it: absent, not null
    assert conflicts[0]["theirs"] == 2.0
    assert conflicts[0]["base"] == 1.0
    assert _params(merged) == {}


def test_x12_an_authored_null_is_reported_as_null():
    merged, conflicts = _param_conflict({"x": None}, {"x": 2.0})

    assert "ours" in conflicts[0]
    assert conflicts[0]["ours"] is None
    assert _params(merged) == {"x": None}


def test_x12_taking_an_authored_null_keeps_the_key():
    merged, conflicts = _param_conflict({"x": None}, {"x": 2.0})

    resolved, remaining = apply_choices(
        merged, conflicts, {"parts.a.params.x": {"take": "ours"}}
    )

    assert remaining == []
    assert _params(resolved) == {"x": None}


def test_x12_taking_an_absent_side_deletes_the_key():
    merged, conflicts = _param_conflict({}, {"x": 2.0})

    resolved, remaining = apply_choices(
        merged, conflicts, {"parts.a.params.x": {"take": "ours"}}
    )

    assert remaining == []
    assert _params(resolved) == {}


def test_x12_take_variants_across_absent_and_null_sides():
    # theirs authored a null: taking it writes the null, it does not delete.
    merged, conflicts = _param_conflict(
        {"x": 1.0}, {"x": None}, base_params={"x": 0.0}
    )
    got, _ = apply_choices(
        merged, conflicts, {"parts.a.params.x": {"take": "theirs"}}
    )
    assert _params(got) == {"x": None}

    # both sides ADDED the key: there is no base, so taking base deletes it.
    merged, conflicts = _param_conflict({"x": 1.0}, {"x": 2.0}, base_params={})
    assert "base" not in conflicts[0]
    got, _ = apply_choices(
        merged, conflicts, {"parts.a.params.x": {"take": "base"}}
    )
    assert _params(got) == {}

    # a base that IS an authored null is takeable as that null.
    merged, conflicts = _param_conflict(
        {"x": 1.0}, {"x": 2.0}, base_params={"x": None}
    )
    assert conflicts[0]["base"] is None
    got, _ = apply_choices(
        merged, conflicts, {"parts.a.params.x": {"take": "base"}}
    )
    assert _params(got) == {"x": None}


def test_x12_an_absent_whole_entry_is_omitted_too():
    base, ours, theirs = triple(manifest(parts=[part("flange")]))
    ours["parts"] = []
    entry_of(theirs["parts"], "flange")["label"] = "kept"

    _merged, conflicts = merge_manifests(base, ours, theirs)

    assert keys_of(conflicts) == ["parts.flange"]
    assert "ours" not in conflicts[0]
    assert conflicts[0]["theirs"]["label"] == "kept"


# --------------------- X13: dotted ids must not break conflict reversibility
#
# The public conflict key stays a dotted string (it addresses the choice), but
# apply_choices must resolve it through the RECORDED path segments, never by
# re-splitting on '.'.


def test_x13_a_dotted_solid_material_key_resolves_to_the_real_mapping():
    base, ours, theirs = triple(
        manifest(parts=[part("body", solid_materials={"wall.inner": "stainless_316"})])
    )
    entry_of(ours["parts"], "body")["solid_materials"]["wall.inner"] = "inconel718"
    entry_of(theirs["parts"], "body")["solid_materials"]["wall.inner"] = "alu6061"

    merged, conflicts = merge_manifests(base, ours, theirs)

    assert keys_of(conflicts) == ["parts.body.solid_materials.wall.inner"]
    assert conflicts[0]["path"] == [
        "parts", "body", "solid_materials", "wall.inner"
    ]

    resolved, remaining = apply_choices(
        merged,
        conflicts,
        {"parts.body.solid_materials.wall.inner": {"take": "theirs"}},
    )

    assert remaining == []
    entry = entry_of(resolved["parts"], "body")
    assert entry["solid_materials"] == {"wall.inner": "alu6061"}
    assert "solid_materials.wall.inner" not in entry  # no bogus flat field


def test_x13_a_dotted_part_id_resolves_to_the_real_entry():
    base, ours, theirs = triple(manifest(parts=[part("a.b", params={"x": 1.0})]))
    entry_of(ours["parts"], "a.b")["params"]["x"] = 2.0
    entry_of(theirs["parts"], "a.b")["params"]["x"] = 3.0

    merged, conflicts = merge_manifests(base, ours, theirs)

    assert conflicts[0]["path"] == ["parts", "a.b", "params", "x"]
    resolved, remaining = apply_choices(
        merged, conflicts, {conflicts[0]["key"]: {"take": "theirs"}}
    )

    assert remaining == []
    assert entry_of(resolved["parts"], "a.b")["params"] == {"x": 3.0}


def test_x13_a_dotted_instance_id_resolves_to_the_real_instance():
    base, ours, theirs = triple(
        manifest(parts=[part("a")], instances=[instance("a.1", "a")])
    )
    entry_of(ours["assembly"]["instances"], "a.1")["position"] = [1.0, 0.0, 0.0]
    entry_of(theirs["assembly"]["instances"], "a.1")["position"] = [2.0, 0.0, 0.0]

    merged, conflicts = merge_manifests(base, ours, theirs)

    assert conflicts[0]["path"] == ["assembly", "instances", "a.1", "position"]
    resolved, _ = apply_choices(
        merged, conflicts, {conflicts[0]["key"]: {"take": "theirs"}}
    )
    assert entry_of(resolved["assembly"]["instances"], "a.1")["position"] == [
        2.0, 0.0, 0.0
    ]


# ------------------------------------------- PRD-011: the two package maps


def pkg(version="1.0.0", index="agentcad-core", content_id="sha256:" + "9f" * 32):
    return {"version": version, "content_id": content_id, "index": index,
            "source": {"kind": "local", "path": "catalog"}}


def with_packages(**locked):
    doc = manifest(parts=[part("flange")])
    doc["packages"] = {
        name: {"version_req": "^1.0.0", "index": entry["index"]}
        for name, entry in locked.items()
    }
    doc["packages_lock"] = dict(locked)
    return doc


def test_two_branches_adding_different_packages_merge_clean():
    """The whole point of the key-wise heads: adding `iso4762` on one branch
    and `din625` on another is not a conflict."""
    base = manifest(parts=[part("flange")])
    ours = with_packages(iso4762=pkg())
    theirs = with_packages(din625=pkg())

    merged, conflicts = merge_manifests(base, ours, theirs)

    assert conflicts == []
    assert sorted(merged["packages_lock"]) == ["din625", "iso4762"]
    assert sorted(merged["packages"]) == ["din625", "iso4762"]


def test_the_same_package_at_two_versions_conflicts_on_the_lock_entry():
    base = with_packages(iso4762=pkg())
    ours = with_packages(iso4762=pkg(version="1.1.0", content_id="sha256:" + "aa" * 32))
    theirs = with_packages(iso4762=pkg(version="1.2.0", content_id="sha256:" + "bb" * 32))

    merged, conflicts = merge_manifests(base, ours, theirs)

    assert "packages_lock.iso4762" in keys_of(conflicts)
    conflict = next(c for c in conflicts if c["key"] == "packages_lock.iso4762")
    assert conflict["path"] == ["packages_lock", "iso4762"]
    # atomic: the recorded sides are whole entries, never half of one
    assert conflict["ours"]["version"] == "1.1.0"
    assert conflict["theirs"]["content_id"] == "sha256:" + "bb" * 32


def test_a_lock_entry_never_merges_field_wise():
    """One side's version with the other side's content id is an entry nobody
    authored and that verifies against nothing."""
    base = with_packages(iso4762=pkg())
    ours = with_packages(iso4762=pkg(version="1.1.0"))
    theirs = with_packages(iso4762=pkg(content_id="sha256:" + "cc" * 32))

    merged, conflicts = merge_manifests(base, ours, theirs)

    assert keys_of(conflicts).count("packages_lock.iso4762") == 1
    assert merged["packages_lock"]["iso4762"] == ours["packages_lock"]["iso4762"]


def test_resolving_a_package_conflict_writes_into_the_map_not_a_flat_key():
    base = with_packages(iso4762=pkg())
    ours = with_packages(iso4762=pkg(version="1.1.0", index="agentcad-core"))
    theirs = with_packages(iso4762=pkg(version="1.2.0", index="acme"))

    merged, conflicts = merge_manifests(base, ours, theirs)
    choices = {c["key"]: {"take": "theirs"} for c in conflicts}
    resolved, remaining = apply_choices(merged, conflicts, choices)

    assert remaining == []
    assert resolved["packages_lock"]["iso4762"]["version"] == "1.2.0"
    assert resolved["packages"]["iso4762"]["index"] == "acme"
    assert "packages.iso4762" not in resolved
    assert "packages_lock.iso4762" not in resolved


def test_taking_a_side_that_never_had_the_package_removes_it():
    base = manifest(parts=[part("flange")])
    ours = with_packages(iso4762=pkg(version="1.1.0"))
    ours["packages"]["iso4762"]["version_req"] = "^1.1.0"
    theirs = with_packages(iso4762=pkg(version="1.2.0"))
    theirs["packages"]["iso4762"]["version_req"] = "^1.2.0"

    merged, conflicts = merge_manifests(base, ours, theirs)
    assert sorted(keys_of(conflicts)) == ["packages.iso4762",
                                          "packages_lock.iso4762"]
    choices = {c["key"]: {"take": "base"} for c in conflicts}
    resolved, remaining = apply_choices(merged, conflicts, choices)

    assert remaining == []
    assert resolved["packages"] == {}
    assert resolved["packages_lock"] == {}


def test_one_side_removing_a_package_the_other_left_alone_merges_clean():
    base = with_packages(iso4762=pkg(), din625=pkg())
    ours = with_packages(iso4762=pkg(), din625=pkg())
    theirs = with_packages(iso4762=pkg())

    merged, conflicts = merge_manifests(base, ours, theirs)

    assert conflicts == []
    assert list(merged["packages_lock"]) == ["iso4762"]


def test_a_manifest_with_no_packages_is_untouched_by_the_new_heads():
    """FR15 from the merge side: a project that never used packages merges
    byte-identically to how it did before the keys existed."""
    base, ours, theirs = triple(manifest(parts=[part("flange")]))
    entry_of(ours["parts"], "flange")["label"] = "ours"

    merged, conflicts = merge_manifests(base, ours, theirs)

    assert conflicts == []
    assert "packages" not in merged and "packages_lock" not in merged
