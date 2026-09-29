"""Per-theme source health. Observability only — never selects or publishes.

One status line per theme that ran, plus whether GitHub Search was authenticated
and the last Search ``X-RateLimit-Remaining`` if a response carried it.

Counters live in ``<STATE_DIR>/source_health.json`` (same volume as
``state.json``). A separate file so the sent-repo schema stays untouched.
Missing or unreadable state starts fresh. A failed write logs that this run's
counters stayed in memory and does not fail the digest.

Sustained empty/error (24h since the streak began) sends one admin DM per
source per 24h through the existing ``ALERT_CHAT_ID`` → ``send_alert`` path.
When that chat id is unset, the same dedupe writes one
``source_health ALERT`` warning instead. No new environment variable.
"""
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from html import escape as html_escape

log = logging.getLogger("bot")

HEALTH_FILENAME = "source_health.json"
# Empty or erroring this long before the first admin alert, and the minimum
# gap before the same source is alerted again.
HEALTH_WINDOW = timedelta(hours=24)
# A delivered digest smaller than this is logged. Posting is unchanged.
THIN_POST_BELOW = 3

_STATUSES = ("ok", "empty", "error")
_BOT_TOKEN = re.compile(r"\d{6,}:[A-Za-z0-9_-]{10,}")
_BEARER = re.compile(r"Bearer\s+\S+", re.IGNORECASE)


def health_path(state_dir: str) -> str:
    return os.path.join(state_dir, HEALTH_FILENAME)


def short_reason(exc: BaseException, *secrets: str, limit: int = 160) -> str:
    """One-line failure reason with secrets removed. Safe to log."""
    text = " ".join(str(exc).split())
    for secret in secrets:
        if isinstance(secret, str) and len(secret) >= 8 and secret in text:
            text = text.replace(secret, "***")
    text = _BOT_TOKEN.sub("***", text)
    text = _BEARER.sub("Bearer ***", text)
    if not text:
        text = type(exc).__name__
    if len(text) > limit:
        text = text[: limit - 3] + "..."
    return text


def source_line(source: str, status: str, items: int, error: str = "") -> str:
    """The single per-source summary line."""
    base = f"source_health source={source} status={status} items={int(items)}"
    if status == "error":
        reason = " ".join((error or "unknown").split()) or "unknown"
        return f"{base} error={reason}"
    return base


def github_line(authenticated: bool, remaining: int | None) -> str:
    """Auth is yes/no only. Remaining is omitted when this run never saw a header."""
    auth = "yes" if authenticated else "no"
    line = f"source_health github_authenticated={auth}"
    if remaining is None:
        return line
    return f"{line} rate_limit_remaining={int(remaining)}"


def thin_post_line(source: str, items: int) -> str:
    return f"source_health thin_post source={source} items={int(items)}"


def alert_warn_line(source: str, status: str, items: int) -> str:
    return (
        f"source_health ALERT source={source} status={status} "
        f"items={int(items)} unhealthy_for>=24h"
    )


def alert_text(source: str, status: str, items: int, entry: dict, error: str = "") -> str:
    """Admin DM body. Dynamic bits are HTML-escaped (Telegram parse_mode=HTML)."""
    kind = "empty" if status == "empty" else "erroring"
    body = (
        f"⚠️ interesting-repos: source {html_escape(str(source))} has been {kind} "
        f"for 24h+ (items={int(items)}, "
        f"consecutive_empty={int(entry.get('consecutive_empty') or 0)}, "
        f"consecutive_error={int(entry.get('consecutive_error') or 0)})."
    )
    if error:
        body += f" Last error: {html_escape(error)}."
    return body


def _as_utc(now: datetime) -> datetime:
    if now.tzinfo is None or now.tzinfo.utcoffset(now) is None:
        return now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc)


def _parse_dt(value) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    return _as_utc(parsed)


def _iso(now: datetime) -> str:
    return _as_utc(now).isoformat()


def _count(value) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return value if value > 0 else 0


def _dt_field(raw: dict, key: str) -> str | None:
    parsed = _parse_dt(raw.get(key))
    return _iso(parsed) if parsed else None


def _normalize(raw) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    return {
        "last_ok": _dt_field(raw, "last_ok"),
        "consecutive_empty": _count(raw.get("consecutive_empty")),
        "consecutive_error": _count(raw.get("consecutive_error")),
        "unhealthy_since": _dt_field(raw, "unhealthy_since"),
        "last_alert": _dt_field(raw, "last_alert"),
    }


def load_health(path: str) -> dict:
    """Source → entry. Missing, corrupt, or non-object files yield ``{}``."""
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError):
        log.warning("source_health state unreadable; starting fresh")
        return {}
    if not isinstance(data, dict):
        log.warning("source_health state unreadable; starting fresh")
        return {}
    return {
        key: value
        for key, value in data.items()
        if isinstance(key, str) and isinstance(value, dict)
    }


def save_health(path: str, health: dict) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(health, handle)
    os.replace(tmp, path)


def apply_outcome(entry, status: str, now: datetime) -> dict:
    """Fold one observation into a new entry. ``ok`` clears the unhealthy streak."""
    if status not in _STATUSES:
        raise ValueError(f"unknown source status {status!r}")
    now = _as_utc(now)
    current = _normalize(entry)
    if status == "ok":
        current["last_ok"] = _iso(now)
        current["consecutive_empty"] = 0
        current["consecutive_error"] = 0
        current["unhealthy_since"] = None
        return current
    if status == "empty":
        current["consecutive_empty"] += 1
        current["consecutive_error"] = 0
    else:
        current["consecutive_error"] += 1
        current["consecutive_empty"] = 0
    if not current["unhealthy_since"]:
        current["unhealthy_since"] = _iso(now)
    return current


def alert_due(entry: dict, now: datetime, *, window: timedelta = HEALTH_WINDOW) -> bool:
    """True when the unhealthy streak is at least ``window`` and the last alert is older."""
    if not isinstance(entry, dict):
        return False
    since = _parse_dt(entry.get("unhealthy_since"))
    if since is None:
        return False
    now = _as_utc(now)
    if now - since < window:
        return False
    last = _parse_dt(entry.get("last_alert"))
    if last is not None and now - last < window:
        return False
    return True


def mark_alerted(entry: dict, now: datetime) -> None:
    entry["last_alert"] = _iso(now)


def report_source_health(
    outcomes: list[tuple[str, str, int, str]],
    *,
    state_dir: str,
    now: datetime,
    dry_run: bool,
    github_authenticated: bool,
    alert_chat_id: str,
    telegram_token: str,
    rate_remaining: int | None,
    send_alert,
) -> None:
    """Log one line per outcome, then the GitHub auth line.

    Live runs update the health file and raise at most one admin alert per
    source per ``HEALTH_WINDOW``. Dry runs log only.
    """
    now = _as_utc(now)
    for source, status, items, error in outcomes:
        log.info("%s", source_line(source, status, items, error))
    log.info("%s", github_line(github_authenticated, rate_remaining))
    if dry_run or not outcomes:
        return

    path = health_path(state_dir)
    health = load_health(path)
    pending: list[tuple[str, str, int, str, dict]] = []
    for source, status, items, error in outcomes:
        entry = apply_outcome(health.get(source), status, now)
        health[source] = entry
        if status in ("empty", "error") and alert_due(entry, now):
            pending.append((source, status, items, error, entry))

    for source, status, items, error, entry in pending:
        if alert_chat_id:
            sent = False
            try:
                sent = bool(send_alert(
                    telegram_token,
                    alert_chat_id,
                    alert_text(source, status, items, entry, error),
                ))
            except Exception:
                sent = False
            if sent:
                mark_alerted(entry, now)
        else:
            # ALERT_CHAT_ID is the only admin chat already wired. Unset → log.
            log.warning("%s", alert_warn_line(source, status, items))
            mark_alerted(entry, now)

    try:
        save_health(path, health)
    except Exception:
        log.warning("source_health state not persisted; tracking in-memory this run")
