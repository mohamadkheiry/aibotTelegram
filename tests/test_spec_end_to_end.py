"""Cross-role acceptance journeys derived from the original PDFs and UI requests.

Only Telegram/provider boundaries are simulated. Buttons and their message IDs
come from the emitted screen; the real forms, handlers, SQLite and outbox run.
"""

from __future__ import annotations

import copy
import csv
import io
import json
import unittest
from datetime import timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from app.admin_forms import ACTIONS, GROUP_PARENTS, GROUPS, MAIN_GROUPS
from app.bot import BotApplication
from app.keyboards import callback_button, inline_keyboard
from app.utils import utc_now
from tests import test_bot as fixture


class ScreenTelegram(fixture.FakeTelegram):
    """Keep the actual visible screen after edits, not an invented callback map."""

    def __init__(self):
        super().__init__()
        self.screens = {}

    def send_message(self, chat_id, text, **kwargs):
        result = super().send_message(chat_id, text, **kwargs)
        self.screens[int(chat_id)] = copy.deepcopy(result)
        return result

    def edit_message_text(self, chat_id, message_id, text, **kwargs):
        result = super().edit_message_text(chat_id, message_id, text, **kwargs)
        self.screens[int(chat_id)] = copy.deepcopy(result)
        return result

    def edit_message_reply_markup(self, chat_id, message_id, reply_markup=None, **kwargs):
        result = super().edit_message_reply_markup(chat_id, message_id, reply_markup, **kwargs)
        screen = self.screens.get(int(chat_id))
        if screen and screen["message_id"] == message_id:
            screen["reply_markup"] = copy.deepcopy(reply_markup)
        return result


class SourceEndToEndTests(unittest.TestCase):
    OWNER = fixture.BotApplicationIntegrationTests.OWNER
    CUSTOMER = fixture.BotApplicationIntegrationTests.CUSTOMER
    tearDown = fixture.BotApplicationIntegrationTests.tearDown
    message = fixture.BotApplicationIntegrationTests.message
    callback = fixture.BotApplicationIntegrationTests.callback
    _take_update_id = fixture.BotApplicationIntegrationTests._take_update_id

    def setUp(self):
        fixture.BotApplicationIntegrationTests.setUp(self)
        self.telegram = ScreenTelegram()
        self.app = BotApplication(self.settings, self.db, self.telegram)
        self.app.initialize()
        self.send(self.OWNER, text="/start")

    def send(self, actor, **kwargs):
        update = self.message(actor, **kwargs)
        self.assertIsNot(self.app.process_update_safe(update), False)
        return update

    def screen(self, actor):
        return self.telegram.screens[actor["id"]]

    def buttons(self, actor):
        markup = self.screen(actor).get("reply_markup") or {}
        return [button for row in markup.get("inline_keyboard", markup.get("keyboard", [])) for button in row]

    def click(self, actor, label=None, *, data=None, ending=None, screen=None):
        screen = copy.deepcopy(screen or self.screen(actor))
        markup = screen.get("reply_markup") or {}
        buttons = [b for row in markup.get("inline_keyboard", markup.get("keyboard", [])) for b in row]
        found = [b for b in buttons if label is not None and b["text"] == label
                 or data is not None and b.get("callback_data") == data
                 or ending is not None and b.get("callback_data", "").endswith(ending)]
        self.assertEqual(len(found), 1, (label, data, ending, screen))
        button = found[0]
        if "callback_data" not in button:
            self.assertFalse(button.get("request_contact"), "Contact needs the sender's real identity.")
            return self.send(actor, text=button["text"])
        update = self.callback(actor, button["callback_data"])
        update["callback_query"]["message"] = {
            **screen, "chat": {"id": actor["id"], "type": "private"},
        }
        self.assertIsNot(self.app.process_update_safe(update), False)
        return update

    def state(self, actor=None):
        user = self.db.get_user_by_chat_id((actor or self.OWNER)["id"])
        state = self.db.get_user_state(user["id"])
        return state["data"] if state and state["state"] == "admin:ui" else None

    def panel(self, group):
        self.send(self.OWNER, text="/start")
        self.click(self.OWNER, "پنل مدیریت")
        groups = [b["text"] for b in self.buttons(self.OWNER) if b.get("callback_data", "").startswith("adm:ui:g:")]
        self.assertEqual(groups, [GROUPS[key] for key in MAIN_GROUPS])
        if group in GROUP_PARENTS:
            self.click(self.OWNER, GROUPS[GROUP_PARENTS[group]])
        self.click(self.OWNER, GROUPS[group])

    def fill_current(self, values):
        for _ in range(35):
            state = self.state()
            self.assertIsNotNone(state)
            if state["status"] != "editing":
                return state
            field = self.app.admin_controller.button_ui.current_field(state)
            if field.key not in values and field.default is not None:
                self.click(self.OWNER, ending=":default:0")
            elif state.get("options") and (field.kind == "choice" or field.kind.startswith("entity:")):
                index = next((i for i, option in enumerate(state["options"])
                              if option[0] == str(values[field.key])), None)
                self.assertIsNotNone(index, (field, values, state["options"]))
                self.click(self.OWNER, ending=f":pick:{index}")
            else:
                self.send(self.OWNER, text=str(values[field.key]))
        self.fail("Form did not reach a result/confirmation.")

    def action(self, key, values=None):
        action = ACTIONS[key]
        self.panel(action.group)
        self.click(self.OWNER, action.label)
        state = self.fill_current(values or {})
        if state["status"] == "confirm":
            self.click(self.OWNER, "تأیید و اجرا")
        self.assertEqual(self.state()["status"], "done", self.screen(self.OWNER))

    def customer(self):
        self.send(self.CUSTOMER, text="/start")
        return self.db.get_user_by_chat_id(self.CUSTOMER["id"])

    def processing_ready_order(self):
        user = self.customer()
        self.db.credit_wallet(user["id"], 100_000, reason="synthetic fixture", idempotency_key="audit-funds")
        order = self.db.create_order(user["id"], self.product["id"])
        self.db.hold_wallet_funds(order["id"], idempotency_key="audit-hold")
        self.db.mark_ready_order_processing(order["id"], admin_note="Synthetic restock wait")
        return user, self.db.get_order(order["id"])

    def test_ready_restock_order_never_displays_manual_information_button(self):
        user, order = self.processing_ready_order()
        self.app.show_order(user, order["id"])
        self.assertFalse(any(b.get("callback_data", "").startswith("orderinfo:") for b in self.buttons(self.CUSTOMER)))
        self.assertEqual(self.db.get_order(order["id"])["status"], "processing")

    def test_legacy_ready_information_button_is_rejected_before_collecting_data(self):
        user, order = self.processing_ready_order()
        # Reproduce a real keyboard emitted by the previous release.
        self.telegram.send_message(user["chat_id"], "Legacy ready order", reply_markup=inline_keyboard([
            [callback_button("ارسال اطلاعات", f"orderinfo:{order['id']}")],
        ]))
        self.click(self.CUSTOMER, "ارسال اطلاعات")
        self.assertIsNone(self.db.get_user_state(user["id"]))
        self.assertTrue(self.telegram.callback_answers[-1].get("show_alert"))
        self.assertIsNone(self.db.get_order(order["id"])["customer_info_json"])

    def test_ready_information_state_from_old_release_is_cleared_without_mutation(self):
        user, order = self.processing_ready_order()
        self.db.set_user_state(user["id"], "order_information", {"order_id": order["id"]})
        self.send(self.CUSTOMER, text="This ready product must never collect credentials.")
        self.assertIsNone(self.db.get_user_state(user["id"]))
        self.assertIsNone(self.db.get_order(order["id"])["customer_info_json"])

    def test_cancel_cannot_bypass_disabled_bot_or_forced_join(self):
        for gate in ("disabled", "join"):
            with self.subTest(gate=gate):
                self.db.set_setting("bot_enabled", True)
                user = self.customer()
                self.click(self.CUSTOMER, "کیف پول")
                self.click(self.CUSTOMER, "افزایش موجودی")
                if gate == "disabled":
                    self.db.set_setting("bot_enabled", False)
                with patch.object(self.app, "_check_memberships", return_value=gate != "join"):
                    self.click(self.CUSTOMER, "لغو و بازگشت")
                self.assertIsNone(self.db.get_user_state(user["id"]))
                self.assertNotIn("فروشگاه", [b["text"] for b in self.buttons(self.CUSTOMER)])
                self.assertIn("بروزرسانی" if gate == "disabled" else "عضویت", self.screen(self.CUSTOMER)["text"])

    def test_inactive_filter_is_labelled_by_activity_not_purchases(self):
        active = self.customer()
        old = self.db.upsert_user(17002, 17002, username="inactive_fixture", first_name="قدیمی",
                                  now=utc_now() - timedelta(days=45))
        self.panel("users")
        self.click(self.OWNER, ACTIONS["users"].label)
        self.assertIn("بدون فعالیت در چند روز اخیر", [b["text"] for b in self.buttons(self.OWNER)])
        self.click(self.OWNER, "بدون فعالیت در چند روز اخیر")
        self.fill_current({"days": "30"})
        self.assertEqual(self.state()["status"], "done")
        output = "\n".join(m["text"] for m in self.telegram.messages if m["chat_id"] == self.OWNER["id"])
        self.assertIn(old["username"], output)
        # Both users have no purchase: the filter is last activity, not sales.
        self.assertNotIn(active["username"], output)

    def confirm_form(self, values):
        self.assertEqual(self.fill_current(values)["status"], "confirm")
        self.click(self.OWNER, "تأیید و اجرا")
        self.assertEqual(self.state()["status"], "done", self.screen(self.OWNER))

    def open_product(self, product):
        self.panel("catalog")
        self.click(self.OWNER, "دسته: دسته ممیزی")
        self.click(self.OWNER, "دسته: زیرگروه ممیزی")
        self.click(self.OWNER, "محصول: " + product["name"])

    def create_catalogue(self, kind):
        self.panel("catalog")
        self.click(self.OWNER, "افزودن دستهٔ اصلی")
        self.confirm_form({"name": "دسته ممیزی"})
        self.panel("catalog")
        self.click(self.OWNER, "دسته: دسته ممیزی")
        self.click(self.OWNER, "افزودن زیردسته")
        self.confirm_form({"name": "زیرگروه ممیزی"})
        self.panel("catalog")
        self.click(self.OWNER, "دسته: دسته ممیزی")
        self.click(self.OWNER, "دسته: زیرگروه ممیزی")
        self.click(self.OWNER, "افزودن محصول در این دسته")
        title = "اشتراک ممیزی " + kind
        self.confirm_form({"name": title, "amount": "100000", "duration": "۳۰ روز", "type": kind})
        return self.app.admin_controller._query_one("SELECT * FROM products WHERE name=?", (title,))

    def edit_product(self, product, label, value):
        self.open_product(product)
        self.click(self.OWNER, "اطلاعات و ویرایش محصول")
        self.click(self.OWNER, label)
        self.click(self.OWNER, "ویرایش این مقدار")
        self.confirm_form({"value": value})

    def purchase(self, product):
        user = self.customer()
        self.click(self.CUSTOMER, "فروشگاه")
        self.click(self.CUSTOMER, "دسته ممیزی")
        self.click(self.CUSTOMER, "زیرگروه ممیزی")
        self.click(self.CUSTOMER, product["name"])
        self.click(self.CUSTOMER, "خرید")
        if not user.get("customer_name"):
            self.send(self.CUSTOMER, text="خریدار ممیزی")
            contact_buttons = [b for b in self.buttons(self.CUSTOMER) if b.get("request_contact")]
            self.assertEqual(len(contact_buttons), 1)
            self.send(self.CUSTOMER, contact={"user_id": self.CUSTOMER["id"],
                                             "phone_number": "+989120001234", "first_name": "خریدار"})
        orders = self.db.list_orders(user_id=user["id"])
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0]["status"], "pending_payment")
        self.assertIn(orders[0]["order_number"], self.screen(self.CUSTOMER)["text"])
        return user, orders[0]

    def test_catalogue_buttons_to_discount_partial_wallet_receipt_delivery_and_report(self):
        product = self.create_catalogue("ready")
        self.edit_product(product, "توضیح کوتاه", "html:<b>توضیح اصلی ممیزی</b>")
        self.edit_product(product, "راهنمای تحویل", "راهنمای ورود ممیزی")
        self.open_product(product)
        self.click(self.OWNER, "انبار محصول")
        self.click(self.OWNER, "افزایش موجودی / افزودن اکانت")
        payload = "audit@example.test\npass: synthetic-only\n2FA: fixture"
        self.confirm_form({"secret": payload})
        self.assertFalse(any(payload in m["text"] for m in self.telegram.messages if m["chat_id"] == self.OWNER["id"]))
        user = self.customer()
        self.action("wallet_adjust", {"target": user["chat_id"], "amount": "30000", "note": "اعتبار ممیزی"})
        self.action("discount_add", {"code": "AUDIT10", "type": "percent", "amount": "10", "product": product["id"]})
        user, order = self.purchase(product)
        self.click(self.CUSTOMER, "ثبت کد تخفیف")
        self.send(self.CUSTOMER, text="NOT-A-CODE")
        self.assertIn("معتبر نیست", self.screen(self.CUSTOMER)["text"])
        self.click(self.CUSTOMER, "بازگشت")
        self.click(self.CUSTOMER, "ثبت کد تخفیف")
        self.send(self.CUSTOMER, text="AUDIT10")
        self.assertEqual(self.db.get_order(order["id"])["discount_amount"], 10000)
        self.click(self.CUSTOMER, "پرداخت")
        self.click(self.CUSTOMER, "کیف پول")
        self.assertEqual(self.db.get_order(order["id"])["payable_amount"], 60000)
        self.click(self.CUSTOMER, "کارت به کارت")
        payment = self.db.latest_order_payment(order["id"])
        self.assertEqual(payment["base_amount"], 60000)
        copied = next(b["copy_text"]["text"] for b in self.buttons(self.CUSTOMER) if b["text"] == "کپی مبلغ")
        self.assertEqual(int(copied), payment["payable_amount"])
        self.click(self.CUSTOMER, "ارسال فیش واریز")
        self.send(self.CUSTOMER, photo=[{"file_id": "audit-receipt-photo", "width": 80, "height": 80}])
        self.assertEqual(self.db.get_payment(payment["id"])["status"], "verifying")
        self.action("approve_payment", {"target": payment["payment_number"]})
        completed = self.db.get_order(order["id"])
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["wallet_captured_amount"], 30000)
        self.assertEqual(completed["external_paid_amount"], 60000)
        self.assertEqual(self.db.wallet_balance(user["id"]), 0)
        deliveries = [m for m in self.telegram.messages if m["chat_id"] == user["chat_id"] and payload in m["text"]]
        self.assertEqual(len(deliveries), 1)
        self.assertIn("راهنمای ورود ممیزی", deliveries[0]["text"])
        self.app.run_maintenance()
        self.assertEqual(len([m for m in self.telegram.messages if payload in m["text"]]), 1)
        self.customer()
        self.click(self.CUSTOMER, "حساب من")
        self.click(self.CUSTOMER, "سفارش‌های من")
        self.click(self.CUSTOMER, data=f"order:{order['id']}")
        self.assertIn("synthetic-only", self.screen(self.CUSTOMER)["text"])
        today = utc_now().astimezone(ZoneInfo(self.settings.timezone)).date().isoformat()
        self.action("report", {"kind": "finance", "start": today, "end": today})
        report = list(csv.DictReader(io.StringIO(self.telegram.documents[-1]["document"].decode("utf-8-sig"))))
        self.assertEqual(len(report), 1)
        self.assertEqual(report[0]["order_number"], order["order_number"])
        self.assertEqual(self.db.summary_report()["gross_revenue"], 90000)

    def test_manual_catalogue_to_information_correction_default_completion_and_reminders(self):
        product = self.create_catalogue("manual")
        self.edit_product(product, "متن درخواست اطلاعات", "html:<b>ایمیل فعال‌سازی را ارسال کنید.</b>")
        self.edit_product(product, "متن تکمیل سفارش", "html:<b>فعال‌سازی ممیزی تکمیل شد.</b>")
        self.edit_product(product, "روزهای یادآوری", "7,3,0")
        user = self.customer()
        self.action("wallet_adjust", {"target": user["chat_id"], "amount": "100000", "note": "اعتبار ممیزی دستی"})
        user, order = self.purchase(product)
        self.click(self.CUSTOMER, "پرداخت")
        self.click(self.CUSTOMER, "کیف پول")
        self.assertEqual(self.db.get_order(order["id"])["status"], "awaiting_info")
        self.assertIn("ایمیل فعال‌سازی", self.screen(self.CUSTOMER)["text"])
        self.click(self.CUSTOMER, "ارسال اطلاعات")
        self.send(self.CUSTOMER, text="first@example.test")
        self.assertEqual(self.db.get_order(order["id"])["status"], "processing")
        self.action("request_info", {"target": order["order_number"], "body": "لطفاً ایمیل را اصلاح کنید."})
        self.assertEqual(self.db.get_order(order["id"])["status"], "awaiting_info")
        self.customer()
        self.click(self.CUSTOMER, "حساب من")
        self.click(self.CUSTOMER, "سفارش‌های من")
        self.click(self.CUSTOMER, data=f"order:{order['id']}")
        self.click(self.CUSTOMER, "ارسال اطلاعات")
        self.send(self.CUSTOMER, text="corrected@example.test")
        self.panel("orders")
        self.click(self.OWNER, ACTIONS["complete"].label)
        state = self.state()
        index = next(i for i, option in enumerate(state["options"]) if option[0] == order["order_number"])
        self.click(self.OWNER, ending=f":pick:{index}")
        self.click(self.OWNER, "استفاده از متن تکمیل محصول")
        self.assertEqual(self.db.get_order(order["id"])["status"], "processing")
        confirmation = self.click(self.OWNER, "تأیید و اجرا")
        self.app.process_update_safe(copy.deepcopy(confirmation))
        completed = self.db.get_order(order["id"])
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(json.loads(completed["customer_info_json"])["text"], "corrected@example.test")
        reminders = self.app.admin_controller._query("SELECT * FROM reminders WHERE order_id=?", (order["id"],))
        self.assertEqual(sorted(r["days_before"] for r in reminders), [0, 3, 7])
        self.app.run_maintenance()
        notices = [m for m in self.telegram.messages if m["chat_id"] == user["chat_id"] and "فعال‌سازی ممیزی تکمیل شد" in m["text"]]
        self.assertEqual(len(notices), 1)

    def test_faq_ticket_attachment_reply_close_and_reopen_through_buttons(self):
        self.action("faq_category_add", {"name": "پرسش‌های ممیزی"})
        category = self.app.admin_controller._query_one("SELECT * FROM faq_categories WHERE name=?", ("پرسش‌های ممیزی",))
        self.action("faq_add", {"target": category["id"], "question": "چگونه وارد شوم؟", "answer": "html:<b>راهنمای پاسخ کامل</b>"})
        user = self.customer()
        self.click(self.CUSTOMER, "پشتیبانی")
        self.click(self.CUSTOMER, "سوالات متداول")
        self.click(self.CUSTOMER, "پرسش‌های ممیزی")
        self.click(self.CUSTOMER, "چگونه وارد شوم؟")
        self.assertIn("<b>راهنمای پاسخ کامل</b>", self.screen(self.CUSTOMER)["text"])
        self.customer()
        self.click(self.CUSTOMER, "پشتیبانی")
        self.click(self.CUSTOMER, "ثبت تیکت")
        self.send(self.CUSTOMER, text="درخواست کمک ممیزی")
        self.send(self.CUSTOMER, document={"file_id": "audit-ticket-file", "file_name": "audit.txt"})
        ticket = self.db.list_tickets(user_id=user["id"])[0]
        self.action("ticket_reply", {"target": ticket["ticket_number"], "body": "پاسخ پشتیبانی ممیزی"})
        self.action("ticket_attachment", {"target": ticket["ticket_number"],
                                          "attachment": self.db.list_ticket_messages(ticket["id"])[0]["id"]})
        self.assertEqual(self.telegram.documents[-1]["document"], "audit-ticket-file")
        self.action("ticket_close", {"target": ticket["ticket_number"]})
        self.customer()
        self.click(self.CUSTOMER, "پشتیبانی")
        self.click(self.CUSTOMER, "تیکت‌های قبلی")
        self.click(self.CUSTOMER, data=f"ticket:{ticket['id']}")
        self.assertNotIn("ارسال پاسخ", [b["text"] for b in self.buttons(self.CUSTOMER)])
        self.action("ticket_status", {"target": ticket["ticket_number"], "status": "open"})
        self.customer()
        self.click(self.CUSTOMER, "پشتیبانی")
        self.click(self.CUSTOMER, "تیکت‌های قبلی")
        self.click(self.CUSTOMER, data=f"ticket:{ticket['id']}")
        self.click(self.CUSTOMER, "ارسال پاسخ")
        self.send(self.CUSTOMER, text="پیگیری پس از بازشدن تیکت")
        self.assertEqual(len(self.db.list_ticket_messages(ticket["id"])), 3)

    def test_combined_reward_editor_names_the_buyer_whose_referrals_are_evaluated(self):
        self.panel("rewards")
        self.click(self.OWNER, ACTIONS["reward_add"].label)
        self.click(self.OWNER, "شرط‌های ترکیبی")
        self.send(self.OWNER, text="1000")
        self.click(self.OWNER, ending=":default:0")
        self.click(self.OWNER, ending=":default:0")
        self.click(self.OWNER, "خیر")
        self.assertIn("دعوت‌های ثبت‌شدهٔ خریدار", self.screen(self.OWNER)["text"])
        self.click(self.OWNER, ending=":default:0")
        self.assertIn("دعوت‌های واجد پاداشِ خریدار", self.screen(self.OWNER)["text"])


if __name__ == "__main__":
    unittest.main()
