"""Use emitted Telegram buttons for the requested force-join channel hierarchy."""
from __future__ import annotations

import copy
import unittest
from unittest.mock import patch

from app.bot import BotApplication
from app.db import DatabaseError
from app.keyboards import callback_button, contains_emoji, force_join_channel_button, reply_button
from app.telegram import TelegramError
from tests import test_admin_ui_navigation as fixture


class AdminJoinNavigationTests(unittest.TestCase):
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

    def buttons(self):
        return [b for row in self.prompt()["reply_markup"]["inline_keyboard"] for b in row]

    def view(self):
        state = self.db.get_user_state(self.actor_user()["id"])
        self.assertEqual(state["state"], "admin:joins")
        return state["data"]

    def channel(self, title="کانال اول", *, active=True, suffix="first"):
        return self.db.upsert_force_join_channel("@join_" + suffix, title, invite_url="https://t.me/join_" + suffix, active=active)

    def current(self, channel_id):
        return next((c for c in self.db.list_force_join_channels(active_only=False) if c["id"] == channel_id), None)

    def open_joins(self):
        self.send_message(self.OWNER, text="/start")
        self.click(label="پنل مدیریت")
        self.click(label="مدیریت کلی ربات")
        self.click(label="جوین اجباری")

    def valid_api(self, method, params=None):
        if method == "getMe":
            return {"id": 8255000000}
        if method == "getChat":
            return {"type": "channel"}
        if method == "getChatMember":
            return {"status": "administrator"}
        return True

    def test_empty_list_has_only_add_and_back_and_settings_has_one_entry(self):
        self.open_joins()
        self.assertEqual([b["text"] for b in self.buttons()], ["افزودن کانال اجباری", "بازگشت"])
        self.assertIn("کانالی ثبت نشده", self.prompt()["text"])
        self.click(label="بازگشت")
        labels = [b["text"] for b in self.buttons()]
        self.assertIn("جوین اجباری", labels)
        self.assertNotIn("افزودن کانال اجباری", labels)
        self.assertNotIn("تغییر وضعیت کانال اجباری", labels)
        self.assertNotIn("حذف کانال اجباری", labels)

    def test_each_channel_has_status_marker_and_exact_three_detail_buttons(self):
        first = self.channel("🎁 کانال اول ✅")
        second = self.channel("کانال دوم", active=False, suffix="second")
        self.open_joins()
        self.assertEqual([b["text"] for b in self.buttons()], ["✅ کانال اول", "❌ کانال دوم", "افزودن کانال اجباری", "بازگشت"])
        self.click(label="❌ کانال دوم")
        self.assertEqual(self.view()["id"], second["id"])
        self.assertEqual([b["text"] for b in self.buttons()], ["فعال/غیرفعال کردن", "حذف", "بازگشت"])
        self.assertIn("غیرفعال", self.prompt()["text"])
        self.click(label="بازگشت")
        self.click(label="✅ کانال اول")
        self.assertEqual(self.view()["id"], first["id"])

    def test_add_validates_channel_and_returns_to_list(self):
        self.open_joins()
        self.click(label="افزودن کانال اجباری")
        self.send_message(self.OWNER, text="@join_added")
        self.send_message(self.OWNER, text="کانال اضافه‌شده")
        self.send_message(self.OWNER, text="https://t.me/join_added")
        self.assertEqual(self.db.list_force_join_channels(active_only=False), [])
        with patch.object(self.telegram, "call", side_effect=self.valid_api) as api:
            self.click(label="تأیید و اجرا")
        self.assertEqual(self.state()["status"], "done")
        self.assertEqual({c.args[0] for c in api.call_args_list}, {"getMe", "getChat", "getChatMember"})
        self.click(label="بازگشت")
        self.assertIn("✅ کانال اضافه‌شده", [b["text"] for b in self.buttons()])

    def test_add_rejects_non_admin_bot_and_preserves_cancel(self):
        self.open_joins()
        self.click(label="افزودن کانال اجباری")
        for text in ("@join_denied", "کانال ممنوع", "https://t.me/join_denied"):
            self.send_message(self.OWNER, text=text)
        def denied(method, params=None):
            return {"status": "member"} if method == "getChatMember" else self.valid_api(method, params)
        with patch.object(self.telegram, "call", side_effect=denied):
            self.click(label="تأیید و اجرا")
        self.assertEqual(self.state()["status"], "confirm")
        self.assertEqual(self.db.list_force_join_channels(active_only=False), [])
        self.click(label="لغو و بازگشت")
        self.assertEqual(self.view()["kind"], "list")

    def test_toggle_only_selected_channel_and_duplicate_confirm_does_not_toggle_twice(self):
        first, second = self.channel(), self.channel("کانال دوم", suffix="second")
        self.open_joins()
        self.click(label="✅ کانال اول")
        self.click(label="فعال/غیرفعال کردن")
        self.assertEqual(self.state()["values"]["target"], str(first["id"]))
        self.assertTrue(self.current(first["id"])["is_active"])
        prompt = copy.deepcopy(self.prompt())
        update = self.click(label="تأیید و اجرا")
        self.app.process_update(copy.deepcopy(update))
        self.click(label="تأیید و اجرا", prompt=prompt)
        self.assertFalse(self.current(first["id"])["is_active"])
        self.assertTrue(self.current(second["id"])["is_active"])
        self.click(label="بازگشت")
        self.assertEqual(self.view()["kind"], "channel")
        self.click(label="بازگشت")
        self.assertIn("❌ کانال اول", [b["text"] for b in self.buttons()])
        self.click(label="❌ کانال اول")
        self.click(label="فعال/غیرفعال کردن")
        with patch.object(self.telegram, "call", side_effect=self.valid_api):
            self.click(label="تأیید و اجرا")
        self.assertTrue(self.current(first["id"])["is_active"])

    def test_delete_cancel_stays_on_channel_then_confirm_returns_to_list(self):
        channel = self.channel()
        self.open_joins()
        self.click(label="✅ کانال اول")
        self.click(label="حذف")
        self.click(label="لغو و بازگشت")
        self.assertEqual(self.view()["id"], channel["id"])
        self.assertIsNotNone(self.current(channel["id"]))
        self.click(label="حذف")
        prompt = copy.deepcopy(self.prompt())
        update = self.click(label="تأیید و اجرا")
        self.app.process_update(copy.deepcopy(update))
        self.click(label="بازگشت")
        self.assertIsNone(self.current(channel["id"]))
        self.assertEqual(self.view()["kind"], "list")
        self.click(label="تأیید و اجرا", prompt=prompt)
        self.assertEqual(self.view()["kind"], "list")

    def test_pages_preserve_scope_after_return_and_clamp_after_deletion(self):
        for index in range(21):
            self.channel(f"کانال {index}", suffix=str(index))
        self.open_joins()
        self.assertEqual(len([b for b in self.buttons() if ":j:channel:" in b["callback_data"]]), 20)
        self.click(label="صفحه بعد")
        self.click(label="✅ کانال 20")
        self.click(label="بازگشت")
        self.assertEqual(self.view()["page"], 2)
        self.click(label="✅ کانال 20")
        self.click(label="حذف")
        self.click(label="تأیید و اجرا")
        self.click(label="بازگشت")
        self.assertEqual(self.view()["page"], 1)
        self.assertNotIn("صفحه بعد", [b["text"] for b in self.buttons()])

    def test_reordered_and_deleted_channel_buttons_never_select_another_channel(self):
        channel = self.channel()
        self.open_joins()
        old = copy.deepcopy(self.prompt())
        self.db.upsert_force_join_channel("@join_earlier", "قبلی", sort_order=-1)
        self.click(label="✅ کانال اول", prompt=old)
        self.assertEqual(self.view()["id"], channel["id"])
        detail = copy.deepcopy(self.prompt())
        self.db.delete_force_join_channel(channel["id"])
        self.click(label="حذف", prompt=detail)
        self.assertEqual(self.view()["kind"], "list")
        self.assertEqual(len(self.db.list_force_join_channels(active_only=False)), 1)

    def test_private_chat_role_revocation_and_malformed_routes_fail_closed(self):
        channel = self.channel()
        actor = {"id": 58100, "username": "join_admin", "first_name": "مدیر تست"}
        self.db.upsert_user(actor["id"], actor["id"], username=actor["username"])
        self.db.add_admin(actor["username"], actor["id"], role="admin")
        with patch.object(self, "OWNER", actor):
            self.open_joins()
            before = self.view()
            list_prompt = copy.deepcopy(self.prompt())
            for suffix in ("list:0", "channel:x:1", "delete:999999999999999999999:1", "toggle:1:-1", "unknown"):
                self.send_callback(actor, "adm:ui:j:" + suffix)
                self.assertEqual(self.view(), before)
            forged = self.button_update(label="✅ کانال اول", prompt=list_prompt)
            forged["callback_query"]["message"]["chat"]["type"] = "group"
            self.app.process_update(forged)
            self.assertEqual(self.view(), before)
            self.click(label="✅ کانال اول", prompt=list_prompt)
            self.click(label="حذف")
            update = self.button_update(label="تأیید و اجرا")
            self.db.add_admin(actor["username"], actor["id"], role="support")
            self.app.process_update(update)
            self.assertIsNotNone(self.current(channel["id"]))
            count = len(self.telegram.messages)
            self.send_callback(actor, "adm:ui:j:list:1")
            self.assertFalse(any("لیست کانال‌های جوین اجباری" in m["text"] for m in self.telegram.messages[count:]))

    def test_restart_and_start_retire_previous_channel_keyboard(self):
        self.channel()
        self.open_joins()
        old = copy.deepcopy(self.prompt())
        self.app = BotApplication(self.settings, self.db, self.telegram)
        self.app.initialize()
        self.click(label="✅ کانال اول")
        self.assert_retired(old)
        detail = copy.deepcopy(self.prompt())
        self.send_message(self.OWNER, text="/start")
        self.assert_retired(detail)
        self.assertIsNone(self.db.get_user_state(self.actor_user()["id"]))

    def test_crash_after_toggle_commit_replays_frozen_effect_and_cleanup_is_best_effort(self):
        channel = self.channel()
        self.open_joins()
        self.click(label="✅ کانال اول")
        self.click(label="فعال/غیرفعال کردن")
        update = self.button_update(label="تأیید و اجرا")
        original = self.db.set_force_join_channel_active
        failed = False
        def crash_after(*args, **kwargs):
            nonlocal failed
            result = original(*args, **kwargs)
            if not failed:
                failed = True
                raise DatabaseError("simulated force-join toggle commit crash")
            return result
        with patch.object(self.db, "set_force_join_channel_active", side_effect=crash_after):
            self.assertIs(self.app.process_update_safe(copy.deepcopy(update)), False)
            with patch.object(self.telegram, "edit_message_reply_markup", side_effect=TelegramError("uneditable")):
                self.assertIsNot(self.app.process_update_safe(copy.deepcopy(update)), False)
        self.assertTrue(failed)
        self.assertFalse(self.current(channel["id"])["is_active"])
        self.assertEqual(self.state()["status"], "done")

    def test_only_channel_rows_allow_fixed_status_emoji_and_general_builders_stay_strict(self):
        for active, symbol in ((True, "✅"), (False, "❌")):
            button = force_join_channel_button("کانال", 1, 1, active=active)
            self.assertEqual(button["text"], symbol + " کانال")
            self.assertEqual(button["callback_data"], "adm:ui:j:channel:1:1")
            with self.assertRaises(ValueError):
                force_join_channel_button("کانال 🎁", 1, 1, active=active)
        with self.assertRaises(ValueError):
            callback_button("✅ کانال", "anywhere")
        with self.assertRaises(ValueError):
            reply_button("❌ کانال")
        self.channel("🎁 عنوان ✅ ❌")
        self.open_joins()
        self.click(label="✅ عنوان")
        for message in self.telegram.messages:
            for row in (message.get("reply_markup") or {}).get("inline_keyboard", []):
                for button in row:
                    label = button["text"]
                    if button.get("callback_data", "").startswith("adm:ui:j:channel:"):
                        self.assertIn(label[:2], {"✅ ", "❌ "})
                        label = label[2:]
                    self.assertFalse(contains_emoji(label))
                    self.assertLessEqual(len(button.get("callback_data", "").encode()), 64)

    def test_prompt_commit_crash_recovers_clickable_channel_controls(self):
        self.channel()
        self.open_joins()
        update = self.button_update(label="✅ کانال اول")
        original = self.db.set_user_state
        failed = False
        def crash_after(*args, **kwargs):
            nonlocal failed
            result = original(*args, **kwargs)
            if not failed and args[1] == "admin:joins" and args[2].get("prompt_message_id"):
                failed = True
                raise DatabaseError("simulated force-join prompt commit crash")
            return result
        with patch.object(self.db, "set_user_state", side_effect=crash_after):
            self.assertIs(self.app.process_update_safe(copy.deepcopy(update)), False)
            self.assertIsNot(self.app.process_update_safe(copy.deepcopy(update)), False)
        self.assertTrue(failed)
        self.click(label="فعال/غیرفعال کردن")
        self.click(label="لغو و بازگشت")
        self.assertEqual([b["text"] for b in self.buttons()], ["فعال/غیرفعال کردن", "حذف", "بازگشت"])


if __name__ == "__main__":
    unittest.main()
