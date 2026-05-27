from datetime import date
import bot.main as main
from bot.config import Config, Theme
from bot.github import Repo


def _cfg(tmp_path, themes):
    return Config("tok", "-100", "", "", str(tmp_path), themes)

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
