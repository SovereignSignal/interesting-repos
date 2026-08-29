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
