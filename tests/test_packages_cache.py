"""PRD-011 slice 2 — the content-verified cache, its receipts, and the two
lockfile maps.

The claims under test are the ones a compromise would have to defeat: a
cached tree is re-verified against a hash the *receipt* holds, a mismatch is
refused and never silently re-fetched, an interrupted install leaves nothing
behind, and a lock entry contains only content-determined values so two
independent installs of the same package write the same bytes.

So most of these tests are the negation of a claim: a flipped byte, an added
file, a deleted receipt, a symlink planted in the cache, a copy that raises
half way, a second install over a tampered entry.
"""

import json
import shutil

import pytest

from agentcad.core.model import NotFoundError, ValidationError
from agentcad.core.packages import cache, content, lockfile

PART_SCRIPT = "PARAMS = {}\n\n\ndef build(p):\n    return None\n"


@pytest.fixture
def cache_root(tmp_path, monkeypatch):
    root = tmp_path / "cache"
    monkeypatch.setenv("AGENTCAD_PACKAGES_DIR", str(root))
    return root


def source_tree(tmp_path, name="iso4762") -> "object":
    root = tmp_path / "src" / name
    (root / "parts").mkdir(parents=True)
    (root / "parts" / "cap_screw.py").write_text(PART_SCRIPT)
    (root / "package.json").write_text('{"name": "iso4762"}\n')
    (root / "docs").mkdir()
    (root / "docs" / "README.md").write_text("# iso4762\n")
    return root


SOURCE = {"kind": "local", "path": "catalog"}


def install(src, *, name="iso4762", version="1.0.0", index="agentcad-core",
            expected=None, source=None):
    return cache.install(
        src, name, version, expected or content.content_id(src),
        index=index, source=source if source is not None else SOURCE,
    )


# ------------------------------------------------------------ cache root


def test_the_cache_root_never_touches_a_real_home(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENTCAD_PACKAGES_DIR", raising=False)
    monkeypatch.setenv("AGENTCAD_CONFIG", str(tmp_path / "cfg" / "config.json"))
    assert cache.root() == tmp_path / "cfg" / "packages"
    monkeypatch.setenv("AGENTCAD_PACKAGES_DIR", str(tmp_path / "elsewhere"))
    assert cache.root() == tmp_path / "elsewhere"


def test_the_cache_layout_is_the_published_one(cache_root, tmp_path):
    src = source_tree(tmp_path)
    path = install(src)
    assert path == cache_root / "iso4762" / "1.0.0"
    assert cache.receipt_path("iso4762", "1.0.0") == (
        cache_root / "iso4762" / ".receipts" / "1.0.0.json"
    )


# --------------------------------------------------------------- install


def test_install_copies_the_tree_and_the_copy_has_the_same_content_id(
        cache_root, tmp_path):
    src = source_tree(tmp_path)
    path = install(src)
    assert (path / "parts" / "cap_screw.py").read_text() == PART_SCRIPT
    assert content.content_id(path) == content.content_id(src)


def test_the_receipt_is_a_sibling_so_it_cannot_change_the_trees_own_id(
        cache_root, tmp_path):
    """A receipt written *inside* the version directory would be part of the
    content it attests to."""
    src = source_tree(tmp_path)
    path = install(src)
    receipt = json.loads(cache.receipt_path("iso4762", "1.0.0").read_text())
    assert receipt["content_id"] == content.content_id(src)
    assert not any(p.name.endswith(".json") and "receipt" in p.name
                   for p in path.rglob("*"))
    assert cache.verify("iso4762", "1.0.0")["status"] == "ok"


def test_the_receipt_carries_the_machine_facts_the_lock_may_not(
        cache_root, tmp_path):
    src = source_tree(tmp_path)
    install(src)
    receipt = json.loads(cache.receipt_path("iso4762", "1.0.0").read_text())
    assert receipt["index"] == "agentcad-core"
    assert receipt["source"] == SOURCE
    assert receipt["fetched_at"]
    assert receipt["files"] == 3
    assert receipt["bytes"] > 0


def test_ignored_files_never_land_in_the_cache(cache_root, tmp_path):
    src = source_tree(tmp_path)
    (src / ".DS_Store").write_bytes(b"\x00")
    (src / "__pycache__").mkdir()
    (src / "__pycache__" / "x.pyc").write_bytes(b"\x00")
    path = install(src)
    assert not (path / ".DS_Store").exists()
    assert not (path / "__pycache__").exists()


def test_install_refuses_a_source_whose_id_is_not_the_expected_one(
        cache_root, tmp_path):
    src = source_tree(tmp_path)
    wrong = "sha256:" + "0" * 64
    with pytest.raises(ValidationError) as exc:
        install(src, expected=wrong)
    message = str(exc.value)
    assert wrong in message and content.content_id(src) in message
    assert not (cache_root / "iso4762" / "1.0.0").exists()


def test_install_refuses_a_source_that_breaks_the_ceilings(
        cache_root, tmp_path, monkeypatch):
    src = source_tree(tmp_path)
    monkeypatch.setattr(content, "MAX_FILE_BYTES", 4)
    with pytest.raises(ValidationError) as exc:
        install(src)
    assert "cap_screw.py" in str(exc.value)
    assert not (cache_root / "iso4762" / "1.0.0").exists()


def test_install_refuses_a_source_containing_a_symlink(cache_root, tmp_path):
    src = source_tree(tmp_path)
    (src / "parts" / "alias.py").symlink_to(src / "parts" / "cap_screw.py")
    with pytest.raises(ValidationError) as exc:
        cache.install(src, "iso4762", "1.0.0", "sha256:" + "0" * 64,
                      index="agentcad-core", source=SOURCE)
    assert "symlink" in str(exc.value)


def test_a_failed_copy_leaves_no_partial_directory_and_no_staging(
        cache_root, tmp_path, monkeypatch):
    src = source_tree(tmp_path)
    calls = {"n": 0}
    real = shutil.copy2

    def flaky(a, b, **kw):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("disk full")
        return real(a, b, **kw)

    monkeypatch.setattr(cache.shutil, "copy2", flaky)
    with pytest.raises(OSError):
        install(src)
    assert not (cache_root / "iso4762" / "1.0.0").exists()
    leftovers = list((cache_root / "iso4762").glob(".staging-*"))
    assert leftovers == []
    assert cache.verify("iso4762", "1.0.0")["status"] == "missing"


def test_installing_the_same_version_twice_is_idempotent(cache_root, tmp_path):
    src = source_tree(tmp_path)
    first = install(src)
    stamp = cache.receipt_path("iso4762", "1.0.0").read_text()
    second = install(src)
    assert first == second
    assert cache.receipt_path("iso4762", "1.0.0").read_text() == stamp
    assert cache.verify("iso4762", "1.0.0")["status"] == "ok"


def test_an_install_interrupted_before_the_receipt_can_be_finished(
        cache_root, tmp_path):
    """`os.replace` lands the tree, then the receipt is written. A crash in
    between leaves correct bytes with nothing attesting to them — and a retry
    must not be stuck for ever behind "remove that directory"."""
    src = source_tree(tmp_path)
    install(src)
    cache.receipt_path("iso4762", "1.0.0").unlink()
    assert cache.verify("iso4762", "1.0.0")["reason"] == "no_receipt"
    install(src)
    assert cache.verify("iso4762", "1.0.0")["status"] == "ok"


def test_finishing_an_interrupted_install_still_refuses_a_tampered_tree(
        cache_root, tmp_path):
    """The receipt is rewritten only when the cached tree hashes to the id the
    index declares — so deleting a receipt is not a way to launder bytes."""
    src = source_tree(tmp_path)
    path = install(src)
    cache.receipt_path("iso4762", "1.0.0").unlink()
    (path / "parts" / "cap_screw.py").write_text("# evil\n")
    with pytest.raises(ValidationError):
        install(src)
    assert cache.verify("iso4762", "1.0.0")["status"] == "tampered"


def test_installing_over_a_tampered_entry_is_refused_not_silently_replaced(
        cache_root, tmp_path):
    """Silently re-downloading over a mismatch is how a compromise becomes
    invisible. The fix is spelled out instead."""
    src = source_tree(tmp_path)
    path = install(src)
    (path / "parts" / "cap_screw.py").write_text(PART_SCRIPT + "# evil\n")
    with pytest.raises(ValidationError) as exc:
        install(src)
    assert "remove" in str(exc.value)
    assert str(path) in str(exc.value)


# ---------------------------------------------------------------- verify


def test_verify_reports_ok_after_a_clean_install(cache_root, tmp_path):
    src = source_tree(tmp_path)
    install(src)
    report = cache.verify("iso4762", "1.0.0")
    assert report["status"] == "ok"
    assert report["expected"] == report["actual"] == content.content_id(src)
    assert report["first_diff"] is None


def test_flipping_one_byte_in_a_cached_script_is_tampered_naming_that_file(
        cache_root, tmp_path):
    src = source_tree(tmp_path)
    path = install(src)
    (path / "parts" / "cap_screw.py").write_text(PART_SCRIPT.replace("None", "Non3"))
    report = cache.verify("iso4762", "1.0.0")
    assert report["status"] == "tampered"
    assert report["first_diff"] == "parts/cap_screw.py"
    assert report["expected"] != report["actual"]


def test_a_file_added_to_or_removed_from_the_cached_tree_is_tampered(
        cache_root, tmp_path):
    src = source_tree(tmp_path)
    path = install(src)
    (path / "parts" / "extra.py").write_text("")
    assert cache.verify("iso4762", "1.0.0")["first_diff"] == "parts/extra.py"
    (path / "parts" / "extra.py").unlink()
    (path / "docs" / "README.md").unlink()
    assert cache.verify("iso4762", "1.0.0")["first_diff"] == "docs/README.md"


def test_a_missing_version_directory_is_missing(cache_root, tmp_path):
    src = source_tree(tmp_path)
    path = install(src)
    shutil.rmtree(path)
    report = cache.verify("iso4762", "1.0.0")
    assert report["status"] == "missing"
    assert report["actual"] is None


def test_an_entry_whose_receipt_is_gone_cannot_be_called_ok(
        cache_root, tmp_path):
    """No receipt is no expected hash. "We did not look" is not "fine", so it
    fails closed."""
    src = source_tree(tmp_path)
    install(src)
    cache.receipt_path("iso4762", "1.0.0").unlink()
    report = cache.verify("iso4762", "1.0.0")
    assert report["status"] == "tampered"
    assert report["reason"] == "no_receipt"
    assert report["expected"] is None


def test_an_unreadable_receipt_is_not_ok_either(cache_root, tmp_path):
    src = source_tree(tmp_path)
    install(src)
    cache.receipt_path("iso4762", "1.0.0").write_text("{not json")
    assert cache.verify("iso4762", "1.0.0")["status"] == "tampered"


def test_a_symlink_planted_in_the_cache_is_tampered_not_a_crash(
        cache_root, tmp_path):
    """A symlink is the way a cached tree gets a file it does not own. The
    inventory raises on one; `verify` must answer, never raise."""
    src = source_tree(tmp_path)
    path = install(src)
    outside = tmp_path / "evil.py"
    outside.write_text("import os\n")
    (path / "parts" / "cap_screw.py").unlink()
    (path / "parts" / "cap_screw.py").symlink_to(outside)
    report = cache.verify("iso4762", "1.0.0")
    assert report["status"] == "tampered"
    assert report["reason"] == "unreadable_tree"


def test_verify_never_raises_whatever_the_tree_looks_like(cache_root, tmp_path):
    """`list_packages` must be able to report on a broken cache entry, so
    every corruption is a status and none of them is an exception."""
    assert cache.verify("never_installed", "9.9.9")["status"] == "missing"
    assert cache.verify("never_installed", "../../etc")["status"] == "missing"
    src = source_tree(tmp_path)
    path = install(src)
    for corrupt in (
        lambda: (path / "parts" / "cap_screw.py").write_text("x"),
        lambda: (path / "docs").rmdir() if not any((path / "docs").iterdir())
        else shutil.rmtree(path / "docs"),
        lambda: cache.receipt_path("iso4762", "1.0.0").write_bytes(b"\xff\xfe"),
    ):
        corrupt()
        assert cache.verify("iso4762", "1.0.0")["status"] == "tampered"


# --------------------------------------------------------------- require


def test_require_returns_the_verified_path(cache_root, tmp_path):
    src = source_tree(tmp_path)
    with pytest.raises(ValidationError):
        cache.require("iso4762", "1.0.0")
    install(src)
    assert cache.require("iso4762", "1.0.0") == cache_root / "iso4762" / "1.0.0"


def test_require_raises_on_a_tampered_entry_and_never_repairs_it(
        cache_root, tmp_path, monkeypatch):
    src = source_tree(tmp_path)
    path = install(src)
    (path / "parts" / "cap_screw.py").write_text("# evil\n")

    def must_not_run(*a, **kw):
        raise AssertionError("require re-fetched over a mismatch")

    monkeypatch.setattr(cache, "install", must_not_run)
    with pytest.raises(ValidationError) as exc:
        cache.require("iso4762", "1.0.0")
    message = str(exc.value)
    assert "iso4762" in message and "1.0.0" in message
    assert "parts/cap_screw.py" in message
    assert "remove" in message and "add_package" in message
    # and the tampered bytes are still there: refusing is not repairing.
    assert (path / "parts" / "cap_screw.py").read_text() == "# evil\n"


def test_require_raises_on_a_missing_entry(cache_root):
    with pytest.raises(ValidationError) as exc:
        cache.require("iso4762", "1.0.0")
    assert "not in the cache" in str(exc.value)


# ------------------------------------------------------------- inspection


def test_cached_versions_lists_only_real_version_directories(
        cache_root, tmp_path):
    src = source_tree(tmp_path)
    install(src, version="1.0.0")
    install(src, version="1.2.0")
    (cache_root / "iso4762" / "not-a-version").mkdir()
    assert cache.cached_versions("iso4762") == ["1.0.0", "1.2.0"]
    assert cache.cached_versions("nothing") == []


def test_read_receipt_returns_none_rather_than_raising(cache_root, tmp_path):
    assert cache.read_receipt("iso4762", "1.0.0") is None
    install(source_tree(tmp_path))
    assert cache.read_receipt("iso4762", "1.0.0")["index"] == "agentcad-core"


# -------------------------------------------------------------- lockfile


def manifest() -> dict:
    return {"schema_version": 2, "name": "rig", "units": "mm", "parts": [],
            "assembly": {"instances": []}}


RESOLVED = {"version": "1.0.0", "content_id": "sha256:" + "9f" * 32,
            "source": {"kind": "local", "path": "catalog"}}


def test_add_writes_both_maps_in_the_published_shape():
    doc = manifest()
    lockfile.add(doc, "iso4762", "^1.0.0", "agentcad-core", RESOLVED)
    assert doc["packages"] == {
        "iso4762": {"version_req": "^1.0.0", "index": "agentcad-core"}
    }
    assert doc["packages_lock"] == {
        "iso4762": {"version": "1.0.0", "content_id": RESOLVED["content_id"],
                    "index": "agentcad-core", "source": RESOLVED["source"]}
    }


def test_two_independent_adds_write_byte_identical_entries():
    """Two branches adding the same package must merge clean, so every value
    in both maps is content-determined — no timestamp, no path, no client."""
    a, b = manifest(), manifest()
    lockfile.add(a, "iso4762", "^1.0.0", "agentcad-core", RESOLVED)
    lockfile.add(b, "iso4762", "^1.0.0", "agentcad-core", dict(RESOLVED))
    assert json.dumps(a, indent=2) == json.dumps(b, indent=2)


def test_no_machine_fact_reaches_either_map():
    doc = manifest()
    lockfile.add(doc, "iso4762", "^1.0.0", "agentcad-core",
                 dict(RESOLVED, fetched_at="2026-08-16T00:00:00Z",
                      path="/Users/someone/.agentcad/packages/iso4762/1.0.0"))
    blob = json.dumps({"packages": doc["packages"],
                       "packages_lock": doc["packages_lock"]})
    for machine_fact in ("fetched_at", "/Users/", "installed_at", "mtime"):
        assert machine_fact not in blob


def test_the_maps_are_written_in_sorted_key_order():
    a, b = manifest(), manifest()
    for name in ("zeta", "alpha"):
        lockfile.add(a, name, "*", "agentcad-core", RESOLVED)
    for name in ("alpha", "zeta"):
        lockfile.add(b, name, "*", "agentcad-core", RESOLVED)
    assert list(a["packages"]) == ["alpha", "zeta"]
    assert json.dumps(a, indent=2) == json.dumps(b, indent=2)


def test_adding_the_same_package_again_replaces_its_entries():
    doc = manifest()
    lockfile.add(doc, "iso4762", "^1.0.0", "agentcad-core", RESOLVED)
    lockfile.add(doc, "iso4762", "^1.2.0", "acme",
                 dict(RESOLVED, version="1.2.0"))
    assert doc["packages"]["iso4762"] == {"version_req": "^1.2.0",
                                          "index": "acme"}
    assert doc["packages_lock"]["iso4762"]["version"] == "1.2.0"
    assert len(doc["packages_lock"]) == 1


def test_removing_the_last_package_removes_both_keys_entirely():
    """FR15: a project that ends up with no packages is byte-identical to one
    that never had any."""
    before = json.dumps(manifest(), indent=2)
    doc = manifest()
    lockfile.add(doc, "iso4762", "^1.0.0", "agentcad-core", RESOLVED)
    assert lockfile.remove(doc, "iso4762") == []
    assert "packages" not in doc and "packages_lock" not in doc
    assert json.dumps(doc, indent=2) == before


def test_removing_one_of_two_packages_keeps_the_other():
    doc = manifest()
    lockfile.add(doc, "iso4762", "^1.0.0", "agentcad-core", RESOLVED)
    lockfile.add(doc, "din625", "^1.0.0", "agentcad-core", RESOLVED)
    lockfile.remove(doc, "iso4762")
    assert list(doc["packages"]) == ["din625"]
    assert list(doc["packages_lock"]) == ["din625"]


def test_removing_a_package_that_is_not_there_is_not_found():
    with pytest.raises(NotFoundError):
        lockfile.remove(manifest(), "iso4762")


def test_remove_reports_the_parts_that_now_read_removed_provenance():
    """The hook slice 7 fills with `provenance.scan`; empty until then."""
    doc = manifest()
    lockfile.add(doc, "iso4762", "^1.0.0", "agentcad-core", RESOLVED)
    assert lockfile.remove(doc, "iso4762", scan=lambda: ["screw_1"]) == ["screw_1"]


def test_read_and_entry_for_answer_from_the_two_maps():
    doc = manifest()
    assert lockfile.read(doc) == {} and lockfile.read_lock(doc) == {}
    assert lockfile.entry_for(doc, "iso4762") is None
    lockfile.add(doc, "iso4762", "^1.0.0", "agentcad-core", RESOLVED)
    assert lockfile.entry_for(doc, "iso4762")["version"] == "1.0.0"
    assert lockfile.requirement_for(doc, "iso4762")["version_req"] == "^1.0.0"


def test_the_returned_maps_are_copies():
    doc = manifest()
    lockfile.add(doc, "iso4762", "^1.0.0", "agentcad-core", RESOLVED)
    lockfile.read(doc)["iso4762"]["index"] = "tampered"
    lockfile.entry_for(doc, "iso4762")["version"] = "9.9.9"
    assert doc["packages"]["iso4762"]["index"] == "agentcad-core"
    assert doc["packages_lock"]["iso4762"]["version"] == "1.0.0"


def test_a_declared_package_with_no_lock_entry_is_visible_as_such():
    """A hand-edited manifest. `use_part` (slice 7) refuses on this, so the
    two maps must be readable independently."""
    doc = manifest()
    doc["packages"] = {"iso4762": {"version_req": "*", "index": "x"}}
    assert lockfile.read(doc) and lockfile.entry_for(doc, "iso4762") is None


@pytest.mark.parametrize("bad", [None, 3, "x", [1]])
def test_a_corrupt_packages_map_reads_as_empty_rather_than_crashing(bad):
    doc = manifest()
    doc["packages"] = bad
    doc["packages_lock"] = bad
    assert lockfile.read(doc) == {}
    assert lockfile.entry_for(doc, "iso4762") is None
