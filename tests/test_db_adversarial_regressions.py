from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.db import ConflictError, Database, ValidationError


BASE = datetime(2026, 2, 1, 12, 0, tzinfo=timezone.utc)


class DatabaseAdversarialRegressionTests(unittest.TestCase):
    """Regression coverage for failures found by the independent DB audit."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = Database(self.root / "audit.sqlite3", busy_timeout_ms=20_000)
        self.db.initialize()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def user(self, suffix: int) -> dict:
        return self.db.upsert_user(
            10_000 + suffix,
            20_000 + suffix,
            username=f"audit_user_{suffix}",
            now=BASE,
        )

    def product(self, name: str, *, price: int = 1_000) -> dict:
        categories = self.db.list_categories(parent_id=None, active_only=False)
        category = categories[0] if categories else self.db.create_category("Audit", now=BASE)
        return self.db.create_product(
            category["id"],
            name,
            product_type="ready",
            price_amount=price,
            duration_days=30,
            reserve_enabled=True,
            idempotency_key=f"product:{name}",
            now=BASE,
        )

    def test_previous_release_schema_migrates_idempotently(self) -> None:
        old_path = self.root / "old.sqlite3"
        current_schema = Path(self.db.schema_path).read_text(encoding="utf-8")
        old_schema = current_schema.replace(
            "    source_admin_update_id INTEGER UNIQUE,\n", ""
        )
        old_schema = old_schema.replace("    icon TEXT,\n    description TEXT,\n", "")
        old_schema = old_schema.replace(
            "    attachment_kind TEXT CHECK (attachment_kind IN ('photo', 'document')),\n",
            "",
        )
        old_schema = old_schema.replace(
            "    order_origin TEXT NOT NULL DEFAULT 'customer'\n"
            "        CHECK (order_origin IN ('customer', 'admin_assignment')),\n",
            "",
        )
        old_schema = old_schema.replace(
            "'refund_confirmed', 'dismiss', 'credit_confirmed', 'provider_completed',",
            "'refund_confirmed', 'dismiss', 'provider_completed',",
        ).replace(
            "(action IN ('refund_confirmed', 'dismiss', 'credit_confirmed')",
            "(action IN ('refund_confirmed', 'dismiss')",
        )
        old_schema = old_schema.replace(
            """CREATE TABLE IF NOT EXISTS processed_admin_updates (
    update_id INTEGER PRIMARY KEY,
    fingerprint TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'started'
        CHECK (status IN ('started', 'completed')),
    effect_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT
);""",
            """CREATE TABLE IF NOT EXISTS processed_admin_updates (
    update_id INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL
);""",
        )
        stamp = BASE.isoformat(timespec="seconds")
        connection = sqlite3.connect(old_path)
        connection.row_factory = sqlite3.Row
        try:
            connection.executescript(old_schema)
            connection.execute(
                "INSERT INTO processed_admin_updates(update_id,created_at) VALUES (77,?)",
                (stamp,),
            )
            connection.execute(
                "INSERT INTO categories(name,is_active,sort_order,created_at,updated_at) "
                "VALUES ('legacy-category',1,0,?,?)",
                (stamp, stamp),
            )
            connection.execute(
                "INSERT INTO users(telegram_user_id,chat_id,username,username_key,joined_at,"
                "is_blocked,created_at,updated_at) VALUES (1,1,'legacy','legacy',?,0,?,?)",
                (stamp, stamp, stamp),
            )
            connection.execute(
                "INSERT INTO products(category_id,name,product_type,price_amount,created_at,updated_at) "
                "VALUES (1,'legacy-product','ready',100,?,?)",
                (stamp, stamp),
            )
            connection.execute(
                "INSERT INTO orders(order_number,idempotency_key,user_id,product_id,"
                "product_name_snapshot,product_type_snapshot,unit_price_amount,"
                "subtotal_amount,payable_amount,currency,status,expires_at,created_at,updated_at) "
                "VALUES ('ADM-LEGACY','admin-inventory:1:1',1,1,'legacy-product','ready',"
                "0,0,0,'TOMAN','completed',?,?,?)",
                (stamp, stamp, stamp),
            )
            connection.execute(
                "INSERT INTO tickets(ticket_number,idempotency_key,user_id,subject,status,created_at,updated_at) "
                "VALUES ('T-OLD','ticket-old',1,'legacy ticket','open',?,?)",
                (stamp, stamp),
            )
            connection.execute(
                "INSERT INTO ticket_messages(ticket_id,sender_type,sender_user_id,body,"
                "attachment_file_id,idempotency_key,created_at) "
                "VALUES (1,'user',1,'legacy attachment','legacy-file','message-old',?)",
                (stamp,),
            )
            connection.execute(
                "INSERT INTO admins(username,username_key,chat_id,role,is_active,"
                "created_at,updated_at) VALUES ('legacy-owner','legacy-owner',99,"
                "'owner',1,?,?)",
                (stamp, stamp),
            )
            connection.execute(
                "INSERT INTO payments(payment_number,idempotency_key,user_id,purpose,"
                "method,base_amount,payable_amount,currency,status,provider_invoice_id,"
                "expires_at,created_at,updated_at) VALUES ('PAY-OLD','pay-old',1,"
                "'wallet_topup','crypto',100,100,'TOMAN','failed','INV-OLD',?,?,?)",
                (stamp, stamp, stamp),
            )
            connection.execute(
                "INSERT INTO provider_payment_events(provider,payment_id,provider_reference,"
                "provider_status,received_amount,amount_evidence,disposition,raw_payload_json,"
                "raw_payload_sha256,created_at) VALUES ('plisio',1,'INV-OLD','expired',"
                "'1','nonzero','review','{}','legacy-sha',?)",
                (stamp,),
            )
            connection.execute(
                "INSERT INTO provider_payment_event_resolutions(event_id,action,"
                "actor_admin_id,note,created_at) VALUES (1,'dismiss',1,'legacy decision',?)",
                (stamp,),
            )
            connection.commit()
        finally:
            connection.close()

        migrated = Database(old_path)
        migrated.initialize()
        migrated.initialize()

        self.assertEqual(migrated.get_category(1)["name"], "legacy-category")
        self.assertEqual(migrated.list_ticket_messages(1)[0]["attachment_kind"], "document")
        connection = sqlite3.connect(old_path)
        connection.row_factory = sqlite3.Row
        try:
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(list(connection.execute("PRAGMA foreign_key_check")), [])
            self.assertEqual(
                connection.execute(
                    "SELECT value FROM schema_meta WHERE key='schema_version'"
                ).fetchone()[0],
                "11",
            )
            order_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(orders)")
            }
            self.assertIn("order_origin", order_columns)
            category_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(categories)")
            }
            self.assertIn("source_admin_update_id", category_columns)
            inventory_columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(inventory_items)")
            }
            self.assertIn("source_admin_update_id", inventory_columns)
            admin_update_columns = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(processed_admin_updates)"
                )
            }
            self.assertTrue(
                {
                    "fingerprint",
                    "status",
                    "effect_json",
                    "updated_at",
                    "completed_at",
                }.issubset(admin_update_columns)
            )
            migrated_update = connection.execute(
                "SELECT * FROM processed_admin_updates WHERE update_id = 77"
            ).fetchone()
            self.assertEqual(migrated_update["fingerprint"], "legacy:77")
            self.assertEqual(migrated_update["status"], "completed")
            self.assertEqual(migrated_update["completed_at"], stamp)
            self.assertEqual(
                connection.execute(
                    "SELECT order_origin FROM orders WHERE order_number='ADM-LEGACY'"
                ).fetchone()[0],
                "admin_assignment",
            )
            resolution_sql = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' "
                "AND name='provider_payment_event_resolutions'"
            ).fetchone()[0]
            self.assertIn("credit_confirmed", resolution_sql)
            resolution = connection.execute(
                "SELECT action,note FROM provider_payment_event_resolutions WHERE id=1"
            ).fetchone()
            self.assertEqual(tuple(resolution), ("dismiss", "legacy decision"))
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE provider_payment_event_resolutions SET note='changed' WHERE id=1"
                )
        finally:
            connection.close()

    def test_live_v5_admin_shape_migrates_before_new_column_indexes(self) -> None:
        """Fresh-schema DDL must not reference columns absent from live v5."""

        old_path = self.root / "live-v5-shape.sqlite3"
        stamp = BASE.isoformat(timespec="seconds")
        connection = sqlite3.connect(old_path)
        try:
            connection.executescript(
                """
                PRAGMA foreign_keys = ON;
                CREATE TABLE schema_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                INSERT INTO schema_meta(key, value)
                VALUES ('schema_version', '5');
                CREATE TABLE users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_user_id INTEGER NOT NULL UNIQUE,
                    chat_id INTEGER NOT NULL UNIQUE,
                    username TEXT,
                    username_key TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    customer_name TEXT,
                    phone TEXT,
                    email TEXT,
                    joined_at TEXT NOT NULL,
                    is_blocked INTEGER NOT NULL DEFAULT 0
                        CHECK (is_blocked IN (0, 1)),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE admins (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL,
                    username_key TEXT NOT NULL UNIQUE,
                    chat_id INTEGER UNIQUE,
                    role TEXT NOT NULL
                        CHECK (role IN ('owner', 'admin', 'support')),
                    is_active INTEGER NOT NULL DEFAULT 1
                        CHECK (is_active IN (0, 1)),
                    created_by_admin_id INTEGER
                        REFERENCES admins(id) ON DELETE SET NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            connection.execute(
                "INSERT INTO users(telegram_user_id,chat_id,username,username_key,"
                "joined_at,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
                (91, 91, "live_owner", "live_owner", stamp, stamp, stamp),
            )
            connection.execute(
                "INSERT INTO admins(username,username_key,chat_id,role,is_active,"
                "created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
                ("live_owner", "live_owner", 91, "owner", 1, stamp, stamp),
            )
            connection.commit()
        finally:
            connection.close()

        migrated = Database(old_path)
        migrated.initialize()
        migrated.initialize()

        connection = sqlite3.connect(old_path)
        connection.row_factory = sqlite3.Row
        try:
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(list(connection.execute("PRAGMA foreign_key_check")), [])
            self.assertEqual(
                connection.execute(
                    "SELECT value FROM schema_meta WHERE key='schema_version'"
                ).fetchone()[0],
                "11",
            )
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(admins)")
            }
            self.assertIn("identity_verified_at", columns)
            self.assertIn("is_bootstrap_owner", columns)
            owner = connection.execute(
                "SELECT * FROM admins WHERE chat_id = 91"
            ).fetchone()
            self.assertEqual(owner["identity_verified_at"], stamp)
            self.assertEqual(owner["is_bootstrap_owner"], 0)
            index = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type='index' "
                "AND name='uq_admin_bootstrap_owner'"
            ).fetchone()
            self.assertIsNotNone(index)
            self.assertIn("is_bootstrap_owner", index["sql"])
        finally:
            connection.close()

    def test_legacy_destination_path_backup_table_migrates(self) -> None:
        legacy_path = self.root / "legacy-backups.sqlite3"
        legacy = Database(legacy_path)
        legacy.initialize()
        connection = sqlite3.connect(legacy_path)
        try:
            connection.executescript(
                """
                DROP TABLE backups;
                CREATE TABLE backups (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    destination_path TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('running','completed','failed')),
                    size_bytes INTEGER,
                    sha256 TEXT,
                    error_text TEXT,
                    created_at TEXT NOT NULL,
                    completed_at TEXT
                );
                """
            )
            connection.commit()
        finally:
            connection.close()

        migrated = Database(legacy_path)
        migrated.initialize()
        backup = migrated.create_backup(self.root / "legacy-copy.sqlite3", now=BASE)
        self.assertEqual(backup["status"], "completed")

    def test_order_and_payment_idempotency_keys_cannot_cross_users(self) -> None:
        product = self.product("cross-user")
        first_user, second_user = self.user(1), self.user(2)
        first_order = self.db.create_order(
            first_user["id"], product["id"], idempotency_key="shared-order-key", now=BASE
        )
        with self.assertRaises(ConflictError):
            self.db.create_order(
                second_user["id"], product["id"], idempotency_key="shared-order-key", now=BASE
            )

        second_order = self.db.create_order(
            second_user["id"], product["id"], idempotency_key="second-order", now=BASE
        )
        self.db.create_order_payment(
            first_order["id"], "card", idempotency_key="shared-payment-key", now=BASE
        )
        with self.assertRaises(ConflictError):
            self.db.create_order_payment(
                second_order["id"], "card", idempotency_key="shared-payment-key", now=BASE
            )

    def test_failed_last_external_payment_reopens_order(self) -> None:
        product, user = self.product("failed-provider"), self.user(1)
        order = self.db.create_order(
            user["id"], product["id"], idempotency_key="failed-order", now=BASE
        )
        payment = self.db.create_order_payment(
            order["id"],
            "crypto",
            idempotency_key="failed-payment",
            provider_invoice_id="provider-operation",
            provider_invoice_url="https://provider.invalid/invoice",
            unique_amount_window=0,
            now=BASE,
        )
        self.db.set_payment_status(payment["id"], "failed", now=BASE)
        self.assertEqual(self.db.get_order(order["id"])["status"], "pending_payment")

    def test_expired_order_cannot_be_revived_by_full_discount(self) -> None:
        product, user = self.product("expired-discount"), self.user(1)
        order = self.db.create_order(
            user["id"], product["id"], idempotency_key="expired-order", now=BASE
        )
        self.db.create_discount(
            "EXPIRED-FREE", discount_type="percent", value=100, now=BASE
        )
        with self.assertRaises(ValidationError):
            self.db.apply_discount(
                order["id"], "EXPIRED-FREE", now=BASE + timedelta(minutes=31)
            )

    def test_partial_hold_cannot_be_refunded_by_a_status_shortcut(self) -> None:
        product, user = self.product("partial-refund"), self.user(1)
        self.db.credit_wallet(
            user["id"], 400, reason="seed", idempotency_key="refund-seed", now=BASE
        )
        order = self.db.create_order(
            user["id"], product["id"], idempotency_key="partial-order", now=BASE
        )
        self.db.hold_wallet_funds(
            order["id"], max_amount=400, idempotency_key="partial-hold", now=BASE
        )
        self.db.refund_wallet_hold(
            order["id"], amount=100, idempotency_key="partial-release", now=BASE
        )
        payment = self.db.create_order_payment(
            order["id"], "card", idempotency_key="remaining-payment", now=BASE
        )
        self.db.mark_payment_paid(
            payment["id"], external_reference="external-payment", now=BASE
        )
        with self.assertRaises(ValidationError):
            self.db.update_order_status(order["id"], "refunded", now=BASE)
        self.assertEqual(self.db.wallet_balance(user["id"]), 100)
        persisted = self.db.get_order(order["id"])
        self.assertEqual(persisted["status"], "paid")
        self.assertEqual(persisted["wallet_refunded_amount"], 100)

    def test_wallet_topup_refund_cannot_leave_credit_spendable(self) -> None:
        user = self.user(1)
        payment = self.db.create_wallet_topup_payment(
            user["id"], 2_000, "card", idempotency_key="topup", now=BASE
        )
        self.db.mark_payment_paid(
            payment["id"], external_reference="bank-reference", now=BASE
        )
        with self.assertRaises(ValidationError):
            self.db.set_payment_status(payment["id"], "refunded", now=BASE)
        self.assertEqual(self.db.wallet_balance(user["id"]), 2_000)

    def test_wallet_topup_credit_collision_rolls_back_paid_transition(self) -> None:
        user = self.user(1)
        payment = self.db.create_wallet_topup_payment(
            user["id"], 2_000, "card", idempotency_key="topup-collision", now=BASE
        )
        self.db.credit_wallet(
            user["id"],
            1,
            reason="unrelated credit",
            idempotency_key=f"payment:{payment['id']}:wallet-credit",
            now=BASE,
        )

        with self.assertRaises(ConflictError):
            self.db.mark_payment_paid(
                payment["id"], external_reference="colliding-bank-reference", now=BASE
            )

        persisted = self.db.get_payment(payment["id"])
        self.assertEqual(persisted["status"], "pending")
        self.assertIsNone(persisted["external_reference"])
        self.assertIsNone(persisted["confirmed_at"])
        self.assertEqual(self.db.wallet_balance(user["id"]), 1)
        self.assertEqual(len(self.db.list_wallet_entries(user["id"], limit=10)), 1)

    def test_wallet_idempotency_rejects_cross_operation_collisions(self) -> None:
        user, product = self.user(1), self.product("wallet-key", price=500)
        self.db.credit_wallet(
            user["id"],
            500,
            reason="seed",
            idempotency_key="shared-wallet-key",
            now=BASE,
        )
        with self.assertRaises(ConflictError):
            self.db.adjust_wallet(
                user["id"],
                500,
                reason="different operation",
                entry_type="admin_adjustment",
                idempotency_key="shared-wallet-key",
                now=BASE,
            )

        order = self.db.create_order(
            user["id"], product["id"], idempotency_key="wallet-key-order", now=BASE
        )
        self.db.hold_wallet_funds(
            order["id"], idempotency_key="real-hold-key", now=BASE
        )
        with self.assertRaises(ConflictError):
            self.db.refund_wallet_hold(
                order["id"],
                amount=500,
                idempotency_key="shared-wallet-key",
                now=BASE,
            )
        self.assertEqual(self.db.get_order(order["id"])["wallet_refunded_amount"], 0)

    def test_product_idempotency_rejects_a_different_payload(self) -> None:
        first_category = self.db.create_category("First", now=BASE)
        second_category = self.db.create_category("Second", now=BASE)
        self.db.create_product(
            first_category["id"],
            "First product",
            product_type="ready",
            price_amount=100,
            idempotency_key="shared-product-key",
            now=BASE,
        )
        with self.assertRaises(ConflictError):
            self.db.create_product(
                second_category["id"],
                "Second product",
                product_type="manual",
                price_amount=999,
                idempotency_key="shared-product-key",
                now=BASE,
            )

    def test_ticket_idempotency_rejects_cross_user_and_cross_ticket_keys(self) -> None:
        first, second = self.user(1), self.user(2)
        first_ticket = self.db.create_ticket(
            first["id"], "First subject", idempotency_key="ticket-one", now=BASE
        )
        with self.assertRaises(ConflictError):
            self.db.create_ticket(
                second["id"], "Second subject", idempotency_key="ticket-one", now=BASE
            )
        second_ticket = self.db.create_ticket(
            second["id"], "Second subject", idempotency_key="ticket-two", now=BASE
        )
        self.db.add_ticket_message(
            first_ticket["id"],
            "First message",
            sender_type="user",
            sender_id=first["id"],
            idempotency_key="shared-message-key",
            now=BASE,
        )
        with self.assertRaises(ConflictError):
            self.db.add_ticket_message(
                second_ticket["id"],
                "Second message",
                sender_type="user",
                sender_id=second["id"],
                idempotency_key="shared-message-key",
                now=BASE,
            )

    def test_outbound_idempotency_rejects_cross_recipient_collision(self) -> None:
        first, second = self.user(1), self.user(2)
        self.db.queue_outbound_message(
            "First body",
            recipient_user_id=first["id"],
            idempotency_key="shared-outbound-key",
            now=BASE,
        )
        with self.assertRaises(ConflictError):
            self.db.queue_outbound_message(
                "Second body",
                recipient_user_id=second["id"],
                idempotency_key="shared-outbound-key",
                now=BASE,
            )

    def test_expiring_last_short_payment_reopens_unexpired_order(self) -> None:
        user, product = self.user(1), self.product("short-payment")
        order = self.db.create_order(
            user["id"], product["id"], idempotency_key="short-order", now=BASE
        )
        payment = self.db.create_order_payment(
            order["id"],
            "card",
            idempotency_key="short-payment",
            expires_in_minutes=5,
            now=BASE,
        )
        self.assertEqual(
            self.db.expire_pending_payments(now=BASE + timedelta(minutes=6)),
            [payment["id"]],
        )
        self.assertEqual(self.db.get_order(order["id"])["status"], "pending_payment")

    def test_legacy_backup_without_error_column_migrates(self) -> None:
        path = self.root / "legacy-no-error.sqlite3"
        legacy = Database(path)
        legacy.initialize()
        connection = sqlite3.connect(path)
        try:
            connection.executescript(
                """
                DROP TABLE backups;
                CREATE TABLE backups (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    destination_path TEXT NOT NULL,
                    status TEXT NOT NULL,
                    size_bytes INTEGER,
                    sha256 TEXT,
                    created_at TEXT NOT NULL,
                    completed_at TEXT
                );
                INSERT INTO backups(
                    destination_path,status,size_bytes,sha256,created_at,completed_at
                ) VALUES ('legacy.sqlite3','completed',123,'abc',
                          '2026-01-01T00:00:00+00:00','2026-01-01T00:01:00+00:00');
                """
            )
            connection.commit()
        finally:
            connection.close()
        migrated = Database(path)
        migrated.initialize()
        self.assertEqual(migrated.list_backups()[0]["path"], "legacy.sqlite3")

    def test_retry_cannot_resurrect_a_sent_outbound_message(self) -> None:
        user = self.user(1)
        message = self.db.queue_outbound_message(
            "Already delivered",
            recipient_user_id=user["id"],
            idempotency_key="sent-message",
            now=BASE,
        )
        self.db.claim_outbound_message(message["id"], now=BASE)
        self.db.mark_outbound_message(
            message["id"], success=True, telegram_message_id=55, now=BASE
        )
        with self.assertRaises(ConflictError):
            self.db.schedule_outbound_retry(
                message["id"], "late duplicate failure", now=BASE + timedelta(seconds=1)
            )

    def test_purchase_reward_window_uses_event_time_during_late_recovery(self) -> None:
        inviter, invitee = self.user(1), self.user(2)
        self.db.record_referral(inviter["id"], invitee["id"], now=BASE)
        self.db.create_reward_rule(
            "short-window",
            event_type="product_purchase",
            amount=25,
            starts_at=BASE - timedelta(hours=1),
            ends_at=BASE + timedelta(hours=1),
            now=BASE - timedelta(hours=1),
        )
        product = self.product("late-window", price=1_000)
        self.db.credit_wallet(
            invitee["id"],
            1_000,
            reason="late reward window purchase seed",
            idempotency_key="late-window-seed",
            now=BASE,
        )
        order = self.db.create_order(
            invitee["id"], product["id"], idempotency_key="late-window-order", now=BASE
        )
        self.db.hold_wallet_funds(
            order["id"], idempotency_key="late-window-hold", now=BASE
        )
        self.db.grant_purchase_rewards(order["id"], now=BASE + timedelta(days=2))
        self.assertEqual(self.db.wallet_balance(inviter["id"]), 25)

    def test_reward_rule_created_after_purchase_is_not_retroactive(self) -> None:
        inviter, invitee = self.user(1), self.user(2)
        self.db.record_referral(inviter["id"], invitee["id"], now=BASE)
        product = self.product("non-retroactive", price=0)
        order = self.db.create_order(
            invitee["id"],
            product["id"],
            idempotency_key="non-retroactive-order",
            now=BASE,
        )
        self.db.create_reward_rule(
            "future-rule",
            event_type="product_purchase",
            amount=40,
            now=BASE + timedelta(hours=1),
        )
        self.db.grant_purchase_rewards(order["id"], now=BASE + timedelta(hours=2))
        self.assertEqual(self.db.wallet_balance(inviter["id"]), 0)


if __name__ == "__main__":
    unittest.main()
