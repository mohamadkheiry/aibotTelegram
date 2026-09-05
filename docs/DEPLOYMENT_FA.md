# راهنمای استقرار، پرداخت و نگهداری

این راهنما نحوه نصب را توضیح می‌دهد؛ انجام این مراحل بر عهده اپراتور سرور است و وجود این فایل به معنی میزبانی‌شدن فعلی ربات نیست.

> **مرجع اجرایی قطعی:** برای هر نصب، update یا rollback از [deployment.md](deployment.md) استفاده کنید. نمونه‌های این سند توضیح تکمیلی‌اند و بدون backup معتبر، `--migrate-only`، `--check` و health check نباید مستقیماً روی production اجرا شوند.

## ۱. انتخاب محل اجرا

### توسعه روی کامپیوتر شخصی

برای آزمایش مناسب است، اما برای سرویس ۲۴ساعته توصیه نمی‌شود. sleep، قطع اینترنت، تغییر IP و خاموش‌شدن دستگاه polling و callback پرداخت را متوقف می‌کند.

### هاست اشتراکی

فقط اگر اجرای دائمی process پایتون، worker پس‌زمینه، اتصال خروجی طولانی و SQLite پایدار را صریحاً پشتیبانی کند. بیشتر هاست‌های اشتراکی process طولانی را kill می‌کنند؛ پیش از خرید سؤال کنید.

### VPS کوچک Linux؛ پیشنهاد معمول

برای شروع یک VPS با مشخصات تقریبی زیر کافی است:

- ۱ vCPU
- ۵۱۲ مگابایت تا ۱ گیگابایت RAM
- ۵ تا ۱۰ گیگابایت دیسک SSD
- Ubuntu LTS یا Debian پایدار
- Python 3.12 یا جدیدتر، همراه ماژول `venv`
- snapshot/backup و IP ثابت

قیمت و کیفیت ارائه‌دهندگان مرتب تغییر می‌کند؛ منطقه‌ای را انتخاب کنید که از داخل آن دسترسی خروجی پایدار به Telegram و سرویس پرداخت انتخابی برقرار باشد. پیش از خرید، قوانین، data residency، امکان backup، محدودیت پورت و دسترسی سرویس پرداخت را بررسی کنید.

### PaaS یا container host

باید worker همیشه‌روشن، persistent disk و در صورت استفاده از MacroDroid ورودی HTTPS را پشتیبانی کند. scale را روی یک replica نگه دارید. سرویس‌های با filesystem موقت برای SQLite مناسب نیستند مگر volume پایدار متصل شود.

## ۲. مدل شبکه

- Telegram updateها از طریق [`getUpdates`](https://core.telegram.org/bots/api#getupdates) و long polling دریافت می‌شوند.
- برای Telegram هیچ پورت ورودی یا webhook لازم نیست.
- فقط یک instance با این bot token اجرا شود.
- برنامه هنگام initialize، webhook قدیمی را با `deleteWebhook` و `drop_pending_updates=False` حذف می‌کند؛ webhook و `getUpdates` هم‌زمان کار نمی‌کنند. offset فقط پس از ACK handler در SQLite ذخیره می‌شود. فقط بازگشت `False` صریح، برای خطای موقت پایهٔ دیتابیس، NACK است: update جاری و موارد بعدی همان batch بدون advance offset می‌مانند و از همان offset با backoff نمایی سقف‌دار و stop-aware retry می‌شوند. `None`، خطای terminal دامنه و failure پاسخ Telegram ACK هستند تا update مسموم صف را نبندد. مرگ process پیش از ذخیره offset نیز می‌تواند همان update را replay کند.
- callback کارت بانکی، در صورت فعال‌بودن، یک HTTP endpoint جدا است و Telegram webhook محسوب نمی‌شود.
- اگر callback کارت غیرفعال است و Plisio نیز صرفاً poll می‌شود، می‌توان همه پورت‌های ورودی برنامه را بسته نگه داشت.

## ۳. آماده‌سازی امن سرور

1. سیستم‌عامل و بسته‌ها را به‌روز کنید.
2. login مستقیم root و احراز هویت SSH با رمز را در صورت امکان ببندید؛ از SSH key استفاده کنید.
3. یک کاربر سرویس بدون shell به نام `alonebot` بسازید.
4. کد را در `/opt/alone-account-bot` و داده را در `/var/lib/alone-account-bot` نگه دارید.
5. secretها را در `/etc/alone-account-bot.env` با مالک `root:alonebot` و mode برابر `0640` قرار دهید تا root بتواند آن را مدیریت کند و user سرویس فقط بخواند؛ برای جداسازی قوی‌تر می‌توان credential injection در systemd را جایگزین کرد.
6. firewall فقط SSH و در صورت نیاز 80/443 reverse proxy را باز کند. پورت 8787 را عمومی نکنید.
7. ساعت سیستم و NTP را فعال کنید؛ انقضای سفارش و زمان پرداخت به ساعت درست وابسته است.

نمونه secretها:

```dotenv
BOT_TOKEN=PASTE_A_NEW_BOTFATHER_TOKEN_HERE
BOOTSTRAP_ADMIN_USERNAME=mohammadrezakheiry
BOOTSTRAP_ADMIN_CHAT_ID=123456789
DATA_DIR=/var/lib/alone-account-bot
DATABASE_PATH=/var/lib/alone-account-bot/alone_account.sqlite3
TIMEZONE=Asia/Tehran
CURRENCY_LABEL=تومان
TELEGRAM_API_BASE=https://api.telegram.org
PAYMENT_CALLBACK_BIND=127.0.0.1
PAYMENT_CALLBACK_PORT=8787
PAYMENT_CALLBACK_SECRET=
PLISIO_API_KEY=
```

مقدار خالی callback را غیرفعال نگه می‌دارد. برای فعال‌سازی، `python -c "import secrets; print(secrets.token_urlsafe(32))"` یک secret سازگار ۴۳ کاراکتری می‌سازد؛ خروجی را مستقیم در secret store قرار دهید. هر مقدار غیرخالی باید ۴۳ تا ۱۲۸ نویسه URL-safe از `[A-Za-z0-9_-]` باشد. هیچ مقدار واقعی را داخل unit file یا repository قرار ندهید. واحد مالی این release فقط `TOMAN` است؛ `CURRENCY_LABEL` فقط برچسب نمایش و مقدار production آن «تومان» است. هر URL بیرونی قابل کلیک، قوانین محصول، invoice provider یا callback عمومی باید HTTPS مطلق، بدون credential، `localhost`، IP literal محلی/خصوصی/reserved یا host عددی مبهم باشد؛ validator DNS lookup نمی‌کند و لینک کانال/invite Telegram قید canonical سخت‌گیرانه‌تری دارد. `TELEGRAM_API_BASE` مقصد غیر-loopback را فقط با HTTPS می‌پذیرد؛ HTTP تنها برای `localhost`/زیردامنه آن یا IP loopback، و همیشه بدون credential/query/fragment، مجاز است.

## ۴. نصب مستقیم با systemd

نمونه روند نصب روی Debian/Ubuntu است. پیش از اجرای block زیر، release بازبینی‌شده را در `/opt/alone-account-bot` کپی/checkout کنید تا `requirements.txt` و package `app` حاضر باشند؛ clone شناور branch را مستقیماً به production نبرید:

```bash
sudo useradd --system --home-dir /opt/alone-account-bot --shell /usr/sbin/nologin alonebot
sudo install -d -o root -g root -m 0755 /opt/alone-account-bot
sudo install -d -o alonebot -g alonebot -m 0700 /var/lib/alone-account-bot
sudo install -d -o alonebot -g alonebot -m 0700 /srv/backups/alone-account-bot
sudo python3.12 -m venv /opt/alone-account-bot/.venv
sudo /opt/alone-account-bot/.venv/bin/python -m pip install --upgrade pip
sudo /opt/alone-account-bot/.venv/bin/python -m pip install -r /opt/alone-account-bot/requirements.txt
```

کد پروژه باید برای کاربر سرویس خواندنی باشد. سپس:

```bash
sudo cp /opt/alone-account-bot/alone-account-bot.service.example /etc/systemd/system/alone-account-bot.service
sudo systemctl daemon-reload
cd /opt/alone-account-bot
sudo -u alonebot ./.venv/bin/python -m app.main \
  --env-file /etc/alone-account-bot.env --migrate-only
sudo -u alonebot ./.venv/bin/python -m app.main \
  --env-file /etc/alone-account-bot.env --check
sudo systemctl enable --now alone-account-bot
sudo systemctl status alone-account-bot
```

`--check` فقط token را نمی‌سنجد: schema را تا نسخه ۱۱ initialize/migrate، owner bootstrap را با marker یکتای root و private chat/user ID پایدار روی همان DB ایجاد/اعتبارسنجی و سپس `getMe` را اجرا می‌کند. پس از verify، username صرفاً metadata است؛ restart مالک غیرفعال را فعال نمی‌کند و تغییر configured chat ID فقط به owner فعال و verifyشده marker را منتقل می‌کند. تعارض legacy/identity پیش از تماس Telegram fail closed است؛ بنابراین آن را بعد از backup و با identity نهایی release اجرا کنید.

روی دیتابیس تازه، wallet فعال، crypto خاموش و card تا تکمیل `/set_card` در UI مخفی است. flag ذخیره‌شده به‌تنهایی availability نیست: `/payment card on` بدون شماره+صاحب حساب و `/payment crypto on` بدون `PLISIO_API_KEY` باید fail closed بماند. پس از start، مالک visibility واقعی سه روش را با حساب آزمایشی کنترل کند.

مشاهده لاگ:

```bash
sudo journalctl -u alone-account-bot -f
```

فایل نمونه systemd به‌صورت non-root اجرا می‌شود، دسترسی نوشتن را به state directory محدود می‌کند و پس از خطا restart می‌شود.

## ۵. نصب با Docker Compose

در ریشه پروژه `.env` را با mode محدود بسازید و سپس:

```bash
docker compose build --pull
docker compose up -d
docker compose ps
docker compose logs -f bot
```

ویژگی‌های Compose تحویلی:

- یک replica.
- اجرای image با user غیر root.
- root filesystem خواندنی و named volume نوشتنی `bot-data:/app/data` با ownership مناسب user غیر root.
- drop همه Linux capabilityها و `no-new-privileges`.
- انتشار callback فقط روی `127.0.0.1` میزبان.
- restart با سیاست `unless-stopped`.
- مهلت توقف ۷۵ ثانیه است تا poller و worker non-daemon، و در صورت فعال‌بودن callback، listener/requestهای non-daemon آن پس از request جاری drain و بدون thread باقی‌مانده بسته شوند.

دایرکتوری build context ممکن است به Docker daemon ارسال شود؛ `.env` را هرگز داخل image کپی نکنید و از daemon یا remote builder غیرقابل اعتماد استفاده نکنید. Dockerfile فقط `app` و `requirements.txt` را داخل image کپی می‌کند. Compose برای driver `json-file` سقف `10m` و پنج فایل تعیین کرده است؛ پس از استقرار اعمال‌شدن آن را با `docker inspect` تأیید کنید.

## ۶. HTTPS برای callback کارت‌به‌کارت

حالت امن پیشنهادی:

```text
MacroDroid روی موبایل
        |
        | HTTPS + secret header
        v
Reverse proxy روی 443
        |
        | loopback
        v
127.0.0.1:8787
```

نمونه حداقلی Caddy، پس از اتصال DNS دامنه:

```caddyfile
payments.example.com {
    @payment_routes path /payments/card/confirm /health
    handle @payment_routes {
        reverse_proxy 127.0.0.1:8787
    }
    respond 404
}
```

Caddy برای دامنه معتبر HTTPS را مدیریت می‌کند. firewall باید 8787 را بسته نگه دارد. در reverse proxy می‌توان rate limit، محدودیت IP در صورت ثابت‌بودن IP موبایل و حداکثر بدنه 4 KiB را نیز اعمال کرد. secret header باید بدون تغییر به upstream برسد.

اگر Docker استفاده می‌شود، Compose پورت container را روی `127.0.0.1:${PAYMENT_CALLBACK_PORT:-8787}` میزبان قرار می‌دهد و reverse proxy میزبان به همان آدرس وصل می‌شود.

## ۷. API callback بانک

فعال‌سازی فقط با secret غیرخالی:

ابتدا secret را با مولد رمزنگارانه بالا بسازید و از secret store در env قرار دهید؛ placeholder فارسی یا عبارت قابل حدس به علت قرارداد ۴۳ تا ۱۲۸ نویسه URL-safe در startup رد می‌شود.

```dotenv
PAYMENT_CALLBACK_SECRET=
PAYMENT_CALLBACK_BIND=127.0.0.1
PAYMENT_CALLBACK_PORT=8787
```

احراز هویت هر دو endpoint با یکی از این روش‌ها الزامی است:

```http
X-Payment-Secret: <secret>
```

یا:

```http
Authorization: Bearer <secret>
```

مقایسه secret در برنامه به‌صورت constant-time انجام می‌شود. secret را دوره‌ای rotate کنید و در لاگ MacroDroid نمایش ندهید.

### بررسی سلامت

```http
GET /health
X-Payment-Secret: <secret>
```

پاسخ موفق:

```json
{"status":"ok"}
```

### تأیید مبلغ

```http
POST /payments/card/confirm
Content-Type: application/json
X-Payment-Secret: <secret>

{"amount":1250430,"reference":"sms-20260904-001","occurred_at":"2026-09-04T20:40:00+03:30"}
```

قواعد:

- `amount` اجباری، عدد صحیح مثبت و بدون comma/واحد پول است.
- `reference` اجباری است و باید یک مقدار پایدار و یکتا برای همان پیامک/تراکنش بانکی باشد. در retry همان رخداد، دقیقاً همان reference را دوباره بفرستید.
- `occurred_at` اجباری است و باید زمان واقعی رخداد بانکی را به قالب ISO-8601 همراه timezone بفرستد؛ برای مثال `2026-09-04T20:40:00+03:30` یا معادل UTC با `Z`. مقدار بدون offset مانند `2026-09-04T20:40:00` رد می‌شود.
- field ناشناخته، JSON تکراری/نامعتبر، body فشرده و body بیش از 4096 بایت رد می‌شود.
- مبلغ باید دقیقاً همان مبلغ شناسه‌دار نشان‌داده‌شده توسط ربات باشد؛ مبلغ را round نکنید.

پاسخ‌های routeها:

| HTTP | status JSON | معنی |
|---|---|---|
| 200 | `confirmed` | پرداخت تأیید شد |
| 200 | `already_confirmed` | retry همان تأیید؛ اقدام دیگری لازم نیست |
| 200 | `ok` | health احراز‌شده سالم است |
| 400 | `error` | path/JSON/field/length نامعتبر، body خالی یا ناقص، یا transfer encoding پشتیبانی‌نشده |
| 401 | `error` با `error=unauthorised` | secret اشتباه، مفقود یا مبهم |
| 404 | `not_found` یا `error` با `error=route_not_found` | پرداخت باز منطبق پیدا نشد یا route ناشناخته است |
| 405 | `error` با `error=method_not_allowed` | method route اشتباه است؛ header `Allow` method درست را می‌دهد |
| 408 | `error` با `error=request_timeout` | خواندن body از timeout گذشت |
| 409 | `conflict` | پرداخت پیدا شد ولی وضعیت/مرجع با تأیید تعارض دارد |
| 411 | `error` با `error=length_required` | دقیقاً یک `Content-Length` لازم است |
| 413 | `error` با `error=body_too_large` | body از 4096 بایت بزرگ‌تر است |
| 415 | `error` | media type یا content encoding پشتیبانی نمی‌شود |
| 500 | `error` با `error=internal_error` | callback داخلی شکست خورد؛ payload/secret در پاسخ بازتاب داده نمی‌شود |

روی 404 یا 409 retry نامحدود نکنید. ابتدا مبلغ، مهلت ۳۰دقیقه‌ای و وجود سفارش باز را بررسی کنید.

## ۸. تنظیم MacroDroid

نام گزینه‌ها ممکن است بین نسخه‌های MacroDroid فرق کند، اما جریان امن چنین است:

1. Trigger را فقط روی SMS/notification برنامه بانک و sender معتبر محدود کنید.
2. متن را با pattern مشخص همان بانک parse و مبلغ، reference یکتا و زمان واقعی رخداد را استخراج کنید.
3. ارقام فارسی/عربی، جداکننده هزارگان و واحد ریال/تومان را پیش از ارسال به integer یکسان تبدیل کنید.
4. Action از نوع HTTP Request با متد POST و URL عمومی HTTPS بسازید.
5. headerهای `Content-Type: application/json` و `X-Payment-Secret` را اضافه کنید.
6. زمان رخداد را با offset منطقه زمانی دستگاه به ISO-8601 تبدیل کنید و body را با هر سه فیلد اجباری `amount`، `reference` و `occurred_at` مطابق نمونه بخش قبل بسازید.
7. reference ثابت همان SMS را در retry دوباره استفاده کنید؛ 200 شامل `already_confirmed` رفتار idempotent است.
8. ثبت body/header در notification یا log عمومی MacroDroid را خاموش کنید.
9. ابتدا با سفارش کم‌مبلغ آزمایشی، سناریوهای موفق، مبلغ اشتباه، retry و انقضا را تست کنید.

به sender text به‌تنهایی اعتماد نکنید؛ اگر سیستم‌عامل اجازه می‌دهد package/برنامه مبدا را نیز محدود کنید. callback نباید موجودی را صرفاً از متن دلخواه کاربر افزایش دهد؛ برنامه آن را فقط با پرداخت باز و مبلغ منطبق تطبیق می‌دهد.

## ۹. تنظیم Plisio

کد شامل adapter اختیاری Plisio برای ساخت invoice و بررسی operation است. Telegram همچنان با `getUpdates` کار می‌کند و Plisio جای webhook تلگرام نیست.

پیش از فعال‌سازی حتماً این موارد را مستقیماً از ارائه‌دهنده بررسی کنید:

- امکان ثبت‌نام و ارائه سرویس به کشور/شخصیت حقوقی شما.
- نیازهای KYC، تحریم، مالیات و شرایط استفاده.
- پشتیبانی فعلی از source currency و cryptocurrency/network انتخابی.
- کارمزد، حداقل مبلغ، زمان انقضا و سیاست refund.

وجود adapter در سورس به معنی تضمین eligibility یا قابل‌استفاده‌بودن سرویس در محل شما نیست. مرجع فنی: [مستندات رسمی Plisio](https://plisio.net/documentation).

پس از گرفتن API key:

```dotenv
PLISIO_API_KEY=YOUR_PRIVATE_PROVIDER_KEY
PLISIO_CURRENCY=USDT_TRX
PLISIO_SOURCE_CURRENCY=IRR
PLISIO_AMOUNT_MULTIPLIER=10
PUBLIC_PAYMENT_CALLBACK_URL=
```

- اگر قیمت فروشگاه تومان و provider مبلغ مبدا را ریال می‌گیرد، multiplier برابر 10 تبدیل تومان به ریال است. این فرض را با invoice آزمایشی و مستندات روز provider تأیید کنید.
- اگر واحد داخلی و provider یکی است، multiplier معمولاً 1 است.
- chain را اشتباه انتخاب نکنید؛ برای نمونه `USDT_TRX` با USDT روی شبکه‌های دیگر یکسان نیست.
- کلید API فقط secret سرور است و نباید به Telegram، کاربر یا MacroDroid فرستاده شود.
- URL invoice برگشتی provider فقط وقتی ذخیره/نمایش می‌شود که HTTPS مطلق و بدون credential/host literal محلی یا خصوصی باشد؛ URL دارای HTTP، credential، `localhost`، IP literal خصوصی/reserved یا قالب مبهم را دور نزنید. validator DNS را resolve نمی‌کند.
- حالت این نسخه با polling وضعیت operation کار می‌کند. `PUBLIC_PAYMENT_CALLBACK_URL` را خالی بگذارید مگر اینکه endpoint callback سازگار با Plisio را جداگانه پیاده‌سازی و پشت HTTPS آزمایش کرده باشید. endpoint کارت‌به‌کارت مقصد callback Plisio نیست.

پس از تنظیم، از پنل مدیر:

```text
/payment crypto on
```

وجود `PLISIO_API_KEY` به‌تنهایی دکمه ارز را ظاهر نمی‌کند؛ این فرمان enable صریح لازم است. برعکس، روی نصب تازه card flag اولیه فعال است اما تا ثبت هر دو مقدار `/set_card` مخفی می‌ماند؛ اگر قرار نیست کارت عرضه شود، `/payment card off` را ثبت کنید.

با مبلغ کم، invoice، مبلغ تبدیل‌شده، شبکه، انقضا، وضعیت completed و تحویل سفارش را end-to-end تست کنید.

poller هر مشاهده پولی را پیش از settlement در ledger immutable provider ثبت می‌کند. `completed` فقط با `id` دقیق، `type=invoice` و terms اختیاری معتبر تسویه می‌شود؛ `operation.amount` مبلغ crypto است و با تومان مقایسه نمی‌شود. `params.source_amount/source_currency/currency` فقط اگر حاضر باشند بررسی می‌شوند؛ نبودشان completion را باطل نمی‌کند، اما نوع نامعتبر `params` یا mismatch هر فیلد حاضر review است. terminal با مبلغ دریافتی crypto صریحاً صفر failure قطعی است. partial/nonzero، مبلغ نامعلوم یا پاسخ malformed/mismatch به `verifying` و صف review می‌رود و اعتبار خودکار نمی‌سازد. poll حتی بعد از deadline محلی تا نتیجه terminal provider ادامه دارد؛ خاموشی سرویس میان ثبت شاهد completed و settlement در اجرای بعدی از همان شاهد و بدون network بازیابی می‌شود. review باز را از `/crypto_reviews` ببینید و فقط طبق [راهنمای مدیر](ADMIN_GUIDE_FA.md) و با شاهد مستقل تعیین تکلیف کنید؛ status/SQL دستی جای سند پرداخت یا بازپرداخت نیست.

### مهلت‌ها و محدودیت‌های عملیاتی پرداخت

- مهلت پایه سفارش و intent کارت ۳۰ دقیقه است؛ deadline محلی invoice ارزی آن را terminal نمی‌کند و نتیجه provider مبناست.
- فیش فقط برای payment کارت است: نخستین فیش strictly پیش از `expires_at` پذیرفته می‌شود؛ سپس تا تصمیم صریح مدیر در verifying قابل بررسی و جایگزینی می‌ماند و خودکار منقضی نمی‌شود. payment دارای فیش با دکمه قدیمی کاربر لغو نمی‌شود. پارامترهای داخلی قدیمی grace فقط سازگاری API هستند و سقف خودکار انتظار مدیر ایجاد نمی‌کنند.
- هر کاربر حداکثر ۱۰ سفارش پرداخت‌نشده با وضعیت `pending_payment` یا `awaiting_confirmation` می‌تواند داشته باشد.
- هر کاربر برای هر روش پرداخت حداکثر ۵ پرداخت فعال با وضعیت `pending` یا `verifying` می‌تواند داشته باشد.
- هر سفارش در مجموع روش‌های card/crypto فقط یک external intent فعال دارد. لغو card بدون فیش سفارش را terminal می‌کند و تغییر روش به سفارش تازه نیاز دارد؛ invoice crypto صادرشده محلی لغو نمی‌شود، deadline محلی آن را terminal نمی‌کند و provider evidence تا نتیجه قطعی یا late transition پایش می‌شود.
- Order/topup crypto باز باید در جزئیات/کیف پول با URL ذخیره‌شده امن و دکمه «ادامه پرداخت ارزی» قابل resume باشد؛ دکمه «ارسال فیش» فقط card است. URL legacy خالی یا ناامن باید بدون لینک و با مسیر پشتیبانی fail closed شود.
- مبلغ terminal کارت تا ۲۴ ساعت پس از `max(expires_at, updated_at)` quarantine است؛ این window حفاظتی را با تغییر مستقیم DB دور نزنید.
- برای شارژ کیف پول، در مجموع card/crypto فقط یک درخواست تازه برای هر کاربر فعال می‌شود. replay تنها با همان روش، مبلغ و terms همان رکورد را برمی‌گرداند؛ اختلاف هرکدام conflict است و هیچ topup به‌طور ضمنی جایگزین یا لغو نمی‌شود. مورد فعال باید ابتدا از مسیر واقعی خودش terminal شود. پس از migration، اگر داده legacy دو روش فعال داشت، کیف پول باید هر دو را جدا برای resume نشان دهد؛ این وضعیت را با SQL حذف نکنید و انتظار ساخت intent دوم هم نداشته باشید.
- یکتاسازی مبلغ payable فقط برای card است؛ crypto با invoice/reference provider تطبیق می‌یابد و مبلغ provider برای ساخت suffix مصنوعی تغییر نمی‌کند.
- هر user حداکثر ۲۰ intent کارت در ۲۴ ساعت می‌سازد و پس از ۳ لغو کارت در یک ساعت ساخت intent تازه تا پایان cooldown مسدود می‌شود. این رخدادها audit و به مدیران مجاز هشدار داده می‌شوند؛ برای دورزدن آن رکورد payment/cancellation را ویرایش یا حذف نکنید.
- payment در `paid` terminal است و این release workflow refund payment/topup ندارد؛ بازپرداخت را با تغییر دستی status یا SQL شبیه‌سازی نکنید.

### مبدا مدت اشتراک

مدت اشتراک هنگام پرداخت شروع نمی‌شود. برای محصول آماده، مبدا زمان تخصیص موجودی و تحویل اطلاعات است؛ برای محصول دستی، مبدا زمان تکمیل سفارش توسط مدیر است. سفارش رزروشده تا زمان تحویل از مدت اشتراک مصرف نمی‌کند و یادآوری‌های پایان اشتراک نیز بر اساس همین زمان تحویل زمان‌بندی می‌شوند.

## ۱۰. بکاپ امن

داده اصلی در `DATABASE_PATH` و فایل‌های تولیدی در `DATA_DIR` هستند. دیتابیس SQLite از WAL استفاده می‌کند.

روش پیشنهادی اول، فرمان مالک است:

```text
/backup
```

برای backup سازگار از API آنلاین خود SQLite استفاده کنید؛ خروجی شامل snapshot و hash ثبت‌شده است. `umask 077` از ایجاد فایل خواندنی برای دیگر کاربران جلوگیری می‌کند:

systemd:

```bash
cd /opt/alone-account-bot
sudo -u alonebot sh -c 'umask 077; exec "$@"' sh \
  ./.venv/bin/python -c \
  'from app.db import Database; print(Database("/var/lib/alone-account-bot/alone_account.sqlite3").create_backup("/srv/backups/alone-account-bot")["path"])'
```

Docker:

```bash
BACKUP_PATH=$(docker compose exec -T bot python -c \
  'from app.db import Database; print(Database("/app/data/alone_account.sqlite3").create_backup("/app/data/backups")["path"])')
CONTAINER_ID=$(docker compose ps -q bot)
BACKUP_NAME=$(basename "$BACKUP_PATH")
sudo install -d -o root -g root -m 0700 /srv/backups/alone-account-bot
umask 077
sudo docker cp "$CONTAINER_ID:$BACKUP_PATH" "/srv/backups/alone-account-bot/$BACKUP_NAME"
sudo chmod 0600 "/srv/backups/alone-account-bot/$BACKUP_NAME"
```

Compose از named volume به نام `bot-data` استفاده می‌کند. فرمان‌های `docker compose down -v` و `docker volume rm` دیتابیس را حذف می‌کنند و در update عادی ممنوع‌اند.

قواعد:

- بکاپ را با رمزگذاری در مقصد جدا از VPS نگه دارید.
- access key بکاپ را روی همان سرور و کنار archive نگذارید.
- retention روزانه/هفتگی تعریف و فضای دیسک را monitor کنید.
- حداقل ماهی یک بار restore پایه را روی مسیر ایزوله، بدون poller/worker و بدون token، با `integrity_check` و `foreign_key_check` امتحان کنید. برای آزمون bot روی داده restoreشده، ابتدا مجوز و anonymization لازم است و فقط token آزمایشی مجاز است.
- پس از انتقال موفق، permission فایل backup باید `0600` و directory آن `0700` باشد.
- هنگام اجرای زنده، تنها فایل `.sqlite3` را با `cp` برندارید؛ ممکن است داده جدید در `-wal` باشد.

## ۱۱. به‌روزرسانی و rollback

قبل از هر update:

1. release فعلی و `.env` را مشخص کنید.
2. بکاپ سازگار دیتابیس بگیرید.
3. release جدید را در مسیر جدا extract کنید.
4. dependencyها را نصب و تست‌ها را اجرا کنید.
5. فقط یک فرایند را stop و نسخه جدید را start کنید.
6. `/health`، `/start`، یک سفارش آزمایشی و لاگ را بررسی کنید.
7. release قبلی را تا پایان تأیید نگه دارید.

تست:

```bash
python -m unittest discover -s tests -v
```

systemd:

```bash
sudo systemctl stop alone-account-bot
# پیش از ادامه، backup سازگار و integrity آن را طبق deployment.md تأیید کنید.
sudo /opt/alone-account-bot/.venv/bin/python -m pip install -r /opt/alone-account-bot/requirements.txt
cd /opt/alone-account-bot
sudo -u alonebot ./.venv/bin/python -m app.main \
  --env-file /etc/alone-account-bot.env --migrate-only
sudo -u alonebot ./.venv/bin/python -m app.main \
  --env-file /etc/alone-account-bot.env --check
sudo systemctl start alone-account-bot
sudo systemctl status alone-account-bot
```

Docker:

```bash
docker compose stop bot
# پیش از build، backup سازگار و integrity آن را طبق deployment.md تأیید کنید.
docker compose build --pull
docker compose run --rm bot python -m app.main --migrate-only
docker compose run --rm bot python -m app.main --check
docker compose up -d
docker compose logs --tail=200 bot
```

اگر migration یا رفتار نسخه جدید مشکل داشت، سرویس را متوقف، کد release قبلی و در صورت تغییر ناسازگار schema بکاپ قبل از update را restore کنید. هیچ rollback دیتابیس را روی فرایند در حال اجرا انجام ندهید.

## ۱۲. پایش و رفع اشکال

### ربات update نمی‌گیرد

- webhook قدیمی را با `getWebhookInfo` بررسی کنید.
- مطمئن شوید نمونه دیگری با همان token فعال نیست.
- اتصال DNS/HTTPS به `api.telegram.org` را از خود سرور بررسی کنید.
- token را در لاگ چاپ نکنید؛ در صورت شک آن را rotate کنید.

### جوین اجباری همیشه رد می‌شود

- username/chat ID کانال را بررسی کنید.
- ربات را با دسترسی لازم در کانال قرار دهید.
- وضعیت عضویت را با حساب غیرمدیر آزمایش کنید.

### callback پاسخ 401 می‌دهد

- secret سرور و MacroDroid باید byte-for-byte یکسان باشد.
- reverse proxy باید header را عبور دهد.
- whitespace ابتدا/انتها و Bearer ناقص را حذف کنید.

### callback پاسخ 404 یا 409 می‌دهد

- پرداخت باز، مبلغ دقیق، روش card، مهلت سفارش و reference تکراری را بررسی کنید.
- پرداخت را کورکورانه approve نکنید؛ ابتدا گزارش بانک را تطبیق دهید.

### خطای SQLite یا database locked

- فقط یک bot process اجرا کنید.
- مالکیت و permission پوشه data را بررسی کنید.
- دیتابیس را روی network filesystem نامطمئن قرار ندهید.
- فضای دیسک را بررسی کنید.

### رنگ یا آیکون دکمه فرق دارد

- client تلگرام را به‌روز کنید.
- style پیش‌فرض وابسته به client/theme است.
- custom icon نیازمند شرایط Premium/Fragment توضیح‌داده‌شده در README است.
