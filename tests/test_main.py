from datetime import date
import bot.main as main
from bot.config import Config, Theme
from bot.github import Repo


def _cfg(tmp_path, themes, delay=0):
    # Config(tg_token, tg_chat, github_token, state_dir, themes, ollama_host=...).
    # ollama_host="" keeps make_titles/translate/rank offline; delay=0 => no real sleep.
    return Config("tok", "-100", "", str(tmp_path), themes, "", send_delay_seconds=delay)

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
    failures = main.run(_cfg(tmp_path, [theme]), today=date(2026, 5, 26))
    assert failures == 0 and len(sent) == 1
    import json
    saved = json.loads((tmp_path / "state.json").read_text())
    assert sorted(saved["t"]) == [1, 2]

def test_run_dry_run_does_not_send_or_persist(tmp_path, monkeypatch, capsys):
    sent = []
    theme = Theme(key="t", name="T", emoji="", query="q", count=1)
    _patch(monkeypatch, [_repo(1, 10)], sent)
    failures = main.run(_cfg(tmp_path, [theme]), today=date(2026, 5, 26), dry_run=True)
    assert failures == 0 and sent == []
    assert not (tmp_path / "state.json").exists()
    assert "a/1" in capsys.readouterr().out

def test_run_skips_already_sent(tmp_path, monkeypatch):
    sent = []
    theme = Theme(key="t", name="T", emoji="", query="q", count=5)
    _patch(monkeypatch, [_repo(1, 10)], sent)
    (tmp_path / "state.json").write_text('{"t": [1]}')
    failures = main.run(_cfg(tmp_path, [theme]), today=date(2026, 5, 26))
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
    failures = main.run(_cfg(tmp_path, [good, bad]), today=date(2026, 5, 26))
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
    main.run(_cfg(tmp_path, [first, second]), today=date(2026, 6, 4))
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
    main.run(_cfg(tmp_path, [trending, ai]), today=date(2026, 6, 4))
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
    main.run(_cfg(tmp_path, [theme]), today=date(2026, 6, 4))
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
    main.run(_cfg(tmp_path, [theme]), today=date(2026, 6, 4))
    import json
    saved = json.loads((tmp_path / "state.json").read_text())
    assert sorted(saved["sec"]) == [1, 2, 4]   # at most 2 packs (p1,p2) + non-pack tool; p3 dropped


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
    main.run(_cfg(tmp_path, [a, b], delay=5), today=date(2026, 6, 4))
    assert len(sent) == 2       # two themes, one message each
    assert slept == [5]         # exactly one inter-message pause (none before the first send)
