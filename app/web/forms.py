"""Parsing between the console's config form and the JSONB it is stored in.

Pure functions, no database, no request - which is the point: this is where the
bugs would be, so it is all unit-testable (`tests/test_web_forms.py`).

Two rules the shapes here exist to enforce:

  * A field the agent reads must never be saveable in a shape the agent cannot
    read. Hours are validated with `core.booking.parse_hours`, the booking
    engine's own parser, so the console cannot save "8-6pm" and leave a shop
    silently closed all week.
  * Errors name the line that is wrong. "Line 3: the high price must be a
    number" is actionable; "invalid JSON" is not.
"""

from __future__ import annotations

import json
from typing import Any

from app.core.booking import parse_hours
from app.core.defaults import WEEKDAYS


class FormError(ValueError):
    """A human-readable problem with one field. Shown next to the form."""


# ----------------------------------------------------------------- simple lists


def lines(text: str | None) -> list[str]:
    """Textarea -> list, one item per line, blanks dropped."""
    return [line.strip() for line in (text or "").splitlines() if line.strip()]


def from_lines(items: list[Any] | None) -> str:
    return "\n".join(str(item) for item in (items or []))


def csv_list(text: str | None) -> list[str]:
    """"en, es" -> ["en", "es"]. Order is kept: it is a priority order."""
    out: list[str] = []
    for part in (text or "").replace("\n", ",").split(","):
        value = part.strip().lower()
        if value and value not in out:
            out.append(value)
    return out


def to_csv(items: list[Any] | None) -> str:
    return ", ".join(str(item) for item in (items or []))


# --------------------------------------------------------------- piped records


def _split(line: str, count: int, lineno: int) -> list[str]:
    parts = [p.strip() for p in line.split("|")]
    if len(parts) < count:
        raise FormError(
            f"Line {lineno}: expected {count} parts separated by | "
            f"but found {len(parts)}."
        )
    if count == 2:
        key, _, rest = line.partition("|")
        return [key.strip(), rest.strip()]
    return parts[:count]


def price_ranges_from_text(text: str | None) -> list[dict]:
    """`Front brakes | 300 | 450` per line.

    A range, never a single number: the prompt is explicit that the agent may
    only ever quote a range, and a malformed row here would become an invented
    firm price.
    """
    rows: list[dict] = []
    for lineno, line in enumerate(lines(text), start=1):
        service, low_raw, high_raw = _split(line, 3, lineno)
        if not service:
            raise FormError(f"Line {lineno}: the service name is missing.")
        try:
            low = int(float(low_raw))
            high = int(float(high_raw))
        except ValueError:
            raise FormError(
                f"Line {lineno}: the low and high prices must both be numbers "
                f'(got "{low_raw}" and "{high_raw}").'
            ) from None
        if low < 0 or high < 0:
            raise FormError(f"Line {lineno}: prices cannot be negative.")
        if low > high:
            raise FormError(
                f"Line {lineno}: the low price ({low}) is above the high "
                f"price ({high})."
            )
        rows.append({"service": service, "low": low, "high": high})
    return rows


def price_ranges_to_text(rows: list[dict] | None) -> str:
    return "\n".join(
        f"{row.get('service', '')} | {row.get('low', '')} | {row.get('high', '')}"
        for row in (rows or [])
    )


def faqs_from_text(text: str | None) -> list[dict]:
    """`Do you take walk-ins? | Yes, any time before 4pm.` per line."""
    rows: list[dict] = []
    for lineno, line in enumerate(lines(text), start=1):
        question, answer = _split(line, 2, lineno)
        if not question or not answer:
            raise FormError(
                f"Line {lineno}: a FAQ needs both a question and an answer, "
                f"separated by |."
            )
        rows.append({"q": question, "a": answer})
    return rows


def faqs_to_text(rows: list[dict] | None) -> str:
    return "\n".join(
        f"{row.get('q', '')} | {row.get('a', '')}" for row in (rows or [])
    )


def info_from_text(text: str | None) -> dict:
    """`address | 1200 W Grand Ave` per line. Keys are kept as typed."""
    out: dict[str, str] = {}
    for lineno, line in enumerate(lines(text), start=1):
        key, value = _split(line, 2, lineno)
        if not key or not value:
            raise FormError(
                f"Line {lineno}: expected a label and a value separated by |."
            )
        out[key] = value
    return out


def info_to_text(info: dict | None) -> str:
    return "\n".join(f"{key} | {value}" for key, value in (info or {}).items())


# ---------------------------------------------------------------------- hours


def hours_from_values(values: dict[str, str]) -> dict:
    """One input per weekday. Validated with the booking engine's own parser."""
    out: dict[str, str] = {}
    for day in WEEKDAYS:
        raw = (values.get(day) or "").strip()
        if not raw or raw.lower() in {"closed", "-"}:
            out[day] = "closed"
            continue
        if parse_hours(raw) is None:
            raise FormError(
                f"{day.capitalize()}: \"{raw}\" is not a time range. "
                f'Use "8:00-18:00", or "closed".'
            )
        out[day] = raw
    return out


# ----------------------------------------------------------------------- json


def json_object(text: str | None, *, label: str) -> dict:
    """For the rarely-touched structured fields, edited as JSON."""
    raw = (text or "").strip()
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise FormError(f"{label}: invalid JSON on line {exc.lineno} - {exc.msg}.") from None
    if not isinstance(value, dict):
        raise FormError(f"{label}: expected a JSON object, got {type(value).__name__}.")
    return value


def json_dump(value: Any) -> str:
    """Stable, readable, and diffable in the audit log."""
    return json.dumps(value or {}, indent=2, sort_keys=True, ensure_ascii=False)


# -------------------------------------------------------------------- numbers


def positive_int(raw: str | None, *, label: str, default: int, maximum: int) -> int:
    text = (raw or "").strip()
    if not text:
        return default
    try:
        value = int(text)
    except ValueError:
        raise FormError(f"{label}: must be a whole number.") from None
    if value < 1 or value > maximum:
        raise FormError(f"{label}: must be between 1 and {maximum}.")
    return value


def bounded_int(
    raw: str | None, *, label: str, default: int, minimum: int, maximum: int
) -> int:
    """Like `positive_int`, but zero can be a real setting.

    `reminder_hours_before = 0` is how reminders are turned off, so a helper
    that insists on 1 or more would make that unreachable from the form.
    """
    text = (raw or "").strip()
    if not text:
        return default
    try:
        value = int(text)
    except ValueError:
        raise FormError(f"{label}: must be a whole number.") from None
    if value < minimum or value > maximum:
        raise FormError(f"{label}: must be between {minimum} and {maximum}.")
    return value


def changed_fields(before: dict, after: dict) -> list[str]:
    """Which config keys a save actually altered - recorded in the audit log."""
    keys = sorted(set(before) | set(after))
    return [key for key in keys if before.get(key) != after.get(key)]
