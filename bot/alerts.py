from bot.ollama import chat
from bot.telegram import send_message


def llm_reachable(host: str, model: str, api_key: str = "", client=None) -> bool:
    """Pre-flight health check: True when no host is configured (LLM unused → not a
    failure), else True iff a one-shot chat ping round-trips. A 401/outage makes chat()
    return "" → False, so a silently-degrading run can be alerted instead of hiding."""
    if not host:
        return True
    return bool(chat("ping", host=host, model=model, api_key=api_key, client=client))


def resolve_curator(host: str, candidates, base_model: str, api_key: str = "",
                    client=None) -> tuple:
    """Pick the first reachable curator model, walking `candidates` in order and falling
    back to `base_model` as the final rung. Returns (model_or_None, skipped), where
    `skipped` is the models tried-and-failed before the chosen one ([] if the first
    works). (None, all_tried) when nothing is reachable → caller degrades to stars.
    No pings when host is unset (LLM disabled) → (base_model or None, []). Lets a retired/
    401 primary (the recurring failure mode) self-heal to a working model instead of
    degrading the whole run."""
    if not host:
        return (base_model or None, [])
    order: list = []
    for m in (*candidates, base_model):
        if m and m not in order:    # base appended once; dedupe if already a candidate
            order.append(m)
    skipped: list = []
    for m in order:
        if llm_reachable(host, m, api_key, client=client):
            return (m, skipped)
        skipped.append(m)
    return (None, skipped)


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
