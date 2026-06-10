# Interesting Repos → Telegram

Digest of trending GitHub repos, by theme, posted to a Telegram group.
Push-only: a script run by Railway cron three times daily. No server.

## How it works
Discover (GitHub Search API "breakout" queries: recently-created, high-star) →
curate (an Ollama model picks the most interesting and filters spam/star-farms for
`rank=llm` themes; else top-by-stars) → format (repo's own description,
README-first-line fallback, non-English text translated to English via Ollama) →
post one message per theme → remember sent repos so nothing repeats. See
`docs/superpowers/specs/` and `docs/superpowers/plans/` for the full design.

## Themes
Edit `themes.toml`. Each `[[theme]]` is one message. `query` uses GitHub Search
qualifiers; `{since:Nd}` expands to N days ago. Sorting is the `sort` field, not a
`sort:` qualifier. `rank = "llm"` has an Ollama model curate candidates against the
theme's `profile` (filtering spam/star-farmed/low-effort repos); `rank = "stars"`
just takes the top by stars (no AI). LLM curation needs `OLLAMA_*` set (below).
`at` is a list of `"weekday HH"` slots (e.g. `at = ["mon 13", "thu 16"]`); a theme
only fires when the current run's UTC weekday and hour match one of its slots. A run
where no theme matches sends nothing.

## Translation
Descriptions in non-Latin scripts (Chinese, Japanese, Korean, Cyrillic, Arabic, …)
are auto-translated to English via an **Ollama** chat model (default `gemma3:12b`).
Use Ollama Cloud (`OLLAMA_HOST=https://ollama.com` + `OLLAMA_API_KEY` from
ollama.com) or a local server (`OLLAMA_HOST=http://localhost:11434`, no key).
Leave `OLLAMA_HOST` blank to disable. English/Latin text is never sent to the model,
and any translation error falls back to the original text.
`OLLAMA_CURATOR_MODEL` — optional stronger model for curation scoring and summaries;
defaults to `OLLAMA_MODEL`.

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
4. The service runs on the cron in `railway.json` (`0 13,16,19 * * *` = three times
   daily at 13:00, 16:00, 19:00 UTC). Each theme fires only in its `at` slots.
5. Tip: trigger one run and watch logs; or temporarily set the start command to
   `python -m bot --dry-run` for a safe first run.
