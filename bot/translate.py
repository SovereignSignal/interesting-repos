import re

from bot.config import DEFAULT_OLLAMA_MODEL
from bot.ollama import chat

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

_DEFAULT_HOST = "https://ollama.com"
_DEFAULT_MODEL = DEFAULT_OLLAMA_MODEL


def needs_translation(text: str) -> bool:
    return bool(_NON_LATIN.search(text))


def translate_to_english(text: str, host: str = _DEFAULT_HOST, model: str = _DEFAULT_MODEL,
                         api_key: str = "", client=None) -> str:
    """Translate a non-Latin-script description to English via an Ollama chat model.

    Returns the text unchanged when there's nothing to do (empty, no host, already
    Latin script) and on any error, so the digest never breaks.
    """
    if not text or not host or not needs_translation(text):
        return text
    prompt = (
        "Translate this GitHub repository description to concise English. "
        "Return ONLY the translation, with no quotes or extra notes:\n\n"
        f"{text}"
    )
    return chat(prompt, host=host, model=model, api_key=api_key, client=client,
                think=False) or text
