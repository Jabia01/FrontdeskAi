"""Set Retell up to answer the phone for every shop.

    python -m seeds.retell_agent --list-voices
    python -m seeds.retell_agent --create --voice-id <id>
    python -m seeds.retell_agent --update            # re-push config to RETELL_AGENT_ID
    python -m seeds.retell_agent --point-number +13125551234
    python -m seeds.retell_agent --show

One Retell agent serves every tenant. The shop is resolved per call from the
number dialled, so onboarding a business is a row in `channel_endpoints` - not a
new agent, not a new prompt, not a new thing to keep in sync.

The one place that cannot be per-call is the language set: Retell picks the
speech recogniser and the voice when the agent is configured, and it cannot
stitch language coverage together mid-call. So the agent's languages are the
*union* of every shop's enabled languages, computed from the database by
default, and re-pushing after onboarding a shop with a new language is a real
step rather than a formality.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import select

from app.config import get_settings
from app.db import dispose_engine, session_scope
from app.integrations import retell
from app.models import ClientConfig

#: What is actually known about each provider's language reach. Deliberately
#: short and hedged: Retell publishes no per-model language table, so anything
#: more confident than this would be invented.
MULTILINGUAL_NOTES: dict[str, str] = {
    "elevenlabs": (
        "multilingual via voice_model=eleven_multilingual_v2 (or v3/v4). "
        "TTS costs $0.060/min instead of $0.015 - the safe choice."
    ),
    "platform": "Retell's own, $0.015/min TTS. Includes Mexican-accent Spanish voices.",
    "cartesia": "$0.015/min TTS. sonic-3 models; Spanish and French voices exist.",
    "minimax": "$0.015/min TTS.",
    "inworld": "$0.015/min TTS.",
    "openai": "$0.015/min TTS.",
    "fish_audio": "$0.015/min TTS.",
    "xai": "$0.015/min TTS; these carry no accent metadata at all.",
}


async def union_of_enabled_languages() -> list[str]:
    """Every language any shop has turned on, plus English."""
    async with session_scope() as session:
        rows = await session.execute(select(ClientConfig.enabled_languages))
        codes: set[str] = {"en"}
        for (enabled,) in rows:
            for code in enabled or []:
                cleaned = (code or "").strip().lower()
                if cleaned:
                    codes.add(cleaned)
    return sorted(codes)


def _urls() -> tuple[str, str]:
    settings = get_settings()
    websocket = retell.websocket_url(settings.public_base_url, settings.retell_ws_secret)
    webhook = f"{settings.public_base_url.rstrip('/')}/webhooks/retell/events"
    return websocket, webhook


def _preflight(*, needs_urls: bool, needs_voice: bool) -> str | None:
    """Check only what this particular action actually needs.

    Listing voices needs nothing but the key - demanding a websocket secret and
    a public URL first would block the very step you run *in order to* decide
    what to configure.
    """
    settings = get_settings()
    if not settings.retell_api_key:
        return (
            "RETELL_API_KEY is not set. Check the spelling in .env - it is easy "
            "to write RETEL_API_KEY with one L, which reads as nothing at all."
        )
    if not needs_urls:
        return None
    if not settings.retell_ws_secret:
        return (
            "RETELL_WS_SECRET is not set. Generate one with:\n"
            '  python -c "import secrets; print(secrets.token_urlsafe(32))"'
        )
    if settings.public_base_url.startswith(("http://localhost", "http://127.")):
        return (
            f"PUBLIC_BASE_URL is still {settings.public_base_url!r}. Retell has to "
            "reach this service from the internet, so set it to the deployed URL "
            "before creating the agent - it is baked into the websocket URL Retell "
            "stores."
        )
    if needs_voice and not settings.retell_voice_id:
        return (
            "No voice chosen. Run --list-voices, pick one that covers every "
            "language your shops serve, then pass --voice-id or set RETELL_VOICE_ID."
        )
    return None


async def do_list_voices() -> None:
    """Print the account's voices, grouped so a choice can actually be made.

    A Retell voice object carries no language list - only `accent` - so the
    thing this listing cannot tell you is the thing that matters most. Language
    coverage is a property of the provider and its `voice_model`, and Retell
    does not publish a per-model language table either, so the honest end of
    this is a test call in each language you serve. The preview URLs below are
    the fastest way to hear one before committing.
    """
    voices = await retell.list_voices()
    if not voices:
        print("Retell returned no voices for this account.")
        return

    by_provider: dict[str, list[dict]] = {}
    for voice in voices:
        by_provider.setdefault(str(voice.get("provider") or "?"), []).append(voice)

    print(f"{len(voices)} voices from {len(by_provider)} providers.")
    print()
    for provider, group in sorted(by_provider.items(), key=lambda kv: -len(kv[1])):
        recommended = [v for v in group if v.get("recommended")]
        note = MULTILINGUAL_NOTES.get(provider, "")
        print(f"{provider}  ({len(group)} voices, {len(recommended)} recommended)")
        if note:
            print(f"    {note}")
        # Recommended first, then anything with a non-American accent, because
        # those are the ones worth auditioning for a multilingual shop.
        interesting = recommended or [
            v for v in group if (v.get("accent") or "American") != "American"
        ]
        for voice in interesting[:8]:
            voice_id = str(voice.get("voice_id") or "?")
            accent = str(voice.get("accent") or "-")
            gender = str(voice.get("gender") or "-")
            preview = str(voice.get("preview_audio_url") or "")
            print(f"    {voice_id:<38} {accent:<11} {gender:<7} {preview}")
        print()

    print("Then either:")
    print("  RETELL_VOICE_ID=<id>  in .env, or  --voice-id <id>")
    print("  RETELL_VOICE_MODEL=eleven_multilingual_v2   for a multilingual shop")
    print()
    print(
        "RETELL_VOICE_MODEL is the setting that decides whether a Polish caller "
        "is spoken to in Polish. Leaving it blank uses Retell's default for that "
        "voice, which is cheapest and usually English-first."
    )


async def do_create(voice_id: str, languages: list[str]) -> None:
    websocket, webhook = _urls()
    settings = get_settings()
    agent = await retell.create_agent(
        websocket=websocket,
        voice_id=voice_id,
        languages=languages,
        webhook_url=webhook,
        max_call_seconds=settings.retell_max_call_seconds,
        voice_model=settings.retell_voice_model,
    )
    agent_id = agent.get("agent_id") if isinstance(agent, dict) else None
    print(f"Created agent {agent_id}")
    print(f"  websocket : {websocket}")
    print(f"  webhook   : {webhook}")
    print(f"  languages : {', '.join(retell.locales_for(languages))}")
    print(f"\nNow set RETELL_AGENT_ID={agent_id} and point a number at it:")
    print("  python -m seeds.retell_agent --point-number +1...")


async def do_update(agent_id: str, voice_id: str, languages: list[str]) -> None:
    websocket, webhook = _urls()
    settings = get_settings()
    await retell.update_agent(
        agent_id,
        websocket=websocket,
        voice_id=voice_id,
        languages=languages,
        webhook_url=webhook,
        max_call_seconds=settings.retell_max_call_seconds,
        voice_model=settings.retell_voice_model,
    )
    print(f"Updated agent {agent_id}")
    print(f"  websocket : {websocket}")
    print(f"  webhook   : {webhook}")
    print(f"  languages : {', '.join(retell.locales_for(languages))}")


async def do_point_number(number: str, agent_id: str) -> None:
    settings = get_settings()
    inbound = f"{settings.public_base_url.rstrip('/')}/webhooks/retell/inbound"
    await retell.point_number_at_agent(number, agent_id, inbound)
    print(f"{number} now rings agent {agent_id}")
    print(f"  inbound webhook: {inbound}")
    print(
        "\nAdd the same number as a `voice` endpoint for the shop in the console, "
        "or its callers will be declined - that mapping is how the call finds a "
        "tenant."
    )


async def do_show() -> None:
    settings = get_settings()
    websocket, webhook = _urls()
    print("Configured here:")
    print(f"  public base url : {settings.public_base_url}")
    print(f"  agent id        : {settings.retell_agent_id or '(not set)'}")
    print(f"  voice id        : {settings.retell_voice_id or '(not set)'}")
    print(f"  voice model     : {settings.retell_voice_model or '(Retell default)'}")
    print(f"  api key         : {'set' if settings.retell_api_key else '(not set)'}")
    print(f"  ws secret       : {'set' if settings.retell_ws_secret else '(not set)'}")
    print(f"  websocket url   : {websocket}")
    print(f"  events webhook  : {webhook}")
    print(f"  inbound webhook : {settings.public_base_url.rstrip('/')}/webhooks/retell/inbound")

    if not settings.retell_agent_id:
        return
    try:
        agent = await retell.get_agent(settings.retell_agent_id)
    except retell.RetellError as exc:
        print(f"\nCould not read the agent back: {exc}")
        return
    engine = (agent or {}).get("response_engine") or {}
    live = engine.get("llm_websocket_url")
    print("\nAs Retell has it:")
    print(f"  websocket url   : {live}")
    print(f"  languages       : {agent.get('language')}")
    print(f"  voice id        : {agent.get('voice_id')}")
    print(f"  voice model     : {agent.get('voice_model')}")
    print(f"  events webhook  : {agent.get('webhook_url')}")
    if live != websocket:
        print(
            "\n  MISMATCH: Retell is pointing at a different websocket than this "
            "service would serve. Run --update."
        )


async def main() -> None:
    parser = argparse.ArgumentParser(description="Configure Retell for Chichi voice.")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--list-voices", action="store_true")
    action.add_argument("--create", action="store_true")
    action.add_argument("--update", action="store_true")
    action.add_argument("--point-number", metavar="E164")
    action.add_argument("--show", action="store_true")
    parser.add_argument("--voice-id", help="defaults to RETELL_VOICE_ID")
    parser.add_argument(
        "--agent-id", help="defaults to RETELL_AGENT_ID (for --update / --point-number)"
    )
    parser.add_argument(
        "--languages",
        help=(
            "comma-separated ISO codes. Defaults to the union of every shop's "
            "enabled languages."
        ),
    )
    args = parser.parse_args()

    settings = get_settings()
    needs_voice = bool(args.create or args.update)
    # Only creating the agent or pointing a number at it depends on the public
    # URL and the websocket secret; listing voices and --show do not.
    needs_urls = bool(args.create or args.update or args.point_number)
    problem = _preflight(needs_urls=needs_urls, needs_voice=needs_voice)
    if problem and not args.show:
        print(problem)
        sys.exit(1)

    try:
        if args.list_voices:
            await do_list_voices()
            return
        if args.show:
            await do_show()
            return

        languages = (
            [c.strip() for c in args.languages.split(",") if c.strip()]
            if args.languages
            else await union_of_enabled_languages()
        )
        voice_id = args.voice_id or settings.retell_voice_id
        agent_id = args.agent_id or settings.retell_agent_id

        if args.create:
            await do_create(voice_id, languages)
        elif args.update:
            if not agent_id:
                print("No agent id. Set RETELL_AGENT_ID or pass --agent-id.")
                sys.exit(1)
            await do_update(agent_id, voice_id, languages)
        elif args.point_number:
            if not agent_id:
                print("No agent id. Set RETELL_AGENT_ID or pass --agent-id.")
                sys.exit(1)
            await do_point_number(args.point_number, agent_id)
    except retell.RetellError as exc:
        print(f"Retell refused the call: {exc}")
        sys.exit(1)
    finally:
        await dispose_engine()


if __name__ == "__main__":
    asyncio.run(main())
