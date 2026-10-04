import logging
import os
import time
from datetime import datetime, timezone, date

from bot.config import expand_since
from bot.github import (
    search_repos, fetch_repos, readme_first_line, readme_parts,
    github_rate_remaining, reset_github_rate_remaining,
)
from bot.trending import collect_trending, merge_trending
from bot.source_health import (
    THIN_POST_BELOW, report_source_health, short_reason, thin_post_line,
)
from bot.filters import (
    clean, cap_agent_skills, cap_ai, star_velocity, age_days,
    is_ai_repo, is_empty_metadata,
)
from bot.ranker import rank
from bot.formatter import build_messages
from bot.starsnap import (load_snapshot, save_snapshot, find_baseline,
                          order_by_delta, retain)
from bot.telegram import send_message
from bot.translate import translate_to_english
from bot.titles import make_titles
from bot.summaries import make_summaries
from bot.slack import send_slack_message
from bot.alerts import (
    TITLE_VIA_CURATOR, TITLE_VIA_NONE, resolve_curator, resolve_title_model,
    send_alert, llm_reachable,
)
from bot.state import load_state, save_state, unsent, record_sent, unposted, record_posted

log = logging.getLogger("bot")

# Most candidates to hand the LLM curator per theme (keeps the prompt tight/fast).
CANDIDATE_LIMIT = 30
# Cheap watch query folded into today's snapshot so Movers has mid-week memory
# of repos that weren't in that hour's theme search. Disposable; failures warn.
WATCH_QUERY = "created:>{since:120d} stars:>100"
SEARCH_PER_PAGE = 100


def _merge_github_trending(theme, repos: list, token: str):
    """Append GitHub Trending repos the search pool did not already return.

    Returns ``(repos, gains, added, hits)``. ``gains`` maps repo id to
    ``(stars_gained, period)`` for every resolved hit, including repos search
    already had. Never raises — a scrape or hydrate failure leaves the search
    pool in place.
    """
    if not theme.github_trending:
        return repos, {}, 0, 0
    try:
        hits = collect_trending(theme.github_trending)
    except Exception:
        log.warning("theme %s: github trending fetch failed; search pool only",
                    theme.key, exc_info=True)
        return repos, {}, 0, 0
    known = {r.full_name.lower() for r in repos}
    missing = [h.full_name for h in hits if h.full_name.lower() not in known]
    hydrated: list = []
    if missing:
        try:
            hydrated = fetch_repos(missing, token=token)
        except Exception:
            log.warning("theme %s: trending hydrate failed; search pool only",
                        theme.key, exc_info=True)
            hydrated = [None] * len(missing)
    merged, gains = merge_trending(repos, hits, missing, hydrated)
    return merged, gains, len(merged) - len(repos), len(hits)


def _failure_reason(exc: BaseException, config) -> str:
    return short_reason(
        exc,
        config.github_token,
        config.telegram_bot_token,
        config.ollama_api_key,
        config.slack_bot_token,
    )


def run(config, now: datetime | None = None, dry_run: bool = False) -> int:
    now = now or datetime.now(timezone.utc)   # cron hours are UTC; never local time
    reset_github_rate_remaining()
    today = now.date()
    state_path = os.path.join(config.state_dir, "state.json")
    state = load_state(state_path)
    # Movers store: every theme folds the repos it searches into today's snapshot.
    # A delta theme (theme.delta_days set) sources candidates by week-over-week growth.
    today_snap = load_snapshot(config.state_dir, today)
    baselines: dict = {}     # theme.key -> baseline {repo_id: stars} (delta themes only)
    # theme.key -> {repo_id: (gained, period)} from GitHub Trending, for repos
    # with no snapshot baseline yet. Display and velocity both read this.
    gained: dict = {}
    trending_empty: list[str] = []
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
    # Titles + translation sit outside the curator chain. A 200 with blank
    # content (Gemma 4 thinking) used to look like "base unavailable" and page
    # every cron; llm_reachable now treats HTTP 200 as live, and titles/translation
    # send think=False so content is actually filled. resolve_title_model also
    # tries the `-cloud` sibling of a leftover local-offload tag. Skip when
    # degraded (whole run is stars-only; don't re-ping a dead base).
    if not config.ollama_host or curator_model is None:
        title_model, title_via = "", TITLE_VIA_NONE
    else:
        title_model, title_via = resolve_title_model(
            config.ollama_host, config.ollama_model, curator_model,
            config.ollama_api_key, ping=llm_reachable)
        if title_model != config.ollama_model:
            log.info("titles/translation using %s (configured base %s, via %s)",
                     title_model, config.ollama_model, title_via)
    claimed: set = set()        # repo ids already taken by an earlier theme THIS run
    results: dict = {}          # theme.key -> picked repos
    # theme.key -> (status, items, error) for themes that ran this slot.
    # Skipped slots are absent so they are not recorded as empty.
    outcomes: dict = {}
    # llm rank whose scores did not parse (empty content, timeout, unparseable).
    # A quiet slot is not recorded here. Alerted after delivery, never on the digest.
    scoring_fallbacks: list[tuple[str, str]] = []

    readme_cache: dict[str, tuple[str, str]] = {}

    def describe(r):
        first, _ = readme_cache.get(r.full_name) or readme_parts(
            r.full_name, token=config.github_token)
        return first

    def translate(text):
        return translate_to_english(text, host=config.ollama_host,
                                    model=title_model or config.ollama_model,
                                    api_key=config.ollama_api_key)

    # Phase 1 — select. Catch-all themes (e.g. Trending) are processed LAST so they
    # cannot duplicate a specific theme's picks; `claimed` enforces one theme per repo.
    for theme in sorted(config.themes, key=lambda t: t.catch_all):
        if theme.at is not None and (now.weekday(), now.hour) not in theme.at:
            continue  # not scheduled for this weekday+hour slot
        try:
            queries = theme.query if isinstance(theme.query, tuple) else (theme.query,)
            repos, seen_ids = [], set()
            # page 2 only when we will shape out AI — otherwise the extra 100
            # are the same star-sorted head the curator already sees.
            pages = (1, 2) if theme.ai_cap is not None else (1,)
            for q in queries:
                for page in pages:
                    for r in search_repos(expand_since(q, today), sort=theme.sort,
                                          order=theme.order, token=config.github_token,
                                          per_page=SEARCH_PER_PAGE, page=page):
                        if r.id not in seen_ids:
                            seen_ids.add(r.id)
                            repos.append(r)
            if len(queries) > 1:
                repos.sort(key=lambda r: r.stars, reverse=True)   # merged pool, best first
            repos, theme_gains, trending_added, trending_hits = _merge_github_trending(
                theme, repos, config.github_token)
            gained[theme.key] = theme_gains
            if theme.github_trending and not theme_gains:
                trending_empty.append(theme.key)
                log.warning("theme %s: github trending added no repos", theme.key)
            repos = [r for r in repos if not r.is_fork and not r.is_archived]
            for r in repos:
                today_snap[r.id] = r.stars      # feed the Movers store (every theme, every run)
            dropped_no_baseline = 0
            baseline_days = 0
            if theme.delta_days:                # source candidates by N-day star growth
                # Baseline merges every snapshot from delta+tolerance ago through
                # yesterday (oldest count per repo). Today's file is not read.
                before_delta = len(repos)
                baseline = find_baseline(config.state_dir, today, theme.delta_days)
                # Snapshot diff when we have one; otherwise the Trending page's
                # period gain so an older repo is eligible the day we first see it.
                extras = theme_gains if theme.github_trending else None
                repos = order_by_delta(
                    repos, baseline, extras, span_days=theme.delta_days)
                dropped_no_baseline = before_delta - len(repos)
                baseline_days = getattr(baseline, "baseline_days", 0)
                baselines[theme.key] = baseline
            elif theme_gains:
                # No snapshot reorder. Put Trending hits first so the candidate
                # cap cannot bury an any-age repo under the search head.
                front = [r for r in repos if r.id in theme_gains]
                back = [r for r in repos if r.id not in theme_gains]
                repos = front + back
            n_searched = len(repos)
            repos = clean(repos, today, theme.max_idle_days)
            n_clean = len(repos)
            repos = unsent(state, theme.key, repos)
            repos = [r for r in repos if r.id not in claimed]
            if not theme.delta_days:          # Movers may re-feature a known breakout
                repos = unposted(state, repos)
            n_unsent = len(repos)
            # cap=0 only: empty desc+topics + a README that is clearly AI → drop.
            # Not applied under ai_cap=N (too aggressive on thin metadata).
            if theme.agent_skill_cap == 0 or theme.ai_cap == 0:
                kept = []
                for r in repos:
                    if is_empty_metadata(r) and not is_ai_repo(r):
                        line = readme_first_line(r.full_name, token=config.github_token)
                        if is_ai_repo(r, readme=line):
                            continue
                    kept.append(r)
                repos = kept
            repos = cap_agent_skills(repos, theme.agent_skill_cap)
            repos = cap_ai(repos, theme.ai_cap)
            repos = repos[:CANDIDATE_LIMIT]
            n_cap = len(repos)
            picked = rank(repos, theme, today=today, ollama_host=config.ollama_host,
                          ollama_model=curator_model or "", ollama_api_key=config.ollama_api_key)
            fallback_reason = getattr(picked, "fallback_reason", None)
            if fallback_reason:
                scoring_fallbacks.append((theme.key, fallback_reason))
            funnel = ("theme %s: searched=%d after_clean=%d after_unsent=%d "
                      "after_cap=%d picked=%d")
            funnel_args: list = [theme.key, n_searched, n_clean, n_unsent, n_cap, len(picked)]
            if theme.delta_days:
                funnel += " baseline_days=%d dropped_no_baseline=%d"
                funnel_args.extend((baseline_days, dropped_no_baseline))
            if theme.github_trending:
                funnel += " trending_hits=%d trending_added=%d"
                funnel_args.extend((trending_hits, trending_added))
            log.info(funnel, *funnel_args)
            # Stars fallback is not "none above the bar" — that line is the quiet slot.
            if repos and not picked and not fallback_reason:
                log.info("theme %s: %d candidates, none above the quality bar",
                         theme.key, len(repos))
            results[theme.key] = picked
            claimed.update(p.repo.id for p in picked)
            outcomes[theme.key] = ("ok" if picked else "empty", len(picked), "")
        except Exception as exc:
            failures += 1
            log.exception("theme %s failed during selection", theme.key)
            outcomes[theme.key] = ("error", 0, _failure_reason(exc, config))

    # Persist today's snapshot once after selection (never in a dry-run, which mutates
    # nothing). The store is DISPOSABLE, so a write/retain failure must NEVER take down
    # the digest — warn and proceed to delivery. (A persistent disk problem also surfaces
    # via state.json's save in Phase 2, which is already counted as a theme failure and
    # alerted; a transient hiccup here is within Movers' tolerance window, so no alert.)
    if not dry_run:
        try:
            for r in search_repos(expand_since(WATCH_QUERY, today), sort="stars",
                                  order="desc", token=config.github_token,
                                  per_page=SEARCH_PER_PAGE):
                if not r.is_fork and not r.is_archived:
                    today_snap[r.id] = r.stars
        except Exception:
            log.warning("movers watchlist search failed; snapshot proceeds without it",
                        exc_info=True)
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
                excerpts = []
                for r in repos_:
                    first, excerpt = readme_cache.get(r.full_name) or readme_parts(
                        r.full_name, token=config.github_token)
                    readme_cache[r.full_name] = (first, excerpt)
                    excerpts.append(excerpt)
                summaries = make_summaries(repos_, excerpts, whys=whys, host=config.ollama_host,
                                           model=curator_model or "", api_key=config.ollama_api_key)
            titles = make_titles(repos_, host=config.ollama_host,
                                 model=title_model or config.ollama_model,
                                 api_key=config.ollama_api_key)
            deltas = None
            delta_periods = None
            if theme.delta_days:    # annotate the meta line with '+N★ this week'
                base = baselines.get(theme.key, {})
                extra = gained.get(theme.key, {})
                deltas = []
                delta_periods = []
                for r in repos_:
                    if r.id in base:
                        deltas.append(r.stars - base[r.id])
                        delta_periods.append("weekly")
                    elif r.id in extra:
                        gain, period = extra[r.id]
                        deltas.append(gain)
                        delta_periods.append(period)
                    else:
                        deltas.append(None)
                        delta_periods.append("weekly")
            # ★/day momentum for every theme — None when creation date is unknown (so the
            # velocity would be meaningless), which the formatter renders as no badge.
            # Hide ★/day for repos younger than 2 days (same-day 88k★/day theatre).
            momenta = []
            for r in repos_:
                age = age_days(r.created_at, today)
                momenta.append(star_velocity(r, today) if age is not None and age >= 2
                               else None)
            messages = build_messages(theme, repos_, describe, translate, titles,
                                      summaries, deltas, momenta, delta_periods)
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
            # Visibility only: a 1-2 repo digest still goes out unchanged.
            if len(picked) < THIN_POST_BELOW:
                log.warning("%s", thin_post_line(theme.key, len(picked)))
            ids = [p.repo.id for p in picked]
            state = record_sent(state, theme.key, ids)
            state = record_posted(state, ids)   # movers writes too; only *reads* are exempt
            save_state(state_path, state)
            log.info("theme %s: sent %d repos", theme.key, len(picked))
        except Exception as exc:
            failures += 1
            log.exception("theme %s failed during delivery", theme.key)
            outcomes[theme.key] = (
                "error", len(picked) if picked else 0, _failure_reason(exc, config))

    if not dry_run:
        if degraded:
            # Whole chain down — this DM already says the run is stars-only.
            # rank() still logs a WARNING and sets fallback_reason; a second
            # scoring DM would double-page the same outage.
            send_alert(config.telegram_bot_token, config.alert_chat_id,
                       "⚠️ interesting-repos: Ollama unreachable/unauthorized — this run is "
                       "degraded (stars-only picks, no AI titles/blurbs/translation). "
                       "Check OLLAMA_API_KEY in Railway.")
        elif scoring_fallbacks:
            # Reachable model, but scoring returned nothing usable. Do not send
            # the "ran on {model}" heads-up — that claims curation happened.
            # A quiet slot (scores parsed, none above min_score) is not in this list.
            detail = ", ".join(f"{key} ({reason})" for key, reason in scoring_fallbacks)
            send_alert(config.telegram_bot_token, config.alert_chat_id,
                       "⚠️ interesting-repos: LLM scoring failed — "
                       f"{detail}. Stars fallback; curator scores were not usable.")
        elif curator_skipped and curator_model:
            # primary curator(s) were down but a fallback worked — the run is fully curated,
            # just on a backup model; nudge Sov to fix the config (e.g. a retired model).
            send_alert(config.telegram_bot_token, config.alert_chat_id,
                       f"⚠️ interesting-repos: curator model(s) {', '.join(curator_skipped)} "
                       f"unavailable — ran on {curator_model}. "
                       "Update OLLAMA_CURATOR_MODEL in Railway.")
        if title_via == TITLE_VIA_CURATOR:
            # base name + cloud alias both down, but curation is fine — titles/translation
            # ran on the curator rather than deterministic fallbacks. Independent of the
            # curator-skipped branch above: both can fire (a fallback curator AND a dead
            # base). A working cloud alias of OLLAMA_MODEL is via=base and stays silent.
            send_alert(config.telegram_bot_token, config.alert_chat_id,
                       f"⚠️ interesting-repos: base model {config.ollama_model} unavailable — "
                       f"titles and translation ran on {title_model} "
                       "(curation unaffected). "
                       "Update OLLAMA_MODEL in Railway.")
        if failures:
            send_alert(config.telegram_bot_token, config.alert_chat_id,
                       f"⚠️ interesting-repos: {failures} theme(s) failed this run.")
        if trending_empty:
            # The search pool still ran. This DM is the age-free source being
            # down (page markup or hydrate), which otherwise looks like a
            # normal thin Movers post.
            send_alert(config.telegram_bot_token, config.alert_chat_id,
                       "⚠️ interesting-repos: GitHub Trending added no repos for "
                       f"{', '.join(trending_empty)}; search-pool only this run.")
    # Observability only. Does not change picks, message text, or state.json.
    ordered = []
    for theme in config.themes:
        if theme.key in outcomes:
            status, items, error = outcomes[theme.key]
            ordered.append((theme.key, status, items, error))
    try:
        report_source_health(
            ordered,
            state_dir=config.state_dir,
            now=now,
            dry_run=dry_run,
            github_authenticated=bool(config.github_token),
            alert_chat_id=config.alert_chat_id,
            telegram_token=config.telegram_bot_token,
            rate_remaining=github_rate_remaining(),
            send_alert=send_alert,
        )
    except Exception:
        log.warning("source_health report failed; delivery already finished", exc_info=True)
    return failures
