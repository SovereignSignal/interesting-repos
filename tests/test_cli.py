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
