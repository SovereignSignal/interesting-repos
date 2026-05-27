import re
from datetime import date, timedelta

_SINCE_RE = re.compile(r"\{since:(\d+)d\}")


def expand_since(query: str, today: date) -> str:
    def repl(m: "re.Match[str]") -> str:
        days = int(m.group(1))
        return (today - timedelta(days=days)).isoformat()
    return _SINCE_RE.sub(repl, query)
