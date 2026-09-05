from __future__ import annotations

import unittest
import sqlite3
from datetime import timedelta

from app.db import NotFoundError, OutOfStockError, ValidationError
from app.utils import utc_now
from tests import test_bot as _bot_tests


class PurchaseSpecificationAuditTests(unittest.TestCase):
    """Acceptance regressions drawn directly from the supplied purchase documents."""

    CUSTOMER = _bot_tests.BotApplicationIntegrationTests.CUSTOMER
    OWNER = _bot_tests.BotApplicationIntegrationTests.OWNER
    setUp = _bot_tests.BotApplicationIntegrationTests.setUp
    tearDown = _bot_tests.BotApplicationIntegrationTests.tearDown
    message = _bot_tests.BotApplicationIntegrationTests.message
    callback = _bot_tests.BotApplicationIntegrationTests.callback
    send_message = _bot_tests.BotApplicationIntegrationTests.send_message
    send_callback = _bot_tests.BotApplicationIntegrationTests.send_callback
    _take_update_id = _bot_tests.BotApplicationIntegrationTests._take_update_id

    def customer(self):
        user = self.db.upsert_user(
            self.CUSTOMER["id"],
            self.CUSTOMER["id"],
            username=self.CUSTOMER["username"],
        )
        return self.db.update_user_profile(
            user["id"], customer_name="Test Customer", phone="+989121234567"
        )

    def test_full_discount_keeps_updated_summary_until_explicit_confirmation(self):
        # pasted-text: a valid discount always returns to the updated summary,
        # keeping the same payment / discount / back buttons.
        user = self.customer()
        self.send_callback(self.CUSTOMER, f"buy:{self.product['id']}")
        order = self.db.list_orders(user_id=user["id"])[0]
        self.db.create_discount("ALLFREE", discount_type="percent", value=100)
        self.send_callback(self.CUSTOMER, f"discount:{order['id']}")
        self.send_message(self.CUSTOMER, text="ALLFREE")

        discounted = self.db.get_order(order["id"])
        self.assertEqual(discounted["status"], "pending_payment")
        self.assertEqual(discounted["payable_amount"], 0)
        self.assertIsNone(discounted["delivered_payload"])
        summary = self.telegram.messages[-1]
        self.assertIn("خلاصه سفارش", summary["text"])
        self.assertIn("مبلغ نهایی", summary["text"])
        self.assertEqual(
            [
                button["callback_data"]
                for row in summary["reply_markup"]["inline_keyboard"]
                for button in row
            ],
            [
                f"checkout:{order['id']}",
                f"discount:{order['id']}",
                f"prod:{self.product['id']}",
            ],
        )
        self.app.run_maintenance()
        self.assertEqual(self.db.get_order(order["id"])["status"], "pending_payment")

        self.send_callback(self.CUSTOMER, f"checkout:{order['id']}")
        self.assertEqual(self.db.get_order(order["id"])["status"], "completed")
        self.assertEqual(self.db.wallet_balance(user["id"]), 0)
        self.assertIsNone(self.db.latest_order_payment(order["id"]))
        self.assertEqual(
            self.db.inventory_count(self.product["id"], status="assigned"), 1
        )
        before_replay = len(self.telegram.messages)
        self.db.confirm_zero_payable_order(order["id"], user["id"])
        self.app.run_maintenance()
        self.assertEqual(len(self.telegram.messages), before_replay)

    def test_zero_confirmation_rejects_wrong_owner_expiry_and_wallet_coverage(self):
        user = self.customer()
        self.db.create_discount("ZEROGUARD", discount_type="percent", value=100)
        created = utc_now() - timedelta(hours=1)
        expired = self.db.create_order(
            user["id"], self.product["id"], idempotency_key="expired-zero", now=created
        )
        self.db.apply_discount(expired["id"], "ZEROGUARD", now=created)
        with self.assertRaises(ValidationError):
            self.db.confirm_zero_payable_order(expired["id"], user["id"])
        self.assertEqual(self.db.get_order(expired["id"])["status"], "pending_payment")

        order = self.db.create_order(
            user["id"], self.product["id"], idempotency_key="owned-zero"
        )
        self.db.apply_discount(order["id"], "ZEROGUARD")
        with self.assertRaises(NotFoundError):
            self.db.confirm_zero_payable_order(order["id"], user["id"] + 99)
        self.assertEqual(self.db.get_order(order["id"])["status"], "pending_payment")

        wallet_order = self.db.create_order(
            user["id"], self.product["id"], idempotency_key="wallet-not-zero"
        )
        self.db.credit_wallet(
            user["id"], 100_000, reason="seed", idempotency_key="wallet-zero-seed"
        )
        self.db.hold_wallet_funds(
            wallet_order["id"], idempotency_key="wallet-zero-hold"
        )
        self.assertEqual(self.db.get_order(wallet_order["id"])["payable_amount"], 0)
        with self.assertRaises(ValidationError):
            self.db.confirm_zero_payable_order(wallet_order["id"], user["id"])
        self.assertEqual(
            self.db.get_order(wallet_order["id"])["wallet_captured_amount"], 100_000
        )

    def test_free_product_waits_for_summary_confirmation_and_recovers_success_notice(
        self,
    ):
        user = self.customer()
        product = self.db.create_product(
            self.category["id"],
            "Free checkout product",
            product_type="ready",
            price_amount=0,
        )
        self.db.add_inventory_item(product["id"], "free-order-stock")
        self.send_callback(self.CUSTOMER, f"buy:{product['id']}")
        order = self.db.list_orders(user_id=user["id"])[0]
        self.assertEqual(order["status"], "pending_payment")
        self.assertIn("خلاصه سفارش", self.telegram.messages[-1]["text"])
        self.app.run_maintenance()
        self.assertEqual(self.db.get_order(order["id"])["status"], "pending_payment")
        self.assertEqual(self.db.inventory_count(product["id"]), 1)

        # A crash after explicit confirmation but before notification must keep
        # inventory gated until the canonical success notice is recovered.
        self.db.confirm_zero_payable_order(order["id"], user["id"])
        self.assertFalse(self.db.order_success_notice_ready(order["id"]))
        self.app.fulfill_order(order["id"])
        self.assertEqual(self.db.get_order(order["id"])["status"], "paid")
        before = len(self.telegram.messages)
        self.app.run_maintenance()
        self.assertEqual(self.db.get_order(order["id"])["status"], "completed")
        notices = [
            row
            for row in self.telegram.messages[before:]
            if row["chat_id"] == user["chat_id"]
        ]
        self.assertIn("پرداخت با موفقیت", notices[0]["text"])
        self.assertIn("سفارش رایگان", notices[0]["text"])
        self.assertTrue(any("free-order-stock" in row["text"] for row in notices[1:]))
        self.assertIsNone(self.db.latest_order_payment(order["id"]))

    def test_new_purchase_cannot_jump_existing_paid_reservation(self):
        # PDF 1 page 4 / PDF 2 page 2: restock is delivered in queue order.
        product = self.db.create_product(
            self.category["id"],
            "FIFO product",
            product_type="ready",
            price_amount=100,
            reserve_enabled=True,
        )
        first_user = self.customer()
        self.db.credit_wallet(
            first_user["id"], 100, reason="seed", idempotency_key="fifo-seed-1"
        )
        first_order = self.db.create_order(
            first_user["id"], product["id"], idempotency_key="fifo-1"
        )
        self.send_callback(self.CUSTOMER, f"paywallet:{first_order['id']}")
        self.assertEqual(
            self.db.get_order(first_order["id"])["status"], "awaiting_stock"
        )
        item = self.db.add_inventory_item(product["id"], "oldest-customer-stock")

        second_actor = {"id": 1002, "username": "second_fifo", "first_name": "Second"}
        second_user = self.db.upsert_user(1002, 1002, username="second_fifo")
        self.db.credit_wallet(
            second_user["id"], 100, reason="seed", idempotency_key="fifo-seed-2"
        )
        second_order = self.db.create_order(
            second_user["id"], product["id"], idempotency_key="fifo-2"
        )
        self.send_callback(second_actor, f"paywallet:{second_order['id']}")

        self.assertNotEqual(
            self.db.get_order(second_order["id"])["status"], "completed"
        )
        self.app.run_maintenance()
        self.assertEqual(self.db.get_order(first_order["id"])["status"], "completed")
        assigned = next(
            row
            for row in self.db.list_inventory_items(product["id"])
            if row["id"] == item["id"]
        )
        self.assertEqual(assigned["assigned_order_id"], first_order["id"])
        self.assertEqual(
            self.db.get_order(second_order["id"])["status"], "awaiting_stock"
        )

    def test_fifo_uses_payment_order_even_before_reservation_is_queued(self):
        product = self.db.create_product(
            self.category["id"],
            "Payment FIFO product",
            product_type="ready",
            price_amount=100,
            reserve_enabled=True,
        )
        user = self.customer()
        self.db.credit_wallet(
            user["id"], 200, reason="seed", idempotency_key="fifo-paid-seed"
        )
        created = utc_now() - timedelta(minutes=2)
        first_created = self.db.create_order(
            user["id"], product["id"], idempotency_key="created-first", now=created
        )
        first_paid = self.db.create_order(
            user["id"], product["id"], idempotency_key="paid-first", now=created
        )
        self.db.hold_wallet_funds(
            first_paid["id"], idempotency_key="hold-first", now=created
        )
        self.db.hold_wallet_funds(
            first_created["id"],
            idempotency_key="hold-second",
            now=created + timedelta(seconds=1),
        )
        self.db.reserve_product(user["id"], product["id"], order_id=first_created["id"])
        self.db.add_inventory_item(product["id"], "payment-order-secret")

        # A paid-but-not-yet-queued order already has its place in the FIFO.
        with self.assertRaises(OutOfStockError):
            self.db.assign_inventory(first_created["id"])
        self.assertIsNone(self.db.fulfill_next_available_reservation())
        self.db.reserve_product(user["id"], product["id"], order_id=first_paid["id"])
        fulfilled = self.db.fulfill_next_available_reservation()
        self.assertEqual(fulfilled["order_id"], first_paid["id"])
        self.assertEqual(
            self.db.get_order(first_created["id"])["status"], "awaiting_stock"
        )

    def test_expired_wallet_topup_warns_customer_not_to_pay_old_bill(self):
        # PDF 1 page 3: every unpaid bill expires after thirty minutes and the
        # user must receive a warning not to pay the old payment instructions.
        user = self.customer()
        payment = self.db.create_wallet_topup_payment(
            user["id"],
            10_000,
            "card",
            idempotency_key="expired-topup-audit",
            now=utc_now() - timedelta(hours=1),
        )
        self.telegram.messages.clear()
        self.app.run_maintenance()
        self.assertEqual(self.db.get_payment(payment["id"])["status"], "expired")
        customer_messages = [
            row for row in self.telegram.messages if row["chat_id"] == user["chat_id"]
        ]
        self.assertTrue(
            any(
                payment["payment_number"] in row["text"] and "واریز نکن" in row["text"]
                for row in customer_messages
            )
        )
        before_repeat = len(self.telegram.messages)
        self.app.run_maintenance()
        self.assertEqual(len(self.telegram.messages), before_repeat)

    def test_submitted_order_and_topup_receipts_remain_reviewable_after_eight_days(
        self,
    ):
        user = self.customer()
        created = utc_now() - timedelta(days=8)
        order = self.db.create_order(
            user["id"], self.product["id"], idempotency_key="review-8days", now=created
        )
        payment = self.db.create_order_payment(
            order["id"], "card", idempotency_key="order-review-8days", now=created
        )
        topup = self.db.create_wallet_topup_payment(
            user["id"],
            10_000,
            "card",
            idempotency_key="topup-review-8days",
            now=created,
        )
        for pending in (payment, topup):
            self.db.submit_payment_receipt(
                pending["id"],
                f"receipt-{pending['id']}",
                now=created + timedelta(minutes=1),
            )
        self.app.run_maintenance()
        self.assertEqual(
            self.db.get_order(order["id"])["status"], "awaiting_confirmation"
        )
        for pending in (payment, topup):
            self.assertEqual(self.db.get_payment(pending["id"])["status"], "verifying")
            self.db.submit_payment_receipt(pending["id"], f"corrected-{pending['id']}")
            self.db.mark_payment_paid(
                pending["id"], external_reference=f"reviewed-{pending['id']}"
            )
            self.assertEqual(self.db.get_payment(pending["id"])["status"], "paid")
        self.assertEqual(self.db.wallet_balance(user["id"]), 10_000)

    def test_same_second_mixed_payments_preserve_first_payer_before_queueing(self):
        self.db.create_discount("FIFOZERO", discount_type="percent", value=100)
        for index, (first_method, second_method) in enumerate(
            (
                ("wallet", "card"),
                ("card", "wallet"),
                ("wallet", "wallet"),
                ("zero", "card"),
            )
        ):
            with self.subTest(first_method=first_method, second_method=second_method):
                user = self.db.upsert_user(3000 + index, 3000 + index)
                product = self.db.create_product(
                    self.category["id"],
                    f"Same-second FIFO {index}",
                    price_amount=100,
                    product_type="ready",
                    reserve_enabled=True,
                )
                self.db.credit_wallet(
                    user["id"],
                    200,
                    reason="seed",
                    idempotency_key=f"same-second-seed-{index}",
                )
                now = utc_now() - timedelta(minutes=1)
                created_first = self.db.create_order(
                    user["id"],
                    product["id"],
                    idempotency_key=f"same-second-A-{index}",
                    now=now,
                )
                paid_first = self.db.create_order(
                    user["id"],
                    product["id"],
                    idempotency_key=f"same-second-B-{index}",
                    now=now,
                )

                def pay(order, method):
                    if method == "wallet":
                        self.db.hold_wallet_funds(
                            order["id"],
                            idempotency_key=f"same-second-hold-{order['id']}",
                            now=now,
                        )
                    elif method == "zero":
                        if (
                            self.db.get_order(order["id"])["status"]
                            == "pending_payment"
                        ):
                            self.db.apply_discount(order["id"], "FIFOZERO", now=now)
                        self.db.confirm_zero_payable_order(
                            order["id"], user["id"], now=now
                        )
                    else:
                        external = self.db.create_order_payment(
                            order["id"],
                            "card",
                            idempotency_key=f"same-second-payment-{order['id']}",
                            now=now,
                        )
                        self.db.mark_payment_paid(
                            external["id"],
                            external_reference=f"same-second-ref-{order['id']}",
                            now=now,
                        )

                pay(paid_first, first_method)
                first_stamp = self.db.get_order(paid_first["id"])["paid_at"]
                pay(created_first, second_method)
                second_stamp = self.db.get_order(created_first["id"])["paid_at"]
                self.assertLess(first_stamp, second_stamp)
                self.assertEqual(first_stamp[:19], second_stamp[:19])
                pay(paid_first, first_method)
                self.assertEqual(
                    self.db.get_order(paid_first["id"])["paid_at"], first_stamp
                )
                self.db.add_inventory_item(product["id"], f"same-second-stock-{index}")
                self.db.reserve_product(
                    user["id"], product["id"], order_id=created_first["id"]
                )
                with self.assertRaises(OutOfStockError):
                    self.db.assign_inventory(created_first["id"])
                self.db.reserve_product(
                    user["id"], product["id"], order_id=paid_first["id"]
                )
                self.assertEqual(
                    self.db.fulfill_next_available_reservation()["order_id"],
                    paid_first["id"],
                )

    def test_legacy_equal_payment_timestamps_preserve_known_reservation_order(self):
        user = self.customer()
        product = self.db.create_product(
            self.category["id"],
            "Legacy FIFO",
            price_amount=100,
            product_type="ready",
            reserve_enabled=True,
        )
        self.db.credit_wallet(
            user["id"], 200, reason="seed", idempotency_key="legacy-fifo-seed"
        )
        first = self.db.create_order(
            user["id"], product["id"], idempotency_key="legacy-fifo-A"
        )
        second = self.db.create_order(
            user["id"], product["id"], idempotency_key="legacy-fifo-B"
        )
        for order in (second, first):
            self.db.hold_wallet_funds(
                order["id"], idempotency_key=f"legacy-hold-{order['id']}"
            )
            self.db.reserve_product(user["id"], product["id"], order_id=order["id"])
        connection = sqlite3.connect(self.db.path)
        try:
            connection.execute(
                "UPDATE orders SET paid_at = ? WHERE id IN (?, ?)",
                (utc_now().isoformat(timespec="seconds"), first["id"], second["id"]),
            )
            connection.commit()
        finally:
            connection.close()
        self.db.add_inventory_item(product["id"], "legacy-queue-stock")
        self.assertEqual(
            self.db.fulfill_next_available_reservation()["order_id"], second["id"]
        )

    def test_wallet_topup_expiry_notice_recovers_after_committed_expiry_and_restart(
        self,
    ):
        from app.bot import BotApplication
        from app.db import Database

        user = self.customer()
        payment = self.db.create_wallet_topup_payment(
            user["id"],
            10_000,
            "card",
            idempotency_key="crashed-topup-expiry",
            now=utc_now() - timedelta(hours=1),
        )
        self.db.expire_pending_payments()
        self.assertIsNone(
            self.db.get_outbound_message_by_idempotency_key(
                f"payment:{payment['id']}:topup-expired"
            )
        )
        restarted_telegram = _bot_tests.FakeTelegram()
        restarted = BotApplication(
            self.settings, Database(self.settings.database_path), restarted_telegram
        )
        restarted.initialize()
        restarted.run_maintenance()
        notice = self.db.get_outbound_message_by_idempotency_key(
            f"payment:{payment['id']}:topup-expired"
        )
        self.assertEqual(notice["status"], "sent")
        self.assertIn(payment["payment_number"], notice["body"])
        self.assertIn("10,000", notice["body"])
        before_repeat = len(restarted_telegram.messages)
        restarted.run_maintenance()
        self.assertEqual(len(restarted_telegram.messages), before_repeat)
