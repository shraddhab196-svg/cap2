# Company research: safe multi-page crawl + sourced facts

Date: 2026-10-03 · Branch: `dev`

## Why

The letter's strongest move is a bridge: "you are doing X (specific, checkable) → I did something like X (from my resume)". The angle generator already makes that match (company evidence ↔ job need ↔ candidate evidence), but its company input is weak. Measured on 9 real sites from the cover-letter folder:

- Only the pasted URL is read (usually a marketing homepage). ERGO's homepage is funeral-insurance ads.
- Text is 3–4× duplicated (containers and their paragraphs are both collected): ERGO 1,468 words → 354 unique; HappyHotel ~4,200 words → 1,139 unique. Nothing is trimmed before the anchor prompt, which runs against a ~7,000 input-token Groq limit.
- German pages without a charset header come back garbled (`abschlie�en`).
- helsing.ai blocks the default `python-requests` client.
- Any URL is fetched, including `http://localhost` and cloud metadata addresses (SSRF).

## Goal

Research returns about 15 short, specific facts about the company, each with its source page, preferring facts relevant to the job description. Angles cite these facts. Failures degrade gracefully and never expose the server.

## Design

### 1. Safe fetch (`fetch_page`)
- Only `http`/`https`, ports 80/443. Every hop is resolved and rejected if any address is private, loopback, link-local, reserved or multicast. Redirects are followed manually (max 5) so each hop is checked.
- Browser-like `User-Agent` and `Accept-Language: de,en`. 10s timeout per page, 2 MB cap (streamed), HTML content types only.
- Bytes go to BeautifulSoup, which reads the page's own charset declaration (fixes the umlaut bug).
- Known limitation: DNS rebinding between check and connect is not handled; documented.

### 2. Job-board links
Hosts like LinkedIn, Indeed, StepStone, XING, Greenhouse, Lever, Workday (`myworkdayjobs.com`), Personio, SmartRecruiters and join.com are rejected with: "That looks like a job board link. Please paste the company's own website, e.g. https://company.com".

### 3. Clean extraction
- Leaf elements only (`p`, `li`, `h1`–`h4`), each text kept once; nav/header/footer/script/style removed.
- Cookie and consent text dropped; very short list items (menus) dropped.
- About 800 words per page max.

### 4. Pick relevant pages (3–5 total)
From the homepage's links on the same site, score by path and link text: about/company/über-uns/unternehmen, products/produkte/platform/solutions/lösungen, engineering/technology/tech/blog, news/newsroom/presse/press, research/forschung, careers/karriere. Skip login, legal (impressum, datenschutz, privacy, terms, agb), files and fragments. Fetch the top 4 in parallel, with a total time budget of 20s. Pages that fail are skipped.

### 5. Facts digest (one LLM step, `company_facts`)
Through the LLM gateway: given the cleaned pages (≤3,000 words total) and the job description, return JSON `{"facts": [{"fact", "type", "source"}]}`, max 15. `type` is product | technology | news | scale | customers | mission. Facts must be stated on the pages (no inference) and should be the ones most relevant to the role. `company_research` becomes a bullet list `- fact (source: url)`, so the anchor generator's interface does not change. If the LLM step fails, fall back to the deduplicated text, trimmed to 1,500 words.

### Result shape
`research_company(company_url, job_description="")` returns `{"company_url", "company_research", "facts", "pages"}`; `app.py` passes the job description.

## Edge cases → behavior
| Case | Behavior |
|---|---|
| Site blocks bots / times out | Homepage failure → clear error on the job page ("couldn't read that website…"); subpage failure → skipped |
| JavaScript-only site | Little text → facts from whatever exists; if under 50 words: error asking for an about/product page URL |
| Job-board URL | Rejected with the message above |
| Private/internal address | Rejected: "That address can't be researched." |
| Huge page / non-HTML | Cut at 2 MB / skipped |
| German site | Read correctly; facts keep the page language |

## Out of scope
JS rendering, news search outside the company's site, scraping services (later, only if many real sites fail), showing facts in the UI.

## Testing
A local test server serves a fake site (duplicated containers, nav, cookie banner, latin-1 page without a charset header, about and news pages, a legal page, a redirect to a private address). Tests: dedupe, encoding, page selection, fact digest with a mocked gateway, fallback when the LLM step fails, job-board rejection, private-address rejection including via redirect. Plus re-running the 9-site measurement before and after (word counts, failures).
