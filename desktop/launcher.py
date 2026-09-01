"""Desktop entry point: wire the FastAPI backend and model servers to a native window.

Run from source with `python -m desktop.launcher`; this same module is the frozen
app's entry point (see `reconciliation.spec`). Flow:

  1. Reserve a free loopback port for FastAPI.
  2. Start the two llama.cpp model servers (non-blocking; they load in the
     background while the window comes up).
  3. Run uvicorn in a daemon thread bound to 127.0.0.1:<port>.
  4. Open a pywebview window on that URL — the same UI as the browser build, minus
     the address bar. The window's loading overlay polls GET /ready and reveals the
     upload form once both models answer.
  5. When the user closes the window, tear everything down.

Everything stays on loopback; nothing is exposed off the machine.
"""

from __future__ import annotations

import logging
import socket
import threading
import time

import httpx
import uvicorn
import webview

from desktop.llama_supervisor import LlamaSupervisor

logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(name)s: %(message)s")
logger = logging.getLogger("desktop.launcher")

WINDOW_TITLE = "Reconciliation Engine"


def _free_port() -> int:
    """Ask the OS for an unused loopback port, then hand it to uvicorn.

    A brief close-then-reopen race is acceptable here: this is a single desktop
    session, not a server accepting arbitrary connections.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_for_http(url: str, timeout: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if httpx.get(url, timeout=2.0).status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    return False


def _serve_backend(port: int) -> uvicorn.Server:
    """Start uvicorn in a daemon thread and return the running server handle."""
    # Imported here (not at module top) so the model servers are already launching
    # by the time the app package — and its heavier deps — get imported.
    from app.main import app

    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="uvicorn", daemon=True)
    thread.start()
    return server


def main() -> None:
    supervisor = LlamaSupervisor()
    port = _free_port()
    server: uvicorn.Server | None = None
    try:
        # Kick off the slow part (model load) first, then bring up the web server
        # while the weights stream in.
        supervisor.start()

        server = _serve_backend(port)
        url = f"http://127.0.0.1:{port}"
        if not _wait_for_http(f"{url}/health"):
            raise RuntimeError("FastAPI backend did not start in time")

        logger.info("UI at %s (backend up; models loading in background)", url)
        webview.create_window(WINDOW_TITLE, url, width=1280, height=860, min_size=(900, 600))
        # Blocks on the main thread until the window is closed.
        webview.start()
    finally:
        logger.info("Shutting down")
        if server is not None:
            server.should_exit = True
        supervisor.stop()


if __name__ == "__main__":
    main()
