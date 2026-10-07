"""GitNova parsers. Fixtures are trimmed from gitnova.dev on 2026-10-07.

The likely-inflated row is the label shape from GitNova's own description of
star-trust (organic / unusual / likely inflated). That day's category pages
showed unusual, not a third label, so the drop case is a one-line stand-in.
"""
import json
from pathlib import Path

import httpx

from bot.gitnova import (
    GitNovaHit,
    collect_gitnova,
    is_likely_inflated,
    merge_gitnova,
    parse_category_md,
    parse_feed_xml,
    parse_search_json,
    public_summary,
)

_FIX = Path(__file__).parent / "fixtures"


def _text(name: str) -> str:
    return (_FIX / name).read_text(encoding="utf-8")


def test_parse_feed_xml_reads_name_magnitude_and_summary():
    hits = parse_feed_xml(_text("gitnova_feed_excerpt.xml"))
    assert [(h.full_name, h.magnitude) for h in hits] == [
        ("Funny-Bones/ELDEN-RING-Combat-Rewrite", 5.0),
        ("pingdotgg/ts-rust", 6.2),
    ]
    assert "TypeScript 7 compiler" in hits[1].summary
    assert hits[0].trust == "" and hits[0].stars_today == 0
    assert "&#x27;" not in hits[0].summary


def test_parse_feed_xml_bad_input_is_empty():
    assert parse_feed_xml("") == []
    assert parse_feed_xml("<feed>nope") == []
    assert parse_feed_xml("not xml") == []


def test_parse_category_md_reads_breakout_early_unusual_and_no_language():
    hits = parse_category_md(_text("gitnova_ai_agents_excerpt.md"), category="ai_agents")
    by_name = {h.full_name.lower(): h for h in hits}
    rea = by_name["morluto/rea"]
    assert rea.stage == "breakout" and rea.stars_today == 3293
    assert rea.language == "TypeScript" and rea.category == "ai_agents"
    assert rea.magnitude == 9.3
    assert "Hopper or Ghidra" in rea.summary
    assert by_name["lexmount/moli"].stars_today == 1060
    assert by_name["thedotmack/claude-mem"].stage == "breakout"
    dots = by_name["feder-cr/invisible_dots"]
    assert dots.trust == "doubt"
    assert "unusual" in dots.trust_label.lower()
    assert not is_likely_inflated(dots)
    early = by_name["agentmemoryrepo/agentmemoryrepo"]
    assert early.stage == "early" and early.language == "" and early.stars_today == 45
    assert "Full card" not in early.summary
    assert all(not is_likely_inflated(h) for h in hits)


def test_parse_category_md_drops_nothing_but_flags_likely_inflated():
    text = """
9. **farm/stars** — 8.0 · Breakout · AI agents · Python · +9,000 stars measured 2026-10-07 · likely inflated
   A dumped star count with no project behind it.
"""
    hits = parse_category_md(text, category="ai_agents")
    assert len(hits) == 1
    assert hits[0].full_name == "farm/stars"
    assert is_likely_inflated(hits[0])
    assert "likely inflated" not in public_summary(hits[0].summary).lower()
    assert "dumped star count" in hits[0].summary


def test_parse_search_json_keeps_ok_and_doubt():
    hits = parse_search_json(_text("gitnova_search_excerpt.json"))
    assert hits[0].full_name == "morluto/rea"
    assert hits[0].stars == 12633 and hits[0].stars_today == 3293
    assert hits[0].age_days == 176 and hits[0].trust == "ok"
    assert not is_likely_inflated(hits[0])
    assert hits[1].trust == "doubt" and not is_likely_inflated(hits[1])


def test_parse_search_json_flags_inflated_trust_codes():
    payload = {
        "results": [
            {"full_name": "a/farm", "trust": "likely_inflated", "summary": "nope",
             "stars_today_so_far": 10, "stage": "breakout"},
            {"full_name": "b/ok", "trust": "organic", "summary": "real tool"},
            {"full_name": "not a repo", "trust": "inflated"},
        ]
    }
    hits = parse_search_json(payload)
    assert [h.full_name for h in hits] == ["a/farm", "b/ok"]
    assert is_likely_inflated(hits[0])
    assert not is_likely_inflated(hits[1])
    assert parse_search_json("{") == []
    assert parse_search_json({"nope": True}) == []


def test_merge_gitnova_prefers_richer_record_and_keeps_inflated():
    thin = GitNovaHit("morluto/rea", summary="from the feed", magnitude=9.3)
    rich = GitNovaHit(
        "Morluto/rea", summary="", category="ai_agents", language="TypeScript",
        stars=12633, stars_today=3293, stage="breakout", trust="ok", age_days=176,
    )
    flagged = GitNovaHit("farm/stars", trust="ok", stars_today=50, stage="breakout")
    prose = GitNovaHit("farm/stars", trust_label="likely inflated", summary="A dump.")
    merged = merge_gitnova([thin, rich, flagged, prose])
    by_name = {h.full_name.lower(): h for h in merged}
    rea = by_name["morluto/rea"]
    assert rea.stars == 12633 and rea.stars_today == 3293
    assert rea.summary == "from the feed" and rea.category == "ai_agents"
    assert is_likely_inflated(by_name["farm/stars"])


def test_public_summary_strips_a_trailing_trust_clause():
    assert public_summary("A compiler. likely inflated") == "A compiler."
    assert "unusual" not in public_summary("Ships a CLI. unusual star pattern (heuristic)").lower()
    assert public_summary("likely inflated") == ""


def test_collect_gitnova_merges_reads_and_skips_a_failed_one():
    feed = _text("gitnova_feed_excerpt.xml")
    md = _text("gitnova_ai_agents_excerpt.md")
    search = _text("gitnova_search_excerpt.json")
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        url = str(request.url)
        if url.endswith("/feed.xml"):
            return httpx.Response(200, text=feed)
        if url.endswith("/c/ai_agents.md"):
            return httpx.Response(200, text=md)
        if "/api/v1/search" in url and "category=ai_agents" in url:
            return httpx.Response(200, text=search)
        if url.endswith("/c/llm.md"):
            return httpx.Response(500, text="nope")
        return httpx.Response(200, text="not a category page" if url.endswith(".md") else '{"results":[]}')

    hits, status, error = collect_gitnova(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        retries=1, sleep=lambda _s: None)
    assert status == "ok" and error == ""
    names = {h.full_name.lower() for h in hits}
    assert "morluto/rea" in names
    assert "pingdotgg/ts-rust" in names
    assert "lexmount/moli" in names
    rea = next(h for h in hits if h.full_name.lower() == "morluto/rea")
    # JSON is the richer record: total stars survive the markdown row.
    assert rea.stars == 12633 and rea.stars_today == 3293 and not is_likely_inflated(rea)
    assert any(url.endswith("/feed.xml") for url in seen)
    assert any("/c/devtools.md" in url for url in seen)
    assert any("category=llm" in url for url in seen)
    assert any("category=gen_media" in url for url in seen)


def test_collect_gitnova_all_failed_is_an_error_and_does_not_raise():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(503, text="down")

    hits, status, error = collect_gitnova(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        retries=1, sleep=lambda _s: None)
    assert hits == [] and status == "error" and error
    # Two failures and the rest of the reads are skipped, so a dead host
    # cannot stall the cron on every category URL.
    assert len(seen) == 2
