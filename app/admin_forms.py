"""Declarative, Persian button forms over the existing admin domain handlers.

Values are collected separately: a title or message containing ``|`` is data,
never command syntax. Internal command names remain a compatibility boundary.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    kind: str = "text"
    options: tuple[tuple[str, str], ...] = ()
    default: str | None = None
    hint: str = ""


@dataclass(frozen=True)
class Action:
    key: str
    label: str
    group: str
    command: str
    fields: tuple[Field, ...] = ()
    mutation: bool = False
    separator: str = " "


GROUPS = {
    "orders": "سفارش‌ها", "payments": "پرداخت‌ها", "catalog": "دسته‌ها و محصولات",
    "inventory": "انبار", "users": "کاربران", "tickets": "تیکت‌ها",
    "faq": "سؤالات متداول", "discounts": "تخفیف‌ها", "rewards": "دعوت و پاداش",
    "broadcast": "پیام گروهی", "reports": "گزارش‌ها", "settings": "تنظیمات ربات",
    "admins": "مدیران و پشتیبان‌ها",
}
# Product specification, PDF 1 pp. 1-3: exactly nine business sections.
# The other stable keys remain usable by historical callbacks, but are nested.
MAIN_GROUPS = ("settings", "catalog", "orders", "tickets", "users", "broadcast", "faq", "discounts", "reports")
GROUP_PARENTS = {"payments": "orders", "inventory": "catalog", "rewards": "settings", "admins": "settings"}
GROUPS.update(settings="مدیریت کلی ربات", catalog="محصولات", broadcast="ارسال پیام", reports="گزارش")
ORDER_OPTIONS = (
    ("همه وضعیت‌ها", "all"), ("در انتظار پرداخت", "pending_payment"),
    ("در انتظار تأیید", "awaiting_confirmation"), ("پرداخت‌شده", "paid"),
    ("در صف موجودی", "awaiting_stock"), ("در انتظار اطلاعات", "awaiting_info"),
    ("در حال انجام", "processing"), ("تکمیل‌شده", "completed"),
    ("ردشده", "rejected"), ("منقضی", "expired"), ("لغوشده", "cancelled"),
    ("بازپرداخت‌شده", "refunded"),
)
TICKET_OPTIONS = (("باز", "open"), ("پاسخ‌داده‌شده", "answered"), ("بسته", "closed"))
YES_NO = (("بله", "true"), ("خیر", "false"))
PRODUCT_FIELDS = (
    ("نام", "name"), ("دسته", "category"), ("نوع تحویل", "type"), ("آیکون", "icon"),
    ("توضیح کوتاه", "short_description"), ("توضیح کامل", "long_description"),
    ("قیمت به تومان", "price"), ("عنوان مدت", "duration"), ("مدت به روز", "duration_days"),
    ("نوع اکانت", "account_type"), ("روش فعال‌سازی", "activation"),
    ("قابل تمدید", "renewable"), ("گارانتی", "warranty"), ("ویژگی‌ها", "features"),
    ("راهنمای فعال‌سازی", "activation_instructions"), ("شرایط استفاده", "usage_terms"),
    ("قوانین", "rules"), ("لینک قوانین", "rules_url"),
    ("متن درخواست اطلاعات", "info_request_text"), ("متن تکمیل سفارش", "completion_text"),
    ("راهنمای تحویل", "delivery_instructions"), ("روزهای یادآوری", "reminder_days"),
    ("سقف موجودی دستی", "stock_limit"),
)


def choice(key: str, label: str, options: tuple[tuple[str, str], ...]) -> Field:
    return Field(key, label, "choice", options)


def entity(kind: str, label: str, key: str = "target", default: str | None = None) -> Field:
    return Field(key, label, "entity:" + kind, default=default)


def date_field(key: str, label: str, optional: bool = False) -> Field:
    return Field(key, label, "date", default="0" if optional else None,
                 hint="تاریخ میلادی با قالب YYYY-MM-DD؛ یا انتخاب تاریخ از دکمه‌ها.")


START = date_field("start", "از تاریخ")
END = date_field("end", "تا تاریخ")
USER = entity("user", "انتخاب کاربر")
PRODUCT = entity("product", "انتخاب محصول")
CATEGORY = entity("category", "انتخاب دسته")
ORDER = entity("order", "انتخاب سفارش")
TICKET = entity("ticket", "انتخاب تیکت")
PAYMENT = entity("payment", "انتخاب پرداخت")
INVENTORY = entity("inventory", "انتخاب آیتم انبار")
FAQ_CATEGORY = entity("faq_category", "انتخاب دسته پرسش‌ها")
FAQ = entity("faq", "انتخاب پرسش")
NOTE = Field("note", "توضیح")
BODY = Field("body", "متن پیام", hint="متن را در یک پیام بفرستید. قالب HTML فقط با پیشوند html: فعال می‌شود.")
TITLE = Field("name", "عنوان")
AMOUNT = Field("amount", "مبلغ به تومان", "positive")
STATUS = choice("status", "وضعیت سفارش", ORDER_OPTIONS)
RULE_PRODUCT = entity("product", "محدود به محصول", "product", "0")

ACTIONS: dict[str, Action] = {}


def add(key: str, label: str, group: str, *fields: Field,
        mutation: bool = False, pipe: bool = False, command: str | None = None) -> None:
    ACTIONS[key] = Action(key, label, group, command or "/" + key,
                          tuple(fields), mutation, " | " if pipe else " ")


add("admin_help", "راهنمای دکمه‌ها", "settings")
add("bot_on", "فعال‌کردن ربات", "settings", mutation=True)
add("bot_off", "غیرفعال‌کردن ربات", "settings", mutation=True)
add("set_card", "تنظیم کارت بانکی", "settings", Field("number", "شماره کارت", "card"),
    Field("holder", "نام صاحب کارت"), mutation=True, pipe=True)
add("set_channel", "تنظیم کانال اصلی", "settings", Field("url", "لینک کانال", "url"), mutation=True)
add("payment", "فعال یا غیرفعال‌کردن روش پرداخت", "settings",
    choice("method", "روش پرداخت", (("کیف پول", "wallet"), ("کارت بانکی", "card"), ("رمزارز", "crypto"))),
    choice("enabled", "وضعیت جدید", (("فعال", "on"), ("غیرفعال", "off"))), mutation=True)
add("joins", "کانال‌های جوین اجباری", "settings")
add("join_add", "افزودن کانال اجباری", "settings", Field("channel", "یوزرنیم یا شناسه کانال"),
    TITLE, Field("url", "لینک عضویت", "url"), mutation=True, pipe=True)
for key, label in (("join_toggle", "تغییر وضعیت کانال اجباری"), ("join_delete", "حذف کانال اجباری")):
    add(key, label, "settings", entity("join", "انتخاب کانال"), mutation=True)
add("backup", "دریافت نسخه پشتیبان", "settings", mutation=True)
add("admins", "فهرست مدیران", "admins")
add("admin_add", "افزودن یا تغییر نقش مدیر", "admins", Field("username", "یوزرنیم مدیر", "username"),
    Field("chat", "چت‌آی‌دی عددی مدیر", "positive"),
    choice("role", "نقش مدیر", (("مدیر", "admin"), ("پشتیبان", "support"), ("مالک", "owner"))), mutation=True)
for key, label in (("admin_enable", "فعال‌کردن مدیر"), ("admin_disable", "غیرفعال‌کردن مدیر")):
    add(key, label, "admins", entity("admin", "انتخاب مدیر"), mutation=True)
add("categories", "فهرست دسته‌ها", "catalog")
add("category_add", "افزودن دسته اصلی", "catalog", TITLE,
    Field("icon", "آیکون دسته", default="0"), Field("description", "توضیح دسته", default="0"),
    mutation=True, pipe=True)
add("subcategory_add", "افزودن زیردسته", "catalog", CATEGORY, TITLE,
    Field("icon", "آیکون دسته", default="0"), Field("description", "توضیح دسته", default="0"),
    mutation=True, pipe=True)
for key, label in (("category_toggle", "فعال یا غیرفعال‌کردن دسته"), ("category_delete", "حذف دسته")):
    add(key, label, "catalog", CATEGORY, mutation=True)
add("category_set", "ویرایش دسته", "catalog", CATEGORY,
    choice("field", "ویژگی دسته", (("نام", "name"), ("والد", "parent"), ("آیکون", "icon"),
                                  ("توضیح", "description"), ("ترتیب نمایش", "sort_order"))),
    Field("value", "مقدار جدید", "dynamic"), mutation=True, pipe=True)
add("products", "فهرست محصولات", "catalog", replace(CATEGORY, default="all"))
add("product_add", "افزودن محصول", "catalog", CATEGORY, TITLE, AMOUNT,
    Field("duration", "مدت اشتراک", hint="مثلاً ۳۰ روز؛ مدت عددی بر حسب روز محاسبه می‌شود."),
    choice("type", "نوع تحویل", (("آماده و خودکار", "ready"), ("فعال‌سازی دستی", "manual"))),
    mutation=True, pipe=True)
add("product_set", "ویرایش مشخصات محصول", "catalog", PRODUCT,
    choice("field", "ویژگی محصول", PRODUCT_FIELDS), Field("value", "مقدار جدید", "dynamic"),
    mutation=True, pipe=True)
add("product_toggle", "تغییر نمایش یا فروش محصول", "catalog", PRODUCT,
    choice("field", "ویژگی مورد تغییر", (("نمایش در فروشگاه", "visible"),
                                         ("قابل خرید", "available"), ("امکان رزرو", "reserve"))), mutation=True)
add("product_delete", "حذف محصول", "catalog", PRODUCT, mutation=True)
add("inventory_list", "فهرست موجودی محصول", "inventory", PRODUCT)
add("inventory_add", "افزودن اکانت آماده", "inventory", entity("ready_product", "محصول آماده"),
    Field("secret", "اطلاعات محرمانه اکانت", "secret", hint="کل اطلاعات تحویل را در یک پیام متنی بفرستید؛ در پیش‌نمایش بازنشر نمی‌شود."), mutation=True)
add("inventory_edit", "ویرایش اطلاعات اکانت", "inventory", INVENTORY,
    Field("secret", "اطلاعات محرمانه جدید", "secret"), mutation=True)
for key, label in (("inventory_disable", "غیرفعال‌کردن اکانت"), ("inventory_enable", "فعال‌کردن اکانت"),
                   ("inventory_delete", "حذف اکانت")):
    add(key, label, "inventory", INVENTORY, mutation=True)
add("inventory_assign", "تخصیص مستقیم اکانت به کاربر", "inventory", INVENTORY,
    replace(USER, key="user"), mutation=True)
add("orders", "فهرست و فیلتر سفارش‌ها", "orders", STATUS,
    choice("range", "بازه ثبت سفارش", (("همه تاریخ‌ها", "all"), ("انتخاب بازه", "custom"))))
add("order", "مشاهده جزئیات سفارش", "orders", ORDER)
add("order_attachment", "دریافت پیوست اطلاعات سفارش", "orders", ORDER)
add("order_status", "تغییر وضعیت سفارش", "orders", ORDER,
    choice("status", "وضعیت جدید", tuple(pair for pair in ORDER_OPTIONS if pair[1] not in {
        "all", "paid", "completed", "refunded", "awaiting_stock", "awaiting_info",
    })), Field("note", "توضیح برای کاربر", default=""), mutation=True)
add("complete", "تکمیل سفارش دستی", "orders", entity("manual_order", "انتخاب سفارش دستی"),
    Field("delivery", "متن کامل تحویل", "secret", hint="متن نهایی تحویل را بفرستید؛ اطلاعات محرمانه در پیش‌نمایش بازنشر نمی‌شود."), mutation=True, pipe=True)
add("request_info", "درخواست اصلاح اطلاعات", "orders", ORDER, BODY, mutation=True, pipe=True)
add("payment_detail", "جزئیات پرداخت و دریافت فیش", "payments", PAYMENT)
add("approve_payment", "تأیید فیش پرداخت", "payments", entity("receipt", "انتخاب فیش در انتظار بررسی"), mutation=True)
add("reject_payment", "رد فیش پرداخت", "payments", entity("receipt", "انتخاب فیش در انتظار بررسی"),
    NOTE, mutation=True, pipe=True)
add("card_reviews", "رخدادهای بانکی نیازمند بررسی", "payments")
add("crypto_reviews", "رخدادهای ارزی نیازمند بررسی", "payments")
for key, kind, label, options in (
    ("card_resolve", "card_review", "تعیین تکلیف رخداد بانکی",
     (("بستن بدون اعتباردهی", "dismiss"), ("تأیید بازپرداخت انجام‌شده", "refund_confirmed"))),
    ("crypto_resolve", "crypto_review", "تعیین تکلیف رخداد ارزی",
     (("بستن بدون اعتباردهی", "dismiss"), ("تأیید بازپرداخت انجام‌شده", "refund_confirmed"),
      ("اعتباردهی با شاهد قطعی پرداخت", "credit_confirmed"))),
):
    add(key, label, "payments", entity(kind, "انتخاب رخداد"), choice("resolution", "نتیجه بررسی", options),
        NOTE, mutation=True)
add("users", "فهرست و فیلتر کاربران", "users", choice("mode", "فیلتر کاربران", (
    ("همه کاربران", "all"), ("فعال", "active"), ("مسدود", "blocked"),
    ("جدید در چند روز اخیر", "new"), ("بدون فعالیت در چند روز اخیر", "inactive"),
    ("عضویت در بازه", "joined"), ("خریداران محصول در بازه", "product"))))
for key, label in (("user", "مشخصات کامل کاربر"), ("user_transactions", "تراکنش‌های کاربر"),
                   ("user_referrals", "زیرمجموعه‌های کاربر"), ("user_rewards", "پاداش‌های کاربر")):
    add(key, label, "users", USER)
add("user_orders", "سفارش‌های کاربر", "users", USER, STATUS)
for key, label in (("block", "مسدودکردن کاربر"), ("unblock", "رفع مسدودی کاربر")):
    add(key, label, "users", USER, mutation=True)
add("wallet_adjust", "اصلاح موجودی کیف پول", "users", USER,
    Field("amount", "مبلغ تغییر به تومان", "signed", hint="افزایش: عدد مثبت؛ کاهش: عدد منفی. مثلاً ۵۰۰۰۰ یا -۵۰۰۰۰."),
    NOTE, mutation=True, pipe=True)
add("message", "ارسال پیام به کاربر", "users", USER, BODY, mutation=True, pipe=True)
add("discounts", "فهرست تخفیف‌ها", "discounts")
add("discount_add", "افزودن کد تخفیف", "discounts", Field("code", "کد تخفیف", "word"),
    choice("type", "نوع تخفیف", (("مبلغ ثابت به تومان", "fixed"), ("درصدی", "percent"))),
    Field("amount", "مقدار تخفیف", "positive"), Field("limit", "سقف کل استفاده", "nonnegative", default="0"),
    RULE_PRODUCT, entity("user", "محدود به کاربر", "user", "0"), date_field("end", "پایان اعتبار", True),
    Field("minimum", "حداقل مبلغ سفارش به تومان", "nonnegative", default="0"),
    Field("per_user", "سقف استفاده هر کاربر", "nonnegative", default="0"),
    date_field("start", "شروع اعتبار", True), mutation=True, pipe=True)
for key, label in (("discount_toggle", "تغییر وضعیت تخفیف"), ("discount_delete", "حذف تخفیف")):
    add(key, label, "discounts", entity("discount", "انتخاب تخفیف"), mutation=True)
add("tickets", "فهرست و فیلتر تیکت‌ها", "tickets",
    choice("status", "وضعیت تیکت", (("همه وضعیت‌ها", "all"), *TICKET_OPTIONS)))
add("ticket", "مشاهده مکالمه کامل تیکت", "tickets", TICKET)
add("ticket_attachment", "دریافت پیوست تیکت", "tickets", TICKET,
    entity("ticket_attachment", "انتخاب پیوست", "attachment"))
add("ticket_reply", "پاسخ به تیکت", "tickets", TICKET, BODY, mutation=True, pipe=True)
add("ticket_status", "تغییر وضعیت تیکت", "tickets", TICKET,
    choice("status", "وضعیت جدید", TICKET_OPTIONS), mutation=True)
add("ticket_close", "بستن تیکت", "tickets", TICKET, mutation=True)
add("faq_categories", "فهرست دسته‌های پرسش‌ها", "faq")
add("faq_category_add", "افزودن دسته پرسش‌ها", "faq", TITLE, mutation=True)
for key, label in (("faq_category_toggle", "تغییر وضعیت دسته پرسش‌ها"), ("faq_category_delete", "حذف دسته پرسش‌ها")):
    add(key, label, "faq", FAQ_CATEGORY, mutation=True)
add("faq_category_set", "ویرایش دسته پرسش‌ها", "faq", FAQ_CATEGORY,
    choice("field", "ویژگی دسته", (("نام", "name"), ("ترتیب نمایش", "sort_order"))),
    Field("value", "مقدار جدید", "dynamic"), mutation=True, pipe=True)
add("faqs", "فهرست پرسش‌ها", "faq", replace(FAQ_CATEGORY, default="all"))
add("faq_add", "افزودن پرسش و پاسخ", "faq", FAQ_CATEGORY, Field("question", "پرسش"),
    Field("answer", "پاسخ"), mutation=True, pipe=True)
for key, label in (("faq_toggle", "تغییر وضعیت پرسش"), ("faq_delete", "حذف پرسش")):
    add(key, label, "faq", FAQ, mutation=True)
add("faq_set", "ویرایش پرسش و پاسخ", "faq", FAQ,
    choice("field", "ویژگی پرسش", (("پرسش", "question"), ("پاسخ", "answer"),
                                   ("دسته", "category"), ("ترتیب نمایش", "sort_order"))),
    Field("value", "مقدار جدید", "dynamic"), mutation=True, pipe=True)
# These handlers only PREVIEW; their existing durable, counted confirmation
# remains the sole broadcast mutation boundary.
add("broadcast_all", "پیام به همه کاربران", "broadcast", BODY)
add("broadcast_joined", "پیام بر اساس تاریخ عضویت", "broadcast", START, END, BODY, pipe=True)
add("broadcast_product", "پیام به خریداران محصول", "broadcast", PRODUCT, START, END, BODY, pipe=True)
add("report", "ساخت گزارش و دریافت فایل", "reports", choice("kind", "نوع گزارش", (
    ("سفارش‌ها", "orders"), ("عضویت کاربران", "joined"), ("خریداران محصول", "product"), ("مالی", "finance"))))
add("rewards", "فهرست قواعد پاداش", "rewards")
add("reward_add", "افزودن قانون پاداش", "rewards", choice("event", "رویداد پاداش", (
    ("شروع ربات", "start"), ("اولین خرید", "first_purchase"),
    ("خرید محصول", "product_purchase"), ("شرط‌های ترکیبی", "combined"))), AMOUNT,
    mutation=True, pipe=True)
add("reward_toggle", "تغییر وضعیت قانون پاداش", "rewards", entity("reward", "انتخاب قانون"), mutation=True)


def form_fields(action: Action, values: dict) -> tuple[Field, ...]:
    """Resolve branches from *collected* values; no user-authored JSON/code."""
    fields = list(action.fields)
    if action.key == "orders" and values.get("range") == "custom":
        fields.extend((START, END))
    if action.key == "users":
        mode = values.get("mode")
        if mode in {"new", "inactive"}:
            fields.append(Field("days", "تعداد روز", "positive",
                                (("۷ روز", "7"), ("۳۰ روز", "30"), ("۹۰ روز", "90"))))
        if mode == "product":
            fields.append(PRODUCT)
        if mode in {"joined", "product"}:
            fields.extend((START, END))
    if action.key == "report" and values.get("kind"):
        if values["kind"] == "orders":
            fields.append(STATUS)
        if values["kind"] == "product":
            fields.append(replace(PRODUCT, default="all"))
        fields.extend((START, END))
    if action.key == "reward_add" and values.get("event"):
        if values["event"] != "start":
            fields.append(RULE_PRODUCT)
        if values["event"] == "combined":
            fields.extend((
                Field("minimum_successful_purchases", "حداقل خرید موفق زیرمجموعه", "nonnegative", default="0"),
                choice("first_purchase", "آیا فقط اولین خرید مشمول باشد؟", YES_NO),
                Field("minimum_referrals", "حداقل دعوت‌های ثبت‌شدهٔ خریدار", "nonnegative", default="0"),
                Field("minimum_qualified_referrals", "حداقل دعوت‌های واجد پاداشِ خریدار", "nonnegative", default="0"),
                Field("product_ids", "محصول‌های مجاز شرط ترکیبی", "multi:product", default="[]"),
                Field("minimum_order_amount", "حداقل مبلغ سفارش به تومان", "nonnegative", default="0"),
            ))
        fields.extend((date_field("start", "شروع اعتبار", True), date_field("end", "پایان اعتبار", True)))
    result = []
    for field in fields:
        if field.kind != "dynamic":
            result.append(field)
            continue
        name = values.get("field")
        resolved = field
        if name in {"category", "parent"}:
            kind = "faq_category" if action.key == "faq_set" else "category"
            resolved = entity(kind, "انتخاب دسته جدید", "value", "0" if name == "parent" else None)
        elif name == "type":
            resolved = choice("value", "نوع تحویل", (("آماده و خودکار", "ready"), ("فعال‌سازی دستی", "manual")))
        elif name == "renewable":
            resolved = choice("value", "قابل تمدید است؟", YES_NO)
        elif name in {"price", "sort_order", "stock_limit", "duration_days"}:
            kind = {"price": "positive", "sort_order": "integer", "stock_limit": "nonnegative", "duration_days": "positive"}[name]
            resolved = replace(field, kind=kind,
                               default="none" if name in {"stock_limit", "duration_days"} else None)
        elif name == "reminder_days":
            resolved = Field("value", "روزهای یادآوری پیش از انقضا", "reminders",
                             (("روز پایان اشتراک", "0"), ("یک روز قبل", "1"),
                              ("سه و یک روز قبل و روز پایان", "3,1,0")), default="0",
                             hint="برای مقدار سفارشی، روزهای صحیح نامنفی را با ویرگول جدا کنید. صفر یعنی روز پایان، پیش از انقضا.")
        else:
            resolved = replace(field, kind="text", default="none" if name in {"icon", "description", "rules_url"} else None)
        result.append(resolved)
    return tuple(result)


def arguments(action: Action, values: dict, *, page: int = 1) -> tuple[str, list[str] | None]:
    """Return legacy arguments plus lossless structured pipe fields."""
    fields = form_fields(action, values)
    parts = [str(values[field.key]) for field in fields]
    pipe = action.separator == " | "
    key = action.key
    if key == "orders":
        parts = [values["status"]]
        if values["range"] == "custom":
            parts.extend((values["start"], values["end"]))
        parts.append(str(page))
    elif key in {"inventory_add", "inventory_edit"}:
        parts = [values["target"]]
    elif key == "ticket_attachment":
        parts = [values["attachment"]]
    elif key in {"order_status", "card_resolve", "crypto_resolve"}:
        second = values["status"] if key == "order_status" else values["resolution"]
        parts = [f"{values['target']} {second}", values["note"]]
        if not values["note"]:
            parts.pop()
        pipe = True
    elif key == "report":
        kind = values["kind"]
        parts = {
            "orders": ["orders", values.get("status", "all")],
            "joined": ["users", "joined"],
            "product": ["users", "product", values.get("target", "all")],
            "finance": ["finance"],
        }[kind] + [values["start"], values["end"]]
    elif key == "reward_add":
        parts = [values["event"], values["amount"], values.get("product", "0")]
        if values["event"] == "combined":
            conditions = {name: int(values[name]) for name in (
                "minimum_successful_purchases", "minimum_referrals", "minimum_qualified_referrals", "minimum_order_amount"
            ) if int(values[name]) > 0}
            if values["first_purchase"] == "true":
                conditions["first_purchase"] = True
            products = json.loads(values["product_ids"])
            if products:
                conditions["product_ids"] = products
            parts.append(json.dumps(conditions, separators=(",", ":")))
        parts.extend((values["start"], values["end"]))
    elif key in {"admins", "categories", "products", "discounts", "faq_categories", "faqs", "rewards",
                 "inventory_list", "users", "tickets", "user_orders", "user_transactions", "user_referrals", "user_rewards"}:
        parts.append(str(page))
    return (" | " if pipe else " ").join(parts), parts if pipe else None
