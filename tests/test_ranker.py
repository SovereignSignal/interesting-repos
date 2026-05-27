from dataclasses import dataclass, field

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
