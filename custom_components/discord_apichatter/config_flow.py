"""Config flow for Discord API Chatter."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
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
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
)
from homeassistant.util import dt as dt_util, slugify

from .api import DiscordApiClient, DiscordApiError, DiscordAuthenticationError
from .const import (
    ATTR_CHANNEL_NAME,
    ATTR_CHANNEL_ID,
    ATTR_ENTRY_ID,
    ATTR_LIVE_TEMPLATE,
    ATTR_MESSAGE_ID,
    ATTR_OFFLINE_TEMPLATE,
    ATTR_SEND_LIVE_IMAGE,
    ATTR_SEND_OFFLINE_IMAGE,
    ATTR_SEND_UPDATE_IMAGE,
    ATTR_SYNC_NOW,
    ATTR_TRACKER_ID,
    ATTR_UPDATE_ON_GAME_CHANGE,
    ATTR_UPDATE_ON_TITLE_CHANGE,
    ATTR_UPDATE_TEMPLATE,
    CONF_CHANNELS,
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
TEST_SEND_LIVE_IMAGE = "test_send_live_image"
TEST_SEND_UPDATE_IMAGE = "test_send_update_image"
TEST_SEND_OFFLINE_IMAGE = "test_send_offline_image"
TEST_THUMBNAIL_URL = "test_thumbnail_url"
TEST_CHANNEL_PICTURE = "test_channel_picture"
TEST_LAST_TITLE = "last_title"
TEST_LAST_GAME = "last_game"
TEST_LAST_VIEWERS = "last_viewers"
TEST_LAST_STARTED_AT = "last_started_at"
TRACKER_DIAGNOSTICS_TEXT = "tracker_diagnostics_text"

CONFIG_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_API_TOKEN): str,
        vol.Optional(CONF_DEFAULT_CHANNEL): str,
    }
)


def _build_tracker_schema(
    defaults: Mapping[str, Any] | None = None,
    channel_options: list[dict[str, str]] | None = None,
) -> vol.Schema:
    """Build the options-flow schema for a tracked stream."""
    defaults = defaults or {}
    channel_options = channel_options or []

    return vol.Schema(
        {
            vol.Required(
                ATTR_ENTITY_ID,
                default=defaults.get(ATTR_ENTITY_ID, "sensor.channel123"),
            ): EntitySelector(EntitySelectorConfig(domain="sensor")),
            vol.Optional(
                ATTR_CHANNEL_ID,
                default=defaults.get(ATTR_CHANNEL_ID, ""),
            ): SelectSelector(
                SelectSelectorConfig(
                    options=channel_options,
                    mode=SelectSelectorMode.DROPDOWN,
                )
            ),
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
            vol.Optional(
                ATTR_SEND_LIVE_IMAGE,
                default=defaults.get(ATTR_SEND_LIVE_IMAGE, True),
            ): BooleanSelector(),
            vol.Optional(
                ATTR_SEND_UPDATE_IMAGE,
                default=defaults.get(ATTR_SEND_UPDATE_IMAGE, True),
            ): BooleanSelector(),
            vol.Optional(
                ATTR_SEND_OFFLINE_IMAGE,
                default=defaults.get(ATTR_SEND_OFFLINE_IMAGE, True),
            ): BooleanSelector(),
        }
    )


def _build_test_message_schema(
    defaults: Mapping[str, Any] | None = None,
    channel_options: list[dict[str, str]] | None = None,
) -> vol.Schema:
    """Build the options-flow schema for sending fake test messages."""
    defaults = defaults or {}
    channel_options = channel_options or []

    return vol.Schema(
        {
            vol.Optional(
                ATTR_CHANNEL_ID,
                default=defaults.get(ATTR_CHANNEL_ID, ""),
            ): SelectSelector(
                SelectSelectorConfig(
                    options=channel_options,
                    mode=SelectSelectorMode.DROPDOWN,
                )
            ),
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
                        dict(value="live", label="Send live/start message"),
                        dict(value="update", label="Send update message"),
                        dict(value="offline", label="Send offline/stop message"),
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
                TEST_SEND_LIVE_IMAGE,
                default=defaults.get(TEST_SEND_LIVE_IMAGE, True),
            ): BooleanSelector(),
            vol.Optional(
                TEST_SEND_UPDATE_IMAGE,
                default=defaults.get(TEST_SEND_UPDATE_IMAGE, True),
            ): BooleanSelector(),
            vol.Optional(
                TEST_SEND_OFFLINE_IMAGE,
                default=defaults.get(TEST_SEND_OFFLINE_IMAGE, True),
            ): BooleanSelector(),
            vol.Optional(
                CONF_URL,
                default=defaults.get(CONF_URL, "https://www.twitch.tv/test_streamer"),
            ): TextSelector(TextSelectorConfig()),
        }
    )


def _build_test_message_image_schema(
    defaults: Mapping[str, Any] | None = None,
) -> vol.Schema:
    """Build image-specific tester inputs shown only when needed."""
    defaults = defaults or {}

    return vol.Schema(
        {
            vol.Optional(
                TEST_THUMBNAIL_URL,
                default=defaults.get(TEST_THUMBNAIL_URL) or "",
            ): TextSelector(TextSelectorConfig()),
            vol.Optional(
                TEST_CHANNEL_PICTURE,
                default=defaults.get(TEST_CHANNEL_PICTURE) or "",
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
                    options={},
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
        self._config_entry = config_entry
        self._selected_tracker_id: str | None = None
        self._selected_channel_id: str | None = None
        self._pending_test_message_data: dict[str, Any] | None = None

    async def async_step_init(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Show the tracker management menu."""
        try:
            menu_options = ["add_tracker", "test_message", "manage_channels"]
            if self._get_trackers():
                menu_options.extend(["edit_tracker_select", "remove_tracker"])

            return self.async_show_menu(step_id="init", menu_options=menu_options)
        except Exception as err:
            _LOGGER.exception("Error in async_step_init: %s", err)
            raise

    async def async_step_add_tracker(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Add a new tracked stream."""
        if user_input is not None:
            return self._async_save_tracker(user_input)

        return self.async_show_form(
            step_id="add_tracker",
            data_schema=_build_tracker_schema(
                channel_options=self._build_channel_dropdown_options(),
            ),
        )

    async def async_step_manage_channels(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Manage saved Discord channel entries."""
        menu_options = ["add_channel"]
        if self._get_channel_entries():
            menu_options.extend(["edit_channel_select", "remove_channel"])

        return self.async_show_menu(step_id="manage_channels", menu_options=menu_options)

    async def async_step_add_channel(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Add a configured Discord channel entry."""
        errors: dict[str, str] = {}

        if user_input is not None:
            channel_id = str(user_input.get(ATTR_CHANNEL_ID, "")).strip()
            channel_name = str(user_input.get(ATTR_CHANNEL_NAME, "")).strip()
            if not channel_id or not channel_name:
                errors["base"] = "channel_required"
            elif self._find_channel_entry(channel_id) is not None:
                errors["base"] = "channel_exists"
            else:
                channels = self._get_channel_entries()
                channels.append(
                    {
                        ATTR_CHANNEL_ID: channel_id,
                        ATTR_CHANNEL_NAME: channel_name,
                    }
                )
                return self._async_save_channel_entries(channels)

        return self.async_show_form(
            step_id="add_channel",
            data_schema=vol.Schema(
                {
                    vol.Required(ATTR_CHANNEL_ID): TextSelector(TextSelectorConfig()),
                    vol.Required(ATTR_CHANNEL_NAME): TextSelector(TextSelectorConfig()),
                }
            ),
            errors=errors,
        )

    async def async_step_edit_channel_select(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Choose which configured channel entry to edit."""
        channels = self._get_channel_entries()
        if not channels:
            return await self.async_step_manage_channels()

        if user_input is not None:
            self._selected_channel_id = user_input[ATTR_CHANNEL_ID]
            return await self.async_step_edit_channel()

        return self.async_show_form(
            step_id="edit_channel_select",
            data_schema=vol.Schema(
                {
                    vol.Required(ATTR_CHANNEL_ID): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                dict(
                                    value=entry[ATTR_CHANNEL_ID],
                                    label=(
                                        f"{entry[ATTR_CHANNEL_ID]} - "
                                        f"{entry[ATTR_CHANNEL_NAME]}"
                                    ),
                                )
                                for entry in channels
                            ],
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    )
                }
            ),
        )

    async def async_step_edit_channel(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Edit a configured Discord channel entry."""
        selected = self._find_channel_entry(self._selected_channel_id)
        if selected is None:
            return await self.async_step_manage_channels()

        errors: dict[str, str] = {}
        if user_input is not None:
            channel_id = str(user_input.get(ATTR_CHANNEL_ID, "")).strip()
            channel_name = str(user_input.get(ATTR_CHANNEL_NAME, "")).strip()
            if not channel_id or not channel_name:
                errors["base"] = "channel_required"
            else:
                existing = self._find_channel_entry(channel_id)
                if existing is not None and existing[ATTR_CHANNEL_ID] != selected[ATTR_CHANNEL_ID]:
                    errors["base"] = "channel_exists"
                else:
                    channels = [
                        entry
                        for entry in self._get_channel_entries()
                        if entry.get(ATTR_CHANNEL_ID) != selected[ATTR_CHANNEL_ID]
                    ]
                    channels.append(
                        {
                            ATTR_CHANNEL_ID: channel_id,
                            ATTR_CHANNEL_NAME: channel_name,
                        }
                    )
                    return self._async_save_channel_entries(channels)

        return self.async_show_form(
            step_id="edit_channel",
            data_schema=vol.Schema(
                {
                    vol.Required(ATTR_CHANNEL_ID, default=selected[ATTR_CHANNEL_ID]): TextSelector(
                        TextSelectorConfig()
                    ),
                    vol.Required(
                        ATTR_CHANNEL_NAME,
                        default=selected[ATTR_CHANNEL_NAME],
                    ): TextSelector(TextSelectorConfig()),
                }
            ),
            errors=errors,
        )

    async def async_step_remove_channel(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Remove a configured Discord channel entry."""
        channels = self._get_channel_entries()
        if not channels:
            return await self.async_step_manage_channels()

        if user_input is not None:
            remove_channel_id = user_input[ATTR_CHANNEL_ID]
            channels = [
                entry
                for entry in channels
                if entry.get(ATTR_CHANNEL_ID) != remove_channel_id
            ]
            return self._async_save_channel_entries(channels)

        return self.async_show_form(
            step_id="remove_channel",
            data_schema=vol.Schema(
                {
                    vol.Required(ATTR_CHANNEL_ID): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                dict(
                                    value=entry[ATTR_CHANNEL_ID],
                                    label=(
                                        f"{entry[ATTR_CHANNEL_ID]} - "
                                        f"{entry[ATTR_CHANNEL_NAME]}"
                                    ),
                                )
                                for entry in channels
                            ],
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    )
                }
            ),
        )

    async def async_step_test_message(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Send a fake live, update, or offline message for previewing."""
        self._pending_test_message_data = None
        errors: dict[str, str] = {}
        defaults = self._get_test_message_defaults(user_input)

        if user_input is not None:
            try:
                normalized = self._normalize_test_message_data(user_input)
                requires_image_inputs = (
                    normalized.get(TEST_SEND_LIVE_IMAGE)
                    or normalized.get(TEST_SEND_UPDATE_IMAGE)
                    or normalized.get(TEST_SEND_OFFLINE_IMAGE)
                )
                if requires_image_inputs:
                    self._pending_test_message_data = normalized
                    return await self.async_step_test_message_images()

                normalized[TEST_THUMBNAIL_URL] = None
                normalized[TEST_CHANNEL_PICTURE] = None
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
                    data=(self._config_entry.options or {}) | {CONF_TEST_MESSAGE: saved_test_data},
                )

        return self.async_show_form(
            step_id="test_message",
            data_schema=_build_test_message_schema(
                defaults,
                channel_options=self._build_channel_dropdown_options(
                    current_channel_id=str(defaults.get(ATTR_CHANNEL_ID, "") or ""),
                ),
            ),
            errors=errors,
            description_placeholders={
                "default_channel": str(
                    self._config_entry.data.get(CONF_DEFAULT_CHANNEL) or "not set"
                )
            },
        )

    async def async_step_test_message_images(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Collect optional image URLs only when test image toggles are enabled."""
        if self._pending_test_message_data is None:
            return await self.async_step_test_message()

        errors: dict[str, str] = {}
        defaults = self._get_test_message_defaults(self._pending_test_message_data)

        if user_input is not None:
            pending = dict(self._pending_test_message_data)
            pending[TEST_THUMBNAIL_URL] = (
                str(user_input.get(TEST_THUMBNAIL_URL, "")).strip() or None
            )
            pending[TEST_CHANNEL_PICTURE] = (
                str(user_input.get(TEST_CHANNEL_PICTURE, "")).strip() or None
            )

            try:
                saved_test_data = await self._async_run_test_message(pending)
            except HomeAssistantError as err:
                _LOGGER.warning("Failed to send Discord test message: %s", err)
                errors["base"] = "test_message_failed"
            except DiscordApiError as err:
                _LOGGER.warning("Discord API rejected the test message: %s", err)
                errors["base"] = "test_message_failed"
            else:
                self._pending_test_message_data = None
                return self.async_create_entry(
                    title="",
                    data=(self._config_entry.options or {}) | {CONF_TEST_MESSAGE: saved_test_data},
                )

        return self.async_show_form(
            step_id="test_message_images",
            data_schema=_build_test_message_image_schema(defaults),
            errors=errors,
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
                                dict(
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
            menu_options=[
                "edit_tracker",
                "preview_tracker_template",
                "tracker_health",
                "copy_tracker_diagnostics",
                "test_message",
                "confirm_reset_templates",
            ],
            description_placeholders={"entity_id": str(tracker[ATTR_ENTITY_ID])},
        )

    async def async_step_preview_tracker_template(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Choose which tracker template to preview."""
        tracker = self._get_selected_tracker()
        if tracker is None:
            return await self.async_step_init()

        return self.async_show_menu(
            step_id="preview_tracker_template",
            menu_options=[
                "preview_tracker_template_live",
                "preview_tracker_template_update",
                "preview_tracker_template_offline",
            ],
            description_placeholders={"entity_id": str(tracker[ATTR_ENTITY_ID])},
        )

    async def async_step_preview_tracker_template_live(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Show a rendered preview of the live template."""
        return self._async_show_tracker_preview("live")

    async def async_step_preview_tracker_template_update(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Show a rendered preview of the update template."""
        return self._async_show_tracker_preview("update")

    async def async_step_preview_tracker_template_offline(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Show a rendered preview of the offline template."""
        return self._async_show_tracker_preview("offline")

    async def async_step_tracker_health(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Show tracker health and last-action diagnostics."""
        tracker = self._get_selected_tracker()
        runtime = self._get_selected_tracker_runtime()
        if tracker is None or runtime is None:
            return await self.async_step_init()

        return self.async_show_form(
            step_id="tracker_health",
            data_schema=vol.Schema({}),
            description_placeholders={
                "entity_id": str(tracker[ATTR_ENTITY_ID]),
                "tracker_id": str(runtime.get(ATTR_TRACKER_ID, "n/a")),
                "channel_id": str(runtime.get(ATTR_CHANNEL_ID) or "default channel"),
                "message_id": str(runtime.get(ATTR_MESSAGE_ID) or "n/a"),
                "last_state": str(runtime.get("last_state") or "n/a"),
                "last_action": str(runtime.get("last_action") or "n/a"),
                "last_processed_at": str(runtime.get("last_processed_at") or "n/a"),
                "last_error": str(runtime.get("last_error") or "none"),
            },
        )

    async def async_step_copy_tracker_diagnostics(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Show a copy-ready diagnostics block for the selected tracker."""
        tracker = self._get_selected_tracker()
        runtime = self._get_selected_tracker_runtime()
        if tracker is None or runtime is None:
            return await self.async_step_init()

        diagnostics_text = self._build_tracker_health_report(runtime)
        return self.async_show_form(
            step_id="copy_tracker_diagnostics",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        TRACKER_DIAGNOSTICS_TEXT,
                        default=diagnostics_text,
                    ): TextSelector(TextSelectorConfig(multiline=True)),
                }
            ),
            description_placeholders={
                "entity_id": str(tracker[ATTR_ENTITY_ID]),
            },
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
            data_schema=_build_tracker_schema(
                defaults,
                channel_options=self._build_channel_dropdown_options(
                    current_channel_id=str(tracker.get(ATTR_CHANNEL_ID, "") or ""),
                ),
            ),
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
                data=(self._config_entry.options or {}) | {CONF_TRACKERS: trackers},
            )

        return self.async_show_form(
            step_id="remove_tracker",
            data_schema=vol.Schema(
                {
                    vol.Required(ATTR_TRACKER_ID): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                dict(
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
        options = self._config_entry.options or {}
        saved = dict(options.get(CONF_TEST_MESSAGE, {}))
        defaults: dict[str, Any] = {
            ATTR_CHANNEL_ID: (
                saved.get(ATTR_CHANNEL_ID)
                or self._config_entry.data.get(CONF_DEFAULT_CHANNEL)
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
            TEST_SEND_LIVE_IMAGE: bool(saved.get(TEST_SEND_LIVE_IMAGE, True)),
            TEST_SEND_UPDATE_IMAGE: bool(saved.get(TEST_SEND_UPDATE_IMAGE, True)),
            TEST_SEND_OFFLINE_IMAGE: bool(saved.get(TEST_SEND_OFFLINE_IMAGE, True)),
            CONF_URL: saved.get(CONF_URL, "https://www.twitch.tv/test_streamer"),
            TEST_THUMBNAIL_URL: saved.get(TEST_THUMBNAIL_URL) or "",
            TEST_CHANNEL_PICTURE: saved.get(TEST_CHANNEL_PICTURE) or "",
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
            TEST_SEND_LIVE_IMAGE: bool(user_input.get(TEST_SEND_LIVE_IMAGE, True)),
            TEST_SEND_UPDATE_IMAGE: bool(user_input.get(TEST_SEND_UPDATE_IMAGE, True)),
            TEST_SEND_OFFLINE_IMAGE: bool(user_input.get(TEST_SEND_OFFLINE_IMAGE, True)),
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
            self._config_entry.entry_id
        )
        if entry_data is None:
            raise HomeAssistantError("The Discord config entry is not loaded.")

        manager = self.hass.data.get(DOMAIN, {}).get(DATA_STREAM_TRACKER)
        if manager is None:
            raise HomeAssistantError("The stream tracker manager is not available.")

        client = entry_data["client"]
        saved = dict((self._config_entry.options or {}).get(CONF_TEST_MESSAGE, {}))
        channel_id = test_data.get(ATTR_CHANNEL_ID) or self._config_entry.data.get(
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
        action = str(test_data[TEST_ACTION])
        include_images = {
            "live": bool(test_data.get(TEST_SEND_LIVE_IMAGE, True)),
            "update": bool(test_data.get(TEST_SEND_UPDATE_IMAGE, True)),
            "offline": bool(test_data.get(TEST_SEND_OFFLINE_IMAGE, True)),
        }
        action_embeds = embeds if include_images.get(action, True) else []

        message_id = saved.get(ATTR_MESSAGE_ID)
        if action == "live" or not message_id:
            response = await client.async_send_message(
                str(channel_id),
                content,
                embeds=action_embeds,
            )
            message_id = response.get("id", message_id)
        else:
            try:
                response = await client.async_edit_message(
                    str(channel_id),
                    str(message_id),
                    content=content,
                    embeds=action_embeds,
                )
            except DiscordApiError:
                response = await client.async_send_message(
                    str(channel_id),
                    content,
                    embeds=action_embeds,
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
            return manager.get_trackers_for_entry(self._config_entry.entry_id)

        options = self._config_entry.options or {}
        return [
            dict(tracker)
            for tracker in options.get(CONF_TRACKERS, [])
        ]

    def _get_channel_entries(self) -> list[dict[str, str]]:
        """Return configured Discord channel entries."""
        options = self._config_entry.options or {}
        entries = [
            {
                ATTR_CHANNEL_ID: str(entry.get(ATTR_CHANNEL_ID, "")).strip(),
                ATTR_CHANNEL_NAME: str(entry.get(ATTR_CHANNEL_NAME, "")).strip(),
            }
            for entry in options.get(CONF_CHANNELS, [])
            if str(entry.get(ATTR_CHANNEL_ID, "")).strip()
            and str(entry.get(ATTR_CHANNEL_NAME, "")).strip()
        ]
        entries.sort(key=lambda item: item[ATTR_CHANNEL_NAME].lower())
        return entries

    def _find_channel_entry(self, channel_id: str | None) -> dict[str, str] | None:
        """Return a configured channel entry by ID."""
        if not channel_id:
            return None

        normalized = str(channel_id).strip()
        for entry in self._get_channel_entries():
            if entry[ATTR_CHANNEL_ID] == normalized:
                return entry
        return None

    def _build_channel_dropdown_options(
        self,
        current_channel_id: str = "",
    ) -> list[dict[str, str]]:
        """Build channel select options with configured channel labels."""
        options: list[dict[str, str]] = [
            dict(value="", label="Use default channel")
        ]
        channel_entries = self._get_channel_entries()

        options.extend(
            [
                dict(
                    value=entry[ATTR_CHANNEL_ID],
                    label=f"{entry[ATTR_CHANNEL_ID]} - {entry[ATTR_CHANNEL_NAME]}",
                )
                for entry in channel_entries
            ]
        )

        normalized_current = str(current_channel_id or "").strip()
        if normalized_current and all(
            entry[ATTR_CHANNEL_ID] != normalized_current for entry in channel_entries
        ):
            options.append(
                dict(
                    value=normalized_current,
                    label=f"{normalized_current} - (unconfigured)",
                )
            )

        return options

    def _async_save_channel_entries(
        self,
        channel_entries: list[dict[str, str]],
    ) -> ConfigFlowResult:
        """Save channel entries while preserving other options."""
        return self.async_create_entry(
            title="",
            data=(self._config_entry.options or {}) | {CONF_CHANNELS: channel_entries},
        )

    def _get_selected_tracker(self) -> dict[str, Any] | None:
        """Return the tracker selected for editing."""
        if self._selected_tracker_id is None:
            return None

        for tracker in self._get_trackers():
            if tracker.get(ATTR_TRACKER_ID) == self._selected_tracker_id:
                return tracker
        return None

    def _get_selected_tracker_runtime(self) -> dict[str, Any] | None:
        """Return full runtime tracker data from the tracker manager."""
        if self._selected_tracker_id is None:
            return None

        manager = self.hass.data.get(DOMAIN, {}).get(DATA_STREAM_TRACKER)
        if manager is None:
            return None

        runtime = manager.get_tracker(self._selected_tracker_id)
        if runtime is None:
            return None
        if runtime.get(ATTR_ENTRY_ID) != self._config_entry.entry_id:
            return None
        return runtime

    def _build_tracker_health_report(self, runtime: Mapping[str, Any]) -> str:
        """Build a copy-friendly tracker diagnostics block."""
        lines = [
            f"tracker_id: {runtime.get(ATTR_TRACKER_ID) or 'n/a'}",
            f"entity_id: {runtime.get(ATTR_ENTITY_ID) or 'n/a'}",
            f"channel_id: {runtime.get(ATTR_CHANNEL_ID) or 'default channel'}",
            f"message_id: {runtime.get(ATTR_MESSAGE_ID) or 'n/a'}",
            f"last_state: {runtime.get('last_state') or 'n/a'}",
            f"last_action: {runtime.get('last_action') or 'n/a'}",
            f"last_processed_at: {runtime.get('last_processed_at') or 'n/a'}",
            f"last_error: {runtime.get('last_error') or 'none'}",
            f"last_title: {runtime.get('last_title') or 'n/a'}",
            f"last_game: {runtime.get('last_game') or 'n/a'}",
            f"last_viewers: {runtime.get('last_viewers') or 'n/a'}",
            f"last_started_at: {runtime.get('last_started_at') or 'n/a'}",
        ]
        return "\n".join(lines)

    def _async_show_tracker_preview(self, template_kind: str) -> ConfigFlowResult:
        """Render and show one tracker template preview."""
        tracker = self._get_selected_tracker()
        runtime = self._get_selected_tracker_runtime()
        if tracker is None or runtime is None:
            return self.async_show_menu(step_id="init", menu_options=["add_tracker", "test_message", "manage_channels"])

        manager = self.hass.data.get(DOMAIN, {}).get(DATA_STREAM_TRACKER)
        if manager is None:
            preview = "Stream tracker manager unavailable."
        else:
            try:
                preview = manager.preview_tracker_template(
                    runtime[ATTR_TRACKER_ID],
                    template_kind,
                )
            except HomeAssistantError as err:
                preview = f"Template preview failed: {err}"

        return self.async_show_form(
            step_id=f"preview_tracker_template_{template_kind}",
            data_schema=vol.Schema({}),
            description_placeholders={
                "entity_id": str(tracker[ATTR_ENTITY_ID]),
                "preview": preview,
            },
        )

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
            data=(self._config_entry.options or {}) | {CONF_TRACKERS: trackers},
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
            f"{user_input[ATTR_ENTITY_ID]}_{channel_id or 'default'}_{self._config_entry.entry_id}"
        )
        reset_templates = force_defaults

        return {
            ATTR_TRACKER_ID: normalized_tracker_id,
            ATTR_ENTRY_ID: self._config_entry.entry_id,
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
            ATTR_SEND_LIVE_IMAGE: bool(
                user_input.get(ATTR_SEND_LIVE_IMAGE, True)
            ),
            ATTR_SEND_UPDATE_IMAGE: bool(
                user_input.get(ATTR_SEND_UPDATE_IMAGE, True)
            ),
            ATTR_SEND_OFFLINE_IMAGE: bool(
                user_input.get(ATTR_SEND_OFFLINE_IMAGE, True)
            ),
        }

    def _tracker_label(self, tracker: Mapping[str, Any]) -> str:
        """Create a readable label for a stored tracker."""
        channel = tracker.get(ATTR_CHANNEL_ID) or "default channel"
        return f"{tracker[ATTR_ENTITY_ID]} → {channel}"

