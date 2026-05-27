from bot.translate import needs_translation, translate_to_english


def test_needs_translation_detects_non_latin_scripts():
    assert needs_translation("微信账单分析工具")          # Chinese
    assert needs_translation("基于Python的开源量化交易")   # mixed CJK + Latin
    assert needs_translation("Привет мир")               # Cyrillic


def test_needs_translation_false_for_latin_text():
    assert not needs_translation("A widget toolkit for Python")
    assert not needs_translation("café résumé")   # accented Latin is left alone
    assert not needs_translation("")


def test_translate_returns_original_without_key():
    assert translate_to_english("微信账单", api_key="") == "微信账单"


def test_translate_skips_english_even_with_key():
    # English text must not trigger an API call; returned unchanged.
    assert translate_to_english("Hello world", api_key="key") == "Hello world"


def test_translate_uses_client_for_non_english():
    class FakeMessages:
        def create(self, **kwargs):
            class Msg:
                content = [type("B", (), {"text": "WeChat bill analysis tool"})()]
            return Msg()

    class FakeAnthropic:
        messages = FakeMessages()

    out = translate_to_english("微信账单分析工具", api_key="key", client=FakeAnthropic())
    assert out == "WeChat bill analysis tool"


def test_translate_falls_back_to_original_on_error():
    class BoomClient:
        def __getattr__(self, _):
            raise RuntimeError("boom")

    assert translate_to_english("微信账单", api_key="key", client=BoomClient()) == "微信账单"
