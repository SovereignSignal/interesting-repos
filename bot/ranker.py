import json
import re
from datetime import date

from bot.ollama import chat
from bot.filters import age_days, star_velocity, VELOCITY_CEILING

_INDICES_RE = re.compile(r"\[[\d,\s]*\]")


def rank_by_stars(repos: list, count: int, today: date | None = None) -> list:
    """Top by stars, but implausible-velocity outliers are pushed to the back so a
    degraded run never *leads* with a star-farm. They still appear if slots remain."""
    today = today or date.today()
    normal, outliers = [], []
    for r in repos:
        (outliers if star_velocity(r, today) > VELOCITY_CEILING else normal).append(r)
    ordered = (sorted(normal, key=lambda r: r.stars, reverse=True)
               + sorted(outliers, key=lambda r: r.stars, reverse=True))
    return ordered[:count]


def rank(repos: list, theme, today: date | None = None, ollama_host: str = "",
         ollama_model: str = "", ollama_api_key: str = "", client=None) -> list:
    """Pick the top repos for a theme.

    rank="llm": ask an Ollama model to curate (recency-aware) against the theme's
    profile. Falls back to top-by-stars if the LLM is unavailable, errors, or returns
    nothing — so a digest always ships.
    """
    today = today or date.today()
    if theme.rank == "llm" and ollama_host:
        try:
            picked = _rank_llm(repos, theme, ollama_host, ollama_model,
                               ollama_api_key, today=today, client=client)
            if picked:
                return picked[:theme.count]
        except Exception:
            pass  # graceful degradation
    return rank_by_stars(repos, theme.count, today)


def _parse_indices(text: str) -> list:
    """Extract a JSON array of ints from the model reply, tolerating code fences
    and surrounding prose."""
    match = _INDICES_RE.search(text)
    if not match:
        return []
    try:
        return json.loads(match.group(0))
    except Exception:
        return []


def _describe_age(repo, today: date) -> str:
    created = age_days(repo.created_at, today)
    pushed = age_days(repo.pushed_at, today)
    created_s = f"{created}d old" if created is not None else "age unknown"
    pushed_s = f"pushed {pushed}d ago" if pushed is not None else "push unknown"
    return f"{created_s}, {pushed_s}, {round(star_velocity(repo, today))}★/day"


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


def _rank_llm(repos: list, theme, host: str, model: str, api_key: str,
              today: date | None = None, client=None) -> list:
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
        f'You are curating the list "{theme.name}" for a developer audience.\n'
        f"Selection criteria: {criteria}.\n"
        "Exclude: spam, keyword-stuffed or scammy repos; joke or low-effort repos; "
        "'awesome-*' link lists and curated-list repos (prefer real tools, libraries, "
        "and apps); and repos whose star count looks artificially inflated or that lean "
        "on hype (e.g. 'fastest repo to 100K stars', 'enjoy the party').\n"
        "Prefer repos that are fresh and actively maintained (recently pushed). Discount "
        "stale repos (not pushed in weeks) and repos whose stars grew implausibly fast "
        "(very high ★/day), which are often star-farmed.\n"
        f"{_diversity_directive(theme.agent_skill_cap)}"
        f"\nCandidates (index. owner/name (stars, age, velocity) — description [topics]):\n{listing}\n\n"
        f"Choose the {theme.count} best. Return ONLY a JSON array of their indices, "
        "most interesting first. Example: [3, 0, 7]."
    )
    text = chat(prompt, host=host, model=model, api_key=api_key, client=client)
    out = []
    for idx in _parse_indices(text):
        if isinstance(idx, int) and 0 <= idx < len(repos):
            out.append(repos[idx])
    return out
