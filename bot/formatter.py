from html import escape

TELEGRAM_LIMIT = 4096


def _entry(repo, describe, translate) -> str:
    desc = translate(repo.description or describe(repo) or "")
    meta = f"⭐ {repo.stars:,}"
    if repo.language:
        meta += f" · {escape(repo.language)}"
    title = f'<a href="{repo.html_url}">{escape(repo.full_name)}</a>'
    return f"{meta}\n{title}\n{escape(desc)}".rstrip()


def build_messages(theme, repos, describe, translate=lambda s: s) -> list[str]:
    header = f"{theme.emoji} <b>{escape(theme.name)}</b>".strip()
    messages: list[str] = []
    current = header
    for repo in repos:
        block = _entry(repo, describe, translate)
        candidate = f"{current}\n\n{block}"
        if len(candidate) > TELEGRAM_LIMIT:
            messages.append(current)
            current = block            # continuation message, no header
        else:
            current = candidate
    if current:
        messages.append(current)
    return messages
