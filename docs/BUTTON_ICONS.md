# آیکون‌های مینیمال و دکمه‌های شیشه‌ای

درخواست مستقیم مالک: استفاده از ربات جدید و آیکون‌های مینیمال Premium با دکمه‌های شیشه‌ای. رابط همچنان دکمه‌محور است؛ هیچ Mini App، CSS یا سرویس وب تازه‌ای برای این تغییر لازم نیست.

## ظاهر و محدودیت واقعی Telegram

- دکمهٔ شیشه‌ای در این پروژه همان inline keyboard داخل پیام است و با رنگ‌بندی سند سازگار است. طبق تأکید مالک در ۲۰۲۶-۰۹-۰۶، حالت انتشار `BUTTON_COLOR_MODE=colored` است؛ تعویض ربات یا افزودن آیکون مجوز تغییر این تنظیم نیست. `theme` فقط fallback انتخابی با تأیید مالک است که رنگ اجباری را حذف می‌کند. blur، opacity، رنگ دلخواه نوشته یا شفافیت پیکسلی در Bot API فیلد مستقلی ندارند.
- درخواست شماره موبایل همچنان reply keyboard با `request_contact` است؛ تبدیل آن به inline، ارسال امن شمارهٔ خود کاربر را از بین می‌برد. این استثنا عمدی است.
- آیکون از `icon_custom_emoji_id` می‌آید، نه از Unicode emoji داخل label؛ تنها استثنای قبلی ✅/❌ فهرست مدیریت جوین اجباری حفظ می‌شود.
- قابلیت آیکون به Premium مالک ربات یا username واجدشرایط Fragment وابسته است؛ ادعای «ربات Premium است» جای تأیید قابلیت واقعی ارسال را نمی‌گیرد. `getMe` به‌تنهایی Premium مالک را ثابت نمی‌کند.
- custom emojiها با `needs_repainting=true` منتشر می‌شوند تا رنگ آن‌ها با متن/تم سازگار شود. بررسی نهایی در کلاینت روشن و تاریک پس از انتشار لازم است؛ موفقیت API به‌تنهایی اثبات pixel-level ظاهر تمام کلاینت‌ها نیست.

منابع رسمی: [InlineKeyboardButton](https://core.telegram.org/bots/api#inlinekeyboardbutton)، [ساخت بسته و repainting](https://core.telegram.org/bots/api#createnewstickerset)، [فرمت static emoji](https://core.telegram.org/stickers#static-emoji).

## مجموعه انتخاب‌شده

۴۵ آیکون خطی هماهنگ از `lucide-static@1.41.0` انتخاب شده است. منوهای کاربر، ۹ بخش مدیریت، کاتالوگ، سفارش، پرداخت، پشتیبانی، جست‌وجو، ویرایش، حذف، بازگشت و کنترل چیدمان پوشش دارند. آیکون ناشناخته از نماد خنثی فهرست استفاده می‌کند، نه یک عملیات ساختگی.

- registry معنایی: `app/button_icons.py:ICON_SOURCES`.
- SVG اصلی، فایل WebP و checksum: [assets/button-icons](../assets/button-icons/).
- manifest قابل بازتولید: [sources.json](../assets/button-icons/sources.json).
- مجوز کامل ISC و MIT مشتقات Feather: [LICENSE-Lucide.txt](../assets/button-icons/LICENSE-Lucide.txt)؛ [منبع مجوز](https://lucide.dev/license).
- WebPها lossless، با alpha و ابعاد دقیق ۱۰۰×۱۰۰ هستند؛ Node/Sharp فقط ابزار تولیدند و dependency اجرای ربات نیستند.

## قرارداد اجرای برنامه

`LayoutEngine.prepare` از کپی markup آیکون می‌سازد؛ `LayoutTelegram` همه send/edit/document/photo/copy و raw-callهای مربوط را پوشش می‌دهد. سپس metadata داخلی حذف و `TelegramClient.call` سیاست رنگ را اعمال می‌کند. دکمهٔ دارای آیکون صریح، همان آیکون قبلی را حفظ می‌کند؛ callback، URL، copy payload، contact request، label، شرط نمایش، ترتیب و مجوز عوض نمی‌شوند. outbox canonical برای ظاهر بازنویسی نمی‌شود.

اعلان شارژ کیف پول و اعتبار جایگزینِ پرداخت ارزی باید `main_menu_keyboard()` بدون آرگومان تنظیم آیکون را queue کنند؛ تزئین فقط موقع send/retry انجام می‌شود. ممیزی یک باگ مشترک در سه call site یافت و اصلاح کرد: ذخیرهٔ IDهای عملیاتی داخل canonical می‌توانست با تعویض manifest، همان کلید اعلان را متعارض کند. تست shutdown پیش از ارسال، restart با آیکون تازه، ارسال یک‌باره و ثابت‌ماندن JSON ذخیره‌شده/موجودی کیف پول این مرز را حفظ می‌کند. آیکون صریح پیام‌های legacy و دادهٔ قدیمی بازنویسی نمی‌شوند.

`BUTTON_ICON_MANIFEST` فایل JSON با کلید `icons` است؛ مقدار هر آیکون، رشتهٔ عددی custom emoji ID و کلید آن یکی از نام‌های `ICON_SOURCES` است. مسیر نسبی نسبت به پوشهٔ همان env تفسیر می‌شود. فایل بزرگ‌تر از ۶۴ KiB، JSON خراب، کلید ناشناخته یا مقدار غیرعددی fail closed است و محتوا در خطا echo نمی‌شود. متغیرهای `BUTTON_ICON_SHOP` و همتایانشان override اختیاری همان آیکون‌اند. manifest خالی و متغیرهای خالی، fallback بدون آیکون را حفظ می‌کنند.

```dotenv
BUTTON_COLOR_MODE=colored
BUTTON_ICON_MANIFEST=/etc/alone-account-icons.json
```

در Docker، manifest را read-only mount کنید؛ فایل‌های SVG/WebP داخل image runtime لازم نیستند. فعال‌کردن آیکون منجر به تغییر رنگ اولیهٔ builder یا شخصی‌سازی چیدمان نمی‌شود.

## ساخت و انتشار امن بسته

۱. `lucide-static@1.41.0` را در محیط ابزار جداگانه دریافت و SHA-512 موجود در `sources.json` را با tarball تطبیق دهید. Node.js، Sharp و Python پروژه لازم‌اند.

```bash
node tools/build_button_icons.cjs --lucide-dir /path/to/lucide-static/package --preview-out /tmp/icon-preview.png
```

`SHARP_MODULE` و `PYTHON_PATH` مسیر اختیاری ابزارهای نصب‌شده‌اند. اسکریپت نسخه package را کنترل، SVGهای منتخب و مجوز را کپی و WebPها را بازتولید می‌کند. پیش‌نمایش، sheet آموزشی تم روشن/تاریک است؛ اسکرین‌شات Telegram نیست.

۲. مالک با حساب موردنظر، ربات جدید را Start کند. بدون شناخته‌شدن user برای ربات، `createNewStickerSet` می‌تواند با `USER NOT FOUND` رد شود.

۳. توکن فقط از فایل env خصوصی یا محیط خوانده شود. نام کاربری/ID ربات و ID مالک باید صریح و تأییدشده باشند:

```bash
python tools/publish_button_icons.py --env-file /etc/alone-account-bot.env \
  --owner-id OWNER_CHAT_ID --bot-id EXPECTED_BOT_ID --bot-username EXPECTED_BOT_USERNAME \
  --output /etc/alone-account-icons.json
```

این ابزار DB را باز نمی‌کند، `getUpdates` ندارد و پیام آزمایشی به مشتری نمی‌فرستد. ابتدا `getMe` و هش فایل‌ها را کنترل می‌کند؛ نام بسته از digest دارایی‌ها و username ربات ساخته می‌شود. اجرای دوباره بستهٔ موجود را می‌خواند و pack دوم نمی‌سازد. در خطای شبکه مبهمِ create، اجرای دوباره ابتدا همان نام را بررسی می‌کند. انتشار mapping فقط بعد از بررسی شمارش، ابعاد، repainting و `getCustomEmojiStickers` انجام می‌شود. Unicode emoji در `emoji_list` فقط metadata اجباری بسته است و وارد label دکمه نمی‌شود.

۴. manifest را در env معرفی، فقط یک poller را restart، و منوی تازه را با حساب مالک باز کنید. اگر Premium مالک/مجوز بسته تأیید نشد، ادعای فعال‌بودن آیکون‌ها نکنید؛ ابتدا علت را رفع یا با اعلام صریح از fallback بدون آیکون استفاده کنید.

## تغییر هویت ربات با تعویض token

rotation توکن همان bot ID با انتقال به bot ID متفاوت یکسان نیست. `update_id`، offset، journal مدیریتی، message ID، state فرم‌ها و file IDهای Telegram به ربات قبلی وابسته‌اند. صرف تعویض token روی DB قبلی می‌تواند updateهای جدید را نادیده بگیرد یا به اثر قبلی وصل کند. قبل از راه‌اندازی:

1. با `getMe` ID/username مقصد را تأیید کنید؛ token در خروجی یا Git نباشد.
2. برای bot ID متفاوت، DB مستقل و تصمیم صریح درباره دامنه انتقال داده لازم است. انتخاب انتقال فقط کاتالوگ/تنظیمات/مدیران باید با مالک هماهنگ باشد؛ تاریخچه قدیمی در DB/backup اصلی دست‌نخورده بماند. انتقال کامل تاریخچه، مهاجرت اختصاصی شناسه‌ها و فایل‌ها می‌خواهد.
3. فیش، پیوست تیکت و سایر file IDها را قابل استفاده توسط ربات جدید فرض نکنید. offset یا journal را روی DB اصلی با SQL پاک نکنید.
4. حق نقش‌ها و وضعیت ربات/پرداخت بدون درخواست تازه تغییر نکند. افراد باید ربات جدید را Start کنند تا دریافت پیام و آزمون واقعی ممکن شود.
5. backup معتبر بگیرید، poller قبلی را با PID/command/path دقیق متوقف کنید، مقصد را با env و DB جدید و فقط یک poller اجرا کنید. webhook با حفظ pending update حذف می‌شود.

## تست و rollback

`tests/test_button_icons.py` پوشش ۳۹ نوع صفحه، ۹ بخش مدیریت، حفظ payload/label/آیکون صریح، contact، چیدمان، حالت theme، multipart/raw/edit، manifest، source/hash/license و انتشار مجدد idempotent را بررسی می‌کند. دو regression رنگ، حفظ رنگ‌های منوی اصلی در هر شش مسیر inline و رنگ‌های reply/contact در حالت colored صریح و پیش‌فرض را کنترل می‌کنند؛ دکمهٔ «لغو و بازگشت» در contact همچنان رنگ پیش‌فرض تعیین‌شدهٔ خود را دارد. suite کامل خرید و outbox نیز باید سبز بماند.

Rollback ظاهر: manifest و overrideهای آیکون را از env کنار بگذارید؛ برای برگشت رنگ‌های مرجع `BUTTON_COLOR_MODE=colored` و سپس restart تک‌نمونه‌ای. DB و کاتالوگ پاک یا restore نمی‌شوند. rollback انتقال ربات نیازمند توقف مقصد و بازگردانی env/DB مختص مبدأ است؛ دو bot ID نباید از یک DB runtime استفاده کنند.
