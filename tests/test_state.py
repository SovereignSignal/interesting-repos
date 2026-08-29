from dataclasses import dataclass
from bot.state import (
    load_state, save_state, unsent, record_sent,
    unposted, record_posted, POSTED_KEY,
)


@dataclass(frozen=True)
class FakeRepo:
    id: int


def test_load_missing_file_returns_empty(tmp_path):
    assert load_state(str(tmp_path / "nope.json")) == {}

def test_save_then_load_roundtrip(tmp_path):
    path = str(tmp_path / "sub" / "state.json")   # nested dir must be created
    save_state(path, {"t": [1, 2, 3]})
    assert load_state(path) == {"t": [1, 2, 3]}

def test_unsent_filters_already_sent_ids():
    state = {"t": [1, 2]}
    repos = [FakeRepo(1), FakeRepo(3), FakeRepo(4)]
    assert [r.id for r in unsent(state, "t", repos)] == [3, 4]

def test_unsent_unknown_theme_returns_all():
    repos = [FakeRepo(9)]
    assert unsent({}, "t", repos) == repos

def test_record_sent_appends_without_duplicates():
    state = record_sent({"t": [1]}, "t", [1, 2, 3])
    assert state["t"] == [1, 2, 3]

def test_record_sent_caps_fifo():
    state = record_sent({}, "t", list(range(10)), cap=3)
    assert state["t"] == [7, 8, 9]


def test_unposted_drops_globally_posted_ids():
    state = {POSTED_KEY: [1, 2]}
    repos = [FakeRepo(1), FakeRepo(3), FakeRepo(4)]
    assert [r.id for r in unposted(state, repos)] == [3, 4]


def test_unposted_empty_state_keeps_all():
    repos = [FakeRepo(9)]
    assert unposted({}, repos) == repos


def test_record_posted_appends_without_duplicates_and_caps():
    state = record_posted({POSTED_KEY: [1]}, [1, 2, 3])
    assert state[POSTED_KEY] == [1, 2, 3]
    state = record_posted({}, list(range(10)), cap=3)
    assert state[POSTED_KEY] == [7, 8, 9]
