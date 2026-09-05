from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path
from typing import Any

from app.bot import BotApplication
from app.config import Settings
from app.db import Database
from app.keyboards import SHOP
from app.utils import utc_now
from tests.test_bot import FakeTelegram


class MembershipTelegram(FakeTelegram):
    def __init__(self) -> None:
        super().__init__()
        self.memberships: dict[tuple[str, int], bool] = {}

    def is_chat_member(self, chat_id: str | int, user_id: int) -> bool:
        return self.memberships.get((str(chat_id), int(user_id)), True)


class UserFlowAdversarialRegressionTests(unittest.TestCase):
    USER = {"id": 1001, "username": "adversarial_user", "first_name": "User"}
    OTHER = {"id": 1002, "username": "adversarial_other", "first_name": "Other"}

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.settings = Settings(
            bot_token="adversarial-test-token",
            database_path=root / "bot.sqlite3",
            data_dir=root / "data",
            bootstrap_admin_username="adversarial_owner",
            bootstrap_admin_chat_id=9001,
            receipt_delay_seconds=0,
            job_interval_seconds=3600,
        )
        self.db = Database(self.settings.database_path)
        self.telegram = MembershipTelegram()
        self.app = BotApplication(self.settings, self.db, self.telegram)  # type: ignore[arg-type]
        self.app.initialize()
        self.db.set_setting("completion_notice_pending", False)
        self.db.set_setting("card_number", "6037997512345678")
        self.db.set_setting("card_owner", "Test Store")
        self.user = self.db.upsert_user(
            self.USER["id"], self.USER["id"], username=self.USER["username"]
        )
        self.user = self.db.update_user_profile(
            self.user["id"], customer_name="Adversarial User", phone="09120000000"
        )
        self.other = self.db.upsert_user(
            self.OTHER["id"], self.OTHER["id"], username=self.OTHER["username"]
        )
        self.category = self.db.create_category("Adversarial category")
        self.product = self.db.create_product(
            self.category["id"],
            "Adversarial product",
            product_type="manual",
            price_amount=100_000,
            idempotency_key="adversarial-product",
        )
        self._update_id = 1

    def tearDown(self) -> None:
        self.temp.cleanup()

    def callback(self, data: str, actor: dict[str, Any] | None = None) -> dict[str, Any]:
        update_id = self._update_id
        self._update_id += 1
        source = actor or self.USER
        return {
            "update_id": update_id,
            "callback_query": {
                "id": f"callback-{update_id}",
                "from": copy.deepcopy(source),
                "message": {
                    "message_id": max(1, update_id - 1),
                    "chat": {"id": int(source["id"]), "type": "private"},
                },
                "data": data,
            },
        }

    def message(
        self,
        *,
        text: str | None = None,
        contact: dict[str, Any] | None = None,
        photo: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        update_id = self._update_id
        self._update_id += 1
        payload: dict[str, Any] = {
            "message_id": update_id,
            "chat": {"id": self.USER["id"], "type": "private"},
            "from": copy.deepcopy(self.USER),
        }
        if text is not None:
            payload["text"] = text
        if contact is not None:
            payload["contact"] = copy.deepcopy(contact)
        if photo is not None:
            payload["photo"] = copy.deepcopy(photo)
        return {"update_id": update_id, "message": payload}

    @staticmethod
    def callbacks(surface: dict[str, Any]) -> list[str]:
        return [
            str(button["callback_data"])
            for row in (surface.get("reply_markup") or {}).get("inline_keyboard", [])
            for button in row
            if button.get("callback_data")
        ]

    @staticmethod
    def button_count(surface: dict[str, Any]) -> int:
        return sum(
            len(row)
            for row in (surface.get("reply_markup") or {}).get("inline_keyboard", [])
        )

    def test_malformed_callbacks_fail_closed_and_are_answered_once(self) -> None:
        self.db.set_setting("payment_crypto_enabled", True)
        malformed = [
            "cat:", "cat:not-int", "prod:", "prodmore:", "buy:", "order:",
            "faqcat:", "faq:", "profile:orders:x:x", "profile:transactions:-1",
            "tickets:list:1.5", "ticket:", "ticket:abc", "ticket:1:abc",
            "ticket:1:2:3", "ticketreply:", "ordersummary:", "discount:",
            "checkout:", "paywallet:", "paycard:", "paycrypto:", "receipt:",
            "cancelpay:", "orderinfo:", "topupcard:", "topupcrypto:",
        ]
        for data in malformed:
            with self.subTest(data=data):
                before = len(self.telegram.callback_answers)
                self.app.process_update(self.callback(data))
                self.assertEqual(len(self.telegram.callback_answers), before + 1)
                self.assertTrue(self.telegram.callback_answers[-1].get("show_alert"))

    def test_stale_display_callbacks_are_answered_once_with_alert(self) -> None:
        stale = [
            "cat:999999", "prod:999999", "prodmore:999999", "order:999999",
            "faqcat:999999", "faq:999999", "ticket:999999", "ordersummary:999999",
            "checkout:999999", "paycard:999999", "ticketfile:999999", "ticketmsg:999999",
        ]
        for data in stale:
            with self.subTest(data=data):
                before = len(self.telegram.callback_answers)
                self.app.process_update(self.callback(data))
                self.assertEqual(len(self.telegram.callback_answers), before + 1)
                self.assertTrue(self.telegram.callback_answers[-1].get("show_alert"))

    def test_ticket_reply_callback_checks_existence_owner_and_open_status(self) -> None:
        owned = self.db.create_ticket(
            self.user["id"], "owned", "body", idempotency_key="owned-ticket"
        )
        self.db.close_ticket(owned["id"])
        foreign = self.db.create_ticket(
            self.other["id"], "foreign", "body", idempotency_key="foreign-ticket"
        )
        for data in (
            "ticketreply:999999",
            f"ticketreply:{owned['id']}",
            f"ticketreply:{foreign['id']}",
        ):
            with self.subTest(data=data):
                self.db.clear_user_state(self.user["id"])
                self.app.process_update(self.callback(data))
                self.assertIsNone(self.db.get_user_state(self.user["id"]))
                self.assertTrue(self.telegram.callback_answers[-1].get("show_alert"))

    def test_transaction_pagination_never_drops_clamped_long_entries(self) -> None:
        for index in range(35):
            self.db.credit_wallet(
                self.user["id"],
                1,
                reason=f"TXMARK-{index:03d}-" + ("<&" * 120),
                idempotency_key=f"transaction-{index}",
                now=utc_now(),
            )
        surfaces: list[dict[str, Any]] = []
        page = 0
        while True:
            data = "profile:transactions" if page == 0 else f"profile:transactions:{page}"
            self.app.process_update(self.callback(data))
            surface = self.telegram.edits[-1]
            surfaces.append(surface)
            self.assertLessEqual(len(surface["text"]), 3900)
            if f"profile:transactions:{page + 1}" not in self.callbacks(surface):
                break
            page += 1
        rendered = "\n".join(surface["text"] for surface in surfaces)
        for index in range(35):
            self.assertEqual(rendered.count(f"TXMARK-{index:03d}"), 1)

    def test_catalog_faq_and_join_surfaces_are_paginated(self) -> None:
        for index in range(25):
            self.db.create_category(f"Root {index:02d}")
        self.app.process_update(self.message(text=SHOP))
        store = self.telegram.messages[-1]
        self.assertLessEqual(self.button_count(store), 22)
        self.assertIn("store:1", self.callbacks(store))

        many_products = self.db.create_category("Many products")
        for index in range(25):
            self.db.create_product(
                many_products["id"], f"Product {index:02d}", product_type="manual",
                price_amount=1, idempotency_key=f"product-{index}",
            )
        self.app.process_update(self.callback(f"cat:{many_products['id']}"))
        category = self.telegram.edits[-1]
        self.assertLessEqual(self.button_count(category), 22)
        self.assertIn(f"cat:{many_products['id']}:1", self.callbacks(category))

        for index in range(25):
            self.db.create_faq_category(f"FAQ {index:02d}")
        self.app.process_update(self.callback("support:faqs"))
        faqs = self.telegram.edits[-1]
        self.assertLessEqual(self.button_count(faqs), 22)
        self.assertIn("support:faqs:1", self.callbacks(faqs))

        for index in range(25):
            channel = f"@required_{index:02d}"
            self.db.upsert_force_join_channel(
                channel, f"Required {index:02d}", invite_url=f"https://t.me/required_{index:02d}"
            )
            self.telegram.memberships[(channel, self.USER["id"])] = False
        self.app.process_update(self.message(text="/start"))
        join = self.telegram.messages[-1]
        self.assertLessEqual(self.button_count(join), 14)
        self.assertIn("join:page:1", self.callbacks(join))

    def test_forced_join_long_title_is_clamped_to_telegram_limit(self) -> None:
        channel = "@very_long_required"
        self.db.upsert_force_join_channel(
            channel, "X" * 4096, invite_url="https://t.me/very_long_required"
        )
        self.telegram.memberships[(channel, self.USER["id"])] = False
        self.app.process_update(self.message(text="/start"))
        self.assertLessEqual(len(self.telegram.messages[-1]["text"]), 3900)

    def test_user_amount_and_contact_inputs_are_strict(self) -> None:
        self.db.set_user_state(self.user["id"], "wallet_topup_amount", {})
        self.app.process_update(self.message(text="abc10,000xyz"))
        self.assertEqual(
            self.db.get_user_state(self.user["id"])["state"], "wallet_topup_amount"
        )

        before = self.db.count_orders(user_id=self.user["id"])
        self.db.set_user_state(
            self.user["id"], "purchase_phone", {"product_id": self.product["id"]}
        )
        self.app.process_update(
            self.message(contact={"user_id": self.USER["id"], "phone_number": ""})
        )
        self.assertEqual(
            self.db.get_user_state(self.user["id"])["state"], "purchase_phone"
        )
        self.assertEqual(self.db.count_orders(user_id=self.user["id"]), before)

    def test_stale_states_are_cleared_and_closed_entities_are_not_mutated(self) -> None:
        order = self.db.create_order(
            self.user["id"], self.product["id"], idempotency_key="stale-payment-order"
        )
        payment = self.db.create_order_payment(
            order["id"], "card", idempotency_key="stale-payment"
        )
        self.app.process_update(self.callback(f"receipt:{payment['id']}"))
        self.db.set_payment_status(payment["id"], "cancelled")
        self.app.process_update(self.message(photo=[{"file_id": "late-receipt"}]))
        self.assertIsNone(self.db.get_user_state(self.user["id"]))
        self.assertIsNone(self.db.get_payment(payment["id"])["receipt_file_id"])

        ticket = self.db.create_ticket(
            self.user["id"], "closing", "body", idempotency_key="closing-ticket"
        )
        self.app.process_update(self.callback(f"ticketreply:{ticket['id']}"))
        self.db.close_ticket(ticket["id"])
        self.app.process_update(self.message(text="late reply"))
        self.assertIsNone(self.db.get_user_state(self.user["id"]))
        self.assertEqual(len(self.db.list_ticket_messages(ticket["id"])), 1)

        info_product = self.db.create_product(
            self.category["id"], "Info product", product_type="manual", price_amount=0,
            info_request_text="send info", idempotency_key="info-product",
        )
        info_order = self.db.create_order(
            self.user["id"], info_product["id"], idempotency_key="info-order"
        )
        self.app.fulfill_order(info_order["id"])
        self.app.process_update(self.callback(f"orderinfo:{info_order['id']}"))
        self.db.set_order_customer_info(info_order["id"], {"text": "stored info"})
        self.db.update_order_status(info_order["id"], "processing")
        self.db.complete_order(info_order["id"], "already complete")
        before_info = self.db.get_order(info_order["id"])["customer_info_json"]
        self.app.process_update(self.message(text="late customer info"))
        final = self.db.get_order(info_order["id"])
        self.assertEqual(final["customer_info_json"], before_info)
        self.assertIsNone(self.db.get_user_state(self.user["id"]))

    def test_reward_reconciliation_survives_partial_grant_after_completion(self) -> None:
        """A fulfilled order must remain eligible for unfinished reward events."""

        inviter = self.db.upsert_user(
            2001, 2001, username="reward_reconciliation_inviter"
        )
        self.db.record_referral(inviter["id"], self.user["id"])
        ready = self.db.create_product(
            self.category["id"],
            "Reward reconciliation ready product",
            product_type="ready",
            price_amount=1_000,
            idempotency_key="reward-reconciliation-product",
        )
        self.db.add_inventory_item(ready["id"], "credential: reward-reconciliation")
        self.db.create_reward_rule(
            "reward-reconciliation-product-purchase",
            event_type="product_purchase",
            amount=100,
            product_id=ready["id"],
        )
        self.db.create_reward_rule(
            "reward-reconciliation-first-purchase",
            event_type="first_purchase",
            amount=200,
        )
        self.db.credit_wallet(
            self.user["id"],
            1_000,
            reason="reward reconciliation test seed",
            idempotency_key="reward-reconciliation-seed",
        )
        order = self.db.create_order(
            self.user["id"],
            ready["id"],
            idempotency_key="reward-reconciliation-order",
        )
        paid = self.db.hold_wallet_funds(
            order["id"], idempotency_key="reward-reconciliation-hold"
        )
        self.assertEqual(paid["status"], "paid")
        self.app._reconcile_zero_external_payment_notices()
        success_notice = self.db.get_outbound_message_by_idempotency_key(
            f"order:{order['id']}:wallet-confirmed"
        )
        self.assertEqual(success_notice["status"], "sent")

        original_grant = self.db.grant_purchase_rewards

        def crash_after_product_reward(order_id: int) -> list[dict[str, Any]]:
            current = self.db.get_order(order_id)
            assert current is not None
            self.db.grant_referral_reward(
                current["user_id"],
                "product_purchase",
                f"order:{order_id}:product-purchase",
                product_id=current["product_id"],
                source_order_id=order_id,
            )
            raise RuntimeError("simulated crash between purchase reward event types")

        self.db.grant_purchase_rewards = crash_after_product_reward  # type: ignore[method-assign]
        try:
            # Reward processing fails, but fulfilment is deliberately allowed to
            # commit, moving the order out of the old `paid` reconciliation set.
            self.app._after_order_paid(order["id"])
        finally:
            self.db.grant_purchase_rewards = original_grant  # type: ignore[method-assign]

        completed = self.db.get_order(order["id"])
        self.assertEqual(completed["status"], "completed")
        self.assertIsNone(completed.get("reward_processed_at"))
        self.assertEqual(self.db.wallet_balance(inviter["id"]), 100)

        retry_calls = 0

        def counted_grant(order_id: int) -> list[dict[str, Any]]:
            nonlocal retry_calls
            retry_calls += 1
            return original_grant(order_id)

        self.db.grant_purchase_rewards = counted_grant  # type: ignore[method-assign]
        try:
            self.app._reconcile_paid_orders()
            self.app._reconcile_paid_orders()
        finally:
            self.db.grant_purchase_rewards = original_grant  # type: ignore[method-assign]

        self.assertEqual(retry_calls, 1, "the completion marker must prevent rescanning")
        self.assertIsNotNone(self.db.get_order(order["id"])["reward_processed_at"])
        self.assertEqual(self.db.wallet_balance(inviter["id"]), 300)
        self.assertEqual(self.db.referral_summary(inviter["id"])["reward_total"], 300)
        notices = [
            message
            for message in self.telegram.messages
            if message["chat_id"] == inviter["chat_id"]
            and "پاداش دعوت" in message["text"]
        ]
        self.assertEqual(
            len(notices),
            2,
            "each committed reward needs one durable notice, even after retry",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
