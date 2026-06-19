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


def test_find_baseline_returns_empty_when_window_empty(tmp_path):
    # nothing aged 7-10 days; a newer snapshot (within delta window) must be ignored
    starsnap.save_snapshot(str(tmp_path), date(2026, 6, 7), {9: 50})   # 1 day old
    assert starsnap.find_baseline(str(tmp_path), date(2026, 6, 8), delta_days=7) == {}


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
