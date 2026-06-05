import argparse
import logging
import sys

from bot.config import load_config
from bot.main import run


def _configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    # httpx/httpcore log full request URLs at INFO. Telegram's API embeds the bot token
    # in the URL path (/bot<TOKEN>/sendMessage), so leaving these at INFO leaks the token
    # into stdout and Railway's logs on every successful send. Raise them to WARNING.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def cli(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="bot", description="Weekly interesting repos → Telegram")
    parser.add_argument("--dry-run", action="store_true", help="print messages instead of sending")
    parser.add_argument("--themes", default="themes.toml", help="path to themes.toml")
    args = parser.parse_args(argv)

    _configure_logging()
    config = load_config(themes_path=args.themes)
    failures = run(config, dry_run=args.dry_run)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(cli())
