"""Keep the suite off GitNova and the extra Trending windows.

Those pools are on in production (``EXTRA_SOURCES``). A test that wants them
sets the flag back to True and passes fixture data — nothing here dials out.
"""
import pytest

import bot.main as main


@pytest.fixture(autouse=True)
def extra_sources_off(monkeypatch):
    monkeypatch.setattr(main, "EXTRA_SOURCES", False)
