import argparse
import logging
import sys

from bot.config import load_config
from bot.main import run


def cli(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="bot", description="Weekly interesting repos → Telegram")
    parser.add_argument("--dry-run", action="store_true", help="print messages instead of sending")
    parser.add_argument("--themes", default="themes.toml", help="path to themes.toml")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config = load_config(themes_path=args.themes)
    failures = run(config, dry_run=args.dry_run)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(cli())
