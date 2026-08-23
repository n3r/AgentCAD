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


#: PRD-029 skill budget: how many skills an agent may hold loaded at once,
#: their combined size, and the cap one skill is truncated to. Defaults are
#: the spec's; env beats the stored config, which beats these.
SKILLS_BUDGET_DEFAULTS = {"max_loaded": 4, "max_loaded_chars": 40_000,
                          "max_skill_chars": 24_000}

_SKILLS_ENV = {"max_loaded": "AGENTCAD_SKILLS_MAX_LOADED",
               "max_loaded_chars": "AGENTCAD_SKILLS_MAX_LOADED_CHARS",
               "max_skill_chars": "AGENTCAD_SKILLS_MAX_SKILL_CHARS"}


def get_skills_budget() -> dict:
    """The three skill caps: env > config `skills` block > the defaults.

    The `get_kernel_pool_size` shape, three keys wide: a value is taken only
    when it is a positive integer, so a typo falls through to the layer below
    instead of pinning the budget to zero. Never writes the config file —
    reading a default must not create one.
    """
    cfg = load_config().get("skills")
    cfg = cfg if isinstance(cfg, dict) else {}
    out = {}
    for key, default in SKILLS_BUDGET_DEFAULTS.items():
        override = os.environ.get(_SKILLS_ENV[key])
        if override and override.strip().isdigit() and int(override) >= 1:
            out[key] = int(override)
            continue
        stored = cfg.get(key)
        out[key] = stored if isinstance(stored, int) and stored >= 1 else default
    return out


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
