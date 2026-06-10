import httpx

from bot.alerts import llm_reachable, send_alert


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def _content_client(content):
    return _client(lambda r: httpx.Response(200, json={"message": {"content": content}}))


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
