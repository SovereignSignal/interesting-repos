from bot.github import parse_repo, Repo

ITEM = {
    "id": 42,
    "full_name": "acme/widgets",
    "html_url": "https://github.com/acme/widgets",
    "description": "A widget toolkit",
    "stargazers_count": 1234,
    "language": "Python",
    "topics": ["widgets", "ui"],
    "fork": False,
    "archived": False,
}

def test_parse_repo_maps_fields():
    r = parse_repo(ITEM)
    assert r == Repo(42, "acme/widgets", "https://github.com/acme/widgets",
                     "A widget toolkit", 1234, "Python", ["widgets", "ui"], False, False)

def test_parse_repo_handles_nulls():
    r = parse_repo({"id": 1, "full_name": "a/b", "html_url": "u",
                    "description": None, "language": None})
    assert r.description == "" and r.language == "" and r.stars == 0
    assert r.topics == [] and r.is_fork is False and r.is_archived is False


import httpx
from bot.github import search_repos

def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))

def test_search_repos_sends_query_sort_order_and_parses_items():
    captured = {}
    def handler(request):
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, json={"items": [ITEM]})
    repos = search_repos("created:>2026-05-19", sort="stars", order="desc",
                         token="gh", per_page=50, client=_client(handler))
    assert len(repos) == 1 and repos[0].full_name == "acme/widgets"
    assert "q=created" in captured["url"]
    assert "sort=stars" in captured["url"] and "order=desc" in captured["url"]
    assert "per_page=50" in captured["url"]
    assert captured["auth"] == "Bearer gh"

def test_search_repos_omits_auth_without_token():
    def handler(request):
        assert request.headers.get("Authorization") is None
        return httpx.Response(200, json={"items": []})
    assert search_repos("x", sort="stars", order="desc", client=_client(handler)) == []
