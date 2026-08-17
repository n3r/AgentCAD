"""PRD-012 acceptance — configurations, AC1–AC9.

One test per criterion, each naming it in its docstring, each graded against
the shipped surface rather than a stub built for the occasion.

| AC | Test |
|---|---|
| AC1 | `TestTheFlangeFamily::test_ac1_the_family_builds_as_one_matrix_of_three_distinct_masses` |
| AC2 | `TestTheFlangeFamily::test_ac2_the_drawing_tabulates_one_measured_row_per_configuration` |
| AC3 | `TestTheFlangeFamily::test_ac3_removing_a_bound_configuration_names_the_instance` |
| AC4 | `TestTheFlangeFamily::test_ac4_a_configuration_export_is_suffixed_and_the_base_one_is_not` |
| AC5 | `TestTheFlangeFamily::test_ac5_identical_parameter_maps_share_one_cache_entry` |
| AC6 | `TestTheFlangeFamily::test_ac6_two_instances_of_one_part_are_two_sizes` |
| AC7 | `TestTheFlangeFamily::test_ac7_an_override_diverges_and_removing_it_returns_to_the_configuration` |
| AC8 | `test_ac8_a_project_without_configurations_is_byte_identical` + `test_ac8_the_full_suite_count_is_cited` |
| AC9 | `test_ac9_the_ui_surfaces_exist_and_the_session_was_clean` |

Three of them are worth reading before you believe them:

* **AC5 is anchored by a kernel-call counter, not by a key comparison.** Two
  equal strings prove the *service* agreed with itself; what the criterion
  claims is that the second configuration cost no geometry. So the matrix runs
  with `service.kernel.request` counted by method, and the assertion is that a
  four-member family with three distinct parameter maps issues exactly three
  `build` calls — and that toggling `active_config` afterwards issues none.
* **AC8 is two claims and they are graded separately.** "Byte-identical
  without configurations" is measured against `test_examples_golden.GOLDENS`
  on a **copy** of the bundled `examples/rocketry` — the same mesh sha PRD-010
  pinned — plus the raw manifest carrying neither new key. "Full suite green"
  is a claim about a *run*, so it stays an evidence check on the newest
  changelog entry (the PRD-004 AC10 / PRD-011 AC8 precedent).
* **AC9 is a browser session, and a test cannot be one.** The session is in
  changelog `0195-configs-ui.md` (real headless Chrome, `ERROR COUNT: 0` /
  `FAILED REQUESTS: 0`). What a test grades is the *evidence*: the surfaces
  that session drove still exist, the routes they call are still mounted, and
  the entry still records the counts.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest

from agentcad.core.materials import DEFAULT_MATERIAL
from agentcad.core.tools import build_registry

from .conftest import (
    FLANGE_SCRIPT,
    THREE_SIZE_CONFIGS,
    clone_test_service,
    make_test_service,
)
from .test_examples_golden import GOLDENS, assert_matches_golden, measure_part

pytestmark = pytest.mark.slow

REPO = Path(__file__).resolve().parents[1]
CHANGELOG = REPO / "docs" / "changelog"
FRONTEND = REPO / "frontend"
EXAMPLES = REPO / "examples"
PRD_NAME = "PRD-012-configurations.md"

CONFIG_TOOLS = ("set_part_configs", "list_configs", "build_configs",
                "set_active_config", "set_instance_config")

#: The family minus its largest member — the removal AC3 refuses and then
#: allows.
WITHOUT_L = {name: entry for name, entry in THREE_SIZE_CONFIGS.items()
             if name != "l"}


def _find_prd() -> Path:
    """Locate the PRD wherever it currently lives.

    A PRD moves from `in-progress/` to `completed/` at **merge**, not when the
    build finishes, so a test that hard-codes one directory is red for the
    whole review window (PRD-010's close-out hit exactly that, changelog 0164;
    `tests/test_prd011_acceptance.py` carries the same helper).
    """
    prd_root = REPO / "docs" / "prd"
    for stage in ("in-progress", "completed", "pending"):
        candidate = prd_root / stage / PRD_NAME
        if candidate.is_file():
            return candidate
    found = sorted(prd_root.rglob(PRD_NAME))
    assert found, f"{PRD_NAME} is not anywhere under {prd_root}"
    return found[0]


PRD = _find_prd()


def _counting(service, monkeypatch) -> dict:
    """Count kernel requests by method (the `tests/test_specs_api.py` idiom)."""
    calls: dict = {}
    original = service.kernel.request

    def counting(method, params, timeout_s=None, affinity=None):
        calls[method] = calls.get(method, 0) + 1
        return original(method, params, timeout_s=timeout_s, affinity=affinity)

    monkeypatch.setattr(service.kernel, "request", counting)
    return calls


# ============================================================ AC1–AC7


@pytest.mark.integration
@pytest.mark.timeout(600)
class TestTheFlangeFamily:
    """The S/M/L flange family of the PRD's own walkthrough, built for real.

    The template project — the script plus the family, **no builds** — is made
    once per class and cloned per test, so every criterion is measured on its
    own manifest and its own cold cache directory. Cold is deliberate: AC5
    counts kernel builds, and a warm clone would count zero of them for the
    wrong reason.
    """

    @pytest.fixture(scope="class")
    @staticmethod
    def family_projects(kernel, tmp_path_factory):
        projects = tmp_path_factory.mktemp("prd012_projects")
        svc = make_test_service(projects, kernel)
        svc.create_project("demo")
        svc.store.add_part("demo", "flange", "Flange", DEFAULT_MATERIAL,
                           FLANGE_SCRIPT)
        return projects

    @pytest.fixture
    def demo(self, kernel, tmp_path, family_projects):
        service = clone_test_service(family_projects, tmp_path / "projects",
                                     kernel)
        return service, build_registry(service)

    @staticmethod
    def _declare(registry, configs=None) -> dict:
        out = registry.call("set_part_configs", {
            "project": "demo", "part_id": "flange",
            "configs": THREE_SIZE_CONFIGS if configs is None else configs})
        assert "error" not in out, out
        return out

    # ------------------------------------------------------------- AC1

    def test_ac1_the_family_builds_as_one_matrix_of_three_distinct_masses(
            self, demo):
        """**AC1** — a three-size flange family builds as a matrix in **one**
        `build_configs` call returning per-configuration mass, with correct
        distinct values.

        The masses are cross-checked in the only way that makes them a claim
        about geometry rather than about a row: L's matrix mass must be the
        mass `get_metrics` reports after `set_active_config l` — two different
        code paths (the pure-configuration build and the working-state
        rebuild) arriving at the same number, which is exactly what "a
        variant's identity does not depend on session state" means.
        """
        service, registry = demo
        self._declare(registry)

        matrix = registry.call("build_configs", {"project": "demo",
                                                 "part_id": "flange"})
        assert "error" not in matrix, matrix
        assert matrix["part_id"] == "flange"
        rows = matrix["configs"]
        assert [row["name"] for row in rows] == ["s", "m", "l"]   # family order
        assert [row["label"] for row in rows] == ["Small", "Medium", "Large"]
        assert all(row["ok"] for row in rows), rows

        masses = {row["name"]: row["metrics"]["mass_g"] for row in rows}
        assert masses["s"] < masses["m"] < masses["l"]
        assert len(set(masses.values())) == 3
        assert len({row["cache_key"] for row in rows}) == 3
        assert all(row["cached"] is False for row in rows), \
            "a cold cache reported a hit"

        # The matrix left the working state alone: nothing is active, no
        # override was invented, and the part's own badge is untouched.
        stored = service.store.get_part("demo", "flange")
        assert stored.active_config is None and stored.params == {}

        # ...and L's row is L's geometry, measured again through the working
        # state.
        assert "error" not in registry.call("set_active_config", {
            "project": "demo", "part_id": "flange", "config": "l"})
        metrics = registry.call("get_metrics", {"project": "demo",
                                                "part_id": "flange"})
        assert metrics["mass_g"] == pytest.approx(masses["l"], rel=1e-12)
        assert metrics["bbox"]["max"][0] == pytest.approx(100.0)   # OD 200 / 2

    # ------------------------------------------------------------- AC2

    def test_ac2_the_drawing_tabulates_one_measured_row_per_configuration(
            self, demo):
        """**AC2** — the flange drawing with `dim_table` shows a tabulated
        dimension table with one row per configuration (SVG content + the
        structured echo), and the browser half is on the record.

        Every number in the table is **measured from that configuration's
        built shape inside the handler** — which is why the X column is the
        outer diameter and not a parameter echoed back, and why a row's cell
        prints `Label (name)`: the name is the identity every other surface
        uses (`part@config`, `?config=`, the manifest key) and a sheet reading
        only "Small" could not be traced back to it.
        """
        service, registry = demo
        self._declare(registry)

        result = registry.call("generate_drawing", {
            "project": "demo", "part_id": "flange", "dim_table": True})
        assert "error" not in result, result

        table = result["dim_table"]
        assert table == result["detected"]["dim_table"]
        assert table["placement"] == "right-column"
        assert table["columns"] == ["outer_d", "bore_d", "bc_d"]
        assert [row["config"] for row in table["rows"]] == ["s", "m", "l"]
        assert [row["label"] for row in table["rows"]] == ["Small", "Medium",
                                                           "Large"]
        assert all(row["ok"] for row in table["rows"]), table["rows"]
        extents = {row["config"]: row["values"] for row in table["rows"]}
        assert [extents[n]["X"] for n in ("s", "m", "l")] == [100.0, 140.0,
                                                             200.0]
        assert [extents[n]["Y"] for n in ("s", "m", "l")] == [100.0, 140.0,
                                                             200.0]
        assert {extents[n]["Z"] for n in ("s", "m", "l")} == {14.0}

        svg = (service.store.exports_dir("demo")
               / "flange_drawing.svg").read_text(encoding="utf-8")
        for label, name in (("Small", "s"), ("Medium", "m"), ("Large", "l")):
            assert svg.count(f">{label} ({name})<") == 1, label
        assert svg.count(">config<") == 1
        for column in table["columns"]:
            assert svg.count(f">{column}<") == 1, column
        for value in ("100.00", "140.00", "200.00"):
            assert value in svg

        # The browser half of AC2 ("one browser check") is the session in
        # changelog 0195, where the preview was opened with the table on.
        entry = (CHANGELOG / "0195-configs-ui.md").read_text(encoding="utf-8")
        assert "dim table" in entry and "flange_m_drawing.svg" in entry
        assert "ERROR COUNT: 0" in entry

    # ------------------------------------------------------------- AC3

    def test_ac3_removing_a_bound_configuration_names_the_instance(self, demo):
        """**AC3** — `set_part_configs` removing a configuration an assembly
        instance is bound to returns `conflict_error` **naming the instance**;
        after clearing the binding the removal succeeds.

        The refusal is graded in three places at once, because a conflict that
        does not survive all three is not referential integrity: the payload
        names the referrer as data (`details.instances`) *and* in the message,
        and not one byte of the family was written.
        """
        service, registry = demo
        self._declare(registry)
        assert "error" not in registry.call("set_assembly", {
            "project": "demo",
            "instances": [{"id": "f1", "part": "flange", "config": "l"}]})

        refused = registry.call("set_part_configs", {
            "project": "demo", "part_id": "flange", "configs": WITHOUT_L})
        assert refused["error"]["type"] == "conflict_error"
        details = refused["error"]["details"]
        assert details["part"] == "flange"
        assert details["configs"] == ["l"]
        assert details["instances"] == ["f1"]
        assert details["active_config"] is False
        assert "f1" in refused["error"]["message"]
        assert list(service.store.get_part("demo", "flange").configs) == \
            ["s", "m", "l"], "a refused removal wrote anyway"

        # `list_configs` makes it a lookup before it is a surprise.
        row = registry.call("list_configs", {"project": "demo",
                                             "part_id": "flange"})["parts"][0]
        assert row["referrers"] == {"l": ["f1"]}

        assert "error" not in registry.call("set_instance_config", {
            "project": "demo", "instance": "f1"})
        removed = registry.call("set_part_configs", {
            "project": "demo", "part_id": "flange", "configs": WITHOUT_L})
        assert "error" not in removed, removed
        assert list(removed["configs"]) == ["s", "m"]
        assert "l" not in service.get_part("demo", "flange")["configs"]

    # ------------------------------------------------------------- AC4

    def test_ac4_a_configuration_export_is_suffixed_and_the_base_one_is_not(
            self, demo):
        """**AC4** — `export_part {config: "l"}` writes `flange_l.step`; base
        export naming is unchanged.

        The base file is read before and after the configuration export: a
        suffix that is really a suffix must leave the base artifact on disk
        untouched, not rewrite it. (STEP carries a timestamp in its header, so
        the comparison is of one file across the second call, never of two
        separate exports.)
        """
        service, registry = demo
        self._declare(registry)
        exports = service.store.exports_dir("demo")

        base = registry.call("export_part", {
            "project": "demo", "part_id": "flange", "format": "step"})
        assert "error" not in base, base
        assert Path(base["path"]).name == "flange.step"
        assert "config" not in base
        base_bytes = (exports / "flange.step").read_bytes()

        large = registry.call("export_part", {
            "project": "demo", "part_id": "flange", "format": "step",
            "config": "l"})
        assert "error" not in large, large
        assert Path(large["path"]).name == "flange_l.step"
        assert large["config"] == "l"
        assert (exports / "flange_l.step").is_file()
        assert (exports / "flange.step").read_bytes() == base_bytes

        # Lowercase by the frozen grammar: `flange_l.step`, never `flange_L`.
        assert sorted(p.name for p in exports.glob("flange*.step")) == \
            ["flange.step", "flange_l.step"]

    # ------------------------------------------------------------- AC5

    def test_ac5_identical_parameter_maps_share_one_cache_entry(
            self, demo, monkeypatch):
        """**AC5** — two configurations with identical resolved parameters
        share one cache entry, distinct ones get distinct entries, and
        toggling `active_config` twice rebuilds from cache.

        Anchored by the kernel-call counter rather than by a key comparison:
        two equal strings prove only that the service agreed with itself, and
        the criterion is about geometry not being computed twice.
        `m2` is `m` spelled again (a fourth member, four rows, three keys,
        **three** `build` calls) — and the toggle afterwards issues none,
        because the working state's key *is* the configuration's pure key once
        the overrides are cleared.
        """
        service, registry = demo
        family = {**THREE_SIZE_CONFIGS,
                  "m2": {"params": dict(THREE_SIZE_CONFIGS["m"]["params"]),
                         "label": "Medium again"}}
        self._declare(registry, configs=family)

        calls = _counting(service, monkeypatch)
        rows = registry.call("build_configs", {"project": "demo",
                                               "part_id": "flange"})["configs"]
        assert [row["name"] for row in rows] == ["s", "m", "l", "m2"]
        assert all(row["ok"] for row in rows), rows
        keys = {row["name"]: row["cache_key"] for row in rows}
        assert keys["m"] == keys["m2"]
        assert len(set(keys.values())) == 3
        assert calls["build"] == 3, (
            f"four members with three distinct parameter maps cost "
            f"{calls.get('build')} builds")
        # The shared row is reported as a hit rather than silently duplicated.
        assert {row["name"]: row["cached"] for row in rows}["m2"] is True

        calls.clear()
        for name in ("l", "m", "l", "m"):
            out = registry.call("set_active_config", {
                "project": "demo", "part_id": "flange", "config": name})
            assert "error" not in out, out
            assert out["ok"] is True
            assert out["cache_key"] == keys[name]
        assert calls.get("build", 0) == 0, \
            "toggling the active configuration re-entered the kernel"

    # ------------------------------------------------------------- AC6

    def test_ac6_two_instances_of_one_part_are_two_sizes(self, demo):
        """**AC6** — two instances of one part bound to different
        configurations report different masses in `get_assembly`, and
        `check_interference` uses each instance's own geometry.

        The interference half is discriminating in both directions: the two S
        instances stand 110 mm apart, which clears at S (⌀100) and would
        overlap by 30 mm at L (⌀200), while the two L instances are stacked
        5 mm apart in Z. A resolution that ignored the bindings reports the
        wrong pair set, not merely a different volume.
        """
        service, registry = demo
        self._declare(registry)

        assembly = registry.call("set_assembly", {"project": "demo", "instances": [
            {"id": "s1", "part": "flange", "position": [0, 0, 0], "config": "s"},
            {"id": "s2", "part": "flange", "position": [110, 0, 0],
             "config": "s"},
            {"id": "l1", "part": "flange", "position": [0, 400, 0],
             "config": "l"},
            {"id": "l2", "part": "flange", "position": [0, 400, 5],
             "config": "l"},
        ]})
        assert "error" not in assembly, assembly
        by_id = {entry["id"]: entry for entry in assembly["instances"]}
        assert [entry["state"] for entry in assembly["instances"]] == ["ok"] * 4
        assert by_id["s1"]["config"] == "s" and by_id["l1"]["config"] == "l"
        assert by_id["s1"]["mass_g"] < by_id["l1"]["mass_g"]
        assert by_id["s1"]["mass_g"] == pytest.approx(by_id["s2"]["mass_g"])
        # Content-addressed geometry: one mesh key per size, not per instance.
        assert by_id["s1"]["mesh_key"] == by_id["s2"]["mesh_key"]
        assert by_id["s1"]["mesh_key"] != by_id["l1"]["mesh_key"]

        result = registry.call("check_interference", {"project": "demo"})
        assert result["checked"] == 4
        assert [{pair["a"], pair["b"]} for pair in result["pairs"]] == \
            [{"l1", "l2"}]
        assert result["pairs"][0]["volume_mm3"] > 1000.0

    # ------------------------------------------------------------- AC7

    def test_ac7_an_override_diverges_and_removing_it_returns_to_the_configuration(
            self, demo):
        """**AC7** — an explicit `set_params` on top of an active
        configuration flags divergence in `get_part`'s status; clearing the
        override (`null` removes) returns the part to the pure configuration.

        Divergence is **semantic**, not "an override exists": the round trip
        ends on the same cache key the pure configuration builds under, which
        is the only statement that means the geometry came back too.
        """
        service, registry = demo
        self._declare(registry)
        pure = service._ensure_config_built("demo", "flange", "m")["cache_key"]

        assert "error" not in registry.call("set_active_config", {
            "project": "demo", "part_id": "flange", "config": "m"})
        before = registry.call("get_part", {"project": "demo",
                                            "part_id": "flange"})
        assert before["active_config"] == "m"
        assert list(before["configs"]) == ["s", "m", "l"]
        assert before["status"]["diverged"] is False
        assert before["status"]["diverged_params"] == []
        assert service.mesh_info("demo", "flange")["key"] == pure

        assert "error" not in registry.call("set_params", {
            "project": "demo", "part_id": "flange", "values": {"thick": 20.0}})
        diverged = registry.call("get_part", {"project": "demo",
                                              "part_id": "flange"})
        assert diverged["active_config"] == "m"
        assert diverged["params"] == {"thick": 20.0}     # the OVERRIDES
        assert diverged["status"]["diverged"] is True
        assert diverged["status"]["diverged_params"] == ["thick"]
        assert service.mesh_info("demo", "flange")["key"] != pure

        assert "error" not in registry.call("set_params", {
            "project": "demo", "part_id": "flange", "values": {"thick": None}})
        after = registry.call("get_part", {"project": "demo",
                                           "part_id": "flange"})
        assert after["params"] == {}
        assert after["active_config"] == "m"
        assert after["status"]["diverged"] is False
        assert after["status"]["diverged_params"] == []
        assert service.mesh_info("demo", "flange")["key"] == pure


# ==================================================================== AC8


@pytest.mark.integration
@pytest.mark.timeout(600)
def test_ac8_a_project_without_configurations_is_byte_identical(
        kernel, tmp_path):
    """**AC8, first half** — a project with no configurations behaves
    byte-identically.

    Graded against the strongest statement in the tree: PRD-010's golden for
    `examples/rocketry`'s flange, which pins the metrics **and the `.acm`
    payload sha**. A copy of the bundled example is rebuilt through the real
    pipeline (`measure_part` goes through `service._rebuild`, the cache key
    and the sidecar) and must still land on the same bytes — so any of
    PRD-012's twenty `record.params → record.effective_params` renames that
    had changed what a configuration-free part builds would show up here as a
    moved sha, not as a rounding.

    The manifest half is the other direction: `to_manifest` must still write
    neither new key, because a project that gains `"configs": {}` is not
    byte-identical however equal its geometry is.
    """
    projects = tmp_path / "projects"
    projects.mkdir()
    shutil.copytree(EXAMPLES / "rocketry", projects / "rocketry",
                    ignore=shutil.ignore_patterns(".cache", "exports"))
    service = make_test_service(projects, kernel)
    name = service.open_project(str(projects / "rocketry"))["name"]

    measured = measure_part(service, name, "flange")
    assert_matches_golden(measured, GOLDENS[("rocketry", "flange")],
                          "rocketry/flange")

    manifest_path = projects / "rocketry" / "project.json"
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    for entry in raw["parts"]:
        assert "configs" not in entry, entry["id"]
        assert "active_config" not in entry, entry["id"]
    for instance in raw.get("assembly", {}).get("instances", []):
        assert "config" not in instance, instance["id"]

    # ...and the same is true after a rebuild has rewritten the manifest.
    # Fix wave (F8): AC8a's word is **byte**-identical, and a `json.loads` on
    # both sides would pass a file that had been reordered or reformatted. The
    # bytes are the assertion; the parsed one above is what names the keys.
    raw_bytes = manifest_path.read_bytes()
    service.set_params(name, "flange", {})
    assert manifest_path.read_bytes() == raw_bytes

    # The part payload still *answers* the configuration question — an empty
    # map and a null, never a missing key, so a client never has to guess.
    detail = service.get_part(name, "flange")
    assert detail["configs"] == {} and detail["active_config"] is None
    assert detail["status"]["diverged"] is False


def test_ac8_the_full_suite_count_is_cited():
    """**AC8, second half** — "full suite green" is a claim about a *run*, so
    this is the evidence check that a count is on the record in the close-out
    changelog (the PRD-004 AC10 / PRD-008 AC9 / PRD-011 AC8 precedent).

    It stays an evidence check deliberately: recomputing the number would mean
    running the full suite from inside the full suite, and `--collect-only`
    counts *cases*, which is not what `make test` reports.

    The number is required **immediately before the word `passed`** rather than
    anywhere in the file: every changelog entry's own title is a four-digit
    number, so "the file contains a long digit string" is satisfied by an entry
    that cites nothing. The literal placeholder ("N passed, M skipped") is red
    here on purpose, so the close-out cannot forget to fill it in.
    """
    entry = CHANGELOG / "0196-prd-012-docs-and-acceptance.md"
    assert entry.is_file(), "the PRD-012 close-out changelog entry is missing"
    text = entry.read_text(encoding="utf-8")
    assert "make test" in text
    assert re.search(r"\b\d{4,6}\s+passed\b", text.replace(",", "")), \
        "the close-out entry does not cite a `make test` suite count"

    latest = max(CHANGELOG.glob("0[0-9][0-9][0-9]-*.md"))
    if latest != entry:
        recent = latest.read_text(encoding="utf-8")
        assert "make test" in recent and "passed" in recent, (
            f"{latest.name} is the newest changelog entry and cites no suite "
            "count; every entry that lands work must cite one")


# ==================================================================== AC9


def test_ac9_the_ui_surfaces_exist_and_the_session_was_clean():
    """**AC9** — the browser session (switch configurations in the inspector,
    watch the viewport and metrics follow, see the divergence chip appear on a
    manual parameter edit, zero console errors) is in changelog
    `0195-configs-ui.md`, driven against a real headless Chrome.

    A test cannot be a session. What it grades is that the surfaces that
    session drove are still here — the config bar and its switcher, the
    provenance marks, the tree badge, the placement picker, the matrix modal
    and the content-addressed mesh fetch — that the routes they call are still
    mounted, and that the entry still records both counts.
    """
    inspector = (FRONTEND / "js" / "inspector.js").read_text(encoding="utf-8")
    tree = (FRONTEND / "js" / "tree.js").read_text(encoding="utf-8")
    placement = (FRONTEND / "js" / "placement.js").read_text(encoding="utf-8")
    api = (FRONTEND / "js" / "api.js").read_text(encoding="utf-8")
    configs = (FRONTEND / "js" / "configs.js").read_text(encoding="utf-8")
    index = (FRONTEND / "index.html").read_text(encoding="utf-8")
    main = (FRONTEND / "js" / "main.js").read_text(encoding="utf-8")

    # The switcher, the chip and the provenance marks (steps 1–4 of the
    # session).
    assert "renderConfigBar(" in inspector and "markConfigSources(" in inspector
    assert "cfg-chip" in inspector
    assert "setActiveConfig" in inspector and "setActiveConfig" in api
    assert 'id="config-bar"' in index
    # The tree badge and `part@config` instance rows (step 5).
    assert "row-badge" in tree and "active_config" in tree
    # The per-instance binding picker (step 5) and its passthrough.
    assert "setInstanceConfig" in placement and "setInstanceConfig" in api
    # Content-addressed assembly geometry: two sizes of one part on stage.
    assert "getMeshByKey" in api and "getMeshByKey" in main
    assert "mesh_key" in main
    # The matrix modal (step 6).
    assert "buildConfigs" in configs and "configs-modal" in index
    # Fix wave (F2): a per-configuration export or drawing is the **pure**
    # configuration (Decision 3/8), so when the working state is diverged the
    # browser says so instead of letting the file be read as "what is on
    # screen". Both surfaces already have the flag on the payload.
    drawings = (FRONTEND / "js" / "drawings.js").read_text(encoding="utf-8")
    assert "status.diverged" in drawings and "status.diverged" in main

    routes = (REPO / "agentcad" / "server"
              / "routes_configs.py").read_text(encoding="utf-8")
    for path in ("/projects/{proj}/configs",
                 "/projects/{proj}/parts/{part_id}/configs",
                 "/projects/{proj}/parts/{part_id}/active-config",
                 "/projects/{proj}/configs/build",
                 "/projects/{proj}/assembly/instances/{instance_id}/config",
                 "/projects/{proj}/meshes/{key}"):
        assert path in routes, f"the browser's route {path} is gone"

    entry = (CHANGELOG / "0195-configs-ui.md").read_text(encoding="utf-8")
    assert "ERROR COUNT: 0" in entry and "FAILED REQUESTS: 0" in entry


# ====================================================== the record itself


def test_the_roadmap_link_resolves_to_the_prd_where_it_actually_lives():
    """The house meta-test: the roadmap's row for this PRD links to the folder
    the PRD is actually in, and the two move in the same commit."""
    roadmap = (REPO / "docs" / "roadmap.md").read_text(encoding="utf-8")
    row = next(line for line in roadmap.splitlines()
               if line.startswith("| [012]"))
    match = re.search(r"\((prd/[^)]+\.md)\)", row)
    assert match, f"the roadmap row for PRD-012 carries no link: {row}"
    assert (REPO / "docs" / match.group(1)).is_file(), \
        f"the roadmap link {match.group(1)} does not resolve"
    assert (REPO / "docs" / match.group(1)) == PRD, \
        f"the roadmap points at {match.group(1)} but the PRD is at {PRD}"


def test_every_configuration_tool_is_registered_and_documented(tmp_path,
                                                               kernel):
    """A tool an agent cannot discover is a tool that does not exist, and a
    tool the reference does not carry is one no agent will reach for. Both
    halves in one test, because they are one claim."""
    service = make_test_service(tmp_path / "projects", kernel)
    registered = {tool.name for tool in build_registry(service).list()}
    assert set(CONFIG_TOOLS) <= registered

    api = (REPO / "docs" / "agent-api.md").read_text(encoding="utf-8")
    for tool in CONFIG_TOOLS:
        assert tool in api, f"docs/agent-api.md does not document {tool}"


def test_the_documentation_describes_the_shipped_configuration_surface():
    """The docs half of this slice, graded on the things a reader has to be
    able to find: the amended arguments, the two validation rules that differ
    from each other, the divergence state and the content-addressed mesh."""
    api = (REPO / "docs" / "agent-api.md").read_text(encoding="utf-8")
    for needle in ("dim_table", "keep_overrides", "mesh_key", "active_config",
                   "diverged_params", "instance_config"):
        assert needle in api, f"docs/agent-api.md does not cover {needle!r}"
    # The count line is measured, not guessed: `build_registry` over a service
    # without the `[fem]` extra registers 85 tools, and the extra adds three.
    # (Wrapped across a line in the source, so compare on collapsed space.)
    assert "85 tools (88 with the optional" in " ".join(api.split())

    guide = (REPO / "docs" / "user-guide.md").read_text(encoding="utf-8")
    assert "## Configurations" in guide
    for needle in ("— modified", "Matrix", "dimension table"):
        assert needle in guide, f"docs/user-guide.md does not cover {needle!r}"

    architecture = (REPO / "docs"
                    / "architecture.md").read_text(encoding="utf-8")
    assert "## Configurations" in architecture
    for needle in ("_build_with", "_config_status", "_record_for", "mesh_key"):
        assert needle in architecture, \
            f"docs/architecture.md does not cover {needle!r}"

    agents = (REPO / "AGENTS.md").read_text(encoding="utf-8")
    assert "PRD-012" in agents and "_build_with" in agents
    claude = (REPO / "CLAUDE.md").read_text(encoding="utf-8")
    assert "PRD-012" in claude and "_config_status" in claude
