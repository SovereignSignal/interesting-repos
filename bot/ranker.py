import json
import re

from bot.ollama import chat

_INDICES_RE = re.compile(r"\[[\d,\s]*\]")


def rank_by_stars(repos: list, count: int) -> list:
    return sorted(repos, key=lambda r: r.stars, reverse=True)[:count]


def rank(repos: list, theme, ollama_host: str = "", ollama_model: str = "",
         ollama_api_key: str = "", client=None) -> list:
    """Pick the top repos for a theme.

    rank="llm": ask an Ollama model to curate (filtering spam/star-farms) against
    the theme's profile. Falls back to top-by-stars if LLM is unavailable, errors,
    or returns nothing — so a digest always ships.
    """
    if theme.rank == "llm" and ollama_host:
        try:
            picked = _rank_llm(repos, theme, ollama_host, ollama_model, ollama_api_key, client=client)
            if picked:
                return picked[:theme.count]
        except Exception:
            pass  # graceful degradation
    return rank_by_stars(repos, theme.count)


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


def _rank_llm(repos: list, theme, host: str, model: str, api_key: str, client=None) -> list:
    lines = []
    for i, r in enumerate(repos):
        topics = ", ".join(r.topics)
        lines.append(f"{i}. {r.full_name} (★{r.stars}) — {r.description} [topics: {topics}]")
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
        "Prefer a DIVERSE set — do not fill the list with many near-identical projects "
        "(e.g. interchangeable 'AI coding agent skill' packs); pick the most distinctive.\n\n"
        f"Candidates (index. owner/name (stars) — description [topics]):\n{listing}\n\n"
        f"Choose the {theme.count} best. Return ONLY a JSON array of their indices, "
        "most interesting first. Example: [3, 0, 7]."
    )
    text = chat(prompt, host=host, model=model, api_key=api_key, client=client)
    out = []
    for idx in _parse_indices(text):
        if isinstance(idx, int) and 0 <= idx < len(repos):
            out.append(repos[idx])
    return out
