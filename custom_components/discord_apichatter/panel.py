"""Panel configuration views for Discord API Chatter."""

from __future__ import annotations

import inspect
import json
import logging
from pathlib import Path
import re
from typing import Any

from aiohttp import web

from homeassistant.components.http import HomeAssistantView
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers.typing import ConfigType

from .const import (
    ATTR_CHANNEL_ID,
    ATTR_CHANNEL_NAME,
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
    DOMAIN,
)
from .stream_tracker import (
    DEFAULT_LIVE_TEMPLATE,
    DEFAULT_OFFLINE_TEMPLATE,
    DEFAULT_UPDATE_TEMPLATE,
)

_LOGGER = logging.getLogger(__name__)

PANEL_URL_PATH = "discord-apichatter-config"
PANEL_WEB_URL = "/api/discord_apichatter/panel"
PANEL_CONFIG_URL = "/api/discord_apichatter/panel/config"
PANEL_SAVE_URL = "/api/discord_apichatter/panel/save"
PANEL_TEST_URL = "/api/discord_apichatter/panel/test"

# Discord snowflake IDs are numeric strings of 17�20 digits.
_SNOWFLAKE_RE = re.compile(r"^\d{17,20}$")

# Maximum accepted request-body size for the save endpoint.
_MAX_PAYLOAD_BYTES = 128 * 1024  # 128 KB

# Allowed keys for tracker objects persisted in options (prevents arbitrary injection).
_TRACKER_ALLOWED_KEYS: frozenset[str] = frozenset(
    {
        ATTR_TRACKER_ID,
        ATTR_ENTITY_ID,
        ATTR_CHANNEL_ID,
        ATTR_UPDATE_ON_TITLE_CHANGE,
        ATTR_UPDATE_ON_GAME_CHANGE,
        ATTR_SYNC_NOW,
        ATTR_LIVE_TEMPLATE,
        ATTR_UPDATE_TEMPLATE,
        ATTR_OFFLINE_TEMPLATE,
        ATTR_SEND_LIVE_IMAGE,
        ATTR_SEND_UPDATE_IMAGE,
        ATTR_SEND_OFFLINE_IMAGE,
        ATTR_MESSAGE_ID,
    }
)

# Allowed keys for the test_message object persisted in options.
_TEST_MESSAGE_ALLOWED_KEYS: frozenset[str] = frozenset(
    {
        ATTR_CHANNEL_ID,
        ATTR_ENTITY_ID,
        ATTR_MESSAGE_ID,
        "test_action",
        "test_name",
        "test_title",
        "test_game",
        "test_viewers",
        "test_started_at",
        "test_send_live_image",
        "test_send_update_image",
        "test_send_offline_image",
        "url",
        "test_thumbnail_url",
        "test_channel_picture",
        "last_title",
        "last_game",
        "last_viewers",
        "last_started_at",
    }
)

_PANEL_TEMPLATE_PATH = Path(__file__).with_name("panel.html")


def _get_domain_entries(hass: HomeAssistant) -> list[ConfigEntry]:
    """Return all config entries for this integration domain."""
    return list(hass.config_entries.async_entries(DOMAIN))


def _serialize_entry(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, Any]:
    """Serialize a config entry for panel API responses.

    Reads from both entry.data and entry.options so that configurations
    written by older versions of the integration are surfaced correctly.
    """
    data = entry.data or {}
    options = entry.options or {}

    # default_channel lives in entry.data (current schema).
    # Older installs may have placed it in entry.options � accept both.
    default_channel = (
        data.get(CONF_DEFAULT_CHANNEL)
        or options.get(CONF_DEFAULT_CHANNEL)
        or ""
    )

    # channels / trackers / test_message live in entry.options (current schema).
    # Older installs may have stored them in entry.data � fall back gracefully.
    channels = options.get(CONF_CHANNELS) or data.get(CONF_CHANNELS) or []
    trackers = options.get(CONF_TRACKERS) or data.get(CONF_TRACKERS) or []
    if not trackers:
        # Legacy tracker definitions may still live in storage; surface them in the panel.
        manager = hass.data.get(DOMAIN, {}).get(DATA_STREAM_TRACKER)
        if manager is not None:
            try:
                trackers = manager.get_trackers_for_entry(entry.entry_id)
            except Exception:  # pragma: no cover - defensive
                trackers = []
    test_message = options.get(CONF_TEST_MESSAGE) or data.get(CONF_TEST_MESSAGE) or {}

    return {
        "entry_id": entry.entry_id,
        "title": entry.title,
        "default_channel": default_channel,
        "channels": channels,
        "trackers": trackers,
        "test_message": test_message,
    }


def _find_entry_by_id(hass: HomeAssistant, entry_id: str) -> ConfigEntry | None:
    """Find a config entry by entry_id for this domain."""
    for entry in _get_domain_entries(hass):
        if entry.entry_id == entry_id:
            return entry
    return None


def _panel_icon_kwargs(register_panel: Any) -> dict[str, str]:
    """Return the correct icon kwarg name for this HA runtime."""
    try:
        params = inspect.signature(register_panel).parameters
    except (TypeError, ValueError):
        params = {}

    if "icon" in params:
        return {"icon": "mdi:discord"}
    if "sidebar_icon" in params:
        return {"sidebar_icon": "mdi:discord"}

    return {"sidebar_icon": "mdi:discord"}


def _render_panel_html() -> str:
    """Render the external panel HTML with runtime placeholders."""
    template = _PANEL_TEMPLATE_PATH.read_text(encoding="utf-8")
    return (
        template.replace("__PANEL_CONFIG_URL__", PANEL_CONFIG_URL)
        .replace("__PANEL_SAVE_URL__", PANEL_SAVE_URL)
        .replace("__PANEL_TEST_URL__", PANEL_TEST_URL)
        .replace("__DEFAULT_LIVE_TEMPLATE__", json.dumps(DEFAULT_LIVE_TEMPLATE))
        .replace("__DEFAULT_UPDATE_TEMPLATE__", json.dumps(DEFAULT_UPDATE_TEMPLATE))
        .replace("__DEFAULT_OFFLINE_TEMPLATE__", json.dumps(DEFAULT_OFFLINE_TEMPLATE))
    )


class DiscordApiChatterPanelView(HomeAssistantView):
    """Serve the external panel HTML."""

    url = PANEL_WEB_URL
    name = "api:discord_apichatter:panel"
    # The iframe shell itself must be public in some HA versions because
    # iframe requests do not carry the authenticated frontend context.
    # Sensitive operations remain protected in config/save views.
    requires_auth = False
    requires_admin = False

    async def get(self, request: web.Request) -> web.Response:
        """Return panel HTML with security headers."""
        html = _render_panel_html()
        return web.Response(
            text=html,
            content_type="text/html",
            headers={
                # Tight CSP: no external resources; inline styles/scripts only.
                "Content-Security-Policy": (
                    "default-src 'none'; "
                    "style-src 'unsafe-inline'; "
                    "script-src 'unsafe-inline'; "
                    "connect-src 'self';"
                ),
                "X-Content-Type-Options": "nosniff",
                "X-Frame-Options": "SAMEORIGIN",
            },
        )


class DiscordApiChatterPanelConfigView(HomeAssistantView):
    """Read current integration config for panel UI."""

    url = PANEL_CONFIG_URL
    name = "api:discord_apichatter:panel_config"
    requires_auth = True
    requires_admin = True

    async def get(self, request: web.Request) -> web.Response:
        """Return all entries and options for panel editing."""
        hass: HomeAssistant = request.app["hass"]
        entries = [_serialize_entry(hass, entry) for entry in _get_domain_entries(hass)]
        return self.json({"entries": entries})


class DiscordApiChatterPanelSaveView(HomeAssistantView):
    """Persist panel changes to a config entry."""

    url = PANEL_SAVE_URL
    name = "api:discord_apichatter:panel_save"
    requires_auth = True
    requires_admin = True

    async def post(self, request: web.Request) -> web.Response:
        """Validate and save selected fields from the panel UI."""
        hass: HomeAssistant = request.app["hass"]

        # Guard against oversized payloads before reading the body.
        content_length = request.content_length
        if content_length is not None and content_length > _MAX_PAYLOAD_BYTES:
            return self.json_message("Request payload too large.", status_code=413)

        try:
            body = await request.read()
        except Exception:
            return self.json_message("Failed to read request body.", status_code=400)

        if len(body) > _MAX_PAYLOAD_BYTES:
            return self.json_message("Request payload too large.", status_code=413)

        try:
            payload: ConfigType = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            return self.json_message("Invalid JSON payload.", status_code=400)

        if not isinstance(payload, dict):
            return self.json_message("Payload must be a JSON object.", status_code=400)

        # ── entry_id ──────────────────────────────────────────────────────
        entry_id = str(payload.get("entry_id", "")).strip()
        if not entry_id:
            return self.json_message("'entry_id' is required.", status_code=400)

        entry = _find_entry_by_id(hass, entry_id)
        if entry is None:
            return self.json_message("Entry not found.", status_code=404)

        # ── Raw field extraction ──────────────────────────────────────────
        default_channel = str(payload.get("default_channel", "")).strip()
        channels        = payload.get("channels", [])
        trackers        = payload.get("trackers", [])
        test_message    = payload.get("test_message", {})

        # ── Type checks ───────────────────────────────────────────────────
        if not isinstance(channels, list):
            return self.json_message("'channels' must be a JSON array.", status_code=400)
        if not isinstance(trackers, list):
            return self.json_message("'trackers' must be a JSON array.", status_code=400)
        if not isinstance(test_message, dict):
            return self.json_message("'test_message' must be a JSON object.", status_code=400)

        # ── Default channel snowflake validation ──────────────────────────
        if default_channel and not _SNOWFLAKE_RE.match(default_channel):
            return self.json_message(
                "'default_channel' must be a Discord snowflake (17–20 digits only).",
                status_code=400,
            )

        # ── Channels ──────────────────────────────────────────────────────
        normalized_channels: list[dict[str, str]] = []
        seen_channel_ids: set[str] = set()
        for idx, channel in enumerate(channels):
            if not isinstance(channel, dict):
                return self.json_message(
                    f"Channel at index {idx} must be a JSON object.",
                    status_code=400,
                )
            channel_id   = str(channel.get("channel_id",   "")).strip()
            channel_name = str(channel.get("channel_name", "")).strip()
            if not channel_id or not channel_name:
                return self.json_message(
                    f"Channel at index {idx} requires non-empty 'channel_id' and 'channel_name'.",
                    status_code=400,
                )
            if not _SNOWFLAKE_RE.match(channel_id):
                return self.json_message(
                    f"Channel at index {idx}: 'channel_id' must be a Discord snowflake "
                    f"(17–20 digits). Got: '{channel_id[:30]}'.",
                    status_code=400,
                )
            if channel_id in seen_channel_ids:
                return self.json_message(
                    f"Duplicate 'channel_id' '{channel_id}' found in channels list.",
                    status_code=400,
                )
            seen_channel_ids.add(channel_id)
            normalized_channels.append(
                {"channel_id": channel_id, "channel_name": channel_name}
            )

        # ── Trackers ──────────────────────────────────────────────────────
        normalized_trackers: list[dict[str, Any]] = []
        seen_tracker_ids: set[str] = set()
        for idx, tracker in enumerate(trackers):
            if not isinstance(tracker, dict):
                return self.json_message(
                    f"Tracker at index {idx} must be a JSON object.",
                    status_code=400,
                )
            tracker_id = str(tracker.get(ATTR_TRACKER_ID, "")).strip()
            entity_id  = str(tracker.get(ATTR_ENTITY_ID,  "")).strip()
            if not tracker_id or not entity_id:
                return self.json_message(
                    f"Tracker at index {idx} requires non-empty 'tracker_id' and 'entity_id'.",
                    status_code=400,
                )
            if tracker_id in seen_tracker_ids:
                return self.json_message(
                    f"Duplicate 'tracker_id' '{tracker_id}' found in trackers list.",
                    status_code=400,
                )
            seen_tracker_ids.add(tracker_id)

            # Reject unknown keys to prevent arbitrary data injection.
            bad_keys = set(tracker.keys()) - _TRACKER_ALLOWED_KEYS
            if bad_keys:
                return self.json_message(
                    f"Tracker at index {idx} contains unknown fields: "
                    f"{', '.join(sorted(bad_keys))}.",
                    status_code=400,
                )
            normalized_trackers.append(
                {k: v for k, v in tracker.items() if k in _TRACKER_ALLOWED_KEYS}
            )

        # ── Test message ──────────────────────────────────────────────────
        bad_test_keys = set(test_message.keys()) - _TEST_MESSAGE_ALLOWED_KEYS
        if bad_test_keys:
            return self.json_message(
                f"'test_message' contains unknown fields: "
                f"{', '.join(sorted(bad_test_keys))}.",
                status_code=400,
            )
        safe_test_message = {
            k: v for k, v in test_message.items() if k in _TEST_MESSAGE_ALLOWED_KEYS
        }

        # ── Persist ───────────────────────────────────────────────────────
        new_data = dict(entry.data)
        if default_channel:
            new_data[CONF_DEFAULT_CHANNEL] = default_channel
        else:
            new_data.pop(CONF_DEFAULT_CHANNEL, None)

        new_options = dict(entry.options or {})
        # Ensure default_channel doesn't linger in options (older schema artefact).
        new_options.pop(CONF_DEFAULT_CHANNEL, None)
        new_options[CONF_CHANNELS]     = normalized_channels
        new_options[CONF_TRACKERS]     = normalized_trackers
        new_options[CONF_TEST_MESSAGE] = safe_test_message

        hass.config_entries.async_update_entry(entry, data=new_data, options=new_options)

        _LOGGER.debug("Saved panel config for entry %s", entry.entry_id)
        return self.json({"ok": True})


class DiscordApiChatterPanelTestView(HomeAssistantView):
    """Send a test Discord message from the panel."""

    url = PANEL_TEST_URL
    name = "api:discord_apichatter:panel_test"
    requires_auth = True
    requires_admin = True

    async def post(self, request: web.Request) -> web.Response:
        """Render and send a test message using the panel form values."""
        hass: HomeAssistant = request.app["hass"]

        content_length = request.content_length
        if content_length is not None and content_length > _MAX_PAYLOAD_BYTES:
            return self.json_message("Request payload too large.", status_code=413)

        try:
            body = await request.read()
        except Exception:
            return self.json_message("Failed to read request body.", status_code=400)

        if len(body) > _MAX_PAYLOAD_BYTES:
            return self.json_message("Request payload too large.", status_code=413)

        try:
            payload: ConfigType = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            return self.json_message("Invalid JSON payload.", status_code=400)

        if not isinstance(payload, dict):
            return self.json_message("Payload must be a JSON object.", status_code=400)

        entry_id = str(payload.get("entry_id", "")).strip()
        if not entry_id:
            return self.json_message("'entry_id' is required.", status_code=400)

        entry = _find_entry_by_id(hass, entry_id)
        if entry is None:
            return self.json_message("Entry not found.", status_code=404)

        # Resolve the Discord client for this entry.
        entry_data = hass.data.get(DOMAIN, {}).get(DATA_ENTRIES, {}).get(entry_id)
        if entry_data is None:
            return self.json_message(
                "Integration entry is not loaded.", status_code=503
            )
        manager = hass.data.get(DOMAIN, {}).get(DATA_STREAM_TRACKER)
        if manager is None:
            return self.json_message(
                "Stream tracker manager is not available.", status_code=503
            )

        client = entry_data["client"]

        # Resolve channel – form override wins, then entry default.
        channel_id = str(payload.get(ATTR_CHANNEL_ID, "")).strip()
        if not channel_id:
            channel_id = str(entry.data.get(CONF_DEFAULT_CHANNEL, "")).strip()
        if not channel_id:
            return self.json_message(
                "No channel ID supplied and no default channel is configured.",
                status_code=400,
            )
        if not _SNOWFLAKE_RE.match(channel_id):
            return self.json_message(
                "'channel_id' must be a Discord snowflake (17\u201320 digits).",
                status_code=400,
            )

        entity_id = str(payload.get(ATTR_ENTITY_ID, "sensor.test")).strip()
        action = str(payload.get("test_action", "live")).strip()
        if action not in {"live", "update", "offline"}:
            return self.json_message(
                "'test_action' must be 'live', 'update', or 'offline'.", status_code=400
            )

        thumbnail_url = str(payload.get("test_thumbnail_url", "")).strip() or None
        channel_picture = str(payload.get("test_channel_picture", "")).strip() or None

        # Build a fake tracker config using the entry's saved test_message defaults
        # for templates (so the panel test honours any custom templates on saved trackers).
        saved_test = (entry.options or {}).get(CONF_TEST_MESSAGE, {})
        fake_tracker = {
            ATTR_ENTITY_ID: entity_id,
            ATTR_LIVE_TEMPLATE: saved_test.get(ATTR_LIVE_TEMPLATE, DEFAULT_LIVE_TEMPLATE),
            ATTR_UPDATE_TEMPLATE: saved_test.get(ATTR_UPDATE_TEMPLATE, DEFAULT_UPDATE_TEMPLATE),
            ATTR_OFFLINE_TEMPLATE: saved_test.get(ATTR_OFFLINE_TEMPLATE, DEFAULT_OFFLINE_TEMPLATE),
            "last_title": saved_test.get("last_title"),
            "last_game": saved_test.get("last_game"),
            "last_viewers": saved_test.get("last_viewers"),
            "last_started_at": saved_test.get("last_started_at"),
            "last_thumbnail_url": thumbnail_url,
            "last_channel_picture": channel_picture,
            "url": str(payload.get("url", "")).strip() or None,
        }

        synthetic_state = State(
            entity_id,
            "offline" if action == "offline" else "streaming",
            {
                "friendly_name": str(payload.get("test_name", entity_id)),
                "title": str(payload.get("test_title", "")).strip() or None,
                "game": str(payload.get("test_game", "")).strip() or None,
                "game_name": str(payload.get("test_game", "")).strip() or None,
                "viewers": str(payload.get("test_viewers", "")).strip() or None,
                "started_at": str(payload.get("test_started_at", "")).strip() or None,
                "thumbnail_url": thumbnail_url,
                "entity_picture": thumbnail_url,
                "channel_picture": channel_picture,
                "url": fake_tracker["url"],
            },
        )

        try:
            content = manager._render_message(
                fake_tracker, "panel_test", synthetic_state, action
            )
        except Exception as err:  # pragma: no cover
            return self.json_message(f"Template render failed: {err}", status_code=422)

        embeds = manager._build_embeds(thumbnail_url, channel_picture)
        include_image = {
            "live": bool(payload.get("test_send_live_image", True)),
            "update": bool(payload.get("test_send_update_image", True)),
            "offline": bool(payload.get("test_send_offline_image", True)),
        }
        action_embeds = embeds if include_image.get(action, True) else []

        message_id = saved_test.get(ATTR_MESSAGE_ID)
        try:
            if action == "live" or not message_id:
                response = await client.async_send_message(
                    channel_id, content, embeds=action_embeds
                )
                message_id = response.get("id", message_id)
            else:
                try:
                    response = await client.async_edit_message(
                        channel_id, str(message_id), content=content, embeds=action_embeds
                    )
                except Exception:
                    response = await client.async_send_message(
                        channel_id, content, embeds=action_embeds
                    )
                    message_id = response.get("id", message_id)
        except Exception as err:
            _LOGGER.warning("Panel test message failed: %s", err)
            return self.json_message(f"Discord API error: {err}", status_code=502)

        # Persist the updated test state (including the new message_id) back to options.
        new_test = dict(saved_test) | dict(payload) | {
            ATTR_CHANNEL_ID: channel_id,
            ATTR_MESSAGE_ID: message_id,
            "last_title": str(payload.get("test_title", "")).strip() or None,
            "last_game": str(payload.get("test_game", "")).strip() or None,
            "last_viewers": str(payload.get("test_viewers", "")).strip() or None,
            "last_started_at": str(payload.get("test_started_at", "")).strip() or None,
        }
        # Strip keys not in the allowed set before persisting.
        safe_test = {k: v for k, v in new_test.items() if k in _TEST_MESSAGE_ALLOWED_KEYS}
        new_options = dict(entry.options or {})
        new_options[CONF_TEST_MESSAGE] = safe_test
        hass.config_entries.async_update_entry(entry, options=new_options)

        _LOGGER.debug(
            "Panel test message sent (action=%s, channel=%s, message_id=%s)",
            action, channel_id, message_id,
        )
        return self.json({"ok": True, "message_id": message_id, "action": action})


async def async_setup_panel(hass: HomeAssistant) -> None:
    """Register panel views and sidebar item."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    if domain_data.get("panel_registered"):
        return

    hass.http.register_view(DiscordApiChatterPanelView())
    hass.http.register_view(DiscordApiChatterPanelConfigView())
    hass.http.register_view(DiscordApiChatterPanelSaveView())
    hass.http.register_view(DiscordApiChatterPanelTestView())

    frontend_component = getattr(getattr(hass, "components", None), "frontend", None)
    if frontend_component is not None:
        common_kwargs = {
            "component_name": "iframe",
            "sidebar_title": "Discord API Chatter",
            "frontend_url_path": PANEL_URL_PATH,
            "config": {"url": PANEL_WEB_URL},
            "require_admin": True,
        }
        frontend_component.async_register_built_in_panel(
            **common_kwargs,
            **_panel_icon_kwargs(frontend_component.async_register_built_in_panel),
        )
    else:
        # Newer runtimes may not expose hass.components; use module helper APIs.
        from homeassistant.components import frontend as frontend_module

        common_kwargs = {
            "component_name": "iframe",
            "sidebar_title": "Discord API Chatter",
            "frontend_url_path": PANEL_URL_PATH,
            "config": {"url": PANEL_WEB_URL},
            "require_admin": True,
        }
        frontend_module.async_register_built_in_panel(
            hass,
            **common_kwargs,
            **_panel_icon_kwargs(frontend_module.async_register_built_in_panel),
        )

    domain_data["panel_registered"] = True


async def async_unload_panel(hass: HomeAssistant) -> None:
    """Remove panel when last entry is unloaded."""
    domain_data = hass.data.get(DOMAIN, {})
    if not domain_data.get("panel_registered"):
        return

    try:
        frontend_component = getattr(getattr(hass, "components", None), "frontend", None)
        if frontend_component is not None:
            frontend_component.async_remove_panel(PANEL_URL_PATH)
        else:
            from homeassistant.components import frontend as frontend_module

            frontend_module.async_remove_panel(hass, PANEL_URL_PATH)
    except Exception:  # pragma: no cover - defensive
        _LOGGER.debug("Panel '%s' was not registered; skipping removal.", PANEL_URL_PATH)

    domain_data.pop("panel_registered", None)
