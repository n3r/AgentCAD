# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller onedir spec for the AgentCAD single-binary distribution.

Build with scripts/build_binary.sh (or `make dist`); the result is
dist/agentcad/ with an `agentcad` executable that serves the app — server,
frontend, examples, and the OCCT kernel worker (which the executable re-execs
in itself via the hidden `worker` subcommand; see agentcad/_spawn.py).
"""

from pathlib import Path

from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_dynamic_libs,
    collect_submodules,
)

REPO_ROOT = Path(SPECPATH).resolve().parent.parent  # noqa: F821 — SPECPATH is injected

hiddenimports = []
# agentcad discovers its extension packs at runtime with pkgutil.iter_modules
# (core/tools_*.py, server/routes_*.py, kernel/handlers/*.py) and the toolkit
# re-exports submodules via a lazy __getattr__ — none of that is visible to
# static import analysis. Collecting every agentcad submodule is version-proof
# as new packs are added (PyInstaller's pkgutil runtime hook then makes
# iter_modules work over the frozen package).
hiddenimports += collect_submodules("agentcad")
# OCP submodules are tiny shims (`from ..OCP.X import *`) over one big
# compiled extension. build123d's static imports pull most of them anyway;
# collecting all of them keeps user part scripts free to import any
# `OCP.<module>` (part scripts exec inside the frozen worker).
hiddenimports += collect_submodules("OCP")
# bd_warehouse (threads/fasteners) is imported lazily by agentcad.toolkit and
# freely by user part scripts — pure Python, cheap to include entirely.
hiddenimports += collect_submodules("bd_warehouse")
# uvicorn's "standard" extras (uvloop/httptools/websockets) are imported
# behind try/except at runtime; make sure the compiled ones are present.
hiddenimports += collect_submodules("uvicorn")

datas = [
    # App resources, resolved at runtime through agentcad._resources.resource_root().
    (str(REPO_ROOT / "frontend"), "frontend"),
    (str(REPO_ROOT / "examples"), "examples"),
]
# build123d ships non-Python data: bundled fonts (data/fonts/...) used by
# text rendering, and template_render.js.
datas += collect_data_files("build123d")
# lib3mf (3MF export) loads its C library from the package directory via
# ctypes — the .dylib is data as far as PyInstaller's analysis is concerned.
datas += collect_data_files("lib3mf", includes=["**/*.dylib", "**/*.so"])

binaries = []
# The OCCT toolkit dylibs live in OCP/.dylibs and are loaded by the OCP
# extension module; PyInstaller's analysis usually finds them as link-time
# dependencies, but collecting explicitly is the robust, documented path.
binaries += collect_dynamic_libs("OCP")

a = Analysis(
    [str(REPO_ROOT / "packaging" / "pyinstaller" / "entry.py")],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Dev/test-only or absent-by-default heavies; the [fem] extra
        # (gmsh/scikit-fem/meshio) is not part of the bundled distribution.
        "tkinter",
        "pytest",
        "gmsh",
        "skfem",
        "meshio",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="agentcad",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="agentcad",
)
