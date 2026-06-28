"""Config flow for the RAE Price Scraper integration."""

from __future__ import annotations

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback

from .const import (
    CONF_DISCOUNTED,
    CONF_PLAN,
    CONF_PROVIDER,
    CONF_URL,
    DEFAULT_URL,
    DOMAIN,
)


def _unique_id(provider: str, plan: str, discounted: bool) -> str:
    """Stable id so the same provider/plan/discount is not added twice."""
    suffix = "y" if discounted else "n"
    return f"rae_price_{provider}_{plan}_{suffix}".lower().replace(" ", "_")


def _data_schema(defaults: dict) -> vol.Schema:
    return vol.Schema({
        vol.Required(CONF_PROVIDER, default=defaults.get(CONF_PROVIDER, "")): str,
        vol.Required(CONF_PLAN, default=defaults.get(CONF_PLAN, "")): str,
        vol.Optional(CONF_DISCOUNTED, default=defaults.get(CONF_DISCOUNTED, True)): bool,
        vol.Optional(CONF_URL, default=defaults.get(CONF_URL, DEFAULT_URL)): str,
    })


class RaeConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for RAE Price Scraper."""

    VERSION = 1

    async def async_step_user(self, user_input=None) -> ConfigFlowResult:
        """Handle UI-initiated setup."""
        if user_input is not None:
            await self.async_set_unique_id(
                _unique_id(user_input[CONF_PROVIDER], user_input[CONF_PLAN],
                           user_input[CONF_DISCOUNTED])
            )
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=f"{user_input[CONF_PROVIDER]} - {user_input[CONF_PLAN]}",
                data=user_input,
            )
        return self.async_show_form(step_id="user", data_schema=_data_schema({}))

    async def async_step_import(self, import_data: dict) -> ConfigFlowResult:
        """Migrate a legacy YAML platform configuration to a config entry."""
        discounted = str(import_data.get(CONF_DISCOUNTED, "Y")).upper() == "Y"
        data = {
            CONF_PROVIDER: import_data[CONF_PROVIDER],
            CONF_PLAN: import_data[CONF_PLAN],
            CONF_DISCOUNTED: discounted,
            CONF_URL: import_data.get(CONF_URL) or DEFAULT_URL,
        }
        await self.async_set_unique_id(
            _unique_id(data[CONF_PROVIDER], data[CONF_PLAN], discounted)
        )
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=f"{data[CONF_PROVIDER]} - {data[CONF_PLAN]}", data=data
        )

    async def async_step_reconfigure(self, user_input=None) -> ConfigFlowResult:
        """Allow reconfiguring an existing entry (any field may change)."""
        entry = self._get_reconfigure_entry()
        if user_input is not None:
            return self.async_update_reload_and_abort(entry, data=user_input)
        return self.async_show_form(
            step_id="reconfigure", data_schema=_data_schema(dict(entry.data))
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return RaeOptionsFlow()


class RaeOptionsFlow(OptionsFlow):
    """Options flow: edit plan/discount/url without changing the provider."""

    async def async_step_init(self, user_input=None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        current = {**self.config_entry.data, **self.config_entry.options}
        schema = vol.Schema({
            vol.Required(CONF_PLAN, default=current.get(CONF_PLAN, "")): str,
            vol.Optional(CONF_DISCOUNTED,
                         default=current.get(CONF_DISCOUNTED, True)): bool,
            vol.Optional(CONF_URL,
                         default=current.get(CONF_URL, DEFAULT_URL)): str,
        })
        return self.async_show_form(step_id="init", data_schema=schema)
