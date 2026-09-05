"""Specification-first, emitted-keyboard tests for the nine-section admin tree."""

from __future__ import annotations

import copy
import unittest
from unittest.mock import patch

from app.admin_forms import GROUPS, MAIN_GROUPS, PRODUCT_FIELDS
from app.bot import BotApplication
from app.db import DatabaseError
from app.keyboards import contains_emoji
from tests import test_admin_ui_navigation as fixture


class AdminCatalogHierarchyTests(unittest.TestCase):
    OWNER = fixture.AdminNavigationTests.OWNER
    setUp = fixture.AdminNavigationTests.setUp
    tearDown = fixture.AdminNavigationTests.tearDown
    message = fixture.AdminNavigationTests.message
    callback = fixture.AdminNavigationTests.callback
    _take_update_id = fixture.AdminNavigationTests._take_update_id
    send_message = fixture.AdminNavigationTests.send_message
    send_callback = fixture.AdminNavigationTests.send_callback
    actor_user = fixture.AdminNavigationTests.actor_user
    state = fixture.AdminNavigationTests.state
    prompt = fixture.AdminNavigationTests.prompt
    button_update = fixture.AdminNavigationTests.button_update
    click = fixture.AdminNavigationTests.click

    def view(self):
        state = self.db.get_user_state(self.actor_user()["id"])
        self.assertEqual(state["state"], "admin:catalog")
        return state["data"]

    def buttons(self):
        return [b for row in self.prompt()["reply_markup"]["inline_keyboard"] for b in row]

    def open_products(self):
        self.send_message(self.OWNER, text="/start")
        self.click(label="پنل مدیریت")
        self.click(label="محصولات")

    def open_product(self, product=None):
        product = product or self.product
        self.open_products()
        self.click(ending=f":category:{product['category_id']}:1")
        self.click(ending=f":product:{product['id']}")

    def confirm(self):
        self.assertEqual(self.state()["status"], "confirm")
        self.click(label="تأیید و اجرا")
        self.assertEqual(self.state()["status"], "done")

    def test_nine_main_business_buttons_match_source_order_and_children_are_nested(self):
        self.send_callback(self.OWNER, "adm:ui:home")
        groups = [b for b in self.buttons() if b.get("callback_data", "").startswith("adm:ui:g:")]
        self.assertEqual([b["text"] for b in groups],
                         ["مدیریت کلی ربات", "محصولات", "سفارش‌ها", "تیکت‌ها", "کاربران", "ارسال پیام", "سؤالات متداول", "تخفیف‌ها", "گزارش"])
        self.assertEqual([b["callback_data"].split(":")[-1] for b in groups], list(MAIN_GROUPS))
        self.click(label="مدیریت کلی ربات")
        labels = [b["text"] for b in self.buttons()]
        self.assertIn(GROUPS["admins"], labels)
        self.assertIn(GROUPS["rewards"], labels)
        self.click(label=GROUPS["admins"])
        self.click(label="بازگشت به مدیریت کلی ربات")
        self.click(label="پنل مدیریت")
        self.click(label="سفارش‌ها")
        self.assertIn(GROUPS["payments"], [b["text"] for b in self.buttons()])

    def test_category_tree_only_lists_its_children_and_products(self):
        other = self.db.create_category("دسته دیگر")
        unrelated = self.db.create_product(other["id"], "نباید اینجا باشد", product_type="ready", price_amount=200)
        sub = self.db.create_category("زیردسته", parent_id=self.category["id"])
        nested = self.db.create_product(sub["id"], "محصول زیرشاخه", product_type="ready", price_amount=200)
        self.open_products()
        self.assertFalse(any(b["callback_data"].endswith(f":product:{self.product['id']}") for b in self.buttons()))
        self.click(ending=f":category:{self.category['id']}:1")
        callbacks = [b["callback_data"] for b in self.buttons()]
        self.assertIn(f"adm:ui:c:product:{self.product['id']}", callbacks)
        self.assertNotIn(f"adm:ui:c:product:{unrelated['id']}", callbacks)
        self.assertNotIn(f"adm:ui:c:product:{nested['id']}", callbacks)
        self.click(ending=f":category:{sub['id']}:1")
        self.click(ending=f":product:{nested['id']}")
        self.assertEqual(self.view()["id"], nested["id"])
        self.click(label="بازگشت به دستهٔ محصول")
        self.assertEqual(self.view()["id"], sub["id"])

    def test_product_price_edit_is_preselected_confirmed_and_returns_to_same_product(self):
        self.open_product()
        self.click(label="اطلاعات و ویرایش محصول")
        self.click(label="قیمت به تومان")
        self.click(label="ویرایش این مقدار")
        self.assertEqual(self.state()["values"], {"target": str(self.product["id"]), "field": "price"})
        self.send_message(self.OWNER, text="۱۲۵۰۰")
        self.assertEqual(self.db.get_product(self.product["id"])["price_amount"], 100000)
        self.confirm()
        self.assertEqual(self.db.get_product(self.product["id"])["price_amount"], 12500)
        self.click(label="بازگشت به بخش انتخاب‌شده")
        self.assertEqual((self.view()["kind"], self.view()["id"]), ("product", self.product["id"]))

    def test_all_product_fields_have_a_full_value_view_and_contextual_edit(self):
        for label, key in PRODUCT_FIELDS:
            with self.subTest(field=key):
                self.open_product()
                self.click(label="اطلاعات و ویرایش محصول")
                self.click(label=label)
                self.assertEqual(self.view()["field"], key)
                self.click(label="ویرایش این مقدار")
                self.assertEqual(self.state()["values"]["field"], key)
                self.assertEqual(self.state()["values"]["target"], str(self.product["id"]))
                self.click(label="لغو و بازگشت")
                self.assertEqual(self.view()["kind"], "product")

    def test_long_product_data_is_displayed_without_truncation(self):
        description = ("توضیحات کامل <آزمایش> " * 800) + "پایان_یگانه_متن"
        self.db.update_product(self.product["id"], long_description=description)
        self.open_product()
        self.click(label="اطلاعات و ویرایش محصول")
        before = len(self.telegram.messages)
        self.click(label="توضیح کامل")
        messages = self.telegram.messages[before:]
        self.assertGreater(len(messages), 1)
        self.assertTrue(all(len(m["text"]) <= 3900 for m in messages))
        self.assertIn("پایان_یگانه_متن", messages[-1]["text"])
        self.assertTrue(messages[-1]["reply_markup"]["inline_keyboard"])

        # Context titles are previews, not an unbounded echo of a product name.
        long_name = "نام بسیار بلند " * 13
        product = self.db.create_product(self.category["id"], long_name, product_type="manual", price_amount=1)
        self.open_product(product)
        self.click(label="اطلاعات و ویرایش محصول")
        self.click(label="قیمت به تومان")
        self.click(label="ویرایش این مقدار")
        self.assertLess(len(self.prompt()["text"]), 3900)
        self.assertIn("شناسه: " + str(product["id"]), self.prompt()["text"])
        self.assertEqual(self.db.get_product(product["id"])["name"], long_name.strip())

    def test_add_product_and_subcategory_use_selected_category_without_global_selector(self):
        self.open_products()
        self.click(ending=f":category:{self.category['id']}:1")
        self.click(label="افزودن محصول در این دسته")
        self.assertEqual(self.state()["values"]["target"], str(self.category["id"]))
        self.send_message(self.OWNER, text="محصول جدید")
        self.send_message(self.OWNER, text="2500")
        self.send_message(self.OWNER, text="۳۰ روز")
        self.click(label="آماده و خودکار")
        self.confirm()
        self.click(label="بازگشت به بخش انتخاب‌شده")
        self.assertEqual(self.view()["id"], self.category["id"])
        self.assertIn("محصول: محصول جدید", [b["text"] for b in self.buttons()])
        self.click(label="افزودن زیردسته")
        self.send_message(self.OWNER, text="زیرشاخه جدید")
        self.click(label="بدون آیکون")
        self.click(ending=":default:0")
        self.confirm()
        self.assertEqual(self.db.list_categories(parent_id=self.category["id"])[0]["name"], "زیرشاخه جدید")

    def test_inventory_add_edit_disable_enable_delete_stay_inside_one_product(self):
        self.open_product()
        self.click(label="انبار محصول")
        self.click(label="افزایش موجودی / افزودن اکانت")
        self.assertEqual(self.state()["values"]["target"], str(self.product["id"]))
        secret = "dummy@example.test\npassword: dummy | unchanged\n2FA: DEMO"
        self.send_message(self.OWNER, text=secret)
        update = self.button_update(label="تأیید و اجرا")
        self.app.process_update(update)
        self.app.process_update(copy.deepcopy(update))
        self.assertEqual(self.db.inventory_count(self.product["id"]), 2)
        self.click(label="بازگشت به بخش انتخاب‌شده")
        self.assertEqual(self.view()["kind"], "stock")
        item = self.db.list_inventory_items(self.product["id"])[0]
        self.click(ending=f":item:{self.product['id']}:{item['id']}")
        self.click(label="ویرایش اطلاعات اکانت")
        self.send_message(self.OWNER, text="new dummy credentials")
        self.confirm()
        self.click(label="بازگشت به بخش انتخاب‌شده")
        self.click(label="غیرفعال‌کردن موجودی")
        self.confirm()
        self.click(label="بازگشت به بخش انتخاب‌شده")
        self.click(label="فعال‌کردن موجودی")
        self.confirm()
        self.click(label="بازگشت به بخش انتخاب‌شده")
        self.click(label="حذف موجودی")
        self.confirm()
        self.click(label="بازگشت به بخش انتخاب‌شده")
        self.assertEqual(self.db.inventory_count(self.product["id"]), 1)
        self.assertEqual(self.view()["kind"], "stock")
        self.assertFalse(any(secret in m["text"] for m in self.telegram.messages))

    def test_inventory_assignment_is_confirmed_and_records_internal_order(self):
        target = self.db.upsert_user(55000, 55000, username="catalog_buyer", first_name="گیرنده")
        self.open_product()
        self.click(label="انبار محصول")
        self.click(ending=f":item:{self.product['id']}:{self.inventory['id']}")
        self.click(label="تخصیص این موجودی به کاربر")
        self.click(label="گیرنده @catalog_buyer · 55000")
        self.confirm()
        order = self.db.list_orders(user_id=target["id"])[0]
        self.assertEqual(order["order_origin"], "admin_assignment")
        self.click(label="بازگشت به بخش انتخاب‌شده")
        self.assertFalse(any("حذف" in b["text"] or "ویرایش" in b["text"] or "تخصیص" in b["text"] for b in self.buttons()))

    def test_format_and_reservation_are_contextual_and_manual_stock_has_its_own_control(self):
        self.db.delete_inventory_item(self.inventory["id"])
        self.open_product()
        self.click(label="فرمت محصول")
        self.click(label="انتخاب فرمت محصول")
        self.click(label="فعال‌سازی دستی")
        self.confirm()
        self.assertEqual(self.db.get_product(self.product["id"])["product_type"], "manual")
        self.click(label="بازگشت به بخش انتخاب‌شده")
        self.click(label="انبار محصول")
        self.assertNotIn("افزایش موجودی / افزودن اکانت", [b["text"] for b in self.buttons()])
        self.click(label="تغییر سقف موجودی دستی")
        self.send_message(self.OWNER, text="10")
        self.confirm()
        self.assertEqual(self.db.get_product(self.product["id"])["stock_limit"], 10)

    def test_catalog_search_pagination_and_back_preserve_selected_category(self):
        for index in range(24):
            self.db.create_product(self.category["id"], f"کالای شماره {index}", product_type="ready", price_amount=100)
        self.open_products()
        self.click(ending=f":category:{self.category['id']}:1")
        self.click(label="صفحه بعد")
        self.assertEqual(self.view()["page"], 2)
        self.click(label="محصول: کالای شماره 23")
        self.click(label="بازگشت به دستهٔ محصول")
        self.assertEqual(self.view()["page"], 2)
        self.send_message(self.OWNER, text="کالای شماره 23")
        self.assertEqual(self.view()["search"], "کالای شماره 23")
        products = [b for b in self.buttons() if ":c:product:" in b["callback_data"]]
        self.assertEqual(len(products), 1)
        self.click(label="پاک‌کردن جست‌وجو")
        self.assertEqual(self.view()["search"], "")

    def test_role_private_chat_and_missing_entities_fail_closed(self):
        actor = {"id": 56000, "username": "catalog_support", "first_name": "پشتیبان"}
        self.db.upsert_user(actor["id"], actor["id"], username=actor["username"])
        self.db.add_admin(actor["username"], actor["id"], role="support")
        with patch.object(self, "OWNER", actor):
            self.send_callback(actor, f"adm:ui:c:product:{self.product['id']}")
            self.assertIsNone(self.state())
            self.assertTrue(any("برای این نقش مجاز نیست" in m["text"] for m in self.telegram.messages if m["chat_id"] == actor["id"]))
            self.assertFalse(any(b.get("callback_data", "").startswith("adm:ui:c:") for b in self.buttons()))
        self.open_product()
        self.db.soft_delete_product(self.product["id"])
        self.click(label="اطلاعات و ویرایش محصول")
        self.assertTrue(any("حذف شده" in m["text"] for m in self.telegram.messages))
        self.open_products()
        before = self.view()
        forged = self.button_update(label="افزودن دستهٔ اصلی")
        forged["callback_query"]["message"]["chat"]["type"] = "group"
        self.app.process_update(forged)
        self.assertEqual(self.view(), before)

    def test_catalog_state_survives_restart_and_all_labels_are_safe(self):
        self.open_product()
        self.app = BotApplication(self.settings, self.db, self.telegram)
        self.app.initialize()
        self.click(label="انبار محصول")
        self.assertEqual(self.view()["kind"], "stock")
        for message in self.telegram.messages:
            for row in (message.get("reply_markup") or {}).get("inline_keyboard", []):
                for button in row:
                    self.assertFalse(contains_emoji(button["text"]))
                    self.assertLessEqual(len(button.get("callback_data", "").encode()), 64)

    def test_scoped_correction_cannot_change_product_or_field(self):
        self.open_product()
        self.click(label="اطلاعات و ویرایش محصول")
        self.click(label="قیمت به تومان")
        self.click(label="ویرایش این مقدار")
        self.assertNotIn("مرحله قبل / اصلاح", [b["text"] for b in self.buttons()])
        self.send_message(self.OWNER, text="999")
        self.click(label="مرحله قبل / اصلاح")
        self.assertEqual(self.state()["values"], {"target": str(self.product["id"]), "field": "price"})
        self.assertNotIn("مرحله قبل / اصلاح", [b["text"] for b in self.buttons()])
        state = self.state()
        self.send_callback(self.OWNER, f"adm:ui:f:{state['token']}:{state['revision']}:back:0")
        self.assertEqual(self.state()["values"], state["values"])
        self.send_message(self.OWNER, text="777")
        self.confirm()
        self.assertEqual(self.db.get_product(self.product["id"])["price_amount"], 777)

    def test_product_category_change_returns_to_its_new_category(self):
        destination = self.db.create_category("مقصد جدید")
        self.open_product()
        self.click(label="اطلاعات و ویرایش محصول")
        self.click(label="دسته")
        self.click(label="ویرایش این مقدار")
        self.click(label=f"مقصد جدید · {destination['id']}")
        self.confirm()
        self.click(label="بازگشت به بخش انتخاب‌شده")
        self.click(label="بازگشت به دستهٔ محصول")
        self.assertEqual(self.view()["id"], destination["id"])
        self.assertTrue(any(b["callback_data"].endswith(f":product:{self.product['id']}") for b in self.buttons()))

    def test_product_category_visibility_and_availability_controls_are_confirmed(self):
        self.open_product()
        for label, column in (("نمایش / عدم نمایش محصول", "is_visible"), ("موجود / ناموجود", "is_available")):
            self.click(label=label)
            self.assertTrue(self.db.get_product(self.product["id"])[column])
            self.confirm()
            self.assertFalse(self.db.get_product(self.product["id"])[column])
            self.click(label="بازگشت به بخش انتخاب‌شده")
        self.click(label="بازگشت به دستهٔ محصول")
        self.assertTrue(any("مخفی" in b["text"] and ":c:product:" in b["callback_data"] for b in self.buttons()))
        self.click(label="نمایش / عدم نمایش دسته")
        self.confirm()
        self.click(label="بازگشت به بخش انتخاب‌شده")
        self.assertFalse(self.db.get_category(self.category["id"])["is_active"])
        self.click(label="ویرایش مشخصات دسته")
        self.click(label="نام")
        self.send_message(self.OWNER, text="نام ویرایش‌شده")
        self.confirm()
        self.assertEqual(self.db.get_category(self.category["id"])["name"], "نام ویرایش‌شده")

    def test_contextual_deletions_return_to_parent_and_protect_nonempty_category(self):
        self.open_product()
        self.click(label="حذف محصول")
        self.confirm()
        self.click(label="بازگشت به بخش انتخاب‌شده")
        self.assertEqual(self.view()["id"], self.category["id"])
        self.assertFalse(any(":c:product:" in b["callback_data"] for b in self.buttons()))
        self.click(label="حذف دسته")
        self.click(label="تأیید و اجرا")
        self.assertEqual(self.state()["status"], "confirm")
        self.assertIsNotNone(self.db.get_category(self.category["id"]))
        empty = self.db.create_category("دسته خالی")
        self.open_products()
        self.click(ending=f":category:{empty['id']}:1")
        self.click(label="حذف دسته")
        self.confirm()
        self.click(label="بازگشت به بخش انتخاب‌شده")
        self.assertEqual(self.view()["id"], 0)
        self.assertIsNone(self.db.get_category(empty["id"]))

    def test_format_rejects_existing_stock_and_reservation_without_data_loss(self):
        self.open_product()
        self.click(label="فرمت محصول")
        self.click(label="انتخاب فرمت محصول")
        self.click(label="فعال‌سازی دستی")
        self.click(label="تأیید و اجرا")
        self.assertEqual(self.state()["status"], "confirm")
        self.assertEqual(self.db.get_product(self.product["id"])["product_type"], "ready")
        self.assertEqual(self.db.inventory_count(self.product["id"]), 1)
        self.assertTrue(any("محصول هنوز آیتم انبار دارد" in m["text"] for m in self.telegram.messages))
        self.click(label="لغو و بازگشت")
        self.click(label="فرمت محصول")
        self.click(label="فعال / غیرفعال‌کردن رزرو")
        self.confirm()
        self.assertTrue(self.db.get_product(self.product["id"])["reserve_enabled"])
        self.click(label="بازگشت به بخش انتخاب‌شده")
        self.click(label="فرمت محصول")
        self.click(label="انتخاب فرمت محصول")
        self.click(label="فعال‌سازی دستی")
        self.click(label="تأیید و اجرا")
        self.assertTrue(any("پیش از تغییر به فرمت دستی، رزرو را غیرفعال کنید" in m["text"] for m in self.telegram.messages))
        self.assertEqual(self.db.get_product(self.product["id"])["product_type"], "ready")

    def test_stock_pagination_search_and_item_return_preserve_scope(self):
        for index in range(23):
            self.db.add_inventory_item(self.product["id"], f"dummy-account-{index}")
        self.open_product()
        self.click(label="انبار محصول")
        self.click(label="صفحه بعد")
        self.assertEqual(self.view()["page"], 2)
        self.click(ending=f":item:{self.product['id']}:{self.inventory['id']}")
        self.click(label="بازگشت به انبار محصول")
        self.assertEqual(self.view()["page"], 2)
        self.send_message(self.OWNER, text=str(self.inventory["id"]))
        search = self.view()["search"]
        self.click(ending=f":item:{self.product['id']}:{self.inventory['id']}")
        self.click(label="ویرایش اطلاعات اکانت")
        self.send_message(self.OWNER, text="dummy-updated")
        self.confirm()
        self.click(label="بازگشت به بخش انتخاب‌شده")
        self.click(label="بازگشت به انبار محصول")
        self.assertEqual(self.view()["search"], search)

    def test_cross_product_and_malformed_inventory_callbacks_cannot_open_item(self):
        other = self.db.create_product(self.category["id"], "محصول جدا", product_type="ready", price_amount=50)
        self.open_product(other)
        previous = self.view()
        for suffix in (f"item:{other['id']}:{self.inventory['id']}", "category:abc", "stock:1:no", f"edit:{other['id']}:unknown"):
            with self.subTest(suffix=suffix):
                self.send_callback(self.OWNER, "adm:ui:c:" + suffix)
                self.assertEqual(self.view(), previous)
        self.assertEqual(self.db.inventory_count(self.product["id"]), 1)

    def test_role_revocation_blocks_scoped_confirmation_but_support_can_open_single_message(self):
        actor = {"id": 56100, "username": "catalog_role", "first_name": "مدیر"}
        self.db.upsert_user(actor["id"], actor["id"], username=actor["username"])
        self.db.add_admin(actor["username"], actor["id"], role="admin")
        with patch.object(self, "OWNER", actor):
            self.open_product()
            self.click(label="نمایش / عدم نمایش محصول")
            update = self.button_update(label="تأیید و اجرا")
            self.db.add_admin(actor["username"], actor["id"], role="support")
            self.app.process_update(update)
            self.assertTrue(self.db.get_product(self.product["id"])["is_visible"])
            self.send_callback(actor, "adm:ui:home")
            self.assertNotIn("محصولات", [b["text"] for b in self.buttons()])
            self.click(label="ارسال پیام")
            self.click(label="ارسال پیام تکی")
            self.assertEqual(self.state()["action"], "message")

    def test_catalog_prompt_save_crash_recovers_and_stale_form_does_not_mutate(self):
        self.open_product()
        update = self.button_update(label="انبار محصول")
        original = self.db.set_user_state
        failed = False

        def crash_after(*args, **kwargs):
            nonlocal failed
            result = original(*args, **kwargs)
            if not failed and args[1] == "admin:catalog" and args[2].get("prompt_message_id"):
                failed = True
                raise DatabaseError("simulated catalog prompt commit crash")
            return result

        with patch.object(self.db, "set_user_state", side_effect=crash_after):
            self.assertIs(self.app.process_update_safe(copy.deepcopy(update)), False)
            self.assertIsNot(self.app.process_update_safe(copy.deepcopy(update)), False)
        self.assertTrue(failed)
        self.click(label="بازگشت به محصول")
        self.click(label="نمایش / عدم نمایش محصول")
        stale = copy.deepcopy(self.prompt())
        self.click(label="لغو و بازگشت")
        self.click(label="تأیید و اجرا", prompt=stale)
        self.assertEqual(self.view()["kind"], "product")
        self.assertTrue(self.db.get_product(self.product["id"])["is_visible"])


if __name__ == "__main__":
    unittest.main()
