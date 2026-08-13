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


def _format_momentum(v) -> str | None:
    """A compact 'N★/day' velocity badge (the reader-facing momentum signal), or
    None when there's nothing meaningful to show — unknown age or under ~1★/day."""
    if v is None or v < 1:
        return None
    return f"{round(v):,}★/day"


def _entry(repo, title, summary, describe, translate, delta=None, momentum=None) -> str:
    desc = summary or translate(repo.description or describe(repo) or "")
    heading = f'<a href="{repo.html_url}"><b>{escape(title)}</b></a>'
    meta = f"⭐ {repo.stars:,}"
    pace = _format_momentum(momentum)
    if pace:
        meta += f" · {pace}"
    growth = _format_delta(delta) if delta is not None else None
    if growth:
        meta += f" · {growth}"
    license_ = getattr(repo, "license", "") or ""
    if license_:
        meta += f" · {escape(license_)}"
    if repo.language:
        meta += f" · {escape(repo.language)}"
    meta += f" · {escape(repo.full_name)}"
    return f"{heading}\n{meta}\n{escape(desc)}".rstrip()


def build_messages(theme, repos, describe, translate=lambda s: s, titles=None,
                   summaries=None, deltas=None, momenta=None) -> list[str]:
    header = f"{theme.emoji} <b>{escape(theme.name)}</b>".strip()
    if titles is None:
        titles = [r.full_name for r in repos]
    if summaries is None:
        summaries = [None] * len(repos)
    if deltas is None:
        deltas = [None] * len(repos)
    if momenta is None:
        momenta = [None] * len(repos)
    messages: list[str] = []
    current = header
    for repo, title, summary, delta, momentum in zip(repos, titles, summaries, deltas, momenta):
        block = _entry(repo, title, summary, describe, translate, delta, momentum)
        candidate = f"{current}\n\n{block}"
        if len(candidate) > TELEGRAM_LIMIT:
            messages.append(current)
            current = block            # continuation message, no header
        else:
            current = candidate
    if current:
        messages.append(current)
    return messages
