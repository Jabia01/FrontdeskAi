"""Where the data actually is, as one structure the GDPR page is generated from.

This file exists so that the hosting claim on a public page cannot drift from
the hosting configuration. `HOSTING_REGION` is pinned to `render.yaml` by a test
in `tests/test_deploy_readiness.py`: moving the service to Frankfurt without
updating this file fails the build, and - more to the point - *saying* we are in
Frankfurt while the blueprint says Ohio fails it too. That second direction is
the one that matters, because it is the one that would be a false statement to a
clinic's data protection officer.

Everything here is a fact about the deployment, not a legal position. Where a
legal position is needed - the transfer mechanism, the retention period we
commit to, who our Art. 27 representative is - the value is `TODO` and the page
renders it visibly. See `app/web/brand.py` for why that is the pattern.
"""

from __future__ import annotations

from typing import Any

from app.web.brand import TODO

#: The Render region the service runs in, as written in `render.yaml`. A test
#: reads both and fails if they disagree.
HOSTING_REGION = "ohio"

#: Render's region names to somewhere a person can place on a map. Only the
#: regions we might plausibly be in; an unknown region fails the test rather
#: than rendering a blank.
REGION_LOCATIONS: dict[str, str] = {
    "ohio": "Ohio, United States",
    "oregon": "Oregon, United States",
    "virginia": "Virginia, United States",
    "frankfurt": "Frankfurt, Germany",
    "singapore": "Singapore",
}

#: True while any personal data leaves the EEA. Derived rather than typed, so
#: it cannot say one thing while `HOSTING_REGION` says another.
EU_REGIONS = frozenset({"frankfurt"})


def hosting_location() -> str:
    return REGION_LOCATIONS.get(HOSTING_REGION, HOSTING_REGION)


def hosted_in_eu() -> bool:
    return HOSTING_REGION in EU_REGIONS


#: Everyone who touches the data, what they touch, and where they are. This is
#: the list a DPO asks for, and under GDPR Art. 28(2) it is also the list a
#: business has to be told about before we add to it.
#:
#: `location` is a fact. `safeguard` is a legal position, and is TODO until a
#: lawyer has looked at it - an unreviewed claim that a transfer is covered by
#: the EU-US Data Privacy Framework is exactly the kind of sentence that is
#: worse than silence.
SUBPROCESSORS: list[dict[str, Any]] = [
    {
        "name": "Render",
        "role": "Runs the application itself.",
        "data": "Everything, in transit. Nothing is stored on the service.",
        "location": hosting_location(),
        "safeguard": TODO,
    },
    {
        "name": "Supabase",
        "role": "The database.",
        "data": (
            "Conversations, phone numbers, names, appointments, and the "
            "business's own configuration."
        ),
        "location": "US East (North Virginia), United States",
        "safeguard": TODO,
    },
    {
        "name": "Upstash",
        "role": "The job queue.",
        "data": (
            "Identifiers of messages waiting to be handled. No message text "
            "and no phone numbers."
        ),
        # Not known from the repository: the queue is configured by pasting a
        # connection string in, and the region is a property of that Upstash
        # database rather than of anything here. Filling this in is a matter of
        # reading it off the Upstash console, not of asking a lawyer.
        "location": TODO,
        "safeguard": TODO,
    },
    {
        "name": "Anthropic",
        "role": "The model that writes the replies.",
        "data": (
            "The current conversation and the business's own configuration, "
            "sent with each reply."
        ),
        "location": "United States",
        "safeguard": TODO,
    },
    {
        "name": "Twilio",
        "role": "Delivers the SMS and WhatsApp messages.",
        "data": "Message text and phone numbers.",
        "location": "United States, with global delivery infrastructure",
        "safeguard": TODO,
    },
    {
        "name": "Retell AI",
        "role": "Answers phone calls: speech to text, and text to speech.",
        "data": (
            "Call audio and its transcript, while the call is happening. Only "
            "for businesses with the voice channel switched on."
        ),
        "location": "United States",
        "safeguard": TODO,
    },
    {
        "name": "Google",
        "role": "Calendar, for businesses that connect one.",
        "data": "Appointment times, and the customer's name on the booking.",
        "location": TODO,
        "safeguard": TODO,
    },
]


def placeholders() -> list[str]:
    """Which residency facts are still unfilled, for the startup warning.

    Same contract as `brand.placeholders()`: a page that reads finished while
    its transfer mechanism is unstated is the failure this prevents.
    """
    out = []
    for row in SUBPROCESSORS:
        out.extend(
            f"{row['name']}.{key}" for key, value in row.items() if value == TODO
        )
    return sorted(out)
