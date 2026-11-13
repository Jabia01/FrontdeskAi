"""Retell webhook authenticity.

This is the only thing standing between a shop's call history and anyone who
knows our URL, so the tests here are about the ways a signature check is
usually wrong rather than the way it is right: a happy path that passes proves
almost nothing on its own.
"""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest

from app.gateway.retell_signature import MAX_SKEW_MS, SignatureError, sign, verify

KEY = "test-retell-api-key"
NOW = 1_762_900_000_000  # a fixed clock, so no test depends on the real one


class TestAGenuineWebhook:
    def test_it_is_accepted(self):
        body = json.dumps({"event": "call_ended"}).encode()
        verify(body, sign(body, KEY, now_ms=NOW), KEY, now_ms=NOW)

    def test_verify_returns_none_rather_than_true(self):
        """A `verify() -> bool` is one forgotten `if` away from no check at all,
        so this one raises instead and returns nothing on success."""
        body = b'{"event":"call_started"}'
        assert verify(body, sign(body, KEY, now_ms=NOW), KEY, now_ms=NOW) is None

    def test_a_body_with_unicode_survives_the_round_trip(self):
        """Arabic and Chinese appear in real transcripts, and a signature
        computed over the wrong encoding passes for everything but those."""
        body = json.dumps(
            {"transcript": "مرحبا، كم سعر تغيير الزيت؟"}, ensure_ascii=False
        ).encode("utf-8")
        verify(body, sign(body, KEY, now_ms=NOW), KEY, now_ms=NOW)


class TestAForgery:
    def test_a_different_key_is_rejected(self):
        body = b'{"event":"call_ended"}'
        signature = sign(body, "someone-elses-key", now_ms=NOW)
        with pytest.raises(SignatureError, match="does not match"):
            verify(body, signature, KEY, now_ms=NOW)

    def test_a_tampered_body_is_rejected(self):
        body = b'{"event":"call_ended","call":{"call_id":"abc"}}'
        signature = sign(body, KEY, now_ms=NOW)
        tampered = body.replace(b"abc", b"xyz")
        with pytest.raises(SignatureError, match="does not match"):
            verify(tampered, signature, KEY, now_ms=NOW)

    def test_a_missing_header_is_rejected(self):
        with pytest.raises(SignatureError, match="missing or malformed"):
            verify(b"{}", None, KEY, now_ms=NOW)

    @pytest.mark.parametrize(
        "header",
        [
            "",
            "nonsense",
            "v=123",
            "d=abc",
            "v=abc,d=def",  # timestamp must be digits
            "v=123,d=nothex!",
            "V=123,D=ABC",  # the key names are not interchangeable
        ],
    )
    def test_a_malformed_header_is_rejected(self, header):
        with pytest.raises(SignatureError, match="missing or malformed"):
            verify(b"{}", header, KEY, now_ms=NOW)

    def test_an_unsigned_request_cannot_pass_by_leaving_the_key_blank(self):
        """The failure mode where an empty key makes an empty digest match."""
        with pytest.raises(SignatureError, match="no Retell API key"):
            verify(b"{}", "v=1,d=00", "", now_ms=NOW)


class TestReplay:
    def test_a_signature_from_last_week_is_rejected(self):
        body = b'{"event":"call_ended"}'
        old = sign(body, KEY, now_ms=NOW - MAX_SKEW_MS - 1)
        with pytest.raises(SignatureError, match="outside the 5 minute window"):
            verify(body, old, KEY, now_ms=NOW)

    def test_a_signature_from_the_near_future_is_accepted(self):
        """Clocks drift in both directions; only one of them is an attack."""
        body = b'{"event":"call_ended"}'
        ahead = sign(body, KEY, now_ms=NOW + 60_000)
        verify(body, ahead, KEY, now_ms=NOW)

    def test_the_timestamp_is_part_of_what_is_signed(self):
        """Otherwise an old signature could simply be re-stamped with a fresh
        timestamp and replayed forever."""
        body = b'{"event":"call_ended"}'
        signature = sign(body, KEY, now_ms=NOW)
        digest = signature.split("d=")[1]
        restamped = f"v={NOW + 1000},d={digest}"
        with pytest.raises(SignatureError, match="does not match"):
            verify(body, restamped, KEY, now_ms=NOW)


class TestTheReserialisationAllowance:
    """Retell's docs sign the raw body; their SDK examples sign a
    re-serialisation of it. We accept either form of the same parsed body - but
    only forms that still HMAC correctly under our key."""

    def _signature_over(self, text: str, timestamp: int) -> str:
        digest = hmac.new(
            KEY.encode(), (text + str(timestamp)).encode("utf-8"), hashlib.sha256
        ).hexdigest()
        return f"v={timestamp},d={digest}"

    def test_a_compact_reserialisation_is_accepted(self):
        sent = b'{"event": "call_ended", "call": {"call_id": "abc"}}'
        compact = json.dumps(json.loads(sent), separators=(",", ":"))
        verify(sent, self._signature_over(compact, NOW), KEY, now_ms=NOW)

    def test_a_spaced_reserialisation_is_accepted(self):
        sent = b'{"event":"call_ended","call":{"call_id":"abc"}}'
        spaced = json.dumps(json.loads(sent), separators=(", ", ": "))
        verify(sent, self._signature_over(spaced, NOW), KEY, now_ms=NOW)

    def test_the_allowance_does_not_accept_a_different_body(self):
        """The important one. Reformatting is forgiven; changing a value is not,
        because every candidate is still a re-serialisation of *this* body."""
        sent = b'{"event":"call_ended","call":{"call_id":"abc"}}'
        other = json.dumps({"event": "call_ended", "call": {"call_id": "xyz"}})
        with pytest.raises(SignatureError, match="does not match"):
            verify(sent, self._signature_over(other, NOW), KEY, now_ms=NOW)

    def test_a_body_that_is_not_json_is_still_checked_as_raw_bytes(self):
        body = b"not json at all"
        verify(body, sign(body, KEY, now_ms=NOW), KEY, now_ms=NOW)

    def test_a_body_that_is_not_utf8_is_rejected_rather_than_crashing(self):
        with pytest.raises(SignatureError, match="not valid UTF-8"):
            verify(b"\xff\xfe\x00", f"v={NOW},d=ab", KEY, now_ms=NOW)
