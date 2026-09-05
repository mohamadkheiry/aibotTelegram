from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.config import Settings
from app.db import ConflictError, Database
from app.main import main


class _Telegram:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.closed = False

    def call(self, method: str) -> dict[str, object]:
        self.calls.append(method)
        return {"id": 1, "username": "preflight_test_bot"}

    def close(self) -> None:
        self.closed = True


class MainPreflightTests(unittest.TestCase):
    def settings(self, root: Path, *, chat_id: int = 111) -> Settings:
        return Settings(
            bot_token="test-token",
            database_path=root / "bot.sqlite3",
            data_dir=root / "data",
            bootstrap_admin_username="owner_one",
            bootstrap_admin_chat_id=chat_id,
        )

    def test_check_bootstraps_and_validates_the_owner_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = self.settings(root)
            telegram = _Telegram()
            with (
                patch.object(sys, "argv", ["alone-account-bot", "--check"]),
                patch("app.main.load_settings", return_value=settings),
                patch("app.main.TelegramClient", return_value=telegram),
            ):
                self.assertEqual(main(), 0)

            owner = Database(settings.database_path).list_admins()[0]
            self.assertEqual(owner["username_key"], "owner_one")
            self.assertEqual(owner["chat_id"], 111)
            self.assertEqual(telegram.calls, ["getMe"])
            self.assertTrue(telegram.closed)

    def test_check_rejects_a_conflicting_persisted_owner_binding(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = Database(root / "bot.sqlite3")
            database.initialize()
            database.bootstrap_admin("owner_one", 111, role="owner")
            settings = self.settings(root, chat_id=222)
            telegram = _Telegram()

            with (
                patch.object(sys, "argv", ["alone-account-bot", "--check"]),
                patch("app.main.load_settings", return_value=settings),
                patch("app.main.TelegramClient", return_value=telegram),
                self.assertRaises(ConflictError),
            ):
                main()
            self.assertEqual(telegram.calls, [])


if __name__ == "__main__":
    unittest.main()
