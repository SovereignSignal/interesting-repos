# Design: Curation v2 (mechanical fixes)

- **Date:** 2026-06-02
- **Status:** Draft (brainstorming) — awaiting user review
- **Owner:** sov@sovereignsignal.com
- **Builds on:** `2026-05-26-telegram-repo-bot-design.md`

## 1. Summary

Three tightly-scoped, deterministic improvements to the existing weekly digest's
**curation**, motivated by reviewing the live discovery pool (2026-06-02) and the
real production digest (Mon 2026-06-01 run):

1. **Cross-theme dedup + ordering** — a repo appears in at most one theme per run,
   and "Trending Overall" is selected *last* so it becomes genuinely cross-domain.
2. **Recency-awareness** — restore `created_at`/`pushed_at`, surface repo age and
   star-velocity to the curator, and drop stale repos (not pushed in 60 days).
3. **Deterministic spam pre-filters** — drop keyword-stuffed and awesome-list repos
   before ranking, and keep the stars-fallback from ever leading with a star-farm.

No model change, no theme redesign, no presentation change. Those are deferred
(see §10).

## 2. Motivation (what the live run showed)

The LLM curator already excludes the worst star-farms (the 193K-star "enjoy the
party" `claw-code`, the 106K `gstack`, the 86K `awesome-design-md`). It is *not*
broken. But the 2026-06-01 digest exposed three concrete defects:

| Defect | Evidence in the 2026-06-01 digest |
|---|---|
| **Cross-theme duplication** | `nexu-io/open-design`, `santifer/career-ops`, and `mvanhorn/cli-printing-press` each filled **two** theme slots — 6 of 42 wasted — and got a *different* AI title in each ("Open Design Platform" vs "Open Design Tool"), which reads like a bug. |
| **Trending ≈ AI & Agents** | Both themes were 7/7 AI-agent repos; back-to-back they felt redundant. |
| **Recency blindness** | `karpathy/autoresearch` led the whole digest at ⭐84,470 despite being **pushed 69 days ago** and *losing* stars (84,783 → 84,470). The `Repo` record had dropped `pushed_at`, so the curator could not see it. |

A fourth, latent risk: the curator's fallback is top-by-stars. On any Ollama
hiccup, the digest would lead with the 193K "enjoy the party" repo. The current
design has no deterministic floor under that failure mode.

## 3. Goals / Non-goals

**Goals**
- No repo appears in more than one theme in a single run.
- Each repo gets exactly one AI title per run (falls out of dedup for free).
- "Trending Overall" is cross-domain by construction, while still displayed first.
- The curator can see and act on recency (age, last-push, star-velocity).
- Repos not pushed within 60 days are dropped before ranking.
- Keyword-stuffed and awesome-list repos are dropped deterministically.
- The stars-fallback never *leads* with an implausible-velocity star-farm.
- All behavior is covered by tests that mock HTTP (no live calls), matching the
  existing 59-test suite.

**Non-goals (YAGNI / deferred)**
- No curation-model upgrade or per-repo scoring (Approach C).
- No theme reshaping beyond a `catch_all` flag (Approach B).
- No "why interesting" line, images, or other presentation work.
- No GitHub `Retry-After` handling (a reliability item, separate iteration).

## 4. Detailed design

### 4.1 Cross-theme dedup + ordering (the `main.py` restructure)

Today `main.run` selects and sends per theme in a single loop. Split it into two
phases so that *which theme owns a repo* is decoupled from *display order*:

**Phase 1 — select** (iterate in *selection order*: non-`catch_all` themes first,
`catch_all` themes last; stable otherwise):
```
claimed: set[int] = set()          # repo ids already taken THIS run
results: dict[str, list[Repo]] = {} # theme.key -> picked repos
for theme in sorted(config.themes, key=lambda t: t.catch_all):
    repos = search_repos(expand_since(theme.query, today), ...)
    repos = [r for r in repos if not r.is_fork and not r.is_archived]
    repos = filters.clean(repos, today, theme.max_idle_days)   # 4.3 + 4.2
    repos = unsent(state, theme.key, repos)                    # cross-week dedup
    repos = [r for r in repos if r.id not in claimed]          # cross-theme dedup
    repos = repos[:CANDIDATE_LIMIT]
    picked = rank(repos, theme, today, ollama_*...)
    results[theme.key] = picked
    claimed.update(r.id for r in picked)
```

**Phase 2 — deliver** (iterate in *display order* = `config.themes` order, Trending
first):
```
for theme in config.themes:
    picked = results.get(theme.key) or []
    if not picked:
        log.info("theme %s: no new repos", theme.key); continue
    titles = make_titles(picked, ...)
    messages = build_messages(theme, picked, describe, translate, titles)
    if dry_run:
        print each message; continue
    for m in messages: send_message(...)
    state = record_sent(state, theme.key, [r.id for r in picked])
    save_state(state_path, state)
```

Because `trending` is the only `catch_all` theme, it is selected after all specific
themes have claimed their repos, so it cannot duplicate them and naturally fills
its slots with the best *cross-domain* repos that no category took — yet it still
displays first. The state-after-send crash-safety tradeoff from the original design
is preserved (state is recorded only after a theme's messages are sent).

**Title-once falls out for free:** each repo is now owned by exactly one theme, so
no repo is titled twice. No separate title cache is needed.

### 4.2 Recency-awareness

- `Repo` regains `created_at: str` and `pushed_at: str` (ISO strings straight from
  the Search API). `parse_repo` reads `item["created_at"]` / `item["pushed_at"]`,
  defaulting to `""` if absent.
- `filters.clean` drops any repo whose `pushed_at` is older than `max_idle_days`
  (default 60). A repo idle exactly 60 days is **kept**; 61 days is dropped.
  Unparseable/missing `pushed_at` is treated as **not stale** (kept), so a parsing
  gap never silently empties a theme.
- The ranker's candidate listing gains age and velocity, e.g.:
  `3. owner/name (★12,300, 14d old, pushed 2d ago, 878★/day) — description [topics]`
  (computed via `filters.age_days` / `filters.star_velocity`, so all date logic
  lives in one module).
- The ranker prompt gains: *"Prefer repos that are fresh and actively maintained
  (recently pushed). Discount repos that look stale or whose stars grew
  implausibly fast (very high ★/day) — those are often star-farmed."*

### 4.3 Deterministic spam pre-filters (`bot/filters.py`, new)

A small, dependency-free module that owns all deterministic candidate pruning, so
`github.py` stays focused and the rules are unit-testable in isolation.

```
KEYWORD_REPEAT_THRESHOLD = 5     # a length>=3 token repeated this many times
VELOCITY_CEILING = 2500.0        # ★/day above which a repo is a fallback outlier

def age_days(iso: str, today: date) -> int | None
    # days between iso's date and `today`; None if unparseable

def star_velocity(repo, today) -> float
    # repo.stars / max(age_days(repo.created_at, today) or 1, 1)

def is_keyword_stuffed(repo) -> bool
    # lowercase + split desc on non-alphanumerics; True if any token of length>=3
    # occurs >= KEYWORD_REPEAT_THRESHOLD times. Catches "hyperliquid sdk |
    # hyperliquid sdk | ..." and "polymarket clob | ...".

def is_awesome_list(repo) -> bool
    # name part (after '/') lowercased starts with "awesome-" or == "awesome",
    # OR topics contains "awesome-list" or "awesome".

def is_stale(repo, today, max_idle_days) -> bool
    # (age_days(repo.pushed_at, today) or 0) > max_idle_days

def clean(repos, today, max_idle_days) -> list
    # drop is_keyword_stuffed / is_awesome_list / is_stale; keep the rest, order
    # preserved.
```

**Velocity is a soft signal, not a hard drop.** It is shown to the LLM (4.2) and
used only to protect the *fallback*: `rank_by_stars` partitions candidates into
normal vs. velocity-outliers (`> VELOCITY_CEILING`), takes from normal first, and
draws on outliers only to fill remaining slots. This guarantees a non-empty result
while ensuring a degraded run never *leads* with a star-farm. (Rationale: velocity
is low-precision — a real hit like `open-design` runs ~1,600★/day, close to a
farm's ~3,000 — so a hard cutoff would nuke legit repos. Keyword-stuffing and
awesome-list are high-precision, hence safe to hard-drop.)

## 5. Configuration changes

`Theme` gains two optional fields (both with safe defaults, so existing themes and
tests are unaffected unless they opt in):

| Field | Type | Default | Meaning |
|---|---|---|---|
| `catch_all` | bool | `False` | Select this theme *after* all non-catch-all themes; it cannot duplicate their picks. |
| `max_idle_days` | int | `60` | Drop candidates not pushed within this many days. |

`load_themes` reads `t.get("catch_all", False)` and `t.get("max_idle_days", 60)`.

`themes.toml`: add `catch_all = true` to the `trending` theme. No other theme
changes.

## 6. Data flow (new two-phase run)

1. Load config + state once.
2. **Select** every theme in selection order (catch-all last), threading a
   run-level `claimed` set: search → drop forks/archived → `filters.clean`
   (spam + stale) → `unsent` (cross-week) → drop `claimed` (cross-theme) → cap to
   `CANDIDATE_LIMIT` → `rank`. Record picks in `results`; add their ids to
   `claimed`.
3. **Deliver** every theme in display order (`themes.toml` order): build titles +
   messages, send (or print on `--dry-run`), then record state + save.
4. Per-theme isolation and the non-zero-exit-on-failure behavior are unchanged.

## 7. Edge cases & accepted tradeoffs

- **Theme order = claim priority.** If a repo matches two specific themes, the one
  earlier in `themes.toml` claims it. Deterministic and acceptable.
- **Failed send.** If a theme's send fails, its claimed repos are absent that week
  (in any theme) and remain eligible next week (state not updated). Acceptable for
  a weekly digest.
- **Trending underfill.** If most candidates were already claimed, Trending may
  send fewer than `count` repos. Acceptable.
- **Over-pruning.** Staleness (60d) or velocity could rarely drop a good repo;
  mitigated by generous defaults and velocity being soft (fallback-only).
- **Unparseable dates** are treated as *not stale* and *zero-ish age*, never
  crashing or silently emptying a theme.

## 8. Testing (TDD; all HTTP mocked)

- **`test_filters.py` (new):** keyword-stuffed true/false (incl. the pipe-repeat
  case); awesome-list by name and by topic; `is_stale` boundary (60 kept, 61
  dropped, missing date kept); `star_velocity` math; `age_days` parsing + `None`
  on garbage; `clean` drops the right repos and preserves order.
- **`test_github.py`:** `parse_repo` populates `created_at`/`pushed_at`, defaults
  to `""` when absent.
- **`test_ranker.py`:** the candidate listing includes age + ★/day; the
  stars-fallback deprioritizes a velocity outlier (a 193K-in-63-days repo ends up
  after normal repos and only appears if slots remain).
- **`test_main.py`:** a repo present in two themes' results is kept by only the
  earlier-selected theme (cross-theme dedup); a `catch_all` theme is selected last
  but delivered in `themes.toml` order; `--dry-run` still sends nothing and touches
  no state.

## 9. File change map

| File | Change |
|---|---|
| `bot/github.py` | Add `created_at`, `pushed_at` to `Repo` + `parse_repo`. |
| `bot/filters.py` | **New.** Deterministic pruning + age/velocity helpers (§4.3). |
| `bot/ranker.py` | Listing shows age + ★/day; prompt prefers fresh/active; fallback deprioritizes velocity outliers. `rank` gains a `today` arg. |
| `bot/config.py` | `Theme.catch_all`, `Theme.max_idle_days`; `load_themes` reads them. |
| `bot/main.py` | Two-phase select/deliver with run-level `claimed` dedup and catch-all-last ordering. |
| `themes.toml` | `catch_all = true` on `trending`. |
| `tests/` | New `test_filters.py`; extend `test_github`, `test_ranker`, `test_main`. |

## 10. Out of scope (future iterations)

- **B (structural):** reshape/replace "Trending Overall," add a per-digest
  diversity cap so no theme is all agent-skills.
- **C (model):** stronger Ollama Cloud curation model and/or per-repo scoring with
  a "why interesting" line.
- GitHub `Retry-After`/backoff; presentation/preview images; failure alerting.
