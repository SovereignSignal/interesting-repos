# Weekly "Movers" (star-delta) Implementation Plan

> **For agentic workers:** TDD throughout (write failing tests → verify fail → implement → verify pass → commit). Steps use checkbox (`- [ ]`) syntax.

**Goal:** Add a weekly "This Week's Movers" digest that surfaces repos by 7-day star growth — the independent, owned-metric version of the "fastest growing repos this week" post format. Built on a star-snapshot store fed by every run.

**Architecture:** One PR. A new pure module (`bot/starsnap.py`) holds a rolling per-day `{repo_id: stars}` store. A new `Theme.delta_days` field makes a theme source candidates by week-over-week delta (reorder + drop un-baselined) **before** the unchanged gauntlet. The formatter annotates the meta line with `+1.2k★ this week`. One new theme (`movers`) fires weekly in the grid's only empty slot (Sun 19 UTC).

**Tech Stack:** Python 3.11, httpx, pytest (tmp_path + monkeypatch), tomllib, Ollama Cloud, Railway cron.

**Spec:** `docs/superpowers/specs/2026-06-18-movers-star-delta-design.md`

**⚠️ Live channel.** `TELEGRAM_CHAT_ID` has followers — anything sent is public. All verification is **unit tests (mocked) or `--dry-run` only**. No real send to Telegram/Slack during this work. `--dry-run` prints and persists nothing (incl. snapshots).

All commands run from the repo root: `/Users/home/Documents/Sovereign Signal/repos/interesting-repos`.
Run tests with: `.venv/bin/python -m pytest`.

---

### Task 0: Branch

- [ ] **Step 0.1: Create the feature branch**

```bash
git checkout main && git pull && git checkout -b movers
```

### Task 1: The star-snapshot store — `bot/starsnap.py`

**Files:**
- New: `bot/starsnap.py`
- Test: `tests/test_starsnap.py`

A pure, filesystem-backed rolling store: one JSON file per day under `STATE_DIR/starsnap/YYYY-MM-DD.json`, mapping `{repo_id: stars}`. Disposable (delete the dir → Movers goes quiet a week; nothing else breaks).

- [ ] **Step 1.1: Write the failing tests** — new file `tests/test_starsnap.py`:

```python
from datetime import date, timedelta
from dataclasses import dataclass
from bot import starsnap


@dataclass(frozen=True)
class R:
    id: int
    stars: int


def test_load_snapshot_missing_file_is_empty(tmp_path):
    assert starsnap.load_snapshot(str(tmp_path), date(2026, 6, 1)) == {}


def test_save_then_load_roundtrips_int_keys_and_values(tmp_path):
    starsnap.save_snapshot(str(tmp_path), date(2026, 6, 1), {1: 100, 2: 250})
    assert starsnap.load_snapshot(str(tmp_path), date(2026, 6, 1)) == {1: 100, 2: 250}


def test_load_snapshot_coerces_string_keys_to_int(tmp_path):
    # JSON round-trips int keys as strings; load must restore ints (Repo.id is int)
    d = tmp_path / "starsnap"
    d.mkdir()
    (d / "2026-06-01.json").write_text('{"1": 100, "2": 250}')
    assert starsnap.load_snapshot(str(tmp_path), date(2026, 6, 1)) == {1: 100, 2: 250}


def test_find_baseline_returns_exact_day_when_present(tmp_path):
    starsnap.save_snapshot(str(tmp_path), date(2026, 6, 1), {1: 100})
    out = starsnap.find_baseline(str(tmp_path), date(2026, 6, 8), delta_days=7)
    assert out == {1: 100}


def test_find_baseline_falls_back_to_nearest_older_within_tolerance(tmp_path):
    # exact day (6/1) missing; 6/2 present (8 days ago, within tolerance of 7+3)
    starsnap.save_snapshot(str(tmp_path), date(2026, 6, 2), {9: 50})
    out = starsnap.find_baseline(str(tmp_path), date(2026, 6, 10), delta_days=7, tolerance=3)
    assert out == {9: 50}


def test_find_baseline_returns_empty_when_window_empty(tmp_path):
    # nothing aged 7-10 days; a newer snapshot (within delta window) must be ignored
    starsnap.save_snapshot(str(tmp_path), date(2026, 6, 7), {9: 50})   # 1 day old
    assert starsnap.find_baseline(str(tmp_path), date(2026, 6, 8), delta_days=7) == {}


def test_find_baseline_returns_empty_on_cold_start(tmp_path):
    assert starsnap.find_baseline(str(tmp_path), date(2026, 6, 8), delta_days=7) == {}


def test_retain_deletes_files_older_than_keep_days(tmp_path):
    today = date(2026, 6, 18)
    starsnap.save_snapshot(str(tmp_path), today - timedelta(days=20), {1: 1})  # gone
    starsnap.save_snapshot(str(tmp_path), today - timedelta(days=14), {2: 2})  # kept
    starsnap.save_snapshot(str(tmp_path), today, {3: 3})                       # kept
    starsnap.retain(str(tmp_path), today, keep_days=14)
    days = {date.fromisoformat(p.stem) for p in (tmp_path / "starsnap").glob("*.json")}
    assert days == {today - timedelta(days=14), today}


def test_retain_noop_when_folder_absent(tmp_path):
    starsnap.retain(str(tmp_path), date(2026, 6, 18))   # no starsnap/ dir yet


def test_order_by_delta_sorts_desc_and_drops_unbaselined():
    repos = [R(1, 250), R(2, 60), R(3, 9999)]            # 3 has no baseline
    baseline = {1: 100, 2: 50}                            # deltas: 150, 10
    out = starsnap.order_by_delta(repos, baseline)
    assert [r.id for r in out] == [1, 2]                  # by delta desc; repo 3 dropped


def test_order_by_delta_ties_keep_input_order():
    repos = [R(1, 110), R(2, 210), R(3, 310)]
    baseline = {1: 100, 2: 200, 3: 300}                   # all delta 10 -> stable
    out = starsnap.order_by_delta(repos, baseline)
    assert [r.id for r in out] == [1, 2, 3]
```

- [ ] **Step 1.2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_starsnap.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'bot.starsnap'`.

- [ ] **Step 1.3: Implement** — new file `bot/starsnap.py`:

```python
"""Rolling star-snapshot store for the Movers (star-delta) digest.

Every run folds the repos it searches into today's snapshot
(``STATE_DIR/starsnap/YYYY-MM-DD.json`` → ``{repo_id: stars}``). A delta theme
compares today's counts to a baseline ~7 days old to source "what blew up this
week." Disposable: delete the dir and Movers goes quiet for a week while it
rebuilds; no other feature depends on it."""
import json
import os
from datetime import date, timedelta


def snapshot_path(state_dir: str, day: date) -> str:
    return os.path.join(state_dir, "starsnap", f"{day.isoformat()}.json")


def load_snapshot(state_dir: str, day: date) -> dict:
    """``{repo_id: stars}`` for `day`, or ``{}`` if absent. Coerces JSON string
    keys back to ints (Repo.id is int)."""
    path = snapshot_path(state_dir, day)
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return {int(k): int(v) for k, v in data.items()}


def save_snapshot(state_dir: str, day: date, mapping: dict) -> None:
    """Atomic write (``.tmp`` + ``os.replace``, like ``state.save_state``)."""
    path = snapshot_path(state_dir, day)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(mapping, f)
    os.replace(tmp, path)


def find_baseline(state_dir: str, today: date, delta_days: int,
                  tolerance: int = 3) -> dict:
    """The nearest snapshot aged in ``[delta_days, delta_days+tolerance]`` days,
    walking older. Returns ``{}`` when nothing in the window exists (cold start
    or a cron gap wider than tolerance) — caller treats that as a quiet slot."""
    for offset in range(delta_days, delta_days + tolerance + 1):
        snap = load_snapshot(state_dir, today - timedelta(days=offset))
        if snap:
            return snap
    return {}


def retain(state_dir: str, today: date, keep_days: int = 14) -> None:
    """Delete snapshots older than ``keep_days``. Keeps ~2x the delta window so a
    missed cron day still leaves a usable baseline."""
    folder = os.path.join(state_dir, "starsnap")
    if not os.path.isdir(folder):
        return
    cutoff = today - timedelta(days=keep_days)
    for name in os.listdir(folder):
        if not name.endswith(".json"):
            continue
        try:
            day = date.fromisoformat(name[:-5])
        except ValueError:
            continue
        if day < cutoff:
            os.remove(os.path.join(folder, name))


def order_by_delta(repos, baseline: dict) -> list:
    """Repos with a baseline entry, sorted by ``stars_now - baseline`` desc.
    Repos absent from the baseline are dropped (delta undefined = not eligible).
    Stable on ties (preserves input order)."""
    scored = []
    for r in repos:
        prev = baseline.get(r.id)
        if prev is None:
            continue
        scored.append((r.stars - prev, r))
    scored.sort(key=lambda t: t[0], reverse=True)
    return [r for _, r in scored]
```

- [ ] **Step 1.4: Run the full suite**

Run: `.venv/bin/python -m pytest`
Expected: all pass (161 + 11 new).

- [ ] **Step 1.5: Commit**

```bash
git add bot/starsnap.py tests/test_starsnap.py
git commit -m "feat(starsnap): rolling star-snapshot store for the Movers digest"
```

### Task 2: Config — `Theme.delta_days`

**Files:**
- Modify: `bot/config.py` (`Theme` dataclass, `load_themes`)
- Test: `tests/test_config.py`

- [ ] **Step 2.1: Write the failing tests** — append to `tests/test_config.py`:

```python
def test_load_themes_delta_days_defaults_none(tmp_path):
    p = tmp_path / "t.toml"
    p.write_text('[[theme]]\nkey="k"\nname="N"\nquery="q"\n')
    assert load_themes(str(p))[0].delta_days is None


def test_load_themes_reads_delta_days(tmp_path):
    p = tmp_path / "t.toml"
    p.write_text('[[theme]]\nkey="k"\nname="N"\nquery="q"\ndelta_days=7\n')
    assert load_themes(str(p))[0].delta_days == 7
```

- [ ] **Step 2.2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_config.py -k delta_days -v`
Expected: FAIL — `Theme` has no field `delta_days`.

- [ ] **Step 2.3: Implement.** In `bot/config.py`, add the field to `Theme` (after `min_score`):

```python
    delta_days: int | None = None   # set => source candidates by N-day star growth (Movers)
```

and in `load_themes`'s `Theme(...)` construction add `delta_days=t.get("delta_days"),`.

- [ ] **Step 2.4: Run the full suite**

Run: `.venv/bin/python -m pytest`
Expected: all pass.

- [ ] **Step 2.5: Commit**

```bash
git add bot/config.py tests/test_config.py
git commit -m "feat(config): Theme.delta_days opts into star-delta candidate sourcing"
```

### Task 3: Formatter — growth annotation in the meta line

**Files:**
- Modify: `bot/formatter.py` (`_entry`, `build_messages`, new `_format_delta`)
- Test: `tests/test_formatter.py`

`build_messages` gains an optional `deltas: list[int | None] | None` (raw star deltas, aligned with repos). `_entry` formats a present delta as `+1.2k★ this week` and splices it into the meta line. `deltas=None` (every non-mover theme) → output byte-identical to today.

- [ ] **Step 3.1: Write the failing tests** — append to `tests/test_formatter.py`:

```python
from bot.formatter import _format_delta


def test_format_delta_compact_above_thousand():
    assert _format_delta(1234) == "+1.2k★ this week"
    assert _format_delta(1000) == "+1.0k★ this week"
    assert _format_delta(12345) == "+12.3k★ this week"


def test_format_delta_plain_below_thousand():
    assert _format_delta(999) == "+999★ this week"
    assert _format_delta(1) == "+1★ this week"


def test_format_delta_none_for_non_growth():
    assert _format_delta(0) is None
    assert _format_delta(-5) is None


def test_build_messages_deltas_none_is_identical_to_today():
    repos = [R(1, "a/b", "u", "d", 1500, "Rust")]
    plain = build_messages(_theme(), repos, describe=lambda r: "", titles=["T"])[0]
    no_deltas = build_messages(_theme(), repos, describe=lambda r: "",
                               titles=["T"], deltas=None)[0]
    assert plain == no_deltas


def test_build_messages_shows_growth_annotation_when_delta_present():
    repos = [R(1, "a/b", "https://x/1", "d", 1500, "Rust")]
    m = build_messages(_theme(), repos, describe=lambda r: "", titles=["T"],
                       deltas=[1234])[0]
    assert "⭐ 1,500 · +1.2k★ this week · Rust · a/b" in m


def test_build_messages_none_delta_entry_is_unannotated():
    repos = [R(1, "a/b", "u", "d", 10, "Go")]
    m = build_messages(_theme(), repos, describe=lambda r: "", titles=["T"],
                       deltas=[None])[0]
    assert "this week" not in m
    assert "⭐ 10 · Go · a/b" in m
```

- [ ] **Step 3.2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_formatter.py -v`
Expected: FAIL — `_format_delta` missing; `deltas` kwarg rejected.

- [ ] **Step 3.3: Implement.** In `bot/formatter.py`:

Add the helper (above `_entry`):

```python
def _format_delta(n: int) -> str | None:
    """A compact '+N★ this week' growth annotation, or None when there's nothing
    to show (<=0). Compact form (1.2k) mirrors the 'fastest growing repos this
    week' post format Movers is based on."""
    if n <= 0:
        return None
    if n >= 1000:
        return f"+{n / 1000:.1f}k★ this week"
    return f"+{n:,}★ this week"
```

Change `_entry` to thread a delta into the meta line:

```python
def _entry(repo, title, summary, describe, translate, delta=None) -> str:
    desc = summary or translate(repo.description or describe(repo) or "")
    heading = f'<a href="{repo.html_url}"><b>{escape(title)}</b></a>'
    meta = f"⭐ {repo.stars:,}"
    growth = _format_delta(delta) if delta is not None else None
    if growth:
        meta += f" · {growth}"
    if repo.language:
        meta += f" · {escape(repo.language)}"
    meta += f" · {escape(repo.full_name)}"
    return f"{heading}\n{meta}\n{escape(desc)}".rstrip()
```

Change `build_messages` to accept + zip `deltas`:

```python
def build_messages(theme, repos, describe, translate=lambda s: s, titles=None,
                   summaries=None, deltas=None) -> list[str]:
    header = f"{theme.emoji} <b>{escape(theme.name)}</b>".strip()
    if titles is None:
        titles = [r.full_name for r in repos]
    if summaries is None:
        summaries = [None] * len(repos)
    if deltas is None:
        deltas = [None] * len(repos)
    messages: list[str] = []
    current = header
    for repo, title, summary, delta in zip(repos, titles, summaries, deltas):
        block = _entry(repo, title, summary, describe, translate, delta)
        candidate = f"{current}\n\n{block}"
        if len(candidate) > TELEGRAM_LIMIT:
            messages.append(current)
            current = block            # continuation message, no header
        else:
            current = candidate
    if current:
        messages.append(current)
    return messages
```

- [ ] **Step 3.4: Run the full suite**

Run: `.venv/bin/python -m pytest`
Expected: all pass.

- [ ] **Step 3.5: Commit**

```bash
git add bot/formatter.py tests/test_formatter.py
git commit -m "feat(formatter): optional '+N★ this week' growth annotation in meta line"
```

### Task 4: Wire snapshots + delta sourcing into `main.run`

**Files:**
- Modify: `bot/main.py` (imports, Phase 1 snapshot fold + delta sourcing, post-Phase-1 persist, Phase 2 deltas)
- Test: `tests/test_main.py`

Every theme folds its searched repos into today's snapshot; delta themes reorder by growth and drop un-baselined repos *before* the cap. Snapshots persist once after Phase 1 (never in `--dry-run`). Phase 2 builds `deltas` from the stashed baseline and passes it to `build_messages`.

- [ ] **Step 4.1: Write the failing tests** — append to `tests/test_main.py`:

```python
from bot import starsnap


def _seed_snapshot(tmp_path, day, mapping):
    starsnap.save_snapshot(str(tmp_path), day, mapping)


def test_run_writes_a_snapshot_of_searched_repos(tmp_path, monkeypatch):
    _patch(monkeypatch, [_repo(1, 250), _repo(2, 60)], [])
    theme = Theme(key="t", name="T", emoji="", query="q", count=2)
    main.run(_cfg(tmp_path, [theme]), now=datetime(2026, 6, 8, 13))
    snap = starsnap.load_snapshot(str(tmp_path), date(2026, 6, 8))
    assert snap == {1: 250, 2: 60}


def test_run_dry_run_writes_no_snapshot(tmp_path, monkeypatch):
    _patch(monkeypatch, [_repo(1, 10)], [])
    theme = Theme(key="t", name="T", emoji="", query="q", count=1)
    main.run(_cfg(tmp_path, [theme]), now=datetime(2026, 6, 8, 13), dry_run=True)
    assert not (tmp_path / "starsnap").exists()


def test_run_snapshot_unions_across_themes_in_one_run(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "search_repos",
                        lambda query, **k: [_repo(1, 10)] if "AAA" in query else [_repo(2, 20)])
    monkeypatch.setattr(main, "readme_first_line", lambda *a, **k: "")
    monkeypatch.setattr(main, "send_message", lambda *a, **k: {"ok": True})
    a = Theme(key="a", name="A", emoji="", query="AAA", count=1)
    b = Theme(key="b", name="B", emoji="", query="BBB", count=1)
    main.run(_cfg(tmp_path, [a, b]), now=datetime(2026, 6, 8, 13))
    snap = starsnap.load_snapshot(str(tmp_path), date(2026, 6, 8))
    assert snap == {1: 10, 2: 20}


def test_run_delta_theme_orders_candidates_by_growth_and_drops_unbaselined(tmp_path, monkeypatch):
    # baseline a week ago: repo 1 grew most (100->250), repo 3 has no baseline
    _seed_snapshot(tmp_path, date(2026, 6, 1), {1: 100, 2: 50})
    seen = {}
    def fake_rank(repos, theme, **k):
        seen["order"] = [r.id for r in repos]
        from bot.ranker import Pick
        return [Pick(r) for r in repos[:2]]
    monkeypatch.setattr(main, "search_repos",
                        lambda *a, **k: [_repo(3, 9999), _repo(1, 250), _repo(2, 60)])
    monkeypatch.setattr(main, "readme_first_line", lambda *a, **k: "")
    monkeypatch.setattr(main, "rank", fake_rank)
    monkeypatch.setattr(main, "send_message", lambda *a, **k: {"ok": True})
    theme = Theme(key="m", name="M", emoji="🚀", query="q", count=2, delta_days=7)
    main.run(_cfg(tmp_path, [theme]), now=datetime(2026, 6, 8, 13))
    assert seen["order"] == [1, 2]      # delta 150, 10; repo 3 (no baseline) dropped


def test_run_delta_theme_annotates_growth_in_message(tmp_path, monkeypatch):
    _seed_snapshot(tmp_path, date(2026, 6, 1), {1: 100})   # grew 100 -> 250 (+150)
    sent = []
    from bot.ranker import Pick
    monkeypatch.setattr(main, "search_repos", lambda *a, **k: [_repo(1, 250)])
    monkeypatch.setattr(main, "readme_first_line", lambda *a, **k: "")
    monkeypatch.setattr(main, "rank", lambda repos, theme, **k: [Pick(r) for r in repos])
    monkeypatch.setattr(main, "send_message", lambda *a, **k: sent.append(a[2]) or {"ok": True})
    theme = Theme(key="m", name="M", emoji="🚀", query="q", count=1, delta_days=7)
    main.run(_cfg(tmp_path, [theme]), now=datetime(2026, 6, 8, 13))
    assert any("+150★ this week" in m for m in sent)


def test_run_delta_theme_cold_start_is_quiet(tmp_path, monkeypatch, caplog):
    caplog.set_level("INFO")
    sent = []
    _patch(monkeypatch, [_repo(1, 250), _repo(2, 60)], sent)   # no baseline seeded
    theme = Theme(key="m", name="M", emoji="🚀", query="q", count=2, delta_days=7)
    failures = main.run(_cfg(tmp_path, [theme]), now=datetime(2026, 6, 8, 13))
    assert failures == 0 and sent == []            # quiet slot, no failure, no alert
    assert "quality bar" in caplog.text or "m" not in caplog.text  # no crash either way
```

(Note: add `from datetime import date` to the test imports — `datetime` is already imported.)

- [ ] **Step 4.2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_main.py -k "snapshot or delta" -v`
Expected: FAIL — no snapshot written; `delta_days` ignored; cold start sends anyway.

- [ ] **Step 4.3: Implement.** In `bot/main.py`:

Imports — add near the top:

```python
from datetime import datetime, timezone, date
```
(already imports `datetime, timezone` — append `date`), and:

```python
from bot.starsnap import load_snapshot, save_snapshot, find_baseline, order_by_delta, retain
```

Phase 1 — load today's snapshot + a per-theme baseline stash. Just inside `run`, after `state = load_state(state_path)`:

```python
    today_snap = load_snapshot(config.state_dir, today)   # Movers store (fed by every theme)
    baselines: dict = {}                                  # theme.key -> baseline {id: stars}
```

In the Phase 1 loop, immediately after the fork/archived filter line (`repos = [r for r in repos if not r.is_fork and not r.is_archived]`), add:

```python
            for r in repos:
                today_snap[r.id] = r.stars      # feed the store (every theme, every run)
            if theme.delta_days:                # source candidates by N-day star growth
                baseline = find_baseline(config.state_dir, today, theme.delta_days)
                repos = order_by_delta(repos, baseline)
                baselines[theme.key] = baseline
```

After the Phase 1 loop, before Phase 2 (`sent_any = False`), persist (never in dry-run):

```python
    if not dry_run:
        save_snapshot(config.state_dir, today, today_snap)
        retain(config.state_dir, today)
```

Phase 2 — build `deltas` from the stashed baseline and pass to `build_messages`. Replace the `messages = build_messages(...)` line with:

```python
            deltas = None
            if theme.delta_days:
                base = baselines.get(theme.key, {})
                deltas = [r.stars - base.get(r.id, r.stars) for r in repos_]
            messages = build_messages(theme, repos_, describe, translate, titles,
                                      summaries, deltas)
```

- [ ] **Step 4.4: Run the full suite**

Run: `.venv/bin/python -m pytest`
Expected: all pass (existing tests unaffected: they have `delta_days=None`, so `deltas=None` and no snapshot reordering; non-dry runs now also write a `starsnap/` file, which no existing test asserts against).

- [ ] **Step 4.5: Commit**

```bash
git add bot/main.py tests/test_main.py
git commit -m "feat(main): snapshot searched repos; delta themes source by star growth"
```

### Task 5: The Movers theme in `themes.toml`

**Files:**
- Modify: `themes.toml` (append the theme; extend the header comment)

- [ ] **Step 5.1: Append the theme and document `delta_days`.** At the end of `themes.toml`:

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
agent_skill_cap = 2          # keep a real viral AI tool, cut clone/skill packs
min_score = 7               # higher bar — we're featuring "what blew up"
at = ["sun 19"]             # the grid's only empty slot — one firing/week
profile = "Real breakouts with genuine momentum this week — repos whose star growth reflects substance (a novel tool, an inflection point, a real release), not manufactured hype. Aggressively discount anything whose velocity looks bought or coordinated, agent-skill clone packs, and forks riding a parent's fame. If the growth is real but the project is thin, it is not a mover."
```

Add to the header comment block (after the `rank:`/`profile:` lines):

```toml
# delta_days: set (e.g. 7) to source candidates by N-day star growth instead of
#             leaving them in search-stars order — repos with no prior snapshot
#             are dropped (the weekly Movers digest uses this). Optional.
```

- [ ] **Step 5.2: Verify the grid still has one theme per slot + movers is the only `sun 19`**

Run:
```bash
.venv/bin/python - <<'EOF'
from collections import Counter
from bot.config import load_themes
themes = load_themes("themes.toml")
slots = [s for t in themes for s in (t.at or [])]
dupes = [s for s, n in Counter(slots).items() if n > 1]
movers = [t for t in themes if t.key == "movers"][0]
assert not dupes, dupes
assert movers.delta_days == 7 and (6, 19) in movers.at
assert movers.agent_skill_cap == 2 and movers.min_score == 7
print(f"grid OK: {len(themes)} themes, {len(slots)} firings, no collisions; movers weekly at sun 19")
EOF
```
Expected: `grid OK: 11 themes, 21 firings, no collisions; movers weekly at sun 19`.

Run: `.venv/bin/python -m pytest`
Expected: all pass.

- [ ] **Step 5.3: Commit**

```bash
git add themes.toml
git commit -m "feat(themes): weekly Movers digest at sun 19 (star-delta, cap=2, min_score=7)"
```

### Task 6: Dry-run calibration (CHECKPOINT — no live send)

Verify the cold-start path and the format locally, **without** touching the live channel. This confirms the snapshot wiring is sound before the store starts accumulating against prod state.

- [ ] **Step 6.1: Cold-start dry-run on a throwaway state dir** (local, no secrets needed — slot-matching applies to dry runs, so force the movers slot with a temporary `--themes` that strips every other theme's schedule, OR just assert the in-process behavior):

```bash
.venv/bin/python - <<'EOF'
# In-process: movers at sun 19 with an empty store must be a quiet slot, no crash.
from datetime import datetime
from bot.config import load_themes
from bot.main import run
import tempfile, os, bot.main as m
themes = [t for t in load_themes("themes.toml") if t.key == "movers"]
# stub out network + LLM so this is purely the cold-start path
m.search_repos = lambda *a, **k: []
m.readme_first_line = lambda *a, **k: ""
with tempfile.TemporaryDirectory() as d:
    from bot.config import Config
    cfg = Config("tok", "-100", "", d, themes, "", )   # ollama off
    failures = run(cfg, now=datetime(2026, 6, 21, 19), dry_run=True)  # Sun 19:00 UTC
    print("cold-start dry-run failures:", failures, "(expect 0; quiet slot, no send)")
EOF
```
Expected: `cold-start dry-run failures: 0`. (On prod, the same path produces an empty Movers post until ~7–11 days of snapshots accrue — by design, not a bug.)

- [ ] **Step 6.2: Format sanity** — confirm a seeded-baseline dry-run renders the annotation (local, mocked search):

```bash
.venv/bin/python - <<'EOF'
from datetime import datetime, date
from bot.config import load_themes, Config
from bot.starsnap import save_snapshot
from bot.github import Repo
import bot.main as m, tempfile
themes = [t for t in load_themes("themes.toml") if t.key == "movers"]
m.search_repos = lambda *a, **k: [Repo(1, "a/hotness", "https://x/1", "a thing", 1500, "Rust", [], False, False)]
m.readme_first_line = lambda *a, **k: ""
m.rank = lambda repos, theme, **k: [__import__("bot.ranker", fromlist=["Pick"]).Pick(r) for r in repos]
with tempfile.TemporaryDirectory() as d:
    save_snapshot(d, date(2026, 6, 14), {1: 200})   # +1300 over the week
    cfg = Config("tok", "-100", "", d, themes, "", )
    run = m.run
    run(cfg, now=datetime(2026, 6, 21, 19), dry_run=True)
EOF
```
Expected: printed dry-run message contains `🚀 This Week's Movers`, `⭐ 1,500 · +1.3k★ this week · Rust · a/hotness`.

- [ ] **Step 6.3: Present both outputs to Sov.** Confirm the cold-start quiet path and the rendered annotation read well before opening the PR. **No send to TELEGRAM_CHAT_ID/Slack occurs in either step (both are `--dry-run` / mocked).**

### Task 7: PR

- [ ] **Step 7.1: Full suite + push + PR**

```bash
.venv/bin/python -m pytest && git push -u origin movers
gh pr create --title "Weekly Movers digest: star-delta sourcing via a snapshot store" --body "$(cat <<'EOF'
Adds a weekly "This Week's Movers" digest — the independent, owned-metric version of the
"fastest growing repos this week" post format.

- New `bot/starsnap.py`: a rolling per-day `{repo_id: stars}` store under
  `STATE_DIR/starsnap/`. Every run folds searched repos in; disposable (delete it →
  Movers goes quiet a week, nothing else breaks).
- New `Theme.delta_days`: a delta theme sources candidates by N-day star growth
  (reorder + drop un-baselined) **before** the unchanged gauntlet (clean → unsent →
  cap → rank). No prompt changes; `min_score` + `agent_skill_cap` still enforce.
- Formatter annotates the meta line with `+1.2k★ this week` (deltas=None elsewhere →
  byte-identical output).
- New `movers` theme: weekly at Sun 19 UTC (the grid's only empty slot), `delta_days=7`,
  `agent_skill_cap=2`, `min_score=7`.
- **Cold start:** ~7–11 days of empty Sundays before the first real digest (no backfill —
  the store is owned data). Documented loudly so it isn't read as a bug.

Re-broadcasting viral X posts was considered and rejected (dissolves the independent-
curation moat); see the spec's Non-goal section.

Spec: docs/superpowers/specs/2026-06-18-movers-star-delta-design.md

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 7.2: After merge:** snapshotting starts immediately (side effect of every run), so the clock starts at merge. Watch the first two Sundays — the first should be a quiet slot (cold start), the second the first real digest. Calibrate `stars:>`, `agent_skill_cap`, `min_score`, and compact-vs-comma delta format against real output.
