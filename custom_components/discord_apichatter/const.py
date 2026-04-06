"""Constants for the Discord API Chatter integration."""

from __future__ import annotations

from typing import Final

from homeassistant.const import CONF_URL, Platform

DOMAIN: Final = "discord_apichatter"
DEFAULT_NAME: Final = "Discord API Chatter"
DISCORD_API_BASE: Final = "https://discord.com/api/v10"
PLATFORMS: Final[list[Platform]] = [Platform.NOTIFY]

DATA_ENTRIES: Final = "entries"
DATA_STREAM_TRACKER: Final = "stream_tracker"

STORAGE_KEY: Final = f"{DOMAIN}_tracked_streams"
STORAGE_VERSION: Final = 1

CONF_DEFAULT_CHANNEL: Final = "default_channel"
CONF_TEST_MESSAGE: Final = "test_message"
CONF_TRACKERS: Final = "trackers"

ATTR_ALLOWED_MENTIONS: Final = "allowed_mentions"
ATTR_CHANNEL_ID: Final = "channel_id"
ATTR_CONTENT: Final = "content"
ATTR_DELETE_MESSAGE: Final = "delete_message"
ATTR_EMBEDS: Final = "embeds"
ATTR_ENTRY_ID: Final = "entry_id"
ATTR_LIVE_TEMPLATE: Final = "live_template"
ATTR_MESSAGE_ID: Final = "message_id"
ATTR_OFFLINE_TEMPLATE: Final = "offline_template"
ATTR_SYNC_NOW: Final = "sync_now"
ATTR_TRACKER_ID: Final = "tracker_id"
ATTR_TTS: Final = "tts"
ATTR_UPDATE_ON_GAME_CHANGE: Final = "update_on_game_change"
ATTR_UPDATE_ON_TITLE_CHANGE: Final = "update_on_title_change"
ATTR_UPDATE_TEMPLATE: Final = "update_template"

SERVICE_SEND_MESSAGE: Final = "send_message"
SERVICE_EDIT_MESSAGE: Final = "edit_message"
SERVICE_DELETE_MESSAGE: Final = "delete_message"
SERVICE_TRACK_STREAM: Final = "track_stream"
SERVICE_UNTRACK_STREAM: Final = "untrack_stream"

URL_PLACEHOLDER = {CONF_URL: "https://www.home-assistant.io/integrations/discord"}
