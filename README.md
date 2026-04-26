# Discord API Chatter for Home Assistant

`Discord API Chatter` is a Home Assistant custom integration that talks to Discord through the **official Discord REST API** using **bot credentials only**.

It is designed for users who want a lightweight Discord bot integration with:

- ✅ config-flow setup from the Home Assistant UI
- ✅ `notify` support for simple notifications
- ✅ direct services to **send**, **edit**, and **delete** Discord messages
- ✅ persistent **stream tracker** support for Twitch-style sensors
- ✅ a built-in **test UI** for fake live / update / offline previews
- ✅ a PowerShell release helper in `scripts/release.ps1`

---

## Features

Unlike integrations that rely on user credentials, this one uses a Discord **bot token** and the normal REST endpoints only:

- `POST /channels/{channel_id}/messages`
- `PATCH /channels/{channel_id}/messages/{message_id}`
- `DELETE /channels/{channel_id}/messages/{message_id}`

That makes it suitable for:

- sending notifications from Home Assistant
- editing the same message over time
- deleting messages when needed
- keeping a single Discord post in sync with a live stream status

---

## Installation

### Manual installation

1. Copy `custom_components/discord_apichatter` into your Home Assistant `custom_components` directory.
2. Restart Home Assistant.
3. Open **Settings → Devices & Services**.
4. Click **Add Integration**.
5. Search for **Discord API Chatter**.
6. Enter your Discord **bot token** and optionally a **default channel ID**.

---

## Discord bot setup

1. Create a Discord application in the [Discord Developer Portal](https://discord.com/developers/applications).
2. Add a **Bot** to that application.
3. Copy the bot token.
4. Invite the bot to your server.
5. Give it permission to:
   - view the channel
   - send messages
   - embed links
   - manage messages
6. Copy the target Discord channel ID.

Reference: <https://www.home-assistant.io/integrations/discord>

> User credentials are **not** supported by this integration.

---

## Basic usage

### Send a message

```yaml
service: discord_apichatter.send_message
response_variable: discord_result
data:
  channel_id: "123456789012345678"
  message: "Hello from Home Assistant"
```

The response includes the created `message_id`.

### Edit a message

```yaml
service: discord_apichatter.edit_message
data:
  channel_id: "123456789012345678"
  message_id: "{{ discord_result.message_id }}"
  content: "Updated by Home Assistant"
```

### Delete a message

```yaml
service: discord_apichatter.delete_message
data:
  channel_id: "123456789012345678"
  message_id: "{{ discord_result.message_id }}"
```

### Use the `notify` platform

The notify service name is typically based on the connected bot name, for example `notify.my_discord_bot`.

```yaml
service: notify.my_discord_bot
data:
  message: "Motion detected at the front door"
  target:
    - "123456789012345678"
```

### Send embeds

```yaml
service: discord_apichatter.send_message
data:
  channel_id: "123456789012345678"
  message: ""
  embeds:
    - title: "Garage Alert"
      description: "Motion detected"
      color: 16711680
```

---

## Stream tracker UI

Twitch-style stream entities for this feature can be provided by [`ha_twitch_helix`](https://github.com/Radioh/ha_twitch_helix).

You can configure tracked stream messages directly from the Home Assistant UI:

1. Open **Settings → Devices & Services**.
2. Open **Discord API Chatter**.
3. Click **Configure**.
4. Choose **Add stream tracker**.
5. Pick your Twitch-style stream sensor and target Discord channel.

You can also manage reusable channel entries from the same Configure menu, then select those entries from tracker/test message dropdowns.

### Tracker behavior

For each tracked entity/channel pair:

- when the stream goes **live**, a new Discord message is posted
- when the **title** or **game/category** changes, that same message is edited
- when the stream goes **offline**, the same message is edited to its offline state
- the tracker includes independent switches to control image embeds for live, update, and offline events
- the integration remembers the `message_id` so updates continue across restarts
- template preview is available for live/update/offline messages before saving
- tracker health and copy-ready diagnostics are available from tracker actions

Multiple streamers and multiple channels are supported.

### Tracker templates

You can customize the live, update, and offline messages with Jinja templates.

Common template variables include:

| Variable | Meaning |
|---|---|
| `{{ name }}` | Streamer display name |
| `{{ title }}` | Current stream title |
| `{{ previous_title }}` | Previous title |
| `{{ title_changed }}` | Whether the title changed |
| `{{ game }}` | Current game/category |
| `{{ previous_game }}` | Previous game/category |
| `{{ game_changed }}` | Whether the game changed |
| `{{ viewers }}` | Viewer count |
| `{{ started_at }}` | Stream start timestamp |
| `{{ stream_duration_human }}` | Friendly duration like `2h 14m 08s` |
| `{{ stream_duration_seconds }}` | Raw duration in seconds |
| `{{ url }}` | Stream URL |
| `{{ thumbnail_url }}` / `{{ stream_picture }}` | Stream preview image |
| `{{ channel_picture }}` | Channel avatar |
| `{{ entity_id }}` | Home Assistant entity ID |
| `{{ tracker_id }}` | Internal tracker identifier |

If Twitch artwork is available, the integration automatically includes it as a Discord embed image.

---

## Test message UI

A built-in tester is available from the same **Configure** menu.

Use **`Test live/update/offline messages`** to send fake preview messages without waiting for a real stream event.

The tester lets you enter and remember:

- Discord `channel_id`
- fake entity ID
- streamer name
- title
- game/category
- viewer count
- `started_at`
- stream URL
- thumbnail URL
- channel avatar URL

### Why this is useful

The test UI remembers the last fake values **and** the last test `message_id`, so you can simulate a full lifecycle:

1. send a **live/start** message
2. reopen the tester
3. change the fake title or game
4. send an **update** message
5. send an **offline/stop** message

This makes it easy to preview formatting before using a real tracker.

---

## Configuration UI notes

- Home Assistant controls the config dialog header UI.
- Custom integrations cannot add or customize a header back arrow next to the close button.
- This integration uses in-flow menu steps/actions to navigate between tracker/channel pages.

---

## Advanced services

The UI covers normal setup, but the following services are also available for advanced use:

- `discord_apichatter.send_message`
- `discord_apichatter.edit_message`
- `discord_apichatter.delete_message`
- `discord_apichatter.track_stream`
- `discord_apichatter.untrack_stream`

See `custom_components/discord_apichatter/services.yaml` for the full field list.

---

## Troubleshooting

### Labels or translation text look missing

After updating the integration, Home Assistant or the browser may still show cached strings.

Try:

- closing and reopening the Configure dialog
- refreshing the page
- restarting Home Assistant if needed

### Messages are not sent

Check that:

- the bot token is valid
- the bot is in the server
- the channel ID is correct
- the bot has permission to send and manage messages

### Stream tracker does not update as expected

Check that your stream entity:

- exists in Home Assistant
- changes state between live/offline properly
- exposes attributes like `title`, `game`, `started_at`, and optionally artwork URLs

---

## Release helper

The repository includes `scripts/release.ps1` to update `manifest.json`, create a git tag, and optionally publish a GitHub release.

Release flow summary:

- validates branch and local working tree
- fetches remote tags from `origin` and checks for tag conflicts
- updates `manifest.json` version and creates a release commit
- pushes `main` to `origin`
- creates/pushes release tag
- creates GitHub release with `gh` (unless skipped)

You do not need to manually push first; the script performs push steps itself.

Examples:

```powershell
.\scripts\release.ps1 -DryRun
.\scripts\release.ps1 -BumpType patch
.\scripts\release.ps1 -BumpType minor -SkipGitHubRelease
```

---

## Notes

- Versioning is intended to be handled through `scripts/release.ps1`.
- The integration is currently focused on Discord bot messaging and Twitch-style stream update workflows.
- Integration icon/logo files can be included under `custom_components/discord_apichatter/brand`, but older Home Assistant versions may not display local custom-integration branding.

