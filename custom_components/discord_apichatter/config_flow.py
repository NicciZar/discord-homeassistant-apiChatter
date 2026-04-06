"""Config flow for Discord API Chatter."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import ATTR_ENTITY_ID, CONF_API_TOKEN, CONF_NAME
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    EntitySelector,
    EntitySelectorConfig,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
)
from homeassistant.util import slugify

from .api import DiscordApiClient, DiscordApiError, DiscordAuthenticationError
from .const import (
    ATTR_CHANNEL_ID,
    ATTR_ENTRY_ID,
    ATTR_LIVE_TEMPLATE,
    ATTR_OFFLINE_TEMPLATE,
    ATTR_SYNC_NOW,
    ATTR_TRACKER_ID,
    ATTR_UPDATE_ON_GAME_CHANGE,
    ATTR_UPDATE_ON_TITLE_CHANGE,
    ATTR_UPDATE_TEMPLATE,
    CONF_DEFAULT_CHANNEL,
    CONF_TRACKERS,
    DATA_STREAM_TRACKER,
    DEFAULT_NAME,
    DOMAIN,
)
from .stream_tracker import (
    DEFAULT_LIVE_TEMPLATE,
    DEFAULT_OFFLINE_TEMPLATE,
    DEFAULT_UPDATE_TEMPLATE,
)

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_API_TOKEN): str,
        vol.Optional(CONF_DEFAULT_CHANNEL): str,
    }
)


def _build_tracker_schema(defaults: Mapping[str, Any] | None = None) -> vol.Schema:
    """Build the options-flow schema for a tracked stream."""
    defaults = defaults or {}

    return vol.Schema(
        {
            vol.Required(
                ATTR_ENTITY_ID,
                default=defaults.get(ATTR_ENTITY_ID, "sensor.channel123"),
            ): EntitySelector(EntitySelectorConfig(domain="sensor")),
            vol.Optional(
                ATTR_CHANNEL_ID,
                default=defaults.get(ATTR_CHANNEL_ID, ""),
            ): TextSelector(TextSelectorConfig()),
            vol.Optional(
                ATTR_UPDATE_ON_TITLE_CHANGE,
                default=defaults.get(ATTR_UPDATE_ON_TITLE_CHANGE, True),
            ): BooleanSelector(),
            vol.Optional(
                ATTR_UPDATE_ON_GAME_CHANGE,
                default=defaults.get(ATTR_UPDATE_ON_GAME_CHANGE, True),
            ): BooleanSelector(),
            vol.Optional(
                ATTR_SYNC_NOW,
                default=defaults.get(ATTR_SYNC_NOW, True),
            ): BooleanSelector(),
            vol.Optional(
                ATTR_LIVE_TEMPLATE,
                default=defaults.get(ATTR_LIVE_TEMPLATE, DEFAULT_LIVE_TEMPLATE),
            ): TextSelector(TextSelectorConfig(multiline=True)),
            vol.Optional(
                ATTR_UPDATE_TEMPLATE,
                default=defaults.get(ATTR_UPDATE_TEMPLATE, DEFAULT_UPDATE_TEMPLATE),
            ): TextSelector(TextSelectorConfig(multiline=True)),
            vol.Optional(
                ATTR_OFFLINE_TEMPLATE,
                default=defaults.get(ATTR_OFFLINE_TEMPLATE, DEFAULT_OFFLINE_TEMPLATE),
            ): TextSelector(TextSelectorConfig(multiline=True)),
        }
    )


class DiscordApiChatterConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Discord API Chatter."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> "DiscordApiChatterOptionsFlow":
        """Create the options flow."""
        return DiscordApiChatterOptionsFlow(config_entry)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial configuration step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            session = async_get_clientsession(self.hass)
            client = DiscordApiClient(session, user_input[CONF_API_TOKEN])

            try:
                bot_user = await client.async_get_current_user()
            except DiscordAuthenticationError:
                errors["base"] = "invalid_auth"
            except DiscordApiError:
                errors["base"] = "cannot_connect"
            except Exception:  # pragma: no cover - defensive
                _LOGGER.exception("Unexpected exception during Discord validation")
                errors["base"] = "unknown"
            else:
                unique_id = str(bot_user["id"])
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()

                title = (
                    bot_user.get("global_name")
                    or bot_user.get("username")
                    or user_input.get(CONF_NAME)
                    or DEFAULT_NAME
                )

                return self.async_create_entry(
                    title=title,
                    data=user_input | {CONF_NAME: title},
                )

        return self.async_show_form(
            step_id="user",
            data_schema=CONFIG_SCHEMA,
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Handle a reauth flow request."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm reauthentication with a fresh bot token."""
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()

        if user_input is not None:
            session = async_get_clientsession(self.hass)
            client = DiscordApiClient(session, user_input[CONF_API_TOKEN])

            try:
                await client.async_get_current_user()
            except DiscordAuthenticationError:
                errors["base"] = "invalid_auth"
            except DiscordApiError:
                errors["base"] = "cannot_connect"
            except Exception:  # pragma: no cover - defensive
                _LOGGER.exception("Unexpected exception during Discord reauth")
                errors["base"] = "unknown"
            else:
                return self.async_update_reload_and_abort(
                    entry,
                    data=entry.data | user_input,
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_API_TOKEN): str}),
            errors=errors,
        )


class DiscordApiChatterOptionsFlow(OptionsFlow):
    """Manage tracked stream settings from the Home Assistant UI."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Initialize the options flow."""
        self.config_entry = config_entry
        self._selected_tracker_id: str | None = None

    async def async_step_init(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Show the tracker management menu."""
        menu_options = ["add_tracker"]
        if self._get_trackers():
            menu_options.extend(["edit_tracker_select", "remove_tracker"])

        return self.async_show_menu(step_id="init", menu_options=menu_options)

    async def async_step_add_tracker(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Add a new tracked stream."""
        if user_input is not None:
            trackers = self._get_trackers()
            tracker = self._normalize_tracker(user_input)
            trackers = [
                existing
                for existing in trackers
                if existing.get(ATTR_TRACKER_ID) != tracker[ATTR_TRACKER_ID]
            ]
            trackers.append(tracker)
            return self.async_create_entry(
                title="",
                data=self.config_entry.options | {CONF_TRACKERS: trackers},
            )

        return self.async_show_form(
            step_id="add_tracker",
            data_schema=_build_tracker_schema(),
        )

    async def async_step_edit_tracker_select(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Choose which tracker to edit."""
        trackers = self._get_trackers()
        if not trackers:
            return await self.async_step_init()

        if user_input is not None:
            self._selected_tracker_id = user_input[ATTR_TRACKER_ID]
            return await self.async_step_edit_tracker()

        return self.async_show_form(
            step_id="edit_tracker_select",
            data_schema=vol.Schema(
                {
                    vol.Required(ATTR_TRACKER_ID): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                SelectOptionDict(
                                    value=tracker[ATTR_TRACKER_ID],
                                    label=self._tracker_label(tracker),
                                )
                                for tracker in trackers
                            ],
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    )
                }
            ),
        )

    async def async_step_edit_tracker(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Edit an existing tracked stream."""
        tracker = self._get_selected_tracker()
        if tracker is None:
            return await self.async_step_init()

        if user_input is not None:
            trackers = self._get_trackers()
            updated = self._normalize_tracker(
                user_input,
                tracker_id=tracker[ATTR_TRACKER_ID],
            )
            trackers = [
                existing
                for existing in trackers
                if existing.get(ATTR_TRACKER_ID) != tracker[ATTR_TRACKER_ID]
            ]
            trackers.append(updated)
            return self.async_create_entry(
                title="",
                data=self.config_entry.options | {CONF_TRACKERS: trackers},
            )

        defaults = tracker | {ATTR_SYNC_NOW: True}
        return self.async_show_form(
            step_id="edit_tracker",
            data_schema=_build_tracker_schema(defaults),
        )

    async def async_step_remove_tracker(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Remove a tracked stream from the UI."""
        trackers = self._get_trackers()
        if not trackers:
            return await self.async_step_init()

        if user_input is not None:
            tracker_id = user_input[ATTR_TRACKER_ID]
            trackers = [
                tracker
                for tracker in trackers
                if tracker.get(ATTR_TRACKER_ID) != tracker_id
            ]
            return self.async_create_entry(
                title="",
                data=self.config_entry.options | {CONF_TRACKERS: trackers},
            )

        return self.async_show_form(
            step_id="remove_tracker",
            data_schema=vol.Schema(
                {
                    vol.Required(ATTR_TRACKER_ID): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                SelectOptionDict(
                                    value=tracker[ATTR_TRACKER_ID],
                                    label=self._tracker_label(tracker),
                                )
                                for tracker in trackers
                            ],
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    )
                }
            ),
        )

    def _get_trackers(self) -> list[dict[str, Any]]:
        """Return the currently known trackers for this config entry."""
        manager = self.hass.data.get(DOMAIN, {}).get(DATA_STREAM_TRACKER)
        if manager is not None:
            return manager.get_trackers_for_entry(self.config_entry.entry_id)

        return [
            dict(tracker)
            for tracker in self.config_entry.options.get(CONF_TRACKERS, [])
        ]

    def _get_selected_tracker(self) -> dict[str, Any] | None:
        """Return the tracker selected for editing."""
        if self._selected_tracker_id is None:
            return None

        for tracker in self._get_trackers():
            if tracker.get(ATTR_TRACKER_ID) == self._selected_tracker_id:
                return tracker
        return None

    def _normalize_tracker(
        self,
        user_input: Mapping[str, Any],
        *,
        tracker_id: str | None = None,
    ) -> dict[str, Any]:
        """Normalize tracker form input for storage."""
        channel_id = str(user_input.get(ATTR_CHANNEL_ID, "")).strip() or None
        normalized_tracker_id = tracker_id or slugify(
            f"{user_input[ATTR_ENTITY_ID]}_{channel_id or 'default'}_{self.config_entry.entry_id}"
        )

        return {
            ATTR_TRACKER_ID: normalized_tracker_id,
            ATTR_ENTRY_ID: self.config_entry.entry_id,
            ATTR_ENTITY_ID: str(user_input[ATTR_ENTITY_ID]),
            ATTR_CHANNEL_ID: channel_id,
            ATTR_LIVE_TEMPLATE: str(
                user_input.get(ATTR_LIVE_TEMPLATE, DEFAULT_LIVE_TEMPLATE)
            ),
            ATTR_UPDATE_TEMPLATE: str(
                user_input.get(ATTR_UPDATE_TEMPLATE, DEFAULT_UPDATE_TEMPLATE)
            ),
            ATTR_OFFLINE_TEMPLATE: str(
                user_input.get(ATTR_OFFLINE_TEMPLATE, DEFAULT_OFFLINE_TEMPLATE)
            ),
            ATTR_UPDATE_ON_TITLE_CHANGE: bool(
                user_input.get(ATTR_UPDATE_ON_TITLE_CHANGE, True)
            ),
            ATTR_UPDATE_ON_GAME_CHANGE: bool(
                user_input.get(ATTR_UPDATE_ON_GAME_CHANGE, True)
            ),
        }

    def _tracker_label(self, tracker: Mapping[str, Any]) -> str:
        """Create a readable label for a stored tracker."""
        channel = tracker.get(ATTR_CHANNEL_ID) or "default channel"
        return f"{tracker[ATTR_ENTITY_ID]} → {channel}"

