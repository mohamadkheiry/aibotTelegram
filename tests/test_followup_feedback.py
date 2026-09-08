"""Synthetic regressions for the six safe September follow-up changes.

Public order numbers are not database IDs. Multipart input is explicitly sealed;
no completion, lost message, phantom attachment or duplicate notification is OK.
"""
from __future__ import annotations

import copy
import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from app import customer_layouts, order_information, texts
from app.admin_forms import ACTIONS
from app.bot import BotApplication
from app.db import ConflictError, Database, ValidationError
from tests import test_admin_catalog_hierarchy as catalog_fixture
from tests import test_db as db_fixture
from tests import test_spec_end_to_end as journey_fixture


class FollowupDomainTests(db_fixture.DatabaseTestCase):
    def manual(self):
        user = self.user()
        product = self.product(price=0, product_type="manual", reserve_enabled=False)
        order = self.db.create_order(user["id"], product["id"])
        self.db.update_order_status(order["id"], "awaiting_info")
        self.db.begin_manual_information(order["id"], user["id"], "a" * 12)
        return user, order

    def append(self, user, order, message_id=1, **payload):
        return self.db.append_manual_information(order["id"], user["id"], "a" * 12,
                                               {"telegram_message_id": message_id, **payload})

    def finish(self, user, order):
        return self.db.finish_manual_information(order["id"], user["id"], "a" * 12,
                                                 outbound_body="Synthetic acknowledgement", reply_markup={})

    def test_sequence_starts_1000_restarts_and_does_not_consume_on_replay(self):
        user, product = self.user(), self.product()
        first = self.db.create_order(user["id"], product["id"], idempotency_key="first")
        self.assertEqual(first["order_number"], "1000")
        self.assertEqual(self.db.create_order(user["id"], product["id"], idempotency_key="first")["id"], first["id"])
        self.db = Database(self.root / "bot.sqlite3")
        self.db.initialize()
        second = self.db.create_order(user["id"], product["id"])
        self.assertEqual(second["order_number"], "1001")
        self.assertEqual(self.db.get_order("1000")["id"], first["id"])
        self.assertIsNone(self.db.get_order(str(first["id"])))
        self.assertEqual(self.db.get_order(first["id"])["order_number"], "1000")

    def test_sequence_is_unique_under_concurrent_creates_and_replays(self):
        user, product = self.user(), self.product()
        def create(index):
            return self.db.create_order(user["id"], product["id"], idempotency_key=f"parallel-{index % 8}")
        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(create, range(16)))
        self.assertEqual({r["order_number"] for r in results}, {str(n) for n in range(1000, 1008)})
        self.assertEqual(self.db.count_orders(), 8)

    def test_legacy_codes_are_unchanged_and_imported_numeric_high_water_is_respected(self):
        user, product = self.user(), self.product()
        first = self.db.create_order(user["id"], product["id"])
        with self.db._transaction() as connection:
            connection.execute("UPDATE orders SET order_number='ORD-LEGACY-TEST' WHERE id=?", (first["id"],))
            connection.execute("DELETE FROM settings WHERE key='order_number_sequence'")
        second = self.db.create_order(user["id"], product["id"])
        self.assertEqual(second["order_number"], "1000")
        self.assertEqual(self.db.get_order("ORD-LEGACY-TEST")["id"], first["id"])
        with self.db._transaction() as connection:
            connection.execute("UPDATE orders SET order_number='2500' WHERE id=?", (second["id"],))
        self.assertEqual(self.db.create_order(user["id"], product["id"])["order_number"], "2501")

    def test_failed_order_transaction_rolls_back_number_and_notice(self):
        user, product = self.user(), self.product()
        def broken_notice(_order):
            raise ValidationError("Synthetic crash before notice")
        with self.assertRaises(ValidationError):
            self.db.create_order(user["id"], product["id"], order_notice=broken_notice)
        self.assertEqual(self.db.count_orders(), 0)
        self.assertIsNone(self.db.get_setting("order_number_sequence"))
        self.assertEqual(self.db.create_order(user["id"], product["id"])["order_number"], "1000")

    def test_corrupt_sequence_fails_closed(self):
        user, product = self.user(), self.product()
        for value in (True, "1000", 2**63, 12):
            with self.subTest(value=value):
                self.db.set_setting("order_number_sequence", value)
                with self.assertRaises(ValidationError):
                    self.db.create_order(user["id"], product["id"])
        self.assertEqual(self.db.count_orders(), 0)

    def test_multipart_preserves_all_parts_and_file_types_without_duplicate_messages(self):
        user, order = self.manual()
        first = self.append(user, order, text="first <private> text", file_kind="document")
        self.assertIsNone(json.loads(first["customer_info_json"])["file_kind"])
        self.append(user, order, message_id=2, text="second text")
        self.append(user, order, message_id=3, file_id="synthetic-photo", file_kind="photo")
        result = self.append(user, order, message_id=4, file_id="synthetic-document", file_kind="document")
        replay = self.append(user, order, message_id=4, file_id="synthetic-document", file_kind="document")
        self.assertEqual(result, replay)
        info = order_information.decode(result["customer_info_json"])
        self.assertEqual(info["text"], "first <private> text\n\nsecond text")
        self.assertEqual(len(info["messages"]), 4)
        self.assertEqual([i["file_kind"] for i in order_information.attachments(info)], ["photo", "document"])
        self.assertEqual(result["status"], "awaiting_info")
        with self.assertRaises(ConflictError):
            self.append(user, order, message_id=4, file_id="changed", file_kind="document")

    def test_finish_is_atomic_idempotent_and_does_not_erase_another_conversation(self):
        user, order = self.manual()
        with self.assertRaises(ValidationError):
            self.finish(user, order)
        self.append(user, order, text="Synthetic info")
        self.db.set_user_state(user["id"], "order_information", {"order_id": order["id"], "collection_id": "a" * 12})
        with patch.object(self.db, "_queue_user_message_in_transaction", side_effect=ValidationError("Synthetic outbox failure")):
            with self.assertRaises(ValidationError):
                self.finish(user, order)
        self.assertEqual(self.db.get_order(order["id"])["status"], "awaiting_info")
        self.assertIsNotNone(self.db.get_user_state(user["id"]))
        finished = self.finish(user, order)
        self.assertEqual(finished["status"], "processing")
        self.assertFalse(json.loads(finished["customer_info_json"])["collecting"])
        self.assertIsNone(self.db.get_user_state(user["id"]))
        key = f"order:{order['id']}:info-received:{'a' * 12}"
        notice = self.db.get_outbound_message_by_idempotency_key(key)
        self.db.set_user_state(user["id"], "ticket_body", {"test": True})
        self.assertEqual(self.finish(user, order), finished)
        self.assertEqual(self.db.get_outbound_message_by_idempotency_key(key)["id"], notice["id"])
        self.assertEqual(self.db.get_user_state(user["id"])["state"], "ticket_body")

    def test_reopen_retains_legacy_input_and_stale_finish_cannot_seal_new_collection(self):
        user, order = self.manual()
        self.append(user, order, text="original")
        self.finish(user, order)
        self.db.begin_manual_information(order["id"], user["id"], "b" * 12)
        with self.assertRaises(ConflictError):
            self.finish(user, order)
        with self.assertRaises(ValidationError):
            self.db.complete_order(order["id"], "Cannot complete collecting input")
        self.assertEqual(json.loads(self.db.get_order(order["id"])["customer_info_json"])["text"], "original")

    def test_concurrent_appends_preserve_every_message_once(self):
        user, order = self.manual()
        def append(index):
            self.append(user, order, message_id=index % 8 + 1, text=f"part-{index % 8}")
        with ThreadPoolExecutor(max_workers=8) as executor:
            list(executor.map(append, range(16)))
        info = json.loads(self.db.get_order(order["id"])["customer_info_json"])
        self.assertEqual(len(info["messages"]), 8)
        self.assertEqual({m["text"] for m in info["messages"]}, {f"part-{n}" for n in range(8)})

    def test_owner_type_status_and_bad_message_guards(self):
        user, order = self.manual()
        stranger = self.user(2)
        with self.assertRaises(ValidationError):
            self.db.begin_manual_information(order["id"], stranger["id"], "b" * 12)
        with self.assertRaises(ValidationError):
            self.append(user, order, text="", file_id=None)
        with self.assertRaises(ValidationError):
            self.append(user, order, message_id=0, text="bad ID")
        with self.assertRaises(ValidationError):
            self.append(user, order, file_id="synthetic", file_kind="voice")
        self.append(user, order, text="valid")
        with self.assertRaises(ConflictError):
            self.db.submit_manual_order_info(order["id"], user["id"], {"text": "overwrite attempt"})
        self.finish(user, order)
        self.db.complete_order(order["id"], "Synthetic final")
        with self.assertRaises(ValidationError):
            self.db.begin_manual_information(order["id"], user["id"], "b" * 12)
        ready = self.product(sku="ready")
        ready_order = self.db.create_order(user["id"], ready["id"])
        with self.assertRaises(ValidationError):
            self.db.begin_manual_information(ready_order["id"], user["id"], "c" * 12)

    def test_legacy_single_message_is_preserved_when_collection_begins(self):
        user, order = self.manual()
        with self.db._transaction() as connection:
            connection.execute("UPDATE orders SET customer_info_json=? WHERE id=?", (
                json.dumps({"text": "legacy", "file_id": "legacy-file", "file_kind": "document"}), order["id"]))
        result = self.db.begin_manual_information(order["id"], user["id"], "b" * 12)
        info = order_information.decode(result["customer_info_json"])
        self.assertEqual(info["text"], "legacy")
        self.assertEqual(order_information.attachments(info)[0]["file_id"], "legacy-file")

    def test_manual_header_escapes_product_preserves_rich_body_and_legacy_number(self):
        body = '<b>Ready</b> <a href="https://example.test">Guide</a>'
        rendered = texts.manual_delivery({"order_number": "ORD-LEGACY", "product_name_snapshot": "A & B", "product_icon_snapshot": "🔑"}, body)
        self.assertEqual(rendered, '✅ <b>سفارشت آماده است</b>\n\n🧾 شماره سفارش: <code>ORD-LEGACY</code>\n🔑 A &amp; B\n\n' + body)

    def test_old_saved_information_layout_gains_finish_before_cancel(self):
        old = {"rows": [["cancel"]], "columns": 1, "item_order": [], "reverse": False}
        updated = customer_layouts.upgrade_saved_layout("input_order_info", old)
        self.assertEqual(updated["rows"], [["finish"], ["cancel"]])
        self.assertEqual(old["rows"], [["cancel"]])
        customer_layouts.validate("input_order_info", updated)


class FollowupJourneyTests(unittest.TestCase):
    OWNER = journey_fixture.SourceEndToEndTests.OWNER
    CUSTOMER = journey_fixture.SourceEndToEndTests.CUSTOMER
    setUp = journey_fixture.SourceEndToEndTests.setUp
    tearDown = journey_fixture.SourceEndToEndTests.tearDown
    message = journey_fixture.SourceEndToEndTests.message
    callback = journey_fixture.SourceEndToEndTests.callback
    _take_update_id = journey_fixture.SourceEndToEndTests._take_update_id
    send = journey_fixture.SourceEndToEndTests.send
    screen = journey_fixture.SourceEndToEndTests.screen
    buttons = journey_fixture.SourceEndToEndTests.buttons
    click = journey_fixture.SourceEndToEndTests.click
    customer = journey_fixture.SourceEndToEndTests.customer
    state = journey_fixture.SourceEndToEndTests.state
    panel = journey_fixture.SourceEndToEndTests.panel
    fill_current = journey_fixture.SourceEndToEndTests.fill_current
    action = journey_fixture.SourceEndToEndTests.action

    def manual(self):
        user = self.customer()
        product = self.db.create_product(self.category["id"], "Synthetic manual", product_type="manual", price_amount=0, icon="🔑")
        order = self.db.create_order(user["id"], product["id"])
        self.db.update_order_status(order["id"], "awaiting_info")
        self.app.show_order(user, order["id"])
        self.click(self.CUSTOMER, "ارسال اطلاعات")
        return user, order

    def test_two_texts_and_two_files_survive_restart_finish_replay_and_admin_retrieval(self):
        user, order = self.manual()
        first = self.send(self.CUSTOMER, text="first@example.test")
        self.app.process_update_safe(copy.deepcopy(first))
        self.send(self.CUSTOMER, text="<private> second")
        self.send(self.CUSTOMER, photo=[{"file_id": "synthetic-photo"}])
        self.app = BotApplication(self.settings, self.db, self.telegram)
        self.app.initialize()
        self.send(self.CUSTOMER, document={"file_id": "synthetic-file"})
        self.assertEqual(self.db.get_order(order["id"])["status"], "awaiting_info")
        self.assertEqual(len(json.loads(self.db.get_order(order["id"])["customer_info_json"])["messages"]), 4)
        self.app._reconcile_manual_order_info_alerts()
        self.assertFalse(any("اطلاعات سفارش دستی دریافت شد" in m["text"] for m in self.telegram.messages))
        finished = self.click(self.CUSTOMER, "پایان ارسال اطلاعات")
        self.app.process_update_safe(copy.deepcopy(finished))
        self.app._reconcile_manual_order_info_alerts()
        alerts = [m for m in self.telegram.messages if m["chat_id"] == self.OWNER["id"] and "اطلاعات سفارش دستی دریافت شد" in m["text"]]
        self.assertEqual(len(alerts), 1)
        self.assertIn("&lt;private&gt;", alerts[0]["text"])
        self.click(self.OWNER, "دریافت پیوست‌ها (2)", screen=alerts[0])
        self.assertEqual(self.telegram.photos[-1]["photo"], "synthetic-photo")
        self.assertEqual(self.telegram.documents[-1]["document"], "synthetic-file")
        self.assertTrue(self.telegram.documents[-1]["protect_content"])
        self.assertEqual(self.db.get_order(order["id"])["status"], "processing")
        self.assertIsNone(self.db.get_user_state(user["id"]))

    def test_text_only_has_no_attachment_action_and_full_text_is_viewable(self):
        _user, order = self.manual()
        self.send(self.CUSTOMER, text="first " + "<" * 800)
        self.send(self.CUSTOMER, text="last-marker@example.test")
        self.click(self.CUSTOMER, "پایان ارسال اطلاعات")
        alert = copy.deepcopy(self.screen(self.OWNER))
        self.assertIn("ندارد؛ اطلاعات متنی است", alert["text"])
        self.assertFalse(any("attachment" in b.get("callback_data", "") for b in self.buttons(self.OWNER)))
        start = len(self.telegram.messages)
        self.click(self.OWNER, "مشاهده اطلاعات کامل سفارش", screen=alert)
        self.assertTrue(any("last-marker@example.test" in m["text"] for m in self.telegram.messages[start:]))
        self.assertFalse(any("attachment" in b.get("callback_data", "") for b in self.buttons(self.OWNER)))
        self.assertEqual(self.state()["values"]["target"], order["order_number"])

    def test_return_and_resume_does_not_lose_input_and_empty_finish_is_rejected(self):
        user, order = self.manual()
        self.click(self.CUSTOMER, "پایان ارسال اطلاعات")
        self.assertEqual(self.db.get_order(order["id"])["status"], "awaiting_info")
        self.send(self.CUSTOMER, text="saved-before-return")
        collection = json.loads(self.db.get_order(order["id"])["customer_info_json"])["collection_id"]
        self.click(self.CUSTOMER, "لغو و بازگشت")
        self.app.show_order(user, order["id"])
        self.click(self.CUSTOMER, "ارسال اطلاعات")
        self.assertEqual(json.loads(self.db.get_order(order["id"])["customer_info_json"])["collection_id"], collection)
        self.send(self.CUSTOMER, text="saved-after-return")
        self.click(self.CUSTOMER, "پایان ارسال اطلاعات")
        self.assertEqual(json.loads(self.db.get_order(order["id"])["customer_info_json"])["text"], "saved-before-return\n\nsaved-after-return")

    def test_foreign_user_cannot_finish_collection(self):
        _user, order = self.manual()
        self.send(self.CUSTOMER, text="private")
        screen = copy.deepcopy(self.screen(self.CUSTOMER))
        self.click(self.OWNER, "پایان ارسال اطلاعات", screen=screen)
        self.assertEqual(self.db.get_order(order["id"])["status"], "awaiting_info")

    def test_processing_order_reopened_for_input_is_not_offered_for_completion(self):
        user, order = self.manual()
        self.send(self.CUSTOMER, text="first-round")
        self.click(self.CUSTOMER, "پایان ارسال اطلاعات")
        self.app.show_order(user, order["id"])
        self.click(self.CUSTOMER, "ارسال اطلاعات")
        self.assertEqual(self.db.get_order(order["id"])["status"], "processing")
        self.panel("orders")
        self.click(self.OWNER, ACTIONS["complete"].label)
        self.assertEqual(self.state()["options"], [])
        with self.assertRaises(ValidationError):
            self.db.complete_order(order["id"], "premature")

    def test_old_queued_admin_info_notice_is_not_rewritten_or_sent_twice(self):
        user, order = self.manual()
        self.send(self.CUSTOMER, text="synthetic-old-notice")
        data = json.loads(self.db.get_order(order["id"])["customer_info_json"])
        self.db.finish_manual_information(order["id"], user["id"], data["collection_id"], outbound_body="Saved", reply_markup={})
        order = self.db.get_order(order["id"])
        admin = next(a for a in self.db.list_admins(active_only=True) if a["chat_id"] == self.OWNER["id"])
        owner = self.db.get_user_by_chat_id(self.OWNER["id"])
        key = f"order:{order['id']}:customer-info:{self.app._manual_info_version(order)}:admin:{admin['id']}"
        self.db.queue_outbound_message("Synthetic previous release notice", recipient_user_id=owner["id"], idempotency_key=key)
        self.app._alert_manual_order_info(order, user)
        self.app._alert_manual_order_info(order, user)
        self.assertEqual(self.db.get_outbound_message_by_idempotency_key(key)["body"], "Synthetic previous release notice")
        self.assertEqual(len([m for m in self.telegram.messages if m["text"] == "Synthetic previous release notice"]), 1)

    def test_order_search_is_visible_and_finds_persian_number_username_and_chat_id(self):
        user, order = self.manual()
        for search in ("۱۰۰۰", "@" + self.CUSTOMER["username"], str(user["chat_id"]), "Synthetic manual"):
            with self.subTest(search=search):
                self.panel("orders")
                self.click(self.OWNER, ACTIONS["order"].label)
                self.send(self.OWNER, text=search)
                self.assertEqual([x[0] for x in self.state()["options"]], [order["order_number"]])
                self.click(self.OWNER, ending=":pick:0")
                self.assertIn("جست‌وجوی سفارش", [b["text"] for b in self.buttons(self.OWNER)])

    def test_manual_completion_emits_new_header_once_with_full_rich_body(self):
        _user, order = self.manual()
        self.send(self.CUSTOMER, text="synthetic-info")
        self.click(self.CUSTOMER, "پایان ارسال اطلاعات")
        self.action("complete", {"target": order["order_number"], "delivery": 'html:<b>Done</b> <a href="https://example.test">Help</a>'})
        delivered = [m for m in self.telegram.messages if m["chat_id"] == self.CUSTOMER["id"] and "سفارشت آماده است" in m["text"]]
        self.assertEqual(len(delivered), 1)
        self.assertIn("🔑 Synthetic manual", delivered[0]["text"])
        self.assertIn('<a href="https://example.test">Help</a>', delivered[0]["text"])
        self.assertIn("شماره سفارش: <code>1000</code>", delivered[0]["text"])

    def test_legacy_committed_completion_replay_uses_original_outbox_body(self):
        user, order = self.manual()
        self.send(self.CUSTOMER, text="synthetic-info")
        self.click(self.CUSTOMER, "پایان ارسال اطلاعات")
        key = f"order:{order['id']}:manual-completion-notice"
        self.db.complete_order(order["id"], "Synthetic old delivery", outbound_body="Synthetic old header", outbound_idempotency_key=key)
        self.send(self.OWNER, text=f"/complete {order['order_number']} | Synthetic old delivery")
        self.send(self.OWNER, text=f"/complete {order['order_number']} | Synthetic old delivery")
        self.assertEqual(self.db.get_outbound_message_by_idempotency_key(key)["body"], "Synthetic old header")
        self.assertEqual(len([m for m in self.telegram.messages if m["chat_id"] == user["chat_id"] and m["text"] == "Synthetic old header"]), 1)


class FollowupCatalogTests(unittest.TestCase):
    OWNER = catalog_fixture.AdminCatalogHierarchyTests.OWNER
    setUp = catalog_fixture.AdminCatalogHierarchyTests.setUp
    tearDown = catalog_fixture.AdminCatalogHierarchyTests.tearDown
    message = catalog_fixture.AdminCatalogHierarchyTests.message
    callback = catalog_fixture.AdminCatalogHierarchyTests.callback
    _take_update_id = catalog_fixture.AdminCatalogHierarchyTests._take_update_id
    send_message = catalog_fixture.AdminCatalogHierarchyTests.send_message
    send_callback = catalog_fixture.AdminCatalogHierarchyTests.send_callback
    actor_user = catalog_fixture.AdminCatalogHierarchyTests.actor_user
    state = catalog_fixture.AdminCatalogHierarchyTests.state
    prompt = catalog_fixture.AdminCatalogHierarchyTests.prompt
    button_update = catalog_fixture.AdminCatalogHierarchyTests.button_update
    click = catalog_fixture.AdminCatalogHierarchyTests.click
    buttons = catalog_fixture.AdminCatalogHierarchyTests.buttons
    open_products = catalog_fixture.AdminCatalogHierarchyTests.open_products
    open_product = catalog_fixture.AdminCatalogHierarchyTests.open_product
    confirm = catalog_fixture.AdminCatalogHierarchyTests.confirm

    def test_product_reward_add_is_scoped_visible_confirmed_and_replay_safe(self):
        general = self.db.create_reward_rule("general", event_type="start", amount=50)
        self.open_product()
        self.click(label="پاداش معرف این محصول")
        self.click(label="افزودن پاداش ثابت این محصول")
        self.assertNotIn("شروع ربات", [b["text"] for b in self.buttons()])
        self.click(label="خرید محصول")
        self.send_message(self.OWNER, text="1234")
        self.click(ending=":default:0")
        self.click(ending=":default:0")
        state = self.state()
        self.assertEqual(state["status"], "confirm")
        self.assertEqual(state["values"]["product"], str(self.product["id"]))
        self.assertIn(self.product["name"], self.prompt()["text"])
        self.assertEqual(len(self.db.list_reward_rules()), 1)
        event = self.click(label="تأیید و اجرا")
        self.app.process_update_safe(copy.deepcopy(event))
        rules = self.db.list_reward_rules()
        self.assertEqual(len(rules), 2)
        scoped = next(r for r in rules if r["product_id"] == self.product["id"])
        self.assertEqual((scoped["amount"], scoped["event_type"]), (1234, "product_purchase"))
        self.assertEqual(next(r for r in rules if r["id"] == general["id"]), general)
        self.click(label="بازگشت به بخش انتخاب‌شده")
        self.assertIn("پاداش معرف", self.prompt()["text"])

    def test_product_reward_toggle_is_frozen_and_not_limited_to_latest_100_rules(self):
        rule = self.db.create_reward_rule("scoped-old", event_type="product_purchase", amount=20, product_id=self.product["id"])
        for i in range(101):
            self.db.create_reward_rule(f"general-{i}", event_type="start", amount=10)
        self.open_product()
        self.click(label="پاداش معرف این محصول")
        self.click(ending=f":reward:{self.product['id']}:{rule['id']}")
        self.click(label="غیرفعال‌کردن پاداش")
        self.assertEqual(self.state()["status"], "confirm")
        self.db.set_reward_rule_active(rule["id"], False)
        self.confirm()
        row = self.app.admin_controller._query_one("SELECT * FROM reward_rules WHERE id=?", (rule["id"],))
        self.assertEqual(row["is_active"], 0)

    def test_foreign_product_reward_callback_fails_closed(self):
        product = self.db.create_product(self.category["id"], "Other product", product_type="manual", price_amount=100)
        rule = self.db.create_reward_rule("other", event_type="product_purchase", amount=20, product_id=product["id"])
        self.open_product()
        self.send_callback(self.OWNER, f"adm:ui:c:rewardtoggle:{self.product['id']}:{rule['id']}")
        self.assertTrue(self.db.list_reward_rules()[0]["is_active"])
        self.assertTrue(any("متعلق به این محصول نیست" in m["text"] for m in self.telegram.messages))

    def test_ready_stock_limit_is_in_inventory_and_manual_legacy_link_cannot_edit_it(self):
        self.open_product()
        self.click(label="انبار محصول")
        self.click(label="سقف آیتم‌های انبار")
        self.assertIn("غیرفعال", self.prompt()["text"])
        self.assertIn("سفارش‌های دستی", self.prompt()["text"])
        self.db.delete_inventory_item(self.inventory["id"])
        self.db.update_product(self.product["id"], product_type="manual")
        self.send_callback(self.OWNER, f"adm:ui:c:field:{self.product['id']}:stock_limit")
        self.assertFalse(any("ویرایش" in b["text"] for b in self.buttons()))
