"""Route GitNova and GitHub Trending hits onto a theme's existing pool.

Movers (``github_trending`` set) keeps the whole Trending window. Other themes
take a hit only when its category, language, or topic matches that theme's
query — the search pool stays the primary net. Breakout / Early-signal /
Peaking GitNova rows also feed Movers, with a few Early-signal slots held
back so a small repo is not buried under a four-thousand-star day.

Nothing here posts, and nothing here calls the network.
"""
import re

from bot.filters import is_ai_repo
from bot.github import Repo
from bot.gitnova import GitNovaHit, is_likely_inflated
from bot.trending import TrendingHit

# GitNova category → theme key. gen_media has no theme of its own; AI & Agents
# is the list those repos can clear. Movers still sees them via stage.
CATEGORY_THEMES = {
    "ai_agents": frozenset({"ai-agents"}),
    "llm": frozenset({"ai-agents"}),
    "gen_media": frozenset({"ai-agents"}),
    "devtools": frozenset({"dev-tools"}),
}
# Stages that mean "moving now". Steady and cooling are not a Movers signal.
MOVER_STAGES = frozenset({"breakout", "early", "peak"})
GITNOVA_LIMIT = 12
EARLY_SLOTS = 4
THEME_TRENDING_LIMIT = 12

_TOPIC = re.compile(r"\btopic:([A-Za-z0-9][A-Za-z0-9-]*)", re.I)
_LANG = re.compile(r"\blanguage:([A-Za-z0-9+#.]+)", re.I)


def query_topics(theme) -> list[str]:
    queries = theme.query if isinstance(theme.query, tuple) else (theme.query,)
    found = []
    for query in queries:
        for match in _TOPIC.finditer(query or ""):
            token = match.group(1).lower()
            if token not in found:
                found.append(token)
    return found


def query_languages(theme) -> list[str]:
    queries = theme.query if isinstance(theme.query, tuple) else (theme.query,)
    found = []
    for query in queries:
        for match in _LANG.finditer(query or ""):
            token = match.group(1)
            if token.lower() not in {item.lower() for item in found}:
                found.append(token)
    return found


def _blob(*parts) -> str:
    return " ".join(p for p in parts if p).lower()


def _topic_in(blob: str, topic: str) -> bool:
    phrases = {topic, topic.replace("-", " ")}
    for phrase in phrases:
        if re.search(rf"\b{re.escape(phrase)}\b", blob):
            return True
    return False


def matches_theme(theme, *, language: str = "", description: str = "",
                  full_name: str = "", category: str = "", topics=()) -> bool:
    """True when this hit belongs on ``theme`` other than via Movers' open window."""
    if theme.key in CATEGORY_THEMES.get((category or "").lower(), frozenset()):
        return True
    langs = {item.lower() for item in query_languages(theme)}
    if language and language.lower() in langs:
        return True
    blob = _blob(full_name, description, language, " ".join(topics or ()))
    return any(_topic_in(blob, topic) for topic in query_topics(theme))


def _cap_gitnova(hits: list[GitNovaHit]) -> list[GitNovaHit]:
    """Keep the fastest rows, and reserve a few seats for Early signal."""
    early = [h for h in hits if h.stage == "early"]
    rest = [h for h in hits if h.stage != "early"]
    early.sort(key=lambda h: (h.magnitude, h.stars_today), reverse=True)
    rest.sort(key=lambda h: (h.stars_today, h.magnitude), reverse=True)
    early_keep = early[:EARLY_SLOTS]
    rest_keep = rest[: max(0, GITNOVA_LIMIT - len(early_keep))]
    chosen = []
    seen = set()
    for hit in rest_keep + early_keep:
        key = hit.full_name.lower()
        if key in seen:
            continue
        seen.add(key)
        chosen.append(hit)
    return chosen[:GITNOVA_LIMIT]


def gitnova_for_theme(hits: list[GitNovaHit], theme) -> list[GitNovaHit]:
    """Theme-matched rows, plus Movers' breakout/early/peak rows. Inflated dropped."""
    matched = []
    seen = set()
    for hit in hits:
        if is_likely_inflated(hit):
            continue
        key = hit.full_name.lower()
        if key in seen:
            continue
        take = False
        if theme.delta_days and hit.stage in MOVER_STAGES and hit.stars_today > 0:
            take = True
        elif matches_theme(
            theme, language=hit.language, description=hit.summary,
            full_name=hit.full_name, category=hit.category, topics=hit.topics,
        ):
            take = True
        if take:
            seen.add(key)
            matched.append(hit)
    return _cap_gitnova(matched)


def trending_for_theme(hits: list[TrendingHit], theme, *, extra: bool) -> list[TrendingHit]:
    """Movers: the configured windows. Other themes: language/topic matches, capped."""
    usable = [h for h in hits if not h.is_fork]
    if theme.github_trending:
        wanted = set(theme.github_trending)
        return [h for h in usable if h.period in wanted]
    if not extra:
        return []
    matched = [
        h for h in usable
        if matches_theme(
            theme, language=h.language, description=h.description,
            full_name=h.full_name,
        )
    ]
    matched.sort(key=lambda h: h.gained, reverse=True)
    return matched[:THEME_TRENDING_LIMIT]


def looks_ai(full_name: str, description: str = "", language: str = "",
             topics=()) -> bool:
    """Same classifier as ``cap_ai``. A throwaway ``Repo`` is not posted."""
    return is_ai_repo(Repo(
        0, full_name, "", description or "", 0, language or "",
        list(topics or []), False, False,
    ))


def blocked_by_ceiling(stars: int, age_days, theme) -> bool:
    """An over-ceiling repo whose age is already known and not exempt.

    Unknown age is not blocked here — the caller hydrates it so the young-repo
    exemption can still apply. ``cap_stars`` repeats the rule on the ``Repo``.
    """
    ceiling = getattr(theme, "max_stars", None)
    if not ceiling or stars <= ceiling:
        return False
    if age_days is None:
        return False
    exempt = getattr(theme, "max_stars_exempt_days", None)
    if exempt is None:
        return True
    return age_days > exempt


def repo_from_trending(hit: TrendingHit) -> Repo | None:
    """A pool repo from the Trending page alone. None without a numeric id."""
    if not hit.repo_id:
        return None
    return Repo(
        id=hit.repo_id,
        full_name=hit.full_name,
        html_url=f"https://github.com/{hit.full_name}",
        description=hit.description or "",
        stars=hit.stars,
        language=hit.language or "",
        topics=[],
        is_fork=hit.is_fork,
        is_archived=False,
    )


def with_description(repo: Repo, description: str) -> Repo:
    """Use ``description`` only when GitHub's own text is empty."""
    if repo.description or not description:
        return repo
    return Repo(
        id=repo.id,
        full_name=repo.full_name,
        html_url=repo.html_url,
        description=description,
        stars=repo.stars,
        language=repo.language,
        topics=list(repo.topics),
        is_fork=repo.is_fork,
        is_archived=repo.is_archived,
        created_at=repo.created_at,
        pushed_at=repo.pushed_at,
        forks=repo.forks,
        license=repo.license,
        owner_type=repo.owner_type,
    )
