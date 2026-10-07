"""Copy guards applied to real channel text (posts #354–#388).

The posted descriptions are the model output that already shipped. This does
not call a model. It checks what the new title rule and blurb guard do to
that text, and how long the resulting posts are at the new cap of 5 items.
"""

import re
from dataclasses import dataclass

from bot.config import Theme
from bot.formatter import build_messages
from bot.summaries import BLURB_CHAR_LIMIT, prepare_public_blurb
from bot.titles import title_for

# Channel average over #354–#388, from the 2026-10-07 audit.
AUDITED_AVERAGE = 1941


@dataclass(frozen=True)
class Item:
    full_name: str
    stars: int
    language: str
    license: str
    posted: str          # the description that actually shipped
    description: str = ""  # repo's own text, used when `posted` is rejected
    readme: str = ""
    h1: str = ""


def _visible(html: str) -> str:
    text = re.sub(r"<[^>]+>", "", html)
    return (text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
            .replace("&quot;", '"').replace("&#x27;", "'"))


def _render(name: str, items: list[Item], limit: int | None = 5) -> str:
    shown = items if limit is None else items[:limit]
    theme = Theme(key="t", name=name, emoji="•", query="q", count=len(shown))
    repos = []
    for n, item in enumerate(shown):
        repos.append(type("R", (), {
            "id": n,
            "full_name": item.full_name,
            "html_url": f"https://github.com/{item.full_name}",
            "description": item.description or item.posted,
            "stars": item.stars,
            "language": item.language,
            "license": item.license,
            "topics": [],
        })())
    titles = [title_for(i.full_name, i.h1) for i in shown]
    summaries = [i.posted for i in shown]
    html = build_messages(
        theme, repos, describe=lambda r: next(
            i.readme for i in shown if i.full_name == r.full_name),
        titles=titles, summaries=summaries,
    )[0]
    return _visible(html)


# Real posts. Descriptions are quoted from the public channel scrape.

POST_354 = [Item(
    "ram0verflow/ram0verflow", 68, "Python", "MIT",
    "A small Bitcoin-like chain whose canonical ledger is my README. "
    "Proof of work is subset-sum, not hashing.",
    h1="ROFL",
)]

POST_362 = [Item(
    "0sec-labs/0", 359, "TypeScript", "",
    "0 is an open-source AI security agent that finds, exploits, and fixes "
    "vulnerabilities across codebases. It is notable for autonomous vulnerability "
    "discovery and remediation, with strong momentum as a research preview from a Swiss AI security lab.",
), Item(
    "devZero-Security/redStackPRO", 87, "Python", "MIT",
    "redStackPRO is a web canvas for designing red team infrastructure and cyber ranges, "
    "exporting runnable Terraform and Ansible. It keeps cloud credentials local, filling "
    "an infrastructure-as-code gap for security teams.",
), Item(
    "ressl/cve-2026-87902-poc", 38, "Python", "MIT",
    "This repository provides a proof-of-concept for CVE-2026-87902, an unauthenticated "
    "path traversal in WordPress Core enabling local PHP inclusion and conditional RCE. "
    "It includes a pinned vulnerable lab, making it timely and practical for testing.",
), Item(
    "dhicoc/dsh-reverse-skill", 181, "PowerShell", "MIT",
    "dsh-reverse-skill packages all 88 SKILL.md files from the reverse-skill project as "
    "a DeepSeek Harness plugin. It is useful for authorized reverse engineering and "
    "pentesting, providing a complete skill pack within the dsh ecosystem.",
), Item(
    "ok/mirall", 51, "JavaScript", "AGPL-3.0",
    "Mirall is a peer-to-peer application for secure large file transfer without "
    "cloud storage or accounts. It uses end-to-end encryption and direct connections, "
    "designed for moving terabyte-scale files privately with a small but dedicated user base.",
)]

POST_363 = [Item(
    "ppy/osu-web", 1159, "PHP", "AGPL-3.0",
    "ppy/osu-web is the browser-facing frontend for the osu! rhythm game, built as "
    "a production Laravel application. It is notable as a real-world example of a "
    "large-scale Laravel codebase powering a popular game's web services.",
    h1="osu!web",
), Item(
    "Napster2210/ngx-spinner", 867, "CSS", "MIT",
    "Napster2210/ngx-spinner is an Angular library offering over 50 customizable "
    "loading spinner animations, supporting Angular versions 4 through 22. It is "
    "notable for its maturity, extensive feature set like HTTP interceptors, and a new interactive playground.",
)]

POST_366 = [Item(
    "propavingk/SlotDrift", 64, "Python", "MIT",
    "SlotDrift is a Rust tool that analyzes captured Solana slot records to identify "
    "gaps, skips, duplicate slots, and orphaned branches. It is notable as an "
    "independent, offline observability tool for validators to assess chain continuity and fork behavior.",
    description="Slot continuity and fork analysis for captured Solana slot records - "
                "gaps, skips, duplicate slots and orphaned branches, with an independent Rust engine.",
    readme="Slot continuity and fork analysis for captured Solana slot records.",
    h1="SlotDrift",
)]

POST_374 = [Item(
    "wellwelwel/lagune", 172, "TypeScript", "MIT",
    "Lagune is a security copilot that points an AI agent at a codebase to guide "
    "developers and auditors through relevant security work without requiring an API key. "
    "It is notable for supporting 73 agents and any programming language, though it "
    "remains early-stage with low traction.",
)]

POST_381 = [Item(
    "tester-army/e2e", 3885, "TypeScript", "Apache-2.0",
    "tester-army/e2e is an end-to-end testing framework for web and mobile apps that "
    "uses natural language goals to drive autonomous agent actions with assertions. "
    "It is notable for its organization-backed approach to complex testing scenarios and active development.",
    h1="e2e",
), Item(
    "shihabal3amri/DiPlay", 4424, "Kotlin", "GPL-3.0",
    "DiPlay is an open-source application that enables CarPlay on compatible Android "
    "head units, specifically targeting BYD vehicles. It is notable for solving a "
    "specific Android/CarPlay integration problem with early community traction and a GPL-3.0 license.",
), Item(
    "newliver666/apk-reverse", 3212, "Python", "MIT",
    "apk-reverse is an agent skill designed for Android APK reverse engineering, "
    "including dex patching and repacking. It is notable for its structured approach "
    "to mobile security analysis and for being a tool rather than a tutorial for AI agents.",
), Item(
    "oil-oil/oil-motion", 2520, "Python", "MIT",
    "oil-motion is an interaction animation library for web applications that responds "
    "to scrolling, dragging, and pointer movements. It is notable for its utility in "
    "the Chinese developer community and its active maintenance under the MIT license.",
), Item(
    "thebuggeddev/anatomy", 3375, "TypeScript", "",
    "anatomy is an interactive 3D human anatomy explorer built using Three.js. "
    "It is notable for its modern web-based interface despite being built on an older "
    "GPT-5.6 foundation and having limited recent updates.",
)]

POST_387 = [Item(
    "Novotarskii/ivan-bohun", 40, "C", "MIT",
    "A personal website hosted across six ESP32 microcontrollers without cloud or CDN "
    "infrastructure. The blades elect a virtual-MAC leader for TCP load balancing and "
    "failover, demonstrating sophisticated distributed systems design on minimal hardware.",
)]

POST_388 = [Item(
    "omlahore/RemoveMacAI", 3358, "Swift", "MIT",
    "A native macOS app and CLI to disable Apple Intelligence, delete its models, and "
    "silence analytics, ads, pop-ups, and background updaters, with every change "
    "previewable and reversible. It builds on pared's reverse-engineering and drew "
    "press coverage, though its star growth looks inflated.",
    h1="RemoveMacAI",
), Item(
    "jaisuriya-11/tsuzuri", 186, "Go", "",
    "A block-based notebook for the terminal that stores notes as plain Markdown and "
    "offers Vim-style editing, live preview, boards, calendars, and 96 themes. A "
    "single-binary install for macOS, Linux, and Windows makes it an unusually "
    "full-featured TUI note-taking option.",
), Item(
    "CheshireMew/PuppetLoom", 245, "TypeScript", "AGPL-3.0",
    "A Windows desktop app, deterministic CLI, and project format that turns layered "
    "PSD character art into rigged, animated desktop puppets, with agents driving "
    "revisions through a public CLI. It builds on the See-Through layer-decomposition "
    "research and never fabricates missing poses.",
), Item(
    "ACoci86/terrahour", 163, "Python", "",
    "A terminal world clock that renders a braille-dot day/night world map plus a "
    "24-hour per-city timeline for planning meetings across zones. It includes DST "
    "handling and time scrubbing, and is a polished, fresh single-tool TUI.",
), Item(
    "4evy/pared", 162, "Swift", "MIT",
    "A tool that selectively removes unwanted Apple Intelligence models on macOS "
    "without disabling SIP, via a native app, CLI, nix-darwin, and Home Manager. "
    "It also underpins RemoveMacAI, which credits it for the hard reverse-engineering work.",
)]

# #371 and #359 are the evergreen-giant posts. The ceiling drops them; the copy
# guard is still applied here so the length sample includes a long "notable" post.
POST_371 = [Item(
    "vercel/next.js", 142993, "JavaScript", "MIT",
    "Next.js is a React framework for building full-stack web applications with "
    "Rust-based tooling for faster builds. It is notable as the dominant React "
    "framework, widely used by major companies and backed by a large community.",
    description="The React Framework for the Web",
), Item(
    "sveltejs/svelte", 88228, "JavaScript", "MIT",
    "Svelte is a compiler that converts declarative components into efficient "
    "JavaScript that updates the DOM surgically. It is notable for its compiler-based "
    "approach to UI development, offering a novel alternative to runtime frameworks.",
    description="web development for the rest of us",
), Item(
    "webpack/webpack", 65956, "JavaScript", "MIT",
    "webpack is a module bundler that packages JavaScript and other assets for browser "
    "use, supporting code splitting and various module formats. It is notable as a "
    "major, mature build tool with deep bundling and runtime implications.",
    description="A bundler for javascript and friends.",
)]

POST_359 = [Item(
    "go-sql-driver/mysql", 15286, "Go", "MPL-2.0",
    "go-sql-driver/mysql is a MySQL driver for Go's database/sql package. "
    "It is notable as the de facto standard, battle-tested MySQL driver for Go applications.",
), Item(
    "dbcli/pgcli", 13407, "Python", "BSD-3-Clause",
    "pgcli is a Postgres command-line client with autocompletion and syntax highlighting. "
    "It is notable as a best-in-class, daily-use tool for database developers.",
), Item(
    "dbcli/mycli", 11978, "Python", "BSD-3-Clause",
    "mycli is a MySQL terminal client with auto-completion, syntax highlighting, and "
    "dataframe integration. It is notable as a practical, well-maintained tool that "
    "supports MySQL, MariaDB, Percona, TiDB, and Apache Doris.",
)]

FIXTURES = {
    "Crypto & Web3": POST_354,
    "Security": POST_362,
    "Web & Frontend": POST_363,
    "Crypto thin": POST_366,
    "Security long": POST_374,
    "Trending Overall": POST_381,
    "Embedded & Hardware": POST_387,
    "Dev Tools & CLI": POST_388,
    "Systems & Languages": POST_371,
    "Data & Databases": POST_359,
}


def test_real_posts_use_repo_names_and_drop_leaks():
    slot = _render("Crypto", POST_366, limit=1)
    assert "SlotDrift" in slot
    assert "Slot Drift Analysis" not in slot
    assert "Rust" not in slot
    assert "Python" in slot
    assert "Slot continuity and fork analysis for captured Solana slot records." in slot

    crypto = _render("Crypto", POST_354, limit=1)
    assert "ROFL" in crypto
    assert "README Bitcoin Chain" not in crypto

    security = _render("Security", POST_362, limit=5)
    assert "\n0\n" in security or security.splitlines()[2] == "0"
    assert "AI Security Agent" not in security
    assert "small but dedicated" not in security
    assert "notable" not in security.lower()

    web = _render("Web", POST_363, limit=5)
    assert "osu!web" in web
    assert "ppy/osu-web is" not in web
    assert "ngx-spinner" in web
    assert "Napster2210/ngx-spinner is" not in web

    tools = _render("Dev Tools", POST_388, limit=1)
    assert "RemoveMacAI" in tools
    assert "inflated" not in tools
    assert "star growth" not in tools
    assert "notable" not in tools.lower()

    trending = _render("Trending", POST_381, limit=5)
    assert "e2e" in trending
    assert "Next Generation E2E" not in trending
    assert "anatomy" in trending
    assert "limited recent updates" not in trending

    embedded = _render("Embedded", POST_387, limit=1)
    assert "Ivan Bohun" in embedded
    assert "Ivan Bohun Website" not in embedded

    for post in FIXTURES.values():
        rendered = _render("T", post, limit=5)
        assert "notable" not in rendered.lower()
        for line in rendered.splitlines():
            # metadata lines are short; blurbs are the long ones under them
            if line.startswith("⭐"):
                continue
            assert len(line) <= BLURB_CHAR_LIMIT or line.startswith("•")


def test_fixture_posts_are_well_under_the_audited_average():
    lengths = []
    for name, items in FIXTURES.items():
        text = _render(name, items, limit=5)
        lengths.append(len(text))
        for item in items:
            blurb = prepare_public_blurb(item.posted, type("R", (), {
                "full_name": item.full_name,
                "language": item.language,
                "license": item.license,
            })())
            if blurb is not None:
                assert len(blurb) <= BLURB_CHAR_LIMIT
                assert "notable" not in blurb.lower()
    average = sum(lengths) / len(lengths)
    five = [len(_render(name, items, limit=5))
            for name, items in FIXTURES.items() if len(items) >= 5]
    five_avg = sum(five) / len(five)
    # The audited channel average was 1,941 characters, usually 7 two-sentence
    # items. The 5-item fixtures are the fair comparison (Security, Trending,
    # Dev Tools). The all-fixture average also includes the 1-item posts.
    assert five_avg < 1100, five_avg
    assert average < 1200, average
    assert average < AUDITED_AVERAGE * 0.7
    assert max(lengths) < AUDITED_AVERAGE
