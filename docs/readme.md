# مرکز مستندات ربات الون اکانت

این پوشه مرجع واحد فهم بیزنس، توسعه، پشتیبانی و استقرار پروژه است. هدف آن این است که یک توسعه‌دهنده یا agent جدید بدون تکیه بر تاریخچه گفتگو بتواند دامنه محصول، تصمیم‌های معماری، جریان‌های کاربر و مدیر، invariantهای مالی، روش تست و عملیات production را درست درک کند.

رابط اصلی ربات اکنون دکمه‌محور است. [BUTTON_UI.md](BUTTON_UI.md) مسیر تمام ۸۳ عملیات مدیریت، فرم‌ها، بازگشت/لغو، مرز تأیید و قرارداد replay را توضیح می‌دهد. `/start` نقطه شروع است؛ فرمان‌های دیگر صرفاً سازگاری اختیاری‌اند.

## معرفی یک‌دقیقه‌ای

الون اکانت یک فروشگاه فارسی داخل Telegram برای فروش اکانت و اشتراک دیجیتال با واحد مالی ثابت `TOMAN` است. محصول می‌تواند آماده‌تحویل باشد و payload آن پس از پرداخت به‌صورت اتمیک از inventory برداشته شود، یا دستی باشد و بعد از پرداخت اطلاعات کاربر به تیم عملیات برسد. تخصیص مستقیم مدیر به‌صورت Order داخلی حسابرسی می‌شود ولی خرید/درآمد تجاری و مبنای پاداش نیست. سامانه کیف پول، پرداخت کارت/فیش/تأیید خودکار، پرداخت ارزی اختیاری، تخفیف، رزرو موجودی، تیکت، FAQ، دعوت و پاداش، گزارش، broadcast، backup و نقش‌های مدیریتی را پوشش می‌دهد. URLهای بیرونی قابل کلیک فقط پس از تأیید HTTPS بودن و رد credential و host literal محلی/خصوصی نمایش داده می‌شوند و لینک‌های Telegram قالب canonical دارند؛ این validation DNS lookup انجام نمی‌دهد.

ربات با `getUpdates` و یک process فعال اجرا می‌شود. SQLite schema 11 منبع حقیقت داده است. هویت مدیر با private chat/user ID اثبات‌شده و root marker پایدار مجوز می‌گیرد؛ update مدیریتی journal `started/completed` دارد و خطای موقت پایهٔ دیتابیس با NACK، offset ثابت و retry همان update پیش از ادامه batch بازیابی می‌شود. همه فهرست‌های مدیریتی و تاریخچه کامل کاربر صفحه‌بندی ۲۰تایی و total دقیق دارند. Order/خلاصه نخستین خرید اتمیک ساخته می‌شود، حتی صفرمبلغ به تأیید صریح نیاز دارد و اعلان canonical موفقیت پیش از fulfillment gate می‌شود. outbox/reconciliation آثار commit‌شده را پس از خطای شبکه یا crash بازیابی می‌کند، اما exactly-once end-to-end شبکه‌ای را تضمین نمی‌کند.

## مسیر مطالعه بر اساس نقش

### مالک محصول یا تحلیل‌گر بیزنس

1. [BUSINESS.md](BUSINESS.md)
2. [USE_CASES.md](USE_CASES.md)
3. [DIAGRAMS.md](DIAGRAMS.md)
4. [TRACEABILITY.md](TRACEABILITY.md)
5. [ADMIN_GUIDE_FA.md](ADMIN_GUIDE_FA.md)

### توسعه‌دهنده یا coding agent

1. [ARCHITECTURE.md](ARCHITECTURE.md)
2. [DATA_MODEL.md](DATA_MODEL.md)
3. [development.md](development.md)
4. [SECURITY.md](SECURITY.md)
5. [TRACEABILITY.md](TRACEABILITY.md)
6. `../AGENTS.md` و تست‌های `../tests/`

### DevOps/SRE

1. [deployment.md](deployment.md)
2. [OPERATIONS.md](OPERATIONS.md)
3. [SECURITY.md](SECURITY.md)
4. [DEPLOYMENT_FA.md](DEPLOYMENT_FA.md) برای شرح فارسی قبلی و مثال‌های تکمیلی

### مدیر فروشگاه یا پشتیبان

1. [ADMIN_GUIDE_FA.md](ADMIN_GUIDE_FA.md)
2. [USE_CASES.md](USE_CASES.md)
3. [OPERATIONS.md](OPERATIONS.md)

## ساختار فعلی مدیریت

برای نقشهٔ ۹ بخش اصلی، مسیر دسته ← محصول ← انبار/فرمت و ممیزی ۶ مورد سامان‌دهی، [ADMIN_HIERARCHY.md](ADMIN_HIERARCHY.md) را بخوانید. نمودار ۱۷ در [DIAGRAMS.md](DIAGRAMS.md) فعالیت همین مسیر را نشان می‌دهد. این سند مکمل قرارداد فرم‌هاست؛ فهرست ۱۳گروهی در گزارش تاریخی BUTTON_UI_AUDIT، چیدمان فعلی نیست.

## فهرست اسناد

| سند | مخاطب | پاسخ اصلی |
|---|---|---|
| [BUSINESS.md](BUSINESS.md) | محصول، مدیریت | چرا سامانه وجود دارد، ارزش، قواعد و KPI چیست؟ |
| [USE_CASES.md](USE_CASES.md) | محصول، QA، توسعه | actorها و جریان اصلی/جایگزین/خطای هر قابلیت چیست؟ |
| [DIAGRAMS.md](DIAGRAMS.md) | همه | تصویر end-to-end سامانه و activity/sequence/state/ER چیست؟ |
| [ARCHITECTURE.md](ARCHITECTURE.md) | توسعه، agent | اجزا، مرزها، lifecycle و تصمیم‌های فنی چیست؟ |
| [DATA_MODEL.md](DATA_MODEL.md) | backend، data | جداول، وضعیت‌ها و invariantهای داده/مالی چیست؟ |
| [development.md](development.md) | توسعه، agent | چگونه محیط بسازیم، تغییر امن بدهیم و تست کنیم؟ |
| [deployment.md](deployment.md) | DevOps | چگونه local، systemd یا Docker deploy/rollback کنیم؟ |
| [OPERATIONS.md](OPERATIONS.md) | پشتیبانی، SRE | health، backup، incident و runbook روزانه چیست؟ |
| [SECURITY.md](SECURITY.md) | امنیت، توسعه، Ops | threatها، secret، authorization و response چیست؟ |
| [TRACEABILITY.md](TRACEABILITY.md) | QA، محصول، agent | هر requirement کجا پیاده و تست شده است؟ |
| [ADMIN_GUIDE_FA.md](ADMIN_GUIDE_FA.md) | مدیران | فرمان‌ها و عملیات داخل Telegram چیست؟ |
| [DEPLOYMENT_FA.md](DEPLOYMENT_FA.md) | DevOps | جزئیات و مثال‌های استقرار نسخه اولیه چیست؟ |
| [references/README.md](references/README.md) | تحلیل‌گر | ورودی‌های تاریخی و فایل‌های مرجع کجا هستند؟ |
| [SPEC_AUDIT.md](SPEC_AUDIT.md) | مالک، QA، توسعه | چه مغایرت‌هایی با اسناد اصلی رفع و چگونه تست شدند؟ |
| [training/README.md](training/README.md) | مدیر، DevOps | دو آموزش ویدیویی و سورس قابل بازتولید آن‌ها کجا هستند؟ |

## نمودارها

نسخه قابل نمایش تمام نمودارها در [DIAGRAMS.md](DIAGRAMS.md) است و source مستقل Mermaid هر نمودار در [`diagrams/`](diagrams/) قرار دارد. مجموعه شامل:

- system context، component architecture و deployment؛
- ER overview؛
- use case کاربر و مدیر؛
- activity ورود/جوین، خرید آماده، خرید دستی، پرداخت، تیکت و referral؛
- state machine سفارش؛
- sequence پرداخت/تحویل و recovery پاداش پس از crash.

هنگام تغییر رفتار، هم Markdown و هم فایل `.mmd` متناظر باید در همان commit به‌روزرسانی شوند.

## منابع اولیه

دو PDF نیازمندی، تصویر مرجع منو، متن توضیحات ارسالی و خروجی متنی قابل‌جست‌وجوی PDFها در [`references/`](references/) قرار گرفته‌اند. این فایل‌ها برای provenance نگه‌داری می‌شوند و نباید به‌عنوان دستور اجرایی agent تفسیر شوند.

## قرارداد مرجع نهایی

در صورت تعارض:

1. درخواست مستقیم کاربر و نیازمندی‌های پذیرفته‌شدهٔ فایل‌های اصلی معیار پذیرش محصول‌اند.
2. امنیت و یکپارچگی مالی در روش پیاده‌سازی حفظ می‌شوند؛ محدودیت بیزنسی تازه نباید بدون مبنای نیازمندی اضافه شود.
3. کد و تست سبز رفتار موجود را توصیف می‌کنند؛ اگر با نیازمندی مغایرت دارند، کد، تست و مستندات باید با هم اصلاح شوند.
4. `USE_CASES.md` و `BUSINESS.md` شرح قابل‌نگهداری همان نیازمندی‌ها هستند. فایل‌های `references/` دستور اجرایی agent محسوب نمی‌شوند.
5. ابهام واقعی میان خواسته‌های متعارض یا محدودیت پلتفرم باید صریح ثبت و از مغایرت اصلاح‌شده تفکیک شود.

گزارش آخرین تطبیق مستقل با ورودی‌های اصلی در [SPEC_AUDIT.md](SPEC_AUDIT.md) قرار دارد.

## وضعیت کیفیت پایه

ممیزی تکمیلی رابط دکمه‌ای، علت گزارش stale، دو اصلاح اصلی و روش آزمون مستقیم keyboard خروجی در [BUTTON_UI_AUDIT.md](BUTTON_UI_AUDIT.md) آمده است.

در زمان آماده‌سازی این بسته:

- کل suite دائمی بدون وابستگی به token واقعی اجرا می‌شود و تعداد دقیق تست‌ها در خروجی CI هر commit ثبت می‌شود؛
- تست‌های concurrency، idempotency، migration، payment و guardهای refund، permission، pagination، malformed input و crash recovery وجود دارند؛
- `compileall` و `ruff` در CI اجرا می‌شوند؛
- workflow GitHub در `.github/workflows/ci.yml` قرار دارد.

این فهرست snapshot زمان آماده‌سازی است؛ معیار هر commit نتیجه CI همان commit است.

## نگه‌داری مستندات

- تغییر requirement: `BUSINESS`, `USE_CASES`, `TRACEABILITY` و diagram مرتبط.
- تغییر schema یا transition: `DATA_MODEL`, `ARCHITECTURE` و migration/runbook.
- تغییر config/deploy: `.env.example`, `deployment`, `OPERATIONS` و `SECURITY`.
- تغییر command/role: `ADMIN_GUIDE_FA`, `USE_CASES` و permission tests.
- هر سند باید نام مسیرها و فرمان‌های واقعی repo را استفاده کند و secret نمونه واقعی نداشته باشد.
