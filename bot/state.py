import json
import os


def load_state(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_state(path: str, state: dict) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f)
    os.replace(tmp, path)   # atomic on POSIX


def unsent(state: dict, theme_key: str, repos: list) -> list:
    sent = set(state.get(theme_key, []))
    return [r for r in repos if r.id not in sent]


def record_sent(state: dict, theme_key: str, repo_ids: list, cap: int = 500) -> dict:
    existing = list(state.get(theme_key, []))
    for rid in repo_ids:
        if rid not in existing:
            existing.append(rid)
    state[theme_key] = existing[-cap:]
    return state


# Global cooling-off: a repo posted under any theme is skipped by later themes
# until it ages out of this cap. Magic key must never collide with a theme.key.
POSTED_KEY = "_posted"
POSTED_CAP = 2000


def posted_ids(state: dict) -> set:
    return set(state.get(POSTED_KEY, []))


def unposted(state: dict, repos: list) -> list:
    """Drop repos already posted by *any* theme. Movers callers skip this."""
    seen = posted_ids(state)
    return [r for r in repos if r.id not in seen]


def record_posted(state: dict, repo_ids: list, cap: int = POSTED_CAP) -> dict:
    existing = list(state.get(POSTED_KEY, []))
    for rid in repo_ids:
        if rid not in existing:
            existing.append(rid)
    state[POSTED_KEY] = existing[-cap:]
    return state


# Replay payload for AI Wire. Not a theme key, so unsent/unposted ignore it.
# The id lists stay the dedupe record; this is the owner/repo, title, url, and
# posted blurb those lists do not have. Older volumes simply lack the key.
WIRE_KEY = "_ai_wire"
WIRE_CAP = 2000


def record_wire(state: dict, items: list, cap: int = WIRE_CAP) -> dict:
    """Remember posted ingest items, newest last, one row per canonical key."""
    raw = state.get(WIRE_KEY)
    existing = []
    if isinstance(raw, list):
        for entry in raw:
            if isinstance(entry, dict) and isinstance(entry.get("canonical_key"), str):
                existing.append(entry)
    order: list[str] = []
    by_key: dict = {}
    for entry in existing:
        key = entry["canonical_key"]
        if key in by_key:
            order = [item for item in order if item != key]
        by_key[key] = entry
        order.append(key)
    for item in items or []:
        if not isinstance(item, dict):
            continue
        key = item.get("canonical_key")
        if not isinstance(key, str) or not key:
            continue
        if key in by_key:
            order = [item_key for item_key in order if item_key != key]
        by_key[key] = dict(item)
        order.append(key)
    if order:
        state[WIRE_KEY] = [by_key[key] for key in order[-cap:]]
    return state
