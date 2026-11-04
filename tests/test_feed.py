"""Test the feed reader against local HTTP responses."""

import gzip
import importlib.util
import json
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import requests

MODULE_PATH = (
    Path(__file__).resolve().parents[1] / "custom_components/rae_price_scraper/feed.py"
)
spec = importlib.util.spec_from_file_location("rae_price_feed", MODULE_PATH)
feed = importlib.util.module_from_spec(spec)
spec.loader.exec_module(feed)


class FeedHandler(BaseHTTPRequestHandler):
    """Serve controlled responses and record redirect targets."""

    redirects_followed = 0

    def do_GET(self):
        """Return a test feed or a redirect."""
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header(
                "Location", f"http://127.0.0.1:{self.server.server_port}/target"
            )
            self.end_headers()
            return
        if self.path == "/target":
            type(self).redirects_followed += 1
        if self.path == "/missing":
            self.send_error(404)
            return
        if self.path == "/slow":
            # Send one byte at a time, each well inside the read timeout.
            self.send_response(200)
            self.send_header("Content-Length", "1000")
            self.end_headers()
            try:
                for _ in range(1000):
                    self.wfile.write(b" ")
                    self.wfile.flush()
                    time.sleep(0.1)
            except (BrokenPipeError, ConnectionResetError, ValueError, OSError):
                pass
            return
        self.send_response(200)
        if self.path == "/large":
            body = b" " * (feed.MAX_FEED_BYTES + 1)
        elif self.path == "/compressed":
            body = gzip.compress(b" " * (feed.MAX_FEED_BYTES + 1))
            self.send_header("Content-Encoding", "gzip")
        elif self.path == "/invalid":
            body = b'{"rows": [1]}'
        else:
            body = json.dumps(
                {"rows": [{"provider": "test", "price_on": "0.14"}]}
            ).encode()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, *args):
        """Keep test output concise."""


class TestFeed(unittest.TestCase):
    """Check valid and hostile feed responses."""

    @classmethod
    def setUpClass(cls):
        """Start an isolated server for the test suite."""
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), FeedHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        """Stop the test server."""
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def test_valid_feed(self):
        """A valid feed remains available to the integration."""
        result = feed.fetch_feed(self.base_url + "/valid", {}, 3)
        self.assertEqual(result["rows"][0]["price_on"], "0.14")

    def test_redirect_is_not_followed(self):
        """The server cannot redirect the client to another endpoint."""
        FeedHandler.redirects_followed = 0
        with self.assertRaisesRegex(ValueError, "redirect"):
            feed.fetch_feed(self.base_url + "/redirect", {}, 3)
        self.assertEqual(FeedHandler.redirects_followed, 0)

    def test_large_response_is_rejected(self):
        """A feed larger than the limit is rejected."""
        with self.assertRaisesRegex(ValueError, "maximum response size"):
            feed.fetch_feed(self.base_url + "/large", {}, 3)

    def test_compressed_expansion_is_rejected(self):
        """The limit applies to decoded content, including gzip responses."""
        with self.assertRaisesRegex(ValueError, "maximum response size"):
            feed.fetch_feed(self.base_url + "/compressed", {}, 3)

    def test_invalid_payload_is_rejected(self):
        """A rows list with non-object entries is rejected."""
        with self.assertRaisesRegex(ValueError, "nonempty list of objects"):
            feed.fetch_feed(self.base_url + "/invalid", {}, 3)

    def test_slow_download_is_cut_off(self):
        """A server that drips bytes cannot hold the caller past the limit."""
        started = time.monotonic()
        with self.assertRaisesRegex(ValueError, "exceeded"):
            feed.fetch_feed(self.base_url + "/slow", {}, 3, max_seconds=1)
        self.assertLess(time.monotonic() - started, 5)

    def test_http_failure_is_reported(self):
        """An HTTP error is not mistaken for valid feed data."""
        with self.assertRaises(requests.HTTPError):
            feed.fetch_feed(self.base_url + "/missing", {}, 3)


if __name__ == "__main__":
    unittest.main()
