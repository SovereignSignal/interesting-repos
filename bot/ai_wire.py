"""Push repos that were just posted to Telegram into the AI Wire registry.

Contract (ingest v1): ``POST {AI_WIRE_URL}/api/ingest/items`` with
``Authorization: Bearer {AI_WIRE_INGEST_TOKEN}``. One batch per cron run, after
the Telegram send has succeeded. Timeout 5s, one retry. A failure is a log
line plus a pending outbox row — it never fails the digest or the backfill.

Every posted item is also queued in ``_ai_wire_outbox`` in the same
``state.json`` save as the theme. ``flush_outbox`` runs on every ordinary run,
so a registry that was down during a publish is filled in later without a
second Telegram post.

``AI_WIRE_ENABLED`` defaults off. ``state.json`` id lists are not enough to
rebuild an item (no owner/repo); ``_ai_wire`` rows written at post time are.
``python -m bot --backfill-ai-wire`` prints that history and does not POST
unless ``--send`` is also set.
"""
import json
import logging
import os
import re
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx

from bot.formatter import posted_blurb
from bot.source_health import short_reason
from bot.state import OUTBOX_KEY, WIRE_KEY, load_state, outbox, record_dead
from bot.titles import repo_name_title

log = logging.getLogger("bot.ai_wire")

SOURCE_BOT = "interesting-repos"
CHANNEL = "interestingrepos"
KIND = "repo"
CHANNEL_POST = "https://t.me/interestingrepos/{}"
SUMMARY_LIMIT = 600
# The ingest API rejects a body over this. A cron slot is one theme, so the
# live push is one request; only a long backfill hits the ceiling.
BATCH_LIMIT = 100
TIMEOUT_SECONDS = 5.0
_ATTEMPTS = 2  # the call, plus one retry
_NAME = re.compile(r"^[a-z0-9_.-]+/[a-z0-9_.-]+$")


def canonical_key(full_name: str) -> str | None:
    name = (full_name or "").strip().lower()
    if not _NAME.match(name):
        return None
    return f"repo:{name}"


def ingest_endpoint(base_url: str) -> str:
    base = (base_url or "").strip().rstrip("/")
    if not base:
        return ""
    suffix = "/api/ingest/items"
    if base.endswith(suffix):
        return base
    return base + suffix


def message_id_of(response) -> int | None:
    """Telegram ``sendMessage`` id, from ``result.message_id`` or the top level."""
    if not isinstance(response, dict):
        return None
    containers = []
    result = response.get("result")
    if isinstance(result, dict):
        containers.append(result)
    containers.append(response)
    for container in containers:
        raw = container.get("message_id")
        if isinstance(raw, bool) or not isinstance(raw, int):
            continue
        if raw > 0:
            return raw
    return None


def format_posted_at(now) -> str | None:
    if isinstance(now, datetime):
        if now.tzinfo is None or now.tzinfo.utcoffset(now) is None:
            now = now.replace(tzinfo=timezone.utc)
        return now.astimezone(timezone.utc).isoformat()
    if isinstance(now, str) and now.strip():
        return now.strip()
    return None


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        return None
    if number.is_integer():
        return int(number)
    return number


def _tags(repo) -> list[str]:
    tags: list[str] = []
    seen: set[str] = set()
    for topic in getattr(repo, "topics", None) or []:
        text = str(topic).strip()
        if not text or text.lower() in seen:
            continue
        seen.add(text.lower())
        tags.append(text)
    language = (getattr(repo, "language", "") or "").strip()
    if language and language.lower() not in seen:
        tags.append(language)
    return tags


def _known_gain(delta, repo_id, gains) -> int | None:
    if isinstance(delta, int) and not isinstance(delta, bool):
        return delta
    if not isinstance(gains, dict) or repo_id is None:
        return None
    extra = gains.get(repo_id)
    if isinstance(extra, tuple) and extra:
        gain = extra[0]
        if isinstance(gain, int) and not isinstance(gain, bool):
            return gain
    return None


def ingest_item(repo, *, title: str = "", summary: str = "", lane: str = "",
                score=None, stars_gained=None, posted_at=None,
                message_id: int | None = None) -> dict | None:
    """One registry item for a repo that was posted. None when owner/repo is unusable."""
    full_name = (getattr(repo, "full_name", "") or "").strip()
    key = canonical_key(full_name)
    if key is None:
        return None
    shown = (title or "").strip() or repo_name_title(full_name)
    if not shown:
        return None
    url = (getattr(repo, "html_url", "") or "").strip()
    if not url:
        url = f"https://github.com/{full_name}"
    item = {
        "source_bot": SOURCE_BOT,
        "kind": KIND,
        "canonical_key": key,
        "title": shown,
        "url": url,
        "channel": CHANNEL,
    }
    blurb = (summary or "").strip()
    if blurb:
        item["summary"] = blurb[:SUMMARY_LIMIT]
    when = format_posted_at(posted_at)
    if when:
        item["posted_at"] = when
    if (lane or "").strip():
        item["lane"] = lane.strip()
    if isinstance(message_id, int) and not isinstance(message_id, bool) and message_id > 0:
        item["channel_post_url"] = CHANNEL_POST.format(message_id)
    tags = _tags(repo)
    if tags:
        item["tags"] = tags
    scored = _number(score)
    if scored is not None:
        item["score"] = scored
    extra = {}
    stars = getattr(repo, "stars", None)
    if isinstance(stars, int) and not isinstance(stars, bool):
        extra["stars"] = stars
    gained = stars_gained if isinstance(stars_gained, int) and not isinstance(stars_gained, bool) else None
    if gained is not None:
        extra["stars_gained"] = gained
    if extra:
        item["extra"] = extra
    return item


def items_for_indexes(*, theme_name: str, picks, titles, summaries, indexes,
                      describe, translate, deltas, gains, posted_at, response) -> list[dict]:
    """Ingest items for the repos that landed in one successful Telegram message."""
    describe = describe or (lambda repo: "")
    translate = translate or (lambda text: text)
    n = len(picks)
    if summaries is None:
        summaries = [None] * n
    when = format_posted_at(posted_at)
    message_id = message_id_of(response)
    out = []
    for i in indexes:
        if isinstance(i, bool) or not isinstance(i, int) or i < 0 or i >= n:
            continue
        pick = picks[i]
        repo = pick.repo
        try:
            blurb = posted_blurb(
                repo,
                summaries[i] if i < len(summaries) else None,
                describe,
                translate,
            )
            delta = deltas[i] if deltas is not None and i < len(deltas) else None
            item = ingest_item(
                repo,
                title=titles[i] if titles is not None and i < len(titles) else "",
                summary=blurb,
                lane=theme_name or "",
                score=getattr(pick, "score", None),
                stars_gained=_known_gain(delta, getattr(repo, "id", None), gains),
                posted_at=when,
                message_id=message_id,
            )
        except Exception as exc:
            log.warning("ai_wire push failed: %s", short_reason(exc))
            continue
        if item:
            out.append(item)
    return out


def _close(resp) -> None:
    close = getattr(resp, "close", None)
    if not callable(close):
        return
    try:
        close()
    except Exception:
        return


def _nonneg_int(value) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


def _written(resp, fallback: int) -> int:
    """Rows the ingest call created plus rows it updated.

    The registry reports new rows as ``created`` and existing rows as
    ``upserted``. Logging only ``upserted`` printed ``n=0`` when every item
    in the batch was new. Missing both fields falls back to the batch size.
    """
    try:
        body = resp.json()
    except Exception:
        return fallback
    if not isinstance(body, dict):
        return fallback
    created = _nonneg_int(body.get("created"))
    updated = _nonneg_int(body.get("upserted"))
    if created is None and updated is None:
        return fallback
    return (created or 0) + (updated or 0)


# Statuses that will not succeed on a retry of the same payload. Auth and
# routing errors (401/403/404/3xx) stay pending: they are config, not data.
_PERMANENT = {400, 409, 410, 413, 415, 422}
OK, TRANSIENT, PERMANENT = "ok", "transient", "permanent"


def _deliver_batch(client, endpoint: str, token: str, batch: list,
                   timeout: float) -> tuple[str, str]:
    """POST one batch: ``(OK|TRANSIENT|PERMANENT, reason)``. One retry if transient."""
    reason = "unknown"
    for _attempt in range(_ATTEMPTS):
        try:
            resp = client.post(
                endpoint,
                json={"items": batch},
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json",
                },
                timeout=timeout,
                follow_redirects=False,
            )
        except Exception as exc:
            reason = short_reason(exc, token)
            continue
        try:
            status = getattr(resp, "status_code", None)
            if status == 200:
                log.info("ai_wire push ok n=%s", _written(resp, len(batch)))
                return OK, ""
            reason = f"HTTP {status}" if status else "HTTP error"
            if status in _PERMANENT:
                log.warning("ai_wire push failed: %s", reason)
                return PERMANENT, reason
        finally:
            _close(resp)
    log.warning("ai_wire push failed: %s", reason)
    return TRANSIENT, reason


def _post_batch(client, endpoint: str, token: str, batch: list, timeout: float) -> bool:
    return _deliver_batch(client, endpoint, token, batch, timeout)[0] == OK


def push_items(items, *, base_url: str, token: str, client=None,
               timeout: float = TIMEOUT_SECONDS) -> bool:
    """POST ``items``. Never raises. True when every batch returned HTTP 200."""
    try:
        if not items:
            return True
        endpoint = ingest_endpoint(base_url)
        token = (token or "").strip()
        if not endpoint or not token:
            log.warning("ai_wire push failed: AI_WIRE_URL or AI_WIRE_INGEST_TOKEN unset")
            return False
        owns = client is None
        client = client or httpx.Client(timeout=timeout)
        ok = True
        try:
            for start in range(0, len(items), BATCH_LIMIT):
                batch = items[start:start + BATCH_LIMIT]
                if not _post_batch(client, endpoint, token, batch, timeout):
                    ok = False
        finally:
            if owns:
                try:
                    client.close()
                except Exception:
                    pass
        return ok
    except Exception as exc:
        log.warning("ai_wire push failed: %s", short_reason(exc, token or ""))
        return False


def push_posted(config, items, client=None) -> bool:
    """Fire-and-forget after a successful Telegram send. Flag-off is a no-op."""
    try:
        if not items or not getattr(config, "ai_wire_enabled", False):
            return True
        return push_items(
            items,
            base_url=getattr(config, "ai_wire_url", "") or "",
            token=getattr(config, "ai_wire_ingest_token", "") or "",
            client=client,
        )
    except Exception as exc:
        token = getattr(config, "ai_wire_ingest_token", "") or ""
        log.warning("ai_wire push failed: %s", short_reason(exc, token))
        return False


def flush_outbox(config, state: dict, now, client=None,
                 timeout: float = TIMEOUT_SECONDS) -> tuple[dict, dict]:
    """Deliver pending ``_ai_wire_outbox`` rows. Never raises.

    Acks leave the outbox. A permanent rejection moves the row to
    ``_ai_wire_dead`` (a multi-item batch is retried one item at a time first,
    so one bad row does not sink the rest). A transient failure bumps
    ``attempts`` and stops this flush — the next ordinary run tries again.
    Returns ``(state, report)`` with ``acked``, ``dead``, ``pending``,
    ``oldest`` (``queued_at`` of the oldest pending row) and ``error``.
    """
    report = {"acked": 0, "dead": 0, "pending": 0, "oldest": None, "error": ""}
    token = (getattr(config, "ai_wire_ingest_token", "") or "").strip()
    rows = outbox(state)
    try:
        endpoint = ingest_endpoint(getattr(config, "ai_wire_url", "") or "")
        manifest = {}
        raw = state.get(WIRE_KEY)
        for entry in raw if isinstance(raw, list) else []:
            if isinstance(entry, dict) and isinstance(entry.get("canonical_key"), str):
                manifest[entry["canonical_key"]] = entry
        queue = []
        for row in rows:
            item = item_from_history(manifest.get(row["key"]))
            if item is None:
                # The manifest row aged out or is unusable; nothing to resend.
                log.warning("ai_wire outbox dropped %s: no posted row", row["key"])
                continue
            queue.append((row, item))
        keep = {id(row) for row, _ in queue}
        rows = [row for row in rows if id(row) in keep]
        if queue and (not endpoint or not token):
            report["error"] = "AI_WIRE_URL or AI_WIRE_INGEST_TOKEN unset"
            log.warning("ai_wire push failed: %s", report["error"])
            queue = []
        owns = client is None and bool(queue)
        if owns:
            client = httpx.Client(timeout=timeout)
        done: set = set()
        dead: list = []
        stamp = format_posted_at(now) or ""
        try:
            for start in range(0, len(queue), BATCH_LIMIT):
                chunk = queue[start:start + BATCH_LIMIT]
                outcome, reason = _deliver_batch(
                    client, endpoint, token, [item for _, item in chunk], timeout)
                if outcome == PERMANENT and len(chunk) > 1:
                    singles = []
                    for row, item in chunk:
                        one, why = _deliver_batch(client, endpoint, token, [item], timeout)
                        singles.append((row, one, why))
                        if one == TRANSIENT:
                            break
                    results = singles
                else:
                    results = [(row, outcome, reason) for row, _ in chunk]
                stop = False
                for row, one, why in results:
                    row["attempts"] = int(row.get("attempts") or 0) + 1
                    row["last_attempt_at"] = stamp
                    if one == OK:
                        done.add(id(row))
                        report["acked"] += 1
                    elif one == PERMANENT:
                        done.add(id(row))
                        dead.append({"key": row["key"], "queued_at": row.get("queued_at"),
                                     "failed_at": stamp, "error": why})
                        report["dead"] += 1
                        report["error"] = why
                    else:
                        row["last_error"] = why
                        report["error"] = why
                        stop = True
                if stop:
                    break
        finally:
            if owns:
                try:
                    client.close()
                except Exception:
                    pass
        rows = [row for row in rows if id(row) not in done]
        if dead:
            for entry in dead:
                log.warning("ai_wire outbox rejected %s: %s", entry["key"], entry["error"])
            state = record_dead(state, dead)
    except Exception as exc:
        report["error"] = short_reason(exc, token)
        log.warning("ai_wire push failed: %s", report["error"])
    state[OUTBOX_KEY] = rows
    report["pending"] = len(rows)
    if rows:
        report["oldest"] = min((str(r.get("queued_at") or "") for r in rows), default=None)
        log.warning("ai_wire outbox pending=%d oldest_queued_at=%s last_error=%s",
                    report["pending"], report["oldest"], report["error"] or "none")
    elif report["acked"]:
        log.info("ai_wire outbox drained acked=%d", report["acked"])
    return state, report


def _replay(entry: dict) -> dict | None:
    key = entry.get("canonical_key")
    title = entry.get("title")
    url = entry.get("url")
    source = entry.get("source_bot", SOURCE_BOT)
    if source != SOURCE_BOT:
        return None
    if not isinstance(key, str) or canonical_key(key[5:] if key.startswith("repo:") else "") != key:
        return None
    if not isinstance(title, str) or not title.strip():
        return None
    if not isinstance(url, str) or not url.strip():
        return None
    item = {
        "source_bot": SOURCE_BOT,
        "kind": KIND,
        "canonical_key": key,
        "title": title.strip(),
        "url": url.strip(),
        "channel": CHANNEL,
    }
    summary = entry.get("summary")
    if isinstance(summary, str) and summary.strip():
        item["summary"] = summary.strip()[:SUMMARY_LIMIT]
    posted_at = entry.get("posted_at")
    if isinstance(posted_at, str) and posted_at.strip():
        item["posted_at"] = posted_at.strip()
    lane = entry.get("lane")
    if isinstance(lane, str) and lane.strip():
        item["lane"] = lane.strip()
    post_url = entry.get("channel_post_url")
    if isinstance(post_url, str) and post_url.startswith("https://t.me/interestingrepos/"):
        tail = post_url.rsplit("/", 1)[-1]
        if tail.isdigit() and int(tail) > 0:
            item["channel_post_url"] = CHANNEL_POST.format(int(tail))
    tags = entry.get("tags")
    if isinstance(tags, list):
        clean = []
        seen = set()
        for tag in tags:
            if not isinstance(tag, str) or not tag.strip():
                continue
            if tag.strip().lower() in seen:
                continue
            seen.add(tag.strip().lower())
            clean.append(tag.strip())
        if clean:
            item["tags"] = clean
    scored = _number(entry.get("score"))
    if scored is not None:
        item["score"] = scored
    extra_in = entry.get("extra")
    extra = {}
    if isinstance(extra_in, dict):
        stars = extra_in.get("stars")
        if isinstance(stars, int) and not isinstance(stars, bool):
            extra["stars"] = stars
        gained = extra_in.get("stars_gained")
        if isinstance(gained, int) and not isinstance(gained, bool):
            extra["stars_gained"] = gained
    if extra:
        item["extra"] = extra
    return item


def item_from_history(entry) -> dict | None:
    """An ingest item when the row has owner/repo (and whatever else was stored)."""
    if not isinstance(entry, dict):
        return None
    # A row we stored at post time. Don't rebuild it from a partial full_name.
    if isinstance(entry.get("canonical_key"), str) and entry.get("canonical_key"):
        return _replay(entry)
    full_name = entry.get("full_name")
    if not isinstance(full_name, str) or "/" not in full_name.strip():
        return None
    extra = entry.get("extra") if isinstance(entry.get("extra"), dict) else {}
    stars = entry.get("stars", extra.get("stars"))
    if isinstance(stars, bool) or not isinstance(stars, int):
        stars = None
    gained = entry.get("stars_gained", extra.get("stars_gained"))
    topics = entry.get("topics") if isinstance(entry.get("topics"), list) else []
    language = entry.get("language") if isinstance(entry.get("language"), str) else ""
    if not topics and isinstance(entry.get("tags"), list):
        topics = [tag for tag in entry["tags"] if isinstance(tag, str) and tag.strip()
                  and tag.strip().lower() != language.strip().lower()]
    repo = SimpleNamespace(
        full_name=full_name.strip(),
        html_url=entry.get("url") if isinstance(entry.get("url"), str) else "",
        stars=stars,
        language=language,
        topics=topics,
    )
    return ingest_item(
        repo,
        title=entry.get("title") if isinstance(entry.get("title"), str) else "",
        summary=entry.get("summary") if isinstance(entry.get("summary"), str) else "",
        lane=entry.get("lane") if isinstance(entry.get("lane"), str) else "",
        score=entry.get("score"),
        stars_gained=gained if isinstance(gained, int) and not isinstance(gained, bool) else None,
        posted_at=entry.get("posted_at") if isinstance(entry.get("posted_at"), str) else None,
        message_id=message_id_of({"message_id": _message_tail(entry.get("channel_post_url"))}),
    )


def _message_tail(url) -> int | None:
    if not isinstance(url, str) or not url.startswith("https://t.me/interestingrepos/"):
        return None
    tail = url.rsplit("/", 1)[-1]
    if tail.isdigit() and int(tail) > 0:
        return int(tail)
    return None


def history_items(state: dict) -> list[dict]:
    """Posted rows that can be replayed. Latest row wins for a repeated key."""
    if not isinstance(state, dict):
        return []
    raw = state.get(WIRE_KEY)
    if not isinstance(raw, list):
        return []
    cleaned = []
    for entry in raw:
        item = item_from_history(entry)
        if item:
            cleaned.append(item)
    seen = set()
    ordered = []
    for item in reversed(cleaned):
        key = item["canonical_key"]
        if key in seen:
            continue
        seen.add(key)
        ordered.append(item)
    ordered.reverse()
    return ordered


def sent_id_count(state: dict) -> int:
    if not isinstance(state, dict):
        return 0
    total = 0
    for key, value in state.items():
        if key == WIRE_KEY or not isinstance(value, list):
            continue
        total += sum(1 for item in value if isinstance(item, int) and not isinstance(item, bool))
    return total


def backfill(config, *, send: bool = False, client=None) -> int:
    """Replay ``_ai_wire`` from ``state.json``. Dry-run unless ``send`` is true.

    Id-only sent history (the pre-wire volume) is not posted: it has no
    owner/repo. Returns 0 on a dry-run or a successful send, 1 when a send
    was requested and did not succeed. Does not raise.
    """
    token = getattr(config, "ai_wire_ingest_token", "") or ""
    try:
        path = os.path.join(config.state_dir, "state.json")
        state = load_state(path)
    except Exception as exc:
        log.warning("ai_wire push failed: %s", short_reason(exc, token))
        return 1
    items = history_items(state)
    if not items:
        message = (
            "ai_wire backfill: state.json sent history does not hold "
            f"owner/repo, title, and url (n={sent_id_count(state)} repo ids)"
        )
        log.info("%s", message)
        print(message)
        return 0
    if not send:
        print(json.dumps({"items": items}, indent=2, ensure_ascii=False))
        log.info("ai_wire backfill dry-run n=%d", len(items))
        return 0
    if not getattr(config, "ai_wire_enabled", False):
        log.warning("ai_wire push failed: AI_WIRE_ENABLED is off")
        return 1
    ok = push_items(
        items,
        base_url=getattr(config, "ai_wire_url", "") or "",
        token=token,
        client=client,
    )
    return 0 if ok else 1
