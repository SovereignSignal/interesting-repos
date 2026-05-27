from dataclasses import dataclass

import httpx

_API = "https://api.github.com"


@dataclass(frozen=True)
class Repo:
    id: int
    full_name: str
    html_url: str
    description: str
    stars: int
    language: str
    topics: list[str]
    is_fork: bool
    is_archived: bool


def parse_repo(item: dict) -> Repo:
    return Repo(
        id=item["id"],
        full_name=item["full_name"],
        html_url=item["html_url"],
        description=item.get("description") or "",
        stars=item.get("stargazers_count", 0),
        language=item.get("language") or "",
        topics=item.get("topics") or [],
        is_fork=bool(item.get("fork", False)),
        is_archived=bool(item.get("archived", False)),
    )


def search_repos(query: str, sort: str = "stars", order: str = "desc",
                 token: str = "", per_page: int = 50,
                 client: httpx.Client | None = None) -> list[Repo]:
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    params = {"q": query, "sort": sort, "order": order, "per_page": per_page}
    owns_client = client is None
    client = client or httpx.Client(timeout=30)
    try:
        resp = client.get(f"{_API}/search/repositories", params=params, headers=headers)
        resp.raise_for_status()
        return [parse_repo(item) for item in resp.json().get("items", [])]
    finally:
        if owns_client:
            client.close()
