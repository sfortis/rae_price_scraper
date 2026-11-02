"""Retrieve the tariff feed with bounded response handling."""

import json

import requests

MAX_FEED_BYTES = 10 * 1024 * 1024
READ_CHUNK_BYTES = 64 * 1024


def fetch_feed(url: str, headers: dict[str, str], timeout: int) -> dict:
    """Fetch a tariff feed without following redirects or buffering large bodies.

    Args:
        url: The configured feed URL.
        headers: The headers required by the feed.
        timeout: The connection and read timeout in seconds.

    Returns:
        The parsed feed with a nonempty list of rows.

    Raises:
        ValueError: If the response redirects, is too large, or has invalid rows.
        TypeError: If the JSON response is not an object.
        requests.RequestException: If the request fails.
    """
    with requests.get(
        url, headers=headers, timeout=timeout, stream=True, allow_redirects=False
    ) as response:
        # A feed server must not redirect Home Assistant to an unexpected host.
        if 300 <= response.status_code < 400:
            raise ValueError("RAE feed redirects are not supported")
        response.raise_for_status()

        body = bytearray()
        for chunk in response.iter_content(chunk_size=READ_CHUNK_BYTES):
            if len(body) + len(chunk) > MAX_FEED_BYTES:
                raise ValueError("RAE feed exceeds the maximum response size")
            body.extend(chunk)

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
