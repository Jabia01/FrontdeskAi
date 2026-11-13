"""Retell webhook authenticity.

Retell signs every webhook with

    X-Retell-Signature: v={unix_ms},d={hex_digest}

where the digest is HMAC-SHA256, keyed with the account's API key, over the
request body concatenated with that same timestamp.

One wrinkle worth knowing about, because it is the kind of thing that fails
silently in production and looks like a key problem: Retell's own docs say the
digest covers the *raw* body, while their SDK examples re-serialise the parsed
JSON before verifying. Those two strings differ whenever the sender's whitespace
or key order differs from the re-serialisation. Rather than guess which one this
account's webhooks actually use, `verify` accepts a match against either
canonical form of the *same parsed body* and logs which one matched. That is not
a weaker check - every candidate is still a byte string that must HMAC to the
signature under our secret key - it just stops a formatting difference from
reading as a forgery.

Replay is bounded by the timestamp window, and the comparison is constant time.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import time

log = logging.getLogger(__name__)

#: Retell's documented tolerance. A webhook older than this is a replay.
MAX_SKEW_MS = 5 * 60 * 1000

_SIGNATURE = re.compile(r"^v=(\d+),d=([0-9a-fA-F]+)$")


class SignatureError(Exception):
    """The signature header is absent, malformed, stale or wrong."""


def _candidates(raw_body: bytes) -> list[str]:
    """The byte strings Retell might have signed, most likely first.

    The raw body is always first: it is what the docs specify and what a
    byte-exact implementation produces. The re-serialisations are only reached
    when the raw body does not match, which is why a well-behaved sender never
    pays for them.
    """
    try:
        text = raw_body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SignatureError("webhook body is not valid UTF-8") from exc

    forms = [text]
    try:
        parsed = json.loads(text or "null")
    except ValueError:
        return forms

    # json.dumps' two common settings. Not an exhaustive search - just the two
    # shapes Retell's own client libraries produce.
    for separators in ((",", ":"), (", ", ": ")):
        rendered = json.dumps(parsed, separators=separators, ensure_ascii=False)
        if rendered not in forms:
            forms.append(rendered)
    return forms


def verify(raw_body: bytes, signature: str | None, api_key: str, *, now_ms: int | None = None) -> None:
    """Raise `SignatureError` unless this body really came from Retell.

    Returns None on success so a caller cannot mistake a falsy return for a
    passing check - the failure mode of a `verify() -> bool` that someone
    forgets to branch on.
    """
    if not api_key:
        raise SignatureError("no Retell API key configured, so nothing to verify against")

    match = _SIGNATURE.match((signature or "").strip())
    if match is None:
        raise SignatureError("missing or malformed X-Retell-Signature header")

    timestamp, digest = match.group(1), match.group(2).lower()

    current = now_ms if now_ms is not None else int(time.time() * 1000)
    if abs(current - int(timestamp)) > MAX_SKEW_MS:
        raise SignatureError("Retell signature timestamp is outside the 5 minute window")

    key = api_key.encode("utf-8")
    for index, candidate in enumerate(_candidates(raw_body)):
        expected = hmac.new(
            key, (candidate + timestamp).encode("utf-8"), hashlib.sha256
        ).hexdigest()
        if hmac.compare_digest(expected, digest):
            if index:
                log.info(
                    "Retell signature matched re-serialised body (form %d), not the "
                    "raw bytes - harmless, but see app/gateway/retell_signature.py",
                    index,
                )
            return

    raise SignatureError("Retell signature does not match the request body")


def sign(raw_body: bytes, api_key: str, *, now_ms: int | None = None) -> str:
    """Produce a header Retell would have produced. Used by the tests and the
    local call simulator, so the verification path is exercised for real rather
    than bypassed with a flag."""
    timestamp = str(now_ms if now_ms is not None else int(time.time() * 1000))
    digest = hmac.new(
        api_key.encode("utf-8"),
        (raw_body.decode("utf-8") + timestamp).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"v={timestamp},d={digest}"
