import re
from datetime import date, datetime

KEYWORD_REPEAT_THRESHOLD = 5     # a length>=3 token repeated this many times => stuffed
VELOCITY_CEILING = 2500.0        # stars/day above which a repo is a fallback-only outlier

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def age_days(iso: str, today: date) -> int | None:
    """Whole days between an ISO-8601 timestamp's date and `today`. None if unparseable."""
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (today - dt.date()).days


def star_velocity(repo, today: date) -> float:
    """Stars per day since creation, with an age floor of 1 day (avoids div-by-zero
    and treats unknown/just-created repos as 1 day old)."""
    age = age_days(repo.created_at, today)
    return repo.stars / max(age or 1, 1)


def is_keyword_stuffed(repo) -> bool:
    """A description that repeats one token many times is keyword spam
    (e.g. 'hyperliquid sdk | hyperliquid sdk | ...')."""
    counts: dict[str, int] = {}
    for tok in _TOKEN_RE.findall((repo.description or "").lower()):
        if len(tok) >= 3:
            counts[tok] = counts.get(tok, 0) + 1
            if counts[tok] >= KEYWORD_REPEAT_THRESHOLD:
                return True
    return False


def is_awesome_list(repo) -> bool:
    """'awesome-*' curated link lists, by repo name or topic."""
    name = repo.full_name.split("/")[-1].lower()
    if name == "awesome" or name.startswith("awesome-"):
        return True
    return any(t.lower() in ("awesome", "awesome-list") for t in repo.topics)


def is_stale(repo, today: date, max_idle_days: int) -> bool:
    """True if not pushed within max_idle_days. Unknown push date => not stale (kept)."""
    idle = age_days(repo.pushed_at, today)
    return idle is not None and idle > max_idle_days


def clean(repos: list, today: date, max_idle_days: int) -> list:
    """Drop deterministic noise (keyword-stuffed, awesome-list) and stale repos,
    preserving order. The high-precision drops here also protect the stars-fallback."""
    return [r for r in repos
            if not is_keyword_stuffed(r)
            and not is_awesome_list(r)
            and not is_stale(r, today, max_idle_days)]
