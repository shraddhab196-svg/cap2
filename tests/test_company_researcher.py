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


if __name__ == "__main__":
    unittest.main()
