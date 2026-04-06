"""Constants for the Discord API Chatter integration."""

from __future__ import annotations

from typing import Final

from homeassistant.const import CONF_URL, Platform

DOMAIN: Final = "discord_apichatter"
DEFAULT_NAME: Final = "Discord API Chatter"
DISCORD_API_BASE: Final = "https://discord.com/api/v10"
PLATFORMS: Final[list[Platform]] = [Platform.NOTIFY]

CONF_DEFAULT_CHANNEL: Final = "default_channel"

ATTR_ALLOWED_MENTIONS: Final = "allowed_mentions"
ATTR_CHANNEL_ID: Final = "channel_id"
ATTR_CONTENT: Final = "content"
ATTR_EMBEDS: Final = "embeds"
ATTR_ENTRY_ID: Final = "entry_id"
ATTR_MESSAGE_ID: Final = "message_id"
ATTR_TTS: Final = "tts"

SERVICE_SEND_MESSAGE: Final = "send_message"
SERVICE_EDIT_MESSAGE: Final = "edit_message"
SERVICE_DELETE_MESSAGE: Final = "delete_message"

URL_PLACEHOLDER = {CONF_URL: "https://www.home-assistant.io/integrations/discord"}
