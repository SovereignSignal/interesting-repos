# Interesting Repos → Telegram

Digest of trending GitHub repos, by theme, posted to a Telegram group.
Push-only: a script run by Railway cron three times daily. No server.

## How it works
Discover (GitHub Search API "breakout" queries: recently-created, high-star; a theme
may merge several queries) → pre-filter (deterministic spam/stale/agent-skill-cap
drops) → curate (for `rank=llm` themes an Ollama model **scores every candidate 0–10**
against the theme's `profile` and writes a one-line "why"; code keeps only scores at or
above `min_score`, so a thin pool posts fewer repos — or none — rather than padding;
else top-by-stars) → summarize (an LLM blurb of *what it is + why it's notable*, with
the repo's own description / README-first-line as fallback; non-English text translated
to English) → post one message per theme → remember sent repos so nothing repeats. See
`docs/superpowers/specs/` and `docs/superpowers/plans/` for the full design.

**Graceful degradation is the core design rule:** every LLM call falls back silently
(stars-sort, raw descriptions, original text) so a missing/broken Ollama key never
crashes a run — it just produces a worse digest. Because that hides outages, a
pre-flight health-check DMs an alert when the LLM is unreachable (see `bot/alerts.py`).

## Themes
Edit `themes.toml`. Each `[[theme]]` is one message. `query` is a GitHub Search
qualifier string **or a list of strings** (results merged + deduped by repo id);
`{since:Nd}` expands to N days ago. Sorting is the `sort` field, not a `sort:`
qualifier. `rank = "llm"` has an Ollama model score candidates against the theme's
`profile`; `min_score` (default 6) is the bar a repo must clear to be posted — raise
it to be stricter, lower it if a theme runs too sparse. `rank = "stars"` just takes
the top by stars (no AI). `count` caps the posts. `agent_skill_cap` limits agent-skill
*packs* (0 = drop all AI repos, used by Trending; N = at most N packs). LLM curation
needs `OLLAMA_*` set (below). `at` is a list of `"weekday HH"` slots (e.g.
`at = ["mon 13", "thu 16"]`, UTC); a theme fires only when the run's UTC weekday and
hour match one of its slots. The grid assigns one theme per slot, so each cron run (one
UTC hour) posts a single theme; a run where no theme matches sends nothing.

## Where it posts
`TELEGRAM_CHAT_ID` is the primary target (a channel or group). If `SLACK_BOT_TOKEN` +
`SLACK_CHANNEL_ID` are set, every message is also mirrored to Slack (`bot/slack.py`
converts the Telegram-HTML to mrkdwn); Slack is best-effort and never blocks a run, but
a mirror failure now logs a WARNING. `ALERT_CHAT_ID` is a **separate** DM target used
only for failure/degradation alerts — never for digest content.

## Translation
Descriptions in non-Latin scripts (Chinese, Japanese, Korean, Cyrillic, Arabic, …)
are auto-translated to English via an **Ollama** chat model (default `gemma3:12b`).
Use Ollama Cloud (`OLLAMA_HOST=https://ollama.com` + `OLLAMA_API_KEY` from
ollama.com) or a local server (`OLLAMA_HOST=http://localhost:11434`, no key).
Leave `OLLAMA_HOST` blank to disable. English/Latin text is never sent to the model,
and any translation error falls back to the original text.
`OLLAMA_CURATOR_MODEL` — stronger model(s) for curation scoring and summaries. Set a
comma-separated list (e.g. `deepseek-v3.1:671b,gpt-oss:120b`) for an ordered fallback
chain: at startup the bot picks the first reachable one, and if all are down it falls
back to `OLLAMA_MODEL`, then to stars-only. A retired or unauthorized primary self-heals
to the next model (with a heads-up alert) instead of degrading the whole run. Blank ⇒
curate with `OLLAMA_MODEL`.

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
