# Coverage & Quality Expansion — Design

**Date:** 2026-08-20
**Status:** Research / recommended direction (not yet implemented)
**Ships as:** a sequence of small PRs (see §8). This document is the map, not a
single-diff spec.

## 1. Summary

The bot already has the hard parts: scored curation with a quality bar, deterministic
spam/skill-pack filters, a full slot grid, and a weekly Movers delta. The remaining
problems are **not "we need more LLM"** and **not "scrape HN/X"**. They are:

1. **Coverage is starved on the non-AI themes** by `topic:` + `stars:>N` queries that
   return a dozen candidates — too few for `min_score` to choose from.
2. **Quality is starved on the fat themes** because GitHub Search returns the
   starriest 50 of tens of thousands, and those 50 are ~80% AI coding agents /
   harnesses. `agent_skill_cap = 2` only cuts *skill packs*, so standalone AI tools
   colonize Dev Tools, Finance, Security, Data, and Web.
3. **The classifier has blind spots** that let AI through Trending's `cap=0`
   (empty description + empty topics + "AI" only in the org name).
4. **The schedule grid is full.** New themes need a fourth cron hour or a slot
   stolen from a 2×/week theme — do that *after* the existing themes can fill.

Live probes (GitHub Search, unauthenticated, 2026-08-20) and the public channel
(`t.me/s/interestingrepos`) back this. The right next work is mostly
`themes.toml` + pool-shaping in `filters.py` / `github.py`, in that order.

## 2. What already shipped (so we don't re-propose it)

| Date | What | Still true? |
|---|---|---|
| 2026-06-02 | Cross-theme `claimed` dedup, stale/spam `clean()`, velocity-aware stars fallback | Yes |
| 2026-06-04 | `agent_skill_cap`; LLM-enforced caps **falsified** (5 models); deterministic `is_agent_skill_pack` / `is_ai_repo` | Yes — and the 2026-08 failure mode is the *next* one: standalone AI tools, not packs |
| 2026-06-10 | One theme per 13/16/19 UTC slot; scored 0–10 + `min_score`; query lists; curator model | Yes |
| 2026-06-16 | Curator fallback chain | Yes |
| 2026-06-18 | Movers via `starsnap` + `delta_days` at Sun 19 | Yes; snapshot is per-run-day, so Movers' baseline is mostly last Sunday's Movers search, not a union of the week |
| 2026-08-13 | Momentum / license / owner-type shown to curator and in the meta line | Yes |

**Explicitly rejected before, still rejected:**

- Watching X/HN for "external buzz" and re-broadcasting someone else's ranking
  (`2026-06-18` movers spec, "Non-goal"). Independent metric + own gauntlet stays.
- Pushing "exclude all X / at most N" into the curator prompt. Code enforces;
  the LLM only scores.

**Deferred then, still worth picking up now** (this doc's job):

- HN/social as a *seed* (still no — the query net is the bottleneck, not missing
  viral URLs).
- Per-domain movers (still no — one Movers first; calibrate that).
- Deltas on non-mover themes (nice-to-have; not the first lever).
- GitHub `Retry-After` (reliability; separate from coverage/quality).
- Per-theme Telegram topics.

## 3. Diagnosis

### 3.1 The two pool shapes

GitHub Search `total_count` for each theme's *current* `themes.toml` query
(`{since:Nd}` expanded against 2026-08-20). Search returns at most 50 items per
call; Phase 1 then slices to `CANDIDATE_LIMIT = 30`.

| Theme | Current query (abbrev.) | `total_count` | Shape |
|---|---|---|---|
| trending | `created:>90d stars:>1000` | 414 | Medium, then `cap=0` guts it |
| ai-agents | `topic:ai-agents` + `topic:llm`, 90d | 38k + 43k | Fat |
| dev-tools | `topic:cli` + `developer-tools` + `terminal`, 90d | 26k + 24k + 5k | Fat |
| crypto | `topic:web3` + `blockchain`, 120d | 5.7k + 5.2k | Fat (spammy) |
| finance | `finance created:>180d stars:>100` | **56** | Thin + AI-infiltrated |
| security | `topic:security` + `cybersecurity`, 120d | 15k + 13k | Fat |
| systems | `topic:systems-programming … stars:>10` | **42** | Starved |
| data | `topic:database` + `data-engineering`, `stars:>50` | **12 + 13** | Starved |
| web | `topic:frontend stars:>50` + `topic:webdev stars:>50` | **23 + 0** | Starved; **webdev is a dead query** |
| science | `topic:scientific-computing … stars:>20` | **27** | Starved |
| movers | `created:>120d stars:>500` | 1.4k | OK as a search; delta then drops unknowns |

"Starved" means the curator never sees 30 real candidates. Quiet slots and 1-item
posts (Finance, Data, Science, Trending on the live channel) are the honest
outcome of an empty cupboard, not a scoring bug.

Loosening the floor, same topics, same windows:

| Query | With current floor | Loosened |
|---|---|---|
| `topic:systems-programming` 180d | 42 (`stars:>10`) | **1,422** (no floor) |
| `topic:database` 120d | 12 (`stars:>50`) | **4,270** (no floor) / **102** (`stars:>10`) |
| `topic:frontend` 120d | 23 (`stars:>50`) | **9,778** (no floor) / **96** (`stars:>10`) |
| `topic:scientific-computing` 180d | 27 (`stars:>20`) | **1,361** (no floor) / **73** (`stars:>5`) |
| `finance` 180d | 56 (`stars:>100`) | 90 (`stars:>50`) |
| `topic:webdev` 120d `stars:>50` | **0** | 134 (no floor) |

Complementary topics the starved themes do not search today (all with a modest
floor, 2026-08-20):

| Add | Count | Natural home |
|---|---|---|
| `topic:compiler created:>180d stars:>10` | 131 | systems |
| `language:Rust created:>90d stars:>50` | 506 | systems (noisy — not all systems) |
| `language:Zig created:>180d stars:>10` | 180 | systems |
| `topic:postgresql created:>120d stars:>20` | 101 | data |
| `topic:react created:>120d stars:>50` | 276 | web |
| `topic:simulation created:>180d stars:>10` | 160 | science |
| `topic:quant` / `topic:trading` / `topic:finance` | 1,015 / 97 / 98 | finance |

### 3.2 Fat-theme top-30 is already the wrong 30

Fetched `sort=stars`, `per_page=30`, then ran today's `is_ai_repo` /
`is_agent_skill_pack`:

| Query | AI / 30 | Packs / 30 | Empty desc | No topics |
|---|---|---|---|---|
| trending `stars:>1000` | 17 | 3 | 1 | 14 |
| `topic:cli` (dev-tools) | **24** | 3 | 1 | 0 |
| finance `stars:>100` | **21** | **12** | 1 | 5 |
| systems current (all 30 of 42) | 7 | 0 | 0 | 0 |
| database current (all 12) | 4 | 0 | 0 | 0 |

`agent_skill_cap = 2` keeps **every non-pack**, including 21 AI CLIs. After the
cap, Dev Tools still hands the curator a wall of agent harnesses. That matches
the live channel: Dev Tools posts (Qwen Audio Agent, Phone Harness, Fuxi, Agent
Manager, BossConsole, …) are coding-agent products, not CLIs.

GitHub's `-topic:ai -topic:llm -topic:ai-agents` is a **weak** lever (`topic:cli`
only dropped 26k → 19k). Most of the flood is not tagged `ai`; it is tagged
`cli` and *is* an agent. Exclusion belongs in `filters.py` after fetch — but
fetching only the starriest 50 means the non-AI remainder is ~6 repos. **We have
to fetch a wider page (or several star-band queries) before the cap**, or the
cap has nothing to keep.

### 3.3 Live channel failure modes (public scrape, mid-August 2026)

These are the user-visible bugs the numbers predict:

- **Trending underfill + leak.** One-item Trending posts. `MiniMax-AI/MiniMax-H3`
  (6.5k★, **empty description, empty topics**, org name contains `AI`) shipped as
  the non-AI highlight; the blurb then called it a prompt-writing skill. Today's
  `is_ai_repo` does not look at the owner login and does not treat a bare `ai`
  token as a marker, so `cap=0` never saw it.
- **Theme colonisation.** Dev Tools, Data (`ktx`, `pgbot`), Security
  (`AgentGuard`, `skilldoctor`) read as AI-adjacent even when they are "in
  domain."
- **Cross-day duplicates.** `unsent` is keyed by `theme.key`. `fuxicodex/Fuxi`
  appeared in both Dev Tools and AI & Agents on different days. `claimed` only
  dedups *within one cron hour*, and the grid is one theme per hour, so
  cross-theme repeats are free.
- **Off-theme survivors of a thin pool.** Systems posted a Windows temp-file
  cleaner (`Quiesce`) — when the cupboard is 42 repos, `min_score=6` still fills
  from leftovers.
- **Low-star crypto.** 8★ posts. Fat query, but after spam/skill filters and
  `unsent` the remainder is thin and the bar does not save it.
- **Velocity theatre.** `deepseek-ai/deepseek-harness` showed as `88,057★/day`
  (same-day creation). Velocity is a *fallback* partition only
  (`VELOCITY_CEILING = 2500`); the LLM still scores the repo, and the formatter
  still prints the badge. Harmless as data, noisy as a reader signal.

### 3.4 What is *not* the bottleneck

- Model strength. Scoring-with-a-bar already works when the pool is mixed.
  Quiet slots are correct.
- More cron frequency on the same queries. 2×/week per theme is fine; the second
  firing is empty when `unsent` has nothing new — that's working as designed.
- README/summary quality as the first lever. Blurbs are downstream of *which*
  repos were picked.
- External social graphs. The query net already catches viral GitHub URLs; it
  fails at *sampling and shaping* them.

## 4. Goals / non-goals

**Goals**

- Every scheduled theme, most weeks, presents the curator ≥ 20 *on-theme,
  post-filter* candidates — enough for `min_score` to mean something.
- Non-AI themes are not visually interchangeable with AI & Agents.
- Trending's `cap=0` does not ship an AI model card / skill pack because of
  empty metadata.
- A repo posted under any theme is not re-posted under another theme for a
  cooling-off window (configurable).
- New domains (mobile, gamedev, robotics, …) are added only once the existing
  ten can fill, and only with a slot-grid plan.

**Non-goals**

- Scraping HN, X, GitHub Trending HTML, or star-history.io.
- LLM-enforced diversity / "at most N AI."
- Per-user personalization, extra Telegram topics, or a web UI.
- Backfilling star history for Movers.
- Changing the graceful-degradation contract (LLM down → stars fallback + alert).

## 5. Design — four tracks

Tracks are independent. Ship A before B before C; D is opportunistic.

### Track A — Feed the starved themes (`themes.toml` only)

The June 10 "wider net" lever added extra `topic:`s but left **stars floors that
zero out the extra queries** (`topic:webdev stars:>50` → 0 hits). The quality bar
exists specifically so we can widen the net and let scores drop the junk.

**A1. Kill dead / tautological floors**

| Theme | Change |
|---|---|
| web | Drop `topic:webdev created:>{since:120d} stars:>50`. Replace with `topic:react created:>{since:120d} stars:>20` (276 hits) and/or `topic:javascript` / `topic:css` with a low floor. Lower `topic:frontend` from `stars:>50` → `stars:>10` (96 hits). |
| data | Lower `stars:>50` → `stars:>10` on both queries (102 + similar). Add `topic:postgresql created:>{since:120d} stars:>10`. |
| science | Lower `stars:>20` → `stars:>5`. Add `topic:simulation created:>{since:180d} stars:>5`. |
| systems | Keep `topic:systems-programming` but **drop `stars:>10`** (42 → 1.4k, curator still only sees 30). Add `topic:compiler created:>{since:180d}`. Optionally `language:Zig created:>{since:180d} stars:>10` (small, on-theme). **Do not** dump `language:Rust stars:>50` (506) into systems without a dry-run — it will be CLI/web/AI Rust. |
| finance | Lower `stars:>100` → `stars:>50`. Add `topic:quant` and `topic:trading` as list entries (keyword `finance` misses tagged quant). |
| trending | Optional, after cap hardening (Track B): `stars:>1000` → `stars:>500` (414 → 904) so `cap=0` has a larger non-AI remainder. Do **not** do this before B1/B2 or Trending becomes a slightly larger AI leak. |

**A2. Dual window for "old repo, new life"**

Every starved theme's query is `created:>`. A compiler that is 3 years old and
just shipped a 1.0 is invisible. Add a second query per starved theme:

```
topic:compiler pushed:>{since:30d} stars:>50
```

(window and floor tuned per theme). Merge is already implemented. Cost: +1 Search
call per slot, well under 30 req/min.

**Validation:** `--dry-run` with `--themes` stripping `at`, `OLLAMA_HOST=`
(stars fallback is enough to see *pool size* post-`clean`/`cap`). Log
`len(repos)` after each Phase-1 step (today we only log "none above the quality
bar"). Accept when each starved theme has ≥ 20 post-cap candidates on two
consecutive dry-runs.

### Track B — Shape fat-theme pools (deterministic, not prompt)

**B1. `ai_cap` distinct from `agent_skill_cap`**

Today one field means two policies (`0` = drop all AI; `N` = drop packs after N).
The 2026-08 problem needs a third: *at most N AI repos, packs or not*.

```python
# Theme.ai_cap: int | None = None
# None → unchanged (AI & Agents, and today's cap=N themes until they opt in)
# 0    → same as today's agent_skill_cap=0 (drop is_ai_repo)
# N    → keep all non-AI, plus at most N is_ai_repo (order preserved)
```

`agent_skill_cap` stays as the *pack* cap and still applies. Suggested prod
values after a dry-run, not before:

| Theme | `agent_skill_cap` | `ai_cap` |
|---|---|---|
| trending | 0 (or drop, use `ai_cap=0`) | **0** |
| ai-agents | None | None |
| movers | 2 | 3–4 (it's a breakout digest; AI is allowed, not a monopoly) |
| everything else | 2 | **2** (or 1) |

Enforcement is `cap_agent_skills`-shaped: walk the list, keep non-matches,
count matches, skip once the cap is hit. Unit-test a truth table. **Do not**
put "at most 2 AI" in the profile.

**B2. Fetch more than the starriest 50, then cap**

`search_repos(..., per_page=50)` → `per_page=100` (Search API max). Optionally
a second page (`page=2`) only for themes with `ai_cap` set — two calls, 200
candidates, then `clean` → `ai_cap` → `CANDIDATE_LIMIT`.

Alternative, same idea: **star-band queries** that the merge path already
supports:

```toml
query = [
  "topic:cli created:>{since:90d} stars:>1000",
  "topic:cli created:>{since:90d} stars:100..400",
]
```

Band 2 is where non-AI CLIs actually live. Prefer this over `sort=updated` as
the first experiment (updated is "what pushed," not "what's interesting").

**B3. Harden `is_ai_repo` for the empty-metadata leak**

High-precision additions, each with a unit test, aimed at Trending `cap=0`:

1. Owner login / org name: token `ai` as a path segment (`MiniMax-AI`,
   `langchain-ai`, `deepseek-ai`). `\bai\b` on the **owner only**, not the
   whole blob (false positives: `details`, `email`, `available` in
   descriptions).
2. Empty description **and** empty topics **and** (name or owner matches an
   AI marker, or README first line — see 3) → treat as AI for `cap=0` only.
   Do not apply the empty-metadata rule under `ai_cap=N`; too aggressive.
3. Optional, Trending-only: if still empty after (1), fetch `readme_first_line`
   for the remaining `cap=0` candidates (Trending's post-search N is small)
   and run markers against it. Extra API calls only on the thin path.

Do **not** add a generic `\bai\b` matcher on descriptions. It will eat
"email", "details", "available".

**B4. Global cooling-off**

`state.json` stays per-theme (a finance repo can still appear in finance after
it appeared in trending two months ago — that's fine). Add a second, flat list
`state["_posted"]` of repo ids posted by *any* theme, capped (e.g. 2000).
Phase 1 drops ids in `_posted` for every theme except the one that originally
… actually, simpler: drop for **all** themes. A repo gets one appearance in
the channel, period, until it ages out of the cap.

Movers: either honor `_posted` (no "mover" that already ran in Trending) or
exempt Movers so a true breakout can re-appear with a `+N★` badge. Recommend
**exempt Movers** — that's the one digest whose job is "what blew up," even if
we already mentioned it. Document the exception.

This kills the Fuxi double-post. Tests: a repo recorded under `ai-agents` is
absent from `dev-tools` on a later `now`.

### Track C — New domains & schedule (only after A+B)

Candidate themes with a *loosened* query (no aggressive stars floor) — pool is
large enough to exist; quality unknown until a dry-run:

| Theme | Probe query (loosened) | `total_count` |
|---|---|---|
| robotics | `topic:robotics created:>180d` | 6.1k |
| gamedev | `topic:game-development created:>180d` | 4.9k |
| embedded / hardware | `topic:embedded created:>180d` | 3.2k |
| audio / graphics | `topic:audio` / `topic:graphics` + floor | 193 / 59 with floor; larger without |
| privacy | `topic:privacy created:>120d stars:>20` | 198 |
| android / mobile | `topic:android stars:>50` + `topic:ios` | 215 + 98 |

Do **not** add `topic:machine-learning` as a sibling of AI & Agents — it will
duplicate. Do **not** add a generic "Rust" theme; put Zig/compiler into
systems via Track A.

**Slot math:** 21 slots, 11 themes, grid full. Options, in preference order:

1. **Steal one firing** from a 2×/week theme that is consistently thin even
   after Track A (likely Science or Finance) → one new theme at 1×/week.
2. **Fourth cron hour** (`0 10,13,16,19` or `13,16,19,22`) → +7 slots/week.
   Only if the channel wants four posts/day. `SEND_DELAY` is irrelevant
   (still one theme per hour).
3. Collapse two related themes (e.g. keep systems, don't add "languages")
   rather than growing forever.

One-per-slot invariant stays. New `[[theme]]` + unique `at` entries, same as
v3.

### Track D — Quality bar, signals, eval (opportunistic)

**D1. Per-theme `min_score`.** Crypto and Movers already think in "be
aggressive." Crypto should try `min_score = 7` once A/B give it a cleaner
pool (raising the bar on an 8★ leftover pool just silences the slot — that's
OK). Finance/systems/science stay at 6 until pools grow.

**D2. Velocity badge.** Don't print `★/day` when `age_days < 2` (the
88,057★/day case). Formatter-only; tests at the 1d/2d boundary. The curator
still *sees* velocity in the listing.

**D3. Phase-1 counters.** Log, at INFO, for every firing:

```
theme {key}: searched={n} after_clean={n} after_unsent={n} after_cap={n} picked={n}
```

No repo names (noise). This is how we notice starvation without scraping
Telegram. Quiet-bar vs empty-search become distinguishable.

**D4. Eval harness (optional, offline).** Notion DB
[Interesting Repos](https://app.notion.com/p/37e000c0d59081dabd9dcd15aede9a22)
is a 379-row log of *what we posted* (2026-05-27 → 2026-06-25), not a
ground-truth of what we *should* have posted. It is the wrong shape for a
Grant-Wires-style recall audit. A useful eval is instead:

- freeze a JSON fixture of one Search page per theme (committed under
  `tests/data/`),
- snapshot `clean` / `ai_cap` / picked-ids,
- fail CI when a known leak (`MiniMax-H3`, `finance-skills`) re-enters a
  `cap=0` / `ai_cap=2` pool.

Do not call live GitHub from CI.

**D5. GitHub `Retry-After`.** Still the right reliability fix; does not
change coverage. Own PR, copy `telegram.py`'s backoff shape.

**D6. Snapshot watchlist (Movers coverage).** `find_baseline` reads **one**
day's file. Weekday runs snapshot only that hour's theme, so a repo that
crossed `stars:>500` mid-week has no Sunday-7 baseline if it wasn't in last
Sunday's Movers page. Optional: every run, also search a cheap watch
query (`created:>{since:120d} stars:>100`, `per_page=100`) and fold into
`today_snap`. Rebuilds Movers' memory of the mid-tier. Own PR; quiet until
the next Sunday.

## 6. What we will not do (and why)

| Idea | Why not |
|---|---|
| HN / X / GitHub Trending HTML as a source | Fragile, ToS-y, duplicates Search, was already the rejected "re-broadcast" path. |
| Stronger curator prompt to "prefer non-AI" | Falsified June 4 across 5 models. |
| Raise `CANDIDATE_LIMIT` to 100 and stuff the prompt | Prompt quality and latency; shape the pool *before* `rank()`. 30 scored well is the point. |
| `sort=updated` as the primary order | Surfaces maintenance churn, not breakouts. Fine as a *second* merged query later. |
| Star-history API backfill | Slow, ToS-fragile; Movers' cold-start contract is accepted. |
| Per-domain Movers | One general Movers; split only if that digest is rich *and* the channel wants it. |
| Interactive bot / per-subscriber feeds | Push-only is the product. |

## 7. Error handling / safety

- Channel is live. All validation is unit tests or `--dry-run` (dummy Telegram
  ids, `STATE_DIR` temp, throwaway state so we don't poison prod `unsent`).
- Track A is config-only: a bad query is a thin slot, not a crash. Invalid
  `at` still fails at load.
- Track B `ai_cap` default `None` → byte-identical to today for un-migrated
  themes.
- Global `_posted` must not be written on `--dry-run` (same as `state.json`
  and `starsnap`).
- Extra Search pages: one theme per slot, ≤3 calls/run even with A2+B2+D6.
  Authenticated Search is 30 req/min; we stay far below.

## 8. Recommended PR sequence

1. **A1 query retune** (`themes.toml`): dead `webdev`, lower floors on
   data/web/science/finance/systems, add compiler / postgres / quant / react /
   simulation. Plus **D3** pool-size logs (otherwise we can't see if A1
   worked). Dry-run, then ship.
2. **B3 classifier leak** + a `tests/data` fixture containing MiniMax-H3-shaped
   metadata. Fixes Trending independently of pool size.
3. **B1 `ai_cap` + B2 wider fetch** for non-AI themes. Dry-run Dev Tools /
   Finance / Security side by side vs main. Tune N.
4. **D2** velocity-badge age floor (tiny, can piggyback on 2 or 3).
5. **B4** global cooling-off (behavior change readers will notice — call it
   out in the rollout note).
6. **A2** dual `pushed:` window, theme by theme, only where A1 still underfills.
7. **C** new theme + slot, only with a named hole in the grid.
8. **D5 / D6** as housekeeping.

Do not bundle 1–5. Each needs a live dry-run against the real Search pool.

## 9. Testing

Project convention: TDD, HTTP mocked, no live GitHub in CI.

- **A1:** `load_themes` still parses query lists; a small unit test that
  `webdev` is absent from the loaded web theme (guards regression).
- **B1:** `cap_ai(repos, n)` truth table analogous to `cap_agent_skills`.
- **B2:** `search_repos` pagination / `per_page` (extend `test_github.py`).
- **B3:** MiniMax-H3-shaped `Repo` is `is_ai_repo`; a non-AI org with `ai` inside
  a description word (`"available"`) is not.
- **B4:** `test_main` that a repo in `_posted` is skipped by a later theme;
  Movers exempt if that's the chosen policy; dry-run does not persist.
- **D2:** `_format_momentum` returns `None` when caller passes a sentinel, or
  `main` passes `None` for `age_days < 2`.
- **D3:** log-call assertion with `caplog`.

## 10. File change map (when we implement)

| File | Tracks |
|---|---|
| `themes.toml` | A1, A2, C |
| `bot/filters.py` | B1, B3 |
| `bot/config.py` | B1 (`ai_cap`), B4 (nothing if `_posted` is a magic key in state) |
| `bot/github.py` | B2 pagination |
| `bot/main.py` | B1 apply, B2, B4, D3, D6 |
| `bot/formatter.py` | D2 |
| `bot/state.py` | B4 |
| `bot/starsnap.py` | D6 only |
| `railway.json` | C if fourth hour |
| `tests/` | all |
| `CLAUDE.md` | module-map / grid once C or B1 ships |

This research PR adds only this spec.

## 11. Rollout note (for whichever PR goes first)

Off-schedule live send is still the throwaway-`STATE_DIR` Railway pattern.
Prefer `--dry-run` with a themes copy that has `at` stripped so the clock
doesn't hide the theme under test.
