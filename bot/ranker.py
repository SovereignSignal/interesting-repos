import json
import re
from dataclasses import dataclass
from datetime import date

from bot.ollama import chat
from bot.filters import age_days, star_velocity, VELOCITY_CEILING

_ARR_RE = re.compile(r"\[.*\]", re.S)


@dataclass(frozen=True)
class Pick:
    """One curated repo plus the curator's one-line reason it's notable."""
    repo: object
    why: str = ""


def rank_by_stars(repos: list, count: int, today: date | None = None) -> list:
    """Top by stars, but implausible-velocity outliers are pushed to the back so a
    degraded run never *leads* with a star-farm. They still appear if slots remain."""
    today = today or date.today()
    normal, outliers = [], []
    for r in repos:
        (outliers if star_velocity(r, today) > VELOCITY_CEILING else normal).append(r)
    ordered = (sorted(normal, key=lambda r: r.stars, reverse=True)
               + sorted(outliers, key=lambda r: r.stars, reverse=True))
    return ordered[:count]


def rank(repos: list, theme, today: date | None = None, ollama_host: str = "",
         ollama_model: str = "", ollama_api_key: str = "", client=None) -> list:
    """Pick the top repos for a theme; returns Picks (repo + curator's why).

    rank="llm": the model scores EVERY candidate 0-10 against the theme profile;
    deterministic code keeps only scores >= theme.min_score (LLM provides signal,
    code enforces). An empty result after a successful scoring round means a quiet
    slot — deliberately NOT the stars fallback. The fallback (top-by-stars, empty
    whys) fires only when the LLM is unavailable, errors, or replies unparseably —
    so a digest still ships when degraded.
    """
    today = today or date.today()
    if theme.rank == "llm" and ollama_host:
        try:
            scored = _rank_llm(repos, theme, ollama_host, ollama_model,
                               ollama_api_key, today=today, client=client)
            if scored is not None:
                return scored[:theme.count]
        except Exception:
            pass  # graceful degradation
    return [Pick(r) for r in rank_by_stars(repos, theme.count, today)]


def _parse_scores(text: str) -> list | None:
    """Extract [(index, score, why), ...] from the model reply, tolerating code
    fences, prose, and junk entries. None = unparseable (caller falls back)."""
    match = _ARR_RE.search(text)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except Exception:
        return None
    if not isinstance(data, list):
        return None
    out = []
    for e in data:
        if not isinstance(e, dict):
            continue
        i, score = e.get("i"), e.get("score")
        if isinstance(i, int) and isinstance(score, (int, float)):
            out.append((i, float(score), str(e.get("why") or "")))
    # a non-empty array where nothing parsed is a malformed reply, not "none qualify"
    return out if out or not data else None


def _describe_age(repo, today: date) -> str:
    created = age_days(repo.created_at, today)
    pushed = age_days(repo.pushed_at, today)
    created_s = f"{created}d old" if created is not None else "age unknown"
    pushed_s = f"pushed {pushed}d ago" if pushed is not None else "push unknown"
    return f"{created_s}, {pushed_s}, {round(star_velocity(repo, today))}★/day"


def _rank_llm(repos: list, theme, host: str, model: str, api_key: str,
              today: date | None = None, client=None) -> list | None:
    today = today or date.today()
    lines = []
    for i, r in enumerate(repos):
        topics = ", ".join(r.topics)
        lines.append(f"{i}. {r.full_name} (★{r.stars}, {_describe_age(r, today)}) "
                     f"— {r.description} [topics: {topics}]")
    listing = "\n".join(lines)

    criteria = theme.profile or (
        "genuinely interesting, substantive, currently-trending projects a developer "
        "audience would want to know about"
    )
    prompt = (
        f'You are scoring candidates for the list "{theme.name}" for a developer audience.\n'
        f"Selection criteria: {criteria}.\n"
        "Score EVERY candidate 0-10 for how interesting and substantive it is for this list:\n"
        "  0-3: spam, keyword-stuffed or scammy repos; joke or low-effort repos; 'awesome-*' "
        "link lists and curated-list repos; repos whose star count looks artificially inflated "
        "(very high ★/day) or that lean on hype.\n"
        "  4-5: legitimate but unremarkable — tutorials, thin wrappers, me-too projects.\n"
        "  6-7: solid, genuinely useful or interesting projects.\n"
        "  8-10: exceptional — novel, substantive, clearly worth a developer's attention.\n"
        "Prefer repos that are fresh and actively maintained (recently pushed); discount "
        "stale repos.\n"
        "For each candidate also give one short reason (max 20 words) for the score — what's "
        "novel, who it's for, or its momentum.\n\n"
        f"Candidates (index. owner/name (stars, age, velocity) — description [topics]):\n{listing}\n\n"
        'Return ONLY a JSON array with one object per candidate, like: '
        '[{"i": 0, "score": 8, "why": "first open-source X with Y"}]'
    )
    text = chat(prompt, host=host, model=model, api_key=api_key, client=client)
    parsed = _parse_scores(text)
    if parsed is None:
        return None
    picks, seen = [], set()
    for i, score, why in sorted(parsed, key=lambda t: -t[1]):
        if 0 <= i < len(repos) and i not in seen and score >= theme.min_score:
            seen.add(i)
            picks.append(Pick(repos[i], why))
    return picks
