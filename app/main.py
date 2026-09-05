from __future__ import annotations

import argparse
import logging
import signal
import sys

from .bot import BotApplication
from .config import load_settings
from .db import Database
from .telegram import TelegramClient


def configure_logging(level_name: str) -> None:
    """Configure application logs without exposing credential-bearing URLs."""

    logging.basicConfig(
        level=getattr(logging, level_name, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # urllib3 debug request lines include Telegram's /bot<TOKEN>/ path and
    # Plisio's api_key query parameter. Keep transport internals clamped even
    # when application diagnostics are intentionally DEBUG.
    for logger_name in ("urllib3", "requests", "http.client"):
        logging.getLogger(logger_name).setLevel(logging.WARNING)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Alone Account Telegram shop bot")
    parser.add_argument("--env-file", help="Path to an environment file")
    parser.add_argument(
        "--migrate-only", action="store_true", help="Initialize the SQLite schema and exit"
    )
    parser.add_argument(
        "--check", action="store_true", help="Validate configuration, database and bot token, then exit"
    )
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    settings = load_settings(args.env_file)
    configure_logging(settings.log_level)
    database = Database(settings.database_path)
    if args.migrate_only:
        database.initialize()
        logging.getLogger(__name__).info("Database schema is ready at %s", settings.database_path)
        return 0

    telegram = TelegramClient(
        settings.bot_token,
        api_base=settings.telegram_api_base,
        read_timeout=settings.request_timeout_seconds,
    )
    if args.check:
        database.initialize()
        database.bootstrap_admin(
            settings.bootstrap_admin_username,
            settings.bootstrap_admin_chat_id,
            role="owner",
            bootstrap_root=True,
        )
        result = telegram.call("getMe")
        username = result.get("username") if isinstance(result, dict) else "unknown"
        logging.getLogger(__name__).info("Configuration is valid for @%s", username)
        telegram.close()
        return 0

    application = BotApplication(settings, database, telegram)

    def stop_handler(_signum: int, _frame: object) -> None:
        application.stop_event.set()

    for signal_name in ("SIGINT", "SIGTERM"):
        if hasattr(signal, signal_name):
            signal.signal(getattr(signal, signal_name), stop_handler)
    try:
        application.run()
    except KeyboardInterrupt:
        application.stop_event.set()
    finally:
        application.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
