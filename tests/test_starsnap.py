from datetime import date, timedelta
from dataclasses import dataclass
from bot import starsnap


@dataclass(frozen=True)
class R:
    id: int
    stars: int


def test_load_snapshot_missing_file_is_empty(tmp_path):
    assert starsnap.load_snapshot(str(tmp_path), date(2026, 6, 1)) == {}


def test_save_then_load_roundtrips_int_keys_and_values(tmp_path):
    starsnap.save_snapshot(str(tmp_path), date(2026, 6, 1), {1: 100, 2: 250})
    assert starsnap.load_snapshot(str(tmp_path), date(2026, 6, 1)) == {1: 100, 2: 250}


def test_load_snapshot_coerces_string_keys_to_int(tmp_path):
    # JSON round-trips int keys as strings; load must restore ints (Repo.id is int)
    d = tmp_path / "starsnap"
    d.mkdir()
    (d / "2026-06-01.json").write_text('{"1": 100, "2": 250}')
    assert starsnap.load_snapshot(str(tmp_path), date(2026, 6, 1)) == {1: 100, 2: 250}


def test_find_baseline_returns_exact_day_when_present(tmp_path):
    starsnap.save_snapshot(str(tmp_path), date(2026, 6, 1), {1: 100})
    out = starsnap.find_baseline(str(tmp_path), date(2026, 6, 8), delta_days=7)
    assert out == {1: 100}


def test_find_baseline_falls_back_to_nearest_older_within_tolerance(tmp_path):
    # exact day (6/1) missing; 6/2 present (8 days ago, within tolerance of 7+3)
    starsnap.save_snapshot(str(tmp_path), date(2026, 6, 2), {9: 50})
    out = starsnap.find_baseline(str(tmp_path), date(2026, 6, 10), delta_days=7, tolerance=3)
    assert out == {9: 50}


def test_find_baseline_ignores_today_and_snapshots_before_the_window(tmp_path):
    today = date(2026, 6, 8)
    # window is May 29 .. June 7 (delta 7 + default tolerance 3); today is skipped
    starsnap.save_snapshot(str(tmp_path), today, {1: 10})
    starsnap.save_snapshot(str(tmp_path), date(2026, 5, 28), {2: 20})
    out = starsnap.find_baseline(str(tmp_path), today, delta_days=7)
    assert out == {}
    assert out.baseline_days == 0


def test_find_baseline_merges_weekday_snapshots_keeping_oldest_count(tmp_path):
    # Sunday June 14. Window is June 4 .. June 13. Last Sunday and Wednesday both count.
    today = date(2026, 6, 14)
    starsnap.save_snapshot(str(tmp_path), date(2026, 6, 3), {9: 1})             # before window
    starsnap.save_snapshot(str(tmp_path), date(2026, 6, 7), {1: 500})           # previous Sunday
    starsnap.save_snapshot(str(tmp_path), date(2026, 6, 10), {1: 100, 2: 40})   # Wednesday
    starsnap.save_snapshot(str(tmp_path), date(2026, 6, 11), {})                 # empty: not a day
    starsnap.save_snapshot(str(tmp_path), date(2026, 6, 13), {4: 70})           # yesterday
    starsnap.save_snapshot(str(tmp_path), today, {3: 999})                       # today skipped
    out = starsnap.find_baseline(str(tmp_path), today, delta_days=7, tolerance=3)
    assert out[1] == 500          # earliest count, not Wednesday's smaller 100
    assert out[2] == 40           # Wednesday-only repo is visible
    assert out[4] == 70           # yesterday is inside the window
    assert 3 not in out and 9 not in out
    assert out.baseline_days == 3


def test_find_baseline_returns_empty_on_cold_start(tmp_path):
    assert starsnap.find_baseline(str(tmp_path), date(2026, 6, 8), delta_days=7) == {}


def test_retain_deletes_files_older_than_keep_days(tmp_path):
    today = date(2026, 6, 18)
    starsnap.save_snapshot(str(tmp_path), today - timedelta(days=20), {1: 1})  # gone
    starsnap.save_snapshot(str(tmp_path), today - timedelta(days=14), {2: 2})  # kept
    starsnap.save_snapshot(str(tmp_path), today, {3: 3})                       # kept
    starsnap.retain(str(tmp_path), today, keep_days=14)
    days = {date.fromisoformat(p.stem) for p in (tmp_path / "starsnap").glob("*.json")}
    assert days == {today - timedelta(days=14), today}


def test_retain_noop_when_folder_absent(tmp_path):
    starsnap.retain(str(tmp_path), date(2026, 6, 18))   # no starsnap/ dir yet


def test_order_by_delta_sorts_desc_and_drops_unbaselined():
    repos = [R(1, 250), R(2, 60), R(3, 9999)]            # 3 has no baseline
    baseline = {1: 100, 2: 50}                            # deltas: 150, 10
    out = starsnap.order_by_delta(repos, baseline)
    assert [r.id for r in out] == [1, 2]                  # by delta desc; repo 3 dropped


def test_order_by_delta_ties_keep_input_order():
    repos = [R(1, 110), R(2, 210), R(3, 310)]
    baseline = {1: 100, 2: 200, 3: 300}                   # all delta 10 -> stable
    out = starsnap.order_by_delta(repos, baseline)
    assert [r.id for r in out] == [1, 2, 3]


def test_order_by_delta_keeps_unbaselined_repo_with_a_measured_gain():
    # Audit shape: an older repo has no snapshot, but GitHub Trending measured
    # +14,507 this week. It outranks a young repo whose snapshot grew by 150.
    repos = [R(1, 250), R(2, 60), R(3, 20000)]
    baseline = {1: 100, 2: 50}                            # deltas 150 and 10
    extras = {3: (14507, "weekly")}
    out = starsnap.order_by_delta(repos, baseline, extras, span_days=7)
    assert [r.id for r in out] == [3, 1, 2]


def test_order_by_delta_compares_daily_and_weekly_gains_per_day():
    repos = [R(1, 1), R(2, 1)]
    extras = {1: (1400, "weekly"), 2: (500, "daily")}     # 200/day vs 500/day
    out = starsnap.order_by_delta(repos, {}, extras)
    assert [r.id for r in out] == [2, 1]


def test_order_by_delta_snapshot_beats_a_page_gain_for_the_same_repo():
    repos = [R(1, 110), R(2, 5000)]
    baseline = {1: 100}                                   # +10, not the page's +9000
    extras = {1: (9000, "daily"), 2: (100, "weekly")}
    out = starsnap.order_by_delta(repos, baseline, extras, span_days=7)
    assert [r.id for r in out] == [2, 1]


def test_growth_pace_windows():
    assert starsnap.growth_pace(500, "daily") == 500
    assert starsnap.growth_pace(1400, "weekly") == 200
    assert starsnap.growth_pace(3000, "monthly") == 100
