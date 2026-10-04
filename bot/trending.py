"""GitHub Trending (github.com/trending) as a candidate source.

Search queries with ``created:>{since:Nd}`` never see an older repo, no matter
how many stars it gained this week. The Trending pages have no such gate, and
GitHub publishes no trending API — the HTML list is the source. Movers ranks
those repos by the page's own period gain ("14,507 stars this week") until a
snapshot in ``STATE_DIR`` can supply an owned diff.

Parsing stays on the two strings that identify a row: the ``<h2>`` repo link
and the "N stars today/this week/this month" text. Class names are not
required. ``Accept-Language: en`` keeps that sentence in English.
"""
import logging
import re
import time
from dataclasses import dataclass

import httpx

log = logging.getLogger("bot.trending")

_PAGE = "https://github.com/trending"
_HEADERS = {
    "User-Agent": "interesting-repos-bot",
    "Accept": "text/html",
    "Accept-Language": "en",
}
_ARTICLE = re.compile(r"<article\b[^>]*>([\s\S]*?)</article>", re.I)
_H2 = re.compile(r"<h2\b[^>]*>([\s\S]*?)</h2>", re.I)
_HREF = re.compile(r'href="/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)"')
_GAIN = re.compile(
    r"([\d][\d,]*(?:\.\d+)?k?)\s+stars\s+(today|this week|this month)",
    re.I,
)
_STAR_LINK = re.compile(r'href="/[^"]+/stargazers"[^>]*>([\s\S]*?)</a>', re.I)
_COUNT = re.compile(r"[\d][\d,]*(?:\.\d+)?k?", re.I)
_LABEL = {"today": "daily", "this week": "weekly", "this month": "monthly"}
# Prefer the longer window when a repo is on more than one list, so a Sunday
# Movers run labels the gain "this week" instead of "today".
_PERIOD_RANK = {"weekly": 3, "monthly": 2, "daily": 1}


@dataclass(frozen=True)
class TrendingHit:
    full_name: str
    gained: int
    period: str          # "daily", "weekly", or "monthly"
    stars: int = 0       # total on the page; the API count replaces it after hydrate


def _parse_count(raw: str) -> int | None:
    s = raw.strip().lower().replace(",", "")
    if not s:
        return None
    mult = 1
    if s.endswith("k"):
        mult = 1000
        s = s[:-1]
    try:
        return int(float(s) * mult)
    except ValueError:
        return None


def parse_trending_html(html: str) -> list[TrendingHit]:
    """Rows from one Trending page. Articles without a period-gain are skipped."""
    hits: list[TrendingHit] = []
    seen: set[str] = set()
    for article in _ARTICLE.findall(html or ""):
        h2 = _H2.search(article)
        if not h2:
            continue
        href = _HREF.search(h2.group(1))
        if not href:
            continue
        full_name = f"{href.group(1)}/{href.group(2)}"
        key = full_name.lower()
        if key in seen:
            continue
        gain_m = _GAIN.search(article)
        if not gain_m:
            continue
        gained = _parse_count(gain_m.group(1))
        period = _LABEL.get(gain_m.group(2).lower())
        if gained is None or not period:
            continue
        stars = 0
        star_link = _STAR_LINK.search(article)
        if star_link:
            text = re.sub(r"<[^>]+>", " ", star_link.group(1))
            count_m = _COUNT.search(text)
            if count_m:
                stars = _parse_count(count_m.group(0)) or 0
        seen.add(key)
        hits.append(TrendingHit(full_name, gained, period, stars))
    return hits


def merge_hits(hits: list[TrendingHit]) -> list[TrendingHit]:
    """One hit per repo. The longer window wins (weekly over daily); ties keep
    the larger gain. First-seen order is preserved."""
    best: dict[str, TrendingHit] = {}
    order: list[str] = []
    for hit in hits:
        key = hit.full_name.lower()
        prev = best.get(key)
        if prev is None:
            best[key] = hit
            order.append(key)
            continue
        rank = _PERIOD_RANK.get(hit.period, 0)
        prev_rank = _PERIOD_RANK.get(prev.period, 0)
        if rank > prev_rank or (rank == prev_rank and hit.gained > prev.gained):
            best[key] = hit
    return [best[k] for k in order]


def _fetch_html(client: httpx.Client, period: str, retries: int, sleep) -> str:
    last_status = None
    for attempt in range(retries):
        try:
            resp = client.get(_PAGE, params={"since": period}, headers=_HEADERS)
        except httpx.HTTPError:
            if attempt < retries - 1:
                sleep(float(2 ** attempt))
                continue
            return ""
        if resp.status_code == 200 and resp.text:
            return resp.text
        last_status = resp.status_code
        if resp.status_code in (403, 429) or resp.status_code >= 500:
            if attempt < retries - 1:
                sleep(float(2 ** attempt))
                continue
        return ""
    log.warning("github trending %s HTTP %s", period, last_status)
    return ""


def collect_trending(periods, client: httpx.Client | None = None, retries: int = 3,
                     sleep=time.sleep) -> list[TrendingHit]:
    """Fetch and parse each window. A failed window is skipped; the other
    still counts. Never raises — the caller keeps the search pool."""
    if not periods:
        return []
    owns_client = client is None
    client = client or httpx.Client(timeout=30, follow_redirects=True)
    hits: list[TrendingHit] = []
    try:
        for period in periods:
            html = _fetch_html(client, period, retries=retries, sleep=sleep)
            if not html:
                log.warning("github trending %s fetch failed", period)
                continue
            found = parse_trending_html(html)
            if not found:
                log.warning("github trending %s parsed 0 repos", period)
            hits.extend(found)
    finally:
        if owns_client:
            client.close()
    return merge_hits(hits)


def merge_trending(repos: list, hits: list[TrendingHit], missing_names: list[str],
                   hydrated: list) -> tuple[list, dict]:
    """Append hydrated Trending repos the search pool did not already contain.

    ``hydrated`` lines up with ``missing_names`` (``None`` = skipped). Returns
    ``(repos, gains)`` where ``gains`` is ``{repo_id: (gained, period)}`` for
    every resolved hit, including repos that were already in ``repos``.
    Forks and archived repos are not appended. Input ``repos`` is not mutated.
    """
    by_name = {r.full_name.lower(): r for r in repos}
    if len(hydrated) < len(missing_names):
        hydrated = list(hydrated) + [None] * (len(missing_names) - len(hydrated))
    for name, repo in zip(missing_names, hydrated):
        if repo is None:
            continue
        by_name.setdefault(name.lower(), repo)
        by_name[repo.full_name.lower()] = repo
    gains: dict[int, tuple[int, str]] = {}
    added: list = []
    seen = {r.id for r in repos}
    for hit in hits:
        repo = by_name.get(hit.full_name.lower())
        if repo is None or repo.is_fork or repo.is_archived:
            continue
        gains[repo.id] = (hit.gained, hit.period)
        if repo.id not in seen:
            seen.add(repo.id)
            added.append(repo)
    return list(repos) + added, gains
