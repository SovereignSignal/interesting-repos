# Time Slots & Curation Quality — Design

**Date:** 2026-06-10
**Status:** Approved direction, pending spec review
**Ships as:** two PRs — PR 1 (time slots), PR 2 (quality: scored curation + curator model + wider net)

## Problem

1. **Burstiness.** The staggered daily schedule (PR #5) still fires all of a day's ~3 themes
   in one 13:00 UTC run, so the channel gets a burst of back-to-back messages that is hard
   to follow.
2. **Pick quality.** Some picks are legitimate but low-substance (tutorials, thin wrappers,
   me-too projects), because the curator is told to "choose the 7 best" and fills the quota
   no matter how thin the pool is.
3. **Blurb quality.** Summaries say *what* a repo is but never *why* it's worth a click.

## PR 1 — Time-slot scheduling

One theme per time slot, spread across the day. Same stateless design as `days`: the run
timestamp alone decides the lineup.

### Config

- `themes.toml`: each theme's `days = ["mon", "thu"]` is **replaced** by
  `at = ["mon 13", "thu 16"]` — weekday + UTC hour per firing. A theme may fire at
  different hours on different days.
- `Theme.days` is removed; `Theme.at: tuple[tuple[int, int], ...] | None` holds
  `(weekday, hour)` pairs (`None` = fire every run, preserving current semantics for
  themes without a schedule).
- `config.py` parses `"mon 13"` → `(0, 13)` via the existing `_WEEKDAYS` map; invalid
  day names or hours outside 0–23 raise at load time (fail fast at startup, not mid-run).

### Runtime

- `main.run(config, now: datetime | None = None, dry_run=False)` — the `today: date`
  parameter becomes `now: datetime` (UTC); `today = now.date()` internally.
- Theme skip rule: `theme.at is not None and (now.weekday(), now.hour) not in theme.at`.
  Exact-hour match — Railway cron fires at minute 0, and a container that starts a few
  minutes late is still within the hour.
- `bot/__main__.py` passes `datetime.now(timezone.utc)`. Cron hours are UTC, so the
  match must be UTC too — never local time.
- `railway.json` cron: `0 13 * * *` → `0 13,16,19 * * *`. A run where no theme matches
  (Sun 19:00) sends nothing and exits 0.

### Slot grid (20 firings over 21 slots; day pairs unchanged from PR #5)

| UTC | Mon | Tue | Wed | Thu | Fri | Sat | Sun |
|-----|-----|-----|-----|-----|-----|-----|-----|
| 13:00 | trending | ai-agents | dev-tools | trending | ai-agents | dev-tools | web |
| 16:00 | crypto | finance | security | crypto | finance | security | science |
| 19:00 | systems | data | web | science | systems | data | — |

`SEND_DELAY_SECONDS` still spaces messages *within* a run (a long theme can split into
multiple Telegram messages).

## PR 2 — Curation quality

Three levers in one PR; they all touch the ranker/summaries seam and are validated with
one live `--dry-run`.

### Lever A: scored curation with a quality bar + "why it matters"

The hard-won project rule stands: **the LLM provides signal; deterministic code enforces.**

- `_rank_llm` prompt changes from "choose the N best, return indices" to: score every
  candidate 0–10 against the theme profile and write a one-line *why* (what makes it
  notable — novelty, pedigree, momentum, who it's for). Reply format:
  `[{"i": 3, "score": 8, "why": "..."}]`. Parsing tolerates code fences/prose around the
  JSON (same approach as today's `_parse_indices`).
- Deterministic gate in `rank()`: keep repos with `score >= theme.min_score`, sort by
  score desc, cap at `theme.count`.
  - `min_score` is a new `Theme` field; TOML override per theme; **default 6** —
    deliberately conservative until calibrated against a real pool (see Validation).
- `rank()` now returns `list[Pick]` where `Pick = (repo, why)` (a small frozen dataclass).
  The stars fallback returns `why=""` for each pick. Callers (`main`, tests) adapt.
- **Outcome semantics — three distinct cases:**
  1. LLM scored, some repos clear the bar → post those (1 to `count` repos).
  2. LLM scored, *nothing* clears the bar → theme posts nothing this slot; logged as
     `"theme X: no repos above quality bar"`. **Not** a failure, no alert. Quiet slots
     are the feature.
  3. LLM error / unreachable / unparseable → existing degraded path: stars fallback
     fills `count` (a digest always ships), covered by the existing pre-flight alert.

### Lever A (blurbs): "what + why" summaries

- `make_summaries` gains a `whys` parameter (the curator's why-lines, same order).
- Prompt asks for **up to two sentences, ≤ 40 words**: first *what it is* (factual, as
  today), then *why it's notable* — informed by the curator's why but rewritten, not
  pasted. Same tone rules: no marketing language, no emoji, no hype.
- All existing fallbacks unchanged: no LLM / bad JSON / length mismatch → `None` per
  repo → raw description.

### Lever B: stronger curator model

- New env var `OLLAMA_CURATOR_MODEL` → `Config.ollama_curator_model`; **defaults to
  `OLLAMA_MODEL` when unset** (zero-config behavior identical to today).
- Used by: curation scoring (`rank`) and summaries (`make_summaries`). Titles and
  translation stay on `OLLAMA_MODEL` (gemma3:12b — cheap and adequate).
- Prod value decided at rollout via dry-run comparison; starting candidate
  `qwen3-next:80b` (strongest at *relevance* in the v3 bake-off; its negative-constraint
  weakness no longer matters — Lever A asks it only to score, never to exclude).
- Pre-flight: `main.run`'s degraded check pings **both** models when they differ
  (one extra `llm_reachable` call), so a 401/missing curator model can't silently
  degrade scoring back to stars — the exact failure mode of the 2026-06-06 incident.

### Lever C: wider search net

- `themes.toml` `query` accepts a **string or list of strings**; `Config` normalizes to
  `Theme.queries: tuple[str, ...]`.
- `main` Phase 1 runs `search_repos` once per query, merges results, dedupes by repo id,
  sorts merged pool by stars desc, then proceeds through the existing filter chain
  unchanged (`CANDIDATE_LIMIT` still applies after filters).
- Initial query expansions (tuned during implementation; one `topic:` per query since
  GitHub ANDs multiple topic qualifiers):
  - dev-tools: + `topic:developer-tools`, `topic:terminal`
  - ai-agents: + `topic:llm`
  - crypto: + `topic:blockchain`
  - data: + `topic:data-engineering`
  - web: + `topic:webdev`
  - security: + `topic:cybersecurity`
  - others stay single-query until there's evidence the pool is thin.
- Cost: a few extra GitHub Search calls per run — far below the 30 req/min search limit
  at 1 theme per slot.

## Error handling summary

| Condition | Behavior |
|---|---|
| Invalid `at` entry in TOML | `SystemExit` at config load |
| No theme matches current slot | Run sends nothing, exits 0 |
| Curator LLM down / 401 | Pre-flight alert (both models) + stars fallback, digest ships |
| All candidates below `min_score` | Theme silent this slot, info log, no alert |
| One query of several fails | Theme selection raises → counted as theme failure (existing behavior); acceptable since queries share the same GitHub API |

## Testing

TDD throughout (project convention; 128 tests today). New/updated coverage:

- `config`: `at` parsing (valid, invalid day, invalid hour, absent), `query` as list,
  `min_score` default/override, `OLLAMA_CURATOR_MODEL` default chain.
- `main`: slot matching (fires on match, skips on hour mismatch, `at=None` always fires),
  multi-query merge/dedupe, pre-flight pings both models only when they differ.
- `ranker`: score parsing (fenced/prose JSON, bad entries), threshold gate, all-below-bar
  → empty (and main treats empty as quiet, not failure), LLM error → stars fallback with
  empty whys, `Pick` ordering by score.
- `summaries`: whys threaded into prompt, two-sentence output accepted, all fallbacks
  intact.

## Validation & rollout

1. **PR 1** ships first: small, isolated; verified by unit tests + next day's live runs
   (three single-theme posts at 13/16/19 UTC).
2. **PR 2** before merge: `--dry-run` with the real Ollama key on the live pool,
   comparing gemma3:12b vs qwen3-next:80b scoring side by side; inspect score
   distribution to sanity-check `min_score = 6` (tune if it gates everything or nothing).
3. After merge: watch the first two days of slots; the existing alert path covers LLM
   outages, and quiet slots are expected occasionally by design.

## Out of scope (deferred)

- HN/social cross-signal ("external buzz") — prove the three levers live first.
- Per-theme channels/Telegram topics.
- Token/key rotation (outstanding housekeeping for Sov, tracked in memory).
