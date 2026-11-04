"""RAE electricity price sensors.

Reads a Greek electricity tariff from a maintained JSON endpoint and exposes,
per matched product:

- sensor.rae_price_single: the price of the chosen plan, standard or
  discounted as configured. Its entity_id and unique_id never change, so it
  can stay in the Energy dashboard across plan changes.
- sensor.rae_price_standard and sensor.rae_price_discounted: both prices of
  the same plan, whatever the discount setting.
- per-tier or day/night prices, or a dual-zone price where a flat plan
  carries one (these follow the discount setting);
- the price period and sensor.rae_last_updated (timestamp).

A single DataUpdateCoordinator fetches the feed once and all sensors read it.
"""

import datetime
import logging
import re
from datetime import timedelta

from homeassistant.components.sensor import (
    ENTITY_ID_FORMAT,
    SensorDeviceClass,
    SensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
    UpdateFailed,
)

from .api import fetch_rae_feed
from .catalog import as_price, average_price, feed_rows, find_members, row_price
from .const import (
    CONF_DISCOUNTED,
    CONF_PLAN,
    CONF_PROVIDER,
    CONF_SEGMENT,
    CONF_URL,
    DEFAULT_URL,
    DOMAIN,
    SEGMENT_HOUSEHOLD,
)

_LOGGER = logging.getLogger(__name__)

UPDATE_INTERVAL = timedelta(hours=12)


def _classify(members):
    """flat | tiered | zoned for a product's rows."""
    if any(m.get("zone") for m in members):
        return "zoned"
    if any((m.get("tier_count") or 1) > 1 for m in members):
        return "tiered"
    return "flat"


def _format_period(year, month):
    """Render a (year, month) pair as MM/YYYY, or None if incomplete."""
    if not year or not month:
        return None
    return f"{month:02d}/{year}"


def _kwh_label(kwh_min, kwh_max):
    """Human label for a consumption band, e.g. '0-100 kWh' or '101+ kWh'."""
    lo = kwh_min if kwh_min is not None else 0
    if kwh_max in (None, -1):
        return f"{lo}+ kWh"
    return f"{lo}-{kwh_max} kWh"


def _fmt(price):
    return None if price is None else f"{price:.5f}"


class RAEDataCoordinator(DataUpdateCoordinator):
    """Fetches the JSON feed once for all sensors of an entry.

    It also holds the entry's selection (provider, plan, segment), so every
    sensor matches the product the same way through members().
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry):
        super().__init__(
            hass, _LOGGER, config_entry=entry, name="RAE electricity price feed",
            update_interval=UPDATE_INTERVAL,
        )
        cfg = entry.data
        self._url = cfg.get(CONF_URL) or DEFAULT_URL
        self.provider = cfg[CONF_PROVIDER]
        self.plan = cfg[CONF_PLAN]
        self.segment = cfg.get(CONF_SEGMENT) or SEGMENT_HOUSEHOLD

    async def _async_update_data(self):
        return await self.hass.async_add_executor_job(self._fetch)

    def _fetch(self):
        try:
            payload = fetch_rae_feed(self._url)
        except Exception as e:
            raise UpdateFailed(f"Error fetching RAE feed: {e}") from e
        # One list with household and business rows, each with its segment.
        return {"updated_utc": payload.get("updated_utc"),
                "rows": feed_rows(payload)}

    def members(self):
        """(members, year, month) of the configured product."""
        return find_members(self.data.get("rows", []),
                            self.provider, self.plan, self.segment)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the sensors from a config entry."""
    coordinator = RAEDataCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    discounted = bool(entry.data.get(CONF_DISCOUNTED, True))
    device_name = entry.title or f"RAE {coordinator.provider} - {coordinator.plan}"
    args = (coordinator, discounted, entry.entry_id, device_name)

    _remove_legacy_entities(hass, entry)

    entities = [
        RAEPriceSensor(*args),
        RAEStandardPriceSensor(*args),
        RAEDiscountedPriceSensor(*args),
        RAEPricePeriodSensor(*args),
        RAELastUpdatedSensor(*args),
    ]

    # Shape the extra price sensors to the matched product's structure.
    members, _, _ = coordinator.members()
    shape = _extra_shape(members)
    for spec in shape or ():
        if spec[0] == "tier":
            _, tier_index, kwh_min, kwh_max = spec
            entities.append(RAETierPriceSensor(
                *args, tier_index=tier_index, kwh_min=kwh_min, kwh_max=kwh_max))
        elif spec[0] == "zone":
            entities.append(RAEZonePriceSensor(*args, zone=spec[1]))
        else:
            entities.append(RAEDualPriceSensor(*args))

    if shape is not None:
        _remove_stale_extras(hass, entry, {_spec_role(s) for s in shape})

    @callback
    def _reload_on_shape_change() -> None:
        # A product can change structure between registry months (flat ->
        # tiered, a third tier, ...). Reload so the extra sensors match it.
        # A refresh where the plan is missing is ignored, so a feed glitch
        # never removes sensors.
        new_members, _, _ = coordinator.members()
        new_shape = _extra_shape(new_members)
        if new_shape is not None and new_shape != shape:
            _LOGGER.info("Product structure of '%s' changed; reloading",
                         entry.title)
            hass.config_entries.async_schedule_reload(entry.entry_id)

    entry.async_on_unload(coordinator.async_add_listener(_reload_on_shape_change))
    async_add_entities(entities)


def _extra_shape(members):
    """The extra price sensors a product needs, or None if it was not found.

    Each item is ("tier", index, kwh_min, kwh_max), ("zone", "day"|"night")
    or ("dual",). Comparing two shapes tells whether the sensors must change,
    including a renamed tier band.
    """
    if not members:
        return None
    kind = _classify(members)
    if kind == "tiered":
        tiers = {}
        for m in members:
            index = m.get("tier_index") or 1
            tiers.setdefault(index, ("tier", index, m.get("kwh_min"), m.get("kwh_max")))
        return tuple(tiers[i] for i in sorted(tiers))
    if kind == "zoned":
        return (("zone", "day"), ("zone", "night"))
    if members[0].get("price_off_dual") or members[0].get("price_on_dual"):
        # Flat plan that also carries a static time-of-use price.
        return (("dual",),)
    return ()


def _spec_role(spec):
    """The entity role (unique_id suffix) of an extra sensor spec."""
    if spec[0] == "tier":
        return f"tier{spec[1]}"
    if spec[0] == "zone":
        return spec[1]
    return "dual"


_EXTRA_ROLE = re.compile(r"(tier\d+|day|night|dual)")


def _remove_stale_extras(hass, entry, keep_roles):
    """Delete this entry's tier/zone/dual entities the product no longer has."""
    registry = er.async_get(hass)
    prefix = f"{entry.entry_id}_"
    for reg_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
        if not reg_entry.unique_id.startswith(prefix):
            continue
        role = reg_entry.unique_id[len(prefix):]
        if _EXTRA_ROLE.fullmatch(role) and role not in keep_roles:
            _LOGGER.info("Removing %s: the product no longer has it",
                         reg_entry.entity_id)
            registry.async_remove(reg_entry.entity_id)


def _remove_legacy_entities(hass, entry):
    """Delete this entry's entities whose unique_id predates version 1.2.

    Since 1.2 every entity unique_id is <entry_id>_<role>. Entities with an
    older, plan-based unique_id were left behind by plan changes in earlier
    versions; nothing can ever update them again, so they only show up as
    unavailable. The 1.2 migration renames the entities of the current plan
    before this runs, so only those leftovers are removed.
    """
    registry = er.async_get(hass)
    prefix = f"{entry.entry_id}_"
    for reg_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
        if not reg_entry.unique_id.startswith(prefix):
            _LOGGER.info("Removing %s: left over from an earlier version",
                         reg_entry.entity_id)
            registry.async_remove(reg_entry.entity_id)


class _RAEBaseEntity(CoordinatorEntity):
    _attr_has_entity_name = True
    # Stable, rae_-prefixed object id. Pinning entity_id here bypasses Home
    # Assistant's device-name slug generation (which produced ugly, unstable
    # ids like sensor.dee_my_home_online_...). Subclasses with a dynamic id
    # (tiers/zones) set self.entity_id in their own __init__ instead.
    _entity_slug = None
    # Role of the entity within its entry; the unique_id is <entry_id>_<role>,
    # so changing the plan or the discount keeps the same entity and history.
    _role = None

    def __init__(self, coordinator, discounted, entry_id, device_name):
        super().__init__(coordinator)
        self._discounted = discounted
        self._entry_id = entry_id
        self._device_name = device_name
        if self._entity_slug:
            self.entity_id = ENTITY_ID_FORMAT.format(self._entity_slug)

    @property
    def unique_id(self):
        return f"{self._entry_id}_{self._role}"

    @property
    def device_info(self):
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry_id)},
            name=self._device_name,
            manufacturer="RAE / RAAEY",
            model="Electricity tariff",
        )


class _RAEPriceEntity(_RAEBaseEntity, SensorEntity):
    _attr_icon = "mdi:flash"
    _attr_native_unit_of_measurement = "EUR/kWh"

    def _members(self):
        return self.coordinator.members()

    @property
    def extra_state_attributes(self):
        members, year, month = self._members()
        return {
            'last_found_month': month,
            'matched_period': _format_period(year, month),
            'provider': self.coordinator.provider,
            'plan': self.coordinator.plan,
            'segment': self.coordinator.segment,
            'discounted_price': "Y" if self._discounted else "N",
            'product_kind': _classify(members) if members else None,
            'available': members[0].get('available') if members else None,
        }


class RAEPriceSensor(_RAEPriceEntity):
    """Price of the chosen plan, keeping the historical entity_id.

    For a flat plan this is the single-zone price. For a tiered or day/night
    plan it is the average across the tiers/zones, so a user who does not care
    about the breakdown still gets one sensible number. It is unknown until
    every tier or zone has a published price.
    """

    _entity_slug = "rae_price_single"
    _role = "price"

    @property
    def name(self):
        members, _, _ = self._members()
        return ("Average price" if _classify(members) in ("tiered", "zoned")
                else "Single-zone price")

    @property
    def native_value(self):
        members, _, _ = self._members()
        return _fmt(average_price(members, self._discounted))


class _RAEFixedPriceSensor(RAEPriceSensor):
    """The plan's price with a fixed discount choice, ignoring the setting."""

    _fixed_discount: bool

    def __init__(self, coordinator, discounted, entry_id, device_name):
        super().__init__(coordinator, self._fixed_discount, entry_id, device_name)

    @property
    def name(self):
        return self._attr_name


class RAEStandardPriceSensor(_RAEFixedPriceSensor):
    """The standard (not discounted) price of the chosen plan, always."""

    _fixed_discount = False
    _attr_name = "Standard price"
    _entity_slug = "rae_price_standard"
    _role = "price_standard"


class RAEDiscountedPriceSensor(_RAEFixedPriceSensor):
    """The discounted price of the chosen plan, always."""

    _fixed_discount = True
    _attr_name = "Discounted price"
    _entity_slug = "rae_price_discounted"
    _role = "price_discounted"


class RAETierPriceSensor(_RAEPriceEntity):
    """Per-kWh price for one consumption tier of a tiered plan."""

    def __init__(self, coordinator, discounted, entry_id, device_name, *,
                 tier_index, kwh_min, kwh_max):
        super().__init__(coordinator, discounted, entry_id, device_name)
        self._tier_index = tier_index
        self._kwh_min = kwh_min
        self._kwh_max = kwh_max
        self._role = f"tier{tier_index}"
        self.entity_id = ENTITY_ID_FORMAT.format(f"rae_price_tier{tier_index}")

    @property
    def name(self):
        return (f"Price tier {self._tier_index} "
                f"({_kwh_label(self._kwh_min, self._kwh_max)})")

    @property
    def native_value(self):
        members, _, _ = self._members()
        for m in members:
            if (m.get("tier_index") or 1) == self._tier_index:
                return _fmt(row_price(m, self._discounted))
        return None

    @property
    def extra_state_attributes(self):
        attrs = super().extra_state_attributes
        attrs.update(tier=self._tier_index,
                     kwh_min=self._kwh_min, kwh_max=self._kwh_max)
        return attrs


class RAEZonePriceSensor(_RAEPriceEntity):
    """Per-kWh price for the day or night zone of a time-of-use plan."""

    _ICONS = {"day": "mdi:weather-sunny", "night": "mdi:weather-night"}

    def __init__(self, coordinator, discounted, entry_id, device_name, *, zone):
        super().__init__(coordinator, discounted, entry_id, device_name)
        self._zone = zone
        self._role = zone
        self.entity_id = ENTITY_ID_FORMAT.format(f"rae_price_{zone}")

    @property
    def icon(self):
        return self._ICONS.get(self._zone, "mdi:flash")

    @property
    def name(self):
        return "Day price" if self._zone == "day" else "Night price"

    @property
    def native_value(self):
        members, _, _ = self._members()
        for m in members:
            if m.get("zone") == self._zone:
                return _fmt(row_price(m, self._discounted))
        return None


class RAEDualPriceSensor(_RAEPriceEntity):
    """Dual-zone (static time-of-use) price for a flat plan that carries one."""

    _attr_name = "Dual-zone price"
    _entity_slug = "rae_price_dual"
    _role = "dual"

    @property
    def native_value(self):
        members, _, _ = self._members()
        if not members:
            return None
        return _fmt(as_price(members[0].get(
            "price_on_dual" if self._discounted else "price_off_dual")))


class RAEPricePeriodSensor(_RAEBaseEntity, SensorEntity):
    """The registry month/year the current price refers to (MM/YYYY)."""

    _attr_icon = "mdi:calendar-month"
    _attr_name = "Price period"
    _entity_slug = "rae_price_period"
    _role = "period"

    @property
    def native_value(self):
        _, year, month = self.coordinator.members()
        return _format_period(year, month)


class RAELastUpdatedSensor(_RAEBaseEntity, SensorEntity):
    _attr_icon = "mdi:clock-outline"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_name = "Last updated"
    _entity_slug = "rae_last_updated"
    _role = "last_updated"

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
