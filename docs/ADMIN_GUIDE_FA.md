# راهنمای مدیریت ربات

برای مرتب‌کردن دکمه‌های کاربران: «پنل مدیریت ← مدیریت کلی ربات ← چیدمان دکمه‌های کاربران». بخش را انتخاب، روی دکمهٔ پیش‌نمایش بزنید، جایگاه/ردیف‌ها را تنظیم و پس از مرور تأیید کنید. «بازگشت به چیدمان قبلی» و «بازنشانی به چیدمان اولیه» جدا هستند. فهرست کاتالوگ جابه‌جایی تک‌تک گزینه‌ها دارد و پیام‌های قدیمی ویرایش جمعی نمی‌شوند. [آموزش کامل و همهٔ صفحه‌ها](CUSTOMER_LAYOUTS.md).

پنل اصلی ۹ بخش سند را دارد؛ «مدیران و پشتیبان‌ها» و «دعوت و پاداش» داخل «مدیریت کلی ربات»، و «پرداخت‌ها» داخل «سفارش‌ها» هستند. برای تغییر کالا، «محصولات ← دسته/زیردسته ← محصول» را دنبال کنید؛ «اطلاعات و ویرایش محصول»، «انبار محصول» و «فرمت محصول» در همین صفحه‌اند. برای نام، قیمت، مدت، متن‌ها و دیگر مشخصه‌ها ابتدا مقدار کامل فعلی را ببینید؛ سپس «ویرایش این مقدار» و تأیید را بزنید. [نقشه و مثال‌های کامل](ADMIN_HIERARCHY.md).

این راهنما برای مالک، مدیر و پشتیبان است. **روش اصلی کار، دکمه‌هاست:** پس از `/start` دکمه «پنل مدیریت» را بزنید، بخش و عملیات را انتخاب کنید و فرم مرحله‌ای را ادامه دهید. همه ۸۳ عملیات قدیمی مسیر دکمه‌ای دارند. فقط داده‌هایی مثل نام، مبلغ، شناسه مدیر جدید و متن پیام تایپ می‌شوند؛ انتخاب نوع، وضعیت، رکورد و صفحه با دکمه است.

راهنمای مرحله‌ای و ماتریس کامل مسیرها در [BUTTON_UI.md](BUTTON_UI.md) است. عملیات تغییردهنده در فرم جدید خلاصه و تأیید نهایی دارند؛ پیام گروهی همان پیش‌نمایش دارای تعداد مخاطب را حفظ می‌کند. «مرحله قبل / اصلاح» برای تصحیح و «لغو و بازگشت» برای خروج است. انتخاب رکوردها جست‌وجو و صفحات ۲۰تایی دارد. منوی اصلی مشتری مطابق سند مانده و فقط مدیر تأییدشده دکمه اضافی پنل را می‌بیند.

فرمان‌های زیر صرفاً مرجع سازگاری و استفاده اختیاری کاربران پیشرفته‌اند؛ برای هیچ عملیات معمول مدیریتی لازم نیست syntax آن‌ها را تایپ کنید. در منوی فرمان‌های Telegram فقط `/start` معرفی می‌شود. دکمه‌های اعلان‌های قدیمی همچنان برای سازگاری فعال‌اند و الزاماً فرم تأیید جدید را باز نمی‌کنند.

## ۱. راه‌اندازی مالک اولیه

راهنمای جدید جوین اجباری: [ADMIN_JOINS.md](ADMIN_JOINS.md). در «مدیریت کلی ربات ← جوین اجباری»، هر کانال یک دکمه ✅/❌ دارد؛ داخل آن فعال/غیرفعال‌کردن، حذف و بازگشت قرار دارند. برای افزودن، دکمه زیر فهرست را بزنید. حذف و تغییر وضعیت نیازمند تأییدند و ربات برای فعال‌سازی باید مدیر کانال باشد.

پیش از اولین اجرای production، در `.env` هر دو شناسه مالک را ثبت کنید:

```dotenv
BOOTSTRAP_ADMIN_USERNAME=mohammadrezakheiry
BOOTSTRAP_ADMIN_CHAT_ID=123456789
```

عدد بالا نمونه است و باید با private chat/user ID واقعی جایگزین شود. username را می‌توان با یا بدون `@` نوشت؛ برنامه آن را نرمال می‌کند. پس از اثبات اولیه، chat ID anchor مجوز و username فقط metadata است؛ rename معتبر از update همان chat اطلاعات رکورد را تازه می‌کند، اما واگذاری username به chat دیگر دسترسی نمی‌سازد.

اگر chat ID از ابتدا تنظیم شده باشد، owner با هر دو شناسه در startup ثبت و verify می‌شود. اگر `BOOTSTRAP_ADMIN_CHAT_ID` خالی باشد، برنامه یک root pending با username می‌سازد و فقط نخستین private update با همان username می‌تواند chat ID را bind کند؛ این حالت برای production توصیه نمی‌شود. پس از anchorشدن، رکورد با marker یکتای `is_bootstrap_owner` شناخته می‌شود: restart username قدیمی را به root تازه تبدیل نمی‌کند، drift username همان chat conflict نیست و owner عمداً غیرفعال‌شده را دوباره فعال نمی‌کند. `app.main --check` همین initialize/migration/bootstrap را روی DB واقعی اجرا و تعارض legacy یا identity را پیش از `getMe` fail closed می‌کند؛ read-only نیست.

برای انتقال برنامه‌ریزی‌شده root، ابتدا با `/admin_add` یک owner دیگر بسازید، از همان chat با username دقیق `/start` بفرستید و در `/admins` verify و activeبودن او را کنترل کنید. سپس `BOOTSTRAP_ADMIN_CHAT_ID` را به chat ID همین owner اثبات‌شده تغییر دهید و `--check` را در پنجره استقرار پس از backup اجرا کنید؛ marker منتقل می‌شود، اما owner قبلی خودکار فعال/غیرفعال یا حذف نمی‌شود. configured chat ناشناخته، pending، غیرفعال یا non-owner رد می‌شود. آخرین owner فعال قابل غیرفعال‌سازی یا تنزل نقش نیست.

## ۲. نقش‌ها و افزودن مدیر

نقش‌های ذخیره‌شده:

- `owner`: مالک اصلی و مدیریت کامل، از جمله مدیران.
- `admin`: مدیر اجرایی فروشگاه.
- `support`: مشاهده خلاصه و تاریخچه صفحه‌بندی‌شده سفارش/تراکنش/زیرمجموعه/پاداش کاربر، کار با تیکت‌ها و پیوست همان تیکت، ارسال پیام مستقیم و `/request_info`. این نقش اجازه بازفرستادن فیش پرداخت یا پیوست اطلاعات سفارش manual، `/order_status`، `/complete`، تأیید/رد پرداخت، تغییر کیف پول یا دسترسی به تنظیمات، محصولات، گزارش‌ها و مدیران را ندارد.

مشاهده مدیران:

```text
/admins [PAGE]
```

افزودن مدیر جدید با هر دو شناسه:

```text
/admin_add @newmanager 987654321 admin
/admin_add @supportperson 1122334455 support
```

مقادیر نقش فقط `owner`، `admin` یا `support` هستند. اگر `users` از قبل همان username و chat ID را دقیقاً شناخته باشد grant فوراً verify می‌شود؛ در غیر این صورت رکورد «در انتظار تأیید هویت» و بدون هیچ دسترسی است تا همان chat ID با همان username یک update معتبر در گفت‌وگوی خصوصی بفرستد؛ `/start` روش پیشنهادی این اثبات نخستین است. اختلاف یکی از دو مقدار هرگز grant را فعال نمی‌کند. اگر username یک grant pending اشتباه تایپ شده است، owner می‌تواند `/admin_add USERNAME_CORRECT SAME_CHAT_ID ROLE` را دوباره بزند؛ برای ابطال pending از `/admin_disable CHAT_ID` استفاده کنید. شخص جدید باید خودش گفت‌وگو را شروع کند، چون Telegram ارسال proactive صرفاً بر اساس username را اجازه نمی‌دهد.

فعال/غیرفعال‌کردن دسترسی با chat ID:

```text
/admin_disable 987654321
/admin_enable 987654321
```

نکات امنیتی:

- chat ID و username باید متعلق به یک نفر باشند. اگر هرکدام قبلاً به رکورد متفاوتی متصل باشد، عملیات باید به‌عنوان conflict رد شود.
- بعد از verify، chat ID پایدار مبنای مجوز است. تغییر username روی همان حساب دسترسی را از بین نمی‌برد، ولی metadata با update معتبر همان chat تازه می‌شود؛ username یک مدیر روی chat دیگر قابل تصاحب نیست.
- نقش owner را فقط به شخصی بدهید که مجاز به تغییر مدیران، تنظیمات مالی و بکاپ است.
- برای قطع دسترسی از `admin_disable` استفاده کنید؛ حذف دستی رکوردهای SQLite توصیه نمی‌شود.
- فرمان مدیریتی یا خروجی دارای اطلاعات اکانت را به گروه forward نکنید.
- mutationهای مدیریتی با journal `started/completed` و fingerprint محافظت می‌شوند. اگر process قطع یا دیتابیس موقتاً unavailable شد، handler با NACK صریح offset را ثابت نگه می‌دارد و همان update را پیش از موارد بعدی batch با backoff سقف‌دار replay می‌کند؛ اثر freezeشده/idempotent ادامه می‌یابد، payload متفاوت با همان update ID رد و update کامل‌شده skip می‌شود. پیام diagnostic فقط در تلاش اول ارسال می‌شود و شکست آن NACK را از بین نمی‌برد. خطای terminal دامنه یا شکست پاسخ Telegram ACK می‌شود تا صف مدیریت برای همیشه متوقف نشود. این سازوکار مجوز اجرای دستی دوباره فرمان برای «اطمینان» نیست؛ ابتدا نتیجه موجود را بررسی کنید.

## ۳. قالب فرمان‌ها

در فرمان‌های چندبخشی از `|` استفاده کنید. فاصله قبل و بعد از آن مهم نیست:

```text
/set_card 6037-xxxx-xxxx-xxxx | نام صاحب حساب
```

شناسه‌هایی مانند `CATEGORY_ID`، `PRODUCT_ID` و `ITEM_ID` را ابتدا از فرمان فهرست همان بخش بگیرید. همه مبلغ‌ها عدد صحیح و فقط به تومان هستند؛ currency ذخیره‌شده فقط `TOMAN` است. `CURRENCY_LABEL` صرفاً برچسب نمایشی است و در production باید «تومان» بماند.

فهرست مدیران، دسته‌ها، محصولات، موجودی انبار، تخفیف‌ها، دسته و سوالات FAQ و قواعد پاداش همگی ۲۰ ردیف در صفحه دارند. شماره `PAGE` اختیاری و پیش‌فرض ۱ است؛ سربرگ مجموع دقیق و فرمان‌های قبلی/بعدی را نشان می‌دهد. برای صفحه‌های همه محصولات یا همه سوالات، `all` را به‌جای دسته بنویسید؛ یک عدد تنها بعد از `/products` یا `/faqs` همچنان شناسه دسته است. محدودیت طول پیام باعث حذف ردیف‌های همان صفحه نمی‌شود.

### متن غنی با `html:`

ورودی متنی عادی به‌صورت خودکار escape می‌شود و تگ‌های داخل آن اجرا نمی‌شوند. فقط وقتی خودِ مقدار متن را با `html:` شروع کنید، ربات آن را به‌عنوان HTML تلگرام اعتبارسنجی و نمایش می‌دهد؛ پیشوند `html:` در پیام نهایی دیده نمی‌شود.

نمونه‌ها:

```text
/message 123456789 | html:<b>اطلاع مهم</b> <a href="https://example.com">مشاهده جزئیات</a>
/broadcast_all html:<blockquote>زمان نگهداری امشب</blockquote>
/product_set 12 | long_description | html:<b>اشتراک ویژه</b> با تحویل فوری
/category_set 4 | description | html:<i>محصولات این دسته</i>
```

تگ‌های مجاز عبارت‌اند از:

```text
b, strong, i, em, u, ins, s, strike, del, a, code, pre,
blockquote, span class="tg-spoiler", tg-spoiler, tg-emoji
```

- تگ `a` فقط `href` مطلق HTTPS بدون credential می‌پذیرد؛ `http`، `tg:`، `mailto:`، userinfo، `localhost`، IP literal محلی/خصوصی/reserved و host عددی مبهم رد می‌شوند. validator DNS را resolve نمی‌کند. اگر مقصد Telegram است، از لینک canonical مانند `https://t.me/name` استفاده کنید.
- تگ `tg-emoji` فقط ویژگی عددی `emoji-id` را می‌پذیرد.
- `code` می‌تواند فقط class با پیشوند `language-` داشته باشد؛ `blockquote` فقط ویژگی `expandable` و `span` فقط class برابر `tg-spoiler` را می‌پذیرد.
- تگ‌ها باید درست و به‌ترتیب بسته شوند؛ تگ خودبسته، comment، اعلان HTML و attribute اضافی رد می‌شود.
- `html:` فقط در ورودی‌هایی که صریحاً متن غنی را پشتیبانی می‌کنند معتبر است: توضیح دسته؛ فیلدهای محصول `short_description`، `long_description`، `account_type`، `activation`، `warranty`، `activation_instructions`، `usage_terms`، `rules`، `info_request_text`، `completion_text` و `delivery_instructions`؛ متن `/complete` و `/request_info`؛ متن `/message`؛ متن هر سه فرمان broadcast؛ و پاسخ FAQ. این پیشوند یک حالت سراسری برای تمام فرمان‌ها نیست؛ برای نمونه پاسخ `/ticket_reply` و یادداشت `/order_status` متن ساده‌اند.

## ۴. مدیریت کلی

از «مدیریت کلی ربات»، دکمهٔ وضعیت را انتخاب کنید. وقتی ربات فعال است «غیرفعال‌کردن ربات» و «فعال‌کردن حالت تعمیرات» می‌بینید؛ وقتی غیرفعال است «فعال‌کردن ربات» و «غیرفعال‌کردن حالت تعمیرات» نمایش داده می‌شوند. تعمیرات همان توقف دسترسی کاربران عادی است؛ مدیران می‌توانند از همین پنل دوباره ربات را فعال کنند. خلاصهٔ وضعیت فعلی/مقصد را تأیید کنید؛ پس از اجرا، دکمهٔ عمل معکوس فوراً می‌آید. «لغو و بازگشت» تغییری نمی‌دهد.

فرمان‌های سازگار اختیاری؛ برای کار روزانه لازم نیستند:

```text
/bot_on
/bot_off
```

تنظیم کارت و کانال اصلی:

```text
/set_card شماره کارت | نام صاحب حساب
/set_channel https://t.me/example_channel
```

لینک کانال اصلی فقط قالب canonical `https://t.me/...`، بدون port/query/fragment یا credential را می‌پذیرد؛ HTTP، `localhost`، IP literal محلی/خصوصی و دامنه‌های مشابه جعلی رد می‌شوند.

فعال یا غیرفعال‌کردن روش پرداخت:

از دکمهٔ «فعال یا غیرفعال‌کردن روش پرداخت»، ابتدا روش را انتخاب کنید. بالای دکمه‌های وضعیت، «روش انتخاب‌شده» و «وضعیت فعلی» دیده می‌شود؛ در تأیید نهایی «وضعیت جدید» نیز جداست. شماره/صاحب کارت یا تنظیم سرویس ارزی ناقص باشد، فرم علت نمایش‌داده‌نشدن آن روش به مشتری را توضیح می‌دهد. لغو تغییری نمی‌دهد؛ نتیجه، نام روش و وضعیت تازه را نشان می‌دهد. فرمان‌های زیر فقط مسیر سازگار اختیاری‌اند.

```text
/payment wallet on
/payment card on
/payment crypto off
```

مقادیر روش فقط `wallet`، `card` و `crypto` و وضعیت فقط `on` یا `off` است.

در دیتابیس تازه، کیف پول فعال است. flag کارت فعال ساخته می‌شود، اما تا وقتی هر دو مقدار شماره کارت و صاحب حساب با `/set_card` ثبت نشده باشند، کارت در UI مخفی است و `/payment card on` نیز بدون این اطلاعات fail closed می‌شود. ارز دیجیتال پیش‌فرض خاموش است؛ برای نمایش آن باید `PLISIO_API_KEY` هنگام startup موجود باشد و سپس `/payment crypto on` اجرا شود. اگر می‌خواهید کارت پس از `/set_card` همچنان مخفی بماند، `/payment card off` را اجرا کنید.

## ۵. جوین اجباری

ربات باید در کانال اجباری دسترسی لازم برای خواندن وضعیت عضویت داشته باشد. برای کانال private، لینک عضویت معتبر را نگه دارید.

```text
/joins
/join_add @channel_username | عنوان نمایشی | https://t.me/channel_username
/join_toggle CHANNEL_ID
/join_delete CHANNEL_ID
```

- `/joins` شناسه داخلی `CHANNEL_ID`، شناسه تلگرامی، عنوان و وضعیت فعال/غیرفعال همه کانال‌ها را نشان می‌دهد. برای toggle/delete از همین شناسه داخلی استفاده کنید، نه chat ID تلگرام.
- شناسه تلگرامی در `/join_add` باید username معتبر `@...` یا chat ID کانال با قالب `-100...` باشد و لینک عضویت باید canonical HTTPS از دامنه دقیق `t.me` یا `telegram.me`، بدون port/query/fragment/credential باشد؛ دامنه suffixدار جعلی و URL محلی رد می‌شود.
- هنگام افزودن، ربات `getChat` و `getChatMember` را اجرا می‌کند و فقط وقتی کانال قابل دسترس و خود ربات `administrator` یا `creator` باشد آن را فعال می‌کند.
- حذف کانال با غیرفعال‌کردن فرق دارد؛ برای توقف موقت از toggle استفاده کنید.

پس از هر تغییر، با یک حساب غیرمدیر `/start` را تست کنید.

## ۶. دسته و زیردسته

```text
/categories [PAGE]
/category_add عنوان | آیکون|0 | توضیح|0
/subcategory_add PARENT_ID | عنوان | آیکون|0 | توضیح|0
/category_toggle CATEGORY_ID
/category_set CATEGORY_ID | name | عنوان تازه
/category_set CATEGORY_ID | parent | PARENT_ID
/category_set CATEGORY_ID | icon | آیکون تازه
/category_set CATEGORY_ID | description | توضیح تازه
/category_set CATEGORY_ID | sort_order | 10
/category_delete CATEGORY_ID
```

- آیکون و توضیح اختیاری‌اند؛ در زمان ساخت مقدار `0` یعنی ثبت‌نکردن آن فیلد. شکل کوتاه `/category_add عنوان` و `/subcategory_add PARENT_ID | عنوان` نیز معتبر است.
- توضیح دسته از `html:` پشتیبانی می‌کند. عنوان و آیکون متن ساده‌اند.
- برای پاک‌کردن `icon` یا `description` موجود، مقدار `0`، `none`، `null` یا `-` را بدهید.
- `/categories` آیکون، خلاصه توضیح، والد و وضعیت هر دسته را هم نشان می‌دهد.
- برای انتقال دسته به ریشه، مقدار `parent` را `0`، `none`، `root` یا `ریشه` بگذارید.

برنامه والد ناموجود، والد خود دسته، چرخه در درخت و عنوان تکراری زیر یک والد را رد می‌کند. `category_delete` فقط دسته کاملاً خالی را حذف می‌کند؛ اگر زیردسته یا محصولی دارد، ابتدا آن وابستگی را جابه‌جا کنید. `category_toggle` نمایش دسته را تغییر می‌دهد.

## ۷. محصول

فهرست و ساخت محصول:

```text
/products [CATEGORY_ID|all] [PAGE]
/products all 2
/products CATEGORY_ID 1
/product_add CATEGORY_ID | عنوان | قیمت | مدت | ready
/product_add CATEGORY_ID | عنوان | قیمت | مدت | manual
```

- `ready`: اطلاعات تحویل از انبار برداشته و خودکار ارسال می‌شود.
- `manual`: پس از پرداخت، اطلاعات از کاربر گرفته می‌شود و مدیر فعال‌سازی را کامل می‌کند.

ویرایش یک فیلد:

```text
/product_set PRODUCT_ID | FIELD | VALUE
```

فیلدهای قابل استفاده:

```text
name
category
type
stock_limit
icon
short_description
long_description
price
duration
duration_days
account_type
activation
renewable
warranty
features
activation_instructions
usage_terms
rules
rules_url
info_request_text
completion_text
delivery_instructions
reminder_days
```

مقدار `type` فقط `ready` یا `manual` و currency فقط `TOMAN` است. برای برداشتن سقف موجودی، `stock_limit` را `none` بگذارید. `rules_url` فقط URL مطلق HTTPS بدون credential، whitespace/control، `localhost`، IP literal محلی/خصوصی/reserved یا host عددی مبهم می‌پذیرد؛ validator DNS را resolve نمی‌کند. برای پاک‌کردن آن از `none` استفاده کنید. با تغییر `duration`، اگر مقدار به شکل عدد، «N روز» یا `N days` باشد `duration_days` نیز به N تنظیم می‌شود؛ هر برچسب دیگری انقضای عددی را پاک می‌کند. تغییر مستقیم `duration_days` به یک عدد مثبت، برچسب را به «N روز» همگام می‌کند و مقدار `none`، `null`، `-`، «بدون انقضا» یا «مادام العمر» هر دو را روی بدون انقضا می‌گذارد. تبدیل محصول آماده به manual تا وقتی هر نوع آیتم انبار به آن متصل باشد رد می‌شود؛ ابتدا آیتم‌های آزاد یا غیرفعال را حذف کنید. دسته مقصد نیز باید از قبل وجود داشته باشد.

`reminder_days` فهرست اعداد صحیح نامنفی و جداشده با ویرگول است؛ مانند `7,3,1,0`. اعداد مثبت تعداد روز پیش از پایان‌اند و `0` یادآوری همان روز پایان اشتراک را فعال می‌کند. زمان آن ابتدای روز پایان در `TIMEZONE` ربات است؛ اگر ثبت یادآوری در همان روز باشد، تا زمانی که اشتراک هنوز معتبر است موعد فوری می‌گیرد. برای پایان دقیقاً در نیمه‌شب یا اشتراک پایان‌یافته، پیام دیرهنگام ساخته نمی‌شود. عدد منفی رد می‌شود.

تغییر flagهای محصول:

```text
/product_toggle PRODUCT_ID visible
/product_toggle PRODUCT_ID available
/product_toggle PRODUCT_ID reserve
/product_delete PRODUCT_ID
```

- `visible`: نمایش در فروشگاه.
- `available`: امکان انتخاب/خرید.
- `reserve`: اجازه ورود سفارش پرداخت‌شده به صف انتظار وقتی موجودی آماده نداریم.
- `product_delete`: حذف نرم؛ رکورد و سوابق سفارش حفظ می‌شوند اما محصول فعال، قابل نمایش، قابل خرید و قابل رزرو نخواهد بود.

برای تغییرات قیمت و نوع محصول، ابتدا سفارش‌های باز را بررسی کنید. سفارش‌ها snapshot عنوان، قیمت، نوع و مدت محصول را نگه می‌دارند و تغییر محصول نباید سوابق گذشته را بازنویسی کند.

## ۸. انبار محصول آماده

افزودن موجودی:

```text
/inventory_add PRODUCT_ID
```

پس از فرمان، ربات پیام بعدی را به‌عنوان payload محرمانه موجودی می‌گیرد؛ برای مثال لینک، ایمیل، رمز، کد 2FA و توضیح. هر اکانت را در یک پیام جدا ثبت کنید.

```text
/inventory_list PRODUCT_ID [PAGE]
/inventory_edit ITEM_ID
/inventory_disable ITEM_ID
/inventory_enable ITEM_ID
/inventory_delete ITEM_ID
/inventory_assign ITEM_ID USER_CHAT_ID
```

- پس از `/inventory_edit ITEM_ID`، payload محرمانه جدید را در پیام بعدی بفرستید. ربات متن قبلی یا جدید را در پاسخ تکرار نمی‌کند و با `/cancel` می‌توان حالت ویرایش را لغو کرد.
- آیتم `assigned` قابل ویرایش نیست؛ فقط آیتم تحویل‌نشده را ویرایش کنید. payload تکراری برای همان محصول رد می‌شود.
- assignment دستی، سفارش completed متناظر و delivery outbox با کلید پایدار را در یک transaction ایجاد می‌کند. اگر اعلان پایدار ساخته نشود کل assignment rollback می‌شود؛ خطای شبکه بعد از commit از همان outbox retry می‌شود.
- `/inventory_assign` فقط وقتی مجاز است که برای همان محصول هیچ سفارش ready قدیمی در `paid`، `processing` یا `awaiting_stock` و بدون آیتم تخصیص‌یافته وجود نداشته باشد. اگر پیام conflict صف FIFO دیدید، آیتم را به کاربر تازه واگذار نکنید؛ اجازه دهید maintenance ابتدا قدیمی‌ترین backlog همان محصول را fulfil کند.
- فعال/غیرفعال‌کردن و حذف فقط برای آیتم‌های `available` یا `disabled` مجاز است. آیتم `assigned` قابل تغییر یا حذف نیست تا سابقه تحویل خراب نشود.
- payload تکراری برای یک محصول بر اساس hash تشخیص داده می‌شود.
- متن نهایی تحویل آماده، شامل عنوان/icon snapshot، payload و `delivery_instructions`، باید در یک پیام حداکثر ۳۹۰۰ نویسه جا شود. add/edit موجودی و تغییر فیلدهای مؤثر محصول پیش از ذخیره این متن را با renderer واقعی می‌سنجند؛ رکورد قدیمیِ بلند نیز پیش از assignment رد می‌شود. credential را برای عبور از محدودیت در چند آیتم یا چند پیام تکه نکنید؛ محتوای معتبر را کوتاه و دوباره ثبت کنید.
- اطلاعات انبار در SQLite ذخیره می‌شود؛ دسترسی فایل و بکاپ را به حساب سرویس محدود و دیسک/بکاپ را رمزگذاری کنید.
- پس از تغییر موجودی، یک خرید آزمایشی انجام دهید و مطمئن شوید یک آیتم به دو سفارش تحویل نمی‌شود.

## ۹. سفارش و فعال‌سازی

```text
/orders [STATUS|all] [FROM_DATE TO_DATE] [PAGE]
/orders pending_payment 2
/orders pending_payment 2026-09-01 2026-09-30 3
/orders all 2026-09-01 2026-09-30 1
/orders 2026-09-01 2026-09-30 2
/order ORDER_NUMBER
/order_attachment ORDER_NUMBER
/order_status ORDER_NUMBER STATUS | پیام اختیاری
```

فهرست `/orders` با ترتیب `id` نزولی و ۲۰ ردیف در هر صفحه نمایش داده می‌شود. سربرگ هر صفحه «صفحه X از Y | مجموع: N» و در صورت وجود، فرمان آماده قبلی/بعدی را نشان می‌دهد؛ `_send_blocks` همه ردیف‌های همان صفحه را زیر سقف Telegram در چند پیام حفظ می‌کند. صفحه کمتر از ۱ یا بیشتر از آخرین صفحه رد می‌شود. آرگومان‌های سازگار عبارت‌اند از: بدون فیلتر، فقط status، فقط بازه، status+بازه، و در همه حالت‌ها شماره صفحه به‌عنوان آخرین عدد صحیح.

`/order ORDER_NUMBER` و جست‌وجوی شماره دقیق در `/user_orders` تمام متن اطلاعات فعال‌سازی ارسال‌شده توسط کاربر را نمایش می‌دهند. متن بلند به بخش‌های escapeشده و کامل زیر سقف Telegram تقسیم می‌شود و انتهای آن حذف نمی‌شود. این نمایش در همان دسترسی مشاهده سفارش است؛ بازفرستادن فایل پیوست همچنان فقط از `/order_attachment` و با مجوز `owner/admin` انجام می‌شود.

وضعیت‌های قابل ذخیره:

```text
pending_payment
awaiting_confirmation
awaiting_stock
awaiting_info
paid
processing
completed
rejected
expired
cancelled
refunded
```

همه انتقال‌ها مجاز نیستند؛ برنامه transition نامعتبر را رد می‌کند. علاوه بر آن، سه وضعیت حساس با `/order_status` قابل ثبت مستقیم نیستند: برای `paid` حتماً مسیر معتبر تأیید پرداخت و برای `completed` حتماً fulfillment خودکار ready یا `/complete` دستی لازم است. `refunded` برای workflow آینده رزرو شده و نسخه فعلی هیچ فرمان یا مسیر اجرایی ورود به آن ندارد؛ بازپرداخت آینده باید عملیات واقعی ارائه‌دهنده و ثبت مالی متناظر را اتمیک انجام دهد. `/order_status ... cancelled|expired|rejected` نیز تا وقتی هر external payment آن سفارش `pending/verifying` است fail closed می‌شود: فیش card را فقط با `/reject_payment` تعیین تکلیف کنید و crypto invoice را تا evidence terminal provider محلی لغو/رد/منقضی نکنید. برای محصول manual:

```text
/request_info ORDER_NUMBER | متن درخواست اصلاح یا اطلاعات جدید
/complete ORDER_NUMBER | متن تحویل یا تأیید فعال‌سازی
```

`/order_status` و `/complete` فقط برای `owner` و `admin` هستند. `/complete` فقط سفارش `manual` در وضعیت `processing` و دارای اطلاعات معتبر مشتری را می‌پذیرد؛ سفارش `ready` یا وضعیت دیگری هرگز دستی تکمیل نمی‌شود. تکرار همان متن تحویل برای سفارش تکمیل‌شده idempotent است، اما متن متفاوت اجازه بازنویسی تحویل قبلی را ندارد. نقش `support` می‌تواند سفارش‌ها را ببیند و برای سفارش manual از `/request_info` استفاده کند، اما نمی‌تواند سفارش را تکمیل کند یا وضعیت آن را دلخواه تغییر دهد.

`/order_attachment` فقط برای `owner/admin` است و پیوست ذخیره‌شده اطلاعات سفارش manual را با نوع اصلی `photo/document` دوباره می‌فرستد. این فرمان فقط ابزار مشاهده است؛ اعتبارسنجی وضعیت و تکمیل همچنان باید با workflow سفارش انجام شود و پیوست را در گروه یا تیکت نامرتبط forward نکنید.

ثبت اطلاعات کاربر و transition سفارش manual به `processing` یک عملیات اتمیک است. هر نسخه محتوای `customer_info_json` با hash کوتاه خودش یک alert پایدار می‌گیرد. restart یا retry همان نسخه alert دوم نمی‌سازد، اما جایگزینی متن/فایل alert تازه‌ای ایجاد می‌کند و `/order_attachment` همیشه نسخه فعلی commit‌شده را می‌فرستد؛ maintenance نسخه commit‌شده‌ای را که alert آن جا افتاده دوباره پیدا می‌کند. متن نهایی `/complete` نیز باید همراه سربرگ سفارش در سقف ۳۹۰۰ نویسه یک پیام جا شود؛ متن بلند پیش از completeشدن Order و ساخت اعلان رد می‌شود و تحویل قبلی truncate/split نمی‌گردد.

پس از first-contact، Order و خلاصه تأیید آن با کلید `order:{id}:created-summary` اتمیک ثبت می‌شوند؛ failure ارسال/restart نباید Order دوم یا سفارش بدون خلاصه بسازد. پس از موفقیت مالی نیز اعلان canonical شامل شماره سفارش، محصول، مبلغ و روش باید پیش از رزرو، درخواست اطلاعات manual یا تحویل credential attempt شود. تا status اعلان `queued/sending` است fulfillment صبر می‌کند؛ اگر ارسال `sent` یا پس از retryها terminal `failed/cancelled` شود ادامه مجاز است. مورد terminal در outbox برای عملیات قابل مشاهده می‌ماند و نباید با SQL به queued برگردانده شود.

برای محصول ready، تحویل خودکار از انبار انجام می‌شود. اگر reserve فعال و انبار خالی باشد، سفارش در `awaiting_stock` می‌ماند و با ورود موجودی بر اساس صف پردازش می‌شود. اگر reserve غیرفعال بوده ولی race پس از پرداخت آخرین item را مصرف کند، سفارش `processing` می‌شود؛ پس از restock، maintenance قدیمی‌ترین سفارش ready در این وضعیت را خودکار fulfil می‌کند و `/complete` دستی همچنان مجاز نیست.

مدت اشتراک از تحویل واقعی محاسبه می‌شود، نه از پرداخت: برای محصول آماده، زمان تخصیص موجودی و برای محصول manual، زمان اجرای `/complete` مبدا است. بنابراین زمان انتظار در صف رزرو یا انتظار برای ارسال اطلاعات از مدت اشتراک کم نمی‌کند.

## ۱۰. بررسی فیش و پرداخت

```text
/approve_payment PAYMENT_NUMBER
/reject_payment PAYMENT_NUMBER | دلیل
/payment_detail PAYMENT_NUMBER
/card_reviews
/card_resolve EVENT_ID dismiss|refund_confirmed | توضیح
/crypto_reviews
/crypto_resolve EVENT_ID dismiss|refund_confirmed|credit_confirmed | توضیح
```

- approve/reject دستی فقط برای پرداخت `card` دارای فیش و در وضعیت `verifying` مجاز است؛ payment ارزی، پرداخت بدون فیش یا وضعیت نامرتبط را با این فرمان‌ها تعیین تکلیف نکنید.
- `/payment_detail` فقط برای `owner/admin` است، جزئیات payment را نشان می‌دهد و فیش ذخیره‌شده را با نوع اصلی `photo/document` دوباره می‌فرستد؛ برای تصمیم مالی همیشه شماره، مبلغ، کاربر و گزارش مستقل بانک را با هم تطبیق دهید.
- هر جایگزینی واقعی فیش، بر اساس hash نوع و شناسه فایل alert پایدار تازه‌ای برای owner/admin می‌سازد؛ replay/restart همان نسخه alert تکراری نمی‌سازد. دریافت alert جای مشاهده فایل commit‌شده با `/payment_detail` را نمی‌گیرد.
- قبل از approve، مبلغ، شماره پرداخت، کاربر و تصویر فیش را تطبیق دهید. replay همان تصمیم نهایی با همان شناسه idempotent است؛ تصمیم مخالف یا reference متفاوت conflict محسوب می‌شود.
- رد آخرین پرداخت بیرونی سفارش، hold قابل‌آزادسازی را برمی‌گرداند و اگر سفارش هنوز منقضی نشده باشد آن را به `pending_payment` بازمی‌گرداند.
- سفارش unpaid بدون crypto فعال و intent کارت بدون فیش طبق `ORDER_EXPIRY_MINUTES`، به‌طور پیش‌فرض پس از ۳۰ دقیقه، منقضی می‌شوند. invoice crypto با deadline محلی terminal نمی‌شود و تا شاهد provider پایش می‌گردد.
- فیش فقط برای روش `card` است. نخستین photo/document باید پیش از `expires_at` ثبت شود؛ پس از ثبت نخستین فیش، جایگزینی آن تا زمانی که بررسی دستی باز است پذیرفته می‌شود.
- پرداخت دارای فیش با وضعیت `verifying` تا تصمیم صریح مدیریت باز می‌ماند و به‌دلیل گذشت زمان به‌صورت خودکار منقضی نمی‌شود. payment دارای فیش/`verifying` حتی با دکمه لغو قدیمی کاربر قابل لغو نیست.
- هر سفارش فقط یک external intent فعال در مجموع card/crypto دارد. کاربر برای تغییر card به crypto باید card بدون فیش را لغو کند؛ چون این کار سفارش را terminal می‌کند، سپس باید خرید تازه بسازد. invoice ارزی صادرشده دکمه لغو ندارد، callback لغو نسخه قدیمی را نیز رد می‌کند و deadline محلی آن را terminal نمی‌کند؛ provider evidence تا نتیجه قطعی یا late transition پایش می‌شود.
- در جزئیات سفارش و صفحه کیف پول، intent ارزی `pending/verifying` با URL ذخیره‌شده‌ای که دوباره اعتبارسنجی امنیتی شود، دکمه «ادامه پرداخت ارزی» دارد. «ارسال فیش» فقط برای card است؛ اگر URL legacy نامعتبر یا خالی باشد لینک نمایش داده نمی‌شود و کاربر به پشتیبانی هدایت می‌شود.
- مبلغ یکتای کارت پس از terminalشدن intent تا ۲۴ ساعت بعد از `max(expires_at, updated_at)` دوباره تخصیص داده نمی‌شود. مصرف موقت window مبلغ در ترافیک بالا رفتار حفاظتی است؛ window را با SQL دستی آزاد نکنید.
- callback کارت‌به‌کارت و Plisio جای بررسی انسانی موارد مشکوک را نمی‌گیرند.
- payment در وضعیت `paid` terminal است و نسخه فعلی workflow refund payment/topup ندارد؛ `set_payment_status(refunded)` و میان‌بر وضعیت عمداً رد می‌شوند. بازپرداخت آینده باید عملیات واقعی provider/بانک و ledger reversal را در workflow جدا و آزموده هماهنگ کند.

رخداد کارت نامنطبق/دیررس و پاسخ partial، مبلغ نامعلوم یا ناسازگار Plisio در review پایدار می‌مانند و به مدیران مجاز هشدار داده می‌شوند. `/card_reviews` و `/crypto_reviews` تا ۱۰۰ مورد باز را نشان می‌دهند. فرمان‌های resolve فقط برای owner فعال‌اند و note اجباری است:

- `dismiss`: شاهد مستقل نشان می‌دهد پرداخت قابل پذیرش نیست؛ مورد بدون ثبت پرداخت بسته می‌شود.
- `refund_confirmed`: فقط وقتی بازپرداخت بیرونی واقعاً انجام و مستند شده است؛ این برچسب خودش وجهی جابه‌جا یا کیف پول را credit نمی‌کند.
- `credit_confirmed` فقط برای crypto و فقط روی completed evidence دقیق همان invoice پذیرفته می‌شود. برای topup اعتبار همان topup ثبت می‌شود؛ اگر Order قبلاً پس از review terminal شده باشد، Order احیا نمی‌شود و `payments.base_amount` دقیقاً یک‌بار به‌عنوان اعتبار جبرانی کیف پول ثبت می‌گردد، نه درآمد فروش همان سفارش.

هیچ‌وقت برای خالی‌کردن review، status payment/order یا wallet ledger را با SQL تغییر ندهید. مشاهده completed بعدی یا terminal-zero می‌تواند review باز قبلی را خودکار با پیوند به رخداد جدید ببندد؛ crash بعد از ثبت completed evidence نیز توسط maintenance بازیابی می‌شود. late evidence تازه پس از تصمیم قبلی باید دوباره به‌عنوان مورد پرخطر بررسی شود، نه اینکه تصمیم قدیمی کورکورانه روی آن اعمال گردد.

محدودیت‌های فعال برای جلوگیری از انباشت درخواست:

- حداکثر ۱۰ سفارش پرداخت‌نشده (`pending_payment` یا `awaiting_confirmation`) برای هر کاربر.
- حداکثر ۵ پرداخت `pending` یا `verifying` برای هر کاربر و هر روش پرداخت.
- حداکثر یک payment بیرونی فعال برای هر سفارش، مستقل از روش card/crypto.
- در مجموع card و crypto فقط یک شارژ کیف پول تازه برای هر کاربر فعال می‌شود. replay فقط با همان روش، مبلغ و terms همان رکورد را برمی‌گرداند؛ روش، مبلغ یا terms متفاوت conflict است و هیچ topup به‌طور ضمنی replace یا cancel نمی‌شود. اگر migration از نسخه قدیمی دو intent فعال به‌جا گذاشته باشد، کیف پول هر دو را جدا برای resume نشان می‌دهد تا تعیین تکلیف شوند؛ این سازگاری مجوز ساخت intent دوم نیست.
- حداکثر ۲۰ intent کارت در ۲۴ ساعت برای هر کاربر. پس از ۳ لغو کارت در یک ساعت، ساخت intent تازه تا پایان cooldown رد و رخداد به مدیران مجاز هشدار داده می‌شود.

## ۱۱. کاربر و کیف پول

```text
/users [all|active|blocked] [PAGE]
/users PAGE
/users new|inactive [DAYS] [PAGE]
/users joined FROM_DATE TO_DATE [PAGE]
/users product PRODUCT_ID FROM_DATE TO_DATE [PAGE]
/user CHAT_ID
/user @username
/user ORDER_NUMBER
/user_orders CHAT_ID|@username [STATUS|all] [PAGE|ORDER_NUMBER]
/user_transactions CHAT_ID|@username [PAGE]
/user_referrals CHAT_ID|@username [PAGE]
/user_rewards CHAT_ID|@username [PAGE]
/block CHAT_ID
/unblock CHAT_ID
/message CHAT_ID | متن
```

معنای فیلترها:

- بدون آرگومان، `all` یا فقط یک `PAGE`: همه کاربران بدون فیلتر مسدودی، به ترتیب `id` نزولی.
- `active`: کاربران غیرمسدودی که `updated_at` آن‌ها در ۳۰ روز اخیر است.
- `blocked`: همه کاربران مسدود.
- `new [DAYS]`: کاربران غیرمسدود با `joined_at` در تعداد روز اخیر؛ پیش‌فرض ۷ روز.
- `inactive [DAYS]`: کاربران غیرمسدود که `updated_at` آن‌ها قدیمی‌تر از بازه است؛ پیش‌فرض ۳۰ روز.
- `joined FROM_DATE TO_DATE`: همه کاربران عضو‌شده در بازه تاریخ.
- `product PRODUCT_ID FROM_DATE TO_DATE`: همه خریداران متمایز محصول که `paid_at` آن‌ها در بازه است، Order آن‌ها `order_origin=customer` و subtotal مثبت دارد و وضعیت یکی از `paid`، `awaiting_stock`، `awaiting_info`، `processing` یا `completed` است؛ تخصیص داخلی مدیر/سفارش صفرمبلغ خریدار محسوب نمی‌شود.

همه فهرست‌های بالا ۲۰ ردیف در صفحه، total دقیق و راهنمای قبلی/بعدی دارند و صفحه نامعتبر را رد می‌کنند. فیلترهای `active` و `inactive` از هم جدایند؛ مرز پیش‌فرض هر دو ۳۰ روز است. فیلترهای `new` و `inactive` کاربران مسدود را حذف می‌کنند، اما `joined` و `product` وضعیت مسدودی را فیلتر نمی‌کنند و آن را در خروجی نشان می‌دهند. برای `new` و `inactive` مقدار `DAYS` باید بین ۱ و ۳۶۵۰ باشد؛ به‌علت سازگاری، یک عدد تنها پس از `new|inactive` همیشه DAYS است، پس برای رفتن به صفحه بعد DAYS را نیز صریح بنویسید. تاریخ‌ها `YYYY-MM-DD` و بر اساس `TIMEZONE` هستند و روز پایان را نیز کامل شامل می‌شوند. در `/user ORDER_NUMBER` شماره سفارش باید قالب واقعی سفارش، مانند پیشوند `ORD-` یا `ADM-`، داشته باشد.

`/user` یک پیش‌نمایش از آخرین سفارش‌ها/تراکنش‌ها، totalها، مانده و اولین/آخرین تاریخ خرید تجاری را نشان می‌دهد؛ MIN/MAX خرید از کل تاریخچه و بدون cap محاسبه می‌شود. چهار فرمان `/user_*` تاریخچه کامل را ۲۰تایی نشان می‌دهند: سفارش‌ها به ترتیب `id` نزولی و قابل فیلتر status یا جست‌وجوی `ORDER_NUMBER` متعلق به همان user؛ تراکنش و رخداد پاداش جدیدترین‌اول؛ و زیرمجموعه‌ها به ترتیب `id` نزولی همراه status/date و تعداد/مجموع پاداش. شماره سفارش متعلق به کاربر دیگر به‌صورت «پیدا نشد» رد می‌شود. این چهار سطح برای `owner/admin/support` مشاهده‌ای است؛ مجوز mutation نقش support را افزایش نمی‌دهد.

در تراکنش‌ها، تاریخ و مبلغ همراه «نوع» فارسی تراکنش و «دلیل» جداگانه نمایش داده می‌شود؛ دلیل آزاد مدیر یا پرداخت، نوعی مثل اصلاح موجودی، خرید، شارژ یا پاداش را پنهان نمی‌کند.

تغییر موجودی:

```text
/wallet_adjust CHAT_ID | 50000 | دلیل افزایش
/wallet_adjust CHAT_ID | -25000 | دلیل کاهش
```

مبلغ signed است؛ عدد مثبت اعتبار و عدد منفی بدهی ایجاد می‌کند. دلیل اجباری و شناسه مدیر در ledger ثبت می‌شود. برای اصلاح اشتباه، entry قبلی را حذف نکنید؛ یک adjustment معکوس با دلیل روشن ثبت کنید.

## ۱۲. تخفیف

```text
/discounts [PAGE]
/discount_add CODE | fixed|percent | VALUE | MAX_USES|0 | PRODUCT_ID|0 | USER_CHAT_ID|0 | YYYY-MM-DD|0
/discount_add CODE | fixed|percent | VALUE | MAX_USES|0 | PRODUCT_ID|0 | USER_CHAT_ID|0 | END_DATE|0 | MINIMUM|0 | PER_USER_LIMIT|0 | START_DATE|0
/discount_toggle CODE
/discount_delete CODE
```

مثال ساخت تخفیف ۱۰ درصدی عمومی تا یک تاریخ:

```text
/discount_add AUTUMN10 | percent | 10 | 100 | 0 | 0 | 2026-10-01
```

مثال تخفیف با حداقل سفارش ۵۰۰۰۰، حداکثر دو بار برای هر کاربر و تاریخ شروع مشخص:

```text
/discount_add AUTUMN10 | percent | 10 | 100 | 0 | 0 | 2026-10-01 | 50000 | 2 | 2026-09-01
```

`0` در جای محدودیت یعنی بدون محدودیت همان فیلد. برای percent مقدار بیش از ۱۰۰ معتبر نیست. `discount_delete` فقط کدی را حذف می‌کند که هنوز روی هیچ سفارشی اعمال نشده باشد؛ برای کد استفاده‌شده از `discount_toggle` جهت غیرفعال‌سازی استفاده کنید تا سابقه مالی حفظ شود.

## ۱۳. پشتیبانی و FAQ

```text
/tickets [open|answered|closed|all] [PAGE]
/tickets open 2
/ticket TICKET_NUMBER
/ticket_attachment MESSAGE_ID
/ticket_reply TICKET_NUMBER | پاسخ
/ticket_status TICKET_NUMBER open|answered|closed
/ticket_close TICKET_NUMBER
```

فهرست تیکت‌ها بر اساس `updated_at` نزولی، ۲۰ ردیف در صفحه، با total و راهنمای قبلی/بعدی است. شماره صفحه نامعتبر رد می‌شود و محدودیت طول Telegram باعث حذف تیکت از صفحه نمی‌شود؛ برای پرونده مشخص از `/ticket TICKET_NUMBER` استفاده کنید.

`/ticket_status` برای هر سه نقش `owner`، `admin` و `support` مجاز است. مقدار `open` تیکت بسته را باز می‌کند و زمان بسته‌شدن را پاک می‌کند؛ `answered` پاسخ‌داده‌شدن و `closed` بسته‌شدن را ثبت می‌کند. مدیر اجراکننده نیز به‌عنوان مدیر مسئول تیکت ثبت می‌شود و تغییر وضعیت به کاربر اعلام می‌گردد. `/ticket_close` میان‌بری برای بستن تیکت است.

`/ticket` تمام متن پیام‌های گفت‌وگو را، حتی وقتی یک پیام بلند یا دارای نویسه‌های HTML باشد، در بخش‌های کامل نشان می‌دهد. به‌جای افشای raw file ID، برای هر پیام فایل‌دار شناسه `MESSAGE_ID` را نشان می‌دهد. `/ticket_attachment MESSAGE_ID` برای `owner/admin/support` همان photo/document ذخیره‌شده را پس از بررسی دوباره role، وجود پیام و تیکت مرتبط بازمی‌فرستد؛ این فرمان را فقط در گفت‌وگوی خصوصی مدیریتی اجرا کنید. دسترسی به پیوست تیکت مجوز `/payment_detail` یا `/order_attachment` نیست.

```text
/faq_categories [PAGE]
/faq_category_add عنوان دسته
/faq_category_toggle CATEGORY_ID
/faq_category_set CATEGORY_ID | name | عنوان تازه
/faq_category_set CATEGORY_ID | sort_order | 10
/faq_category_delete CATEGORY_ID
/faqs [CATEGORY_ID|all] [PAGE]
/faqs CATEGORY_ID 1
/faq_add دسته | سوال | جواب
/faq_toggle FAQ_ID
/faq_set FAQ_ID | question | متن سوال تازه
/faq_set FAQ_ID | answer | متن پاسخ تازه
/faq_set FAQ_ID | category | CATEGORY_ID
/faq_set FAQ_ID | sort_order | 10
/faq_delete FAQ_ID
```

برای بی‌دسته‌کردن یک FAQ، مقدار `category` را `0` یا `none` بگذارید. حذف دسته FAQ فقط وقتی ممکن است که هیچ سوالی، حتی سوال غیرفعال، در آن نباشد؛ سوال‌ها را ابتدا جابه‌جا یا حذف کنید.

پیش از بستن تیکت مطمئن شوید پاسخ نهایی برای کاربر ارسال شده است. اطلاعات محرمانه انبار یا secretهای پرداخت را در متن تیکت قرار ندهید.

## ۱۴. پیام گروهی و گزارش

```text
/broadcast_all متن
/broadcast_joined FROM_DATE | TO_DATE | متن
/broadcast_product PRODUCT_ID | FROM_DATE | TO_DATE | متن
```

قبل از ارسال، ربات تعداد مخاطب هدف را نشان می‌دهد و تأیید می‌گیرد. تاریخ‌ها را به شکل `YYYY-MM-DD` وارد کنید. ابتدا روی گروه کوچک آزمایش کنید؛ ارسال انبوه را پشت سرهم تکرار نکنید و نتیجه موفق/ناموفق را بررسی کنید.

گزارش:

```text
/report orders FROM_DATE TO_DATE
/report orders all FROM_DATE TO_DATE
/report orders STATUS FROM_DATE TO_DATE
/report users FROM_DATE TO_DATE
/report users joined FROM_DATE TO_DATE
/report users started FROM_DATE TO_DATE
/report users product PRODUCT_ID FROM_DATE TO_DATE
/report users product all FROM_DATE TO_DATE
/report finance FROM_DATE TO_DATE
```

- `orders` بدون status یا با `all` همه سفارش‌های ساخته‌شده در بازه، از جمله تاریخچه تخصیص داخلی `ADM-...`، را برمی‌گرداند؛ می‌توان هر وضعیت معتبر سفارش را جای `STATUS` گذاشت. مبنای تاریخ این گزارش `created_at` است.
- `users` بدون mode، یا با `joined`/`started`، کاربران شروع‌کننده و عضو‌شده در بازه را بر اساس `joined_at` گزارش می‌کند؛ `started` نام مستعار `joined` است.
- `users product PRODUCT_ID` خریداران یک محصول و `users product all` خریداران هر محصول را بر اساس `paid_at` و وضعیت‌های موفق، فقط برای `order_origin=customer` با subtotal مثبت گزارش می‌کند.
- `finance` سفارش‌های تجاری `order_origin=customer` با subtotal مثبت و `paid_at` در بازه را برای وضعیت‌های مالی مرتبط، شامل `refunded`، گزارش می‌کند؛ تخصیص داخلی/سفارش صفرمبلغ در CSV مالی نیست.
- تاریخ‌ها بر اساس `TIMEZONE` تفسیر می‌شوند و روز پایان به‌طور کامل داخل بازه است. خروجی ابتدا خلاصه متنی است و اگر حداقل یک ردیف وجود داشته باشد، فایل CSV نیز ارسال می‌شود.
- در گزارش `orders` و `finance`، شمار سفارش، تعداد تکمیل‌شده و درآمد ناخالص از همان ردیف‌های CSV و همان فیلتر محاسبه می‌شوند؛ `orders` ممکن است ردیف داخلی `ADM-...` را در شمار/تکمیل داشته باشد ولی مبلغ آن صفر است، در حالی که `finance` فقط ردیف تجاری دارد. شمار کاربرانِ نمایش‌داده‌شده در این دو گزارش از کاربران دارای `joined_at` در بازه می‌آید. در گزارش `users` فقط شمار کاربران از ردیف‌های CSV همان فیلتر می‌آید و شاخص‌های سفارش از summary عمومی سفارش‌های ساخته‌شده در بازه باقی می‌مانند. شمار تیکت باز در همه حالت‌ها یک شاخص سراسری لحظه‌ای است؛ این شاخص‌های داشبورد الزاماً با دامنه ردیف‌های CSV یکی نیستند.
- ستون `external_paid_amount` سهم بیرونیِ settleشده فروش همان Order است، نه همه دریافتی خام درگاه. completed ارزی دیررس برای Order terminal، حتی پس از `credit_confirmed`، به‌صورت `manual_credit` جبرانی کیف پول ثبت می‌شود و Order/درآمد فروش/`external_paid_amount` آن را احیا نمی‌کند.

## ۱۵. قوانین پاداش دعوت

```text
/rewards [PAGE]
/reward_add start | AMOUNT | 0
/reward_add first_purchase | AMOUNT | PRODUCT_ID|0
/reward_add product_purchase | AMOUNT | PRODUCT_ID|0
/reward_add combined | AMOUNT | PRODUCT_ID|0 | CONDITIONS_JSON
/reward_add combined | AMOUNT | PRODUCT_ID|0 | CONDITIONS_JSON | START_DATE|0 | END_DATE|0
/reward_toggle RULE_ID
```

برای محدودکردن قاعده به بازه زمانی، دو آرگومان اختیاری تاریخ را اضافه کنید: `| START_DATE|0 | END_DATE|0`. قالب تاریخ `YYYY-MM-DD` است و تاریخ پایان به‌طور کامل شامل می‌شود. مثال:

```text
/reward_add product_purchase | 50000 | 12 | 2026-09-01 | 2026-09-30
```

پاداش به دعوت‌کننده واریز می‌شود: `start` پس از عبور کاربر معرفی‌شده از access guard `/start`، از جمله تأیید جوین اجباری در صورت فعال‌بودن، اجرا می‌شود؛ `first_purchase` هنگام نخستین خرید تجاری موفق او و `product_purchase` برای هر خرید تجاری منطبق اجرا می‌شود. خرید تجاری یعنی Order با `order_origin=customer` و `subtotal_amount > 0`؛ تخصیص `ADM-...`/`admin_assignment` و سفارش داخلی صفرمبلغ پاداش نمی‌سازند و نخستین خرید را مصرف نمی‌کنند. رویداد `start` محصول‌محور نیست؛ برای آن `PRODUCT_ID` را حتماً `0` بگذارید. برای دو رویداد خرید، `0` یعنی همه محصولات و شناسه غیرصفر خرید جاری را به همان محصول محدود می‌کند. بازه‌ها بر اساس `TIMEZONE` ربات محاسبه می‌شوند.

برای `combined`، آرگومان چهارم باید یک شیء JSON معتبر با حداقل یک شرط مؤثر باشد. همه شرط‌های ثبت‌شده هم‌زمان، با منطق AND، بررسی می‌شوند. کلیدهای مجاز:

- `minimum_successful_purchases`: حداقل تعداد خریدهای موفق کاربر معرفی‌شده، شامل خرید جاری؛ عدد صحیح نامنفی.
- `first_purchase`: اگر `true` باشد، خرید جاری باید نخستین خرید موفق کاربر معرفی‌شده باشد؛ مقدار باید boolean واقعی JSON باشد.
- `minimum_referrals`: حداقل تعداد کاربرانی که خودِ کاربر معرفی‌شده دعوت کرده است؛ عدد صحیح نامنفی.
- `minimum_qualified_referrals`: حداقل تعداد دعوت‌های واجد شرایط همان کاربر؛ عدد صحیح نامنفی.
- `product_ids`: فهرست غیرخالی شناسه محصولات مجاز برای خرید جاری؛ هر شناسه باید عدد صحیح مثبت و محصول موجود باشد.
- `minimum_order_amount`: حداقل مبلغ سفارش جاری پس از کسر تخفیف؛ عدد صحیح نامنفی به تومان (`TOMAN`).

نمونه پاداش برای دومین خرید موفق به بعد، به شرط حداقل مبلغ ۵۰۰۰۰۰:

```text
/reward_add combined | 75000 | 0 | {"minimum_successful_purchases":2,"minimum_order_amount":500000}
```

نمونه پاداش فقط برای نخستین خرید یکی از دو محصول و در یک بازه:

```text
/reward_add combined | 50000 | 0 | {"first_purchase":true,"product_ids":[12,15]} | 2026-09-01 | 2026-09-30
```

`PRODUCT_ID` بیرون JSON نیز محصول خرید جاری را محدود می‌کند؛ برای همه محصولات `0` بگذارید و برای چند محصول از `product_ids` استفاده کنید. شرط خالی، شرط‌هایی که همگی صفر/false هستند، کلید ناشناخته، نوع مقدار نادرست و ترکیب `first_purchase:true` با `minimum_successful_purchases` بزرگ‌تر از ۱ رد می‌شوند. JSON باید با syntax استاندارد، از جمله `true`/`false` انگلیسی و اعداد لاتین، نوشته شود.

پیش از فعال‌سازی عمومی، قوانین سوءاستفاده، خرید برگشتی و سقف پاداش را مشخص و با حساب آزمایشی بررسی کنید.

## ۱۶. بکاپ

```text
/backup
```

این فرمان در خود ربات فقط برای نقش `owner` مجاز است؛ نقش‌های `admin` و `support` با خطای دسترسی روبه‌رو می‌شوند. فایل خروجی را فقط در گفت‌وگوی خصوصی مالک دریافت و یک نسخه رمزگذاری‌شده خارج از سرور نگه دارید. SQLite در حالت WAL است؛ برای کپی دستی هنگام اجرای ربات، صرفاً فایل `.sqlite3` را copy نکنید. از فرمان داخلی بکاپ یا SQLite backup API استفاده کنید، یا سرویس را متوقف و مجموعه فایل‌های مرتبط را به‌صورت سازگار کپی کنید. بازیابی را دوره‌ای در محیط جدا تست کنید.

## چک‌لیست کار روزانه

1. سفارش‌های `awaiting_confirmation`، `awaiting_stock` و `awaiting_info` را بررسی کنید.
2. موجودی محصولات پرتقاضا را کنترل کنید.
3. تیکت‌های باز را پاسخ دهید.
4. خطاهای پرداخت و لاگ سرویس را بررسی کنید.
5. از موفق‌بودن بکاپ اخیر مطمئن شوید.
6. هیچ secret یا payload اکانت را در کانال یا گروه منتشر نکنید.
