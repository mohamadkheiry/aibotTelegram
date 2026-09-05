from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.config import load_settings
from app.utils import (
    extract_start_ref,
    format_admin_text,
    is_safe_https_url,
    is_safe_telegram_channel_url,
    is_safe_telegram_invite_url,
    money,
    normalize_digits,
    normalize_username,
    parse_amount,
)


class ConfigAndUtilsTests(unittest.TestCase):
    def test_env_file_and_redacted_repr(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env_path = Path(directory) / ".env"
            env_path.write_text(
                "BOT_TOKEN=very-secret\n"
                "BOOTSTRAP_ADMIN_USERNAME=@MohammadRezaKheiry\n"
                f"DATA_DIR={directory}/data\n",
                encoding="utf-8",
            )
            with patch.dict(os.environ, {}, clear=True):
                settings = load_settings(env_path)
            self.assertEqual(settings.bootstrap_admin_username, "mohammadrezakheiry")
            self.assertNotIn("very-secret", repr(settings))

    def test_amount_and_identifier_normalization(self) -> None:
        self.assertEqual(normalize_digits("۱۲۳٤"), "1234")
        self.assertEqual(parse_amount("۱٬۲۵۰٬۰۰۰ تومان"), 1_250_000)
        self.assertEqual(normalize_username(" @SomeUser "), "someuser")
        self.assertEqual(extract_start_ref("/start ref_۱۲۳"), 123)
        self.assertEqual(money(125000), "125,000 تومان")

    def test_explicit_html_accepts_only_public_https_link_targets(self) -> None:
        for target in (
            "https://",
            "http://example.test/path",
            "tg://resolve?domain=valid_channel",
            "mailto:person@example.test",
            "javascript:alert(1)",
            "data:text/html,unsafe",
            "https://127.0.0.1/private",
            "https://127.1/private",
            "https://127.0.1/private",
            "https://0177.0.0.1/private",
            "https://0x7f.0.0.1/private",
            "https://localhost/private",
            "https://10.0.0.1/private",
            "https://user:password@example.test/path",
            "https://example.test/path\nbreak",
        ):
            with self.subTest(target=target), self.assertRaises(ValueError):
                format_admin_text(f'html:<a href="{target}">لینک</a>')

        for target in (
            "https://example.com/path?item=1#details",
            "https://t.me/valid_channel",
        ):
            with self.subTest(target=target):
                expected = f'<a href="{target}">لینک</a>'
                self.assertEqual(format_admin_text(f"html:{expected}"), expected)

    def test_channel_url_validator_accepts_only_canonical_https_t_me_links(self) -> None:
        for target in (
            "https://t.me/valid_channel",
            "https://t.me/+AbCdEf_12345",
            "https://t.me/joinchat/AbCdEf_12345",
        ):
            with self.subTest(target=target):
                self.assertTrue(is_safe_telegram_channel_url(target))

        for target in (
            "http://t.me/valid_channel",
            "tg://resolve?domain=valid_channel",
            "https://evil.example/valid_channel",
            "https://t.me.evil.example/valid_channel",
            "https://t.me@evil.example/valid_channel",
            "https://t.me/",
            "https://t.me/valid_channel?next=evil",
            "https://t.me/valid channel",
        ):
            with self.subTest(target=target):
                self.assertFalse(is_safe_telegram_channel_url(target))

    def test_invite_and_general_https_validators_reject_smuggled_or_local_urls(self) -> None:
        for target in (
            "https://t.me/valid_channel",
            "https://t.me/+AbCdEf_12345",
            "https://telegram.me/joinchat/AbCdEf_12345",
        ):
            with self.subTest(invite=target):
                self.assertTrue(is_safe_telegram_invite_url(target))
        for target in (
            "https://telegram.me.evil.example/channel",
            "https://t.me@evil.example/channel",
            "https://t.me/channel?redirect=evil",
            "http://t.me/channel",
            "https://t.me/../evil",
        ):
            with self.subTest(unsafe_invite=target):
                self.assertFalse(is_safe_telegram_invite_url(target))

        for target in (
            "https://example.test/rules",
            "https://example.test:443/rules?version=2#top",
            "https://t.me/share/url?url=https%3A%2F%2Fexample.test",
            "https://8.8.8.8/",
        ):
            with self.subTest(url=target):
                self.assertTrue(is_safe_https_url(target))
        for target in (
            "http://example.test/rules",
            "javascript:alert(1)",
            "https://user@example.test/rules",
            "https://localhost/rules",
            "https://127.0.0.1/rules",
            "https://127.1/rules",
            "https://127.0.1/rules",
            "https://0177.0.0.1/rules",
            "https://0x7f.0.0.1/rules",
            "https://1.1/rules",
            "https://example.test:444/rules",
            "https://example.test:/rules",
            " https://example.test/rules",
            "https://example.test/rules ",
            "https://example.test/line\nbreak",
        ):
            with self.subTest(unsafe_url=target):
                self.assertFalse(is_safe_https_url(target))

    def test_invalid_runtime_settings_fail_before_startup(self) -> None:
        invalid_cases = {
            "BOOTSTRAP_ADMIN_USERNAME=x": "BOOTSTRAP_ADMIN_USERNAME",
            "BOOTSTRAP_ADMIN_CHAT_ID=-1": "BOOTSTRAP_ADMIN_CHAT_ID",
            "POLL_TIMEOUT_SECONDS=0": "POLL_TIMEOUT_SECONDS",
            "POLL_TIMEOUT_SECONDS=30\nREQUEST_TIMEOUT_SECONDS=30": "REQUEST_TIMEOUT_SECONDS",
            "JOB_INTERVAL_SECONDS=0": "JOB_INTERVAL_SECONDS",
            "ORDER_EXPIRY_MINUTES=0": "ORDER_EXPIRY_MINUTES",
            "RECEIPT_DELAY_SECONDS=-1": "RECEIPT_DELAY_SECONDS",
            "ORDER_EXPIRY_MINUTES=1\nRECEIPT_DELAY_SECONDS=60": "RECEIPT_DELAY_SECONDS",
            "TIMEZONE=Not/A_Timezone": "TIMEZONE",
            "PAYMENT_CALLBACK_PORT=65536": "PAYMENT_CALLBACK_PORT",
            "PAYMENT_CALLBACK_SECRET=weak": "PAYMENT_CALLBACK_SECRET",
            "PAYMENT_CALLBACK_SECRET=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA!": "PAYMENT_CALLBACK_SECRET",
            "PUBLIC_PAYMENT_CALLBACK_URL=http://example.test/callback": "PUBLIC_PAYMENT_CALLBACK_URL",
            "PLISIO_AMOUNT_MULTIPLIER=0": "PLISIO_AMOUNT_MULTIPLIER",
            "LOG_LEVEL=TRACE": "LOG_LEVEL",
            "TELEGRAM_API_BASE=not-a-url": "TELEGRAM_API_BASE",
            "TELEGRAM_API_BASE=http://collector.example": "TELEGRAM_API_BASE",
            "TELEGRAM_API_BASE=http://192.168.1.10:8081": "TELEGRAM_API_BASE",
            "BUTTON_ICON_SHOP=not-numeric": "BUTTON_ICON_SHOP",
            "POLL_TIMEOUT_SECONDS=abc": "POLL_TIMEOUT_SECONDS",
        }
        for extra, expected in invalid_cases.items():
            with self.subTest(extra=extra), tempfile.TemporaryDirectory() as directory:
                env_path = Path(directory) / ".env"
                env_path.write_text(
                    "BOT_TOKEN=test-token\n"
                    "BOOTSTRAP_ADMIN_USERNAME=valid_owner\n"
                    f"DATA_DIR={directory}/data\n"
                    f"{extra}\n",
                    encoding="utf-8",
                )
                with patch.dict(os.environ, {}, clear=True), self.assertRaises(
                    RuntimeError
                ) as caught:
                    load_settings(env_path)
                self.assertIn(expected, str(caught.exception))

    def test_secure_callback_secret_and_loopback_api_base_are_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env_path = Path(directory) / ".env"
            env_path.write_text(
                "BOT_TOKEN=test-token\n"
                "BOOTSTRAP_ADMIN_USERNAME=valid_owner\n"
                f"DATA_DIR={directory}/data\n"
                f"PAYMENT_CALLBACK_SECRET={'A' * 43}\n"
                "TELEGRAM_API_BASE=http://127.0.0.1:8081/local-api\n",
                encoding="utf-8",
            )
            with patch.dict(os.environ, {}, clear=True):
                settings = load_settings(env_path)
            self.assertTrue(settings.payment_callback_enabled)
            self.assertEqual(
                settings.telegram_api_base,
                "http://127.0.0.1:8081/local-api",
            )


if __name__ == "__main__":
    unittest.main()
