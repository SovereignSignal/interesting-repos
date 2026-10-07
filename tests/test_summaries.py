from dataclasses import dataclass

import httpx

from bot.summaries import make_summaries


@dataclass(frozen=True)
class R:
    full_name: str = "a/b"
    description: str = "d"
    language: str = ""
    license: str = ""
    stars: int = 10


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def _content_client(content):
    return _client(lambda request: httpx.Response(200, json={"message": {"content": content}}))


def test_make_summaries_returns_blurbs_in_order():
    repos = [R("a/one"), R("b/two")]
    out = make_summaries(repos, ["ex1", "ex2"], host="http://x", model="m",
                         client=_content_client('["Blurb one.", "Blurb two."]'))
    assert out == ["Blurb one.", "Blurb two."]


def test_make_summaries_without_host_returns_nones():
    assert make_summaries([R(), R()], host="") == [None, None]


def test_make_summaries_on_error_returns_nones():
    out = make_summaries([R(), R()], host="http://x", model="m",
                         client=_client(lambda request: httpx.Response(500)))
    assert out == [None, None]


def test_make_summaries_length_mismatch_returns_nones():
    out = make_summaries([R(), R(), R()], host="http://x", model="m",
                         client=_content_client('["only one"]'))
    assert out == [None, None, None]


def test_make_summaries_tolerates_fences_and_prose():
    out = make_summaries([R("a/x")], host="http://x", model="m",
                         client=_content_client('Sure:\n```json\n["Clean blurb."]\n```'))
    assert out == ["Clean blurb."]


def test_make_summaries_blank_blurb_becomes_none():
    out = make_summaries([R("a/x"), R("b/y")], host="http://x", model="m",
                         client=_content_client('["", "Real blurb."]'))
    assert out == [None, "Real blurb."]


def test_make_summaries_does_not_send_curator_whys():
    # The why is scoring rationale. #388 quoted "star growth looks inflated"
    # from it. The notes stay at the call site and out of the prompt.
    captured = {}
    def handler(request):
        import json as _json
        captured["p"] = _json.loads(request.content)["messages"][0]["content"]
        return httpx.Response(200, json={"message": {"content": '["Blurb."]'}})
    make_summaries([R("a/x", language="Python", stars=64, license="MIT")],
                   ["readme text"], whys=["star growth looks inflated"],
                   host="http://x", model="m", client=_client(handler))
    prompt = captured["p"]
    assert "star growth looks inflated" not in prompt
    assert "language: Python" in prompt
    assert "150" in prompt
    assert "160" not in prompt
    assert "complete sentence" in prompt.lower()
    assert "notable" in prompt.lower()   # the ban, not a request to write it


def test_make_summaries_disables_thinking():
    seen = {}
    def handler(request):
        import json as _json
        seen["think"] = _json.loads(request.content).get("think", "OMITTED")
        return httpx.Response(200, json={"message": {"content": '["Blurb."]'}})
    make_summaries([R("a/x")], host="http://x", model="m", client=_client(handler))
    assert seen["think"] is False


def test_prepare_splits_a_sentence_that_ends_on_a_number():
    # "versions 4 through 22. It is notable…" — the dot after 22 is a sentence
    # end. "v1.2 ships." must not split on the version dot.
    from bot.summaries import prepare_public_blurb
    repo = R("Napster2210/ngx-spinner", language="CSS", license="MIT")
    out = prepare_public_blurb(
        "ngx-spinner supports Angular versions 4 through 22. It is notable for its maturity.",
        repo,
    )
    assert out == "ngx-spinner supports Angular versions 4 through 22."
    version = prepare_public_blurb("Library v1.2 ships one binary.", repo)
    assert version == "Library v1.2 ships one binary."


def test_prepare_keeps_the_first_sentence_and_drops_notable():
    from bot.summaries import prepare_public_blurb
    repo = R("wellwelwel/lagune", language="TypeScript")
    posted = (
        "Lagune is a security copilot that points an AI agent at a codebase "
        "to guide developers and auditors through relevant security work "
        "without requiring an API key. It is notable for supporting 73 agents "
        "and any programming language, though it remains early-stage with low traction."
    )
    out = prepare_public_blurb(posted, repo)
    assert out.startswith("Lagune is a security copilot")
    assert "notable" not in out.lower()
    assert "low traction" not in out.lower()
    assert len(out) <= 160


def test_prepare_rejects_a_rust_claim_on_a_python_repo():
    from bot.summaries import prepare_public_blurb
    repo = R("propavingk/SlotDrift", language="Python", license="MIT")
    posted = ("SlotDrift is a Rust tool that analyzes captured Solana slot records. "
              "It is notable as an independent observability tool.")
    assert prepare_public_blurb(posted, repo) is None
    # A component mention with no identity claim still names the other language.
    assert prepare_public_blurb(
        "Analyzes slots, with an independent Rust engine.", repo) is None
    # A PoC can name the target language. Tooling can name another language
    # without claiming the repo is written in it.
    poc = R("ressl/cve-2026-87902-poc", language="Python")
    kept_poc = prepare_public_blurb(
        "A proof-of-concept for a path traversal in WordPress Core enabling local PHP inclusion.",
        poc)
    assert kept_poc and "PHP inclusion" in kept_poc
    js = R("vercel/next.js", language="JavaScript")
    kept_js = prepare_public_blurb(
        "Next.js is a React framework for the web with Rust-based tooling for faster builds.",
        js)
    assert kept_js and "Rust-based" in kept_js
    rust = R("benbenbang/libitofin", language="Rust")
    kept = prepare_public_blurb(
        "libitofin is a Rust port of QuantLib with Python and Go bindings.", rust)
    assert kept and "Rust port" in kept


def test_prepare_strips_a_leading_slug_and_rejects_a_license_mismatch():
    from bot.summaries import prepare_public_blurb
    repo = R("ppy/osu-web", language="PHP", license="AGPL-3.0")
    out = prepare_public_blurb(
        "ppy/osu-web is the browser-facing frontend for the osu! rhythm game. "
        "It is notable as a large Laravel codebase.",
        repo,
    )
    assert out.startswith("osu-web is the browser-facing")
    assert not out.lower().startswith("ppy/")
    assert "notable" not in out.lower()
    assert prepare_public_blurb("A PHP frontend under the MIT license.", repo) is None


def test_prepare_clips_a_long_sentence_on_a_comma():
    from bot.summaries import prepare_public_blurb
    repo = R("omlahore/RemoveMacAI", language="Swift", license="MIT")
    posted = (
        "A native macOS app and CLI to disable Apple Intelligence, delete its models, "
        "and silence analytics, ads, pop-ups, and background updaters, with every change "
        "previewable and reversible."
    )
    out = prepare_public_blurb(posted, repo)
    assert out.endswith("background updaters.")
    assert "every change" not in out
    assert len(out) <= 160
    assert "..." not in out


def test_prepare_rejects_internal_judgment_in_the_only_sentence():
    from bot.summaries import prepare_public_blurb
    repo = R("omlahore/RemoveMacAI", language="Swift", license="MIT")
    assert prepare_public_blurb(
        "RemoveMacAI drew press coverage, though its star growth looks inflated.",
        repo,
    ) is None


def test_make_summaries_filters_a_bad_blurb_to_none():
    repo = R("propavingk/SlotDrift", language="Python")
    out = make_summaries(
        [repo], host="http://x", model="m",
        client=_content_client('["SlotDrift is a Rust tool that analyzes slots."]'),
    )
    assert out == [None]


def test_make_summaries_works_without_whys():
    out = make_summaries([R("a/x")], ["ex"], host="http://x", model="m",
                         client=_content_client('["Blurb."]'))
    assert out == ["Blurb."]


_DANGLING = {
    "a", "an", "the", "of", "with", "to", "for", "and", "or", "by",
    "in", "on", "from", "via", "using",
}


def _assert_closed(text: str) -> None:
    assert text.endswith(".")
    assert 40 <= len(text) <= 160
    assert "..." not in text and "…" not in text
    assert text[:-1].split()[-1].lower().strip(".,;:!?") not in _DANGLING


def test_prepare_closes_the_vulnhunter_blurb_on_a_clause():
    # Post #389. The writer stopped on "sandboxed" (156 chars). A longer
    # source hard-cut at the same word. Both close before "with".
    from bot.summaries import prepare_public_blurb
    repo = R("nealbridges/VulnHunter", language="Python", license="Apache-2.0")
    posted = (
        "VulnHunter is an Apache-2.0 AI security scanner maintained as a "
        "harness-portable fork of Capital One's tool and used to prove "
        "vulnerabilities with sandboxed"
    )
    expected = (
        "VulnHunter is an Apache-2.0 AI security scanner maintained as a "
        "harness-portable fork of Capital One's tool and used to prove "
        "vulnerabilities."
    )
    out = prepare_public_blurb(posted, repo)
    assert out == expected
    _assert_closed(out)
    assert "sandboxed" not in out
    longer = posted + " execution environments today"
    assert len(longer) > 160
    assert prepare_public_blurb(longer, repo) == expected


def test_prepare_closes_the_pbitm_blurb_on_a_clause():
    # Post #389. 153 characters, unfinished on "session viewing".
    from bot.summaries import prepare_public_blurb
    repo = R("P-BitM-Framework/P-BitM", language="Python", license="GPL-3.0")
    posted = (
        "P-BitM is a GPL-3.0 Python platform for controlled "
        "browser-in-the-middle security assessments with an admin dashboard, "
        "isolated browsers, session viewing"
    )
    out = prepare_public_blurb(posted, repo)
    assert out == (
        "P-BitM is a GPL-3.0 Python platform for controlled "
        "browser-in-the-middle security assessments with an admin dashboard, "
        "isolated browsers."
    )
    _assert_closed(out)
    assert "session viewing" not in out


def test_prepare_closes_a_long_blurb_with_no_clause_boundary():
    # No comma, semicolon, dash, or and/with/that/which. The cap must still
    # end on a word and a period, and "using" is not a place to stop.
    from bot.summaries import prepare_public_blurb
    repo = R("a/scanner")
    posted = (
        "Scanner examines container images inside isolated runners during "
        "nightly passes across production fleets nightly nightly nightly "
        "nightly nightly nightly using leftovers"
    )
    assert len(posted) > 160
    out = prepare_public_blurb(posted, repo)
    _assert_closed(out)
    assert "using" not in out
    assert "leftovers" not in out
    stem = out[:-1]
    assert posted.startswith(stem)
    assert posted[len(stem)] == " "


def test_prepare_keeps_a_near_cap_description_with_a_real_final_clause():
    # Unfinished and close to the cap, but the words after the last "and" are
    # a real clause. Closing it must not drop them the way a remnant is dropped.
    from bot.summaries import prepare_public_blurb
    repo = R("rust-dd/stochastic-rs", language="Rust", license="Apache-2.0")
    posted = (
        "High-performance quantitative finance in Rust providing stochastic "
        "processes, option pricing, calibration, and risk tools for researchers "
        "who want them"
    )
    assert len(posted) >= 140
    out = prepare_public_blurb(posted, repo)
    assert out.endswith("who want them.")
    assert "risk tools" in out
    _assert_closed(out)


def test_prepare_omits_a_cut_that_would_be_a_fragment():
    # The only clause boundary is an early comma, so the closed blurb would
    # be "Go." Callers fall back to the repo description instead.
    from bot.summaries import prepare_public_blurb
    repo = R("a/b", language="Go", description="A maintained scanner that proves findings.")
    posted = "Go, " + ("scanner examines images inside isolated runners " * 6)
    assert len(posted) > 160
    assert prepare_public_blurb(posted, repo) is None
