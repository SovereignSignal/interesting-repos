# LLM-written Summaries Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace each digest entry's summary line with a concise, factual, LLM-written blurb generated from the repo's description + a short README excerpt, with graceful fallback to the current description.

**Architecture:** A new `bot/summaries.py` (mirrors `titles.py`) makes one batched LLM call per theme returning a JSON array of blurbs. `github.readme_excerpt` supplies README context. The formatter uses the blurb when present, else falls back to today's `translate(description or readme-first-line)`. `main` fetches excerpts + summaries only when an Ollama host is configured.

**Tech Stack:** Python 3.11+, httpx (mocked via `httpx.MockTransport`), pytest. LLM via Ollama (`bot/ollama.chat`).

**Spec:** `docs/superpowers/specs/2026-06-05-llm-summaries-design.md`

**Prerequisite:** On branch `llm-summaries` (spec committed). Run tests with `.venv/bin/python -m pytest`.

---

## File Structure

| File | Responsibility | Action |
|---|---|---|
| `bot/github.py` | Adds `readme_excerpt` — short README intro as blurb source. | Modify |
| `bot/summaries.py` | `make_summaries` — batched LLM blurbs per theme, graceful fallback. | **Create** |
| `bot/formatter.py` | `build_messages`/`_entry` accept + use a `summaries` list. | Modify |
| `bot/main.py` | Delivery fetches excerpts + calls `make_summaries` (only when an Ollama host is set). | Modify |
| `tests/test_summaries.py` | Unit tests for the new module. | **Create** |
| `tests/test_github.py`, `tests/test_formatter.py`, `tests/test_main.py` | Extend. | Modify |

---

## Task 1: `github.readme_excerpt`

**Files:**
- Modify: `bot/github.py`
- Test: `tests/test_github.py` (append)

- [ ] **Step 1: Write the failing tests** — append to `tests/test_github.py` (reuses the existing `_readme_client` helper):

```python
from bot.github import readme_excerpt


def test_readme_excerpt_joins_real_lines_up_to_max():
    body = "# Title\n\n![badge](x)\n\nFirst real line.\nSecond real line.\n"
    assert readme_excerpt("a/b", client=_readme_client(body)) == "First real line. Second real line."


def test_readme_excerpt_truncates_to_max_chars():
    out = readme_excerpt("a/b", client=_readme_client("word " * 500), max_chars=100)
    assert len(out) <= 100


def test_readme_excerpt_returns_empty_on_error():
    assert readme_excerpt("a/b", client=_readme_client("nope", status=404)) == ""
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_github.py -q`
Expected: FAIL — `ImportError: cannot import name 'readme_excerpt'`.

- [ ] **Step 3: Implement** — add to `bot/github.py` (after `readme_first_line`; reuses `_is_noise` and `_API`):

```python
def readme_excerpt(full_name: str, token: str = "",
                   client: httpx.Client | None = None, max_chars: int = 600) -> str:
    """The first ~max_chars of real README prose (skipping headings/badges/rules),
    joined into one line — context for the LLM summarizer. "" on any HTTP error."""
    headers = {"Accept": "application/vnd.github.raw+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    owns_client = client is None
    client = client or httpx.Client(timeout=30)
    try:
        resp = client.get(f"{_API}/repos/{full_name}/readme", headers=headers)
        resp.raise_for_status()
        parts: list[str] = []
        total = 0
        for raw in resp.text.splitlines():
            if _is_noise(raw):
                continue
            line = raw.strip()
            parts.append(line)
            total += len(line) + 1
            if total >= max_chars:
                break
        return " ".join(parts)[:max_chars].strip()
    except httpx.HTTPError:
        return ""
    finally:
        if owns_client:
            client.close()
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_github.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bot/github.py tests/test_github.py
git commit -m "feat(github): add readme_excerpt for summary source material"
```

---

## Task 2: `bot/summaries.py` — `make_summaries`

**Files:**
- Create: `bot/summaries.py`
- Test: `tests/test_summaries.py` (create)

- [ ] **Step 1: Write the failing tests** — create `tests/test_summaries.py`:

```python
from dataclasses import dataclass

import httpx

from bot.summaries import make_summaries


@dataclass(frozen=True)
class R:
    full_name: str = "a/b"
    description: str = "d"


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def _content_client(content):
    return _client(lambda request: httpx.Response(200, json={"message": {"content": content}}))


def test_make_summaries_returns_blurbs_in_order():
    repos = [R("a/one"), R("b/two")]
    out = make_summaries(repos, ["ex1", "ex2"], host="http://x", model="m",
                         client=_content_client('["Blurb one.", "Blurb two."]'))
    assert out == ["Blurb one.", "Blurb two."]


def test_make_summaries_without_host_returns_nones():
    assert make_summaries([R(), R()], host="") == [None, None]


def test_make_summaries_on_error_returns_nones():
    out = make_summaries([R(), R()], host="http://x", model="m",
                         client=_client(lambda request: httpx.Response(500)))
    assert out == [None, None]


def test_make_summaries_length_mismatch_returns_nones():
    out = make_summaries([R(), R(), R()], host="http://x", model="m",
                         client=_content_client('["only one"]'))
    assert out == [None, None, None]


def test_make_summaries_tolerates_fences_and_prose():
    out = make_summaries([R("a/x")], host="http://x", model="m",
                         client=_content_client('Sure:\n```json\n["Clean blurb."]\n```'))
    assert out == ["Clean blurb."]


def test_make_summaries_blank_blurb_becomes_none():
    out = make_summaries([R("a/x"), R("b/y")], host="http://x", model="m",
                         client=_content_client('["", "Real blurb."]'))
    assert out == [None, "Real blurb."]
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_summaries.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'bot.summaries'`.

- [ ] **Step 3: Implement** — create `bot/summaries.py`:

```python
import json
import re

from bot.ollama import chat

_ARR_RE = re.compile(r"\[.*\]", re.S)


def make_summaries(repos, excerpts=None, host: str = "", model: str = "",
                   api_key: str = "", client=None) -> list:
    """One concise, factual English blurb per repo (same order), written by an Ollama
    model from each repo's description + README excerpt. Returns None for a repo when
    unavailable (callers fall back to the repo's own description). No host / chat error /
    bad JSON / length mismatch => all None, so the no-LLM digest is unchanged."""
    n = len(repos)
    if not host or not repos:
        return [None] * n
    excerpts = excerpts or [""] * n
    listing = "\n".join(
        f"{i}. {r.full_name} — {r.description}  [README: {ex}]"
        for i, (r, ex) in enumerate(zip(repos, excerpts))
    )
    prompt = (
        "For each GitHub repository below, write ONE concise, factual sentence "
        "(at most 25 words) in plain English describing what it is and what it does. "
        "No marketing language, no emoji, no hype. Use the description and README "
        "excerpt. Return ONLY a JSON array of strings, one per repo, in the same order.\n\n"
        f"{listing}"
    )
    text = chat(prompt, host=host, model=model, api_key=api_key, client=client)
    match = _ARR_RE.search(text)
    if not match:
        return [None] * n
    try:
        blurbs = json.loads(match.group(0))
    except Exception:
        return [None] * n
    if not isinstance(blurbs, list) or len(blurbs) != n:
        return [None] * n
    return [str(b).strip() or None for b in blurbs]
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_summaries.py -q`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add bot/summaries.py tests/test_summaries.py
git commit -m "feat(summaries): batched LLM repo blurbs with graceful fallback"
```

---

## Task 3: formatter uses the blurb

**Files:**
- Modify: `bot/formatter.py`
- Test: `tests/test_formatter.py` (append)

- [ ] **Step 1: Write the failing tests** — append to `tests/test_formatter.py`:

```python
def test_build_messages_uses_summary_when_present():
    repos = [R(1, "a/b", "u", "raw description", 10, "Go")]
    m = build_messages(_theme(), repos, describe=lambda r: "", titles=["T"],
                       summaries=["A clean blurb."])[0]
    assert "A clean blurb." in m and "raw description" not in m


def test_build_messages_falls_back_when_summary_none():
    repos = [R(1, "a/b", "u", "raw description", 10, "Go")]
    m = build_messages(_theme(), repos, describe=lambda r: "", titles=["T"],
                       summaries=[None])[0]
    assert "raw description" in m
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_formatter.py -q`
Expected: FAIL — `build_messages() got an unexpected keyword argument 'summaries'`.

- [ ] **Step 3: Implement** — in `bot/formatter.py`, update both functions:

```python
def _entry(repo, title, summary, describe, translate) -> str:
    desc = summary or translate(repo.description or describe(repo) or "")
    heading = f'<a href="{repo.html_url}"><b>{escape(title)}</b></a>'
    meta = f"⭐ {repo.stars:,}"
    if repo.language:
        meta += f" · {escape(repo.language)}"
    meta += f" · {escape(repo.full_name)}"
    return f"{heading}\n{meta}\n{escape(desc)}".rstrip()


def build_messages(theme, repos, describe, translate=lambda s: s, titles=None,
                   summaries=None) -> list[str]:
    header = f"{theme.emoji} <b>{escape(theme.name)}</b>".strip()
    if titles is None:
        titles = [r.full_name for r in repos]
    if summaries is None:
        summaries = [None] * len(repos)
    messages: list[str] = []
    current = header
    for repo, title, summary in zip(repos, titles, summaries):
        block = _entry(repo, title, summary, describe, translate)
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

Run: `.venv/bin/python -m pytest tests/test_formatter.py -q`
Expected: PASS — the two new tests plus all existing formatter tests (which omit `summaries`, so it defaults to `[None]*` and behavior is unchanged).

- [ ] **Step 5: Commit**

```bash
git add bot/formatter.py tests/test_formatter.py
git commit -m "feat(formatter): render the LLM blurb, fall back to description"
```

---

## Task 4: wire summaries into `main`

**Files:**
- Modify: `bot/main.py`
- Test: `tests/test_main.py` (update `_cfg`, append a test)

- [ ] **Step 1: Update `_cfg` and write the failing test** — in `tests/test_main.py`, change `_cfg` to allow an Ollama host, then append the wiring test:

```python
def _cfg(tmp_path, themes, delay=0, ollama=""):
    # ollama="" keeps make_titles/make_summaries/translate offline; the summary branch
    # (README fetch + make_summaries) only runs when ollama is set.
    return Config("tok", "-100", "", str(tmp_path), themes, ollama, send_delay_seconds=delay)
```

```python
def test_run_uses_llm_summaries_in_output(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(main, "search_repos", lambda *a, **k: [_repo(1, 10)])
    monkeypatch.setattr(main, "readme_first_line", lambda *a, **k: "")
    monkeypatch.setattr(main, "readme_excerpt", lambda *a, **k: "readme stuff")
    monkeypatch.setattr(main, "make_titles", lambda repos, **k: ["Title"])
    monkeypatch.setattr(main, "make_summaries", lambda repos, excerpts, **k: ["LLM blurb here."])
    monkeypatch.setattr(main, "send_message", lambda *a, **k: sent.append(a[2]) or {"ok": True})
    theme = Theme(key="t", name="T", emoji="", query="q", count=1)
    main.run(_cfg(tmp_path, [theme], ollama="http://x"), today=date(2026, 6, 4))
    assert any("LLM blurb here." in m for m in sent)
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_main.py::test_run_uses_llm_summaries_in_output -q`
Expected: FAIL — `AttributeError: module 'bot.main' has no attribute 'readme_excerpt'` (or `make_summaries`).

- [ ] **Step 3: Implement** — in `bot/main.py`:

(a) Extend the imports:

```python
from bot.github import search_repos, readme_first_line, readme_excerpt
```
```python
from bot.summaries import make_summaries
```

(b) In Phase 2, replace the `titles = … ; messages = build_messages(…)` lines. Find:

```python
            titles = make_titles(picked, host=config.ollama_host,
                                 model=config.ollama_model, api_key=config.ollama_api_key)
            messages = build_messages(theme, picked, describe, translate, titles)
```

Replace with (fetch README excerpts + blurbs only when an Ollama host is configured, so a no-LLM run skips ~70 pointless fetches and stays byte-identical to today):

```python
            summaries = None
            if config.ollama_host:
                excerpts = [readme_excerpt(r.full_name, token=config.github_token) for r in picked]
                summaries = make_summaries(picked, excerpts, host=config.ollama_host,
                                           model=config.ollama_model, api_key=config.ollama_api_key)
            titles = make_titles(picked, host=config.ollama_host,
                                 model=config.ollama_model, api_key=config.ollama_api_key)
            messages = build_messages(theme, picked, describe, translate, titles, summaries)
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_main.py -q`
Expected: PASS — the new wiring test, plus all existing main tests (they use `_cfg` with `ollama=""`, so the summary branch is skipped and `readme_excerpt`/`make_summaries` are never called — no network, behavior unchanged).

- [ ] **Step 5: Run the FULL suite + commit**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS — all tests (93 + new summaries/github/formatter/main tests).

```bash
git add bot/main.py tests/test_main.py
git commit -m "feat(main): generate LLM summaries (README-backed) in delivery"
```

---

## Self-Review (run after writing; fix inline)

**Spec coverage:**
- §4.1 `make_summaries` (batched, fallback, fences, length-mismatch) → Task 2. ✓
- §4.2 `readme_excerpt` → Task 1. ✓
- §4.3 formatter uses blurb, falls back → Task 3. ✓
- §4.4 main wiring → Task 4. ✓
- §6 fallback (no host / error / mismatch → description) → `make_summaries` returns `None`s + formatter fallback (Tasks 2,3); the `if config.ollama_host` guard covers the no-host case in Task 4. ✓
- §8 testing — every listed test maps to a task. ✓

**Type/signature consistency:**
- `make_summaries(repos, excerpts=None, host, model, api_key, client)` — defined Task 2, called Task 4 with `(picked, excerpts, host=…, model=…, api_key=…)`. ✓
- `readme_excerpt(full_name, token, client, max_chars)` — Task 1; called Task 4 with `(r.full_name, token=…)`. ✓
- `build_messages(…, titles=None, summaries=None)` / `_entry(repo, title, summary, describe, translate)` — Task 3; called Task 4 with `(theme, picked, describe, translate, titles, summaries)`. ✓
- `_cfg(tmp_path, themes, delay=0, ollama="")` — Task 4; existing callers use defaults (`ollama=""`). ✓

**Placeholder scan:** none — all code, prompts, and commands are concrete.

---

## Execution Handoff

After Task 4 the branch is green. Next: a live dry-run with the Ollama key (`python -m bot --dry-run`) to eyeball the actual blurbs vs the old descriptions, then PR/merge.
