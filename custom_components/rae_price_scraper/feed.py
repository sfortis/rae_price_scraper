"""Retrieve the tariff feed with bounded response handling."""

import json
import time

import requests

MAX_FEED_BYTES = 10 * 1024 * 1024
READ_CHUNK_BYTES = 64 * 1024
# The requests timeout applies to each socket read, so a server that sends a
# byte just before every read timeout could hold the caller for ever. This
# caps the whole download (it can overrun by at most one read timeout); the
# live feed (~300 KB) takes well under a second.
MAX_FEED_SECONDS = 60


def _chunks(response: requests.Response):
    """Yield the decoded body as it arrives.

    urllib3 2.x read1() does at most one socket read and returns what has
    arrived, so the caller regains control after each read and can enforce a
    deadline; a plain read(amt) would block until amt bytes came in. Older
    urllib3 versions fall back to iter_content.
    """
    raw = response.raw
    if not hasattr(raw, "read1"):
        yield from response.iter_content(chunk_size=READ_CHUNK_BYTES)
        return
    while True:
        chunk = raw.read1(READ_CHUNK_BYTES, decode_content=True)
        if not chunk:
            return
        yield chunk


def fetch_feed(
    url: str,
    headers: dict[str, str],
    timeout: int,
    max_seconds: float = MAX_FEED_SECONDS,
) -> dict:
    """Fetch a tariff feed without following redirects or buffering large bodies.

    Args:
        url: The configured feed URL.
        headers: The headers required by the feed.
        timeout: The connection and per-read timeout in seconds.
        max_seconds: The limit for the whole download in seconds.

    Returns:
        The parsed feed with a nonempty list of rows.

    Raises:
        ValueError: If the response redirects, is too large, has invalid rows,
            or takes longer than max_seconds.
        TypeError: If the JSON response is not an object.
        requests.RequestException: If the request fails.
    """
    started = time.monotonic()
    with requests.get(
        url, headers=headers, timeout=timeout, stream=True, allow_redirects=False
    ) as response:
        # A feed server must not redirect Home Assistant to an unexpected host.
        if 300 <= response.status_code < 400:
            raise ValueError("RAE feed redirects are not supported")
        response.raise_for_status()

        deadline = started + max_seconds
        body = bytearray()
        for chunk in _chunks(response):
            if len(body) + len(chunk) > MAX_FEED_BYTES:
                raise ValueError("RAE feed exceeds the maximum response size")
            body.extend(chunk)
            if time.monotonic() > deadline:
                raise ValueError(
                    f"RAE feed download exceeded {max_seconds:g} seconds")

    payload = json.loads(body)
    if not isinstance(payload, dict):
        raise TypeError("RAE feed must be a JSON object")
    rows = payload.get("rows")
    if (
        not isinstance(rows, list)
        or not rows
        or not all(isinstance(row, dict) for row in rows)
    ):
        raise ValueError("RAE feed must contain a nonempty list of objects")
    return payload
