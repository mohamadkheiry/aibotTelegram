# ممیزی سرتاسری تطبیق با اسناد

این گزارش شواهد ممیزی پیش از انتقال را نگه می‌دارد. ربات مقصد پس از این ممیزی به `@ElevenaccountsTestbot` منتقل شده است؛ وضعیت زنده، نتایج آزمون انتشارهای بعدی، اطلاع‌رسانی مدیران و فعال‌سازی آیکون‌های اختصاصی در [CURRENT_DEPLOYMENT.md](CURRENT_DEPLOYMENT.md) ثبت شده‌اند. اشاره‌های تاریخی این گزارش به مقصد قبلی، دستور انتشار تازه نیستند.

تاریخ: ۲۰۲۶-۰۹-۰۶. نتیجهٔ این دور: **چهار مغایرت مستقل اصلاح شد**؛ سه سفر متصل مشتری/مدیر و تست‌های بازگشت باگ اضافه شدند. این شمارش شامل باگ‌های رفع‌شده در دورهای قبل، تعداد فایل‌ها یا چند لایهٔ محافظت یک باگ نیست.

## مبنا و روش

هر چهار صفحهٔ «توضیح امکانات ربات تلگرام الون اکانت» و هر چهار صفحهٔ «توضیحات ربات فروشگاهی» هم به‌صورت متن و هم تصویر صفحه بازخوانی شد؛ متن تکمیلی و تصویر منو نیز مرجع بودند. نسخه‌های اصلی و هش آن‌ها در [references](references/README.md) محفوظ‌اند و بازنویسی نشده‌اند. دستورهای اجرایی در منابع، مجوز اجرای عملیات نیستند.

درخواست‌های مستقیم بعدی کاربر نیز بخشی از معیار پذیرش‌اند: رابط دکمه‌محور، مدیریت ۹بخشی و درخت محصولات، تمام داده‌های محصول/انبار/فرمت، صفحهٔ جوین اجباری، وضعیت واضح روش پرداخت، چیدمان قابل‌تغییر همهٔ صفحه‌های مشتری و واگرد، و ظاهر اختیاری Premium. یک کنترل خاموش/روشن از صفحهٔ اول هر دو PDF می‌آید؛ دکمهٔ مستقل و تکراری تعمیرات جزو نیازمندی اصلی نیست.

ابتدا مجموعهٔ قبلی بدون تغییر اجرا شد، سپس رفتارهای محل اتصال مسیرها با سند تطبیق و مغایرت‌ها بازتولید شدند. در [test_spec_end_to_end.py](../tests/test_spec_end_to_end.py)، click از دکمه و message ID صادرشدهٔ خود برنامه گرفته می‌شود؛ فرم، کنترل مجوز، منطق سفارش، SQLite و outbox واقعی‌اند. فقط ارسال/دریافت Telegram ساختگی است. هیچ خرید، جابه‌جایی پول، تغییر وضعیت، پیام گروهی یا poller آزمایشی روی محیط زنده انجام نشد.

## چهار اصلاح این دور

| شناسه | مغایرت | اصلاح و شاهد |
|---|---|---|
| E2E-01 | سفارش ready در انتظار موجودی با وضعیت `processing` دکمهٔ «ارسال اطلاعات» مخصوص manual را نشان می‌داد؛ callback تاریخی هم فرم نامعتبر باز می‌کرد و state قبلی باقی می‌ماند. | شرط snapshot نوع manual در نمایش، callback و state؛ بررسی دامنهٔ نهایی حفظ شد. سه تست `test_ready_restock_order_never_displays_manual_information_button`، `test_legacy_ready_information_button_is_rejected_before_collecting_data` و `test_ready_information_state_from_old_release_is_cleared_without_mutation`. مبنا: تفکیک تحویل آماده/فعال‌سازی دستی در PDFها و متن تکمیلی. |
| E2E-02 | اگر ربات حین فرم خاموش یا عضویت نامعتبر می‌شد، خروج متنی «لغو و بازگشت» بدون guard منوی عمومی را نشان می‌داد. | اجرای guard پیش از منو؛ پاک‌شدن state حتی در صورت رد دسترسی، بدون لغو مالی. `test_cancel_cannot_bypass_disabled_bot_or_forced_join` با دو حالت خاموشی و جوین. مبنا: کنترل دسترسی عمومی و جوین اجباری، صفحهٔ اول هر دو PDF. |
| E2E-03 | عنوان فیلتر inactive می‌گفت «بدون خرید»، درحالی‌که query آخرین فعالیت کاربر را بررسی می‌کرد. | عنوان «بدون فعالیت در چند روز اخیر»؛ تست دو کاربر بدون خرید، یکی فعال و دیگری قدیمی، نتیجهٔ درست را اثبات می‌کند: `test_inactive_filter_is_labelled_by_activity_not_purchases`. مبنا: تفکیک کاربران فعال/غیرفعال و خریداران در مدیریت کاربران PDF اول. |
| E2E-04 | دو عنوان فرم combined دعوت‌کننده و زیرمجموعه دارای خرید را نام می‌بردند، ولی قواعد، دعوت‌های خودِ خریدار معرفی‌شده و وضعیت qualified را ارزیابی می‌کنند. | نمایش «دعوت‌های ثبت‌شدهٔ خریدار» و «دعوت‌های واجد پاداشِ خریدار»؛ معنای قاعدهٔ موجود و پرداخت تغییر نکرد. `test_combined_reward_editor_names_the_buyer_whose_referrals_are_evaluated` و تست‌های دامنه پاداش. مبنا: امکان تنظیم شروط پاداشِ قابل‌فهم برای مدیر در PDFها؛ معنای دقیق شروط در راهنمای جاری نیز تصریح شده بود. |

## سفرهای پذیرش تازه

1. **محصول آماده:** مدیر با دکمه دسته و زیردسته می‌سازد، محصول ایجاد/ویرایش می‌کند، payload انبار و راهنمای تحویل می‌گذارد؛ مشتری از همان درخت محصول را پیدا می‌کند، نام و contact خود را ثبت می‌کند، کد نامعتبر سپس کد معتبر می‌زند، بخشی از مبلغ را از کیف پول و بقیه با کارت/فیش می‌پردازد؛ مدیر فیش را تأیید می‌کند؛ یک تحویل، برداشت درست، تاریخچه و CSV مالی با همان سفارش کنترل می‌شوند. مبلغ fixture: ۱۰۰٬۰۰۰، تخفیف ۱۰٬۰۰۰، کیف پول ۳۰٬۰۰۰، سهم فروش بیرونی ۶۰٬۰۰۰ تومان. اجرای maintenance تحویل دوم نمی‌سازد.
2. **محصول دستی:** ایجاد محصول و ویرایش prompt/متن تکمیل/روزهای یادآوری از همان صفحهٔ محصول؛ خرید و پرداخت کیف پول؛ ارسال اطلاعات؛ درخواست اصلاح مدیر؛ ورود دوباره از تاریخچه و ارسال نسخه صحیح؛ انتخاب متن پیش‌فرض محصول و تأیید تکمیل؛ replay همان تأیید؛ یک پیام تکمیل و reminderهای ۷، ۳ و صفر روز از completion بررسی می‌شوند.
3. **پشتیبانی:** مدیر دسته FAQ و سؤال/پاسخ قالب‌دار می‌سازد؛ مشتری جواب را می‌بیند، تیکت با فایل ثبت می‌کند؛ مدیر پاسخ می‌دهد، همان فایل را بازیابی می‌کند و تیکت را می‌بندد؛ دکمهٔ پاسخ مشتری مخفی است؛ مدیر بازگشایی و مشتری از تاریخچه پاسخ جدید می‌فرستد؛ مکالمه کامل باقی می‌ماند.

این سفرها جایگزین تست‌های مرزی نیستند؛ به آن‌ها اضافه شده‌اند. تأخیر فیش در fixture سفر صفر است تا زمان واقعی تلف نشود؛ محدودیت ۶۰ثانیه/انقضای ۳۰دقیقه، فیش به‌موقع و بررسی دیرهنگام در تست‌های مجزای پرداخت پوشش دارند.

## پوشش همه حوزه‌های سند

| حوزهٔ نیازمندی | شاهد در مجموعهٔ آزمون |
|---|---|
| شروع، نام و تماس خود کاربر، مسدودی، جوین، وضعیت عمومی و مجوز مدیر/پشتیبان | [bot](../tests/test_bot.py)، [admin](../tests/test_admin.py)، [وضعیت ربات](../tests/test_admin_bot_status.py)، [جوین](../tests/test_admin_joins.py)، [adversarial کاربر](../tests/test_user_flow_adversarial.py)، E2E-02 |
| شش دکمهٔ منوی اصلی، ترتیب/رنگ پیش‌فرض، کانال مستقیم، متن غنی، عدم emoji در label با استثنای صریح جوین | [keyboards](../tests/test_keyboards.py)، [texts](../tests/test_texts.py)، [channel](../tests/test_spec_channel_audit.py)، [خوانایی](../tests/test_button_readability.py)، [icons](../tests/test_button_icons.py) |
| ۹ بخش مدیریت، همهٔ ۸۳ عمل، فرم تأیید، جست‌وجو، «همه/بدون محدودیت»، stale/replay/restart | [دکمه‌ها](../tests/test_admin_buttons.py)، [ناوبری](../tests/test_admin_ui_navigation.py)، [ممیزی مدیر](../tests/test_spec_admin_audit.py) |
| درخت دسته/زیردسته/محصول، ۲۳ مشخصه، مخفی/ناموجود، انبار، فرمت، حذف امن، تخصیص مدیر | [کاتالوگ](../tests/test_admin_catalog_hierarchy.py)، [ناوبری](../tests/test_admin_ui_navigation.py)، [DB](../tests/test_db.py)، سفرهای ۱ و ۲ |
| خلاصه سفارش، تخفیف، پرداخت صریح حتی مبلغ صفر، کیف پول کامل/ترکیبی، روش انتخاب‌شده و وضعیت آن | [خرید](../tests/test_spec_purchase_audit.py)، [زمینه پرداخت](../tests/test_admin_payment_context.py)، [DB](../tests/test_db.py)، سفر ۱ |
| مبلغ یکتای کارت، کپی مبلغ/کارت، فیش و فایل، تأخیر، تأیید دستی/بانکی، انقضا و بازیابی | [payment server](../tests/test_payment_server.py)، [bot](../tests/test_bot.py)، [خرید](../tests/test_spec_purchase_audit.py)، [release invariants](../tests/test_release_invariants.py)، سفر ۱ |
| پرداخت ارزی اختیاری، ساخت/ادامه intent، webhook و polling شاهد، replay، عدم احیای سفارش terminal | [Plisio](../tests/test_plisio.py)، [payment server](../tests/test_payment_server.py)، [release invariants](../tests/test_release_invariants.py)، [bot](../tests/test_bot.py) |
| تحویل آماده FIFO، رزرو، راهنمای تحویل؛ manual، اصلاح اطلاعات، متن تکمیل، فایل و یادآوری | [DB adversarial](../tests/test_db_adversarial_regressions.py)، [خرید](../tests/test_spec_purchase_audit.py)، [یادآوری](../tests/test_spec_reminders_transactions.py)، [jobs](../tests/test_jobs.py)، E2E-01 و سفرهای ۱/۲ |
| پروفایل، آمار، سفارش و ادامه آن، تراکنش با تاریخ/نوع/مبلغ، تاریخچه کامل و فیلتر مدیر، بلاک و اصلاح مانده | [تاریخچه](../tests/test_user_history_pagination.py)، [ممیزی مدیر](../tests/test_spec_admin_audit.py)، [تراکنش‌ها](../tests/test_spec_reminders_transactions.py)، E2E-03 و سفر ۱ |
| FAQ دسته‌دار، سؤال/پاسخ کامل، تیکت و مکالمه، فایل، نقش پشتیبان، وضعیت بسته/باز | [admin](../tests/test_admin.py)، [ممیزی مشتری](../tests/test_spec_customer_audit.py)، [bot](../tests/test_bot.py)، سفر ۳ |
| دعوت، آمار و شرایط قابل‌مشاهده، چهار نوع پاداش، window و شروط، ثبت یک‌باره مانده/اعلان | [ممیزی مشتری](../tests/test_spec_customer_audit.py)، [DB](../tests/test_db.py)، [release invariants](../tests/test_release_invariants.py)، [دکمه‌ها](../tests/test_admin_buttons.py)، E2E-04 |
| پیام فردی/گروهی و مخاطب فیلترشده، preview شمارش و تأیید، گزارش سفارش/کاربر/مالی و CSV | [admin](../tests/test_admin.py)، [دکمه‌ها](../tests/test_admin_buttons.py)، [release invariants](../tests/test_release_invariants.py)، سفر ۱ |
| چیدمان همهٔ ۳۹ نوع صفحه، تغییر ردیف/ستون/ترتیب، preview/publish، undo/reset، رکورد موردی، conflict | [چیدمان مشتری](../tests/test_customer_layouts.py)، [ویرایشگر مدیر](../tests/test_admin_layouts.py)، ماتریس [CUSTOMER_LAYOUTS.md](CUSTOMER_LAYOUTS.md) |
| بکاپ، getUpdates تک‌نمونه، lifecycle و retry، پاکی مخزن، ویدیو و مستندات/نمودارها | [main](../tests/test_main.py)، [config](../tests/test_config_utils.py)، [telegram](../tests/test_telegram.py)، [hygiene](../tests/test_repository_hygiene.py)، [documentation](../tests/test_documentation.py)، workflow CI |

## نتیجهٔ اجرا

- خط مبنای Windows / Python 3.14: `Ran 454 tests in 487.379s`، بدون شکست؛ دو آزمون permission مخصوص POSIX طبق شرط محیط skip شدند.
- مجموعهٔ تازهٔ مسیرهای متصل و بازگشت باگ: `Ran 9 tests in 46.344s`، هر ۹ موفق.
- اجرای نهایی Windows / Python 3.14: `Ran 463 tests in 543.502s`؛ `OK (skipped=2)`، یعنی ۴۶۱ موفق و فقط دو تست مجوز فایل POSIX اجرا‌نشده، بدون failure/error. این اجرا همهٔ ۹ تست تازه و مجموعهٔ قبلی را شامل است.
- `ruff check .`، `compileall`، `pip check`، `git diff --check` و هر ۸ تست مستندات/پاکی مخزن موفق بودند. تست گزارش روزانه پس از استفاده از timezone تنظیم‌شده نیز جداگانه در ۱۷٫۴۷۷ ثانیه موفق شد تا وابستگی به مرز روز UTC نداشته باشد.
- [CI مستقل لینوکس همین اصلاحات](https://github.com/mohamadkheiry/aibotTelegram/actions/runs/34007160475) روی commit سورس `57fc473064a2904f76099f555688349b10db645c` موفق شد: Python 3.12، `Ran 463 tests in 83.869s`، `OK` بدون skip/failure/error. هر دو تست POSIX، lint/compile/secret scan، Docker build و migration روی volume قابل‌نوشتن موفق بودند. ثبت این شواهد در مستندات تغییری در برنامه یا تست‌های آن commit نمی‌دهد؛ نتیجهٔ هر push بعدی نیز باید از [workflow](https://github.com/mohamadkheiry/aibotTelegram/actions/workflows/ci.yml) بررسی شود.
- نمودارهای ۷ و ۹ رندر و بصری بازبینی شدند؛ شرح use case، قواعد بیزنس، رابط دکمه‌ای، ردیابی و راهنمای توسعه با اصلاحات هماهنگ‌اند. تست تطبیق هر ۱۹ منبع Mermaid با بلوک Markdown و SVG، لینک‌های محلی، هش منابع، وجود آموزش‌ها و اسکن پاکی مخزن موفق است. schema و مبلغ/موجودی محیط زنده تغییر نکرده‌اند.
- decode کامل هر دو ویدیوی تحویلی با FFmpeg بدون خطا بود: H.264، ۱۹۲۰×۱۰۸۰ و ۱۵fps؛ آموزش مدیریت ۱۹۱ ثانیه و استقرار حدود ۱۸۰ ثانیه. این کنترل سلامت فایل است؛ بازبینی بصری تازهٔ این دور به صفحات منابع و دو نمودار تغییرکرده محدود بود.

برای بازتولید از ریشهٔ مخزن، محیط توسعه را طبق [development.md](development.md) بسازید و اجرا کنید:

```console
python -m unittest tests.test_spec_end_to_end -v
python -m unittest discover -s tests -v
python -m ruff check .
python -m compileall -q app tests
python -m pip check
git diff --check
```

## مرز ادعا و موارد عملیاتی باقی‌مانده

- سبزبودن آزمون‌ها، اثبات نبود مطلق باگ نیست. معیار این دور رفع چهار مغایرت بازتولیدشده و گذراندن شواهد ثبت‌شده است؛ دریافت گزارش تازه نیازمند بازتولید و افزودن regression است.
- رندر واقعی همه themeها و Telegram Web/موبایل، تسویه واقعی بانک/Macrodroid یا Plisio و انتشار واقعی Premium icon pack با test double تأیید نمی‌شود. آیکون/رنگ فقط در مرز خروجی و assets سنجیده شده‌اند؛ خوانایی theme خاص نیازمند مشاهده همان کلاینت است.
- انتشار روی `@ElevenaccountsTestbot` با کامل‌شدن سورس یکی نیست. تا مالک ربات جدید را Start نکرده و دامنهٔ انتقال داده مشخص نشده، DB و token ربات‌های مختلف نباید مخلوط شوند؛ فایل/پیام/offset ربات قبلی قابل انتقال کور نیست. به همین علت در این دور poller جدید راه‌اندازی و پیام «آماده تست» برای مدیران ارسال نشده است. پس از رفع پیش‌شرط‌ها، rollout و smoke test و اطلاع‌رسانی در خود ربات جدید طبق [deployment.md](deployment.md) و [BUTTON_ICONS.md](BUTTON_ICONS.md) انجام می‌شود.
- preflight فقط‌خواندنی همین دور، هویت ربات جدید و نبود webhook را تأیید کرد، اما چت مالک هنوز برای آن در دسترس نبود. دیتابیس قبلی `integrity_check=ok` و صفر خطای foreign key داشت و دو مدیر درخواستی در همان دیتابیس فعال و اثبات‌شده بودند؛ این نتیجه مجوز یا ثبت مدیر در ربات جدید را اثبات نمی‌کند.
- تعارض تاریخی مثال popup کیف پول با درخواست پرداخت ترکیبی، و تصویر نمونهٔ Developer API با منوی فارسی نهایی، طبق تصمیم‌های ثبت‌شده در [SPEC_AUDIT.md](SPEC_AUDIT.md) باقی مانده‌اند؛ در این دور محدودیت تجاری تازه‌ای از آن‌ها ساخته نشده است.
