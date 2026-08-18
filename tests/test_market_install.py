"""PRD-031a slice 3: add-to-library via ``market_install`` (PRD-011 verbatim).

A marketplace is a registry index + a web front, so installing from it is
``add_package`` + ``use_part`` — the existing authenticated path. ``market_install``
is the one-call agent convenience, **scoped to the seeded public catalog**: a
package that lives only in a private index is refused before anything installs.

Runs on a **copy** of the bundled catalog's own package (a genuinely valid tree),
so AC5's lockfile-pin + byte-identical rebuild is inherited from PRD-011, not
re-implemented.
"""

from __future__ import annotations

import json
import shutil

import pytest

from agentcad import config as user_config
from agentcad._resources import resource_root
from agentcad.core.tools import build_registry
from agentcad.server import security

from .conftest import make_test_service

PACKAGE = "extrusion_2020"
PART = "extrusion"
PRIVATE_PKG = "acme-secret"


@pytest.fixture
def catalog_rig(tmp_path, kernel, monkeypatch):
    """A service + registry with the bundled PUBLIC catalog **and** a private
    index carrying its own package. On a copy: the cache and config dirs are
    redirected under the scratch ``tmp_path``."""
    from agentcad.cli import bundled_index_entries

    monkeypatch.setenv("AGENTCAD_PACKAGES_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("AGENTCAD_CONFIG", str(tmp_path / "cfg" / "config.json"))

    # A private index, copied from the bundled din625 tree so it is genuinely
    # valid — the same shape `conftest.configure_private_index` builds.
    catalog = resource_root() / "catalog"
    doc = json.loads((catalog / "index.json").read_text(encoding="utf-8"))
    entry = dict(doc["packages"]["din625"]["versions"]["1.0.0"])
    entry["path"] = f"{PRIVATE_PKG}/1.0.0"
    priv_root = tmp_path / "private-index"
    shutil.copytree(catalog / "din625" / "1.0.0", priv_root / PRIVATE_PKG / "1.0.0")
    priv_doc = {"format": 1, "name": "acme", "scope": "private",
                "packages": {PRIVATE_PKG: {"versions": {"1.0.0": entry}}}}
    (priv_root / "index.json").write_text(json.dumps(priv_doc), encoding="utf-8")
    user_config.save_config({"indexes": [
        {"name": "acme", "kind": "local", "path": str(priv_root),
         "scope": "private"}]})

    service = make_test_service(tmp_path / "projects", kernel)
    registry = build_registry(service)            # installs service.packages
    service.bundled_indexes = bundled_index_entries()
    service.packages.reload_indexes()
    service.create_project("proj")
    # The private index is first (user config), the public catalog second.
    assert [ix.name for ix in service.packages.indexes] == ["acme", "agentcad-core"]
    return service, registry


# ------------------------------------------------------------ AC5 the pin

def test_ac5_market_install_pins_the_version(catalog_rig):
    service, registry = catalog_rig
    result = registry.call("market_install", {
        "project": "proj", "package": PACKAGE, "part": PART, "part_id": "beam"})
    assert "error" not in result, result
    assert result["index"] == "agentcad-core"

    manifest = service.store.manifest("proj")
    assert PACKAGE in (manifest.get("packages") or {})
    lock = (manifest.get("packages_lock") or {})[PACKAGE]
    assert lock["version"] == "1.0.0"
    assert lock["content_id"].startswith("sha256:")
    # The lock entry is what market_install returns (AC6's "returns the lock").
    assert result["lock"]["content_id"] == lock["content_id"]


def test_ac5_the_lock_matches_the_index_content_id(catalog_rig):
    service, registry = catalog_rig
    registry.call("market_install", {
        "project": "proj", "package": PACKAGE, "part": PART, "part_id": "beam"})
    doc = json.loads(
        (resource_root() / "catalog" / "index.json").read_text(encoding="utf-8"))
    declared = doc["packages"][PACKAGE]["versions"]["1.0.0"]["content_id"]
    lock = service.store.manifest("proj")["packages_lock"][PACKAGE]
    assert lock["content_id"] == declared


def test_ac5_the_materialised_part_rebuilds_byte_identically(catalog_rig):
    """PRD-011 AC3 inherited: the copied script carries an immutable provenance
    header with no timestamp/path, so re-materialising is byte-identical."""
    service, registry = catalog_rig
    registry.call("market_install", {
        "project": "proj", "package": PACKAGE, "part": PART, "part_id": "beam"})
    first = service.store.read_script("proj", "beam")
    # Re-materialise into a second id and compare the bodies (byte-identical
    # modulo the part id, which the header does not carry).
    registry.call("market_install", {
        "project": "proj", "package": PACKAGE, "part": PART, "part_id": "beam2"})
    second = service.store.read_script("proj", "beam2")
    assert first == second


def test_the_provenance_header_names_the_package(catalog_rig):
    service, registry = catalog_rig
    registry.call("market_install", {
        "project": "proj", "package": PACKAGE, "part": PART, "part_id": "beam"})
    script = service.store.read_script("proj", "beam")
    assert PACKAGE in script                      # the PRD-011 provenance header


# ---------------------------------------------------- AC6 seeded-catalog scope

def test_ac6_a_private_only_package_is_refused(catalog_rig):
    """The private index carries ``acme-secret``; ``market_install`` refuses it
    before installing anything — its scope is the seeded public catalog only."""
    service, registry = catalog_rig
    result = registry.call("market_install", {
        "project": "proj", "package": PRIVATE_PKG, "part": "ball_bearing",
        "part_id": "brg"})
    assert "error" in result, result
    assert result["error"]["type"] == "notfound_error"
    # Nothing installed: the manifest has no packages at all.
    manifest = service.store.manifest("proj")
    assert not (manifest.get("packages") or {})
    assert "brg" not in {p["id"] for p in manifest.get("parts", [])}


def test_ac6_a_nonexistent_package_is_refused(catalog_rig):
    _service, registry = catalog_rig
    result = registry.call("market_install", {
        "project": "proj", "package": "no-such-thing", "part": "x",
        "part_id": "y"})
    assert result.get("error", {}).get("type") == "notfound_error"


# ---------------------------------------------------- the browser path is private

def test_the_add_to_library_routes_require_a_session():
    """The browser 'Add to library' reuses the existing package routes; they are
    NOT on the anonymous surface (a logged-out request is 401)."""
    assert security.is_public("/api/projects/proj/packages") is False
    assert security.is_public("/api/projects/proj/packages/extrusion_2020/use") \
        is False


def test_market_install_is_registered(catalog_rig):
    _service, registry = catalog_rig
    assert registry.get("market_install") is not None
    # It loads BEFORE tools_packages, but reads service.packages inside the
    # function — so the registry still built cleanly with add_package present.
    assert registry.get("add_package") is not None
