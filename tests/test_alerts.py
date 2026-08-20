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


_NO_SLEEP = lambda _: None    # skip real backoff delays in tests


def _flaky_client(fail_first: int):
    """Returns (client, calls): the first `fail_first` pings 500, then 200 pong.
    Simulates a transient blip that recovers on retry."""
    calls = {"n": 0}
    def handler(request):
        calls["n"] += 1
        if calls["n"] <= fail_first:
            return httpx.Response(500)
        return httpx.Response(200, json={"message": {"content": "pong"}})
    return _client(handler), calls


def test_llm_reachable_true_without_host():
    assert llm_reachable("", "m") is True          # LLM not configured -> not a failure


def test_llm_reachable_true_when_chat_responds():
    assert llm_reachable("http://x", "m", client=_content_client("pong")) is True


def test_llm_reachable_false_on_auth_error():
    c = _client(lambda r: httpx.Response(401, text="unauthorized"))
    # 401 every attempt -> sustained failure -> unreachable (no_sleep skips backoff)
    assert llm_reachable("http://x", "m", client=c, sleep=_NO_SLEEP) is False


def test_llm_reachable_rides_out_transient_blip():
    c, calls = _flaky_client(fail_first=1)   # first ping 500, second 200
    assert llm_reachable("http://x", "m", client=c, sleep=_NO_SLEEP) is True
    assert calls["n"] == 2                    # retried past the blip, didn't page


def test_llm_reachable_false_after_exhausting_attempts():
    c, calls = _flaky_client(fail_first=99)   # never recovers
    assert llm_reachable("http://x", "m", client=c, attempts=3, sleep=_NO_SLEEP) is False
    assert calls["n"] == 3                     # tried exactly `attempts` times


def test_llm_reachable_single_attempt_does_not_retry():
    c, calls = _flaky_client(fail_first=99)
    assert llm_reachable("http://x", "m", client=c, attempts=1, sleep=_NO_SLEEP) is False
    assert calls["n"] == 1                     # attempts=1 -> one-shot, no retry


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
    assert resolve_curator("http://x", ("a", "b"), "base", client=c,
                           sleep=_NO_SLEEP) == ("b", ["a"])


def test_resolve_curator_falls_through_to_base():
    c = _model_aware_client({"base"})                 # a, b down
    assert resolve_curator("http://x", ("a", "b"), "base", client=c,
                           sleep=_NO_SLEEP) == ("base", ["a", "b"])


def test_resolve_curator_none_when_all_dead():
    c = _model_aware_client(set())                    # everything down
    assert resolve_curator("http://x", ("a", "b"), "base", client=c,
                           sleep=_NO_SLEEP) == (None, ["a", "b", "base"])


def test_resolve_curator_tolerates_transient_blip_on_primary():
    # 'a' fails its first probe then recovers; the retry must keep it as the curator
    # instead of skipping to 'b' and firing a spurious curator-fallback heads-up.
    state = {"a": 0}
    def handler(request):
        model = _json.loads(request.content)["model"]
        if model == "a":
            state["a"] += 1
            if state["a"] == 1:
                return httpx.Response(500)            # transient blip on first probe
        return httpx.Response(200, json={"message": {"content": "pong"}})
    c = _client(handler)
    assert resolve_curator("http://x", ("a", "b"), "base", client=c,
                           sleep=_NO_SLEEP) == ("a", [])


def test_resolve_curator_dedupes_base_already_in_candidates():
    seen = []
    c = _model_aware_client(set(), seen)              # all down, capture probe order
    # attempts=1 isolates the dedup-order check from retry behavior
    model, skipped = resolve_curator("http://x", ("a", "base"), "base", client=c, attempts=1)
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
