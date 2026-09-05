"""Channel-centred force-join navigation; writes use the existing confirmed FSM."""
from __future__ import annotations

import copy
from typing import Any

from .keyboards import force_join_channel_button
from .utils import escape, split_telegram_html


class AdminJoins:
    def __init__(self, ui: Any) -> None:
        self.ui = ui
        self.controller = ui.controller
        self.db = ui.db

    def authorise(self, user: dict, admin: dict, event: dict | None = None) -> dict:
        from .admin_ui import ButtonInputError

        current = self.ui.authorise(user, admin, event)
        if current["role"] not in {"owner", "admin"}:
            raise ButtonInputError("مدیریت جوین اجباری برای این نقش مجاز نیست.")
        return current

    @staticmethod
    def _number(value: str) -> int:
        from .admin_ui import ButtonInputError

        if not value.isascii() or not value.isdigit() or not 0 < int(value) < 2**63:
            raise ButtonInputError("دکمهٔ جوین اجباری معتبر نیست.")
        return int(value)

    def context(self, user: dict) -> dict:
        stored = self.db.get_user_state(int(user["id"]))
        if stored and stored["state"] == "admin:joins":
            return {key: copy.deepcopy(stored["data"][key]) for key in ("scope", "kind", "id", "page") if key in stored["data"]}
        if stored and stored["state"] == "admin:ui":
            context = stored["data"].get("return_to", {})
            if context.get("scope") == "joins":
                return copy.deepcopy(context)
        return {"scope": "joins", "kind": "list", "page": 1}

    def _publish(self, context: dict, text: str, rows: list[list[dict]], user: dict, admin: dict) -> None:
        previous = self.db.get_user_state(int(user["id"]))
        previous_id = ((previous or {}).get("data") or {}).get("prompt_message_id")
        state = {**context, "scope": "joins", "actor": admin["id"], "chat": user["chat_id"]}
        self.db.set_user_state(int(user["id"]), "admin:joins", state)
        parts = split_telegram_html(text, maximum=3500)
        result = None
        for index, part in enumerate(parts):
            result = self.ui._send(user, part, rows if index == len(parts) - 1 else None)
        if isinstance(result, dict) and isinstance(result.get("message_id"), int):
            state["prompt_message_id"] = result["message_id"]
            self.db.set_user_state(int(user["id"]), "admin:joins", state)
            if previous_id != result["message_id"]:
                self.ui._retire_prompt(user, previous_id)

    def show_list(self, user: dict, admin: dict, page: int = 1) -> None:
        from .admin_ui import label_text

        total = int(self.controller._query_one("SELECT COUNT(*) total FROM force_join_channels")["total"])
        pages = max(1, (total + 19) // 20)
        page = min(max(1, page), pages)
        channels = self.controller._query(
            "SELECT id, title, is_active FROM force_join_channels ORDER BY sort_order, id LIMIT ? OFFSET ?",
            (20, (page - 1) * 20))
        rows = [[force_join_channel_button(label_text(channel["title"], 54), int(channel["id"]), page,
                                          active=bool(channel["is_active"]))] for channel in channels]
        pager = []
        if page > 1:
            pager.append(self.ui._button("صفحه قبل", f"j:list:{page - 1}"))
        if page < pages:
            pager.append(self.ui._button("صفحه بعد", f"j:list:{page + 1}"))
        if pager:
            rows.append(pager)
        rows += [[self.ui._button("افزودن کانال اجباری", f"j:add:{page}")],
                 [self.ui._button("بازگشت", "g:settings")]]
        text = f"<b>لیست کانال‌های جوین اجباری</b>\nصفحه {page} از {pages} | مجموع: {total}\n✅ فعال | ❌ غیرفعال"
        if not channels:
            text += "\nکانالی ثبت نشده است."
        self._publish({"kind": "list", "page": page}, text, rows, user, admin)

    def _channel(self, channel_id: int) -> dict | None:
        return self.controller._query_one("SELECT id, title, telegram_chat_id, invite_url, is_active FROM force_join_channels WHERE id=?", (channel_id,))

    def show_channel(self, channel_id: int, page: int, user: dict, admin: dict) -> None:
        channel = self._channel(channel_id)
        if channel is None:
            self.ui._send(user, "این کانال حذف شده یا دیگر وجود ندارد؛ فهرست به‌روز شد.")
            self.show_list(user, admin, page)
            return
        text = (f"<b>{escape(channel['title'])}</b>\nشناسه: {channel_id}\nکانال: {escape(channel['telegram_chat_id'])}"
                f"\nوضعیت: {'فعال' if channel['is_active'] else 'غیرفعال'}\nلینک عضویت: {escape(channel.get('invite_url') or 'تنظیم نشده')}")
        rows = [[self.ui._button("فعال/غیرفعال کردن", f"j:toggle:{channel_id}:{page}")],
                [self.ui._button("حذف", f"j:delete:{channel_id}:{page}", style="danger")],
                [self.ui._button("بازگشت", f"j:list:{page}")]]
        self._publish({"kind": "channel", "id": channel_id, "page": page}, text, rows, user, admin)

    def restore(self, context: dict, user: dict, admin: dict) -> None:
        page = int(context.get("page") or 1)
        if context.get("kind") == "channel":
            self.show_channel(int(context["id"]), page, user, admin)
        else:
            self.show_list(user, admin, page)

    def callback(self, suffix: str, event: dict, user: dict, admin: dict) -> bool:
        from .admin_ui import ButtonInputError

        admin = self.authorise(user, admin, event)
        parts = suffix.split(":")
        if parts == ["back"]:
            self.restore(self.context(user), user, admin)
        elif len(parts) == 2 and parts[0] in {"list", "add"}:
            page = self._number(parts[1])
            if parts[0] == "list":
                self.show_list(user, admin, page)
            else:
                self.ui.begin("join_add", event, user, admin, return_to={"scope": "joins", "kind": "list", "page": page})
        elif len(parts) == 3 and parts[0] in {"channel", "toggle", "delete"}:
            identifier, page = self._number(parts[1]), self._number(parts[2])
            if parts[0] == "channel" or self._channel(identifier) is None:
                self.show_channel(identifier, page, user, admin)
            else:
                self.ui.begin("join_" + parts[0], event, user, admin, selected=str(identifier),
                              return_to={"scope": "joins", "kind": "channel", "id": identifier, "page": page})
        else:
            raise ButtonInputError("دکمهٔ جوین اجباری معتبر نیست.")
        return True

    def message(self, event: dict, user: dict, admin: dict) -> bool:
        admin = self.authorise(user, admin, event)
        self.restore(self.context(user), user, admin)
        return True
