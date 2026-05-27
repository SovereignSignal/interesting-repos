import httpx
import pytest
from bot.telegram import send_message


def test_send_message_posts_expected_payload():
    seen = {}
    def handler(request):
        import json
        seen.update(json.loads(request.content))
        assert request.url.path == "/bottok/sendMessage"
        return httpx.Response(200, json={"ok": True})
    client = httpx.Client(transport=httpx.MockTransport(handler))
    out = send_message("tok", "-100", "hi", client=client, sleep=lambda s: None)
    assert out == {"ok": True}
    assert seen["chat_id"] == "-100" and seen["text"] == "hi"
    assert seen["parse_mode"] == "HTML"

def test_send_message_retries_then_succeeds():
    calls = {"n": 0}
    def handler(request):
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(500)
        return httpx.Response(200, json={"ok": True})
    client = httpx.Client(transport=httpx.MockTransport(handler))
    out = send_message("tok", "-1", "hi", client=client, retries=3, sleep=lambda s: None)
    assert out == {"ok": True} and calls["n"] == 3

def test_send_message_raises_sanitized_error_after_exhausting_retries():
    def handler(request):
        return httpx.Response(500)
    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(RuntimeError) as excinfo:
        send_message("secrettoken", "-1", "hi", client=client, retries=2, sleep=lambda s: None)
    msg = str(excinfo.value)
    assert "HTTP 500" in msg
    assert "secrettoken" not in msg   # the bot token must never leak into the error
