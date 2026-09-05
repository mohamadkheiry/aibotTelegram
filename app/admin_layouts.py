"""Button-only customer-layout editor: safe preview, draft, publish and undo."""
from __future__ import annotations

import copy
import secrets
from typing import Any

from .customer_layouts import GROUPS, SECTIONS, LayoutEngine, defaults, definition, validate
from .db import ConflictError, NotFoundError, ValidationError
from .utils import escape


def reflow(config: dict, columns: int) -> dict:
    result = copy.deepcopy(config)
    rows, pending = [], []
    for key in [key for row in config["rows"] for key in row]:
        if key == "items":
            rows.extend(pending[index:index + columns] for index in range(0, len(pending), columns))
            pending = []
            rows.append([key])
        else:
            pending.append(key)
    rows.extend(pending[index:index + columns] for index in range(0, len(pending), columns))
    result.update(rows=rows, columns=columns)
    return result


def move_slot(config: dict, key: str, direction: str) -> dict:
    result = copy.deepcopy(config)
    rows = result["rows"]
    row = next(i for i, value in enumerate(rows) if key in value)
    column = rows[row].index(key)
    if direction in {"left", "right"}:
        target = column + (-1 if direction == "left" else 1)
        if 0 <= target < len(rows[row]):
            rows[row][column], rows[row][target] = rows[row][target], rows[row][column]
    elif direction in {"up", "down"}:
        target = row + (-1 if direction == "up" else 1)
        if 0 <= target < len(rows):
            rows[row], rows[target] = rows[target], rows[row]
    elif direction in {"joinprev", "joinnext"}:
        target = row + (-1 if direction == "joinprev" else 1)
        if 0 <= target < len(rows) and key != "items" and "items" not in rows[target] and len(rows[target]) < 3:
            rows[row].remove(key)
            rows[target].append(key)
    elif direction in {"alone", "first", "last"}:
        rows[row].remove(key)
        target = row + 1 if direction == "alone" else 0 if direction == "first" else len(rows)
        rows.insert(target, [key])
    else:
        raise ValueError("جهت جابه‌جایی معتبر نیست.")
    result["rows"] = [value for value in rows if value]
    return result


class AdminLayouts:
    PAGE_SIZE = 12

    def __init__(self, ui: Any):
        self.ui, self.db = ui, ui.db
        self.engine = LayoutEngine(self.db)

    def authorise(self, user, admin, event=None):
        from .admin_ui import ButtonInputError
        current = self.ui.authorise(user, admin, event)
        if current["role"] not in {"owner", "admin"}:
            raise ButtonInputError("تغییر چیدمان فقط برای مدیر و مالک مجاز است.")
        return current

    def save_state(self, user, state):
        self.db.set_user_state(int(user["id"]), "admin:layouts", state)

    def state(self, user, admin):
        from .admin_ui import ButtonInputError
        stored = self.db.get_user_state(int(user["id"]))
        data = stored["data"] if stored and stored["state"] == "admin:layouts" else {}
        if not data or data.get("actor") != admin["id"] or data.get("chat") != user["chat_id"]:
            raise ButtonInputError("ویرایشگر چیدمان بسته شده است؛ دوباره از مدیریت کلی ربات وارد شوید.")
        return data

    def home(self, user, admin):
        admin = self.authorise(user, admin)
        old = self.db.get_user_state(int(user["id"]))
        state = {"actor": admin["id"], "chat": user["chat_id"], "token": secrets.token_hex(5), "revision": 0,
                 "view": "home", "page": 0, "search": "", "last_input": None,
                 "prompt_message_id": (old or {}).get("data", {}).get("prompt_message_id")}
        self.render(state, user)

    def button(self, state, label, op, value="0", **kwargs):
        return self.ui._button(label, f"l:f:{state['token']}:{state['revision']}:{op}:{value}", **kwargs)

    def choose(self, state, label, value, op="pick", **kwargs):
        index = len(state["options"])
        state["options"].append(value)
        return self.button(state, label, op, str(index), **kwargs)

    def preview_presentation(self, section, key):
        base = section.split(":")[0]
        styles = {
            "main": {"store": "success", "wallet": "success", "profile": "primary", "referral": "primary"},
            "join": {"items": "primary", "check": "success"},
            "product": {"buy": "success", "more": "primary"}, "product_details": {"buy": "success"},
            "wallet": {"receipt": "primary", "cancel": "danger", "invoice": "success", "retry": "primary", "support": "primary", "topup": "success"},
            "referral": {"share": "primary"}, "support": {"new": "primary"}, "ticket": {"reply": "primary"},
            "order_summary": {"pay": "success", "discount": "primary"},
            "payment_methods": {"wallet": "success", "card": "primary", "crypto": "primary"},
            "topup_methods": {"card": "primary", "crypto": "primary"}, "card_payment": {"receipt": "primary", "cancel": "danger"},
            "crypto_payment": {"invoice": "success"},
            "order": {"pay": "success", "invoice": "success", "retry": "primary", "support": "primary", "info": "primary"},
            "input_contact": {"contact": "primary"}, "info_notice": {"info": "primary"},
        }
        presentation = {"style": styles.get(base, {}).get(key)}
        icons = self.ui.controller.settings.button_icon_ids
        if base == "main":
            icon = icons.get({"store": "shop", "profile": "account"}.get(key, key))
        elif base == "card_payment":
            icon = icons.get("copy" if key in {"amount", "card"} else key)
        else:
            icon = None
        if icon:
            presentation["icon_custom_emoji_id"] = icon
        return presentation

    def scoped_records(self, base):
        spec = definition(base)
        table, label = {"category": ("categories", "name"), "product": ("products", "name"),
                        "faq_category": ("faq_categories", "name"), "faq": ("faqs", "question")}[spec.scoped]
        where = " WHERE is_active=1" if spec.scoped == "product" else ""
        return self.ui.controller._query(f"SELECT id,{label} AS label FROM {table}{where} ORDER BY id")

    def entries(self, section):
        base, _, ident = section.partition(":")
        if base == "store":
            return [(f"cat:{row['id']}", row["name"]) for row in self.db.list_categories(parent_id=None, active_only=False)]
        if base == "category" and ident:
            return ([(f"cat:{row['id']}", row["name"]) for row in self.db.list_categories(parent_id=int(ident), active_only=False)] +
                    [(f"prod:{row['id']}", row["name"]) for row in self.db.list_products(category_id=int(ident), visible_only=False) if row["is_active"]])
        if base == "faq_categories":
            return [(f"faqcat:{row['id']}", row["name"]) for row in self.db.list_faq_categories(active_only=False)]
        if base == "faqs" and ident:
            return [(f"faq:{row['id']}", row["question"]) for row in self.db.list_faqs(category_id=int(ident), active_only=False)]
        if base == "join":
            return [(f"join:{row['id']}", row["title"]) for row in self.db.list_force_join_channels(active_only=False)]
        return []

    def ordered_entries(self, state, config=None):
        config = state["draft"] if config is None else config
        positions = {key: index for index, key in enumerate(config["item_order"])}
        entries = sorted(self.entries(state["section"]), key=lambda row: positions.get(row[0], len(positions)))
        return list(reversed(entries)) if config["reverse"] else entries

    def open_editor(self, state, section):
        spec = definition(section)
        if ":" in section:
            records = self.scoped_records(section.split(":")[0])
            if not any(str(row["id"]) == section.split(":")[1] for row in records):
                raise ValueError("بخش انتخاب‌شده دیگر موجود نیست.")
        snapshot = self.engine.snapshot(section)
        state.update(view="editor", section=section, draft=snapshot["config"], version=snapshot["version"],
                     base_version=snapshot["base_version"], can_undo=snapshot["can_undo"], selected=None,
                     page=0, search="", phase="editing", operation="save", title=spec.title)

    def paginated(self, state, rows, options, op="pick"):
        search = state.get("search", "").casefold()
        options = [(label, value) for label, value in options if not search or search in label.casefold()]
        pages = max(1, (len(options) + self.PAGE_SIZE - 1) // self.PAGE_SIZE)
        state["page"] = min(max(0, state.get("page", 0)), pages - 1)
        for label, value in options[state["page"] * self.PAGE_SIZE:(state["page"] + 1) * self.PAGE_SIZE]:
            rows.append([self.choose(state, label, value, op)])
        nav = []
        if state["page"]:
            nav.append(self.button(state, "صفحه قبل", "page", str(state["page"] - 1)))
        if state["page"] + 1 < pages:
            nav.append(self.button(state, "صفحه بعد", "page", str(state["page"] + 1)))
        if nav:
            rows.append(nav)
        if search:
            rows.append([self.button(state, "پاک‌کردن جست‌وجو", "clear")])
        return f"\nصفحه {state['page'] + 1} از {pages} | تعداد: {len(options)}\nبرای جست‌وجو بخشی از نام را پیام کنید."

    def render(self, state, user, note=""):
        from .admin_ui import label_text
        state["revision"] += 1
        state["options"] = []
        rows = []
        text = "<b>چیدمان دکمه‌های کاربران</b>"
        if not self.db.get_setting("customer_layouts_enabled", True):
            text += "\nاعمال چیدمان سفارشی موقتاً توسط اپراتور غیرفعال است؛ ذخیره‌سازی همچنان ممکن است."
        view = state["view"]
        if view == "home":
            text += f"\n{len(SECTIONS)} نوع صفحه؛ تنظیم جداگانه برای دسته‌ها، محصولات و سؤال‌ها نیز موجود است.\nبخش را انتخاب کنید."
            for key, label in GROUPS.items():
                count = sum(spec.group == key for spec in SECTIONS.values())
                rows.append([self.choose(state, f"{label} · {count} بخش", key)])
            rows.append([self.ui._button("بازگشت به مدیریت کلی ربات", "g:settings")])
        elif view == "sections":
            text += "\n" + GROUPS[state["group"]]
            text += self.paginated(state, rows, [(spec.title, key) for key, spec in SECTIONS.items() if spec.group == state["group"]])
            rows.append([self.button(state, "بازگشت به بخش‌ها", "home")])
        elif view == "scopes":
            text += "\n" + SECTIONS[state["base"]].title + "\nالگوی مشترک یا یک مورد مشخص را انتخاب کنید."
            rows.append([self.choose(state, "الگوی مشترک همه موارد", state["base"])])
            text += self.paginated(state, rows, [(label_text(row["label"]) + f" · {row['id']}", f"{state['base']}:{row['id']}")
                                                 for row in self.scoped_records(state["base"])])
            rows.append([self.button(state, "بازگشت به فهرست صفحه‌ها", "sections")])
        else:
            section, config = state["section"], state["draft"]
            spec = definition(section)
            names = dict(spec.slots)
            text += "\n" + escape(spec.title)
            if ":" in section:
                record = next((row for row in self.scoped_records(section.split(":")[0]) if str(row["id"]) == section.split(":")[1]), None)
                text += " / " + escape(label_text(record["label"]) if record else "مورد حذف‌شده")
            text += f"\nنسخه ذخیره‌شده: {state['version']}"
            if view in {"items", "positions"}:
                entries = self.ordered_entries(state)
                text += "\nترتیب گزینه‌ها در کل فهرست و پیش از صفحه‌بندی تغییر می‌کند."
                if view == "positions":
                    text += "\nجایگاه مقصد را انتخاب کنید؛ گزینه انتخاب‌شده به همان شماره منتقل می‌شود."
                text += self.paginated(state, rows, [(f"{index + 1}. {label_text(label)}", key) for index, (key, label) in enumerate(entries)], "destination" if view == "positions" else "item")
                selected = state.get("selected_item")
                if selected and selected in dict(entries) and view == "items":
                    text += "\nانتخاب‌شده: " + escape(label_text(dict(entries)[selected]))
                    rows.extend([[self.button(state, "یک جایگاه بالاتر", "imove", "up"), self.button(state, "یک جایگاه پایین‌تر", "imove", "down")],
                                 [self.button(state, "انتقال به ابتدا", "imove", "first"), self.button(state, "انتقال به انتها", "imove", "last")]])
                    rows.append([self.button(state, "انتخاب جایگاه دلخواه", "positions")])
                rows.append([self.button(state, "بازگشت به پیش‌نمایش", "editor")])
            else:
                text += ("\nپیش‌نمایش امن؛ دکمه‌ها اینجا فقط انتخاب می‌شوند و عملیات خرید/پرداخت اجرا نمی‌شود."
                         "\nگزینه‌های شرطی فقط هنگام مجازبودن به کاربر نمایش داده می‌شوند.")
                if "items" in names:
                    text += "\nفهرست متغیر با حداکثر سه نمونه نمایش داده می‌شود؛ ترتیب کامل از بخش مرتب‌کردن فهرست قابل بررسی است."
                elif len(names) == 1:
                    text += "\nاین صفحه فقط یک دکمه دارد؛ تغییر ترتیب تا اضافه‌شدن دکمه دیگر تفاوت ظاهری ندارد."
                preview_config = state.get("preview_config", config) if state.get("phase") == "confirm" else config
                for row in preview_config["rows"]:
                    if row == ["items"]:
                        examples = self.ordered_entries(state, preview_config)[:3] or [("", f"{names['items']} {index}") for index in range(1, 4)]
                        buttons = [self.choose(state, label_text(label), "items", "select", **self.preview_presentation(section, "items")) for _, label in examples]
                        width = preview_config["columns"]
                        rows.extend(buttons[index:index + width] for index in range(0, len(buttons), width))
                    else:
                        rows.append([self.choose(state, ("انتخاب: " if state.get("selected") == key else "") + names[key], key, "select", **self.preview_presentation(section, key)) for key in row])
                if state.get("phase") == "confirm":
                    text += "\n\n" + {"save": "پس از تأیید، چیدمان برای همه کاربران منتشر می‌شود.", "undo": "پس از تأیید، آخرین چیدمان ذخیره‌شده بازگردانده می‌شود.", "reset": "پس از تأیید، چیدمان اولیهٔ سند بازگردانده می‌شود."}[state["operation"]]
                    rows.extend([[self.button(state, "تأیید انتشار", "confirm", style="success")],
                                 [self.button(state, "بازگشت به ویرایش", "edit")]])
                else:
                    text += "\n\nروی دکمه پیش‌نمایش بزنید، سپس جای آن را تغییر دهید. تغییرات تا انتشار فقط پیش‌نویس‌اند."
                    key = state.get("selected")
                    if key:
                        text += "\nانتخاب‌شده: " + escape(names[key])
                        rows.extend([[self.button(state, "ردیف بالاتر", "move", "up"), self.button(state, "ردیف پایین‌تر", "move", "down")],
                                     [self.button(state, "انتقال به ابتدا", "move", "first"), self.button(state, "انتقال به انتها", "move", "last")]])
                        if key != "items":
                            rows.extend([[self.button(state, "سمت چپ در ردیف", "move", "left"), self.button(state, "سمت راست در ردیف", "move", "right")],
                                         [self.button(state, "کنار ردیف قبلی", "move", "joinprev"), self.button(state, "کنار ردیف بعدی", "move", "joinnext")],
                                         [self.button(state, "ردیف مستقل برای این دکمه", "move", "alone")]])
                    rows.append([self.button(state, label, "preset", str(width)) for width, label in ((1, "تک‌ستونه"), (2, "دوستونه"), (3, "سه‌ستونه"))])
                    if "items" in names:
                        rows.append([self.button(state, f"فهرست: {width} دکمه در ردیف", "columns", str(width)) for width in (1, 2, 3)])
                        if spec.public_items:
                            if self.entries(section):
                                rows.append([self.button(state, "مرتب‌کردن تک‌تک گزینه‌های فهرست", "items")])
                        else:
                            rows.append([self.button(state, "ترتیب داخل هر صفحه: " + ("معکوس" if config["reverse"] else "معمول"), "reverse")])
                    rows.append([self.button(state, "پیش‌نمایش نهایی و انتشار", "prepare", "save", style="success")])
                    if state.get("can_undo"):
                        rows.append([self.button(state, "بازگشت به چیدمان قبلی", "prepare", "undo")])
                    rows.append([self.button(state, "بازنشانی به چیدمان اولیه", "prepare", "reset")])
                    rows.append([self.button(state, "بازخوانی نسخه ذخیره‌شده", "reload")])
                rows.append([self.button(state, "لغو تغییرات و بازگشت", "sections")])
        if note:
            text += "\n\n" + escape(note)
        old_message = state.get("prompt_message_id")
        self.save_state(user, state)
        result = self.ui._send(user, text, rows)
        if isinstance(result, dict) and isinstance(result.get("message_id"), int):
            state["prompt_message_id"] = result["message_id"]
            self.save_state(user, state)
            if old_message != result["message_id"]:
                self.ui._retire_prompt(user, old_message)

    def execute(self, state, event, user, admin):
        admin = self.authorise(user, admin, event)
        try:
            self.db.save_customer_layout(state["section"], state["draft"], expected_version=state["version"],
                                         expected_base_version=state["base_version"], admin_id=admin["id"], chat_id=user["chat_id"],
                                         update_id=state["execution_update"], operation=state["operation"])
        except NotFoundError as exc:
            state.update(view="sections", phase="editing", last_input=self.ui._input_id(event), page=0, search="")
            self.render(state, user, str(exc))
            return
        except (ConflictError, ValidationError) as exc:
            state.update(phase="editing", last_input=self.ui._input_id(event))
            self.render(state, user, str(exc))
            return
        self.open_editor(state, state["section"])
        state["last_input"] = self.ui._input_id(event)
        self.render(state, user, "چیدمان ذخیره شد. از نمایش بعدی این بخش برای کاربران اعمال می‌شود؛ پیام‌های قدیمی تغییر نمی‌کنند.")

    def callback(self, suffix, event, user, admin):
        from .admin_ui import ButtonInputError
        admin = self.authorise(user, admin, event)
        if suffix == "home":
            self.home(user, admin)
            return True
        state = self.state(user, admin)
        identity = self.ui._input_id(event)
        if state.get("phase") == "executing" and state.get("execution_input") == identity:
            self.execute(state, event, user, admin)
            return True
        parts = suffix.split(":")
        if len(parts) != 5 or parts[0] != "f":
            raise ButtonInputError("دکمه چیدمان معتبر نیست.")
        _, token, revision, op, value = parts
        if token != state["token"] or revision != str(state["revision"]) or state.get("last_input") == identity:
            self.render(state, user, "این دکمه مربوط به پیش‌نمایش قبلی بود؛ از دکمه‌های تازه استفاده کنید.")
            return True
        if state.get("phase") == "executing":
            self.render(state, user, "انتشار قبلی هنوز در حال بازیابی است.")
            return True
        if state.get("phase") == "confirm" and op not in {"confirm", "edit", "sections"}:
            self.render(state, user)
            return True
        try:
            if op == "pick":
                chosen = state["options"][int(value)] if value.isdigit() else None
                if state["view"] == "home":
                    state.update(view="sections", group=chosen, page=0, search="")
                elif state["view"] == "sections":
                    if definition(chosen).scoped:
                        state.update(view="scopes", base=chosen, page=0, search="")
                    else:
                        self.open_editor(state, chosen)
                elif state["view"] == "scopes":
                    self.open_editor(state, chosen)
                else:
                    raise ValueError("انتخاب معتبر نیست.")
            elif op == "home":
                state.update(view="home", page=0, search="")
            elif op == "sections":
                state.update(view="sections", group=state.get("group", "main"), phase="editing", page=0, search="")
            elif op in {"page", "clear"}:
                if state["view"] not in {"sections", "scopes", "items", "positions"}:
                    raise ValueError("صفحه‌بندی در این بخش وجود ندارد.")
                state["page"] = int(value) if op == "page" and value.isdigit() else 0
                if op == "clear":
                    state["search"] = ""
            elif state["view"] not in {"editor", "items", "positions"}:
                raise ValueError("ابتدا بخش چیدمان را انتخاب کنید.")
            elif op == "select":
                key = state["options"][int(value)] if value.isdigit() else None
                if key not in dict(definition(state["section"]).slots):
                    raise ValueError("دکمه انتخاب‌شده معتبر نیست.")
                state["selected"] = key
            elif op == "move":
                state["draft"] = move_slot(state["draft"], state.get("selected"), value)
            elif op in {"preset", "columns"}:
                if value not in {"1", "2", "3"}:
                    raise ValueError("تعداد ستون معتبر نیست.")
                if op == "preset":
                    state["draft"] = reflow(state["draft"], int(value))
                else:
                    state["draft"]["columns"] = int(value)
            elif op == "reverse":
                if definition(state["section"]).public_items:
                    raise ValueError("از مرتب‌کردن گزینه‌های فهرست استفاده کنید.")
                state["draft"]["reverse"] = not state["draft"]["reverse"]
            elif op == "items":
                if not definition(state["section"]).public_items:
                    raise ValueError("فهرست عمومی نیست.")
                state.update(view="items", page=0, search="")
            elif op == "item":
                state["selected_item"] = state["options"][int(value)]
            elif op == "positions":
                if state.get("selected_item") not in dict(self.ordered_entries(state)):
                    raise ValueError("ابتدا گزینه فهرست را انتخاب کنید.")
                state.update(view="positions", page=0, search="")
            elif op == "destination":
                entries = [key for key, _ in self.ordered_entries(state)]
                target_key = state["options"][int(value)]
                index, target = entries.index(state.get("selected_item")), entries.index(target_key)
                entries.insert(target, entries.pop(index))
                state["draft"].update(item_order=entries, reverse=False)
                state.update(view="items", search="", page=target // self.PAGE_SIZE)
            elif op == "imove":
                entries = [key for key, _ in self.ordered_entries(state)]
                index = entries.index(state.get("selected_item"))
                target = {"up": max(0, index - 1), "down": min(len(entries) - 1, index + 1), "first": 0, "last": len(entries) - 1}.get(value)
                if target is None:
                    raise ValueError("مقصد جابه‌جایی معتبر نیست.")
                entries.insert(target, entries.pop(index))
                state["draft"].update(item_order=entries, reverse=False)
            elif op in {"editor", "edit"}:
                state.update(view="editor", phase="editing", page=0, search="")
            elif op == "reload":
                self.open_editor(state, state["section"])
            elif op == "prepare":
                if value not in {"save", "undo", "reset"}:
                    raise ValueError("عملیات انتشار معتبر نیست.")
                preview = state["draft"]
                if value == "reset":
                    preview = defaults(state["section"])
                elif value == "undo":
                    document = self.db.get_setting("customer_layout:" + state["section"], {})
                    if not document.get("history"):
                        raise ValueError("چیدمان قبلی وجود ندارد.")
                    inherited = self.engine.snapshot(state["section"].split(":")[0])["config"] if ":" in state["section"] else defaults(state["section"])
                    preview = document["history"][-1] or inherited
                state.update(phase="confirm", operation=value, preview_config=preview)
            elif op == "confirm":
                if state.get("phase") != "confirm" or type(event.get("_admin_update_id")) is not int:
                    raise ValueError("تأیید انتشار معتبر نیست.")
                state.update(phase="executing", execution_update=event["_admin_update_id"], execution_input=identity)
                self.save_state(user, state)
                self.execute(state, event, user, admin)
                return True
            else:
                raise ValueError("عملیات چیدمان شناخته نشد.")
            if state.get("draft"):
                validate(state["section"], state["draft"])
        except (ValueError, TypeError, IndexError, KeyError, StopIteration) as exc:
            raise ButtonInputError("انتخاب یا جابه‌جایی معتبر نیست؛ از پیش‌نمایش تازه استفاده کنید.") from exc
        state["last_input"] = identity
        self.render(state, user)
        return True

    def message(self, event, user, admin):
        admin = self.authorise(user, admin, event)
        state = self.state(user, admin)
        if state["view"] in {"sections", "scopes", "items", "positions"} and isinstance(event.get("text"), str):
            state.update(search=event["text"].strip()[:120], page=0)
            self.render(state, user)
        else:
            self.render(state, user, "در این بخش از دکمه‌ها استفاده کنید؛ نوشتن فرمان یا عدد لازم نیست.")
        return True
