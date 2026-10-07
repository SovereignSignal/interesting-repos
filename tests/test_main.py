from datetime import datetime, date
import bot.main as main
from bot.config import Config, Theme
from bot.github import Repo
from bot import starsnap


def _seed_snapshot(tmp_path, day, mapping):
    starsnap.save_snapshot(str(tmp_path), day, mapping)


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
    monkeypatch.setattr(main, "readme_parts", lambda *a, **k: ("", ""))
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
    monkeypatch.setattr(main, "readme_parts", lambda *a, **k: ("", "readme stuff"))
    monkeypatch.setattr(main, "make_titles", lambda repos, **k: ["Title"])
    monkeypatch.setattr(main, "make_summaries", lambda repos, excerpts, **k: ["LLM blurb here."])
    monkeypatch.setattr(main, "send_message", lambda *a, **k: sent.append(a[2]) or {"ok": True})
    # curator resolves to the base model -> no real pre-flight pings (avoids retry backoff)
    monkeypatch.setattr(main, "resolve_curator", lambda *a, **k: ("gemma4:31b", []))
    monkeypatch.setattr(main, "llm_reachable", lambda *a, **k: True)
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


def test_run_logs_phase1_pool_funnel(tmp_path, monkeypatch, caplog):
    caplog.set_level("INFO")
    sent = []
    _patch(monkeypatch, [_repo(1, 10), _repo(2, 20)], sent)
    theme = Theme(key="t", name="T", emoji="", query="q", count=2)
    main.run(_cfg(tmp_path, [theme]), now=datetime(2026, 5, 26))
    assert "theme t: searched=2 after_clean=2 after_unsent=2 after_cap=2 picked=2" in caplog.text


def test_run_pool_funnel_counts_unsent_drop(tmp_path, monkeypatch, caplog):
    caplog.set_level("INFO")
    sent = []
    _patch(monkeypatch, [_repo(1, 10), _repo(2, 20)], sent)
    (tmp_path / "state.json").write_text('{"t": [1]}')
    theme = Theme(key="t", name="T", emoji="", query="q", count=5)
    main.run(_cfg(tmp_path, [theme]), now=datetime(2026, 5, 26))
    assert "theme t: searched=2 after_clean=2 after_unsent=1 after_cap=1 picked=1" in caplog.text


def test_run_passes_curator_whys_to_summaries(tmp_path, monkeypatch):
    from bot.ranker import Pick
    seen = {}
    monkeypatch.setattr(main, "search_repos", lambda *a, **k: [_repo(1, 10)])
    monkeypatch.setattr(main, "readme_first_line", lambda *a, **k: "")
    monkeypatch.setattr(main, "readme_parts", lambda *a, **k: ("", "ex"))
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
    monkeypatch.setattr(main, "llm_reachable", lambda *a, **k: True)  # base fine -> no retry backoff
    theme = Theme(key="t", name="T", emoji="", query="q", count=1)
    main.run(_cfg(tmp_path, [theme], ollama="http://x"), now=datetime(2026, 6, 8, 13))
    assert seen["whys"] == ["novel rust db"]


def test_run_heads_up_alert_on_curator_fallback(tmp_path, monkeypatch):
    # primary curator down but a fallback worked: run is NOT degraded, but a heads-up DM fires
    alerts = []
    monkeypatch.setattr(main, "search_repos", lambda *a, **k: [_repo(1, 10)])
    monkeypatch.setattr(main, "readme_first_line", lambda *a, **k: "")
    monkeypatch.setattr(main, "readme_parts", lambda *a, **k: ("", ""))
    monkeypatch.setattr(main, "make_titles", lambda repos, **k: ["T"])
    monkeypatch.setattr(main, "make_summaries", lambda repos, excerpts, **k: [None])
    monkeypatch.setattr(main, "send_message", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(main, "send_slack_message", lambda *a, **k: False)
    monkeypatch.setattr(main, "resolve_curator",
                        lambda *a, **k: ("gpt-oss:120b", ["deepseek-v3.1:671b"]))
    monkeypatch.setattr(main, "llm_reachable", lambda *a, **k: True)  # base healthy: isolate curator alert
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
    monkeypatch.setattr(main, "readme_parts", lambda *a, **k: ("", ""))
    monkeypatch.setattr(main, "make_titles", lambda repos, **k: ["T"])
    monkeypatch.setattr(main, "make_summaries", lambda repos, excerpts, **k: [None])
    monkeypatch.setattr(main, "send_message", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(main, "send_slack_message", lambda *a, **k: False)
    monkeypatch.setattr(main, "resolve_curator", lambda *a, **k: ("deepseek-v3.1:671b", []))
    monkeypatch.setattr(main, "llm_reachable", lambda *a, **k: True)  # base healthy too -> fully silent
    monkeypatch.setattr(main, "send_alert", lambda token, chat, text, **k: alerts.append(text) or True)
    cfg = Config("tok", "-100", "", str(tmp_path),
                 [Theme(key="t", name="T", emoji="", query="q", count=1)],
                 "http://x", alert_chat_id="d")
    main.run(cfg, now=datetime(2026, 6, 8, 13))
    assert alerts == []      # primary worked -> silent


def test_run_heads_up_alert_when_base_model_unavailable(tmp_path, monkeypatch):
    # The curator resolved to a non-base model, so resolve_curator never pinged the base
    # model (OLLAMA_MODEL, drives translation). It's retired. Translation must run on
    # the live curator and a heads-up DM must fire; the run is NOT degraded (curation
    # is fine). Titles are the repo name either way. This is the 2026-07-15 gemma3:12b
    # retirement. Host is not ollama.com, so no -cloud alias is tried.
    alerts, models = [], {}
    monkeypatch.setattr(main, "search_repos", lambda *a, **k: [_repo(1, 10)])
    monkeypatch.setattr(main, "readme_first_line", lambda *a, **k: "")
    monkeypatch.setattr(main, "readme_parts", lambda *a, **k: ("", ""))
    def fake_translate(text, model="", **k):
        models["translation"] = model
        return text
    monkeypatch.setattr(main, "translate_to_english", fake_translate)
    monkeypatch.setattr(main, "make_summaries", lambda repos, excerpts, **k: [None])
    monkeypatch.setattr(main, "send_message", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(main, "send_slack_message", lambda *a, **k: False)
    monkeypatch.setattr(main, "resolve_curator", lambda *a, **k: ("gpt-oss:120b", []))
    # base model gemma3:12b is down; every other model is reachable
    monkeypatch.setattr(main, "llm_reachable", lambda host, model, *a, **k: model != "gemma3:12b")
    monkeypatch.setattr(main, "send_alert", lambda token, chat, text, **k: alerts.append(text) or True)
    cfg = Config("tok", "-100", "", str(tmp_path),
                 [Theme(key="t", name="T", emoji="", query="q", count=1)],
                 "http://x", ollama_model="gemma3:12b",
                 ollama_curator_models=("gpt-oss:120b",), alert_chat_id="d")
    failures = main.run(cfg, now=datetime(2026, 6, 8, 13))
    assert failures == 0 and len(alerts) == 1
    assert models["translation"] == "gpt-oss:120b"    # curator, not a dead base
    assert "gemma3:12b" in alerts[0] and "OLLAMA_MODEL" in alerts[0]
    assert "ran on gpt-oss:120b" in alerts[0]
    assert "deterministic" not in alerts[0]
    assert "Ollama unreachable" not in alerts[0]      # heads-up, not the degraded alert


def test_run_no_base_alert_when_cloud_alias_reachable(tmp_path, monkeypatch):
    # Prod leftover: OLLAMA_MODEL=gemma4:31b on ollama.com. The local tag 410s; the
    # hosted sibling gemma4:31b-cloud is fine. Translation must use the alias and stay silent
    # — this is the alert that used to page every cron. Curator is a different model.
    alerts, models = [], {}
    monkeypatch.setattr(main, "search_repos", lambda *a, **k: [_repo(1, 10)])
    monkeypatch.setattr(main, "readme_first_line", lambda *a, **k: "")
    monkeypatch.setattr(main, "readme_parts", lambda *a, **k: ("", ""))
    def fake_translate(text, model="", **k):
        models["translation"] = model
        return text
    monkeypatch.setattr(main, "translate_to_english", fake_translate)
    monkeypatch.setattr(main, "make_summaries", lambda repos, excerpts, **k: [None])
    monkeypatch.setattr(main, "send_message", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(main, "send_slack_message", lambda *a, **k: False)
    monkeypatch.setattr(main, "resolve_curator", lambda *a, **k: ("deepseek-v4-pro", []))
    monkeypatch.setattr(main, "llm_reachable",
                        lambda host, model, *a, **k: model != "gemma4:31b")
    monkeypatch.setattr(main, "send_alert", lambda token, chat, text, **k: alerts.append(text) or True)
    cfg = Config("tok", "-100", "", str(tmp_path),
                 [Theme(key="t", name="T", emoji="", query="q", count=1)],
                 "https://ollama.com", ollama_model="gemma4:31b",
                 ollama_curator_models=("deepseek-v4-pro",), alert_chat_id="d")
    main.run(cfg, now=datetime(2026, 6, 8, 13))
    assert alerts == []
    assert models["translation"] == "gemma4:31b-cloud"


def test_run_heads_up_when_base_and_cloud_alias_dead_uses_curator(tmp_path, monkeypatch):
    # Both the configured tag and its -cloud sibling are gone (true retirement).
    # Translation runs on the curator; a heads-up fires. Not a degraded/stars-only run.
    alerts, models = [], {}
    monkeypatch.setattr(main, "search_repos", lambda *a, **k: [_repo(1, 10)])
    monkeypatch.setattr(main, "readme_first_line", lambda *a, **k: "")
    monkeypatch.setattr(main, "readme_parts", lambda *a, **k: ("", ""))
    def fake_translate(text, model="", **k):
        models["translation"] = model
        return text
    monkeypatch.setattr(main, "translate_to_english", fake_translate)
    monkeypatch.setattr(main, "make_summaries", lambda repos, excerpts, **k: [None])
    monkeypatch.setattr(main, "send_message", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(main, "send_slack_message", lambda *a, **k: False)
    monkeypatch.setattr(main, "resolve_curator", lambda *a, **k: ("deepseek-v4-pro", []))
    dead = {"gemma4:31b", "gemma4:31b-cloud"}
    monkeypatch.setattr(main, "llm_reachable",
                        lambda host, model, *a, **k: model not in dead)
    monkeypatch.setattr(main, "send_alert", lambda token, chat, text, **k: alerts.append(text) or True)
    cfg = Config("tok", "-100", "", str(tmp_path),
                 [Theme(key="t", name="T", emoji="", query="q", count=1)],
                 "https://ollama.com", ollama_model="gemma4:31b",
                 ollama_curator_models=("deepseek-v4-pro",), alert_chat_id="d")
    failures = main.run(cfg, now=datetime(2026, 6, 8, 13))
    assert failures == 0 and len(alerts) == 1
    assert models["translation"] == "deepseek-v4-pro"
    assert "ran on deepseek-v4-pro" in alerts[0]
    assert "deterministic" not in alerts[0]
    assert "Ollama unreachable" not in alerts[0]


def test_run_no_base_alert_when_base_model_reachable(tmp_path, monkeypatch):
    # curator on a non-base rung AND the base model is reachable -> no base heads-up.
    alerts = []
    monkeypatch.setattr(main, "search_repos", lambda *a, **k: [_repo(1, 10)])
    monkeypatch.setattr(main, "readme_first_line", lambda *a, **k: "")
    monkeypatch.setattr(main, "readme_parts", lambda *a, **k: ("", ""))
    monkeypatch.setattr(main, "make_titles", lambda repos, **k: ["T"])
    monkeypatch.setattr(main, "make_summaries", lambda repos, excerpts, **k: [None])
    monkeypatch.setattr(main, "send_message", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(main, "send_slack_message", lambda *a, **k: False)
    monkeypatch.setattr(main, "resolve_curator", lambda *a, **k: ("gpt-oss:120b", []))
    monkeypatch.setattr(main, "llm_reachable", lambda host, model, *a, **k: True)
    monkeypatch.setattr(main, "send_alert", lambda token, chat, text, **k: alerts.append(text) or True)
    cfg = Config("tok", "-100", "", str(tmp_path),
                 [Theme(key="t", name="T", emoji="", query="q", count=1)],
                 "http://x", ollama_model="gemma3:12b",
                 ollama_curator_models=("gpt-oss:120b",), alert_chat_id="d")
    main.run(cfg, now=datetime(2026, 6, 8, 13))
    assert alerts == []      # base reachable -> silent


def test_run_no_base_alert_on_fully_degraded_run(tmp_path, monkeypatch):
    # whole chain down (curator is None) -> the degraded alert already says "no titles/
    # translation"; the base heads-up must NOT also fire (guarded by `not degraded`).
    alerts = []
    monkeypatch.setattr(main, "search_repos", lambda *a, **k: [_repo(1, 10)])
    monkeypatch.setattr(main, "readme_first_line", lambda *a, **k: "")
    monkeypatch.setattr(main, "make_titles", lambda repos, **k: ["T"])
    monkeypatch.setattr(main, "send_message", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(main, "send_slack_message", lambda *a, **k: False)
    monkeypatch.setattr(main, "resolve_curator", lambda *a, **k: (None, ["gemma3:12b"]))
    # llm_reachable should never even be consulted for the base once degraded; make it loud if it is
    monkeypatch.setattr(main, "llm_reachable",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("base pinged while degraded")))
    monkeypatch.setattr(main, "send_alert", lambda token, chat, text, **k: alerts.append(text) or True)
    cfg = Config("tok", "-100", "", str(tmp_path),
                 [Theme(key="t", name="T", emoji="", query="q", count=1)],
                 "http://x", ollama_model="gemma3:12b", alert_chat_id="d")
    main.run(cfg, now=datetime(2026, 6, 8, 13))
    assert len(alerts) == 1 and "Ollama unreachable" in alerts[0]


def test_run_routes_curator_model_to_rank_and_summaries_only(tmp_path, monkeypatch):
    from bot.ranker import Pick
    models = {}
    monkeypatch.setattr(main, "search_repos", lambda *a, **k: [_repo(1, 10)])
    monkeypatch.setattr(main, "readme_first_line", lambda *a, **k: "")
    monkeypatch.setattr(main, "readme_parts", lambda *a, **k: ("", "ex"))
    def fake_rank(repos, theme, ollama_model="", **k):
        models["rank"] = ollama_model
        return [Pick(repos[0], "w")]
    monkeypatch.setattr(main, "rank", fake_rank)
    def fake_summaries(repos, excerpts, whys=None, model="", **k):
        models["summaries"] = model
        return [None]
    monkeypatch.setattr(main, "make_summaries", fake_summaries)
    def fake_translate(text, model="", **k):
        models["translation"] = model
        return text
    monkeypatch.setattr(main, "translate_to_english", fake_translate)
    monkeypatch.setattr(main, "send_message", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(main, "send_slack_message", lambda *a, **k: False)
    # resolved curator routes to rank + summaries; translation stays on the base model
    monkeypatch.setattr(main, "resolve_curator", lambda *a, **k: ("qwen3-next:80b", []))
    monkeypatch.setattr(main, "llm_reachable", lambda *a, **k: True)  # base fine -> no retry backoff
    theme = Theme(key="t", name="T", emoji="", query="q", count=1)
    cfg = Config("tok", "-100", "", str(tmp_path), [theme], "http://x",
                 ollama_model="gemma3:12b", ollama_curator_models=("qwen3-next:80b",))
    main.run(cfg, now=datetime(2026, 6, 8, 13))
    assert models == {"rank": "qwen3-next:80b", "summaries": "qwen3-next:80b",
                      "translation": "gemma3:12b"}


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


def test_run_writes_a_snapshot_of_searched_repos(tmp_path, monkeypatch):
    _patch(monkeypatch, [_repo(1, 250), _repo(2, 60)], [])
    theme = Theme(key="t", name="T", emoji="", query="q", count=2)
    main.run(_cfg(tmp_path, [theme]), now=datetime(2026, 6, 8, 13))
    snap = starsnap.load_snapshot(str(tmp_path), date(2026, 6, 8))
    assert snap == {1: 250, 2: 60}


def test_run_dry_run_writes_no_snapshot(tmp_path, monkeypatch):
    _patch(monkeypatch, [_repo(1, 10)], [])
    theme = Theme(key="t", name="T", emoji="", query="q", count=1)
    main.run(_cfg(tmp_path, [theme]), now=datetime(2026, 6, 8, 13), dry_run=True)
    assert not (tmp_path / "starsnap").exists()


def test_run_snapshot_unions_across_themes_in_one_run(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "search_repos",
                        lambda query, **k: [_repo(1, 10)] if "AAA" in query else [_repo(2, 20)])
    monkeypatch.setattr(main, "readme_first_line", lambda *a, **k: "")
    monkeypatch.setattr(main, "send_message", lambda *a, **k: {"ok": True})
    a = Theme(key="a", name="A", emoji="", query="AAA", count=1)
    b = Theme(key="b", name="B", emoji="", query="BBB", count=1)
    main.run(_cfg(tmp_path, [a, b]), now=datetime(2026, 6, 8, 13))
    snap = starsnap.load_snapshot(str(tmp_path), date(2026, 6, 8))
    assert snap == {1: 10, 2: 20}


def test_run_delta_theme_orders_candidates_by_growth_and_drops_unbaselined(tmp_path, monkeypatch):
    # baseline a week ago: repo 1 grew most (100->250), repo 3 has no baseline
    _seed_snapshot(tmp_path, date(2026, 6, 1), {1: 100, 2: 50})
    seen = {}
    def fake_rank(repos, theme, **k):
        seen["order"] = [r.id for r in repos]
        from bot.ranker import Pick
        return [Pick(r) for r in repos[:2]]
    monkeypatch.setattr(main, "search_repos",
                        lambda *a, **k: [_repo(3, 9999), _repo(1, 250), _repo(2, 60)])
    monkeypatch.setattr(main, "readme_first_line", lambda *a, **k: "")
    monkeypatch.setattr(main, "rank", fake_rank)
    monkeypatch.setattr(main, "send_message", lambda *a, **k: {"ok": True})
    theme = Theme(key="m", name="M", emoji="🚀", query="q", count=2, delta_days=7)
    main.run(_cfg(tmp_path, [theme]), now=datetime(2026, 6, 8, 13))
    assert seen["order"] == [1, 2]      # delta 150, 10; repo 3 (no baseline) dropped


def test_run_delta_theme_annotates_growth_in_message(tmp_path, monkeypatch):
    _seed_snapshot(tmp_path, date(2026, 6, 1), {1: 100})   # grew 100 -> 250 (+150)
    sent = []
    from bot.ranker import Pick
    monkeypatch.setattr(main, "search_repos", lambda *a, **k: [_repo(1, 250)])
    monkeypatch.setattr(main, "readme_first_line", lambda *a, **k: "")
    monkeypatch.setattr(main, "rank", lambda repos, theme, **k: [Pick(r) for r in repos])
    monkeypatch.setattr(main, "send_message", lambda *a, **k: sent.append(a[2]) or {"ok": True})
    theme = Theme(key="m", name="M", emoji="🚀", query="q", count=1, delta_days=7)
    main.run(_cfg(tmp_path, [theme]), now=datetime(2026, 6, 8, 13))
    assert any("+150★ this week" in m for m in sent)


def test_run_delta_theme_cold_start_is_quiet(tmp_path, monkeypatch, caplog):
    caplog.set_level("INFO")
    sent = []
    _patch(monkeypatch, [_repo(1, 250), _repo(2, 60)], sent)   # no baseline seeded
    theme = Theme(key="m", name="M", emoji="🚀", query="q", count=2, delta_days=7)
    failures = main.run(_cfg(tmp_path, [theme]), now=datetime(2026, 6, 8, 13))
    assert failures == 0 and sent == []            # quiet slot, no failure, no alert
    assert "baseline_days=0" in caplog.text
    assert "dropped_no_baseline=2" in caplog.text


def test_run_survives_snapshot_write_failure(tmp_path, monkeypatch, caplog):
    caplog.set_level("WARNING")
    sent = []
    _patch(monkeypatch, [_repo(1, 10)], sent)
    # the snapshot store is DISPOSABLE — a write failure must never break the digest
    def boom(state_dir, day, mapping):
        raise OSError("disk full")
    monkeypatch.setattr(main, "save_snapshot", boom)
    theme = Theme(key="t", name="T", emoji="", query="q", count=1)
    failures = main.run(_cfg(tmp_path, [theme]), now=datetime(2026, 6, 8, 13))
    assert failures == 0           # not counted as a theme failure
    assert len(sent) == 1          # Phase 2 still delivered
    assert "snapshot" in caplog.text.lower()   # warned, not crashed


def test_run_ai_cap_limits_standalone_ai_tools(tmp_path, monkeypatch):
    sent = []
    a1 = Repo(1, "a/gstack", "u", "Claude Code setup", 100, "Py", [], False, False)
    a2 = Repo(2, "b/harness", "u", "an llm gateway", 90, "Py", [], False, False)
    a3 = Repo(3, "c/agent", "u", "coding agent runtime", 80, "Py", [], False, False)
    tool = Repo(4, "d/realdb", "u", "a database engine", 70, "Py", [], False, False)
    monkeypatch.setattr(main, "search_repos", lambda *a, **k: [a1, a2, a3, tool])
    monkeypatch.setattr(main, "readme_first_line", lambda *a, **k: "")
    monkeypatch.setattr(main, "send_message", lambda *a, **k: sent.append(a[2]) or {"ok": True})
    theme = Theme(key="dev", name="D", emoji="", query="q", count=5, ai_cap=2)
    main.run(_cfg(tmp_path, [theme]), now=datetime(2026, 6, 4))
    import json
    saved = json.loads((tmp_path / "state.json").read_text())
    assert sorted(saved["dev"]) == [1, 2, 4]   # two AI + the non-AI; a3 dropped


def test_run_skips_globally_posted_repos(tmp_path, monkeypatch):
    sent = []
    (tmp_path / "state.json").write_text('{"_posted": [1], "other": [1]}')
    _patch(monkeypatch, [_repo(1, 10), _repo(2, 20)], sent)
    theme = Theme(key="t", name="T", emoji="", query="q", count=5)
    main.run(_cfg(tmp_path, [theme]), now=datetime(2026, 6, 4))
    import json
    saved = json.loads((tmp_path / "state.json").read_text())
    assert saved["t"] == [2]
    assert 2 in saved["_posted"] and 1 in saved["_posted"]


def test_run_movers_exempt_from_global_posted(tmp_path, monkeypatch):
    _seed_snapshot(tmp_path, date(2026, 6, 1), {1: 100})
    sent = []
    (tmp_path / "state.json").write_text('{"_posted": [1], "trending": [1]}')
    from bot.ranker import Pick
    monkeypatch.setattr(main, "search_repos", lambda *a, **k: [_repo(1, 250)])
    monkeypatch.setattr(main, "readme_first_line", lambda *a, **k: "")
    monkeypatch.setattr(main, "rank", lambda repos, theme, **k: [Pick(r) for r in repos])
    monkeypatch.setattr(main, "send_message", lambda *a, **k: sent.append(a[2]) or {"ok": True})
    theme = Theme(key="movers", name="M", emoji="🚀", query="q", count=1, delta_days=7)
    main.run(_cfg(tmp_path, [theme]), now=datetime(2026, 6, 8, 13))
    assert sent   # posted again as a mover despite _posted


def test_run_dry_run_does_not_persist_posted(tmp_path, monkeypatch):
    _patch(monkeypatch, [_repo(1, 10)], [])
    theme = Theme(key="t", name="T", emoji="", query="q", count=1)
    main.run(_cfg(tmp_path, [theme]), now=datetime(2026, 6, 4), dry_run=True)
    assert not (tmp_path / "state.json").exists()


def test_run_hides_velocity_badge_when_younger_than_two_days(tmp_path, monkeypatch):
    sent = []
    young = Repo(1, "a/1", "https://x/1", "desc", 88057, "Py", [], False, False,
                 created_at="2026-06-08T00:00:00Z")  # 0 days old
    old = Repo(2, "a/2", "https://x/2", "desc", 300, "Py", [], False, False,
               created_at="2026-06-01T00:00:00Z")     # 7 days old
    monkeypatch.setattr(main, "search_repos", lambda *a, **k: [young, old])
    monkeypatch.setattr(main, "readme_first_line", lambda *a, **k: "")
    monkeypatch.setattr(main, "send_message", lambda *a, **k: sent.append(a[2]) or {"ok": True})
    theme = Theme(key="t", name="T", emoji="", query="q", count=2)
    main.run(_cfg(tmp_path, [theme]), now=datetime(2026, 6, 8, 13))
    body = "\n".join(sent)
    assert "88,057★/day" not in body and "88057★/day" not in body
    assert "43★/day" in body   # 300/7


def test_run_cap_zero_drops_empty_metadata_readme_ai(tmp_path, monkeypatch):
    leak = Repo(1, "acme/untitled", "u", "", 100, "Py", [], False, False)
    keep = Repo(2, "b/db", "u", "a database engine", 50, "Py", [], False, False)
    monkeypatch.setattr(main, "search_repos", lambda *a, **k: [leak, keep])
    monkeypatch.setattr(main, "readme_first_line",
                        lambda full_name, **k: "A Claude Code skill pack" if "untitled" in full_name else "")
    monkeypatch.setattr(main, "send_message", lambda *a, **k: {"ok": True})
    theme = Theme(key="trending", name="T", emoji="📈", query="q", count=5,
                  agent_skill_cap=0, ai_cap=0)
    main.run(_cfg(tmp_path, [theme]), now=datetime(2026, 6, 4))
    import json
    saved = json.loads((tmp_path / "state.json").read_text())
    assert saved["trending"] == [2]


def test_run_watchlist_folds_into_snapshot(tmp_path, monkeypatch):
    def fake_search(query, **k):
        if "stars:>100" in query:
            return [_repo(99, 800)]
        return [_repo(1, 250)]
    monkeypatch.setattr(main, "search_repos", lambda query, **k: fake_search(query))
    monkeypatch.setattr(main, "readme_first_line", lambda *a, **k: "")
    monkeypatch.setattr(main, "send_message", lambda *a, **k: {"ok": True})
    theme = Theme(key="t", name="T", emoji="", query="topic:cli created:>x", count=1)
    main.run(_cfg(tmp_path, [theme]), now=datetime(2026, 6, 8, 13))
    snap = starsnap.load_snapshot(str(tmp_path), date(2026, 6, 8))
    assert snap[1] == 250 and snap[99] == 800


def test_run_fetches_second_page_when_ai_cap_set(tmp_path, monkeypatch):
    pages = []
    def fake_search(query, **k):
        pages.append(k.get("page", 1))
        return [_repo(k.get("page", 1), 10)]
    monkeypatch.setattr(main, "search_repos", lambda query, **k: fake_search(query, **k))
    monkeypatch.setattr(main, "readme_first_line", lambda *a, **k: "")
    monkeypatch.setattr(main, "send_message", lambda *a, **k: {"ok": True})
    theme = Theme(key="t", name="T", emoji="", query="q", count=5, ai_cap=2)
    main.run(_cfg(tmp_path, [theme]), now=datetime(2026, 6, 4))
    assert 1 in pages and 2 in pages


def _patch_llm_rank(monkeypatch, chat_reply, sent, alerts, curator=("m", [])):
    """Real rank(); chat_result is stubbed so the run never touches the network."""
    import bot.ranker as ranker
    monkeypatch.setattr(main, "search_repos", lambda *a, **k: [_repo(1, 10), _repo(2, 99)])
    monkeypatch.setattr(main, "readme_first_line", lambda *a, **k: "")
    monkeypatch.setattr(main, "readme_parts", lambda *a, **k: ("", ""))
    monkeypatch.setattr(main, "make_titles", lambda repos, **k: ["T"])
    monkeypatch.setattr(main, "make_summaries", lambda repos, excerpts, **k: [None])
    monkeypatch.setattr(main, "send_message", lambda *a, **k: sent.append(a[2]) or {"ok": True})
    monkeypatch.setattr(main, "send_alert",
                        lambda token, chat, text, **k: alerts.append((chat, text)) or True)
    monkeypatch.setattr(main, "resolve_curator", lambda *a, **k: curator)
    monkeypatch.setattr(main, "llm_reachable", lambda *a, **k: True)
    monkeypatch.setattr(ranker, "chat_result", lambda *a, **k: chat_reply)


def _llm_cfg(tmp_path):
    return Config("tok", "-100", "", str(tmp_path),
                  [Theme(key="t", name="T", emoji="", query="q", count=2, rank="llm")],
                  "http://x", alert_chat_id="dm-alerts", ollama_api_key="super-secret-key")


def test_run_alerts_on_unparseable_scoring_not_ran_on(tmp_path, monkeypatch, caplog):
    caplog.set_level("WARNING")
    sent, alerts = [], []
    _patch_llm_rank(monkeypatch, ("I cannot rank these, sorry.", None), sent, alerts,
                    curator=("gemma4:31b", ["deepseek-v4-pro:0813"]))
    failures = main.run(_llm_cfg(tmp_path), now=datetime(2026, 6, 8, 13))
    assert failures == 0 and sent   # stars fallback still posts
    assert len(alerts) == 1
    chat, text = alerts[0]
    assert chat == "dm-alerts"          # ALERT_CHAT_ID, not the digest channel
    assert "t (unparseable)" in text
    assert "LLM scoring failed" in text
    assert "ran on" not in text
    assert "Ollama unreachable" not in text
    assert "super-secret-key" not in text
    assert all("LLM scoring failed" not in m for m in sent)
    assert any("LLM scoring failed (unparseable)" in r.getMessage() for r in caplog.records)


def test_run_alerts_on_empty_scoring_content(tmp_path, monkeypatch, caplog):
    caplog.set_level("WARNING")
    sent, alerts = [], []
    _patch_llm_rank(monkeypatch, ("", "empty content"), sent, alerts,
                    curator=("gemma4:31b", ["deepseek-v4-pro:0813"]))
    failures = main.run(_llm_cfg(tmp_path), now=datetime(2026, 6, 8, 13))
    assert failures == 0 and len(alerts) == 1
    assert alerts[0][0] == "dm-alerts"
    assert "empty content" in alerts[0][1]
    assert "ran on" not in alerts[0][1]
    assert all("empty content" not in m for m in sent)
    assert any("LLM scoring failed (empty content)" in r.getMessage() for r in caplog.records)


def test_run_quiet_slot_does_not_fall_back_or_alert(tmp_path, monkeypatch, caplog):
    caplog.set_level("INFO")
    sent, alerts = [], []
    reply = '[{"i": 0, "score": 2, "why": "meh"}, {"i": 1, "score": 3, "why": "thin"}]'
    _patch_llm_rank(monkeypatch, (reply, None), sent, alerts,
                    curator=("deepseek-v4-pro:0813", []))
    failures = main.run(_llm_cfg(tmp_path), now=datetime(2026, 6, 8, 13))
    assert failures == 0 and sent == [] and alerts == []
    assert "quality bar" in caplog.text
    assert "falling back to stars" not in caplog.text
    assert "Ollama unreachable" not in caplog.text


def test_run_heads_up_when_scores_parsed_on_fallback_curator(tmp_path, monkeypatch):
    # Scores actually parsed, so the "ran on {model}" heads-up is still the right DM.
    sent, alerts = [], []
    reply = ('[{"i": 0, "score": 9, "why": "novel"}, {"i": 1, "score": 8, "why": "solid"}]')
    _patch_llm_rank(monkeypatch, (reply, None), sent, alerts,
                    curator=("gpt-oss:120b", ["deepseek-v3.1:671b"]))
    failures = main.run(_llm_cfg(tmp_path), now=datetime(2026, 6, 8, 13))
    assert failures == 0 and sent
    assert len(alerts) == 1
    assert "ran on gpt-oss:120b" in alerts[0][1]
    assert "LLM scoring failed" not in alerts[0][1]
    assert "Ollama unreachable" not in alerts[0][1]


def test_run_dry_run_does_not_alert_on_scoring_fallback(tmp_path, monkeypatch, caplog):
    caplog.set_level("WARNING")
    sent, alerts = [], []
    _patch_llm_rank(monkeypatch, ("not json", None), sent, alerts)
    failures = main.run(_llm_cfg(tmp_path), now=datetime(2026, 6, 8, 13), dry_run=True)
    assert failures == 0 and alerts == [] and sent == []
    assert any("LLM scoring failed (unparseable)" in r.getMessage() for r in caplog.records)


def test_run_fully_degraded_does_not_stack_scoring_alert(tmp_path, monkeypatch):
    sent, alerts = [], []
    _patch_llm_rank(monkeypatch, ("", "timeout"), sent, alerts,
                    curator=(None, ["gemma4:31b"]))
    failures = main.run(_llm_cfg(tmp_path), now=datetime(2026, 6, 8, 13))
    assert failures == 0 and len(alerts) == 1
    assert "Ollama unreachable" in alerts[0][1]
    assert "LLM scoring failed" not in alerts[0][1]


def test_run_movers_baseline_reads_midweek_snapshot(tmp_path, monkeypatch, caplog):
    caplog.set_level("INFO")
    # Sunday June 14. Repo 1 is on last Sunday (100) and Wednesday (200); oldest wins.
    # Repo 2 exists only on Wednesday. Repo 3 has no snapshot in the window.
    _seed_snapshot(tmp_path, date(2026, 6, 7), {1: 100})
    _seed_snapshot(tmp_path, date(2026, 6, 10), {1: 200, 2: 50})
    sent = []
    from bot.ranker import Pick
    monkeypatch.setattr(main, "search_repos",
                        lambda *a, **k: [_repo(2, 500), _repo(1, 250), _repo(3, 9999)])
    monkeypatch.setattr(main, "readme_first_line", lambda *a, **k: "")
    monkeypatch.setattr(main, "rank", lambda repos, theme, **k: [Pick(r) for r in repos])
    monkeypatch.setattr(main, "send_message", lambda *a, **k: sent.append(a[2]) or {"ok": True})
    theme = Theme(key="movers", name="Movers", emoji="📈", query="q", count=5, delta_days=7)
    failures = main.run(_cfg(tmp_path, [theme]), now=datetime(2026, 6, 14, 19))
    body = "\n".join(sent)
    assert failures == 0
    assert "+150★ this week" in body    # 250 - Sunday's 100, not Wednesday's 200
    assert "+50★" not in body
    assert "+450★ this week" in body    # Wednesday-only repo is eligible
    assert "a/3" not in body
    assert "baseline_days=2" in caplog.text
    assert "dropped_no_baseline=1" in caplog.text


def _trending_theme(**kwargs):
    fields = dict(key="movers", name="Movers", emoji="🚀", query="q", count=5,
                  delta_days=7, github_trending=("daily", "weekly"))
    fields.update(kwargs)
    return Theme(**fields)


def _install_trending(monkeypatch, search_repos, hits, hydrated, fetched):
    from bot.ranker import Pick
    monkeypatch.setattr(main, "search_repos", lambda *a, **k: list(search_repos))
    monkeypatch.setattr(main, "collect_trending", lambda periods, **k: list(hits))
    def _fetch(names, **k):
        fetched.extend(names)
        return list(hydrated)
    monkeypatch.setattr(main, "fetch_repos", _fetch)
    monkeypatch.setattr(main, "readme_first_line", lambda *a, **k: "")
    monkeypatch.setattr(main, "rank", lambda repos, theme, **k: [Pick(r) for r in repos])


def test_run_movers_ranks_any_age_trending_repo_by_page_gain(tmp_path, monkeypatch):
    """The 2026-10-04 misses (hindsight +14.5k, created 2025-10) never entered
    Search because of created:>120d. The Trending page gain admits them with
    no snapshot baseline, ahead of a young repo's smaller snapshot delta."""
    from bot.trending import TrendingHit
    _seed_snapshot(tmp_path, date(2026, 6, 1), {1: 100})
    young = _repo(1, 250)   # snapshot delta +150
    old = Repo(99, "vectorize-io/hindsight", "https://github.com/vectorize-io/hindsight",
               "memory that learns", 20000, "Python", [], False, False,
               created_at="2025-10-01T00:00:00Z", pushed_at="2026-06-07T00:00:00Z")
    daily = Repo(100, "thedotmack/claude-mem", "https://github.com/thedotmack/claude-mem",
                 "session notes", 9000, "TypeScript", [], False, False,
                 created_at="2025-01-01T00:00:00Z", pushed_at="2026-06-07T00:00:00Z")
    hits = [
        TrendingHit("vectorize-io/hindsight", 14507, "weekly", 20000),
        TrendingHit("thedotmack/claude-mem", 627, "daily", 9000),
    ]
    sent, fetched, seen = [], [], {}

    def fake_rank(repos, theme, **k):
        seen["order"] = [r.id for r in repos]
        from bot.ranker import Pick
        return [Pick(r) for r in repos]

    monkeypatch.setattr(main, "search_repos", lambda *a, **k: [young])
    monkeypatch.setattr(main, "collect_trending", lambda periods, **k: list(hits))
    monkeypatch.setattr(main, "fetch_repos",
                        lambda names, **k: fetched.extend(names) or [old, daily])
    monkeypatch.setattr(main, "readme_first_line", lambda *a, **k: "")
    monkeypatch.setattr(main, "rank", fake_rank)
    monkeypatch.setattr(main, "send_message",
                        lambda *a, **k: sent.append(a[2]) or {"ok": True})
    alerts = []
    monkeypatch.setattr(main, "send_alert",
                        lambda *a, **k: alerts.append(a[2]) or True)
    theme = _trending_theme()
    cfg = Config("tok", "-100", "", str(tmp_path), [theme], "", alert_chat_id="dm")
    failures = main.run(cfg, now=datetime(2026, 6, 8, 13))
    assert failures == 0 and alerts == []
    assert fetched == ["vectorize-io/hindsight", "thedotmack/claude-mem"]
    assert seen["order"][0] == 99          # +14.5k/week beats +150 snapshot and +627 today
    assert 100 in seen["order"] and 1 in seen["order"]
    body = "\n".join(sent)
    assert "+14.5k★ this week" in body
    assert "+627★ today" in body
    assert "+150★ this week" in body
    snap = starsnap.load_snapshot(str(tmp_path), date(2026, 6, 8))
    assert snap[99] == 20000 and snap[100] == 9000 and snap[1] == 250


def test_run_trending_does_not_rehydrate_a_repo_search_already_returned(tmp_path, monkeypatch):
    from bot.trending import TrendingHit
    _seed_snapshot(tmp_path, date(2026, 6, 1), {1: 100})
    already = Repo(1, "acme/already", "https://github.com/acme/already",
                   "desc", 250, "Py", [], False, False)
    fetched = []
    sent = []
    _install_trending(
        monkeypatch, [already],
        [TrendingHit("acme/already", 4000, "weekly", 250)],
        [], fetched)
    monkeypatch.setattr(main, "send_message",
                        lambda *a, **k: sent.append(a[2]) or {"ok": True})
    main.run(_cfg(tmp_path, [_trending_theme(count=1)]), now=datetime(2026, 6, 8, 13))
    assert fetched == []
    # Page gain is not used once a snapshot baseline exists (+150, not +4000).
    assert any("+150★ this week" in m for m in sent)
    assert "+4.0k" not in "\n".join(sent)


def test_run_movers_still_drops_a_trending_repo_it_already_sent(tmp_path, monkeypatch):
    from bot.trending import TrendingHit
    old = Repo(99, "vectorize-io/hindsight", "u", "memory", 20000, "Py", [], False, False)
    (tmp_path / "state.json").write_text('{"movers": [99]}')
    sent = []
    _install_trending(monkeypatch, [], [TrendingHit("vectorize-io/hindsight", 14507, "weekly")],
                      [old], [])
    monkeypatch.setattr(main, "send_message",
                        lambda *a, **k: sent.append(a[2]) or {"ok": True})
    failures = main.run(_cfg(tmp_path, [_trending_theme()]), now=datetime(2026, 6, 8, 13))
    assert failures == 0 and sent == []


def test_run_movers_can_refeature_a_trending_repo_another_theme_posted(tmp_path, monkeypatch):
    from bot.trending import TrendingHit
    old = Repo(99, "mvschwarz/openrig", "https://github.com/mvschwarz/openrig",
               "rig", 8000, "Go", [], False, False)
    (tmp_path / "state.json").write_text('{"_posted": [99], "web": [99]}')
    sent = []
    _install_trending(monkeypatch, [], [TrendingHit("mvschwarz/openrig", 4200, "weekly")],
                      [old], [])
    monkeypatch.setattr(main, "send_message",
                        lambda *a, **k: sent.append(a[2]) or {"ok": True})
    main.run(_cfg(tmp_path, [_trending_theme(count=1)]), now=datetime(2026, 6, 8, 13))
    assert sent and "openrig" in sent[0]
    assert "+4.2k★ this week" in sent[0]


def test_run_trending_still_obeys_ai_cap(tmp_path, monkeypatch):
    """Any-age Trending hits still go through the deterministic caps. The age
    window was the bug; ai_cap is not bypassed."""
    from bot.trending import TrendingHit
    fast = Repo(1, "a/fast", "u", "a claude coding agent", 20000, "Py", [], False, False)
    slow = Repo(2, "b/slow", "u", "an llm gateway", 9000, "Py", [], False, False)
    tool = Repo(3, "c/db", "u", "a database engine", 1000, "Go", [], False, False)
    hits = [
        TrendingHit("a/fast", 16000, "weekly"),
        TrendingHit("b/slow", 8000, "weekly"),
        TrendingHit("c/db", 500, "weekly"),
    ]
    sent = []
    _install_trending(monkeypatch, [], hits, [fast, slow, tool], [])
    monkeypatch.setattr(main, "send_message",
                        lambda *a, **k: sent.append(a[2]) or {"ok": True})
    theme = _trending_theme(count=5, ai_cap=1)
    main.run(_cfg(tmp_path, [theme]), now=datetime(2026, 6, 8, 13))
    import json
    saved = json.loads((tmp_path / "state.json").read_text())
    assert sorted(saved["movers"]) == [1, 3]   # fastest AI + the non-AI; slow AI dropped


def test_run_trending_outage_keeps_the_search_pool_and_alerts(tmp_path, monkeypatch, caplog):
    caplog.set_level("WARNING")
    _seed_snapshot(tmp_path, date(2026, 6, 1), {1: 100})
    sent, alerts = [], []

    def boom(*a, **k):
        raise RuntimeError("trending down")

    from bot.ranker import Pick
    monkeypatch.setattr(main, "search_repos", lambda *a, **k: [_repo(1, 250)])
    monkeypatch.setattr(main, "collect_trending", boom)
    monkeypatch.setattr(main, "readme_first_line", lambda *a, **k: "")
    monkeypatch.setattr(main, "rank", lambda repos, theme, **k: [Pick(r) for r in repos])
    monkeypatch.setattr(main, "send_message",
                        lambda *a, **k: sent.append(a[2]) or {"ok": True})
    monkeypatch.setattr(main, "send_alert",
                        lambda *a, **k: alerts.append(a[2]) or True)
    theme = _trending_theme(count=1)
    cfg = Config("tok", "-100", "", str(tmp_path), [theme], "", alert_chat_id="dm")
    failures = main.run(cfg, now=datetime(2026, 6, 8, 13))
    assert failures == 0 and sent           # young snapshot repo still posts
    assert any("search-pool only" in text for text in alerts)
    assert "github trending" in caplog.text.lower()


def test_run_trending_outage_does_not_alert_on_dry_run(tmp_path, monkeypatch):
    _seed_snapshot(tmp_path, date(2026, 6, 1), {1: 100})
    alerts = []
    monkeypatch.setattr(main, "search_repos", lambda *a, **k: [_repo(1, 250)])
    monkeypatch.setattr(main, "collect_trending", lambda *a, **k: [])
    monkeypatch.setattr(main, "readme_first_line", lambda *a, **k: "")
    monkeypatch.setattr(main, "send_alert",
                        lambda *a, **k: alerts.append(a[2]) or True)
    cfg = Config("tok", "-100", "", str(tmp_path), [_trending_theme()], "",
                 alert_chat_id="dm")
    failures = main.run(cfg, now=datetime(2026, 6, 8, 13), dry_run=True)
    assert failures == 0 and alerts == []


def test_run_star_ceiling_rewrites_pushed_query_and_drops_old_giants(tmp_path, monkeypatch):
    queries = []
    old = Repo(1, "vercel/next.js", "https://github.com/vercel/next.js",
               "The React Framework", 142993, "JavaScript", [], False, False,
               "2016-10-01T00:00:00Z")
    young = Repo(2, "hypit-ai/hypit", "https://github.com/hypit-ai/hypit",
                 "A compiler", 19941, "Rust", [], False, False,
                 "2026-07-29T00:00:00Z")
    small = Repo(3, "google/xls", "https://github.com/google/xls",
                 "Hardware synthesis", 1937, "C++", [], False, False,
                 "2020-05-07T00:00:00Z")

    def fake_search(query, **k):
        queries.append(query)
        return [old, young, small]

    sent = []
    monkeypatch.setattr(main, "search_repos", fake_search)
    monkeypatch.setattr(main, "readme_first_line", lambda *a, **k: "")
    monkeypatch.setattr(main, "send_message",
                        lambda *a, **k: sent.append(a[2]) or {"ok": True})
    theme = Theme(
        key="systems", name="Systems", emoji="",
        query=("topic:compiler created:>{since:180d}",
               "topic:compiler pushed:>{since:30d} stars:>50"),
        count=5, max_stars=5000, max_stars_exempt_days=180,
    )
    failures = main.run(_cfg(tmp_path, [theme]), now=datetime(2026, 10, 7, 19))
    assert failures == 0
    assert any(q.endswith("stars:51..5000") for q in queries)
    assert any("created:>" in q and "stars:" not in q for q in queries)
    body = "\n".join(sent)
    assert "next.js" not in body
    assert "hypit" in body and "xls" in body


def test_run_star_ceiling_with_only_giants_is_a_quiet_slot(tmp_path, monkeypatch, caplog):
    caplog.set_level("INFO")
    old = Repo(1, "vercel/next.js", "u", "d", 142993, "JavaScript", [], False, False,
               "2016-10-01T00:00:00Z")
    sent = []
    monkeypatch.setattr(main, "search_repos", lambda *a, **k: [old])
    monkeypatch.setattr(main, "readme_first_line", lambda *a, **k: "")
    monkeypatch.setattr(main, "send_message",
                        lambda *a, **k: sent.append(a[2]) or {"ok": True})
    theme = Theme(key="systems", name="S", emoji="", query="q", count=5,
                  max_stars=5000, max_stars_exempt_days=180)
    failures = main.run(_cfg(tmp_path, [theme]), now=datetime(2026, 10, 7, 19))
    assert failures == 0 and sent == []
    assert "no new repos" in caplog.text
    assert "quality bar" not in caplog.text
