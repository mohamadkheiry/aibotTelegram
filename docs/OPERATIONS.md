# راهنمای عملیات، پشتیبانی و پاسخ به رخداد

برای عملیات روزانه پس از شروع، از «پنل مدیریت» و بخش مربوط استفاده کنید؛ [BUTTON_UI.md](BUTTON_UI.md) جایگزین نیاز به تایپ فرمان است. صفحه قدیمی فاقد دکمه پنل با شروع/منوی اصلی تازه می‌شود. «دکمه قدیمی» یعنی token/revision با فرم جاری فرق دارد، نه لزوماً قطع ربات؛ از آخرین پیام استفاده کنید. در نتیجه نامطمئن ابتدا وضعیت/تاریخچه را بخوانید. اجرای کور دوباره تأیید مالی مجاز نیست. در rollback به نسخه قبل، فرم ناشناخته admin:ui با /start لغو می‌شود.

این سند برای operator، تیم پشتیبانی و agentی است که باید سرویس فعال را بدون آسیب به سفارش، کیف پول یا موجودی نگهداری کند. راهنمای توسعه در `development.md`، استقرار/rollback در `deployment.md` و syntax کامل فرمان‌های مدیر در `ADMIN_GUIDE_FA.md` است.

## قواعد طلایی عملیات

برای گزارش دکمه هم‌رنگِ متن در Telegram Web، پیش‌فرض `BUTTON_COLOR_MODE=colored` رنگ‌های مرجع را حفظ می‌کند؛ اگر مشکل ادامه داشت fallback اختیاری `theme` رنگ اجباری را فقط در نسخه ارسالی حذف می‌کند. پس از تغییر env یک process را restart کنید و از کاربر بخواهید `/menu` یا `/start` بفرستد. پیام‌های قبلی خودکار edit نمی‌شوند؛ ردیف‌های outbox را برای اصلاح ظاهر دست‌کاری یا دوباره ارسال نکنید. اگر منوی تازه همچنان ناخواناست، نسخه Web A/K، حالت روشن/تاریک و اسکرین‌شات لازم است.

- در هر زمان فقط یک poller با یک bot token فعال باشد.
- پیش از migration، restore، اصلاح گسترده یا اقدام مالی، بکاپ سازگار بگیرید.
- پرداخت، کیف پول، موجودی، reward، outbox و status سفارش را مستقیم با SQL تغییر ندهید.
- داده بانک/provider را با order/payment قطعی تطبیق دهید؛ از روی screenshot یا ادعای کاربر credit نکنید.
- token، API key، secret callback، payload موجودی و PII را در ticket، log یا کانال تیم paste نکنید.
- هنگام رخداد، evidence را حفظ کنید. restart مکرر یا حذف WAL می‌تواند علت و داده قابل بازیابی را از بین ببرد.
- ابتدا اثر را محدود کنید، سپس علت را تشخیص دهید، و فقط با یک مسیر rollback/verification روشن تغییر دهید.

## نمای سرویس

| مؤلفه | مسئولیت | failure قابل مشاهده |
|---|---|---|
| `BotApplication` | route update، خرید، پرداخت و تحویل | پاسخ‌ندادن یا state اشتباه کاربر |
| `TelegramClient` | `getUpdates`، ACK/NACK offset و Bot API | timeout، 401، 409، 429 یا NACK تکرارشونده |
| SQLite | منبع حقیقت سفارش/مالی/انبار | lock، disk full، corruption یا migration failure |
| `PeriodicWorker` | expiry، provider polling، recovery، queue و reminder | backlog، سفارش/اعلان گیرکرده |
| `PaymentCallbackServer` | دریافت رویداد کارت احراز‌شده | 401/404/409/500 یا timeout |
| `PlisioClient` | invoice و polling crypto | invoice نساختن یا وضعیت syncنشده |
| `AdminController` | عملیات مدیر و پشتیبان | عدم دسترسی، command validation یا گزارش ناقص |

worker نگه‌داری با فاصله `JOB_INTERVAL_SECONDS` این ترتیب را اجرا می‌کند: settlement شاهد completedِ provider که پیش از crash ثبت شده، polling crypto، reconciliation هشدار review/receipt/manual-info/ticket/security، انقضای سفارش و paymentهای card همراه بازیابی notice terminal جاافتاده، reconciliation سفارش‌های paid/reward و paid-notice، بازیابی prompt سفارش‌های `awaiting_stock`/`awaiting_info`، fulfil رزرو، fulfil سفارش ready در `processing` پس از restock، recovery تحویل ready تکمیل‌شده، reminder، outbox و گزارش broadcast. deadline محلی به‌تنهایی crypto را terminal نمی‌کند. هر stage budget محدود دارد؛ queryهای missing-notice/delivery و cursor/wrap باید باعث شوند backlog بزرگ یا خطای دائمی یک ردیف، ردیف‌های قدیمی‌تر را برای همیشه starve نکند.

اطلاعات سفارش manual با `submit_manual_order_info` همراه transition به `processing` در یک transaction ثبت می‌شود. اگر رکورد legacy در `awaiting_info` ولی دارای `customer_info_json` است، آن را دستی پاک یا complete نکنید؛ maintenance آن نسخه را برای alert owner/admin می‌بیند و پس از بررسی، اصلاح باید از workflow دامنه‌ای انجام شود. شکست ارسال alert نباید payload commit‌شده یا دسترسی `/order_attachment` را از بین ببرد.

## سطوح رخداد

| سطح | نمونه | اقدام اولیه |
|---|---|---|
| `SEV-1` | credit اشتباه، تحویل payload به فرد اشتباه، تکرار گسترده تحویل، DB خراب، افشای token/secret | توقف/محدودسازی فوری، حفظ evidence، مالک و مسئول فنی |
| `SEV-2` | ربات برای همه قطع، poller conflict، callback پرداخت قطع، backlog رو به رشد، disk بحرانی | مهار ظرف چند دقیقه، failover/rollback کنترل‌شده |
| `SEV-3` | یک سفارش/کاربر گیرکرده، یک integration کند، report ناموفق | بررسی موردی بدون تغییر مستقیم داده |
| `SEV-4` | اشکال ظاهری، سؤال استفاده یا درخواست قابلیت | backlog عادی توسعه |

برای خطای مالی یا امنیتی severity را کمتر تخمین نزنید. maintenance mode با `/bot_off` ورود عملیات جدید کاربر را محدود می‌کند، اما جای stop سرویس در corruption یا compromise نیست.

## مسئولیت‌ها و دسترسی

- `owner`: تنظیمات حساس، مدیران، بکاپ و تصمیم مالی/امنیتی.
- `admin`: مدیریت کاتالوگ، سفارش، پرداخت، کاربران، تخفیف، پیام و گزارش.
- `support`: مشاهده خلاصه سفارش/کاربر، تیکت و پیوست همان تیکت، پیام مستقیم و درخواست اصلاح اطلاعات؛ فیش payment و پیوست اطلاعات سفارش manual را نمی‌بیند و حق تغییر دلخواه وضعیت مالی یا تکمیل سفارش ندارد.
- operator سرور: service، filesystem، secret store، backup، monitoring و release.

حساب مشترک نسازید. نقش‌ها را حداقلی، chat ID را قطعی و مدیر خارج‌شده را همان روز disable کنید. ثبت دسترسی production و restore باید قابل ممیزی باشد.

## بررسی‌های روتین

### روزانه

- process/container فعال و بدون restart loop است.
- log جدید خطای تکرارشونده Telegram، SQLite، provider یا maintenance ندارد.
- کاربران واقعی update می‌فرستند و offset رشد می‌کند.
- تعداد پرداخت‌های `verifying` و سفارش‌های `awaiting_confirmation` با کار تیم تطبیق دارد.
- صف `outbound_messages` و `reminders` رشد دائمی ندارد.
- سفارش `awaiting_stock` با موجودی و صف رزرو هماهنگ است.
- disk و inode فضای امن دارند.
- آخرین backup موفق و hash/size غیرصفر است.

### هفتگی

- یک smoke test روی bot آزمایشی برای شروع، فروشگاه، سفارش، ticket و نقش‌ها اجرا شود.
- خطاهای terminal outbox و payment مرور و علت دسته‌بندی شود.
- مدیران فعال، کانال‌های جوین و روش‌های پرداخت بازبینی شوند.
- retention log/backup و ظرفیت disk بررسی شود.
- dependency/security advisoryها مرور شوند؛ update بدون staging انجام نشود.

### ماهانه

- restore drill پایه روی مسیر ایزوله، بدون راه‌اندازی bot/poller و بدون token اجرا شود؛ آزمون جریان فقط پس از مجوز و anonymization داده، با token آزمایشی مجاز است.
- `integrity_check` و `foreign_key_check` روی فایل restoreشده پاس شود.
- token/API key/secret rotation policy و دسترسی افراد audit شود.
- یک مانور rollback و یک تست failure شبکه/provider انجام شود.
- runbookها با تغییرات کد و incidentهای ماه قبل sync شوند.

## فرمان‌های سلامت سرویس

### systemd

```bash
sudo systemctl is-active alone-account-bot
sudo systemctl show alone-account-bot \
  -p ActiveState -p SubState -p NRestarts -p MainPID
sudo journalctl -u alone-account-bot --since '30 minutes ago' --no-pager
```

### Docker Compose

```bash
docker compose ps
docker compose logs --since=30m --tail=500 bot
docker inspect --format '{{.State.Status}} {{.RestartCount}}' "$(docker compose ps -q bot)"
```

### بررسی config و Bot API

`--check` دیتابیس را initialize/migrate تا schema 11 می‌کند، owner bootstrap را با root marker و private chat/user ID پایدار روی همان DB ایجاد/اعتبارسنجی می‌کند و سپس به `getMe` وصل می‌شود. drift username همان chat پس از verify conflict نیست؛ owner غیرفعال‌شده دوباره فعال نمی‌شود و انتقال marker فقط به owner فعال و verifyشده ممکن است. تعارض identity legacy یا configured chat ID نامعتبر پیش از تماس Telegram fail closed است. آن را فقط بعد از backup و با identity نهایی release برای preflight کنترل‌شده استفاده کنید، نه probe پرتکرار monitoring:

```bash
cd /opt/alone-account-bot
sudo -u alonebot ./.venv/bin/python -m app.main \
  --env-file /etc/alone-account-bot.env --check
```

برای دیدن وضعیت webhook بدون چاپ token یا URL:

```bash
cd /opt/alone-account-bot
sudo -u alonebot ./.venv/bin/python -c \
  'from app.config import load_settings; from app.telegram import TelegramClient; s=load_settings("/etc/alone-account-bot.env"); t=TelegramClient(s.bot_token); r=t.call("getWebhookInfo"); print({"url_set": bool(r.get("url")), "pending_updates": r.get("pending_update_count")}); t.close()'
```

در حالت عادی `url_set` باید `False` باشد. restart موفق برنامه webhook را با `drop_pending_updates=False` حذف می‌کند.

## بررسی read-only دیتابیس

این queryها اطلاعات خلاصه می‌دهند و payload/PII را چاپ نمی‌کنند. مسیر را صریح و فقط برای همان محیط تعیین کنید:

```bash
BOT_DB_PATH=/var/lib/alone-account-bot/alone_account.sqlite3
sudo -u alonebot sqlite3 -readonly "$BOT_DB_PATH"
```

```sql
SELECT value AS schema_version
FROM schema_meta WHERE key = 'schema_version';

SELECT key, value_json, updated_at
FROM settings WHERE key = 'telegram_update_offset';

SELECT status, COUNT(*)
FROM orders GROUP BY status ORDER BY status;

SELECT method, purpose, status, COUNT(*)
FROM payments GROUP BY method, purpose, status
ORDER BY method, purpose, status;

SELECT 'card' AS source, COUNT(*) AS open_reviews
FROM card_payment_events event
LEFT JOIN card_payment_event_resolutions resolution ON resolution.event_id = event.id
WHERE event.status = 'review' AND resolution.id IS NULL
UNION ALL
SELECT 'provider', COUNT(*)
FROM provider_payment_events event
LEFT JOIN provider_payment_event_resolutions resolution ON resolution.event_id = event.id
JOIN payments payment ON payment.id = event.payment_id
WHERE resolution.id IS NULL
  AND (event.disposition = 'review'
       OR (event.disposition = 'completed' AND payment.status = 'failed'));

SELECT event_type, COUNT(*), MAX(created_at) AS latest
FROM payment_security_events GROUP BY event_type ORDER BY event_type;

SELECT status, COUNT(*)
FROM outbound_messages GROUP BY status ORDER BY status;

SELECT COUNT(*) AS stale_sending
FROM outbound_messages
WHERE status = 'sending'
  AND julianday(updated_at) <= julianday('now', '-5 minutes');

SELECT status, COUNT(*)
FROM reminders GROUP BY status ORDER BY status;

SELECT COUNT(*) AS reward_reconciliation_backlog
FROM orders
WHERE reward_processed_at IS NULL
  AND status IN ('paid','awaiting_stock','awaiting_info','processing','completed');

SELECT status, COUNT(*), MAX(created_at) AS latest
FROM backups GROUP BY status ORDER BY status;

PRAGMA quick_check;
PRAGMA foreign_key_check;
```

`quick_check` باید `ok` و `foreign_key_check` باید بدون ردیف باشد. خروجی‌های دارای username، phone، receipt، raw payload یا inventory payload را در ابزار monitoring جمع نکنید.

برای این release مقدار `schema_version` باید `11` باشد. اختلاف نسخه را با اجرای migration پشتیبانی‌شده روی backup/fixture حل کنید؛ `schema_meta` را دستی تغییر ندهید.

## وضعیت طبیعی queue و recovery

- `outbound_messages`: `queued → sending → sent`؛ claim `sending` قدیمی‌تر از پنج دقیقه خودکار به صف برمی‌گردد. خطای موقت با backoff نمایی retry و پس از حداکثر ۱۲ تلاش `failed` می‌شود. خطای 4xx دائمی، به‌جز 429، زودتر terminal است.
- `reminders`: batch محدود `pending → processing` claim می‌شود و هر عضو مستقل ادامه می‌یابد؛ recipient دائماً blocked نباید reminder بعدی را starve کند. reminder بدون user/order معتبر failed می‌شود. اگر outbox متناظر `queued/sending` است، outbox مالک retry و reminder تا stale reconciliation در `processing` می‌ماند؛ فقط نبود outbox باعث release فوری می‌شود.
- `reward_processed_at`: فقط پس از grant idempotent reward و ثبت اعلان‌های پایدار پر می‌شود؛ completion fulfillment نیست. reward scan با cursor `maintenance_reward_reconcile_cursor` و selector مستقل سفارش‌های status=`paid` با `maintenance_fulfillment_reconcile_cursor` هرکدام حداکثر ۱۰۰ مورد را می‌گیرند و در انتها wrap می‌شوند.
- `reward_events`: هر event فاقد outbox `reward:{id}:notice`، حتی event نوع `start` بدون Order، با `maintenance_reward_notice_cursor` بازیابی می‌شود؛ credit دستی برای «اصلاح اعلان» ممنوع است.
- سفارش ready در `processing`: این state پس از race اتمام stock، منبع `maintenance_ready_stock_alert_cursor` است. نبود `manual-stock-notice` کاربر یا alert per-admin به معنای شکست مالی/تحویل نیست؛ reconciler باید همان کلید پایدار را بسازد.
- خلاصه ساخت Order: پس از first-contact باید کلید `order:{id}:created-summary` هم‌زمان با Order commit شده باشد. نبود آن در Order تازه نشانه release ناسازگار است؛ flow/state را با SQL پاک یا Order دوم نسازید.
- موفقیت پرداخت: payment بیرونی paid کلید `payment:{id}:order-confirmed` یا `payment:{id}:topup-confirmed` دارد. برای Order، متن canonical باید شماره، محصول، مبلغ و روش را پیش از fulfillment attempt کند؛ status `queued/sending` dependency را بسته نگه می‌دارد و `sent/failed/cancelled` آن را باز می‌کند. `failed/cancelled` باید به‌عنوان اعلان غیرقابل‌تحویل عملیاتی بررسی شود، نه اینکه Order paid را برگرداند یا settlement را تکرار کند.
- موفقیت بدون پرداخت بیرونی: Orderهای تأییدشده کیف پول، تخفیف کامل و رایگان کاربر که کلید متناظر `order:{id}:wallet-confirmed`، `order:{id}:discount-confirmed` یا `order:{id}:free-confirmed` ندارند با `maintenance_zero_external_notice_cursor` صفحه‌بندی و wrap می‌شوند؛ status/ledger را برای جبران پیام تغییر ندهید.
- `reservations`: موجودی جدید ابتدا به قدیمی‌ترین reservation واجد شرایط در کل محصولات اختصاص می‌یابد و هر چرخه حداکثر ۱۰۰ fulfil انجام می‌دهد.
- `processed_admin_updates`: ردیف `started` فقط در replay همان fingerprint resume و `completed` skip می‌شود؛ update ID با fingerprint متفاوت conflict است. `effect_json` target toggle را freeze می‌کند. ردیف started مانده را دستی completed نکنید؛ ابتدا اثر دامنه‌ای/idempotency و امکان replay همان update را بررسی کنید. این journal به‌تنهایی exactly-once شبکه‌ای نیست.
- `telegram_update_offset`: در خطای موقت پایهٔ DB، `process_update_safe` باید `False` بدهد؛ poller offset update جاری یا موارد بعدی همان batch را ذخیره نمی‌کند، batch را قطع و از همان offset با backoff نمایی سقف‌دار/stop-aware retry می‌کند. diagnostic فقط تلاش نخست است و failure آن NACK را خنثی نمی‌کند. خطاهای terminal دامنه و پاسخ Telegram ACK هستند. NACK تکرارشونده یعنی مشکل DB هنوز رفع نشده است؛ با تغییر دستی offset یا `processed_admin_updates.status` از آن عبور نکنید.
- `provider_payment_events`: completed ثبت‌شده ولی payment هنوز `pending/verifying` باید در ابتدای چرخه بعد بدون network settle شود؛ review باز باید در `/crypto_reviews` دیده شود. حذف/ویرایش این ledger ممنوع است.
- `payment_security_events`: رخدادهای `card_daily_limit` و `card_cancel_cooldown` باید با alert پایدار مدیر همگام شوند؛ برای رفع هشدار counterها را با SQL دست‌کاری نکنید.

رکورد `sent` را مستقیم به `queued` برنگردانید و count مالی را با update دستی «اصلاح» نکنید. اگر ابزار recovery عمومی وجود ندارد، ابتدا patch و regression test بسازید یا از فرمان دامنه‌ای موجود استفاده کنید.

## روند عمومی پاسخ به رخداد

1. **اعلام و زمان‌سنجی:** زمان UTC/محلی، severity، محیط، release SHA و مسئول incident را ثبت کنید.
2. **مهار:** روش پرداخت، bot یا service را به اندازه لازم خاموش کنید؛ دامنه را بی‌جهت بزرگ نکنید.
3. **حفظ evidence:** log سانسورشده، service status، release SHA، schema version، queryهای شمارشی و backup آنلاین را ذخیره کنید.
4. **تشخیص:** اولین خطا را از پیامدها جدا کنید؛ restart loop و retryها ممکن است noise بسازند.
5. **بازیابی:** fix، rollback یا اقدام دامنه‌ای پشتیبانی‌شده را انتخاب و یک‌بار کنترل‌شده اجرا کنید.
6. **اعتبارسنجی:** process، DB integrity، poller، backlog، smoke Telegram و تراکنش نمونه را بررسی کنید.
7. **پایش:** حداقل یک چرخه maintenance و بازه زمانی متناسب با incident را زیر نظر بگیرید.
8. **بستن:** مشتریان/مالک را با واقعیت قطعی مطلع، timeline و داده مالی reconcile و regression test اضافه کنید.

## Runbook: ربات پاسخ نمی‌دهد

1. status و آخرین log را بدون restart بررسی کنید.
2. اتصال DNS و HTTPS خروجی به `api.telegram.org` را از همان میزبان بررسی کنید.
3. یک poller دیگر روی همان سرور، container stack دیگر، laptop توسعه یا میزبان قدیمی را پیدا کنید.
4. `getWebhookInfo` را با اسکریپت سانسورشده بالا بررسی کنید.
5. خطای مشخص را دسته‌بندی کنید:
   - `401`: token نامعتبر/revokeشده؛ token تازه BotFather لازم است.
   - `409 Conflict`: poller دیگر یا webhook فعال است.
   - `429`: rate limit؛ retry داخلی `retry_after` را رعایت می‌کند، حجم broadcast را بررسی کنید.
   - timeout/5xx: شبکه یا Telegram؛ از restart پی‌درپی خودداری و روند retry را پایش کنید.
6. پس از رفع علت، فقط یک instance را start کنید.
7. `/start` و یک callback را smoke test و رشد offset را تأیید کنید.

اگر update وارد می‌شود ولی یک کاربر پاسخ نمی‌گیرد، blocked بودن کاربر، private بودن chat، state stale و خطای 4xx همان recipient را بررسی کنید؛ outage سراسری اعلام نکنید. اگر offset روی یک update مدیریتی ثابت و همان update با فاصله‌های رو به افزایش تکرار می‌شود، وضعیت SQLite/disk/lock و journal `started` همان update را بررسی کنید. اجرای update بعدی پیش از حل NACK یا جلو بردن دستی offset ممنوع است؛ پس از رفع خطای موقت، همان update باید ابتدا کامل و journal آن `completed` شود.

## Runbook: فهرست یا تاریخچه مدیریتی بزرگ

- `/orders`, `/tickets`, `/users` و چهار فرمان `/user_*` هر صفحه ۲۰ ردیف و سربرگ `صفحه X از Y | مجموع: N` دارند. از فرمان قبلی/بعدی چاپ‌شده استفاده کنید؛ صفحه خارج بازه عمداً رد می‌شود.
- `/orders` باید همه ردیف‌ها را با `id DESC`، `/tickets` با `updated_at DESC` و users با `id DESC` نشان دهد. تغییر هم‌زمان داده ممکن است total صفحه بعد را تغییر دهد؛ برای snapshot قابل استناد از گزارش CSV بازه‌ای استفاده کنید.
- `/user CHAT_ID|@username|ORDER_NUMBER` فقط preview است. برای تاریخچه کامل از `/user_orders USER [STATUS|all] [PAGE|ORDER_NUMBER]`, `/user_transactions USER [PAGE]`, `/user_referrals USER [PAGE]` و `/user_rewards USER [PAGE]` استفاده کنید. search سفارش باید مالکیت همان user را تأیید کند.
- نبود ردیف قدیمی با وجود total بزرگ، تکرار ردیف بین صفحه‌ها بدون mutation هم‌زمان، یا نبود راهنمای `بعدی` پیش از آخرین صفحه را regression بدانید؛ output را با SQL دارای PII در ticket کپی نکنید.

## Runbook: خطای 409 یا دو poller

نشانه اصلی خطای conflict مکرر `getUpdates` است.

```bash
pgrep -af 'python.*app.main'
docker ps --format '{{.ID}} {{.Names}} {{.Image}}'
```

همه میزبان‌های شناخته‌شده را بررسی کنید. instance مورد نظر production را مشخص، بقیه را graceful stop و سپس service اصلی را یک بار restart کنید. دو poller ممکن است updateها را تقسیم کرده و offsetهای متفاوت بسازند؛ پس از مهار، سفارش‌ها/فرمان‌های همان بازه را audit کنید. `drop_pending_updates=True` استفاده نکنید، چون update حل‌نشده را دور می‌ریزد.

## Runbook: restart loop یا خطای startup

- `BOT_TOKEN is required`: secret provisioning یا permission env خراب است.
- `getMe` ناموفق: token/network را بررسی کنید؛ token را چاپ نکنید.
- `database is locked`: runbook SQLite را اجرا کنید.
- `Permission denied`: مالکیت data directory و env را با user سرویس تطبیق دهید.
- migration failure: سرویس را متوقف نگه دارید؛ DB را restore یا migration را روی کپی debug کنید.
- bind failure پورت callback: process قدیمی یا پورت اشتباه را بررسی کنید.

پیش از restart، `NRestarts` و اولین exception را ذخیره کنید. restart loop را با افزایش بی‌دلیل delay پنهان نکنید.

## Runbook: SQLite locked، disk full یا WAL غیرعادی

1. تعداد processها و استفاده هم‌زمان از DB را بررسی کنید.
2. disk، inode، permission و نوع filesystem را بررسی کنید.
3. job/command طولانی یا ابزار خارجی متصل به DB را قطع کنید.
4. در disk بحرانی، service را stop کنید تا write جدید نیاید؛ فایل live یا WAL را حذف نکنید.
5. فضای امن را با حذف artifactهای غیرمرتبط و قابل‌بازیابی آزاد کنید، نه DB/backups اخیر.
6. یک backup سازگار بگیرید و integrity را بررسی کنید.
7. تنها پس از رفع علت یک instance را start و backlog را پایش کنید.

WAL و SHM اجزای فعال دیتابیس‌اند. حذف، rename یا کپی جداگانه آن‌ها هنگام اجرای process ممنوع است. DB را روی NFS/Dropbox/OneDrive یا volume موقت قرار ندهید.

## Runbook: corruption یا foreign key failure

این رخداد `SEV-1` است.

1. service را فوری stop کنید و دسترسی write را قطع کنید.
2. کل state directory و log مرتبط را به مقصد forensic فقط‌خواندنی کپی کنید.
3. روی کپی، `integrity_check` و `foreign_key_check` اجرا کنید؛ روی اصل آزمایش repair نکنید.
4. آخرین backup سالم را پیدا و روی مسیر موقت restore کنید.
5. release/schema متناظر backup را تعیین کنید.
6. طبق `deployment.md` rollback کد+DB انجام دهید.
7. سفارش و پرداخت بعد از زمان backup را با منبع بانک/provider reconcile کنید.

استفاده مستقیم از `.recover`، حذف ردیف یا خاموش‌کردن foreign key بدون طرح تأییدشده می‌تواند خسارت مالی را بیشتر کند.

## Runbook: callback کارت سالم نیست

### timeout یا connection refused

- فعال‌بودن `PAYMENT_CALLBACK_SECRET` و listener را از log بررسی کنید.
- bind/port، reverse proxy upstream، firewall و TLS را بررسی کنید.
- health داخلی authenticated و سپس URL بیرونی را تست کنید.
- پورت داخلی را مستقیم روی اینترنت باز نکنید.

### پاسخ 401

- secret server و bridge باید byte-for-byte برابر باشند.
- reverse proxy باید header را عبور دهد و آن را log نکند.
- header تکراری، Bearer ناقص و whitespace ناخواسته رد می‌شوند.

### پاسخ 400/413/415

- JSON باید object سخت‌گیرانه با فیلدهای `amount`, `reference`, `occurred_at` باشد.
- amount عدد صحیح مثبت، reference غیرخالی و timestamp دارای timezone باشد.
- `Content-Type` باید `application/json` و body حداکثر 4096 byte باشد.

### پاسخ 404 یا 409

- 404 یعنی پرداخت matching پیدا نشده است.
- 409 یعنی رویداد/پرداخت وجود دارد اما وضعیت یا زمان آن اجازه تأیید ندارد.

هیچ‌کدام دلیل approve کورکورانه نیست. reference، زمان وقوع، مبلغ، روش، purpose، user و status را با سند بانک بررسی کنید. رویداد دیررس نباید به intent تازه با همان مبلغ متصل شود.

## Runbook: پرداخت یا کیف پول مشکوک

1. در صورت الگوی گسترده، روش را با `/payment card off` یا `/payment crypto off` غیرفعال و در صورت نیاز `/bot_off` کنید.
2. payment number، order number، chat ID، مبلغ، زمان و reference را از مسیر مدیریتی جمع کنید؛ payload محرمانه را منتشر نکنید.
3. وضعیت بانک/provider را از منبع مستقل تأیید کنید.
4. order، payment و مجموع دفترکل کیف پول را بدون تغییر بررسی کنید.
5. replay یا external reference تکراری، پرداخت منقضی، partial wallet و refund قبلی را لحاظ کنید.
6. برای فیش از `/payment_detail` سپس `/approve_payment` یا `/reject_payment` استفاده کنید؛ برای evidence نامنطبق از `/card_reviews` یا `/crypto_reviews` و resolve فقط‌مالک استفاده کنید.
7. اگر حالت با فرمان‌های موجود قابل حل نیست، fix کد با تست regression و idempotency بسازید؛ SQL دستی نزنید.
8. پس از حل، gross/external/wallet/refund report را برای همان بازه reconcile کنید.

`external_paid_amount` فقط سهم بیرونیِ فروش settleشده همان Order است، نه gross cash مشاهده‌شده provider. completed دیررس برای Order terminal با تصمیم owner به `manual_credit` جبرانی می‌رود و نباید به درآمد فروش یا aggregate همان Order افزوده شود.

دفترکل `wallet_entries` append-only است. اصلاح موجودی فقط entry جبرانی ثبت می‌کند. هرگز triggerهای منع update/delete را خاموش نکنید.

واحد مالی این release فقط `TOMAN` است. هر Order فقط یک external intent فعال card/crypto و هر user در مجموع این دو روش فقط یک topup تازه فعال دارد؛ replay فقط method/amount/terms یکسان را می‌پذیرد و اختلاف هرکدام conflict است. هیچ intent ضمنی replace/cancel نمی‌شود. داده legacy ممکن است دو topup فعال داشته باشد؛ هر دو باید جدا در کیف پول برای resume دیده شوند، اما intent دوم تازه مجاز نیست. یکتاسازی مبلغ فقط برای card است. فیش فقط برای card پذیرفته می‌شود: نخستین ارسال باید قبل از expires_at باشد؛ replacement فیش ثبت‌شده به‌موقع تا تصمیم صریح مدیر و در وضعیت باز مجاز است. پرداخت دارای فیش به دلیل گذشت زمان خودکار منقضی نمی‌شود. Order/topup crypto باز باید با URL ذخیره‌شده امن «ادامه پرداخت ارزی» داشته باشد؛ URL legacy ناامن لینک یا receipt تولید نمی‌کند. payment دارای فیش/`verifying` قابل لغو کاربر نیست؛ callback قدیمی لغو را retry نکنید. لغو card بدون فیش Order را terminal می‌کند و تغییر روش به Order تازه نیاز دارد. invoice crypto صادرشده محلی لغو نمی‌شود و deadline محلی آن را terminal نمی‌کند؛ تا evidence terminal provider و برای late transitionهای reviewشده poll می‌شود. مبلغ terminal کارت تا ۲۴ ساعت پس از `max(expires_at, updated_at)` quarantine است؛ آن را با SQL آزاد نکنید. پس از ۲۰ intent کارت در ۲۴ ساعت یا ۳ لغو در یک ساعت، ساخت تازه محدود و رخداد audit/alert می‌شود. `paid` terminal است و هیچ transition عمومی refund payment یا topup وجود ندارد؛ برچسب `refund_confirmed` فقط ثبت انجام واقعی بازپرداخت بیرونی است و خودش پول جابه‌جا نمی‌کند.

## Runbook: Plisio sync نمی‌شود

- وجود API key و فعال‌بودن crypto در setting را جداگانه بررسی کنید.
- دسترسی خروجی، timeout و status operation را بررسی کنید.
- invoice ID و order/payment را بدون چاپ query حاوی API key تطبیق دهید.
- URL invoice باید HTTPS مطلق باشد؛ HTTP، credential، `localhost`، IP literal محلی/خصوصی/reserved، host عددی مبهم یا URL بدقالب باید fail closed شود و نباید با لینک دستی دور زده شود. این validation DNS lookup نیست؛ hostnameای که رفتار DNS مشکوک دارد را جداگانه در egress/DNS policy کنترل کنید.
- هر پاسخ مالی ابتدا با payload/hash در `provider_payment_events` ثبت می‌شود. `completed` با `id/type=invoice` منطبق به settlement مشترک می‌رود. فیلد `operation.amount` crypto دریافتی است، نه تومان؛ آن را با مبلغ TOMAN مقایسه نکنید. فقط `expired/cancelled/error` همراه crypto amount صریحاً صفر failure قطعی است. `mismatch`، partial/nonzero، مبلغ نامعلوم، هویت ناسازگار یا mismatch فیلدهای fiat/source در `params` در صورت حضور همیشه review/quarantine است و credit خودکار ندارد.
- failure شبکه مبهم را پرداخت ناموفق قطعی تلقی نکنید؛ polling بعدی باید امکان reconciliation داشته باشد.
- در Order و topup، Payment provisional باید پیش از create شبکه‌ای وجود داشته باشد. اگر invoice ID/URL هنوز NULL است، آن را حذف/failed نکنید، hold/discount/order terms را تغییر ندهید و merchant order تازه نسازید؛ retry از همان Order/Wallet باید همان `payment_number` و مبلغ ثابت را با `return_existing=1` دوباره به provider بدهد و سپس `attach_crypto_invoice` را اجرا کند. provisional ناقص هنوز وارد poller operation نمی‌شود.
- invoice صادرشده را locally cancel یا با invoice روش دیگری روی همان Order جایگزین نکنید؛ deadline محلی را با terminal provider اشتباه نگیرید.
- `/crypto_reviews` را بررسی کنید. `dismiss` یا `refund_confirmed` فقط با شاهد مستقل و note مالک ثبت شود؛ completed تازه پس از چنین resolutionای review پرخطر جدید است. `credit_confirmed` فقط با evidence دقیق completed همان invoice مجاز است: topup را settle می‌کند، اما برای Order terminal صرفاً اعتبار جبرانی کیف پول می‌سازد و revenue فروش/احیای Order نیست.
- اگر completed event در DB هست ولی payment هنوز `pending/verifying` است، service را یک‌بار طبق runbook start کنید تا `_reconcile_completed_provider_events` بدون network آن را اعمال کند؛ event را دوباره insert/delete و payment را دستی paid نکنید.

`PUBLIC_PAYMENT_CALLBACK_URL` را به endpoint کارت وصل نکنید. نسخه فعلی Plisio را با polling operation همگام می‌کند.

## Runbook: روش پرداخت در UI دیده نمی‌شود

1. setting را با پنل مدیر بررسی کنید؛ wallet در نصب تازه فعال و crypto خاموش است.
2. برای card وجود هم‌زمان شماره و صاحب حساب را با `/set_card` کنترل کنید. enable بدون این دو عمداً رد می‌شود.
3. برای crypto وجود `PLISIO_API_KEY` در environment همان process و سپس `/payment crypto on` را کنترل کنید. افزودن key نیازمند restart است، چون client در startup ساخته می‌شود.
4. روش ناقص را با SQL یا تغییر مستقیم setting مجبور به نمایش نکنید؛ callback قدیمی نیز guard availability را دوباره اجرا می‌کند.

## Runbook: تحویل به‌دلیل طول پیام رد می‌شود

1. متن نهایی آماده را شامل عنوان/icon محصول، payload و `delivery_instructions` در نظر بگیرید؛ برای manual سربرگ سفارش و متن `/complete` را با هم حساب کنید.
2. خروجی باید حداکثر ۳۹۰۰ نویسه و در یک پیام باشد. credential را truncate یا چندقسمتی نکنید.
3. برای ready، payload/instruction را کوتاه و از مسیر مجاز edit کنید؛ assignment ناموفق نباید item یا Order را تغییر داده باشد. برای manual، `/complete` کوتاه‌تر را دوباره بفرستید؛ status باید همچنان `processing` مانده باشد.
4. اگر رکورد legacy بلند است، قبل از retry assignment آن را اصلاح و سپس invariant inventory/order/outbox را بررسی کنید.

## Runbook: پیام، reminder یا broadcast گیر کرده

1. count وضعیت queue و خطاهای maintenance را بررسی کنید.
2. اگر `sending` بیش از پنج دقیقه مانده، یک چرخه worker باید آن را recover کند؛ اجرای هم‌زمان worker دوم نسازید.
3. 429 باید با delay provider retry شود؛ ارسال دستی سریع‌تر مشکل را بدتر می‌کند.
4. 4xx دائمی معمولاً chat حذف‌شده، bot blocked یا recipient نامعتبر است.
5. message با وضعیت `sent` را requeue نکنید. در failure مبهم امکان دریافت پیام و ثبت‌نشدن پاسخ وجود دارد.
6. broadcast تازه را برای جبران کل audience تکرار نکنید تا recipientها دوباره پیام نگیرند؛ ابتدا batch و پیام‌های failed را مشخص کنید.
7. پس از رفع علت، قابلیت retry هدفمند را در کد/ابزار دامنه‌ای پیاده یا پیام جدید با scope دقیق بسازید.

برای Order پرداخت‌شده‌ای که fulfillment جلو نمی‌رود، ابتدا outbox canonical آن را پیدا کنید. `queued/sending` تا retry یا stale recovery باید gate را بسته نگه دارد؛ `failed/cancelled` terminal gate را باز می‌کند و علت عدم تحویل پیام باید در incident/پشتیبانی ثبت شود. این ردیف terminal را برای retry نامحدود به `queued` برنگردانید و پرداخت/کیف پول را دوباره settle نکنید.

گزارش نهایی broadcast از batch و status پیام‌های آن ساخته می‌شود و باید فقط یک‌بار برای مدیر ارسال شود. failure دائمی delivery همان summary یک تلاش نهاییِ حسابرسی‌پذیر است؛ batch نباید صرفاً به علت `notified_at` خالی برای همیشه ظرفیت دورهای بعدی را اشغال کند. اگر batch پنجاه‌ویکم پشت مجموعه ثابت batchهای قدیمی دیده نمی‌شود، query/cursor non-starvation regression دارد.

برای reminder، `reminder_days` عدد صحیح نامنفی است؛ صفر به معنی آغاز روز پایان در timezone محیط است. اگر schedule همان روز پیش از پایان انجام شود، موعد فوری خواهد بود؛ در انقضای دقیق نیمه‌شب یا پس از پایان واقعی reminder روز صفر ساخته نمی‌شود. وجود `reminder:{id}` در outbox queued/sending یعنی retry در مالکیت outbox است؛ reminder را دستی pending نکنید. متن روزهای مثبت تاریخ/ساعت مطلق پایان و timezone دارد و صفر «امروز» همراه ساعت پایان است. پیش از هر ارسال صف، reminder پایان‌یافته همراه outbox بدون پیام cancelled می‌شود. در یک batch، failure دائمی گیرنده نخست باید failed شود و گیرنده‌های بعدی ادامه یابند؛ توقف کل batch نشانه regression است.

notice انقضای Order/payment باید همراه terminal mutation در همان transaction ثبت یا از query «terminal و فاقد outbox» بازیابی شود. اگر پس از restart status terminal است اما پیام «دیگر پرداخت نکن» وجود ندارد، اجرای بعدی باید همان notice را با کلید پایدار بسازد؛ status را برای ارسال دوباره برنگردانید. همین اصل برای reply/status/close تیکت برقرار است: mutation تیکت و اعلان کاربر اتمیک‌اند و retry از outbox انجام می‌شود، نه تکرار فرمان.

alert پیام کاربر در تیکت برای همه نقش‌های active `owner/admin/support` از روی TicketMessage commit‌شده و cursor/wrap بازیابی می‌شود. شکست transient کپی مستقیم photo/document را با forward دستی تکرار نکنید؛ alert پایدار شامل `/ticket_attachment MESSAGE_ID` است و هر مدیر مجاز می‌تواند همان فایل DB را دوباره بگیرد. backlog باید در دورهای بعد جلو برود و کلید per-admin از alert تکراری برای همان پیام جلوگیری می‌کند.

## Runbook: تحویل موجودی یا رزرو

برای تقدم صف، زمان ساخت reservation کافی نیست. paid_at پرداخت‌های تازه با میکروثانیه افزایشی ثبت می‌شود؛ خرید تازه هم نباید از paid Order قدیمی‌تر فاقد reservation جلو بزند. در داده legacy با paid_at مساوی، reservation موجود و Order ID tie-break قطعی‌اند و درباره ترتیب تاریخی ناشناخته تضمینی وجود ندارد.

- product `ready` با موجودی `available` باید اتمیک assign و complete شود.
- نبود موجودی همراه reserve فعال، سفارش paid را در `awaiting_stock` و reservation FIFO نگه می‌دارد.
- با افزودن موجودی، worker قدیمی‌ترین Order پرداخت‌شده واجد stock را از صف رزرو fulfil می‌کند؛ بیش از ۱۰۰ تحویل در یک چرخه طبیعی نیست و باقی backlog باید در چرخه بعد پیشرفت کند.
- `/inventory_assign` مستقیم در حضور هر Order ready همان محصول با status `paid|processing|awaiting_stock` و بدون item assigned عمداً conflict می‌شود. برای دورزدن صف status/reservation را با SQL تغییر ندهید؛ maintenance باید backlog قدیمی‌تر را اول fulfil کند و تخصیص مستقیم فقط پس از خالی‌شدن صف مجاز است.
- پس از رسیدن stage رزرو به budget همان چرخه، stage سفارش‌های ready در `processing` باید product دارای reservation معتبر قدیمی‌تر را skip کند. مشاهده تحویل processing در حالی که برای همان product reservation قدیمی queued مانده است regression FIFO است؛ سرویس را از تخصیص تازه بازدارید و order/reservation/item IDها را برای تحلیل حفظ کنید.
- اگر Order ready بدون reserve در `processing` است ولی کاربر یا owner/admin هشدار ندیده، status را دستی برنگردانید؛ `list_ready_processing_orders` و `_reconcile_ready_stock_alerts` باید outboxهای `manual-stock-*` را با cursor/wrap بازیابی کنند.
- item `assigned` را disable/delete/edit نکنید و payload را در chat تیم paste نکنید.
- اگر crash بعد از assign رخ داده باشد، reconciliation delivery از order complete و کلید پایدار استفاده می‌کند.
- در `/inventory_assign` دستیِ مجاز و بدون backlog همان محصول، item assignment، سفارش completed `ADM-...` با `order_origin=admin_assignment`/marker پاداش، و delivery outbox یک transaction هستند. این Order داخلی نباید در خریدار، درآمد یا purchase reward ظاهر شود؛ مشاهده assignment بدون outbox متناظر، origin/marker نادرست، یا تخصیص مستقیم در حضور صف قدیمی نشانه invariant شکسته و نیازمند توقف mutation و بررسی release است.

برای ادعای تحویل تکراری، delivery notice، `assigned_order_id`، order status و outbound idempotency را بررسی کنید. payload تازه صادر نکنید تا مالکیت و دریافت قبلی روشن شود. اگر credential تحویلی افشا شده، آن را در سرویس مبدأ revoke و جایگزینی را با incident مالی/امنیتی ثبت کنید.

## Runbook: reward یا reminder اجرا نشده

- سفارش با کد ۱۰۰٪ یا محصول رایگانِ pending ممکن است منتظر دکمه پرداخت کاربر باشد؛ ثبت تخفیف تأیید نهایی نیست. پس از تأیید، discount-confirmed/free-confirmed و سپس fulfillment را بررسی کنید.
- شارژ expired باید `payment:{id}:topup-expired` داشته باشد؛ پیام جاافتاده پیش از restart با cursor/wrap بازیابی می‌شود. برای هشدار، payment terminal را احیا نکنید.
- فیش پیش از deadline به دلیل طول انتظار مدیر خودکار منقضی نمی‌شود؛ approve/reject صریح لازم است. پارامتر قدیمی grace در API داخلی فقط برای سازگاری پذیرفته می‌شود و سیاست انقضای خودکار ایجاد نمی‌کند.

- برای reward، referral، rule window، product condition، successful order و `reward_processed_at` را بررسی کنید. برای تحویل سفارش status=`paid` این marker را معیار نگیرید؛ selector مستقل `list_paid_orders_pending_fulfillment` باید آن را حتی با marker پر پیدا کند.
- rule ساخته‌شده بعد از purchase نباید retroactive باشد.
- grant جزئی پس از crash در چرخه بعدی با eventهای idempotent ادامه می‌یابد؛ credit تکراری دستی ندهید.
- marker فقط پس از durable notice ثبت می‌شود؛ علاوه بر آن، `list_reward_events_missing_notice` event فاقد `reward:{id}:notice` را مستقل و با cursor/wrap می‌یابد. backlog کوتاه‌مدت و کاهش حداکثر ۱۰۰ مورد در هر چرخه طبیعی است، اما نبود پیشرفت در چند چرخه یا رشد پایدار غیرطبیعی است.
- reminder فقط پس از تحویل واقعی و بر اساس `subscription_ends_at` ساخته می‌شود؛ پرداخت یا reservation به‌تنهایی شروع اشتراک نیست.

## Runbook: جوین اجباری همیشه رد می‌شود

- chat ID/username و invite URL را بررسی کنید؛ invite فقط canonical HTTPS روی `t.me`/`telegram.me` بدون port/query/fragment است.
- نوع chat باید channel/supergroup معتبر باشد.
- bot باید عضو و دارای سطح لازم باشد؛ فعال‌سازی مجدد کانال این مورد را revalidate می‌کند.
- حساب تست غیرمدیر و صفحه‌بندی کانال‌ها را آزمایش کنید.
- failure Telegram را با bypass دائمی جوین حل نکنید؛ ابتدا دسترسی bot را درست کنید.

## Runbook: مالک یا مدیر دسترسی ندارد

- فقط private chat قابل قبول است.
- `/admins` را با owner فعال بررسی کنید: «در انتظار تأیید هویت» یعنی `identity_verified_at` خالی و حتی اگر `is_active=1` باشد هنوز هیچ مجوزی ندارد.
- grant تازه به هر دو username و private chat/user ID نیاز دارد. زوج ناشناخته فقط وقتی verify می‌شود که همان chat با همان username یک update خصوصی معتبر بفرستد؛ `/start` روش عملیاتی پیشنهادی است. mismatch را با SQL bind نکنید. username pending اشتباه را با `/admin_add USERNAME_CORRECT SAME_CHAT_ID ROLE` اصلاح یا با `/admin_disable CHAT_ID` ابطال کنید.
- بعد از verify، chat ID anchor است و rename username همان حساب با update معتبر metadata را تازه می‌کند؛ از واگذاری username به حساب دیگر برای بازیابی access استفاده نکنید.
- bootstrap username-only امکان proactive message ندارد و تا proof pending است. marker `is_bootstrap_owner` restart را از بازساخت root قدیمی یا re-enable مالک غیرفعال بازمی‌دارد.
- برای rotation، ابتدا owner جایگزین را add، از chat خودش verify و activeبودنش را کنترل کنید؛ سپس configured `BOOTSTRAP_ADMIN_CHAT_ID` را به همان chat تغییر و بعد از backup `--check` اجرا کنید. مقصد pending/غیرفعال/non-owner fail closed است و رکورد قبلی خودکار حذف نمی‌شود.
- role escalation به owner و غیرفعال‌سازی مالک باید مطابق guardهای برنامه باشد؛ DB را دستی ویرایش نکنید.
- اگر token/owner compromise شده، runbook امنیت را اجرا کنید، نه اینکه فقط username را عوض کنید.

## Runbook: افشای token، secret یا API key

این رخداد حداقل `SEV-1` امنیتی است.

1. دامنه افشا و زمان را ثبت کنید؛ secret را دوباره در incident channel paste نکنید.
2. Bot token را در BotFather revoke و token تازه صادر کنید. callback secret و provider API key را در مبدأ rotate کنید.
3. instanceهای قدیمی را stop و secret تازه را فقط از secret store deploy کنید.
4. history Git، CI artifact، container layer، log، backup و chat را برای secret قدیمی جست‌وجو کنید.
5. اگر secret commit شده، history remediation هماهنگ انجام دهید؛ force-push بدون هماهنگی نکنید.
6. service را start، `getMe`/callback/provider را verify و همه instanceهای قدیمی را دوباره بررسی کنید.
7. پرداخت، پیام و تغییر مدیریتی بازه exposure را audit کنید.

log redaction کد مانع همه انواع افشا نیست؛ reverse proxy، shell history و ابزارهای ثالث نیز باید بررسی شوند.

## پشتیبانی یک سفارش بدون دست‌کاری داده

روال استاندارد:

1. کاربر را با chat ID یا username از `/user` پیدا کنید.
2. order number را با `/order` بررسی کنید.
3. اگر اطلاعات activation ناقص است، `/request_info` بفرستید.
4. فیش را فقط با payment number و سند مستقل بررسی کنید.
5. برای محصول دستی پس از انجام واقعی سرویس `/complete` را اجرا کنید.
6. پاسخ و تصمیم را در ticket نگه دارید و از گرفتن password غیرضروری خودداری کنید.
7. اگر حالت transition مجاز نیست، آن را force نکنید؛ علت مالی/فنی را escalate کنید.

پشتیبان نباید از کاربر token، رمز اصلی حساب شخصی، CVV2 یا تصویر مدرک غیرضروری بخواهد. attachment تیکت حاوی داده شخصی است و باید با حداقل دسترسی دیده شود.

## بکاپ، retention و restore drill

- بکاپ روزانه و قبل از release/تغییر مالی مهم بگیرید.
- حداقل یک نسخه خارج VPS و رمزگذاری‌شده نگه دارید.
- retention را متناسب با نیاز قانونی و کسب‌وکار تعریف کنید؛ نمونه متداول روزانه ۷، هفتگی ۴ و ماهانه چند نسخه است، اما تصمیم نهایی با مالک داده است.
- metadata شامل زمان UTC، release SHA، schema version، اندازه، SHA-256 و نتیجه integrity را ثبت کنید.
- restore drill پایه را در مسیر ایزوله و access-controlled انجام دهید و فقط integrity/schema را بدون شروع poller، worker، callback یا delivery بررسی کنید؛ این مرحله به bot token نیاز ندارد.
- اگر آزمون جریان کسب‌وکار روی داده restoreشده لازم است، مجوز روشن مالک داده و anonymization متناسب پیش‌شرط است؛ سپس فقط از bot token آزمایشی استفاده کنید. هرگز clone دیتابیس production را با token production یا هم‌زمان با سرویس production poll نکنید.
- فایل restoreشده را با داده جدید production merge نکنید مگر ابزار reconciliation طراحی و تست‌شده داشته باشید.

فرمان `/backup` فقط برای owner است و از SQLite backup API استفاده می‌کند. دستورهای systemd/Docker و rollback کامل در `deployment.md` آمده‌اند.

## تغییرات اضطراری

hotfix نیز باید حداقل این گیت‌ها را داشته باشد:

- issue و دامنه دقیق incident.
- regression test که قبل از fix fail و بعد از آن pass می‌شود.
- کل suite و compile موفق.
- بازبینی اثر migration/idempotency/roles/secrets.
- backup قبل از deploy و rollback مشخص.
- release SHA ثبت‌شده و یک instance.

ویرایش مستقیم فایل Python روی سرور، اجرای SQL دستی یا deploy branch نامشخص راه‌حل قابل قبول نیست. اگر برای مهار اضطراری ناچار به تغییر موقت config شدید، آن را ثبت و پس از incident به config-as-code برگردانید.

## قالب گزارش incident و handoff

```text
شناسه و عنوان رخداد:
severity و وضعیت فعلی:
زمان شروع/کشف/مهار/بازیابی (UTC و محلی):
محیط، host و release SHA:
schema_version:
دامنه کاربران/سفارش‌ها/مبالغ متاثر:
نشانه اولیه و اولین خطای معتبر:
اقدامات مهار انجام‌شده:
backup/evidence و checksum:
علت ریشه‌ای تاییدشده یا فرضیه‌های باز:
اقدام بازیابی و verification:
reconciliation مالی:
secret rotation لازم/انجام‌شده:
تست regression و PR/commit:
مالک کارهای پیگیری و deadline:
```

در handoff، داده شخصی را با شناسه داخلی حداقلی جایگزین و دسترسی evidence را محدود کنید. «حل شد» فقط وقتی معتبر است که علت، اصلاح، تست، verification production و پایش بعدی ثبت شده باشد.

## معیار بسته‌شدن رخداد

- اثر جدید متوقف شده است.
- منبع حقیقت مالی و انبار reconcile شده است.
- process پایدار، دقیقاً یک poller و DB سالم است.
- backlog در حال کاهش یا صفر و smoke test موفق است.
- مشتری/مالک مرتبط با متن تاییدشده مطلع شده‌اند.
- secretهای متاثر rotate شده‌اند.
- regression test و اصلاح پایدار در Git قرار گرفته‌اند.
- postmortem شامل علت سیستمی و اقدام پیشگیرانه زمان‌دار است.
