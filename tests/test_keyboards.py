from __future__ import annotations

import unittest

from app.keyboards import (
    contains_emoji,
    contact_keyboard,
    copy_text_button,
    inline_button,
    inline_keyboard,
    main_menu_keyboard,
    reply_button,
    url_button,
)


class MainMenuKeyboardTests(unittest.TestCase):
    def test_main_menu_has_exact_rows_and_order(self) -> None:
        markup = main_menu_keyboard()

        self.assertEqual(
            [[button["text"] for button in row] for row in markup["keyboard"]],
            [
                ["فروشگاه"],
                ["کیف پول", "حساب من"],
                ["پشتیبانی"],
                ["دعوت و کسب درآمد"],
                ["کانال"],
            ],
        )
        self.assertTrue(markup["resize_keyboard"])
        self.assertTrue(markup["is_persistent"])

    def test_main_menu_styles_match_design(self) -> None:
        rows = main_menu_keyboard()["keyboard"]

        self.assertEqual(rows[0][0]["style"], "success")
        self.assertEqual(rows[1][0]["style"], "success")
        self.assertEqual(rows[1][1]["style"], "primary")
        self.assertNotIn("style", rows[2][0])
        self.assertEqual(rows[3][0]["style"], "primary")
        self.assertNotIn("style", rows[4][0])

    def test_main_menu_labels_contain_no_unicode_emoji(self) -> None:
        buttons = (
            button
            for row in main_menu_keyboard()["keyboard"]
            for button in row
        )
        self.assertTrue(all(not contains_emoji(button["text"]) for button in buttons))

    def test_main_menu_accepts_semantic_icon_ids(self) -> None:
        rows = main_menu_keyboard(
            {
                "shop": "10001",
                "wallet": "10002",
                "account": "10003",
                "support": "10004",
                "referral": "10005",
                "channel": "10006",
            }
        )["keyboard"]

        self.assertEqual(
            [button["icon_custom_emoji_id"] for row in rows for button in row],
            ["10001", "10002", "10003", "10004", "10005", "10006"],
        )


class KeyboardHelperTests(unittest.TestCase):
    def test_contact_keyboard_uses_request_contact_without_label_emoji(self) -> None:
        markup = contact_keyboard(icon_custom_emoji_id="20001")
        button = markup["keyboard"][0][0]

        self.assertEqual(button["text"], "ارسال شماره موبایل")
        self.assertIs(button["request_contact"], True)
        self.assertEqual(button["style"], "primary")
        self.assertEqual(button["icon_custom_emoji_id"], "20001")
        self.assertTrue(markup["one_time_keyboard"])

    def test_copy_text_button_uses_current_bot_api_shape(self) -> None:
        button = copy_text_button(
            "کپی مبلغ",
            "1250042",
            style="primary",
            icon_custom_emoji_id="30001",
        )

        self.assertEqual(
            button,
            {
                "text": "کپی مبلغ",
                "copy_text": {"text": "1250042"},
                "style": "primary",
                "icon_custom_emoji_id": "30001",
            },
        )

    def test_inline_keyboard_preserves_rows(self) -> None:
        first = inline_button("خرید", callback_data="buy:7", style="success")
        second = inline_button("بازگشت", callback_data="back")

        self.assertEqual(
            inline_keyboard([[first], [second]]),
            {"inline_keyboard": [[first], [second]]},
        )

    def test_default_style_is_omitted(self) -> None:
        self.assertNotIn("style", reply_button("پشتیبانی", style="default"))

    def test_url_button_requires_a_safe_public_https_url(self) -> None:
        self.assertEqual(
            url_button("Open", "https://example.test/path")["url"],
            "https://example.test/path",
        )
        for unsafe in (
            "javascript:alert(1)",
            "http://example.test/path",
            "https://localhost/path",
            "https://user@example.test/path",
        ):
            with self.subTest(unsafe=unsafe), self.assertRaises(ValueError):
                url_button("Open", unsafe)

    def test_all_builders_reject_emoji_in_button_text(self) -> None:
        with self.assertRaisesRegex(ValueError, "must not contain Unicode emoji"):
            reply_button("🏪 فروشگاه")
        with self.assertRaisesRegex(ValueError, "must not contain Unicode emoji"):
            inline_button("⬅️ بازگشت", callback_data="back")

    def test_less_common_unicode_emoji_symbols_are_rejected(self) -> None:
        symbols = ("▪", "▶", "◻", "〰", "〽", "㊗", "㊙", "Ⓜ", "↔", "⤴")

        for symbol in symbols:
            with self.subTest(symbol=symbol):
                self.assertTrue(contains_emoji(symbol))
                with self.assertRaisesRegex(ValueError, "must not contain Unicode emoji"):
                    inline_button(f"{symbol} عنوان", callback_data="item:1")

    def test_plain_numbers_and_ascii_markers_are_not_mistaken_for_emoji(self) -> None:
        self.assertFalse(contains_emoji("پلن 12 ماهه #2 * ویژه"))
        self.assertTrue(contains_emoji("1️⃣"))

    def test_inline_button_requires_exactly_one_action(self) -> None:
        with self.assertRaisesRegex(ValueError, "exactly one action"):
            inline_button("خرید")
        with self.assertRaisesRegex(ValueError, "exactly one action"):
            inline_button("خرید", callback_data="buy", url="https://example.com")

    def test_callback_data_limit_is_measured_in_utf8_bytes(self) -> None:
        with self.assertRaisesRegex(ValueError, "1-64 UTF-8 bytes"):
            inline_button("خرید", callback_data="ش" * 33)

    def test_reply_button_rejects_multiple_request_actions(self) -> None:
        with self.assertRaisesRegex(ValueError, "at most one request action"):
            reply_button(
                "ارسال اطلاعات",
                request_contact=True,
                request_location=True,
            )


if __name__ == "__main__":
    unittest.main()
