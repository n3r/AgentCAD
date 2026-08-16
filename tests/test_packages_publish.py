"""PRD-011 slice 8 — publishing: the gate as the gate, immutability, yank.

`agentcad package validate` is report-honest; `agentcad publish` is
**fail-closed**. Three claims carry this slice and each is tested against its
negation:

* **The gate is the gate.** A report that is not `publishable` publishes
  nothing, and "nothing" is asserted by hashing the whole index tree before
  and after. Publish always runs **every** stage, so `skip / not_selected` can
  never reach the verdict.
* **A version is immutable** (FR10, AC5). Republishing an existing
  `name@version` is a `conflict_error` naming it, **even when the content id
  is identical** — a byte comparison would let a publisher redefine
  "identical" later.
* **Yank deletes nothing.** A lockfile naming a yanked version keeps
  resolving, a fresh `^1.0.0` never selects it, and an explicitly-named yanked
  version warns and proceeds.

Plus the vendor gate — the mechanism behind FR13's confinement — and the
window nobody else closes: a package whose content id changed **between the
gate and the publish**.
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from agentcad.core.model import ConflictError, NotFoundError, ValidationError
from agentcad.core.packages import (cache, content, format as pkgformat, gate,
                                    indexes, lockfile)
from agentcad.core.packages.manager import PackageManager
from agentcad.core.tools import build_registry
from .conftest import make_test_service

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "tests" / "fixtures" / "packages"
WIDGET = "widget_good"


# --------------------------------------------------------------- fixtures


@pytest.fixture
def cache_root(tmp_path, monkeypatch):
    root = tmp_path / "cache"
    monkeypatch.setenv("AGENTCAD_PACKAGES_DIR", str(root))
    monkeypatch.setenv("AGENTCAD_CONFIG", str(tmp_path / "cfg" / "config.json"))
    return root


@pytest.fixture
def empty_index(tmp_path):
    """A published index with nothing in it yet — what `agentcad publish`
    writes into."""
    root = tmp_path / "catalog"
    root.mkdir()
    (root / "index.json").write_text(json.dumps(
        {"format": 1, "name": "agentcad-core", "scope": "public",
         "packages": {}, "embeddings": None}, indent=2))
    return root


@pytest.fixture
def service(tmp_path, kernel, cache_root):
    svc = make_test_service(tmp_path / "projects", kernel)
    svc.create_project("rig")
    return svc


@pytest.fixture
def source(tmp_path):
    """A writable copy of the green fixture (publish never mutates it, but a
    test that edits the package must not edit the repo's fixture)."""
    dest = tmp_path / "src" / WIDGET
    shutil.copytree(FIXTURES / WIDGET, dest)
    return dest


def tree_hash(root: Path) -> str:
    """Every file under `root`, path and bytes — what "wrote nothing" means."""
    digest = hashlib.sha256()
    for path in sorted(p for p in Path(root).rglob("*") if p.is_file()):
        digest.update(str(path.relative_to(root)).encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
    return digest.hexdigest()


def local(root, name="agentcad-core", scope=None):
    return indexes.LocalIndex(name, root, scope=scope)


def publish(service, index, path, **kw):
    return indexes.publish(index, path, service, **kw)


def read_entry(root, name=WIDGET, version="1.0.0"):
    doc = json.loads((Path(root) / "index.json").read_text())
    return doc["packages"][name]["versions"][version]


def set_vendor(root, redistributable):
    doc = json.loads((root / "package.json").read_text())
    doc["provenance"]["vendor"] = {"name": "McMaster-Carr",
                                   "redistributable": redistributable}
    (root / "package.json").write_text(json.dumps(doc, indent=2) + "\n")


# ================================================== the happy path, end to end


def test_publish_then_search_then_add_then_use(service, empty_index, source,
                                               tmp_path, monkeypatch):
    """The loop the registry exists for, with no network anywhere in it."""
    from agentcad import config as user_config

    result = publish(service, local(empty_index), source,
                     work_dir=str(tmp_path / "work"))
    assert result["published"] == f"{WIDGET}@1.0.0"
    assert (empty_index / WIDGET / "1.0.0" / "package.json").is_file()

    user_config.save_config({"indexes": [
        {"name": "agentcad-core", "kind": "local", "path": str(empty_index)}]})
    registry = build_registry(service)
    hits = registry.call("search_packages", {"query": WIDGET})["hits"]
    assert [hit["name"] for hit in hits] == [WIDGET]
    registry.call("add_package", {"project": "rig", "name": WIDGET})
    detail = registry.call("use_part", {
        "project": "rig", "package": WIDGET, "part": "mount_block",
        "part_id": "block"})
    assert detail["status"]["state"] == "ok"


def test_the_published_entry_validates_as_an_index_document(service,
                                                            empty_index,
                                                            source, tmp_path):
    publish(service, local(empty_index), source, work_dir=str(tmp_path / "w"))
    doc = json.loads((empty_index / "index.json").read_text())
    assert pkgformat.validate_index(doc) == []


def test_the_parts_digest_comes_from_the_gates_own_measurements(
        service, empty_index, source, tmp_path):
    """Never hand-written: the params are the inspected PARAMS spec, the
    connectors are what the kernel reported, the specs are what ran."""
    publish(service, local(empty_index), source, work_dir=str(tmp_path / "w"))
    digest = read_entry(empty_index)["parts"]["mount_block"]
    assert {spec["name"] for spec in digest["params"]} == {
        "length", "bore_d", "grade", "chamfered"}
    length = next(s for s in digest["params"] if s["name"] == "length")
    assert (length["min"], length["max"], length["unit"]) == (24.0, 80.0, "mm")
    assert digest["connectors"] == {"seat": "rigid", "bore": "cylindrical"}
    assert set(digest["specs"]) == {"valid", "not_hollowed_out"}


def test_the_entry_records_the_presets_previews_and_what_was_not_measured(
        service, empty_index, source, tmp_path):
    publish(service, local(empty_index), source, work_dir=str(tmp_path / "w"))
    entry = read_entry(empty_index)
    assert entry["presets"] == ["mount_block.short", "mount_block.wide_16"]
    assert entry["previews"] == ["previews/mount_block_iso.png"]
    assert entry["gate"]["status"] == "green"
    # What stops "validated" from becoming a badge — and it is ONE shape.
    assert entry["gate"]["exempt_skips"] == ["policy:no_policy_configured"]
    assert all(skip.count(":") == 1 for skip in entry["gate"]["exempt_skips"])
    assert entry["gate"]["report_id"].startswith("sha256:")
    assert entry["yanked"] is False and entry["signatures"] == []


def test_the_published_content_id_is_the_one_a_consumer_recomputes(
        service, empty_index, source, tmp_path):
    publish(service, local(empty_index), source, work_dir=str(tmp_path / "w"))
    entry = read_entry(empty_index)
    assert entry["content_id"] == content.content_id(
        empty_index / entry["path"])
    assert entry["content_id"] == content.content_id(source)


# ==================================================== the gate is the gate


def test_a_red_gate_blocks_publish_and_writes_nothing(service, empty_index,
                                                      tmp_path):
    """AC2's fixture, from the publisher's side. 'Nothing' is the whole index
    tree hashed before and after."""
    before = tree_hash(empty_index)
    with pytest.raises(ValidationError) as exc:
        publish(service, local(empty_index), FIXTURES / "break_at_extreme",
                work_dir=str(tmp_path / "w"))
    assert "build:strut@length=max" in json.dumps(exc.value.details)
    assert exc.value.details["checks"], "the failing rows must travel"
    assert tree_hash(empty_index) == before


def test_a_subset_report_can_never_publish(service, empty_index, source,
                                           tmp_path):
    """`publish` runs every stage precisely so `skip / not_selected` cannot
    reach the verdict; handed one anyway, `LocalIndex.publish` refuses."""
    report = gate.PackageGate(service).run(source, stages=("format",),
                                           work_dir=str(tmp_path / "w"))
    assert report["publishable"] is False
    before = tree_hash(empty_index)
    with pytest.raises(ValidationError):
        local(empty_index).publish(source, report)
    assert tree_hash(empty_index) == before


def test_a_package_with_no_presets_and_no_specs_still_publishes(
        service, empty_index, source, tmp_path):
    """The stage-level exemptions are legitimate absences, not blind spots —
    so publish agrees with the gate rather than working around it."""
    (source / "presets.json").unlink()
    script = (source / "parts" / "mount_block.py").read_text()
    script = script.replace(
        "from agentcad.toolkit.specs import check_valid, check_volume\n", "")
    script = script.replace(
        "SPECS = [\n    check_valid(requirement=\"PKG-001\"),\n"
        "    check_volume(min_mm3=1000.0, name=\"not_hollowed_out\",\n"
        "                 requirement=\"PKG-002\"),\n]\n", "")
    (source / "parts" / "mount_block.py").write_text(script)
    result = publish(service, local(empty_index), source,
                     work_dir=str(tmp_path / "w"))
    assert result["published"] == f"{WIDGET}@1.0.0"
    exempt = read_entry(empty_index)["gate"]["exempt_skips"]
    assert "presets:no_presets_declared" in exempt
    assert "specs:not_declared" in exempt


def test_a_package_that_changed_between_the_gate_and_the_publish_is_refused(
        service, empty_index, source, tmp_path):
    """The window the gate's own moving-target check cannot see: the report is
    finished, and then the tree moves. Publishing it would attest a content id
    nobody measured."""
    report = gate.PackageGate(service).run(source, work_dir=str(tmp_path / "w"))
    assert report["publishable"] is True
    (source / "docs" / "README.md").write_text(
        (source / "docs" / "README.md").read_text() + "\nand one more line.\n")
    before = tree_hash(empty_index)
    with pytest.raises(ValidationError) as exc:
        local(empty_index).publish(source, report)
    assert "changed" in str(exc.value)
    assert report["package"]["content_id"] in str(exc.value)
    assert tree_hash(empty_index) == before


def test_publish_refuses_a_report_that_is_not_a_gate_report(empty_index,
                                                            source):
    with pytest.raises(ValidationError):
        local(empty_index).publish(source, {"status": "green"})


def test_publish_refuses_when_the_gate_could_not_name_build123d(
        service, empty_index, source, tmp_path):
    """Fail-closed: the index entry declares what the package was PROVED
    against, and an entry that cannot say is not an entry."""
    report = gate.PackageGate(service).run(source, work_dir=str(tmp_path / "w"))
    report["host"]["build123d"] = None
    with pytest.raises(ValidationError) as exc:
        local(empty_index).publish(source, report)
    assert "build123d" in str(exc.value)


# ============================================================ immutability


def test_republishing_a_version_is_a_conflict_even_byte_for_byte(
        service, empty_index, source, tmp_path):
    """AC5. A byte comparison would let a publisher redefine 'identical'
    later, so identity is not a defence."""
    publish(service, local(empty_index), source, work_dir=str(tmp_path / "w1"))
    after = tree_hash(empty_index)
    with pytest.raises(ConflictError) as exc:
        publish(service, local(empty_index), source,
                work_dir=str(tmp_path / "w2"))
    assert f"{WIDGET}@1.0.0" in str(exc.value)
    assert tree_hash(empty_index) == after


def test_a_stray_directory_with_no_index_entry_is_also_a_conflict(
        service, empty_index, source, tmp_path):
    """A half-published tree. Overwriting it would silently redefine a version
    whose bytes somebody may already hold."""
    (empty_index / WIDGET / "1.0.0").mkdir(parents=True)
    (empty_index / WIDGET / "1.0.0" / "stale.txt").write_text("older\n")
    with pytest.raises(ConflictError) as exc:
        publish(service, local(empty_index), source,
                work_dir=str(tmp_path / "w"))
    assert str(Path(WIDGET) / "1.0.0") in str(exc.value)


def test_a_second_version_publishes_beside_the_first(service, empty_index,
                                                     source, tmp_path):
    publish(service, local(empty_index), source, work_dir=str(tmp_path / "w1"))
    doc = json.loads((source / "package.json").read_text())
    doc["version"] = "1.1.0"
    (source / "package.json").write_text(json.dumps(doc, indent=2) + "\n")
    publish(service, local(empty_index), source, work_dir=str(tmp_path / "w2"))
    versions = json.loads(
        (empty_index / "index.json").read_text())["packages"][WIDGET]["versions"]
    assert sorted(versions) == ["1.0.0", "1.1.0"]


# =================================================================== yank


def test_yank_flips_the_flag_and_deletes_nothing(service, empty_index, source,
                                                 tmp_path):
    publish(service, local(empty_index), source, work_dir=str(tmp_path / "w"))
    files = tree_hash(empty_index / WIDGET)
    index = local(empty_index)
    assert index.yank(WIDGET, "1.0.0")["yanked"] is True
    assert read_entry(empty_index)["yanked"] is True
    assert tree_hash(empty_index / WIDGET) == files


def test_yank_is_idempotent_and_unyank_is_possible(service, empty_index,
                                                   source, tmp_path):
    publish(service, local(empty_index), source, work_dir=str(tmp_path / "w"))
    index = local(empty_index)
    index.yank(WIDGET, "1.0.0")
    assert index.yank(WIDGET, "1.0.0")["already"] is True
    index.yank(WIDGET, "1.0.0", yanked=False)
    assert read_entry(empty_index)["yanked"] is False


def test_yanking_something_that_is_not_published_is_not_found(empty_index):
    with pytest.raises(NotFoundError):
        local(empty_index).yank(WIDGET, "1.0.0")


def test_a_fresh_requirement_skips_a_yanked_version(service, empty_index,
                                                    source, tmp_path):
    """AC5's other half."""
    publish(service, local(empty_index), source, work_dir=str(tmp_path / "w"))
    local(empty_index).yank(WIDGET, "1.0.0")
    manager = PackageManager(service, indexes=[local(empty_index)])
    with pytest.raises(NotFoundError) as exc:
        manager.resolve(WIDGET, "^1.0.0")
    assert "yanked" in str(exc.value)


def test_an_explicitly_named_yanked_version_warns_and_proceeds(
        service, empty_index, source, tmp_path):
    publish(service, local(empty_index), source, work_dir=str(tmp_path / "w"))
    local(empty_index).yank(WIDGET, "1.0.0")
    manager = PackageManager(service, indexes=[local(empty_index)])
    resolution = manager.resolve(WIDGET, "1.0.0")
    assert resolution["version"] == "1.0.0" and resolution["yanked"] is True
    added = manager.add("rig", WIDGET, "1.0.0")
    assert any("YANKED" in warning for warning in added["warnings"])


def test_a_lock_entry_naming_a_yanked_version_keeps_working(
        service, empty_index, source, tmp_path):
    """`use_part` never resolves — it reads the lock and the cache — so a yank
    cannot break a project that already depends on the version."""
    publish(service, local(empty_index), source, work_dir=str(tmp_path / "w"))
    from agentcad import config as user_config
    user_config.save_config({"indexes": [
        {"name": "agentcad-core", "kind": "local", "path": str(empty_index)}]})
    registry = build_registry(service)
    registry.call("add_package", {"project": "rig", "name": WIDGET})
    local(empty_index).yank(WIDGET, "1.0.0")
    detail = registry.call("use_part", {
        "project": "rig", "package": WIDGET, "part": "mount_block",
        "part_id": "block"})
    assert detail["status"]["state"] == "ok"


def test_a_yanked_version_is_not_a_search_hit(service, empty_index, source,
                                              tmp_path):
    publish(service, local(empty_index), source, work_dir=str(tmp_path / "w"))
    local(empty_index).yank(WIDGET, "1.0.0")
    from agentcad.core.packages import search
    assert search.search([local(empty_index)])["hits"] == []


# =========================================================== the vendor gate


def test_a_non_redistributable_package_is_refused_by_a_public_index(
        service, empty_index, source, tmp_path):
    """FR13's confinement is a flag the publisher CHECKS, not a label nobody
    enforces."""
    set_vendor(source, False)
    before = tree_hash(empty_index)
    with pytest.raises(ValidationError) as exc:
        publish(service, local(empty_index), source,
                work_dir=str(tmp_path / "w"))
    assert "McMaster-Carr" in str(exc.value)
    assert "agentcad-core" in str(exc.value)
    assert tree_hash(empty_index) == before


def test_a_non_redistributable_package_publishes_to_a_private_index(
        service, tmp_path, source):
    private = tmp_path / "private"
    private.mkdir()
    (private / "index.json").write_text(json.dumps(
        {"format": 1, "name": "acme", "scope": "private", "packages": {},
         "embeddings": None}, indent=2))
    set_vendor(source, False)
    result = publish(service, local(private), source,
                     work_dir=str(tmp_path / "w"))
    assert result["published"] == f"{WIDGET}@1.0.0"


def test_a_redistributable_vendor_package_publishes_publicly(
        service, empty_index, source, tmp_path):
    set_vendor(source, True)
    assert publish(service, local(empty_index), source,
                   work_dir=str(tmp_path / "w"))["published"]


# ==================================================================== the CLI


def _argv() -> list:
    """The installed console script when there is one (it is what a user
    runs), otherwise `main()` through this interpreter."""
    script = Path(sys.executable).with_name("agentcad")
    return ([str(script)] if script.exists()
            else [sys.executable, "-c", "from agentcad.cli import main; main()"])


def run_cli(*args, projects):
    env = {**os.environ, "AGENTCAD_KERNEL_POOL_SIZE": "1"}
    return subprocess.run(
        _argv() + ["publish", *args, "--projects-dir", str(projects)],
        cwd=str(REPO), capture_output=True, text=True, timeout=600, env=env)


@pytest.fixture
def cli_env(tmp_path, monkeypatch):
    """A config, a cache and a projects dir entirely inside tmp_path."""
    monkeypatch.setenv("AGENTCAD_PACKAGES_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("AGENTCAD_CONFIG", str(tmp_path / "cfg" / "config.json"))
    return tmp_path


@pytest.mark.integration
def test_the_cli_publishes_and_exits_zero(cli_env, empty_index, source):
    from agentcad import config as user_config
    user_config.save_config({"indexes": [
        {"name": "agentcad-core", "kind": "local", "path": str(empty_index)}]})
    proc = run_cli(str(source), "--index", "agentcad-core",
                   "--work-dir", str(cli_env / "work"),
                   projects=cli_env / "projects")
    assert proc.returncode == 0, proc.stderr
    assert f"{WIDGET}@1.0.0" in proc.stdout
    assert "not a security boundary" in proc.stderr
    assert read_entry(empty_index)["gate"]["status"] == "green"


@pytest.mark.integration
def test_the_cli_exits_one_on_a_red_package(cli_env, empty_index):
    from agentcad import config as user_config
    user_config.save_config({"indexes": [
        {"name": "agentcad-core", "kind": "local", "path": str(empty_index)}]})
    before = tree_hash(empty_index)
    proc = run_cli(str(FIXTURES / "break_at_extreme"), "--index",
                   "agentcad-core", "--work-dir", str(cli_env / "work"),
                   projects=cli_env / "projects")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "build:strut@length=max" in proc.stderr
    assert tree_hash(empty_index) == before


@pytest.mark.integration
def test_the_cli_exits_two_on_an_unknown_index(cli_env, source):
    proc = run_cli(str(source), "--index", "nope",
                   projects=cli_env / "projects")
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "nope" in proc.stderr


@pytest.mark.integration
def test_the_cli_yanks_without_starting_a_kernel(cli_env, empty_index, source,
                                                 service, tmp_path):
    """A yank measures nothing, so it must not pay for a kernel."""
    from agentcad import config as user_config
    publish(service, local(empty_index), source, work_dir=str(tmp_path / "w"))
    user_config.save_config({"indexes": [
        {"name": "agentcad-core", "kind": "local", "path": str(empty_index)}]})
    proc = run_cli("--index", "agentcad-core",
                   "--yank", f"{WIDGET}@1.0.0", projects=cli_env / "projects")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert read_entry(empty_index)["yanked"] is True


def test_the_cli_refuses_a_yank_that_is_not_name_at_version(cli_env,
                                                            empty_index):
    from agentcad import config as user_config
    user_config.save_config({"indexes": [
        {"name": "agentcad-core", "kind": "local", "path": str(empty_index)}]})
    proc = run_cli("--index", "agentcad-core", "--yank", "widget_good",
                   projects=cli_env / "projects")
    assert proc.returncode == 2
    assert "name@version" in proc.stderr


def test_publish_without_a_directory_or_a_yank_is_a_usage_error(cli_env,
                                                                empty_index):
    from agentcad import config as user_config
    user_config.save_config({"indexes": [
        {"name": "agentcad-core", "kind": "local", "path": str(empty_index)}]})
    proc = run_cli("--index", "agentcad-core", projects=cli_env / "projects")
    assert proc.returncode == 2
    assert "directory" in proc.stderr
