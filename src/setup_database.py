from __future__ import annotations

import json
import logging
from pathlib import Path

from database import create_test_user, save_cover_letter, save_style_profile

logger = logging.getLogger(__name__)


def load_letters_from_folder(folder: Path) -> list[tuple[str, str]]:
    """Read all extracted letters from the extracted_letters directory."""
    if not folder.exists():
        raise FileNotFoundError(f"Extracted letters directory does not exist: {folder}")

    items: list[tuple[str, str]] = []
    for path in sorted(folder.glob("*.txt")):
        content = path.read_text(encoding="utf-8")
        items.append((path.name, content))

    if not items:
        raise ValueError(f"No extracted text files were found in {folder}.")

    return items


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    try:
        project_root = Path(__file__).resolve().parent.parent
        extracted_dir = project_root / "extracted_letters"
        profile_path = project_root / "style_profile.json"

        user = create_test_user()
        user_id = str(user["id"])
        logger.info("Using test user: %s", user_id)

        for filename, content in load_letters_from_folder(extracted_dir):
            save_cover_letter(user_id, filename, content)
            logger.info("Saved cover letter to database: %s", filename)

        with profile_path.open("r", encoding="utf-8") as fh:
            style_profile = json.load(fh)

        save_style_profile(user_id, style_profile)
        logger.info("Saved style profile to database.")
        logger.info("Database setup complete.")
        return 0
    except Exception as exc:
        logger.exception("Database setup failed: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
