"""Persistent stream tracking and auto-update support for Discord API Chatter."""

from __future__ import annotations

import asyncio
from typing import Any

from homeassistant.const import (
    ATTR_ENTITY_ID,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, State, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.storage import Store
from homeassistant.helpers.template import Template, TemplateError
from homeassistant.util import dt as dt_util
from homeassistant.util import slugify

from .api import DiscordApiClient, DiscordApiError
from .const import (
    ATTR_CHANNEL_ID,
    ATTR_DELETE_MESSAGE,
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
    DATA_ENTRIES,
    DOMAIN,
    STORAGE_KEY,
    STORAGE_VERSION,
)

DEFAULT_LIVE_TEMPLATE = """🔴 **{{ name }}** is now live on Twitch!\n\n🎮 **Game:** {{ game or 'Unknown' }}\n📝 **Title:** {{ title or 'No title set' }}{% if viewers is not none %}\n👀 **Viewers:** {{ viewers }}{% endif %}{% if started_at %}\n🕒 **Started:** {{ started_at }}{% endif %}{% if stream_duration_human %}\n⏱️ **Live for:** {{ stream_duration_human }}{% endif %}\n\n🔗 {{ url }}"""
DEFAULT_UPDATE_TEMPLATE = """🟣 **{{ name }}** updated the stream.\n\n🎮 **Game:** {{ game or 'Unknown' }}\n📝 **Title:** {{ title or 'No title set' }}{% if viewers is not none %}\n👀 **Viewers:** {{ viewers }}{% endif %}{% if stream_duration_human %}\n⏱️ **Live for:** {{ stream_duration_human }}{% endif %}\n\n🔗 {{ url }}"""
DEFAULT_OFFLINE_TEMPLATE = """⚫ **{{ name }}** is now offline.\n\n📝 **Last title:** {{ title or 'No title set' }}\n🎮 **Last game:** {{ game or 'Unknown' }}{% if started_at %}\n🕒 **Started:** {{ started_at }}{% endif %}{% if stream_duration_human %}\n⏱️ **Streamed for:** {{ stream_duration_human }}{% endif %}\n\n🔗 {{ url }}"""

LIVE_STATES = {"streaming", "live", STATE_ON}
OFFLINE_STATES = {"offline", "not_streaming", STATE_OFF, "idle"}
IGNORED_STATES = {STATE_UNKNOWN, STATE_UNAVAILABLE, "unknown", "unavailable"}


class StreamTrackerManager:
    """Persist and auto-update tracked stream announcements."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the manager."""
        self.hass = hass
        self._store: Store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self._trackers: dict[str, dict[str, Any]] = {}
        self._unsubscribers: dict[str, CALLBACK_TYPE] = {}
        self._lock = asyncio.Lock()

    async def async_initialize(self) -> None:
        """Load persisted trackers and restore listeners."""
        stored = await self._store.async_load()
        self._trackers = (stored or {}).get("trackers", {})

        for tracker_id in list(self._trackers):
            self._async_subscribe_tracker(tracker_id)

    async def async_shutdown(self) -> None:
        """Remove all registered listeners."""
        for tracker_id in list(self._unsubscribers):
            self._async_unsubscribe_tracker(tracker_id)

    @callback
    def get_trackers_for_entry(self, entry_id: str) -> list[dict[str, Any]]:
        """Return all trackers associated with a config entry."""
        entries = self.hass.data.get(DOMAIN, {}).get(DATA_ENTRIES, {})
        single_entry = len(entries) == 1
        trackers: list[dict[str, Any]] = []

        for tracker in self._trackers.values():
            tracker_entry_id = tracker.get(ATTR_ENTRY_ID)
            if tracker_entry_id not in (None, entry_id):
                continue
            if tracker_entry_id is None and not single_entry:
                continue

            trackers.append(
                {
                    ATTR_TRACKER_ID: tracker[ATTR_TRACKER_ID],
                    ATTR_ENTITY_ID: tracker[ATTR_ENTITY_ID],
                    ATTR_ENTRY_ID: entry_id,
                    ATTR_CHANNEL_ID: tracker.get(ATTR_CHANNEL_ID),
                    ATTR_LIVE_TEMPLATE: tracker.get(
                        ATTR_LIVE_TEMPLATE, DEFAULT_LIVE_TEMPLATE
                    ),
                    ATTR_UPDATE_TEMPLATE: tracker.get(
                        ATTR_UPDATE_TEMPLATE, DEFAULT_UPDATE_TEMPLATE
                    ),
                    ATTR_OFFLINE_TEMPLATE: tracker.get(
                        ATTR_OFFLINE_TEMPLATE, DEFAULT_OFFLINE_TEMPLATE
                    ),
                    ATTR_UPDATE_ON_TITLE_CHANGE: tracker.get(
                        ATTR_UPDATE_ON_TITLE_CHANGE, True
                    ),
                    ATTR_UPDATE_ON_GAME_CHANGE: tracker.get(
                        ATTR_UPDATE_ON_GAME_CHANGE, True
                    ),
                }
            )

        trackers.sort(
            key=lambda item: (item[ATTR_ENTITY_ID], item.get(ATTR_CHANNEL_ID) or "")
        )
        return trackers

    async def async_apply_entry_trackers(
        self,
        entry_id: str,
        trackers: list[dict[str, Any]],
    ) -> None:
        """Apply the desired tracker set for a config entry."""
        desired_ids: set[str] = set()

        for tracker in trackers:
            tracker_config = dict(tracker)
            tracker_config[ATTR_ENTRY_ID] = entry_id
            tracker_id = str(
                tracker_config.get(ATTR_TRACKER_ID)
                or self._build_tracker_id(
                    tracker_config[ATTR_ENTITY_ID],
                    tracker_config.get(ATTR_CHANNEL_ID),
                    entry_id,
                )
            )
            tracker_config[ATTR_TRACKER_ID] = tracker_id
            desired_ids.add(tracker_id)
            await self.async_register_tracker(tracker_config | {ATTR_SYNC_NOW: True})

        for tracker_id, current_tracker in list(self._trackers.items()):
            current_entry_id = current_tracker.get(ATTR_ENTRY_ID)
            if current_entry_id is None and len(self.hass.data.get(DOMAIN, {}).get(DATA_ENTRIES, {})) == 1:
                current_entry_id = entry_id
                current_tracker[ATTR_ENTRY_ID] = entry_id

            if current_entry_id == entry_id and tracker_id not in desired_ids:
                self._async_unsubscribe_tracker(tracker_id)
                self._trackers.pop(tracker_id, None)

        await self._async_save()

    @callback
    def async_detach_entry(self, entry_id: str) -> None:
        """Detach listeners for trackers that belong to a config entry."""
        single_entry = len(self.hass.data.get(DOMAIN, {}).get(DATA_ENTRIES, {})) == 1
        for tracker_id, tracker in self._trackers.items():
            tracker_entry_id = tracker.get(ATTR_ENTRY_ID)
            if tracker_entry_id == entry_id or (tracker_entry_id is None and single_entry):
                self._async_unsubscribe_tracker(tracker_id)

    async def async_register_tracker(self, config: dict[str, Any]) -> dict[str, Any]:
        """Register or update a tracked stream entity."""
        entity_id = str(config[ATTR_ENTITY_ID])
        channel_id = config.get(ATTR_CHANNEL_ID)
        entry_id = config.get(ATTR_ENTRY_ID)

        if entry_id is None:
            entries = self.hass.data.get(DOMAIN, {}).get(DATA_ENTRIES, {})
            if len(entries) == 1:
                entry_id = next(iter(entries))

        tracker_id = str(
            config.get(ATTR_TRACKER_ID)
            or self._build_tracker_id(entity_id, channel_id, entry_id)
        )

        tracker = self._trackers.get(tracker_id, {})
        tracker.update(
            {
                ATTR_TRACKER_ID: tracker_id,
                ATTR_ENTITY_ID: entity_id,
                ATTR_ENTRY_ID: entry_id,
                ATTR_CHANNEL_ID: str(channel_id) if channel_id is not None else None,
                ATTR_LIVE_TEMPLATE: config.get(
                    ATTR_LIVE_TEMPLATE,
                    tracker.get(ATTR_LIVE_TEMPLATE, DEFAULT_LIVE_TEMPLATE),
                ),
                ATTR_UPDATE_TEMPLATE: config.get(
                    ATTR_UPDATE_TEMPLATE,
                    tracker.get(ATTR_UPDATE_TEMPLATE, DEFAULT_UPDATE_TEMPLATE),
                ),
                ATTR_OFFLINE_TEMPLATE: config.get(
                    ATTR_OFFLINE_TEMPLATE,
                    tracker.get(ATTR_OFFLINE_TEMPLATE, DEFAULT_OFFLINE_TEMPLATE),
                ),
                ATTR_UPDATE_ON_TITLE_CHANGE: bool(
                    config.get(
                        ATTR_UPDATE_ON_TITLE_CHANGE,
                        tracker.get(ATTR_UPDATE_ON_TITLE_CHANGE, True),
                    )
                ),
                ATTR_UPDATE_ON_GAME_CHANGE: bool(
                    config.get(
                        ATTR_UPDATE_ON_GAME_CHANGE,
                        tracker.get(ATTR_UPDATE_ON_GAME_CHANGE, True),
                    )
                ),
            }
        )

        self._trackers[tracker_id] = tracker

        # Validate that a usable Discord entry/channel exists.
        self._resolve_client(tracker)
        self._resolve_channel_id(tracker)

        self._async_subscribe_tracker(tracker_id)
        await self._async_save()

        result = {
            "ok": True,
            "tracker_id": tracker_id,
            "entity_id": entity_id,
            "channel_id": tracker.get(ATTR_CHANNEL_ID) or self._resolve_channel_id(tracker),
            "message_id": tracker.get(ATTR_MESSAGE_ID),
        }

        if config.get(ATTR_SYNC_NOW, True):
            result["sync"] = await self.async_sync_tracker(tracker_id)
            result["message_id"] = self._trackers[tracker_id].get(ATTR_MESSAGE_ID)

        return result

    async def async_remove_tracker(self, config: dict[str, Any]) -> dict[str, Any]:
        """Remove a tracked stream entity."""
        tracker_id = config.get(ATTR_TRACKER_ID)
        if tracker_id is None:
            tracker_id = self._find_tracker_id(
                entity_id=config.get(ATTR_ENTITY_ID),
                channel_id=config.get(ATTR_CHANNEL_ID),
                entry_id=config.get(ATTR_ENTRY_ID),
            )

        if tracker_id not in self._trackers:
            raise HomeAssistantError(f"Tracked stream '{tracker_id}' was not found.")

        tracker = self._trackers.pop(tracker_id)
        self._async_unsubscribe_tracker(tracker_id)

        deleted = False
        if config.get(ATTR_DELETE_MESSAGE) and tracker.get(ATTR_MESSAGE_ID):
            client = self._resolve_client(tracker)
            channel_id = self._resolve_channel_id(tracker)
            try:
                await client.async_delete_message(channel_id, tracker[ATTR_MESSAGE_ID])
            except DiscordApiError:
                deleted = False
            else:
                deleted = True

        await self._async_save()
        return {
            "ok": True,
            "tracker_id": tracker_id,
            "deleted_message": deleted,
        }

    async def async_sync_tracker(self, tracker_id: str) -> dict[str, Any]:
        """Synchronize a tracker with the entity's current state."""
        tracker = self._trackers.get(tracker_id)
        if tracker is None:
            raise HomeAssistantError(f"Tracked stream '{tracker_id}' was not found.")

        state = self.hass.states.get(tracker[ATTR_ENTITY_ID])
        if state is None:
            return {
                "ok": True,
                "tracker_id": tracker_id,
                "action": "waiting",
                "reason": "entity_not_found",
            }

        return await self._async_process_state(tracker_id, state, force=True)

    @callback
    def _async_subscribe_tracker(self, tracker_id: str) -> None:
        """Subscribe to state changes for a tracker."""
        tracker = self._trackers.get(tracker_id)
        if tracker is None:
            return

        self._async_unsubscribe_tracker(tracker_id)

        @callback
        def _state_listener(event) -> None:
            new_state = event.data.get("new_state")
            if new_state is not None:
                self.hass.async_create_task(
                    self._async_process_state(tracker_id, new_state, force=False)
                )

        self._unsubscribers[tracker_id] = async_track_state_change_event(
            self.hass,
            [tracker[ATTR_ENTITY_ID]],
            _state_listener,
        )

    @callback
    def _async_unsubscribe_tracker(self, tracker_id: str) -> None:
        """Remove a tracker state listener."""
        if unsub := self._unsubscribers.pop(tracker_id, None):
            unsub()

    async def _async_process_state(
        self,
        tracker_id: str,
        new_state: State,
        *,
        force: bool,
    ) -> dict[str, Any]:
        """Handle a new state update for a tracked entity."""
        async with self._lock:
            tracker = self._trackers.get(tracker_id)
            if tracker is None:
                return {"ok": False, "reason": "tracker_removed"}

            state_value = str(new_state.state).lower()
            if state_value in IGNORED_STATES:
                return {
                    "ok": True,
                    "tracker_id": tracker_id,
                    "action": "ignored",
                    "state": state_value,
                }

            is_live = self._state_is_live(state_value)
            was_live = bool(tracker.get("last_is_live", False))
            title = self._coalesce_text(
                new_state.attributes.get("title"), tracker.get("last_title")
            )
            game = self._coalesce_text(
                new_state.attributes.get("game") or new_state.attributes.get("game_name"),
                tracker.get("last_game"),
            )
            viewers = new_state.attributes.get("viewers", tracker.get("last_viewers"))
            started_at = new_state.attributes.get(
                "started_at", tracker.get("last_started_at")
            )
            thumbnail_url = self._resolve_image_url(
                new_state.attributes.get("thumbnail_url")
                or new_state.attributes.get("entity_picture")
                or tracker.get("last_thumbnail_url")
            )
            channel_picture = self._resolve_image_url(
                new_state.attributes.get("channel_picture")
                or tracker.get("last_channel_picture")
            )

            title_changed = title != tracker.get("last_title")
            game_changed = game != tracker.get("last_game")

            client = self._resolve_client(tracker)
            channel_id = self._resolve_channel_id(tracker)
            embeds = self._build_embeds(thumbnail_url, channel_picture)

            action = "ignored"
            response: dict[str, Any] | None = None

            if is_live and (not was_live or not tracker.get(ATTR_MESSAGE_ID)):
                content = self._render_message(tracker, tracker_id, new_state, "live")
                response = await client.async_send_message(
                    channel_id,
                    content,
                    embeds=embeds,
                )
                tracker[ATTR_MESSAGE_ID] = response.get("id")
                action = "sent"
            elif is_live and (
                force
                or (tracker.get(ATTR_UPDATE_ON_TITLE_CHANGE, True) and title_changed)
                or (tracker.get(ATTR_UPDATE_ON_GAME_CHANGE, True) and game_changed)
            ):
                content = self._render_message(tracker, tracker_id, new_state, "update")
                if tracker.get(ATTR_MESSAGE_ID):
                    try:
                        response = await client.async_edit_message(
                            channel_id,
                            tracker[ATTR_MESSAGE_ID],
                            content=content,
                            embeds=embeds,
                        )
                        action = "edited" if (title_changed or game_changed) else "synced"
                    except DiscordApiError:
                        response = await client.async_send_message(
                            channel_id,
                            content,
                            embeds=embeds,
                        )
                        tracker[ATTR_MESSAGE_ID] = response.get("id")
                        action = "sent"
                else:
                    response = await client.async_send_message(
                        channel_id,
                        content,
                        embeds=embeds,
                    )
                    tracker[ATTR_MESSAGE_ID] = response.get("id")
                    action = "sent"
            elif (not is_live) and was_live and tracker.get(ATTR_MESSAGE_ID):
                content = self._render_message(tracker, tracker_id, new_state, "offline")
                try:
                    response = await client.async_edit_message(
                        channel_id,
                        tracker[ATTR_MESSAGE_ID],
                        content=content,
                        embeds=embeds,
                    )
                    action = "edited"
                except DiscordApiError:
                    action = "ignored"

            tracker["last_state"] = state_value
            tracker["last_is_live"] = is_live
            tracker["last_title"] = title
            tracker["last_game"] = game
            tracker["last_viewers"] = viewers
            tracker["last_started_at"] = started_at
            tracker["last_thumbnail_url"] = thumbnail_url
            tracker["last_channel_picture"] = channel_picture

            await self._async_save()

            return {
                "ok": True,
                "tracker_id": tracker_id,
                "entity_id": tracker[ATTR_ENTITY_ID],
                "channel_id": channel_id,
                "message_id": tracker.get(ATTR_MESSAGE_ID),
                "state": state_value,
                "action": action,
                "response": response,
            }

    def _render_message(
        self,
        tracker: dict[str, Any],
        tracker_id: str,
        state: State,
        template_kind: str,
    ) -> str:
        """Render one of the configured Discord message templates."""
        template_map = {
            "live": tracker.get(ATTR_LIVE_TEMPLATE, DEFAULT_LIVE_TEMPLATE),
            "update": tracker.get(ATTR_UPDATE_TEMPLATE, DEFAULT_UPDATE_TEMPLATE),
            "offline": tracker.get(ATTR_OFFLINE_TEMPLATE, DEFAULT_OFFLINE_TEMPLATE),
        }
        template_string = template_map[template_kind]
        context = self._build_template_context(tracker, tracker_id, state)

        try:
            rendered = Template(template_string, self.hass).async_render(
                context,
                parse_result=False,
            )
        except TemplateError as err:
            raise HomeAssistantError(f"Failed to render {template_kind} template: {err}") from err

        rendered_text = str(rendered).strip()
        if not rendered_text:
            raise HomeAssistantError(
                f"The rendered {template_kind} template for tracker '{tracker_id}' was empty."
            )
        return rendered_text

    def _build_template_context(
        self,
        tracker: dict[str, Any],
        tracker_id: str,
        state: State,
    ) -> dict[str, Any]:
        """Build the template context for Discord message rendering."""
        entity_id = tracker[ATTR_ENTITY_ID]
        object_id = entity_id.split(".", 1)[1] if "." in entity_id else entity_id
        title = self._coalesce_text(state.attributes.get("title"), tracker.get("last_title"))
        game = self._coalesce_text(
            state.attributes.get("game") or state.attributes.get("game_name"),
            tracker.get("last_game"),
        )
        viewers = state.attributes.get("viewers", tracker.get("last_viewers"))
        started_at = state.attributes.get("started_at", tracker.get("last_started_at"))
        channel_picture = self._resolve_image_url(
            state.attributes.get("channel_picture") or tracker.get("last_channel_picture")
        )
        thumbnail_url = self._resolve_image_url(
            state.attributes.get("thumbnail_url")
            or state.attributes.get("entity_picture")
            or tracker.get("last_thumbnail_url")
        )
        stream_duration = self._calculate_stream_duration(started_at)

        return {
            "entity_id": entity_id,
            "tracker_id": tracker_id,
            "state": state.state,
            "status": state.state,
            "is_live": self._state_is_live(str(state.state).lower()),
            "name": state.name,
            "title": title,
            "game": game,
            "viewers": viewers,
            "started_at": started_at,
            "stream_duration": stream_duration,
            "stream_duration_seconds": int(stream_duration.total_seconds()) if stream_duration is not None else None,
            "stream_duration_human": self._format_duration(stream_duration),
            "channel_picture": channel_picture,
            "stream_picture": thumbnail_url,
            "thumbnail_url": thumbnail_url,
            "url": f"https://www.twitch.tv/{object_id}",
        }

    def _resolve_client(self, tracker: dict[str, Any]) -> DiscordApiClient:
        """Resolve which Discord API client should be used for a tracker."""
        entries = self.hass.data.get(DOMAIN, {}).get(DATA_ENTRIES, {})
        if not entries:
            raise HomeAssistantError("No Discord API Chatter config entries are loaded.")

        entry_id = tracker.get(ATTR_ENTRY_ID)
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

        return entry_data["client"]

    def _resolve_channel_id(self, tracker: dict[str, Any]) -> str:
        """Resolve the Discord target channel for a tracker."""
        entries = self.hass.data.get(DOMAIN, {}).get(DATA_ENTRIES, {})
        entry_id = tracker.get(ATTR_ENTRY_ID)

        if entry_id is not None:
            entry_data = entries.get(entry_id)
            default_channel = entry_data["entry"].data.get(CONF_DEFAULT_CHANNEL) if entry_data else None
        elif len(entries) == 1:
            default_channel = next(iter(entries.values()))["entry"].data.get(
                CONF_DEFAULT_CHANNEL
            )
        else:
            default_channel = None

        channel_id = tracker.get(ATTR_CHANNEL_ID) or default_channel
        if not channel_id:
            raise HomeAssistantError(
                "No `channel_id` was supplied and no default channel is configured."
            )
        return str(channel_id)

    def _find_tracker_id(
        self,
        *,
        entity_id: str | None,
        channel_id: str | None,
        entry_id: str | None,
    ) -> str:
        """Find a tracker by its identifying fields."""
        for tracker_id, tracker in self._trackers.items():
            if entity_id is not None and tracker.get(ATTR_ENTITY_ID) != entity_id:
                continue
            if channel_id is not None and str(tracker.get(ATTR_CHANNEL_ID)) != str(channel_id):
                continue
            if entry_id is not None and tracker.get(ATTR_ENTRY_ID) != entry_id:
                continue
            return tracker_id

        raise HomeAssistantError(
            "No tracked stream matched the supplied `tracker_id` or entity/channel values."
        )

    async def _async_save(self) -> None:
        """Persist trackers to Home Assistant storage."""
        await self._store.async_save({"trackers": self._trackers})

    def _build_tracker_id(
        self,
        entity_id: str,
        channel_id: str | None,
        entry_id: str | None,
    ) -> str:
        """Generate a stable tracker identifier."""
        return slugify(f"{entity_id}_{channel_id or 'default'}_{entry_id or 'auto'}")

    def _build_embeds(
        self,
        thumbnail_url: str | None,
        channel_picture: str | None,
    ) -> list[dict[str, Any]] | None:
        """Build a simple Discord embed containing the stream artwork."""
        if not thumbnail_url and not channel_picture:
            return None

        embed: dict[str, Any] = {}
        if thumbnail_url:
            embed["image"] = {"url": thumbnail_url}
        if channel_picture:
            embed["thumbnail"] = {"url": channel_picture}
        return [embed]

    def _calculate_stream_duration(self, started_at: Any):
        """Calculate how long the stream has been live."""
        if not started_at:
            return None

        try:
            started = dt_util.parse_datetime(str(started_at))
        except (TypeError, ValueError):
            return None

        if started is None:
            return None

        if started.tzinfo is None:
            started = dt_util.as_utc(started)

        now = dt_util.utcnow()
        if started > now:
            return None

        return now - started

    def _format_duration(self, duration) -> str | None:
        """Format a duration as a short human-readable string."""
        if duration is None:
            return None

        total_seconds = int(duration.total_seconds())
        if total_seconds < 0:
            return None

        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)

        parts: list[str] = []
        if hours:
            parts.append(f"{hours}h")
        if minutes or hours:
            parts.append(f"{minutes}m")
        parts.append(f"{seconds}s")
        return " ".join(parts)

    def _resolve_image_url(self, value: Any) -> str | None:
        """Normalize Twitch image URLs and fill width/height placeholders."""
        if value is None:
            return None

        url = str(value).strip()
        if not url:
            return None

        return (
            url.replace("{width}", "1280")
            .replace("{height}", "720")
            .replace("{Width}", "1280")
            .replace("{Height}", "720")
        )

    def _coalesce_text(self, current: Any, fallback: Any) -> str | None:
        """Return the current non-empty text, else the previous value."""
        if current is not None and str(current).strip() != "":
            return str(current)
        if fallback is not None and str(fallback).strip() != "":
            return str(fallback)
        return None

    def _state_is_live(self, state_value: str) -> bool:
        """Determine whether a state should be treated as live/streaming."""
        return state_value in LIVE_STATES or (
            state_value not in OFFLINE_STATES and state_value not in IGNORED_STATES
        )
