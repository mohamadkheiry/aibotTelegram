from __future__ import annotations

import json
import os
import sqlite3
import stat
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from app.db import (
    ConflictError,
    Database,
    DatabaseError,
    NotFoundError,
    OutOfStockError,
    ValidationError,
)


UTC = timezone.utc
BASE_TIME = datetime(2026, 1, 10, 12, 0, tzinfo=UTC)


class DatabaseTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.db = Database(self.root / "bot.sqlite3", busy_timeout_ms=10_000)
        self.db.initialize()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def user(self, suffix: int = 1) -> dict:
        return self.db.upsert_user(
            10_000 + suffix,
            20_000 + suffix,
            username=f"user{suffix}",
            first_name=f"Telegram {suffix}",
            now=BASE_TIME,
        )

    def product(self, price: int = 1_000, **overrides) -> dict:
        category = next(
            (
                item
                for item in self.db.list_categories(
                    parent_id=None, active_only=False
                )
                if item["name"] == "Accounts"
            ),
            None,
        )
        if category is None:
            category = self.db.create_category("Accounts", now=BASE_TIME)
        arguments = {
            "product_type": "ready",
            "price_amount": price,
            "duration_days": 30,
            "duration_label": "30 days",
            "reminder_days": (7, 3, 1),
            "reserve_enabled": True,
            "idempotency_key": f"product-{price}-{len(overrides)}-{overrides.get('sku', '')}",
            "now": BASE_TIME,
        }
        arguments.update(overrides)
        return self.db.create_product(category["id"], overrides.pop("name", "Premium"), **arguments)


class IdentityAndStateTests(DatabaseTestCase):
    def test_profile_admin_binding_settings_state_and_channels(self) -> None:
        user = self.user()
        updated = self.db.update_user_profile(
            user["id"], customer_name="Customer Entered Name", phone="09120000000"
        )
        self.assertEqual(updated["customer_name"], "Customer Entered Name")

        refreshed = self.db.upsert_user(
            user["telegram_user_id"],
            user["chat_id"],
            username="User1",
            first_name="Changed Telegram Name",
        )
        self.assertEqual(refreshed["first_name"], "Changed Telegram Name")
        self.assertEqual(refreshed["customer_name"], "Customer Entered Name")

        pending = self.db.bootstrap_admin("PendingAdmin", role="support")
        self.assertIsNone(pending["chat_id"])
        self.assertFalse(self.db.is_admin(username="@pendingadmin", roles=("support",)))
        self.assertFalse(self.db.is_admin(chat_id=555))
        self.db.upsert_user(555, 555, username="pendingadmin", now=BASE_TIME)
        bound = self.db.bind_admin_chat("pendingadmin", 555)
        self.assertEqual(bound["chat_id"], 555)
        self.assertTrue(self.db.is_admin(chat_id=555))
        self.assertEqual(self.db.bootstrap_admin("PENDINGADMIN", 555, role="support")["id"], pending["id"])
        self.assertEqual(self.db.bootstrap_admin("pendingadmin", role="support")["chat_id"], 555)

        self.assertEqual(self.db.save_update_offset(15), 15)
        self.assertEqual(self.db.save_update_offset(9), 15)
        self.db.set_setting("bot_enabled", True)
        self.assertTrue(self.db.get_setting("bot_enabled"))
        state = self.db.set_user_state(user["id"], "awaiting_phone", {"step": 1})
        self.assertEqual(state["data"], {"step": 1})
        self.assertTrue(self.db.clear_user_state(user["id"]))

        first = self.db.upsert_force_join_channel("@one", "One", sort_order=2)
        same = self.db.upsert_force_join_channel("@one", "One updated", sort_order=1)
        self.assertEqual(first["id"], same["id"])
        self.assertEqual(self.db.list_force_join_channels()[0]["title"], "One updated")

    def test_admin_identity_and_last_owner_invariants_are_enforced_in_database(self) -> None:
        owner = self.db.bootstrap_admin("primary_owner", 901, role="owner")

        with self.assertRaises(ConflictError):
            self.db.bootstrap_admin("primary_owner", 901, role="admin")
        with self.assertRaises(ConflictError):
            self.db.set_admin_active(owner["id"], False)
        with self.assertRaises(ConflictError):
            self.db.bootstrap_admin("primary_owner", 902, role="owner")
        with self.assertRaises(ConflictError):
            self.db.bootstrap_admin("different_owner", 901, role="owner")

        unchanged = next(
            item for item in self.db.list_admins() if int(item["id"]) == int(owner["id"])
        )
        self.assertEqual(unchanged["role"], "owner")
        self.assertEqual(unchanged["chat_id"], 901)
        self.assertTrue(unchanged["is_active"])

        self.db.bootstrap_admin("secondary_owner", 903, role="owner")
        demoted = self.db.bootstrap_admin("primary_owner", 901, role="admin")
        self.assertEqual(demoted["role"], "admin")

    def test_delegated_admin_requires_a_proven_username_and_chat_pair(self) -> None:
        owner = self.db.bootstrap_admin("identity_owner", 990, role="owner")
        self.db.upsert_user(701, 701, username="known_bob", now=BASE_TIME)
        with self.assertRaisesRegex(ConflictError, "different Telegram username"):
            self.db.add_admin(
                "known_alice",
                701,
                role="admin",
                created_by_admin_id=owner["id"],
                now=BASE_TIME,
            )
        self.assertFalse(any(int(item.get("chat_id") or 0) == 701 for item in self.db.list_admins()))

        pending = self.db.add_admin(
            "future_alice",
            702,
            role="admin",
            created_by_admin_id=owner["id"],
            now=BASE_TIME,
        )
        self.assertIsNone(pending["identity_verified_at"])
        self.assertFalse(self.db.is_admin(chat_id=702))
        corrected = self.db.add_admin(
            "future_alice_corrected",
            702,
            role="admin",
            created_by_admin_id=owner["id"],
            now=BASE_TIME,
        )
        self.assertEqual(corrected["username"], "future_alice_corrected")
        self.assertIsNone(corrected["identity_verified_at"])
        self.db.upsert_user(702, 702, username="wrong_name", now=BASE_TIME)
        with self.assertRaisesRegex(ConflictError, "not been proven"):
            self.db.bind_admin_chat("future_alice_corrected", 702, now=BASE_TIME)
        self.assertFalse(self.db.is_admin(chat_id=702))

        self.db.upsert_user(702, 702, username="future_alice_corrected", now=BASE_TIME)
        bound = self.db.bind_admin_chat("future_alice_corrected", 702, now=BASE_TIME)
        self.assertIsNotNone(bound["identity_verified_at"])
        self.assertTrue(
            self.db.is_admin(
                username="future_alice_corrected", chat_id=702, roles=("admin",)
            )
        )

    def test_bootstrap_root_survives_username_drift_without_creating_stale_owner(
        self,
    ) -> None:
        root = self.db.bootstrap_admin(
            "owner_old", role="owner", bootstrap_root=True, now=BASE_TIME
        )
        self.assertTrue(root["is_bootstrap_owner"])
        self.db.upsert_user(801, 801, username="owner_old", now=BASE_TIME)
        root = self.db.bind_admin_chat("owner_old", 801, now=BASE_TIME)
        self.db.upsert_user(801, 801, username="owner_new", now=BASE_TIME)
        renamed = self.db.bind_admin_chat("owner_new", 801, now=BASE_TIME)
        self.assertEqual(renamed["id"], root["id"])

        restarted = Database(self.db.path)
        restarted.initialize()
        by_username_only = restarted.bootstrap_admin(
            "owner_old", role="owner", bootstrap_root=True, now=BASE_TIME
        )
        self.assertEqual(by_username_only["id"], root["id"])
        self.assertEqual(by_username_only["username"], "owner_new")
        by_stable_chat = restarted.bootstrap_admin(
            "owner_old", 801, role="owner", bootstrap_root=True, now=BASE_TIME
        )
        self.assertEqual(by_stable_chat["id"], root["id"])
        self.assertEqual(by_stable_chat["username"], "owner_new")
        self.assertEqual(
            len([item for item in restarted.list_admins() if item["role"] == "owner"]),
            1,
        )

        connection = sqlite3.connect(self.db.path)
        try:
            connection.execute("UPDATE admins SET is_bootstrap_owner = 0")
            connection.commit()
        finally:
            connection.close()
        adopted = restarted.bootstrap_admin(
            "owner_old", role="owner", bootstrap_root=True, now=BASE_TIME
        )
        self.assertEqual(adopted["id"], root["id"])
        self.assertEqual(adopted["username"], "owner_new")
        self.assertTrue(adopted["is_bootstrap_owner"])

        restarted.upsert_user(802, 802, username="owner_old", now=BASE_TIME)
        with self.assertRaises(NotFoundError):
            restarted.bind_admin_chat("owner_old", 802, now=BASE_TIME)
        self.assertFalse(restarted.is_admin(chat_id=802))
        with self.assertRaisesRegex(ConflictError, "stable root identity"):
            restarted.bootstrap_admin(
                "owner_old", 999, role="owner", bootstrap_root=True, now=BASE_TIME
            )

    def test_bootstrap_root_disable_persists_and_verified_owner_rotation_is_safe(
        self,
    ) -> None:
        root = self.db.bootstrap_admin(
            "root_a", 811, role="owner", bootstrap_root=True, now=BASE_TIME
        )
        alternate = self.db.bootstrap_admin(
            "root_b", 812, role="owner", now=BASE_TIME
        )
        disabled = self.db.set_admin_active(root["id"], False, now=BASE_TIME)
        self.assertFalse(disabled["is_active"])

        restarted = Database(self.db.path)
        restarted.initialize()
        unchanged = restarted.bootstrap_admin(
            "stale_root_a", 811, role="owner", bootstrap_root=True, now=BASE_TIME
        )
        self.assertEqual(unchanged["id"], root["id"])
        self.assertFalse(unchanged["is_active"])
        self.assertTrue(unchanged["is_bootstrap_owner"])

        rotated = restarted.bootstrap_admin(
            "root_b", 812, role="owner", bootstrap_root=True, now=BASE_TIME
        )
        self.assertEqual(rotated["id"], alternate["id"])
        self.assertTrue(rotated["is_bootstrap_owner"])
        old = next(
            item
            for item in restarted.list_admins(active_only=False)
            if int(item["id"]) == int(root["id"])
        )
        self.assertFalse(old["is_active"])
        self.assertFalse(old["is_bootstrap_owner"])

        # A stale username-only environment follows the stable marker and
        # cannot recreate or reactivate the previous root.
        stable = restarted.bootstrap_admin(
            "root_a", role="owner", bootstrap_root=True, now=BASE_TIME
        )
        self.assertEqual(stable["id"], alternate["id"])
        self.assertEqual(len(restarted.list_admins(active_only=False)), 2)

    def test_legacy_root_adoption_rejects_a_conflicting_configured_chat(self) -> None:
        legacy = self.db.bootstrap_admin("legacy_root", 821, role="owner")
        self.assertFalse(legacy["is_bootstrap_owner"])
        with self.assertRaisesRegex(ConflictError, "stable root identity"):
            self.db.bootstrap_admin(
                "legacy_root",
                822,
                role="owner",
                bootstrap_root=True,
                now=BASE_TIME,
            )
        current = self.db.list_admins(active_only=False)
        self.assertEqual(len(current), 1)
        self.assertFalse(current[0]["is_bootstrap_owner"])

        support = self.db.bootstrap_admin("legacy_support", 823, role="support")
        with self.assertRaisesRegex(ConflictError, "proven active owner"):
            self.db.bootstrap_admin(
                support["username"],
                823,
                role="owner",
                bootstrap_root=True,
                now=BASE_TIME,
            )

    def test_username_only_root_marker_can_rotate_to_a_proven_owner(self) -> None:
        root = self.db.bootstrap_admin(
            "pending_root", role="owner", bootstrap_root=True, now=BASE_TIME
        )
        alternate = self.db.bootstrap_admin(
            "proven_alternate", 832, role="owner", now=BASE_TIME
        )
        rotated = self.db.bootstrap_admin(
            "proven_alternate",
            832,
            role="owner",
            bootstrap_root=True,
            now=BASE_TIME,
        )
        self.assertEqual(rotated["id"], alternate["id"])
        old = next(
            item
            for item in self.db.list_admins(active_only=False)
            if int(item["id"]) == int(root["id"])
        )
        self.assertFalse(old["is_bootstrap_owner"])
        self.assertTrue(rotated["is_bootstrap_owner"])


class CatalogAndInventoryTests(DatabaseTestCase):
    def test_negative_reminders_are_rejected_and_one_day_is_schedulable(self) -> None:
        category = self.db.create_category("Reminder validation", now=BASE_TIME)
        with self.assertRaisesRegex(ValidationError, "non-negative integers"):
            self.db.create_product(
                category["id"],
                "Invalid negative reminder",
                product_type="manual",
                price_amount=0,
                duration_days=2,
                reminder_days=(1, -1),
                idempotency_key="invalid-negative-reminder",
                now=BASE_TIME,
            )

        product = self.db.create_product(
            category["id"],
            "Valid one-day reminder",
            product_type="manual",
            price_amount=0,
            duration_days=2,
            reminder_days=(1,),
            idempotency_key="valid-one-day-reminder",
            now=BASE_TIME,
        )
        with self.assertRaisesRegex(ValidationError, "non-negative integers"):
            self.db.update_product(product["id"], reminder_days=(-1,), now=BASE_TIME)

        user = self.user(88)
        order = self.db.create_order(
            user["id"], product["id"], idempotency_key="one-day-reminder-order", now=BASE_TIME
        )
        self.db.update_order_status(order["id"], "awaiting_info", now=BASE_TIME)
        self.db.set_order_customer_info(
            order["id"], {"text": "customer info"}, now=BASE_TIME
        )
        self.db.update_order_status(order["id"], "processing", now=BASE_TIME)
        self.db.complete_order(order["id"], "delivery", now=BASE_TIME)
        reminders = self.db.schedule_order_reminders(order["id"], now=BASE_TIME)
        self.assertEqual([item["days_before"] for item in reminders], [1])
        self.assertEqual(
            [item["id"] for item in self.db.claim_due_reminders(now=BASE_TIME + timedelta(days=1))],
            [reminders[0]["id"]],
        )
        with self.assertRaisesRegex(ValidationError, "non-negative integers"):
            self.db.schedule_order_reminders(order["id"], days_before=(-1,), now=BASE_TIME)

    def test_rich_catalog_and_unavailable_visible_product(self) -> None:
        parent = self.db.create_category("Digital")
        child = self.db.create_category("Subscriptions", parent_id=parent["id"])
        with self.assertRaises(ConflictError):
            self.db.create_category("subscriptions", parent_id=parent["id"])
        self.assertEqual(self.db.get_category(child["id"])["parent_id"], parent["id"])
        product = self.db.create_product(
            child["id"],
            "Service",
            product_type="ready",
            price_amount=200,
            icon="service",
            short_description="Short",
            long_description="Long",
            duration_days=30,
            duration_label="One month",
            account_type="shared",
            activation="instant",
            renewable=True,
            warranty_text="7 days",
            features=("A", "B"),
            activation_instructions="Activate",
            usage_terms="Terms",
            rules_text="Rules",
            rules_url="https://example.test/rules",
            reserve_enabled=True,
            info_request_text="Send profile",
            completion_text="Done",
            delivery_instructions="Keep private",
            reminder_days=(10, 2),
            available=False,
            visible=True,
            idempotency_key="rich-product",
        )
        self.assertEqual(json.loads(product["features_json"]), ["A", "B"])
        self.assertFalse(product["is_available"])
        self.assertEqual([item["id"] for item in self.db.list_products(category_id=child["id"])], [product["id"]])
        self.assertFalse(self.db.update_product(product["id"], is_available=False)["is_available"])

        stock = self.db.add_inventory_item(product["id"], "login:secret")
        with self.assertRaises(ConflictError):
            self.db.add_inventory_item(product["id"], "login:secret")
        self.assertGreater(stock["id"], 0)
        self.assertEqual(self.db.inventory_count(product["id"]), 1)

    def test_inventory_assignment_is_atomic_under_concurrency(self) -> None:
        product = self.product(price=100, sku="atomic")
        self.db.add_inventory_item(product["id"], "only-item")
        orders = []
        for suffix in (1, 2):
            user = self.user(suffix)
            self.db.credit_wallet(user["id"], 100, reason="seed", idempotency_key=f"seed-{suffix}")
            order = self.db.create_order(
                user["id"], product["id"], idempotency_key=f"atomic-order-{suffix}", now=BASE_TIME
            )
            orders.append(
                self.db.hold_wallet_funds(
                    order["id"], idempotency_key=f"atomic-hold-{suffix}", now=BASE_TIME
                )
            )
        self.assertTrue(all(order["status"] == "paid" for order in orders))

        def assign(order_id: int) -> str:
            try:
                return self.db.assign_inventory(order_id)["payload"]
            except OutOfStockError:
                return "out"

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(assign, [order["id"] for order in orders]))
        self.assertEqual(results.count("only-item"), 1)
        self.assertEqual(results.count("out"), 1)
        self.assertEqual(self.db.inventory_count(product["id"], status="assigned"), 1)

    def test_direct_inventory_assignment_cannot_bypass_ready_order_backlogs(self) -> None:
        product = self.product(price=100, sku="fifo-direct-assignment")
        waiting = self.user(31)
        direct_target = self.user(32)
        self.db.credit_wallet(
            waiting["id"],
            100,
            reason="fifo seed",
            idempotency_key="fifo-direct-seed",
        )
        order = self.db.create_order(
            waiting["id"],
            product["id"],
            idempotency_key="fifo-direct-order",
            now=BASE_TIME,
        )
        self.db.hold_wallet_funds(
            order["id"], idempotency_key="fifo-direct-hold", now=BASE_TIME
        )
        self.db.reserve_product(
            waiting["id"], product["id"], order_id=order["id"], now=BASE_TIME
        )
        item = self.db.add_inventory_item(product["id"], "fifo-secret")

        def direct_assign() -> str:
            try:
                self.db.assign_inventory_item_to_user(
                    item["id"], direct_target["id"], now=BASE_TIME
                )
            except ConflictError:
                return "blocked"
            return "stolen"

        def fulfill_waiting() -> dict | None:
            return self.db.fulfill_next_available_reservation(now=BASE_TIME)

        with ThreadPoolExecutor(max_workers=2) as pool:
            direct_future = pool.submit(direct_assign)
            fulfill_future = pool.submit(fulfill_waiting)
            self.assertEqual(direct_future.result(), "blocked")
            fulfilled = fulfill_future.result()

        self.assertIsNotNone(fulfilled)
        self.assertEqual(fulfilled["order_id"], order["id"])
        assigned = self.db.list_inventory_items(product["id"])[0]
        self.assertEqual(assigned["assigned_order_id"], order["id"])
        self.assertEqual(assigned["assigned_user_id"], waiting["id"])

        recovery_product = self.product(
            price=100,
            sku="processing-direct-assignment",
            reserve_enabled=False,
        )
        recovery_user = self.user(33)
        other_target = self.user(34)
        self.db.credit_wallet(
            recovery_user["id"],
            100,
            reason="processing seed",
            idempotency_key="processing-direct-seed",
        )
        recovery_order = self.db.create_order(
            recovery_user["id"],
            recovery_product["id"],
            idempotency_key="processing-direct-order",
            now=BASE_TIME,
        )
        self.db.hold_wallet_funds(
            recovery_order["id"],
            idempotency_key="processing-direct-hold",
            now=BASE_TIME,
        )
        self.db.mark_ready_order_processing(recovery_order["id"], now=BASE_TIME)
        recovery_item = self.db.add_inventory_item(
            recovery_product["id"], "processing-secret"
        )
        with self.assertRaises(ConflictError):
            self.db.assign_inventory_item_to_user(
                recovery_item["id"], other_target["id"], now=BASE_TIME
            )
        recovered = self.db.fulfill_next_processing_ready_order(now=BASE_TIME)
        self.assertIsNotNone(recovered)
        self.assertEqual(recovered["id"], recovery_order["id"])

    def test_processing_ready_fulfillment_cannot_cross_reservation_batch_boundary(self) -> None:
        product = self.product(price=0, sku="reservation-boundary")
        reservations = []
        for index in range(101):
            waiting = self.user(1_000 + index)
            order = self.db.create_order(
                waiting["id"],
                product["id"],
                idempotency_key=f"reservation-boundary-order:{index}",
                now=BASE_TIME,
            )
            reservations.append(
                self.db.reserve_product(
                    waiting["id"],
                    product["id"],
                    order_id=order["id"],
                    now=BASE_TIME,
                )
            )

        self.db.update_product(product["id"], reserve_enabled=False)
        processing_user = self.user(1_200)
        processing_order = self.db.create_order(
            processing_user["id"],
            product["id"],
            idempotency_key="reservation-boundary-processing",
            now=BASE_TIME,
        )
        self.db.mark_ready_order_processing(processing_order["id"], now=BASE_TIME)
        for index in range(101):
            self.db.add_inventory_item(
                product["id"], f"reservation-boundary-secret:{index}"
            )

        for _ in range(100):
            self.assertIsNotNone(
                self.db.fulfill_next_available_reservation(now=BASE_TIME)
            )
        self.assertIsNone(
            self.db.fulfill_next_processing_ready_order(now=BASE_TIME)
        )
        last_reserved = self.db.fulfill_next_available_reservation(now=BASE_TIME)

        self.assertIsNotNone(last_reserved)
        self.assertEqual(last_reserved["id"], reservations[-1]["id"])
        self.assertEqual(
            self.db.get_order(processing_order["id"])["status"], "processing"
        )


class CommerceTests(DatabaseTestCase):
    def test_repository_rejects_unsafe_external_urls(self) -> None:
        user = self.user()
        product = self.product(price=1_000, sku="safe-url-boundary")

        with self.assertRaises(ValidationError):
            self.db.upsert_force_join_channel(
                "@valid_channel",
                "Channel",
                invite_url="https://telegram.me.evil.example/channel",
            )
        with self.assertRaises(ValidationError):
            self.db.create_product(
                product["category_id"],
                "Unsafe rules",
                product_type="ready",
                price_amount=1_000,
                rules_url="javascript:alert(1)",
                idempotency_key="unsafe-rules-create",
            )
        with self.assertRaises(ValidationError):
            self.db.create_product(
                product["category_id"],
                "Unsupported currency",
                product_type="ready",
                price_amount=1_000,
                currency="USD",
                idempotency_key="unsupported-product-currency",
            )
        with self.assertRaises(ValidationError):
            self.db.update_product(
                product["id"], rules_url="http://example.test/rules"
            )

        order = self.db.create_order(
            user["id"], product["id"], idempotency_key="unsafe-provider-order"
        )
        with self.assertRaises(ValidationError):
            self.db.create_order_payment(
                order["id"],
                "crypto",
                idempotency_key="unsafe-provider-order-payment",
                provider_invoice_url="javascript:alert(1)",
            )
        with self.assertRaises(ValidationError):
            self.db.create_wallet_topup_payment(
                user["id"],
                1_000,
                "crypto",
                idempotency_key="unsafe-provider-topup",
                provider_invoice_url="https://localhost/invoice",
            )
        with self.assertRaises(ValidationError):
            self.db.create_wallet_topup_payment(
                user["id"],
                1_000,
                "card",
                idempotency_key="unsupported-payment-currency",
                currency="USD",
            )

    def test_sensitive_order_states_require_dedicated_domain_workflows(self) -> None:
        user = self.user()
        product = self.product(price=1_000, sku="sensitive-order-state")
        order = self.db.create_order(
            user["id"], product["id"], idempotency_key="sensitive-order-state"
        )

        for status in ("awaiting_confirmation", "paid", "completed", "refunded"):
            with self.subTest(status=status), self.assertRaises(ValidationError):
                self.db.update_order_status(order["id"], status)
        unchanged = self.db.get_order(order["id"])
        self.assertEqual(unchanged["status"], "pending_payment")
        self.assertIsNone(unchanged["paid_at"])

    def test_reservation_requires_a_paid_ready_order(self) -> None:
        user = self.user()
        product = self.product(price=1_000, sku="reservation-paid-guard")
        order = self.db.create_order(
            user["id"], product["id"], idempotency_key="reservation-unpaid-order"
        )

        with self.assertRaises(ValidationError):
            self.db.reserve_product(
                user["id"], product["id"], order_id=order["id"], now=BASE_TIME
            )
        self.assertEqual(self.db.get_order(order["id"])["status"], "pending_payment")
        connection = sqlite3.connect(self.db.path)
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM reservations WHERE order_id = ?",
                    (order["id"],),
                ).fetchone()[0],
                0,
            )
        finally:
            connection.close()

        with self.assertRaises(ValidationError):
            self.db.create_product(
                product["category_id"],
                "Manual reservation",
                product_type="manual",
                price_amount=1_000,
                reserve_enabled=True,
                idempotency_key="manual-reservation-create",
            )
        with self.assertRaises(ValidationError):
            self.db.update_product(product["id"], product_type="manual")

        self.db.credit_wallet(
            user["id"], 1_000, reason="seed", idempotency_key="reservation-seed"
        )
        self.db.hold_wallet_funds(
            order["id"], idempotency_key="reservation-paid-hold", now=BASE_TIME
        )
        reservation = self.db.reserve_product(
            user["id"], product["id"], order_id=order["id"], now=BASE_TIME
        )
        self.assertEqual(reservation["status"], "queued")
        self.assertEqual(self.db.get_order(order["id"])["status"], "awaiting_stock")
        self.db.update_product(product["id"], reserve_enabled=False)
        with self.assertRaises(ConflictError):
            self.db.update_product(product["id"], product_type="manual")

    def test_user_cancellation_is_atomic_and_never_cancels_a_submitted_receipt(self) -> None:
        user = self.user()
        product = self.product(price=1_000, sku="atomic-user-cancel")
        order = self.db.create_order(
            user["id"], product["id"], idempotency_key="atomic-user-cancel-order"
        )
        card = self.db.create_order_payment(
            order["id"], "card", idempotency_key="atomic-user-cancel-card"
        )
        self.db.submit_payment_receipt(
            card["id"], "receipt-file", now=BASE_TIME + timedelta(seconds=1)
        )

        with self.assertRaises(ValidationError):
            self.db.cancel_pending_payment(card["id"], user["id"], now=BASE_TIME)
        self.assertEqual(self.db.get_payment(card["id"])["status"], "verifying")
        self.assertEqual(
            self.db.get_order(order["id"])["status"], "awaiting_confirmation"
        )

        with self.assertRaises(ConflictError):
            self.db.create_order_payment(
                order["id"], "crypto", idempotency_key="atomic-user-cancel-crypto"
            )
        self.assertEqual(
            self.db.get_order(order["id"])["status"], "awaiting_confirmation"
        )

        self.db.set_payment_status(card["id"], "failed", now=BASE_TIME)
        crypto = self.db.create_order_payment(
            order["id"],
            "crypto",
            idempotency_key="atomic-user-cancel-crypto",
            provider_invoice_id="atomic-user-cancel-provider",
            provider_invoice_url="https://pay.example.test/invoice/cannot-cancel",
        )
        with self.assertRaises(ValidationError):
            self.db.cancel_pending_payment(crypto["id"], user["id"], now=BASE_TIME)
        self.assertEqual(self.db.get_payment(crypto["id"])["status"], "pending")
        self.assertEqual(
            self.db.get_order(order["id"])["status"],
            "awaiting_confirmation",
        )

        other_order = self.db.create_order(
            user["id"],
            product["id"],
            idempotency_key="atomic-user-cancel-other-order",
        )
        pending = self.db.create_order_payment(
            other_order["id"],
            "card",
            idempotency_key="atomic-user-cancel-pending",
        )
        self.db.cancel_pending_payment(pending["id"], user["id"], now=BASE_TIME)
        self.assertEqual(self.db.get_payment(pending["id"])["status"], "cancelled")
        self.assertEqual(self.db.get_order(other_order["id"])["status"], "cancelled")

    def test_partial_wallet_hold_is_idempotent_and_expiry_refunds(self) -> None:
        user = self.user()
        product = self.product(price=1_000, sku="wallet")
        self.db.credit_wallet(user["id"], 700, reason="deposit", idempotency_key="deposit")
        order = self.db.create_order(
            user["id"], product["id"], idempotency_key="wallet-order", now=BASE_TIME
        )
        held = self.db.hold_wallet_funds(
            order["id"], max_amount=900, idempotency_key="wallet-hold", now=BASE_TIME
        )
        self.assertEqual(held["wallet_held_amount"], 700)
        self.assertEqual(held["payable_amount"], 300)
        repeated = self.db.hold_wallet_funds(
            order["id"], max_amount=900, idempotency_key="wallet-hold", now=BASE_TIME
        )
        self.assertEqual(repeated["wallet_held_amount"], 700)
        self.assertEqual(self.db.wallet_balance(user["id"]), 0)

        expired = self.db.expire_unpaid_orders(now=BASE_TIME + timedelta(minutes=31))
        self.assertEqual(expired, [order["id"]])
        self.assertEqual(self.db.get_order(order["order_number"])["status"], "expired")
        self.assertEqual(self.db.wallet_balance(user["id"]), 700)
        self.assertEqual(len(self.db.list_wallet_entries(user["id"], limit=10)), 3)

    def test_discounts_are_single_and_released_on_expiry(self) -> None:
        user = self.user()
        product = self.product(price=1_000, sku="discount")
        first = self.db.create_discount(
            "TEN", discount_type="percent", value=10, max_uses=1, now=BASE_TIME
        )
        self.db.create_discount("FIXED", discount_type="fixed", value=50, now=BASE_TIME)
        order = self.db.create_order(
            user["id"], product["id"], idempotency_key="discount-order", now=BASE_TIME
        )
        discounted = self.db.apply_discount(order["id"], "ten", now=BASE_TIME)
        self.assertEqual(discounted["discount_amount"], 100)
        self.assertEqual(
            self.db.apply_discount(order["id"], "TEN", now=BASE_TIME)["discount_amount"],
            100,
        )
        with self.assertRaises(ConflictError):
            self.db.apply_discount(order["id"], "FIXED", now=BASE_TIME)
        self.db.expire_unpaid_orders(now=BASE_TIME + timedelta(minutes=31))

        second = self.db.create_order(
            user["id"], product["id"], idempotency_key="discount-order-2", now=BASE_TIME
        )
        self.assertEqual(
            self.db.apply_discount(second["id"], first["code"], now=BASE_TIME)["discount_amount"],
            100,
        )

    def test_unique_payment_amounts_order_payment_and_wallet_topup(self) -> None:
        product = self.product(price=1_000, sku="payment")
        users = [self.user(1), self.user(2)]
        orders = [
            self.db.create_order(
                user["id"], product["id"], idempotency_key=f"pay-order-{index}", now=BASE_TIME
            )
            for index, user in enumerate(users)
        ]
        payments = [
            self.db.create_order_payment(
                order["id"], "card", idempotency_key=f"pay-{index}", now=BASE_TIME
            )
            for index, order in enumerate(orders)
        ]
        self.assertEqual({payment["payable_amount"] for payment in payments}, {1_000, 1_001})
        found = self.db.find_pending_payment_by_amount(payments[0]["payable_amount"], method="card")
        self.assertEqual(found["id"], payments[0]["id"])
        self.assertEqual(
            self.db.get_payment_by_number(payments[0]["payment_number"])["id"], payments[0]["id"]
        )
        self.assertEqual(
            self.db.attach_payment_receipt(
                payments[0]["id"], "file-1", now=BASE_TIME
            )["receipt_file_id"],
            "file-1",
        )
        paid = self.db.mark_payment_paid(payments[0]["id"], external_reference="bank-ref-1", now=BASE_TIME)
        self.assertEqual(paid["order_id"], orders[0]["id"])
        self.assertEqual(paid["user_id"], users[0]["id"])
        self.assertEqual(
            self.db.get_payment_by_external_reference("bank-ref-1")["id"],
            payments[0]["id"],
        )
        self.assertEqual(self.db.get_order(orders[0]["id"])["status"], "paid")

        topup = self.db.create_wallet_topup_payment(
            users[1]["id"],
            500,
            "crypto",
            idempotency_key="crypto-topup",
            provider_invoice_id="invoice-1",
            provider_invoice_url="https://provider.test/i/1",
            now=BASE_TIME,
        )
        self.assertEqual(self.db.list_pending_provider_payments(method="crypto")[0]["id"], topup["id"])
        self.db.mark_payment_paid(topup["id"], external_reference="chain-ref-1", now=BASE_TIME)
        self.assertEqual(self.db.wallet_balance(users[1]["id"]), 500)
        self.db.mark_payment_paid(topup["id"], external_reference="chain-ref-1", now=BASE_TIME)
        self.assertEqual(self.db.wallet_balance(users[1]["id"]), 500)

    def test_paid_payment_rejects_a_different_external_reference(self) -> None:
        user = self.user()
        payment = self.db.create_wallet_topup_payment(
            user["id"],
            500,
            "card",
            idempotency_key="reference-conflict-payment",
            now=BASE_TIME,
        )

        first = self.db.mark_payment_paid(
            payment["id"], external_reference="bank-reference-1", now=BASE_TIME
        )
        replay = self.db.mark_payment_paid(
            payment["id"], external_reference="bank-reference-1", now=BASE_TIME
        )

        self.assertEqual(first["external_reference"], "bank-reference-1")
        self.assertEqual(replay["external_reference"], "bank-reference-1")
        self.assertEqual(self.db.wallet_balance(user["id"]), 500)
        with self.assertRaises(ConflictError):
            self.db.mark_payment_paid(
                payment["id"], external_reference="bank-reference-2", now=BASE_TIME
            )
        self.assertIsNone(
            self.db.get_payment_by_external_reference("bank-reference-2")
        )
        self.assertEqual(self.db.wallet_balance(user["id"]), 500)

    def test_active_crypto_topup_cannot_be_replaced_with_another_amount(self) -> None:
        user = self.user()
        first = self.db.create_wallet_topup_payment(
            user["id"],
            500,
            "crypto",
            idempotency_key="crypto-topup-non-cancellable-first",
            provider_invoice_id="crypto-topup-non-cancellable-provider",
            provider_invoice_url="https://provider.test/i/non-cancellable",
            unique_amount_window=0,
            now=BASE_TIME,
        )

        with self.assertRaises(ConflictError):
            self.db.create_wallet_topup_payment(
                user["id"],
                750,
                "crypto",
                idempotency_key="crypto-topup-non-cancellable-second",
                provider_invoice_id="crypto-topup-should-not-exist",
                provider_invoice_url="https://provider.test/i/should-not-exist",
                unique_amount_window=0,
                now=BASE_TIME + timedelta(minutes=1),
            )
        with self.assertRaises(ConflictError):
            self.db.create_wallet_topup_payment(
                user["id"],
                500,
                "crypto",
                idempotency_key="crypto-topup-same-amount-different-provider",
                provider_invoice_id="different-provider-identity",
                provider_invoice_url="https://provider.test/i/different-provider",
                unique_amount_window=0,
                now=BASE_TIME + timedelta(minutes=1),
            )

        unchanged = self.db.get_payment(first["id"])
        self.assertEqual(unchanged["status"], "pending")
        self.assertEqual(
            [payment["id"] for payment in self.db.list_pending_provider_payments()],
            [first["id"]],
        )

    def test_provisional_crypto_topup_invoice_attachment_is_exact_and_idempotent(self) -> None:
        user = self.user(45)
        other = self.user(46)
        provisional = self.db.create_wallet_topup_payment(
            user["id"],
            100_000,
            "crypto",
            idempotency_key="provisional-crypto-topup",
            unique_amount_window=0,
            now=BASE_TIME,
        )
        self.assertNotIn(
            provisional["id"],
            [row["id"] for row in self.db.list_pending_provider_payments()],
        )
        attached = self.db.attach_crypto_invoice(
            provisional["id"],
            user["id"],
            "provisional-provider-id",
            "https://pay.example.test/invoice/provisional",
            now=BASE_TIME,
        )
        replay = self.db.attach_crypto_invoice(
            provisional["id"],
            user["id"],
            "provisional-provider-id",
            "https://pay.example.test/invoice/provisional",
            now=BASE_TIME,
        )
        self.assertEqual(attached["id"], replay["id"])
        with self.assertRaises(ConflictError):
            self.db.attach_crypto_invoice(
                provisional["id"],
                user["id"],
                "different-provider-id",
                "https://pay.example.test/invoice/different",
            )
        with self.assertRaises(DatabaseError):
            self.db.attach_crypto_invoice(
                provisional["id"],
                other["id"],
                "provisional-provider-id",
                "https://pay.example.test/invoice/provisional",
            )
        with self.assertRaises(ValidationError):
            self.db.attach_crypto_invoice(
                provisional["id"],
                user["id"],
                "provisional-provider-id",
                "https://localhost/invoice",
            )

        second_user = self.user(47)
        second = self.db.create_wallet_topup_payment(
            second_user["id"],
            100_000,
            "crypto",
            idempotency_key="provisional-crypto-topup-collision",
            unique_amount_window=0,
            now=BASE_TIME,
        )
        with self.assertRaises(ConflictError):
            self.db.attach_crypto_invoice(
                second["id"],
                second_user["id"],
                "provisional-provider-id",
                "https://pay.example.test/invoice/provisional",
            )

    def test_concurrent_provisional_crypto_order_creation_reuses_one_intent(self) -> None:
        user = self.user(48)
        product = self.product(price=1_000, sku="concurrent-provisional-order")
        order = self.db.create_order(
            user["id"],
            product["id"],
            idempotency_key="concurrent-provisional-order",
            now=BASE_TIME,
        )

        def create(suffix: int) -> int:
            payment = self.db.create_order_payment(
                order["id"],
                "crypto",
                idempotency_key=f"concurrent-provisional-payment:{suffix}",
                requested_amount=1_000,
                unique_amount_window=0,
                now=BASE_TIME,
            )
            return int(payment["id"])

        with ThreadPoolExecutor(max_workers=2) as executor:
            payment_ids = list(executor.map(create, (1, 2)))
        self.assertEqual(len(set(payment_ids)), 1)
        payment = self.db.get_payment(payment_ids[0])
        self.assertIsNone(payment["provider_invoice_id"])
        self.assertEqual(payment["base_amount"], 1_000)

    def test_wallet_topup_allows_only_one_active_method_under_concurrency(self) -> None:
        user = self.user(62)

        def create(method: str) -> tuple[str, str, int | None]:
            kwargs = (
                {
                    "provider_invoice_id": "cross-method-provider",
                    "provider_invoice_url": "https://pay.example.test/cross-method",
                    "unique_amount_window": 0,
                }
                if method == "crypto"
                else {"unique_amount_window": 5}
            )
            try:
                payment = self.db.create_wallet_topup_payment(
                    user["id"],
                    20_000,
                    method,
                    idempotency_key=f"cross-method:{method}",
                    now=BASE_TIME,
                    **kwargs,
                )
            except ConflictError:
                return method, "conflict", None
            return method, "created", int(payment["id"])

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(create, ("card", "crypto")))

        self.assertEqual(
            sorted(status for _method, status, _payment_id in results),
            ["conflict", "created"],
        )
        active = self.db.list_active_wallet_topup_payments(user["id"])
        self.assertEqual(len(active), 1)
        winner_method = str(active[0]["method"])
        replay_kwargs = (
            {
                "provider_invoice_id": "cross-method-provider",
                "provider_invoice_url": "https://pay.example.test/cross-method",
                "unique_amount_window": 0,
            }
            if winner_method == "crypto"
            else {"unique_amount_window": 5}
        )
        replay = self.db.create_wallet_topup_payment(
            user["id"],
            20_000,
            winner_method,
            idempotency_key=f"cross-method:{winner_method}",
            now=BASE_TIME,
            **replay_kwargs,
        )
        self.assertEqual(replay["id"], active[0]["id"])

        losing_method = "crypto" if winner_method == "card" else "card"
        losing_kwargs = (
            {
                "provider_invoice_id": "late-cross-method-provider",
                "provider_invoice_url": "https://pay.example.test/late-cross-method",
                "unique_amount_window": 0,
            }
            if losing_method == "crypto"
            else {"unique_amount_window": 5}
        )
        with self.assertRaises(ConflictError):
            self.db.create_wallet_topup_payment(
                user["id"],
                20_000,
                losing_method,
                idempotency_key=f"cross-method-loser:{losing_method}",
                now=BASE_TIME,
                **losing_kwargs,
            )

    def test_local_expiry_sweeps_preserve_crypto_until_provider_terminal_status(self) -> None:
        user = self.user()
        product = self.product(price=1_000, sku="crypto-provider-terminal-expiry")
        self.db.credit_wallet(
            user["id"],
            400,
            reason="crypto expiry refund test",
            idempotency_key="crypto-expiry-refund-credit",
            now=BASE_TIME,
        )
        order = self.db.create_order(
            user["id"],
            product["id"],
            idempotency_key="crypto-expiry-order",
            now=BASE_TIME,
        )
        self.db.hold_wallet_funds(
            order["id"],
            idempotency_key="crypto-expiry-wallet-hold",
            now=BASE_TIME,
        )
        order_payment = self.db.create_order_payment(
            order["id"],
            "crypto",
            idempotency_key="crypto-expiry-order-payment",
            provider_invoice_id="crypto-expiry-order-provider",
            provider_invoice_url="https://provider.test/i/crypto-expiry-order",
            unique_amount_window=0,
            now=BASE_TIME,
        )
        topup = self.db.create_wallet_topup_payment(
            user["id"],
            250,
            "crypto",
            idempotency_key="crypto-expiry-topup-payment",
            provider_invoice_id="crypto-expiry-topup-provider",
            provider_invoice_url="https://provider.test/i/crypto-expiry-topup",
            unique_amount_window=0,
            now=BASE_TIME,
        )
        after_deadline = BASE_TIME + timedelta(minutes=31)

        self.assertEqual(self.db.expire_unpaid_orders(now=after_deadline), [])
        self.assertEqual(self.db.expire_pending_payments(now=after_deadline), [])
        self.assertEqual(self.db.get_payment(order_payment["id"])["status"], "pending")
        self.assertEqual(self.db.get_payment(topup["id"])["status"], "pending")
        self.assertEqual(
            self.db.get_order(order["id"])["status"], "awaiting_confirmation"
        )
        self.assertEqual(self.db.wallet_balance(user["id"]), 0)

        self.db.set_payment_status(
            order_payment["id"], "failed", now=after_deadline
        )
        self.assertEqual(self.db.get_payment(order_payment["id"])["status"], "failed")
        self.assertEqual(self.db.get_order(order["id"])["status"], "expired")
        self.assertEqual(self.db.wallet_balance(user["id"]), 400)

        self.db.set_payment_status(topup["id"], "failed", now=after_deadline)
        self.assertEqual(self.db.get_payment(topup["id"])["status"], "failed")
        self.assertEqual(self.db.wallet_balance(user["id"]), 400)

    def test_confirmed_card_event_replay_requires_identical_terms(self) -> None:
        user = self.user()
        payment = self.db.create_wallet_topup_payment(
            user["id"],
            500,
            "card",
            idempotency_key="strict-card-event-terms",
            now=BASE_TIME,
        )
        reference = "strict-bank-reference-1"
        occurred_at = (BASE_TIME + timedelta(seconds=1)).isoformat()
        raw_payload = {
            "amount": int(payment["payable_amount"]),
            "reference": reference,
            "occurred_at": occurred_at,
        }

        self.db.mark_payment_paid(
            payment["id"],
            external_reference=reference,
            raw_payload=raw_payload,
            card_event_amount=int(payment["payable_amount"]),
            card_event_occurred_at=occurred_at,
            now=BASE_TIME + timedelta(seconds=2),
        )
        original_event = self.db.get_card_payment_event(reference)
        self.assertIsNotNone(original_event)

        mismatches = {
            "amount": {
                "card_event_amount": int(payment["payable_amount"]) + 1,
                "card_event_occurred_at": occurred_at,
                "raw_payload": raw_payload,
            },
            "occurred_at": {
                "card_event_amount": int(payment["payable_amount"]),
                "card_event_occurred_at": (
                    BASE_TIME + timedelta(seconds=2)
                ).isoformat(),
                "raw_payload": raw_payload,
            },
            "raw_payload": {
                "card_event_amount": int(payment["payable_amount"]),
                "card_event_occurred_at": occurred_at,
                "raw_payload": {**raw_payload, "bank": "different"},
            },
        }
        for label, mismatch in mismatches.items():
            with self.subTest(label=label), self.assertRaises(ConflictError):
                self.db.mark_payment_paid(
                    payment["id"],
                    external_reference=reference,
                    **mismatch,
                )

        self.assertEqual(self.db.get_card_payment_event(reference), original_event)
        self.assertEqual(self.db.wallet_balance(user["id"]), 500)

    def test_card_event_failure_rolls_back_wallet_credit(self) -> None:
        user = self.user()
        payment = self.db.create_wallet_topup_payment(
            user["id"],
            500,
            "card",
            idempotency_key="atomic-card-wallet-rollback",
            now=BASE_TIME,
        )
        reference = "atomic-wallet-reference-1"
        occurred_at = (BASE_TIME + timedelta(seconds=1)).isoformat()
        raw_payload = {
            "amount": int(payment["payable_amount"]),
            "reference": reference,
            "occurred_at": occurred_at,
        }

        with patch.object(
            self.db,
            "_record_card_payment_event_in_transaction",
            side_effect=DatabaseError("simulated event insert failure"),
        ):
            with self.assertRaises(DatabaseError):
                self.db.mark_payment_paid(
                    payment["id"],
                    external_reference=reference,
                    raw_payload=raw_payload,
                    card_event_amount=int(payment["payable_amount"]),
                    card_event_occurred_at=occurred_at,
                )

        unchanged = self.db.get_payment(payment["id"])
        self.assertEqual(unchanged["status"], "pending")
        self.assertIsNone(unchanged["external_reference"])
        self.assertEqual(self.db.wallet_balance(user["id"]), 0)
        self.assertIsNone(self.db.get_card_payment_event(reference))

    def test_recent_terminal_card_amount_is_quarantined_before_reuse(self) -> None:
        product = self.product(price=10_000, sku="card-amount-cooldown")
        first_user = self.user(1)
        first_order = self.db.create_order(
            first_user["id"],
            product["id"],
            idempotency_key="card-amount-cooldown-first-order",
            now=BASE_TIME,
        )
        first = self.db.create_order_payment(
            first_order["id"],
            "card",
            idempotency_key="card-amount-cooldown-first-payment",
            unique_amount_window=1,
            now=BASE_TIME,
        )
        self.db.cancel_pending_payment(
            first["id"], first_user["id"], now=BASE_TIME + timedelta(minutes=1)
        )

        second_user = self.user(2)
        second_order = self.db.create_order(
            second_user["id"],
            product["id"],
            idempotency_key="card-amount-cooldown-second-order",
            now=BASE_TIME + timedelta(minutes=2),
        )
        second = self.db.create_order_payment(
            second_order["id"],
            "card",
            idempotency_key="card-amount-cooldown-second-payment",
            unique_amount_window=1,
            now=BASE_TIME + timedelta(minutes=2),
        )
        self.assertEqual(second["payable_amount"], first["payable_amount"] + 1)

        # The quarantine is bounded rather than permanently consuming a code.
        third_user = self.user(3)
        after_cooldown = BASE_TIME + timedelta(hours=25)
        third_order = self.db.create_order(
            third_user["id"],
            product["id"],
            idempotency_key="card-amount-cooldown-third-order",
            now=after_cooldown,
        )
        third = self.db.create_order_payment(
            third_order["id"],
            "card",
            idempotency_key="card-amount-cooldown-third-payment",
            unique_amount_window=1,
            now=after_cooldown,
        )
        self.assertEqual(third["payable_amount"], first["payable_amount"])

    def test_single_user_cannot_exhaust_card_amount_pool(self) -> None:
        """Per-user quota must preserve an amount slot for another buyer."""

        attacker = self.user(1)
        victim = self.user(2)
        product = self.product(price=10_000, sku="amount-pool-quota")
        attacker_was_limited = False

        # The production allocator exposes 1,000 distinct payable amounts for
        # one base amount (base through base+999). Without an active-intent
        # quota, one Telegram account can occupy every slot with separate
        # orders and deny card payment to all other customers.
        for index in range(1_000):
            order = self.db.create_order(
                attacker["id"],
                product["id"],
                idempotency_key=f"attacker-order-{index}",
                now=BASE_TIME,
            )
            try:
                self.db.create_order_payment(
                    order["id"],
                    "card",
                    idempotency_key=f"attacker-payment-{index}",
                    now=BASE_TIME,
                )
            except ConflictError:
                attacker_was_limited = True
                break

        self.assertTrue(
            attacker_was_limited,
            "one user must be rate/quota limited before occupying all 1,000 amount slots",
        )

        victim_order = self.db.create_order(
            victim["id"],
            product["id"],
            idempotency_key="victim-order-after-attack",
            now=BASE_TIME,
        )
        victim_payment = self.db.create_order_payment(
            victim_order["id"],
            "card",
            idempotency_key="victim-payment-after-attack",
            now=BASE_TIME,
        )
        self.assertEqual(victim_payment["status"], "pending")

    def test_submitted_receipt_stays_reviewable_after_payment_deadline(self) -> None:
        user = self.user()
        product = self.product(price=1_000, sku="receipt-review")
        order = self.db.create_order(
            user["id"], product["id"], idempotency_key="receipt-order", now=BASE_TIME
        )
        payment = self.db.create_order_payment(
            order["id"], "card", idempotency_key="receipt-payment", now=BASE_TIME
        )
        self.db.attach_payment_receipt(payment["id"], "receipt-file", now=BASE_TIME)
        self.db.set_payment_status(payment["id"], "verifying", now=BASE_TIME)

        after_deadline = BASE_TIME + timedelta(minutes=31)
        self.assertEqual(self.db.expire_unpaid_orders(now=after_deadline), [])
        self.assertEqual(self.db.expire_pending_payments(now=after_deadline), [])
        self.assertEqual(self.db.get_order(order["id"])["status"], "awaiting_confirmation")
        self.assertEqual(self.db.get_payment(payment["id"])["status"], "verifying")

    def test_submitted_receipt_stays_pending_until_explicit_admin_decision(self) -> None:
        user = self.user()
        product = self.product(price=1_000, sku="receipt-grace-anchor")
        order = self.db.create_order(
            user["id"], product["id"], idempotency_key="receipt-grace-order", now=BASE_TIME
        )
        payment = self.db.create_order_payment(
            order["id"], "card", idempotency_key="receipt-grace-payment", now=BASE_TIME
        )
        self.db.submit_payment_receipt(
            payment["id"], "receipt-one", now=BASE_TIME + timedelta(minutes=1)
        )
        self.db.submit_payment_receipt(
            payment["id"], "receipt-two", now=BASE_TIME + timedelta(days=6)
        )

        after_original_grace = BASE_TIME + timedelta(days=7, minutes=31)
        self.assertEqual(
            self.db.expire_pending_payments(now=after_original_grace),
            [],
        )
        self.assertEqual(self.db.expire_unpaid_orders(now=after_original_grace), [])
        self.assertEqual(self.db.get_payment(payment["id"])["status"], "verifying")
        self.assertEqual(self.db.get_order(order["id"])["status"], "awaiting_confirmation")
        self.db.submit_payment_receipt(
            payment["id"], "receipt-three", now=after_original_grace
        )
        self.db.mark_payment_paid(
            payment["id"], external_reference="delayed-receipt-review", now=after_original_grace
        )
        self.assertEqual(self.db.get_payment(payment["id"])["status"], "paid")
        self.assertEqual(self.db.get_order(order["id"])["status"], "paid")

    def test_first_receipt_is_rejected_after_the_payment_deadline(self) -> None:
        user = self.user()
        product = self.product(price=1_000, sku="late-first-receipt")
        order = self.db.create_order(
            user["id"], product["id"], idempotency_key="late-first-receipt-order", now=BASE_TIME
        )
        payment = self.db.create_order_payment(
            order["id"], "card", idempotency_key="late-first-receipt-payment", now=BASE_TIME
        )

        with self.assertRaises(ValidationError):
            self.db.submit_payment_receipt(
                payment["id"], "late-receipt", now=BASE_TIME + timedelta(minutes=31)
            )
        unchanged = self.db.get_payment(payment["id"])
        self.assertEqual(unchanged["status"], "pending")
        self.assertIsNone(unchanged["receipt_file_id"])


class SupportReferralAndOperationsTests(DatabaseTestCase):
    def test_admin_assignment_and_internal_free_order_are_not_commercial_purchases(
        self,
    ) -> None:
        inviter = self.user(91)
        invitee = self.user(92)
        admin = self.db.bootstrap_admin("gift_owner", 9_991, role="owner")
        product = self.product(price=100, sku="commercial-after-gift")
        gift_item = self.db.add_inventory_item(product["id"], "gift-payload")
        self.db.record_referral(inviter["id"], invitee["id"], now=BASE_TIME)
        self.db.create_reward_rule(
            "gift-product-guard",
            event_type="product_purchase",
            amount=30,
            now=BASE_TIME,
        )
        self.db.create_reward_rule(
            "gift-first-guard",
            event_type="first_purchase",
            amount=70,
            now=BASE_TIME,
        )

        gift = self.db.assign_inventory_item_to_user(
            gift_item["id"],
            invitee["id"],
            actor_admin_id=admin["id"],
            now=BASE_TIME,
        )
        self.assertEqual(gift["order_origin"], "admin_assignment")
        self.assertIsNotNone(gift["reward_processed_at"])
        self.assertEqual(self.db.grant_purchase_rewards(gift["id"]), [])

        free_product = self.product(price=0, sku="internal-free-acquisition")
        free_order = self.db.create_order(
            invitee["id"],
            free_product["id"],
            idempotency_key="internal-free-acquisition",
            now=BASE_TIME,
        )
        self.assertEqual(free_order["order_origin"], "customer")
        self.assertEqual(free_order["status"], "paid")
        self.assertEqual(self.db.grant_purchase_rewards(free_order["id"]), [])
        self.assertEqual(self.db.wallet_balance(inviter["id"]), 0)

        self.db.credit_wallet(
            invitee["id"], 100, reason="seed", idempotency_key="real-purchase-seed"
        )
        paid_order = self.db.create_order(
            invitee["id"],
            product["id"],
            idempotency_key="real-purchase-after-gift",
            now=BASE_TIME + timedelta(minutes=1),
        )
        paid_order = self.db.hold_wallet_funds(
            paid_order["id"],
            idempotency_key="real-purchase-after-gift-hold",
            now=BASE_TIME + timedelta(minutes=1),
        )
        rewards = self.db.grant_purchase_rewards(paid_order["id"])
        self.assertEqual({int(reward["amount"]) for reward in rewards}, {30, 70})
        self.assertEqual(self.db.wallet_balance(inviter["id"]), 100)

        summary = self.db.user_summary(invitee["id"])
        self.assertEqual(summary["order_count"], 3)
        self.assertEqual(summary["successful_order_count"], 1)
        self.assertEqual(summary["purchase_total"], 100)
        report = self.db.summary_report()
        self.assertEqual(report["order_count"], 3)
        self.assertEqual(report["completed_order_count"], 1)
        self.assertEqual(report["gross_revenue"], 100)

    def test_faq_ticket_and_outbound_message_queues(self) -> None:
        user = self.user()
        admin = self.db.bootstrap_admin("owner", 999, role="owner")
        category = self.db.create_faq_category("Payment")
        faq = self.db.create_faq("How?", "Like this.", category_id=category["id"])
        self.assertEqual(self.db.list_faq_categories()[0]["id"], category["id"])
        self.assertEqual(self.db.list_faqs(category_id=category["id"])[0]["id"], faq["id"])
        self.assertEqual(self.db.get_faq(faq["id"])["answer"], "Like this.")

        ticket = self.db.create_ticket(user["id"], "Need help", idempotency_key="ticket-1")
        self.db.add_ticket_message(
            ticket["id"],
            "Initial",
            sender_type="user",
            sender_user_id=user["id"],
            idempotency_key="ticket-initial",
        )
        self.db.add_ticket_message(
            ticket["id"],
            "Reply",
            sender_type="admin",
            sender_id=admin["id"],
            idempotency_key="ticket-reply",
        )
        self.assertEqual(self.db.get_ticket(ticket["ticket_number"])["status"], "answered")
        self.assertEqual(len(self.db.list_ticket_messages(ticket["id"])), 2)
        self.assertEqual(self.db.list_tickets(user_id=user["id"], status="answered")[0]["id"], ticket["id"])

        queued = self.db.queue_message(
            "Hello", recipient_user_id=user["id"], idempotency_key="message-1", now=BASE_TIME
        )
        claimed = self.db.claim_outbound_messages(now=BASE_TIME)
        self.assertEqual(claimed[0]["id"], queued["id"])
        self.assertEqual(
            self.db.mark_outbound_message(queued["id"], success=True, telegram_message_id=11)["status"],
            "sent",
        )

    def test_referral_reward_is_exactly_once(self) -> None:
        inviter = self.user(1)
        invitee = self.user(2)
        self.db.record_referral(inviter["id"], invitee["id"], now=BASE_TIME)
        self.db.create_reward_rule("start-bonus", event_type="start", amount=50, now=BASE_TIME)
        first = self.db.grant_referral_reward(invitee["id"], "start", "update-10", now=BASE_TIME)
        second = self.db.grant_referral_reward(invitee["id"], "start", "update-10", now=BASE_TIME)
        self.assertEqual(first[0]["id"], second[0]["id"])
        self.assertEqual(self.db.wallet_balance(inviter["id"]), 50)
        summary = self.db.referral_summary(inviter["id"])
        self.assertEqual(summary["invited_count"], 1)
        self.assertEqual(summary["qualified_count"], 1)
        self.assertEqual(summary["reward_total"], 50)

    def test_reward_window_boundaries_and_start_product_guard(self) -> None:
        inviter = self.user(11)
        invitee = self.user(12)
        self.db.record_referral(inviter["id"], invitee["id"], now=BASE_TIME)
        starts_at = BASE_TIME + timedelta(hours=1)
        ends_at = BASE_TIME + timedelta(hours=2)
        self.db.create_reward_rule(
            "windowed-start",
            event_type="start",
            amount=75,
            starts_at=starts_at,
            ends_at=ends_at,
            now=BASE_TIME,
        )

        self.assertEqual(
            self.db.grant_referral_reward(
                invitee["id"], "start", "before", now=BASE_TIME
            ),
            [],
        )
        self.assertEqual(
            len(
                self.db.grant_referral_reward(
                    invitee["id"], "start", "at-start", now=starts_at
                )
            ),
            1,
        )
        self.assertEqual(
            self.db.grant_referral_reward(
                invitee["id"], "start", "at-end", now=ends_at
            ),
            [],
        )
        self.assertEqual(self.db.wallet_balance(inviter["id"]), 75)
        with self.assertRaises(ValidationError):
            self.db.create_reward_rule(
                "invalid-start-product",
                event_type="start",
                amount=10,
                product_id=999,
            )

    def test_reward_processing_without_a_referral_is_a_noop(self) -> None:
        customer = self.user()
        self.assertEqual(
            self.db.grant_referral_reward(
                customer["id"], "start", "ordinary-start", now=BASE_TIME
            ),
            [],
        )

    def test_first_purchase_reward_counts_paid_orders_waiting_for_stock(self) -> None:
        inviter = self.user(1)
        invitee = self.user(2)
        product = self.product(price=100, sku="first-purchase")
        self.db.record_referral(inviter["id"], invitee["id"], now=BASE_TIME)
        self.db.create_reward_rule(
            "first-order", event_type="first_purchase", amount=50, now=BASE_TIME
        )
        self.db.credit_wallet(
            invitee["id"], 200, reason="seed", idempotency_key="first-seed"
        )

        first = self.db.create_order(
            invitee["id"], product["id"], idempotency_key="first-order-1", now=BASE_TIME
        )
        self.db.hold_wallet_funds(
            first["id"], idempotency_key="first-order-1-hold", now=BASE_TIME
        )
        self.db.grant_purchase_rewards(first["id"], now=BASE_TIME)
        self.db.reserve_product(
            invitee["id"],
            product["id"],
            order_id=first["id"],
            now=BASE_TIME,
        )

        second = self.db.create_order(
            invitee["id"], product["id"], idempotency_key="first-order-2", now=BASE_TIME
        )
        self.db.hold_wallet_funds(
            second["id"], idempotency_key="first-order-2-hold", now=BASE_TIME
        )
        self.db.grant_purchase_rewards(second["id"], now=BASE_TIME)

        self.assertEqual(self.db.wallet_balance(inviter["id"]), 50)
        self.assertEqual(self.db.user_summary(invitee["id"])["purchase_total"], 200)

    def test_reminders_backup_reports_and_foreign_keys(self) -> None:
        user = self.user()
        product = self.product(
            price=100,
            sku="reminder",
            product_type="manual",
            reserve_enabled=False,
        )
        self.db.credit_wallet(user["id"], 100, reason="seed", idempotency_key="reminder-seed")
        order = self.db.create_order(
            user["id"], product["id"], idempotency_key="reminder-order", now=BASE_TIME
        )
        paid = self.db.hold_wallet_funds(
            order["id"], idempotency_key="reminder-hold", now=BASE_TIME
        )
        self.assertEqual(paid["status"], "paid")
        self.db.update_order_status(order["id"], "awaiting_info", now=BASE_TIME)
        self.db.set_order_customer_info(
            order["id"], {"text": "reminder fixture customer info"}, now=BASE_TIME
        )
        self.db.update_order_status(order["id"], "processing", now=BASE_TIME)
        delivered = self.db.complete_order(
            order["id"], "reminder-test-delivery", now=BASE_TIME
        )
        self.assertEqual(delivered["status"], "completed")
        self.assertIsNotNone(delivered["subscription_ends_at"])
        reminders = self.db.schedule_order_reminders(order["id"], now=BASE_TIME)
        self.assertEqual({item["days_before"] for item in reminders}, {7, 3, 1})
        due = self.db.claim_due_reminders(now=BASE_TIME + timedelta(days=23))
        self.assertEqual([item["days_before"] for item in due], [7])
        self.db.mark_reminder_sent(due[0]["id"], 123)

        report = self.db.summary_report()
        self.assertEqual(report["order_count"], 1)
        self.assertEqual(report["gross_revenue"], 100)
        self.assertEqual(self.db.user_summary(user["id"])["purchase_total"], 100)

        backup_directory = self.root / "backups"
        backup = self.db.create_backup(backup_directory)
        backup_path = Path(backup["path"])
        self.assertEqual(backup["status"], "completed")
        self.assertTrue(backup_path.exists())
        copied = sqlite3.connect(backup_path)
        try:
            self.assertEqual(copied.execute("SELECT COUNT(*) FROM users").fetchone()[0], 1)
        finally:
            copied.close()
        self.assertEqual(self.db.list_backups()[0]["id"], backup["id"])

    def test_ticket_reply_and_status_commit_with_exact_notice(self) -> None:
        customer = self.user(72)
        admin = self.db.bootstrap_admin("ticket_atomic_admin", 8072, role="admin")
        ticket = self.db.create_ticket(
            customer["id"],
            "Atomic ticket",
            "initial",
            idempotency_key="atomic-ticket",
            now=BASE_TIME,
        )

        with patch.object(
            self.db,
            "_queue_user_message_in_transaction",
            side_effect=DatabaseError("simulated outbox failure"),
        ):
            with self.assertRaises(DatabaseError):
                self.db.add_ticket_message(
                    ticket["id"],
                    "reply",
                    sender_type="admin",
                    sender_id=admin["id"],
                    idempotency_key="atomic-ticket-reply",
                    outbound_body="reply notice",
                    outbound_idempotency_key="atomic-ticket-reply-notice",
                    now=BASE_TIME,
                )
        self.assertEqual(len(self.db.list_ticket_messages(ticket["id"])), 1)

        reply = self.db.add_ticket_message(
            ticket["id"],
            "reply",
            sender_type="admin",
            sender_id=admin["id"],
            idempotency_key="atomic-ticket-reply",
            outbound_body="reply notice",
            outbound_idempotency_key="atomic-ticket-reply-notice",
            now=BASE_TIME,
        )
        replay = self.db.add_ticket_message(
            ticket["id"],
            "reply",
            sender_type="admin",
            sender_id=admin["id"],
            idempotency_key="atomic-ticket-reply",
            outbound_body="reply notice",
            outbound_idempotency_key="atomic-ticket-reply-notice",
            now=BASE_TIME,
        )
        self.assertEqual(reply["id"], replay["id"])
        with self.assertRaises(ConflictError):
            self.db.add_ticket_message(
                ticket["id"],
                "different",
                sender_type="admin",
                sender_id=admin["id"],
                idempotency_key="atomic-ticket-reply",
                outbound_body="different notice",
                outbound_idempotency_key="atomic-ticket-reply-notice",
                now=BASE_TIME,
            )

        with patch.object(
            self.db,
            "_queue_user_message_in_transaction",
            side_effect=DatabaseError("simulated status outbox failure"),
        ):
            with self.assertRaises(DatabaseError):
                self.db.set_ticket_status(
                    ticket["id"],
                    "closed",
                    assigned_admin_id=admin["id"],
                    outbound_body="closed notice",
                    outbound_idempotency_key="atomic-ticket-closed-notice",
                    now=BASE_TIME,
                )
        self.assertEqual(self.db.get_ticket(ticket["id"])["status"], "answered")

        self.db.set_ticket_status(
            ticket["id"],
            "closed",
            assigned_admin_id=admin["id"],
            outbound_body="closed notice",
            outbound_idempotency_key="atomic-ticket-closed-notice",
            now=BASE_TIME,
        )
        self.db.set_ticket_status(
            ticket["id"],
            "closed",
            assigned_admin_id=admin["id"],
            outbound_body="closed notice",
            outbound_idempotency_key="atomic-ticket-closed-notice",
            now=BASE_TIME,
        )
        self.assertEqual(self.db.get_ticket(ticket["id"])["status"], "closed")
        self.assertIsNotNone(
            self.db.get_outbound_message_by_idempotency_key(
                "atomic-ticket-closed-notice"
            )
        )

    def test_manual_completion_and_information_requests_reject_ready_orders(self) -> None:
        user = self.user(77)
        ready = self.product(price=100, sku="ready-manual-guard")
        self.db.credit_wallet(
            user["id"],
            100,
            reason="seed",
            idempotency_key="ready-manual-guard-seed",
        )
        order = self.db.create_order(
            user["id"],
            ready["id"],
            idempotency_key="ready-manual-guard-order",
            now=BASE_TIME,
        )
        self.db.hold_wallet_funds(
            order["id"],
            idempotency_key="ready-manual-guard-hold",
            now=BASE_TIME,
        )
        reservation = self.db.reserve_product(
            user["id"], ready["id"], order_id=order["id"], now=BASE_TIME
        )

        with self.assertRaises(ValidationError):
            self.db.complete_order(order["id"], "must-not-bypass-inventory", now=BASE_TIME)
        with self.assertRaises(ValidationError):
            self.db.update_order_status(
                order["id"], "awaiting_info", admin_note="must-not-strand", now=BASE_TIME
            )

        unchanged = self.db.get_order(order["id"])
        self.assertEqual(unchanged["status"], "awaiting_stock")
        self.assertIsNone(unchanged["delivered_payload"])
        connection = sqlite3.connect(self.db.path)
        try:
            queued = connection.execute(
                "SELECT status, order_id FROM reservations WHERE id = ?",
                (reservation["id"],),
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual(queued, ("queued", order["id"]))

    def test_manual_information_submission_atomically_enters_processing(self) -> None:
        customer = self.user(79)
        stranger = self.user(80)
        product = self.product(
            price=0,
            sku="atomic-manual-info",
            product_type="manual",
            reserve_enabled=False,
        )
        order = self.db.create_order(
            customer["id"],
            product["id"],
            idempotency_key="atomic-manual-info-order",
            now=BASE_TIME,
        )
        self.db.update_order_status(order["id"], "awaiting_info", now=BASE_TIME)
        payload = {"text": "first", "file_id": "photo-1", "file_kind": "photo"}

        submitted = self.db.submit_manual_order_info(
            order["id"], customer["id"], payload, now=BASE_TIME
        )
        replay = self.db.submit_manual_order_info(
            order["id"], customer["id"], payload, now=BASE_TIME
        )
        self.assertEqual(submitted["status"], "processing")
        self.assertEqual(replay["customer_info_json"], submitted["customer_info_json"])

        replacement = self.db.submit_manual_order_info(
            order["id"],
            customer["id"],
            {"text": "corrected"},
            now=BASE_TIME + timedelta(minutes=1),
        )
        self.assertEqual(
            json.loads(replacement["customer_info_json"])["text"], "corrected"
        )
        with self.assertRaises(ValidationError):
            self.db.submit_manual_order_info(
                order["id"], stranger["id"], {"text": "not mine"}, now=BASE_TIME
            )

        self.db.complete_order(
            order["id"], "done", now=BASE_TIME + timedelta(minutes=2)
        )
        with self.assertRaises(ValidationError):
            self.db.submit_manual_order_info(
                order["id"], customer["id"], {"text": "too late"}, now=BASE_TIME
            )

    @unittest.skipUnless(os.name == "posix", "POSIX permission bits are required")
    def test_backup_paths_have_explicit_private_posix_modes(self) -> None:
        previous_umask = os.umask(0)
        try:
            managed_directory = self.root / "managed-backups"
            managed = self.db.create_backup(managed_directory)
            managed_path = Path(managed["path"])
            self.assertEqual(stat.S_IMODE(managed_directory.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(managed_path.stat().st_mode), 0o600)

            delegated_directory = self.root / "delegated-backups"
            delegated_directory.mkdir(mode=0o777)
            delegated_directory.chmod(0o777)
            delegated = self.db.create_backup(delegated_directory)
            self.assertEqual(
                stat.S_IMODE(delegated_directory.stat().st_mode),
                0o700,
            )
            self.assertEqual(
                stat.S_IMODE(Path(delegated["path"]).stat().st_mode),
                0o600,
            )

            public_parent = self.root / "public-parent"
            public_parent.mkdir(mode=0o777)
            public_parent.chmod(0o777)
            explicit_path = public_parent / "explicit.sqlite3"
            self.db.create_backup(explicit_path)
            self.assertEqual(stat.S_IMODE(public_parent.stat().st_mode), 0o777)
            self.assertEqual(stat.S_IMODE(explicit_path.stat().st_mode), 0o600)

            explicit_path.chmod(0o666)
            self.db.create_backup(explicit_path, overwrite=True)
            self.assertEqual(stat.S_IMODE(public_parent.stat().st_mode), 0o777)
            self.assertEqual(stat.S_IMODE(explicit_path.stat().st_mode), 0o600)

            created_parent = self.root / "new-parent" / "nested"
            nested_path = created_parent / "explicit.sqlite3"
            self.db.create_backup(nested_path)
            self.assertEqual(stat.S_IMODE(created_parent.stat().st_mode), 0o700)
            self.assertEqual(
                stat.S_IMODE(created_parent.parent.stat().st_mode),
                0o700,
            )
            self.assertEqual(stat.S_IMODE(nested_path.stat().st_mode), 0o600)
        finally:
            os.umask(previous_umask)


if __name__ == "__main__":
    unittest.main()
