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
    raise NotImplementedError
