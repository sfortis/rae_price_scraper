"""Tariff matching and the provider/plan catalog, built from feed rows.

This module has no Home Assistant or package imports, so it can be tested on
its own. The config flow and the sensors both use find_members(), so the
price shown next to a plan in the flow is the price the sensor will show.
"""

from __future__ import annotations

import datetime
import re
from dataclasses import dataclass

# Must match const.SEGMENT_HOUSEHOLD; kept literal so this module stays
# importable without the package.
HOUSEHOLD = "household"

# The registry appends a publication version to plan names, e.g. "My Home
# Online" is published as "My Home Online 06.26". The trailing " MM.YY" is
# stripped so a configured plan keeps matching across version bumps.
_PLAN_VERSION_SUFFIX = re.compile(r"\s+\d{2}\.\d{2}$")

# How many months find_members() searches back from the current one.
SEARCH_MONTHS = 12


def plan_base(row_plan: str) -> str:
    """The plan name without the trailing publication version."""
    return _PLAN_VERSION_SUFFIX.sub("", row_plan or "").strip()


def plan_matches(row_plan: str, plan: str) -> bool:
    """Exact match against the configured plan, ignoring the version suffix."""
    if not row_plan:
        return False
    return plan_base(row_plan) == plan or row_plan == plan


def row_segment(row: dict) -> str:
    """The segment of a feed row; rows of older feeds have none."""
    return row.get("segment") or HOUSEHOLD


def feed_rows(payload: dict) -> list[dict]:
    """All rows of a feed payload, household and business.

    "rows" holds the household tariffs (it is all that versions before 0.12.0
    read). "business_rows" holds the business tariffs. Rows without a segment
    are stamped as household.
    """
    rows = []
    for row in payload.get("rows") or []:
        rows.append(row if "segment" in row else {**row, "segment": HOUSEHOLD})
    rows.extend(payload.get("business_rows") or [])
    return rows


def _previous_month(d: datetime.date) -> datetime.date:
    if d.month == 1:
        return d.replace(year=d.year - 1, month=12, day=1)
    return d.replace(month=d.month - 1, day=1)


def find_members(rows, provider, plan, segment=HOUSEHOLD, today=None):
    """Return (members, year, month) for the matched product, most recent month.

    A single tariff (one invoice-id) may span several feed rows: consumption
    tiers (distinct kWh bands) or day/night zones. All rows of one product are
    returned so the caller can build tier/zone sensors. Commercially available
    products are preferred over withdrawn ones, and when a plan name maps to
    more than one product in a month the first in feed order is taken.

    Returns ([], None, None) when nothing matches within SEARCH_MONTHS.
    """
    candidates = [
        r for r in rows
        if row_segment(r) == segment
        and r.get("provider") == provider
        and plan_matches(r.get("plan"), plan)
    ]
    search = (today or datetime.date.today()).replace(day=1)
    for _ in range(SEARCH_MONTHS):
        month_rows = [r for r in candidates
                      if r.get("month") == str(search.month)
                      and r.get("year") in (str(search.year), None)]
        if month_rows:
            available = [r for r in month_rows if r.get("available", True)]
            pool = available or month_rows
            by_invoice: dict = {}
            for r in pool:
                by_invoice.setdefault(r.get("invoice"), []).append(r)
            members = next(iter(by_invoice.values()))
            return members, search.year, search.month
        search = _previous_month(search)
    return [], None, None


def as_price(raw) -> float | None:
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def row_price(row: dict, discounted: bool) -> float | None:
    """The discounted (price_on) or standard (price_off) price of a row."""
    return as_price(row.get("price_on" if discounted else "price_off"))


def average_price(members, discounted: bool) -> float | None:
    """Average over the tiers/zones, or None if any price is unknown.

    An average over only the known tiers would be a wrong number (for hybrid
    plans it is just the first tier's price).
    """
    prices = [row_price(m, discounted) for m in members]
    if not prices or any(p is None for p in prices):
        return None
    return sum(prices) / len(prices)


def _price_range(members, discounted: bool) -> str | None:
    prices = [p for m in members if (p := row_price(m, discounted)) is not None]
    if not prices:
        return None
    lo, hi = min(prices), max(prices)
    return f"{lo:.4f}" if lo == hi else f"{lo:.4f}-{hi:.4f}"


def price_note(members) -> str:
    """Short price text for a dropdown label, e.g. '0.1421 €/kWh (disc. 0.1300)'."""
    standard = _price_range(members, False)
    discounted = _price_range(members, True)
    if standard is None and discounted is None:
        return "price not published"
    if discounted is None or discounted == standard:
        note = f"{standard} €/kWh"
    elif standard is None:
        note = f"disc. {discounted} €/kWh"
    else:
        note = f"{standard} €/kWh (disc. {discounted})"
    if len(members) > 1 and any(row_price(m, False) is None for m in members):
        # The average sensor stays unknown until every tier has a price.
        note += ", a tier price is not published"
    return note


@dataclass(frozen=True)
class PlanInfo:
    """What the feed says about one plan of a provider."""

    year: int
    month: int
    available: bool
    in_latest_period: bool

    def label(self, name: str, price: str | None = None) -> str:
        """The dropdown label: name, optional price, and a status note."""
        text = f"{name}: {price}" if price else name
        if not self.in_latest_period:
            return f"{text} [last listed {self.month:02d}/{self.year}]"
        if not self.available:
            return f"{text} [withdrawn]"
        return text


def _period(row) -> tuple[int, int] | None:
    try:
        return int(row.get("year")), int(row.get("month"))
    except (TypeError, ValueError):
        return None


def build_catalog(rows: list[dict], segment: str = HOUSEHOLD
                  ) -> dict[str, dict[str, PlanInfo]]:
    """Map each provider of a segment to its plans, by each plan's latest period.

    A plan is available when any of its rows in that period is commercially
    available. A plan that is missing from the newest period of the feed is
    still listed, because an existing customer can stay on an old tariff.
    """
    rows = [r for r in rows if row_segment(r) == segment]
    latest_feed = max((p for p in map(_period, rows) if p), default=None)
    seen: dict[tuple[str, str], tuple[tuple[int, int], bool]] = {}
    for row in rows:
        period = _period(row)
        provider = (row.get("provider") or "").strip()
        name = plan_base(row.get("plan"))
        if period is None or not provider or not name:
            continue
        available = bool(row.get("available", True))
        key = (provider, name)
        best = seen.get(key)
        if best is None or period > best[0]:
            seen[key] = (period, available)
        elif period == best[0] and available:
            seen[key] = (period, True)

    catalog: dict[str, dict[str, PlanInfo]] = {}
    for (provider, name), ((year, month), available) in seen.items():
        catalog.setdefault(provider, {})[name] = PlanInfo(
            year, month, available, (year, month) == latest_feed)
    return catalog


def plan_sort_key(item: tuple[str, PlanInfo]):
    """Current, available plans first, then by name."""
    name, info = item
    return (not info.in_latest_period, not info.available, name.casefold())
