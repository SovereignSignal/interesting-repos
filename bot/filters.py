import re
from datetime import date, datetime

KEYWORD_REPEAT_THRESHOLD = 5     # a length>=3 token repeated this many times => stuffed
VELOCITY_CEILING = 2500.0        # stars/day above which a repo is a fallback-only outlier

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def age_days(iso: str, today: date) -> int | None:
    """Whole days between an ISO-8601 timestamp's date and `today`. None if unparseable."""
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (today - dt.date()).days


def star_velocity(repo, today: date) -> float:
    """Stars per day since creation, with an age floor of 1 day (avoids div-by-zero
    and treats unknown/just-created repos as 1 day old)."""
    age = age_days(repo.created_at, today)
    return repo.stars / max(age or 1, 1)


def is_keyword_stuffed(repo) -> bool:
    """A description that repeats one token many times is keyword spam
    (e.g. 'hyperliquid sdk | hyperliquid sdk | ...')."""
    counts: dict[str, int] = {}
    for tok in _TOKEN_RE.findall((repo.description or "").lower()):
        if len(tok) >= 3:
            counts[tok] = counts.get(tok, 0) + 1
            if counts[tok] >= KEYWORD_REPEAT_THRESHOLD:
                return True
    return False


def is_awesome_list(repo) -> bool:
    """'awesome-*' curated link lists, by repo name or topic."""
    name = repo.full_name.split("/")[-1].lower()
    if name == "awesome" or name.startswith("awesome-"):
        return True
    return any(t.lower() in ("awesome", "awesome-list") for t in repo.topics)


def is_stale(repo, today: date, max_idle_days: int) -> bool:
    """True if not pushed within max_idle_days. Unknown push date => not stale (kept)."""
    idle = age_days(repo.pushed_at, today)
    return idle is not None and idle > max_idle_days


def clean(repos: list, today: date, max_idle_days: int) -> list:
    """Drop deterministic noise (keyword-stuffed, awesome-list) and stale repos,
    preserving order. The high-precision drops here also protect the stars-fallback."""
    return [r for r in repos
            if not is_keyword_stuffed(r)
            and not is_awesome_list(r)
            and not is_stale(r, today, max_idle_days)]


# --- Diversity: deterministic agent-skill / AI classification (Curation v3) ---
# LLM enforcement of the per-theme cap was falsified (5 models failed); pool-shaping
# is deterministic instead. is_agent_skill_pack is NARROW (clone collections);
# is_ai_repo is BROAD (anything AI/agent/LLM, used only for the cap=0 non-AI slot).

_PACK_TOPICS = {"agent-skills", "claude-skills", "claude-code-skills", "skills",
                "subagents", "agent-skill"}
_PACK_DESC = ("skills for", "skill pack", "agent skill", "subagent", "collection of skills")
_PACK_NUM_RE = re.compile(r"\b\d{2,}\b.{0,30}\bskills?\b")

_AI_TOPICS = {"ai", "ai-agent", "ai-agents", "agent", "agents", "agentic", "llm", "llms",
              "mcp", "mcp-server", "rag", "claude", "claude-code", "codex", "cursor",
              "gemini", "copilot", "openclaw", "anthropic", "ai-tools", "genai", "gpt",
              "chatgpt", "ai-agent-tools"}
_AI_MARKERS = ("ai agent", "ai-agent", "agentic", "agent-native", "agent-first",
               "multi-agent", "autonomous agent", "claude code", "claude-code", "codex",
               "cursor", "gemini cli", "copilot", "mcp server", " mcp ", "openclaw",
               "anthropic", "ai coding", "ai-powered", "ai-native", "coding agent",
               "ai assistant", "ai memory", "ai workspace", "co-scientist", "llm",
               "large language model")


def is_agent_skill_pack(repo) -> bool:
    """Narrow: a generic collection of skills/subagents/prompts (a 'clone'), as opposed
    to a substantive standalone tool. Targets what the cap=N policy should cut."""
    if "skill" in repo.full_name.split("/")[-1].lower():
        return True
    if {t.lower() for t in repo.topics} & _PACK_TOPICS:
        return True
    d = (repo.description or "").lower()
    return any(p in d for p in _PACK_DESC) or bool(_PACK_NUM_RE.search(d))


def is_ai_repo(repo) -> bool:
    """Broad: any AI / agent / LLM repo (a skill pack, an AI topic, or an AI marker in
    the name/description). Used only for the cap=0 (non-AI highlight) policy, where
    aggressive exclusion is intended and false positives are harmless."""
    if is_agent_skill_pack(repo):
        return True
    if {t.lower() for t in repo.topics} & _AI_TOPICS:
        return True
    blob = (repo.full_name + " " + (repo.description or "")).lower()
    return any(m in blob for m in _AI_MARKERS)


def cap_agent_skills(repos: list, cap) -> list:
    """Shape a theme's candidate pool per its agent_skill_cap (order preserved):
    None → unchanged; 0 → drop every AI repo (the non-AI highlight); N → keep all
    non-packs plus at most N agent-skill packs (cut the clones, keep distinct tools)."""
    if cap is None:
        return repos
    if cap == 0:
        return [r for r in repos if not is_ai_repo(r)]
    out, packs = [], 0
    for r in repos:
        if is_agent_skill_pack(r):
            if packs >= cap:
                continue
            packs += 1
        out.append(r)
    return out
