"""Constants for the RAE Price Scraper integration."""

DOMAIN = "rae_price_scraper"

# Config keys
CONF_PROVIDER = "provider_filter"
CONF_PLAN = "plan_filter"
CONF_URL = "url"
CONF_DISCOUNTED = "discounted_price"
CONF_SEGMENT = "segment"

# Registry segments. Entries created before segments existed have no
# CONF_SEGMENT and are household.
SEGMENT_HOUSEHOLD = "household"
SEGMENT_BUSINESS = "business"
SEGMENTS = (SEGMENT_HOUSEHOLD, SEGMENT_BUSINESS)

# Default endpoint that serves the tariff data as JSON. Requests carry a shared
# access token in the X-Access-Token header and a browser User-Agent.
DEFAULT_URL = "https://asksakis.net/rae/data.json"
ACCESS_TOKEN = "tTo9b4-WaLQ7zJDGw69dUmGZ-GwQIwEJCoK5uWMVCRg"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
REQUEST_TIMEOUT = 30
