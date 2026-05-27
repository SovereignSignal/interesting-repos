import json
import re

from bot.ollama import chat

_ARR_RE = re.compile(r"\[.*\]", re.S)
_ACRONYMS = {
    "ai", "cli", "api", "sdk", "ui", "ux", "css", "html", "js", "ts", "ml", "llm",
    "os", "db", "gpu", "cpu", "rag", "mcp", "tui", "p2p", "sql", "http", "io", "3d",
}


def _prettify(full_name: str) -> str:
    """Deterministic fallback title from a repo's name part (no AI)."""
    base = full_name.split("/")[-1]
    words = [w for w in re.split(r"[-_.\s]+", base) if w]
    if not words:
        return base
    return " ".join(w.upper() if w.lower() in _ACRONYMS else w[:1].upper() + w[1:]
                    for w in words)


def make_titles(repos, host: str = "", model: str = "", api_key: str = "",
                client=None) -> list[str]:
    """Clean human-readable titles for each repo (same order).

    Asks an Ollama model for nice titles; falls back to a deterministic prettified
    repo name when there's no host, the model errors, or the reply doesn't line up.
    """
    fallback = [_prettify(r.full_name) for r in repos]
    if not host or not repos:
        return fallback
    listing = "\n".join(f"{i}. {r.full_name} — {r.description}" for i, r in enumerate(repos))
    prompt = (
        "For each GitHub repository below, write a short, clean, human-readable title "
        "(2-4 words, proper capitalization, keep acronyms like CLI/AI/SDK/API uppercase, "
        "no quotes). Return ONLY a JSON array of strings, one per repo, in the same order.\n\n"
        f"{listing}"
    )
    text = chat(prompt, host=host, model=model, api_key=api_key, client=client)
    match = _ARR_RE.search(text)
    if not match:
        return fallback
    try:
        titles = json.loads(match.group(0))
    except Exception:
        return fallback
    if not isinstance(titles, list) or len(titles) != len(repos):
        return fallback
    return [str(t).strip() or fallback[i] for i, t in enumerate(titles)]
