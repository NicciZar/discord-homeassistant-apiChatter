"""Notify support for Discord API Chatter."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.notify import ATTR_DATA, ATTR_TARGET, BaseNotificationService
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType
from homeassistant.core import HomeAssistant

from .const import (
    ATTR_ALLOWED_MENTIONS,
    ATTR_EMBEDS,
    ATTR_ENTRY_ID,
    ATTR_TTS,
    CONF_DEFAULT_CHANNEL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)
ATTR_EMBED = "embed"


async def async_get_service(
    hass: HomeAssistant,
    config: ConfigType,
    discovery_info: DiscoveryInfoType | None = None,
) -> "DiscordNotificationService | None":
    """Return the Discord notification service."""
    if discovery_info is None:
        return None

    entry_id = discovery_info[ATTR_ENTRY_ID]
    entry_data = hass.data[DOMAIN][entry_id]
    return DiscordNotificationService(
        client=entry_data["client"],
        default_channel=entry_data["entry"].data.get(CONF_DEFAULT_CHANNEL),
    )


class DiscordNotificationService(BaseNotificationService):
    """Implementation of the Home Assistant notify platform for Discord."""

    def __init__(self, client, default_channel: str | None) -> None:
        """Initialize the notify service."""
        self._client = client
        self._default_channel = default_channel

    async def async_send_message(self, message: str, **kwargs: Any) -> None:
        """Send a notification to one or more Discord channels."""
        data = kwargs.get(ATTR_DATA) or {}
        targets = kwargs.get(ATTR_TARGET)

        if isinstance(targets, str):
            channel_ids = [targets]
        elif targets:
            channel_ids = [str(target) for target in targets]
        elif self._default_channel:
            channel_ids = [self._default_channel]
        else:
            _LOGGER.error(
                "No Discord target provided and no default channel is configured."
            )
            return

        embeds = data.get(ATTR_EMBEDS)
        if embeds is None and ATTR_EMBED in data:
            embeds = data[ATTR_EMBED]

        if embeds is not None and not isinstance(embeds, list):
            embeds = [embeds]

        for channel_id in channel_ids:
            await self._client.async_send_message(
                channel_id,
                message,
                tts=bool(data.get(ATTR_TTS, False)),
                embeds=embeds,
                allowed_mentions=data.get(ATTR_ALLOWED_MENTIONS),
            )
