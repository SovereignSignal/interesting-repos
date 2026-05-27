from dataclasses import dataclass, field
from bot.ranker import rank, rank_by_stars
from bot.config import Theme


@dataclass(frozen=True)
class R:
    id: int
    stars: int
    full_name: str = "a/b"
    description: str = "d"
    topics: list = field(default_factory=list)


def _theme(rank_mode="stars", count=2):
    return Theme(key="t", name="T", emoji="", query="q", rank=rank_mode, count=count)

def test_rank_by_stars_sorts_desc_and_limits():
    repos = [R(1, 10), R(2, 99), R(3, 50)]
    assert [r.id for r in rank_by_stars(repos, 2)] == [2, 3]

def test_rank_stars_mode_uses_stars():
    repos = [R(1, 10), R(2, 99)]
    assert [r.id for r in rank(repos, _theme("stars"))] == [2, 1]

def test_rank_llm_without_key_falls_back_to_stars():
    repos = [R(1, 10), R(2, 99)]
    assert [r.id for r in rank(repos, _theme("llm"), anthropic_api_key="")] == [2, 1]

def test_rank_llm_error_falls_back_to_stars():
    class BoomClient:
        def __getattr__(self, _):
            raise RuntimeError("boom")
    repos = [R(1, 10), R(2, 99)]
    out = rank(repos, _theme("llm"), anthropic_api_key="key", client=BoomClient())
    assert [r.id for r in out] == [2, 1]
