"""Real emitted-keyboard acceptance; isolated SQLite and FakeTelegram only."""
from __future__ import annotations

import copy
import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from app.bot import BotApplication
from app.customer_layouts import GROUPS, SECTIONS, defaults, keyboard
from app.db import ConflictError, DatabaseError, ValidationError
from app.keyboards import contains_emoji
from tests import test_admin_ui_navigation as fixture


class AdminLayoutTests(unittest.TestCase):
    OWNER = fixture.AdminNavigationTests.OWNER
    setUp = fixture.AdminNavigationTests.setUp
    tearDown = fixture.AdminNavigationTests.tearDown
    message = fixture.AdminNavigationTests.message
    callback = fixture.AdminNavigationTests.callback
    _take_update_id = fixture.AdminNavigationTests._take_update_id
    send_message = fixture.AdminNavigationTests.send_message
    send_callback = fixture.AdminNavigationTests.send_callback
    actor_user = fixture.AdminNavigationTests.actor_user
    prompt = fixture.AdminNavigationTests.prompt
    button_update = fixture.AdminNavigationTests.button_update
    click = fixture.AdminNavigationTests.click

    def state(self):
        stored = self.db.get_user_state(self.actor_user()["id"])
        self.assertEqual(stored["state"], "admin:layouts")
        return stored["data"]

    def open_section(self, section):
        base, _, ident = section.partition(":")
        self.send_callback(self.OWNER, "adm:ui:l:home")
        spec = SECTIONS[base]
        group_label = GROUPS[spec.group] + " · " + str(sum(s.group == spec.group for s in SECTIONS.values())) + " بخش"
        self.click(label=group_label)
        self.click(label=spec.title)
        if spec.scoped:
            if ident:
                record = next(row for row in self.app.admin_controller.button_ui.layouts.scoped_records(base) if str(row["id"]) == ident)
                from app.admin_ui import label_text
                self.click(label=label_text(record["label"]) + " · " + ident)
            else:
                self.click(label="الگوی مشترک همه موارد")
        self.assertEqual(self.state()["section"], section)

    def publish(self):
        self.click(label="پیش‌نمایش نهایی و انتشار")
        return self.click(label="تأیید انتشار")

    def test_settings_entry_keeps_nine_sections_and_preview_never_executes_customer_actions(self):
        self.send_callback(self.OWNER, "adm:ui:g:settings")
        self.click(label="چیدمان دکمه‌های کاربران")
        self.assertEqual(self.state()["view"], "home")
        self.open_section("order_summary")
        before = self.db.count_orders()
        self.click(label="پرداخت")
        self.assertEqual(self.db.count_orders(), before)
        buttons = [b for row in self.prompt()["reply_markup"]["inline_keyboard"] for b in row]
        self.assertTrue(all(b["callback_data"].startswith("adm:ui:") for b in buttons))
        self.assertTrue(all(not contains_emoji(b["text"]) and len(b["callback_data"].encode()) <= 64 for b in buttons))

    def test_all_39_sections_are_reachable_and_editable_using_emitted_buttons(self):
        for section in SECTIONS:
            with self.subTest(section=section):
                self.open_section(section)
                self.click(label="دوستونه")
                self.assertEqual(self.state()["draft"]["columns"], 2)
                self.click(label="پیش‌نمایش نهایی و انتشار")
                self.assertEqual(self.state()["phase"], "confirm")
                self.click(label="لغو تغییرات و بازگشت")
                self.assertEqual(self.app.layouts.snapshot(section)["version"], 0)

    def test_verified_admin_can_publish_and_revoked_admin_cannot(self):
        actor = {"id": 55881, "username": "layout_admin", "first_name": "مدیر"}
        self.db.upsert_user(actor["id"], actor["id"], username=actor["username"])
        self.db.add_admin(actor["username"], actor["id"], role="admin")
        with patch.object(self, "OWNER", actor):
            self.open_section("main")
            self.click(label="دوستونه")
            self.publish()
            self.assertEqual(self.app.layouts.snapshot("main")["version"], 1)
            self.click(label="سه‌ستونه")
            self.click(label="پیش‌نمایش نهایی و انتشار")
            update = self.button_update(label="تأیید انتشار")
            self.db.add_admin(actor["username"], actor["id"], role="support")
            self.app.process_update_safe(update)
            self.assertEqual(self.app.layouts.snapshot("main")["version"], 1)

    def test_publish_reflow_undo_and_default_reset_are_confirmed_and_immediate_on_next_menu(self):
        self.open_section("main")
        original = defaults("main")
        self.click(label="دوستونه")
        draft = self.state()["draft"]
        self.assertNotEqual(draft, original)
        self.assertEqual(self.app.layouts.snapshot("main")["config"], original)
        self.publish()
        self.assertEqual(self.app.layouts.snapshot("main")["config"], draft)
        self.app.show_main_menu(self.actor_user())
        self.assertEqual(len(self.prompt()["reply_markup"]["inline_keyboard"][0]), 2)
        self.open_section("main")
        self.click(label="بازگشت به چیدمان قبلی")
        self.assertEqual(self.app.layouts.snapshot("main")["config"], draft)
        self.click(label="تأیید انتشار")
        self.assertEqual(self.app.layouts.snapshot("main")["config"], original)
        self.click(label="سه‌ستونه")
        self.publish()
        self.click(label="بازنشانی به چیدمان اولیه")
        self.click(label="تأیید انتشار")
        self.assertEqual(self.app.layouts.snapshot("main")["config"], original)
        self.click(label="بازگشت به چیدمان قبلی")
        self.click(label="تأیید انتشار")
        self.assertEqual(self.app.layouts.snapshot("main")["config"]["columns"], 3)

    def test_cancel_draft_and_cancel_confirmation_have_no_public_effect(self):
        self.open_section("support")
        self.click(label="دوستونه")
        self.click(label="پیش‌نمایش نهایی و انتشار")
        self.click(label="بازگشت به ویرایش")
        self.click(label="لغو تغییرات و بازگشت")
        self.assertEqual(self.app.layouts.snapshot("support")["version"], 0)

    def test_selected_button_row_and_column_controls_persist_exact_draft(self):
        self.open_section("main")
        self.click(label="حساب من")
        self.click(label="ردیف مستقل برای این دکمه")
        self.assertIn(["profile"], self.state()["draft"]["rows"])
        self.click(label="انتقال به ابتدا")
        self.assertEqual(self.state()["draft"]["rows"][0], ["profile"])
        self.click(label="کنار ردیف بعدی")
        self.assertIn(["store", "profile"], self.state()["draft"]["rows"])
        self.click(label="سمت چپ در ردیف")
        self.assertIn(["profile", "store"], self.state()["draft"]["rows"])
        draft = self.state()["draft"]
        self.publish()
        self.assertEqual(self.app.layouts.snapshot("main")["config"], draft)

    def test_stale_click_replays_and_restart_cannot_publish_twice(self):
        self.open_section("main")
        stale = copy.deepcopy(self.prompt())
        self.click(label="دوستونه")
        draft = self.state()["draft"]
        self.click(label="سه‌ستونه", prompt=stale)
        self.assertEqual(self.state()["draft"], draft)
        self.app = BotApplication(self.settings, self.db, self.telegram)
        self.app.initialize()
        update = self.publish()
        self.app.process_update_safe(copy.deepcopy(update))
        self.send_callback(self.OWNER, update["callback_query"]["data"])
        self.assertEqual(self.app.layouts.snapshot("main")["version"], 1)

    def test_crash_after_commit_and_after_executing_state_replays_once(self):
        self.open_section("main")
        self.click(label="دوستونه")
        self.click(label="پیش‌نمایش نهایی و انتشار")
        update = self.button_update(label="تأیید انتشار")
        original = self.db.save_customer_layout
        calls = 0

        def crash(*args, **kwargs):
            nonlocal calls
            result = original(*args, **kwargs)
            calls += 1
            if calls == 1:
                raise DatabaseError("simulated after layout commit")
            return result

        with patch.object(self.db, "save_customer_layout", side_effect=crash):
            self.assertIs(self.app.process_update_safe(copy.deepcopy(update)), False)
            self.assertEqual(self.state()["phase"], "executing")
            self.assertIsNot(self.app.process_update_safe(copy.deepcopy(update)), False)
        self.assertEqual(self.app.layouts.snapshot("main")["version"], 1)
        document = self.db.get_setting("customer_layout:main")
        self.assertEqual(len(document["history"]), 1)

    def test_commit_journal_failure_does_not_overwrite_history_on_retry(self):
        self.open_section("support")
        self.click(label="دوستونه")
        self.click(label="پیش‌نمایش نهایی و انتشار")
        update = self.button_update(label="تأیید انتشار")
        original = self.db.complete_admin_update
        fail = True

        def crash(*args, **kwargs):
            nonlocal fail
            if fail:
                fail = False
                raise DatabaseError("simulated journal completion crash")
            return original(*args, **kwargs)

        with patch.object(self.db, "complete_admin_update", side_effect=crash):
            self.assertIs(self.app.process_update_safe(copy.deepcopy(update)), False)
            self.assertIsNot(self.app.process_update_safe(copy.deepcopy(update)), False)
        self.assertEqual(self.app.layouts.snapshot("support")["version"], 1)

    def test_stale_version_conflict_keeps_other_admin_change_and_requires_reload(self):
        self.open_section("main")
        self.click(label="دوستونه")
        self.db.set_setting("customer_layout:main", {"current": defaults("main"), "version": 5})
        self.publish()
        self.assertEqual(self.app.layouts.snapshot("main")["version"], 5)
        self.assertIn("مدیر دیگری", self.prompt()["text"])
        self.click(label="بازخوانی نسخه ذخیره‌شده")
        self.assertEqual(self.state()["version"], 5)

    def test_parent_change_conflicts_with_a_scoped_draft(self):
        self.open_section(f"product:{self.product['id']}")
        self.click(label="دوستونه")
        self.db.set_setting("customer_layout:product", {"current": defaults("product"), "version": 2})
        self.publish()
        self.assertEqual(self.app.layouts.snapshot(f"product:{self.product['id']}")["version"], 0)
        self.assertIn("مدیر دیگری", self.prompt()["text"])

    def test_deleted_target_does_not_strand_an_executing_editor(self):
        category = self.db.create_category("دسته موقت")
        section = f"category:{category['id']}"
        self.open_section(section)
        self.click(label="دوستونه")
        self.db.delete_category(category["id"])
        self.publish()
        self.assertEqual(self.state()["view"], "sections")
        self.assertEqual(self.state()["phase"], "editing")
        self.assertEqual(self.app.layouts.snapshot(section)["version"], 0)

    def test_undo_restores_exact_previous_inherited_layout_even_after_parent_changes(self):
        base = defaults("product")
        self.db.set_setting("customer_layout:product", {"current": base, "version": 1})
        section = f"product:{self.product['id']}"
        self.open_section(section)
        self.click(label="دوستونه")
        self.publish()
        changed = copy.deepcopy(base)
        changed["rows"].reverse()
        self.db.set_setting("customer_layout:product", {"current": changed, "version": 2})
        self.open_section(section)
        self.click(label="بازگشت به چیدمان قبلی")
        self.click(label="تأیید انتشار")
        self.assertEqual(self.app.layouts.snapshot(section)["config"], base)

    def test_catalog_reorder_across_pages_and_collision_with_navigation_label(self):
        created = [self.db.create_category("بازگشت" if index == 23 else f"گروه {index}") for index in range(24)]
        selected = created[-1]
        self.open_section("store")
        self.click(label="مرتب‌کردن تک‌تک گزینه‌های فهرست")
        self.send_message(self.OWNER, text="بازگشت")
        choices = [b for row in self.prompt()["reply_markup"]["inline_keyboard"] for b in row if b["text"].endswith(". بازگشت")]
        self.assertEqual(len(choices), 1)
        self.click(label=choices[0]["text"])
        self.click(label="انتقال به ابتدا")
        self.click(label="بازگشت به پیش‌نمایش")
        self.click(label="فهرست: 2 دکمه در ردیف")
        self.publish()
        self.app.show_store(self.OWNER["id"])
        markup = self.prompt()["reply_markup"]
        self.assertEqual(markup["inline_keyboard"][0][0]["callback_data"], f"cat:{selected['id']}")
        self.assertEqual(len(markup["inline_keyboard"][0]), 2)
        self.assertEqual(markup["inline_keyboard"][-1][0]["callback_data"], "menu")
        self.app.show_store(self.OWNER["id"], page=1)
        all_buttons = [b for row in self.prompt()["reply_markup"]["inline_keyboard"] for b in row]
        self.assertFalse(any(b["callback_data"] == f"cat:{selected['id']}" for b in all_buttons))

    def test_category_scoped_order_does_not_change_other_categories(self):
        other = self.db.create_category("گروه دیگر")
        child = self.db.create_category("زیرگروه", parent_id=self.category["id"])
        self.open_section(f"category:{self.category['id']}")
        self.click(label="مرتب‌کردن تک‌تک گزینه‌های فهرست")
        self.click(label="1. زیرگروه")
        self.click(label="انتقال به انتها")
        self.click(label="بازگشت به پیش‌نمایش")
        self.publish()
        self.app.show_category(self.OWNER["id"], self.category["id"])
        self.assertEqual(self.prompt()["reply_markup"]["inline_keyboard"][0][0]["callback_data"], f"prod:{self.product['id']}")
        self.assertEqual(self.app.layouts.snapshot(f"category:{other['id']}")["version"], 0)
        self.assertEqual(self.db.get_category(child["id"])["parent_id"], self.category["id"])

    def test_support_revocation_forgery_and_group_context_cannot_change_layout(self):
        support = {"id": 55770, "username": "layout_support", "first_name": "پشتیبان"}
        self.db.upsert_user(support["id"], support["id"], username=support["username"])
        self.db.add_admin(support["username"], support["id"], role="support")
        self.send_callback(support, "adm:ui:l:home")
        self.assertNotEqual((self.db.get_user_state(self.db.get_user_by_chat_id(support["id"])["id"]) or {}).get("state"), "admin:layouts")
        self.open_section("main")
        self.click(label="دوستونه")
        self.click(label="پیش‌نمایش نهایی و انتشار")
        update = self.button_update(label="تأیید انتشار")
        forged = copy.deepcopy(update)
        forged["callback_query"]["from"] = support
        self.app.process_update_safe(forged)
        group = copy.deepcopy(update)
        group["callback_query"]["message"]["chat"]["type"] = "group"
        self.app.process_update_safe(group)
        backup = self.db.upsert_user(55771, 55771, username="layout_owner")
        self.db.add_admin("layout_owner", backup["chat_id"], role="owner")
        admin = next(a for a in self.db.list_admins(active_only=True) if a["chat_id"] == self.OWNER["id"])
        self.db.set_admin_active(admin["id"], False)
        self.app.process_update_safe(update)
        self.assertEqual(self.app.layouts.snapshot("main")["version"], 0)

    def test_database_guard_collision_and_role_are_independent_of_ui(self):
        self.open_section("main")
        self.click(label="دوستونه")
        update = self.publish()
        journal = self.db.get_admin_update(update["update_id"])
        effect = json.loads(journal["effect_json"])["value"]
        request = effect["request"]
        params = dict(expected_version=request["version"], expected_base_version=request["base_version"],
                      admin_id=request["admin_id"], chat_id=request["chat_id"], update_id=update["update_id"])
        self.assertEqual(self.db.save_customer_layout("main", request["config"], **params)["version"], 1)
        with self.assertRaises(ConflictError):
            self.db.save_customer_layout("main", defaults("main"), **params)
        params["chat_id"] = 1
        with self.assertRaises(ValidationError):
            self.db.save_customer_layout("main", request["config"], **params)

    def test_queued_notifications_are_canonical_and_apply_new_layout_at_delivery(self):
        self.open_section("order_summary")
        self.click(label="دوستونه")
        markup = keyboard("order_summary", [[{"text": "پرداخت", "callback_data": "checkout:999"}],
                                           [{"text": "ثبت کد تخفیف", "callback_data": "discount:999"}]])
        original = copy.deepcopy(markup)
        self.publish()
        self.app.telegram.send_message(self.OWNER["id"], "نمونه", reply_markup=markup)
        self.assertEqual(len(self.prompt()["reply_markup"]["inline_keyboard"][0]), 2)
        self.assertEqual(markup, original)

    def test_real_outbox_replay_keeps_canonical_data_and_checks_action_collisions(self):
        self.open_section("order_summary")
        self.click(label="دوستونه")
        self.publish()
        markup = keyboard("order_summary", [[{"text": "پرداخت", "callback_data": "checkout:999"}],
                                           [{"text": "ثبت کد تخفیف", "callback_data": "discount:999"}]])
        item = self.db.queue_outbound_message("اعلان ساختگی", idempotency_key="layout-notice-test",
                                              recipient_user_id=self.actor_user()["id"], reply_markup=markup)
        self.app._deliver_outbound_messages()
        self.assertEqual(self.db.get_outbound_message_by_idempotency_key("layout-notice-test")["status"], "sent")
        self.assertEqual(self.db.get_outbound_message_by_idempotency_key("layout-notice-test")["reply_markup_json"], item["reply_markup_json"])
        self.assertEqual(len(self.prompt()["reply_markup"]["inline_keyboard"][0]), 2)
        legacy = {key: value for key, value in markup.items() if key != "_customer_layout"}
        self.assertEqual(self.db.queue_outbound_message("اعلان ساختگی", idempotency_key="layout-notice-test",
                                                       recipient_user_id=self.actor_user()["id"], reply_markup=legacy)["id"], item["id"])
        changed = copy.deepcopy(legacy)
        changed["inline_keyboard"][0][0]["callback_data"] = "checkout:1000"
        with self.assertRaises(ConflictError):
            self.db.queue_outbound_message("اعلان ساختگی", idempotency_key="layout-notice-test",
                                            recipient_user_id=self.actor_user()["id"], reply_markup=changed)

    def test_two_concurrent_publications_allow_exactly_one_winner(self):
        self.open_section("main")
        admin = next(row for row in self.db.list_admins(active_only=True) if row["chat_id"] == self.OWNER["id"])
        for update_id in (900001, 900002):
            self.db.begin_admin_update(update_id, str(update_id) * 10)

        def publish(update_id):
            try:
                return self.db.save_customer_layout("main", defaults("main"), expected_version=0, expected_base_version=0,
                                                     admin_id=admin["id"], chat_id=self.OWNER["id"], update_id=update_id)["version"]
            except ConflictError:
                return "conflict"

        with ThreadPoolExecutor(max_workers=2) as workers:
            outcomes = list(workers.map(publish, (900001, 900002)))
        self.assertCountEqual(outcomes, [1, "conflict"])
        self.assertEqual(len(self.db.get_setting("customer_layout:main")["history"]), 1)


if __name__ == "__main__":
    unittest.main()
