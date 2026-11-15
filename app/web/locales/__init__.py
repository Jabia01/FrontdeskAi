"""Locales for the public site.

Python modules rather than gettext. The content here is nested structures - a
demo conversation has an accent, a direction and a list of turns - and `.po`
files are built for sentences. Every test already imports `content.FAQ`
directly, a missing key becomes an ImportError at startup rather than a 500 on
a visitor's page, and the image needs no compile step.

A locale is published only when it is complete. `TRANSLATION_TODO` marks a
string that still holds its English text, and `missing()` finds them, so a
half-translated Spanish site cannot go live by accident - the same mechanism
`app/web/brand.py` uses for its `[to be completed]` placeholders.
"""

from __future__ import annotations

from types import ModuleType
from typing import Any

from app.web.locales import en

DEFAULT_LOCALE = "en"

#: Every locale the site knows about, in the order a language switcher shows
#: them. Adding one is a file plus an entry here.
LOCALES: dict[str, ModuleType] = {"en": en}

#: Prefix on any value still carrying its English text.
TRANSLATION_TODO = "TODO:"

#: What a locale has to define to be usable at all. Checked by a test rather
#: than at import, so a missing key is a red build and not a broken page.
REQUIRED_KEYS: tuple[str, ...] = (
    "DEMO_SHOP",
    "DEMO_CAPTION",
    "DEMO_CONVERSATIONS",
    "OWNER_ALERT",
    "HEADLINE_STEM",
    "HEADLINE_TAILS",
    "CHANNELS",
    "METRICS",
    "LANGUAGE_DEMO",
    "SHOW_REVIEWS",
    "REVIEWS",
    "FEATURES",
    "FAQ",
)


def get(locale: str | None = None) -> ModuleType:
    """The module for a locale. Unknown or missing falls back to the default,
    so a bad URL serves English rather than a 404 on a marketing page."""
    return LOCALES.get((locale or "").strip().lower(), LOCALES[DEFAULT_LOCALE])


def is_known(locale: str | None) -> bool:
    return (locale or "").strip().lower() in LOCALES


def missing(locale: str) -> list[str]:
    """Keys a locale does not define, plus any still marked untranslated."""
    module = LOCALES.get(locale)
    if module is None:
        # Sorted like the other branch: a caller comparing two results should
        # not have to know which one it got.
        return sorted(REQUIRED_KEYS)
    out: list[str] = []
    for key in REQUIRED_KEYS:
        if not hasattr(module, key) or _has_todo(getattr(module, key)):
            out.append(key)
    return sorted(out)


def is_publishable(locale: str) -> bool:
    return not missing(locale)


def _has_todo(value: Any) -> bool:
    if isinstance(value, str):
        return value.startswith(TRANSLATION_TODO)
    if isinstance(value, dict):
        return any(_has_todo(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return any(_has_todo(v) for v in value)
    return False
