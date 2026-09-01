"""Start, health-gate, and stop the two bundled llama.cpp model servers.

The reconciliation pipeline talks to two `llama-server` processes over HTTP (chat
on 8080, embeddings on 8081 — see `app/local_llm.py`). In the developer setup a
human starts those in two terminals; in the packaged app this class does it, using
the GPU-enabled binary and model weights bundled by `reconciliation.spec`.

Design notes:
- GPU offload is requested with `-ngl` (number of layers on the GPU). The Vulkan
  build offloads to any NVIDIA/AMD/Intel GPU and silently keeps layers on the CPU
  when there is no usable device, so `-ngl 99` is safe on CPU-only machines too.
- Readiness is gated on each server's own `/health` endpoint, because a model can
  take 10-30s to load and the UI must not accept a reconciliation before then.
- Teardown is deliberately thorough (kill a whole Windows process tree, plus an
  `atexit` backstop) so closing the window never leaves an orphaned `llama-server`
  holding a GPU and a port.
"""

from __future__ import annotations

import atexit
import logging
import os
import subprocess
import sys
import time
from urllib.parse import urlparse

import httpx

from app.local_llm import DEFAULT_EMBEDDING_SERVER_URL, DEFAULT_SERVER_URL
from desktop import resources

logger = logging.getLogger("desktop.llama")

# Offload every layer to the GPU by default; llama.cpp keeps whatever doesn't fit
# (or all of it, on a machine with no GPU) on the CPU. Override with LLAMA_NGL=0 to
# force pure CPU, e.g. for troubleshooting a GPU driver.
DEFAULT_NGL = "99"

# Context window for the chat model, matching the README's `-c 8192`. Raise if a
# long statement gets truncated during extraction.
CHAT_CONTEXT = "8192"

# A cold start loads ~2.5GB of weights from disk into VRAM/RAM; be generous.
READY_TIMEOUT_SECONDS = 180.0


def _port(url: str) -> int:
    parsed = urlparse(url)
    return parsed.port or (443 if parsed.scheme == "https" else 80)


CHAT_PORT = _port(DEFAULT_SERVER_URL)
EMBEDDING_PORT = _port(DEFAULT_EMBEDDING_SERVER_URL)


class LlamaServerError(RuntimeError):
    """A model server failed to launch or never became healthy."""


class LlamaSupervisor:
    """Owns the lifetime of both `llama-server` processes."""

    def __init__(self, ngl: str | None = None):
        self._ngl = ngl or os.environ.get("LLAMA_NGL", DEFAULT_NGL)
        self._procs: list[subprocess.Popen] = []
        self._started = False

    def start(self) -> None:
        """Launch both servers. Idempotent within one process."""
        if self._started:
            return

        binary = resources.llama_server_binary()
        if not binary.exists():
            raise LlamaServerError(
                f"Bundled llama-server not found at {binary}. On a source checkout "
                "run build_windows.ps1 (or place the Vulkan build there manually)."
            )

        # atexit is the backstop for crashes / unexpected exits; the launcher also
        # calls stop() explicitly on a clean window close.
        atexit.register(self.stop)

        self._procs.append(
            self._spawn(
                [
                    str(binary),
                    "-m", str(resources.chat_model()),
                    "--port", str(CHAT_PORT),
                    "-c", CHAT_CONTEXT,
                    "-ngl", self._ngl,
                ],
                label="chat",
            )
        )
        self._procs.append(
            self._spawn(
                [
                    str(binary),
                    "-m", str(resources.embedding_model()),
                    "--port", str(EMBEDDING_PORT),
                    "--embeddings",
                    "-ngl", self._ngl,
                ],
                label="embeddings",
            )
        )
        self._started = True
        logger.info(
            "Launched llama-server: chat :%d, embeddings :%d (-ngl %s)",
            CHAT_PORT, EMBEDDING_PORT, self._ngl,
        )

    def _spawn(self, cmd: list[str], *, label: str) -> subprocess.Popen:
        creationflags = 0
        if sys.platform == "win32":
            # CREATE_NO_WINDOW: no console window flashes on launch.
            # CREATE_NEW_PROCESS_GROUP: lets stop() signal the whole group cleanly.
            creationflags = (
                subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
                | subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
            )
        try:
            return subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creationflags,
            )
        except OSError as exc:
            raise LlamaServerError(f"Failed to launch {label} server: {exc}") from exc

    def wait_until_ready(self, timeout: float = READY_TIMEOUT_SECONDS) -> bool:
        """Block until both servers answer /health, or `timeout` elapses.

        Returns True if both came up. On timeout returns False rather than raising:
        the pipeline degrades gracefully (unreachable server -> rows go to the CA
        review queue), so a slow/failed model load must not crash the app.
        """
        deadline = time.monotonic() + timeout
        pending = {CHAT_PORT, EMBEDDING_PORT}
        while pending and time.monotonic() < deadline:
            self._assert_alive()
            for port in list(pending):
                if _health_ok(port):
                    pending.discard(port)
                    logger.info("llama-server :%d ready", port)
            if pending:
                time.sleep(0.5)
        if pending:
            logger.warning("llama-server not ready on ports %s within %.0fs", pending, timeout)
        return not pending

    def is_ready(self) -> bool:
        """Non-blocking readiness check for both servers (drives GET /ready)."""
        return _health_ok(CHAT_PORT) and _health_ok(EMBEDDING_PORT)

    def _assert_alive(self) -> None:
        for proc in self._procs:
            if proc.poll() is not None:
                raise LlamaServerError(
                    f"llama-server exited early with code {proc.returncode} — check "
                    "the GPU driver, or run with LLAMA_NGL=0 to force CPU."
                )

    def stop(self) -> None:
        """Terminate both servers and their children. Safe to call more than once."""
        for proc in self._procs:
            if proc.poll() is not None:
                continue
            _kill_tree(proc)
        self._procs.clear()
        self._started = False


def _health_ok(port: int) -> bool:
    try:
        response = httpx.get(f"http://127.0.0.1:{port}/health", timeout=2.0)
        return response.status_code == 200
    except httpx.HTTPError:
        return False


def _kill_tree(proc: subprocess.Popen) -> None:
    """Kill a process and any children it spawned.

    llama-server itself doesn't fork, but taskkill /T is cheap insurance and the
    portable branch keeps this working if the app is ever run from source on
    another OS.
    """
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        else:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
    except OSError:
        # Already gone, or we lack permission — nothing more we can do here.
        pass
