# Interesting Repos → Telegram

Weekly digest of trending GitHub repos, by theme, posted to a Telegram group.
Push-only: a script run once a week by Railway cron. No server.

## How it works
Discover (GitHub Search API) → rank (top-by-stars, or Claude for `rank=llm` themes)
→ format (repo's own description, README-first-line fallback) → post one message per
theme → remember sent repos so nothing repeats. See `docs/superpowers/specs/` and
`docs/superpowers/plans/` for the full design.

## Themes
Edit `themes.toml`. Each `[[theme]]` is one message. `query` uses GitHub Search
qualifiers; `{since:Nd}` expands to N days ago. Sorting is the `sort` field, not a
`sort:` qualifier. `rank = "llm"` ranks candidates against `profile` via Claude
(needs `ANTHROPIC_API_KEY`); default `rank = "stars"` needs no AI.

## Setup
1. Create a bot with [@BotFather](https://t.me/BotFather) → `TELEGRAM_BOT_TOKEN`.
2. Add the bot to your group. Get the group's `chat_id`: add
   [@RawDataBot](https://t.me/RawDataBot) to the group (or call
   `getUpdates`), read the negative `chat.id` → `TELEGRAM_CHAT_ID`, then remove it.
3. (Recommended) Create a GitHub personal access token (no scopes needed for public
   search) → `GITHUB_TOKEN`.

## Run locally
```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env   # fill in values, then export them
pytest -q              # run the tests
python -m bot --dry-run   # print the digest instead of sending
```

## Deploy on Railway
1. New project from this repo.
2. Set the env vars from `.env.example` in the service Variables.
3. Add a **Volume** mounted at `/data` (holds `state.json` for dedup).
4. The service runs on the cron in `railway.json` (`0 13 * * 1` = Mon 13:00 UTC).
   Adjust as desired.
5. Tip: trigger one run and watch logs; or temporarily set the start command to
   `python -m bot --dry-run` for a safe first run.
