from dataclasses import dataclass

import httpx

from bot.titles import heading_is_project_name, make_titles, repo_name_title, title_for


@dataclass(frozen=True)
class R:
    full_name: str
    description: str = ""


def test_repo_name_title_keeps_separator_free_names_and_prettifies_hyphens():
    assert repo_name_title("google-gemini/gemini-cli") == "Gemini CLI"
    assert repo_name_title("foo/my_cool_tool") == "My Cool Tool"
    assert repo_name_title("nokia-applied-research/AnyJev") == "AnyJev"
    assert repo_name_title("tester-army/e2e") == "e2e"
    assert repo_name_title("0sec-labs/0") == "0"
    assert repo_name_title("vercel/next.js") == "next.js"
    assert repo_name_title("Novotarskii/ivan-bohun") == "Ivan Bohun"


def test_h1_must_be_the_project_name():
    assert heading_is_project_name("SlotDrift", "propavingk/SlotDrift")
    assert heading_is_project_name("osu!web", "ppy/osu-web")
    assert heading_is_project_name("e2e", "tester-army/e2e")
    assert heading_is_project_name("ROFL", "ram0verflow/ram0verflow")
    assert heading_is_project_name("Steel Bank Common Lisp", "sbcl/sbcl")
    assert heading_is_project_name("Lagune AI", "wellwelwel/lagune")
    # Invented or template headings are not the name.
    assert not heading_is_project_name("AI Security Agent", "0sec-labs/0")
    assert not heading_is_project_name("Next Generation E2E Testing Framework", "tester-army/e2e")
    assert not heading_is_project_name("vinext-starter", "thebuggeddev/anatomy")
    assert not heading_is_project_name("README", "ram0verflow/ram0verflow")
    assert not heading_is_project_name("Ivan Bohun Website", "Novotarskii/ivan-bohun")
    assert not heading_is_project_name("", "a/b")


def test_title_for_real_posts():
    # #362, #369, #381, #387, #354, #366, #363, #381 anatomy
    assert title_for("0sec-labs/0", "AI Security Agent") == "0"
    assert title_for("nokia-applied-research/AnyJev") == "AnyJev"
    assert title_for("tester-army/e2e", "e2e") == "e2e"
    assert title_for("Novotarskii/ivan-bohun") == "Ivan Bohun"
    assert title_for("ram0verflow/ram0verflow", "ROFL") == "ROFL"
    assert title_for("ram0verflow/ram0verflow", "README") == "ram0verflow"
    assert title_for("propavingk/SlotDrift", "SlotDrift") == "SlotDrift"
    assert title_for("ppy/osu-web", "osu!web") == "osu!web"
    assert title_for("thebuggeddev/anatomy", "vinext-starter") == "anatomy"


def test_make_titles_ignores_the_model_even_when_a_host_is_set():
    repos = [R("0sec-labs/0"), R("propavingk/SlotDrift")]
    def handler(request):
        return httpx.Response(
            200, json={"message": {"content": '["AI Security Agent", "Slot Drift Analysis"]'}})
    client = httpx.Client(transport=httpx.MockTransport(handler))
    out = make_titles(repos, headings=["", "SlotDrift"], host="http://x", model="m",
                      api_key="k", client=client)
    assert out == ["0", "SlotDrift"]


def test_make_titles_without_headings_uses_the_repo_name():
    assert make_titles([R("google-gemini/gemini-cli")]) == ["Gemini CLI"]


def test_make_titles_mismatched_headings_are_ignored():
    assert make_titles([R("a/b"), R("c/d-tool")], headings=["Only"]) == ["b", "D Tool"]
