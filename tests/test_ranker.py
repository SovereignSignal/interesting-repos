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


import json
from bot.ranker import _rank_llm

class FakeMessages:
    def __init__(self, payload): self._payload = payload
    def create(self, **kwargs):
        payload = self._payload
        class Msg:  # mimic anthropic response.content[0].text
            content = [type("B", (), {"text": json.dumps(payload)})()]
        return Msg()

class FakeAnthropic:
    def __init__(self, payload): self.messages = FakeMessages(payload)

def test_rank_llm_orders_by_returned_indices():
    repos = [R(1, 10), R(2, 20), R(3, 30)]
    theme = _theme("llm", count=2)
    client = FakeAnthropic([2, 0])          # pick repo id 3, then id 1
    out = _rank_llm(repos, theme, "key", client=client)
    assert [r.id for r in out] == [3, 1]

def test_rank_llm_ignores_out_of_range_indices():
    repos = [R(1, 10), R(2, 20)]
    client = FakeAnthropic([5, 1, 99])      # only index 1 is valid
    out = _rank_llm(repos, _theme("llm"), "key", client=client)
    assert [r.id for r in out] == [2]
