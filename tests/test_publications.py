"""PRD-007 slice 1: the publication store.

A ``Publication`` is a capability that lives in the **state dir**, never in
project data — shaped exactly like ``core/authstore.py`` (atomic JSON + an
``fcntl.flock`` + an mtime-keyed re-read), and for the same reasons: a second
writer (``agentcad admin``, a future takedown CLI) is routine, ``tar`` of the
volume is a correct backup because every write is an ``os.replace``, and a
token is a store-backed sha256 capability so revocation is immediate.

The tests are the negations the quality bar names: a revoked token that still
resolved, an expired token distinguishable from a revoked one (they must be one
indistinguishable ``None``), a raw secret left in the file, a digest leaked
through the owner listing, and a second process's write not seen without a
restart.
"""

from __future__ import annotations

import time

import pytest

from agentcad.core.publications import PublicationStore


@pytest.fixture
def store(tmp_path):
    return PublicationStore(tmp_path / "publications")


def _mk(store, **over):
    kw = dict(
        share_scope="part", project="p", part_id="nozzle",
        ref={"kind": "tag", "name": "v1", "commit": "9f2c"},
        script_sha="sha256:abc",
        settings={"customizer": True, "exports": ["step"],
                  "show_script": False, "expires": None, "config": None},
        created_by="nikita", default_variant_key="k")
    kw.update(over)
    return store.create(**kw)


def test_token_round_trips_and_names_one_record(store):
    pub_id, token = _mk(store)
    assert token.startswith("shr_")
    assert token.split("_", 2)[1] == pub_id     # the id is in the token
    rec = store.resolve(token)
    assert rec["pub_id"] == pub_id and rec["part_id"] == "nozzle"
    assert rec["settings"]["customizer"] is True
    assert rec["counters"] == {"views": 0, "rebuilds": 0, "downloads": 0}


def test_the_token_is_unguessable_secrets_grade(store):
    """>=128 bits from ``secrets``: the secret half is a token_urlsafe(32) =
    256 bits, so two mints never collide and a guess is hopeless."""
    seen = {_mk(store)[1].split("_", 2)[2] for _ in range(20)}
    assert len(seen) == 20
    assert all(len(secret) >= 40 for secret in seen)   # 32 bytes url-safe


def test_unknown_revoked_and_expired_all_resolve_to_none(store):
    pub_id, token = _mk(store)
    # unknown
    assert store.resolve("shr_deadbeef_" + "x" * 40) is None
    # revoked
    store.revoke(pub_id, by="nikita")
    assert store.resolve(token) is None
    # expired
    _, tok2 = _mk(store, settings={"customizer": False, "exports": [],
                  "show_script": False, "expires": int(time.time()) - 1,
                  "config": None})
    assert store.resolve(tok2) is None


def test_revoked_and_expired_are_indistinguishable(store):
    """AC6's store-level half: both dead paths answer the *same* ``None``,
    with no way to tell a revoked token from an expired one — no oracle over
    what was ever published."""
    p1, t1 = _mk(store)
    store.revoke(p1, by="nikita")
    _, t2 = _mk(store, settings={"customizer": True, "exports": ["step"],
                "show_script": False, "expires": int(time.time()) - 1,
                "config": None})
    assert store.resolve(t1) is store.resolve(t2)   # both None, one identity


def test_a_wrong_secret_for_a_real_id_is_none(store):
    """The id half is public (it is in the URL); only the digest-compared
    secret authenticates. A real id with a forged secret must not resolve."""
    pub_id, token = _mk(store)
    forged = f"shr_{pub_id}_" + "y" * 43
    assert store.resolve(forged) is None
    assert store.resolve(token) is not None          # positive control


def test_list_never_leaks_the_digest(store):
    _mk(store)
    listed = store.list_for("nikita", "p")
    assert listed and "token_digest" not in repr(listed)
    assert "counters" in listed[0] and "pub_id" in listed[0]


def test_list_is_scoped_to_owner_and_project(store):
    _mk(store, created_by="nikita", project="p")
    _mk(store, created_by="anya", project="p")
    _mk(store, created_by="nikita", project="other")
    mine = store.list_for("nikita", "p")
    assert len(mine) == 1 and mine[0]["created_by"] == "nikita"


def test_the_secret_is_not_stored_raw(store, tmp_path):
    _, token = _mk(store)
    blob = (tmp_path / "publications" / "store.json").read_text()
    assert token.split("_", 2)[2] not in blob


def test_bump_is_a_coarse_counter(store):
    pub_id, _ = _mk(store)
    store.bump(pub_id, "views")
    store.bump(pub_id, "rebuilds", 3)
    rec = store.get(pub_id)
    assert rec["counters"]["views"] == 1
    assert rec["counters"]["rebuilds"] == 3


def test_script_path_and_build_root_are_under_the_store(store, tmp_path):
    root = tmp_path / "publications"
    assert store.script_path("sha256:abc") == root / "scripts" / "sha256:abc.py"
    assert store.build_root() == root / "build"


def test_the_module_imports_no_geometry():
    """The publication store and the promoted limiter are server/core code —
    OCP-free by construction, not by care (the PRD-005a meta-path probe)."""
    import subprocess
    import sys

    out = subprocess.run(
        [sys.executable, "-c",
         "import sys\n"
         "class Block:\n"
         "    def find_module(self, name, path=None):\n"
         "        if name.split('.')[0] in {'OCP', 'build123d'}:\n"
         "            raise ImportError(name)\n"
         "sys.meta_path.insert(0, Block())\n"
         "import agentcad.core.publications, agentcad.core.ratelimit\n"
         "print('ok')"],
        capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "ok"


def test_a_second_process_write_is_seen_without_restart(store, tmp_path):
    import subprocess
    import sys

    pub_id, _ = _mk(store)
    subprocess.run(
        [sys.executable, "-c",
         "from agentcad.core.publications import PublicationStore;"
         f"s=PublicationStore({str(tmp_path / 'publications')!r});"
         f"s.bump({pub_id!r}, 'views')"],
        check=True)
    # the running store re-reads on mtime change
    assert store.list_for("nikita", "p")[0]["counters"]["views"] == 1
