"""GitHub Trending (github.com/trending) as a candidate source.

Search queries with ``created:>{since:Nd}`` never see an older repo, no matter
how many stars it gained this week. The Trending pages have no such gate, and
GitHub publishes no trending API — the HTML list is the source. Movers ranks
those repos by the page's own period gain ("14,507 stars this week") until a
snapshot in ``STATE_DIR`` can supply an owned diff.

Parsing stays on the strings that identify a row: the ``<h2>`` repo link, the
"N stars today/this week/this month" text, and (when present) the description
paragraph, ``itemprop="programmingLanguage"``, and ``record_id`` in the row.
Class names are not required. A row with no period-gain is skipped. Missing
id or description does not drop the row — the caller hydrates only if it
still needs those fields. ``Accept-Language: en`` keeps the gain sentence in
English.

Optional per-language pages (``/trending/{language}?since=``) are extra
windows for a theme whose query names ``language:``. A failed page is skipped.
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
_P = re.compile(r"<p\b[^>]*>([\s\S]*?)</p>", re.I)
_LANG_PROP = re.compile(
    r'itemprop="programmingLanguage"[^>]*>\s*([^<]+?)\s*<', re.I)
_RECORD_ID = re.compile(
    r'(?:record_id|repository_id)(?:"|&quot;|&#34;)\s*:\s*(\d+)')
_LANG_SLUG = re.compile(r"[a-z0-9+.#]{1,40}")


@dataclass(frozen=True)
class TrendingHit:
    full_name: str
    gained: int
    period: str          # "daily", "weekly", or "monthly"
    stars: int = 0       # total on the page; the API count replaces it after hydrate
    description: str = ""
    language: str = ""
    repo_id: int = 0     # GitHub numeric id from the page; 0 when the markup has none
    is_fork: bool = False


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


def _article_description(article: str) -> str:
    best = ""
    for match in _P.finditer(article):
        text = re.sub(r"<[^>]+>", " ", match.group(1))
        text = re.sub(r"\s+", " ", text).strip()
        if text.lower() in {"star", "sponsor"}:
            continue
        if len(text) > len(best):
            best = text
    return best[:300]


def _article_language(article: str) -> str:
    match = _LANG_PROP.search(article)
    if not match:
        return ""
    return re.sub(r"\s+", " ", match.group(1)).strip()


def _article_repo_id(article: str) -> int:
    # record_id sits on the repo heading; repository_id on the star control.
    # Either is the GitHub database id. The first one in the row is enough.
    match = _RECORD_ID.search(article)
    if not match:
        return 0
    try:
        return int(match.group(1))
    except ValueError:
        return 0


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
        hits.append(TrendingHit(
            full_name, gained, period, stars,
            description=_article_description(article),
            language=_article_language(article),
            repo_id=_article_repo_id(article),
            is_fork=bool(re.search(r"\bforked from\b", article, re.I)),
        ))
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


def prefer_gain(current: tuple | None, gain: int, period: str) -> tuple:
    """Keep the longer window (weekly over daily). Same window keeps the larger gain."""
    proposed = (int(gain), period)
    if current is None:
        return proposed
    rank = _PERIOD_RANK.get(period, 0)
    prev = _PERIOD_RANK.get(current[1], 0)
    if rank > prev or (rank == prev and proposed[0] > current[0]):
        return proposed
    return current


def _language_slug(language: str) -> str:
    slug = (language or "").strip().lower()
    if _LANG_SLUG.fullmatch(slug):
        return slug
    return ""


def _fetch_html(client: httpx.Client, period: str, retries: int, sleep,
                language: str = "") -> str:
    last_status = None
    url = _PAGE
    slug = _language_slug(language)
    if slug:
        url = f"{_PAGE}/{slug}"
    for attempt in range(retries):
        try:
            resp = client.get(url, params={"since": period}, headers=_HEADERS)
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
                     sleep=time.sleep, languages=()) -> list[TrendingHit]:
    """Fetch and parse each window. A failed window is skipped; the other
    still counts. ``languages`` adds ``/trending/{language}`` for those same
    windows (a theme's ``language:`` qualifier). Never raises — the caller
    keeps the search pool."""
    if not periods:
        return []
    owns_client = client is None
    client = client or httpx.Client(timeout=30, follow_redirects=True)
    pages = [(period, "") for period in periods]
    for period in periods:
        for language in languages or ():
            if _language_slug(language):
                pages.append((period, language))
    hits: list[TrendingHit] = []
    try:
        for period, language in pages:
            html = _fetch_html(client, period, retries=retries, sleep=sleep,
                               language=language)
            label = f"{language} {period}".strip() if language else period
            if not html:
                log.warning("github trending %s fetch failed", label)
                continue
            found = parse_trending_html(html)
            if not found:
                log.warning("github trending %s parsed 0 repos", label)
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
