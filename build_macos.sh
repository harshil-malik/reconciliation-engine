#!/usr/bin/env bash
# Build the macOS desktop app into a distributable zip.
#
# Run from the repo root inside the project's virtualenv:
#   chmod +x build_macos.sh && ./build_macos.sh
#
# Output: dist/ReconciliationEngine-macos.zip
# Users unzip it and double-click ReconciliationEngine.app
#
# Requires: macOS 12+, Python 3.11+, Homebrew

set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
BIN_DIR="$ROOT/desktop/bin"
MODEL_DIR="$ROOT/desktop/models"

mkdir -p "$BIN_DIR" "$MODEL_DIR"

download_if_missing() {
    local url="$1" dest="$2" label="$3"
    if [[ -f "$dest" ]]; then
        echo "[skip] $label already present"
        return
    fi
    echo "[download] $label -> $dest"
    curl -4 -L -C - --retry 5 --retry-delay 3 -o "$dest" "$url"
}

# --- 1. Python dependencies --------------------------------------------------
echo "== Installing Python dependencies =="
python -m pip install --quiet -r "$ROOT/requirements.txt"

# --- 2. llama-server (Metal build via Homebrew) ------------------------------
if [[ -f "$BIN_DIR/llama-server" ]]; then
    echo "[skip] llama-server already present"
else
    echo "== Fetching llama-server (Metal build) =="

    # Try Homebrew first — it's the cleanest Metal build on macOS
    if command -v brew &>/dev/null; then
        brew install llama.cpp
        BREW_SERVER="$(brew --prefix llama.cpp)/bin/llama-server"
        if [[ -f "$BREW_SERVER" ]]; then
            cp "$BREW_SERVER" "$BIN_DIR/llama-server"
            chmod +x "$BIN_DIR/llama-server"
            echo "[ok] llama-server (Homebrew Metal) -> $BIN_DIR"
        fi
    fi

    # Fall back to downloading a release binary if Homebrew didn't work
    if [[ ! -f "$BIN_DIR/llama-server" ]]; then
        echo "Homebrew not available or binary not found — downloading from GitHub releases"
        ARCH="$(uname -m)"   # arm64 or x86_64
        RELEASES=$(curl -s -H "User-Agent: reconciliation-engine-build" \
            "https://api.github.com/repos/ggerganov/llama.cpp/releases?per_page=5")
        ASSET_URL=$(echo "$RELEASES" | python3 -c "
import sys, json, re
data = json.load(sys.stdin)
pattern = re.compile(r'bin-macos-$ARCH\.zip$'.replace('$ARCH', '${ARCH}'))
for rel in data:
    for a in rel.get('assets', []):
        if pattern.search(a['name']):
            print(a['browser_download_url'])
            sys.exit(0)
")
        if [[ -z "$ASSET_URL" ]]; then
            echo "ERROR: could not find a bin-macos-${ARCH} asset in recent llama.cpp releases." >&2
            exit 1
        fi
        ZIP="$TMPDIR/llamacpp-macos.zip"
        EXTRACT="$TMPDIR/llamacpp-macos"
        download_if_missing "$ASSET_URL" "$ZIP" "llama.cpp macos-${ARCH}"
        rm -rf "$EXTRACT" && mkdir -p "$EXTRACT"
        unzip -q "$ZIP" -d "$EXTRACT"
        SERVER_BIN="$(find "$EXTRACT" -name "llama-server" -type f | head -1)"
        if [[ -z "$SERVER_BIN" ]]; then
            echo "ERROR: llama-server not found in downloaded archive." >&2; exit 1
        fi
        cp "$SERVER_BIN" "$BIN_DIR/llama-server"
        chmod +x "$BIN_DIR/llama-server"
        # Copy any dylibs sitting alongside the binary
        find "$(dirname "$SERVER_BIN")" -name "*.dylib" -exec cp {} "$BIN_DIR/" \;
        echo "[ok] llama-server + dylibs -> $BIN_DIR"
    fi
fi

# --- 3. GGUF model weights ---------------------------------------------------
echo "== Fetching model weights (~2.5 GB, first run only) =="
download_if_missing \
    "https://huggingface.co/Qwen/Qwen2.5-3B-Instruct-GGUF/resolve/main/qwen2.5-3b-instruct-q4_k_m.gguf" \
    "$MODEL_DIR/qwen2.5-3b-instruct-q4_k_m.gguf" \
    "Qwen2.5-3B chat model"

download_if_missing \
    "https://huggingface.co/Qwen/Qwen3-Embedding-0.6B-GGUF/resolve/main/Qwen3-Embedding-0.6B-Q8_0.gguf" \
    "$MODEL_DIR/Qwen3-Embedding-0.6B-Q8_0.gguf" \
    "Qwen3-Embedding-0.6B model"

# --- 4. PyInstaller build ----------------------------------------------------
echo "== Running PyInstaller =="
rm -rf "$ROOT/build" "$ROOT/dist"
python -m PyInstaller --noconfirm "$ROOT/reconciliation_macos.spec"

# --- 5. Zip for distribution -------------------------------------------------
APP_PATH="$ROOT/dist/ReconciliationEngine.app"
if [[ ! -d "$APP_PATH" ]]; then
    echo "ERROR: PyInstaller did not produce dist/ReconciliationEngine.app" >&2; exit 1
fi

ZIP_OUT="$ROOT/dist/ReconciliationEngine-macos.zip"
echo "== Zipping $ZIP_OUT =="
cd "$ROOT/dist"
zip -r --symlinks "ReconciliationEngine-macos.zip" "ReconciliationEngine.app"
cd "$ROOT"

echo ""
echo "Done. Distributable: $ZIP_OUT"
echo "Users unzip and double-click ReconciliationEngine.app"
