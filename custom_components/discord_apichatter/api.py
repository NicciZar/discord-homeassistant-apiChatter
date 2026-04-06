"""Async Discord REST API client used by the integration."""

from __future__ import annotations

from http import HTTPStatus
import logging
from typing import Any

from aiohttp import ClientError, ClientSession
from homeassistant.exceptions import HomeAssistantError

from .const import DISCORD_API_BASE

_LOGGER = logging.getLogger(__name__)


class DiscordApiError(HomeAssistantError):
    """Raised when the Discord API returns an error."""


class DiscordAuthenticationError(DiscordApiError):
    """Raised when the configured bot token is invalid."""


class DiscordApiClient:
    """Small wrapper around Discord's default REST API."""

    def __init__(
        self,
        session: ClientSession,
        token: str,
        base_url: str = DISCORD_API_BASE,
    ) -> None:
        """Initialize the client."""
        self._session = session
        self._token = token.strip()
        self._base_url = base_url.rstrip("/")

    @property
    def _headers(self) -> dict[str, str]:
        """Return the standard Discord REST headers for bot auth."""
        return {
            "Authorization": f"Bot {self._token}",
            "Content-Type": "application/json",
            "User-Agent": "HomeAssistantDiscordApiChatter/0.1.0",
        }

    async def async_get_current_user(self) -> dict[str, Any]:
        """Validate the bot token and return the bot user profile."""
        return await self._async_request("GET", "/users/@me")

    async def async_send_message(
        self,
        channel_id: str,
        content: str | None,
        *,
        tts: bool = False,
        embeds: list[dict[str, Any]] | None = None,
        allowed_mentions: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Send a message to a Discord channel via the REST API."""
        payload: dict[str, Any] = {"tts": tts}

        if content is not None:
            payload["content"] = content
        if embeds is not None:
            payload["embeds"] = embeds
        if allowed_mentions is not None:
            payload["allowed_mentions"] = allowed_mentions

        if "content" not in payload and "embeds" not in payload:
            raise DiscordApiError("A message requires `content` or `embeds`.")

        return await self._async_request(
            "POST",
            f"/channels/{channel_id}/messages",
            json=payload,
            expected_status=(HTTPStatus.OK, HTTPStatus.CREATED),
        )

    async def async_edit_message(
        self,
        channel_id: str,
        message_id: str,
        *,
        content: str | None = None,
        embeds: list[dict[str, Any]] | None = None,
        allowed_mentions: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Edit an existing Discord message."""
        payload: dict[str, Any] = {}

        if content is not None:
            payload["content"] = content
        if embeds is not None:
            payload["embeds"] = embeds
        if allowed_mentions is not None:
            payload["allowed_mentions"] = allowed_mentions

        if not payload:
            raise DiscordApiError(
                "Editing requires at least one of `content`, `embeds`, or `allowed_mentions`."
            )

        return await self._async_request(
            "PATCH",
            f"/channels/{channel_id}/messages/{message_id}",
            json=payload,
        )

    async def async_delete_message(self, channel_id: str, message_id: str) -> None:
        """Delete a Discord message."""
        await self._async_request(
            "DELETE",
            f"/channels/{channel_id}/messages/{message_id}",
            expected_status=HTTPStatus.NO_CONTENT,
        )

    async def _async_request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        expected_status: int | tuple[int, ...] = HTTPStatus.OK,
    ) -> dict[str, Any]:
        """Make a Discord REST API request and validate the response."""
        expected = (
            expected_status if isinstance(expected_status, tuple) else (expected_status,)
        )
        url = f"{self._base_url}{path}"

        try:
            async with self._session.request(
                method,
                url,
                headers=self._headers,
                json=json,
            ) as response:
                payload = await self._async_parse_response(response)

                if response.status == HTTPStatus.UNAUTHORIZED:
                    raise DiscordAuthenticationError("Invalid Discord bot token.")

                if response.status == HTTPStatus.TOO_MANY_REQUESTS:
                    retry_after = payload.get("retry_after", "unknown")
                    raise DiscordApiError(
                        f"Discord rate limited the request. Retry after {retry_after} seconds."
                    )

                if response.status not in expected:
                    message = payload.get("message") or payload.get("error") or str(payload)
                    raise DiscordApiError(
                        f"Discord API error {response.status}: {message}"
                    )

                return payload
        except ClientError as err:
            _LOGGER.debug("Discord API request failed: %s", err)
            raise DiscordApiError("Unable to reach the Discord REST API.") from err

    async def _async_parse_response(self, response) -> dict[str, Any]:
        """Best-effort parse of a Discord API response."""
        if response.status == HTTPStatus.NO_CONTENT:
            return {}

        if response.content_type == "application/json":
            data = await response.json()
            return data if isinstance(data, dict) else {"data": data}

        text = await response.text()
        return {"message": text} if text else {}
