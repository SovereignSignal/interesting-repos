from datetime import datetime
import bot.main as main
from bot.config import Config, Theme
from bot.github import Repo


def _cfg(tmp_path, themes, delay=0, ollama=""):
    # Config(tg_token, tg_chat, github_token, state_dir, themes, ollama_host=...).
    # ollama="" keeps make_titles/make_summaries/translate offline; the summary branch
    # (README fetch + make_summaries) only runs when ollama is set. delay=0 => no real sleep.
    return Config("tok", "-100", "", str(tmp_path), themes, ollama, send_delay_seconds=delay)

def _repo(i, stars):
    return Repo(i, f"a/{i}", f"https://x/{i}", "desc", stars, "Py", [], False, False)

def _patch(monkeypatch, repos, sent_box):
    monkeypatch.setattr(main, "search_repos", lambda *a, **k: list(repos))
    monkeypatch.setattr(main, "readme_first_line", lambda *a, **k: "")
    monkeypatch.setattr(main, "send_message",
                        lambda *a, **k: sent_box.append(a[2]) or {"ok": True})

def test_run_sends_and_records_state(tmp_path, monkeypatch):
    sent = []
    theme = Theme(key="t", name="T", emoji="🔥", query="created:>{since:7d}", count=2)
    _patch(monkeypatch, [_repo(1, 10), _repo(2, 99)], sent)
    failures = main.run(_cfg(tmp_path, [theme]), now=datetime(2026, 5, 26))
    assert failures == 0 and len(sent) == 1
    import json
    saved = json.loads((tmp_path / "state.json").read_text())
    assert sorted(saved["t"]) == [1, 2]

def test_run_dry_run_does_not_send_or_persist(tmp_path, monkeypatch, capsys):
    sent = []
    theme = Theme(key="t", name="T", emoji="", query="q", count=1)
    _patch(monkeypatch, [_repo(1, 10)], sent)
    failures = main.run(_cfg(tmp_path, [theme]), now=datetime(2026, 5, 26), dry_run=True)
    assert failures == 0 and sent == []
    assert not (tmp_path / "state.json").exists()
    assert "a/1" in capsys.readouterr().out

def test_run_skips_already_sent(tmp_path, monkeypatch):
    sent = []
    theme = Theme(key="t", name="T", emoji="", query="q", count=5)
    _patch(monkeypatch, [_repo(1, 10)], sent)
    (tmp_path / "state.json").write_text('{"t": [1]}')
    failures = main.run(_cfg(tmp_path, [theme]), now=datetime(2026, 5, 26))
    assert failures == 0 and sent == []

def test_run_isolates_theme_failures(tmp_path, monkeypatch):
    sent = []
    good = Theme(key="g", name="G", emoji="", query="q", count=1)
    bad = Theme(key="b", name="B", emoji="", query="q", count=1)
    monkeypatch.setattr(main, "search_repos", lambda *a, **k: [_repo(1, 5)])
    monkeypatch.setattr(main, "readme_first_line", lambda *a, **k: "")
    monkeypatch.setattr(main, "send_message", lambda *a, **k: sent.append(a[2]) or {"ok": True})
    real_rank = main.rank
    def flaky_rank(repos, theme, **k):
        if theme.key == "b":
            raise RuntimeError("rank boom")
        return real_rank(repos, theme, **k)
    monkeypatch.setattr(main, "rank", flaky_rank)
    failures = main.run(_cfg(tmp_path, [good, bad]), now=datetime(2026, 5, 26))
    assert failures == 1 and len(sent) == 1


def test_run_dedups_repo_across_themes(tmp_path, monkeypatch):
    sent = []
    def fake_search(query, **k):
        # `first` (query AAA) and `second` (query BBB) both surface repo id=1.
        return [_repo(1, 100), _repo(2, 50)] if "AAA" in query else [_repo(1, 100), _repo(3, 40)]
    monkeypatch.setattr(main, "search_repos", lambda query, **k: fake_search(query))
    monkeypatch.setattr(main, "readme_first_line", lambda *a, **k: "")
    monkeypatch.setattr(main, "send_message", lambda *a, **k: sent.append(a[2]) or {"ok": True})
    first = Theme(key="first", name="F", emoji="", query="AAA", count=5)
    second = Theme(key="second", name="S", emoji="", query="BBB", count=5)
    main.run(_cfg(tmp_path, [first, second]), now=datetime(2026, 6, 4))
    import json
    saved = json.loads((tmp_path / "state.json").read_text())
    assert saved["first"] == [1, 2]    # `first` selected first, claims repo 1
    assert saved["second"] == [3]      # repo 1 deduped out of `second`


def test_run_catch_all_selected_last_but_delivered_first(tmp_path, monkeypatch):
    sent = []
    def fake_search(query, **k):
        return [_repo(1, 100), _repo(9, 80)] if "TR" in query else [_repo(1, 100), _repo(2, 50)]
    monkeypatch.setattr(main, "search_repos", lambda query, **k: fake_search(query))
    monkeypatch.setattr(main, "readme_first_line", lambda *a, **k: "")
    monkeypatch.setattr(main, "send_message", lambda *a, **k: sent.append(a[2]) or {"ok": True})
    trending = Theme(key="trending", name="Trending", emoji="📈", query="TR", count=5, catch_all=True)
    ai = Theme(key="ai", name="AI", emoji="🤖", query="AI", count=5)
    main.run(_cfg(tmp_path, [trending, ai]), now=datetime(2026, 6, 4))
    import json
    saved = json.loads((tmp_path / "state.json").read_text())
    assert saved["ai"] == [1, 2]                  # ai selected first, claims shared repo 1
    assert saved["trending"] == [9]               # trending selected last -> repo 1 deduped out
    assert "📈" in sent[0] and "🤖" in sent[1]      # delivered in themes.toml (display) order


def test_run_cap_zero_drops_ai_repos(tmp_path, monkeypatch):
    sent = []
    ai = Repo(1, "a/gstack", "u1", "Claude Code setup", 100, "Py", [], False, False)
    nonai = Repo(2, "b/pretext", "u2", "text measurement and layout", 50, "Py", [], False, False)
    monkeypatch.setattr(main, "search_repos", lambda *a, **k: [ai, nonai])
    monkeypatch.setattr(main, "readme_first_line", lambda *a, **k: "")
    monkeypatch.setattr(main, "send_message", lambda *a, **k: sent.append(a[2]) or {"ok": True})
    theme = Theme(key="trending", name="T", emoji="📈", query="q", count=5, agent_skill_cap=0)
    main.run(_cfg(tmp_path, [theme]), now=datetime(2026, 6, 4))
    import json
    saved = json.loads((tmp_path / "state.json").read_text())
    assert saved["trending"] == [2]   # the AI repo (gstack) is dropped by cap=0


def test_run_cap_n_limits_skill_packs(tmp_path, monkeypatch):
    sent = []
    p1 = Repo(1, "a/one-skills", "u", "skills for agents", 100, "Py", [], False, False)
    p2 = Repo(2, "b/two-skills", "u", "skill pack", 90, "Py", [], False, False)
    p3 = Repo(3, "c/three-skills", "u", "agent skill collection", 80, "Py", [], False, False)
    tool = Repo(4, "d/realdb", "u", "a database engine", 70, "Py", [], False, False)
    monkeypatch.setattr(main, "search_repos", lambda *a, **k: [p1, p2, p3, tool])
    monkeypatch.setattr(main, "readme_first_line", lambda *a, **k: "")
    monkeypatch.setattr(main, "send_message", lambda *a, **k: sent.append(a[2]) or {"ok": True})
    theme = Theme(key="sec", name="S", emoji="", query="q", count=5, agent_skill_cap=2)
    main.run(_cfg(tmp_path, [theme]), now=datetime(2026, 6, 4))
    import json
    saved = json.loads((tmp_path / "state.json").read_text())
    assert sorted(saved["sec"]) == [1, 2, 4]   # at most 2 packs (p1,p2) + non-pack tool; p3 dropped


def test_run_fires_only_the_theme_matching_current_slot(tmp_path, monkeypatch):
    sent = []
    # distinct repos per theme so dedup can't mask the slot filter
    monkeypatch.setattr(main, "search_repos",
                        lambda query, **k: [_repo(1, 10)] if "EARLY" in query else [_repo(2, 10)])
    monkeypatch.setattr(main, "readme_first_line", lambda *a, **k: "")
    monkeypatch.setattr(main, "send_message", lambda *a, **k: sent.append(a[2]) or {"ok": True})
    early = Theme(key="early", name="E", emoji="", query="EARLY", count=1, at=((0, 13),))  # Mon 13
    late = Theme(key="late", name="L", emoji="", query="LATE", count=1, at=((0, 19),))     # Mon 19
    main.run(_cfg(tmp_path, [early, late]), now=datetime(2026, 6, 8, 13))  # Mon 13:00 UTC
    import json
    saved = json.loads((tmp_path / "state.json").read_text())
    assert "early" in saved and "late" not in saved   # only the 13:00 theme fired
    assert len(sent) == 1


def test_run_skips_theme_on_right_day_wrong_hour(tmp_path, monkeypatch):
    sent = []
    _patch(monkeypatch, [_repo(1, 10)], sent)
    theme = Theme(key="t", name="T", emoji="", query="q", count=1, at=((0, 13),))  # Mon 13
    main.run(_cfg(tmp_path, [theme]), now=datetime(2026, 6, 8, 16))  # Mon 16:00 UTC
    assert sent == []
    assert not (tmp_path / "state.json").exists()


def test_run_fires_themes_without_at_on_any_run(tmp_path, monkeypatch):
    sent = []
    _patch(monkeypatch, [_repo(1, 10)], sent)
    always = Theme(key="always", name="A", emoji="", query="q", count=1)   # at=None
    main.run(_cfg(tmp_path, [always]), now=datetime(2026, 6, 8, 16))
    import json
    assert "always" in json.loads((tmp_path / "state.json").read_text())


def test_run_throttles_sends_to_avoid_flooding(tmp_path, monkeypatch):
    slept = []
    monkeypatch.setattr(main.time, "sleep", lambda s: slept.append(s))
    sent = []
    monkeypatch.setattr(main, "search_repos",
                        lambda query, **k: [_repo(1, 10)] if "AAA" in query else [_repo(2, 10)])
    monkeypatch.setattr(main, "readme_first_line", lambda *a, **k: "")
    monkeypatch.setattr(main, "send_message", lambda *a, **k: sent.append(a[2]) or {"ok": True})
    a = Theme(key="a", name="A", emoji="", query="AAA", count=1)
    b = Theme(key="b", name="B", emoji="", query="BBB", count=1)
    main.run(_cfg(tmp_path, [a, b], delay=5), now=datetime(2026, 6, 4))
    assert len(sent) == 2       # two themes, one message each
    assert slept == [5]         # exactly one inter-message pause (none before the first send)


def test_run_mirrors_each_message_to_slack(tmp_path, monkeypatch):
    sent_tg, sent_slack = [], []
    monkeypatch.setattr(main, "search_repos", lambda *a, **k: [_repo(1, 10)])
    monkeypatch.setattr(main, "readme_first_line", lambda *a, **k: "")
    monkeypatch.setattr(main, "send_message", lambda *a, **k: sent_tg.append(a[2]) or {"ok": True})
    monkeypatch.setattr(main, "send_slack_message",
                        lambda token, channel, m, **k: sent_slack.append((token, channel, m)) or True)
    theme = Theme(key="t", name="T", emoji="", query="q", count=1)
    cfg = Config("tok", "-100", "", str(tmp_path), [theme], "",
                 slack_bot_token="xoxb", slack_channel_id="C1")
    main.run(cfg, now=datetime(2026, 6, 8))
    assert len(sent_tg) == 1
    assert sent_slack == [("xoxb", "C1", sent_tg[0])]   # same message, mirrored with the Slack creds


def test_run_alerts_when_llm_degraded(tmp_path, monkeypatch):
    alerts = []
    monkeypatch.setattr(main, "search_repos", lambda *a, **k: [_repo(1, 10)])
    monkeypatch.setattr(main, "readme_first_line", lambda *a, **k: "")
    monkeypatch.setattr(main, "readme_excerpt", lambda *a, **k: "")
    monkeypatch.setattr(main, "make_titles", lambda repos, **k: ["T"])
    monkeypatch.setattr(main, "make_summaries", lambda repos, excerpts, **k: [None])
    monkeypatch.setattr(main, "send_message", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(main, "send_slack_message", lambda *a, **k: False)
    # whole curator chain (incl. base) down -> genuinely stars-only, degraded run
    monkeypatch.setattr(main, "resolve_curator", lambda *a, **k: (None, ["gemma3:12b"]))
    monkeypatch.setattr(main, "send_alert", lambda token, chat, text, **k: alerts.append(text) or True)
    cfg = Config("tok", "-100", "", str(tmp_path),
                 [Theme(key="t", name="T", emoji="", query="q", count=1)],
                 "http://x", alert_chat_id="d")   # ollama_host set => pre-flight runs
    main.run(cfg, now=datetime(2026, 6, 8))
    assert alerts and "Ollama" in alerts[0]


def test_run_alerts_on_theme_failures(tmp_path, monkeypatch):
    alerts = []
    def boom(*a, **k):
        raise RuntimeError("search down")
    monkeypatch.setattr(main, "search_repos", boom)
    monkeypatch.setattr(main, "send_alert", lambda token, chat, text, **k: alerts.append(text) or True)
    cfg = _cfg(tmp_path, [Theme(key="t", name="T", emoji="", query="q", count=1)])  # ollama_host=""
    main.run(cfg, now=datetime(2026, 6, 8))
    assert alerts and "failed" in alerts[0]


def test_run_uses_llm_summaries_in_output(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(main, "search_repos", lambda *a, **k: [_repo(1, 10)])
    monkeypatch.setattr(main, "readme_first_line", lambda *a, **k: "")
    monkeypatch.setattr(main, "readme_excerpt", lambda *a, **k: "readme stuff")
    monkeypatch.setattr(main, "make_titles", lambda repos, **k: ["Title"])
    monkeypatch.setattr(main, "make_summaries", lambda repos, excerpts, **k: ["LLM blurb here."])
    monkeypatch.setattr(main, "send_message", lambda *a, **k: sent.append(a[2]) or {"ok": True})
    theme = Theme(key="t", name="T", emoji="", query="q", count=1)
    main.run(_cfg(tmp_path, [theme], ollama="http://x"), now=datetime(2026, 6, 4))
    assert any("LLM blurb here." in m for m in sent)


def test_run_quiet_slot_when_nothing_clears_quality_bar(tmp_path, monkeypatch, caplog):
    caplog.set_level("INFO")   # the "bot" logger defaults to WARNING under pytest
    sent = []
    _patch(monkeypatch, [_repo(1, 10)], sent)
    monkeypatch.setattr(main, "rank", lambda repos, theme, **k: [])   # scored, none above bar
    theme = Theme(key="t", name="T", emoji="", query="q", count=3)
    failures = main.run(_cfg(tmp_path, [theme]), now=datetime(2026, 6, 8, 13))
    assert failures == 0 and sent == []          # not a failure, no message
    assert "quality bar" in caplog.text


def test_run_passes_curator_whys_to_summaries(tmp_path, monkeypatch):
    from bot.ranker import Pick
    seen = {}
    monkeypatch.setattr(main, "search_repos", lambda *a, **k: [_repo(1, 10)])
    monkeypatch.setattr(main, "readme_first_line", lambda *a, **k: "")
    monkeypatch.setattr(main, "readme_excerpt", lambda *a, **k: "ex")
    monkeypatch.setattr(main, "rank",
                        lambda repos, theme, **k: [Pick(repos[0], "novel rust db")])
    monkeypatch.setattr(main, "make_titles", lambda repos, **k: ["T"])
    def fake_summaries(repos, excerpts, whys=None, **k):
        seen["whys"] = whys
        return [None]
    monkeypatch.setattr(main, "make_summaries", fake_summaries)
    monkeypatch.setattr(main, "send_message", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(main, "send_slack_message", lambda *a, **k: False)
    monkeypatch.setattr(main, "resolve_curator", lambda *a, **k: ("m", []))
    theme = Theme(key="t", name="T", emoji="", query="q", count=1)
    main.run(_cfg(tmp_path, [theme], ollama="http://x"), now=datetime(2026, 6, 8, 13))
    assert seen["whys"] == ["novel rust db"]


def test_run_heads_up_alert_on_curator_fallback(tmp_path, monkeypatch):
    # primary curator down but a fallback worked: run is NOT degraded, but a heads-up DM fires
    alerts = []
    monkeypatch.setattr(main, "search_repos", lambda *a, **k: [_repo(1, 10)])
    monkeypatch.setattr(main, "readme_first_line", lambda *a, **k: "")
    monkeypatch.setattr(main, "readme_excerpt", lambda *a, **k: "")
    monkeypatch.setattr(main, "make_titles", lambda repos, **k: ["T"])
    monkeypatch.setattr(main, "make_summaries", lambda repos, excerpts, **k: [None])
    monkeypatch.setattr(main, "send_message", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(main, "send_slack_message", lambda *a, **k: False)
    monkeypatch.setattr(main, "resolve_curator",
                        lambda *a, **k: ("gpt-oss:120b", ["deepseek-v3.1:671b"]))
    monkeypatch.setattr(main, "send_alert", lambda token, chat, text, **k: alerts.append(text) or True)
    cfg = Config("tok", "-100", "", str(tmp_path),
                 [Theme(key="t", name="T", emoji="", query="q", count=1)],
                 "http://x", alert_chat_id="d")
    failures = main.run(cfg, now=datetime(2026, 6, 8, 13))
    assert failures == 0 and len(alerts) == 1
    assert "deepseek-v3.1:671b" in alerts[0] and "gpt-oss:120b" in alerts[0]
    assert "Ollama unreachable" not in alerts[0]      # heads-up, not the degraded alert


def test_run_no_alert_when_primary_curator_works(tmp_path, monkeypatch):
    alerts = []
    monkeypatch.setattr(main, "search_repos", lambda *a, **k: [_repo(1, 10)])
    monkeypatch.setattr(main, "readme_first_line", lambda *a, **k: "")
    monkeypatch.setattr(main, "readme_excerpt", lambda *a, **k: "")
    monkeypatch.setattr(main, "make_titles", lambda repos, **k: ["T"])
    monkeypatch.setattr(main, "make_summaries", lambda repos, excerpts, **k: [None])
    monkeypatch.setattr(main, "send_message", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(main, "send_slack_message", lambda *a, **k: False)
    monkeypatch.setattr(main, "resolve_curator", lambda *a, **k: ("deepseek-v3.1:671b", []))
    monkeypatch.setattr(main, "send_alert", lambda token, chat, text, **k: alerts.append(text) or True)
    cfg = Config("tok", "-100", "", str(tmp_path),
                 [Theme(key="t", name="T", emoji="", query="q", count=1)],
                 "http://x", alert_chat_id="d")
    main.run(cfg, now=datetime(2026, 6, 8, 13))
    assert alerts == []      # primary worked -> silent


def test_run_routes_curator_model_to_rank_and_summaries_only(tmp_path, monkeypatch):
    from bot.ranker import Pick
    models = {}
    monkeypatch.setattr(main, "search_repos", lambda *a, **k: [_repo(1, 10)])
    monkeypatch.setattr(main, "readme_first_line", lambda *a, **k: "")
    monkeypatch.setattr(main, "readme_excerpt", lambda *a, **k: "ex")
    def fake_rank(repos, theme, ollama_model="", **k):
        models["rank"] = ollama_model
        return [Pick(repos[0], "w")]
    monkeypatch.setattr(main, "rank", fake_rank)
    def fake_summaries(repos, excerpts, whys=None, model="", **k):
        models["summaries"] = model
        return [None]
    monkeypatch.setattr(main, "make_summaries", fake_summaries)
    def fake_titles(repos, model="", **k):
        models["titles"] = model
        return ["T"]
    monkeypatch.setattr(main, "make_titles", fake_titles)
    monkeypatch.setattr(main, "send_message", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(main, "send_slack_message", lambda *a, **k: False)
    # resolved curator routes to rank + summaries; titles/translation stay on the base model
    monkeypatch.setattr(main, "resolve_curator", lambda *a, **k: ("qwen3-next:80b", []))
    theme = Theme(key="t", name="T", emoji="", query="q", count=1)
    cfg = Config("tok", "-100", "", str(tmp_path), [theme], "http://x",
                 ollama_model="gemma3:12b", ollama_curator_models=("qwen3-next:80b",))
    main.run(cfg, now=datetime(2026, 6, 8, 13))
    assert models == {"rank": "qwen3-next:80b", "summaries": "qwen3-next:80b",
                      "titles": "gemma3:12b"}


def test_run_merges_and_dedupes_multi_query_themes(tmp_path, monkeypatch):
    sent = []
    def fake_search(query, **k):
        # both queries surface repo 1; each contributes one unique repo
        return [_repo(1, 100), _repo(2, 50)] if "QA" in query else [_repo(1, 100), _repo(3, 80)]
    monkeypatch.setattr(main, "search_repos", lambda query, **k: fake_search(query))
    monkeypatch.setattr(main, "readme_first_line", lambda *a, **k: "")
    monkeypatch.setattr(main, "send_message", lambda *a, **k: sent.append(a[2]) or {"ok": True})
    theme = Theme(key="t", name="T", emoji="", query=("QA", "QB"), count=5)
    main.run(_cfg(tmp_path, [theme]), now=datetime(2026, 6, 8, 13))
    import json
    saved = json.loads((tmp_path / "state.json").read_text())
    assert sorted(saved["t"]) == [1, 2, 3]   # merged, repo 1 deduped


def test_run_warns_when_slack_mirror_fails(tmp_path, monkeypatch, caplog):
    caplog.set_level("WARNING")
    monkeypatch.setattr(main, "search_repos", lambda *a, **k: [_repo(1, 10)])
    monkeypatch.setattr(main, "readme_first_line", lambda *a, **k: "")
    monkeypatch.setattr(main, "send_message", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(main, "send_slack_message", lambda *a, **k: False)
    theme = Theme(key="t", name="T", emoji="", query="q", count=1)
    cfg = Config("tok", "-100", "", str(tmp_path), [theme], "",
                 slack_bot_token="xoxb", slack_channel_id="C1")
    main.run(cfg, now=datetime(2026, 6, 8, 13))
    assert "slack mirror failed" in caplog.text.lower()


def test_run_no_slack_warning_when_slack_unconfigured(tmp_path, monkeypatch, caplog):
    caplog.set_level("WARNING")
    sent = []
    _patch(monkeypatch, [_repo(1, 10)], sent)
    theme = Theme(key="t", name="T", emoji="", query="q", count=1)
    main.run(_cfg(tmp_path, [theme]), now=datetime(2026, 6, 8, 13))
    assert "slack" not in caplog.text.lower()
