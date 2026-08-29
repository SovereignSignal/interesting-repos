import argparse
import logging
import sys
from dataclasses import replace
from datetime import datetime, timezone
from html import escape

from bot.config import load_config
from bot.main import run
from bot.alerts import send_alert


def _configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    # httpx/httpcore log full request URLs at INFO. Telegram's API embeds the bot token
    # in the URL path (/bot<TOKEN>/sendMessage), so leaving these at INFO leaks the token
    # into stdout and Railway's logs on every successful send. Raise them to WARNING.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def _parse_now(raw: str) -> datetime:
    """ISO-8601 → aware UTC datetime. Bare datetimes are treated as UTC."""
    s = raw.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def cli(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="bot", description="Interesting GitHub repos → Telegram, by theme")
    parser.add_argument("--dry-run", action="store_true", help="print messages instead of sending")
    parser.add_argument("--themes", default="themes.toml", help="path to themes.toml")
    parser.add_argument("--now", metavar="ISO",
                        help="simulate this UTC instant (e.g. 2026-08-28T13:00:00Z)")
    parser.add_argument("--theme", metavar="KEY",
                        help="run only this theme key (ignores at slots)")
    args = parser.parse_args(argv)

    _configure_logging()
    config = load_config(themes_path=args.themes, require_telegram=not args.dry_run)
    if args.theme:
        matched = [t for t in config.themes if t.key == args.theme]
        if not matched:
            raise SystemExit(f"unknown theme: {args.theme!r}")
        config = replace(config, themes=[replace(t, at=None) for t in matched])
    now = _parse_now(args.now) if args.now else None
    try:
        failures = run(config, now=now, dry_run=args.dry_run)
    except Exception as e:
        # last-resort alert: run() handles degraded/per-theme failures itself, so reaching
        # here means an unhandled crash. escape() so a message with HTML chars still sends.
        if not args.dry_run:
            send_alert(config.telegram_bot_token, config.alert_chat_id,
                       f"❌ interesting-repos run crashed: {type(e).__name__}: {escape(str(e))}")
        raise
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(cli())
