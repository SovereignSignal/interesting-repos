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
