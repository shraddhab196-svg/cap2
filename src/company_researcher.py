from __future__ import annotations

import ipaddress
import logging
import re
import socket
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

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


MAX_REDIRECTS = 5


def check_public_url(url: str) -> None:
    """Refuse URLs that point at the server itself or a private network (SSRF): users type these URLs."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError(f"Invalid company URL: {url}")
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(parts.hostname, parts.port or 443, proto=socket.IPPROTO_TCP)}
    except (socket.gaierror, UnicodeError, ValueError) as exc:
        raise ValueError(f"Could not find the company website: {url}") from exc
    for address in addresses:
        if not ipaddress.ip_address(address.split("%")[0]).is_global:
            raise ValueError(f"That address is not a public website: {url}")


def fetch_company_html(company_url: str, timeout: int = 15) -> str:
    """Fetch the company website HTML with basic validation and timeout handling."""
    if not company_url or not company_url.strip():
        raise ValueError("Company URL is required.")

    try:
        # Follow redirects by hand so every hop gets the same public-address check.
        # ponytail: checks DNS before connecting; a DNS-rebinding attacker could still swap the address in between.
        # Pin the resolved IP in the connection if this app ever runs next to sensitive internal services.
        url = company_url.strip()
        for _ in range(MAX_REDIRECTS + 1):
            check_public_url(url)
            response = requests.get(url, timeout=timeout, allow_redirects=False)
            if not response.is_redirect:
                break
            url = urljoin(url, response.headers["location"])
        else:
            raise ValueError(f"That website redirects too many times: {company_url}")
        response.raise_for_status()
    except requests.exceptions.MissingSchema as exc:
        raise ValueError(f"Invalid company URL: {company_url}") from exc
    except requests.exceptions.InvalidURL as exc:
        raise ValueError(f"Invalid company URL: {company_url}") from exc
    except requests.exceptions.Timeout as exc:
        raise TimeoutError(f"Request timed out while fetching the company website: {company_url}") from exc
    except requests.exceptions.RequestException as exc:
        raise ValueError(f"We couldn't open that website. Check the address, or try the company's main page: {company_url}") from exc

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
