import json

import httpx

from bot.translate import needs_translation, translate_to_english


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_needs_translation_detects_non_latin_scripts():
    assert needs_translation("微信账单分析工具")          # Chinese
    assert needs_translation("基于Python的开源量化交易")   # mixed CJK + Latin
    assert needs_translation("Привет мир")               # Cyrillic


def test_needs_translation_false_for_latin_text():
    assert not needs_translation("A widget toolkit for Python")
    assert not needs_translation("café résumé")   # accented Latin is left alone
    assert not needs_translation("")


def test_translate_skips_english_without_calling_ollama():
    def handler(request):
        raise AssertionError("must not call Ollama for English text")
    assert translate_to_english("Hello world", client=_client(handler)) == "Hello world"


def test_translate_disabled_when_host_empty():
    def handler(request):
        raise AssertionError("must not call Ollama when host is empty")
    assert translate_to_english("微信账单", host="", client=_client(handler)) == "微信账单"


def test_translate_calls_ollama_chat_and_sends_auth():
    seen = {}
    def handler(request):
        seen["path"] = request.url.path
        seen["auth"] = request.headers.get("Authorization")
        body = json.loads(request.content)
        seen["model"] = body["model"]
        seen["stream"] = body["stream"]
        return httpx.Response(200, json={"message": {"content": "WeChat bill analysis tool"}})
    out = translate_to_english("微信账单分析工具", model="gemma3:12b",
                               api_key="key", client=_client(handler))
    assert out == "WeChat bill analysis tool"
    assert seen["path"] == "/api/chat"
    assert seen["auth"] == "Bearer key"
    assert seen["model"] == "gemma3:12b" and seen["stream"] is False


def test_translate_omits_auth_without_key():
    def handler(request):
        assert request.headers.get("Authorization") is None
        return httpx.Response(200, json={"message": {"content": "hi"}})
    assert translate_to_english("привет", client=_client(handler)) == "hi"


def test_translate_falls_back_to_original_on_error():
    def handler(request):
        return httpx.Response(500)
    assert translate_to_english("微信账单", client=_client(handler)) == "微信账单"
