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


def test_build_messages_single_message_has_header_title_meta_and_desc():
    repos = [R(1, "owner/widgets", "https://x/1", "Cool tool", 1500, "Rust")]
    msgs = build_messages(_theme(), repos, describe=lambda r: "", titles=["Cool Widgets"])
    m = msgs[0]
    assert "🔥 <b>Top Stars</b>" in m
    assert '<a href="https://x/1"><b>Cool Widgets</b></a>' in m   # clean title is the link
    assert "⭐ 1,500 · Rust · owner/widgets" in m                  # path demoted to meta
    assert "Cool tool" in m


def test_build_messages_defaults_title_to_full_name():
    repos = [R(1, "a/b", "https://x/1", "d", 5, "")]
    m = build_messages(_theme(), repos, describe=lambda r: "")[0]
    assert '<a href="https://x/1"><b>a/b</b></a>' in m
    assert "⭐ 5 · a/b" in m   # no language separator when blank


def test_build_messages_uses_describe_when_description_blank():
    repos = [R(1, "a/b", "u", "", 10, "")]
    msgs = build_messages(_theme(), repos, describe=lambda r: "README line", titles=["T"])
    assert "README line" in msgs[0]


def test_build_messages_escapes_html_in_title_and_description():
    repos = [R(1, "a/b", "u", "uses <script> & stuff", 1, "C++")]
    m = build_messages(_theme(), repos, describe=lambda r: "", titles=["T<x>"])[0]
    assert "&lt;script&gt; &amp; stuff" in m
    assert "<script>" not in m
    assert "T&lt;x&gt;" in m   # title is escaped too


def test_build_messages_applies_translate_to_description():
    repos = [R(1, "a/b", "https://x/1", "微信账单", 10, "")]
    msgs = build_messages(_theme(), repos, describe=lambda r: "",
                          translate=lambda s: "WeChat bill" if s == "微信账单" else s,
                          titles=["T"])
    assert "WeChat bill" in msgs[0]
    assert "微信账单" not in msgs[0]


def test_build_messages_splits_over_limit():
    repos = [R(i, f"a/{i}", "u", "x" * 1000, i, "Go") for i in range(10)]
    titles = [f"T{i}" for i in range(10)]
    msgs = build_messages(_theme(), repos, describe=lambda r: "", titles=titles)
    assert len(msgs) > 1
    assert all(len(m) <= TELEGRAM_LIMIT for m in msgs)
