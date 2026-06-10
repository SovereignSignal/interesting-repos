# Design: Failure-alert ping

- **Date:** 2026-06-08
- **Status:** Approved (brainstorming)

## 1. Summary

Make silent failures **visible**: when a run is degraded (Ollama unreachable/401),
fails to deliver a theme, or crashes outright, DM Sov on Telegram via an opt-in
`ALERT_CHAT_ID`. Mirrors the `doctor`/health pattern of the sibling bots. Closes the
project's recurring weakness — graceful degradation that hides outages (the 2026-06-06
dead-key incident hid for days).

## 2. Design

### 2.1 `bot/alerts.py` (new)
- `llm_reachable(host, model, api_key="", client=None) -> bool` — **pre-flight health
  check.** `True` when no host is configured (LLM not used → not a failure); otherwise
  `True` iff a one-shot `chat("ping")` returns non-empty. A 401/outage makes `chat`
  return `""` → `False`. One cheap call answers "is the brain reachable right now?".
- `send_alert(token, chat_id, text, client=None) -> bool` — DM a plain-text alert via
  `telegram.send_message(..., retries=1)`. **No-op (False)** when `chat_id` is unset;
  **never raises** (an alert failure must not break the run).

### 2.2 Config
`Config.alert_chat_id: str = ""`; `load_config` reads `ALERT_CHAT_ID`.

### 2.3 `main.run` — the two warnings
- Pre-flight: `degraded = (not dry_run) and bool(config.ollama_host) and not
  llm_reachable(config.ollama_host, config.ollama_model, config.ollama_api_key)`,
  computed at the top of `run`.
- After delivery (skipped under `--dry-run`):
  - `degraded` → alert: *"Ollama unreachable/unauthorized — this run is degraded
    (stars-only picks, no AI titles/blurbs/translation). Check OLLAMA_API_KEY in
    Railway."* — fires even when delivery otherwise succeeds (that's the silent case).
  - `failures > 0` → alert: *"N theme(s) failed to deliver this run."*

### 2.4 `bot/__main__.cli` — the crash
Wrap `run()` in `try/except`; on an unhandled exception send a crash alert
(`escape()`d) then re-raise (preserves the non-zero exit so Railway also flags it).

## 3. Out of scope
- Slack-channel alerting (Telegram DM chosen). Easy follow-on if wanted.
- Alerting on config/startup errors (no Telegram token yet to alert with).
- Mid-run LLM death after a healthy pre-flight (rare; the 401-key case is caught).

## 4. Testing (TDD; HTTP mocked)
- **`test_alerts.py`:** `llm_reachable` True without host / True on chat response /
  False on 401; `send_alert` no-op (no POST) without `chat_id` / sends to the chat /
  False (never raises) on send failure.
- **`test_config.py`:** `ALERT_CHAT_ID` read (present + default blank).
- **`test_main.py`:** `run` calls `send_alert` when `llm_reachable` is False (degraded);
  `run` calls `send_alert` on theme failures; existing tests unaffected (`alert_chat_id`
  unset → `send_alert` no-ops; `ollama_host` unset → no pre-flight).
- **`test_cli.py`:** `cli` sends a crash alert and re-raises when `run` throws.

## 5. File change map
| File | Change |
|---|---|
| `bot/alerts.py` | **New.** `llm_reachable` + `send_alert`. |
| `bot/config.py` | `Config.alert_chat_id`; `load_config` reads `ALERT_CHAT_ID`. |
| `bot/main.py` | pre-flight degraded check + two `send_alert` calls. |
| `bot/__main__.py` | crash alert around `run()`. |
| `.env.example` | document `ALERT_CHAT_ID`. |
| `tests/` | new `test_alerts.py`; extend `test_config`, `test_main`, `test_cli`. |
