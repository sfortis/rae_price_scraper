"""Config flow for the RAE Price Scraper integration.

Setup and reconfigure share two steps. The first step picks the provider (and,
as an advanced option, the feed URL); the second picks the plan and whether to
use the discounted price. Both lists come from the tariff feed, so the user
never has to type a registry name or know about the " MM.YY" plan version.
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    SOURCE_RECONFIGURE,
    ConfigFlow,
    ConfigFlowResult,
)
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .api import fetch_rae_feed
from .catalog import PlanInfo, build_catalog, plan_sort_key
from .const import (
    CONF_DISCOUNTED,
    CONF_PLAN,
    CONF_PROVIDER,
    CONF_URL,
    DEFAULT_URL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


def entry_unique_id(provider: str, plan: str, discounted: bool) -> str:
    """Stable id so the same provider/plan/discount is not added twice."""
    suffix = "y" if discounted else "n"
    return f"rae_price_{provider}_{plan}_{suffix}".lower().replace(" ", "_")


def default_title(data: dict[str, Any]) -> str:
    """The title an entry gets when the user has not renamed it."""
    return f"{data.get(CONF_PROVIDER)} - {data.get(CONF_PLAN)}"


def _dropdown(options: list[SelectOptionDict]) -> SelectSelector:
    return SelectSelector(SelectSelectorConfig(
        options=options, mode=SelectSelectorMode.DROPDOWN, sort=False))


class RaeConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for RAE Price Scraper.

    All settings live in entry.data and change only through Reconfigure.
    Minor version 2 removed the options flow; async_migrate_entry moves any
    options of older entries into data.
    """

    VERSION = 1
    MINOR_VERSION = 2

    def __init__(self) -> None:
        self._catalog: dict[str, dict[str, PlanInfo]] = {}
        self._catalog_url: str | None = None
        self._provider: str | None = None
        self._url: str = DEFAULT_URL

    async def _async_load_catalog(self, url: str) -> bool:
        """Fetch the feed at url and build the catalog; False on failure."""
        if self._catalog and self._catalog_url == url:
            return True
        try:
            payload = await self.hass.async_add_executor_job(fetch_rae_feed, url)
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Could not load the RAE feed from %s: %s", url, err)
            return False
        self._catalog = build_catalog(payload["rows"])
        self._catalog_url = url
        return bool(self._catalog)

    def _current(self) -> dict[str, Any]:
        """The saved settings when reconfiguring, else nothing."""
        if self.source == SOURCE_RECONFIGURE:
            return dict(self._get_reconfigure_entry().data)
        return {}

    # Step 1: provider and feed URL -----------------------------------------

    async def async_step_user(self, user_input=None) -> ConfigFlowResult:
        """Handle UI-initiated setup."""
        return await self._async_step_provider("user", user_input)

    async def async_step_reconfigure(self, user_input=None) -> ConfigFlowResult:
        """Change any setting of an existing entry."""
        return await self._async_step_provider("reconfigure", user_input)

    async def _async_step_provider(self, step_id, user_input) -> ConfigFlowResult:
        current = self._current()
        errors: dict[str, str] = {}

        if user_input is not None:
            url = (user_input.get(CONF_URL) or "").strip() or DEFAULT_URL
            if not await self._async_load_catalog(url):
                errors[CONF_URL] = "cannot_connect"
            elif user_input[CONF_PROVIDER] not in self._catalog:
                errors[CONF_PROVIDER] = "unknown_provider"
            else:
                self._provider = user_input[CONF_PROVIDER]
                self._url = url
                return await self.async_step_plan()
            url_default = url
        else:
            url_default = current.get(CONF_URL) or DEFAULT_URL
            # A broken custom URL must not lock the user out of reconfigure.
            if not (await self._async_load_catalog(url_default)
                    or await self._async_load_catalog(DEFAULT_URL)):
                return self.async_abort(reason="cannot_connect")

        providers = sorted(self._catalog, key=str.casefold)
        schema = vol.Schema({
            vol.Required(CONF_PROVIDER, default=(
                current.get(CONF_PROVIDER)
                if current.get(CONF_PROVIDER) in self._catalog
                else vol.UNDEFINED)):
                _dropdown([SelectOptionDict(value=p, label=p) for p in providers]),
            vol.Optional(CONF_URL, default=url_default): str,
        })
        return self.async_show_form(
            step_id=step_id, data_schema=schema, errors=errors)

    # Step 2: plan and discount ---------------------------------------------

    async def async_step_plan(self, user_input=None) -> ConfigFlowResult:
        """Pick the plan of the chosen provider and the discount."""
        current = self._current()
        plans = self._catalog.get(self._provider, {})

        if user_input is not None:
            data = {
                CONF_PROVIDER: self._provider,
                CONF_PLAN: user_input[CONF_PLAN],
                CONF_DISCOUNTED: user_input[CONF_DISCOUNTED],
                CONF_URL: self._url,
            }
            new_uid = entry_unique_id(
                data[CONF_PROVIDER], data[CONF_PLAN], data[CONF_DISCOUNTED])
            if self.source != SOURCE_RECONFIGURE:
                await self.async_set_unique_id(new_uid)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=default_title(data), data=data)

            entry = self._get_reconfigure_entry()
            if new_uid != entry.unique_id:
                # Refuse a provider/plan/discount that another entry already has.
                await self.async_set_unique_id(new_uid)
                self._abort_if_unique_id_configured()
            # Keep a title the user chose; follow the plan only for the default.
            title = (default_title(data)
                     if entry.title == default_title(entry.data) else entry.title)
            return self.async_update_reload_and_abort(
                entry, unique_id=new_uid, title=title, data=data)

        options = [SelectOptionDict(value=name, label=info.label(name))
                   for name, info in sorted(plans.items(), key=plan_sort_key)]
        saved_plan = (current.get(CONF_PLAN)
                      if current.get(CONF_PROVIDER) == self._provider else None)
        if saved_plan and saved_plan not in plans:
            # Keep the saved plan selectable even if the feed no longer lists it.
            options.append(SelectOptionDict(
                value=saved_plan, label=f"{saved_plan} (not in the feed)"))
        schema = vol.Schema({
            vol.Required(CONF_PLAN, default=saved_plan or vol.UNDEFINED):
                _dropdown(options),
            vol.Optional(CONF_DISCOUNTED,
                         default=current.get(CONF_DISCOUNTED, True)): bool,
        })
        return self.async_show_form(
            step_id="plan", data_schema=schema,
            description_placeholders={"provider": self._provider})
