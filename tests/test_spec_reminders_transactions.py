from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from app.bot import BotApplication
from app.config import Settings
from app.db import Database, ValidationError
from app.utils import parse_iso
from tests.test_bot import FakeTelegram


BASE_TIME = datetime(2026, 1, 10, 12, tzinfo=timezone.utc)


class ReminderAndTransactionSpecificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.db = Database(root / "spec.sqlite3")
        self.db.initialize()
        self.settings = Settings(
            bot_token="specification-test-token",
            database_path=self.db.path,
            data_dir=root / "data",
        )
        self.telegram = FakeTelegram()
        self.app = BotApplication(self.settings, self.db, self.telegram)
        self.user = self.db.upsert_user(75001, 75001, first_name="Spec customer")
        self.category = self.db.create_category("Spec reminders")

    def completed_order(self, *, days: tuple[int, ...] = (1, 0)) -> dict:
        product = self.db.create_product(
            self.category["id"], "اشتراک آزمایشی", product_type="manual",
            price_amount=0, duration_days=2, reminder_days=days,
            idempotency_key="spec-reminder-product", now=BASE_TIME,
        )
        order = self.db.create_order(
            self.user["id"], product["id"], idempotency_key="spec-reminder-order",
            now=BASE_TIME,
        )
        self.db.update_order_status(order["id"], "awaiting_info", now=BASE_TIME)
        self.db.submit_manual_order_info(
            order["id"], self.user["id"], {"text": "synthetic activation details"},
            now=BASE_TIME,
        )
        return self.db.complete_order(order["id"], "synthetic delivery", now=BASE_TIME)

    def test_same_day_reminder_is_scheduled_before_expiry_and_replay_is_unique(self) -> None:
        """PDF 1 page 2 explicitly asks for a reminder on the expiry day."""
        order = self.completed_order()
        reminders = self.db.schedule_order_reminders(order["id"], now=BASE_TIME)
        by_day = {row["days_before"]: row for row in reminders}
        self.assertEqual(set(by_day), {0, 1})
        self.assertEqual(
            parse_iso(by_day[0]["remind_at"]),
            datetime(2026, 1, 11, 20, 30, tzinfo=timezone.utc),
        )
        self.assertLess(parse_iso(by_day[0]["remind_at"]), parse_iso(order["subscription_ends_at"]))
        again = self.db.schedule_order_reminders(order["id"], now=BASE_TIME)
        self.assertEqual([row["id"] for row in reminders], [row["id"] for row in again])
        for invalid in ((-1,), (0.5,), (True,)):
            with self.subTest(invalid=invalid), self.assertRaises(ValidationError):
                self.db.update_product(order["product_id"], reminder_days=invalid)

    def test_same_day_reminder_can_be_added_during_the_day_but_not_after_expiry(self) -> None:
        order = self.completed_order(days=())
        during_day = BASE_TIME + timedelta(days=2, hours=-2)
        reminders = self.db.schedule_order_reminders(
            order["id"], days_before=(0,), now=during_day,
        )
        self.assertEqual(len(reminders), 1)
        self.assertEqual(parse_iso(reminders[0]["remind_at"]), during_day)
        self.assertEqual(
            self.db.schedule_order_reminders(
                order["id"], days_before=(0,), now=BASE_TIME + timedelta(days=2),
            ), [],
        )

    def test_same_day_delivery_says_today_and_shows_the_expiry_time(self) -> None:
        order = self.completed_order(days=(0,))
        due_time = datetime(2026, 1, 12, 1, tzinfo=timezone.utc)
        claim = self.db.claim_due_reminders
        with (
            patch.object(self.db, "claim_due_reminders", side_effect=lambda **kw: claim(now=due_time, **kw)),
            patch("app.bot.utc_now", return_value=due_time),
        ):
            self.app._deliver_due_reminders()
        messages = [m["text"] for m in self.telegram.messages if m["chat_id"] == self.user["chat_id"]]
        self.assertEqual(len(messages), 1)
        self.assertIn("امروز", messages[0])
        self.assertIn("15:30", messages[0])
        self.assertNotIn("تا 1 روز دیگر", messages[0])
        self.assertEqual(order["status"], "completed")

    def test_reminder_scheduling_uses_the_configured_timezone(self) -> None:
        order = self.completed_order(days=())
        alternative = Database(self.db.path, reminder_timezone="America/New_York")
        reminders = alternative.schedule_order_reminders(
            order["id"], days_before=(0,), now=BASE_TIME,
        )
        self.assertEqual(
            parse_iso(reminders[0]["remind_at"]),
            datetime(2026, 1, 12, 5, tzinfo=timezone.utc),
        )

    def test_queued_reminder_is_cancelled_if_retry_occurs_after_expiry(self) -> None:
        order = self.completed_order(days=(0,))
        reminder = self.db.schedule_order_reminders(order["id"], now=BASE_TIME)[0]
        outbound = self.db.queue_outbound_message(
            "Reminder queued before the simulated outage", recipient_user_id=self.user["id"],
            idempotency_key=f"reminder:{reminder['id']}", now=BASE_TIME,
        )
        with patch("app.bot.utc_now", return_value=BASE_TIME + timedelta(days=3)):
            self.app._deliver_outbound_messages()
        self.assertEqual(self.telegram.messages, [])
        stored = self.db.get_outbound_message_by_idempotency_key(outbound["idempotency_key"])
        self.assertEqual(stored["status"], "cancelled")
        self.assertEqual(self.db.get_reminder(reminder["id"])["status"], "cancelled")

    def test_transaction_type_is_visible_even_when_a_custom_reason_exists(self) -> None:
        """PDF 1 page 1 requires date, amount and type for every transaction."""
        for index, kind in enumerate(("admin_adjustment", "referral_reward", "topup", "manual_credit")):
            self.db.adjust_wallet(
                self.user["id"], 500, entry_type=kind, reason="یادداشت اختصاصی",
                idempotency_key=f"spec-transaction:{index}", now=BASE_TIME,
            )
        self.app.show_transactions(self.user)
        text = self.telegram.messages[-1]["text"]
        for label in ("اصلاح موجودی توسط مدیر", "پاداش دعوت", "شارژ کیف پول", "افزایش اعتبار"):
            self.assertIn(label, text)
        self.assertEqual(text.count("یادداشت اختصاصی"), 4)
        self.assertEqual(text.count("2026-01-10"), 4)
        self.assertLessEqual(len(text), 4096)


if __name__ == "__main__":
    unittest.main()
