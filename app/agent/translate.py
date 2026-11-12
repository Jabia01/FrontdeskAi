"""Short translations for owner-to-customer relays.

The owner writes in their own language; the customer must read it in theirs. This
is a deliberately tiny, cheap call on Haiku with structured output - not the
front-desk agent, which would try to be helpful and add things the owner did not
say.
"""

from __future__ import annotations

import json
import logging

from pydantic import BaseModel

from app.agent.language import language_name, normalise
from app.config import get_settings

log = logging.getLogger(__name__)

MAX_TOKENS = 600

_SCHEMA = {
    "type": "json_schema",
    "schema": {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "The translated message, and nothing else.",
            }
        },
        "required": ["text"],
        "additionalProperties": False,
    },
}

SYSTEM = (
    "You translate one short message from a small business to its customer.\n"
    "Rules:\n"
    "- Translate faithfully into the target language. Do not add, remove, soften "
    "or explain anything, and do not answer the message.\n"
    "- Keep part names, item models, prices, dates and times exactly as "
    "written.\n"
    "- Keep it the same length and register: a plain text message from a shop.\n"
    "- If the message is already in the target language, return it unchanged.\n"
    "- Return only the translation."
)


class Translation(BaseModel):
    text: str


async def translate(
    text: str, *, to_language: str, from_language: str = "en"
) -> tuple[str, bool]:
    """Returns (text, translated). Falls back to the original on any failure.

    A relay must never fail to reach the customer, so a translation error sends
    the owner's own words rather than nothing.
    """
    target = normalise(to_language)
    source = normalise(from_language)

    if not text.strip() or not target or target == source:
        return text, False

    from app.agent.runner import get_client

    try:
        response = await get_client().messages.create(
            model=get_settings().model_default,
            max_tokens=MAX_TOKENS,
            system=SYSTEM,
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Target language: {language_name(target)} ({target}).\n"
                        f"Message:\n{text}"
                    ),
                }
            ],
            output_config={"effort": "low", "format": _SCHEMA},
        )
        if response.stop_reason == "refusal":
            log.warning("translation refused; sending the original text")
            return text, False
        body = next(
            (b.text for b in response.content if getattr(b, "type", None) == "text"),
            None,
        )
        if not body:
            return text, False
        return Translation.model_validate(json.loads(body)).text, True
    except Exception as exc:
        # Broad by design: a relay must reach the customer. A missing key, a
        # rate limit or a bad response all fall back to the owner's own words.
        log.error("translation failed (%s); sending the original", exc)
        return text, False
