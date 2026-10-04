from html import escape

TELEGRAM_LIMIT = 4096


_DELTA_LABEL = {"daily": "today", "weekly": "this week", "monthly": "this month"}


def _format_delta(n: int, period: str = "weekly") -> str | None:
    """A compact '+N★ this week' growth annotation, or None when there's nothing
    to show (<=0). Compact form (1.2k) mirrors the 'fastest growing repos this
    week' post format Movers is based on. ``period="daily"`` says "today"."""
    if n <= 0:
        return None
    label = _DELTA_LABEL.get(period, "this week")
    if n >= 1000:
        return f"+{n / 1000:.1f}k★ {label}"
    return f"+{n:,}★ {label}"


def _format_momentum(v) -> str | None:
    """A compact 'N★/day' velocity badge (the reader-facing momentum signal), or
    None when there's nothing meaningful to show — unknown age or under ~1★/day."""
    if v is None or v < 1:
        return None
    return f"{round(v):,}★/day"


def _entry(repo, title, summary, describe, translate, delta=None, momentum=None,
           delta_period: str = "weekly") -> str:
    desc = summary or translate(repo.description or describe(repo) or "")
    heading = f'<a href="{repo.html_url}"><b>{escape(title)}</b></a>'
    meta = f"⭐ {repo.stars:,}"
    pace = _format_momentum(momentum)
    if pace:
        meta += f" · {pace}"
    growth = _format_delta(delta, delta_period) if delta is not None else None
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
                   summaries=None, deltas=None, momenta=None,
                   delta_periods=None) -> list[str]:
    header = f"{theme.emoji} <b>{escape(theme.name)}</b>".strip()
    if titles is None:
        titles = [r.full_name for r in repos]
    if summaries is None:
        summaries = [None] * len(repos)
    if deltas is None:
        deltas = [None] * len(repos)
    if momenta is None:
        momenta = [None] * len(repos)
    if delta_periods is None:
        delta_periods = ["weekly"] * len(repos)
    messages: list[str] = []
    current = header
    for repo, title, summary, delta, momentum, delta_period in zip(
            repos, titles, summaries, deltas, momenta, delta_periods):
        block = _entry(repo, title, summary, describe, translate, delta, momentum,
                       delta_period)
        candidate = f"{current}\n\n{block}"
        if len(candidate) > TELEGRAM_LIMIT:
            messages.append(current)
            current = block            # continuation message, no header
        else:
            current = candidate
    if current:
        messages.append(current)
    return messages
