import json
import logging
from dataclasses import dataclass
from datetime import date

from bot.ollama import chat_result
from bot.filters import age_days, star_velocity, VELOCITY_CEILING

log = logging.getLogger("bot")


@dataclass(frozen=True)
class Pick:
    """One curated repo, the curator's one-line reason, and the score when the
    model actually scored it. Stars fallback leaves ``score`` empty."""
    repo: object
    why: str = ""
    score: float | None = None


class RankResult(list):
    """Picks from ``rank``. ``fallback_reason`` is set only when llm scoring
    failed and stars were used. A quiet slot is empty, with reason None."""

    def __init__(self, picks=(), fallback_reason: str | None = None):
        super().__init__(picks)
        self.fallback_reason = fallback_reason


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
    slot — deliberately NOT the stars fallback, and not a failure. The fallback
    (top-by-stars, empty whys) fires only when the LLM is unavailable, errors, or
    replies unparseably — so a digest still ships when degraded. That path logs a
    WARNING naming the reason and sets ``RankResult.fallback_reason`` so the run
    can alert. Host unset (LLM disabled) is stars with no reason and no warning.
    """
    today = today or date.today()
    if theme.rank == "llm" and ollama_host:
        try:
            scored, reason = _rank_llm(repos, theme, ollama_host, ollama_model,
                                       ollama_api_key, today=today, client=client)
        except Exception as exc:
            # type name only — exception text can carry the request URL
            scored, reason = None, type(exc).__name__
        if scored is not None:
            return RankResult(scored[:theme.count])
        reason = reason or "unparseable"
        log.warning("theme %s: LLM scoring failed (%s); falling back to stars",
                    theme.key, reason)
        return RankResult(
            (Pick(r) for r in rank_by_stars(repos, theme.count, today)),
            fallback_reason=reason,
        )
    return RankResult(Pick(r) for r in rank_by_stars(repos, theme.count, today))


def _json_arrays(text: str):
    """Balanced ``[...]`` slices, left to right, respecting JSON strings.

    A greedy ``\\[.*\\]`` latches onto an earlier bracket (prose, a quoted
    ``[topics]`` line) and then ``json.loads`` rejects the whole reply.
    """
    n = len(text)
    i = 0
    while i < n:
        if text[i] != "[":
            i += 1
            continue
        end = _closing_bracket(text, i)
        if end is None:
            i += 1
            continue
        yield text[i:end + 1]
        i += 1


def _closing_bracket(text: str, start: int) -> int | None:
    depth = 0
    in_str = False
    esc = False
    for j in range(start, len(text)):
        c = text[j]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                return j
    return None


def _score_rows(data) -> list | None:
    """Score tuples from one JSON value. ``[]`` if the array is empty, None if
    it isn't a list of ``{i, score}`` objects (caller tries the next array)."""
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


def _parse_scores(text: str) -> list | None:
    """Extract [(index, score, why), ...] from the model reply, tolerating code
    fences, prose, and junk entries. None = unparseable (caller falls back).

    Prefers the first array that actually contains ``{i, score}`` objects, so
    an earlier ``[topics: ...]`` bracket does not swallow the JSON.
    """
    if not text:
        return None
    saw_empty = False
    for raw in _json_arrays(text):
        try:
            data = json.loads(raw)
        except Exception:
            continue
        rows = _score_rows(data)
        if rows:
            return rows
        if rows == []:
            saw_empty = True
    return [] if saw_empty else None


def _describe_age(repo, today: date) -> str:
    created = age_days(repo.created_at, today)
    pushed = age_days(repo.pushed_at, today)
    created_s = f"{created}d old" if created is not None else "age unknown"
    pushed_s = f"pushed {pushed}d ago" if pushed is not None else "push unknown"
    return f"{created_s}, {pushed_s}, {round(star_velocity(repo, today))}★/day"


def _rank_llm(repos: list, theme, host: str, model: str, api_key: str,
              today: date | None = None, client=None) -> tuple[list | None, str | None]:
    today = today or date.today()
    lines = []
    for i, r in enumerate(repos):
        topics = ", ".join(r.topics)
        # Facts the curator can weigh: stars/age/velocity plus engagement (forks) and
        # provenance (license, org- vs user-owned) — signal only, never a hard rule
        # (deterministic code still enforces the bar; see rank()'s docstring).
        facts = [f"★{r.stars}", _describe_age(r, today)]
        forks = getattr(r, "forks", 0)
        if forks:
            facts.append(f"{forks} forks")
        lic = getattr(r, "license", "")
        if lic:
            facts.append(lic)
        owner_type = getattr(r, "owner_type", "")
        if owner_type:
            facts.append("org-owned" if owner_type == "Organization" else "user-owned")
        lines.append(f"{i}. {r.full_name} ({', '.join(facts)}) "
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
        f"Candidates (index. owner/name (stars, age, velocity, forks, license, owner type) "
        f"— description [topics]):\n{listing}\n\n"
        'Return ONLY a JSON array with one object per candidate, like: '
        '[{"i": 0, "score": 8, "why": "first open-source X with Y"}]'
    )
    # think=False: Gemma 4's default thinking path returns 200 with empty content.
    text, fail = chat_result(prompt, host=host, model=model, api_key=api_key,
                             client=client, think=False)
    if fail:
        return None, fail
    parsed = _parse_scores(text)
    if parsed is None:
        return None, "unparseable"
    picks, seen = [], set()
    for i, score, why in sorted(parsed, key=lambda t: -t[1]):
        if 0 <= i < len(repos) and i not in seen and score >= theme.min_score:
            seen.add(i)
            picks.append(Pick(repos[i], why, float(score)))
    return picks, None
