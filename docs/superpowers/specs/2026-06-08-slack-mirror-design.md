# Design: Slack mirror

- **Date:** 2026-06-08
- **Status:** Approved (brainstorming)
- **Owner:** sov@sovereignsignal.com
- **Reference:** ports `modelbytes`' Slack pattern (`send_slack_post` / `_telegram_html_to_slack_mrkdwn`).

## 1. Summary

Mirror each published digest message to Slack via `chat.postMessage`, **opt-in** via
`SLACK_BOT_TOKEN` + `SLACK_CHANNEL_ID`. Dormant (no-op) when unset, so the working
Telegram-only deploy is unaffected. The Telegram-HTML the formatter already emits is
converted to Slack mrkdwn (converter ported from `modelbytes`).

## 2. Design

### 2.1 `bot/slack.py` (new)
- `_SlackMrkdwnConverter(HTMLParser)` + `html_to_mrkdwn(html) -> str` — ported from
  `modelbytes`: `<b>`/`<strong>` → `*`, `<i>`/`<em>` → `_`, `<code>`/`<pre>` → `` ` ``,
  `<br>` → newline, `<a href="u">label</a>` → `<u|label>`. Re-escapes `&`/`<`/`>` for
  Slack and collapses 3+ blank lines. (Our headings `<a href><b>Title</b></a>` become
  `*<url|Title>*` — a bold link.)
- `send_slack_message(token, channel, html, client=None) -> bool` — POST to
  `https://slack.com/api/chat.postMessage` with `Authorization: Bearer <token>` and
  `json={"channel": channel, "text": html_to_mrkdwn(html)[:39000], "unfurl_links":
  False, "unfurl_media": False}`. Returns **False (no-op)** when `token`/`channel` is
  unset, and False on any error or `ok != true`. **Never raises** — Telegram is the
  primary channel. Uses `httpx` (consistent with the rest of the bot).

### 2.2 Config
`Config` gains `slack_bot_token: str = ""` and `slack_channel_id: str = ""`;
`load_config` reads `SLACK_BOT_TOKEN` / `SLACK_CHANNEL_ID` (default `""`).

### 2.3 `main` wiring
In the Phase-2 send loop, immediately after the Telegram `send_message(...)`, add
`send_slack_message(config.slack_bot_token, config.slack_channel_id, m)` — mirrors the
same message to Slack; a no-op when Slack is unconfigured. `--dry-run` is unchanged
(it prints and never sends to either channel). The 20s throttle continues to pace the
loop, so both channels get the staggered cadence.

### 2.4 `.env.example`
Document `SLACK_BOT_TOKEN` + `SLACK_CHANNEL_ID` as optional (unset = Telegram-only).

## 3. Out of scope
- Slack Block Kit / attachments — plain mrkdwn text mirror only.
- Multiple Slack channels / per-theme routing — one channel.

## 4. Testing (TDD; HTTP mocked)
- **`test_slack.py` (new):** `html_to_mrkdwn` (bold, link, entity escaping);
  `send_slack_message` dormant → `False` and **no POST** when creds unset; sends the
  converted mrkdwn to the channel and returns `True` on `{"ok": true}`; returns `False`
  on `{"ok": false}`; returns `False` (never raises) on a transport error.
- **`test_config.py`:** `SLACK_BOT_TOKEN` / `SLACK_CHANNEL_ID` read (present + default).
- **`test_main.py`:** the send loop calls `send_slack_message` once per delivered
  message; with Slack unconfigured it's a no-op (existing tests unaffected — no network).

## 5. File change map
| File | Change |
|---|---|
| `bot/slack.py` | **New.** `html_to_mrkdwn` + `send_slack_message`. |
| `bot/config.py` | `Config.slack_bot_token` / `slack_channel_id`; `load_config` reads them. |
| `bot/main.py` | Mirror each sent message to Slack in the Phase-2 loop. |
| `.env.example` | Document the two optional Slack vars. |
| `tests/` | New `test_slack.py`; extend `test_config`, `test_main`. |
