import time

from bot.ollama import chat_accepted
from bot.telegram import send_message

# resolve_title_model via= values. "base" includes a working cloud alias of OLLAMA_MODEL.
TITLE_VIA_BASE = "base"
TITLE_VIA_CURATOR = "curator"
TITLE_VIA_NONE = "none"


def is_ollama_cloud(host: str) -> bool:
    """True when `host` is Ollama Cloud's direct API (ollama.com)."""
    return "ollama.com" in (host or "").lower()


def model_aliases(model: str, host: str) -> tuple[str, ...]:
    """Names to probe for `model` on `host`, preferred first.

    Direct ollama.com /api/chat uses catalog ids from /api/tags (`gemma4:31b`).
    The `-cloud` suffix is the *local daemon* offload tag (`ollama run
    gemma4:31b-cloud`) and is NOT in the Cloud API catalog. Try the configured
    name first, then the sibling, so a leftover of either form self-heals
    without burning retries on the working id. Local hosts and colon-less
    cloud-native ids (`deepseek-v4-pro`) are used as-is.
    """
    name = (model or "").strip()
    if not name:
        return ()
    if not is_ollama_cloud(host):
        return (name,)
    if name.endswith("-cloud"):
        bare = name[: -len("-cloud")]
        return (name, bare) if bare else (name,)
    if ":" in name:
        return (name, f"{name}-cloud")
    return (name,)


def llm_reachable(host: str, model: str, api_key: str = "", client=None,
                  attempts: int = 3, sleep=time.sleep) -> bool:
    """Pre-flight health check: True when no host is configured (LLM unused → not a
    failure), else True iff a chat ping is *accepted* (HTTP 200) within `attempts`
    tries. A 200 with blank message.content still counts — Gemma 4's thinking path
    often leaves content empty (the 2026-08-24 repeating 'gemma4:31b unavailable'
    heads-up). 401/410/5xx/timeout retry with backoff; only a *sustained* failure
    returns False. Backoff mirrors telegram.send_message; `sleep` is injectable
    for tests. Pings send think=False so a healthy Gemma 4 answers quickly."""
    if not host:
        return True
    for attempt in range(attempts):
        if chat_accepted("ping", host=host, model=model, api_key=api_key,
                         client=client, think=False):
            return True
        if attempt < attempts - 1:
            sleep(2 ** attempt)
    return False


def resolve_curator(host: str, candidates, base_model: str, api_key: str = "",
                    client=None, attempts: int = 3, sleep=time.sleep) -> tuple:
    """Pick the first reachable curator model, walking `candidates` in order and falling
    back to `base_model` as the final rung. Returns (model_or_None, skipped), where
    `skipped` is the models tried-and-failed before the chosen one ([] if the first
    works). (None, all_tried) when nothing is reachable → caller degrades to stars.
    Each configured name is expanded via model_aliases (catalog id first, then
    the `-cloud` sibling). No pings when host is unset (LLM disabled) →
    (base_model or None, []). Lets a retired/401 primary (the recurring failure
    mode) self-heal to a working model instead of degrading the whole run."""
    if not host:
        return (base_model or None, [])
    order: list = []
    for m in (*candidates, base_model):
        # expand each configured name to its cloud aliases so a last-rung
        # `gemma4:31b` still resolves on ollama.com instead of stars-degrading.
        for alias in model_aliases(m, host):
            if alias and alias not in order:    # base appended once; dedupe if already a candidate
                order.append(alias)
    skipped: list = []
    for m in order:
        if llm_reachable(host, m, api_key, client=client, attempts=attempts, sleep=sleep):
            return (m, skipped)
        skipped.append(m)
    return (None, skipped)


def resolve_title_model(host: str, base_model: str, curator_model: str | None,
                        api_key: str = "", client=None, attempts: int = 3,
                        sleep=time.sleep, ping=None) -> tuple[str, str]:
    """Pick the model for titles + translation.

    Walks aliases of `base_model` (catalog id first, then the `-cloud` sibling).
    If those fail and a curator already resolved,
    reuse it — it is known reachable, so do not re-ping. Returns `(model, via)`
    where via is TITLE_VIA_BASE (configured name or its alias), TITLE_VIA_CURATOR
    (fall-through), or TITLE_VIA_NONE (nothing usable).
    """
    ping = ping or llm_reachable
    if not host:
        return ("", TITLE_VIA_NONE)
    for alias in model_aliases(base_model, host):
        # curator already proved this name reachable as a chain rung — skip a
        # second ping (and its retry budget) on the same id.
        if curator_model and alias == curator_model:
            return (alias, TITLE_VIA_BASE)
        if ping(host, alias, api_key, client=client, attempts=attempts, sleep=sleep):
            return (alias, TITLE_VIA_BASE)
    if curator_model:
        return (curator_model, TITLE_VIA_CURATOR)
    return ("", TITLE_VIA_NONE)


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
