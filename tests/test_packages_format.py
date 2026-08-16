"""PRD-011 slice 1 — the published format, the content id, and the frozen
PRD-012 configuration schema.

Pure data: no kernel, no service, no network, no I/O beyond reading a package
directory. Everything here is a *published* contract — `package.json`,
`index.json`, `presets.json`, the content id and the version-requirement
grammar are depended upon by every pinned consumer once a package ships — so
the tests are written against the negation of each claim as much as its happy
path: a tampered byte, a reordered tree, a symlink, an empty file, a file
touched but not changed.
"""

import hashlib
import os
from pathlib import Path

import pytest

from agentcad.core.model import ValidationError
from agentcad.core.packages import content, format as pkgformat

# "the key is not there at all", which is not the same as a null value.
ABSENT = object()

# --------------------------------------------------------------- builders


def write(root: Path, relpath: str, data: bytes | str = b"") -> Path:
    path = root / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data.encode() if isinstance(data, str) else data)
    return path


PART_SCRIPT = '''\
"""A socket-head cap screw."""

PARAMS = {"length": {"default": 16.0, "min": 5.0, "max": 60.0,
                     "unit": "mm", "description": "shank length"}}


def build(p):
    raise NotImplementedError
'''


def manifest(**overrides) -> dict:
    doc = {
        "format": 1,
        "name": "iso4762",
        "version": "1.0.0",
        "summary": "ISO 4762 socket-head cap screws",
        "keywords": ["fastener", "screw"],
        "standards": ["ISO 4762"],
        "license": "Apache-2.0",
        "authors": [{"name": "AgentCAD", "url": "https://example.invalid"}],
        "disclosure": "agent",
        "min_agentcad": "0.1.0",
        "provenance": {
            "generator": {"name": "agentcad", "version": "0.1.0"},
            "vendor": None,
        },
        "parts": {
            "cap_screw": {
                "file": "parts/cap_screw.py",
                "label": "Socket-head cap screw",
                "summary": "ISO 4762 cap screw",
            }
        },
        "remix": None,
        "requires": None,
    }
    for key, value in overrides.items():
        if value is ABSENT:
            doc.pop(key, None)
        else:
            doc[key] = value
    return doc


def package_tree(root: Path) -> Path:
    """A minimal well-formed package directory."""
    write(root, "package.json", "{}")
    write(root, "parts/cap_screw.py", PART_SCRIPT)
    write(root, "docs/README.md", "# iso4762\n\nCap screws.\n")
    write(root, "presets.json", "{}")
    return root


def index_entry(**overrides) -> dict:
    entry = {
        "content_id": "sha256:" + "9f" * 32,
        "path": "iso4762/1.0.0",
        "summary": "ISO 4762 socket-head cap screws",
        "keywords": ["fastener"],
        "standards": ["ISO 4762"],
        "license": "Apache-2.0",
        "disclosure": "agent",
        "min_agentcad": "0.1.0",
        "parts": {
            "cap_screw": {
                "params": [
                    {"name": "length", "type": "number", "min": 5.0,
                     "max": 60.0, "unit": "mm", "description": "shank length"}
                ],
                "connectors": {"axis": "cylindrical"},
                "specs": ["shank_dia"],
            }
        },
        "presets": ["m5x16"],
        "previews": ["previews/cap_screw_iso.png"],
        "gate": {"status": "green", "exempt_skips": [], "agentcad": "0.1.0",
                 "build123d": "0.11.1", "report_id": "sha256:" + "ab" * 32},
        "yanked": False,
        "signatures": [],
    }
    for key, value in overrides.items():
        if value is ABSENT:
            entry.pop(key, None)
        else:
            entry[key] = value
    return entry


def index_doc(**overrides) -> dict:
    doc = {
        "format": 1,
        "name": "agentcad-core",
        "scope": "public",
        "packages": {"iso4762": {"versions": {"1.0.0": index_entry()}}},
        "embeddings": None,
    }
    doc.update(overrides)
    return doc


def codes(problems) -> set:
    return {p["code"] for p in problems}


def fields(problems) -> set:
    return {p.get("field") for p in problems}


# ------------------------------------------------------------- content id


def test_the_content_id_of_a_directory_and_its_copy_is_identical(tmp_path):
    a = package_tree(tmp_path / "a")
    b = package_tree(tmp_path / "b")
    assert content.content_id(a) == content.content_id(b)
    assert content.CONTENT_ID_RE.match(content.content_id(a))


def test_the_content_id_is_exactly_the_documented_listing_digest(tmp_path):
    """The formula is published (design Decision 3). A change to it is a
    format change, so it is pinned here rather than described."""
    root = package_tree(tmp_path / "pkg")
    inv = content.inventory(root)
    listing = "".join(f"{path}\0{sha}\n" for path, _n, sha in inv)
    expected = "sha256:" + hashlib.sha256(listing.encode()).hexdigest()
    assert content.content_id(root) == expected


def test_one_added_byte_changes_the_content_id(tmp_path):
    root = package_tree(tmp_path / "pkg")
    before = content.content_id(root)
    (root / "parts" / "cap_screw.py").write_text(PART_SCRIPT + " ")
    assert content.content_id(root) != before


def test_an_added_file_changes_the_content_id(tmp_path):
    root = package_tree(tmp_path / "pkg")
    before = content.content_id(root)
    write(root, "parts/extra.py", "")
    assert content.content_id(root) != before


def test_an_added_empty_file_changes_the_content_id(tmp_path):
    """An empty file is a file: it has a path, so it is in the listing."""
    root = package_tree(tmp_path / "pkg")
    before = content.content_id(root)
    write(root, "docs/EMPTY", b"")
    inv = dict((p, n) for p, n, _s in content.inventory(root))
    assert inv["docs/EMPTY"] == 0
    assert content.content_id(root) != before


def test_renaming_a_file_changes_the_content_id(tmp_path):
    root = package_tree(tmp_path / "pkg")
    before = content.content_id(root)
    (root / "parts" / "cap_screw.py").rename(root / "parts" / "screw.py")
    assert content.content_id(root) != before


def test_two_files_swapping_contents_changes_the_content_id(tmp_path):
    """The listing binds each hash to its path. A digest over the sorted
    hashes alone would call this pair of packages identical."""
    root = package_tree(tmp_path / "pkg")
    write(root, "docs/a.txt", "alpha")
    write(root, "docs/b.txt", "beta")
    before = content.content_id(root)
    write(root, "docs/a.txt", "beta")
    write(root, "docs/b.txt", "alpha")
    assert content.content_id(root) != before


def test_touching_every_mtime_does_not_change_the_content_id(tmp_path):
    root = package_tree(tmp_path / "pkg")
    before = content.content_id(root)
    for path in sorted(root.rglob("*")):
        os.utime(path, (1_000_000, 1_000_000))
    assert content.content_id(root) == before


def test_creating_the_files_in_a_different_order_does_not_change_the_id(tmp_path):
    a = tmp_path / "a"
    for rel in ("package.json", "parts/cap_screw.py", "docs/README.md", "z.txt"):
        write(a, rel, rel)
    b = tmp_path / "b"
    for rel in ("z.txt", "docs/README.md", "parts/cap_screw.py", "package.json"):
        write(b, rel, rel)
    assert content.content_id(a) == content.content_id(b)


def test_ignored_paths_are_not_part_of_the_identity(tmp_path):
    root = package_tree(tmp_path / "pkg")
    before = content.content_id(root)
    write(root, ".git/config", "[core]\n")
    write(root, "parts/__pycache__/cap_screw.cpython-312.pyc", b"\x00")
    write(root, "parts/stray.pyc", b"\x00")
    write(root, ".DS_Store", b"\x00")
    write(root, "build.tmp", b"x")
    assert content.content_id(root) == before
    assert [p for p, _n, _s in content.inventory(root)] == [
        "docs/README.md", "package.json", "parts/cap_screw.py", "presets.json",
    ]


def test_first_difference_names_the_first_differing_path(tmp_path):
    a = package_tree(tmp_path / "a")
    b = package_tree(tmp_path / "b")
    (b / "parts" / "cap_screw.py").write_text(PART_SCRIPT + "# edited\n")
    assert content.first_difference(
        content.inventory(a), content.inventory(b)
    ) == "parts/cap_screw.py"
    assert content.first_difference(
        content.inventory(a), content.inventory(a)
    ) is None


def test_first_difference_reports_an_added_and_a_removed_path(tmp_path):
    a = package_tree(tmp_path / "a")
    b = package_tree(tmp_path / "b")
    write(b, "docs/AAA.md", "x")
    assert content.first_difference(
        content.inventory(a), content.inventory(b)
    ) == "docs/AAA.md"
    (b / "docs" / "AAA.md").unlink()
    (b / "docs" / "README.md").unlink()
    assert content.first_difference(
        content.inventory(a), content.inventory(b)
    ) == "docs/README.md"


# --------------------------------------------------------------- ceilings


def synthetic(n_files: int, each_bytes: int) -> list:
    return [(f"f{i:04d}", each_bytes, "0" * 64) for i in range(n_files)]


def test_a_package_inside_every_ceiling_has_no_problems():
    assert content.check_ceilings(synthetic(10, 1024)) == []


def test_the_ceilings_refuse_an_oversized_file():
    inv = synthetic(2, 8) + [("big.step", content.MAX_FILE_BYTES + 1, "0" * 64)]
    problems = content.check_ceilings(inv)
    assert codes(problems) == {"file_too_large"}
    assert "big.step" in problems[0]["message"]


def test_the_ceilings_refuse_an_oversized_tree():
    each = content.MAX_FILE_BYTES
    n = content.MAX_PACKAGE_BYTES // each + 1
    problems = content.check_ceilings(synthetic(n, each))
    assert "package_too_large" in codes(problems)


def test_the_ceilings_refuse_too_many_files():
    problems = content.check_ceilings(synthetic(content.MAX_FILES + 1, 1))
    assert "too_many_files" in codes(problems)


def test_a_real_oversized_file_is_caught_through_the_inventory(tmp_path):
    root = package_tree(tmp_path / "pkg")
    write(root, "imports/vendor.step", b"0" * (content.MAX_FILE_BYTES + 1))
    problems = content.check_ceilings(content.inventory(root))
    assert codes(problems) == {"file_too_large"}
    assert "imports/vendor.step" in problems[0]["message"]


# ------------------------------------------------------ paths and symlinks


def test_a_symlinked_file_inside_a_package_is_refused(tmp_path):
    root = package_tree(tmp_path / "pkg")
    outside = tmp_path / "outside.py"
    outside.write_text("print('hi')\n")
    (root / "parts" / "linked.py").symlink_to(outside)
    with pytest.raises(ValidationError) as exc:
        content.inventory(root)
    assert "parts/linked.py" in str(exc.value)
    assert "symlink" in str(exc.value)


def test_a_symlinked_directory_inside_a_package_is_refused(tmp_path):
    root = package_tree(tmp_path / "pkg")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "x.py").write_text("")
    (root / "vendor").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValidationError) as exc:
        content.inventory(root)
    assert "vendor" in str(exc.value)


def test_a_symlink_pointing_at_a_file_inside_the_root_is_still_refused(tmp_path):
    """Not a traversal — but two paths for one file make the listing a
    function of how the tree was built, which the content id must not be."""
    root = package_tree(tmp_path / "pkg")
    (root / "parts" / "alias.py").symlink_to(root / "parts" / "cap_screw.py")
    with pytest.raises(ValidationError):
        content.inventory(root)


def test_a_missing_root_and_a_file_root_are_refused(tmp_path):
    with pytest.raises(ValidationError):
        content.inventory(tmp_path / "nope")
    (tmp_path / "afile").write_text("")
    with pytest.raises(ValidationError):
        content.inventory(tmp_path / "afile")


@pytest.mark.parametrize(
    "relpath", ["../x", "/etc/passwd", "parts/../../x", "", ".", "..", "a/../../b"]
)
def test_resolve_within_refuses_a_path_that_escapes_the_root(tmp_path, relpath):
    with pytest.raises(ValidationError):
        content.resolve_within(tmp_path, relpath)


def test_resolve_within_refuses_a_symlink_that_escapes_the_root(tmp_path):
    root = tmp_path / "pkg"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    (root / "link.txt").symlink_to(outside)
    with pytest.raises(ValidationError):
        content.resolve_within(root, "link.txt")


def test_resolve_within_accepts_an_ordinary_relative_path(tmp_path):
    root = package_tree(tmp_path / "pkg")
    assert content.resolve_within(root, "parts/cap_screw.py").is_file()


# ------------------------------------------------------- package manifest


def test_a_complete_manifest_validates(tmp_path):
    root = package_tree(tmp_path / "pkg")
    assert pkgformat.validate_package_manifest(manifest(), root=root) == []


@pytest.mark.parametrize("field", sorted(pkgformat.PACKAGE_REQUIRED))
def test_every_required_field_is_required(tmp_path, field):
    root = package_tree(tmp_path / "pkg")
    doc = manifest(**{field: ABSENT})
    problems = pkgformat.validate_package_manifest(doc, root=root)
    assert "missing_field" in codes(problems)
    assert field in fields(problems)


@pytest.mark.parametrize(
    "field,value",
    [
        ("format", "1"),
        ("format", 2),
        ("name", 5),
        ("name", "ISO4762"),
        ("name", "9lives"),
        ("version", "1.0"),
        ("version", "1.0.0-rc.1"),
        ("version", 1.0),
        ("summary", 3),
        ("summary", "   "),
        ("keywords", "fastener"),
        ("keywords", [3]),
        ("standards", "ISO 4762"),
        ("license", ""),
        ("license", None),
        ("authors", []),
        ("authors", ["AgentCAD"]),
        ("authors", [{"url": "x"}]),
        ("authors", [{"name": ""}]),
        ("disclosure", "robot"),
        ("disclosure", None),
        ("min_agentcad", "0.1"),
        ("provenance", []),
        ("parts", {}),
        ("parts", []),
        ("remix", {"of": {"name": "x"}}),
        ("requires", {"extras": "fem"}),
    ],
)
def test_a_wrong_typed_or_out_of_range_field_is_a_problem(tmp_path, field, value):
    root = package_tree(tmp_path / "pkg")
    problems = pkgformat.validate_package_manifest(
        manifest(**{field: value}), root=root
    )
    assert problems, f"{field}={value!r} was accepted"
    assert any(str(p.get("field", "")).startswith(field) for p in problems)


def test_an_unknown_key_is_a_problem_not_ignored(tmp_path):
    """A published format that swallows a typo teaches authors it worked."""
    root = package_tree(tmp_path / "pkg")
    problems = pkgformat.validate_package_manifest(
        manifest(licence="Apache-2.0"), root=root
    )
    assert "unknown_key" in codes(problems)
    assert "licence" in " ".join(p["message"] for p in problems)


def test_an_unknown_key_inside_an_author_and_inside_provenance_is_refused(tmp_path):
    root = package_tree(tmp_path / "pkg")
    doc = manifest(authors=[{"name": "A", "verified": True}])
    assert "unknown_key" in codes(
        pkgformat.validate_package_manifest(doc, root=root)
    )
    doc = manifest(provenance={"generator": {"name": "a", "version": "0.1.0"},
                               "vendor": None, "signed_by": "x"})
    assert "unknown_key" in codes(
        pkgformat.validate_package_manifest(doc, root=root)
    )


@pytest.mark.parametrize("value", ["human", "agent", "hybrid"])
def test_disclosure_accepts_exactly_the_three_declared_values(tmp_path, value):
    root = package_tree(tmp_path / "pkg")
    assert pkgformat.validate_package_manifest(
        manifest(disclosure=value), root=root
    ) == []


def test_a_part_file_must_live_under_parts_and_must_exist(tmp_path):
    root = package_tree(tmp_path / "pkg")
    write(root, "elsewhere.py", PART_SCRIPT)
    outside = manifest(parts={"cap_screw": {"file": "elsewhere.py"}})
    assert "bad_value" in codes(
        pkgformat.validate_package_manifest(outside, root=root)
    )
    missing = manifest(parts={"cap_screw": {"file": "parts/nope.py"}})
    assert "missing_file" in codes(
        pkgformat.validate_package_manifest(missing, root=root)
    )
    escaping = manifest(parts={"cap_screw": {"file": "parts/../../secrets.py"}})
    assert "bad_value" in codes(
        pkgformat.validate_package_manifest(escaping, root=root)
    )


def test_a_part_id_must_be_an_identifier(tmp_path):
    root = package_tree(tmp_path / "pkg")
    doc = manifest(parts={"Cap Screw": {"file": "parts/cap_screw.py"}})
    assert "bad_value" in codes(
        pkgformat.validate_package_manifest(doc, root=root)
    )


def test_without_a_root_the_file_existence_check_is_skipped():
    doc = manifest(parts={"cap_screw": {"file": "parts/nope.py"}})
    assert pkgformat.validate_package_manifest(doc) == []


def test_remix_and_requires_are_reserved_but_validated_when_present(tmp_path):
    root = package_tree(tmp_path / "pkg")
    ok = manifest(
        remix={"of": {"name": "iso4762", "version": "1.0.0",
                      "content_id": "sha256:" + "0" * 64}},
        requires={"extras": ["fem"]},
    )
    assert pkgformat.validate_package_manifest(ok, root=root) == []
    bad = manifest(remix={"of": {"name": "iso4762", "version": "1.0.0",
                                 "content_id": "not-a-digest"}})
    assert pkgformat.validate_package_manifest(bad, root=root)


def test_a_vendor_provenance_block_is_mechanical(tmp_path):
    """`redistributable` is the flag slice 8's publisher checks, so its type
    is enforced here rather than being a label nobody validates."""
    root = package_tree(tmp_path / "pkg")
    vendor = {"name": "McMaster-Carr", "part_number": "91290A115",
              "url": "https://example.invalid", "terms": "vendor terms",
              "redistributable": False}
    assert pkgformat.validate_package_manifest(
        manifest(provenance={"generator": {"name": "agentcad",
                                           "version": "0.1.0"},
                             "vendor": vendor}), root=root
    ) == []
    vendor_bad = dict(vendor, redistributable="false")
    problems = pkgformat.validate_package_manifest(
        manifest(provenance={"generator": {"name": "agentcad",
                                           "version": "0.1.0"},
                             "vendor": vendor_bad}), root=root
    )
    assert "wrong_type" in codes(problems)


def test_a_non_object_manifest_is_one_problem_not_a_crash(tmp_path):
    for doc in ([], "x", None, 3):
        problems = pkgformat.validate_package_manifest(doc)
        assert len(problems) == 1 and problems[0]["code"] == "wrong_type"


# ----------------------------------------------------------------- semver


@pytest.mark.parametrize(
    "text,expected",
    [("0.0.0", (0, 0, 0)), ("1.2.3", (1, 2, 3)), ("10.20.30", (10, 20, 30))],
)
def test_parse_version_accepts_the_v1_grammar(text, expected):
    assert pkgformat.parse_version(text) == expected


@pytest.mark.parametrize(
    "text",
    ["1.2", "1.2.3.4", "1.2.3-rc.1", "v1.2.3", "01.2.3", "1.2.x", "", None, 1.2],
)
def test_parse_version_refuses_everything_else(text):
    with pytest.raises(ValidationError):
        pkgformat.parse_version(text)


def test_compare_orders_versions_numerically_not_lexically():
    assert pkgformat.compare("1.10.0", "1.9.0") > 0
    assert pkgformat.compare("1.0.0", "1.0.0") == 0
    assert pkgformat.compare("0.9.9", "1.0.0") < 0


@pytest.mark.parametrize(
    "version,requirement,expected",
    [
        # exact
        ("1.2.3", "1.2.3", True),
        ("1.2.4", "1.2.3", False),
        # caret: >=X.Y.Z, <(X+1).0.0
        ("1.2.3", "^1.2.3", True),
        ("1.9.9", "^1.2.3", True),
        ("2.0.0", "^1.2.3", False),
        ("1.2.2", "^1.2.3", False),
        # caret on 0.x is NOT npm's rule (see the docstring)
        ("0.9.0", "^0.1.0", True),
        ("1.0.0", "^0.1.0", False),
        # tilde: >=X.Y.Z, <X.(Y+1).0
        ("1.2.9", "~1.2.3", True),
        ("1.3.0", "~1.2.3", False),
        ("1.2.2", "~1.2.3", False),
        # wildcard / omitted
        ("7.0.1", "*", True),
        ("7.0.1", None, True),
        ("7.0.1", "", True),
    ],
)
def test_the_requirement_grammar_table(version, requirement, expected):
    assert pkgformat.satisfies(version, requirement) is expected


def test_caret_zero_is_not_npms_caret_zero():
    """npm reads `^0.1.0` as `>=0.1.0 <0.2.0`; this format reads it as
    `>=0.1.0 <1.0.0`. A reader will assume npm, so it is pinned."""
    assert pkgformat.satisfies("0.2.0", "^0.1.0") is True


@pytest.mark.parametrize("requirement", ["=1.2.3", ">=1.2.3", "^1.2", "1.x", "^*"])
def test_an_unsupported_requirement_is_refused(requirement):
    with pytest.raises(ValidationError):
        pkgformat.satisfies("1.2.3", requirement)


def test_resolve_picks_the_highest_matching_version():
    versions = ["1.0.0", "1.2.0", "1.10.0", "2.0.0"]
    assert pkgformat.resolve(versions, "^1.0.0") == "1.10.0"
    assert pkgformat.resolve(versions, "~1.2.0") == "1.2.0"
    assert pkgformat.resolve(versions, "*") == "2.0.0"
    assert pkgformat.resolve(versions, "3.0.0") is None


def test_resolve_skips_yanked_versions_unless_asked():
    versions = {
        "1.0.0": {"yanked": False},
        "1.1.0": {"yanked": True},
    }
    assert pkgformat.resolve(versions, "^1.0.0") == "1.0.0"
    assert pkgformat.resolve(versions, "^1.0.0", allow_yanked=True) == "1.1.0"
    assert pkgformat.resolve({"1.1.0": {"yanked": True}}, "^1.0.0") is None


def test_resolve_ignores_a_version_string_it_cannot_parse():
    """An index is data from elsewhere; one bad key must not make the whole
    package unresolvable."""
    assert pkgformat.resolve(["1.0.0", "nonsense"], "*") == "1.0.0"


# --------------------------------------------------------------- index.json


def test_a_complete_index_validates():
    assert pkgformat.validate_index(index_doc()) == []


@pytest.mark.parametrize("scope", ["public", "private"])
def test_index_scope_accepts_exactly_two_values(scope):
    assert pkgformat.validate_index(index_doc(scope=scope)) == []


def test_an_unknown_index_scope_is_a_problem():
    assert "bad_value" in codes(pkgformat.validate_index(index_doc(scope="org")))


def test_an_entry_path_that_escapes_the_index_root_is_a_problem():
    for path in ("../elsewhere", "/abs/path", "iso4762/../../x"):
        doc = index_doc(
            packages={"iso4762": {"versions": {"1.0.0": index_entry(path=path)}}}
        )
        assert "bad_value" in codes(pkgformat.validate_index(doc)), path


def test_a_malformed_content_id_is_a_problem():
    for bad in ("9f" * 32, "sha256:zz", "sha1:" + "0" * 40, "SHA256:" + "0" * 64):
        doc = index_doc(
            packages={"iso4762": {"versions": {"1.0.0": index_entry(content_id=bad)}}}
        )
        assert "bad_value" in codes(pkgformat.validate_index(doc)), bad


def test_signatures_must_be_present_and_empty():
    doc = index_doc(
        packages={"iso4762": {"versions": {"1.0.0": index_entry(
            signatures=ABSENT)}}}
    )
    assert "missing_field" in codes(pkgformat.validate_index(doc))
    doc = index_doc(
        packages={"iso4762": {"versions": {"1.0.0": index_entry(
            signatures=[{"by": "someone"}])}}}
    )
    assert "bad_value" in codes(pkgformat.validate_index(doc))


def test_gate_exempt_skips_must_be_a_list_of_strings():
    doc = index_doc(
        packages={"iso4762": {"versions": {"1.0.0": index_entry(
            gate={"status": "green", "exempt_skips": "none", "agentcad": "0.1.0",
                  "build123d": "0.11.1", "report_id": "sha256:" + "0" * 64})}}}
    )
    assert "wrong_type" in codes(pkgformat.validate_index(doc))


def test_an_index_version_key_must_be_a_version():
    doc = index_doc(
        packages={"iso4762": {"versions": {"latest": index_entry()}}}
    )
    assert "bad_value" in codes(pkgformat.validate_index(doc))


def test_an_unknown_index_key_is_refused():
    assert "unknown_key" in codes(pkgformat.validate_index(index_doc(mirror="x")))


def test_a_non_object_index_is_one_problem_not_a_crash():
    problems = pkgformat.validate_index([])
    assert len(problems) == 1 and problems[0]["code"] == "wrong_type"


# ------------------------------------------- configurations (the PRD-012 freeze)


PARAMS_SPEC = {
    "size": {"type": "enum", "default": "M5-0.8",
             "choices": ["M3-0.5", "M5-0.8"], "min": None, "max": None,
             "unit": None, "description": "thread"},
    "length": {"type": "number", "default": 16.0, "min": 5.0, "max": 60.0,
               "unit": "mm", "description": "shank length"},
    "knurled": {"type": "bool", "default": False, "min": None, "max": None,
                "unit": None, "description": "knurl the head"},
}


def test_a_configuration_validates_with_and_without_a_params_spec():
    entry = {"params": {"size": "M5-0.8", "length": 16.0}, "label": "M5 × 16"}
    assert pkgformat.validate_configuration(entry, None) == []
    assert pkgformat.validate_configuration(entry, PARAMS_SPEC) == []


def test_a_prd012_shaped_config_map_validates_through_the_same_function():
    """PRD-012 will store `parts.<id>.configs` — the same objects, validated
    by this function. Decision 4 freezes that, so it is a test, not a comment.
    """
    configs = {
        "s": {"params": {"width": 10.0}, "label": "Small"},
        "l": {"params": {"width": 40.0}, "label": "Large",
              "description": "the wide one"},
    }
    for name, entry in configs.items():
        assert pkgformat.CONFIG_RE.match(name)
        assert pkgformat.validate_configuration(entry, None) == []


def test_the_flat_prd012_fr1_shape_is_refused_and_names_the_ambiguity():
    """`{"s": {"width": 10}}` is unreadable the day a part declares a
    parameter called `label`. The refusal has to say so."""
    problems = pkgformat.validate_configuration({"width": 10}, None)
    assert "unknown_key" in codes(problems)
    assert "missing_field" in codes(problems)
    message = " ".join(p["message"] for p in problems)
    assert "a part may declare a parameter called 'label'" in message


def test_a_configuration_refuses_unknown_keys_and_non_scalar_params():
    assert "unknown_key" in codes(
        pkgformat.validate_configuration(
            {"params": {"length": 16.0}, "notes": "hi"}, None)
    )
    for value in ([1, 2], {"a": 1}, None):
        assert "wrong_type" in codes(
            pkgformat.validate_configuration({"params": {"length": value}}, None)
        )


def test_a_configuration_is_checked_against_the_params_spec_when_given():
    unknown = pkgformat.validate_configuration(
        {"params": {"lenght": 16.0}}, PARAMS_SPEC)
    assert "bad_value" in codes(unknown)
    assert "lenght" in " ".join(p["message"] for p in unknown)

    out_of_range = pkgformat.validate_configuration(
        {"params": {"length": 600.0}}, PARAMS_SPEC)
    assert "bad_value" in codes(out_of_range)

    wrong_choice = pkgformat.validate_configuration(
        {"params": {"size": "M99"}}, PARAMS_SPEC)
    assert "bad_value" in codes(wrong_choice)

    wrong_type = pkgformat.validate_configuration(
        {"params": {"knurled": "yes"}}, PARAMS_SPEC)
    assert "wrong_type" in codes(wrong_type)

    # a bool is not an int-typed number: JSON says True == 1 and the params
    # contract does not.
    assert "wrong_type" in codes(
        pkgformat.validate_configuration({"params": {"length": True}}, PARAMS_SPEC)
    )


def test_an_int_valued_number_parameter_is_accepted():
    assert pkgformat.validate_configuration(
        {"params": {"length": 16}}, PARAMS_SPEC) == []


# --------------------------------------------------------------- presets.json


def presets_doc(**overrides) -> dict:
    doc = {
        "format": 1,
        "presets": {
            "cap_screw": {
                "m5x16": {"params": {"size": "M5-0.8", "length": 16.0},
                          "label": "M5 × 16"}
            }
        },
    }
    doc.update(overrides)
    return doc


def test_a_presets_document_validates():
    assert pkgformat.validate_presets(presets_doc(), ["cap_screw"]) == []


def test_presets_refuse_a_part_the_package_does_not_declare():
    problems = pkgformat.validate_presets(presets_doc(), ["other_part"])
    assert "bad_value" in codes(problems)
    assert "cap_screw" in " ".join(p["message"] for p in problems)


def test_a_configuration_name_must_match_the_published_grammar():
    doc = presets_doc(presets={"cap_screw": {"M5 x 16": {"params": {}}}})
    assert "bad_value" in codes(pkgformat.validate_presets(doc, ["cap_screw"]))


def test_an_unknown_presets_key_and_a_bad_format_are_refused():
    assert "unknown_key" in codes(
        pkgformat.validate_presets(presets_doc(extra=1), ["cap_screw"])
    )
    assert "bad_value" in codes(
        pkgformat.validate_presets(presets_doc(format=2), ["cap_screw"])
    )


def test_an_empty_presets_document_is_legitimate():
    assert pkgformat.validate_presets({"format": 1, "presets": {}}, []) == []


@pytest.mark.portability
@pytest.mark.skipif(
    os.name == "nt" or getattr(os, "geteuid", lambda: 1)() == 0,
    reason="root reads a 0o000 file anyway; Windows has neither geteuid nor "
           "POSIX mode bits (chmod 0o000 does not make a file unreadable)")
def test_a_file_that_cannot_be_read_is_one_error_type(tmp_path):
    """`inventory` raises ValidationError and nothing else, so `cache.verify`
    (which must never propagate) has one thing to catch."""
    root = package_tree(tmp_path / "pkg")
    locked = root / "parts" / "cap_screw.py"
    locked.chmod(0o000)
    try:
        with pytest.raises(ValidationError) as exc:
            content.inventory(root)
        assert "parts/cap_screw.py" in str(exc.value)
    finally:
        locked.chmod(0o644)
