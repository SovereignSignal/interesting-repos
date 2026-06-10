from dataclasses import dataclass, field
from datetime import date

import httpx

from bot.ranker import rank, rank_by_stars, _rank_llm, Pick
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


def test_rank_stars_mode_returns_picks():
    repos = [R(1, 10), R(2, 99)]
    out = rank(repos, _theme("stars"))
    assert [p.repo.id for p in out] == [2, 1] and all(p.why == "" for p in out)


def test_rank_llm_without_host_falls_back_to_stars():
    repos = [R(1, 10), R(2, 99)]
    assert [p.repo.id for p in rank(repos, _theme("llm"), ollama_host="")] == [2, 1]


def test_rank_llm_error_falls_back_to_stars():
    repos = [R(1, 10), R(2, 99)]
    client = _client(lambda request: httpx.Response(500))
    out = rank(repos, _theme("llm"), ollama_host="http://x", ollama_model="m", client=client)
    assert [p.repo.id for p in out] == [2, 1]
    assert all(p.why == "" for p in out)   # stars fallback carries no why


def test_rank_llm_unparseable_reply_falls_back_to_stars():
    repos = [R(1, 10), R(2, 99)]
    out = rank(repos, _theme("llm"), ollama_host="http://x", ollama_model="m",
               client=_content_client("I cannot rank these, sorry."))
    assert [p.repo.id for p in out] == [2, 1]


def test_rank_llm_keeps_only_scores_above_bar_ordered_by_score():
    repos = [R(1, 10), R(2, 20), R(3, 30)]
    reply = ('[{"i": 0, "score": 9, "why": "novel"}, {"i": 1, "score": 3, "why": "spam"}, '
             '{"i": 2, "score": 7, "why": "solid"}]')
    out = _rank_llm(repos, _theme("llm", 5), "http://x", "m", "k",
                    client=_content_client(reply))
    assert [(p.repo.id, p.why) for p in out] == [(1, "novel"), (3, "solid")]  # 3-scorer gated out


def test_rank_llm_all_below_bar_returns_empty_not_fallback():
    repos = [R(1, 10), R(2, 99)]
    reply = '[{"i": 0, "score": 4, "why": "meh"}, {"i": 1, "score": 5, "why": "thin"}]'
    out = rank(repos, _theme("llm"), ollama_host="http://x", ollama_model="m",
               client=_content_client(reply))
    assert out == []   # quiet slot — deliberately NOT the stars fallback


def test_rank_llm_respects_theme_min_score():
    theme = Theme(key="t", name="T", emoji="", query="q", rank="llm", count=5, min_score=8)
    repos = [R(1, 10), R(2, 20)]
    reply = '[{"i": 0, "score": 9, "why": "great"}, {"i": 1, "score": 7, "why": "good"}]'
    out = _rank_llm(repos, theme, "http://x", "m", "k", client=_content_client(reply))
    assert [p.repo.id for p in out] == [1]


def test_rank_llm_tolerates_fences_prose_and_junk_entries():
    repos = [R(1, 10), R(2, 20)]
    reply = ('Sure! Here:\n```json\n[{"i": 1, "score": 8, "why": "good"}, '
             '{"i": 99, "score": 9, "why": "oob"}, {"score": 9}, "junk", '
             '{"i": 1, "score": 8, "why": "dupe"}]\n```')
    out = _rank_llm(repos, _theme("llm", 5), "http://x", "m", "k",
                    client=_content_client(reply))
    assert [p.repo.id for p in out] == [2]   # out-of-range, malformed, and dupes dropped


def test_rank_caps_picks_at_theme_count():
    repos = [R(1, 1), R(2, 2), R(3, 3)]
    reply = ('[{"i": 0, "score": 7, "why": "a"}, {"i": 1, "score": 8, "why": "b"}, '
             '{"i": 2, "score": 9, "why": "c"}]')
    out = rank(repos, _theme("llm", 2), ollama_host="http://x", ollama_model="m",
               client=_content_client(reply))
    assert [p.repo.id for p in out] == [3, 2]


def test_rank_stars_mode_uses_stars():
    repos = [R(1, 10), R(2, 99)]
    out = rank(repos, _theme("stars"))
    assert [p.repo.id for p in out] == [2, 1]


def test_rank_llm_prompt_asks_for_scores_and_includes_age():
    today = date(2026, 6, 4)
    captured = {}
    def handler(request):
        import json as _json
        captured["prompt"] = _json.loads(request.content)["messages"][0]["content"]
        return httpx.Response(200, json={"message": {"content": '[{"i":0,"score":9,"why":"w"}]'}})
    repos = [R(1, 500, created_at="2026-06-01T00:00:00Z", pushed_at="2026-06-03T00:00:00Z")]
    _rank_llm(repos, _theme("llm", 1), "http://x", "m", "k", today=today, client=_client(handler))
    assert "score" in captured["prompt"].lower()
    assert "3d old" in captured["prompt"] and "pushed 1d ago" in captured["prompt"]
    assert "★/day" in captured["prompt"]


def test_rank_by_stars_deprioritizes_velocity_outliers():
    today = date(2026, 6, 4)
    farm = R(1, 200000, created_at="2026-06-02T00:00:00Z")   # ~100000 stars/day
    real = R(2, 300, created_at="2026-05-01T00:00:00Z")      # ~9 stars/day
    # count=1 must skip the farm even though it has far more stars
    assert [r.id for r in rank_by_stars([farm, real], 1, today)] == [2]
    # count=2 keeps both, but the farm is last
    assert [r.id for r in rank_by_stars([farm, real], 2, today)] == [2, 1]


def _prompt_for(cap):
    captured = {}
    def handler(request):
        import json as _json
        captured["p"] = _json.loads(request.content)["messages"][0]["content"]
        return httpx.Response(200, json={"message": {"content": '[{"i":0,"score":9,"why":"w"}]'}})
    theme = Theme(key="t", name="T", emoji="", query="q", rank="llm", count=7,
                  agent_skill_cap=cap)
    _rank_llm([R(1, 10, created_at="2026-06-01T00:00:00Z")], theme, "http://x", "m", "k",
              today=date(2026, 6, 4), client=_client(handler))
    return captured["p"]


def test_curation_prompt_has_no_cap_directive():
    # v3 enforcement is deterministic (filters.cap_agent_skills); the curation prompt
    # carries no agent-skill cap directive for any cap value.
    for cap in (None, 0, 2):
        p = _prompt_for(cap)
        assert "AT MOST" not in p
        assert "Do NOT select" not in p
        assert "NON-AI highlight" not in p
        assert "Prefer a DIVERSE set" not in p   # the old soft sentence is removed
