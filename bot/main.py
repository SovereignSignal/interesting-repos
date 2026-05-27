import logging
import os
from datetime import date

from bot.config import expand_since
from bot.github import search_repos, readme_first_line
from bot.ranker import rank
from bot.formatter import build_messages
from bot.telegram import send_message
from bot.translate import translate_to_english
from bot.state import load_state, save_state, unsent, record_sent

log = logging.getLogger("bot")


def run(config, today: date | None = None, dry_run: bool = False) -> int:
    today = today or date.today()
    state_path = os.path.join(config.state_dir, "state.json")
    state = load_state(state_path)
    failures = 0

    def describe(r):
        return readme_first_line(r.full_name, token=config.github_token)

    def translate(text):
        return translate_to_english(text, api_key=config.anthropic_api_key)

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

            messages = build_messages(theme, picked, describe, translate)

            if dry_run:
                for m in messages:
                    print(m)
                    print("-" * 40)
                continue

            # State is recorded only after every message for the theme is sent, so a
            # crash never marks a repo "sent" that wasn't delivered. The deliberate
            # tradeoff (see spec): we prefer re-sending over losing a repo. If a theme
            # splits into multiple messages and a later one fails, the earlier ones may
            # be re-sent next run. The starter themes fit in one message, so this can't
            # trigger today; revisit (record per-message) only if a theme grows long.
            for m in messages:
                send_message(config.telegram_bot_token, config.telegram_chat_id, m)
            state = record_sent(state, theme.key, [r.id for r in picked])
            save_state(state_path, state)
            log.info("theme %s: sent %d repos", theme.key, len(picked))
        except Exception:
            failures += 1
            log.exception("theme %s failed", theme.key)

    return failures
