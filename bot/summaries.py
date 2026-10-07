"""Public blurbs: one short sentence, consistent with the repo's facts.

The curator ``why`` is accepted and deliberately not sent to the model. Those
lines are scoring notes ("inflated", "low traction") and were leaking into the
channel. Code then drops anything the model still phrases that way, plus any
sentence that names a different implementation language than the metadata.
"""

import json
import re

from bot.ollama import chat

_ARR_RE = re.compile(r"\[.*\]", re.S)

# One sentence, about this long. The formatter applies the same cap to
# fallback descriptions so a post cannot grow back to a two-sentence wall.
BLURB_CHAR_LIMIT = 160
# The writer is asked to finish under this, short of the hard cap, so a
# sentence can end before anything has to be cut.
BLURB_PROMPT_LIMIT = 150
# A cut shorter than this is a fragment. Callers then try the repo's own
# description (trimmed the same way) and otherwise omit the blurb.
BLURB_MIN_CHARS = 40
# Post #389 shipped unfinished blurbs of 153 and 156 characters
# ("…session viewing", "…sandboxed"). An unfinished blurb this close to the
# cap was stopped by the limit, same as one that ran past it.
_UNFINISHED_CLOSE_AT = BLURB_CHAR_LIMIT - 20

# Never leave a blurb on one of these. They are the tail of a phrase that
# was cut off ("with sandboxed", "and session").
_DANGLING_WORDS = frozenset({
    "a", "an", "the", "of", "with", "to", "for", "and", "or", "by",
    "in", "on", "from", "via", "using",
})
# Clause boundary inside the cap: comma, semicolon, a spaced dash, or one
# of the clause words. A hyphen inside "Apache-2.0" or "harness-portable"
# is not a boundary.
_CLAUSE_RE = re.compile(
    r",|;|—|–| - |\s+(?:and|with|that|which)\b",
    re.I,
)

_NOTABLE_RE = re.compile(r"\b(?:notabl\w*|stands\s+out)\b", re.I)
_JUDGMENT_RE = re.compile(
    r"\b(?:inflated|low[\s-]traction|early[\s-]stage|small but dedicated|"
    r"dedicated user base|limited recent updates|star growth)\b",
    re.I,
)
# Sentence end is "." or "?" followed by a space or the end. "!" is not a
# boundary: "osu!" shows up inside names. "v1.2" is not one either, because
# that dot is followed by a digit rather than a space. "22. It" is a boundary
# even though the dot follows a digit.
_SENTENCE_END = re.compile(r"^.+?[.?](?=\s|$)")

_LANG_CANON = {
    "python": "python", "py": "python",
    "rust": "rust", "rs": "rust",
    "go": "go", "golang": "go",
    "javascript": "javascript", "js": "javascript",
    "typescript": "typescript", "ts": "typescript",
    "java": "java",
    "c++": "c++", "cpp": "c++",
    "c": "c",
    "c#": "c#", "csharp": "c#", "cs": "c#",
    "ruby": "ruby",
    "php": "php",
    "swift": "swift",
    "kotlin": "kotlin",
    "zig": "zig",
    "haskell": "haskell",
    "scala": "scala",
    "elixir": "elixir",
    "dart": "dart",
    "lua": "lua",
    "perl": "perl",
    "r": "r",
    "shell": "shell", "bash": "shell",
    "powershell": "powershell",
}
# "is a Rust tool" / "written in Python" / "is a Rust-based …".
_LANG_CLAIM = re.compile(
    r"\b(?:written|built|implemented|coded)\s+in\s+([A-Za-z0-9#+.]+)"
    r"|\bis\s+an?\s+([A-Za-z0-9#+.]+)-based\b"
    r"|\bis\s+an?\s+([A-Za-z0-9#+.]+)(?:-based)?\s+"
    r"(?:tool|library|framework|compiler|runtime|app|application|package|"
    r"cli|sdk|program|project|implementation|port|language|crate)\b",
    re.I,
)
# "Rust engine" / "Python library" — a language used as the implementation,
# not a passing mention ("PHP inclusion", "Rust-based tooling").
_LANG_NOUN = re.compile(
    r"\b([A-Za-z0-9#+.]+)(?:-based)?\s+"
    r"(?:tool|library|framework|compiler|runtime|engine|app|application|"
    r"implementation|port|crate|sdk|cli)\b",
    re.I,
)
_SPDX = (
    "MIT", "Apache-2.0", "GPL-3.0", "GPL-2.0", "AGPL-3.0", "LGPL-3.0",
    "LGPL-2.1", "BSD-3-Clause", "BSD-2-Clause", "MPL-2.0", "Unlicense",
    "ISC", "Zlib", "CC-BY-4.0", "CC0-1.0", "BSL-1.0", "PostgreSQL",
)


def _canon_lang(token: str) -> str | None:
    return _LANG_CANON.get((token or "").strip().lower())


def _claimed_languages(text: str) -> list[str]:
    found = []
    for match in _LANG_CLAIM.finditer(text):
        token = next(group for group in match.groups() if group)
        canon = _canon_lang(token)
        if canon and canon not in found:
            found.append(canon)
    return found


def _noun_languages(text: str) -> list[str]:
    found = []
    for match in _LANG_NOUN.finditer(text):
        canon = _canon_lang(match.group(1))
        if canon and canon not in found:
            found.append(canon)
    return found


def language_contradiction(text: str, repo) -> bool:
    """True when the blurb says the project is implemented in another language.

    An identity claim ("is a Rust tool", "written in Rust", "Rust engine")
    fails even if the metadata language is also named. A passing mention
    ("PHP inclusion" in a Python PoC, "Rust-based tooling" on a JavaScript
    framework, "Python bindings" on a Rust library) does not.
    """
    repo_lang = _canon_lang(getattr(repo, "language", "") or "")
    if not repo_lang:
        return False
    named = _claimed_languages(text) + _noun_languages(text)
    return any(lang != repo_lang for lang in named)


def license_contradiction(text: str, repo) -> bool:
    """True when the blurb names an SPDX id and not the repo's license."""
    repo_lic = (getattr(repo, "license", "") or "").lower()
    if not repo_lic:
        return False
    found = [spdx.lower() for spdx in _SPDX
             if re.search(rf"\b{re.escape(spdx)}\b", text, re.I)]
    if not found:
        return False
    return repo_lic not in found


def _strip_slug_opener(text: str, repo) -> str:
    """``ppy/osu-web is …`` becomes ``osu-web is …``. The owner/name slug is
    already on the metadata line."""
    full = getattr(repo, "full_name", "") or ""
    if not full or not text.lower().startswith(full.lower()):
        return text
    rest = text[len(full):].lstrip(" \t-:—–")
    name = full.split("/")[-1]
    if rest[:2].lower() == "is" and (len(rest) == 2 or not rest[2].isalnum()):
        return f"{name} {rest}".strip()
    return rest or text


def _first_sentence(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    match = _SENTENCE_END.match(text)
    return match.group(0).strip() if match else text


def _normalize_blurb(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    # A trailing ellipsis is a cut, not a sentence end.
    return re.sub(r"(?:\.\.\.|…)+\s*$", "", text).rstrip()


def _is_finished(text: str) -> bool:
    return bool(re.search(r"[.?!]$", text))


def _last_word(text: str) -> str:
    core = re.sub(r"[.?!]+$", "", text).strip()
    if not core:
        return ""
    return core.split()[-1].strip(".,;:!?").lower()


def _ends_dangling(text: str) -> bool:
    return _last_word(text) in _DANGLING_WORDS


def _strip_dangling(text: str) -> str:
    core = re.sub(r"[.?!]+$", "", text).strip()
    words = core.split()
    while words and words[-1].strip(".,;:!?").lower() in _DANGLING_WORDS:
        words.pop()
    return " ".join(words)


def _fit_window(text: str, limit: int) -> str:
    """The prefix that must fit, without ending in the middle of a word."""
    if len(text) <= limit:
        return text
    window = text[:limit]
    nxt = text[limit]
    if window and not window[-1].isspace() and not nxt.isspace():
        space = window.rfind(" ")
        if space > 0:
            window = window[:space]
    return window.rstrip()


def _last_sentence_in(window: str) -> str | None:
    last = None
    for match in re.finditer(r"[.?](?=\s|$)", window):
        # "..." is an ellipsis, and "v1.2" never matches (the dot is followed
        # by a digit). "22. It" does.
        if window[match.start()] == "." and match.start() >= 1 and window[match.start() - 1] == ".":
            continue
        candidate = window[:match.end()].rstrip()
        if candidate.endswith("...") or candidate.endswith("…"):
            continue
        last = candidate
    return last


# Words after the last comma / "with" / "and". Shorter than this, the tail is
# the writer running out of room ("sandboxed", "session viewing").
_REMNANT_WORDS = 3


def _last_clause(window: str) -> tuple[str, str] | None:
    """Prefix before the last clause boundary, and the tail after it."""
    best = None
    for match in _CLAUSE_RE.finditer(window):
        if match.start() > 0:
            best = match
    if best is None:
        return None
    prefix = window[:best.start()].rstrip()
    if not prefix:
        return None
    return prefix, window[best.end():].strip()


def _close(prefix: str, limit: int) -> str | None:
    """Drop a dangling tail and end on a period, inside the cap.

    None when that would be under ``BLURB_MIN_CHARS``: a fragment, which the
    caller replaces with the repo description or leaves blank.
    """
    cut = _strip_dangling(prefix)
    cut = re.sub(r"[\s,;:]+$", "", cut).rstrip()
    if not cut:
        return None
    if not _is_finished(cut):
        while cut and len(cut) + 1 > limit:
            if " " not in cut:
                cut = cut[:limit - 1].rstrip()
                break
            cut = _strip_dangling(cut.rsplit(" ", 1)[0])
            cut = re.sub(r"[\s,;:]+$", "", cut).rstrip()
        if cut and not _is_finished(cut):
            cut += "."
    if (not cut or len(cut) < BLURB_MIN_CHARS or len(cut) > limit
            or _ends_dangling(cut) or cut.endswith("...") or cut.endswith("…")):
        return None
    return cut


def _close_within(text: str, limit: int) -> str | None:
    window = _fit_window(text, limit)
    sentence = _last_sentence_in(window)
    if (sentence and BLURB_MIN_CHARS <= len(sentence) <= limit
            and not _ends_dangling(sentence)):
        return sentence
    clause = _last_clause(window)
    if clause is not None:
        prefix, tail = clause
        # Past the cap, the rest does not fit. Under the cap, only a short
        # tail is a remnant; a real final clause stays and just gains a period.
        if len(text) > limit or len(tail.split()) <= _REMNANT_WORDS:
            return _close(prefix, limit)
    return _close(window, limit)


def _clip(text: str, limit: int) -> str | None:
    text = _normalize_blurb(text)
    if not text:
        return None
    if len(text) <= limit and _is_finished(text) and not _ends_dangling(text):
        return text
    # Over the cap, or unfinished and close enough that the writer stopped
    # mid-phrase to stay under it. Either way, end on a sentence or clause.
    if len(text) > limit or (not _is_finished(text) and len(text) >= _UNFINISHED_CLOSE_AT):
        return _close_within(text, limit)
    if not _is_finished(text) and _ends_dangling(text):
        text = _strip_dangling(text)
        if len(text) < BLURB_MIN_CHARS:
            return None
        return text if _is_finished(text) else text + "."
    return text


def prepare_public_blurb(text: str, repo) -> str | None:
    """One public sentence, or None when the text should not be posted.

    None means: notable/stands-out, a curator judgment, a language or
    license that contradicts the repo, or a cut that would be a fragment
    under ``BLURB_MIN_CHARS``. Callers fall back to the repo's own
    description, then the README line, and omit the blurb when every source
    fails. A sentence longer than ``BLURB_CHAR_LIMIT`` — or an unfinished one
    pushed up against that cap — is closed on the last sentence or clause
    inside the limit, with a period, never on a dangling word.
    """
    text = _strip_slug_opener((text or "").strip(), repo)
    if not text:
        return None
    sentence = _first_sentence(text)
    if _NOTABLE_RE.search(sentence) or _JUDGMENT_RE.search(sentence):
        return None
    if language_contradiction(sentence, repo) or license_contradiction(sentence, repo):
        return None
    return _clip(sentence, BLURB_CHAR_LIMIT)


def make_summaries(repos, excerpts=None, whys=None, host: str = "", model: str = "",
                   api_key: str = "", client=None) -> list:
    """One short factual sentence per repo, or None when that repo has no
    usable blurb (callers fall back to the repo's own description).

    ``whys`` is the curator's private score rationale. It is not placed in the
    prompt: public copy was repeating "inflated", "low traction", and
    "small but dedicated user base" from those notes. No host / chat error /
    bad JSON / length mismatch => all None, so the no-LLM digest is unchanged.
    """
    n = len(repos)
    if not host or not repos:
        return [None] * n
    # Accepted so the call site can keep passing the curator notes. Unused
    # on purpose — see the docstring.
    del whys
    excerpts = excerpts or [""] * n
    listing = "\n".join(
        f"{i}. {r.full_name} (language: {getattr(r, 'language', '') or 'unknown'}, "
        f"stars: {getattr(r, 'stars', 0)}, "
        f"license: {getattr(r, 'license', '') or 'unknown'}) "
        f"— {r.description}  [README: {ex}]"
        for i, (r, ex) in enumerate(zip(repos, excerpts))
    )
    prompt = (
        "For each GitHub repository below, write ONE complete factual sentence of "
        f"at most {BLURB_PROMPT_LIMIT} characters in plain English: what it does, "
        "and why a developer would look at it now. Use only the description and "
        "README excerpt.\n"
        "Rules:\n"
        "- One complete sentence that ends with a period. Stay under "
        f"{BLURB_PROMPT_LIMIT} characters so the sentence finishes; do not stop "
        "mid-phrase.\n"
        "- Do not add a second sentence.\n"
        "- Do not use the word \"notable\" or the phrase \"stands out\".\n"
        "- Do not start with the owner/repo slug.\n"
        "- The parenthetical facts (language, stars, license) are authoritative. "
        "Do not name a different implementation language or a different license.\n"
        "- Do not mention traction, user-base size, maturity, early-stage status, "
        "inflated or artificial star growth, or any scoring judgment.\n"
        "- No marketing, no emoji, no hype.\n"
        "Return ONLY a JSON array of strings, one per repo, in the same order.\n\n"
        f"{listing}"
    )
    text = chat(prompt, host=host, model=model, api_key=api_key, client=client,
                think=False)
    match = _ARR_RE.search(text)
    if not match:
        return [None] * n
    try:
        blurbs = json.loads(match.group(0))
    except Exception:
        return [None] * n
    if not isinstance(blurbs, list) or len(blurbs) != n:
        return [None] * n
    out = []
    for blurb, repo in zip(blurbs, repos):
        cleaned = prepare_public_blurb(str(blurb).strip(), repo) if str(blurb).strip() else None
        out.append(cleaned)
    return out
