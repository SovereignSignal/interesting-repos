from datetime import date
from types import SimpleNamespace

from bot.filters import age_days, star_velocity


def test_age_days_counts_whole_days():
    assert age_days("2026-06-01T00:00:00Z", date(2026, 6, 4)) == 3


def test_age_days_returns_none_on_blank_or_garbage():
    assert age_days("", date(2026, 6, 4)) is None
    assert age_days("not-a-date", date(2026, 6, 4)) is None


def test_star_velocity_is_stars_per_day():
    repo = SimpleNamespace(stars=300, created_at="2026-06-01T00:00:00Z")
    assert star_velocity(repo, date(2026, 6, 4)) == 100.0


def test_star_velocity_floors_age_at_one_day():
    fresh = SimpleNamespace(stars=500, created_at="2026-06-04T00:00:00Z")  # 0 days old
    assert star_velocity(fresh, date(2026, 6, 4)) == 500.0
    unknown = SimpleNamespace(stars=500, created_at="")
    assert star_velocity(unknown, date(2026, 6, 4)) == 500.0
