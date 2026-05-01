"""Embedded configuration panel for Discord API Chatter."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from aiohttp import web

from homeassistant.components.http import HomeAssistantView
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
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
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

PANEL_URL_PATH = "discord-apichatter-config"
PANEL_WEB_URL = "/api/discord_apichatter/panel"
PANEL_CONFIG_URL = "/api/discord_apichatter/panel/config"
PANEL_SAVE_URL = "/api/discord_apichatter/panel/save"

# Discord snowflake IDs are numeric strings of 17–20 digits.
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


def _get_domain_entries(hass: HomeAssistant) -> list[ConfigEntry]:
    """Return all config entries for this integration domain."""
    return list(hass.config_entries.async_entries(DOMAIN))


def _serialize_entry(entry: ConfigEntry) -> dict[str, Any]:
    """Serialize a config entry for panel API responses.

    Reads from both entry.data and entry.options so that configurations
    written by older versions of the integration are surfaced correctly.
    """
    data = entry.data or {}
    options = entry.options or {}

    # default_channel lives in entry.data (current schema).
    # Older installs may have placed it in entry.options — accept both.
    default_channel = (
        data.get(CONF_DEFAULT_CHANNEL)
        or options.get(CONF_DEFAULT_CHANNEL)
        or ""
    )

    # channels / trackers / test_message live in entry.options (current schema).
    # Older installs may have stored them in entry.data — fall back gracefully.
    channels    = options.get(CONF_CHANNELS)     or data.get(CONF_CHANNELS)     or []
    trackers    = options.get(CONF_TRACKERS)     or data.get(CONF_TRACKERS)     or []
    test_message = options.get(CONF_TEST_MESSAGE) or data.get(CONF_TEST_MESSAGE) or {}

    return {
        "entry_id":       entry.entry_id,
        "title":          entry.title,
        "default_channel": default_channel,
        "channels":       channels,
        "trackers":       trackers,
        "test_message":   test_message,
    }


def _find_entry_by_id(hass: HomeAssistant, entry_id: str) -> ConfigEntry | None:
    """Find a config entry by entry_id for this domain."""
    for entry in _get_domain_entries(hass):
        if entry.entry_id == entry_id:
            return entry
    return None


# ---------------------------------------------------------------------------
# Panel HTML (module-level constant; placeholder URLs substituted at serve time)
# ---------------------------------------------------------------------------

_PANEL_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <meta http-equiv="Content-Security-Policy"
        content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self';" />
  <title>Discord API Chatter Config</title>
  <style>
    :root {
      --bg: #f6f7fb; --card: #fff; --text: #1f2937; --muted: #6b7280;
      --line: #e5e7eb; --accent: #5865f2; --accent-hover: #4c59da;
      --danger: #dc2626; --warn: #d97706; --ok-color: #166534; --radius: 14px;
    }
    *, *::before, *::after { box-sizing: border-box; }
    body {
      margin: 0;
      background: radial-gradient(circle at 10% 0%, #eef2ff 0%, var(--bg) 60%);
      color: var(--text);
      font-family: Segoe UI, system-ui, -apple-system, sans-serif;
      font-size: 14px;
    }
    .wrap { max-width: 1080px; margin: 28px auto; padding: 0 16px 48px; }
    .card {
      background: var(--card); border: 1px solid var(--line);
      border-radius: var(--radius); padding: 18px 20px;
      box-shadow: 0 4px 14px rgba(17,24,39,.06); margin-bottom: 14px;
    }
    details.card > summary {
      display: flex; align-items: center; justify-content: space-between;
      cursor: pointer; list-style: none; user-select: none;
    }
    details.card > summary::-webkit-details-marker { display: none; }
    details.card[open] > summary { margin-bottom: 14px; }
    details.card > summary > .sum-title { font-weight: 600; font-size: .95rem; }
    h1 { margin: 0 0 6px; font-size: 1.4rem; }
    h2 { margin: 0 0 10px; font-size: 1rem; font-weight: 700; }
    p.muted { color: var(--muted); font-size: .88rem; margin: 0; }
    label { display: block; margin: 12px 0 5px; font-weight: 600; font-size: .88rem; }
    input, select, textarea, button { font: inherit; }
    input[type="text"], select {
      width: 100%; border: 1px solid #d1d5db; border-radius: 9px;
      padding: 8px 11px; background: #fff; transition: border-color .15s;
    }
    input[type="text"]:focus, select:focus {
      outline: none; border-color: var(--accent);
      box-shadow: 0 0 0 3px rgba(88,101,242,.15);
    }
    input.field-invalid { border-color: var(--danger) !important; }
    textarea {
      width: 100%; min-height: 160px; resize: vertical;
      border: 1px solid #d1d5db; border-radius: 9px; padding: 9px 11px;
      background: #fff; font-family: Consolas, ui-monospace, monospace;
      font-size: .82rem; line-height: 1.5; transition: border-color .15s;
    }
    textarea:focus {
      outline: none; border-color: var(--accent);
      box-shadow: 0 0 0 3px rgba(88,101,242,.15);
    }
    textarea.field-invalid { border-color: var(--danger) !important; }
    button {
      border: 0; border-radius: 9px; padding: 8px 14px; cursor: pointer;
      font-weight: 500; transition: background .15s, opacity .15s;
    }
    button:disabled { opacity: .45; cursor: default; }
    .btn-primary { background: var(--accent); color: #fff; }
    .btn-primary:hover:not(:disabled) { background: var(--accent-hover); }
    .btn-ghost { background: #f3f4f6; color: #111; }
    .btn-ghost:hover:not(:disabled) { background: #e5e7eb; }
    .btn-danger { background: #fee2e2; color: var(--danger); }
    .btn-danger:hover:not(:disabled) { background: #fecaca; }
    .btn-sm { padding: 4px 10px; font-size: .81rem; border-radius: 7px; }
    .row { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; margin-top: 10px; }
    .sec-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 10px; }
    .sec-header h2 { margin: 0; }
    .sec-actions { display: flex; gap: 6px; align-items: center; }
    .badge { display: inline-block; padding: 3px 10px; border-radius: 20px; font-size: .78rem; font-weight: 600; }
    .badge-warn { background: #fef3c7; color: var(--warn); }
    .field-err { display: block; color: var(--danger); font-size: .82rem; margin-top: 4px; min-height: 1rem; }
    .status { margin-top: 10px; min-height: 1.1rem; font-size: .88rem; }
    .s-ok   { color: var(--ok-color); }
    .s-err  { color: var(--danger); }
    .s-warn { color: var(--warn); }
    /* Channel table */
    .ch-table { width: 100%; border-collapse: collapse; margin-bottom: 12px; }
    .ch-table th {
      text-align: left; padding: 7px 10px; font-size: .8rem; font-weight: 600;
      background: #f9fafb; border-bottom: 1px solid var(--line);
      color: var(--muted); text-transform: uppercase; letter-spacing: .04em;
    }
    .ch-table td { padding: 7px 10px; border-bottom: 1px solid var(--line); vertical-align: middle; }
    .ch-table tr:last-child td { border-bottom: none; }
    .ch-table .empty { text-align: center; color: var(--muted); font-style: italic; padding: 20px; }
    .ch-table .mono { font-family: Consolas, monospace; font-size: .83rem; }
    .ch-table .tbl-actions { display: flex; gap: 6px; justify-content: flex-end; }
    .add-ch-form { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 8px; align-items: flex-start; }
    .add-ch-form input { flex: 1 1 180px; }
    .add-ch-form button { flex-shrink: 0; align-self: center; }
    .schema-hint {
      background: #f8fafc; border: 1px solid var(--line); border-radius: 9px;
      padding: 10px 14px; font-family: Consolas, monospace; font-size: .78rem;
      line-height: 1.5; color: #374151; white-space: pre-wrap; margin-bottom: 10px; overflow-x: auto;
    }
    .hdr-row { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 10px; }
  </style>
</head>
<body>
  <div class="wrap">
    <div class="card hdr-row">
      <div>
        <h1>Discord API Chatter</h1>
        <p class="muted">Embedded configuration editor &mdash; channels, trackers, and test defaults.</p>
      </div>
      <span id="dirtyBadge" class="badge badge-warn" hidden>Unsaved changes</span>
    </div>

    <div class="card">
      <label for="entrySelect">Integration Entry</label>
      <select id="entrySelect"></select>
      <label for="defaultChannel">Default Channel ID</label>
      <input id="defaultChannel" type="text" placeholder="e.g. 123456789012345678"
             autocomplete="off" spellcheck="false" />
      <p class="muted" style="font-size:.83rem;margin-top:5px;">
        17&ndash;20 digit Discord snowflake. Leave blank to require an explicit channel per request.
      </p>
      <span id="defChErr" class="field-err"></span>
    </div>

    <div class="card">
      <h2>Channel Entries</h2>
      <p class="muted" style="font-size:.83rem;margin-bottom:12px;">
        Named shortcuts for channels used by services and stream trackers.
        Channel IDs must be Discord snowflakes (17&ndash;20 digits, numbers only).
      </p>
      <table class="ch-table">
        <thead><tr><th>Channel ID</th><th>Friendly Name</th><th></th></tr></thead>
        <tbody id="chTbody"></tbody>
      </table>
      <div class="add-ch-form">
        <input id="newChId"   type="text" placeholder="Channel ID (e.g. 123456789012345678)"
               autocomplete="off" spellcheck="false" />
        <input id="newChName" type="text" placeholder="Friendly name (e.g. stream-updates)"
               autocomplete="off" />
        <button class="btn-primary btn-sm" id="addChBtn" type="button">+ Add Channel</button>
      </div>
      <span id="chErr" class="field-err"></span>
    </div>

    <div class="card">
      <div class="sec-header">
        <h2>Trackers (JSON array)</h2>
        <div class="sec-actions">
          <button class="btn-ghost btn-sm" id="fmtTrackersBtn" type="button">Format</button>
          <button class="btn-ghost btn-sm" id="valTrackersBtn" type="button">Validate</button>
          <button class="btn-ghost btn-sm" id="schemaTrackersBtn" type="button">Schema &#9658;</button>
        </div>
      </div>
      <pre class="schema-hint" id="trackerSchemaHint" hidden>[
  {
    "tracker_id": "my_tracker",           // unique identifier (required)
    "entity_id":  "sensor.channel123",    // Home Assistant sensor entity (required)
    "channel_id": "",                     // overrides default channel (optional)
    "live_template":    "...",            // Jinja2 template (optional)
    "update_template":  "...",            // Jinja2 template (optional)
    "offline_template": "...",            // Jinja2 template (optional)
    "send_live_image":    true,
    "send_update_image":  true,
    "send_offline_image": true,
    "update_on_title_change": true,
    "update_on_game_change":  true
  }
]</pre>
      <textarea id="trackersJson" spellcheck="false" placeholder="[]"></textarea>
      <span id="trackersErr" class="field-err"></span>
    </div>

    <details class="card">
      <summary>
        <span class="sum-title">Test Message Defaults (JSON object)</span>
        <div class="sec-actions">
          <button class="btn-ghost btn-sm" id="fmtTestBtn" type="button"
                  onclick="event.stopPropagation()">Format</button>
          <button class="btn-ghost btn-sm" id="valTestBtn" type="button"
                  onclick="event.stopPropagation()">Validate</button>
        </div>
      </summary>
      <p class="muted" style="font-size:.83rem;margin-bottom:10px;">
        Optional remembered defaults for the built-in test message flow in the HA options dialog.
      </p>
      <textarea id="testMessageJson" spellcheck="false" placeholder="{}"></textarea>
      <span id="testErr" class="field-err"></span>
    </details>

    <div class="card">
      <div class="row">
        <button class="btn-ghost" id="reloadBtn" type="button">&#8635; Reload</button>
        <button class="btn-primary" id="saveBtn" type="button">Save All</button>
      </div>
      <div id="statusEl" class="status"></div>
    </div>
  </div>

  <script>
    'use strict';

    const CONFIG_URL   = '__PANEL_CONFIG_URL__';
    const SAVE_URL     = '__PANEL_SAVE_URL__';
    const SNOWFLAKE_RE = /^\\d{17,20}$/;

    const state = { entries: [], selectedEntryId: null, channels: [], dirty: false };

    const $ = (id) => document.getElementById(id);
    const entrySelEl = $('entrySelect'), defChIn = $('defaultChannel'),
          defChErr = $('defChErr'), chTbody = $('chTbody'),
          newChIdEl = $('newChId'), newChNameEl = $('newChName'),
          chErrEl = $('chErr'), trackersEl = $('trackersJson'),
          trackersErrEl = $('trackersErr'), testMsgEl = $('testMessageJson'),
          testErrEl = $('testErr'), statusEl = $('statusEl'),
          dirtyBadge = $('dirtyBadge'), saveBtnEl = $('saveBtn');

    function esc(s) {
      return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;')
        .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
    }
    function isSnowflake(id) { return SNOWFLAKE_RE.test(id); }
    function setStatus(msg, cls) { statusEl.textContent = msg; statusEl.className = 'status ' + (cls || 's-ok'); }
    function clearStatus() { statusEl.textContent = ''; statusEl.className = 'status'; }
    function setFieldErr(el, msg) { el.textContent = msg || ''; }
    function clearFieldErr(el)    { el.textContent = ''; }
    function markDirty()  { state.dirty = true;  dirtyBadge.hidden = false; }
    function clearDirty() { state.dirty = false; dirtyBadge.hidden = true;  }

    function tryFormatJson(str, defaultVal) {
      try   { return JSON.stringify(JSON.parse(str), null, 2); }
      catch { return defaultVal !== undefined ? defaultVal : str; }
    }
    function validateJsonTextarea(textarea, errEl, kind) {
      const fallback = kind === 'array' ? '[]' : '{}';
      try {
        JSON.parse(textarea.value.trim() || fallback);
        textarea.classList.remove('field-invalid'); clearFieldErr(errEl); return true;
      } catch (e) {
        textarea.classList.add('field-invalid'); setFieldErr(errEl, 'JSON error: ' + e.message); return false;
      }
    }

    // Channel table
    function renderChannels() {
      chTbody.innerHTML = '';
      if (!state.channels.length) {
        chTbody.innerHTML = '<tr><td colspan="3" class="empty">No channels configured &mdash; use the form below to add one.</td></tr>';
        return;
      }
      state.channels.forEach((ch, idx) => {
        const tr = document.createElement('tr');
        tr.innerHTML =
          '<td class="mono">' + esc(ch.channel_id) + '</td>' +
          '<td>' + esc(ch.channel_name) + '</td>' +
          '<td class="tbl-actions">' +
            '<button class="btn-ghost btn-sm" onclick="startEditChannel(' + idx + ')">Edit</button>' +
            '<button class="btn-danger btn-sm" onclick="removeChannel(' + idx + ')">Remove</button>' +
          '</td>';
        chTbody.appendChild(tr);
      });
    }
    function addChannel() {
      clearFieldErr(chErrEl);
      newChIdEl.classList.remove('field-invalid');
      newChNameEl.classList.remove('field-invalid');
      const id = newChIdEl.value.trim(), name = newChNameEl.value.trim();
      if (!id || !name) {
        setFieldErr(chErrEl, 'Both Channel ID and a friendly name are required.');
        if (!id)   newChIdEl.classList.add('field-invalid');
        if (!name) newChNameEl.classList.add('field-invalid');
        return;
      }
      if (!isSnowflake(id)) {
        setFieldErr(chErrEl, 'Channel ID must be a 17\u201320 digit Discord snowflake (numbers only).');
        newChIdEl.classList.add('field-invalid'); return;
      }
      if (state.channels.some((c) => c.channel_id === id)) {
        setFieldErr(chErrEl, 'A channel with ID "' + esc(id) + '" already exists.');
        newChIdEl.classList.add('field-invalid'); return;
      }
      state.channels.push({ channel_id: id, channel_name: name });
      newChIdEl.value = ''; newChNameEl.value = '';
      renderChannels(); markDirty();
    }
    function removeChannel(idx) { state.channels.splice(idx, 1); renderChannels(); markDirty(); }
    function startEditChannel(idx) {
      const ch = state.channels[idx];
      newChIdEl.value = ch.channel_id; newChNameEl.value = ch.channel_name;
      state.channels.splice(idx, 1); renderChannels();
      clearFieldErr(chErrEl); newChIdEl.focus(); markDirty();
    }

    // JSON editor helpers
    $('fmtTrackersBtn').addEventListener('click', () => {
      const fmt = tryFormatJson(trackersEl.value, null);
      if (fmt !== null) { trackersEl.value = fmt; trackersEl.classList.remove('field-invalid'); clearFieldErr(trackersErrEl); markDirty(); }
    });
    $('valTrackersBtn').addEventListener('click', () => {
      if (validateJsonTextarea(trackersEl, trackersErrEl, 'array')) setStatus('Trackers JSON is valid.', 's-ok');
    });
    $('schemaTrackersBtn').addEventListener('click', () => {
      const hint = $('trackerSchemaHint'); hint.hidden = !hint.hidden;
      $('schemaTrackersBtn').textContent = hint.hidden ? 'Schema \u25B8' : 'Schema \u25BE';
    });
    $('fmtTestBtn').addEventListener('click', (e) => {
      e.stopPropagation();
      const fmt = tryFormatJson(testMsgEl.value, null);
      if (fmt !== null) { testMsgEl.value = fmt; testMsgEl.classList.remove('field-invalid'); clearFieldErr(testErrEl); markDirty(); }
    });
    $('valTestBtn').addEventListener('click', (e) => {
      e.stopPropagation();
      if (validateJsonTextarea(testMsgEl, testErrEl, 'object')) setStatus('Test message JSON is valid.', 's-ok');
    });

    // Entry management
    function getSelectedEntry() { return state.entries.find((e) => e.entry_id === state.selectedEntryId); }
    function populateFromEntry(entry) {
      defChIn.value = entry ? (entry.default_channel || '') : '';
      defChIn.classList.remove('field-invalid'); clearFieldErr(defChErr);
      state.channels = entry ? (entry.channels || []).map((c) => Object.assign({}, c)) : [];
      renderChannels();
      trackersEl.value = entry ? JSON.stringify(entry.trackers || [], null, 2) : '[]';
      trackersEl.classList.remove('field-invalid'); clearFieldErr(trackersErrEl);
      testMsgEl.value = entry ? JSON.stringify(entry.test_message || {}, null, 2) : '{}';
      testMsgEl.classList.remove('field-invalid'); clearFieldErr(testErrEl);
    }
    function renderEntrySelector() {
      entrySelEl.innerHTML = '';
      for (const entry of state.entries) {
        const opt = document.createElement('option');
        opt.value = entry.entry_id;
        opt.textContent = (entry.title || 'Discord API Chatter') + ' (' + entry.entry_id.slice(0, 8) + '\u2026)';
        entrySelEl.appendChild(opt);
      }
      if (!state.selectedEntryId && state.entries.length) state.selectedEntryId = state.entries[0].entry_id;
      entrySelEl.value = state.selectedEntryId || '';
      populateFromEntry(getSelectedEntry());
    }
    entrySelEl.addEventListener('change', () => {
      state.selectedEntryId = entrySelEl.value;
      populateFromEntry(getSelectedEntry()); clearDirty(); clearStatus();
    });

    // Load
    async function loadConfig() {
      clearStatus(); setStatus('Loading\u2026', 's-warn');
      const res = await fetch(CONFIG_URL, { credentials: 'same-origin' });
      if (!res.ok) throw new Error('Load failed (' + res.status + ')');
      const payload = await res.json();
      state.entries = payload.entries || [];
      if (!state.entries.length) {
        state.selectedEntryId = null; renderEntrySelector();
        setStatus('No Discord API Chatter entries found. Add one via Settings \u2192 Integrations.', 's-warn');
        return;
      }
      const keepSelected = state.entries.some((e) => e.entry_id === state.selectedEntryId);
      if (!keepSelected) state.selectedEntryId = state.entries[0].entry_id;
      renderEntrySelector(); clearDirty(); setStatus('Configuration loaded.', 's-ok');
    }

    // Save
    async function saveConfig() {
      const entry = getSelectedEntry();
      if (!entry) { setStatus('No entry selected.', 's-err'); return; }

      let valid = true;
      clearFieldErr(defChErr); defChIn.classList.remove('field-invalid');
      const defCh = defChIn.value.trim();
      if (defCh && !isSnowflake(defCh)) {
        setFieldErr(defChErr, 'Must be a 17\u201320 digit Discord snowflake (numbers only).');
        defChIn.classList.add('field-invalid'); valid = false;
      }
      clearFieldErr(chErrEl);
      for (const ch of state.channels) {
        if (!isSnowflake(ch.channel_id)) {
          setFieldErr(chErrEl, 'Channel ID "' + esc(ch.channel_id) + '" is not a valid Discord snowflake. Edit or remove it before saving.');
          valid = false; break;
        }
      }
      if (!validateJsonTextarea(trackersEl,  trackersErrEl, 'array'))  valid = false;
      if (!validateJsonTextarea(testMsgEl,   testErrEl,     'object')) valid = false;
      if (!valid) { setStatus('Please fix the errors above before saving.', 's-err'); return; }

      let parsedTrackers, parsedTestMessage;
      try   { parsedTrackers    = JSON.parse(trackersEl.value.trim()  || '[]'); }
      catch (e) { setStatus('Trackers JSON: ' + e.message, 's-err'); return; }
      try   { parsedTestMessage = JSON.parse(testMsgEl.value.trim()   || '{}'); }
      catch (e) { setStatus('Test message JSON: ' + e.message, 's-err'); return; }

      saveBtnEl.disabled = true;
      setStatus('Saving\u2026', 's-warn');
      try {
        const res = await fetch(SAVE_URL, {
          method: 'POST', credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            entry_id: entry.entry_id, default_channel: defCh,
            channels: state.channels, trackers: parsedTrackers, test_message: parsedTestMessage,
          }),
        });
        const payload = await res.json().catch(() => ({}));
        if (!res.ok) { setStatus(payload.message || payload.error || 'Save failed (' + res.status + ')', 's-err'); return; }
        await loadConfig();
        setStatus('Saved successfully. Home Assistant will reload this integration entry.', 's-ok');
      } finally { saveBtnEl.disabled = false; }
    }

    // Unsaved-changes guard
    window.addEventListener('beforeunload', (e) => { if (state.dirty) { e.preventDefault(); e.returnValue = ''; } });
    [trackersEl, testMsgEl].forEach((el) => el.addEventListener('input', markDirty));
    defChIn.addEventListener('input', () => {
      markDirty();
      const v = defChIn.value.trim();
      if (v && !isSnowflake(v)) { setFieldErr(defChErr, 'Must be 17\u201320 digits (numbers only).'); defChIn.classList.add('field-invalid'); }
      else { clearFieldErr(defChErr); defChIn.classList.remove('field-invalid'); }
    });

    $('addChBtn').addEventListener('click', addChannel);
    newChNameEl.addEventListener('keydown', (e) => { if (e.key === 'Enter') addChannel(); });
    newChIdEl.addEventListener('keydown',   (e) => { if (e.key === 'Enter') newChNameEl.focus(); });
    $('reloadBtn').addEventListener('click', () => {
      if (state.dirty && !confirm('You have unsaved changes. Reload and discard them?')) return;
      loadConfig().catch((e) => setStatus(e.message, 's-err'));
    });
    $('saveBtn').addEventListener('click', () => { saveConfig().catch((e) => setStatus(e.message, 's-err')); });

    loadConfig().catch((e) => setStatus(e.message, 's-err'));
  </script>
</body>
</html>"""


class DiscordApiChatterPanelView(HomeAssistantView):
    """Serve the embedded panel HTML."""

    url = PANEL_WEB_URL
    name = "api:discord_apichatter:panel"
    # The iframe shell itself must be public in some HA versions because
    # iframe requests do not carry the authenticated frontend context.
    # Sensitive operations remain protected in config/save views.
    requires_auth = False
    requires_admin = False

    async def get(self, request: web.Request) -> web.Response:
        """Return panel HTML with security headers."""
        html = _PANEL_HTML.replace("__PANEL_CONFIG_URL__", PANEL_CONFIG_URL).replace(
            "__PANEL_SAVE_URL__", PANEL_SAVE_URL
        )
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
        entries = [_serialize_entry(entry) for entry in _get_domain_entries(hass)]
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


async def async_setup_panel(hass: HomeAssistant) -> None:
    """Register panel views and sidebar item."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    if domain_data.get("panel_registered"):
        return

    hass.http.register_view(DiscordApiChatterPanelView())
    hass.http.register_view(DiscordApiChatterPanelConfigView())
    hass.http.register_view(DiscordApiChatterPanelSaveView())

    frontend_component = getattr(getattr(hass, "components", None), "frontend", None)
    if frontend_component is not None:
        common_kwargs = {
            "component_name": "iframe",
            "sidebar_title": "Discord API Chatter",
            "frontend_url_path": PANEL_URL_PATH,
            "config": {"url": PANEL_WEB_URL},
            "require_admin": True,
        }
        try:
            frontend_component.async_register_built_in_panel(
                **common_kwargs,
                icon="mdi:discord",
            )
        except TypeError:
            frontend_component.async_register_built_in_panel(
                **common_kwargs,
                sidebar_icon="mdi:discord",
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
        try:
            frontend_module.async_register_built_in_panel(
                hass,
                **common_kwargs,
                icon="mdi:discord",
            )
        except TypeError:
            frontend_module.async_register_built_in_panel(
                hass,
                **common_kwargs,
                sidebar_icon="mdi:discord",
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
