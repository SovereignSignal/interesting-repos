import logging
import os
import time
from datetime import datetime, timezone, date

from bot.config import apply_star_ceiling, expand_since
from bot.github import (
    search_repos, fetch_repos, readme_first_line, readme_parts,
    github_rate_remaining, reset_github_rate_remaining,
)
from bot.trending import collect_trending, merge_trending, prefer_gain
from bot.gitnova import collect_gitnova, is_likely_inflated, public_summary
from bot.candidates import (
    trending_for_theme, gitnova_for_theme, repo_from_trending,
    looks_ai, blocked_by_ceiling, with_description, query_languages,
)
from bot.source_health import (
    THIN_POST_BELOW, report_source_health, short_reason, thin_post_line,
)
from bot.filters import (
    clean, cap_agent_skills, cap_ai, cap_stars, star_velocity, age_days,
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
from bot.state import (
    load_state, save_state, unsent, record_sent, unposted, record_posted, posted_ids,
)

log = logging.getLogger("bot")

# Most candidates to hand the LLM curator per theme (keeps the prompt tight/fast).
CANDIDATE_LIMIT = 30
# Core-API hydrates per theme for repos the page/GitNova did not already fully
# describe. Search hits are already complete. Tests leave this off so a Theme()
# fixture stays on search (+ an explicit github_trending window).
EXTRA_SOURCES = True
HYDRATE_LIMIT = 8
# Cheap watch query folded into today's snapshot so Movers has mid-week memory
# of repos that weren't in that hour's theme search. Disposable; failures warn.
WATCH_QUERY = "created:>{since:120d} stars:>100"
SEARCH_PER_PAGE = 100


def _scheduled(themes, now: datetime) -> list:
    chosen = []
    for theme in themes:
        if theme.at is not None and (now.weekday(), now.hour) not in theme.at:
            continue
        chosen.append(theme)
    return chosen


def _load_external(themes, now: datetime, token_secrets):
    """Scrape Trending and GitNova once per run. Never raises.

    Returns ``(trending_hits, gitnova_hits, outcomes)``. ``outcomes`` is a
    list of source-health tuples for sources this run actually tried.
    ``token_secrets`` is only used to scrub a failure string.
    """
    scheduled = _scheduled(themes, now)
    periods: list[str] = []
    languages: list[str] = []
    for theme in scheduled:
        windows = theme.github_trending or (("daily", "weekly") if EXTRA_SOURCES else ())
        for period in windows:
            if period not in periods:
                periods.append(period)
        if EXTRA_SOURCES or theme.github_trending:
            for language in query_languages(theme):
                if language.lower() not in {item.lower() for item in languages}:
                    languages.append(language)
    # Language pages are the extra-source path. Movers' own windows stay global.
    if not EXTRA_SOURCES:
        languages = []

    trending_hits: list = []
    gitnova_hits: list = []
    outcomes: list[tuple] = []
    if periods:
        try:
            kwargs = {"languages": tuple(languages)} if languages else {}
            trending_hits = collect_trending(tuple(periods), **kwargs)
            status = "ok" if trending_hits else "empty"
            outcomes.append(("github_trending", status, len(trending_hits), ""))
            if not trending_hits:
                log.warning("github trending parsed no repos; search pool only")
        except Exception as exc:
            log.warning("github trending fetch failed; search pool only", exc_info=True)
            trending_hits = []
            outcomes.append(("github_trending", "error", 0, _failure_reason(exc, token_secrets)))
    if EXTRA_SOURCES and scheduled:
        try:
            gitnova_hits, status, error = collect_gitnova()
        except Exception as exc:
            log.warning("gitnova fetch failed; search pool only", exc_info=True)
            gitnova_hits, status, error = [], "error", _failure_reason(exc, token_secrets)
        outcomes.append(("gitnova", status, len(gitnova_hits), error if status == "error" else ""))
        if status != "ok":
            log.warning("gitnova %s; search pool only (%s)", status, error or "no rows")
    return trending_hits, gitnova_hits, outcomes


def _push_name(ordered: list, seen: set, name: str) -> None:
    key = name.lower()
    if key in seen:
        return
    seen.add(key)
    ordered.append(name)


def _merge_external(theme, repos: list, trending_hits: list, gitnova_hits: list,
                    inflated: set, blocked_ids: set, token: str):
    """Fold Trending and GitNova into ``repos``.

    Returns ``(repos, gains, trending_added, trending_considered, gitnova_added,
    inflated_dropped)``. Page rows that already carry a GitHub id are usable
    without an API call. ``HYDRATE_LIMIT`` core GETs cover, in order: over-ceiling
    stubs (the young-repo exemption needs ``created_at``), id-less names (state
    dedupe needs the numeric id), then the highest page-gain stubs. A hydrate
    failure keeps the page stub. Never raises.
    """
    dropped = 0
    kept = []
    for repo in repos:
        if repo.full_name.lower() in inflated:
            dropped += 1
            continue
        kept.append(repo)
    repos = kept
    search_names = {repo.full_name.lower() for repo in repos}

    thits = [
        hit for hit in trending_for_theme(trending_hits, theme, extra=EXTRA_SOURCES)
        if hit.full_name.lower() not in inflated
    ]
    ghits = [
        hit for hit in gitnova_for_theme(gitnova_hits, theme)
        if hit.full_name.lower() not in inflated
        and not blocked_by_ceiling(hit.stars, hit.age_days, theme)
    ]
    if theme.ai_cap == 0 or theme.agent_skill_cap == 0:
        thits = [
            hit for hit in thits
            if not looks_ai(hit.full_name, hit.description, hit.language)
        ]
        ghits = [
            hit for hit in ghits
            if not looks_ai(hit.full_name, hit.summary, hit.language, hit.topics)
        ]

    known = set(search_names)
    ids = {repo.id for repo in repos}
    stubs = []
    for hit in thits:
        key = hit.full_name.lower()
        if key in known or not hit.repo_id or hit.repo_id in ids or hit.repo_id in blocked_ids:
            continue
        stub = repo_from_trending(hit)
        if stub is None or stub.is_fork:
            continue
        stubs.append(stub)
        known.add(key)
        ids.add(stub.id)
    repos = list(repos) + stubs

    trending_idless = []
    for hit in thits:
        key = hit.full_name.lower()
        if key in known or hit.repo_id:
            continue
        trending_idless.append(hit.full_name)
        known.add(key)
    gitnova_idless = []
    for hit in ghits:
        key = hit.full_name.lower()
        if key in known:
            continue
        gitnova_idless.append(hit.full_name)
        known.add(key)

    over = [
        stub for stub in stubs
        if theme.max_stars and stub.stars > theme.max_stars and not stub.created_at
    ]
    over_names = {stub.full_name.lower() for stub in over}
    others = [stub for stub in stubs if stub.full_name.lower() not in over_names and not stub.created_at]
    gain_of = {hit.full_name.lower(): hit.gained for hit in thits}
    others.sort(key=lambda stub: gain_of.get(stub.full_name.lower(), 0), reverse=True)

    ordered: list[str] = []
    seen_names: set[str] = set()
    for stub in over:
        _push_name(ordered, seen_names, stub.full_name)
    for name in trending_idless:
        _push_name(ordered, seen_names, name)
    for name in gitnova_idless:
        _push_name(ordered, seen_names, name)
    for stub in others:
        _push_name(ordered, seen_names, stub.full_name)
    if len(ordered) > HYDRATE_LIMIT:
        log.info("theme %s: enriched %d of %d external repos (core API budget)",
                 theme.key, HYDRATE_LIMIT, len(ordered))
        ordered = ordered[:HYDRATE_LIMIT]

    fetched: dict = {}
    if ordered:
        try:
            hydrated = fetch_repos(ordered, token=token)
        except Exception:
            log.warning("theme %s: external hydrate failed; keeping page data",
                        theme.key, exc_info=True)
            hydrated = [None] * len(ordered)
        for name, repo in zip(ordered, hydrated):
            if repo is not None:
                fetched[name.lower()] = repo

    missing = []
    aligned = []
    for name in trending_idless:
        key = name.lower()
        if key not in {item.lower() for item in ordered}:
            continue
        missing.append(name)
        aligned.append(fetched.get(key))
    merged, gains = merge_trending(repos, thits, missing, aligned)

    replaced = []
    for repo in merged:
        api = fetched.get(repo.full_name.lower())
        if api is not None and (api.is_fork or api.is_archived):
            gains.pop(repo.id, None)
            if api.id != repo.id:
                gains.pop(api.id, None)
            continue
        if api is None:
            replaced.append(repo)
            continue
        if repo.id in gains and api.id != repo.id:
            gains[api.id] = gains.pop(repo.id)
        replaced.append(api)
    merged = replaced

    for hit in ghits:
        key = hit.full_name.lower()
        existing = next((repo for repo in merged if repo.full_name.lower() == key), None)
        api = fetched.get(key)
        if existing is not None:
            if hit.stars_today:
                gains[existing.id] = prefer_gain(
                    gains.get(existing.id), hit.stars_today, "daily")
            summary = public_summary(hit.summary)
            if summary and not existing.description:
                merged = [
                    with_description(repo, summary) if repo.full_name.lower() == key else repo
                    for repo in merged
                ]
            continue
        if api is None or api.is_fork or api.is_archived or api.id in blocked_ids:
            continue
        api = with_description(api, public_summary(hit.summary))
        if any(repo.id == api.id for repo in merged):
            if hit.stars_today:
                gains[api.id] = prefer_gain(gains.get(api.id), hit.stars_today, "daily")
            continue
        if hit.stars_today:
            gains[api.id] = prefer_gain(gains.get(api.id), hit.stars_today, "daily")
        merged.append(api)

    trend_names = {hit.full_name.lower() for hit in thits}
    trending_added = sum(
        1 for repo in merged
        if repo.full_name.lower() in trend_names and repo.full_name.lower() not in search_names
    )
    gitnova_names = {hit.full_name.lower() for hit in ghits}
    gitnova_added = sum(
        1 for repo in merged
        if repo.full_name.lower() in gitnova_names and repo.full_name.lower() not in search_names
        and repo.full_name.lower() not in trend_names
    )
    return merged, gains, trending_added, len(thits), gitnova_added, dropped


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
    # Translation sits outside the curator chain. Titles are the repo name
    # (or a qualifying README H1) and do not call a model. A 200 with blank
    # content (Gemma 4 thinking) used to look like "base unavailable" and page
    # every cron; llm_reachable now treats HTTP 200 as live, and translation
    # sends think=False so content is actually filled. resolve_title_model
    # (historical name) also tries the `-cloud` sibling of a leftover
    # local-offload tag. Skip when degraded (whole run is stars-only; don't
    # re-ping a dead base).
    if not config.ollama_host or curator_model is None:
        title_model, title_via = "", TITLE_VIA_NONE
    else:
        title_model, title_via = resolve_title_model(
            config.ollama_host, config.ollama_model, curator_model,
            config.ollama_api_key, ping=llm_reachable)
        if title_model != config.ollama_model:
            log.info("translation using %s (configured base %s, via %s)",
                     title_model, config.ollama_model, title_via)
    claimed: set = set()        # repo ids already taken by an earlier theme THIS run
    results: dict = {}          # theme.key -> picked repos
    # theme.key -> (status, items, error) for themes that ran this slot.
    # Skipped slots are absent so they are not recorded as empty.
    outcomes: dict = {}
    # llm rank whose scores did not parse (empty content, timeout, unparseable).
    # A quiet slot is not recorded here. Alerted after delivery, never on the digest.
    scoring_fallbacks: list[tuple[str, str]] = []

    trending_hits, gitnova_hits, extra_outcomes = _load_external(config.themes, now, config)
    inflated = {hit.full_name.lower() for hit in gitnova_hits if is_likely_inflated(hit)}

    readme_cache: dict[str, tuple] = {}

    def _readme(full_name: str) -> tuple:
        cached = readme_cache.get(full_name)
        if not cached:
            cached = readme_parts(full_name, token=config.github_token)
            readme_cache[full_name] = cached
        return cached

    def describe(r):
        return _readme(r.full_name)[0]

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
                    searched = apply_star_ceiling(expand_since(q, today), theme.max_stars)
                    for r in search_repos(searched, sort=theme.sort,
                                          order=theme.order, token=config.github_token,
                                          per_page=SEARCH_PER_PAGE, page=page):
                        if r.id not in seen_ids:
                            seen_ids.add(r.id)
                            repos.append(r)
            if len(queries) > 1:
                repos.sort(key=lambda r: r.stars, reverse=True)   # merged pool, best first
            blocked_ids = set(state.get(theme.key, []))
            if not theme.delta_days:
                blocked_ids |= posted_ids(state)
            (repos, theme_gains, trending_added, trending_considered,
             gitnova_added, inflated_dropped) = _merge_external(
                theme, repos, trending_hits, gitnova_hits, inflated,
                blocked_ids, config.github_token)
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
            repos = cap_stars(repos, theme.max_stars, theme.max_stars_exempt_days, today)
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
                funnel_args.extend((trending_considered, trending_added))
            log.info(funnel, *funnel_args)
            if gitnova_added or inflated_dropped:
                log.info("theme %s: gitnova_added=%d inflated_dropped=%d",
                         theme.key, gitnova_added, inflated_dropped)
            # Stars fallback is not "none above the bar" — that line is the quiet slot.
            if repos and not picked and not fallback_reason:
                log.info("theme %s: %d candidates, none above the quality bar",
                         theme.key, len(repos))
            results[theme.key] = picked
            claimed.update(p.repo.id for p in picked)
            # Quiet slot: candidates were fetched and scoring parsed, but none
            # cleared min_score. That is healthy (see ranker.rank). Recording
            # it as empty made two crypto runs page "source empty for 24h+".
            # A pool that is actually empty, or a scoring fallback that
            # produced no picks, stays empty. A selection exception is
            # "error" below.
            status = "ok" if picked or (repos and not fallback_reason) else "empty"
            outcomes[theme.key] = (status, len(picked), "")
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
            headings = None
            if config.ollama_host:
                excerpts = []
                headings = []
                for r in repos_:
                    parts = _readme(r.full_name)
                    excerpts.append(parts[1] if len(parts) > 1 else "")
                    headings.append(parts[2] if len(parts) > 2 else "")
                summaries = make_summaries(repos_, excerpts, whys=whys, host=config.ollama_host,
                                           model=curator_model or "", api_key=config.ollama_api_key)
            # Titles never call a model. The H1 is available when the README
            # was already fetched for the summary; otherwise the repo name is used.
            titles = make_titles(repos_, headings=headings)
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
                       "degraded (stars-only picks, no AI blurbs/translation). "
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
                       f"translation ran on {title_model} "
                       "(curation unaffected; titles are the repo name). "
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
    ordered.extend(extra_outcomes)
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
