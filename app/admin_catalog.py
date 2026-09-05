"""Entity-centred catalog navigation over existing, confirmed domain actions.

Browse callbacks carry stable record IDs, never position-dependent indexes.
They can only read/open a form; writes still require AdminButtonUI confirmation.
"""

from __future__ import annotations

import copy
import json
from typing import Any

from .admin_forms import PRODUCT_FIELDS
from .utils import escape, money, normalize_digits, render_rich_text, split_telegram_html


class AdminCatalog:
    def __init__(self, ui: Any) -> None:
        self.ui = ui
        self.controller = ui.controller
        self.db = ui.db

    @staticmethod
    def error(message: str) -> None:
        from .admin_ui import ButtonInputError

        raise ButtonInputError(message)

    def authorise(self, user: dict, admin: dict, event: dict | None = None) -> dict:
        current = self.ui.authorise(user, admin, event)
        if current["role"] not in {"owner", "admin"}:
            self.error("مدیریت محصولات برای این نقش مجاز نیست.")
        return current

    def button(self, label: str, route: str, **kwargs: Any) -> dict:
        return self.ui._button(label, "c:" + route, **kwargs)

    def _category(self, category_id: int) -> dict | None:
        if category_id == 0:
            return None
        category = self.db.get_category(category_id)
        if category is None:
            self.error("این دسته دیگر وجود ندارد؛ از بخش محصولات ادامه دهید.")
        return category

    def _product(self, product_id: int) -> dict:
        product = self.db.get_product(product_id)
        if not product or not product["is_active"]:
            self.error("این محصول حذف شده یا وجود ندارد؛ از دستهٔ آن ادامه دهید.")
        return product

    def _item(self, item_id: int, product_id: int | None = None) -> dict:
        # Never include credential payloads in navigation queries or previews.
        item = self.controller._query_one(
            "SELECT id, product_id, status, assigned_order_id, assigned_user_id, created_at "
            "FROM inventory_items WHERE id=?", (item_id,))
        if not item or product_id is not None and item["product_id"] != product_id:
            self.error("این موجودی در انبار محصول انتخاب‌شده نیست یا حذف شده است.")
        self._product(int(item["product_id"]))
        return item

    def context(self, user: dict) -> dict:
        stored = self.db.get_user_state(int(user["id"]))
        if stored and stored["state"] == "admin:catalog":
            data = stored["data"]
            return {key: copy.deepcopy(data[key]) for key in ("kind", "id", "page", "search", "field", "product_id", "category_context", "stock_context")
                    if key in data}
        if stored and stored["state"] == "admin:ui":
            return copy.deepcopy(stored["data"].get("return_to", {}))
        return {}

    def _page(self, query: str, params: tuple, requested: int) -> tuple[list[dict], int, int, int]:
        count = self.controller._query_one(f"SELECT COUNT(*) total FROM ({query})", params)
        total = int(count["total"])
        pages = max(1, (total + 19) // 20)
        page = min(max(1, requested), pages)
        rows = self.controller._query(query + " LIMIT ? OFFSET ?", (*params, 20, (page - 1) * 20))
        return rows, total, pages, page

    def _pager(self, route: str, page: int, pages: int) -> list[list[dict]]:
        row = []
        if page > 1:
            row.append(self.button("صفحه قبل", f"{route}:{page - 1}"))
        if page < pages:
            row.append(self.button("صفحه بعد", f"{route}:{page + 1}"))
        return [row] if row else []

    def _publish(self, context: dict, text: str, rows: list[list[dict]], user: dict, admin: dict) -> None:
        previous = self.db.get_user_state(int(user["id"]))
        state = {**context, "actor": admin["id"], "chat": user["chat_id"]}
        previous_id = ((previous or {}).get("data") or {}).get("prompt_message_id")
        self.db.set_user_state(int(user["id"]), "admin:catalog", state)
        parts = split_telegram_html(text, maximum=3500)
        result = None
        for index, part in enumerate(parts):
            result = self.ui._send(user, part, rows if index == len(parts) - 1 else None)
        if isinstance(result, dict) and isinstance(result.get("message_id"), int):
            state["prompt_message_id"] = result["message_id"]
            self.db.set_user_state(int(user["id"]), "admin:catalog", state)
            if previous_id != result["message_id"]:
                self.ui._retire_prompt(user, previous_id)

    def _breadcrumb(self, category_id: int) -> str:
        names, seen = [], set()
        while category_id and category_id not in seen:
            seen.add(category_id)
            category = self._category(category_id)
            names.append(str(category["name"]))
            category_id = int(category.get("parent_id") or 0)
        return "محصولات / " + " / ".join(reversed(names))

    def category(self, category_id: int, user: dict, admin: dict, *, page: int = 1, search: str = "") -> None:
        category = self._category(category_id)
        query = (
            "SELECT 'category' kind, id, name, is_active, sort_order, 0 priority FROM categories WHERE parent_id IS ? "
            "UNION ALL SELECT 'product' kind, id, name, is_visible is_active, 0 sort_order, 1 priority "
            "FROM products WHERE category_id=? AND is_active=1")
        query = f"SELECT * FROM ({query}) WHERE instr(lower(name), lower(?))>0 ORDER BY priority, sort_order, id"
        items, total, pages, page = self._page(query, (category_id or None, category_id, search), page)
        text = f"<b>{escape(self._breadcrumb(category_id) if category else 'محصولات')}</b>\nصفحه {page} از {pages} | مجموع: {total}"
        if category:
            text += f"\nشناسه دسته: {category_id}\nنمایش دسته: {'فعال' if category['is_active'] else 'غیرفعال'}"
            if category.get("description"):
                text += "\n\n" + render_rich_text(category["description"])
        text += "\n\nدسته، زیردسته یا محصول را انتخاب کنید. برای جست‌وجو در همین بخش، نام را بفرستید."
        if not items:
            text += "\nموردی در این بخش یافت نشد."
        rows = [[self.button(("دسته: " if item["kind"] == "category" else "محصول: ") + item["name"]
                             + (" · مخفی" if not item["is_active"] else ""),
                             f"{item['kind']}:{item['id']}" + (":1" if item["kind"] == "category" else ""))]
                for item in items]
        rows += self._pager(f"category:{category_id}", page, pages)
        if search:
            rows.append([self.button("پاک‌کردن جست‌وجو", f"clear:category:{category_id}")])
        if category:
            for key, label in (("product_add", "افزودن محصول در این دسته"), ("subcategory_add", "افزودن زیردسته"),
                               ("category_set", "ویرایش مشخصات دسته"), ("category_toggle", "نمایش / عدم نمایش دسته"),
                               ("category_delete", "حذف دسته")):
                rows.append([self.button(label, f"act:category:{category_id}:{key}")])
            rows.append([self.button("بازگشت به دستهٔ بالاتر", f"category:{category.get('parent_id') or 0}:1")])
        else:
            rows.append([self.button("افزودن دستهٔ اصلی", "act:category:0:category_add")])
        self._publish({"kind": "category", "id": category_id, "page": page, "search": search}, text,
                      rows + self.ui.navigation(), user, admin)

    def _category_context(self, product: dict, user: dict) -> dict:
        previous = self.context(user)
        if previous.get("kind") == "category" and previous.get("id") == product["category_id"]:
            return previous
        inherited = previous.get("category_context", {})
        return inherited if inherited.get("id") == product["category_id"] else {"kind": "category", "id": product["category_id"]}

    def product(self, product_id: int, user: dict, admin: dict, *, kind: str = "product", field: str | None = None) -> None:
        product = self._product(product_id)
        category_context = self._category_context(product, user)
        context = {"kind": kind, "id": product_id, "category_context": category_context}
        title = f"<b>{escape(product['name'])}</b>\n{escape(self._breadcrumb(product['category_id']))}\nشناسه محصول: {product_id}"
        rows = []
        if kind == "product":
            stock = self.controller._query_one(
                "SELECT COUNT(*) total, COALESCE(SUM(status='available'), 0) available FROM inventory_items WHERE product_id=?", (product_id,))
            text = (title + f"\nقیمت: {money(product['price_amount'])}\nمدت: {escape(product.get('duration_label') or '—')}"
                    f"\nفرمت: {'موجود در انبار' if product['product_type'] == 'ready' else 'نیازمند اطلاعات کاربر'}"
                    f"\nنمایش: {'فعال' if product['is_visible'] else 'مخفی'} | قابل خرید: {'بله' if product['is_available'] else 'خیر'}"
                    f"\nرزرو: {'فعال' if product['reserve_enabled'] else 'غیرفعال'}"
                    f"\nموجودی آماده: {stock['available']} | کل آیتم‌ها: {stock['total']}")
            if product.get("short_description"):
                text += "\n\n" + render_rich_text(product["short_description"])
            rows = [[self.button(label, f"{route}:{product_id}")] for label, route in (
                ("اطلاعات و ویرایش محصول", "fields"), ("انبار محصول", "stock"), ("فرمت محصول", "format"))]
            rows += [[self.button("نمایش / عدم نمایش محصول", f"toggle:{product_id}:visible")],
                     [self.button("موجود / ناموجود", f"toggle:{product_id}:available")],
                     [self.button("یادآوری پایان اشتراک", f"field:{product_id}:reminder_days")],
                     [self.button("حذف محصول", f"act:product:{product_id}:product_delete")],
                     [self.button("بازگشت به دستهٔ محصول", "up")]]
        elif kind == "fields":
            text = title + "\n\nهر مشخصه را انتخاب کنید تا مقدار کامل فعلی را ببینید و ویرایش کنید."
            rows = [[self.button(label, f"field:{product_id}:{key}")] for label, key in PRODUCT_FIELDS]
        elif kind == "field":
            from .admin import _PRODUCT_FIELDS

            labels = {key: label for label, key in PRODUCT_FIELDS}
            if field not in labels:
                self.error("این مشخصهٔ محصول قابل ویرایش نیست.")
            context["field"] = field
            value = product.get(_PRODUCT_FIELDS[field])
            if field in {"features", "reminder_days"}:
                value = "؛ ".join(map(str, json.loads(product[field + "_json"])))
            elif field == "type":
                value = "موجود در انبار" if value == "ready" else "نیازمند اطلاعات کاربر"
            elif field == "renewable":
                value = "بله" if value else "خیر"
            elif field == "category":
                value = self._category(int(value))["name"]
            text = title + f"\n\n<b>{escape(labels[field])}</b>\nمقدار فعلی:\n{escape(value if value is not None else 'تنظیم نشده')}"
            rows = [[self.button("ویرایش این مقدار", f"edit:{product_id}:{field}")],
                    [self.button("بازگشت به اطلاعات محصول", f"fields:{product_id}")]]
        elif kind == "format":
            text = title + "\n\nفرمت محصول: " + ("موجود در انبار" if product["product_type"] == "ready" else "نیازمند اطلاعات کاربر")
            text += "\nموجود در انبار: تحویل خودکار اطلاعات اکانت پس از پرداخت.\nنیازمند اطلاعات: دریافت اطلاعات خریدار و انجام سفارش توسط مدیر."
            text += "\nتغییر از موجود در انبار به دستی، نیازمند خاموش‌بودن رزرو و نداشتن آیتم انبار یا سفارش آمادهٔ باز است؛ سوابق قبلی خودکار حذف نمی‌شوند."
            rows = [[self.button("انتخاب فرمت محصول", f"edit:{product_id}:type")]]
            if product["product_type"] == "ready":
                rows += [[self.button("فعال / غیرفعال‌کردن رزرو", f"toggle:{product_id}:reserve")],
                         [self.button("راهنمای تحویل اکانت", f"field:{product_id}:delivery_instructions")]]
            else:
                rows += [[self.button("متن دریافت اطلاعات کاربر", f"field:{product_id}:info_request_text")],
                         [self.button("متن تکمیل سفارش", f"field:{product_id}:completion_text")],
                         [self.button("سقف موجودی محصول دستی", f"field:{product_id}:stock_limit")]]
        else:
            self.error("صفحهٔ محصول معتبر نیست.")
        if kind != "product":
            rows.append([self.button("بازگشت به محصول", f"product:{product_id}")])
        self._publish(context, text, rows + self.ui.navigation(), user, admin)

    def stock(self, product_id: int, user: dict, admin: dict, *, page: int = 1, search: str = "") -> None:
        product = self._product(product_id)
        query = ("SELECT id, status FROM inventory_items WHERE product_id=? "
                 "AND instr(CAST(id AS TEXT), ?)>0 ORDER BY id DESC")
        items, total, pages, page = self._page(query, (product_id, search), page)
        text = f"<b>انبار {escape(product['name'])}</b>\nصفحه {page} از {pages} | کل آیتم‌ها: {total}"
        text += "\nبرای جست‌وجو، شناسهٔ آیتم را بفرستید. اطلاعات محرمانه در فهرست بازنشر نمی‌شود."
        labels = {"available": "آماده تحویل", "assigned": "تحویل‌شده", "disabled": "غیرفعال"}
        rows = [[self.button(f"آیتم {item['id']} · {labels[item['status']]}", f"item:{product_id}:{item['id']}")] for item in items]
        rows += self._pager(f"stock:{product_id}", page, pages)
        if not items:
            text += "\nموجودی در این فهرست یافت نشد."
        if search:
            rows.append([self.button("پاک‌کردن جست‌وجو", f"clear:stock:{product_id}")])
        if product["product_type"] == "ready":
            rows.append([self.button("افزایش موجودی / افزودن اکانت", f"act:product:{product_id}:inventory_add")])
        else:
            text += "\nاین محصول دستی است؛ سقف تعداد قابل فروش را از گزینهٔ زیر تغییر دهید."
            rows.append([self.button("تغییر سقف موجودی دستی", f"edit:{product_id}:stock_limit")])
        rows.append([self.button("بازگشت به محصول", f"product:{product_id}")])
        self._publish({"kind": "stock", "id": product_id, "page": page, "search": search,
                       "category_context": self._category_context(product, user)}, text, rows + self.ui.navigation(), user, admin)

    def item(self, product_id: int, item_id: int, user: dict, admin: dict) -> None:
        product = self._product(product_id)
        item = self._item(item_id, product_id)
        labels = {"available": "آماده تحویل", "assigned": "تحویل‌شده", "disabled": "غیرفعال"}
        text = f"<b>آیتم {item_id}</b>\nمحصول: {escape(product['name'])}\nوضعیت: {labels[item['status']]}"
        rows = []
        if item["status"] != "assigned":
            actions = [("inventory_edit", "ویرایش اطلاعات اکانت"), ("inventory_delete", "حذف موجودی")]
            actions.append(("inventory_disable", "غیرفعال‌کردن موجودی") if item["status"] == "available" else ("inventory_enable", "فعال‌کردن موجودی"))
            if item["status"] == "available" and product["product_type"] == "ready":
                actions.append(("inventory_assign", "تخصیص این موجودی به کاربر"))
            rows += [[self.button(label, f"act:item:{item_id}:{key}")] for key, label in actions]
        else:
            text += "\nاین موجودی قبلاً تحویل شده و قابل تغییر، حذف یا تخصیص دوباره نیست."
        previous = self.context(user)
        stock_context = previous if previous.get("kind") == "stock" and previous.get("id") == product_id else previous.get("stock_context", {})
        if stock_context.get("kind") != "stock" or stock_context.get("id") != product_id:
            stock_context = {"kind": "stock", "id": product_id}
        rows.append([self.button("بازگشت به انبار محصول", "backstock")])
        self._publish({"kind": "item", "id": item_id, "product_id": product_id,
                       "stock_context": stock_context,
                       "category_context": self._category_context(product, user)}, text, rows + self.ui.navigation(), user, admin)

    def open_context(self, context: dict, user: dict, admin: dict) -> None:
        kind, identifier = context.get("kind"), int(context.get("id") or 0)
        if kind == "category":
            self.category(identifier, user, admin, page=int(context.get("page") or 1), search=str(context.get("search") or ""))
        elif kind == "stock":
            self.stock(identifier, user, admin, page=int(context.get("page") or 1), search=str(context.get("search") or ""))
        elif kind == "item":
            self.item(int(context["product_id"]), identifier, user, admin)
        elif kind in {"product", "fields", "field", "format"}:
            self.product(identifier, user, admin, kind=kind, field=context.get("field"))
        else:
            self.category(0, user, admin)

    def start_action(self, kind: str, identifier: int, key: str, event: dict, user: dict, admin: dict,
                     *, field: str | None = None, flag: str | None = None) -> None:
        allowed = {
            "category": {"category_add", "subcategory_add", "category_set", "category_toggle", "category_delete", "product_add"},
            "product": {"product_set", "product_toggle", "product_delete", "inventory_add"},
            "item": {"inventory_edit", "inventory_delete", "inventory_enable", "inventory_disable", "inventory_assign"},
        }
        if key not in allowed.get(kind, set()):
            self.error("این عملیات به مورد انتخاب‌شده مربوط نیست.")
        context = self.context(user)
        preset = {}
        selected: str | None = str(identifier)
        if kind == "category":
            category = self._category(identifier)
            if key == "category_add":
                if identifier:
                    self.error("دستهٔ اصلی باید از صفحهٔ محصولات ساخته شود.")
                selected = None
            elif not category:
                self.error("ابتدا یک دسته انتخاب کنید.")
            context = context if context.get("kind") == "category" and context.get("id") == identifier else {"kind": "category", "id": identifier}
            if key == "category_delete":
                context = {"kind": "category", "id": category.get("parent_id") or 0}
        elif kind == "product":
            product = self._product(identifier)
            if field is not None:
                if key != "product_set" or field not in dict((key, label) for label, key in PRODUCT_FIELDS):
                    self.error("مشخصهٔ محصول معتبر نیست.")
                preset["field"] = field
            if flag is not None:
                if key != "product_toggle" or flag not in {"visible", "available", "reserve"}:
                    self.error("وضعیت محصول معتبر نیست.")
                preset["field"] = flag
            context = {"kind": "stock" if key == "inventory_add" else "product", "id": identifier,
                       "category_context": self._category_context(product, user)}
            if key == "product_delete":
                context = self._category_context(product, user)
        else:
            item = self._item(identifier)
            if item["status"] == "assigned":
                self.error("موجودی تحویل‌شده قابل تغییر نیست.")
            stock_context = context.get("stock_context", {"kind": "stock", "id": item["product_id"]})
            context = stock_context if key == "inventory_delete" else {
                "kind": "item", "id": identifier, "product_id": item["product_id"], "stock_context": stock_context,
                "category_context": context.get("category_context", {})}
        self.ui.begin(key, event, user, admin, selected=selected, preset=preset, return_to=context)

    def callback(self, suffix: str, event: dict, user: dict, admin: dict) -> bool:
        admin = self.authorise(user, admin, event)
        parts = suffix.split(":")
        route = parts[0]
        context = self.context(user)
        if route == "back" and len(parts) == 1:
            self.open_context(context, user, admin)
            return True
        if route == "up" and len(parts) == 1:
            self.open_context(context.get("category_context", {"kind": "category", "id": 0}), user, admin)
            return True
        if route == "backstock" and len(parts) == 1:
            self.open_context(context.get("stock_context", {"kind": "category", "id": 0}), user, admin)
            return True
        if route == "act" and len(parts) == 4 and parts[2].isdigit():
            self.start_action(parts[1], int(parts[2]), parts[3], event, user, admin)
            return True
        if route == "clear" and len(parts) == 3 and parts[2].isdigit() and parts[1] in {"category", "stock"}:
            self.open_context({"kind": parts[1], "id": int(parts[2])}, user, admin)
            return True
        if len(parts) < 2 or not parts[1].isdigit():
            self.error("دکمهٔ محصولات معتبر نیست.")
        identifier = int(parts[1])
        if route in {"category", "stock"} and len(parts) in {2, 3}:
            if len(parts) == 3 and not parts[2].isdigit():
                self.error("صفحه معتبر نیست.")
            page = int(parts[2]) if len(parts) == 3 else 1
            search = context.get("search", "") if context.get("kind") == route and context.get("id") == identifier else ""
            self.open_context({"kind": route, "id": identifier, "page": page, "search": search}, user, admin)
        elif route in {"product", "fields", "format"} and len(parts) == 2:
            self.product(identifier, user, admin, kind=route)
        elif route == "field" and len(parts) == 3:
            self.product(identifier, user, admin, kind="field", field=parts[2])
        elif route in {"edit", "toggle"} and len(parts) == 3:
            self.start_action("product", identifier, "product_set" if route == "edit" else "product_toggle", event, user, admin,
                              field=parts[2] if route == "edit" else None, flag=parts[2] if route == "toggle" else None)
        elif route == "item" and len(parts) == 3 and parts[2].isdigit():
            self.item(identifier, int(parts[2]), user, admin)
        else:
            self.error("دکمهٔ محصولات معتبر نیست.")
        return True

    def message(self, event: dict, user: dict, admin: dict) -> bool:
        admin = self.authorise(user, admin, event)
        context = self.context(user)
        if context.get("kind") in {"category", "stock"}:
            text = event.get("text")
            if not isinstance(text, str) or not text.strip():
                self.error("نام یا شناسه را به‌صورت متن بفرستید؛ یا از دکمهٔ بازگشت استفاده کنید.")
            context.update(search=normalize_digits(text.strip())[:120], page=1)
        self.open_context(context, user, admin)
        return True
