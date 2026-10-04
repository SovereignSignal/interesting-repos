"""Rolling star-snapshot store for the Movers (star-delta) digest.

Every run folds the repos it searches (and any GitHub Trending repos it
hydrated) into today's snapshot (``STATE_DIR/starsnap/YYYY-MM-DD.json`` →
``{repo_id: stars}``). A delta theme compares today's counts to the oldest
star count per repo across snapshots from ``delta_days + tolerance`` ago
through yesterday, so a mid-week watch hit is visible on Sunday. A repo with
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
    """``{repo_id: stars}`` plus how many non-empty snapshot days were merged."""

    def __init__(self, data=(), *, baseline_days: int = 0):
        super().__init__(data)
        self.baseline_days = baseline_days


def find_baseline(state_dir: str, today: date, delta_days: int,
                  tolerance: int = 3) -> Baseline:
    """Oldest star count per repo from every snapshot in
    ``[today - delta_days - tolerance, today - 1]``.

    Today's file is not read. A repo keeps the count from the earliest day
    it appears, so a Wednesday snapshot still feeds Sunday even when last
    Sunday's file exists. Empty (``baseline_days == 0``) when the window has
    no repos — caller treats that as a quiet slot.
    """
    start = today - timedelta(days=delta_days + tolerance)
    end = today - timedelta(days=1)
    merged: dict[int, int] = {}
    days = 0
    day = start
    while day <= end:
        snap = load_snapshot(state_dir, day)
        if snap:
            days += 1
            for repo_id, stars in snap.items():
                if repo_id not in merged:  # earliest day wins
                    merged[repo_id] = stars
        day += timedelta(days=1)
    return Baseline(merged, baseline_days=days)


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
# diff can share one sort. Snapshot diffs use the caller's span instead.
_PERIOD_DAYS = {"daily": 1, "weekly": 7, "monthly": 30}


def growth_pace(gain: int, period: str) -> float:
    """Stars per day for a Trending-page gain. Unknown periods return the raw gain."""
    days = _PERIOD_DAYS.get(period)
    if not days:
        return float(gain)
    return gain / days


def order_by_delta(repos, baseline: dict, extras: dict | None = None,
                   span_days: int = 7) -> list:
    """Sort by recent star growth, highest first. Stable on ties.

    A repo in ``baseline`` ranks by the owned snapshot diff
    (``stars_now - baseline``). That wins even when ``extras`` also has the
    repo. Otherwise ``extras[id]`` is ``(gain, period)`` from GitHub Trending
    (``"daily"`` / ``"weekly"`` / ``"monthly"``). Repos with neither are
    dropped — a delta is undefined, so they are not eligible.

    Without ``extras`` the sort key is the raw snapshot diff (Movers' original
    order). With ``extras``, daily and weekly gains are compared per day, so
    a repo that gained 500 stars today outranks one that gained 1,400 this week.
    """
    scored = []
    for r in repos:
        prev = baseline.get(r.id)
        if prev is not None:
            gain = r.stars - prev
            key = gain / max(span_days, 1) if extras is not None else gain
        elif extras and r.id in extras:
            gain, period = extras[r.id]
            key = growth_pace(gain, period)
        else:
            continue
        scored.append((key, r))
    scored.sort(key=lambda t: t[0], reverse=True)
    return [r for _, r in scored]
