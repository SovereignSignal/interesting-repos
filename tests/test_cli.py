import logging

from bot.__main__ import _configure_logging


def test_configure_logging_silences_httpx_to_keep_token_out_of_logs():
    # httpx/httpcore log full request URLs at INFO, and the Telegram bot token lives
    # in the URL path — so they must be raised to WARNING to keep the token out of logs.
    logging.getLogger("httpx").setLevel(logging.INFO)
    logging.getLogger("httpcore").setLevel(logging.INFO)
    _configure_logging()
    assert logging.getLogger("httpx").level == logging.WARNING
    assert logging.getLogger("httpcore").level == logging.WARNING


import pytest
from types import SimpleNamespace
import bot.__main__ as _cli


def test_cli_alerts_on_crash_and_reraises(monkeypatch):
    alerts = []
    monkeypatch.setattr(_cli, "load_config",
                        lambda **k: SimpleNamespace(telegram_bot_token="tok", alert_chat_id="d"))
    def boom(*a, **k):
        raise RuntimeError("kaboom")
    monkeypatch.setattr(_cli, "run", boom)
    monkeypatch.setattr(_cli, "send_alert", lambda token, chat, text, **k: alerts.append(text) or True)
    with pytest.raises(RuntimeError):
        _cli.cli([])
    assert alerts and "crashed" in alerts[0] and "kaboom" in alerts[0]


def test_cli_passes_now_and_theme_and_skips_telegram_on_dry_run(monkeypatch, tmp_path):
    seen = {}
    p = tmp_path / "t.toml"
    p.write_text('[[theme]]\nkey="trending"\nname="T"\nquery="q"\nat=["mon 13"]\n'
                 '[[theme]]\nkey="other"\nname="O"\nquery="q"\nat=["tue 13"]\n')

    def fake_load(**k):
        seen["require_telegram"] = k.get("require_telegram", True)
        from bot.config import load_config
        return load_config(env={"TELEGRAM_BOT_TOKEN": "tok", "TELEGRAM_CHAT_ID": "-1"},
                           themes_path=str(p), require_telegram=k.get("require_telegram", True))

    def fake_run(config, now=None, dry_run=False):
        seen["now"] = now
        seen["dry_run"] = dry_run
        seen["keys"] = [t.key for t in config.themes]
        seen["at"] = config.themes[0].at
        return 0

    monkeypatch.setattr(_cli, "load_config", fake_load)
    monkeypatch.setattr(_cli, "run", fake_run)
    rc = _cli.cli(["--dry-run", "--now", "2026-08-28T13:00:00Z",
                   "--theme", "trending", "--themes", str(p)])
    assert rc == 0
    assert seen["require_telegram"] is False
    assert seen["dry_run"] is True
    assert seen["keys"] == ["trending"]
    assert seen["at"] is None          # --theme strips at so the clock cannot hide it
    assert seen["now"].year == 2026 and seen["now"].hour == 13


def test_cli_unknown_theme_exits(monkeypatch, tmp_path):
    p = tmp_path / "t.toml"
    p.write_text('[[theme]]\nkey="trending"\nname="T"\nquery="q"\n')
    from bot.config import load_config
    monkeypatch.setattr(_cli, "load_config",
                        lambda **k: load_config(
                            env={"TELEGRAM_BOT_TOKEN": "tok", "TELEGRAM_CHAT_ID": "-1"},
                            themes_path=str(p)))
    with pytest.raises(SystemExit, match="unknown theme"):
        _cli.cli(["--theme", "nope", "--themes", str(p)])
