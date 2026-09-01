<#
.SYNOPSIS
  Build the Windows desktop app into a distributable zip.

.DESCRIPTION
  Fetches the assets that are too large to commit (the Vulkan llama.cpp build and
  the two GGUF models), then runs PyInstaller and zips the result. Downloads are
  skipped when the files already exist, so re-runs are cheap.

  Output: dist\ReconciliationEngine-windows.zip — unzip and run
  ReconciliationEngine\ReconciliationEngine.exe. Fully offline; no install needed.

.NOTES
  Run from the repo root in the project's virtualenv:
    .\build_windows.ps1
#>

$ErrorActionPreference = "Stop"
$root     = $PSScriptRoot
$binDir   = Join-Path $root "desktop\bin"
$modelDir = Join-Path $root "desktop\models"

New-Item -ItemType Directory -Force -Path $binDir, $modelDir | Out-Null

function Get-FileIfMissing {
    param([string]$Url, [string]$Destination, [string]$Label)
    if (Test-Path $Destination) {
        Write-Host "[skip] $Label already present"
        return
    }
    Write-Host "[download] $Label -> $Destination"
    # curl.exe (ships with Windows 10/11) handles large files, IPv4/IPv6 fallback,
    # and automatic resume better than BITS or Invoke-WebRequest for HuggingFace.
    # -4: prefer IPv4 (HuggingFace's IPv6 endpoints can be unreliable on some networks)
    # -C -: resume a partial download if the file already exists
    # -L: follow redirects (HuggingFace CDN redirects the download)
    # --retry 5: retry transient failures
    & curl.exe -4 -L -C - --retry 5 --retry-delay 3 -o $Destination $Url
    if ($LASTEXITCODE -ne 0) { throw "curl.exe failed (exit $LASTEXITCODE) downloading $Label" }
}

# --- 1. Python dependencies -------------------------------------------------
Write-Host "== Installing Python dependencies =="
python -m pip install --quiet -r (Join-Path $root "requirements.txt")

# --- 2. Vulkan llama.cpp build ----------------------------------------------
# One Vulkan binary drives NVIDIA / AMD / Intel GPUs, and runs on CPU where there
# is no usable device. Pulled from the latest ggerganov/llama.cpp build release.
# Note: the "latest" release tag has no binaries; we take the first release in the
# list that has assets (llama.cpp publishes numbered builds like b10673).
if (-not (Test-Path (Join-Path $binDir "llama-server.exe"))) {
    Write-Host "== Fetching Vulkan llama.cpp build =="
    $releases = Invoke-RestMethod -Uri "https://api.github.com/repos/ggerganov/llama.cpp/releases?per_page=5" `
        -Headers @{ "User-Agent" = "reconciliation-engine-build" }
    $asset = $null
    foreach ($rel in $releases) {
        $asset = $rel.assets | Where-Object { $_.name -match "bin-win-vulkan-x64\.zip$" } | Select-Object -First 1
        if ($asset) { break }
    }
    if (-not $asset) { throw "Could not find a bin-win-vulkan-x64 asset in recent llama.cpp releases." }

    $zipPath = Join-Path $env:TEMP $asset.name
    Get-FileIfMissing -Url $asset.browser_download_url -Destination $zipPath -Label $asset.name
    $extractDir = Join-Path $env:TEMP "llamacpp-vulkan"
    Remove-Item -Recurse -Force $extractDir -ErrorAction SilentlyContinue
    Expand-Archive -Path $zipPath -DestinationPath $extractDir

    # Copy llama-server.exe and all DLLs from the same directory (runtime deps must
    # sit alongside the exe so the Vulkan loader finds them).
    $serverExe = Get-ChildItem -Recurse -Path $extractDir -Filter "llama-server.exe" | Select-Object -First 1
    if (-not $serverExe) { throw "llama-server.exe not found in the downloaded archive." }
    $payload = Get-ChildItem -Path "$($serverExe.DirectoryName)\*" -Include "*.exe", "*.dll"
    Copy-Item -Path $payload.FullName -Destination $binDir -Force
    Write-Host "[ok] llama-server + runtime DLLs -> $binDir"
} else {
    Write-Host "[skip] llama-server.exe already present"
}

# --- 3. GGUF model weights --------------------------------------------------
Write-Host "== Fetching model weights (~2.5GB, first run only) =="
Get-FileIfMissing `
    -Url "https://huggingface.co/Qwen/Qwen2.5-3B-Instruct-GGUF/resolve/main/qwen2.5-3b-instruct-q4_k_m.gguf" `
    -Destination (Join-Path $modelDir "qwen2.5-3b-instruct-q4_k_m.gguf") `
    -Label "Qwen2.5-3B chat model"
Get-FileIfMissing `
    -Url "https://huggingface.co/Qwen/Qwen3-Embedding-0.6B-GGUF/resolve/main/Qwen3-Embedding-0.6B-Q8_0.gguf" `
    -Destination (Join-Path $modelDir "Qwen3-Embedding-0.6B-Q8_0.gguf") `
    -Label "Qwen3-Embedding-0.6B model"

# --- 4. PyInstaller build ---------------------------------------------------
Write-Host "== Running PyInstaller =="
Remove-Item -Recurse -Force (Join-Path $root "build"), (Join-Path $root "dist") -ErrorAction SilentlyContinue
python -m PyInstaller --noconfirm (Join-Path $root "reconciliation.spec")

# --- 5. Zip for distribution ------------------------------------------------
$appDir = Join-Path $root "dist\ReconciliationEngine"
if (-not (Test-Path $appDir)) { throw "PyInstaller did not produce dist\ReconciliationEngine." }
$zipOut = Join-Path $root "dist\ReconciliationEngine-windows.zip"
Write-Host "== Zipping $zipOut =="
Compress-Archive -Path $appDir -DestinationPath $zipOut -Force

Write-Host ""
Write-Host "Done. Distributable: $zipOut"
Write-Host "Users unzip it and run ReconciliationEngine\ReconciliationEngine.exe"
