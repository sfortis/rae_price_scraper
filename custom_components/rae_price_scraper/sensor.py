"""RAE electricity price sensor.

Reads a Greek electricity tariff from a maintained JSON endpoint and exposes it
as sensor.rae_price_per_kwh.
"""

import datetime
import logging
import re
from datetime import timedelta

import requests
import voluptuous as vol

from homeassistant.components.sensor import PLATFORM_SCHEMA, SensorEntity
from homeassistant.config_entries import SOURCE_IMPORT, ConfigEntry
from homeassistant.core import HomeAssistant
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    ACCESS_TOKEN,
    CONF_DISCOUNTED,
    CONF_PLAN,
    CONF_PROVIDER,
    CONF_URL,
    DEFAULT_URL,
    DOMAIN,
    REQUEST_TIMEOUT,
    USER_AGENT,
)

_LOGGER = logging.getLogger(__name__)

SCAN_INTERVAL = timedelta(hours=12)

# The website appends a publication-version suffix to plan names, e.g.
# "My Home Online" is published as "My Home Online 06.26". We strip a trailing
# " MM.YY" so the configured plan can be matched exactly without breaking each
# time the provider bumps that version.
_PLAN_VERSION_SUFFIX = re.compile(r"\s+\d{2}\.\d{2}$")

# Legacy YAML schema, kept only to migrate existing configs into a config entry.
PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Required(CONF_PROVIDER): cv.string,
    vol.Required(CONF_PLAN): cv.string,
    vol.Optional(CONF_URL, default=DEFAULT_URL): cv.string,
    vol.Optional(CONF_DISCOUNTED, default="Y"): cv.string,
})


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    """Import a legacy YAML platform config into a config entry (then it can be
    removed from configuration.yaml)."""
    hass.async_create_task(
        hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_IMPORT}, data=dict(config)
        )
    )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the sensor from a config entry."""
    cfg = {**entry.data, **entry.options}
    sensor = RAEPriceSensor(
        provider=cfg[CONF_PROVIDER],
        plan=cfg[CONF_PLAN],
        url=cfg.get(CONF_URL) or DEFAULT_URL,
        discounted=bool(cfg.get(CONF_DISCOUNTED, True)),
    )
    async_add_entities([sensor], update_before_add=True)


class RAEPriceSensor(SensorEntity):
    _attr_icon = "mdi:flash"

    def __init__(self, provider, plan, url, discounted):
        self._provider = provider
        self._plan = plan
        self._url = url
        self._discounted = discounted
        self._state = None
        self._initialized = False
        self._last_found_month = None

    @property
    def name(self):
        return 'rae_price_per_kwh'

    @property
    def unique_id(self):
        """Stable unique ID so the entity is manageable from the UI."""
        suffix = "y" if self._discounted else "n"
        raw = f"rae_price_{self._provider}_{self._plan}_{suffix}"
        return raw.lower().replace(" ", "_")

    @property
    def native_value(self):
        return self._state

    @property
    def native_unit_of_measurement(self):
        return "EUR/kWh"

    @property
    def extra_state_attributes(self):
        return {
            'last_found_month': self._last_found_month,
            'provider': self._provider,
            'plan': self._plan,
            'discounted_price': "Y" if self._discounted else "N",
            'last_update': datetime.datetime.now().isoformat(),
        }

    def _get_previous_month(self, current_date):
        if current_date.month == 1:
            return current_date.replace(year=current_date.year - 1, month=12, day=1)
        return current_date.replace(month=current_date.month - 1, day=1)

    def _plan_matches(self, row_plan):
        """Exact match against the configured plan, ignoring the version suffix.

        The row's plan name carries a trailing " MM.YY" version (e.g.
        "My Home Online 06.26"); we strip it and compare exactly, so
        "My Home Online" matches but "My Home Online Plus" does not. A user who
        types the full versioned name still matches via the raw comparison.
        """
        if not row_plan:
            return False
        base = _PLAN_VERSION_SUFFIX.sub("", row_plan).strip()
        return base == self._plan or row_plan == self._plan

    def _search_price_in_data(self, data, month_filter):
        for item in data:
            if (item.get("provider") == self._provider and
                    item.get("month") == month_filter and
                    self._plan_matches(item.get("plan"))):

                raw_price = item.get("price_on") if self._discounted else item.get("price_off")
                try:
                    price = float(raw_price)
                    _LOGGER.info("Found price %.5f EUR/kWh for month %s", price, month_filter)
                    return price, int(month_filter)
                except (ValueError, TypeError) as e:
                    _LOGGER.error("Error converting price value: %s. Raw value: %s",
                                  e, raw_price)

        _LOGGER.debug("No price found for month %s", month_filter)
        return None, None

    def _fetch_data(self):
        """Fetch the published JSON payload and return the list of rows."""
        try:
            resp = requests.get(
                self._url,
                headers={
                    'User-Agent': USER_AGENT,
                    'Accept': 'application/json',
                    'X-Access-Token': ACCESS_TOKEN,
                },
                timeout=REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            payload = resp.json()
        except Exception as e:
            _LOGGER.error("Error fetching RAE data from %s: %s", self._url, e)
            return None

        rows = payload.get("rows")
        if not isinstance(rows, list) or not rows:
            _LOGGER.error("RAE data payload has no rows (updated_utc=%s)",
                          payload.get("updated_utc"))
            return None

        _LOGGER.info("Fetched %d rows (published %s)",
                     len(rows), payload.get("updated_utc"))
        return rows

    def update(self):
        """Update sensor data."""
        final_price = None
        found_month = None

        try:
            data = self._fetch_data()

            if data:
                current_date = datetime.datetime.now()
                search_date = current_date
                max_attempts = 12
                attempts = 0

                while attempts < max_attempts and final_price is None:
                    month_filter = str(search_date.month)
                    final_price, found_month = self._search_price_in_data(data, month_filter)
                    if final_price is None:
                        search_date = self._get_previous_month(search_date)
                        attempts += 1
                    else:
                        break

                if final_price is not None:
                    self._state = f"{final_price:.5f}"
                    self._initialized = True
                    self._last_found_month = found_month
                    _LOGGER.info("Updated RAE price: %.5f EUR/kWh (Month: %d)",
                                 final_price, found_month)
                else:
                    _LOGGER.warning("No price found in the last %d months for "
                                    "provider=%s plan=%s", max_attempts,
                                    self._provider, self._plan)
            else:
                _LOGGER.error("Failed to fetch data from RAE JSON endpoint")

        except Exception as e:
            _LOGGER.error("Unexpected error while fetching RAE data: %s", e, exc_info=True)

        if not self._initialized and self._state is None:
            self._state = 'Unavailable'
            _LOGGER.warning("Sensor remains uninitialized, setting state to 'Unavailable'")
