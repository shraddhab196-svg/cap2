import socket
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from src import company_researcher


def resolves_to(address):
    return lambda host, port, **_: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port))]


class PublicUrlTests(unittest.TestCase):
    """Users type the company URL, so the server must never fetch its own or a private network's addresses."""

    def test_private_and_local_addresses_are_refused(self):
        for address in ("127.0.0.1", "10.0.0.5", "192.168.1.1", "169.254.169.254", "0.0.0.0", "::1", "fd00::1"):
            with self.subTest(address=address), patch("socket.getaddrinfo", resolves_to(address)), \
                 patch("requests.get") as get:
                with self.assertRaisesRegex(ValueError, "not a public website"):
                    company_researcher.fetch_company_html("https://looks-innocent.example")
                get.assert_not_called()

    def test_non_web_schemes_are_refused(self):
        for url in ("file:///etc/passwd", "ftp://example.com", "gopher://example.com", "example.com"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                company_researcher.fetch_company_html(url)

    def test_redirect_to_a_private_address_is_refused(self):
        hosts = {"public.example": "93.184.216.34", "internal.example": "10.0.0.5"}
        redirect = SimpleNamespace(is_redirect=True, headers={"location": "http://internal.example/admin"})
        with patch("socket.getaddrinfo", lambda host, port, **_: resolves_to(hosts[host])(host, port)), \
             patch("requests.get", return_value=redirect) as get:
            with self.assertRaisesRegex(ValueError, "not a public website"):
                company_researcher.fetch_company_html("https://public.example")
        self.assertEqual(get.call_count, 1)

    def test_public_site_is_fetched(self):
        page = SimpleNamespace(is_redirect=False, text="<p>Hello</p>", raise_for_status=lambda: None)
        with patch("socket.getaddrinfo", resolves_to("93.184.216.34")), patch("requests.get", return_value=page) as get:
            self.assertEqual(company_researcher.fetch_company_html("https://public.example"), "<p>Hello</p>")
        # Browser-style headers: some sites (dhan.ai) answer 406 to python-requests' defaults.
        headers = get.call_args.kwargs["headers"]
        self.assertIn("text/html", headers["Accept"])
        self.assertTrue(headers["User-Agent"].startswith("Mozilla/5.0"))


PUBLIC_IP = "93.184.216.34"
MAIN_URL = "https://www.lumen-orchard.example/"


def page(body):
    return SimpleNamespace(is_redirect=False, text=f"<html><body><main>{body}</main></body></html>", raise_for_status=lambda: None)


def words(prefix, count):
    return " ".join(f"{prefix}{i}" for i in range(count))


MAIN_HTML_BODY = (
    f"<p>{words('main', 900)}</p>"
    '<a href="/about-us">About us</a>'
    '<a href="https://lumen-orchard.example/blog/">Blog</a>'
    '<a href="/careers">Careers</a>'
    '<a href="/about-us#team">About again (duplicate)</a>'
    '<a href="https://other-company.example/about">Partner about page</a>'
    '<a href="http://10.0.0.5/about">Intranet about</a>'
    '<a href="javascript:alert(1)">Press</a>'
)


class ExtraCompanyPagesTests(unittest.TestCase):
    """Change D: up to two more same-site pages, labeled by source, capped, and fetched with the same SSRF checks."""

    def setUp(self):
        self.pages = {
            MAIN_URL: page(MAIN_HTML_BODY),
            "https://www.lumen-orchard.example/about-us": page(f"<p>{words('about', 600)}</p>"),
            "https://lumen-orchard.example/blog/": page(f"<p>{words('blog', 600)}</p>"),
            "https://www.lumen-orchard.example/careers": page(f"<p>{words('career', 600)}</p>"),
        }
        self.fetched = []
        hosts = {"www.lumen-orchard.example": PUBLIC_IP, "lumen-orchard.example": PUBLIC_IP, "other-company.example": PUBLIC_IP, "10.0.0.5": "10.0.0.5"}

        def fake_get(url, **_):
            self.fetched.append(url)
            if url not in self.pages:
                raise company_researcher.requests.exceptions.ConnectionError("unreachable")
            return self.pages[url]

        for patcher in (
            patch("socket.getaddrinfo", lambda host, port, **_: resolves_to(hosts[host])(host, port)),
            patch("requests.get", side_effect=fake_get),
            patch.dict("os.environ", {"COMPANY_RESEARCH_EXTRA_PAGES": "1"}),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_extra_pages_fetched_in_priority_order_and_capped(self):
        result = company_researcher.research_company(MAIN_URL)
        self.assertEqual(result["pages"], [MAIN_URL, "https://www.lumen-orchard.example/about-us", "https://lumen-orchard.example/blog/"])
        research = result["company_research"]
        self.assertLessEqual(len(research), company_researcher.RESEARCH_MAX_CHARS)
        sections = research.split("### Source: ")[1:]
        self.assertEqual(len(sections), 3)
        main_text = sections[0].split("\n", 1)[1].strip()
        self.assertLessEqual(len(main_text), company_researcher.MAIN_PAGE_MAX_CHARS)
        for section in sections[1:]:
            self.assertLessEqual(len(section.split("\n", 1)[1].strip()), company_researcher.EXTRA_PAGE_MAX_CHARS)
        self.assertNotIn("career0", research)  # third kind not needed: two extra pages max

    def test_cut_at_word_boundary(self):
        capped = company_researcher.cap_text("alpha beta gamma delta", 13)
        self.assertEqual(capped, "alpha beta")
        self.assertEqual(company_researcher.cap_text("short", 100), "short")

    def test_other_hosts_private_hosts_and_non_web_links_are_never_fetched(self):
        company_researcher.research_company(MAIN_URL)
        for url in self.fetched:
            self.assertIn("lumen-orchard.example", url)
        self.assertNotIn("http://10.0.0.5/about", self.fetched)
        self.assertNotIn("https://other-company.example/about", self.fetched)

    def test_same_site_link_resolving_to_private_address_is_refused(self):
        # Extra pages go through fetch_company_html, so a same-site link that resolves privately is refused too.
        urls = company_researcher.find_extra_page_urls(MAIN_URL, self.pages[MAIN_URL].text)
        with patch("socket.getaddrinfo", resolves_to("10.0.0.9")), self.assertRaisesRegex(ValueError, "not a public website"):
            company_researcher.fetch_company_html(urls[0], timeout=company_researcher.EXTRA_PAGE_TIMEOUT_SECONDS)

    def test_failed_extra_page_is_skipped(self):
        del self.pages["https://www.lumen-orchard.example/about-us"]
        result = company_researcher.research_company(MAIN_URL)
        self.assertEqual(result["pages"], [MAIN_URL, "https://lumen-orchard.example/blog/"])
        self.assertIn("https://www.lumen-orchard.example/about-us", self.fetched)  # tried, then skipped

    def test_switch_off_fetches_only_the_main_page(self):
        with patch.dict("os.environ", {"COMPANY_RESEARCH_EXTRA_PAGES": "0"}):
            result = company_researcher.research_company(MAIN_URL)
        self.assertEqual(self.fetched, [MAIN_URL])
        self.assertEqual(result["pages"], [MAIN_URL])
        self.assertLessEqual(len(result["company_research"].split("\n", 1)[1]), company_researcher.MAIN_PAGE_MAX_CHARS)

    def test_source_lines_and_old_keys_preserved(self):
        result = company_researcher.research_company(MAIN_URL)
        self.assertEqual(result["company_url"], MAIN_URL)
        self.assertTrue(result["company_research"].startswith(f"### Source: {MAIN_URL}\n"))
        for url in result["pages"]:
            self.assertIn(f"### Source: {url}\n", result["company_research"])

    def test_research_logs_counts_only(self):
        with self.assertLogs("src.company_researcher", level="INFO") as logs:
            company_researcher.research_company(MAIN_URL)
        joined = "\n".join(logs.output)
        self.assertNotIn("lumen-orchard", joined)
        self.assertNotIn("main0", joined)


if __name__ == "__main__":
    unittest.main()
