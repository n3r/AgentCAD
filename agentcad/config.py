"""Persistent user-level configuration (~/.agentcad/config.json).

The path can be overridden with the AGENTCAD_CONFIG environment variable,
which tests use to avoid touching the real home directory.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

DEFAULT_PORT = 8630  # allocated via the local port registry (cad_claude/dev/web)


def config_path() -> Path:
    override = os.environ.get("AGENTCAD_CONFIG")
    if override:
        return Path(override)
    return Path.home() / ".agentcad" / "config.json"


def load_config() -> dict:
    path = config_path()
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {}
        return data
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_config(cfg: dict) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp, path)


def get_port() -> int:
    cfg = load_config()
    port = cfg.get("port")
    if isinstance(port, int) and 1024 <= port <= 65535:
        return port
    cfg["port"] = DEFAULT_PORT
    save_config(cfg)
    return DEFAULT_PORT


def get_kernel_pool_size() -> int:
    """Number of kernel workers. Memory (~0.5 GB/worker), not cores, is the
    limit, so default conservatively; override via config or env."""
    override = os.environ.get("AGENTCAD_KERNEL_POOL_SIZE")
    if override and override.isdigit():
        return max(1, int(override))
    cfg = load_config()
    size = cfg.get("kernel_pool_size")
    if isinstance(size, int) and size >= 1:
        return size
    cores = os.cpu_count() or 4
    return max(1, min(3, cores // 3))
