import re

# Scripts we translate to English. Latin-script text (incl. accented European
# languages) is left as-is; the goal is to make non-Latin descriptions readable.
_NON_LATIN = re.compile(
    "[぀-ヿ"   # Hiragana, Katakana
    "㐀-䶿"    # CJK Extension A
    "一-鿿"    # CJK Unified Ideographs
    "가-힯"    # Hangul
    "Ѐ-ӿ"    # Cyrillic
    "֐-׿"    # Hebrew
    "؀-ۿ"    # Arabic
    "]"
)

_MODEL = "claude-haiku-4-5"


def needs_translation(text: str) -> bool:
    return bool(_NON_LATIN.search(text))


def translate_to_english(text: str, api_key: str = "", client=None) -> str:
    """Translate a non-Latin-script description to English via Claude.

    Returns the text unchanged when there's nothing to do (empty, no API key,
    or already Latin script) and on any API error, so the digest never breaks.
    """
    if not text or not api_key or not needs_translation(text):
        return text
    try:
        if client is None:
            import anthropic  # lazy: only needed when translating
            client = anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model=_MODEL,
            max_tokens=200,
            messages=[{
                "role": "user",
                "content": (
                    "Translate this GitHub repository description to concise English. "
                    "Return ONLY the translation, with no quotes or extra notes:\n\n"
                    f"{text}"
                ),
            }],
        )
        return resp.content[0].text.strip() or text
    except Exception:
        return text
