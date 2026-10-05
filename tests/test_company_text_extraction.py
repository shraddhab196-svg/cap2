import unittest

from src.company_researcher import extract_company_text

JOB_PAGE = """
<html><body>
  <header>Site header</header>
  <nav>Menu</nav>
  <main>
    <h1>Software Developer für KI-Plattformen (w/m/d)</h1>
    <p>Das SCC ist der zentrale IT-Dienstleister des KIT.</p>
    <h2>Ihre Aufgaben</h2>
    <ul>
      <li>Entwicklung einer Plattform für KI-Modelle.</li>
      <li><p>Betrieb skalierbarer Kubernetes-Infrastruktur.</p></li>
    </ul>
    <h2>Ihr Profil</h2>
    <ul><li>Erfahrung mit Python und Cloud-Technologien.</li></ul>
  </main>
  <footer>Impressum</footer>
</body></html>
"""

NESTED_PAGE = """
<html><body>
  <main>
    <section>
      <h2>About us</h2>
      <article>
        <h3>Our mission</h3>
        <p>We build sovereign AI infrastructure.</p>
      </article>
      <div>Loose company fact inside a section div.</div>
    </section>
    <section><p>Second section paragraph.</p></section>
  </main>
</body></html>
"""

DIV_ONLY_PAGE = """
<html><body><main><section><div><span>Company text that lives only in divs and spans.</span></div></section></main></body></html>
"""


class ExtractCompanyTextTests(unittest.TestCase):
    def assert_each_once(self, text, sentences):
        for sentence in sentences:
            with self.subTest(sentence=sentence):
                self.assertEqual(text.count(sentence), 1, f"{sentence!r} appears {text.count(sentence)}x")

    def test_text_elements_inside_main_are_extracted_once(self):
        text = extract_company_text("https://example.com", JOB_PAGE)
        self.assert_each_once(text, [
            "Software Developer für KI-Plattformen (w/m/d)",
            "Das SCC ist der zentrale IT-Dienstleister des KIT.",
            "Ihre Aufgaben",
            "Entwicklung einer Plattform für KI-Modelle.",
            "Betrieb skalierbarer Kubernetes-Infrastruktur.",  # <p> inside <li>
            "Ihr Profil",
            "Erfahrung mit Python und Cloud-Technologien.",
        ])
        for removed in ("Site header", "Menu", "Impressum"):
            self.assertNotIn(removed, text)

    def test_nested_containers_are_extracted_once(self):
        text = extract_company_text("https://example.com", NESTED_PAGE)
        self.assert_each_once(text, [
            "About us",
            "Our mission",
            "We build sovereign AI infrastructure.",
            "Loose company fact inside a section div.",
            "Second section paragraph.",
        ])

    def test_no_block_is_contained_in_another(self):
        for page in (JOB_PAGE, NESTED_PAGE):
            blocks = extract_company_text("https://example.com", page).split("\n\n")
            for block in blocks:
                with self.subTest(block=block):
                    self.assertFalse(any(block in other and block != other for other in blocks))

    def test_meaningful_text_kept_in_order_and_paragraphs(self):
        text = extract_company_text("https://example.com", JOB_PAGE)
        blocks = text.split("\n\n")
        self.assertEqual(blocks[0], "Software Developer für KI-Plattformen (w/m/d)")
        self.assertLess(text.index("Ihre Aufgaben"), text.index("Ihr Profil"))
        self.assertEqual(len(blocks), 7)

    def test_text_only_in_divs_is_still_extracted(self):
        text = extract_company_text("https://example.com", DIV_ONLY_PAGE)
        self.assertEqual(text, "Company text that lives only in divs and spans.")

    def test_empty_page_still_raises(self):
        with self.assertRaises(ValueError):
            extract_company_text("https://example.com", "<html><body><nav>Menu</nav></body></html>")


if __name__ == "__main__":
    unittest.main()
