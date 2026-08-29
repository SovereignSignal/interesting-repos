import time
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
    created_at: str = ""
    pushed_at: str = ""
    forks: int = 0
    license: str = ""       # SPDX id (e.g. "MIT"); "" when none/unrecognized
    owner_type: str = ""    # "User" or "Organization" — solo vs org-backed signal


def _spdx(item: dict) -> str:
    """The repo's SPDX license id, or "" when unlicensed/unrecognized. GitHub uses
    the sentinel "NOASSERTION" for a license it can't map — treat that as none."""
    spdx = ((item.get("license") or {}).get("spdx_id")) or ""
    return "" if spdx == "NOASSERTION" else spdx


def parse_repo(item: dict) -> Repo:
    return Repo(
        id=item["id"],
        full_name=item["full_name"],
        html_url=item["html_url"],
        description=item.get("description") or "",
        stars=item.get("stargazers_count", 0),
        language=item.get("language") or "",
        topics=list(item.get("topics") or []),
        is_fork=bool(item.get("fork", False)),
        is_archived=bool(item.get("archived", False)),
        created_at=item.get("created_at") or "",
        pushed_at=item.get("pushed_at") or "",
        forks=item.get("forks_count", 0),
        license=_spdx(item),
        owner_type=(item.get("owner") or {}).get("type") or "",
    )


def _retry_wait(resp: httpx.Response | None, attempt: int) -> float:
    """Seconds to wait: honor Retry-After when present, else 2**attempt."""
    raw = (resp.headers.get("Retry-After") if resp is not None else None) or ""
    try:
        return max(float(raw), 0.0)
    except (TypeError, ValueError):
        return float(2 ** attempt)


def search_repos(query: str, sort: str = "stars", order: str = "desc",
                 token: str = "", per_page: int = 100, page: int = 1,
                 client: httpx.Client | None = None, retries: int = 3,
                 sleep=time.sleep) -> list[Repo]:
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    params = {"q": query, "sort": sort, "order": order,
              "per_page": per_page, "page": page}
    owns_client = client is None
    client = client or httpx.Client(timeout=30)
    last_exc: Exception | None = None
    try:
        for attempt in range(retries):
            try:
                resp = client.get(f"{_API}/search/repositories", params=params,
                                  headers=headers)
                if resp.status_code in (403, 429) or resp.status_code >= 500:
                    last_exc = httpx.HTTPStatusError(
                        f"GitHub search HTTP {resp.status_code}",
                        request=resp.request, response=resp)
                    if attempt < retries - 1:
                        sleep(_retry_wait(resp, attempt))
                        continue
                    resp.raise_for_status()
                resp.raise_for_status()
                return [parse_repo(item) for item in resp.json().get("items", [])]
            except httpx.HTTPError as exc:
                last_exc = exc
                if attempt < retries - 1:
                    sleep(_retry_wait(getattr(exc, "response", None), attempt))
                    continue
                raise
        raise last_exc or RuntimeError("GitHub search failed")
    finally:
        if owns_client:
            client.close()


def _is_noise(line: str) -> bool:
    s = line.strip()
    if not s:
        return True
    if s.startswith("#"):             # markdown heading
        return True
    if set(s) <= set("=-*_> "):       # heading underline / rule / blockquote marker
        return True
    if s.startswith(("![", "[![", "<")):  # badge/image/html
        return True
    return False


def _readme_raw(full_name: str, token: str = "",
                client: httpx.Client | None = None) -> str:
    """Raw README markdown, or "" on any HTTP error. One GET."""
    headers = {"Accept": "application/vnd.github.raw+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    owns_client = client is None
    client = client or httpx.Client(timeout=30)
    try:
        resp = client.get(f"{_API}/repos/{full_name}/readme", headers=headers)
        resp.raise_for_status()
        return resp.text
    except httpx.HTTPError:
        return ""
    finally:
        if owns_client:
            client.close()


def first_line_from(text: str) -> str:
    for raw in text.splitlines():
        if not _is_noise(raw):
            return raw.strip()[:200]
    return ""


def excerpt_from(text: str, max_chars: int = 600) -> str:
    parts: list[str] = []
    total = 0
    for raw in text.splitlines():
        if _is_noise(raw):
            continue
        line = raw.strip()
        parts.append(line)
        total += len(line) + 1
        if total >= max_chars:
            break
    return " ".join(parts)[:max_chars].strip()


def readme_first_line(full_name: str, token: str = "",
                      client: httpx.Client | None = None) -> str:
    return first_line_from(_readme_raw(full_name, token=token, client=client))


def readme_excerpt(full_name: str, token: str = "",
                   client: httpx.Client | None = None, max_chars: int = 600) -> str:
    """The first ~max_chars of real README prose (skipping headings/badges/rules),
    joined into one line — context for the LLM summarizer. "" on any HTTP error."""
    return excerpt_from(_readme_raw(full_name, token=token, client=client),
                        max_chars=max_chars)


def readme_parts(full_name: str, token: str = "",
                 client: httpx.Client | None = None,
                 max_chars: int = 600) -> tuple[str, str]:
    """(first_line, excerpt) from a single README fetch."""
    raw = _readme_raw(full_name, token=token, client=client)
    return first_line_from(raw), excerpt_from(raw, max_chars=max_chars)
