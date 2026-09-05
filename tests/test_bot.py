from __future__ import annotations

import copy
import sqlite3
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from typing import Any, Iterable
from unittest.mock import Mock, patch

from app.bot import BotApplication
from app.config import Settings
from app.db import Database, DatabaseError, ValidationError
from app.keyboards import (
    ACCOUNT,
    CHANNEL,
    MAIN_MENU_ROWS,
    REFERRAL,
    SHOP,
    SUPPORT,
    WALLET,
    contains_emoji,
)
from app.payment_server import ConfirmationOutcome
from app.plisio import PlisioError, PlisioInvoice
from app.telegram import (
    TelegramAPIError,
    TelegramClient,
    TelegramError,
    TelegramRequestCancelled,
)
from app.utils import parse_iso, utc_now


class FakeTelegram:
    """Small stateful Telegram API double used by BotApplication end-to-end tests."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.messages: list[dict[str, Any]] = []
        self.edits: list[dict[str, Any]] = []
        self.documents: list[dict[str, Any]] = []
        self.photos: list[dict[str, Any]] = []
        self.copies: list[dict[str, Any]] = []
        self.callback_answers: list[dict[str, Any]] = []
        self.closed = False
        self.stop_event: Any | None = None
        self._next_message_id = 1

    def set_stop_event(self, stop_event: Any) -> None:
        self.stop_event = stop_event

    def call(self, method: str, params: dict[str, Any] | None = None) -> Any:
        self.calls.append({"method": method, "params": copy.deepcopy(params)})
        if method == "getMe":
            return {
                "id": 8255000000,
                "is_bot": True,
                "first_name": "Alone test bot",
                "username": "alone_account_test_bot",
            }
        return True

    def send_message(self, chat_id: int, text: str, **kwargs: Any) -> dict[str, Any]:
        item = {
            "message_id": self._allocate_message_id(),
            "chat_id": int(chat_id),
            "text": text,
            **copy.deepcopy(kwargs),
        }
        self.messages.append(item)
        return copy.deepcopy(item)

    def edit_message_text(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        item = {
            "chat_id": int(chat_id),
            "message_id": int(message_id),
            "text": text,
            **copy.deepcopy(kwargs),
        }
        self.edits.append(item)
        return copy.deepcopy(item)

    def send_document(self, chat_id: int, document: Any, **kwargs: Any) -> dict[str, Any]:
        item = {
            "message_id": self._allocate_message_id(),
            "chat_id": int(chat_id),
            "document": document,
            **copy.deepcopy(kwargs),
        }
        self.documents.append(item)
        return copy.deepcopy(item)

    def edit_message_reply_markup(
        self, chat_id: int, message_id: int, reply_markup: dict[str, Any] | None = None, **kwargs: Any
    ) -> dict[str, Any] | bool:
        self.calls.append({
            "method": "editMessageReplyMarkup",
            "params": {"chat_id": chat_id, "message_id": message_id, "reply_markup": copy.deepcopy(reply_markup), **kwargs},
        })
        for message in self.messages:
            if message["chat_id"] == chat_id and message["message_id"] == message_id:
                message["reply_markup"] = copy.deepcopy(reply_markup)
                return copy.deepcopy(message)
        return True

    def send_photo(self, chat_id: int, photo: str, **kwargs: Any) -> dict[str, Any]:
        item = {
            "message_id": self._allocate_message_id(),
            "chat_id": int(chat_id),
            "photo": photo,
            **copy.deepcopy(kwargs),
        }
        self.photos.append(item)
        return copy.deepcopy(item)

    def copy_message(
        self,
        chat_id: int,
        from_chat_id: int,
        message_id: int,
        **kwargs: Any,
    ) -> dict[str, Any]:
        item = {
            "message_id": self._allocate_message_id(),
            "chat_id": int(chat_id),
            "from_chat_id": int(from_chat_id),
            "source_message_id": int(message_id),
            **copy.deepcopy(kwargs),
        }
        self.copies.append(item)
        return copy.deepcopy(item)

    def answer_callback_query(
        self,
        callback_query_id: str,
        text: str | None = None,
        **kwargs: Any,
    ) -> bool:
        self.callback_answers.append(
            {
                "callback_query_id": callback_query_id,
                "text": text,
                **copy.deepcopy(kwargs),
            }
        )
        return True

    def is_chat_member(self, _chat_id: str | int, _user_id: int) -> bool:
        return True

    def close(self) -> None:
        self.closed = True

    def _allocate_message_id(self) -> int:
        message_id = self._next_message_id
        self._next_message_id += 1
        return message_id

    def emitted(self) -> Iterable[dict[str, Any]]:
        yield from self.messages
        yield from self.edits
        yield from self.documents
        yield from self.photos
        yield from self.copies


class BotApplicationIntegrationTests(unittest.TestCase):
    CUSTOMER = {
        "id": 1001,
        "username": "buyer_one",
        "first_name": "خریدار",
    }
    OWNER = {
        "id": 9001,
        "username": "mohammadrezakheiry",
        "first_name": "مدیر",
    }

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.settings = Settings(
            bot_token="test-token-is-never-sent",
            database_path=self.root / "bot.sqlite3",
            data_dir=self.root / "data",
            bootstrap_admin_username=self.OWNER["username"],
            bootstrap_admin_chat_id=self.OWNER["id"],
            receipt_delay_seconds=0,
            job_interval_seconds=3600,
        )
        self.db = Database(self.settings.database_path)
        self.telegram = FakeTelegram()
        self.app = BotApplication(self.settings, self.db, self.telegram)  # type: ignore[arg-type]
        self.app.initialize()
        self.db.set_setting("completion_notice_pending", False)
        self.db.set_setting("card_number", "6037-9975-1234-5678")
        self.db.set_setting("card_owner", "فروشگاه الون اکانت")

        # Emoji are deliberately stored in dynamic catalog fields. They may be
        # shown in message bodies, but BotApplication must strip them from every
        # Telegram button label.
        self.category = self.db.create_category("🎁 اشتراک‌ها")
        self.product = self.db.create_product(
            self.category["id"],
            "🔐 اکانت آماده تست",
            product_type="ready",
            price_amount=100_000,
            icon="🔐",
            short_description="تحویل خودکار از موجودی",
            duration_days=30,
            duration_label="۳۰ روز",
            delivery_instructions="پس از ورود، گذرواژه را تغییر بده.",
        )
        self.inventory_payload = "login@example.test\npassword: secret-test-value"
        self.inventory = self.db.add_inventory_item(
            self.product["id"], self.inventory_payload
        )
        self._next_update_id = 1
        self._next_callback_id = 1

    def tearDown(self) -> None:
        self.temp.cleanup()

    # -- Update construction helpers ----------------------------------

    def message(
        self,
        actor: dict[str, Any],
        *,
        text: str | None = None,
        contact: dict[str, Any] | None = None,
        photo: list[dict[str, Any]] | None = None,
        document: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        update_id = self._take_update_id()
        payload: dict[str, Any] = {
            "message_id": update_id,
            "date": 1_700_000_000 + update_id,
            "chat": {
                "id": int(actor["id"]),
                "type": "private",
                "username": actor.get("username"),
                "first_name": actor.get("first_name"),
            },
            "from": copy.deepcopy(actor),
        }
        if text is not None:
            payload["text"] = text
        if contact is not None:
            payload["contact"] = copy.deepcopy(contact)
        if photo is not None:
            payload["photo"] = copy.deepcopy(photo)
        if document is not None:
            payload["document"] = copy.deepcopy(document)
        return {"update_id": update_id, "message": payload}

    def callback(self, actor: dict[str, Any], data: str) -> dict[str, Any]:
        update_id = self._take_update_id()
        callback_id = f"callback-{self._next_callback_id}"
        self._next_callback_id += 1
        return {
            "update_id": update_id,
            "callback_query": {
                "id": callback_id,
                "from": copy.deepcopy(actor),
                "chat_instance": "private-test-chat",
                "message": {
                    "message_id": max(1, update_id - 1),
                    "date": 1_700_000_000 + update_id,
                    "chat": {
                        "id": int(actor["id"]),
                        "type": "private",
                        "username": actor.get("username"),
                        "first_name": actor.get("first_name"),
                    },
                },
                "data": data,
            },
        }

    def send_message(self, actor: dict[str, Any], **kwargs: Any) -> None:
        self.app.process_update(self.message(actor, **kwargs))

    def send_callback(self, actor: dict[str, Any], data: str) -> None:
        self.app.process_update(self.callback(actor, data))

    def _take_update_id(self) -> int:
        update_id = self._next_update_id
        self._next_update_id += 1
        return update_id

    # -- Flow helpers --------------------------------------------------

    def test_pending_admin_grant_activates_only_for_the_exact_telegram_identity(
        self,
    ) -> None:
        unknown_chat = 4_003
        self.send_message(
            self.OWNER,
            text=f"/admin_add @future_admin {unknown_chat} admin",
        )
        pending = next(
            item
            for item in self.db.list_admins(active_only=False)
            if int(item.get("chat_id") or 0) == unknown_chat
        )
        self.assertIsNone(pending["identity_verified_at"])
        self.assertFalse(
            any(int(item.get("chat_id") or 0) == unknown_chat for item in self.db.list_admins(active_only=True))
        )

        impostor = {"id": unknown_chat, "username": "wrong_admin", "first_name": "Wrong"}
        before = len(self.telegram.messages)
        self.send_message(impostor, text="/admins")
        self.assertFalse(
            any("<b>مدیران</b>" in item["text"] for item in self.telegram.messages[before:])
        )
        self.assertFalse(
            any(int(item.get("chat_id") or 0) == unknown_chat for item in self.db.list_admins(active_only=True))
        )

        exact = {"id": unknown_chat, "username": "future_admin", "first_name": "Exact"}
        before = len(self.telegram.messages)
        self.send_message(exact, text="/admins")
        self.assertTrue(
            any("<b>مدیران</b>" in item["text"] for item in self.telegram.messages[before:])
        )
        verified = next(
            item
            for item in self.db.list_admins(active_only=True)
            if int(item.get("chat_id") or 0) == unknown_chat
        )
        self.assertIsNotNone(verified["identity_verified_at"])

    def test_verified_owner_username_rename_keeps_stable_chat_access(self) -> None:
        self.send_message(self.OWNER, text="/admins")
        root = next(
            item for item in self.db.list_admins(active_only=True)
            if int(item["chat_id"]) == int(self.OWNER["id"])
        )
        pending = self.db.add_admin(
            "renamed_owner",
            9_098,
            created_by_admin_id=int(root["id"]),
        )
        self.assertIsNone(pending["identity_verified_at"])

        # The new username is already reserved by a stale/pending grant, so
        # metadata refresh cannot succeed.  A previously verified private
        # Telegram chat remains the authentication anchor nonetheless.
        renamed = {**self.OWNER, "username": "renamed_owner"}
        before = len(self.telegram.messages)
        self.send_message(renamed, text="/admins")
        self.assertTrue(
            any("<b>مدیران</b>" in item["text"] for item in self.telegram.messages[before:])
        )
        owner = next(
            item for item in self.db.list_admins(active_only=True)
            if int(item["chat_id"]) == int(self.OWNER["id"])
        )
        self.assertEqual(owner["username"], self.OWNER["username"])
        still_pending = next(
            item
            for item in self.db.list_admins(active_only=False)
            if int(item["id"]) == int(pending["id"])
        )
        self.assertIsNone(still_pending["identity_verified_at"])

        impostor = {"id": 9_099, "username": "renamed_owner", "first_name": "Wrong"}
        before = len(self.telegram.messages)
        self.send_message(impostor, text="/admins")
        self.assertFalse(
            any("<b>مدیران</b>" in item["text"] for item in self.telegram.messages[before:])
        )

        # Telegram usernames are optional.  Once an administrator identity has
        # been proven, the stable private chat/user id remains sufficient.
        without_username = {key: value for key, value in self.OWNER.items() if key != "username"}
        before = len(self.telegram.messages)
        self.send_message(without_username, text="/admins")
        self.assertTrue(
            any("<b>مدیران</b>" in item["text"] for item in self.telegram.messages[before:])
        )

    def start_customer_and_open_product(self) -> dict[str, Any]:
        self.send_message(self.CUSTOMER, text="/start")
        customer = self.db.get_user_by_chat_id(self.CUSTOMER["id"])
        self.assertIsNotNone(customer)

        menu = self.telegram.messages[-1]["reply_markup"]["inline_keyboard"]
        self.assertEqual(
            [[button["text"] for button in row] for row in menu],
            [list(row) for row in MAIN_MENU_ROWS],
        )
        self.assertEqual(menu[0][0].get("style"), "success")
        self.assertEqual(menu[1][0].get("style"), "success")
        self.assertEqual(menu[1][1].get("style"), "primary")
        self.assertNotIn("style", menu[2][0])
        self.assertEqual(menu[3][0].get("style"), "primary")
        self.assertNotIn("style", menu[4][0])

        self.send_message(self.CUSTOMER, text=SHOP)
        self.send_callback(self.CUSTOMER, f"cat:{self.category['id']}")
        self.send_callback(self.CUSTOMER, f"prod:{self.product['id']}")
        return customer  # type: ignore[return-value]

    def create_order_through_verified_contact(self) -> tuple[dict[str, Any], dict[str, Any]]:
        customer = self.start_customer_and_open_product()
        self.send_callback(self.CUSTOMER, f"buy:{self.product['id']}")
        self.assertEqual(self.db.get_user_state(customer["id"])["state"], "purchase_name")

        self.send_message(self.CUSTOMER, text="محمد رضایی")
        state = self.db.get_user_state(customer["id"])
        self.assertEqual(state["state"], "purchase_phone")
        contact_markup = self.telegram.messages[-1]["reply_markup"]
        self.assertTrue(contact_markup["keyboard"][0][0]["request_contact"])

        self.send_message(
            self.CUSTOMER,
            contact={"user_id": 7777, "phone_number": "+989121111111"},
        )
        self.assertEqual(
            self.db.get_user_state(customer["id"])["state"], "purchase_phone"
        )
        self.assertEqual(self.db.list_orders(user_id=customer["id"]), [])

        self.send_message(
            self.CUSTOMER,
            contact={
                "user_id": self.CUSTOMER["id"],
                "phone_number": "+989121234567",
                "first_name": "خریدار",
            },
        )
        customer = self.db.get_user(customer["id"])
        self.assertIsNotNone(customer)
        self.assertEqual(customer["phone"], "+989121234567")
        self.assertIsNone(self.db.get_user_state(customer["id"]))
        orders = self.db.list_orders(user_id=customer["id"])
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0]["status"], "pending_payment")
        return customer, orders[0]  # type: ignore[return-value]

    def submit_card_receipt_after_partial_wallet(
        self,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        customer = self.start_customer_and_open_product()
        self.db.credit_wallet(
            customer["id"],
            40_000,
            reason="integration test seed",
            idempotency_key="test:wallet-seed",
        )

        self.send_callback(self.CUSTOMER, f"buy:{self.product['id']}")
        self.send_message(self.CUSTOMER, text="محمد رضایی")
        self.send_message(
            self.CUSTOMER,
            contact={
                "user_id": self.CUSTOMER["id"],
                "phone_number": "+989121234567",
            },
        )
        order = self.db.list_orders(user_id=customer["id"])[0]

        self.send_callback(self.CUSTOMER, f"checkout:{order['id']}")
        self.send_callback(self.CUSTOMER, f"paywallet:{order['id']}")
        order = self.db.get_order(order["id"])
        self.assertIsNotNone(order)
        self.assertEqual(order["wallet_held_amount"], 40_000)
        self.assertEqual(order["payable_amount"], 60_000)
        self.assertEqual(self.db.wallet_balance(customer["id"]), 0)

        self.send_callback(self.CUSTOMER, f"paycard:{order['id']}")
        payment = self.db.latest_order_payment(order["id"])
        self.assertIsNotNone(payment)
        self.assertEqual(payment["method"], "card")
        self.assertEqual(payment["purpose"], "order")
        self.assertEqual(payment["base_amount"], 60_000)
        self.assertGreaterEqual(payment["payable_amount"], 60_000)

        self.send_callback(self.CUSTOMER, f"receipt:{payment['id']}")
        self.assertEqual(
            self.db.get_user_state(customer["id"])["state"], "payment_receipt"
        )
        self.send_message(
            self.CUSTOMER,
            photo=[
                {"file_id": "receipt-small", "file_unique_id": "small"},
                {"file_id": "receipt-large", "file_unique_id": "large"},
            ],
        )
        payment = self.db.get_payment(payment["id"])
        self.assertEqual(payment["receipt_file_id"], "receipt-large")
        self.assertIsNone(self.db.get_user_state(customer["id"]))
        self.assertTrue(
            any(
                button.get("callback_data") == f"adm:payok:{payment['id']}"
                for button in self.all_buttons()
            ),
            "receipt notification must expose an admin approval callback",
        )
        return customer, order, payment  # type: ignore[return-value]

    def create_pending_card_payment_without_wallet(
        self,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        self.send_message(self.CUSTOMER, text="/start")
        customer = self.db.get_user_by_chat_id(self.CUSTOMER["id"])
        self.assertIsNotNone(customer)
        customer = self.db.update_user_profile(
            customer["id"],
            customer_name="محمد رضایی",
            phone="+989121234567",
        )
        self.send_callback(self.CUSTOMER, f"buy:{self.product['id']}")
        order = self.db.list_orders(user_id=customer["id"])[0]
        self.send_callback(self.CUSTOMER, f"checkout:{order['id']}")
        self.send_callback(self.CUSTOMER, f"paycard:{order['id']}")
        payment = self.db.latest_order_payment(order["id"])
        self.assertIsNotNone(payment)
        self.assertEqual(payment["status"], "pending")
        return customer, order, payment  # type: ignore[return-value]

    def all_buttons(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for emitted in self.telegram.emitted():
            markup = emitted.get("reply_markup") or {}
            for key in ("keyboard", "inline_keyboard"):
                for row in markup.get(key, []):
                    result.extend(row)
        return result

    # -- Integration assertions --------------------------------------

    def test_admin_update_journal_replays_before_effect_and_same_object_safely(
        self,
    ) -> None:
        update = self.message(self.OWNER, text="/category_add Journal category")
        update_id = int(update["update_id"])
        with patch.object(
            self.app.admin_controller,
            "handle",
            side_effect=KeyboardInterrupt("hard stop before effect"),
        ):
            with self.assertRaises(KeyboardInterrupt):
                self.app.process_update(update)
        self.assertEqual(self.db.get_admin_update(update_id)["status"], "started")
        self.assertFalse(
            any(
                item["name"] == "Journal category"
                for item in self.db.list_categories(
                    parent_id=None, active_only=False
                )
            )
        )

        # Reusing the exact same Python update object must not contaminate the
        # wire fingerprint with internal journal annotations.
        self.app.process_update(update)
        created = [
            item
            for item in self.db.list_categories(parent_id=None, active_only=False)
            if item["name"] == "Journal category"
        ]
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0]["source_admin_update_id"], update_id)
        self.assertEqual(self.db.get_admin_update(update_id)["status"], "completed")
        self.app.process_update(update)
        self.assertEqual(
            len(
                [
                    item
                    for item in self.db.list_categories(
                        parent_id=None, active_only=False
                    )
                    if item["name"] == "Journal category"
                ]
            ),
            1,
        )

    def test_admin_update_journal_freezes_toggle_and_idempotent_create(self) -> None:
        toggle_update = self.message(
            self.OWNER, text=f"/category_toggle {self.category['id']}"
        )
        with patch.object(
            self.db,
            "complete_admin_update",
            side_effect=KeyboardInterrupt("hard stop after toggle"),
        ):
            with self.assertRaises(KeyboardInterrupt):
                self.app.process_update(toggle_update)
        self.assertFalse(self.db.get_category(self.category["id"])["is_active"])
        self.assertEqual(
            self.db.get_admin_update(toggle_update["update_id"])["status"],
            "started",
        )
        self.app.process_update(toggle_update)
        self.assertFalse(self.db.get_category(self.category["id"])["is_active"])
        self.assertEqual(
            self.db.get_admin_update(toggle_update["update_id"])["status"],
            "completed",
        )
        self.db.set_category_active(self.category["id"], True)

        product_update = self.message(
            self.OWNER,
            text=(
                f"/product_add {self.category['id']} | Journal product | "
                "125000 | 30 روز | ready"
            ),
        )
        with patch.object(
            self.db,
            "complete_admin_update",
            side_effect=KeyboardInterrupt("hard stop after create"),
        ):
            with self.assertRaises(KeyboardInterrupt):
                self.app.process_update(product_update)
        self.app.process_update(product_update)
        products = [
            item
            for item in self.db.list_products(visible_only=False)
            if item["name"] == "Journal product"
        ]
        self.assertEqual(len(products), 1)
        self.assertEqual(
            products[0]["idempotency_key"],
            f"admin-update:{product_update['update_id']}:product-create",
        )

    def test_admin_update_journal_queues_direct_message_and_replays_delete(self) -> None:
        target = self.db.upsert_user(4_801, 4_801, username="journal_target")
        direct = self.message(self.OWNER, text="/message 4801 | durable hello")
        with patch.object(
            self.db,
            "complete_admin_update",
            side_effect=KeyboardInterrupt("hard stop after outbox commit"),
        ):
            with self.assertRaises(KeyboardInterrupt):
                self.app.process_update(direct)
        self.assertFalse(
            any(
                item["chat_id"] == target["chat_id"]
                and item["text"] == "durable hello"
                for item in self.telegram.messages
            )
        )
        self.app.process_update(direct)
        delivered = [
            item
            for item in self.telegram.messages
            if item["chat_id"] == target["chat_id"]
            and item["text"] == "durable hello"
        ]
        self.assertEqual(len(delivered), 1)

        disposable = self.db.create_category("Journal delete")
        deletion = self.message(
            self.OWNER, text=f"/category_delete {disposable['id']}"
        )
        with patch.object(
            self.db,
            "complete_admin_update",
            side_effect=KeyboardInterrupt("hard stop after delete"),
        ):
            with self.assertRaises(KeyboardInterrupt):
                self.app.process_update(deletion)
        self.assertIsNone(self.db.get_category(disposable["id"]))
        self.app.process_update(deletion)
        self.assertEqual(
            self.db.get_admin_update(deletion["update_id"])["status"],
            "completed",
        )

    def test_admin_inventory_state_replay_is_idempotent_and_clears_state(self) -> None:
        self.send_message(self.OWNER, text=f"/inventory_add {self.product['id']}")
        owner_user = self.db.get_user_by_chat_id(self.OWNER["id"])
        self.assertEqual(
            self.db.get_user_state(owner_user["id"])["state"],
            "admin:inventory",
        )
        payload = "journal-state@example.test\npassword: one-time"
        update = self.message(self.OWNER, text=payload)
        with patch.object(
            self.db,
            "complete_admin_update",
            side_effect=KeyboardInterrupt("hard stop after inventory insert"),
        ):
            with self.assertRaises(KeyboardInterrupt):
                self.app.process_update(update)
        self.assertIsNotNone(self.db.get_user_state(owner_user["id"]))
        self.assertEqual(
            len(self.db.list_inventory_items(self.product["id"])),
            2,
        )

        self.app.process_update(update)
        self.assertIsNone(self.db.get_user_state(owner_user["id"]))
        self.assertEqual(
            len(self.db.list_inventory_items(self.product["id"])),
            2,
        )

    def test_admin_inventory_state_transient_failure_remains_replayable(self) -> None:
        self.send_message(self.OWNER, text=f"/inventory_add {self.product['id']}")
        owner_user = self.db.get_user_by_chat_id(self.OWNER["id"])
        payload = "transient-state@example.test\npassword: retry-once"
        update = self.message(self.OWNER, text=payload)
        baseline = len(self.db.list_inventory_items(self.product["id"]))

        with patch.object(
            self.db,
            "add_inventory_item",
            side_effect=DatabaseError("transient inventory write failure"),
        ):
            self.assertIs(self.app.process_update(update), False)

        self.assertEqual(
            self.db.get_admin_update(update["update_id"])["status"],
            "started",
        )
        self.assertEqual(
            self.db.get_user_state(owner_user["id"])["state"],
            "admin:inventory",
        )
        self.assertEqual(
            len(self.db.list_inventory_items(self.product["id"])), baseline
        )

        self.assertIsNone(self.app.process_update(update))
        self.assertEqual(
            self.db.get_admin_update(update["update_id"])["status"],
            "completed",
        )
        self.assertIsNone(self.db.get_user_state(owner_user["id"]))
        items = self.db.list_inventory_items(self.product["id"])
        self.assertEqual(len(items), baseline + 1)
        connection = sqlite3.connect(self.db.path)
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM inventory_items "
                    "WHERE product_id = ? AND payload = ?",
                    (int(self.product["id"]), payload),
                ).fetchone()[0],
                1,
            )
        finally:
            connection.close()

    def test_admin_journal_begin_and_complete_database_failures_are_nacked(
        self,
    ) -> None:
        begin_update = self.message(self.OWNER, text="/bot_off")
        with patch.object(
            self.db,
            "begin_admin_update",
            side_effect=DatabaseError("transient journal begin failure"),
        ):
            self.assertIs(self.app.process_update_safe(begin_update), False)
        self.assertIsNone(self.db.get_admin_update(begin_update["update_id"]))
        self.assertTrue(self.db.get_setting("bot_enabled", True))

        self.assertIsNone(self.app.process_update_safe(begin_update))
        self.assertEqual(
            self.db.get_admin_update(begin_update["update_id"])["status"],
            "completed",
        )
        self.assertFalse(self.db.get_setting("bot_enabled", True))

        complete_update = self.message(self.OWNER, text="/bot_on")
        with patch.object(
            self.db,
            "complete_admin_update",
            side_effect=DatabaseError("transient journal completion failure"),
        ):
            self.assertIs(self.app.process_update_safe(complete_update), False)
        self.assertEqual(
            self.db.get_admin_update(complete_update["update_id"])["status"],
            "started",
        )
        self.assertTrue(self.db.get_setting("bot_enabled", False))

        self.assertIsNone(self.app.process_update_safe(complete_update))
        self.assertEqual(
            self.db.get_admin_update(complete_update["update_id"])["status"],
            "completed",
        )
        self.assertTrue(self.db.get_setting("bot_enabled", False))

    def test_admin_transient_nack_survives_failed_diagnostic_notification(
        self,
    ) -> None:
        self.send_message(self.OWNER, text=f"/inventory_add {self.product['id']}")
        payload = "diagnostic-failure@example.test\npassword: retry-once"
        update = self.message(self.OWNER, text=payload)
        telegram_failure = TelegramAPIError(
            "sendMessage", "Bot API request failed", error_code=503
        )

        with (
            patch.object(
                self.db,
                "add_inventory_item",
                side_effect=DatabaseError("transient inventory write failure"),
            ),
            patch.object(
                self.telegram, "send_message", side_effect=telegram_failure
            ),
        ):
            self.assertIs(self.app.process_update_safe(update), False)

        self.assertEqual(
            self.db.get_admin_update(update["update_id"])["status"], "started"
        )
        self.assertIsNone(self.app.process_update_safe(update))
        self.assertEqual(
            self.db.get_admin_update(update["update_id"])["status"], "completed"
        )
        connection = sqlite3.connect(self.db.path)
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM inventory_items "
                    "WHERE product_id = ? AND payload = ?",
                    (int(self.product["id"]), payload),
                ).fetchone()[0],
                1,
            )
        finally:
            connection.close()

    def test_permanent_admin_reply_failure_is_acknowledged(self) -> None:
        update = self.message(self.OWNER, text="/admin_help")
        telegram_failure = TelegramAPIError(
            "sendMessage", "Bot API request failed", error_code=403
        )

        with patch.object(
            self.telegram, "send_message", side_effect=telegram_failure
        ):
            self.assertIsNone(self.app.process_update_safe(update))

        self.assertEqual(
            self.db.get_admin_update(update["update_id"])["status"], "completed"
        )

    def test_polling_retries_transient_admin_update_before_later_batch_items(
        self,
    ) -> None:
        class RecordingStop:
            def __init__(self) -> None:
                self.stopped = False
                self.waits: list[float] = []

            def is_set(self) -> bool:
                return self.stopped

            def set(self) -> None:
                self.stopped = True

            def wait(self, delay: float) -> bool:
                self.waits.append(delay)
                return self.stopped

        self.send_message(self.OWNER, text=f"/inventory_add {self.product['id']}")
        payload = "polling-retry@example.test\npassword: exactly-once"
        retry_update = self.message(self.OWNER, text=payload)
        later_update = self.message(self.OWNER, text="/admin_help")
        stop_event = RecordingStop()
        polling = TelegramClient(
            "123456:test-token", retry_backoff=0.01, max_retry_delay=0.1
        )
        self.addCleanup(polling.close)
        requested_offsets: list[int | None] = []
        saved_offsets: list[int] = []
        dispatch_order: list[int] = []

        def get_updates(**kwargs: Any) -> list[dict[str, Any]]:
            requested_offsets.append(kwargs.get("offset"))
            return [retry_update, later_update]

        polling.get_updates = get_updates  # type: ignore[method-assign]
        original_add = self.db.add_inventory_item
        add_attempts = 0

        def flaky_add(*args: Any, **kwargs: Any) -> dict[str, Any]:
            nonlocal add_attempts
            add_attempts += 1
            if add_attempts == 1:
                self.assertEqual(polling.last_update_offset, retry_update["update_id"])
                self.assertEqual(saved_offsets, [])
                raise DatabaseError("transient inventory write failure")
            return original_add(*args, **kwargs)

        def handler(item: dict[str, Any]) -> bool | None:
            dispatch_order.append(int(item["update_id"]))
            result = self.app.process_update_safe(item)
            if item is later_update:
                stop_event.set()
            return result

        with patch.object(self.db, "add_inventory_item", side_effect=flaky_add):
            final_offset = polling.run_polling(
                handler,
                offset=int(retry_update["update_id"]),
                timeout=0,
                stop_event=stop_event,
                save_offset=saved_offsets.append,
            )

        self.assertEqual(
            dispatch_order,
            [
                int(retry_update["update_id"]),
                int(retry_update["update_id"]),
                int(later_update["update_id"]),
            ],
        )
        self.assertEqual(
            requested_offsets,
            [int(retry_update["update_id"]), int(retry_update["update_id"])],
        )
        self.assertEqual(
            saved_offsets,
            [int(retry_update["update_id"]) + 1, int(later_update["update_id"]) + 1],
        )
        self.assertEqual(final_offset, int(later_update["update_id"]) + 1)
        self.assertEqual(stop_event.waits, [0.01])
        self.assertEqual(add_attempts, 2)
        connection = sqlite3.connect(self.db.path)
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM inventory_items "
                    "WHERE product_id = ? AND payload = ?",
                    (int(self.product["id"]), payload),
                ).fetchone()[0],
                1,
            )
        finally:
            connection.close()

    def test_generic_admin_start_is_not_claimed_by_the_admin_journal(self) -> None:
        update = self.message(self.OWNER, text="/start")
        self.app.process_update(update)
        self.app.process_update(update)
        self.assertIsNone(self.db.get_admin_update(update["update_id"]))

    def test_start_menu_purchase_requires_own_contact_and_creates_order(self) -> None:
        customer, order = self.create_order_through_verified_contact()

        self.assertEqual(customer["customer_name"], "محمد رضایی")
        self.assertEqual(order["product_id"], self.product["id"])
        self.assertEqual(order["subtotal_amount"], 100_000)
        self.assertTrue(
            any(
                button.get("callback_data") == f"checkout:{order['id']}"
                for button in self.all_buttons()
            )
        )

        # /menu returns to the exact main inline keyboard with a direct channel link.
        self.send_message(self.CUSTOMER, text="/menu")
        self.assertEqual(
            [
                [button["text"] for button in row]
                for row in self.telegram.messages[-1]["reply_markup"]["inline_keyboard"]
            ],
            [list(row) for row in MAIN_MENU_ROWS],
        )

    def test_created_order_summary_survives_send_failure_and_update_replay(self) -> None:
        customer = self.db.upsert_user(
            4_901,
            4_901,
            username="created_summary_recovery",
            first_name="Recovery",
        )
        customer = self.db.update_user_profile(
            customer["id"], customer_name="Recovery Buyer", phone="+989121234567"
        )
        with patch.object(
            self.telegram,
            "send_message",
            side_effect=TelegramError("summary transport outage"),
        ):
            order = self.app._create_order_and_confirm(
                customer,
                int(self.product["id"]),
                update_id=49_001,
            )

        self.assertEqual(
            len(self.db.list_orders(user_id=int(customer["id"]))),
            1,
        )

        notice_key = f"order:{int(order['id'])}:created-summary"
        notice = self.db.get_outbound_message_by_idempotency_key(notice_key)
        self.assertIsNotNone(notice)
        self.assertEqual(notice["status"], "queued")
        connection = sqlite3.connect(self.db.path)
        try:
            connection.execute(
                "UPDATE outbound_messages SET scheduled_at = ? WHERE id = ?",
                ("2000-01-01T00:00:00+00:00", int(notice["id"])),
            )
            connection.commit()
        finally:
            connection.close()

        self.app._deliver_outbound_messages()
        summaries = [
            item
            for item in self.telegram.messages
            if int(item["chat_id"]) == int(customer["chat_id"])
            and str(order["order_number"]) in item["text"]
            and any(
                button.get("callback_data") == f"checkout:{int(order['id'])}"
                for row in (item.get("reply_markup") or {}).get(
                    "inline_keyboard", []
                )
                for button in row
            )
        ]
        self.assertEqual(len(summaries), 1)

        replay = self.app._create_order_and_confirm(
            customer,
            int(self.product["id"]),
            update_id=49_001,
        )
        self.assertEqual(replay["id"], order["id"])
        self.assertEqual(
            len(self.db.list_orders(user_id=int(customer["id"]))),
            1,
        )
        self.assertEqual(
            len(
                [
                    item
                    for item in self.telegram.messages
                    if int(item["chat_id"]) == int(customer["chat_id"])
                    and str(order["order_number"]) in item["text"]
                ]
            ),
            1,
        )

    def test_phone_state_survives_failure_before_created_summary_commit(self) -> None:
        customer = self.start_customer_and_open_product()
        self.send_callback(self.CUSTOMER, f"buy:{self.product['id']}")
        self.send_message(self.CUSTOMER, text="Recovery Buyer")
        self.assertEqual(
            self.db.get_user_state(customer["id"])["state"], "purchase_phone"
        )
        contact = {
            "user_id": self.CUSTOMER["id"],
            "phone_number": "+989121234567",
        }
        with patch.object(
            self.app,
            "_create_order_and_confirm",
            side_effect=RuntimeError("crash before order transaction"),
        ):
            with self.assertRaisesRegex(RuntimeError, "crash before order transaction"):
                self.send_message(self.CUSTOMER, contact=contact)
        self.assertEqual(
            self.db.get_user_state(customer["id"])["state"], "purchase_phone"
        )
        self.assertEqual(self.db.list_orders(user_id=customer["id"]), [])

        self.send_message(self.CUSTOMER, contact=contact)
        self.assertIsNone(self.db.get_user_state(customer["id"]))
        orders = self.db.list_orders(user_id=customer["id"])
        self.assertEqual(len(orders), 1)
        self.assertIsNotNone(
            self.db.get_outbound_message_by_idempotency_key(
                f"order:{orders[0]['id']}:created-summary"
            )
        )

    def test_partial_wallet_card_receipt_admin_callback_confirms_and_delivers(self) -> None:
        customer, order, payment = self.submit_card_receipt_after_partial_wallet()

        before = len(self.telegram.messages)
        self.send_callback(self.OWNER, f"adm:payok:{payment['id']}")

        confirmed = self.db.get_payment(payment["id"])
        delivered = self.db.get_order(order["id"])
        inventory = self.db.list_inventory_items(self.product["id"])[0]
        self.assertEqual(confirmed["status"], "paid")
        self.assertEqual(delivered["status"], "completed")
        self.assertEqual(delivered["wallet_captured_amount"], 40_000)
        self.assertEqual(delivered["external_paid_amount"], 60_000)
        self.assertEqual(self.db.wallet_balance(customer["id"]), 0)
        self.assertEqual(inventory["status"], "assigned")
        self.assertEqual(inventory["assigned_order_id"], order["id"])
        self.assertTrue(
            any(
                item["chat_id"] == customer["chat_id"]
                and self.inventory_payload in item["text"]
                for item in self.telegram.messages
            ),
            "paid ready-stock order must deliver its inventory payload to the buyer",
        )
        self.assertNotEqual(
            self.telegram.callback_answers[-1].get("text"),
            "این گزینه دیگر معتبر نیست.",
        )
        buyer_messages = [
            item
            for item in self.telegram.messages[before:]
            if int(item["chat_id"]) == int(customer["chat_id"])
        ]
        success_index = next(
            index
            for index, item in enumerate(buyer_messages)
            if "پرداخت با موفقیت" in item["text"]
        )
        delivery_index = next(
            index
            for index, item in enumerate(buyer_messages)
            if self.inventory_payload in item["text"]
        )
        self.assertLess(success_index, delivery_index)
        self.assertEqual(
            sum("پرداخت با موفقیت" in item["text"] for item in buyer_messages),
            1,
        )
        self.assertFalse(
            any("پرداخت شما توسط مدیریت" in item["text"] for item in buyer_messages)
        )

    def test_financial_admin_callback_replay_does_not_duplicate_fulfillment(self) -> None:
        customer, order, payment = self.submit_card_receipt_after_partial_wallet()
        update = self.callback(self.OWNER, f"adm:payok:{payment['id']}")
        with patch.object(
            self.db,
            "complete_admin_update",
            side_effect=KeyboardInterrupt("hard stop after financial commit"),
        ):
            with self.assertRaises(KeyboardInterrupt):
                self.app.process_update(update)

        self.assertEqual(self.db.get_payment(payment["id"])["status"], "paid")
        self.assertEqual(self.db.get_order(order["id"])["status"], "completed")
        self.assertEqual(
            self.db.get_admin_update(update["update_id"])["status"], "started"
        )
        self.app.process_update(update)
        self.assertEqual(
            self.db.get_admin_update(update["update_id"])["status"], "completed"
        )
        deliveries = [
            item
            for item in self.telegram.messages
            if int(item["chat_id"]) == int(customer["chat_id"])
            and self.inventory_payload in item["text"]
        ]
        successes = [
            item
            for item in self.telegram.messages
            if int(item["chat_id"]) == int(customer["chat_id"])
            and "پرداخت با موفقیت" in item["text"]
        ]
        self.assertEqual(len(deliveries), 1)
        self.assertEqual(len(successes), 1)
        self.assertEqual(
            len(
                [
                    item
                    for item in self.db.list_wallet_entries(customer["id"], limit=20)
                    if item.get("order_id") == order["id"]
                ]
            ),
            1,
        )

    def test_old_cancel_button_cannot_cancel_a_submitted_card_receipt(self) -> None:
        customer, order, payment = self.submit_card_receipt_after_partial_wallet()

        self.send_callback(self.CUSTOMER, f"cancelpay:{payment['id']}")

        self.assertEqual(self.db.get_payment(payment["id"])["status"], "verifying")
        self.assertEqual(
            self.db.get_order(order["id"])["status"], "awaiting_confirmation"
        )
        self.assertTrue(self.telegram.callback_answers[-1].get("show_alert"))
        self.assertIn("قابل لغو نیست", self.telegram.callback_answers[-1]["text"])

        self.send_callback(self.OWNER, f"adm:payok:{payment['id']}")
        self.assertEqual(self.db.get_payment(payment["id"])["status"], "paid")
        self.assertEqual(self.db.get_order(order["id"])["status"], "completed")
        self.assertEqual(self.db.get_order(order["id"])["user_id"], customer["id"])

    def test_crypto_invoice_is_not_orphaned_or_user_cancelled(self) -> None:
        customer = self.db.upsert_user(
            self.CUSTOMER["id"],
            self.CUSTOMER["id"],
            username=self.CUSTOMER["username"],
        )
        order = self.db.create_order(
            customer["id"],
            self.product["id"],
            idempotency_key="crypto-orphan-guard-order",
        )
        card = self.db.create_order_payment(
            order["id"],
            "card",
            idempotency_key="crypto-orphan-guard-card",
        )
        self.db.set_setting("payment_crypto_enabled", True)
        provider = Mock()
        self.app._plisio = provider

        with self.assertRaises(ValidationError):
            self.app._begin_crypto_payment(customer, order["id"], query=None)
        provider.create_invoice.assert_not_called()

        self.db.set_payment_status(card["id"], "failed")
        invoice_url = "https://pay.example.test/invoice/tracked-until-expiry"
        crypto = self.db.create_order_payment(
            order["id"],
            "crypto",
            idempotency_key="crypto-orphan-guard-crypto",
            provider_invoice_id="crypto-orphan-provider-id",
            provider_invoice_url=invoice_url,
            unique_amount_window=0,
        )
        self.app._show_crypto_invoice(customer, crypto, invoice_url, query=None)
        buttons = self.telegram.messages[-1]["reply_markup"]["inline_keyboard"]
        self.assertFalse(
            any(
                str(button.get("callback_data") or "").startswith("cancelpay:")
                for row in buttons
                for button in row
            )
        )

        # A callback from a message created by an older release is also denied
        # at both the bot and repository boundaries.
        self.send_callback(self.CUSTOMER, f"cancelpay:{crypto['id']}")
        self.assertTrue(self.telegram.callback_answers[-1].get("show_alert"))
        self.assertIn("قابل لغو نیست", self.telegram.callback_answers[-1]["text"])
        self.assertEqual(self.db.get_payment(crypto["id"])["status"], "pending")
        self.assertEqual(
            self.db.get_order(order["id"])["status"], "awaiting_confirmation"
        )

    def test_order_detail_resumes_crypto_and_gates_receipts_by_method(self) -> None:
        customer = self.db.upsert_user(
            self.CUSTOMER["id"],
            self.CUSTOMER["id"],
            username=self.CUSTOMER["username"],
        )
        order = self.db.create_order(
            customer["id"],
            self.product["id"],
            idempotency_key="resume-crypto-order",
        )
        invoice_url = "https://pay.example.test/invoice/resume-order"
        crypto = self.db.create_order_payment(
            order["id"],
            "crypto",
            idempotency_key="resume-crypto-payment",
            provider_invoice_id="resume-order-provider-id",
            provider_invoice_url=invoice_url,
            unique_amount_window=0,
        )

        # The invoice's Back button enters the real callback route and must
        # preserve a safe way to resume the same non-cancellable intent.
        self.app._show_crypto_invoice(customer, crypto, invoice_url, query=None)
        invoice_buttons = self.telegram.messages[-1]["reply_markup"]["inline_keyboard"]
        back_data = invoice_buttons[-1][0]["callback_data"]
        self.assertEqual(back_data, f"order:{order['id']}")
        self.send_callback(self.CUSTOMER, back_data)
        detail = self.telegram.edits[-1]
        detail_buttons = [
            button
            for row in detail["reply_markup"]["inline_keyboard"]
            for button in row
        ]
        self.assertTrue(
            any(
                button.get("text") == "ادامه پرداخت ارزی"
                and button.get("url") == invoice_url
                for button in detail_buttons
            )
        )
        self.assertFalse(
            any(
                str(button.get("callback_data") or "").startswith("receipt:")
                for button in detail_buttons
            )
        )

        # Legacy rows may predate repository URL validation. They are never
        # rendered as clickable links or misrouted into the receipt workflow.
        for legacy_url in ("javascript:alert(1)", None):
            with self.subTest(legacy_url=legacy_url):
                connection = sqlite3.connect(self.db.path)
                try:
                    connection.execute(
                        "UPDATE payments SET provider_invoice_url = ? WHERE id = ?",
                        (legacy_url, crypto["id"]),
                    )
                    connection.commit()
                finally:
                    connection.close()
                self.send_callback(self.CUSTOMER, f"order:{order['id']}")
                unavailable = self.telegram.edits[-1]
                unavailable_buttons = [
                    button
                    for row in unavailable["reply_markup"]["inline_keyboard"]
                    for button in row
                ]
                self.assertIn("در دسترس نیست", unavailable["text"])
                self.assertFalse(any("url" in button for button in unavailable_buttons))
                self.assertFalse(
                    any(
                        str(button.get("callback_data") or "").startswith("receipt:")
                        for button in unavailable_buttons
                    )
                )
                self.assertTrue(
                    any(
                        button.get("callback_data") == "support"
                        for button in unavailable_buttons
                    )
                )

        card_order = self.db.create_order(
            customer["id"],
            self.product["id"],
            idempotency_key="resume-card-order",
        )
        card = self.db.create_order_payment(
            card_order["id"],
            "card",
            idempotency_key="resume-card-payment",
        )
        self.app.show_order(customer, card_order["id"])
        card_buttons = [
            button
            for row in self.telegram.messages[-1]["reply_markup"]["inline_keyboard"]
            for button in row
        ]
        self.assertTrue(
            any(
                button.get("callback_data") == f"receipt:{card['id']}"
                for button in card_buttons
            )
        )
        self.assertFalse(any("url" in button for button in card_buttons))

    def test_wallet_resumes_active_crypto_topup_and_card_receipt_separately(self) -> None:
        customer = self.db.upsert_user(
            self.CUSTOMER["id"],
            self.CUSTOMER["id"],
            username=self.CUSTOMER["username"],
        )
        amount = 250_000
        invoice_url = "https://pay.example.test/invoice/resume-topup"
        crypto = self.db.create_wallet_topup_payment(
            customer["id"],
            amount,
            "crypto",
            idempotency_key="resume-crypto-topup",
            provider_invoice_id="resume-topup-provider-id",
            provider_invoice_url=invoice_url,
            unique_amount_window=0,
        )
        self.app._show_crypto_invoice(customer, crypto, invoice_url, query=None)
        invoice_buttons = self.telegram.messages[-1]["reply_markup"]["inline_keyboard"]
        self.assertEqual(invoice_buttons[-1][0]["callback_data"], "wallet")

        self.send_callback(self.CUSTOMER, "wallet")
        wallet = self.telegram.edits[-1]
        wallet_buttons = [
            button
            for row in wallet["reply_markup"]["inline_keyboard"]
            for button in row
        ]
        self.assertIn("250,000", wallet["text"])
        self.assertTrue(
            any(
                button.get("text") == "ادامه پرداخت ارزی"
                and button.get("url") == invoice_url
                for button in wallet_buttons
            )
        )
        self.assertFalse(
            any(
                str(button.get("callback_data") or "").startswith("receipt:")
                for button in wallet_buttons
            )
        )

        provider = Mock()
        self.app._plisio = provider
        self.db.set_setting("payment_crypto_enabled", True)
        self.app._begin_crypto_topup(customer, amount, query=None)
        provider.create_invoice.assert_not_called()
        repeated_buttons = self.telegram.messages[-1]["reply_markup"]["inline_keyboard"]
        self.assertTrue(
            any(
                button.get("url") == invoice_url
                for row in repeated_buttons
                for button in row
            )
        )

        for legacy_url in ("https://localhost/private", None):
            with self.subTest(legacy_url=legacy_url):
                connection = sqlite3.connect(self.db.path)
                try:
                    connection.execute(
                        "UPDATE payments SET provider_invoice_url = ? WHERE id = ?",
                        (legacy_url, crypto["id"]),
                    )
                    connection.commit()
                finally:
                    connection.close()
                self.app.show_wallet(customer)
                unavailable = self.telegram.messages[-1]
                unavailable_buttons = [
                    button
                    for row in unavailable["reply_markup"]["inline_keyboard"]
                    for button in row
                ]
                self.assertIn("در دسترس نیست", unavailable["text"])
                self.assertFalse(any("url" in button for button in unavailable_buttons))
                self.assertFalse(
                    any(
                        str(button.get("callback_data") or "").startswith("receipt:")
                        for button in unavailable_buttons
                    )
                )

        card_user = self.db.upsert_user(1002, 1002, username="card_topup_resume")
        card = self.db.create_wallet_topup_payment(
            card_user["id"],
            300_000,
            "card",
            idempotency_key="resume-card-topup",
        )
        self.app.show_wallet(card_user)
        card_buttons = [
            button
            for row in self.telegram.messages[-1]["reply_markup"]["inline_keyboard"]
            for button in row
        ]
        self.assertTrue(
            any(
                button.get("callback_data") == f"receipt:{card['id']}"
                for button in card_buttons
            )
        )
        self.assertFalse(any("url" in button for button in card_buttons))

        # Stale cross-method callbacks cannot issue a second active top-up.
        with self.assertRaises(DatabaseError):
            self.app._begin_card_topup(customer, amount, query=None)
        self.assertEqual(
            len(self.db.list_active_wallet_topup_payments(customer["id"])), 1
        )

        provider.reset_mock()
        with self.assertRaises(ValidationError):
            self.app._begin_crypto_topup(card_user, 300_000, query=None)
        provider.create_invoice.assert_not_called()

        # A database upgraded from a per-method release may already contain
        # both methods. Neither real payable intent may be hidden from its
        # owner while the new repository prevents creating such a pair.
        self.db.set_payment_status(card["id"], "failed")
        legacy_crypto_url = "https://pay.example.test/invoice/legacy-dual-topup"
        legacy_crypto = self.db.create_wallet_topup_payment(
            card_user["id"],
            310_000,
            "crypto",
            idempotency_key="legacy-dual-crypto",
            provider_invoice_id="legacy-dual-provider",
            provider_invoice_url=legacy_crypto_url,
            unique_amount_window=0,
        )
        connection = sqlite3.connect(self.db.path)
        try:
            connection.execute(
                "UPDATE payments SET status = 'pending' WHERE id = ?", (card["id"],)
            )
            connection.commit()
        finally:
            connection.close()
        self.app.show_wallet(card_user)
        legacy_buttons = [
            button
            for row in self.telegram.messages[-1]["reply_markup"]["inline_keyboard"]
            for button in row
        ]
        self.assertEqual(
            {payment["id"] for payment in self.db.list_active_wallet_topup_payments(card_user["id"])},
            {card["id"], legacy_crypto["id"]},
        )
        self.assertTrue(
            any(
                button.get("callback_data") == f"receipt:{card['id']}"
                for button in legacy_buttons
            )
        )
        self.assertTrue(
            any(button.get("url") == legacy_crypto_url for button in legacy_buttons)
        )

    def test_active_crypto_topup_blocks_a_new_provider_invoice(self) -> None:
        customer = self.db.upsert_user(
            self.CUSTOMER["id"],
            self.CUSTOMER["id"],
            username=self.CUSTOMER["username"],
        )
        self.db.set_setting("payment_crypto_enabled", True)
        provider = Mock()
        self.app._plisio = provider
        active = self.db.create_wallet_topup_payment(
            customer["id"],
            100_000,
            "crypto",
            idempotency_key="active-crypto-topup-provider-guard",
            provider_invoice_id="active-crypto-topup-provider-id",
            provider_invoice_url="https://pay.example.test/invoice/active-topup",
            unique_amount_window=0,
        )

        with self.assertRaises(ValidationError):
            self.app._begin_crypto_topup(customer, 200_000, query=None)

        provider.create_invoice.assert_not_called()
        self.assertEqual(self.db.get_payment(active["id"])["status"], "pending")
        self.assertEqual(
            [payment["id"] for payment in self.db.list_pending_provider_payments()],
            [active["id"]],
        )

    def test_crypto_topup_provisional_intent_recovers_remote_create_ambiguity(self) -> None:
        customer = self.db.upsert_user(
            self.CUSTOMER["id"],
            self.CUSTOMER["id"],
            username=self.CUSTOMER["username"],
        )
        self.db.set_setting("payment_crypto_enabled", True)
        invoice = PlisioInvoice(
            "stable-topup-provider-id",
            "https://pay.example.test/invoice/stable-topup",
            "new",
        )
        provider = Mock()
        provider.create_invoice.side_effect = [
            PlisioError("ambiguous transport failure"),
            invoice,
            invoice,
        ]
        self.app._plisio = provider

        self.app._begin_crypto_topup(customer, 150_000, query=None)
        provisional = self.db.list_active_wallet_topup_payments(customer["id"])
        self.assertEqual(len(provisional), 1)
        self.assertIsNone(provisional[0]["provider_invoice_id"])
        self.assertIsNone(provisional[0]["provider_invoice_url"])
        self.assertEqual(self.db.list_pending_provider_payments(), [])
        self.app.show_wallet(customer)
        retry_buttons = [
            button
            for row in self.telegram.messages[-1]["reply_markup"]["inline_keyboard"]
            for button in row
        ]
        self.assertTrue(
            any(
                button.get("callback_data") == "topupcrypto:150000"
                for button in retry_buttons
            )
        )

        original_attach = self.db.attach_crypto_invoice
        with patch.object(
            self.db,
            "attach_crypto_invoice",
            side_effect=DatabaseError("simulated crash before attach commit"),
        ):
            self.app._begin_crypto_topup(customer, 150_000, query=None)
        still_provisional = self.db.list_active_wallet_topup_payments(customer["id"])
        self.assertEqual(
            [row["id"] for row in still_provisional], [provisional[0]["id"]]
        )
        self.assertIsNone(still_provisional[0]["provider_invoice_id"])
        self.assertFalse(
            any(
                invoice.invoice_url in str(item.get("text") or "")
                for item in self.telegram.messages
            )
        )

        self.app._begin_crypto_topup(customer, 150_000, query=None)
        attached = original_attach(
            provisional[0]["id"],
            customer["id"],
            invoice.transaction_id,
            invoice.invoice_url,
        )
        self.assertEqual(attached["provider_invoice_id"], invoice.transaction_id)
        self.assertEqual(
            len(self.db.list_active_wallet_topup_payments(customer["id"])), 1
        )
        merchant_numbers = [
            call.kwargs["order_number"] for call in provider.create_invoice.call_args_list
        ]
        self.assertEqual(
            merchant_numbers,
            [provisional[0]["payment_number"]] * 3,
        )
        self.assertEqual(
            [row["id"] for row in self.db.list_pending_provider_payments()],
            [provisional[0]["id"]],
        )

    def test_crypto_order_provisional_freezes_terms_before_remote_side_effect(self) -> None:
        customer = self.db.upsert_user(
            self.CUSTOMER["id"],
            self.CUSTOMER["id"],
            username=self.CUSTOMER["username"],
        )
        order = self.db.create_order(
            customer["id"],
            self.product["id"],
            idempotency_key="provisional-crypto-order",
        )
        self.db.set_setting("payment_crypto_enabled", True)
        invoice = PlisioInvoice(
            "stable-order-provider-id",
            "https://pay.example.test/invoice/stable-order",
            "new",
        )
        provider = Mock()
        provider.create_invoice.side_effect = [
            PlisioError("ambiguous order transport failure"),
            invoice,
        ]
        self.app._plisio = provider

        self.app._begin_crypto_payment(customer, order["id"], query=None)
        provisional = self.db.find_active_order_payment(order["id"])
        self.assertIsNotNone(provisional)
        self.assertEqual(provisional["method"], "crypto")
        self.assertEqual(provisional["base_amount"], 100_000)
        self.assertIsNone(provisional["provider_invoice_id"])
        self.assertEqual(
            self.db.get_order(order["id"])["status"], "awaiting_confirmation"
        )
        self.app.show_order(customer, order["id"])
        retry_buttons = [
            button
            for row in self.telegram.messages[-1]["reply_markup"]["inline_keyboard"]
            for button in row
        ]
        self.assertTrue(
            any(
                button.get("callback_data") == f"paycrypto:{order['id']}"
                for button in retry_buttons
            )
        )

        self.db.credit_wallet(
            customer["id"],
            40_000,
            reason="must not mutate provisional terms",
            idempotency_key="provisional-order-wallet-credit",
        )
        with self.assertRaises(ValidationError):
            self.db.hold_wallet_funds(
                order["id"], idempotency_key="provisional-order-late-hold"
            )
        discount = self.db.create_discount(
            "PROVISIONAL10", discount_type="percent", value=10
        )
        with self.assertRaises(ValidationError):
            self.db.apply_discount(order["id"], discount["code"])

        self.app._begin_crypto_payment(customer, order["id"], query=None)
        attached = self.db.find_active_order_payment(order["id"])
        self.assertEqual(attached["id"], provisional["id"])
        self.assertEqual(attached["base_amount"], 100_000)
        self.assertEqual(attached["provider_invoice_id"], invoice.transaction_id)
        self.assertEqual(
            [call.kwargs["order_number"] for call in provider.create_invoice.call_args_list],
            [provisional["payment_number"], provisional["payment_number"]],
        )
        self.assertEqual(
            [
                call.kwargs["amount_in_shop_currency"]
                for call in provider.create_invoice.call_args_list
            ],
            [100_000, 100_000],
        )

    def test_late_observed_crypto_payments_settle_once_after_local_deadline(self) -> None:
        customer = self.db.upsert_user(
            self.CUSTOMER["id"],
            self.CUSTOMER["id"],
            username=self.CUSTOMER["username"],
        )
        past = utc_now() - timedelta(hours=1)
        order = self.db.create_order(
            customer["id"],
            self.product["id"],
            idempotency_key="late-crypto-completed-order",
            now=past,
        )
        order_payment = self.db.create_order_payment(
            order["id"],
            "crypto",
            idempotency_key="late-crypto-completed-order-payment",
            provider_invoice_id="late-crypto-completed-order-provider",
            provider_invoice_url="https://pay.example.test/invoice/late-order",
            unique_amount_window=0,
            now=past,
        )
        topup = self.db.create_wallet_topup_payment(
            customer["id"],
            25_000,
            "crypto",
            idempotency_key="late-crypto-completed-topup-payment",
            provider_invoice_id="late-crypto-completed-topup-provider",
            provider_invoice_url="https://pay.example.test/invoice/late-topup",
            unique_amount_window=0,
            now=past,
        )
        provider = Mock()
        provider.operation.side_effect = lambda transaction_id: {
            "id": transaction_id,
            "type": "invoice",
            "status": "completed",
        }
        self.app._plisio = provider

        self.app.run_maintenance()
        self.app.run_maintenance()

        settled_order = self.db.get_order(order["id"])
        self.assertEqual(self.db.get_payment(order_payment["id"])["status"], "paid")
        self.assertEqual(settled_order["status"], "completed")
        self.assertEqual(settled_order["external_paid_amount"], 100_000)
        self.assertEqual(self.db.get_payment(topup["id"])["status"], "paid")
        self.assertEqual(self.db.wallet_balance(customer["id"]), 25_000)
        self.assertEqual(
            len(
                [
                    entry
                    for entry in self.db.list_wallet_entries(customer["id"], limit=20)
                    if entry["payment_id"] == topup["id"]
                ]
            ),
            1,
        )
        self.assertEqual(provider.operation.call_count, 2)
        self.assertEqual(
            sum(
                1
                for message in self.telegram.messages
                if self.inventory_payload in message["text"]
            ),
            1,
        )

    def test_provider_expired_crypto_terminals_order_after_local_deadline(self) -> None:
        customer = self.db.upsert_user(
            self.CUSTOMER["id"],
            self.CUSTOMER["id"],
            username=self.CUSTOMER["username"],
        )
        past = utc_now() - timedelta(hours=1)
        order = self.db.create_order(
            customer["id"],
            self.product["id"],
            idempotency_key="provider-expired-crypto-order",
            now=past,
        )
        payment = self.db.create_order_payment(
            order["id"],
            "crypto",
            idempotency_key="provider-expired-crypto-payment",
            provider_invoice_id="provider-expired-crypto-id",
            provider_invoice_url="https://pay.example.test/invoice/provider-expired",
            unique_amount_window=0,
            now=past,
        )
        provider = Mock()
        provider.operation.return_value = {
            "id": "provider-expired-crypto-id",
            "type": "invoice",
            "status": "expired",
            "amount": "0",
        }
        self.app._plisio = provider

        self.app.run_maintenance()
        self.app.run_maintenance()

        self.assertEqual(self.db.get_payment(payment["id"])["status"], "failed")
        self.assertEqual(self.db.get_order(order["id"])["status"], "expired")
        # Failed-zero crypto invoices remain under bounded observation because
        # Plisio may later manually complete an expired invoice.
        self.assertEqual(provider.operation.call_count, 2)
        self.assertEqual(
            sum(
                1
                for message in self.telegram.messages
                if order["order_number"] in message["text"] and "منقضی" in message["text"]
            ),
            1,
        )

    def test_provider_outage_does_not_locally_expire_crypto_intents(self) -> None:
        customer = self.db.upsert_user(
            self.CUSTOMER["id"],
            self.CUSTOMER["id"],
            username=self.CUSTOMER["username"],
        )
        past = utc_now() - timedelta(hours=1)
        order = self.db.create_order(
            customer["id"],
            self.product["id"],
            idempotency_key="provider-outage-crypto-order",
            now=past,
        )
        order_payment = self.db.create_order_payment(
            order["id"],
            "crypto",
            idempotency_key="provider-outage-crypto-order-payment",
            provider_invoice_id="provider-outage-crypto-order-id",
            provider_invoice_url="https://pay.example.test/invoice/provider-outage-order",
            unique_amount_window=0,
            now=past,
        )
        topup = self.db.create_wallet_topup_payment(
            customer["id"],
            25_000,
            "crypto",
            idempotency_key="provider-outage-crypto-topup-payment",
            provider_invoice_id="provider-outage-crypto-topup-id",
            provider_invoice_url="https://pay.example.test/invoice/provider-outage-topup",
            unique_amount_window=0,
            now=past,
        )
        provider = Mock()
        provider.operation.side_effect = PlisioError("temporary provider outage")
        self.app._plisio = provider

        self.app.run_maintenance()

        self.assertEqual(self.db.get_payment(order_payment["id"])["status"], "pending")
        self.assertEqual(self.db.get_payment(topup["id"])["status"], "pending")
        self.assertEqual(
            self.db.get_order(order["id"])["status"], "awaiting_confirmation"
        )
        self.assertEqual(self.db.wallet_balance(customer["id"]), 0)

    def test_delayed_cancelled_card_transfer_cannot_match_a_new_intent(self) -> None:
        current = utc_now()
        first_user = self.db.upsert_user(5201, 5201, username="old_card_sender")
        first_order = self.db.create_order(
            first_user["id"],
            self.product["id"],
            idempotency_key="delayed-card-first-order",
            now=current,
        )
        first_payment = self.db.create_order_payment(
            first_order["id"],
            "card",
            idempotency_key="delayed-card-first-payment",
            unique_amount_window=1,
            now=current,
        )
        self.db.cancel_pending_payment(
            first_payment["id"],
            first_user["id"],
            now=current + timedelta(minutes=1),
        )

        second_user = self.db.upsert_user(5202, 5202, username="new_card_buyer")
        second_order = self.db.create_order(
            second_user["id"],
            self.product["id"],
            idempotency_key="delayed-card-second-order",
            now=current + timedelta(minutes=2),
        )
        second_payment = self.db.create_order_payment(
            second_order["id"],
            "card",
            idempotency_key="delayed-card-second-payment",
            unique_amount_window=1,
            now=current + timedelta(minutes=2),
        )
        self.assertNotEqual(
            first_payment["payable_amount"], second_payment["payable_amount"]
        )

        outcome = self.app.confirm_card_amount(
            int(first_payment["payable_amount"]),
            "delayed-transfer-after-cancel",
            (current + timedelta(minutes=3)).isoformat(),
        )
        self.assertEqual(outcome, ConfirmationOutcome.NOT_FOUND)
        self.assertEqual(
            self.db.get_payment(second_payment["id"])["status"], "pending"
        )
        event = self.db.get_card_payment_event("delayed-transfer-after-cancel")
        self.assertIsNotNone(event)
        self.assertEqual(event["status"], "review")
        self.assertEqual(event["payment_id"], first_payment["id"])

    def test_permanent_reminder_failure_does_not_starve_the_next_reminder(self) -> None:
        current = utc_now() - timedelta(days=1, seconds=1)
        product = self.db.create_product(
            self.category["id"],
            "سرویس یادآوری",
            product_type="manual",
            price_amount=0,
            duration_days=2,
            reminder_days=(1,),
            idempotency_key="reminder-head-of-line-product",
        )
        users = [
            self.db.upsert_user(5101, 5101, username="blocked_reminder_user"),
            self.db.upsert_user(5102, 5102, username="valid_reminder_user"),
        ]
        for index, customer in enumerate(users):
            order = self.db.create_order(
                customer["id"],
                product["id"],
                idempotency_key=f"reminder-head-of-line-order:{index}",
                now=current,
            )
            self.db.update_order_status(order["id"], "awaiting_info", now=current)
            self.db.set_order_customer_info(
                order["id"], {"text": "customer information"}, now=current
            )
            self.db.update_order_status(order["id"], "processing", now=current)
            self.db.complete_order(order["id"], "delivery", now=current)

        attempts: list[int] = []
        original_send = self.telegram.send_message

        def send_with_one_blocked_recipient(
            chat_id: int, text: str, **kwargs: Any
        ) -> dict[str, Any]:
            attempts.append(int(chat_id))
            if int(chat_id) == int(users[0]["chat_id"]):
                raise TelegramAPIError(
                    "sendMessage", "bot was blocked", error_code=403
                )
            return original_send(chat_id, text, **kwargs)

        with patch.object(
            self.telegram,
            "send_message",
            side_effect=send_with_one_blocked_recipient,
        ):
            self.app._deliver_due_reminders()

        self.assertEqual(attempts.count(int(users[0]["chat_id"])), 1)
        self.assertEqual(attempts.count(int(users[1]["chat_id"])), 1)
        connection = sqlite3.connect(self.db.path)
        try:
            statuses = [
                row[0]
                for row in connection.execute(
                    "SELECT status FROM reminders ORDER BY id"
                ).fetchall()
            ]
        finally:
            connection.close()
        self.assertEqual(statuses, ["failed", "sent"])

    def test_expired_subscription_does_not_receive_a_stale_reminder(self) -> None:
        completed_at = utc_now() - timedelta(days=3)
        product = self.db.create_product(
            self.category["id"],
            "سرویس یادآوری منقضی",
            product_type="manual",
            price_amount=0,
            duration_days=1,
            reminder_days=(1,),
            idempotency_key="expired-reminder-product",
        )
        # A zero-day setting schedules before expiry on the local expiry date.
        # If delivery is delayed beyond expiry, cancel it without sending.
        connection = sqlite3.connect(self.db.path)
        try:
            connection.execute(
                "UPDATE products SET reminder_days_json = '[0]' WHERE id = ?",
                (product["id"],),
            )
            connection.commit()
        finally:
            connection.close()
        customer = self.db.upsert_user(5301, 5301, username="expired_reminder_user")
        order = self.db.create_order(
            customer["id"],
            product["id"],
            idempotency_key="expired-reminder-order",
            now=completed_at,
        )
        self.db.update_order_status(order["id"], "awaiting_info", now=completed_at)
        self.db.set_order_customer_info(
            order["id"], {"text": "customer information"}, now=completed_at
        )
        self.db.update_order_status(order["id"], "processing", now=completed_at)
        self.db.complete_order(order["id"], "delivery", now=completed_at)
        before = len(self.telegram.messages)

        self.app._deliver_due_reminders()

        self.assertEqual(len(self.telegram.messages), before)
        connection = sqlite3.connect(self.db.path)
        try:
            reminder = connection.execute(
                "SELECT status, error_text FROM reminders WHERE order_id = ?",
                (order["id"],),
            ).fetchone()
        finally:
            connection.close()
        self.assertIsNotNone(reminder)
        self.assertEqual(reminder[0], "cancelled")
        self.assertIn("subscription ended", reminder[1])

    def test_card_confirmation_reference_is_idempotent_and_delivers_once(self) -> None:
        customer, order, payment = self.create_pending_card_payment_without_wallet()
        reference = "bank-sms-reference-0001"
        created_at = parse_iso(payment["created_at"])
        self.assertIsNotNone(created_at)
        occurred_at = (created_at + timedelta(seconds=1)).isoformat(
            timespec="seconds"
        )

        before = len(self.telegram.messages)
        first = self.app.confirm_card_amount(
            int(payment["payable_amount"]),
            reference,
            occurred_at,
        )
        second = self.app.confirm_card_amount(
            int(payment["payable_amount"]),
            reference,
            occurred_at,
        )

        confirmed = self.db.get_payment(payment["id"])
        delivered = self.db.get_order(order["id"])
        inventory = self.db.list_inventory_items(self.product["id"])[0]
        deliveries = [
            item
            for item in self.telegram.messages
            if item["chat_id"] == customer["chat_id"]
            and self.inventory_payload in item["text"]
        ]
        self.assertEqual(first, ConfirmationOutcome.CONFIRMED)
        self.assertEqual(second, ConfirmationOutcome.ALREADY_CONFIRMED)
        self.assertEqual(confirmed["status"], "paid")
        self.assertEqual(confirmed["external_reference"], reference)
        self.assertEqual(delivered["status"], "completed")
        self.assertEqual(delivered["external_paid_amount"], 100_000)
        self.assertEqual(self.db.wallet_balance(customer["id"]), 0)
        self.assertEqual(inventory["status"], "assigned")
        self.assertEqual(inventory["assigned_order_id"], order["id"])
        self.assertEqual(len(deliveries), 1)
        buyer_messages = [
            item
            for item in self.telegram.messages[before:]
            if int(item["chat_id"]) == int(customer["chat_id"])
        ]
        self.assertLess(
            next(
                index
                for index, item in enumerate(buyer_messages)
                if "پرداخت با موفقیت" in item["text"]
            ),
            next(
                index
                for index, item in enumerate(buyer_messages)
                if self.inventory_payload in item["text"]
            ),
        )

    def test_external_payment_success_precedes_manual_information_prompt(self) -> None:
        customer = self.db.upsert_user(5_801, 5_801, username="manual_order_buyer")
        manual = self.db.create_product(
            self.category["id"],
            "سرویس دستی",
            product_type="manual",
            price_amount=50_000,
            info_request_text="شناسه سرویس را ارسال کن.",
            idempotency_key="manual-order-success-ordering",
        )
        order = self.db.create_order(
            customer["id"], manual["id"], idempotency_key="manual-success-order"
        )
        payment = self.db.create_order_payment(
            order["id"], "card", idempotency_key="manual-success-payment"
        )
        before = len(self.telegram.messages)

        self.app._complete_payment(
            int(payment["id"]), external_reference="manual-success-reference"
        )

        buyer_messages = [
            item
            for item in self.telegram.messages[before:]
            if int(item["chat_id"]) == int(customer["chat_id"])
        ]
        self.assertGreaterEqual(len(buyer_messages), 2)
        self.assertIn("پرداخت با موفقیت", buyer_messages[0]["text"])
        self.assertIn("شناسه سرویس", buyer_messages[1]["text"])

    def test_transient_success_notice_failure_defers_every_fulfillment_branch(
        self,
    ) -> None:
        cases = [
            ("ready", self.product, "completed", self.inventory_payload),
            (
                "manual",
                self.db.create_product(
                    self.category["id"],
                    "دستی وابسته به اعلان",
                    product_type="manual",
                    price_amount=40_000,
                    info_request_text="اطلاعات دستی وابسته را بفرست.",
                    idempotency_key="success-dependency-manual",
                ),
                "awaiting_info",
                "اطلاعات دستی وابسته",
            ),
            (
                "reserve",
                self.db.create_product(
                    self.category["id"],
                    "رزرو وابسته به اعلان",
                    product_type="ready",
                    price_amount=30_000,
                    reserve_enabled=True,
                    idempotency_key="success-dependency-reserve",
                ),
                "awaiting_stock",
                "رزرو",
            ),
        ]
        original_send = self.telegram.send_message
        for index, (label, product, expected_status, branch_text) in enumerate(cases):
            with self.subTest(branch=label):
                customer = self.db.upsert_user(
                    5_900 + index,
                    5_900 + index,
                    username=f"success_dependency_{label}",
                )
                order = self.db.create_order(
                    customer["id"],
                    product["id"],
                    idempotency_key=f"success-dependency-order:{label}",
                )
                payment = self.db.create_order_payment(
                    order["id"],
                    "card",
                    idempotency_key=f"success-dependency-payment:{label}",
                )

                def fail_success(chat_id: int, text: str, **kwargs: Any) -> dict[str, Any]:
                    if int(chat_id) == int(customer["chat_id"]) and "پرداخت با موفقیت" in text:
                        raise TelegramError("temporary success-message outage")
                    return original_send(chat_id, text, **kwargs)

                with patch.object(
                    self.telegram, "send_message", side_effect=fail_success
                ):
                    self.app._complete_payment(
                        int(payment["id"]),
                        external_reference=f"success-dependency-reference:{label}",
                    )

                self.assertEqual(self.db.get_order(order["id"])["status"], "paid")
                self.assertFalse(
                    any(
                        int(item["chat_id"]) == int(customer["chat_id"])
                        and branch_text in item["text"]
                        for item in self.telegram.messages
                    )
                )

                connection = sqlite3.connect(self.db.path)
                try:
                    connection.execute(
                        "UPDATE outbound_messages SET scheduled_at = ? "
                        "WHERE idempotency_key = ?",
                        (
                            "2000-01-01T00:00:00+00:00",
                            f"payment:{payment['id']}:order-confirmed",
                        ),
                    )
                    connection.commit()
                finally:
                    connection.close()

                before = len(self.telegram.messages)
                self.app.run_maintenance()
                self.assertEqual(
                    self.db.get_order(order["id"])["status"], expected_status
                )
                buyer_messages = [
                    item
                    for item in self.telegram.messages[before:]
                    if int(item["chat_id"]) == int(customer["chat_id"])
                ]
                self.assertGreaterEqual(len(buyer_messages), 2)
                self.assertIn("پرداخت با موفقیت", buyer_messages[0]["text"])
                self.assertTrue(
                    any(branch_text in item["text"] for item in buyer_messages[1:])
                )

    def test_terminal_success_notice_failure_does_not_strand_paid_delivery(self) -> None:
        customer = self.db.upsert_user(
            5_950, 5_950, username="terminal_notice_failure"
        )
        product = self.db.create_product(
            self.category["id"],
            "تحویل پس از شکست نهایی اعلان",
            product_type="ready",
            price_amount=25_000,
            idempotency_key="terminal-notice-product",
        )
        payload = "terminal-notice-delivery-payload"
        self.db.add_inventory_item(product["id"], payload)
        order = self.db.create_order(
            customer["id"], product["id"], idempotency_key="terminal-notice-order"
        )
        payment = self.db.create_order_payment(
            order["id"], "card", idempotency_key="terminal-notice-payment"
        )
        with patch.object(
            self.telegram,
            "send_message",
            side_effect=TelegramError("success notice unavailable"),
        ):
            self.app._complete_payment(
                int(payment["id"]), external_reference="terminal-notice-reference"
            )
        outbound = self.db.get_outbound_message_by_idempotency_key(
            f"payment:{payment['id']}:order-confirmed"
        )
        self.assertIsNotNone(outbound)
        for attempt in range(2, 13):
            at = utc_now() + timedelta(days=attempt)
            claimed = self.db.claim_outbound_message(int(outbound["id"]), now=at)
            self.assertIsNotNone(claimed)
            outbound = self.db.schedule_outbound_retry(
                int(outbound["id"]),
                "still unavailable",
                max_attempts=12,
                now=at,
            )
        self.assertEqual(outbound["status"], "failed")

        before = len(self.telegram.messages)
        self.app.run_maintenance()
        self.assertEqual(self.db.get_order(order["id"])["status"], "completed")
        self.assertTrue(
            any(
                int(item["chat_id"]) == int(customer["chat_id"])
                and payload in item["text"]
                for item in self.telegram.messages[before:]
            )
        )
        self.app.show_order(customer, int(order["id"]))
        self.assertIn(payload, self.telegram.messages[-1]["text"])

    def test_card_confirmation_rolls_back_when_event_insert_fails(self) -> None:
        customer, order, payment = self.create_pending_card_payment_without_wallet()
        reference = "bank-atomic-rollback-0001"
        created_at = parse_iso(payment["created_at"])
        self.assertIsNotNone(created_at)
        occurred_at = (created_at + timedelta(seconds=1)).isoformat(
            timespec="seconds"
        )

        with patch.object(
            self.db,
            "_record_card_payment_event_in_transaction",
            side_effect=DatabaseError("simulated event insert failure"),
        ):
            with self.assertRaises(DatabaseError):
                self.app.confirm_card_amount(
                    int(payment["payable_amount"]),
                    reference,
                    occurred_at,
                )

        rolled_back_payment = self.db.get_payment(payment["id"])
        rolled_back_order = self.db.get_order(order["id"])
        self.assertEqual(rolled_back_payment["status"], "pending")
        self.assertIsNone(rolled_back_payment["external_reference"])
        self.assertEqual(rolled_back_order["status"], "awaiting_confirmation")
        self.assertEqual(rolled_back_order["external_paid_amount"], 0)
        self.assertIsNone(self.db.get_card_payment_event(reference))
        self.assertEqual(
            self.db.list_inventory_items(self.product["id"])[0]["status"],
            "available",
        )

        retry = self.app.confirm_card_amount(
            int(payment["payable_amount"]),
            reference,
            occurred_at,
        )
        confirmed_event = self.db.get_card_payment_event(reference)
        self.assertEqual(retry, ConfirmationOutcome.CONFIRMED)
        self.assertEqual(self.db.get_payment(payment["id"])["status"], "paid")
        self.assertIsNotNone(confirmed_event)
        self.assertEqual(confirmed_event["status"], "confirmed")
        self.assertEqual(confirmed_event["payment_id"], payment["id"])
        self.assertTrue(
            any(
                item["chat_id"] == customer["chat_id"]
                and self.inventory_payload in item["text"]
                for item in self.telegram.messages
            )
        )

    def test_card_confirmation_backfills_legacy_paid_event_and_rejects_changed_terms(
        self,
    ) -> None:
        _customer, order, payment = self.create_pending_card_payment_without_wallet()
        reference = "bank-legacy-paid-0001"
        created_at = parse_iso(payment["created_at"])
        self.assertIsNotNone(created_at)
        occurred = created_at + timedelta(seconds=1)
        occurred_at = occurred.isoformat(timespec="seconds")
        raw_payload = {
            "amount": int(payment["payable_amount"]),
            "reference": reference,
            "occurred_at": occurred_at,
        }

        # Reproduce the old crash boundary: money/order committed, but the
        # separate card_payment_events insert never ran.
        self.db.mark_payment_paid(
            payment["id"],
            external_reference=reference,
            raw_payload=raw_payload,
        )
        self.assertIsNone(self.db.get_card_payment_event(reference))

        replay = self.app.confirm_card_amount(
            int(payment["payable_amount"]),
            reference,
            occurred_at,
        )
        event = self.db.get_card_payment_event(reference)
        self.assertEqual(replay, ConfirmationOutcome.ALREADY_CONFIRMED)
        self.assertIsNotNone(event)
        self.assertEqual(event["payment_id"], payment["id"])
        self.assertEqual(event["status"], "confirmed")
        self.assertEqual(
            self.db.get_order(order["id"])["external_paid_amount"],
            int(payment["base_amount"]),
        )

        changed_terms = self.app.confirm_card_amount(
            int(payment["payable_amount"]),
            reference,
            (occurred + timedelta(seconds=1)).isoformat(timespec="seconds"),
        )
        unchanged_event = self.db.get_card_payment_event(reference)
        self.assertEqual(changed_terms, ConfirmationOutcome.CONFLICT)
        self.assertEqual(unchanged_event, event)
        self.assertEqual(
            self.db.get_order(order["id"])["external_paid_amount"],
            int(payment["base_amount"]),
        )

    def test_card_confirmation_rejects_unproven_paid_state_without_raw_payload(
        self,
    ) -> None:
        _customer, _order, payment = self.create_pending_card_payment_without_wallet()
        reference = "bank-unproven-paid-0001"
        created_at = parse_iso(payment["created_at"])
        self.assertIsNotNone(created_at)
        occurred_at = (created_at + timedelta(seconds=1)).isoformat(
            timespec="seconds"
        )

        self.db.mark_payment_paid(
            payment["id"],
            external_reference=reference,
        )
        result = self.app.confirm_card_amount(
            int(payment["payable_amount"]),
            reference,
            occurred_at,
        )

        self.assertEqual(result, ConfirmationOutcome.CONFLICT)
        self.assertIsNone(self.db.get_card_payment_event(reference))
        self.assertIsNone(self.db.get_payment(payment["id"])["raw_payload_json"])

    def test_notification_failures_do_not_log_private_chat_id(self) -> None:
        private_chat_id = 987_654_321_012
        user = self.db.upsert_user(
            private_chat_id,
            private_chat_id,
            username="log_redaction_test",
        )

        with patch.object(
            self.telegram,
            "send_message",
            side_effect=TelegramError("simulated delivery failure"),
        ), self.assertLogs("app.bot", level="WARNING") as captured:
            self.assertFalse(self.app._notify_user(private_chat_id, "پیام آزمایشی"))
            self.assertFalse(
                self.app._notify_user_durable(
                    user,
                    "پیام پایدار آزمایشی",
                    idempotency_key="log-redaction-notice",
                )
            )

        rendered_logs = "\n".join(captured.output)
        self.assertNotIn(str(private_chat_id), rendered_logs)
        self.assertIn("Could not notify user", rendered_logs)
        self.assertIn("Could not deliver outbound message", rendered_logs)

    def test_card_confirmation_before_payment_creation_conflicts_without_mutation(self) -> None:
        customer, order, payment = self.create_pending_card_payment_without_wallet()
        created_at = parse_iso(payment["created_at"])
        self.assertIsNotNone(created_at)
        stale_occurred_at = (created_at - timedelta(minutes=5)).isoformat(
            timespec="seconds"
        )

        result = self.app.confirm_card_amount(
            int(payment["payable_amount"]),
            "bank-sms-before-payment",
            stale_occurred_at,
        )

        unchanged_payment = self.db.get_payment(payment["id"])
        unchanged_order = self.db.get_order(order["id"])
        inventory = self.db.list_inventory_items(self.product["id"])[0]
        self.assertEqual(result, ConfirmationOutcome.CONFLICT)
        self.assertEqual(unchanged_payment["status"], "pending")
        self.assertIsNone(unchanged_payment["external_reference"])
        self.assertEqual(unchanged_order["status"], "awaiting_confirmation")
        self.assertEqual(unchanged_order["external_paid_amount"], 0)
        self.assertEqual(self.db.wallet_balance(customer["id"]), 0)
        self.assertEqual(inventory["status"], "available")
        self.assertFalse(
            any(
                item["chat_id"] == customer["chat_id"]
                and self.inventory_payload in item["text"]
                for item in self.telegram.messages
            )
        )

    def test_late_card_event_cannot_credit_an_intent_created_after_the_event(self) -> None:
        """A released amount slot must not redirect an older transfer to a new user."""

        first_user = self.db.upsert_user(5001, 5001, username="first_payer")
        second_user = self.db.upsert_user(5002, 5002, username="second_payer")
        first_created_at = utc_now() - timedelta(minutes=1)
        first_payment = self.db.create_wallet_topup_payment(
            first_user["id"],
            100_000,
            "card",
            idempotency_key="late-event-first-payment",
            unique_amount_window=0,
            now=first_created_at,
        )
        self.db.set_payment_status(
            first_payment["id"],
            "cancelled",
            now=first_created_at + timedelta(seconds=20),
        )
        second_payment = self.db.create_wallet_topup_payment(
            second_user["id"],
            100_000,
            "card",
            idempotency_key="late-event-second-payment",
            unique_amount_window=1,
            now=first_created_at + timedelta(seconds=50),
        )
        occurred_at = (first_created_at + timedelta(seconds=10)).isoformat(
            timespec="seconds"
        )

        result = self.app.confirm_card_amount(
            100_000,
            "bank-transfer-for-first-payment",
            occurred_at,
        )

        self.assertEqual(second_payment["payable_amount"], 100_001)
        self.assertEqual(result, ConfirmationOutcome.NOT_FOUND)
        self.assertEqual(self.db.get_payment(second_payment["id"])["status"], "pending")
        self.assertEqual(self.db.wallet_balance(first_user["id"]), 0)
        self.assertEqual(self.db.wallet_balance(second_user["id"]), 0)
        event = self.db.get_card_payment_event("bank-transfer-for-first-payment")
        self.assertIsNotNone(event)
        self.assertEqual(event["status"], "review")
        self.assertEqual(event["payment_id"], first_payment["id"])

    def test_crash_after_inventory_assignment_is_reconciled_to_one_delivery(self) -> None:
        """A committed credential assignment must remain discoverable by maintenance."""

        customer, order, payment = self.create_pending_card_payment_without_wallet()
        self.db.mark_payment_paid(
            payment["id"],
            external_reference="bank-crash-after-assignment",
        )
        assigned = self.db.assign_inventory(order["id"])
        self.assertEqual(assigned["payload"], self.inventory_payload)
        self.assertEqual(self.db.get_order(order["id"])["status"], "completed")

        # This database state represents a process death after assignment was
        # committed but before the Telegram send/outbox step ran.
        self.telegram.messages.clear()
        self.app.run_maintenance()
        self.app.run_maintenance()

        deliveries = [
            item
            for item in self.telegram.messages
            if item["chat_id"] == customer["chat_id"]
            and self.inventory_payload in item["text"]
        ]
        self.assertEqual(
            len(deliveries),
            1,
            "maintenance must recover the committed delivery exactly once",
        )

    def test_non_reserved_ready_stock_race_completes_after_restock(self) -> None:
        customer = self.db.upsert_user(5101, 5101, username="restock_buyer")
        product = self.db.create_product(
            self.category["id"],
            "محصول آماده بدون رزرو",
            product_type="ready",
            price_amount=200,
            reserve_enabled=False,
            idempotency_key="processing-restock-product",
        )
        order = self.db.create_order(
            customer["id"], product["id"], idempotency_key="processing-restock-order"
        )
        payment = self.db.create_order_payment(
            order["id"], "card", idempotency_key="processing-restock-payment"
        )
        self.app._complete_payment(
            int(payment["id"]), external_reference="processing-restock-reference"
        )
        self.assertEqual(self.db.get_order(order["id"])["status"], "processing")

        payload = "restocked@example.test\npassword: restocked-secret"
        self.db.add_inventory_item(product["id"], payload)
        self.app.run_maintenance()
        self.app.run_maintenance()

        completed = self.db.get_order(order["id"])
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["delivered_payload"], payload)
        deliveries = [
            item
            for item in self.telegram.messages
            if item["chat_id"] == customer["chat_id"] and payload in item["text"]
        ]
        self.assertEqual(len(deliveries), 1)

    def test_sufficient_wallet_pays_and_delivers_without_card(self) -> None:
        customer = self.start_customer_and_open_product()
        self.db.credit_wallet(
            customer["id"],
            100_000,
            reason="integration test full wallet",
            idempotency_key="test:wallet-full",
        )
        self.send_callback(self.CUSTOMER, f"buy:{self.product['id']}")
        self.send_message(self.CUSTOMER, text="محمد رضایی")
        self.send_message(
            self.CUSTOMER,
            contact={
                "user_id": self.CUSTOMER["id"],
                "phone_number": "+989121234567",
            },
        )
        order = self.db.list_orders(user_id=customer["id"])[0]

        self.send_callback(self.CUSTOMER, f"checkout:{order['id']}")
        before = len(self.telegram.messages)
        self.send_callback(self.CUSTOMER, f"paywallet:{order['id']}")

        delivered = self.db.get_order(order["id"])
        inventory = self.db.list_inventory_items(self.product["id"])[0]
        self.assertEqual(delivered["status"], "completed")
        self.assertEqual(delivered["wallet_captured_amount"], 100_000)
        self.assertEqual(delivered["external_paid_amount"], 0)
        self.assertEqual(self.db.wallet_balance(customer["id"]), 0)
        self.assertEqual(inventory["status"], "assigned")
        self.assertTrue(
            any(
                item["chat_id"] == customer["chat_id"]
                and self.inventory_payload in item["text"]
                for item in self.telegram.messages
            )
        )
        buyer_messages = [
            item
            for item in self.telegram.messages[before:]
            if int(item["chat_id"]) == int(customer["chat_id"])
        ]
        self.assertIn("پرداخت با موفقیت", buyer_messages[0]["text"])
        self.assertTrue(any(self.inventory_payload in item["text"] for item in buyer_messages[1:]))

    def test_maintenance_delivers_new_stock_to_oldest_paid_reservation(self) -> None:
        reserved_product = self.db.create_product(
            self.category["id"],
            "محصول رزروی",
            product_type="ready",
            price_amount=25_000,
            reserve_enabled=True,
            delivery_instructions="اطلاعات را محرمانه نگه دار.",
        )
        first_actor = {
            "id": 3001,
            "username": "first_reserved_buyer",
            "first_name": "خریدار اول",
        }
        second_actor = {
            "id": 3002,
            "username": "second_reserved_buyer",
            "first_name": "خریدار دوم",
        }

        def create_paid_reservation(
            actor: dict[str, Any], idempotency_suffix: str
        ) -> tuple[dict[str, Any], dict[str, Any]]:
            self.send_message(actor, text="/start")
            user = self.db.get_user_by_chat_id(actor["id"])
            self.assertIsNotNone(user)
            self.db.credit_wallet(
                user["id"],
                25_000,
                reason="reservation integration test",
                idempotency_key=f"test:reservation-credit:{idempotency_suffix}",
            )
            order = self.db.create_order(
                user["id"],
                reserved_product["id"],
                idempotency_key=f"test:reservation-order:{idempotency_suffix}",
            )
            paid = self.db.hold_wallet_funds(
                order["id"],
                idempotency_key=f"test:reservation-wallet:{idempotency_suffix}",
            )
            self.assertEqual(paid["status"], "paid")
            self.app._reconcile_zero_external_payment_notices()
            queued = self.app.fulfill_order(paid["id"])
            self.assertEqual(queued["status"], "awaiting_stock")
            return user, queued  # type: ignore[return-value]

        first_user, first_order = create_paid_reservation(first_actor, "first")
        second_user, second_order = create_paid_reservation(second_actor, "second")
        payload = "oldest-reservation@example.test\npassword: reserved-secret"
        item = self.db.add_inventory_item(reserved_product["id"], payload)

        self.app.run_maintenance()

        first_after = self.db.get_order(first_order["id"])
        second_after = self.db.get_order(second_order["id"])
        assigned = self.db.list_inventory_items(reserved_product["id"])[0]
        self.assertEqual(first_after["status"], "completed")
        self.assertEqual(second_after["status"], "awaiting_stock")
        self.assertEqual(assigned["id"], item["id"])
        self.assertEqual(assigned["status"], "assigned")
        self.assertEqual(assigned["assigned_order_id"], first_order["id"])
        self.assertTrue(
            any(
                sent["chat_id"] == first_user["chat_id"] and payload in sent["text"]
                for sent in self.telegram.messages
            ),
            "new stock must be delivered to the oldest queued buyer",
        )
        self.assertFalse(
            any(
                sent["chat_id"] == second_user["chat_id"] and payload in sent["text"]
                for sent in self.telegram.messages
            ),
            "one stock item must not be delivered to a later reservation",
        )

    def test_maintenance_stops_before_the_next_stage(self) -> None:
        self.assertIs(self.telegram.stop_event, self.app.stop_event)

        def request_shutdown(*, limit: int) -> list[int]:
            self.assertEqual(limit, 500)
            self.app.stop_event.set()
            return []

        with patch.object(
            self.db,
            "expire_unpaid_orders",
            side_effect=request_shutdown,
        ), patch.object(self.db, "expire_pending_payments") as expire_payments:
            self.app.run_maintenance()

        expire_payments.assert_not_called()

    def test_missing_order_notices_rotate_and_expiry_recovers_after_restart(self) -> None:
        customer = self.db.upsert_user(
            3040, 3040, username="notice_recovery_customer"
        )
        manual = self.db.create_product(
            self.category["id"],
            "سرویس دستی اعلان",
            product_type="manual",
            price_amount=0,
            info_request_text="اطلاعات حساب را ارسال کن.",
            idempotency_key="notice-recovery-manual",
        )
        orders = []
        for index in range(3):
            order = self.db.create_order(
                customer["id"],
                manual["id"],
                idempotency_key=f"notice-recovery-info:{index}",
            )
            orders.append(
                self.db.update_order_status(order["id"], "awaiting_info")
            )

        self.app.MAINTENANCE_ORDER_NOTICE_RECONCILE_LIMIT = 1
        self.telegram.messages.clear()
        for _ in orders:
            self.app._reconcile_pending_order_notices()
        for order in orders:
            queued = self.db.get_outbound_message_by_idempotency_key(
                f"order:{order['id']}:info-request"
            )
            self.assertIsNotNone(queued)
            self.assertEqual(queued["status"], "sent")
        before_replay = len(self.telegram.messages)
        self.app._reconcile_pending_order_notices()
        self.assertEqual(len(self.telegram.messages), before_replay)

        created_at = utc_now() - timedelta(hours=2)
        self.db.credit_wallet(
            customer["id"],
            40_000,
            reason="expiry notice seed",
            idempotency_key="expiry-notice-wallet-seed",
            now=created_at,
        )
        discount = self.db.create_discount(
            "EXPIRYNOTICE",
            discount_type="fixed",
            value=10_000,
            max_uses=1,
            now=created_at,
        )
        expiring = self.db.create_order(
            customer["id"],
            self.product["id"],
            idempotency_key="expiry-notice-order",
            now=created_at,
        )
        self.db.apply_discount(expiring["id"], discount["code"], now=created_at)
        self.db.hold_wallet_funds(
            expiring["id"],
            max_amount=40_000,
            idempotency_key="expiry-notice-hold",
            now=created_at,
        )
        payment = self.db.create_order_payment(
            expiring["id"],
            "card",
            idempotency_key="expiry-notice-card",
            now=created_at,
        )
        self.db.expire_unpaid_orders(now=created_at + timedelta(minutes=31))
        state_after_expiry = self.db.get_order(expiring["id"])
        self.assertEqual(state_after_expiry["status"], "expired")
        self.assertEqual(self.db.get_payment(payment["id"])["status"], "expired")
        self.assertEqual(self.db.wallet_balance(customer["id"]), 40_000)
        self.assertEqual(
            next(
                row
                for row in self.db.list_discounts(active_only=False)
                if row["id"] == discount["id"]
            )["used_count"],
            0,
        )
        self.assertIsNone(
            self.db.get_outbound_message_by_idempotency_key(
                f"order:{expiring['id']}:expired-notice"
            )
        )

        self.telegram.messages.clear()
        self.app._reconcile_expired_order_notices()
        self.app._reconcile_expired_order_notices()
        expiry_notices = [
            item
            for item in self.telegram.messages
            if item["chat_id"] == customer["chat_id"]
            and expiring["order_number"] in item["text"]
        ]
        self.assertEqual(len(expiry_notices), 1)
        state_after_notice = self.db.get_order(expiring["id"])
        for field in (
            "status",
            "wallet_held_amount",
            "wallet_refunded_amount",
            "discount_amount",
            "payable_amount",
        ):
            self.assertEqual(state_after_notice[field], state_after_expiry[field])

    def test_user_ticket_alerts_are_durable_for_every_active_staff_role(self) -> None:
        admin = self.db.bootstrap_admin("ticket_alert_admin", 9041, role="admin")
        support = self.db.bootstrap_admin(
            "ticket_alert_support", 9042, role="support"
        )
        customer = self.db.upsert_user(
            3041, 3041, username="ticket_alert_customer"
        )
        self.db.set_user_state(
            customer["id"], "ticket_body", {"subject": "مشکل پیوست"}
        )
        staff_chats = {self.OWNER["id"], 9041, 9042}
        original_send = self.telegram.send_message

        def fail_staff_alert(
            chat_id: int, text: str, **kwargs: Any
        ) -> dict[str, Any]:
            if int(chat_id) in staff_chats and "پیام جدید کاربر" in text:
                raise TelegramAPIError(
                    "sendMessage", "temporary upstream error", error_code=500
                )
            return original_send(chat_id, text, **kwargs)

        with patch.object(
            self.telegram, "send_message", side_effect=fail_staff_alert
        ), self.assertLogs("app.bot", level="WARNING"):
            self.send_message(
                {"id": 3041, "username": "ticket_alert_customer"},
                document={"file_id": "durable-ticket-document"},
            )

        ticket = self.db.list_tickets(user_id=customer["id"])[0]
        initial = self.db.list_ticket_messages(ticket["id"])[0]
        staff = [
            row
            for row in self.db.list_admins(active_only=True)
            if row["id"] in {self.db.list_admins(active_only=True)[0]["id"], admin["id"], support["id"]}
        ]
        self.assertEqual({row["role"] for row in staff}, {"owner", "admin", "support"})
        for row in staff:
            queued = self.db.get_outbound_message_by_idempotency_key(
                f"ticket-message:{initial['id']}:admin:{row['id']}"
            )
            self.assertIsNotNone(queued)
            self.assertEqual(queued["status"], "queued")
            self.assertIn(
                f"/ticket_attachment {initial['id']}", queued["body"]
            )

        reply = self.db.add_ticket_message(
            ticket["id"],
            "پاسخ ثبت‌شده پیش از توقف پردازش",
            sender_type="user",
            sender_id=customer["id"],
            idempotency_key="ticket-alert-crash-reply",
        )
        self.app.MAINTENANCE_TICKET_ALERT_RECONCILE_LIMIT = 1
        self.app._reconcile_ticket_admin_alerts()
        self.app._reconcile_ticket_admin_alerts()

        connection = sqlite3.connect(self.db.path)
        try:
            connection.execute(
                "UPDATE outbound_messages SET scheduled_at = ? WHERE status = 'queued'",
                ((utc_now() - timedelta(minutes=1)).isoformat(timespec="seconds"),),
            )
            connection.commit()
        finally:
            connection.close()
        self.app._deliver_outbound_messages()

        for row in staff:
            for ticket_message in (initial, reply):
                queued = self.db.get_outbound_message_by_idempotency_key(
                    f"ticket-message:{ticket_message['id']}:admin:{row['id']}"
                )
                self.assertIsNotNone(queued)
                self.assertEqual(queued["status"], "sent")
            self.assertEqual(
                len(
                    [
                        item
                        for item in self.telegram.messages
                        if item["chat_id"] == row["chat_id"]
                        and "پیام جدید کاربر" in item["text"]
                    ]
                ),
                2,
            )

        before_replay = len(self.telegram.messages)
        self.app._reconcile_ticket_admin_alerts()
        self.app._reconcile_ticket_admin_alerts()
        self.app._deliver_outbound_messages()
        self.assertEqual(len(self.telegram.messages), before_replay)

    def test_outbound_loop_stops_between_items_without_stranding_claims(self) -> None:
        customer = self.db.upsert_user(
            3030,
            3030,
            username="shutdown_outbound_customer",
        )
        first = self.db.queue_outbound_message(
            "پیام اول",
            recipient_user_id=customer["id"],
            idempotency_key="shutdown-outbound-first",
        )
        second = self.db.queue_outbound_message(
            "پیام دوم",
            recipient_user_id=customer["id"],
            idempotency_key="shutdown-outbound-second",
        )
        original_send = self.telegram.send_message

        def send_then_stop(chat_id: int, text: str, **kwargs: Any) -> dict[str, Any]:
            sent = original_send(chat_id, text, **kwargs)
            self.app.stop_event.set()
            return sent

        with patch.object(self.telegram, "send_message", side_effect=send_then_stop):
            self.app._deliver_outbound_messages()

        self.assertEqual(
            self.db.get_outbound_message_by_idempotency_key(
                "shutdown-outbound-first"
            )["status"],
            "sent",
        )
        self.assertEqual(
            self.db.get_outbound_message_by_idempotency_key(
                "shutdown-outbound-second"
            )["status"],
            "queued",
        )
        self.assertEqual(
            [item["text"] for item in self.telegram.messages if item["text"].startswith("پیام")],
            ["پیام اول"],
        )
        self.assertLess(int(first["id"]), int(second["id"]))

    def test_shutdown_during_outbound_send_releases_the_current_claim(self) -> None:
        customer = self.db.upsert_user(
            3031,
            3031,
            username="shutdown_current_outbound_customer",
        )
        self.db.queue_outbound_message(
            "پیام در حال توقف",
            recipient_user_id=customer["id"],
            idempotency_key="shutdown-current-outbound",
        )

        def cancel_send(*_args: Any, **_kwargs: Any) -> None:
            self.app.stop_event.set()
            raise TelegramRequestCancelled("sendMessage")

        with patch.object(self.telegram, "send_message", side_effect=cancel_send):
            self.app._deliver_outbound_messages()

        self.assertEqual(
            self.db.get_outbound_message_by_idempotency_key(
                "shutdown-current-outbound"
            )["status"],
            "queued",
        )

    def test_reward_reconciliation_is_bounded_and_rotates_past_failures(self) -> None:
        product = self.db.create_product(
            self.category["id"],
            "سرویس دستی تست batch",
            product_type="manual",
            price_amount=0,
            info_request_text="اطلاعات تست را بفرست.",
        )
        customer = self.db.upsert_user(
            3010,
            3010,
            username="reward_batch_customer",
        )
        orders = [
            self.db.create_order(
                customer["id"],
                product["id"],
                idempotency_key=f"reward-batch-{index}",
            )
            for index in range(3)
        ]
        attempted: list[int] = []
        first_batch_ids = {int(order["id"]) for order in orders[:2]}
        original_after_paid = self.app._after_order_paid

        def reconcile_or_fail(order_id: int) -> None:
            attempted.append(order_id)
            if order_id in first_batch_ids:
                raise DatabaseError("simulated persistent reward failure")
            self.db.mark_order_rewards_processed(order_id)

        self.app.MAINTENANCE_REWARD_RECONCILE_LIMIT = 2
        self.app._after_order_paid = reconcile_or_fail  # type: ignore[method-assign]
        try:
            with self.assertLogs("app.bot", level="ERROR"):
                self.app._reconcile_paid_orders()
            self.assertEqual(attempted, [order["id"] for order in orders[:2]])

            self.app._reconcile_paid_orders()
        finally:
            self.app._after_order_paid = original_after_paid  # type: ignore[method-assign]

        self.assertEqual(attempted, [order["id"] for order in orders])
        self.assertIsNotNone(
            self.db.get_order(orders[2]["id"])["reward_processed_at"]
        )
        self.assertIsNone(self.db.get_order(orders[0]["id"])["reward_processed_at"])

    def test_reserved_inventory_fulfillment_is_bounded_and_progresses(self) -> None:
        product = self.db.create_product(
            self.category["id"],
            "محصول رزروی batch",
            product_type="ready",
            price_amount=0,
            reserve_enabled=True,
        )
        orders: list[dict[str, Any]] = []
        for index in range(3):
            customer = self.db.upsert_user(
                3020 + index,
                3020 + index,
                username=f"reservation_batch_{index}",
            )
            order = self.db.create_order(
                customer["id"],
                product["id"],
                idempotency_key=f"reservation-batch-{index}",
            )
            self.db.reserve_product(
                customer["id"],
                product["id"],
                order_id=order["id"],
            )
            orders.append(order)
        for index in range(3):
            self.db.add_inventory_item(
                product["id"],
                f"batch-user-{index}@example.test\npassword: synthetic-{index}",
            )

        self.app.MAINTENANCE_RESERVED_FULFILLMENT_LIMIT = 2
        self.app._fulfill_reserved_inventory()

        self.assertEqual(
            [self.db.get_order(order["id"])["status"] for order in orders],
            ["completed", "completed", "awaiting_stock"],
        )
        self.assertEqual(self.db.inventory_count(product["id"]), 1)

        self.app._fulfill_reserved_inventory()

        self.assertEqual(
            [self.db.get_order(order["id"])["status"] for order in orders],
            ["completed", "completed", "completed"],
        )
        self.assertEqual(self.db.inventory_count(product["id"]), 0)

    def test_referral_start_link_grants_reward_once_and_updates_summary(self) -> None:
        inviter = {"id": 2001, "username": "inviter", "first_name": "دعوت‌کننده"}
        invitee = {"id": 2002, "username": "invitee", "first_name": "دعوت‌شده"}
        self.send_message(inviter, text="/start")
        inviter_user = self.db.get_user_by_chat_id(inviter["id"])
        self.assertIsNotNone(inviter_user)
        self.db.create_reward_rule(
            "start-reward-test",
            event_type="start",
            amount=15_000,
        )

        self.send_message(invitee, text=f"/start ref_{inviter['id']}")
        summary = self.db.referral_summary(inviter_user["id"])
        self.assertEqual(summary["invited_count"], 1)
        self.assertEqual(summary["qualified_count"], 1)
        self.assertEqual(summary["reward_total"], 15_000)
        self.assertEqual(self.db.wallet_balance(inviter_user["id"]), 15_000)

        # Replaying /start is idempotent at the ledger level.
        self.send_message(invitee, text=f"/start ref_{inviter['id']}")
        self.assertEqual(self.db.wallet_balance(inviter_user["id"]), 15_000)

        self.send_message(inviter, text=REFERRAL)
        self.assertIn(
            f"start=ref_{inviter['id']}", self.telegram.messages[-1]["text"]
        )

    def test_reward_notice_queue_failure_remains_reconcilable(self) -> None:
        inviter = self.db.upsert_user(
            2201, 2201, username="reward_inviter", now=utc_now()
        )
        invitee = self.db.upsert_user(
            2202, 2202, username="reward_invitee", now=utc_now()
        )
        self.db.record_referral(inviter["id"], invitee["id"])
        self.db.create_reward_rule(
            "notice-recovery", event_type="product_purchase", amount=10_000
        )
        product = self.db.create_product(
            self.category["id"],
            "محصول بازیابی پاداش",
            product_type="ready",
            price_amount=1_000,
            idempotency_key="reward-notice-product",
        )
        self.db.add_inventory_item(product["id"], "reward-notice-credential")
        self.db.credit_wallet(
            invitee["id"],
            1_000,
            reason="reward notice recovery seed",
            idempotency_key="reward-notice-seed",
        )
        order = self.db.create_order(
            invitee["id"], product["id"], idempotency_key="reward-notice-order"
        )
        self.db.hold_wallet_funds(
            order["id"], idempotency_key="reward-notice-wallet-hold"
        )
        self.app._reconcile_zero_external_payment_notices()
        success_notice = self.db.get_outbound_message_by_idempotency_key(
            f"order:{order['id']}:wallet-confirmed"
        )
        self.assertEqual(success_notice["status"], "sent")
        original_queue = self.db.queue_outbound_message

        def fail_reward_notice(body: str, **kwargs: Any) -> dict[str, Any]:
            if str(kwargs.get("idempotency_key", "")).startswith("reward:"):
                raise DatabaseError("simulated queue commit failure")
            return original_queue(body, **kwargs)

        self.db.queue_outbound_message = fail_reward_notice  # type: ignore[method-assign]
        try:
            self.app._after_order_paid(order["id"])
        finally:
            self.db.queue_outbound_message = original_queue  # type: ignore[method-assign]

        after_failure = self.db.get_order(order["id"])
        self.assertEqual(after_failure["status"], "completed")
        self.assertIsNone(after_failure["reward_processed_at"])

        self.app._reconcile_paid_orders()

        self.assertIsNotNone(self.db.get_order(order["id"])["reward_processed_at"])
        self.assertTrue(
            any("پاداش دعوت" in item["text"] for item in self.telegram.messages)
        )

    def test_reward_notice_recovery_rotates_past_start_reward_crashes(self) -> None:
        self.db.create_reward_rule(
            "start-reward-crash-recovery", event_type="start", amount=7_000
        )
        rewards = []
        inviters = []
        for index in range(3):
            inviter = self.db.upsert_user(
                2300 + index,
                2300 + index,
                username=f"crash_reward_inviter_{index}",
            )
            invitee = self.db.upsert_user(
                2400 + index,
                2400 + index,
                username=f"crash_reward_invitee_{index}",
            )
            referral = self.db.record_referral(inviter["id"], invitee["id"])
            rewards.append(self.db.grant_start_rewards(referral["id"])[0])
            inviters.append(inviter)

        self.assertTrue(
            all(
                self.db.get_outbound_message_by_idempotency_key(
                    f"reward:{reward['id']}:notice"
                )
                is None
                for reward in rewards
            )
        )
        self.app.MAINTENANCE_REWARD_RECONCILE_LIMIT = 1
        self.telegram.messages.clear()
        for _ in rewards:
            self.app._reconcile_reward_notices()

        for inviter, reward in zip(inviters, rewards, strict=True):
            self.assertEqual(self.db.wallet_balance(inviter["id"]), 7_000)
            notice = self.db.get_outbound_message_by_idempotency_key(
                f"reward:{reward['id']}:notice"
            )
            self.assertIsNotNone(notice)
            self.assertEqual(notice["status"], "sent")
        before_replay = len(self.telegram.messages)
        self.app._reconcile_reward_notices()
        self.assertEqual(len(self.telegram.messages), before_replay)

    def test_reward_marker_crash_does_not_strand_paid_fulfillment(self) -> None:
        ready = self.db.create_product(
            self.category["id"],
            "محصول آماده بازیابی",
            product_type="ready",
            price_amount=0,
            idempotency_key="reward-marker-ready",
        )
        ready_payload = "reward-marker-ready-secret"
        self.db.add_inventory_item(ready["id"], ready_payload)
        manual = self.db.create_product(
            self.category["id"],
            "محصول دستی بازیابی",
            product_type="manual",
            price_amount=0,
            info_request_text="شناسه حساب را بفرست.",
            idempotency_key="reward-marker-manual",
        )
        buyers = [
            self.db.upsert_user(
                2500 + index,
                2500 + index,
                username=f"reward_marker_buyer_{index}",
            )
            for index in range(4)
        ]
        ready_order = self.db.create_order(
            buyers[0]["id"],
            ready["id"],
            idempotency_key="reward-marker-ready-order",
        )
        manual_orders = [
            self.db.create_order(
                buyers[index + 1]["id"],
                manual["id"],
                idempotency_key=f"reward-marker-manual-order:{index}",
            )
            for index in range(3)
        ]
        for order in [ready_order, *manual_orders]:
            self.db.mark_order_rewards_processed(order["id"])

        restarted = BotApplication(
            self.settings, self.db, self.telegram  # type: ignore[arg-type]
        )
        restarted.MAINTENANCE_FULFILLMENT_RECONCILE_LIMIT = 1
        self.telegram.messages.clear()
        for _ in range(4):
            restarted._reconcile_paid_fulfillment()

        self.assertEqual(self.db.get_order(ready_order["id"])["status"], "completed")
        self.assertTrue(
            any(ready_payload in item["text"] for item in self.telegram.messages)
        )
        for order in manual_orders:
            self.assertEqual(self.db.get_order(order["id"])["status"], "awaiting_info")
            self.assertEqual(
                self.db.get_outbound_message_by_idempotency_key(
                    f"order:{order['id']}:info-request"
                )["status"],
                "sent",
            )

    def test_no_stock_transition_alerts_recover_for_user_and_staff(self) -> None:
        customer = self.db.upsert_user(
            2600, 2600, username="no_stock_crash_customer"
        )
        product = self.db.create_product(
            self.category["id"],
            "محصول بدون موجودی",
            product_type="ready",
            price_amount=0,
            reserve_enabled=False,
            idempotency_key="no-stock-crash-product",
        )
        order = self.db.create_order(
            customer["id"],
            product["id"],
            idempotency_key="no-stock-crash-order",
        )
        self.db.mark_ready_order_processing(order["id"])

        self.telegram.messages.clear()
        self.app._reconcile_ready_stock_alerts()
        self.app._reconcile_ready_stock_alerts()

        user_notice = self.db.get_outbound_message_by_idempotency_key(
            f"order:{order['id']}:manual-stock-notice"
        )
        owner = self.db.list_admins(active_only=True)[0]
        admin_notice = self.db.get_outbound_message_by_idempotency_key(
            f"order:{order['id']}:manual-stock-admin:{owner['id']}"
        )
        self.assertEqual(user_notice["status"], "sent")
        self.assertEqual(admin_notice["status"], "sent")
        self.assertEqual(
            len(
                [
                    item
                    for item in self.telegram.messages
                    if item["chat_id"] == customer["chat_id"]
                    and "موجودی آماده" in item["text"]
                ]
            ),
            1,
        )
        self.assertEqual(
            len(
                [
                    item
                    for item in self.telegram.messages
                    if item["chat_id"] == owner["chat_id"]
                    and "موجودی ندارد" in item["text"]
                ]
            ),
            1,
        )

    def test_wallet_and_full_discount_success_notices_recover_without_starvation(self) -> None:
        manual = self.db.create_product(
            self.category["id"],
            "سرویس موفقیت بدون درگاه",
            product_type="manual",
            price_amount=100_000,
            info_request_text="اطلاعات را بفرست.",
            idempotency_key="zero-external-success-product",
        )
        wallet_user = self.db.upsert_user(
            2700, 2700, username="wallet_success_crash"
        )
        self.db.credit_wallet(
            wallet_user["id"],
            100_000,
            reason="wallet success seed",
            idempotency_key="wallet-success-seed",
        )
        wallet_order = self.db.create_order(
            wallet_user["id"],
            manual["id"],
            idempotency_key="wallet-success-order",
        )
        self.db.hold_wallet_funds(
            wallet_order["id"], idempotency_key="wallet-success-hold"
        )
        deferred_wallet = self.app.fulfill_order(wallet_order["id"])
        self.assertEqual(deferred_wallet["status"], "paid")

        discount = self.db.create_discount(
            "SUCCESS100", discount_type="percent", value=100
        )
        discount_orders = []
        for index in range(2):
            customer = self.db.upsert_user(
                2710 + index,
                2710 + index,
                username=f"discount_success_crash_{index}",
            )
            order = self.db.create_order(
                customer["id"],
                manual["id"],
                idempotency_key=f"discount-success-order:{index}",
            )
            self.db.apply_discount(order["id"], discount["code"])
            self.db.confirm_zero_payable_order(order["id"], customer["id"])
            deferred_discount = self.app.fulfill_order(order["id"])
            self.assertEqual(deferred_discount["status"], "paid")
            discount_orders.append(order)

        self.assertIsNone(
            self.db.get_outbound_message_by_idempotency_key(
                f"order:{wallet_order['id']}:wallet-confirmed"
            )
        )
        self.app.MAINTENANCE_ZERO_EXTERNAL_NOTICE_LIMIT = 1
        self.telegram.messages.clear()
        for _ in range(3):
            self.app._reconcile_zero_external_payment_notices()

        wallet_notice = self.db.get_outbound_message_by_idempotency_key(
            f"order:{wallet_order['id']}:wallet-confirmed"
        )
        self.assertEqual(wallet_notice["status"], "sent")
        self.assertIn("کیف پول", wallet_notice["body"])
        for order in discount_orders:
            discount_notice = self.db.get_outbound_message_by_idempotency_key(
                f"order:{order['id']}:discount-confirmed"
            )
            self.assertEqual(discount_notice["status"], "sent")
            self.assertIn("تخفیف کامل", discount_notice["body"])
        self.app._reconcile_paid_fulfillment()
        self.assertEqual(
            self.db.get_order(wallet_order["id"])["status"], "awaiting_info"
        )
        for order in discount_orders:
            self.assertEqual(
                self.db.get_order(order["id"])["status"], "awaiting_info"
            )
        for customer in [wallet_user] + [
            self.db.get_user(int(order["user_id"])) for order in discount_orders
        ]:
            customer_messages = [
                item
                for item in self.telegram.messages
                if customer is not None
                and int(item["chat_id"]) == int(customer["chat_id"])
            ]
            self.assertIn("پرداخت با موفقیت", customer_messages[0]["text"])
            self.assertTrue(
                any("اطلاعات را بفرست" in item["text"] for item in customer_messages[1:])
            )
        before_replay = len(self.telegram.messages)
        self.app._reconcile_zero_external_payment_notices()
        self.assertEqual(len(self.telegram.messages), before_replay)

    def test_admin_home_callback_is_actionable(self) -> None:
        self.send_message(self.OWNER, text="/admin")
        admin_home = self.telegram.messages[-1]
        orders_button = next(
            button
            for row in admin_home["reply_markup"]["inline_keyboard"]
            for button in row
            if button.get("callback_data") == "adm:orders"
        )
        before_output = len(self.telegram.messages) + len(self.telegram.edits)

        self.send_callback(self.OWNER, orders_button["callback_data"])

        after_output = len(self.telegram.messages) + len(self.telegram.edits)
        self.assertGreater(after_output, before_output)
        self.assertNotEqual(
            self.telegram.callback_answers[-1].get("text"),
            "این گزینه دیگر معتبر نیست.",
        )

    def test_zero_target_broadcast_reports_actual_result_once(self) -> None:
        self.send_message(self.OWNER, text="/admin")
        owner_user = self.db.get_user_by_chat_id(self.OWNER["id"])
        owner_admin = next(
            item for item in self.db.list_admins(active_only=True) if item["role"] == "owner"
        )
        self.db.queue_broadcast_batch(
            "broadcast:zero-target",
            actor_admin_id=int(owner_admin["id"]),
            actor_user_id=int(owner_user["id"]),
            recipient_user_ids=[],
            body="متن بدون مخاطب",
        )

        self.app.run_maintenance()
        self.app.run_maintenance()

        reports = [
            item
            for item in self.telegram.messages
            if "گزارش نهایی ارسال گروهی" in item["text"]
        ]
        self.assertEqual(len(reports), 1)
        self.assertIn("تعداد هدف: 0", reports[0]["text"])
        self.assertIn("ارسال موفق: 0", reports[0]["text"])
        self.assertEqual(self.db.list_ready_broadcast_summaries(), [])

    def test_permanent_broadcast_summary_failures_do_not_starve_batch_51(self) -> None:
        self.send_message(self.OWNER, text="/admin")
        owner_user = self.db.get_user_by_chat_id(self.OWNER["id"])
        owner_admin = next(
            item for item in self.db.list_admins(active_only=True) if item["role"] == "owner"
        )
        for index in range(51):
            self.db.queue_broadcast_batch(
                f"summary-starvation-{index:02d}",
                actor_admin_id=int(owner_admin["id"]),
                actor_user_id=int(owner_user["id"]),
                recipient_user_ids=[],
                body="body",
            )

        with patch.object(
            self.telegram,
            "send_message",
            side_effect=TelegramAPIError(
                "sendMessage", "bot was blocked", error_code=403
            ),
        ):
            self.app._report_completed_broadcasts()
            self.app._report_completed_broadcasts()

        self.assertEqual(self.db.list_ready_broadcast_summaries(), [])
        connection = sqlite3.connect(self.db.path)
        try:
            notified = connection.execute(
                "SELECT COUNT(*) FROM broadcast_batches WHERE notified_at IS NOT NULL"
            ).fetchone()[0]
            summary_rows = connection.execute(
                """
                SELECT COUNT(*) FROM outbound_messages
                WHERE idempotency_key LIKE 'broadcast:summary-starvation-%:summary'
                  AND status = 'failed'
                """
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(notified, 51)
        self.assertEqual(summary_rows, 51)

    def test_all_emitted_button_labels_are_plain_text_without_emoji(self) -> None:
        self.submit_card_receipt_after_partial_wallet()
        self.send_message(self.OWNER, text="/admin")
        self.send_message(self.CUSTOMER, text=ACCOUNT)
        self.send_message(self.CUSTOMER, text=WALLET)
        self.send_message(self.CUSTOMER, text=SUPPORT)
        self.send_message(self.CUSTOMER, text=REFERRAL)
        self.send_message(self.CUSTOMER, text=CHANNEL)

        buttons = self.all_buttons()
        self.assertGreater(len(buttons), 20)
        offending = [button["text"] for button in buttons if contains_emoji(button["text"])]
        self.assertEqual(offending, [])

    def test_channel_runtime_fails_closed_for_unsafe_persisted_url(self) -> None:
        user = self.db.upsert_user(
            self.CUSTOMER["id"],
            self.CUSTOMER["id"],
            username=self.CUSTOMER["username"],
        )
        self.db.set_setting("main_channel_url", "https://t.me.evil.example/channel")

        self.app.show_channel(user)

        unavailable = self.telegram.messages[-1]
        self.assertIn("معتبر", unavailable["text"])
        self.assertNotIn("reply_markup", unavailable)

        valid = "https://t.me/alone_account_channel"
        self.db.set_setting("main_channel_url", valid)
        self.app.show_channel(user)
        button = self.telegram.messages[-1]["reply_markup"]["inline_keyboard"][0][0]
        self.assertEqual(button["url"], valid)

    def test_legacy_external_urls_fail_closed_at_render_time(self) -> None:
        user = self.db.upsert_user(
            self.CUSTOMER["id"],
            self.CUSTOMER["id"],
            username=self.CUSTOMER["username"],
        )
        channel = self.db.upsert_force_join_channel(
            "@valid_channel",
            "کانال",
            invite_url="https://t.me/valid_channel",
        )
        payment = self.db.create_wallet_topup_payment(
            user["id"],
            10_000,
            "crypto",
            idempotency_key="legacy-unsafe-invoice",
            provider_invoice_url="https://pay.example.test/invoice/1",
        )
        connection = sqlite3.connect(self.db.path)
        try:
            connection.execute(
                "UPDATE products SET rules_url = ? WHERE id = ?",
                ("javascript:alert(1)", self.product["id"]),
            )
            connection.execute(
                "UPDATE force_join_channels SET invite_url = ? WHERE id = ?",
                ("https://t.me.evil.example/channel", channel["id"]),
            )
            connection.execute(
                "UPDATE payments SET provider_invoice_url = ? WHERE id = ?",
                ("javascript:alert(1)", payment["id"]),
            )
            connection.commit()
        finally:
            connection.close()

        self.app.show_product(user["chat_id"], self.product["id"])
        product_buttons = self.telegram.messages[-1]["reply_markup"]["inline_keyboard"]
        self.assertFalse(any("url" in button for row in product_buttons for button in row))

        self.app._show_join_required(user)
        join_urls = [
            button["url"]
            for row in self.telegram.messages[-1]["reply_markup"]["inline_keyboard"]
            for button in row
            if "url" in button
        ]
        self.assertEqual(join_urls, ["https://t.me/valid_channel"])

        stored_payment = self.db.get_payment(payment["id"])
        self.app._show_crypto_invoice(
            user,
            stored_payment,
            stored_payment["provider_invoice_url"],
            query=None,
        )
        unsafe_invoice = self.telegram.messages[-1]
        self.assertIn("معتبر نیست", unsafe_invoice["text"])
        self.assertFalse(
            any(
                "url" in button
                for row in unsafe_invoice["reply_markup"]["inline_keyboard"]
                for button in row
            )
        )


if __name__ == "__main__":
    unittest.main()
