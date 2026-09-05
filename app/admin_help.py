from __future__ import annotations


ADMIN_HELP = """🛠 <b>راهنمای مدیریت ربات</b>

فقط در گفت‌وگوی خصوصی بفرستید؛ جداکننده آرگومان‌ها <code>|</code> است.

<b>مدیریت کلی</b>
<code>/admin_help</code>
<code>/bot_on</code> — روشن‌کردن ربات
<code>/bot_off</code> — حالت تعمیرات
<code>/set_card شماره کارت | نام صاحب حساب</code>
<code>/set_channel https://t.me/channel</code>
<code>/payment wallet|card|crypto on|off</code>
<code>/join_add @channel | عنوان | لینک عضویت</code>
<code>/join_toggle ID</code>
<code>/join_delete ID</code>
<code>/backup</code>

<b>مدیران</b>
<code>/admins</code>
<code>/admin_add @username CHAT_ID owner|admin|support</code>
<code>/admin_enable CHAT_ID</code>
<code>/admin_disable CHAT_ID</code>

<b>دسته و محصول</b>
<code>/categories</code>
<code>/category_add عنوان</code>
<code>/subcategory_add PARENT_ID | عنوان</code>
<code>/category_toggle ID</code>
<code>/category_set CATEGORY_ID | name|parent|sort_order | VALUE</code>
<code>/category_delete CATEGORY_ID</code> — فقط دسته خالی
<code>/products [CATEGORY_ID]</code>
<code>/product_add CATEGORY_ID | عنوان | قیمت | مدت | ready|manual</code>
<code>/product_set PRODUCT_ID | FIELD | VALUE</code>
<code>/product_toggle PRODUCT_ID visible|available|reserve</code>
<code>/product_delete PRODUCT_ID</code> — حذف نرم
فیلدهای قابل ویرایش: <code>name, category, type, stock_limit, icon, short_description, long_description, price, duration, duration_days, account_type, activation, renewable, warranty, features, activation_instructions, usage_terms, rules, rules_url, info_request_text, completion_text, delivery_instructions, reminder_days</code>

<b>انبار</b>
<code>/inventory_add PRODUCT_ID</code> — سپس payload محرمانه را بفرستید
<code>/inventory_list PRODUCT_ID</code>
<code>/inventory_disable ITEM_ID</code>
<code>/inventory_enable ITEM_ID</code>
<code>/inventory_delete ITEM_ID</code> — فقط available/disabled
<code>/inventory_assign ITEM_ID USER_CHAT_ID</code>

<b>سفارش و پرداخت</b>
<code>/orders [STATUS|all] [FROM_DATE TO_DATE] [PAGE]</code>
<code>/order ORDER_NUMBER</code>
<code>/order_status ORDER_NUMBER STATUS | پیام اختیاری</code>
<code>/complete ORDER_NUMBER | متن تحویل</code>
<code>/request_info ORDER_NUMBER | متن درخواست اصلاح</code>
<code>/approve_payment PAYMENT_NUMBER</code>
<code>/reject_payment PAYMENT_NUMBER | دلیل</code>

<b>کاربر</b>
<code>/users [all|active|blocked] [PAGE]</code>
<code>/user CHAT_ID|@username</code>
<code>/block CHAT_ID</code>
<code>/unblock CHAT_ID</code>
<code>/wallet_adjust CHAT_ID | AMOUNT_SIGNED | دلیل</code>
<code>/message CHAT_ID | متن</code>

<b>تخفیف</b>
<code>/discounts</code>
<code>/discount_add CODE | fixed|percent | VALUE | MAX_USES|0 | PRODUCT_ID|0 | USER_CHAT_ID|0 | END_DATE|0 [| MINIMUM|0 | PER_USER_LIMIT|0 | START_DATE|0]</code>
<code>/discount_toggle CODE</code>
<code>/discount_delete CODE</code> — فقط کد استفاده‌نشده

<b>پشتیبانی و FAQ</b>
<code>/tickets [open|answered|closed|all] [PAGE]</code>
<code>/ticket TICKET_NUMBER</code>
<code>/ticket_reply TICKET_NUMBER | پاسخ</code>
<code>/ticket_close TICKET_NUMBER</code>
<code>/faq_categories</code>
<code>/faq_category_add عنوان</code>
<code>/faq_category_toggle CATEGORY_ID</code>
<code>/faq_category_set CATEGORY_ID | name|sort_order | VALUE</code>
<code>/faq_category_delete CATEGORY_ID</code> — فقط دسته خالی
<code>/faqs [CATEGORY_ID]</code>
<code>/faq_add دسته | سوال | جواب</code>
<code>/faq_toggle FAQ_ID</code>
<code>/faq_set FAQ_ID | question|answer|category|sort_order | VALUE</code>
<code>/faq_delete FAQ_ID</code>

<b>پیام و گزارش</b>
<code>/broadcast_all متن</code> — پیش‌نمایش و تأیید
<code>/broadcast_joined FROM_DATE | TO_DATE | متن</code>
<code>/broadcast_product PRODUCT_ID | FROM_DATE | TO_DATE | متن</code>
<code>/report orders|users|finance FROM_DATE TO_DATE</code>

<b>پاداش دعوت</b>
<code>/rewards</code>
<code>/reward_add EVENT | AMOUNT | PRODUCT_ID|0 [| START|0 | END|0]</code>
<code>/reward_toggle RULE_ID</code>

"""


ADMIN_HELP_MORE = """<b>فرمان‌های تکمیلی و قالب‌بندی</b>

<code>/joins</code> — فهرست کانال‌های جوین اجباری
<code>/inventory_edit ITEM_ID</code> — ویرایش امن موجودی
<code>/ticket_status TICKET_NUMBER open|answered|closed</code>
<code>/ticket_attachment MESSAGE_ID</code> — دریافت دوباره پیوست ذخیره‌شده
<code>/users new [DAYS] [PAGE]</code> یا <code>/users inactive [DAYS] [PAGE]</code>
<code>/users joined FROM_DATE TO_DATE [PAGE]</code>
<code>/users product PRODUCT_ID FROM_DATE TO_DATE [PAGE]</code>
<code>/user_orders USER [STATUS|all] [PAGE|ORDER_NUMBER]</code>
<code>/user_transactions USER [PAGE]</code>
<code>/user_referrals USER [PAGE]</code>
<code>/user_rewards USER [PAGE]</code>
<code>/report orders [STATUS|all] FROM_DATE TO_DATE</code>
<code>/report users joined FROM_DATE TO_DATE</code>
<code>/report users product PRODUCT_ID|all FROM_DATE TO_DATE</code>
<code>/reward_add combined | AMOUNT | PRODUCT_ID|0 | CONDITIONS_JSON [| START|0 | END|0]</code>
<code>/card_reviews</code>
<code>/card_resolve EVENT_ID dismiss|refund_confirmed | توضیح</code> — فقط مالک؛ بدون اعتبار خودکار
<code>/payment_detail PAYMENT_NUMBER</code> — جزئیات و ارسال مجدد فیش ذخیره‌شده
<code>/order_attachment ORDER_NUMBER</code> — ارسال مجدد پیوست اطلاعات سفارش دستی
<code>/crypto_reviews</code>
<code>/crypto_resolve EVENT_ID dismiss|refund_confirmed|credit_confirmed | توضیح</code> — فقط مالک؛ credit فقط با شاهد completed معتبر

برای متن بولد، ایتالیک، لینک، نقل‌قول و ایموجی متحرک، متن را با
<code>html:</code> شروع کنید؛ فقط HTML مجاز تلگرام پذیرفته می‌شود.

کلیدهای شرط ترکیبی: <code>minimum_successful_purchases</code>،
<code>first_purchase</code>، <code>minimum_referrals</code>،
<code>minimum_qualified_referrals</code>، <code>product_ids</code> و
<code>minimum_order_amount</code>.
"""


ADMIN_HELP_PARTS = (ADMIN_HELP, ADMIN_HELP_MORE)


SUPPORT_HELP = """🎧 <b>راهنمای پشتیبان</b>

<code>/admin_help</code>
<code>/orders [STATUS|all] [FROM_DATE TO_DATE] [PAGE]</code>
<code>/order ORDER_NUMBER</code>
<code>/request_info ORDER_NUMBER | متن درخواست اصلاح</code>
<code>/tickets [open|answered|closed|all] [PAGE]</code>
<code>/ticket TICKET_NUMBER</code>
<code>/ticket_attachment MESSAGE_ID</code>
<code>/ticket_reply TICKET_NUMBER | پاسخ</code>
<code>/ticket_status TICKET_NUMBER open|answered|closed</code>
<code>/ticket_close TICKET_NUMBER</code>
<code>/users [all|active|blocked] [PAGE]</code>
<code>/user CHAT_ID|@username</code>
<code>/user_orders USER [STATUS|all] [PAGE|ORDER_NUMBER]</code>
<code>/user_transactions USER [PAGE]</code>
<code>/user_referrals USER [PAGE]</code>
<code>/user_rewards USER [PAGE]</code>
<code>/message CHAT_ID | متن</code>
"""


def split_command(text: str) -> tuple[str, str]:
    first, _, rest = text.strip().partition(" ")
    return first.split("@", 1)[0].lower(), rest.strip()


def pipe_parts(value: str, minimum: int = 1) -> list[str]:
    parts = [part.strip() for part in value.split("|")]
    if len(parts) < minimum or any(not part for part in parts[:minimum]):
        raise ValueError("تعداد یا ترتیب ورودی‌ها درست نیست؛ نمونه فرمان را از /admin_help ببینید.")
    return parts
