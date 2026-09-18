# Demo assets

`demo.gif` is the first thing anyone opening the repo sees, so it shows the product
working rather than the terminal: two PDFs go in, and `UNEXPLAINED` lands on `0.00`.

It is **generated, not hand-recorded**, so it cannot drift away from what the engine
actually does. `scripts/make_demo_gif.py` drives the real UI in a headless browser
against the real sample statements, screenshots each step, and assembles the frames
with Pillow. Re-run it whenever the interface changes:

```bash
source .venv/bin/activate
uvicorn app.main:app --port 8123          # in another terminal
python scripts/make_demo_gif.py
```

It asserts that the summary reads `0.00` before writing the file, so a regression
fails the capture instead of quietly publishing a GIF that shows the wrong number.
