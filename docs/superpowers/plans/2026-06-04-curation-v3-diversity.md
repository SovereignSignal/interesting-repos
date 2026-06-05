# Curation v3 (Diversity & Breadth) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Break the AI-agent-skill monoculture via an LLM-enforced per-theme `agent_skill_cap` (None/0/N) and four new non-AI themes, taking the digest from 6 → 10 themes.

**Architecture:** One new optional `Theme` field, `agent_skill_cap`, whose value *is* the policy. `_rank_llm` turns it into an explicit prompt directive (`None` → nothing, `0` → exclude agent-skill packs, `N` → at most N), replacing v2's soft diversity sentence. `themes.toml` sets caps and adds the four themes. No `main.py`/`filters.py` change — the cap rides the existing theme → ranker path.

**Tech Stack:** Python 3.11+, httpx (mocked via `httpx.MockTransport`), tomllib, pytest. Enforcement is the Ollama LLM; the deterministic, unit-tested seam is *prompt construction*.

**Spec:** `docs/superpowers/specs/2026-06-04-curation-v3-diversity-design.md`

**Prerequisite:** On branch `curation-v3-diversity` (spec committed). Run tests with `.venv/bin/python -m pytest`.

---

## File Structure

| File | Responsibility | Action |
|---|---|---|
| `bot/config.py` | `Theme` gains `agent_skill_cap: int \| None`. | Modify |
| `bot/ranker.py` | `_rank_llm` injects the agent-skill definition + cap directive; old soft sentence removed. | Modify |
| `themes.toml` | caps on 5 existing themes; 4 new theme blocks. | Modify |
| `tests/test_config.py` | `agent_skill_cap` read (present int / present 0 / absent → None). | Modify |
| `tests/test_ranker.py` | prompt carries the right directive per cap value. | Modify |

---

## Task 1: `Theme.agent_skill_cap`

**Files:**
- Modify: `bot/config.py` (the `Theme` dataclass + `load_themes`)
- Test: `tests/test_config.py` (append)

- [ ] **Step 1: Write the failing tests** — append to `tests/test_config.py`:

```python
def test_load_themes_reads_agent_skill_cap(tmp_path):
    p = tmp_path / "t.toml"
    p.write_text('[[theme]]\nkey="k"\nname="N"\nquery="q"\nagent_skill_cap=2\n')
    assert load_themes(str(p))[0].agent_skill_cap == 2

def test_load_themes_agent_skill_cap_absent_is_none(tmp_path):
    p = tmp_path / "t.toml"
    p.write_text('[[theme]]\nkey="k"\nname="N"\nquery="q"\n')
    assert load_themes(str(p))[0].agent_skill_cap is None

def test_load_themes_agent_skill_cap_zero_is_kept(tmp_path):
    # 0 must survive as 0 (exclude policy), not be coerced to None
    p = tmp_path / "t.toml"
    p.write_text('[[theme]]\nkey="k"\nname="N"\nquery="q"\nagent_skill_cap=0\n')
    assert load_themes(str(p))[0].agent_skill_cap == 0
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_config.py -q`
Expected: FAIL — `AttributeError: 'Theme' object has no attribute 'agent_skill_cap'`.

- [ ] **Step 3: Implement** — in `bot/config.py`, add one field to the END of `Theme`:

```python
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
    catch_all: bool = False
    max_idle_days: int = 60
    agent_skill_cap: int | None = None
```

And in `load_themes`, add one key to the `Theme(...)` construction (use `.get` with no default so an absent key is `None` while a present `0` stays `0`):

```python
            catch_all=t.get("catch_all", False),
            max_idle_days=t.get("max_idle_days", 60),
            agent_skill_cap=t.get("agent_skill_cap"),
        ))
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_config.py -q`
Expected: PASS (existing config tests unaffected; 3 new pass).

- [ ] **Step 5: Commit**

```bash
git add bot/config.py tests/test_config.py
git commit -m "feat(config): add Theme.agent_skill_cap"
```

---

## Task 2: LLM-enforced cap in the curation prompt

**Files:**
- Modify: `bot/ranker.py` (`_rank_llm` + a new helper)
- Test: `tests/test_ranker.py` (append a helper + 3 tests)

- [ ] **Step 1: Write the failing tests** — append to `tests/test_ranker.py`:

```python
def _prompt_for(cap):
    captured = {}
    def handler(request):
        import json as _json
        captured["p"] = _json.loads(request.content)["messages"][0]["content"]
        return httpx.Response(200, json={"message": {"content": "[0]"}})
    theme = Theme(key="t", name="T", emoji="", query="q", rank="llm", count=7,
                  agent_skill_cap=cap)
    _rank_llm([R(1, 10, created_at="2026-06-01T00:00:00Z")], theme, "http://x", "m", "k",
              today=date(2026, 6, 4), client=_client(handler))
    return captured["p"]

def test_prompt_excludes_agent_skills_when_cap_zero():
    assert "Do NOT select ANY generic AI-agent/skill packs" in _prompt_for(0)

def test_prompt_caps_agent_skills_when_cap_set():
    assert "AT MOST 2 generic AI-agent/skill packs" in _prompt_for(2)

def test_prompt_has_no_diversity_directive_when_cap_none():
    p = _prompt_for(None)
    assert "AT MOST" not in p
    assert "Do NOT select" not in p
    assert "Prefer a DIVERSE set" not in p   # the old soft sentence is removed
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_ranker.py -q`
Expected: FAIL — `cap=0`/`cap=2` tests (no such text yet) and the `cap=None` test (the old "Prefer a DIVERSE set" sentence is still present).

- [ ] **Step 3: Implement** — in `bot/ranker.py`:

(a) Add the module-level definition + helper just above `_rank_llm` (after `_describe_age`):

```python
_AGENT_SKILL_DEF = (
    "A 'generic AI-agent/skill pack' is a repo whose main offering is a collection of "
    "skills, subagents, prompts, or MCP servers for coding agents (Claude Code, Codex, "
    "Cursor, Gemini CLI, Copilot, OpenClaw, etc.) — not a substantive standalone tool, "
    "library, app, or framework. "
)


def _diversity_directive(cap) -> str:
    """Per-theme agent-skill policy line for the prompt (cap = theme.agent_skill_cap)."""
    if cap is None:
        return ""
    if cap == 0:
        return (_AGENT_SKILL_DEF + "Do NOT select ANY generic AI-agent/skill packs for "
                "this list — it is for everything else that's trending.\n")
    return (_AGENT_SKILL_DEF + f"Select AT MOST {cap} generic AI-agent/skill packs; fill "
            "the remaining slots with substantive projects from distinct domains.\n")
```

(b) In `_rank_llm`'s `prompt`, replace the recency-line-plus-old-diversity-sentence-plus-Candidates block. Find:

```python
        "Prefer repos that are fresh and actively maintained (recently pushed). Discount "
        "stale repos (not pushed in weeks) and repos whose stars grew implausibly fast "
        "(very high ★/day), which are often star-farmed.\n"
        "Prefer a DIVERSE set — do not fill the list with many near-identical projects "
        "(e.g. interchangeable 'AI coding agent skill' packs); pick the most distinctive.\n\n"
        f"Candidates (index. owner/name (stars, age, velocity) — description [topics]):\n{listing}\n\n"
```

Replace with (drops the old sentence; inserts the cap directive then a blank line):

```python
        "Prefer repos that are fresh and actively maintained (recently pushed). Discount "
        "stale repos (not pushed in weeks) and repos whose stars grew implausibly fast "
        "(very high ★/day), which are often star-farmed.\n"
        f"{_diversity_directive(theme.agent_skill_cap)}"
        f"\nCandidates (index. owner/name (stars, age, velocity) — description [topics]):\n{listing}\n\n"
```

(With `cap=None` the directive is `""`, so the text reads `…star-farmed.\n\nCandidates…` — identical spacing to today, just without the diversity sentence.)

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_ranker.py -q`
Expected: PASS — the 3 new tests plus all existing ranker tests (the age/velocity test is unaffected).

- [ ] **Step 5: Commit**

```bash
git add bot/ranker.py tests/test_ranker.py
git commit -m "feat(ranker): LLM-enforced per-theme agent-skill cap in the curation prompt"
```

---

## Task 3: Apply caps + add the 4 new themes in `themes.toml`

**Files:**
- Modify: `themes.toml`

- [ ] **Step 1: Add `agent_skill_cap = 0` to `trending`** — find (the trending block, post-v2 it has `catch_all = true`):

```toml
rank  = "llm"
catch_all = true
profile = "Breakout projects from the last few months that a broad developer audience genuinely finds interesting
```

Replace with:

```toml
rank  = "llm"
catch_all = true
agent_skill_cap = 0
profile = "Breakout projects from the last few months that a broad developer audience genuinely finds interesting
```

- [ ] **Step 2: Add `agent_skill_cap = 2` to the four domain themes** — make these four edits (each profile prefix is unique, so the anchor is unambiguous):

`dev-tools`:
```toml
rank  = "llm"
profile = "Genuinely useful developer tools
```
→
```toml
rank  = "llm"
agent_skill_cap = 2
profile = "Genuinely useful developer tools
```

`crypto`:
```toml
rank  = "llm"
profile = "Substantive crypto / web3 / DeFi projects
```
→
```toml
rank  = "llm"
agent_skill_cap = 2
profile = "Substantive crypto / web3 / DeFi projects
```

`finance`:
```toml
rank  = "llm"
profile = "Serious quantitative finance
```
→
```toml
rank  = "llm"
agent_skill_cap = 2
profile = "Serious quantitative finance
```

`security`:
```toml
rank  = "llm"
profile = "Substantive security tooling
```
→
```toml
rank  = "llm"
agent_skill_cap = 2
profile = "Substantive security tooling
```

(`ai-agents` is deliberately left without the key → `None` → uncapped.)

- [ ] **Step 3: Append the 4 new theme blocks** at the end of `themes.toml`:

```toml
[[theme]]
key   = "systems"
name  = "Systems & Languages"
emoji = "⚙️"
query = "topic:systems-programming created:>{since:180d} stars:>10"
sort  = "stars"
count = 7
rank  = "llm"
agent_skill_cap = 2
profile = "Substantive systems & low-level work — languages, compilers, runtimes, OS, emulators, allocators, performance engineering. Favor real implementations over tutorials or skill packs."

[[theme]]
key   = "data"
name  = "Data & Databases"
emoji = "🗄️"
query = "topic:database created:>{since:120d} stars:>50"
sort  = "stars"
count = 7
rank  = "llm"
agent_skill_cap = 2
profile = "Substantive databases, query and storage engines, data pipelines, and analytics tooling with real engineering. Favor standalone tools and engines over agent-skill packs."

[[theme]]
key   = "web"
name  = "Web & Frontend"
emoji = "🌐"
query = "topic:frontend created:>{since:120d} stars:>50"
sort  = "stars"
count = 7
rank  = "llm"
agent_skill_cap = 2
profile = "Substantive web and frontend projects — frameworks, build tools, UI toolkits, browsers, rendering — with real utility. Favor standalone tools over design-agent skill packs."

[[theme]]
key   = "science"
name  = "Science & Simulation"
emoji = "🔬"
query = "topic:scientific-computing created:>{since:180d} stars:>20"
sort  = "stars"
count = 7
rank  = "llm"
agent_skill_cap = 2
profile = "Substantive scientific computing and simulation — numerical methods, physics and engineering simulation, scientific ML (PINNs and similar), graphics and compute. Favor real research code and engines."
```

- [ ] **Step 4: Verify `themes.toml` parses with the right caps**

Run:
```bash
.venv/bin/python -c "from bot.config import load_themes; ts=load_themes('themes.toml'); caps={t.key:t.agent_skill_cap for t in ts}; print(len(ts), caps); assert len(ts)==10; assert caps['trending']==0; assert caps['ai-agents'] is None; assert all(caps[k]==2 for k in ['dev-tools','crypto','finance','security','systems','data','web','science']); print('OK')"
```
Expected: `10 {…}` then `OK` (no assertion error).

- [ ] **Step 5: Run the FULL suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS — all tests (v2's 76 + the new config/ranker tests).

- [ ] **Step 6: Commit**

```bash
git add themes.toml
git commit -m "feat(themes): cap agent-skill packs; add 4 non-AI themes (lineup 6->10)"
```

---

## Self-Review (run after writing; fix inline)

**Spec coverage:**
- §4.1 `agent_skill_cap` field (None/0/N) → Task 1. ✓
- §4.2 LLM enforcement via prompt + agent-skill definition, replacing the old sentence → Task 2. ✓
- §4.3 10-theme lineup with caps + 4 validated queries → Task 3. ✓
- §5 config field + `themes.toml` edits → Tasks 1 & 3. ✓
- §8 testing (prompt directive per cap; `agent_skill_cap` read incl. 0 vs absent) → Tasks 1 & 2. ✓
- §6 fallback unchanged / no `main`/`filters` change → no task touches them (correct). ✓

**Type/signature consistency:**
- `Theme.agent_skill_cap: int | None` (Task 1) read via `theme.agent_skill_cap` (Task 2) and `t.get("agent_skill_cap")` (Task 1). ✓
- `_diversity_directive(cap)` defined + called with `theme.agent_skill_cap` (Task 2). ✓
- Test fixture `Theme(..., agent_skill_cap=cap)` (Task 2) matches the field added in Task 1. ✓

**Placeholder scan:** none — all code, queries, and profiles are concrete.

---

## Execution Handoff

After Task 3 the branch is green. Next: the **live dry-run with the Ollama key** is the real acceptance test (does `gemma3` honor `0`/`2`?) — that's the §7 go/no-go for trusting B and the signal for whether to pull C forward. Then PR/merge as preferred.
