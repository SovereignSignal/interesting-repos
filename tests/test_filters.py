from datetime import date
from pathlib import Path
from types import SimpleNamespace
import json

from bot.filters import age_days, star_velocity


def test_age_days_counts_whole_days():
    assert age_days("2026-06-01T00:00:00Z", date(2026, 6, 4)) == 3


def test_age_days_returns_none_on_blank_or_garbage():
    assert age_days("", date(2026, 6, 4)) is None
    assert age_days("not-a-date", date(2026, 6, 4)) is None


def test_star_velocity_is_stars_per_day():
    repo = SimpleNamespace(stars=300, created_at="2026-06-01T00:00:00Z")
    assert star_velocity(repo, date(2026, 6, 4)) == 100.0


def test_star_velocity_floors_age_at_one_day():
    fresh = SimpleNamespace(stars=500, created_at="2026-06-04T00:00:00Z")  # 0 days old
    assert star_velocity(fresh, date(2026, 6, 4)) == 500.0
    unknown = SimpleNamespace(stars=500, created_at="")
    assert star_velocity(unknown, date(2026, 6, 4)) == 500.0


from bot.github import Repo
from bot.filters import is_keyword_stuffed, is_awesome_list, is_stale, clean


def _repo(full_name="acme/widget", description="A useful widget toolkit",
          topics=None, stars=100, created_at="", pushed_at=""):
    return Repo(1, full_name, "u", description, stars, "Py", topics or [],
                False, False, created_at, pushed_at)


def test_is_keyword_stuffed_flags_repeated_token():
    assert is_keyword_stuffed(_repo(description="hyperliquid sdk | " * 6)) is True


def test_is_keyword_stuffed_passes_normal_description():
    assert is_keyword_stuffed(_repo(description="A fast quantitative trading framework")) is False


def test_is_awesome_list_flags_by_name_and_topic():
    assert is_awesome_list(_repo(full_name="VoltAgent/awesome-design-md")) is True
    assert is_awesome_list(_repo(topics=["awesome-list", "design"])) is True
    assert is_awesome_list(_repo(full_name="acme/widget", topics=["ui"])) is False


def test_is_stale_uses_max_idle_days_boundary():
    today = date(2026, 6, 4)
    assert is_stale(_repo(pushed_at="2026-04-04T00:00:00Z"), today, 60) is True   # 61 days
    assert is_stale(_repo(pushed_at="2026-04-05T00:00:00Z"), today, 60) is False  # 60 days
    assert is_stale(_repo(pushed_at=""), today, 60) is False                      # unknown -> kept


def test_clean_drops_noise_and_stale_preserving_order():
    today = date(2026, 6, 4)
    good = _repo(full_name="acme/good", description="solid tool", pushed_at="2026-06-01T00:00:00Z")
    spam = _repo(full_name="x/spam", description="buy buy buy buy buy buy now")
    awesome = _repo(full_name="x/awesome-lists", description="a list")
    stale = _repo(full_name="x/stale", description="old", pushed_at="2026-01-01T00:00:00Z")
    out = clean([good, spam, awesome, stale], today, 60)
    assert [r.full_name for r in out] == ["acme/good"]


from bot.filters import is_agent_skill_pack, is_ai_repo, cap_agent_skills, cap_ai


def test_is_agent_skill_pack_flags_collections():
    assert is_agent_skill_pack(_repo(full_name="himself65/finance-skills",
                                     description="A collection of skills for AI analysis")) is True
    assert is_agent_skill_pack(_repo(full_name="x/y",
                                     description="754 structured cybersecurity skills for AI agents")) is True
    assert is_agent_skill_pack(_repo(full_name="x/y", topics=["agent-skills"])) is True
    assert is_agent_skill_pack(_repo(full_name="Leonxlnx/taste-skill", description="gives your AI taste")) is True


def test_is_agent_skill_pack_passes_real_tools():
    assert is_agent_skill_pack(_repo(full_name="NVIDIA/NemoClaw",
                                     description="Run agents like Hermes more securely")) is False
    assert is_agent_skill_pack(_repo(full_name="t8y2/dbx",
                                     description="lightweight cross-platform database client")) is False


def test_is_ai_repo_flags_ai_and_packs():
    assert is_ai_repo(_repo(full_name="garrytan/gstack",
                            description="Use Garry Tan's exact Claude Code setup")) is True
    assert is_ai_repo(_repo(full_name="x/finance-skills", description="collection of skills for ai")) is True
    assert is_ai_repo(_repo(full_name="z/tool", topics=["ai-agents"], description="orchestration")) is True


def test_is_ai_repo_passes_non_ai():
    assert is_ai_repo(_repo(full_name="chenglou/pretext",
                            description="Fast, accurate & comprehensive text measurement & layout")) is False
    assert is_ai_repo(_repo(full_name="NawfalMotii79/PLFM_RADAR",
                            description="Open-source 10.5 GHz PLFM phased array RADAR system")) is False
    assert is_ai_repo(_repo(full_name="t8y2/dbx", description="cross-platform database client")) is False


def test_is_ai_repo_owner_ai_token_and_description_false_positives():
    """MiniMax-H3-shaped leak (empty metadata, AI only in the org name) plus the
    guards: 'ai' inside description words, and 'openai' as a single token."""
    cases = json.loads((Path(__file__).parent / "data" / "ai_leaks.json").read_text())
    for case in cases:
        repo = _repo(full_name=case["full_name"], description=case["description"],
                     topics=case["topics"])
        assert is_ai_repo(repo) is case["ai"], case["why"]


def test_cap_agent_skills_zero_drops_minimax_h3_shaped_leak():
    leak = _repo(full_name="MiniMax-AI/MiniMax-H3", description="", topics=[])
    keep = _repo(full_name="vorssaint/vorssaint-utils", description="macOS menu bar toolkit")
    assert cap_agent_skills([leak, keep], 0) == [keep]


def test_cap_agent_skills_none_is_passthrough():
    repos = [_repo(full_name="a/x-skills", description="skills for ai"),
             _repo(full_name="b/db", description="database")]
    assert cap_agent_skills(repos, None) == repos


def test_cap_agent_skills_zero_drops_all_ai():
    ai = _repo(full_name="a/gstack", description="Claude Code setup")
    pack = _repo(full_name="b/x-skills", description="skills for claude code")
    clean_repo = _repo(full_name="c/pretext", description="text measurement and layout")
    assert cap_agent_skills([ai, pack, clean_repo], 0) == [clean_repo]


def test_is_ai_repo_cursor_is_word_not_precursor():
    assert is_ai_repo(_repo(full_name="lab/precursor",
                            description="a precursor to the new compiler")) is False
    assert is_ai_repo(_repo(full_name="acme/ide-bridge",
                            description="a cursor agent for the editor")) is True


def test_is_ai_repo_empty_metadata_readme_is_ai():
    bare = _repo(full_name="acme/untitled", description="", topics=[])
    assert is_ai_repo(bare) is False
    assert is_ai_repo(bare, readme="A Claude Code skill for writing prompts") is True
    # populated metadata: readme must not flip a real non-AI tool
    real = _repo(full_name="acme/db", description="a database engine", topics=["database"])
    assert is_ai_repo(real, readme="mentions an llm in passing") is False


def test_cap_ai_none_is_passthrough():
    repos = [_repo(full_name="a/gstack", description="Claude Code setup"),
             _repo(full_name="b/db", description="database")]
    assert cap_ai(repos, None) == repos


def test_cap_ai_zero_drops_all_ai():
    ai = _repo(full_name="a/gstack", description="Claude Code setup")
    pack = _repo(full_name="b/x-skills", description="skills for claude code")
    clean_repo = _repo(full_name="c/pretext", description="text measurement and layout")
    assert cap_ai([ai, pack, clean_repo], 0) == [clean_repo]


def test_cap_ai_n_keeps_non_ai_and_at_most_n_ai():
    a1 = _repo(full_name="a/one", description="Claude Code setup")
    a2 = _repo(full_name="b/two", topics=["ai-agents"], description="orchestration")
    a3 = _repo(full_name="c/three", description="an llm gateway")
    tool = _repo(full_name="d/db", description="a database engine")
    out = cap_ai([a1, tool, a2, a3], 2)
    assert out == [a1, tool, a2]   # two AI + the non-AI; a3 dropped


def test_cap_agent_skills_n_limits_packs_keeps_tools():
    p1 = _repo(full_name="a/one-skills", description="skills for agents")
    tool = _repo(full_name="b/nemo", topics=["ai-agents"], description="run agents")  # AI tool, not a pack
    p2 = _repo(full_name="c/two-skills", description="skill pack")
    p3 = _repo(full_name="d/three-skills", description="agent skill collection")
    out = cap_agent_skills([p1, tool, p2, p3], 2)
    assert out == [p1, tool, p2]   # all non-packs kept + at most 2 packs; p3 dropped
