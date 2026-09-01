# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the Windows desktop build.

One-folder build (not one-file): the app ships ~2.5GB of GGUF weights, and a
one-file build would unpack all of that to a temp dir on every launch. One-folder
keeps startup fast and lets `build_windows.ps1` zip the result for download.

Bundled data (all resolved at runtime via `desktop/resources.py`):
  - app/static      the web UI (html + local fonts)
  - desktop/models  the two GGUF models (fetched by build_windows.ps1)
  - desktop/bin     the Vulkan llama-server.exe + its runtime DLLs

Build with:  pyinstaller reconciliation.spec   (see build_windows.ps1)
"""

from PyInstaller.utils.hooks import collect_all, collect_submodules

datas = [
    ("app/static", "app/static"),
    ("desktop/models", "desktop/models"),
    ("desktop/bin", "desktop/bin"),
]
binaries = []
hiddenimports = []

# pywebview loads its platform backend (Edge WebView2 via pythonnet on Windows)
# dynamically, so its modules/data must be pulled in explicitly.
for pkg in ("webview",):
    pkg_datas, pkg_binaries, pkg_hidden = collect_all(pkg)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden

# uvicorn selects its loop/protocol/lifespan implementations by string at runtime;
# those submodules are invisible to PyInstaller's import graph without this.
hiddenimports += collect_submodules("uvicorn")
# Heavier deps whose optional submodules PyInstaller can miss.
hiddenimports += ["pandas", "openpyxl", "pypdf", "httpx"]


block_cipher = None

a = Analysis(
    ["desktop/launcher.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # The optional hosted providers pull large SDKs the local build never uses.
    # Excluding them keeps the bundle smaller; app/main.py defaults to local.
    excludes=["google", "google.genai", "anthropic", "voyageai"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ReconciliationEngine",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # windowed app — no console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="ReconciliationEngine",
)
