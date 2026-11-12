"""Twilio webhook signature validation.

The URL Twilio signed is the public one it was configured with, which is not
necessarily what the app sees behind a proxy or tunnel - so the canonical URL is
built from `PUBLIC_BASE_URL` rather than from the request.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping

from app.config import get_settings

log = logging.getLogger(__name__)


def canonical_url(path: str) -> str:
    base = get_settings().public_base_url.rstrip("/")
    if not path.startswith("/"):
        path = f"/{path}"
    return f"{base}{path}"


def validate_twilio_signature(
    path: str, params: Mapping[str, str], signature: str | None
) -> bool:
    """True when the request is authentic, or when validation is switched off.

    Validation is skipped only when it is explicitly disabled, or when no auth
    token is configured yet (Phase 1, before a Twilio account exists). Both are
    logged so a misconfigured production deploy is visible.
    """
    settings = get_settings()

    if not settings.twilio_validate_signature:
        log.warning("Twilio signature validation is DISABLED by configuration")
        return True

    if not settings.twilio_auth_token:
        log.warning(
            "Twilio signature validation skipped: no TWILIO_AUTH_TOKEN configured"
        )
        return True

    if not signature:
        log.warning("rejected Twilio webhook: missing X-Twilio-Signature")
        return False

    from twilio.request_validator import RequestValidator

    validator = RequestValidator(settings.twilio_auth_token)
    url = canonical_url(path)
    ok = validator.validate(url, dict(params), signature)
    if not ok:
        log.warning("rejected Twilio webhook: bad signature for %s", url)
    return ok
