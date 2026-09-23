import httpx


def _post_chat(prompt: str, host: str, model: str, api_key: str = "",
               client: httpx.Client | None = None, timeout: float = 60,
               think: bool | None = None) -> tuple[dict | None, str | None]:
    """POST /api/chat. Returns ``(body, None)`` on HTTP 200 JSON, else
    ``(None, reason)``. ``reason`` is ``"timeout"`` or ``"request failed"``.
    Never raises. A blank host is ``(None, None)`` — LLM disabled, not a failure."""
    if not host:
        return None, None
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
        if isinstance(data, dict):
            return data, None
        return None, "request failed"
    except httpx.TimeoutException:
        return None, "timeout"
    except Exception:
        return None, "request failed"
    finally:
        if owns_client:
            client.close()


def chat_result(prompt: str, host: str, model: str, api_key: str = "",
                client: httpx.Client | None = None, timeout: float = 60,
                think: bool | None = None) -> tuple[str, str | None]:
    """``(text, failure)``. ``failure`` is None when text is non-empty.

    ``"empty content"`` is HTTP 200 with a blank ``message.content`` (Gemma 4's
    thinking channel). ``"timeout"`` and ``"request failed"`` are transport
    failures. A blank host returns ``("", None)`` — disabled, not a failure.
    """
    data, reason = _post_chat(prompt, host, model, api_key, client, timeout, think)
    if data is None:
        if not host:
            return "", None
        return "", reason or "request failed"
    text = ((data.get("message") or {}).get("content") or "").strip()
    if not text:
        return "", "empty content"
    return text, None


def chat(prompt: str, host: str, model: str, api_key: str = "",
         client: httpx.Client | None = None, timeout: float = 60,
         think: bool | None = None) -> str:
    """Single-turn chat against an Ollama /api/chat endpoint (cloud or local).

    Returns the assistant's text, or "" on any error or when no host is set.
    Never raises — callers decide how to fall back. Pass think=False so Gemma 4
    fills message.content instead of leaving it empty (the thinking channel).
    Ping, titles, translation, scoring, and summaries all do.
    """
    text, _reason = chat_result(prompt, host, model, api_key, client, timeout, think)
    return text


def chat_accepted(prompt: str, host: str, model: str, api_key: str = "",
                  client: httpx.Client | None = None, timeout: float = 60,
                  think: bool | None = False) -> bool:
    """True iff the host accepted `model` (HTTP 200 JSON), even when content is blank.

    Gemma 4's default thinking path often returns 200 with empty message.content
    (the trace sits in message.thinking). Using chat() for health checks then
    pages 'model unavailable' on a live model every cron.
    """
    data, _reason = _post_chat(prompt, host, model, api_key, client, timeout, think)
    return data is not None
