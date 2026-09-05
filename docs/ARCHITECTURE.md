# معماری فنی ربات الون اکانت

لایهٔ جدید `customer_layouts` بین markup canonical و ارسال قرار می‌گیرد: `LayoutTelegram` همان transport/poller را delegate می‌کند و فقط کپی reply markup را reflow و tagهای داخلی را حذف می‌کند. ترتیب کاتالوگ عمومی پیش از pagination اعمال می‌شود. `admin_layouts` ویرایشگر draft/confirm/executing با نسخهٔ مورد/والد است؛ ذخیره فقط از `Database.save_customer_layout` و journal موجود. outbox، callback تجاری و authorization کاربر تغییر نمی‌کنند. [قرارداد توسعه و بازیابی](CUSTOMER_LAYOUTS.md).

رابط مدیریت پنج جزء دارد: `admin_forms.py` کاتالوگ عملیات و ۹ بخش اصلی، `admin_catalog.py` مرور درخت دسته/محصول/انبار، `admin_joins.py` مرور کانال‌های اجباری، `admin_ui.py` FSM پایدار دکمه‌ای و `admin.py` handler مشترک با مسیر سازگار فرمان. مرور در `admin:catalog` فقط شناسه و زمینه را ذخیره می‌کند؛ فرم در `admin:ui` token/revision و هویت input را نگه می‌دارد، role را زنده revalidate می‌کند و اجرای تأییدشده را به journal/دامنه موجود می‌سپارد. تراکنش مالی موازی یا poller جدید ندارد. جزئیات در [BUTTON_UI.md](BUTTON_UI.md)، [ADMIN_HIERARCHY.md](ADMIN_HIERARCHY.md) و نمودارهای ۰۲، ۰۶، ۱۶ و ۱۷ آمده است.

## هدف معماری

جزء پنجم UI، `admin_joins.py`، مرور فقط‌خواندنی کانال‌های اجباری و بازکردن فرم مشترک را انجام می‌دهد؛ state آن `admin:joins` و مقصد بازگشت فرم با scope=joins جدا از کاتالوگ است. کنترل‌های دامنه و نقش تغییری ندارند. [قرارداد](ADMIN_JOINS.md).

چرخهٔ نمایش فرم شامل revision تازه، persistence نگاشت گزینه‌ها، ارسال پیام جایگزین، ثبت `prompt_message_id` و پاک‌کردن best-effort markup قبلی است. شکست پاک‌کردن keyboard هرگز مجوز اجرای دوباره نمی‌سازد. callback قدیمی بدون فراخوانی handler، فرم فعلی یا گزینهٔ refresh خواندنی را نمایش می‌دهد؛ کنترل role قبل از recovery هم الزامی است.

این سامانه یک ربات فروشگاهی فارسی و stateful برای Telegram است که با یک فرایند Python، دریافت `getUpdates`، پایگاه داده SQLite و worker نگه‌داری دوره‌ای اجرا می‌شود. طراحی برای یک instance فعال، استقرار کم‌هزینه، تخصیص منطقی دقیقاً یک‌باره اقلام حساس و بازیابی امن پس از قطع شبکه یا crash بهینه شده است. ارسال شبکه‌ای Telegram با outbox و retry به‌صورت at-least-once انجام می‌شود و API تلگرام تضمین end-to-end دقیقاً یک‌باره ارائه نمی‌کند.

مرجع تصویری معماری در [DIAGRAMS.md](DIAGRAMS.md) و جزئیات جداول در [DATA_MODEL.md](DATA_MODEL.md) قرار دارد.

## مرز سامانه

ورودی‌ها:

- updateهای خصوصی Telegram از نوع `message` و `callback_query`؛
- فرمان‌ها و callbackهای مدیران؛
- callback اختیاری و امضاشده پیامک بانک/MacroDroid؛
- پاسخ API اختیاری Plisio؛
- متغیرهای محیطی و تنظیمات ذخیره‌شده در SQLite.

خروجی‌ها:

- پیام، ویرایش پیام، فایل، تصویر و دکمه در Telegram؛
- تحویل امن محصول آماده یا ارجاع محصول دستی به مدیر؛
- ثبت ledger کیف پول، پرداخت، سفارش، تیکت، پیام خروجی و گزارش؛
- فایل backup و CSV مدیریتی.

خارج از مرز سامانه:

- پنل وب عمومی، درگاه بانکی داخلی مستقیم و سیستم حسابداری رسمی؛
- نگه‌داری token یا secret در Git؛
- اجرای چند replica هم‌زمان با یک token؛
- تضمین SLA سرویس‌های بیرونی Telegram یا Plisio.

## اجزای اصلی

| مسیر | مسئولیت |
|---|---|
| `app/main.py` | CLI، بارگذاری config، lifecycle و signal handling |
| `app/config.py` | خواندن env، مقدارهای پیش‌فرض و redaction تنظیمات |
| `app/telegram.py` | Telegram Bot API، retry/backoff، upload و long polling |
| `app/bot.py` | orchestration جریان کاربر، stateها، خرید، پرداخت، fulfillment و maintenance |
| `app/admin.py` | authorization نقش‌ها، فرمان‌ها، callbackهای پنل، گزارش و مدیریت محتوا |
| `app/db.py` | مرز تراکنش، validation دامنه، idempotency و همه دسترسی‌های SQLite |
| `app/schema.sql` | schema پایه برای دیتابیس تازه |
| `app/keyboards.py` | ساخت reply/inline keyboard، رنگ و custom emoji icon ID |
| `app/texts.py` | قالب متن‌های سمت کاربر |
| `app/payment_server.py` | HTTP callback محلی کارت‌به‌کارت با احراز هویت و validation سخت‌گیرانه |
| `app/plisio.py` | adapter پرداخت ارزی اختیاری |
| `app/jobs.py` | worker دوره‌ای maintenance |
| `app/utils.py` | اعداد فارسی، HTML امن، escape، تاریخ و محدودسازی متن |

قاعده لایه‌بندی: handlerها تصمیم جریان را می‌گیرند، ولی mutation حساس در runtime اصلی باید از متد دامنه‌ای `Database` عبور کند. `admin.py` علاوه بر query/report، چند write fallback قدیمی برای compatibility با DB doubleها و نسخه‌های سبک دارد؛ این fallbackها مسیر اصلی production نیستند و نباید توسعه یابند. منطق مالی یا تغییر وضعیت جدید فقط با API تراکنشی `Database` و تست مستقل اضافه شود.

`show_main_menu` برای منوی اصلی از `inline_main_menu_keyboard` استفاده می‌کند تا دکمه کانال URL مستقیم داشته باشد. متن اصلی یک‌بار با حذف reply keyboard قبلی ارسال و inline markup به همان message ID متصل می‌شود. شکست Telegram در اتصال markup، فقط پیام کوتاه انتخاب با همان کنترل‌ها می‌سازد؛ cancellation در shutdown دوباره ارسال نمی‌کند. منوی reply و routeهای متنی قدیمی برای اعلان‌ها و سازگاری باقی‌اند؛ keyboard درخواست contact همچنان reply است. تمام callbackهای منوی inline از همان guardها و routeهای مشتری عبور می‌کنند.

سیاست رنگ در مرز HTTP و `TelegramClient.call` اعمال می‌شود: `BUTTON_COLOR_MODE=colored` پیش‌فرض و معادل رفتار قبلی است. fallback اختیاری `theme` فقط کلید `style` دکمه‌های reply/inline را از یک کپی payload حذف می‌کند. JSON، multipart، ارسال و edit و markupهای قدیمی outbox همگی همین مسیر را دارند. ورودی، متن، callback/URL، آیکون و رکورد پایدار دست‌نخورده‌اند؛ در نتیجه fingerprint/idempotency موجود تغییر نمی‌کند. Bot API رنگ مستقل نوشتهٔ دکمه ندارد.

## چرخه آغاز و توقف

مسیر preflight یعنی `python -m app.main --check`، config را می‌خواند، schema را تا نسخه ۱۱ initialize/migrate می‌کند، owner bootstrap را روی همان DB ایجاد یا با root marker/هویت پایدار تطبیق می‌دهد و سپس `getMe` را اجرا می‌کند. username پس از verify metadata است؛ تعارض legacy/configured chat ID یا انتقال به مقصد verifyنشده پیش از تماس Telegram fail closed می‌شود و restart owner غیرفعال‌شده را بازفعال نمی‌کند. بنابراین `--check` یک probe صرفاً read-only نیست.

1. `load_settings` فایل env یا محیط را می‌خواند و مسیر data را می‌سازد.
2. `Database.initialize` schema پایه و migrationهای idempotent را اعمال می‌کند.
3. مالک bootstrap و تنظیمات پیش‌فرض ثبت می‌شوند. wallet پیش‌فرض فعال است؛ card فقط با شماره و صاحب حساب کامل قابل نمایش است و crypto علاوه بر setting فعال به API key startup نیاز دارد، بنابراین روش بیرونی ناقص fail closed می‌ماند.
4. `getMe` هویت ربات را کنترل می‌کند.
5. `deleteWebhook(drop_pending_updates=False)` اجرا می‌شود تا updateها حذف نشوند.
6. فرمان‌های ربات ثبت، پنل مدیریت متصل و integrationهای اختیاری ساخته می‌شوند.
7. worker نگه‌داری شروع و offset ذخیره‌شده برای long polling خوانده می‌شود.
8. پس از بازگشت handler، فقط نتیجه‌ای غیر از `False` ACK است و offset به‌صورت monotonic ذخیره می‌شود. `False` صریح از خطای موقت پایهٔ دیتابیس NACK است: offset update جاری و موارد بعدی همان batch ذخیره نمی‌شود، batch قطع و `getUpdates` از offset ثابت با backoff نمایی سقف‌دار و stop-aware تکرار می‌شود. خطای terminal دامنه و شکست پاسخ Telegram ACK هستند تا poison update صف را متوقف نکند. مرگ process پیش از ذخیره offset نیز ممکن است همان update را replay کند.
9. در `SIGTERM`/`SIGINT` ابتدا stop event مشترک set می‌شود. همه callهای TelegramClient پس از request در حال اجرا retry/backoff تازه آغاز نمی‌کنند؛ poller خارج می‌شود و updateای که هم‌زمان با shutdown برگشته ولی handler آن شروع نشده، offset نمی‌گیرد و پس از restart دوباره دریافت می‌شود. worker غیر-daemon است و پس از پایان آیتم جاریِ bounded join می‌شود؛ listener و request threadهای callback نیز non-daemon هستند، listener بسته و confirmation در حال اجرا تا سقف مهلت shutdown drain می‌شود؛ سپس sessionهای HTTP بسته می‌شوند.

## مسیر پردازش update

```text
Telegram update
  -> TelegramClient.run_polling
  -> BotApplication.process_update_safe
  -> message یا callback dispatch
  -> identity/upsert + admin binding
  -> bot enabled / block / forced-join guard
  -> user flow یا AdminController
  -> Database transaction
  -> Telegram response یا durable outbox
  -> ACK: ذخیره offset و update بعدی
  -> NACK موقت DB: حفظ offset، توقف ادامه batch و retry همان update
```

callbackها با parserهای fail-closed خوانده می‌شوند. شناسه نامعتبر، entity متعلق به کاربر دیگر یا state منقضی نباید mutation ایجاد کند. update مدیریتی احراز‌شده با fingerprint وارد journal `processed_admin_updates` می‌شود: `started` فقط replay همان payload را resume، `completed` را skip و payload متفاوت با update ID یکسان را conflict می‌کند. مقصد toggle در `effect_json` freeze و create/state/message با کلیدهای دامنه‌ای محافظت می‌شوند. خطای موقت begin/mutation/complete journal تا poller به‌شکل NACK می‌رسد؛ diagnostic فقط در تلاش اول و به‌صورت best effort است. این مدل ادعای exactly-once شبکه‌ای ندارد.

سطوح مدیریتی پرتعداد به query محدود و count هم‌فیلتر متکی‌اند، نه دریافت newest-N ثابت و clamp خروجی. `/orders` با `id DESC`، `/tickets` با `updated_at DESC`، users با `id DESC` و تاریخچه‌های transaction/reward/referral با ترتیب deterministic، ۲۰ ردیف در صفحه می‌گیرند. `_page_bounds` صفحه خارج بازه را رد و `_send_blocks` تمام ردیف‌های صفحه را در چند پیام زیر سقف Telegram نگه می‌دارد. `/user` فقط preview است؛ چهار فرمان `/user_orders`، `/user_transactions`، `/user_referrals` و `/user_rewards` تاریخچه کامل و count دقیق را عرضه می‌کنند و جست‌وجوی سفارش مالکیت user را دوباره بررسی می‌کند.
## مدل state مکالمه

صفحه محصول/FAQ متن بلند را با `split_telegram_html` کامل نمایش می‌دهد و دکمه‌ها در آخرین قطعه هستند. referral تمام قواعد فعال داخل بازه جاری را با offset می‌خواند و مبلغ/محصول/شروط را توضیح می‌دهد. سطوح مدیریتی category/product/inventory/discount/reward/FAQ/admin از `_management_rows` و `_send_page` با صفحات ۲۰تایی استفاده می‌کنند؛ `/order` و `/ticket` ادامه متن را حذف نمی‌کنند.

state کوتاه‌مدت هر کاربر در `user_states` ذخیره می‌شود، نه در حافظه process؛ بنابراین restart مکالمه را از بین نمی‌برد. هر state یک نام، JSON کمینه و timestamp دارد. handler قبل از mutation باید وجود، مالکیت و وضعیت entity مرجع را دوباره اعتبارسنجی کند و stateهای stale را پاک کند.

نمونه stateها شامل دریافت نام/شماره اولین خرید، مبلغ شارژ، دریافت فیش، متن/پیوست تیکت، اطلاعات سفارش دستی و ویرایش امن inventory است.

## سازگاری تراکنشی و idempotency

SQLite با `foreign_keys=ON`، WAL، `busy_timeout` و transactionهای `BEGIN IMMEDIATE` استفاده می‌شود. invariantهای اصلی:

- یک update تکراری نباید سفارش، پرداخت، تحویل، ledger یا پاداش تکراری بسازد.
- کلید idempotency فقط وقتی قابل استفاده مجدد است که actor، entity و همه terms مهم یکسان باشند؛ collision متناقض خطاست.
- پایان first-contact، Order و خلاصه `order:{id}:created-summary` را در transaction واحد می‌سازد و فقط بعد state خرید را پاک می‌کند؛ replay همان update باید همان Order/notice را بازیابد.
- `orders.order_origin` خرید تجاری `customer` را از تخصیص داخلی `admin_assignment` جدا می‌کند؛ ADM/سفارش داخلی صفرمبلغ از reward، first purchase، buyer و revenue حذف و marker پاداش آن هنگام ساخت پر می‌شود.
- موجودی آماده در transaction تخصیص می‌یابد تا دو خریدار یک payload نگیرند.
- واحد مالی محصول و payment فقط `TOMAN` است؛ برچسب نمایش currency دامنه را تغییر نمی‌دهد.
- URLهای بیرونیِ قابل کلیک، قوانین و invoice provider فقط پس از validation URL مطلق HTTPS بدون credential، `localhost`، IP literal محلی/خصوصی/reserved یا host عددی مبهم استفاده می‌شوند؛ کانال/invite Telegram قید canonical سخت‌گیرانه‌تری دارند. validator برای linkهای client-side DNS lookup/TOCTOU انجام نمی‌دهد.
- مبلغ کیف پول source of truth جمع `wallet_entries.amount_signed` است؛ فیلدهای snapshot فقط برای audit و نمایش‌اند.
- تخفیف فعال هر سفارش یکتا است و در لغو/انقضا release می‌شود.
- transition مالی terminal قابل بازگشت خودکار نیست؛ `paid/completed/refunded` setter عمومی سفارش ندارند و فقط workflow تخصصی پرداخت یا fulfillment دو وضعیت نخست را ثبت می‌کند. workflow ورود به `refunded` در نسخه فعلی وجود ندارد. setter عمومی Order در حضور external payment `pending/verifying`، مقصد `cancelled|expired|rejected` را نیز رد می‌کند؛ receipt card و crypto evidence مسیر تخصصی خود را دارند. Payment در `paid` terminal است و `set_payment_status(refunded)` عمداً تا افزودن workflow مالی اثبات‌شده رد می‌شود.
- سفارش پرداخت‌شده از دو مسیر مستقل reward و fulfillment قابل reconciliation است. `reward_processed_at` فقط completion پاداش است؛ selector صفحه‌بندی‌شدهٔ status=`paid` حتی پس از ثبت این marker، تحویل ready یا transition/prompt محصول manual را ادامه می‌دهد.
- fulfillment خرید تجاری به اعلان canonical موفقیت وابسته است. `order_success_notice_ready` در نبود outbox یا statusهای `queued/sending` بسته و در `sent|failed|cancelled` باز است؛ در نتیجه اعلان در حالت قابل‌تحویل مقدم می‌ماند ولی شکست terminal Telegram paid Order را برای همیشه strand نمی‌کند.
- هر Order در مجموع card/crypto فقط یک external intent فعال و هر user در مجموع این دو روش فقط یک topup تازه فعال دارد؛ replay فقط با method/amount/terms یکسان معتبر است و intent متفاوت conflict می‌شود. جایگزینی ضمنی رخ نمی‌دهد. query نمایشی کیف پول تمام topupهای فعال را برمی‌گرداند تا اگر داده legacy دو روش فعال داشت، هیچ intent قابل‌پرداختی پنهان نشود؛ این حالت فقط compatibility است. مبلغ تطبیقی یکتا فقط در card استفاده می‌شود.
- شاهد provider پیش از settlement به‌صورت immutable و hash‌شده commit می‌شود. رخداد completedِ اعمال‌نشده یک recovery queue دیتابیس‌محور است؛ پاسخ مبهم/partial/ناسازگار در review می‌ماند و هیچ اثر مالی مستقیم ندارد.
- فیش card و اطلاعات سفارش manual پیش از alert در DB commit می‌شوند. hash نوع/شناسه فیش یا JSON اطلاعات، نسخه alert را در کلید outbox می‌سازد تا retry همان نسخه تکراری نباشد و replacement واقعی اعلان تازه داشته باشد؛ بازیابی این دو فقط برای owner/admin از فرمان اختصاصی انجام می‌شود. پیوست photo/document تیکت نیز در DB می‌ماند و `/ticket_attachment MESSAGE_ID` پس از revalidation نقش و entity آن را برای owner/admin/support بازمی‌فرستد.
- در ارسال اطلاعات محصول manual، `submit_manual_order_info` مالکیت/type/status را داخل transaction دوباره کنترل و payload و transition `awaiting_info -> processing` را با هم commit می‌کند. alert نسخه‌دار جداگانه از روی داده commit‌شده قابل reconciliation است؛ بنابراین crash نمی‌تواند payload را با state قدیمی جدید ایجاد کند و داده legacy نیمه‌کاره همچنان برای alert دیده می‌شود.
- transition محصول ready بدون رزرو به `processing` نیز منبع recovery است: اگر process پیش از alert قطع شود، `list_ready_processing_orders` با cursor/wrap اعلان پایدار کاربر و همه owner/adminهای فعال را با کلیدهای ثابت بازسازی می‌کند.

## تحویل و outbox پایدار

برای پیام‌های حیاتی، ابتدا `outbound_messages` با کلید یکتا commit می‌شود و سپس ارسال Telegram انجام می‌شود. خطای موقت به retry زمان‌بندی‌شده و خطای دائمی 4xx غیر از 429 به وضعیت terminal تبدیل می‌شود. claim پنج‌دقیقه‌ای stale دوباره آزاد می‌شود.

کلیدهای موفقیت Order عبارت‌اند از `payment:{payment_id}:order-confirmed` برای پرداخت بیرونی و `order:{order_id}:wallet-confirmed`، `order:{order_id}:discount-confirmed` و `order:{order_id}:free-confirmed` برای کیف پول، تخفیف کامل و محصول رایگانِ تأییدشده در مسیر کاربر. متن canonical شماره سفارش، محصول، مبلغ و روش را دارد. maintenance missing notice را پیش از reward/fulfillment با cursor/wrap می‌سازد؛ همه شاخه‌های ready، reserve و manual همین dependency را درست پیش از mutation دوباره بررسی می‌کنند. محصول رایگان کاربر با وجود created-summary از فراخوانی داخلی رایگان تفکیک می‌شود. terminal failure در outbox و شمارش عملیاتی باقی می‌ماند و مجوز queue دستی دوباره یا تکرار settlement نیست.

تحویل خودکار محصول آماده دو مرحله قابل‌بازیابی دارد: تخصیص inventory به سفارش، سپس ایجاد/ارسال پیام دارای کلید پایدار. اگر process بین این دو crash کند، maintenance تخصیص ثبت‌شده را پیدا و فقط یک اعلان تحویل ایجاد می‌کند. در مسیر صریح `/inventory_assign` ابتدا داخل همان transaction بررسی می‌شود هیچ سفارش ready همان محصول با status `paid|processing|awaiting_stock` و بدون item assigned در backlog نیست؛ وجود آن تخصیص مستقیم را رد می‌کند تا FIFO دور زده نشود. فقط در نبود backlog، assignment، سفارش completed و delivery outbox از ابتدا در یک transaction ساخته می‌شوند؛ failure ساخت اعلان پایدار کل mutation را rollback می‌کند.

پیام credential آماده و خروجی نهایی `/complete` عمداً split/truncate نمی‌شوند. renderer واقعی پیش از add/edit موجودی، تغییر فیلد مؤثر محصول، assignment یا complete طول نهایی را با سقف ۳۹۰۰ می‌سنجد؛ failure قبل از mutation رخ می‌دهد. chunking فقط برای سطح‌های نمایشی/فهرستی مناسب است که شکستن آن‌ها معنای تحویل را عوض نمی‌کند.

## worker نگه‌داری

checkout صفرمبلغ کاربر با `create_order(defer_free_confirmation=True)` خلاصه pending می‌سازد؛ تخفیف کامل نیز فقط summary را به‌روز می‌کند. دکمه پرداخت از `confirm_zero_payable_order` می‌گذرد و اعلان discount/free پیش از fulfillment است؛ پیش‌فرض فراخوانی داخلی رایگان برای سازگاری حفظ شده است. همه تخصیص‌های ready تقدم پرداخت را تراکنشی کنترل می‌کنند؛ `_allocate_paid_timestamp` زمان پرداخت‌های تازه همان ثانیه را مرتب می‌کند و `_assign_inventory` سفارش قدیمی فاقد reservation را هم می‌بیند.

`run_maintenance` به‌صورت دوره‌ای این مسئولیت‌ها را دارد:

1. settlement رخداد completed provider که پیش‌تر durable شده ولی پس از crash اعمال نشده است؛
2. polling پرداخت‌های Plisio و ثبت immutable evidence؛
3. reconciliation اعلان reviewهای provider/card، نتیجه تصمیم‌های دستی آن‌ها، فیش کارت، اطلاعات سفارش manual، no-stock سفارش ready، تمام `reward_event`های فاقد notice، پیام‌های کاربر در تیکت و رخدادهای امنیتی card؛
4. انقضای سفارش‌های unpaid؛
5. انقضای paymentهای card بدون فیش و خارج مهلت اولیه؛ crypto با deadline محلی sweep نمی‌شود؛
6. بازیابی اعلان canonical پرداخت‌های بیرونی `paid` و اعلان موفقیت wallet-only/تخفیف کامل/خرید رایگان تأییدشده کاربر فاقد outbox متناظر؛ سپس reconciliation پاداش سفارش‌های موفق با `reward_processed_at IS NULL` و selector مستقل fulfillment سفارش‌های status=`paid`؛
7. بازیابی prompt سفارش‌های `awaiting_stock` و `awaiting_info`؛
8. تحویل FIFO رزروها پس از شارژ inventory؛
9. fulfil سفارش‌های ready در `processing` پس از restock؛
10. بازیابی پیام تحویل سفارش ready که assignment آن commit شده است؛
11. ارسال reminderهای موعدرسیده؛
12. claim/send/retry outbox؛
13. گزارش نهایی broadcastها.

در هر چرخه، missing success noticeهای بیرونی و wallet-only/تخفیف کامل/خرید رایگان تأییدشده کاربر پیش از reconciliation پاداش و fulfillment مستقل سفارش paid اجرا می‌شوند؛ سپس fulfil صف رزرو و fulfil سفارش ready در `processing` هرکدام budget محدود دارند. cursorهای reward و fulfillment در `maintenance_reward_reconcile_cursor` و `maintenance_fulfillment_reconcile_cursor` ذخیره و در انتهای backlog wrap می‌شوند؛ بنابراین ثبت marker پاداش یا یک ردیف خطادار قدیمی نباید fulfillment را حذف یا jobهای بعدی را گرسنه کند. اعلان‌های `reward_event`، no-stock و موفقیت بدون external نیز از query missing/cursorهای `maintenance_reward_notice_cursor`، `maintenance_ready_stock_alert_cursor` و `maintenance_zero_external_notice_cursor` بازسازی می‌شوند. queryهای اعلان سفارش pending، تیکت و بازیابی delivery تکمیل‌شده باید مستقیماً ردیف‌های فاقد outbox را با ترتیب پایدار/صفحه‌بندی محدود پیدا کنند، نه اینکه فقط جدیدترین مجموعه ثابت را بگیرند؛ در نتیجه backlog بزرگ نباید ردیف قدیمی missing را برای همیشه پنهان کند. fulfil رزرو از Order دارای stock با قدیمی‌ترین زمان پرداخت آغاز می‌شود؛ ساخت دیرهنگام reservation اولویت پرداخت را عوض نمی‌کند. مرحله ready-processing قدیمی‌ترین سفارش واجد stock را فقط برای محصولی انتخاب می‌کند که reservation معتبر قدیمی‌تری باقی ندارد؛ تمام‌شدن budget صدتایی رزرو در همان چرخه مجوز مصرف stock آن محصول به نفع processing نیست.

هر job باید batch محدود، idempotent و restart-safe بماند. loopها قبل از claim/شروع آیتم بعدی `BotApplication.stop_event` را بررسی می‌کنند؛ claim پیام یا reminder که shutdown پیش از ارسال آن را ببیند فوراً به صف برمی‌گردد. پیام‌های user-authored تیکت با cursor/wrap و کلید per-admin پایدار به همه owner/admin/support فعال alert می‌شوند؛ کپی مستقیم فایل فقط best effort است و `/ticket_attachment` fallback durable است. reminderها batch محدود claim و مستقل پردازش می‌شوند تا گیرنده blocked، اعضای بعدی را متوقف نکند؛ موردِ اشتراک پایان‌یافته بدون پیام `cancelled` می‌شود و متن روزهای مثبت تاریخ/ساعت مطلق پایان و timezone دارد؛ روز صفر «امروز» همراه ساعت پایان است و retry پس از پایان واقعی لغو می‌شود. پس از ساخته‌شدن outbox در `queued/sending`، outbox مالک retry است و reminder تا stale reconciliation در `processing` می‌ماند؛ آزادسازی فوری فقط وقتی مجاز است که outbox ساخته نشده باشد. بنابراین توقف حداکثر منتظر I/O همان آیتم جاری می‌ماند، نه تمام batch و نه زنجیره retry.

## پرداخت‌ها

پس از انقضای شارژ بدون فیش، `_reconcile_expired_wallet_topup_notices` از `list_expired_wallet_topups_missing_notice` استفاده و اعلان `payment:{id}:topup-expired` را با budget صدتایی و cursor/wrap بازیابی می‌کند. فیش ثبت‌شده به‌موقع تا تصمیم صریح مدیر بررسی می‌شود و انقضای خودکار هفت‌روزه ندارد.

سه منبع تأمین مبلغ سفارش وجود دارد:

1. کیف پول کامل؛
2. کیف پول جزئی به‌علاوه کارت‌به‌کارت؛
3. کیف پول جزئی به‌علاوه Plisio.

هر Order فقط یک external intent فعال در مجموع card/crypto و هر user در مجموع دو روش فقط یک topup تازه فعال دارد. replay تنها وقتی همان intent را می‌دهد که method/amount/terms یکسان باشد؛ تفاوت هرکدام conflict است و هیچ topup ضمنی replace/cancel نمی‌شود. داده legacy ممکن است دو topup فعال داشته باشد؛ `list_active_wallet_topup_payments` همه را برای resume جداگانه نمایش می‌دهد، در حالی که مسیر create همچنان intent دوم را رد می‌کند. پرداخت کارت با مبلغ قابل‌تطبیق، reference خارجی پایدار و بازه زمانی intent تطبیق داده می‌شود؛ uniqueness مبلغ فقط برای card است و مبلغ terminal آن تا ۲۴ ساعت پس از `max(expires_at, updated_at)` quarantine است تا callback رخداد بانکی قدیمی را به intent جدید وصل نکند. ایجاد بیش از ۲۰ intent کارت در ۲۴ ساعت یا ساخت تازه پس از ۳ لغو در یک ساعت fail closed، audit و alert می‌شود. فیش فقط برای card است: نخستین ارسال strictly پیش از expires_at است؛ فیش ثبت‌شده به‌موقع در verifying تا تصمیم صریح مدیر قابل بررسی و جایگزینی می‌ماند و خودکار منقضی نمی‌شود. Order/topup ارزی باز از URL امن ذخیره‌شده با «ادامه پرداخت ارزی» resume می‌شود و اگر URL legacy نامعتبر باشد هیچ لینک یا receipt اشتباهی نشان داده نمی‌شود. لغو فقط card payment متعلق به کاربر با status `pending` و بدون فیش را اتمیک همراه parent/hold/discount reconcile و parent Order را terminal می‌کند؛ تغییر روش به Order تازه نیاز دارد.

Plisio adapter مبلغ تومان را بر اساس multiplier تنظیم می‌کند، invoice می‌سازد و maintenance وضعیت transaction را می‌خواند. در Order و topup، Payment provisional با user/purpose/base amount و terms ثابت ابتدا در DB ساخته می‌شود؛ `payment_number` merchant order پایدار برای `create_invoice(return_existing=1)` است و `attach_crypto_invoice` شناسه/URL را فقط یک‌بار و اتمیک به همان user/payment فعال وصل می‌کند. ساخت provisional، Order را به `awaiting_confirmation` می‌برد تا wallet/discount دیگر terms invoice بیرونی را تغییر ندهند. بنابراین خطای مبهم create یا crash پیش از attach با retry همان provisional/invoice بازیابی می‌شود و poller فقط پس از attach کامل آن را می‌بیند. URL invoice فقط اگر HTTPS مطلق و فاقد credential/host literal محلی یا خصوصی باشد ذخیره/نمایش داده می‌شود؛ validator DNS را resolve نمی‌کند. invoice صادرشده دکمه/مسیر لغو محلی ندارد و deadline محلی آن را terminal نمی‌کند. poller هویت `id/type=invoice` را کنترل و هر نتیجه مالی را ابتدا با payload/hash در `provider_payment_events` commit می‌کند: completed معتبر قابل settlement، terminal با crypto amount صفر قابل failure و partial/nonzero/unknown/mismatch قابل review است. `operation.amount` crypto دریافتی است، نه تومان؛ `params` fiat/source فقط در صورت حضور تطبیق و mismatch آن quarantine می‌شود. رخداد completed ثبت‌شده ولی settleنشده پیش از هر poll بعدی بدون network بازیابی می‌شود. مشاهده بعدی completed/terminal-zero review باز را با پیوند resolution می‌بندد؛ completed تازه پس از resolution/failedشدن قبلی review پرخطر تازه می‌سازد و Order terminal را احیا نمی‌کند. تصمیم owner با note حسابرسی می‌شود و credit احتمالی فقط با completed evidence دقیق به ledger می‌رود؛ برای Order قدیمی این مبلغ اعتبار جبرانی کیف پول است، نه revenue همان فروش. API key/token از URL خطا، repr، exception chain، traceback و log حذف می‌شود.

## مدل authorization

| نقش | دامنه کلی |
|---|---|
| `owner` | همه عملیات، مدیریت مدیران و backup کامل |
| `admin` | مدیریت فروشگاه، سفارش، پرداخت، کاربران، محتوا، پاداش و گزارش؛ مشاهده فیش و پیوست manual-order |
| `support` | مشاهده خلاصه و تاریخچه کامل سفارش/تراکنش/دعوت/پاداش کاربر، تیکت و پیوست همان تیکت، پیام مستقیم و درخواست اطلاعات؛ بدون فیش/payment attachment، پیوست manual-order، تغییر مالی یا تکمیل دلخواه |

delegated admin فقط بعد از اثبات زوج username و private chat/user ID (`identity_verified_at`) مجوز می‌گیرد. grant ناشناخته pending است و فقط نخستین update دقیق همان زوج آن را verify می‌کند؛ پس از آن chat ID anchor و username metadata قابل refresh از همان chat است، بنابراین rename همان حساب access را قطع و reassignment در chat دیگر access را ایجاد نمی‌کند. root با marker یکتای `is_bootstrap_owner` نگه‌داری می‌شود؛ restart role/active را بازنویسی نمی‌کند، legacy conflict fail closed است و configured chat ID فقط marker را به owner فعال و verifyشده منتقل می‌کند. فقط owner می‌تواند owner دیگری بسازد و آخرین owner فعال قابل غیرفعال‌سازی یا تنزل نیست.

## محدودیت مقیاس و topology

- فقط یک poller فعال برای هر token و database پشتیبانی می‌شود.
- SQLite برای یک process با burst معمول ربات مناسب است. چند replica، filesystem شبکه‌ای یا write throughput بالا نیازمند انتقال transaction layer به PostgreSQL و lock توزیع‌شده است.
- callback server پیش‌فرض loopback است. exposure عمومی باید پشت reverse proxy TLS، firewall و secret قوی انجام شود.
- broadcastها صف‌بندی می‌شوند تا rate limit Telegram و restart مدیریت شود.

## تصمیم‌های معماری تثبیت‌شده

1. long polling به‌جای webhook Telegram، مطابق نیاز محصول.
2. SQLite و online backup برای استقرار ساده تک‌گره‌ای.
3. snapshot مشخصات محصول در سفارش برای جلوگیری از تغییر تاریخچه با ویرایش کاتالوگ.
4. ledger append-only و entry جبرانی به‌جای بازنویسی موجودی.
5. outbox پایدار برای اعلان‌های حیاتی.
6. HTML فقط با opt-in صریح `html:` و validator allowlist.
7. custom emoji با ID اختیاری؛ متن label دکمه‌ها بدون Unicode emoji، جز پیشوند ثابت ✅/❌ وضعیت کانال در مدیریت جوین اجباری به درخواست صریح کاربر.
8. هویت مدیر با chat ID verifyشده و root marker پایدار؛ username به‌تنهایی مجوز نیست.
9. journal `started/completed` همراه با NACK polling برای resume امن update مدیریتی: خطای موقت DB offset را جلو نمی‌برد، اما خطای terminal ACK می‌شود؛ نه preclaim at-most-once و نه تضمین شبکه‌ای exactly-once.
10. خلاصه ساخت Order و dependency اعلان موفقیت پرداخت در outbox پیش از fulfillment.
11. pagination commandمحور ۲۰تایی با count هم‌فیلتر و ترتیب deterministic برای پیمایش کامل سطوح مدیریتی پرتعداد.

## راهنمای تغییر برای agent

پیش از تغییر، [development.md](development.md)، [USE_CASES.md](USE_CASES.md) و [TRACEABILITY.md](TRACEABILITY.md) را بخوانید. مسیر معمول تغییر:

1. invariant و transition موردنظر را مشخص کنید.
2. mutation را در `db.py` تراکنشی و idempotent بسازید.
3. orchestration را در `bot.py` یا `admin.py` اضافه کنید.
4. متن/keyboard را در ماژول تخصصی نگه دارید.
5. تست happy path، authorization، replay، stale state، collision و crash recovery بنویسید.
6. schema تازه و migration دیتابیس قبلی را هم‌زمان به‌روزرسانی کنید.
7. مستندات و traceability را در همان commit اصلاح کنید.

برای تغییر قرارداد بین ماژول‌ها، ابتدا diagram متناظر را نیز به‌روزرسانی کنید تا agent بعدی به برداشت قدیمی تکیه نکند.
