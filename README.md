# Discord API Chatter for Home Assistant

A custom Home Assistant integration that talks to Discord through the **official Discord REST API** using **bot credentials only**.

It provides:

- a config-flow based Discord bot setup
- a standard `notify` service for sending messages
- direct REST-backed services to **send**, **edit**, and **delete** messages
- persistent **tracked stream updates** for Twitch-style stream sensors
- a release helper script under `scripts/release.ps1`

## What makes this different

This integration does **not** use user credentials.
It uses a Discord **bot token** and the standard REST endpoints:

- `POST /channels/{channel_id}/messages`
- `PATCH /channels/{channel_id}/messages/{message_id}`
- `DELETE /channels/{channel_id}/messages/{message_id}`

## Installation

1. Copy `custom_components/discord_apichatter` into your Home Assistant `custom_components` folder.
2. Restart Home Assistant.
3. Go to **Settings → Devices & Services → Add Integration**.
4. Add **Discord API Chatter**.
5. Paste your Discord **bot token** and optionally a **default channel ID**.

## Discord bot setup

This follows the same general Discord bot setup flow as the official integration:

- create a Discord application
- create a bot user
- invite it to your server
- grant it permission to send/manage messages in the target channel
- copy the bot token and channel ID

Reference: <https://www.home-assistant.io/integrations/discord>

## Usage

### 1) Send a message via the integration service

```yaml
service: discord_apichatter.send_message
response_variable: discord_result
data:
  channel_id: "123456789012345678"
  message: "Hello from Home Assistant"
```

Returned response data includes the created `message_id`.

### 2) Edit a message

```yaml
service: discord_apichatter.edit_message
data:
  channel_id: "123456789012345678"
  message_id: "{{ discord_result.message_id }}"
  content: "Updated by Home Assistant"
```

### 3) Delete a message

```yaml
service: discord_apichatter.delete_message
data:
  channel_id: "123456789012345678"
  message_id: "{{ discord_result.message_id }}"
```

### 4) Use the notify service

The notify service name is typically based on the connected bot name, for example `notify.my_discord_bot`.

```yaml
service: notify.my_discord_bot
data:
  message: "Motion detected at the front door"
  target:
    - "123456789012345678"
```

You can also send embeds:

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

## Automatic Twitch stream tracking

You can now configure stream trackers directly from the Home Assistant UI:

1. Open **Settings → Devices & Services**.
2. Open **Discord API Chatter**.
3. Select **Configure**.
4. Choose **Add stream tracker**.
5. Pick your Twitch Helix entity and the Discord channel.

Behavior:
- when the stream goes **live**, a **new** Discord message is posted
- when the **title** or **game/category** changes, that same Discord message is **edited**
- when the stream goes **offline**, the same Discord message is **edited** to show offline status
- each tracked streamer/channel pair keeps its own saved `message_id`, so multiple streamers are supported

The UI also lets you customize the live/update/offline templates with variables such as:
- `{{ name }}`
- `{{ title }}`
- `{{ game }}`
- `{{ viewers }}`
- `{{ started_at }}`
- `{{ stream_duration_human }}`
- `{{ stream_duration_seconds }}`
- `{{ url }}`
- `{{ thumbnail_url }}`
- `{{ stream_picture }}`
- `{{ channel_picture }}`

When Twitch provides artwork, the tracker also attaches the stream thumbnail automatically as a Discord embed image, and it now calculates how long the stream has been live for update/offline messages.

The `track_stream` / `untrack_stream` services still exist for advanced use, but they are no longer required for normal setup.

## Release helper

Use the included PowerShell script to bump `manifest.json`, create a git tag, and optionally publish a GitHub release:

```powershell
.\scripts\release.ps1 -DryRun
.\scripts\release.ps1 -BumpType patch
```
