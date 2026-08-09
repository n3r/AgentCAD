"""Unit tests for the frozen-bundle (PyInstaller) helpers.

Pure logic tests — no PyInstaller involved. They fake ``sys.frozen`` /
``sys._MEIPASS`` / ``sys.executable`` with monkeypatch and assert both the
frozen argv/paths and, critically, that the unfrozen behavior is byte-identical
to what the app has always done.
"""

from __future__ import annotations

import sys
from pathlib import Path

from agentcad._resources import resource_root
from agentcad._spawn import worker_argv
from agentcad.agent.mcp_server import _server_spawn_argv

REPO_ROOT = Path(__file__).resolve().parent.parent


def _fake_frozen(monkeypatch, exe: str = "/opt/agentcad/agentcad") -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", exe)


def _fake_unfrozen(monkeypatch) -> None:
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)


# ---------------------------------------------------------------- worker_argv


def test_worker_argv_unfrozen_is_byte_identical(monkeypatch):
    _fake_unfrozen(monkeypatch)
    assert worker_argv() == [sys.executable, "-u", "-m", "agentcad.kernel.worker"]


def test_worker_argv_unfrozen_honors_python_exe(monkeypatch):
    _fake_unfrozen(monkeypatch)
    assert worker_argv("/some/python") == [
        "/some/python", "-u", "-m", "agentcad.kernel.worker",
    ]


def test_worker_argv_frozen_reexecs_bundle_executable(monkeypatch):
    _fake_frozen(monkeypatch, "/Apps/agentcad/agentcad")
    assert worker_argv() == ["/Apps/agentcad/agentcad", "worker"]


def test_worker_argv_reexported_from_cli():
    from agentcad.cli import worker_argv as cli_worker_argv

    assert cli_worker_argv is worker_argv


# -------------------------------------------------------------- resource_root


def test_resource_root_unfrozen_is_repo_root(monkeypatch):
    _fake_unfrozen(monkeypatch)
    root = resource_root()
    assert root == REPO_ROOT
    assert (root / "frontend").is_dir()
    assert (root / "examples").is_dir()


def test_resource_root_frozen_uses_meipass(monkeypatch, tmp_path):
    _fake_frozen(monkeypatch)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert resource_root() == tmp_path


def test_resource_root_frozen_without_meipass_falls_back_to_exe_dir(
    monkeypatch, tmp_path
):
    exe = tmp_path / "bundle" / "agentcad"
    exe.parent.mkdir()
    exe.touch()
    _fake_frozen(monkeypatch, str(exe))
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    assert resource_root() == exe.parent


# --------------------------------------------------------- mcp server spawner


def test_mcp_spawn_argv_unfrozen_uses_uv(monkeypatch):
    _fake_unfrozen(monkeypatch)
    assert _server_spawn_argv() == ["uv", "run", "agentcad", "serve", "--no-open"]


def test_mcp_spawn_argv_frozen_reexecs_bundle_executable(monkeypatch):
    _fake_frozen(monkeypatch, "/Apps/agentcad/agentcad")
    assert _server_spawn_argv() == ["/Apps/agentcad/agentcad", "serve", "--no-open"]
