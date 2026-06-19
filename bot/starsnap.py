"""Rolling star-snapshot store for the Movers (star-delta) digest.

Every run folds the repos it searches into today's snapshot
(``STATE_DIR/starsnap/YYYY-MM-DD.json`` → ``{repo_id: stars}``). A delta theme
compares today's counts to a baseline ~7 days old to source "what blew up this
week." Disposable: delete the dir and Movers goes quiet for a week while it
rebuilds; no other feature depends on it."""
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


def find_baseline(state_dir: str, today: date, delta_days: int,
                  tolerance: int = 3) -> dict:
    """The nearest snapshot aged in ``[delta_days, delta_days+tolerance]`` days,
    walking older. Returns ``{}`` when nothing in the window exists (cold start
    or a cron gap wider than tolerance) — caller treats that as a quiet slot."""
    for offset in range(delta_days, delta_days + tolerance + 1):
        snap = load_snapshot(state_dir, today - timedelta(days=offset))
        if snap:
            return snap
    return {}


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


def order_by_delta(repos, baseline: dict) -> list:
    """Repos with a baseline entry, sorted by ``stars_now - baseline`` desc.
    Repos absent from the baseline are dropped (delta undefined = not eligible).
    Stable on ties (preserves input order)."""
    scored = []
    for r in repos:
        prev = baseline.get(r.id)
        if prev is None:
            continue
        scored.append((r.stars - prev, r))
    scored.sort(key=lambda t: t[0], reverse=True)
    return [r for _, r in scored]
