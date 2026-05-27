import re
import tomllib
from dataclasses import dataclass
from datetime import date, timedelta

_SINCE_RE = re.compile(r"\{since:(\d+)d\}")


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


def load_themes(path: str) -> list[Theme]:
    with open(path, "rb") as f:
        data = tomllib.load(f)
    themes: list[Theme] = []
    for t in data.get("theme", []):
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
        ))
    return themes
