# راهنمای امنیت و حریم خصوصی

## دامنه

این سند کنترل‌های امنیتی کد، استقرار و عملیات ربات را توضیح می‌دهد. اطلاعات حساب‌های تحویلی، شماره تماس، chat ID، پیام‌های تیکت، پرداخت و backup داده حساس‌اند. token ربات، secret callback و API keyهای پرداخت secret سطح بالا هستند.

## مدل تهدید

تهدیدهای اصلی:

- افشای token/API key از Git، log، traceback، URL یا backup؛
- جعل callback بانکی، replay reference یا اتصال رخداد قدیمی به intent جدید؛
- تکرار Telegram update پس از crash و اجرای دوباره mutation؛
- تصاحب username یا grant ناقص مدیر بدون اثبات private chat/user ID؛
- دسترسی role محدود به عملیات مالی/مالک؛
- IDOR در callback سفارش، تیکت یا attachment؛
- تحویل یک inventory payload به دو کاربر؛
- injection در HTML تلگرام، CSV و JSON؛
- DoS با body بزرگ، callback malformed، متن طولانی یا broadcast؛
- اجرای دو poller و race روی updateها؛
- سرقت backup یا دیتابیس میزبان.

## مدیریت secret

- secret واقعی فقط در env process، فایل خارج repo با مجوز محدود یا secret manager قرار گیرد.
- `.env.example` فقط placeholder دارد؛ `.env` در `.gitignore` است.
- token نباید در command output، issue، commit، تصویر یا مستندات درج شود.
- `Settings.__repr__` token را redacted می‌کند و transport exceptionها URL حساس را نمایش نمی‌دهند. wrapper شبکه باید exception سانسورشده را بدون cause محرمانه بالا بیاورد تا حتی `traceback.format_exc()` نیز bot token یا API key را بازسازی نکند.
- اگر token در چت/commit/log افشا شد، از BotFather revoke و token تازه صادر، سپس همه instanceها restart شوند.
- `PAYMENT_CALLBACK_SECRET` در حالت غیرخالی باید ۴۳ تا ۱۲۸ کاراکتر URL-safe از مجموعه `A-Z`, `a-z`, `0-9`, `_`, `-` باشد؛ مقدار خالی listener را غیرفعال می‌کند. آن را مستقل از `PLISIO_API_KEY`، با مولد رمزنگارانه بسازید و دوره‌ای rotate کنید.
- `TELEGRAM_API_BASE` نباید credential، query یا fragment داشته باشد. HTTP فقط برای `localhost`/زیردامنه آن یا IP loopback همان میزبان مجاز است؛ هر مقصد غیر-loopback، از جمله test double شبکه، باید HTTPS باشد تا token موجود در مسیر Bot API روی متن ساده ارسال نشود.

پیش از هر push:

```bash
git grep -n -E '[0-9]{8,12}:[A-Za-z0-9_-]{30,}' -- . ':!docs/references/*.pdf' ':!docs/references/*.jpg'
git status --ignored
```

CI تست `test_repository_contains_no_runtime_state_or_plaintext_secrets` را در مرحله «Repository hygiene and secret scan» اجرا می‌کند. این guard همه فایل‌های tracked و unignored را برای `.env` و artifactهای runtime و الگوهای مشخص token تلگرام، token گیت‌هاب، AWS access key، private key و Stripe live key بررسی و build را fail می‌کند. این اسکن الگو-محور پوشش جامع همه providerها یا entropy-based secret scanning نیست؛ review دستی بالا و در صورت نیاز scanner تخصصیِ pinشده همچنان دفاع تکمیلی‌اند.

## احراز هویت و مجوز

- کاربر فقط در chat خصوصی پذیرفته می‌شود و Telegram user/chat ID از update معتبر خوانده می‌شود.
- grant delegated فقط پس از اثبات هم‌زمان username و private chat/user ID فعال می‌شود؛ زوج ناشناخته با `identity_verified_at=NULL` pending و بدون مجوز است. پس از اثبات، chat ID anchor و username metadata است: rename از update همان chat رکورد را تازه می‌کند، ولی reassignment username روی chat دیگر مجوز نمی‌سازد.
- bootstrap username-only فقط یک root pending می‌سازد. marker یکتای `is_bootstrap_owner` پس از anchorشدن از ساخت root قدیمی با username قبلی جلوگیری می‌کند؛ restart role/active ذخیره‌شده را بازنویسی و owner غیرفعال‌شده را re-enable نمی‌کند. انتقال marker فقط با configured chat ID متعلق به owner فعال و verifyشده مجاز است؛ مقصد ناشناخته/pending/non-owner و conflict legacy fail closed است.
- `python -m app.main --check` نیز روی همان دیتابیس schema را تا نسخه ۱۱ migrate و owner bootstrap را ایجاد/اعتبارسنجی می‌کند؛ conflict identity پیش از `getMe` fail می‌شود. این preflight را فقط بعد از backup و با identity نهایی release اجرا کنید.
- role در هر فرمان/callback دوباره از رکورد active بررسی می‌شود.
- تنها `owner` می‌تواند backup کامل و مدیریت حساس مدیران را انجام دهد.
- نقش `support` می‌تواند فهرست و جزئیات کاربر/سفارش/تیکت و چهار تاریخچه کامل سفارش، تراکنش، زیرمجموعه و پاداش user را فقط در private chat ببیند؛ این خروجی PII/اطلاعات مالی است و نباید forward یا log شود. این نقش حق تغییر کیف پول، پرداخت، کاتالوگ، reward، وضعیت دلخواه سفارش یا تکمیل مالی ندارد.
- callbackهای user باید مالکیت order/ticket/attachment را قبل از نمایش یا mutation کنترل کنند.
- فیش پرداخت و پیوست اطلاعات سفارش manual فقط برای `owner/admin` قابل بازفرستادن است؛ `support` فقط attachment تیکت را در دامنه پشتیبانی می‌بیند. `/ticket_attachment MESSAGE_ID` برای owner/admin/support بعد از revalidation نقش، وجود پیام و تیکت مرتبط مجاز است و raw file ID در متن `/ticket` چاپ نمی‌شود. شناسه فایل یا پیام به‌تنهایی مجوز نیست.
- self-contact فقط وقتی پذیرفته می‌شود که `contact.user_id` با فرستنده برابر باشد.

تغییر permission matrix باید هم‌زمان در تست `test_support_role_is_limited_but_can_use_direct_messages`، راهنمای مدیر و این سند ثبت شود.

update مدیریتی با `(update_id, fingerprint)` وارد journal `started/completed` می‌شود. `started` فقط replay همان payload را ادامه می‌دهد، `completed` skip و fingerprint متفاوت conflict می‌شود؛ target عملیات toggle پیش از mutation freeze می‌گردد. خطای موقت پایهٔ دیتابیس، از جمله begin یا complete journal، با `False` صریح NACK می‌شود: offset update جاری/موارد بعدی batch ذخیره نمی‌شود و retry از همان offset با backoff سقف‌دار انجام می‌گیرد. diagnostic خطا فقط در تلاش اول و best effort است؛ failure ارسال diagnostic نباید update را ACK کند. برعکس، خطای terminal دامنه و شکست پاسخ Telegram ACK می‌شوند تا یک recipient/درخواست مسموم صف را قفل نکند. domain idempotency همچنان لازم است و این کنترل نباید به‌عنوان exactly-once شبکه‌ای یا مجوز اعتماد به replay دلخواه تفسیر شود.

## Telegram و محتوای خروجی

- متن معمولی همیشه escape می‌شود.
- HTML فقط با پیشوند صریح `html:` فعال و با allowlist tag/attribute اعتبارسنجی می‌شود. `href` فقط HTTPS مطلق است؛ `http`، `tg:`، `mailto:`، userinfo، `localhost`، IP literal محلی/خصوصی/reserved و host عددی مبهم رد می‌شوند و مقصد Telegram باید به شکل canonical `https://t.me/...` نوشته شود.
- هر URL بیرونیِ قابل کلیک، `rules_url`، URL invoice provider و callback عمومی باید مطلق، HTTPS و بدون credential، whitespace/control، `localhost`، IP literal محلی/خصوصی/reserved یا host عددی مبهم باشد. validator عمداً DNS lookup انجام نمی‌دهد و درباره hostname عمومی که بعداً به IP خصوصی resolve شود تضمین DNS-level/TOCTOU نمی‌دهد؛ callsiteهای فعلی لینک client-side هستند. URL دکمه در زمان render نیز دوباره fail closed می‌شود تا داده legacy ناامن نمایش داده نشود.
- URL کانال اصلی فقط canonical `https://t.me/...` و invite جوین اجباری فقط canonical HTTPS روی `t.me` یا `telegram.me`، بدون port/query/fragment، پذیرفته می‌شود؛ دامنه مشابه یا suffix جعلی رد است.
- label دکمه‌ها بدون Unicode emoji است؛ icon سفارشی فقط ID ارائه‌شده Telegram است.
- طول پیام و callback data محدود و خروجی فهرست‌ها pagination/chunk می‌شود. فهرست‌های commandمحور users/orders/tickets و تاریخچه‌های user، ۲۰ ردیف در صفحه با count هم‌فیلتر و ترتیب deterministic دارند؛ split طولی باید تمام ردیف‌های صفحه را حفظ کند. شناسه user/order/ticket و شماره صفحه از ورودی trust نمی‌شوند و ownership جست‌وجوی سفارش user دوباره کنترل می‌شود.
- credential تحویل ready و خروجی `/complete` manual باید پیش از هر mutation در یک پیام و سقف ۳۹۰۰ نویسه جا شوند؛ این دو مسیر محرمانه truncate/split نمی‌شوند. payload یا متن بلند، از جمله رکورد legacy، قبل از assignment/complete رد می‌شود.
- attachment با Telegram file ID و kind اصلی `photo/document` ذخیره می‌شود. مالکیت تیکت در مسیر مشتری و role/entity در بازیابی مدیریتی تیکت، فیش یا manual-order attachment دوباره کنترل می‌شود؛ اعلان شبکه‌ای ناموفق نباید metadata فایل را از DB حذف کند. alert فیش و customer info با hash محتوا نسخه‌بندی می‌شود تا replacement از retry قابل تشخیص باشد؛ hash مجوز، رمزنگاری یا proof مالکیت نیست.
- خطای blocked recipient نباید update queue یا batch reminder را متوقف کند؛ پس از ساخته‌شدن outbox، همان outbox مالک retry است تا آزادسازی فوری reminder موجب head-of-line یا ارسال تکراری نشود. reminder اشتراک پایان‌یافته نباید ارسال شود و متن دیررس باید remaining-days واقعی را از `subscription_ends_at` محاسبه کند.

## پرداخت کارت و callback

callback server باید:

- پیش‌فرض فقط روی `127.0.0.1` bind شود؛
- در exposure عمومی پشت TLS reverse proxy و allowlist شبکه باشد؛
- secret را در Bearer header یا قرارداد مستند بپذیرد؛
- فقط JSON با content type صحیح، body محدود، کلیدهای غیرتکراری و مقدارهای validated قبول کند؛
- reference یکتا، مبلغ مثبت و timestamp معتبر بخواهد؛
- رخداد قدیمی/دیرهنگام را بدون mutation خطرناک در ledger `card_payment_events` ثبت کند؛
- برای retry caller کدهای HTTP سازگار و deterministic بدهد.

secret نباید query parameter عمومی باشد. health endpoint نیز بدون secret اطلاعات عملیاتی ندهد.

## پرداخت ارزی

- API key Plisio فقط سمت سرور است.
- invoice متعلق به user/purpose/amount مشخص و idempotency key پایدار است. برای Order و topup، provisional Payment و terms ثابت باید پیش از side effect شبکه ثبت شوند؛ `payment_number` merchant order پایدار `return_existing=1` است و `attach_crypto_invoice` فقط با revalidation user/state/method و منع collision نتیجه را اتمیک متصل می‌کند. provisional ناقص poll نمی‌شود و نباید با intent تازه یا تغییر wallet/discount جایگزین شود.
- هر پاسخ مالی پیش از settlement با payload/hash در ledger immutable ثبت و هویت `id/type=invoice` آن بررسی می‌شود.
- وضعیت paid فقط با completed evidence دقیق `id/type=invoice` ثبت می‌شود. `operation.amount` crypto دریافتی است، نه تومان؛ terminal با دریافت صریح crypto amount صفر failed و partial/nonzero/unknown/mismatch یا mismatch فیلدهای fiat/source در `params` در صورت حضور به review/quarantine می‌رود.
- deadline محلی crypto را terminal نمی‌کند. completed evidence ثبت‌شده ولی settleنشده پس از crash بدون network بازیابی می‌شود؛ completed دیررس پس از resolution/failedشدن قبلی review پرخطر تازه می‌سازد و Order terminal را خودکار احیا نمی‌کند.
- resolve فقط owner فعال و با note است. card review هیچ credit خودکار ندارد؛ provider credit فقط با completed evidence دقیق و wallet entry idempotent مجاز است. برای Order terminal مبلغ صرفاً اعتبار جبرانی کاربر است، نه revenue فروش قبلی.
- خطاها query string/API key را از message، exception chain و traceback redact می‌کنند.
- conversion تومان/ریال باید در config و تست صریح باشد.

## امنیت مالی و یکپارچگی

- تمام mutationهای مالی در transaction و از `Database` انجام شوند.
- `wallet_entries` append-only است؛ correction با entry جبرانی.
- idempotency collision با actor/entity/terms متفاوت باید fail closed باشد.
- reference خارجی نمی‌تواند برای payment دیگر reuse شود.
- هر Order فقط یک external intent فعال در مجموع card/crypto دارد؛ روش دوم نباید invoice یا instruction اول را orphan کند.
- external method ناقص fail closed است: card بدون شماره+صاحب حساب و crypto بدون API key یا setting فعال نه نمایش داده می‌شوند و نه با فرمان مدیر قابل فعال‌سازی‌اند. flag اولیه card به‌تنهایی availability نیست.
- setter عمومی Order در حضور external payment `pending/verifying` نمی‌تواند آن را cancelled/expired/rejected کند؛ card receipt فقط با reject تخصصی و crypto با evidence terminal provider بسته می‌شود. ردیف terminal قدیمی فقط برای پایش late transition است، نه الگوی ساخت state تازه.
- هر user در مجموع card/crypto فقط یک topup تازه فعال دارد؛ replay فقط method/amount/terms یکسان را می‌پذیرد و هر اختلافی conflict است. هیچ topup ضمنی replace/cancel نمی‌شود. UI تمام intentهای active را می‌خواند تا ردیف‌های dual-active به‌جامانده از legacy پنهان نشوند، اما create همچنان intent دوم را رد می‌کند. مبلغ یکتای تطبیق فقط برای card است و crypto با invoice/reference یکتا شناسایی می‌شود.
- مبلغ terminal کارت تا ۲۴ ساعت پس از `max(expires_at, updated_at)` quarantine است تا transfer دیررس صرفاً به‌دلیل amount برابر به intent تازه متصل نشود.
- هر user حداکثر ۲۰ intent کارت در ۲۴ ساعت می‌سازد و ۳ لغو در یک ساعت cooldown ساخت تازه را فعال می‌کند. rejection با کلید پایدار در `payment_security_events` ثبت و alert می‌شود؛ replay همان idempotency key نباید شمارنده یا event دوم بسازد.
- refund کیف پول و external payment باید هماهنگ باشد؛ نسخه فعلی هیچ transition اجرایی به `refunded` ندارد و status بدون workflow مالی اثبات‌شده و ledger جبرانی نباید تغییر کند.
- currency مالی محصول و payment در این نسخه فقط `TOMAN` است؛ `CURRENCY_LABEL` مجوز ارز دیگر نیست.
- فیش فقط card است؛ نخستین فیش باید پیش از `expires_at` و هر جایگزینی پیش از `expires_at + 7 days` برسد. grace از deadline اولیه محاسبه می‌شود و با تعویض فیش تمدید نمی‌شود. crypto باز فقط با `provider_invoice_url` ذخیره‌شده‌ای که در render نیز safe-HTTPS validation را پاس کند قابل resume است؛ URL خالی/legacy ناامن نه لینک می‌سازد و نه به receipt callback تبدیل می‌شود.
- لغو کاربر فقط card payment متعلق به او، `pending` و بدون فیش را در transaction دامنه‌ای لغو می‌کند؛ callback قدیمی نباید payment دارای فیش/`verifying` را لغو یا hold را آزاد کند. crypto invoice صادرشده قابل لغو محلی نیست، deadline محلی آن را terminal نمی‌کند و provider evidence تا نتیجه قطعی/late transition پایش می‌شود؛ UI نباید برای آن دکمه cancel بسازد.
- زمان انقضا و grace period رسید قبل از approval دوباره بررسی شود.
- گزارش مالی فقط از Order تجاری `order_origin=customer` با subtotal مثبت، ردیف‌های `paid_at` و ledger refund متناظر ساخته شود؛ تخصیص داخلی/صفرمبلغ نباید revenue یا buyer event بسازد.
- ساخت Order پس از first-contact باید خلاصه `order:{id}:created-summary` را در همان transaction ثبت و فقط بعد state خرید را پاک کند؛ crash/replay نباید سفارش بدون تأیید یا سفارش دوم بسازد.
- paid payment فاقد اعلان و Order موفق wallet-only/تخفیف کامل فاقد success notice باید توسط maintenance با query/cursor پایدار به outbox برگردد؛ recovery اعلان نباید settlement یا wallet capture/credit را تکرار کند. fulfillment تا status اعلان canonical از `queued/sending` خارج نشده است بسته می‌ماند؛ `sent` یا failure/cancellation terminal اجازه ادامه می‌دهد تا failure دائمی Telegram Order پرداخت‌شده را strand نکند. failure terminal باید در outbox/alert عملیاتی دیده شود و با SQL به retry نامحدود برنگردد.
- CSV همه سلول‌هایی را که پس از whitespace با `=`, `+`, `-`, `@` شروع می‌شوند neutralize می‌کند.

## امنیت inventory

- payload در فرمان echo نمی‌شود و برای ویرایش از state بعدی دریافت می‌شود.
- لیست عمومی/admin خلاصه نباید payload کامل را نمایش دهد مگر عملیات مجاز تحویل/backup.
- hash از duplicate جلوگیری می‌کند، اما hash جای رمزنگاری at rest نیست.
- میزبان و backup باید encrypted disk و ACL محدود داشته باشند.
- log کردن object کامل inventory یا database row ممنوع است.
- در تخصیص دستی، assignment، سفارش completed و delivery outbox باید در یک transaction commit شوند؛ ارسال مستقیم بدون رکورد پایدار یا commit ناقص مجاز نیست.

## پایگاه داده و backup

- directory data فقط برای service user قابل خواندن باشد.
- SQLite روی filesystem محلی قابل اعتماد قرار گیرد؛ share شبکه‌ای توصیه نمی‌شود.
- `foreign_keys=ON`، WAL و `busy_timeout` حفظ شوند.
- backup با online backup گرفته و hash/restore آن دوره‌ای در محیط ایزوله و access-controlled تست شود؛ integrity/schema را بدون راه‌اندازی poller، worker یا مسیر delivery بررسی کنید.
- backup قبل از migration و deploy نگه‌داری شود و retention مشخص داشته باشد.
- backup را در Telegram فقط owner دریافت می‌کند؛ انتقال آن همچنان ریسک دارد و باید حداقل شود.
- داده production هرگز برای تست محلی یا اجرای bot آزمایشی کپی نشود مگر با مجوز روشن و anonymization متناسب؛ restore drill صرفاً integrity/schema به اجرای bot نیاز ندارد.

## شبکه و میزبان

- پورت callback فقط در صورت نیاز باز شود؛ long polling به inbound port نیاز ندارد.
- container/systemd با non-root user، filesystem read-only تا حد ممکن، capability صفر و `no-new-privileges` اجرا شود.
- patch امنیتی OS و dependencyها منظم اعمال شود.
- یک instance فعال برای هر token؛ replica دوم می‌تواند `getUpdates` conflict و رفتار نامشخص ایجاد کند.
- ساعت و timezone میزبان با NTP همگام باشد.

## logging و مانیتورینگ

مجاز برای log:

- سطح خطا، نام متد، update/order/payment ID داخلی، latency و نتیجه کلی؛
- شروع polling، migration و health.

غیرمجاز:

- token، API key، Authorization header، callback secret؛
- payload inventory، شماره کامل کارت، شماره تماس یا متن کامل تیکت؛
- raw provider response بدون redaction.

`provider_payment_events.raw_payload_json`، فیش و customer-info attachment شواهد حساس‌اند؛ در dashboard/log عمومی چاپ نشوند، retention و دسترسی DB/backup شامل آن‌ها باشد و برای incident فقط حداقل داده لازم با redaction استخراج شود.

alert پیشنهادی: توقف process، تکرار 401/409 Telegram، رشد outbox queued/failed، خطای integrity، کمبود disk، failure backup، افزایش payment review و failure callback auth.

## پاسخ به رخداد

### افشای token

1. bot را برای جلوگیری از سوءاستفاده متوقف کنید.
2. token را در BotFather revoke و جایگزین کنید.
3. secret جدید را خارج Git نصب و service را restart کنید.
4. webhook را کنترل و خالی کنید.
5. history/log/artifact را برای محل افشا بررسی و credentialهای مرتبط را rotate کنید.

### جعل یا replay پرداخت

1. پرداخت و سفارش مرتبط را mutate نکنید؛ ابتدا snapshot/backup بگیرید.
2. `card_payment_events`, `payments`, `wallet_entries` و log reverse proxy را با reference بررسی کنید.
3. secret callback را rotate و source IP/TLS را کنترل کنید.
4. correction مالی فقط با entry جبرانی و audit note انجام شود.
5. regression test از payload واقعیِ پاک‌سازی‌شده بسازید.

### احتمال نشت inventory/backup

1. دسترسی فایل و سرویس را قطع و snapshot forensic بگیرید.
2. حساب‌های تحویلی تحت تأثیر را revoke/rotate کنید.
3. کاربران و مالک محصول را طبق سیاست incident مطلع کنید.
4. backupهای توزیع‌شده، log و chat ارسال فایل را شناسایی و retention را اجرا کنید.

## checklist بازبینی امنیتی تغییر

- آیا ورودی untrusted normalize و validate شده است؟
- آیا authorization و ownership در لحظه mutation کنترل می‌شود؟
- آیا replay/collision/stale state تست دارد؟
- آیا transaction failure حالت نیمه‌کاره می‌سازد؟
- آیا متن، HTML، URL، CSV و log escape/redact می‌شوند؟
- آیا secret یا PII غیرضروری/تأییدنشده وارد test fixture یا commit نشده است؟ منابع provenance که با تصمیم صریح مالک byte-for-byte نگه‌داری می‌شوند باید جداگانه privacy review و محدودیت بازنشر داشته باشند.
- آیا delivery/payment/reward پس از crash قابل reconciliation است؟
- آیا backup و rollback این تغییر تعریف شده است؟
