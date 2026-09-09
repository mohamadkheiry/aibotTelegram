"""Actual emitted-button journeys for the remaining September UI reports."""
from __future__ import annotations

import copy
import json
import unittest
from datetime import timedelta
from unittest.mock import patch

from app.admin_forms import ACTIONS
from app.bot import BotApplication
from app.customer_layouts import arrange, upgrade_saved_layout, validate
from app.db import ConflictError, Database, ValidationError
from app.keyboards import contains_emoji
from app.telegram import TelegramError
from app.ticket_ui import notice_markup
from app.utils import utc_now
from tests import test_spec_end_to_end as fixture


class PendingFeedbackTests(unittest.TestCase):
    OWNER = fixture.SourceEndToEndTests.OWNER
    CUSTOMER = fixture.SourceEndToEndTests.CUSTOMER
    setUp = fixture.SourceEndToEndTests.setUp
    tearDown = fixture.SourceEndToEndTests.tearDown
    message = fixture.SourceEndToEndTests.message
    callback = fixture.SourceEndToEndTests.callback
    _take_update_id = fixture.SourceEndToEndTests._take_update_id
    send = fixture.SourceEndToEndTests.send
    screen = fixture.SourceEndToEndTests.screen
    buttons = fixture.SourceEndToEndTests.buttons
    click = fixture.SourceEndToEndTests.click
    state = fixture.SourceEndToEndTests.state
    panel = fixture.SourceEndToEndTests.panel
    customer = fixture.SourceEndToEndTests.customer
    fill_current = fixture.SourceEndToEndTests.fill_current

    def ticket(self, *, closed=False):
        user = self.customer()
        ticket = self.db.create_ticket(user["id"], "موضوع آزمایشی", "درخواست اولیه", idempotency_key="pending-feedback-ticket")
        if closed:
            ticket = self.db.close_ticket(ticket["id"])
        return user, ticket

    def admin_ticket(self, ticket):
        self.panel("tickets")
        self.click(self.OWNER, ACTIONS["ticket"].label)
        self.send(self.OWNER, text=ticket["ticket_number"])
        self.click(self.OWNER, ending=":pick:0")

    def reply(self, ticket, text="پاسخ آزمایشی"):
        self.admin_ticket(ticket)
        self.click(self.OWNER, ACTIONS["ticket_reply"].label)
        self.send(self.OWNER, text=text)
        return self.click(self.OWNER, "تأیید و اجرا")

    def test_reopen_enters_durable_reply_input_and_delivers_first_text_once(self):
        user, ticket = self.ticket(closed=True)
        self.app.show_tickets(user)
        self.click(self.CUSTOMER, data=f"ticket:{ticket['id']}")
        self.click(self.CUSTOMER, "باز کردن مجدد تیکت")
        update = self.click(self.CUSTOMER, "باز کردن مجدد تیکت")
        self.assertEqual(self.db.get_user_state(user["id"])["state"], "ticket_reply")
        self.assertIsNot(self.app.process_update_safe(copy.deepcopy(update)), False)
        self.assertEqual(self.db.get_user_state(user["id"])["data"]["ticket_id"], ticket["id"])
        self.app = BotApplication(self.settings, Database(self.db.path), self.telegram)
        message = self.send(self.CUSTOMER, text="اولین پیام بعد از بازگشایی")
        self.assertIsNot(self.app.process_update_safe(copy.deepcopy(message)), False)
        self.assertEqual(self.db.count_ticket_messages(ticket["id"]), 2)
        notices = [m for m in self.telegram.messages if m["chat_id"] == self.OWNER["id"] and "اولین پیام بعد از بازگشایی" in m["text"]]
        self.assertEqual(len(notices), 1)

    def test_reply_notice_has_real_response_and_close_buttons_and_replay_is_once(self):
        user, ticket = self.ticket()
        update = self.reply(ticket)
        self.assertEqual(self.db.count_ticket_messages(ticket["id"]), 2)
        notice = copy.deepcopy(self.screen(self.CUSTOMER))
        labels = [b["text"] for b in self.buttons(self.CUSTOMER)]
        self.assertIn("ارسال پاسخ", labels)
        self.assertIn("بستن تیکت", labels)
        self.assertIsNot(self.app.process_update_safe(copy.deepcopy(update)), False)
        self.assertEqual(self.db.count_ticket_messages(ticket["id"]), 2)
        self.click(self.CUSTOMER, "ارسال پاسخ", screen=notice)
        self.send(self.CUSTOMER, text="ادامه درخواست")
        self.assertEqual(self.db.count_ticket_messages(ticket["id"]), 3)
        self.click(self.CUSTOMER, "بستن تیکت")
        self.click(self.CUSTOMER, "بستن تیکت")
        self.assertEqual(self.db.get_ticket(ticket["id"])["status"], "closed")
        self.assertIsNone(self.db.get_user_state(user["id"]))

    def test_reply_notice_keyboard_is_retained_after_network_failure_and_restart(self):
        _user, ticket = self.ticket()
        real_send = self.telegram.send_message
        def fail_customer(chat_id, text, **kwargs):
            if chat_id == self.CUSTOMER["id"]:
                raise TelegramError("synthetic delivery failure")
            return real_send(chat_id, text, **kwargs)
        with patch.object(self.telegram, "send_message", side_effect=fail_customer):
            self.reply(ticket)
        with self.db._read() as connection:
            row = dict(connection.execute("SELECT * FROM outbound_messages WHERE idempotency_key LIKE ?", (f"ticket:{ticket['id']}:admin-notice:%",)).fetchone())
        self.assertEqual(row["status"], "queued")
        canonical = row["reply_markup_json"]
        self.app = BotApplication(self.settings, Database(self.db.path), self.telegram)
        claim = self.app.db.claim_outbound_messages
        with patch.object(self.app.db, "claim_outbound_messages", side_effect=lambda **kw: claim(now=utc_now() + timedelta(minutes=1), **kw)):
            self.app._deliver_outbound_messages()
        self.assertIn("بستن تیکت", [b["text"] for b in self.buttons(self.CUSTOMER)])
        final = self.db.get_outbound_message_by_idempotency_key(row["idempotency_key"])
        self.assertEqual(final["status"], "sent")
        self.assertEqual(final["reply_markup_json"], canonical)

    def test_closed_ticket_is_not_replyable_and_stale_form_gets_persian_error(self):
        _user, ticket = self.ticket()
        self.admin_ticket(ticket)
        self.click(self.OWNER, ACTIONS["ticket_reply"].label)
        self.send(self.OWNER, text="نباید ارسال شود")
        self.db.close_ticket(ticket["id"])
        before = len(self.telegram.messages)
        self.click(self.OWNER, "تأیید و اجرا")
        self.assertEqual(self.db.count_ticket_messages(ticket["id"]), 1)
        texts = "\n".join(m["text"] for m in self.telegram.messages[before:])
        self.assertIn("این تیکت بسته شده", texts)
        self.assertNotIn("closed ticket cannot", texts)
        self.admin_ticket(ticket)
        self.assertNotIn(ACTIONS["ticket_reply"].label, [b["text"] for b in self.buttons(self.OWNER)])
        self.assertIn(ACTIONS["ticket_status"].label, [b["text"] for b in self.buttons(self.OWNER)])

    def test_ticket_list_searches_username_and_opens_selected_ticket(self):
        user, ticket = self.ticket()
        self.panel("tickets")
        self.click(self.OWNER, ACTIONS["tickets"].label)
        self.click(self.OWNER, "همه وضعیت‌ها")
        self.assertIn("جست‌وجوی تیکت", [b["text"] for b in self.buttons(self.OWNER)])
        self.click(self.OWNER, "جست‌وجوی تیکت")
        self.send(self.OWNER, text="@" + user["username"])
        self.assertEqual(self.state()["option_total"], 1)
        self.assertEqual(self.state()["options"][0][0], ticket["ticket_number"])
        self.assertIn(user["username"], self.state()["options"][0][1])
        before = len(self.telegram.messages)
        self.click(self.OWNER, ending=":pick:0")
        content = "\n".join(m["text"] for m in self.telegram.messages[before:])
        self.assertIn(str(user["chat_id"]), content)
        self.assertIn("<blockquote>درخواست اولیه</blockquote>", content)

    def test_customer_conversation_escapes_user_html_and_preserves_staff_rich_text(self):
        user, ticket = self.ticket()
        self.db.add_ticket_message(ticket["id"], "<b>متن معمولی</b>", sender_type="user", sender_id=user["id"], idempotency_key="escaped")
        self.reply(ticket, "html:<b>پاسخ پررنگ</b> <blockquote>نقل قول</blockquote>")
        self.click(self.CUSTOMER, "مشاهده تیکت")
        text = self.screen(self.CUSTOMER)["text"]
        self.assertIn("&lt;b&gt;متن معمولی&lt;/b&gt;", text)
        self.assertIn("<b>پاسخ پررنگ</b>", text)
        self.assertNotIn("<blockquote><blockquote>", text)
        self.assertIn("شما", text)
        self.assertIn("پشتیبانی", text)

    def test_message_recipient_starts_empty_then_selects_searched_user(self):
        user = self.customer()
        self.panel("broadcast")
        self.click(self.OWNER, "ارسال پیام تکی")
        self.assertEqual(self.state()["options"], [])
        self.assertIn("ابتدا نام", self.screen(self.OWNER)["text"])
        self.send(self.OWNER, text="@" + user["username"])
        self.assertEqual(self.state()["option_total"], 1)
        self.click(self.OWNER, ending=":pick:0")
        self.assertEqual(self.state()["values"]["target"], str(user["chat_id"]))
        self.assertEqual(self.state()["status"], "editing")
        self.click(self.OWNER, "لغو و بازگشت")
        self.assertEqual(self.db.wallet_balance(user["id"]), 0)

    def test_users_list_exposes_search_and_accepts_persian_chat_id(self):
        user = self.customer()
        self.panel("users")
        self.click(self.OWNER, ACTIONS["users"].label)
        self.fill_current({"mode": "all"})
        self.click(self.OWNER, "جست‌وجوی کاربر")
        digits = str(user["chat_id"]).translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))
        self.send(self.OWNER, text=digits)
        self.assertEqual(self.state()["option_total"], 1)

    def test_faq_category_drilldown_edit_locked_target_search_and_return(self):
        category = self.db.create_faq_category("دسته تست")
        other = self.db.create_faq_category("دسته دیگر")
        faq = self.db.create_faq("پرسش هدف", "جواب اولیه", category_id=category["id"])
        self.db.create_faq("پرسش بیرونی", "نباید اینجا دیده شود", category_id=other["id"])
        self.panel("faq")
        self.click(self.OWNER, ACTIONS["faq_categories"].label)
        self.click(self.OWNER, data=f"adm:ui:faqcat:{category['id']}")
        self.assertTrue(any(b.get("callback_data") == f"adm:ui:faqitem:{faq['id']}" for b in self.buttons(self.OWNER)))
        self.send(self.OWNER, text="پرسش هدف")
        self.assertEqual(self.state()["result_search"], "پرسش هدف")
        self.click(self.OWNER, data=f"adm:ui:faqitem:{faq['id']}")
        self.assertIn("جواب اولیه", self.screen(self.OWNER)["text"])
        self.click(self.OWNER, "ویرایش پرسش و پاسخ")
        self.assertEqual(self.state()["minimum_step"], 1)
        self.click(self.OWNER, "پاسخ")
        self.send(self.OWNER, text="جواب تازه")
        self.click(self.OWNER, "تأیید و اجرا")
        self.assertEqual(self.db.get_faq(faq["id"])["answer"], "جواب تازه")
        self.click(self.OWNER, "بازگشت به بخش انتخاب‌شده")
        self.assertEqual(self.state()["values"]["target"], str(category["id"]))

    def test_faq_add_delete_and_category_delete_return_to_surviving_parent(self):
        category = self.db.create_faq_category("دسته موقت")
        self.panel("faq")
        self.click(self.OWNER, ACTIONS["faq_categories"].label)
        self.click(self.OWNER, data=f"adm:ui:faqcat:{category['id']}")
        self.click(self.OWNER, "افزودن پرسش در این دسته")
        self.send(self.OWNER, text="پرسش تازه")
        self.send(self.OWNER, text="جواب تازه")
        self.click(self.OWNER, "تأیید و اجرا")
        faq = self.db.list_faqs(category_id=category["id"], active_only=False)[0]
        self.click(self.OWNER, "بازگشت به بخش انتخاب‌شده")
        self.click(self.OWNER, data=f"adm:ui:faqitem:{faq['id']}")
        self.click(self.OWNER, "حذف این پرسش")
        self.assertIsNotNone(self.db.get_faq(faq["id"]))
        self.click(self.OWNER, "تأیید و اجرا")
        self.assertIsNone(self.db.get_faq(faq["id"]))
        self.click(self.OWNER, "بازگشت به بخش انتخاب‌شده")
        self.click(self.OWNER, "حذف این دسته")
        self.click(self.OWNER, "تأیید و اجرا")
        self.click(self.OWNER, "بازگشت به بخش انتخاب‌شده")
        self.assertEqual(self.state()["action"], "faq_categories")

    def test_compact_transaction_button_keeps_full_owner_scoped_detail(self):
        user = self.customer()
        self.db.credit_wallet(user["id"], 12345, reason="شرح تراکنش آزمایشی", idempotency_key="compact-transaction")
        self.app.show_transactions(user)
        button = next(b for b in self.buttons(self.CUSTOMER) if b.get("callback_data", "").startswith("transaction:"))
        self.assertLess(len(button["text"]), 40)
        self.assertFalse(contains_emoji(button["text"]))
        self.assertIn("12,345", button["text"])
        self.click(self.CUSTOMER, data=button["callback_data"])
        text = self.screen(self.CUSTOMER)["text"]
        for expected in ("تاریخ و ساعت", "نوع:", "روش پرداخت", "شرح تراکنش آزمایشی"):
            self.assertIn(expected, text)

    def test_notice_layout_upgrade_preserves_old_order_and_conditional_buttons(self):
        previous = {"rows": [["back"], ["reply", "view"]], "columns": 2, "reverse": True, "item_order": []}
        upgraded = validate("ticket_notice", upgrade_saved_layout("ticket_notice", previous))
        self.assertEqual(upgraded["rows"][-1], ["reply", "view"])
        for closed in (True, False):
            markup = notice_markup(123, closed=closed)
            arranged = arrange("ticket_notice", markup, upgraded)
            expected = sorted(b["callback_data"] for r in markup["inline_keyboard"] for b in r)
            actual = sorted(b["callback_data"] for r in arranged["inline_keyboard"] for b in r)
            self.assertEqual(actual, expected)

    def test_reply_markup_collision_rolls_back_and_does_not_duplicate_message(self):
        user, ticket = self.ticket()
        admin = next(a for a in self.db.list_admins() if a["chat_id"] == self.OWNER["id"])
        kwargs = dict(sender_type="admin", sender_id=admin["id"], idempotency_key="frozen-ticket-reply",
                      outbound_body="اعلان آزمایشی", outbound_idempotency_key="frozen-ticket-notice")
        markup = notice_markup(ticket["id"])
        self.db.add_ticket_message(ticket["id"], "متن", outbound_reply_markup=markup, **kwargs)
        with self.assertRaises(ConflictError):
            self.db.add_ticket_message(ticket["id"], "متن", outbound_reply_markup=notice_markup(ticket["id"] + 1), **kwargs)
        self.assertEqual(self.db.count_ticket_messages(ticket["id"]), 2)
        row = self.db.get_outbound_message_by_idempotency_key("frozen-ticket-notice")
        self.assertEqual(json.loads(row["reply_markup_json"]), markup)
        with self.assertRaises(ValidationError):
            self.db.add_ticket_message(ticket["id"], "متن دیگر", sender_type="user", sender_id=user["id"],
                                       idempotency_key="orphan-markup", outbound_reply_markup=markup)

    def test_faq_toggle_freezes_preview_target_across_concurrent_change_and_replay(self):
        category = self.db.create_faq_category("دسته وضعیت")
        faq = self.db.create_faq("پرسش وضعیت", "پاسخ", category_id=category["id"])
        self.panel("faq")
        self.click(self.OWNER, ACTIONS["faq_categories"].label)
        self.click(self.OWNER, data=f"adm:ui:faqcat:{category['id']}")
        self.click(self.OWNER, "غیرفعال‌کردن این دسته")
        self.assertIn("پس از تأیید: غیرفعال", self.screen(self.OWNER)["text"])
        self.db.set_faq_category_active(category["id"], False)
        update = self.click(self.OWNER, "تأیید و اجرا")
        self.app.process_update_safe(copy.deepcopy(update))
        self.assertFalse(self.db.get_faq_category(category["id"])["is_active"])
        self.click(self.OWNER, "بازگشت به بخش انتخاب‌شده")
        self.click(self.OWNER, data=f"adm:ui:faqitem:{faq['id']}")
        self.click(self.OWNER, "غیرفعال‌کردن این پرسش")
        self.db.set_faq_active(faq["id"], False)
        update = self.click(self.OWNER, "تأیید و اجرا")
        self.app.process_update_safe(copy.deepcopy(update))
        self.assertFalse(self.db.get_faq(faq["id"])["is_active"])

    def test_faq_pagination_and_search_keep_all_matching_buttons(self):
        category = self.db.create_faq_category("دسته بزرگ")
        for number in range(23):
            self.db.create_faq(f"پرسش مشترک {number}", "پاسخ", category_id=category["id"])
        self.panel("faq")
        self.click(self.OWNER, ACTIONS["faq_categories"].label)
        before = len(self.telegram.messages)
        self.click(self.OWNER, data=f"adm:ui:faqcat:{category['id']}")
        self.assertIn("برای جست‌وجوی پرسش", "\n".join(m["text"] for m in self.telegram.messages[before:]))
        def ids():
            return {b["callback_data"] for b in self.buttons(self.OWNER) if b.get("callback_data", "").startswith("adm:ui:faqitem:")}
        first = ids()
        self.assertEqual(len(first), 20)
        self.click(self.OWNER, "صفحه بعد")
        self.assertEqual(len(ids()), 3)
        self.assertFalse(ids() & first)
        self.send(self.OWNER, text="مشترک 22")
        self.assertEqual(len(ids()), 1)
        self.assertEqual(self.state()["result_page"], 1)
        self.assertEqual(self.state()["list_pages"], 1)

    def test_reopen_queue_failure_rolls_back_status_and_input_state(self):
        user, ticket = self.ticket(closed=True)
        self.db.set_user_state(user["id"], "ticket_status_confirm", {"test": "preserved"})
        with patch.object(self.db, "_queue_user_message_in_transaction", side_effect=RuntimeError("synthetic rollback")):
            with self.assertRaises(RuntimeError):
                self.db.set_user_ticket_status(ticket["id"], user["id"], "open", expected_status="closed",
                    expected_updated_at=ticket["updated_at"], expected_message_count=1,
                    idempotency_key="failed-reopen", body="اعلان", reply_markup=notice_markup(ticket["id"]), resume_reply=True)
        self.assertEqual(self.db.get_ticket(ticket["id"])["status"], "closed")
        self.assertEqual(self.db.get_user_state(user["id"])["data"], {"test": "preserved"})
        self.assertIsNone(self.db.get_outbound_message_by_idempotency_key("failed-reopen"))

    def test_long_non_bmp_conversation_preserves_text_in_safe_quoted_pages(self):
        import html
        import re
        user, ticket = self.ticket()
        body = "\U0001f642" * 3000
        self.db.add_ticket_message(ticket["id"], body, sender_type="user", sender_id=user["id"], idempotency_key="long-quote")
        before = len(self.telegram.messages)
        self.admin_ticket(ticket)
        messages = [m["text"] for m in self.telegram.messages[before:]]
        self.assertEqual(sum(m.count("\U0001f642") for m in messages), 3000)
        self.assertTrue(all(len(html.unescape(re.sub(r"<[^>]*>", "", m)).encode("utf-16-le")) // 2 < 4096 for m in messages))
        self.app.show_tickets(user)
        self.click(self.CUSTOMER, data=f"ticket:{ticket['id']}")
        texts = []
        for _ in range(20):
            texts.append(self.screen(self.CUSTOMER)["text"])
            if not any(b["text"] == "قدیمی‌تر" for b in self.buttons(self.CUSTOMER)):
                break
            self.click(self.CUSTOMER, "قدیمی‌تر")
        self.assertEqual(sum(m.count("\U0001f642") for m in texts), 3000)
        self.assertTrue(all(len(html.unescape(re.sub(r"<[^>]*>", "", m)).encode("utf-16-le")) // 2 < 4096 for m in texts))
