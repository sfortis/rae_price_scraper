"""Config flow for the RAE Price Scraper integration.

Setup and reconfigure share three steps: the segment (household or business,
plus the feed URL as an advanced option), the provider, and the plan with the
discount choice. All lists come from the tariff feed, so the user never has
to type a registry name or know about the " MM.YY" plan version, and each
plan shows its current price.
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
from .catalog import (
    PlanInfo,
    build_catalog,
    feed_rows,
    find_members,
    plan_sort_key,
    price_note,
)
from .const import (
    CONF_DISCOUNTED,
    CONF_PLAN,
    CONF_PROVIDER,
    CONF_SEGMENT,
    CONF_URL,
    DEFAULT_URL,
    DOMAIN,
    SEGMENT_BUSINESS,
    SEGMENT_HOUSEHOLD,
    SEGMENTS,
)

_LOGGER = logging.getLogger(__name__)


def entry_unique_id(provider: str, plan: str, discounted: bool,
                    segment: str = SEGMENT_HOUSEHOLD) -> str:
    """Stable id so the same selection is not added twice.

    Household ids keep the format of earlier versions; business ids carry the
    segment, because the same plan name can exist in both registries.
    """
    suffix = "y" if discounted else "n"
    scope = "business_" if segment == SEGMENT_BUSINESS else ""
    return f"rae_price_{scope}{provider}_{plan}_{suffix}".lower().replace(" ", "_")


def default_title(data: dict[str, Any]) -> str:
    """The title an entry gets when the user has not renamed it."""
    title = f"{data.get(CONF_PROVIDER)} - {data.get(CONF_PLAN)}"
    if data.get(CONF_SEGMENT) == SEGMENT_BUSINESS:
        title += " (business)"
    return title


def _dropdown(options: list[SelectOptionDict], translation_key=None) -> SelectSelector:
    config = SelectSelectorConfig(
        options=options, mode=SelectSelectorMode.DROPDOWN, sort=False)
    if translation_key:
        # The selector schema accepts only a string here, never None.
        config["translation_key"] = translation_key
    return SelectSelector(config)


class RaeConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for RAE Price Scraper.

    All settings live in entry.data and change only through Reconfigure.
    Minor version 2 removed the options flow; async_migrate_entry moves any
    options of older entries into data.
    """

    VERSION = 1
    MINOR_VERSION = 2

    def __init__(self) -> None:
        self._rows: list[dict] = []
        self._rows_url: str | None = None
        self._catalog: dict[str, dict[str, PlanInfo]] = {}
        self._segment: str = SEGMENT_HOUSEHOLD
        self._provider: str | None = None
        self._url: str = DEFAULT_URL

    async def _async_load(self, url: str) -> bool:
        """Fetch the feed at url once per flow; False on failure."""
        if self._rows and self._rows_url == url:
            return True
        try:
            payload = await self.hass.async_add_executor_job(fetch_rae_feed, url)
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Could not load the RAE feed from %s: %s", url, err)
            return False
        self._rows = feed_rows(payload)
        self._rows_url = url
        return bool(self._rows)

    def _current(self) -> dict[str, Any]:
        """The saved settings when reconfiguring, else nothing."""
        if self.source == SOURCE_RECONFIGURE:
            return dict(self._get_reconfigure_entry().data)
        return {}

    # Step 1: segment and feed URL ------------------------------------------

    async def async_step_user(self, user_input=None) -> ConfigFlowResult:
        """Handle UI-initiated setup."""
        return await self._async_step_segment("user", user_input)

    async def async_step_reconfigure(self, user_input=None) -> ConfigFlowResult:
        """Change any setting of an existing entry."""
        return await self._async_step_segment("reconfigure", user_input)

    async def _async_step_segment(self, step_id, user_input) -> ConfigFlowResult:
        current = self._current()
        errors: dict[str, str] = {}
        if user_input is not None:
            url = (user_input.get(CONF_URL) or "").strip() or DEFAULT_URL
            segment = user_input[CONF_SEGMENT]
            if not await self._async_load(url):
                errors[CONF_URL] = "cannot_connect"
            else:
                catalog = build_catalog(self._rows, segment)
                if not catalog:
                    errors[CONF_SEGMENT] = "empty_segment"
                else:
                    self._segment, self._catalog, self._url = segment, catalog, url
                    return await self.async_step_provider()
            url_default = url
        else:
            url_default = current.get(CONF_URL) or DEFAULT_URL
            # A broken custom URL must not lock the user out of reconfigure.
            if not (await self._async_load(url_default)
                    or await self._async_load(DEFAULT_URL)):
                return self.async_abort(reason="cannot_connect")

        schema = vol.Schema({
            vol.Required(CONF_SEGMENT, default=current.get(
                CONF_SEGMENT, SEGMENT_HOUSEHOLD)):
                _dropdown([SelectOptionDict(value=s, label=s) for s in SEGMENTS],
                          translation_key=CONF_SEGMENT),
            vol.Optional(CONF_URL, default=url_default): str,
        })
        return self.async_show_form(
            step_id=step_id, data_schema=schema, errors=errors)

    # Step 2: provider ------------------------------------------------------

    async def async_step_provider(self, user_input=None) -> ConfigFlowResult:
        """Pick the provider within the chosen segment."""
        if user_input is not None:
            self._provider = user_input[CONF_PROVIDER]
            return await self.async_step_plan()
        current = self._current()
        providers = sorted(self._catalog, key=str.casefold)
        saved = current.get(CONF_PROVIDER)
        schema = vol.Schema({
            vol.Required(CONF_PROVIDER, default=(
                saved if saved in self._catalog else vol.UNDEFINED)):
                _dropdown([SelectOptionDict(value=p, label=p) for p in providers]),
        })
        return self.async_show_form(step_id="provider", data_schema=schema)

    # Step 3: plan and discount ---------------------------------------------

    async def async_step_plan(self, user_input=None) -> ConfigFlowResult:
        """Pick the plan of the chosen provider and the discount."""
        current = self._current()
        plans = self._catalog.get(self._provider, {})

        if user_input is not None:
            data = {
                CONF_SEGMENT: self._segment,
                CONF_PROVIDER: self._provider,
                CONF_PLAN: user_input[CONF_PLAN],
                CONF_DISCOUNTED: user_input[CONF_DISCOUNTED],
                CONF_URL: self._url,
            }
            new_uid = entry_unique_id(data[CONF_PROVIDER], data[CONF_PLAN],
                                      data[CONF_DISCOUNTED], data[CONF_SEGMENT])
            if self.source != SOURCE_RECONFIGURE:
                await self.async_set_unique_id(new_uid)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=default_title(data), data=data)

            entry = self._get_reconfigure_entry()
            if new_uid != entry.unique_id:
                # Refuse a selection that another entry already has.
                await self.async_set_unique_id(new_uid)
                self._abort_if_unique_id_configured()
            # Keep a title the user chose; follow the plan only for the default.
            title = (default_title(data)
                     if entry.title == default_title(entry.data) else entry.title)
            return self.async_update_reload_and_abort(
                entry, unique_id=new_uid, title=title, data=data)

        options = []
        for name, info in sorted(plans.items(), key=plan_sort_key):
            members, _, _ = find_members(
                self._rows, self._provider, name, self._segment)
            options.append(SelectOptionDict(
                value=name, label=info.label(name, price_note(members))))
        same_scope = (current.get(CONF_PROVIDER) == self._provider
                      and (current.get(CONF_SEGMENT) or SEGMENT_HOUSEHOLD)
                      == self._segment)
        saved_plan = current.get(CONF_PLAN) if same_scope else None
        if saved_plan and saved_plan not in plans:
            # Keep the saved plan selectable even if the feed no longer lists it.
            options.append(SelectOptionDict(
                value=saved_plan, label=f"{saved_plan} [not in the feed]"))
        schema = vol.Schema({
            vol.Required(CONF_PLAN, default=saved_plan or vol.UNDEFINED):
                _dropdown(options),
            vol.Optional(CONF_DISCOUNTED,
                         default=current.get(CONF_DISCOUNTED, True)): bool,
        })
        return self.async_show_form(
            step_id="plan", data_schema=schema,
            description_placeholders={"provider": self._provider})
