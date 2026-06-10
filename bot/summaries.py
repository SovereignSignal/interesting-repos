import json
import re

from bot.ollama import chat

_ARR_RE = re.compile(r"\[.*\]", re.S)


def make_summaries(repos, excerpts=None, whys=None, host: str = "", model: str = "",
                   api_key: str = "", client=None) -> list:
    """One concise, factual English blurb per repo (same order): what it is, then why
    it's notable — written by an Ollama model from each repo's description, README
    excerpt, and the curator's why-line. Returns None for a repo when unavailable
    (callers fall back to the repo's own description). No host / chat error / bad
    JSON / length mismatch => all None, so the no-LLM digest is unchanged."""
    n = len(repos)
    if not host or not repos:
        return [None] * n
    excerpts = excerpts or [""] * n
    whys = whys or [""] * n
    listing = "\n".join(
        f"{i}. {r.full_name} — {r.description}  [README: {ex}] [curator: {why}]"
        for i, (r, ex, why) in enumerate(zip(repos, excerpts, whys))
    )
    prompt = (
        "For each GitHub repository below, write ONE or TWO concise, factual sentences "
        "(at most 40 words total) in plain English: first what it is and does, then why "
        "it is notable — what's novel, who it's for, or its momentum (the curator note "
        "may help). No marketing language, no emoji, no hype. Use the description and "
        "README excerpt. Return ONLY a JSON array of strings, one per repo, in the "
        "same order.\n\n"
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
