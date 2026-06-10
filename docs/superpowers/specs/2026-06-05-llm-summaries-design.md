# Design: LLM-written repo summaries

- **Date:** 2026-06-05
- **Status:** Approved (brainstorming)
- **Supersedes:** the v1 non-goal "No AI-written summaries" (`2026-05-26-telegram-repo-bot-design.md` §2).

## 1. Summary

Replace each entry's summary line — today the repo's own GitHub description
(translated, README-first-line when empty) — with a concise, factual, **LLM-written
blurb** generated from the repo's description **plus a short README excerpt**. One
batched LLM call per theme (like titles), with graceful fallback to the current
description on any failure.

## 2. Motivation

The live digest showed the authentic description is frequently uninformative
("runs anywhere. uses anything", "Learn it. Build it. Ship it for others.") or an
emoji marketing wall ("🎨 … ⚡ 259+ Skills · ✨ 142+ Design Systems …"). v1 valued
*authenticity*, but authentic ≠ *informative*. The bot already uses the LLM for
titles and translation, so model-written summary text is consistent with where the
codebase already is. Feeding the model the **README** (not just the description) is
what rescues the vague cases — the description alone often has no information to
rewrite.

## 3. Goals / Non-goals

**Goals**
- Every entry gets one concise, factual sentence in plain English.
- Generated from the description **and** a short README excerpt.
- Batched per theme; **graceful fallback** to the current description on LLM
  error / no host (the no-LLM digest is unchanged from today).
- Because blurbs are written in English, the happy path needs **no translation**.

**Non-goals**
- No multi-sentence summaries or per-repo deep analysis.
- No "why interesting" curatorial editorializing (factual *what it is*, not opinion).
- No change to discovery, ranking, caps, dedup, or the send throttle.
- `translate()` is **not** removed — it remains for the fallback path.

## 4. Detailed design

### 4.1 `bot/summaries.py` (new) — `make_summaries`

Mirrors `bot/titles.py`.

```
make_summaries(repos, excerpts, host="", model="", api_key="", client=None) -> list
```

- Returns a list the same length as `repos`; each item is a blurb `str`, or `None`
  when unavailable (caller falls back to the description).
- No `host` or no `repos` → returns `[None] * len(repos)`.
- Listing per repo: `i. full_name — {description}  [README: {excerpt}]`.
- Prompt: *"For each GitHub repository below, write ONE concise, factual sentence
  (≤25 words) in plain English describing what it is and what it does. No marketing
  language, no emoji, no hype. Use the description and README excerpt. Return ONLY a
  JSON array of strings, one per repo, in the same order."*
- Parse the first JSON array from the reply (tolerating fences/prose, like
  `titles`). If it isn't a list, or the length doesn't match `repos`, return
  `[None] * len(repos)`. Otherwise map each element to a non-empty `str` or `None`.
- Any `chat()` error / empty reply → `[None] * len(repos)`.

### 4.2 `bot/github.py` — `readme_excerpt`

```
readme_excerpt(full_name, token="", client=None, max_chars=600) -> str
```

- GETs `/repos/{full_name}/readme` (raw), iterates lines, skips `_is_noise` lines
  (reuses the existing helper — headings, badges, rules), joins the real lines with
  single spaces until ~`max_chars`, returns the trimmed result.
- `""` on any `httpx.HTTPError` (so a missing/huge README never breaks the run).

### 4.3 `bot/formatter.py` — use the blurb

- `build_messages(theme, repos, describe, translate=lambda s: s, titles=None, summaries=None)`.
- `summaries=None` → treated as `[None] * len(repos)` (backward compatible).
- `_entry(repo, title, summary, describe, translate)`:
  ```python
  desc = summary or translate(repo.description or describe(repo) or "")
  ```
  i.e. use the blurb when present; otherwise today's behavior exactly. Rendering
  (heading, ⭐/lang/full_name meta, escaping, 4096 split) is unchanged.

### 4.4 `bot/main.py` — wiring (Phase 2)

Per theme, before `build_messages`:
```python
excerpts = [readme_excerpt(r.full_name, token=config.github_token) for r in picked]
summaries = make_summaries(picked, excerpts, host=config.ollama_host,
                           model=config.ollama_model, api_key=config.ollama_api_key)
titles = make_titles(picked, host=config.ollama_host,
                     model=config.ollama_model, api_key=config.ollama_api_key)
messages = build_messages(theme, picked, describe, translate, titles, summaries)
```

## 5. Data flow

Discovery / selection / caps / dedup are unchanged. Delivery per theme: **fetch
README excerpts → `make_summaries` (batched) → `make_titles` → `build_messages`
(blurb per entry, falling back to the description) → throttled send**.

## 6. Error handling / fallback

| Failure | Behavior |
|---|---|
| README fetch error | excerpt `""`; the model works from the description alone |
| LLM error / no host / bad JSON / length mismatch | `make_summaries` → all `None` → formatter falls back to `translate(description or readme-first-line)` (today's behavior) |
| A single blurb empty/`None` | that entry falls back; others keep their blurbs |

The no-LLM digest is therefore byte-identical to today's fallback rendering.

## 7. Cost / performance

~70 README fetches/week (authenticated — far inside GitHub limits) + 10 batched LLM
calls. Adds ~30–60s to the weekly cron. Acceptable for a once-a-week job.

## 8. Testing (TDD; all HTTP mocked)

- **`test_summaries.py` (new):** blurbs parsed from a mocked JSON array; `[None]*`
  on no-host, on `chat` error, on non-list reply, and on length mismatch; tolerates
  code fences/prose.
- **`test_github.py`:** `readme_excerpt` joins non-noise lines up to `max_chars`;
  `""` on a 404.
- **`test_formatter.py`:** `_entry` uses the blurb when present and falls back to the
  description when `summary is None`; `build_messages` with `summaries` still splits
  correctly under 4096 and stays backward compatible when `summaries=None`.
- **`test_main.py`:** delivery fetches excerpts and calls `make_summaries`, and the
  resulting blurb reaches the sent message.

## 9. File change map

| File | Change |
|---|---|
| `bot/summaries.py` | **New.** `make_summaries` (batched blurbs, graceful fallback). |
| `bot/github.py` | Add `readme_excerpt`. |
| `bot/formatter.py` | `build_messages`/`_entry` accept + use `summaries`. |
| `bot/main.py` | Phase 2 fetches excerpts + calls `make_summaries`. |
| `tests/` | New `test_summaries.py`; extend `test_github`, `test_formatter`, `test_main`. |

## 10. Out of scope (future)

- A "why interesting" curatorial line (opinion, distinct from this factual blurb).
- A stronger model for relevance ranking / selection quality.
- Per-repo preview images or cards.
