"""Provider and plan catalog built from the tariff feed rows.

This module has no Home Assistant imports, so it can be tested on its own.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# The registry appends a publication version to plan names, e.g. "My Home
# Online" is published as "My Home Online 06.26". The trailing " MM.YY" is
# stripped so a configured plan keeps matching across version bumps.
_PLAN_VERSION_SUFFIX = re.compile(r"\s+\d{2}\.\d{2}$")


def plan_base(row_plan: str) -> str:
    """The plan name without the trailing publication version."""
    return _PLAN_VERSION_SUFFIX.sub("", row_plan or "").strip()


@dataclass(frozen=True)
class PlanInfo:
    """What the feed says about one plan of a provider."""

    year: int
    month: int
    available: bool
    in_latest_period: bool

    def label(self, name: str) -> str:
        """The dropdown label: the name plus a note when it is not current."""
        if not self.in_latest_period:
            return f"{name} (last listed {self.month:02d}/{self.year})"
        if not self.available:
            return f"{name} (withdrawn)"
        return name


def _period(row) -> tuple[int, int] | None:
    try:
        return int(row.get("year")), int(row.get("month"))
    except (TypeError, ValueError):
        return None


def build_catalog(rows: list[dict]) -> dict[str, dict[str, PlanInfo]]:
    """Map each provider to its plans, using each plan's latest period.

    A plan is available when any of its rows in that period is commercially
    available. A plan that is missing from the newest period of the feed is
    still listed, because an existing customer can stay on an old tariff.
    """
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
