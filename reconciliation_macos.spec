# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the macOS desktop build.

One-folder build wrapped into a .app bundle. The app ships ~2.5 GB of GGUF
weights, so a one-file build is impractical (would unpack on every launch).

Bundled data (resolved at runtime via desktop/resources.py):
  - app/static      the web UI (html + local fonts)
  - desktop/models  the two GGUF models (fetched by build_macos.sh)
  - desktop/bin     the Metal llama-server binary

Build with:  python -m PyInstaller --noconfirm reconciliation_macos.spec
(see build_macos.sh — run on macOS only)
"""

from PyInstaller.utils.hooks import collect_all, collect_submodules

datas = [
    ("app/static", "app/static"),
    ("desktop/models", "desktop/models"),
    ("desktop/bin", "desktop/bin"),
]
binaries = []
hiddenimports = []

for pkg in ("webview",):
    pkg_datas, pkg_binaries, pkg_hidden = collect_all(pkg)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden

hiddenimports += collect_submodules("uvicorn")
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
    excludes=["google", "google.genai", "anthropic", "voyageai"],
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
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=True,   # needed for macOS .app open events
    target_arch=None,      # None = native arch (arm64 on Apple Silicon)
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

app = BUNDLE(
    coll,
    name="ReconciliationEngine.app",
    icon=None,
    bundle_identifier="com.reconciliationengine.app",
    info_plist={
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "12.0",
        "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True},
    },
)
