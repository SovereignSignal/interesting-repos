import json as _json

import httpx

from bot.alerts import llm_reachable, send_alert, resolve_curator


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def _content_client(content):
    return _client(lambda r: httpx.Response(200, json={"message": {"content": content}}))


def _model_aware_client(reachable, seen=None):
    """200 (pong) for models in `reachable`, 410 (retired) otherwise; records probes."""
    def handler(request):
        model = _json.loads(request.content)["model"]
        if seen is not None:
            seen.append(model)
        if model in reachable:
            return httpx.Response(200, json={"message": {"content": "pong"}})
        return httpx.Response(410, text="retired")
    return _client(handler)


def test_llm_reachable_true_without_host():
    assert llm_reachable("", "m") is True          # LLM not configured -> not a failure


def test_llm_reachable_true_when_chat_responds():
    assert llm_reachable("http://x", "m", client=_content_client("pong")) is True


def test_llm_reachable_false_on_auth_error():
    c = _client(lambda r: httpx.Response(401, text="unauthorized"))
    assert llm_reachable("http://x", "m", client=c) is False   # 401 -> chat "" -> unreachable


def test_send_alert_noop_without_chat_id():
    calls = []
    c = _client(lambda r: calls.append(r) or httpx.Response(200, json={"ok": True}))
    assert send_alert("tok", "", "boom", client=c) is False
    assert calls == []                             # no POST when no alert target


def test_send_alert_sends_to_chat():
    captured = {}
    def handler(request):
        import json as _json
        captured["body"] = _json.loads(request.content)
        return httpx.Response(200, json={"ok": True})
    assert send_alert("tok", "123456789", "⚠️ degraded", client=_client(handler)) is True
    assert captured["body"]["chat_id"] == "123456789"
    assert "degraded" in captured["body"]["text"]


def test_send_alert_false_on_send_failure():
    c = _client(lambda r: httpx.Response(500))
    assert send_alert("tok", "C1", "boom", client=c) is False   # never raises


def test_resolve_curator_first_candidate_reachable():
    c = _model_aware_client({"a", "b", "base"})
    assert resolve_curator("http://x", ("a", "b"), "base", client=c) == ("a", [])


def test_resolve_curator_skips_dead_primary_to_next():
    c = _model_aware_client({"b", "base"})            # a is down
    assert resolve_curator("http://x", ("a", "b"), "base", client=c) == ("b", ["a"])


def test_resolve_curator_falls_through_to_base():
    c = _model_aware_client({"base"})                 # a, b down
    assert resolve_curator("http://x", ("a", "b"), "base", client=c) == ("base", ["a", "b"])


def test_resolve_curator_none_when_all_dead():
    c = _model_aware_client(set())                    # everything down
    assert resolve_curator("http://x", ("a", "b"), "base", client=c) == (None, ["a", "b", "base"])


def test_resolve_curator_dedupes_base_already_in_candidates():
    seen = []
    c = _model_aware_client(set(), seen)              # all down, capture probe order
    model, skipped = resolve_curator("http://x", ("a", "base"), "base", client=c)
    assert seen == ["a", "base"]                      # base rung not probed twice
    assert (model, skipped) == (None, ["a", "base"])


def test_resolve_curator_no_pings_without_host():
    calls = []
    c = _client(lambda r: calls.append(r) or httpx.Response(200, json={"message": {"content": "x"}}))
    assert resolve_curator("", ("a", "b"), "base", client=c) == ("base", [])
    assert calls == []                                # LLM disabled -> no probes


def test_resolve_curator_single_candidate_equals_base():
    # common single-model setup: one curator that is also the base -> probed once, no skips
    c = _model_aware_client({"gemma3:12b"})
    assert resolve_curator("http://x", ("gemma3:12b",), "gemma3:12b", client=c) == ("gemma3:12b", [])
