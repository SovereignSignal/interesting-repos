from bot.ollama import chat
from bot.telegram import send_message


def llm_reachable(host: str, model: str, api_key: str = "", client=None) -> bool:
    """Pre-flight health check: True when no host is configured (LLM unused → not a
    failure), else True iff a one-shot chat ping round-trips. A 401/outage makes chat()
    return "" → False, so a silently-degrading run can be alerted instead of hiding."""
    if not host:
        return True
    return bool(chat("ping", host=host, model=model, api_key=api_key, client=client))


def send_alert(token: str, chat_id: str, text: str, client=None) -> bool:
    """DM a failure alert via Telegram. No-op (returns False) when chat_id is unset;
    never raises — an alert failure must not break the run. One try (no retry storm)."""
    if not chat_id:
        return False
    try:
        send_message(token, chat_id, text, client=client, retries=1)
        return True
    except Exception:
        return False
