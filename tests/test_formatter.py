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
    license: str = ""


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
    # Blurbs are capped at 160 characters, so the split needs more items
    # than it did when a description could be 1000 characters.
    repos = [R(i, f"a/{i}", "u", "x" * 1000, i, "Go") for i in range(30)]
    titles = [f"T{i}" for i in range(30)]
    msgs = build_messages(_theme(), repos, describe=lambda r: "", titles=titles)
    assert len(msgs) > 1
    assert all(len(m) <= TELEGRAM_LIMIT for m in msgs)


def test_build_messages_uses_summary_when_present():
    repos = [R(1, "a/b", "u", "raw description", 10, "Go")]
    m = build_messages(_theme(), repos, describe=lambda r: "", titles=["T"],
                       summaries=["A clean blurb."])[0]
    assert "A clean blurb." in m and "raw description" not in m


def test_build_messages_falls_back_when_summary_none():
    repos = [R(1, "a/b", "u", "raw description", 10, "Go")]
    m = build_messages(_theme(), repos, describe=lambda r: "", titles=["T"],
                       summaries=[None])[0]
    assert "raw description" in m


def test_build_messages_uses_the_description_when_the_blurb_is_a_fragment():
    # A cut that cannot make a clause falls through to the GitHub description,
    # which is trimmed by the same rules. A fragment is not what gets posted.
    repos = [R(1, "a/b", "u", "A maintained scanner that proves findings.", 10, "Go")]
    fragment = "Go, " + ("scanner examines images inside isolated runners " * 6)
    m = build_messages(_theme(), repos, describe=lambda r: "", titles=["T"],
                       summaries=[fragment])[0]
    assert "A maintained scanner that proves findings." in m
    assert "scanner examines images inside" not in m


def test_build_messages_rejects_a_language_claim_and_uses_the_readme_line():
    # #366: "SlotDrift is a Rust tool…" while the metadata line says Python.
    # The repo's own description also names a Rust engine, so the README line
    # (no language claim) is what ships.
    repos = [R(1, "propavingk/SlotDrift", "https://github.com/propavingk/SlotDrift",
               "Slot continuity and fork analysis, with an independent Rust engine.",
               64, "Python", license="MIT")]
    summary = ("SlotDrift is a Rust tool that analyzes captured Solana slot records "
               "to identify gaps, skips, duplicate slots, and orphaned branches. "
               "It is notable as an independent observability tool.")
    m = build_messages(
        _theme(), repos, titles=["SlotDrift"], summaries=[summary],
        describe=lambda r: "Slot continuity and fork analysis for captured Solana slot records.",
    )[0]
    assert "Rust" not in m
    assert "notable" not in m.lower()
    assert "Slot continuity and fork analysis for captured Solana slot records." in m


from bot.formatter import _format_delta


def test_format_delta_compact_above_thousand():
    assert _format_delta(1234) == "+1.2k★ this week"
    assert _format_delta(1000) == "+1.0k★ this week"
    assert _format_delta(12345) == "+12.3k★ this week"


def test_format_delta_plain_below_thousand():
    assert _format_delta(999) == "+999★ this week"
    assert _format_delta(1) == "+1★ this week"


def test_format_delta_none_for_non_growth():
    assert _format_delta(0) is None
    assert _format_delta(-5) is None


def test_format_delta_labels_the_trending_window():
    assert _format_delta(627, "daily") == "+627★ today"
    assert _format_delta(14507, "weekly") == "+14.5k★ this week"
    assert _format_delta(2000, "monthly") == "+2.0k★ this month"


def test_build_messages_daily_delta_says_today():
    repos = [R(1, "a/b", "https://x/1", "d", 1500, "Rust")]
    m = build_messages(_theme(), repos, describe=lambda r: "", titles=["T"],
                       deltas=[627], delta_periods=["daily"])[0]
    assert "+627★ today" in m
    assert "this week" not in m


def test_build_messages_deltas_none_is_identical_to_today():
    repos = [R(1, "a/b", "u", "d", 1500, "Rust")]
    plain = build_messages(_theme(), repos, describe=lambda r: "", titles=["T"])[0]
    no_deltas = build_messages(_theme(), repos, describe=lambda r: "",
                               titles=["T"], deltas=None)[0]
    assert plain == no_deltas


def test_build_messages_shows_growth_annotation_when_delta_present():
    repos = [R(1, "a/b", "https://x/1", "d", 1500, "Rust")]
    m = build_messages(_theme(), repos, describe=lambda r: "", titles=["T"],
                       deltas=[1234])[0]
    assert "⭐ 1,500 · +1.2k★ this week · Rust · a/b" in m


def test_build_messages_none_delta_entry_is_unannotated():
    repos = [R(1, "a/b", "u", "d", 10, "Go")]
    m = build_messages(_theme(), repos, describe=lambda r: "", titles=["T"],
                       deltas=[None])[0]
    assert "this week" not in m
    assert "⭐ 10 · Go · a/b" in m


from bot.formatter import _format_momentum


def test_format_momentum_rounds_and_annotates():
    assert _format_momentum(38.4) == "38★/day"
    assert _format_momentum(1234.5) == "1,234★/day"


def test_format_momentum_none_when_unknown_or_below_one():
    assert _format_momentum(None) is None
    assert _format_momentum(0.4) is None


def test_build_messages_shows_momentum_badge_when_provided():
    repos = [R(1, "a/b", "https://x/1", "d", 1500, "Rust")]
    m = build_messages(_theme(), repos, describe=lambda r: "", titles=["T"],
                       momenta=[38.4])[0]
    assert "⭐ 1,500 · 38★/day · Rust · a/b" in m


def test_build_messages_omits_momentum_when_none_or_below_one():
    repos = [R(1, "a/b", "u", "d", 10, "Go")]
    low = build_messages(_theme(), repos, describe=lambda r: "", titles=["T"], momenta=[0.4])[0]
    none = build_messages(_theme(), repos, describe=lambda r: "", titles=["T"], momenta=[None])[0]
    assert "★/day" not in low and "★/day" not in none
    assert "⭐ 10 · Go · a/b" in low


def test_build_messages_shows_license_when_present():
    repos = [R(1, "a/b", "https://x/1", "d", 1500, "Rust", license="MIT")]
    m = build_messages(_theme(), repos, describe=lambda r: "", titles=["T"])[0]
    assert "⭐ 1,500 · MIT · Rust · a/b" in m


def test_build_messages_orders_momentum_then_growth_then_license():
    repos = [R(1, "a/b", "https://x/1", "d", 1500, "Rust", license="MIT")]
    m = build_messages(_theme(), repos, describe=lambda r: "", titles=["T"],
                       deltas=[1234], momenta=[38.4])[0]
    assert "⭐ 1,500 · 38★/day · +1.2k★ this week · MIT · Rust · a/b" in m
