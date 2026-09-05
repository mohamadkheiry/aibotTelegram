from __future__ import annotations

import unittest
from unittest.mock import patch

from app.keyboards import MAIN_MENU_ROWS, contains_emoji, inline_main_menu_keyboard
from app.telegram import TelegramError, TelegramRequestCancelled
from tests import test_bot as _bot_tests


class ChannelSpecificationAuditTests(unittest.TestCase):
    CUSTOMER = _bot_tests.BotApplicationIntegrationTests.CUSTOMER
    OWNER = _bot_tests.BotApplicationIntegrationTests.OWNER
    setUp = _bot_tests.BotApplicationIntegrationTests.setUp
    tearDown = _bot_tests.BotApplicationIntegrationTests.tearDown
    message = _bot_tests.BotApplicationIntegrationTests.message
    callback = _bot_tests.BotApplicationIntegrationTests.callback
    send_message = _bot_tests.BotApplicationIntegrationTests.send_message
    send_callback = _bot_tests.BotApplicationIntegrationTests.send_callback
    _take_update_id = _bot_tests.BotApplicationIntegrationTests._take_update_id

    def test_main_channel_button_opens_configured_channel_directly(self):
        # PDF 1 page 1 requires Channel to be a direct link in the main menu.
        self.db.set_setting("main_channel_url", "https://t.me/example_channel")
        with patch.object(
            self.telegram, "send_message", wraps=self.telegram.send_message
        ) as send:
            self.send_message(self.CUSTOMER, text="/start")
        menus = [row for row in self.telegram.messages if "منوی اصلی" in row["text"]]
        self.assertEqual(len(menus), 1)
        rows = menus[0]["reply_markup"].get("inline_keyboard", [])
        self.assertTrue(
            rows, "the canonical main menu must support a direct URL button"
        )
        self.assertEqual(rows[-1][0]["url"], "https://t.me/example_channel")
        self.assertNotIn("callback_data", rows[-1][0])
        self.assertEqual(send.call_count, 1)
        self.assertEqual(
            send.call_args.kwargs["reply_markup"], {"remove_keyboard": True}
        )

    def test_inline_main_menu_preserves_layout_styles_icons_and_safe_actions(self):
        icons = {
            "shop": "101",
            "wallet": "102",
            "account": "103",
            "support": "104",
            "referral": "105",
            "channel": "106",
        }
        rows = inline_main_menu_keyboard(icons, "https://t.me/example_channel")[
            "inline_keyboard"
        ]
        self.assertEqual(
            [[button["text"] for button in row] for row in rows],
            [list(row) for row in MAIN_MENU_ROWS],
        )
        buttons = [button for row in rows for button in row]
        self.assertEqual(
            [button.get("style") for button in buttons],
            ["success", "success", "primary", None, "primary", None],
        )
        self.assertEqual(
            [button["icon_custom_emoji_id"] for button in buttons], list(icons.values())
        )
        self.assertEqual(
            [button["callback_data"] for button in buttons[:-1]],
            ["store", "wallet", "profile", "support", "referral"],
        )
        self.assertFalse(any(contains_emoji(button["text"]) for button in buttons))
        self.assertNotIn("callback_data", buttons[-1])
        for url in (
            "",
            "http://t.me/example_channel",
            "https://localhost/x",
            "https://t.me.evil.test/example_channel",
            "https://example.test/channel",
        ):
            with self.subTest(url=url):
                fallback = inline_main_menu_keyboard(main_channel_url=url)[
                    "inline_keyboard"
                ][-1][0]
                self.assertEqual(fallback["callback_data"], "channel")
                self.assertNotIn("url", fallback)

    def test_every_main_menu_callback_reaches_its_user_route(self):
        self.send_message(self.CUSTOMER, text="/start")
        rows = self.telegram.messages[-1]["reply_markup"]["inline_keyboard"]
        routes = {
            "store": "show_store",
            "wallet": "show_wallet",
            "profile": "show_account",
            "support": "show_support",
            "referral": "show_referral",
            "channel": "show_channel",
        }
        for button in [button for row in rows for button in row]:
            callback = button["callback_data"]
            with (
                self.subTest(callback=callback),
                patch.object(self.app, routes[callback]) as route,
            ):
                self.send_callback(self.CUSTOMER, callback)
                route.assert_called_once()
        self.send_callback(self.CUSTOMER, "channel")
        self.assertIn("لینک معتبر کانال", self.telegram.messages[-1]["text"])

    def test_main_menu_edit_failure_keeps_single_welcome_and_actionable_fallback(self):
        self.db.set_setting("main_channel_url", "https://t.me/example_channel")
        with patch.object(
            self.telegram,
            "edit_message_reply_markup",
            side_effect=TelegramError("edit unavailable"),
        ):
            self.send_message(self.CUSTOMER, text="/start")
        self.assertEqual(len(self.telegram.messages), 2)
        self.assertEqual(
            sum("منوی اصلی" in row["text"] for row in self.telegram.messages), 1
        )
        self.assertEqual(
            self.telegram.messages[0]["reply_markup"], {"remove_keyboard": True}
        )
        fallback = self.telegram.messages[-1]
        self.assertIn("یکی از گزینه", fallback["text"])
        self.assertEqual(
            fallback["reply_markup"]["inline_keyboard"][-1][0]["url"],
            "https://t.me/example_channel",
        )

    def test_cancelled_menu_edit_does_not_send_during_shutdown(self):
        with patch.object(
            self.telegram,
            "edit_message_reply_markup",
            side_effect=TelegramRequestCancelled("stopping"),
        ):
            with self.assertRaises(TelegramRequestCancelled):
                self.send_message(self.CUSTOMER, text="/start")
        self.assertEqual(len(self.telegram.messages), 1)
