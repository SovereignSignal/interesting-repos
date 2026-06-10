# Design: Weekly "Interesting Repos" Telegram Bot

- **Date:** 2026-05-26
- **Status:** Approved (brainstorming)

## 1. Summary

A small Python program, run **once a week by Railway cron**, that discovers
trending GitHub repositories, groups them into configurable **themes**, and posts
one Telegram message per theme to a group chat. It remembers what it has already
sent so repos never repeat.

Inspired by curated "interesting repos this week" threads. Two themes ship at
launch:

| Theme | What it surfaces |
|---|---|
| 🔥 **Top stars this week** | New repos that gained the most stars in the last 7 days |
| 💰 **Top finance repos** | Highest-starred, still-active finance / quant / trading repos |

There is **no always-on server** and **no webhook/polling** — the bot is push-only.
A "bot" here is a scheduled script that calls Telegram's `sendMessage` API.

## 2. Goals / Non-goals

**Goals**
- Weekly, hands-off digest of genuinely interesting repos.
- Theme-driven: a new theme is a few lines of config, not a code change.
- Robust enough to run unattended on Railway for months.
- Each entry's summary is the repo's **own GitHub description** (authentic, no AI
  marketing copy), with a README-first-line fallback when the description is empty.
- No repeats across weeks (per-theme dedup).

**Non-goals (YAGNI)**
- No interactive bot commands (`/latest`, etc.). Push-only.
- No AI-written summaries. (AI is available *only* for optional per-theme ranking.)
- No web UI / dashboard beyond Railway's own logs.
- No multi-user accounts or per-user personalization.

## 3. Key decisions (from brainstorming)

| Decision | Choice | Rationale |
|---|---|---|
| Repo focus | Trending, ranked to taste — realized as **themes** | The two example posts are two themed lists, not one feed. |
| Taste signal | A written interest profile (per-theme, optional) | Transparent, editable; only used by `rank: llm` themes. |
| Summary text | Repo's GitHub description as-is (README fallback) | Authentic, zero cost; no AI summary generation. |
| Delivery | A Telegram **group chat** | Shared digest; bot added to the group. |
| Hosting | **Railway cron** service | User deploys on Railway; tooling already available. |
| Stack | **Python** | Minimal, readable glue script; deploys cleanly on Railway. |
| Discovery source | **Official GitHub Search API** (Approach B) | No brittle HTML scraping; stable JSON; set-and-forget. |

**Trade-off accepted:** the Search API does not expose GitHub's exact "stars
gained this week" delta. "Top stars this week" is therefore implemented as *repos
created in the last 7 days, sorted by total stars* — i.e. new repos that blew up.
This matches the spirit of the example and avoids scraping fragility.

## 4. Architecture

One small Python package. Executes top-to-bottom and exits. Each module has one job
and is testable in isolation.

| Module | Responsibility | Depends on |
|---|---|---|
| `config.py` | Load + validate env vars; load `themes.toml`. Fail fast if a required secret is missing. | env, file |
| `github.py` | Run a theme's Search query; return `Repo` records. Fetch a README first line on demand for description fallback. | GitHub API |
| `ranker.py` | Optional: send candidates + profile to Claude (Haiku), return top-N ranked. Falls back to top-by-stars on error. | Anthropic API |
| `formatter.py` | Build one Telegram message for a theme. Handle empty-description fallback and the 4096-char limit. | (uses `github` for README) |
| `telegram.py` | POST a message to the group via Bot API `sendMessage`, with retries. | Telegram API |
| `state.py` | Load/save already-sent repo IDs (JSON on the Railway volume), keyed per theme, capped. | filesystem volume |
| `main.py` | Orchestrate the run per theme; own error handling and `--dry-run`. | all of the above |

### `Repo` record (shape)
`full_name`, `html_url`, `description`, `stars`, `language`, `topics`,
`created_at`, `pushed_at`, `is_fork`, `is_archived`, `id`.

## 5. Themes

Defined in `themes.toml` (version-controlled, edit without code changes). Each theme:

- `key` — stable slug used for state (e.g. `top-stars`, `finance`).
- `name` + `emoji` — message header.
- `query` — GitHub search qualifiers; supports a `{since:Nd}` placeholder expanded to
  a date N days before run time (e.g. `created:>{since:7d}`).
- `sort` — Search API sort (default `stars`, order `desc`).
- `count` — entries per message (default 7).
- `rank` — `stars` (default, deterministic) or `llm`.
- `profile` — optional text; only used when `rank = "llm"`.

**Starter `themes.toml`:**

```toml
[[theme]]
key   = "top-stars"
name  = "Top Stars This Week"
emoji = "🔥"
query = "created:>{since:7d} sort:stars-desc"
count = 7
rank  = "stars"

[[theme]]
key   = "finance"
name  = "Top Finance Repos"
emoji = "💰"
query = "topic:finance topic:quant topic:trading pushed:>{since:90d} sort:stars-desc"
count = 7
rank  = "stars"
```

## 6. Data flow (the weekly run)

For each theme, independently:

1. `main` loads config + the theme's already-sent IDs from the volume.
2. `github` runs the theme's query, retrieves up to ~50 candidates, and drops
   forks, archived repos, and already-sent IDs.
3. `ranker`:
   - `rank = stars` → keep Search API order (already by stars), take top `count`.
   - `rank = llm` → send candidates + profile to Claude, take the returned top
     `count`; **on any Anthropic error, fall back to top-by-stars**.
4. `formatter` builds one message: `{emoji} {name}` header + one line per repo —
   linked name · ⭐ stars · language · description (README first line if the
   description is empty; repo name only if README fetch also fails). Splits into
   multiple messages if over 4096 chars.
5. `telegram` sends the message(s) to the group, retrying on transient failure.
6. **On confirmed send**, `state` records the new IDs and saves to the volume.
   On send failure, state is **not** updated (repos remain eligible next week).

Themes are isolated: a failure in one theme is logged and skipped; the others
still run. The process exits non-zero if any theme failed to deliver, so Railway
flags the run.

## 7. Configuration & secrets

Railway environment variables:

| Var | Required | Purpose |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | yes | Bot API token from BotFather |
| `TELEGRAM_CHAT_ID` | yes | The target group's chat id (negative number) |
| `GITHUB_TOKEN` | recommended | Raises Search API rate limits (10→30 req/min) |
| `ANTHROPIC_API_KEY` | optional | Only needed if a theme uses `rank = "llm"` |
| `STATE_DIR` | optional | State file directory; defaults to `/data` (Railway volume) |

`themes.toml` is committed to the repo (not a secret).

## 8. State

- Single `state.json` at `${STATE_DIR}/state.json`.
- Shape: `{ "<theme-key>": [repo_id, ...], ... }`.
- Per-theme so the same repo may legitimately appear under different themes.
- Capped at the most recent ~500 IDs per theme (FIFO) to bound growth.
- Missing file → treated as empty state (first run).
- Requires a Railway **volume** mounted at `STATE_DIR`; without it, dedup resets
  each run (degraded but still functional). README documents the volume setup.

## 9. Error handling

| Failure | Behavior |
|---|---|
| Missing required env var | Exit 1 at startup with a clear message |
| GitHub query error / rate limit | Respect `Retry-After`/backoff; if still failing, log + skip that theme |
| Anthropic ranking error | Fall back to top-by-stars for that theme |
| README fetch error (fallback path) | Use repo name only; never crash |
| Telegram send error | Retry 2–3× with backoff; if still failing, don't update state, mark run failed |
| Theme has no new repos | Skip posting that theme (logged) |
| Any theme failed to deliver | Process exits non-zero (Railway flags it) |

## 10. Testing (TDD)

All tests mock HTTP — no live API calls.

- `github`: parse Search JSON → `Repo`; filters drop forks/archived/already-sent;
  README first-line extraction.
- `ranker`: returns ordered subset for `llm`; **fallback to stars on Anthropic error**.
- `formatter`: description present vs empty→README fallback; output stays under the
  4096-char limit and splits correctly when long.
- `state`: missing file → empty; save/reload round-trip; per-theme isolation; 500-cap FIFO.
- `telegram`: correct `sendMessage` payload; retry-on-failure path.
- `main`: `--dry-run` prints messages and touches neither Telegram nor state; one
  theme erroring does not abort the others.

`--dry-run` flag prints each theme's rendered message to stdout instead of sending —
used for local testing and a safe first Railway run.

## 11. Project layout

```
interesting-repos/
  bot/
    __init__.py
    config.py
    github.py
    ranker.py
    formatter.py
    telegram.py
    state.py
    main.py
  themes.toml          # the two starter themes
  tests/
  pyproject.toml       # deps: httpx, anthropic, tomli (py<3.11), pytest, respx
  railway.json         # weekly cron schedule, e.g. "0 13 * * 1" (Mon 13:00 UTC)
  .env.example
  README.md            # BotFather setup, get group chat_id, Railway vars + volume
```

Entry point: `python -m bot` (runs all themes). `python -m bot --dry-run` prints
instead of sending.

## 12. Deployment (Railway)

1. Create bot via BotFather → `TELEGRAM_BOT_TOKEN`.
2. Add the bot to the target group; capture the group `chat_id` →
   `TELEGRAM_CHAT_ID`.
3. Create a Railway project from the repo; set env vars.
4. Attach a volume mounted at `/data` for `state.json`.
5. Configure the service as a **cron** with a weekly schedule (default Mon 13:00 UTC).
6. First run with `--dry-run` (or inspect logs) to confirm output before going live.

## 13. Open items / future (not in scope now)

- A `rank: llm` "ranked to my taste" theme using a written interest profile.
- More themes (Rust, AI agents, etc.) — config-only additions.
- Exact "stars gained this week" delta would require the trending-page scrape
  (Approach A); deferred unless the Search-API approximation proves unsatisfying.
