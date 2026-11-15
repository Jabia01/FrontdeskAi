"""The public site's copy, for the default locale.

The strings themselves moved to `app/web/locales/en.py` when the site gained a
second language. This module stays as the front door for two reasons: every
test reaches for `content.FAQ` directly, and `visible_reviews()` has to read
the module-level `SHOW_REVIEWS` that those tests monkeypatch.

For a page in a particular locale, go through `app.web.locales.get(locale)`
instead - `_landing_context` in `app/web/marketing.py` does.

The two rules the original file existed to enforce still hold, and they hold
per locale:

1. **The demo is real.** A demo conversation is one that actually happened
   against this system. Replace it only with another real capture - it is the
   one claim on this site a visitor can disprove in thirty seconds, because
   they will try it themselves.

2. **Reviews are off until they are real.** `SHOW_REVIEWS` is False and every
   sample is marked. Invented testimonials are an FTC matter, and this site is
   read by carrier reviewers during A2P 10DLC vetting.
"""

from __future__ import annotations

from typing import Any

from app.web.locales import en

# Re-exported by name rather than with `import *`, so what the site depends on
# is a list you can read, and a locale that drops a key fails here instead of
# at render time.
DEMO_SHOP = en.DEMO_SHOP
DEMO_CAPTION = en.DEMO_CAPTION
DEMO_CONVERSATIONS = en.DEMO_CONVERSATIONS
OWNER_ALERT = en.OWNER_ALERT
HEADLINE_STEM = en.HEADLINE_STEM
HEADLINE_TAILS = en.HEADLINE_TAILS
CHANNELS = en.CHANNELS
METRICS = en.METRICS
LANGUAGE_DEMO = en.LANGUAGE_DEMO
SHOW_REVIEWS = en.SHOW_REVIEWS
REVIEWS = en.REVIEWS
FEATURES = en.FEATURES
FAQ = en.FAQ
SECTORS = en.SECTORS
COPY = en.COPY


def reviews_are_samples() -> bool:
    return any(r.get("sample") for r in REVIEWS)


def visible_reviews() -> list[dict[str, Any]]:
    """Reads this module's own `SHOW_REVIEWS`, which is what the tests patch."""
    return list(REVIEWS) if SHOW_REVIEWS else []
