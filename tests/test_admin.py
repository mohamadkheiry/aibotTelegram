from __future__ import annotations

import csv
import io
import json
import os
import stat
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from app.admin import AdminController, DOCUMENTED_COMMANDS
from app.admin_help import ADMIN_HELP
from app.db import Database
from app.keyboards import contains_emoji


class FakeTelegram:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []
        self.documents: list[dict[str, Any]] = []
        self.photos: list[dict[str, Any]] = []
        self.callback_answers: list[dict[str, Any]] = []

    def send_message(self, chat_id: int, text: str, **kwargs: Any) -> dict[str, Any]:
        item = {"chat_id": chat_id, "text": text, **kwargs}
        self.messages.append(item)
        return {"message_id": len(self.messages), **item}

    def send_document(self, chat_id: int, document: Any, **kwargs: Any) -> dict[str, Any]:
        item = {"chat_id": chat_id, "document": document, **kwargs}
        self.documents.append(item)
        return {"message_id": len(self.documents), **item}

    def send_photo(self, chat_id: int, photo: str, **kwargs: Any) -> dict[str, Any]:
        item = {"chat_id": chat_id, "photo": photo, **kwargs}
        self.photos.append(item)
        return {"message_id": len(self.photos), **item}

    def answer_callback_query(self, callback_query_id: str, text: str | None = None, **kwargs: Any) -> bool:
        self.callback_answers.append(
            {"callback_query_id": callback_query_id, "text": text, **kwargs}
        )
        return True


class ForcedJoinTelegram(FakeTelegram):
    def __init__(self) -> None:
        super().__init__()
        self.chat_type = "channel"
        self.membership_status = "administrator"
        self.api_calls: list[tuple[str, dict[str, Any] | None]] = []

    def call(
        self,
        method: str,
        parameters: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.api_calls.append((method, parameters))
        if method == "getChat":
            return {
                "id": (parameters or {}).get("chat_id"),
                "type": self.chat_type,
            }
        if method == "getMe":
            return {"id": 999_001, "is_bot": True}
        if method == "getChatMember":
            return {"status": self.membership_status}
        raise AssertionError(f"unexpected Telegram method: {method}")


class AdminControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = Database(self.root / "bot.sqlite3")
        self.db.initialize()
        self.telegram = FakeTelegram()
        self.settings = SimpleNamespace(data_dir=self.root / "data", currency_label="تومان")
        self.owner_user = self.db.upsert_user(1001, 1001, username="owner", first_name="Owner")
        self.owner = self.db.bootstrap_admin("owner", 1001, role="owner")
        self.notices: list[tuple[int, str]] = []
        self.fulfilled: list[dict[str, Any]] = []
        self.controller = AdminController(
            self.db,
            self.telegram,
            self.settings,
            notify_user=lambda chat_id, text: self.notices.append((chat_id, text)),
            fulfill_order=lambda order: self.fulfilled.append(dict(order)),
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def message(self, text: str, chat_id: int = 1001) -> dict[str, Any]:
        return {"message_id": 1, "chat": {"id": chat_id, "type": "private"}, "text": text}

    def handle(self, text: str, *, user: dict[str, Any] | None = None, admin: dict[str, Any] | None = None) -> bool:
        selected_user = user or self.owner_user
        selected_admin = admin or self.owner
        return self.controller.handle(
            self.message(text, int(selected_user["chat_id"])),
            selected_user,
            selected_admin,
        )

    def product(
        self,
        price: int = 100,
        *,
        product_type: str = "ready",
    ) -> dict[str, Any]:
        category = next(
            (
                item
                for item in self.db.list_categories(
                    parent_id=None, active_only=False
                )
                if item["name"] == "اشتراک"
            ),
            None,
        )
        if category is None:
            category = self.db.create_category("اشتراک")
        return self.db.create_product(
            category["id"],
            "محصول تست",
            product_type=product_type,
            price_amount=price,
            duration_label="یک ماه",
        )

    def settle_order(
        self,
        order: dict[str, Any],
        label: str,
        *,
        now: str | None = None,
    ) -> dict[str, Any]:
        payment = self.db.create_order_payment(
            int(order["id"]),
            "card",
            idempotency_key=f"fixture-payment:{label}",
            now=now,
        )
        self.db.mark_payment_paid(
            int(payment["id"]),
            external_reference=f"fixture-reference:{label}",
            now=now,
        )
        return self.db.get_order(int(order["id"]))

    def test_every_documented_command_is_registered(self) -> None:
        self.assertEqual(set(self.controller._handlers), set(DOCUMENTED_COMMANDS))
        self.assertLessEqual(len(ADMIN_HELP), 3_900)
        self.assertTrue(ADMIN_HELP.rstrip().endswith("</code>"))

    def test_product_reminder_days_require_at_least_one_day(self) -> None:
        product = self.product(product_type="manual")

        self.handle(f"/product_set {product['id']} | reminder_days | 7,0,1")

        self.assertIn("حداقل ۱ روز", self.telegram.messages[-1]["text"])
        stored = self.db.get_product(product["id"])
        self.assertEqual(json.loads(stored["reminder_days_json"]), [7, 3, 1])

    def test_ticket_attachments_are_retrievable_after_restart_by_support(self) -> None:
        customer = self.db.upsert_user(2201, 2201, username="ticket_customer")
        photo_id = "private-ticket-photo-file-id"
        ticket = self.db.create_ticket(
            customer["id"],
            "Attachment recovery",
            "photo body",
            attachment_file_id=photo_id,
            attachment_kind="photo",
            idempotency_key="ticket-attachment-photo",
        )
        photo_message = self.db.list_ticket_messages(ticket["id"])[0]
        document_id = "private-ticket-document-file-id"
        document_message = self.db.add_ticket_message(
            ticket["id"],
            "document body",
            sender_type="user",
            sender_id=customer["id"],
            attachment_file_id=document_id,
            attachment_kind="document",
            idempotency_key="ticket-attachment-document",
        )

        self.handle(f"/ticket {ticket['ticket_number']}")
        rendered = "\n".join(item["text"] for item in self.telegram.messages)
        self.assertNotIn(photo_id, rendered)
        self.assertNotIn(document_id, rendered)
        self.assertIn(f"/ticket_attachment {photo_message['id']}", rendered)
        self.assertIn(f"/ticket_attachment {document_message['id']}", rendered)

        # A fresh controller represents process restart after the original
        # best-effort copy failed. Persisted Telegram file IDs remain usable.
        restarted = AdminController(
            self.db,
            self.telegram,
            self.settings,
            notify_user=lambda chat_id, text: self.notices.append((chat_id, text)),
            fulfill_order=lambda order: self.fulfilled.append(dict(order)),
        )
        support_user = self.db.upsert_user(2202, 2202, username="ticket_support")
        support = self.db.bootstrap_admin("ticket_support", 2202, role="support")
        self.assertTrue(
            restarted.handle(
                self.message(
                    f"/ticket_attachment {photo_message['id']}",
                    int(support_user["chat_id"]),
                ),
                support_user,
                support,
            )
        )
        self.assertEqual(self.telegram.photos[-1]["photo"], photo_id)
        self.assertNotIn(photo_id, self.telegram.photos[-1]["caption"])

        self.assertTrue(
            restarted.handle(
                self.message(f"/ticket_attachment {document_message['id']}"),
                self.owner_user,
                self.owner,
            )
        )
        self.assertEqual(self.telegram.documents[-1]["document"], document_id)

        file_count = len(self.telegram.photos) + len(self.telegram.documents)
        unrelated = self.db.upsert_user(2203, 2203, username="not_an_admin")
        self.assertFalse(
            restarted.handle(
                self.message(
                    f"/ticket_attachment {photo_message['id']}",
                    int(unrelated["chat_id"]),
                ),
                unrelated,
                {},
            )
        )
        self.assertEqual(
            len(self.telegram.photos) + len(self.telegram.documents), file_count
        )

        invalid = {
            **photo_message,
            "attachment_file_id": "invalid-kind-secret",
            "attachment_kind": "video",
        }
        with patch.object(self.db, "get_ticket_message", return_value=invalid):
            restarted.handle(
                self.message(f"/ticket_attachment {photo_message['id']}"),
                self.owner_user,
                self.owner,
            )
        self.assertIn("نوع پیوست", self.telegram.messages[-1]["text"])
        self.assertEqual(
            len(self.telegram.photos) + len(self.telegram.documents), file_count
        )

    def test_support_role_is_limited_but_can_use_direct_messages(self) -> None:
        support_user = self.db.upsert_user(2001, 2001, username="helper")
        support = self.db.bootstrap_admin("helper", 2001, role="support")
        target = self.db.upsert_user(3001, 3001, username="target")

        self.assertTrue(self.handle("/bot_off", user=support_user, admin=support))
        self.assertIsNone(self.db.get_setting("bot_enabled"))
        self.assertIn("مجاز نیست", self.telegram.messages[-1]["text"])

        self.assertTrue(
            self.handle("/message 3001 | سلام", user=support_user, admin=support)
        )
        queued = self.db.claim_outbound_messages(limit=1)
        self.assertEqual(len(queued), 1)
        self.assertEqual(queued[0]["recipient_user_id"], target["id"])
        self.assertEqual(queued[0]["body"], "سلام")

    def test_main_channel_setting_accepts_only_safe_https_t_me_urls(self) -> None:
        valid = "https://t.me/alone_account_channel"
        self.handle(f"/set_channel {valid}")
        self.assertEqual(self.db.get_setting("main_channel_url"), valid)

        for unsafe in (
            "http://t.me/alone_account_channel",
            "tg://resolve?domain=alone_account_channel",
            "https://t.me.evil.example/alone_account_channel",
            "https://t.me@evil.example/alone_account_channel",
        ):
            with self.subTest(unsafe=unsafe):
                self.handle(f"/set_channel {unsafe}")
                self.assertIn("خطا", self.telegram.messages[-1]["text"])
                self.assertEqual(self.db.get_setting("main_channel_url"), valid)

    def test_join_and_product_rule_commands_validate_external_urls(self) -> None:
        telegram = ForcedJoinTelegram()
        self.telegram = telegram
        self.controller.telegram = telegram

        self.handle(
            "/join_add @valid_channel | کانال | "
            "https://telegram.me.evil.example/channel"
        )
        self.assertEqual(self.db.list_force_join_channels(active_only=False), [])
        self.assertEqual(telegram.api_calls, [])
        self.assertIn("خطا", telegram.messages[-1]["text"])

        self.handle(
            "/join_add @valid_channel | کانال | https://telegram.me/valid_channel"
        )
        self.assertEqual(len(self.db.list_force_join_channels(active_only=False)), 1)

        product = self.product()
        valid_rules = "https://example.test/rules"
        self.handle(f"/product_set {product['id']} | rules_url | {valid_rules}")
        self.assertEqual(self.db.get_product(product["id"])["rules_url"], valid_rules)

        self.handle(
            f"/product_set {product['id']} | rules_url | javascript:alert(1)"
        )
        self.assertIn("خطا", self.telegram.messages[-1]["text"])
        self.assertEqual(self.db.get_product(product["id"])["rules_url"], valid_rules)

        self.handle(f"/product_set {product['id']} | rules_url | حذف")
        self.assertIsNone(self.db.get_product(product["id"])["rules_url"])

    def test_admin_enable_disable_skips_username_only_rows(self) -> None:
        self.db.bootstrap_admin("pending_owner", role="owner")
        target = self.db.bootstrap_admin("bound_support", 3003, role="support")

        self.handle("/admin_disable 3003")
        self.assertFalse(self.db.list_admins(active_only=False)[-1]["is_active"])
        self.handle("/admin_enable 3003")

        refreshed = next(
            item
            for item in self.db.list_admins(active_only=False)
            if item["id"] == target["id"]
        )
        self.assertTrue(refreshed["is_active"])

    def test_support_cannot_change_or_complete_orders_but_owner_can(self) -> None:
        support_user = self.db.upsert_user(2101, 2101, username="order-helper")
        support = self.db.bootstrap_admin("order-helper", 2101, role="support")
        buyer = self.db.upsert_user(2102, 2102, username="order-buyer")
        product = self.product(product_type="manual")

        status_order = self.db.create_order(
            buyer["id"],
            product["id"],
            idempotency_key="support-status-order",
        )
        status_command = f"/order_status {status_order['order_number']} paid | denied"
        self.assertTrue(self.handle(status_command, user=support_user, admin=support))
        self.assertEqual(self.db.get_order(status_order["id"])["status"], "pending_payment")
        self.assertIn("مجاز نیست", self.telegram.messages[-1]["text"])

        self.assertTrue(self.handle(status_command))
        self.assertEqual(
            self.db.get_order(status_order["id"])["status"], "pending_payment"
        )
        self.assertIn("/approve_payment", self.telegram.messages[-1]["text"])

        payment = self.db.create_order_payment(
            status_order["id"],
            "card",
            idempotency_key="owner-approved-payment",
        )
        self.db.submit_payment_receipt(payment["id"], "owner-approved-receipt")
        approve_command = f"/approve_payment {payment['payment_number']}"
        self.assertTrue(
            self.handle(approve_command, user=support_user, admin=support)
        )
        self.assertEqual(self.db.get_payment(payment["id"])["status"], "verifying")
        self.assertEqual(
            self.db.get_order(status_order["id"])["status"],
            "awaiting_confirmation",
        )
        self.assertIn("مجاز نیست", self.telegram.messages[-1]["text"])

        self.assertTrue(self.handle(approve_command))
        self.assertEqual(self.db.get_payment(payment["id"])["status"], "paid")
        self.assertEqual(self.db.get_order(status_order["id"])["status"], "paid")
        self.db.update_order_status(status_order["id"], "awaiting_info")
        self.db.set_order_customer_info(status_order["id"], {"text": "customer info"})
        self.db.update_order_status(status_order["id"], "processing")

        complete_command = f"/complete {status_order['order_number']} | secret-delivery"
        self.assertTrue(self.handle(complete_command, user=support_user, admin=support))
        denied_command_order = self.db.get_order(status_order["id"])
        self.assertEqual(denied_command_order["status"], "processing")
        self.assertIsNone(denied_command_order["delivered_payload"])
        self.assertIn("مجاز نیست", self.telegram.messages[-1]["text"])

        self.assertTrue(self.handle(complete_command))
        owner_completed = self.db.get_order(status_order["id"])
        self.assertEqual(owner_completed["status"], "completed")
        self.assertEqual(owner_completed["delivered_payload"], "secret-delivery")

        callback_order = self.db.create_order(
            buyer["id"],
            product["id"],
            idempotency_key="support-complete-callback-order",
        )
        self.settle_order(callback_order, "support-complete-callback")
        self.db.update_order_status(callback_order["id"], "awaiting_info")
        self.db.set_order_customer_info(callback_order["id"], {"text": "callback info"})
        self.db.update_order_status(callback_order["id"], "processing")
        callback_data = f"adm:complete:{callback_order['id']}"
        self.assertTrue(
            self.controller.handle_callback(
                callback_data,
                {"id": "support-complete-denied", "message": {"chat": {"id": 2101}}},
                support_user,
                support,
            )
        )
        denied_callback_order = self.db.get_order(callback_order["id"])
        self.assertEqual(denied_callback_order["status"], "processing")
        self.assertIsNone(denied_callback_order["delivered_payload"])
        self.assertTrue(self.telegram.callback_answers[-1]["show_alert"])

        self.assertTrue(
            self.controller.handle_callback(
                callback_data,
                {"id": "owner-complete-allowed", "message": {"chat": {"id": 1001}}},
                self.owner_user,
                self.owner,
            )
        )
        owner_callback_order = self.db.get_order(callback_order["id"])
        self.assertEqual(owner_callback_order["status"], "completed")
        self.assertIsNotNone(owner_callback_order["delivered_payload"])
        self.assertFalse(self.telegram.callback_answers[-1].get("show_alert", False))

    def test_manual_completion_commits_its_delivery_notice_before_network_send(self) -> None:
        buyer = self.db.upsert_user(2103, 2103, username="manual-crash-buyer")
        product = self.product(product_type="manual")
        order = self.db.create_order(
            buyer["id"], product["id"], idempotency_key="manual-crash-order"
        )
        self.settle_order(order, "manual-crash")
        self.db.update_order_status(order["id"], "awaiting_info")
        self.db.set_order_customer_info(order["id"], {"text": "customer input"})
        self.db.update_order_status(order["id"], "processing")

        with patch.object(
            self.controller,
            "_notify",
            side_effect=RuntimeError("simulated crash before Telegram send"),
        ), self.assertRaises(RuntimeError):
            self.controller._complete(
                f"{order['order_number']} | durable-delivery",
                self.message("/complete"),
                self.owner_user,
                self.owner,
            )

        completed = self.db.get_order(order["id"])
        self.assertEqual(completed["status"], "completed")
        queued = self.db.get_outbound_message_by_idempotency_key(
            f"order:{order['id']}:manual-completion-notice"
        )
        self.assertIsNotNone(queued)
        self.assertEqual(queued["status"], "queued")
        self.assertIn("durable-delivery", queued["body"])

    def test_order_status_rejects_completion_and_refund_shortcuts(self) -> None:
        buyer = self.db.upsert_user(2201, 2201, username="status-buyer")
        product = self.product()

        for index, (status, guidance) in enumerate(
            (("completed", "/complete"), ("refunded", "بازپرداخت")),
            start=1,
        ):
            order = self.db.create_order(
                buyer["id"],
                product["id"],
                idempotency_key=f"forbidden-order-status-{index}",
            )
            self.assertTrue(
                self.handle(f"/order_status {order['order_number']} {status}")
            )
            self.assertEqual(
                self.db.get_order(order["id"])["status"],
                "pending_payment",
            )
            self.assertIn(guidance, self.telegram.messages[-1]["text"])

    def test_ready_orders_reject_manual_completion_and_information_requests(self) -> None:
        buyer = self.db.upsert_user(2202, 2202, username="ready-guard-buyer")
        product = self.product()
        order = self.db.create_order(
            buyer["id"], product["id"], idempotency_key="admin-ready-guard"
        )
        self.settle_order(order, "ready-guard")

        self.handle(f"/complete {order['order_number']} | forbidden-delivery")
        self.assertIn("manual", self.telegram.messages[-1]["text"])
        self.assertEqual(self.db.get_order(order["id"])["status"], "paid")

        self.handle(f"/request_info {order['order_number']} | forbidden-request")
        self.assertIn("manual", self.telegram.messages[-1]["text"])
        self.assertEqual(self.db.get_order(order["id"])["status"], "paid")

        self.controller.handle_callback(
            f"adm:complete:{order['id']}",
            {"id": "ready-completion-denied", "message": {"chat": {"id": 1001}}},
            self.owner_user,
            self.owner,
        )
        self.assertTrue(self.telegram.callback_answers[-1]["show_alert"])
        self.assertIn("manual", self.telegram.callback_answers[-1]["text"])
        self.assertEqual(self.db.get_order(order["id"])["status"], "paid")

    def test_category_update_cycle_guard_and_safe_delete(self) -> None:
        root = self.db.create_category("دسته ریشه")
        child = self.db.create_category("دسته فرزند", parent_id=int(root["id"]))
        leaf = self.db.create_category("دسته موقت")

        self.handle(f"/category_set {child['id']} | name | عنوان تازه")
        self.assertEqual(self.db.get_category(int(child["id"]))["name"], "عنوان تازه")

        self.handle(f"/category_set {root['id']} | parent | {child['id']}")
        self.assertIsNone(self.db.get_category(int(root["id"]))["parent_id"])
        self.assertIn("خطا", self.telegram.messages[-1]["text"])

        self.handle(f"/category_delete {root['id']}")
        self.assertIsNotNone(self.db.get_category(int(root["id"])))
        self.assertIn("خطا", self.telegram.messages[-1]["text"])

        self.handle(f"/category_delete {leaf['id']}")
        self.assertIsNone(self.db.get_category(int(leaf["id"])))

    def test_product_extended_fields_soft_delete_and_type_guard(self) -> None:
        product = self.product()
        destination = self.db.create_category("دسته مقصد")

        self.handle(f"/product_set {product['id']} | name | نام تازه")
        self.handle(f"/product_set {product['id']} | category | {destination['id']}")
        self.handle(f"/product_set {product['id']} | stock_limit | 12")
        self.handle(f"/product_set {product['id']} | type | manual")
        updated = self.db.get_product(int(product["id"]))
        self.assertEqual(updated["name"], "نام تازه")
        self.assertEqual(updated["category_id"], destination["id"])
        self.assertEqual(updated["stock_limit"], 12)
        self.assertEqual(updated["product_type"], "manual")

        self.handle(f"/product_set {product['id']} | stock_limit | none")
        self.assertIsNone(self.db.get_product(int(product["id"]))["stock_limit"])

        stocked = self.product()
        self.db.add_inventory_item(int(stocked["id"]), "stocked-secret")
        self.handle(f"/product_set {stocked['id']} | type | manual")
        self.assertEqual(
            self.db.get_product(int(stocked["id"]))["product_type"],
            "ready",
        )
        self.assertIn("خطا", self.telegram.messages[-1]["text"])

        self.handle(f"/product_delete {product['id']}")
        deleted = self.db.get_product(int(product["id"]))
        self.assertIsNotNone(deleted)
        for field in ("is_active", "is_visible", "is_available", "reserve_enabled"):
            self.assertEqual(deleted[field], 0)

    def test_inventory_enable_delete_and_assigned_guard(self) -> None:
        product = self.product()
        removable = self.db.add_inventory_item(int(product["id"]), "removable-secret")

        self.handle(f"/inventory_disable {removable['id']}")
        self.assertEqual(
            self.db.list_inventory_items(int(product["id"]))[0]["status"],
            "disabled",
        )
        self.handle(f"/inventory_enable {removable['id']}")
        self.assertEqual(
            self.db.list_inventory_items(int(product["id"]))[0]["status"],
            "available",
        )
        self.handle(f"/inventory_delete {removable['id']}")
        self.assertFalse(
            any(
                item["id"] == removable["id"]
                for item in self.db.list_inventory_items(int(product["id"]))
            )
        )

        assigned = self.db.add_inventory_item(int(product["id"]), "assigned-secret")
        buyer = self.db.upsert_user(2301, 2301, username="assigned-buyer")
        self.db.assign_inventory_item_to_user(int(assigned["id"]), int(buyer["id"]))
        self.handle(f"/inventory_enable {assigned['id']}")
        self.assertIn("خطا", self.telegram.messages[-1]["text"])
        self.handle(f"/inventory_delete {assigned['id']}")
        remaining = self.db.list_inventory_items(int(product["id"]))
        self.assertTrue(
            any(
                item["id"] == assigned["id"] and item["status"] == "assigned"
                for item in remaining
            )
        )
        self.assertIn("خطا", self.telegram.messages[-1]["text"])

    def test_manual_inventory_delivery_uses_one_atomically_queued_notice(self) -> None:
        target = self.db.upsert_user(4010, 4010, username="inventory-target")
        product = self.product()
        item = self.db.add_inventory_item(product["id"], "private-delivery-payload")

        with patch.object(self.db, "claim_outbound_message", return_value=None):
            self.handle(f"/inventory_assign {item['id']} {target['chat_id']}")

        self.assertEqual(self.notices, [])
        order = self.db.list_orders(user_id=target["id"])[0]
        queued = self.db.get_outbound_message_by_idempotency_key(
            f"order:{order['id']}:delivery"
        )
        self.assertEqual(order["status"], "completed")
        self.assertEqual(queued["status"], "queued")
        self.assertIn("private-delivery-payload", queued["body"])

        self.controller._deliver_prequeued_notification(
            target,
            queued["body"],
            idempotency_key=queued["idempotency_key"],
        )
        self.assertEqual(len(self.notices), 1)
        self.assertEqual(
            self.db.get_outbound_message_by_idempotency_key(
                queued["idempotency_key"]
            )["status"],
            "sent",
        )
        self.controller._deliver_prequeued_notification(
            target,
            queued["body"],
            idempotency_key=queued["idempotency_key"],
        )
        self.assertEqual(len(self.notices), 1)

    def test_faq_category_and_question_crud(self) -> None:
        self.handle("/faq_category_add راهنما")
        category = next(
            item
            for item in self.db.list_faq_categories(active_only=False)
            if item["name"] == "راهنما"
        )
        self.handle(f"/faq_category_set {category['id']} | name | راهنمای تازه")
        self.handle(f"/faq_category_set {category['id']} | sort_order | 7")
        self.handle(f"/faq_category_toggle {category['id']}")
        updated_category = self.db.get_faq_category(int(category["id"]))
        self.assertEqual(updated_category["name"], "راهنمای تازه")
        self.assertEqual(updated_category["sort_order"], 7)
        self.assertEqual(updated_category["is_active"], 0)

        self.handle("/faq_add راهنمای تازه | پرسش نمونه | پاسخ نمونه")
        faq = next(
            item
            for item in self.db.list_faqs(active_only=False)
            if item["question"] == "پرسش نمونه"
        )
        self.handle(f"/faqs {category['id']}")
        self.assertIn("پرسش نمونه", self.telegram.messages[-1]["text"])
        self.handle(f"/faq_set {faq['id']} | answer | پاسخ تازه")
        self.assertEqual(self.db.get_faq(int(faq["id"]))["answer"], "پاسخ تازه")

        destination = self.db.create_faq_category("دسته دوم")
        self.handle(f"/faq_set {faq['id']} | category | {destination['id']}")
        self.assertEqual(
            self.db.get_faq(int(faq["id"]))["category_id"],
            destination["id"],
        )
        self.handle(f"/faq_category_delete {destination['id']}")
        self.assertIsNotNone(self.db.get_faq_category(int(destination["id"])))
        self.assertIn("خطا", self.telegram.messages[-1]["text"])

        self.handle(f"/faq_delete {faq['id']}")
        self.assertIsNone(self.db.get_faq(int(faq["id"])))
        self.handle(f"/faq_category_delete {destination['id']}")
        self.assertIsNone(self.db.get_faq_category(int(destination["id"])))

    def test_extended_discount_create_and_safe_delete(self) -> None:
        self.handle(
            "/discount_add SAVE10 | percent | 10 | 5 | 0 | 0 | 2026-12-31 | "
            "500 | 2 | 2026-01-01"
        )
        discount = next(
            item for item in self.db.list_discounts() if item["code"] == "SAVE10"
        )
        self.assertEqual(discount["minimum_order_amount"], 500)
        self.assertEqual(discount["per_user_limit"], 2)
        self.assertTrue(discount["starts_at"].startswith("2026-01-01"))
        self.assertTrue(discount["ends_at"].startswith("2027-01-01"))

        self.handle("/discount_delete SAVE10")
        self.assertFalse(any(item["code"] == "SAVE10" for item in self.db.list_discounts()))

        buyer = self.db.upsert_user(2401, 2401, username="discount-buyer")
        product = self.product(1_000)
        order = self.db.create_order(
            int(buyer["id"]),
            int(product["id"]),
            idempotency_key="used-discount-order",
        )
        self.db.create_discount("USED10", discount_type="percent", value=10)
        self.db.apply_discount(int(order["id"]), "USED10")
        self.handle("/discount_delete USED10")
        self.assertTrue(any(item["code"] == "USED10" for item in self.db.list_discounts()))
        self.assertIn("خطا", self.telegram.messages[-1]["text"])

    def test_user_and_order_filters(self) -> None:
        active = self.db.upsert_user(2501, 2501, username="active-filter-user")
        blocked = self.db.upsert_user(2502, 2502, username="blocked-filter-user")
        self.db.set_user_blocked(int(blocked["id"]), True)

        self.handle("/users blocked")
        blocked_text = self.telegram.messages[-1]["text"]
        self.assertIn("2502", blocked_text)
        self.assertNotIn("2501", blocked_text)

        self.handle("/users active")
        active_text = self.telegram.messages[-1]["text"]
        self.assertIn("2501", active_text)
        self.assertNotIn("2502", active_text)

        product = self.product()
        january = self.db.create_order(
            int(active["id"]),
            int(product["id"]),
            idempotency_key="january-filter-order",
            now="2026-01-15T12:00:00+00:00",
        )
        february = self.db.create_order(
            int(active["id"]),
            int(product["id"]),
            idempotency_key="february-filter-order",
            now="2026-02-15T12:00:00+00:00",
        )
        self.settle_order(
            january,
            "january-filter",
            now="2026-01-15T12:01:00+00:00",
        )

        self.handle("/orders paid 2026-01-01 2026-01-31")
        january_text = self.telegram.messages[-1]["text"]
        self.assertIn(january["order_number"], january_text)
        self.assertNotIn(february["order_number"], january_text)

        self.handle("/orders 2026-02-01 2026-02-28")
        february_text = self.telegram.messages[-1]["text"]
        self.assertIn(february["order_number"], february_text)
        self.assertNotIn(january["order_number"], february_text)

    def test_active_and_inactive_user_filters_are_disjoint(self) -> None:
        recent = self.db.upsert_user(2601, 2601, username="recent-user")
        inactive = self.db.upsert_user(
            2602,
            2602,
            username="inactive-user",
            now="2020-01-01T00:00:00+00:00",
        )

        self.handle("/users active")
        active_text = self.telegram.messages[-1]["text"]
        self.assertIn(str(recent["chat_id"]), active_text)
        self.assertNotIn(str(inactive["chat_id"]), active_text)

        self.handle("/users inactive")
        inactive_text = self.telegram.messages[-1]["text"]
        self.assertIn(str(inactive["chat_id"]), inactive_text)
        self.assertNotIn(str(recent["chat_id"]), inactive_text)
        self.assertIn("غیرفعال", inactive_text)

    def test_large_user_lists_are_paged_and_join_lists_are_split_safely(self) -> None:
        for index in range(100):
            self.db.upsert_user(
                20_000 + index,
                20_000 + index,
                username=f"pagination_user_{index:03d}_long",
            )
            self.db.upsert_force_join_channel(
                f"@pagination_channel_{index:03d}",
                f"کانال آزمایشی شماره {index:03d}",
                invite_url=f"https://t.me/pagination_channel_{index:03d}",
            )

        before = len(self.telegram.messages)
        self.handle("/users active")
        user_messages = self.telegram.messages[before:]
        self.assertTrue(all(len(item["text"]) <= 3_800 for item in user_messages))
        self.assertIn("20099", "\n".join(item["text"] for item in user_messages))
        self.assertIn("مجموع: 101", "\n".join(item["text"] for item in user_messages))
        self.assertIn("/users active 2", "\n".join(item["text"] for item in user_messages))

        before = len(self.telegram.messages)
        self.handle("/users active 5")
        fifth_page = "\n".join(
            item["text"] for item in self.telegram.messages[before:]
        )
        self.assertIn("صفحه 5 از 6", fifth_page)
        self.assertIn("20000", fifth_page)

        before = len(self.telegram.messages)
        self.handle("/users active 6")
        last_page = "\n".join(
            item["text"] for item in self.telegram.messages[before:]
        )
        self.assertIn("صفحه 6 از 6", last_page)
        self.assertIn("1001", last_page)

        before = len(self.telegram.messages)
        self.handle("/joins")
        join_messages = self.telegram.messages[before:]
        self.assertGreater(len(join_messages), 1)
        self.assertTrue(all(len(item["text"]) <= 3_800 for item in join_messages))
        self.assertIn(
            "@pagination_channel_099",
            "\n".join(item["text"] for item in join_messages),
        )

    def test_order_and_ticket_indexes_page_every_record_without_clamping(self) -> None:
        buyers = [
            self.db.upsert_user(
                27_001 + index,
                27_001 + index,
                username=f"paged-buyer-{index:02d}",
            )
            for index in range(11)
        ]
        product = self.product()
        orders = [
            self.db.create_order(
                int(buyers[index // 10]["id"]),
                int(product["id"]),
                idempotency_key=f"paged-order-{index:03d}",
            )
            for index in range(105)
        ]
        tickets = [
            self.db.create_ticket(
                int(buyers[0]["id"]),
                f"{index:03d}-" + ("موضوع طولانی " * 24),
                "متن تیکت",
                idempotency_key=f"paged-ticket-{index:03d}",
                now=f"2026-01-01T00:{index // 60:02d}:{index % 60:02d}+00:00",
            )
            for index in range(105)
        ]

        before = len(self.telegram.messages)
        self.handle("/orders 1")
        first_order_page_messages = self.telegram.messages[before:]
        first_order_page = "\n".join(
            item["text"] for item in first_order_page_messages
        )
        self.assertIn("صفحه 1 از 6", first_order_page)
        self.assertIn("مجموع: 105", first_order_page)
        self.assertIn(orders[-1]["order_number"], first_order_page)
        self.assertNotIn(orders[0]["order_number"], first_order_page)
        self.assertTrue(
            all(len(item["text"]) <= 3_800 for item in first_order_page_messages)
        )

        before = len(self.telegram.messages)
        self.handle("/orders 6")
        last_order_page = "\n".join(
            item["text"] for item in self.telegram.messages[before:]
        )
        self.assertIn("صفحه 6 از 6", last_order_page)
        self.assertIn(orders[0]["order_number"], last_order_page)

        expected_ticket_page = self.db.list_tickets(
            status="open", limit=20, offset=0
        )
        before = len(self.telegram.messages)
        self.handle("/tickets open 1")
        first_ticket_page_messages = self.telegram.messages[before:]
        first_ticket_page = "\n".join(
            item["text"] for item in first_ticket_page_messages
        )
        self.assertIn("صفحه 1 از 6", first_ticket_page)
        self.assertIn("مجموع: 105", first_ticket_page)
        for ticket in expected_ticket_page:
            self.assertIn(ticket["ticket_number"], first_ticket_page)
        self.assertTrue(
            all(len(item["text"]) <= 3_800 for item in first_ticket_page_messages)
        )

        before = len(self.telegram.messages)
        self.handle("/tickets open 6")
        last_ticket_page = "\n".join(
            item["text"] for item in self.telegram.messages[before:]
        )
        self.assertIn("صفحه 6 از 6", last_ticket_page)
        self.assertIn(tickets[0]["ticket_number"], last_ticket_page)

    def test_user_history_commands_page_filter_search_and_show_reward_details(self) -> None:
        target = self.db.upsert_user(28_001, 28_001, username="history-target")
        other = self.db.upsert_user(28_002, 28_002, username="history-other")
        product = self.product()
        orders = []
        for index in range(25):
            order = self.db.create_order(
                int(target["id"]),
                int(product["id"]),
                idempotency_key=f"history-order-{index:03d}",
            )
            orders.append(order)
            if index < 22:
                self.db.update_order_status(
                    int(order["id"]),
                    "cancelled",
                    now=f"2026-01-01T00:00:{index:02d}+00:00",
                )
        other_order = self.db.create_order(
            int(other["id"]),
            int(product["id"]),
            idempotency_key="history-other-order",
        )
        for index in range(25):
            self.db.credit_wallet(
                int(target["id"]),
                index + 1,
                reason=f"تراکنش آزمایشی {index:03d}",
                idempotency_key=f"history-wallet-{index:03d}",
                now=f"2026-02-01T00:00:{index:02d}+00:00",
            )

        self.db.create_reward_rule(
            "history-start-a", event_type="start", amount=100
        )
        self.db.create_reward_rule(
            "history-start-b", event_type="start", amount=200
        )
        invitees = []
        for index in range(25):
            invitee = self.db.upsert_user(
                29_000 + index,
                29_000 + index,
                username=f"history-invitee-{index:02d}",
            )
            invitees.append(invitee)
            self.db.record_referral(int(target["id"]), int(invitee["id"]))
            self.db.grant_referral_reward(
                int(invitee["id"]),
                "start",
                f"history-start-{index:03d}",
            )

        before = len(self.telegram.messages)
        self.handle(f"/user_orders {target['chat_id']} cancelled 2")
        order_page = "\n".join(
            item["text"] for item in self.telegram.messages[before:]
        )
        self.assertIn("صفحه 2 از 2", order_page)
        self.assertIn("مجموع: 22", order_page)
        self.assertIn(orders[0]["order_number"], order_page)

        before = len(self.telegram.messages)
        self.handle(
            f"/user_orders {target['chat_id']} {orders[7]['order_number']}"
        )
        exact_order = "\n".join(
            item["text"] for item in self.telegram.messages[before:]
        )
        self.assertIn(orders[7]["order_number"], exact_order)

        self.handle(
            f"/user_orders {target['chat_id']} {other_order['order_number']}"
        )
        self.assertIn("برای این کاربر پیدا نشد", self.telegram.messages[-1]["text"])

        before = len(self.telegram.messages)
        self.handle(f"/user_transactions {target['chat_id']} 4")
        transaction_page = "\n".join(
            item["text"] for item in self.telegram.messages[before:]
        )
        self.assertIn("صفحه 4 از 4", transaction_page)
        self.assertIn("مجموع: 75", transaction_page)
        self.assertIn("تراکنش آزمایشی 000", transaction_page)

        before = len(self.telegram.messages)
        self.handle(f"/user_referrals {target['chat_id']} 2")
        referral_page = "\n".join(
            item["text"] for item in self.telegram.messages[before:]
        )
        self.assertIn("صفحه 2 از 2", referral_page)
        self.assertIn("مجموع: 25", referral_page)
        self.assertIn(str(invitees[0]["chat_id"]), referral_page)
        self.assertIn("2 پاداش", referral_page)

        before = len(self.telegram.messages)
        self.handle(f"/user_rewards {target['chat_id']} 3")
        reward_page = "\n".join(
            item["text"] for item in self.telegram.messages[before:]
        )
        self.assertIn("صفحه 3 از 3", reward_page)
        self.assertIn("مجموع: 50", reward_page)
        self.assertIn("history-invitee-00", reward_page)
        self.assertTrue(
            all(len(item["text"]) <= 3_800 for item in self.telegram.messages[before:])
        )

    def test_user_profile_purchase_dates_come_from_uncapped_aggregate(self) -> None:
        target = self.db.upsert_user(28_500, 28_500, username="aggregate-user")
        product = self.product()
        oldest = self.db.create_order(
            int(target["id"]),
            int(product["id"]),
            idempotency_key="aggregate-oldest-order",
            now="2025-01-01T00:00:00+00:00",
        )
        newest = self.db.create_order(
            int(target["id"]),
            int(product["id"]),
            idempotency_key="aggregate-newest-order",
            now="2026-01-01T00:00:00+00:00",
        )
        oldest = self.settle_order(
            oldest, "aggregate-oldest", now="2025-01-01T00:01:00+00:00"
        )
        newest = self.settle_order(
            newest, "aggregate-newest", now="2026-01-01T00:01:00+00:00"
        )

        before = len(self.telegram.messages)
        with patch.object(self.db, "list_orders", return_value=[newest]):
            self.handle(f"/user {target['chat_id']}")
        profile = "\n".join(
            item["text"] for item in self.telegram.messages[before:]
        )

        self.assertIn(f"اولین خرید: {oldest['paid_at']}", profile)
        self.assertIn(f"آخرین خرید: {newest['paid_at']}", profile)

    def test_admin_add_requires_both_identity_fields_and_prevents_owner_escalation(self) -> None:
        self.handle("/admin_add @newadmin admin")
        self.assertIn("هر دو مقدار", self.telegram.messages[-1]["text"])

        self.handle("/admin_add @newadmin 4001 admin")
        added = next(item for item in self.db.list_admins() if item["chat_id"] == 4001)
        self.assertEqual(added["username"], "newadmin")
        self.assertEqual(added["role"], "admin")

        actor_user = self.db.upsert_user(4001, 4001, username="newadmin")
        self.handle("/admin_add @intruder 4002 owner", user=actor_user, admin=added)
        self.assertFalse(any(item["chat_id"] == 4002 for item in self.db.list_admins()))
        self.assertIn("فقط مالک", self.telegram.messages[-1]["text"])

        self.handle("/admin_add @owner 1001 admin")
        unchanged_owner = next(
            item for item in self.db.list_admins() if int(item["id"]) == int(self.owner["id"])
        )
        self.assertEqual(unchanged_owner["role"], "owner")
        self.assertTrue(unchanged_owner["is_active"])
        self.assertIn("آخرین مالک", self.telegram.messages[-1]["text"])

        self.handle("/admin_add @owner 4999 owner")
        self.assertEqual(
            next(item for item in self.db.list_admins() if int(item["id"]) == int(self.owner["id"]))["chat_id"],
            1001,
        )
        self.assertIn("خطا", self.telegram.messages[-1]["text"])

    def test_sensitive_inventory_is_collected_through_user_state_without_echo(self) -> None:
        product = self.product()
        secret = "email@example.test | password-secret | 2FA-secret"

        self.assertTrue(self.handle(f"/inventory_add {product['id']}"))
        state = self.db.get_user_state(self.owner_user["id"])
        self.assertEqual(state["state"], "admin:inventory")

        self.assertTrue(
            self.controller.handle_state(
                self.message(secret),
                self.owner_user,
                self.owner,
                state,
            )
        )
        self.assertEqual(self.db.inventory_count(product["id"]), 1)
        self.assertIsNone(self.db.get_user_state(self.owner_user["id"]))
        self.assertNotIn(secret, "\n".join(item["text"] for item in self.telegram.messages))

        self.handle(f"/inventory_add {product['id']}")
        duplicate_state = self.db.get_user_state(self.owner_user["id"])
        self.controller.handle_state(
            self.message(secret),
            self.owner_user,
            self.owner,
            duplicate_state,
        )
        self.assertEqual(self.db.inventory_count(product["id"]), 1)
        self.assertIn("already exists", self.telegram.messages[-1]["text"])
        self.assertIsNotNone(self.db.get_user_state(self.owner_user["id"]))

    def test_broadcast_previews_count_and_requires_matching_callback(self) -> None:
        self.db.upsert_user(5001, 5001, username="one")
        self.db.upsert_user(5002, 5002, username="two")

        self.handle("/broadcast_all پیام آزمایشی")
        preview = self.telegram.messages[-1]
        self.assertIn("3", preview["text"])
        buttons = [
            button
            for row in preview["reply_markup"]["inline_keyboard"]
            for button in row
        ]
        self.assertTrue(all(not contains_emoji(button["text"]) for button in buttons))
        self.assertEqual(self.db.claim_outbound_messages(), [])

        confirm_data = buttons[0]["callback_data"]
        handled = self.controller.handle_callback(
            confirm_data,
            {"id": "callback-1", "message": {"chat": {"id": 1001}}},
            self.owner_user,
            self.owner,
        )
        self.assertTrue(handled)
        queued = self.db.claim_outbound_messages()
        self.assertEqual(len(queued), 3)
        self.assertTrue(all(item["body"] == "پیام آزمایشی" for item in queued))
        self.assertEqual(self.db.list_ready_broadcast_summaries(), [])
        for index, item in enumerate(queued):
            self.db.mark_outbound_message(
                item["id"],
                success=index != len(queued) - 1,
                error_text=None if index != len(queued) - 1 else "blocked",
            )
        summary = self.db.list_ready_broadcast_summaries()
        self.assertEqual(len(summary), 1)
        self.assertEqual(summary[0]["target_count"], 3)
        self.assertEqual(summary[0]["sent_count"], 2)
        self.assertEqual(summary[0]["failed_count"], 1)
        self.assertIsNone(self.db.get_user_state(self.owner_user["id"]))
        self.assertEqual(self.telegram.callback_answers[-1]["callback_query_id"], "callback-1")

    def test_broadcast_enqueue_is_idempotent_for_preview_token(self) -> None:
        self.db.upsert_user(5101, 5101, username="recipient")
        kwargs = {
            "actor_user_id": int(self.owner_user["id"]),
            "batch_token": "same-preview",
        }

        first = self.controller._enqueue_broadcast(
            {"kind": "all"}, "متن <آزمایش> & ادامه", int(self.owner["id"]), **kwargs
        )
        second = self.controller._enqueue_broadcast(
            {"kind": "all"}, "متن <آزمایش> & ادامه", int(self.owner["id"]), **kwargs
        )

        self.assertEqual(first["id"], second["id"])
        messages = self.db.claim_outbound_messages()
        self.assertEqual(len(messages), 2)
        self.assertTrue(
            all(
                item["body"] == "متن &lt;آزمایش&gt; &amp; ادامه"
                for item in messages
            )
        )

    def test_reward_rule_accepts_optional_inclusive_date_window(self) -> None:
        product = self.product()
        self.settings.timezone = "Asia/Tehran"

        self.handle(
            f"/reward_add product_purchase | 50000 | {product['id']} | "
            "2026-09-01 | 2026-09-30"
        )

        rules = self.controller._query("SELECT * FROM reward_rules ORDER BY id")
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0]["starts_at"], "2026-08-31T20:30:00+00:00")
        self.assertEqual(rules[0]["ends_at"], "2026-09-30T20:30:00+00:00")

        self.handle(f"/reward_add start | 100 | {product['id']}")
        self.assertIn("PRODUCT_ID", self.telegram.messages[-1]["text"])
        self.assertEqual(
            len(self.controller._query("SELECT * FROM reward_rules ORDER BY id")),
            1,
        )

    def test_combined_reward_rejects_disjoint_product_filters(self) -> None:
        outer = self.product(100)
        second_category = self.db.create_category("اشتراک دوم")
        inner = self.db.create_product(
            second_category["id"],
            "محصول دوم",
            product_type="ready",
            price_amount=200,
            duration_label="یک ماه",
        )

        self.handle(
            f'/reward_add combined | 10 | {outer["id"]} | '
            f'{{"product_ids":[{inner["id"]}]}}'
        )

        self.assertEqual(self.controller._query("SELECT * FROM reward_rules"), [])
        self.assertIn("PRODUCT_ID", self.telegram.messages[-1]["text"])

        self.handle(
            f'/reward_add combined | 10 | {outer["id"]} | '
            f'{{"product_ids":[{outer["id"]}]}}'
        )
        self.assertEqual(
            len(self.controller._query("SELECT * FROM reward_rules")),
            1,
        )

    def test_force_join_revalidates_chat_type_and_bot_role_on_enable(self) -> None:
        telegram = ForcedJoinTelegram()
        self.telegram = telegram
        self.controller.telegram = telegram

        telegram.chat_type = "private"
        self.handle("/join_add @valid_channel | کانال | https://t.me/valid_channel")
        self.assertEqual(self.db.list_force_join_channels(active_only=False), [])
        self.assertIn("خطا:", telegram.messages[-1]["text"])

        telegram.chat_type = "channel"
        self.handle("/join_add @valid_channel | کانال | https://t.me/valid_channel")
        channel = self.db.list_force_join_channels(active_only=False)[0]
        self.handle(f'/join_toggle {channel["id"]}')
        self.assertFalse(
            self.db.list_force_join_channels(active_only=False)[0]["is_active"]
        )

        telegram.membership_status = "member"
        self.handle(f'/join_toggle {channel["id"]}')
        self.assertIn("خطا:", telegram.messages[-1]["text"])
        self.assertFalse(
            self.db.list_force_join_channels(active_only=False)[0]["is_active"]
        )

    def test_backup_uses_online_sqlite_backup_and_sends_file(self) -> None:
        self.handle("/backup")

        self.assertEqual(len(self.telegram.documents), 1)
        path = Path(self.telegram.documents[0]["document"])
        self.assertTrue(path.is_file())
        self.assertNotEqual(path.resolve(), self.db.path)
        self.assertEqual(self.db.list_backups()[0]["status"], "completed")

    @unittest.skipUnless(os.name == "posix", "POSIX permission bits are required")
    def test_backup_command_secures_its_managed_directory_and_file(self) -> None:
        backup_dir = self.settings.data_dir / "backups"
        backup_dir.mkdir(parents=True, mode=0o777)
        backup_dir.chmod(0o777)

        self.handle("/backup")

        path = Path(self.telegram.documents[-1]["document"])
        self.assertEqual(stat.S_IMODE(backup_dir.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_manual_payment_approve_and_reject(self) -> None:
        target = self.db.upsert_user(6001, 6001, username="buyer")
        product = self.product(250)
        first_order = self.db.create_order(target["id"], product["id"], idempotency_key="order-approve")
        first_payment = self.db.create_order_payment(
            first_order["id"], "card", idempotency_key="payment-approve"
        )
        self.db.submit_payment_receipt(first_payment["id"], "receipt-approve")

        approval_events: list[str] = []
        self.controller.notify_user = lambda _chat_id, _text: approval_events.append(
            "success"
        )
        self.controller.fulfill_order = lambda _order: approval_events.append(
            "fulfillment"
        )
        self.handle(f"/approve_payment {first_payment['payment_number']}")
        self.assertEqual(self.db.get_payment(first_payment["id"])["status"], "paid")
        self.assertEqual(self.db.get_order(first_order["id"])["status"], "paid")
        self.assertEqual(approval_events[:2], ["success", "fulfillment"])
        self.controller.notify_user = lambda chat_id, text: self.notices.append(
            (chat_id, text)
        )

        second_order = self.db.create_order(target["id"], product["id"], idempotency_key="order-reject")
        second_payment = self.db.create_order_payment(
            second_order["id"], "card", idempotency_key="payment-reject"
        )
        self.db.submit_payment_receipt(second_payment["id"], "receipt-reject")
        self.handle(f"/reject_payment {second_payment['payment_number']} | فیش نامعتبر")
        self.assertEqual(self.db.get_payment(second_payment["id"])["status"], "failed")
        self.assertEqual(self.db.get_order(second_order["id"])["status"], "pending_payment")
        self.assertIn("فیش نامعتبر", self.notices[-1][1])

    def test_order_status_notifies_without_note_and_persists_same_status_note(self) -> None:
        buyer = self.db.upsert_user(6003, 6003, username="status-notice-buyer")
        product = self.product(250)
        order = self.db.create_order(
            buyer["id"], product["id"], idempotency_key="status-notice-order"
        )

        before = len(self.notices)
        self.handle(f"/order_status {order['order_number']} cancelled")
        self.assertEqual(len(self.notices), before + 1)
        self.assertIn("cancelled", self.notices[-1][1])

        updated = self.db.update_order_status(
            order["id"], "cancelled", admin_note="same-status audit note"
        )
        self.assertEqual(updated["admin_note"], "same-status audit note")

    def test_ticket_close_records_the_acting_admin(self) -> None:
        buyer = self.db.upsert_user(6004, 6004, username="ticket-close-buyer")
        ticket = self.db.create_ticket(
            buyer["id"],
            "موضوع تست بستن",
            "شرح تست",
            idempotency_key="ticket-close-actor",
        )

        self.handle(f"/ticket_close {ticket['ticket_number']}")

        closed = self.db.get_ticket(ticket["id"])
        self.assertEqual(closed["status"], "closed")
        self.assertEqual(closed["assigned_admin_id"], self.owner["id"])

    def test_ticket_reopen_and_reclose_uses_a_new_lifecycle_notice(self) -> None:
        buyer = self.db.upsert_user(6005, 6005, username="ticket-reclose-buyer")
        ticket = self.db.create_ticket(
            buyer["id"],
            "موضوع چرخه تیکت",
            "شرح",
            idempotency_key="ticket-reclose-cycle",
        )

        def handle_with_id(command: str, message_id: int) -> None:
            payload = self.message(command)
            payload["message_id"] = message_id
            self.controller.handle(payload, self.owner_user, self.owner)

        handle_with_id(f"/ticket_close {ticket['ticket_number']}", 101)
        handle_with_id(f"/ticket_status {ticket['ticket_number']} open", 102)
        handle_with_id(f"/ticket_close {ticket['ticket_number']}", 103)
        user_notice_count = len(self.notices)
        handle_with_id(f"/ticket_close {ticket['ticket_number']}", 103)

        self.assertEqual(self.db.get_ticket(ticket["id"])["status"], "closed")
        self.assertEqual(len(self.notices), user_notice_count)
        self.assertIsNotNone(
            self.db.get_outbound_message_by_idempotency_key(
                f"ticket:{ticket['id']}:closed-notice:{self.owner['id']}:101"
            )
        )
        self.assertIsNotNone(
            self.db.get_outbound_message_by_idempotency_key(
                f"ticket:{ticket['id']}:closed-notice:{self.owner['id']}:103"
            )
        )

    def test_admin_payment_review_rejects_missing_receipt_and_non_card_intents(self) -> None:
        target = self.db.upsert_user(6002, 6002, username="review-guard-buyer")
        product = self.product(250)
        order = self.db.create_order(
            target["id"], product["id"], idempotency_key="review-guard-order"
        )
        card = self.db.create_order_payment(
            order["id"], "card", idempotency_key="review-guard-card"
        )

        self.handle(f"/approve_payment {card['payment_number']}")
        self.assertIn("فیش", self.telegram.messages[-1]["text"])
        self.assertEqual(self.db.get_payment(card["id"])["status"], "pending")

        self.db.set_payment_status(card["id"], "cancelled")
        crypto = self.db.create_order_payment(
            order["id"],
            "crypto",
            idempotency_key="review-guard-crypto",
            receipt_file_id="synthetic-crypto-receipt",
        )
        self.handle(f"/approve_payment {crypto['payment_number']}")
        self.assertIn("کارت", self.telegram.messages[-1]["text"])
        self.assertEqual(self.db.get_payment(crypto["id"])["status"], "pending")

    def test_admin_panel_callbacks_and_support_permissions(self) -> None:
        support_user = self.db.upsert_user(8001, 8001, username="callback_helper")
        support = self.db.bootstrap_admin("callback_helper", 8001, role="support")

        for index, data in enumerate(("adm:orders", "adm:tickets", "adm:users"), start=1):
            handled = self.controller.handle_callback(
                data,
                {"id": f"panel-{index}", "message": {"chat": {"id": 8001}}},
                support_user,
                support,
            )
            self.assertTrue(handled)
            self.assertEqual(self.telegram.callback_answers[-1]["show_alert"], False)

        message_count = len(self.telegram.messages)
        self.assertTrue(
            self.controller.handle_callback(
                "adm:settings",
                {"id": "panel-settings-denied", "message": {"chat": {"id": 8001}}},
                support_user,
                support,
            )
        )
        self.assertEqual(len(self.telegram.messages), message_count)
        self.assertTrue(self.telegram.callback_answers[-1]["show_alert"])

        self.db.set_setting("card_number", "6037997512345678")
        self.assertTrue(
            self.controller.handle_callback(
                "adm:settings",
                {"id": "panel-settings-owner", "message": {"chat": {"id": 1001}}},
                self.owner_user,
                self.owner,
            )
        )
        settings_text = self.telegram.messages[-1]["text"]
        self.assertIn("تنظیمات ربات", settings_text)
        self.assertNotIn("6037997512345678", settings_text)

    def test_receipt_callback_approval_and_rejection(self) -> None:
        target = self.db.upsert_user(9001, 9001, username="receipt_buyer")
        product = self.product(400)
        approved_order = self.db.create_order(
            target["id"],
            product["id"],
            idempotency_key="callback-order-approve",
        )
        approved_payment = self.db.create_order_payment(
            approved_order["id"],
            "card",
            idempotency_key="callback-payment-approve",
        )
        self.db.submit_payment_receipt(approved_payment["id"], "callback-receipt-approve")

        self.assertTrue(
            self.controller.handle_callback(
                f"adm:payok:{approved_payment['id']}",
                {"id": "payment-approve", "message": {"chat": {"id": 1001}}},
                self.owner_user,
                self.owner,
            )
        )
        self.assertEqual(self.db.get_payment(approved_payment["id"])["status"], "paid")
        self.assertEqual(self.db.get_order(approved_order["id"])["status"], "paid")
        self.assertIs(type(self.fulfilled[-1]), dict)
        self.assertEqual(self.fulfilled[-1]["id"], approved_order["id"])
        self.assertEqual(self.telegram.callback_answers[-1]["text"], "پرداخت تأیید شد.")

        rejected_order = self.db.create_order(
            target["id"],
            product["id"],
            idempotency_key="callback-order-reject",
        )
        rejected_payment = self.db.create_order_payment(
            rejected_order["id"],
            "card",
            idempotency_key="callback-payment-reject",
        )
        self.db.submit_payment_receipt(rejected_payment["id"], "callback-receipt-reject")
        self.assertTrue(
            self.controller.handle_callback(
                f"adm:payno:{rejected_payment['id']}",
                {"id": "payment-reject", "message": {"chat": {"id": 1001}}},
                self.owner_user,
                self.owner,
            )
        )
        self.assertEqual(self.db.get_payment(rejected_payment["id"])["status"], "failed")
        self.assertEqual(self.db.get_order(rejected_order["id"])["status"], "pending_payment")
        self.assertIn("تأیید نشد", self.notices[-1][1])
        self.assertEqual(self.telegram.callback_answers[-1]["text"], "پرداخت رد شد.")

    def test_report_sends_human_summary_and_utf8_csv(self) -> None:
        today = datetime.now(UTC).date().isoformat()
        self.db.upsert_user(7001, 7001, username="report-user")

        self.handle(f"/report users {today} {today}")

        self.assertIn("گزارش", self.telegram.messages[-1]["text"])
        self.assertEqual(len(self.telegram.documents), 1)
        document = self.telegram.documents[0]
        self.assertTrue(document["filename"].endswith(".csv"))
        self.assertTrue(document["document"].startswith(b"\xef\xbb\xbf"))

    def test_finance_summary_uses_the_csv_paid_at_window(self) -> None:
        buyer = self.db.upsert_user(7101, 7101, username="finance-window")
        product = self.product(900)
        created_at = "2025-12-31T23:00:00+00:00"
        paid_at = "2026-01-01T00:00:00+00:00"
        order = self.db.create_order(
            buyer["id"],
            product["id"],
            idempotency_key="finance-paid-at-window",
            now=created_at,
        )
        payment = self.db.create_order_payment(
            order["id"],
            "card",
            idempotency_key="finance-paid-at-window:payment",
            now=created_at,
        )
        self.db.mark_payment_paid(payment["id"], now=paid_at)

        self.handle("/report finance 2026-01-01 2026-01-31")

        summary = self.telegram.messages[-1]["text"]
        self.assertIn("سفارش‌ها: 1", summary)
        self.assertIn("درآمد ناخالص: 900 تومان", summary)
        exported = list(
            csv.DictReader(
                io.StringIO(self.telegram.documents[-1]["document"].decode("utf-8-sig"))
            )
        )
        self.assertEqual(
            [row["order_number"] for row in exported],
            [order["order_number"]],
        )

    def test_order_report_summary_respects_the_status_filter(self) -> None:
        buyer = self.db.upsert_user(7201, 7201, username="order-summary-filter")
        product = self.product(700, product_type="manual")
        completed = self.db.create_order(
            buyer["id"],
            product["id"],
            idempotency_key="status-summary-completed",
            now="2026-02-01T00:00:00+00:00",
        )
        payment = self.db.create_order_payment(
            completed["id"],
            "card",
            idempotency_key="status-summary-completed:payment",
            now="2026-02-01T00:00:00+00:00",
        )
        self.db.mark_payment_paid(payment["id"], now="2026-02-01T00:01:00+00:00")
        self.db.update_order_status(
            completed["id"],
            "awaiting_info",
            now="2026-02-01T00:01:20+00:00",
        )
        self.db.set_order_customer_info(
            completed["id"],
            {"text": "report fixture customer info"},
            now="2026-02-01T00:01:30+00:00",
        )
        self.db.update_order_status(
            completed["id"],
            "processing",
            now="2026-02-01T00:01:40+00:00",
        )
        self.db.complete_order(
            completed["id"],
            "delivery",
            now="2026-02-01T00:02:00+00:00",
        )
        pending = self.db.create_order(
            buyer["id"],
            product["id"],
            idempotency_key="status-summary-pending",
            now="2026-02-02T00:00:00+00:00",
        )

        self.handle("/report orders pending_payment 2026-02-01 2026-02-28")

        summary = self.telegram.messages[-1]["text"]
        self.assertIn("سفارش‌ها: 1", summary)
        self.assertIn("تکمیل‌شده: 0", summary)
        self.assertIn("درآمد ناخالص: 0 تومان", summary)
        exported = list(
            csv.DictReader(
                io.StringIO(self.telegram.documents[-1]["document"].decode("utf-8-sig"))
            )
        )
        self.assertEqual(
            [row["order_number"] for row in exported],
            [pending["order_number"]],
        )

    def test_csv_neutralizes_formula_prefixes_after_optional_whitespace(self) -> None:
        dangerous = {
            "equals": "=1+1",
            "plus": " +CMD|' /C calc'!A0",
            "minus": "\t-10+20",
            "at": "  @SUM(A1:A2)",
            "safe": "ordinary text",
        }

        payload = self.controller._csv_bytes([dangerous])
        exported = next(csv.DictReader(io.StringIO(payload.decode("utf-8-sig"))))

        for column in ("equals", "plus", "minus", "at"):
            self.assertEqual(exported[column], "'" + dangerous[column])
        self.assertEqual(exported["safe"], dangerous["safe"])


if __name__ == "__main__":
    unittest.main()
