"""Service registration for Discord API Chatter."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components.notify import ATTR_MESSAGE
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv

from .api import DiscordApiClient
from .const import (
    ATTR_ALLOWED_MENTIONS,
    ATTR_CHANNEL_ID,
    ATTR_CONTENT,
    ATTR_DELETE_MESSAGE,
    ATTR_EMBEDS,
    ATTR_ENTRY_ID,
    ATTR_LIVE_TEMPLATE,
    ATTR_MESSAGE_ID,
    ATTR_OFFLINE_TEMPLATE,
    ATTR_SYNC_NOW,
    ATTR_TRACKER_ID,
    ATTR_TTS,
    ATTR_UPDATE_ON_GAME_CHANGE,
    ATTR_UPDATE_ON_TITLE_CHANGE,
    ATTR_UPDATE_TEMPLATE,
    CONF_DEFAULT_CHANNEL,
    DATA_ENTRIES,
    DATA_STREAM_TRACKER,
    DOMAIN,
    SERVICE_DELETE_MESSAGE,
    SERVICE_EDIT_MESSAGE,
    SERVICE_SEND_MESSAGE,
    SERVICE_TRACK_STREAM,
    SERVICE_UNTRACK_STREAM,
)
from .stream_tracker import StreamTrackerManager

SEND_MESSAGE_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_ENTRY_ID): cv.string,
        vol.Optional(ATTR_CHANNEL_ID): cv.string,
        vol.Required(ATTR_MESSAGE): cv.string,
        vol.Optional(ATTR_TTS, default=False): cv.boolean,
        vol.Optional(ATTR_EMBEDS): vol.Any(dict, [dict]),
        vol.Optional(ATTR_ALLOWED_MENTIONS): dict,
    },
    extra=vol.ALLOW_EXTRA,
)

EDIT_MESSAGE_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_ENTRY_ID): cv.string,
        vol.Optional(ATTR_CHANNEL_ID): cv.string,
        vol.Required(ATTR_MESSAGE_ID): cv.string,
        vol.Optional(ATTR_CONTENT): cv.string,
        vol.Optional(ATTR_EMBEDS): vol.Any(dict, [dict]),
        vol.Optional(ATTR_ALLOWED_MENTIONS): dict,
    },
    extra=vol.ALLOW_EXTRA,
)

DELETE_MESSAGE_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_ENTRY_ID): cv.string,
        vol.Optional(ATTR_CHANNEL_ID): cv.string,
        vol.Required(ATTR_MESSAGE_ID): cv.string,
    },
    extra=vol.ALLOW_EXTRA,
)

TRACK_STREAM_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_id,
        vol.Optional(ATTR_TRACKER_ID): cv.string,
        vol.Optional(ATTR_ENTRY_ID): cv.string,
        vol.Optional(ATTR_CHANNEL_ID): cv.string,
        vol.Optional(ATTR_LIVE_TEMPLATE): cv.string,
        vol.Optional(ATTR_UPDATE_TEMPLATE): cv.string,
        vol.Optional(ATTR_OFFLINE_TEMPLATE): cv.string,
        vol.Optional(ATTR_UPDATE_ON_TITLE_CHANGE, default=True): cv.boolean,
        vol.Optional(ATTR_UPDATE_ON_GAME_CHANGE, default=True): cv.boolean,
        vol.Optional(ATTR_SYNC_NOW, default=True): cv.boolean,
    },
    extra=vol.ALLOW_EXTRA,
)

UNTRACK_STREAM_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_TRACKER_ID): cv.string,
        vol.Optional(ATTR_ENTITY_ID): cv.entity_id,
        vol.Optional(ATTR_ENTRY_ID): cv.string,
        vol.Optional(ATTR_CHANNEL_ID): cv.string,
        vol.Optional(ATTR_DELETE_MESSAGE, default=False): cv.boolean,
    },
    extra=vol.ALLOW_EXTRA,
)


async def async_register_services(hass: HomeAssistant) -> None:
    """Register the integration services once."""

    async def _async_handle_send_message(service: ServiceCall) -> dict[str, Any]:
        client, default_channel = _resolve_client(hass, service)
        channel_id = _resolve_channel_id(service, default_channel)

        result = await client.async_send_message(
            channel_id,
            service.data[ATTR_MESSAGE],
            tts=service.data.get(ATTR_TTS, False),
            embeds=_normalize_embeds(service.data.get(ATTR_EMBEDS)),
            allowed_mentions=service.data.get(ATTR_ALLOWED_MENTIONS),
        )

        return {
            "ok": True,
            "channel_id": channel_id,
            "message_id": result.get("id"),
            "response": result,
        }

    async def _async_handle_edit_message(service: ServiceCall) -> dict[str, Any]:
        client, default_channel = _resolve_client(hass, service)
        channel_id = _resolve_channel_id(service, default_channel)

        if (
            ATTR_CONTENT not in service.data
            and ATTR_EMBEDS not in service.data
            and ATTR_ALLOWED_MENTIONS not in service.data
        ):
            raise HomeAssistantError(
                "Editing requires `content`, `embeds`, or `allowed_mentions`."
            )

        result = await client.async_edit_message(
            channel_id,
            service.data[ATTR_MESSAGE_ID],
            content=service.data.get(ATTR_CONTENT),
            embeds=_normalize_embeds(service.data.get(ATTR_EMBEDS)),
            allowed_mentions=service.data.get(ATTR_ALLOWED_MENTIONS),
        )

        return {
            "ok": True,
            "channel_id": channel_id,
            "message_id": result.get("id", service.data[ATTR_MESSAGE_ID]),
            "response": result,
        }

    async def _async_handle_delete_message(service: ServiceCall) -> dict[str, Any]:
        client, default_channel = _resolve_client(hass, service)
        channel_id = _resolve_channel_id(service, default_channel)
        message_id = service.data[ATTR_MESSAGE_ID]

        await client.async_delete_message(channel_id, message_id)

        return {
            "ok": True,
            "channel_id": channel_id,
            "message_id": message_id,
            "deleted": True,
        }

    async def _async_handle_track_stream(service: ServiceCall) -> dict[str, Any]:
        manager = _get_stream_tracker_manager(hass)
        return await manager.async_register_tracker(dict(service.data))

    async def _async_handle_untrack_stream(service: ServiceCall) -> dict[str, Any]:
        manager = _get_stream_tracker_manager(hass)
        return await manager.async_remove_tracker(dict(service.data))

    if not hass.services.has_service(DOMAIN, SERVICE_SEND_MESSAGE):
        hass.services.async_register(
            DOMAIN,
            SERVICE_SEND_MESSAGE,
            _async_handle_send_message,
            schema=SEND_MESSAGE_SCHEMA,
            supports_response=SupportsResponse.OPTIONAL,
        )

    if not hass.services.has_service(DOMAIN, SERVICE_EDIT_MESSAGE):
        hass.services.async_register(
            DOMAIN,
            SERVICE_EDIT_MESSAGE,
            _async_handle_edit_message,
            schema=EDIT_MESSAGE_SCHEMA,
            supports_response=SupportsResponse.OPTIONAL,
        )

    if not hass.services.has_service(DOMAIN, SERVICE_DELETE_MESSAGE):
        hass.services.async_register(
            DOMAIN,
            SERVICE_DELETE_MESSAGE,
            _async_handle_delete_message,
            schema=DELETE_MESSAGE_SCHEMA,
            supports_response=SupportsResponse.OPTIONAL,
        )

    if not hass.services.has_service(DOMAIN, SERVICE_TRACK_STREAM):
        hass.services.async_register(
            DOMAIN,
            SERVICE_TRACK_STREAM,
            _async_handle_track_stream,
            schema=TRACK_STREAM_SCHEMA,
            supports_response=SupportsResponse.OPTIONAL,
        )

    if not hass.services.has_service(DOMAIN, SERVICE_UNTRACK_STREAM):
        hass.services.async_register(
            DOMAIN,
            SERVICE_UNTRACK_STREAM,
            _async_handle_untrack_stream,
            schema=UNTRACK_STREAM_SCHEMA,
            supports_response=SupportsResponse.OPTIONAL,
        )


async def async_unregister_services(hass: HomeAssistant) -> None:
    """Unregister services when the last entry is removed."""
    for service_name in (
        SERVICE_SEND_MESSAGE,
        SERVICE_EDIT_MESSAGE,
        SERVICE_DELETE_MESSAGE,
        SERVICE_TRACK_STREAM,
        SERVICE_UNTRACK_STREAM,
    ):
        if hass.services.has_service(DOMAIN, service_name):
            hass.services.async_remove(DOMAIN, service_name)


def _resolve_client(
    hass: HomeAssistant,
    service: ServiceCall,
) -> tuple[DiscordApiClient, str | None]:
    """Resolve the config entry and client to use for a service call."""
    entries = hass.data.get(DOMAIN, {}).get(DATA_ENTRIES, {})
    if not entries:
        raise HomeAssistantError("No Discord API Chatter config entries are loaded.")

    entry_id = service.data.get(ATTR_ENTRY_ID)
    if entry_id is not None:
        if entry_id not in entries:
            raise HomeAssistantError(
                f"Discord API Chatter entry '{entry_id}' was not found."
            )
        entry_data = entries[entry_id]
    elif len(entries) == 1:
        entry_data = next(iter(entries.values()))
    else:
        raise HomeAssistantError(
            "Multiple Discord API Chatter entries exist; specify `entry_id`."
        )

    entry = entry_data["entry"]
    return entry_data["client"], entry.data.get(CONF_DEFAULT_CHANNEL)


def _resolve_channel_id(service: ServiceCall, default_channel: str | None) -> str:
    """Resolve the target channel for a service call."""
    channel_id = service.data.get(ATTR_CHANNEL_ID) or default_channel
    if not channel_id:
        raise HomeAssistantError(
            "No `channel_id` was supplied and no default channel is configured."
        )
    return str(channel_id)


def _normalize_embeds(value: Any) -> list[dict[str, Any]] | None:
    """Normalize the embeds payload to a list of dictionaries."""
    if value is None:
        return None
    if isinstance(value, list):
        return value
    return [value]


def _get_stream_tracker_manager(hass: HomeAssistant) -> StreamTrackerManager:
    """Return the active stream tracker manager."""
    manager = hass.data.get(DOMAIN, {}).get(DATA_STREAM_TRACKER)
    if manager is None:
        raise HomeAssistantError("The stream tracker manager is not initialized.")
    return manager
