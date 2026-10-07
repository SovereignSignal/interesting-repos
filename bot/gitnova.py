"""GitNova (gitnova.dev) as a candidate source. Free, no key.

Three reads, merged by repo name:

- Atom ``/en/feed.xml`` — repos whose stars just started climbing (every 30 min).
- Category markdown ``/en/c/{ai_agents,llm,gen_media,devtools}.md``.
- JSON ``/api/v1/search?category=…`` — stars, today's gain, stage, trust.

Star-trust labels are a drop rule, not copy. ``likely inflated`` (and the JSON
trust code that means the same thing) removes the repo. ``organic`` / ``ok``
and ``unusual`` / ``doubt`` stay; the curator already sees velocity. The label
is never written onto a ``Repo``.

A failed read is skipped. If every read fails the caller gets status
``error`` and keeps the search pool. This module does not call the GitHub API.
"""
import json
import logging
import re
import time
from dataclasses import dataclass, replace
from xml.etree import ElementTree

import httpx

log = logging.getLogger("bot.gitnova")

_FEED = "https://gitnova.dev/en/feed.xml"
_CATEGORY_MD = "https://gitnova.dev/en/c/{category}.md"
_SEARCH = "https://gitnova.dev/api/v1/search"
# The scan's four lanes. Other GitNova categories are not fetched.
CATEGORIES = ("ai_agents", "llm", "gen_media", "devtools")

_NS = {"a": "http://www.w3.org/2005/Atom"}
_REPO_IN_LINK = re.compile(r"/r/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)")
_TITLE_NAME = re.compile(r"^([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)\s*$")
_MAGNITUDE = re.compile(r"magnitude\s+([0-9]+(?:\.[0-9]+)?)", re.I)
_ITEM = re.compile(
    r"^(\d+)\.\s+\*\*([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)\*\*\s+[—–-]\s+(.+)$",
    re.M,
)
_STARS_TODAY = re.compile(r"\+\s*([\d][\d,]*)\s+stars\b", re.I)

_STAGE = {
    "breakout": "breakout",
    "early": "early",
    "early signal": "early",
    "peak": "peak",
    "peaking": "peak",
    "steady": "steady",
    "cooling": "cooling",
}
_CATEGORY_NAME = {
    "ai agents": "ai_agents",
    "ai_agents": "ai_agents",
    "language models": "llm",
    "llm": "llm",
    "developer tools": "devtools",
    "devtools": "devtools",
    "generative media": "gen_media",
    "gen_media": "gen_media",
}
# JSON `trust` values seen on the search API, plus the prose labels.
_INFLATED_TRUST = {"inflated", "likely inflated", "likely_inflated", "fake"}
_HEADERS = {"User-Agent": "interesting-repos-bot", "Accept-Language": "en"}


@dataclass(frozen=True)
class GitNovaHit:
    full_name: str
    summary: str = ""
    category: str = ""          # ai_agents, llm, gen_media, devtools, …
    language: str = ""
    stars: int = 0
    stars_today: int = 0        # measured gain so far today; 0 when unknown
    magnitude: float = 0.0      # GitNova's velocity signal, not a quality score
    stage: str = ""             # breakout, early, peak, steady, cooling
    trust: str = ""             # ok / doubt / inflated — internal, never posted
    trust_label: str = ""       # prose label — internal, never posted
    age_days: int | None = None
    topics: tuple = ()


def is_likely_inflated(hit: GitNovaHit) -> bool:
    """True only for the drop label. Unusual / organic / doubt stay."""
    trust = (hit.trust or "").strip().lower().replace("_", " ")
    if trust in _INFLATED_TRUST or trust.replace(" ", "_") in _INFLATED_TRUST:
        return True
    label = (hit.trust_label or "").lower()
    if "likely inflated" in label:
        return True
    if "inflated" in label and "unusual" not in label:
        return True
    return False


def public_summary(text: str) -> str:
    """Summary with a trailing trust clause removed. Empty if nothing factual remains."""
    cleaned = (text or "").strip()
    cleaned = re.sub(
        r"(?i)\s*(?:[·•|]|-\s+)?\s*(?:likely inflated|unusual star pattern"
        r"(?:\s*\([^)]*\))?|star growth looks organic)\.?\s*$",
        "",
        cleaned,
    ).strip()
    if is_likely_inflated(GitNovaHit("_/_", trust_label=cleaned)):
        return ""
    low = cleaned.lower()
    if low in {"likely inflated", "unusual star pattern", "star growth looks organic"}:
        return ""
    return cleaned


def _stage(raw: str) -> str:
    return _STAGE.get((raw or "").strip().lower(), "")


def _category_key(raw: str) -> str:
    key = (raw or "").strip().lower()
    return _CATEGORY_NAME.get(key, "")


def _parse_int(raw: str) -> int:
    try:
        return int(str(raw).replace(",", "").strip())
    except (TypeError, ValueError):
        return 0


def _full_name(title: str, link: str) -> str:
    match = _REPO_IN_LINK.search(link or "")
    if match:
        return f"{match.group(1)}/{match.group(2)}"
    head = re.split(r"\s+[—–-]\s+", title or "", maxsplit=1)[0].strip()
    named = _TITLE_NAME.match(head)
    if named:
        return f"{named.group(1)}/{named.group(2)}"
    return ""


def parse_feed_xml(xml: str) -> list[GitNovaHit]:
    """Atom entries. No trust and no star count — magnitude only. Bad XML → []."""
    if not xml or "<feed" not in xml:
        return []
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError:
        return []
    hits = []
    for entry in root.findall("a:entry", _NS):
        title = entry.findtext("a:title", default="", namespaces=_NS) or ""
        summary = entry.findtext("a:summary", default="", namespaces=_NS) or ""
        link = ""
        for el in entry.findall("a:link", _NS):
            if el.get("rel") in (None, "alternate"):
                link = el.get("href") or link
                if el.get("rel") == "alternate":
                    break
        full_name = _full_name(title, link)
        if not full_name:
            continue
        mag = _MAGNITUDE.search(title)
        hits.append(GitNovaHit(
            full_name,
            summary=public_summary(summary),
            magnitude=float(mag.group(1)) if mag else 0.0,
        ))
    return hits


def _trust_from_prose(part: str) -> tuple[str, str]:
    low = part.lower()
    if "likely inflated" in low or ("inflated" in low and "unusual" not in low):
        return "inflated", part.strip()
    if "unusual" in low:
        return "doubt", part.strip()
    if "organic" in low:
        return "ok", part.strip()
    return "", ""


def parse_category_md(text: str, category: str = "") -> list[GitNovaHit]:
    """Numbered category-page items. A row without ``owner/repo`` is skipped."""
    if not text:
        return []
    matches = list(_ITEM.finditer(text))
    hits = []
    default_cat = _category_key(category) or category
    for index, match in enumerate(matches):
        owner, name, rest = match.group(2), match.group(3), match.group(4)
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        block = text[match.end():end]
        summary = ""
        for line in block.splitlines():
            stripped = line.strip()
            if not stripped or stripped.lower().startswith("full card:"):
                continue
            summary = stripped
            break
        parts = [p.strip() for p in rest.split("·")]
        magnitude = 0.0
        stage = ""
        cat = default_cat
        language = ""
        stars_today = 0
        trust, trust_label = "", ""
        for i, part in enumerate(parts):
            if i == 0:
                try:
                    magnitude = float(part)
                except ValueError:
                    magnitude = 0.0
                continue
            if i == 1 and _stage(part):
                stage = _stage(part)
                continue
            if i == 2 and _category_key(part):
                cat = _category_key(part)
                continue
            stars = _STARS_TODAY.search(part)
            if stars:
                stars_today = _parse_int(stars.group(1))
                continue
            code, label = _trust_from_prose(part)
            if code or label:
                trust, trust_label = code, label
                continue
            if not language and re.fullmatch(r"[A-Za-z0-9+#. ]{1,32}", part):
                language = part.strip()
        hits.append(GitNovaHit(
            f"{owner}/{name}",
            summary=public_summary(summary),
            category=cat,
            language=language,
            stars_today=stars_today,
            magnitude=magnitude,
            stage=stage,
            trust=trust,
            trust_label=trust_label,
        ))
    return hits


def _optional_int(value) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return None


def parse_search_json(payload) -> list[GitNovaHit]:
    """``/api/v1/search`` body. A non-object or missing ``results`` is []."""
    if isinstance(payload, str):
        if not payload.strip():
            return []
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return []
    if not isinstance(payload, dict):
        return []
    results = payload.get("results")
    if not isinstance(results, list):
        return []
    hits = []
    for item in results:
        if not isinstance(item, dict):
            continue
        full_name = item.get("full_name") or ""
        if not _TITLE_NAME.match(str(full_name).strip()):
            continue
        topics = item.get("topics") or ()
        if not isinstance(topics, (list, tuple)):
            topics = ()
        hits.append(GitNovaHit(
            full_name=str(full_name).strip(),
            summary=public_summary(item.get("summary") or ""),
            category=_category_key(str(item.get("category") or "")) or str(item.get("category") or ""),
            language=str(item.get("language") or ""),
            stars=_parse_int(item.get("stars") or 0),
            stars_today=_parse_int(item.get("stars_today_so_far") or 0),
            magnitude=float(item["magnitude"]) if isinstance(item.get("magnitude"), (int, float)) else 0.0,
            stage=_stage(str(item.get("stage") or "")),
            trust=str(item.get("trust") or ""),
            trust_label=str(item.get("trust_label") or ""),
            age_days=_optional_int(item.get("age_days")),
            topics=tuple(str(t) for t in topics if t),
        ))
    return hits


def _richness(hit: GitNovaHit) -> tuple:
    return (
        1 if hit.trust or hit.trust_label else 0,
        1 if hit.stars else 0,
        1 if hit.stars_today else 0,
        1 if hit.stage else 0,
        1 if hit.category else 0,
        1 if hit.age_days is not None else 0,
        1 if hit.summary else 0,
    )


def _fill(primary: GitNovaHit, other: GitNovaHit) -> GitNovaHit:
    age = primary.age_days if primary.age_days is not None else other.age_days
    return replace(
        primary,
        summary=primary.summary or other.summary,
        category=primary.category or other.category,
        language=primary.language or other.language,
        stars=primary.stars or other.stars,
        stars_today=primary.stars_today or other.stars_today,
        magnitude=primary.magnitude or other.magnitude,
        stage=primary.stage or other.stage,
        trust=primary.trust or other.trust,
        trust_label=primary.trust_label or other.trust_label,
        age_days=age,
        topics=primary.topics or other.topics,
    )


def merge_gitnova(hits: list[GitNovaHit]) -> list[GitNovaHit]:
    """One hit per repo. The richer record wins; inflated on any read sticks."""
    best: dict[str, GitNovaHit] = {}
    order: list[str] = []
    inflated: set[str] = set()
    for hit in hits:
        key = hit.full_name.lower()
        if is_likely_inflated(hit):
            inflated.add(key)
        prev = best.get(key)
        if prev is None:
            best[key] = hit
            order.append(key)
            continue
        primary, other = (hit, prev) if _richness(hit) >= _richness(prev) else (prev, hit)
        best[key] = _fill(primary, other)
    merged = []
    for key in order:
        hit = best[key]
        if key in inflated and not is_likely_inflated(hit):
            hit = replace(hit, trust="inflated", trust_label=hit.trust_label or "likely inflated")
        merged.append(hit)
    return merged


def _get(client: httpx.Client, url: str, params: dict | None, retries: int, sleep,
         headers: dict) -> tuple[str, str]:
    """``(body, error)``. Empty body and an error string on failure. Never raises."""
    last = ""
    for attempt in range(retries):
        try:
            resp = client.get(url, params=params, headers=headers)
        except httpx.HTTPError as exc:
            last = type(exc).__name__
            if attempt < retries - 1:
                sleep(float(2 ** attempt))
                continue
            return "", last
        if resp.status_code == 200 and resp.text:
            return resp.text, ""
        last = f"HTTP {resp.status_code}"
        if resp.status_code in (403, 429) or resp.status_code >= 500:
            if attempt < retries - 1:
                sleep(float(2 ** attempt))
                continue
        return "", last
    return "", last or "gitnova fetch failed"


def collect_gitnova(client: httpx.Client | None = None, retries: int = 2,
                    sleep=time.sleep, limit: int = 40) -> tuple[list[GitNovaHit], str, str]:
    """Fetch Atom, the four category pages, and the JSON search.

    Returns ``(hits, status, error)``. ``status`` is ``ok``, ``empty``, or
    ``error``. Never raises — the digest keeps its other sources.
    """
    owns = client is None
    client = client or httpx.Client(timeout=15, follow_redirects=True)
    hits: list[GitNovaHit] = []
    errors: list[str] = []
    reads = 0
    failures = 0

    def _read(url: str, params: dict | None, accept: str, label: str) -> str:
        nonlocal reads, failures
        if failures >= 2:
            errors.append(f"{label} skipped")
            return ""
        reads += 1
        body, err = _get(client, url, params, retries, sleep, {**_HEADERS, "Accept": accept})
        if err or not body:
            failures += 1
            errors.append(f"{label} {err or 'empty'}")
            log.warning("gitnova %s failed: %s", label, err or "empty")
            return ""
        return body

    try:
        body = _read(_FEED, None, "application/atom+xml", "feed")
        if body:
            found = parse_feed_xml(body)
            if not found:
                log.warning("gitnova feed parsed 0 repos")
            hits.extend(found)
        for category in CATEGORIES:
            body = _read(_CATEGORY_MD.format(category=category), None,
                         "text/markdown", f"{category}.md")
            if body:
                found = parse_category_md(body, category=category)
                if not found:
                    log.warning("gitnova category %s parsed 0 repos", category)
                hits.extend(found)
            body = _read(
                _SEARCH,
                {"category": category, "sort": "magnitude", "limit": limit, "lang": "en"},
                "application/json", f"search {category}")
            if not body:
                continue
            found = parse_search_json(body)
            if not found:
                log.warning("gitnova search %s parsed 0 repos", category)
            hits.extend(found)
    finally:
        if owns:
            client.close()
    merged = merge_gitnova(hits)
    if merged:
        return merged, "ok", ""
    if errors and len(errors) >= reads:
        return [], "error", errors[0][:160]
    if errors and not merged:
        # Every read that returned a body parsed to nothing, and some failed.
        return [], "error", errors[0][:160]
    return [], "empty", ""
