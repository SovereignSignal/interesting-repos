"""AI Wire ingest: mapping, flag-off no-op, and a push that never fails the run."""
import json
import logging
from datetime import datetime, timezone

import httpx

import bot.main as main
from bot.ai_wire import (
    BATCH_LIMIT,
    backfill,
    history_items,
    ingest_endpoint,
    ingest_item,
    items_for_indexes,
    push_items,
    push_posted,
)
from bot.config import Config, Theme
from bot.github import Repo
from bot.ranker import Pick


TOKEN = "super-secret-token"
WHEN = datetime(2026, 10, 10, 13, tzinfo=timezone.utc)


def _repo():
    return Repo(
        7, "Acme/Cool-Widgets", "https://github.com/Acme/Cool-Widgets",
        "Layouts text on the screen.", 1500, "Rust", ["text", "layout"],
        False, False,
    )


def _item(**overrides):
    base = ingest_item(
        _repo(),
        title="Cool Widgets",
        summary="Layouts text on the screen.",
        lane="Dev Tools & CLI",
        score=8,
        stars_gained=40,
        posted_at=WHEN,
        message_id=979,
    )
    base.update(overrides)
    return base


class _Client:
    def __init__(self, handler):
        self.calls = []
        self._handler = handler

    def post(self, url, json=None, headers=None, timeout=None, **kwargs):
        self.calls.append({
            "url": url, "json": json, "headers": headers, "timeout": timeout,
        })
        return self._handler(self.calls[-1])

    def close(self):
        self.calls.append("close")


def test_ingest_item_maps_a_posted_repo():
    item = _item()
    assert item == {
        "source_bot": "interesting-repos",
        "kind": "repo",
        "canonical_key": "repo:acme/cool-widgets",
        "title": "Cool Widgets",
        "url": "https://github.com/Acme/Cool-Widgets",
        "channel": "interestingrepos",
        "summary": "Layouts text on the screen.",
        "posted_at": "2026-10-10T13:00:00+00:00",
        "lane": "Dev Tools & CLI",
        "channel_post_url": "https://t.me/interestingrepos/979",
        "tags": ["text", "layout", "Rust"],
        "score": 8,
        "extra": {"stars": 1500, "stars_gained": 40},
    }


def test_ingest_item_omits_unknown_score_gain_and_message():
    item = ingest_item(_repo(), title="Cool Widgets", lane="Dev Tools")
    assert item["canonical_key"] == "repo:acme/cool-widgets"
    assert item["extra"] == {"stars": 1500}
    assert "score" not in item
    assert "stars_gained" not in item["extra"]
    assert "summary" not in item
    assert "channel_post_url" not in item
    assert "posted_at" not in item


def test_ingest_item_caps_summary_and_dedupes_language_tag():
    repo = Repo(1, "acme/widgets", "https://github.com/acme/widgets", "d", 1, "Rust",
                ["rust", "cli"], False, False)
    item = ingest_item(repo, title="Widgets", summary="y" * 700, lane="T")
    assert len(item["summary"]) == 600
    assert item["tags"] == ["rust", "cli"]


def test_ingest_item_rejects_a_name_that_is_not_owner_repo():
    repo = Repo(1, "not-a-repo", "https://example.test", "d", 1, "", [], False, False)
    assert ingest_item(repo, title="X", lane="T") is None


def test_summary_is_the_posted_blurb_not_a_rejected_line():
    repo = Repo(
        1, "acme/pretext", "https://github.com/acme/pretext",
        "Measures text layout on the screen.", 10, "Rust", ["text"], False, False,
    )
    items = items_for_indexes(
        theme_name="Dev Tools",
        picks=[Pick(repo, "curator why", 9)],
        titles=["Pretext"],
        summaries=["This project is notable."],
        indexes=(0,),
        describe=lambda r: "",
        translate=lambda s: s,
        deltas=None,
        gains={},
        posted_at=WHEN,
        response={"ok": True, "result": {"message_id": 979}},
    )
    assert items[0]["summary"] == "Measures text layout on the screen."
    assert "notable" not in items[0]["summary"]
    assert items[0]["score"] == 9
    assert items[0]["channel_post_url"] == "https://t.me/interestingrepos/979"
    assert "why" not in items[0]["summary"]


def test_stars_gained_uses_the_delta_then_the_trending_gain():
    repo = _repo()
    pick = Pick(repo, "", None)
    common = dict(
        theme_name="Movers", picks=[pick], titles=["Cool Widgets"], summaries=[None],
        indexes=(0,), describe=lambda r: "", translate=lambda s: s, posted_at=WHEN,
        response={"message_id": 4},
    )
    from_delta = items_for_indexes(deltas=[3], gains={7: (40, "weekly")}, **common)
    assert from_delta[0]["extra"]["stars_gained"] == 3
    from_gain = items_for_indexes(deltas=[None], gains={7: (40, "daily")}, **common)
    assert from_gain[0]["extra"]["stars_gained"] == 40
    unknown = items_for_indexes(deltas=None, gains={}, **common)
    assert "stars_gained" not in unknown[0]["extra"]


def test_flag_off_does_not_post():
    called = []

    class Client:
        def post(self, *args, **kwargs):
            called.append(1)
            raise AssertionError("posted")

    cfg = type("C", (), {})()
    cfg.ai_wire_enabled = False
    cfg.ai_wire_url = "https://wire.example"
    cfg.ai_wire_ingest_token = TOKEN
    assert push_posted(cfg, [_item()], client=Client()) is True
    assert called == []


def test_push_failure_never_raises_and_retries_once(caplog):
    caplog.set_level(logging.INFO)

    class Client:
        def __init__(self):
            self.n = 0

        def post(self, *args, **kwargs):
            self.n += 1
            raise httpx.ConnectError(f"down {TOKEN}")

    client = Client()
    ok = push_items([_item()], base_url="https://wire.example", token=TOKEN, client=client)
    assert ok is False
    assert client.n == 2
    assert "ai_wire push failed:" in caplog.text
    assert caplog.text.count("ai_wire push failed:") == 1
    assert TOKEN not in caplog.text


def test_push_retries_once_then_logs_upserted(caplog):
    caplog.set_level(logging.INFO)
    client = _Client(lambda call: (
        httpx.Response(500, json={"error": "nope"}) if len(client.calls) == 1
        else httpx.Response(200, json={"upserted": 4, "created": 1, "keys": []})
    ))
    # The handler above sees the call already appended, so the first call has len 1.
    ok = push_items([_item()], base_url="https://wire.example/", token=TOKEN, client=client)
    assert ok is True
    assert len(client.calls) == 2
    assert client.calls[0]["url"] == "https://wire.example/api/ingest/items"
    assert client.calls[0]["headers"]["Authorization"] == f"Bearer {TOKEN}"
    assert client.calls[0]["timeout"] == 5.0
    assert client.calls[0]["json"]["items"][0]["canonical_key"] == "repo:acme/cool-widgets"
    assert "ai_wire push ok n=4" in caplog.text
    assert "ai_wire push failed:" not in caplog.text


def test_push_is_one_batch_until_the_api_ceiling():
    def handler(call):
        return httpx.Response(200, json={"upserted": len(call["json"]["items"]), "created": 0, "keys": []})

    client = _Client(handler)
    items = [{"canonical_key": f"repo:a/{i}", "title": "T", "url": "https://github.com/a/x"} for i in range(3)]
    assert push_items(items, base_url="https://wire.example", token=TOKEN, client=client) is True
    assert len(client.calls) == 1
    assert len(client.calls[0]["json"]["items"]) == 3

    client = _Client(handler)
    many = [{"canonical_key": f"repo:a/{i}"} for i in range(BATCH_LIMIT + 1)]
    assert push_items(many, base_url="https://wire.example", token=TOKEN, client=client) is True
    assert [len(call["json"]["items"]) for call in client.calls] == [BATCH_LIMIT, 1]


def test_default_client_uses_a_five_second_timeout(monkeypatch):
    seen = {}

    class Client:
        def __init__(self, timeout=None):
            seen["timeout"] = timeout

        def post(self, url, json=None, headers=None, timeout=None, **kwargs):
            seen["post_timeout"] = timeout
            return httpx.Response(200, json={"upserted": 1, "created": 1, "keys": []})

        def close(self):
            seen["closed"] = True

    monkeypatch.setattr("bot.ai_wire.httpx.Client", Client)
    assert push_items([_item()], base_url="https://wire.example", token=TOKEN) is True
    assert seen["timeout"] == 5.0 and seen["post_timeout"] == 5.0 and seen["closed"] is True


def test_missing_credentials_log_failed_and_do_not_raise(caplog):
    caplog.set_level(logging.WARNING)
    assert push_items([_item()], base_url="", token="") is False
    assert "ai_wire push failed:" in caplog.text


def test_ingest_endpoint_appends_the_path_once():
    assert ingest_endpoint("https://wire.example/") == "https://wire.example/api/ingest/items"
    assert ingest_endpoint("https://wire.example/api/ingest/items") == "https://wire.example/api/ingest/items"


def test_history_id_lists_are_not_enough_and_full_name_is():
    assert history_items({"trending": [1, 2], "_posted": [1]}) == []
    items = history_items({"_ai_wire": [{
        "full_name": "Acme/Widget",
        "summary": "Draws widgets on the screen.",
        "lane": "Web",
    }]})
    assert items[0]["canonical_key"] == "repo:acme/widget"
    assert items[0]["title"] == "Widget"
    assert items[0]["url"] == "https://github.com/Acme/Widget"
    assert items[0]["summary"] == "Draws widgets on the screen."
    assert items[0]["lane"] == "Web"
    assert items[0]["source_bot"] == "interesting-repos"


def _cfg(tmp_path, **kwargs):
    return Config(
        "tok", "-100", "", str(tmp_path), [],
        ai_wire_url="https://wire.example",
        ai_wire_ingest_token=TOKEN,
        **kwargs,
    )


def test_backfill_dry_run_prints_and_does_not_post(tmp_path, capsys, caplog):
    caplog.set_level(logging.INFO)
    (tmp_path / "state.json").write_text(json.dumps({"_ai_wire": [_item()]}))
    called = []

    class Client:
        def post(self, *args, **kwargs):
            called.append(1)
            raise AssertionError("posted")

    rc = backfill(_cfg(tmp_path, ai_wire_enabled=True), send=False, client=Client())
    assert rc == 0 and called == []
    out = capsys.readouterr().out
    body = json.loads(out)
    assert body["items"][0]["canonical_key"] == "repo:acme/cool-widgets"
    assert "ai_wire backfill dry-run n=1" in caplog.text
    assert TOKEN not in out


def test_backfill_id_only_history_does_not_post(tmp_path, capsys, caplog):
    caplog.set_level(logging.INFO)
    (tmp_path / "state.json").write_text(json.dumps({"trending": [1, 2], "_posted": [1]}))
    called = []

    class Client:
        def post(self, *args, **kwargs):
            called.append(1)

    rc = backfill(_cfg(tmp_path, ai_wire_enabled=True), send=True, client=Client())
    out = capsys.readouterr().out
    assert rc == 0 and called == []
    assert "does not hold" in out
    assert "n=3 repo ids" in out


def test_backfill_send_posts_when_enabled(tmp_path, caplog):
    caplog.set_level(logging.INFO)
    (tmp_path / "state.json").write_text(json.dumps({"_ai_wire": [_item()]}))
    client = _Client(lambda call: httpx.Response(200, json={"upserted": 1, "created": 0, "keys": []}))
    rc = backfill(_cfg(tmp_path, ai_wire_enabled=True), send=True, client=client)
    assert rc == 0
    assert len(client.calls) == 1
    assert "ai_wire push ok n=1" in caplog.text


def test_backfill_send_flag_off_does_not_post(tmp_path, caplog):
    caplog.set_level(logging.INFO)
    (tmp_path / "state.json").write_text(json.dumps({"_ai_wire": [_item()]}))
    called = []

    class Client:
        def post(self, *args, **kwargs):
            called.append(1)

    rc = backfill(_cfg(tmp_path, ai_wire_enabled=False), send=True, client=Client())
    assert rc == 1 and called == []
    assert "ai_wire push failed: AI_WIRE_ENABLED is off" in caplog.text


def _wire_cfg(tmp_path, themes, enabled=True):
    return Config(
        "tok", "-100", "", str(tmp_path), themes, "",
        send_delay_seconds=0,
        ai_wire_enabled=enabled,
        ai_wire_url="https://wire.example",
        ai_wire_ingest_token=TOKEN,
    )


def _patch_delivery(monkeypatch, repos_for):
    monkeypatch.setattr(main, "search_repos", lambda query, **k: repos_for(query))
    monkeypatch.setattr(main, "readme_first_line", lambda *a, **k: "")
    events = []

    def send(*args, **kwargs):
        events.append("tg")
        return {"ok": True, "result": {"message_id": 100 + len(events)}}

    monkeypatch.setattr(main, "send_message", send)
    posts = []

    class Client:
        def __init__(self, timeout=None):
            posts.append(("init", timeout))

        def post(self, url, json=None, headers=None, timeout=None, **kwargs):
            events.append("push")
            posts.append(json)
            return httpx.Response(200, json={"upserted": len(json["items"]), "created": 0, "keys": []})

        def close(self):
            posts.append("close")

    monkeypatch.setattr("bot.ai_wire.httpx.Client", Client)
    return events, posts


def test_run_pushes_one_batch_after_telegram_succeeds(tmp_path, monkeypatch):
    def repos_for(query):
        if "AAA" in query:
            return [Repo(1, "acme/one", "https://github.com/acme/one",
                         "First tool for the digest.", 10, "Go", ["cli"], False, False)]
        if "BBB" in query:
            return [Repo(2, "acme/two", "https://github.com/acme/two",
                         "Second tool for the digest.", 8, "Rust", [], False, False)]
        return []

    events, posts = _patch_delivery(monkeypatch, repos_for)
    monkeypatch.setattr(main, "rank", lambda repos, theme, **k: [Pick(repos[0], "why", 8)])
    themes = [
        Theme(key="a", name="Alpha", emoji="", query="AAA", count=1),
        Theme(key="b", name="Beta", emoji="", query="BBB", count=1),
    ]
    failures = main.run(_wire_cfg(tmp_path, themes), now=WHEN)
    assert failures == 0
    assert events == ["tg", "tg", "push"]
    bodies = [entry for entry in posts if isinstance(entry, dict)]
    assert len(bodies) == 1
    items = bodies[0]["items"]
    assert [item["canonical_key"] for item in items] == ["repo:acme/one", "repo:acme/two"]
    assert items[0]["lane"] == "Alpha" and items[1]["lane"] == "Beta"
    assert items[0]["channel_post_url"] == "https://t.me/interestingrepos/101"
    assert items[1]["channel_post_url"] == "https://t.me/interestingrepos/102"
    assert items[0]["summary"] == "First tool for the digest."
    assert items[0]["score"] == 8
    assert items[1]["tags"] == ["Rust"]
    assert items[0]["extra"] == {"stars": 10}
    saved = json.loads((tmp_path / "state.json").read_text())
    assert saved["a"] == [1] and saved["b"] == [2]
    assert [row["canonical_key"] for row in saved["_ai_wire"]] == [
        "repo:acme/one", "repo:acme/two",
    ]


def test_run_flag_off_records_history_without_posting(tmp_path, monkeypatch):
    events, posts = _patch_delivery(
        monkeypatch,
        lambda query: [Repo(1, "acme/one", "https://github.com/acme/one",
                            "First tool for the digest.", 10, "Go", [], False, False)],
    )
    theme = Theme(key="a", name="Alpha", emoji="", query="q", count=1)
    failures = main.run(_wire_cfg(tmp_path, [theme], enabled=False), now=WHEN)
    assert failures == 0
    assert events == ["tg"]
    assert not any(isinstance(entry, dict) for entry in posts)
    saved = json.loads((tmp_path / "state.json").read_text())
    assert saved["_ai_wire"][0]["canonical_key"] == "repo:acme/one"


def test_run_dry_run_does_not_push(tmp_path, monkeypatch, capsys):
    events, posts = _patch_delivery(
        monkeypatch,
        lambda query: [Repo(1, "acme/one", "https://github.com/acme/one",
                            "First tool for the digest.", 10, "Go", [], False, False)],
    )
    theme = Theme(key="a", name="Alpha", emoji="", query="q", count=1)
    failures = main.run(_wire_cfg(tmp_path, [theme]), now=WHEN, dry_run=True)
    assert failures == 0 and events == [] and posts == []
    assert not (tmp_path / "state.json").exists()
    assert "acme/one" in capsys.readouterr().out


def test_run_wire_failure_does_not_fail_the_digest(tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.INFO)

    def send(*args, **kwargs):
        return {"ok": True, "result": {"message_id": 9}}

    monkeypatch.setattr(main, "search_repos", lambda *a, **k: [
        Repo(1, "acme/one", "https://github.com/acme/one",
             "First tool for the digest.", 10, "Go", [], False, False),
    ])
    monkeypatch.setattr(main, "readme_first_line", lambda *a, **k: "")
    monkeypatch.setattr(main, "send_message", send)

    class Client:
        def __init__(self, timeout=None):
            pass

        def post(self, *args, **kwargs):
            raise httpx.TimeoutException(f"timed out {TOKEN}")

        def close(self):
            pass

    monkeypatch.setattr("bot.ai_wire.httpx.Client", Client)
    theme = Theme(key="a", name="Alpha", emoji="", query="q", count=1)
    failures = main.run(_wire_cfg(tmp_path, [theme]), now=WHEN)
    assert failures == 0
    assert "ai_wire push failed:" in caplog.text
    assert TOKEN not in caplog.text
    assert json.loads((tmp_path / "state.json").read_text())["a"] == [1]


def test_run_does_not_push_repos_whose_telegram_send_failed(tmp_path, monkeypatch):
    def repos_for(query):
        if "AAA" in query:
            return [Repo(1, "acme/one", "https://github.com/acme/one",
                         "First tool for the digest.", 10, "Go", [], False, False)]
        if "BBB" in query:
            return [Repo(2, "acme/two", "https://github.com/acme/two",
                         "Second tool for the digest.", 8, "Rust", [], False, False)]
        return []

    monkeypatch.setattr(main, "search_repos", lambda query, **k: repos_for(query))
    monkeypatch.setattr(main, "readme_first_line", lambda *a, **k: "")
    sent = {"n": 0}

    def send(*args, **kwargs):
        sent["n"] += 1
        if sent["n"] == 2:
            raise RuntimeError("telegram down")
        return {"ok": True, "result": {"message_id": 15}}

    monkeypatch.setattr(main, "send_message", send)
    posts = []

    class Client:
        def __init__(self, timeout=None):
            pass

        def post(self, url, json=None, headers=None, timeout=None, **kwargs):
            posts.append(json)
            return httpx.Response(200, json={"upserted": 1, "created": 0, "keys": []})

        def close(self):
            pass

    monkeypatch.setattr("bot.ai_wire.httpx.Client", Client)
    themes = [
        Theme(key="a", name="Alpha", emoji="", query="AAA", count=1),
        Theme(key="b", name="Beta", emoji="", query="BBB", count=1),
    ]
    failures = main.run(_wire_cfg(tmp_path, themes), now=WHEN)
    assert failures == 1
    assert len(posts) == 1
    assert [item["canonical_key"] for item in posts[0]["items"]] == ["repo:acme/one"]
    saved = json.loads((tmp_path / "state.json").read_text())
    assert "a" in saved and "b" not in saved
