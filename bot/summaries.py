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
