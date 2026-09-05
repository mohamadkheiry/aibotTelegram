from __future__ import annotations

import copy
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from app import main
from app.config import Settings, load_settings
from app.keyboards import contact_keyboard, inline_main_menu_keyboard, main_menu_keyboard
from app.telegram import TelegramClient


class ButtonReadabilityTests(unittest.TestCase):
    def client(self, **kwargs):
        session = Mock()
        session.headers = {}
        session.post.return_value.status_code = 200
        session.post.return_value.json.return_value = {
            "ok": True, "result": {"message_id": 1},
        }
        client = TelegramClient("readability-test-token", session=session, **kwargs)
        return client, session

    def assert_theme_markup(self, original, sent):
        expected = copy.deepcopy(original)
        for kind in ("keyboard", "inline_keyboard"):
            for row in expected.get(kind, []):
                for button in row:
                    button.pop("style", None)
        self.assertEqual(sent, expected)

    def test_theme_send_and_edit_strip_only_button_styles_without_mutating_inputs(self):
        for method in ("sendMessage", "editMessageText", "editMessageReplyMarkup"):
            for markup in (
                inline_main_menu_keyboard({"account": "123456"}, "https://t.me/example_channel"),
                main_menu_keyboard(),
                contact_keyboard(),
                {"remove_keyboard": True},
            ):
                with self.subTest(method=method, markup=markup):
                    client, session = self.client(button_color_mode="theme")
                    payload = {"chat_id": 1, "text": "منو", "reply_markup": markup}
                    saved = copy.deepcopy(payload)
                    client.call(method, payload)
                    self.assert_theme_markup(markup, session.post.call_args.kwargs["json"]["reply_markup"])
                    self.assertEqual(payload, saved)

    def test_stored_colored_outbox_markup_uses_theme_during_multipart_delivery(self):
        client, session = self.client(button_color_mode="theme")
        stored = inline_main_menu_keyboard(main_channel_url="https://t.me/example_channel")
        saved = copy.deepcopy(stored)
        for method in ("sendDocument", "sendPhoto"):
            with self.subTest(method=method):
                client.call(
                    method, {"chat_id": 1, "reply_markup": stored},
                    files={"document": ("synthetic.txt", io.BytesIO(b"training"))},
                )
                encoded = session.post.call_args.kwargs["data"]["reply_markup"]
                self.assert_theme_markup(stored, json.loads(encoded))
                self.assertEqual(stored, saved)

    def test_theme_mode_also_handles_json_encoded_markup_and_all_color_styles(self):
        client, session = self.client(button_color_mode="theme")
        markup = {"inline_keyboard": [[
            {"text": "تأیید", "style": "success", "callback_data": "confirm"},
            {"text": "بازگشت", "style": "primary", "callback_data": "back"},
            {"text": "لغو", "style": "danger", "callback_data": "cancel"},
        ]]}
        client.call("sendMessage", {"chat_id": 1, "reply_markup": json.dumps(markup)})
        sent = session.post.call_args.kwargs["json"]["reply_markup"]
        if isinstance(sent, str):
            sent = json.loads(sent)
        self.assert_theme_markup(markup, sent)

    def test_default_and_explicit_colored_mode_preserve_original_presentation(self):
        markup = inline_main_menu_keyboard()
        for options in ({}, {"button_color_mode": "colored"}):
            with self.subTest(options=options):
                client, session = self.client(**options)
                client.send_message(1, "منو", reply_markup=markup)
                self.assertEqual(session.post.call_args.kwargs["json"]["reply_markup"], markup)

    def test_color_policy_does_not_change_unrelated_payload_fields(self):
        client, session = self.client(button_color_mode="theme")
        payload = {"chat_id": 1, "text": "style primary", "metadata": {"style": "primary"}}
        client.call("sendMessage", payload)
        self.assertEqual(session.post.call_args.kwargs["json"], payload)

    def test_invalid_client_mode_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "button_color_mode"):
            self.client(button_color_mode="blue")

    def test_environment_preserves_colored_default_and_validates_explicit_modes(self):
        with tempfile.TemporaryDirectory() as root:
            base = {"BOT_TOKEN": "readability-test-token", "DATA_DIR": root}
            for raw, expected in ((None, "colored"), ("theme", "theme"), (" COLORED ", "colored")):
                values = dict(base)
                if raw is not None:
                    values["BUTTON_COLOR_MODE"] = raw
                with self.subTest(mode=raw), patch.dict(os.environ, values, clear=True):
                    settings = load_settings(Path(root) / "missing.env")
                    self.assertEqual(settings.button_color_mode, expected)
            with patch.dict(os.environ, {**base, "BUTTON_COLOR_MODE": "blue"}, clear=True):
                with self.assertRaisesRegex(RuntimeError, "BUTTON_COLOR_MODE"):
                    load_settings(Path(root) / "missing.env")

    def test_entrypoint_passes_configured_color_mode_to_client(self):
        with tempfile.TemporaryDirectory() as root:
            settings = Settings(
                bot_token="readability-test-token", data_dir=Path(root),
                database_path=Path(root) / "test.sqlite3", button_color_mode="colored",
            )
            with (
                patch.object(main, "_arguments", return_value=Mock(
                    env_file=None, migrate_only=False, check=True,
                )),
                patch.object(main, "load_settings", return_value=settings),
                patch.object(main, "configure_logging"),
                patch.object(main, "Database"),
                patch.object(main, "TelegramClient") as client_type,
            ):
                self.assertEqual(main.main(), 0)
                self.assertEqual(client_type.call_args.kwargs["button_color_mode"], "colored")


if __name__ == "__main__":
    unittest.main()
