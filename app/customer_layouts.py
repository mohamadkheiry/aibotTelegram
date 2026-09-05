"""Customer keyboard contract and lossless presentation-only layout engine.

Templates contain semantic slots, never customer IDs, URLs or action payloads.
The canonical markup (including its section tag) stays in the outbox; a copy is
arranged at the delivery boundary. Missing/conditional buttons are never added.
"""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass
from typing import Any

from .button_icons import apply_icons


@dataclass(frozen=True)
class Section:
    title: str
    group: str
    slots: tuple[tuple[str, str], ...]
    rows: tuple[tuple[str, ...], ...]
    scoped: str = ""
    public_items: bool = False


SECTIONS: dict[str, Section] = {}
GROUPS = {
    "main": "شروع و منوی اصلی", "catalog": "فروشگاه و محصولات",
    "account": "حساب، کیف پول و دعوت", "support": "پشتیبانی و سؤال‌ها",
    "purchase": "خرید و پرداخت", "input": "فرم‌های ورود اطلاعات",
    "notice": "پیام‌ها و اعلان‌های کاربر",
}


def _section(key: str, title: str, group: str, slots: str, *, rows=None, scoped="", public=False) -> None:
    pairs = tuple(tuple(part.split("=", 1)) for part in slots.split("|"))
    SECTIONS[key] = Section(title, group, pairs, tuple(tuple(row) for row in rows) if rows else
                            tuple((key,) for key, _ in pairs), scoped, public)


NAV = "prev=صفحه قبل|next=صفحه بعد|back=بازگشت"
_section("main", "منوی اصلی", "main", "store=فروشگاه|wallet=کیف پول|profile=حساب من|support=پشتیبانی|referral=دعوت و کسب درآمد|channel=کانال",
         rows=(("store",), ("wallet", "profile"), ("support",), ("referral",), ("channel",)))
_section("join", "عضویت اجباری کاربر", "main", "items=کانال‌های اجباری|prev=صفحه قبل|next=صفحه بعد|check=بررسی عضویت",
         rows=(("items",), ("prev", "next"), ("check",)), public=True)
_section("store", "دسته‌های اصلی فروشگاه", "catalog", "items=دسته‌ها|" + NAV,
         rows=(("items",), ("prev", "next"), ("back",)), public=True)
_section("category", "داخل دسته و زیردسته", "catalog", "items=زیردسته‌ها و محصولات|" + NAV,
         rows=(("items",), ("prev", "next"), ("back",)), scoped="category", public=True)
_section("product", "صفحه محصول", "catalog", "buy=خرید|more=توضیحات تکمیلی|rules=مشاهده قوانین|back=بازگشت", scoped="product")
_section("product_details", "توضیحات کامل محصول", "catalog", "buy=خرید|rules=مشاهده قوانین|back=بازگشت", scoped="product")
_section("profile", "حساب من", "account", "stats=آمار من|orders=سفارش‌های من|transactions=تراکنش‌های من|back=بازگشت")
_section("stats", "آمار من", "account", "back=بازگشت")
_section("orders", "فهرست سفارش‌های من", "account", "items=دکمه‌های سفارش|" + NAV,
         rows=(("items",), ("prev", "next"), ("back",)))
_section("transactions", "تراکنش‌های من", "account", NAV, rows=(("prev", "next"), ("back",)))
_section("wallet", "کیف پول و پرداخت‌های باز", "account", "receipt=ارسال فیش واریز|cancel=لغو پرداخت|invoice=ادامه پرداخت ارزی|retry=تلاش دوباره برای پرداخت ارزی|support=پیگیری از پشتیبانی|topup=افزایش موجودی|back=بازگشت")
_section("referral", "دعوت و کسب درآمد", "account", "share=ارسال لینک به دوستان|back=بازگشت")
_section("channel", "ورود به کانال رسمی", "main", "channel=ورود به کانال")
_section("support", "منوی پشتیبانی", "support", "faqs=سوالات متداول|new=ثبت تیکت|tickets=تیکت‌های قبلی|back=بازگشت")
_section("faq_categories", "دسته‌های سؤال‌های متداول", "support", "items=دسته‌های سؤال|" + NAV,
         rows=(("items",), ("prev", "next"), ("back",)), public=True)
_section("faqs", "سؤال‌های یک دسته", "support", "items=سؤال‌ها|" + NAV,
         rows=(("items",), ("prev", "next"), ("back",)), scoped="faq_category", public=True)
_section("faq", "پاسخ سؤال متداول", "support", "back=بازگشت", scoped="faq")
_section("tickets", "فهرست تیکت‌های من", "support", "items=دکمه‌های تیکت|" + NAV,
         rows=(("items",), ("prev", "next"), ("back",)))
_section("ticket", "گفت‌وگوی تیکت و پیوست‌ها", "support", "items=دریافت پیوست|prev=قدیمی‌تر|next=جدیدتر|reply=ارسال پاسخ|back=بازگشت",
         rows=(("items",), ("prev", "next"), ("reply",), ("back",)))
_section("order_summary", "خلاصه و تأیید سفارش", "purchase", "pay=پرداخت|discount=ثبت کد تخفیف|back=بازگشت")
_section("payment_methods", "انتخاب روش پرداخت سفارش", "purchase", "wallet=کیف پول|card=کارت به کارت|crypto=پرداخت ارزی|back=بازگشت")
_section("topup_methods", "انتخاب روش شارژ کیف پول", "purchase", "card=کارت به کارت|crypto=پرداخت ارزی|back=بازگشت")
_section("card_payment", "پرداخت کارت‌به‌کارت", "purchase", "amount=کپی مبلغ|card=کپی شماره کارت|receipt=ارسال فیش واریز|cancel=لغو پرداخت")
_section("crypto_payment", "پرداخت ارزی", "purchase", "invoice=رفتن به صفحه پرداخت|back=بازگشت")
_section("crypto_error", "لینک نامعتبر پرداخت ارزی", "purchase", "back=بازگشت")
_section("order", "جزئیات و پیگیری سفارش", "purchase", "pay=ادامه پرداخت|receipt=ارسال فیش واریز|invoice=ادامه پرداخت ارزی|retry=تلاش دوباره برای پرداخت ارزی|support=پیگیری از پشتیبانی|info=ارسال اطلاعات|back=بازگشت")
for _key, _title in (("name", "نام خریدار"), ("discount", "کد تخفیف"), ("topup", "مبلغ شارژ"),
                     ("receipt", "ارسال فیش"), ("order_info", "اطلاعات سفارش دستی"),
                     ("ticket_subject", "موضوع تیکت"), ("ticket_body", "متن تیکت"), ("ticket_reply", "پاسخ تیکت")):
    _section("input_" + _key, "فرم " + _title, "input", "cancel=لغو و بازگشت")
_section("input_contact", "فرم ارسال شماره موبایل", "input", "contact=ارسال شماره موبایل|cancel=لغو و بازگشت")
_section("discount_error", "خطای کد تخفیف", "input", "back=بازگشت")
_section("order_notice", "اعلان ثبت اطلاعات، رزرو و تأمین سفارش", "notice", "order=مشاهده سفارش")
_section("info_notice", "اعلان درخواست اطلاعات سفارش", "notice", "info=ارسال اطلاعات")
_section("wallet_notice", "اعلان نتیجه پرداخت کیف پول", "notice", "wallet=کیف پول")


def definition(section: str) -> Section:
    if not isinstance(section, str) or not re.fullmatch(r"[a-z_]+(?::[1-9][0-9]{0,18})?", section):
        raise ValueError("بخش چیدمان معتبر نیست.")
    base, _, ident = section.partition(":")
    spec = SECTIONS.get(base)
    if spec is None or ident and (not spec.scoped or int(ident) >= 2**63):
        raise ValueError("بخش چیدمان شناخته نشد.")
    return spec


def defaults(section: str) -> dict:
    spec = definition(section)
    return {"rows": [list(row) for row in spec.rows], "columns": 1, "item_order": [], "reverse": False}


def validate(section: str, config: dict) -> dict:
    spec = definition(section)
    if not isinstance(config, dict) or set(config) != {"rows", "columns", "item_order", "reverse"}:
        raise ValueError("ساختار چیدمان معتبر نیست.")
    rows = config["rows"]
    if not isinstance(rows, list) or not rows or any(not isinstance(row, list) or not 1 <= len(row) <= 3 for row in rows):
        raise ValueError("هر ردیف باید یک تا سه دکمه داشته باشد.")
    keys = [key for row in rows for key in row]
    if any(not isinstance(key, str) for key in keys) or sorted(keys) != sorted(dict(spec.slots)):
        raise ValueError("همه دکمه‌های بخش باید دقیقاً یک بار در چیدمان باشند.")
    if any("items" in row and len(row) != 1 for row in rows):
        raise ValueError("فهرست متغیر باید ردیف مستقل داشته باشد.")
    if type(config["columns"]) is not int or not 1 <= config["columns"] <= 3 or type(config["reverse"]) is not bool:
        raise ValueError("تعداد ستون یا جهت فهرست معتبر نیست.")
    order = config["item_order"]
    if not isinstance(order, list) or len(order) > 10000 or any(not isinstance(key, str) or not re.fullmatch(r"(?:cat|prod|faqcat|faq|join):[1-9][0-9]{0,18}", key) for key in order):
        raise ValueError("ترتیب گزینه‌های فهرست معتبر نیست.")
    if len(set(order)) != len(order) or order and not spec.public_items:
        raise ValueError("ترتیب فهرست تکراری یا غیرمجاز است.")
    prefixes = {"store": {"cat"}, "category": {"cat", "prod"}, "faq_categories": {"faqcat"}, "faqs": {"faq"}, "join": {"join"}}
    if any(key.split(":")[0] not in prefixes.get(section.split(":")[0], set()) or int(key.split(":")[1]) >= 2**63 for key in order):
        raise ValueError("گزینه فهرست با نوع این بخش منطبق نیست.")
    return copy.deepcopy(config)


def tagged(section: str, markup: dict) -> dict:
    definition(section)
    return {**markup, "_customer_layout": section}


def keyboard(section: str, rows: list) -> dict:
    from .keyboards import inline_keyboard
    return tagged(section, inline_keyboard(rows))


def slot(section: str, button: dict) -> str | None:
    # The admin entry is not a customer action and stays separate at the end.
    if button.get("callback_data", "").startswith("adm:") or (not button.get("callback_data") and button.get("text") == "پنل مدیریت"):
        return None
    spec = definition(section)
    if button.get("_layout_slot") in dict(spec.slots):
        return button["_layout_slot"]
    # Catalog names are untrusted data, including names identical to Back/Next.
    if spec.public_items and (button.get("_layout_item") or
                              re.fullmatch(r"(?:cat|prod|faqcat|faq):[1-9][0-9]*", str(button.get("callback_data", "")))):
        return "items"
    labels = {label: key for key, label in spec.slots if key != "items"}
    if button.get("text") in labels:
        return labels[button["text"]]
    if "items" in dict(spec.slots):
        return "items"
    return None


def item_key(button: dict) -> str:
    return str(button.get("_layout_item") or button.get("callback_data") or "")


def arrange(section: str, markup: dict, config: dict) -> dict:
    """Only permute/reflow existing buttons. Never infer or synthesize actions."""
    config = validate(section, config)
    result = copy.deepcopy(markup)
    kind = "inline_keyboard" if "inline_keyboard" in result else "keyboard"
    original = result.get(kind, [])
    buckets: dict[str | None, list] = {}
    for row in original:
        for button in row:
            buckets.setdefault(slot(section, button), []).append(button)
    rows = []
    for configured in config["rows"]:
        if configured == ["items"]:
            items = buckets.pop("items", [])
            # Public lists have already been ordered before pagination.
            if config["reverse"] and not definition(section).public_items:
                items.reverse()
            width = config["columns"]
            rows.extend(items[index:index + width] for index in range(0, len(items), width))
        else:
            row = [button for key in configured for button in buckets.pop(key, [])]
            rows.extend(row[index:index + 3] for index in range(0, len(row), 3))
    # Unknown/new actions and the admin entry remain reachable, in input order.
    for remainder in buckets.values():
        rows.extend([button] for button in remainder)
    result[kind] = rows
    return result


def clean_markup(markup: Any) -> Any:
    if isinstance(markup, str):
        try:
            return json.dumps(clean_markup(json.loads(markup)), ensure_ascii=False)
        except (TypeError, ValueError):
            return markup
    if not isinstance(markup, dict):
        return markup
    result = {key: copy.deepcopy(value) for key, value in markup.items() if key != "_customer_layout"}
    for kind in ("inline_keyboard", "keyboard"):
        if isinstance(result.get(kind), (list, tuple)):
            for row in result[kind]:
                if not isinstance(row, (list, tuple)):
                    continue
                for button in row:
                    if isinstance(button, dict):
                        button.pop("_layout_item", None)
                        button.pop("_layout_slot", None)
    return result


def same_canonical_markup(first: str | None, second: str | None) -> bool:
    """Legacy outbox compatibility: ignore ONLY internal presentation tags.

    Recipient/body checks remain at the caller; rows and every real button
    property (including URL/callback/copy data) must still match exactly.
    """
    if first == second:
        return True
    if first is None or second is None:
        return False
    try:
        return clean_markup(json.loads(first)) == clean_markup(json.loads(second))
    except (ValueError, TypeError, AttributeError):
        return False


class LayoutEngine:
    def __init__(self, db: Any, button_icon_ids=None):
        self.db = db
        self.button_icon_ids = dict(button_icon_ids or {})

    def snapshot(self, section: str) -> dict:
        definition(section)
        document = self.db.get_setting("customer_layout:" + section, {})
        base = section.split(":")[0]
        parent = self.db.get_setting("customer_layout:" + base, {}) if ":" in section else {}
        document = document if isinstance(document, dict) else {}
        parent = parent if isinstance(parent, dict) else {}
        config = document.get("current") or parent.get("current") or defaults(section)
        try:
            config = validate(section, config)
        except (TypeError, ValueError):
            config = defaults(section)
        return {"config": config, "version": document.get("version", 0), "base_version": parent.get("version", 0),
                "can_undo": bool(document.get("history")), "custom": bool(document.get("current"))}

    def prepare(self, markup: Any) -> Any:
        original = markup
        encoded = isinstance(markup, str)
        if encoded:
            try:
                markup = json.loads(markup)
            except (TypeError, ValueError):
                return markup
        if not isinstance(markup, dict):
            return original
        section = markup.get("_customer_layout")
        if section and self.db.get_setting("customer_layouts_enabled", True):
            try:
                config = self.snapshot(section)["config"]
                # Preserve the original default exactly, including repeated
                # per-payment controls in legacy wallets with multiple intents.
                if config != defaults(section):
                    markup = arrange(section, markup, config)
            except (TypeError, ValueError):
                # Invalid presentation must not strand a paid notification.
                pass
        result = clean_markup(apply_icons(markup, self.button_icon_ids))
        return json.dumps(result, ensure_ascii=False) if encoded else result

    def order_items(self, section: str, entries: list, key) -> list:
        if not self.db.get_setting("customer_layouts_enabled", True):
            return list(entries)
        config = self.snapshot(section)["config"]
        positions = {value: index for index, value in enumerate(config["item_order"])}
        result = sorted(entries, key=lambda entry: positions.get(key(entry), len(positions)))
        return list(reversed(result)) if config["reverse"] else result


class LayoutTelegram:
    """One transparent outbound boundary, also used with the fake transport.

    Polling and all non-presentation calls delegate to the original instance.
    No new client/session, polling loop, outbox or network request is created.
    """
    METHODS = frozenset({"send_message", "edit_message_text", "edit_message_reply_markup",
                         "send_document", "send_photo", "copy_message"})

    def __init__(self, transport: Any, engine: LayoutEngine):
        self.transport = transport
        self.engine = engine

    def __getattr__(self, name: str) -> Any:
        method = getattr(self.transport, name)
        if name == "call":
            def call(api_method, *args, **kwargs):
                args = list(args)
                if args and isinstance(args[0], dict) and "reply_markup" in args[0]:
                    args[0] = {**args[0], "reply_markup": self.engine.prepare(args[0]["reply_markup"])}
                for parameter in ("payload", "params"):
                    if isinstance(kwargs.get(parameter), dict) and "reply_markup" in kwargs[parameter]:
                        kwargs[parameter] = {**kwargs[parameter], "reply_markup": self.engine.prepare(kwargs[parameter]["reply_markup"])}
                return method(api_method, *args, **kwargs)
            return call
        if name not in self.METHODS:
            return method

        def send(*args, **kwargs):
            # This is the one wrapper whose markup is not keyword-only.
            if name == "edit_message_reply_markup" and len(args) >= 3:
                args = (*args[:2], self.engine.prepare(args[2]), *args[3:])
            if "reply_markup" in kwargs:
                kwargs["reply_markup"] = self.engine.prepare(kwargs["reply_markup"])
            return method(*args, **kwargs)
        return send
