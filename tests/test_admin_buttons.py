"""Button-first acceptance, permissions, lossless arguments and crash boundaries."""

from __future__ import annotations

import copy
import json
import unittest
from unittest.mock import patch

from app.admin import DOCUMENTED_COMMANDS, SUPPORT_COMMANDS
from app.admin_forms import ACTIONS, Field, arguments, form_fields
from app.admin_ui import SELECTORS, ButtonInputError
from app.db import DatabaseError
from app.keyboards import MAIN_MENU_ROWS, contains_emoji, inline_main_menu_keyboard
from app.telegram import TelegramError
from tests import test_bot as bot_fixture


class AdminButtonTests(unittest.TestCase):
    OWNER = bot_fixture.BotApplicationIntegrationTests.OWNER
    setUp = bot_fixture.BotApplicationIntegrationTests.setUp
    tearDown = bot_fixture.BotApplicationIntegrationTests.tearDown
    message = bot_fixture.BotApplicationIntegrationTests.message
    callback = bot_fixture.BotApplicationIntegrationTests.callback
    _take_update_id = bot_fixture.BotApplicationIntegrationTests._take_update_id
    send_message = bot_fixture.BotApplicationIntegrationTests.send_message
    send_callback = bot_fixture.BotApplicationIntegrationTests.send_callback

    def actor_user(self, actor=None):
        actor = actor or self.OWNER
        return self.db.get_user_by_chat_id(actor["id"])

    def state(self, actor=None):
        user = self.actor_user(actor)
        result = self.db.get_user_state(user["id"]) if user else None
        return result["data"] if result and result["state"] == "admin:ui" else None

    def begin(self, action, actor=None):
        self.send_callback(actor or self.OWNER, "adm:ui:a:" + action)
        return self.state(actor)

    def form_data(self, operation, value="0", actor=None):
        state = self.state(actor)
        return f"adm:ui:f:{state['token']}:{state['revision']}:{operation}:{value}"

    def click(self, operation, value="0", actor=None):
        self.send_callback(actor or self.OWNER, self.form_data(operation, value, actor))

    def pick(self, value, actor=None):
        state = self.state(actor)
        options = state["options"]
        selected = next((i for i, pair in enumerate(options) if pair[0] == str(value)), None)
        self.assertIsNotNone(selected, (state["action"], options, value))
        self.click("pick", str(selected), actor)

    def fill(self, action, values, actor=None):
        actor = actor or self.OWNER
        self.begin(action, actor)
        for _ in range(30):
            state = self.state(actor)
            if not state or state["status"] != "editing":
                return state
            field = self.app.admin_controller.button_ui.current_field(state)
            if field.key not in values and field.default is not None:
                self.click("default", actor=actor)
            elif field.kind.startswith("multi:"):
                for value in values.get(field.key, []):
                    self.pick(value, actor)
                self.click("multi", actor=actor)
            else:
                value = values.get(field.key)
                self.assertIsNotNone(value, (action, field))
                if field.kind == "choice" or field.kind.startswith("entity:"):
                    self.pick(value, actor)
                else:
                    self.send_message(actor, text=str(value))
        self.fail("form did not finish")

    def new_customer(self, chat=33001):
        return self.db.upsert_user(chat, chat, username=f"customer_{chat}", first_name="مشتری")

    def test_every_documented_operation_has_a_role_filtered_button(self):
        ui = self.app.admin_controller.button_ui
        self.assertEqual({a.command for a in ACTIONS.values()}, DOCUMENTED_COMMANDS)
        self.assertEqual({a.command for a in ACTIONS.values() if ui.allowed(a, "support")}, SUPPORT_COMMANDS)
        for role in ("owner", "admin", "support"):
            self.assertEqual(ui.allowed(ACTIONS["backup"], role), role == "owner")
            self.assertEqual(ui.allowed(ACTIONS["crypto_resolve"], role), role == "owner")
        self.send_callback(self.OWNER, "adm:ui:home")
        self.assertIn("پنل مدیریت", self.telegram.messages[-1]["text"])
        self.assertIn("نیازی به تایپ فرمان نیست", self.telegram.messages[-1]["text"])

    def test_customer_menu_is_unchanged_admin_entry_is_private_and_commands_are_optional(self):
        ordinary = inline_main_menu_keyboard()
        self.assertEqual([[b["text"] for b in row] for row in ordinary["inline_keyboard"]],
                         [list(row) for row in MAIN_MENU_ROWS])
        self.send_message(self.OWNER, text="/start")
        self.assertTrue(any(b.get("callback_data") == "adm:ui:home" for row in
                            self.telegram.messages[-1]["reply_markup"]["inline_keyboard"] for b in row))
        commands = next(c["params"]["commands"] for c in self.telegram.calls if c["method"] == "setMyCommands")
        self.assertEqual([c["command"] for c in commands], ["start"])
        self.send_message(self.OWNER, text="پنل مدیریت")
        self.assertIn("پنل مدیریت", self.telegram.messages[-1]["text"])

    def test_wallet_form_requires_confirmation_and_rejects_double_click_and_new_update(self):
        target = self.new_customer()
        self.fill("wallet_adjust", {"target": target["chat_id"], "amount": "۵۰۰۰۰", "note": "اصلاح با دکمه"})
        self.assertEqual(self.db.wallet_balance(target["id"]), 0)
        update = self.callback(self.OWNER, self.form_data("confirm"))
        self.app.process_update(update)
        self.assertEqual(self.db.wallet_balance(target["id"]), 50000)
        self.app.process_update(copy.deepcopy(update))
        self.send_callback(self.OWNER, update["callback_query"]["data"])
        self.assertEqual(self.db.wallet_balance(target["id"]), 50000)
        # A second intentionally confirmed form must not reuse keyboard message_id.
        self.fill("wallet_adjust", {"target": target["chat_id"], "amount": "1000", "note": "اصلاح دوم"})
        self.click("confirm")
        self.assertEqual(self.db.wallet_balance(target["id"]), 51000)

    def test_wallet_crash_after_commit_resumes_same_confirmation_once(self):
        target = self.new_customer()
        self.fill("wallet_adjust", {"target": target["chat_id"], "amount": "500", "note": "بازیابی"})
        update = self.callback(self.OWNER, self.form_data("confirm"))
        original = self.db.adjust_wallet
        calls = 0

        def fail_after(*args, **kwargs):
            nonlocal calls
            result = original(*args, **kwargs)
            calls += 1
            if calls == 1:
                raise DatabaseError("simulated crash after wallet commit")
            return result

        with patch.object(self.db, "adjust_wallet", side_effect=fail_after):
            self.assertIs(self.app.process_update_safe(copy.deepcopy(update)), False)
            self.assertEqual(self.state()["status"], "executing")
            self.assertEqual(self.db.wallet_balance(target["id"]), 500)
            self.assertIsNot(self.app.process_update_safe(copy.deepcopy(update)), False)
        self.assertEqual(self.db.wallet_balance(target["id"]), 500)
        self.assertEqual(self.state()["status"], "done")

    def test_text_step_replay_after_ui_state_commit_does_not_advance_twice(self):
        self.begin("set_card")
        update = self.message(self.OWNER, text="6037997512345678")
        original = self.db.complete_admin_update
        with patch.object(self.db, "complete_admin_update", side_effect=DatabaseError("journal outage")):
            self.assertIs(self.app.process_update_safe(copy.deepcopy(update)), False)
        self.assertEqual(self.state()["step"], 1)
        with patch.object(self.db, "complete_admin_update", wraps=original):
            self.assertIsNot(self.app.process_update_safe(copy.deepcopy(update)), False)
        self.assertEqual(self.state()["step"], 1)
        self.assertNotIn("holder", self.state()["values"])

    def test_back_cancel_and_old_form_buttons_never_mutate(self):
        self.begin("set_card")
        stale = self.form_data("back")
        self.send_message(self.OWNER, text="6037997512345678")
        self.click("back")
        self.assertEqual(self.state()["values"], {})
        self.send_callback(self.OWNER, "adm:ui:g:settings")
        self.assertIsNone(self.state())
        self.send_callback(self.OWNER, stale)
        self.assertIsNone(self.state())
        self.assertNotEqual(self.db.get_setting("card_number"), "new-value")

    def test_revoked_role_and_forged_owner_action_are_rejected(self):
        target = self.new_customer()
        actor = {"id": 9100, "username": "button_admin", "first_name": "مدیر"}
        self.db.upsert_user(actor["id"], actor["id"], username=actor["username"])
        admin = self.db.add_admin(actor["username"], actor["id"], role="admin")
        self.fill("wallet_adjust", {"target": target["chat_id"], "amount": "700", "note": "آزمون"}, actor)
        confirmation = self.form_data("confirm", actor=actor)
        self.db.add_admin(actor["username"], actor["id"], role="support")
        self.send_callback(actor, confirmation)
        self.assertEqual(self.db.wallet_balance(target["id"]), 0)
        self.send_callback(actor, "adm:ui:a:backup")
        self.assertEqual(self.telegram.documents, [])
        owner = next(a for a in self.db.list_admins() if a["role"] == "owner")
        event = self.callback(actor, "adm:ui:home")["callback_query"]
        with self.assertRaises(ButtonInputError):
            self.app.admin_controller.button_ui.callback("adm:ui:home", event, self.actor_user(actor), owner)
        self.db.set_admin_active(admin["id"], False)
        self.assertFalse(self.db.is_admin(chat_id=actor["id"]))

    def test_pipes_and_html_are_lossless_data_in_category_and_message(self):
        title = "دسته | نمونه <متن>"
        self.fill("category_add", {"name": title, "description": "شرح | کامل"})
        self.click("confirm")
        category = next(c for c in self.db.list_categories(active_only=False) if c["name"] == title)
        self.assertEqual(category["description"], "شرح | کامل")
        target = self.new_customer()
        body = "html:<b>متن | کامل</b>"
        self.fill("message", {"target": target["chat_id"], "body": body})
        self.click("confirm")
        self.assertTrue(any(m["chat_id"] == target["chat_id"] and m["text"] == "<b>متن | کامل</b>"
                            for m in self.telegram.messages))

    def test_inventory_secret_is_not_echoed_and_confirmation_replay_is_safe(self):
        secret = "name|unchanged\npass: ۰۱۲۳ <unsafe>"
        self.fill("inventory_add", {"target": self.product["id"], "secret": secret})
        self.assertNotIn(secret, json.dumps(self.telegram.messages, ensure_ascii=False))
        update = self.callback(self.OWNER, self.form_data("confirm"))
        self.app.process_update(update)
        self.app.process_update(copy.deepcopy(update))
        self.assertEqual(self.db.inventory_count(self.product["id"]), 2)
        self.assertNotIn("secret", self.state()["values"])
        self.assertNotIn(secret, json.dumps(self.telegram.messages, ensure_ascii=False))

    def test_all_selector_queries_work_without_secrets_and_page_beyond_twenty(self):
        ui = self.app.admin_controller.button_ui
        self.send_message(self.OWNER, text="/start")
        admin = next(a for a in self.db.list_admins() if a["role"] == "owner")
        for kind in SELECTORS:
            field = Field("target", "انتخاب", "entity:" + kind)
            state = {"values": {"target": "missing-ticket"}, "page": 1, "search": ""}
            ui.options(field, state, admin)
            self.assertGreaterEqual(state["option_pages"], 1)
        for index in range(26):
            self.new_customer(35000 + index)
        self.begin("user")
        self.assertGreater(self.state()["option_total"], 20)
        first = {pair[0] for pair in self.state()["options"]}
        self.click("next", "2")
        self.assertFalse(first.intersection(pair[0] for pair in self.state()["options"]))
        self.send_message(self.OWNER, text="customer_35003")
        self.assertEqual([p[0] for p in self.state()["options"]], ["35003"])
        self.pick("35003")
        self.assertEqual(self.state()["status"], "done")

    def test_list_pagination_keeps_filters_and_exact_totals(self):
        for index in range(27):
            self.new_customer(36000 + index)
        self.fill("users", {"mode": "all"})
        self.assertEqual(self.state()["status"], "done")
        self.assertEqual(self.state()["list_pages"], 2)
        self.click("list", "2")
        self.assertTrue(any("صفحه 2 از 2" in m["text"] for m in self.telegram.messages))
        self.assertEqual(self.state()["values"], {"mode": "all"})

    def test_discount_and_combined_reward_can_be_created_without_command_or_json(self):
        self.fill("discount_add", {"code": "BUTTON10", "type": "percent", "amount": "10"})
        self.click("confirm")
        self.assertTrue(any(d["code"] == "BUTTON10" for d in self.db.list_discounts()))
        self.fill("reward_add", {"event": "combined", "amount": "100", "first_purchase": "true",
                                 "minimum_successful_purchases": "1", "product_ids": [self.product["id"]]})
        self.click("confirm")
        rule = self.db.list_reward_rules()[-1]
        self.assertEqual(rule["event_type"], "combined")
        conditions = json.loads(rule["conditions_json"])
        self.assertEqual(conditions["product_ids"], [self.product["id"]])
        self.assertTrue(conditions["first_purchase"])

    def test_product_enum_fields_are_buttons_and_input_validation_is_local(self):
        self.fill("product_set", {"target": self.product["id"], "field": "renewable", "value": "true"})
        self.click("confirm")
        self.assertTrue(self.db.get_product(self.product["id"])["is_renewable"])
        self.begin("set_card")
        self.send_message(self.OWNER, text="bad-card")
        self.assertEqual(self.state()["step"], 0)
        self.send_message(self.OWNER, text="۶۰۳۷۹۹۷۵۱۲۳۴۵۶۷۸")
        self.assertEqual(self.state()["values"]["number"], "6037997512345678")

    def test_report_argument_branches_and_combined_products_are_structured(self):
        for mode, expected in (("orders", "orders all"), ("joined", "users joined"),
                               ("product", "users product all"), ("finance", "finance")):
            data = {"kind": mode, "status": "all", "target": "all", "start": "2026-09-01", "end": "2026-09-05"}
            rest, parts = arguments(ACTIONS["report"], data)
            self.assertEqual(rest, expected + " 2026-09-01 2026-09-05")
            self.assertIsNone(parts)
        for mode in ("all", "active", "blocked", "new", "inactive", "joined", "product"):
            data = {"mode": mode, "days": "7", "target": "1", "start": "2026-09-01", "end": "2026-09-05"}
            rest, _ = arguments(ACTIONS["users"], data, page=2)
            self.assertTrue(rest.startswith(mode + " "))
            self.assertTrue(rest.endswith(" 2"))

    def test_button_labels_and_callbacks_remain_emoji_free_and_within_api_limit(self):
        for key in ACTIONS:
            self.begin(key)
        for message in self.telegram.messages:
            for row in (message.get("reply_markup") or {}).get("inline_keyboard", []):
                for button in row:
                    self.assertFalse(contains_emoji(button["text"]), button)
                    self.assertLessEqual(len(button.get("callback_data", "").encode()), 64)
        for action in ACTIONS.values():
            for field in form_fields(action, {"event": "combined", "kind": "product", "mode": "product"}):
                self.assertNotEqual(field.kind, "dynamic")

    def test_failed_admin_response_after_commit_never_repeats_wallet(self):
        target = self.new_customer()
        self.fill("wallet_adjust", {"target": target["chat_id"], "amount": "900", "note": "ارسال ناموفق"})
        update = self.callback(self.OWNER, self.form_data("confirm"))
        with patch.object(self.telegram, "send_message", side_effect=TelegramError("offline")):
            self.app.process_update(copy.deepcopy(update))
        self.assertEqual(self.state()["status"], "done")
        self.app.process_update(copy.deepcopy(update))
        self.send_callback(self.OWNER, update["callback_query"]["data"])
        self.assertEqual(self.db.wallet_balance(target["id"]), 900)

    def test_callback_spinner_failure_does_not_drop_a_valid_confirmation(self):
        target = self.new_customer()
        self.fill("wallet_adjust", {"target": target["chat_id"], "amount": "600", "note": "spinner outage"})
        update = self.callback(self.OWNER, self.form_data("confirm"))
        with patch.object(self.telegram, "answer_callback_query", side_effect=TelegramError("expired spinner")):
            self.app.process_update(update)
        self.assertEqual(self.db.wallet_balance(target["id"]), 600)
        self.assertEqual(self.state()["status"], "done")

    def test_customer_text_and_contact_inputs_have_button_cancellation(self):
        actor = {"id": 38001, "username": "customer_buttons", "first_name": "مشتری"}
        self.send_message(actor, text="/start")
        for data, expected in (("wallet:topup", "wallet_topup_amount"), ("ticket:new", "ticket_subject"),
                               (f"buy:{self.product['id']}", "purchase_name")):
            self.send_callback(actor, data)
            state = self.db.get_user_state(self.actor_user(actor)["id"])
            self.assertEqual(state["state"], expected)
            buttons = self.telegram.messages[-1]["reply_markup"]["keyboard"]
            self.assertEqual([[b["text"] for b in row] for row in buttons], [["لغو و بازگشت"]])
            self.send_message(actor, text="لغو و بازگشت")
            self.assertIsNone(self.db.get_user_state(self.actor_user(actor)["id"]))
        self.send_callback(actor, f"buy:{self.product['id']}")
        self.send_message(actor, text="نام مشتری")
        self.assertEqual(self.telegram.messages[-1]["reply_markup"]["keyboard"][-1][0]["text"], "لغو و بازگشت")
        self.send_message(actor, text="لغو و بازگشت")
        self.assertIsNone(self.db.get_user_state(self.actor_user(actor)["id"]))

    def test_faq_uses_selected_category_and_all_catalog_lists_keep_their_filter(self):
        category = self.db.create_faq_category("پرسش‌های اصلی")
        self.fill("faq_add", {"target": category["id"], "question": "سؤال | نمونه", "answer": "پاسخ | کامل"})
        self.click("confirm")
        item = self.db.list_faqs(category_id=category["id"])[0]
        self.assertEqual(item["question"], "سؤال | نمونه")
        self.assertEqual(item["answer"], "پاسخ | کامل")
        self.assertEqual(len(self.db.list_faq_categories()), 1)
        for key in ("products", "faqs"):
            self.fill(key, {})
            self.assertEqual(self.state()["values"]["target"], "all")
            self.assertEqual(self.state()["status"], "done")

    def test_last_text_commit_crash_resumes_confirmation_without_mutation(self):
        target = self.new_customer()
        self.begin("message")
        self.pick(target["chat_id"])
        update = self.message(self.OWNER, text="نباید پیش از تأیید فرستاده شود")
        original = self.db.set_user_state
        failed = False

        def crash_after(*args, **kwargs):
            nonlocal failed
            result = original(*args, **kwargs)
            data = args[2] if len(args) > 2 else {}
            if not failed and data.get("status") == "editing" and data.get("step") == 2:
                failed = True
                raise DatabaseError("crash after final value was saved")
            return result

        with patch.object(self.db, "set_user_state", side_effect=crash_after):
            self.assertIs(self.app.process_update_safe(copy.deepcopy(update)), False)
            self.assertIsNot(self.app.process_update_safe(copy.deepcopy(update)), False)
        self.assertEqual(self.state()["status"], "confirm")
        self.assertFalse(any(m["chat_id"] == target["chat_id"] for m in self.telegram.messages))
        self.click("confirm")
        self.assertTrue(any(m["chat_id"] == target["chat_id"] for m in self.telegram.messages))

    def test_toggle_crash_does_not_invert_the_same_product_twice(self):
        self.fill("product_toggle", {"target": self.product["id"], "field": "visible"})
        update = self.callback(self.OWNER, self.form_data("confirm"))
        original = self.db.update_product
        failed = False

        def crash_after(*args, **kwargs):
            nonlocal failed
            result = original(*args, **kwargs)
            if not failed:
                failed = True
                raise DatabaseError("crash after product toggle")
            return result

        with patch.object(self.db, "update_product", side_effect=crash_after):
            self.assertIs(self.app.process_update_safe(copy.deepcopy(update)), False)
            self.assertIsNot(self.app.process_update_safe(copy.deepcopy(update)), False)
        self.assertFalse(self.db.get_product(self.product["id"])["is_visible"])

    def test_receipt_approval_and_support_reply_use_existing_safe_workflows(self):
        target = self.new_customer()
        order = self.db.create_order(target["id"], self.product["id"])
        payment = self.db.create_order_payment(order["id"], "card", idempotency_key="ui-receipt")
        self.db.submit_payment_receipt(payment["id"], "test-photo-id", file_kind="photo")
        self.fill("payment_detail", {"target": payment["payment_number"]})
        self.assertTrue(self.telegram.photos)
        self.fill("approve_payment", {"target": payment["payment_number"]})
        self.assertEqual(self.db.get_payment(payment["id"])["status"], "verifying")
        update = self.callback(self.OWNER, self.form_data("confirm"))
        self.app.process_update(update)
        self.app.process_update(copy.deepcopy(update))
        self.assertEqual(self.db.get_payment(payment["id"])["status"], "paid")
        actor = {"id": 9400, "username": "button_support", "first_name": "پشتیبان"}
        self.db.upsert_user(actor["id"], actor["id"], username=actor["username"])
        self.db.add_admin(actor["username"], actor["id"], role="support")
        ticket = self.db.create_ticket(target["id"], "آزمون تیکت", "متن درخواست", idempotency_key="ui-ticket")
        self.fill("ticket_reply", {"target": ticket["ticket_number"], "body": "پاسخ | کامل"}, actor)
        self.click("confirm", actor=actor)
        messages = self.db.list_ticket_messages(ticket["id"])
        self.assertEqual(messages[-1]["body"], "پاسخ | کامل")

    def test_manual_completion_offers_product_default_and_still_requires_confirmation(self):
        target = self.new_customer()
        product = self.db.create_product(self.category["id"], "اشتراک دستی دکمه‌ای",
                                         product_type="manual", price_amount=100,
                                         completion_text="فعال‌سازی با موفقیت انجام شد.")
        self.db.credit_wallet(target["id"], 100, reason="test funds", idempotency_key="manual-ui-funds")
        order = self.db.create_order(target["id"], product["id"])
        self.db.hold_wallet_funds(order["id"], idempotency_key="manual-ui-payment")
        self.db.update_order_status(order["id"], "awaiting_info")
        self.db.submit_manual_order_info(order["id"], target["id"], {"text": "اطلاعات آزمایشی مشتری"})
        self.begin("complete")
        self.pick(order["order_number"])
        self.assertEqual(self.state()["options"][0][1], "استفاده از متن تکمیل محصول")
        self.click("pick", "0")
        self.assertNotEqual(self.db.get_order(order["id"])["status"], "completed")
        self.click("confirm")
        self.assertEqual(self.db.get_order(order["id"])["status"], "completed")
        self.assertTrue(any(m["chat_id"] == target["chat_id"] and product["completion_text"] in m["text"]
                            for m in self.telegram.messages))

    def test_broadcast_buttons_preserve_counted_preview_and_single_confirmation(self):
        self.new_customer()
        self.fill("broadcast_all", {"body": "پیام | گروهی تست"})
        state = self.db.get_user_state(self.actor_user()["id"])
        self.assertEqual(state["state"], "admin:broadcast")
        self.assertGreater(state["data"]["target_count"], 0)
        confirmation = f"adm:broadcast:confirm:{state['data']['token']}"
        update = self.callback(self.OWNER, confirmation)
        self.app.process_update(update)
        self.app.process_update(copy.deepcopy(update))
        self.send_callback(self.OWNER, confirmation)
        rows = self.app.admin_controller._query("SELECT * FROM broadcast_batches")
        self.assertEqual(len(rows), 1)

    def test_broadcast_preview_recovers_after_state_commit_before_first_render(self):
        self.begin("broadcast_all")
        update = self.message(self.OWNER, text="متن پیش‌نمایش قابل بازیابی")
        original = self.db.set_user_state
        failed = False

        def crash_after(*args, **kwargs):
            nonlocal failed
            result = original(*args, **kwargs)
            if args[1] == "admin:broadcast" and not failed:
                failed = True
                raise DatabaseError("crash after preview state commit")
            return result

        with patch.object(self.db, "set_user_state", side_effect=crash_after):
            self.assertIs(self.app.process_update_safe(copy.deepcopy(update)), False)
            state = self.db.get_user_state(self.actor_user()["id"])
            token = state["data"]["token"]
            self.assertIsNot(self.app.process_update_safe(copy.deepcopy(update)), False)
        preview = self.telegram.messages[-1]
        self.assertIn("پیش‌نمایش ارسال گروهی", preview["text"])
        self.assertEqual(preview["reply_markup"]["inline_keyboard"][0][0]["callback_data"],
                         "adm:broadcast:confirm:" + token)
        self.assertEqual(self.app.admin_controller._query("SELECT * FROM broadcast_batches"), [])

    def test_all_action_forms_route_to_the_registered_handler_without_command_typing(self):
        controller = self.app.admin_controller
        ui = controller.button_ui
        captured = []

        def handler(key):
            def capture(rest, message, user, admin):
                captured.append((key, rest, controller._button_context["parts"]))
            return capture

        original_options = ui.options

        def options(field, state, admin):
            if state.get("action") == "complete" and field.key == "delivery":
                return [("متن ساختگی تکمیل محصول", "متن پیش‌فرض")]
            result = original_options(field, state, admin)
            if field.kind.startswith("entity:") and not result:
                state.update(option_pages=1, option_total=1)
                kind = field.kind.split(":")[1]
                value = {"order": "ORD-DEMO", "manual_order": "ORD-DEMO", "receipt": "PAY-DEMO",
                         "payment": "PAY-DEMO", "ticket": "TKT-DEMO", "discount": "DEMO"}.get(kind, "1")
                return [(value, "مورد ساختگی تست")]
            return result

        mappings = {a.command: handler(a.key) for a in ACTIONS.values()}
        with patch.dict(controller._handlers, mappings), patch.object(ui, "options", side_effect=options):
            for key, action in ACTIONS.items():
                with self.subTest(action=key):
                    if key in {"admin_help", "inventory_add", "inventory_edit"}:
                        continue  # Real workflows are tested separately.
                    before = len(captured)
                    self.begin(key)
                    for _ in range(25):
                        state = self.state()
                        if state["status"] != "editing":
                            break
                        field = ui.current_field(state)
                        if field.default is not None:
                            self.click("default")
                        elif state.get("options"):
                            self.click("pick", "0")
                        else:
                            value = {"card": "6037997512345678", "username": "sample_admin", "positive": "5",
                                     "integer": "0", "nonnegative": "0", "signed": "20", "word": "SAMPLE",
                                     "url": "https://t.me/example_channel"}.get(field.kind, "متن | نمونه")
                            self.send_message(self.OWNER, text=value)
                    if self.state()["status"] == "confirm":
                        self.click("confirm")
                    self.assertEqual(len(captured), before + 1, self.state())
                    self.assertEqual(captured[-1][0], key)
                    if not key.startswith("broadcast_"):
                        self.assertEqual(self.state()["status"], "done")
                    # Broadcast handlers are mocked here; the real preview's
                    # counted state transition has its own integration test.


if __name__ == "__main__":
    unittest.main()
