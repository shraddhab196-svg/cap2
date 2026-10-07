import unittest

import app
from src.cover_letter_generator import build_cover_letter_plan_prompt

COMPANY = "https://www.fernwick-labs.example/"
ABOUT = "https://www.fernwick-labs.example/about"
OPENING_RULE = "The opening should start with the selected job need from job_connection, then connect that need to the candidate's relevant evidence."


def anchor(source_url):
    return {
        "title": "Fair rotas",
        "company_evidence": "Fernwick Labs builds rota tools.",
        "company_evidence_matched": True,
        "job_connection": "The role owns scheduling quality.",
        "candidate_evidence": "Built a rota planner.",
        "anchor": "Fair scheduling.",
        "source_url": source_url,
    }


class PlanPromptTests(unittest.TestCase):
    def test_plan_prompt_asks_for_an_opening_from_the_job_need(self):
        prompt = build_cover_letter_plan_prompt(
            job_description="Scheduling engineer.",
            selected_anchors=[anchor(COMPANY)],
            style_profile={"voice": "plain"},
            previous_letters=[("letter_1", "An earlier letter.")],
            company_url=COMPANY,
        )
        self.assertIn(OPENING_RULE, prompt)
        self.assertEqual(prompt.count(OPENING_RULE), 1)
        self.assertIn("The role owns scheduling quality.", prompt)  # job_connection is in the prompt to start from


class SourceLinkTests(unittest.TestCase):
    def render(self, source_url, company_url=COMPANY):
        return app.templates.env.get_template("company_angles.html").render(
            request=None, user={"name": "jane.doe"}, company_url=company_url, job_description="JD",
            job_application_id="j1", anchors=[anchor(source_url)], error=None,
        )

    def test_link_hidden_when_source_is_the_typed_company_url(self):
        for source in (COMPANY, COMPANY.rstrip("/")):
            with self.subTest(source=source):
                html = self.render(source)
                self.assertNotIn("<dt>Source</dt>", html)
                self.assertIn("Matched on the page", html)  # the match status still shows

    def test_link_shown_when_source_is_a_different_page(self):
        html = self.render(ABOUT)
        self.assertIn("<dt>Source</dt>", html)
        self.assertIn(f'href="{ABOUT}"', html)


if __name__ == "__main__":
    unittest.main()
