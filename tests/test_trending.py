import httpx

from bot.github import Repo
from bot.trending import (
    TrendingHit, collect_trending, merge_hits, merge_trending, parse_trending_html,
    prefer_gain,
)

# Mirrors github.com/trending as of 2026-10-04: the repo link lives in <h2>,
# the period gain is plain text, and other anchors (login, avatars) are not
# the candidate. Class names are filler — the parser must not need them.
_WEEKLY = """
<article class="Box-row">
  <a href="/login?return_to=%2Fvectorize-io%2Fhindsight">Star</a>
  <a href="/sponsors/someone">sponsor</a>
  <h2 class="h3 lh-condensed">
    <a href="/vectorize-io/hindsight">
      <span class="text-normal">vectorize-io /</span> hindsight
    </a>
  </h2>
  <p class="col-9 color-fg-muted">Hindsight: Agent Memory That Learns</p>
  <a href="/vectorize-io/hindsight/stargazers"><svg></svg> 20,000</a>
  <a href="/cryppadotta">dev</a>
  <span class="d-inline-block float-sm-right">14,507 stars this week</span>
</article>
<article>
  <h2><a href="/debpalash/VoiceStudio">VoiceStudio</a></h2>
  <span>16,800 stars this week</span>
</article>
<article>
  <h2><a href="/nope/nogain">skipped</a></h2>
  <p>no period line, so this row is not a candidate</p>
</article>
"""

_DAILY = """
<article>
  <h2><a href="/vectorize-io/hindsight">hindsight</a></h2>
  <a href="/vectorize-io/hindsight/stargazers">20,100</a>
  <span>900 stars today</span>
</article>
<article>
  <h2><a href="/thedotmack/claude-mem">claude-mem</a></h2>
  <span>627 stars today</span>
</article>
"""


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_parse_trending_html_uses_h2_link_and_period_gain():
    hits = parse_trending_html(_WEEKLY)
    assert [(h.full_name, h.gained, h.period, h.stars) for h in hits] == [
        ("vectorize-io/hindsight", 14507, "weekly", 20000),
        ("debpalash/VoiceStudio", 16800, "weekly", 0),
    ]


def test_parse_trending_html_reads_today_and_compact_k():
    html = """
    <article>
      <h2><a href="/a/b">b</a></h2>
      <span>1.5k stars today</span>
    </article>
    """
    hits = parse_trending_html(html)
    assert hits == [TrendingHit("a/b", 1500, "daily", 0)]


def test_parse_trending_html_empty_and_non_article_is_empty():
    assert parse_trending_html("") == []
    assert parse_trending_html("<html><a href='/a/b'>x</a> 10 stars today</html>") == []


def test_merge_hits_prefers_weekly_gain_over_daily():
    hits = parse_trending_html(_WEEKLY) + parse_trending_html(_DAILY)
    merged = merge_hits(hits)
    by_name = {h.full_name.lower(): h for h in merged}
    assert by_name["vectorize-io/hindsight"].period == "weekly"
    assert by_name["vectorize-io/hindsight"].gained == 14507
    assert by_name["thedotmack/claude-mem"].gained == 627
    assert by_name["debpalash/voicestudio"].period == "weekly"
    # first-seen order: weekly page first, then daily-only
    assert [h.full_name for h in merged] == [
        "vectorize-io/hindsight", "debpalash/VoiceStudio", "thedotmack/claude-mem",
    ]


def test_collect_trending_requests_each_window_and_skips_a_failed_one():
    seen = []

    def handler(request):
        seen.append((str(request.url), request.headers.get("Authorization"),
                     request.headers.get("Accept-Language")))
        if "since=daily" in str(request.url):
            return httpx.Response(500, text="nope")
        return httpx.Response(200, text=_WEEKLY)

    hits = collect_trending(("weekly", "daily"), client=_client(handler),
                            retries=1, sleep=lambda _s: None)
    assert [h.full_name for h in hits] == ["vectorize-io/hindsight", "debpalash/VoiceStudio"]
    assert all(auth is None for _, auth, _ in seen)
    assert all(lang == "en" for _, _, lang in seen)
    assert any("since=weekly" in url for url, _, _ in seen)
    assert any("since=daily" in url for url, _, _ in seen)


def test_collect_trending_empty_periods_does_not_fetch():
    def handler(request):
        raise AssertionError("should not fetch")
    assert collect_trending((), client=_client(handler)) == []


def test_merge_trending_adds_only_new_repos_and_keeps_gains_for_known_ones():
    already = Repo(1, "acme/already", "https://github.com/acme/already",
                   "young", 500, "Py", [], False, False)
    old = Repo(99, "vectorize-io/hindsight", "https://github.com/vectorize-io/hindsight",
               "memory", 20000, "Python", [], False, False,
               created_at="2025-10-01T00:00:00Z")
    fork = Repo(7, "a/fork", "u", "", 10, "", [], True, False)
    hits = [
        TrendingHit("acme/already", 40, "weekly", 500),
        TrendingHit("vectorize-io/hindsight", 14507, "weekly", 20000),
        TrendingHit("a/fork", 10, "daily", 10),
    ]
    merged, gains = merge_trending(
        [already], hits, ["vectorize-io/hindsight", "a/fork"], [old, fork])
    assert [r.id for r in merged] == [1, 99]
    assert gains[1] == (40, "weekly")
    assert gains[99] == (14507, "weekly")
    assert 7 not in gains


def test_merge_trending_matches_hydration_when_the_api_renames_case():
    repo = Repo(3, "NVIDIA/OpenShell", "u", "", 100, "", [], False, False)
    hits = [TrendingHit("nvidia/openshell", 5900, "weekly")]
    merged, gains = merge_trending([], hits, ["nvidia/openshell"], [repo])
    assert [r.full_name for r in merged] == ["NVIDIA/OpenShell"]
    assert gains[3] == (5900, "weekly")


def test_parse_trending_html_reads_id_description_language_and_skips_forks_marker():
    from pathlib import Path
    html = (Path(__file__).parent / "fixtures" / "trending_daily_excerpt.html").read_text()
    hits = parse_trending_html(html)
    assert [(h.full_name, h.gained, h.stars, h.language, h.repo_id) for h in hits] == [
        ("morluto/rea", 4666, 12800, "TypeScript", 1209966933),
        ("boykopovar/AnyPS5", 2725, 9025, "C++", 1322250666),
        ("mattpocock/skills", 1406, 279116, "Shell", 1148788086),
    ]
    assert hits[0].description.startswith("Reverse engineer anything")
    assert hits[0].is_fork is False
    fork = parse_trending_html(
        '<article><h2><a href="/someone/forked-demo">x</a></h2>'
        "<p>forked from acme/demo</p><span>10 stars today</span></article>"
    )
    assert fork[0].is_fork is True


def test_prefer_gain_keeps_weekly_over_a_smaller_daily_count():
    assert prefer_gain(None, 3293, "daily") == (3293, "daily")
    assert prefer_gain((3293, "daily"), 14507, "weekly") == (14507, "weekly")
    assert prefer_gain((14507, "weekly"), 3293, "daily") == (14507, "weekly")


def test_collect_trending_requests_language_page_and_keeps_global_when_it_fails():
    seen = []

    def handler(request):
        seen.append(str(request.url))
        url = str(request.url)
        if "/trending/zig" in url:
            return httpx.Response(404, text="missing")
        return httpx.Response(200, text=_DAILY)

    hits = collect_trending(("daily",), languages=("Zig",), client=_client(handler),
                            retries=1, sleep=lambda _s: None)
    assert any(h.full_name == "thedotmack/claude-mem" for h in hits)
    assert any("/trending/zig" in url and "since=daily" in url for url in seen)
    assert any(url.split("?")[0].endswith("/trending") for url in seen)
