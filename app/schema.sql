PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;
PRAGMA busy_timeout = 5000;

CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

INSERT INTO schema_meta(key, value) VALUES ('schema_version', '1')
ON CONFLICT(key) DO NOTHING;

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_user_id INTEGER NOT NULL UNIQUE,
    chat_id INTEGER NOT NULL UNIQUE,
    username TEXT,
    username_key TEXT,
    first_name TEXT,
    last_name TEXT,
    customer_name TEXT,
    phone TEXT,
    email TEXT,
    joined_at TEXT NOT NULL,
    is_blocked INTEGER NOT NULL DEFAULT 0 CHECK (is_blocked IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_users_username_key ON users(username_key);
CREATE INDEX IF NOT EXISTS idx_users_joined_at ON users(joined_at);

CREATE TABLE IF NOT EXISTS admins (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL,
    username_key TEXT NOT NULL UNIQUE,
    chat_id INTEGER UNIQUE,
    role TEXT NOT NULL CHECK (role IN ('owner', 'admin', 'support')),
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    identity_verified_at TEXT,
    is_bootstrap_owner INTEGER NOT NULL DEFAULT 0 CHECK (is_bootstrap_owner IN (0, 1)),
    created_by_admin_id INTEGER REFERENCES admins(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_admins_active_role ON admins(is_active, role);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_states (
    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    state TEXT NOT NULL,
    data_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS force_join_channels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_chat_id TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    invite_url TEXT,
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_force_join_channels_order
ON force_join_channels(is_active, sort_order, id);

CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_admin_update_id INTEGER UNIQUE,
    parent_id INTEGER REFERENCES categories(id) ON DELETE RESTRICT,
    name TEXT NOT NULL,
    icon TEXT,
    description TEXT,
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK (parent_id IS NULL OR parent_id <> id)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_root_category_name
ON categories(name COLLATE NOCASE) WHERE parent_id IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_child_category_name
ON categories(parent_id, name COLLATE NOCASE) WHERE parent_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_categories_parent_order
ON categories(parent_id, is_active, sort_order, id);

CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category_id INTEGER NOT NULL REFERENCES categories(id) ON DELETE RESTRICT,
    sku TEXT UNIQUE,
    idempotency_key TEXT UNIQUE,
    name TEXT NOT NULL,
    icon TEXT,
    short_description TEXT,
    long_description TEXT,
    product_type TEXT NOT NULL CHECK (product_type IN ('ready', 'manual')),
    price_amount INTEGER NOT NULL CHECK (price_amount >= 0),
    currency TEXT NOT NULL DEFAULT 'TOMAN',
    duration_days INTEGER CHECK (duration_days IS NULL OR duration_days > 0),
    duration_label TEXT,
    account_type TEXT,
    activation TEXT,
    is_renewable INTEGER NOT NULL DEFAULT 0 CHECK (is_renewable IN (0, 1)),
    warranty_text TEXT,
    features_json TEXT NOT NULL DEFAULT '[]',
    activation_instructions TEXT,
    usage_terms TEXT,
    rules_text TEXT,
    rules_url TEXT,
    reserve_enabled INTEGER NOT NULL DEFAULT 0 CHECK (reserve_enabled IN (0, 1)),
    info_request_text TEXT,
    completion_text TEXT,
    delivery_instructions TEXT,
    reminder_days_json TEXT NOT NULL DEFAULT '[7,3,1]',
    stock_limit INTEGER CHECK (stock_limit IS NULL OR stock_limit >= 0),
    is_visible INTEGER NOT NULL DEFAULT 1 CHECK (is_visible IN (0, 1)),
    is_available INTEGER NOT NULL DEFAULT 1 CHECK (is_available IN (0, 1)),
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_products_catalog
ON products(category_id, is_visible, is_available, id);

CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_number TEXT NOT NULL UNIQUE,
    idempotency_key TEXT NOT NULL UNIQUE,
    order_origin TEXT NOT NULL DEFAULT 'customer'
        CHECK (order_origin IN ('customer', 'admin_assignment')),
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE RESTRICT,
    product_name_snapshot TEXT NOT NULL,
    product_icon_snapshot TEXT,
    product_type_snapshot TEXT NOT NULL CHECK (product_type_snapshot IN ('ready', 'manual')),
    duration_days_snapshot INTEGER CHECK (duration_days_snapshot IS NULL OR duration_days_snapshot > 0),
    duration_label_snapshot TEXT,
    quantity INTEGER NOT NULL DEFAULT 1 CHECK (quantity > 0),
    unit_price_amount INTEGER NOT NULL CHECK (unit_price_amount >= 0),
    subtotal_amount INTEGER NOT NULL CHECK (subtotal_amount >= 0),
    discount_amount INTEGER NOT NULL DEFAULT 0 CHECK (discount_amount >= 0),
    wallet_held_amount INTEGER NOT NULL DEFAULT 0 CHECK (wallet_held_amount >= 0),
    wallet_captured_amount INTEGER NOT NULL DEFAULT 0 CHECK (wallet_captured_amount >= 0),
    wallet_refunded_amount INTEGER NOT NULL DEFAULT 0 CHECK (wallet_refunded_amount >= 0),
    external_paid_amount INTEGER NOT NULL DEFAULT 0 CHECK (external_paid_amount >= 0),
    payable_amount INTEGER NOT NULL CHECK (payable_amount >= 0),
    currency TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending_payment'
        CHECK (status IN (
            'pending_payment', 'awaiting_confirmation', 'awaiting_stock', 'awaiting_info',
            'paid', 'processing', 'completed', 'rejected', 'expired', 'cancelled', 'refunded'
        )),
    customer_info_json TEXT,
    admin_note TEXT,
    receipt_file_id TEXT,
    delivered_payload TEXT,
    expires_at TEXT NOT NULL,
    paid_at TEXT,
    reward_processed_at TEXT,
    completed_at TEXT,
    subscription_ends_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK (discount_amount <= subtotal_amount),
    CHECK (wallet_held_amount <= subtotal_amount - discount_amount),
    CHECK (wallet_captured_amount <= wallet_held_amount),
    CHECK (wallet_refunded_amount <= wallet_held_amount)
);

CREATE INDEX IF NOT EXISTS idx_orders_user_created ON orders(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_orders_status_expiry ON orders(status, expires_at);
CREATE INDEX IF NOT EXISTS idx_orders_product_status ON orders(product_id, status);

CREATE TABLE IF NOT EXISTS inventory_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_admin_update_id INTEGER UNIQUE,
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE RESTRICT,
    payload TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'available' CHECK (status IN ('available', 'assigned', 'disabled')),
    assigned_order_id INTEGER UNIQUE REFERENCES orders(id) ON DELETE RESTRICT,
    assigned_user_id INTEGER REFERENCES users(id) ON DELETE RESTRICT,
    assigned_at TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(product_id, payload_hash)
);

CREATE INDEX IF NOT EXISTS idx_inventory_available
ON inventory_items(product_id, status, id);

CREATE TABLE IF NOT EXISTS reservations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    order_id INTEGER UNIQUE REFERENCES orders(id) ON DELETE SET NULL,
    status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'fulfilled', 'cancelled')),
    fulfilled_inventory_item_id INTEGER REFERENCES inventory_items(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL,
    fulfilled_at TEXT
);

-- Earlier versions limited a user to one queued reservation per product,
-- which lost the second of two separately paid orders.  Queue identity is the
-- order, while an order-less "notify me" reservation remains unique per user.
DROP INDEX IF EXISTS uq_active_reservation;

CREATE UNIQUE INDEX IF NOT EXISTS uq_active_order_reservation
ON reservations(order_id)
WHERE status = 'queued' AND order_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_active_user_reservation_without_order
ON reservations(product_id, user_id)
WHERE status = 'queued' AND order_id IS NULL;

CREATE INDEX IF NOT EXISTS idx_reservations_queue
ON reservations(product_id, status, id);

CREATE TABLE IF NOT EXISTS discounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL,
    code_key TEXT NOT NULL UNIQUE,
    discount_type TEXT NOT NULL CHECK (discount_type IN ('fixed', 'percent')),
    value INTEGER NOT NULL CHECK (value > 0),
    product_id INTEGER REFERENCES products(id) ON DELETE CASCADE,
    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    minimum_order_amount INTEGER NOT NULL DEFAULT 0 CHECK (minimum_order_amount >= 0),
    max_uses INTEGER CHECK (max_uses IS NULL OR max_uses > 0),
    per_user_limit INTEGER CHECK (per_user_limit IS NULL OR per_user_limit > 0),
    used_count INTEGER NOT NULL DEFAULT 0 CHECK (used_count >= 0),
    starts_at TEXT,
    ends_at TEXT,
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK (discount_type <> 'percent' OR value <= 100)
);

CREATE INDEX IF NOT EXISTS idx_discounts_scope
ON discounts(is_active, product_id, user_id);

CREATE TABLE IF NOT EXISTS order_discounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    discount_id INTEGER NOT NULL REFERENCES discounts(id) ON DELETE RESTRICT,
    amount INTEGER NOT NULL CHECK (amount >= 0),
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at TEXT NOT NULL,
    released_at TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_active_order_discount
ON order_discounts(order_id) WHERE is_active = 1;

CREATE INDEX IF NOT EXISTS idx_order_discounts_discount
ON order_discounts(discount_id, is_active);

CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    payment_number TEXT NOT NULL UNIQUE,
    idempotency_key TEXT NOT NULL UNIQUE,
    order_id INTEGER REFERENCES orders(id) ON DELETE RESTRICT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    purpose TEXT NOT NULL CHECK (purpose IN ('order', 'wallet_topup')),
    method TEXT NOT NULL,
    base_amount INTEGER NOT NULL CHECK (base_amount > 0),
    payable_amount INTEGER NOT NULL CHECK (payable_amount > 0),
    currency TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'verifying', 'paid', 'failed', 'cancelled', 'expired', 'refunded')),
    external_reference TEXT UNIQUE,
    provider_invoice_id TEXT UNIQUE,
    provider_invoice_url TEXT,
    receipt_file_id TEXT,
    raw_payload_json TEXT,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    confirmed_at TEXT,
    CHECK (
        (purpose = 'order' AND order_id IS NOT NULL)
        OR (purpose = 'wallet_topup' AND order_id IS NULL)
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_active_payment_payable_amount
ON payments(method, currency, payable_amount)
WHERE method = 'card' AND status IN ('pending', 'verifying');

-- Supports the bounded card-amount quarantine lookup across terminal history.
CREATE INDEX IF NOT EXISTS idx_payments_amount_history
ON payments(method, currency, payable_amount);

CREATE INDEX IF NOT EXISTS idx_payments_order_status ON payments(order_id, status);

-- The Bot API file id alone does not reveal whether it must be resent with
-- sendPhoto or sendDocument.  Keep that recoverability metadata separately so
-- older payment rows remain migration-compatible.
CREATE TABLE IF NOT EXISTS payment_receipt_attachments (
    payment_id INTEGER PRIMARY KEY REFERENCES payments(id) ON DELETE RESTRICT,
    file_id TEXT NOT NULL,
    file_kind TEXT NOT NULL CHECK (file_kind IN ('photo', 'document')),
    submitted_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Bank-event ledger is append-only through the domain/repository API. Recording
-- rejected/late references prevents a replay from being matched to a later
-- customer's equal-amount payment; the database also enforces unique references.
CREATE TABLE IF NOT EXISTS card_payment_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    reference TEXT NOT NULL UNIQUE,
    amount INTEGER NOT NULL CHECK (amount > 0),
    occurred_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('confirmed', 'review')),
    payment_id INTEGER REFERENCES payments(id) ON DELETE RESTRICT,
    raw_payload_json TEXT NOT NULL,
    received_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS card_payment_event_resolutions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL UNIQUE
        REFERENCES card_payment_events(id) ON DELETE RESTRICT,
    action TEXT NOT NULL CHECK (action IN ('refund_confirmed', 'dismiss')),
    actor_admin_id INTEGER NOT NULL REFERENCES admins(id) ON DELETE RESTRICT,
    note TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS provider_payment_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,
    payment_id INTEGER NOT NULL REFERENCES payments(id) ON DELETE RESTRICT,
    provider_reference TEXT NOT NULL,
    provider_status TEXT NOT NULL,
    received_amount TEXT,
    amount_evidence TEXT NOT NULL
        CHECK (amount_evidence IN ('zero', 'nonzero', 'unknown')),
    disposition TEXT NOT NULL
        CHECK (disposition IN ('completed', 'failed', 'review')),
    raw_payload_json TEXT NOT NULL,
    raw_payload_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(provider, payment_id, raw_payload_sha256)
);

CREATE INDEX IF NOT EXISTS idx_provider_payment_events_review
ON provider_payment_events(disposition, id);

CREATE TABLE IF NOT EXISTS provider_payment_event_resolutions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL UNIQUE
        REFERENCES provider_payment_events(id) ON DELETE RESTRICT,
    action TEXT NOT NULL
        CHECK (action IN (
            'refund_confirmed', 'dismiss', 'credit_confirmed', 'provider_completed',
            'provider_terminal_zero'
        )),
    actor_admin_id INTEGER REFERENCES admins(id) ON DELETE RESTRICT,
    resolving_event_id INTEGER
        REFERENCES provider_payment_events(id) ON DELETE RESTRICT,
    note TEXT NOT NULL,
    created_at TEXT NOT NULL,
    CHECK (
        (action IN ('refund_confirmed', 'dismiss', 'credit_confirmed')
            AND actor_admin_id IS NOT NULL AND resolving_event_id IS NULL)
        OR
        (action IN ('provider_completed', 'provider_terminal_zero')
            AND actor_admin_id IS NULL AND resolving_event_id IS NOT NULL)
    )
);

CREATE TRIGGER IF NOT EXISTS provider_payment_events_no_update
BEFORE UPDATE ON provider_payment_events
BEGIN
    SELECT RAISE(ABORT, 'provider payment events are immutable');
END;

CREATE TRIGGER IF NOT EXISTS provider_payment_events_no_delete
BEFORE DELETE ON provider_payment_events
BEGIN
    SELECT RAISE(ABORT, 'provider payment events are immutable');
END;

CREATE TRIGGER IF NOT EXISTS provider_payment_event_resolutions_no_update
BEFORE UPDATE ON provider_payment_event_resolutions
BEGIN
    SELECT RAISE(ABORT, 'provider payment event resolutions are immutable');
END;

CREATE TRIGGER IF NOT EXISTS provider_payment_event_resolutions_no_delete
BEFORE DELETE ON provider_payment_event_resolutions
BEGIN
    SELECT RAISE(ABORT, 'provider payment event resolutions are immutable');
END;

CREATE INDEX IF NOT EXISTS idx_card_payment_events_received
ON card_payment_events(received_at DESC);

CREATE TABLE IF NOT EXISTS card_payment_cancellations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    payment_id INTEGER NOT NULL UNIQUE REFERENCES payments(id) ON DELETE RESTRICT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_card_payment_cancellations_user_time
ON card_payment_cancellations(user_id, created_at);

CREATE TABLE IF NOT EXISTS payment_security_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_key TEXT NOT NULL UNIQUE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    event_type TEXT NOT NULL CHECK (
        event_type IN ('card_daily_limit', 'card_cancel_cooldown')
    ),
    details_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_payment_security_events_created
ON payment_security_events(created_at, id);

CREATE TABLE IF NOT EXISTS wallet_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    order_id INTEGER REFERENCES orders(id) ON DELETE RESTRICT,
    payment_id INTEGER REFERENCES payments(id) ON DELETE RESTRICT,
    actor_admin_id INTEGER REFERENCES admins(id) ON DELETE SET NULL,
    amount_signed INTEGER NOT NULL CHECK (amount_signed <> 0),
    entry_type TEXT NOT NULL CHECK (entry_type IN (
        'topup', 'admin_adjustment', 'wallet_hold', 'wallet_refund',
        'order_refund', 'referral_reward', 'manual_credit', 'manual_debit'
    )),
    reason TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_wallet_entries_user_id
ON wallet_entries(user_id, id);

CREATE INDEX IF NOT EXISTS idx_wallet_entries_order_id
ON wallet_entries(order_id, id);

CREATE TRIGGER IF NOT EXISTS wallet_entries_no_update
BEFORE UPDATE ON wallet_entries
BEGIN
    SELECT RAISE(ABORT, 'wallet ledger entries are immutable');
END;

CREATE TRIGGER IF NOT EXISTS wallet_entries_no_delete
BEFORE DELETE ON wallet_entries
BEGIN
    SELECT RAISE(ABORT, 'wallet ledger entries are immutable');
END;

CREATE TABLE IF NOT EXISTS faq_categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL COLLATE NOCASE UNIQUE,
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS faqs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category_id INTEGER REFERENCES faq_categories(id) ON DELETE SET NULL,
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_faqs_order ON faqs(category_id, is_active, sort_order, id);

CREATE TABLE IF NOT EXISTS tickets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_number TEXT NOT NULL UNIQUE,
    idempotency_key TEXT NOT NULL UNIQUE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    subject TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'answered', 'closed')),
    assigned_admin_id INTEGER REFERENCES admins(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    closed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_tickets_status_updated ON tickets(status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_tickets_user_updated ON tickets(user_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS ticket_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id INTEGER NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
    sender_type TEXT NOT NULL CHECK (sender_type IN ('user', 'admin')),
    sender_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    sender_admin_id INTEGER REFERENCES admins(id) ON DELETE SET NULL,
    body TEXT NOT NULL,
    attachment_file_id TEXT,
    attachment_kind TEXT CHECK (attachment_kind IN ('photo', 'document')),
    idempotency_key TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    CHECK (
        (sender_type = 'user' AND sender_user_id IS NOT NULL AND sender_admin_id IS NULL)
        OR
        (sender_type = 'admin' AND sender_admin_id IS NOT NULL AND sender_user_id IS NULL)
    )
);

CREATE INDEX IF NOT EXISTS idx_ticket_messages_ticket ON ticket_messages(ticket_id, id);

CREATE TABLE IF NOT EXISTS outbound_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    idempotency_key TEXT NOT NULL UNIQUE,
    recipient_user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    audience_json TEXT,
    body TEXT NOT NULL,
    reply_markup_json TEXT,
    status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'sending', 'sent', 'failed', 'cancelled')),
    scheduled_at TEXT NOT NULL,
    telegram_message_id INTEGER,
    error_text TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    sent_at TEXT,
    CHECK (recipient_user_id IS NOT NULL OR audience_json IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS idx_outbound_messages_queue
ON outbound_messages(status, scheduled_at, id);

-- Telegram can replay an update if the process stops before persisting the
-- getUpdates offset.  Started effects are replayable; completed effects are
-- skipped.  effect_json freezes the intended target of non-idempotent toggles.
CREATE TABLE IF NOT EXISTS processed_admin_updates (
    update_id INTEGER PRIMARY KEY,
    fingerprint TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'started'
        CHECK (status IN ('started', 'completed')),
    effect_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS outbound_message_attempts (
    message_id INTEGER PRIMARY KEY REFERENCES outbound_messages(id) ON DELETE CASCADE,
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    last_attempt_at TEXT,
    last_error TEXT
);

CREATE TABLE IF NOT EXISTS broadcast_batches (
    id TEXT PRIMARY KEY,
    actor_admin_id INTEGER NOT NULL REFERENCES admins(id) ON DELETE RESTRICT,
    actor_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    target_count INTEGER NOT NULL CHECK (target_count >= 0),
    created_at TEXT NOT NULL,
    completed_at TEXT,
    notified_at TEXT
);

CREATE TABLE IF NOT EXISTS broadcast_batch_messages (
    batch_id TEXT NOT NULL REFERENCES broadcast_batches(id) ON DELETE CASCADE,
    message_id INTEGER NOT NULL UNIQUE REFERENCES outbound_messages(id) ON DELETE CASCADE,
    PRIMARY KEY(batch_id, message_id)
);

CREATE INDEX IF NOT EXISTS idx_broadcast_batches_pending
ON broadcast_batches(notified_at, created_at);

CREATE TABLE IF NOT EXISTS referrals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    inviter_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    invitee_user_id INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE RESTRICT,
    status TEXT NOT NULL DEFAULT 'registered' CHECK (status IN ('registered', 'qualified', 'blocked')),
    created_at TEXT NOT NULL,
    qualified_at TEXT,
    CHECK (inviter_user_id <> invitee_user_id)
);

CREATE INDEX IF NOT EXISTS idx_referrals_inviter ON referrals(inviter_user_id, created_at);

CREATE TABLE IF NOT EXISTS reward_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_key TEXT NOT NULL UNIQUE,
    event_type TEXT NOT NULL CHECK (event_type IN ('start', 'first_purchase', 'product_purchase', 'combined')),
    product_id INTEGER REFERENCES products(id) ON DELETE CASCADE,
    amount INTEGER NOT NULL CHECK (amount > 0),
    conditions_json TEXT NOT NULL DEFAULT '{}',
    starts_at TEXT,
    ends_at TEXT,
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_reward_rules_active
ON reward_rules(event_type, is_active, product_id);

CREATE TABLE IF NOT EXISTS reward_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    reward_rule_id INTEGER NOT NULL REFERENCES reward_rules(id) ON DELETE RESTRICT,
    referral_id INTEGER NOT NULL REFERENCES referrals(id) ON DELETE RESTRICT,
    event_key TEXT NOT NULL,
    source_order_id INTEGER REFERENCES orders(id) ON DELETE RESTRICT,
    amount INTEGER NOT NULL CHECK (amount > 0),
    wallet_entry_id INTEGER NOT NULL UNIQUE REFERENCES wallet_entries(id) ON DELETE RESTRICT,
    created_at TEXT NOT NULL,
    UNIQUE(reward_rule_id, referral_id, event_key)
);

CREATE INDEX IF NOT EXISTS idx_reward_events_referral ON reward_events(referral_id, id);

CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    remind_at TEXT NOT NULL,
    days_before INTEGER NOT NULL CHECK (days_before >= 0),
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'processing', 'sent', 'failed', 'cancelled')),
    telegram_message_id INTEGER,
    error_text TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    sent_at TEXT,
    UNIQUE(order_id, days_before)
);

CREATE INDEX IF NOT EXISTS idx_reminders_due ON reminders(status, remind_at, id);

CREATE TABLE IF NOT EXISTS backups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('running', 'completed', 'failed')),
    sha256 TEXT,
    size_bytes INTEGER CHECK (size_bytes IS NULL OR size_bytes >= 0),
    error_text TEXT,
    created_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_backups_created ON backups(created_at DESC, id DESC);
