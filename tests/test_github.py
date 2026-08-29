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

def test_parse_repo_copies_topics_not_aliases_source():
    src = {"id": 1, "full_name": "a/b", "html_url": "u", "topics": ["x"]}
    r = parse_repo(src)
    r.topics.append("y")           # mutating the repo's list...
    assert src["topics"] == ["x"]  # ...must not corrupt the source dict


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
    assert "page=1" in captured["url"]
    assert captured["auth"] == "Bearer gh"


def test_search_repos_default_per_page_is_100_and_sends_page():
    captured = {}
    def handler(request):
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"items": []})
    search_repos("x", client=_client(handler), page=2)
    assert "per_page=100" in captured["url"]
    assert "page=2" in captured["url"]


def test_search_repos_retries_on_429_honoring_retry_after():
    calls = {"n": 0}
    slept = []
    def handler(request):
        calls["n"] += 1
        if calls["n"] < 2:
            return httpx.Response(429, headers={"Retry-After": "7"}, json={})
        return httpx.Response(200, json={"items": [ITEM]})
    repos = search_repos("x", client=_client(handler), retries=3, sleep=slept.append)
    assert len(repos) == 1 and calls["n"] == 2
    assert 7.0 in slept

def test_search_repos_omits_auth_without_token():
    def handler(request):
        assert request.headers.get("Authorization") is None
        return httpx.Response(200, json={"items": []})
    assert search_repos("x", sort="stars", order="desc", client=_client(handler)) == []


from bot.github import readme_first_line

def _readme_client(body, status=200):
    def handler(request):
        assert request.url.path.endswith("/readme")
        assert request.headers.get("Accept") == "application/vnd.github.raw+json"
        return httpx.Response(status, text=body)
    return _client(handler)

def test_readme_first_line_skips_headings_and_badges():
    body = "# Title\n\n![badge](x.svg)\n\nThe real first sentence.\n"
    assert readme_first_line("a/b", client=_readme_client(body)) == "The real first sentence."

def test_readme_first_line_truncates_to_200():
    body = "x" * 500
    assert len(readme_first_line("a/b", client=_readme_client(body))) == 200

def test_readme_first_line_returns_empty_on_error():
    assert readme_first_line("a/b", client=_readme_client("nope", status=404)) == ""


def test_parse_repo_reads_timestamps():
    r = parse_repo({**ITEM, "created_at": "2026-06-01T00:00:00Z",
                    "pushed_at": "2026-06-03T00:00:00Z"})
    assert r.created_at == "2026-06-01T00:00:00Z"
    assert r.pushed_at == "2026-06-03T00:00:00Z"


def test_parse_repo_defaults_timestamps_to_empty():
    r = parse_repo({"id": 1, "full_name": "a/b", "html_url": "u"})
    assert r.created_at == "" and r.pushed_at == ""


def test_parse_repo_reads_engagement_and_ownership_fields():
    r = parse_repo({**ITEM, "forks_count": 88, "license": {"spdx_id": "MIT"},
                    "owner": {"type": "Organization"}})
    assert r.forks == 88 and r.license == "MIT" and r.owner_type == "Organization"


def test_parse_repo_normalizes_missing_or_unrecognized_license():
    assert parse_repo({**ITEM}).license == ""                                  # no key
    assert parse_repo({**ITEM, "license": None}).license == ""                 # explicit null
    assert parse_repo({**ITEM, "license": {"spdx_id": "NOASSERTION"}}).license == ""  # sentinel
    r = parse_repo({**ITEM})
    assert r.forks == 0 and r.owner_type == ""                                 # engagement/owner default


from bot.github import readme_excerpt, readme_parts, first_line_from, excerpt_from


def test_readme_excerpt_joins_real_lines_up_to_max():
    body = "# Title\n\n![badge](x)\n\nFirst real line.\nSecond real line.\n"
    assert readme_excerpt("a/b", client=_readme_client(body)) == "First real line. Second real line."


def test_readme_excerpt_truncates_to_max_chars():
    out = readme_excerpt("a/b", client=_readme_client("word " * 500), max_chars=100)
    assert len(out) <= 100


def test_readme_excerpt_returns_empty_on_error():
    assert readme_excerpt("a/b", client=_readme_client("nope", status=404)) == ""


def test_readme_parts_is_one_fetch_first_line_and_excerpt():
    body = "# Title\n\nThe real first sentence.\nSecond line.\n"
    first, excerpt = readme_parts("a/b", client=_readme_client(body))
    assert first == "The real first sentence."
    assert excerpt == "The real first sentence. Second line."
    assert first_line_from(body) == first
    assert excerpt_from(body) == excerpt
