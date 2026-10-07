"""Is a text German or English? A small word-list heuristic, no dependency.

Counts very common function words of each language; ties and empty text count as English.
"""
from __future__ import annotations

import re

GERMAN_WORDS = frozenset(
    "und der die das den dem des ein eine einen einer wir sie ihr ihre ihren unser unsere unseren für mit von bei "
    "auf ist sind wird werden nicht auch oder als zu zur zum im über sowie aufgaben anforderungen erfahrung "
    "kenntnisse bewerbung stelle wünschenswert idealerweise ich mich meine meinen".split()
)
ENGLISH_WORDS = frozenset(
    "the and you your our we with for of to is are will be on as an this that from have has who what "
    "experience requirements role team responsibilities i my me".split()
)
WORD = re.compile(r"[a-zäöüß]+")


def detect_language(text: str | None) -> str:
    """"de" when the text reads as German, otherwise "en"."""
    words = WORD.findall(str(text or "").lower())
    german = sum(word in GERMAN_WORDS for word in words)
    english = sum(word in ENGLISH_WORDS for word in words)
    return "de" if german > english else "en"
