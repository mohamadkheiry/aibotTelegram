from __future__ import annotations

import json
import math
import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from app.admin import AdminController
from app.db import Database
from app.utils import escape


class SpecAdminAuditTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Database(Path(self.temp.name) / "audit.sqlite3")
        self.db.initialize()
        self.user = self.db.upsert_user(1001, 1001, username="owner", first_name="Owner")
        self.admin = self.db.bootstrap_admin("owner", 1001, role="owner")
        self.messages: list[str] = []
        self.telegram = SimpleNamespace(send_message=self.send_message)
        self.controller = AdminController(
            self.db, self.telegram, SimpleNamespace(currency_label="تومان")
        )

    def send_message(self, chat_id: int, text: str, **kwargs: Any) -> dict[str, Any]:
        self.assertLessEqual(len(text), 3900)
        self.messages.append(text)
        return {"message_id": len(self.messages)}

    def handle(self, command: str) -> str:
        self.messages.clear()
        self.assertTrue(self.controller.handle(
            {"message_id": 1, "chat": {"id": 1001, "type": "private"}, "text": command},
            self.user, self.admin,
        ))
        return "\n".join(self.messages)

    def test_all_management_lists_expose_records_beyond_previous_caps(self) -> None:
        """PDF 1 pp2-3 requires lists usable to manage every catalog/stock/code row."""
        records: dict[str, list[int | str]] = {
            "/categories": [], "/products all": [], "/discounts": [],
            "/rewards": [], "/faq_categories": [], "/faqs all": [],
            "/admins": [1001],
        }
        inventory_ids: list[int | str] = []
        stock_product = None
        for index in range(205):
            category = self.db.create_category(f"category-{index:03d}")
            records["/categories"].append(category["id"])
            product = self.db.create_product(
                category["id"], f"product-{index:03d}", product_type="ready", price_amount=100
            )
            records["/products all"].append(product["id"])
            if stock_product is None:
                stock_product = product
            item = self.db.add_inventory_item(stock_product["id"], f"test-only-stock-{index}")
            inventory_ids.append(item["id"])
            discount = self.db.create_discount(f"AUDIT{index:03d}", discount_type="fixed", value=10)
            records["/discounts"].append(discount["code"])
            rule = self.db.create_reward_rule(f"audit-{index}", event_type="start", amount=10)
            records["/rewards"].append(rule["id"])
            faq_category = self.db.create_faq_category(f"faq-category-{index:03d}")
            records["/faq_categories"].append(faq_category["id"])
            faq = self.db.create_faq(f"question-{index:03d}", "answer", category_id=faq_category["id"])
            records["/faqs all"].append(faq["id"])
            if index < 105:
                chat_id = 10_000 + index
                self.db.add_admin(f"staff_{index:03d}", chat_id, role="admin")
                records["/admins"].append(chat_id)
        assert stock_product is not None
        records[f"/inventory_list {stock_product['id']}"] = inventory_ids

        for command, identifiers in records.items():
            with self.subTest(command=command):
                seen = []
                pages = math.ceil(len(identifiers) / 20)
                for page in range(1, pages + 1):
                    rendered = self.handle(f"{command} {page}")
                    self.assertIn(f"مجموع: {len(identifiers):,}", rendered)
                    seen.extend(re.findall(r"<code>([^<]+)</code> \|", rendered))
                    if page < pages:
                        self.assertIn(f"{command} {page + 1}", rendered)
                    if page > 1:
                        self.assertIn(f"{command} {page - 1}", rendered)
                self.assertCountEqual(seen, [str(value) for value in identifiers])
                self.assertIn("خارج از بازه", self.handle(f"{command} {pages + 1}"))
                self.assertIn("حداقل", self.handle(f"{command} 0"))

    def test_manual_order_details_preserve_complete_customer_text(self) -> None:
        """The manual activation workflow must not hide the end of customer input."""
        category = self.db.create_category("manual")
        product = self.db.create_product(category["id"], "manual", product_type="manual", price_amount=100)
        order = self.db.create_order(self.user["id"], product["id"], idempotency_key="audit-info")
        customer_text = "first-line\n" + ("<&> \"customer\" " * 280) + "\nLAST-LINE-MUST-BE-VISIBLE"
        self.db.set_order_customer_info(order["id"], {"text": customer_text})
        for command in (
            f"/order {order['order_number']}",
            f"/user_orders 1001 {order['order_number']}",
        ):
            with self.subTest(command=command):
                rendered = self.handle(command)
                self.assertIn("LAST-LINE-MUST-BE-VISIBLE", rendered)
                self.assertNotIn("<customer>", rendered)
                self.assertEqual(rendered.count("&amp;"), customer_text.count("&"))
                self.assertIn(escape("first-line"), rendered)

    def test_same_day_reminder_is_accepted_but_negative_is_rejected(self) -> None:
        category = self.db.create_category("reminder")
        product = self.db.create_product(category["id"], "manual", product_type="manual", price_amount=100)
        self.handle(f"/product_set {product['id']} | reminder_days | 7,3,1,0")
        self.assertEqual(json.loads(self.db.get_product(product["id"])["reminder_days_json"]), [7, 3, 1, 0])
        self.assertIn("منفی", self.handle(f"/product_set {product['id']} | reminder_days | 7,-1"))
        self.assertEqual(json.loads(self.db.get_product(product["id"])["reminder_days_json"]), [7, 3, 1, 0])

    def test_ticket_detail_preserves_the_end_of_long_escaped_messages(self) -> None:
        body = "&" * 3800 + "TICKET-MESSAGE-TAIL"
        ticket = self.db.create_ticket(
            self.user["id"], "long conversation", body, idempotency_key="audit-ticket"
        )
        rendered = self.handle(f"/ticket {ticket['ticket_number']}")
        self.assertIn("TICKET-MESSAGE-TAIL", rendered)
        self.assertEqual(rendered.count("&amp;"), 3800)

    def test_admin_transaction_views_show_type_and_reason_separately(self) -> None:
        self.db.adjust_wallet(
            self.user["id"], 100, reason="audit credit reason",
            idempotency_key="audit-adjustment", actor_admin_id=self.admin["id"],
        )
        for command in ("/user 1001", "/user_transactions 1001"):
            with self.subTest(command=command):
                rendered = self.handle(command)
                self.assertIn("نوع: اصلاح موجودی توسط مدیر", rendered)
                self.assertIn("دلیل: audit credit reason", rendered)
