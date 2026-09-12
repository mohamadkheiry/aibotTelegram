"""Durable button-first admin conversations, without a second business layer.

The UI state uses a nonce, revision and last input identity. A confirmation is
persisted as executing before the existing journaled handler runs. Transient DB
errors keep that exact operation replayable; a completed form cannot run again.
"""

from __future__ import annotations

import json
import re
import secrets
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .admin_forms import ACTIONS, GROUP_PARENTS, GROUPS, MAIN_GROUPS, Action, Field, arguments, form_fields
from .db import ConflictError, DatabaseError
from .keyboards import callback_button, contains_emoji, inline_keyboard
from .telegram import TelegramError
from .utils import escape, money, normalize_digits, normalize_username
from . import order_information


class ButtonInputError(ValueError):
    """Invalid or expired UI input; never a retryable domain mutation."""


class ClosedFormError(ButtonInputError):
    """A previously displayed form has been closed without a replacement form."""


# Every query is a read-only, static projection. Credential payloads, phone
# numbers and payment raw evidence are intentionally absent from selectors.
SELECTORS = {
    "category": "SELECT CAST(id AS TEXT) value, name || ' · ' || id label, name || ' ' || id search FROM categories",
    "product": "SELECT CAST(id AS TEXT) value, name || ' · ' || id || CASE WHEN is_active=0 THEN ' · حذف‌شده' ELSE '' END label, name || ' ' || id search FROM products",
    "ready_product": "SELECT CAST(id AS TEXT) value, name || ' · ' || id label, name || ' ' || id search FROM products WHERE is_active=1 AND product_type='ready'",
    "user": "SELECT CAST(chat_id AS TEXT) value, COALESCE(customer_name, first_name, '') || ' @' || COALESCE(username, '') || ' · ' || chat_id label, COALESCE(username, '') || ' ' || COALESCE(customer_name, first_name, '') || ' ' || chat_id search FROM users WHERE chat_id IS NOT NULL",
    "admin": "SELECT CAST(chat_id AS TEXT) value, '@' || username || ' · ' || role || ' · ' || chat_id label, username || ' ' || chat_id search FROM admins WHERE chat_id IS NOT NULL",
    "order": "SELECT o.order_number value, o.order_number || ' · ' || o.product_name_snapshot || ' · ' || o.status label, o.order_number || ' ' || o.product_name_snapshot || ' ' || COALESCE(u.username,'') || ' ' || COALESCE(u.chat_id,'') search FROM orders o JOIN users u ON u.id=o.user_id",
    "manual_order": "SELECT order_number value, order_number || ' · ' || product_name_snapshot || ' · ' || status label, order_number || ' ' || product_name_snapshot search FROM orders WHERE product_type_snapshot='manual' AND status='processing' AND customer_info_json IS NOT NULL AND CASE WHEN json_valid(customer_info_json) THEN COALESCE(json_extract(customer_info_json,'$.collecting'),0)=0 ELSE 0 END",
    "payment": "SELECT payment_number value, payment_number || ' · ' || payable_amount || ' تومان · ' || status label, payment_number || ' ' || payable_amount search FROM payments",
    "receipt": "SELECT payment_number value, payment_number || ' · ' || payable_amount || ' تومان' label, payment_number || ' ' || payable_amount search FROM payments WHERE method='card' AND status='verifying' AND receipt_file_id IS NOT NULL",
    "join": "SELECT CAST(id AS TEXT) value, title || ' · ' || telegram_chat_id label, title || ' ' || telegram_chat_id search FROM force_join_channels",
    "inventory": "SELECT CAST(i.id AS TEXT) value, p.name || ' · آیتم ' || i.id || ' · ' || i.status label, p.name || ' ' || i.id search FROM inventory_items i JOIN products p ON p.id=i.product_id",
    "discount": "SELECT code value, code || ' · ' || value || ' · ' || discount_type label, code search FROM discounts",
    "ticket": "SELECT t.ticket_number value, t.ticket_number || ' · ' || CASE WHEN u.username IS NOT NULL THEN '@' || u.username ELSE COALESCE(u.customer_name,u.first_name,'بدون نام') END || ' · ' || COALESCE(u.chat_id,'') || ' · ' || t.subject || ' · ' || t.status label, t.ticket_number || ' ' || t.subject || ' ' || COALESCE(u.username,'') || ' ' || COALESCE(u.customer_name,u.first_name,'') || ' ' || COALESCE(u.chat_id,'') search FROM tickets t JOIN users u ON u.id=t.user_id",
    "ticket_attachment": "SELECT CAST(m.id AS TEXT) value, 'پیوست ' || m.id || ' · ' || m.attachment_kind || ' · ' || m.created_at label, CAST(m.id AS TEXT) search FROM ticket_messages m JOIN tickets t ON t.id=m.ticket_id WHERE m.attachment_file_id IS NOT NULL AND t.ticket_number=?",
    "faq_category": "SELECT CAST(id AS TEXT) value, name || ' · ' || id label, name || ' ' || id search FROM faq_categories",
    "faq": "SELECT CAST(id AS TEXT) value, question || ' · ' || id label, question || ' ' || id search FROM faqs",
    "reward": "SELECT CAST(id AS TEXT) value, 'قانون ' || id || ' · ' || event_type || ' · ' || CASE WHEN amount_mode='percent' THEN amount || '%' ELSE amount || ' تومان' END label, id || ' ' || event_type search FROM reward_rules",
    "card_review": "SELECT CAST(e.id AS TEXT) value, 'رخداد ' || e.id || ' · ' || e.amount || ' تومان · ' || e.reference label, e.id || ' ' || e.reference search FROM card_payment_events e WHERE e.status='review' AND NOT EXISTS (SELECT 1 FROM card_payment_event_resolutions r WHERE r.event_id=e.id)",
    "crypto_review": "SELECT CAST(e.id AS TEXT) value, 'رخداد ' || e.id || ' · ' || p.payment_number || ' · ' || e.provider_status label, e.id || ' ' || p.payment_number search FROM provider_payment_events e JOIN payments p ON p.id=e.payment_id WHERE e.disposition='review' AND NOT EXISTS (SELECT 1 FROM provider_payment_event_resolutions r WHERE r.event_id=e.id)",
}

RESULT_ACTIONS = {
    "user": ("user_orders", "user_transactions", "user_referrals", "user_rewards",
             "message", "wallet_adjust", "block", "unblock"),
    "order": ("order_attachment", "complete", "request_info", "order_status"),
    "ticket": ("ticket_reply", "ticket_attachment", "ticket_status", "ticket_close"),
    "payment_detail": ("approve_payment", "reject_payment"),
}


def label_text(value: Any, maximum: int = 60) -> str:
    """Untrusted catalog names must not smuggle emoji into button labels."""
    text = " ".join(str(value).split())
    text = " ".join("".join(char for char in text if not contains_emoji(char)).split())
    return text[:maximum] or "انتخاب"


class AdminButtonUI:
    def __init__(self, controller: Any) -> None:
        self.controller = controller
        self.db = controller.db
        from .admin_catalog import AdminCatalog
        from .admin_joins import AdminJoins
        from .admin_layouts import AdminLayouts

        self.catalog = AdminCatalog(self)
        self.joins = AdminJoins(self)
        self.layouts = AdminLayouts(self)

    @staticmethod
    def allowed(action: Action, role: str) -> bool:
        from .admin import SUPPORT_COMMANDS

        if role not in {"owner", "admin", "support"}:
            return False
        if action.command in {"/backup", "/card_resolve", "/crypto_resolve"}:
            return role == "owner"
        return role != "support" or action.command in SUPPORT_COMMANDS

    def authorise(self, user: dict, admin: dict, event: dict | None = None) -> dict:
        chat_id = int(user["chat_id"])
        current = self.controller._query_one(
            "SELECT * FROM admins WHERE id=? AND chat_id=? AND is_active=1 AND identity_verified_at IS NOT NULL",
            (admin["id"], chat_id),
        )
        if not current:
            raise ButtonInputError("دسترسی مدیریت شما فعال نیست.")
        if event is not None:
            message = event.get("message", event)
            chat = message.get("chat", {})
            if chat.get("type") != "private" or chat.get("id") != chat_id:
                raise ButtonInputError("پنل فقط در گفت‌وگوی خصوصی خود مدیر قابل استفاده است.")
            sender = event.get("from") or message.get("from")
            if sender and sender.get("id") != user["telegram_user_id"]:
                raise ButtonInputError("هویت فرستنده معتبر نیست.")
        return current

    def _send(self, user: dict, text: str, rows: list[list[dict]] | None = None) -> Any:
        return self.controller._send(int(user["chat_id"]), text, inline_keyboard(rows) if rows else None)

    def _retire_prompt(self, user: dict, message_id: Any) -> None:
        """Remove consumed UI buttons, never the message or stored outbox data.

        Cleanup is best effort: old clients/messages may not be editable. Nonce,
        revision and domain idempotency remain authoritative even in that case.
        """
        if not isinstance(message_id, int) or isinstance(message_id, bool) or message_id <= 0:
            return
        try:
            self.controller.telegram.edit_message_reply_markup(
                int(user["chat_id"]), message_id, reply_markup={"inline_keyboard": []})
        except TelegramError:
            self.controller.log.warning("Could not retire an old admin keyboard; revision guard remains active")

    def _publish(self, state: dict, user: dict, text: str, rows: list[list[dict]]) -> None:
        previous = state.get("prompt_message_id")
        result = self._send(user, text, rows)
        if isinstance(result, dict) and isinstance(result.get("message_id"), int):
            state["prompt_message_id"] = result["message_id"]
            self._save(user, state)
            if previous != result["message_id"]:
                self._retire_prompt(user, previous)

    def _recover_prompt(self, state: dict, event: dict, user: dict, admin: dict) -> None:
        """Reject a stale action and show the current UI; never run its handler."""
        clicked = (event.get("message") or {}).get("message_id")
        self.render(state, user, admin)
        if clicked != state.get("prompt_message_id"):
            self._retire_prompt(user, clicked)

    @staticmethod
    def _button(label: str, suffix: str, **kwargs: Any) -> dict:
        return callback_button(label_text(label), "adm:ui:" + suffix, **kwargs)

    def navigation(self, group: str | None = None) -> list[list[dict]]:
        rows = []
        if group:
            rows.append([self._button("بازگشت به " + GROUPS[group], "g:" + group)])
            if group in GROUP_PARENTS:
                parent = GROUP_PARENTS[group]
                rows.append([self._button("بازگشت به " + GROUPS[parent], "g:" + parent)])
        rows.append([self._button("پنل مدیریت", "home"), callback_button("منوی اصلی", "menu")])
        return rows

    def bot_status(self) -> tuple[str, list[list[dict]]]:
        """One public-access switch, as specified; no duplicate maintenance action.

        Never invert the live flag on click: a cached button or replay must
        retain the operation the administrator actually chose and confirmed.
        """
        enabled = bool(self.db.get_setting("bot_enabled", True))
        status = "ربات: فعال\nدسترسی کاربران: باز" if enabled else "ربات: غیرفعال\nدسترسی کاربران: بسته (نمایش پیام بروزرسانی)"
        label = "غیرفعال‌کردن ربات" if enabled else "فعال‌کردن ربات"
        target = "a:bot_off" if enabled else "a:bot_on"
        rows = [[self._button(label, target, style="danger" if enabled else "success")]]
        return status, rows

    def payment_context(self, state: dict) -> str:
        """Read live configuration; never confuse the chosen target with it."""
        method = state.get("values", {}).get("method")
        if method is None:
            return ""
        names = {value: label for label, value in ACTIONS["payment"].fields[0].options}
        if not isinstance(method, str) or method not in names:
            raise ButtonInputError("روش پرداخت انتخاب‌شده معتبر نیست؛ از پنل دوباره انتخاب کنید.")
        enabled = bool(self.db.get_setting(f"payment_{method}_enabled", method != "crypto"))
        lines = ["روش انتخاب‌شده: " + names[method], "وضعیت فعلی: " + ("فعال" if enabled else "غیرفعال")]
        # These are configuration prerequisites, not a network/provider health
        # check. Do not disclose account numbers, owners or API key values.
        if method == "card" and not (
            str(self.db.get_setting("card_number", "") or "").strip()
            and str(self.db.get_setting("card_owner", "") or "").strip()
        ):
            lines.append("تنظیمات کارت کامل نیست؛ تا ثبت شماره کارت و صاحب حساب، این روش به مشتری نمایش داده نمی‌شود.")
        if method == "crypto" and not str(getattr(self.controller.settings, "plisio_api_key", "") or "").strip():
            lines.append("تنظیمات پرداخت ارزی کامل نیست؛ تا تنظیم کلید سرویس در محیط اجرا، این روش به مشتری نمایش داده نمی‌شود.")
        return "\n".join(lines)

    def home(self, user: dict, admin: dict, group: str | None = None) -> None:
        admin = self.authorise(user, admin)
        if group is not None and group not in GROUPS:
            raise ButtonInputError("بخش مدیریت شناخته نشد.")
        if group == "catalog":
            self.catalog.authorise(user, admin)
            self.catalog.category(0, user, admin)
            return
        actions = [a for a in ACTIONS.values() if self.allowed(a, admin["role"])]
        if group:
            actions = [a for a in actions if a.group == group]
            if not actions and not (group == "broadcast" and self.allowed(ACTIONS["message"], admin["role"])):
                raise ButtonInputError("دسترسی به این بخش ندارید.")
            rows = []
            for action in actions:
                if action.key == "bot_on":
                    rows.extend(self.bot_status()[1])
                elif action.key not in {"bot_off", "join_add", "join_toggle", "join_delete"}:
                    rows.append([self._button("جوین اجباری", "j:list:1") if action.key == "joins" else
                                 self._button(action.label, "a:" + action.key)])
            for child, parent in GROUP_PARENTS.items():
                if parent == group and any(self.allowed(a, admin["role"]) and a.group == child for a in ACTIONS.values()):
                    rows.append([self._button(GROUPS[child], "g:" + child)])
            if group == "broadcast" and self.allowed(ACTIONS["message"], admin["role"]):
                rows.insert(0, [self._button("ارسال پیام تکی", "a:message")])
            if group == "users":
                rows.insert(0, [self._button("جست‌وجوی کاربر", "a:user")])
            rows = self._compact_related_rows(rows)
            title = GROUPS[group]
            if group == "settings" and admin["role"] in {"owner", "admin"}:
                rows.insert(0, [self._button("چیدمان دکمه‌های کاربران", "l:home", style="primary")])
                self.controller._send_settings_panel(int(user["chat_id"]), button_mode=True)
        else:
            groups = [g for g in MAIN_GROUPS if any(a.group == g for a in actions)
                      or g == "broadcast" and self.allowed(ACTIONS["message"], admin["role"])]
            rows = [[self._button(GROUPS[g], "g:" + g)] for g in groups]
            title = "پنل مدیریت"
        previous = self.db.get_user_state(int(user["id"]))
        self.db.clear_user_state(int(user["id"]))
        role = {"owner": "مالک", "admin": "مدیر", "support": "پشتیبان"}[admin["role"]]
        self._send(user, f"<b>{title}</b>\nنقش شما: {role}\nگزینه را انتخاب کنید؛ نیازی به تایپ فرمان نیست.",
                   rows + self.navigation(GROUP_PARENTS.get(group)) if group else rows + [[callback_button("منوی اصلی", "menu")]])
        if previous and previous["state"] in {"admin:ui", "admin:catalog", "admin:joins", "admin:layouts"}:
            self._retire_prompt(user, previous["data"].get("prompt_message_id"))

    @staticmethod
    def _compact_related_rows(rows: list[list[dict]]) -> list[list[dict]]:
        pairs = {frozenset(pair) for pair in (("block", "unblock"), ("admin_enable", "admin_disable"),
                 ("inventory_disable", "inventory_enable"), ("user_referrals", "user_rewards"))}

        def action(button: dict) -> tuple[str, str]:
            parts = button.get("callback_data", "").removeprefix("adm:ui:").split(":", 2)
            return (parts[1], parts[2] if len(parts) == 3 else "") if len(parts) > 1 and parts[0] in {"a", "open"} else ("", "")

        compact: list[list[dict]] = []
        for row in rows:
            if compact and len(row) == len(compact[-1]) == 1:
                previous, current = action(compact[-1][0]), action(row[0])
                if previous[1] == current[1] and frozenset((previous[0], current[0])) in pairs:
                    compact[-1].extend(row)
                    continue
            compact.append(list(row))
        return compact

    @staticmethod
    def _input_id(event: dict) -> str:
        update = event.get("_admin_update_id")
        if update is not None:
            return f"update:{update}"
        if event.get("id"):
            return "callback:" + str(event["id"])
        return "message:" + str(event.get("message_id"))

    def _save(self, user: dict, state: dict) -> None:
        self.db.set_user_state(int(user["id"]), "admin:ui", state)

    def _state(self, user: dict, admin: dict) -> dict:
        state = self.db.get_user_state(int(user["id"]))
        data = state.get("data", {}) if state and state["state"] == "admin:ui" else {}
        if not data:
            raise ClosedFormError("این فرم بسته شده است؛ از پنل مدیریت ادامه دهید.")
        if data.get("actor") != admin["id"] or data.get("chat") != user["chat_id"]:
            raise ButtonInputError("این فرم دیگر فعال نیست؛ از پنل مدیریت دوباره انتخاب کنید.")
        action = ACTIONS.get(data.get("action"))
        if action is None or not self.allowed(action, admin["role"]):
            raise ButtonInputError("دسترسی اجرای این عملیات را ندارید.")
        return data

    def begin(self, key: str, event: dict, user: dict, admin: dict, *, selected: str | None = None,
              preset: dict[str, str] | None = None, return_to: dict | None = None,
              product_scope: int | None = None) -> None:
        action = ACTIONS.get(key)
        if action is None or not self.allowed(action, admin["role"]):
            raise ButtonInputError("دسترسی اجرای این عملیات را ندارید.")
        old = self.db.get_user_state(int(user["id"]))
        prior = old.get("data", {}) if old and old["state"] == "admin:ui" else {}
        if prior.get("started_by") == self._input_id(event):
            if prior.get("status") == "executing":
                self.execute(prior, event, user, admin)
            elif prior.get("status") == "editing":
                self.advance(prior, event, user, admin)
            else:
                self.render(prior, user, admin)
            return
        state = {"action": key, "actor": admin["id"], "chat": user["chat_id"],
                 "token": secrets.token_hex(6), "revision": 0, "values": {}, "labels": {},
                 "status": "editing", "last_input": None, "started_by": self._input_id(event),
                 "step": 0, "page": 1, "search": "", "selected": []}
        if prior.get("prompt_message_id"):
            state["prompt_message_id"] = prior["prompt_message_id"]
        elif old and old["state"] in {"admin:catalog", "admin:joins", "admin:layouts"}:
            state["prompt_message_id"] = old["data"].get("prompt_message_id")
        if return_to:
            state["return_to"] = return_to
        if product_scope is not None:
            if key != "reward_add":
                raise ButtonInputError("زمینه محصول فقط برای فرم پاداش مجاز است.")
            product = self.catalog._product(product_scope)
            state["values"].update(product=str(product_scope), _product_scope=True)
            state["labels"]["product"] = product["name"]
        if selected is not None:
            fields = form_fields(action, {})
            if not fields or not fields[0].kind.startswith("entity:"):
                raise ButtonInputError("انتخاب اولیه مجاز نیست.")
            item = self.entity_value(fields[0], selected, state)
            if item is None:
                raise ButtonInputError("مورد انتخاب‌شده دیگر در این فهرست نیست.")
            state["values"][fields[0].key] = item[0]
            state["labels"][fields[0].key] = item[1]
            state["step"] = 1
        remaining = dict(preset or {})
        while remaining:
            field = self.current_field(state)
            if field.key not in remaining:
                raise ButtonInputError("مقادیر اولیه فرم باید به ترتیب مرحله‌ها باشند.")
            value = remaining.pop(field.key)
            if field.kind == "choice":
                candidates = dict(self.options(field, state, admin))
                if value not in candidates:
                    raise ButtonInputError("گزینهٔ اولیه فرم معتبر نیست.")
                label = candidates[value]
            else:
                raise ButtonInputError("پیش‌انتخاب این نوع فیلد مجاز نیست.")
            self.set_value(state, field, value, label)
        if return_to:
            # A product-scoped form cannot change its target while navigating
            # back. Only its editable values may be corrected before confirm.
            state["minimum_step"] = state["step"]
        self._save(user, state)
        self.advance(state, event, user, admin)

    def callback(self, data: str, event: dict, user: dict, admin: dict) -> bool:
        admin = self.authorise(user, admin, event)
        # Answer promptly before read queries / document creation. Feedback after
        # validation is a normal message, not a second answer to the same query.
        try:
            self.controller._answer(str(event.get("id") or ""))
        except TelegramError:
            # A lost/expired spinner acknowledgement is not the operation.
            # Authorization, form revision and domain idempotency still apply.
            self.controller.log.warning("Could not acknowledge admin button; continuing validated operation")
        suffix = data.removeprefix("adm:ui:")
        if suffix.startswith("l:"):
            return self.layouts.callback(suffix[2:], event, user, admin)
        if suffix.startswith("c:"):
            return self.catalog.callback(suffix[2:], event, user, admin)
        if suffix.startswith("j:"):
            return self.joins.callback(suffix[2:], event, user, admin)
        if suffix == "home":
            self.home(user, admin)
            return True
        if suffix.startswith("g:"):
            self.home(user, admin, suffix[2:])
            return True
        if suffix.startswith(("faqcat:", "faqback:", "faqitem:")):
            kind, raw = suffix.split(":", 1)
            if not raw.isascii() or not raw.isdigit():
                raise ButtonInputError("شناسه پرسش یا دسته معتبر نیست.")
            if not self.allowed(ACTIONS["faqs"], admin["role"]):
                raise ButtonInputError("دسترسی مدیریت پرسش‌ها ندارید.")
            if kind == "faqitem":
                self.faq_detail(int(raw), user, admin)
            elif self.db.get_faq_category(int(raw)):
                self.begin("faqs", event, user, admin, selected=raw)
            else:
                self.begin("faq_categories", event, user, admin)
            return True
        if suffix.startswith("faqdo:"):
            parts = suffix.split(":")
            allowed = {"faq_add", "faq_set", "faq_toggle", "faq_delete",
                       "faq_category_set", "faq_category_toggle", "faq_category_delete"}
            if len(parts) != 3 or parts[1] not in allowed or not parts[2].isascii() or not parts[2].isdigit():
                raise ButtonInputError("دکمه پرسش معتبر نیست.")
            key, identifier = parts[1], int(parts[2])
            if not self.allowed(ACTIONS[key], admin["role"]):
                raise ButtonInputError("دسترسی مدیریت پرسش‌ها ندارید.")
            item = self.db.get_faq(identifier) if key in {"faq_set", "faq_toggle", "faq_delete"} else None
            if key in {"faq_set", "faq_toggle", "faq_delete"} and item is None:
                raise ButtonInputError("این پرسش حذف شده است.")
            category_id = item["category_id"] if item else identifier
            self.begin(key, event, user, admin, selected=str(identifier),
                       return_to={"scope": "faq", "category_id": category_id or 0})
            return True
        if suffix.startswith("discount:"):
            raw = suffix.split(":", 1)[1]
            if not raw.isdigit():
                raise ButtonInputError("شناسه تخفیف معتبر نیست.")
            self.discount_detail(int(raw), user, admin)
            return True
        if suffix.startswith("discountdo:"):
            parts = suffix.split(":")
            if len(parts) != 3 or not parts[1].isdigit() or parts[2] not in {"toggle", "delete"}:
                raise ButtonInputError("دکمه تخفیف معتبر نیست.")
            item = self.controller._query_one("SELECT code FROM discounts WHERE id=?", (int(parts[1]),))
            if item is None:
                raise ButtonInputError("این تخفیف حذف شده است.")
            self.begin("discount_" + parts[2], event, user, admin, selected=item["code"])
            return True
        if suffix.startswith("a:"):
            self.begin(suffix[2:], event, user, admin)
            return True
        if suffix.startswith("open:"):
            parts = suffix.split(":", 2)
            if len(parts) != 3:
                raise ButtonInputError("دکمه نامعتبر است.")
            self.begin(parts[1], event, user, admin, selected=parts[2])
            return True
        parts = suffix.split(":")
        if len(parts) != 5 or parts[0] != "f":
            raise ButtonInputError("دکمه نامعتبر است.")
        _, token, revision, operation, value = parts
        try:
            state = self._state(user, admin)
        except ClosedFormError as exc:
            active = self.db.get_user_state(int(user["id"]))
            if active and active["state"] == "admin:layouts":
                self.layouts.authorise(user, admin, event)
                self.layouts.render(self.layouts.state(user, admin), user)
                return True
            if active and active["state"] == "admin:joins":
                self.joins.authorise(user, admin, event)
                self.joins.restore(self.joins.context(user), user, admin)
                return True
            if active and active["state"] == "admin:catalog":
                self.catalog.authorise(user, admin, event)
                self.catalog.open_context(self.catalog.context(user), user, admin)
                return True
            self._send(user, str(exc), self.navigation())
            self._retire_prompt(user, (event.get("message") or {}).get("message_id"))
            return True
        if token != state["token"]:
            self._recover_prompt(state, event, user, admin)
            return True
        # Older releases did not retain the prompt ID. Learn it only after
        # checking the form token, so rollout also cleans up consumed keyboards.
        if not state.get("prompt_message_id"):
            state["prompt_message_id"] = (event.get("message") or {}).get("message_id")
        identity = self._input_id(event)
        if state.get("status") == "executing":
            if state.get("execution_input") == identity:
                self.execute(state, event, user, admin)
                return True
            raise ButtonInputError("این عملیات قبلاً تأیید شده است؛ نتیجه را بررسی کنید.")
        if state.get("last_input") == identity:
            if state["status"] == "editing":
                self.advance(state, event, user, admin)
            else:
                self.render(state, user, admin)
            return True
        if revision != str(state["revision"]):
            self._recover_prompt(state, event, user, admin)
            return True
        if operation == "list":
            if state["status"] != "done" or not value.isdigit() or int(value) < 1 or "list_pages" not in state:
                raise ButtonInputError("صفحه نامعتبر است.")
            if int(value) > state["list_pages"]:
                raise ButtonInputError("صفحه در این فهرست نیست.")
            state["result_page"] = int(value)
            state["revision"] += 1
            state["last_input"] = identity
            self.execute(state, event, user, admin)
            return True
        if state["status"] == "done":
            self._recover_prompt(state, event, user, admin)
            return True
        if operation == "confirm":
            if state["status"] != "confirm":
                raise ButtonInputError("فرم هنوز کامل نشده است.")
            self.execute(state, event, user, admin)
            return True
        if operation == "back":
            minimum = state.get("minimum_step", 0)
            if state["step"] <= minimum:
                raise ButtonInputError("برای انتخاب مورد دیگر، به بخش قبل برگردید.")
            state["step"] = max(minimum, state["step"] - 1)
            fields = form_fields(ACTIONS[state["action"]], state["values"])
            keep = {f.key for f in fields[:state["step"]]}
            state["values"] = {k: v for k, v in state["values"].items() if k in keep}
            state["labels"] = {k: v for k, v in state["labels"].items() if k in keep}
            state.update(status="editing", page=1, search="", selected=[])
        elif operation in {"pick", "default", "next", "multi"}:
            field = self.current_field(state)
            if operation == "next":
                if not field.kind.startswith(("entity:", "multi:")) or not value.isdigit():
                    raise ButtonInputError("صفحه نامعتبر است.")
                page = int(value)
                if not 1 <= page <= state.get("option_pages", 1):
                    raise ButtonInputError("صفحه نامعتبر است.")
                state["page"] = page
            elif operation == "default":
                if field.default is None:
                    raise ButtonInputError("این فیلد الزامی است.")
                self.set_value(state, field, field.default, "بدون محدودیت / پیش‌فرض")
            elif operation == "multi":
                if not field.kind.startswith("multi:"):
                    raise ButtonInputError("انتخاب چندتایی مجاز نیست.")
                selected = state.get("selected", [])
                self.set_value(state, field, json.dumps(selected), "، ".join(map(str, selected)) or "همه محصولات")
            else:
                if not value.isdigit() or int(value) >= len(state.get("options", [])):
                    raise ButtonInputError("گزینه نامعتبر است.")
                chosen, label = state["options"][int(value)]
                if field.kind.startswith("multi:"):
                    selected = state.setdefault("selected", [])
                    number = int(chosen)
                    if number in selected:
                        selected.remove(number)
                    else:
                        selected.append(number)
                else:
                    self.set_value(state, field, chosen, label)
        else:
            raise ButtonInputError("عملیات فرم شناخته نشد.")
        state["revision"] += 1
        state["last_input"] = identity
        self._save(user, state)
        self.advance(state, event, user, admin)
        return True

    def message(self, event: dict, user: dict, admin: dict) -> bool:
        admin = self.authorise(user, admin, event)
        state = self._state(user, admin)
        identity = self._input_id(event)
        if state.get("status") == "executing" and state.get("execution_input") == identity:
            self.execute(state, event, user, admin)
            return True
        if state.get("last_input") == identity:
            if state["status"] == "editing":
                self.advance(state, event, user, admin)
            else:
                self.render(state, user, admin)
            return True
        if state["status"] != "editing":
            if state["status"] == "done" and state["action"] == "faqs" and isinstance(event.get("text"), str):
                state.update(result_search=normalize_digits(event["text"].strip())[:120], result_page=1,
                             last_input=identity)
                self.execute(state, event, user, admin)
                return True
            self.render(state, user, admin)
            return True
        field = self.current_field(state)
        text = event.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ButtonInputError("این مرحله متن می‌خواهد. از دکمه لغو هم می‌توانید استفاده کنید.")
        if field.kind.startswith(("entity:", "multi:")):
            # Search is data, never an identifier directly submitted to a mutation.
            state["search"] = normalize_digits(text.strip()).lstrip("@")[:120]
            state["page"] = 1
        elif field.kind == "choice":
            raise ButtonInputError("در این مرحله یکی از دکمه‌ها را انتخاب کنید.")
        else:
            value = self.validate(field, text)
            rich_fields = {"icon", "description", "short_description", "long_description", "account_type",
                           "activation", "warranty", "activation_instructions", "usage_terms", "rules",
                           "info_request_text", "completion_text", "delivery_instructions", "features"}
            if (field.key in {"body", "delivery", "answer", "description", "icon"}
                    or state["action"] in {"product_set", "category_set", "faq_set"}
                    and field.key == "value" and state["values"].get("field") in rich_fields | {"answer"}):
                from .utils import telegram_input_text
                value = telegram_input_text(text, event.get("entities"))
            self.set_value(state, field, value, value)
        state["revision"] += 1
        state["last_input"] = identity
        self._save(user, state)
        self.advance(state, event, user, admin)
        return True

    @staticmethod
    def validate(field: Field, text: str) -> str:
        value = normalize_digits(text.strip())
        if field.kind in {"positive", "nonnegative", "signed", "integer"}:
            value = value.replace(",", "").replace("٬", "")
            if not re.fullmatch(r"-?\d+", value):
                raise ButtonInputError("یک عدد صحیح وارد کنید.")
            number = int(value)
            if (field.kind == "positive" and number <= 0
                    or field.kind == "nonnegative" and number < 0
                    or field.kind == "signed" and number == 0):
                raise ButtonInputError("مقدار عددی برای این فیلد معتبر نیست.")
            return str(number)
        if field.kind == "card":
            value = re.sub(r"[\s-]", "", value)
            if not re.fullmatch(r"\d{16,19}", value):
                raise ButtonInputError("شماره کارت باید ۱۶ تا ۱۹ رقم باشد.")
            return value
        if field.kind == "date":
            try:
                return datetime.strptime(value, "%Y-%m-%d").date().isoformat()
            except ValueError as exc:
                raise ButtonInputError("تاریخ معتبر میلادی با قالب YYYY-MM-DD وارد کنید.") from exc
        if field.kind == "username":
            value = normalize_username(value)
            if not re.fullmatch(r"[a-zA-Z0-9_]{5,32}", value):
                raise ButtonInputError("یوزرنیم معتبر تلگرام وارد کنید.")
            return value
        if field.kind == "word" and re.search(r"\s|\|", value):
            raise ButtonInputError("کد را بدون فاصله یا جداکننده وارد کنید.")
        if field.kind == "reminders":
            value = value.replace("،", ",")
            if value.lower() in {"off", "غیرفعال"}:
                return "off"
            if not re.fullmatch(r"\d+(?:\s*,\s*\d+)*", value):
                raise ButtonInputError("روزها باید عدد صحیح نامنفی باشند؛ نمونه: ۳،۱،۰")
            return value
        if field.kind == "duration":
            if value in {"بدون انقضا", "مادام العمر"}:
                return "بدون انقضا"
            if not re.fullmatch(r"[1-9][0-9]*\s*(?:روز|ماه|سال|days?|months?|years?)?", value.casefold()):
                raise ButtonInputError("مدت را با عدد مثبت و واحد بنویسید؛ نمونه: ۳ ماه یا ۳۰ روز.")
            return value
        # Do not alter digits, spacing, pipes or HTML of secret/free text.
        return text if field.kind == "secret" else text.strip()

    @staticmethod
    def current_field(state: dict) -> Field:
        fields = form_fields(ACTIONS[state["action"]], state["values"])
        if not 0 <= state["step"] < len(fields):
            raise ButtonInputError("فرم منتظر تأیید است.")
        return fields[state["step"]]

    @staticmethod
    def set_value(state: dict, field: Field, value: str, label: str) -> None:
        state["values"][field.key] = value
        state["labels"][field.key] = "اطلاعات محرمانه ثبت شد" if field.kind == "secret" else label
        state["step"] += 1
        state.update(page=1, search="", selected=[])

    def advance(self, state: dict, event: dict, user: dict, admin: dict) -> None:
        action = ACTIONS[state["action"]]
        if state["step"] >= len(form_fields(action, state["values"])):
            if not action.mutation:
                self.execute(state, event, user, admin)
                return
            state["status"] = "confirm"
            self._save(user, state)
        self.render(state, user, admin)

    def _selector(self, field: Field, state: dict) -> tuple[str, tuple]:
        kind = field.kind.split(":", 1)[1]
        params: tuple = (state["values"]["target"],) if kind == "ticket_attachment" else ()
        query = SELECTORS[kind]
        if kind == "ticket" and state.get("action") == "ticket_reply":
            query += " WHERE t.status!='closed'"
        return query, params

    def entity_value(self, field: Field, value: str, state: dict) -> tuple[str, str] | None:
        query, params = self._selector(field, state)
        row = self.controller._query_one(f"SELECT value, label FROM ({query}) WHERE value=?", (*params, value))
        return (str(row["value"]), str(row["label"])) if row else None

    def options(self, field: Field, state: dict, admin: dict) -> list[tuple[str, str]]:
        if state.get("action") == "complete" and field.key == "delivery":
            order = self.controller._require_order(state["values"]["target"])
            product = self.db.get_product(int(order["product_id"]))
            content = str((product or {}).get("completion_text") or "اشتراک شما با موفقیت فعال شد.")
            return [(content, "استفاده از متن تکمیل محصول")]
        if field.kind.startswith(("entity:", "multi:")):
            query, params = self._selector(field, state)
            search = state.get("search", "")
            if state.get("action") == "message" and field.kind == "entity:user" and not search:
                state.update(option_total=0, option_pages=1)
                return []
            if field.kind in {"entity:order", "entity:manual_order"}:
                search = normalize_digits(search).lstrip("@")
            query = f"SELECT * FROM ({query}) WHERE instr(lower(search), lower(?)) > 0 ORDER BY length(value) DESC, value DESC"
            rows, total, pages = self.controller._management_rows(int(state.get("page", 1)), query, (*params, search))
            state.update(option_total=total, option_pages=pages)
            return [(str(row["value"]), str(row["label"])) for row in rows]
        choices = [(value, label) for label, value in field.options]
        if state.get("action") == "reward_add" and field.key == "event" and state["values"].get("_product_scope"):
            choices = [(value, label) for value, label in choices if value != "start"]
        if state.get("action") == "payment" and field.key == "method":
            choices = [(value, label + " · " + ("فعال" if self.db.get_setting(
                f"payment_{value}_enabled", value != "crypto") else "غیرفعال")) for value, label in choices]
        if field.key == "role" and admin["role"] != "owner":
            choices = [(value, label) for value, label in choices if value != "owner"]
        if field.kind == "date":
            today = datetime.now(ZoneInfo(getattr(self.controller.settings, "timezone", "UTC"))).date()
            choices = [((today + timedelta(days=days)).isoformat(), label) for days, label in (
                (0, "امروز"), (-1, "دیروز"), (-7, "۷ روز قبل"), (-30, "۳۰ روز قبل"),
                (1, "فردا"), (7, "۷ روز بعد"), (30, "۳۰ روز بعد"))]
        return choices

    def _form_button(self, state: dict, label: str, operation: str, value: str = "0", **kwargs: Any) -> dict:
        return self._button(label, f"f:{state['token']}:{state['revision']}:{operation}:{value}", **kwargs)

    def render(self, state: dict, user: dict, admin: dict) -> None:
        action = ACTIONS[state["action"]]
        payment_context = self.payment_context(state) if action.key == "payment" else ""
        if state["status"] == "done":
            text = "این درخواست قبلاً انجام شده است؛ از گزینه‌های زیر ادامه دهید."
            if action.key in {"bot_on", "bot_off"}:
                text += "\n" + self.bot_status()[0]
            if payment_context:
                text += "\n" + payment_context
            self._publish(state, user, text,
                          self.result_rows(state, admin))
            return
        if state["status"] == "executing":
            self._send(user, "این عملیات قبلاً تأیید شده و در حال بازیابی نتیجه است.", self.navigation(action.group))
            return
        # Every published selection map has a new revision, including replay,
        # stale recovery and re-render after catalog changes. Reusing a revision
        # could silently map an old index to a newly inserted/reordered entity.
        state["revision"] += 1
        rows: list[list[dict]] = []
        if state["status"] == "confirm":
            lines = [f"<b>تأیید نهایی: {action.label}</b>"]
            if action.key in {"faq_toggle", "faq_category_toggle"}:
                identifier = int(state["values"]["target"])
                category = action.key == "faq_category_toggle"
                item = self.db.get_faq_category(identifier) if category else self.db.get_faq(identifier)
                if item is None:
                    raise ButtonInputError("پرسش یا دسته دیگر وجود ندارد.")
                effect_key = f"{'faq-category' if category else 'faq'}:{identifier}:active"
                if state.get("faq_target", {}).get("effect_key") != effect_key:
                    state["faq_target"] = {"effect_key": effect_key, "active": not bool(item["is_active"])}
                lines.append("انتخاب‌شده: " + escape(item["name" if category else "question"]))
                lines.append("وضعیت فعلی: " + ("فعال" if item["is_active"] else "غیرفعال"))
                lines.append("پس از تأیید: " + ("فعال" if state["faq_target"]["active"] else "غیرفعال"))
            if action.key == "reward_toggle":
                item = self.controller._query_one("SELECT * FROM reward_rules WHERE id=?", (state["values"]["target"],))
                if item is None:
                    raise ButtonInputError("این قانون پاداش دیگر وجود ندارد.")
                if state.get("reward_target", {}).get("id") != item["id"]:
                    state["reward_target"] = {"id": item["id"], "active": not bool(item["is_active"])}
                lines.append(f"قانون: {item['id']} | وضعیت فعلی: {'فعال' if item['is_active'] else 'غیرفعال'}")
                lines.append("پس از تأیید: " + ("فعال" if state["reward_target"]["active"] else "غیرفعال"))
            if action.key in {"discount_toggle", "discount_delete"}:
                item = self.controller._query_one("SELECT * FROM discounts WHERE code_key=?", (state["values"]["target"].casefold(),))
                if item is None:
                    raise ButtonInputError("این کد تخفیف دیگر وجود ندارد.")
                lines.append(f"کد انتخاب‌شده: {escape(item['code'])}\nوضعیت فعلی: {'فعال' if item['is_active'] else 'غیرفعال'}")
                if action.key == "discount_toggle":
                    if state.get("discount_target", {}).get("id") != item["id"]:
                        state["discount_target"] = {"id": item["id"], "active": not bool(item["is_active"])}
                    lines.append("پس از تأیید: " + ("کد فعال می‌شود." if state["discount_target"]["active"] else "کد غیرفعال می‌شود؛ سوابق استفاده حفظ می‌شود."))
            if payment_context:
                lines.append(payment_context)
            if action.key in {"bot_on", "bot_off"}:
                lines.extend(["وضعیت فعلی:\n" + self.bot_status()[0],
                              "پس از تأیید: " + ("ربات فعال می‌شود و دسترسی کاربران باز می‌شود." if action.key == "bot_on" else
                                                 "ربات غیرفعال می‌شود؛ کاربران پیام بروزرسانی می‌بینند."),
                              "دسترسی مدیران برای فعال‌سازی دوباره محفوظ می‌ماند."])
            for field in form_fields(action, state["values"]):
                if payment_context and field.key == "method":
                    continue  # The canonical method name is already above.
                label = state["labels"].get(field.key, "")
                # Preview limits never alter the original arguments.
                if len(label) > 140:
                    label = label[:140] + "… (متن کامل بدون تغییر ذخیره شده است)"
                lines.append(f"{escape(field.label)}: {escape(label)}")
            lines.append("تا دکمه تأیید را نزنید، تغییری اعمال نمی‌شود.")
            if action.key in {"card_resolve", "crypto_resolve"}:
                lines.append("تأیید بازپرداخت صرفاً ثبت انجام واقعی آن است؛ این ربات انتقال بانکی انجام نمی‌دهد.")
            rows.append([self._form_button(state, "تأیید و اجرا", "confirm", style="success")])
            text = "\n".join(lines)
        else:
            field = self.current_field(state)
            choices = self.options(field, state, admin)
            state["options"] = choices
            for index, (value, label) in enumerate(choices):
                selected = field.kind.startswith("multi:") and int(value) in state.get("selected", [])
                rows.append([self._form_button(state, ("انتخاب‌شده: " if selected else "") + label,
                                              "pick", str(index))])
            if field.kind == "choice" and len(rows) == 2:
                rows = [[*rows[0], *rows[1]]]
            if field.kind.startswith(("entity:", "multi:")):
                page = state.get("page", 1)
                nav = []
                if page > 1:
                    nav.append(self._form_button(state, "قبلی", "next", str(page - 1)))
                if page < state["option_pages"]:
                    nav.append(self._form_button(state, "بعدی", "next", str(page + 1)))
                if nav:
                    rows.append(nav)
                info = (f"\nصفحه {page} از {state['option_pages']} | مجموع: {state['option_total']}"
                        "\nبرای جست‌وجو نام، شماره یا یوزرنیم را بفرستید؛ سپس نتیجه را با دکمه انتخاب کنید.")
                if not choices:
                    info += ("\nدسته‌ای برای انتخاب نیست؛ برای دیدن کل فهرست «همه / بدون محدودیت» را بزنید."
                             if field.default == "all" else
                             "\nموردی یافت نشد؛ عبارت جست‌وجو را تغییر دهید یا به بخش قبل برگردید.")
                if action.key == "message" and field.kind == "entity:user" and not state.get("search"):
                    info = "\nابتدا نام، یوزرنیم یا چت‌آی‌دی گیرنده را بفرستید؛ سپس فقط نتیجه‌های جست‌وجو نمایش داده می‌شوند."
                if action.key == "ticket_reply" and field.kind == "entity:ticket":
                    info += "\nتیکت بسته در این فهرست نیست؛ برای پاسخ، ابتدا از جزئیات تیکت وضعیت را به باز تغییر دهید."
            else:
                info = "\nاز دکمه‌ها انتخاب کنید." if field.kind == "choice" else "\nمقدار را در یک پیام بفرستید."
            if field.kind.startswith("multi:"):
                rows.append([self._form_button(state, "پایان انتخاب محصولات", "multi")])
            if field.default is not None:
                default_label = "ردکردن این فیلد / پیش‌فرض"
                if field.default in {"0", "all", "[]"} and field.kind.startswith(("entity:", "multi:")):
                    default_label = "همه / بدون محدودیت"
                if field.key == "icon":
                    default_label = "بدون آیکون"
                rows.append([self._form_button(state, default_label, "default")])
            text = f"<b>{action.label}</b>"
            if payment_context:
                text += "\n" + payment_context
            text += f"\nمرحله {state['step'] - state.get('minimum_step', 0) + 1}: {escape(field.label)}{info}"
            if state.get("return_to") and state["labels"].get("target"):
                selected_label = state["labels"]["target"]
                preview = selected_label[:120] + ("…" if len(selected_label) > 120 else "")
                text += "\nانتخاب‌شده: " + escape(preview) + " | شناسه: " + escape(state["values"]["target"])
            if field.hint:
                text += "\n" + escape(field.hint)
        if state["values"].get("_product_scope"):
            product = self.catalog._product(int(state["values"]["product"]))
            text += f"\n\nمحصول ثابت: {escape(product['name'])} | شناسه: {product['id']}\nپاداش ثابت یا درصدی این محصول قابل تنظیم است؛ قواعد عمومی موجود بدون تغییر می‌مانند."
        if state["step"] > state.get("minimum_step", 0):
            rows.append([self._form_button(state, "مرحله قبل / اصلاح", "back")])
        rows.append([self._button("لغو و بازگشت", self.return_route(state) if state.get("return_to") else "g:" + action.group, style="danger")])
        self._save(user, state)
        self._publish(state, user, text, rows)

    def execute(self, state: dict, event: dict, user: dict, admin: dict) -> None:
        from .admin import _ADMIN_INPUT_ERRORS

        # Re-read the verified role at the mutation boundary, never trust the
        # role snapshot saved at form creation or values inside callback data.
        admin = self.authorise(user, admin, event)
        action = ACTIONS[state["action"]]
        if not self.allowed(action, admin["role"]):
            raise ButtonInputError("مجوز این عملیات را ندارید.")
        identity = self._input_id(event)
        if state.get("status") != "executing":
            state.update(status="executing", execution_input=identity,
                         execution_update=event.get("_admin_update_id"))
            self._save(user, state)
        elif state.get("execution_input") != identity:
            raise ConflictError("button form execution identity changed")
        rest, parts = arguments(action, state["values"], page=state.get("result_page", 1))
        message = {"chat": {"id": user["chat_id"], "type": "private"},
                   # Stable per form, unlike the reused keyboard message ID.
                   "message_id": "ui-" + state["token"], "from": {"id": user["telegram_user_id"]}}
        for name in ("_admin_update_id", "_admin_update_replay"):
            if name in event:
                message[name] = event[name]
        context = {"parts": parts, "responses": [], "state": state, "page": None}
        self.controller._button_context = context
        terminal_error: Exception | None = None
        try:
            if action.key == "admin_help":
                self.controller._send(int(user["chat_id"]),
                                      "از پنل، بخش و عملیات را انتخاب کنید. انتخاب‌ها با دکمه‌اند؛ فقط مقادیر و متن‌ها تایپ می‌شوند. "
                                      "برای اصلاح، مرحله قبل و برای خروج، لغو را بزنید. عملیات حساس تأیید نهایی دارند.")
            elif action.key in {"inventory_add", "inventory_edit"}:
                mode = "admin:inventory" if action.key == "inventory_add" else "admin:inventory_edit"
                target_key = "product_id" if action.key == "inventory_add" else "item_id"
                self.controller._handle_state(message, state["values"]["secret"], user, admin,
                                              state={"state": mode, "data": {target_key: int(rest)}})
            else:
                self.controller._handlers[action.command](rest, message, user, admin)
        except _ADMIN_INPUT_ERRORS as exc:
            terminal_error = exc
        except TelegramError as exc:
            # The transport outcome may be uncertain; never blindly repeat a
            # financial confirmation after its update is acknowledged.
            terminal_error = exc
        except DatabaseError:
            raise
        finally:
            self.controller._button_context = None
        if terminal_error:
            state["status"] = "confirm" if action.mutation else "editing"
            state["revision"] += 1
            state["last_input"] = identity
            if isinstance(terminal_error, TelegramError):
                state["status"] = "done"
                text = "پاسخ سرویس کامل دریافت نشد. پیش از تکرار، وضعیت مورد را از فهرست بررسی کنید."
            else:
                text = "عملیات انجام نشد: " + self.friendly_error(str(terminal_error))
                if not action.mutation:
                    fields = form_fields(action, state["values"])
                    if fields:
                        state["step"] = len(fields) - 1
                        state["values"].pop(fields[-1].key, None)
                        state["labels"].pop(fields[-1].key, None)
                    else:
                        state["status"] = "done"
            self._save(user, state)
            self._send(user, escape(text), self.navigation(action.group))
            if state["status"] in {"confirm", "editing"}:
                self.render(state, user, admin)
            return
        # Broadcast preview deliberately transitions to its existing, durable
        # counted confirmation state. Inventory standalone handlers may clear
        # state; the UI completion marker is persisted again below.
        if not action.key.startswith("broadcast_"):
            state["status"] = "done"
            state["last_input"] = identity
            state["revision"] += 1
            state.pop("options", None)
            for field in form_fields(action, state["values"]):
                if field.kind == "secret":
                    state["values"].pop(field.key, None)
            if context["page"]:
                state["list_pages"] = context["page"][1]
            self._save(user, state)
        for text, markup in context["responses"]:
            self.controller._send(int(user["chat_id"]), text, markup)
        if action.key.startswith("broadcast_"):
            self._retire_prompt(user, state.get("prompt_message_id"))
        else:
            text = "گزینه بعدی را انتخاب کنید."
            if action.key in {"bot_on", "bot_off"}:
                text = self.bot_status()[0] + "\n" + text
            if action.key == "payment":
                text = self.payment_context(state) + "\n" + text
            self._publish(state, user, text, self.result_rows(state, admin))

    @staticmethod
    def return_route(state: dict) -> str:
        if state.get("return_to", {}).get("scope") == "faq":
            return "faqback:" + str(state["return_to"].get("category_id") or 0)
        return "j:back" if state.get("return_to", {}).get("scope") == "joins" else "c:back"

    def result_rows(self, state: dict, admin: dict) -> list[list[dict]]:
        action = ACTIONS[state["action"]]
        rows = []
        if action.key in {"user", "users"}:
            rows.append([self._button("جست‌وجوی کاربر دیگر" if action.key == "user" else "جست‌وجوی کاربر", "a:user")])
        if action.key in {"order", "orders"}:
            rows.append([self._button("جست‌وجوی سفارش", "a:order")])
        if action.key in {"ticket", "tickets"}:
            rows.append([self._button("جست‌وجوی تیکت", "a:ticket")])
        if action.key == "tickets":
            status = state["values"].get("status")
            items = self.db.list_tickets(status=None if status == "all" else status,
                                         limit=20, offset=(int(state.get("result_page", 1)) - 1) * 20)
            for item in items:
                owner = self.db.get_user(int(item["user_id"])) or {}
                identity = "@" + owner["username"] if owner.get("username") else str(owner.get("chat_id") or "بدون شناسه")
                rows.append([self._button(item["ticket_number"] + " · " + identity, f"open:ticket:{item['ticket_number']}")])
        if action.key == "discounts":
            items, _, _ = self.controller._management_rows(int(state.get("result_page", 1)), "SELECT * FROM discounts ORDER BY id DESC")
            rows.extend([[self._button(label_text(item["code"]) + " · " + ("فعال" if item["is_active"] else "غیرفعال"),
                                       f"discount:{item['id']}")] for item in items])
        if action.key == "faq_categories":
            items, _, _ = self.controller._management_rows(int(state.get("result_page", 1)),
                                                          "SELECT * FROM faq_categories ORDER BY sort_order,id")
            rows.extend([[self._button(label_text(item["name"], 40) + " · " + ("فعال" if item["is_active"] else "غیرفعال"),
                                       f"faqcat:{item['id']}")] for item in items])
            rows.append([self._button("جست‌وجوی دسته پرسش‌ها", "a:faqs")])
            rows.append([self._button("افزودن دسته پرسش‌ها", "a:faq_category_add")])
        if action.key == "faqs":
            raw_category = state["values"].get("target", "all")
            category_id = int(raw_category) if str(raw_category).isdigit() else None
            query, params = self.controller._faq_listing_query(category_id, state.get("result_search", ""))
            items, _, _ = self.controller._management_rows(int(state.get("result_page", 1)), query, params)
            rows.extend([[self._button(label_text(item["question"], 40) + " · " + ("فعال" if item["is_active"] else "غیرفعال"),
                                       f"faqitem:{item['id']}")] for item in items])
            if category_id is not None:
                category = self.db.get_faq_category(category_id)
                if category:
                    rows.extend([
                        [self._button("افزودن پرسش در این دسته", f"faqdo:faq_add:{category_id}")],
                        [self._button("ویرایش مشخصات این دسته", f"faqdo:faq_category_set:{category_id}")],
                        [self._button("غیرفعال‌کردن این دسته" if category["is_active"] else "فعال‌کردن این دسته", f"faqdo:faq_category_toggle:{category_id}")],
                        [self._button("حذف این دسته", f"faqdo:faq_category_delete:{category_id}", style="danger")],
                        [self._button("نمایش همه پرسش‌های این دسته", f"faqcat:{category_id}")],
                    ])
            rows.append([self._button("بازگشت به دسته‌های پرسش‌ها", "a:faq_categories")])
        if action.key in {"bot_on", "bot_off"} and self.allowed(action, admin["role"]):
            rows.extend(self.bot_status()[1])
        if state.get("return_to"):
            if state["return_to"].get("scope") == "joins":
                return [[self._button("بازگشت", "j:back")]]
            rows.append([self._button("بازگشت به بخش انتخاب‌شده", self.return_route(state))])
        for key in RESULT_ACTIONS.get(action.key, ()):
            linked = ACTIONS[key]
            if self.allowed(linked, admin["role"]):
                target = state["values"]["target"]
                if action.key == "ticket":
                    ticket = self.controller._require_ticket(target)
                    if ticket["status"] == "closed" and key in {"ticket_reply", "ticket_close"}:
                        continue
                if action.key == "order":
                    order = self.controller._require_order(target)
                    try:
                        info = order_information.decode(order.get("customer_info_json"))
                    except (TypeError, ValueError):
                        info = {}
                    if key == "order_attachment" and not order_information.attachments(info):
                        continue
                    if key == "complete" and info.get("collecting"):
                        continue
                if self.entity_value(linked.fields[0], target, state) is not None:
                    rows.append([self._button(linked.label, f"open:{key}:{target}")])
        if "list_pages" in state and not action.mutation:
            page, pages = state.get("result_page", 1), state["list_pages"]
            nav = []
            if page > 1:
                nav.append(self._form_button(state, "صفحه قبل", "list", str(page - 1)))
            if page < pages:
                nav.append(self._form_button(state, "صفحه بعد", "list", str(page + 1)))
            if nav:
                rows.append(nav)
            rows.append([self._form_button(state, "نمایش دوباره نتیجه", "list", str(page))])
        return self._compact_related_rows(rows) + self.navigation(
            "faq" if state.get("return_to", {}).get("scope") == "faq" else
            "catalog" if state.get("return_to") else action.group)

    def faq_detail(self, identifier: int, user: dict, admin: dict) -> None:
        from .utils import render_rich_text, split_telegram_html
        admin = self.authorise(user, admin)
        if not self.allowed(ACTIONS["faqs"], admin["role"]):
            raise ButtonInputError("دسترسی مدیریت پرسش‌ها ندارید.")
        item = self.db.get_faq(identifier)
        if item is None:
            raise ButtonInputError("این پرسش حذف شده است.")
        category = self.db.get_faq_category(item["category_id"]) if item.get("category_id") else None
        text = (f"<b>پرسش {identifier}</b>\nدسته: {escape((category or {}).get('name') or 'بدون دسته')}"
                f"\nوضعیت: {'فعال' if item['is_active'] else 'غیرفعال'}\nترتیب نمایش: {item['sort_order']}"
                f"\n\n<b>{escape(item['question'])}</b>\n{render_rich_text(item['answer'])}")
        rows = [
            [self._button("ویرایش پرسش و پاسخ", f"faqdo:faq_set:{identifier}")],
            [self._button("غیرفعال‌کردن این پرسش" if item["is_active"] else "فعال‌کردن این پرسش", f"faqdo:faq_toggle:{identifier}")],
            [self._button("حذف این پرسش", f"faqdo:faq_delete:{identifier}", style="danger")],
            [self._button("بازگشت به پرسش‌های دسته", f"faqback:{item.get('category_id') or 0}")],
        ]
        previous = self.db.get_user_state(int(user["id"]))
        self.db.clear_user_state(int(user["id"]))
        chunks = split_telegram_html(text, maximum=1900)
        for index, chunk in enumerate(chunks):
            self._send(user, chunk, rows if index == len(chunks) - 1 else None)
        if previous:
            self._retire_prompt(user, previous["data"].get("prompt_message_id"))

    def discount_detail(self, identifier: int, user: dict, admin: dict) -> None:
        admin = self.authorise(user, admin)
        if not self.allowed(ACTIONS["discount_toggle"], admin["role"]):
            raise ButtonInputError("دسترسی مدیریت تخفیف ندارید.")
        item = self.controller._query_one("SELECT * FROM discounts WHERE id=?", (identifier,))
        if item is None:
            raise ButtonInputError("این تخفیف حذف شده است.")
        from .utils import display_datetime
        value = f"{item['value']} درصد" if item["discount_type"] == "percent" else money(item["value"])
        text = (f"<b>کد تخفیف {escape(item['code'])}</b>\nوضعیت: {'فعال' if item['is_active'] else 'غیرفعال'}"
                f"\nمقدار: {value}\nاستفاده: {item['used_count']} از {item.get('max_uses') or 'نامحدود'}"
                f"\nپایان اعتبار (تهران): {display_datetime(item.get('ends_at'), self.controller.settings.timezone)}"
                "\nغیرفعال‌کردن، استفادهٔ جدید را متوقف می‌کند و سوابق قبلی را حذف نمی‌کند."
                "\nبرای حفظ سوابق، حذف فقط برای کدهایی مجاز است که استفاده نشده‌اند.")
        previous = self.db.get_user_state(int(user["id"]))
        self.db.clear_user_state(int(user["id"]))
        self._send(user, text, [
            [self._button("غیرفعال‌کردن این کد" if item["is_active"] else "فعال‌کردن این کد", f"discountdo:{item['id']}:toggle")],
            [self._button("حذف این کد", f"discountdo:{item['id']}:delete", style="danger")],
            [self._button("بازگشت به فهرست تخفیف‌ها", "a:discounts")],
        ])
        if previous:
            self._retire_prompt(user, previous["data"].get("prompt_message_id"))

    @staticmethod
    def friendly_error(text: str) -> str:
        format_errors = {
            "closed ticket cannot receive messages": "این تیکت بسته شده است. ابتدا از جزئیات تیکت، وضعیت را به باز تغییر دهید؛ سپس پاسخ را ثبت کنید.",
            "reservations are valid only for ready products": "رزرو فقط برای فرمت موجود در انبار است؛ پیش از تغییر به فرمت دستی، رزرو را غیرفعال کنید.",
            "remove ready-product inventory before changing type to manual": "محصول هنوز آیتم انبار دارد. برای حفظ سوابق تحویل، آن‌ها خودکار حذف نمی‌شوند؛ برای فرمت دستی محصول جدا بسازید یا فقط موجودی تحویل‌نشده را مدیریت کنید.",
            "resolve all live ready-product orders before changing type": "این محصول سفارش آمادهٔ باز دارد؛ ابتدا سفارش‌ها را تعیین تکلیف کنید، سپس فرمت را تغییر دهید.",
        }
        text = format_errors.get(text, text)
        # Compatibility errors sometimes point to old commands. In the button
        # flow refer to their human-readable operation instead.
        for action in sorted(ACTIONS.values(), key=lambda item: -len(item.command)):
            text = text.replace(action.command, "«" + action.label + "»")
        return text
