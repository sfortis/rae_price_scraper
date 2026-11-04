"""Config entry migrations for the RAE Price Scraper integration."""

from __future__ import annotations

import logging
import re

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er

from .config_flow import default_title, entry_unique_id
from .const import CONF_DISCOUNTED, CONF_PLAN, CONF_PROVIDER, DOMAIN

_LOGGER = logging.getLogger(__name__)


def _legacy_role(old_uid: str, provider: str, plan: str, discounted: bool):
    """The entity role encoded in a pre-1.2 unique_id, or None.

    Before 1.2 each entity unique_id embedded the provider, the plan and the
    discount flag, for example rae_price_dei_my_home_online_tier2_y. Only ids
    that match the entry's current settings are recognised.
    """
    def slug(raw: str) -> str:
        return raw.lower().replace(" ", "_")

    suffix = "y" if discounted else "n"
    base = slug(f"{provider}_{plan}")
    fixed = {
        slug(f"rae_price_{provider}_{plan}_{suffix}"): "price",
        slug(f"rae_price_dual_{provider}_{plan}_{suffix}"): "dual",
        slug(f"rae_price_period_{provider}_{plan}_{suffix}"): "period",
        slug(f"rae_last_updated_{provider}_{plan}_{suffix}"): "last_updated",
        slug(f"rae_price_{provider}_{plan}_day_{suffix}"): "day",
        slug(f"rae_price_{provider}_{plan}_night_{suffix}"): "night",
    }
    if old_uid in fixed:
        return fixed[old_uid]
    tier = re.fullmatch(
        rf"rae_price_{re.escape(base)}_tier(\d+)_{suffix}", old_uid)
    return f"tier{tier.group(1)}" if tier else None


async def async_migrate_to_1_2(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Move options into data and give entities plan-independent unique_ids.

    1. The options flow was removed. Settings that an older version stored in
       entry.options override entry.data, so they are merged into data and
       the options are cleared. The entry unique_id and the default title
       follow the merged settings, unless another entry already uses that
       unique_id or the user renamed the entry.
    2. Entity unique_ids become <entry_id>_<role>, so a later change of plan
       or discount keeps the same entities, entity_ids and history.
    """
    data = {**entry.data, **entry.options}
    provider = data[CONF_PROVIDER]
    plan = data[CONF_PLAN]
    discounted = bool(data.get(CONF_DISCOUNTED, True))

    new_uid = entry_unique_id(provider, plan, discounted)
    taken = any(
        other.unique_id == new_uid and other.entry_id != entry.entry_id
        for other in hass.config_entries.async_entries(DOMAIN)
    )
    if taken:
        _LOGGER.warning(
            "Entry '%s' keeps unique_id %s because %s is used by another entry",
            entry.title, entry.unique_id, new_uid)
        new_uid = entry.unique_id
    title = (default_title(data)
             if entry.title == default_title(entry.data) else entry.title)

    @callback
    def _rename(reg_entry: er.RegistryEntry):
        role = _legacy_role(reg_entry.unique_id, provider, plan, discounted)
        if role is None:
            # An entity of an earlier plan; it is already orphaned and would
            # collide with the new id of the same role, so leave it alone.
            return None
        return {"new_unique_id": f"{entry.entry_id}_{role}"}

    await er.async_migrate_entries(hass, entry.entry_id, _rename)

    hass.config_entries.async_update_entry(
        entry, data=data, options={}, unique_id=new_uid, title=title,
        minor_version=2)
    _LOGGER.debug("Migrated entry '%s' to version 1.2", title)
