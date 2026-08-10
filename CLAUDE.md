# interesting-repos — agent guide

A push-only bot that discovers trending GitHub repos by theme, has an LLM curate them, and
posts one message per theme to a Telegram channel (mirrored to Slack). No server — a Python
script run by Railway cron three times daily. **Live in production.**

- **Run:** `python -m bot` · preview without sending: `python -m bot --dry-run` · custom themes: `--themes path.toml`
- **Test:** `.venv/bin/python -m pytest` (TDD throughout; ~152 tests, keep them green)
- **Stack:** Python 3.11+, httpx, tomllib, Ollama Cloud for LLM, Railway cron + a `/data` volume.
- **GitHub:** `SovereignSignal/interesting-repos` (public). Design history in `docs/superpowers/{specs,plans}/`.

## The pipeline (`bot/main.py run()`)

One cron run = one UTC hour. `run(config, now, dry_run)` does two phases:

**Phase 1 — select** (themes in `catch_all`-last order, so specific themes claim repos before
Trending sweeps the remainder):
1. Skip the theme unless `(now.weekday(), now.hour)` is in `theme.at` (`at=None` ⇒ always run).
2. `search_repos` per query — a theme's `query` may be a **list**; results merge, dedupe by id, re-sort by stars.
3. Drop forks/archived → `clean()` (keyword-stuffed, awesome-lists, stale > `max_idle_days`) → `unsent()` (state) → drop already-`claimed` (cross-theme) → `cap_agent_skills()` → cap at `CANDIDATE_LIMIT` (30).
4. `rank()` → `Pick`s; add their ids to `claimed`.

**Phase 2 — deliver** (themes in config/display order):
- Build `titles` + `summaries` (summaries get the curator's *why* lines), then `build_messages` (splits at the 4096-char Telegram limit).
- `send_message` (Telegram, primary) then `send_slack_message` (mirror; logs a WARNING on failure, never raises).
- `save_state` **only after all of a theme's messages send** — a mid-delivery crash re-delivers rather than losing repos.

## Critical design rules (violating these has bitten us)

- **The LLM provides signal; deterministic code enforces.** `rank()` asks the model to *score*
  every candidate 0–10 + a one-line why; `bot/filters.py` and the `min_score` gate do the actual
  keeping/dropping. Negative constraints ("exclude all X", "at most N") were **falsified live across
  5 models** — never push enforcement into the prompt.
- **`rank()` has three outcomes, and empty ≠ fallback:** scored picks above `min_score` → post them;
  scored but none clear the bar → **empty list = quiet slot** (logged INFO "none above the quality bar",
  not a failure, theme posts nothing); LLM down/unparseable → `_rank_llm` returns `None` → **stars
  fallback** (Picks with empty whys) so a digest still ships.
- **Graceful degradation is silent by design, so it must be alarmed.** Every LLM call falls back to ""
  (stars-sort / `_prettify` titles / raw descriptions / untranslated text). A bad `OLLAMA_API_KEY`
  once degraded prod with no crash and no alert (2026-06-06). `bot/alerts.llm_reachable` now
  pre-flight pings the LLM and DMs an alert. It pings the curator chain (via `resolve_curator`)
  **and**, independently in `main.run`, the base `OLLAMA_MODEL` — because titles + translation
  call the base model *directly*, outside the chain, so a curator that resolves on an earlier
  rung leaves the base unverified. A retired base (the 2026-07-15 `gemma3:12b` retirement slipped
  through exactly this gap) fires its own heads-up ("base model … unavailable — titles/translation
  fell back", run not degraded). Both alerts can fire in one run (dead curator primary + dead base).
- **Curator model split + fallback chain:** `alerts.resolve_curator` walks
  `OLLAMA_CURATOR_MODEL` (a comma-list of candidates) at pre-flight, picks the first reachable,
  and appends `OLLAMA_MODEL` as the final rung; the chosen model drives `rank()` + `make_summaries`
  while **titles and translation always stay on `OLLAMA_MODEL`**. A retired/401 primary self-heals
  to the next candidate (heads-up DM, run not degraded); only an all-down chain is stars-only +
  degraded. Prod runs `OLLAMA_CURATOR_MODEL=deepseek-v4-pro,gpt-oss:120b` with `gemma4:31b-cloud` base
  (predecessors `deepseek-v3.1:671b` / `gemma3:12b` retired 2026-07-15; bare `gemma4:31b`
  stopped resolving on Ollama Cloud 2026-08-10 — use the `-cloud` suffix).
  (Ollama Cloud retires models with little notice — `qwen3-next:80b` was pulled 2026-06-16, which
  is why the fallback chain exists; on a degraded/heads-up alert, probe the model for HTTP 410.)
- **Never log at INFO around sends.** httpx logs request URLs at INFO and the Telegram token sits in
  the URL path — `__main__._configure_logging` raises httpx/httpcore to WARNING to keep it out of logs.
- **`ALERT_CHAT_ID` is a DM and is alerts-only** — never digest content. Digests go to `TELEGRAM_CHAT_ID`
  (the channel) and the Slack mirror.

## Config

Env vars (`bot/config.load_config`): **required** `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.
Optional: `GITHUB_TOKEN`, `STATE_DIR` (=`/data`), `OLLAMA_HOST` (=`https://ollama.com`),
`OLLAMA_MODEL` (=`gemma4:31b-cloud`), `OLLAMA_API_KEY`, `OLLAMA_CURATOR_MODEL` (comma-list of curator
candidates, first reachable wins, base model is the final rung; blank ⇒ curate with `OLLAMA_MODEL`),
`SEND_DELAY_SECONDS` (=20, spaces messages within a run), `SLACK_BOT_TOKEN`, `SLACK_CHANNEL_ID`,
`ALERT_CHAT_ID`. Leave `OLLAMA_HOST` blank to disable all LLM features.

Themes (`themes.toml`, parsed to frozen `Theme`): `key`, `name`, `emoji`, `query` (string **or list**),
`sort`/`order`, `count` (cap), `rank` (`"llm"`|`"stars"`), `profile` (curator guidance),
`catch_all`, `max_idle_days` (=60), `agent_skill_cap` (None ⇒ unfiltered, 0 ⇒ drop **all** AI repos,
N ⇒ keep all non-packs + at most N skill *packs*), `min_score` (=6), `at` (list of `"weekday HH"`
UTC slots). `{since:Nd}` in a query expands to N days ago at run time.

**Schedule grid** — cron `0 13,16,19 * * *` (UTC), one theme per slot (this one-per-slot invariant is
maintainer-managed in `themes.toml`; if you add a theme, keep slots unique):

| UTC | Mon | Tue | Wed | Thu | Fri | Sat | Sun |
|----|----|----|----|----|----|----|----|
| 13 | trending | ai-agents | dev-tools | trending | ai-agents | dev-tools | web |
| 16 | crypto | finance | security | crypto | finance | security | science |
| 19 | systems | data | web | science | systems | data | — |

## Module map

| File | Responsibility |
|---|---|
| `main.py` | The two-phase run loop; slot matching; dedup; alert wiring |
| `__main__.py` | CLI (`--dry-run`, `--themes`), logging hardening, crash alert |
| `config.py` | `Theme`/`Config` dataclasses, `load_themes`/`load_config`, `_parse_at`, `expand_since` |
| `github.py` | `Repo`, `search_repos`, `readme_first_line`/`readme_excerpt` (raises on search error; readmes return "") |
| `ranker.py` | `rank` (3 outcomes), `_rank_llm` scoring prompt, `_parse_scores`, `rank_by_stars`, `Pick` |
| `filters.py` | `clean`, `cap_agent_skills`, `is_agent_skill_pack` (narrow) vs `is_ai_repo` (broad), `star_velocity`/`VELOCITY_CEILING`=2500 |
| `summaries.py` | `make_summaries` — "what + why" blurbs from desc+README+why; all-None fallback |
| `titles.py` | `make_titles` — 2–4 word titles, `_prettify` deterministic fallback |
| `translate.py` | `translate_to_english` — only non-Latin scripts; falls back to original text |
| `formatter.py` | `build_messages`/`_entry`, `TELEGRAM_LIMIT`=4096, HTML-escapes user text (not the URL) |
| `telegram.py` | `send_message` — HTML, retries w/ backoff, token-sanitized errors |
| `slack.py` | `send_slack_message` (never raises, returns bool), `html_to_mrkdwn` |
| `alerts.py` | `llm_reachable` pre-flight ping, `send_alert` DM (no-op when `ALERT_CHAT_ID` unset) |
| `state.py` | `load_state`/`save_state` (atomic `.tmp`+`os.replace`), `unsent`/`record_sent` (keyed by `theme.key`, cap 500) |
| `ollama.py` | `chat` — single-turn; returns "" on any error (the silent-degradation primitive) |

## Operating notes

- **Off-schedule test send** (prod secrets, throwaway state, won't collide with the cron's dedup):
  `railway run -- bash -c 'TELEGRAM_CHAT_ID=<dm> STATE_DIR=/tmp/x .venv/bin/python -m bot'`.
  For a dry-run on the real pool, strip `at` from a copy of `themes.toml` (slot-matching applies to
  dry runs too) and pass `--themes`.
- **Operate Railway** via the `railway-ops` skill (CLI + GraphQL token path; the MCP creds are often stale).
  Project `475326de-…`, env `production d1505ad5-…`, service `3c7798b9-…`.
- **Reading the live channel:** scrape `https://t.me/s/interestingrepos`; Slack via `railway run` +
  `conversations.history` with the bot token.
- **Heuristic drift:** the agent-skill classifiers are keyword/topic based — expect occasional tuning
  in `filters.py` as terminology drifts (e.g. AI repos slipping `cap=0` on Trending).
- **Thin Trending is expected,** not a bug: it has the strictest gauntlet (>1000★ + `cap=0` + dedup +
  quality bar). If it's persistently empty, loosen `stars:`/the window in `themes.toml`.

## Outstanding (housekeeping, owner-side)

Rotate the **Telegram bot token** and **Ollama Cloud key** — both have passed through chat and the
token historically hit Railway logs; the repo is public. No secrets are committed.
