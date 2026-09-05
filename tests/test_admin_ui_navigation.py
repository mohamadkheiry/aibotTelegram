"""Exercise the actual keyboards sent to Telegram, never invented form payloads."""

from __future__ import annotations

import copy
import json
import unittest
from unittest.mock import patch

from app.admin import _PRODUCT_FIELDS
from app.admin_forms import ACTIONS, GROUPS, GROUP_PARENTS, PRODUCT_FIELDS
from app.bot import BotApplication
from app.db import DatabaseError
from app.telegram import TelegramError
from tests import test_admin_buttons as fixture


class AdminNavigationTests(unittest.TestCase):
    OWNER = fixture.AdminButtonTests.OWNER
    setUp = fixture.AdminButtonTests.setUp
    tearDown = fixture.AdminButtonTests.tearDown
    message = fixture.AdminButtonTests.message
    callback = fixture.AdminButtonTests.callback
    _take_update_id = fixture.AdminButtonTests._take_update_id
    send_message = fixture.AdminButtonTests.send_message
    send_callback = fixture.AdminButtonTests.send_callback
    actor_user = fixture.AdminButtonTests.actor_user
    state = fixture.AdminButtonTests.state
    begin = fixture.AdminButtonTests.begin

    def prompt(self):
        return next(m for m in reversed(self.telegram.messages)
                    if m["chat_id"] == self.OWNER["id"] and (m.get("reply_markup") or {}).get("inline_keyboard"))

    def button_update(self, *, label=None, ending=None, prompt=None):
        prompt = prompt or self.prompt()
        choices = [b for row in prompt["reply_markup"]["inline_keyboard"] for b in row
                   if (label is not None and b["text"] == label
                       or ending is not None and b.get("callback_data", "").endswith(ending))]
        self.assertEqual(len(choices), 1, (label, ending, prompt))
        update = self.callback(self.OWNER, choices[0]["callback_data"])
        update["callback_query"]["message"] = {
            "message_id": prompt["message_id"], "text": prompt["text"],
            "chat": {"id": self.OWNER["id"], "type": "private"},
            "reply_markup": copy.deepcopy(prompt["reply_markup"]),
        }
        return update

    def click(self, **kwargs):
        update = self.button_update(**kwargs)
        self.assertIsNot(self.app.process_update_safe(update), False)
        return update

    def assert_retired(self, prompt):
        current = next(m for m in self.telegram.messages if m["message_id"] == prompt["message_id"])
        self.assertFalse((current.get("reply_markup") or {}).get("inline_keyboard"))

    def test_empty_faq_all_button_finishes_and_retires_consumed_keyboard(self):
        self.begin("faqs")
        original = copy.deepcopy(self.prompt())
        self.click(label="همه / بدون محدودیت")
        self.assertEqual(self.state()["status"], "done")
        self.assertTrue(any("سوالی ثبت نشده است" in m["text"] for m in self.telegram.messages))
        self.assert_retired(original)

    def test_repeated_faq_all_click_offers_safe_result_refresh(self):
        self.begin("faqs")
        original = copy.deepcopy(self.prompt())
        self.click(label="همه / بدون محدودیت")
        count = len(self.telegram.messages)
        self.click(label="همه / بدون محدودیت", prompt=original)
        self.assertFalse(any("این دکمه قدیمی است" in m["text"] for m in self.telegram.messages[count:]))
        self.click(label="نمایش دوباره نتیجه")
        self.assertEqual(self.state()["values"]["target"], "all")
        self.assertEqual(self.state()["status"], "done")

    def test_stale_previous_step_restores_current_step_without_accepting_old_value(self):
        self.begin("orders")
        original = copy.deepcopy(self.prompt())
        self.click(label="همه وضعیت‌ها")
        before = self.state()["values"]
        self.click(label="تکمیل‌شده", prompt=original)
        self.assertEqual(self.state()["values"], before)
        self.assertEqual(self.state()["step"], 1)
        self.click(label="همه تاریخ‌ها")
        self.assertEqual(self.state()["status"], "done")

    def test_republished_selector_cannot_remap_an_old_button_to_another_entity(self):
        self.begin("product_set")
        original = copy.deepcopy(self.prompt())
        old_revision = self.state()["revision"]
        self.db.create_product(self.category["id"], "محصول تازه", product_type="ready", price_amount=200)
        ui = self.app.admin_controller.button_ui
        admin = self.db.list_admins(active_only=True)[0]
        ui.render(self.state(), self.actor_user(), admin)
        self.assertGreater(self.state()["revision"], old_revision)
        self.click(ending=":pick:0", prompt=original)
        self.assertEqual(self.state()["step"], 0)
        self.assertEqual(self.state()["values"], {})
        self.click(ending=":pick:0")
        self.assertEqual(self.state()["step"], 1)

    def test_cancel_and_new_form_retire_old_prompt_without_mutation(self):
        self.begin("bot_off")
        original = copy.deepcopy(self.prompt())
        self.click(label="لغو و بازگشت")
        self.assertTrue(self.db.get_setting("bot_enabled"))
        self.assert_retired(original)
        self.begin("set_card")
        another = copy.deepcopy(self.prompt())
        self.begin("faqs")
        self.assert_retired(another)

    def test_old_form_callback_preserves_new_form_and_recovers_its_buttons(self):
        self.begin("bot_off")
        original = copy.deepcopy(self.prompt())
        self.begin("orders")
        self.click(label="تأیید و اجرا", prompt=original)
        self.assertEqual(self.state()["action"], "orders")
        self.assertTrue(self.db.get_setting("bot_enabled"))
        self.click(label="همه وضعیت‌ها")
        self.assertEqual(self.state()["step"], 1)

    def test_keyboard_cleanup_failure_does_not_replay_a_finished_operation(self):
        self.begin("bot_off")
        update = self.button_update(label="تأیید و اجرا")
        with patch.object(self.telegram, "edit_message_reply_markup", side_effect=TelegramError("uneditable")):
            self.assertIsNot(self.app.process_update_safe(update), False)
        self.assertFalse(self.db.get_setting("bot_enabled"))
        self.assertEqual(self.state()["status"], "done")

    def test_crash_after_sending_new_prompt_recovers_with_clickable_latest_buttons(self):
        self.begin("orders")
        update = self.button_update(label="همه وضعیت‌ها")
        previous_id = self.prompt()["message_id"]
        original = self.db.set_user_state
        failed = False

        def crash_after(*args, **kwargs):
            nonlocal failed
            result = original(*args, **kwargs)
            if not failed and args[2].get("step") == 1 and args[2].get("prompt_message_id", 0) > previous_id:
                failed = True
                raise DatabaseError("simulated prompt persistence failure")
            return result

        with patch.object(self.db, "set_user_state", side_effect=crash_after):
            self.assertIs(self.app.process_update_safe(copy.deepcopy(update)), False)
            self.assertIsNot(self.app.process_update_safe(copy.deepcopy(update)), False)
        self.assertTrue(failed)
        self.click(label="همه تاریخ‌ها")
        self.assertEqual(self.state()["status"], "done")

    def test_noncatalog_actions_are_reachable_through_nested_emitted_buttons(self):
        # Catalog/inventory and force-join now use entity-centred views, covered
        # by test_admin_catalog_hierarchy and test_admin_joins. All 83 handlers retain the
        # independent routing test in test_admin_buttons.
        visited = set()
        joins = {"joins", "join_add", "join_toggle", "join_delete"}
        for group, title in GROUPS.items():
            if group in {"catalog", "inventory"}:
                continue
            self.send_callback(self.OWNER, "adm:ui:home")
            if group in GROUP_PARENTS:
                self.click(label=GROUPS[GROUP_PARENTS[group]])
            self.click(label=title)
            actions = copy.deepcopy(self.prompt())
            for action in (a for a in ACTIONS.values() if a.group == group and a.key not in joins):
                with self.subTest(action=action.key):
                    self.click(label=action.label, prompt=actions)
                    self.assertEqual(self.state()["action"], action.key)
                    self.assertIn(self.state()["status"], {"editing", "confirm", "done"})
                    visited.add(action.key)
        self.assertEqual(visited, {a.key for a in ACTIONS.values() if a.group not in {"catalog", "inventory"} and a.key not in joins})

    def test_start_and_text_cancel_retire_form_buttons_and_preserve_customer_menu(self):
        for text in ("/start", "لغو و بازگشت"):
            with self.subTest(text=text):
                self.begin("bot_off")
                original = copy.deepcopy(self.prompt())
                self.send_message(self.OWNER, text=text)
                self.assert_retired(original)
                self.assertIsNone(self.state())
                self.assertTrue(self.db.get_setting("bot_enabled"))
                self.click(label="پنل مدیریت")

    def test_old_button_after_cancel_does_not_reopen_or_execute_the_closed_form(self):
        self.begin("bot_off")
        original = copy.deepcopy(self.prompt())
        self.click(label="لغو و بازگشت")
        self.click(label="تأیید و اجرا", prompt=original)
        self.assertIsNone(self.state())
        self.assertTrue(self.db.get_setting("bot_enabled"))
        self.click(label="پنل مدیریت")

    def test_existing_release_state_without_prompt_id_recovers_and_retires_legacy_button(self):
        self.begin("faqs")
        original = copy.deepcopy(self.prompt())
        self.click(label="همه / بدون محدودیت")
        state = self.state()
        state.pop("prompt_message_id", None)
        self.db.set_user_state(self.actor_user()["id"], "admin:ui", state)
        self.click(label="همه / بدون محدودیت", prompt=original)
        self.assert_retired(original)
        self.click(label="نمایش دوباره نتیجه")
        self.assertEqual(self.state()["status"], "done")

    def test_current_form_buttons_survive_application_restart(self):
        self.begin("faqs")
        original = copy.deepcopy(self.prompt())
        self.app = BotApplication(self.settings, self.db, self.telegram)
        self.app.initialize()
        self.click(label="همه / بدون محدودیت", prompt=original)
        self.assertEqual(self.state()["status"], "done")
        self.assert_retired(original)

    def test_selector_search_pages_and_stale_search_results_never_select_wrong_user(self):
        for index in range(24):
            chat = 44000 + index
            self.db.upsert_user(chat, chat, username=f"navigation_{index}", first_name="آزمایشی")
        self.begin("user")
        original = copy.deepcopy(self.prompt())
        self.click(label="بعدی")
        self.assertEqual(self.state()["page"], 2)
        self.click(label="قبلی")
        self.assertEqual(self.state()["page"], 1)
        self.send_message(self.OWNER, text="@navigation_7")
        self.assertEqual(len(self.state()["options"]), 1)
        self.click(ending=":pick:0", prompt=original)
        self.assertEqual(self.state()["values"], {})
        self.assertEqual(self.state()["search"], "navigation_7")
        self.click(ending=":pick:0")
        self.assertEqual(self.state()["values"]["target"], "44007")
        self.assertEqual(self.state()["status"], "done")

    def test_list_pagination_and_refresh_preserve_filter_and_allow_back(self):
        selected = self.db.create_faq_category("دسته انتخابی")
        other = self.db.create_faq_category("دسته دیگر")
        for index in range(24):
            self.db.create_faq(f"پرسش انتخابی {index}", "پاسخ", category_id=selected["id"])
        self.db.create_faq("نباید در نتیجه باشد", "پاسخ", category_id=other["id"])
        self.begin("faqs")
        self.click(label=f"دسته انتخابی · {selected['id']}")
        old_result = copy.deepcopy(self.prompt())
        self.click(label="صفحه بعد")
        self.assertEqual(self.state()["result_page"], 2)
        self.click(label="نمایش دوباره نتیجه", prompt=old_result)
        self.assertEqual(self.state()["result_page"], 2)
        start = len(self.telegram.messages)
        self.click(label="نمایش دوباره نتیجه")
        self.assertEqual(self.state()["values"]["target"], str(selected["id"]))
        self.assertFalse(any("نباید در نتیجه باشد" in m["text"] for m in self.telegram.messages[start:]))
        self.click(label="صفحه قبل")
        self.assertEqual(self.state()["result_page"], 1)

    def test_real_wallet_confirmation_double_click_and_failed_delivery_never_double_credit(self):
        target = self.db.upsert_user(45000, 45000, username="wallet_navigation", first_name="کیف تست")
        self.begin("wallet_adjust")
        self.click(label="کیف تست @wallet_navigation · 45000")
        self.send_message(self.OWNER, text="500")
        self.send_message(self.OWNER, text="اصلاح آزمایشی")
        original = copy.deepcopy(self.prompt())
        update = self.button_update(label="تأیید و اجرا")
        with patch.object(self.telegram, "send_message", side_effect=TelegramError("offline")):
            self.assertIsNot(self.app.process_update_safe(update), False)
        self.click(label="تأیید و اجرا", prompt=original)
        self.assertEqual(self.db.wallet_balance(target["id"]), 500)
        self.assertEqual(self.state()["status"], "done")
        self.assert_retired(original)

    def test_revoked_role_cannot_use_stale_recovery_to_reveal_or_confirm_a_form(self):
        actor = {"id": 46000, "username": "navigation_admin", "first_name": "مدیر تست"}
        self.db.upsert_user(actor["id"], actor["id"], username=actor["username"])
        self.db.add_admin(actor["username"], actor["id"], role="admin")
        with patch.object(self, "OWNER", actor):
            self.begin("bot_off")
            original = copy.deepcopy(self.prompt())
            self.db.add_admin(actor["username"], actor["id"], role="support")
            self.click(label="تأیید و اجرا", prompt=original)
            self.assertTrue(self.db.get_setting("bot_enabled"))
            self.assertNotIn("تأیید و اجرا", str(self.prompt().get("reply_markup")))

    def test_empty_search_keeps_optional_all_action_available(self):
        self.begin("products")
        self.send_message(self.OWNER, text="هیچ نتیجه منطبقی وجود ندارد")
        self.assertEqual(self.state()["options"], [])
        self.click(label="همه / بدون محدودیت")
        self.assertEqual(self.state()["status"], "done")
        self.assertTrue(any("اکانت آماده تست" in m["text"] for m in self.telegram.messages))

    def test_invalid_text_keeps_form_usable_without_accepting_command_syntax(self):
        self.begin("set_card")
        self.send_message(self.OWNER, text="متن نامعتبر")
        self.assertEqual(self.state()["step"], 0)
        self.send_message(self.OWNER, text="۶۰۳۷۹۹۷۵۱۲۳۴۵۶۷۸")
        self.assertEqual(self.state()["step"], 1)
        self.click(label="مرحله قبل / اصلاح")
        self.assertEqual(self.state()["values"], {})
        self.click(label="لغو و بازگشت")
        self.assertIsNone(self.state())

    def test_all_product_edit_branches_execute_with_actual_buttons_and_lossless_values(self):
        replacements = {
            "price": ("12500", 12500), "duration": ("20 روز", "20 روز"),
            "duration_days": ("20", 20), "stock_limit": ("25", 25),
            "features": ("ویژگی یک;ویژگی دو", ["ویژگی یک", "ویژگی دو"]),
            "reminder_days": ("3,1,0", [3, 1, 0]),
            "rules_url": ("https://example.com/rules", "https://example.com/rules"),
        }
        for label, field in PRODUCT_FIELDS:
            with self.subTest(field=field):
                product = self.db.create_product(self.category["id"], "محصول شاخه آزمایش",
                                                 product_type="manual", price_amount=100)
                self.begin("product_set")
                self.send_message(self.OWNER, text=str(product["id"]))
                # Match by visible product ID, not by the DB state's callback revision.
                self.click(label=f"محصول شاخه آزمایش · {product['id']}")
                self.click(label=label)
                if field == "category":
                    expected = self.state()["options"][0][0]
                    self.click(ending=":pick:0")
                    expected = int(expected)
                elif field == "type":
                    self.click(label="آماده و خودکار")
                    expected = "ready"
                elif field == "renewable":
                    self.click(label="بله")
                    expected = 1
                else:
                    value, expected = replacements.get(field, ("مقدار | کامل", "مقدار | کامل"))
                    self.send_message(self.OWNER, text=value)
                self.assertEqual(self.state()["status"], "confirm")
                self.click(label="تأیید و اجرا")
                self.assertEqual(self.state()["status"], "done")
                stored = self.db.get_product(product["id"])
                actual = (json.loads(stored[field + "_json"]) if field in {"features", "reminder_days"}
                          else stored[_PRODUCT_FIELDS[field]])
                self.assertEqual(actual, expected)

    def test_all_report_branches_and_user_filters_complete_from_emitted_buttons(self):
        for action, key in (("report", "kind"), ("users", "mode")):
            for label, value in ACTIONS[action].fields[0].options:
                with self.subTest(action=action, choice=value):
                    self.begin(action)
                    self.click(label=label)
                    for _ in range(8):
                        state = self.state()
                        if state["status"] == "done":
                            break
                        current = self.app.admin_controller.button_ui.current_field(state)
                        if current.default is not None:
                            self.click(ending=":default:0")
                        elif current.key in {"start", "end"}:
                            self.send_message(self.OWNER, text="2026-01-01" if current.key == "start" else "2026-12-31")
                        else:
                            self.click(ending=":pick:0")
                    self.assertEqual(self.state()["status"], "done")
                    self.assertEqual(self.state()["values"][key], value)

    def test_multiselect_old_click_cannot_toggle_selection_twice(self):
        self.begin("reward_add")
        self.click(label="شرط‌های ترکیبی")
        self.send_message(self.OWNER, text="100")
        self.click(label="همه / بدون محدودیت")
        self.click(ending=":default:0")
        self.click(label="خیر")
        self.click(ending=":default:0")
        self.click(ending=":default:0")
        old = copy.deepcopy(self.prompt())
        self.click(ending=":pick:0")
        self.assertEqual(self.state()["selected"], [self.product["id"]])
        self.click(ending=":pick:0", prompt=old)
        self.assertEqual(self.state()["selected"], [self.product["id"]])
        self.click(label="پایان انتخاب محصولات")
        self.click(ending=":default:0")
        self.click(ending=":default:0")
        self.click(ending=":default:0")
        self.assertEqual(self.state()["status"], "confirm")
        self.click(label="تأیید و اجرا")
        self.assertEqual(self.state()["status"], "done")


if __name__ == "__main__":
    unittest.main()
