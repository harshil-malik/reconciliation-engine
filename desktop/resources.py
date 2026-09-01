"""Locate bundled assets in both a normal checkout and a PyInstaller build.

When running from source, assets live next to this file (`desktop/bin`,
`desktop/models`) or in the repo (`app/static`). When frozen by PyInstaller, they
are unpacked under a temporary root exposed as `sys._MEIPASS`, with the same
relative layout preserved by `reconciliation.spec`. Every path lookup in the
desktop layer goes through here so that single distinction lives in one place.
"""

from __future__ import annotations

import sys
from pathlib import Path

# The chat/extraction and embedding GGUF weights, bundled into the app. These file
# names are what `build_windows.ps1` downloads into `desktop/models/`, and what the
# PyInstaller spec ships, so the three must stay in agreement.
CHAT_MODEL_FILE = "qwen2.5-3b-instruct-q4_k_m.gguf"
EMBEDDING_MODEL_FILE = "Qwen3-Embedding-0.6B-Q8_0.gguf"

LLAMA_SERVER_EXE = "llama-server.exe" if sys.platform == "win32" else "llama-server"


def _base_dir() -> Path:
    """Root under which bundled assets are found.

    Frozen: the PyInstaller extraction dir (`sys._MEIPASS`). Source: the repo root,
    i.e. the parent of this `desktop/` package.
    """
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return Path(__file__).resolve().parent.parent


def static_dir() -> Path:
    """The CA-facing web UI (`app/static`), served by FastAPI."""
    return _base_dir() / "app" / "static"


def llama_server_binary() -> Path:
    """Absolute path to the bundled `llama-server.exe`."""
    return _base_dir() / "desktop" / "bin" / LLAMA_SERVER_EXE


def model_path(file_name: str) -> Path:
    """Absolute path to a bundled GGUF model by file name."""
    return _base_dir() / "desktop" / "models" / file_name


def chat_model() -> Path:
    return model_path(CHAT_MODEL_FILE)


def embedding_model() -> Path:
    return model_path(EMBEDDING_MODEL_FILE)
