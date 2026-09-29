"""Source-health logs, 24h threshold, per-source dedupe, missing/corrupt state."""
import json
import logging
from datetime import datetime, timedelta, timezone

from bot.config import Config, Theme
from bot.github import Repo
from bot.source_health import (
    alert_warn_line,
    apply_outcome,
    github_line,
    health_path,
    load_health,
    report_source_health,
    save_health,
    source_line,
    thin_post_line,
)
import bot.main as main


T0 = datetime(2026, 6, 1, 16, tzinfo=timezone.utc)
TOKEN = "ghp_SUPERSECRETTOKEN"
TG_TOKEN = "123456:ABCDEFGHIJKLMNOPQRSTUV"


def _status_lines(caplog):
    return [
        r.getMessage()
        for r in caplog.records
        if r.getMessage().startswith("source_health source=") and " status=" in r.getMessage()
    ]


def _send(box, ok=True):
    def send(token, chat, text, client=None):
        box.append((token, chat, text))
        return ok
    return send


def _report(tmp_path, outcomes, now, box=None, ok=True, **kw):
    box = [] if box is None else box
    params = dict(
        state_dir=str(tmp_path),
        now=now,
        dry_run=False,
        github_authenticated=False,
        alert_chat_id="dm-admin",
        telegram_token=TG_TOKEN,
        rate_remaining=None,
        send_alert=_send(box, ok),
    )
    params.update(kw)
    report_source_health(outcomes, **params)
    return box


def _health(tmp_path):
    return json.loads((tmp_path / "source_health.json").read_text())


def test_log_lines_one_per_source_and_github_auth(tmp_path, caplog):
    caplog.set_level(logging.INFO)
    _report(
        tmp_path,
        [
            ("crypto", "ok", 2, ""),
            ("trending", "empty", 0, ""),
            ("security", "error", 0, "HTTP 403\nrate limited"),
        ],
        T0,
        dry_run=True,
        github_authenticated=True,
        alert_chat_id="",
        rate_remaining=4988,
        send_alert=lambda *a, **k: (_ for _ in ()).throw(AssertionError("send")),
    )
    assert _status_lines(caplog) == [
        "source_health source=crypto status=ok items=2",
        "source_health source=trending status=empty items=0",
        "source_health source=security status=error items=0 error=HTTP 403 rate limited",
    ]
    assert "source_health github_authenticated=yes rate_limit_remaining=4988" in [
        r.getMessage() for r in caplog.records
    ]
    assert TG_TOKEN not in caplog.text
    assert TOKEN not in caplog.text
    assert not (tmp_path / "source_health.json").exists()


def test_github_line_yes_no_only_and_zero_remaining_is_present():
    assert github_line(False, None) == "source_health github_authenticated=no"
    assert github_line(True, None) == "source_health github_authenticated=yes"
    assert github_line(True, 0) == "source_health github_authenticated=yes rate_limit_remaining=0"
    assert "ghp_" not in github_line(True, 12)
    assert source_line("crypto", "ok", 4) == "source_health source=crypto status=ok items=4"
    assert thin_post_line("crypto", 1) == "source_health thin_post source=crypto items=1"
    assert alert_warn_line("crypto", "empty", 0).startswith("source_health ALERT source=crypto")


def test_threshold_under_24h_does_not_alert(tmp_path):
    box = _report(tmp_path, [("crypto", "empty", 0, "")], T0)
    assert box == []
    box = _report(
        tmp_path, [("crypto", "empty", 0, "")], T0 + timedelta(hours=23, minutes=59), box)
    assert box == []
    entry = _health(tmp_path)["crypto"]
    assert entry["consecutive_empty"] == 2
    assert entry["consecutive_error"] == 0
    assert entry["last_ok"] is None
    assert entry["last_alert"] is None


def test_threshold_at_24h_alerts_empty_and_error(tmp_path):
    box = []
    _report(tmp_path, [("crypto", "empty", 0, "")], T0, box)
    _report(tmp_path, [("crypto", "empty", 0, "")], T0 + timedelta(hours=24), box)
    assert len(box) == 1
    assert box[0][1] == "dm-admin"
    assert "has been empty for 24h+" in box[0][2]
    assert TG_TOKEN not in box[0][2]

    err_dir_box = []
    # A separate streak that is already 24h old alerts on this observation.
    aged = apply_outcome(None, "error", T0 - timedelta(hours=24))
    fresh = tmp_path / "err"
    fresh.mkdir()
    save_health(health_path(str(fresh)), {"security": aged})
    _report(fresh, [("security", "error", 0, "HTTP 403")], T0, err_dir_box)
    assert len(err_dir_box) == 1
    assert "has been erroring for 24h+" in err_dir_box[0][2]
    assert "HTTP 403" in err_dir_box[0][2]


def test_dedupe_one_alert_per_source_per_24h(tmp_path):
    box = []
    _report(tmp_path, [("crypto", "empty", 0, ""), ("web", "ok", 5, "")], T0, box)
    _report(
        tmp_path,
        [("crypto", "empty", 0, ""), ("web", "empty", 0, "")],
        T0 + timedelta(hours=24),
        box,
    )
    assert len(box) == 1
    assert "source crypto" in box[0][2]
    assert "web" not in box[0][2]
    # crypto is inside its 24h cooldown; web has only been empty for an hour
    _report(
        tmp_path,
        [("crypto", "empty", 0, ""), ("web", "empty", 0, "")],
        T0 + timedelta(hours=25),
        box,
    )
    assert len(box) == 1
    _report(
        tmp_path,
        [("crypto", "empty", 0, ""), ("web", "empty", 0, "")],
        T0 + timedelta(hours=48),
        box,
    )
    assert len(box) == 3
    fresh_alerts = [text for _, _, text in box[1:]]
    assert any("source crypto" in text for text in fresh_alerts)
    assert any("source web" in text for text in fresh_alerts)


def test_ok_clears_streak_and_error_does_not_reset_unhealthy_since(tmp_path):
    _report(tmp_path, [("crypto", "empty", 0, "")], T0)
    since = _health(tmp_path)["crypto"]["unhealthy_since"]
    _report(tmp_path, [("crypto", "error", 0, "boom")], T0 + timedelta(hours=1))
    entry = _health(tmp_path)["crypto"]
    assert entry["unhealthy_since"] == since
    assert entry["consecutive_empty"] == 0
    assert entry["consecutive_error"] == 1
    _report(tmp_path, [("crypto", "ok", 4, "")], T0 + timedelta(hours=2))
    cleared = _health(tmp_path)["crypto"]
    assert cleared["consecutive_empty"] == 0
    assert cleared["consecutive_error"] == 0
    assert cleared["unhealthy_since"] is None
    assert cleared["last_ok"]
    box = _report(tmp_path, [("crypto", "empty", 0, "")], T0 + timedelta(hours=30))
    assert box == []


def test_missing_health_file_starts_fresh(tmp_path):
    path = tmp_path / "source_health.json"
    assert not path.exists()
    assert load_health(str(path)) == {}
    box = _report(tmp_path, [("crypto", "empty", 1, "")], T0)
    assert box == []
    saved = _health(tmp_path)
    assert saved["crypto"]["consecutive_empty"] == 1
    assert saved["crypto"]["last_ok"] is None
    assert saved["crypto"]["last_alert"] is None


def test_corrupt_health_file_starts_fresh(tmp_path, caplog):
    path = tmp_path / "source_health.json"
    caplog.set_level(logging.WARNING)
    path.write_text("{not json")
    box = _report(tmp_path, [("crypto", "empty", 0, "")], T0)
    assert box == []
    assert _health(tmp_path)["crypto"]["consecutive_empty"] == 1
    assert "source_health state unreadable; starting fresh" in caplog.text

    caplog.clear()
    path.write_text("[]")
    _report(tmp_path, [("crypto", "empty", 0, "")], T0 + timedelta(hours=1))
    assert "source_health state unreadable; starting fresh" in caplog.text
    # corrupt file discarded the previous streak, so this observation is a new one
    assert _health(tmp_path)["crypto"]["consecutive_empty"] == 1

    path.write_text("")
    assert load_health(str(path)) == {}
    path.write_text("null")
    assert load_health(str(path)) == {}


def test_corrupt_fields_and_sibling_sources_are_tolerated(tmp_path):
    path = tmp_path / "source_health.json"
    path.write_text(json.dumps({
        "crypto": {"unhealthy_since": "not-a-date", "consecutive_empty": "lots", "last_ok": 3},
        "web": {"consecutive_empty": 4, "note": "keep"},
        "bad": "nope",
    }))
    box = _report(tmp_path, [("crypto", "empty", 0, "")], T0)
    assert box == []
    saved = _health(tmp_path)
    assert saved["crypto"]["consecutive_empty"] == 1
    assert saved["crypto"]["unhealthy_since"]
    assert saved["web"]["consecutive_empty"] == 4
    assert saved["web"]["note"] == "keep"
    assert "bad" not in saved


def test_no_alert_chat_logs_warn_once_per_24h(tmp_path, caplog):
    caplog.set_level(logging.INFO)
    box = []
    _report(tmp_path, [("crypto", "empty", 0, "")], T0, box, alert_chat_id="")
    _report(
        tmp_path, [("crypto", "empty", 0, "")], T0 + timedelta(hours=24), box, alert_chat_id="")
    _report(
        tmp_path, [("crypto", "empty", 0, "")], T0 + timedelta(hours=25), box, alert_chat_id="")
    assert box == []
    warns = [
        r.getMessage() for r in caplog.records
        if r.levelname == "WARNING" and r.getMessage().startswith("source_health ALERT ")
    ]
    assert warns == ["source_health ALERT source=crypto status=empty items=0 unhealthy_for>=24h"]
    assert _health(tmp_path)["crypto"]["last_alert"]


def test_failed_send_does_not_consume_dedupe(tmp_path):
    box = []
    _report(tmp_path, [("crypto", "error", 0, "down")], T0, box, ok=False)
    _report(
        tmp_path, [("crypto", "error", 0, "down")], T0 + timedelta(hours=24), box, ok=False)
    _report(
        tmp_path, [("crypto", "error", 0, "down")], T0 + timedelta(hours=25), box, ok=False)
    assert len(box) == 2
    assert _health(tmp_path)["crypto"]["last_alert"] is None
    assert _health(tmp_path)["crypto"]["consecutive_error"] == 3


def test_dry_run_does_not_persist_or_alert(tmp_path, caplog):
    caplog.set_level(logging.INFO)
    path = tmp_path / "source_health.json"
    aged = apply_outcome(None, "empty", T0 - timedelta(hours=48))
    save_health(str(path), {"crypto": aged})
    before = path.read_text()
    box = []
    _report(
        tmp_path, [("crypto", "empty", 0, "")], T0, box, dry_run=True, github_authenticated=True)
    assert box == []
    assert path.read_text() == before
    assert _status_lines(caplog) == ["source_health source=crypto status=empty items=0"]
    assert "source_health github_authenticated=yes" in caplog.text


def test_unwritable_state_logs_in_memory_note(tmp_path, caplog):
    caplog.set_level(logging.WARNING)
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("x")
    box = _report(blocker, [("crypto", "empty", 0, "")], T0)
    assert box == []
    assert "source_health state not persisted; tracking in-memory this run" in caplog.text


def _repo(i, stars):
    return Repo(i, f"a/{i}", f"https://x/{i}", "desc", stars, "Py", [], False, False)


def _cfg(tmp_path, themes, github_token="", alert_chat_id=""):
    return Config(
        TG_TOKEN, "-100", github_token, str(tmp_path), themes, "",
        alert_chat_id=alert_chat_id,
    )


def _patch_run(monkeypatch, repos, sent, rate=None):
    monkeypatch.setattr(main, "search_repos", lambda *a, **k: list(repos))
    monkeypatch.setattr(main, "readme_first_line", lambda *a, **k: "")
    monkeypatch.setattr(main, "send_message", lambda *a, **k: sent.append(a[2]) or {"ok": True})
    if rate is not None:
        monkeypatch.setattr(main, "github_rate_remaining", lambda: rate)


def test_run_logs_posted_count_auth_and_thin_post(tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    sent = []
    repos = [_repo(1, 50), _repo(2, 40), _repo(3, 30), _repo(4, 20)]
    _patch_run(monkeypatch, repos, sent, rate=17)
    theme = Theme(key="crypto", name="Crypto", emoji="", query="q", count=2)
    failures = main.run(_cfg(tmp_path, [theme], github_token=TOKEN), now=T0)
    assert failures == 0 and len(sent) == 1
    assert "a/1" in sent[0] and "a/2" in sent[0] and "a/3" not in sent[0]
    assert _status_lines(caplog) == ["source_health source=crypto status=ok items=2"]
    assert "source_health github_authenticated=yes rate_limit_remaining=17" in caplog.text
    assert TOKEN not in caplog.text
    assert "source_health thin_post source=crypto items=2" in caplog.text
    state = json.loads((tmp_path / "state.json").read_text())
    assert "last_ok" not in json.dumps(state)
    assert "consecutive_empty" not in json.dumps(state)
    health = _health(tmp_path)
    assert health["crypto"]["last_ok"]
    assert health["crypto"]["consecutive_empty"] == 0


def test_run_three_items_is_not_a_thin_post(tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    sent = []
    _patch_run(monkeypatch, [_repo(1, 30), _repo(2, 20), _repo(3, 10)], sent)
    theme = Theme(key="web", name="Web", emoji="", query="q", count=5)
    main.run(_cfg(tmp_path, [theme]), now=T0)
    assert len(sent) == 1 and "a/1" in sent[0] and "a/3" in sent[0]
    assert "thin_post" not in caplog.text
    assert _status_lines(caplog) == ["source_health source=web status=ok items=3"]
    assert "source_health github_authenticated=no" in caplog.text
    assert "rate_limit_remaining" not in caplog.text


def test_run_skips_unscheduled_theme(tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    sent = []
    _patch_run(monkeypatch, [_repo(1, 10), _repo(2, 9), _repo(3, 8)], sent)
    early = Theme(key="early", name="E", emoji="", query="q", count=3, at=((0, 13),))
    late = Theme(key="late", name="L", emoji="", query="q", count=3, at=((0, 19),))
    # 2026-06-08 is a Monday.
    main.run(
        _cfg(tmp_path, [early, late]),
        now=datetime(2026, 6, 8, 13, tzinfo=timezone.utc),
    )
    assert _status_lines(caplog) == ["source_health source=early status=ok items=3"]
    assert "source=late" not in caplog.text
    assert "late" not in _health(tmp_path)


def test_run_empty_and_selection_error(tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    sent = []

    def search(query, **k):
        if "BAD" in query:
            raise RuntimeError(f"search failed {TOKEN}")
        return []

    monkeypatch.setattr(main, "search_repos", search)
    monkeypatch.setattr(main, "readme_first_line", lambda *a, **k: "")
    monkeypatch.setattr(main, "send_message", lambda *a, **k: sent.append(a[2]) or {"ok": True})
    empty = Theme(key="trending", name="T", emoji="", query="OK", count=5)
    bad = Theme(key="security", name="S", emoji="", query="BAD", count=5)
    failures = main.run(_cfg(tmp_path, [empty, bad], github_token=TOKEN), now=T0)
    assert failures == 1 and sent == []
    assert _status_lines(caplog) == [
        "source_health source=trending status=empty items=0",
        "source_health source=security status=error items=0 error=search failed ***",
    ]
    status = " ".join(_status_lines(caplog))
    assert TOKEN not in status
    assert "thin_post" not in caplog.text
    health = _health(tmp_path)
    assert health["trending"]["consecutive_empty"] == 1
    assert health["security"]["consecutive_error"] == 1


def test_run_24h_empty_alerts_admin_chat_only(tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    now = datetime(2026, 6, 8, 16, tzinfo=timezone.utc)
    aged = apply_outcome(None, "empty", now - timedelta(hours=30))
    save_health(health_path(str(tmp_path)), {"crypto": aged})
    alerts = []
    monkeypatch.setattr(
        main, "send_alert", lambda token, chat, text, **k: alerts.append((chat, text)) or True)
    monkeypatch.setattr(main, "search_repos", lambda *a, **k: [])
    monkeypatch.setattr(main, "readme_first_line", lambda *a, **k: "")
    monkeypatch.setattr(main, "send_message", lambda *a, **k: (_ for _ in ()).throw(AssertionError("digest")))
    theme = Theme(key="crypto", name="Crypto", emoji="", query="q", count=5)
    cfg = _cfg(tmp_path, [theme], github_token=TOKEN, alert_chat_id="998877")
    assert main.run(cfg, now=now) == 0
    assert len(alerts) == 1
    assert alerts[0][0] == "998877"
    assert "has been empty for 24h+" in alerts[0][1]
    assert "-100" not in alerts[0][1]
    assert TOKEN not in alerts[0][1]
    assert not any(
        r.levelname == "WARNING" and r.getMessage().startswith("source_health ALERT ")
        for r in caplog.records
    )
    main.run(cfg, now=now + timedelta(hours=1))
    assert len(alerts) == 1


def test_run_without_alert_chat_warns_instead_of_sending(tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    now = datetime(2026, 6, 8, 16, tzinfo=timezone.utc)
    aged = apply_outcome(None, "empty", now - timedelta(hours=30))
    save_health(health_path(str(tmp_path)), {"crypto": aged})
    alerts = []
    monkeypatch.setattr(
        main, "send_alert", lambda *a, **k: alerts.append(a) or True)
    monkeypatch.setattr(main, "search_repos", lambda *a, **k: [])
    monkeypatch.setattr(main, "readme_first_line", lambda *a, **k: "")
    theme = Theme(key="crypto", name="Crypto", emoji="", query="q", count=5)
    main.run(_cfg(tmp_path, [theme]), now=now)
    assert alerts == []
    assert any(
        r.levelname == "WARNING" and r.getMessage().startswith("source_health ALERT source=crypto")
        for r in caplog.records
    )


def test_run_dry_run_logs_health_without_writing(tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    sent = []
    _patch_run(monkeypatch, [_repo(1, 10)], sent, rate=4)
    theme = Theme(key="crypto", name="Crypto", emoji="", query="q", count=1)
    main.run(_cfg(tmp_path, [theme], github_token=TOKEN), now=T0, dry_run=True)
    assert sent == []
    assert not (tmp_path / "state.json").exists()
    assert not (tmp_path / "source_health.json").exists()
    assert _status_lines(caplog) == ["source_health source=crypto status=ok items=1"]
    assert "thin_post" not in caplog.text
    assert "rate_limit_remaining=4" in caplog.text
