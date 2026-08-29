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
- **`--dry-run` does not require Telegram credentials.** `load_config(..., require_telegram=False)`
  fills dummy token/chat. Live sends still require `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.
  Force a slot with `--now 2026-08-28T13:00:00Z` or a single theme with `--theme trending`
  (strips `at`, so you do not need to copy `themes.toml`).
- **`STATE_DIR` defaults to `/data`** (the Railway volume), which doesn't exist here — set it to a
  writable temp dir for local runs, e.g. `STATE_DIR=/tmp/ir-state`.
- **Leave `OLLAMA_HOST` blank to disable all LLM calls** (curation, titles, summaries, translation).
  With no Ollama key available, this is the way to run end-to-end: themes fall back to top-by-stars
  and descriptions/titles use deterministic fallbacks. It still hits the **real** GitHub Search API
  (works unauthenticated at a low rate limit; set `GITHUB_TOKEN` to raise it).
- **Slot matching applies to dry runs too** unless you pass `--theme KEY` (strips `at`) or
  `--now` to pick a grid cell. Grid is in `CLAUDE.md`.

Example end-to-end dry run:

```bash
OLLAMA_HOST= STATE_DIR=/tmp/ir-state \
  .venv/bin/python -m bot --dry-run --theme trending
```
