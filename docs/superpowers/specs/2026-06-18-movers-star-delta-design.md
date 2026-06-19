# Weekly "Movers" (star-delta) digest — Design

**Date:** 2026-06-18
**Status:** Proposed, pending review
**Ships as:** one PR (`movers`)

## Problem

A post genre is having a moment on tech X — "the fastest growing GitHub repos in
finance + AI this week," ranked by weekly `+N★`, one line each. The hook that makes
them pop is **velocity**, not total stars: "what actually blew up this week."

The bot cannot surface that today. Every theme sorts a created-recently window by
**total** stars (`sort = "stars"`); nothing computes a star *delta*. GitHub Search
can't sort by delta either (it offers stars/forks/updated/help-wanted/best-match, not
"stars gained since T-7d"). So "this week's real breakouts" is a genuine blind spot,
and it's exactly the *feel* of the viral posts.

The independent path is to compute the metric ourselves, on data we already own, and
run it through the existing gauntlet — not to re-broadcast anyone else's ranking.

## Non-goal: re-broadcasting viral posts

Considered and rejected. Watching X for viral repo posts and re-posting their picks
would:

- **Dissolve the moat.** The channel's value is independent curation; re-broadcasting
  turns it into an amplifier for someone else's (often astroturfed or paid) ranking.
  These accounts mostly pull GitHub Trending + star-history and dress it up.
- **Duplicate data we already query.** Same source pool, worse provenance, more steps.
- **Add fragile failure surfaces** — X scraping (auth/rate limits/ToS), a virality
  detector (itself gamed), a new state dimension — reintroducing the silent-degradation
  holes the alerting work just closed.

The salvaged kernel of that idea — "use external buzz as a discovery seed that still
runs the full gauntlet" — is low-leverage, since the bot's query net already catches
the repos. This spec builds the owned-metric path instead.

## Approach

The format is the thing worth stealing. Three pieces: a **star-snapshot store** (fed by
every run, not just Movers), a **delta-sourcing candidate path** (new `delta_days`
theme field), and one **Movers theme** that uses them. The snapshot store is the only
new state; it is disposable and rebuildable (same ethos as `state.json`'s sent-history
and the brain's "search is disposable" rule).

### 1. The snapshot store — `bot/starsnap.py`

A rolling per-day record of star counts for every repo the bot *examines* (every search
result, whether picked or not). Movers reads from it; every theme feeds it.

- **Location:** `STATE_DIR/starsnap/YYYY-MM-DD.json` — a JSON map `{repo_id(str): stars(int)}`.
  Separate from `state.json` (different concern; keeps `state.py` focused on sent-history).
- **Write:** idempotent per-day merge. Every run loads today's file (or `{}`), folds each
  searched repo's `(id, stars)` in, and atomically re-saves (`.tmp` + `os.replace`, same
  as `save_state`). A same-day re-run just overwrites with equal-or-later counts.
- **Retention:** keep ~14 days; `retain(state_dir, keep_days=14)` deletes older files at
  end of each run. 14 (2× the delta window) gives slack for a missed cron day.
- **Size:** a few thousand repo ids × 14 days × ~40 bytes ≈ well under 1 MB total. Trivial.
- **Disposable:** delete `starsnap/` and the only consequence is Movers going quiet for a
  week while it rebuilds. No other feature depends on it.

API (pure, filesystem-backed):
```
snapshot_path(state_dir, day: date) -> str
load_snapshot(state_dir, day: date) -> dict[int, int]      # {} if absent
save_snapshot(state_dir, day: date, mapping: dict[int,int]) -> None   # atomic
find_baseline(state_dir, today: date, delta_days: int, tolerance: int = 3) -> dict[int,int]
    # nearest snapshot aged in [delta_days, delta_days+tolerance]; {} if none (cold start / gap)
retain(state_dir, keep_days: int = 14) -> None              # delete files older than keep_days
order_by_delta(repos, baseline: dict) -> list               # repos in baseline, by (stars-now - baseline) desc
```

`find_baseline` walks days older from `today - delta_days` out to `today - (delta_days + tolerance)`,
returning the first existing snapshot. Tolerance covers a missed cron day or a deploy gap;
if nothing exists in the window (cold start), it returns `{}` and every repo is dropped →
quiet slot, by design.

### 2. Delta sourcing — new `Theme.delta_days`

`Theme` gains `delta_days: int | None = None`. When set, Phase 1 sources candidates by
week-over-week delta instead of leaving them in search-stars order:

1. Run the theme's wide query as usual (GitHub is still the source of truth for repo
   metadata + current stars). Snapshot every result into today's file (this is the same
   write every other theme does — Movers is not special-cased for snapshotting).
2. Load `baseline = find_baseline(state_dir, today, theme.delta_days)`.
3. Reorder by delta, dropping repos with no prior snapshot (delta undefined = "new to the
   pool" → not eligible this week; regular themes pick them up). This reordering happens
   **before** the `CANDIDATE_LIMIT` cap, so the LLM scores the top movers, not top-by-stars.

The rest of the pipeline is **unchanged**: `clean` → `unsent` → drop `claimed` →
`cap_agent_skills` → cap → `rank` (LLM, `min_score` gate). A delta theme is just a theme
whose candidate pool arrived in delta order and was pre-filtered to known repos. `rank`
and the curator prompt are untouched — the delta is pure data; per the standing rule, we
do not push "exclude star farms" into the prompt (the cap + `min_score` enforce that).

`delta_days` is orthogonal to `rank` (still `"llm"`) and to `agent_skill_cap`. It only
governs candidate sourcing/ordering.

### 3. The Movers theme

One firing per week — `at = ["sun 19"]`, the **only empty slot** in the grid (so it fits
without displacing anything; the one-per-slot invariant holds).

```toml
[[theme]]
key   = "movers"
name  = "This Week's Movers"
emoji = "🚀"
query = "created:>{since:120d} stars:>500"
sort  = "stars"
count = 7
rank  = "llm"
delta_days = 7
agent_skill_cap = 2          # keep real AI tools, cut clone/skill packs
min_score = 7               # higher bar than default 6 — we're featuring "what blew up"
at = ["sun 19"]
profile = "Real breakouts with genuine momentum this week — repos whose star growth reflects substance (a novel tool, an inflection point, a real release), not manufactured hype. Aggressively discount anything whose velocity looks bought or coordinated, agent-skill clone packs, and forks riding a parent's fame. If the growth is real but the project is thin, it is not a mover."
```

Tuning rationale, all adjustable at rollout:
- **`stars:>500`** (vs Trending's `>1000`): the delta lens naturally surfaces repos that
  *started* modest and are accelerating — exactly the "movers" signal — so a lower floor
  is right. Calibrate against the first dry-runs.
- **`agent_skill_cap = 2`** (not Trending's `0`): the example tweet that prompted this
  was itself a skill pack (`last30days-skill`, +12k★/wk) — the precise thing the bot is
  built to exclude. `cap=2` keeps a genuinely-viral real AI tool while cutting clone
  packs. If the first weeks show AI noise dominating, escalate to `cap=0` (one-line
  TOML change; `VELOCITY_CEILING` and the anti-hype scoring language already arm the
  fallback path).
- **`min_score = 7`**: we are explicitly *featuring* growth, so the quality bar goes up.
- **Not `catch_all`:** Movers is specific. It runs Sunday; Trending/etc. run other days,
  so `claimed` (per-run) never interacts. `unsent` still de-dupes week-over-week, so a
  repo can't be a "mover" two weeks running — a nice emergent property.

### 4. Display — deltas in the formatter

`_entry` currently builds `⭐ 1,234 · Lang · owner/name`. Thread the delta through so a
mover reads `⭐ 1,234 · **+1.2k★ this week** · Lang · owner/name`:

- `build_messages` and `_entry` gain an optional `deltas: list[str | None] | None`.
  When a delta string is present for a repo it's spliced into the meta line; absent
  (every non-mover theme) → output byte-identical to today. Default `None` keeps all
  existing call sites unchanged.
- `main` builds `deltas` from the same `baseline` dict for the picked repos, since
  `repo.stars` is already the authoritative current count: `delta = repo.stars - baseline[id]`.
- A small `_format_delta(n)` helper → `+1.2k★ this week` for ≥1000, else `+123★ this week`.
  Compact form on purpose (that's the viral-format feel); trivial to switch to
  `+1,234★` (thousands-separator, matching the `⭐` style) if it reads better live.

The LLM why-blurb (`make_summaries`) is unchanged — the delta is data shown alongside,
not generated, text.

## Cold start

Snapshots accumulate from **ship day forward** — there is no historical star data and no
backfill (the star-history APIs are slow, ToS-fragile, and out of scope; the owned path
is the point). Consequences:

- First ~7–11 days: `find_baseline` returns `{}`, Movers has no candidates → **quiet
  slot**, logged `INFO` (the existing "no repos above quality bar" path), **no alert**.
  Quiet slots are the feature, not a failure. Expect the first real Movers post the
  second Sunday after ship.
- Snapshotting is a side effect of **every** run, so the clock starts the moment the PR
  merges — not when Movers first fires.

This must be called out in the rollout note so a week of empty Sundays isn't read as a bug.

## Wiring in `main.run`

Phase 1 additions (all guarded, zero impact on non-`delta_days` themes):

- Load `today_snap = load_snapshot(state_dir, today)` once at the top of Phase 1.
- After each `search_repos`, fold results in: `for r in hits: today_snap[r.id] = r.stars`.
  (Runs for every theme — this is what builds the store.)
- When `theme.delta_days` is set, after the fork/archived filter and **before** the cap:
  `baseline = find_baseline(...)`; `repos = order_by_delta(repos, baseline)`. Repos absent
  from `baseline` are dropped here, so `CANDIDATE_LIMIT` and `rank` see only eligible movers.
- Persist `today_snap` once after Phase 1 (`save_snapshot`), then `retain(state_dir)`.
  Saving at end-of-Phase-1 (not mid-loop) keeps it atomic even if a theme later throws.
- `deltas` for delivery: computed from the `baseline` used in Phase 1, passed to
  `build_messages`. (If Movers didn't run this slot, `deltas=None` everywhere.)

Net blast radius: one new module (`starsnap.py`), a `delta_days` field on `Theme` +
one line in `load_themes`, a guarded block + two snapshot lines in `main.run`, an
optional `deltas` param on two formatter functions, and the new theme in `themes.toml`.
No change to `ranker.py`, `filters.py`, `state.py`, `summaries.py`, `titles.py`, or
`translate.py`.

## Cost

- **GitHub:** one extra search per Movers firing (weekly) — negligible vs the 30 req/min
  search budget. Snapshotting reuses stars we already fetched (no extra API calls).
- **LLM:** none extra — Movers uses the same `rank` + `make_summaries` calls any theme makes.
- **Disk:** < 1 MB (see above).
- **Runtime:** one JSON read + one atomic write per run; trivial.

## Error handling

| Condition | Behavior |
|---|---|
| No baseline snapshot yet (cold start) or store deleted | `find_baseline` → `{}`; Movers posts nothing; `INFO` log; no alert |
| Missed cron day within the window | `find_baseline` falls back to the nearest snapshot within `tolerance`; delta uses that |
| Movers search fails | Theme selection raises → counted as a theme failure (existing behavior); existing alert fires |
| A picked repo absent from baseline | Cannot happen — dropped at `order_by_delta`; but formatter treats `None` delta as "no annotation" defensively |
| LLM down this slot | Existing `rank` stars-fallback + pre-flight degraded alert; delta annotation still shows (it's data, not LLM output) |

## Testing (TDD)

`starsnap` (pure, tmp-path fixtures — the repo's convention):
- `load_snapshot` on missing file → `{}`; round-trip save/load.
- `find_baseline`: exact day present → it; exact missing, older present within tolerance
  → the older one; nothing in window → `{}`; out-of-window newer snapshot ignored.
- `retain`: deletes files older than `keep_days`, keeps the rest.
- `order_by_delta`: sorts by `now - baseline` desc; drops repos not in baseline; ties stable.

`config`: `delta_days` parses (set/unset/default `None`); TOML round-trip.

`main` (monkeypatch `search_repos`/`rank`/sends):
- Non-delta theme: behavior byte-identical to today (no snapshot reordering, deltas `None`).
- Delta theme: candidates arrive in delta order, repos with no baseline are dropped before
  the cap, `today_snap` written, `retain` called.
- Snapshot accumulates across multiple themes in one run (union, not overwrite).
- Cold-start baseline `{}` → Movers quiet slot, no send, no alert.
- `deltas` list built for picked repos and threaded into `build_messages`.

`formatter`: `deltas=None` → output identical to today; `deltas` with some `None` → those
entries unannotated; `_format_delta` at 0/999/1000/12345 boundaries.

## Validation & rollout

1. Unit tests green (project convention; ~152 today).
2. `--dry-run` on the live pool with the real Ollama key: confirm `find_baseline` returns
  `{}` (cold start) and the run exits clean with no Movers output and no alert — i.e. the
  quiet-slot path is verified before it goes live.
3. Land snapshotting first (it's a no-op without a delta theme) so the clock starts; add
  the theme and slot in the same PR so there's one config change to review.
4. After merge: watch the first two Sundays. The first should be a quiet slot (cold start,
  by design); the second should produce the first real digest. Calibrate `stars:>`,
  `agent_skill_cap`, `min_score`, and compact-vs-comma delta format against real output.

## Safety

The channel is **live with followers** (`[[channel-is-live-no-test-sends]]`). All
verification is unit tests (mocked) or `--dry-run` only. No real send to
`TELEGRAM_CHAT_ID`/Slack during this work; an off-schedule live send uses the existing
throwaway-`STATE_DIR` Railway pattern.

## Known limitations (accepted)

- **Cold start is ~7–11 days of empty Sundays.** No backfill (would need star-history
  APIs — slow, ToS-fragile, and against the owned-data principle). Accepted: the clock
  starts at merge.
- **The pool is "repos we already query," not all of GitHub.** A repo outside every
  theme's search window has no snapshot and can't be a mover. This is a feature, not a
  bug: it keeps Movers inside the same candidate universe as the rest of the channel, and
  it self-filters toward repos the bot has been watching. Widening `query` later is one line.
- **`tolerance=3` days** means the baseline is occasionally 8–10 days old rather than 7,
  so a "weekly" delta is approximate. Immaterial for ranking; document and move on.
- **Delta is point-in-time, not true weekly growth.** A repo that spiked then cooled
  inside the window shows a muted delta. Fine — the goal is "what's hot *now*," and the
  LLM quality bar + `unsent` de-dup handle the rest.

## Out of scope

- Star-history / stargazer-timestamp backfill (the cold-start fix; rejected as above).
- Watching X/HN/social for external-buzz cross-signal (proven false-start; the independent
  metric replaces it).
- Per-domain movers (crypto-movers, ai-movers). One general Movers first; split only if
  the pool is rich enough to support it and the channel wants it.
- Surfacing the delta number on non-mover themes (e.g. Trending). Could be a nice touch
  later; keep this PR's blast radius small.
