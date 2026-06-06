from dataclasses import dataclass

import httpx

from bot.summaries import make_summaries


@dataclass(frozen=True)
class R:
    full_name: str = "a/b"
    description: str = "d"


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def _content_client(content):
    return _client(lambda request: httpx.Response(200, json={"message": {"content": content}}))


def test_make_summaries_returns_blurbs_in_order():
    repos = [R("a/one"), R("b/two")]
    out = make_summaries(repos, ["ex1", "ex2"], host="http://x", model="m",
                         client=_content_client('["Blurb one.", "Blurb two."]'))
    assert out == ["Blurb one.", "Blurb two."]


def test_make_summaries_without_host_returns_nones():
    assert make_summaries([R(), R()], host="") == [None, None]


def test_make_summaries_on_error_returns_nones():
    out = make_summaries([R(), R()], host="http://x", model="m",
                         client=_client(lambda request: httpx.Response(500)))
    assert out == [None, None]


def test_make_summaries_length_mismatch_returns_nones():
    out = make_summaries([R(), R(), R()], host="http://x", model="m",
                         client=_content_client('["only one"]'))
    assert out == [None, None, None]


def test_make_summaries_tolerates_fences_and_prose():
    out = make_summaries([R("a/x")], host="http://x", model="m",
                         client=_content_client('Sure:\n```json\n["Clean blurb."]\n```'))
    assert out == ["Clean blurb."]


def test_make_summaries_blank_blurb_becomes_none():
    out = make_summaries([R("a/x"), R("b/y")], host="http://x", model="m",
                         client=_content_client('["", "Real blurb."]'))
    assert out == [None, "Real blurb."]
