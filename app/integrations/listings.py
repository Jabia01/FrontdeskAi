"""Where property listings come from.

An agency's stock lives in Inmovilla, Resales Online, a WordPress plugin or a
spreadsheet, and every one of them has a different shape. None of that belongs
in a tool handler, so the handler talks to this interface and nothing else.

The implementation shipped here reads whatever the agency has typed into its
own config. That is deliberately the dumbest possible source: it needs no
credentials, no network call inside a webhook, and no partner integration
before the first agency can be onboarded. When a real feed is wired up, it
implements `ListingSource` and the handler does not change.

The one rule every implementation inherits: **return the agency's own words**.
A listing's text is what the agency wrote. Nothing here summarises, translates
or embellishes it, because a square-metre figure or a licence status invented
in transit is the kind of error that ends in a lawyer's letter.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class Listing:
    """One property, as the agency described it."""

    reference: str
    summary: str
    price: str = ""
    area: str = ""
    bedrooms: str = ""
    url: str = ""

    def as_dict(self) -> dict[str, str]:
        out = {"reference": self.reference, "summary": self.summary}
        for key in ("price", "area", "bedrooms", "url"):
            value = getattr(self, key)
            if value:
                out[key] = value
        return out


class ListingSource(Protocol):
    """Anything that can answer "what have we got like this?"."""

    async def search(
        self,
        config: dict[str, Any],
        *,
        area: str,
        max_price: str,
        bedrooms: str,
        limit: int = 3,
    ) -> list[Listing]: ...


class ConfigListingSource:
    """Listings typed into `business_info.listings` by the agency.

    Filtering is deliberately crude - substring on area, and a numeric compare
    on price when both sides parse as numbers. An agency with more stock than
    this handles comfortably wants a real feed, and that is what the interface
    is for.
    """

    async def search(
        self,
        config: dict[str, Any],
        *,
        area: str,
        max_price: str,
        bedrooms: str,
        limit: int = 3,
    ) -> list[Listing]:
        raw = (config.get("business_info") or {}).get("listings") or []
        wanted_area = " ".join(area.split()).lower()
        ceiling = _number(max_price)
        wanted_beds = _number(bedrooms)

        out: list[Listing] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            if wanted_area:
                haystack = " ".join(
                    str(item.get(k, "")) for k in ("area", "summary", "reference")
                ).lower()
                if wanted_area not in haystack:
                    continue
            if ceiling is not None:
                price = _number(str(item.get("price", "")))
                if price is not None and price > ceiling:
                    continue
            if wanted_beds is not None:
                beds = _number(str(item.get("bedrooms", "")))
                if beds is not None and beds < wanted_beds:
                    continue
            out.append(
                Listing(
                    reference=str(item.get("reference", "") or "?"),
                    summary=str(item.get("summary", "") or ""),
                    price=str(item.get("price", "") or ""),
                    area=str(item.get("area", "") or ""),
                    bedrooms=str(item.get("bedrooms", "") or ""),
                    url=str(item.get("url", "") or ""),
                )
            )
            if len(out) >= limit:
                break
        return out


def _number(text: str) -> float | None:
    """The leading number in a string like '850,000 EUR' or '3 beds'."""
    cleaned = []
    for char in str(text):
        if char.isdigit():
            cleaned.append(char)
        elif char in ".," and cleaned:
            continue
        elif cleaned:
            break
    if not cleaned:
        return None
    try:
        return float("".join(cleaned))
    except ValueError:
        return None


#: Swapped for a real feed by assigning a different implementation here.
SOURCE: ListingSource = ConfigListingSource()
