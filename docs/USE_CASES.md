# شرح یوزکیس‌های ربات فروشگاهی الون اکانت

> نسخه مبنا: 2026-09-05<br>
> مخاطب: تحلیل‌گر، QA، توسعه‌دهنده، تیم عملیات و agentهای نرم‌افزاری<br>
> قواعد مرجع: [BUSINESS.md](BUSINESS.md)

## ۱. قرارداد این سند

هر یوزکیس دارای شناسه پایدار `UC-*` است. در تغییرات بعدی، شناسه حذف یا به معنای دیگری استفاده نشود؛ یوزکیس منسوخ با برچسب Deprecated باقی بماند. «سامانه» در این سند شامل BotApplication، دیتابیس SQLite، worker دوره‌ای و adapterهای بیرونی است.

قواعد مشترک همه یوزکیس‌ها:

- تعامل مشتری و مدیر فقط در private chat انجام می‌شود.
- ورودی callback، شناسه entity و مالکیت دوباره در سمت سرور بررسی می‌شود؛ نمایش قبلی یک دکمه مجوز دائمی نیست.
- عملیات تکرارشونده یا حساس با update ID، idempotency key، unique constraint و transaction محافظت می‌شود.
- پیام خطا نباید token، کلید API، payload انبار یا جزئیات فنی حساس را افشا کند.
- `/cancel` state مکالمه را پاک می‌کند و کاربر را به منوی اصلی بازمی‌گرداند.
- وضعیت‌ها و اصطلاحات مالی مطابق [BUSINESS.md](BUSINESS.md) هستند.

## ۲. فهرست بازیگران

| کد | بازیگر | شرح |
|---|---|---|
| ACT-U | کاربر/مشتری | شخصی که ربات را در Telegram شروع می‌کند |
| ACT-R | دعوت‌کننده | کاربر موجودی که لینک دعوت خود را منتشر می‌کند |
| ACT-S | پشتیبان | مدیر با نقش `support` و اختیارات محدود |
| ACT-A | مدیر | مدیر اجرایی با نقش `admin` |
| ACT-O | مالک | مدیر با نقش `owner` |
| ACT-T | Telegram | Bot API، update، ارسال پیام و وضعیت عضویت |
| ACT-B | رخداد بانکی | MacroDroid/سامانه‌ای که callback کارت را فراخوانی می‌کند |
| ACT-P | Plisio | ارائه‌دهنده invoice و وضعیت پرداخت ارزی |
| ACT-W | worker | پردازش دوره‌ای داخلی |

## ۳. کاتالوگ یوزکیس‌ها

| شناسه | عنوان | بازیگر اصلی | اولویت |
|---|---|---|---|
| UC-01 | شروع، ثبت هویت و عبور از دسترسی | ACT-U | حیاتی |
| UC-02 | ثبت دعوت و پاداش شروع | ACT-U/ACT-R | بالا |
| UC-03 | مرور کاتالوگ و جزئیات محصول | ACT-U | حیاتی |
| UC-04 | ایجاد سفارش و تکمیل پروفایل خرید | ACT-U | حیاتی |
| UC-05 | اعمال کد تخفیف | ACT-U | بالا |
| UC-06 | پرداخت کامل یا جزئی با کیف پول | ACT-U | حیاتی |
| UC-07 | پرداخت کارت‌به‌کارت خودکار | ACT-U/ACT-B | حیاتی |
| UC-08 | ارسال و بررسی دستی فیش | ACT-U/ACT-A | حیاتی |
| UC-09 | پرداخت ارزی Plisio | ACT-U/ACT-P | متوسط/اختیاری |
| UC-10 | شارژ کیف پول | ACT-U | بالا |
| UC-11 | تحویل خودکار محصول آماده | ACT-W | حیاتی |
| UC-12 | رزرو و تحویل پس از تأمین موجودی | ACT-U/ACT-W | بالا |
| UC-13 | فعال‌سازی محصول دستی | ACT-U/ACT-A | حیاتی |
| UC-14 | مشاهده حساب، سفارش‌ها و تراکنش‌ها | ACT-U | بالا |
| UC-15 | FAQ، تیکت و مکالمه پشتیبانی | ACT-U/ACT-S | بالا |
| UC-16 | لینک دعوت، آمار و پاداش خرید | ACT-U/ACT-R | بالا |
| UC-17 | مشاهده کانال رسمی | ACT-U | پایین |
| UC-20 | bootstrap و مدیریت دسترسی مدیران | ACT-O/ACT-A | حیاتی |
| UC-21 | تنظیم حالت ربات، پرداخت، کانال و جوین اجباری | ACT-A/ACT-O | بالا |
| UC-22 | مدیریت دسته و محصول | ACT-A/ACT-O | حیاتی |
| UC-23 | مدیریت امن انبار | ACT-A/ACT-O | حیاتی |
| UC-24 | پایش و تعیین تکلیف سفارش/پرداخت | ACT-A/ACT-O/ACT-S | حیاتی |
| UC-25 | مدیریت کاربر و اصلاح کیف پول | ACT-A/ACT-O/ACT-S | بالا |
| UC-26 | مدیریت تخفیف | ACT-A/ACT-O | بالا |
| UC-27 | مدیریت FAQ و تیکت | ACT-S/ACT-A/ACT-O | بالا |
| UC-28 | broadcast و پیام مستقیم | ACT-A/ACT-O/ACT-S | متوسط |
| UC-29 | گزارش مدیریتی | ACT-A/ACT-O | بالا |
| UC-30 | مدیریت قوانین پاداش | ACT-A/ACT-O | بالا |
| UC-31 | تهیه بکاپ کامل | ACT-O | حیاتی |
| UC-40 | دریافت و پردازش update با long polling | ACT-T | حیاتی |
| UC-41 | نگهداری دوره‌ای و reconciliation | ACT-W | حیاتی |
| UC-42 | ارسال پایدار، retry و reminder | ACT-W | بالا |

## ۴. یوزکیس‌های مشتری

### UC-01 — شروع، ثبت هویت و عبور از دسترسی

**هدف:** ساخت/به‌روزرسانی هویت کاربر و نمایش منوی اصلی فقط پس از عبور از guardهای کسب‌وکار.

**بازیگر اصلی:** ACT-U؛ **همکار:** ACT-T<br>
**محرک:** پیام `/start [referrer-id]` یا `/menu` یا «منوی اصلی» در private chat.

**پیش‌شرط‌ها:** update معتبر Telegram به سامانه رسیده است.

**پس‌شرط موفق:** رکورد User با شناسه‌های Telegram به‌روز است؛ state قبلی برای نمایش منو پاک شده؛ در صورت نیاز عضویت تأیید و منوی اصلی نمایش داده شده است.

**جریان اصلی:**

1. سامانه privateبودن chat را بررسی می‌کند.
2. هویت Telegram را با `telegram_user_id` و `chat_id` upsert می‌کند.
3. اگر grant مدیر pending است، فقط تطبیق هم‌زمان username و private chat/user ID آن را verify می‌کند؛ اگر مدیر قبلاً verify شده است، همان chat ID پایدار authorization anchor است و username تازه همان chat صرفاً metadata را به‌روزرسانی می‌کند.
4. وضعیت block و حالت تعمیرات بررسی می‌شود.
5. همه کانال‌های جوین اجباری فعال خوانده و عضویت کاربر با Telegram بررسی می‌شود.
6. اگر همه شروط برقرار است، پاداش start احتمالی reconcile و متن اصلی منو یک‌بار ارسال می‌شود؛ همین پیام ابتدا reply keyboard قدیمی را حذف می‌کند و سپس inline keyboard پنج‌ردیفی با همان ترتیب، style و labelهای بدون emoji به آن متصل می‌شود. دکمه کانالِ تنظیم‌شده مستقیماً URL کانال را باز می‌کند.

**جریان‌های جایگزین/خطا:**

- chat غیرخصوصی: عملیات انجام نمی‌شود؛ callback در صورت وجود پاسخ محدودیت می‌گیرد.
- کاربر blocked و مدیر معتبر نیست: پیام مسدودی نمایش داده می‌شود.
- ربات در تعمیرات و کاربر مدیر نیست: پیام موقت نمایش داده می‌شود.
- عضویت ناقص: فهرست صفحه‌بندی‌شده کانال‌ها و «بررسی عضویت» نمایش داده می‌شود؛ پس از لمس دکمه، همه کانال‌ها دوباره بررسی می‌شوند.
- خطای موقت `getChatMember`: عضویت تأییدشده فرض نمی‌شود.
- callback قدیمی یا malformed: fail closed و پاسخ کنترل‌شده، بدون exception قابل مشاهده کاربر.
- username متعلق به grant روی chat دیگری دیده شود یا زوج pending کامل منطبق نباشد: دسترسی مدیر فعال نمی‌شود؛ rename username مدیر verifyشده روی همان chat دسترسی را قطع نمی‌کند.
- خطای Telegram هنگام اتصال inline keyboard: یک پیام کوتاه انتخاب با همان کنترل‌ها ارسال می‌شود؛ متن اصلی منو دوباره فرستاده نمی‌شود. cancellation هنگام shutdown پیام fallback تازه نمی‌سازد. shortcutهای متنی و reply keyboard اعلان‌های قبلی برای سازگاری قابل استفاده می‌مانند.

**قواعد:** BR-ID-01..05، BR-ORD-08، BR-OPS-02.<br>
**پیاده‌سازی:** `BotApplication._handle_message`, `_handle_callback`, `_access_guard`, `_check_memberships`, `_show_join_required`, `show_main_menu` در `app/bot.py`؛ `inline_main_menu_keyboard` در `app/keyboards.py`؛ `upsert_user` در `app/db.py`.<br>
**تست:** `test_start_menu_purchase_requires_own_contact_and_creates_order`، `test_catalog_faq_and_join_surfaces_are_paginated`، `test_forced_join_long_title_is_clamped_to_telegram_limit`، `test_malformed_callbacks_fail_closed_and_are_answered_once`، `test_inline_main_menu_preserves_layout_styles_icons_and_safe_actions`، `test_every_main_menu_callback_reaches_its_user_route`، `test_main_menu_edit_failure_keeps_single_welcome_and_actionable_fallback`، `test_cancelled_menu_edit_does_not_send_during_shutdown`.

### UC-02 — ثبت دعوت و پاداش شروع

**هدف:** اتصال قطعی دعوت‌شونده جدید به یک دعوت‌کننده و اعطای احتمالی پاداش شروع.

**بازیگر اصلی:** ACT-U؛ **ذی‌نفع:** ACT-R<br>
**محرک:** `/start <telegram_user_id دعوت‌کننده>`.

**پیش‌شرط‌ها:** دعوت‌کننده قبلاً User است؛ دعوت‌شونده همان شخص نیست.

**پس‌شرط موفق:** Referral یکتا ثبت می‌شود؛ پس از عبور از access guard، همه ruleهای فعال start در پنجره زمانی، دقیقاً یک‌بار به کیف پول دعوت‌کننده واریز و اعلان پایدار queue می‌شوند.

**جریان اصلی:**

1. سامانه referrer ID را از deep link استخراج می‌کند.
2. دعوت‌کننده را با Telegram ID پیدا می‌کند.
3. نبود self-referral و نبود دعوت قبلی برای invitee را بررسی می‌کند.
4. Referral با وضعیت `registered` ساخته می‌شود.
5. بعد از تأیید جوین و دسترسی، ruleهای `start` ارزیابی می‌شوند.
6. WalletEntry و RewardEvent در یک transaction ساخته و اعلان دعوت‌کننده ارسال/queue می‌شود.

**جریان‌های جایگزین/خطا:** دعوت‌کننده ناموجود، self-referral، لینک تکراری یا تلاش تغییر دعوت‌کننده بدون اثر است؛ replay همان update پاداش دوم نمی‌سازد؛ کاربرِ هنوز عضو کانال‌ها نشده پاداش start را تا تأیید عضویت دریافت نمی‌کند.

**قواعد:** BR-REF-01..03، BR-REF-07.<br>
**پیاده‌سازی:** `_record_referral`, `_grant_start_referral_reward` در `app/bot.py`؛ `record_referral`, `grant_start_rewards` در `app/db.py`.<br>
**تست:** `test_referral_start_link_grants_reward_once_and_updates_summary`، `test_referral_reward_is_exactly_once`.

### UC-03 — مرور کاتالوگ و جزئیات محصول

**هدف:** یافتن محصول قابل فروش بدون نمایش داده غیرفعال یا خارج از مسیر معتبر.

**بازیگر اصلی:** ACT-U<br>
**محرک:** دکمه «فروشگاه» یا callbackهای دسته/محصول.

**پیش‌شرط‌ها:** UC-01 موفق؛ محصول و مسیر دسته فعال‌اند.

**پس‌شرط موفق:** صفحه دسته/محصول متناسب با دسترسی، همراه قیمت، مدت، نوع، فعال‌سازی، تمدید، گارانتی، توضیحات و قوانین نمایش داده می‌شود.

**جریان اصلی:**

1. ریشه‌های فعال کاتالوگ صفحه‌بندی می‌شوند.
2. کاربر دسته و در صورت وجود زیردسته را انتخاب می‌کند.
3. فقط محصولات visible و مسیر فعال نشان داده می‌شوند.
4. summary محصول، متن اصلی `short_description` با rich text معتبر و دکمه خرید/توضیحات نمایش داده می‌شود؛ توضیح اصلی در صفحه محصول است و با متن تکمیلی جایگزین نمی‌شود.
5. در «توضیحات بیشتر»، تمام محتوای تکمیلی و URL/قواعد معتبر نمایش داده می‌شود. متن بلند هر دو صفحه به قطعه‌های HTML معتبر حداکثر ۳۹۰۰ نویسه تقسیم می‌شود؛ هیچ ادامه‌ای حذف نمی‌شود و دکمه‌های خرید/بازگشت در آخرین قطعه می‌مانند.

**جریان‌های جایگزین/خطا:** دسته/محصول حذف یا غیرفعال‌شده، ID خارج از مالکیت UI یا callback بد، پیام نامعتبر می‌گیرد؛ صفحه خارج از بازه به صفحه معتبر clamp می‌شود؛ محصول visible ولی unavailable ممکن است دیده شود اما خرید آن رد می‌شود.

**قواعد:** BR-ORD-01، BR-ORD-08، تصمیم‌های UX سند بیزنس.<br>
**پیاده‌سازی:** `show_store`, `show_category`, `show_product`, `show_product_details` در `app/bot.py`; `app/texts.py`, `app/keyboards.py`.<br>
**تست:** `test_rich_catalog_and_unavailable_visible_product`، `test_catalog_faq_and_join_surfaces_are_paginated`، `test_product_page_displays_primary_description_before_details`، `test_full_product_details_are_sent_without_oversized_messages`، `test_long_primary_description_preserves_all_text_and_final_actions_on_callback`، مجموعه `tests/test_keyboards.py`.

### UC-04 — ایجاد سفارش و تکمیل پروفایل خرید

**هدف:** ساخت یک سفارش یکتا با snapshot کامل شرایط فروش.

**بازیگر اصلی:** ACT-U<br>
**محرک:** لمس «خرید» روی محصول.

**پیش‌شرط‌ها:** UC-01؛ محصول قابل مرور و available؛ برای ready، موجودی موجود است یا reserve فعال است؛ کاربر blocked نیست.

**پس‌شرط موفق:** نام/شماره معتبر در پروفایل ثبت و یک Order با quantity=1، snapshot و expiry همراه summary پایدار `order:{id}:created-summary` در یک transaction ساخته می‌شود.

**جریان اصلی:**

1. سامانه وضعیت لحظه‌ای محصول و دسته را دوباره بررسی می‌کند.
2. اگر `customer_name` خالی است، نام ۳ تا ۱۰۰ نویسه دریافت می‌کند.
3. اگر phone خالی است، contact متعلق به همان Telegram user از دکمه `request_contact` دریافت می‌کند.
4. سقف ۱۰ سفارش باز بررسی می‌شود.
5. Order با idempotency key مشتق از user/product/update و callback ساخت summary داخل transaction ساخته می‌شود.
6. عنوان، نوع، قیمت، currency ثابت `TOMAN` و مدت محصول snapshot می‌شوند.
7. summary شامل مبلغ، کیف پول و گزینه تخفیف/پرداخت با کلید canonical در outbox commit و سپس state خرید پاک/پیام ارسال می‌شود.

**جریان‌های جایگزین/خطا:** نام نامعتبر دوباره درخواست می‌شود؛ contact تایپی/فرد دیگر رد می‌شود؛ stale state یا unavailableشدن محصول فرایند را پایان می‌دهد؛ ready بدون موجودی و بدون reserve سفارش نمی‌سازد؛ failure پیش از commit summary state شماره را حفظ می‌کند؛ failure ارسال یا replay همان update همان Order/notice را بازیابی و سفارش دوم را منع می‌کند؛ قیمت صفر در مسیر کاربر با defer_free_confirmation=True تا دکمه پرداخت pending می‌ماند؛ فراخوانی داخلی پیش‌فرض برای سازگاری همان رفتار قبلی را دارد.

**قواعد:** BR-ID-09، BR-ORD-01..08/15.<br>
**پیاده‌سازی:** `begin_purchase`, `_create_order_and_confirm`, `show_order_summary` در `app/bot.py`; `Database.create_order`.<br>
**تست:** `test_start_menu_purchase_requires_own_contact_and_creates_order`، `test_user_amount_and_contact_inputs_are_strict`، `test_order_and_payment_idempotency_keys_cannot_cross_users`، `test_created_order_summary_survives_send_failure_and_update_replay`، `test_phone_state_survives_failure_before_created_summary_commit`.

### UC-05 — اعمال کد تخفیف

**هدف:** کاهش مبلغ یک سفارش باز بر اساس یک قانون معتبر و قابل حسابرسی.

**بازیگر اصلی:** ACT-U<br>
**محرک:** «ثبت کد تخفیف» و ارسال code.

**پیش‌شرط‌ها:** Order متعلق به کاربر و دقیقاً `pending_payment` است.

**پس‌شرط موفق:** OrderDiscount فعال ثبت، `discount_amount` و `payable_amount` به‌روز و شمارنده مصرف رزرو می‌شود؛ حتی با تخفیف کامل، خلاصه با همان دکمه‌ها نمایش داده می‌شود و سفارش تا تأیید «پرداخت» pending می‌ماند.

**جریان اصلی:**

1. مالکیت و وضعیت سفارش بررسی می‌شود.
2. code نرمال و قانون متناظر پیدا می‌شود.
3. active، window، دامنه محصول/کاربر، minimum، max uses و per-user limit بررسی می‌شوند.
4. مبلغ fixed/percent محاسبه و تا subtotal محدود می‌شود.
5. یک OrderDiscount در transaction ثبت و summary تازه نمایش داده می‌شود.
6. اگر مبلغ نهایی صفر باشد، فقط دکمه «پرداخت» با `confirm_zero_payable_order` مالکیت، مهلت و نبود پوشش کیف پول/پرداخت بیرونی را بررسی و Order را paid می‌کند؛ اعلان موفقیت و سپس fulfillment اجرا می‌شوند و Payment صفرمبلغ ساخته نمی‌شود.

**جریان‌های جایگزین/خطا:** code ناموجود/منقضی/خارج دامنه/مصرف‌شده پیام عمومی نامعتبر می‌گیرد؛ تخفیف دوم هم‌زمان رد می‌شود؛ سفارش expired با تخفیف ۱۰۰٪ احیا نمی‌شود؛ بازگشت state ورودی را پاک می‌کند.

**قواعد:** BR-DSC-01..05، BR-ORD-07..09.<br>
**پیاده‌سازی:** state `discount_code` و callback `checkout` در `app/bot.py`; `Database.apply_discount`, `confirm_zero_payable_order`.<br>
**تست:** `test_discounts_are_single_and_released_on_expiry`، `test_extended_discount_create_and_safe_delete`، `test_expired_order_cannot_be_revived_by_full_discount`، `test_full_discount_keeps_updated_summary_until_explicit_confirmation`، `test_zero_confirmation_rejects_wrong_owner_expiry_and_wallet_coverage`، `test_free_product_waits_for_summary_confirmation_and_recovers_success_notice`.

### UC-06 — پرداخت کامل یا جزئی با کیف پول

**هدف:** استفاده امن از موجودی کیف پول، به‌تنهایی یا همراه پرداخت بیرونی.

**بازیگر اصلی:** ACT-U<br>
**محرک:** انتخاب «کیف پول» در روش پرداخت.

**پیش‌شرط‌ها:** روش wallet فعال؛ سفارش متعلق به کاربر و باز؛ مانده مثبت.

**پس‌شرط موفق:** تا سقف مبلغ لازم wallet hold ثبت می‌شود. اگر کافی باشد مبلغ capture، Order paid و اعلان canonical پایدار `order:{id}:wallet-confirmed` ایجاد می‌شود و فقط پس از sent یا terminalشدن تلاش آن fulfillment آغاز می‌گردد؛ اگر ناکافی باشد `payable_amount` بیرونی کاهش می‌یابد.

**جریان اصلی:**

1. سامانه مالکیت سفارش و فعال‌بودن روش را بررسی می‌کند.
2. مانده ledger و سهم باقی‌مانده سفارش محاسبه می‌شود.
3. WalletEntry یکتا برای hold در transaction ثبت می‌شود.
4. اگر سهم کیف پول کل مبلغ نهایی را پوشش دهد، hold capture و سفارش paid می‌شود، notice کامل شماره سفارش/محصول/مبلغ/روش queue می‌شود و gate fulfillment وضعیت آن را بررسی می‌کند.
5. در غیر این صورت، روش‌های بیرونی برای فقط remainder نمایش داده می‌شوند.

**جریان‌های جایگزین/خطا:** مانده صفر/ناکافی برای هیچ سهمی، هشدار می‌دهد؛ replay همان callback hold دوم نمی‌سازد؛ crash پس از paid و پیش از notice از Order فاقد `wallet-confirmed` با cursor/wrap بازیابی می‌شود؛ `queued/sending` همه شاخه‌های fulfillment را عقب می‌اندازد و failure/cancellation terminal پیام اجازه ادامه می‌دهد ولی در outbox قابل مشاهده می‌ماند؛ cancel مجاز card یا fail/expire آخرین پرداخت بیرونی hold آزادشدنی را برمی‌گرداند؛ پس از capture، refund shortcut وجود ندارد و بازپرداخت آینده به workflow مالی و entry جبرانی نیاز دارد.

**قواعد:** BR-PAY-01..08.<br>
**پیاده‌سازی:** commerce callback `paywallet` در `app/bot.py`; `hold_wallet_funds`, `_refund_wallet_hold`, `_refund_captured_wallet` در `app/db.py`.<br>
**تست:** `test_sufficient_wallet_pays_and_delivers_without_card`، `test_wallet_and_full_discount_success_notices_recover_without_starvation`، `test_partial_wallet_card_receipt_admin_callback_confirms_and_delivers`، `test_partial_wallet_hold_is_idempotent_and_expiry_refunds`، `test_partial_hold_cannot_be_refunded_by_a_status_shortcut`.

### UC-07 — پرداخت کارت‌به‌کارت خودکار

**هدف:** تطبیق قطعی یک رخداد بانکی با یک intent باز و تحویل بدون تأیید دستی.

**بازیگران:** ACT-U، ACT-B<br>
**محرک:** انتخاب کارت و سپس POST معتبر به `/payments/card/confirm`.

**پیش‌شرط‌ها:** card فعال و پیکربندی‌شده؛ Order/topup باز؛ callback secret فعال؛ ارتباط عمومی در production پشت HTTPS است.

**پس‌شرط موفق:** Payment paid با reference بیرونی؛ برای order، سهم کیف پول capture و Order paid؛ برای topup، WalletEntry واحد؛ رخداد بانک `confirmed` و اعلان/fulfillment آغاز می‌شود.

**جریان اصلی:**

1. سامانه بررسی می‌کند Order هیچ external intent فعال card/crypto دیگری نداشته باشد و برای remainder یک payable amount فعال و یکتا می‌سازد؛ مبلغ terminal کارت تا ۲۴ ساعت پس از `max(expires_at, updated_at)` quarantine است.
2. کاربر مبلغ دقیق شناسه‌دار و کارت مقصد را می‌بیند.
3. ACT-B بدنه JSON شامل `amount`, `reference`, `occurred_at` را می‌فرستد و secret را فقط در header `X-Payment-Secret` یا `Authorization: Bearer ...` قرار می‌دهد.
4. server نوع محتوا، اندازه، JSON strict، secret header، positivity، reference و timezone را بررسی می‌کند.
5. pending payment هم‌روش/هم‌currency ثابت `TOMAN`/هم‌مبلغ پیدا می‌شود.
6. زمان رخداد باید بعد از creation و تا expiry intent باشد.
7. Payment/Order یا topup در transaction تکمیل و CardPaymentEvent ثبت می‌شود.
8. برای Order، اعلان canonical `payment:{id}:order-confirmed` پیش از fulfillment attempt می‌شود؛ برای topup اعلان متناسب خودش صادر می‌شود.

**جریان‌های جایگزین/خطا:** external intent فعال روش دیگر ساخت card را رد می‌کند؛ لغو card بدون فیش payment و parent Order را اتمیک terminal می‌کند و تغییر روش به خرید/Order تازه نیاز دارد؛ retry همان reference نتیجه `already_confirmed` می‌دهد؛ secret بد 401؛ payload بد 400/413/415؛ مبلغ بی‌تطبیق 404 و event=`review`؛ reference/زمان ناسازگار 409 و event=`review`؛ رخداد قبل از ساخت intent یا بعد از expiry، همراه quarantine ۲۴ ساعته مبلغ terminal، هرگز به intent تازه هم‌مبلغ متصل نمی‌شود؛ missing success notice با maintenance بازیابی و تا خروج از `queued/sending` تحویل/رزرو/prompt متوقف می‌شود.

**قواعد:** BR-PAY-04..10، BR-PAY-13..14، BR-OPS-03.<br>
**پیاده‌سازی:** `app/payment_server.py`; `BotApplication.confirm_card_amount`, `_complete_payment`; payment/card-event methods در `app/db.py`.<br>
**تست:** همه `tests/test_payment_server.py`، `test_recent_terminal_card_amount_is_quarantined_before_reuse`، `test_delayed_cancelled_card_transfer_cannot_match_a_new_intent`، `test_card_confirmation_reference_is_idempotent_and_delivers_once`، `test_card_confirmation_before_payment_creation_conflicts_without_mutation`، `test_late_card_event_cannot_credit_an_intent_created_after_the_event`.

### UC-08 — ارسال و بررسی دستی فیش

**هدف:** فراهم‌کردن مسیر کنترل‌شده برای پرداخت کارت که خودکار تأیید نشده است.

**بازیگران:** ACT-U؛ ACT-A/ACT-O<br>
**محرک:** بعد از حداقل تأخیر، لمس «ارسال فیش» و ارسال photo/document.

**پیش‌شرط‌ها:** payment کارت متعلق به کاربر و `pending|verifying`؛ برای نخستین فیش هنوز `expires_at` نگذشته؛ برای replacement فیش به‌موقع موجود است و هنوز تصمیم نهایی ثبت نشده؛ receipt delay گذشته است.

**پس‌شرط موفق:** Payment `verifying`، receipt file ID ثبت، مدیران اعلان می‌گیرند؛ approve به paid و fulfillment/topup می‌رسد یا reject آن را failed و مسیر سفارش را reconcile می‌کند.

**جریان اصلی:**

1. کاربر پس از مهلت کوتاه خودکار، دکمه فیش را می‌زند.
2. سامانه مالکیت، method=`card`، status و deadline ثابت intent را بررسی می‌کند.
3. فقط photo/document دریافت و به payment متصل می‌شود.
4. payment به `verifying` و order به `awaiting_confirmation` می‌رود.
5. receipt و دکمه‌های تأیید/رد با اعلان پایدار برای مدیران مجاز ارسال می‌شود؛ maintenance اعلان جاافتاده را دوباره queue می‌کند.
6. مدیر با `/payment_detail PAYMENT_NUMBER` فیش را در نوع اصلی `photo/document` بازمی‌فرستد، مبلغ و کاربر را کنترل و `/approve_payment` یا `/reject_payment` اجرا می‌کند.
7. approve برای Order ابتدا اعلان canonical موفقیت را queue/attempt می‌کند و بعد از آماده‌شدن gate، UC-11/12/13 را آغاز می‌کند؛ topup فقط credit/اعلان خودش را دارد.

**جریان‌های جایگزین/خطا:** ارسال زودتر از delay پیام انتظار می‌دهد؛ crypto یا text بدون فایل رد می‌شود؛ نخستین فیش در/بعد از `expires_at` پذیرفته نمی‌شود؛ replacement فیش به‌موقع تا تصمیم نهایی مجاز است؛ گذشت زمان آن را خودکار منقضی نمی‌کند و مهلت نخستین ارسال تغییر نمی‌کند؛ entity نهایی فیش تازه نمی‌پذیرد؛ payment دارای فیش یا `verifying` حتی با callback قدیمی لغو نمی‌شود؛ failure شبکه‌ایِ اعلان مدیر فیش را از DB حذف نمی‌کند؛ replay approve/reject اثر دوم ندارد؛ support اجازه تأیید/رد ندارد.

**قواعد:** BR-PAY-07، BR-PAY-11..14، BR-ID-08.<br>
**پیاده‌سازی:** callback/state `receipt`/`payment_receipt` در `app/bot.py`; approve/reject در `app/admin.py`; `submit_payment_receipt`, `set_payment_status`, `mark_payment_paid` در `app/db.py`.<br>
**تست:** `test_first_receipt_is_rejected_after_the_payment_deadline`، `test_submitted_receipt_stays_pending_until_explicit_admin_decision`، `test_submitted_receipt_stays_reviewable_after_payment_deadline`، `test_user_cancellation_is_atomic_and_never_cancels_a_submitted_receipt`، `test_old_cancel_button_cannot_cancel_a_submitted_card_receipt`، `test_admin_payment_review_rejects_missing_receipt_and_non_card_intents`، `test_receipt_callback_approval_and_rejection`.

### UC-09 — پرداخت ارزی Plisio

**هدف:** پرداخت remainder سفارش یا topup با invoice ارزی و polling نتیجه.

**بازیگران:** ACT-U، ACT-P، ACT-W<br>
**محرک:** انتخاب «پرداخت ارزی».

**پیش‌شرط‌ها:** crypto فعال؛ API key و currency/network تنظیم؛ order/topup معتبر؛ provider در محل کسب‌وکار مجاز است.

**پس‌شرط موفق:** invoice و شناسه provider روی Payment ثبت؛ پس از status completed، Payment paid و ادامه سفارش/topup دقیقاً یک‌بار انجام می‌شود.

**جریان اصلی:**

1. مبلغ داخلی با multiplier پیکربندی‌شده به source amount provider تبدیل می‌شود.
2. تنها در نبود هر external intent فعال دیگر، Payment provisional برای Order/topup در DB ساخته و user/purpose/amount/terms ثابت می‌شود؛ `payment_number` به‌عنوان merchant order پایدار به `create_invoice(return_existing=1)` می‌رود.
3. شناسه و URL invoice با `attach_crypto_invoice` و revalidation به همان Payment اتمیک وصل می‌شود. URL فقط پس از validation به‌عنوان HTTPS مطلقِ بدون credential و host literal محلی/خصوصی، همراه شبکه/ارز و زمان پرداخت برای کاربر نمایش داده می‌شود. دکمه Back به جزئیات Order/کیف پول می‌رود و تا وقتی intent باز و URL همچنان امن است، «ادامه پرداخت ارزی» همان invoice را resume می‌کند؛ provisional ناقص دکمه retry همان terms و receipt فقط در روش card دارد.
4. worker ابتدا completed evidence ثبت‌شده ولی settleنشده را از DB بازیابی و سپس paymentهای provider را poll می‌کند.
5. پاسخ فقط پس از تطبیق `id` و `type=invoice` دسته‌بندی و همراه payload/hash در `provider_payment_events` ثبت می‌شود. `operation.amount` crypto دریافتی است، نه تومان؛ فیلدهای fiat/source داخل `params` فقط اگر حاضر باشند با terms intent مقایسه می‌شوند.
6. completed معتبر باعث `_complete_payment` می‌شود؛ برای Order اعلان canonical پیش از fulfillment attempt و برای topup credit/notice دقیقاً یک‌باره ثبت می‌شود. terminal با مبلغ دریافتی صفر payment را failed و parent را reconcile می‌کند.
7. partial/nonzero، مبلغ نامعلوم، status ناشناخته یا هویت ناسازگار payment را `verifying` نگه می‌دارد و با اعلان پایدار وارد review مالک می‌کند.

**جریان‌های جایگزین/خطا:** external intent card فعال ساخت crypto را رد می‌کند؛ برای تغییر روش، لغو card سفارش را terminal و ایجاد Order تازه را الزامی می‌کند؛ خطای مبهم remote create یا crash پیش از attach، provisional را باز نگه می‌دارد و retry همان `payment_number`/amount را resume می‌کند؛ wallet/discount پس از provisional نمی‌تواند invoice بیرونی را با terms تازه orphan کند؛ invoice ارزی صادرشده دکمه لغو ندارد و callback قدیمی cancel را repository نیز رد می‌کند؛ deadline محلی به‌تنهایی crypto را منقضی نمی‌کند و poll تا شاهد terminal provider ادامه دارد؛ خطای provider یا transport state را failed نمی‌کند و API key از URL/log/traceback redacted است؛ URL invoice از HTTP، credential، `localhost`، IP literal محلی/خصوصی/reserved یا host عددی مبهم ذخیره/نمایش نمی‌شود؛ DNS hostname resolve نمی‌شود و این validator تضمین DNS-level نیست؛ URL خالی یا legacy ناامن در render هیچ لینک/receipt اشتباهی نمی‌سازد و مسیر پشتیبانی نشان می‌دهد؛ invoice تکراری به intent متفاوت متصل نمی‌شود؛ crypto amount غیرصفر در status ناموفق partial/review است و mismatch `params` fiat/source در صورت حضور review می‌شود؛ completed دیررسِ payment باز reviewهای قبلی را با رخداد جدید می‌بندد و settle می‌شود؛ terminal-zero review قبلی را می‌بندد و failed می‌کند؛ crash پس از commit شاهد completed با replay DB و بدون network بازیابی می‌شود. اگر review قبلاً `dismiss/refund_confirmed` و payment/order terminal شده باشد، completed تازه یک review پرخطر جدید است و parent Order هرگز خودکار احیا نمی‌شود؛ owner تنها با شاهد exact completed می‌تواند topup را settle یا مبلغ order قدیمی را به‌عنوان اعتبار جبرانی کیف پول ثبت کند، نه revenue فروش سفارش قبلی. اشتباه network یا multiplier مسئله عملیاتی است و باید قبل از production end-to-end تست شود.

**قواعد:** BR-PAY-04..08، BR-PAY-13..20، BR-OPS-04، scope خارجی.<br>
**پیاده‌سازی:** `app/plisio.py`; `_begin_crypto_payment`, `_begin_crypto_topup`, `_poll_crypto_payments`, `_reconcile_completed_provider_events`, review alerts و crypto methods در `app/bot.py`; `attach_crypto_invoice` و provider event/resolution methods در `app/db.py`.<br>
**تست:** `test_crypto_invoice_is_not_orphaned_or_user_cancelled`، `test_crypto_order_provisional_freezes_terms_before_remote_side_effect`، `test_crypto_topup_provisional_intent_recovers_remote_create_ambiguity`، `test_concurrent_provisional_crypto_order_creation_reuses_one_intent`، `test_provisional_crypto_topup_invoice_attachment_is_exact_and_idempotent`، `test_order_detail_resumes_crypto_and_gates_receipts_by_method`، `test_wallet_resumes_active_crypto_topup_and_card_receipt_separately`، `test_late_observed_crypto_payments_settle_once_after_local_deadline`، `test_provider_expired_crypto_terminals_order_after_local_deadline`، `test_provider_outage_does_not_locally_expire_crypto_intents`، `test_late_completed_crypto_requires_owner_credit_and_never_revives_order`، `test_zero_failed_topup_is_watched_then_late_completed_is_reviewed`، `test_terminal_local_crypto_orders_remain_watched_without_revival`، `test_poll_cursor_reaches_payment_51_and_invalid_identity_never_credits`، `test_create_invoice_rejects_an_unsafe_provider_url`.

### UC-10 — شارژ کیف پول

**هدف:** افزایش مانده کیف پول از card یا crypto بدون اتصال به سفارش.

**بازیگر اصلی:** ACT-U<br>
**محرک:** کیف پول ← افزایش اعتبار ← مبلغ.

**پیش‌شرط‌ها:** UC-01؛ حداقل یک روش بیرونی فعال.

**پس‌شرط موفق:** Payment با purpose=`wallet_topup` و currency=`TOMAN` ایجاد و پس از تأیید یک WalletEntry نوع `topup` ثبت می‌شود.

**جریان اصلی:**

1. سامانه مبلغ با ارقام فارسی/لاتین و جداکننده‌های مجاز را parse می‌کند.
2. مبلغ باید بین `minimum_topup_amount` و `maximum_topup_amount` باشد؛ پیش‌فرض ۱۰٬۰۰۰ تا ۱۰۰٬۰۰۰٬۰۰۰.
3. روش card/crypto فعال نمایش داده می‌شود.
4. اگر برای کاربر در مجموع card/crypto topup فعالی نباشد intent ساخته می‌شود؛ replay فقط با همان روش، مبلغ و terms همان رکورد را برمی‌گرداند.
5. برای crypto ابتدا Payment provisional ثبت، `payment_number` به‌عنوان merchant order ثابت با `return_existing=1` به provider ارسال و شناسه/URL invoice به‌صورت اتمیک attach می‌شود.
6. UC-07/08/09 اجرا و پس از paid مانده تازه اعلام می‌شود.

**جریان‌های جایگزین/خطا:** مبلغ منفی، اعشاری، متن آزاد یا خارج بازه رد می‌شود؛ هر topup فعال با روش/مبلغ/terms متفاوت conflict است و هیچ intent ضمنی replace/cancel نمی‌شود؛ داده legacy ممکن است card و crypto فعال هم‌زمان داشته باشد و کیف پول باید هر دو را جداگانه برای receipt کارت یا resume URL ارز نشان دهد، اما create intent تازه دوم را رد می‌کند؛ خطای مبهم create یا crash پیش از attach، provisional را نگه می‌دارد و retry همان invoice/Payment را resume می‌کند؛ یکتاسازی مبلغ فقط برای card است و crypto مبلغ provider را تغییر نمی‌دهد؛ بیش از سقف پرداخت‌های فعال یا محدودیت anti-churn کارت رد می‌شود؛ تکرار confirmation credit دوم نمی‌سازد؛ refund topup بدون reversal هماهنگ رد می‌شود.

**قواعد:** BR-PAY-01، BR-PAY-04..08، BR-PAY-13.<br>
**پیاده‌سازی:** state `wallet_topup_amount`, `_begin_card_topup`, `_begin_crypto_topup` در `app/bot.py`; `create_wallet_topup_payment`, `attach_crypto_invoice` در `app/db.py`.<br>
**تست:** `test_wallet_topup_allows_only_one_active_method_under_concurrency`، `test_wallet_resumes_active_crypto_topup_and_card_receipt_separately`، `test_crypto_topup_provisional_intent_recovers_remote_create_ambiguity`، `test_provisional_crypto_topup_invoice_attachment_is_exact_and_idempotent`، `test_unique_payment_amounts_order_payment_and_wallet_topup`، `test_single_user_cannot_exhaust_card_amount_pool`، `test_wallet_topup_refund_cannot_leave_credit_spendable`، `test_user_amount_and_contact_inputs_are_strict`.

### UC-11 — تحویل خودکار محصول آماده

تکمیل UC-10: پس از انقضای شارژ بدون فیش، `_reconcile_expired_wallet_topup_notices` از Payment ثبت‌شده پیام پایدار `payment:{id}:topup-expired` شامل شماره، مبلغ، هشدار عدم پرداخت و دکمه کیف پول می‌سازد. query فاقد notice با cursor/wrap و budget محدود، انقضای commit‌شده پیش از restart را هم بازیابی می‌کند. crypto با deadline محلی منقضی نمی‌شود. تست‌ها: `test_expired_wallet_topup_warns_customer_not_to_pay_old_bill`، `test_wallet_topup_expiry_notice_recovers_after_committed_expiry_and_restart`.

**هدف:** تخصیص دقیقاً یک payload موجودی به یک سفارش paid.

**بازیگر اصلی:** ACT-W (آغاز مستقیم پس از پرداخت یا maintenance)<br>
**محرک:** Order ready وارد status موفق پرداخت می‌شود.

**پیش‌شرط‌ها:** محصول snapshot=`ready`؛ Order `paid|processing|awaiting_stock`؛ InventoryItem available؛ برای خرید تجاری، اعلان canonical موفقیت `sent|failed|cancelled` است.

**پس‌شرط موفق:** item `assigned` به همان order/user؛ Order `completed` با payload، completed/subscription end؛ reminderها ساخته؛ پیام تحویل پایدار ارسال/queue می‌شود.

**جریان اصلی:**

1. existing assignment و dependency اعلان موفقیت سفارش بررسی می‌شوند؛ `queued/sending` پیش از هر mutation جریان را متوقف می‌کند.
2. متن نهایی تحویل با snapshot محصول، payload و instruction رندر و کنترل می‌شود که در یک پیام و سقف ۳۹۰۰ جا شود.
3. قدیمی‌ترین item available در transaction claim می‌شود.
4. item به assigned و order به completed تغییر می‌کند.
5. subscription end از زمان assignment و duration snapshot محاسبه می‌شود.
6. reservation احتمالی fulfilled و reminderها ساخته می‌شوند.
7. payload فقط برای کاربر سفارش در پیام تحویل ارسال می‌شود.

**جریان‌های جایگزین/خطا:** replay fulfillment existing assignment را برمی‌گرداند؛ notice موفقیت `queued/sending` همه شاخه‌ها را defer می‌کند، ولی failure/cancellation terminal آن gate را باز و خطا را برای عملیات نگه می‌دارد؛ race فقط یک winner دارد؛ payload/instruction قدیمیِ بلند پیش از هر assignment/order mutation رد می‌شود و secret نه truncate و نه split می‌شود؛ crash پس از commit ولی قبل از پیام با reconciliation همان delivery idempotency key را queue می‌کند؛ نبود موجودی به UC-12 یا وضعیت `processing` برای restock می‌رود. در حالت دوم transition commit‌شده منبع بازیابی alert پایدار کاربر و owner/admin است و maintenance پس از ورود stock فقط وقتی آن را خودکار fulfil می‌کند که reservation معتبر قدیمی‌تری برای همان product باقی نمانده باشد؛ cap batch رزرو FIFO را دور نمی‌زند. حتی اگر `reward_processed_at` پیش از crash پر شده باشد selector مستقل status=`paid` fulfillment را ادامه می‌دهد.

**قواعد:** BR-FUL-01..03، BR-FUL-06..09، BR-OPS-03..04.<br>
**پیاده‌سازی:** `_assign_inventory`, `assign_inventory`, `fulfill_next_processing_ready_order`, `fulfill_order`; reconciliation در `app/bot.py`.<br>
**تست:** `test_inventory_assignment_is_atomic_under_concurrency`، `test_crash_after_inventory_assignment_is_reconciled_to_one_delivery`، `test_non_reserved_ready_stock_race_completes_after_restock`، `test_long_delivery_is_rejected_before_inventory_or_order_mutation`.

### UC-12 — رزرو و تحویل پس از تأمین موجودی

**هدف:** حفظ تعهد فروش ready پرداخت‌شده هنگام نبود موجودی و تحویل منصفانه FIFO.

**بازیگران:** ACT-U، ACT-W، ACT-A<br>
**محرک:** fulfillment سفارش ready بدون item و reserve فعال.

**پیش‌شرط‌ها:** Order paid؛ `reserve_enabled=1`؛ اعلان canonical موفقیت دیگر `queued/sending` نیست.

**پس‌شرط موفق اولیه:** Reservation `queued` و Order `awaiting_stock`; اعلان رزرو.<br>
**پس‌شرط نهایی:** با ورود موجودی، قدیمی‌ترین reservation به UC-11 می‌رسد.

**جریان اصلی:**

1. سامانه dependency اعلان موفقیت را بررسی و سپس برای همان Order یک reservation یکتا می‌سازد.
2. Order به awaiting_stock و کاربر مطلع می‌شود.
3. مدیر inventory تازه اضافه می‌کند.
4. worker قدیمی‌ترین queue معتبر همان product را claim می‌کند.
5. item assign، reservation fulfilled و delivery ارسال می‌شود.

**جریان‌های جایگزین/خطا:** replay سفارش دوم در صف نمی‌سازد؛ دو order مجزا از یک user هرکدام queue مستقل دارند؛ reservation بدون order برای notify-me فقط یکی به ازای user/product است؛ order refunded یا نامعتبر از fulfillment عبور نمی‌کند؛ اگر reserve خاموش و race بعد از پرداخت رخ دهد، order `processing` و مدیر مطلع می‌شود و پس از restock فقط وقتی worker آن را به UC-11 می‌فرستد که reservation معتبر قدیمی‌تری برای همان product باقی نباشد.

**قواعد:** BR-FUL-01..04، BR-FUL-06.<br>
**پیاده‌سازی:** `reserve_product`, `fulfill_next_reservation` در `app/db.py`; `_fulfill_reserved_inventory` در `app/bot.py`.<br>
**تست:** `test_maintenance_delivers_new_stock_to_oldest_paid_reservation`، `test_inventory_assignment_is_atomic_under_concurrency`.

### UC-13 — فعال‌سازی محصول دستی

تکمیل UC-11/12: همه entry pointهای تخصیص ready، از checkout تازه تا reservation و processing، نخستین Order پرداخت‌شده واجد شرایط همان محصول را مقدم می‌دانند؛ `_assign_inventory` این قید را داخل transaction حتی پیش از ساخته‌شدن reservation قدیمی کنترل می‌کند. `_allocate_paid_timestamp` ترتیب paid شدن تازه در یک ثانیه را با میکروثانیه افزایشی ثبت می‌کند؛ tie legacy فقط از reservation موجود و سپس Order ID استفاده می‌کند. تست‌ها: `test_new_purchase_cannot_jump_existing_paid_reservation`، `test_fifo_uses_payment_order_even_before_reservation_is_queued`.

**هدف:** دریافت امن اطلاعات مشتری و تکمیل کنترل‌شده سرویس manual.

**بازیگران:** ACT-U، ACT-A/ACT-O؛ ACT-S فقط درخواست اصلاح<br>
**محرک:** paidشدن Order manual.

**پیش‌شرط‌ها:** snapshot نوع manual و Order paid؛ اعلان canonical موفقیت دیگر `queued/sending` نیست.

**پس‌شرط موفق:** اطلاعات customer روی Order ذخیره؛ status processing؛ manager مجاز با متن تحویل Order را completed می‌کند؛ subscription end/reminder از completion محاسبه می‌شود.

**جریان اصلی:**

1. سامانه پس از آماده‌شدن dependency اعلان موفقیت، Order را `awaiting_info` و prompt محصول را ارسال می‌کند.
2. کاربر «ارسال اطلاعات» را می‌زند و متن، photo یا document می‌فرستد.
3. repository مالکیت، manualبودن و status را دوباره بررسی و customer_info و transition به `processing` را در یک transaction ذخیره می‌کند.
4. alert پایدارِ نسخه‌دار بر پایه hash کامل `customer_info_json` برای owner/admin ساخته می‌شود؛ maintenance نسخه commit‌شده فاقد alert را پس از restart دوباره پیدا می‌کند.
5. owner/admin فعال‌سازی را انجام و `/complete ORDER | delivery text` می‌فرستد.
6. متن نهایی همراه سربرگ سفارش پیش از mutation با سقف ۳۹۰۰ سنجیده می‌شود.
7. Order completed؛ completed_at/subscription_ends_at/reminder ثبت و متن تحویل به کاربر ارسال می‌شود.

**جریان‌های جایگزین/خطا:** ورودی خالی، actor غیرمالک، نوع غیرmanual یا status نهایی رد می‌شود؛ state قدیمی entity بسته را تغییر نمی‌دهد؛ replay همان payload در `processing` no-op و replacement واقعی نسخه/alert تازه دارد؛ crash نمی‌تواند payload تازه را در `awaiting_info` رها کند و ردیف legacy دارای payload برای alert recovery دیده می‌شود؛ manager/support با `/request_info` سفارش manual را به awaiting_info برمی‌گرداند و prompt اصلاح می‌فرستد؛ support نمی‌تواند پیوست manual را بازفرستد یا complete کند؛ تکمیل order ready، سفارش manual خارج `processing`، سفارش بدون اطلاعات معتبر مشتری یا پیام نهایی بلند رد می‌شود؛ رد طول پیش از mutation است؛ replay همان متن تحویل idempotent است و متن متفاوت delivery قبلی را بازنویسی نمی‌کند.

**قواعد:** BR-FUL-05..10، BR-ID-08، BR-ORD-10.<br>
**پیاده‌سازی:** `fulfill_order`, state `order_information` در `app/bot.py`; `_request_info`, `_complete` در `app/admin.py`; `submit_manual_order_info`, `complete_order` در `app/db.py`.<br>
**تست:** `test_manual_information_submission_atomically_enters_processing`، `test_support_cannot_change_or_complete_orders_but_owner_can`، `test_stale_states_are_cleared_and_closed_entities_are_not_mutated`، `test_ready_orders_reject_manual_completion_and_information_requests`، `test_manual_completion_commits_its_delivery_notice_before_network_send`، `test_long_delivery_is_rejected_before_inventory_or_order_mutation`، `test_receipt_and_manual_attachment_are_recoverable_from_committed_state`.

### UC-14 — مشاهده حساب، سفارش‌ها و تراکنش‌ها

**هدف:** ارائه دید self-service از پروفایل و سوابق بدون نشت داده دیگران.

**بازیگر اصلی:** ACT-U<br>
**محرک:** «حساب من»، «آمار»، «سفارش‌ها»، «تراکنش‌ها» یا `/orders`.

**پیش‌شرط‌ها:** UC-01.

**پس‌شرط موفق:** فقط summary و entityهای متعلق به User، صفحه‌بندی‌شده و با total صحیح نمایش داده می‌شوند.

**جریان اصلی:** پروفایل و آمار کل خوانده می‌شود؛ سفارش‌ها از جدید به قدیم؛ تراکنش‌ها از ledger/payments به‌صورت صفحه‌ای با تاریخ، مبلغ و نوع فارسی مستقل از دلیل آزاد؛ انتخاب سفارش جزئیات snapshot، مبلغ و status را نشان می‌دهد.

**جریان‌های جایگزین/خطا:** order ID متعلق به کاربر دیگر not found دیده می‌شود؛ callback صفحه malformed پاسخ داده و query معلق نمی‌ماند؛ توضیح بسیار بلند clamp/chunk می‌شود ولی هیچ entry از صفحه‌بندی حذف نمی‌شود؛ صفحه خارج محدوده اصلاح می‌شود.

**قواعد:** BR-ORD-08، BR-PAY-01، تصمیم UX pagination.<br>
**پیاده‌سازی:** `show_account`, `show_stats`, `show_orders`, `show_transactions`, `show_order` در `app/bot.py`; queryهای history در `app/db.py`.<br>
**تست:** `tests/test_user_history_pagination.py`، `test_transaction_pagination_never_drops_clamped_long_entries`.

### UC-15 — FAQ، تیکت و مکالمه پشتیبانی

**هدف:** پاسخ self-service و ایجاد پرونده مکالمه قابل پیگیری با پیوست.

**بازیگران:** ACT-U؛ ACT-S/ACT-A/ACT-O<br>
**محرک:** «پشتیبانی» و انتخاب FAQ یا تیکت جدید/موجود.

**پیش‌شرط‌ها:** UC-01.

**پس‌شرط موفق:** FAQ فعال نمایش داده می‌شود یا Ticket/Message با مالکیت صحیح ساخته و دو طرف مطلع می‌شوند.

**نمایش FAQ:** جواب کامل با قالب‌بندی HTML معتبر خوانده می‌شود؛ جواب بلند به چند پیام امن تقسیم می‌شود و دکمه بازگشت به دسته در آخرین پیام قرار می‌گیرد. محدودیت اندازه پیام نباید انتهای جواب را حذف کند.

**جریان اصلی تیکت:**

1. کاربر موضوع ۳ تا ۱۲۰ نویسه می‌فرستد.
2. شرح متنی یا photo/document می‌فرستد.
3. Ticket `open` و نخستین TicketMessage با idempotency key ساخته می‌شود.
4. همه owner/admin/support فعال با alert پایدار per-admin مطلع می‌شوند؛ attachment با فرمان `/ticket_attachment MESSAGE_ID` در همان alert قابل بازیابی است.
5. کاربر/پشتیبان در تیکت باز پاسخ می‌دهند؛ مکالمه از جدید به قدیم صفحه‌بندی می‌شود. owner/admin/support از `/ticket` شناسه پیام فایل‌دار را می‌گیرند و `/ticket_attachment MESSAGE_ID` همان photo/document commit‌شده را پس از revalidation نقش و entity بازمی‌فرستد.
6. مدیر status را answered/closed می‌کند یا closed را reopen می‌کند.

**جریان‌های جایگزین/خطا:** موضوع یا body نامعتبر دوباره درخواست می‌شود؛ replay پیام دوم نمی‌سازد؛ user به تیکت/پیوست دیگران دسترسی ندارد؛ actor بدون role، MESSAGE_ID ناموجود، پیام بدون فایل یا kind خارج photo/document رد می‌شود و raw file ID در متن جزئیات افشا نمی‌شود؛ شکست copy/alert شبکه metadata را از DB حذف نمی‌کند، maintenance با cursor/wrap alert per-admin را بازیابی و فرمان attachment را ارائه می‌کند؛ closed پاسخ user نمی‌پذیرد؛ reply/status/close و notice کاربر در transaction/outbox پایدارند تا crash میان mutation و send اعلان را گم نکند؛ FAQ غیرفعال یا callback قدیمی نمایش داده نمی‌شود.

**قواعد:** BR-SUP-01..03، BR-ORD-08.<br>
**پیاده‌سازی:** support/ticket methods در `app/bot.py`; ticket/FAQ repository در `app/db.py`; commands در `app/admin.py`.<br>
**تست:** `test_faq_ticket_and_outbound_message_queues`، همه `tests/test_user_history_pagination.py`، `test_ticket_reply_callback_checks_existence_owner_and_open_status`، `test_ticket_idempotency_rejects_cross_user_and_cross_ticket_keys`، `test_ticket_attachments_are_retrievable_after_restart_by_support`، `test_faq_callback_preserves_the_complete_formatted_answer_and_back_action`.

### UC-16 — لینک دعوت، آمار و پاداش خرید

**هدف:** نمایش لینک/آمار و اعطای پاداش خرید به دعوت‌کننده.

**بازیگر اصلی:** ACT-R؛ **رویدادساز:** ACT-U<br>
**محرک:** «دعوت و کسب درآمد» یا paidشدن خرید تجاری invitee.

**پیش‌شرط‌ها:** ACT-R User معتبر؛ برای پاداش خرید Referral و rule منطبق وجود دارد و Order از نوع `order_origin=customer` با `subtotal_amount > 0` است.

**پس‌شرط موفق:** لینک deep-link، تعداد invited/qualified و reward total نمایش داده می‌شود؛ در خرید منطبق، WalletEntry/RewardEvent دقیقاً یک‌بار و اعلان پایدار ایجاد می‌شود.

**جریان اصلی:**

1. سامانه bot username و Telegram ID کاربر را به deep link تبدیل می‌کند.
2. summary دعوت، مجموع eventهای پاداش و توضیح تمام قانون‌های فعال داخل بازه فعلی را نمایش می‌دهد: نوع رویداد، مبلغ، محصولات مشمول، همه شروط ترکیبی و مرز زمانی صریح UTC. شروط تعداد خرید/دعوت متعلق به دوست دعوت‌شده‌اند. قانون غیرفعال، منقضی یا هنوز شروع‌نشده وعده داده نمی‌شود؛ نبود قانون جاری صریح اعلام می‌شود. خواندن قانون‌ها offset دارد و ادامه فهرست یا متن بلند حذف نمی‌شود؛ دکمه ارسال لینک در آخرین پیام می‌ماند.
3. پس از خرید تجاری موفق invitee، ruleهای product_purchase، first_purchase و combined در زمان purchase ارزیابی می‌شوند.
4. تمام شروط combined با AND بررسی می‌شوند.
5. پاداش دعوت‌کننده credit و Referral qualified می‌شود.
6. اعلان `reward:{id}:notice` پایدار queue و سپس marker پاداش Order ثبت می‌شود.
7. maintenance مستقل از marker سفارش، همه `reward_event`های فاقد notice، از جمله `start`، را با cursor/wrap بازیابی می‌کند؛ fulfillment سفارش paid نیز selector جدا دارد.

**جریان‌های جایگزین/خطا:** بدون referral نتیجه noop است؛ rule خارج window/غیرفعال/بعد از خرید اعمال نمی‌شود؛ تخصیص `admin_assignment` و Order داخلی با subtotal صفر purchase event نیست و نخستین خرید واقعی را مصرف نمی‌کند؛ crash میان چند rule در maintenance ادامه می‌یابد؛ crash پس از credit و قبل از notice از `reward_event` commit‌شده بازیابی می‌شود؛ event تکراری reward دوم نمی‌دهد؛ refunded در تعریف خرید موفق جدید نیست و clawback خودکار خارج scope است.

**قواعد:** BR-REF-02..07.<br>
**پیاده‌سازی:** `show_referral`, `_reconcile_purchase_rewards`, `_reconcile_reward_notices`, `list_reward_events_missing_notice`; reward methods در `app/db.py`.<br>
**تست:** `test_referral_reward_is_exactly_once`، `test_first_purchase_reward_counts_paid_orders_waiting_for_stock`، `test_reward_reconciliation_survives_partial_grant_after_completion`، `test_reward_notice_recovery_rotates_past_start_reward_crashes`، `test_purchase_reward_window_uses_event_time_during_late_recovery`، `test_reward_rule_created_after_purchase_is_not_retroactive`، `test_admin_assignment_and_internal_free_order_are_not_commercial_purchases`، `test_referral_explains_active_reward_amounts_scope_conditions_and_window`، `test_referral_without_current_rule_does_not_promise_a_reward`، `test_referral_explanation_reaches_rules_beyond_default_repository_limit`.

### UC-17 — مشاهده کانال رسمی

**هدف:** هدایت کاربر به URL کانال تنظیم‌شده.

**بازیگر اصلی:** ACT-U<br>
**محرک:** دکمه «کانال».

**پیش‌شرط‌ها:** UC-01.

**پس‌شرط موفق:** دکمه inline «کانال» در خود منوی اصلی مستقیماً URL امن و تنظیم‌شده `https://t.me/...` را باز می‌کند؛ ارسال یک لینک واسطه پس از لمس دکمه لازم نیست.

**جایگزین:** اگر کانال تنظیم نشده یا URL نامعتبر است، دکمه از callback کنترل‌شده `channel` استفاده می‌کند که پیام عدم دسترس‌بودن نشان می‌دهد و لینک ناسالم نمی‌سازد. مسیر متنی قدیمی «کانال» همچنان با `show_channel` لینک معتبر را نمایش می‌دهد.

**قواعد:** BR-ID-01، امنیت لینک.<br>
**پیاده‌سازی:** `inline_main_menu_keyboard` در `app/keyboards.py`؛ `show_main_menu`, `show_channel`, `_dispatch_user_callback` در `app/bot.py`؛ `/set_channel` در `app/admin.py`.<br>
**تست:** `test_main_channel_button_opens_configured_channel_directly`، `test_inline_main_menu_preserves_layout_styles_icons_and_safe_actions`، `test_every_main_menu_callback_reaches_its_user_route`.

## ۵. یوزکیس‌های مدیریت و عملیات

### UC-20 — bootstrap و مدیریت دسترسی مدیران

**هدف:** ایجاد owner اولیه و اداره امن نقش‌ها.

**بازیگران:** ACT-O و ACT-A<br>
**محرک:** شروع سرویس یا `/admins`, `/admin_add`, `/admin_enable`, `/admin_disable`.

**پیش‌شرط‌ها:** bootstrap username/chat ID در environment؛ actor مدیر فعال و verifyشده.

**پس‌شرط موفق:** Admin یکتا با username/chat ID/role، `identity_verified_at`, marker root و `created_by` ثبت یا وضعیت دسترسی تغییر می‌کند؛ grant pending تا proof مجوز ندارد.

**جریان اصلی:**

1. startup و `app.main --check` schema 11، owner bootstrap و marker یکتای `is_bootstrap_owner` را idempotently ایجاد/اعتبارسنجی می‌کنند؛ DB پس از seed مرجع role/active است.
2. bootstrap username-only یک root pending می‌سازد؛ فقط نخستین private update با همان username آن chat را اثبات می‌کند. با هر دو شناسه، binding در startup کامل است.
3. owner/admin فهرست مدیران را همراه وضعیت «در انتظار تأیید هویت»، فعال یا غیرفعال می‌بیند.
4. افزودن با هر دو username و chat ID و یکی از سه role انجام می‌شود. user شناخته‌شده فقط با تطبیق دقیق هر دو فوراً verify؛ زوج ناشناخته pending می‌شود تا همان chat/username یک update خصوصی معتبر—برای مثال `/start`—بفرستد.
5. پس از verify، chat ID anchor دسترسی است و update همان chat می‌تواند rename username را به metadata تازه تبدیل کند؛ chat دیگر نمی‌تواند username را تصاحب کند.
6. actor مجاز دسترسی رکورد هدف را فعال/غیرفعال می‌کند؛ owner می‌تواند username pending اشتباه را با `/admin_add` و همان chat اصلاح یا آن را disable کند.
7. rotation root فقط با configured chat ID یک owner فعال و verifyشده marker را منتقل می‌کند؛ رکورد پیشین خودکار re-enable/delete نمی‌شود.

**جریان‌های جایگزین/خطا:** mismatch زوج pending مجوز نمی‌گیرد؛ username یا chat ID متصل به دو شخص conflict و bootstrap به‌جای rebind کردن fail closed می‌شود؛ drift username روی همان chat verifyشده access را قطع نمی‌کند؛ restart owner root عمداً غیرفعال‌شده را دوباره فعال نمی‌کند؛ rotation به unknown/pending/inactive/non-owner و adoption legacy مبهم رد می‌شود؛ فقط owner می‌تواند owner جدید بسازد یا owner موجود را تغییر دهد؛ admin می‌تواند admin/support را اداره کند؛ self-disable و disable/تنزل آخرین owner فعال رد می‌شود؛ support به فرمان‌های مدیریت مدیر دسترسی ندارد؛ username-only bootstrap برای production پرریسک و موقت است.

**قواعد:** BR-ID-06..08.<br>
**پیاده‌سازی:** `bootstrap_admin`, `bind_admin_chat`, `add_admin`, `set_admin_active` در `app/db.py`; admin handlers در `app/admin.py`.<br>
**تست:** `test_check_bootstraps_and_validates_the_owner_identity`، `test_check_rejects_a_conflicting_persisted_owner_binding`، `test_delegated_admin_requires_a_proven_username_and_chat_pair`، `test_pending_admin_grant_activates_only_for_the_exact_telegram_identity`، `test_verified_owner_username_rename_keeps_stable_chat_access`، `test_bootstrap_root_survives_username_drift_without_creating_stale_owner`، `test_bootstrap_root_disable_persists_and_verified_owner_rotation_is_safe`، `test_legacy_root_adoption_rejects_a_conflicting_configured_chat`، `test_username_only_root_marker_can_rotate_to_a_proven_owner`، `test_admin_add_requires_both_identity_fields_and_prevents_owner_escalation`.

### UC-21 — تنظیم حالت ربات، پرداخت، کانال و جوین اجباری

**هدف:** کنترل availability و ورودی‌های فروش بدون تغییر کد.

**بازیگران:** ACT-A/ACT-O<br>
**محرک:** `/bot_on|off`, `/payment`, `/set_card`, `/set_channel`, `/joins`, `/join_*`.

**پیش‌شرط‌ها:** actor admin/owner فعال؛ private chat.

**پس‌شرط موفق:** setting یا کانال با validation ذخیره و در درخواست بعدی کاربران اعمال می‌شود.

**جریان اصلی:** تغییر bot_enabled؛ فعال/غیرفعال‌کردن wallet/card/crypto؛ ثبت کارت/صاحب کارت یا URL کانال؛ افزودن force-join با `getChat` و `getChatMember`؛ مشاهده فهرست، تغییر status یا حذف کانال اجباری. در نصب تازه wallet فعال است، card تا ثبت هم‌زمان شماره و صاحب حساب مخفی می‌ماند و crypto تا API key startup و `/payment crypto on` نمایش داده نمی‌شود.

**جریان‌های جایگزین/خطا:** روش ناشناخته رد؛ enable کارت بدون شماره+صاحب حساب و enable crypto بدون `PLISIO_API_KEY` fail closed و روش ناقص در UI مخفی است؛ لینک کانال اصلی فقط canonical `https://t.me/...` است؛ force-join فقط username `@...` یا chat ID `-100...` و invite canonical HTTPS روی `t.me`/`telegram.me` بدون port/query/fragment می‌پذیرد؛ HTTP، local/private یا دامنه مشابه جعلی رد می‌شود؛ اگر chat channel/supergroup نباشد یا bot admin/creator نباشد add/toggle-on رد می‌شود؛ خطای Telegram به activation موفق تبدیل نمی‌شود.

**قواعد:** BR-ID-03..05، BR-PAY-14/23.<br>
**پیاده‌سازی:** settings/force-join handlers در `app/admin.py`; settings/channel repository در `app/db.py`.<br>
**تست:** `test_unconfigured_payment_methods_are_hidden_and_cannot_be_enabled`، `test_main_channel_setting_accepts_only_safe_https_t_me_urls`، `test_join_and_product_rule_commands_validate_external_urls`، `test_force_join_revalidates_chat_type_and_bot_role_on_enable`، `test_large_user_lists_are_paged_and_join_lists_are_split_safely`.

### UC-22 — مدیریت دسته و محصول

**هدف:** ساخت و نگهداری کاتالوگ با حفظ سوابق فروش.

**بازیگران:** ACT-A/ACT-O<br>
**محرک:** `/categories`, `/category_*`, `/products`, `/product_*`.

**پیش‌شرط‌ها:** actor مجاز؛ والد/دسته مقصد موجود.

**پس‌شرط موفق:** درخت معتبر و Product با فیلدهای تجاری به‌روز؛ سفارش‌های قبلی بدون تغییر snapshot باقی می‌مانند.

**جریان اصلی:**

1. دسته ریشه یا زیردسته با نام، icon، description و sort ساخته می‌شود.
2. چرخه والد و uniqueness نام زیر والد کنترل می‌شود.
3. محصول ready/manual با قیمت integer فقط به `TOMAN` و مدت ساخته می‌شود.
4. توضیحات، نوع حساب، فعال‌سازی، تمدید، گارانتی، feature، قوانین، متن اطلاعات/تحویل، reminder و stock limit تنظیم می‌شوند؛ `reminder_days` فهرست عددهای صحیح نامنفی است؛ صفر یعنی آغاز روز محلی پایان اشتراک.
5. visible/available/reserve مستقل toggle می‌شوند.
6. حذف محصول soft-delete و حفظ سابقه است.

**جریان‌های جایگزین/خطا:** category دارای child/product حذف نمی‌شود؛ والد خود/چرخه رد؛ قیمت منفی، currency غیر `TOMAN`، duration نامعتبر و reminder منفی/اعشاری/boolean رد؛ صفر تازه یا legacy یادآوری روز پایان در timezone تنظیم‌شده را زمان‌بندی می‌کند؛ `rules_url` فقط URL مطلق HTTPS بدون credential و بدون host literal محلی/خصوصی/reserved یا عددی مبهم است؛ `duration` عددی duration_days را sync و label غیرعددی expiry را پاک می‌کند؛ تغییر ready به manual تا وجود هر inventory item رد؛ hard-delete محصول دارای سابقه انجام نمی‌شود.

**قواعد:** BR-ORD-01..04، BR-FUL-06..08.<br>
**پیاده‌سازی:** catalog handlers در `app/admin.py`; category/product methods در `app/db.py`.<br>
**تست:** `test_category_update_cycle_guard_and_safe_delete`، `test_product_extended_fields_soft_delete_and_type_guard`، `test_product_reminder_days_accept_same_day_and_reject_negative_days`، `test_rich_catalog_and_unavailable_visible_product`.

### UC-23 — مدیریت امن انبار

**هدف:** ثبت/ویرایش/توقف/واگذاری payload دیجیتال بدون افشا یا شکستن سابقه.

**بازیگران:** ACT-A/ACT-O<br>
**محرک:** `/inventory_add|edit|list|enable|disable|delete|assign`.

**پیش‌شرط‌ها:** Product ready؛ actor مجاز؛ برای mutation، item تحویل‌نشده مگر assign.

**پس‌شرط موفق:** item با hash یکتا و status درست ذخیره؛ در assign دستی، assignment، Order completed نوع `ADM-...` با `order_origin=admin_assignment` و marker پاداش ازپیش‌پردازش‌شده، و یک delivery outbox پایدار در یک transaction ثبت می‌شوند. این Order خرید/درآمد تجاری یا مبنای پاداش نیست.

**جریان اصلی:**

1. مدیر command اولیه را می‌فرستد.
2. برای add/edit، سامانه state امن می‌سازد و payload را از پیام بعد می‌گیرد.
3. پاسخ، payload قدیم/جدید را echo نمی‌کند.
4. item available/disabled قابل ویرایش یا تغییر status است.
5. repository در همان transaction نبود Order ready قدیمی‌تر همان product با status `paid|processing|awaiting_stock` و بدون item assigned را کنترل می‌کند؛ سپس item available را به user assign و Order صفرمبلغ completed با origin داخلی/marker پاداش، و outbox تحویل با کلید `order:{id}:delivery` را atomically می‌سازد.

**جریان‌های جایگزین/خطا:** duplicate hash همان product رد؛ payload یا مشخصات مؤثر محصول که خروجی نهایی تحویل را از ۳۹۰۰ نویسه بیشتر کند پیش از ذخیره رد می‌شود؛ item legacy بلند نیز پیش از assignment بدون mutation رد می‌شود؛ assigned قابل edit/delete/enable/disable/واگذاری دیگر نیست؛ وجود backlog ready همان product تخصیص مستقیم را conflict می‌کند تا FIFO دور زده نشود؛ تغییر نوع Product به manual با inventory متصل رد؛ race assignment فقط یک winner؛ failure ساخت outbox کل assignment/order را rollback می‌کند؛ failure شبکه پس از commit از همان outbox retry می‌شود؛ `/cancel` state را پاک می‌کند.

**قواعد:** BR-FUL-01، BR-FUL-04/09، BR-OPS-06.<br>
**پیاده‌سازی:** inventory handlers در `app/admin.py`; inventory methods در `app/db.py`.<br>
**تست:** `test_sensitive_inventory_is_collected_through_user_state_without_echo`، `test_inventory_enable_delete_and_assigned_guard`، `test_inventory_assignment_is_atomic_under_concurrency`، `test_manual_inventory_delivery_uses_one_atomically_queued_notice`، `test_long_delivery_is_rejected_before_inventory_or_order_mutation`، `test_admin_assignment_and_internal_free_order_are_not_commercial_purchases`.

### UC-24 — پایش و تعیین تکلیف سفارش/پرداخت

**هدف:** مشاهده صف عملیات و اعمال transition مجاز همراه با اثر مالی درست.

**بازیگران:** مشاهده خلاصه: ACT-S/A/O؛ فیش و پیوست manual-order و mutation وضعیت/پرداخت: ACT-A/O؛ request info: ACT-S/A/O<br>
**محرک:** `/orders`, `/order`, `/order_attachment`, `/order_status`, `/request_info`, `/complete`, `/approve_payment`, `/reject_payment`, `/payment_detail`, `/card_reviews`, `/card_resolve`, `/crypto_reviews`, `/crypto_resolve`.

**پیش‌شرط‌ها:** مدیر فعال؛ entity موجود؛ command متناسب با role/status.

**پس‌شرط موفق:** status و admin note معتبر؛ اعلان کاربر در outbox پایدار؛ در approve پرداخت، پرداخت/کیف پول/fulfillment هماهنگ؛ در reject، hold و وضعیت والد reconcile.

**جریان اصلی:** مدیر `/orders [STATUS|all] [FROM TO] [PAGE]` را با ترتیب `id DESC`، page size ۲۰، total/تعداد صفحه و راهنمای قبلی/بعدی تا انتهای backlog پیمایش می‌کند؛ ردیف‌های یک صفحه در صورت بلندی به چند پیام شکسته می‌شوند و حذف نمی‌شوند. سپس جزئیات/پیوست manual را بازمی‌فرستد؛ هر نسخه فیش یا customer info ابتدا commit و با hash محتوایی alert پایدار جدا می‌گیرد؛ برای سفارش manual اطلاعات تازه می‌خواهد یا فقط از `processing` و پس از ثبت اطلاعات معتبر مشتری complete می‌کند؛ فیش کارت در وضعیت `verifying` را با `/payment_detail` بازبینی و approve/reject می‌کند؛ review کارت/provider را فهرست می‌کند و owner با action و note حسابرسی‌پذیر تعیین تکلیف می‌کند؛ برای status عمومی فقط transition مجاز و غیرحساس را همراه اعلان اتمیک اعمال می‌کند.

**جریان‌های جایگزین/خطا:** صفحه کمتر از ۱/بیشتر از آخرین یا status/date نامعتبر بدون query ناقص رد می‌شود؛ support mutation حساس و دسترسی `/payment_detail` یا `/order_attachment` ندارد، ولی فهرست/جزئیات سفارش و پیوست تیکت را با `/ticket_attachment MESSAGE_ID` و revalidation در دامنه پشتیبانی می‌بیند؛ retry همان attachment version alert دوم نمی‌سازد و replacement نسخه تازه می‌سازد؛ `/order_status` مستقیماً paid/completed/refunded نمی‌سازد و مقصد cancelled/expired/rejected را در حضور external payment `pending/verifying` نیز رد می‌کند؛ فیش card فقط با `/reject_payment` و crypto فقط پس از evidence terminal تعیین تکلیف می‌شود؛ پیام نهایی بلند پیش از تغییر status/payment/order رد می‌شود؛ تکمیل دستی ready، وضعیت غیر-`processing` یا نبود اطلاعات مشتری رد می‌شود؛ approve/reject دستی برای crypto، پرداخت بدون فیش یا وضعیت غیرقابل‌بررسی رد می‌شود؛ card resolve هرگز credit نمی‌کند و crypto credit فقط با completed evidence دقیق مجاز است؛ replay همان تصمیم نهایی idempotent و تصمیم مخالف یا external reference متفاوت conflict است؛ رد آخرین پرداخت بیرونی سفارش منقضی‌نشده را pending می‌کند؛ late completed پس از terminalشدن review سفارش قبلی را احیا نمی‌کند و فقط review تازه می‌سازد؛ payment `paid` terminal است و `set_payment_status(refunded)` نیز عمداً رد می‌شود، چون workflow refund عمومی در نسخه فعلی وجود ندارد.

**قواعد:** BR-ORD-10..11، BR-PAY-02..08، BR-ID-08.<br>
**پیاده‌سازی:** order/payment handlers در `app/admin.py`; transition/payment methods در `app/db.py`; `fulfill_order` در `app/bot.py`.<br>
**تست:** `test_order_and_ticket_indexes_page_every_record_without_clamping`، `test_order_status_rejects_completion_and_refund_shortcuts`، `test_manual_payment_approve_and_reject`، `test_admin_payment_review_rejects_missing_receipt_and_non_card_intents`، `test_ready_orders_reject_manual_completion_and_information_requests`، `test_paid_payment_rejects_a_different_external_reference`، `test_failed_last_external_payment_reopens_order`، `test_status_rejection_and_info_notice_commit_atomically`، `test_mutating_admin_notices_validate_before_domain_change`، `test_receipt_and_manual_attachment_are_recoverable_from_committed_state`.

### UC-25 — مدیریت کاربر و اصلاح کیف پول

**هدف:** یافتن/مسدودکردن مشتری، مشاهده کامل تاریخچه تجاری/دعوت، ارتباط مستقیم و correction حسابرسی‌پذیر مانده.

**بازیگران:** مشاهده/message: ACT-S/A/O؛ block/wallet: ACT-A/O<br>
**محرک:** `/users`, `/user`, `/user_orders`, `/user_transactions`, `/user_referrals`, `/user_rewards`, `/block`, `/unblock`, `/message`, `/wallet_adjust`.

**پیش‌شرط‌ها:** مدیر فعال؛ user identifier معتبر.

**پس‌شرط موفق:** query/تغییر block یا message انجام؛ همه فهرست‌ها total و پیمایش کامل دارند؛ adjustment signed همراه reason و actor در ledger ثبت.

**جریان اصلی:** مدیر `/users` را با فیلتر all/active/blocked/new/inactive/joined/product و صفحه اختیاری، ۲۰ ردیف در صفحه و ترتیب `id DESC` می‌بیند؛ `/user` با chat ID/username/order خلاصه، تعدادهای کامل و MIN/MAX دقیق اولین/آخرین خرید تجاری بدون cap را نشان می‌دهد. `/user_orders USER [STATUS|all] [PAGE|ORDER_NUMBER]` سفارش‌های همان user را فیلتر/جست‌وجو و تمام اطلاعات متنی محصول دستی را بدون حذف ادامه نمایش می‌دهد؛ `/user_transactions USER [PAGE]`، `/user_referrals USER [PAGE]` و `/user_rewards USER [PAGE]` تمام ledger، invitee status/date و جزئیات event/مبلغ/invitee/order را صفحه‌بندی می‌کنند. سپس مدیر در دامنه نقش block/unblock، پیام مستقیم یا adjustment مثبت/منفی با دلیل انجام می‌دهد.

**جریان‌های جایگزین/خطا:** فیلتر تاریخ/day یا صفحه خارج بازه رد؛ یک عدد تنها پس از `new|inactive` برای سازگاری DAYS است و PAGE دومین عدد؛ active/inactive overlap ندارند؛ output بلند chunk می‌شود ولی هیچ ردیف همان صفحه حذف نمی‌شود؛ ORDER_NUMBER متعلق به user دیگر not found است؛ support فقط مشاهده چهار تاریخچه و message دارد؛ adjustment بدون reason یا کلید collision رد؛ اصلاح اشتباه با entry معکوس است نه delete/update ledger.

**قواعد:** BR-ID-02، BR-PAY-01، BR-RPT-01/02/04، BR-MSG-02.<br>
**پیاده‌سازی:** user handlers در `app/admin.py`; user/wallet methods در `app/db.py`.<br>
**تست:** `test_user_and_order_filters`، `test_active_and_inactive_user_filters_are_disjoint`، `test_large_user_lists_are_paged_and_join_lists_are_split_safely`، `test_user_history_commands_page_filter_search_and_show_reward_details`، `test_user_profile_purchase_dates_come_from_uncapped_aggregate`، `test_wallet_idempotency_rejects_cross_operation_collisions`.

### UC-26 — مدیریت تخفیف

**هدف:** ساخت و توقف قوانین کاهش قیمت بدون تخریب audit.

**بازیگران:** ACT-A/ACT-O<br>
**محرک:** `/discounts`, `/discount_add`, `/discount_toggle`, `/discount_delete`.

**پیش‌شرط‌ها:** product/user هدف در صورت انتخاب موجود؛ مقادیر و تاریخ‌ها معتبر.

**پس‌شرط موفق:** Discount با code یکتا، type/value، scope، window و limits ثبت یا toggle/delete امن می‌شود.

**جریان‌های جایگزین/خطا:** percent>100، fixed/limit نامثبت، start بعد end یا target ناموجود رد می‌شود؛ تکرار code با همان terms همان رکورد را idempotently برمی‌گرداند، ولی استفاده همان code با terms متفاوت conflict است؛ تخفیف دارای سابقه حذف نمی‌شود؛ غیرفعال‌سازی بر snapshot مصرف گذشته اثر ندارد.

**قواعد:** BR-DSC-01..05.<br>
**پیاده‌سازی:** discount handlers در `app/admin.py`; discount methods در `app/db.py`.<br>
**تست:** `test_extended_discount_create_and_safe_delete`، `test_discounts_are_single_and_released_on_expiry`.

### UC-27 — مدیریت FAQ و تیکت

**هدف:** نگهداری دانش عمومی و حل پرونده‌های مشتری.

**بازیگران:** Ticket: ACT-S/A/O؛ FAQ: ACT-A/O<br>
**محرک:** `/tickets`, `/ticket`, `/ticket_attachment`, `/ticket_reply`, `/ticket_status`, `/ticket_close`, `/faq*`.

**پیش‌شرط‌ها:** مدیر فعال؛ entity موجود.

**پس‌شرط موفق:** پاسخ/وضعیت ticket ثبت و کاربر مطلع؛ FAQ/category با sort/status درست به‌روز.

**جریان اصلی:** پشتیبان `/tickets [open|answered|closed|all] [PAGE]` را با ترتیب `updated_at DESC`، page size ۲۰، total و راهنمای قبلی/بعدی تا همه پرونده‌ها پیمایش می‌کند؛ photo/document ذخیره‌شده را با `/ticket_attachment MESSAGE_ID` بازمی‌فرستد، پاسخ می‌دهد، مسئول می‌شود و status را تغییر می‌دهد؛ admin/owner دسته FAQ و سؤال/جواب را CRUD/toggle می‌کند. mutation پاسخ/status/close همراه notice پایدار کاربر commit می‌شود.

**جریان‌های جایگزین/خطا:** status یا صفحه نامعتبر رد و هیچ تیکتی به‌علت سقف پیام silently حذف نمی‌شود؛ حذف FAQ category دارای سؤال—even inactive—رد؛ FAQ می‌تواند بدون دسته شود؛ ticket بسته قابل reopen است؛ reply به entity ناموجود/بسته و attachment message ناموجود/بدون فایل/kind نامعتبر رد؛ نقش و تیکت درست قبل از بازفرستادن فایل دوباره بررسی می‌شوند؛ crash بعد از commit پاسخ/status/close از outbox همان notice را retry می‌کند؛ متن/پیوست محرمانه فروشگاه نباید در تیکت منتشر شود.

**قواعد:** BR-SUP-01..03، BR-OPS-06.<br>
**پیاده‌سازی:** FAQ/ticket handlers در `app/admin.py`; repository در `app/db.py`.<br>
**تست:** `test_order_and_ticket_indexes_page_every_record_without_clamping`، `test_faq_category_and_question_crud`، `test_faq_ticket_and_outbound_message_queues`، `test_ticket_attachments_are_retrievable_after_restart_by_support`، `test_ticket_reply_and_status_commit_with_exact_notice`.

### UC-28 — broadcast و پیام مستقیم

**هدف:** ارسال کنترل‌شده پیام به یک کاربر یا audience مشخص.

**بازیگران:** message: ACT-S/A/O؛ broadcast: ACT-A/O<br>
**محرک:** `/message`, `/broadcast_all`, `/broadcast_joined`, `/broadcast_product`.

**پیش‌شرط‌ها:** actor مجاز؛ متن معتبر؛ audience/date/product معتبر.

**پس‌شرط موفق:** direct message ارسال/queue یا preview سپس batch/outbox یکتا ساخته می‌شود؛ نتیجه batch یک‌بار گزارش می‌شود.

**جریان اصلی broadcast:**

1. audience از همه کاربران، joined window یا خریداران تجاری product (`order_origin=customer` و subtotal مثبت) ساخته می‌شود.
2. تعداد هدف و preview به actor نشان داده می‌شود.
3. actor همان callback تأیید را می‌زند.
4. BroadcastBatch و پیام‌های recipient-specific idempotently queue می‌شوند.
5. worker ارسال، retry و summary موفق/ناموفق را انجام می‌دهد.

**جریان‌های جایگزین/خطا:** preview متعلق به مدیر/توکن دیگر پذیرفته نمی‌شود؛ double-click batch دوم نمی‌سازد؛ audience صفر نتیجه واقعی صفر را فقط یک‌بار گزارش می‌کند؛ HTML فقط با `html:` و markup امن؛ پیام sent دوباره queue نمی‌شود.

**قواعد:** BR-MSG-01..03، BR-RPT-01/02، BR-OPS-03/04.<br>
**پیاده‌سازی:** broadcast handlers در `app/admin.py`; batch/outbox methods در `app/db.py`; delivery در `app/bot.py`.<br>
**تست:** `test_broadcast_previews_count_and_requires_matching_callback`، `test_broadcast_enqueue_is_idempotent_for_preview_token`، `test_zero_target_broadcast_reports_actual_result_once`، `test_retry_cannot_resurrect_a_sent_outbound_message`.

### UC-29 — گزارش مدیریتی

**هدف:** تولید summary و CSV با دامنه شفاف برای سفارش، کاربر و مالی.

**بازیگران:** ACT-A/ACT-O<br>
**محرک:** `/report orders|users|finance ...`.

**پیش‌شرط‌ها:** تاریخ `YYYY-MM-DD` معتبر؛ status/product در صورت استفاده معتبر.

**پس‌شرط موفق:** summary متنی و در صورت وجود ردیف، CSV UTF-8 با فیلتر درخواستی ارسال می‌شود؛ شاخص‌های ردیف‌محور summary با CSV منطبق‌اند و دامنه شاخص‌های عمومی مطابق BR-RPT-03 تعریف شده است.

**جریان اصلی:** بازه به timezone تبدیل و انتهای روز inclusive می‌شود؛ orders بر created_at و status و شامل تاریخچه داخلی است؛ users joined بر joined_at؛ product buyers/finance بر paid_at و فقط `order_origin=customer` با subtotal مثبت؛ برای orders/finance شمار سفارش، تکمیل‌شده و درآمد ناخالص و برای users شمار کاربر از همان rows محاسبه می‌شود. ردیف داخلی در شمار/تکمیل گزارش orders می‌آید اما چون صفرمبلغ است درآمد نمی‌سازد.

**جریان‌های جایگزین/خطا:** بازه وارونه/بسیار نامعتبر رد؛ بدون row فقط summary؛ در گزارش users شاخص‌های سفارش summary از همه سفارش‌های ساخته‌شده در بازه می‌آیند، در گزارش orders/finance شمار کاربران عمومی بر `joined_at` بازه است و `open_ticket_count` در همه گزارش‌ها شاخص سراسری لحظه‌ای باقی می‌ماند؛ cell با `=,+,-,@` پس از whitespace برای جلوگیری از spreadsheet formula injection خنثی می‌شود.

**قواعد:** BR-RPT-01..04، سیاست مالی مرجع.<br>
**پیاده‌سازی:** `_report`, `_report_rows`, `_csv_bytes` در `app/admin.py`; `summary_report` در `app/db.py`.<br>
**تست:** `test_report_sends_human_summary_and_utf8_csv`، `test_finance_summary_uses_the_csv_paid_at_window`، `test_order_report_summary_respects_the_status_filter`، `test_csv_neutralizes_formula_prefixes_after_optional_whitespace`.

### UC-30 — مدیریت قوانین پاداش

**هدف:** تعریف incentive قابل پیش‌بینی و بدون پرداخت تکراری.

**بازیگران:** ACT-A/ACT-O<br>
**محرک:** `/rewards`, `/reward_add`, `/reward_toggle`.

**پیش‌شرط‌ها:** amount مثبت؛ event معتبر؛ product/window/conditions معتبر.

**پس‌شرط موفق:** RewardRule یکتا ساخته یا status آن toggle می‌شود؛ فقط رویدادهای آینده/درون window را مطابق event time پوشش می‌دهد.

**جریان اصلی:** انتخاب `start|first_purchase|product_purchase|combined`؛ تعیین amount/product؛ در combined ثبت JSON با شروط مجاز؛ تعیین start/end inclusive؛ نمایش و toggle.

**جریان‌های جایگزین/خطا:** start با product رد؛ combined بدون شرط مؤثر/کلید ناشناخته/type غلط رد؛ `first_purchase:true` با minimum purchases>1 conflict؛ product_ids ناموجود یا intersection تهی رد؛ idempotency key rule با terms متفاوت conflict؛ rule جدید retroactive نیست.

**قواعد:** BR-REF-02..07.<br>
**پیاده‌سازی:** reward handlers در `app/admin.py`; reward methods در `app/db.py`.<br>
**تست:** `test_reward_rule_accepts_optional_inclusive_date_window`، `test_combined_reward_rejects_disjoint_product_filters`، `test_reward_window_boundaries_and_start_product_guard`، adversarial reward tests.

### UC-31 — تهیه بکاپ کامل

**هدف:** ایجاد snapshot سازگار دیتابیس برای disaster recovery.

**بازیگر اصلی:** ACT-O<br>
**محرک:** `/backup`.

**پیش‌شرط‌ها:** actor owner فعال؛ private chat؛ فضای کافی.

**پس‌شرط موفق:** SQLite online backup در مسیر data/backups ایجاد؛ metadata، SHA-256، اندازه و status completed ثبت؛ فایل فقط به private chat owner فرستاده می‌شود.

**جریان‌های جایگزین/خطا:** admin/support forbidden؛ شکست filesystem/database با status failed و error کنترل‌شده ثبت؛ فایل ناقص موفق اعلام نمی‌شود؛ restore خودکار این یوزکیس نیست و باید در محیط جدا طبق deployment runbook آزموده شود.

**قواعد:** BR-OPS-05/06/08.<br>
**پیاده‌سازی:** `_backup`, `_create_backup` در `app/admin.py`; `Database.create_backup`.<br>
**تست:** `test_backup_uses_online_sqlite_backup_and_sends_file`، `test_reminders_backup_reports_and_foreign_keys`، migration tests بکاپ legacy.

## ۶. یوزکیس‌های سیستمی

### UC-40 — دریافت و پردازش update با long polling

**هدف:** دریافت ترتیبی updateهای Telegram بدون webhook و ادامه از offset پایدار.

**بازیگران:** ACT-T؛ سامانه<br>
**محرک:** اجرای `python -m app.main`.

**پیش‌شرط‌ها:** config معتبر، token در secret store، DB قابل نوشتن، دسترسی HTTPS؛ هیچ instance دیگری فعال نیست.

**پس‌شرط موفق:** webhook قدیمی با `drop_pending_updates=False` حذف؛ commandها ثبت؛ فقط message/callback poll؛ update پردازش و offset بعدی ذخیره می‌شود؛ mutation مدیریتی موفق journal `completed` دارد.

**جریان اصلی:** در `--check`، initialize/migration تا schema 11، ایجاد/اعتبارسنجی bootstrap root پایدار و سپس `getMe`؛ در اجرای واقعی، همین bootstrap سپس `getMe`; `deleteWebhook`; setMyCommands؛ شروع callback server/worker اختیاری؛ loop `getUpdates(offset, timeout)`؛ `process_update_safe`. برای mutation مدیریتی: `begin_admin_update` با fingerprint، skip ردیف `completed` یا resume ردیف `started`، freeze مقصد toggle با `get_or_store_admin_update_effect`، اجرای API idempotent دامنه، و `complete_admin_update` پس از بازگشت عادی. فقط پس از ACK handler، offset همان update ذخیره و پردازش batch ادامه می‌یابد.

**جریان‌های جایگزین/خطا:** `--check` در identity conflict پیش از Telegram fail closed است، read-only نیست و owner غیرفعال را re-enable نمی‌کند. خطای موقت پایهٔ `DatabaseError`/SQLite—شامل begin، mutation یا complete journal—از `process_update_safe` مقدار `False` می‌دهد؛ poller offset را ذخیره نمی‌کند، updateهای بعدی همان batch را اجرا نمی‌کند و همان offset را با backoff نمایی سقف‌دار/stop-aware دوباره می‌گیرد. در replay، `started` همان payload و اثر freezeشده/idempotent را resume و `completed` را skip می‌کند؛ update ID یکسان با fingerprint متفاوت conflict است. diagnostic موقت فقط بار اول تلاش و failure خودش نادیده گرفته می‌شود تا NACK حفظ شود. خطای terminal از subclassهای دامنه و خطای پاسخ Telegram ACK است تا poison update صف را نبندد؛ هر بازگشت دیگر، از جمله `None`، ACK محسوب می‌شود. این journal/ACK ارسال Telegram را exactly-once نمی‌کند. token در خطای transport redacted است؛ SIGINT/SIGTERM پس از request جاری retry/backoff را لغو می‌کند، update dispatch‌نشده را بدون advance offset برای restart می‌گذارد، worker non-daemon را join می‌کند و callback listener/requestهای non-daemon در حال اجرا را تا سقف مهلت shutdown drain می‌کند؛ دو instance باعث conflict عملیاتی و ممنوع است.

**قواعد:** BR-OPS-01..05/07.<br>
**پیاده‌سازی:** `app/main.py`, `BotApplication.initialize/run/process_update_safe`, `app/telegram.py`, `app/jobs.py`.<br>
**تست:** `test_worker_runs_and_stops`، `test_worker_is_non_daemon_and_stop_joins_the_active_cycle`، `test_stop_waits_for_an_inflight_confirmation_callback`، `test_polling_does_not_retry_or_back_off_after_shutdown`، `test_update_returned_during_shutdown_is_left_for_restart`، `test_handler_nack_preserves_offset_and_retries_before_later_batch_update`، `test_backoff_saturates_for_unbounded_handler_retries`، `test_polling_retries_transient_admin_update_before_later_batch_items`، `test_admin_journal_begin_and_complete_database_failures_are_nacked`، `test_admin_transient_nack_survives_failed_diagnostic_notification`، `test_permanent_admin_reply_failure_is_acknowledged`، `test_admin_update_journal_replays_before_effect_and_same_object_safely`، `test_admin_update_journal_freezes_toggle_and_idempotent_create`، `test_admin_update_journal_queues_direct_message_and_replays_delete`، `test_admin_inventory_state_transient_failure_remains_replayable`، `test_transport_errors_never_expose_the_bot_token`، `test_every_documented_command_is_registered`.

### UC-41 — نگهداری دوره‌ای و reconciliation

**هدف:** نهایی‌کردن کارهای durable که به علت زمان، شبکه یا crash عقب افتاده‌اند.

**بازیگر اصلی:** ACT-W<br>
**محرک:** هر `JOB_INTERVAL_SECONDS`، پیش‌فرض ۱۰ ثانیه.

**پیش‌شرط‌ها:** service فعال و DB reachable.

**پس‌شرط موفق:** هر queue/رکورد واجد شرایط به status بعدی می‌رود یا با retry state امن باقی می‌ماند؛ exception یک job دور بعد را متوقف نمی‌کند.

**جریان اصلی به‌ترتیب:**

1. settlement شاهدهای completed provider که پیش از crash ثبت ولی اعمال نشده‌اند؛
2. poll paymentهای Plisio باز/نیازمند بررسی late transition و ثبت durable evidence؛
3. queue/reconcile هشدارهای provider/card review، اعلان نتیجه تصمیم دستی reviewها، فیش‌های `verifying`، اطلاعات سفارش manual، no-stock سفارش ready، تمام reward-eventهای فاقد notice، پیام‌های کاربر در تیکت و رخداد امنیتی کارت؛
4. انقضای سفارش‌های unpaid فاقد crypto فعال و ثبت اتمیک اعلان، یا بازیابی Order terminal فاقد outbox پس از crash؛
5. انقضای paymentهای card بدون فیش و خارج مهلت اولیه، reconciliation والد و ثبت/بازیابی اعلان terminal؛ crypto با deadline محلی sweep نمی‌شود؛
6. بازیابی اعلان paymentهای بیرونی paid و اعلان موفقیت wallet-only/تخفیف کامل/خرید رایگان تأییدشده کاربر بدون outbox متناظر، پیش از هر fulfillment؛
7. بازیابی reward سفارش‌های موفق با marker خالی و سپس selector مستقل fulfillment همه سفارش‌های status=`paid` حتی با marker پاداش پر؛ هر شاخه fulfillment dependency اعلان موفقیت را دوباره کنترل می‌کند؛
8. بازیابی prompt سفارش‌های `awaiting_stock` و `awaiting_info`؛
9. تخصیص موجودی به reservationهای FIFO؛
10. fulfil سفارش‌های ready در `processing` پس از restock، فقط برای product بدون reservation معتبر قدیمی‌تر؛
11. بازیابی delivery سفارش completed؛
12. claim reminderهای موعدرسیده، cancel بدون ارسال برای اشتراک پایان‌یافته و نمایش زمان مطلق پایان برای مورد دیررسِ هنوز معتبر؛
13. claim/send/retry outbox؛
14. summary broadcastهای تمام‌شده و mark نتیجه تلاش اعلان؛ failure دائمی summary نباید batchهای بعدی را starve کند.

**جریان‌های جایگزین/خطا:** exception log و دور بعد؛ provider outage payment را failed نمی‌کند؛ completed evidence durable پس از restart بدون network settle می‌شود؛ partial/unknown/mismatch فقط review می‌سازد؛ claim از رقابت تکراری جلوگیری می‌کند؛ blockedشدن recipient یک reminder نباید اعضای بعدی batch را متوقف کند؛ reminder صفر آغاز روز محلی پایان است؛ اشتراک با subscription_ends_at <= now در ارسال نخست و retry بدون پیام cancelled می‌شود؛ متن روزهای مثبت زمان مطلق پایان را نشان می‌دهد و صفر «امروز» همراه ساعت پایان دارد؛ completion marker پاداش نمی‌تواند سفارش `paid` را از selector fulfillment حذف کند؛ reward-event/no-stock/payment/order noticeهای commit‌شده با query missing و cursor/wrap بازیابی می‌شوند؛ success notice `queued/sending` همه شاخه‌های fulfillment را defer و `sent` یا terminal `failed/cancelled` gate را باز می‌کند؛ query ردیف‌های فاقد notice/delivery مانع پنهان‌شدن ردیف قدیمی پشت newest-N می‌شود؛ cap رزرو اجازه انتخاب processing-ready همان product پیش از reservation قدیمی‌تر را نمی‌دهد؛ delivery committed ولی پیام شکست‌خورده دوباره queue می‌شود نه دوباره‌فروشی item؛ انقضای commit‌شده فاقد notice در restart از outbox key پایدار بازیابی می‌شود؛ failure دائمی summary broadcast به‌عنوان تلاش نهایی ثبت و batch بعدی قابل پیشرفت می‌ماند؛ entity terminal احیا نمی‌شود؛ shutdown در مرز آیتم بعدی loop را متوقف می‌کند و cursor فقط تا آخرین آیتم پردازش‌شده جلو می‌رود.

**قواعد:** BR-OPS-03/04، BR-REF-07، BR-FUL-01/03/08، BR-MSG-02.<br>
**پیاده‌سازی:** `BotApplication.run_maintenance`، `_reconcile_paid_fulfillment`, `_reconcile_reward_notices`, `_reconcile_ready_stock_alerts` و helperها؛ claim/retry methods در `app/db.py`; `PeriodicWorker`.<br>
**تست:** `test_crash_after_inventory_assignment_is_reconciled_to_one_delivery`، `test_non_reserved_ready_stock_race_completes_after_restock`، `test_reward_marker_crash_does_not_strand_paid_fulfillment`، `test_reward_notice_recovery_rotates_past_start_reward_crashes`، `test_no_stock_transition_alerts_recover_for_user_and_staff`، `test_wallet_and_full_discount_success_notices_recover_without_starvation`، `test_transient_success_notice_failure_defers_every_fulfillment_branch`، `test_terminal_success_notice_failure_does_not_strand_paid_delivery`، `test_reward_notice_queue_failure_remains_reconcilable`، `test_reward_reconciliation_survives_partial_grant_after_completion`، `test_expired_subscription_does_not_receive_a_stale_reminder`، `test_maintenance_stops_before_the_next_stage`، `test_outbound_loop_stops_between_items_without_stranding_claims`، `test_missing_order_notices_rotate_and_expiry_recovers_after_restart`.

### UC-42 — ارسال پایدار، retry و reminder

**هدف:** کاهش گم‌شدن اعلان‌های تجاری مهم در خطای موقت Telegram.

**بازیگر اصلی:** ACT-W؛ **همکار:** ACT-T<br>
**محرک:** queueشدن OutboundMessage یا رسیدن `remind_at`.

**پیش‌شرط‌ها:** recipient معتبر؛ scheduled time رسیده؛ status قابل claim.

**پس‌شرط موفق:** پیام sent با telegram message ID/time یا failed/retryable با attempt/error کنترل‌شده؛ reminder sent/failed می‌شود یا، پس از ساخت outbox، در اختیار retry همان outbox می‌ماند.

**جریان اصلی:** `reminder_days` نامنفی است؛ صفر آغاز روز پایان در timezone تنظیم‌شده و در صورت schedule همان روز پیش از پایان، فوراً موعد دارد. انقضای دقیق نیمه‌شب یا اشتراک پایان‌یافته reminder روز صفر نمی‌سازد. worker reminderها را با batch محدود claim و مستقل پردازش می‌کند؛ پیام durable با `reminder:{id}`، زمان مطلق پایان و timezone ساخته می‌شود. روز صفر «امروز» همراه ساعت پایان است. outbox queued/sending مالک retry است و reminder تا stale reconciliation در processing می‌ماند؛ فقط نبود outbox آن را فوراً آزاد می‌کند. `_deliver_outbound_messages` پیش از retry دوباره پایان واقعی و مالکیت را کنترل می‌کند و reminder منقضی را همراه outbox بدون ارسال cancelled می‌کند. outboxها یک‌به‌یک claim/send می‌شوند تا shutdown پیش از claim بعدی متوقف شود؛ broadcast summary پس از پایان همه فرزندان ساخته می‌شود و نتیجه terminal تلاش آن، batch را از صف summaryهای آینده آزاد می‌کند.

**جریان‌های جایگزین/خطا:** خطای transient backoff/requeue؛ خطای terminal/blocked برای یک recipient failed می‌شود و reminder بعدی همان batch ادامه می‌یابد؛ تکرار idempotency key فقط همان recipient/body/markup را می‌پذیرد؛ پیام sent هیچ‌وقت به queued برنمی‌گردد؛ ارسال شبکه‌ای exactly-once مطلق نیست، اما اثر دیتابیس و retry تا حد ممکن idempotent است.

**قواعد:** BR-MSG-01..03، BR-FUL-08، BR-OPS-03/04.<br>
**پیاده‌سازی:** outbox/reminder methods در `app/db.py`; `_notify_user_durable`, `_deliver_outbound_messages`, `_deliver_due_reminders` در `app/bot.py`.<br>
**تست:** `test_faq_ticket_and_outbound_message_queues`، `test_retry_cannot_resurrect_a_sent_outbound_message`، `test_outbound_idempotency_rejects_cross_recipient_collision`، `test_reminders_backup_reports_and_foreign_keys`، `test_permanent_reminder_failure_does_not_starve_the_next_reminder`.

## ۷. ماتریس دسترسی یوزکیس‌های مدیریتی

فهرست‌های مدیریتی تکمیلی همگی ۲۰ ردیف در صفحه، total دقیق و فرمان قبلی/بعدی دارند:

| فرمان | دامنه/صفحه |
|---|---|
| `/admins [PAGE]` | تمام مدیران و وضعیت هویت/دسترسی |
| `/categories [PAGE]` | تمام دسته‌ها |
| `/products [CATEGORY_ID\|all] [PAGE]` | همه محصولات یا یک دسته |
| `/inventory_list PRODUCT_ID [PAGE]` | اقلام انبار همان محصول |
| `/discounts [PAGE]` | تمام تخفیف‌ها |
| `/faq_categories [PAGE]` | دسته‌های FAQ |
| `/faqs [CATEGORY_ID\|all] [PAGE]` | همه سوال‌ها یا یک دسته |
| `/rewards [PAGE]` | تمام قواعد پاداش |

نمایش `/order` و جست‌وجوی دقیق `/user_orders` تمام متن اطلاعات مشتری برای محصول دستی را حفظ می‌کند؛ `/ticket` نیز ادامه هیچ پیام مکالمه‌ای را حذف نمی‌کند. نوع فارسی تراکنش در `/user`، `/user_transactions` و نمای کاربر مستقل از دلیل آزاد دیده می‌شود. تست‌ها: `test_all_management_lists_expose_records_beyond_previous_caps`، `test_manual_order_details_preserve_complete_customer_text`، `test_ticket_detail_preserves_the_end_of_long_escaped_messages`، `test_admin_transaction_views_show_type_and_reason_separately`.

| قابلیت | support | admin | owner |
|---|:---:|:---:|:---:|
| مشاهده کاربر/سفارش | بله | بله | بله |
| پیام مستقیم | بله | بله | بله |
| مشاهده/پاسخ/وضعیت تیکت | بله | بله | بله |
| درخواست اصلاح اطلاعات سفارش | بله | بله | بله |
| مشاهده پیوست تیکت | بله | بله | بله |
| بازفرستادن فیش یا پیوست سفارش manual | خیر | بله | بله |
| تغییر status یا complete سفارش | خیر | بله | بله |
| تأیید/رد payment | خیر | بله | بله |
| block/unblock و wallet adjustment | خیر | بله | بله |
| کاتالوگ، انبار، تخفیف، FAQ | خیر | بله | بله |
| payment/settings/force-join | خیر | بله | بله |
| broadcast/report/reward rules | خیر | بله | بله |
| افزودن/تغییر admin یا support | خیر | بله | بله |
| افزودن/تغییر owner | خیر | خیر | بله |
| بکاپ کامل | خیر | خیر | بله |

نکته: این جدول باید با `SUPPORT_COMMANDS` و guardهای اختصاصی owner در `app/admin.py` همگام بماند.

## ۸. ماتریس حالت و اقدام کاربر

| entity/status | اقدام مجاز مشتری | اقدام عملیاتی معمول |
|---|---|---|
| Order `pending_payment` | تخفیف، انتخاب روش؛ لغو فقط card pending بدون فیش | انقضا فقط بدون crypto فعال یا مشاهده |
| Order `awaiting_confirmation` | مشاهده، در صورت مجاز فیش | approve/reject |
| Order `paid` | مشاهده | reward + fulfillment فوری |
| Order `awaiting_stock` | مشاهده | تأمین و FIFO assignment |
| Order `awaiting_info` | ارسال اطلاعات | request info/پیگیری |
| Order `processing` | ارسال اطلاعات اصلاحی اگر prompt داده شده، مشاهده | complete یا request info |
| Order `completed` | مشاهده جزئیات/تحویل | terminal؛ refund workflow در نسخه فعلی وجود ندارد |
| Order `expired/cancelled/rejected/refunded` | مشاهده؛ شروع خرید تازه | بدون احیای مستقیم |
| Payment card `pending` بدون فیش | پرداخت، پس از delay فیش، لغو | callback/expiry |
| Payment crypto `pending` | پرداخت/مشاهده invoice؛ بدون لغو محلی | polling تا شاهد terminal provider؛ نه expiry محلی |
| Payment card `verifying` دارای فیش | مشاهده/فیش تکمیلی تا تصمیم نهایی | تأیید/رد صریح؛ بدون انقضای خودکار |
| Payment crypto `verifying` | مشاهده؛ بدون لغو/فیش | ادامه poll، auto-resolution با شاهد بعدی یا review مالک |
| Payment `paid` | مشاهده | terminal؛ refund workflow در نسخه فعلی وجود ندارد |
| Payment terminal دیگر | مشاهده | بدون احیا |
| Ticket `open/answered` | مشاهده، پاسخ، پیوست | reply/status/close |
| Ticket `closed` | مشاهده | reopen توسط مدیر |

## ۹. سناریوهای پذیرش end-to-end پیشنهادی

این سناریوها مکمل unit test هستند و برای staging با Bot/DB آزمایشی اجرا شوند:

1. **کاربر جدید با جوین:** `/start` با لینک دعوت، عدم عضویت، پیوستن، check، منو و یک پاداش start.
2. **ready با کیف پول کامل:** شارژ آزمایشی، خرید، یک debit/capture، یک item assigned، یک delivery، replay callback بدون تغییر.
3. **ready با کیف پول جزئی و کارت:** hold بخشی، intent مبلغ remainder، callback معتبر، completed و مانده درست.
4. **انتظار بررسی فیش:** payment کارت، انتظار حداقل delay، upload فیش پیش از deadline، عبور بیش از ۷ روز، باقی‌ماندن verifying، replacement معتبر و تأیید/رد صریح مدیر؛ intent بدون فیش همچنان منقضی شود.
5. **انقضا:** order/intent کارت بدون فیش و بدون crypto فعال، اجرای maintenance، expired، آزادسازی hold و عدم پذیرش callback دیرهنگام.
6. **تعویض روش/تأخیر بانک:** card فعال مانع crypto؛ لغو card سفارش را terminal؛ ساخت Order تازه با crypto؛ invoice صادرشده بدون cancel؛ transfer دیررس مبلغ کارت در quarantine به payment تازه متصل نشود.
7. **رزرو:** دو سفارش paid بدون موجودی، افزودن یک item، تحویل فقط به قدیمی‌ترین، افزودن دوم و تحویل بعدی.
8. **manual:** پرداخت، customer info با فایل، request correction، اطلاعات تازه، complete، subscription end از زمان completion.
9. **تخفیف کامل:** کد ۱۰۰٪ روی order باز، نمایش دوباره خلاصه با همان دکمه‌ها و بدون تحویل؛ دکمه پرداخت، تأیید صفرمبلغ، اعلان موفقیت و fulfillment؛ replay بدون اثر دوم. محصول رایگان نیز تا همین تأیید منتظر بماند.
10. **پاداش خرید و crash:** rule first/product/combined، توقف مصنوعی پس از یک grant، restart، تکمیل بقیه بدون credit/notice تکراری.
11. **مجوزها:** support مشاهده و پاسخ تیکت؛ تلاش wallet adjustment، approve و complete همگی رد.
12. **مالکیت:** callback سفارش، تیکت و attachment کاربر B با حساب A همگی fail closed.
13. **reminder روز پایان:** صفر در ورودی پذیرفته و آغاز روز پایان در timezone تنظیم‌شده schedule شود؛ شروع schedule در همان روز پیش از پایان فوراً موعد دارد؛ انقضای نیمه‌شب و اشتراک پایان‌یافته پیام تازه نمی‌گیرند. پیام روزهای مثبت تاریخ مطلق دارد و retry بعد از پایان لغو می‌شود؛ blocked recipient بعدی را starve نکند.
14. **broadcast:** audience کوچک، preview، double confirm، یک batch و یک پیام برای هر target، summary یک‌بار؛ سپس بیش از cap batch قدیمی با summaryهای 403 بسازید و اثبات کنید batch بعدی starve نمی‌شود.
15. **provider review/recovery:** partial به verifying و بدون credit؛ completed بعدی یک settlement؛ crash پس از ثبت completed evidence و پیش از settlement با restart و بدون network بازیابی؛ terminal-zero بدون احیای Order.
16. **late provider after resolution:** dismiss/refund review، failedشدن payment، سپس completed تازه؛ review جدید، عدم احیای Order و فقط credit جبرانی evidence-based با تصمیم owner.
17. **topup conflict و resume:** یک topup card یا crypto فعال بسازید؛ replay همان method/amount/terms همان intent باشد و روش یا مبلغ دیگر conflict کند. fixture legacy با هر دو روش فعال باید هر دو را جدا در wallet نشان دهد، receipt فقط card و URL resume فقط crypto باشد، بدون ساخت intent سوم یا replace/cancel ضمنی.
18. **backup/restore:** owner backup، verify hash/integrity در محیط جدا، اجرای migrations و suite روی restore.
19. **پیکربندی fail-closed پرداخت:** نصب تازه بدون کارت/API key؛ فقط wallet دیده شود، `/payment card on` و `/payment crypto on` ناقص رد شوند؛ پس از تنظیم معتبر فقط روش خواسته‌شده ظاهر شود.
20. **تحویل بلند:** payload آماده، instruction مؤثر و `/complete` دستی که خروجی نهایی را از ۳۹۰۰ عبور می‌دهند، قبل از inventory/order/outbox mutation رد شوند؛ نسخه کوتاه همان جریان فقط یک تحویل بسازد.
21. **پیوست بازیابی‌پذیر:** فیش و customer info فایل‌دار commit، crash فرضی و reconciliation؛ owner/admin فایل فعلی را بازفرستند، support رد شود، همان hash alert دوم نسازد و replacement hash تازه یک alert جدید بسازد. جداگانه photo/document تیکت را پس از restart با `/ticket_attachment MESSAGE_ID` برای support بازفرستید و actor بی‌نقش/kind نامعتبر را رد کنید.
22. **resume پرداخت ارزی:** از invoice crypto با Back به Order و wallet برگردید؛ همان URL امن «ادامه پرداخت ارزی» باشد و receipt/cancel نمایش داده نشود. URL legacy ناامن یا خالی فقط راه پشتیبانی بدهد.
23. **notice پس از crash و starvation:** انقضا و ticket reply/status/close را در مرز commit/send قطع و پس از restart دقیقاً یک outbox معتبر بازیابی کنید؛ بیش از cap سفارش pending/completed قدیمی با noticeهای موجود بسازید و اثبات کنید ردیف missing قدیمی در دورهای بعد پیدا می‌شود.
24. **هویت و rotation مدیر:** grant ناشناخته را pending بسازید؛ mismatch username/chat مجوز نگیرد، زوج دقیق verify شود، rename همان chat access را حفظ و username روی chat دیگر رد شود. root را disable/restart کنید و بازفعال‌نشدن را ببینید؛ marker را فقط به owner فعال/verifyشده منتقل کنید.
25. **replay فرمان مدیر:** process را بعد از journal `started` و قبل/بعد mutation toggle/create/message قطع کنید؛ replay همان update باید همان effect را ادامه دهد، رکورد `completed` skip و همان update ID با payload متفاوت conflict شود.
26. **خلاصه ساخت Order:** ارسال خلاصه را fail و همان update first-contact را replay کنید؛ فقط یک Order و یک `created-summary` باقی بماند. failure پیش از transaction باید state شماره/contact را حفظ کند.
27. **تقدم اعلان موفقیت:** failure transient پیام canonical را برای ready/reserve/manual تزریق کنید و نبود هر mutation fulfillment را ثابت کنید؛ پس از `sent` ادامه انجام شود. سپس failure دائمی terminal بسازید و اثبات کنید خطا در outbox دیده می‌شود ولی paid Order برای همیشه strand نمی‌ماند.
28. **پیمایش مدیریتی کامل:** بیش از ۲۰ user/order/ticket و بیش از ۲۰ transaction/referral/reward/order برای یک user بسازید؛ همه صفحه‌ها را از راهنمای بعدی/قبلی پیمایش و total، ترتیب، نبود حذف/تکرار و ownership جست‌وجوی سفارش را بررسی کنید. تاریخ اولین/آخرین خرید باید از کل داده و نه preview محدود بیاید.
29. **NACK در batch:** update مدیریتی اول را یک‌بار با خطای موقت DB و update دوم را در همان پاسخ `getUpdates` قرار دهید؛ offset و journal قبل از retry ثابت بماند، update دوم زودتر اجرا نشود، diagnostic فقط بار اول باشد و پس از retry هر اثر یک‌بار و offsetها به‌ترتیب commit شوند.

## ۱۰. Traceability سریع یوزکیس به تست

| یوزکیس | فایل تست اصلی |
|---|---|
| UC-01..04 | `tests/test_bot.py`, `tests/test_user_flow_adversarial.py`, `tests/test_keyboards.py` |
| UC-05..10 | `tests/test_db.py`, `tests/test_bot.py`, `tests/test_payment_server.py`, `tests/test_plisio.py` |
| UC-11..13 | `tests/test_db.py`, `tests/test_bot.py`, `tests/test_admin.py` |
| UC-14..17 | `tests/test_user_history_pagination.py`, `tests/test_user_flow_adversarial.py`, `tests/test_db.py` |
| UC-20..31 | `tests/test_admin.py`, `tests/test_db.py` |
| UC-40..42 | `tests/test_jobs.py`, `tests/test_telegram.py`, `tests/test_db_adversarial_regressions.py`, `tests/test_bot.py` |

نام تست‌های دقیق کنار هر یوزکیس آمده است. هر تغییر در جریان اصلی باید حداقل یک happy-path test داشته باشد؛ هر تغییر مالی/مالکیت علاوه بر آن به تست error، replay/idempotency و در صورت چندمرحله‌ای‌بودن به تست crash/recovery نیاز دارد.

## ۱۱. الگوی افزودن یوزکیس جدید

برای توسعه بعدی، این قالب را کپی کنید:

```markdown
### UC-XX — عنوان

**هدف:** ...

**بازیگر اصلی:** ...<br>
**محرک:** ...

**پیش‌شرط‌ها:** ...

**پس‌شرط موفق:** ...<br>
**پس‌شرط شکست:** هیچ اثر ناقص مالی/تحویلی باقی نمی‌ماند؛ ...

**جریان اصلی:**

1. ...

**جریان‌های جایگزین/خطا:** ...

**قواعد:** BR-...<br>
**پیاده‌سازی:** `app/...`<br>
**تست:** `tests/...`
```

پیش از پیاده‌سازی، برای یوزکیس تازه پاسخ این پرسش‌ها لازم است: مالک entity کیست؟ چه roleای مجاز است؟ transaction boundary کجاست؟ idempotency key از چه هویتی ساخته می‌شود؟ اثر retry/restart چیست؟ اطلاعات حساس کدام است؟ status terminal چیست؟ migration و rollback چگونه انجام می‌شود؟
