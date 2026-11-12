"""The system prompt must be byte-stable, or prompt caching silently stops working."""

from __future__ import annotations

from app.agent.prompt import (
    build_base_prompt,
    build_system_prompt,
    config_prompt_version,
    prompt_version,
)
from app.agent.tools import tool_definitions, tool_names

SHOP = {
    "name": "Westside Auto Repair",
    "timezone": "America/Chicago",
    "config": {
        "business_info": {"address": "1 Test St", "payment": "Cards and cash"},
        "services": ["Brakes", "Oil change"],
        "hours": {"monday": "8:00-18:00", "sunday": "closed"},
        "price_ranges": [{"service": "Front brakes", "low": 300, "high": 450}],
        "tone": "Friendly and brief.",
        "handoff_rules": {"keywords": ["tow", "accident"], "notes": "Over $1500."},
        "faqs": [{"q": "Walk-ins?", "a": "Yes."}],
        "channel_style": {"sms": {"max_chars": 320, "rules": "Short."}},
    },
}


class TestDeterminism:
    def test_identical_input_gives_identical_bytes(self):
        a = build_system_prompt(SHOP, "sms")
        b = build_system_prompt(SHOP, "sms")
        assert a == b
        assert prompt_version(a) == prompt_version(b)

    def test_dict_ordering_does_not_change_the_prompt(self):
        """An unsorted dict must not produce a different cache prefix."""
        reordered = {
            "timezone": SHOP["timezone"],
            "name": SHOP["name"],
            "config": {
                "channel_style": SHOP["config"]["channel_style"],
                "faqs": SHOP["config"]["faqs"],
                "tone": SHOP["config"]["tone"],
                "handoff_rules": {
                    "notes": "Over $1500.",
                    "keywords": ["accident", "tow"],  # reversed
                },
                "price_ranges": SHOP["config"]["price_ranges"],
                "hours": {"sunday": "closed", "monday": "8:00-18:00"},  # reversed
                "services": SHOP["config"]["services"],
                "business_info": {
                    "payment": "Cards and cash",
                    "address": "1 Test St",
                },
            },
        }
        assert build_system_prompt(reordered, "sms") == build_system_prompt(
            SHOP, "sms"
        )

    def test_no_timestamp_leaks_in(self):
        text = build_system_prompt(SHOP, "sms")
        # A rendered year or an ISO timestamp would move every single request.
        assert "2026" not in text
        assert "T00:" not in text

    def test_tool_definitions_are_stable_and_ordered(self):
        """Tools render before `system`, so their order is part of the prefix."""
        assert tool_definitions() == tool_definitions()
        assert tool_names() == [
            "get_business_info",
            "create_lead",
            "handoff_to_human",
            "check_availability",
            "book_appointment",
            "get_job_status",
        ]

    def test_every_tool_is_strict_and_closed(self):
        for tool in tool_definitions():
            assert tool["strict"] is True, tool["name"]
            schema = tool["input_schema"]
            assert schema["additionalProperties"] is False, tool["name"]
            # strict requires every property to be listed in `required`
            assert set(schema["required"]) == set(schema["properties"]), tool["name"]


class TestChannelVariants:
    def test_channels_get_different_prompts(self):
        sms = build_system_prompt(SHOP, "sms")
        voice = build_system_prompt(SHOP, "voice")
        assert sms != voice
        assert prompt_version(sms) != prompt_version(voice)

    def test_voice_style_forbids_urls_and_lists(self):
        voice = build_system_prompt(SHOP, "voice")
        assert "No lists" in voice or "no lists" in voice
        assert "URL" in voice or "no URLs" in voice

    def test_base_prompt_is_channel_independent(self):
        assert build_base_prompt(SHOP) in build_system_prompt(SHOP, "sms")
        assert build_base_prompt(SHOP) in build_system_prompt(SHOP, "voice")


class TestGuardrails:
    def test_prompt_carries_every_guardrail(self):
        text = build_system_prompt(SHOP, "sms")
        assert "Never state a firm price" in text
        assert "only help with" in text  # off-topic refusal
        assert "handoff_to_human" in text
        assert "Never reveal" in text
        assert "never offer a menu" in text.lower()

    def test_booking_rules_forbid_inventing_a_time(self):
        text = build_system_prompt(SHOP, "sms")
        assert "check_availability" in text
        assert "book_appointment" in text
        assert "copied from that result exactly" in text
        assert "Never invent, guess, hold or promise a time" in text

    def test_job_status_must_not_be_guessed(self):
        text = build_system_prompt(SHOP, "sms")
        assert "get_job_status" in text
        assert "never guess a collection time" in text

    def test_configured_price_ranges_appear_as_ranges(self):
        text = build_system_prompt(SHOP, "sms")
        assert "Front brakes: $300-$450" in text

    def test_missing_price_ranges_warn_against_guessing(self):
        shop = {**SHOP, "config": {**SHOP["config"], "price_ranges": []}}
        assert "never quote a number" in build_system_prompt(shop, "sms")

    def test_owner_handoff_keywords_are_included(self):
        text = build_system_prompt(SHOP, "sms")
        assert "accident, tow" in text  # sorted
        assert "Over $1500." in text


def test_prompt_clears_the_haiku_cache_minimum():
    """Haiku 5.5 needs a 512-token prefix before anything caches at all."""
    text = build_system_prompt(SHOP, "sms")
    # ~4 chars per token is the usual rough ratio; this fixture is a small shop.
    assert len(text) > 512 * 2, (
        f"prompt is only {len(text)} chars - a real shop config must clear "
        f"roughly 2KB or prompt caching silently never engages"
    )


class TestConfigFingerprint:
    """`config_prompt_version` is what the console stores as `prompt_version`.

    It must move whenever anything a config edit can touch moves - including
    the pieces that live in the per-channel rules rather than the base prompt,
    which is the mistake the first version of the console made.
    """

    CHANNELS = ("sms", "whatsapp", "simulator", "voice")

    def version(self, shop) -> str:
        return config_prompt_version(shop, self.CHANNELS)

    def test_it_is_stable(self):
        assert self.version(SHOP) == self.version(SHOP)

    def test_channel_order_does_not_matter(self):
        assert config_prompt_version(SHOP, ["voice", "sms"]) == (
            config_prompt_version(SHOP, ["sms", "voice"])
        )

    def test_a_tone_change_moves_it(self):
        """Tone is rendered into the per-channel rules, not the base prompt, so
        hashing the base prompt alone would have missed this entirely."""
        changed = {**SHOP, "config": {**SHOP["config"], "tone": "Blunt."}}
        assert self.version(changed) != self.version(SHOP)

    def test_a_channel_style_change_moves_it(self):
        changed = {
            **SHOP,
            "config": {
                **SHOP["config"],
                "channel_style": {"sms": {"max_chars": 100, "rules": "Tiny."}},
            },
        }
        assert self.version(changed) != self.version(SHOP)

    def test_an_owner_language_change_moves_it(self):
        changed = {**SHOP, "config": {**SHOP["config"], "owner_language": "es"}}
        assert self.version(changed) != self.version(SHOP)

    def test_a_services_change_moves_it(self):
        changed = {**SHOP, "config": {**SHOP["config"], "services": ["Tires"]}}
        assert self.version(changed) != self.version(SHOP)

    def test_it_is_short_enough_for_the_column(self):
        # client_config.prompt_version is String(64).
        assert len(self.version(SHOP)) == 16
