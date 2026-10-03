# German language: website (DE/EN) and German cover letters

Date: 2026-10-03 · Branch: `dev`

## Goal

German users can use the whole site in German, and anyone can get a cover letter in German (for German postings), written in proper German business-letter style, in their own voice.

## Part 1: website in German or English

- A `DE | EN` switch in the header of every page, the landing page and the auth pages.
- The choice is stored in a `lang` cookie (1 year). Without a cookie, the browser's `Accept-Language` decides; the default is English. `<html lang>` follows the choice.
- Translations live in one Python dict per language (`src/i18n.py`), exposed to templates as `t("key")`. No gettext/Babel dependency; ~150 strings.
- Server messages shown to users (validation errors, busy/LLM errors, research errors) go through the same dictionary.
- Not translated: user content (letters, file names, angles produced by the LLM), and URLs/routes.
- Tests pin English strings, so English stays the default and tests run in English; new tests check the German rendering of each page.

## Part 2: German cover letters

- On the job page: "Letter language: Auto (from the job description) / English / Deutsch". Auto detects German from common German words in the job description (no dependency).
- The language is passed to letter generation. Revisions keep the language of the current letter, so no database change is needed.
- German prompt rules: formal "Sie"; "Sehr geehrte Frau/Herr …" if a contact name is in the posting, otherwise "Sehr geehrte Damen und Herren"; "Mit freundlichen Grüßen"; German business style (no "Hiermit bewerbe ich mich…" style openings: the same no-generic-opening rule, applied in German).
- The voice still comes from the user's past letters, even if they are in English.
- The deterministic checks get German equivalents of the generic openings and empty claims ("Hiermit bewerbe ich mich", "Ich bin leidenschaftlich", "teamfähig und motiviert").
- Angles stay in the language they're generated in (the job description's language), shown as-is.

## Out of scope
Other languages, translating existing letters, a per-user language column in the database.

## Testing
- The full-journey test also runs in German (cookie set): every page renders German strings and no English leftovers from the dictionary.
- Language detection: German/English sample job descriptions.
- Prompt tests: German letters get the German rules; revisions of a German letter keep German.
- The fake backend returns a German sample letter when German is requested, so the flow can be clicked through.
- A few real letters per language with real keys before release (can't be faked).
