"""Config flow for Discord API Chatter."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_API_TOKEN, CONF_NAME
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import DiscordApiClient, DiscordApiError, DiscordAuthenticationError
from .const import CONF_DEFAULT_CHANNEL, DEFAULT_NAME, DOMAIN

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_API_TOKEN): str,
        vol.Optional(CONF_DEFAULT_CHANNEL): str,
    }
)


class DiscordApiChatterConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Discord API Chatter."""

    VERSION = 1

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
