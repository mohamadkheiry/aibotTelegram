from __future__ import annotations

import hashlib
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from typing import Any
from unittest.mock import Mock

from app.bot import BotApplication
from app.config import Settings
from app.db import ConflictError, Database, ValidationError
from app.payment_server import ConfirmationOutcome
from app.utils import utc_now


class RecordingTelegram:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []
        self.photos: list[dict[str, Any]] = []
        self.documents: list[dict[str, Any]] = []
        self.copies: list[dict[str, Any]] = []
        self.answers: list[dict[str, Any]] = []
        self.stop_event: Any = None

    def set_stop_event(self, event: Any) -> None:
        self.stop_event = event

    def call(self, method: str, _parameters: Any = None) -> Any:
        if method == "getMe":
            return {"id": 999, "username": "release_test_bot"}
        return True

    def send_message(self, chat_id: int, text: str, **kwargs: Any) -> dict[str, Any]:
        result = {
            "message_id": len(self.messages) + 1,
            "chat_id": int(chat_id),
            "text": text,
            **kwargs,
        }
        self.messages.append(result)
        return result

    def send_photo(self, chat_id: int, photo: str, **kwargs: Any) -> dict[str, Any]:
        result = {"chat_id": int(chat_id), "photo": photo, **kwargs}
        self.photos.append(result)
        return result

    def send_document(self, chat_id: int, document: Any, **kwargs: Any) -> dict[str, Any]:
        result = {"chat_id": int(chat_id), "document": document, **kwargs}
        self.documents.append(result)
        return result

    def copy_message(
        self, chat_id: int, from_chat_id: int, message_id: int, **kwargs: Any
    ) -> dict[str, Any]:
        result = {
            "chat_id": int(chat_id),
            "from_chat_id": int(from_chat_id),
            "message_id": int(message_id),
            **kwargs,
        }
        self.copies.append(result)
        return result

    def answer_callback_query(self, callback_id: str, **kwargs: Any) -> bool:
        self.answers.append({"id": callback_id, **kwargs})
        return True

    def close(self) -> None:
        return None


class ReleaseFinancialSafetyTests(unittest.TestCase):
    OWNER_CHAT_ID = 9001

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.settings = Settings(
            bot_token="unit-test-token",
            database_path=root / "bot.sqlite3",
            data_dir=root / "data",
            bootstrap_admin_username="release_owner",
            bootstrap_admin_chat_id=self.OWNER_CHAT_ID,
            job_interval_seconds=3600,
        )
        self.db = Database(self.settings.database_path)
        self.telegram = RecordingTelegram()
        self.app = BotApplication(self.settings, self.db, self.telegram)  # type: ignore[arg-type]
        self.app.initialize()
        self.db.set_setting("completion_notice_pending", False)
        self.owner_user = self.db.upsert_user(
            self.OWNER_CHAT_ID,
            self.OWNER_CHAT_ID,
            username="release_owner",
        )
        self.owner = next(
            admin
            for admin in self.db.list_admins(active_only=True)
            if int(admin["chat_id"]) == self.OWNER_CHAT_ID
        )
        self.admin = self.app.admin_controller
        self.category = self.db.create_category("Release products")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def handle_admin(self, command: str, *, message_id: int = 1) -> None:
        self.admin.handle(
            {
                "message_id": message_id,
                "chat": {"id": self.OWNER_CHAT_ID, "type": "private"},
                "text": command,
            },
            self.owner_user,
            self.owner,
        )

    def manual_processing_order(self, suffix: str = "one") -> tuple[dict, dict]:
        user = self.db.upsert_user(
            20_000 + len(suffix),
            20_000 + len(suffix),
            username=f"manual_{suffix}",
        )
        product = self.db.create_product(
            self.category["id"],
            f"Manual {suffix}",
            product_type="manual",
            price_amount=1_000,
            idempotency_key=f"manual-product:{suffix}",
        )
        order = self.db.create_order(
            user["id"], product["id"], idempotency_key=f"manual-order:{suffix}"
        )
        payment = self.db.create_order_payment(
            order["id"], "card", idempotency_key=f"manual-payment:{suffix}"
        )
        self.db.mark_payment_paid(payment["id"])
        self.db.update_order_status(order["id"], "awaiting_info")
        self.db.set_order_customer_info(order["id"], {"text": "customer info"})
        self.db.update_order_status(order["id"], "processing")
        return user, self.db.get_order(order["id"])  # type: ignore[return-value]

    def test_long_delivery_is_rejected_before_inventory_or_order_mutation(self) -> None:
        product = self.db.create_product(
            self.category["id"],
            "Ready",
            product_type="ready",
            price_amount=1_000,
            idempotency_key="long-ready-product",
        )
        with self.assertRaises(ValidationError):
            self.db.add_inventory_item(product["id"], "&" * 1_000)
        self.assertEqual(self.db.inventory_count(product["id"]), 0)

        item = self.db.add_inventory_item(product["id"], "short-secret")
        with self.assertRaises(ValidationError):
            self.db.update_product(
                product["id"], delivery_instructions="&" * 1_000
            )
        self.assertIsNone(self.db.get_product(product["id"])["delivery_instructions"])

        buyer = self.db.upsert_user(30_001, 30_001, username="legacy_long")
        order = self.db.create_order(
            buyer["id"], product["id"], idempotency_key="legacy-long-order"
        )
        payment = self.db.create_order_payment(
            order["id"], "card", idempotency_key="legacy-long-payment"
        )
        self.db.mark_payment_paid(payment["id"])
        legacy_payload = "&" * 1_000
        connection = sqlite3.connect(self.db.path)
        try:
            connection.execute(
                "UPDATE inventory_items SET payload = ?, payload_hash = ? WHERE id = ?",
                (
                    legacy_payload,
                    hashlib.sha256(legacy_payload.encode()).hexdigest(),
                    item["id"],
                ),
            )
            connection.commit()
        finally:
            connection.close()
        with self.assertRaises(ValidationError):
            self.db.assign_inventory(order["id"])
        self.assertEqual(self.db.get_order(order["id"])["status"], "paid")
        self.assertEqual(
            self.db.list_inventory_items(product["id"])[0]["status"], "available"
        )
        self.db.update_inventory_item_payload(item["id"], "short-secret")
        self.db.assign_inventory(order["id"])
        self.assertEqual(self.db.get_order(order["id"])["status"], "completed")
        with self.assertRaises(ValidationError):
            self.db.update_product(
                product["id"], delivery_instructions="&" * 1_000
            )

        _user, manual = self.manual_processing_order("length")
        self.handle_admin(f"/complete {manual['order_number']} | {'X' * 3_900}")
        self.assertEqual(self.db.get_order(manual["id"])["status"], "processing")
        self.assertIsNone(
            self.db.get_outbound_message_by_idempotency_key(
                f"order:{manual['id']}:manual-completion-notice"
            )
        )
        short_notice = (
            "سفارش شما تکمیل شد."
            f"\nشماره سفارش: <code>{manual['order_number']}</code>"
            "\n\nshort delivery"
        )
        notice_key = f"order:{manual['id']}:manual-completion-notice"
        self.db.complete_order(
            manual["id"],
            "short delivery",
            outbound_body=short_notice,
            outbound_idempotency_key=notice_key,
        )
        replay = self.db.complete_order(
            manual["id"],
            "short delivery",
            outbound_body=short_notice,
            outbound_idempotency_key=notice_key,
        )
        self.assertEqual(replay["status"], "completed")
        with self.assertRaises(ConflictError):
            self.db.complete_order(
                manual["id"],
                "short delivery",
                outbound_body=f"{short_notice}!",
                outbound_idempotency_key=notice_key,
            )

    def test_status_rejection_and_info_notice_commit_atomically(self) -> None:
        _user, manual = self.manual_processing_order("atomic-info")
        request_body = "اطلاعات سفارش نیاز به اصلاح دارد.\nمتن دقیق اصلاح"
        request_key = f"order:{manual['id']}:info-correction:atomic"
        self.db.update_order_status(
            manual["id"],
            "awaiting_info",
            admin_note="متن دقیق اصلاح",
            outbound_body=request_body,
            outbound_idempotency_key=request_key,
        )
        self.assertEqual(self.db.get_order(manual["id"])["status"], "awaiting_info")
        self.assertEqual(
            self.db.get_outbound_message_by_idempotency_key(request_key)["body"],
            request_body,
        )
        self.db.update_order_status(
            manual["id"],
            "awaiting_info",
            admin_note="متن دقیق اصلاح",
            outbound_body=request_body,
            outbound_idempotency_key=request_key,
        )
        with self.assertRaises(ConflictError):
            self.db.update_order_status(
                manual["id"],
                "awaiting_info",
                admin_note="نباید commit شود",
                outbound_body="different body",
                outbound_idempotency_key=request_key,
            )
        self.assertEqual(self.db.get_order(manual["id"])["admin_note"], "متن دقیق اصلاح")

        cancelled = self.db.create_order(
            manual["user_id"],
            manual["product_id"],
            idempotency_key="atomic-status-order",
        )
        status_body = "وضعیت سفارش لغو شد."
        status_key = f"order:{cancelled['id']}:status:cancelled:atomic"
        self.db.update_order_status(
            cancelled["id"],
            "cancelled",
            outbound_body=status_body,
            outbound_idempotency_key=status_key,
        )
        self.assertEqual(self.db.get_order(cancelled["id"])["status"], "cancelled")
        self.assertIsNotNone(self.db.get_outbound_message_by_idempotency_key(status_key))

        rejected_order = self.db.create_order(
            manual["user_id"],
            manual["product_id"],
            idempotency_key="atomic-reject-order",
        )
        payment = self.db.create_order_payment(
            rejected_order["id"], "card", idempotency_key="atomic-reject-payment"
        )
        self.db.submit_payment_receipt(payment["id"], "atomic-receipt")
        rejection_body = "پرداخت شما رد شد.\nدلیل: تست اتمیک"
        rejection_key = f"payment:{payment['id']}:admin-rejected-notice"
        self.db.set_payment_status(
            payment["id"],
            "failed",
            raw_payload={"reason": "تست اتمیک"},
            outbound_body=rejection_body,
            outbound_idempotency_key=rejection_key,
        )
        self.assertEqual(self.db.get_payment(payment["id"])["status"], "failed")
        self.assertEqual(
            self.db.get_outbound_message_by_idempotency_key(rejection_key)["body"],
            rejection_body,
        )

    def test_mutating_admin_notices_validate_before_domain_change(self) -> None:
        _user, order = self.manual_processing_order("notice")
        self.handle_admin(
            f"/request_info {order['order_number']} | {'R' * 3_850}",
            message_id=11,
        )
        self.assertEqual(self.db.get_order(order["id"])["status"], "processing")

        pending = self.db.create_order(
            order["user_id"],
            order["product_id"],
            idempotency_key="long-order-status",
        )
        self.handle_admin(
            f"/order_status {pending['order_number']} cancelled | {'N' * 3_880}",
            message_id=12,
        )
        self.assertEqual(self.db.get_order(pending["id"])["status"], "pending_payment")

        card = self.db.create_order_payment(
            pending["id"], "card", idempotency_key="long-reject-payment"
        )
        self.db.submit_payment_receipt(card["id"], "receipt-long")
        self.handle_admin(
            f"/reject_payment {card['payment_number']} | {'Q' * 3_850}",
            message_id=13,
        )
        self.assertEqual(self.db.get_payment(card["id"])["status"], "verifying")
        with self.assertRaises(ConflictError):
            self.db.update_order_status(pending["id"], "cancelled")
        self.assertEqual(self.db.get_payment(card["id"])["status"], "verifying")

        ticket = self.db.create_ticket(
            order["user_id"],
            "Long reply",
            "body",
            idempotency_key="long-reply-ticket",
        )
        self.handle_admin(
            f"/ticket_reply {ticket['ticket_number']} | {'T' * 3_850}",
            message_id=14,
        )
        self.assertEqual(len(self.db.list_ticket_messages(ticket["id"])), 1)

    def test_unconfigured_payment_methods_are_hidden_and_cannot_be_enabled(self) -> None:
        buyer = self.db.upsert_user(35_001, 35_001, username="config_guard")
        product = self.db.create_product(
            self.category["id"],
            "Config guard",
            product_type="ready",
            price_amount=100,
            idempotency_key="config-guard-product",
        )
        order = self.db.create_order(
            buyer["id"], product["id"], idempotency_key="config-guard-order"
        )
        self.app.show_payment_methods(buyer, order["id"])
        buttons = [
            button
            for row in self.telegram.messages[-1]["reply_markup"]["inline_keyboard"]
            for button in row
        ]
        callbacks = {str(button.get("callback_data") or "") for button in buttons}
        self.assertFalse(any(value.startswith("paycard:") for value in callbacks))
        self.assertFalse(any(value.startswith("paycrypto:") for value in callbacks))

        self.db.set_setting("payment_card_enabled", False)
        self.handle_admin("/payment card on", message_id=21)
        self.assertFalse(self.db.get_setting("payment_card_enabled", False))
        self.handle_admin("/payment crypto on", message_id=22)
        self.assertFalse(self.db.get_setting("payment_crypto_enabled", False))

    def test_receipt_and_manual_attachment_are_recoverable_from_committed_state(self) -> None:
        support_chat_id = 9_002
        self.db.add_admin(
            "release_support",
            support_chat_id,
            role="support",
            created_by_admin_id=self.owner["id"],
        )
        buyer = self.db.upsert_user(40_001, 40_001, username="receipt_recovery")
        product = self.db.create_product(
            self.category["id"],
            "Receipt product",
            product_type="ready",
            price_amount=500,
            idempotency_key="receipt-product",
        )
        order = self.db.create_order(
            buyer["id"], product["id"], idempotency_key="receipt-order"
        )
        payment = self.db.create_order_payment(
            order["id"], "card", idempotency_key="receipt-payment"
        )
        self.db.set_user_state(
            buyer["id"], "payment_receipt", {"payment_id": payment["id"]}
        )
        self.app.process_update(
            {
                "update_id": 91_001,
                "message": {
                    "message_id": 81,
                    "chat": {"id": buyer["chat_id"], "type": "private"},
                    "from": {
                        "id": buyer["telegram_user_id"],
                        "username": buyer["username"],
                    },
                    "photo": [{"file_id": "photo-receipt-id"}],
                },
            }
        )
        self.assertIn(self.OWNER_CHAT_ID, {copy["chat_id"] for copy in self.telegram.copies})
        self.assertNotIn(support_chat_id, {copy["chat_id"] for copy in self.telegram.copies})
        self.app._reconcile_card_receipt_alerts()
        self.app._reconcile_card_receipt_alerts()
        alerts = [
            message
            for message in self.telegram.messages
            if payment["payment_number"] in message["text"]
            and "/payment_detail" in message["text"]
        ]
        self.assertEqual(len(alerts), 1)
        self.handle_admin(f"/payment_detail {payment['payment_number']}")
        self.assertEqual(self.telegram.photos[-1]["photo"], "photo-receipt-id")

        # A replacement committed just before a process crash gets its own
        # durable alert key; timestamp precision is intentionally irrelevant.
        self.db.submit_payment_receipt(
            payment["id"], "replacement-document", file_kind="document"
        )
        self.app._reconcile_card_receipt_alerts()
        self.app._reconcile_card_receipt_alerts()
        alerts = [
            message
            for message in self.telegram.messages
            if payment["payment_number"] in message["text"]
            and "/payment_detail" in message["text"]
        ]
        self.assertEqual(len(alerts), 2)
        self.assertEqual(len(self._outbox_keys(":receipt:")), 2)
        self.handle_admin(f"/payment_detail {payment['payment_number']}")
        self.assertEqual(self.telegram.documents[-1]["document"], "replacement-document")

        manual_product = self.db.create_product(
            self.category["id"],
            "Attachment product",
            product_type="manual",
            price_amount=100,
            idempotency_key="attachment-product",
        )
        manual_order = self.db.create_order(
            buyer["id"],
            manual_product["id"],
            idempotency_key="attachment-order",
        )
        manual_payment = self.db.create_order_payment(
            manual_order["id"], "card", idempotency_key="attachment-payment"
        )
        self.db.mark_payment_paid(manual_payment["id"])
        self.db.update_order_status(manual_order["id"], "awaiting_info")
        self.db.set_user_state(
            buyer["id"], "order_information", {"order_id": manual_order["id"]}
        )
        copies_before_manual = len(self.telegram.copies)
        self.app.process_update(
            {
                "update_id": 91_002,
                "message": {
                    "message_id": 82,
                    "chat": {"id": buyer["chat_id"], "type": "private"},
                    "from": {
                        "id": buyer["telegram_user_id"],
                        "username": buyer["username"],
                    },
                    "caption": "v1",
                    "photo": [{"file_id": "manual-photo"}],
                },
            }
        )
        manual_copies = self.telegram.copies[copies_before_manual:]
        self.assertEqual({copy["chat_id"] for copy in manual_copies}, {self.OWNER_CHAT_ID})
        self.app._reconcile_manual_order_info_alerts()
        self.app._reconcile_manual_order_info_alerts()
        first_keys = self._outbox_keys("customer-info")
        self.assertEqual(len(first_keys), 1)
        self.handle_admin(f"/order_attachment {manual_order['order_number']}")
        self.assertEqual(self.telegram.photos[-1]["photo"], "manual-photo")

        self.db.set_order_customer_info(
            manual_order["id"],
            {
                "file_id": "manual-document",
                "file_kind": "document",
                "text": "v2",
            },
        )
        self.app._reconcile_manual_order_info_alerts()
        self.app._reconcile_manual_order_info_alerts()
        self.assertEqual(len(self._outbox_keys("customer-info")), 2)
        self.handle_admin(f"/order_attachment {manual_order['order_number']}")
        self.assertEqual(self.telegram.documents[-1]["document"], "manual-document")

    def _outbox_keys(self, fragment: str) -> list[str]:
        connection = sqlite3.connect(self.db.path)
        try:
            return [
                str(row[0])
                for row in connection.execute(
                    "SELECT idempotency_key FROM outbound_messages WHERE idempotency_key LIKE ?",
                    (f"%{fragment}%",),
                ).fetchall()
            ]
        finally:
            connection.close()

    def test_historical_card_review_links_unique_candidate_and_not_ambiguous_one(self) -> None:
        now = utc_now() - timedelta(minutes=2)
        first = self.db.upsert_user(50_001, 50_001, username="old_card_owner")
        payment = self.db.create_wallet_topup_payment(
            first["id"],
            100_000,
            "card",
            idempotency_key="historical-card-one",
            unique_amount_window=0,
            now=now,
        )
        self.db.cancel_pending_payment(
            payment["id"], first["id"], now=now + timedelta(seconds=20)
        )
        outcome = self.app.confirm_card_amount(
            100_000,
            "historical-reference-one",
            (now + timedelta(seconds=10)).isoformat(),
        )
        event = self.db.get_card_payment_event("historical-reference-one")
        self.assertEqual(outcome, ConfirmationOutcome.NOT_FOUND)
        self.assertEqual(event["payment_id"], payment["id"])
        self.db.resolve_card_payment_review(
            event["id"], "dismiss", self.owner["id"], "checked"
        )
        self.app._reconcile_card_review_resolution_notices()
        self.app._reconcile_card_review_resolution_notices()
        self.assertIsNotNone(
            self.db.get_outbound_message_by_idempotency_key(
                f"card-review:{event['id']}:resolution:dismiss:user"
            )
        )

        second = self.db.upsert_user(50_002, 50_002, username="other_card_owner")
        second_payment = self.db.create_wallet_topup_payment(
            second["id"],
            100_000,
            "card",
            idempotency_key="historical-card-two",
            unique_amount_window=1,
            now=now,
        )
        self.db.cancel_pending_payment(
            second_payment["id"], second["id"], now=now + timedelta(seconds=20)
        )
        connection = sqlite3.connect(self.db.path)
        try:
            connection.execute(
                "UPDATE payments SET payable_amount = 100000 WHERE id = ?",
                (second_payment["id"],),
            )
            connection.commit()
        finally:
            connection.close()
        self.app.confirm_card_amount(
            100_000,
            "historical-reference-ambiguous",
            (now + timedelta(seconds=10)).isoformat(),
        )
        ambiguous = self.db.get_card_payment_event("historical-reference-ambiguous")
        self.assertIsNone(ambiguous["payment_id"])

    def test_late_completed_crypto_requires_owner_credit_and_never_revives_order(self) -> None:
        now = utc_now() - timedelta(hours=2)
        buyer = self.db.upsert_user(60_001, 60_001, username="late_crypto")
        ready = self.db.create_product(
            self.category["id"],
            "Late crypto product",
            product_type="ready",
            price_amount=1_000,
            idempotency_key="late-crypto-product",
        )
        inventory = self.db.add_inventory_item(ready["id"], "late-secret")
        self.db.credit_wallet(
            buyer["id"], 300, reason="seed", idempotency_key="late-crypto-seed"
        )
        order = self.db.create_order(
            buyer["id"], ready["id"], idempotency_key="late-crypto-order", now=now
        )
        self.db.hold_wallet_funds(order["id"], idempotency_key="late-hold", now=now)
        payment = self.db.create_order_payment(
            order["id"],
            "crypto",
            idempotency_key="late-crypto-payment",
            provider_invoice_id="late-operation",
            now=now,
        )
        partial = self.db.record_provider_payment_event(
            payment["id"],
            "plisio",
            "late-operation",
            "expired",
            {
                "id": "late-operation",
                "type": "invoice",
                "status": "expired",
                "amount": "10",
            },
            received_amount="10",
            disposition="review",
            now=now + timedelta(minutes=31),
        )
        self.db.resolve_provider_payment_review(
            partial["id"],
            "dismiss",
            self.owner["id"],
            "initial terminal review",
            now=now + timedelta(hours=1),
        )
        before = self.db.get_order(order["id"])
        self.assertEqual(before["status"], "expired")
        self.assertEqual(self.db.wallet_balance(buyer["id"]), 300)

        provider = Mock()
        provider.operation.return_value = {
            "id": "late-operation",
            "type": "invoice",
            "status": "completed",
            "amount": "700",
        }
        self.app._plisio = provider
        self.app._poll_crypto_payments()
        self.assertEqual(self.db.get_payment(payment["id"])["status"], "failed")
        self.assertEqual(self.db.wallet_balance(buyer["id"]), 300)
        late_review = self.db.list_provider_payment_reviews(limit=10)[0]
        self.assertEqual(late_review["provider_status"], "completed")
        self.db.resolve_provider_payment_review(
            late_review["id"],
            "dismiss",
            self.owner["id"],
            "first completed observation dismissed",
        )
        provider.operation.return_value = {
            "id": "late-operation",
            "type": "invoice",
            "status": "completed",
            "amount": "700",
            "observed_at": "second-observation",
        }
        self.db.set_setting(self.app._CRYPTO_POLL_CURSOR_SETTING, payment["id"] - 1)
        self.app._poll_crypto_payments()
        self.assertEqual(self.db.wallet_balance(buyer["id"]), 300)
        late_review = self.db.list_provider_payment_reviews(limit=10)[0]
        self.assertEqual(late_review["provider_status"], "completed")

        def resolve() -> dict:
            return self.db.resolve_provider_payment_review(
                late_review["id"],
                "credit_confirmed",
                self.owner["id"],
                "live provider evidence checked",
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _index: resolve(), range(2)))
        self.assertTrue(
            all(result["settlement"] == "wallet_fallback_credited" for result in results)
        )
        self.assertEqual(self.db.wallet_balance(buyer["id"]), 1_000)
        self.assertEqual(self.db.get_order(order["id"])["status"], "expired")
        self.assertEqual(
            self.db.list_inventory_items(ready["id"])[0]["id"], inventory["id"]
        )
        self.assertEqual(
            self.db.list_inventory_items(ready["id"])[0]["status"], "available"
        )
        # Simulate a crash after the atomic financial resolution and before
        # AdminController gets a chance to queue the user-facing outcome.
        self.app._reconcile_provider_review_resolution_notices()
        self.app._reconcile_provider_review_resolution_notices()
        self.assertIsNotNone(
            self.db.get_outbound_message_by_idempotency_key(
                f"provider-review:{late_review['id']}:"
                "resolution:credit_confirmed:user"
            )
        )
        self.app._reconcile_paid_payment_notices()
        self.app._reconcile_paid_payment_notices()
        notices = [
            message
            for message in self.telegram.messages
            if message["chat_id"] == buyer["chat_id"]
            and "پرداخت ارزی قطعی تعیین تکلیف شد" in message["text"]
        ]
        self.assertEqual(len(notices), 1)
        transactions = self.db.list_user_transactions(buyer["id"])
        reasons = {str(entry["reason"]) for entry in transactions}
        self.assertIn("اعتبار جبرانی پرداخت ارزی دیررس", reasons)
        self.assertIn("دریافت دیررس پرداخت ارزی؛ سفارش قبلی فعال نشد", reasons)
        summary = self.db.user_summary(buyer["id"])
        self.assertEqual(summary["successful_order_count"], 0)
        self.assertEqual(summary["total_paid"], 0)

    def test_zero_failed_topup_is_watched_then_late_completed_is_reviewed(self) -> None:
        buyer = self.db.upsert_user(70_001, 70_001, username="late_topup")
        payment = self.db.create_wallet_topup_payment(
            buyer["id"],
            25_000,
            "crypto",
            idempotency_key="late-topup",
            provider_invoice_id="late-topup-operation",
        )
        self.db.record_provider_payment_event(
            payment["id"],
            "plisio",
            "late-topup-operation",
            "expired",
            {
                "id": "late-topup-operation",
                "type": "invoice",
                "status": "expired",
                "amount": "0",
            },
            received_amount="0",
            disposition="failed",
        )
        provider = Mock()
        provider.operation.return_value = {
            "id": "late-topup-operation",
            "type": "invoice",
            "status": "completed",
            "amount": "25000",
        }
        self.app._plisio = provider
        self.app._poll_crypto_payments()
        review = self.db.list_provider_payment_reviews(limit=10)[0]
        self.assertEqual(self.db.wallet_balance(buyer["id"]), 0)
        self.assertEqual(self.db.get_payment(payment["id"])["status"], "failed")
        resolved = self.db.resolve_provider_payment_review(
            review["id"],
            "credit_confirmed",
            self.owner["id"],
            "verified",
        )
        self.assertEqual(resolved["settlement"], "wallet_topup_credited")
        self.assertEqual(self.db.wallet_balance(buyer["id"]), 25_000)
        replay = self.db.resolve_provider_payment_review(
            review["id"],
            "credit_confirmed",
            self.owner["id"],
            "verified",
        )
        self.assertEqual(replay["settlement"], "wallet_topup_credited")
        self.assertEqual(self.db.wallet_balance(buyer["id"]), 25_000)
        self.app._reconcile_provider_review_resolution_notices()
        self.app._reconcile_provider_review_resolution_notices()
        self.assertIsNotNone(
            self.db.get_outbound_message_by_idempotency_key(
                f"provider-review:{review['id']}:"
                "resolution:credit_confirmed:user"
            )
        )
        with self.assertRaises(ConflictError):
            self.db.resolve_provider_payment_review(
                review["id"], "dismiss", self.owner["id"], "different"
            )

        superseded_payment = self.db.create_wallet_topup_payment(
            buyer["id"],
            30_000,
            "crypto",
            idempotency_key="superseded-topup",
            provider_invoice_id="superseded-operation",
        )
        superseded_review = self.db.record_provider_payment_event(
            superseded_payment["id"],
            "plisio",
            "superseded-operation",
            "expired",
            {
                "id": "superseded-operation",
                "type": "invoice",
                "status": "expired",
                "amount": "1",
            },
            received_amount="1",
            disposition="review",
        )
        self.db.record_provider_payment_event(
            superseded_payment["id"],
            "plisio",
            "superseded-operation",
            "completed",
            {
                "id": "superseded-operation",
                "type": "invoice",
                "status": "completed",
            },
            received_amount=None,
            disposition="completed",
        )
        with self.assertRaises(ConflictError):
            self.db.resolve_provider_payment_review(
                superseded_review["id"],
                "dismiss",
                self.owner["id"],
                "must not override provider evidence",
            )

    def test_terminal_local_crypto_orders_remain_watched_without_revival(self) -> None:
        buyer = self.db.upsert_user(75_001, 75_001, username="terminal_crypto")
        product = self.db.create_product(
            self.category["id"],
            "Terminal crypto product",
            product_type="ready",
            price_amount=9_000,
            idempotency_key="terminal-crypto-product",
        )
        untouched = self.db.create_order(
            buyer["id"], product["id"], idempotency_key="guarded-ready-order"
        )
        with self.assertRaises(ValidationError):
            self.db.update_order_status(untouched["id"], "awaiting_confirmation")
        with self.assertRaises(ValidationError):
            self.db.update_order_status(untouched["id"], "processing")
        self.assertEqual(self.db.get_order(untouched["id"])["status"], "pending_payment")
        reserved_product = self.db.create_product(
            self.category["id"],
            "Reserved ready guard",
            product_type="ready",
            price_amount=9_000,
            reserve_enabled=True,
            idempotency_key="reserved-ready-guard-product",
        )
        reserved_order = self.db.create_order(
            buyer["id"],
            reserved_product["id"],
            idempotency_key="reserved-ready-guard-order",
        )
        reserved_payment = self.db.create_order_payment(
            reserved_order["id"],
            "card",
            idempotency_key="reserved-ready-guard-payment",
        )
        self.db.mark_payment_paid(reserved_payment["id"])
        reservation = self.db.reserve_product(
            buyer["id"], reserved_product["id"], order_id=reserved_order["id"]
        )
        with self.assertRaises(ValidationError):
            self.db.update_order_status(reserved_order["id"], "processing")
        self.assertEqual(
            self.db.get_order(reserved_order["id"])["status"], "awaiting_stock"
        )
        connection = sqlite3.connect(self.db.path)
        try:
            reservation_status = connection.execute(
                "SELECT status FROM reservations WHERE id = ?",
                (reservation["id"],),
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(reservation_status, "queued")
        created: list[tuple[dict, dict, str]] = []
        for index, terminal_status in enumerate(("cancelled", "expired", "rejected")):
            order = self.db.create_order(
                buyer["id"],
                product["id"],
                idempotency_key=f"terminal-crypto-order:{index}",
            )
            payment = self.db.create_order_payment(
                order["id"],
                "crypto",
                idempotency_key=f"terminal-crypto-payment:{index}",
                provider_invoice_id=f"terminal-operation:{index}",
                unique_amount_window=0,
            )
            with self.assertRaises(ConflictError):
                self.db.update_order_status(order["id"], terminal_status)
            # Simulate a database produced by an older release before the
            # no-local-cancel invariant. Current polling must still recover it.
            connection = sqlite3.connect(self.db.path)
            try:
                payment_status = (
                    "expired" if terminal_status == "expired" else "cancelled"
                )
                connection.execute(
                    "UPDATE payments SET status = ? WHERE id = ?",
                    (payment_status, payment["id"]),
                )
                connection.execute(
                    "UPDATE orders SET status = ? WHERE id = ?",
                    (terminal_status, order["id"]),
                )
                connection.commit()
            finally:
                connection.close()
            created.append((order, payment, terminal_status))

        provider = Mock()
        provider.operation.side_effect = lambda reference: {
            "id": reference,
            "type": "invoice",
            "status": "completed",
            "amount": "9000",
        }
        self.app._plisio = provider
        self.db.set_setting(self.app._CRYPTO_POLL_CURSOR_SETTING, 0)
        self.app._poll_crypto_payments()

        self.assertEqual(provider.operation.call_count, 3)
        self.assertEqual(len(self.db.list_provider_payment_reviews(limit=10)), 3)
        for order, payment, terminal_status in created:
            self.assertEqual(self.db.get_order(order["id"])["status"], terminal_status)
            expected_payment = "expired" if terminal_status == "expired" else "cancelled"
            self.assertEqual(self.db.get_payment(payment["id"])["status"], expected_payment)
        self.assertEqual(self.db.wallet_balance(buyer["id"]), 0)

    def test_poll_cursor_reaches_payment_51_and_invalid_identity_never_credits(self) -> None:
        payments: list[dict] = []
        for index in range(51):
            user = self.db.upsert_user(
                80_000 + index,
                80_000 + index,
                username=f"poll_user_{index}",
            )
            payments.append(
                self.db.create_wallet_topup_payment(
                    user["id"],
                    10_000,
                    "crypto",
                    idempotency_key=f"poll-payment:{index}",
                    provider_invoice_id=f"poll-operation:{index}",
                )
            )

        provider = Mock()

        def operation(reference: str) -> dict[str, str]:
            if reference == "poll-operation:50":
                return {
                    "id": reference,
                    "type": "invoice",
                    "status": "completed",
                    "amount": "10000",
                }
            return {
                "id": reference,
                "type": "invoice",
                "status": "pending",
                "amount": "0",
            }

        provider.operation.side_effect = operation
        self.app._plisio = provider
        self.app._poll_crypto_payments()
        self.assertEqual(self.db.get_payment(payments[50]["id"])["status"], "pending")
        self.app._poll_crypto_payments()
        self.assertEqual(self.db.get_payment(payments[50]["id"])["status"], "paid")

        victim = self.db.upsert_user(81_000, 81_000, username="identity_victim")
        mismatched = self.db.create_wallet_topup_payment(
            victim["id"],
            50_000,
            "crypto",
            idempotency_key="identity-payment",
            provider_invoice_id="expected-operation",
        )
        provider.operation.side_effect = None
        provider.operation.return_value = {
            "id": "different-operation",
            "type": "invoice",
            "status": "completed",
            "amount": "50000",
        }
        self.db.set_setting(self.app._CRYPTO_POLL_CURSOR_SETTING, mismatched["id"] - 1)
        self.app._poll_crypto_payments()
        self.assertEqual(self.db.get_payment(mismatched["id"])["status"], "verifying")
        self.assertEqual(self.db.wallet_balance(victim["id"]), 0)

        terms_victim = self.db.upsert_user(
            81_001, 81_001, username="terms_identity_victim"
        )
        terms_payment = self.db.create_wallet_topup_payment(
            terms_victim["id"],
            50_000,
            "crypto",
            idempotency_key="terms-identity-payment",
            provider_invoice_id="terms-operation",
        )
        provider.operation.return_value = {
            "id": "terms-operation",
            "type": "invoice",
            "status": "completed",
            "params": {
                "source_amount": "1",
                "source_currency": self.settings.plisio_source_currency,
                "currency": self.settings.plisio_currency,
            },
        }
        self.db.set_setting(
            self.app._CRYPTO_POLL_CURSOR_SETTING, terms_payment["id"] - 1
        )
        self.app._poll_crypto_payments()
        self.assertEqual(self.db.get_payment(terms_payment["id"])["status"], "verifying")
        terms_review = next(
            event
            for event in self.db.list_provider_payment_reviews(limit=20)
            if int(event["payment_id"]) == int(terms_payment["id"])
        )
        self.assertEqual(terms_review["provider_status"], "malformed")
        with self.assertRaises(ConflictError):
            self.db.resolve_provider_payment_review(
                terms_review["id"],
                "credit_confirmed",
                self.owner["id"],
                "must not credit mismatched invoice terms",
            )
        self.assertEqual(self.db.wallet_balance(terms_victim["id"]), 0)

    def test_username_reassignment_and_card_cancel_abuse_fail_closed(self) -> None:
        former = self.db.upsert_user(90_001, 90_001, username="alice_owner")
        current = self.db.upsert_user(90_002, 90_002, username=None)
        self.db.upsert_user(former["telegram_user_id"], former["chat_id"], username=None)
        self.db.upsert_user(
            current["telegram_user_id"], current["chat_id"], username="alice_owner"
        )
        self.assertEqual(
            self.db.get_user_by_username("@alice_owner")["id"], current["id"]
        )

        original_limit = self.db.CARD_CANCEL_BURST_LIMIT
        original_cooldown = self.db.CARD_CANCEL_COOLDOWN
        self.db.CARD_CANCEL_BURST_LIMIT = 3
        self.db.CARD_CANCEL_COOLDOWN = timedelta(hours=1)
        try:
            start = utc_now() - timedelta(minutes=10)
            for index in range(3):
                payment = self.db.create_wallet_topup_payment(
                    current["id"],
                    10_000 + index,
                    "card",
                    idempotency_key=f"abuse:{index}",
                    unique_amount_window=0,
                    now=start + timedelta(minutes=index),
                )
                self.db.cancel_pending_payment(
                    payment["id"],
                    current["id"],
                    now=start + timedelta(minutes=index, seconds=1),
                )
            with self.assertRaises(ConflictError):
                self.db.create_wallet_topup_payment(
                    current["id"],
                    20_000,
                    "card",
                    idempotency_key="abuse:blocked",
                    unique_amount_window=0,
                    now=start + timedelta(minutes=4),
                )
            self.assertEqual(len(self.db.list_payment_security_events()), 1)
            allowed = self.db.create_wallet_topup_payment(
                current["id"],
                20_000,
                "card",
                idempotency_key="abuse:after-window",
                unique_amount_window=0,
                now=start + timedelta(hours=2),
            )
            self.assertEqual(allowed["status"], "pending")
        finally:
            self.db.CARD_CANCEL_BURST_LIMIT = original_limit
            self.db.CARD_CANCEL_COOLDOWN = original_cooldown


if __name__ == "__main__":
    unittest.main()
