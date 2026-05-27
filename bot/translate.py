import re

import httpx

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
_DEFAULT_MODEL = "gemma3:12b"


def needs_translation(text: str) -> bool:
    return bool(_NON_LATIN.search(text))


def translate_to_english(text: str, host: str = _DEFAULT_HOST, model: str = _DEFAULT_MODEL,
                         api_key: str = "", client: httpx.Client | None = None) -> str:
    """Translate a non-Latin-script description to English via an Ollama chat model.

    Works against Ollama Cloud (https://ollama.com + api_key) or a local Ollama
    (http://localhost:11434, no key). Returns the text unchanged when there's
    nothing to do (empty, no host, already Latin script) and on any error, so the
    digest never breaks.
    """
    if not text or not host or not needs_translation(text):
        return text
    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    payload = {
        "model": model,
        "messages": [{
            "role": "user",
            "content": (
                "Translate this GitHub repository description to concise English. "
                "Return ONLY the translation, with no quotes or extra notes:\n\n"
                f"{text}"
            ),
        }],
        "stream": False,
    }
    owns_client = client is None
    client = client or httpx.Client(timeout=60)
    try:
        resp = client.post(f"{host}/api/chat", json=payload, headers=headers)
        resp.raise_for_status()
        return resp.json().get("message", {}).get("content", "").strip() or text
    except Exception:
        return text
    finally:
        if owns_client:
            client.close()
