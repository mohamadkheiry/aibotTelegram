# مقصد انتشار جاری

## یکتایی پاداش محصول — ۲۰۲۶-۰۹-۱۳، ساعت ۱۱:۲۱ تهران

کد `8e2b9eee9d0576c07c4452e79fcdaa026ddc992f` (تغییر اجرایی `17ce4cbb380ba7b457769eedcb369c91f4274922`) روی `192.168.10.111` در `/opt/alone-account-bot` برای `@ElevenaccountsTestbot` منتشر شد. schema همان ۱۲ است. این بخش وضعیت جاری و بخش‌های پایین سابقه‌اند.

- یک مغایرت بیزنسی رفع شد: جلوگیری تراکنشی از ثبت و فعال‌سازی دو قانون پاداش صریح یک محصول در بازه مشترک، شامل فیلتر محصول combined. ثابت/درصدی جایگزین یکدیگرند؛ قوانین غیرفعال، بازه‌های جدا و سوابق حفظ شدند. توضیح فارسی در فرم و خطای تأیید افزوده شد.
- ۱۰ تست تازه (۸ دامنه و ۲ فرم واقعی) افزوده شد. اجرای کامل Linux روی همین commit: **۵۸۰ تست، ۷۰٫۵۹۷ ثانیه، OK، بدون skip**. دور قبلی یک fixture نمایش قدیمیِ دارای پاداش‌های متداخل داشت؛ fixture با محصول دوم و حفظ تمام assertions اصلاح و کل suite دوباره اجرا شد. تست‌های هدفمند Windows، lint، compile، لینک/نمودار و hygiene پاس شدند. اجرای کامل قدیمی Windows پس از تغییر fixture متوقف شد؛ مبنای پذیرش کامل، Linux است.
- backup آنلاین: `/srv/backups/alone-account-bot/feedback-20260913-074038/before-release.sqlite3`، SHA-256 `c42c44fa8846decc09b061120ba3fd53363476e9bef9387e9e3e0a85e858f8e9`.
- backup نهایی با سرویس متوقف و MainPID=0: `/srv/backups/alone-account-bot/feedback-20260913-075015/before-release.sqlite3`، SHA-256 `576144b66ac43c8feb43f2dcb80dea52f095095fe382fdbe6f0eda8950b3726a`. هر دو integrity=ok، FK=0 و restore ایزوله با تطبیق fingerprint تأیید شدند.
- `--check` با حساب `alonebot` و env واقعی موفق؛ سرویس پس از start فعال، PID `2854286`، NRestarts=0. بررسی خواندنی: هویت صحیح، webhook خالی، pending_updates=0، schema12، integrity=ok، FK=0، bot_enabled=true، رنگ `colored` و ۴۵ آیکون. outbox همان ۱۸ failed تاریخی و ۱۱۳ sent؛ candidate عقب‌مانده پاداش صفر.
- قبل از انتشار هیچ قانون پاداش فعالی در DB زنده وجود نداشت؛ قانون/پاداش/کیف پول واقعی برای تست ساخته یا اصلاح نشد. نسخه و مستندات در origin/main ثبت شدند. rollout به داده‌های قبلی دست نمی‌زند؛ rollback کد guard جدید را برمی‌دارد و نباید با restore دیتابیس قدیمی همراه شود.
- systemd هنگام stop/start هشدار `NeedDaemonReload=yes` داشت؛ unit و drop-inهای میزبان در این تغییر ویرایش یا reload سراسری نشدند. سرویس با تنظیم بارگذاری‌شده فعلی راه‌اندازی و سلامت آن تأیید شد. تغییرات احتمالی بیرون از این انتشار باید پیش از reload جدا بررسی شوند.
- راهنمای برگشت دستی پاداش و سؤال اولویت پاداش عمومی/محصول و مبلغ اضافه‌واریزی به کارفرما ریپلای شد. سیاست تازه کم/اضافه‌واریزی و اولویت قواعد عمومی در این انتشار اجرا نشده؛ [تصمیم‌های مالی و موارد باز](FINANCIAL_DECISIONS_2026-09-12.md).
- نتیجه انتشار و ۵۸۰ تست در گفت‌وگوی کارفرما در ساعت ۱۱:۲۲ تهران ارسال و حضور پیام با نشان ارسال در Telegram Web K تأیید شد؛ خوانده‌شدن پیام یا پاسخ به دو سؤال باز هنوز تأیید نشده است.

## انتشار قواعد مالی و لغو سفارش — ۲۰۲۶-۰۹-۱۲، ساعت ۱۳:۱۵ تهران

کد **`856eb256c9f17d60916ee1c9a0401d5dca0e23d5`** روی **`192.168.10.111`** در
`/opt/alone-account-bot` برای **`@ElevenaccountsTestbot`** منتشر شد. این بخش
وضعیت جاری است و بخش‌های پایین شواهد تاریخی‌اند. schema از ۱۱ به **۱۲** ارتقا
یافت.

- دو گروه بازخورد مالی/لغو سفارش به هشت اصلاح فنی تبدیل شدند: پاداش خرید فقط
  پس از تکمیل و تحویل موفق؛ مبلغ ثابت یا درصدی؛ مبنای مبلغ قبل از تخفیف با گردکردن
  رو به پایین؛ سقف اختیاری؛ امکان بدهکارشدن فقط برای اصلاح حسابرسی‌شده مدیر؛ لغو
  صریح سفارش پرداخت‌نشده با تأیید؛ آزادسازی وجه رزروشده و کد تخفیف؛ و حذف دکمه
  لغو پس از ایجاد درخواست پرداخت بیرونی. جزئیات و تصمیم‌های باز در
  [FINANCIAL_DECISIONS_2026-09-12.md](FINANCIAL_DECISIONS_2026-09-12.md) ثبت شده‌اند.
- Windows: مجموعه کامل پیش از آخرین guard رابط **۵۶۹ تست در ۹۴۰٫۶۴۱ ثانیه**،
  بدون failure/error و با دو skip صرفاً POSIX؛ سپس دو تست رگرسیون لغو روی کد نهایی
  در ۲٫۸۵۱ ثانیه موفق شدند. compile، Ruff و `git diff --check` نیز موفق بودند.
  Linux میزبان: checkout دقیق همین commit، **۵۷۰ تست در ۷۰٫۳۴۶ ثانیه**، بدون
  failure/error/skip و compile موفق؛ Ruff در venv سرور نصب نیست و نتیجه Ruff
  مربوط به محیط توسعه است.
- کد و اسناد روی `main` مخزن GitHub کاربر push شدند. checkout عملیاتی دقیقاً همین
  commit است؛ فایل env، token، DB و helperهای خصوصی وارد گیت نشدند.
- backup آنلاین پیش از توقف:
  `/srv/backups/alone-account-bot/feedback-20260912-093939/before-release.sqlite3`
  با SHA-256 `1acab58f26816302e5bb65a662e57e509f0ea59d9deb22a2af003b22384bf2ca`.
  backup نهایی در حالت توقف:
  `/srv/backups/alone-account-bot/feedback-20260912-094358/before-release.sqlite3`
  با SHA-256 `2c1b291704141f290f077fc9fdea961e085c7a3a554373cf27dacc1a2f7d3818`.
  هر دو schema ۱۱، integrity=ok، FK=0 و restore ایزولهٔ برابر داشتند. snapshot
  پس از migration در
  `/srv/backups/alone-account-bot/feedback-20260912-094457/before-release.sqlite3`
  با SHA-256 `fb36544fbad92a8cd35010561d67b2a43ad3a05e45bf087cd100b5e23b048483`
  نیز schema ۱۲، integrity=ok، FK=0 و restore تأییدشده دارد.
- `--migrate-only` و `--check` با حساب `alonebot` موفق بودند. سرویس از
  **۱۳:۱۵:۰۴** active/running است؛ PID آغاز انتشار `3235467`، NRestarts=0 و فقط
  یک process `python -m app.main` دیده شد. journal فقط شروع getUpdates با offset
  محفوظ `157802930` را ثبت کرد و خطای تازه‌ای نداشت.
- health نهایی: getMe برابر `ElevenaccountsTestbot`، webhook خالی، pending update
  صفر، bot_enabled=true، schema=12، integrity=ok، FK=0، رنگ `colored` و هر ۴۵
  آیکون سفارشی فعال‌اند. هیچ خرید/پرداخت/نقش یا دادهٔ تجاری برای تست در production
  ساخته نشد. ۱۸ outbox failed تاریخی پاک یا دوباره‌ارسال نشدند؛ outbox sent در
  لحظه health برابر ۱۱۳ بود.
- مطابق آخرین درخواست کاربر، تلگرام کارفرما به‌صورت دوره‌ای بررسی نشد و پیام
  تازه‌ای نیز در این انتشار ارسال نشد؛ بررسی بعدی فقط با دستور صریح کاربر انجام
  می‌شود.

Rollback کد به **`70e04854230f0e1d7db47b3d98423f5fca9a20a1`** فقط پس از توقف همین unit،
حفظ DB جاری و بررسی سازگاری schema ۱۲ انجام شود. snapshot schema ۱۱ را روی دادهٔ
جدید restore نکنید؛ rollback ایمن باید migration رو به جلو و داده‌های ثبت‌شده پس
از انتشار را حفظ کند.

## انتشار پیگیری — ۲۰۲۶-۰۹-۰۹، ساعت ۰۹:۴۹ تهران

کد **`34084f698065c5590180bd20e5e07e896eb17f2f`** روی **`192.168.10.111`** در `/opt/alone-account-bot` برای **`@ElevenaccountsTestbot`** منتشر شد. این بخش وضعیت جاری است؛ بخش‌های پایین تاریخی‌اند. schema همچنان ۱۱ است.

- [هشت گروه اصلاح رابط MR-01 و MR-03..09](PENDING_FEEDBACK_2026-09-09.md) شامل FAQ درختی، جست‌وجو، نمایش/اعلان تیکت، بازگشایی و دکمه کوتاه تراکنش. مجموع گزارش‌شده با دورهای قبل ۳۹ گروه اصلاح/بهبود است، نه ادعای صفر باگ. MR-02 بازتولید نشده؛ قواعد مالی و cashback فاز دوم کامل‌شده نیستند.
- artifact دقیق در checkout خصوصی Linux: **۵۶۳ تست در ۶۹٫۴۱۶ ثانیه، بدون failure/error/skip**؛ ۱۷ تست جدید. Ruff، compile، hygiene/secret scan، اسناد و pip check موفق. اجرای کامل اولیه ۱۹ failure و ۱ error داشت: ۱۵ failure و ۱ error از نبود `.git` در استخراج tar برای hygiene، دو fixture بدون FAQ واقعی، انتظار عنوان قدیمی تراکنش و یک ایراد پیام فهرست FAQ خالی. QA نهایی از bundle واقعی clone شد؛ ایراد نمایش اصلاح و تست‌های قرارداد جدید تکمیل شدند. آن اجرای اولیه شاهد پذیرش نیست.
- SHA-256 آرشیو نهایی `a57e1ee27870a685b238e83bb632b37d03c34e632b649276acb3c9ef1ca85c83` و bundle خصوصی `388128e16eb18a02d0385e998269e52fb2a63d6d1958e70f15cc2ed7660afc39` است؛ hash مقصد و Git bundle verify موفق. فایل‌های Python و schema نصب‌شده با checkout تست‌شده برابرند. push عمومی تازه به علت تصمیم محرمانگیِ باز انجام نشد.
- backup آنلاین `/srv/backups/alone-account-bot/feedback-20260909-061526/before-release.sqlite3` با SHA-256 `497363cedeac04b29536c807d5349b9129231145aa4ea9f8b29e72a71a2451b8`؛ backup نهایی پس از توقف در `/srv/backups/alone-account-bot/feedback-20260909-061814/before-release.sqlite3` با SHA-256 `0b1a0b242985c84efd540024a4a5eba61564eada701d17c91ff70935370ec344`. هر دو integrity=ok، FK=0 و restore ایزوله با مقایسه همه ردیف‌ها و schema تأیید شدند.
- unit قبل از checkout inactive/dead و PID صفر بود. فقط app source برای alonebot group-readable شد؛ env/data/manifest تغییر نکردند. `--migrate-only` و `--check` با حساب سرویس موفق بودند. fingerprint کل DB پس از preflight با manifest بکاپ توقف برابر بود: `d58f77f7e7c727dc072e860854f4a01f899770a8951cb73f5a1a7ceef5ae6a37`.
- سرویس از **۰۹:۴۹:۰۲** active/running، enabled، یک PID `2742938` و NRestarts=0 است. getMe هویت درست، webhook خالی، pending=0، integrity=ok، FK=0، bot_enabled=true، colored و ۴۵ آیکون را تأیید کردند؛ offset `157802930` بود. پس از start هشدار/error تازه‌ای در journal unit دیده نشد. هیچ poller Windows/getUpdates دستی، reboot یا تغییری در پروژه‌های دیگر انجام نشد.
- آزمون‌های mutation روی fixture و Telegram ساختگی‌اند؛ خرید، پرداخت، تیکت یا نقش production برای تست تغییر نکرد. این دور ادعای تست پرداخت بانکی زنده یا بازبینی پیکسلی همه کلاینت‌ها ندارد.
- ۱۸ outbox failed و ۱۱۰ sent در بکاپ پیش از start و health بعد از start یکسان بودند؛ این failureهای تاریخی برای انتشار تازه requeue/پاک نشدند و خطای تازه این release شمرده نمی‌شوند.
- تبدیل بیرونی هر سه صوت کامل شد و نیازمندی‌ها استخراج شدند. هنوز پیام سوال‌ها یا اطلاعیه تازه‌ای از حساب شخصی برای کارفرما ارسال نشده؛ متن دقیق منتظر تأیید اقدام در ابزار Windows است. پنج acknowledgment قبلی نباید تکرار شوند.

Rollback فقط کد به **`f4ece52ca572b8ac5aa442de5c331692eb26babc`**، با توقف همین unit و حفظ DB جاری، کنترل دسترسی، `--check` و start یک poller است. snapshot تاریخی را روی سفارش‌های تازه restore نکنید. commitهای صرفاً مستنداتی پس از این SHA نیازمند restart نیستند.

## انتشار پیگیری — ۲۰۲۶-۰۹-۰۸، ساعت ۱۸:۳۴ تهران

کد **`7c7287604584bce169248039c49191ba2261a549`** روی **`192.168.10.111`** در `/opt/alone-account-bot` برای **`@ElevenaccountsTestbot`** منتشر شد. این بخش جایگزین SHA عملیاتی بخش‌های تاریخی پایین است؛ schema همچنان ۱۱ است.

- [شش گروه پیگیری FU-01..06](FOLLOWUP_FEEDBACK_2026-09-08.md) شامل پنج گزارش متنی تازه و دسترسی پاداش از محصول؛ درصد/سقف، زمان واریز و قواعد مالی مبهم همچنان باز هستند. شمار تجمعی گزارش‌های دو دور **۳۱ گروه اصلاح/بهبود** است، نه ۳۱ باگ مستقل یا ادعای پایان همه نیازمندی‌ها.
- artifact نهایی در Linux میزبان مصوب: **۵۴۶ تست در ۶۶٫۱۷۷ ثانیه، بدون failure/error/skip**؛ Ruff، compile، hygiene/secret scan، سازگاری اسناد و pip check موفق. ۲۷ تست افزوده نسبت به ۵۱۹ تست قبلی. دو شکستِ دور اول (fixture پاداش و تشخیص AST اعلان مدیر) اصلاح و کل مجموعه دوباره اجرا شد. Windows نیز ۳۹ تست اصلاحات/پوشش و ۳۰ تست آیکون/اسناد/hygiene را پس از اصلاح گذراند؛ اجرای کاملِ اولیه Windows با دو شکست و دو skip، شاهد پذیرش نهایی نیست.
- hash آرشیو تست‌شده: `0cec6a0c871369a2c92a14e9c00dfdfd477e255e0ec15cca05b4b36bca249109`. Git bundle خصوصی با hash `e39ef7e0456c9ffb651ff5ebc0f1b0645258aebdb1352323425d6f6f669b6d8a` بررسی و منتقل شد. فایل‌های app نصب‌شده با artifact کاملِ تست‌شده برابرند. هیچ push عمومی تازه انجام نشد؛ تعیین تکلیف محرمانگی هنوز لازم است.
- backup آنلاین `/srv/backups/alone-account-bot/feedback-20260908-145011/before-release.sqlite3` با SHA-256 `e307cbb26e5ea5f41d0987ebbe32a57b5441547d2798ecc353d3275aac9970fc`؛ backup نهاییِ توقف در `/srv/backups/alone-account-bot/feedback-20260908-150232/before-release.sqlite3` با SHA-256 `549283e3e92f257ebd5bd5624e42e04603fc8de9327cc621b9f2671333047f8e`. هر دو integrity=ok، FK=0 و restore ایزوله در حافظه با مقایسه همه ردیف‌ها تأیید شدند؛ فایل‌ها خصوصی/root هستند.
- پیش از checkout، service `inactive/dead` و PID صفر بود. checkout دقیق، group-read فایل‌های Python داخل app، `--migrate-only` و `--check` با حساب alonebot موفق بودند. entry point درست `python -m app.main` است؛ فراخوانی اولیه اشتباه `-m app` پیش از بازکردن DB خطا داد و اصلاح شد. fingerprint کامل DB پس از preflight با manifest بکاپ برابر بود: `d60ee43f7b00ce7dce99bcf0113f7f9b6fdc4e64749042b0596a16fcb6f3c34e`. مقایسه از manifest انجام شد؛ رونویسی نخست hash از ترمینال دارای wrap یک نویسه اضافه داشت، نه تغییر داده.
- سرویس از **۱۸:۳۴:۰۹** active/running، PID `107266`، NRestarts=0 است. getMe هویت صحیح، webhook خالی و pending=0؛ integrity=ok، FK=0، bot_enabled=true، colored و ۴۵ آیکون حفظ شده‌اند. offset اولیه پس از start `157802678` است؛ هیچ getUpdates دستی یا poller محلی اجرا نشد. شماره تازه با اولین سفارش واقعی تخصیص می‌یابد؛ برای آزمون شماره هیچ خرید production ساخته نشد.
- ۱۳ outbox failed تاریخی و ۸۵ sentِ پیش از نصب حفظ شدند؛ requeue یا پاک نشدند. این اعداد به معنی ۱۳ خطای تازه در release نیستند. تست مالی/خرید/پیوست روی fixture بوده، نه پرداخت بانکی زنده یا بررسی پیکسلی تمام کلاینت‌ها.
- این دور پیام پایان تازه‌ای برای کارفرما یا مدیران نفرستاد؛ پنج acknowledgment قبلی تکرار نمی‌شوند. موارد مالیِ باز باید در اطلاعیه صریح باشند.

Rollback قبل از برگشت به `d6504cc` نیازمند بررسی **تمام collectionهای چندپیامی** است؛ نسخه قبلی append/collecting را نمی‌شناسد و می‌تواند اطلاعات را جایگزین کند. ابتدا سازگاری این مسیر را نگه دارید یا با مالک مجموعه‌ها را تعیین تکلیف کنید. DB جاری و شمارنده محفوظ باشند؛ backup قدیمی روی سفارش‌های تازه restore نشود. [قرارداد rollback](FOLLOWUP_FEEDBACK_2026-09-08.md).

## انتشار بازخورد کارفرما — ۲۰۲۶-۰۹-۰۸

کد **`6220e1e3dee822603366b88d637552a75311e117`** در `/opt/alone-account-bot` روی میزبان مصوب `192.168.10.111` منتشر شد؛ ربات همچنان `@ElevenaccountsTestbot` است. service از **۱۵:۲۹:۱۸ به وقت تهران** active/running، enabled و با یک poller است. SHAهای قدیمی پایین، شواهد تاریخی‌اند؛ این بخش مرجع release جاری است.

- مجموعه کامل fixture در Linux همین میزبان: **۵۱۹ تست در ۶۳٫۲۱۶ ثانیه، بدون failure/error/skip**؛ ۲۸ تست تازه برای گزارش‌ها. Ruff، compile، آزمون اسناد و pip check موفق بودند. کد/تست/dependency در bundle دقیقاً همان artifact آزمایش‌شده‌اند؛ فقط دو متن مستند پس از تست، نتیجه آزمون و توضیح reminder را ثبت کردند و تست اسناد دوباره اجرا شد.
- [۲۵ گروه اصلاح/بهبود و موارد باز](CLIENT_FEEDBACK_2026-09-08.md). schema همچنان ۱۱؛ رنگ colored و ۴۵ آیکون حفظ شدند؛ روش callback بانک و کلید Plisio هنوز تنظیم نشده‌اند. پاداش خرید شخصی و تأیید کم‌واریزی فعال نشده‌اند.
- backup آنلاین: `/srv/backups/alone-account-bot/feedback-20260908-115609/before-release.sqlite3`، SHA-256 `9673f0e87152b7b54acd6987be97528dcf20e28c0ffc31c807f63994932ac87a`. backup نهایی پس از توقف: `/srv/backups/alone-account-bot/feedback-20260908-115740/before-release.sqlite3`، SHA-256 `6e07503de295e823faf4df50ab213219a0ec239a1d08de977d7c20c1cd0a0f4e`. هر دو خصوصی/root، integrity=ok، FK=0 و restore ایزوله در حافظه با fingerprint همه ردیف‌ها تأیید شدند.
- قبل از checkout، unit کاملاً inactive/dead و PID صفر بود. پس از checkout، بررسی با حساب alonebot مجوز ناکافی فایل تازه را آشکار کرد؛ فقط گروه/مجوز خواندن فایل‌های Python در پوشه app اصلاح شد. env، DB و secret دست‌نخورده ماندند. migrate-only و check موفق شدند و **fingerprint کل DB پیش/پس نصب برابر بود**. سپس فقط همان unit آغاز شد، NRestarts=0.
- health پس از start: getMe درست، webhook خالی، pending updates صفر، integrity=ok، FK=0 و سه پیوست قدیمی تیکت با getFile معتبر بودند. offset از مقدار محفوظ `157802471` با تعامل زنده جلو رفت. هیچ poller محلی یا getUpdates دستی اجرا نشد.
- آزمون زنده در حساب معرفی‌شده کاربر: `/start` یک پیام inline، رنگ/آیکون خوانا، پنل ۹بخشی، منوی جمع‌وجور کاربران، انتخاب کاربر و جزئیات زمان تهران و دکمه جست‌وجوی مجدد بررسی شدند. این smoke فقط خواندنی بود؛ خرید، تغییر موجودی، پرداخت، نقش یا تیکت آزمایشی واقعی ایجاد نشد. آزمون mutation/خطاها روی fixture بوده است، نه ادعای پرداخت بانکی واقعی.
- هیچ ریپلای پایان کار یا اطلاعیه به کارفرما/مدیر دیگر در این دور ارسال نشده: سه صوت هنوز متن ندارند و سیاست کم‌واریزی باز است. بهبود UX نباید به‌عنوان خارج‌ازسندبودن اصل قابلیت اعلام شود.
- commit و bundle خصوصی ثبت/منتقل شدند؛ push تازه به GitHub عمومی به دلیل ابهام محرمانگی انجام نشد. SHA-256 bundle: `575d0f169b3241614efc79447ff80b727c5b59db3c582edca7362a33f5a6250a`.

Rollback کد به **`e083827c4cbdcc8996e7b03fac57e23dbe382f08`** با توقف همین unit، حفظ DB جاری، بررسی دسترسی فایل‌ها، `--check` و شروع یک poller است. DB قدیمی روی سفارش‌های تازه restore نشود. متن/layout تازه در UI قدیمی ممکن است کامل نمایش داده نشود. تغییرات بعدی صرفاً اسنادی به restart نیاز ندارند.

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
