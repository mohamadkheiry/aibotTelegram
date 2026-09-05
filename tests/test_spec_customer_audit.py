from __future__ import annotations

import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from app.bot import BotApplication
from app.config import Settings
from app.db import Database
from app.utils import utc_now
from tests.test_bot import FakeTelegram


class CustomerSourceSpecificationTests(unittest.TestCase):
    """Acceptance regressions against the original product/referral requirements."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        settings = Settings(
            bot_token="customer-spec-test-token",
            database_path=root / "test.sqlite3",
            data_dir=root / "data",
            bootstrap_admin_username="customer_spec_owner",
            bootstrap_admin_chat_id=9001,
            job_interval_seconds=3600,
        )
        self.db = Database(settings.database_path)
        self.telegram = FakeTelegram()
        self.app = BotApplication(settings, self.db, self.telegram)  # type: ignore[arg-type]
        self.app.initialize()
        self.user = self.db.upsert_user(1001, 1001, first_name="Customer")
        self.category = self.db.create_category("اشتراک‌ها")
        self.product = self.db.create_product(
            self.category["id"],
            "محصول <آزمایشی>",
            product_type="manual",
            price_amount=150_000,
            short_description="html:<b>نکته اصلی اشتراک</b>",
            long_description="شرح اختصاصی تکمیلی",
        )
        self.telegram.messages.clear()
        self.telegram.edits.clear()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def rendered(self) -> str:
        return "\n".join(
            str(item["text"])
            for item in [*self.telegram.edits, *self.telegram.messages]
            if item["chat_id"] == self.user["chat_id"]
        )

    def test_product_page_displays_primary_description_before_details(self) -> None:
        self.app.show_product(self.user["chat_id"], self.product["id"])
        rendered = self.rendered()
        self.assertIn("<b>نکته اصلی اشتراک</b>", rendered)
        self.assertNotIn("شرح اختصاصی تکمیلی", rendered)
        self.assertIn("محصول &lt;آزمایشی&gt;", rendered)

    def test_full_product_details_are_sent_without_oversized_messages(self) -> None:
        description = "FULL-DESCRIPTION-" * 550
        self.db.update_product(self.product["id"], long_description=description)
        self.app.show_product_details(self.user["chat_id"], self.product["id"])
        self.assertTrue(all(len(item["text"]) <= 3900 for item in self.telegram.messages))
        self.assertEqual(
            "".join(item["text"] for item in self.telegram.messages).count("FULL-DESCRIPTION-"),
            550,
        )
        self.assertIn(
            f"buy:{self.product['id']}",
            str(self.telegram.messages[-1].get("reply_markup")),
        )

    def test_long_primary_description_preserves_all_text_and_final_actions_on_callback(self) -> None:
        description = "PRIMARY-CONTENT-" * 500
        self.db.update_product(self.product["id"], short_description=description)
        self.app.show_product(
            self.user["chat_id"], self.product["id"],
            query={"message": {"message_id": 1, "chat": {"id": self.user["chat_id"]}}},
        )
        surfaces = [*self.telegram.edits, *self.telegram.messages]
        self.assertGreater(len(surfaces), 1)
        self.assertTrue(all(len(item["text"]) <= 3900 for item in surfaces))
        self.assertEqual("".join(item["text"] for item in surfaces).count("PRIMARY-CONTENT-"), 500)
        self.assertIn(f"buy:{self.product['id']}", str(surfaces[-1].get("reply_markup")))
        self.assertNotIn("شرح اختصاصی تکمیلی", self.rendered())

    def test_referral_explains_active_reward_amounts_scope_conditions_and_window(self) -> None:
        now = utc_now()
        self.db.create_reward_rule("start-reward", event_type="start", amount=1700)
        self.db.create_reward_rule(
            "first-reward", event_type="first_purchase", amount=2300
        )
        self.db.create_reward_rule(
            "product-reward", event_type="product_purchase", amount=3100,
            product_id=self.product["id"],
            starts_at=now - timedelta(days=1), ends_at=now + timedelta(days=2),
        )
        self.db.create_reward_rule(
            "combined-reward", event_type="combined", amount=4300,
            conditions={
                "minimum_successful_purchases": 2,
                "minimum_referrals": 3,
                "minimum_qualified_referrals": 1,
                "minimum_order_amount": 125_000,
                "product_ids": [self.product["id"]],
            },
        )
        self.db.create_reward_rule(
            "inactive-reward", event_type="start", amount=98765, active=False
        )
        self.db.create_reward_rule(
            "expired-reward", event_type="start", amount=87654,
            ends_at=now - timedelta(days=1),
        )
        self.db.create_reward_rule(
            "future-reward", event_type="start", amount=76543,
            starts_at=now + timedelta(days=1),
        )
        self.app.show_referral(self.user)
        rendered = self.rendered()
        for expected in (
            "1,700 تومان", "2,300 تومان", "3,100 تومان", "4,300 تومان",
            "اولین خرید", "محصول &lt;آزمایشی&gt;", "125,000 تومان",
            "حداقل 2 خرید", "حداقل 3 دعوت", "حداقل 1 دعوت واجد پاداش",
            (now + timedelta(days=2)).strftime("%Y-%m-%d"),
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, rendered)
        for excluded in ("98,765 تومان", "87,654 تومان", "76,543 تومان"):
            self.assertNotIn(excluded, rendered)
        self.assertIn("ارسال لینک به دوستان", str(self.telegram.messages[-1]))

    def test_referral_without_current_rule_does_not_promise_a_reward(self) -> None:
        self.app.show_referral(self.user)
        self.assertIn("در حال حاضر پاداش فعالی تعریف نشده", self.rendered())

    def test_faq_callback_preserves_the_complete_formatted_answer_and_back_action(self) -> None:
        category = self.db.create_faq_category("راهنمای کاربر")
        answer = "html:<b>" + "شرح " * 1200 + "FAQ-END-MARKER</b>"
        faq = self.db.create_faq("چگونه استفاده کنم؟", answer, category_id=category["id"])
        self.app.show_faq(
            self.user, faq["id"],
            query={"message": {"message_id": 1, "chat": {"id": self.user["chat_id"]}}},
        )
        surfaces = [*self.telegram.edits, *self.telegram.messages]
        self.assertIn("FAQ-END-MARKER", self.rendered())
        self.assertEqual(self.rendered().count("شرح"), 1200)
        self.assertTrue(all(len(item["text"]) <= 3900 for item in surfaces))
        self.assertIn(f"faqcat:{category['id']}", str(surfaces[-1].get("reply_markup")))
        self.assertTrue(all(item["text"].count("<b>") == item["text"].count("</b>") for item in surfaces))

    def test_referral_explanation_reaches_rules_beyond_default_repository_limit(self) -> None:
        for index in range(205):
            self.db.create_reward_rule(
                f"many-rules-{index}", event_type="first_purchase", amount=1000 + index
            )
        self.app.show_referral(self.user)
        surfaces = self.telegram.messages
        self.assertTrue(all(len(item["text"]) <= 3900 for item in surfaces))
        self.assertGreater(len(surfaces), 1)
        for index in range(205):
            self.assertIn(f"{1000 + index:,} تومان", self.rendered())
        self.assertIn("ارسال لینک به دوستان", str(surfaces[-1].get("reply_markup")))


if __name__ == "__main__":
    unittest.main()
