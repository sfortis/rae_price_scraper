"""Config flow for the RAE Price Scraper integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult

from .const import (
    CONF_DISCOUNTED,
    CONF_PLAN,
    CONF_PROVIDER,
    CONF_URL,
    DEFAULT_URL,
    DOMAIN,
)


def entry_unique_id(provider: str, plan: str, discounted: bool) -> str:
    """Stable id so the same provider/plan/discount is not added twice."""
    suffix = "y" if discounted else "n"
    return f"rae_price_{provider}_{plan}_{suffix}".lower().replace(" ", "_")


def default_title(data: dict[str, Any]) -> str:
    """The title an entry gets when the user has not renamed it."""
    return f"{data.get(CONF_PROVIDER)} - {data.get(CONF_PLAN)}"


def _data_schema(defaults: dict) -> vol.Schema:
    return vol.Schema({
        vol.Required(CONF_PROVIDER, default=defaults.get(CONF_PROVIDER, "")): str,
        vol.Required(CONF_PLAN, default=defaults.get(CONF_PLAN, "")): str,
        vol.Optional(CONF_DISCOUNTED, default=defaults.get(CONF_DISCOUNTED, True)): bool,
        vol.Optional(CONF_URL, default=defaults.get(CONF_URL, DEFAULT_URL)): str,
    })


class RaeConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for RAE Price Scraper.

    All settings live in entry.data and change only through Reconfigure.
    Minor version 2 removed the options flow; async_migrate_entry moves any
    options of older entries into data.
    """

    VERSION = 1
    MINOR_VERSION = 2

    async def async_step_user(self, user_input=None) -> ConfigFlowResult:
        """Handle UI-initiated setup."""
        if user_input is not None:
            await self.async_set_unique_id(
                entry_unique_id(user_input[CONF_PROVIDER], user_input[CONF_PLAN],
                                user_input[CONF_DISCOUNTED])
            )
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=default_title(user_input),
                data=user_input,
            )
        return self.async_show_form(step_id="user", data_schema=_data_schema({}))

    async def async_step_reconfigure(self, user_input=None) -> ConfigFlowResult:
        """Change any setting of an existing entry."""
        entry = self._get_reconfigure_entry()
        if user_input is not None:
            new_uid = entry_unique_id(user_input[CONF_PROVIDER],
                                      user_input[CONF_PLAN],
                                      user_input[CONF_DISCOUNTED])
            if new_uid != entry.unique_id:
                # Refuse a provider/plan/discount that another entry already has.
                await self.async_set_unique_id(new_uid)
                self._abort_if_unique_id_configured()
            # Keep a title the user chose; follow the plan only for the default.
            title = (default_title(user_input)
                     if entry.title == default_title(entry.data) else entry.title)
            return self.async_update_reload_and_abort(
                entry, unique_id=new_uid, title=title, data=user_input)
        return self.async_show_form(
            step_id="reconfigure", data_schema=_data_schema(dict(entry.data))
        )
