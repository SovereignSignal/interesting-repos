import httpx


def chat(prompt: str, host: str, model: str, api_key: str = "",
         client: httpx.Client | None = None, timeout: float = 60) -> str:
    """Single-turn chat against an Ollama /api/chat endpoint (cloud or local).

    Returns the assistant's text, or "" on any error or when no host is set.
    Never raises — callers decide how to fall back.
    """
    if not host:
        return ""
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
    }
    owns_client = client is None
    client = client or httpx.Client(timeout=timeout)
    try:
        resp = client.post(f"{host}/api/chat", json=payload, headers=headers)
        resp.raise_for_status()
        return resp.json().get("message", {}).get("content", "").strip()
    except Exception:
        return ""
    finally:
        if owns_client:
            client.close()
