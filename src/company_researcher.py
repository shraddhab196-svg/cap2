from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

logger = logging.getLogger(__name__)


def load_environment() -> None:
    """Load environment variables from the project root .env file if present."""
    project_root = Path(__file__).resolve().parent.parent
    env_path = project_root / ".env"
    if env_path.exists():
        load_dotenv(dotenv_path=env_path)
    else:
        load_dotenv()


def fetch_company_html(company_url: str, timeout: int = 15) -> str:
    """Fetch the company website HTML with basic validation and timeout handling."""
    if not company_url or not company_url.strip():
        raise ValueError("Company URL is required.")

    try:
        response = requests.get(company_url, timeout=timeout)
        response.raise_for_status()
    except requests.exceptions.MissingSchema as exc:
        raise ValueError(f"Invalid company URL: {company_url}") from exc
    except requests.exceptions.InvalidURL as exc:
        raise ValueError(f"Invalid company URL: {company_url}") from exc
    except requests.exceptions.Timeout as exc:
        raise TimeoutError(f"Request timed out while fetching the company website: {company_url}") from exc
    except requests.exceptions.RequestException as exc:
        raise RuntimeError(f"Failed to fetch the company website: {company_url}") from exc

    html = response.text
    if not html or not html.strip():
        raise ValueError(f"Website returned empty content for: {company_url}")

    return html


def extract_company_text(company_url: str, html: str) -> str:
    """Extract readable text from company HTML while removing navigation and non-content nodes."""
    soup = BeautifulSoup(html, "html.parser")

    for tag_name in ["script", "style", "noscript", "svg", "nav", "footer", "header"]:
        for tag in soup.find_all(tag_name):
            tag.decompose()

    text_blocks: list[str] = []
    for element in soup.find_all(["p", "li", "h1", "h2", "h3", "h4", "article", "section", "main"]):
        text = " ".join(element.get_text(" ", strip=True).split())
        if text:
            text_blocks.append(text)

    cleaned = "\n\n".join(text_blocks)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    cleaned = cleaned.strip()

    if not cleaned:
        raise ValueError(f"No readable company content could be extracted from: {company_url}")

    return cleaned


def research_company(company_url: str) -> dict[str, Any]:
    """Fetch and summarize company research text from the supplied website."""
    logger.info("Company URL: %s", company_url)
    logger.info("Researching company...")

    html = fetch_company_html(company_url)
    readable_text = extract_company_text(company_url, html)

    logger.info("Company research complete.")
    return {
        "company_url": company_url,
        "company_research": readable_text,
    }
