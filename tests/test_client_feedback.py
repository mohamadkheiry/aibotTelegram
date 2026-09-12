"""Regression cases for reviewed client UI feedback; fictional data only."""
from __future__ import annotations

import unittest
import copy
import json
from datetime import timedelta
from unittest.mock import patch

from app import texts
from app.admin import _duration_days
from app.customer_layouts import defaults, upgrade_saved_layout, validate
from app.db import ConflictError, NotFoundError, ValidationError
from app.keyboards import inline_main_menu_keyboard, main_menu_keyboard
from app.telegram import TelegramAPIError, TelegramError
from app.utils import utc_now
from app.utils import custom_emoji_id, display_datetime, duration_text, telegram_input_text
from tests import test_admin_catalog_hierarchy as catalog_fixture
from tests import test_spec_end_to_end as journey_fixture
from tests import test_spec_reminders_transactions as reminder_fixture


class RichInputTests(unittest.TestCase):
    def test_utf16_native_emoji_nested_formatting_and_safe_link_round_trip(self):
        text = "💜 عنوان و راهنما"
        value = telegram_input_text(text, [
            {"type": "custom_emoji", "offset": 0, "length": 2, "custom_emoji_id": "123456789"},
            {"type": "bold", "offset": 3, "length": 5},
            {"type": "italic", "offset": 3, "length": 5},
            {"type": "text_link", "offset": 11, "length": 6, "url": "https://example.com/help?a=1&b=2"},
        ])
        self.assertIn('<tg-emoji emoji-id="123456789">💜</tg-emoji>', value)
        self.assertIn("<b><i>عنوان</i></b>", value)
        self.assertIn('href="https://example.com/help?a=1&amp;b=2"', value)
        self.assertEqual(custom_emoji_id(value), "123456789")

    def test_unsafe_links_invalid_ranges_and_literal_html_do_not_become_markup(self):
        value = telegram_input_text("💜 <b>text</b>", [
            {"type": "bold", "offset": 1, "length": 1},
            {"type": "text_link", "offset": 3, "length": 11, "url": "https://127.0.0.1/private"},
            {"type": "custom_emoji", "offset": 0, "length": 2, "custom_emoji_id": '12" bad'},
        ])
        self.assertEqual(value, "💜 <b>text</b>")
        self.assertNotIn("html:", value)

    def test_explicit_units_and_timezone_do_not_reinterpret_old_data(self):
        self.assertEqual(_duration_days("۳ ماه"), 90)
        self.assertEqual(_duration_days("18 months"), 540)
        self.assertEqual(_duration_days("10"), 10)
        self.assertEqual(duration_text("10", 10), "10 روز")
        self.assertIsNone(_duration_days("بدون انقضا"))
        self.assertEqual(display_datetime("2026-09-08T09:30:00+00:00"), "2026-09-08 · 13:00")

    def test_product_copy_omits_empty_sections_and_places_facts_before_description(self):
        product = {"title": "محصول", "price": 2500, "duration": "3 ماه", "short_description": "توضیح نهایی",
                   "icon": 'html:<tg-emoji emoji-id="123">💜</tg-emoji>', "warranty": "", "activation": None}
        summary = texts.product_summary(product, "تومان")
        self.assertLess(summary.index("2,500"), summary.index("توضیح نهایی"))
        self.assertNotIn("گارانتی", summary)
        self.assertNotIn("فعال‌سازی", summary)
        self.assertIn('<tg-emoji emoji-id="123">', summary)
        detail = texts.product_details(product)
        self.assertNotIn("شرایط استفاده", detail)
        self.assertNotIn("—", detail)


class ClientAdminFeedbackTests(unittest.TestCase):
    OWNER = catalog_fixture.AdminCatalogHierarchyTests.OWNER
    setUp = catalog_fixture.AdminCatalogHierarchyTests.setUp
    tearDown = catalog_fixture.AdminCatalogHierarchyTests.tearDown
    message = catalog_fixture.AdminCatalogHierarchyTests.message
    callback = catalog_fixture.AdminCatalogHierarchyTests.callback
    _take_update_id = catalog_fixture.AdminCatalogHierarchyTests._take_update_id
    send_message = catalog_fixture.AdminCatalogHierarchyTests.send_message
    send_callback = catalog_fixture.AdminCatalogHierarchyTests.send_callback
    actor_user = catalog_fixture.AdminCatalogHierarchyTests.actor_user
    state = catalog_fixture.AdminCatalogHierarchyTests.state
    prompt = catalog_fixture.AdminCatalogHierarchyTests.prompt
    button_update = catalog_fixture.AdminCatalogHierarchyTests.button_update
    click = catalog_fixture.AdminCatalogHierarchyTests.click
    buttons = catalog_fixture.AdminCatalogHierarchyTests.buttons
    open_products = catalog_fixture.AdminCatalogHierarchyTests.open_products
    open_product = catalog_fixture.AdminCatalogHierarchyTests.open_product
    confirm = catalog_fixture.AdminCatalogHierarchyTests.confirm
    view = catalog_fixture.AdminCatalogHierarchyTests.view

    def test_category_sort_through_actual_buttons_and_customer_order(self):
        self.open_products()
        self.click(ending=f":category:{self.category['id']}:1")
        self.click(label="ویرایش مشخصات دسته")
        self.click(label="ترتیب نمایش")
        self.assertIn("عدد کوچک‌تر", self.prompt()["text"])
        self.send_message(self.OWNER, text="-۲۰")
        self.confirm()
        self.assertEqual(self.db.get_category(self.category["id"])["sort_order"], -20)

    def test_native_completion_format_is_saved_without_losing_link_labels(self):
        self.open_product()
        self.click(label="اطلاعات و ویرایش محصول")
        self.click(label="متن تکمیل سفارش")
        self.click(label="ویرایش این مقدار")
        update = self.message(self.OWNER, text="راهنما و پایان")
        update["message"]["entities"] = [{"type": "text_link", "offset": 0, "length": 6, "url": "https://example.com/help"}]
        self.app.process_update_safe(update)
        self.confirm()
        self.assertEqual(self.db.get_product(self.product["id"])["completion_text"],
                         'html:<a href="https://example.com/help">راهنما</a> و پایان')

    def test_payment_method_first_list_shows_status(self):
        self.send_callback(self.OWNER, "adm:ui:a:payment")
        labels = [b["text"] for b in self.buttons()]
        self.assertIn("کیف پول · فعال", labels)
        self.assertIn("رمزارز · غیرفعال", labels)

    def test_main_menu_is_single_inline_message_without_editing_reply_removal(self):
        before = len(self.telegram.messages)
        self.send_message(self.OWNER, text="/start")
        sent = self.telegram.messages[before:]
        self.assertEqual(len(sent), 1)
        self.assertIn("inline_keyboard", sent[0]["reply_markup"])
        self.assertNotIn("remove_keyboard", sent[0]["reply_markup"])

    def test_reminder_off_and_single_duration_field(self):
        self.open_product()
        self.click(label="اطلاعات و ویرایش محصول")
        self.assertIn("مدت اشتراک", [b["text"] for b in self.buttons()])
        self.assertNotIn("مدت به روز", [b["text"] for b in self.buttons()])
        self.click(label="روزهای یادآوری")
        self.click(label="ویرایش این مقدار")
        self.click(label="غیرفعال‌کردن یادآوری")
        self.confirm()
        self.assertEqual(self.db.get_product(self.product["id"])["reminder_days_json"], "[]")

    def test_stock_filters_search_assignment_context_and_explicit_payload(self):
        target = self.db.upsert_user(88201, 88201, username="assigned_buyer")
        other = self.db.add_inventory_item(self.product["id"], "other synthetic secret")
        self.db.set_inventory_status(other["id"], "disabled")
        self.open_product()
        self.click(label="انبار محصول")
        filter_button = next(b for b in self.buttons() if b.get("callback_data", "").endswith(":disabled"))
        self.click(label=filter_button["text"])
        self.assertEqual(self.view()["status"], "disabled")
        item_buttons = [b for b in self.buttons() if ":item:" in b.get("callback_data", "")]
        self.assertEqual(len(item_buttons), 1)
        self.assertTrue(item_buttons[0]["callback_data"].endswith(f":{other['id']}"))
        self.click(label="جست‌وجوی آیتم‌ها")
        self.send_message(self.OWNER, text=str(other["id"]))
        self.assertEqual(self.view()["status"], "disabled")
        self.click(ending=f":item:{self.product['id']}:{other['id']}")
        self.assertNotIn("other synthetic secret", self.prompt()["text"])
        self.click(label="نمایش اطلاعات محرمانه این آیتم")
        secret = next(m for m in self.telegram.messages if "other synthetic secret" in m["text"])
        self.assertTrue(secret["protect_content"])
        self.assertNotIn("other synthetic secret", json.dumps(self.view()))
        self.assertEqual(self.db.wallet_balance(target["id"]), 0)

    def test_discount_list_detail_freezes_target_until_confirmation(self):
        discount = self.db.create_discount("HELLO10", discount_type="percent", value=10)
        self.send_callback(self.OWNER, "adm:ui:a:discounts")
        choice = next(b for b in self.buttons() if b.get("callback_data") == f"adm:ui:discount:{discount['id']}")
        self.click(label=choice["text"])
        self.assertIn("HELLO10", self.prompt()["text"])
        self.assertIn("وضعیت: فعال", self.prompt()["text"])
        self.click(label="غیرفعال‌کردن این کد")
        self.assertEqual(self.state()["status"], "confirm")
        self.assertIn("پس از تأیید: کد غیرفعال می‌شود", self.prompt()["text"])
        original = copy.deepcopy(self.prompt())
        self.confirm()
        self.assertFalse(next(d for d in self.db.list_discounts() if d["id"] == discount["id"])["is_active"])
        self.click(label="تأیید و اجرا", prompt=original)
        self.assertFalse(next(d for d in self.db.list_discounts() if d["id"] == discount["id"])["is_active"])

    def test_historical_duration_field_opens_unified_control(self):
        self.send_message(self.OWNER, text="/start")
        self.send_callback(self.OWNER, f"adm:ui:c:field:{self.product['id']}:duration_days")
        self.assertEqual(self.view()["field"], "duration")
        self.click(label="ویرایش این مقدار")
        self.send_message(self.OWNER, text="2 ماه")
        self.confirm()
        self.assertEqual(self.db.get_product(self.product["id"])["duration_days"], 60)

    def test_user_detail_has_new_search_and_pairs_same_user_block_actions(self):
        target = self.db.upsert_user(88202, 88202, username="search_target")
        self.send_message(self.OWNER, text="/start")
        self.send_callback(self.OWNER, f"adm:ui:open:user:{target['chat_id']}")
        rows = self.prompt()["reply_markup"]["inline_keyboard"]
        self.assertIn("جست‌وجوی کاربر دیگر", [b["text"] for row in rows for b in row])
        pairs = [row for row in rows if any(f"open:block:{target['chat_id']}" in b.get("callback_data", "") for b in row)]
        self.assertEqual(len(pairs), 1)
        self.assertEqual(len(pairs[0]), 2)
        self.assertTrue(pairs[0][1]["callback_data"].endswith(f"open:unblock:{target['chat_id']}"))

    def test_full_completion_footer_is_checked_before_changing_inventory(self):
        before = self.db.get_product(self.product["id"])
        with self.assertRaises(ValidationError):
            self.db.update_product(self.product["id"], completion_text="ت" * 3900)
        self.assertEqual(self.db.get_product(self.product["id"])["completion_text"], before["completion_text"])
        empty = self.db.create_product(self.category["id"], "Empty catalog fixture", product_type="ready", price_amount=1,
            completion_text="ت" * 3900)
        with self.assertRaises(ValidationError):
            self.db.add_inventory_item(empty["id"], "synthetic payload")
        self.assertEqual(self.db.inventory_count(empty["id"]), 0)


class ClientJourneyFeedbackTests(unittest.TestCase):
    OWNER = journey_fixture.SourceEndToEndTests.OWNER
    CUSTOMER = journey_fixture.SourceEndToEndTests.CUSTOMER
    setUp = journey_fixture.SourceEndToEndTests.setUp
    tearDown = journey_fixture.SourceEndToEndTests.tearDown
    message = journey_fixture.SourceEndToEndTests.message
    callback = journey_fixture.SourceEndToEndTests.callback
    _take_update_id = journey_fixture.SourceEndToEndTests._take_update_id
    send = journey_fixture.SourceEndToEndTests.send
    screen = journey_fixture.SourceEndToEndTests.screen
    buttons = journey_fixture.SourceEndToEndTests.buttons
    click = journey_fixture.SourceEndToEndTests.click
    customer = journey_fixture.SourceEndToEndTests.customer

    def receipt(self):
        user = self.customer()
        payment = self.db.create_wallet_topup_payment(user["id"], 500000, "card", idempotency_key="client-receipt")
        self.app._show_card_payment(user, payment, "6037997512345678", "Synthetic owner", query=None)
        self.click(self.CUSTOMER, "ارسال فیش واریز")
        self.send(self.CUSTOMER, photo=[{"file_id": "synthetic-client-receipt"}])
        return user, self.db.get_payment(payment["id"])

    def test_explicit_order_cancel_confirms_and_releases_discount(self):
        user = self.customer()
        discount = self.db.create_discount(
            "CANCEL10", discount_type="percent", value=10, max_uses=1
        )
        order = self.db.create_order(user["id"], self.product["id"])
        self.db.apply_discount(order["id"], "CANCEL10")
        self.app.show_order_summary(user, order["id"])

        self.click(self.CUSTOMER, "لغو سفارش")
        self.assertIn(order["order_number"], self.screen(self.CUSTOMER)["text"])
        self.assertEqual(self.db.get_order(order["id"])["status"], "pending_payment")
        self.click(self.CUSTOMER, "بازگشت")
        self.assertEqual(self.db.get_order(order["id"])["status"], "pending_payment")
        self.click(self.CUSTOMER, "لغو سفارش")
        self.click(self.CUSTOMER, "تأیید لغو سفارش")

        self.assertEqual(self.db.get_order(order["id"])["status"], "cancelled")
        self.assertEqual(
            next(row for row in self.db.list_discounts() if row["id"] == discount["id"])["used_count"],
            0,
        )

    def test_order_cancel_is_not_offered_after_external_payment_intent_exists(self):
        user = self.customer()
        order = self.db.create_order(user["id"], self.product["id"])
        self.db.create_order_payment(
            order["id"],
            "card",
            idempotency_key="awaiting-confirmation-cancel-guard",
        )

        self.app.show_order_summary(user, order["id"])

        labels = [button["text"] for button in self.buttons(self.CUSTOMER)]
        self.assertNotIn("لغو سفارش", labels)
        self.assertIn("در انتظار تأیید", self.screen(self.CUSTOMER)["text"])

    def test_receipt_is_one_caption_with_buttons_and_rejection_requires_reason(self):
        user, payment = self.receipt()
        self.assertEqual(payment["status"], "verifying")
        alerts = [p for p in self.telegram.photos if p["chat_id"] == self.OWNER["id"]]
        self.assertEqual(len(alerts), 1)
        self.assertEqual(self.telegram.copies, [])
        self.assertIn("شماره پیگیری بانک نیست", alerts[0]["caption"])
        self.app._reconcile_card_receipt_alerts()
        self.assertEqual(len(self.telegram.photos), 1)
        self.click(self.OWNER, "رد پرداخت", screen=alerts[0])
        self.assertEqual(self.db.get_payment(payment["id"])["status"], "verifying")
        self.send(self.OWNER, text="رسید با مبلغ درخواست‌شده همخوانی ندارد")
        self.assertEqual(self.db.get_payment(payment["id"])["status"], "verifying")
        update = self.click(self.OWNER, "تأیید و اجرا")
        self.assertEqual(self.db.get_payment(payment["id"])["status"], "failed")
        self.assertEqual(self.db.wallet_balance(user["id"]), 0)
        self.assertIn("رسید با مبلغ درخواست‌شده همخوانی ندارد", self.screen(self.CUSTOMER)["text"])
        count = len(self.telegram.messages)
        self.assertIsNot(self.app.process_update_safe(copy.deepcopy(update)), False)
        self.assertEqual(len(self.telegram.messages), count)

    def test_pending_receipt_is_not_delivered_to_revoked_admin(self):
        user = self.customer()
        revoked = self.db.upsert_user(98703, 98703, username="revoked_receipt_admin")
        admin = self.db.add_admin(revoked["username"], revoked["chat_id"], role="admin")
        payment = self.db.create_wallet_topup_payment(user["id"], 600000, "card", idempotency_key="queued-receipt")
        payment = self.db.submit_payment_receipt(payment["id"], "synthetic-queued-photo", file_kind="photo")
        with patch.object(self.telegram, "send_photo", side_effect=TelegramError("temporary network failure")):
            self.app._alert_card_receipt(payment, user)
        self.db.set_admin_active(admin["id"], False)
        claim = self.db.claim_outbound_messages
        with patch.object(self.db, "claim_outbound_messages", side_effect=lambda **kw: claim(now=utc_now() + timedelta(minutes=2), **kw)):
            self.app._deliver_outbound_messages()
        self.assertFalse(any(p["chat_id"] == revoked["chat_id"] for p in self.telegram.photos))
        rows = self.app.admin_controller._query("SELECT status FROM outbound_messages WHERE idempotency_key LIKE ?", (f"payment:%:receipt:%:admin:{admin['id']}",))
        self.assertTrue(rows)
        self.assertTrue(all(r["status"] == "cancelled" for r in rows))

    def test_media_timeout_never_falls_back_to_duplicate_plain_alert(self):
        user = self.customer()
        payment = self.db.create_wallet_topup_payment(user["id"], 700000, "card", idempotency_key="timeout-receipt")
        payment = self.db.submit_payment_receipt(payment["id"], "synthetic-timeout-photo", file_kind="photo")
        before = len(self.telegram.messages)
        with patch.object(self.telegram, "send_photo", side_effect=TelegramError("synthetic timeout")):
            self.app._alert_card_receipt(payment, user)
        self.assertEqual(len(self.telegram.messages), before)
        rows = self.app.admin_controller._query("SELECT o.status, a.attempt_count FROM outbound_messages o JOIN outbound_message_attempts a ON a.message_id=o.id WHERE o.idempotency_key LIKE 'payment:%:receipt:%'")
        self.assertEqual([(r["status"], r["attempt_count"]) for r in rows], [("queued", 1)])

    def test_definitely_unavailable_receipt_media_falls_back_once(self):
        user = self.customer()
        payment = self.db.create_wallet_topup_payment(user["id"], 700000, "card", idempotency_key="bad-media-receipt")
        payment = self.db.submit_payment_receipt(payment["id"], "synthetic-old-file", file_kind="photo")
        with patch.object(self.telegram, "send_photo", side_effect=TelegramAPIError("sendPhoto", "invalid file", error_code=400)):
            self.app._alert_card_receipt(payment, user)
            self.app._alert_card_receipt(payment, user)
        notices = [m for m in self.telegram.messages if payment["payment_number"] in m["text"]]
        self.assertEqual(len(notices), 1)
        self.assertTrue(notices[0]["reply_markup"]["inline_keyboard"])

    def test_referral_counts_buyers_once_and_reports_only_credited_rewards(self):
        user = self.customer()
        inviter = self.db.upsert_user(98704, 98704, username="synthetic_inviter")
        referral = self.db.record_referral(inviter["id"], user["id"])
        self.db.create_reward_rule("Synthetic start reward", event_type="start", amount=25)
        self.db.grant_start_rewards(referral["id"])
        self.db.credit_wallet(user["id"], 200000, reason="synthetic funds", idempotency_key="referral-funds")
        for index in range(2):
            order = self.db.create_order(user["id"], self.product["id"], idempotency_key=f"referral-order-{index}")
            self.db.hold_wallet_funds(order["id"], idempotency_key=f"referral-hold-{index}")
        summary = self.db.referral_summary(inviter["id"])
        self.assertEqual(summary["invited_count"], 1)
        self.assertEqual(summary["buyer_count"], 1)
        self.assertEqual(summary["reward_total"], self.db.wallet_balance(inviter["id"]))
        self.assertEqual(summary["reward_total"], 25)

    def test_wallet_amount_input_and_both_card_copy_units(self):
        user = self.customer()
        self.click(self.CUSTOMER, "کیف پول")
        self.assertEqual(self.db.get_user_state(user["id"])["state"], "wallet_topup_amount")
        self.assertNotIn("افزایش موجودی", [b["text"] for b in self.buttons(self.CUSTOMER)])
        self.send(self.CUSTOMER, text="۲۰۰۰۰۰۰")
        self.click(self.CUSTOMER, "کارت به کارت")
        amount = next(b["copy_text"]["text"] for b in self.buttons(self.CUSTOMER) if b["text"] == "کپی مبلغ به تومان")
        rial = next(b["copy_text"]["text"] for b in self.buttons(self.CUSTOMER) if b["text"] == "کپی مبلغ به ریال")
        self.assertEqual(int(rial), int(amount) * 10)
        self.assertEqual(self.db.wallet_balance(user["id"]), 0)

    def test_ticket_single_input_close_cancel_reopen_and_foreign_scope(self):
        user = self.customer()
        self.click(self.CUSTOMER, "پشتیبانی")
        self.click(self.CUSTOMER, "ثبت تیکت")
        update = self.message(self.CUSTOMER, photo=[{"file_id": "synthetic-ticket-photo"}])
        update["message"]["caption"] = "مشکل ورود\nشرح کامل مسئله"
        self.assertIsNot(self.app.process_update_safe(update), False)
        ticket = self.db.list_tickets(user_id=user["id"])[0]
        self.assertEqual(ticket["subject"], "مشکل ورود")
        self.assertEqual(self.db.count_ticket_messages(ticket["id"]), 1)
        self.click(self.CUSTOMER, "مشاهده تیکت")
        self.assertIn("شرح کامل مسئله", self.screen(self.CUSTOMER)["text"])
        self.click(self.CUSTOMER, "بستن تیکت")
        stale = copy.deepcopy(self.screen(self.CUSTOMER))
        self.click(self.CUSTOMER, "بازگشت")
        self.click(self.CUSTOMER, "بستن تیکت", screen=stale)
        self.assertEqual(self.db.get_ticket(ticket["id"])["status"], "open")
        # Open a fresh, emitted view after the rejected stale confirmation.
        self.app.show_tickets(user)
        self.click(self.CUSTOMER, data=f"ticket:{ticket['id']}")
        self.click(self.CUSTOMER, "بستن تیکت")
        update = self.click(self.CUSTOMER, "بستن تیکت")
        self.assertEqual(self.db.get_ticket(ticket["id"])["status"], "closed")
        self.assertIsNot(self.app.process_update_safe(copy.deepcopy(update)), False)
        self.click(self.CUSTOMER, "مشاهده تیکت")
        self.click(self.CUSTOMER, "باز کردن مجدد تیکت")
        self.click(self.CUSTOMER, "باز کردن مجدد تیکت")
        self.assertEqual(self.db.get_ticket(ticket["id"])["status"], "open")
        outsider = self.db.upsert_user(98701, 98701)
        with self.assertRaises(NotFoundError):
            self.db.set_user_ticket_status(ticket["id"], outsider["id"], "closed", expected_status="open",
                expected_updated_at=ticket["updated_at"], expected_message_count=1,
                idempotency_key="foreign-status", body="forged", reply_markup={})

    def test_ticket_confirmation_rejects_a_new_message_and_preserves_it(self):
        user = self.customer()
        ticket = self.db.create_ticket(user["id"], "Subject", "Initial", idempotency_key="ticket-race")
        self.app.show_tickets(user)
        self.click(self.CUSTOMER, data=f"ticket:{ticket['id']}")
        self.click(self.CUSTOMER, "بستن تیکت")
        before = self.db.get_user_state(user["id"])["data"]
        self.db.add_ticket_message(ticket["id"], "New message", sender_type="user", sender_user_id=user["id"], idempotency_key="ticket-race-message")
        with self.assertRaises(ConflictError):
            self.db.set_user_ticket_status(ticket["id"], user["id"], "closed", expected_status=before["expected_status"],
                expected_updated_at=before["expected_updated_at"], expected_message_count=before["expected_message_count"],
                idempotency_key="ticket-race-close", body="closed", reply_markup={})
        self.assertEqual(self.db.get_ticket(ticket["id"])["status"], "open")
        self.assertEqual(self.db.count_ticket_messages(ticket["id"]), 2)

    def test_pending_transaction_details_are_owner_scoped_and_do_not_credit_wallet(self):
        user = self.customer()
        payment = self.db.create_wallet_topup_payment(user["id"], 800000, "card", idempotency_key="pending-history")
        self.app.show_transactions(user)
        button = next(b for b in self.buttons(self.CUSTOMER) if b.get("callback_data", "").startswith("transaction:"))
        self.assertIn("در انتظار", button["text"])
        self.assertNotIn("در انتظار پرداخت", button["text"])
        self.click(self.CUSTOMER, data=button["callback_data"])
        self.assertIn("روش پرداخت: کارت به کارت", self.screen(self.CUSTOMER)["text"])
        self.assertIn("وضعیت: در انتظار پرداخت", self.screen(self.CUSTOMER)["text"])
        self.assertEqual(self.db.wallet_balance(user["id"]), 0)
        outsider = self.db.upsert_user(98702, 98702)
        self.assertIsNone(self.db.get_user_transaction(outsider["id"], f"payment:{payment['id']}"))
        self.assertIsNone(self.db.get_user_transaction(user["id"], "payment:999999999999999999999999"))

    def test_ready_delivery_keeps_complete_native_footer_and_does_not_resend(self):
        user = self.customer()
        footer = 'html:<b>همه قوانین</b>\n<a href="https://example.com/help">راهنمای ورود</a>\nپایان کامل'
        self.db.update_product(self.product["id"], completion_text=footer)
        self.db.credit_wallet(user["id"], 100000, reason="synthetic credit", idempotency_key="footer-funds")
        order = self.db.create_order(user["id"], self.product["id"], idempotency_key="footer-order")
        self.app.show_order_summary(user, order["id"])
        self.click(self.CUSTOMER, "پرداخت")
        self.click(self.CUSTOMER, "کیف پول")
        delivered = [m for m in self.telegram.messages if self.inventory_payload in m["text"]]
        self.assertEqual(len(delivered), 1)
        self.assertIn('<a href="https://example.com/help">راهنمای ورود</a>', delivered[0]["text"])
        self.assertIn("پایان کامل", delivered[0]["text"])
        self.assertIn("گذرواژه", delivered[0]["text"])
        self.app.run_maintenance()
        self.assertEqual(len([m for m in self.telegram.messages if self.inventory_payload in m["text"]]), 1)

    def test_legacy_main_outbox_is_delivered_inline_without_rewriting_or_resending(self):
        user = self.customer()
        queued = self.db.queue_outbound_message("Canonical notice", recipient_user_id=user["id"],
            idempotency_key="legacy-main-notice", reply_markup=main_menu_keyboard())
        for _ in range(2):
            self.assertTrue(self.app._notify_user_durable(user, queued["body"], idempotency_key=queued["idempotency_key"],
                reply_markup=inline_main_menu_keyboard()))
        messages = [m for m in self.telegram.messages if m["text"] == queued["body"]]
        self.assertEqual(len(messages), 1)
        self.assertIn("inline_keyboard", messages[0]["reply_markup"])
        self.assertEqual(self.db.get_outbound_message_by_idempotency_key(queued["idempotency_key"])["reply_markup_json"], queued["reply_markup_json"])


class ReminderFeedbackTests(unittest.TestCase):
    setUp = reminder_fixture.ReminderAndTransactionSpecificationTests.setUp
    completed_order = reminder_fixture.ReminderAndTransactionSpecificationTests.completed_order

    def test_disable_also_cancels_existing_queued_reminder(self):
        base = reminder_fixture.BASE_TIME
        order = self.completed_order()
        reminder = self.db.schedule_order_reminders(order["id"], now=base)[0]
        queued = self.db.queue_outbound_message("Old queued reminder", recipient_user_id=self.user["id"],
            idempotency_key=f"reminder:{reminder['id']}", now=base)
        self.db.update_product(order["product_id"], reminder_days=[])
        with patch("app.bot.utc_now", return_value=base + timedelta(days=1)):
            self.app._deliver_outbound_messages()
        self.assertEqual(self.telegram.messages, [])
        self.assertEqual(self.db.get_reminder(reminder["id"])["status"], "cancelled")
        self.assertEqual(self.db.get_outbound_message_by_idempotency_key(queued["idempotency_key"])["status"], "cancelled")

    def test_delayed_reminder_uses_current_days_and_correct_order_button(self):
        base = reminder_fixture.BASE_TIME
        order = self.completed_order()
        reminder = self.db.schedule_order_reminders(order["id"], now=base)[0]
        queued = self.db.queue_outbound_message("Old relative wording", recipient_user_id=self.user["id"],
            idempotency_key=f"reminder:{reminder['id']}", now=base)
        with patch("app.bot.utc_now", return_value=base + timedelta(days=2, hours=-1)):
            self.app._deliver_outbound_messages()
        notice = self.telegram.messages[-1]
        self.assertIn("امروز", notice["text"])
        self.assertIn(order["order_number"], notice["text"])
        self.assertEqual(notice["reply_markup"]["inline_keyboard"][0][0]["callback_data"], f"order:{order['id']}")
        self.assertEqual(self.db.get_outbound_message_by_idempotency_key(queued["idempotency_key"])["body"], "Old relative wording")


class LayoutUpgradeFeedbackTests(unittest.TestCase):
    def test_all_added_slots_upgrade_old_saved_rows_without_losing_order(self):
        added = {"stats": {"refresh"}, "transactions": {"items"}, "referral": {"copy"}, "faq": {"new"},
                 "ticket": {"close", "reopen"}, "card_payment": {"rial"}}
        for section, introduced in added.items():
            with self.subTest(section=section):
                old = defaults(section)
                old["rows"] = [[slot for slot in row if slot not in introduced] for row in reversed(old["rows"])]
                old["rows"] = [row for row in old["rows"] if row]
                before = copy.deepcopy(old)
                upgraded = validate(section, upgrade_saved_layout(section, old))
                retained = [[slot for slot in row if slot not in introduced] for row in upgraded["rows"]]
                self.assertEqual([r for r in retained if r], old["rows"])
                self.assertEqual(old, before)


if __name__ == "__main__":
    unittest.main()
