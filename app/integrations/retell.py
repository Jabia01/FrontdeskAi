"""Retell REST client and language mapping.

Only the setup surface lives here - creating the agent, pointing a number at it,
listing voices. None of it is on the call path: once a number is configured,
answering a call touches Retell over the websocket only, and this module is not
imported.

Chichi stores languages as ISO 639-1 (`es`, `pl`). Retell wants locales
(`es-419`, `pl-PL`), and the choice of regional variant is a real decision
rather than a formatting detail: `es-419` is Latin American Spanish, which is
who actually calls a Chicago shop, and `es-ES` would transcribe and speak like
Madrid.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import get_settings

log = logging.getLogger(__name__)

API_BASE = "https://api.retellai.com"

# Paths are spelled out one by one because Retell's are not uniform - some carry
# a /v2 prefix and some do not, so a cleverly-derived path would be wrong.
PATH_CREATE_AGENT = "/create-agent"
PATH_UPDATE_AGENT = "/update-agent/{agent_id}"
PATH_GET_AGENT = "/get-agent/{agent_id}"
PATH_LIST_VOICES = "/list-voices"
PATH_UPDATE_NUMBER = "/update-phone-number/{phone_number}"
PATH_LIST_NUMBERS = "/v2/list-phone-numbers"
PATH_GET_CALL = "/v2/get-call/{call_id}"

#: ISO 639-1 -> Retell locale, for every language Chichi knows how to speak.
#: Regional choices are deliberate; see the module docstring.
RETELL_LOCALES: dict[str, str] = {
    "ar": "ar-SA",
    "cs": "cs-CZ",
    "da": "da-DK",
    "de": "de-DE",
    "el": "el-GR",
    "en": "en-US",
    "es": "es-419",
    "fr": "fr-FR",
    "he": "he-IL",
    "hi": "hi-IN",
    "hu": "hu-HU",
    "it": "it-IT",
    "ja": "ja-JP",
    "ko": "ko-KR",
    "nl": "nl-NL",
    "no": "no-NO",
    "pl": "pl-PL",
    "pt": "pt-BR",
    "ro": "ro-RO",
    "ru": "ru-RU",
    "sk": "sk-SK",
    "sv": "sv-SE",
    "th": "th-TH",
    # Retell has no Tagalog; Filipino is the standardised register of it.
    "tl": "fil-PH",
    "tr": "tr-TR",
    "uk": "uk-UA",
    "ur": "ur-IN",
    "vi": "vi-VN",
    "zh": "zh-CN",
}

#: Retell's own default when nothing is configured.
FALLBACK_LOCALE = "en-US"


class RetellError(Exception):
    """A Retell API call failed. Carries the status and body for diagnosis."""

    def __init__(self, status: int, body: str) -> None:
        super().__init__(f"Retell API returned {status}: {body[:500]}")
        self.status = status
        self.body = body


def locales_for(languages: list[str] | tuple[str, ...]) -> list[str]:
    """ISO codes -> the locale list for an agent's `language` field.

    Retell cannot stitch language coverage across providers mid-call, so the
    set we send here is a promise the chosen voice and STT must both keep. We
    send only languages we have a mapping for, and English is always included:
    a caller who gets no recognised language at all should still be understood
    rather than met with silence.
    """
    seen: list[str] = []
    for code in list(languages) + ["en"]:
        locale = RETELL_LOCALES.get((code or "").strip().lower())
        if locale and locale not in seen:
            seen.append(locale)
    return seen or [FALLBACK_LOCALE]


def websocket_url(public_base_url: str, secret: str) -> str:
    """The custom-LLM URL to register with Retell.

    Retell appends `/{call_id}` to whatever is registered, so this is a prefix,
    not a complete endpoint. http(s) is rewritten to ws(s) because Retell will
    not upgrade an http:// URL.
    """
    base = (public_base_url or "").rstrip("/")
    if base.startswith("https://"):
        base = "wss://" + base[len("https://") :]
    elif base.startswith("http://"):
        base = "ws://" + base[len("http://") :]
    return f"{base}/ws/retell/{secret}"


async def _request(method: str, path: str, payload: dict | None = None) -> Any:
    settings = get_settings()
    if not settings.retell_api_key:
        raise RetellError(0, "RETELL_API_KEY is not set")

    async with httpx.AsyncClient(base_url=API_BASE, timeout=30.0) as client:
        response = await client.request(
            method,
            path,
            json=payload,
            headers={"Authorization": f"Bearer {settings.retell_api_key}"},
        )
    if response.status_code >= 400:
        raise RetellError(response.status_code, response.text)
    if not response.content:
        return None
    try:
        return response.json()
    except ValueError:
        return response.text


def agent_payload(
    *,
    websocket: str,
    voice_id: str,
    languages: list[str] | tuple[str, ...],
    webhook_url: str,
    agent_name: str = "Chichi front desk",
    max_call_seconds: int = 600,
    voice_model: str = "",
) -> dict:
    """The agent body. One agent serves every tenant.

    Deliberate choices:

    * `stt_mode: accurate` - a shop's name, a street and a car model are exactly
      the words `fast` gets wrong, and a misheard address is a lost customer.
    * `backchannel` off - Chichi replies in whatever language the caller used,
      and English "mm-hm"s dropped into a Polish call sound broken.
    * `voicemail_option` hangs up rather than talking to an answering machine,
      which otherwise bills a full conversation to nobody.
    * `webhook_events` excludes `transcript_updated`: it fires constantly and we
      already have every turn from the websocket.
    """
    payload: dict[str, Any] = {
        "agent_name": agent_name,
        "response_engine": {"type": "custom-llm", "llm_websocket_url": websocket},
        "voice_id": voice_id,
        "language": locales_for(languages),
        "webhook_url": webhook_url,
        "webhook_events": ["call_started", "call_ended", "call_analyzed"],
        "stt_mode": "accurate",
        "denoising_mode": "noise-and-background-speech-cancellation",
        "enable_backchannel": False,
        "max_call_duration_ms": max(60, int(max_call_seconds)) * 1000,
        "end_call_after_silence_ms": 20_000,
        # `hangup`, not `hang_up` - the API rejects the latter. The other
        # allowed actions are `bridge_transfer` and leaving a spoken message,
        # which needs its own `text`.
        "voicemail_option": {"action": {"type": "hangup"}},
        "post_call_analysis_model": "gpt-4o-mini",
    }
    if voice_model:
        # Omitted rather than sent as null when unset, so Retell applies its own
        # default for the chosen voice instead of us clearing a deliberate
        # dashboard setting on every re-push.
        payload["voice_model"] = voice_model
    return payload


async def create_agent(**kwargs: Any) -> dict:
    return await _request("POST", PATH_CREATE_AGENT, agent_payload(**kwargs))


async def update_agent(agent_id: str, **kwargs: Any) -> dict:
    return await _request(
        "PATCH", PATH_UPDATE_AGENT.format(agent_id=agent_id), agent_payload(**kwargs)
    )


async def get_agent(agent_id: str) -> dict:
    return await _request("GET", PATH_GET_AGENT.format(agent_id=agent_id))


def _items(result: Any) -> list[dict]:
    """Rows out of either response shape.

    Retell retired its unversioned list endpoints in June 2026. The versioned
    ones answer with `{items, pagination_key, has_more}` instead of a bare
    array, and not every endpoint has moved yet - `list-voices` is still an
    array - so both shapes are read rather than one being assumed.
    """
    if isinstance(result, list):
        return [row for row in result if isinstance(row, dict)]
    if isinstance(result, dict):
        rows = result.get("items")
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)]
    return []


async def list_voices() -> list[dict]:
    return _items(await _request("GET", PATH_LIST_VOICES))


async def list_numbers() -> list[dict]:
    """Every number on the account.

    Only used by the setup CLI, and a shop has one number, so the
    `pagination_key` the versioned endpoint offers is not followed - a second
    page here would mean a hundred-plus numbers, which is a different product
    than the one this is for.
    """
    return _items(await _request("GET", PATH_LIST_NUMBERS))


async def point_number_at_agent(
    phone_number: str, agent_id: str, inbound_webhook_url: str
) -> dict:
    """Route a number to our agent and to our inbound webhook.

    The webhook is what resolves the tenant: it runs before the call connects,
    and without it every shop's callers would reach the same unconfigured agent.
    """
    return await _request(
        "PATCH",
        PATH_UPDATE_NUMBER.format(phone_number=phone_number),
        {
            "inbound_agents": [{"agent_id": agent_id, "weight": 1}],
            "inbound_webhook_url": inbound_webhook_url,
            "nickname": "Chichi front desk",
        },
    )


async def get_call(call_id: str) -> dict:
    """Authoritative record of a finished call - duration, recording, summary.

    The `call_ended` webhook carries most of this, but it is fetched rather than
    trusted when a field we need is missing, because the webhook arrives once
    and a dropped field is a permanently incomplete record.
    """
    return await _request("GET", PATH_GET_CALL.format(call_id=call_id))
