"""Payment-method identity and live status throughout the emitted admin form."""

from __future__ import annotations

import copy
import unittest
from dataclasses import replace
from unittest.mock import patch

from app.admin_ui import ButtonInputError
from app.bot import BotApplication
from app.db import DatabaseError
from app.telegram import TelegramError
from tests import test_admin_ui_navigation as fixture


class AdminPaymentContextTests(unittest.TestCase):
    OWNER = fixture.AdminNavigationTests.OWNER
    setUp = fixture.AdminNavigationTests.setUp
    tearDown = fixture.AdminNavigationTests.tearDown
    message = fixture.AdminNavigationTests.message
    callback = fixture.AdminNavigationTests.callback
    _take_update_id = fixture.AdminNavigationTests._take_update_id
    send_message = fixture.AdminNavigationTests.send_message
    send_callback = fixture.AdminNavigationTests.send_callback
    actor_user = fixture.AdminNavigationTests.actor_user
    state = fixture.AdminNavigationTests.state
    prompt = fixture.AdminNavigationTests.prompt
    button_update = fixture.AdminNavigationTests.button_update
    click = fixture.AdminNavigationTests.click
    assert_retired = fixture.AdminNavigationTests.assert_retired

    def open_payment(self):
        self.send_message(self.OWNER, text="/start")
        self.click(label="پنل مدیریت")
        self.click(label="مدیریت کلی ربات")
        self.click(label="فعال یا غیرفعال‌کردن روش پرداخت")

    def assert_context(self, name, enabled):
        text = self.prompt()["text"]
        self.assertIn("روش انتخاب‌شده: " + name, text)
        self.assertIn("وضعیت فعلی: " + ("فعال" if enabled else "غیرفعال"), text)

    def test_selected_wallet_name_is_visible_before_choosing_new_status(self):
        self.open_payment()
        self.click(label="کیف پول")
        self.assertIn("روش انتخاب‌شده: کیف پول", self.prompt()["text"])
        self.assertEqual(self.state()["step"], 1)
        self.assertTrue(self.db.get_setting("payment_wallet_enabled"))

    def test_selected_method_current_status_is_visible_for_all_three_methods(self):
        for method, name in (("wallet", "کیف پول"), ("card", "کارت بانکی"), ("crypto", "رمزارز")):
            for enabled in (False, True):
                with self.subTest(method=method, enabled=enabled):
                    self.db.set_setting(f"payment_{method}_enabled", enabled)
                    self.open_payment()
                    self.click(label=name)
                    self.assert_context(name, enabled)
                    self.assertEqual(self.db.get_setting(f"payment_{method}_enabled"), enabled)

    def test_confirmation_distinguishes_current_and_new_status_then_result_is_fresh(self):
        for enabled, target_label in ((True, "غیرفعال"), (False, "فعال")):
            with self.subTest(enabled=enabled):
                self.db.set_setting("payment_wallet_enabled", enabled)
                others = [self.db.get_setting("payment_card_enabled"), self.db.get_setting("payment_crypto_enabled")]
                self.open_payment()
                self.click(label="کیف پول")
                self.click(label=target_label)
                self.assert_context("کیف پول", enabled)
                self.assertIn("وضعیت جدید: " + target_label, self.prompt()["text"])
                self.assertEqual(self.db.get_setting("payment_wallet_enabled"), enabled)
                self.click(label="تأیید و اجرا")
                self.assertEqual(self.db.get_setting("payment_wallet_enabled"), not enabled)
                self.assert_context("کیف پول", not enabled)
                self.assertEqual(others, [self.db.get_setting("payment_card_enabled"), self.db.get_setting("payment_crypto_enabled")])

    def test_back_clears_old_method_and_reselecting_displays_the_new_context(self):
        self.db.set_setting("payment_wallet_enabled", False)
        self.db.set_setting("payment_card_enabled", True)
        self.open_payment()
        self.click(label="کیف پول")
        old = copy.deepcopy(self.prompt())
        self.click(label="مرحله قبل / اصلاح")
        self.assertNotIn("روش انتخاب‌شده:", self.prompt()["text"])
        self.click(label="کارت بانکی")
        self.assert_context("کارت بانکی", True)
        self.assertNotIn("روش انتخاب‌شده: کیف پول", self.prompt()["text"])
        self.assert_retired(old)
        self.click(label="غیرفعال")
        self.click(label="مرحله قبل / اصلاح")
        self.assert_context("کارت بانکی", True)
        self.assertEqual(self.state()["step"], 1)

    def test_cancel_at_status_or_confirmation_does_not_mutate_any_payment_setting(self):
        before = {m: self.db.get_setting(f"payment_{m}_enabled") for m in ("wallet", "card", "crypto")}
        for confirmation in (False, True):
            with self.subTest(confirmation=confirmation):
                self.open_payment()
                self.click(label="کیف پول")
                if confirmation:
                    self.click(label="غیرفعال")
                self.click(label="لغو و بازگشت")
                self.assertIsNone(self.state())
                self.assertEqual(before, {m: self.db.get_setting(f"payment_{m}_enabled") for m in before})

    def test_stale_choice_recovers_fresh_database_status_without_accepting_old_input(self):
        self.open_payment()
        self.click(label="کیف پول")
        old = copy.deepcopy(self.prompt())
        self.click(label="غیرفعال")
        self.db.set_setting("payment_wallet_enabled", False)
        self.click(label="فعال", prompt=old)
        self.assertEqual(self.state()["values"], {"method": "wallet", "enabled": "off"})
        self.assert_context("کیف پول", False)
        self.assertFalse(self.db.get_setting("payment_wallet_enabled"))

    def test_restart_and_legacy_labels_preserve_selected_method_and_read_live_status(self):
        self.open_payment()
        self.click(label="کیف پول")
        state = self.state()
        state["labels"].pop("method", None)
        self.db.set_user_state(self.actor_user()["id"], "admin:ui", state)
        self.db.set_setting("payment_wallet_enabled", False)
        self.app = BotApplication(self.settings, self.db, self.telegram)
        self.app.initialize()
        # Continue via the actual pre-restart keyboard, not invented state data.
        self.click(label="غیرفعال")
        self.assert_context("کیف پول", False)
        self.assertEqual(self.state()["values"]["method"], "wallet")

    def test_incomplete_card_configuration_is_explained_without_exposing_bank_details(self):
        self.db.set_setting("card_owner", "")
        self.open_payment()
        self.click(label="کارت بانکی")
        self.assert_context("کارت بانکی", True)
        self.assertIn("تنظیمات کارت کامل نیست", self.prompt()["text"])
        self.assertNotIn(str(self.db.get_setting("card_number")), self.prompt()["text"])
        self.click(label="فعال")
        self.click(label="تأیید و اجرا")
        self.assertEqual(self.state()["status"], "confirm")
        self.assert_context("کارت بانکی", True)

    def test_crypto_key_readiness_is_explained_and_enable_guard_is_preserved(self):
        self.open_payment()
        self.click(label="رمزارز")
        self.assert_context("رمزارز", False)
        self.assertIn("تنظیمات پرداخت ارزی کامل نیست", self.prompt()["text"])
        self.click(label="فعال")
        self.click(label="تأیید و اجرا")
        self.assertFalse(self.db.get_setting("payment_crypto_enabled"))
        self.assert_context("رمزارز", False)
        configured = replace(self.settings, plisio_api_key="configured-for-offline-test")
        with patch.object(self.app.admin_controller, "settings", configured):
            self.click(label="تأیید و اجرا")
            self.assertTrue(self.db.get_setting("payment_crypto_enabled"))
            self.assert_context("رمزارز", True)
            self.assertNotIn("تنظیمات پرداخت ارزی کامل نیست", self.prompt()["text"])
            self.assertNotIn(configured.plisio_api_key, self.prompt()["text"])

    def test_role_revocation_and_nonprivate_confirmation_do_not_change_method_status(self):
        actor = {"id": 72100, "username": "payment_admin", "first_name": "مدیر"}
        self.db.upsert_user(actor["id"], actor["id"], username=actor["username"])
        self.db.add_admin(actor["username"], actor["id"], role="admin")
        with patch.object(self, "OWNER", actor):
            self.open_payment()
            self.click(label="کیف پول")
            self.click(label="غیرفعال")
            original = copy.deepcopy(self.prompt())
            forged = self.button_update(label="تأیید و اجرا")
            forged["callback_query"]["message"]["chat"]["type"] = "group"
            self.app.process_update(forged)
            self.assertTrue(self.db.get_setting("payment_wallet_enabled"))
            self.db.add_admin(actor["username"], actor["id"], role="support")
            self.click(label="تأیید و اجرا", prompt=original)
            self.assertTrue(self.db.get_setting("payment_wallet_enabled"))

    def test_crash_after_setting_commit_and_duplicate_confirm_keep_the_same_target(self):
        self.open_payment()
        self.click(label="کیف پول")
        self.click(label="غیرفعال")
        original_prompt = copy.deepcopy(self.prompt())
        update = self.button_update(label="تأیید و اجرا")
        original = self.db.set_setting
        failed = False

        def crash(key, value):
            nonlocal failed
            result = original(key, value)
            if key == "payment_wallet_enabled" and not failed:
                failed = True
                raise DatabaseError("simulated payment-setting commit failure")
            return result

        with patch.object(self.db, "set_setting", side_effect=crash):
            self.assertIs(self.app.process_update_safe(copy.deepcopy(update)), False)
        self.assertTrue(failed)
        self.assertFalse(self.db.get_setting("payment_wallet_enabled"))
        with patch.object(self.telegram, "edit_message_reply_markup", side_effect=TelegramError("uneditable")):
            self.assertIsNot(self.app.process_update_safe(copy.deepcopy(update)), False)
        self.assert_context("کیف پول", False)
        self.db.set_setting("payment_wallet_enabled", True)
        self.click(label="تأیید و اجرا", prompt=original_prompt)
        self.assertTrue(self.db.get_setting("payment_wallet_enabled"))
        self.assert_context("کیف پول", True)

    def test_unknown_method_cannot_read_arbitrary_settings_or_render_a_forged_label(self):
        for method in ("<secret>", "", [], {}, False):
            with self.subTest(method=method), patch.object(self.db, "get_setting") as get_setting:
                with self.assertRaises(ButtonInputError):
                    self.app.admin_controller.button_ui.payment_context({"values": {"method": method}})
                get_setting.assert_not_called()


if __name__ == "__main__":
    unittest.main()
