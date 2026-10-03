# Frontend overhaul — "The writer's desk"

Date: 2026-10-03 · Branch: `dev`

## Goal

Turn the app's plain forms into an Awwwards-grade experience: a cinematic landing page plus a cohesive, animated design system across every app page. No backend rewrite.

## Decisions (agreed)

- **Stack:** keep FastAPI + Jinja. Add GSAP 3.13 (ScrollTrigger, SplitText) + Lenis from CDN on the landing page only; the native View Transitions API for page-to-page motion; CSS springs (`linear()` easing, Kinetics-style) for micro-interactions. No build step.
- **Scope:** new landing page at `/`, restyle all 7 app pages.
- **Direction:** editorial / paper — warm paper, ink, and an editor's red pen as the single accent (it echoes the 3-round revision loop).
- **Resources:** kinetics.colorion.co and transitions.dev used as CSS sources; cta.gallery as inspiration for the hero and final CTA; React libraries (kobra, beui, reverseui, astryx, beautifului) as visual reference only; evilcharts skipped (no charts in the app).

## Design system

- Colors: `--paper #F4EFE6`, `--paper-2 #EBE4D6`, `--ink #1A1714`, `--ink-soft #5B544B`, `--line rgba(26,23,20,.12)`, `--pen #D9432B` (only accent), `--ok #3F6B4A`. Light only. Subtle SVG-noise paper grain.
- Type: Fraunces (variable serif, display + letters), Instrument Sans (UI/body), JetBrains Mono (tiny meta labels). Fluid `clamp()` scale.
- Motion tokens: 160 / 320 / 700 / 1200 ms; expo ease-out; `--spring-snappy`, `--spring-soft` via `linear()`.
- `prefers-reduced-motion: reduce` disables all non-essential motion, including GSAP (landing renders in its final state).

## Landing page (`/`)

1. Nav: wordmark, Log in, magnetic "Start writing" pill.
2. Hero: giant Fraunces headline "Cover letters, in your own *voice*." — lines mask-reveal via SplitText; "voice" in pen red with a hand-drawn SVG underline that draws in. Beside it a paper sheet that types a sample letter, then a red-pen strike-through and rewrite (shows revision). Sheet tilts subtly toward the pointer.
3. Marquee: mono pipeline strip (resume → style → research → angle → letter), speed nudged by scroll velocity.
4. How it works: 4 steps, pinned horizontal scroll on desktop, stacked on mobile. Each step has a small CSS-built visual.
5. Manifesto: large statement whose words fill with ink as you scroll (scrubbed).
6. Final CTA (cta.gallery-inspired): inverted ink section, oversized type, signature SVG that draws in, magnetic button.
7. Footer.

Signup keeps working at `/signup`; the landing CTAs point there.

## App pages

- Shared `base.html` (head, fonts, CSS, JS, view transitions) — every page extends it. Account header markup is unchanged (tests pin it); only restyled.
- Journey stepper in the header on the flow pages: Profile → Role → Angle → Letter.
- Auth: split screen — form on paper, an ink panel with a slowly self-writing letter excerpt.
- Profile setup: files as index cards, drag-and-drop onto the picker, animated SVG checkmarks in the progress aside.
- Job input: auto-growing textarea with a live word count.
- Angles: anchors as selectable cards (staggered entrance, pen-red selected state).
- Letter: the letter on a paper sheet with a line-by-line ink reveal, Copy button with feedback, revision dots (3), a "Final" stamp animation once accepted.
- Every LLM-backed form shows a loading state: button spinner + a full-page ink overlay with rotating status lines ("Researching the company…").

## Files

- `templates/base.html` (shared head, fonts, overlay, scripts), `templates/auth_base.html` (signup/login layout), `templates/landing.html` (new); all existing templates extend these.
- `static/css/base.css` (tokens, type, buttons, forms, overlay, view transitions), `app.css` (app pages), `landing.css` (landing only) replace `static/styles.css`.
- `static/js/app.js` (vanilla micro-interactions), `static/js/landing.js` (GSAP + Lenis).
- `app.py`: `/` renders `landing.html`. No other backend change.
- Test-pinned markup is kept verbatim: account menu, hidden inputs, `cta-button" disabled`, `step-icon done`, "GENERATED COVER LETTER", page headings.

## Testing

- Existing tests stay green without edits (they pin header markup and key strings).
- New test: `/` renders the landing and links to `/signup`; `/signup` still renders the form.
- Visual check in the browser at desktop and mobile widths with mocked data, plus reduced-motion.
