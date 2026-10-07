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
              "chatgpt", "ai-agent-tools", "generative-ai", "machine-learning",
              "deep-learning"}
_AI_MARKERS = ("ai agent", "ai-agent", "agentic", "agent-native", "agent-first",
               "multi-agent", "autonomous agent", "claude code", "claude-code", "codex",
               "cursor", "gemini cli", "copilot", "mcp server", " mcp ", "openclaw",
               "anthropic", "ai coding", "ai-powered", "ai-native", "coding agent",
               "ai assistant", "ai memory", "ai workspace", "co-scientist", "llm",
               "large language model",
               # Model-family names. Word boundaries, never a bare "ai" or "agent".
               "deepseek", "qwen", "ollama", "openai", "mistral", "llama", "grok", "gemma",
               # Both separators: repo names use hyphens ("Embodied-AI", "world-model").
               "embodied ai", "embodied-ai", "vision-language", "vision language",
               "world model", "world-model", "inference engine", "inference-engine",
               "agent harness", "agent-harness")
# Owner login only. Punctuation-separated "ai" (deepseek-ai) or a glued token of
# length >= 5 ending in "ai" (sqliteai, genai). Descriptions are not scanned, so
# words such as "email" and "available" stay non-AI. There is no bare "ai" scan.
_OWNER_SEP_RE = re.compile(r"[-_.]+")


def is_agent_skill_pack(repo) -> bool:
    """Narrow: a generic collection of skills/subagents/prompts (a 'clone'), as opposed
    to a substantive standalone tool. Targets what the cap=N policy should cut."""
    if "skill" in repo.full_name.split("/")[-1].lower():
        return True
    if {t.lower() for t in repo.topics} & _PACK_TOPICS:
        return True
    d = (repo.description or "").lower()
    return any(p in d for p in _PACK_DESC) or bool(_PACK_NUM_RE.search(d))


def _owner_has_ai_token(repo) -> bool:
    """True when the owner login has a path segment exactly equal to 'ai'
    (MiniMax-AI, langchain-ai, deepseek-ai, owner 'ai'), or a glued token of
    length >= 5 that ends in 'ai' (sqliteai, genai). A mid-word 'ai'
    (email, available) does not match. Descriptions are not scanned."""
    owner = repo.full_name.split("/")[0].lower()
    for tok in _OWNER_SEP_RE.split(owner):
        if not tok:
            continue
        if tok == "ai" or (len(tok) >= 5 and tok.endswith("ai")):
            return True
    return False


def _has_ai_marker(blob: str) -> bool:
    """Phrase markers are substring matches; single tokens use word boundaries so
    'cursor' does not fire on 'precursor'."""
    blob = blob.lower()
    for m in _AI_MARKERS:
        if " " in m or "-" in m:
            if m in blob:
                return True
        elif re.search(rf"\b{re.escape(m)}\b", blob):
            return True
    return False


def is_empty_metadata(repo) -> bool:
    return not (repo.description or "").strip() and not repo.topics


def is_ai_repo(repo, readme: str = "") -> bool:
    """Broad: any AI / agent / LLM repo (a skill pack, an AI topic, an AI marker in
    the name/description, an 'ai' token in the owner login, or a glued owner token
    of length >= 5 ending in 'ai'). Used for cap=0 and `ai_cap`. Optional `readme`
    covers the empty-metadata leak (a bare name plus a README that is clearly an
    agent/skill card)."""
    if is_agent_skill_pack(repo):
        return True
    if {t.lower() for t in repo.topics} & _AI_TOPICS:
        return True
    if _owner_has_ai_token(repo):
        return True
    blob = (repo.full_name + " " + (repo.description or "")).lower()
    if _has_ai_marker(blob):
        return True
    if readme and is_empty_metadata(repo) and _has_ai_marker(readme):
        return True
    return False


def cap_ai(repos: list, cap) -> list:
    """Shape a theme's candidate pool per its ai_cap (order preserved):
    None → unchanged; 0 → drop every is_ai_repo; N → keep all non-AI plus
    at most N AI repos (packs or standalone tools). Distinct from
    cap_agent_skills, which only limits skill *packs*."""
    if cap is None:
        return repos
    if cap == 0:
        return [r for r in repos if not is_ai_repo(r)]
    out, n_ai = [], 0
    for r in repos:
        if is_ai_repo(r):
            if n_ai >= cap:
                continue
            n_ai += 1
        out.append(r)
    return out


def cap_stars(repos: list, max_stars, exempt_days, today: date) -> list:
    """Drop repos above ``max_stars``.

    ``max_stars`` None leaves the list unchanged. A repo created within
    ``exempt_days`` is kept even above the ceiling (a young breakout can be
    large). Unknown ``created_at`` is not exempt. Order is preserved.
    """
    if not max_stars:
        return list(repos)
    kept = []
    for repo in repos:
        if repo.stars <= max_stars:
            kept.append(repo)
            continue
        if exempt_days is None:
            continue
        age = age_days(getattr(repo, "created_at", "") or "", today)
        if age is not None and age <= exempt_days:
            kept.append(repo)
    return kept


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
