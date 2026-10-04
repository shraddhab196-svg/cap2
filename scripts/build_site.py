"""Build the public website (GitHub Pages) from the app's own templates.

    python scripts/build_site.py              # writes _site/
    SITE_URL=https://example.com/ APP_URL=https://app.example.com python scripts/build_site.py

The landing and legal pages are the same templates the app serves, so the site never drifts from the app.
Links to the app (log in, sign up) point at APP_URL; everything else stays on the site.
Only needs jinja2, so the deploy job doesn't install the app's dependencies.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
from pathlib import Path
from urllib.parse import urlsplit

from jinja2 import Environment, FileSystemLoader, StrictUndefined

ROOT = Path(__file__).resolve().parents[1]
SITE_PAGES = {"/": "", "/privacy": "privacy/", "/terms": "terms/", "/imprint": "imprint/"}
APP_PAGES = {"/login", "/signup", "/profile/setup"}
# Same copy as app.ERROR_COPY[404] (a test keeps them in sync).
NOT_FOUND = ("This page isn't in the draft.", "The link may be old, or the page moved. Nothing you did is lost.")


def load_config() -> dict:
    config = json.loads((ROOT / "site" / "config.json").read_text(encoding="utf-8"))
    for key in ("site_url", "app_url"):
        config[key] = os.getenv(key.upper()) or config[key]
    config["site_url"] = config["site_url"].rstrip("/") + "/"
    config["app_url"] = config["app_url"].rstrip("/")
    return config


def build(out: Path) -> list[str]:
    config = load_config()
    base = urlsplit(config["site_url"]).path  # "/cap2/" on a project page, "/" on a custom domain

    def url(path: str) -> str:
        if path in SITE_PAGES:
            return base + SITE_PAGES[path]
        if path in APP_PAGES:
            return config["app_url"] + path
        raise ValueError(f"No website or app page for {path!r}; add it to scripts/build_site.py")

    env = Environment(loader=FileSystemLoader(ROOT / "templates"), autoescape=True, undefined=StrictUndefined)
    env.globals.update(url=url, asset=lambda path: f"{base}static/{path}", site=config, site_url=None, user=None)

    shutil.rmtree(out, ignore_errors=True)
    pages = {
        "index.html": env.get_template("landing.html").render(site_url=config["site_url"]),
        "privacy/index.html": env.get_template("privacy.html").render(),
        "terms/index.html": env.get_template("terms.html").render(),
        "imprint/index.html": env.get_template("imprint.html").render(),
        "404.html": env.get_template("error.html").render(status_code=404, heading=NOT_FOUND[0], message=NOT_FOUND[1], signed_in=False),
    }
    for name, html in pages.items():
        # Root-relative links would point outside a project page (/cap2/), so every one must start with the base.
        stray = [link for link in re.findall(r'(?:href|src)="(/[^"]*)"', html) if not link.startswith(base)]
        if stray:
            raise ValueError(f"{name} has links outside the site: {stray}")
        (out / name).parent.mkdir(parents=True, exist_ok=True)
        (out / name).write_text(html, encoding="utf-8")

    shutil.copytree(ROOT / "static", out / "static")
    (out / ".nojekyll").write_text("", encoding="utf-8")
    (out / "robots.txt").write_text(f"User-agent: *\nAllow: /\nSitemap: {config['site_url']}sitemap.xml\n", encoding="utf-8")
    urls = "".join(f"  <url><loc>{config['site_url']}{page}</loc></url>\n" for page in SITE_PAGES.values())
    (out / "sitemap.xml").write_text(
        f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n{urls}</urlset>\n',
        encoding="utf-8",
    )

    missing = [key for key in ("operator_name", "contact_email", "governing_law", "address_lines") if not config.get(key)]
    return missing


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "_site"
    missing = build(out)
    print(f"Built {out}")
    if missing:
        print(f"WARNING: site/config.json is missing {', '.join(missing)}; the legal pages show 'to be added' for them.")
