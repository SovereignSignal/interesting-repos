import os
import re
import tomllib
from dataclasses import dataclass
from datetime import date, timedelta

_SINCE_RE = re.compile(r"\{since:(\d+)d\}")
_WEEKDAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}

# Default host is Ollama Cloud. Direct /api/chat catalog id is `gemma4:31b`
# (confirmed via GET https://ollama.com/api/tags). `gemma4:31b-cloud` is the
# local-daemon offload tag (`ollama run …`) and is not in that catalog.
# `alerts.model_aliases` still tries the `-cloud` sibling so a leftover of
# either form self-heals. Local runs: OLLAMA_HOST=http://localhost:11434.
DEFAULT_OLLAMA_MODEL = "gemma4:31b"


def _parse_at(raw: list) -> tuple:
    """Parse ["mon 13", "thu 16"] into ((0, 13), (3, 16)) — (weekday, UTC hour) pairs.
    Invalid entries fail fast at config load, not mid-run."""
    slots = []
    for entry in raw:
        try:
            day_s, hour_s = entry.split()
            day = _WEEKDAYS[day_s.lower()]
            hour = int(hour_s)
        except (ValueError, KeyError, AttributeError):
            raise SystemExit(f"themes.toml: invalid at entry {entry!r} (want e.g. 'mon 13')")
        if not 0 <= hour <= 23:
            raise SystemExit(f"themes.toml: invalid hour in at entry {entry!r} (0-23)")
        slots.append((day, hour))
    return tuple(slots)


def expand_since(query: str, today: date) -> str:
    def repl(m: "re.Match[str]") -> str:
        days = int(m.group(1))
        return (today - timedelta(days=days)).isoformat()
    return _SINCE_RE.sub(repl, query)


@dataclass(frozen=True)
class Theme:
    key: str
    name: str
    emoji: str
    query: str | tuple  # one GitHub search query, or several merged+deduped
    sort: str = "stars"
    order: str = "desc"
    count: int = 7
    rank: str = "stars"
    profile: str = ""
    catch_all: bool = False
    max_idle_days: int = 60
    agent_skill_cap: int | None = None
    min_score: int = 6    # curator score a repo must reach to be posted (0-10)
    delta_days: int | None = None   # set => source candidates by N-day star growth (Movers)
    at: tuple | None = None


def load_themes(path: str) -> list[Theme]:
    with open(path, "rb") as f:
        data = tomllib.load(f)
    themes: list[Theme] = []
    for t in data.get("theme", []):
        raw_at = t.get("at")
        at = _parse_at(raw_at) if raw_at else None
        raw_q = t["query"]
        query = tuple(raw_q) if isinstance(raw_q, list) else raw_q
        themes.append(Theme(
            key=t["key"],
            name=t["name"],
            emoji=t.get("emoji", ""),
            query=query,
            sort=t.get("sort", "stars"),
            order=t.get("order", "desc"),
            count=t.get("count", 7),
            rank=t.get("rank", "stars"),
            profile=t.get("profile", ""),
            catch_all=t.get("catch_all", False),
            max_idle_days=t.get("max_idle_days", 60),
            agent_skill_cap=t.get("agent_skill_cap"),
            min_score=t.get("min_score", 6),
            delta_days=t.get("delta_days"),
            at=at,
        ))
    return themes


@dataclass(frozen=True)
class Config:
    telegram_bot_token: str
    telegram_chat_id: str
    github_token: str
    state_dir: str
    themes: list[Theme]
    ollama_host: str = "https://ollama.com"
    ollama_model: str = DEFAULT_OLLAMA_MODEL
    ollama_api_key: str = ""
    ollama_curator_models: tuple = ()   # ordered curator candidates; first reachable wins
    send_delay_seconds: float = 0
    slack_bot_token: str = ""
    slack_channel_id: str = ""
    alert_chat_id: str = ""


def load_config(env: dict | None = None, themes_path: str = "themes.toml") -> Config:
    env = os.environ if env is None else env

    def require(name: str) -> str:
        val = env.get(name)
        if not val:
            raise SystemExit(f"Missing required environment variable: {name}")
        return val

    return Config(
        telegram_bot_token=require("TELEGRAM_BOT_TOKEN"),
        telegram_chat_id=require("TELEGRAM_CHAT_ID"),
        github_token=env.get("GITHUB_TOKEN", ""),
        state_dir=env.get("STATE_DIR", "/data"),
        themes=load_themes(themes_path),
        ollama_host=env.get("OLLAMA_HOST", "https://ollama.com"),
        ollama_model=env.get("OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL),
        ollama_api_key=env.get("OLLAMA_API_KEY", ""),
        ollama_curator_models=tuple(
            m.strip() for m in env.get("OLLAMA_CURATOR_MODEL", "").split(",") if m.strip()),
        send_delay_seconds=float(env.get("SEND_DELAY_SECONDS", "20")),
        slack_bot_token=env.get("SLACK_BOT_TOKEN", ""),
        slack_channel_id=env.get("SLACK_CHANNEL_ID", ""),
        alert_chat_id=env.get("ALERT_CHAT_ID", ""),
    )
