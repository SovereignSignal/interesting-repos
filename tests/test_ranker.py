from dataclasses import dataclass, field
from datetime import date

import httpx

from bot.ranker import rank, rank_by_stars, _rank_llm
from bot.config import Theme


@dataclass(frozen=True)
class R:
    id: int
    stars: int
    full_name: str = "a/b"
    description: str = "d"
    topics: list = field(default_factory=list)
    created_at: str = ""
    pushed_at: str = ""


def _theme(rank_mode="stars", count=2):
    return Theme(key="t", name="T", emoji="", query="q", rank=rank_mode, count=count)


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def _content_client(content):
    return _client(lambda request: httpx.Response(200, json={"message": {"content": content}}))


def test_rank_by_stars_sorts_desc_and_limits():
    repos = [R(1, 10), R(2, 99), R(3, 50)]
    assert [r.id for r in rank_by_stars(repos, 2)] == [2, 3]


def test_rank_stars_mode_uses_stars():
    repos = [R(1, 10), R(2, 99)]
    assert [r.id for r in rank(repos, _theme("stars"))] == [2, 1]


def test_rank_llm_without_host_falls_back_to_stars():
    repos = [R(1, 10), R(2, 99)]
    assert [r.id for r in rank(repos, _theme("llm"), ollama_host="")] == [2, 1]


def test_rank_llm_error_falls_back_to_stars():
    repos = [R(1, 10), R(2, 99)]
    client = _client(lambda request: httpx.Response(500))
    out = rank(repos, _theme("llm"), ollama_host="http://x", ollama_model="m", client=client)
    assert [r.id for r in out] == [2, 1]


def test_rank_llm_orders_by_returned_indices():
    repos = [R(1, 10), R(2, 20), R(3, 30)]
    out = _rank_llm(repos, _theme("llm", 2), "http://x", "m", "k", client=_content_client("[2, 0]"))
    assert [r.id for r in out] == [3, 1]


def test_rank_llm_tolerates_fences_and_prose():
    repos = [R(1, 10), R(2, 20)]
    out = _rank_llm(repos, _theme("llm"), "http://x", "m", "k",
                    client=_content_client("Sure! Here you go:\n```json\n[1]\n```"))
    assert [r.id for r in out] == [2]


def test_rank_llm_ignores_out_of_range_indices():
    repos = [R(1, 10), R(2, 20)]
    out = _rank_llm(repos, _theme("llm"), "http://x", "m", "k", client=_content_client("[5, 1, 99]"))
    assert [r.id for r in out] == [2]


def test_rank_by_stars_deprioritizes_velocity_outliers():
    today = date(2026, 6, 4)
    farm = R(1, 200000, created_at="2026-06-02T00:00:00Z")   # ~100000 stars/day
    real = R(2, 300, created_at="2026-05-01T00:00:00Z")      # ~9 stars/day
    # count=1 must skip the farm even though it has far more stars
    assert [r.id for r in rank_by_stars([farm, real], 1, today)] == [2]
    # count=2 keeps both, but the farm is last
    assert [r.id for r in rank_by_stars([farm, real], 2, today)] == [2, 1]


def test_rank_llm_listing_includes_age_and_velocity():
    today = date(2026, 6, 4)
    captured = {}
    def handler(request):
        import json as _json
        captured["prompt"] = _json.loads(request.content)["messages"][0]["content"]
        return httpx.Response(200, json={"message": {"content": "[0]"}})
    repos = [R(1, 500, created_at="2026-06-01T00:00:00Z", pushed_at="2026-06-03T00:00:00Z")]
    _rank_llm(repos, _theme("llm", 1), "http://x", "m", "k", today=today, client=_client(handler))
    assert "3d old" in captured["prompt"]         # created 2026-06-01 -> 3 days
    assert "pushed 1d ago" in captured["prompt"]   # pushed 2026-06-03 -> 1 day
    assert "★/day" in captured["prompt"]


def _prompt_for(cap):
    captured = {}
    def handler(request):
        import json as _json
        captured["p"] = _json.loads(request.content)["messages"][0]["content"]
        return httpx.Response(200, json={"message": {"content": "[0]"}})
    theme = Theme(key="t", name="T", emoji="", query="q", rank="llm", count=7,
                  agent_skill_cap=cap)
    _rank_llm([R(1, 10, created_at="2026-06-01T00:00:00Z")], theme, "http://x", "m", "k",
              today=date(2026, 6, 4), client=_client(handler))
    return captured["p"]


def test_prompt_excludes_agent_skills_when_cap_zero():
    assert "Do NOT select ANY generic AI-agent/skill packs" in _prompt_for(0)


def test_prompt_caps_agent_skills_when_cap_set():
    assert "AT MOST 2 generic AI-agent/skill packs" in _prompt_for(2)


def test_prompt_has_no_diversity_directive_when_cap_none():
    p = _prompt_for(None)
    assert "AT MOST" not in p
    assert "Do NOT select" not in p
    assert "Prefer a DIVERSE set" not in p   # the old soft sentence is removed
