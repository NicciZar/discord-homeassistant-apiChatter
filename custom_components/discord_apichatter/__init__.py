"""The Discord API Chatter integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_API_TOKEN, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv, discovery
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .api import DiscordApiClient, DiscordApiError, DiscordAuthenticationError
from .const import ATTR_ENTRY_ID, DOMAIN
from .services import async_register_services, async_unregister_services

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
PLATFORMS = [Platform.NOTIFY]


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

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "client": client,
        "entry": entry,
    }

    await async_register_services(hass)

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
    domain_data.pop(entry.entry_id, None)

    if not domain_data:
        await async_unregister_services(hass)
        hass.data.pop(DOMAIN, None)

    return True
