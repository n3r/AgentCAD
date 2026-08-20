"""PRD-011 slice 11 — the package route pack.

Three claims, each tested against its negation:

* **Every package failure is a refusal, never a 200 body.** Unlike a check
  report — which is evidence even when it is red — an unresolvable name, a
  `part_id` that already exists and a bad body have nothing to render, so
  `_BODY_ERRORS` is empty and they arrive as 404/409/422. A test asserts each
  status, and one asserts that no route ever answers 200 with an `error` key.
* **Body keys are whitelisted, never `**body`.** An unknown key is ignored
  rather than forwarded into a registry that would reject the whole call, and
  a `null` reads as "omitted" rather than as an argument.
* **A preview is served out of the index, inside the index.** The path comes
  back to us from a search hit, so it is caller data: it must be a `.png` and
  it must resolve inside the version's own directory. `../` is refused before
  a byte is read.

The gate is deliberately **not** routed: it builds every variant of a package
on the shared kernel pool, and `--work-dir` cannot be widened from a running
server because the seatbelt profile is fixed at worker spawn.
"""

import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agentcad.core.packages import content
from agentcad.core.tools import build_registry
from agentcad.server.app import create_app
from .conftest import make_test_service

REPO = Path(__file__).resolve().parents[1]
CATALOG = REPO / "catalog"
FIXTURES = REPO / "tests" / "fixtures" / "packages"
WIDGET = "widget_good"


# --------------------------------------------------------------- fixtures


@pytest.fixture
def index_dir(tmp_path):
    """A published index holding the green gate fixture, copied in."""
    root = tmp_path / "index"
    (root / WIDGET).mkdir(parents=True)
    shutil.copytree(FIXTURES / WIDGET, root / WIDGET / "1.0.0")
    rel = f"{WIDGET}/1.0.0"
    doc = {
        "format": 1, "name": "agentcad-core", "scope": "public",
        "embeddings": None,
        "packages": {WIDGET: {"versions": {"1.0.0": {
            "content_id": content.content_id(root / rel),
            "path": rel,
            "summary": "A bored, chamfered mounting block",
            "keywords": ["fixture", "block", "mount"],
            "standards": ["ISO 9999"],
            "license": "Apache-2.0", "disclosure": "agent",
            "parts": {"mount_block": {
                "params": [{"name": "length", "type": "number", "min": 24.0,
                            "max": 80.0, "unit": "mm"}],
                "connectors": {"seat": "rigid", "bore": "cylindrical"},
                "specs": ["valid"]}},
            "presets": ["mount_block.short", "mount_block.wide_16"],
            "previews": ["previews/mount_block_iso.png"],
            "gate": {"status": "green", "exempt_skips": [],
                     "agentcad": "0.1.0", "build123d": "0.11.1",
                     "report_id": "sha256:" + "ab" * 32},
            "yanked": False, "signatures": [],
        }}}},
    }
    (root / "index.json").write_text(json.dumps(doc, indent=2) + "\n",
                                     encoding="utf-8")
    return root


@pytest.fixture
def rig(tmp_path, kernel, index_dir, monkeypatch):
    from agentcad import config as user_config

    monkeypatch.setenv("AGENTCAD_PACKAGES_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("AGENTCAD_INDEXES_DIR", str(tmp_path / "indexes"))
    monkeypatch.setenv("AGENTCAD_CONFIG", str(tmp_path / "cfg" / "config.json"))
    user_config.save_config({"indexes": [
        {"name": "agentcad-core", "kind": "local", "path": str(index_dir)}]})
    service = make_test_service(tmp_path / "projects", kernel)
    service.create_project("rig")
    registry = build_registry(service)
    app = create_app(service, registry, extra_allowed_hosts={"testserver"})
    return service, registry, TestClient(app, base_url="http://127.0.0.1")


def _install(client):
    res = client.post("/api/projects/rig/packages", json={"name": WIDGET})
    assert res.status_code == 200, res.text
    return res.json()


# ============================================================== the routes


def test_search_answers_and_says_what_it_did(rig):
    _service, _registry, client = rig
    res = client.get("/api/packages/search", params={"query": "block"})
    assert res.status_code == 200
    body = res.json()
    assert [hit["name"] for hit in body["hits"]] == [WIDGET]
    hit = body["hits"][0]
    assert hit["why"] and hit["index"] == "agentcad-core"
    assert body["semantic"] is False
    assert body["semantic_reason"] == "no_embedding_provider"


def test_search_takes_comma_separated_facets(rig):
    _service, _registry, client = rig
    hit = client.get("/api/packages/search",
                     params={"standards": "ISO 9999"}).json()
    assert [h["name"] for h in hit["hits"]] == [WIDGET]
    miss = client.get("/api/packages/search",
                      params={"standards": "ISO 9999,ISO 4762"}).json()
    assert miss["hits"] == [], "standards are an AND filter"


def test_search_with_no_query_lists_everything(rig):
    _service, _registry, client = rig
    body = client.get("/api/packages/search").json()
    assert [hit["name"] for hit in body["hits"]] == [WIDGET]


def test_install_list_use_and_remove(rig):
    service, _registry, client = rig
    added = _install(client)
    assert added["lock"]["version"] == "1.0.0"

    listed = client.get("/api/projects/rig/packages")
    assert listed.status_code == 200
    body = listed.json()
    assert body["packages"][WIDGET]["cache"] == "ok"
    assert [index["name"] for index in body["indexes"]] == ["agentcad-core"]

    used = client.post(f"/api/projects/rig/packages/{WIDGET}/use",
                       json={"part": "mount_block", "part_id": "block",
                             "preset": "short"})
    assert used.status_code == 200, used.text
    payload = used.json()
    assert payload["package_provenance"]["status"] == "ok"
    assert payload["params"]["length"] == 24.0

    removed = client.delete(f"/api/projects/rig/packages/{WIDGET}")
    assert removed.status_code == 200
    assert removed.json()["materialized_parts"] == ["block"]
    # FR6: the part is still a project file and it still builds.
    assert service.get_part("rig", "block")["metrics"]["volume_mm3"] > 0


def test_a_null_body_value_reads_as_omitted(rig):
    _service, _registry, client = rig
    res = client.post("/api/projects/rig/packages",
                      json={"name": WIDGET, "version_req": None,
                            "index": None})
    assert res.status_code == 200, res.text


def test_an_unknown_body_key_is_not_forwarded(rig):
    """Never `**body`: the registry rejects unknown arguments, so forwarding
    one would turn a stray key into a 422 for an otherwise valid call."""
    _service, _registry, client = rig
    res = client.post("/api/projects/rig/packages",
                      json={"name": WIDGET, "nonsense": "x"})
    assert res.status_code == 200, res.text


def test_a_body_that_is_not_an_object_is_a_422(rig):
    _service, _registry, client = rig
    res = client.post("/api/projects/rig/packages", json=["not", "an", "object"])
    assert res.status_code == 422


# ================================================ every failure is a refusal


def test_an_unresolvable_package_is_a_404(rig):
    _service, _registry, client = rig
    res = client.post("/api/projects/rig/packages", json={"name": "nope"})
    assert res.status_code == 404
    assert "nope" in res.json()["error"]["message"]


def test_an_unknown_project_is_a_404(rig):
    _service, _registry, client = rig
    res = client.get("/api/projects/ghost/packages")
    assert res.status_code == 404


def test_a_part_id_that_already_exists_is_a_409(rig):
    service, _registry, client = rig
    _install(client)
    service.create_part("rig", "taken", script=(
        "PARAMS = {}\n\n\ndef build(p):\n"
        "    from build123d import Box\n    return Box(1, 1, 1)\n"))
    res = client.post(f"/api/projects/rig/packages/{WIDGET}/use",
                      json={"part": "mount_block", "part_id": "taken"})
    assert res.status_code == 409


def test_a_missing_preset_is_a_404(rig):
    _service, _registry, client = rig
    _install(client)
    res = client.post(f"/api/projects/rig/packages/{WIDGET}/use",
                      json={"part": "mount_block", "part_id": "b",
                            "preset": "no_such_preset"})
    assert res.status_code == 404


def test_using_a_package_that_was_never_added_is_refused(rig):
    """Fail-closed: guessing a version there is inventing a dependency."""
    _service, _registry, client = rig
    res = client.post(f"/api/projects/rig/packages/{WIDGET}/use",
                      json={"part": "mount_block", "part_id": "b"})
    assert res.status_code in (404, 422)


def test_removing_a_package_that_is_not_installed_is_a_404(rig):
    _service, _registry, client = rig
    res = client.delete("/api/projects/rig/packages/nope")
    assert res.status_code == 404


def test_no_route_ever_answers_200_with_an_error_body(rig):
    """`_BODY_ERRORS` is empty and this is what says so: every failure shape
    the pack can produce arrives as a status, never as a 200 nobody
    inspects."""
    _service, _registry, client = rig
    calls = [
        client.post("/api/projects/rig/packages", json={"name": "nope"}),
        client.get("/api/projects/ghost/packages"),
        client.delete("/api/projects/rig/packages/nope"),
        client.post(f"/api/projects/rig/packages/{WIDGET}/use",
                    json={"part": "mount_block", "part_id": "b"}),
    ]
    for res in calls:
        assert res.status_code != 200, res.text


# ==================================================== previews are contained


def test_a_preview_is_served_out_of_the_index(rig):
    _service, _registry, client = rig
    res = client.get(f"/api/packages/{WIDGET}/versions/1.0.0/preview",
                     params={"path": "previews/mount_block_iso.png"})
    assert res.status_code == 200
    assert res.headers["content-type"] == "image/png"
    assert res.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_a_preview_is_served_before_the_package_is_installed(rig, tmp_path):
    """There is no copy in the cache yet — the listing has to render anyway."""
    _service, _registry, client = rig
    assert not (tmp_path / "cache").exists()
    res = client.get(f"/api/packages/{WIDGET}/versions/1.0.0/preview",
                     params={"path": "previews/mount_block_iso.png"})
    assert res.status_code == 200


@pytest.mark.parametrize("path", [
    "../../../etc/passwd",
    "previews/../../../etc/passwd",
    "/etc/passwd",
    "previews/../package.json",
])
def test_a_preview_path_that_escapes_the_package_is_refused(rig, path):
    _service, _registry, client = rig
    res = client.get(f"/api/packages/{WIDGET}/versions/1.0.0/preview",
                     params={"path": path})
    assert res.status_code in (404, 422), res.text
    assert b"root:" not in res.content


def test_a_preview_must_be_a_png(rig):
    _service, _registry, client = rig
    res = client.get(f"/api/packages/{WIDGET}/versions/1.0.0/preview",
                     params={"path": "package.json"})
    assert res.status_code == 422


def test_a_preview_for_an_unknown_version_is_a_404(rig):
    _service, _registry, client = rig
    res = client.get(f"/api/packages/{WIDGET}/versions/9.9.9/preview",
                     params={"path": "previews/mount_block_iso.png"})
    assert res.status_code == 404


def test_a_preview_from_a_pinned_index_that_does_not_carry_it_is_a_404(rig):
    _service, _registry, client = rig
    res = client.get(f"/api/packages/{WIDGET}/versions/1.0.0/preview",
                     params={"path": "previews/mount_block_iso.png",
                             "index": "no-such-index"})
    assert res.status_code == 404


# ====================================================== the gate is not routed


def test_the_gate_is_not_reachable_over_http(rig):
    """`validate_package` and `publish` are CLI-and-tool surfaces. The routes
    that exist are the five the design spec lists plus the preview — and, from
    PRD-005a slice 7, the four **read-only** `/api/public/packages…` routes,
    which are a separate scope-filtered pack (`server/routes_public.py`) and
    carry no write verb either. PRD-031a adds five more anonymous GET routes:
    `search`, `script`, `params` (still kernel-free, in `routes_public.py`) and
    the two customizer routes `variant`/`download` plus the kernel-free `mesh`
    read (all in `routes_market.py`, whose `variant`/`download` are the ONE
    kernel-reaching market surface) — every one a GET, none a publish/write
    verb. A new *write* package route landing here is what this test notices."""
    _service, _registry, client = rig
    package_routes = {path for path in client.app.openapi()["paths"]
                      if "package" in path}
    assert package_routes == {
        "/api/packages/search",
        "/api/projects/{proj}/packages",
        "/api/projects/{proj}/packages/{name}",
        "/api/projects/{proj}/packages/{name}/use",
        "/api/packages/{name}/versions/{version}/preview",
        "/api/public/packages",
        "/api/public/packages/{name}",
        "/api/public/packages/{name}/versions/{version}",
        "/api/public/packages/{name}/versions/{version}/preview",
        "/api/public/packages/search",
        "/api/public/packages/{name}/versions/{version}/script/{part}",
        "/api/public/packages/{name}/versions/{version}/params/{part}",
        "/api/public/packages/{name}/versions/{version}/parts/{part}/variant",
        "/api/public/packages/{name}/versions/{version}/parts/{part}/download/{fmt}",
        "/api/public/packages/{name}/versions/{version}/parts/{part}/mesh/{key}",
    }
    for path, operations in client.app.openapi()["paths"].items():
        if path.startswith("/api/public/"):
            assert set(operations) == {"get"}, (path, operations)


# ============================================ the dialog, statically checked


def test_the_library_dialog_carries_the_security_non_claim_as_visible_text():
    """Decision 11, place 8. Visible text, not a `title=` tooltip — a claim
    nobody can read is a claim nobody made."""
    html = (REPO / "frontend" / "index.html").read_text(encoding="utf-8")
    assert 'id="library-modal"' in html
    block = html.split('id="library-modal"', 1)[1].split("</footer>", 1)[0]
    footer = " ".join(block.split())
    assert "not a security boundary" in footer
    assert "your privileges" in footer
    assert "docs/packages.md" in footer
    assert "title=" not in block.split('class="lib-nonclaim"', 1)[1]


def test_the_library_module_is_wired_into_the_boot_sequence():
    main = (REPO / "frontend" / "js" / "main.js").read_text(encoding="utf-8")
    assert 'import * as library from "./library.js";' in main
    # PRD-026 renamed the panel DI object `actions` -> `panelApi`; the name
    # `actions` now belongs to the shell's action registry.
    assert "library.init(panelApi);" in main
    assert "setupLibrary();" in main


def test_the_library_module_scopes_every_search_response_to_its_request():
    """PRD-009's rule: typing outruns a search, and an out-of-order answer
    would render the wrong result set."""
    src = (REPO / "frontend" / "js" / "library.js").read_text(encoding="utf-8")
    assert "++searchToken" in src
    assert "token !== searchToken" in src


def test_the_library_module_installs_before_it_materialises():
    """`add_package` then `use_part`, in that order and never one call: a
    project can depend on a package it has not materialised."""
    src = (REPO / "frontend" / "js" / "library.js").read_text(encoding="utf-8")
    assert src.index("api.addPackage(") < src.index("api.usePackagePart(")


def test_the_catalog_is_searchable_through_the_route(tmp_path, kernel,
                                                     monkeypatch):
    """End to end against the bundled catalog rather than the fixture, so the
    dialog's default empty-query listing is known to show real content."""
    from agentcad.cli import bundled_index_entries

    monkeypatch.setenv("AGENTCAD_PACKAGES_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("AGENTCAD_CONFIG", str(tmp_path / "cfg" / "config.json"))
    service = make_test_service(tmp_path / "projects", kernel)
    service.bundled_indexes = bundled_index_entries()
    service.create_project("rig")
    registry = build_registry(service)
    app = create_app(service, registry, extra_allowed_hosts={"testserver"})
    client = TestClient(app, base_url="http://127.0.0.1")

    body = client.get("/api/packages/search",
                      params={"query": "cap screw"}).json()
    hit = next(h for h in body["hits"] if h["name"] == "iso4762")
    assert "cap_screw.m5x16" in hit["presets"]
    preview = hit["previews"][0]
    res = client.get(f"/api/packages/iso4762/versions/{hit['version']}/preview",
                     params={"path": preview})
    assert res.status_code == 200
    assert res.content[:8] == b"\x89PNG\r\n\x1a\n"


@pytest.mark.parametrize("path", ["\x00.png", "previews/\x00x.png",
                                  "previews/a\x1fb.png"])
def test_a_control_character_in_a_preview_path_is_a_refusal_not_a_500(rig,
                                                                      path):
    """**m9, review changelog 0181.** A NUL escapes nothing, so
    `resolve_within` accepted it lexically and `Path.resolve()` then raised
    `ValueError: embedded null character` — an exception no caller catches,
    because every caller catches `ValidationError`. The result was an
    **unauthenticated 500** on this route. A path inside a package is a file
    name, so the whole C0 range is refused."""
    _service, _registry, client = rig
    res = client.get(f"/api/packages/{WIDGET}/versions/1.0.0/preview",
                     params={"path": path})
    assert res.status_code in (404, 422), res.text
    assert res.status_code != 500
    assert "control character" in res.text or "Internal" not in res.text
