# Interesting Repos Telegram Bot — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Python program, run weekly by Railway cron, that discovers trending GitHub repos by theme, ranks them, and posts one Telegram message per theme to a group — never repeating a repo.

**Architecture:** A small package (`bot/`) of single-responsibility modules — `config`, `github`, `ranker`, `formatter`, `telegram`, `state` — orchestrated by `main`. Push-only (no server/webhook): it runs top-to-bottom and exits. Themes are defined in `themes.toml`. Dedup state is a JSON file on a Railway volume, written only after a confirmed send.

**Tech Stack:** Python 3.11+ (stdlib `tomllib`), `httpx` for all HTTP, `anthropic` SDK (lazy, optional — only for `rank: llm` themes), `pytest`. Tests mock HTTP via `httpx.MockTransport` — no live calls, no extra test deps.

---

## File Structure

```
interesting-repos/
  bot/
    __init__.py        # empty package marker
    config.py          # Config + Theme dataclasses, themes.toml load, env load, {since:Nd} expansion
    github.py          # Repo dataclass, Search API query, README-first-line fallback
    ranker.py          # rank dispatch: stars (default) or llm, with stars fallback
    formatter.py       # build Telegram HTML messages, 4096-char splitting, escaping
    telegram.py        # sendMessage with retry/backoff
    state.py           # load/save sent-id state, per-theme, FIFO-capped
    main.py            # orchestrate per-theme run; per-theme isolation; --dry-run
    __main__.py        # CLI entry: argparse --dry-run, calls main.run
  tests/
    test_config.py  test_github.py  test_ranker.py
    test_formatter.py  test_telegram.py  test_state.py  test_main.py
  themes.toml          # the two starter themes
  pyproject.toml       # deps + pytest config (pythonpath=".")
  railway.json         # weekly cron schedule
  .env.example
  README.md
```

**Interfaces locked across tasks** (names used consistently below):
- `Theme(key, name, emoji, query, sort="stars", order="desc", count=7, rank="stars", profile="")`
- `Config(telegram_bot_token, telegram_chat_id, github_token, anthropic_api_key, state_dir, themes)`
- `Repo(id:int, full_name, html_url, description, stars:int, language, topics:list, is_fork:bool, is_archived:bool)`
- `expand_since(query:str, today:date) -> str`
- `load_themes(path) -> list[Theme]` · `load_config(env, themes_path) -> Config`
- `parse_repo(item:dict) -> Repo` · `search_repos(query, sort, order, token="", per_page=50, client=None) -> list[Repo]` · `readme_first_line(full_name, token="", client=None) -> str`
- `load_state(path) -> dict` · `save_state(path, state)` · `unsent(state, theme_key, repos) -> list[Repo]` · `record_sent(state, theme_key, repo_ids, cap=500) -> dict`
- `rank(repos, theme, anthropic_api_key="", client=None) -> list[Repo]` · `rank_by_stars(repos, count) -> list[Repo]`
- `build_messages(theme, repos, describe) -> list[str]`
- `send_message(token, chat_id, text, parse_mode="HTML", client=None, retries=3, sleep=time.sleep) -> dict`
- `run(config, today=None, dry_run=False) -> int` (returns failure count)

---

## Task 1: Project scaffold

**Files:**
- Create: `bot/__init__.py` (empty)
- Create: `pyproject.toml`
- Create: `tests/test_smoke.py`

- [ ] **Step 1: Create the package marker**

Create `bot/__init__.py` as an empty file (0 bytes).

- [ ] **Step 2: Create `pyproject.toml`**

```toml
[project]
name = "interesting-repos-bot"
version = "0.1.0"
description = "Weekly interesting GitHub repos, delivered to Telegram by theme."
requires-python = ">=3.11"
dependencies = [
    "httpx>=0.27",
    "anthropic>=0.40",
]

[project.optional-dependencies]
dev = ["pytest>=8.0"]

[tool.pytest.ini_options]
pythonpath = ["."]
testpaths = ["tests"]
```

- [ ] **Step 3: Write a smoke test**

`tests/test_smoke.py`:
```python
def test_package_imports():
    import bot
    assert bot is not None
```

- [ ] **Step 4: Create the venv and install, run the smoke test**

Run:
```bash
python3 -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]" && pytest -q
```
Expected: `1 passed`.

- [ ] **Step 5: Create `.gitignore` and commit**

`.gitignore`:
```
.venv/
__pycache__/
*.pyc
.env
data/
```

```bash
git add bot/__init__.py pyproject.toml tests/test_smoke.py .gitignore
git commit -m "chore: scaffold bot package and pytest"
```

---

## Task 2: `expand_since` query placeholder

**Files:**
- Create: `bot/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write the failing test**

`tests/test_config.py`:
```python
from datetime import date
from bot.config import expand_since

def test_expand_since_replaces_token_with_iso_date():
    q = expand_since("created:>{since:7d}", today=date(2026, 5, 26))
    assert q == "created:>2026-05-19"

def test_expand_since_handles_multiple_and_other_windows():
    q = expand_since("a:{since:7d} b:{since:90d}", today=date(2026, 5, 26))
    assert q == "a:2026-05-19 b:2026-02-25"

def test_expand_since_leaves_plain_queries_untouched():
    assert expand_since("topic:finance", today=date(2026, 5, 26)) == "topic:finance"
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_config.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'bot.config'`.

- [ ] **Step 3: Implement**

`bot/config.py`:
```python
import re
from datetime import date, timedelta

_SINCE_RE = re.compile(r"\{since:(\d+)d\}")


def expand_since(query: str, today: date) -> str:
    def repl(m: "re.Match[str]") -> str:
        days = int(m.group(1))
        return (today - timedelta(days=days)).isoformat()
    return _SINCE_RE.sub(repl, query)
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_config.py -q`
Expected: `3 passed`.

- [ ] **Step 5: Commit**

```bash
git add bot/config.py tests/test_config.py
git commit -m "feat(config): expand {since:Nd} query placeholders"
```

---

## Task 3: `Theme` dataclass and `load_themes`

**Files:**
- Modify: `bot/config.py`
- Test: `tests/test_config.py`
- Create (test fixture): `tests/data/themes_sample.toml`

- [ ] **Step 1: Write the failing test**

Create `tests/data/themes_sample.toml`:
```toml
[[theme]]
key = "top-stars"
name = "Top Stars This Week"
emoji = "🔥"
query = "created:>{since:7d}"
sort = "stars"
count = 5
rank = "stars"

[[theme]]
key = "fin"
name = "Finance"
emoji = "💰"
query = "finance"
```

Append to `tests/test_config.py`:
```python
from pathlib import Path
from bot.config import load_themes, Theme

SAMPLE = Path(__file__).parent / "data" / "themes_sample.toml"

def test_load_themes_parses_all_fields():
    themes = load_themes(str(SAMPLE))
    assert len(themes) == 2
    t = themes[0]
    assert (t.key, t.name, t.emoji, t.query, t.sort, t.count, t.rank) == (
        "top-stars", "Top Stars This Week", "🔥", "created:>{since:7d}", "stars", 5, "stars")

def test_load_themes_applies_defaults():
    fin = load_themes(str(SAMPLE))[1]
    assert fin.sort == "stars" and fin.order == "desc"
    assert fin.count == 7 and fin.rank == "stars" and fin.profile == ""
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_config.py -q`
Expected: FAIL — `ImportError: cannot import name 'load_themes'`.

- [ ] **Step 3: Implement**

Add to top of `bot/config.py` (below existing imports):
```python
import tomllib
from dataclasses import dataclass


@dataclass(frozen=True)
class Theme:
    key: str
    name: str
    emoji: str
    query: str
    sort: str = "stars"
    order: str = "desc"
    count: int = 7
    rank: str = "stars"
    profile: str = ""


def load_themes(path: str) -> list[Theme]:
    with open(path, "rb") as f:
        data = tomllib.load(f)
    themes: list[Theme] = []
    for t in data.get("theme", []):
        themes.append(Theme(
            key=t["key"],
            name=t["name"],
            emoji=t.get("emoji", ""),
            query=t["query"],
            sort=t.get("sort", "stars"),
            order=t.get("order", "desc"),
            count=t.get("count", 7),
            rank=t.get("rank", "stars"),
            profile=t.get("profile", ""),
        ))
    return themes
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_config.py -q`
Expected: `5 passed`.

- [ ] **Step 5: Commit**

```bash
git add bot/config.py tests/test_config.py tests/data/themes_sample.toml
git commit -m "feat(config): Theme dataclass and themes.toml loader"
```

---

## Task 4: `Config` and `load_config` with validation

**Files:**
- Modify: `bot/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_config.py`:
```python
import pytest
from bot.config import load_config, Config

def _env(**overrides):
    base = {"TELEGRAM_BOT_TOKEN": "tok", "TELEGRAM_CHAT_ID": "-100123"}
    base.update(overrides)
    return base

def test_load_config_reads_required_and_optional():
    cfg = load_config(env=_env(GITHUB_TOKEN="gh", STATE_DIR="/tmp/x"), themes_path=str(SAMPLE))
    assert isinstance(cfg, Config)
    assert cfg.telegram_bot_token == "tok"
    assert cfg.telegram_chat_id == "-100123"
    assert cfg.github_token == "gh"
    assert cfg.state_dir == "/tmp/x"
    assert len(cfg.themes) == 2

def test_load_config_defaults_state_dir_and_blank_optionals():
    cfg = load_config(env=_env(), themes_path=str(SAMPLE))
    assert cfg.state_dir == "/data"
    assert cfg.github_token == "" and cfg.anthropic_api_key == ""

def test_load_config_missing_required_var_exits():
    with pytest.raises(SystemExit):
        load_config(env={"TELEGRAM_CHAT_ID": "-1"}, themes_path=str(SAMPLE))
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_config.py -q`
Expected: FAIL — `ImportError: cannot import name 'load_config'`.

- [ ] **Step 3: Implement**

Add to `bot/config.py`:
```python
import os


@dataclass(frozen=True)
class Config:
    telegram_bot_token: str
    telegram_chat_id: str
    github_token: str
    anthropic_api_key: str
    state_dir: str
    themes: list[Theme]


def load_config(env: dict | None = None, themes_path: str = "themes.toml") -> Config:
    env = os.environ if env is None else env

    def require(name: str) -> str:
        val = env.get(name)
        if not val:
            raise SystemExit(f"Missing required environment variable: {name}")
        return val

    return Config(
        telegram_bot_token=require("TELEGRAM_BOT_TOKEN"),
        telegram_chat_id=require("TELEGRAM_CHAT_ID"),
        github_token=env.get("GITHUB_TOKEN", ""),
        anthropic_api_key=env.get("ANTHROPIC_API_KEY", ""),
        state_dir=env.get("STATE_DIR", "/data"),
        themes=load_themes(themes_path),
    )
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_config.py -q`
Expected: `8 passed`.

- [ ] **Step 5: Commit**

```bash
git add bot/config.py tests/test_config.py
git commit -m "feat(config): Config dataclass and env loader with validation"
```

---

## Task 5: `Repo` dataclass and `parse_repo`

**Files:**
- Create: `bot/github.py`
- Test: `tests/test_github.py`

- [ ] **Step 1: Write the failing test**

`tests/test_github.py`:
```python
from bot.github import parse_repo, Repo

ITEM = {
    "id": 42,
    "full_name": "acme/widgets",
    "html_url": "https://github.com/acme/widgets",
    "description": "A widget toolkit",
    "stargazers_count": 1234,
    "language": "Python",
    "topics": ["widgets", "ui"],
    "fork": False,
    "archived": False,
}

def test_parse_repo_maps_fields():
    r = parse_repo(ITEM)
    assert r == Repo(42, "acme/widgets", "https://github.com/acme/widgets",
                     "A widget toolkit", 1234, "Python", ["widgets", "ui"], False, False)

def test_parse_repo_handles_nulls():
    r = parse_repo({"id": 1, "full_name": "a/b", "html_url": "u",
                    "description": None, "language": None})
    assert r.description == "" and r.language == "" and r.stars == 0
    assert r.topics == [] and r.is_fork is False and r.is_archived is False
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_github.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'bot.github'`.

- [ ] **Step 3: Implement**

`bot/github.py`:
```python
from dataclasses import dataclass


@dataclass(frozen=True)
class Repo:
    id: int
    full_name: str
    html_url: str
    description: str
    stars: int
    language: str
    topics: list[str]
    is_fork: bool
    is_archived: bool


def parse_repo(item: dict) -> Repo:
    return Repo(
        id=item["id"],
        full_name=item["full_name"],
        html_url=item["html_url"],
        description=item.get("description") or "",
        stars=item.get("stargazers_count", 0),
        language=item.get("language") or "",
        topics=item.get("topics") or [],
        is_fork=bool(item.get("fork", False)),
        is_archived=bool(item.get("archived", False)),
    )
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_github.py -q`
Expected: `2 passed`.

- [ ] **Step 5: Commit**

```bash
git add bot/github.py tests/test_github.py
git commit -m "feat(github): Repo dataclass and parse_repo"
```

---

## Task 6: `search_repos` against the Search API

**Files:**
- Modify: `bot/github.py`
- Test: `tests/test_github.py`

Note: tests use `httpx.MockTransport` to assert request params and feed a canned response — no network.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_github.py`:
```python
import httpx
from bot.github import search_repos

def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))

def test_search_repos_sends_query_sort_order_and_parses_items():
    captured = {}
    def handler(request):
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, json={"items": [ITEM]})
    repos = search_repos("created:>2026-05-19", sort="stars", order="desc",
                         token="gh", per_page=50, client=_client(handler))
    assert len(repos) == 1 and repos[0].full_name == "acme/widgets"
    assert "q=created" in captured["url"]
    assert "sort=stars" in captured["url"] and "order=desc" in captured["url"]
    assert "per_page=50" in captured["url"]
    assert captured["auth"] == "Bearer gh"

def test_search_repos_omits_auth_without_token():
    def handler(request):
        assert request.headers.get("Authorization") is None
        return httpx.Response(200, json={"items": []})
    assert search_repos("x", sort="stars", order="desc", client=_client(handler)) == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_github.py -q`
Expected: FAIL — `ImportError: cannot import name 'search_repos'`.

- [ ] **Step 3: Implement**

Add to `bot/github.py`:
```python
import httpx

_API = "https://api.github.com"


def search_repos(query: str, sort: str = "stars", order: str = "desc",
                 token: str = "", per_page: int = 50,
                 client: httpx.Client | None = None) -> list[Repo]:
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    params = {"q": query, "sort": sort, "order": order, "per_page": per_page}
    owns_client = client is None
    client = client or httpx.Client(timeout=30)
    try:
        resp = client.get(f"{_API}/search/repositories", params=params, headers=headers)
        resp.raise_for_status()
        return [parse_repo(item) for item in resp.json().get("items", [])]
    finally:
        if owns_client:
            client.close()
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_github.py -q`
Expected: `4 passed`.

- [ ] **Step 5: Commit**

```bash
git add bot/github.py tests/test_github.py
git commit -m "feat(github): search_repos via official Search API"
```

---

## Task 7: `readme_first_line` fallback

**Files:**
- Modify: `bot/github.py`
- Test: `tests/test_github.py`

Behavior: GET the raw README; return the first line that is not blank, not a Markdown heading marker alone, and not a badge/image line (`![...]`, `[![...`, or an HTML tag). Truncate to 200 chars. On any error or no suitable line, return `""`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_github.py`:
```python
from bot.github import readme_first_line

def _readme_client(body, status=200):
    def handler(request):
        assert request.url.path.endswith("/readme")
        assert request.headers.get("Accept") == "application/vnd.github.raw+json"
        return httpx.Response(status, text=body)
    return _client(handler)

def test_readme_first_line_skips_headings_and_badges():
    body = "# Title\n\n![badge](x.svg)\n\nThe real first sentence.\n"
    assert readme_first_line("a/b", client=_readme_client(body)) == "The real first sentence."

def test_readme_first_line_truncates_to_200():
    body = "x" * 500
    assert len(readme_first_line("a/b", client=_readme_client(body))) == 200

def test_readme_first_line_returns_empty_on_error():
    assert readme_first_line("a/b", client=_readme_client("nope", status=404)) == ""
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_github.py -q`
Expected: FAIL — `ImportError: cannot import name 'readme_first_line'`.

- [ ] **Step 3: Implement**

Add to `bot/github.py`:
```python
def _is_noise(line: str) -> bool:
    s = line.strip()
    if not s:
        return True
    if set(s) <= set("#=-*_> "):      # heading underline / rule / blockquote marker
        return True
    if s.startswith(("![", "[![", "<")):  # badge/image/html
        return True
    return False


def readme_first_line(full_name: str, token: str = "",
                      client: httpx.Client | None = None) -> str:
    headers = {"Accept": "application/vnd.github.raw+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    owns_client = client is None
    client = client or httpx.Client(timeout=30)
    try:
        resp = client.get(f"{_API}/repos/{full_name}/readme", headers=headers)
        resp.raise_for_status()
        for raw in resp.text.splitlines():
            line = raw.lstrip("# ").strip()
            if not _is_noise(raw):
                return line[:200]
        return ""
    except Exception:
        return ""
    finally:
        if owns_client:
            client.close()
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_github.py -q`
Expected: `7 passed`.

- [ ] **Step 5: Commit**

```bash
git add bot/github.py tests/test_github.py
git commit -m "feat(github): readme_first_line description fallback"
```

---

## Task 8: State (load/save/unsent/record_sent)

**Files:**
- Create: `bot/state.py`
- Test: `tests/test_state.py`

- [ ] **Step 1: Write the failing test**

`tests/test_state.py`:
```python
from dataclasses import dataclass
from bot.state import load_state, save_state, unsent, record_sent


@dataclass(frozen=True)
class FakeRepo:
    id: int


def test_load_missing_file_returns_empty(tmp_path):
    assert load_state(str(tmp_path / "nope.json")) == {}

def test_save_then_load_roundtrip(tmp_path):
    path = str(tmp_path / "sub" / "state.json")   # nested dir must be created
    save_state(path, {"t": [1, 2, 3]})
    assert load_state(path) == {"t": [1, 2, 3]}

def test_unsent_filters_already_sent_ids():
    state = {"t": [1, 2]}
    repos = [FakeRepo(1), FakeRepo(3), FakeRepo(4)]
    assert [r.id for r in unsent(state, "t", repos)] == [3, 4]

def test_unsent_unknown_theme_returns_all():
    repos = [FakeRepo(9)]
    assert unsent({}, "t", repos) == repos

def test_record_sent_appends_without_duplicates():
    state = record_sent({"t": [1]}, "t", [1, 2, 3])
    assert state["t"] == [1, 2, 3]

def test_record_sent_caps_fifo():
    state = record_sent({}, "t", list(range(10)), cap=3)
    assert state["t"] == [7, 8, 9]
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_state.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'bot.state'`.

- [ ] **Step 3: Implement**

`bot/state.py`:
```python
import json
import os


def load_state(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_state(path: str, state: dict) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f)
    os.replace(tmp, path)   # atomic on POSIX


def unsent(state: dict, theme_key: str, repos: list) -> list:
    sent = set(state.get(theme_key, []))
    return [r for r in repos if r.id not in sent]


def record_sent(state: dict, theme_key: str, repo_ids: list, cap: int = 500) -> dict:
    existing = list(state.get(theme_key, []))
    for rid in repo_ids:
        if rid not in existing:
            existing.append(rid)
    state[theme_key] = existing[-cap:]
    return state
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_state.py -q`
Expected: `6 passed`.

- [ ] **Step 5: Commit**

```bash
git add bot/state.py tests/test_state.py
git commit -m "feat(state): per-theme sent-id store with atomic save and FIFO cap"
```

---

## Task 9: Ranker — stars mode, dispatch, and fallback

**Files:**
- Create: `bot/ranker.py`
- Test: `tests/test_ranker.py`

- [ ] **Step 1: Write the failing test**

`tests/test_ranker.py`:
```python
from dataclasses import dataclass, field
from bot.ranker import rank, rank_by_stars
from bot.config import Theme


@dataclass(frozen=True)
class R:
    id: int
    stars: int
    full_name: str = "a/b"
    description: str = "d"
    topics: list = field(default_factory=list)


def _theme(rank_mode="stars", count=2):
    return Theme(key="t", name="T", emoji="", query="q", rank=rank_mode, count=count)

def test_rank_by_stars_sorts_desc_and_limits():
    repos = [R(1, 10), R(2, 99), R(3, 50)]
    assert [r.id for r in rank_by_stars(repos, 2)] == [2, 3]

def test_rank_stars_mode_uses_stars():
    repos = [R(1, 10), R(2, 99)]
    assert [r.id for r in rank(repos, _theme("stars"))] == [2, 1]

def test_rank_llm_without_key_falls_back_to_stars():
    repos = [R(1, 10), R(2, 99)]
    assert [r.id for r in rank(repos, _theme("llm"), anthropic_api_key="")] == [2, 1]

def test_rank_llm_error_falls_back_to_stars():
    class BoomClient:
        def __getattr__(self, _):
            raise RuntimeError("boom")
    repos = [R(1, 10), R(2, 99)]
    out = rank(repos, _theme("llm"), anthropic_api_key="key", client=BoomClient())
    assert [r.id for r in out] == [2, 1]
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_ranker.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'bot.ranker'`.

- [ ] **Step 3: Implement**

`bot/ranker.py`:
```python
def rank_by_stars(repos: list, count: int) -> list:
    return sorted(repos, key=lambda r: r.stars, reverse=True)[:count]


def rank(repos: list, theme, anthropic_api_key: str = "", client=None) -> list:
    if theme.rank == "llm" and anthropic_api_key:
        try:
            return _rank_llm(repos, theme, anthropic_api_key, client=client)[:theme.count]
        except Exception:
            pass  # graceful degradation: a digest still ships
    return rank_by_stars(repos, theme.count)
```

(`_rank_llm` is added in Task 10. Until then the `llm` paths exercise the fallback via the `except`, because the name is undefined — so to keep this task green, add a temporary stub at the bottom of the file:)
```python
def _rank_llm(repos, theme, api_key, client=None):
    raise NotImplementedError
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_ranker.py -q`
Expected: `4 passed` (the `NotImplementedError` stub is caught by the fallback).

- [ ] **Step 5: Commit**

```bash
git add bot/ranker.py tests/test_ranker.py
git commit -m "feat(ranker): stars ranking, dispatch, and graceful fallback"
```

---

## Task 10: Ranker — real `_rank_llm`

**Files:**
- Modify: `bot/ranker.py`
- Test: `tests/test_ranker.py`

Behavior: build one prompt listing candidates (index, name, stars, description, topics) plus the theme's `profile`; ask Claude `claude-haiku-4-5` to return a JSON array of the most relevant indices, best first. Parse, map indices back to repos, preserving model order, dropping out-of-range. The `anthropic` client is injected for tests; in production it's lazily constructed.

- [ ] **Step 1: Write the failing test (replace the stub's behavior)**

Append to `tests/test_ranker.py`:
```python
import json
from bot.ranker import _rank_llm

class FakeMessages:
    def __init__(self, payload): self._payload = payload
    def create(self, **kwargs):
        class Msg:  # mimic anthropic response.content[0].text
            content = [type("B", (), {"text": json.dumps(payload)})()]
        payload = self._payload
        return Msg()

class FakeAnthropic:
    def __init__(self, payload): self.messages = FakeMessages(payload)

def test_rank_llm_orders_by_returned_indices():
    repos = [R(1, 10), R(2, 20), R(3, 30)]
    theme = _theme("llm", count=2)
    client = FakeAnthropic([2, 0])          # pick repo id 3, then id 1
    out = _rank_llm(repos, theme, "key", client=client)
    assert [r.id for r in out] == [3, 1]

def test_rank_llm_ignores_out_of_range_indices():
    repos = [R(1, 10), R(2, 20)]
    client = FakeAnthropic([5, 1, 99])      # only index 1 is valid
    out = _rank_llm(repos, _theme("llm"), "key", client=client)
    assert [r.id for r in out] == [2]
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_ranker.py -q`
Expected: FAIL — `NotImplementedError` from the stub.

- [ ] **Step 3: Implement (replace the stub)**

Replace the `_rank_llm` stub in `bot/ranker.py` with:
```python
import json

_MODEL = "claude-haiku-4-5"


def _rank_llm(repos: list, theme, api_key: str, client=None):
    if client is None:
        import anthropic  # lazy: only needed for llm themes
        client = anthropic.Anthropic(api_key=api_key)

    lines = []
    for i, r in enumerate(repos):
        topics = ", ".join(r.topics)
        lines.append(f"{i}. {r.full_name} (★{r.stars}) — {r.description} [topics: {topics}]")
    listing = "\n".join(lines)

    prompt = (
        f"You are curating a list for the theme \"{theme.name}\".\n"
        f"Reader interests: {theme.profile or 'general developer interest'}.\n\n"
        f"Here are candidate repositories, each with an index:\n{listing}\n\n"
        f"Return ONLY a JSON array of the {theme.count} most relevant indices, "
        f"most relevant first. Example: [3, 0, 7]."
    )
    resp = client.messages.create(
        model=_MODEL,
        max_tokens=200,
        messages=[{"role": "user", "content": prompt}],
    )
    text = resp.content[0].text.strip()
    indices = json.loads(text)
    out = []
    for idx in indices:
        if isinstance(idx, int) and 0 <= idx < len(repos):
            out.append(repos[idx])
    return out
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_ranker.py -q`
Expected: `6 passed`.

- [ ] **Step 5: Commit**

```bash
git add bot/ranker.py tests/test_ranker.py
git commit -m "feat(ranker): LLM relevance ranking via Claude Haiku"
```

---

## Task 11: Formatter — message building, escaping, splitting

**Files:**
- Create: `bot/formatter.py`
- Test: `tests/test_formatter.py`

Behavior: header `"{emoji} <b>{name}</b>"`. Each entry: `⭐ {stars:,}` + ` · {language}` if present, then a linked title `<a href="url">full_name</a>`, then the description. Description = `repo.description` or `describe(repo)` when blank. HTML-escape the dynamic text fields (name, description, language) — never the URL. Join header + entries with blank lines; if the running message would exceed 4096 chars, start a new message (header only on the first).

- [ ] **Step 1: Write the failing test**

`tests/test_formatter.py`:
```python
from dataclasses import dataclass, field
from bot.formatter import build_messages, TELEGRAM_LIMIT
from bot.config import Theme


@dataclass(frozen=True)
class R:
    id: int
    full_name: str
    html_url: str
    description: str
    stars: int
    language: str
    topics: list = field(default_factory=list)


def _theme():
    return Theme(key="t", name="Top Stars", emoji="🔥", query="q")

def test_build_messages_single_message_has_header_and_entries():
    repos = [R(1, "a/b", "https://x/1", "Cool tool", 1500, "Rust")]
    msgs = build_messages(_theme(), repos, describe=lambda r: "")
    assert len(msgs) == 1
    m = msgs[0]
    assert "🔥 <b>Top Stars</b>" in m
    assert "⭐ 1,500 · Rust" in m
    assert '<a href="https://x/1">a/b</a>' in m
    assert "Cool tool" in m

def test_build_messages_uses_describe_when_description_blank():
    repos = [R(1, "a/b", "u", "", 10, "")]
    msgs = build_messages(_theme(), repos, describe=lambda r: "README line")
    assert "README line" in msgs[0]
    assert " · " not in msgs[0].split("\n")[1]  # no language separator when blank

def test_build_messages_escapes_html_in_dynamic_text():
    repos = [R(1, "a/b", "u", "uses <script> & stuff", 1, "C++")]
    m = build_messages(_theme(), repos, describe=lambda r: "")[0]
    assert "&lt;script&gt; &amp; stuff" in m
    assert "<script>" not in m

def test_build_messages_splits_over_limit():
    repos = [R(i, f"a/{i}", "u", "x" * 1000, i, "Go") for i in range(10)]
    msgs = build_messages(_theme(), repos, describe=lambda r: "")
    assert len(msgs) > 1
    assert all(len(m) <= TELEGRAM_LIMIT for m in msgs)
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_formatter.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'bot.formatter'`.

- [ ] **Step 3: Implement**

`bot/formatter.py`:
```python
from html import escape

TELEGRAM_LIMIT = 4096


def _entry(repo, describe) -> str:
    desc = repo.description or describe(repo) or ""
    meta = f"⭐ {repo.stars:,}"
    if repo.language:
        meta += f" · {escape(repo.language)}"
    title = f'<a href="{repo.html_url}">{escape(repo.full_name)}</a>'
    return f"{meta}\n{title}\n{escape(desc)}".rstrip()


def build_messages(theme, repos, describe) -> list[str]:
    header = f"{theme.emoji} <b>{escape(theme.name)}</b>".strip()
    messages: list[str] = []
    current = header
    for repo in repos:
        block = _entry(repo, describe)
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

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_formatter.py -q`
Expected: `4 passed`.

- [ ] **Step 5: Commit**

```bash
git add bot/formatter.py tests/test_formatter.py
git commit -m "feat(formatter): themed HTML messages with escaping and 4096 splitting"
```

---

## Task 12: Telegram sender with retry

**Files:**
- Create: `bot/telegram.py`
- Test: `tests/test_telegram.py`

- [ ] **Step 1: Write the failing test**

`tests/test_telegram.py`:
```python
import httpx
import pytest
from bot.telegram import send_message


def test_send_message_posts_expected_payload():
    seen = {}
    def handler(request):
        import json
        seen.update(json.loads(request.content))
        assert request.url.path == "/bottok/sendMessage"
        return httpx.Response(200, json={"ok": True})
    client = httpx.Client(transport=httpx.MockTransport(handler))
    out = send_message("tok", "-100", "hi", client=client, sleep=lambda s: None)
    assert out == {"ok": True}
    assert seen["chat_id"] == "-100" and seen["text"] == "hi"
    assert seen["parse_mode"] == "HTML"

def test_send_message_retries_then_succeeds():
    calls = {"n": 0}
    def handler(request):
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(500)
        return httpx.Response(200, json={"ok": True})
    client = httpx.Client(transport=httpx.MockTransport(handler))
    out = send_message("tok", "-1", "hi", client=client, retries=3, sleep=lambda s: None)
    assert out == {"ok": True} and calls["n"] == 3

def test_send_message_raises_after_exhausting_retries():
    def handler(request):
        return httpx.Response(500)
    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(httpx.HTTPStatusError):
        send_message("tok", "-1", "hi", client=client, retries=2, sleep=lambda s: None)
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_telegram.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'bot.telegram'`.

- [ ] **Step 3: Implement**

`bot/telegram.py`:
```python
import time
import httpx

_API = "https://api.telegram.org"


def send_message(token: str, chat_id: str, text: str, parse_mode: str = "HTML",
                 client: httpx.Client | None = None, retries: int = 3,
                 sleep=time.sleep) -> dict:
    url = f"{_API}/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": False,
    }
    owns_client = client is None
    client = client or httpx.Client(timeout=30)
    last_exc: Exception | None = None
    try:
        for attempt in range(retries):
            try:
                resp = client.post(url, json=payload)
                resp.raise_for_status()
                return resp.json()
            except Exception as exc:
                last_exc = exc
                if attempt < retries - 1:
                    sleep(2 ** attempt)
        raise last_exc
    finally:
        if owns_client:
            client.close()
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_telegram.py -q`
Expected: `3 passed`.

- [ ] **Step 5: Commit**

```bash
git add bot/telegram.py tests/test_telegram.py
git commit -m "feat(telegram): sendMessage with retry/backoff"
```

---

## Task 13: Orchestration in `main.run`

**Files:**
- Create: `bot/main.py`
- Test: `tests/test_main.py`

Behavior of `run(config, today=None, dry_run=False) -> int` (returns count of themes that failed to deliver):
- `today` defaults to `date.today()`.
- For each theme: expand query → `search_repos` → drop forks/archived → `unsent` → `rank` → if empty, skip → `build_messages` (describe = README fallback). In `dry_run`, print messages, do not send, do not touch state. Otherwise send each message, then `record_sent` + `save_state`.
- Any exception in a theme is caught, logged, increments the failure count; other themes continue.
- To keep `main` testable, the collaborators are module-level names the test monkeypatches (`search_repos`, `readme_first_line`, `rank`, `send_message`, `load_state`, `save_state`).

- [ ] **Step 1: Write the failing test**

`tests/test_main.py`:
```python
from datetime import date
import bot.main as main
from bot.config import Config, Theme
from bot.github import Repo


def _cfg(tmp_path, themes):
    return Config("tok", "-100", "", "", str(tmp_path), themes)

def _repo(i, stars):
    return Repo(i, f"a/{i}", f"https://x/{i}", "desc", stars, "Py", [], False, False)

def _patch(monkeypatch, repos, sent_box):
    monkeypatch.setattr(main, "search_repos", lambda *a, **k: list(repos))
    monkeypatch.setattr(main, "readme_first_line", lambda *a, **k: "")
    monkeypatch.setattr(main, "send_message",
                        lambda *a, **k: sent_box.append(a[2]) or {"ok": True})

def test_run_sends_and_records_state(tmp_path, monkeypatch):
    sent = []
    theme = Theme(key="t", name="T", emoji="🔥", query="created:>{since:7d}", count=2)
    _patch(monkeypatch, [_repo(1, 10), _repo(2, 99)], sent)
    failures = main.run(_cfg(tmp_path, [theme]), today=date(2026, 5, 26))
    assert failures == 0 and len(sent) == 1
    # state persisted with both ids
    import json
    saved = json.loads((tmp_path / "state.json").read_text())
    assert sorted(saved["t"]) == [1, 2]

def test_run_dry_run_does_not_send_or_persist(tmp_path, monkeypatch, capsys):
    sent = []
    theme = Theme(key="t", name="T", emoji="", query="q", count=1)
    _patch(monkeypatch, [_repo(1, 10)], sent)
    failures = main.run(_cfg(tmp_path, [theme]), today=date(2026, 5, 26), dry_run=True)
    assert failures == 0 and sent == []
    assert not (tmp_path / "state.json").exists()
    assert "a/1" in capsys.readouterr().out

def test_run_skips_already_sent(tmp_path, monkeypatch):
    sent = []
    theme = Theme(key="t", name="T", emoji="", query="q", count=5)
    _patch(monkeypatch, [_repo(1, 10)], sent)
    (tmp_path / "state.json").write_text('{"t": [1]}')
    failures = main.run(_cfg(tmp_path, [theme]), today=date(2026, 5, 26))
    assert failures == 0 and sent == []   # nothing new → no message

def test_run_isolates_theme_failures(tmp_path, monkeypatch):
    sent = []
    good = Theme(key="g", name="G", emoji="", query="q", count=1)
    bad = Theme(key="b", name="B", emoji="", query="q", count=1)
    monkeypatch.setattr(main, "search_repos", lambda *a, **k: [_repo(1, 5)])
    monkeypatch.setattr(main, "readme_first_line", lambda *a, **k: "")
    monkeypatch.setattr(main, "send_message", lambda *a, **k: sent.append(a[2]) or {"ok": True})
    # Make only the "bad" theme blow up, inside rank; "good" uses the real ranker.
    real_rank = main.rank
    def flaky_rank(repos, theme, **k):
        if theme.key == "b":
            raise RuntimeError("rank boom")
        return real_rank(repos, theme, **k)
    monkeypatch.setattr(main, "rank", flaky_rank)
    failures = main.run(_cfg(tmp_path, [good, bad]), today=date(2026, 5, 26))
    assert failures == 1 and len(sent) == 1   # good delivered, bad failed but isolated
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_main.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'bot.main'`.

- [ ] **Step 3: Implement**

`bot/main.py`:
```python
import logging
import os
from datetime import date

from bot.config import expand_since
from bot.github import search_repos, readme_first_line
from bot.ranker import rank
from bot.formatter import build_messages
from bot.telegram import send_message
from bot.state import load_state, save_state, unsent, record_sent

log = logging.getLogger("bot")


def run(config, today: date | None = None, dry_run: bool = False) -> int:
    today = today or date.today()
    state_path = os.path.join(config.state_dir, "state.json")
    state = load_state(state_path)
    failures = 0

    for theme in config.themes:
        try:
            query = expand_since(theme.query, today)
            repos = search_repos(query, sort=theme.sort, order=theme.order,
                                 token=config.github_token)
            repos = [r for r in repos if not r.is_fork and not r.is_archived]
            repos = unsent(state, theme.key, repos)
            picked = rank(repos, theme, anthropic_api_key=config.anthropic_api_key)
            if not picked:
                log.info("theme %s: no new repos", theme.key)
                continue

            def describe(r):
                return readme_first_line(r.full_name, token=config.github_token)

            messages = build_messages(theme, picked, describe)

            if dry_run:
                for m in messages:
                    print(m)
                    print("-" * 40)
                continue

            for m in messages:
                send_message(config.telegram_bot_token, config.telegram_chat_id, m)
            record_sent(state, theme.key, [r.id for r in picked])
            save_state(state_path, state)
            log.info("theme %s: sent %d repos", theme.key, len(picked))
        except Exception:
            failures += 1
            log.exception("theme %s failed", theme.key)

    return failures
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_main.py -q`
Expected: `4 passed`.

- [ ] **Step 5: Run the whole suite**

Run: `pytest -q`
Expected: all tests pass (`30+ passed`).

- [ ] **Step 6: Commit**

```bash
git add bot/main.py tests/test_main.py
git commit -m "feat(main): per-theme orchestration with isolation and dry-run"
```

---

## Task 14: CLI entry point

**Files:**
- Create: `bot/__main__.py`
- Test: manual (dry-run against real config requires secrets; we assert wiring only)

- [ ] **Step 1: Implement the CLI**

`bot/__main__.py`:
```python
import argparse
import logging
import sys

from bot.config import load_config
from bot.main import run


def cli(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="bot", description="Weekly interesting repos → Telegram")
    parser.add_argument("--dry-run", action="store_true", help="print messages instead of sending")
    parser.add_argument("--themes", default="themes.toml", help="path to themes.toml")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config = load_config(themes_path=args.themes)
    failures = run(config, dry_run=args.dry_run)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(cli())
```

- [ ] **Step 2: Verify `--help` works (no secrets needed)**

Run: `python -m bot --help`
Expected: usage text listing `--dry-run` and `--themes`, exit 0.

- [ ] **Step 3: Commit**

```bash
git add bot/__main__.py
git commit -m "feat(cli): python -m bot entry point with --dry-run"
```

---

## Task 15: Deployment artifacts

**Files:**
- Create: `themes.toml`
- Create: `.env.example`
- Create: `railway.json`
- Create: `README.md`

- [ ] **Step 1: Create the starter `themes.toml`**

```toml
# Each [[theme]] becomes one Telegram message per weekly run.
# query: GitHub Search qualifiers. {since:Nd} expands to the date N days ago.
#        Sorting is via the `sort` field below, NOT a sort: qualifier.
# rank:  "stars" (top by stars, no AI) or "llm" (Claude ranks against `profile`).

[[theme]]
key   = "top-stars"
name  = "Top Stars This Week"
emoji = "🔥"
query = "created:>{since:7d} stars:>10"
sort  = "stars"
count = 7
rank  = "stars"

[[theme]]
key   = "finance"
name  = "Top Finance Repos"
emoji = "💰"
query = "finance stars:>500 pushed:>{since:365d}"
sort  = "stars"
count = 7
rank  = "stars"
```

- [ ] **Step 2: Create `.env.example`**

```bash
# Required
TELEGRAM_BOT_TOKEN=123456:ABC-your-botfather-token
TELEGRAM_CHAT_ID=-1001234567890        # the group chat id (negative)

# Recommended (raises GitHub Search API rate limits)
GITHUB_TOKEN=ghp_xxx

# Optional (only needed if a theme uses rank = "llm")
ANTHROPIC_API_KEY=

# Optional (defaults to /data — the Railway volume mount)
STATE_DIR=/data
```

- [ ] **Step 3: Create `railway.json`**

```json
{
  "$schema": "https://railway.app/railway.schema.json",
  "build": { "builder": "NIXPACKS" },
  "deploy": {
    "startCommand": "python -m bot",
    "cronSchedule": "0 13 * * 1",
    "restartPolicyType": "NEVER"
  }
}
```

- [ ] **Step 4: Create `README.md`**

````markdown
# Interesting Repos → Telegram

Weekly digest of trending GitHub repos, by theme, posted to a Telegram group.
Push-only: a script run once a week by Railway cron. No server.

## How it works
Discover (GitHub Search API) → rank (top-by-stars, or Claude for `rank=llm` themes)
→ format (repo's own description, README-first-line fallback) → post one message per
theme → remember sent repos so nothing repeats. See `docs/superpowers/specs/` and
`docs/superpowers/plans/` for the full design.

## Themes
Edit `themes.toml`. Each `[[theme]]` is one message. `query` uses GitHub Search
qualifiers; `{since:Nd}` expands to N days ago. Sorting is the `sort` field, not a
`sort:` qualifier. `rank = "llm"` ranks candidates against `profile` via Claude
(needs `ANTHROPIC_API_KEY`); default `rank = "stars"` needs no AI.

## Setup
1. Create a bot with [@BotFather](https://t.me/BotFather) → `TELEGRAM_BOT_TOKEN`.
2. Add the bot to your group. Get the group's `chat_id`: add
   [@RawDataBot](https://t.me/RawDataBot) to the group (or call
   `getUpdates`), read the negative `chat.id` → `TELEGRAM_CHAT_ID`, then remove it.
3. (Recommended) Create a GitHub personal access token (no scopes needed for public
   search) → `GITHUB_TOKEN`.

## Run locally
```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env   # fill in values, then export them
pytest -q              # run the tests
python -m bot --dry-run   # print the digest instead of sending
```

## Deploy on Railway
1. New project from this repo.
2. Set the env vars from `.env.example` in the service Variables.
3. Add a **Volume** mounted at `/data` (holds `state.json` for dedup).
4. The service runs on the cron in `railway.json` (`0 13 * * 1` = Mon 13:00 UTC).
   Adjust as desired.
5. Tip: trigger one run and watch logs; or temporarily set the start command to
   `python -m bot --dry-run` for a safe first run.
````

- [ ] **Step 5: Verify themes.toml loads and dry-run wiring is intact**

Run:
```bash
python -c "from bot.config import load_themes; print([t.key for t in load_themes('themes.toml')])"
```
Expected: `['top-stars', 'finance']`.

- [ ] **Step 6: Commit**

```bash
git add themes.toml .env.example railway.json README.md
git commit -m "feat: deployment artifacts (themes, env example, railway, readme)"
```

---

## Self-Review

**Spec coverage:**
- Push-only weekly script → Task 13/14, `railway.json` cron (Task 15). ✓
- Theme-driven, two starter themes → Tasks 3, 15. ✓
- Discovery via official Search API → Task 6. ✓
- Description as-is + README fallback → Tasks 7, 11. ✓
- Optional LLM ranking with stars fallback → Tasks 9, 10. ✓
- Per-theme dedup state on a volume, written only after send → Tasks 8, 13. ✓
- Telegram group delivery with retry → Task 12. ✓
- 4096-char splitting + HTML escaping → Task 11. ✓
- Fail-fast config, per-theme isolation, non-zero exit on failure → Tasks 4, 13, 14. ✓
- `--dry-run` → Tasks 13, 14. ✓
- Deployment/setup docs → Task 15. ✓

**Placeholder scan:** No TBD/TODO; every code step shows complete code. The Task 9 `_rank_llm` stub is intentional and explicitly replaced in Task 10. ✓

**Type consistency:** `Repo.id:int`, `Theme.sort/order/count/rank`, and all function signatures match the locked interface list and are used identically across `main`, tests, and modules. The query strings use the `sort` field (request param) — no `sort:` qualifier left in any query. ✓

**Note for the implementer:** `main.run` imports its collaborators by name (`from bot.X import fn`), so the `test_main.py` tests monkeypatch them as attributes of the `bot.main` module (e.g. `monkeypatch.setattr(main, "search_repos", ...)`) — patching `bot.github.search_repos` would not take effect. Keep the patch targets as written.
