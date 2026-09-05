"""Shared administrator operations with button-first and command entry points.

The controller contains no polling loop.  ``bot.py`` hands authenticated
private-chat messages and callback queries to :class:`AdminController`.
Repository methods are preferred whenever ``Database`` exposes them; compact
SQLite fallbacks keep the documented management surface usable with the
current repository version without modifying ``db.py``.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import os
import re
import secrets
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from contextlib import closing
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import texts
from .admin_help import ADMIN_HELP_PARTS, SUPPORT_HELP, pipe_parts, split_command
from .db import (
    ConflictError,
    DatabaseError,
    InsufficientFundsError,
    NotFoundError,
    OutOfStockError,
    ValidationError,
)
from .keyboards import callback_button, inline_keyboard
from .telegram import TelegramError
from .utils import (
    clamp_text,
    escape,
    format_admin_text,
    is_safe_https_url,
    is_safe_telegram_channel_url,
    is_safe_telegram_invite_url,
    money,
    normalize_digits,
    normalize_username,
    parse_amount,
    render_rich_text,
)


class AdminInputError(ValueError):
    """A safe, user-facing command validation error."""


class AdminIntegrationError(RuntimeError):
    """The installed repository does not expose data needed by a command."""


NotifyUser = Callable[[int, str], Any]
FulfillOrder = Callable[[dict[str, Any]], Any]


SUPPORT_COMMANDS = frozenset(
    {
        "/admin_help",
        "/orders",
        "/order",
        "/request_info",
        "/tickets",
        "/ticket",
        "/ticket_attachment",
        "/ticket_reply",
        "/ticket_status",
        "/ticket_close",
        "/users",
        "/user",
        "/user_orders",
        "/user_transactions",
        "/user_referrals",
        "/user_rewards",
        "/message",
    }
)

DOCUMENTED_COMMANDS = frozenset(
    {
        "/admin_help",
        "/bot_on",
        "/bot_off",
        "/set_card",
        "/set_channel",
        "/payment",
        "/joins",
        "/join_add",
        "/join_toggle",
        "/join_delete",
        "/backup",
        "/admins",
        "/admin_add",
        "/admin_enable",
        "/admin_disable",
        "/categories",
        "/category_add",
        "/subcategory_add",
        "/category_toggle",
        "/category_set",
        "/category_delete",
        "/products",
        "/product_add",
        "/product_set",
        "/product_toggle",
        "/product_delete",
        "/inventory_add",
        "/inventory_list",
        "/inventory_edit",
        "/inventory_disable",
        "/inventory_enable",
        "/inventory_delete",
        "/inventory_assign",
        "/orders",
        "/order",
        "/order_attachment",
        "/order_status",
        "/complete",
        "/request_info",
        "/approve_payment",
        "/reject_payment",
        "/payment_detail",
        "/card_reviews",
        "/card_resolve",
        "/crypto_reviews",
        "/crypto_resolve",
        "/users",
        "/user",
        "/user_orders",
        "/user_transactions",
        "/user_referrals",
        "/user_rewards",
        "/block",
        "/unblock",
        "/wallet_adjust",
        "/message",
        "/discounts",
        "/discount_add",
        "/discount_toggle",
        "/discount_delete",
        "/tickets",
        "/ticket",
        "/ticket_attachment",
        "/ticket_reply",
        "/ticket_status",
        "/ticket_close",
        "/faq_categories",
        "/faq_category_add",
        "/faq_category_toggle",
        "/faq_category_set",
        "/faq_category_delete",
        "/faqs",
        "/faq_add",
        "/faq_toggle",
        "/faq_set",
        "/faq_delete",
        "/broadcast_all",
        "/broadcast_joined",
        "/broadcast_product",
        "/report",
        "/rewards",
        "/reward_add",
        "/reward_toggle",
    }
)

_ORDER_STATUSES = {
    "pending_payment",
    "awaiting_confirmation",
    "awaiting_stock",
    "awaiting_info",
    "paid",
    "processing",
    "completed",
    "rejected",
    "expired",
    "cancelled",
    "refunded",
}
_TICKET_STATUSES = {"open", "answered", "closed"}
_ADMIN_PAGE_SIZE = 20
_ADMIN_TERMINAL_ERRORS = (
    ConflictError,
    InsufficientFundsError,
    NotFoundError,
    OutOfStockError,
    ValidationError,
)
_ADMIN_INPUT_ERRORS = (ValueError, AdminIntegrationError, *_ADMIN_TERMINAL_ERRORS)
_PRODUCT_FIELDS = {
    "name": "name",
    "category": "category_id",
    "type": "product_type",
    "icon": "icon",
    "short_description": "short_description",
    "long_description": "long_description",
    "price": "price_amount",
    "duration": "duration_label",
    "duration_days": "duration_days",
    "account_type": "account_type",
    "activation": "activation",
    "renewable": "is_renewable",
    "warranty": "warranty_text",
    "features": "features",
    "activation_instructions": "activation_instructions",
    "usage_terms": "usage_terms",
    "rules": "rules_text",
    "rules_url": "rules_url",
    "info_request_text": "info_request_text",
    "completion_text": "completion_text",
    "delivery_instructions": "delivery_instructions",
    "reminder_days": "reminder_days",
    "stock_limit": "stock_limit",
}
_USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{5,32}$")


def _duration_days(value: str) -> int | None:
    normalized = normalize_digits(value).strip().casefold()
    match = re.fullmatch(r"(\d+)\s*(?:روز|روزها|day|days)?", normalized)
    if not match:
        return None
    days = int(match.group(1))
    return days if days > 0 else None


def _utc_timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _as_int(value: str, label: str = "شناسه") -> int:
    try:
        result = int(value.strip())
    except (TypeError, ValueError) as exc:
        raise AdminInputError(f"{label} باید عدد صحیح باشد.") from exc
    if result == 0:
        raise AdminInputError(f"{label} نمی‌تواند صفر باشد.")
    return result


def _page_number(value: str) -> int:
    try:
        result = int(normalize_digits(value.strip()))
    except (TypeError, ValueError) as exc:
        raise AdminInputError("شماره صفحه باید عدد صحیح مثبت باشد.") from exc
    if result < 1:
        raise AdminInputError("شماره صفحه باید حداقل ۱ باشد.")
    return result


def _page_bounds(total: int, page: int, *, page_size: int = _ADMIN_PAGE_SIZE) -> tuple[int, int]:
    pages = max(1, (max(0, int(total)) + page_size - 1) // page_size)
    if page > pages:
        raise AdminInputError(f"شماره صفحه خارج از بازه است؛ آخرین صفحه {pages} است.")
    return pages, (page - 1) * page_size


def _on_off(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized not in {"on", "off"}:
        raise AdminInputError("وضعیت باید on یا off باشد.")
    return normalized == "on"


def _boolean_value(value: str) -> bool:
    normalized = value.strip().casefold()
    if normalized in {"1", "true", "yes", "on", "بله", "فعال"}:
        return True
    if normalized in {"0", "false", "no", "off", "خیر", "غیرفعال"}:
        return False
    raise AdminInputError("مقدار بولی باید on/off یا true/false باشد.")


def _date_range(
    from_value: str,
    to_value: str,
    timezone_name: str = "UTC",
) -> tuple[str, str]:
    try:
        start = date.fromisoformat(from_value.strip())
        end = date.fromisoformat(to_value.strip())
    except ValueError as exc:
        raise AdminInputError("تاریخ باید با قالب YYYY-MM-DD نوشته شود.") from exc
    if end < start:
        raise AdminInputError("تاریخ پایان نباید قبل از تاریخ شروع باشد.")
    try:
        local_zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise AdminInputError("منطقه زمانی TIMEZONE معتبر نیست.") from exc
    start_at = datetime.combine(start, datetime.min.time(), tzinfo=local_zone)
    end_exclusive = datetime.combine(
        end + timedelta(days=1), datetime.min.time(), tzinfo=local_zone
    )
    return (
        start_at.astimezone(UTC).isoformat(timespec="seconds"),
        end_exclusive.astimezone(UTC).isoformat(timespec="seconds"),
    )


class AdminController:
    """Handle documented administrator commands and callbacks.

    ``notify_user`` must accept ``(chat_id, html_text)``. ``fulfill_order`` is
    called as ``fulfill_order(order_dict)`` after manual payment approval,
    making automatic inventory delivery reusable from the main bot service.
    """

    def __init__(
        self,
        db: Any,
        telegram: Any,
        settings: Any,
        notify_user: NotifyUser | None = None,
        fulfill_order: FulfillOrder | None = None,
    ) -> None:
        self.db = db
        self.telegram = telegram
        self.settings = settings
        self.notify_user = notify_user
        self.fulfill_order = fulfill_order
        self.log = logging.getLogger(__name__)
        self._button_context: dict[str, Any] | None = None
        from .admin_ui import AdminButtonUI

        self.button_ui = AdminButtonUI(self)
        self._handlers = {
            "/admin_help": self._help,
            "/bot_on": self._bot_on,
            "/bot_off": self._bot_off,
            "/set_card": self._set_card,
            "/set_channel": self._set_channel,
            "/payment": self._payment,
            "/joins": self._joins,
            "/join_add": self._join_add,
            "/join_toggle": self._join_toggle,
            "/join_delete": self._join_delete,
            "/backup": self._backup,
            "/admins": self._admins,
            "/admin_add": self._admin_add,
            "/admin_enable": self._admin_enable,
            "/admin_disable": self._admin_disable,
            "/categories": self._categories,
            "/category_add": self._category_add,
            "/subcategory_add": self._subcategory_add,
            "/category_toggle": self._category_toggle,
            "/category_set": self._category_set,
            "/category_delete": self._category_delete,
            "/products": self._products,
            "/product_add": self._product_add,
            "/product_set": self._product_set,
            "/product_toggle": self._product_toggle,
            "/product_delete": self._product_delete,
            "/inventory_add": self._inventory_add,
            "/inventory_list": self._inventory_list,
            "/inventory_edit": self._inventory_edit,
            "/inventory_disable": self._inventory_disable,
            "/inventory_enable": self._inventory_enable,
            "/inventory_delete": self._inventory_delete,
            "/inventory_assign": self._inventory_assign,
            "/orders": self._orders,
            "/order": self._order,
            "/order_attachment": self._order_attachment,
            "/order_status": self._order_status,
            "/complete": self._complete,
            "/request_info": self._request_info,
            "/approve_payment": self._approve_payment,
            "/reject_payment": self._reject_payment,
            "/payment_detail": self._payment_detail,
            "/card_reviews": self._card_reviews,
            "/card_resolve": self._card_resolve,
            "/crypto_reviews": self._crypto_reviews,
            "/crypto_resolve": self._crypto_resolve,
            "/users": self._users,
            "/user": self._user,
            "/user_orders": self._user_orders,
            "/user_transactions": self._user_transactions,
            "/user_referrals": self._user_referrals,
            "/user_rewards": self._user_rewards,
            "/block": self._block,
            "/unblock": self._unblock,
            "/wallet_adjust": self._wallet_adjust,
            "/message": self._message,
            "/discounts": self._discounts,
            "/discount_add": self._discount_add,
            "/discount_toggle": self._discount_toggle,
            "/discount_delete": self._discount_delete,
            "/tickets": self._tickets,
            "/ticket": self._ticket,
            "/ticket_attachment": self._ticket_attachment,
            "/ticket_reply": self._ticket_reply,
            "/ticket_status": self._ticket_status,
            "/ticket_close": self._ticket_close,
            "/faq_categories": self._faq_categories,
            "/faq_category_add": self._faq_category_add,
            "/faq_category_toggle": self._faq_category_toggle,
            "/faq_category_set": self._faq_category_set,
            "/faq_category_delete": self._faq_category_delete,
            "/faqs": self._faqs,
            "/faq_add": self._faq_add,
            "/faq_toggle": self._faq_toggle,
            "/faq_set": self._faq_set,
            "/faq_delete": self._faq_delete,
            "/broadcast_all": self._broadcast_all,
            "/broadcast_joined": self._broadcast_joined,
            "/broadcast_product": self._broadcast_product,
            "/report": self._report,
            "/rewards": self._rewards,
            "/reward_add": self._reward_add,
            "/reward_toggle": self._reward_toggle,
        }
        missing = DOCUMENTED_COMMANDS - self._handlers.keys()
        if missing:  # pragma: no cover - guards future edits to the help file.
            raise RuntimeError(f"admin command handlers are missing: {sorted(missing)}")

    # -- Entry points -----------------------------------------------------

    def handles_command(self, text: str) -> bool:
        """Return whether a slash message belongs to the admin command surface."""

        command, _ = split_command(str(text or ""))
        return command in self._handlers

    def handle(self, message: dict[str, Any], user: dict[str, Any], admin: dict[str, Any]) -> bool:
        text = message.get("text")
        text = text if isinstance(text, str) else ""
        role = self._active_role(admin)
        if role is None:
            return False

        chat_id = self._chat_id(message, user)
        is_private = message.get("chat", {}).get("type", "private") == "private"
        if text.strip() == "/cancel":
            state = self._get_state(user)
            if not state or not str(state.get("state", "")).startswith("admin:"):
                return False
            if not is_private:
                self._send(chat_id, "فرمان‌های مدیریت فقط در گفت‌وگوی خصوصی قابل استفاده‌اند.")
                return True
            self.db.clear_user_state(int(user["id"]))
            self._send(chat_id, "عملیات مدیریت لغو شد.")
            return True

        if not text.lstrip().startswith("/"):
            state = self._get_state(user)
            if not state or not str(state.get("state", "")).startswith("admin:"):
                return False
            if not is_private:
                self._send(chat_id, "اطلاعات مدیریت را فقط در گفت‌وگوی خصوصی ارسال کنید.")
                return True
            try:
                return self._handle_state(message, text, user, admin, state=state)
            except _ADMIN_INPUT_ERRORS as exc:
                self._send(chat_id, f"خطا: {escape(exc)}")
                return True
            except TelegramError:
                self.log.warning("Telegram rejected an admin state response")
                return True
            except DatabaseError as exc:
                message["_admin_update_retry"] = True
                self._retry_feedback(
                    message, chat_id=chat_id, text=f"خطای موقت: {escape(exc)}"
                )
                return True
            except Exception:
                message["_admin_update_retry"] = True
                self.log.exception("Unhandled admin state failure")
                self._retry_feedback(
                    message,
                    chat_id=chat_id,
                    text="ثبت اطلاعات با خطای موقت روبه‌رو شد و دوباره تلاش می‌شود.",
                )
                return True

        command, rest = split_command(text)
        handler = self._handlers.get(command)
        if handler is None:
            return False
        if not is_private:
            self._send(chat_id, "فرمان‌های مدیریت فقط در گفت‌وگوی خصوصی قابل استفاده‌اند.")
            return True
        if role == "support" and command not in SUPPORT_COMMANDS:
            self._send(chat_id, "این فرمان در سطح دسترسی پشتیبان مجاز نیست.")
            return True
        try:
            handler(rest, message, user, admin)
        except _ADMIN_INPUT_ERRORS as exc:
            self._send(chat_id, f"خطا: {escape(exc)}")
        except TelegramError:
            self.log.warning(
                "Telegram rejected an admin command response",
                extra={"command": command},
            )
        except DatabaseError as exc:
            message["_admin_update_retry"] = True
            self._retry_feedback(
                message, chat_id=chat_id, text=f"خطای موقت: {escape(exc)}"
            )
        except Exception:
            message["_admin_update_retry"] = True
            self.log.exception("Unhandled admin command failure", extra={"command": command})
            self._retry_feedback(
                message,
                chat_id=chat_id,
                text="اجرای فرمان با خطای موقت روبه‌رو شد و دوباره تلاش می‌شود.",
            )
        return True

    def handle_state(
        self,
        message: dict[str, Any],
        user: dict[str, Any],
        admin: dict[str, Any],
        state: dict[str, Any],
    ) -> bool:
        """Handle a non-command admin conversation state for ``bot.py``."""

        if self._active_role(admin) is None or not str(state.get("state", "")).startswith("admin:"):
            return False
        if message.get("chat", {}).get("type", "private") != "private":
            self._send(self._chat_id(message, user), "اطلاعات مدیریت را فقط در گفت‌وگوی خصوصی ارسال کنید.")
            return True
        text = str(message.get("text") or message.get("caption") or "")
        try:
            return self._handle_state(message, text, user, admin, state=state)
        except _ADMIN_INPUT_ERRORS as exc:
            self._send(self._chat_id(message, user), f"خطا: {escape(exc)}")
        except TelegramError:
            self.log.warning("Telegram rejected an admin state response")
        except DatabaseError as exc:
            # Preserve the started journal row and conversation state. The
            # identical Telegram update can then safely resume its frozen
            # effect after a transient repository failure.
            message["_admin_update_retry"] = True
            self._retry_feedback(
                message,
                chat_id=self._chat_id(message, user),
                text=f"خطای موقت: {escape(exc)}",
            )
        except Exception:
            message["_admin_update_retry"] = True
            self.log.exception("Unhandled admin state failure")
            self._retry_feedback(
                message,
                chat_id=self._chat_id(message, user),
                text="ثبت اطلاعات با خطای موقت روبه‌رو شد و دوباره تلاش می‌شود.",
            )
        return True

    def handle_callback(
        self,
        data: str,
        query: dict[str, Any],
        user: dict[str, Any],
        admin: dict[str, Any],
    ) -> bool:
        if not data.startswith("adm:"):
            return False
        callback_id = str(query.get("id") or "")
        role = self._active_role(admin)
        if role is None:
            self._answer(callback_id, "دسترسی کافی نیست.", show_alert=True)
            return True

        if data.startswith("adm:broadcast:"):
            return self._handle_broadcast_callback(data, query, user, admin)

        chat_id = self._callback_chat_id(query, user)
        callback_message = {"chat": {"id": chat_id, "type": "private"}}
        try:
            if data.startswith("adm:ui:"):
                return self.button_ui.callback(data, query, user, admin)
            if data == "adm:orders":
                self._orders("", callback_message, user, admin)
                self._answer(callback_id, "فهرست سفارش‌ها ارسال شد.")
                return True
            if data == "adm:tickets":
                self._tickets("", callback_message, user, admin)
                self._answer(callback_id, "فهرست تیکت‌ها ارسال شد.")
                return True
            if data == "adm:users":
                self._send_users_panel(chat_id)
                self._answer(callback_id, "فهرست کاربران ارسال شد.")
                return True
            if data == "adm:settings":
                if role not in {"owner", "admin"}:
                    self._answer(callback_id, "این بخش برای پشتیبان مجاز نیست.", show_alert=True)
                    return True
                self._send_settings_panel(chat_id)
                self._answer(callback_id, "تنظیمات ارسال شد.")
                return True
            if data.startswith("adm:complete:"):
                if role not in {"owner", "admin"}:
                    self._answer(
                        callback_id,
                        "تکمیل سفارش برای پشتیبان مجاز نیست.",
                        show_alert=True,
                    )
                    return True
                parts = data.split(":")
                if len(parts) != 3 or not parts[2].isdigit() or int(parts[2]) < 1:
                    raise AdminInputError("شناسه سفارش نامعتبر است.")
                order = self.db.get_order(int(parts[2]))
                if order is None:
                    raise AdminInputError("سفارش پیدا نشد.")
                if order.get("product_type_snapshot") != "manual":
                    raise AdminInputError("تکمیل دستی فقط برای سفارش محصول manual مجاز است.")
                product = self.db.get_product(int(order["product_id"]))
                completion = str(
                    (product or {}).get("completion_text")
                    or "اشتراک شما با موفقیت فعال شد."
                )
                notification = (
                    "سفارش شما تکمیل شد."
                    f"\nشماره سفارش: <code>{escape(order['order_number'])}</code>"
                    f"\n\n{render_rich_text(completion)}"
                )
                notification_key = f"order:{order['id']}:manual-completion-notice"
                updated = self.db.complete_order(
                    int(order["id"]),
                    completion,
                    outbound_body=notification,
                    outbound_idempotency_key=notification_key,
                )
                target = self.db.get_user(int(order["user_id"]))
                self._deliver_prequeued_notification(
                    target,
                    notification,
                    idempotency_key=notification_key,
                )
                self._answer(callback_id, "سفارش تکمیل شد.")
                self._send(
                    chat_id,
                    f"سفارش <code>{escape(updated['order_number'])}</code> تکمیل شد.",
                )
                return True
            if data.startswith("adm:payok:") or data.startswith("adm:payno:"):
                if role not in {"owner", "admin"}:
                    self._answer(callback_id, "تأیید یا رد پرداخت برای پشتیبان مجاز نیست.", show_alert=True)
                    return True
                parts = data.split(":")
                if len(parts) != 3 or not parts[2].isdigit() or int(parts[2]) < 1:
                    raise AdminInputError("شناسه پرداخت نامعتبر است.")
                payment = self.db.get_payment(int(parts[2]))
                if payment is None:
                    raise AdminInputError("پرداخت پیدا نشد.")
                if parts[1] == "payok":
                    self._approve_payment_record(payment, int(admin["id"]), source="admin_callback")
                    self._answer(callback_id, "پرداخت تأیید شد.")
                    self._send(chat_id, "پرداخت تأیید شد.")
                else:
                    reason = "فیش پرداخت توسط مدیریت تأیید نشد."
                    self._reject_payment_record(
                        payment,
                        reason,
                        int(admin["id"]),
                        source="admin_callback",
                    )
                    self._answer(callback_id, "پرداخت رد شد.")
                    self._send(chat_id, f"پرداخت رد شد.\nدلیل: {escape(reason)}")
                return True
            self._answer(callback_id, "عملیات مدیریت شناخته نشد.", show_alert=True)
        except _ADMIN_INPUT_ERRORS as exc:
            if data.startswith("adm:ui:"):
                self._send(chat_id, escape(self.button_ui.friendly_error(str(exc))),
                           inline_keyboard(self.button_ui.navigation()))
            else:
                self._answer(callback_id, str(exc), show_alert=True)
        except TelegramError:
            self.log.warning(
                "Telegram rejected an admin callback response",
                extra={"callback_data": data},
            )
        except DatabaseError as exc:
            query["_admin_update_retry"] = True
            self._retry_feedback(
                query, callback_id=callback_id, text=f"خطای موقت: {exc}"
            )
        except Exception:
            query["_admin_update_retry"] = True
            self.log.exception("Unhandled admin callback failure", extra={"callback_data": data})
            self._retry_feedback(
                query,
                callback_id=callback_id,
                text="عملیات با خطای موقت روبه‌رو شد و دوباره تلاش می‌شود.",
            )
        return True

    def _handle_broadcast_callback(
        self,
        data: str,
        query: dict[str, Any],
        user: dict[str, Any],
        admin: dict[str, Any],
    ) -> bool:
        """Confirm or cancel a previously previewed broadcast."""

        callback_id = str(query.get("id") or "")
        if self._active_role(admin) not in {"owner", "admin"}:
            self._answer(callback_id, "دسترسی کافی نیست.", show_alert=True)
            return True
        state = self._get_state(user)
        parts = data.split(":", 3)
        action = parts[2] if len(parts) > 2 else ""
        token = parts[3] if len(parts) > 3 else ""
        expected = (state or {}).get("data", {}).get("token")
        if not state or state.get("state") != "admin:broadcast" or not token or token != expected:
            self._answer(callback_id, "این درخواست منقضی شده است.", show_alert=True)
            return True
        chat_id = self._callback_chat_id(query, user)
        if action == "cancel":
            self.db.clear_user_state(int(user["id"]))
            self._answer(callback_id, "ارسال لغو شد.")
            self._send(chat_id, "ارسال گروهی لغو شد.",
                       inline_keyboard(self.button_ui.navigation("broadcast")))
            return True
        if action != "confirm":
            self._answer(callback_id, "عملیات نامعتبر است.", show_alert=True)
            return True
        payload = state["data"]
        try:
            queued = self._enqueue_broadcast(
                payload["audience"],
                payload["body"],
                int(admin["id"]),
                actor_user_id=int(user["id"]),
                batch_token=token,
            )
            self.db.clear_user_state(int(user["id"]))
            self._answer(callback_id, "ارسال در صف قرار گرفت.")
            self._send(
                chat_id,
                f"پیام گروهی برای {int(queued['queued_count']):,} مخاطب در صف ارسال قرار گرفت."
                f"\nشناسه صف: <code>{escape(queued.get('id', '—'))}</code>",
                inline_keyboard(self.button_ui.navigation("broadcast")),
            )
        except _ADMIN_INPUT_ERRORS as exc:
            self._answer(callback_id, "ثبت ارسال ناموفق بود.", show_alert=True)
            self._send(chat_id, f"خطا: {escape(exc)}")
        except TelegramError:
            self.log.warning("Telegram rejected an admin broadcast response")
        except DatabaseError as exc:
            query["_admin_update_retry"] = True
            self._retry_feedback(
                query,
                callback_id=callback_id,
                text=f"خطای موقت: {exc}",
            )
        except Exception:
            query["_admin_update_retry"] = True
            self.log.exception("Unhandled admin broadcast callback failure")
            self._retry_feedback(
                query,
                callback_id=callback_id,
                text="ثبت ارسال با خطای موقت روبه‌رو شد و دوباره تلاش می‌شود.",
            )
        return True

    # -- Generic collaborators -------------------------------------------

    @staticmethod
    def _active_role(admin: Mapping[str, Any] | None) -> str | None:
        if not admin or not bool(admin.get("is_active", 1)):
            return None
        role = str(admin.get("role") or "")
        return role if role in {"owner", "admin", "support"} else None

    @staticmethod
    def _chat_id(message: Mapping[str, Any], user: Mapping[str, Any]) -> int:
        raw = (message.get("chat") or {}).get("id") or user.get("chat_id")
        if raw is None:
            raise AdminInputError("شناسه گفت‌وگو مشخص نیست.")
        return int(raw)

    @staticmethod
    def _callback_chat_id(query: Mapping[str, Any], user: Mapping[str, Any]) -> int:
        message = query.get("message") or {}
        chat = message.get("chat") or {}
        raw = chat.get("id") or user.get("chat_id")
        if raw is None:
            raise AdminInputError("شناسه گفت‌وگو مشخص نیست.")
        return int(raw)

    @staticmethod
    def _admin_update_id(event: Mapping[str, Any]) -> int | None:
        raw = event.get("_admin_update_id")
        try:
            return int(raw) if raw is not None else None
        except (TypeError, ValueError, OverflowError):
            return None

    @staticmethod
    def _admin_update_is_replay(event: Mapping[str, Any]) -> bool:
        return bool(event.get("_admin_update_replay"))

    def _admin_idempotency_key(
        self, event: Mapping[str, Any], effect: str
    ) -> str | None:
        update_id = self._admin_update_id(event)
        return f"admin-update:{update_id}:{effect}" if update_id is not None else None

    def _admin_toggle_target(
        self,
        event: Mapping[str, Any],
        effect_key: str,
        current: bool,
    ) -> bool:
        """Persist a toggle's desired target before changing domain state."""

        desired = not bool(current)
        update_id = self._admin_update_id(event)
        if update_id is None or not hasattr(
            self.db, "get_or_store_admin_update_effect"
        ):
            return desired
        return bool(
            self.db.get_or_store_admin_update_effect(
                update_id, effect_key, desired
            )
        )

    def _freeze_admin_state_effect(
        self,
        event: Mapping[str, Any],
        effect_key: str,
        value: Mapping[str, Any],
    ) -> None:
        update_id = self._admin_update_id(event)
        if update_id is None or not hasattr(
            self.db, "get_or_store_admin_update_effect"
        ):
            return
        stored = self.db.get_or_store_admin_update_effect(
            update_id, effect_key, dict(value)
        )
        if stored != dict(value):
            raise ConflictError("admin state replay payload changed")

    def _command_parts(self, rest: str, minimum: int) -> list[str]:
        if self._button_context is not None and self._button_context.get("parts") is not None:
            parts = list(self._button_context["parts"])
            if len(parts) < minimum:
                raise AdminInputError("اطلاعات فرم کامل نیست.")
            return parts
        return pipe_parts(rest, minimum)

    def _send(self, chat_id: int, text: str, reply_markup: Mapping[str, Any] | None = None) -> Any:
        if self._button_context is not None:
            self._button_context["responses"].append((text, reply_markup))
            return None
        return self.telegram.send_message(
            chat_id,
            clamp_text(text),
            parse_mode="HTML",
            reply_markup=reply_markup,
        )

    def _retry_feedback(
        self,
        event: Mapping[str, Any],
        *,
        text: str,
        chat_id: int | None = None,
        callback_id: str | None = None,
    ) -> None:
        """Report the first transient failure without making retry depend on Telegram."""

        if self._admin_update_is_replay(event):
            return
        if callback_id:
            try:
                self._answer(callback_id, text, show_alert=True)
            except TelegramError:
                self.log.warning(
                    "Could not report transient admin callback failure",
                    extra={"admin_update_id": self._admin_update_id(event)},
                )
        if chat_id is not None:
            try:
                self._send(chat_id, text)
            except TelegramError:
                self.log.warning(
                    "Could not report transient admin command failure",
                    extra={"admin_update_id": self._admin_update_id(event)},
                )

    def _send_blocks(
        self, chat_id: int, blocks: Sequence[str], *, maximum: int = 3_800
    ) -> None:
        """Send every HTML block without silently dropping older history."""

        chunks: list[str] = []
        current = ""
        for raw_block in blocks:
            block = clamp_text(str(raw_block), maximum)
            candidate = f"{current}\n\n{block}" if current else block
            if current and len(candidate) > maximum:
                chunks.append(current)
                current = block
            else:
                current = candidate
        if current:
            chunks.append(current)
        for chunk in chunks:
            self._send(chat_id, chunk)

    def _send_page(
        self,
        chat_id: int,
        *,
        title: str,
        rows: Sequence[str],
        total: int,
        page: int,
        pages: int,
        command_prefix: str,
        empty_text: str,
        tail: Sequence[str] = (),
    ) -> None:
        blocks = [
            f"<b>{escape(title)}</b>\nصفحه {page:,} از {pages:,} | مجموع: {total:,}"
        ]
        blocks.extend(rows or [empty_text])
        if self._button_context is not None:
            self._button_context["page"] = (page, pages)
            self._send_blocks(chat_id, blocks)
            return
        blocks.extend(tail)
        navigation: list[str] = []
        if page > 1:
            navigation.append(
                f"قبلی: <code>{escape(command_prefix)} {page - 1}</code>"
            )
        if page < pages:
            navigation.append(
                f"بعدی: <code>{escape(command_prefix)} {page + 1}</code>"
            )
        if navigation:
            blocks.append("\n".join(navigation))
        self._send_blocks(chat_id, blocks)

    def _management_rows(
        self,
        page: int,
        query: str,
        parameters: Sequence[Any] = (),
    ) -> tuple[list[dict[str, Any]], int, int]:
        """Page an internal read-only listing with the same count/filter query."""

        count = self._query_one(
            f"SELECT COUNT(*) AS total FROM ({query})", parameters
        )
        total = int((count or {}).get("total") or 0)
        pages, offset = _page_bounds(total, page)
        rows = self._query(
            f"{query} LIMIT ? OFFSET ?",
            (*parameters, _ADMIN_PAGE_SIZE, offset),
        )
        return rows, total, pages

    def _answer(self, callback_id: str, text: str = "", *, show_alert: bool = False) -> Any:
        if not callback_id:
            return None
        return self.telegram.answer_callback_query(callback_id, text or None, show_alert=show_alert)

    def _send_users_panel(
        self,
        chat_id: int,
        *,
        blocked: bool | None = None,
        page: int = 1,
    ) -> None:
        total = self.db.count_users(blocked=blocked)
        pages, offset = _page_bounds(total, page)
        users = self.db.list_users(
            blocked=blocked, limit=_ADMIN_PAGE_SIZE, offset=offset
        )
        title = "کاربران مسدود" if blocked is True else "کاربران فعال" if blocked is False else "آخرین کاربران"
        mode = "blocked" if blocked is True else "active" if blocked is False else "all"
        self._send_user_rows(
            chat_id,
            users,
            title,
            total=total,
            page=page,
            pages=pages,
            command_prefix=f"/users {mode}",
        )

    def _send_user_rows(
        self,
        chat_id: int,
        users: Sequence[Mapping[str, Any]],
        title: str,
        *,
        total: int,
        page: int,
        pages: int,
        command_prefix: str,
        unblocked_status: str = "فعال",
    ) -> None:
        rows: list[str] = []
        for item in users:
            username = f"@{escape(item['username'])}" if item.get("username") else "بدون username"
            status = "مسدود" if item.get("is_blocked") else unblocked_status
            rows.append(
                f"<code>{item.get('chat_id') or '—'}</code> | {username} | {status}"
            )
        self._send_page(
            chat_id,
            title=title,
            rows=rows,
            total=total,
            page=page,
            pages=pages,
            command_prefix=command_prefix,
            empty_text="کاربری ثبت نشده است.",
            tail=("جزئیات کاربر: <code>/user CHAT_ID</code>",),
        )

    def _send_settings_panel(self, chat_id: int, *, button_mode: bool = False) -> None:
        def enabled(key: str, default: bool = False) -> str:
            return "فعال" if bool(self.db.get_setting(key, default)) else "غیرفعال"

        card_number = str(self.db.get_setting("card_number", "") or "")
        masked_card = (
            f"{card_number[:4]}********{card_number[-4:]}"
            if len(card_number) >= 8
            else ("ثبت شده" if card_number else "ثبت نشده")
        )
        card_owner = str(self.db.get_setting("card_owner", "") or "ثبت نشده")
        channel_url = str(self.db.get_setting("main_channel_url", "") or "ثبت نشده")
        self._send(
            chat_id,
            "<b>تنظیمات ربات</b>"
            f"\nربات: {enabled('bot_enabled', True)}"
            f"\nحالت تعمیرات: {'غیرفعال' if bool(self.db.get_setting('bot_enabled', True)) else 'فعال'}"
            f"\nکیف پول: {enabled('payment_wallet_enabled', True)}"
            f"\nکارت: {enabled('payment_card_enabled', True)}"
            f"\nارز دیجیتال: {enabled('payment_crypto_enabled')}"
            f"\nشماره کارت: <code>{escape(masked_card)}</code>"
            f"\nصاحب حساب: {escape(card_owner)}"
            f"\nکانال اصلی: {escape(channel_url)}"
            + ("\n\nبرای ویرایش از دکمه‌های زیر انتخاب کنید." if button_mode
               else "\n\nویرایش تنظیمات: <code>/admin_help</code>"),
        )

    def _notify(self, chat_id: int | None, text: str) -> Any:
        if chat_id is None:
            return None
        if self.notify_user is not None:
            return self.notify_user(int(chat_id), text)
        return self.telegram.send_message(int(chat_id), text, parse_mode="HTML")

    def _notify_target(
        self,
        target: Mapping[str, Any] | None,
        text: str,
        *,
        idempotency_key: str,
    ) -> Any:
        """Persist a target notice before its immediate delivery attempt."""

        if target is None:
            return False
        self.db.queue_outbound_message(
            text,
            recipient_user_id=int(target["id"]),
            idempotency_key=idempotency_key,
        )
        return self._deliver_prequeued_notification(
            target,
            text,
            idempotency_key=idempotency_key,
        )

    def _deliver_prequeued_notification(
        self,
        target: Mapping[str, Any] | None,
        text: str,
        *,
        idempotency_key: str,
    ) -> Any:
        """Send an outbox row that was committed atomically with its domain change."""

        queued = self.db.get_outbound_message_by_idempotency_key(idempotency_key)
        if queued is None:
            raise AdminIntegrationError("پیام پایدار تحویل در تراکنش تکمیل ثبت نشد.")
        if queued.get("status") == "sent":
            return True
        if queued.get("status") != "queued":
            return False
        if target is None or target.get("chat_id") is None:
            return False
        claimed = self.db.claim_outbound_message(int(queued["id"]))
        if claimed is None:
            return False
        try:
            result = self._notify(int(target["chat_id"]), text)
        except Exception:
            self.db.mark_outbound_message(
                int(queued["id"]),
                "queued",
                error_text="immediate delivery interrupted",
            )
            raise
        if result is not False:
            telegram_message_id = (
                result.get("message_id") if isinstance(result, Mapping) else None
            )
            self.db.mark_outbound_message(
                int(queued["id"]),
                success=True,
                telegram_message_id=(
                    int(telegram_message_id)
                    if telegram_message_id is not None
                    else None
                ),
            )
        else:
            self.db.mark_outbound_message(
                int(queued["id"]),
                "queued",
                error_text="immediate delivery failed",
            )
        return result is not False

    def _get_state(self, user: Mapping[str, Any]) -> dict[str, Any] | None:
        return self.db.get_user_state(int(user["id"]))

    def _public(self, name: str) -> Callable[..., Any] | None:
        candidate = getattr(self.db, name, None)
        return candidate if callable(candidate) else None

    def _connect(self) -> sqlite3.Connection:
        path = getattr(self.db, "path", None)
        if path is None:
            raise AdminIntegrationError("متد لازم در Database موجود نیست و مسیر دیتابیس هم در دسترس نیست.")
        connection = sqlite3.connect(Path(path), timeout=5, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def _query(self, query: str, parameters: Sequence[Any] = ()) -> list[dict[str, Any]]:
        with closing(self._connect()) as connection:
            return [dict(row) for row in connection.execute(query, parameters).fetchall()]

    def _query_one(self, query: str, parameters: Sequence[Any] = ()) -> dict[str, Any] | None:
        rows = self._query(query, parameters)
        return rows[0] if rows else None

    def _execute(self, query: str, parameters: Sequence[Any] = ()) -> int:
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                cursor = connection.execute(query, parameters)
                connection.commit()
                return int(cursor.lastrowid or cursor.rowcount)
            except Exception:
                connection.rollback()
                raise

    def _handle_state(
        self,
        message: Mapping[str, Any],
        text: str,
        user: Mapping[str, Any],
        admin: Mapping[str, Any],
        *,
        state: Mapping[str, Any] | None = None,
    ) -> bool:
        state = state or self._get_state(user)
        if not state or not str(state.get("state", "")).startswith("admin:"):
            return False
        if state["state"] == "admin:ui":
            return self.button_ui.message(message, user, admin)
        if state["state"] == "admin:catalog":
            return self.button_ui.catalog.message(message, user, admin)
        if state["state"] == "admin:joins":
            return self.button_ui.joins.message(message, user, admin)
        if state["state"] == "admin:layouts":
            return self.button_ui.layouts.message(message, user, admin)
        chat_id = self._chat_id(message, user)
        if state["state"] == "admin:inventory":
            if self._active_role(admin) not in {"owner", "admin"}:
                self.db.clear_user_state(int(user["id"]))
                self._send(chat_id, "دسترسی ثبت موجودی ندارید.")
                return True
            if not text.strip():
                self._send(chat_id, "اطلاعات موجودی باید به‌صورت متن ارسال شود یا /cancel را بزنید.")
                return True
            product_id = int(state.get("data", {}).get("product_id"))
            self._freeze_admin_state_effect(
                message,
                f"admin-state:inventory:{int(user['id'])}",
                {"product_id": product_id},
            )
            item = self.db.add_inventory_item(
                product_id,
                text,
                source_admin_update_id=self._admin_update_id(message),
            )
            if self._admin_update_id(message) is None:
                # Direct/controller callers do not have the application-level
                # update journal that clears state after marking the journal
                # row complete. Preserve the long-standing standalone API
                # contract for those callers.
                self.db.clear_user_state(int(user["id"]))
            elif isinstance(message, dict):
                message["_admin_state_complete"] = True
            self._send(
                chat_id,
                "موجودی محرمانه با موفقیت ثبت شد."
                f"\nشناسه آیتم: <code>{escape(item.get('id', '—'))}</code>",
            )
            return True
        if state["state"] == "admin:inventory_edit":
            if self._active_role(admin) not in {"owner", "admin"}:
                self.db.clear_user_state(int(user["id"]))
                self._send(chat_id, "دسترسی ویرایش موجودی ندارید.")
                return True
            if not text.strip():
                self._send(chat_id, "اطلاعات جدید باید متنی باشد یا /cancel را بزنید.")
                return True
            item_id = int(state.get("data", {}).get("item_id"))
            self._freeze_admin_state_effect(
                message,
                f"admin-state:inventory-edit:{int(user['id'])}",
                {"item_id": item_id},
            )
            item = self.db.update_inventory_item_payload(item_id, text)
            if self._admin_update_id(message) is None:
                self.db.clear_user_state(int(user["id"]))
            elif isinstance(message, dict):
                message["_admin_state_complete"] = True
            self._send(
                chat_id,
                "اطلاعات محرمانه موجودی ویرایش شد."
                f"\nشناسه آیتم: <code>{item['id']}</code>",
            )
            return True
        if state["state"] == "admin:broadcast":
            if self._active_role(admin) not in {"owner", "admin"}:
                raise AdminInputError("دسترسی ارسال گروهی ندارید.")
            data = state.get("data", {})
            if data.get("ui_input") == self.button_ui._input_id(message):
                self._render_broadcast_preview(chat_id, data)
            else:
                self._send(chat_id, "برای تأیید یا لغو ارسال گروهی از دکمه‌های پیام پیش‌نمایش استفاده کنید.",
                           inline_keyboard(self.button_ui.navigation("broadcast")))
            return True
        return False

    # -- General settings and administrator identities -------------------

    def _help(self, _rest: str, message: dict[str, Any], user: dict[str, Any], admin: dict[str, Any]) -> None:
        chat_id = self._chat_id(message, user)
        if admin["role"] == "support":
            self._send(chat_id, SUPPORT_HELP)
            return
        for content in ADMIN_HELP_PARTS:
            self._send(chat_id, content)

    def _bot_on(self, rest: str, message: dict[str, Any], user: dict[str, Any], _admin: dict[str, Any]) -> None:
        if rest:
            raise AdminInputError("فرمان /bot_on آرگومان نمی‌گیرد.")
        self.db.set_setting("bot_enabled", True)
        self._send(self._chat_id(message, user), "ربات فعال شد و حالت تعمیرات غیرفعال شد.")

    def _bot_off(self, rest: str, message: dict[str, Any], user: dict[str, Any], _admin: dict[str, Any]) -> None:
        if rest:
            raise AdminInputError("فرمان /bot_off آرگومان نمی‌گیرد.")
        self.db.set_setting("bot_enabled", False)
        self._send(self._chat_id(message, user), "ربات غیرفعال شد و در حالت تعمیرات قرار گرفت.")

    def _set_card(self, rest: str, message: dict[str, Any], user: dict[str, Any], _admin: dict[str, Any]) -> None:
        parts = self._command_parts(rest, 2)
        if len(parts) != 2:
            raise AdminInputError("نمونه: /set_card شماره کارت | نام صاحب حساب")
        card_number = parts[0].replace(" ", "").replace("-", "")
        if not card_number.isdigit() or not 16 <= len(card_number) <= 19:
            raise AdminInputError("شماره کارت باید ۱۶ تا ۱۹ رقم باشد.")
        self.db.set_setting("card_number", card_number)
        self.db.set_setting("card_owner", parts[1])
        self._send(self._chat_id(message, user), "اطلاعات کارت پرداخت به‌روزرسانی شد.")

    def _set_channel(self, rest: str, message: dict[str, Any], user: dict[str, Any], _admin: dict[str, Any]) -> None:
        url = rest.strip()
        if not is_safe_telegram_channel_url(url):
            raise AdminInputError("لینک کانال باید یک لینک معتبر HTTPS از دامنه t.me باشد.")
        self.db.set_setting("main_channel_url", url)
        self._send(self._chat_id(message, user), "لینک کانال اصلی ذخیره شد.")

    def _payment(self, rest: str, message: dict[str, Any], user: dict[str, Any], _admin: dict[str, Any]) -> None:
        parts = rest.split()
        if len(parts) != 2 or parts[0].lower() not in {"wallet", "card", "crypto"}:
            raise AdminInputError("نمونه: /payment wallet|card|crypto on|off")
        method = parts[0].lower()
        enabled = _on_off(parts[1])
        if enabled and method == "card" and not (
            str(self.db.get_setting("card_number", "") or "").strip()
            and str(self.db.get_setting("card_owner", "") or "").strip()
        ):
            raise AdminInputError(
                "پیش از فعال‌کردن کارت، /set_card را کامل کنید."
            )
        if enabled and method == "crypto" and not str(
            getattr(self.settings, "plisio_api_key", "") or ""
        ).strip():
            raise AdminInputError(
                "برای فعال‌کردن پرداخت ارزی، PLISIO_API_KEY باید در محیط اجرا تنظیم شود."
            )
        self.db.set_setting(f"payment_{method}_enabled", enabled)
        state = "فعال" if enabled else "غیرفعال"
        self._send(self._chat_id(message, user), f"روش پرداخت <code>{method}</code> {state} شد.")

    def _joins(self, rest: str, message: dict[str, Any], user: dict[str, Any], _admin: dict[str, Any]) -> None:
        if rest.strip():
            raise AdminInputError("فرمان /joins آرگومان نمی‌گیرد.")
        channels = self.db.list_force_join_channels(active_only=False)
        blocks = ["<b>کانال‌های عضویت اجباری</b>"]
        for channel in channels:
            blocks.append(
                f"<code>{channel['id']}</code> | "
                f"{escape(channel['telegram_chat_id'])} | "
                f"{escape(clamp_text(str(channel['title']), 512))} | "
                f"{'فعال' if channel['is_active'] else 'غیرفعال'}"
            )
        if not channels:
            blocks.append("کانالی ثبت نشده است.")
        self._send_blocks(self._chat_id(message, user), blocks)

    def _validate_force_join_channel(self, telegram_chat_id: str) -> None:
        """Require a channel/supergroup where the bot can check membership."""

        api_call = getattr(self.telegram, "call", None)
        if not callable(api_call):
            return
        try:
            chat = api_call("getChat", {"chat_id": telegram_chat_id})
            if not isinstance(chat, Mapping) or chat.get("type") not in {
                "channel",
                "supergroup",
            }:
                raise AdminInputError(
                    "شناسه ارسالی باید به یک کانال یا سوپرگروه تلگرام اشاره کند."
                )
            me = api_call("getMe")
            membership = api_call(
                "getChatMember",
                {"chat_id": telegram_chat_id, "user_id": int(me["id"])},
            )
        except AdminInputError:
            raise
        except (TelegramError, KeyError, TypeError, ValueError) as exc:
            raise AdminInputError(
                "کانال از طریق Bot API قابل اعتبارسنجی نیست؛ عضویت و دسترسی ربات را بررسی کنید."
            ) from exc
        if not isinstance(membership, Mapping) or membership.get("status") not in {
            "administrator",
            "creator",
        }:
            raise AdminInputError("برای جوین اجباری، ربات باید مدیر کانال باشد.")

    def _join_add(self, rest: str, message: dict[str, Any], user: dict[str, Any], _admin: dict[str, Any]) -> None:
        parts = self._command_parts(rest, 3)
        if len(parts) != 3:
            raise AdminInputError("نمونه: /join_add @channel | عنوان | لینک عضویت")
        if not re.fullmatch(r"(?:@[A-Za-z0-9_]{5,32}|-100[0-9]{6,})", parts[0]):
            raise AdminInputError("شناسه کانال باید @username یا شناسه عددی -100... باشد.")
        if not is_safe_telegram_invite_url(parts[2]):
            raise AdminInputError("لینک عضویت باید لینک HTTPS معتبر تلگرام باشد.")
        self._validate_force_join_channel(parts[0])
        channel = self.db.upsert_force_join_channel(
            parts[0],
            parts[1],
            invite_url=parts[2],
            active=True,
        )
        self._send(
            self._chat_id(message, user),
            f"کانال عضویت اجباری ثبت شد. شناسه: <code>{channel['id']}</code>",
        )

    def _join_toggle(self, rest: str, message: dict[str, Any], user: dict[str, Any], _admin: dict[str, Any]) -> None:
        channel_id = _as_int(rest)
        channels = self.db.list_force_join_channels(active_only=False)
        channel = next((item for item in channels if int(item["id"]) == channel_id), None)
        if channel is None:
            raise AdminInputError("کانال پیدا نشد.")
        active = self._admin_toggle_target(
            message,
            f"force-join:{channel_id}:active",
            bool(channel["is_active"]),
        )
        if active:
            self._validate_force_join_channel(str(channel["telegram_chat_id"]))
        method = self._public("set_force_join_channel_active")
        if method is not None:
            method(channel_id, active)
        else:
            changed = self._execute(
                "UPDATE force_join_channels SET is_active = ?, updated_at = ? WHERE id = ?",
                (int(active), _utc_timestamp(), channel_id),
            )
            if changed != 1:
                raise AdminInputError("کانال پیدا نشد.")
        self._send(self._chat_id(message, user), f"وضعیت کانال به {'فعال' if active else 'غیرفعال'} تغییر کرد.")

    def _join_delete(self, rest: str, message: dict[str, Any], user: dict[str, Any], _admin: dict[str, Any]) -> None:
        channel_id = _as_int(rest)
        existing = next(
            (
                item
                for item in self.db.list_force_join_channels(active_only=False)
                if int(item["id"]) == channel_id
            ),
            None,
        )
        if existing is None and self._admin_update_is_replay(message):
            self._send(self._chat_id(message, user), "کانال عضویت اجباری قبلاً حذف شده است.")
            return
        method = self._public("delete_force_join_channel")
        if method is not None:
            deleted = method(channel_id)
        else:
            deleted = self._execute("DELETE FROM force_join_channels WHERE id = ?", (channel_id,)) == 1
        if not deleted:
            raise AdminInputError("کانال پیدا نشد.")
        self._send(self._chat_id(message, user), "کانال عضویت اجباری حذف شد.")

    def _backup(self, rest: str, message: dict[str, Any], user: dict[str, Any], admin: dict[str, Any]) -> None:
        if rest:
            raise AdminInputError("فرمان /backup آرگومان نمی‌گیرد.")
        if admin.get("role") != "owner":
            raise AdminInputError("دریافت بکاپ کامل فقط برای مالک مجاز است.")
        path = self._create_backup()
        self.telegram.send_document(
            self._chat_id(message, user),
            path,
            caption="نسخه پشتیبان کامل دیتابیس",
            parse_mode="HTML",
        )

    def _create_backup(self) -> Path:
        database_path = Path(getattr(self.db, "path", Path.cwd() / "database.sqlite3"))
        data_dir = Path(getattr(self.settings, "data_dir", database_path.parent))
        destination_dir = data_dir / "backups"
        public = self._public("create_backup")
        if public is not None:
            # Passing the directory delegates naming and POSIX permission
            # hardening to the repository's online-backup implementation.
            result = public(destination_dir)
            if isinstance(result, Mapping):
                result = result.get("destination_path") or result.get("path")
            path = Path(result)
            if not path.is_file():
                raise AdminIntegrationError("فایل خروجی بکاپ ساخته نشد.")
            return path

        destination_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        if os.name == "posix":
            destination_dir.chmod(0o700)
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        target = destination_dir / f"alone-account-{stamp}-{secrets.token_hex(3)}.sqlite3"
        source_path = database_path
        if not source_path.is_file():
            raise AdminIntegrationError("فایل دیتابیس برای بکاپ پیدا نشد.")
        file_descriptor = os.open(
            target,
            os.O_CREAT | os.O_EXCL | os.O_RDWR,
            0o600,
        )
        try:
            if os.name == "posix":
                os.fchmod(file_descriptor, 0o600)
        finally:
            os.close(file_descriptor)
        try:
            with closing(sqlite3.connect(source_path)) as source, closing(
                sqlite3.connect(target)
            ) as destination:
                source.backup(destination)
        except Exception:
            target.unlink(missing_ok=True)
            raise
        return target

    def _admins(self, rest: str, message: dict[str, Any], user: dict[str, Any], _admin: dict[str, Any]) -> None:
        page = _page_number(rest) if rest else 1
        admins, total, pages = self._management_rows(
            page,
            "SELECT * FROM admins ORDER BY CASE role WHEN 'owner' THEN 0 WHEN 'admin' THEN 1 ELSE 2 END, id",
        )
        lines = []
        for item in admins:
            if not item.get("identity_verified_at"):
                status = "در انتظار تأیید هویت"
            else:
                status = "فعال" if item["is_active"] else "غیرفعال"
            lines.append(
                f"<code>{item['chat_id']}</code> | @{escape(item['username'])} | "
                f"{escape(item['role'])} | {status}"
            )
        self._send_page(
            self._chat_id(message, user), title="مدیران", rows=lines,
            total=total, page=page, pages=pages, command_prefix="/admins",
            empty_text="موردی ثبت نشده است.",
        )

    def _admin_add(self, rest: str, message: dict[str, Any], user: dict[str, Any], admin: dict[str, Any]) -> None:
        parts = rest.split()
        if len(parts) != 3:
            raise AdminInputError("افزودن مدیر به هر دو مقدار username و CHAT_ID نیاز دارد.")
        username = normalize_username(parts[0])
        chat_id = _as_int(parts[1], "chat_id")
        role = parts[2].lower()
        if not _USERNAME_RE.fullmatch(username):
            raise AdminInputError("username تلگرام باید ۵ تا ۳۲ نویسه انگلیسی، عدد یا زیرخط باشد.")
        if chat_id <= 0:
            raise AdminInputError("chat_id مدیر باید شناسه مثبت گفت‌وگوی خصوصی باشد.")
        if role not in {"owner", "admin", "support"}:
            raise AdminInputError("نقش باید owner، admin یا support باشد.")
        if role == "owner" and admin["role"] != "owner":
            raise AdminInputError("فقط مالک می‌تواند مالک دیگری اضافه کند.")
        existing = [
            item
            for item in self.db.list_admins(active_only=False)
            if normalize_username(item.get("username")) == username
            or (item.get("chat_id") is not None and int(item["chat_id"]) == chat_id)
        ]
        if admin["role"] != "owner" and any(item["role"] == "owner" for item in existing):
            raise AdminInputError("فقط مالک می‌تواند اطلاعات یا نقش یک مالک را تغییر دهد.")
        if role != "owner" and any(
            item["role"] == "owner" and bool(item["is_active"]) for item in existing
        ):
            active_owners = [
                item
                for item in self.db.list_admins(active_only=True)
                if item["role"] == "owner"
            ]
            if len(active_owners) <= 1:
                raise AdminInputError("آخرین مالک فعال را نمی‌توان تنزل نقش داد.")
        added = self.db.add_admin(
            username,
            chat_id,
            role=role,
            active=True,
            created_by_admin_id=int(admin["id"]),
        )
        suffix = (
            " و هویت تلگرام او تأیید شد."
            if added.get("identity_verified_at")
            else "؛ دسترسی پس از ارسال /start از همان chat_id با همین username فعال می‌شود."
        )
        self._send(
            self._chat_id(message, user),
            f"مدیر @{escape(added['username'])} با chat_id "
            f"<code>{added['chat_id']}</code> ثبت شد{suffix}",
        )

    def _admin_enable(self, rest: str, message: dict[str, Any], user: dict[str, Any], admin: dict[str, Any]) -> None:
        self._set_admin_enabled(rest, True, message, user, admin)

    def _admin_disable(self, rest: str, message: dict[str, Any], user: dict[str, Any], admin: dict[str, Any]) -> None:
        self._set_admin_enabled(rest, False, message, user, admin)

    def _set_admin_enabled(
        self,
        rest: str,
        active: bool,
        message: dict[str, Any],
        user: dict[str, Any],
        actor: dict[str, Any],
    ) -> None:
        chat_id = _as_int(rest, "chat_id")
        target = next(
            (
                item
                for item in self.db.list_admins(active_only=False)
                if item.get("chat_id") is not None
                and int(item["chat_id"]) == chat_id
            ),
            None,
        )
        if target is None:
            raise AdminInputError("مدیر پیدا نشد.")
        if target["role"] == "owner" and actor["role"] != "owner":
            raise AdminInputError("فقط مالک می‌تواند دسترسی مالک دیگری را تغییر دهد.")
        if not active and int(target["id"]) == int(actor["id"]):
            raise AdminInputError("نمی‌توانید دسترسی خودتان را غیرفعال کنید.")
        if not active and target["role"] == "owner":
            active_owners = [
                item
                for item in self.db.list_admins(active_only=True)
                if item["role"] == "owner"
            ]
            if len(active_owners) <= 1:
                raise AdminInputError("آخرین مالک فعال را نمی‌توان غیرفعال کرد.")
        self.db.set_admin_active(int(target["id"]), active)
        self._send(
            self._chat_id(message, user),
            f"دسترسی مدیر {'فعال' if active else 'غیرفعال'} شد.",
        )

    # -- Catalog and sensitive inventory ---------------------------------

    def _all_categories(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        pending = list(self.db.list_categories(parent_id=None, active_only=False))
        seen: set[int] = set()
        while pending:
            item = pending.pop(0)
            item_id = int(item["id"])
            if item_id in seen:
                continue
            seen.add(item_id)
            result.append(item)
            pending.extend(self.db.list_categories(parent_id=item_id, active_only=False))
        return result

    def _categories(self, rest: str, message: dict[str, Any], user: dict[str, Any], _admin: dict[str, Any]) -> None:
        page = _page_number(rest) if rest else 1
        categories, total, pages = self._management_rows(
            page, "SELECT * FROM categories ORDER BY sort_order, id"
        )
        lines = []
        for item in categories:
            marker = "فعال" if item["is_active"] else "غیرفعال"
            parent = f" | والد {item['parent_id']}" if item.get("parent_id") else ""
            icon = f"{escape(item['icon'])} " if item.get("icon") else ""
            description = (
                f" | {escape(clamp_text(str(item['description']), 120))}"
                if item.get("description")
                else ""
            )
            lines.append(
                f"<code>{item['id']}</code> | {icon}{escape(item['name'])}"
                f"{parent} | {marker}{description}"
            )
        self._send_page(
            self._chat_id(message, user), title="دسته‌ها", rows=lines,
            total=total, page=page, pages=pages, command_prefix="/categories",
            empty_text="دسته‌ای ثبت نشده است.",
        )

    def _category_add(self, rest: str, message: dict[str, Any], user: dict[str, Any], _admin: dict[str, Any]) -> None:
        if not rest.strip():
            raise AdminInputError("عنوان دسته الزامی است.")
        parts = self._command_parts(rest, 1)
        if len(parts) not in {1, 2, 3} or not parts[0]:
            raise AdminInputError("نمونه: /category_add عنوان | آیکون|0 | توضیح|0")
        if len(parts) > 2 and parts[2].lower().startswith("html:"):
            format_admin_text(parts[2])
        category = self.db.create_category(
            parts[0],
            source_admin_update_id=self._admin_update_id(message),
            icon=(parts[1] if len(parts) > 1 and parts[1] != "0" else None),
            description=(parts[2] if len(parts) > 2 and parts[2] != "0" else None),
        )
        self._send(self._chat_id(message, user), f"دسته ثبت شد. شناسه: <code>{category['id']}</code>")

    def _subcategory_add(self, rest: str, message: dict[str, Any], user: dict[str, Any], _admin: dict[str, Any]) -> None:
        parts = self._command_parts(rest, 2)
        if len(parts) not in {2, 3, 4}:
            raise AdminInputError(
                "نمونه: /subcategory_add PARENT_ID | عنوان | آیکون|0 | توضیح|0"
            )
        if len(parts) > 3 and parts[3].lower().startswith("html:"):
            format_admin_text(parts[3])
        category = self.db.create_category(
            parts[1],
            parent_id=_as_int(parts[0], "شناسه والد"),
            source_admin_update_id=self._admin_update_id(message),
            icon=(parts[2] if len(parts) > 2 and parts[2] != "0" else None),
            description=(parts[3] if len(parts) > 3 and parts[3] != "0" else None),
        )
        self._send(self._chat_id(message, user), f"زیردسته ثبت شد. شناسه: <code>{category['id']}</code>")

    def _category_toggle(self, rest: str, message: dict[str, Any], user: dict[str, Any], _admin: dict[str, Any]) -> None:
        category_id = _as_int(rest)
        category = next((item for item in self._all_categories() if int(item["id"]) == category_id), None)
        if category is None:
            raise AdminInputError("دسته پیدا نشد.")
        active = self._admin_toggle_target(
            message,
            f"category:{category_id}:active",
            bool(category["is_active"]),
        )
        method = self._public("set_category_active")
        if method is not None:
            method(category_id, active)
        else:
            changed = self._execute(
                "UPDATE categories SET is_active = ?, updated_at = ? WHERE id = ?",
                (int(active), _utc_timestamp(), category_id),
            )
            if changed != 1:
                raise AdminInputError("دسته پیدا نشد.")
        self._send(self._chat_id(message, user), f"دسته {'فعال' if active else 'غیرفعال'} شد.")

    def _category_set(self, rest: str, message: dict[str, Any], user: dict[str, Any], _admin: dict[str, Any]) -> None:
        parts = self._command_parts(rest, 3)
        if len(parts) != 3:
            raise AdminInputError(
                "نمونه: /category_set CATEGORY_ID | name|parent|icon|description|sort_order | VALUE"
            )
        category_id = _as_int(parts[0], "شناسه دسته")
        field = parts[1].strip().lower()
        if field == "name":
            value: Any = parts[2].strip()
            if not value:
                raise AdminInputError("عنوان دسته نمی‌تواند خالی باشد.")
            changes = {"name": value}
        elif field == "parent":
            raw_parent = normalize_digits(parts[2]).strip().lower()
            if raw_parent in {"0", "none", "root", "ریشه"}:
                changes = {"parent_id": None}
            else:
                changes = {"parent_id": _as_int(raw_parent, "شناسه والد")}
        elif field == "sort_order":
            try:
                value = int(normalize_digits(parts[2]).strip())
            except ValueError as exc:
                raise AdminInputError("ترتیب نمایش باید عدد صحیح باشد.") from exc
            changes = {"sort_order": value}
        elif field in {"icon", "description"}:
            value = parts[2].strip()
            if field == "description" and value.lower().startswith("html:"):
                format_admin_text(value)
            changes = {field: None if value.lower() in {"0", "none", "null", "-"} else value}
        else:
            raise AdminInputError(
                "فیلد دسته باید name، parent، icon، description یا sort_order باشد."
            )
        category = self.db.update_category(category_id, **changes)
        self._send(
            self._chat_id(message, user),
            f"دسته <code>{category['id']}</code> به‌روزرسانی شد.",
        )

    def _category_delete(self, rest: str, message: dict[str, Any], user: dict[str, Any], _admin: dict[str, Any]) -> None:
        category_id = _as_int(rest, "شناسه دسته")
        if self.db.get_category(category_id) is None and self._admin_update_is_replay(
            message
        ):
            self._send(
                self._chat_id(message, user),
                f"دسته <code>{category_id}</code> قبلاً حذف شده است.",
            )
            return
        self.db.delete_category(category_id)
        self._send(self._chat_id(message, user), f"دسته <code>{category_id}</code> حذف شد.")

    def _products(self, rest: str, message: dict[str, Any], user: dict[str, Any], _admin: dict[str, Any]) -> None:
        tokens = rest.split()
        if len(tokens) > 2:
            raise AdminInputError("نمونه: /products [CATEGORY_ID|all] [PAGE]")
        category_id = (
            _as_int(tokens[0], "شناسه دسته")
            if tokens and tokens[0].lower() != "all" else None
        )
        page = _page_number(tokens[1]) if len(tokens) == 2 else 1
        query = "SELECT * FROM products"
        parameters: tuple[Any, ...] = ()
        if category_id is not None:
            query += " WHERE category_id = ?"
            parameters = (category_id,)
        products, total, pages = self._management_rows(
            page, query + " ORDER BY id", parameters
        )
        currency = getattr(self.settings, "currency_label", "تومان")
        lines = []
        for item in products:
            flags = "/".join(
                (
                    "فعال" if item["is_active"] else "حذف‌شده",
                    "نمایش" if item["is_visible"] else "مخفی",
                    "موجود" if item["is_available"] else "ناموجود",
                    "رزرو" if item["reserve_enabled"] else "بدون رزرو",
                )
            )
            lines.append(
                f"<code>{item['id']}</code> | {escape(item['name'])} | "
                f"{money(item['price_amount'], currency)} | {escape(item['product_type'])} | {flags}"
            )
        self._send_page(
            self._chat_id(message, user), title="محصولات", rows=lines,
            total=total, page=page, pages=pages,
            command_prefix=f"/products {category_id if category_id is not None else 'all'}",
            empty_text="محصولی پیدا نشد.",
        )

    def _product_add(self, rest: str, message: dict[str, Any], user: dict[str, Any], _admin: dict[str, Any]) -> None:
        parts = self._command_parts(rest, 5)
        if len(parts) != 5:
            raise AdminInputError("نمونه: /product_add CATEGORY_ID | عنوان | قیمت | مدت | ready|manual")
        product_type = parts[4].lower()
        if product_type not in {"ready", "manual"}:
            raise AdminInputError("نوع محصول باید ready یا manual باشد.")
        product = self.db.create_product(
            _as_int(parts[0], "شناسه دسته"),
            parts[1],
            product_type=product_type,
            price_amount=parse_amount(parts[2]),
            duration_days=_duration_days(parts[3]),
            duration_label=parts[3],
            idempotency_key=self._admin_idempotency_key(message, "product-create"),
        )
        self._send(self._chat_id(message, user), f"محصول ثبت شد. شناسه: <code>{product['id']}</code>")

    def _product_set(self, rest: str, message: dict[str, Any], user: dict[str, Any], _admin: dict[str, Any]) -> None:
        parts = self._command_parts(rest, 3)
        if len(parts) != 3:
            raise AdminInputError("نمونه: /product_set PRODUCT_ID | FIELD | VALUE")
        field = parts[1].lower()
        column = _PRODUCT_FIELDS.get(field)
        if column is None:
            raise AdminInputError("این فیلد محصول قابل ویرایش نیست.")
        value: Any = parts[2]
        if field == "name":
            value = value.strip()
            if not value:
                raise AdminInputError("عنوان محصول نمی‌تواند خالی باشد.")
        elif field == "category":
            value = _as_int(normalize_digits(value), "شناسه دسته")
        elif field == "type":
            value = value.strip().lower()
            if value not in {"ready", "manual"}:
                raise AdminInputError("نوع محصول باید ready یا manual باشد.")
        elif field == "stock_limit":
            normalized = normalize_digits(value).strip().lower()
            if normalized in {"none", "null", "unlimited", "-", "نامحدود"}:
                value = None
            else:
                try:
                    value = int(normalized)
                except ValueError as exc:
                    raise AdminInputError("سقف موجودی باید عدد صحیح نامنفی یا none باشد.") from exc
                if value < 0:
                    raise AdminInputError("سقف موجودی نمی‌تواند منفی باشد.")
        elif field == "price":
            value = parse_amount(value)
        elif field == "duration_days":
            normalized = normalize_digits(value).strip().lower()
            if normalized in {"none", "null", "-", "بدون انقضا", "مادام العمر"}:
                value = None
            else:
                value = _as_int(normalized, "تعداد روز")
                if value < 1:
                    raise AdminInputError("تعداد روز باید مثبت باشد.")
        elif field == "renewable":
            value = _boolean_value(value)
        elif field == "features":
            value = [item.strip() for item in value.replace("؛", ";").split(";") if item.strip()]
        elif field == "reminder_days":
            try:
                value = [int(item.strip()) for item in value.replace("؛", ",").split(",") if item.strip()]
            except ValueError as exc:
                raise AdminInputError("روزهای یادآوری باید عدد و با ویرگول جدا شوند.") from exc
            if any(day < 0 for day in value):
                raise AdminInputError(
                    "روزهای یادآوری نمی‌توانند منفی باشند؛ صفر یعنی روز پایان اشتراک."
                )
        elif field == "rules_url":
            normalized = value.strip().casefold()
            if normalized in {"none", "null", "-", "خالی", "حذف"}:
                value = None
            elif not is_safe_https_url(value):
                raise AdminInputError("لینک قوانین باید آدرس کامل و امن HTTPS باشد.")
        elif isinstance(value, str) and value.lower().startswith("html:"):
            format_admin_text(value)
        changes = {column: value}
        if field == "duration":
            parsed_days = _duration_days(str(value))
            # Never retain a stale expiry interval after changing its label.
            changes["duration_days"] = parsed_days
        elif field == "duration_days":
            changes["duration_label"] = (
                f"{value} روز" if value is not None else "بدون انقضا"
            )
        product = self.db.update_product(
            _as_int(parts[0], "شناسه محصول"), **changes
        )
        self._send(self._chat_id(message, user), f"محصول <code>{product['id']}</code> به‌روزرسانی شد.")

    def _product_toggle(self, rest: str, message: dict[str, Any], user: dict[str, Any], _admin: dict[str, Any]) -> None:
        parts = (
            [part.strip() for part in rest.split("|")]
            if "|" in rest
            else rest.split()
        )
        if len(parts) != 2 or parts[1].lower() not in {"visible", "available", "reserve"}:
            raise AdminInputError("نمونه: /product_toggle PRODUCT_ID visible|available|reserve")
        product_id = _as_int(parts[0], "شناسه محصول")
        product = self.db.get_product(product_id)
        if product is None:
            raise AdminInputError("محصول پیدا نشد.")
        column = {"visible": "is_visible", "available": "is_available", "reserve": "reserve_enabled"}[parts[1].lower()]
        target = self._admin_toggle_target(
            message,
            f"product:{product_id}:{column}",
            bool(product[column]),
        )
        updated = self.db.update_product(product_id, **{column: target})
        self._send(self._chat_id(message, user), f"وضعیت محصول <code>{updated['id']}</code> تغییر کرد.")

    def _product_delete(self, rest: str, message: dict[str, Any], user: dict[str, Any], _admin: dict[str, Any]) -> None:
        product_id = _as_int(rest, "شناسه محصول")
        product = self.db.soft_delete_product(product_id)
        self._send(
            self._chat_id(message, user),
            f"محصول <code>{product['id']}</code> به‌صورت نرم حذف و از فروشگاه خارج شد.",
        )

    def _inventory_add(self, rest: str, message: dict[str, Any], user: dict[str, Any], _admin: dict[str, Any]) -> None:
        product_id = _as_int(rest, "شناسه محصول")
        product = self.db.get_product(product_id)
        if product is None or product.get("product_type") != "ready":
            raise AdminInputError("محصول آماده پیدا نشد.")
        self.db.set_user_state(int(user["id"]), "admin:inventory", {"product_id": product_id})
        self._send(
            self._chat_id(message, user),
            "اطلاعات محرمانه اکانت را در پیام بعدی بفرستید. این محتوا در پاسخ ربات تکرار نمی‌شود."
            "\nبرای لغو: /cancel",
        )

    def _inventory_list(self, rest: str, message: dict[str, Any], user: dict[str, Any], _admin: dict[str, Any]) -> None:
        tokens = rest.split()
        if len(tokens) not in {1, 2}:
            raise AdminInputError("نمونه: /inventory_list PRODUCT_ID [PAGE]")
        product_id = _as_int(tokens[0], "شناسه محصول")
        page = _page_number(tokens[1]) if len(tokens) == 2 else 1
        items, total, pages = self._management_rows(
            page,
            "SELECT id, product_id, status, assigned_order_id, assigned_user_id, assigned_at, created_at "
            "FROM inventory_items WHERE product_id = ? ORDER BY id DESC",
            (product_id,),
        )
        lines = []
        for item in items:
            lines.append(
                f"<code>{item['id']}</code> | {escape(item['status'])} | "
                f"سفارش {escape(item.get('assigned_order_id') or '—')}"
            )
        self._send_page(
            self._chat_id(message, user), title=f"انبار محصول {product_id}", rows=lines,
            total=total, page=page, pages=pages,
            command_prefix=f"/inventory_list {product_id}", empty_text="آیتمی ثبت نشده است.",
        )

    def _inventory_disable(self, rest: str, message: dict[str, Any], user: dict[str, Any], _admin: dict[str, Any]) -> None:
        item_id = _as_int(rest, "شناسه آیتم")
        method = self._public("set_inventory_status")
        if method is not None:
            method(item_id, "disabled")
        else:
            changed = self._execute(
                "UPDATE inventory_items SET status = 'disabled' WHERE id = ? AND status = 'available'",
                (item_id,),
            )
            if changed != 1:
                raise AdminInputError("فقط موجودی آزاد را می‌توان غیرفعال کرد.")
        self._send(self._chat_id(message, user), "آیتم انبار غیرفعال شد.")

    def _inventory_edit(self, rest: str, message: dict[str, Any], user: dict[str, Any], _admin: dict[str, Any]) -> None:
        item_id = _as_int(rest, "شناسه آیتم")
        item = self._query_one(
            "SELECT id, status FROM inventory_items WHERE id = ?",
            (item_id,),
        )
        if item is None:
            raise AdminInputError("آیتم انبار پیدا نشد.")
        if item["status"] == "assigned":
            raise AdminInputError("موجودی تحویل‌شده قابل ویرایش نیست.")
        self.db.set_user_state(
            int(user["id"]),
            "admin:inventory_edit",
            {"item_id": item_id},
        )
        self._send(
            self._chat_id(message, user),
            "اطلاعات محرمانه جدید را در پیام بعدی بفرستید. متن در پاسخ تکرار نمی‌شود."
            "\nبرای لغو: /cancel",
        )

    def _inventory_enable(self, rest: str, message: dict[str, Any], user: dict[str, Any], _admin: dict[str, Any]) -> None:
        item_id = _as_int(rest, "شناسه آیتم")
        item = self.db.set_inventory_status(item_id, "available")
        self._send(
            self._chat_id(message, user),
            f"آیتم انبار <code>{item['id']}</code> فعال شد.",
        )

    def _inventory_delete(self, rest: str, message: dict[str, Any], user: dict[str, Any], _admin: dict[str, Any]) -> None:
        item_id = _as_int(rest, "شناسه آیتم")
        if (
            self._query_one("SELECT id FROM inventory_items WHERE id = ?", (item_id,))
            is None
            and self._admin_update_is_replay(message)
        ):
            self._send(
                self._chat_id(message, user),
                f"آیتم انبار <code>{item_id}</code> قبلاً حذف شده است.",
            )
            return
        item = self.db.delete_inventory_item(item_id)
        self._send(
            self._chat_id(message, user),
            f"آیتم انبار <code>{item['id']}</code> حذف شد.",
        )

    def _inventory_assign(self, rest: str, message: dict[str, Any], user: dict[str, Any], admin: dict[str, Any]) -> None:
        parts = rest.split()
        if len(parts) != 2:
            raise AdminInputError("نمونه: /inventory_assign ITEM_ID USER_CHAT_ID")
        item_id = _as_int(parts[0], "شناسه آیتم")
        target = self._find_user(parts[1])

        def delivery_notice(result: Mapping[str, Any]) -> tuple[str, str]:
            return (
                "یک اشتراک توسط مدیریت به حساب شما تحویل شد."
                f"\nشماره سفارش: <code>{escape(result['order_number'])}</code>"
                f"\n\n<code>{escape(result['payload'])}</code>",
                f"order:{result['id']}:delivery",
            )

        result = self._assign_inventory_to_user(
            item_id,
            target,
            int(admin["id"]),
            delivery_notice=delivery_notice,
        )
        delivery_text, notification_key = delivery_notice(result)
        self._deliver_prequeued_notification(
            target,
            delivery_text,
            idempotency_key=notification_key,
        )
        self._send(
            self._chat_id(message, user),
            f"آیتم تحویل شد و سفارش <code>{escape(result['order_number'])}</code> ثبت شد.",
        )

    def _assign_inventory_to_user(
        self,
        item_id: int,
        target: Mapping[str, Any],
        actor_admin_id: int,
        *,
        delivery_notice: Callable[[Mapping[str, Any]], tuple[str, str]],
    ) -> dict[str, Any]:
        method = self._public("assign_inventory_item_to_user")
        if method is not None:
            return method(
                item_id,
                int(target["id"]),
                actor_admin_id=actor_admin_id,
                delivery_notice=delivery_notice,
            )
        raise AdminIntegrationError(
            "نسخه Database تحویل اتمیک موجودی و پیام پایدار را پشتیبانی نمی‌کند."
        )

    # -- Orders, payments and users --------------------------------------

    def _orders(self, rest: str, message: dict[str, Any], user: dict[str, Any], _admin: dict[str, Any]) -> None:
        tokens = rest.split()
        page = 1
        if tokens and re.fullmatch(r"[+-]?\d+", normalize_digits(tokens[-1])):
            page = _page_number(tokens.pop())
        filter_tokens = list(tokens)
        status: str | None = None
        start: str | None = None
        end: str | None = None
        if len(tokens) == 1:
            status = None if tokens[0].lower() == "all" else tokens[0].lower()
        elif len(tokens) == 2:
            start, end = _date_range(
                tokens[0], tokens[1], getattr(self.settings, "timezone", "UTC")
            )
        elif len(tokens) == 3:
            status = None if tokens[0].lower() == "all" else tokens[0].lower()
            start, end = _date_range(
                tokens[1], tokens[2], getattr(self.settings, "timezone", "UTC")
            )
        elif len(tokens) > 3:
            raise AdminInputError(
                "نمونه: /orders [STATUS|all] [FROM_DATE TO_DATE] [PAGE]"
            )
        if status is not None and status not in _ORDER_STATUSES:
            raise AdminInputError("وضعیت سفارش معتبر نیست.")
        total = self.db.count_orders(
            status=status, created_from=start, created_until=end
        )
        pages, offset = _page_bounds(total, page)
        orders = self.db.list_orders(
            status=status,
            created_from=start,
            created_until=end,
            limit=_ADMIN_PAGE_SIZE,
            offset=offset,
        )
        rows = []
        for item in orders:
            rows.append(
                f"<code>{escape(item['order_number'])}</code> | "
                f"{escape(item['product_name_snapshot'])} | {escape(item['status'])} | "
                f"کاربر {item['user_id']}"
            )
        command_prefix = "/orders"
        if filter_tokens:
            command_prefix += " " + " ".join(filter_tokens)
        self._send_page(
            self._chat_id(message, user),
            title="سفارش‌ها",
            rows=rows,
            total=total,
            page=page,
            pages=pages,
            command_prefix=command_prefix,
            empty_text="سفارشی پیدا نشد.",
            tail=("جزئیات سفارش: <code>/order ORDER_NUMBER</code>",),
        )

    def _order(self, rest: str, message: dict[str, Any], user: dict[str, Any], _admin: dict[str, Any]) -> None:
        order = self._require_order(rest)
        self._send_order_details(self._chat_id(message, user), order)

    def _order_attachment(
        self,
        rest: str,
        message: dict[str, Any],
        user: dict[str, Any],
        _admin: dict[str, Any],
    ) -> None:
        order = self._require_order(rest)
        chat_id = self._chat_id(message, user)
        raw_info = order.get("customer_info_json")
        if not raw_info:
            raise AdminInputError("این سفارش پیوستی ندارد.")
        try:
            info = json.loads(str(raw_info))
        except (TypeError, ValueError):
            raise AdminInputError("فرمت اطلاعات پیوست معتبر نیست.") from None
        if not isinstance(info, Mapping):
            raise AdminInputError("فرمت اطلاعات پیوست معتبر نیست.")
        file_id = str(info.get("file_id") or "").strip()
        file_kind = str(info.get("file_kind") or "").strip().lower()
        if not file_id:
            raise AdminInputError("این سفارش پیوستی ندارد.")
        if file_kind == "photo" and hasattr(self.telegram, "send_photo"):
            self.telegram.send_photo(
                chat_id,
                file_id,
                caption=f"پیوست سفارش <code>{escape(order['order_number'])}</code>",
            )
        else:
            self.telegram.send_document(
                chat_id,
                file_id,
                caption=f"پیوست سفارش <code>{escape(order['order_number'])}</code>",
            )

    def _require_order(self, order_number: str) -> dict[str, Any]:
        value = order_number.strip()
        if not value:
            raise AdminInputError("شماره سفارش الزامی است.")
        order = self.db.get_order_by_number(value)
        if order is None:
            raise AdminInputError("سفارش پیدا نشد.")
        return order

    def _format_order(self, order: Mapping[str, Any]) -> str:
        currency = getattr(self.settings, "currency_label", "تومان")
        content = (
            "<b>جزئیات سفارش</b>"
            f"\nشماره: <code>{escape(order['order_number'])}</code>"
            f"\nمحصول: {escape(order['product_name_snapshot'])}"
            f"\nکاربر: <code>{order['user_id']}</code>"
            f"\nوضعیت: <code>{escape(order['status'])}</code>"
            f"\nمبلغ: {money(int(order.get('subtotal_amount') or 0), currency)}"
            f"\nتخفیف: {money(int(order.get('discount_amount') or 0), currency)}"
            f"\nمانده پرداخت: {money(int(order.get('payable_amount') or 0), currency)}"
            f"\nزمان ثبت: {escape(order.get('created_at') or '—')}"
        )
        return content

    def _send_order_details(self, chat_id: int, order: Mapping[str, Any]) -> None:
        self._send(chat_id, self._format_order(order))
        raw_info = order.get("customer_info_json")
        if raw_info:
            try:
                info = json.loads(str(raw_info))
            except (TypeError, ValueError):
                info = {"text": str(raw_info)}
            if isinstance(info, Mapping):
                if info.get("text"):
                    # Escape small raw chunks independently, so HTML entities and
                    # Telegram limits never hide the end of activation input.
                    body = str(info["text"])
                    for index in range(0, len(body), 600):
                        self._send(
                            chat_id,
                            "<b>اطلاعات کاربر:</b>"
                            f"\nسفارش: <code>{escape(order['order_number'])}</code>"
                            f"\n{escape(body[index:index + 600])}",
                        )
                if info.get("file_kind"):
                    self._send(chat_id, f"پیوست: {escape(info['file_kind'])}")

    def _order_status(self, rest: str, message: dict[str, Any], user: dict[str, Any], _admin: dict[str, Any]) -> None:
        parts = self._command_parts(rest, 1)
        head = parts[0].split()
        if len(head) != 2:
            raise AdminInputError("نمونه: /order_status ORDER_NUMBER STATUS | پیام اختیاری")
        order = self._require_order(head[0])
        status = head[1].lower()
        if status not in _ORDER_STATUSES:
            raise AdminInputError("وضعیت سفارش معتبر نیست.")
        if status in {"paid", "completed", "refunded"}:
            alternatives = {
                "paid": "برای ثبت پرداخت از /approve_payment استفاده کنید.",
                "completed": "برای تکمیل سفارش از /complete استفاده کنید.",
                "refunded": "بازپرداخت فقط پس از عملیات مالی واقعی قابل ثبت است.",
            }
            raise AdminInputError(alternatives[status])
        note = parts[1] if len(parts) > 1 else None
        notice = (
            f"وضعیت سفارش <code>{escape(order['order_number'])}</code> به "
            f"<code>{escape(status)}</code> تغییر کرد."
        )
        if note:
            notice += f"\n\n{escape(note)}"
        self._require_safe_notification_length(notice)
        notice_key = (
            f"order:{order['id']}:status:{status}:"
            f"{message.get('message_id', 'unknown')}"
        )
        updated = self.db.update_order_status(
            int(order["id"]),
            status,
            admin_note=note,
            outbound_body=notice,
            outbound_idempotency_key=notice_key,
        )
        target = self.db.get_user(int(order["user_id"]))
        self._deliver_prequeued_notification(
            target,
            notice,
            idempotency_key=notice_key,
        )
        self._send(
            self._chat_id(message, user),
            f"وضعیت سفارش به <code>{escape(updated['status'])}</code> تغییر کرد.",
        )

    def _complete(self, rest: str, message: dict[str, Any], user: dict[str, Any], _admin: dict[str, Any]) -> None:
        parts = self._command_parts(rest, 2)
        if len(parts) != 2:
            raise AdminInputError("نمونه: /complete ORDER_NUMBER | متن تحویل")
        rendered_delivery = format_admin_text(parts[1])
        order = self._require_order(parts[0])
        if order.get("product_type_snapshot") != "manual":
            raise AdminInputError("تکمیل دستی فقط برای سفارش محصول manual مجاز است.")
        notification = (
            "سفارش شما تکمیل شد."
            f"\nشماره سفارش: <code>{escape(order['order_number'])}</code>"
            f"\n\n{rendered_delivery}"
        )
        self._require_safe_notification_length(notification)
        notification_key = f"order:{order['id']}:manual-completion-notice"
        method = self._public("complete_order")
        if method is not None:
            updated = method(
                int(order["id"]),
                parts[1],
                outbound_body=notification,
                outbound_idempotency_key=notification_key,
            )
        else:
            raise AdminIntegrationError("نسخه Database تکمیل اتمیک سفارش دستی را پشتیبانی نمی‌کند.")
        target = self.db.get_user(int(order["user_id"]))
        self._deliver_prequeued_notification(
            target,
            notification,
            idempotency_key=notification_key,
        )
        self._send(self._chat_id(message, user), f"سفارش <code>{escape(updated['order_number'])}</code> تکمیل شد.")

    def _request_info(self, rest: str, message: dict[str, Any], user: dict[str, Any], _admin: dict[str, Any]) -> None:
        parts = self._command_parts(rest, 2)
        if len(parts) != 2:
            raise AdminInputError("نمونه: /request_info ORDER_NUMBER | متن درخواست اصلاح")
        rendered_request = format_admin_text(parts[1])
        order = self._require_order(parts[0])
        if order.get("product_type_snapshot") != "manual":
            raise AdminInputError("درخواست اطلاعات فقط برای سفارش محصول manual مجاز است.")
        notice = (
            "اطلاعات سفارش نیاز به اصلاح دارد."
            f"\nشماره سفارش: <code>{escape(order['order_number'])}</code>"
            f"\n\n{rendered_request}"
        )
        self._require_safe_notification_length(notice)
        notice_key = (
            f"order:{order['id']}:info-correction:"
            f"{message.get('message_id', 'unknown')}"
        )
        updated = self.db.update_order_status(
            int(order["id"]),
            "awaiting_info",
            admin_note=parts[1],
            outbound_body=notice,
            outbound_idempotency_key=notice_key,
        )
        target = self.db.get_user(int(order["user_id"]))
        self._deliver_prequeued_notification(
            target,
            notice,
            idempotency_key=notice_key,
        )
        self._send(self._chat_id(message, user), f"درخواست اطلاعات برای <code>{escape(updated['order_number'])}</code> ارسال شد.")

    def _require_safe_notification_length(self, text: str) -> None:
        limit = int(getattr(self.db, "TELEGRAM_SAFE_MESSAGE_LENGTH", 3_900))
        if len(str(text).strip()) > limit:
            raise AdminInputError(
                "متن نهایی پیام برای تلگرام طولانی است؛ آن را کوتاه‌تر کنید."
            )

    def _approve_payment_record(
        self,
        payment: Mapping[str, Any],
        actor_admin_id: int,
        *,
        source: str,
    ) -> dict[str, Any]:
        """Approve a payment and pass a plain order dict to ``fulfill_order``."""

        self._require_reviewable_card_payment(payment, terminal_status="paid")

        order: dict[str, Any] | None = None
        success_body: str | None = None
        success_key: str | None = None
        if payment.get("order_id") is not None:
            order = self.db.get_order(int(payment["order_id"]))
            if order is not None:
                order_view = {
                    **order,
                    "order_no": order.get("order_number"),
                    "product_title": order.get("product_name_snapshot") or "محصول",
                    "product_icon": order.get("product_icon_snapshot") or "",
                }
                method = "کارت به کارت"
                if int(order.get("wallet_held_amount") or 0) > 0:
                    method = f"کیف پول + {method}"
                success_body = texts.payment_success(
                    order_view,
                    int(order["subtotal_amount"]) - int(order["discount_amount"]),
                    method,
                    getattr(self.settings, "currency_label", "تومان"),
                )
                success_key = f"payment:{int(payment['id'])}:order-confirmed"

        approved = self.db.mark_payment_paid(
            int(payment["id"]),
            external_reference=f"manual:{payment['payment_number']}",
            raw_payload={"source": source, "admin_id": actor_admin_id},
            outbound_body=success_body,
            outbound_idempotency_key=success_key,
        )
        if approved.get("order_id") is not None:
            order = self.db.get_order(int(approved["order_id"]))
        target = self.db.get_user(int(payment["user_id"]))
        success_delivered = True
        if success_body is not None and success_key is not None:
            success_delivered = bool(
                self._deliver_prequeued_notification(
                    target,
                    success_body,
                    idempotency_key=success_key,
                )
            )
        if (
            success_delivered
            and order is not None
            and order.get("status") == "paid"
            and self.fulfill_order is not None
        ):
            self.fulfill_order(dict(order))
        if approved.get("purpose") == "wallet_topup":
            balance = self.db.wallet_balance(int(payment["user_id"]))
            self._notify_target(
                target,
                "✅ <b>شارژ کیف پول تأیید شد</b>\n\n"
                f"مبلغ: {money(payment['base_amount'], getattr(self.settings, 'currency_label', 'تومان'))}\n"
                f"موجودی جدید: {money(balance, getattr(self.settings, 'currency_label', 'تومان'))}",
                idempotency_key=f"payment:{payment['id']}:topup-confirmed",
            )
        return approved

    def _reject_payment_record(
        self,
        payment: Mapping[str, Any],
        reason: str,
        actor_admin_id: int,
        *,
        source: str,
    ) -> dict[str, Any]:
        """Reject a submitted card receipt and reopen its live order if possible."""

        self._require_reviewable_card_payment(payment, terminal_status="failed")

        notice = (
            "پرداخت شما رد شد."
            f"\nشماره پرداخت: <code>{escape(payment['payment_number'])}</code>"
            f"\nدلیل: {escape(reason)}"
        )
        self._require_safe_notification_length(notice)

        notice_key = f"payment:{payment['id']}:admin-rejected-notice"
        rejected = self.db.set_payment_status(
            int(payment["id"]),
            "failed",
            raw_payload={
                "source": source,
                "admin_id": actor_admin_id,
                "reason": reason,
            },
            outbound_body=notice,
            outbound_idempotency_key=notice_key,
        )
        target = self.db.get_user(int(payment["user_id"]))
        self._deliver_prequeued_notification(
            target,
            notice,
            idempotency_key=notice_key,
        )
        return rejected

    @staticmethod
    def _require_reviewable_card_payment(
        payment: Mapping[str, Any],
        *,
        terminal_status: str,
    ) -> None:
        if payment.get("method") != "card":
            raise AdminInputError("تأیید یا رد دستی فقط برای فیش پرداخت کارت مجاز است.")
        if not str(payment.get("receipt_file_id") or "").strip():
            raise AdminInputError("این پرداخت هنوز فیشی برای بررسی ندارد.")
        allowed_statuses = {"verifying", terminal_status}
        if payment.get("status") not in allowed_statuses:
            raise AdminInputError("این فیش دیگر در وضعیت قابل بررسی نیست.")

    def _approve_payment(self, rest: str, message: dict[str, Any], user: dict[str, Any], admin: dict[str, Any]) -> None:
        payment = self._require_payment(rest)
        self._approve_payment_record(payment, int(admin["id"]), source="admin_command")
        self._send(self._chat_id(message, user), "پرداخت تأیید شد.")

    def _reject_payment(self, rest: str, message: dict[str, Any], user: dict[str, Any], admin: dict[str, Any]) -> None:
        parts = self._command_parts(rest, 2)
        if len(parts) != 2:
            raise AdminInputError("نمونه: /reject_payment PAYMENT_NUMBER | دلیل")
        payment = self._require_payment(parts[0])
        self._reject_payment_record(
            payment,
            parts[1],
            int(admin["id"]),
            source="admin_command",
        )
        self._send(self._chat_id(message, user), "پرداخت رد شد.")

    def _payment_detail(
        self,
        rest: str,
        message: dict[str, Any],
        user: dict[str, Any],
        _admin: dict[str, Any],
    ) -> None:
        payment = self._require_payment(rest)
        target = self.db.get_user(int(payment["user_id"]))
        order = (
            self.db.get_order(int(payment["order_id"]))
            if payment.get("order_id") is not None
            else None
        )
        chat_id = self._chat_id(message, user)
        lines = [
            "<b>جزئیات پرداخت</b>",
            f"شماره: <code>{escape(payment['payment_number'])}</code>",
            f"وضعیت: <code>{escape(payment['status'])}</code>",
            f"روش: <code>{escape(payment['method'])}</code>",
            f"نوع: <code>{escape(payment['purpose'])}</code>",
            f"مبلغ: <code>{int(payment['payable_amount']):,}</code>",
            f"کاربر: <code>{int(target['chat_id']) if target else 'نامشخص'}</code>",
        ]
        if order is not None:
            lines.append(
                f"سفارش: <code>{escape(order['order_number'])}</code>"
            )
        if payment.get("receipt_file_id"):
            lines.append("فیش ذخیره‌شده در پیام بعدی ارسال می‌شود.")
        self._send(chat_id, "\n".join(lines))
        if not payment.get("receipt_file_id"):
            return
        attachment = self.db.get_payment_receipt_attachment(int(payment["id"]))
        file_kind = str((attachment or {}).get("file_kind") or "document")
        file_id = str(payment["receipt_file_id"])
        caption = f"فیش <code>{escape(payment['payment_number'])}</code>"
        if file_kind == "photo" and hasattr(self.telegram, "send_photo"):
            self.telegram.send_photo(chat_id, file_id, caption=caption)
        else:
            self.telegram.send_document(chat_id, file_id, caption=caption)

    def _card_reviews(
        self,
        rest: str,
        message: dict[str, Any],
        user: dict[str, Any],
        _admin: dict[str, Any],
    ) -> None:
        if rest:
            raise AdminInputError("فرمان /card_reviews آرگومان نمی‌گیرد.")
        reviews = self.db.list_card_payment_reviews(limit=100)
        lines = ["<b>رخدادهای بانکی نیازمند بازبینی</b>"]
        for event in reviews:
            linked = ""
            if event.get("payment_number"):
                linked = (
                    f" | پرداخت <code>{escape(event['payment_number'])}</code>"
                    f" | {escape(event.get('purpose') or '—')}"
                    f" | کاربر <code>{event.get('user_chat_id') or '—'}</code>"
                )
            lines.append(
                f"<code>{int(event['id'])}</code> | "
                f"<code>{escape(event['reference'])}</code> | "
                f"{int(event['amount']):,}{linked}"
            )
        if not reviews:
            lines.append("مورد بازی وجود ندارد.")
        self._send(self._chat_id(message, user), "\n".join(lines))

    def _crypto_reviews(
        self,
        rest: str,
        message: dict[str, Any],
        user: dict[str, Any],
        _admin: dict[str, Any],
    ) -> None:
        if rest:
            raise AdminInputError("فرمان /crypto_reviews آرگومان نمی‌گیرد.")
        reviews = self.db.list_provider_payment_reviews(limit=100)
        lines = ["<b>پرداخت‌های ارزی نیازمند بازبینی</b>"]
        for event in reviews:
            amount = event.get("received_amount")
            lines.append(
                f"<code>{int(event['id'])}</code> | "
                f"<code>{escape(event['payment_number'])}</code> | "
                f"{escape(event['provider_status'])} | "
                f"دریافتی: <code>{escape('نامشخص' if amount is None else amount)}</code>"
            )
        if not reviews:
            lines.append("مورد بازی وجود ندارد.")
        self._send(self._chat_id(message, user), "\n".join(lines))

    def _review_resolution_parts(
        self,
        rest: str,
        command: str,
        *,
        allow_credit: bool = False,
    ) -> tuple[int, str, str]:
        parts = self._command_parts(rest, 2)
        left = parts[0].split()
        actions = {"dismiss", "refund_confirmed"}
        if allow_credit:
            actions.add("credit_confirmed")
        if len(left) != 2 or not left[0].isdigit() or int(left[0]) < 1:
            raise AdminInputError(
                f"نمونه: {command} EVENT_ID "
                f"{'dismiss|refund_confirmed|credit_confirmed' if allow_credit else 'dismiss|refund_confirmed'}"
                " | توضیح"
            )
        action = left[1].strip().lower()
        if action not in actions:
            allowed = "، ".join(sorted(actions))
            raise AdminInputError(f"نتیجه باید یکی از {allowed} باشد.")
        note = " | ".join(parts[1:]).strip()
        if not note:
            raise AdminInputError("توضیح تعیین تکلیف الزامی است.")
        return int(left[0]), action, note

    def _card_resolve(
        self,
        rest: str,
        message: dict[str, Any],
        user: dict[str, Any],
        admin: dict[str, Any],
    ) -> None:
        if self._active_role(admin) != "owner":
            raise AdminInputError("فقط مالک می‌تواند بازبینی مالی را تعیین تکلیف کند.")
        event_id, action, note = self._review_resolution_parts(
            rest, "/card_resolve"
        )
        resolution = self.db.resolve_card_payment_review(
            event_id, action, int(admin["id"]), note
        )
        payment = (
            self.db.get_payment(int(resolution["payment_id"]))
            if resolution.get("payment_id") is not None
            else None
        )
        target = (
            self.db.get_user(int(payment["user_id"])) if payment is not None else None
        )
        self._notify_target(
            target,
            texts.card_review_resolution(action),
            idempotency_key=f"card-review:{event_id}:resolution:{action}:user",
        )
        self._send(
            self._chat_id(message, user),
            "رخداد بانکی تعیین تکلیف شد؛ هیچ اعتبار خودکاری ثبت نشد.",
        )

    def _crypto_resolve(
        self,
        rest: str,
        message: dict[str, Any],
        user: dict[str, Any],
        admin: dict[str, Any],
    ) -> None:
        if self._active_role(admin) != "owner":
            raise AdminInputError("فقط مالک می‌تواند بازبینی مالی را تعیین تکلیف کند.")
        event_id, action, note = self._review_resolution_parts(
            rest, "/crypto_resolve", allow_credit=True
        )
        resolution = self.db.resolve_provider_payment_review(
            event_id, action, int(admin["id"]), note
        )
        payment = self.db.get_payment(int(resolution["payment_id"]))
        target = (
            self.db.get_user(int(payment["user_id"])) if payment is not None else None
        )
        self._notify_target(
            target,
            texts.provider_review_resolution(
                action, str(resolution.get("settlement") or "closed_without_credit")
            ),
            idempotency_key=f"provider-review:{event_id}:resolution:{action}:user",
        )
        self._send(
            self._chat_id(message, user),
            (
                "پرداخت با شواهد قطعی درگاه تعیین تکلیف و اعتبار دقیقاً یک‌بار ثبت شد."
                if action == "credit_confirmed"
                else "بازبینی پرداخت ارزی تعیین تکلیف شد؛ هیچ اعتبار خودکاری ثبت نشد."
            ),
        )

    def _require_payment(self, payment_number: str) -> dict[str, Any]:
        value = payment_number.strip()
        if not value:
            raise AdminInputError("شماره پرداخت الزامی است.")
        payment = self.db.get_payment_by_number(value)
        if payment is None:
            raise AdminInputError("پرداخت پیدا نشد.")
        return payment

    def _find_user(self, identifier: str) -> dict[str, Any]:
        value = identifier.strip()
        if not value:
            raise AdminInputError("شناسه کاربر الزامی است.")
        if value.upper().startswith(("ORD-", "ADM-")):
            order = self.db.get_order_by_number(value)
            result = self.db.get_user(int(order["user_id"])) if order else None
        elif value.startswith("@"):
            username = normalize_username(value)
            result = self.db.get_user_by_username(username)
        else:
            result = self.db.get_user_by_chat_id(_as_int(value, "chat_id"))
        if result is None:
            raise AdminInputError("کاربر پیدا نشد.")
        return result

    def _users(self, rest: str, message: dict[str, Any], user: dict[str, Any], _admin: dict[str, Any]) -> None:
        tokens = rest.strip().split()
        if tokens and re.fullmatch(r"[+-]?\d+", normalize_digits(tokens[0])):
            tokens.insert(0, "all")
        mode = tokens[0].lower() if tokens else "all"
        chat_id = self._chat_id(message, user)
        if mode in {"all", "blocked"} and len(tokens) in {0, 1, 2}:
            page = _page_number(tokens[1]) if len(tokens) == 2 else 1
            blocked = mode == "blocked" or None
            self._send_users_panel(chat_id, blocked=blocked, page=page)
            return
        if mode == "active" and len(tokens) in {1, 2}:
            page = _page_number(tokens[1]) if len(tokens) == 2 else 1
            cutoff = (datetime.now(UTC) - timedelta(days=30)).isoformat(
                timespec="seconds"
            )
            count = self._query_one(
                "SELECT COUNT(*) AS count FROM users "
                "WHERE is_blocked = 0 AND updated_at >= ?",
                (cutoff,),
            )
            total = int((count or {}).get("count") or 0)
            pages, offset = _page_bounds(total, page)
            rows = self._query(
                "SELECT * FROM users WHERE is_blocked = 0 AND updated_at >= ? "
                "ORDER BY id DESC LIMIT ? OFFSET ?",
                (cutoff, _ADMIN_PAGE_SIZE, offset),
            )
            self._send_user_rows(
                chat_id,
                rows,
                "کاربران فعال ۳۰ روز اخیر",
                total=total,
                page=page,
                pages=pages,
                command_prefix="/users active",
            )
            return
        if mode in {"new", "inactive"} and len(tokens) in {1, 2, 3}:
            default_days = 7 if mode == "new" else 30
            try:
                days = int(normalize_digits(tokens[1])) if len(tokens) == 2 else default_days
            except ValueError as exc:
                raise AdminInputError("تعداد روز باید عدد صحیح مثبت باشد.") from exc
            if len(tokens) == 3:
                try:
                    days = int(normalize_digits(tokens[1]))
                except ValueError as exc:
                    raise AdminInputError("تعداد روز باید عدد صحیح مثبت باشد.") from exc
                page = _page_number(tokens[2])
            else:
                page = 1
            if not 1 <= days <= 3650:
                raise AdminInputError("تعداد روز باید بین ۱ و ۳۶۵۰ باشد.")
            cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat(timespec="seconds")
            operator = ">=" if mode == "new" else "<"
            column = "joined_at" if mode == "new" else "updated_at"
            count = self._query_one(
                f"SELECT COUNT(*) AS count FROM users "
                f"WHERE is_blocked = 0 AND {column} {operator} ?",
                (cutoff,),
            )
            total = int((count or {}).get("count") or 0)
            pages, offset = _page_bounds(total, page)
            rows = self._query(
                f"SELECT * FROM users WHERE is_blocked = 0 AND {column} {operator} ? "
                "ORDER BY id DESC LIMIT ? OFFSET ?",
                (cutoff, _ADMIN_PAGE_SIZE, offset),
            )
            title = (
                f"کاربران جدید {days} روز اخیر"
                if mode == "new"
                else f"کاربران غیرفعال بیش از {days} روز"
            )
            self._send_user_rows(
                chat_id,
                rows,
                title,
                total=total,
                page=page,
                pages=pages,
                command_prefix=f"/users {mode} {days}",
                unblocked_status="غیرفعال" if mode == "inactive" else "فعال",
            )
            return
        if mode == "joined" and len(tokens) in {3, 4}:
            page = _page_number(tokens[3]) if len(tokens) == 4 else 1
            start, end = _date_range(
                tokens[1], tokens[2], getattr(self.settings, "timezone", "UTC")
            )
            count = self._query_one(
                "SELECT COUNT(*) AS count FROM users "
                "WHERE joined_at >= ? AND joined_at < ?",
                (start, end),
            )
            total = int((count or {}).get("count") or 0)
            pages, offset = _page_bounds(total, page)
            rows = self._query(
                "SELECT * FROM users WHERE joined_at >= ? AND joined_at < ? "
                "ORDER BY id DESC LIMIT ? OFFSET ?",
                (start, end, _ADMIN_PAGE_SIZE, offset),
            )
            self._send_user_rows(
                chat_id,
                rows,
                "کاربران عضو‌شده در بازه",
                total=total,
                page=page,
                pages=pages,
                command_prefix=f"/users joined {tokens[1]} {tokens[2]}",
            )
            return
        if mode == "product" and len(tokens) in {4, 5}:
            page = _page_number(tokens[4]) if len(tokens) == 5 else 1
            product_id = _as_int(tokens[1], "شناسه محصول")
            start, end = _date_range(
                tokens[2], tokens[3], getattr(self.settings, "timezone", "UTC")
            )
            count = self._query_one(
                """
                SELECT COUNT(DISTINCT u.id) AS count FROM users u
                JOIN orders o ON o.user_id = u.id
                WHERE o.product_id = ? AND o.paid_at >= ? AND o.paid_at < ?
                  AND o.order_origin = 'customer'
                  AND o.subtotal_amount > 0
                  AND o.status IN ('paid','awaiting_stock','awaiting_info','processing','completed')
                """,
                (product_id, start, end),
            )
            total = int((count or {}).get("count") or 0)
            pages, offset = _page_bounds(total, page)
            rows = self._query(
                """
                SELECT DISTINCT u.* FROM users u
                JOIN orders o ON o.user_id = u.id
                WHERE o.product_id = ? AND o.paid_at >= ? AND o.paid_at < ?
                  AND o.order_origin = 'customer'
                  AND o.subtotal_amount > 0
                  AND o.status IN ('paid','awaiting_stock','awaiting_info','processing','completed')
                ORDER BY u.id DESC LIMIT ? OFFSET ?
                """,
                (product_id, start, end, _ADMIN_PAGE_SIZE, offset),
            )
            self._send_user_rows(
                chat_id,
                rows,
                f"خریداران محصول {product_id}",
                total=total,
                page=page,
                pages=pages,
                command_prefix=(
                    f"/users product {product_id} {tokens[2]} {tokens[3]}"
                ),
            )
            return
        raise AdminInputError(
            "نمونه: /users all|active|blocked [PAGE]، "
            "/users new|inactive [DAYS] [PAGE]، /users joined FROM TO [PAGE] یا "
            "/users product PRODUCT_ID FROM TO [PAGE]"
        )

    def _user(self, rest: str, message: dict[str, Any], user: dict[str, Any], _admin: dict[str, Any]) -> None:
        target = self._find_user(rest)
        target_id = int(target["id"])
        orders = self.db.list_orders(user_id=target_id, limit=5)
        summary_method = self._public("user_summary")
        if summary_method is not None:
            summary = summary_method(target_id)
            balance = int(summary.get("wallet_balance") or 0)
            order_count = int(summary.get("order_count") or 0)
            purchase_total = int(summary.get("purchase_total") or 0)
            first_purchase_at = summary.get("first_purchase_at") or "—"
            last_purchase_at = summary.get("last_purchase_at") or "—"
        else:
            balance = int(self.db.get_wallet_balance(target_id))
            aggregate = self._query_one(
                """
                SELECT COUNT(*) AS order_count,
                       COALESCE(SUM(CASE WHEN status IN (
                           'paid','awaiting_stock','awaiting_info','processing','completed'
                       ) AND order_origin = 'customer' AND subtotal_amount > 0
                           THEN subtotal_amount - discount_amount ELSE 0 END), 0)
                           AS purchase_total,
                       MIN(CASE WHEN status IN (
                           'paid','awaiting_stock','awaiting_info','processing','completed'
                       ) AND order_origin = 'customer' AND subtotal_amount > 0
                              AND paid_at IS NOT NULL THEN paid_at END)
                           AS first_purchase_at,
                       MAX(CASE WHEN status IN (
                           'paid','awaiting_stock','awaiting_info','processing','completed'
                       ) AND order_origin = 'customer' AND subtotal_amount > 0
                              AND paid_at IS NOT NULL THEN paid_at END)
                           AS last_purchase_at
                FROM orders WHERE user_id = ?
                """,
                (target_id,),
            )
            order_count = int((aggregate or {}).get("order_count") or 0)
            purchase_total = int((aggregate or {}).get("purchase_total") or 0)
            first_purchase_at = (aggregate or {}).get("first_purchase_at") or "—"
            last_purchase_at = (aggregate or {}).get("last_purchase_at") or "—"
        referral = self.db.referral_summary(target_id)
        transactions = self.db.list_user_transactions(target_id, limit=10)
        transaction_count = self.db.count_user_transactions(target_id)
        currency = getattr(self.settings, "currency_label", "تومان")
        name = " ".join(filter(None, (target.get("first_name"), target.get("last_name")))) or "—"
        lines = [
            "<b>کاربر</b>"
            f"\nشناسه داخلی: <code>{target['id']}</code>"
            f"\nchat_id: <code>{target.get('chat_id') or '—'}</code>"
            f"\nusername: @{escape(target.get('username') or '—')}"
            f"\nنام: {escape(name)}"
            f"\nتلفن: <code>{escape(target.get('phone') or '—')}</code>"
            f"\nایمیل: {escape(target.get('email') or '—')}"
            f"\nوضعیت: {'مسدود' if target.get('is_blocked') else 'فعال'}"
            f"\nموجودی: {money(balance, currency)}"
            f"\nتعداد سفارش: {order_count}"
            f"\nمجموع خرید: {money(purchase_total, currency)}"
            f"\nتاریخ عضویت: {escape(target.get('joined_at') or '—')}"
            f"\nاولین خرید: {escape(first_purchase_at)}"
            f"\nآخرین خرید: {escape(last_purchase_at)}"
            f"\nدعوت‌شده‌ها: {int(referral.get('invited_count') or 0)}"
            f"\nپاداش دعوت: {money(int(referral.get('reward_total') or 0), currency)}"
            f"\nتعداد تراکنش‌ها: {transaction_count:,}"
        ]
        if orders:
            lines.append("\n<b>آخرین سفارش‌ها:</b>")
            lines.extend(
                f"<code>{escape(item['order_number'])}</code> | {escape(item['status'])}"
                for item in orders[:5]
            )
        if transactions:
            lines.append("\n<b>آخرین تراکنش‌ها:</b>")
            lines.extend(
                f"{escape(str(item['created_at'])[:16])} | "
                f"{money(int(item['amount_signed']), currency)} | "
                f"نوع: {escape(texts.transaction_type(item.get('entry_type'), item.get('method')))} | "
                f"دلیل: {escape(item.get('reason') or '—')} | "
                f"{escape(item.get('payment_number') or item.get('order_number') or '—')}"
                for item in transactions
            )
        stable_id = target.get("chat_id") or target["id"]
        history_hint = (
            "\n<b>تاریخچه کامل:</b>"
            f"\n<code>/user_orders {stable_id}</code>"
            f"\n<code>/user_transactions {stable_id}</code>"
            f"\n<code>/user_referrals {stable_id}</code>"
            f"\n<code>/user_rewards {stable_id}</code>"
        )
        if self._button_context is None:
            lines.append(history_hint)
        self._send_blocks(self._chat_id(message, user), lines)

    def _user_orders(
        self,
        rest: str,
        message: dict[str, Any],
        user: dict[str, Any],
        _admin: dict[str, Any],
    ) -> None:
        tokens = rest.split()
        if not tokens or len(tokens) > 3:
            raise AdminInputError(
                "نمونه: /user_orders CHAT_ID|@username [STATUS|all] [PAGE|ORDER_NUMBER]"
            )
        target = self._find_user(tokens[0])
        tail = tokens[1:]
        if len(tail) == 1 and tail[0].upper().startswith(("ORD-", "ADM-")):
            order = self.db.get_order_by_number(tail[0])
            if order is None or int(order["user_id"]) != int(target["id"]):
                raise AdminInputError("سفارش موردنظر برای این کاربر پیدا نشد.")
            self._send_order_details(self._chat_id(message, user), order)
            return

        page = 1
        if tail and re.fullmatch(r"[+-]?\d+", normalize_digits(tail[-1])):
            page = _page_number(tail.pop())
        if len(tail) > 1:
            raise AdminInputError(
                "نمونه: /user_orders CHAT_ID|@username [STATUS|all] [PAGE|ORDER_NUMBER]"
            )
        status = tail[0].lower() if tail else None
        if status == "all":
            status = None
        if status is not None and status not in _ORDER_STATUSES:
            raise AdminInputError("وضعیت سفارش معتبر نیست.")
        target_id = int(target["id"])
        total = self.db.count_orders(user_id=target_id, status=status)
        pages, offset = _page_bounds(total, page)
        orders = self.db.list_orders(
            user_id=target_id,
            status=status,
            limit=_ADMIN_PAGE_SIZE,
            offset=offset,
        )
        rows = [
            f"<code>{escape(item['order_number'])}</code> | "
            f"{escape(item['product_name_snapshot'])} | {escape(item['status'])}"
            for item in orders
        ]
        stable_id = target.get("chat_id") or target_id
        command_prefix = f"/user_orders {stable_id}"
        if status is not None:
            command_prefix += f" {status}"
        self._send_page(
            self._chat_id(message, user),
            title=f"سفارش‌های کاربر {stable_id}",
            rows=rows,
            total=total,
            page=page,
            pages=pages,
            command_prefix=command_prefix,
            empty_text="سفارشی برای این کاربر پیدا نشد.",
            tail=(
                "جست‌وجوی سفارش همین کاربر: "
                f"<code>/user_orders {stable_id} ORDER_NUMBER</code>",
                "جزئیات مستقیم: <code>/order ORDER_NUMBER</code>",
            ),
        )

    def _user_transactions(
        self,
        rest: str,
        message: dict[str, Any],
        user: dict[str, Any],
        _admin: dict[str, Any],
    ) -> None:
        tokens = rest.split()
        if len(tokens) not in {1, 2}:
            raise AdminInputError(
                "نمونه: /user_transactions CHAT_ID|@username [PAGE]"
            )
        target = self._find_user(tokens[0])
        page = _page_number(tokens[1]) if len(tokens) == 2 else 1
        target_id = int(target["id"])
        total = self.db.count_user_transactions(target_id)
        pages, offset = _page_bounds(total, page)
        transactions = self.db.list_user_transactions(
            target_id, limit=_ADMIN_PAGE_SIZE, offset=offset
        )
        currency = getattr(self.settings, "currency_label", "تومان")
        rows = [
            f"{escape(str(item['created_at'])[:19])} | "
            f"{money(int(item['amount_signed']), currency)} | "
            f"نوع: {escape(texts.transaction_type(item.get('entry_type'), item.get('method')))} | "
            f"دلیل: {escape(item.get('reason') or '—')} | "
            f"{escape(item.get('payment_number') or item.get('order_number') or '—')}"
            for item in transactions
        ]
        stable_id = target.get("chat_id") or target_id
        self._send_page(
            self._chat_id(message, user),
            title=f"تراکنش‌های کاربر {stable_id}",
            rows=rows,
            total=total,
            page=page,
            pages=pages,
            command_prefix=f"/user_transactions {stable_id}",
            empty_text="تراکنشی برای این کاربر پیدا نشد.",
        )

    def _user_referrals(
        self,
        rest: str,
        message: dict[str, Any],
        user: dict[str, Any],
        _admin: dict[str, Any],
    ) -> None:
        tokens = rest.split()
        if len(tokens) not in {1, 2}:
            raise AdminInputError("نمونه: /user_referrals CHAT_ID|@username [PAGE]")
        target = self._find_user(tokens[0])
        page = _page_number(tokens[1]) if len(tokens) == 2 else 1
        target_id = int(target["id"])
        total = self.db.count_user_referrals(target_id)
        pages, offset = _page_bounds(total, page)
        referrals = self.db.list_user_referrals(
            target_id, limit=_ADMIN_PAGE_SIZE, offset=offset
        )
        currency = getattr(self.settings, "currency_label", "تومان")
        rows = []
        for item in referrals:
            username = (
                f"@{escape(item['invitee_username'])}"
                if item.get("invitee_username")
                else "بدون username"
            )
            rows.append(
                f"<code>{item.get('invitee_chat_id') or '—'}</code> | {username} | "
                f"{escape(item['status'])} | {escape(str(item['created_at'])[:10])} | "
                f"{int(item.get('reward_count') or 0):,} پاداش، "
                f"{money(int(item.get('reward_total') or 0), currency)}"
            )
        stable_id = target.get("chat_id") or target_id
        self._send_page(
            self._chat_id(message, user),
            title=f"زیرمجموعه‌های کاربر {stable_id}",
            rows=rows,
            total=total,
            page=page,
            pages=pages,
            command_prefix=f"/user_referrals {stable_id}",
            empty_text="زیرمجموعه‌ای برای این کاربر پیدا نشد.",
            tail=(
                f"جزئیات پاداش‌ها: <code>/user_rewards {stable_id}</code>",
            ),
        )

    def _user_rewards(
        self,
        rest: str,
        message: dict[str, Any],
        user: dict[str, Any],
        _admin: dict[str, Any],
    ) -> None:
        tokens = rest.split()
        if len(tokens) not in {1, 2}:
            raise AdminInputError("نمونه: /user_rewards CHAT_ID|@username [PAGE]")
        target = self._find_user(tokens[0])
        page = _page_number(tokens[1]) if len(tokens) == 2 else 1
        target_id = int(target["id"])
        total = self.db.count_user_reward_events(target_id)
        pages, offset = _page_bounds(total, page)
        rewards = self.db.list_user_reward_events(
            target_id, limit=_ADMIN_PAGE_SIZE, offset=offset
        )
        currency = getattr(self.settings, "currency_label", "تومان")
        rows = []
        for item in rewards:
            invitee = (
                f"@{escape(item['invitee_username'])}"
                if item.get("invitee_username")
                else str(item.get("invitee_chat_id") or "—")
            )
            rows.append(
                f"پاداش <code>{item['id']}</code> | {escape(item['event_type'])} | "
                f"{money(int(item['amount']), currency)} | زیرمجموعه {invitee} | "
                f"سفارش {escape(item.get('order_number') or '—')} | "
                f"{escape(str(item['created_at'])[:19])}"
            )
        stable_id = target.get("chat_id") or target_id
        self._send_page(
            self._chat_id(message, user),
            title=f"پاداش‌های دریافتی کاربر {stable_id}",
            rows=rows,
            total=total,
            page=page,
            pages=pages,
            command_prefix=f"/user_rewards {stable_id}",
            empty_text="پاداشی برای این کاربر پیدا نشد.",
        )

    def _block(self, rest: str, message: dict[str, Any], user: dict[str, Any], _admin: dict[str, Any]) -> None:
        target = self._find_user(rest)
        self.db.set_user_blocked(int(target["id"]), True)
        self._send(self._chat_id(message, user), "کاربر مسدود شد.")

    def _unblock(self, rest: str, message: dict[str, Any], user: dict[str, Any], _admin: dict[str, Any]) -> None:
        target = self._find_user(rest)
        self.db.set_user_blocked(int(target["id"]), False)
        self._send(self._chat_id(message, user), "کاربر از حالت مسدود خارج شد.")

    def _wallet_adjust(self, rest: str, message: dict[str, Any], user: dict[str, Any], admin: dict[str, Any]) -> None:
        parts = self._command_parts(rest, 3)
        if len(parts) != 3:
            raise AdminInputError("نمونه: /wallet_adjust CHAT_ID | AMOUNT_SIGNED | دلیل")
        if not parts[0].strip().lstrip("-").isdigit():
            raise AdminInputError(
                "برای تغییر مالی کیف پول باید chat_id عددی کاربر را وارد کنید."
            )
        target = self.db.get_user_by_chat_id(_as_int(parts[0], "chat_id"))
        if target is None:
            raise AdminInputError("کاربر پیدا نشد.")
        try:
            amount = int(normalize_digits(parts[1]).replace(",", "").replace("٬", ""))
        except ValueError as exc:
            raise AdminInputError("مبلغ باید عدد صحیح علامت‌دار باشد.") from exc
        if amount == 0:
            raise AdminInputError("مبلغ تغییر نمی‌تواند صفر باشد.")
        entry = self.db.adjust_wallet(
            int(target["id"]),
            amount,
            reason=parts[2],
            idempotency_key=(
                f"admin:{admin['id']}:wallet:"
                f"{self._chat_id(message, user)}:{message.get('message_id', 'unknown')}"
            ),
            actor_admin_id=int(admin["id"]),
        )
        currency = getattr(self.settings, "currency_label", "تومان")
        self._send(
            self._chat_id(message, user),
            f"موجودی اصلاح شد. موجودی جدید: {money(int(entry['balance']), currency)}",
        )

    def _message(self, rest: str, message: dict[str, Any], user: dict[str, Any], admin: dict[str, Any]) -> None:
        parts = self._command_parts(rest, 2)
        if len(parts) != 2:
            raise AdminInputError("نمونه: /message CHAT_ID | متن")
        target = self._find_user(parts[0])
        body = format_admin_text(parts[1])
        update_key = self._admin_idempotency_key(message, "direct-message")
        key = update_key or (
            f"admin-message:{admin['id']}:"
            f"{self._chat_id(message, user)}:{message.get('message_id', 'unknown')}"
        )
        self.db.queue_outbound_message(
            body,
            recipient_user_id=int(target["id"]),
            idempotency_key=key,
        )
        self._send(
            self._chat_id(message, user),
            "پیام در صف ارسال پایدار قرار گرفت.",
        )

    # -- Discounts -------------------------------------------------------

    def _discounts(self, rest: str, message: dict[str, Any], user: dict[str, Any], _admin: dict[str, Any]) -> None:
        page = _page_number(rest) if rest else 1
        items, total, pages = self._management_rows(
            page, "SELECT * FROM discounts ORDER BY id DESC"
        )
        lines = []
        for item in items:
            lines.append(
                f"<code>{escape(item['code'])}</code> | {escape(item['discount_type'])} "
                f"{item['value']} | {item['used_count']}/{item.get('max_uses') or '∞'} | "
                f"{'فعال' if item['is_active'] else 'غیرفعال'}"
            )
        self._send_page(
            self._chat_id(message, user), title="کدهای تخفیف", rows=lines,
            total=total, page=page, pages=pages, command_prefix="/discounts",
            empty_text="کد تخفیفی ثبت نشده است.",
        )

    def _discount_add(self, rest: str, message: dict[str, Any], user: dict[str, Any], _admin: dict[str, Any]) -> None:
        parts = self._command_parts(rest, 7)
        if len(parts) not in {7, 10} or (len(parts) == 10 and any(not value for value in parts[7:])):
            raise AdminInputError(
                "نمونه: /discount_add CODE | fixed|percent | VALUE | MAX_USES|0 | "
                "PRODUCT_ID|0 | USER_CHAT_ID|0 | END_DATE|0 | "
                "MINIMUM|0 | PER_USER_LIMIT|0 | START_DATE|0"
            )
        discount_type = parts[1].lower()
        if discount_type not in {"fixed", "percent"}:
            raise AdminInputError("نوع تخفیف باید fixed یا percent باشد.")
        value = parse_amount(parts[2])
        if discount_type == "percent" and value > 100:
            raise AdminInputError("تخفیف درصدی نمی‌تواند بیش از ۱۰۰ باشد.")
        try:
            max_uses_raw = int(normalize_digits(parts[3]))
            product_raw = int(normalize_digits(parts[4]))
            user_raw = int(normalize_digits(parts[5]))
            minimum_raw = int(normalize_digits(parts[7])) if len(parts) == 10 else 0
            per_user_raw = int(normalize_digits(parts[8])) if len(parts) == 10 else 0
        except ValueError as exc:
            raise AdminInputError("محدودیت‌های عددی تخفیف باید عدد صحیح باشند.") from exc
        if min(max_uses_raw, product_raw, minimum_raw, per_user_raw) < 0:
            raise AdminInputError("محدودیت‌های تخفیف نمی‌توانند منفی باشند.")
        ends_at = None
        if parts[6] != "0":
            _, ends_at = _date_range(
                parts[6], parts[6], getattr(self.settings, "timezone", "UTC")
            )
        starts_at = None
        if len(parts) == 10 and parts[9] != "0":
            starts_at, _ = _date_range(
                parts[9], parts[9], getattr(self.settings, "timezone", "UTC")
            )
        user_id = None
        if user_raw:
            target = self._find_user(str(user_raw))
            user_id = int(target["id"])
        item = self.db.create_discount(
            parts[0],
            discount_type=discount_type,
            value=value,
            max_uses=max_uses_raw or None,
            product_id=product_raw or None,
            user_id=user_id,
            minimum_order_amount=minimum_raw,
            per_user_limit=per_user_raw or None,
            starts_at=starts_at,
            ends_at=ends_at,
            active=True,
        )
        self._send(self._chat_id(message, user), f"کد تخفیف <code>{escape(item['code'])}</code> ثبت شد.")

    def _discount_toggle(self, rest: str, message: dict[str, Any], user: dict[str, Any], _admin: dict[str, Any]) -> None:
        code = rest.strip().upper()
        if not code:
            raise AdminInputError("کد تخفیف الزامی است.")
        current = self._query_one(
            "SELECT * FROM discounts WHERE code_key = ?", (code.casefold(),)
        )
        if current is None:
            raise AdminInputError("کد تخفیف پیدا نشد.")
        active = self._admin_toggle_target(
            message,
            f"discount:{int(current['id'])}:active",
            bool(current["is_active"]),
        )
        method = self._public("set_discount_active")
        if method is not None:
            method(code, active)
        else:
            self._execute(
                "UPDATE discounts SET is_active = ?, updated_at = ? WHERE id = ?",
                (int(active), _utc_timestamp(), int(current["id"])),
            )
        self._send(self._chat_id(message, user), f"کد تخفیف {'فعال' if active else 'غیرفعال'} شد.")

    def _discount_delete(self, rest: str, message: dict[str, Any], user: dict[str, Any], _admin: dict[str, Any]) -> None:
        code = rest.strip().upper()
        if not code:
            raise AdminInputError("کد تخفیف الزامی است.")
        if (
            self._query_one(
                "SELECT id FROM discounts WHERE code_key = ?", (code.casefold(),)
            )
            is None
            and self._admin_update_is_replay(message)
        ):
            self._send(
                self._chat_id(message, user),
                f"کد تخفیف <code>{escape(code)}</code> قبلاً حذف شده است.",
            )
            return
        item = self.db.delete_discount(code)
        self._send(
            self._chat_id(message, user),
            f"کد تخفیف <code>{escape(item['code'])}</code> حذف شد.",
        )

    # -- Tickets and FAQ -------------------------------------------------

    def _tickets(self, rest: str, message: dict[str, Any], user: dict[str, Any], _admin: dict[str, Any]) -> None:
        tokens = rest.split()
        page = 1
        if tokens and re.fullmatch(r"[+-]?\d+", normalize_digits(tokens[-1])):
            page = _page_number(tokens.pop())
        if len(tokens) > 1:
            raise AdminInputError("نمونه: /tickets [open|answered|closed|all] [PAGE]")
        status = tokens[0].lower() if tokens else None
        if status == "all":
            status = None
        if status is not None and status not in _TICKET_STATUSES:
            raise AdminInputError("وضعیت تیکت باید open، answered یا closed باشد.")
        total = self.db.count_tickets(status=status)
        pages, offset = _page_bounds(total, page)
        tickets = self.db.list_tickets(
            status=status, limit=_ADMIN_PAGE_SIZE, offset=offset
        )
        rows = []
        for item in tickets:
            rows.append(
                f"<code>{escape(item['ticket_number'])}</code> | "
                f"{escape(item['subject'])} | {escape(item['status'])} | کاربر {item['user_id']}"
            )
        command_prefix = "/tickets"
        if tokens:
            command_prefix += " " + tokens[0]
        self._send_page(
            self._chat_id(message, user),
            title="تیکت‌ها",
            rows=rows,
            total=total,
            page=page,
            pages=pages,
            command_prefix=command_prefix,
            empty_text="تیکتی پیدا نشد.",
            tail=("جزئیات تیکت: <code>/ticket TICKET_NUMBER</code>",),
        )

    def _ticket(self, rest: str, message: dict[str, Any], user: dict[str, Any], _admin: dict[str, Any]) -> None:
        ticket = self._require_ticket(rest)
        entries = self.db.list_ticket_messages(int(ticket["id"]))
        blocks = [
            "<b>جزئیات تیکت</b>"
            f"\nشماره: <code>{escape(ticket['ticket_number'])}</code>"
            f"\nموضوع: {escape(ticket['subject'])}"
            f"\nوضعیت: <code>{escape(ticket['status'])}</code>"
            f"\nتعداد پیام‌ها: {len(entries):,}"
        ]
        for entry in entries:
            sender = "مدیریت" if entry["sender_type"] == "admin" else "کاربر"
            body = str(entry["body"] or "")
            for index in range(0, max(1, len(body)), 600):
                blocks.append(f"<b>{sender}</b>: {escape(body[index:index + 600])}")
            if entry.get("attachment_file_id"):
                blocks.append(
                    f"پیوست {int(entry['id'])} ذخیره شده است؛ از دکمه دریافت پیوست استفاده کنید."
                    if self._button_context is not None else
                    "پیوست ذخیره شده است؛ دریافت امن: "
                    f"<code>/ticket_attachment {int(entry['id'])}</code>"
                )
        self._send_blocks(self._chat_id(message, user), blocks)

    def _ticket_attachment(
        self,
        rest: str,
        message: dict[str, Any],
        user: dict[str, Any],
        admin: dict[str, Any],
    ) -> None:
        if self._active_role(admin) not in {"owner", "admin", "support"}:
            raise AdminInputError("دسترسی به پیوست تیکت مجاز نیست.")
        message_id = _as_int(rest, "شناسه پیام تیکت")
        entry = self.db.get_ticket_message(message_id)
        if entry is None:
            raise AdminInputError("پیام تیکت پیدا نشد.")
        ticket = self.db.get_ticket(int(entry["ticket_id"]))
        if ticket is None or int(ticket["id"]) != int(entry["ticket_id"]):
            raise AdminInputError("تیکت این پیام در دسترس نیست.")
        file_id = str(entry.get("attachment_file_id") or "").strip()
        kind = str(entry.get("attachment_kind") or "").strip().lower()
        if not file_id:
            raise AdminInputError("این پیام تیکت پیوستی ندارد.")
        if kind not in {"photo", "document"}:
            raise AdminInputError("نوع پیوست ذخیره‌شده معتبر نیست.")
        caption = (
            f"<b>پیوست تیکت {escape(ticket['ticket_number'])}</b>\n"
            f"شناسه پیام: <code>{message_id}</code>"
        )
        chat_id = self._chat_id(message, user)
        if kind == "photo":
            self.telegram.send_photo(
                chat_id,
                file_id,
                caption=caption,
                parse_mode="HTML",
            )
        else:
            self.telegram.send_document(
                chat_id,
                file_id,
                caption=caption,
                parse_mode="HTML",
            )

    def _require_ticket(self, value: str) -> dict[str, Any]:
        ticket_number = value.strip()
        if not ticket_number:
            raise AdminInputError("شماره تیکت الزامی است.")
        ticket = self.db.get_ticket(ticket_number)
        if ticket is None:
            raise AdminInputError("تیکت پیدا نشد.")
        return ticket

    def _ticket_reply(self, rest: str, message: dict[str, Any], user: dict[str, Any], admin: dict[str, Any]) -> None:
        parts = self._command_parts(rest, 2)
        if len(parts) != 2:
            raise AdminInputError("نمونه: /ticket_reply TICKET_NUMBER | پاسخ")
        ticket = self._require_ticket(parts[0])
        notice = (
            "پاسخ جدیدی برای تیکت شما ثبت شد."
            f"\nشماره تیکت: <code>{escape(ticket['ticket_number'])}</code>"
            f"\n\n{escape(parts[1])}"
        )
        self._require_safe_notification_length(notice)
        notice_key = (
            f"ticket:{ticket['id']}:admin-notice:"
            f"{admin['id']}:{message.get('message_id', 'unknown')}"
        )
        self.db.add_ticket_message(
            int(ticket["id"]),
            parts[1],
            sender_type="admin",
            sender_id=int(admin["id"]),
            idempotency_key=(
                f"admin-ticket:{admin['id']}:"
                f"{self._chat_id(message, user)}:{message.get('message_id', 'unknown')}"
            ),
            outbound_body=notice,
            outbound_idempotency_key=notice_key,
        )
        target = self.db.get_user(int(ticket["user_id"]))
        delivered = self._deliver_prequeued_notification(
            target,
            notice,
            idempotency_key=notice_key,
        )
        if delivered is False:
            self._send(
                self._chat_id(message, user),
                "پاسخ تیکت ثبت شد؛ اعلان کاربر در صف تلاش مجدد قرار گرفت.",
            )
            return
        self._send(self._chat_id(message, user), "پاسخ تیکت ثبت و برای کاربر ارسال شد.")

    def _ticket_close(self, rest: str, message: dict[str, Any], user: dict[str, Any], admin: dict[str, Any]) -> None:
        ticket = self._require_ticket(rest)
        notice = f"تیکت <code>{escape(ticket['ticket_number'])}</code> بسته شد."
        self._require_safe_notification_length(notice)
        notice_key = (
            f"ticket:{ticket['id']}:closed-notice:"
            f"{admin['id']}:{message.get('message_id', 'unknown')}"
        )
        self.db.set_ticket_status(
            int(ticket["id"]),
            "closed",
            assigned_admin_id=int(admin["id"]),
            outbound_body=notice,
            outbound_idempotency_key=notice_key,
        )
        target = self.db.get_user(int(ticket["user_id"]))
        self._deliver_prequeued_notification(
            target,
            notice,
            idempotency_key=notice_key,
        )
        self._send(self._chat_id(message, user), "تیکت بسته شد.")

    def _ticket_status(self, rest: str, message: dict[str, Any], user: dict[str, Any], admin: dict[str, Any]) -> None:
        parts = rest.split()
        if len(parts) != 2 or parts[1].lower() not in _TICKET_STATUSES:
            raise AdminInputError(
                "نمونه: /ticket_status TICKET_NUMBER open|answered|closed"
            )
        ticket = self._require_ticket(parts[0])
        requested_status = parts[1].lower()
        notice = (
            f"وضعیت تیکت <code>{escape(ticket['ticket_number'])}</code> "
            f"به <code>{escape(requested_status)}</code> تغییر کرد."
        )
        self._require_safe_notification_length(notice)
        notice_key = (
            f"ticket:{ticket['id']}:status:{requested_status}:"
            f"{message.get('message_id', 'unknown')}"
        )
        updated = self.db.set_ticket_status(
            int(ticket["id"]),
            requested_status,
            assigned_admin_id=int(admin["id"]),
            outbound_body=notice,
            outbound_idempotency_key=notice_key,
        )
        target = self.db.get_user(int(ticket["user_id"]))
        self._deliver_prequeued_notification(
            target,
            notice,
            idempotency_key=notice_key,
        )
        self._send(
            self._chat_id(message, user),
            f"وضعیت تیکت به <code>{escape(updated['status'])}</code> تغییر کرد.",
        )

    def _faq_categories(self, rest: str, message: dict[str, Any], user: dict[str, Any], _admin: dict[str, Any]) -> None:
        page = _page_number(rest) if rest else 1
        categories, total, pages = self._management_rows(
            page, "SELECT * FROM faq_categories ORDER BY sort_order, id"
        )
        lines = []
        for category in categories:
            count = len(self.db.list_faqs(category_id=int(category["id"]), active_only=False))
            lines.append(
                f"<code>{category['id']}</code> | {escape(category['name'])} | "
                f"{'فعال' if category['is_active'] else 'غیرفعال'} | {count} سوال"
            )
        self._send_page(
            self._chat_id(message, user), title="دسته‌های سوالات متداول", rows=lines,
            total=total, page=page, pages=pages, command_prefix="/faq_categories",
            empty_text="دسته‌ای ثبت نشده است.",
        )

    def _faq_category_add(self, rest: str, message: dict[str, Any], user: dict[str, Any], _admin: dict[str, Any]) -> None:
        if not rest.strip():
            raise AdminInputError("عنوان دسته سوالات الزامی است.")
        category = self.db.create_faq_category(rest.strip())
        self._send(
            self._chat_id(message, user),
            f"دسته سوالات ثبت شد. شناسه: <code>{category['id']}</code>",
        )

    def _faq_category_toggle(self, rest: str, message: dict[str, Any], user: dict[str, Any], _admin: dict[str, Any]) -> None:
        category_id = _as_int(rest, "شناسه دسته سوالات")
        category = self.db.get_faq_category(category_id)
        if category is None:
            raise AdminInputError("دسته سوالات پیدا نشد.")
        active = self._admin_toggle_target(
            message,
            f"faq-category:{category_id}:active",
            bool(category["is_active"]),
        )
        updated = self.db.set_faq_category_active(category_id, active)
        self._send(
            self._chat_id(message, user),
            f"دسته سوالات {'فعال' if updated['is_active'] else 'غیرفعال'} شد.",
        )

    def _faq_category_set(self, rest: str, message: dict[str, Any], user: dict[str, Any], _admin: dict[str, Any]) -> None:
        parts = self._command_parts(rest, 3)
        if len(parts) != 3:
            raise AdminInputError("نمونه: /faq_category_set CATEGORY_ID | name|sort_order | VALUE")
        category_id = _as_int(parts[0], "شناسه دسته سوالات")
        field = parts[1].lower()
        if field == "name":
            value: Any = parts[2].strip()
            if not value:
                raise AdminInputError("عنوان دسته سوالات نمی‌تواند خالی باشد.")
        elif field == "sort_order":
            try:
                value = int(normalize_digits(parts[2]).strip())
            except ValueError as exc:
                raise AdminInputError("ترتیب نمایش باید عدد صحیح باشد.") from exc
        else:
            raise AdminInputError("فیلد دسته سوالات باید name یا sort_order باشد.")
        category = self.db.update_faq_category(category_id, **{field: value})
        self._send(
            self._chat_id(message, user),
            f"دسته سوالات <code>{category['id']}</code> به‌روزرسانی شد.",
        )

    def _faq_category_delete(self, rest: str, message: dict[str, Any], user: dict[str, Any], _admin: dict[str, Any]) -> None:
        category_id = _as_int(rest, "شناسه دسته سوالات")
        if (
            self.db.get_faq_category(category_id) is None
            and self._admin_update_is_replay(message)
        ):
            self._send(
                self._chat_id(message, user),
                f"دسته سوالات <code>{category_id}</code> قبلاً حذف شده است.",
            )
            return
        self.db.delete_faq_category(category_id)
        self._send(
            self._chat_id(message, user),
            f"دسته سوالات <code>{category_id}</code> حذف شد.",
        )

    def _faqs(self, rest: str, message: dict[str, Any], user: dict[str, Any], _admin: dict[str, Any]) -> None:
        tokens = rest.split()
        if len(tokens) > 2:
            raise AdminInputError("نمونه: /faqs [CATEGORY_ID|all] [PAGE]")
        category_id = (
            _as_int(tokens[0], "شناسه دسته سوالات")
            if tokens and tokens[0].lower() != "all" else None
        )
        if category_id is not None and self.db.get_faq_category(category_id) is None:
            raise AdminInputError("دسته سوالات پیدا نشد.")
        page = _page_number(tokens[1]) if len(tokens) == 2 else 1
        query = "SELECT * FROM faqs"
        parameters: tuple[Any, ...] = ()
        if category_id is not None:
            query += " WHERE category_id = ?"
            parameters = (category_id,)
        items, total, pages = self._management_rows(
            page, query + " ORDER BY sort_order, id", parameters
        )
        lines = []
        for item in items:
            lines.append(
                f"<code>{item['id']}</code> | دسته {item.get('category_id') or '—'} | "
                f"{escape(item['question'])} | {'فعال' if item['is_active'] else 'غیرفعال'}"
            )
        self._send_page(
            self._chat_id(message, user), title="سوالات متداول", rows=lines,
            total=total, page=page, pages=pages,
            command_prefix=f"/faqs {category_id if category_id is not None else 'all'}",
            empty_text="سوالی ثبت نشده است.",
        )

    def _faq_add(self, rest: str, message: dict[str, Any], user: dict[str, Any], _admin: dict[str, Any]) -> None:
        parts = self._command_parts(rest, 3)
        if len(parts) != 3:
            raise AdminInputError("نمونه: /faq_add دسته | سوال | جواب")
        if parts[2].lower().startswith("html:"):
            format_admin_text(parts[2])
        if self._button_context is not None:
            category = self.db.get_faq_category(_as_int(parts[0], "شناسه دسته سوالات"))
            if category is None:
                raise AdminInputError("دسته سوالات پیدا نشد.")
        else:
            category = self.db.create_faq_category(parts[0])
        item = self.db.create_faq(parts[1], parts[2], category_id=int(category["id"]))
        self._send(self._chat_id(message, user), f"سؤال متداول ثبت شد. شناسه: <code>{item['id']}</code>")

    def _faq_toggle(self, rest: str, message: dict[str, Any], user: dict[str, Any], _admin: dict[str, Any]) -> None:
        faq_id = _as_int(rest, "شناسه FAQ")
        item = self.db.get_faq(faq_id)
        if item is None:
            raise AdminInputError("سؤال متداول پیدا نشد.")
        active = self._admin_toggle_target(
            message,
            f"faq:{faq_id}:active",
            bool(item["is_active"]),
        )
        method = self._public("set_faq_active")
        if method is not None:
            method(faq_id, active)
        else:
            self._execute(
                "UPDATE faqs SET is_active = ?, updated_at = ? WHERE id = ?",
                (int(active), _utc_timestamp(), faq_id),
            )
        self._send(self._chat_id(message, user), f"سؤال متداول {'فعال' if active else 'غیرفعال'} شد.")

    def _faq_set(self, rest: str, message: dict[str, Any], user: dict[str, Any], _admin: dict[str, Any]) -> None:
        parts = self._command_parts(rest, 3)
        if len(parts) != 3:
            raise AdminInputError("نمونه: /faq_set FAQ_ID | question|answer|category|sort_order | VALUE")
        faq_id = _as_int(parts[0], "شناسه FAQ")
        field = parts[1].lower()
        if field in {"question", "answer"}:
            value: Any = parts[2].strip()
            if not value:
                raise AdminInputError("متن سوال یا جواب نمی‌تواند خالی باشد.")
            if field == "answer" and value.lower().startswith("html:"):
                format_admin_text(value)
            column = field
        elif field == "category":
            raw_category = normalize_digits(parts[2]).strip().lower()
            value = None if raw_category in {"0", "none", "null", "بدون دسته"} else _as_int(
                raw_category,
                "شناسه دسته سوالات",
            )
            column = "category_id"
        elif field == "sort_order":
            try:
                value = int(normalize_digits(parts[2]).strip())
            except ValueError as exc:
                raise AdminInputError("ترتیب نمایش باید عدد صحیح باشد.") from exc
            column = "sort_order"
        else:
            raise AdminInputError("فیلد FAQ باید question، answer، category یا sort_order باشد.")
        item = self.db.update_faq(faq_id, **{column: value})
        self._send(
            self._chat_id(message, user),
            f"سؤال متداول <code>{item['id']}</code> به‌روزرسانی شد.",
        )

    def _faq_delete(self, rest: str, message: dict[str, Any], user: dict[str, Any], _admin: dict[str, Any]) -> None:
        faq_id = _as_int(rest, "شناسه FAQ")
        if self.db.get_faq(faq_id) is None and self._admin_update_is_replay(message):
            self._send(
                self._chat_id(message, user),
                f"سؤال متداول <code>{faq_id}</code> قبلاً حذف شده است.",
            )
            return
        item = self.db.delete_faq(faq_id)
        self._send(
            self._chat_id(message, user),
            f"سؤال متداول <code>{item['id']}</code> حذف شد.",
        )

    # -- Broadcasts and reports -----------------------------------------

    def _broadcast_all(self, rest: str, message: dict[str, Any], user: dict[str, Any], admin: dict[str, Any]) -> None:
        if not rest.strip():
            raise AdminInputError("متن پیام الزامی است.")
        self._preview_broadcast({"kind": "all"}, rest.strip(), message, user, admin)

    def _broadcast_joined(self, rest: str, message: dict[str, Any], user: dict[str, Any], admin: dict[str, Any]) -> None:
        parts = self._command_parts(rest, 3)
        if len(parts) != 3:
            raise AdminInputError("نمونه: /broadcast_joined FROM_DATE | TO_DATE | متن")
        start, end = _date_range(
            parts[0], parts[1], getattr(self.settings, "timezone", "UTC")
        )
        self._preview_broadcast(
            {"kind": "joined", "start": start, "end": end},
            parts[2],
            message,
            user,
            admin,
        )

    def _broadcast_product(self, rest: str, message: dict[str, Any], user: dict[str, Any], admin: dict[str, Any]) -> None:
        parts = self._command_parts(rest, 4)
        if len(parts) != 4:
            raise AdminInputError("نمونه: /broadcast_product PRODUCT_ID | FROM_DATE | TO_DATE | متن")
        product_id = _as_int(parts[0], "شناسه محصول")
        if self.db.get_product(product_id) is None:
            raise AdminInputError("محصول پیدا نشد.")
        start, end = _date_range(
            parts[1], parts[2], getattr(self.settings, "timezone", "UTC")
        )
        self._preview_broadcast(
            {"kind": "product", "product_id": product_id, "start": start, "end": end},
            parts[3],
            message,
            user,
            admin,
        )

    def _preview_broadcast(
        self,
        audience: Mapping[str, Any],
        body: str,
        message: Mapping[str, Any],
        user: Mapping[str, Any],
        _admin: Mapping[str, Any],
    ) -> None:
        format_admin_text(body)
        target_count = self._broadcast_target_count(audience)
        token = secrets.token_urlsafe(6)
        payload = {
            "token": token, "audience": dict(audience), "body": body, "target_count": target_count,
        }
        if self._button_context is not None:
            payload["ui_input"] = self._button_context["state"]["execution_input"]
        self.db.set_user_state(
            int(user["id"]),
            "admin:broadcast",
            payload,
        )
        self._render_broadcast_preview(self._chat_id(message, user), payload)

    def _render_broadcast_preview(self, chat_id: int, payload: Mapping[str, Any]) -> None:
        rendered_body = format_admin_text(str(payload["body"]))
        token = payload["token"]
        target_count = int(payload["target_count"])
        markup = inline_keyboard(
            [
                [callback_button("تأیید ارسال", f"adm:broadcast:confirm:{token}", style="success")],
                [callback_button("لغو", f"adm:broadcast:cancel:{token}", style="danger")],
            ]
        )
        preview = clamp_text(rendered_body, 1200)
        self._send(
            chat_id,
            "<b>پیش‌نمایش ارسال گروهی</b>"
            f"\nتعداد مخاطبان هدف: <b>{target_count:,}</b>"
            f"\n\n{preview}",
            reply_markup=markup,
        )

    def _broadcast_target_count(self, audience: Mapping[str, Any]) -> int:
        public = self._public("count_broadcast_targets")
        if public is not None:
            return int(public(dict(audience)))
        kind = audience.get("kind")
        if kind == "all":
            row = self._query_one("SELECT COUNT(*) AS count FROM users WHERE is_blocked = 0 AND chat_id IS NOT NULL")
        elif kind == "joined":
            row = self._query_one(
                "SELECT COUNT(*) AS count FROM users "
                "WHERE is_blocked = 0 AND chat_id IS NOT NULL AND joined_at >= ? AND joined_at < ?",
                (audience["start"], audience["end"]),
            )
        elif kind == "product":
            row = self._query_one(
                """
                SELECT COUNT(DISTINCT u.id) AS count
                FROM users u JOIN orders o ON o.user_id = u.id
                WHERE u.is_blocked = 0 AND u.chat_id IS NOT NULL
                  AND o.product_id = ? AND o.paid_at >= ? AND o.paid_at < ?
                  AND o.order_origin = 'customer'
                  AND o.subtotal_amount > 0
                  AND o.status IN ('paid','awaiting_stock','awaiting_info','processing','completed')
                """,
                (audience["product_id"], audience["start"], audience["end"]),
            )
        else:
            raise AdminInputError("نوع مخاطبان ارسال گروهی معتبر نیست.")
        return int((row or {}).get("count", 0))

    def _enqueue_broadcast(
        self,
        audience: Mapping[str, Any],
        body: str,
        actor_admin_id: int,
        *,
        actor_user_id: int,
        batch_token: str,
    ) -> dict[str, Any]:
        targets = self._broadcast_targets(audience)
        token = str(batch_token).strip()
        if not token:
            raise AdminInputError("شناسه ارسال گروهی نامعتبر است.")
        rendered_body = format_admin_text(body)
        batch_key = f"broadcast:{actor_admin_id}:{token}"
        return self.db.queue_broadcast_batch(
            batch_key,
            actor_admin_id=actor_admin_id,
            actor_user_id=actor_user_id,
            recipient_user_ids=[int(target["id"]) for target in targets],
            body=rendered_body,
        )

    def _broadcast_targets(self, audience: Mapping[str, Any]) -> list[dict[str, Any]]:
        kind = audience.get("kind")
        if kind == "all":
            return self._query(
                "SELECT id, chat_id FROM users WHERE is_blocked = 0 AND chat_id IS NOT NULL ORDER BY id"
            )
        if kind == "joined":
            return self._query(
                "SELECT id, chat_id FROM users WHERE is_blocked = 0 AND chat_id IS NOT NULL "
                "AND joined_at >= ? AND joined_at < ? ORDER BY id",
                (audience["start"], audience["end"]),
            )
        if kind == "product":
            return self._query(
                """
                SELECT DISTINCT u.id, u.chat_id
                FROM users u JOIN orders o ON o.user_id = u.id
                WHERE u.is_blocked = 0 AND u.chat_id IS NOT NULL
                  AND o.product_id = ? AND o.paid_at >= ? AND o.paid_at < ?
                  AND o.order_origin = 'customer'
                  AND o.subtotal_amount > 0
                  AND o.status IN ('paid','awaiting_stock','awaiting_info','processing','completed')
                ORDER BY u.id
                """,
                (audience["product_id"], audience["start"], audience["end"]),
            )
        raise AdminInputError("نوع مخاطبان ارسال گروهی معتبر نیست.")

    def _report(self, rest: str, message: dict[str, Any], user: dict[str, Any], _admin: dict[str, Any]) -> None:
        parts = rest.split()
        if not parts or parts[0].lower() not in {"orders", "users", "finance"}:
            raise AdminInputError("نمونه‌ها را در /admin_help ببینید.")
        kind = parts[0].lower()
        status: str | None = None
        user_mode = "joined"
        product_id: int | None = None
        if kind == "orders":
            if len(parts) == 3:
                from_label, to_label = parts[1], parts[2]
            elif len(parts) == 4:
                status = None if parts[1].lower() == "all" else parts[1].lower()
                if status is not None and status not in _ORDER_STATUSES:
                    raise AdminInputError("وضعیت سفارش معتبر نیست.")
                from_label, to_label = parts[2], parts[3]
            else:
                raise AdminInputError("نمونه: /report orders [STATUS|all] FROM_DATE TO_DATE")
        elif kind == "users":
            if len(parts) == 3:
                from_label, to_label = parts[1], parts[2]
            elif len(parts) == 4 and parts[1].lower() in {"joined", "started"}:
                user_mode = "joined"
                from_label, to_label = parts[2], parts[3]
            elif len(parts) == 5 and parts[1].lower() == "product":
                user_mode = "product"
                if parts[2].lower() != "all":
                    product_id = _as_int(parts[2], "شناسه محصول")
                    if self.db.get_product(product_id) is None:
                        raise AdminInputError("محصول پیدا نشد.")
                from_label, to_label = parts[3], parts[4]
            else:
                raise AdminInputError(
                    "نمونه: /report users joined FROM TO یا "
                    "/report users product PRODUCT_ID|all FROM TO"
                )
        else:
            if len(parts) != 3:
                raise AdminInputError("نمونه: /report finance FROM_DATE TO_DATE")
            from_label, to_label = parts[1], parts[2]
        start, end = _date_range(
            from_label, to_label, getattr(self.settings, "timezone", "UTC")
        )
        summary = self.db.summary_report(start=start, end=end)
        rows = self._report_rows(
            kind,
            start,
            end,
            status=status,
            user_mode=user_mode,
            product_id=product_id,
        )
        if kind in {"orders", "finance"}:
            successful_statuses = {
                "paid",
                "awaiting_stock",
                "awaiting_info",
                "processing",
                "completed",
            }
            summary = {
                **summary,
                "order_count": len(rows),
                "completed_order_count": sum(
                    1 for row in rows if row.get("status") == "completed"
                ),
                "gross_revenue": sum(
                    (
                        int(row.get("gross_amount") or 0)
                        if kind == "finance"
                        else int(row.get("subtotal_amount") or 0)
                        - int(row.get("discount_amount") or 0)
                    )
                    for row in rows
                    if row.get("status") in successful_statuses
                ),
            }
        currency = getattr(self.settings, "currency_label", "تومان")
        order_count = len(rows) if kind == "orders" else int(summary.get("order_count") or 0)
        user_count = len(rows) if kind == "users" else int(summary.get("user_count") or 0)
        self._send(
            self._chat_id(message, user),
            "<b>گزارش</b>"
            f"\nبازه: {escape(from_label)} تا {escape(to_label)}"
            f"\nسفارش‌ها: {order_count:,}"
            f"\nتکمیل‌شده: {int(summary.get('completed_order_count') or 0):,}"
            f"\nدرآمد ناخالص: {money(int(summary.get('gross_revenue') or 0), currency)}"
            f"\nکاربران: {user_count:,}"
            f"\nتیکت باز: {int(summary.get('open_ticket_count') or 0):,}",
        )
        if rows:
            payload = self._csv_bytes(rows)
            self.telegram.send_document(
                self._chat_id(message, user),
                payload,
                filename=f"{kind}-{from_label}-{to_label}.csv",
                caption=f"گزارش CSV: {kind}",
                parse_mode="HTML",
            )

    def _report_rows(
        self,
        kind: str,
        start: str,
        end: str,
        *,
        status: str | None = None,
        user_mode: str = "joined",
        product_id: int | None = None,
    ) -> list[dict[str, Any]]:
        public = self._public("report_rows")
        if public is not None:
            return list(public(kind, start=start, end=end))
        if not hasattr(self.db, "path"):
            return []
        if kind == "orders":
            status_clause = " AND status = ?" if status is not None else ""
            parameters: list[Any] = [start, end]
            if status is not None:
                parameters.append(status)
            return self._query(
                f"""
                SELECT order_number, product_name_snapshot AS product, status,
                       subtotal_amount, discount_amount, external_paid_amount,
                       wallet_captured_amount, paid_at, created_at, completed_at
                FROM orders WHERE created_at >= ? AND created_at < ?{status_clause}
                ORDER BY id
                """,
                parameters,
            )
        if kind == "users":
            if user_mode == "product":
                product_clause = " AND o.product_id = ?" if product_id is not None else ""
                parameters = [start, end]
                if product_id is not None:
                    parameters.append(product_id)
                return self._query(
                    f"""
                    SELECT DISTINCT u.chat_id, u.username, u.first_name, u.last_name,
                           u.phone, u.email, u.joined_at, u.is_blocked
                    FROM users u JOIN orders o ON o.user_id = u.id
                    WHERE o.paid_at >= ? AND o.paid_at < ?{product_clause}
                      AND o.order_origin = 'customer'
                      AND o.subtotal_amount > 0
                      AND o.status IN ('paid','awaiting_stock','awaiting_info','processing','completed')
                    ORDER BY u.id
                    """,
                    parameters,
                )
            return self._query(
                """
                SELECT chat_id, username, first_name, last_name, phone, joined_at, is_blocked
                FROM users WHERE joined_at >= ? AND joined_at < ? ORDER BY id
                """,
                (start, end),
            )
        return self._query(
            """
            SELECT order_number, product_name_snapshot AS product, status,
                   subtotal_amount - discount_amount AS gross_amount,
                   external_paid_amount,
                   MAX(
                       0,
                       wallet_captured_amount - COALESCE((
                           SELECT SUM(we.amount_signed)
                           FROM wallet_entries we
                           WHERE we.order_id = orders.id
                             AND we.entry_type = 'order_refund'
                       ), 0)
                   ) AS net_wallet_amount,
                   paid_at, completed_at
            FROM orders
            WHERE paid_at >= ? AND paid_at < ?
              AND order_origin = 'customer'
              AND subtotal_amount > 0
              AND status IN ('paid','awaiting_stock','awaiting_info','processing','completed','refunded')
            ORDER BY paid_at, id
            """,
            (start, end),
        )

    @staticmethod
    def _csv_bytes(rows: Sequence[Mapping[str, Any]]) -> bytes:
        columns: list[str] = []
        for row in rows:
            for key in row:
                if key not in columns:
                    columns.append(key)
        output = io.StringIO(newline="")
        writer = csv.DictWriter(output, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            def safe_cell(value: Any) -> Any:
                rendered = (
                    json.dumps(value, ensure_ascii=False)
                    if isinstance(value, (dict, list))
                    else value
                )
                if isinstance(rendered, str) and rendered.lstrip().startswith(
                    ("=", "+", "-", "@")
                ):
                    return "'" + rendered
                return rendered

            writer.writerow(
                {
                    key: safe_cell(value)
                    for key, value in row.items()
                }
            )
        return output.getvalue().encode("utf-8-sig")

    # -- Referral rewards ------------------------------------------------

    def _rewards(self, rest: str, message: dict[str, Any], user: dict[str, Any], _admin: dict[str, Any]) -> None:
        page = _page_number(rest) if rest else 1
        items, total, pages = self._management_rows(
            page, "SELECT * FROM reward_rules ORDER BY id DESC"
        )
        lines = []
        for item in items:
            window = ""
            if item.get("starts_at") or item.get("ends_at"):
                window = (
                    f" | از {escape(item.get('starts_at') or '—')}"
                    f" تا {escape(item.get('ends_at') or '—')}"
                )
            lines.append(
                f"<code>{item['id']}</code> | {escape(item['event_type'])} | "
                f"{item['amount']} | محصول {item.get('product_id') or 'همه'} | "
                f"{'فعال' if item['is_active'] else 'غیرفعال'}{window}"
            )
        self._send_page(
            self._chat_id(message, user), title="قواعد پاداش", rows=lines,
            total=total, page=page, pages=pages, command_prefix="/rewards",
            empty_text="قاعده‌ای ثبت نشده است.",
        )

    def _reward_add(self, rest: str, message: dict[str, Any], user: dict[str, Any], _admin: dict[str, Any]) -> None:
        parts = self._command_parts(rest, 3)
        event = parts[0].lower()
        if event not in {"start", "first_purchase", "product_purchase", "combined"}:
            raise AdminInputError("نوع رویداد پاداش معتبر نیست.")
        expected_lengths = {4, 6} if event == "combined" else {3, 5}
        if len(parts) not in expected_lengths:
            example = (
                "/reward_add combined | AMOUNT | PRODUCT_ID|0 | CONDITIONS_JSON "
                "[| START_DATE|0 | END_DATE|0]"
                if event == "combined"
                else "/reward_add EVENT | AMOUNT | PRODUCT_ID|0 "
                "[| START_DATE|0 | END_DATE|0]"
            )
            raise AdminInputError(f"نمونه: {example}")
        try:
            product_id = int(normalize_digits(parts[2]))
        except ValueError as exc:
            raise AdminInputError("شناسه محصول باید عدد صحیح صفر یا مثبت باشد.") from exc
        if product_id < 0:
            raise AdminInputError("شناسه محصول باید عدد صحیح صفر یا مثبت باشد.")
        if product_id and self.db.get_product(product_id) is None:
            raise AdminInputError("محصول پیدا نشد.")
        if event == "start" and product_id:
            raise AdminInputError("پاداش start به محصول وابسته نیست؛ PRODUCT_ID را 0 بگذارید.")
        conditions: Mapping[str, Any] | None = None
        date_index = 3
        if event == "combined":
            try:
                decoded = json.loads(parts[3])
            except json.JSONDecodeError as exc:
                raise AdminInputError("CONDITIONS_JSON یک JSON معتبر نیست.") from exc
            if not isinstance(decoded, dict):
                raise AdminInputError("CONDITIONS_JSON باید یک شیء JSON باشد.")
            condition_products = decoded.get("product_ids")
            if (
                product_id
                and isinstance(condition_products, list)
                and product_id not in condition_products
            ):
                raise AdminInputError(
                    "PRODUCT_ID باید در فهرست product_ids شرط‌های combined وجود داشته باشد."
                )
            conditions = decoded
            date_index = 4
        starts_at: str | None = None
        ends_at: str | None = None
        if len(parts) == date_index + 2:
            timezone_name = getattr(self.settings, "timezone", "UTC")
            if parts[date_index] != "0":
                starts_at, _ = _date_range(
                    parts[date_index], parts[date_index], timezone_name
                )
            if parts[date_index + 1] != "0":
                _, ends_at = _date_range(
                    parts[date_index + 1], parts[date_index + 1], timezone_name
                )
            if starts_at is not None and ends_at is not None and ends_at <= starts_at:
                raise AdminInputError("تاریخ پایان پاداش نباید قبل از تاریخ شروع باشد.")
        try:
            item = self.db.create_reward_rule(
                self._admin_idempotency_key(message, "reward-create")
                or f"{event}:{product_id or 'all'}:{secrets.token_hex(4)}",
                event_type=event,
                amount=parse_amount(parts[1]),
                product_id=product_id or None,
                conditions=conditions,
                starts_at=starts_at,
                ends_at=ends_at,
                active=True,
            )
        except ValidationError as exc:
            raise AdminInputError(str(exc)) from exc
        self._send(self._chat_id(message, user), f"قاعده پاداش ثبت شد. شناسه: <code>{item['id']}</code>")

    def _reward_toggle(self, rest: str, message: dict[str, Any], user: dict[str, Any], _admin: dict[str, Any]) -> None:
        rule_id = _as_int(rest, "شناسه قاعده")
        rules = self.db.list_reward_rules(active_only=False)
        current = next(
            (item for item in rules if int(item["id"]) == int(rule_id)), None
        )
        if current is None:
            raise AdminInputError("قاعده پاداش پیدا نشد.")
        active = self._admin_toggle_target(
            message,
            f"reward:{rule_id}:active",
            bool(current["is_active"]),
        )
        method = self._public("set_reward_rule_active")
        if method is not None:
            item = method(rule_id, active)
            active = bool(item["is_active"])
        else:
            self._execute(
                "UPDATE reward_rules SET is_active = ?, updated_at = ? WHERE id = ?",
                (int(active), _utc_timestamp(), rule_id),
            )
        self._send(self._chat_id(message, user), f"قاعده پاداش {'فعال' if active else 'غیرفعال'} شد.")


__all__ = [
    "AdminController",
    "AdminInputError",
    "AdminIntegrationError",
    "DOCUMENTED_COMMANDS",
    "SUPPORT_COMMANDS",
]
