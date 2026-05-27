import json

_MODEL = "claude-haiku-4-5"


def rank_by_stars(repos: list, count: int) -> list:
    return sorted(repos, key=lambda r: r.stars, reverse=True)[:count]


def rank(repos: list, theme, anthropic_api_key: str = "", client=None) -> list:
    if theme.rank == "llm" and anthropic_api_key:
        try:
            return _rank_llm(repos, theme, anthropic_api_key, client=client)[:theme.count]
        except Exception:
            pass  # graceful degradation: a digest still ships
    return rank_by_stars(repos, theme.count)


def _rank_llm(repos: list, theme, api_key: str, client=None):
    if client is None:
        import anthropic  # lazy: only needed for llm themes
        client = anthropic.Anthropic(api_key=api_key)

    lines = []
    for i, r in enumerate(repos):
        topics = ", ".join(r.topics)
        lines.append(f"{i}. {r.full_name} (★{r.stars}) — {r.description} [topics: {topics}]")
    listing = "\n".join(lines)

    prompt = (
        f"You are curating a list for the theme \"{theme.name}\".\n"
        f"Reader interests: {theme.profile or 'general developer interest'}.\n\n"
        f"Here are candidate repositories, each with an index:\n{listing}\n\n"
        f"Return ONLY a JSON array of the {theme.count} most relevant indices, "
        f"most relevant first. Example: [3, 0, 7]."
    )
    resp = client.messages.create(
        model=_MODEL,
        max_tokens=200,
        messages=[{"role": "user", "content": prompt}],
    )
    text = resp.content[0].text.strip()
    indices = json.loads(text)
    out = []
    for idx in indices:
        if isinstance(idx, int) and 0 <= idx < len(repos):
            out.append(repos[idx])
    return out
