import time
import httpx

_API = "https://api.telegram.org"


def send_message(token: str, chat_id: str, text: str, parse_mode: str = "HTML",
                 client: httpx.Client | None = None, retries: int = 3,
                 sleep=time.sleep) -> dict:
    url = f"{_API}/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": False,
    }
    owns_client = client is None
    client = client or httpx.Client(timeout=30)
    last_exc: httpx.HTTPError | None = None
    try:
        for attempt in range(retries):
            try:
                resp = client.post(url, json=payload)
                resp.raise_for_status()
                return resp.json()
            except httpx.HTTPError as exc:
                last_exc = exc
                if attempt < retries - 1:
                    sleep(2 ** attempt)
        # Raise a sanitized error: the URL embeds the bot token in its path, so we
        # must never let an exception carrying that URL reach logs/tracebacks.
        status = getattr(getattr(last_exc, "response", None), "status_code", None)
        detail = f"HTTP {status}" if status else type(last_exc).__name__
        raise RuntimeError(
            f"Telegram sendMessage failed after {retries} attempts: {detail}"
        ) from None
    finally:
        if owns_client:
            client.close()
