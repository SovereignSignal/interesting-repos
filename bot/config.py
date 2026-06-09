import os
import re
import tomllib
from dataclasses import dataclass
from datetime import date, timedelta

_SINCE_RE = re.compile(r"\{since:(\d+)d\}")
_WEEKDAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


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
    query: str
    sort: str = "stars"
    order: str = "desc"
    count: int = 7
    rank: str = "stars"
    profile: str = ""
    catch_all: bool = False
    max_idle_days: int = 60
    agent_skill_cap: int | None = None
    days: tuple | None = None


def load_themes(path: str) -> list[Theme]:
    with open(path, "rb") as f:
        data = tomllib.load(f)
    themes: list[Theme] = []
    for t in data.get("theme", []):
        raw_days = t.get("days")
        days = tuple(_WEEKDAYS[d.lower()] for d in raw_days) if raw_days else None
        themes.append(Theme(
            key=t["key"],
            name=t["name"],
            emoji=t.get("emoji", ""),
            query=t["query"],
            sort=t.get("sort", "stars"),
            order=t.get("order", "desc"),
            count=t.get("count", 7),
            rank=t.get("rank", "stars"),
            profile=t.get("profile", ""),
            catch_all=t.get("catch_all", False),
            max_idle_days=t.get("max_idle_days", 60),
            agent_skill_cap=t.get("agent_skill_cap"),
            days=days,
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
    ollama_model: str = "gemma3:12b"
    ollama_api_key: str = ""
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
        ollama_model=env.get("OLLAMA_MODEL", "gemma3:12b"),
        ollama_api_key=env.get("OLLAMA_API_KEY", ""),
        send_delay_seconds=float(env.get("SEND_DELAY_SECONDS", "20")),
        slack_bot_token=env.get("SLACK_BOT_TOKEN", ""),
        slack_channel_id=env.get("SLACK_CHANNEL_ID", ""),
        alert_chat_id=env.get("ALERT_CHAT_ID", ""),
    )
