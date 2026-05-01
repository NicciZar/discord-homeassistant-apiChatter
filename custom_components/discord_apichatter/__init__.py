"""The Discord API Chatter integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_API_TOKEN, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv, discovery
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .api import DiscordApiClient, DiscordApiError, DiscordAuthenticationError
from .const import (
    ATTR_ENTRY_ID,
    CONF_CHANNELS,
    CONF_DEFAULT_CHANNEL,
    CONF_TEST_MESSAGE,
    CONF_TRACKERS,
    DATA_ENTRIES,
    DATA_STREAM_TRACKER,
    DOMAIN,
)
from .panel import async_setup_panel, async_unload_panel
from .services import async_register_services, async_unregister_services
from .stream_tracker import StreamTrackerManager

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
PLATFORMS = [Platform.NOTIFY]

_LOGGER = logging.getLogger(__name__)


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate config entries from older schema versions.

    v1 → v2: Move any channels/trackers/test_message accidentally stored in
    entry.data to entry.options, and move default_channel from options to data
    if it ended up in the wrong place.
    """
    _LOGGER.debug(
        "Migrating Discord API Chatter entry '%s' from schema version %s",
        entry.entry_id,
        entry.version,
    )

    if entry.version > 2:
        _LOGGER.error(
            "Cannot migrate entry '%s': unsupported schema version %s",
            entry.entry_id,
            entry.version,
        )
        return False

    if entry.version == 1:
        new_data    = dict(entry.data)
        new_options = dict(entry.options or {})

        # Move options-level keys that ended up in entry.data in older builds.
        for key in (CONF_CHANNELS, CONF_TRACKERS, CONF_TEST_MESSAGE):
            if key in new_data:
                if key not in new_options:
                    new_options[key] = new_data.pop(key)
                else:
                    # options already has a value — drop the stale data copy.
                    del new_data[key]

        # If default_channel somehow ended up in options, move it to data.
        if CONF_DEFAULT_CHANNEL in new_options and CONF_DEFAULT_CHANNEL not in new_data:
            new_data[CONF_DEFAULT_CHANNEL] = new_options.pop(CONF_DEFAULT_CHANNEL)

        hass.config_entries.async_update_entry(
            entry, data=new_data, options=new_options, version=2
        )
        _LOGGER.info(
            "Migrated Discord API Chatter entry '%s' to schema version 2.",
            entry.entry_id,
        )

    return True


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the integration."""
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Discord API Chatter from a config entry."""
    session = async_get_clientsession(hass)
    client = DiscordApiClient(session=session, token=entry.data[CONF_API_TOKEN])

    try:
        await client.async_get_current_user()
    except DiscordAuthenticationError as err:
        raise ConfigEntryAuthFailed("Invalid Discord bot token") from err
    except DiscordApiError as err:
        raise ConfigEntryNotReady(f"Failed to connect to Discord: {err}") from err

    domain_data = hass.data.setdefault(DOMAIN, {})
    domain_data.setdefault(DATA_ENTRIES, {})[entry.entry_id] = {
        "client": client,
        "entry": entry,
    }

    tracker_manager = domain_data.get(DATA_STREAM_TRACKER)
    if tracker_manager is None:
        tracker_manager = StreamTrackerManager(hass)
        await tracker_manager.async_initialize()
        domain_data[DATA_STREAM_TRACKER] = tracker_manager

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    options = entry.options or {}
    if CONF_TRACKERS in options:
        await tracker_manager.async_apply_entry_trackers(
            entry.entry_id,
            options[CONF_TRACKERS],
        )

    await async_register_services(hass)
    await async_setup_panel(hass)

    hass.async_create_task(
        discovery.async_load_platform(
            hass,
            Platform.NOTIFY,
            DOMAIN,
            dict(entry.data) | {ATTR_ENTRY_ID: entry.entry_id},
            {},
        )
    )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    domain_data = hass.data.get(DOMAIN, {})

    if tracker_manager := domain_data.get(DATA_STREAM_TRACKER):
        tracker_manager.async_detach_entry(entry.entry_id)

    entries = domain_data.get(DATA_ENTRIES, {})
    entries.pop(entry.entry_id, None)

    if not entries:
        await async_unregister_services(hass)

        await async_unload_panel(hass)

        if tracker_manager := domain_data.get(DATA_STREAM_TRACKER):
            await tracker_manager.async_shutdown()

        hass.data.pop(DOMAIN, None)

    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the config entry when options are updated."""
    await hass.config_entries.async_reload(entry.entry_id)
