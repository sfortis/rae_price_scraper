"""Blocking client for the RAE tariff feed (run it in an executor)."""

from __future__ import annotations

from .const import ACCESS_TOKEN, REQUEST_TIMEOUT, USER_AGENT
from .feed import fetch_feed


def fetch_rae_feed(url: str) -> dict:
    """Fetch and validate the feed at url with the headers it requires."""
    return fetch_feed(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "X-Access-Token": ACCESS_TOKEN,
        },
        timeout=REQUEST_TIMEOUT,
    )
