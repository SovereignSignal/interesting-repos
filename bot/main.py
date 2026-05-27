import logging
import os
from datetime import date

from bot.config import expand_since
from bot.github import search_repos, readme_first_line
from bot.ranker import rank
from bot.formatter import build_messages
from bot.telegram import send_message
from bot.state import load_state, save_state, unsent, record_sent

log = logging.getLogger("bot")


def run(config, today: date | None = None, dry_run: bool = False) -> int:
    today = today or date.today()
    state_path = os.path.join(config.state_dir, "state.json")
    state = load_state(state_path)
    failures = 0

    for theme in config.themes:
        try:
            query = expand_since(theme.query, today)
            repos = search_repos(query, sort=theme.sort, order=theme.order,
                                 token=config.github_token)
            repos = [r for r in repos if not r.is_fork and not r.is_archived]
            repos = unsent(state, theme.key, repos)
            picked = rank(repos, theme, anthropic_api_key=config.anthropic_api_key)
            if not picked:
                log.info("theme %s: no new repos", theme.key)
                continue

            def describe(r):
                return readme_first_line(r.full_name, token=config.github_token)

            messages = build_messages(theme, picked, describe)

            if dry_run:
                for m in messages:
                    print(m)
                    print("-" * 40)
                continue

            for m in messages:
                send_message(config.telegram_bot_token, config.telegram_chat_id, m)
            record_sent(state, theme.key, [r.id for r in picked])
            save_state(state_path, state)
            log.info("theme %s: sent %d repos", theme.key, len(picked))
        except Exception:
            failures += 1
            log.exception("theme %s failed", theme.key)

    return failures
