"""Theme routing for GitNova and Trending hits. No network."""
from bot.candidates import (
    blocked_by_ceiling, gitnova_for_theme, matches_theme, trending_for_theme,
)
from bot.config import Theme
from bot.gitnova import GitNovaHit
from bot.trending import TrendingHit


def _movers():
    return Theme(key="movers", name="Movers", emoji="", query="created:>{since:120d} stars:>500",
                 delta_days=7, github_trending=("daily", "weekly"))


def _agents():
    return Theme(key="ai-agents", name="AI", emoji="",
                 query=("topic:ai-agents created:>{since:90d}", "topic:llm created:>{since:90d}"))


def _systems():
    return Theme(key="systems", name="Systems", emoji="",
                 query=("language:Zig created:>{since:180d} stars:>10",
                        "topic:compiler created:>{since:180d}"),
                 max_stars=5000, max_stars_exempt_days=180)


def test_gitnova_movers_keeps_breakout_and_reserves_early_signal():
    hits = [
        GitNovaHit(f"a/{i}", stage="breakout", stars_today=1000 - i, category="apps")
        for i in range(10)
    ]
    hits.append(GitNovaHit("small/early", stage="early", stars_today=12, magnitude=6.4,
                           summary="A tiny spec."))
    hits.append(GitNovaHit("farm/stars", stage="breakout", stars_today=9000, trust="inflated"))
    hits.append(GitNovaHit("quiet/steady", stage="steady", stars_today=800, summary="A database engine."))
    chosen = gitnova_for_theme(hits, _movers())
    names = [h.full_name for h in chosen]
    assert "farm/stars" not in names
    assert "quiet/steady" not in names
    assert "small/early" in names
    assert len(chosen) <= 12


def test_gitnova_routes_categories_and_not_unrelated_themes():
    rea = GitNovaHit("morluto/rea", summary="AI agents inspect binaries.", category="ai_agents",
                     language="TypeScript", stars_today=3293, stage="breakout")
    media = GitNovaHit("storytold/artcraft", summary="An image editor.", category="gen_media",
                       stars_today=100, stage="breakout")
    tool = GitNovaHit("pingdotgg/ts-rust", summary="A Rust compiler port.", category="devtools",
                      language="Rust", stars_today=127, stage="early")
    assert [h.full_name for h in gitnova_for_theme([rea, media, tool], _agents())] == [
        "morluto/rea", "storytold/artcraft",
    ]
    robotics = Theme(key="robotics", name="Robotics", emoji="", query="topic:robotics stars:>10")
    assert gitnova_for_theme([rea], robotics) == []
    assert matches_theme(_systems(), language="Zig", full_name="a/z", description="")
    assert matches_theme(_systems(), description="a compiler for toys", full_name="a/c")


def test_trending_movers_keeps_the_window_and_themes_match_language():
    hits = [
        TrendingHit("morluto/rea", 4666, "daily", language="TypeScript"),
        TrendingHit("acme/zigc", 40, "weekly", language="Zig", description="a compiler"),
        TrendingHit("acme/fork", 10, "daily", is_fork=True),
    ]
    movers = [h.full_name for h in trending_for_theme(hits, _movers(), extra=True)]
    assert movers == ["morluto/rea", "acme/zigc"]
    systems = trending_for_theme(hits, _systems(), extra=True)
    assert [h.full_name for h in systems] == ["acme/zigc"]
    assert trending_for_theme(hits, _systems(), extra=False) == []


def test_blocked_by_ceiling_keeps_unknown_age_and_young_repos():
    theme = _systems()
    assert blocked_by_ceiling(9000, 400, theme) is True
    assert blocked_by_ceiling(9000, 30, theme) is False
    assert blocked_by_ceiling(9000, None, theme) is False
    assert blocked_by_ceiling(100, 4000, theme) is False
