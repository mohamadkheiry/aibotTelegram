# ربات فروشگاهی الون اکانت

یک ربات فروشگاهی فارسی برای تلگرام با فروش محصول آماده و محصول نیازمند فعال‌سازی دستی، کیف پول، پرداخت، سفارش، انبار، پشتیبانی و مدیریت داخل خود ربات.

مخزن رسمی: [mohamadkheiry/aibotTelegram](https://github.com/mohamadkheiry/aibotTelegram)

مرکز مستندات کامل بیزنس، یوزکیس، نمودارها، توسعه، پشتیبانی و استقرار در [docs/readme.md](docs/readme.md) قرار دارد. coding agentها باید ابتدا [AGENTS.md](AGENTS.md) را بخوانند.

گزارش تطبیق با فایل‌های اصلی، اصلاحات و شواهد تست در [docs/SPEC_AUDIT.md](docs/SPEC_AUDIT.md) و دو آموزش ویدیویی قابل بازتولید در [docs/training/README.md](docs/training/README.md) قرار دارند.

این مخزن «سورس قابل نصب» است؛ وجود سورس در GitHub به معنی میزبانی ۲۴ساعته process ربات نیست و استقرار production باید طبق [docs/deployment.md](docs/deployment.md) انجام شود.

## نکته امنیتی مهم

توکن ربات را فقط در فایل `.env` یا secret manager سرور قرار دهید. توکن واقعی نباید در سورس، Dockerfile، Compose، لاگ، اسکرین‌شات یا پیام عمومی ثبت شود. اگر توکن قبلاً در جایی افشا شده است، قبل از استقرار از طریق [BotFather](https://t.me/BotFather) آن را revoke و توکن تازه صادر کنید.

## امکانات اصلی

- دریافت پیام‌ها با `getUpdates` و long polling؛ Telegram webhook استفاده نمی‌شود. خطای موقت دیتابیس در update مدیریتی با NACK صریح، حفظ offset و retry همان update پیش از موارد بعدی batch بازیابی می‌شود.
- منوی فارسی با ترتیب مرجع و لینک مستقیم کانال، بدون ایموجی در متن دکمه‌ها، با رنگ و آیکون سفارشی اختیاری.
- جوین اجباری چندکاناله با فهرست مدیریتی `/joins` و اعتبارسنجی دسترسی ربات هنگام افزودن کانال.
- دسته و زیردسته همراه با آیکون، توضیح و ترتیب نمایش؛ محصولات `ready` و `manual`.
- انبار، ویرایش امن payload، رزرو و تحویل خودکار موجودی آماده با رعایت تقدم پرداخت حتی در پرداخت‌های هم‌ثانیه.
- ثبت نام و دریافت شماره تلفن با دکمه امن `request_contact`.
- سفارش یونیک، تخفیف، کیف پول، کارت‌به‌کارت و پرداخت ارزی اختیاری.
- تأیید دستی فیش و callback اختیاری MacroDroid برای پیامک بانک.
- اتصال اختیاری Plisio و بررسی وضعیت تراکنش از سرویس‌دهنده.
- تیکت با تغییر وضعیت و بازگشایی، FAQ، پیام گروهی، گزارش‌های فیلترپذیر، دعوت و پاداش؛ فهرست‌های مدیریتی کاربر/سفارش/تیکت و تاریخچه کامل هر کاربر صفحه‌بندی می‌شوند.
- نقش‌های `owner`، `admin` و `support` و افزودن مدیر با username و chat ID. نقش `support` فقط امکان مشاهده خلاصه و تاریخچه کامل سفارش/تراکنش/دعوت/پاداش کاربر، کار با تیکت و پیوست همان تیکت، پیام مستقیم و درخواست مجدد اطلاعات را دارد؛ فیش/payment attachment، پیوست اطلاعات سفارش manual، تغییر دلخواه وضعیت یا تکمیل سفارش برای این نقش مجاز نیست.
- دسترسی مدیر فقط پس از اثبات زوج username و private chat/user ID فعال می‌شود؛ پس از آن chat ID پایدار مبنای مجوز است و تغییر username همان حساب فقط metadata را به‌روزرسانی می‌کند. grant ناشناخته تا نخستین تعامل دقیق همان زوج pending می‌ماند.
- بکاپ کامل دیتابیس فقط برای نقش `owner`.
- متن غنی امن با پیشوند صریح `html:` در فیلدها و فرمان‌های متنی پشتیبانی‌شده؛ متن عادی همیشه escape و `href` فقط HTTPS مطلقِ بدون credential و host literal محلی/خصوصی پذیرفته می‌شود.
- SQLite با foreign key، WAL و عملیات تراکنشی.
- در نصب تازه، کیف پول فعال است؛ کارت تا ثبت هم‌زمان شماره و صاحب حساب مخفی می‌ماند و ارز دیجیتال تا وجود `PLISIO_API_KEY` و فعال‌سازی صریح `/payment crypto on` نمایش داده نمی‌شود. فرمان فعال‌سازی روش بیرونیِ ناقص fail closed است.

## چرخه سفارش، پرداخت و اشتراک

- هر کاربر حداکثر ۱۰ سفارش پرداخت‌نشده فعال با وضعیت `pending_payment` یا `awaiting_confirmation` می‌تواند داشته باشد.
- هر کاربر برای هر روش پرداخت حداکثر ۵ پرداخت فعال با وضعیت `pending` یا `verifying` می‌تواند داشته باشد.
- برای هر کاربر در مجموع روش‌های کارت و crypto فقط یک درخواست جدید شارژ کیف پول می‌تواند فعال باشد. replay فقط با همان روش، مبلغ و terms همان intent را برمی‌گرداند؛ روش، مبلغ یا terms متفاوت conflict است و هیچ intent به‌طور ضمنی replace یا cancel نمی‌شود. اگر داده قدیمی پیش از این invariant دو intent فعال داشته باشد، صفحه کیف پول هر دو را جداگانه برای ادامه همان پرداخت نشان می‌دهد، اما intent دوم تازه نمی‌سازد.
- هر سفارش در هر لحظه فقط یک intent بیرونی فعال card/crypto دارد. invoice ارزی محلی لغو/منقضی نمی‌شود و در جزئیات سفارش تا وقتی URL ذخیره‌شده همچنان امن باشد با دکمه «ادامه پرداخت ارزی» قابل بازیابی است؛ «ارسال فیش» فقط برای کارت نمایش داده می‌شود. پاسخ partial یا مبهم وارد review و completed evidence معتبر settle می‌شود. اگر completed پس از terminalشدن review سفارش برسد، سفارش قدیمی احیا نمی‌شود و مبلغ فقط با تصمیم owner و شاهد قطعی به‌عنوان اعتبار جبرانی کیف پول ثبت می‌شود، نه درآمد همان فروش.
- ساخت نخستین Order و خلاصه کاربر با کلید `order:{id}:created-summary` در یک transaction ثبت می‌شوند؛ failure ارسال یا replay همان update سفارش دوم نمی‌سازد و Order را بدون تأیید پایدار رها نمی‌کند.
- اعلان کامل موفقیت پرداخت، شامل شماره سفارش، محصول، مبلغ و روش، پیش از تحویل آماده، رزرو یا درخواست اطلاعات manual در outbox قرار می‌گیرد. تا وقتی اعلان `queued/sending` است fulfillment متوقف می‌ماند؛ `sent` یا شکست/لغو terminal آن اجازه ادامه می‌دهد تا خرابی دائمی Telegram سفارش پرداخت‌شده را برای همیشه متوقف نکند.
- مهلت پایه سفارش و intent کارت ۳۰ دقیقه است. فیش نخست باید در همان مهلت برسد؛ پس از آن پرداخت `verifying` تا تصمیم صریح مدیر باز می‌ماند و خودکار پس از هفت روز بسته نمی‌شود. invoice ارزی با deadline محلی terminal نمی‌شود و نتیجه provider مبناست. شارژ کارت منقضیِ بدون فیش هشدار پایدار «دیگر پرداخت نکنید» دارد.
- محصول رایگان و تخفیف صددرصد نیز ابتدا خلاصه سفارش می‌گیرند و فقط پس از تأیید صریح دکمه پرداخت، تسویه و تحویل می‌شوند.
- یادآوری اشتراک روزهای صحیح نامنفی را می‌پذیرد؛ صفر یعنی روز پایان در `TIMEZONE`، پیش از لحظه انقضا. زمان دقیق پایان در متن می‌آید و retry پس از انقضا لغو می‌شود.
- مدت اشتراک از زمان تحویل واقعی شروع می‌شود: برای محصول آماده از زمان تخصیص موجودی و برای محصول دستی از زمان تکمیل سفارش. پرداخت یا ورود به صف رزرو به‌تنهایی زمان اشتراک را آغاز نمی‌کند.
- تخصیص مستقیم موجودی توسط مدیر یک Order داخلی `admin_assignment` می‌سازد تا تحویل قابل حسابرسی بماند، اما خرید/درآمد تجاری نیست و پاداش خرید یا «اولین خرید» را مصرف نمی‌کند.
- پیام نهایی تحویل محرمانه باید در سقف محافظه‌کارانه ۳۹۰۰ نویسه جا شود. موجودی آماده، تغییر مشخصات مؤثر بر متن تحویل و `/complete` دستی اگر پیام نهایی را از این سقف عبور دهند، پیش از تخصیص موجودی یا تغییر وضعیت سفارش رد می‌شوند؛ متن تحویل credential نه truncate و نه به چند پیام شکسته می‌شود.

## معماری اجرا

فرایند اصلی با فرمان زیر اجرا می‌شود:

```bash
python -m app.main
```

ربات برای تلگرام فقط اتصال خروجی HTTPS می‌سازد و updateها را با long polling دریافت می‌کند. offset فقط پس از acknowledgeشدن update در SQLite ذخیره می‌شود تا پس از restart ادامه کار مشخص باشد. `None` و پاسخ‌های terminal، از جمله خطای API تلگرام هنگام پاسخ به مدیر، ACK هستند؛ فقط `False` صریح برای خطای موقت پایهٔ دیتابیس NACK است. در NACK، offset همان update و موارد بعدی batch ذخیره نمی‌شود، batch قطع و همان offset با backoff نماییِ سقف‌دار و stop-aware دوباره poll می‌شود. هنگام initialize، برنامه `deleteWebhook` را با `drop_pending_updates=False` فراخوانی می‌کند و سپس فقط updateهای `message` و `callback_query` را poll می‌کند. اگر callback کارت‌به‌کارت فعال باشد، همان فرایند یک HTTP server کوچک را در یک thread پس‌زمینه اجرا می‌کند. این HTTP server webhook تلگرام نیست.

تنها یک نمونه از فرایند ربات را اجرا کنید. اجرای هم‌زمان چند replica با یک توکن باعث رقابت روی `getUpdates` می‌شود و SQLite این پروژه نیز برای scale-out چندنمونه‌ای طراحی نشده است.

## نصب محلی

نیازمندی‌ها:

- Python 3.12 یا جدیدتر
- دسترسی HTTPS خروجی به `api.telegram.org`
- فضای نوشتن برای پوشه `data`

Linux/macOS:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Windows PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

## آماده‌سازی در BotFather

1. در [BotFather](https://t.me/BotFather) با `/newbot` ربات را بسازید یا ربات موجود را انتخاب کنید.
2. token تازه را فقط در `BOT_TOKEN` قرار دهید. اگر token قبلی دیده یا ارسال عمومی شده، از BotFather آن را revoke کنید.
3. نام، تصویر و توضیحات عمومی را در BotFather تنظیم کنید؛ این موارد از دیتابیس فروشگاه جدا هستند.
4. برنامه در شروع، فهرست فرمان‌های پایه را با `setMyCommands` ثبت می‌کند و webhook قدیمی را حذف می‌کند.
5. Premium‌بودن مالک یا داشتن username اضافه واجد شرایط در Fragment فقط شرط استفاده از custom emoji icon دکمه است؛ برای کارکرد اصلی ربات لازم نیست.

## تنظیم `.env`

در ریشه پروژه از روی فایل نمونه یک `.env` بسازید و permission آن را محدود کنید:

```bash
cp .env.example .env
chmod 600 .env
```

در Windows PowerShell از `Copy-Item .env.example .env` استفاده کنید. نمونه زیر هیچ secret واقعی ندارد:

```dotenv
BOT_TOKEN=PASTE_A_NEW_BOTFATHER_TOKEN_HERE

DATA_DIR=./data
DATABASE_PATH=./data/alone_account.sqlite3
TIMEZONE=Asia/Tehran
CURRENCY_LABEL=تومان
LOG_LEVEL=INFO

BOOTSTRAP_ADMIN_USERNAME=mohammadrezakheiry
BOOTSTRAP_ADMIN_CHAT_ID=
TELEGRAM_API_BASE=https://api.telegram.org

POLL_TIMEOUT_SECONDS=30
REQUEST_TIMEOUT_SECONDS=45
JOB_INTERVAL_SECONDS=10
ORDER_EXPIRY_MINUTES=30
RECEIPT_DELAY_SECONDS=60

PAYMENT_CALLBACK_BIND=127.0.0.1
PAYMENT_CALLBACK_PORT=8787
PAYMENT_CALLBACK_SECRET=

PLISIO_API_KEY=
PLISIO_CURRENCY=USDT_TRX
PLISIO_SOURCE_CURRENCY=IRR
PLISIO_AMOUNT_MULTIPLIER=10
PUBLIC_PAYMENT_CALLBACK_URL=
```

برای استقرار واقعی، `BOOTSTRAP_ADMIN_CHAT_ID` را با chat ID عددی مالک پر کنید. username را بدون `@` هم می‌توان نوشت. bootstrap پس از نخستین اثبات با marker یکتای `is_bootstrap_owner` به هویت پایدار وصل می‌شود؛ restart مالک غیرفعال‌شده را دوباره فعال نمی‌کند و انتقال marker فقط با تنظیم chat ID یک owner فعال و از قبل verifyشده انجام می‌شود. راهنمای چرخه امن bootstrap و افزودن مدیر در [راهنمای مدیریت](docs/ADMIN_GUIDE_FA.md) آمده است. `TELEGRAM_API_BASE` عمومی باید HTTPS باشد؛ HTTP فقط برای loopback محلی پذیرفته می‌شود.

برای ساخت secret تصادفی callback:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

خروجی را فقط در `PAYMENT_CALLBACK_SECRET` سرور و header امن MacroDroid ذخیره کنید. مقدار غیرخالی باید ۴۳ تا ۱۲۸ نویسه و فقط شامل حروف لاتین، رقم، `_` و `-` باشد؛ در غیر این صورت برنامه عمداً در preflight متوقف می‌شود.

## شروع ربات

برنامه در شروع webhook قدیمی را خودکار با متد رسمی `deleteWebhook` حذف می‌کند؛ طبق مستندات تلگرام، `getUpdates` تا وقتی webhook خروجی فعال است کار نمی‌کند. اگر این مرحله در لاگ خطا داد، مشکل شبکه/توکن را رفع کنید و webhook را با ابزار مدیریتی امن بررسی کنید. توکن را داخل command history یا URL قابل مشاهده ثبت نکنید.

سپس:

```bash
python -m app.main
```

در تلگرام `/start` را بفرستید. مالک می‌تواند برای دیدن فرمان‌ها `/admin_help` را ارسال کند.

## نمای سریع فرمان‌های مدیریتی تکمیلی

- `/joins` فهرست کانال‌های جوین اجباری و شناسه داخلی لازم برای toggle/delete را نشان می‌دهد.
- `/users` از حالت‌های `all`، `active`، `blocked`، `new [DAYS]`، `inactive [DAYS]`، `joined FROM TO` و `product PRODUCT_ID FROM TO` پشتیبانی می‌کند؛ آرگومان پایانی `PAGE` برای همه فهرست‌ها اختیاری است. برای سازگاری، یک عدد تنها پس از `new|inactive` همیشه `DAYS` است؛ برای انتخاب صفحه باید `DAYS PAGE` را هر دو بنویسید. هر صفحه ۲۰ ردیف، total و راهنمای قبلی/بعدی دارد. `active` یعنی کاربر غیرمسدودی که `updated_at` او در ۳۰ روز اخیر بوده است؛ `inactive [DAYS]` کاربران غیرمسدود قدیمی‌تر از بازه را نشان می‌دهد.
- `/orders [STATUS|all] [FROM TO] [PAGE]` و `/tickets [open|answered|closed|all] [PAGE]` تمام backlog را صفحه‌بندی می‌کنند. تاریخچه کامل یک کاربر با `/user_orders USER [STATUS|all] [PAGE|ORDER_NUMBER]`، `/user_transactions USER [PAGE]`، `/user_referrals USER [PAGE]` و `/user_rewards USER [PAGE]` در دسترس است؛ این فرمان‌ها برای نقش `support` نیز فقط به‌صورت مشاهده‌ای مجازند.
- `/report orders [STATUS|all] FROM TO`، `/report users joined FROM TO`، `/report users product PRODUCT_ID|all FROM TO` و `/report finance FROM TO` خلاصه مدیریتی و، در صورت وجود ردیف، فایل CSV می‌سازند.
- دسته را می‌توان با `/category_add عنوان | آیکون|0 | توضیح|0` ساخت و فیلدهای `icon` و `description` را با `/category_set` تغییر داد.
- `/inventory_edit ITEM_ID` payload یک آیتم تحویل‌نشده را از طریق پیام بعدی و بدون بازتاب محتوای محرمانه ویرایش می‌کند.
- `/ticket_status TICKET_NUMBER open|answered|closed` وضعیت تیکت را تغییر می‌دهد و می‌تواند تیکت بسته را دوباره باز کند.
- `/ticket_attachment MESSAGE_ID` برای `owner/admin/support` عکس یا سند ذخیره‌شده همان پیام تیکت را پس از بررسی دوباره role و entity بازمی‌فرستد؛ شناسه را از خروجی `/ticket` بگیرید.
- `/backup` فقط برای نقش `owner` مجاز است؛ `admin` و `support` نمی‌توانند بکاپ کامل را دریافت کنند.

برای فرستادن متن غنی در ورودی‌های پشتیبانی‌شده، خودِ مقدار متن را با `html:` شروع کنید؛ برای مثال:

```text
/broadcast_all html:<b>اطلاعیه</b> <a href="https://example.com">جزئیات</a>
```

فقط زیرمجموعه امن HTML تلگرام پذیرفته می‌شود و markup نامعتبر پیش از ذخیره یا ارسال رد خواهد شد. فهرست دقیق فرمان‌ها، فیلترها و تگ‌های مجاز در [راهنمای مدیریت](docs/ADMIN_GUIDE_FA.md) آمده است.

پاداش ترکیبی با syntax زیر قابل ساخت است؛ همه شرط‌های داخل JSON باید هم‌زمان برقرار باشند:

```text
/reward_add combined | AMOUNT | PRODUCT_ID|0 | CONDITIONS_JSON [| START|0 | END|0]
```

کلیدهای معتبر `CONDITIONS_JSON` عبارت‌اند از `minimum_successful_purchases`، `first_purchase`، `minimum_referrals`، `minimum_qualified_referrals`، `product_ids` و `minimum_order_amount`. نمونه‌ها و قواعد اعتبارسنجی در [راهنمای مدیریت](docs/ADMIN_GUIDE_FA.md) آمده است.

## رنگ و ترتیب منوی اصلی

چیدمان پیاده‌سازی‌شده:

1. `فروشگاه`
2. `کیف پول` و `حساب من`
3. `پشتیبانی`
4. `دعوت و کسب درآمد`
5. `کانال`

نگاشت style فعلی:

| دکمه | Bot API style | نمایش مورد انتظار |
|---|---|---|
| فروشگاه | `success` | سبز |
| کیف پول | `success` | سبز |
| حساب من | `primary` | آبی |
| پشتیبانی | پیش‌فرض | وابسته به برنامه و تم کاربر |
| دعوت و کسب درآمد | `primary` | آبی |
| کانال | پیش‌فرض | وابسته به برنامه و تم کاربر |

تلگرام فقط styleهای `success`، `primary` و `danger` را به‌ترتیب به‌صورت سبز، آبی و قرمز تعریف کرده است. رنگ برنزی/طلایی تصویر مرجع style مستقل ندارد؛ رنگ دکمه پیش‌فرض را client و theme تعیین می‌کند، بنابراین تطابق پیکسلی بین همه نسخه‌های Telegram قابل تضمین نیست. clientهای قدیمی نیز ممکن است style جدید را نادیده بگیرند.

متن دکمه‌ها عمداً ایموجی ندارد. آیکون مناسب فقط از فیلد `icon_custom_emoji_id` فرستاده می‌شود. شناسه‌ها را می‌توان با متغیرهای زیر تنظیم کرد:

```dotenv
BUTTON_ICON_SHOP=
BUTTON_ICON_WALLET=
BUTTON_ICON_ACCOUNT=
BUTTON_ICON_SUPPORT=
BUTTON_ICON_REFERRAL=
BUTTON_ICON_CHANNEL=
BUTTON_ICON_BUY=
BUTTON_ICON_INFO=
BUTTON_ICON_BACK=
BUTTON_ICON_PAY=
BUTTON_ICON_DISCOUNT=
BUTTON_ICON_CARD=
BUTTON_ICON_CRYPTO=
BUTTON_ICON_COPY=
BUTTON_ICON_RECEIPT=
BUTTON_ICON_CANCEL=
BUTTON_ICON_ORDER=
BUTTON_ICON_UPLOAD=
BUTTON_ICON_PHONE=
```

طبق [مستندات رسمی KeyboardButton](https://core.telegram.org/bots/api#keyboardbutton) و [InlineKeyboardButton](https://core.telegram.org/bots/api#inlinekeyboardbutton)، آیکون custom emoji فقط برای botهایی قابل استفاده است که username اضافه از Fragment خریده‌اند، یا در پیام مستقیم bot به private/group/supergroup وقتی مالک bot Telegram Premium دارد. BotFather خودِ این محدودیت را حذف نمی‌کند. اگر حساب مالک واجد شرایط نباشد، متغیرها را خالی بگذارید؛ دکمه‌ها با متن ساده و بدون ایموجی کار می‌کنند.

## تست

```bash
python -m unittest discover -s tests -v
```

تست‌ها به توکن واقعی یا اتصال Telegram نیاز ندارند.

## Docker

پس از ساخت `.env`:

```bash
docker compose build
docker compose up -d
docker compose logs -f bot
```

Compose داده را در named volume پایدار `bot-data` نگه می‌دارد و پورت callback را فقط روی loopback میزبان منتشر می‌کند. برای دسترسی MacroDroid از اینترنت، callback را پشت reverse proxy و HTTPS قرار دهید؛ پورت 8787 را مستقیم روی اینترنت باز نکنید. حذف volume با `docker compose down -v` داده production را پاک می‌کند و در عملیات عادی ممنوع است.

## راهنماهای تکمیلی

- [راهنمای مدیر](docs/ADMIN_GUIDE_FA.md)
- [راهنمای استقرار و امنیت](docs/DEPLOYMENT_FA.md)
- [Telegram Bot API: getUpdates](https://core.telegram.org/bots/api#getupdates)
- [مستندات Plisio](https://plisio.net/documentation)
