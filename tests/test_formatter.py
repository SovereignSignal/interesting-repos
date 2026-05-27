from dataclasses import dataclass, field
from bot.formatter import build_messages, TELEGRAM_LIMIT
from bot.config import Theme


@dataclass(frozen=True)
class R:
    id: int
    full_name: str
    html_url: str
    description: str
    stars: int
    language: str
    topics: list = field(default_factory=list)


def _theme():
    return Theme(key="t", name="Top Stars", emoji="🔥", query="q")

def test_build_messages_single_message_has_header_and_entries():
    repos = [R(1, "a/b", "https://x/1", "Cool tool", 1500, "Rust")]
    msgs = build_messages(_theme(), repos, describe=lambda r: "")
    assert len(msgs) == 1
    m = msgs[0]
    assert "🔥 <b>Top Stars</b>" in m
    assert "⭐ 1,500 · Rust" in m
    assert '<a href="https://x/1">a/b</a>' in m
    assert "Cool tool" in m

def test_build_messages_uses_describe_when_description_blank():
    repos = [R(1, "a/b", "u", "", 10, "")]
    msgs = build_messages(_theme(), repos, describe=lambda r: "README line")
    assert "README line" in msgs[0]
    assert " · " not in msgs[0].split("\n")[1]  # no language separator when blank

def test_build_messages_escapes_html_in_dynamic_text():
    repos = [R(1, "a/b", "u", "uses <script> & stuff", 1, "C++")]
    m = build_messages(_theme(), repos, describe=lambda r: "")[0]
    assert "&lt;script&gt; &amp; stuff" in m
    assert "<script>" not in m

def test_build_messages_splits_over_limit():
    repos = [R(i, f"a/{i}", "u", "x" * 1000, i, "Go") for i in range(10)]
    msgs = build_messages(_theme(), repos, describe=lambda r: "")
    assert len(msgs) > 1
    assert all(len(m) <= TELEGRAM_LIMIT for m in msgs)
