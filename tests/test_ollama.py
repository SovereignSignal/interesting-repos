import json

import httpx

from bot.ollama import chat


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_chat_posts_to_api_chat_with_auth_and_returns_content():
    seen = {}
    def handler(request):
        seen["path"] = request.url.path
        seen["auth"] = request.headers.get("Authorization")
        body = json.loads(request.content)
        seen["model"] = body["model"]
        seen["stream"] = body["stream"]
        return httpx.Response(200, json={"message": {"content": "hello"}})
    out = chat("hi", host="https://ollama.com", model="gemma3:12b", api_key="k", client=_client(handler))
    assert out == "hello"
    assert seen["path"] == "/api/chat" and seen["auth"] == "Bearer k"
    assert seen["model"] == "gemma3:12b" and seen["stream"] is False


def test_chat_omits_auth_without_key():
    def handler(request):
        assert request.headers.get("Authorization") is None
        return httpx.Response(200, json={"message": {"content": "x"}})
    assert chat("p", host="http://localhost:11434", model="m", client=_client(handler)) == "x"


def test_chat_returns_empty_when_host_blank():
    def handler(request):
        raise AssertionError("must not call Ollama when host is blank")
    assert chat("p", host="", model="m", client=_client(handler)) == ""


def test_chat_returns_empty_on_error():
    def handler(request):
        return httpx.Response(500)
    assert chat("p", host="h", model="m", client=_client(handler)) == ""
