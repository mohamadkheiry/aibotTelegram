from __future__ import annotations

import json
import hashlib
import logging
import re
import sqlite3
import threading
import uuid
from collections.abc import Mapping
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

from . import texts
from .config import Settings
from .db import (
    ConflictError,
    Database,
    DatabaseError,
    InsufficientFundsError,
    NotFoundError,
    OutOfStockError,
    ValidationError,
)
from .jobs import PeriodicWorker
from .keyboards import (
    ACCOUNT,
    CHANNEL,
    REFERRAL,
    SHOP,
    SUPPORT,
    WALLET,
    back_button,
    callback_button,
    contact_keyboard,
    contains_emoji,
    copy_text_button,
    inline_keyboard,
    inline_main_menu_keyboard,
    main_menu_keyboard,
    remove_keyboard,
    url_button,
)
from .payment_server import ConfirmationOutcome, PaymentCallbackServer
from .plisio import PlisioClient, PlisioError
from .telegram import (
    TelegramAPIError,
    TelegramClient,
    TelegramError,
    TelegramRequestCancelled,
)
from .utils import (
    clamp_text,
    escape,
    extract_start_ref,
    is_safe_https_url,
    is_safe_telegram_channel_url,
    is_safe_telegram_invite_url,
    money,
    normalize_digits,
    normalize_username,
    parse_amount,
    parse_iso,
    render_rich_text,
    split_telegram_html,
    utc_now,
)


LOG = logging.getLogger(__name__)


class BotApplication:
    """Update router and application service for the shop bot."""

    MAINTENANCE_REWARD_RECONCILE_LIMIT = 100
    MAINTENANCE_FULFILLMENT_RECONCILE_LIMIT = 100
    MAINTENANCE_RESERVED_FULFILLMENT_LIMIT = 100
    MAINTENANCE_PROCESSING_READY_FULFILLMENT_LIMIT = 100
    MAINTENANCE_ORDER_NOTICE_RECONCILE_LIMIT = 100
    MAINTENANCE_TICKET_ALERT_RECONCILE_LIMIT = 100
    MAINTENANCE_ZERO_EXTERNAL_NOTICE_LIMIT = 100
    MAINTENANCE_EXPIRED_TOPUP_NOTICE_LIMIT = 100
    _REWARD_RECONCILE_CURSOR_SETTING = "maintenance_reward_reconcile_cursor"
    _FULFILLMENT_RECONCILE_CURSOR_SETTING = "maintenance_fulfillment_reconcile_cursor"
    _CRYPTO_POLL_CURSOR_SETTING = "maintenance_crypto_poll_cursor"
    _PROVIDER_REVIEW_CURSOR_SETTING = "maintenance_provider_review_cursor"
    _PROVIDER_RESOLUTION_CURSOR_SETTING = "maintenance_provider_resolution_cursor"
    _CARD_REVIEW_CURSOR_SETTING = "maintenance_card_review_cursor"
    _CARD_RESOLUTION_CURSOR_SETTING = "maintenance_card_resolution_cursor"
    _PAYMENT_SECURITY_CURSOR_SETTING = "maintenance_payment_security_cursor"
    _PAID_NOTICE_CURSOR_SETTING = "maintenance_paid_notice_cursor"
    _CARD_RECEIPT_CURSOR_SETTING = "maintenance_card_receipt_cursor"
    _MANUAL_INFO_CURSOR_SETTING = "maintenance_manual_info_cursor"
    _RESERVED_NOTICE_CURSOR_SETTING = "maintenance_reserved_notice_cursor"
    _INFO_NOTICE_CURSOR_SETTING = "maintenance_info_notice_cursor"
    _DELIVERY_NOTICE_CURSOR_SETTING = "maintenance_delivery_notice_cursor"
    _EXPIRED_NOTICE_CURSOR_SETTING = "maintenance_expired_notice_cursor"
    _EXPIRED_TOPUP_NOTICE_CURSOR_SETTING = "maintenance_expired_topup_notice_cursor"
    _TICKET_ALERT_CURSOR_SETTING = "maintenance_ticket_alert_cursor"
    _READY_STOCK_ALERT_CURSOR_SETTING = "maintenance_ready_stock_alert_cursor"
    _REWARD_NOTICE_CURSOR_SETTING = "maintenance_reward_notice_cursor"
    _ZERO_EXTERNAL_NOTICE_CURSOR_SETTING = "maintenance_zero_external_notice_cursor"

    DEFAULT_SETTINGS: dict[str, Any] = {
        "bot_enabled": True,
        "payment_wallet_enabled": True,
        "payment_card_enabled": True,
        "payment_crypto_enabled": False,
        "card_number": "",
        "card_owner": "",
        "main_channel_url": "",
        "completion_notice_pending": True,
    }

    def __init__(
        self,
        settings: Settings,
        database: Database,
        telegram: TelegramClient,
    ) -> None:
        self.settings = settings
        self.db = database
        self.telegram = telegram
        self.stop_event = threading.Event()
        if hasattr(self.telegram, "set_stop_event"):
            self.telegram.set_stop_event(self.stop_event)
        self._card_confirmation_lock = threading.Lock()
        self._durable_notification_lock = threading.Lock()
        self.bot_username = ""
        self._maintenance_worker = PeriodicWorker(
            self.run_maintenance,
            interval_seconds=settings.job_interval_seconds,
            name="alone-account-maintenance",
            logger=LOG,
        )
        self._payment_server: PaymentCallbackServer | None = None
        self._plisio: PlisioClient | None = None
        if settings.plisio_api_key:
            self._plisio = PlisioClient(
                settings.plisio_api_key,
                currency=settings.plisio_currency,
                source_currency=settings.plisio_source_currency,
                source_amount_multiplier=settings.plisio_amount_multiplier,
            )
        self.admin_controller: Any | None = None

    # -- lifecycle ---------------------------------------------------------

    def initialize(self) -> None:
        self.db.initialize()
        self.db.bootstrap_admin(
            self.settings.bootstrap_admin_username,
            self.settings.bootstrap_admin_chat_id,
            role="owner",
            bootstrap_root=True,
        )
        for key, value in self.DEFAULT_SETTINGS.items():
            if self.db.get_setting(key, None) is None:
                self.db.set_setting(key, value)

        me = self.telegram.call("getMe")
        if isinstance(me, dict):
            self.bot_username = str(me.get("username") or "")
            self.db.set_setting("bot_username", self.bot_username)
        # Telegram webhooks and getUpdates are mutually exclusive.
        self.telegram.call("deleteWebhook", {"drop_pending_updates": False})
        self.telegram.call(
            "setMyCommands",
            {
                "commands": [
                    {"command": "start", "description": "شروع ربات"},
                    {"command": "menu", "description": "منوی اصلی"},
                    {"command": "orders", "description": "سفارش‌های من"},
                    {"command": "support", "description": "پشتیبانی"},
                    {"command": "admin", "description": "مدیریت ربات"},
                    {"command": "cancel", "description": "لغو عملیات جاری"},
                ]
            },
        )

        try:
            from .admin import AdminController

            self.admin_controller = AdminController(
                self.db,
                self.telegram,
                self.settings,
                notify_user=self._notify_user,
                # Manual payment approval must run the same exactly-once
                # referral and delivery pipeline as automatic confirmation.
                fulfill_order=lambda order: self._after_order_paid(int(order["id"])),
            )
        except ImportError:
            LOG.warning("Admin controller is not available")

        if self.settings.payment_callback_enabled:
            self._payment_server = PaymentCallbackServer(
                secret=self.settings.payment_callback_secret,
                on_confirm=self.confirm_card_amount,
                host=self.settings.payment_callback_bind,
                port=self.settings.payment_callback_port,
            )
            address = self._payment_server.start()
            LOG.info("MacroDroid callback listener started on %s:%s", *address)

    def run(self) -> None:
        self.initialize()
        self._maintenance_worker.start()
        offset = self.db.get_update_offset(0)
        LOG.info("Starting getUpdates long polling for @%s at offset %s", self.bot_username, offset)
        try:
            self.telegram.run_polling(
                self.process_update_safe,
                offset=offset,
                timeout=self.settings.poll_timeout_seconds,
                allowed_updates=["message", "callback_query"],
                stop_event=self.stop_event,
                save_offset=self.db.save_update_offset,
            )
        finally:
            self.stop()

    def stop(self) -> None:
        self.stop_event.set()
        self._maintenance_worker.stop()
        if self._payment_server:
            self._payment_server.stop()
        if self._plisio:
            self._plisio.close()
        self.telegram.close()

    # -- update dispatch ---------------------------------------------------

    def process_update_safe(self, update: dict[str, Any]) -> bool | None:
        try:
            return self.process_update(update)
        except TelegramRequestCancelled:
            if not self.stop_event.is_set():
                raise
        except TelegramAPIError as exc:
            # A blocked recipient must not poison the durable update queue.
            LOG.warning("Telegram API error while handling update %s: %s", update.get("update_id"), exc)
        except (
            ConflictError,
            InsufficientFundsError,
            NotFoundError,
            OutOfStockError,
            ValidationError,
        ):
            LOG.warning(
                "Terminal domain error while handling update %s",
                update.get("update_id"),
            )
        except (DatabaseError, sqlite3.DatabaseError) as exc:
            LOG.warning(
                "Transient database failure while handling update %s (%s); NACKing",
                update.get("update_id"),
                type(exc).__name__,
            )
            return False
        except Exception:
            LOG.exception("Unhandled update %s", update.get("update_id"))
            event = update.get("message") or (update.get("callback_query") or {}).get("message") or {}
            chat_id = (event.get("chat") or {}).get("id")
            if chat_id:
                try:
                    self.telegram.send_message(
                        chat_id,
                        "خطایی موقت رخ داد. لطفاً دوباره تلاش کن یا /menu را بزن.",
                    )
                except TelegramError:
                    pass
        return None

    def process_update(self, update: dict[str, Any]) -> bool | None:
        if callback := update.get("callback_query"):
            return self._handle_callback(callback, update)
        if message := update.get("message"):
            return self._handle_message(message, update)
        return None

    @staticmethod
    def _admin_update_fingerprint(update: Mapping[str, Any]) -> str:
        """Bind a Telegram update id to its immutable wire payload."""

        def wire_value(value: Any) -> Any:
            if isinstance(value, Mapping):
                return {
                    str(key): wire_value(item)
                    for key, item in value.items()
                    if not str(key).startswith("_admin_")
                }
            if isinstance(value, list):
                return [wire_value(item) for item in value]
            return value

        canonical = json.dumps(
            wire_value(update),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _begin_admin_update(
        self, update: Mapping[str, Any]
    ) -> tuple[int | None, dict[str, Any] | None]:
        update_id = update.get("update_id")
        if update_id is None:
            return None, None
        state = self.db.begin_admin_update(
            int(update_id), self._admin_update_fingerprint(update)
        )
        return int(update_id), state

    def _identity(
        self, event: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        source = event.get("from") or {}
        chat = event.get("chat") or {}
        if not source.get("id") or chat.get("type") != "private":
            raise ValidationError("private user message required")
        user = self.db.upsert_user(
            int(source["id"]),
            int(chat["id"]),
            username=source.get("username"),
            first_name=source.get("first_name"),
            last_name=source.get("last_name"),
        )
        username = normalize_username(source.get("username"))
        if username:
            try:
                self.db.bind_admin_chat(username, int(chat["id"]))
            except (ConflictError, NotFoundError):
                pass
        admin = next(
            (
                item
                for item in self.db.list_admins(active_only=True)
                if item.get("chat_id") == int(chat["id"])
            ),
            None,
        )
        return user, admin

    def _handle_message(
        self, message: dict[str, Any], update: dict[str, Any]
    ) -> bool | None:
        if (message.get("chat") or {}).get("type") != "private":
            return
        user, admin = self._identity(message)
        chat_id = int(user["chat_id"])
        text = str(message.get("text") or "").strip()

        if user.get("is_blocked") and not admin:
            self.telegram.send_message(chat_id, "دسترسی شما به ربات مسدود شده است.")
            return

        if text.startswith("/cancel"):
            self.db.clear_user_state(user["id"])
            self.show_main_menu(user)
            return

        if admin and self._maybe_send_completion_notice(admin):
            pass

        if (
            admin
            and text.startswith("/")
            and self.admin_controller
            and self.admin_controller.handles_command(text)
        ):
            update_id, update_state = self._begin_admin_update(update)
            if update_state is not None and not update_state["should_process"]:
                return
            admin_message = dict(message)
            if update_id is not None:
                admin_message["_admin_update_id"] = update_id
                admin_message["_admin_update_replay"] = bool(
                    update_state and update_state["is_replay"]
                )
            handled = self.admin_controller.handle(admin_message, user, admin)
            retry = bool(admin_message.get("_admin_update_retry", False))
            if update_id is not None and not retry:
                self.db.complete_admin_update(update_id)
            if retry:
                return False
            if handled:
                self._deliver_outbound_messages()
                return

        if text.startswith("/start"):
            referrer_telegram_id = extract_start_ref(text)
            if referrer_telegram_id:
                self._record_referral(user, referrer_telegram_id)
            if not self._access_guard(user, admin):
                return
            self._grant_start_referral_reward(user)
            self.show_main_menu(user)
            return

        if text in {"/menu", "منوی اصلی"}:
            if self._access_guard(user, admin):
                self.show_main_menu(user)
            return

        if not self._access_guard(user, admin):
            return

        state = self.db.get_user_state(user["id"])
        if (
            state
            and admin
            and self.admin_controller
            and str(state.get("state") or "").startswith("admin:")
        ):
            update_id, update_state = self._begin_admin_update(update)
            if update_state is not None and not update_state["should_process"]:
                try:
                    effect = json.loads(str(update_state.get("effect_json") or "{}"))
                except (TypeError, ValueError):
                    effect = {}
                if str(effect.get("key") or "").startswith("admin-state:"):
                    self.db.clear_user_state(int(user["id"]))
                return
            admin_message = dict(message)
            if update_id is not None:
                admin_message["_admin_update_id"] = update_id
                admin_message["_admin_update_replay"] = bool(
                    update_state and update_state["is_replay"]
                )
            handled = self.admin_controller.handle_state(
                admin_message, user, admin, state
            )
            retry = bool(admin_message.get("_admin_update_retry", False))
            if update_id is not None and not retry:
                self.db.complete_admin_update(update_id)
            if admin_message.get("_admin_state_complete") and not retry:
                self.db.clear_user_state(int(user["id"]))
            if retry:
                return False
            if handled:
                self._deliver_outbound_messages()
                return
        if state and self._handle_state(message, user, admin, state, update):
            return

        if text == SHOP:
            self.show_store(chat_id)
        elif text == ACCOUNT:
            self.show_account(user)
        elif text == WALLET:
            self.show_wallet(user)
        elif text == SUPPORT or text == "/support":
            self.show_support(user)
        elif text == REFERRAL:
            self.show_referral(user)
        elif text == CHANNEL:
            self.show_channel(user)
        elif text == "/orders":
            self.show_orders(user)
        elif text == "/admin" and admin:
            self._send_admin_home(admin)
        else:
            self.telegram.send_message(
                chat_id,
                "گزینه موردنظرت رو از منوی پایین انتخاب کن.",
                reply_markup=main_menu_keyboard(self.settings.button_icon_ids),
            )

    def _handle_callback(
        self, query: dict[str, Any], update: dict[str, Any]
    ) -> bool | None:
        message = query.get("message") or {}
        if (message.get("chat") or {}).get("type") != "private":
            self.telegram.answer_callback_query(query["id"], "این گزینه فقط در گفت‌وگوی خصوصی فعال است.")
            return
        event = {**message, "from": query.get("from") or {}}
        user, admin = self._identity(event)
        data = str(query.get("data") or "")

        if user.get("is_blocked") and not admin:
            self.telegram.answer_callback_query(query["id"], "دسترسی شما مسدود شده است.", show_alert=True)
            return
        if admin and data.startswith("adm:") and self.admin_controller:
            update_id, update_state = self._begin_admin_update(update)
            if update_state is not None and not update_state["should_process"]:
                self.telegram.answer_callback_query(
                    query["id"], "این درخواست قبلاً پردازش شده است."
                )
                return
            admin_query = dict(query)
            if update_id is not None:
                admin_query["_admin_update_id"] = update_id
                admin_query["_admin_update_replay"] = bool(
                    update_state and update_state["is_replay"]
                )
            handled = self.admin_controller.handle_callback(
                data, admin_query, user, admin
            )
            retry = bool(admin_query.get("_admin_update_retry", False))
            if update_id is not None and not retry:
                self.db.complete_admin_update(update_id)
            if retry:
                return False
            if handled:
                self._deliver_outbound_messages()
                return
        if data == "join:check":
            if not bool(self.db.get_setting("bot_enabled", True)) and not admin:
                self.telegram.answer_callback_query(
                    query["id"],
                    "ربات موقتاً در حال بروزرسانی است.",
                    show_alert=True,
                )
            elif admin or self._check_memberships(user):
                self.telegram.answer_callback_query(query["id"], "عضویت تأیید شد.")
                self._grant_start_referral_reward(user)
                self.show_main_menu(user)
            else:
                self.telegram.answer_callback_query(
                    query["id"], "هنوز عضو همه کانال‌ها نیستی.", show_alert=True
                )
            return
        if data == "join:page" or data.startswith("join:page:"):
            try:
                page = self._callback_page(data, "join:page")
            except ValidationError as exc:
                self.telegram.answer_callback_query(
                    query["id"], str(exc), show_alert=True
                )
                return
            self._show_join_required(user, page=page)
            self.telegram.answer_callback_query(query["id"])
            return
        if not self._access_guard(user, admin, callback_query_id=query["id"]):
            return

        try:
            handled = self._dispatch_user_callback(data, query, user, update)
        except (ConflictError, NotFoundError, ValidationError, ValueError, OverflowError) as exc:
            self.telegram.answer_callback_query(query["id"], str(exc), show_alert=True)
            return
        if not handled:
            self.telegram.answer_callback_query(query["id"], "این گزینه دیگر معتبر نیست.")

    # -- guards and shared rendering --------------------------------------

    def _access_guard(
        self,
        user: dict[str, Any],
        admin: dict[str, Any] | None,
        *,
        callback_query_id: str | None = None,
    ) -> bool:
        if not bool(self.db.get_setting("bot_enabled", True)) and not admin:
            message = "🔧 ربات موقتاً در حال بروزرسانی است. لطفاً کمی بعد دوباره تلاش کن."
            if callback_query_id:
                self.telegram.answer_callback_query(callback_query_id, message, show_alert=True)
            else:
                self.telegram.send_message(user["chat_id"], message)
            return False
        if admin or self._check_memberships(user):
            return True
        self._show_join_required(user)
        if callback_query_id:
            self.telegram.answer_callback_query(
                callback_query_id, "ابتدا عضویت در کانال‌ها را کامل کن.", show_alert=True
            )
        return False

    def _check_memberships(self, user: dict[str, Any]) -> bool:
        for channel in self.db.list_force_join_channels(active_only=True):
            try:
                if not self.telegram.is_chat_member(
                    channel["telegram_chat_id"], int(user["telegram_user_id"])
                ):
                    return False
            except TelegramError:
                LOG.exception("Cannot check mandatory channel %s", channel.get("id"))
                return False
        return True

    def _show_join_required(self, user: dict[str, Any], *, page: int = 0) -> None:
        channels = self.db.list_force_join_channels(active_only=True)
        page_size = 12
        page, page_count = self._bounded_page(page, len(channels), page_size)
        visible_channels = channels[page * page_size : (page + 1) * page_size]
        rows: list[list[dict[str, Any]]] = []
        for index, channel in enumerate(visible_channels, page * page_size + 1):
            configured_url = channel.get("invite_url")
            url = (
                configured_url
                if is_safe_telegram_invite_url(configured_url)
                else self._channel_url(channel["telegram_chat_id"])
            )
            if url:
                rows.append([url_button(f"عضویت در کانال {index}", url, style="primary")])
        navigation = self._pagination_buttons(page, page_count, "join:page")
        if navigation:
            rows.append(navigation)
        rows.append([callback_button("بررسی عضویت", "join:check", style="success")])
        content = texts.join_required(visible_channels)
        if page_count > 1:
            content += f"\n\nصفحه {page + 1:,} از {page_count:,}"
        self.telegram.send_message(
            user["chat_id"],
            clamp_text(content),
            reply_markup=inline_keyboard(rows),
        )

    @staticmethod
    def _channel_url(chat_id: str) -> str:
        value = str(chat_id).strip()
        if not re.fullmatch(r"@[A-Za-z0-9_]{5,32}", value):
            return ""
        url = f"https://t.me/{value[1:]}"
        return url if is_safe_telegram_channel_url(url) else ""

    def _edit_or_send(
        self,
        query: dict[str, Any],
        text: str,
        reply_markup: dict[str, Any] | None = None,
    ) -> None:
        message = query.get("message") or {}
        chat_id = (message.get("chat") or {}).get("id")
        message_id = message.get("message_id")
        if chat_id and message_id:
            try:
                self.telegram.edit_message_text(
                    chat_id, message_id, clamp_text(text), reply_markup=reply_markup
                )
                return
            except TelegramAPIError as exc:
                if "message is not modified" in str(exc).lower():
                    return
        if chat_id:
            self.telegram.send_message(chat_id, clamp_text(text), reply_markup=reply_markup)

    @staticmethod
    def _button_label(value: Any, fallback: str = "گزینه") -> str:
        """Remove configured/category emoji from dynamic button text."""

        cleaned = "".join(
            character
            for character in str(value)
            if not contains_emoji(character) and ord(character) not in {0x200D, 0xFE0E, 0xFE0F}
        ).strip()
        return (cleaned or fallback)[:60]

    def _category_path_is_active(self, category_id: int) -> bool:
        """Reject stale callbacks for disabled categories or ancestors."""

        seen: set[int] = set()
        current_id: int | None = int(category_id)
        while current_id is not None:
            if current_id in seen:
                return False
            seen.add(current_id)
            category = self.db.get_category(current_id)
            if not category or not bool(category.get("is_active", 1)):
                return False
            parent = category.get("parent_id")
            current_id = int(parent) if parent is not None else None
        return True

    def _product_is_browsable(self, product: Mapping[str, Any] | None) -> bool:
        return bool(
            product
            and product.get("is_visible", 1)
            and product.get("is_active", 1)
            and self._category_path_is_active(int(product["category_id"]))
        )

    def show_main_menu(self, user: dict[str, Any]) -> None:
        self.db.clear_user_state(user["id"])
        name = user.get("customer_name") or user.get("first_name") or "دوست عزیز"
        markup = inline_main_menu_keyboard(
            self.settings.button_icon_ids,
            str(self.db.get_setting("main_channel_url", "") or ""),
        )
        message = self.telegram.send_message(
            user["chat_id"],
            texts.main_menu(name),
            reply_markup=remove_keyboard(),
        )
        message_id = message.get("message_id") if isinstance(message, Mapping) else None
        if isinstance(message_id, int) and not isinstance(message_id, bool) and message_id > 0:
            try:
                self.telegram.edit_message_reply_markup(
                    user["chat_id"], message_id, reply_markup=markup
                )
                return
            except TelegramRequestCancelled:
                raise
            except TelegramError:
                LOG.warning("Could not attach main-menu buttons; sending a selection message")
        # Keep the canonical welcome text single even when an edit fails.
        self.telegram.send_message(
            user["chat_id"], "یکی از گزینه‌های زیر را انتخاب کن:", reply_markup=markup
        )

    def _dispatch_user_callback(
        self,
        data: str,
        query: dict[str, Any],
        user: dict[str, Any],
        update: dict[str, Any],
    ) -> bool:
        query_id = query["id"]
        if data == "menu":
            self.telegram.answer_callback_query(query_id)
            self.show_main_menu(user)
            return True
        if data == "channel":
            self.telegram.answer_callback_query(query_id)
            self.show_channel(user)
            return True
        if data == "store" or data.startswith("store:"):
            page = self._callback_page(data, "store")
            self.show_store(user["chat_id"], query=query, page=page)
            self.telegram.answer_callback_query(query_id)
            return True
        if data.startswith("cat:"):
            category_id, page = self._callback_id_page(data, "cat", label="شناسه دسته")
            self.show_category(user["chat_id"], category_id, query=query, page=page)
            self.telegram.answer_callback_query(query_id)
            return True
        if data.startswith("prod:"):
            product_id = self._callback_id(data, "prod", label="شناسه محصول")
            self.show_product(user["chat_id"], product_id, query=query)
            self.telegram.answer_callback_query(query_id)
            return True
        if data.startswith("prodmore:"):
            product_id = self._callback_id(data, "prodmore", label="شناسه محصول")
            self.show_product_details(user["chat_id"], product_id, query=query)
            self.telegram.answer_callback_query(query_id)
            return True
        if data.startswith("buy:"):
            product_id = self._callback_id(data, "buy", label="شناسه محصول")
            self.telegram.answer_callback_query(query_id)
            self.begin_purchase(user, product_id, update_id=update.get("update_id"))
            return True
        if data.startswith("order:"):
            order_id = self._callback_id(data, "order", label="شناسه سفارش")
            self.show_order(user, order_id, query=query)
            self.telegram.answer_callback_query(query_id)
            return True
        if data == "profile":
            self.telegram.answer_callback_query(query_id)
            self.show_account(user, query=query)
            return True
        if data == "profile:stats":
            self.telegram.answer_callback_query(query_id)
            self.show_stats(user, query=query)
            return True
        if data == "profile:orders" or data.startswith("profile:orders:"):
            page = self._callback_page(data, "profile:orders")
            self.show_orders(user, query=query, page=page)
            self.telegram.answer_callback_query(query_id)
            return True
        if data == "profile:transactions" or data.startswith("profile:transactions:"):
            page = self._callback_page(data, "profile:transactions")
            self.show_transactions(user, query=query, page=page)
            self.telegram.answer_callback_query(query_id)
            return True
        if data == "wallet":
            self.telegram.answer_callback_query(query_id)
            self.show_wallet(user, query=query)
            return True
        if data == "wallet:topup":
            self.telegram.answer_callback_query(query_id)
            self.db.set_user_state(user["id"], "wallet_topup_amount", {})
            self.telegram.send_message(
                user["chat_id"],
                "💰 مبلغ دلخواه شارژ را به تومان وارد کن.\nمثال: <code>250000</code>",
                reply_markup=remove_keyboard(),
            )
            return True
        if data == "support":
            self.telegram.answer_callback_query(query_id)
            self.show_support(user, query=query)
            return True
        if data == "support:faqs" or data.startswith("support:faqs:"):
            page = self._callback_page(data, "support:faqs")
            self.show_faq_categories(user, query=query, page=page)
            self.telegram.answer_callback_query(query_id)
            return True
        if data.startswith("faqcat:"):
            category_id, page = self._callback_id_page(
                data, "faqcat", label="شناسه دسته سوالات"
            )
            self.show_faqs(user, category_id, query=query, page=page)
            self.telegram.answer_callback_query(query_id)
            return True
        if data.startswith("faq:"):
            faq_id = self._callback_id(data, "faq", label="شناسه سوال")
            self.show_faq(user, faq_id, query=query)
            self.telegram.answer_callback_query(query_id)
            return True
        if data == "ticket:new":
            self.telegram.answer_callback_query(query_id)
            self.db.set_user_state(user["id"], "ticket_subject", {})
            self.telegram.send_message(
                user["chat_id"], "موضوع تیکت را کوتاه بنویس:", reply_markup=remove_keyboard()
            )
            return True
        if data == "tickets:list" or data.startswith("tickets:list:"):
            page = self._callback_page(data, "tickets:list")
            self.show_tickets(user, query=query, page=page)
            self.telegram.answer_callback_query(query_id)
            return True
        if data.startswith("ticketfile:"):
            raw_message_id = data.split(":", 1)[1]
            if not raw_message_id.isdigit():
                self.telegram.answer_callback_query(
                    query_id, "پیوست معتبر نیست.", show_alert=True
                )
                return True
            ticket_message = self.db.get_ticket_message(int(raw_message_id))
            if (
                not ticket_message
                or int(ticket_message["ticket_user_id"]) != int(user["id"])
                or not ticket_message.get("attachment_file_id")
            ):
                self.telegram.answer_callback_query(
                    query_id, "این پیوست در دسترس شما نیست.", show_alert=True
                )
                return True
            self.telegram.answer_callback_query(query_id)
            attachment = str(ticket_message["attachment_file_id"])
            if ticket_message.get("attachment_kind") == "photo":
                self.telegram.send_photo(user["chat_id"], attachment)
            else:
                self.telegram.send_document(user["chat_id"], attachment)
            return True
        if data.startswith("ticketmsg:"):
            raw_message_id = data.split(":", 1)[1]
            ticket_message = (
                self.db.get_ticket_message(int(raw_message_id))
                if raw_message_id.isdigit()
                else None
            )
            if (
                not ticket_message
                or int(ticket_message["ticket_user_id"]) != int(user["id"])
            ):
                self.telegram.answer_callback_query(
                    query_id, "این پیام در دسترس شما نیست.", show_alert=True
                )
                return True
            self.telegram.answer_callback_query(query_id)
            sender = (
                "شما" if ticket_message["sender_type"] == "user" else "پشتیبانی"
            )
            body = str(ticket_message.get("body") or "")
            chunks = [body[index : index + 600] for index in range(0, len(body), 600)] or [""]
            for index, chunk in enumerate(chunks, start=1):
                heading = f"<b>متن کامل پیام از {escape(sender)}</b>\n" if index == 1 else ""
                self.telegram.send_message(user["chat_id"], heading + escape(chunk))
            return True
        if data.startswith("ticket:"):
            ticket_id, parsed_page = self._callback_id_page(
                data, "ticket", label="شناسه تیکت"
            )
            page = parsed_page if len(data.split(":")) == 3 else None
            self.show_ticket(user, ticket_id, query=query, page=page)
            self.telegram.answer_callback_query(query_id)
            return True
        if data.startswith("ticketreply:"):
            ticket_id = self._callback_id(data, "ticketreply", label="شناسه تیکت")
            ticket = self.db.get_ticket(ticket_id)
            if (
                not ticket
                or int(ticket["user_id"]) != int(user["id"])
                or ticket.get("status") == "closed"
            ):
                raise NotFoundError("تیکت باز پیدا نشد.")
            self.db.set_user_state(user["id"], "ticket_reply", {"ticket_id": ticket_id})
            self.telegram.send_message(user["chat_id"], "پیامت را برای پشتیبانی بفرست:")
            self.telegram.answer_callback_query(query_id)
            return True
        if data == "referral":
            self.telegram.answer_callback_query(query_id)
            self.show_referral(user, query=query)
            return True
        return self._dispatch_commerce_callback(data, query, user, update)

    @staticmethod
    def _callback_page(data: str, prefix: str) -> int:
        """Parse a zero-based page suffix while preserving legacy callbacks."""

        if data == prefix:
            return 0
        suffix = data.removeprefix(prefix + ":")
        if not suffix.isascii() or not suffix.isdigit():
            raise ValidationError("شماره صفحه معتبر نیست.")
        return int(suffix)

    @staticmethod
    def _callback_id(data: str, prefix: str, *, label: str = "شناسه") -> int:
        """Parse an exact ``prefix:positive-id`` callback without leaking ValueError."""

        marker = prefix + ":"
        if not data.startswith(marker):
            raise ValidationError(f"{label} معتبر نیست.")
        suffix = data[len(marker) :]
        if not suffix.isascii() or not suffix.isdigit():
            raise ValidationError(f"{label} معتبر نیست.")
        value = int(suffix)
        if value < 1:
            raise ValidationError(f"{label} معتبر نیست.")
        return value

    @classmethod
    def _callback_id_page(
        cls,
        data: str,
        prefix: str,
        *,
        label: str = "شناسه",
    ) -> tuple[int, int]:
        """Parse ``prefix:id`` or ``prefix:id:zero-based-page`` callbacks."""

        parts = data.split(":")
        expected = prefix.split(":")
        if parts[: len(expected)] != expected or len(parts) not in {
            len(expected) + 1,
            len(expected) + 2,
        }:
            raise ValidationError(f"{label} معتبر نیست.")
        raw_id = parts[len(expected)]
        if not raw_id.isascii() or not raw_id.isdigit() or int(raw_id) < 1:
            raise ValidationError(f"{label} معتبر نیست.")
        page = 0
        if len(parts) == len(expected) + 2:
            raw_page = parts[-1]
            if not raw_page.isascii() or not raw_page.isdigit():
                raise ValidationError("شماره صفحه معتبر نیست.")
            page = int(raw_page)
        return int(raw_id), page

    @staticmethod
    def _state_id(data: Any, key: str) -> int | None:
        if not isinstance(data, Mapping):
            return None
        raw = data.get(key)
        try:
            value = int(raw)
        except (TypeError, ValueError, OverflowError):
            return None
        return value if value > 0 else None

    def _end_stale_state(self, user: Mapping[str, Any], message: str) -> None:
        """End a conversation whose referenced entity changed meanwhile."""

        self.db.clear_user_state(int(user["id"]))
        self.telegram.send_message(
            int(user["chat_id"]),
            message,
            reply_markup=main_menu_keyboard(self.settings.button_icon_ids),
        )

    @staticmethod
    def _contact_phone(value: Any) -> str | None:
        normalized = normalize_digits(str(value or "").strip())
        normalized = re.sub(r"[\s()\-]", "", normalized)
        if not re.fullmatch(r"\+?[0-9]{7,15}", normalized):
            return None
        return normalized

    @staticmethod
    def _bounded_page(page: int, total: int, page_size: int) -> tuple[int, int]:
        page_count = max(1, (max(0, int(total)) + page_size - 1) // page_size)
        return min(max(0, int(page)), page_count - 1), page_count

    @staticmethod
    def _pagination_buttons(page: int, page_count: int, prefix: str) -> list[dict[str, Any]]:
        buttons: list[dict[str, Any]] = []
        if page > 0:
            buttons.append(callback_button("صفحه قبل", f"{prefix}:{page - 1}"))
        if page + 1 < page_count:
            buttons.append(callback_button("صفحه بعد", f"{prefix}:{page + 1}"))
        return buttons

    # -- catalog -----------------------------------------------------------

    def show_store(
        self,
        chat_id: int,
        *,
        query: dict[str, Any] | None = None,
        page: int = 0,
    ) -> None:
        categories = self.db.list_categories(parent_id=None, active_only=True)
        page_size = 20
        page, page_count = self._bounded_page(page, len(categories), page_size)
        visible = categories[page * page_size : (page + 1) * page_size]
        rows = [
            [callback_button(self._button_label(item["name"], "دسته"), f"cat:{item['id']}")]
            for item in visible
        ]
        navigation = self._pagination_buttons(page, page_count, "store")
        if navigation:
            rows.append(navigation)
        rows.append([back_button("menu")])
        text = texts.store_title()
        if categories:
            text += f"\n\nصفحه {page + 1:,} از {page_count:,}"
        else:
            text += "\n\nهنوز دسته‌بندی فعالی ثبت نشده است."
        markup = inline_keyboard(rows)
        if query:
            self._edit_or_send(query, text, markup)
        else:
            self.telegram.send_message(chat_id, text, reply_markup=markup)

    def show_category(
        self,
        chat_id: int,
        category_id: int,
        *,
        query: dict[str, Any] | None = None,
        page: int = 0,
    ) -> None:
        category = self.db.get_category(category_id) if hasattr(self.db, "get_category") else None
        if not category:
            category = next(
                (
                    item
                    for parent in (None,)
                    for item in self.db.list_categories(parent_id=parent, active_only=False)
                    if item["id"] == category_id
                ),
                None,
            )
        if not category or not self._category_path_is_active(category_id):
            raise NotFoundError("دسته پیدا نشد.")
        children = self.db.list_categories(parent_id=category_id, active_only=True)
        products = self.db.list_products(category_id=category_id, visible_only=True)
        entries: list[tuple[str, dict[str, Any]]] = [
            ("cat", item) for item in children
        ] + [
            ("prod", item)
            for item in products
            if bool(item.get("is_active", 1))
        ]
        page_size = 20
        page, page_count = self._bounded_page(page, len(entries), page_size)
        visible_entries = entries[page * page_size : (page + 1) * page_size]
        rows: list[list[dict[str, Any]]] = []
        for kind, item in visible_entries:
            rows.append(
                [
                    callback_button(
                        self._button_label(
                            item["name"], "دسته" if kind == "cat" else "محصول"
                        ),
                        f"{kind}:{item['id']}",
                    )
                ]
            )
        navigation = self._pagination_buttons(page, page_count, f"cat:{category_id}")
        if navigation:
            rows.append(navigation)
        parent_callback = f"cat:{category['parent_id']}" if category.get("parent_id") else "store"
        rows.append([back_button(parent_callback)])
        text = texts.category_title(
            category.get("icon") or "",
            category["name"],
            category.get("description") or "",
        )
        if entries:
            text += f"\n\nصفحه {page + 1:,} از {page_count:,}"
        else:
            text += "\n\nدر حال حاضر موردی در این دسته ثبت نشده است."
        markup = inline_keyboard(rows)
        if query:
            self._edit_or_send(query, text, markup)
        else:
            self.telegram.send_message(chat_id, text, reply_markup=markup)

    @staticmethod
    def _product_view(product: dict[str, Any]) -> dict[str, Any]:
        features = product.get("features")
        if features is None:
            try:
                features = json.loads(product.get("features_json") or "[]")
            except (TypeError, ValueError):
                features = []
        if isinstance(features, list):
            features = "\n".join(f"• {value}" for value in features)
        duration = product.get("duration_label")
        if not duration and product.get("duration_days"):
            duration = f"{product['duration_days']} روز"
        return {
            **product,
            "title": product.get("name") or product.get("title") or "محصول",
            "price": int(product.get("price_amount") or product.get("price") or 0),
            "duration": duration,
            "renewable": "قابل تمدید" if product.get("is_renewable") else "غیرقابل تمدید",
            "warranty": product.get("warranty_text") or product.get("warranty") or "ندارد",
            "features": features or "—",
            "rules": product.get("rules_text") or product.get("rules"),
        }

    def show_product(
        self,
        chat_id: int,
        product_id: int,
        *,
        query: dict[str, Any] | None = None,
    ) -> None:
        raw = self.db.get_product(product_id)
        if not self._product_is_browsable(raw):
            raise NotFoundError("محصول پیدا نشد.")
        product = self._product_view(raw)
        rows: list[list[dict[str, Any]]] = [
            [callback_button("خرید", f"buy:{product_id}", style="success")],
            [callback_button("توضیحات تکمیلی", f"prodmore:{product_id}", style="primary")],
        ]
        if is_safe_https_url(product.get("rules_url")):
            rows.append([url_button("مشاهده قوانین", product["rules_url"])])
        rows.append([back_button(f"cat:{product['category_id']}")])
        markup = inline_keyboard(rows)
        content = texts.product_summary(product, self.settings.currency_label)
        if not product.get("is_available", 1):
            content += "\n\n⛔️ این محصول فعلاً موجود نیست."
        parts = split_telegram_html(content)
        for index, part in enumerate(parts):
            part_markup = markup if index == len(parts) - 1 else None
            if query and index == 0:
                self._edit_or_send(query, part, part_markup)
            else:
                self.telegram.send_message(chat_id, part, reply_markup=part_markup)

    def show_product_details(
        self,
        chat_id: int,
        product_id: int,
        *,
        query: dict[str, Any] | None = None,
    ) -> None:
        raw = self.db.get_product(product_id)
        if not self._product_is_browsable(raw):
            raise NotFoundError("محصول پیدا نشد.")
        product = self._product_view(raw)
        rows = [[callback_button("خرید", f"buy:{product_id}", style="success")]]
        if is_safe_https_url(product.get("rules_url")):
            rows.append([url_button("مشاهده قوانین", product["rules_url"])])
        rows.append([back_button(f"prod:{product_id}")])
        markup = inline_keyboard(rows)
        content = texts.product_details(product)
        parts = split_telegram_html(content)
        for index, part in enumerate(parts):
            part_markup = markup if index == len(parts) - 1 else None
            if query and index == 0:
                self._edit_or_send(query, part, part_markup)
            else:
                self.telegram.send_message(chat_id, part, reply_markup=part_markup)

    # -- profile, wallet and referrals ------------------------------------

    def show_account(self, user: dict[str, Any], *, query: dict[str, Any] | None = None) -> None:
        balance = self.db.wallet_balance(user["id"])
        text = (
            "👤 <b>حساب من</b>\n\n"
            f"نام: {escape(user.get('customer_name') or user.get('first_name') or 'ثبت نشده')}\n"
            f"نام کاربری: {('@' + escape(user['username'])) if user.get('username') else '—'}\n"
            f"شماره موبایل: {escape(user.get('phone') or 'ثبت نشده')}\n"
            f"موجودی کیف پول: {money(balance, self.settings.currency_label)}\n"
            f"تاریخ عضویت: {escape(str(user.get('joined_at') or '')[:10])}"
        )
        markup = inline_keyboard(
            [
                [callback_button("آمار من", "profile:stats")],
                [callback_button("سفارش‌های من", "profile:orders")],
                [callback_button("تراکنش‌های من", "profile:transactions")],
                [back_button("menu")],
            ]
        )
        if query:
            self._edit_or_send(query, text, markup)
        else:
            self.telegram.send_message(user["chat_id"], text, reply_markup=markup)

    def show_stats(self, user: dict[str, Any], *, query: dict[str, Any] | None = None) -> None:
        stats = self.db.user_summary(user["id"])
        referral = self.db.referral_summary(user["id"])
        recent_orders = self.db.list_orders(user_id=user["id"], limit=1)
        last_order_at = str(recent_orders[0]["created_at"])[:16] if recent_orders else "—"
        text = (
            "📊 <b>آمار من</b>\n\n"
            f"تعداد سفارش‌ها: {stats.get('order_count', 0)}\n"
            f"مجموع پرداختی: {money(stats.get('purchase_total', 0), self.settings.currency_label)}\n"
            f"موجودی کیف پول: {money(stats.get('wallet_balance', 0), self.settings.currency_label)}\n"
            f"تاریخ آخرین سفارش: {escape(last_order_at)}\n"
            f"تعداد دعوت‌شده‌ها: {referral.get('invited_count', 0)}\n"
            f"پاداش زیرمجموعه‌ها: {money(referral.get('reward_total', 0), self.settings.currency_label)}"
        )
        markup = inline_keyboard([[back_button("profile")]])
        if query:
            self._edit_or_send(query, text, markup)
        else:
            self.telegram.send_message(user["chat_id"], text, reply_markup=markup)

    def show_orders(
        self,
        user: dict[str, Any],
        *,
        query: dict[str, Any] | None = None,
        page: int = 0,
    ) -> None:
        page_size = 20
        total = self.db.count_orders(user_id=user["id"])
        page, page_count = self._bounded_page(page, total, page_size)
        orders = self.db.list_orders(
            user_id=user["id"], limit=page_size, offset=page * page_size
        )
        rows = [
            [
                callback_button(
                    f"{item['order_number']} — {texts.STATUS_LABELS.get(item['status'], item['status'])}",
                    f"order:{item['id']}",
                )
            ]
            for item in orders
        ]
        navigation = self._pagination_buttons(page, page_count, "profile:orders")
        if navigation:
            rows.append(navigation)
        rows.append([back_button("profile")])
        text = (
            "📦 <b>سفارش‌های من</b>"
            f"\n\nتعداد: {total:,}"
            f"\nصفحه {page + 1:,} از {page_count:,}"
        )
        if not orders:
            text += "\nهنوز سفارشی ثبت نکرده‌ای."
        markup = inline_keyboard(rows)
        if query:
            self._edit_or_send(query, text, markup)
        else:
            self.telegram.send_message(user["chat_id"], text, reply_markup=markup)

    def show_transactions(
        self,
        user: dict[str, Any],
        *,
        query: dict[str, Any] | None = None,
        page: int = 0,
    ) -> None:
        # Each rendered reason is capped at 240 HTML characters. Ten entries
        # therefore stay below Telegram's 4096-character message limit; using
        # a larger SQL page and truncating afterwards would make the omitted
        # entries permanently unreachable when the next offset is applied.
        page_size = 10
        total = self.db.count_user_transactions(user["id"])
        page, page_count = self._bounded_page(page, total, page_size)
        entries = self.db.list_user_transactions(
            user["id"], limit=page_size, offset=page * page_size
        )
        lines = [
            "💳 <b>تراکنش‌های من</b>",
            f"تعداد: {total:,} | صفحه {page + 1:,} از {page_count:,}",
        ]
        if not entries:
            lines.append("هنوز تراکنشی ثبت نشده است.")
        for entry in entries:
            sign = "+" if int(entry["amount_signed"]) > 0 else ""
            reason = clamp_text(
                escape(entry.get("reason") or ""),
                240,
            )
            kind = texts.transaction_type(entry.get("entry_type"), entry.get("method"))
            lines.append(
                f"{escape(str(entry['created_at'])[:16])} | {sign}{money(entry['amount_signed'], self.settings.currency_label)}\n"
                f"نوع: {kind}\n"
                f"{reason}"
            )
        rows: list[list[dict[str, Any]]] = []
        navigation = self._pagination_buttons(page, page_count, "profile:transactions")
        if navigation:
            rows.append(navigation)
        rows.append([back_button("profile")])
        markup = inline_keyboard(rows)
        content = "\n\n".join(lines)
        if query:
            self._edit_or_send(query, content, markup)
        else:
            self.telegram.send_message(user["chat_id"], content, reply_markup=markup)

    def show_wallet(self, user: dict[str, Any], *, query: dict[str, Any] | None = None) -> None:
        balance = self.db.wallet_balance(user["id"])
        active_payments = self.db.list_active_wallet_topup_payments(int(user["id"]))
        content = (
            "👛 <b>کیف پول</b>\n\n"
            f"موجودی فعلی: {money(balance, self.settings.currency_label)}"
        )
        rows: list[list[dict[str, Any]]] = []
        support_button_added = False
        for active in active_payments:
            method = str(active.get("method") or "")
            method_label = "کارت‌به‌کارت" if method == "card" else "ارزی"
            content += (
                f"\n\n<b>پرداخت {escape(method_label)} در حال پیگیری:</b>\n"
                f"مبلغ: {money(active['payable_amount'], self.settings.currency_label)}\n"
                f"وضعیت: {escape(texts.STATUS_LABELS.get(active['status'], active['status']))}"
            )
            if method == "card":
                card_number = str(self.db.get_setting("card_number", "") or "")
                card_owner = str(self.db.get_setting("card_owner", "") or "")
                if card_number and card_owner:
                    content += (
                        f"\nشماره کارت: <code>{escape(card_number)}</code>"
                        f"\nبه نام: {escape(card_owner)}"
                    )
                rows.append(
                    [
                        callback_button(
                            "ارسال فیش واریز",
                            f"receipt:{active['id']}",
                            style="primary",
                            icon_custom_emoji_id=self.settings.button_icon_ids.get(
                                "receipt"
                            ),
                        )
                    ]
                )
                if active.get("status") == "pending" and not active.get(
                    "receipt_file_id"
                ):
                    rows.append(
                        [
                            callback_button(
                                "لغو پرداخت",
                                f"cancelpay:{active['id']}",
                                style="danger",
                                icon_custom_emoji_id=self.settings.button_icon_ids.get(
                                    "cancel"
                                ),
                            )
                        ]
                    )
            elif method == "crypto":
                invoice_url = str(active.get("provider_invoice_url") or "")
                if is_safe_https_url(invoice_url):
                    rows.append(
                        [
                            url_button(
                                "ادامه پرداخت ارزی",
                                invoice_url,
                                style="success",
                            )
                        ]
                    )
                elif not active.get("provider_invoice_id") and not invoice_url:
                    content += (
                        "\n\nساخت صورتحساب قبلی کامل نشده است. "
                        "برای ادامه، همان درخواست را دوباره امتحان کن."
                    )
                    rows.append(
                        [
                            callback_button(
                                "تلاش دوباره برای پرداخت ارزی",
                                f"topupcrypto:{int(active['base_amount'])}",
                                style="primary",
                            )
                        ]
                    )
                else:
                    content += (
                        "\n\nلینک این پرداخت ارزی در دسترس نیست. "
                        "برای پیگیری با پشتیبانی تماس بگیر."
                    )
                    if not support_button_added:
                        rows.append(
                            [
                                callback_button(
                                    "پیگیری از پشتیبانی",
                                    "support",
                                    style="primary",
                                )
                            ]
                        )
                        support_button_added = True
        if not active_payments:
            content += (
                "\n\nبرای افزایش اعتبار، روی دکمه زیر بزن و مبلغ دلخواهت رو وارد کن."
            )
            rows.append(
                [
                    callback_button(
                        "افزایش موجودی", "wallet:topup", style="success"
                    )
                ]
            )
        rows.append([back_button("menu")])
        markup = inline_keyboard(rows)
        if query:
            self._edit_or_send(query, content, markup)
        else:
            self.telegram.send_message(user["chat_id"], content, reply_markup=markup)

    def show_referral(self, user: dict[str, Any], *, query: dict[str, Any] | None = None) -> None:
        stats = self.db.referral_summary(user["id"])
        current_rules: list[dict[str, Any]] = []
        now = utc_now()
        offset = 0
        product_names: dict[int, str] = {}
        while True:
            rules = self.db.list_reward_rules(active_only=True, limit=200, offset=offset)
            for rule in rules:
                starts_at = parse_iso(rule.get("starts_at"))
                ends_at = parse_iso(rule.get("ends_at"))
                if (starts_at and starts_at > now) or (ends_at and ends_at <= now):
                    continue
                conditions = json.loads(rule.get("conditions_json") or "{}")
                scoped_ids = (
                    [int(rule["product_id"])]
                    if rule.get("product_id") is not None
                    else list(conditions.get("product_ids") or [])
                )
                for product_id in scoped_ids:
                    if product_id not in product_names:
                        product = self.db.get_product(product_id)
                        product_names[product_id] = (
                            str(product["name"]) if product else f"محصول {product_id}"
                        )
                current_rules.append({
                    **rule,
                    "conditions": conditions,
                    "product_names": [product_names[item] for item in scoped_ids],
                })
            offset += len(rules)
            if len(rules) < 200:
                break
        link = f"https://t.me/{self.bot_username}?start=ref_{user['telegram_user_id']}"
        share = "https://t.me/share/url?url=" + quote(link, safe="")
        markup = inline_keyboard(
            [
                [url_button("ارسال لینک به دوستان", share, style="primary")],
                [back_button("menu")],
            ]
        )
        content = texts.referral_page(
            stats.get("invited_count", 0),
            stats.get("reward_total", 0),
            link,
            self.settings.currency_label,
            current_rules,
        )
        # Keep each ordinary rule together, so its amount and conditions are
        # readable in one message; split only a single unusually long block.
        parts: list[str] = []
        for block in content.split("\n\n"):
            for part in split_telegram_html(block):
                if parts and len(parts[-1]) + len(part) + 2 <= 3900:
                    parts[-1] += "\n\n" + part
                else:
                    parts.append(part)
        for index, part in enumerate(parts):
            part_markup = markup if index == len(parts) - 1 else None
            if query and index == 0:
                self._edit_or_send(query, part, part_markup)
            else:
                self.telegram.send_message(user["chat_id"], part, reply_markup=part_markup)

    def show_channel(self, user: dict[str, Any]) -> None:
        url = str(self.db.get_setting("main_channel_url", "") or "")
        if not is_safe_telegram_channel_url(url):
            self.telegram.send_message(user["chat_id"], "لینک معتبر کانال هنوز توسط مدیریت ثبت نشده است.")
            return
        self.telegram.send_message(
            user["chat_id"],
            "🤝 برای ورود به کانال رسمی الون اکانت روی دکمه زیر بزن.",
            reply_markup=inline_keyboard([[url_button("ورود به کانال", url)]]),
        )

    # -- support -----------------------------------------------------------

    def show_support(self, user: dict[str, Any], *, query: dict[str, Any] | None = None) -> None:
        markup = inline_keyboard(
            [
                [callback_button("سوالات متداول", "support:faqs")],
                [callback_button("ثبت تیکت", "ticket:new", style="primary")],
                [callback_button("تیکت‌های قبلی", "tickets:list")],
                [back_button("menu")],
            ]
        )
        content = "💬 <b>پشتیبانی</b>\n\nسوالات متداول را ببین یا یک تیکت جدید ثبت کن."
        if query:
            self._edit_or_send(query, content, markup)
        else:
            self.telegram.send_message(user["chat_id"], content, reply_markup=markup)

    def show_faq_categories(
        self,
        user: dict[str, Any],
        *,
        query: dict[str, Any],
        page: int = 0,
    ) -> None:
        categories = self.db.list_faq_categories(active_only=True)
        page_size = 20
        page, page_count = self._bounded_page(page, len(categories), page_size)
        visible = categories[page * page_size : (page + 1) * page_size]
        rows = [
            [callback_button(self._button_label(item["name"], "دسته سوال"), f"faqcat:{item['id']}")]
            for item in visible
        ]
        navigation = self._pagination_buttons(page, page_count, "support:faqs")
        if navigation:
            rows.append(navigation)
        rows.append([back_button("support")])
        text = "❓ <b>سوالات متداول</b>\n\nیک دسته را انتخاب کن."
        if categories:
            text += f"\n\nصفحه {page + 1:,} از {page_count:,}"
        else:
            text += "\n\nهنوز سوالی ثبت نشده است."
        self._edit_or_send(query, text, inline_keyboard(rows))

    def show_faqs(
        self,
        user: dict[str, Any],
        category_id: int,
        *,
        query: dict[str, Any],
        page: int = 0,
    ) -> None:
        category = self.db.get_faq_category(category_id)
        if not category or not category.get("is_active"):
            raise NotFoundError("دسته سوالات پیدا نشد.")
        faqs = self.db.list_faqs(category_id=category_id, active_only=True)
        page_size = 20
        page, page_count = self._bounded_page(page, len(faqs), page_size)
        visible = faqs[page * page_size : (page + 1) * page_size]
        rows = [
            [callback_button(self._button_label(item["question"], "مشاهده سوال"), f"faq:{item['id']}")]
            for item in visible
        ]
        navigation = self._pagination_buttons(page, page_count, f"faqcat:{category_id}")
        if navigation:
            rows.append(navigation)
        rows.append([back_button("support:faqs")])
        text = "❓ سوال موردنظرت را انتخاب کن:"
        if faqs:
            text += f"\n\nصفحه {page + 1:,} از {page_count:,}"
        else:
            text += "\n\nدر این دسته هنوز سوالی ثبت نشده است."
        self._edit_or_send(query, text, inline_keyboard(rows))

    def show_faq(self, user: dict[str, Any], faq_id: int, *, query: dict[str, Any]) -> None:
        faq = self.db.get_faq(faq_id)
        if not faq or not faq.get("is_active"):
            raise NotFoundError("سوال پیدا نشد.")
        if faq.get("category_id") is None:
            raise NotFoundError("سوال پیدا نشد.")
        category = self.db.get_faq_category(int(faq["category_id"]))
        if not category or not category.get("is_active"):
            raise NotFoundError("سوال پیدا نشد.")
        text = (
            f"❓ <b>{escape(faq['question'])}</b>\n\n"
            f"{render_rich_text(faq['answer'])}"
        )
        markup = inline_keyboard([[back_button(f"faqcat:{faq['category_id']}")]])
        parts = split_telegram_html(text)
        for index, part in enumerate(parts):
            part_markup = markup if index == len(parts) - 1 else None
            if index == 0:
                self._edit_or_send(query, part, part_markup)
            else:
                self.telegram.send_message(user["chat_id"], part, reply_markup=part_markup)

    def show_tickets(
        self,
        user: dict[str, Any],
        *,
        query: dict[str, Any] | None = None,
        page: int = 0,
    ) -> None:
        page_size = 20
        total = self.db.count_tickets(user_id=user["id"])
        page, page_count = self._bounded_page(page, total, page_size)
        tickets = self.db.list_tickets(
            user_id=user["id"], limit=page_size, offset=page * page_size
        )
        rows = [
            [
                callback_button(
                    self._button_label(f"{item['ticket_number']} — {item['subject']}", "مشاهده تیکت"),
                    f"ticket:{item['id']}",
                )
            ]
            for item in tickets
        ]
        navigation = self._pagination_buttons(page, page_count, "tickets:list")
        if navigation:
            rows.append(navigation)
        rows.append([back_button("support")])
        text = (
            "🎫 <b>تیکت‌های قبلی</b>"
            f"\n\nتعداد: {total:,} | صفحه {page + 1:,} از {page_count:,}"
        )
        if not tickets:
            text += "\n\nهنوز تیکتی ثبت نکرده‌ای."
        markup = inline_keyboard(rows)
        if query:
            self._edit_or_send(query, text, markup)
        else:
            self.telegram.send_message(user["chat_id"], text, reply_markup=markup)

    def show_ticket(
        self,
        user: dict[str, Any],
        ticket_id: int,
        *,
        query: dict[str, Any],
        page: int | None = None,
    ) -> None:
        ticket = self.db.get_ticket(ticket_id)
        if not ticket or ticket["user_id"] != user["id"]:
            raise NotFoundError("تیکت پیدا نشد.")
        messages = self.db.list_ticket_messages(ticket_id)
        blocks: list[tuple[str, dict[str, Any], bool]] = []
        for message_index, item in enumerate(messages, start=1):
            sender = "شما" if item["sender_type"] == "user" else "پشتیبانی"
            body = str(item.get("body") or "")
            parts = [body[index : index + 500] for index in range(0, len(body), 500)] or [""]
            for part_index, body_part in enumerate(parts, start=1):
                continuation = (
                    f" ({part_index}/{len(parts)})" if len(parts) > 1 else ""
                )
                block = (
                    f"<b>{escape(sender)} {message_index:,}{continuation}:</b> "
                    f"{escape(body_part)}"
                )
                has_attachment = bool(
                    part_index == len(parts) and item.get("attachment_file_id")
                )
                if has_attachment:
                    block += f"\nپیوست پیام {message_index:,} آماده دریافت است."
                blocks.append((block, item, has_attachment))

        packed_pages: list[list[tuple[str, dict[str, Any], bool]]] = [[]]
        packed_length = 0
        for block in blocks:
            separator_length = 2 if packed_pages[-1] else 0
            if (
                packed_pages[-1]
                and packed_length + separator_length + len(block[0]) > 3_000
            ):
                packed_pages.append([])
                packed_length = 0
                separator_length = 0
            packed_pages[-1].append(block)
            packed_length += separator_length + len(block[0])

        page_count = len(packed_pages)
        selected_page = page_count - 1 if page is None else int(page)
        selected_page, page_count = self._bounded_page(
            selected_page, page_count, 1
        )
        selected_blocks = packed_pages[selected_page]
        lines = [
            f"🎫 <b>{escape(ticket['ticket_number'])} — {escape(ticket['subject'])}</b>",
            f"وضعیت: {escape(ticket['status'])}",
            f"تعداد پیام‌ها: {len(messages):,}",
            f"صفحه گفتگو: {selected_page + 1:,} از {page_count:,}",
        ]
        rows: list[list[dict[str, Any]]] = []
        if selected_blocks:
            lines.extend(block[0] for block in selected_blocks)
            seen_attachments: set[int] = set()
            for _content, item, has_attachment in selected_blocks:
                message_id = int(item["id"])
                if has_attachment and message_id not in seen_attachments:
                    seen_attachments.add(message_id)
                    rows.append(
                        [
                            callback_button(
                                "دریافت پیوست",
                                f"ticketfile:{message_id}",
                            )
                        ]
                    )
        else:
            lines.append("هنوز پیامی در این تیکت ثبت نشده است.")
        conversation_navigation: list[dict[str, Any]] = []
        if selected_page > 0:
            conversation_navigation.append(
                callback_button("قدیمی‌تر", f"ticket:{ticket_id}:{selected_page - 1}")
            )
        if selected_page + 1 < page_count:
            conversation_navigation.append(
                callback_button("جدیدتر", f"ticket:{ticket_id}:{selected_page + 1}")
            )
        if conversation_navigation:
            rows.append(conversation_navigation)
        if ticket["status"] != "closed":
            rows.append([callback_button("ارسال پاسخ", f"ticketreply:{ticket_id}", style="primary")])
        rows.append([back_button("tickets:list")])
        self._edit_or_send(query, "\n\n".join(lines), inline_keyboard(rows))

    # The commerce, state, fulfilment, maintenance and admin bridge methods
    # continue below. They are kept in this class so every state transition can
    # be made idempotent with one database transaction.

    # -- purchase and payment ----------------------------------------------

    @staticmethod
    def _order_view(order: dict[str, Any]) -> dict[str, Any]:
        subtotal = int(order.get("subtotal_amount") or order.get("base_price") or 0)
        discount = int(order.get("discount_amount") or 0)
        return {
            **order,
            "order_no": order.get("order_number") or order.get("order_no"),
            "product_title": order.get("product_name_snapshot") or order.get("product_title") or "محصول",
            "product_icon": order.get("product_icon_snapshot") or order.get("product_icon") or "",
            "product_duration": order.get("duration_label_snapshot")
            or order.get("product_duration")
            or (
                f"{order['duration_days_snapshot']} روز"
                if order.get("duration_days_snapshot")
                else "—"
            ),
            "base_price": subtotal,
            "discount_amount": discount,
            "final_amount": subtotal - discount,
        }

    def begin_purchase(
        self,
        user: dict[str, Any],
        product_id: int,
        *,
        update_id: int | None = None,
    ) -> None:
        product = self.db.get_product(product_id)
        if not self._product_is_browsable(product):
            self.telegram.send_message(user["chat_id"], "این محصول دیگر قابل خرید نیست.")
            return
        if not product.get("is_available", 1):
            self.telegram.send_message(user["chat_id"], "⛔️ این محصول فعلاً موجود نیست.")
            return
        if product.get("product_type") == "ready":
            available = self.db.inventory_count(product_id)
            if available <= 0 and not product.get("reserve_enabled"):
                self.telegram.send_message(user["chat_id"], "⛔️ این محصول فعلاً موجود نیست.")
                return

        state_data = {"product_id": product_id, "update_id": update_id}
        if not user.get("customer_name"):
            self.db.set_user_state(user["id"], "purchase_name", state_data)
            self.telegram.send_message(
                user["chat_id"], texts.ASK_NAME, reply_markup=remove_keyboard()
            )
            return
        if not user.get("phone"):
            self.db.set_user_state(user["id"], "purchase_phone", state_data)
            self.telegram.send_message(
                user["chat_id"],
                texts.ASK_PHONE,
                reply_markup=contact_keyboard(
                    icon_custom_emoji_id=self.settings.button_icon_ids.get("phone")
                ),
            )
            return
        self._create_order_and_confirm(user, product_id, update_id=update_id)

    def _create_order_and_confirm(
        self,
        user: dict[str, Any],
        product_id: int,
        *,
        update_id: int | None,
    ) -> dict[str, Any]:
        key = f"purchase:{user['id']}:{product_id}:{update_id or uuid.uuid4().hex}"
        balance = self.db.wallet_balance(int(user["id"]))

        def created_notice(
            created_order: Mapping[str, Any],
        ) -> tuple[str, str, Mapping[str, Any]]:
            markup = inline_keyboard(
                [
                    [
                        callback_button(
                            "پرداخت",
                            f"checkout:{created_order['id']}",
                            style="success",
                        )
                    ],
                    [
                        callback_button(
                            "ثبت کد تخفیف",
                            f"discount:{created_order['id']}",
                            style="primary",
                        )
                    ],
                    [back_button(f"prod:{created_order['product_id']}")],
                ]
            )
            return (
                texts.order_summary(
                    self._order_view(dict(created_order)),
                    balance,
                    self.settings.currency_label,
                ),
                f"order:{int(created_order['id'])}:created-summary",
                markup,
            )

        order = self.db.create_order(
            user["id"],
            product_id,
            idempotency_key=key,
            expires_in_minutes=self.settings.order_expiry_minutes,
            defer_free_confirmation=True,
            order_notice=created_notice,
        )
        notice_key = f"order:{int(order['id'])}:created-summary"
        notice = self.db.get_outbound_message_by_idempotency_key(notice_key)
        if notice is None:
            raise DatabaseError("created-order confirmation was not persisted")
        markup = (
            json.loads(str(notice["reply_markup_json"]))
            if notice.get("reply_markup_json")
            else None
        )
        self._notify_user_durable(
            user,
            str(notice["body"]),
            idempotency_key=notice_key,
            reply_markup=markup,
        )
        if order["status"] == "paid":
            self.fulfill_order(order["id"])
        return order

    def show_order_summary(
        self,
        user: dict[str, Any],
        order: dict[str, Any] | int,
        *,
        query: dict[str, Any] | None = None,
    ) -> None:
        if isinstance(order, int):
            found = self.db.get_order(order)
            if not found:
                raise NotFoundError("سفارش پیدا نشد.")
            order = found
        if order["user_id"] != user["id"]:
            raise NotFoundError("سفارش پیدا نشد.")
        view = self._order_view(order)
        balance = self.db.wallet_balance(user["id"])
        markup = inline_keyboard(
            [
                [callback_button("پرداخت", f"checkout:{order['id']}", style="success")],
                [callback_button("ثبت کد تخفیف", f"discount:{order['id']}", style="primary")],
                [back_button(f"prod:{order['product_id']}")],
            ]
        )
        content = texts.order_summary(view, balance, self.settings.currency_label)
        if query:
            self._edit_or_send(query, content, markup)
        else:
            self.telegram.send_message(
                user["chat_id"], content, reply_markup=markup
            )

    def show_payment_methods(
        self,
        user: dict[str, Any],
        order_id: int,
        *,
        query: dict[str, Any] | None = None,
    ) -> None:
        order = self.db.get_order(order_id)
        if not order or order["user_id"] != user["id"]:
            raise NotFoundError("سفارش پیدا نشد.")
        if order["status"] not in {"pending_payment", "awaiting_confirmation"}:
            self.show_order(user, order_id, query=query)
            return
        rows: list[list[dict[str, Any]]] = []
        if self.db.get_setting("payment_wallet_enabled", True):
            rows.append([callback_button("کیف پول", f"paywallet:{order_id}", style="success")])
        if self._card_payment_available():
            rows.append([callback_button("کارت به کارت", f"paycard:{order_id}", style="primary")])
        if self._crypto_payment_available():
            rows.append([callback_button("پرداخت ارزی", f"paycrypto:{order_id}", style="primary")])
        rows.append([back_button(f"ordersummary:{order_id}")])
        balance = self.db.wallet_balance(user["id"])
        content = texts.payment_methods(
            self._order_view(order), balance, self.settings.currency_label
        )
        markup = inline_keyboard(rows)
        if query:
            self._edit_or_send(query, content, markup)
        else:
            self.telegram.send_message(user["chat_id"], content, reply_markup=markup)

    def _card_payment_available(self) -> bool:
        return bool(
            self.db.get_setting("payment_card_enabled", True)
            and str(self.db.get_setting("card_number", "") or "").strip()
            and str(self.db.get_setting("card_owner", "") or "").strip()
        )

    def _crypto_payment_available(self) -> bool:
        return bool(
            self.db.get_setting("payment_crypto_enabled", False) and self._plisio
        )

    def _dispatch_commerce_callback(
        self,
        data: str,
        query: dict[str, Any],
        user: dict[str, Any],
        update: dict[str, Any],
    ) -> bool:
        query_id = query["id"]
        if data.startswith("ordersummary:"):
            order_id = self._callback_id(data, "ordersummary", label="شناسه سفارش")
            # A Back callback is also an explicit cancellation of the
            # conversational discount prompt.
            state = self.db.get_user_state(user["id"])
            if state and state.get("state") == "discount_code":
                self.db.clear_user_state(user["id"])
            self.show_order_summary(user, order_id, query=query)
            self.telegram.answer_callback_query(query_id)
            return True
        if data.startswith("discount:"):
            order_id = self._callback_id(data, "discount", label="شناسه سفارش")
            order = self.db.get_order(order_id)
            if not order or order["user_id"] != user["id"]:
                raise NotFoundError("سفارش پیدا نشد.")
            self.telegram.answer_callback_query(query_id)
            self.db.set_user_state(user["id"], "discount_code", {"order_id": order_id})
            self.telegram.send_message(
                user["chat_id"],
                texts.DISCOUNT_PROMPT,
                reply_markup=inline_keyboard([[back_button(f"ordersummary:{order_id}")]]),
            )
            return True
        if data.startswith("checkout:"):
            order_id = self._callback_id(data, "checkout", label="شناسه سفارش")
            order = self.db.get_order(order_id)
            if not order or order["user_id"] != user["id"]:
                raise NotFoundError("سفارش پیدا نشد.")
            if (
                order["status"] == "pending_payment"
                and int(order["subtotal_amount"]) - int(order["discount_amount"]) == 0
            ):
                order = self.db.confirm_zero_payable_order(order_id, int(user["id"]))
                is_free = int(order["subtotal_amount"]) == 0
                self._notify_user_durable(
                    user,
                    texts.payment_success(
                        self._order_view(order), 0,
                        "سفارش رایگان" if is_free else "تخفیف کامل",
                        self.settings.currency_label,
                    ),
                    idempotency_key=(
                        f"order:{order['id']}:{'free' if is_free else 'discount'}-confirmed"
                    ),
                )
                self._after_order_paid(int(order["id"]))
                self.telegram.answer_callback_query(query_id, "سفارش تأیید شد.")
                return True
            self.show_payment_methods(user, order_id, query=query)
            self.telegram.answer_callback_query(query_id)
            return True
        if data.startswith("paywallet:"):
            if not bool(self.db.get_setting("payment_wallet_enabled", True)):
                raise ValidationError("پرداخت با کیف پول غیرفعال است.")
            order_id = self._callback_id(data, "paywallet", label="شناسه سفارش")
            existing_order = self.db.get_order(order_id)
            if not existing_order or existing_order["user_id"] != user["id"]:
                raise NotFoundError("سفارش پیدا نشد.")
            try:
                order = self.db.hold_wallet_funds(
                    order_id,
                    # Each Telegram callback may apply newly topped-up funds,
                    # while replaying the same update stays exactly-once.
                    idempotency_key=(
                        f"order:{order_id}:wallet-hold:"
                        f"{update.get('update_id') or query_id}"
                    ),
                )
            except InsufficientFundsError:
                self.telegram.answer_callback_query(
                    query_id,
                    "⚠️ موجودی کیف پول کافی نیست\nبرای پرداخت این سفارش، اول کیف پولت رو شارژ کن.",
                    show_alert=True,
                )
                return True
            if order["status"] == "paid":
                self._notify_user_durable(
                    user,
                    texts.payment_success(
                        self._order_view(order),
                        int(order["wallet_captured_amount"]),
                        "کیف پول",
                        self.settings.currency_label,
                    ),
                    idempotency_key=f"order:{order['id']}:wallet-confirmed",
                )
                self._after_order_paid(order["id"])
                self.telegram.answer_callback_query(query_id, "پرداخت از کیف پول انجام شد.")
            else:
                self.telegram.answer_callback_query(
                    query_id, "موجودی کیف پول روی سفارش اعمال شد."
                )
                self.show_payment_methods(user, order_id, query=query)
            return True
        if data.startswith("paycard:"):
            if not self._card_payment_available():
                raise ValidationError("پرداخت کارت‌به‌کارت فعال و تنظیم‌شده نیست.")
            order_id = self._callback_id(data, "paycard", label="شناسه سفارش")
            self._begin_card_payment(user, order_id, query=query)
            self.telegram.answer_callback_query(query_id)
            return True
        if data.startswith("paycrypto:"):
            if not self._crypto_payment_available():
                raise ValidationError("پرداخت ارزی فعال و تنظیم‌شده نیست.")
            order_id = self._callback_id(data, "paycrypto", label="شناسه سفارش")
            self._begin_crypto_payment(user, order_id, query=query)
            self.telegram.answer_callback_query(query_id)
            return True
        if data.startswith("receipt:"):
            payment_id = self._callback_id(data, "receipt", label="شناسه پرداخت")
            payment = self.db.get_payment(payment_id)
            if not payment or payment["user_id"] != user["id"]:
                raise NotFoundError("پرداخت پیدا نشد.")
            if payment.get("method") != "card":
                self.telegram.answer_callback_query(
                    query_id,
                    "ارسال فیش فقط برای پرداخت کارت‌به‌کارت مجاز است.",
                    show_alert=True,
                )
                return True
            expires_at = parse_iso(payment.get("expires_at"))
            if expires_at and expires_at <= utc_now() and not payment.get("receipt_file_id"):
                self.telegram.answer_callback_query(
                    query_id, "مهلت این پرداخت تمام شده است.", show_alert=True
                )
                return True
            age = (utc_now() - (parse_iso(payment["created_at"]) or utc_now())).total_seconds()
            if age < self.settings.receipt_delay_seconds:
                self.telegram.answer_callback_query(
                    query_id, "پرداخت هنوز در حال بررسی است.", show_alert=True
                )
                self.telegram.send_message(user["chat_id"], texts.EARLY_RECEIPT)
                return True
            if payment["status"] not in {"pending", "verifying"}:
                self.telegram.answer_callback_query(
                    query_id, "این پرداخت دیگر فیش جدید نمی‌پذیرد.", show_alert=True
                )
                return True
            self.telegram.answer_callback_query(query_id)
            self.db.set_user_state(user["id"], "payment_receipt", {"payment_id": payment_id})
            self.telegram.send_message(
                user["chat_id"],
                "📎 تصویر یا فایل فیش واریز را همینجا ارسال کن.",
                reply_markup=remove_keyboard(),
            )
            return True
        if data.startswith("cancelpay:"):
            payment_id = self._callback_id(data, "cancelpay", label="شناسه پرداخت")
            payment = self.db.get_payment(payment_id)
            if not payment or payment["user_id"] != user["id"]:
                raise NotFoundError("پرداخت پیدا نشد.")
            if payment.get("method") == "crypto":
                self.telegram.answer_callback_query(
                    query_id,
                    "صورتحساب ارزی صادرشده قابل لغو نیست؛ تا پایان مهلت آن صبر کن.",
                    show_alert=True,
                )
                return True
            try:
                self.db.cancel_pending_payment(payment_id, int(user["id"]))
            except ValidationError:
                self.telegram.answer_callback_query(
                    query_id,
                    "پرداختِ دارای فیش یا درحال بررسی قابل لغو نیست.",
                    show_alert=True,
                )
                return True
            self.telegram.answer_callback_query(query_id, "پرداخت لغو شد.")
            self.show_main_menu(user)
            return True
        if data.startswith("orderinfo:"):
            order_id = self._callback_id(data, "orderinfo", label="شناسه سفارش")
            order = self.db.get_order(order_id)
            if not order or order["user_id"] != user["id"]:
                raise NotFoundError("سفارش پیدا نشد.")
            if order["status"] not in {"awaiting_info", "processing"}:
                self.telegram.answer_callback_query(query_id, "این سفارش اطلاعات جدید نمی‌پذیرد.")
                return True
            self.telegram.answer_callback_query(query_id)
            self.db.set_user_state(user["id"], "order_information", {"order_id": order_id})
            self.telegram.send_message(
                user["chat_id"],
                "📤 اطلاعات موردنیاز را به‌صورت متن، تصویر یا فایل ارسال کن.",
                reply_markup=remove_keyboard(),
            )
            return True
        if data.startswith("topupcard:"):
            if not self._card_payment_available():
                raise ValidationError("پرداخت کارت‌به‌کارت فعال و تنظیم‌شده نیست.")
            amount = self._callback_id(data, "topupcard", label="مبلغ شارژ")
            self._validate_topup_amount(amount)
            self.telegram.answer_callback_query(query_id)
            self._begin_card_topup(user, amount, query=query)
            return True
        if data.startswith("topupcrypto:"):
            if not self._crypto_payment_available():
                raise ValidationError("پرداخت ارزی فعال و تنظیم‌شده نیست.")
            amount = self._callback_id(data, "topupcrypto", label="مبلغ شارژ")
            self._validate_topup_amount(amount)
            self.telegram.answer_callback_query(query_id)
            self._begin_crypto_topup(user, amount, query=query)
            return True
        return False

    def _validate_topup_amount(self, amount: int) -> None:
        minimum = int(self.db.get_setting("minimum_topup_amount", 10_000) or 10_000)
        maximum = int(
            self.db.get_setting("maximum_topup_amount", 100_000_000) or 100_000_000
        )
        if not minimum <= int(amount) <= maximum:
            raise ValidationError(
                f"مبلغ شارژ باید بین {money(minimum, self.settings.currency_label)} "
                f"و {money(maximum, self.settings.currency_label)} باشد."
            )

    def _begin_card_payment(
        self,
        user: dict[str, Any],
        order_id: int,
        *,
        query: dict[str, Any] | None,
    ) -> None:
        if not self._card_payment_available():
            raise ValidationError("پرداخت کارت‌به‌کارت فعال و تنظیم‌شده نیست.")
        card_number = str(self.db.get_setting("card_number", "") or "")
        card_owner = str(self.db.get_setting("card_owner", "") or "")
        if not card_number or not card_owner:
            self.telegram.send_message(
                user["chat_id"], "پرداخت کارت‌به‌کارت هنوز توسط مدیریت تنظیم نشده است."
            )
            return
        order = self.db.get_order(order_id)
        if not order or order["user_id"] != user["id"]:
            raise NotFoundError("سفارش پیدا نشد.")
        payment = self.db.create_order_payment(
            order_id,
            "card",
            idempotency_key=f"order:{order_id}:card:{uuid.uuid4().hex}",
            expires_in_minutes=self.settings.order_expiry_minutes,
            unique_amount_window=999,
        )
        self._show_card_payment(user, payment, card_number, card_owner, query=query)

    def _begin_card_topup(
        self,
        user: dict[str, Any],
        amount: int,
        *,
        query: dict[str, Any] | None,
    ) -> None:
        if not self._card_payment_available():
            raise ValidationError("پرداخت کارت‌به‌کارت فعال و تنظیم‌شده نیست.")
        card_number = str(self.db.get_setting("card_number", "") or "")
        card_owner = str(self.db.get_setting("card_owner", "") or "")
        if not card_number or not card_owner:
            self.telegram.send_message(
                user["chat_id"], "پرداخت کارت‌به‌کارت هنوز توسط مدیریت تنظیم نشده است."
            )
            return
        payment = self.db.create_wallet_topup_payment(
            user["id"],
            amount,
            "card",
            idempotency_key=f"topup:{user['id']}:{amount}:card:{uuid.uuid4().hex}",
            currency="TOMAN",
            expires_in_minutes=self.settings.order_expiry_minutes,
            unique_amount_window=999,
        )
        self._show_card_payment(user, payment, card_number, card_owner, query=query)

    def _show_card_payment(
        self,
        user: dict[str, Any],
        payment: dict[str, Any],
        card_number: str,
        card_owner: str,
        *,
        query: dict[str, Any] | None,
    ) -> None:
        markup = inline_keyboard(
            [
                [
                    copy_text_button(
                        "کپی مبلغ",
                        str(payment["payable_amount"]),
                        icon_custom_emoji_id=self.settings.button_icon_ids.get("copy"),
                    )
                ],
                [
                    copy_text_button(
                        "کپی شماره کارت",
                        card_number.replace(" ", "").replace("-", ""),
                        icon_custom_emoji_id=self.settings.button_icon_ids.get("copy"),
                    )
                ],
                [
                    callback_button(
                        "ارسال فیش واریز",
                        f"receipt:{payment['id']}",
                        style="primary",
                        icon_custom_emoji_id=self.settings.button_icon_ids.get("receipt"),
                    )
                ],
                [
                    callback_button(
                        "لغو پرداخت",
                        f"cancelpay:{payment['id']}",
                        style="danger",
                        icon_custom_emoji_id=self.settings.button_icon_ids.get("cancel"),
                    )
                ],
            ]
        )
        content = texts.card_payment(
            payment, card_number, card_owner, self.settings.currency_label
        )
        if query:
            self._edit_or_send(query, content, markup)
        else:
            self.telegram.send_message(user["chat_id"], content, reply_markup=markup)

    def _begin_crypto_payment(
        self,
        user: dict[str, Any],
        order_id: int,
        *,
        query: dict[str, Any] | None,
    ) -> None:
        if not self._crypto_payment_available():
            raise ValidationError("پرداخت ارزی فعال و تنظیم‌شده نیست.")
        if not self._plisio:
            self.telegram.send_message(
                user["chat_id"], "پرداخت ارزی هنوز توسط مدیریت فعال نشده است."
            )
            return
        order = self.db.get_order(order_id)
        if not order or order["user_id"] != user["id"]:
            raise NotFoundError("سفارش پیدا نشد.")
        if order["status"] not in {"pending_payment", "awaiting_confirmation"}:
            raise ValidationError("این سفارش دیگر پرداخت جدید نمی‌پذیرد.")
        expires_at = parse_iso(order.get("expires_at"))
        if expires_at is not None and expires_at <= utc_now():
            raise ValidationError("مهلت پرداخت این سفارش تمام شده است.")
        remaining = int(order["payable_amount"]) - int(
            order.get("external_paid_amount") or 0
        )
        if remaining <= 0:
            raise ValidationError("مبلغ باقیمانده‌ای برای پرداخت وجود ندارد.")
        existing = self.db.find_active_order_payment(order_id)
        if (
            existing
            and existing.get("method") == "crypto"
            and existing.get("provider_invoice_id")
            and existing.get("provider_invoice_url")
        ):
            self._show_crypto_invoice(
                user,
                existing,
                str(existing.get("provider_invoice_url") or ""),
                query=query,
            )
            return
        if existing and existing.get("method") != "crypto":
            raise ValidationError(
                "یک پرداخت کارت فعال است؛ برای تغییر روش، آن پرداخت را لغو و سفارش تازه‌ای ایجاد کن."
            )
        if existing and (
            existing.get("provider_invoice_id")
            or existing.get("provider_invoice_url")
        ):
            raise ValidationError(
                "اطلاعات صورتحساب فعال ناقص است؛ برای پیگیری با پشتیبانی تماس بگیر."
            )
        if existing is None:
            existing = self.db.create_order_payment(
                order_id,
                "crypto",
                idempotency_key=(
                    f"order:{order_id}:crypto:provisional:{uuid.uuid4().hex}"
                ),
                requested_amount=remaining,
                unique_amount_window=0,
            )
        try:
            invoice = self._plisio.create_invoice(
                order_number=str(existing["payment_number"]),
                order_name=order["product_name_snapshot"],
                amount_in_shop_currency=int(existing["base_amount"]),
                expire_minutes=self.settings.order_expiry_minutes,
                description=f"Alone Account {order['order_number']}",
            )
        except PlisioError:
            LOG.exception("Could not create Plisio invoice")
            self.telegram.send_message(
                user["chat_id"], "ساخت پرداخت ارزی ناموفق بود؛ لطفاً کمی بعد دوباره تلاش کن."
            )
            return
        try:
            payment = self.db.attach_crypto_invoice(
                int(existing["id"]),
                int(user["id"]),
                invoice.transaction_id,
                invoice.invoice_url,
            )
        except DatabaseError:
            LOG.exception("Could not attach Plisio order invoice")
            self.telegram.send_message(
                user["chat_id"],
                "صورتحساب هنوز نهایی نشده است؛ پرداخت نکن و همین درخواست را "
                "کمی بعد دوباره امتحان کن.",
            )
            return
        self._show_crypto_invoice(
            user,
            payment,
            str(payment.get("provider_invoice_url") or invoice.invoice_url),
            query=query,
        )

    def _begin_crypto_topup(
        self,
        user: dict[str, Any],
        amount: int,
        *,
        query: dict[str, Any] | None,
    ) -> None:
        if not self._crypto_payment_available():
            raise ValidationError("پرداخت ارزی فعال و تنظیم‌شده نیست.")
        if not self._plisio:
            self.telegram.send_message(user["chat_id"], "پرداخت ارزی هنوز فعال نشده است.")
            return
        active_payments = self.db.list_active_wallet_topup_payments(int(user["id"]))
        active = next(
            (
                payment
                for payment in active_payments
                if payment.get("method") == "crypto"
            ),
            None,
        )
        if active_payments and (
            active is None or int(active["base_amount"]) != int(amount)
        ):
            raise ValidationError(
                "یک پرداخت افزایش موجودی فعال داری؛ تا مشخص‌شدن نتیجه آن "
                "نمی‌توانی روش یا مبلغ دیگری انتخاب کنی."
            )
        if active and active.get("provider_invoice_id") and active.get(
            "provider_invoice_url"
        ):
            self._show_crypto_invoice(
                user,
                active,
                str(active.get("provider_invoice_url") or ""),
                query=query,
            )
            return
        if active is None:
            active = self.db.create_wallet_topup_payment(
                user["id"],
                amount,
                "crypto",
                idempotency_key=(
                    f"topup:{user['id']}:crypto:provisional:{uuid.uuid4().hex}"
                ),
                currency="TOMAN",
                unique_amount_window=0,
            )
        elif active.get("provider_invoice_id") or active.get("provider_invoice_url"):
            raise ValidationError(
                "اطلاعات صورتحساب فعال ناقص است؛ برای پیگیری با پشتیبانی تماس بگیر."
            )
        try:
            invoice = self._plisio.create_invoice(
                order_number=str(active["payment_number"]),
                order_name="Wallet top-up",
                amount_in_shop_currency=amount,
                expire_minutes=self.settings.order_expiry_minutes,
            )
        except PlisioError:
            LOG.exception("Could not create Plisio top-up invoice")
            self.telegram.send_message(user["chat_id"], "ساخت پرداخت ارزی ناموفق بود.")
            return
        try:
            payment = self.db.attach_crypto_invoice(
                int(active["id"]),
                int(user["id"]),
                invoice.transaction_id,
                invoice.invoice_url,
            )
        except DatabaseError:
            LOG.exception("Could not attach Plisio top-up invoice")
            self.telegram.send_message(
                user["chat_id"],
                "صورتحساب هنوز نهایی نشده است؛ پرداخت نکن و همین درخواست را "
                "کمی بعد دوباره امتحان کن.",
            )
            return
        self._show_crypto_invoice(
            user,
            payment,
            str(payment.get("provider_invoice_url") or invoice.invoice_url),
            query=query,
        )

    def _show_crypto_invoice(
        self,
        user: dict[str, Any],
        payment: dict[str, Any],
        invoice_url: str,
        *,
        query: dict[str, Any] | None,
    ) -> None:
        if not is_safe_https_url(invoice_url):
            LOG.error(
                "Refusing unsafe provider invoice URL for payment %s",
                payment.get("id"),
            )
            content = (
                "لینک صورتحساب دریافتی معتبر نیست. پرداخت انجام نده و از مدیریت "
                "بخواه تنظیمات درگاه را بررسی کند."
            )
            markup = inline_keyboard([[back_button("menu")]])
            if query:
                self._edit_or_send(query, content, markup)
            else:
                self.telegram.send_message(
                    user["chat_id"], content, reply_markup=markup
                )
            return
        content = (
            "💰 <b>پرداخت ارزی</b>\n\n"
            f"مبلغ مبنا: {money(payment['base_amount'], self.settings.currency_label)}\n"
            "صورتحساب تا ۳۰ دقیقه معتبر است. پس از تأیید شبکه، نتیجه خودکار ثبت می‌شود."
        )
        markup = inline_keyboard(
            [
                [url_button("رفتن به صفحه پرداخت", invoice_url, style="success")],
                [
                    back_button(
                        f"order:{payment['order_id']}"
                        if payment.get("order_id") is not None
                        else "wallet"
                    )
                ],
            ]
        )
        if query:
            self._edit_or_send(query, content, markup)
        else:
            self.telegram.send_message(user["chat_id"], content, reply_markup=markup)

    def show_order(
        self,
        user: dict[str, Any],
        order_id: int,
        *,
        query: dict[str, Any] | None = None,
    ) -> None:
        order = self.db.get_order(order_id)
        if not order or order["user_id"] != user["id"]:
            raise NotFoundError("سفارش پیدا نشد.")
        view = self._order_view(order)
        lines = [
            "📦 <b>جزئیات سفارش</b>",
            "",
            f"شماره سفارش: <code>{escape(view['order_no'])}</code>",
            f"محصول: {escape(view['product_title'])}",
            f"مبلغ: {money(view['final_amount'], self.settings.currency_label)}",
            f"وضعیت: {escape(texts.STATUS_LABELS.get(order['status'], order['status']))}",
            f"تاریخ: {escape(str(order['created_at'])[:16])}",
        ]
        if order.get("admin_note"):
            lines.append(f"توضیح مدیریت: {escape(order['admin_note'])}")
        if order["status"] == "completed" and order.get("delivered_payload"):
            delivered = (
                f"<code>{escape(order['delivered_payload'])}</code>"
                if order.get("product_type_snapshot") == "ready"
                else render_rich_text(order["delivered_payload"])
            )
            lines.extend(
                (
                    "",
                    "<b>اطلاعات تحویل‌شده:</b>",
                    delivered,
                )
            )
            product = self.db.get_product(order["product_id"])
            if product and product.get("delivery_instructions"):
                lines.append(render_rich_text(product["delivery_instructions"]))
        rows: list[list[dict[str, Any]]] = []
        if order["status"] == "pending_payment":
            rows.append([callback_button("ادامه پرداخت", f"checkout:{order_id}", style="success")])
        elif order["status"] == "awaiting_confirmation":
            payment = self.db.latest_order_payment(order_id)
            if payment and payment["status"] in {"pending", "verifying"}:
                if payment.get("method") == "card":
                    rows.append(
                        [
                            callback_button(
                                "ارسال فیش واریز",
                                f"receipt:{payment['id']}",
                            )
                        ]
                    )
                elif payment.get("method") == "crypto":
                    invoice_url = str(payment.get("provider_invoice_url") or "")
                    if is_safe_https_url(invoice_url):
                        rows.append(
                            [
                                url_button(
                                    "ادامه پرداخت ارزی",
                                    invoice_url,
                                    style="success",
                                )
                            ]
                        )
                    elif not payment.get("provider_invoice_id") and not invoice_url:
                        lines.extend(
                            (
                                "",
                                "ساخت صورتحساب قبلی کامل نشده است؛ همان پرداخت "
                                "ارزی را دوباره امتحان کن.",
                            )
                        )
                        rows.append(
                            [
                                callback_button(
                                    "تلاش دوباره برای پرداخت ارزی",
                                    f"paycrypto:{order_id}",
                                    style="primary",
                                )
                            ]
                        )
                    else:
                        lines.extend(
                            (
                                "",
                                "لینک این پرداخت ارزی در دسترس نیست. "
                                "برای پیگیری با پشتیبانی تماس بگیر.",
                            )
                        )
                        rows.append(
                            [
                                callback_button(
                                    "پیگیری از پشتیبانی",
                                    "support",
                                    style="primary",
                                )
                            ]
                        )
        if order["status"] in {"awaiting_info", "processing"}:
            rows.append([callback_button("ارسال اطلاعات", f"orderinfo:{order_id}", style="primary")])
        rows.append([back_button("profile:orders")])
        markup = inline_keyboard(rows)
        content = "\n".join(lines)
        parts = split_telegram_html(content)
        if query:
            self._edit_or_send(query, parts[0], markup if len(parts) == 1 else None)
            for index, part in enumerate(parts[1:], start=1):
                self.telegram.send_message(
                    user["chat_id"],
                    part,
                    reply_markup=markup if index == len(parts) - 1 else None,
                )
        else:
            for index, part in enumerate(parts):
                self.telegram.send_message(
                    user["chat_id"],
                    part,
                    reply_markup=markup if index == len(parts) - 1 else None,
                )

    # -- conversational input ---------------------------------------------

    def _handle_state(
        self,
        message: dict[str, Any],
        user: dict[str, Any],
        admin: dict[str, Any] | None,
        state: dict[str, Any],
        update: dict[str, Any],
    ) -> bool:
        name = state.get("state")
        data = state.get("data") or state.get("data_json") or {}
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except ValueError:
                data = {}
        if not isinstance(data, Mapping):
            data = {}
        text = str(message.get("text") or message.get("caption") or "").strip()

        if name == "purchase_name":
            if not 3 <= len(text) <= 100:
                self.telegram.send_message(user["chat_id"], "نام و نام خانوادگی معتبر وارد کن.")
                return True
            product_id = self._state_id(data, "product_id")
            product = self.db.get_product(product_id) if product_id is not None else None
            if not self._product_is_browsable(product) or not product.get("is_available", 1):
                self._end_stale_state(user, "این محصول دیگر قابل خرید نیست.")
                return True
            user = self.db.update_user_profile(user["id"], customer_name=text)
            self.db.set_user_state(user["id"], "purchase_phone", data)
            self.telegram.send_message(
                user["chat_id"],
                texts.ASK_PHONE,
                reply_markup=contact_keyboard(
                    icon_custom_emoji_id=self.settings.button_icon_ids.get("phone")
                ),
            )
            return True

        if name == "purchase_phone":
            contact = message.get("contact") or {}
            try:
                contact_user_id = int(contact.get("user_id") or 0)
            except (TypeError, ValueError, OverflowError):
                contact_user_id = 0
            phone = self._contact_phone(contact.get("phone_number"))
            if (
                not contact
                or contact_user_id != int(user["telegram_user_id"])
                or phone is None
            ):
                self.telegram.send_message(
                    user["chat_id"],
                    "شماره معتبر را فقط با دکمه «ارسال شماره موبایل» و از حساب خودت بفرست.",
                    reply_markup=contact_keyboard(
                        icon_custom_emoji_id=self.settings.button_icon_ids.get("phone")
                    ),
                )
                return True
            product_id = self._state_id(data, "product_id")
            product = self.db.get_product(product_id) if product_id is not None else None
            if not self._product_is_browsable(product) or not product.get("is_available", 1):
                self._end_stale_state(user, "این محصول دیگر قابل خرید نیست.")
                return True
            user = self.db.update_user_profile(user["id"], phone=phone)
            self._create_order_and_confirm(
                user,
                product_id,
                update_id=data.get("update_id") or update.get("update_id"),
            )
            self.db.clear_user_state(user["id"])
            return True

        if name == "discount_code":
            order_id = self._state_id(data, "order_id")
            order = self.db.get_order(order_id) if order_id is not None else None
            if (
                not order
                or int(order["user_id"]) != int(user["id"])
                or order.get("status") != "pending_payment"
            ):
                self._end_stale_state(user, "این سفارش دیگر کد تخفیف نمی‌پذیرد.")
                return True
            try:
                order = self.db.apply_discount(order_id, text)
            except (NotFoundError, ValidationError, ConflictError):
                self.telegram.send_message(
                    user["chat_id"],
                    texts.INVALID_DISCOUNT,
                    reply_markup=inline_keyboard([[back_button(f"ordersummary:{order_id}")]]),
                )
                return True
            self.db.clear_user_state(user["id"])
            self.show_order_summary(user, order)
            return True

        if name == "wallet_topup_amount":
            normalized_amount = normalize_digits(text).strip()
            if not re.fullmatch(r"[0-9][0-9,\s٬]*", normalized_amount):
                self.telegram.send_message(user["chat_id"], "مبلغ را به‌صورت یک عدد مثبت وارد کن.")
                return True
            try:
                amount = parse_amount(normalized_amount)
            except ValueError:
                self.telegram.send_message(user["chat_id"], "مبلغ را به‌صورت یک عدد مثبت وارد کن.")
                return True
            minimum = int(self.db.get_setting("minimum_topup_amount", 10_000) or 10_000)
            maximum = int(self.db.get_setting("maximum_topup_amount", 100_000_000) or 100_000_000)
            if not minimum <= amount <= maximum:
                self.telegram.send_message(
                    user["chat_id"],
                    f"مبلغ باید بین {money(minimum, self.settings.currency_label)} و "
                    f"{money(maximum, self.settings.currency_label)} باشد.",
                )
                return True
            self.db.clear_user_state(user["id"])
            rows: list[list[dict[str, Any]]] = []
            if self._card_payment_available():
                rows.append([callback_button("کارت به کارت", f"topupcard:{amount}", style="primary")])
            if self._crypto_payment_available():
                rows.append([callback_button("پرداخت ارزی", f"topupcrypto:{amount}", style="primary")])
            rows.append([back_button("wallet")])
            self.telegram.send_message(
                user["chat_id"],
                f"روش پرداخت برای شارژ {money(amount, self.settings.currency_label)} را انتخاب کن:",
                reply_markup=inline_keyboard(rows),
            )
            return True

        if name == "payment_receipt":
            file_id, file_kind = self._attachment(message)
            if not file_id:
                self.telegram.send_message(user["chat_id"], "لطفاً تصویر یا فایل فیش را بفرست.")
                return True
            payment_id = self._state_id(data, "payment_id")
            payment = self.db.get_payment(payment_id) if payment_id is not None else None
            if not payment or payment["user_id"] != user["id"]:
                self._end_stale_state(user, "این پرداخت دیگر در دسترس نیست.")
                return True
            if payment.get("method") != "card":
                self._end_stale_state(
                    user, "ارسال فیش فقط برای پرداخت کارت‌به‌کارت مجاز است."
                )
                return True
            if payment["status"] not in {"pending", "verifying"}:
                self._end_stale_state(user, "این پرداخت دیگر فیش جدید نمی‌پذیرد.")
                return True
            expires_at = parse_iso(payment.get("expires_at"))
            if expires_at and expires_at <= utc_now() and not payment.get("receipt_file_id"):
                self.db.clear_user_state(user["id"])
                self.telegram.send_message(
                    user["chat_id"],
                    "مهلت این پرداخت تمام شده است؛ مبلغی واریز نکن.",
                    reply_markup=main_menu_keyboard(self.settings.button_icon_ids),
                )
                return True
            payment = self.db.submit_payment_receipt(
                payment_id, file_id, file_kind=file_kind
            )
            self.db.clear_user_state(user["id"])
            self.telegram.send_message(
                user["chat_id"],
                "✅ فیش دریافت شد و برای بررسی دستی به مدیریت ارسال شد.",
                reply_markup=main_menu_keyboard(self.settings.button_icon_ids),
            )
            approval = inline_keyboard(
                [
                    [callback_button("تأیید پرداخت", f"adm:payok:{payment_id}", style="success")],
                    [callback_button("رد پرداخت", f"adm:payno:{payment_id}", style="danger")],
                ]
            )
            self._alert_card_receipt(payment, user, reply_markup=approval)
            self._copy_to_admins(
                message,
                reply_markup=approval,
                allowed_roles={"owner", "admin"},
            )
            return True

        if name == "order_information":
            file_id, file_kind = self._attachment(message)
            if not text and not file_id:
                self.telegram.send_message(user["chat_id"], "اطلاعات را به‌صورت متن، تصویر یا فایل بفرست.")
                return True
            order_id = self._state_id(data, "order_id")
            order = self.db.get_order(order_id) if order_id is not None else None
            if (
                not order
                or order["user_id"] != user["id"]
                or order.get("status") not in {"awaiting_info", "processing"}
            ):
                self._end_stale_state(user, "این سفارش دیگر اطلاعات جدید نمی‌پذیرد.")
                return True
            payload = {
                "text": text,
                "file_id": file_id,
                "file_kind": file_kind,
                "telegram_message_id": message.get("message_id"),
                "updated_at": utc_now().isoformat(timespec="seconds"),
            }
            order = self.db.submit_manual_order_info(
                order_id,
                int(user["id"]),
                payload,
            )
            self.db.clear_user_state(user["id"])
            self.telegram.send_message(
                user["chat_id"],
                texts.information_saved(order["order_number"]),
                reply_markup=inline_keyboard(
                    [[callback_button("مشاهده سفارش", f"order:{order_id}")]]
                ),
            )
            self._alert_manual_order_info(
                order,
                user,
            )
            if file_id:
                self._copy_to_admins(
                    message,
                    reply_markup=inline_keyboard(
                        [
                            [
                                callback_button(
                                    "تکمیل سفارش",
                                    f"adm:complete:{order_id}",
                                    style="success",
                                )
                            ]
                        ]
                    ),
                    allowed_roles={"owner", "admin"},
                )
            return True

        if name == "ticket_subject":
            if not 3 <= len(text) <= 120:
                self.telegram.send_message(user["chat_id"], "موضوع باید بین ۳ تا ۱۲۰ نویسه باشد.")
                return True
            self.db.set_user_state(user["id"], "ticket_body", {"subject": text})
            self.telegram.send_message(user["chat_id"], "حالا شرح کامل درخواستت را بفرست.")
            return True

        if name == "ticket_body":
            file_id, attachment_kind = self._attachment(message)
            if not text and not file_id:
                self.telegram.send_message(user["chat_id"], "شرح یا فایل درخواست را بفرست.")
                return True
            subject = str(data.get("subject") or "").strip()
            if not 3 <= len(subject) <= 120:
                self._end_stale_state(user, "فرآیند ثبت تیکت منقضی شده است؛ دوباره تلاش کن.")
                return True
            ticket = self.db.create_ticket(
                user["id"],
                subject,
                text or "پیوست",
                attachment_file_id=file_id,
                attachment_kind=attachment_kind,
                idempotency_key=f"ticket:{user['id']}:{update.get('update_id')}",
            )
            self.db.clear_user_state(user["id"])
            self.telegram.send_message(
                user["chat_id"],
                f"✅ تیکت <code>{escape(ticket['ticket_number'])}</code> ثبت شد.",
                reply_markup=main_menu_keyboard(self.settings.button_icon_ids),
            )
            initial_messages = self.db.list_ticket_messages(
                int(ticket["id"]), limit=1
            )
            if initial_messages:
                self._alert_user_ticket_message(
                    initial_messages[0], ticket=ticket, user=user
                )
            if file_id:
                self._copy_to_admins(message)
            return True

        if name == "ticket_reply":
            file_id, attachment_kind = self._attachment(message)
            if not text and not file_id:
                self.telegram.send_message(user["chat_id"], "پیام یا فایل را ارسال کن.")
                return True
            ticket_id = self._state_id(data, "ticket_id")
            ticket = self.db.get_ticket(ticket_id) if ticket_id is not None else None
            if not ticket or ticket["user_id"] != user["id"] or ticket["status"] == "closed":
                self._end_stale_state(user, "تیکت باز پیدا نشد؛ پاسخ ارسال نشد.")
                return True
            ticket_message = self.db.add_ticket_message(
                ticket_id,
                text or "پیوست",
                sender_type="user",
                sender_id=user["id"],
                attachment_file_id=file_id,
                attachment_kind=attachment_kind,
                idempotency_key=f"ticket-reply:{update.get('update_id')}",
            )
            self.db.clear_user_state(user["id"])
            self.telegram.send_message(user["chat_id"], "✅ پاسخ شما ثبت شد.")
            self._alert_user_ticket_message(ticket_message, ticket=ticket, user=user)
            if file_id:
                self._copy_to_admins(message)
            return True
        self._end_stale_state(
            user,
            "فرآیند قبلی دیگر معتبر نیست؛ از منو دوباره شروع کن.",
        )
        return True

    @staticmethod
    def _attachment(message: dict[str, Any]) -> tuple[str | None, str | None]:
        if photos := message.get("photo"):
            return str(photos[-1]["file_id"]), "photo"
        if document := message.get("document"):
            return str(document["file_id"]), "document"
        return None, None

    # -- payment completion and fulfilment --------------------------------

    def confirm_card_amount(
        self,
        amount: int,
        reference: str | None,
        occurred_at: str | None,
    ) -> ConfirmationOutcome:
        if not reference or not occurred_at:
            return ConfirmationOutcome.CONFLICT
        reference = reference.strip()
        if not reference:
            return ConfirmationOutcome.CONFLICT
        try:
            raw_occurred = datetime.fromisoformat(
                occurred_at[:-1] + "+00:00" if occurred_at.endswith("Z") else occurred_at
            )
        except (TypeError, ValueError):
            return ConfirmationOutcome.CONFLICT
        if raw_occurred.utcoffset() is None:
            return ConfirmationOutcome.CONFLICT
        occurred = parse_iso(occurred_at)
        if occurred is None:  # pragma: no cover - guarded by parsing above.
            return ConfirmationOutcome.CONFLICT
        raw_payload = {
            "amount": int(amount),
            "reference": reference,
            "occurred_at": occurred_at,
        }

        def matches_intent(candidate: Mapping[str, Any]) -> bool:
            created = parse_iso(candidate.get("created_at"))
            expires_at = parse_iso(candidate.get("expires_at"))
            return bool(
                int(candidate.get("payable_amount") or 0) == int(amount)
                and created is not None
                and expires_at is not None
                # The bank event must strictly follow this exact intent;
                # equal amounts may be reused after cancellation/expiry and
                # second-level timestamps would otherwise be ambiguous.
                and occurred > created
                and occurred <= expires_at
                and occurred <= utc_now() + timedelta(minutes=5)
            )

        def historical_payment_id() -> int | None:
            candidates = [
                candidate
                for candidate in self.db.find_historical_card_payment_candidates(
                    amount, occurred, currency="TOMAN", limit=2
                )
                if matches_intent(candidate)
            ]
            return int(candidates[0]["id"]) if len(candidates) == 1 else None

        # The HTTP listener is threaded. Serialising matching ensures two bank
        # events can never race for the same amount slot in this process; the
        # persistent reference/event ledgers preserve the guarantee on restart.
        with self._card_confirmation_lock:
            previous = self.db.get_payment_by_external_reference(reference)
            if previous:
                if previous["status"] != "paid" or not matches_intent(previous):
                    return ConfirmationOutcome.CONFLICT
                try:
                    # Older releases could commit the paid payment just before
                    # crashing on the separate event insert.  This idempotent
                    # call validates the original callback terms and backfills
                    # the missing ledger row on one SQLite transaction.
                    self.db.mark_payment_paid(
                        int(previous["id"]),
                        external_reference=reference,
                        raw_payload=raw_payload,
                        card_event_amount=amount,
                        card_event_occurred_at=occurred_at,
                    )
                except (ConflictError, ValidationError):
                    return ConfirmationOutcome.CONFLICT
                return ConfirmationOutcome.ALREADY_CONFIRMED
            prior_event = self.db.get_card_payment_event(reference)
            if prior_event:
                if prior_event["status"] == "review":
                    self._alert_card_payment_review(prior_event)
                    return ConfirmationOutcome.CONFLICT
                if prior_event["status"] != "confirmed" or prior_event["payment_id"] is None:
                    return ConfirmationOutcome.CONFLICT
                try:
                    self.db.record_card_payment_event(
                        reference,
                        amount,
                        occurred_at,
                        "confirmed",
                        payment_id=int(prior_event["payment_id"]),
                        raw_payload=raw_payload,
                    )
                except (ConflictError, ValidationError):
                    return ConfirmationOutcome.CONFLICT
                return ConfirmationOutcome.ALREADY_CONFIRMED

            payment = self.db.find_pending_payment_by_amount(
                amount, method="card", currency="TOMAN"
            )
            if not payment:
                try:
                    review = self.db.record_card_payment_event(
                        reference,
                        amount,
                        occurred_at,
                        "review",
                        payment_id=historical_payment_id(),
                        raw_payload=raw_payload,
                    )
                except ConflictError:
                    return ConfirmationOutcome.CONFLICT
                self._alert_card_payment_review(review)
                return ConfirmationOutcome.NOT_FOUND
            if not matches_intent(payment):
                try:
                    review = self.db.record_card_payment_event(
                        reference,
                        amount,
                        occurred_at,
                        "review",
                        payment_id=historical_payment_id(),
                        raw_payload=raw_payload,
                    )
                except ConflictError:
                    pass
                else:
                    self._alert_card_payment_review(review)
                return ConfirmationOutcome.CONFLICT
            try:
                self._complete_payment(
                    payment["id"],
                    external_reference=reference,
                    raw_payload=raw_payload,
                    card_event_amount=amount,
                    card_event_occurred_at=occurred_at,
                )
            except ConflictError:
                try:
                    review = self.db.record_card_payment_event(
                        reference,
                        amount,
                        occurred_at,
                        "review",
                        payment_id=historical_payment_id(),
                        raw_payload=raw_payload,
                    )
                except ConflictError:
                    pass
                else:
                    self._alert_card_payment_review(review)
                return ConfirmationOutcome.CONFLICT
            return ConfirmationOutcome.CONFIRMED

    def _complete_payment(
        self,
        payment_id: int,
        *,
        external_reference: str | None = None,
        raw_payload: dict[str, Any] | None = None,
        card_event_amount: int | None = None,
        card_event_occurred_at: str | None = None,
    ) -> bool:
        before = self.db.get_payment(payment_id)
        if not before:
            raise NotFoundError("پرداخت پیدا نشد.")
        if before["status"] == "paid":
            if card_event_amount is not None or card_event_occurred_at is not None:
                self.db.mark_payment_paid(
                    payment_id,
                    external_reference=external_reference,
                    raw_payload=raw_payload,
                    card_event_amount=card_event_amount,
                    card_event_occurred_at=card_event_occurred_at,
                )
            return False
        success_body: str | None = None
        success_key: str | None = None
        if before.get("purpose") == "order" and before.get("order_id") is not None:
            pending_order = self.db.get_order(int(before["order_id"]))
            if pending_order is not None:
                external_method = (
                    "کارت به کارت" if before["method"] == "card" else "پرداخت ارزی"
                )
                if int(pending_order.get("wallet_held_amount") or 0) > 0:
                    external_method = f"کیف پول + {external_method}"
                success_body = texts.payment_success(
                    self._order_view(pending_order),
                    int(pending_order["subtotal_amount"])
                    - int(pending_order["discount_amount"]),
                    external_method,
                    self.settings.currency_label,
                )
                success_key = f"payment:{int(before['id'])}:order-confirmed"
        payment = self.db.mark_payment_paid(
            payment_id,
            external_reference=external_reference,
            raw_payload=raw_payload,
            card_event_amount=card_event_amount,
            card_event_occurred_at=card_event_occurred_at,
            outbound_body=success_body,
            outbound_idempotency_key=success_key,
        )
        user = self.db.get_user(payment["user_id"])
        if not user:
            return True
        if payment["purpose"] == "wallet_topup":
            balance = self.db.wallet_balance(user["id"])
            self._notify_user_durable(
                user,
                "✅ <b>شارژ کیف پول تأیید شد</b>\n\n"
                f"مبلغ: {money(payment['base_amount'], self.settings.currency_label)}\n"
                f"موجودی جدید: {money(balance, self.settings.currency_label)}",
                idempotency_key=f"payment:{payment['id']}:topup-confirmed",
                reply_markup=main_menu_keyboard(self.settings.button_icon_ids),
            )
            return True
        order = self.db.get_order(payment["order_id"])
        if not order:
            return True
        if order["status"] == "paid":
            external_method = (
                "کارت به کارت" if payment["method"] == "card" else "پرداخت ارزی"
            )
            if int(order.get("wallet_captured_amount") or 0) > 0:
                external_method = f"کیف پول + {external_method}"
            self._notify_user_durable(
                user,
                texts.payment_success(
                    self._order_view(order),
                    int(order["subtotal_amount"]) - int(order["discount_amount"]),
                    external_method,
                    self.settings.currency_label,
                ),
                idempotency_key=f"payment:{payment['id']}:order-confirmed",
            )
            # The canonical success notice is committed with the payment and
            # attempted before any delivery/reservation/manual-info branch.
            # Fulfilment remains independently recoverable if Telegram fails.
            self._after_order_paid(order["id"])
        return True

    def _after_order_paid(self, order_id: int) -> None:
        if hasattr(self.db, "order_success_notice_ready") and not (
            self.db.order_success_notice_ready(int(order_id))
        ):
            LOG.info(
                "Deferring order %s while its success notice retry remains active",
                order_id,
            )
            return
        try:
            self._reconcile_purchase_rewards(order_id)
        except Exception:
            LOG.exception("Could not grant referral reward for order %s", order_id)
        self.fulfill_order(order_id)

    def _reconcile_purchase_rewards(self, order_id: int) -> None:
        """Idempotently grant every purchase reward and enqueue its notice."""

        if not hasattr(self.db, "grant_purchase_rewards"):
            return
        rewards = self.db.grant_purchase_rewards(order_id)
        for reward in rewards or []:
            if self.stop_event.is_set():
                return
            reward_user = self.db.get_user(reward["user_id"])
            if reward_user:
                # Existing rewards are intentionally revisited. The durable
                # idempotency key prevents duplicate Telegram messages while
                # recovering a crash after the wallet credit was committed but
                # before its notification was queued.
                notice_text = (
                    f"🎁 {money(reward['amount'], self.settings.currency_label)} "
                    "پاداش دعوت به کیف پولت اضافه شد."
                )
                notice_key = f"reward:{reward['id']}:notice"
                delivered = self._notify_user_durable(
                    reward_user,
                    notice_text,
                    idempotency_key=notice_key,
                )
                if not delivered:
                    # A Telegram send may fail after the outbox row has been
                    # committed; that is still safe because the retry worker
                    # owns delivery.  A queue/database failure is different:
                    # leave the order unmarked so maintenance can reconcile it.
                    if not hasattr(
                        self.db, "get_outbound_message_by_idempotency_key"
                    ):
                        return
                    try:
                        queued_notice = (
                            self.db.get_outbound_message_by_idempotency_key(notice_key)
                        )
                    except DatabaseError:
                        return
                    if not queued_notice or (
                        queued_notice.get("recipient_user_id")
                        != int(reward_user["id"])
                        or queued_notice.get("body") != notice_text
                    ):
                        return
        if hasattr(self.db, "mark_order_rewards_processed"):
            self.db.mark_order_rewards_processed(order_id)

    def fulfill_order(
        self, order_id: int | Mapping[str, Any]
    ) -> dict[str, Any] | None:
        if isinstance(order_id, Mapping):
            order_id = int(order_id["id"])
        order = self.db.get_order(order_id)
        if not order:
            return None
        user = self.db.get_user(order["user_id"])
        product = self.db.get_product(order["product_id"])
        if not user or not product:
            return None
        if order["status"] == "completed":
            return order
        if order["status"] not in {"paid", "processing", "awaiting_stock", "awaiting_info"}:
            return order
        # A paid commercial order must not reach any delivery/reservation/info
        # mutation while its canonical payment-success notice still has an
        # active retry.  Keep this invariant at the fulfilment boundary too,
        # so future callers cannot bypass the controller-level ordering gate.
        # Terminal outbox failure is deliberately considered ready by the DB:
        # an unreachable Telegram chat must never strand a paid purchase.
        if order["status"] == "paid" and hasattr(
            self.db, "order_success_notice_ready"
        ) and not self.db.order_success_notice_ready(int(order_id)):
            return order

        if order["product_type_snapshot"] == "ready":
            try:
                item = self.db.assign_inventory(order_id)
            except OutOfStockError:
                if product.get("reserve_enabled"):
                    self.db.reserve_product(
                        order["user_id"], order["product_id"], order_id=order_id
                    )
                    order = self.db.get_order(order_id) or order
                    self._notify_user_durable(
                        user,
                        texts.reserved_delivery(self._order_view(order)),
                        idempotency_key=f"order:{order_id}:reserved-notice",
                        reply_markup=inline_keyboard(
                            [[callback_button("مشاهده سفارش", f"order:{order_id}")]]
                        ),
                    )
                    return order
                processing = self.db.mark_ready_order_processing(
                    order_id,
                    admin_note="پرداخت تأیید شد؛ موجودی نیازمند تأمین دستی است.",
                )
                self._alert_ready_stock_processing(processing, user)
                return processing
            delivered = self.db.get_order(order_id) or order
            self._notify_user_durable(
                user,
                texts.ready_delivery(
                    self._order_view(delivered),
                    item["payload"],
                    product.get("delivery_instructions") or "",
                ),
                idempotency_key=f"order:{order_id}:delivery",
            )
            return delivered

        if order["status"] == "paid":
            order = self.db.update_order_status(order_id, "awaiting_info")
        prompt = product.get("info_request_text") or "اطلاعات لازم برای فعال‌سازی را ارسال کن."
        self._notify_user_durable(
            user,
            texts.needs_information(self._order_view(order), prompt),
            idempotency_key=f"order:{order_id}:info-request",
            reply_markup=inline_keyboard(
                [[callback_button("ارسال اطلاعات", f"orderinfo:{order_id}", style="primary")]]
            ),
        )
        self._notify_admins(
            f"📋 سفارش دستی <code>{escape(order['order_number'])}</code> پرداخت شد و منتظر اطلاعات است."
        )
        return order

    # -- referral, maintenance and notifications --------------------------

    def _record_referral(self, invitee: dict[str, Any], inviter_telegram_id: int) -> None:
        inviter = self.db.get_user_by_telegram_id(inviter_telegram_id)
        if not inviter or inviter["id"] == invitee["id"]:
            return
        try:
            self.db.record_referral(inviter["id"], invitee["id"])
        except (ConflictError, ValidationError):
            return

    def _grant_start_referral_reward(self, invitee: Mapping[str, Any]) -> None:
        if not hasattr(self.db, "grant_start_rewards") or not hasattr(
            self.db, "get_referral_by_invitee"
        ):
            return
        referral = self.db.get_referral_by_invitee(int(invitee["id"]))
        if not referral:
            return
        rewards = self.db.grant_start_rewards(int(referral["id"]))
        inviter = self.db.get_user(int(referral["inviter_user_id"]))
        if not inviter:
            return
        for reward in rewards or []:
            self._notify_user_durable(
                inviter,
                f"🎁 {money(reward['amount'], self.settings.currency_label)} پاداش دعوت به کیف پولت اضافه شد.",
                idempotency_key=f"reward:{reward['id']}:notice",
            )

    def run_maintenance(self) -> None:
        if self.stop_event.is_set():
            return
        self._reconcile_completed_provider_events()
        if self.stop_event.is_set():
            return
        # Provider invoices cannot be revoked locally. Reconcile them before
        # any local deadline sweep, and keep unresolved crypto rows active so
        # a delayed provider response can still settle the exact intent.
        self._poll_crypto_payments()
        if self.stop_event.is_set():
            return
        self._reconcile_payment_review_alerts()
        if self.stop_event.is_set():
            return
        self._reconcile_provider_review_resolution_notices()
        if self.stop_event.is_set():
            return
        self._reconcile_card_review_resolution_notices()
        if self.stop_event.is_set():
            return
        self._reconcile_card_receipt_alerts()
        if self.stop_event.is_set():
            return
        self._reconcile_manual_order_info_alerts()
        if self.stop_event.is_set():
            return
        self._reconcile_ready_stock_alerts()
        if self.stop_event.is_set():
            return
        self._reconcile_reward_notices()
        if self.stop_event.is_set():
            return
        self._reconcile_ticket_admin_alerts()
        if self.stop_event.is_set():
            return
        self._reconcile_payment_security_alerts()
        if self.stop_event.is_set():
            return
        for order_id in self.db.expire_unpaid_orders(limit=500):
            if self.stop_event.is_set():
                return
            order = self.db.get_order(order_id)
            if not order:
                continue
            user = self.db.get_user(order["user_id"])
            if user:
                self._notify_user_durable(
                    user,
                    texts.order_expired(order["order_number"]),
                    idempotency_key=f"order:{order_id}:expired-notice",
                )

        if self.stop_event.is_set():
            return
        self._reconcile_expired_order_notices()

        if self.stop_event.is_set():
            return
        if hasattr(self.db, "expire_pending_payments"):
            self.db.expire_pending_payments(limit=500)
        for stage in (
            self._reconcile_expired_wallet_topup_notices,
            self._reconcile_paid_payment_notices,
            self._reconcile_zero_external_payment_notices,
            self._reconcile_paid_orders,
            self._reconcile_paid_fulfillment,
            self._reconcile_pending_order_notices,
            self._fulfill_reserved_inventory,
            self._fulfill_processing_ready_inventory,
            self._reconcile_completed_deliveries,
            self._deliver_due_reminders,
            self._deliver_outbound_messages,
            self._report_completed_broadcasts,
        ):
            if self.stop_event.is_set():
                return
            stage()

    def _reconcile_paid_orders(self) -> None:
        """Recover a bounded batch of reward/fulfilment work for paid orders.

        Reward processing can commit one event type and then fail before the
        remaining event types are granted. Fulfilment must still proceed, so a
        durable completion marker selects only unfinished successful orders on
        later maintenance passes. A durable keyset cursor rotates past a
        repeatedly failing row without skipping it permanently. All
        writes/notices use stable idempotency keys.
        """

        limit = max(1, int(self.MAINTENANCE_REWARD_RECONCILE_LIMIT))
        snapshot: list[dict[str, Any]] = []
        if hasattr(self.db, "list_orders_pending_reward_processing"):
            try:
                cursor = max(
                    0,
                    int(
                        self.db.get_setting(
                            self._REWARD_RECONCILE_CURSOR_SETTING,
                            0,
                        )
                    ),
                )
            except (TypeError, ValueError):
                cursor = 0
            snapshot = self.db.list_orders_pending_reward_processing(
                limit=limit,
                after_id=cursor,
            )
            if not snapshot and cursor:
                # Wrap after reaching the end so earlier failures are retried.
                cursor = 0
                snapshot = self.db.list_orders_pending_reward_processing(
                    limit=limit,
                    after_id=0,
                )
        else:  # pragma: no cover - compatibility with lightweight DB doubles.
            for status in (
                "paid",
                "awaiting_stock",
                "awaiting_info",
                "processing",
                "completed",
            ):
                remaining = limit - len(snapshot)
                if remaining <= 0:
                    break
                snapshot.extend(self.db.list_orders(status=status, limit=remaining))

        last_processed_id: int | None = None
        for order in snapshot:
            if self.stop_event.is_set():
                break
            order_id = int(order["id"])
            try:
                if order.get("status") == "paid":
                    self._after_order_paid(order_id)
                else:
                    self._reconcile_purchase_rewards(order_id)
            except Exception:
                LOG.exception("Could not reconcile paid order %s", order_id)
            last_processed_id = order_id
        if last_processed_id is not None:
            self.db.set_setting(
                self._REWARD_RECONCILE_CURSOR_SETTING,
                last_processed_id,
            )
        elif not self.stop_event.is_set() and hasattr(self.db, "set_setting"):
            self.db.set_setting(self._REWARD_RECONCILE_CURSOR_SETTING, 0)

    def _reconcile_paid_fulfillment(self) -> None:
        """Recover paid orders independently of the referral reward marker."""

        if not hasattr(self.db, "list_paid_orders_pending_fulfillment"):
            return
        try:
            cursor = max(
                0,
                int(
                    self.db.get_setting(
                        self._FULFILLMENT_RECONCILE_CURSOR_SETTING, 0
                    )
                ),
            )
        except (TypeError, ValueError):
            cursor = 0
        orders = self.db.list_paid_orders_pending_fulfillment(
            limit=max(1, int(self.MAINTENANCE_FULFILLMENT_RECONCILE_LIMIT)),
            after_id=cursor,
        )
        if not orders and cursor:
            cursor = 0
            orders = self.db.list_paid_orders_pending_fulfillment(
                limit=max(1, int(self.MAINTENANCE_FULFILLMENT_RECONCILE_LIMIT)),
                after_id=0,
            )
        for order in orders:
            if self.stop_event.is_set():
                return
            order_id = int(order["id"])
            try:
                if self.db.order_success_notice_ready(order_id):
                    self.fulfill_order(order_id)
            except Exception:
                LOG.exception("Could not reconcile paid-order fulfilment %s", order_id)
            cursor = order_id
        self.db.set_setting(
            self._FULFILLMENT_RECONCILE_CURSOR_SETTING,
            cursor if orders else 0,
        )

    def _reconcile_paid_payment_notices(self) -> None:
        if not hasattr(self.db, "list_paid_payments_missing_notice"):
            return
        try:
            cursor = max(
                0,
                int(self.db.get_setting(self._PAID_NOTICE_CURSOR_SETTING, 0)),
            )
        except (TypeError, ValueError):
            cursor = 0
        payments = self.db.list_paid_payments_missing_notice(
            limit=100, after_id=cursor
        )
        if not payments and cursor:
            cursor = 0
            payments = self.db.list_paid_payments_missing_notice(
                limit=100, after_id=0
            )
        for payment in payments:
            if self.stop_event.is_set():
                return
            user = self.db.get_user(int(payment["user_id"]))
            if user is None:
                cursor = int(payment["id"])
                continue
            if payment["purpose"] == "wallet_topup":
                balance = self.db.wallet_balance(int(user["id"]))
                self._notify_user_durable(
                    user,
                    "✅ <b>شارژ کیف پول تأیید شد</b>\n\n"
                    f"مبلغ: {money(payment['base_amount'], self.settings.currency_label)}\n"
                    f"موجودی جدید: {money(balance, self.settings.currency_label)}",
                    idempotency_key=f"payment:{int(payment['id'])}:topup-confirmed",
                    reply_markup=main_menu_keyboard(self.settings.button_icon_ids),
                )
            elif payment.get("provider_wallet_credit"):
                balance = self.db.wallet_balance(int(user["id"]))
                self._notify_user_durable(
                    user,
                    "✅ <b>پرداخت ارزی قطعی تعیین تکلیف شد</b>\n\n"
                    "سفارش پایان‌یافته دوباره فعال نشد و مبلغ پرداخت "
                    "دقیقاً یک‌بار به کیف پولت اضافه شد."
                    f"\nمبلغ: {money(payment['base_amount'], self.settings.currency_label)}"
                    f"\nموجودی جدید: {money(balance, self.settings.currency_label)}",
                    idempotency_key=(
                        f"payment:{int(payment['id'])}:provider-wallet-credit"
                    ),
                    reply_markup=main_menu_keyboard(self.settings.button_icon_ids),
                )
            elif payment.get("order_id") is not None:
                order = self.db.get_order(int(payment["order_id"]))
                if order is not None:
                    external_method = (
                        "کارت به کارت"
                        if payment["method"] == "card"
                        else "پرداخت ارزی"
                    )
                    if int(order.get("wallet_captured_amount") or 0) > 0:
                        external_method = f"کیف پول + {external_method}"
                    self._notify_user_durable(
                        user,
                        texts.payment_success(
                            self._order_view(order),
                            int(order["subtotal_amount"])
                            - int(order["discount_amount"]),
                            external_method,
                            self.settings.currency_label,
                        ),
                        idempotency_key=f"payment:{int(payment['id'])}:order-confirmed",
                    )
            cursor = int(payment["id"])
        self.db.set_setting(
            self._PAID_NOTICE_CURSOR_SETTING, cursor if payments else 0
        )

    def _reconcile_expired_wallet_topup_notices(self) -> None:
        """Notify owners of expired top-up bills with a restart-safe cursor."""
        try:
            cursor = max(
                0, int(self.db.get_setting(self._EXPIRED_TOPUP_NOTICE_CURSOR_SETTING, 0))
            )
        except (TypeError, ValueError):
            cursor = 0
        payments = self.db.list_expired_wallet_topups_missing_notice(
            limit=max(1, int(self.MAINTENANCE_EXPIRED_TOPUP_NOTICE_LIMIT)), after_id=cursor
        )
        if not payments and cursor:
            payments = self.db.list_expired_wallet_topups_missing_notice(
                limit=max(1, int(self.MAINTENANCE_EXPIRED_TOPUP_NOTICE_LIMIT)), after_id=0
            )
        for payment in payments:
            if self.stop_event.is_set():
                return
            user = self.db.get_user(int(payment["user_id"]))
            if user is not None:
                self._notify_user_durable(
                    user,
                    texts.wallet_topup_expired(payment, self.settings.currency_label),
                    idempotency_key=f"payment:{int(payment['id'])}:topup-expired",
                    reply_markup=inline_keyboard([[callback_button("کیف پول", "wallet")]]),
                )
            cursor = int(payment["id"])
        self.db.set_setting(
            self._EXPIRED_TOPUP_NOTICE_CURSOR_SETTING, cursor if payments else 0
        )

    def _reconcile_zero_external_payment_notices(self) -> None:
        """Recover wallet-only, full-discount, and free-checkout success notices."""

        if not hasattr(self.db, "list_zero_external_paid_orders_missing_notice"):
            return
        try:
            cursor = max(
                0,
                int(self.db.get_setting(self._ZERO_EXTERNAL_NOTICE_CURSOR_SETTING, 0)),
            )
        except (TypeError, ValueError):
            cursor = 0
        orders = self.db.list_zero_external_paid_orders_missing_notice(
            limit=max(1, int(self.MAINTENANCE_ZERO_EXTERNAL_NOTICE_LIMIT)),
            after_id=cursor,
        )
        if not orders and cursor:
            cursor = 0
            orders = self.db.list_zero_external_paid_orders_missing_notice(
                limit=max(1, int(self.MAINTENANCE_ZERO_EXTERNAL_NOTICE_LIMIT)),
                after_id=0,
            )
        for order in orders:
            if self.stop_event.is_set():
                return
            user = self.db.get_user(int(order["user_id"]))
            if user is not None:
                success_kind = str(order["success_kind"])
                method = {
                    "discount": "تخفیف کامل",
                    "free": "سفارش رایگان",
                    "wallet": "کیف پول",
                }[success_kind]
                self._notify_user_durable(
                    user,
                    texts.payment_success(
                        self._order_view(order),
                        0
                        if success_kind in {"discount", "free"}
                        else int(order["wallet_captured_amount"]),
                        method,
                        self.settings.currency_label,
                    ),
                    idempotency_key=(
                        f"order:{int(order['id'])}:"
                        f"{success_kind}-confirmed"
                    ),
                )
            cursor = int(order["id"])
        self.db.set_setting(
            self._ZERO_EXTERNAL_NOTICE_CURSOR_SETTING,
            cursor if orders else 0,
        )

    def _reconcile_pending_order_notices(self) -> None:
        """Recover the user prompt if a crash followed an order transition."""

        reserved, reserved_cursor = self._missing_order_notice_batch(
            "reserved", self._RESERVED_NOTICE_CURSOR_SETTING
        )
        for order in reserved:
            if self.stop_event.is_set():
                return
            user = self.db.get_user(int(order["user_id"]))
            if not user:
                reserved_cursor = int(order["id"])
                continue
            self._notify_user_durable(
                user,
                texts.reserved_delivery(self._order_view(order)),
                idempotency_key=f"order:{order['id']}:reserved-notice",
                reply_markup=inline_keyboard(
                    [[callback_button("مشاهده سفارش", f"order:{order['id']}")]]
                ),
            )
            reserved_cursor = int(order["id"])
        self.db.set_setting(
            self._RESERVED_NOTICE_CURSOR_SETTING,
            reserved_cursor if reserved else 0,
        )

        information, information_cursor = self._missing_order_notice_batch(
            "information", self._INFO_NOTICE_CURSOR_SETTING
        )
        for order in information:
            if self.stop_event.is_set():
                return
            user = self.db.get_user(int(order["user_id"]))
            product = self.db.get_product(int(order["product_id"]))
            if not user or not product:
                information_cursor = int(order["id"])
                continue
            prompt = product.get("info_request_text") or "اطلاعات لازم برای فعال‌سازی را ارسال کن."
            self._notify_user_durable(
                user,
                texts.needs_information(self._order_view(order), prompt),
                idempotency_key=f"order:{order['id']}:info-request",
                reply_markup=inline_keyboard(
                    [
                        [
                            callback_button(
                                "ارسال اطلاعات",
                                f"orderinfo:{order['id']}",
                                style="primary",
                            )
                        ]
                    ]
                ),
            )
            information_cursor = int(order["id"])
        self.db.set_setting(
            self._INFO_NOTICE_CURSOR_SETTING,
            information_cursor if information else 0,
        )

    def _missing_order_notice_batch(
        self,
        notice_kind: str,
        cursor_setting: str,
    ) -> tuple[list[dict[str, Any]], int]:
        try:
            cursor = max(0, int(self.db.get_setting(cursor_setting, 0)))
        except (TypeError, ValueError):
            cursor = 0
        orders = self.db.list_orders_missing_notice(
            notice_kind,
            limit=max(1, int(self.MAINTENANCE_ORDER_NOTICE_RECONCILE_LIMIT)),
            after_id=cursor,
        )
        if not orders and cursor:
            cursor = 0
            orders = self.db.list_orders_missing_notice(
                notice_kind,
                limit=max(1, int(self.MAINTENANCE_ORDER_NOTICE_RECONCILE_LIMIT)),
                after_id=0,
            )
        return orders, cursor

    def _reconcile_expired_order_notices(self) -> None:
        """Recover the warning after a crash immediately following expiry."""

        orders, cursor = self._missing_order_notice_batch(
            "expired", self._EXPIRED_NOTICE_CURSOR_SETTING
        )
        for order in orders:
            if self.stop_event.is_set():
                return
            user = self.db.get_user(int(order["user_id"]))
            if user:
                self._notify_user_durable(
                    user,
                    texts.order_expired(str(order["order_number"])),
                    idempotency_key=f"order:{int(order['id'])}:expired-notice",
                )
            cursor = int(order["id"])
        self.db.set_setting(
            self._EXPIRED_NOTICE_CURSOR_SETTING,
            cursor if orders else 0,
        )

    def _fulfill_reserved_inventory(self) -> None:
        """Deliver a bounded FIFO batch of newly stocked reservations."""

        limit = max(1, int(self.MAINTENANCE_RESERVED_FULFILLMENT_LIMIT))
        for _ in range(limit):
            if self.stop_event.is_set():
                return
            fulfilled = self.db.fulfill_next_available_reservation()
            if not fulfilled:
                break
            order = self.db.get_order(fulfilled["order_id"])
            item = fulfilled.get("inventory_item") or {}
            user = self.db.get_user(fulfilled["user_id"])
            product = self.db.get_product(fulfilled["product_id"])
            if not order or not user or not product or not item.get("payload"):
                LOG.error(
                    "A fulfilled reservation %s is missing delivery data",
                    fulfilled.get("id"),
                )
                continue
            self._notify_user_durable(
                user,
                texts.ready_delivery(
                    self._order_view(order),
                    item["payload"],
                    product.get("delivery_instructions") or "",
                ),
                idempotency_key=f"order:{order['id']}:delivery",
            )

    def _fulfill_processing_ready_inventory(self) -> None:
        """Recover paid non-reserved ready orders after stock is replenished."""

        limit = max(1, int(self.MAINTENANCE_PROCESSING_READY_FULFILLMENT_LIMIT))
        for _ in range(limit):
            if self.stop_event.is_set():
                return
            fulfilled = self.db.fulfill_next_processing_ready_order()
            if not fulfilled:
                break
            item = fulfilled.get("inventory_item") or {}
            user = self.db.get_user(int(fulfilled["user_id"]))
            product = self.db.get_product(int(fulfilled["product_id"]))
            if not user or not product or not item.get("payload"):
                LOG.error(
                    "A processing ready order %s is missing delivery data",
                    fulfilled.get("id"),
                )
                continue
            self._notify_user_durable(
                user,
                texts.ready_delivery(
                    self._order_view(fulfilled),
                    str(item["payload"]),
                    product.get("delivery_instructions") or "",
                ),
                idempotency_key=f"order:{fulfilled['id']}:delivery",
            )

    def _reconcile_completed_deliveries(self) -> None:
        """Create/send a missing durable delivery after a crash post-assignment."""

        orders, cursor = self._missing_order_notice_batch(
            "delivery", self._DELIVERY_NOTICE_CURSOR_SETTING
        )
        for order in orders:
            if self.stop_event.is_set():
                return
            user = self.db.get_user(int(order["user_id"]))
            product = self.db.get_product(int(order["product_id"]))
            if not user or not product:
                cursor = int(order["id"])
                continue
            self._notify_user_durable(
                user,
                texts.ready_delivery(
                    self._order_view(order),
                    str(order["delivered_payload"]),
                    product.get("delivery_instructions") or "",
                ),
                idempotency_key=f"order:{order['id']}:delivery",
            )
            cursor = int(order["id"])
        self.db.set_setting(
            self._DELIVERY_NOTICE_CURSOR_SETTING,
            cursor if orders else 0,
        )

    def _poll_crypto_payments(self) -> None:
        if not self._plisio or not hasattr(self.db, "list_pending_provider_payments"):
            return
        try:
            cursor = max(
                0,
                int(self.db.get_setting(self._CRYPTO_POLL_CURSOR_SETTING, 0)),
            )
        except (TypeError, ValueError):
            cursor = 0
        payments = self.db.list_pending_provider_payments(
            method="crypto", limit=50, after_id=cursor
        )
        if not payments and cursor:
            cursor = 0
            payments = self.db.list_pending_provider_payments(
                method="crypto", limit=50, after_id=0
            )
        last_processed_id: int | None = None
        for payment in payments:
            if self.stop_event.is_set():
                break
            last_processed_id = int(payment["id"])
            transaction_id = payment.get("provider_invoice_id")
            if not transaction_id:
                continue
            try:
                operation = self._plisio.operation(transaction_id)
            except PlisioError:
                LOG.warning("Could not poll crypto payment %s", payment["id"])
                continue
            if self.stop_event.is_set():
                break
            status = " ".join(
                str(operation.get("status") or "").strip().lower().split()
            )
            stored_status = status or "unknown"
            received_raw = operation.get("amount") if "amount" in operation else None
            received_amount = self._nonnegative_provider_amount(received_raw)
            operation_id = str(operation.get("id") or "").strip()
            operation_type = str(operation.get("type") or "").strip().lower()
            provider_terms_valid = True
            if "params" in operation:
                provider_params = operation.get("params")
                if not isinstance(provider_params, Mapping):
                    provider_terms_valid = False
                else:
                    if "source_amount" in provider_params:
                        source_amount = self._nonnegative_provider_amount(
                            provider_params.get("source_amount")
                        )
                        expected_source_amount = Decimal(
                            int(payment["base_amount"])
                            * int(self.settings.plisio_amount_multiplier)
                        )
                        provider_terms_valid = (
                            source_amount is not None
                            and source_amount == expected_source_amount
                        )
                    if "source_currency" in provider_params:
                        provider_terms_valid = provider_terms_valid and (
                            str(provider_params.get("source_currency") or "")
                            .strip()
                            .upper()
                            == str(self.settings.plisio_source_currency).strip().upper()
                        )
                    if "currency" in provider_params:
                        provider_terms_valid = provider_terms_valid and (
                            str(provider_params.get("currency") or "").strip().upper()
                            == str(self.settings.plisio_currency).strip().upper()
                        )
            identity_valid = (
                operation_id == str(transaction_id)
                and operation_type == "invoice"
                and provider_terms_valid
            )
            if not identity_valid:
                try:
                    event = self.db.record_provider_payment_event(
                        payment["id"],
                        "plisio",
                        str(transaction_id),
                        "malformed",
                        operation,
                        received_amount=(
                            str(received_amount)
                            if received_amount is not None
                            else None
                        ),
                        disposition="review",
                    )
                    self._alert_crypto_payment_review(event, payment)
                except DatabaseError:
                    LOG.exception(
                        "Could not quarantine malformed crypto response for payment %s",
                        payment["id"],
                    )
                continue
            if status in {"new", "pending", "pending internal"}:
                continue
            if status == "completed":
                try:
                    # A terminal local resolution may already have released
                    # wallet holds, discounts, or stock. A provider-side
                    # manual completion observed later must reopen review and
                    # can never silently resurrect/credit that payment.
                    late_terminal = payment.get("status") in {
                        "failed",
                        "cancelled",
                        "expired",
                    }
                    event = self.db.record_provider_payment_event(
                        payment["id"],
                        "plisio",
                        str(transaction_id),
                        status,
                        operation,
                        received_amount=(
                            str(received_amount)
                            if received_amount is not None
                            else None
                        ),
                        disposition="review" if late_terminal else "completed",
                    )
                    if late_terminal:
                        self._alert_crypto_payment_review(event, payment)
                        continue
                    self._complete_payment(
                        payment["id"],
                        external_reference=transaction_id,
                        raw_payload=operation,
                    )
                except DatabaseError:
                    LOG.exception("Could not finalize crypto payment %s", payment["id"])
            elif status in {
                "expired",
                "cancelled",
                "error",
                "mismatch",
                "cancelled duplicate",
            }:
                needs_review = (
                    status in {"mismatch", "cancelled duplicate"}
                    or received_amount is None
                    or received_amount > 0
                )
                try:
                    event = self.db.record_provider_payment_event(
                        payment["id"],
                        "plisio",
                        str(transaction_id),
                        status,
                        operation,
                        received_amount=(
                            str(received_amount)
                            if received_amount is not None
                            else None
                        ),
                        disposition="review" if needs_review else "failed",
                    )
                    if needs_review:
                        self._alert_crypto_payment_review(event, payment)
                        continue
                    self.db.set_payment_status(
                        payment["id"], "failed", raw_payload=operation
                    )
                except DatabaseError:
                    LOG.exception(
                        "Could not persist terminal crypto payment %s", payment["id"]
                    )
                else:
                    if payment.get("order_id") is None:
                        continue
                    order = self.db.get_order(int(payment["order_id"]))
                    if not order or order.get("status") != "expired":
                        continue
                    user = self.db.get_user(int(order["user_id"]))
                    if user:
                        self._notify_user_durable(
                            user,
                            texts.order_expired(order["order_number"]),
                            idempotency_key=(
                                f"order:{int(order['id'])}:expired-notice"
                            ),
                        )
            else:
                try:
                    event = self.db.record_provider_payment_event(
                        payment["id"],
                        "plisio",
                        str(transaction_id),
                        stored_status,
                        operation,
                        received_amount=(
                            str(received_amount)
                            if received_amount is not None
                            else None
                        ),
                        disposition="review",
                    )
                    self._alert_crypto_payment_review(event, payment)
                except DatabaseError:
                    LOG.exception(
                        "Could not quarantine unknown crypto status for payment %s",
                        payment["id"],
                    )
        if last_processed_id is not None:
            self.db.set_setting(
                self._CRYPTO_POLL_CURSOR_SETTING, last_processed_id
            )
        elif not self.stop_event.is_set() and cursor:
            self.db.set_setting(self._CRYPTO_POLL_CURSOR_SETTING, 0)

    def _reconcile_completed_provider_events(self) -> None:
        """Settle durable completed evidence left unapplied by a prior crash."""

        if not hasattr(self.db, "list_unapplied_completed_provider_events"):
            return
        for event in self.db.list_unapplied_completed_provider_events(limit=100):
            if self.stop_event.is_set():
                return
            try:
                raw_payload = json.loads(str(event["raw_payload_json"]))
                if not isinstance(raw_payload, dict):
                    raise ValueError("provider payload is not an object")
                self._complete_payment(
                    int(event["payment_id"]),
                    external_reference=str(event["provider_reference"]),
                    raw_payload=raw_payload,
                )
            except (DatabaseError, TypeError, ValueError):
                LOG.exception(
                    "Could not reconcile completed provider event %s", event["id"]
                )

    @staticmethod
    def _nonnegative_provider_amount(value: Any) -> Decimal | None:
        if value is None or isinstance(value, bool):
            return None
        try:
            amount = Decimal(str(value).strip())
        except (InvalidOperation, ValueError):
            return None
        if not amount.is_finite() or amount < 0:
            return None
        return amount

    def _reconcile_payment_review_alerts(self) -> None:
        if hasattr(self.db, "list_provider_payment_reviews"):
            try:
                provider_cursor = max(
                    0,
                    int(
                        self.db.get_setting(
                            self._PROVIDER_REVIEW_CURSOR_SETTING, 0
                        )
                    ),
                )
            except (TypeError, ValueError):
                provider_cursor = 0
            provider_events = self.db.list_provider_payment_reviews(
                limit=100, after_id=provider_cursor
            )
            if not provider_events and provider_cursor:
                provider_events = self.db.list_provider_payment_reviews(
                    limit=100, after_id=0
                )
            for event in provider_events:
                if self.stop_event.is_set():
                    return
                payment = self.db.get_payment(int(event["payment_id"]))
                if payment:
                    self._alert_crypto_payment_review(event, payment)
                provider_cursor = int(event["id"])
            self.db.set_setting(
                self._PROVIDER_REVIEW_CURSOR_SETTING,
                provider_cursor if provider_events else 0,
            )
        if hasattr(self.db, "list_card_payment_reviews"):
            try:
                card_cursor = max(
                    0,
                    int(self.db.get_setting(self._CARD_REVIEW_CURSOR_SETTING, 0)),
                )
            except (TypeError, ValueError):
                card_cursor = 0
            card_events = self.db.list_card_payment_reviews(
                limit=100, after_id=card_cursor
            )
            if not card_events and card_cursor:
                card_events = self.db.list_card_payment_reviews(
                    limit=100, after_id=0
                )
            for event in card_events:
                if self.stop_event.is_set():
                    return
                self._alert_card_payment_review(event)
                card_cursor = int(event["id"])
            self.db.set_setting(
                self._CARD_REVIEW_CURSOR_SETTING,
                card_cursor if card_events else 0,
            )

    def _reconcile_provider_review_resolution_notices(self) -> None:
        if not hasattr(self.db, "list_manual_provider_review_resolutions"):
            return
        try:
            cursor = max(
                0,
                int(
                    self.db.get_setting(
                        self._PROVIDER_RESOLUTION_CURSOR_SETTING, 0
                    )
                ),
            )
        except (TypeError, ValueError):
            cursor = 0
        resolutions = self.db.list_manual_provider_review_resolutions(
            limit=100, after_id=cursor
        )
        if not resolutions and cursor:
            cursor = 0
            resolutions = self.db.list_manual_provider_review_resolutions(
                limit=100, after_id=0
            )
        for resolution in resolutions:
            if self.stop_event.is_set():
                return
            user = self.db.get_user(int(resolution["user_id"]))
            if user is not None:
                self._notify_user_durable(
                    user,
                    texts.provider_review_resolution(
                        str(resolution["action"]),
                        str(resolution["settlement"]),
                    ),
                    idempotency_key=(
                        f"provider-review:{int(resolution['event_id'])}:"
                        f"resolution:{resolution['action']}:user"
                    ),
                )
            cursor = int(resolution["id"])
        self.db.set_setting(
            self._PROVIDER_RESOLUTION_CURSOR_SETTING,
            cursor if resolutions else 0,
        )

    def _reconcile_card_review_resolution_notices(self) -> None:
        if not hasattr(self.db, "list_manual_card_review_resolutions"):
            return
        try:
            cursor = max(
                0,
                int(
                    self.db.get_setting(
                        self._CARD_RESOLUTION_CURSOR_SETTING, 0
                    )
                ),
            )
        except (TypeError, ValueError):
            cursor = 0
        resolutions = self.db.list_manual_card_review_resolutions(
            limit=100, after_id=cursor
        )
        if not resolutions and cursor:
            cursor = 0
            resolutions = self.db.list_manual_card_review_resolutions(
                limit=100, after_id=0
            )
        for resolution in resolutions:
            if self.stop_event.is_set():
                return
            user = self.db.get_user(int(resolution["user_id"]))
            if user is not None:
                self._notify_user_durable(
                    user,
                    texts.card_review_resolution(str(resolution["action"])),
                    idempotency_key=(
                        f"card-review:{int(resolution['event_id'])}:"
                        f"resolution:{resolution['action']}:user"
                    ),
                )
            cursor = int(resolution["id"])
        self.db.set_setting(
            self._CARD_RESOLUTION_CURSOR_SETTING,
            cursor if resolutions else 0,
        )

    def _reconcile_payment_security_alerts(self) -> None:
        if not hasattr(self.db, "list_payment_security_events"):
            return
        try:
            cursor = max(
                0,
                int(self.db.get_setting(self._PAYMENT_SECURITY_CURSOR_SETTING, 0)),
            )
        except (TypeError, ValueError):
            cursor = 0
        events = self.db.list_payment_security_events(limit=100, after_id=cursor)
        if not events and cursor:
            events = self.db.list_payment_security_events(limit=100, after_id=0)
        for event in events:
            if self.stop_event.is_set():
                return
            event_type = str(event.get("event_type") or "")
            reason = (
                "لغوهای پیاپی و ورود به دوره انتظار"
                if event_type == "card_cancel_cooldown"
                else "عبور از سقف روزانه ساخت پرداخت کارت"
            )
            self._notify_privileged_admins_durable(
                "⚠️ <b>محدودسازی سوءاستفاده پرداخت کارت</b>"
                f"\nکاربر: <code>{int(event['chat_id'])}</code>"
                f"\nدلیل: {escape(reason)}",
                idempotency_key=f"payment-security:{int(event['id'])}:admin",
            )
            cursor = int(event["id"])
        self.db.set_setting(
            self._PAYMENT_SECURITY_CURSOR_SETTING, cursor if events else 0
        )

    def _reconcile_card_receipt_alerts(self) -> None:
        if not hasattr(self.db, "list_verifying_card_receipts"):
            return
        try:
            cursor = max(
                0,
                int(self.db.get_setting(self._CARD_RECEIPT_CURSOR_SETTING, 0)),
            )
        except (TypeError, ValueError):
            cursor = 0
        payments = self.db.list_verifying_card_receipts(
            limit=100, after_id=cursor
        )
        if not payments and cursor:
            cursor = 0
            payments = self.db.list_verifying_card_receipts(
                limit=100, after_id=0
            )
        for payment in payments:
            if self.stop_event.is_set():
                return
            user = self.db.get_user(int(payment["user_id"]))
            if user is not None:
                self._alert_card_receipt(payment, user)
            cursor = int(payment["id"])
        self.db.set_setting(
            self._CARD_RECEIPT_CURSOR_SETTING, cursor if payments else 0
        )

    def _alert_card_receipt(
        self,
        payment: Mapping[str, Any],
        user: Mapping[str, Any],
        *,
        reply_markup: dict[str, Any] | None = None,
    ) -> None:
        approval = reply_markup or inline_keyboard(
            [
                [
                    callback_button(
                        "تأیید پرداخت",
                        f"adm:payok:{int(payment['id'])}",
                        style="success",
                    )
                ],
                [
                    callback_button(
                        "رد پرداخت",
                        f"adm:payno:{int(payment['id'])}",
                        style="danger",
                    )
                ],
            ]
        )
        attachment = (
            self.db.get_payment_receipt_attachment(int(payment["id"]))
            if hasattr(self.db, "get_payment_receipt_attachment")
            else None
        )
        receipt_file_id = str(
            (attachment or {}).get("file_id") or payment.get("receipt_file_id") or ""
        )
        receipt_kind = str((attachment or {}).get("file_kind") or "")
        version = hashlib.sha256(
            f"{receipt_kind}\0{receipt_file_id}".encode("utf-8")
        ).hexdigest()[:20]
        self._notify_privileged_admins_durable(
            f"📎 <b>فیش پرداخت نیازمند بررسی</b>"
            f"\nپرداخت: <code>{escape(payment['payment_number'])}</code>"
            f"\nمبلغ: {money(int(payment['payable_amount']), self.settings.currency_label)}"
            f"\nکاربر: <code>{int(user['chat_id'])}</code>"
            f"\nمشاهده فیش: <code>/payment_detail {escape(payment['payment_number'])}</code>",
            idempotency_key=(
                f"payment:{int(payment['id'])}:receipt:{version}:admin"
            ),
            reply_markup=approval,
        )

    @staticmethod
    def _manual_info_version(order: Mapping[str, Any]) -> str:
        raw = str(order.get("customer_info_json") or "")
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]

    def _reconcile_manual_order_info_alerts(self) -> None:
        if not hasattr(self.db, "list_manual_orders_with_customer_info"):
            return
        try:
            cursor = max(
                0,
                int(self.db.get_setting(self._MANUAL_INFO_CURSOR_SETTING, 0)),
            )
        except (TypeError, ValueError):
            cursor = 0
        orders = self.db.list_manual_orders_with_customer_info(
            limit=100, after_id=cursor
        )
        if not orders and cursor:
            cursor = 0
            orders = self.db.list_manual_orders_with_customer_info(
                limit=100, after_id=0
            )
        for order in orders:
            if self.stop_event.is_set():
                return
            user = self.db.get_user(int(order["user_id"]))
            if user is not None:
                self._alert_manual_order_info(order, user)
            cursor = int(order["id"])
        self.db.set_setting(
            self._MANUAL_INFO_CURSOR_SETTING, cursor if orders else 0
        )

    def _reconcile_ticket_admin_alerts(self) -> None:
        """Recover durable staff alerts for user-authored ticket messages.

        The cursor deliberately rotates over every user message. Stable
        per-admin outbox keys make reprocessing harmless, while also allowing
        an administrator added after the original submission to receive the
        alert on a later pass.
        """

        if not hasattr(self.db, "list_user_ticket_messages"):
            return
        try:
            cursor = max(
                0,
                int(self.db.get_setting(self._TICKET_ALERT_CURSOR_SETTING, 0)),
            )
        except (TypeError, ValueError):
            cursor = 0
        messages = self.db.list_user_ticket_messages(
            limit=max(1, int(self.MAINTENANCE_TICKET_ALERT_RECONCILE_LIMIT)),
            after_id=cursor,
        )
        if not messages and cursor:
            cursor = 0
            messages = self.db.list_user_ticket_messages(
                limit=max(1, int(self.MAINTENANCE_TICKET_ALERT_RECONCILE_LIMIT)),
                after_id=0,
            )
        for ticket_message in messages:
            if self.stop_event.is_set():
                return
            self._alert_user_ticket_message(ticket_message)
            cursor = int(ticket_message["id"])
        self.db.set_setting(
            self._TICKET_ALERT_CURSOR_SETTING, cursor if messages else 0
        )

    def _reconcile_ready_stock_alerts(self) -> None:
        """Recover user and staff alerts after a no-stock transition."""

        if not hasattr(self.db, "list_ready_processing_orders"):
            return
        try:
            cursor = max(
                0,
                int(self.db.get_setting(self._READY_STOCK_ALERT_CURSOR_SETTING, 0)),
            )
        except (TypeError, ValueError):
            cursor = 0
        orders = self.db.list_ready_processing_orders(limit=100, after_id=cursor)
        if not orders and cursor:
            cursor = 0
            orders = self.db.list_ready_processing_orders(limit=100, after_id=0)
        for order in orders:
            if self.stop_event.is_set():
                return
            user = self.db.get_user(int(order["user_id"]))
            if user is not None:
                self._alert_ready_stock_processing(order, user)
            cursor = int(order["id"])
        self.db.set_setting(
            self._READY_STOCK_ALERT_CURSOR_SETTING, cursor if orders else 0
        )

    def _reconcile_reward_notices(self) -> None:
        """Recover notices for wallet rewards committed before their outbox."""

        if not hasattr(self.db, "list_reward_events_missing_notice"):
            return
        try:
            cursor = max(
                0,
                int(self.db.get_setting(self._REWARD_NOTICE_CURSOR_SETTING, 0)),
            )
        except (TypeError, ValueError):
            cursor = 0
        rewards = self.db.list_reward_events_missing_notice(
            limit=max(1, int(self.MAINTENANCE_REWARD_RECONCILE_LIMIT)),
            after_id=cursor,
        )
        if not rewards and cursor:
            cursor = 0
            rewards = self.db.list_reward_events_missing_notice(
                limit=max(1, int(self.MAINTENANCE_REWARD_RECONCILE_LIMIT)),
                after_id=0,
            )
        for reward in rewards:
            if self.stop_event.is_set():
                return
            reward_user = self.db.get_user(int(reward["user_id"]))
            if reward_user is not None:
                self._notify_user_durable(
                    reward_user,
                    f"🎁 {money(reward['amount'], self.settings.currency_label)} "
                    "پاداش دعوت به کیف پولت اضافه شد.",
                    idempotency_key=f"reward:{int(reward['id'])}:notice",
                )
            cursor = int(reward["id"])
        self.db.set_setting(
            self._REWARD_NOTICE_CURSOR_SETTING, cursor if rewards else 0
        )

    def _alert_ready_stock_processing(
        self,
        order: Mapping[str, Any],
        user: Mapping[str, Any],
    ) -> None:
        order_id = int(order["id"])
        self._notify_user_durable(
            user,
            "پرداخت سفارش با موفقیت ثبت شد، اما موجودی آماده هم‌زمان تمام شد. "
            "سفارش برای تأمین دستی در حال پردازش است و مدیریت نتیجه را اعلام می‌کند."
            f"\nشماره سفارش: <code>{escape(order['order_number'])}</code>",
            idempotency_key=f"order:{order_id}:manual-stock-notice",
            reply_markup=inline_keyboard(
                [[callback_button("مشاهده سفارش", f"order:{order_id}")]]
            ),
        )
        self._notify_privileged_admins_durable(
            f"⚠️ سفارش پرداخت‌شده <code>{escape(order['order_number'])}</code> موجودی ندارد.",
            idempotency_key=f"order:{order_id}:manual-stock-admin",
        )

    def _alert_user_ticket_message(
        self,
        ticket_message: Mapping[str, Any],
        *,
        ticket: Mapping[str, Any] | None = None,
        user: Mapping[str, Any] | None = None,
    ) -> None:
        if ticket is None:
            ticket = self.db.get_ticket(int(ticket_message["ticket_id"]))
        if ticket is None:
            return
        if user is None:
            user = self.db.get_user(int(ticket["user_id"]))
        if user is None:
            return
        body_preview = clamp_text(str(ticket_message.get("body") or ""), 1_000)
        attachment_command = ""
        if ticket_message.get("attachment_file_id"):
            attachment_command = (
                "\nمشاهده پیوست: "
                f"<code>/ticket_attachment {int(ticket_message['id'])}</code>"
            )
        self._notify_active_admins_durable(
            "🎫 <b>پیام جدید کاربر در تیکت</b>"
            f"\nتیکت: <code>{escape(ticket['ticket_number'])}</code>"
            f"\nموضوع: {escape(ticket['subject'])}"
            f"\nکاربر: <code>{int(user['chat_id'])}</code>"
            f"\nپیام: {escape(body_preview)}"
            f"{attachment_command}",
            idempotency_key=f"ticket-message:{int(ticket_message['id'])}:admin",
        )

    def _alert_manual_order_info(
        self,
        order: Mapping[str, Any],
        user: Mapping[str, Any],
    ) -> None:
        version = self._manual_info_version(order)
        try:
            stored_information = json.loads(
                str(order.get("customer_info_json") or "{}")
            )
        except (TypeError, ValueError):
            stored_information = {}
        stored_text = (
            str(stored_information.get("text") or "")
            if isinstance(stored_information, dict)
            else ""
        )
        text_preview = (
            f"\nاطلاعات: {escape(clamp_text(stored_text, 1200))}"
            if stored_text
            else ""
        )
        reply_markup = inline_keyboard(
            [
                [
                    callback_button(
                        "تکمیل سفارش",
                        f"adm:complete:{int(order['id'])}",
                        style="success",
                    )
                ]
            ]
        )
        self._notify_privileged_admins_durable(
            f"📋 <b>اطلاعات سفارش دستی دریافت شد</b>"
            f"\nسفارش: <code>{escape(order['order_number'])}</code>"
            f"\nکاربر: <code>{int(user['chat_id'])}</code>{text_preview}"
            f"\nمشاهده پیوست: <code>/order_attachment {escape(order['order_number'])}</code>",
            idempotency_key=(
                f"order:{int(order['id'])}:customer-info:{version}:admin"
            ),
            reply_markup=reply_markup,
        )

    def _alert_crypto_payment_review(
        self,
        event: Mapping[str, Any],
        payment: Mapping[str, Any],
    ) -> None:
        user = self.db.get_user(int(payment["user_id"]))
        if user:
            self._notify_user_durable(
                user,
                "⚠️ <b>پرداخت ارزی نیازمند بررسی است</b>\n\n"
                "درگاه نتیجه‌ای برگرداند که ممکن است شامل واریز ناقص یا نامشخص باشد. "
                "این مبلغ خودکار تأیید یا رد نشده و مدیریت آن را بررسی می‌کند.",
                idempotency_key=f"provider-review:{int(event['id'])}:user",
            )
        received = event.get("received_amount")
        received_label = "نامشخص" if received is None else str(received)
        can_credit = False
        if event.get("provider_status") == "completed":
            try:
                evidence = json.loads(str(event.get("raw_payload_json") or "{}"))
            except (TypeError, ValueError):
                evidence = {}
            can_credit = bool(
                isinstance(evidence, dict)
                and str(evidence.get("id") or "").strip()
                == str(payment.get("provider_invoice_id") or "")
                and str(evidence.get("type") or "").strip().lower() == "invoice"
                and str(evidence.get("status") or "").strip().lower()
                == "completed"
            )
        credit_command = (
            f"\nتأیید و اعتبار: <code>/crypto_resolve {int(event['id'])} credit_confirmed | توضیح</code>"
            if can_credit
            else ""
        )
        admin_text = (
            "⚠️ <b>بازبینی مالی پرداخت ارزی</b>"
            f"\nشناسه رخداد: <code>{int(event['id'])}</code>"
            f"\nشماره پرداخت: <code>{escape(payment['payment_number'])}</code>"
            f"\nوضعیت درگاه: <code>{escape(event['provider_status'])}</code>"
            f"\nمبلغ دریافتی گزارش‌شده: <code>{escape(received_label)}</code>"
            "\nهیچ اعتباری خودکار ثبت نشده است."
            f"\nبرای تعیین تکلیف: <code>/crypto_resolve {int(event['id'])} dismiss | توضیح</code>"
            f"\nیا: <code>/crypto_resolve {int(event['id'])} refund_confirmed | توضیح</code>"
            f"{credit_command}"
        )
        self._notify_privileged_admins_durable(
            admin_text,
            idempotency_key=f"provider-review:{int(event['id'])}:admin",
        )

    def _alert_card_payment_review(self, event: Mapping[str, Any]) -> None:
        payment = None
        linked_user = None
        if event.get("payment_id") is not None:
            payment = self.db.get_payment(int(event["payment_id"]))
            if payment is not None:
                linked_user = self.db.get_user(int(payment["user_id"]))
        linked = ""
        if payment is not None and linked_user is not None:
            linked = (
                f"\nپرداخت مرتبط: <code>{escape(payment['payment_number'])}</code>"
                f"\nنوع: <code>{escape(payment['purpose'])}</code>"
                f"\nکاربر: <code>{int(linked_user['chat_id'])}</code>"
            )
        admin_text = (
            "⚠️ <b>رخداد بانکی نیازمند بازبینی</b>"
            f"\nشناسه رخداد: <code>{int(event['id'])}</code>"
            f"\nمرجع: <code>{escape(event['reference'])}</code>"
            f"\nمبلغ: <code>{int(event['amount']):,}</code>"
            f"{linked}"
            "\nهیچ کیف پول یا سفارشی خودکار اعتبار نگرفته است."
            f"\nبرای تعیین تکلیف: <code>/card_resolve {int(event['id'])} dismiss | توضیح</code>"
            f"\nیا: <code>/card_resolve {int(event['id'])} refund_confirmed | توضیح</code>"
        )
        self._notify_privileged_admins_durable(
            admin_text,
            idempotency_key=f"card-review:{int(event['id'])}:admin",
        )

    def _deliver_due_reminders(self) -> None:
        if not hasattr(self.db, "claim_due_reminders"):
            return
        claimed = self.db.claim_due_reminders(limit=50)
        for index, reminder in enumerate(claimed):
            if self.stop_event.is_set():
                for pending in claimed[index:]:
                    self.db.release_reminder_for_retry(
                        pending["id"], "shutdown before delivery"
                    )
                return
            user = self.db.get_user(reminder["user_id"])
            order = self.db.get_order(reminder["order_id"])
            if not user or not order:
                self.db.mark_reminder_failed(
                    reminder["id"], "missing user/order", permanent=True
                )
                continue
            try:
                subscription_ends_at = parse_iso(order.get("subscription_ends_at"))
            except (TypeError, ValueError):
                subscription_ends_at = None
            if subscription_ends_at is None:
                self.db.mark_reminder_failed(
                    reminder["id"], "missing subscription end", permanent=True
                )
                continue
            current = utc_now()
            if subscription_ends_at <= current:
                self.db.mark_reminder(
                    reminder["id"],
                    "cancelled",
                    error_text="subscription ended before reminder delivery",
                )
                continue
            local_end = subscription_ends_at.astimezone(ZoneInfo(self.settings.timezone))
            # Keep the durable body stable across delayed retries. Relative
            # day counts become false after a midnight or multi-day outage.
            remaining_text = (
                f"امروز ساعت {local_end:%H:%M}"
                if int(reminder["days_before"]) == 0
                else f"در تاریخ {local_end:%Y-%m-%d} ساعت {local_end:%H:%M}"
            )
            outbox_key = f"reminder:{reminder['id']}"
            delivered = self._notify_user_durable(
                user,
                "⏰ <b>یادآوری پایان اشتراک</b>\n\n"
                f"اشتراک {escape(order['product_name_snapshot'])} {remaining_text} پایان می‌یابد.\n"
                f"زمان پایان: {local_end:%Y-%m-%d %H:%M} ({escape(self.settings.timezone)})",
                idempotency_key=outbox_key,
            )
            outbox = self.db.get_outbound_message_by_idempotency_key(outbox_key)
            if delivered or (outbox and outbox.get("status") == "sent"):
                self.db.mark_reminder_sent(
                    reminder["id"],
                    (
                        int(outbox["telegram_message_id"])
                        if outbox and outbox.get("telegram_message_id") is not None
                        else None
                    ),
                )
            elif outbox and outbox.get("status") in {"failed", "cancelled"}:
                self.db.mark_reminder_failed(
                    reminder["id"],
                    str(outbox.get("error_text") or "outbound delivery failed"),
                    permanent=True,
                )
            elif outbox is None:
                self.db.release_reminder_for_retry(
                    reminder["id"], "could not create durable delivery"
                )
            # A queued/sending outbox row owns retry from this point. Keep the
            # reminder claimed until stale-claim recovery later reconciles its
            # terminal outbox state, instead of reclaiming it in this cycle.

    def _deliver_outbound_messages(self) -> None:
        if not hasattr(self.db, "claim_outbound_messages"):
            return
        for _ in range(20):
            if self.stop_event.is_set():
                return
            claimed = self.db.claim_outbound_messages(limit=1)
            if not claimed:
                return
            item = claimed[0]
            if self.stop_event.is_set():
                self.db.mark_outbound_message(
                    item["id"],
                    "queued",
                    error_text="shutdown before delivery",
                )
                return
            recipient = self.db.get_user(item["recipient_user_id"])
            if not recipient:
                self.db.mark_outbound_message(item["id"], success=False, error="missing user")
                continue
            reminder_key = re.fullmatch(r"reminder:(\d+)", str(item.get("idempotency_key") or ""))
            if reminder_key:
                reminder = self.db.get_reminder(int(reminder_key.group(1)))
                order = self.db.get_order(reminder["order_id"]) if reminder else None
                try:
                    end_at = parse_iso(order.get("subscription_ends_at")) if order else None
                except (TypeError, ValueError):
                    end_at = None
                if (
                    not reminder or not order or end_at is None or end_at <= utc_now()
                    or int(reminder["user_id"]) != int(item["recipient_user_id"])
                    or reminder["status"] == "cancelled"
                ):
                    self.db.mark_outbound_message(
                        item["id"], "cancelled", error_text="reminder no longer valid before delivery",
                    )
                    if reminder:
                        self.db.mark_reminder(reminder["id"], "cancelled")
                    continue
            try:
                reply_markup = None
                if item.get("reply_markup_json"):
                    try:
                        decoded = json.loads(item["reply_markup_json"])
                        if isinstance(decoded, dict):
                            reply_markup = decoded
                    except (TypeError, ValueError):
                        LOG.warning("Ignoring invalid reply markup for outbound message %s", item["id"])
                sent = self.telegram.send_message(
                    recipient["chat_id"],
                    item["body"],
                    parse_mode="HTML",
                    reply_markup=reply_markup,
                )
                self.db.mark_outbound_message(
                    item["id"], success=True, telegram_message_id=sent.get("message_id")
                )
                if self.stop_event.wait(0.04):
                    return
            except TelegramRequestCancelled:
                self.db.mark_outbound_message(
                    item["id"],
                    "queued",
                    error_text="shutdown interrupted delivery",
                )
                return
            except TelegramError as exc:
                self.db.schedule_outbound_retry(
                    int(item["id"]),
                    str(exc),
                    permanent=self._outbound_error_is_permanent(exc),
                )

    def _report_completed_broadcasts(self) -> None:
        if not hasattr(self.db, "list_ready_broadcast_summaries"):
            return
        for batch in self.db.list_ready_broadcast_summaries(limit=50):
            if self.stop_event.is_set():
                return
            summary_status = str(batch.get("summary_status") or "")
            if summary_status in {"sent", "failed", "cancelled"}:
                self.db.mark_broadcast_notified(str(batch["id"]))
                continue
            user = self.db.get_user(int(batch["actor_user_id"]))
            if not user:
                LOG.error(
                    "Broadcast batch %s has no actor user for its summary",
                    batch["id"],
                )
                self.db.mark_broadcast_notified(str(batch["id"]))
                continue
            sent_count = int(batch.get("sent_count") or 0)
            failed_count = int(batch.get("failed_count") or 0)
            delivered = self._notify_user_durable(
                user,
                "📣 <b>گزارش نهایی ارسال گروهی</b>"
                f"\nتعداد هدف: {int(batch['target_count']):,}"
                f"\nارسال موفق: {sent_count:,}"
                f"\nناموفق: {failed_count:,}",
                idempotency_key=f"broadcast:{batch['id']}:summary",
            )
            if delivered:
                self.db.mark_broadcast_notified(str(batch["id"]))
                continue
            summary = self.db.get_outbound_message_by_idempotency_key(
                f"broadcast:{batch['id']}:summary"
            )
            if summary and summary.get("status") in {"failed", "cancelled"}:
                # The immutable outbox row retains the permanent error for
                # audit. Mark the batch terminal so it cannot starve later
                # completion reports behind the same bounded query.
                self.db.mark_broadcast_notified(str(batch["id"]))

    @staticmethod
    def _outbound_error_is_permanent(error: TelegramError) -> bool:
        if not isinstance(error, TelegramAPIError):
            return False
        code = error.error_code or error.status_code
        return code is not None and 400 <= int(code) < 500 and int(code) != 429

    def _notify_user(self, chat_id: int, text: str, **kwargs: Any) -> bool:
        try:
            self.telegram.send_message(chat_id, text, **kwargs)
            return True
        except TelegramError:
            LOG.warning("Could not notify user")
            return False

    def _notify_user_durable(
        self,
        user: Mapping[str, Any],
        text: str,
        *,
        idempotency_key: str,
        reply_markup: dict[str, Any] | None = None,
    ) -> bool:
        """Persist a critical notice before sending it, then deliver once."""

        try:
            parts = split_telegram_html(
                text, maximum=self.db.TELEGRAM_SAFE_MESSAGE_LENGTH
            )
        except ValueError:
            LOG.exception("Could not safely split critical message %s", idempotency_key)
            return False
        if not parts:
            return False
        if len(parts) > 1:
            total = len(parts)
            for index, part in enumerate(parts, start=1):
                delivered = self._notify_user_durable(
                    user,
                    part,
                    idempotency_key=(
                        f"{idempotency_key}:part:{index:04d}-of-{total:04d}"
                    ),
                    reply_markup=reply_markup if index == total else None,
                )
                # Preserve credential order across retries: a later part must
                # never overtake a transiently failed earlier part.
                if not delivered:
                    return False
            return True

        try:
            queued = self.db.queue_outbound_message(
                text,
                recipient_user_id=int(user["id"]),
                reply_markup=reply_markup,
                idempotency_key=idempotency_key,
            )
        except DatabaseError:
            LOG.exception("Could not queue critical user message %s", idempotency_key)
            return False

        if self.stop_event.is_set():
            return False

        with self._durable_notification_lock:
            if queued.get("status") == "sent":
                return True
            if queued.get("status") in {"sending", "failed", "cancelled"}:
                return False
            claimed = self.db.claim_outbound_message(int(queued["id"]))
            if not claimed:
                return False
            if self.stop_event.is_set():
                self.db.mark_outbound_message(
                    int(queued["id"]),
                    "queued",
                    error_text="shutdown before delivery",
                )
                return False
            try:
                sent = self.telegram.send_message(
                    int(user["chat_id"]), text, reply_markup=reply_markup
                )
            except TelegramRequestCancelled:
                self.db.mark_outbound_message(
                    int(queued["id"]),
                    "queued",
                    error_text="shutdown interrupted delivery",
                )
                return False
            except TelegramError as exc:
                self.db.schedule_outbound_retry(
                    int(queued["id"]),
                    str(exc),
                    permanent=self._outbound_error_is_permanent(exc),
                )
                LOG.warning("Could not deliver outbound message %s", queued["id"])
                return False
            self.db.mark_outbound_message(
                int(queued["id"]),
                success=True,
                telegram_message_id=sent.get("message_id"),
            )
            return True

    def _notify_admins(
        self, text: str, *, reply_markup: dict[str, Any] | None = None
    ) -> int:
        sent = 0
        for admin in self.db.list_admins(active_only=True):
            if self.stop_event.is_set():
                break
            if not admin.get("chat_id"):
                continue
            try:
                self.telegram.send_message(
                    admin["chat_id"], text, reply_markup=reply_markup
                )
                sent += 1
            except TelegramError:
                LOG.warning("Could not notify admin %s", admin.get("id"))
        return sent

    def _notify_privileged_admins_durable(
        self,
        text: str,
        *,
        idempotency_key: str,
        reply_markup: dict[str, Any] | None = None,
    ) -> int:
        """Queue one durable financial alert for every owner/admin identity."""

        return self._notify_admin_roles_durable(
            text,
            idempotency_key=idempotency_key,
            reply_markup=reply_markup,
            allowed_roles={"owner", "admin"},
        )

    def _notify_active_admins_durable(
        self,
        text: str,
        *,
        idempotency_key: str,
        reply_markup: dict[str, Any] | None = None,
    ) -> int:
        """Queue a durable operational alert for owner/admin/support roles."""

        return self._notify_admin_roles_durable(
            text,
            idempotency_key=idempotency_key,
            reply_markup=reply_markup,
            allowed_roles={"owner", "admin", "support"},
        )

    def _notify_admin_roles_durable(
        self,
        text: str,
        *,
        idempotency_key: str,
        reply_markup: dict[str, Any] | None,
        allowed_roles: set[str],
    ) -> int:
        delivered = 0
        for admin in self.db.list_admins(active_only=True):
            if self.stop_event.is_set():
                break
            if admin.get("role") not in allowed_roles or not admin.get("chat_id"):
                continue
            try:
                user = self.db.get_user_by_chat_id(int(admin["chat_id"]))
                if user is None:
                    # In a private Telegram chat the user ID and chat ID are
                    # equal. Materialising the row lets the normal durable
                    # outbox protect even a bootstrap owner before /start.
                    user = self.db.upsert_user(
                        int(admin["chat_id"]),
                        int(admin["chat_id"]),
                        username=str(admin.get("username") or "") or None,
                    )
                if self._notify_user_durable(
                    user,
                    text,
                    idempotency_key=f"{idempotency_key}:{int(admin['id'])}",
                    reply_markup=reply_markup,
                ):
                    delivered += 1
            except DatabaseError:
                LOG.exception(
                    "Could not queue durable financial alert for admin %s",
                    admin.get("id"),
                )
        return delivered

    def _copy_to_admins(
        self,
        message: dict[str, Any],
        *,
        reply_markup: dict[str, Any] | None = None,
        allowed_roles: set[str] | frozenset[str] | None = None,
    ) -> int:
        sent = 0
        from_chat_id = message["chat"]["id"]
        message_id = message["message_id"]
        for admin in self.db.list_admins(active_only=True):
            if allowed_roles is not None and admin.get("role") not in allowed_roles:
                continue
            if not admin.get("chat_id"):
                continue
            try:
                self.telegram.copy_message(
                    admin["chat_id"],
                    from_chat_id,
                    message_id,
                    reply_markup=reply_markup,
                )
                sent += 1
            except TelegramError:
                pass
        return sent

    def _maybe_send_completion_notice(self, admin: dict[str, Any]) -> bool:
        if admin.get("role") != "owner" or not self.db.get_setting(
            "completion_notice_pending", True
        ):
            return False
        self.telegram.send_message(
            admin["chat_id"],
            "✅ نسخه کامل ربات الون اکانت آماده و فعال شد.\n\n"
            "برای ورود به پنل مدیریت /admin را بزن و برای راهنمای همه فرمان‌ها /admin_help را ارسال کن.",
        )
        self.db.set_setting("completion_notice_pending", False)
        return True

    def _send_admin_home(self, admin: dict[str, Any]) -> None:
        role_name = {"owner": "مدیر مالک", "admin": "مدیر کل", "support": "پشتیبان"}.get(
            admin.get("role"), admin.get("role")
        )
        self.telegram.send_message(
            admin["chat_id"],
            f"🛠 <b>پنل مدیریت</b>\n\nنقش: {escape(role_name)}\nراهنمای کامل: /admin_help",
            reply_markup=inline_keyboard(
                [
                    [callback_button("سفارش‌ها", "adm:orders")],
                    [callback_button("تیکت‌ها", "adm:tickets")],
                    [callback_button("کاربران", "adm:users")],
                    [callback_button("تنظیمات", "adm:settings")],
                    [back_button("menu")],
                ]
            ),
        )
