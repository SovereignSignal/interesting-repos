"""Deterministic digest titles.

The public title is the repository's own name. A README H1 replaces it only
when that heading is clearly the project's name: short, not a sentence, and
either the same name as the repo (ignoring punctuation), a heading whose
initials are the repo name (``sbcl`` ← "Steel Bank Common Lisp"), or a
single word on an owner-named repo (``ram0verflow/ram0verflow`` ← ``ROFL``).
A model never invents a title.
"""

import re

_ACRONYMS = {
    "ai", "cli", "api", "sdk", "ui", "ux", "css", "html", "js", "ts", "ml", "llm",
    "os", "db", "gpu", "cpu", "rag", "mcp", "tui", "p2p", "sql", "http", "io", "3d",
}

# Whole-heading boilerplate. Compared with letters and digits only.
_H1_DENY = {
    "readme", "introduction", "overview", "about", "home", "documentation",
    "docs", "index", "welcome", "changelog", "license", "contributing",
    "tableofcontents", "toc", "features", "installation", "install", "usage",
    "examples", "example", "demo", "screenshots", "screenshot", "roadmap",
    "support", "faq", "building", "build", "tests", "testing", "quickstart",
    "quick start", "motivation", "background", "summary", "abstract", "note",
    "notes", "warning", "requirements", "prerequisites", "gettingstarted",
}


def _prettify(full_name: str) -> str:
    """Title-case a hyphenated or underscored repo name. Acronyms stay upper."""
    base = full_name.split("/")[-1]
    words = [w for w in re.split(r"[-_.\s]+", base) if w]
    if not words:
        return base
    return " ".join(w.upper() if w.lower() in _ACRONYMS else w[:1].upper() + w[1:]
                    for w in words)


def repo_name_title(full_name: str) -> str:
    """The repo's own name. Separator-free names keep their casing (``AnyJev``,
    ``e2e``, ``next.js``). Hyphens and underscores are spaced and title-cased."""
    name = full_name.split("/")[-1]
    if re.search(r"[-_]", name):
        return _prettify(full_name)
    return name


def _alnum(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _tokens(text: str) -> list[str]:
    return [t for t in re.split(r"[^0-9A-Za-z]+", text.lower()) if len(t) >= 2]


def _initials(heading: str) -> str:
    words = [w for w in re.split(r"[^0-9A-Za-z]+", heading) if w]
    return "".join(w[0] for w in words).lower()


def heading_is_project_name(heading: str, full_name: str) -> bool:
    """True when ``heading`` is the project's name, not a tagline or template."""
    h1 = (heading or "").strip()
    if not h1 or len(h1) > 40:
        return False
    words = h1.split()
    if not 1 <= len(words) <= 4:
        return False
    # "osu!web" is a name. "Hello." and "Done!" are sentences.
    if re.search(r"[.?](?:\s|$)", h1) or h1.endswith("!"):
        return False
    if _alnum(h1) in {_alnum(item) for item in _H1_DENY}:
        return False
    owner, _, name = full_name.partition("/")
    if not name:
        return False
    if _alnum(h1) and _alnum(h1) == _alnum(name):
        return True
    if len(words) >= 2 and _initials(h1) == _alnum(name) and len(_alnum(name)) >= 2:
        return True
    ht = _tokens(h1)
    nt = set(_tokens(name))
    if ht and nt:
        covered = sum(len(t) for t in ht if t in nt)
        total = sum(len(t) for t in ht)
        # Strict majority, so "Ivan Bohun Website" does not ride on the name.
        if total and covered * 4 >= total * 3:
            return True
    # Owner-named repos (ram0verflow/ram0verflow) have no name to overlap.
    # Trust a single-word H1 there; a phrase is not evidence.
    opaque = name.lower() == owner.lower()
    return bool(opaque and len(words) == 1 and 2 <= len(h1) <= 24)


def title_for(full_name: str, heading: str = "") -> str:
    if heading and heading_is_project_name(heading, full_name):
        return heading.strip()
    return repo_name_title(full_name)


def make_titles(repos, headings=None, **_ignored) -> list[str]:
    """One title per repo, from the repo name or a qualifying README H1.

    ``headings`` is aligned with ``repos`` (the README's level-1 heading, or
    ""). Host, model, and client arguments are ignored: titles are not asked
    of a model, including when a host is configured.
    """
    n = len(repos)
    if not headings or len(headings) != n:
        headings = [""] * n
    return [title_for(r.full_name, h or "") for r, h in zip(repos, headings)]
