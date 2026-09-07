# مقصد انتشار جاری

## میزبان جدید مصوب — ۲۰۲۶-۰۹-۰۷

انتقال به میزبان مصوب کاربر **`192.168.10.111` انجام شد**. این آدرس مقصد همهٔ انتشارهای بعدی است. SSH با حساب `mr-kheiry`، Ubuntu 26.04.1 LTS، Python 3.14.4، ساعت همگام Asia/Tehran و HTTPS خروجی Telegram بررسی شدند. ورود و رمز SSH در فایل‌های سورس/گیت ذخیره نمی‌شوند؛ نام حساب فقط برای راهنمای اتصال آمده است.

| مورد | وضعیت و محل فعلی |
|---|---|
| هویت ربات | `@ElevenaccountsTestbot` / `8545042168`؛ تغییر نکرده است |
| سرویس اصلی | `alone-account-bot.service`؛ `enabled` و `active/running`، با user غیر root به نام `alonebot` |
| شروع روی سرور | ۲۰۲۶-۰۹-۰۷ ساعت ۱۴:۱۴:۵۷؛ آخرین restart آزمایشی موفق ساعت ۱۴:۱۷:۰۸ |
| کد تست‌شدهٔ rollout | commit `95179d36471eb8de746ef5a53f81e2356bd69f1b` در `/opt/alone-account-bot`؛ commitهای بعدیِ صرفاً مستنداتی نیاز به restart ندارند |
| env و داده | `/etc/alone-account-bot.env` با `root:alonebot 0640`؛ `/var/lib/alone-account-bot` با `alonebot:alonebot 0700`؛ فایل DB و manifest با mode `0600` |
| آیکون و رنگ | `/var/lib/alone-account-bot/button-icons.json`، ۴۵ آیکون و `BUTTON_COLOR_MODE=colored` |
| log | `journalctl -u alone-account-bot.service`؛ قبل از اشتراک‌گذاری redact شود |
| بکاپ cutover | `/srv/backups/alone-account-bot/host-cutover-20260907.tar.gz`، فقط root با mode `0600`؛ شامل env محرمانه است و عمومی نشود |
| Windows | فقط توسعه/تست بدون token واقعی و آرشیو مبدأ؛ poller قبلی متوقف شده و نباید دوباره شروع شود |

### شواهد انتقال و پذیرش

- ۴۹۱ تست در محیط ایزولهٔ همین سرور، بدون token یا DB واقعی، در **۵۸٫۹۵۹ ثانیه** موفق شدند؛ هیچ skip/failure/error نبود. lint، compile، secret scan و `pip check` نیز موفق بودند. دو تست POSIX که روی Windows skip می‌شدند، اینجا اجرا شدند. dependencies runtime دقیقاً از `requirements-runtime.lock` نصب شدند.
- backup آنلاین سالم پیش از توقف: `work/eleven_runtime_data/recovery/before-host-cutover-20260907.sqlite3` در مبدأ، SHA-256 برابر `d48c33287e6b81dfdc3eca9d9ee6f5702b1fb8601a20c60ddc2e8c862e7cc802`، integrity برابر `ok` و FK violation صفر. هر دو process شناخته‌شدهٔ launcher/worker ویندوز با Ctrl+C محدود به console خودشان متوقف و نبود poller محلی تأیید شد.
- snapshot نهایی پس از توقف با SQLite backup API ساخته و **تمام ردیف‌های همهٔ جدول‌ها، schema و شمارنده‌های sqlite_sequence** با مبدأ مقایسه شد. ۱۹ فایل شامل env با سه مسیر Linux و تمام فایل‌های runtime/پیوست/آرشیو با digest بررسی و روی مقصد نصب شدند. fingerprint یکسان پیش از شروع مقصد: `2abf6474bbbb6d3089f62a625671b462a4009d4a344f7c0f5e76610de8304399`. هیچ رکورد، offset، نقش، پرداخت یا متن تجاری برای انتقال بازنویسی نشد.
- SHA-256 آرشیو خصوصی انتقال: `8df68af0253489bf246cdf0940cd0917c315a6794d767966cbec806d161ca4f3`. نسخهٔ خارج سرور آن در `work/eleven-host-transfer-20260907/runtime.tar.gz` باقی است. env و snapshot این پوشه نیز خصوصی‌اند و وارد Git نمی‌شوند. ردیف‌های بکاپ تاریخی با مسیر Windows برای provenance حفظ شدند؛ برای restore آن‌ها از فایل متناظر منتقل‌شده استفاده کنید، نه اجرای مسیر Windows روی Linux.
- پیش از start، `--check` با حساب `alonebot` هویت صحیح را تأیید کرد. سپس فقط unit اختصاصی شروع شد؛ هیچ `getUpdates` دستی یا poller آزمایشی با token واقعی اجرا نشد. offset اولیه `157801576` بود؛ پس از تعامل‌های واقعی به `157801587` رسید و pending صفر شد. webhook خالی، schema برابر ۱۱ و integrity/FK سالم ماندند. سه پیوست فعلی تیکت با همان bot ID توسط `getFile` قابل دریافت بودند.
- `bot_enabled=true` همان مقدار انتخاب‌شده توسط مدیر در مبدأ بود و حفظ شد؛ این rollout روش پرداخت، نقش مدیران، کاتالوگ، چیدمان، رنگ یا آیکون را عوض نکرد. callback کارت و کلید Plisio همچنان پیکربندی نشده‌اند و listener جدید یا پورت عمومی باز نشد. هشدار شناخته‌شدهٔ fallback منوی اصلی با ادامهٔ پردازش، جدا از سلامت میزبانی است.
- `systemd-analyze verify` موفق بود. restart کنترل‌شده در **۲۹٫۲۴۱ ثانیه** و داخل grace ۷۵ ثانیه تمام شد؛ unit دوباره `active/running`، `Result=success` و بدون restart خطادار بود. restart در خروج خطادار با انتظار ۱۵ ثانیه تنظیم و start-limit خاموش است تا outage طولانی startup، سرویس را برای همیشه متوقف نکند. boot واقعی یا crash اجباری روی سرور مشترک آزمایش نشد؛ enable شدن unit و restart خود ربات بررسی شدند.
- سرور میزبان پروژه‌های دیگر هم هست؛ هیچ container، سرویس نامرتبط، firewall یا تنظیم برق/خواب تغییر نکرد و میزبان reboot نشد. online بودن همچنان به روشن‌بودن و اینترنت همین سرور وابسته است، نه رایانهٔ Windows.
- release از Git bundle بررسی‌شده منتقل شد؛ مخزن Git و تاریخچه روی سرور وجود دارند، اما push تازه به GitHub عمومی انجام نشده است تا تصمیم محرمانگی مشخص شود. origin عمومی ممکن است از نسخهٔ مستقر عقب‌تر باشد؛ بدون تطبیق SHA از `origin/main` deploy نکنید.

### عملیات و rollback از این پس

start/stop/restart فقط با همین unit روی همین IP انجام شود. قبل از update، backup از **DB جاری سرور** بگیرید؛ آرشیو Windows بعد از cutover منبع دادهٔ زنده نیست. در rollback کد، همان DB جاری سرور حفظ می‌شود و ابتدا unit متوقف، commit سازگار و تست‌شده نصب، `--check` اجرا و یک instance شروع می‌شود. snapshot قدیمی را روی دادهٔ تازه restore نکنید. برگشت به Windows یا تغییر میزبان نیازمند درخواست صریح جدید است.

## سوابق پیش از انتقال میزبان

تصمیم صریح کاربر در ۲۰۲۶-۰۹-۰۶: از این پس مقصد انتشار `@ElevenaccountsTestbot`، bot ID برابر `8545042168` است. `@kheirytestrobot` / `8255103609` صرفاً مبدأ تاریخی و rollback است؛ انتخاب آن به‌عنوان مقصد جدید مجاز نیست مگر با درخواست تازهٔ مالک.

انتقال هویت ربات طبق [BOT_MIGRATION.md](BOT_MIGRATION.md) پیش‌تر انجام شد. انتشار اولیه در ۲۰۲۶-۰۹-۰۶ ساعت ۱۳:۰۳:۴۶، فعال‌سازی آیکون در همان روز ساعت ۱۷:۴۶:۵۰ و آخرین اجرای مبدأ Windows با اصلاح recovery شبکه در **۲۰۲۶-۰۹-۰۷ ساعت ۱۳:۳۹:۲۹** بود. این اجرای محلی در انتقال میزبان بالا متوقف شده است. مبنای انتشار آیکون commit `594ff8f5c8809a54abd91a21d7b48012fbdcb144` و اصلاح بعدی `_poll_batch` در `25caf31` ثبت شدند.

## بازیابی قطعی ناشی از 502 — ۲۰۲۶-۰۹-۰۷

- هنگام گزارش کاربر هیچ process رباتی وجود نداشت. آخرین traceback در log قبلی، `TelegramAPIError 502 in getUpdates` بود؛ پس از تمام‌شدن retry محدود در `call`، حلقهٔ قبلی خطا را عبور داده و process خارج شده بود. آخرین write آن log ساعت ۰۴:۴۱:۲۶ است؛ این زمان timestamp ثبت مستقیم exception نیست. علت قطعی را reboot نسبت ندهید؛ boot میزبان هنوز مربوط به روز قبل بود.
- خطا ابتدا در تست fake شبکه دقیقاً بازتولید شد. `_poll_batch` اکنون خطای شبکه/5xx/429 را با همان offset و filters، backoff سقف‌دار و stop-aware بازیابی می‌کند؛ retry_after معتبر حفظ می‌شود و خطاهای 401/409، handler و ارسال مبهم پیام شامل این retry نیستند. ۱۳ regression در `tests/test_polling_recovery.py` این قرارداد را پوشش می‌دهند.
- پیش از راه‌اندازی، backup سالم در `work/eleven_runtime_data/recovery/before-polling-recovery-20260907.sqlite3` با SHA-256 برابر `7bf527ad48da24ebde4e8b26bc844828c59536f14827392004571a8777042669` گرفته شد؛ integrity برابر `ok` و FK violation صفر بود. سپس `--check` هویت مقصد را تأیید کرد و فقط یک درخت launcher/worker با همان env/DB شروع شد.
- سه update معوق پردازش و offset از `157801485` به `157801488` رسید؛ webhook خالی و pending صفر شد. دادهٔ تجاری، نقش مدیران، رنگ `colored`، ۴۵ آیکون و `bot_enabled=false` حفظ شدند. سه Start به fallback امن منوی انتخاب رفتند؛ این هشدار منو علت crash 502 نیست. هیچ update دستی حذف یا offset به‌زور جلو برده نشد.
- بررسی زندهٔ بعدی، پردازش updateهای جدید تا offset `157801500` و افزایش journal مدیریتی completed از ۱۵ به ۲۲ را با pending صفر تأیید کرد؛ این تعامل‌های واقعی با ربات‌اند، نه دادهٔ تست تزریق‌شده به DB. بررسی integrity همچنان `ok` و FK violation صفر بود.
- در venv واقعی runtime، ۲۴ تست هدفمند recovery/Telegram/hygiene در ۴٫۳۱۷ ثانیه موفق شدند. lint کل پروژه، compile و `git diff --check` نیز موفق بودند. این شواهد جای ادعای آزمون ظاهری تمام کلاینت‌ها یا تضمین بدون‌باگ‌بودن کل محصول را نمی‌گیرند.
- مجموعهٔ کامل در venv QA همین Windows با `python -X utf8 -B -m unittest discover -s tests -q` در ۶۱۸٫۶۶۸ ثانیه تمام شد: ۴۹۱ مورد، ۴۸۹ موفق، ۲ skip و صفر failure/error. هر دو skip فقط تست permission bits مخصوص POSIX در `test_admin.py` و `test_db.py` هستند و روی Windows قابل اجرا نیستند؛ تست‌های recovery همگی اجرا و موفق شدند. tracebackهای سناریوهای تزریق خطا در خروجی تست، خطای runtime زنده نیستند.
- log جدید: `work/live_logs/eleven-polling-recovery-20260907T133929.*.log`، خارج مخزن. یک outbox ناموفق از تلاش قبلی اطلاعیه برای مدیرِ دسترس‌ناپذیر وجود دارد؛ این سابقه با recovery حاضر دوباره ارسال نشد.
- اصلاح حاضر روی runtime محلی اعمال شده است؛ push تازه‌ای به GitHub انجام نشده، چون مخزن عمومی است و تصمیم کاربر دربارهٔ تعارض انتشار عمومی با محرمانگی سورس هنوز مشخص نیست. نه visibility مخزن تغییر کرده و نه نتیجهٔ CI قدیمی به این patch نسبت داده می‌شود.
- rollback فقط پس از توقف scoped همین process، با نگهداری DB فعلی و نسخهٔ کد قبلی انجام می‌شود؛ schema تغییر نکرده و restore دیتابیس لازم نیست. rollback کد قبلی نقص تحمل outage طولانی را برمی‌گرداند. سرویس autorun یا میزبانی دائمی تازه‌ای ایجاد نشده است.

## فعال‌سازی آیکون اختصاصی با مالک صحیح — ۲۰۲۶-۰۹-۰۶

کاربر صریحاً **`@RoghayeHoseini`** را مالک Telegram معرفی کرد. تلاش قبلی با حساب bootstrap برنامه به `USER NOT FOUND` رسیده بود؛ این دو نوع مالکیت نباید یکی فرض شوند. بسته با حساب معرفی‌شده و chat ID پایدارِ تأییدشده از `getChat` منتشر شد. نقش‌های داخل پنل، `BOOTSTRAP_ADMIN_USERNAME` و مجوزهای سه مدیر تغییر نکردند.

- [بستهٔ منتشرشدهٔ Lucide Minimal](https://t.me/addemoji/LucideMinimala4a1765b_by_ElevenaccountsTestbot) شامل ۴۵ custom emoji است. شمارش، ابعاد ۱۰۰×۱۰۰، `needs_repainting` و شناسه‌ها با `getStickerSet` و `getCustomEmojiStickers` تأیید شدند. اجرای دوبارهٔ ناشر همان بسته را بازیابی کرد، نه بسته‌ای تازه.
- [manifest عمومی قابل‌استقرار](../assets/button-icons/elevenaccounts-testbot.json) همان ۴۵ نگاشت عملیاتی است؛ token، chat ID شخصی یا file ID خصوصی ندارد. نسخهٔ خصوصی آن در `work/eleven_runtime_data/button-icons.json` قرار دارد و برابری بایت‌به‌بایت این دو قبل از restart کنترل شد.
- در آزمون واقعی روی منوی مالک، هر هفت دکمه `icon_custom_emoji_id` مورد انتظار را در پاسخ API داشتند؛ رنگ، عنوان، عمل و ترتیب نیز برابر بودند. تنها canonicalization عادی `Https` به `https` در URL کانال برای مقایسه نرمال شد؛ مسیر URL و دادهٔ canonical تغییر نکرد.
- پس از بکاپ سالم، launcher/worker قبلی با Ctrl+C محدود به console همان درخت و با بررسی PID/path دقیق متوقف شدند. `--check` با ۴۵ آیکون و `colored` موفق شد و فقط یک poller مقصد با همان DB/offset شروع شد. schema و دادهٔ تجاری تغییر نکردند.
- worker واقعی دو اعلان تازه و منوی آیکون‌دار برای `@RoghayeHoseini` و `@Hooshmandsazanjavan1` فرستاد. هر دو `sent` شدند؛ درخواست همان markup به API برای هر دو `message is not modified` برگرداند و مطابقت خروجی runtime تأیید شد. outbox این اعلان‌ها هیچ ID عملیاتی آیکون را ذخیره نکرده است؛ تزئین در زمان ارسال انجام شده و canonical بعد از ارسال/بازبینی ثابت مانده است.
- ۶۵ تست مستقیم رنگ/آیکون/چیدمان/Telegram/اسناد/secret scan در venv runtime موفق شدند، از جمله regression تازهٔ manifest عمومی و digest دارایی‌های دارای مجوز. lint موفق است. نتیجهٔ مجموعهٔ کامل برای commit نهایی در [CI مخزن](https://github.com/mohamadkheiry/aibotTelegram/actions/workflows/ci.yml) بررسی شود. پیش‌نمایش خود دارایی‌ها در زمینهٔ روشن/تاریک بازبینی شد؛ این پیش‌نمایش اسکرین‌شات Telegram نیست.
- پس از restart: integrity برابر `ok`، FK violation صفر، webhook خالی، pending update صفر، همهٔ journalهای مدیر `completed` و همهٔ outboxها `sent` بودند. نمایش دقیق در نسخه/تم تلگرام مخاطب نیازمند مشاهدهٔ همان کلاینت است؛ موفقیت API ادعای رندر پیکسلی یکسان همهٔ کلاینت‌ها نیست.

بکاپ قبل از آیکون: `work/eleven_runtime_data/migration/before-icons-20260906.sqlite3` با SHA-256 برابر `8760e5773cc15df79c62f0f4b0c01a613382e67988b947d8be9ec7eca9b5de33`. rollback ظاهر با خالی‌کردن manifest و restart همان ربات انجام می‌شود؛ `BUTTON_COLOR_MODE=colored`، نقش‌ها، کاتالوگ، چیدمان و تاریخچه محفوظ می‌مانند.

## بازگردانی رنگ‌های سند — ۲۰۲۶-۰۹-۰۶

درخواست مستقیم مالک «روی ربات جدید اعمال کن رنگ‌ها رو» با تغییر `BUTTON_COLOR_MODE` از `theme` به **`colored`** اجرا شد. در بررسی پیش از انتشار هیچ poller فعالی وجود نداشت؛ boot میزبان ساعت ۱۶:۵۸:۴۶ بود و اجرای محلی پیشین پس از restart رایانه ادامه نیافته بود. برنامه با همان DB، همان bot ID و offset قبلی دوباره شروع شد؛ سرویس autorun تازه‌ای ساخته نشد.

- مطابق متن تکمیلی منبع («فقط فروشگاه سبز باشه»)، فروشگاه و کیف پول `success` / سبز و حساب من و دعوت و کسب درآمد `primary` / آبی هستند. پشتیبانی، کانال و پنل مدیریت رنگ پیش‌فرض کلاینت را نگه می‌دارند. رنگ‌های از قبل تعریف‌شدهٔ سایر صفحه‌ها نیز در ارسال حذف نمی‌شوند؛ ترتیب، callback، URL، contact و تنظیم چیدمان تغییر نکرده‌اند.
- ۶۴ تست مستقیم رنگ/keyboard/icon/layout/Telegram/مستندات و secret scan موفق شدند؛ شامل دو regression تازه برای حفظ رنگ در شش مسیر inline و مسیر reply/contact. lint نیز موفق است. کد تجاری و schema تغییر ندارند.
- قبل از شروع poller، دو منوی واقعی تازه از outbox برای `@RoghayeHoseini` و `@Hooshmandsazanjavan1` ارسال شد. پاسخ Telegram رنگ‌های مورد انتظار را برگرداند؛ درخواست مجدد همان markup برای هر دو پیام با `message is not modified` برابری منو را تأیید کرد. وضعیت دو اطلاعیه `sent` است؛ هیچ پیام آزمایشی به مشتری دیگر ارسال نشد.
- پس از شروع، فقط یک درخت launcher/worker برای مقصد وجود دارد؛ `getMe` صحیح، webhook خالی، سلامت DB برابر `ok`، FK violation صفر و صف update تخلیه شد. چند callback از زمان خاموشی در مرحلهٔ acknowledgment خطای ۴۰۰ داشتند؛ پردازش ادامه یافت و journal مدیر ناتمام نماند. مسیر fallback منوی اصلی همچنان ممکن است یک پیام انتخاب جداگانه بفرستد.
- رنگ پیام‌های تاریخی خودکار عوض نمی‌شود؛ کاربر باید `/start` یا منوی تازه را باز کند. تأیید پاسخ API جای بررسی پیکسلی در همهٔ نسخه‌ها و تم‌های Telegram را نمی‌گیرد. هیچ آیکون تازه‌ای در این rollout فعال نشده است.

بکاپ معتبر قبل از این تغییر: `work/eleven_runtime_data/migration/before-colors-20260906.sqlite3` با SHA-256 برابر `aa9b75c755647aa877b0882135d21cb68daab7eddd6d17f46fd87070e32545b1`. برای rollback ظاهر فقط با انتخاب مالک mode را تغییر و همان poller مقصد را restart کنید؛ DB، تاریخچه یا چیدمان restore/پاک نمی‌شوند.

## شواهد انتقال اولیه

- `getMe` هویت `8545042168` / `ElevenaccountsTestbot` را تأیید کرد؛ webhook خالی و commands فقط `start` هستند. updateهای معوق حذف نشدند و توسط poller مقصد پردازش شدند؛ offset جدید جلو رفت و pending update به صفر رسید.
- process مبدأ پس از بکاپ سالم با سیگنال graceful متوقف و نبود آن کنترل شد. مقصد فقط یک درخت process دارد: launcher محیط مجازی و child اجرای Python، نه دو poller. poller آزمایشی یا فراخوانی دستی `getUpdates` اجرا نشد.
- تست کامل [CI همین release](https://github.com/mohamadkheiry/aibotTelegram/actions/runs/34024889835) روی Linux: **۴۷۵ تست موفق** در ۸۸٫۳۰۲ ثانیه؛ lint، compile، secret scan، Docker build و smoke حجم دادهٔ writable نیز موفق. ۱۲ تست تازهٔ انتقال در venv مستقل مقصد Windows هم موفق‌اند و `pip check` آن محیط سالم است.
- در مقصد integrity برابر `ok` و foreign-key violation صفر است. ۴ کاربر، ۳ مدیر با نقش و اثبات هویت قبلی، ۲ دسته، یک تیکت با دو پیام، تمام تنظیمات تجاری و یک سابقهٔ بکاپ منتقل شدند. دادهٔ محصول/انبار/خرید/پرداخت/کیف پول در مبدأ وجود نداشت؛ دادهٔ آزمایشی به محیط زنده اضافه نشد.
- تصویر تیکت با file ID تازه در ربات مقصد دوباره با `getFile` قابل دریافت است. اصل فایل و journal نگاشت در محل خصوصی نگه‌داری می‌شوند. چهارده اعلان تاریخی همان وضعیت `sent` را حفظ کرده‌اند و دوباره ارسال نشده‌اند.
- دو اطلاعیهٔ جدید برای `@RoghayeHoseini` و `@Hooshmandsazanjavan1` از **همین ربات جدید** با دکمهٔ پنل مدیریت ارسال شدند؛ هر دو در outbox وضعیت `sent` و message ID برگشتی Telegram دارند. پس از اطلاعیه نیز دو update مدیریتی واقعی پردازش شدند و خطای جدید در log مشاهده نشد. این شاهد تحویل API و پردازش است، نه ادعای خوانده‌شدن پیام یا پذیرش بصری توسط همهٔ ادمین‌ها.

## وضعیت تاریخی پیش از انتقال میزبان و محدودیت‌ها

- در انتشار اولیه `bot_enabled=false` حفظ شده بود؛ مدیر بعداً آن را به `true` تغییر داد و در cutover میزبان همان مقدار جدید منتقل شد. مقدار زنده از پنل/DB جاری سرور خوانده شود؛ انتشار این تنظیم کسب‌وکاری را ضمنی تغییر نمی‌دهد.
- `BUTTON_COLOR_MODE=colored` و manifest شامل **۴۵ آیکون فعال** است. دکمه‌های inline با رنگ‌های تعریف‌شده و آیکون‌های اختصاصی ارسال می‌شوند؛ این CSS glass سفارشی نیست. تغییر manifest یا آیکون نباید رنگ‌ها را دوباره به `theme` تغییر دهد.
- `@RoghayeHoseini` مالک Telegram معرفی‌شده توسط کاربر و صاحب بستهٔ آیکون است. `@mohammadrezakheiry` فقط از نظر رکورد موجود برنامه همچنان نقش داخلی `owner` دارد؛ `getChat` او در مقصد خطای ۴۰۰ می‌دهد، بنابراین اطلاعیهٔ جدید به این حساب ارسال نشده است. این نبود دسترسی دیگر مانع انتشار آیکون نیست. تغییر نقش داخلی افراد یا انتقال مالکیت نیازمند درخواست جداگانه است و در rollout آیکون انجام نشده است.
- در نخستین Start، ویرایش markup خوشامدگویی یک بار به مسیر fallback رفت و پیام انتخاب جداگانه ارسال شد؛ update پردازش شد و تکرار خطا یا عقب‌ماندن صف مشاهده نشد. مشاهدهٔ ظاهر در کلاینت مالک پس از فعال‌سازی آیکون همچنان لازم است.
- میزبانی Windowsِ این بخش پایان یافته است؛ از cutover ۲۰۲۶-۰۹-۰۷، unit فعال و enabled روی سرور مصوب جایگزین آن شد. این سرور در شبکهٔ خصوصی است؛ اجرای دائمی نرم‌افزار جای برق/اینترنت پایدار یا تنظیمات فیزیکی میزبان را نمی‌گیرد.

## آرشیو خصوصی Windows؛ مبدأ سابق و نه مقصد انتشار

مسیرهای زیر نسبت به workspace **Windowsِ سابق** و خارج مخزن Git هستند؛ نگهداری آن‌ها برای provenance و بازیابی است، نه start دوباره یا overwrite دادهٔ سرور. token یا محتویات env/DB/نگاشت/log نباید commit شوند.

| مورد | محل |
|---|---|
| env مقصد | `work/eleven-token.env` |
| محیط Python مستقل مقصد | `work/eleven_venv`؛ نصب با `requirements-runtime.lock` |
| manifest فعال آیکون | `work/eleven_runtime_data/button-icons.json`؛ نسخهٔ عمومی قابل بازتولید در `assets/button-icons/elevenaccounts-testbot.json` مخزن است. |
| DB و فایل‌های مقصد | `work/eleven_runtime_data/alone_account.sqlite3` و زیرشاخهٔ `migration/assets` |
| آرشیو کامل مبدأ در لحظهٔ توقف | `work/eleven_runtime_data/migration/source-8255103609-20260906.sqlite3` |
| بکاپ تاریخی منتقل‌شده | `work/eleven_runtime_data/migration/historical-backup-20260905T141717Z.sqlite3`؛ فایل اصلی آن نیز محفوظ است. |
| DB و env مبدأ محفوظ برای بازیابی | `work/runtime_data/alone_account.sqlite3` و `work/runtime.env`؛ این‌ها مقصد انتشار تازه نیستند. |
| log مقصد | `work/live_logs/eleven-icons-594ff8f-20260906T174650.*.log`؛ log انتشارهای قبلی نیز محفوظ است. |

SHA-256 آرشیو کامل مبدأ: `41345142732d82a3d85793f9021e95320af3586cca2cc0bb8c16a936d5a413b5`. بکاپ تاریخی کپی‌شده: `0df4335519a929fd92d2a1143d9d1451ff5b5c9230b93a4f0752921046564bd4`. آرشیو با تمام ردیف‌های مبدأ مقایسه شد؛ مقصد نیز با طرح تبدیل صریح همهٔ جدول‌ها برابر بود. hash فایل DB زنده بعد از updateها تغییر می‌کند و معیار برابری byte-level آرشیو نیست.

از آنجا که مقصد اکنون دادهٔ جدید دریافت کرده، برای rollback صرفاً ربات قبلی را start نکنید: توقف مقصد، حفظ هر دو DB و reconciliation رخدادهای جدید طبق runbook الزامی است. ادامهٔ کار یا فعال‌سازی آیکون باید روی همین bot ID و DB مقصد انجام شود.

توکن در env خصوصی خارج مخزن باقی می‌ماند. ربات‌ها DB و offset مشترک ندارند. برای قابلیت آیکون، Start حساب مالک و موفقیت واقعی انتشار pack/ارسال باید تأیید شود؛ `getMe` به‌تنهایی Premium مالک یا دیده‌شدن آیکون را ثابت نمی‌کند.
