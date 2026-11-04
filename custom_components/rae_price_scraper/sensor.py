"""RAE electricity price sensors.

Reads a Greek electricity tariff from a maintained JSON endpoint and exposes,
per matched product, one of: a single-zone price (sensor.rae_price_single),
per-tier prices, or day/night prices, plus a dual-zone price where a flat plan
carries one, the price period, and sensor.rae_last_updated (timestamp).

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
from .feed import fetch_feed

_LOGGER = logging.getLogger(__name__)

UPDATE_INTERVAL = timedelta(hours=12)

# The website appends a publication-version suffix to plan names, e.g.
# "My Home Online" is published as "My Home Online 06.26". We strip a trailing
# " MM.YY" so the configured plan can be matched exactly across version bumps.
_PLAN_VERSION_SUFFIX = re.compile(r"\s+\d{2}\.\d{2}$")


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


def _find_members(rows, provider, plan):
    """Return (members, year, month) for the matched product, most recent month.

    A single tariff (one invoice-id) may span several feed rows: consumption
    tiers (distinct kWh bands) or day/night zones. We return ALL rows of one
    product so the caller can build tier/zone sensors. Commercially-available
    products are preferred over withdrawn ones, and when a plan name maps to
    more than one product in a month we take the first in feed order.

    Returns ([], None, None) when nothing matches. Year/month come from the
    matched rows so the period reflects the actual data, not just the searched
    month.
    """
    search_date = datetime.datetime.now()
    for _ in range(12):
        month_filter = str(search_date.month)
        month_rows = [
            r for r in rows
            if r.get("provider") == provider
            and r.get("month") == month_filter
            and _plan_matches(r.get("plan"), plan)
        ]
        if month_rows:
            available = [r for r in month_rows if r.get("available", True)]
            pool = available or month_rows
            by_invoice = {}
            for r in pool:
                by_invoice.setdefault(r.get("invoice"), []).append(r)
            members = next(iter(by_invoice.values()))
            year = members[0].get("year")
            return members, int(year) if year else None, int(month_filter)
        search_date = _previous_month(search_date)
    return [], None, None


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


def _as_price(raw):
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _row_price(row, discounted):
    """The configured (discounted or base) supply price of a single row."""
    return _as_price(row.get("price_on" if discounted else "price_off"))


class RAEDataCoordinator(DataUpdateCoordinator):
    """Fetches the JSON feed once for all sensors of an entry."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, url: str):
        super().__init__(
            hass, _LOGGER, config_entry=entry, name="RAE electricity price feed",
            update_interval=UPDATE_INTERVAL,
        )
        self._url = url

    async def _async_update_data(self):
        return await self.hass.async_add_executor_job(self._fetch)

    def _fetch(self):
        try:
            payload = fetch_feed(
                self._url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "application/json",
                    "X-Access-Token": ACCESS_TOKEN,
                },
                timeout=REQUEST_TIMEOUT,
            )
        except Exception as e:
            raise UpdateFailed(f"Error fetching RAE feed: {e}") from e

        return payload


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the sensors from a config entry."""
    cfg = entry.data
    coordinator = RAEDataCoordinator(hass, entry, cfg.get(CONF_URL) or DEFAULT_URL)
    await coordinator.async_config_entry_first_refresh()

    provider = cfg[CONF_PROVIDER]
    plan = cfg[CONF_PLAN]
    discounted = bool(cfg.get(CONF_DISCOUNTED, True))
    device_name = entry.title or f"RAE {provider} - {plan}"
    ids = (entry.entry_id, device_name)

    entities = [
        RAEPriceSensor(coordinator, provider, plan, discounted, *ids),
        RAEPricePeriodSensor(coordinator, provider, plan, discounted, *ids),
        RAELastUpdatedSensor(coordinator, provider, plan, discounted, *ids),
    ]

    # Shape the extra price sensors to the matched product's structure.
    members, _, _ = _find_members(coordinator.data.get("rows", []), provider, plan)
    shape = _extra_shape(members)
    for spec in shape or ():
        if spec[0] == "tier":
            _, tier_index, kwh_min, kwh_max = spec
            entities.append(RAETierPriceSensor(
                coordinator, provider, plan, discounted, *ids,
                tier_index=tier_index, kwh_min=kwh_min, kwh_max=kwh_max))
        elif spec[0] == "zone":
            entities.append(RAEZonePriceSensor(
                coordinator, provider, plan, discounted, *ids, zone=spec[1]))
        else:
            entities.append(RAEDualPriceSensor(
                coordinator, provider, plan, discounted, *ids))

    if shape is not None:
        _remove_stale_extras(hass, entry, {_spec_role(s) for s in shape})

    @callback
    def _reload_on_shape_change() -> None:
        # A product can change structure between registry months (flat ->
        # tiered, a third tier, ...). Reload so the extra sensors match it.
        # A refresh where the plan is missing is ignored, so a feed glitch
        # never removes sensors.
        new_members, _, _ = _find_members(
            coordinator.data.get("rows", []), provider, plan)
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

    def __init__(self, coordinator, provider, plan, discounted, entry_id, device_name):
        super().__init__(coordinator)
        self._provider = provider
        self._plan = plan
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
        return _find_members(self.coordinator.data.get("rows", []),
                             self._provider, self._plan)

    @property
    def extra_state_attributes(self):
        members, year, month = self._members()
        return {
            'last_found_month': month,
            'matched_period': _format_period(year, month),
            'provider': self._provider,
            'plan': self._plan,
            'discounted_price': "Y" if self._discounted else "N",
            'product_kind': _classify(members) if members else None,
            'available': members[0].get('available') if members else None,
        }


class RAEPriceSensor(_RAEPriceEntity):
    """Primary per-kWh price, keeping the historical entity_id.

    For a flat plan this is the single-zone price. For a tiered or day/night
    plan it is the average across the tiers/zones, so a user who does not care
    about the breakdown still gets one sensible number.
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
        prices = [_row_price(m, self._discounted) for m in members]
        # An average over only the known tiers or zones would be a wrong
        # number (for hybrid plans it is just the first tier's price), so the
        # sensor is unknown until every tier or zone has a published price.
        if not prices or any(p is None for p in prices):
            return None
        return f"{sum(prices) / len(prices):.5f}"


class RAETierPriceSensor(_RAEPriceEntity):
    """Per-kWh price for one consumption tier of a tiered plan."""

    def __init__(self, coordinator, provider, plan, discounted, entry_id,
                 device_name, *, tier_index, kwh_min, kwh_max):
        super().__init__(coordinator, provider, plan, discounted,
                         entry_id, device_name)
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
                price = _row_price(m, self._discounted)
                return None if price is None else f"{price:.5f}"
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

    def __init__(self, coordinator, provider, plan, discounted, entry_id,
                 device_name, *, zone):
        super().__init__(coordinator, provider, plan, discounted,
                         entry_id, device_name)
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
                price = _row_price(m, self._discounted)
                return None if price is None else f"{price:.5f}"
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
        price = _as_price(members[0].get(
            "price_on_dual" if self._discounted else "price_off_dual"))
        return None if price is None else f"{price:.5f}"


class RAEPricePeriodSensor(_RAEBaseEntity, SensorEntity):
    """The registry month/year the current price refers to (MM/YYYY)."""

    _attr_icon = "mdi:calendar-month"
    _attr_name = "Price period"
    _entity_slug = "rae_price_period"
    _role = "period"

    @property
    def native_value(self):
        _, year, month = _find_members(self.coordinator.data.get("rows", []),
                                       self._provider, self._plan)
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
