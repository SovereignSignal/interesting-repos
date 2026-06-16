# Automatic Curator-Model Fallback — Design

**Date:** 2026-06-16
**Status:** Approved, pending implementation
**Ships as:** one PR (`curator-fallback`)

## Problem

The curator model is a single configured value (`OLLAMA_CURATOR_MODEL`, currently
`deepseek-v3.1:671b`). When that model becomes unreachable, the whole run degrades to
stars-only — no quality bar, no why-blurbs. On 2026-06-16 Ollama Cloud **retired**
`qwen3-next:80b` (HTTP 410) with no notice; the bot ran degraded for several slots until
a human swapped the model. The base model (`gemma3:12b`) was reachable the whole time, so
the degradation was avoidable. Provider model retirement is recurring, so the bot should
self-heal across an ordered list of curators before degrading.

## Approach

Resolve the curator model **once per run, at pre-flight**, by walking an ordered list of
candidates and picking the first one that's actually reachable. The whole run (scoring +
summaries) uses that single resolved model, so `rank()` and `make_summaries` are unchanged
— only what model string they receive changes.

### The chain (first reachable wins)

1. Each model in `OLLAMA_CURATOR_MODEL`, parsed as a **comma-list**
   (`"deepseek-v3.1:671b,gpt-oss:120b"` → `["deepseek-v3.1:671b", "gpt-oss:120b"]`). A
   single value is a one-element list — fully backward compatible. Blank ⇒ empty list.
2. The **base model** (`OLLAMA_MODEL`, gemma3:12b) as the final curator rung — still scored
   picks + blurbs, just from a weaker model. Always appended (deduped if already present).
3. If even the base is unreachable → today's **stars-only** path + the existing degraded alert.

### Component

A pure helper in `bot/alerts.py` (next to `llm_reachable`, which it reuses):

```
resolve_curator(host, candidates, base_model, api_key, client=None) -> tuple[str | None, list[str]]
```

- `candidates`: ordered curator models (from `ollama_curator_models`).
- Builds the probe order = `candidates + [base_model]` (base appended once; dedupe preserving order; drop blanks).
- Returns `(first_reachable_model, skipped)` where `skipped` is the list of candidates
  tried-and-failed *before* the one that worked. Returns `(None, all_tried)` if nothing is reachable.
- When `host` is blank: returns `(base_model or None, [])` — no pings, LLM disabled path unchanged.

### Wiring in `main.run`

Where it currently computes `curator_model = config.ollama_curator_model or config.ollama_model`
and the `degraded` pre-flight:

- `curator_model, skipped = resolve_curator(host, config.ollama_curator_models, config.ollama_model, api_key)`.
- `degraded = (not dry_run and host and curator_model is None)` — degraded **only** when the
  whole chain (incl. base) is down. Because base is the last rung, `curator_model is None`
  implies base is down too, so the run is genuinely stars-only and the existing degraded
  alert text stays accurate. This replaces today's separate base + curator pings.
- `rank()` and `make_summaries` receive the resolved `curator_model` (unchanged call sites
  otherwise). When `curator_model is None`, they get `""`/host stays set → ranker's existing
  stars fallback fires.

### Config

`bot/config.py`:
- `Config` gains `ollama_curator_models: tuple[str, ...]` (default `()`), parsed in
  `load_config` from `OLLAMA_CURATOR_MODEL` by splitting on `,`, trimming whitespace,
  dropping blanks.
- The old scalar `ollama_curator_model` field is **removed** (no longer read anywhere).
  `main` no longer needs the `or ollama_model` default — `resolve_curator` appends base.
- Everything else (`OLLAMA_MODEL`, etc.) unchanged.

### Alerting (three outcomes)

| Situation | `curator_model` | `skipped` | Alert | Degraded? |
|---|---|---|---|---|
| Primary reachable | primary | `[]` | none | no |
| Primary down, fallback works | fallback/base | non-empty | **heads-up DM** | no |
| All down (incl. base) | `None` | all | **degraded DM** (existing text) | yes |

Heads-up text (sent post-delivery, alongside the existing alert block, only when
`skipped` is non-empty **and** `curator_model is not None`):

> ⚠️ interesting-repos: curator model(s) `deepseek-v3.1:671b` unavailable — ran on
> `gpt-oss:120b`. Update `OLLAMA_CURATOR_MODEL` in Railway.

No alert when the primary worked. Titles/translation still use `OLLAMA_MODEL` regardless.

## Cost

Pre-flight pings candidates in order, stopping at the first success, plus the base only if
all explicit candidates fail — typically **0 extra calls** (primary works) and at most
`len(candidates)` ping calls when the primary is dead. Negligible.

## Testing (TDD)

`resolve_curator` (pure, mock `client` via httpx MockTransport like existing alert/ollama tests):
- first candidate reachable → returns it, `skipped == []`.
- first dead, second reachable → returns second, `skipped == [first]`.
- all candidates dead, base reachable → returns base, `skipped == candidates`.
- everything dead → `(None, candidates + [base])`.
- base already in candidates → not probed twice (dedupe).
- blank host → `(base_model or None, [])`, no pings.

`config`: `OLLAMA_CURATOR_MODEL="a, b ,c"` → `("a","b","c")`; single value → one-tuple;
unset → `()`.

`main.run` (monkeypatch `resolve_curator`/`llm_reachable`/`rank`/`make_summaries`/sends):
- primary works → no alert, resolved model reaches `rank`+`make_summaries`, titles get base.
- fallback used → heads-up DM fires, run **not** degraded, state saved normally.
- all down → degraded DM fires, stars-only (no heads-up).

## Safety

The channel is **live with followers** ([[channel-is-live-no-test-sends]]). All verification
is unit tests (mocked) or `--dry-run` only. No real send to `TELEGRAM_CHAT_ID`/Slack during
this work.

## Known limitation (accepted)

If the **base model is down but an explicit curator candidate is up**, probing short-circuits
at the working candidate and never discovers the base is down — so titles fall back to
`_prettify` and non-English text stays untranslated, with **no alert**. Accepted because
(a) the base is gemma3:12b, the stable small model, while the churn risk is the exotic large
curators (exactly the case this feature fixes), and (b) the impact is cosmetic (titles/
translation), not the picks. Closing it would require an always-on base ping, which YAGNI.

## Out of scope

- Per-theme curator models.
- Retrying a different model *mid-run* after a transient failure (resolution is once, pre-flight).
- Auto-editing `OLLAMA_CURATOR_MODEL` to drop a dead model (the heads-up DM prompts a human).
