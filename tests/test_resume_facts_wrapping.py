"""Change G: wrapped resume bullets become one item, at most 3 facts per header, and the gap rule. Made-up data only."""
import unittest

from src import cover_letter_generator as generator
from src import resume_facts
from src.resume_facts import RESUME_FACTS_RULES, _resume_lines, format_resume_facts_section, select_resume_facts

JD = "Platform engineer: Python services, Kubernetes deployment, monitoring dashboards and data pipelines."
ANCHOR = {"title": "Reliable platforms", "job_connection": "The role owns deployment and monitoring.", "anchor": "Steady releases."}
GAP_RULE = (
    "Before saying the candidate lacks a skill or experience, check these facts; if they cover it, use them instead of "
    "claiming a gap."
)
OLD_RULES = (
    "Rules for these facts: use only what is written above; copy numbers exactly; do not add a baseline, "
    "a time frame or a result that is not written; if a number has no baseline, state it plainly without a comparison."
)
SAMPLE_ANCHOR = {"title": "t", "company_evidence": "e", "job_connection": "j", "candidate_evidence": "c", "anchor": "a", "source_url": "https://fernwick-labs.example"}


def lines(text):
    return [line for line, _group in _resume_lines(text)]


class WrappingTests(unittest.TestCase):
    def test_bullet_wrapped_over_three_lines_is_one_item(self):
        text = "Jane Doe\nPlatform Engineer at Fernwick Labs, 2021-2024\n• Moved twelve Python services to Kubernetes\nwith staged rollouts and health checks\nacross two regions."
        self.assertEqual(_resume_lines(text), [
            ("Platform Engineer at Fernwick Labs, 2021-2024", ""),
            ("Moved twelve Python services to Kubernetes with staged rollouts and health checks across two regions.", "Platform Engineer at Fernwick Labs, 2021-2024"),
        ])

    def test_lowercase_parenthesis_and_digit_starts_join_even_after_a_full_stop(self):
        for continuation in ("(three regions, one team)", "reducing pages for the night shift", "40% fewer alerts after the change"):
            with self.subTest(continuation=continuation):
                text = f"• Rebuilt the alerting rules for the on-call rota.\n{continuation}"
                self.assertEqual(lines(text), [f"Rebuilt the alerting rules for the on-call rota. {continuation}"])

    def test_capitalised_line_after_a_finished_bullet_is_a_header(self):
        text = "• Rebuilt the alerting rules for the on-call rota.\nData Engineer, Lowmere Analytics\n• Built nightly data pipelines in Python and SQL"
        self.assertEqual(_resume_lines(text), [
            ("Rebuilt the alerting rules for the on-call rota.", ""),
            ("Data Engineer, Lowmere Analytics", ""),
            ("Built nightly data pipelines in Python and SQL", "Data Engineer, Lowmere Analytics"),
        ])

    def test_each_bullet_mark_starts_a_new_item(self):
        for mark in ("•", "-", "–"):
            with self.subTest(mark=mark):
                text = f"{mark} Built monitoring dashboards for the platform team\n{mark} Wrote deployment runbooks for new services"
                self.assertEqual(lines(text), [
                    "Built monitoring dashboards for the platform team",
                    "Wrote deployment runbooks for new services",
                ])

    def test_dash_without_a_space_is_not_a_bullet(self):
        text = "• Built monitoring dashboards for the platform team\n-based on open metrics and simple alert rules"
        self.assertEqual(lines(text), ["Built monitoring dashboards for the platform team -based on open metrics and simple alert rules"])

    def test_resume_without_bullets_still_returns_relevant_lines(self):
        text = (
            "Jane Doe\n"
            "I build Python services and run them on Kubernetes for a small platform team.\n"
            "Before that I designed monitoring dashboards and data pipelines for a regional clinic group.\n"
            "Outside work I sing in a community choir on weekends."
        )
        facts = select_resume_facts(text, JD, ANCHOR, [])
        self.assertIn("- I build Python services and run them on Kubernetes for a small platform team.", facts)
        self.assertIn("monitoring dashboards and data pipelines", facts)
        self.assertNotIn("choir", facts)


def bullets(count, topic):
    return "\n".join(f"• {topic} item {n}: Python services, Kubernetes deployment and monitoring dashboards." for n in range(count))


class GroupCapTests(unittest.TestCase):
    def test_five_matching_bullets_under_one_header_give_at_most_three(self):
        facts = select_resume_facts("Jane Doe\nPlatform Engineer, Fernwick Labs\n" + bullets(5, "Fernwick"), JD, ANCHOR, [])
        self.assertEqual(facts.count("Fernwick item"), 3)

    def test_matching_bullets_under_two_headers_both_appear(self):
        text = "Jane Doe\nPlatform Engineer, Fernwick Labs\n" + bullets(5, "Fernwick") + "\nData Engineer, Lowmere Analytics\n" + bullets(5, "Lowmere")
        facts = select_resume_facts(text, JD, ANCHOR, [])
        self.assertEqual(facts.count("Fernwick item"), 3)
        self.assertEqual(facts.count("Lowmere item"), 3)

    def test_without_a_header_there_is_no_group_cap_but_the_line_cap_holds(self):
        facts = select_resume_facts(bullets(12, "Plain"), JD, ANCHOR, [])
        self.assertEqual(len(facts.splitlines()), resume_facts.MAX_LINES)

    def test_without_a_header_the_character_cap_holds(self):
        long_bullets = "\n".join(f"• Item {n}: " + "Python services on Kubernetes with monitoring dashboards " * 3 for n in range(10))
        facts = select_resume_facts(long_bullets, JD, ANCHOR, [])
        self.assertLessEqual(len(facts), resume_facts.MAX_TOTAL_CHARS)
        self.assertGreater(len(facts.splitlines()), 3)  # no group cap without a header


class ExistingCapTests(unittest.TestCase):
    def test_line_character_and_per_line_caps(self):
        groups = "Jane Doe\n" + "\n".join(f"Role {g}, Example Org {g}\n" + bullets(3, f"Group{g}") for g in range(5))
        facts = select_resume_facts(groups, JD, ANCHOR, [])
        self.assertEqual(len(facts.splitlines()), resume_facts.MAX_LINES)
        self.assertLessEqual(len(facts), resume_facts.MAX_TOTAL_CHARS)

        wrapped = "• Built Python services on Kubernetes\n" + "\n".join("with monitoring dashboards and data pipelines" for _ in range(8))
        line = select_resume_facts(wrapped, JD, ANCHOR, []).splitlines()[0]
        self.assertLessEqual(len(line), len("- ") + resume_facts.MAX_LINE_CHARS)


class GapRuleTests(unittest.TestCase):
    FACTS = "- Moved twelve Python services to Kubernetes."

    def letter(self, **kwargs):
        return generator.build_cover_letter_prompt("JD", [SAMPLE_ANCHOR], {"tone": "x"}, [("a", "b")], "https://fernwick-labs.example", {"hook": "h"}, **kwargs)

    def revision(self, **kwargs):
        return generator.build_cover_letter_revision_prompt("letter", "shorter", "JD", [SAMPLE_ANCHOR], {"tone": "x"}, [("a", "b")], "https://fernwick-labs.example", **kwargs)

    def test_rules_keep_the_old_sentence_and_end_with_the_gap_rule(self):
        self.assertEqual(RESUME_FACTS_RULES, f"{OLD_RULES} {GAP_RULE}")

    def test_gap_rule_in_letter_and_revision_prompts_when_facts_exist(self):
        for build in (self.letter, self.revision):
            with self.subTest(build=build.__name__):
                self.assertEqual(build(resume_facts=self.FACTS).count(GAP_RULE), 1)

    def test_no_gap_rule_and_unchanged_prompts_without_facts(self):
        self.assertEqual(format_resume_facts_section(""), "")
        for build in (self.letter, self.revision):
            with self.subTest(build=build.__name__):
                self.assertNotIn(GAP_RULE, build())
                self.assertEqual(build(resume_facts=""), build())
        plan = generator.build_cover_letter_plan_prompt("JD", [SAMPLE_ANCHOR], {"tone": "x"}, [("a", "b")], "https://fernwick-labs.example")
        self.assertNotIn(GAP_RULE, plan)


if __name__ == "__main__":
    unittest.main()
