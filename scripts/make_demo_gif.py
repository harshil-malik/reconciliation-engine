"""Generate docs/demo.gif by driving the real UI against the real sample statements.

A hand-recorded screencast rots: the interface moves on, the numbers change, and the
GIF at the top of the README keeps promising something the engine no longer does.
This drives the actual app in a headless browser, so the demo is only ever as true as
the code — and it asserts the balancing proof before writing the file, so a
regression fails the capture instead of quietly publishing the wrong number.

    source .venv/bin/activate
    uvicorn app.main:app --port 8123      # in another terminal
    python scripts/make_demo_gif.py

Requires `playwright` and `pillow`, plus `playwright install chromium` once.
"""

from __future__ import annotations

import asyncio
import pathlib
import sys
import tempfile

from PIL import Image
from playwright.async_api import async_playwright

ROOT = pathlib.Path(__file__).resolve().parent.parent
BANK = ROOT / "sample_data" / "hdfc" / "hdfc_bank_statement.pdf"
LEDGER = ROOT / "sample_data" / "hdfc" / "hdfc_ledger.pdf"
GIF = ROOT / "docs" / "demo.gif"
URL = "http://127.0.0.1:8123"

# The whole point of the image. If the engine stops balancing, the capture fails.
EXPECTED_UNEXPLAINED = "0.00"

TARGET_WIDTH = 1000
MAX_BYTES = 5 * 1024 * 1024


async def _capture(directory: pathlib.Path) -> list[tuple[pathlib.Path, float]]:
    frames: list[tuple[pathlib.Path, float]] = []

    async def shot(page, name: str, hold: float = 1.0) -> None:
        path = directory / f"{len(frames):03d}_{name}.png"
        await page.screenshot(path=str(path))
        frames.append((path, hold))

    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page(
            viewport={"width": 1280, "height": 820}, device_scale_factor=2
        )
        await page.goto(URL, wait_until="networkidle")
        # The readiness overlay polls /ready and hides itself once the app answers.
        try:
            await page.wait_for_selector("#overlay", state="hidden", timeout=20_000)
        except Exception:
            pass
        await page.wait_for_timeout(800)
        await shot(page, "empty", hold=2)

        await page.set_input_files("#bank-input", str(BANK))
        await page.wait_for_timeout(700)
        await shot(page, "bank_added")

        await page.set_input_files("#ledger-input", str(LEDGER))
        await page.wait_for_timeout(900)
        await shot(page, "ledger_added", hold=2)

        # Both uploads are PDFs, so both pickers must be answered before the run
        # button enables — see updateReconcileEnabled() in app/static/index.html.
        await page.select_option("#bank-template-select", "hdfc")
        await page.wait_for_timeout(400)
        await page.select_option("#ledger-template-select", "auto")
        await page.wait_for_timeout(700)
        await shot(page, "template", hold=2)

        await page.click("#reconcile")
        await page.wait_for_timeout(600)
        await shot(page, "running")

        await page.wait_for_function(
            "document.querySelector('#stat-unexplained')"
            " && document.querySelector('#stat-unexplained').textContent.trim() !== '—'",
            timeout=120_000,
        )
        await page.wait_for_timeout(900)

        shown = (await page.inner_text("#stat-unexplained")).strip()
        if EXPECTED_UNEXPLAINED not in shown:
            raise SystemExit(
                f"UNEXPLAINED reads {shown!r}, expected {EXPECTED_UNEXPLAINED!r}. "
                "The sample no longer balances — fix that before publishing a GIF of it."
            )

        await shot(page, "result", hold=4)
        await page.mouse.wheel(0, 420)
        await page.wait_for_timeout(700)
        await shot(page, "scrolled", hold=3)

        await browser.close()

    return frames


def _assemble(frames: list[tuple[pathlib.Path, float]]) -> None:
    images, durations = [], []
    for path, hold in frames:
        im = Image.open(path).convert("RGB")
        width, height = im.size
        im = im.resize(
            (TARGET_WIDTH, round(height * TARGET_WIDTH / width)), Image.LANCZOS
        )
        # An adaptive palette per frame: a flat off-white UI bands badly on a shared one.
        images.append(im.quantize(colors=128, dither=Image.FLOYDSTEINBERG))
        durations.append(int(hold * 1000))

    GIF.parent.mkdir(exist_ok=True)
    images[0].save(
        GIF,
        save_all=True,
        append_images=images[1:],
        duration=durations,
        loop=0,
        optimize=True,
        disposal=2,
    )

    size = GIF.stat().st_size
    if size > MAX_BYTES:
        raise SystemExit(
            f"{GIF.name} is {size / 1024 / 1024:.1f} MB, over the {MAX_BYTES // 1024 // 1024} MB "
            "budget — GitHub renders it inline but it stalls on mobile. Lower TARGET_WIDTH."
        )
    print(f"{GIF} — {len(images)} frames, {sum(durations) / 1000:.0f}s, {size / 1024:.0f} KB")


def main() -> int:
    for sample in (BANK, LEDGER):
        if not sample.exists():
            raise SystemExit(
                f"{sample} is missing. Run: python scripts/make_bank_samples.py"
            )
    with tempfile.TemporaryDirectory() as tmp:
        frames = asyncio.run(_capture(pathlib.Path(tmp)))
        _assemble(frames)
    return 0


if __name__ == "__main__":
    sys.exit(main())
