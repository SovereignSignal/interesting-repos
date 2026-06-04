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


from bot.github import Repo
from bot.filters import is_keyword_stuffed, is_awesome_list, is_stale, clean


def _repo(full_name="acme/widget", description="A useful widget toolkit",
          topics=None, stars=100, created_at="", pushed_at=""):
    return Repo(1, full_name, "u", description, stars, "Py", topics or [],
                False, False, created_at, pushed_at)


def test_is_keyword_stuffed_flags_repeated_token():
    assert is_keyword_stuffed(_repo(description="hyperliquid sdk | " * 6)) is True


def test_is_keyword_stuffed_passes_normal_description():
    assert is_keyword_stuffed(_repo(description="A fast quantitative trading framework")) is False


def test_is_awesome_list_flags_by_name_and_topic():
    assert is_awesome_list(_repo(full_name="VoltAgent/awesome-design-md")) is True
    assert is_awesome_list(_repo(topics=["awesome-list", "design"])) is True
    assert is_awesome_list(_repo(full_name="acme/widget", topics=["ui"])) is False


def test_is_stale_uses_max_idle_days_boundary():
    today = date(2026, 6, 4)
    assert is_stale(_repo(pushed_at="2026-04-04T00:00:00Z"), today, 60) is True   # 61 days
    assert is_stale(_repo(pushed_at="2026-04-05T00:00:00Z"), today, 60) is False  # 60 days
    assert is_stale(_repo(pushed_at=""), today, 60) is False                      # unknown -> kept


def test_clean_drops_noise_and_stale_preserving_order():
    today = date(2026, 6, 4)
    good = _repo(full_name="acme/good", description="solid tool", pushed_at="2026-06-01T00:00:00Z")
    spam = _repo(full_name="x/spam", description="buy buy buy buy buy buy now")
    awesome = _repo(full_name="x/awesome-lists", description="a list")
    stale = _repo(full_name="x/stale", description="old", pushed_at="2026-01-01T00:00:00Z")
    out = clean([good, spam, awesome, stale], today, 60)
    assert [r.full_name for r in out] == ["acme/good"]
