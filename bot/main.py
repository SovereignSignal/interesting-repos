import logging
import os
from datetime import date

from bot.config import expand_since
from bot.github import search_repos, readme_first_line
from bot.filters import clean, cap_agent_skills
from bot.ranker import rank
from bot.formatter import build_messages
from bot.telegram import send_message
from bot.translate import translate_to_english
from bot.titles import make_titles
from bot.state import load_state, save_state, unsent, record_sent

log = logging.getLogger("bot")

# Most candidates to hand the LLM curator per theme (keeps the prompt tight/fast).
CANDIDATE_LIMIT = 30


def run(config, today: date | None = None, dry_run: bool = False) -> int:
    today = today or date.today()
    state_path = os.path.join(config.state_dir, "state.json")
    state = load_state(state_path)
    failures = 0
    claimed: set = set()        # repo ids already taken by an earlier theme THIS run
    results: dict = {}          # theme.key -> picked repos

    def describe(r):
        return readme_first_line(r.full_name, token=config.github_token)

    def translate(text):
        return translate_to_english(text, host=config.ollama_host,
                                    model=config.ollama_model, api_key=config.ollama_api_key)

    # Phase 1 — select. Catch-all themes (e.g. Trending) are processed LAST so they
    # cannot duplicate a specific theme's picks; `claimed` enforces one theme per repo.
    for theme in sorted(config.themes, key=lambda t: t.catch_all):
        try:
            query = expand_since(theme.query, today)
            repos = search_repos(query, sort=theme.sort, order=theme.order,
                                 token=config.github_token)
            repos = [r for r in repos if not r.is_fork and not r.is_archived]
            repos = clean(repos, today, theme.max_idle_days)
            repos = unsent(state, theme.key, repos)
            repos = [r for r in repos if r.id not in claimed]
            repos = cap_agent_skills(repos, theme.agent_skill_cap)
            repos = repos[:CANDIDATE_LIMIT]
            picked = rank(repos, theme, today=today, ollama_host=config.ollama_host,
                          ollama_model=config.ollama_model, ollama_api_key=config.ollama_api_key)
            results[theme.key] = picked
            claimed.update(r.id for r in picked)
        except Exception:
            failures += 1
            log.exception("theme %s failed during selection", theme.key)

    # Phase 2 — deliver in themes.toml (display) order. State is recorded only after a
    # theme's messages are all sent (a crash never marks a repo "sent" that wasn't
    # delivered); we prefer re-sending over losing a repo.
    for theme in config.themes:
        picked = results.get(theme.key)
        if not picked:
            log.info("theme %s: no new repos", theme.key)
            continue
        try:
            titles = make_titles(picked, host=config.ollama_host,
                                 model=config.ollama_model, api_key=config.ollama_api_key)
            messages = build_messages(theme, picked, describe, translate, titles)
            if dry_run:
                for m in messages:
                    print(m)
                    print("-" * 40)
                continue
            for m in messages:
                send_message(config.telegram_bot_token, config.telegram_chat_id, m)
            state = record_sent(state, theme.key, [r.id for r in picked])
            save_state(state_path, state)
            log.info("theme %s: sent %d repos", theme.key, len(picked))
        except Exception:
            failures += 1
            log.exception("theme %s failed during delivery", theme.key)

    return failures
