"""Minimal, presentation-only custom emoji icons; never decorate label text.

The manifest contains public Telegram custom-emoji IDs, not file IDs or tokens.
An empty mapping keeps the historical no-icon fallback. Action payloads, order,
conditional availability, explicit icons and canonical outbox data are immutable.
"""
from __future__ import annotations

import copy
import re
from collections.abc import Mapping
from typing import Any


ICON_SOURCES = {
    "shop": "store", "wallet": "wallet", "account": "user-round", "support": "headset",
    "referral": "gift", "channel": "megaphone", "buy": "shopping-bag", "info": "info",
    "back": "chevron-left", "pay": "badge-check", "discount": "ticket-percent", "card": "credit-card",
    "crypto": "bitcoin", "copy": "copy", "receipt": "receipt-text", "cancel": "x",
    "order": "clipboard-list", "upload": "upload", "phone": "smartphone", "settings": "settings-2",
    "catalog": "package", "users": "users-round", "tickets": "messages-square", "faq": "circle-help",
    "reports": "chart-no-axes-combined", "admins": "shield-user", "inventory": "boxes", "broadcast": "send",
    "layout": "panels-top-left", "plus": "plus", "pencil": "pencil", "delete": "trash-2",
    "search": "search", "refresh": "refresh-cw", "check": "check", "next": "chevron-right",
    "up": "chevron-up", "down": "chevron-down", "power": "power", "calendar": "calendar-days",
    "folder": "folder", "download": "download", "menu": "house", "list": "list", "lock": "lock-keyhole",
}

GROUP_ICONS = {"settings": "settings", "catalog": "catalog", "orders": "order", "payments": "pay",
               "inventory": "inventory", "users": "users", "tickets": "tickets", "faq": "faq",
               "discounts": "discount", "rewards": "referral", "broadcast": "broadcast",
               "reports": "reports", "admins": "admins"}

LABEL_ICONS = {
    "فروشگاه": "shop", "کیف پول": "wallet", "حساب من": "account", "پشتیبانی": "support",
    "دعوت و کسب درآمد": "referral", "کانال": "channel", "پنل مدیریت": "settings", "منوی اصلی": "menu",
    "خرید": "buy", "پرداخت": "pay", "ادامه پرداخت": "pay", "کارت بانکی": "card", "کارت به کارت": "card",
    "پرداخت ارزی": "crypto", "رمزارز": "crypto", "رفتن به صفحه پرداخت": "crypto", "ادامه پرداخت ارزی": "crypto",
    "افزایش موجودی": "plus", "سوالات متداول": "faq", "سؤالات متداول": "faq", "ثبت تیکت": "plus",
    "تیکت‌های قبلی": "tickets", "آمار من": "reports", "سفارش‌های من": "order", "تراکنش‌های من": "receipt",
    "مشاهده سفارش": "order", "ارسال پاسخ": "broadcast", "ارسال لینک به دوستان": "referral",
    "بررسی عضویت": "check", "ورود به کانال": "channel", "ارسال اطلاعات": "upload", "دریافت پیوست": "download",
    "فعال": "check", "غیرفعال": "cancel", "بله": "check", "خیر": "cancel", "باز": "check", "بسته": "lock",
    "همه / بدون محدودیت": "list", "قدیمی‌تر": "back", "جدیدتر": "next",
}


def validate_icon_ids(values: Any) -> dict[str, str]:
    if not isinstance(values, Mapping) or any(key not in ICON_SOURCES for key in values):
        raise ValueError("Icon manifest must contain only known semantic icon names")
    if any(not isinstance(value, str) or not re.fullmatch(r"[0-9]{5,30}", value) for value in values.values()):
        raise ValueError("Icon manifest values must be numeric custom-emoji ID strings")
    return dict(values)


def icon_key(button: Mapping[str, Any]) -> str:
    if button.get("request_contact"):
        return "phone"
    if button.get("copy_text"):
        return "copy"
    if button.get("_layout_slot") in {"back", "prev", "next"}:
        return "next" if button["_layout_slot"] == "next" else "back"
    data = str(button.get("callback_data") or "")
    text = str(button.get("text") or "").removeprefix("انتخاب: ")
    # Public catalog labels are data; do not infer a destructive-action icon
    # from a product which happens to be named "حذف" or "بازگشت".
    if re.fullmatch(r"(?:cat|prod|faqcat|faq):[1-9][0-9]*", data):
        return {"cat": "folder", "prod": "catalog", "faqcat": "folder", "faq": "faq"}[data.split(":")[0]]
    if data.startswith("adm:ui:g:"):
        return GROUP_ICONS.get(data.split(":")[3], "list")
    if data == "adm:ui:l:home":
        return "layout"
    if text in LABEL_ICONS:
        return LABEL_ICONS[text]
    for prefixes, key in (
        (("لغو",), "cancel"), (("بازگشت به چیدمان قبلی", "بازنشانی", "بازخوانی", "تلاش دوباره"), "refresh"),
        (("بازگشت", "مرحله قبل", "صفحه قبل", "سمت چپ"), "back"),
        (("صفحه بعد", "مرحله بعد", "سمت راست"), "next"), (("حذف",), "delete"),
        (("افزودن", "اضافه", "ثبت کد تخفیف"), "plus"), (("ویرایش", "اصلاح"), "pencil"),
        (("تأیید", "تایید", "ذخیره"), "check"), (("رد پرداخت",), "cancel"),
        (("فعال", "غیرفعال", "حالت تعمیرات", "نمایش /", "نمایش و", "تغییر وضعیت"), "power"),
        (("چیدمان", "پیش‌نمایش", "تک‌ستونه", "دوستونه", "سه‌ستونه", "فهرست:", "مرتب‌کردن", "ردیف مستقل", "کنار ردیف"), "layout"),
        (("ردیف بالاتر", "یک جایگاه بالاتر", "انتقال به ابتدا"), "up"),
        (("ردیف پایین‌تر", "یک جایگاه پایین‌تر", "انتقال به انتها"), "down"),
        (("جست‌وجو", "پاک‌کردن جست‌وجو"), "search"), (("ارسال فیش",), "receipt"),
        (("عضویت در کانال", "کانال"), "channel"), (("توضیحات", "مشاهده قوانین", "راهنما"), "info"),
        (("دریافت", "نسخه پشتیبان"), "download"), (("ارسال",), "broadcast"),
        (("تاریخ", "امروز", "فردا", "دیروز"), "calendar"),
    ):
        if text.startswith(prefixes):
            return key
    if data.startswith(("order:", "ordersummary:")):
        return "order"
    if data.startswith(("ticket:", "ticketfile:", "ticketreply:")):
        return "tickets"
    if "url" in button:
        return "channel" if str(button["url"]).startswith("https://t.me/") else "info"
    return "list"


def apply_icons(markup: dict, icons: Mapping[str, str]) -> dict:
    result = copy.deepcopy(markup)
    if not icons:
        return result
    for kind in ("inline_keyboard", "keyboard"):
        if not isinstance(result.get(kind), (list, tuple)):
            continue
        result[kind] = [list(row) if isinstance(row, (list, tuple)) else row for row in result[kind]]
        for row in result[kind]:
            if not isinstance(row, list):
                continue
            for index, original in enumerate(row):
                button = {"text": original} if isinstance(original, str) else original
                if not isinstance(button, dict) or button.get("icon_custom_emoji_id"):
                    continue
                chosen = icons.get(icon_key(button))
                if chosen:
                    button["icon_custom_emoji_id"] = chosen
                    row[index] = button
    return result
