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
from homeassistant.const import ATTR_ENTITY_ID, CONF_API_TOKEN, CONF_NAME, CONF_URL
from homeassistant.core import State, callback
from homeassistant.exceptions import HomeAssistantError
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
from homeassistant.util import dt as dt_util, slugify

from .api import DiscordApiClient, DiscordApiError, DiscordAuthenticationError
from .const import (
    ATTR_CHANNEL_ID,
    ATTR_ENTRY_ID,
    ATTR_LIVE_TEMPLATE,
    ATTR_MESSAGE_ID,
    ATTR_OFFLINE_TEMPLATE,
    ATTR_SYNC_NOW,
    ATTR_TRACKER_ID,
    ATTR_UPDATE_ON_GAME_CHANGE,
    ATTR_UPDATE_ON_TITLE_CHANGE,
    ATTR_UPDATE_TEMPLATE,
    CONF_DEFAULT_CHANNEL,
    CONF_TEST_MESSAGE,
    CONF_TRACKERS,
    DATA_ENTRIES,
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

TEST_ACTION = "test_action"
TEST_NAME = "test_name"
TEST_TITLE = "test_title"
TEST_GAME = "test_game"
TEST_VIEWERS = "test_viewers"
TEST_STARTED_AT = "test_started_at"
TEST_THUMBNAIL_URL = "test_thumbnail_url"
TEST_CHANNEL_PICTURE = "test_channel_picture"
TEST_LAST_TITLE = "last_title"
TEST_LAST_GAME = "last_game"
TEST_LAST_VIEWERS = "last_viewers"
TEST_LAST_STARTED_AT = "last_started_at"

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


def _build_test_message_schema(defaults: Mapping[str, Any] | None = None) -> vol.Schema:
    """Build the options-flow schema for sending fake test messages."""
    defaults = defaults or {}

    return vol.Schema(
        {
            vol.Optional(
                ATTR_CHANNEL_ID,
                default=defaults.get(ATTR_CHANNEL_ID, ""),
            ): TextSelector(TextSelectorConfig()),
            vol.Optional(
                ATTR_ENTITY_ID,
                default=defaults.get(ATTR_ENTITY_ID, "sensor.test_streamer"),
            ): TextSelector(TextSelectorConfig()),
            vol.Optional(
                TEST_NAME,
                default=defaults.get(TEST_NAME, "Test Streamer"),
            ): TextSelector(TextSelectorConfig()),
            vol.Required(
                TEST_ACTION,
                default=defaults.get(TEST_ACTION, "live"),
            ): SelectSelector(
                SelectSelectorConfig(
                    options=[
                        SelectOptionDict(value="live", label="Send live/start message"),
                        SelectOptionDict(value="update", label="Send update message"),
                        SelectOptionDict(value="offline", label="Send offline/stop message"),
                    ],
                    mode=SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Optional(
                TEST_TITLE,
                default=defaults.get(TEST_TITLE, "Testing Discord API Chatter"),
            ): TextSelector(TextSelectorConfig()),
            vol.Optional(
                TEST_GAME,
                default=defaults.get(TEST_GAME, "Just Chatting"),
            ): TextSelector(TextSelectorConfig()),
            vol.Optional(
                TEST_VIEWERS,
                default=defaults.get(TEST_VIEWERS, "42"),
            ): TextSelector(TextSelectorConfig()),
            vol.Optional(
                TEST_STARTED_AT,
                default=defaults.get(
                    TEST_STARTED_AT,
                    dt_util.utcnow().replace(microsecond=0).isoformat(),
                ),
            ): TextSelector(TextSelectorConfig()),
            vol.Optional(
                CONF_URL,
                default=defaults.get(CONF_URL, "https://www.twitch.tv/test_streamer"),
            ): TextSelector(TextSelectorConfig()),
            vol.Optional(
                TEST_THUMBNAIL_URL,
                default=defaults.get(TEST_THUMBNAIL_URL, ""),
            ): TextSelector(TextSelectorConfig()),
            vol.Optional(
                TEST_CHANNEL_PICTURE,
                default=defaults.get(TEST_CHANNEL_PICTURE, ""),
            ): TextSelector(TextSelectorConfig()),
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
        menu_options = ["add_tracker", "test_message"]
        if self._get_trackers():
            menu_options.extend(["edit_tracker_select", "remove_tracker"])

        return self.async_show_menu(step_id="init", menu_options=menu_options)

    async def async_step_add_tracker(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Add a new tracked stream."""
        if user_input is not None:
            return self._async_save_tracker(user_input)

        return self.async_show_form(
            step_id="add_tracker",
            data_schema=_build_tracker_schema(),
        )

    async def async_step_test_message(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Send a fake live, update, or offline message for previewing."""
        errors: dict[str, str] = {}
        defaults = self._get_test_message_defaults(user_input)

        if user_input is not None:
            try:
                normalized = self._normalize_test_message_data(user_input)
                saved_test_data = await self._async_run_test_message(normalized)
            except HomeAssistantError as err:
                _LOGGER.warning("Failed to send Discord test message: %s", err)
                errors["base"] = "test_message_failed"
            except DiscordApiError as err:
                _LOGGER.warning("Discord API rejected the test message: %s", err)
                errors["base"] = "test_message_failed"
            else:
                return self.async_create_entry(
                    title="",
                    data=self.config_entry.options | {CONF_TEST_MESSAGE: saved_test_data},
                )

        return self.async_show_form(
            step_id="test_message",
            data_schema=_build_test_message_schema(defaults),
            errors=errors,
            description_placeholders={
                "default_channel": str(
                    self.config_entry.data.get(CONF_DEFAULT_CHANNEL) or "not set"
                )
            },
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
            return await self.async_step_edit_tracker_actions()

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

    async def async_step_edit_tracker_actions(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Show actions for the selected tracker."""
        tracker = self._get_selected_tracker()
        if tracker is None:
            return await self.async_step_init()

        return self.async_show_menu(
            step_id="edit_tracker_actions",
            menu_options=["edit_tracker", "test_message", "confirm_reset_templates"],
            description_placeholders={"entity_id": str(tracker[ATTR_ENTITY_ID])},
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
            return self._async_save_tracker(
                user_input,
                tracker_id=tracker[ATTR_TRACKER_ID],
            )

        defaults = tracker | {ATTR_SYNC_NOW: True}
        return self.async_show_form(
            step_id="edit_tracker",
            data_schema=_build_tracker_schema(defaults),
        )

    async def async_step_confirm_reset_templates(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Ask the user to confirm resetting templates to defaults."""
        tracker = self._get_selected_tracker()
        if tracker is None:
            return await self.async_step_init()

        return self.async_show_menu(
            step_id="confirm_reset_templates",
            menu_options=[
                "confirm_reset_templates_yes",
                "confirm_reset_templates_no",
            ],
            description_placeholders={"entity_id": str(tracker[ATTR_ENTITY_ID])},
        )

    async def async_step_confirm_reset_templates_yes(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Reset the selected templates to the current defaults and save."""
        tracker = self._get_selected_tracker()
        if tracker is None:
            return await self.async_step_init()

        return self._async_save_tracker(
            tracker,
            tracker_id=tracker[ATTR_TRACKER_ID],
            force_defaults=True,
        )

    async def async_step_confirm_reset_templates_no(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Return to the tracker actions without resetting templates."""
        return await self.async_step_edit_tracker_actions()

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

    def _get_test_message_defaults(
        self,
        overrides: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Return remembered defaults for the test-message UI."""
        saved = dict(self.config_entry.options.get(CONF_TEST_MESSAGE, {}))
        defaults: dict[str, Any] = {
            ATTR_CHANNEL_ID: (
                saved.get(ATTR_CHANNEL_ID)
                or self.config_entry.data.get(CONF_DEFAULT_CHANNEL)
                or ""
            ),
            ATTR_ENTITY_ID: saved.get(ATTR_ENTITY_ID, "sensor.test_streamer"),
            TEST_NAME: saved.get(TEST_NAME, "Test Streamer"),
            TEST_ACTION: saved.get(TEST_ACTION, "live"),
            TEST_TITLE: saved.get(TEST_TITLE, "Testing Discord API Chatter"),
            TEST_GAME: saved.get(TEST_GAME, "Just Chatting"),
            TEST_VIEWERS: str(saved.get(TEST_VIEWERS, "42") or ""),
            TEST_STARTED_AT: saved.get(
                TEST_STARTED_AT,
                dt_util.utcnow().replace(microsecond=0).isoformat(),
            ),
            CONF_URL: saved.get(CONF_URL, "https://www.twitch.tv/test_streamer"),
            TEST_THUMBNAIL_URL: saved.get(TEST_THUMBNAIL_URL, ""),
            TEST_CHANNEL_PICTURE: saved.get(TEST_CHANNEL_PICTURE, ""),
        }

        if overrides is not None:
            for key, value in overrides.items():
                defaults[key] = "" if value is None else value

        return defaults

    def _normalize_test_message_data(
        self,
        user_input: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Normalize test-message form input for storage and sending."""
        action = str(user_input.get(TEST_ACTION, "live")).strip().lower()
        if action not in {"live", "update", "offline"}:
            raise HomeAssistantError("Invalid test action selected.")

        viewers_raw = str(user_input.get(TEST_VIEWERS, "")).strip()
        viewers: int | None = None
        if viewers_raw:
            try:
                viewers = int(viewers_raw)
            except ValueError as err:
                raise HomeAssistantError(
                    "The viewers field must be a whole number."
                ) from err

        return {
            ATTR_CHANNEL_ID: str(user_input.get(ATTR_CHANNEL_ID, "")).strip() or None,
            ATTR_ENTITY_ID: str(
                user_input.get(ATTR_ENTITY_ID, "sensor.test_streamer")
            ).strip()
            or "sensor.test_streamer",
            TEST_NAME: str(user_input.get(TEST_NAME, "Test Streamer")).strip()
            or "Test Streamer",
            TEST_ACTION: action,
            TEST_TITLE: str(user_input.get(TEST_TITLE, "")).strip() or None,
            TEST_GAME: str(user_input.get(TEST_GAME, "")).strip() or None,
            TEST_VIEWERS: viewers,
            TEST_STARTED_AT: str(user_input.get(TEST_STARTED_AT, "")).strip() or None,
            CONF_URL: str(user_input.get(CONF_URL, "")).strip() or None,
            TEST_THUMBNAIL_URL: str(
                user_input.get(TEST_THUMBNAIL_URL, "")
            ).strip()
            or None,
            TEST_CHANNEL_PICTURE: str(
                user_input.get(TEST_CHANNEL_PICTURE, "")
            ).strip()
            or None,
        }

    async def _async_run_test_message(
        self,
        test_data: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Send or edit a remembered fake Discord test message."""
        entry_data = self.hass.data.get(DOMAIN, {}).get(DATA_ENTRIES, {}).get(
            self.config_entry.entry_id
        )
        if entry_data is None:
            raise HomeAssistantError("The Discord config entry is not loaded.")

        manager = self.hass.data.get(DOMAIN, {}).get(DATA_STREAM_TRACKER)
        if manager is None:
            raise HomeAssistantError("The stream tracker manager is not available.")

        client = entry_data["client"]
        saved = dict(self.config_entry.options.get(CONF_TEST_MESSAGE, {}))
        channel_id = test_data.get(ATTR_CHANNEL_ID) or self.config_entry.data.get(
            CONF_DEFAULT_CHANNEL
        )
        if not channel_id:
            raise HomeAssistantError(
                "No test channel ID was supplied and no default channel is configured."
            )

        selected_tracker = self._get_selected_tracker()
        tracker_templates = selected_tracker or {}
        fake_tracker = {
            ATTR_ENTITY_ID: test_data[ATTR_ENTITY_ID],
            ATTR_LIVE_TEMPLATE: tracker_templates.get(
                ATTR_LIVE_TEMPLATE, DEFAULT_LIVE_TEMPLATE
            ),
            ATTR_UPDATE_TEMPLATE: tracker_templates.get(
                ATTR_UPDATE_TEMPLATE, DEFAULT_UPDATE_TEMPLATE
            ),
            ATTR_OFFLINE_TEMPLATE: tracker_templates.get(
                ATTR_OFFLINE_TEMPLATE, DEFAULT_OFFLINE_TEMPLATE
            ),
            "last_title": saved.get(TEST_LAST_TITLE),
            "last_game": saved.get(TEST_LAST_GAME),
            "last_viewers": saved.get(TEST_LAST_VIEWERS),
            "last_started_at": saved.get(TEST_LAST_STARTED_AT),
            "last_thumbnail_url": test_data.get(TEST_THUMBNAIL_URL),
            "last_channel_picture": test_data.get(TEST_CHANNEL_PICTURE),
            "url": test_data.get(CONF_URL),
        }

        state = State(
            test_data[ATTR_ENTITY_ID],
            "offline" if test_data[TEST_ACTION] == "offline" else "streaming",
            {
                "friendly_name": test_data[TEST_NAME],
                "title": test_data.get(TEST_TITLE),
                "game": test_data.get(TEST_GAME),
                "game_name": test_data.get(TEST_GAME),
                "viewers": test_data.get(TEST_VIEWERS),
                "started_at": test_data.get(TEST_STARTED_AT),
                "thumbnail_url": test_data.get(TEST_THUMBNAIL_URL),
                "entity_picture": test_data.get(TEST_THUMBNAIL_URL),
                "channel_picture": test_data.get(TEST_CHANNEL_PICTURE),
                "url": test_data.get(CONF_URL),
            },
        )

        content = manager._render_message(
            fake_tracker,
            "test_message_preview",
            state,
            str(test_data[TEST_ACTION]),
        )
        embeds = manager._build_embeds(
            test_data.get(TEST_THUMBNAIL_URL),
            test_data.get(TEST_CHANNEL_PICTURE),
        )

        message_id = saved.get(ATTR_MESSAGE_ID)
        if test_data[TEST_ACTION] == "live" or not message_id:
            response = await client.async_send_message(
                str(channel_id),
                content,
                embeds=embeds,
            )
            message_id = response.get("id", message_id)
        else:
            try:
                response = await client.async_edit_message(
                    str(channel_id),
                    str(message_id),
                    content=content,
                    embeds=embeds,
                )
            except DiscordApiError:
                response = await client.async_send_message(
                    str(channel_id),
                    content,
                    embeds=embeds,
                )
                message_id = response.get("id", message_id)

        return saved | dict(test_data) | {
            ATTR_CHANNEL_ID: str(channel_id),
            ATTR_MESSAGE_ID: message_id,
            TEST_LAST_TITLE: test_data.get(TEST_TITLE),
            TEST_LAST_GAME: test_data.get(TEST_GAME),
            TEST_LAST_VIEWERS: test_data.get(TEST_VIEWERS),
            TEST_LAST_STARTED_AT: test_data.get(TEST_STARTED_AT),
        }

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

    def _async_save_tracker(
        self,
        user_input: Mapping[str, Any],
        *,
        tracker_id: str | None = None,
        force_defaults: bool = False,
    ) -> ConfigFlowResult:
        """Save a tracker and close the options flow."""
        trackers = self._get_trackers()
        tracker = self._normalize_tracker(
            user_input,
            tracker_id=tracker_id,
            force_defaults=force_defaults,
        )
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

    def _normalize_tracker(
        self,
        user_input: Mapping[str, Any],
        *,
        tracker_id: str | None = None,
        force_defaults: bool = False,
    ) -> dict[str, Any]:
        """Normalize tracker form input for storage."""
        channel_id = str(user_input.get(ATTR_CHANNEL_ID, "")).strip() or None
        normalized_tracker_id = tracker_id or slugify(
            f"{user_input[ATTR_ENTITY_ID]}_{channel_id or 'default'}_{self.config_entry.entry_id}"
        )
        reset_templates = force_defaults

        return {
            ATTR_TRACKER_ID: normalized_tracker_id,
            ATTR_ENTRY_ID: self.config_entry.entry_id,
            ATTR_ENTITY_ID: str(user_input[ATTR_ENTITY_ID]),
            ATTR_CHANNEL_ID: channel_id,
            ATTR_LIVE_TEMPLATE: (
                DEFAULT_LIVE_TEMPLATE
                if reset_templates
                else str(user_input.get(ATTR_LIVE_TEMPLATE, DEFAULT_LIVE_TEMPLATE))
            ),
            ATTR_UPDATE_TEMPLATE: (
                DEFAULT_UPDATE_TEMPLATE
                if reset_templates
                else str(user_input.get(ATTR_UPDATE_TEMPLATE, DEFAULT_UPDATE_TEMPLATE))
            ),
            ATTR_OFFLINE_TEMPLATE: (
                DEFAULT_OFFLINE_TEMPLATE
                if reset_templates
                else str(user_input.get(ATTR_OFFLINE_TEMPLATE, DEFAULT_OFFLINE_TEMPLATE))
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

