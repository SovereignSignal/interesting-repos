from html import escape

TELEGRAM_LIMIT = 4096


def _entry(repo, title, summary, describe, translate) -> str:
    desc = summary or translate(repo.description or describe(repo) or "")
    heading = f'<a href="{repo.html_url}"><b>{escape(title)}</b></a>'
    meta = f"⭐ {repo.stars:,}"
    if repo.language:
        meta += f" · {escape(repo.language)}"
    meta += f" · {escape(repo.full_name)}"
    return f"{heading}\n{meta}\n{escape(desc)}".rstrip()


def build_messages(theme, repos, describe, translate=lambda s: s, titles=None,
                   summaries=None) -> list[str]:
    header = f"{theme.emoji} <b>{escape(theme.name)}</b>".strip()
    if titles is None:
        titles = [r.full_name for r in repos]
    if summaries is None:
        summaries = [None] * len(repos)
    messages: list[str] = []
    current = header
    for repo, title, summary in zip(repos, titles, summaries):
        block = _entry(repo, title, summary, describe, translate)
        candidate = f"{current}\n\n{block}"
        if len(candidate) > TELEGRAM_LIMIT:
            messages.append(current)
            current = block            # continuation message, no header
        else:
            current = candidate
    if current:
        messages.append(current)
    return messages
