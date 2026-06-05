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
