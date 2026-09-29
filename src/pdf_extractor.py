from __future__ import annotations

import logging
import re
from pathlib import Path

import fitz

logger = logging.getLogger(__name__)


def normalize_text(raw_text: str) -> str:
    """Normalize extracted PDF text while preserving paragraph separation."""
    if not raw_text:
        return ""

    text = raw_text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_pdf_text(pdf_path: Path) -> str:
    """Extract text from a single PDF, preserving page order."""
    document = fitz.open(pdf_path)
    page_texts: list[str] = []

    try:
        for page_number in range(document.page_count):
            page = document[page_number]
            raw_page_text = page.get_text("text")
            cleaned_page_text = normalize_text(raw_page_text)

            if cleaned_page_text:
                page_texts.append(cleaned_page_text)

        if not page_texts:
            logger.warning("No extractable text found in %s; skipping.", pdf_path.name)
            return ""

        return "\n\n".join(page_texts)
    finally:
        document.close()


def extract_all_pdfs(input_dir: Path | None = None, output_dir: Path | None = None) -> list[Path]:
    """Extract text from all PDFs in a directory and save each as a TXT file."""
    project_root = Path(__file__).resolve().parent.parent
    input_dir = input_dir or (project_root / "cover_letters")
    output_dir = output_dir or (project_root / "extracted_letters")

    output_dir.mkdir(parents=True, exist_ok=True)
    pdf_files = sorted(input_dir.glob("*.pdf"))

    if not pdf_files:
        logger.warning("No PDF files found in %s", input_dir)
        return []

    extracted_files: list[Path] = []

    for pdf_path in pdf_files:
        logger.info("Processing %s", pdf_path.name)

        try:
            extracted_text = extract_pdf_text(pdf_path)
        except Exception as exc:
            logger.exception("Failed to extract %s: %s", pdf_path.name, exc)
            continue

        if not extracted_text:
            continue

        txt_path = output_dir / f"{pdf_path.stem}.txt"
        txt_path.write_text(extracted_text, encoding="utf-8")
        extracted_files.append(txt_path)
        logger.info("Saved extracted text to %s", txt_path.name)

    return extracted_files


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    project_root = Path(__file__).resolve().parent.parent
    input_dir = project_root / "cover_letters"
    output_dir = project_root / "extracted_letters"

    extracted_files = extract_all_pdfs(input_dir=input_dir, output_dir=output_dir)

    if not extracted_files:
        logger.warning("No cover letters were extracted.")
        return 0

    logger.info("Extraction complete. %d file(s) created.", len(extracted_files))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
