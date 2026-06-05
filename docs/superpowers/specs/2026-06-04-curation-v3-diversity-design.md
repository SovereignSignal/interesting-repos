# Design: Curation v3 — Diversity & Breadth

- **Date:** 2026-06-04
- **Status:** Draft (brainstorming) — awaiting user review
- **Owner:** sov@sovereignsignal.com
- **Builds on:** `2026-06-02-curation-v2-design.md` (dedup + recency + spam filters, merged)

## 1. Summary

Break the AI-agent-skill **monoculture** in the digest, two ways at once:

1. **Cut the clones** — an LLM-enforced per-theme cap on generic AI-agent/skill-pack
   repos, so no theme is a wall of interchangeable "skills for Claude Code" repos.
2. **Add breadth** — reshape *Trending Overall* into the non-AI highlight (cap 0),
   and add **four new non-AI themes** (Systems & Languages, Data & Databases, Web &
   Frontend, Science & Simulation), taking the lineup from 6 → **10 themes**.

The mechanism is a single new per-theme field, `agent_skill_cap`, whose value *is*
the policy: `None` = no cap, `0` = exclude agent-skill repos, `N` = at most N. The
cap is **enforced by the curator LLM** via an explicit, parameterized prompt
instruction (chosen over a deterministic classifier — see §7).

## 2. Motivation

v2 fixed cross-theme *duplication*, but the June 1 digest was still **~75–80%
AI-agent/Claude-skill repos across all six themes** — even with a soft "prefer a
diverse set" line already in the curation prompt. The sameness is the remaining
quality problem.

A live probe (2026-06-04) confirms the agent-skill flood reaches *every* topic, which
is why a per-theme cap is the right lever and why new "non-AI" themes still need it:

| Probed theme query | Returned | Agent-skill in top 12 |
|---|---|---|
| `topic:systems-programming` | ~12 | 2/12 |
| `topic:database` | ~20 | 2/12 |
| `topic:frontend` | ~14 | 4/12 |
| `topic:scientific-computing` | ~17 | 4/12 |

Systems & Data come up genuinely non-AI; Web & Science are ~⅓ infiltrated — exactly
where `cap=2` does visible work.

## 3. Goals / Non-goals

**Goals**
- No theme (except AI & Agents) is dominated by generic agent-skill packs; each
  surfaces its genuine domain content.
- *Trending Overall* becomes a deliberately non-AI cross-domain highlight.
- The digest spans 10 domains, structurally widening beyond AI tooling.
- The cap policy is expressed as one config field per theme; adding/retuning a theme
  is a `themes.toml` edit, not a code change.
- Prompt construction is deterministic and unit-tested (the LLM's *judgment* is
  verified by a live dry-run, not unit tests).

**Non-goals (this iteration)**
- No deterministic agent-skill classifier (explicitly chosen against — §7).
- No diversity enforcement on the **fallback** path (when Ollama is down, the digest
  reverts to v2's stars+velocity sort; spam-safe but not diversity-controlled).
- No model change (that's the deferred **C**; B is coupled to it — §7).
- No "why interesting" line, presentation work, or alerting.

## 4. Detailed design

### 4.1 The `agent_skill_cap` field

A new optional per-theme field, `agent_skill_cap: int | None = None`. Its value is
the diversity policy:

| Value | Meaning | Themes |
|---|---|---|
| `None` (default) | no cap — the curator may pick freely | **AI & Agents** |
| `0` | exclude generic agent-skill packs entirely | **Trending** (the non-AI highlight) |
| `N` (we use `2`) | at most N agent-skill packs; fill the rest from distinct domains | the other 8 themes |

`None` (the dataclass default) means existing/unspecified themes behave exactly as
today, so the field is backward-compatible.

### 4.2 LLM-driven enforcement (prompt parameterization)

`_rank_llm` reads `theme.agent_skill_cap` and injects a directive into the prompt. A
shared, in-prompt **definition** gives `gemma3` a concrete target:

> A "generic AI-agent/skill pack" is a repo whose main offering is a collection of
> skills, subagents, prompts, or MCP servers for coding agents (Claude Code, Codex,
> Cursor, Gemini CLI, Copilot, OpenClaw, etc.) — as opposed to a substantive
> standalone tool, library, app, or framework.

Then, by cap value:
- `None` → no directive added (today's prompt).
- `0` → *"Do NOT select ANY generic AI-agent/skill packs for this list — it is for
  everything else that's trending."*
- `N ≥ 1` → *"Select AT MOST N generic AI-agent/skill packs; fill the remaining slots
  with substantive projects from distinct domains."*

This **replaces** v2's softer standalone sentence, currently in `_rank_llm`:
*"Prefer a DIVERSE set — do not fill the list with many near-identical projects (e.g.
interchangeable 'AI coding agent skill' packs); pick the most distinctive."* — so the
prompt has one clear, parameterized diversity rule rather than two overlapping ones.

`_rank_llm` already receives `theme`, so no new parameter is needed — it reads the cap
off the theme.

### 4.3 The 10-theme lineup

Existing 6 (caps added) + 4 new (validated queries from the 2026-06-04 probe):

| # | Theme | key | query | `agent_skill_cap` | notes |
|---|---|---|---|---|---|
| 1 | 📈 Trending Overall | `trending` | (unchanged) | **0** | already `catch_all` → now also non-AI |
| 2 | 🤖 AI & Agents | `ai-agents` | (unchanged) | **None** | should be all agents |
| 3 | 🛠️ Dev Tools & CLI | `dev-tools` | (unchanged) | **2** | |
| 4 | ⛓️ Crypto & Web3 | `crypto` | (unchanged) | **2** | |
| 5 | 💰 Finance & Quant | `finance` | (unchanged) | **2** | |
| 6 | 🔐 Security | `security` | (unchanged) | **2** | |
| 7 | ⚙️ Systems & Languages | `systems` | `topic:systems-programming created:>{since:180d} stars:>10` | **2** | wide window — thin but high-quality pool |
| 8 | 🗄️ Data & Databases | `data` | `topic:database created:>{since:120d} stars:>50` | **2** | strong, low-infiltration pool |
| 9 | 🌐 Web & Frontend | `web` | `topic:frontend created:>{since:120d} stars:>50` | **2** | cap active (~⅓ infiltrated) |
| 10 | 🔬 Science & Simulation | `science` | `topic:scientific-computing created:>{since:180d} stars:>20` | **2** | thinnest; may underfill some weeks |

All 10 keep `rank = "llm"`, `count = 7`, `max_idle_days = 60`. New themes get a
`profile` guiding the curator toward substantive domain content (see §5).

## 5. Configuration changes

`Theme` gains one field:

```python
agent_skill_cap: int | None = None
```

`load_themes` reads `t.get("agent_skill_cap")` (default `None` — note: not `0`, so an
absent key means "no cap"). `themes.toml`:
- add `agent_skill_cap = 0` to `trending`;
- add `agent_skill_cap = 2` to `dev-tools`, `crypto`, `finance`, `security`;
- leave `ai-agents` without the key (→ `None`);
- add the 4 new `[[theme]]` blocks with `agent_skill_cap = 2` and profiles, e.g.:

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
```

(`data`/`web`/`science` analogous, with the §4.3 queries and matching profiles.)

## 6. What this deliberately does NOT change

- **Fallback path:** `rank_by_stars` (used when Ollama errors) ignores the cap — no
  diversity control, by design. v2's velocity guard still keeps star-farms from
  leading; the degraded path was never curated.
- **v2 deterministic filters** (`clean`: keyword-stuffing, awesome-list, staleness)
  still run *before* curation and compose with the cap — they pre-drop noise (incl.
  the awesome-lists seen in the new themes' pools) so the LLM sees a cleaner set.
- **Dedup, recency, ordering** from v2 are untouched. Trending stays `catch_all`
  (selected last); `agent_skill_cap = 0` stacks on top of its cross-domain dedup.

## 7. Risks & verification

**Decision: LLM-driven over deterministic.** A deterministic `is_agent_skill()`
classifier was considered and rejected (user call): agent-skill detection is fuzzy
(distinguishing a real tool that uses agents from a generic skill pack), and a
keyword/topic list is brittle. The LLM handles fuzzy classification better. The cost
is the two risks below.

1. **Compliance is unproven and unverifiable without the Ollama key.** B's whole
   mechanism *is* the LLM, so — unlike v2's deterministic half — it cannot be verified
   keyless. The June 1 evidence (75% agents *with* a soft diversity hint) means an
   explicit numeric cap may or may not land. **Verification:** a live dry-run with the
   key must confirm the curator actually honors `0` (exclude) and `2` (cap) before
   this is trusted in production.
2. **Coupling to C (stronger model).** If a 12B model won't honor a hard cap, the fix
   is a better instruction-follower, not more prompt-wrangling — i.e. pull the deferred
   **C** forward. B's live dry-run is the go/no-go signal for that.

## 8. Testing

The deterministic seam is **prompt construction**, which is fully unit-testable even
though enforcement is the LLM's:

- `_rank_llm` with `theme.agent_skill_cap = 0` → prompt contains the "Do NOT select"
  exclusion text (and the agent-skill definition).
- `= 2` → prompt contains "AT MOST 2".
- `= None` → prompt contains neither directive (matches v2 behavior).
- `load_themes` reads `agent_skill_cap` (present → int, absent → `None`).
- Existing ranker/main/config tests stay green (the field defaults to `None`).

The curator's *actual* diversity behavior is validated by the live dry-run (§7),
not unit tests.

## 9. File change map

| File | Change |
|---|---|
| `bot/config.py` | `Theme.agent_skill_cap: int \| None = None`; `load_themes` reads it. |
| `bot/ranker.py` | `_rank_llm` injects the agent-skill definition + cap directive from `theme.agent_skill_cap`; remove the old standalone diversity sentence. |
| `themes.toml` | caps on 5 existing themes; 4 new theme blocks (queries + profiles). |
| `tests/test_config.py` | `agent_skill_cap` read (present/absent). |
| `tests/test_ranker.py` | prompt carries the right directive per cap value (0 / N / None). |

(No change to `main.py`, `github.py`, `filters.py`, `state.py` — the cap rides
entirely through the existing theme → ranker path.)

## 10. Out of scope (future)

- **C**: stronger curation model and/or per-repo scoring + a "why interesting" line —
  now explicitly coupled to B's outcome.
- Deterministic diversity backstop for the fallback path.
- Per-theme retuning of queries/profiles once real digests are observed.
- GitHub `Retry-After`, presentation/preview images, failure alerting.

## 11. Revision (2026-06-04): deterministic enforcement

A live dry-run **falsified the LLM-driven approach (§7).** Five models — `gemma3:12b`,
`gemma3:27b`, `gpt-oss:120b`, `qwen3-next:80b`, `glm-4.7` — all failed to honor the
cap; the strongest (`qwen3-next:80b`) was the **worst** (Trending 1/7 non-AI; the
754-skill `Anthropic-Cybersecurity-Skills` pack returned to Security; `finance-skills`
returned to Finance). "Exclude all of category X" and "at most N of X" are negative /
counting constraints LLMs don't reliably follow — and a more capable model optimizing
for relevance fights the constraint *harder*.

Enforcement therefore moves from the **prompt** to deterministic **pool-shaping**. The
`agent_skill_cap` field (§4.1) stays; only the mechanism changes:

- **`bot/filters.py`** gains:
  - `is_agent_skill_pack(repo)` — *narrow*: a skill/subagent/prompt collection (repo
    name contains "skill"; a skill topic; or a description pattern like "skills for",
    "agent skill", "subagent", "N skills"). Targets the **clones**, not standalone AI
    tools.
  - `is_ai_repo(repo)` — *broad*: any AI/agent/LLM repo (skill-pack, OR an AI topic,
    OR a marker like "claude code", "ai agent", "mcp server", "llm", "agentic").
  - `cap_agent_skills(repos, cap)` — `None` → passthrough; `0` → drop every
    `is_ai_repo` (Trending becomes non-AI); `N` → keep all non-packs plus at most `N`
    `is_agent_skill_pack` repos (order preserved). High-precision hard drops only.
- **`bot/main.py`** Phase 1 applies `cap_agent_skills(repos, theme.agent_skill_cap)`
  after cross-theme dedup, before the `CANDIDATE_LIMIT` slice and ranking.
- **`bot/ranker.py`** reverts the §4.2 prompt directive (`_AGENT_SKILL_DEF` /
  `_diversity_directive` removed) — the pool is pre-shaped, so the prompt needs no cap
  instruction. `gemma3:12b` stays; it ranks relevance fine on a shaped pool.

**Accepted reality:** Trending underfills to ~2–3 repos — only that many genuinely
non-AI repos are trending in mid-2026. Honest, not a bug.

**Testing shift:** the deterministic classifiers + `cap_agent_skills` are unit-tested
directly (truth tables), and the end-to-end effect is validated **keyless** on the
real pool (no further Ollama spend). The §8 prompt-directive tests are removed.
