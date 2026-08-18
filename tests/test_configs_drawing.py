"""PRD-012 slice 6 — per-configuration drawings and the dimension table.

Two halves. ``TestConfigDrawings`` is the real one: a three-member flange
family, drawn for real through the tool and the routes, because the whole
claim of the table is that **every number in it was measured from a built
shape** and a mocked kernel would prove the opposite. Below it, three unit
tests drive ``_measure_table``/``_dim_table`` directly with a fake
``build_shape`` — the truncation, the failed row and the column drop are
formatting rules, and paying nine OCCT builds to see one warning would be a
minute of CI for a string.

The template project (script + family, no builds) is made once per class and
cloned per test, so every test writes its own exports directory.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest
from fastapi.testclient import TestClient

from agentcad.core.materials import DEFAULT_MATERIAL
from agentcad.core.tools import build_registry
from agentcad.server.app import create_app

from .conftest import (
    FLANGE_SCRIPT,
    THREE_SIZE_CONFIGS,
    clone_test_service,
    make_test_service,
)


def _svg(service, name: str) -> str:
    return (service.store.exports_dir("demo") / name).read_text(encoding="utf-8")


@pytest.mark.timeout(900)
class TestConfigDrawings:
    """`generate_drawing {config?, dim_table?}` against one real family."""

    @pytest.fixture(scope="class")
    @classmethod
    def drawing_projects(cls, kernel, tmp_path_factory):
        projects = tmp_path_factory.mktemp("configs_drawing_projects")
        svc = make_test_service(projects, kernel)
        svc.create_project("demo")
        svc.store.add_part("demo", "flange", "Flange", DEFAULT_MATERIAL,
                           FLANGE_SCRIPT)
        svc.store.update_part_entry("demo", "flange",
                                    configs=THREE_SIZE_CONFIGS)
        # The same script with NO family: the "unchanged without
        # configurations" control (and a free build — the base cache key is the
        # script's content, so it shares flange's entry).
        svc.store.add_part("demo", "plate", "Plate", DEFAULT_MATERIAL,
                           FLANGE_SCRIPT)
        return projects

    @pytest.fixture
    def stack(self, kernel, tmp_path, drawing_projects):
        service = clone_test_service(drawing_projects, tmp_path / "projects",
                                     kernel)
        return service, build_registry(service)

    # ------------------------------------------------------ config drawings

    def test_a_configuration_drawing_names_its_own_file_and_geometry(
            self, stack):
        """AC2: `<part>_<config>_drawing.svg`, and the sheet is L's geometry —
        pure resolution, so the part's working state never reaches it."""
        service, registry = stack
        result = registry.call("generate_drawing", {
            "project": "demo", "part_id": "flange", "config": "l"})

        assert "error" not in result, result
        assert result["config"] == "l"
        assert result["path"].endswith("flange_l_drawing.svg")
        assert (service.store.exports_dir("demo") /
                "flange_l_drawing.svg").is_file()
        # L's OD is 200 and its bore is 120 — measured off the projection.
        diameters = result["detected"]["diameters_mm"]
        assert any(abs(d - 200.0) < 0.05 for d in diameters), diameters
        assert any(abs(d - 120.0) < 0.05 for d in diameters), diameters
        # ...and the base sheet is a different file with different geometry.
        base = registry.call("generate_drawing", {
            "project": "demo", "part_id": "flange"})
        assert base["path"].endswith("flange_drawing.svg")
        assert "config" not in base
        assert any(abs(d - 140.0) < 0.05 for d in base["detected"]
                   ["diameters_mm"])

    def test_an_undeclared_configuration_is_refused(self, stack):
        _service, registry = stack
        out = registry.call("generate_drawing", {
            "project": "demo", "part_id": "flange", "config": "xl"})
        assert out["error"]["type"] == "validation_error"
        assert "xl" in out["error"]["message"]

    # -------------------------------------------------------- the dim table

    def test_the_dimension_table_measures_every_configuration(self, stack):
        """AC2: one row per member, in family order, with the extents measured
        from that member's own built shape.

        The family is deliberately **ragged** — `xl` overrides only `thick`,
        the three sizes override only the three diameters — because that is
        where echoing the request's override map instead of the build's
        RESOLVED map shows up: every cell outside a member's own overrides
        would print an em dash while the geometry sitting beside it has the
        script's default.
        """
        service, registry = stack
        service.store.update_part_entry("demo", "flange", configs={
            **THREE_SIZE_CONFIGS,
            "xl": {"params": {"thick": 20.0}, "label": "Stock"},
        })
        result = registry.call("generate_drawing", {
            "project": "demo", "part_id": "flange", "dim_table": True})

        assert "error" not in result, result
        table = result["dim_table"]
        assert table == result["detected"]["dim_table"]
        assert table["placement"] == "right-column"
        assert table["warnings"] == [] and table["dropped"] == []
        assert table["columns"] == ["outer_d", "bore_d", "bc_d", "thick"]
        assert [row["config"] for row in table["rows"]] == ["s", "m", "l",
                                                            "xl"]
        assert [row["label"] for row in table["rows"]] == \
            ["Small", "Medium", "Large", "Stock"]
        assert all(row["ok"] for row in table["rows"])
        # X/Y are the OD (the flange is a disc) and Z its thickness — the
        # WORLD bounding box, not a parameter echoed back.
        extents = {row["config"]: row["values"] for row in table["rows"]}
        assert [extents[n]["X"] for n in ("s", "m", "l")] == [100.0, 140.0,
                                                             200.0]
        assert [extents[n]["Y"] for n in ("s", "m", "l")] == [100.0, 140.0,
                                                             200.0]
        assert {extents[n]["Z"] for n in ("s", "m", "l")} == {14.0}
        assert extents["l"]["bore_d"] == 120.0
        # The ragged half: every member reports every parameter the build
        # actually used, overridden or defaulted.
        assert extents["xl"]["thick"] == 20.0 and extents["xl"]["Z"] == 20.0
        assert extents["xl"]["outer_d"] == 140.0     # the script default
        assert all(extents[n]["thick"] == 14.0 for n in ("s", "m", "l"))
        assert extents["s"]["n_bolts"] == 8.0        # not a column: a bonus

        svg = _svg(service, "flange_drawing.svg")
        # The cell names the configuration AND its label: `s` is the identity
        # every other surface uses, and `Small` alone could not be traced back.
        for label, name in (("Small", "s"), ("Medium", "m"), ("Large", "l"),
                            ("Stock", "xl")):
            assert svg.count(f">{label} ({name})<") == 1, label
        for value in ("100.00", "140.00", "200.00", "20.00"):
            assert value in svg
        assert svg.count(">outer_d<") == 1 and svg.count(">config<") == 1
        assert ">—<" not in svg, "a ragged member must print resolved defaults"

    def test_a_label_with_an_ampersand_still_yields_a_parseable_svg(self, stack):
        """A label is author-supplied text; one `&` unescaped and the whole
        sheet stops being XML."""
        service, registry = stack
        service.store.update_part_entry("demo", "flange", configs={
            "s": {"params": {"outer_d": 100.0}, "label": "S & M <small>"},
            "l": {"params": {"outer_d": 200.0}, "label": "L & XL"},
        })
        result = registry.call("generate_drawing", {
            "project": "demo", "part_id": "flange", "dim_table": True})

        assert "error" not in result, result
        svg = _svg(service, "flange_drawing.svg")
        root = ET.fromstring(svg)          # the assertion: it parses at all
        texts = [node.text for node in root.iter()
                 if node.tag.endswith("text")]
        assert "S & M <small> (s)" in texts and "L & XL (l)" in texts
        assert "&amp;" in svg              # escaped in the bytes, not in the DOM

    def test_a_part_without_configurations_draws_the_base_sheet_unchanged(
            self, stack):
        """G5: `dim_table: true` on an unconfigured part is a question, not an
        error — and the bytes are identical to a plain call."""
        service, registry = stack
        plain = registry.call("generate_drawing", {
            "project": "demo", "part_id": "plate"})
        assert "error" not in plain, plain
        before = (service.store.exports_dir("demo") /
                  "plate_drawing.svg").read_bytes()

        asked = registry.call("generate_drawing", {
            "project": "demo", "part_id": "plate", "dim_table": True})

        assert "error" not in asked, asked
        assert "dim_table" not in asked
        assert "dim_table" not in asked["detected"]
        after = (service.store.exports_dir("demo") /
                 "plate_drawing.svg").read_bytes()
        assert after == before

    def test_dxf_ignores_the_dimension_table(self, stack):
        """DXF ignores the table exactly as it ignores PMI (v1). The bytes are
        not comparable — ezdxf stamps a fresh timestamp and GUIDs into every
        document — so the entities are."""
        import ezdxf

        service, registry = stack
        path = service.store.exports_dir("demo") / "flange_drawing.dxf"
        plain = registry.call("generate_drawing", {
            "project": "demo", "part_id": "flange", "format": "dxf"})
        assert "error" not in plain, plain
        before = sorted(e.dxftype() for e in
                        ezdxf.readfile(str(path)).modelspace())

        asked = registry.call("generate_drawing", {
            "project": "demo", "part_id": "flange", "format": "dxf",
            "dim_table": True})

        assert "error" not in asked, asked
        assert asked["path"] == plain["path"]
        assert "dim_table" not in asked and "dim_table" not in asked["detected"]
        assert sorted(e.dxftype() for e in
                      ezdxf.readfile(str(path)).modelspace()) == before

    def test_a_dxf_request_carries_no_table_and_keeps_the_flat_timeout(
            self, stack, monkeypatch):
        """DXF discards the table, so measuring the family for it would buy a
        minute of builds per eight members and throw every one away. The guard
        is on the REQUEST, which is the only place it saves anything."""
        service, registry = stack
        seen: list[dict] = []
        original = service.kernel.request

        def capturing(method, params, timeout_s=None, affinity=None):
            seen.append({"method": method, "params": params,
                         "timeout_s": timeout_s})
            return original(method, params, timeout_s=timeout_s,
                            affinity=affinity)

        monkeypatch.setattr(service.kernel, "request", capturing)

        dxf = registry.call("generate_drawing", {
            "project": "demo", "part_id": "flange", "format": "dxf",
            "dim_table": True})
        assert "error" not in dxf, dxf
        assert "dim_table" not in seen[-1]["params"]
        assert seen[-1]["timeout_s"] == 120.0

        # ...and the SVG path still pays for what it actually builds.
        svg = registry.call("generate_drawing", {
            "project": "demo", "part_id": "flange", "dim_table": True})
        assert "error" not in svg, svg
        assert len(seen[-1]["params"]["dim_table"]["rows"]) == 3
        assert seen[-1]["timeout_s"] == 120.0 + 60.0 * 3

    def test_the_drawing_request_is_pinned_to_the_parts_worker(self, stack,
                                                                monkeypatch):
        """Fix wave (F6): `affinity=part_id` is the house rule everywhere that
        issues repeated builds of one part (`tools_holes` cites an 11 354 ms →
        1 ms measurement for it). `dim_table` turned one drawing request into
        up to eight builds, and the browser preview issues the request twice —
        the POST, then the GET that regenerates — so an unpinned request pays
        for a cold worker every time."""
        service, registry = stack
        seen: list[dict] = []
        original = service.kernel.request

        def capturing(method, params, timeout_s=None, affinity=None):
            seen.append({"method": method, "affinity": affinity})
            return original(method, params, timeout_s=timeout_s,
                            affinity=affinity)

        monkeypatch.setattr(service.kernel, "request", capturing)

        assert "error" not in registry.call("generate_drawing", {
            "project": "demo", "part_id": "flange", "config": "l",
            "dim_table": True})

        drawings = [call for call in seen if call["method"] == "drawing"]
        assert drawings, seen
        assert all(call["affinity"] == "flange" for call in drawings), seen

    # ------------------------------------------------------------ the routes

    def test_the_routes_forward_the_configuration_and_the_table(self, stack):
        service, registry = stack
        app = create_app(service, registry, extra_allowed_hosts={"testserver"})
        http = TestClient(app, base_url="http://127.0.0.1")

        posted = http.post("/api/projects/demo/parts/flange/drawing",
                           json={"config": "l", "dim_table": True})
        assert posted.status_code == 200, posted.text
        body = posted.json()
        assert body["config"] == "l"
        assert body["path"].endswith("flange_l_drawing.svg")
        assert [row["config"] for row in body["dim_table"]["rows"]] == \
            ["s", "m", "l"]

        # The SVG GET reads the SUFFIXED file, so `?config=` serves that
        # configuration's sheet and not whatever the base call last wrote.
        response = http.get("/api/projects/demo/parts/flange/drawing.svg"
                            "?config=l")
        assert response.status_code == 200, response.text
        assert response.headers["content-type"].startswith("image/svg+xml")
        assert response.text == _svg(service, "flange_l_drawing.svg")

        base = http.get("/api/projects/demo/parts/flange/drawing.svg")
        assert base.status_code == 200, base.text
        assert base.text == _svg(service, "flange_drawing.svg")
        assert base.text != response.text

    def test_the_svg_route_takes_dim_table_as_a_query_flag(self, stack):
        """The browser preview asks for the tabulated sheet with a GET, so the
        flag has to survive FastAPI's query parsing (`1`, `true`)."""
        service, registry = stack
        app = create_app(service, registry, extra_allowed_hosts={"testserver"})
        http = TestClient(app, base_url="http://127.0.0.1")

        plain = http.get("/api/projects/demo/parts/flange/drawing.svg")
        assert plain.status_code == 200, plain.text
        assert "Small (s)" not in plain.text

        for flag in ("1", "true"):
            tabulated = http.get(
                f"/api/projects/demo/parts/flange/drawing.svg"
                f"?config=l&dim_table={flag}")
            assert tabulated.status_code == 200, tabulated.text
            assert tabulated.text.count(">Small (s)<") == 1, flag
            assert tabulated.text == _svg(service, "flange_l_drawing.svg")

    def test_the_svg_route_refuses_an_undeclared_configuration(self, stack):
        """Fix wave (V1): the refusal is an HTTP **error**, not a 200 whose
        body happens to be JSON. An endpoint declared to serve SVG answering
        `content-type: application/json` at HTTP 200 is a success the caller
        has to sniff to disbelieve."""
        service, registry = stack
        app = create_app(service, registry, extra_allowed_hosts={"testserver"})
        http = TestClient(app, base_url="http://127.0.0.1")

        response = http.get("/api/projects/demo/parts/flange/drawing.svg"
                            "?config=xl")

        assert response.status_code == 422, response.text
        assert response.json()["error"]["type"] == "ValidationError"
        assert response.json()["error"]["details"]["declared"] == ["l", "m", "s"]

    def test_the_svg_route_validates_the_configuration_name_itself(
            self, stack, monkeypatch):
        """Fix wave (S7, the defence-in-depth half of the refuted C4): the
        route joins `?config=` into a filename it then reads. Today nothing
        leaks — `generate_drawing`'s first statement is `_record_for`, which
        refuses an undeclared name, so the `if "error" in result` branch
        returns before `suffix` is computed — but the route's own safety must
        not depend on a tool three modules away keeping that order. Same
        grammar (`CONFIG_RE`) and the same `fullmatch` as `_KEY_RE`, for the
        same trailing-newline reason."""
        service, registry = stack
        app = create_app(service, registry, extra_allowed_hosts={"testserver"})
        http = TestClient(app, base_url="http://127.0.0.1")
        called: list[str] = []
        original = registry.call
        monkeypatch.setattr(
            registry, "call",
            lambda name, args: called.append(name) or original(name, args))

        for bad in ("../../etc/passwd", "m%0a", "M", "-m", "x" * 33):
            response = http.get("/api/projects/demo/parts/flange/drawing.svg"
                                f"?config={bad}")
            assert response.status_code == 422, (bad, response.text)
            assert "configuration name" in response.json()["error"]["message"]
        # Refused IN THE ROUTE: the tool was never reached, so no path was
        # ever built from the query value.
        assert called == []

        ok = http.get("/api/projects/demo/parts/flange/drawing.svg?config=l")
        assert ok.status_code == 200, ok.text
        assert called == ["generate_drawing"]


# ------------------------------------------- the renderer's formatting rules


def _fake_build_shape(sizes):
    """A `build_shape` stand-in: the bbox is whatever the params say, and a
    size of 0 raises. No kernel, no OCCT — these tests are about strings.

    It returns a RESOLVED parameter map as its second value, exactly as
    `worker.build_shape` does; that map is what the table's cells read.
    """
    class _Box:
        def __init__(self, x, y, z):
            self.size = type("S", (), {"X": x, "Y": y, "Z": z})()

        def bounding_box(self):
            return self

    def build_shape(_script, params):
        size = sizes[params["size"]]
        if size == 0:
            raise ValueError("size 0 is not buildable")
        return _Box(size, size, 2.0), {"size": params["size"]}, []

    return build_shape


def test_the_table_truncates_beyond_eight_rows_with_a_warning():
    from agentcad.kernel.handlers import drawing as handler

    sizes = {n: float(n) for n in range(1, 11)}
    table = {"columns": ["size"],
             "rows": [{"config": f"c{n}", "label": f"C{n}",
                       "params": {"size": n}} for n in range(1, 11)]}

    measured = handler._measure_table(_fake_build_shape(sizes), "", table)

    assert len(measured["rows"]) == 8
    assert [row["config"] for row in measured["rows"]] == \
        [f"c{n}" for n in range(1, 9)]
    assert any("10 configurations" in w and "first 8" in w
               for w in measured["warnings"]), measured["warnings"]


def test_a_member_that_will_not_build_is_one_row_of_em_dashes():
    from agentcad.kernel.handlers import drawing as handler

    sizes = {1: 1.0, 2: 0.0}
    table = {"columns": ["size"],
             "rows": [{"config": "ok", "label": "OK", "params": {"size": 1}},
                      {"config": "bad", "label": "Bad", "params": {"size": 2}}]}

    measured = handler._measure_table(_fake_build_shape(sizes), "", table)
    els, dropped, warnings = handler._dim_table(measured["rows"],
                                                measured["columns"])

    assert [row["ok"] for row in measured["rows"]] == [True, False]
    assert "not buildable" in measured["rows"][1]["error"]
    assert any("'bad' did not build" in w for w in measured["warnings"])
    assert dropped == [] and warnings == []
    # Header + 2 rows, 5 columns each, one <rect> and one <text> per cell.
    assert len(els) == 2 * 5 * 3
    assert sum(el.count(">—<") for el in els) == 4   # size + X + Y + Z


def test_trailing_columns_are_dropped_until_the_table_fits_the_sheet():
    from agentcad.kernel.handlers import drawing as handler

    columns = [f"parameter_number_{n}" for n in range(12)]
    rows = [{"config": "s", "label": "Small", "ok": True,
             "values": {name: 1.0 for name in columns}
             | {"X": 1.0, "Y": 1.0, "Z": 1.0}}]

    els, dropped, warnings = handler._dim_table(rows, columns)

    kept = [name for name in columns if name not in dropped]
    assert dropped and kept, "only the overflow comes off"
    assert kept == columns[:len(kept)], "the TRAILING columns are the ones cut"
    assert dropped[0] == columns[-1], "and the last one goes first"
    assert len(warnings) == len(dropped)
    assert all("was dropped" in w for w in warnings)
    # `config` and the measured extents are never dropped: they are the point.
    assert sum(el.count(">X<") + el.count(">Z<") + el.count(">config<")
               for el in els) == 3


# --------------- PRD-012 follow-up 2: one `_drawing_result` for both routes --

#: A part whose `build` raises above 50 mm — the kernel-class failure both
#: drawing routes have to answer as a 502 with the worker's own error type.
DRAWING_FRAGILE_SCRIPT = '''\
from build123d import *

PARAMS = {
    "thick": {"default": 10.0, "min": 4.0, "max": 60.0, "unit": "mm",
              "description": "plate thickness"},
}

def build(p):
    if p.thick > 50:
        raise ValueError("thickness above 50 mm is not manufacturable")
    return Box(40, 40, p.thick)
'''


@pytest.mark.timeout(900)
class TestDrawingRouteFailureClasses:
    """Two classes of failure, two answers — and the POST and the GET agree.

    Before this, the POST served **every** failure as `200 {"error": …}` (a
    caller had to sniff a success to disbelieve it) and the GET pushed every
    failure through `_RAISE`'s default, which answered `422 ValidationError`
    and renamed a worker crash or timeout a bad request.
    """

    @pytest.fixture(scope="class")
    @classmethod
    def failure_projects(cls, kernel, tmp_path_factory):
        projects = tmp_path_factory.mktemp("drawing_failure_projects")
        svc = make_test_service(projects, kernel)
        svc.create_project("demo")
        svc.store.add_part("demo", "flange", "Flange", DEFAULT_MATERIAL,
                           FLANGE_SCRIPT)
        svc.store.update_part_entry("demo", "flange",
                                    configs=THREE_SIZE_CONFIGS)
        svc.store.add_part("demo", "fragile", "Fragile", DEFAULT_MATERIAL,
                           DRAWING_FRAGILE_SCRIPT)
        svc.store.update_part_entry(
            "demo", "fragile",
            configs={"heavy": {"params": {"thick": 55.0}}},
            active_config="heavy")
        return projects

    @pytest.fixture
    def http(self, kernel, tmp_path, failure_projects):
        service = clone_test_service(failure_projects, tmp_path / "projects",
                                     kernel)
        app = create_app(service, build_registry(service),
                         extra_allowed_hosts={"testserver"})
        return TestClient(app, base_url="http://127.0.0.1")

    # ----------------------------------------- AppError-class: 4xx, type kept

    def test_the_post_refuses_an_unsupported_format(self, http):
        response = http.post("/api/projects/demo/parts/flange/drawing",
                             json={"format": "gif"})
        assert response.status_code == 422, response.text
        assert response.json()["error"]["type"] == "ValidationError"
        assert "svg or dxf" in response.json()["error"]["message"]

    def test_both_routes_404_an_unknown_part(self, http):
        posted = http.post("/api/projects/demo/parts/nosuch/drawing", json={})
        assert posted.status_code == 404, posted.text
        assert posted.json()["error"]["type"] == "NotFoundError"

        got = http.get("/api/projects/demo/parts/nosuch/drawing.svg")
        assert got.status_code == 404, got.text
        assert got.json()["error"]["type"] == "NotFoundError"

    def test_both_routes_422_an_undeclared_configuration(self, http):
        posted = http.post("/api/projects/demo/parts/flange/drawing",
                           json={"config": "xl"})
        assert posted.status_code == 422, posted.text
        assert posted.json()["error"]["type"] == "ValidationError"
        assert posted.json()["error"]["details"]["declared"] == ["l", "m", "s"]

        got = http.get(
            "/api/projects/demo/parts/flange/drawing.svg?config=xl")
        assert got.status_code == 422, got.text
        assert got.json()["error"]["type"] == "ValidationError"

    # ------------------------------- kernel-class: 502, the kernel type kept

    def test_both_routes_502_a_kernel_failure_with_its_own_type(self, http):
        """`_RAISE`'s default would answer 422 `ValidationError` — "your
        request was invalid, do not retry" — for a worker timeout or crash.
        The house answer for a `KernelError` is `app.py`'s 502 with the
        kernel's own type, and it is the same three lines for both verbs."""
        posted = http.post("/api/projects/demo/parts/fragile/drawing", json={})
        assert posted.status_code == 502, posted.text
        error = posted.json()["error"]
        assert error["type"] == "script_error"
        assert "not manufacturable" in error["message"]
        assert error["details"]["traceback"]

        got = http.get("/api/projects/demo/parts/fragile/drawing.svg")
        assert got.status_code == 502, got.text
        assert got.json()["error"] == error

    def test_a_kernel_failure_on_a_named_configuration_is_a_502_too(self, http):
        posted = http.post("/api/projects/demo/parts/fragile/drawing",
                           json={"config": "heavy"})
        assert posted.status_code == 502, posted.text
        assert posted.json()["error"]["type"] == "script_error"

        got = http.get(
            "/api/projects/demo/parts/fragile/drawing.svg?config=heavy")
        assert got.status_code == 502, got.text
        assert got.json()["error"]["type"] == "script_error"

    def test_every_kernel_error_constant_is_covered(self):
        """The set is the protocol's, not a retyped list: there is no
        `"crash"`, and `script_error`/`kernel_error` are in it."""
        from agentcad.kernel.protocol import (ERROR_CONTRACT, ERROR_CRASH,
                                              ERROR_KERNEL, ERROR_SCRIPT,
                                              ERROR_TIMEOUT)
        from agentcad.server.routes_drawing import _KERNEL_TYPES

        assert _KERNEL_TYPES == {ERROR_SCRIPT, ERROR_CONTRACT, ERROR_KERNEL,
                                 ERROR_TIMEOUT, ERROR_CRASH}
        assert "crash" not in _KERNEL_TYPES

    def test_a_success_is_still_a_plain_200(self, http):
        """The split must not disturb the happy path: `generate_drawing`
        returns no `ok` post-state, so every `{"error"}` it yields is one of
        the two classes and nothing else changes shape."""
        posted = http.post("/api/projects/demo/parts/flange/drawing",
                           json={"config": "l"})
        assert posted.status_code == 200, posted.text
        assert posted.json()["path"].endswith("flange_l_drawing.svg")

        got = http.get("/api/projects/demo/parts/flange/drawing.svg?config=l")
        assert got.status_code == 200, got.text
        assert got.headers["content-type"].startswith("image/svg+xml")
