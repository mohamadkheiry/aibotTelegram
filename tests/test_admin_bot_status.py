"""One specification-backed access switch, exercised from emitted keyboards."""

from __future__ import annotations

import copy
import unittest
from unittest.mock import patch

from app.bot import BotApplication
from app.db import DatabaseError
from app.keyboards import contains_emoji
from app.telegram import TelegramError
from tests import test_admin_ui_navigation as fixture


class AdminBotStatusTests(unittest.TestCase):
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

    def open_settings(self):
        self.send_message(self.OWNER, text="/start")
        self.click(label="پنل مدیریت")
        self.click(label="مدیریت کلی ربات")

    def assert_controls(self, enabled):
        controls = [b for row in self.prompt()["reply_markup"]["inline_keyboard"] for b in row
                    if b.get("callback_data") in {"adm:ui:a:bot_on", "adm:ui:a:bot_off"}]
        self.assertEqual([b["text"] for b in controls],
                         ["غیرفعال‌کردن ربات"] if enabled else ["فعال‌کردن ربات"])
        for button in controls:
            self.assertEqual(button["callback_data"], "adm:ui:a:bot_off" if enabled else "adm:ui:a:bot_on")
            self.assertEqual(button["style"], "danger" if enabled else "success")
            self.assertFalse(contains_emoji(button["text"]))
            self.assertLessEqual(len(button["callback_data"].encode()), 64)

    def test_active_settings_offer_exactly_one_disable_button(self):
        self.open_settings()
        self.assert_controls(True)
        self.assertTrue(any("دسترسی کاربران: باز" in m["text"] for m in self.telegram.messages))

    def test_inactive_settings_offer_exactly_one_enable_button(self):
        self.db.set_setting("bot_enabled", False)
        self.open_settings()
        self.assert_controls(False)
        self.assertTrue(any("دسترسی کاربران: بسته (نمایش پیام بروزرسانی)" in m["text"] for m in self.telegram.messages))

    def test_bot_control_round_trip_immediately_offers_the_reverse_action(self):
        self.open_settings()
        self.click(label="غیرفعال‌کردن ربات")
        self.assertTrue(self.db.get_setting("bot_enabled"))
        self.click(label="تأیید و اجرا")
        self.assertFalse(self.db.get_setting("bot_enabled"))
        self.assert_controls(False)
        self.click(label="فعال‌کردن ربات")
        self.assertFalse(self.db.get_setting("bot_enabled"))
        self.click(label="تأیید و اجرا")
        self.assertTrue(self.db.get_setting("bot_enabled"))
        self.assert_controls(True)

    def test_previously_emitted_maintenance_alias_remains_an_explicit_confirmed_action(self):
        ui = self.app.admin_controller.button_ui
        current = ui.bot_status
        for enabled, alias in ((True, "فعال‌کردن حالت تعمیرات"), (False, "غیرفعال‌کردن حالت تعمیرات")):
            with self.subTest(enabled=enabled):
                self.db.set_setting("bot_enabled", enabled)

                def legacy_keyboard():
                    status, rows = current()
                    rows.append([{**rows[0][0], "text": alias}])
                    return status, rows

                # Render an actual legacy two-button message through FakeTelegram;
                # use its callback/message ID, not reconstructed form revisions.
                with patch.object(ui, "bot_status", side_effect=legacy_keyboard):
                    self.open_settings()
                    previous = copy.deepcopy(self.prompt())
                self.click(label=alias, prompt=previous)
                self.assertEqual(self.state()["action"], "bot_off" if enabled else "bot_on")
                self.assertEqual(self.db.get_setting("bot_enabled"), enabled)
                self.click(label="تأیید و اجرا")
                self.assertEqual(self.db.get_setting("bot_enabled"), not enabled)
                self.assert_controls(not enabled)

    def test_confirmation_explains_current_target_and_cancel_does_not_change_state(self):
        for enabled, label in ((True, "غیرفعال‌کردن ربات"), (False, "فعال‌کردن ربات")):
            with self.subTest(enabled=enabled):
                self.db.set_setting("bot_enabled", enabled)
                self.open_settings()
                self.click(label=label)
                original = copy.deepcopy(self.prompt())
                self.assertIn("وضعیت فعلی", original["text"])
                self.assertIn("پس از تأیید", original["text"])
                self.assertIn("ربات غیرفعال می‌شود؛ کاربران پیام بروزرسانی می‌بینند." if enabled else
                              "ربات فعال می‌شود و دسترسی کاربران باز می‌شود.", original["text"])
                self.assertIn("دسترسی مدیران", original["text"])
                self.click(label="لغو و بازگشت")
                self.assertEqual(self.db.get_setting("bot_enabled"), enabled)
                self.assert_controls(enabled)
                self.assert_retired(original)

    def test_old_action_keeps_its_explicit_target_instead_of_toggling_current_state(self):
        self.open_settings()
        old = copy.deepcopy(self.prompt())
        # Another authorized operation already disabled the bot.
        self.db.set_setting("bot_enabled", False)
        self.click(label="غیرفعال‌کردن ربات", prompt=old)
        self.assertEqual(self.state()["action"], "bot_off")
        self.assertFalse(self.db.get_setting("bot_enabled"))
        self.click(label="تأیید و اجرا")
        self.assertFalse(self.db.get_setting("bot_enabled"))
        self.assert_controls(False)

    def test_duplicate_or_old_confirmation_cannot_undo_a_later_change(self):
        self.open_settings()
        self.click(label="غیرفعال‌کردن ربات")
        original = copy.deepcopy(self.prompt())
        update = self.click(label="تأیید و اجرا")
        self.assertIsNot(self.app.process_update_safe(copy.deepcopy(update)), False)
        self.assert_controls(False)
        self.click(label="فعال‌کردن ربات")
        self.click(label="تأیید و اجرا")
        self.click(label="تأیید و اجرا", prompt=original)
        self.assertIsNot(self.app.process_update_safe(copy.deepcopy(update)), False)
        self.assertTrue(self.db.get_setting("bot_enabled"))
        self.assert_controls(True)

    def test_restart_preserves_status_and_admin_can_restore_customer_access(self):
        self.open_settings()
        self.click(label="غیرفعال‌کردن ربات")
        self.click(label="تأیید و اجرا")
        self.app = BotApplication(self.settings, self.db, self.telegram)
        self.app.initialize()
        customer = {"id": 71100, "username": "status_customer", "first_name": "مشتری"}
        self.send_message(customer, text="/start")
        self.assertIn("در حال بروزرسانی", self.telegram.messages[-1]["text"])
        self.open_settings()
        self.assert_controls(False)
        self.click(label="فعال‌کردن ربات")
        self.click(label="تأیید و اجرا")
        self.send_message(customer, text="/start")
        self.assertTrue(self.telegram.messages[-1].get("reply_markup", {}).get("inline_keyboard"))
        self.assertNotIn("در حال بروزرسانی", self.telegram.messages[-1]["text"])

    def test_group_and_revoked_role_cannot_confirm_a_status_change(self):
        actor = {"id": 71200, "username": "status_admin", "first_name": "مدیر"}
        self.db.upsert_user(actor["id"], actor["id"], username=actor["username"])
        self.db.add_admin(actor["username"], actor["id"], role="admin")
        with patch.object(self, "OWNER", actor):
            self.open_settings()
            self.click(label="غیرفعال‌کردن ربات")
            original = copy.deepcopy(self.prompt())
            forged = self.button_update(label="تأیید و اجرا")
            forged["callback_query"]["message"]["chat"]["type"] = "group"
            self.app.process_update(forged)
            self.assertTrue(self.db.get_setting("bot_enabled"))
            self.db.add_admin(actor["username"], actor["id"], role="support")
            self.click(label="تأیید و اجرا", prompt=original)
            self.send_callback(actor, "adm:ui:a:bot_off")
            self.assertTrue(self.db.get_setting("bot_enabled"))

    def test_crash_before_or_after_setting_commit_replays_the_same_target(self):
        for after_commit in (False, True):
            with self.subTest(after_commit=after_commit):
                self.db.set_setting("bot_enabled", True)
                self.open_settings()
                self.click(label="غیرفعال‌کردن ربات")
                update = self.button_update(label="تأیید و اجرا")
                original = self.db.set_setting
                failed = False

                def crash(key, value):
                    nonlocal failed
                    if key == "bot_enabled" and not failed:
                        failed = True
                        if after_commit:
                            original(key, value)
                        raise DatabaseError("simulated bot status persistence failure")
                    return original(key, value)

                with patch.object(self.db, "set_setting", side_effect=crash):
                    self.assertIs(self.app.process_update_safe(copy.deepcopy(update)), False)
                self.assertTrue(failed)
                self.assertEqual(self.db.get_setting("bot_enabled"), not after_commit)
                self.app = BotApplication(self.settings, self.db, self.telegram)
                self.app.initialize()
                self.assertIsNot(self.app.process_update_safe(copy.deepcopy(update)), False)
                self.assertFalse(self.db.get_setting("bot_enabled"))
                self.assert_controls(False)

    def test_failed_keyboard_cleanup_keeps_reversible_controls_and_legacy_commands(self):
        self.open_settings()
        self.click(label="غیرفعال‌کردن ربات")
        update = self.button_update(label="تأیید و اجرا")
        with patch.object(self.telegram, "edit_message_reply_markup", side_effect=TelegramError("uneditable")):
            self.assertIsNot(self.app.process_update_safe(copy.deepcopy(update)), False)
        self.assertFalse(self.db.get_setting("bot_enabled"))
        self.assert_controls(False)
        self.send_message(self.OWNER, text="/bot_on")
        self.assertTrue(self.db.get_setting("bot_enabled"))
        self.send_message(self.OWNER, text="/bot_off")
        self.assertFalse(self.db.get_setting("bot_enabled"))
        self.open_settings()
        self.assert_controls(False)

    def test_settings_and_confirmations_never_imply_two_independent_flags(self):
        for enabled in (True, False):
            with self.subTest(enabled=enabled):
                self.db.set_setting("bot_enabled", enabled)
                self.telegram.messages.clear()
                self.open_settings()
                self.click(label="غیرفعال‌کردن ربات" if enabled else "فعال‌کردن ربات")
                self.assertFalse(any("حالت تعمیرات:" in message["text"] for message in self.telegram.messages))
                self.assertIn("وضعیت فعلی", self.prompt()["text"])
                self.assertIn("دسترسی کاربران:", self.prompt()["text"])
                self.assertIn("دسترسی مدیران", self.prompt()["text"])

    def test_recovered_done_form_still_has_only_one_status_control(self):
        self.open_settings()
        self.click(label="غیرفعال‌کردن ربات")
        confirm = copy.deepcopy(self.prompt())
        self.click(label="تأیید و اجرا")
        self.assert_controls(False)
        self.app = BotApplication(self.settings, self.db, self.telegram)
        self.app.initialize()
        self.click(label="تأیید و اجرا", prompt=confirm)
        self.assert_controls(False)
        self.assertFalse(self.db.get_setting("bot_enabled"))


if __name__ == "__main__":
    unittest.main()
