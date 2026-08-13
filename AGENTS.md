# AGENTS.md

Project overview, pipeline, config, and module map live in `CLAUDE.md`; local-run and deploy
steps live in `README.md`. Read those first — this file only adds Cursor Cloud specifics.

## Cursor Cloud specific instructions

This is a **push-only CLI bot** (no server, no GUI, no web UI) — one Python process invoked by
Railway cron. "Running the app" means invoking `python -m bot` (see `bot/__main__.py`); demonstrate
it with `--dry-run`, which prints the digest instead of sending to Telegram/Slack.

- **Use the project venv:** run everything via `.venv/bin/python` (the startup update script builds
  it). There is **no linter** configured — only `pytest` and the `--dry-run` app run.
- **Tests are fully mocked / network-free and fast** (~0.4s for ~190 tests): `.venv/bin/python -m pytest`.
- **`--dry-run` still requires `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`** to be set (see
  `config.load_config`), even though nothing is sent — any dummy values work (e.g. `TELEGRAM_BOT_TOKEN=dummy TELEGRAM_CHAT_ID=-100`).
- **`STATE_DIR` defaults to `/data`** (the Railway volume), which doesn't exist here — set it to a
  writable temp dir for local runs, e.g. `STATE_DIR=/tmp/ir-state`.
- **Leave `OLLAMA_HOST` blank to disable all LLM calls** (curation, titles, summaries, translation).
  With no Ollama key available, this is the way to run end-to-end: themes fall back to top-by-stars
  and descriptions/titles use deterministic fallbacks. It still hits the **real** GitHub Search API
  (works unauthenticated at a low rate limit; set `GITHUB_TOKEN` to raise it).
- **Slot matching applies to dry runs too:** a theme only fires when the run's UTC `(weekday, hour)`
  matches one of its `at` slots (grid in `CLAUDE.md`). To force a specific theme regardless of the
  clock, copy `themes.toml`, strip the `at` lines, and pass `--themes <copy>` (per `CLAUDE.md`).

Example end-to-end dry run:

```bash
TELEGRAM_BOT_TOKEN=dummy TELEGRAM_CHAT_ID=-100 OLLAMA_HOST= \
  STATE_DIR=/tmp/ir-state .venv/bin/python -m bot --dry-run
```
