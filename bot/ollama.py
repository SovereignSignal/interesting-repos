import httpx


def _post_chat(prompt: str, host: str, model: str, api_key: str = "",
               client: httpx.Client | None = None, timeout: float = 60,
               think: bool | None = None) -> dict | None:
    """POST /api/chat. Returns the JSON body on HTTP 200, else None. Never raises."""
    if not host:
        return None
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
    }
    if think is not None:
        payload["think"] = think
    owns_client = client is None
    client = client or httpx.Client(timeout=timeout)
    try:
        resp = client.post(f"{host}/api/chat", json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, dict) else None
    except Exception:
        return None
    finally:
        if owns_client:
            client.close()


def chat(prompt: str, host: str, model: str, api_key: str = "",
         client: httpx.Client | None = None, timeout: float = 60,
         think: bool | None = None) -> str:
    """Single-turn chat against an Ollama /api/chat endpoint (cloud or local).

    Returns the assistant's text, or "" on any error or when no host is set.
    Never raises — callers decide how to fall back. Pass think=False for short
    tasks (ping, titles, translation) so Gemma 4 doesn't spend the budget in
    the thinking channel and leave message.content empty.
    """
    data = _post_chat(prompt, host, model, api_key, client, timeout, think)
    if not data:
        return ""
    return ((data.get("message") or {}).get("content") or "").strip()


def chat_accepted(prompt: str, host: str, model: str, api_key: str = "",
                  client: httpx.Client | None = None, timeout: float = 60,
                  think: bool | None = False) -> bool:
    """True iff the host accepted `model` (HTTP 200 JSON), even when content is blank.

    Gemma 4's default thinking path often returns 200 with empty message.content
    (the trace sits in message.thinking). Using chat() for health checks then
    pages 'model unavailable' on a live model every cron.
    """
    return _post_chat(prompt, host, model, api_key, client, timeout, think) is not None
