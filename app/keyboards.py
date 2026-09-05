"""Telegram reply and inline keyboard builders.

Button labels are plain text except the explicitly requested status prefix on
admin force-join channel rows. Other decoration uses ``icon_custom_emoji_id``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal

from .utils import is_safe_https_url, is_safe_telegram_channel_url
from .customer_layouts import tagged


Button = dict[str, Any]
ReplyMarkup = dict[str, Any]
ButtonStyle = Literal["danger", "success", "primary"]

SHOP = "فروشگاه"
WALLET = "کیف پول"
ACCOUNT = "حساب من"
SUPPORT = "پشتیبانی"
REFERRAL = "دعوت و کسب درآمد"
CHANNEL = "کانال"

MAIN_MENU_ROWS: tuple[tuple[str, ...], ...] = (
    (SHOP,),
    (WALLET, ACCOUNT),
    (SUPPORT,),
    (REFERRAL,),
    (CHANNEL,),
)

_MAIN_ICON_KEYS = {
    SHOP: "shop",
    WALLET: "wallet",
    ACCOUNT: "account",
    SUPPORT: "support",
    REFERRAL: "referral",
    CHANNEL: "channel",
}

_MAIN_STYLES: dict[str, ButtonStyle | None] = {
    SHOP: "success",
    WALLET: "success",
    ACCOUNT: "primary",
    SUPPORT: None,
    REFERRAL: "primary",
    CHANNEL: None,
}

# Ranges which Telegram clients render as emoji, including symbols that can be
# emoji when followed by a variation selector.  The BMP exceptions mirror the
# Unicode 17 Emoji property; ASCII keycap bases (#, *, 0-9) remain valid plain
# text and are rejected only when their FE0F/20E3 sequence is present. Persian
# letters and ZWNJ are intentionally outside these ranges.
_EMOJI_RANGES: tuple[tuple[int, int], ...] = (
    (0x1F000, 0x1FAFF),
    (0x1FC00, 0x1FFFF),
    (0x2194, 0x2199),
    (0x21A9, 0x21AA),
    (0x2600, 0x26FF),
    (0x2700, 0x27BF),
    (0x2300, 0x23FF),
    (0x2B00, 0x2BFF),
    (0x1F1E6, 0x1F1FF),
)
_EMOJI_CODEPOINTS = {
    0x00A9,
    0x00AE,
    0x203C,
    0x2049,
    0x20E3,
    0x2122,
    0x2139,
    0x24C2,
    0x25AA,
    0x25AB,
    0x25B6,
    0x25C0,
    0x25FB,
    0x25FC,
    0x25FD,
    0x25FE,
    0x2934,
    0x2935,
    0x3030,
    0x303D,
    0x3297,
    0x3299,
    0xFE0F,
}


def contains_emoji(value: str) -> bool:
    """Return ``True`` when a string contains an emoji code point."""

    for character in value:
        codepoint = ord(character)
        if codepoint in _EMOJI_CODEPOINTS:
            return True
        if any(start <= codepoint <= end for start, end in _EMOJI_RANGES):
            return True
    return False


def validate_button_label(text: str) -> str:
    """Validate the project-wide no-emoji button-label policy."""

    if not isinstance(text, str):
        raise TypeError("button text must be a string")
    if not text.strip():
        raise ValueError("button text must not be empty")
    if contains_emoji(text):
        raise ValueError("button text must not contain Unicode emoji; use an icon id")
    return text


def _add_presentation(
    button: Button,
    *,
    style: ButtonStyle | Literal["default"] | None,
    icon_custom_emoji_id: str | None,
) -> Button:
    if style not in {None, "default", "danger", "success", "primary"}:
        raise ValueError("button style must be danger, success, primary, default, or None")
    if style not in {None, "default"}:
        button["style"] = style
    if icon_custom_emoji_id is not None:
        icon_id = str(icon_custom_emoji_id).strip()
        if not icon_id:
            raise ValueError("icon_custom_emoji_id must not be empty")
        button["icon_custom_emoji_id"] = icon_id
    return button


def reply_button(
    text: str,
    *,
    style: ButtonStyle | Literal["default"] | None = None,
    icon_custom_emoji_id: str | None = None,
    request_contact: bool = False,
    request_location: bool = False,
    request_poll: Mapping[str, Any] | None = None,
    request_users: Mapping[str, Any] | None = None,
    request_chat: Mapping[str, Any] | None = None,
    request_managed_bot: Mapping[str, Any] | None = None,
    web_app: Mapping[str, Any] | None = None,
) -> Button:
    """Build one Bot API ``KeyboardButton`` object."""

    actions: dict[str, Any] = {}
    if request_contact:
        actions["request_contact"] = True
    if request_location:
        actions["request_location"] = True
    for name, value in (
        ("request_poll", request_poll),
        ("request_users", request_users),
        ("request_chat", request_chat),
        ("request_managed_bot", request_managed_bot),
        ("web_app", web_app),
    ):
        if value is not None:
            actions[name] = dict(value)
    if len(actions) > 1:
        raise ValueError("a reply button can contain at most one request action")

    button: Button = {"text": validate_button_label(text), **actions}
    return _add_presentation(
        button,
        style=style,
        icon_custom_emoji_id=icon_custom_emoji_id,
    )


def reply_keyboard(
    rows: Sequence[Sequence[Mapping[str, Any]]],
    *,
    resize_keyboard: bool = True,
    is_persistent: bool | None = None,
    one_time_keyboard: bool | None = None,
    input_field_placeholder: str | None = None,
    selective: bool | None = None,
) -> ReplyMarkup:
    """Build ``ReplyKeyboardMarkup`` while preserving row order."""

    keyboard = [[dict(button) for button in row] for row in rows]
    if not keyboard or any(not row for row in keyboard):
        raise ValueError("reply keyboard rows must not be empty")
    markup: ReplyMarkup = {
        "keyboard": keyboard,
        "resize_keyboard": bool(resize_keyboard),
    }
    if is_persistent is not None:
        markup["is_persistent"] = bool(is_persistent)
    if one_time_keyboard is not None:
        markup["one_time_keyboard"] = bool(one_time_keyboard)
    if input_field_placeholder is not None:
        if not 1 <= len(input_field_placeholder) <= 64:
            raise ValueError("input_field_placeholder must contain 1-64 characters")
        markup["input_field_placeholder"] = input_field_placeholder
    if selective is not None:
        markup["selective"] = bool(selective)
    return markup


def main_menu_keyboard(
    icon_ids: Mapping[str, str] | None = None,
    *,
    is_persistent: bool = True,
    include_admin: bool = False,
) -> ReplyMarkup:
    """Return the exact five-row Persian main-menu reply keyboard.

    ``icon_ids`` uses semantic keys from :class:`app.config.Settings`, namely
    ``shop``, ``wallet``, ``account``, ``support``, ``referral`` and
    ``channel``.  Persian-label keys are accepted too for convenience.
    """

    icons = dict(icon_ids or {})

    def icon_for(label: str) -> str | None:
        return icons.get(_MAIN_ICON_KEYS[label]) or icons.get(label)

    rows = [
        [
            reply_button(
                label,
                style=_MAIN_STYLES[label],
                icon_custom_emoji_id=icon_for(label),
            )
            for label in row
        ]
        for row in MAIN_MENU_ROWS
    ]
    if include_admin:
        rows.append([reply_button("پنل مدیریت")])
    return tagged("main", reply_keyboard(rows, resize_keyboard=True, is_persistent=is_persistent))


def inline_main_menu_keyboard(
    icon_ids: Mapping[str, str] | None = None,
    main_channel_url: str = "",
    *,
    include_admin: bool = False,
) -> ReplyMarkup:
    """Build the canonical five-row menu, including a direct Channel link."""
    icons = dict(icon_ids or {})
    callbacks = {
        SHOP: "store",
        WALLET: "wallet",
        ACCOUNT: "profile",
        SUPPORT: "support",
        REFERRAL: "referral",
        CHANNEL: "channel",
    }
    channel_url = str(main_channel_url or "").strip()
    rows: list[list[Button]] = []
    for row in MAIN_MENU_ROWS:
        buttons: list[Button] = []
        for label in row:
            presentation = {
                "style": _MAIN_STYLES[label],
                "icon_custom_emoji_id": icons.get(_MAIN_ICON_KEYS[label]) or icons.get(label),
            }
            if label == CHANNEL and is_safe_telegram_channel_url(channel_url):
                button = url_button(label, channel_url, **presentation)
            else:
                button = callback_button(label, callbacks[label], **presentation)
            buttons.append(button)
        rows.append(buttons)
    if include_admin:
        rows.append([callback_button("پنل مدیریت", "adm:ui:home")])
    return tagged("main", inline_keyboard(rows))


def request_contact_button(
    text: str = "ارسال شماره موبایل",
    *,
    style: ButtonStyle | Literal["default"] | None = "primary",
    icon_custom_emoji_id: str | None = None,
) -> Button:
    return reply_button(
        text,
        style=style,
        icon_custom_emoji_id=icon_custom_emoji_id,
        request_contact=True,
    )


def contact_keyboard(
    text: str = "ارسال شماره موبایل",
    *,
    style: ButtonStyle | Literal["default"] | None = "primary",
    icon_custom_emoji_id: str | None = None,
) -> ReplyMarkup:
    """One-time private-chat keyboard that requests the user's contact."""

    return tagged("input_contact", reply_keyboard(
        [[request_contact_button(text, style=style, icon_custom_emoji_id=icon_custom_emoji_id)],
         [reply_button("لغو و بازگشت")]],
        resize_keyboard=True,
        one_time_keyboard=True,
    ))


def inline_button(
    text: str,
    *,
    callback_data: str | None = None,
    url: str | None = None,
    copy_text: str | None = None,
    web_app: Mapping[str, Any] | None = None,
    login_url: Mapping[str, Any] | None = None,
    switch_inline_query: str | None = None,
    switch_inline_query_current_chat: str | None = None,
    disabled: bool = False,
    style: ButtonStyle | Literal["default"] | None = None,
    icon_custom_emoji_id: str | None = None,
) -> Button:
    """Build an ``InlineKeyboardButton`` and enforce its one-action rule."""

    actions: list[tuple[str, Any]] = []
    if callback_data is not None:
        if not 1 <= len(callback_data.encode("utf-8")) <= 64:
            raise ValueError("callback_data must contain 1-64 UTF-8 bytes")
        actions.append(("callback_data", callback_data))
    if url is not None:
        if not is_safe_https_url(url):
            raise ValueError("button URL must be a safe absolute HTTPS URL")
        actions.append(("url", url))
    if copy_text is not None:
        if not 1 <= len(copy_text) <= 256:
            raise ValueError("copy text must contain 1-256 characters")
        actions.append(("copy_text", {"text": copy_text}))
    for name, value in (("web_app", web_app), ("login_url", login_url)):
        if value is not None:
            actions.append((name, dict(value)))
    if switch_inline_query is not None:
        actions.append(("switch_inline_query", switch_inline_query))
    if switch_inline_query_current_chat is not None:
        actions.append(("switch_inline_query_current_chat", switch_inline_query_current_chat))
    if disabled:
        actions.append(("disabled", {}))
    if len(actions) != 1:
        raise ValueError("an inline button must contain exactly one action")

    action_name, action_value = actions[0]
    button: Button = {
        "text": validate_button_label(text),
        action_name: action_value,
    }
    return _add_presentation(
        button,
        style=style,
        icon_custom_emoji_id=icon_custom_emoji_id,
    )


def callback_button(
    text: str,
    callback_data: str,
    *,
    style: ButtonStyle | Literal["default"] | None = None,
    icon_custom_emoji_id: str | None = None,
) -> Button:
    return inline_button(
        text,
        callback_data=callback_data,
        style=style,
        icon_custom_emoji_id=icon_custom_emoji_id,
    )


def force_join_channel_button(title: str, channel_id: int, page: int, *, active: bool) -> Button:
    """Narrow user-approved exception; never accept emoji in the channel title."""
    if type(channel_id) is not int or type(page) is not int or not 0 < channel_id < 2**63 or page < 1:
        raise ValueError("channel id and page must be positive integers")
    button = callback_button(title, f"adm:ui:j:channel:{channel_id}:{page}")
    button["text"] = ("✅ " if active else "❌ ") + button["text"]
    return button


def url_button(
    text: str,
    url: str,
    *,
    style: ButtonStyle | Literal["default"] | None = None,
    icon_custom_emoji_id: str | None = None,
) -> Button:
    return inline_button(
        text,
        url=url,
        style=style,
        icon_custom_emoji_id=icon_custom_emoji_id,
    )


def copy_text_button(
    text: str,
    value: str,
    *,
    style: ButtonStyle | Literal["default"] | None = None,
    icon_custom_emoji_id: str | None = None,
) -> Button:
    return inline_button(
        text,
        copy_text=value,
        style=style,
        icon_custom_emoji_id=icon_custom_emoji_id,
    )


def back_button(
    callback_data: str,
    *,
    text: str = "بازگشت",
    icon_custom_emoji_id: str | None = None,
) -> Button:
    return {**callback_button(
        text,
        callback_data,
        icon_custom_emoji_id=icon_custom_emoji_id,
    ), "_layout_slot": "back"}


def inline_keyboard(
    rows: Sequence[Sequence[Mapping[str, Any]]],
    *,
    force_reply: bool | None = None,
) -> ReplyMarkup:
    """Build ``InlineKeyboardMarkup`` while preserving row order."""

    keyboard = [[dict(button) for button in row] for row in rows]
    if not keyboard or any(not row for row in keyboard):
        raise ValueError("inline keyboard rows must not be empty")
    markup: ReplyMarkup = {"inline_keyboard": keyboard}
    if force_reply is not None:
        markup["force_reply"] = bool(force_reply)
    return markup


def remove_keyboard(*, selective: bool | None = None) -> ReplyMarkup:
    markup: ReplyMarkup = {"remove_keyboard": True}
    if selective is not None:
        markup["selective"] = bool(selective)
    return markup


# Backwards-friendly descriptive alias.
main_reply_keyboard = main_menu_keyboard


__all__ = [
    "ACCOUNT",
    "Button",
    "ButtonStyle",
    "CHANNEL",
    "MAIN_MENU_ROWS",
    "REFERRAL",
    "ReplyMarkup",
    "SHOP",
    "SUPPORT",
    "WALLET",
    "back_button",
    "callback_button",
    "contact_keyboard",
    "contains_emoji",
    "force_join_channel_button",
    "copy_text_button",
    "inline_button",
    "inline_keyboard",
    "inline_main_menu_keyboard",
    "main_menu_keyboard",
    "main_reply_keyboard",
    "remove_keyboard",
    "reply_button",
    "reply_keyboard",
    "request_contact_button",
    "url_button",
    "validate_button_label",
]
