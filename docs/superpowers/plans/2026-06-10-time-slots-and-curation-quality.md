# Time Slots & Curation Quality Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Spread the digest to one theme per time slot (13/16/19 UTC) and raise pick quality via scored LLM curation with a deterministic quality bar, "what + why" blurbs, a stronger curator model, and multi-query search.

**Architecture:** Two PRs. PR 1 extends the stateless weekday schedule with an hour dimension (`days` → `at`, cron fires 3×/day). PR 2 changes the curator from "pick N" to "score all 0–10 + why"; deterministic code gates at `min_score`, the why-lines feed the summaries, a separate `OLLAMA_CURATOR_MODEL` handles scoring/summaries, and themes can search multiple GitHub queries.

**Tech Stack:** Python 3.11, httpx, pytest (mock transports + monkeypatch), tomllib, Ollama Cloud, Railway cron.

**Spec:** `docs/superpowers/specs/2026-06-10-time-slots-and-quality-design.md`

**Implementation note (deviation from spec, intent preserved):** the spec names the multi-query field `Theme.queries`. To avoid churning every `Theme(query=...)` construction in code and tests, the field stays `query` and simply accepts a string **or** a tuple of strings (TOML list → tuple). `main` normalizes with `isinstance(theme.query, tuple)`.

All commands run from the repo root: `/Users/home/Documents/Sovereign Signal/repos/interesting-repos`.
Run tests with: `.venv/bin/python -m pytest` (or plain `pytest` if the venv is active).

---

# Part 1 — PR 1: Time-slot scheduling

### Task 0: Branch

- [ ] **Step 0.1: Create the feature branch**

```bash
git checkout main && git pull && git checkout -b time-slots
```

### Task 1: Parse `at` in config (additive — `days` untouched for now)

**Files:**
- Modify: `bot/config.py` (Theme dataclass ~line 18, `load_themes` ~line 35)
- Test: `tests/test_config.py`

- [ ] **Step 1.1: Write the failing tests** — append to `tests/test_config.py`:

```python
def test_load_themes_parses_at_to_weekday_hour_pairs(tmp_path):
    p = tmp_path / "t.toml"
    p.write_text('[[theme]]\nkey="k"\nname="N"\nquery="q"\nat=["mon 13", "thu 16"]\n')
    assert load_themes(str(p))[0].at == ((0, 13), (3, 16))   # (weekday, UTC hour)


def test_load_themes_at_absent_is_none(tmp_path):
    p = tmp_path / "t.toml"
    p.write_text('[[theme]]\nkey="k"\nname="N"\nquery="q"\n')
    assert load_themes(str(p))[0].at is None


def test_load_themes_at_invalid_day_exits(tmp_path):
    p = tmp_path / "t.toml"
    p.write_text('[[theme]]\nkey="k"\nname="N"\nquery="q"\nat=["funday 13"]\n')
    with pytest.raises(SystemExit):
        load_themes(str(p))


def test_load_themes_at_invalid_hour_exits(tmp_path):
    p = tmp_path / "t.toml"
    p.write_text('[[theme]]\nkey="k"\nname="N"\nquery="q"\nat=["mon 24"]\n')
    with pytest.raises(SystemExit):
        load_themes(str(p))


def test_load_themes_at_malformed_entry_exits(tmp_path):
    p = tmp_path / "t.toml"
    p.write_text('[[theme]]\nkey="k"\nname="N"\nquery="q"\nat=["mon"]\n')
    with pytest.raises(SystemExit):
        load_themes(str(p))
```

- [ ] **Step 1.2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_config.py -v -k at`
Expected: FAIL — `Theme` has no field `at` (TypeError) / attribute error.

- [ ] **Step 1.3: Implement.** In `bot/config.py`:

Add below `_WEEKDAYS`:

```python
def _parse_at(raw: list) -> tuple:
    """Parse ["mon 13", "thu 16"] into ((0, 13), (3, 16)) — (weekday, UTC hour) pairs.
    Invalid entries fail fast at config load, not mid-run."""
    slots = []
    for entry in raw:
        try:
            day_s, hour_s = entry.split()
            day = _WEEKDAYS[day_s.lower()]
            hour = int(hour_s)
        except (ValueError, KeyError):
            raise SystemExit(f"themes.toml: invalid at entry {entry!r} (want e.g. 'mon 13')")
        if not 0 <= hour <= 23:
            raise SystemExit(f"themes.toml: invalid hour in at entry {entry!r} (0-23)")
        slots.append((day, hour))
    return tuple(slots)
```

Add field to `Theme` (after `days: tuple | None = None`):

```python
    at: tuple | None = None
```

In `load_themes`, before the `themes.append(...)` call add:

```python
        raw_at = t.get("at")
        at = _parse_at(raw_at) if raw_at else None
```

and pass `at=at,` in the `Theme(...)` construction (after `days=days,`).

- [ ] **Step 1.4: Run the full suite**

Run: `.venv/bin/python -m pytest`
Expected: all pass (128 + 5 new).

- [ ] **Step 1.5: Commit**

```bash
git add bot/config.py tests/test_config.py
git commit -m "feat(config): parse at = [\"mon 13\", ...] into (weekday, hour) slots"
```

### Task 2: Slot matching in `main.run` (signature `today: date` → `now: datetime`)

**Files:**
- Modify: `bot/main.py` (imports, `run` signature ~line 25, skip rule ~line 46)
- Test: `tests/test_main.py`

- [ ] **Step 2.1: Mechanically migrate existing tests** — `main.run` will take `now: datetime` instead of `today: date`. In `tests/test_main.py`:

```bash
sed -i '' -e 's/^from datetime import date$/from datetime import datetime/' \
          -e 's/today=date(/now=datetime(/g' tests/test_main.py
```

(Each `now=datetime(2026, 5, 26)` is midnight — fine, since those themes have `at=None` and fire on every run.)

- [ ] **Step 2.2: Replace the two `days` tests with slot tests.** In `tests/test_main.py`, delete `test_run_fires_only_scheduled_themes_for_the_weekday` and `test_run_fires_themes_without_days_on_any_weekday` (lines ~130–154) and add:

```python
def test_run_fires_only_the_theme_matching_current_slot(tmp_path, monkeypatch):
    sent = []
    # distinct repos per theme so dedup can't mask the slot filter
    monkeypatch.setattr(main, "search_repos",
                        lambda query, **k: [_repo(1, 10)] if "EARLY" in query else [_repo(2, 10)])
    monkeypatch.setattr(main, "readme_first_line", lambda *a, **k: "")
    monkeypatch.setattr(main, "send_message", lambda *a, **k: sent.append(a[2]) or {"ok": True})
    early = Theme(key="early", name="E", emoji="", query="EARLY", count=1, at=((0, 13),))  # Mon 13
    late = Theme(key="late", name="L", emoji="", query="LATE", count=1, at=((0, 19),))     # Mon 19
    main.run(_cfg(tmp_path, [early, late]), now=datetime(2026, 6, 8, 13))  # Mon 13:00 UTC
    import json
    saved = json.loads((tmp_path / "state.json").read_text())
    assert "early" in saved and "late" not in saved   # only the 13:00 theme fired
    assert len(sent) == 1


def test_run_skips_theme_on_right_day_wrong_hour(tmp_path, monkeypatch):
    sent = []
    _patch(monkeypatch, [_repo(1, 10)], sent)
    theme = Theme(key="t", name="T", emoji="", query="q", count=1, at=((0, 13),))  # Mon 13
    main.run(_cfg(tmp_path, [theme]), now=datetime(2026, 6, 8, 16))  # Mon 16:00 UTC
    assert sent == []
    assert not (tmp_path / "state.json").exists()


def test_run_fires_themes_without_at_on_any_run(tmp_path, monkeypatch):
    sent = []
    _patch(monkeypatch, [_repo(1, 10)], sent)
    always = Theme(key="always", name="A", emoji="", query="q", count=1)   # at=None
    main.run(_cfg(tmp_path, [always]), now=datetime(2026, 6, 8, 16))
    import json
    assert "always" in json.loads((tmp_path / "state.json").read_text())
```

- [ ] **Step 2.3: Run to verify failures**

Run: `.venv/bin/python -m pytest tests/test_main.py -v`
Expected: FAIL — `run()` got unexpected keyword `now`.

- [ ] **Step 2.4: Implement.** In `bot/main.py`:

Change the import (line 4):

```python
from datetime import datetime, timezone
```

Change the `run` signature and opening (lines 25–26):

```python
def run(config, now: datetime | None = None, dry_run: bool = False) -> int:
    now = now or datetime.now(timezone.utc)   # cron hours are UTC; never local time
    today = now.date()
```

Replace the weekday skip rule (lines 46–47):

```python
        if theme.at is not None and (now.weekday(), now.hour) not in theme.at:
            continue  # not scheduled for this weekday+hour slot
```

(`bot/__main__.py` needs no change — `run()` defaults `now` itself.)

- [ ] **Step 2.5: Run the full suite**

Run: `.venv/bin/python -m pytest`
Expected: all pass.

- [ ] **Step 2.6: Commit**

```bash
git add bot/main.py tests/test_main.py
git commit -m "feat(main): fire themes per (weekday, hour) slot; run() takes now: datetime"
```

### Task 3: Remove the now-dead `days` field

**Files:**
- Modify: `bot/config.py` (Theme field, `load_themes`)
- Test: `tests/test_config.py`

- [ ] **Step 3.1: Delete the two `days` config tests** — `test_load_themes_parses_days_to_weekday_ints` and `test_load_themes_days_absent_is_none` (lines ~100–109 of `tests/test_config.py`).

- [ ] **Step 3.2: Remove from `bot/config.py`:** the `days: tuple | None = None` field on `Theme`, the `raw_days` / `days = ...` lines in `load_themes`, and `days=days,` in the `Theme(...)` construction.

- [ ] **Step 3.3: Verify nothing else references days**

Run: `grep -rn '\bdays\b' bot/ tests/ | grep -v max_idle_days | grep -v 'days ago' | grep -v age_days || echo CLEAN`
Expected: `CLEAN` (only `max_idle_days`, `age_days`, and prose like "pushed Nd ago" remain).

Run: `.venv/bin/python -m pytest`
Expected: all pass.

- [ ] **Step 3.4: Commit**

```bash
git add bot/config.py tests/test_config.py
git commit -m "refactor(config): drop Theme.days, superseded by at slots"
```

### Task 4: Slot grid in `themes.toml` + cron in `railway.json`

**Files:**
- Modify: `themes.toml` (every theme's `days` line → `at` line; header comment)
- Modify: `railway.json:6`

- [ ] **Step 4.1: Replace each theme's `days` line with its `at` line** (day pairs unchanged from PR #5; hours assign one theme per slot):

| theme | replace | with |
|---|---|---|
| trending | `days = ["mon", "thu"]` | `at = ["mon 13", "thu 13"]` |
| ai-agents | `days = ["tue", "fri"]` | `at = ["tue 13", "fri 13"]` |
| dev-tools | `days = ["wed", "sat"]` | `at = ["wed 13", "sat 13"]` |
| crypto | `days = ["mon", "thu"]` | `at = ["mon 16", "thu 16"]` |
| finance | `days = ["tue", "fri"]` | `at = ["tue 16", "fri 16"]` |
| security | `days = ["wed", "sat"]` | `at = ["wed 16", "sat 16"]` |
| systems | `days = ["mon", "fri"]` | `at = ["mon 19", "fri 19"]` |
| data | `days = ["tue", "sat"]` | `at = ["tue 19", "sat 19"]` |
| web | `days = ["wed", "sun"]` | `at = ["wed 19", "sun 13"]` |
| science | `days = ["thu", "sun"]` | `at = ["thu 19", "sun 16"]` |

Also update the file's header comment first line to:

```toml
# Each [[theme]] fires in its `at` slots (["mon 13", ...] = weekday + UTC hour, one
# theme per slot) and becomes one Telegram message per firing, in the order below.
```

- [ ] **Step 4.2: In `railway.json` change the cron:**

```json
    "cronSchedule": "0 13,16,19 * * *",
```

- [ ] **Step 4.3: Verify the grid loads and is one-theme-per-slot**

Run:
```bash
.venv/bin/python - <<'EOF'
from collections import Counter
from bot.config import load_themes
slots = [s for t in load_themes("themes.toml") for s in (t.at or [])]
dupes = [s for s, n in Counter(slots).items() if n > 1]
assert len(slots) == 20 and not dupes, (len(slots), dupes)
assert all(h in (13, 16, 19) for _, h in slots)
print("grid OK: 20 firings, no slot collisions")
EOF
```
Expected: `grid OK: 20 firings, no slot collisions`

Run: `.venv/bin/python -m pytest`
Expected: all pass.

- [ ] **Step 4.4: Commit**

```bash
git add themes.toml railway.json
git commit -m "feat: one theme per slot — cron 13/16/19 UTC, at-grid in themes.toml"
```

### Task 5: PR 1

- [ ] **Step 5.1: Full suite + push + PR**

```bash
.venv/bin/python -m pytest && git push -u origin time-slots
gh pr create --title "Time-slot scheduling: one theme per 13/16/19 UTC slot" --body "$(cat <<'EOF'
Replaces the per-day burst (~3 themes at 13:00) with one theme per time slot.

- `themes.toml`: `days = ["mon","thu"]` → `at = ["mon 13", "thu 16"]` (weekday + UTC hour)
- `main.run(config, now: datetime)` skips themes not matching `(weekday, hour)` — same stateless design as `days`
- `railway.json` cron → `0 13,16,19 * * *`; a run with no matching theme sends nothing
- Grid: 20 firings over 21 slots (Sun 19:00 empty), day pairs unchanged from PR #5

Spec: docs/superpowers/specs/2026-06-10-time-slots-and-quality-design.md

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 5.2: CHECKPOINT — review/merge PR 1 before starting Part 2** (Part 2 branches from the merged main).

---

# Part 2 — PR 2: Curation quality

### Task 6: Branch

- [ ] **Step 6.1:**

```bash
git checkout main && git pull && git checkout -b curation-quality
```

### Task 7: Config — `min_score`, list `query`, `OLLAMA_CURATOR_MODEL`

**Files:**
- Modify: `bot/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 7.1: Write the failing tests** — append to `tests/test_config.py`:

```python
def test_load_themes_min_score_defaults_to_6(tmp_path):
    p = tmp_path / "t.toml"
    p.write_text('[[theme]]\nkey="k"\nname="N"\nquery="q"\n')
    assert load_themes(str(p))[0].min_score == 6


def test_load_themes_reads_min_score(tmp_path):
    p = tmp_path / "t.toml"
    p.write_text('[[theme]]\nkey="k"\nname="N"\nquery="q"\nmin_score=8\n')
    assert load_themes(str(p))[0].min_score == 8


def test_load_themes_query_list_becomes_tuple(tmp_path):
    p = tmp_path / "t.toml"
    p.write_text('[[theme]]\nkey="k"\nname="N"\nquery=["q1", "q2"]\n')
    assert load_themes(str(p))[0].query == ("q1", "q2")


def test_load_themes_query_string_stays_string(tmp_path):
    p = tmp_path / "t.toml"
    p.write_text('[[theme]]\nkey="k"\nname="N"\nquery="q1"\n')
    assert load_themes(str(p))[0].query == "q1"


def test_load_config_reads_curator_model():
    cfg = load_config(env=_env(OLLAMA_CURATOR_MODEL="qwen3-next:80b"), themes_path=str(SAMPLE))
    assert cfg.ollama_curator_model == "qwen3-next:80b"


def test_load_config_curator_model_defaults_blank():
    # blank means "use ollama_model" — main resolves the fallback
    assert load_config(env=_env(), themes_path=str(SAMPLE)).ollama_curator_model == ""
```

- [ ] **Step 7.2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_config.py -v`
Expected: new tests FAIL.

- [ ] **Step 7.3: Implement in `bot/config.py`:**

`Theme` gains (and `query`'s type widens):

```python
    query: str | tuple    # one GitHub query, or several merged+deduped
    ...
    min_score: int = 6    # curator score a repo must reach to be posted (0-10)
```

(`query` is already a field — just update its annotation; add `min_score` after `agent_skill_cap`.)

In `load_themes`:

```python
        raw_q = t["query"]
        query = tuple(raw_q) if isinstance(raw_q, list) else raw_q
```

and in the `Theme(...)` construction use `query=query,` plus `min_score=t.get("min_score", 6),`.

`Config` gains the field `ollama_curator_model: str = ""` (after `ollama_api_key`), and `load_config` passes `ollama_curator_model=env.get("OLLAMA_CURATOR_MODEL", ""),`.

- [ ] **Step 7.4: Run the full suite**

Run: `.venv/bin/python -m pytest`
Expected: all pass.

- [ ] **Step 7.5: Commit**

```bash
git add bot/config.py tests/test_config.py
git commit -m "feat(config): min_score, query lists, OLLAMA_CURATOR_MODEL"
```

### Task 8: Ranker — score-everything curation with a deterministic bar

**Files:**
- Modify: `bot/ranker.py` (replaces `_parse_indices`/`_rank_llm` internals; `rank` return type)
- Test: `tests/test_ranker.py`

`rank()` now returns `list[Pick]` (`Pick.repo`, `Pick.why`). Three outcomes: scored picks above the bar (post them) / scored but none above bar (`[]` — quiet slot, NO fallback) / LLM error or unparseable (`None` from `_rank_llm` → stars fallback with empty whys).

- [ ] **Step 8.1: Rewrite the LLM-path tests.** In `tests/test_ranker.py`, replace the import line and the five tests `test_rank_llm_orders_by_returned_indices`, `test_rank_llm_tolerates_fences_and_prose`, `test_rank_llm_ignores_out_of_range_indices`, `test_rank_llm_listing_includes_age_and_velocity`, plus update the two fallback tests, with:

```python
from bot.ranker import rank, rank_by_stars, _rank_llm, Pick
```

```python
def test_rank_llm_error_falls_back_to_stars():
    repos = [R(1, 10), R(2, 99)]
    client = _client(lambda request: httpx.Response(500))
    out = rank(repos, _theme("llm"), ollama_host="http://x", ollama_model="m", client=client)
    assert [p.repo.id for p in out] == [2, 1]
    assert all(p.why == "" for p in out)   # stars fallback carries no why


def test_rank_llm_unparseable_reply_falls_back_to_stars():
    repos = [R(1, 10), R(2, 99)]
    out = rank(repos, _theme("llm"), ollama_host="http://x", ollama_model="m",
               client=_content_client("I cannot rank these, sorry."))
    assert [p.repo.id for p in out] == [2, 1]


def test_rank_llm_keeps_only_scores_above_bar_ordered_by_score():
    repos = [R(1, 10), R(2, 20), R(3, 30)]
    reply = ('[{"i": 0, "score": 9, "why": "novel"}, {"i": 1, "score": 3, "why": "spam"}, '
             '{"i": 2, "score": 7, "why": "solid"}]')
    out = _rank_llm(repos, _theme("llm", 5), "http://x", "m", "k",
                    client=_content_client(reply))
    assert [(p.repo.id, p.why) for p in out] == [(1, "novel"), (3, "solid")]  # 3-scorer gated out


def test_rank_llm_all_below_bar_returns_empty_not_fallback():
    repos = [R(1, 10), R(2, 99)]
    reply = '[{"i": 0, "score": 4, "why": "meh"}, {"i": 1, "score": 5, "why": "thin"}]'
    out = rank(repos, _theme("llm"), ollama_host="http://x", ollama_model="m",
               client=_content_client(reply))
    assert out == []   # quiet slot — deliberately NOT the stars fallback


def test_rank_llm_respects_theme_min_score():
    theme = Theme(key="t", name="T", emoji="", query="q", rank="llm", count=5, min_score=8)
    repos = [R(1, 10), R(2, 20)]
    reply = '[{"i": 0, "score": 9, "why": "great"}, {"i": 1, "score": 7, "why": "good"}]'
    out = _rank_llm(repos, theme, "http://x", "m", "k", client=_content_client(reply))
    assert [p.repo.id for p in out] == [1]


def test_rank_llm_tolerates_fences_prose_and_junk_entries():
    repos = [R(1, 10), R(2, 20)]
    reply = ('Sure! Here:\n```json\n[{"i": 1, "score": 8, "why": "good"}, '
             '{"i": 99, "score": 9, "why": "oob"}, {"score": 9}, "junk", '
             '{"i": 1, "score": 8, "why": "dupe"}]\n```')
    out = _rank_llm(repos, _theme("llm", 5), "http://x", "m", "k",
                    client=_content_client(reply))
    assert [p.repo.id for p in out] == [2]   # out-of-range, malformed, and dupes dropped


def test_rank_caps_picks_at_theme_count():
    repos = [R(1, 1), R(2, 2), R(3, 3)]
    reply = ('[{"i": 0, "score": 7, "why": "a"}, {"i": 1, "score": 8, "why": "b"}, '
             '{"i": 2, "score": 9, "why": "c"}]')
    out = rank(repos, _theme("llm", 2), ollama_host="http://x", ollama_model="m",
               client=_content_client(reply))
    assert [p.repo.id for p in out] == [3, 2]


def test_rank_stars_mode_returns_picks():
    repos = [R(1, 10), R(2, 99)]
    out = rank(repos, _theme("stars"))
    assert [p.repo.id for p in out] == [2, 1] and all(p.why == "" for p in out)


def test_rank_llm_prompt_asks_for_scores_and_includes_age():
    today = date(2026, 6, 4)
    captured = {}
    def handler(request):
        import json as _json
        captured["prompt"] = _json.loads(request.content)["messages"][0]["content"]
        return httpx.Response(200, json={"message": {"content": '[{"i":0,"score":9,"why":"w"}]'}})
    repos = [R(1, 500, created_at="2026-06-01T00:00:00Z", pushed_at="2026-06-03T00:00:00Z")]
    _rank_llm(repos, _theme("llm", 1), "http://x", "m", "k", today=today, client=_client(handler))
    assert "score" in captured["prompt"].lower()
    assert "3d old" in captured["prompt"] and "pushed 1d ago" in captured["prompt"]
    assert "★/day" in captured["prompt"]
```

Also mechanically update the remaining `rank(...)`-asserting tests (`test_rank_stars_mode_uses_stars`, `test_rank_llm_without_host_falls_back_to_stars`) from `[r.id for r in ...]` to `[p.repo.id for p in ...]`, and keep `_prompt_for`/`test_curation_prompt_has_no_cap_directive` but change its mock content from `"[0]"` to `'[{"i":0,"score":9,"why":"w"}]'`. `rank_by_stars` tests are unchanged (it still returns repos).

- [ ] **Step 8.2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_ranker.py -v`
Expected: FAIL — no `Pick` in `bot.ranker`.

- [ ] **Step 8.3: Implement.** Replace `bot/ranker.py`'s `_INDICES_RE`, `_parse_indices`, `rank`, and `_rank_llm` with:

```python
from dataclasses import dataclass

_ARR_RE = re.compile(r"\[.*\]", re.S)


@dataclass(frozen=True)
class Pick:
    """One curated repo plus the curator's one-line reason it's notable."""
    repo: object
    why: str = ""
```

```python
def rank(repos: list, theme, today: date | None = None, ollama_host: str = "",
         ollama_model: str = "", ollama_api_key: str = "", client=None) -> list:
    """Pick the top repos for a theme; returns Picks (repo + curator's why).

    rank="llm": the model scores EVERY candidate 0-10 against the theme profile;
    deterministic code keeps only scores >= theme.min_score (LLM provides signal,
    code enforces). An empty result after a successful scoring round means a quiet
    slot — deliberately NOT the stars fallback. The fallback (top-by-stars, empty
    whys) fires only when the LLM is unavailable, errors, or replies unparseably —
    so a digest still ships when degraded.
    """
    today = today or date.today()
    if theme.rank == "llm" and ollama_host:
        try:
            scored = _rank_llm(repos, theme, ollama_host, ollama_model,
                               ollama_api_key, today=today, client=client)
            if scored is not None:
                return scored[:theme.count]
        except Exception:
            pass  # graceful degradation
    return [Pick(r) for r in rank_by_stars(repos, theme.count, today)]


def _parse_scores(text: str) -> list | None:
    """Extract [(index, score, why), ...] from the model reply, tolerating code
    fences, prose, and junk entries. None = unparseable (caller falls back)."""
    match = _ARR_RE.search(text)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except Exception:
        return None
    if not isinstance(data, list):
        return None
    out = []
    for e in data:
        if not isinstance(e, dict):
            continue
        i, score = e.get("i"), e.get("score")
        if isinstance(i, int) and isinstance(score, (int, float)):
            out.append((i, float(score), str(e.get("why") or "")))
    # a non-empty array where nothing parsed is a malformed reply, not "none qualify"
    return out if out or not data else None
```

`_describe_age` is unchanged. `_rank_llm` becomes:

```python
def _rank_llm(repos: list, theme, host: str, model: str, api_key: str,
              today: date | None = None, client=None) -> list | None:
    today = today or date.today()
    lines = []
    for i, r in enumerate(repos):
        topics = ", ".join(r.topics)
        lines.append(f"{i}. {r.full_name} (★{r.stars}, {_describe_age(r, today)}) "
                     f"— {r.description} [topics: {topics}]")
    listing = "\n".join(lines)

    criteria = theme.profile or (
        "genuinely interesting, substantive, currently-trending projects a developer "
        "audience would want to know about"
    )
    prompt = (
        f'You are scoring candidates for the list "{theme.name}" for a developer audience.\n'
        f"Selection criteria: {criteria}.\n"
        "Score EVERY candidate 0-10 for how interesting and substantive it is for this list:\n"
        "  0-3: spam, keyword-stuffed or scammy repos; joke or low-effort repos; 'awesome-*' "
        "link lists and curated-list repos; repos whose star count looks artificially inflated "
        "(very high ★/day) or that lean on hype.\n"
        "  4-5: legitimate but unremarkable — tutorials, thin wrappers, me-too projects.\n"
        "  6-7: solid, genuinely useful or interesting projects.\n"
        "  8-10: exceptional — novel, substantive, clearly worth a developer's attention.\n"
        "Prefer repos that are fresh and actively maintained (recently pushed); discount "
        "stale repos.\n"
        "For each candidate also give one short reason (max 20 words) for the score — what's "
        "novel, who it's for, or its momentum.\n\n"
        f"Candidates (index. owner/name (stars, age, velocity) — description [topics]):\n{listing}\n\n"
        'Return ONLY a JSON array with one object per candidate, like: '
        '[{"i": 0, "score": 8, "why": "first open-source X with Y"}]'
    )
    text = chat(prompt, host=host, model=model, api_key=api_key, client=client)
    parsed = _parse_scores(text)
    if parsed is None:
        return None
    picks, seen = [], set()
    for i, score, why in sorted(parsed, key=lambda t: -t[1]):
        if 0 <= i < len(repos) and i not in seen and score >= theme.min_score:
            seen.add(i)
            picks.append(Pick(repos[i], why))
    return picks
```

- [ ] **Step 8.4: Run ranker tests**

Run: `.venv/bin/python -m pytest tests/test_ranker.py -v`
Expected: PASS. (`tests/test_main.py` will FAIL until Task 9 — expected; do NOT commit yet.)

### Task 9: `main` consumes Picks; whys flow to summaries; quiet-slot logging

**Files:**
- Modify: `bot/main.py` (Phase 1 rank handling, Phase 2 delivery)
- Modify: `bot/summaries.py` (new `whys` parameter)
- Test: `tests/test_main.py`, `tests/test_summaries.py`

- [ ] **Step 9.1: Write the failing tests.** Append to `tests/test_summaries.py`:

```python
def test_make_summaries_threads_whys_into_prompt():
    captured = {}
    def handler(request):
        import json as _json
        captured["p"] = _json.loads(request.content)["messages"][0]["content"]
        return httpx.Response(200, json={"message": {"content": '["Blurb."]'}})
    make_summaries([R("a/x")], ["readme text"], whys=["first OSS tool doing Y"],
                   host="http://x", model="m", client=_client(handler))
    assert "first OSS tool doing Y" in captured["p"]
    assert "why" in captured["p"].lower()   # prompt asks for the why-it-matters angle


def test_make_summaries_works_without_whys():
    out = make_summaries([R("a/x")], ["ex"], host="http://x", model="m",
                         client=_content_client('["Blurb."]'))
    assert out == ["Blurb."]
```

Append to `tests/test_main.py`:

```python
def test_run_quiet_slot_when_nothing_clears_quality_bar(tmp_path, monkeypatch, caplog):
    caplog.set_level("INFO")   # the "bot" logger defaults to WARNING under pytest
    sent = []
    _patch(monkeypatch, [_repo(1, 10)], sent)
    monkeypatch.setattr(main, "rank", lambda repos, theme, **k: [])   # scored, none above bar
    theme = Theme(key="t", name="T", emoji="", query="q", count=3)
    failures = main.run(_cfg(tmp_path, [theme]), now=datetime(2026, 6, 8, 13))
    assert failures == 0 and sent == []          # not a failure, no message
    assert "quality bar" in caplog.text


def test_run_passes_curator_whys_to_summaries(tmp_path, monkeypatch):
    from bot.ranker import Pick
    seen = {}
    monkeypatch.setattr(main, "search_repos", lambda *a, **k: [_repo(1, 10)])
    monkeypatch.setattr(main, "readme_first_line", lambda *a, **k: "")
    monkeypatch.setattr(main, "readme_excerpt", lambda *a, **k: "ex")
    monkeypatch.setattr(main, "rank",
                        lambda repos, theme, **k: [Pick(repos[0], "novel rust db")])
    monkeypatch.setattr(main, "make_titles", lambda repos, **k: ["T"])
    def fake_summaries(repos, excerpts, whys=None, **k):
        seen["whys"] = whys
        return [None]
    monkeypatch.setattr(main, "make_summaries", fake_summaries)
    monkeypatch.setattr(main, "send_message", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(main, "send_slack_message", lambda *a, **k: False)
    monkeypatch.setattr(main, "llm_reachable", lambda *a, **k: True)
    theme = Theme(key="t", name="T", emoji="", query="q", count=1)
    main.run(_cfg(tmp_path, [theme], ollama="http://x"), now=datetime(2026, 6, 8, 13))
    assert seen["whys"] == ["novel rust db"]
```

- [ ] **Step 9.2: Run to verify failures**

Run: `.venv/bin/python -m pytest tests/test_main.py tests/test_summaries.py -v`
Expected: new tests FAIL (plus pre-existing Task 8 breakage).

- [ ] **Step 9.3: Implement `bot/summaries.py`** — new signature and prompt:

```python
def make_summaries(repos, excerpts=None, whys=None, host: str = "", model: str = "",
                   api_key: str = "", client=None) -> list:
    """One concise, factual English blurb per repo (same order): what it is, then why
    it's notable — written by an Ollama model from each repo's description, README
    excerpt, and the curator's why-line. Returns None for a repo when unavailable
    (callers fall back to the repo's own description). No host / chat error / bad
    JSON / length mismatch => all None, so the no-LLM digest is unchanged."""
    n = len(repos)
    if not host or not repos:
        return [None] * n
    excerpts = excerpts or [""] * n
    whys = whys or [""] * n
    listing = "\n".join(
        f"{i}. {r.full_name} — {r.description}  [README: {ex}] [curator: {why}]"
        for i, (r, ex, why) in enumerate(zip(repos, excerpts, whys))
    )
    prompt = (
        "For each GitHub repository below, write ONE or TWO concise, factual sentences "
        "(at most 40 words total) in plain English: first what it is and does, then why "
        "it is notable — what's novel, who it's for, or its momentum (the curator note "
        "may help). No marketing language, no emoji, no hype. Use the description and "
        "README excerpt. Return ONLY a JSON array of strings, one per repo, in the "
        "same order.\n\n"
        f"{listing}"
    )
```

(The parsing/fallback tail of the function is unchanged.)

- [ ] **Step 9.4: Implement `bot/main.py`.** In Phase 1, after the `rank(...)` call (which now returns Picks), replace

```python
            results[theme.key] = picked
            claimed.update(r.id for r in picked)
```

with

```python
            if repos and not picked:
                log.info("theme %s: %d candidates, none above the quality bar",
                         theme.key, len(repos))
            results[theme.key] = picked
            claimed.update(p.repo.id for p in picked)
```

In Phase 2, replace the body between `try:` and `messages = ...` with:

```python
            repos_ = [p.repo for p in picked]
            whys = [p.why for p in picked]
            summaries = None
            if config.ollama_host:
                excerpts = [readme_excerpt(r.full_name, token=config.github_token) for r in repos_]
                summaries = make_summaries(repos_, excerpts, whys=whys, host=config.ollama_host,
                                           model=curator_model, api_key=config.ollama_api_key)
            titles = make_titles(repos_, host=config.ollama_host,
                                 model=config.ollama_model, api_key=config.ollama_api_key)
            messages = build_messages(theme, repos_, describe, translate, titles, summaries)
```

and further down change `record_sent(state, theme.key, [r.id for r in picked])` to `[p.repo.id for p in picked]`.

Near the top of `run` (after `state = ...`), resolve the curator model — Task 10 also uses it:

```python
    curator_model = config.ollama_curator_model or config.ollama_model
```

and pass it to `rank` by changing the `rank(...)` call's `ollama_model=config.ollama_model` to `ollama_model=curator_model`.

- [ ] **Step 9.5: Run the full suite**

Run: `.venv/bin/python -m pytest`
Expected: all pass (test_main's older tests still work: `rank` returns Picks and main unwraps them; the `flaky_rank` test wraps the real rank, unchanged).

Note: `test_run_alerts_when_llm_degraded`'s `make_summaries` monkeypatch is `lambda repos, excerpts, **k: [None]` — `whys=` arrives via `**k`, so it still works. Inserting `ollama_curator_model` after `ollama_api_key` shifts positional order for `send_delay_seconds` and later, but every existing `Config(...)` construction (tests `_cfg`, the two direct ones in `test_main.py`) uses positionals only through `ollama_host` and keywords after — safe, since the new field has a default.

- [ ] **Step 9.6: Commit**

```bash
git add bot/ranker.py bot/summaries.py bot/main.py tests/test_ranker.py tests/test_summaries.py tests/test_main.py
git commit -m "feat: scored curation with quality bar; whys flow into what+why blurbs"
```

### Task 10: Pre-flight pings the curator model too

**Files:**
- Modify: `bot/main.py` (degraded check, ~line 31)
- Test: `tests/test_main.py`

- [ ] **Step 10.1: Write the failing test** — append to `tests/test_main.py`:

```python
def test_run_alerts_when_curator_model_degraded(tmp_path, monkeypatch):
    alerts = []
    monkeypatch.setattr(main, "search_repos", lambda *a, **k: [])
    # base model reachable, curator model not — must still count as degraded
    monkeypatch.setattr(main, "llm_reachable",
                        lambda host, model, key: model != "qwen3-next:80b")
    monkeypatch.setattr(main, "send_alert",
                        lambda token, chat, text, **k: alerts.append(text) or True)
    cfg = Config("tok", "-100", "", str(tmp_path),
                 [Theme(key="t", name="T", emoji="", query="q", count=1)],
                 "http://x", alert_chat_id="d", ollama_curator_model="qwen3-next:80b")
    main.run(cfg, now=datetime(2026, 6, 8, 13))
    assert alerts and "Ollama" in alerts[0]
```

- [ ] **Step 10.2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_main.py::test_run_alerts_when_curator_model_degraded -v`
Expected: FAIL (no alert — curator model never pinged).

- [ ] **Step 10.3: Implement.** In `bot/main.py` replace the `degraded = ...` assignment with (this needs `curator_model` resolved above it — move the Task 9 resolution line just before if needed):

```python
    # pre-flight: detect a degraded LLM up front (the silent-failure case) so we can
    # alert. When a separate curator model is configured, ping it too — a 401/missing
    # curator silently demotes scoring to the stars fallback, the 2026-06-06 incident.
    degraded = (not dry_run and bool(config.ollama_host) and (
        not llm_reachable(config.ollama_host, config.ollama_model, config.ollama_api_key)
        or (curator_model != config.ollama_model
            and not llm_reachable(config.ollama_host, curator_model, config.ollama_api_key))))
```

- [ ] **Step 10.4: Run the full suite**

Run: `.venv/bin/python -m pytest`
Expected: all pass.

- [ ] **Step 10.5: Commit**

```bash
git add bot/main.py tests/test_main.py
git commit -m "feat(main): pre-flight health-check covers the curator model"
```

### Task 11: Multi-query merge in `main`

**Files:**
- Modify: `bot/main.py` (Phase 1 search, ~lines 49–52)
- Test: `tests/test_main.py`

- [ ] **Step 11.1: Write the failing test** — append to `tests/test_main.py`:

```python
def test_run_merges_and_dedupes_multi_query_themes(tmp_path, monkeypatch):
    sent = []
    def fake_search(query, **k):
        # both queries surface repo 1; each contributes one unique repo
        return [_repo(1, 100), _repo(2, 50)] if "QA" in query else [_repo(1, 100), _repo(3, 80)]
    monkeypatch.setattr(main, "search_repos", lambda query, **k: fake_search(query))
    monkeypatch.setattr(main, "readme_first_line", lambda *a, **k: "")
    monkeypatch.setattr(main, "send_message", lambda *a, **k: sent.append(a[2]) or {"ok": True})
    theme = Theme(key="t", name="T", emoji="", query=("QA", "QB"), count=5)
    main.run(_cfg(tmp_path, [theme]), now=datetime(2026, 6, 8, 13))
    import json
    saved = json.loads((tmp_path / "state.json").read_text())
    assert sorted(saved["t"]) == [1, 2, 3]   # merged, repo 1 deduped
```

- [ ] **Step 11.2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_main.py::test_run_merges_and_dedupes_multi_query_themes -v`
Expected: FAIL (tuple query passed straight to `expand_since` → error/failure count).

- [ ] **Step 11.3: Implement.** In `bot/main.py`, replace

```python
            query = expand_since(theme.query, today)
            repos = search_repos(query, sort=theme.sort, order=theme.order,
                                 token=config.github_token)
```

with

```python
            queries = theme.query if isinstance(theme.query, tuple) else (theme.query,)
            repos, seen_ids = [], set()
            for q in queries:
                for r in search_repos(expand_since(q, today), sort=theme.sort,
                                      order=theme.order, token=config.github_token):
                    if r.id not in seen_ids:
                        seen_ids.add(r.id)
                        repos.append(r)
            repos.sort(key=lambda r: r.stars, reverse=True)   # merged pool, best first
```

- [ ] **Step 11.4: Run the full suite**

Run: `.venv/bin/python -m pytest`
Expected: all pass.

- [ ] **Step 11.5: Commit**

```bash
git add bot/main.py tests/test_main.py
git commit -m "feat(main): themes can search multiple queries, merged and deduped"
```

### Task 12: Widen the nets in `themes.toml`; document the env var

**Files:**
- Modify: `themes.toml`, `.env.example`, `README.md`

- [ ] **Step 12.1: Convert these six themes' `query` to lists** (others stay single-query):

```toml
# ai-agents
query = ["topic:ai-agents created:>{since:90d}", "topic:llm created:>{since:90d}"]
# dev-tools
query = ["topic:cli created:>{since:90d}", "topic:developer-tools created:>{since:90d}", "topic:terminal created:>{since:90d}"]
# crypto
query = ["topic:web3 created:>{since:120d}", "topic:blockchain created:>{since:120d}"]
# security
query = ["topic:security created:>{since:120d}", "topic:cybersecurity created:>{since:120d}"]
# data
query = ["topic:database created:>{since:120d} stars:>50", "topic:data-engineering created:>{since:120d} stars:>50"]
# web
query = ["topic:frontend created:>{since:120d} stars:>50", "topic:webdev created:>{since:120d} stars:>50"]
```

Also update the header comment's `query:` line to:

```toml
# query:   GitHub Search qualifiers — a string or a LIST of strings (results merged,
#          deduped). {since:Nd} expands to the date N days ago.
```

- [ ] **Step 12.2: Add to `.env.example`** (next to the other OLLAMA vars):

```
# Optional stronger model for curation scoring + summaries (defaults to OLLAMA_MODEL)
OLLAMA_CURATOR_MODEL=
```

Mention in `README.md`'s env-var section (one line, matching its existing style): `OLLAMA_CURATOR_MODEL` — optional stronger model for curation scoring and summaries; defaults to `OLLAMA_MODEL`.

- [ ] **Step 12.3: Verify themes load + full suite**

Run: `.venv/bin/python -c "from bot.config import load_themes; ts = load_themes('themes.toml'); print(len(ts), 'themes OK')" && .venv/bin/python -m pytest`
Expected: `10 themes OK`, all tests pass.

- [ ] **Step 12.4: Commit**

```bash
git add themes.toml .env.example README.md
git commit -m "feat: wider search nets for six themes; document OLLAMA_CURATOR_MODEL"
```

### Task 13: Live dry-run calibration (CHECKPOINT — needs prod Ollama key)

- [ ] **Step 13.1: Dry-run on the real pool with the current model** (uses prod secrets via Railway; throwaway state):

```bash
railway run -- bash -c 'STATE_DIR=/tmp/calib .venv/bin/python -m bot --dry-run'
```

- [ ] **Step 13.2: Dry-run with the stronger curator:**

```bash
railway run -- bash -c 'OLLAMA_CURATOR_MODEL=qwen3-next:80b STATE_DIR=/tmp/calib2 .venv/bin/python -m bot --dry-run'
```

- [ ] **Step 13.3: Compare:** Are the picks substantive? Do blurbs carry a real "why"? Is `min_score=6` gating everything (raise concern) or nothing (consider 7)? Tune `min_score` in `themes.toml` per theme if needed, commit any tuning. **This step is judgment — present both outputs to Sov if results are ambiguous.**

### Task 14: PR 2

- [ ] **Step 14.1: Full suite + push + PR**

```bash
.venv/bin/python -m pytest && git push -u origin curation-quality
gh pr create --title "Curation quality: scored picks with a quality bar, what+why blurbs, curator model, wider nets" --body "$(cat <<'EOF'
- Curator now scores EVERY candidate 0-10 with a one-line why; deterministic code keeps only score >= theme.min_score (default 6) — thin pools post fewer repos or go quiet (logged, not alerted). LLM failure still falls back to stars so a digest ships.
- rank() returns Picks (repo + why); whys feed make_summaries → blurbs are "what it is + why it's notable" (<= 40 words).
- New OLLAMA_CURATOR_MODEL (defaults to OLLAMA_MODEL) for scoring + summaries; pre-flight health-check pings it too.
- themes can list multiple search queries (merged, deduped); six themes widened.

Spec: docs/superpowers/specs/2026-06-10-time-slots-and-quality-design.md

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 14.2: After merge:** set `OLLAMA_CURATOR_MODEL` in Railway (value chosen in Task 13), watch the next two days of slots; quiet slots are expected occasionally by design.
