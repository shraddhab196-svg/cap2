from __future__ import annotations

import ipaddress
import logging
import os
import re
import socket
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup, NavigableString
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
# Some sites (e.g. dhan.ai) answer 406 to python-requests' default headers, so send what a browser-based reader would.
REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; CoverLetterAI/1.0)",
    "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en",
}

# Research size caps (characters, cut at a word boundary).
MAIN_PAGE_MAX_CHARS = 3000
EXTRA_PAGE_MAX_CHARS = 1500
RESEARCH_MAX_CHARS = 6000
MAX_EXTRA_PAGES = 2
EXTRA_PAGE_TIMEOUT_SECONDS = 6
# Extra same-site pages worth reading, in priority order.
EXTRA_PAGE_KEYWORDS = (
    ("about", "mission", "values"),
    ("news", "blog", "press"),
    ("career", "jobs"),
)


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
            response = requests.get(url, timeout=timeout, allow_redirects=False, headers=REQUEST_HEADERS)
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

    text_tags = ["p", "li", "h1", "h2", "h3", "h4"]
    container_tags = ["article", "section", "main"]
    text_blocks: list[str] = []
    for element in soup.find_all(text_tags + container_tags):
        if element.name in container_tags:
            # Only the container's own loose text; text inside nested text elements/containers is taken from those.
            own_strings = [
                string for string in element.find_all(string=True)
                if type(string) is NavigableString and string.find_parent(text_tags + container_tags) is element
            ]
            text = " ".join(" ".join(own_strings).split())
        elif element.find_parent(text_tags) is not None:
            continue  # already included via the enclosing text element (e.g. <p> inside <li>)
        else:
            text = " ".join(element.get_text(" ", strip=True).split())
        if text:
            text_blocks.append(text)

    # Each piece of text once, in page order (also drops blocks the page itself repeats verbatim).
    cleaned = "\n\n".join(dict.fromkeys(text_blocks))
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    cleaned = cleaned.strip()

    if not cleaned:
        raise ValueError(f"No readable company content could be extracted from: {company_url}")

    return cleaned


def cap_text(text: str, limit: int) -> str:
    """Shorten text to at most `limit` characters, cutting at the last word boundary."""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    boundary = max(cut.rfind(" "), cut.rfind("\n"))
    return (cut[:boundary] if boundary > 0 else cut).rstrip()


def _host_key(hostname: str | None) -> str:
    host = (hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def _url_key(url: str) -> str:
    parts = urlsplit(url)
    return f"{_host_key(parts.hostname)}{parts.path.rstrip('/')}?{parts.query}"


def find_extra_page_urls(page_url: str, html: str, limit: int = MAX_EXTRA_PAGES) -> list[str]:
    """Same-site links that look like about / news / careers pages (one per kind, in that priority)."""
    site = _host_key(urlsplit(page_url).hostname)
    seen = {_url_key(page_url)}
    by_kind: list[list[str]] = [[] for _ in EXTRA_PAGE_KEYWORDS]
    for link in BeautifulSoup(html, "html.parser").find_all("a", href=True):
        absolute = urljoin(page_url, link["href"].strip())
        parts = urlsplit(absolute)
        if parts.scheme not in ("http", "https") or _host_key(parts.hostname) != site:
            continue
        url = urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))
        key = _url_key(url)
        if key in seen:
            continue
        label = f"{link.get_text(' ', strip=True)} {parts.path}".lower()
        for kind, words in enumerate(EXTRA_PAGE_KEYWORDS):
            if any(word in label for word in words):
                by_kind[kind].append(url)
                seen.add(key)
                break
    return [urls[0] for urls in by_kind if urls][:limit]


def extra_pages_enabled() -> bool:
    return os.getenv("COMPANY_RESEARCH_EXTRA_PAGES", "1").strip() != "0"


def research_company(company_url: str) -> dict[str, Any]:
    """Fetch the company's main page plus up to two same-site about/news/careers pages, capped and labeled by source."""
    html = fetch_company_html(company_url)
    sections = [(company_url, cap_text(extract_company_text(company_url, html), MAIN_PAGE_MAX_CHARS))]

    skipped = 0
    if extra_pages_enabled():
        for url in find_extra_page_urls(company_url, html):
            try:
                # fetch_company_html applies the public-address (SSRF) check to every hop.
                page_text = extract_company_text(url, fetch_company_html(url, timeout=EXTRA_PAGE_TIMEOUT_SECONDS))
            except Exception:  # an optional extra page; skip it quietly
                skipped += 1
                continue
            sections.append((url, cap_text(page_text, EXTRA_PAGE_MAX_CHARS)))

    research = cap_text("\n\n".join(f"### Source: {url}\n{text}" for url, text in sections), RESEARCH_MAX_CHARS)
    logger.info("Company research complete: pages=%d skipped=%d chars=%d", len(sections), skipped, len(research))
    return {
        "company_url": company_url,
        "company_research": research,
        "pages": [url for url, _ in sections],
    }
