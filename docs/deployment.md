# راهنمای استقرار و بازگردانی

این سند runbook استقرار برای developer، operator و agent است. هدف، راه‌اندازی قابل تکرار ربات با long polling، یک نمونه فعال و SQLite پایدار است. برای فرمان‌های کسب‌وکاری و تنظیم داخل ربات به `ADMIN_GUIDE_FA.md` و برای جزئیات callback/Plisio به `DEPLOYMENT_FA.md` مراجعه کنید.

## قرارداد استقرار

- Python پشتیبانی‌شده: 3.12 یا جدیدتر.
- entry point: `python -m app.main`.
- دریافت Telegram update فقط از `getUpdates` است؛ برنامه هنگام شروع webhook قبلی را با `drop_pending_updates=False` حذف می‌کند.
- offset فقط پس از ACK handler ذخیره می‌شود؛ خطای موقت پایهٔ دیتابیس در update مدیریتی با NACK `False`، حفظ offset و retry همان update پیش از موارد بعدی batch بازیابی می‌شود. خطای terminal دامنه/Telegram ACK است تا صف قفل نشود.
- برای هر bot token دقیقاً یک process/replica فعال باشد.
- فایل SQLite روی disk محلی و پایدار باشد؛ NFS، filesystem موقت و volume چندنویسنده مناسب نیستند.
- اگر callback کارت فعال نیست، برنامه به هیچ پورت ورودی نیاز ندارد.
- callback کارت باید فقط روی loopback/private network bind و از reverse proxy دارای HTTPS منتشر شود.
- مهلت توقف orchestrator از timeout شبکه بیشتر باشد؛ artifactهای فعلی ۷۵ ثانیه grace در برابر `REQUEST_TIMEOUT_SECONDS=45` دارند.
- migration پیش از start و پس از یک بکاپ معتبر انجام شود.

## استقرار نسخه رابط دکمه‌ای

این نسخه schema جدید ندارد؛ schema 11، داده کاربران و سفارش‌ها، getUpdates تک‌نمونه‌ای و `BUTTON_COLOR_MODE` حفظ شده‌اند. بعد از backup و توقف نمونه قدیمی، کد جدید را با همان env/DB اجرا کنید. startup با `setMyCommands` فقط start را معرفی می‌کند؛ بقیه فرمان‌ها برای سازگاری همچنان قابل استفاده‌اند. مدیر تأییدشده پس از `/start` دکمه «پنل مدیریت» را می‌بیند. اگر صفحه قبلی پنل را نشان نمی‌دهد، یک‌بار شروع/منوی اصلی را باز کند؛ داشتن صفحه قدیمی مجوز تازه نمی‌سازد.

Smoke test بدون تراکنش واقعی: ورود مالک، مشاهده بخش‌های مجاز، جست‌وجوی کاربر، بازگشت/لغو فرم، فهرست محصولات با «همه»، و نبود پنل برای مشتری عادی. جزئیات در [BUTTON_UI.md](BUTTON_UI.md). rollback کد قبلی schema را تغییر نمی‌دهد ولی فرم `admin:ui` در نسخه قدیمی شناخته نیست؛ کاربر با `/start` آن را لغو و دوباره شروع کند. توکن یا DB را برای این تغییر در گیت قرار ندهید.

## فایل‌ها و داده‌های حساس

| دارایی | محل پیشنهادی systemd | محل Docker Compose | سیاست |
|---|---|---|---|
| سورس release | `/opt/alone-account-bot` | checkout پروژه | read-only برای user سرویس |
| env و secret | `/etc/alone-account-bot.env` | `.env` میزبان | خارج از Git، دسترسی حداقلی |
| دیتابیس | `/var/lib/alone-account-bot/alone_account.sqlite3` | named volume در `/app/data/alone_account.sqlite3` | disk پایدار و backupشده |
| بکاپ محلی | `/srv/backups/alone-account-bot` | ابتدا `/app/data/backups` داخل volume و سپس `docker cp` | رمزگذاری و کپی خارج سرور |
| log | journald | Docker logging driver | بدون token، payload و PII اضافی |

هر token یا API key که در chat، issue، log یا commit دیده شده است افشاشده محسوب می‌شود. پیش از production آن را در مبدأ صادرکننده revoke/rotate کنید؛ حذف آن از آخرین commit به‌تنهایی history را پاک نمی‌کند.

## ساخت فایل env بدون افشای secret

از `.env.example` کپی بگیرید و مقدارها را با editor امن یا secret manager وارد کنید. secret واقعی را در دستور shell، screenshot یا مستندات paste نکنید.

```dotenv
BOT_TOKEN=REPLACE_AT_DEPLOY_TIME
BOOTSTRAP_ADMIN_USERNAME=replace_with_owner_username
BOOTSTRAP_ADMIN_CHAT_ID=replace_with_numeric_chat_id

DATA_DIR=/var/lib/alone-account-bot
DATABASE_PATH=/var/lib/alone-account-bot/alone_account.sqlite3
TIMEZONE=Asia/Tehran
CURRENCY_LABEL=تومان
LOG_LEVEL=INFO
BUTTON_COLOR_MODE=colored

POLL_TIMEOUT_SECONDS=30
REQUEST_TIMEOUT_SECONDS=45
JOB_INTERVAL_SECONDS=10
ORDER_EXPIRY_MINUTES=30
RECEIPT_DELAY_SECONDS=60
TELEGRAM_API_BASE=https://api.telegram.org

PAYMENT_CALLBACK_BIND=127.0.0.1
PAYMENT_CALLBACK_PORT=8787
PAYMENT_CALLBACK_SECRET=
PUBLIC_PAYMENT_CALLBACK_URL=

PLISIO_API_KEY=
PLISIO_CURRENCY=USDT_TRX
PLISIO_SOURCE_CURRENCY=IRR
PLISIO_AMOUNT_MULTIPLIER=10
```

فیلدهای خالی integration را غیرفعال نگه می‌دارند. `PAYMENT_CALLBACK_SECRET` وجود HTTP listener کارت را فعال می‌کند و در حالت غیرخالی باید ۴۳ تا ۱۲۸ کاراکتر URL-safe از `[A-Za-z0-9_-]` باشد؛ برای تولید مقدار ۴۳ کاراکتری از `python -c "import secrets; print(secrets.token_urlsafe(32))"` استفاده و خروجی را مستقیم در secret store قرار دهید. `PLISIO_API_KEY` فقط client ارزی را در startup در دسترس می‌گذارد؛ نمایش crypto علاوه بر آن به `/payment crypto on` نیاز دارد. در دیتابیس تازه wallet فعال، crypto خاموش و card با وجود flag اولیه تا ثبت هم‌زمان شماره و صاحب حساب مخفی است؛ `/payment card on` بدون این دو و `/payment crypto on` بدون API key fail closed می‌شوند. واحد مالی این release فقط `TOMAN` است و `CURRENCY_LABEL=تومان` باید ثابت بماند. `PUBLIC_PAYMENT_CALLBACK_URL`، اگر پر شود، فقط URL مطلق HTTPS بدون credential، `localhost`، IP literal محلی/خصوصی/reserved یا host عددی مبهم می‌پذیرد؛ این validation DNS lookup نیست. `TELEGRAM_API_BASE` برای هر مقصد غیر-loopback باید HTTPS باشد؛ HTTP فقط برای `localhost`/زیردامنه آن یا IP loopback مجاز است و credential/query/fragment پذیرفته نمی‌شود.

## چک‌لیست preflight

`BUTTON_COLOR_MODE` فقط `theme` یا `colored` می‌پذیرد. پیش‌فرض `colored` رنگ‌های فعلی را حفظ می‌کند. fallback اختیاری `theme` برای ناسازگاری خوانایی کلاینت، رنگ اجباری دکمه را نمی‌فرستد؛ mode روی منوی اصلی، صفحه‌های فرعی، contact، edit و پیام‌های outbox اثر دارد. پس از تغییر env یک restart لازم است. برای مشاهده دکمه‌های تازه `/menu` یا `/start` ارسال کنید؛ پیام‌های تاریخی کاربر خودکار ویرایش نمی‌شوند. هر تغییر ظاهر باید در تم روشن/تاریک کلاینت‌های واقعی نیز بررسی شود.

قبل از هر استقرار یا update این موارد را ثبت کنید:

- commit/release دقیق و checksum artifact مشخص است.
- `git status` محیط build تمیز و artifact فاقد `.env`، SQLite، backup، log و cache است.
- کل مجموعه تست و compile روی همان commit موفق است.
- token تازه و chat ID مالک از secret store آماده‌اند.
- دسترسی خروجی DNS/HTTPS به `api.telegram.org` و در صورت نیاز provider برقرار است.
- clock و timezone سیستم درست و NTP فعال است.
- disk دیتابیس فضای آزاد کافی، مالکیت درست و backup خارج‌سرور دارد.
- هیچ process یا container دیگری با همان token فعال نیست.
- پورت callback روی اینترنت مستقیم منتشر نشده و TLS/reverse proxy آماده است.
- secret فعال callback طول ۴۳ تا ۱۲۸ و نویسه‌های URL-safe دارد؛ `TELEGRAM_API_BASE` غیر-loopback از HTTPS استفاده می‌کند.
- availability روش‌ها با مالک smoke شده است: wallet مطابق تصمیم محصول، card فقط پس از `/set_card` و crypto فقط پس از API key و enable صریح دیده می‌شود؛ روش ناقص مخفی است.
- backup قبل از migration ایجاد و با `integrity_check` آزمایش شده است.
- روش rollback کد و دیتابیس و مسئول تصمیم rollback مشخص است.

گیت کنترل کیفیت:

```bash
python -m pip install -r requirements-dev.txt
python -m unittest tests.test_repository_hygiene -v
python -m unittest discover -s tests -v
python -m compileall -q app tests
python -m ruff check .
```

`requirements-dev.txt` فقط در runner توسعه/CI یا مرحله validation نصب می‌شود؛ سرور production و image نهایی فقط `requirements.txt` را نیاز دارند.

## استقرار محلی برای توسعه یا smoke test

```bash
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
cp .env.example .env
# مقدارهای توسعه‌ای و token ربات آزمایشی را با editor وارد کنید.
chmod 600 .env
python -m app.main --env-file .env --migrate-only
python -m app.main --env-file .env --check
python -m app.main --env-file .env
```

در Windows PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
Copy-Item .env.example .env
python -m app.main --env-file .env --migrate-only
python -m app.main --env-file .env --check
python -m app.main --env-file .env
```

خروج کنترل‌شده با `Ctrl+C` انجام می‌شود. لپ‌تاپ یا session تعاملی برای سرویس ۲۴ساعته مناسب نیست.

## استقرار Linux با systemd

نمونه زیر Debian/Ubuntu و مسیرهای فایل service موجود در مخزن را فرض می‌کند. نام package نسخه Python ممکن است در توزیع متفاوت باشد.

### ۱. user و مسیرها

```bash
sudo useradd --system --home-dir /opt/alone-account-bot --shell /usr/sbin/nologin alonebot
sudo install -d -o root -g root -m 0755 /opt/alone-account-bot
sudo install -d -o alonebot -g alonebot -m 0700 /var/lib/alone-account-bot
sudo install -d -o alonebot -g alonebot -m 0700 /srv/backups/alone-account-bot
```

اگر user از قبل وجود دارد، خط اول را تکرار نکنید.

### ۲. دریافت release و ساخت venv

```bash
sudo git clone https://github.com/mohamadkheiry/aibotTelegram.git /opt/alone-account-bot
cd /opt/alone-account-bot
sudo git switch --detach RELEASE_COMMIT_SHA
sudo python3.12 -m venv .venv
sudo ./.venv/bin/python -m pip install --upgrade pip
sudo ./.venv/bin/python -m pip install -r requirements.txt
sudo chown -R root:root /opt/alone-account-bot
sudo chmod -R go-w /opt/alone-account-bot
```

در production به SHA یا tag بازبینی‌شده deploy کنید، نه به head متغیر یک branch.

### ۳. env و service

```bash
sudo install -o root -g alonebot -m 0640 /opt/alone-account-bot/.env.example /etc/alone-account-bot.env
sudoedit /etc/alone-account-bot.env
sudo install -o root -g root -m 0644 \
  /opt/alone-account-bot/alone-account-bot.service.example \
  /etc/systemd/system/alone-account-bot.service
sudo systemctl daemon-reload
```

در env مسیرهای `/var/lib/alone-account-bot` را نگه دارید. فایل service با `User=alonebot`، `UMask=0077`، filesystem محدود و restart پس از failure تنظیم شده است.

### ۴. migration و preflight زنده

سرویس قدیمی باید در این مرحله متوقف باشد:

```bash
sudo systemctl stop alone-account-bot 2>/dev/null || true
cd /opt/alone-account-bot
sudo -u alonebot ./.venv/bin/python -m app.main \
  --env-file /etc/alone-account-bot.env --migrate-only
sudo -u alonebot ./.venv/bin/python -m app.main \
  --env-file /etc/alone-account-bot.env --check
```

`--check` schema را تا نسخه ۱۱ initialize/migrate، owner bootstrap را با root marker و private chat/user ID پایدار روی همان DB ایجاد/اعتبارسنجی و سپس `getMe` را فراخوانی می‌کند. پس از verify، drift username همان chat metadata است؛ restart owner غیرفعال‌شده را فعال نمی‌کند و configured chat ID تازه فقط وقتی marker را منتقل می‌کند که مقصد از قبل owner فعال و verifyشده باشد. تعارض legacy/identity پیش از تماس Telegram fail closed است و token یا شبکه نامعتبر نیز preflight را fail می‌کند. این دستور polling را شروع نمی‌کند، اما read-only نیست؛ آن را بعد از backup و با identity نهایی release اجرا کنید.

### ۵. start و verification

```bash
sudo systemctl enable --now alone-account-bot
sudo systemctl is-active alone-account-bot
sudo systemctl status alone-account-bot --no-pager
sudo journalctl -u alone-account-bot -n 200 --no-pager
```

در log باید شروع long polling و نبود loop خطا یا NACK تکرارشونده دیده شود. سپس با حساب آزمایشی `/start`، منو، دسترسی جوین و یک جریان بدون پول واقعی را smoke test کنید. مالک `/admin_help`، یک صفحه بعدی `/users` یا `/orders` و visibility روش‌های پرداخت را بررسی کند؛ نصب تازه یا config ناقص نباید card/crypto قابل انتخاب نشان دهد.

### رفتار توقف کنترل‌شده

artifact سرویس `TimeoutStopSec=75s` دارد. با `REQUEST_TIMEOUT_SECONDS=45`، دریافت `SIGTERM` اجازه می‌دهد request جاری Telegram پایان یابد، اما poller retry/backoff دیگری آغاز نمی‌کند. worker نگه‌داری non-daemon است، در مرز آیتم بعدی متوقف و تا پایان آیتم جاری join می‌شود. listener و request threadهای callback نیز non-daemon هستند؛ listener بسته می‌شود و confirmation در حال اجرا تا سقف مهلت توقف drain می‌شود. timeout سرویس را کمتر از request timeout نگذارید؛ اگر `REQUEST_TIMEOUT_SECONDS` را بالاتر می‌برید، grace را نیز با حاشیه‌ای برای drain callback و بستن sessionها افزایش دهید.

برای آزمون release، هنگام یک long poll و نیز هنگام backlog ساختگی، `systemctl stop alone-account-bot` را اجرا کنید. فرمان باید پیش از grace تمام شود، process/thread باقی نماند و start بعدی بدون conflict `getUpdates` بالا بیاید. updateای که هنگام shutdown برگشته ولی dispatch نشده است باید پس از restart replay شود؛ offset آن نباید جلو رفته باشد.

## استقرار با Docker Compose

نیازمندی: Docker Engine و Compose plugin. Compose فعلی یک replica، root filesystem فقط‌خواندنی، user غیر root، capabilityهای حذف‌شده و named volume پایدار `bot-data` دارد. named volume باعث می‌شود ownership دایرکتوری ساخته‌شده در image برای user غیر root حفظ شود؛ آن را با bind mount بدون provision صریح UID/GID جایگزین نکنید.

Dockerfile فقط `app` و `requirements.txt` را داخل image کپی می‌کند. `.dockerignore` فعلی `.env`، دیتابیس، data، log، cache، test و docs را از build context حذف می‌کند؛ آن را در هر release بازبینی کنید. همچنان از daemon/remote builder غیرقابل اعتماد استفاده نکنید، زیرا نبودن secret در لایه نهایی image به‌تنهایی امنیت build context یا cache سازنده را ثابت نمی‌کند.

```bash
git clone https://github.com/mohamadkheiry/aibotTelegram.git
cd aibotTelegram
git switch --detach RELEASE_COMMIT_SHA
cp .env.example .env
# secretها را با editor/secret provisioner وارد کنید.
chmod 600 .env
docker compose build --pull
docker compose run --rm --no-deps bot python -m app.main --migrate-only
docker compose run --rm --no-deps bot python -m app.main --check
docker compose up -d --no-build
docker compose ps
docker compose logs --tail=200 bot
```

برای update، ابتدا container جاری را stop کنید تا هیچ `docker compose run` یا container جدید با poller قبلی هم‌زمان نشود. scale را هرگز بالاتر از یک قرار ندهید:

```bash
docker compose up -d --scale bot=1
```

Compose نیز `stop_grace_period: 75s` دارد. `docker compose kill` این قرارداد را دور می‌زند و فقط برای incidentی که توقف عادی از grace عبور کرده است مناسب است؛ در آن حالت claimهای پایدار در restart بازیابی می‌شوند و علت I/O گیرکرده باید بررسی شود.

Compose repository برای logging driver `json-file` محدودیت `max-size=10m` و `max-file=5` تعیین می‌کند؛ پس از استقرار آن را با `docker inspect` تأیید کنید. برای systemd نیز سقف نگه‌داری journald باید در policy میزبان مشخص باشد. تغییر یا حذف rotation می‌تواند disk دیتابیس را پر کند.

پورت Compose فقط روی `127.0.0.1` میزبان publish می‌شود. اگر callback غیرفعال است، حذف mapping پورت از override استقرار سطح حمله را کمتر می‌کند.

## reverse proxy و health callback کارت

listener داخلی دو route دارد:

- `GET /health`
- `POST /payments/card/confirm`

هر دو route به `X-Payment-Secret` یا `Authorization: Bearer ...` نیاز دارند. health عمومی و بدون authentication عمداً وجود ندارد. reverse proxy باید:

- TLS معتبر و redirect اجباری HTTP به HTTPS داشته باشد.
- فقط همین routeها و methodها را عبور دهد.
- header احراز هویت را بدون ثبت در access log منتقل کند.
- body size و rate limit سخت‌گیرانه داشته باشد.
- به `127.0.0.1:8787` یا private network وصل شود.

نمونه بررسی محلی با placeholder؛ مقدار production را از secret store تزریق کنید و در shell history ننویسید:

```bash
curl --fail --silent --show-error \
  -H 'X-Payment-Secret: VALUE_FROM_SECRET_STORE' \
  http://127.0.0.1:8787/health
```

پاسخ سالم `{"status":"ok"}` است. 401 یعنی credential نرسیده/غلط است؛ 404 یا 405 یعنی route/method اشتباه است. وجود پاسخ health، سلامت Telegram polling یا صحت پرداخت را ثابت نمی‌کند.

## بکاپ پیش از استقرار

روش ترجیحی بکاپ آنلاین، فرمان `/backup` توسط نقش `owner` یا API داخلی SQLite backup است. API داخلی snapshot سازگار، SHA-256 و metadata ثبت می‌کند.

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

پس از تأیید فایل منتقل‌شده، نسخه داخل volume را طبق retention پاک کنید. `docker compose down -v` و `docker volume rm` در مسیر عادی update/rollback ممنوع‌اند، چون کل دیتابیس و backupهای داخل volume را حذف می‌کنند.

بکاپ را به storage جدا، رمزگذاری‌شده و دارای retention منتقل کنید. فقط وجود فایل کافی نیست؛ آن را روی مسیر موقت باز کنید و نتیجه هر دو بررسی زیر باید سالم باشد:

```sql
PRAGMA integrity_check;
PRAGMA foreign_key_check;
```

`integrity_check` باید `ok` و `foreign_key_check` باید بدون ردیف باشد. هنگام اجرای زنده فایل `.sqlite3` را با `cp` تنها کپی نکنید؛ داده ممکن است در WAL باشد. برای کپی فایل‌سیستمی، سرویس را کامل stop و کل state directory را archive کنید.

## روند استاندارد release/update

ترتیب زیر را تغییر ندهید:

1. release SHA فعلی و مقصد، زمان شروع و operator را ثبت کنید.
2. تست artifact مقصد را خارج production اجرا کنید.
3. بکاپ آنلاین بگیرید، hash را ثبت و restore ایزوله integrity/schema را بدون اجرای poller یا token تأیید کنید؛ آزمون جریان روی داده production-derived فقط پس از مجوز و anonymization مجاز است.
4. ورودی‌های کسب‌وکاری حساس را موقتاً از پنل خاموش کنید، در صورت نیاز maintenance mode را با `/bot_off` فعال کنید.
5. تنها instance جاری را stop کنید.
6. مطمئن شوید process/poller دیگری باقی نمانده است.
7. کد همان release و dependencyهای مطابق بازه‌های ثبت‌شده آن را نصب کنید؛ نسخه‌های resolve‌شده را در گزارش استقرار نگه دارید.
8. `--migrate-only` و سپس `--check` را اجرا کنید.
9. فقط یک instance جدید را start کنید.
10. log، process، callback اختیاری، `/start`، `/admin_help` و یک smoke flow آزمایشی را بررسی کنید.
11. روش‌های پرداختی را که خاموش کرده‌اید پس از تأیید دوباره فعال کنید.
12. حداقل یک چرخه maintenance و رشد offset را پایش کنید و سپس release را موفق اعلام کنید.

برای systemd، start/stop فقط از همان unit انجام شود. برای Docker، project name و checkout یکتا نگه دارید تا دو Compose stack تصادفی با token یکسان بالا نیاید.

## rollback

rollback کد و rollback دیتابیس دو تصمیم جدا هستند. قبل از migration مشخص کنید release قبلی schema جدید را می‌خواند یا نه.

### rollback فقط کد

اگر migration کاملاً backward-compatible است:

1. service را stop کنید.
2. log و DB جاری را برای تحلیل حفظ کنید.
3. commit قبلی pin‌شده و dependencyهای آن را نصب کنید.
4. با DB جاری `--check` را اجرا کنید.
5. یک instance نسخه قبلی را start و smoke test کنید.

### rollback کد و دیتابیس

اگر schema/داده ناسازگار یا migration ناقص است:

1. service را stop و نبود process دیگر را تأیید کنید.
2. از DB خراب/جدید یک کپی forensic جدا بگیرید؛ آن را overwrite نکنید.
3. backup تأییدشده درست قبل از migration را در مسیر جدید extract کنید.
4. `integrity_check` و `foreign_key_check` را روی فایل restoreشده اجرا کنید.
5. مالکیت و permission فایل را به user سرویس برگردانید.
6. release قبلی را فعال کنید.
7. فقط پس از `--check` service را start کنید.
8. تراکنش‌های بین زمان backup و rollback را از گزارش بانک/provider و logها دستی reconcile کنید؛ هیچ credit یا order را حدس نزنید.

هیچ restore یا جایگزینی DB روی process در حال اجرا انجام نشود. فایل‌های `-wal` و `-shm` مربوط به DB دیگری را کنار restore قرار ندهید. rollback ناموفق را با چند بار start/stop پنهان نکنید؛ سرویس را متوقف نگه دارید و runbook incident را اجرا کنید.

## پایش پس از استقرار

حداقل سیگنال‌ها:

- process/container فعال و restart loop ندارد.
- log شامل خطای تکراری Telegram، SQLite، maintenance یا provider نیست.
- offset `telegram_update_offset` پس از update ACKشده افزایش می‌یابد؛ ثابت‌ماندن آن همراه NACK تکرارشونده علامت خطای DB است و نباید با advance دستی پنهان شود.
- backlog `outbound_messages` و `reminders` رشد بی‌پایان ندارد.
- پرداخت‌های `pending/verifying` و سفارش‌های `awaiting_confirmation` با سن غیرعادی alert می‌دهند.
- disk، inode و اندازه WAL پایش می‌شوند.
- آخرین بکاپ موفق، اندازه و hash قابل مشاهده‌اند.
- callback در صورت فعال‌بودن از مسیر داخلی و خارجی authenticated سالم است.
- expiry سفارش، تخصیص رزرو و اعلان پایان subscription در staging دوره‌ای smoke می‌شوند.

`/health` فقط وقتی callback کارت فعال است وجود دارد. در استقرار بدون callback، health اصلی ترکیبی از وضعیت process، log، اتصال `getMe` در preflight و smoke test Telegram است.

## امنیت production

- token BotFather، secret کارت و کلید Plisio را در secret manager نگه دارید و دوره‌ای rotate کنید.
- `.env` را از Git، image، backup عمومی و artifact CI حذف کنید.
- service را non-root و data/env را با permission حداقلی اجرا کنید.
- فقط SSH مدیریتی و reverse proxy لازم را در firewall باز کنید؛ پورت 8787 عمومی نباشد.
- TLS، rate limit و log redaction را در reverse proxy فعال کنید.
- دسترسی به DB و backup را دسترسی به شماره تماس، تاریخچه خرید و payload حساب‌های آماده تلقی کنید.
- backup خارج‌سرور را رمزگذاری و restore drill دوره‌ای اجرا کنید.
- release را با SHA بازبینی‌شده deploy کنید. dependencyهای runtime فعلاً در بازه‌های محدود نسخه و ابزار lint به‌صورت دقیق pin شده‌اند، اما image پایه با digest و کل زنجیره dependency با lockfile ثابت نشده است؛ بنابراین build فعلی best-effort و نه کاملاً reproducible است.
- مدیر غیرفعال یا مشکوک را فوراً disable کنید؛ نقش `owner` را حداقلی نگه دارید.
- پس از احتمال افشای token، ابتدا BotFather token را revoke کنید، سپس secret جدید را deploy و process را restart کنید.

## معیار پایان استقرار

استقرار فقط وقتی تمام است که commit و schema version ثبت شده، تست و migration موفق، دقیقاً یک poller فعال، smoke test موفق، backup قابل restore، مانیتورینگ سالم و rollback آماده باشد. «process روشن است» به‌تنهایی معیار موفقیت نیست.
