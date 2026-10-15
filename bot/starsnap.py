"""Rolling star-snapshot store for the Movers (star-delta) digest.

Every run folds the repos it searches (and any GitHub Trending repos it
hydrated) into today's snapshot (``STATE_DIR/starsnap/YYYY-MM-DD.json`` →
``{repo_id: stars}``). A delta theme compares today's counts to the oldest
star count per repo across snapshots from ``delta_days + tolerance`` ago
through yesterday (the day nearest ``delta_days`` ago per repo), so a mid-week
watch hit is visible on Sunday. Growth is normalized by each repo's actual
elapsed days and labeled with that interval, never assumed weekly. A repo with
no baseline is dropped unless the caller passes a measured gain (the Trending
page's period count) — that is how an older repo is eligible the Sunday we
first see it. Disposable: delete the dir and snapshot diffs go quiet until
the window fills in; the Trending-page gain still covers that cold start.
No other feature depends on the store."""
import json
import os
from datetime import date, timedelta


def snapshot_path(state_dir: str, day: date) -> str:
    return os.path.join(state_dir, "starsnap", f"{day.isoformat()}.json")


def load_snapshot(state_dir: str, day: date) -> dict:
    """``{repo_id: stars}`` for `day`, or ``{}`` if absent. Coerces JSON string
    keys back to ints (Repo.id is int)."""
    path = snapshot_path(state_dir, day)
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return {int(k): int(v) for k, v in data.items()}


def save_snapshot(state_dir: str, day: date, mapping: dict) -> None:
    """Atomic write (``.tmp`` + ``os.replace``, like ``state.save_state``)."""
    path = snapshot_path(state_dir, day)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(mapping, f)
    os.replace(tmp, path)


class Baseline(dict):
    """``{repo_id: stars}`` plus how many non-empty snapshot days were merged,
    and the snapshot day each count was observed (``observed``) as of ``as_of``."""

    def __init__(self, data=(), *, baseline_days: int = 0, observed=None,
                 as_of: date | None = None):
        super().__init__(data)
        self.baseline_days = baseline_days
        self.observed = dict(observed or {})
        self.as_of = as_of

    def elapsed_days(self, repo_id) -> int | None:
        """Days between the observation and ``as_of``. None when unknown."""
        day = self.observed.get(repo_id)
        if day is None or self.as_of is None:
            return None
        return max((self.as_of - day).days, 1)


def elapsed_days(baseline, repo_id, default: int) -> int:
    """A repo's baseline interval in days, or ``default`` for a plain dict."""
    days = None
    if isinstance(baseline, Baseline):
        days = baseline.elapsed_days(repo_id)
    return days if days else max(default, 1)


def find_baseline(state_dir: str, today: date, delta_days: int,
                  tolerance: int = 3) -> Baseline:
    """One star count per repo from the snapshots in
    ``[today - delta_days - tolerance, today - 1]``, with the day it was seen.

    Today's file is not read. A repo keeps the count from the day nearest
    ``delta_days`` ago (the older day on a tie), so last Sunday beats
    Wednesday, and a Wednesday-only repo still feeds Sunday. The observation
    day is kept so growth is divided by the real interval, not ``delta_days``.
    Empty (``baseline_days == 0``) when the window has no repos — caller
    treats that as a quiet slot.
    """
    start = today - timedelta(days=delta_days + tolerance)
    end = today - timedelta(days=1)
    merged: dict[int, int] = {}
    observed: dict[int, date] = {}
    days = 0
    day = start
    while day <= end:
        snap = load_snapshot(state_dir, day)
        if snap:
            days += 1
            distance = abs((today - day).days - delta_days)
            for repo_id, stars in snap.items():
                seen = observed.get(repo_id)
                if seen is None or distance < abs((today - seen).days - delta_days):
                    merged[repo_id] = stars
                    observed[repo_id] = day
        day += timedelta(days=1)
    return Baseline(merged, baseline_days=days, observed=observed, as_of=today)


def retain(state_dir: str, today: date, keep_days: int = 14) -> None:
    """Delete snapshots older than ``keep_days``. Keeps ~2x the delta window so a
    missed cron day still leaves a usable baseline."""
    folder = os.path.join(state_dir, "starsnap")
    if not os.path.isdir(folder):
        return
    cutoff = today - timedelta(days=keep_days)
    for name in os.listdir(folder):
        if not name.endswith(".json"):
            continue
        try:
            day = date.fromisoformat(name[:-5])
        except ValueError:
            continue
        if day < cutoff:
            os.remove(os.path.join(folder, name))


# Days a measured gain covers, so a daily Trending count and a weekly snapshot
# diff can share one sort. Snapshot diffs use their own elapsed days.
_PERIOD_DAYS = {"daily": 1, "weekly": 7, "monthly": 30}


def period_days(period: str) -> int | None:
    """Days a period label covers: the named windows, or ``"Nd"`` for N days."""
    days = _PERIOD_DAYS.get(period)
    if days:
        return days
    if isinstance(period, str) and period.endswith("d") and period[:-1].isdigit():
        return int(period[:-1]) or None
    return None


def interval_period(days: int) -> str:
    """Display period for a snapshot diff over ``days``. Only 7 is "weekly"."""
    return "weekly" if days == 7 else f"{days}d"


def growth_pace(gain: int, period: str) -> float:
    """Stars per day for a Trending-page gain. Unknown periods return the raw gain."""
    days = period_days(period)
    if not days:
        return float(gain)
    return gain / days


def order_by_delta(repos, baseline: dict, extras: dict | None = None,
                   span_days: int = 7) -> list:
    """Sort by recent star growth, highest first. Stable on ties.

    A repo in ``baseline`` ranks by the owned snapshot diff per day
    (``(stars_now - baseline) / elapsed_days``, where a ``Baseline`` knows
    each repo's observation day and a plain dict uses ``span_days``). That wins even when ``extras`` also has the
    repo. Otherwise ``extras[id]`` is ``(gain, period)`` from GitHub Trending
    (``"daily"`` / ``"weekly"`` / ``"monthly"``). Repos with neither are
    dropped — a delta is undefined, so they are not eligible.

    Every key is stars per day, so a repo that gained 500 stars today
    outranks one that gained 1,400 this week, and a 10-day diff is not
    compared as if it were 7 days.
    """
    scored = []
    for r in repos:
        prev = baseline.get(r.id)
        if prev is not None:
            gain = r.stars - prev
            key = gain / elapsed_days(baseline, r.id, span_days)
        elif extras and r.id in extras:
            gain, period = extras[r.id]
            key = growth_pace(gain, period)
        else:
            continue
        scored.append((key, r))
    scored.sort(key=lambda t: t[0], reverse=True)
    return [r for _, r in scored]
