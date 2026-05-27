from dataclasses import dataclass

import httpx

from bot.titles import make_titles, _prettify


@dataclass(frozen=True)
class R:
    full_name: str
    description: str = ""


def _content_client(content):
    return httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={"message": {"content": content}})))


def test_prettify_titlecases_and_keeps_acronyms():
    assert _prettify("google-gemini/gemini-cli") == "Gemini CLI"
    assert _prettify("foo/my_cool_tool") == "My Cool Tool"


def test_make_titles_without_host_uses_prettify():
    assert make_titles([R("google-gemini/gemini-cli")], host="") == ["Gemini CLI"]


def test_make_titles_uses_model_output():
    repos = [R("a/b"), R("c/d")]
    out = make_titles(repos, host="http://x", model="m", api_key="k",
                      client=_content_client('["Title One", "Title Two"]'))
    assert out == ["Title One", "Title Two"]


def test_make_titles_falls_back_on_length_mismatch():
    repos = [R("google-gemini/gemini-cli"), R("c/d")]
    out = make_titles(repos, host="http://x", model="m",
                      client=_content_client('["only one"]'))
    assert out == ["Gemini CLI", "D"]


def test_make_titles_falls_back_on_garbage():
    out = make_titles([R("a/b-tool")], host="http://x", model="m",
                      client=_content_client("no json here"))
    assert out == ["B Tool"]
