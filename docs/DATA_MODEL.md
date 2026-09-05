# مدل داده و invariantهای پایگاه داده

چیدمان مشتری بدون migration و در schema 11 ذخیره می‌شود: `settings` با کلید `customer_layout:SECTION`، سند current/version/history (حداکثر ۱۰ نسخه)/updated_by/updated_at؛ `user_states` با state جدید `admin:layouts`، draft و actor/chat/token/revision/phase/نسخهٔ مبنا. ذخیره و effect `customer-layout` در همان `processed_admin_updates` اتمیک‌اند. config هیچ اطلاعات شخصی سفارش/تیکت یا action payload ندارد. `customer_layouts_enabled` کلید عملیاتی پیش‌فرض true برای خاموش‌کردن صرفاً reflow است. [طرح و invariant دقیق](CUSTOMER_LAYOUTS.md).

ناوبری درختی نیز schema را تغییر نمی‌دهد. JSON حالت `admin:catalog` شامل kind/id، actor/chat، page/search، field یا product_id در صفحات مربوط، category_context/stock_context برای بازگشت و prompt_message_id است؛ payload اکانت در این state یا callback مرور نیست. فرم زمینه‌دار `admin:ui` دو فیلد اختیاری `return_to` و `minimum_step` دارد: اولی مقصد بازگشت و دومی مرز اصلاح است تا target/مشخصه پیش‌انتخاب‌شده عوض نشود. callback مرور شناسه پایدار دارد و فقط صفحه یا فرم را باز می‌کند؛ نوشتن همچنان نیازمند token/revision و تأیید فرم است. [قرارداد](ADMIN_HIERARCHY.md).

افزوده رابط دکمه‌ای بدون migration: `user_states` از مقدار `admin:ui` برای action/actor/chat، token، revision، step، مقادیر مستقل، گزینه‌ها، last_input و وضعیت editing/confirm/executing/done استفاده می‌کند. executing هویت تأیید را پیش از handler نگه می‌دارد؛ message ID داخلی از token فرم ساخته می‌شود. secret پس از موفقیت از state حذف می‌شود. جدول processed_admin_updates و effect یکتای handler تغییر نکرده و schema 11 است. ساختار و crash boundary در [BUTTON_UI.md](BUTTON_UI.md).

## خلاصه

جوین اجباری از state مرور `admin:joins` با kind=list/channel، id، page، actor/chat و prompt_message_id استفاده می‌کند؛ فرم آن `return_to.scope=joins` دارد. schema همچنان 11 است و هیچ migration یا بازنویسی کانال در rollout انجام نمی‌شود. [جزئیات](ADMIN_JOINS.md).

JSON فرم یک `prompt_message_id` اختیاری نیز دارد؛ این فقط شناسهٔ آخرین keyboard قابل retire در private chat خودش است، نه شناسهٔ عملیات مالی. هر بازنماییِ انتخابگر revision تازه دارد و گزینه‌های متناظر پیش از ارسال ذخیره می‌شوند. داده نسخهٔ قبل بدون این فیلد نیاز به migration ندارد. شرح ممیزی در [BUTTON_UI_AUDIT.md](BUTTON_UI_AUDIT.md).

پایگاه داده SQLite تنها source of truth سامانه است. همه timestampها به شکل ISO-8601 UTC ذخیره می‌شوند، مبلغ‌ها integer و فقط با currency برابر `TOMAN` هستند و اعشار شناور در منطق مالی استفاده نمی‌شود. schema پایه در `app/schema.sql` و migrationهای idempotent در `Database._migrate_schema` قرار دارند.

نمودار ارتباطی خلاصه در [DIAGRAMS.md](DIAGRAMS.md) است. برای تغییر schema، بخش migration در [development.md](development.md) باید رعایت شود.

## گروه‌های داده

### هویت و پیکربندی

| جدول | هدف | نکته کلیدی |
|---|---|---|
| `users` | هویت Telegram، پروفایل خرید و وضعیت block | `telegram_user_id` و `chat_id` یکتا |
| `admins` | نقش و هویت پایدار مدیر | نقش یکی از `owner/admin/support`؛ مجوز فقط با `identity_verified_at` و chat ID، marker یکتای `is_bootstrap_owner` |
| `settings` | تنظیمات JSON و offset polling | `telegram_update_offset` فقط پس از ACK جلو می‌رود؛ NACK موقت DB آن را ثابت نگه می‌دارد |
| `user_states` | state پایدار مکالمه | یک state فعال برای هر user |
| `force_join_channels` | کانال‌های جوین اجباری | chat ID یکتا و invite فقط canonical HTTPS عمومی Telegram |
| `schema_meta` | نسخه schema | نسخه جاری ۱۱ |
| `processed_admin_updates` | journal replay فرمان مدیر | update ID، fingerprint، status `started/completed`، اثر freezeشده و زمان‌ها |

### کاتالوگ و fulfillment

| جدول | هدف | نکته کلیدی |
|---|---|---|
| `categories` | دسته و زیردسته | FK خودارجاع، حذف محدودشده؛ `source_admin_update_id` برای create replay-safe |
| `products` | محصول آماده/دستی و سیاست فروش | soft delete، قیمت فقط `TOMAN`، `rules_url` فقط HTTPS مطلق بدون host literal محلی/خصوصی و snapshot در سفارش |
| `inventory_items` | payload محرمانه محصولات آماده | hash یکتا در هر محصول، تخصیص یک‌باره و `source_admin_update_id` برای create replay-safe |
| `reservations` | صف محصول ناموجود | برای سفارش یکتا؛ اولویت تخصیص بر زمان پرداخت Order و سپس Order ID |
| `orders` | قرارداد خرید/تخصیص و snapshot محصول | تمام مبلغ‌ها، lifecycle و `order_origin=customer|admin_assignment` |
| `reminders` | یادآوری پایان اشتراک | برای هر order و days_before نامنفی یکتا؛ صفر آغاز روز محلی پایان اشتراک است |

### قیمت، پرداخت و کیف پول

| جدول | هدف | نکته کلیدی |
|---|---|---|
| `discounts` | قانون تخفیف ثابت/درصدی | scope محصول/کاربر، بازه و محدودیت مصرف |
| `order_discounts` | تخفیف اعمال‌شده | حداکثر یک تخفیف فعال برای سفارش |
| `payments` | intent پرداخت سفارش یا شارژ | currency فقط `TOMAN`؛ idempotency، مبلغ قابل تطبیق، external reference و URL امن provider |
| `payment_receipt_attachments` | نوع و شناسه فیش کارت | یک ردیف به‌ازای payment؛ حفظ `photo/document` برای ارسال مجدد؛ تغییر محتوا alert نسخه‌دار تازه می‌سازد |
| `card_payment_events` | دفتر رخداد بانک با reference یکتا | مسیر دامنه‌ای آن را append-only به‌کار می‌برد؛ برخلاف wallet، trigger منع UPDATE/DELETE ندارد |
| `card_payment_event_resolutions` | تصمیم مالک روی card event در review | event یکتا؛ action، actor، note و زمان ثبت می‌شود |
| `provider_payment_events` | شاهد پاسخ provider | payload+hash immutable؛ disposition یکی از completed/failed/review |
| `provider_payment_event_resolutions` | حل review provider | تصمیم مالک یا حل خودکار با رخداد زنده بعدی؛ immutable |
| `card_payment_cancellations` | تاریخچه لغو intent کارت | payment یکتا؛ ورودی شمارنده cooldown |
| `payment_security_events` | audit محدودیت ضد-churn کارت | کلید پایدار؛ نوع daily-limit یا cancel-cooldown و جزئیات JSON |
| `wallet_entries` | ledger کیف پول | append-only با trigger منع UPDATE/DELETE |

### پشتیبانی و پیام‌رسانی

| جدول | هدف | نکته کلیدی |
|---|---|---|
| `faq_categories` / `faqs` | محتوای سوال متداول | ترتیب و active flag |
| `tickets` | سرآیند تیکت | مالک user و وضعیت open/answered/closed |
| `ticket_messages` | مکالمه و attachment | sender user/admin دقیقاً یکی؛ kind/file ID عکس یا سند برای `/ticket_attachment` پایدار می‌ماند |
| `outbound_messages` | outbox پایدار | idempotency و state ارسال |
| `outbound_message_attempts` | شمارنده retry | یک ردیف برای هر پیام |
| `broadcast_batches` | کار دسته‌ای ارسال گروهی | نتیجه واقعی پس از اتمام گزارش می‌شود |
| `broadcast_batch_messages` | اتصال batch به outbox | هر پیام فقط عضو یک batch |

هویت delegated admin ابتدا فقط یک grant pending است: `identity_verified_at=NULL` هیچ دسترسی نمی‌دهد. نخستین private update باید همان `chat_id` و username ثبت‌شده را هم‌زمان اثبات کند؛ سپس chat ID anchor مجوز و username metadata قابل refresh از همان chat می‌شود. index یکتای `is_bootstrap_owner=1` یک root پایدار می‌سازد؛ restart `is_active`/role موجود را بازنویسی نمی‌کند و marker فقط به owner فعال و verifyشده منتقل می‌شود.

`processed_admin_updates` یک preclaim دائمی نیست. `begin_admin_update` payload را fingerprint می‌کند: `completed` skip، `started` در replay همان payload دوباره اجرا و payload متفاوت برای update ID یکسان conflict می‌شود. `get_or_store_admin_update_effect` مقصد عملیات non-idempotent مانند toggle را پیش از mutation در `effect_json` freeze می‌کند؛ `complete_admin_update` فقط پس از بازگشت عادی handler زمان `completed_at` را ثبت می‌کند. خطای موقت پایهٔ دیتابیس در begin، mutation یا complete به `False` صریح تبدیل می‌شود تا poller offset همان update و موارد بعدی batch را ذخیره نکند و از offset ثابت retry کند. createهای category/inventory با `source_admin_update_id` و mutationهای دامنه‌ای با idempotency خودشان replay را مهار می‌کنند؛ خطای terminal دامنه/Telegram ACK می‌شود و ارسال شبکه‌ای exactly-once تضمین نمی‌شود.

در first-contact، `create_order(..., order_notice=...)` خود Order و پیام خلاصه `order:{id}:created-summary` را در یک transaction می‌سازد و state خرید فقط پس از این commit پاک می‌شود. پیام canonical موفقیت فروش برای Payment بیرونی کلید `payment:{id}:order-confirmed` و برای wallet-only/تخفیف کامل/خرید رایگان تأییدشده کاربر کلیدهای `order:{id}:wallet-confirmed`، `order:{id}:discount-confirmed` و `order:{id}:free-confirmed` دارد. `order_success_notice_ready` تا وقتی outbox وجود ندارد یا `queued/sending` است fulfillment را می‌بندد؛ `sent|failed|cancelled` آن را باز می‌کند. بنابراین شکست terminal پیام در outbox قابل مشاهده می‌ماند ولی paid Order را برای همیشه متوقف نمی‌کند.

### دعوت، پاداش و عملیات

| جدول | هدف | نکته کلیدی |
|---|---|---|
| `referrals` | نسبت inviter/invitee | invitee یکتا و self-referral ممنوع |
| `reward_rules` | قوانین start/purchase/combined | window و زمان ایجاد در eligibility مؤثر است |
| `reward_events` | رخداد پاداش دقیقاً یک‌باره | یکتایی rule + referral + event key |
| `backups` | audit فایل backup | hash، اندازه و نتیجه عملیات |

## نماها و queryهای مدیریتی صفحه‌بندی‌شده

page size فرمان‌های مدیریتی پرتعداد ثابت و برابر ۲۰ است. count و list باید دقیقاً فیلتر یکسان داشته باشند تا `صفحه X از Y | مجموع: N` قابل اتکا بماند:

- users: `count_users(search=None, blocked=None)` و list متناظر؛ فهرست عمومی و فیلترهای active/blocked/new/inactive/joined/product با `id DESC`، offset و limit اجرا می‌شوند؛
- orders: `list_orders`/`count_orders` با `user_id`, `status`, `product_id`, `created_from`, `created_until` و ترتیب `id DESC`؛
- tickets: `list_tickets`/`count_tickets` با `user_id`/`status` و ترتیب `updated_at DESC`؛
- history user: `list/count_user_transactions` جدیدترین‌اول، `list/count_user_referrals` با `referrals.id DESC` و aggregate تعداد/مجموع پاداش هر invitee، و `list/count_user_reward_events` جدیدترین‌اول با جزئیات invitee/order؛
- `user_summary` تاریخ اولین و آخرین خرید را با MIN/MAX بدون cap فقط از Orderهای تجاری `order_origin=customer`, `subtotal_amount>0` و موفق محاسبه می‌کند. preview ده/پنج ردیفی `/user` منبع این aggregate نیست.

صفحه کمتر از ۱ یا بیشتر از تعداد صفحات fail closed است. search شماره سفارش در `/user_orders` علاوه بر lookup، `order.user_id` را با user هدف تطبیق می‌دهد. `_send_blocks` محدودیت Telegram را با چند پیام حل می‌کند و نباید ردیفی از همان صفحه را drop کند.

## وضعیت سفارش

فهرست‌های category/product/inventory/discount/reward/FAQ/admin نیز count هم‌فیلتر و page size بیست دارند؛ `_management_rows` و `_send_page` کل صفحه را نگه می‌دارند. `list_reward_rules` پارامتر offset دارد تا هم مدیر و هم توضیح قواعد فعال کاربر به رکوردهای پس از حد پیش‌فرض دسترسی داشته باشند؛ تغییر schema لازم نیست.

| status | معنا | وضعیت مالی/عملیاتی |
|---|---|---|
| `pending_payment` | سفارش ساخته شده و مبلغ خارجی باقی است | wallet ممکن است hold شده باشد |
| `awaiting_confirmation` | فیش/تأیید در انتظار بررسی | payment معمولاً `verifying` |
| `paid` | پرداخت کامل ثبت شده | آماده ورود به fulfillment |
| `awaiting_stock` | محصول آماده پرداخت شده ولی موجودی ندارد | رزرو FIFO فعال |
| `awaiting_info` | محصول دستی منتظر اطلاعات کاربر | پرداخت نهایی است |
| `processing` | کار عملیاتی در جریان | معمولاً manual پس از دریافت اطلاعات؛ همچنین ready در race اتمام موجودی وقتی reserve غیرفعال است که پس از restock توسط maintenance خودکار fulfil می‌شود |
| `completed` | تحویل/فعال‌سازی انجام شده | reminder ممکن است ساخته شود |
| `rejected` | سفارش رد شده | terminal مگر عملیات صریح جبرانی |
| `expired` | مهلت پرداخت تمام شده | hold و تخفیف release می‌شوند |
| `cancelled` | لغو شده | terminal |
| `refunded` | وضعیت رزروشده برای بازپرداخت هماهنگ | terminal؛ workflow ورود به آن در نسخه فعلی پیاده نشده است |

قانون مهم: هیچ handler نباید با `UPDATE orders SET status=...` دلخواه از invariantهای مالی عبور کند. سه وضعیت `paid`، `completed` و `refunded` از setter عمومی وضعیت رد می‌شوند: دو وضعیت نخست فقط از workflow تأیید/ثبت پرداخت و fulfillment یا تکمیل معتبر manual به‌دست می‌آیند؛ `refunded` تا زمان افزودن workflow مالی اثبات‌شده و ثبت جبرانی هماهنگ قابل ورود نیست. setter عمومی همچنین `cancelled|expired|rejected` را تا وقتی external payment همان Order در `pending/verifying` است رد می‌کند؛ فیش card و crypto provider باید از workflow اختصاصی خود عبور کنند. سایر transitionها نیز فقط از متدهای دامنه‌ای مجاز انجام می‌شوند.

برای سفارش manual، `submit_manual_order_info` اعتبار payload، مالکیت user، نوع snapshot و status `awaiting_info|processing` را داخل همان transaction بررسی می‌کند و `customer_info_json` و status=`processing` را با هم می‌نویسد. replay payload برابر در processing no-op و replacement معتبر نسخه تازه است؛ terminal یا actor دیگر fail closed می‌شود. query alert recovery ردیف‌های legacy دارای payload در هر دو status `awaiting_info/processing` را نیز می‌بیند.

## وضعیت پرداخت

| status | معنا |
|---|---|
| `pending` | intent معتبر و پرداخت‌نشده |
| `verifying` | رسید ارسال شده یا نیازمند بررسی |
| `paid` | reference/تأیید نهایی ثبت شده؛ terminal در نسخه فعلی |
| `failed` | پرداخت ناموفق؛ سفارش در صورت امکان دوباره pending می‌شود |
| `cancelled` | intent کارتِ مجاز به لغو صریح کاربر؛ جایگزینی ضمنی topup وجود ندارد |
| `expired` | intent منقضی؛ parent order reconcile می‌شود |
| `refunded` | مقدار schema رزروشده؛ هیچ transition اجرایی فعلی به آن وجود ندارد |

`purpose` فقط `order` یا `wallet_topup` و `currency` فقط `TOMAN` است. در اولی `order_id` اجباری و در دومی باید NULL باشد. هر order در هر لحظه حداکثر یک payment بیرونی با status `pending/verifying` در هر دو روش card/crypto دارد؛ این invariant داخل transaction ساخت payment اعمال می‌شود. Payment crypto هر دو purpose می‌تواند پیش از تماس شبکه provisional و دارای invoice ID/URL تهی باشد؛ base amount و terms آن immutable و `payment_number` merchant order ثابت provider است. `attach_crypto_invoice` نتیجه exact همان invoice را اتمیک attach می‌کند. `external_reference` و invoice ID یکتا هستند. `provider_invoice_url`، در صورت وجود، باید URL مطلق HTTPS بدون credential، `localhost`، IP literal محلی/خصوصی/reserved یا host عددی مبهم باشد؛ validator DNS را resolve نمی‌کند. renderer Order/Wallet همین URL ذخیره‌شده را دوباره اعتبارسنجی می‌کند و برای provisional دکمه retry و فقط برای crypto attach‌شده دکمه resume می‌سازد؛ فیش فقط card است.

فیش تنها برای payment نوع `card` و status یکی از `pending/verifying` پذیرفته می‌شود. اگر `receipt_file_id` هنوز NULL است، زمان ثبت باید strictly پیش از `expires_at` باشد؛ پس از ثبت نخستین فیش در مهلت، پرداخت تا تأیید یا رد صریح مدیر verifying می‌ماند؛ جایگزینی فیش تا تصمیم نهایی مجاز است و deadline نخستین ارسال تغییر نمی‌کند. لغو کاربر تنها روی card payment متعلق به او با status=`pending` و `receipt_file_id IS NULL` انجام می‌شود و لغو payment، reconciliation سفارش والد، release hold/discount و cancellation reminder/reservation یک transaction واحد است. crypto invoice صادرشده با repository یا callback قدیمی کاربر لغو نمی‌شود؛ deadline محلی آن را terminal نمی‌کند و poll تا شاهد terminal provider و بررسی late transition ادامه دارد. تغییر card به crypto پس از لغو card به ساخت order تازه نیاز دارد.

مبلغ یکتای payment کارت بعد از terminalشدن فوراً آزاد نمی‌شود. هر `(TOMAN, payable_amount)` کارت تا ۲۴ ساعت پس از `max(expires_at, updated_at)` سابقه terminal quarantine است؛ lookup تخصیص مبلغ هم active collision و هم این تاریخچه را می‌بیند. index `idx_payments_amount_history` این scan محدود را پشتیبانی می‌کند.

برای ساخت `wallet_topup`، query فعال بر `(user_id, purpose)` و هر دو روش card/crypto است. وجود هر topup `pending/verifying` برای همان user ساخت intent دوم را می‌بندد؛ replay فقط وقتی همان رکورد را می‌دهد که method، `base_amount` و terms همان باشد و اختلاف هرکدام conflict است. هیچ topup به‌طور ضمنی replace/cancel نمی‌شود. query فهرست کیف پول عمداً تمام ردیف‌های فعال را برمی‌گرداند تا داده legacy دارای card و crypto هم‌زمان هر دو برای resume دیده شوند؛ این استثنای نمایش invariant ساخت را تضعیف نمی‌کند. provisional crypto در poll query نیست و UI retry همان مبلغ/terms را عرضه می‌کند. uniqueness مبلغ payable فقط برای card در index جزئی `uq_active_payment_payable_amount` اعمال می‌شود؛ crypto با `provider_invoice_id` و `external_reference` یکتا تطبیق می‌یابد و مبلغش برای ایجاد شناسه مصنوعی تغییر نمی‌کند.

## شاهد provider و review مالی

هر نتیجه پولی Plisio ابتدا در `provider_payment_events` نوشته می‌شود. کلید یکتای `(provider, payment_id, raw_payload_sha256)` replay payload یکسان را idempotent می‌کند و triggerهای schema، UPDATE/DELETE رخداد و resolution آن را ممنوع می‌کنند. `provider_reference` باید با invoice همان payment منطبق باشد.

- `completed`: فقط status دقیق completed و هویت `id/type=invoice` معتبر؛ settlement از این شاهد durable انجام می‌شود. `operation.amount` مقدار crypto دریافتی است، نه تومان، و با `base_amount` مستقیم مقایسه نمی‌شود.
- `failed`: فقط status terminal پشتیبانی‌شده همراه `amount_evidence=zero`؛ payment و parent Order reconcile می‌شوند.
- `review`: partial/nonzero، مبلغ نامعلوم، پاسخ malformed/ناهمخوان، mismatch فیلدهای fiat/source در `params` در صورت حضور، یا نتیجه‌ای که بدون شاهد کافی قابل تسویه نیست؛ payment باز به `verifying` می‌رود و اعتبار خودکار ساخته نمی‌شود.

maintenance پیش از poll شبکه، رخدادهای completed ثبت‌شده ولی اعمال‌نشده را می‌خواند تا crash بین ثبت شاهد و settlement را بدون تماس دوباره با provider بازیابی کند. poll روی cryptoهای `pending/verifying` حتی پس از deadline محلی ادامه دارد تا provider نتیجه terminal بدهد. مشاهده بعدی completed یا terminal-zero، reviewهای باز قبلی را با `resolving_event_id` می‌بندد. resolution دستی به مالک فعال و note الزامی محدود است؛ هر اثر اعتباری باید به شاهد completed دقیق invoice متصل و در wallet ledger با idempotency key پرداخت ثبت شود، نه صرفاً از انتخاب status. برای Order terminal، `credit_confirmed` خود Payment را برای ثبت دریافت قطعی `paid` می‌کند و دقیقاً یک `manual_credit` به اندازه `payments.base_amount` با `order_id=NULL` می‌سازد؛ Order و `external_paid_amount` آن دست‌نخورده می‌مانند.

`provider_payment_event_resolutions.action` برای تصمیم مالک یکی از `dismiss/refund_confirmed/credit_confirmed` و برای حل خودکار یکی از `provider_completed/provider_terminal_zero` است. در مسیر خودکار `actor_admin_id` تهی و `resolving_event_id` اجباری است؛ در مسیر دستی برعکس. schema ۷ این CHECK را با rebuild تراکنشی جدول نسخه ۶ گسترش می‌دهد و داده و triggerهای immutable را حفظ می‌کند.

card event نامنطبق/دیررس با status=`review` حفظ و با `card_payment_event_resolutions` فقط حسابرسی/تعیین تکلیف می‌شود؛ این مسیر به‌خودی‌خود Payment را paid یا کیف پول را credit نمی‌کند. محدودیت ساخت کارت از شمار ۲۰ intent در ۲۴ ساعت و ۳ لغو در یک ساعت استفاده می‌کند. ردهای جدید در `payment_security_events` ثبت می‌شوند و maintenance برای آن‌ها اعلان پایدار مدیریتی می‌سازد.

## معادله سفارش

```text
subtotal = unit_price * quantity
net_total = subtotal - discount_amount
effective_wallet_hold = wallet_held_amount - wallet_refunded_amount
external_payable = payable_amount = net_total - effective_wallet_hold
remaining_external = external_payable - external_paid_amount
```

در زمان intent، مبلغ کیف پول ابتدا hold می‌شود و `payable_amount` مانده‌ای است که باید از روش بیرونی تأمین شود؛ بنابراین پیش از تسویه نمی‌توان `wallet_captured_amount` را در فرمول مانده جای hold مؤثر گذاشت. هر payment بیرونی که همان Order باز را با موفقیت settle کند بخشی از payable را در `external_paid_amount` جمع می‌کند. این فیلد سهم بیرونیِ فروش تحقق‌یافته است، نه همه وجه مشاهده‌شده در درگاه: completed دیررس برای Order terminal، Order را احیا یا این aggregate را زیاد نمی‌کند و فقط با تصمیم مالک به `manual_credit` جبرانی کیف پول می‌رود. وقتی `external_paid_amount + effective_wallet_hold >= net_total` شد، سهم کیف پول capture و در `wallet_captured_amount` snapshot می‌شود. با لغو/انقضای پیش از capture، hold آزاد می‌شود. `order_refund` نوع ledger لازم برای workflow بازپرداخت آینده است، اما نسخه فعلی transition refund payment/order را ارائه نمی‌کند؛ گزارش مالی فقط refund واقعاً ثبت‌شده در ledger را می‌خواند، نه aggregate snapshot یا status دستی.

## ledger کیف پول

موجودی:

```sql
SELECT COALESCE(SUM(amount_signed), 0)
FROM wallet_entries
WHERE user_id = ?;
```

انواع entry:

- `topup`: شارژ موفق؛
- `admin_adjustment`: تغییر ثبت‌شده مدیر؛
- `wallet_hold`: کسر موقت/مصرف کیف پول برای سفارش؛
- `wallet_refund`: آزادسازی hold استفاده‌نشده؛
- `order_refund`: entry جبرانی برای workflow بازپرداخت اثبات‌شده؛ transition عمومی refund در نسخه فعلی در دسترس نیست؛
- `referral_reward`: پاداش دعوت؛
- `manual_credit` و `manual_debit`: مسیرهای داخلی صریح.

هر entry باید reason، actor/entity مرتبط و idempotency key پایدار داشته باشد. UPDATE و DELETE با trigger ممنوع است؛ اصلاح با entry جدید و علامت معکوس انجام می‌شود.

## snapshot سفارش

فیلدهای `product_name_snapshot`، icon، type، duration و قیمت در لحظه ایجاد سفارش کپی می‌شوند. تغییر یا soft-delete محصول نباید فاکتور و تاریخچه گذشته را تغییر دهد. زمان پایان اشتراک از زمان تحویل واقعی (`completed_at`) محاسبه می‌شود، نه زمان پرداخت.

`orders.order_origin` مرز خرید تجاری را پایدار می‌کند: سفارش عادی `customer` و تخصیص مستقیم مدیر `admin_assignment` است. مسیرهای پاداش خرید، نخستین خریدار، گزارش خریدار و مبلغ فروش فقط `customer` با `subtotal_amount > 0` را می‌شمارند؛ Order داخلی `ADM-...` یا صفرمبلغ، حتی اگر completed باشد، درآمد یا purchase event نیست. migration الگوی legacy `ADM-%` و idempotency `admin-inventory:%` را به `admin_assignment` backfill می‌کند و ساخت تازه تخصیص مدیر marker `reward_processed_at` را از ابتدا پر می‌کند.

## مدل inventory و رزرو

نخستین transition به paid از `_allocate_paid_timestamp` استفاده می‌کند: زمان پرداخت‌های تازه در یک ثانیه با میکروثانیه افزایشی ترتیب می‌گیرد و replay همان timestamp را حفظ می‌کند. created_at، expires_at و شواهد زمانی provider دقت قبلی خود را نگه می‌دارند. در tie قدیمی، reservation موجود سپس Order ID ترتیب قطعی می‌سازند؛ chronology پرداختی که قبلاً ثبت نشده قابل تضمین نیست.

- payload باید non-empty باشد و hash تکراری در همان محصول پذیرفته نمی‌شود.
- متن نهایی `ready_delivery` شامل snapshot محصول، payload و دستور تحویل باید حداکثر `TELEGRAM_SAFE_MESSAGE_LENGTH=3900` نویسه باشد. این شرط هنگام add/edit موجودی، تغییر نام/icon/instruction محصول و دوباره پیش از assignment بررسی می‌شود؛ داده legacy بلند پیش از هر mutation رد می‌شود و secret به چند پیام شکسته یا truncate نمی‌شود.
- status آیتم یکی از `available/assigned/disabled` است.
- تخصیص، `assigned_order_id` یکتا و user مربوط را در یک transaction ثبت می‌کند.
- آیتم assigned قابل ویرایش، حذف یا تخصیص دوباره نیست.
- پیش از `/inventory_assign` مستقیم، همان transaction وجود هر Order ready همان product با status `paid|processing|awaiting_stock` و بدون item assigned را بررسی می‌کند؛ اگر backlog وجود داشته باشد عملیات conflict است و آیتم باید از مسیر FIFO به قدیمی‌ترین Order واجد شرایط برسد.
- `fulfill_next_processing_ready_order` نباید processing-ready همان product را تا وقتی reservation معتبر قدیمی‌تری باقی است انتخاب کند؛ این guard داخل query/transaction است و مستقل از cap پردازش reservationهای همان چرخه عمل می‌کند.
- رزرو سفارش پرداخت‌شده با `order_id` یکتا است؛ چند سفارش جداگانه یک user می‌توانند رزروهای جدا داشته باشند.
- همه مسیرهای تخصیص ready، از خرید تازه تا رزرو و processing، بر زمان پرداخت Order، با paid_at میکروثانیه‌ای افزایشی برای داده تازه و tie-break قطعی برای داده قدیمی ترتیب می‌گیرند. guard تراکنشی `_assign_inventory` وجود سفارش پرداخت‌شده قدیمی‌ترِ فاقد item را حتی پیش از ساخته‌شدن reservation آن بررسی می‌کند؛ زمان ایجاد صف نمی‌تواند اولویت پرداخت را عوض کند.
- در `/inventory_assign` مجازِ بدون backlog، تخصیص آیتم، سفارش completed نوع `ADM-...` با `order_origin=admin_assignment` و marker پاداش ازپیش‌پردازش‌شده، و `outbound_messages` تحویل با کلید `order:{id}:delivery` در همان transaction ساخته می‌شوند؛ وجود صف قدیمی یا نبود امکان queue پیام باید کل عملیات را rollback کند. این Order داخلی در گزارش فروش/خریدار و eligibility پاداش وارد نمی‌شود.

## تخفیف

eligibility شامل active flag، بازه `starts_at/ends_at`، محصول، کاربر، حداقل مبلغ، سقف کلی و محدودیت هر کاربر است. درصد حداکثر ۱۰۰ و مبلغ تخفیف هرگز بیشتر از subtotal نیست. کد ۱۰۰٪ فقط مبلغ خلاصه را صفر می‌کند و وضعیت pending_payment باقی می‌ماند؛ confirm_zero_payable_order پس از دکمه پرداخت، مالکیت و مهلت را بررسی و سفارش را paid می‌کند. Payment صفرمبلغ ساخته نمی‌شود. همین تأیید برای محصول رایگان در مسیر کاربر لازم است.

## پاداش دعوت

eligibility خرید فقط برای Order با `order_origin=customer` و `subtotal_amount > 0` و با زمان رخداد `paid_at` یا در نبود آن `created_at` ارزیابی می‌شود، نه زمان اجرای maintenance. تخصیص مدیر و سفارش داخلی صفرمبلغ purchase event نیست و نخستین خرید واقعی را مصرف نمی‌کند. قانونی که بعد از خرید ساخته شده یا خارج window خرید بوده است عطف‌به‌ماسبق نمی‌شود. انواع rule:

- `start`؛
- `first_purchase`؛
- `product_purchase`؛
- `combined` با شرط‌های validated مانند حداقل خرید، محصول و مبلغ.

هر reward event به entry یکتای کیف پول وصل است. `orders.reward_processed_at` فقط بعد از grant همه ruleها و ثبت durable noticeها پر می‌شود؛ failure قبل از outbox آن را NULL نگه می‌دارد تا recovery ممکن باشد. علاوه بر marker سفارش، query مستقل `list_reward_events_missing_notice` تمام eventهای فاقد `reward:{id}:notice`، از جمله پاداش `start`، را با cursor/wrap می‌یابد. این marker فقط متعلق به reward است؛ `list_paid_orders_pending_fulfillment` سفارش status=`paid` را مستقل انتخاب می‌کند تا completion پاداش، تحویل محصول را پنهان نکند.

## outbox و retry

کلید `payment:{id}:topup-expired` اعلان انقضای شارژ کیف پول است. `list_expired_wallet_topups_missing_notice` فقط Paymentهای منقضی فاقد notice را برای بازیابی محدود انتخاب می‌کند؛ terminal شدن notice جلوی تکرار را می‌گیرد. این مسیر status پرداخت یا مانده کیف پول را تغییر نمی‌دهد.

stateها `queued/sending/sent/failed/cancelled` هستند. claim فقط از queued و موعدرسیده انجام می‌شود. claim مانده بیش از پنج دقیقه دوباره queued می‌شود. retry نمی‌تواند پیام `sent` را زنده کند. کلید تکراری با recipient، body، audience یا markup متفاوت conflict است.

alert فیش کارت با hash کوتاه `file_kind + file_id` و alert اطلاعات سفارش manual با hash JSON کامل `customer_info_json` نسخه‌بندی می‌شود. همان نسخه پس از restart/outbox retry اعلان دوم نمی‌سازد، اما جایگزینی واقعی فایل یا اطلاعات، کلید outbox تازه و قابل بازیابی می‌گیرد. hash فقط هویت نسخه برای idempotency است و جای رمزنگاری یا کنترل دسترسی را نمی‌گیرد.

`reminder_days` فقط integer نامنفی می‌پذیرد. صفر، آغاز روز محلی پایان در `Database.reminder_timezone` است؛ startup این timezone را از Settings می‌دهد. اگر schedule در همان روز پیش از پایان باشد، موعد صفر برابر اکنون است؛ انقضای دقیق نیمه‌شب و اشتراک پایان‌یافته reminder صفر ندارند. موعد مثبتِ گذشته ساخته نمی‌شود. یکتایی `(order_id, days_before)` در replay حفظ می‌شود. worker پیش از queue و دوباره پیش از ارسال outbox، پایان واقعی و مالکیت را می‌سنجد؛ مورد پایان‌یافته همراه outbox بدون پیام cancelled می‌شود. متن روزهای مثبت زمان مطلق پایان را دارد؛ صفر «امروز» و ساعت پایان را نمایش می‌دهد. retry به outbox با کلید `reminder:{id}` تعلق دارد و reminder تا stale reconciliation در processing می‌ماند؛ failure دائمی یک گیرنده پردازش اعضای بعدی batch را متوقف نمی‌کند.

## indexها و query patternها

indexهای مهم برای hot path:

- کاتالوگ بر `category_id/is_visible/is_available`؛
- سفارش بر user+created، status+expiry و product+status؛
- inventory و reservation بر product+status+id؛
- payment بر order+status، مبلغ فعال یکتای فقط card و تاریخچه method/currency/amount برای quarantine کارت؛
- provider/card review بر disposition/status+ID و cancellation/security event بر user/time یا created+ID؛
- ticket بر status/user و updated؛
- outbox/reminder بر status+due time+id؛
- reward rule بر event+active+product؛
- index جزئی `idx_orders_reward_pending` که در migration ساخته می‌شود.

query جدید باید count هم‌فیلتر، pagination و ترتیب deterministic داشته باشد و سقف Telegram/حافظه را رعایت کند. دریافت newest-N ثابت برای سطحی که باید قابل پیمایش کامل باشد یا clampکردن خروجی پس از query مجاز نیست.

## migration

`schema.sql` با `CREATE ... IF NOT EXISTS` دیتابیس تازه را می‌سازد. `_migrate_schema` تغییرهای additive، backfill و indexهای نسخه‌های قبل را انجام می‌دهد و در پایان `schema_version=11` ثبت می‌کند. migration نسخه ۷ علاوه بر index کارت، جدول resolution نسخه ۶ را برای افزودن `credit_confirmed` و قیود actor/resolving-event بازسازی می‌کند و باید تصمیم‌های تاریخی را بدون تغییر حفظ کند. نسخه ۸ ستون constrained `orders.order_origin` را می‌افزاید و رکوردهای legacy `ADM-%`/`admin-inventory:%` را به `admin_assignment` backfill می‌کند. تغییرهای بعدی تا نسخه ۱۱، attachment kind تیکت، `source_admin_update_id` برای createهای مدیریتی، `admins.identity_verified_at` و marker یکتای root، و journal `processed_admin_updates` با fingerprint/status/effect/timestamps را به‌شکل idempotent اضافه/backfill می‌کنند؛ ردیف legacy journal به `completed` با fingerprint `legacy:{update_id}` تبدیل می‌شود.

قواعد:

1. migration باید با اجرای دوباره بی‌خطر باشد.
2. قبل و بعد از migration، `PRAGMA integrity_check` و `PRAGMA foreign_key_check` اجرا شود.
3. migration روی fixture نسخه قبلی، کپی شکل واقعی DB قدیمی و دیتابیس تازه تست شود. index یا trigger وابسته به ستون تازه فقط بعد از `ALTER TABLE`/rebuild همان ستون ساخته شود؛ `test_live_v5_admin_shape_migrates_before_new_column_indexes` این ترتیب را برای `is_bootstrap_owner` قفل می‌کند.
4. rename/drop نیازمند table rebuild تراکنشی و backup است.
5. داده تاریخی با default مبهم جعل نشود؛ backfill باید معنای دامنه‌ای روشن داشته باشد.
6. schema و migration در یک commit باشند.

## backup، retention و حریم خصوصی

online backup با API خود SQLite تهیه می‌شود و hash SHA-256 ثبت می‌شود. فایل backup شامل chat ID، پروفایل، سفارش، پیام تیکت و payload تحویل است و secret محسوب می‌شود؛ باید دسترسی 0600/دایرکتوری 0700، رمزنگاری در rest و retention محدود داشته باشد. backup، فایل `.env` و دیتابیس زنده هرگز وارد Git نمی‌شوند.
