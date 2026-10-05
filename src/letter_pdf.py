from __future__ import annotations

import fitz  # PyMuPDF (already a project dependency, used for PDF text extraction)

PAGE_WIDTH, PAGE_HEIGHT = 595, 842  # A4 in points
MARGIN = 72
FONT_SIZE = 11
LINE_HEIGHT = 1.45


def build_cover_letter_pdf(letter: str) -> bytes:
    """Render plain cover-letter text into a simple A4 PDF, continuing onto new pages if needed."""
    document = fitz.open()
    font = fitz.Font("helv")
    remaining: str = (letter or "").strip() or " "

    while remaining:
        page = document.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
        writer = fitz.TextWriter(page.rect)
        overflow = writer.fill_textbox(
            fitz.Rect(MARGIN, MARGIN, PAGE_WIDTH - MARGIN, PAGE_HEIGHT - MARGIN),
            remaining,
            font=font,
            fontsize=FONT_SIZE,
            lineheight=LINE_HEIGHT,
        )
        writer.write_text(page)
        remaining = "\n".join(line for line, _width in overflow).strip() if overflow else ""

    pdf_bytes = document.tobytes()
    document.close()
    return pdf_bytes
