"""Twilio SMS adapter.

No 24-hour window on SMS, but every 160 characters is a billable segment, so the
style block in client_config keeps replies short.
"""

from __future__ import annotations

from app.channels.base import register
from app.channels.twilio_base import TwilioAdapterBase
from app.models import Channel


class TwilioSmsAdapter(TwilioAdapterBase):
    channel = Channel.sms
    enforces_free_form_window = False


adapter = register(TwilioSmsAdapter())
