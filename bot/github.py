import logging
import re
import time
from dataclasses import dataclass

import httpx

log = logging.getLogger("bot.github")

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


# Last Search X-RateLimit-Remaining observed in this process. Reset per run so a
# previous invocation cannot leak a stale sample. None until a response carries
# a numeric header — callers log it only then, and never log the token.
_rate_remaining: int | None = None


def reset_github_rate_remaining() -> None:
    global _rate_remaining
    _rate_remaining = None


def github_rate_remaining() -> int | None:
    return _rate_remaining


def _header_int(resp: httpx.Response, name: str) -> int | None:
    raw = resp.headers.get(name)
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _note_rate_remaining(resp: httpx.Response) -> None:
    """Record the Search bucket only. Callers of the core REST API must not
    use this — core and search are separate quotas, and source_health reports
    the Search sample."""
    global _rate_remaining
    value = _header_int(resp, "X-RateLimit-Remaining")
    if value is not None:
        _rate_remaining = value


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
                _note_rate_remaining(resp)
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


# owner/repo. Rejects anything that would change the request path.
_REPO_NAME = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
# Leave this much core-API budget for the README fetches that follow hydration.
_HYDRATE_MIN_REMAINING = 10


def _rate_limited(resp: httpx.Response) -> bool:
    if resp.status_code == 429:
        return True
    if resp.status_code != 403:
        return False
    if _header_int(resp, "X-RateLimit-Remaining") == 0:
        return True
    if resp.headers.get("Retry-After"):
        return True
    try:
        body = resp.text.lower()
    except Exception:
        return False
    return "rate limit" in body or "secondary rate" in body


def _fetch_repo(client: httpx.Client, name: str, headers: dict, retries: int,
                sleep) -> tuple[Repo | None, int | None, bool]:
    """GET one repo. Returns ``(repo, remaining, abort_batch)``."""
    for attempt in range(retries):
        try:
            resp = client.get(f"{_API}/repos/{name}", headers=headers)
        except httpx.HTTPError:
            if attempt < retries - 1:
                sleep(float(2 ** attempt))
                continue
            return None, None, True
        remaining = _header_int(resp, "X-RateLimit-Remaining")
        if resp.status_code == 200:
            try:
                return parse_repo(resp.json()), remaining, False
            except (KeyError, TypeError, ValueError):
                log.warning("trending hydrate %s: unusable repo payload", name)
                return None, remaining, False
        if resp.status_code == 404:
            return None, remaining, False
        limited = _rate_limited(resp)
        if limited or resp.status_code >= 500:
            if attempt < retries - 1:
                sleep(_retry_wait(resp, attempt))
                continue
            return None, remaining, True
        return None, remaining, False
    return None, None, True


def fetch_repos(full_names: list[str], token: str = "",
                client: httpx.Client | None = None, retries: int = 3,
                sleep=time.sleep, min_remaining: int = _HYDRATE_MIN_REMAINING) -> list:
    """Hydrate ``owner/name`` strings via ``GET /repos/{owner}/{name}``.

    One result per input name; ``None`` when that repo was skipped or the
    batch stopped early. Stops when the core quota falls below
    ``min_remaining``, a rate limit persists, or the connection fails, so a
    dead API cannot stall the digest on dozens of retries. Does not touch the
    Search rate-limit sample (different bucket).
    """
    headers = {"Accept": "application/vnd.github+json",
               "User-Agent": "interesting-repos-bot"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    owns_client = client is None
    client = client or httpx.Client(timeout=30)
    out: list = []
    remaining: int | None = None
    try:
        for name in full_names:
            if remaining is not None and remaining < min_remaining:
                log.warning(
                    "trending hydrate stopped; core rate_limit_remaining=%s",
                    remaining)
                break
            if not _REPO_NAME.fullmatch(name):
                out.append(None)
                continue
            repo, remaining, abort = _fetch_repo(
                client, name, headers, retries=retries, sleep=sleep)
            out.append(repo)
            if abort:
                log.warning("trending hydrate aborted at %s", name)
                break
        if len(out) < len(full_names):
            out.extend([None] * (len(full_names) - len(out)))
        return out
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
