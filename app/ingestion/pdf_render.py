from __future__ import annotations

import base64
import io
import logging
from typing import Iterable

logger = logging.getLogger(__name__)

# Enough to read a statement's 8pt type on screen without making the payload heavy.
# The page is delivered whole and the browser shows a band of it, so this is the
# resolution the reviewer actually reads at.
DEFAULT_SCALE = 150 / 72

# A statement is a handful of pages; a mis-drop could be hundreds. Rendering is the
# one part of the pipeline whose cost grows with page count and lands in the HTTP
# response, so it is bounded. Citations still carry page and line for pages beyond
# the cap — only the picture is dropped, never the reference.
MAX_RENDERED_PAGES = 12


def render_pages(
    pdf_bytes: bytes, pages: Iterable[int], *, scale: float = DEFAULT_SCALE
) -> dict[int, str]:
    """Render 1-based page numbers to base64 PNG data URLs.

    Only the pages a citation actually points at are rendered — a reviewer never
    opens a page nothing was flagged on, and rendering it would cost the payload for
    nothing.

    Uses pypdfium2 rather than PyMuPDF deliberately: PyMuPDF is AGPL-3.0, which is a
    poor foundation for software distributed to clients, while pypdfium2 is
    BSD/Apache. Best effort — a page that fails to render leaves the citation's page,
    line and text intact.
    """
    wanted = sorted({p for p in pages if p and p > 0})[:MAX_RENDERED_PAGES]
    if not wanted:
        return {}

    try:
        import pypdfium2 as pdfium
    except ImportError:  # noqa: BLE001 - the picture is optional, the citation is not
        logger.info("pypdfium2 not installed; source citations will have no page image")
        return {}

    rendered: dict[int, str] = {}
    try:
        document = pdfium.PdfDocument(io.BytesIO(pdf_bytes))
        try:
            for number in wanted:
                if number > len(document):
                    continue
                image = document[number - 1].render(scale=scale).to_pil()
                # Statements are black type on white. Grayscale cuts the encoded
                # page from 437 KB to 190 KB with nothing lost at this size —
                # measured — and the image travels inside the JSON response.
                image = image.convert("L")
                buffer = io.BytesIO()
                image.save(buffer, format="PNG", optimize=True)
                rendered[number] = (
                    "data:image/png;base64,"
                    + base64.b64encode(buffer.getvalue()).decode("ascii")
                )
        finally:
            document.close()
    except Exception:  # noqa: BLE001
        logger.exception("Could not render PDF pages; citations keep their line numbers")
        return rendered

    logger.info("Rendered %d page image(s) for source highlighting", len(rendered))
    return rendered
