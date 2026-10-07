import copy
import json
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import app
from src import anchor_generator

COMPANY = "https://www.fernwick-labs.example/"
ABOUT = "https://www.fernwick-labs.example/about"
SOURCES = [COMPANY, ABOUT]
RESEARCH = (
    f"### Source: {COMPANY}\n"
    "Fernwick Labs builds open tools that help small clinics schedule night shifts fairly.\n\n"
    f"### Source: {ABOUT}\n"
    "Our mission: make rostering software that nurses actually enjoy using, built with clinics in Lowmere."
)
SAMPLE_ANCHORS = json.loads((app.BASE_DIR / "tests" / "fixtures" / "sample_anchors.json").read_text(encoding="utf-8"))["anchors"]


def anchor(title, evidence, source_url):
    return {
        "title": title,
        "company_evidence": evidence,
        "job_connection": "The role owns scheduling quality.",
        "candidate_evidence": "Built a rota planner used by three teams.",
        "anchor": "A connection between the company, the role and the candidate.",
        "source_url": source_url,
    }


class MatchingTests(unittest.TestCase):
    def test_short_evidence_matches_only_as_a_phrase(self):
        self.assertTrue(anchor_generator.evidence_matches_research("Night shifts, fairly!", RESEARCH))
        self.assertFalse(anchor_generator.evidence_matches_research("Fairly night shifts", RESEARCH))

    def test_long_evidence_matches_on_shared_four_word_runs(self):
        evidence = "Fernwick Labs builds open tools that help small clinics schedule night shifts in a fair way"
        self.assertTrue(anchor_generator.evidence_matches_research(evidence, RESEARCH))

    def test_long_unrelated_evidence_does_not_match(self):
        evidence = "The company recently raised a large funding round to expand into autonomous delivery robots"
        self.assertFalse(anchor_generator.evidence_matches_research(evidence, RESEARCH))

    def test_empty_evidence_or_research_does_not_match(self):
        self.assertFalse(anchor_generator.evidence_matches_research("", RESEARCH))
        self.assertFalse(anchor_generator.evidence_matches_research("anything", ""))


class SourceUrlTests(unittest.TestCase):
    def test_known_source_is_kept_ignoring_trailing_slash(self):
        self.assertEqual(anchor_generator.clean_source_url(ABOUT + "/", COMPANY, SOURCES), ABOUT + "/")
        self.assertEqual(anchor_generator.clean_source_url(f"  {ABOUT}  ", COMPANY, SOURCES), ABOUT)

    def test_javascript_and_non_web_urls_fall_back_to_company_url(self):
        for bad in ("javascript:alert(1)", "data:text/html,hi", "ftp://fernwick-labs.example/x", "", None, "not a url"):
            with self.subTest(bad=bad):
                self.assertEqual(anchor_generator.clean_source_url(bad, COMPANY, SOURCES), COMPANY)

    def test_unknown_url_falls_back_when_sources_are_known(self):
        self.assertEqual(anchor_generator.clean_source_url("https://elsewhere.example/page", COMPANY, SOURCES), COMPANY)

    def test_any_web_url_kept_when_sources_unknown(self):
        self.assertEqual(anchor_generator.clean_source_url("https://elsewhere.example/page", COMPANY, None), "https://elsewhere.example/page")


class AnnotationTests(unittest.TestCase):
    def test_annotation_preserves_every_anchor_and_order(self):
        anchors = [
            anchor("One", "help small clinics schedule night shifts fairly", ABOUT),
            anchor("Two", "The company recently raised a large funding round to expand into robots", "javascript:alert(1)"),
            anchor("Three", "nurses actually enjoy using", "https://elsewhere.example/"),
        ]
        result = anchor_generator.annotate_anchors(copy.deepcopy(anchors), COMPANY, RESEARCH, SOURCES)
        self.assertEqual([a["title"] for a in result], ["One", "Two", "Three"])
        self.assertEqual([a["source_url"] for a in result], [ABOUT, COMPANY, COMPANY])
        self.assertEqual([a["company_evidence_matched"] for a in result], [True, False, True])

    def test_sample_fixture_anchors_still_annotate(self):
        result = anchor_generator.annotate_anchors(copy.deepcopy(SAMPLE_ANCHORS), "https://northwind.example", "", None)
        self.assertEqual(len(result), 3)
        self.assertEqual([a["source_url"] for a in result], [a["source_url"] for a in SAMPLE_ANCHORS])
        self.assertTrue(all(a["company_evidence_matched"] is False for a in result))


class GenerateAnchorsTests(unittest.TestCase):
    def run_generate(self, anchors, **kwargs):
        client = MagicMock()
        client.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({"anchors": anchors})))]
        )
        with patch.object(anchor_generator, "load_environment"), patch.object(anchor_generator, "get_llm_client", return_value=(client, "model")):
            result = anchor_generator.generate_anchors(COMPANY, "JD", RESEARCH, [("letter", "text")], **kwargs)
        return result, client.chat.completions.create.call_args.kwargs

    def test_sources_keyword_annotates_and_keeps_existing_limits(self):
        anchors = [anchor(f"A{i}", "help small clinics schedule night shifts fairly", ABOUT) for i in range(3)]
        result, call = self.run_generate(anchors, sources=SOURCES)
        self.assertEqual(call["max_tokens"], 950)
        self.assertIn('source_url must be one of the "### Source:" URLs', call["messages"][1]["content"])
        self.assertEqual(len(result["anchors"]), 3)
        self.assertTrue(all(a["source_url"] == ABOUT and a["company_evidence_matched"] for a in result["anchors"]))

    def test_positional_call_without_sources_still_works(self):
        anchors = [anchor(f"A{i}", "unrelated evidence text", "https://elsewhere.example/") for i in range(3)]
        result, _ = self.run_generate(anchors)
        self.assertEqual([a["source_url"] for a in result["anchors"]], ["https://elsewhere.example/"] * 3)

    def test_fewer_than_three_useful_anchors_are_still_annotated(self):
        result, _ = self.run_generate([anchor("Only", "x", ABOUT), anchor("Two", "y", ABOUT)], sources=SOURCES)
        self.assertEqual([a["title"] for a in result["anchors"]], ["Only", "Two"])
        self.assertTrue(all("company_evidence_matched" in a for a in result["anchors"]))


class AnglesTemplateTests(unittest.TestCase):
    def render(self, anchors):
        return app.templates.env.get_template("company_angles.html").render(
            request=None, user={"name": "jane.doe"}, company_url=COMPANY, job_description="JD",
            job_application_id="j1", anchors=anchors, error=None,
        )

    def test_new_keys_show_source_link_and_match_status(self):
        anchors = anchor_generator.annotate_anchors(
            [anchor("One", "help small clinics schedule night shifts fairly", ABOUT),
             anchor("Two", "The company recently raised a large funding round to expand into robots", ABOUT)],
            COMPANY, RESEARCH, SOURCES,
        )
        html = self.render(anchors)
        self.assertIn(f'href="{ABOUT}" target="_blank" rel="noopener noreferrer"', html)
        self.assertIn("Matched on the page", html)
        self.assertIn("Couldn't match this to the page. Check it before relying on it.", html)

    def test_old_rows_without_new_keys_show_neither(self):
        html = self.render(SAMPLE_ANCHORS)
        self.assertIn(SAMPLE_ANCHORS[0]["title"], html)
        self.assertNotIn("<dt>Source</dt>", html)
        self.assertNotIn("Matched on the page", html)
        self.assertNotIn("Couldn't match", html)

    def test_unsafe_source_is_never_rendered_as_a_link(self):
        bad = dict(anchor("Bad", "x", "javascript:alert(1)"), company_evidence_matched=False)
        html = self.render([bad])
        self.assertNotIn("javascript:", html)


if __name__ == "__main__":
    unittest.main()
