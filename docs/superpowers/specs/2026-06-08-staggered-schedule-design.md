# Design: Staggered daily schedule

- **Date:** 2026-06-08
- **Status:** Approved (brainstorming)
- **Owner:** sov@sovereignsignal.com

## 1. Summary

Move from a **weekly** cron that fires all 10 themes at once to a **daily** cron where
each theme fires on a configured subset of weekdays. Each theme runs **2×/week**, ~3
themes/day, staggered — so the channel gets a steady daily trickle instead of one
weekly flood.

## 2. Motivation

The weekly Monday run posts all 10 categories within ~5 minutes (confirmed in the
2026-06-08 logs: `13:02:05` → `13:07:16`, ~35s apart). The 20s message throttle works,
but 10 categories on one day still reads as "everything at once." Spreading categories
across the week is the fix.

## 3. Design

### 3.1 Cron (daily)
`railway.json` `cronSchedule`: `"0 13 * * 1"` → **`"0 13 * * *"`** (every day, 13:00 UTC).

### 3.2 `Theme.days`
New optional field `days` on `Theme` — the weekdays a theme fires. In `themes.toml`:
`days = ["mon", "thu"]` (lowercase 3-letter names). `load_themes` maps them to a tuple
of weekday ints via `_WEEKDAYS` (`mon`=0 … `sun`=6, matching `date.weekday()`). Absent
→ `None` → the theme fires **every day** (backward-compatible; existing tests unaffected).

### 3.3 `main` filter
In `run()`, skip a theme when `theme.days is not None and today.weekday() not in
theme.days`. `today` is already passed into `run`, so there's **no extra state** — the
run date alone decides the day's lineup. The skip happens at the top of the Phase-1
loop (before search), so a non-scheduled theme does no work.

### 3.4 The schedule (each theme 2×/week)

| Theme | `days` |
|---|---|
| 📈 trending | mon, thu |
| 🤖 ai-agents | tue, fri |
| 🛠️ dev-tools | wed, sat |
| ⛓️ crypto | mon, thu |
| 💰 finance | tue, fri |
| 🔐 security | wed, sat |
| ⚙️ systems | mon, fri |
| 🗄️ data | tue, sat |
| 🌐 web | wed, sun |
| 🔬 science | thu, sun |

Resulting daily lineups: **Mon** Trending·Crypto·Systems · **Tue** AI-Agents·Finance·Data
· **Wed** Dev-Tools·Security·Web · **Thu** Trending·Crypto·Science · **Fri**
AI-Agents·Finance·Systems · **Sat** Dev-Tools·Security·Data · **Sun** Web·Science. Each
theme's two days are 3-4 apart.

### 3.5 Dedup interaction (why daily isn't spammy)
Each firing sends only repos not already in `state.json`. A theme firing 2×/week
(3-4 days apart) posts only the repos that newly entered its top since last time —
fresh, small posts. A category with nothing new simply skips that day (existing
"no new repos" path).

## 4. Out of scope
- Frequency changes beyond editing `days` per theme.
- Per-theme `count` tuning.

## 5. Testing (TDD; HTTP mocked)
- **`test_config`:** `days` parsed from names to a weekday-int tuple; absent → `None`.
- **`test_main`:** only themes whose `days` include `today.weekday()` fire; a theme
  with `days=None` fires on any weekday; existing tests (no `days`) unaffected.

## 6. File change map
| File | Change |
|---|---|
| `bot/config.py` | `Theme.days: tuple \| None`; `load_themes` parses `days` via `_WEEKDAYS`. |
| `bot/main.py` | Skip themes not scheduled for `today.weekday()`. |
| `themes.toml` | `days = [...]` on each theme (§3.4). |
| `railway.json` | `cronSchedule` → daily. |
| `tests/` | extend `test_config`, `test_main`. |
