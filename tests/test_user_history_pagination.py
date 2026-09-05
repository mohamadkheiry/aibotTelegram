from __future__ import annotations

import copy
import re
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from typing import Any

from app.bot import BotApplication
from app.config import Settings
from app.db import Database
from app.utils import utc_now
from tests.test_bot import FakeTelegram


class HistoryFakeTelegram(FakeTelegram):
    def __init__(self) -> None:
        super().__init__()
        self.photos: list[dict[str, Any]] = []

    def send_photo(
        self,
        chat_id: int,
        photo: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        item = {
            "message_id": self._allocate_message_id(),
            "chat_id": int(chat_id),
            "photo": photo,
            **copy.deepcopy(kwargs),
        }
        self.photos.append(item)
        return copy.deepcopy(item)


class UserHistoryPaginationTests(unittest.TestCase):
    """User-history acceptance tests derived from the supplied specification.

    Callback contract used by these tests:

    * ``profile:orders:N`` - zero-based orders page (legacy callback = page 0).
    * ``profile:transactions:N`` - zero-based transaction page.
    * ``tickets:list:N`` - zero-based ticket-list page.
    * ``ticket:TICKET_ID:N`` - zero-based chronological conversation page;
      the callback without ``N`` opens the newest page.
    * ``ticketfile:MESSAGE_ID`` - securely resend one stored attachment.
    """

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.settings = Settings(
            bot_token="history-test-token",
            database_path=root / "bot.sqlite3",
            data_dir=root / "data",
            bootstrap_admin_username="history_owner",
            bootstrap_admin_chat_id=9001,
            job_interval_seconds=3600,
        )
        self.db = Database(self.settings.database_path)
        self.telegram = HistoryFakeTelegram()
        self.app = BotApplication(self.settings, self.db, self.telegram)  # type: ignore[arg-type]
        self.app.initialize()
        self.db.set_setting("completion_notice_pending", False)
        self.user = self.db.upsert_user(
            1001,
            2001,
            username="history_buyer",
            first_name="History Buyer",
        )
        self.user = self.db.update_user_profile(
            self.user["id"], customer_name="History Buyer", phone="09120000000"
        )
        self.other_user = self.db.upsert_user(
            1002,
            2002,
            username="other_buyer",
            first_name="Other Buyer",
        )
        self.category = self.db.create_category("History category")
        self.product = self.db.create_product(
            self.category["id"],
            "History product",
            product_type="manual",
            price_amount=0,
            idempotency_key="history-product",
        )
        self.owner = self.db.list_admins(active_only=True)[0]
        self._update_id = 1

    def tearDown(self) -> None:
        self.temp.cleanup()

    def callback(
        self,
        data: str,
        *,
        user: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        actor = user or self.user
        update_id = self._update_id
        self._update_id += 1
        return {
            "update_id": update_id,
            "callback_query": {
                "id": f"history-callback-{update_id}",
                "from": {
                    "id": int(actor["telegram_user_id"]),
                    "username": actor.get("username"),
                    "first_name": actor.get("first_name") or "User",
                },
                "message": {
                    "message_id": 1,
                    "chat": {"id": int(actor["chat_id"]), "type": "private"},
                },
                "data": data,
            },
        }

    def message(
        self,
        *,
        text: str | None = None,
        photo: list[dict[str, Any]] | None = None,
        document: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        update_id = self._update_id
        self._update_id += 1
        payload: dict[str, Any] = {
            "message_id": update_id,
            "chat": {"id": int(self.user["chat_id"]), "type": "private"},
            "from": {
                "id": int(self.user["telegram_user_id"]),
                "username": self.user.get("username"),
                "first_name": self.user.get("first_name") or "User",
            },
        }
        if text is not None:
            payload["text"] = text
        if photo is not None:
            payload["photo"] = copy.deepcopy(photo)
        if document is not None:
            payload["document"] = copy.deepcopy(document)
        return {"update_id": update_id, "message": payload}

    @staticmethod
    def callbacks(surface: dict[str, Any]) -> list[str]:
        markup = surface.get("reply_markup") or {}
        return [
            str(button["callback_data"])
            for row in markup.get("inline_keyboard", [])
            for button in row
            if button.get("callback_data")
        ]

    @staticmethod
    def entity_ids(callbacks: list[str], prefix: str) -> list[int]:
        result: list[int] = []
        for value in callbacks:
            if not value.startswith(prefix):
                continue
            parts = value.split(":")
            if len(parts) >= 2 and parts[1].isdigit():
                result.append(int(parts[1]))
        return result

    def collect_entity_pages(
        self,
        *,
        first_surface: dict[str, Any],
        prefix: str,
        entity_prefix: str,
    ) -> list[int]:
        """Follow every forward page callback and return visible entity IDs."""

        surface = first_surface
        page = 0
        result: list[int] = []
        while True:
            callbacks = self.callbacks(surface)
            result.extend(self.entity_ids(callbacks, entity_prefix))
            next_callback = f"{prefix}:{page + 1}"
            if next_callback not in callbacks:
                return result
            self.app.process_update(self.callback(next_callback))
            surface = self.telegram.edits[-1]
            page += 1

    def test_orders_expose_total_and_every_page(self) -> None:
        orders = [
            self.db.create_order(
                self.user["id"],
                self.product["id"],
                idempotency_key=f"history-order-{index:02d}",
            )
            for index in range(1, 26)
        ]

        self.app.show_orders(self.user)

        first = self.telegram.messages[-1]
        first_callbacks = self.callbacks(first)
        self.assertIn("25", first["text"])
        self.assertIn("profile:orders:1", first_callbacks)
        visible_ids = self.collect_entity_pages(
            first_surface=first,
            prefix="profile:orders",
            entity_prefix="order:",
        )
        self.assertEqual(len(visible_ids), len(set(visible_ids)))
        self.assertEqual(set(visible_ids), {int(order["id"]) for order in orders})

    def test_transactions_expose_total_and_older_entries(self) -> None:
        base = utc_now() - timedelta(hours=1)
        for index in range(1, 36):
            self.db.credit_wallet(
                self.user["id"],
                1,
                reason=f"transaction-{index:02d}",
                idempotency_key=f"history-transaction-{index:02d}",
                now=base + timedelta(seconds=index),
            )

        self.app.show_transactions(self.user)

        first = self.telegram.messages[-1]
        self.assertIn("35", first["text"])
        self.assertIn("transaction-35", first["text"])
        self.assertNotIn("transaction-01", first["text"])
        self.assertIn("profile:transactions:1", self.callbacks(first))

        surfaces = [first]
        page = 0
        while f"profile:transactions:{page + 1}" in self.callbacks(surfaces[-1]):
            page += 1
            self.app.process_update(self.callback(f"profile:transactions:{page}"))
            surfaces.append(self.telegram.edits[-1])
        combined = "\n".join(surface["text"] for surface in surfaces)
        for index in range(1, 36):
            self.assertEqual(combined.count(f"transaction-{index:02d}"), 1)
        self.assertIn("profile:transactions:0", self.callbacks(surfaces[1]))

    def test_ticket_list_exposes_total_and_every_page(self) -> None:
        base = utc_now() - timedelta(hours=1)
        tickets = [
            self.db.create_ticket(
                self.user["id"],
                f"subject-{index:02d}",
                f"body-{index:02d}",
                idempotency_key=f"history-ticket-{index:02d}",
                now=base + timedelta(seconds=index),
            )
            for index in range(1, 26)
        ]

        self.app.show_tickets(self.user)

        first = self.telegram.messages[-1]
        first_callbacks = self.callbacks(first)
        self.assertIn("25", first["text"])
        self.assertIn("tickets:list:1", first_callbacks)
        visible_ids = self.collect_entity_pages(
            first_surface=first,
            prefix="tickets:list",
            entity_prefix="ticket:",
        )
        self.assertEqual(len(visible_ids), len(set(visible_ids)))
        self.assertEqual(set(visible_ids), {int(ticket["id"]) for ticket in tickets})

    def test_ticket_conversation_pages_from_newest_to_oldest(self) -> None:
        ticket = self.db.create_ticket(
            self.user["id"],
            "long conversation",
            "message-00",
            idempotency_key="long-ticket",
        )
        for index in range(1, 12):
            sender_type = "user" if index % 2 else "admin"
            sender_id = self.user["id"] if sender_type == "user" else self.owner["id"]
            self.db.add_ticket_message(
                ticket["id"],
                f"message-{index:02d}-" + ("x" * 700),
                sender_type=sender_type,
                sender_id=sender_id,
                idempotency_key=f"long-ticket-message-{index:02d}",
            )

        self.app.process_update(self.callback(f"ticket:{ticket['id']}"))

        surface = self.telegram.edits[-1]
        # A long final message may continue on the newest packed page; its
        # numbered continuation header must still identify the latest entry.
        self.assertRegex(surface["text"], r"(?:شما|پشتیبانی) 12 \(2/2\)")
        self.assertNotIn("message-00", surface["text"])
        match = re.search(r"صفحه گفتگو: ([0-9,]+) از ([0-9,]+)", surface["text"])
        self.assertIsNotNone(match)
        assert match is not None
        current_page = int(match.group(1).replace(",", "")) - 1
        self.assertGreater(current_page, 0)

        combined = surface["text"]
        while current_page > 0:
            current_page -= 1
            callback = f"ticket:{ticket['id']}:{current_page}"
            self.assertIn(callback, self.callbacks(surface))
            self.app.process_update(self.callback(callback))
            surface = self.telegram.edits[-1]
            combined += "\n" + surface["text"]

        for index in range(12):
            self.assertEqual(combined.count(f"message-{index:02d}"), 1)

    def test_document_attachment_can_be_reopened_from_conversation(self) -> None:
        ticket = self.db.create_ticket(
            self.user["id"],
            "document ticket",
            "attached document",
            attachment_file_id="telegram-document-file-id",
            idempotency_key="document-ticket",
        )
        ticket_message = self.db.list_ticket_messages(ticket["id"])[0]

        self.app.process_update(self.callback(f"ticket:{ticket['id']}"))

        callback = f"ticketfile:{ticket_message['id']}"
        self.assertIn(callback, self.callbacks(self.telegram.edits[-1]))

        self.app.process_update(self.callback(callback))

        self.assertEqual(self.telegram.documents[-1]["document"], "telegram-document-file-id")

    def test_photo_attachment_kind_is_preserved_and_reopened_as_photo(self) -> None:
        self.db.set_user_state(
            self.user["id"], "ticket_body", {"subject": "photo ticket"}
        )
        self.app.process_update(
            self.message(
                photo=[
                    {"file_id": "small-photo", "file_unique_id": "small"},
                    {"file_id": "large-photo", "file_unique_id": "large"},
                ]
            )
        )
        ticket = self.db.list_tickets(user_id=self.user["id"], limit=1)[0]
        ticket_message = self.db.list_ticket_messages(ticket["id"])[0]

        self.assertEqual(ticket_message.get("attachment_kind"), "photo")
        self.app.process_update(self.callback(f"ticket:{ticket['id']}"))
        callback = f"ticketfile:{ticket_message['id']}"
        self.assertIn(callback, self.callbacks(self.telegram.edits[-1]))

        self.app.process_update(self.callback(callback))

        self.assertEqual(self.telegram.photos[-1]["photo"], "large-photo")

    def test_ticket_attachment_callback_enforces_ticket_ownership(self) -> None:
        ticket = self.db.create_ticket(
            self.user["id"],
            "private document",
            "private attachment",
            attachment_file_id="private-file-id",
            idempotency_key="private-ticket",
        )
        ticket_message = self.db.list_ticket_messages(ticket["id"])[0]
        callback = f"ticketfile:{ticket_message['id']}"

        self.app.process_update(self.callback(callback, user=self.other_user))

        self.assertEqual(self.telegram.documents, [])
        answer = self.telegram.callback_answers[-1]
        self.assertTrue(answer.get("show_alert"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
