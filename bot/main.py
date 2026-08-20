import logging
import os
import time
from datetime import datetime, timezone, date

from bot.config import expand_since
from bot.github import search_repos, readme_first_line, readme_excerpt
from bot.filters import clean, cap_agent_skills, star_velocity, age_days
from bot.ranker import rank
from bot.formatter import build_messages
from bot.starsnap import (load_snapshot, save_snapshot, find_baseline,
                          order_by_delta, retain)
from bot.telegram import send_message
from bot.translate import translate_to_english
from bot.titles import make_titles
from bot.summaries import make_summaries
from bot.slack import send_slack_message
from bot.alerts import resolve_curator, send_alert, llm_reachable
from bot.state import load_state, save_state, unsent, record_sent

log = logging.getLogger("bot")

# Most candidates to hand the LLM curator per theme (keeps the prompt tight/fast).
CANDIDATE_LIMIT = 30


def run(config, now: datetime | None = None, dry_run: bool = False) -> int:
    now = now or datetime.now(timezone.utc)   # cron hours are UTC; never local time
    today = now.date()
    state_path = os.path.join(config.state_dir, "state.json")
    state = load_state(state_path)
    # Movers store: every theme folds the repos it searches into today's snapshot.
    # A delta theme (theme.delta_days set) sources candidates by week-over-week growth.
    today_snap = load_snapshot(config.state_dir, today)
    baselines: dict = {}     # theme.key -> baseline {repo_id: stars} (delta themes only)
    failures = 0
    # pre-flight: resolve the curator by walking OLLAMA_CURATOR_MODEL's candidates (first
    # reachable wins), falling back to the base model as the final rung. A retired/401
    # primary (the 2026-06-06 and -06-16 incidents) self-heals to a working model instead
    # of degrading the whole run; only an all-down chain (curator_model is None, which
    # implies the base is down too) is a genuinely stars-only, degraded run.
    curator_model, curator_skipped = (
        resolve_curator(config.ollama_host, config.ollama_curator_models,
                        config.ollama_model, config.ollama_api_key)
        if config.ollama_host else (None, []))
    degraded = not dry_run and bool(config.ollama_host) and curator_model is None
    # The base model (OLLAMA_MODEL) drives titles + translation DIRECTLY, outside the curator
    # chain. resolve_curator only pings it as the chain's last rung, so a curator that resolves
    # on an earlier rung leaves the base UNVERIFIED — a retired base (the 2026-07-15 gemma3:12b
    # retirement) then silently degrades titles/translation to deterministic fallbacks with no
    # alert. Ping it independently when the chosen curator isn't already the base. Skip when
    # degraded (whole run is stars-only anyway) or when curator IS the base (already reachable).
    base_model_down = (
        not dry_run and bool(config.ollama_host) and not degraded
        and curator_model != config.ollama_model
        and not llm_reachable(config.ollama_host, config.ollama_model, config.ollama_api_key))
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
        if theme.at is not None and (now.weekday(), now.hour) not in theme.at:
            continue  # not scheduled for this weekday+hour slot
        try:
            queries = theme.query if isinstance(theme.query, tuple) else (theme.query,)
            repos, seen_ids = [], set()
            for q in queries:
                for r in search_repos(expand_since(q, today), sort=theme.sort,
                                      order=theme.order, token=config.github_token):
                    if r.id not in seen_ids:
                        seen_ids.add(r.id)
                        repos.append(r)
            if len(queries) > 1:
                repos.sort(key=lambda r: r.stars, reverse=True)   # merged pool, best first
            repos = [r for r in repos if not r.is_fork and not r.is_archived]
            for r in repos:
                today_snap[r.id] = r.stars      # feed the Movers store (every theme, every run)
            if theme.delta_days:                # source candidates by N-day star growth
                baseline = find_baseline(config.state_dir, today, theme.delta_days)
                repos = order_by_delta(repos, baseline)   # drops repos with no prior snapshot
                baselines[theme.key] = baseline
            n_searched = len(repos)
            repos = clean(repos, today, theme.max_idle_days)
            n_clean = len(repos)
            repos = unsent(state, theme.key, repos)
            repos = [r for r in repos if r.id not in claimed]
            n_unsent = len(repos)
            repos = cap_agent_skills(repos, theme.agent_skill_cap)
            repos = repos[:CANDIDATE_LIMIT]
            n_cap = len(repos)
            picked = rank(repos, theme, today=today, ollama_host=config.ollama_host,
                          ollama_model=curator_model or "", ollama_api_key=config.ollama_api_key)
            log.info("theme %s: searched=%d after_clean=%d after_unsent=%d after_cap=%d picked=%d",
                     theme.key, n_searched, n_clean, n_unsent, n_cap, len(picked))
            if repos and not picked:
                log.info("theme %s: %d candidates, none above the quality bar",
                         theme.key, len(repos))
            results[theme.key] = picked
            claimed.update(p.repo.id for p in picked)
        except Exception:
            failures += 1
            log.exception("theme %s failed during selection", theme.key)

    # Persist today's snapshot once after selection (never in a dry-run, which mutates
    # nothing). The store is DISPOSABLE, so a write/retain failure must NEVER take down
    # the digest — warn and proceed to delivery. (A persistent disk problem also surfaces
    # via state.json's save in Phase 2, which is already counted as a theme failure and
    # alerted; a transient hiccup here is within Movers' tolerance window, so no alert.)
    if not dry_run:
        try:
            save_snapshot(config.state_dir, today, today_snap)
            retain(config.state_dir, today)
        except Exception:
            log.warning("snapshot store write/retain failed; delivery proceeds",
                        exc_info=True)

    # Phase 2 — deliver in themes.toml (display) order. State is recorded only after a
    # theme's messages are all sent (a crash never marks a repo "sent" that wasn't
    # delivered); we prefer re-sending over losing a repo. Messages are spaced by
    # config.send_delay_seconds so a 10-theme digest trickles instead of flooding.
    sent_any = False
    for theme in config.themes:
        picked = results.get(theme.key)
        if not picked:
            log.info("theme %s: no new repos", theme.key)
            continue
        try:
            repos_ = [p.repo for p in picked]
            whys = [p.why for p in picked]
            summaries = None
            if config.ollama_host:
                excerpts = [readme_excerpt(r.full_name, token=config.github_token) for r in repos_]
                summaries = make_summaries(repos_, excerpts, whys=whys, host=config.ollama_host,
                                           model=curator_model or "", api_key=config.ollama_api_key)
            titles = make_titles(repos_, host=config.ollama_host,
                                 model=config.ollama_model, api_key=config.ollama_api_key)
            deltas = None
            if theme.delta_days:    # annotate the meta line with '+N★ this week'
                base = baselines.get(theme.key, {})
                deltas = [r.stars - base.get(r.id, r.stars) for r in repos_]
            # ★/day momentum for every theme — None when creation date is unknown (so the
            # velocity would be meaningless), which the formatter renders as no badge.
            momenta = [star_velocity(r, today) if age_days(r.created_at, today) is not None
                       else None for r in repos_]
            messages = build_messages(theme, repos_, describe, translate, titles,
                                      summaries, deltas, momenta)
            if dry_run:
                for m in messages:
                    print(m)
                    print("-" * 40)
                continue
            for m in messages:
                if sent_any:
                    time.sleep(config.send_delay_seconds)
                send_message(config.telegram_bot_token, config.telegram_chat_id, m)
                mirrored = send_slack_message(config.slack_bot_token, config.slack_channel_id, m)
                if config.slack_bot_token and config.slack_channel_id and not mirrored:
                    # the mirror never raises, so a broken token/channel is otherwise invisible
                    log.warning("theme %s: slack mirror failed (telegram delivered)", theme.key)
                sent_any = True
            state = record_sent(state, theme.key, [p.repo.id for p in picked])
            save_state(state_path, state)
            log.info("theme %s: sent %d repos", theme.key, len(picked))
        except Exception:
            failures += 1
            log.exception("theme %s failed during delivery", theme.key)

    if not dry_run:
        if degraded:
            send_alert(config.telegram_bot_token, config.alert_chat_id,
                       "⚠️ interesting-repos: Ollama unreachable/unauthorized — this run is "
                       "degraded (stars-only picks, no AI titles/blurbs/translation). "
                       "Check OLLAMA_API_KEY in Railway.")
        elif curator_skipped and curator_model:
            # primary curator(s) were down but a fallback worked — the run is fully curated,
            # just on a backup model; nudge Sov to fix the config (e.g. a retired model).
            send_alert(config.telegram_bot_token, config.alert_chat_id,
                       f"⚠️ interesting-repos: curator model(s) {', '.join(curator_skipped)} "
                       f"unavailable — ran on {curator_model}. "
                       "Update OLLAMA_CURATOR_MODEL in Railway.")
        if base_model_down:
            # the base model (titles + translation) is down but curation is fine — the run is
            # NOT degraded, just shipping deterministic titles and untranslated text. Independent
            # of the curator branch above: both can fire (a fallback curator AND a dead base).
            send_alert(config.telegram_bot_token, config.alert_chat_id,
                       f"⚠️ interesting-repos: base model {config.ollama_model} unavailable — "
                       "titles and translation fell back to deterministic output "
                       f"(curation unaffected on {curator_model}). "
                       "Update OLLAMA_MODEL in Railway.")
        if failures:
            send_alert(config.telegram_bot_token, config.alert_chat_id,
                       f"⚠️ interesting-repos: {failures} theme(s) failed this run.")
    return failures
