"""RAE electricity price sensors.

Reads a Greek electricity tariff from a maintained JSON endpoint and exposes:
- sensor.rae_price_per_kwh : single-zone (monozoniki) price (EUR/kWh)
- a dual-zone (time-of-use) price sensor, only for plans that have one
- sensor.rae_last_updated  : when the feed was last refreshed (timestamp)

A single DataUpdateCoordinator fetches the feed once and all sensors read it.
"""

import datetime
import logging
import re
from datetime import timedelta

import requests
import voluptuous as vol

from homeassistant.components.sensor import (
    PLATFORM_SCHEMA,
    SensorDeviceClass,
    SensorEntity,
)
from homeassistant.config_entries import SOURCE_IMPORT, ConfigEntry
from homeassistant.core import HomeAssistant
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
    UpdateFailed,
)

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

UPDATE_INTERVAL = timedelta(hours=12)

# The website appends a publication-version suffix to plan names, e.g.
# "My Home Online" is published as "My Home Online 06.26". We strip a trailing
# " MM.YY" so the configured plan can be matched exactly across version bumps.
_PLAN_VERSION_SUFFIX = re.compile(r"\s+\d{2}\.\d{2}$")

# Legacy YAML schema, kept only to migrate existing configs into a config entry.
PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Required(CONF_PROVIDER): cv.string,
    vol.Required(CONF_PLAN): cv.string,
    vol.Optional(CONF_URL, default=DEFAULT_URL): cv.string,
    vol.Optional(CONF_DISCOUNTED, default="Y"): cv.string,
})


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    """Import a legacy YAML platform config into a config entry."""
    hass.async_create_task(
        hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_IMPORT}, data=dict(config)
        )
    )


def _previous_month(d):
    if d.month == 1:
        return d.replace(year=d.year - 1, month=12, day=1)
    return d.replace(month=d.month - 1, day=1)


def _plan_matches(row_plan, plan):
    """Exact match against the configured plan, ignoring the version suffix."""
    if not row_plan:
        return False
    base = _PLAN_VERSION_SUFFIX.sub("", row_plan).strip()
    return base == plan or row_plan == plan


def _find_row(rows, provider, plan):
    """Return (row, month) for the most recent matching month, else (None, None)."""
    search_date = datetime.datetime.now()
    for _ in range(12):
        month_filter = str(search_date.month)
        for item in rows:
            if (item.get("provider") == provider
                    and item.get("month") == month_filter
                    and _plan_matches(item.get("plan"), plan)):
                return item, int(month_filter)
        search_date = _previous_month(search_date)
    return None, None


def _as_price(raw):
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


class RAEDataCoordinator(DataUpdateCoordinator):
    """Fetches the JSON feed once for all sensors of an entry."""

    def __init__(self, hass: HomeAssistant, url: str):
        super().__init__(
            hass, _LOGGER, name="RAE electricity price feed",
            update_interval=UPDATE_INTERVAL,
        )
        self._url = url

    async def _async_update_data(self):
        return await self.hass.async_add_executor_job(self._fetch)

    def _fetch(self):
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
            raise UpdateFailed(f"Error fetching RAE feed: {e}") from e

        rows = payload.get("rows")
        if not isinstance(rows, list) or not rows:
            raise UpdateFailed("RAE feed contains no rows")
        return payload


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the sensors from a config entry."""
    cfg = {**entry.data, **entry.options}
    coordinator = RAEDataCoordinator(hass, cfg.get(CONF_URL) or DEFAULT_URL)
    await coordinator.async_config_entry_first_refresh()

    provider = cfg[CONF_PROVIDER]
    plan = cfg[CONF_PLAN]
    discounted = bool(cfg.get(CONF_DISCOUNTED, True))
    device_name = entry.title or f"RAE {provider} - {plan}"
    ids = (entry.entry_id, device_name)

    entities = [
        RAEPriceSensor(coordinator, provider, plan, discounted, *ids),
        RAELastUpdatedSensor(coordinator, provider, plan, discounted, *ids),
    ]

    # Add the dual-zone (time-of-use) price sensor only if this plan has one.
    row, _ = _find_row(coordinator.data.get("rows", []), provider, plan)
    if row and (row.get("price_off_dual") or row.get("price_on_dual")):
        entities.append(RAEDualPriceSensor(coordinator, provider, plan, discounted, *ids))

    async_add_entities(entities)


class _RAEBaseEntity(CoordinatorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, provider, plan, discounted, entry_id, device_name):
        super().__init__(coordinator)
        self._provider = provider
        self._plan = plan
        self._discounted = discounted
        self._entry_id = entry_id
        self._device_name = device_name

    def _suffix(self):
        return "y" if self._discounted else "n"

    @property
    def device_info(self):
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry_id)},
            name=self._device_name,
            manufacturer="RAE / RAAEY",
            model="Electricity tariff",
        )


class _RAEPriceBase(_RAEBaseEntity, SensorEntity):
    _attr_icon = "mdi:flash"
    _attr_native_unit_of_measurement = "EUR/kWh"

    # Overridden per zone.
    _field_off = "price_off"
    _field_on = "price_on"

    @property
    def native_value(self):
        row, _ = _find_row(self.coordinator.data.get("rows", []),
                           self._provider, self._plan)
        if not row:
            return None
        price = _as_price(row.get(self._field_on if self._discounted
                                  else self._field_off))
        return None if price is None else f"{price:.5f}"

    @property
    def extra_state_attributes(self):
        _, month = _find_row(self.coordinator.data.get("rows", []),
                             self._provider, self._plan)
        return {
            'last_found_month': month,
            'provider': self._provider,
            'plan': self._plan,
            'discounted_price': "Y" if self._discounted else "N",
        }


class RAEPriceSensor(_RAEPriceBase):
    """Single-zone (monozoniki) price. Keeps the historical entity_id."""

    _attr_name = "Single-zone price"
    _field_off = "price_off"
    _field_on = "price_on"

    @property
    def unique_id(self):
        raw = f"rae_price_{self._provider}_{self._plan}_{self._suffix()}"
        return raw.lower().replace(" ", "_")


class RAEDualPriceSensor(_RAEPriceBase):
    """Dual-zone (time-of-use) price."""

    _attr_name = "Dual-zone price"
    _field_off = "price_off_dual"
    _field_on = "price_on_dual"

    @property
    def unique_id(self):
        raw = f"rae_price_dual_{self._provider}_{self._plan}_{self._suffix()}"
        return raw.lower().replace(" ", "_")


class RAELastUpdatedSensor(_RAEBaseEntity, SensorEntity):
    _attr_icon = "mdi:clock-outline"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_name = "Last updated"

    @property
    def unique_id(self):
        raw = f"rae_last_updated_{self._provider}_{self._plan}_{self._suffix()}"
        return raw.lower().replace(" ", "_")

    @property
    def native_value(self):
        ts = self.coordinator.data.get("updated_utc")
        if not ts:
            return None
        try:
            return datetime.datetime.fromisoformat(ts)
        except ValueError:
            _LOGGER.error("Could not parse updated_utc: %s", ts)
            return None
