from html import escape

TELEGRAM_LIMIT = 4096


def _format_delta(n: int) -> str | None:
    """A compact '+N★ this week' growth annotation, or None when there's nothing
    to show (<=0). Compact form (1.2k) mirrors the 'fastest growing repos this
    week' post format Movers is based on."""
    if n <= 0:
        return None
    if n >= 1000:
        return f"+{n / 1000:.1f}k★ this week"
    return f"+{n:,}★ this week"


def _entry(repo, title, summary, describe, translate, delta=None) -> str:
    desc = summary or translate(repo.description or describe(repo) or "")
    heading = f'<a href="{repo.html_url}"><b>{escape(title)}</b></a>'
    meta = f"⭐ {repo.stars:,}"
    growth = _format_delta(delta) if delta is not None else None
    if growth:
        meta += f" · {growth}"
    if repo.language:
        meta += f" · {escape(repo.language)}"
    meta += f" · {escape(repo.full_name)}"
    return f"{heading}\n{meta}\n{escape(desc)}".rstrip()


def build_messages(theme, repos, describe, translate=lambda s: s, titles=None,
                   summaries=None, deltas=None) -> list[str]:
    header = f"{theme.emoji} <b>{escape(theme.name)}</b>".strip()
    if titles is None:
        titles = [r.full_name for r in repos]
    if summaries is None:
        summaries = [None] * len(repos)
    if deltas is None:
        deltas = [None] * len(repos)
    messages: list[str] = []
    current = header
    for repo, title, summary, delta in zip(repos, titles, summaries, deltas):
        block = _entry(repo, title, summary, describe, translate, delta)
        candidate = f"{current}\n\n{block}"
        if len(candidate) > TELEGRAM_LIMIT:
            messages.append(current)
            current = block            # continuation message, no header
        else:
            current = candidate
    if current:
        messages.append(current)
    return messages
