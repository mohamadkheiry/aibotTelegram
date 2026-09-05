from __future__ import annotations

from typing import Any

from .utils import escape, money, render_rich_text


STATUS_LABELS = {
    "pending_payment": "در انتظار پرداخت",
    "awaiting_confirmation": "در انتظار تأیید",
    "paid": "پرداخت‌شده",
    "awaiting_stock": "در انتظار موجودی",
    "awaiting_info": "در انتظار اطلاعات",
    "processing": "در حال انجام",
    "completed": "تکمیل‌شده",
    "cancelled": "لغوشده",
    "expired": "منقضی‌شده",
    "rejected": "ردشده",
    "refunded": "بازپرداخت‌شده",
}


def provider_review_resolution(action: str, settlement: str) -> str:
    if action == "credit_confirmed":
        if settlement == "wallet_topup_credited":
            outcome = "پرداخت قطعی تأیید و شارژ کیف پول ثبت شد."
        else:
            outcome = (
                "پرداخت قطعی تأیید و مبلغ پرداخت به کیف پول اضافه شد؛ "
                "سفارش قبلی دوباره فعال نشد."
            )
        suffix = "این تعیین تکلیف بر اساس شواهد قطعی درگاه ثبت شده است."
    elif action == "refund_confirmed":
        outcome = "بازپرداخت وجه توسط مدیریت ثبت شد."
        suffix = "هیچ شارژ خودکاری در این مرحله انجام نشده است."
    else:
        outcome = "رخداد پس از بررسی بدون ثبت پرداخت بسته شد."
        suffix = "هیچ شارژ خودکاری در این مرحله انجام نشده است."
    return f"بازبینی پرداخت ارزی شما به پایان رسید.\n{outcome}\n{suffix}"


def card_review_resolution(action: str) -> str:
    outcome = (
        "بازپرداخت وجه توسط مدیریت ثبت شد."
        if action == "refund_confirmed"
        else "رخداد پس از بررسی بدون ثبت پرداخت بسته شد."
    )
    return (
        "بازبینی رخداد بانکی مرتبط با پرداخت شما پایان یافت."
        f"\n{outcome}"
        "\nهیچ اعتبار خودکاری در این مرحله انجام نشده است."
    )


def main_menu(name: str) -> str:
    return (
        "💜 <b>منوی اصلی</b>\n\n"
        f"سلام {escape(name)}، خوش اومدی 👋\n\n"
        "از اینجا به بخش‌های اصلی ربات دسترسی داری:\n\n"
        "🏪   فروشگاه — مشاهده و خرید اشتراک‌ها\n"
        "👤   حساب من — آمار، سفارش‌ها و تراکنش‌ها\n"
        "👛   کیف پول — موجودی و افزایش اعتبار\n"
        "🪙   دعوت و کسب درآمد — لینک دعوت، آمار دوستان و درآمدها\n"
        "💬   پشتیبانی — سوالات متداول، ثبت تیکت\n"
        "🤝   کانال — کانال رسمی الون اکانت\n"
        "یکی از گزینه‌های زیر رو انتخاب کن 👇"
    )


def join_required(channels: list[dict[str, Any]]) -> str:
    names = "\n".join(f"• {escape(c['title'])}" for c in channels)
    return (
        "🔐 <b>عضویت در کانال‌ها</b>\n\n"
        "برای استفاده از ربات، ابتدا در کانال‌های زیر عضو شو:\n\n"
        f"{names}\n\n"
        "بعد از عضویت، روی «بررسی عضویت» بزن."
    )


def store_title() -> str:
    return "🏪 <b>فروشگاه</b>\nدسته‌بندی موردنظرت رو انتخاب کن:"


def category_title(icon: str, title: str, description: str = "") -> str:
    prefix = f"{escape(icon)} " if icon else ""
    details = f"\n{render_rich_text(description)}" if description else ""
    return (
        f"{prefix}<b>{escape(title)}</b>{details}"
        "\nاشتراک موردنظرت رو انتخاب کن:"
    )


def product_summary(product: dict[str, Any], currency: str) -> str:
    icon = f"{escape(product.get('icon') or '')} " if product.get("icon") else ""
    return (
        f"{icon}<b>{escape(product['title'])}</b>\n\n"
        f"💵 قیمت: {money(product['price'], currency)}\n\n"
        f"🗓 مدت اشتراک: {escape(product.get('duration') or '—')}\n"
        f"🔒 نوع اشتراک: {render_rich_text(product.get('account_type') or '—')}\n"
        f"🟢 فعال‌سازی: {render_rich_text(product.get('activation') or '—')}\n"
        f"🔄 تمدید: {escape(product.get('renewable') or '—')}\n"
        f"🛡 گارانتی: {render_rich_text(product.get('warranty') or '—')}\n"
        "برای خرید، روی دکمه زیر بزن👇"
    )


def product_details(product: dict[str, Any]) -> str:
    features = product.get("features") or "—"
    return (
        f"ℹ️ <b>توضیحات تکمیلی {escape(product['title'])}</b>\n\n"
        f"{render_rich_text(product.get('long_description') or product.get('short_description') or '—')}\n\n"
        "✅ <b>امکانات و مزایا:</b>\n"
        f"{escape(features)}\n\n"
        "🐍 <b>نحوه فعال‌سازی:</b>\n"
        f"{render_rich_text(product.get('activation_instructions') or '—')}\n\n"
        "✅ <b>شرایط استفاده:</b>\n"
        f"{render_rich_text(product.get('usage_terms') or '—')}\n\n"
        "🔥 <b>گارانتی:</b>\n"
        f"{render_rich_text(product.get('warranty') or '—')}\n\n"
        "‼️ <b>قوانین:</b>\n"
        f"{render_rich_text(product.get('rules') or '—')}"
    )


ASK_NAME = "👤 <b>اسمت رو وارد کن</b>\n\nبرای ثبت سفارش، نام و نام خانوادگی رو بنویس."
ASK_PHONE = (
    "🤙 <b>شماره موبایلت رو بفرست</b>\n\n"
    "برای ادامه، شماره موبایلت رو با دکمه زیر ارسال کن."
)


def order_summary(order: dict[str, Any], balance: int, currency: str) -> str:
    icon = f"{escape(order.get('product_icon') or '')} " if order.get("product_icon") else ""
    discount = int(order.get("discount_amount") or 0)
    if discount:
        pricing = (
            f"💵قیمت اصلی: {money(order['base_price'], currency)}\n"
            f"🛍تخفیف: {money(discount, currency)}\n"
            f"💰مبلغ نهایی: {money(order['final_amount'], currency)}"
        )
    else:
        pricing = f"💵قیمت: {money(order['final_amount'], currency)}"
    return (
        "📦 <b>خلاصه سفارش</b>\n\n"
        f"شماره سفارش: <code>{escape(order['order_number'])}</code>\n"
        f"{icon}{escape(order['product_title'])}\n"
        f"🗓مدت: {escape(order.get('product_duration') or '—')}\n"
        f"{pricing}\n\n"
        f"💸 موجودی کیف پول: {money(balance, currency)}\n\n"
        "اگر کد تخفیف داری، می‌تونی قبل از پرداخت ثبتش کنی."
    )


DISCOUNT_PROMPT = (
    "🏷🔖 <b>ثبت کد تخفیف</b>\n\n"
    "اگه کد تخفیف داری، اینجا واردش کن.\n\n"
    "بعد از ثبت، مبلغ سفارش به‌صورت خودکار به‌روزرسانی می‌شه."
)
INVALID_DISCOUNT = (
    "👎 کد تخفیف معتبر نیست.\n\n"
    "یک کد دیگه وارد کن یا روی «بازگشت» بزن."
)


def payment_methods(order: dict[str, Any], balance: int, currency: str) -> str:
    discount = int(order.get("discount_amount") or 0)
    lines = [
        "💳 <b>روش پرداخت</b>",
        "",
        f"{escape(order.get('product_icon') or '')} {escape(order['product_title'])}".strip(),
        f"🗓مدت: {escape(order.get('product_duration') or '—')}",
        f"💵قیمت اصلی: {money(order['base_price'], currency)}",
        f"🛍تخفیف: {money(discount, currency)}",
        f"💰مبلغ نهایی: {money(order['final_amount'], currency)}",
        "",
        f"💸 موجودی کیف پول: {money(balance, currency)}",
    ]
    if balance < int(order["final_amount"]):
        lines.extend(
            [
                "⚠️ موجودی کیف پولت برای این سفارش کافی نیست. "
                "یک روش پرداخت دیگه انتخاب کن یا کیف پولت رو شارژ کن."
            ]
        )
    return "\n".join(lines)


def card_payment(payment: dict[str, Any], card_number: str, owner: str, currency: str) -> str:
    return (
        "💳 <b>پرداخت کارت به کارت</b>\n\n"
        f"مبلغ قابل پرداخت: <code>{money(payment['payable_amount'], currency)}</code>\n\n"
        "⚠️ مبلغ رو دقیقاً همین مقدار واریز کن و رُند نکن.\n"
        "عدد انتهای مبلغ، کد شناسایی این پرداخت و برای تأیید خودکار لازمه.\n\n"
        "شماره کارت:\n"
        f"<code>{escape(card_number)}</code>\n\n"
        f"به نام: {escape(owner)}\n\n"
        "بعد از واریز، پرداخت به‌صورت خودکار بررسی می‌شه.\n"
        "⚠️ این سفارش تا ۳۰ دقیقه معتبره."
    )


EARLY_RECEIPT = (
    "⌛️ <b>پرداخت هنوز در حال بررسیه</b>\n\n"
    "اگر تا یک دقیقه بعد از واریز، پرداختت خودکار تأیید نشد، "
    "می‌تونی فیش رو برای بررسی دستی ارسال کنی."
)


def payment_success(order: dict[str, Any], paid_amount: int, method: str, currency: str) -> str:
    return (
        "✅ <b>پرداخت با موفقیت تایید شد</b>\n\n"
        f"🧾 شماره سفارش: <code>{escape(order['order_no'])}</code>\n"
        f"{escape(order.get('product_icon') or '')} {escape(order['product_title'])}".strip()
        + "\n"
        f"💵 مبلغ: {money(paid_amount, currency)}\n"
        f"💱روش پرداخت: {escape(method)}"
    )


def ready_delivery(order: dict[str, Any], content: str, instructions: str = "") -> str:
    return (
        "✅️ <b>سفارشت آماده است</b>\n\n"
        f"🧾 شماره سفارش: <code>{escape(order['order_no'])}</code>\n"
        f"{escape(order.get('product_icon') or '')} {escape(order['product_title'])}".strip()
        + "\n\n"
        f"<code>{escape(content)}</code>\n\n"
        f"{render_rich_text(instructions)}\n\n"
        "با آرزوی تجربه‌ای لذت‌بخش از اشتراکت💜"
    )


def reserved_delivery(order: dict[str, Any]) -> str:
    return (
        "✅ <b>سفارشت ثبت و رزرو شد</b>\n\n"
        "به‌محض موجود شدن اشتراک، به‌صورت خودکار برات ارسال می‌شه.\n\n"
        f"🧾 شماره سفارش: <code>{escape(order['order_no'])}</code>\n"
        f"{escape(order.get('product_icon') or '')} {escape(order['product_title'])}".strip()
        + "\n"
        "⌛️ وضعیت سفارش: در انتظار موجودی"
    )


def needs_information(order: dict[str, Any], prompt: str) -> str:
    return (
        "ℹ️📋 <b>اطلاعات موردنیاز برای فعال‌سازی</b>\n\n"
        f"برای فعال‌سازی {escape(order['product_title'])} باید اطلاعات موردنیاز رو ارسال کنی.\n\n"
        f"{render_rich_text(prompt)}"
    )


def information_saved(order_no: str) -> str:
    return (
        "✅ <b>اطلاعاتت ثبت شد</b>\n\n"
        f"اطلاعات موردنیاز برای سفارش <code>{escape(order_no)}</code> دریافت شد.\n\n"
        "سفارشت برای فعال‌سازی در صف قرار گرفت و بعد از انجام، همینجا بهت اطلاع داده می‌شه.\n\n"
        "اگر اطلاعات نیاز به اصلاح داشته باشه، بهت خبر می‌دیم."
    )


def order_expired(order_no: str) -> str:
    return (
        "⌛️ <b>مهلت پرداخت تمام شد</b>\n\n"
        f"سفارش <code>{escape(order_no)}</code> منقضی شد. لطفاً دیگر مبلغ این پرداخت را واریز نکن.\n"
        "برای خرید، دوباره محصول موردنظرت را از فروشگاه انتخاب کن."
    )


def wallet_page(balance: int, currency: str) -> str:
    return (
        "👛 <b>کیف پول</b>\n\n"
        f"موجودی فعلی: {money(balance, currency)}\n\n"
        "برای افزایش اعتبار، روی دکمه زیر بزن و مبلغ دلخواهت رو وارد کن."
    )


def referral_page(invited: int, rewards: int, link: str, currency: str) -> str:
    return (
        "🪙 <b>دعوت و کسب درآمد</b>\n\n"
        f"تعداد دوستان دعوت‌شده: {invited}\n"
        f"مجموع پاداش: {money(rewards, currency)}\n\n"
        "دوستانت باید ربات را برای اولین بار با لینک اختصاصی تو شروع کنند. "
        "پاداش‌ها مطابق قوانین فعال مدیریت به کیف پولت اضافه می‌شوند.\n\n"
        f"لینک دعوت:\n<code>{escape(link)}</code>"
    )
