from __future__ import annotations

import html
import ipaddress
import json
import re
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlparse


_DIGIT_TABLE = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_AMOUNT_CLEANER = re.compile(r"[^0-9.-]")
_HTML_TOKEN = re.compile(r"(<[^>]*>|&(?:#[0-9]+|#x[0-9A-Fa-f]+|[A-Za-z][A-Za-z0-9]+);)")
_HTML_TAG = re.compile(r"^<\s*(/?)\s*([A-Za-z0-9-]+)[^>]*?(/?)\s*>$")
_TELEGRAM_CHANNEL_PATH = re.compile(
    r"/(?:[A-Za-z0-9_]{1,64}|\+[A-Za-z0-9_-]{5,128}|joinchat/[A-Za-z0-9_-]{5,128})/?"
)
_VOID_TAGS = frozenset({"br", "hr"})
_TELEGRAM_HTML_TAGS = frozenset(
    {
        "b",
        "strong",
        "i",
        "em",
        "u",
        "ins",
        "s",
        "strike",
        "del",
        "a",
        "code",
        "pre",
        "blockquote",
        "span",
        "tg-spoiler",
        "tg-emoji",
    }
)


class _TelegramHTMLValidator(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.stack: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in _TELEGRAM_HTML_TAGS:
            raise ValueError(f"تگ HTML پشتیبانی نمی‌شود: {tag}")
        values = dict(attrs)
        if len(values) != len(attrs):
            raise ValueError("ویژگی HTML تکراری است")
        if tag == "a":
            if set(values) != {"href"} or not values.get("href"):
                raise ValueError("تگ a فقط به href معتبر نیاز دارد")
            if not is_safe_https_url(values["href"]):
                raise ValueError("لینک باید یک آدرس کامل HTTPS با میزبان عمومی باشد")
        elif tag == "tg-emoji":
            if set(values) != {"emoji-id"} or not str(values.get("emoji-id") or "").isdigit():
                raise ValueError("tg-emoji به emoji-id عددی نیاز دارد")
        elif tag == "span":
            if values != {"class": "tg-spoiler"}:
                raise ValueError("span فقط برای tg-spoiler مجاز است")
        elif tag == "code" and values:
            css_class = str(values.get("class") or "")
            if set(values) != {"class"} or not css_class.startswith("language-"):
                raise ValueError("class تگ code معتبر نیست")
        elif tag == "blockquote" and values:
            if set(values) != {"expandable"}:
                raise ValueError("ویژگی blockquote معتبر نیست")
        elif values:
            raise ValueError(f"تگ {tag} ویژگی اضافی دارد")
        self.stack.append(tag)

    def handle_endtag(self, tag: str) -> None:
        if not self.stack or self.stack[-1] != tag:
            raise ValueError("تگ‌های HTML درست بسته نشده‌اند")
        self.stack.pop()

    def handle_startendtag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        raise ValueError("تگ خودبسته در HTML تلگرام مجاز نیست")

    def handle_comment(self, data: str) -> None:
        raise ValueError("comment در متن تلگرام مجاز نیست")

    def handle_decl(self, decl: str) -> None:
        raise ValueError("اعلان HTML در متن تلگرام مجاز نیست")

    def handle_entityref(self, name: str) -> None:
        if name not in {"lt", "gt", "amp", "quot"}:
            raise ValueError("entity نامعتبر در HTML تلگرام")

    def handle_charref(self, name: str) -> None:
        try:
            codepoint = int(name[1:], 16) if name.lower().startswith("x") else int(name)
        except ValueError as exc:
            raise ValueError("entity عددی نامعتبر است") from exc
        if not 0 < codepoint <= 0x10FFFF or 0xD800 <= codepoint <= 0xDFFF:
            raise ValueError("کد entity عددی معتبر نیست")

    def handle_data(self, data: str) -> None:
        if any(character in data for character in ("<", ">", "&")):
            raise ValueError("نویسه‌های HTML باید escape شوند")

    def finish(self) -> None:
        self.close()
        if self.stack:
            raise ValueError("یک تگ HTML بسته نشده است")


def normalize_digits(value: str) -> str:
    return value.translate(_DIGIT_TABLE)


def normalize_username(value: str | None) -> str:
    return (value or "").strip().lstrip("@").lower()


def is_safe_telegram_channel_url(value: Any) -> bool:
    """Accept only canonical HTTPS ``t.me`` channel or invite links."""

    original = str(value or "")
    raw = original.strip()
    if raw != original or not raw or "\\" in raw or any(
        character.isspace() or ord(character) < 32 for character in raw
    ):
        return False
    try:
        parsed = urlparse(raw)
        port = parsed.port
    except ValueError:
        return False
    return bool(
        parsed.scheme.casefold() == "https"
        and parsed.netloc.casefold() == "t.me"
        and parsed.hostname
        and parsed.hostname.casefold() == "t.me"
        and parsed.username is None
        and parsed.password is None
        and port is None
        and not parsed.params
        and not parsed.query
        and not parsed.fragment
        and _TELEGRAM_CHANNEL_PATH.fullmatch(parsed.path)
    )


def is_safe_telegram_invite_url(value: Any) -> bool:
    """Accept canonical HTTPS Telegram public-channel and invite URLs."""

    original = str(value or "")
    raw = original.strip()
    if raw != original or not raw or "\\" in raw or any(
        character.isspace() or ord(character) < 32 for character in raw
    ):
        return False
    try:
        parsed = urlparse(raw)
        port = parsed.port
    except ValueError:
        return False
    return bool(
        parsed.scheme.casefold() == "https"
        and parsed.netloc.casefold() in {"t.me", "telegram.me"}
        and parsed.hostname
        and parsed.hostname.casefold() in {"t.me", "telegram.me"}
        and parsed.username is None
        and parsed.password is None
        and port is None
        and not parsed.params
        and not parsed.query
        and not parsed.fragment
        and _TELEGRAM_CHANNEL_PATH.fullmatch(parsed.path)
    )


def is_safe_https_url(value: Any) -> bool:
    """Validate an absolute public HTTPS URL without credentials or controls."""

    original = str(value or "")
    raw = original.strip()
    if raw != original or not raw or "\\" in raw or any(
        character.isspace() or ord(character) < 32 for character in raw
    ):
        return False
    hostname = ""
    try:
        parsed = urlparse(raw)
        port = parsed.port
        hostname = (parsed.hostname or "").casefold().rstrip(".")
        address = ipaddress.ip_address(hostname)
    except ValueError:
        address = None
    if not hostname:
        return False
    if address is not None:
        if not address.is_global:
            return False
        authority_host = f"[{hostname}]" if address.version == 6 else hostname
    else:
        if (
            hostname == "localhost"
            or hostname.endswith((".localhost", ".local"))
            or "." not in hostname
        ):
            return False
        try:
            ascii_hostname = hostname.encode("idna").decode("ascii")
        except UnicodeError:
            return False
        labels = ascii_hostname.split(".")
        if any(
            not label
            or len(label) > 63
            or not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", label)
            for label in labels
        ) or not re.search(r"[a-z]", labels[-1]):
            # A DNS hostname ends in an alphabetic (or punycode) public
            # suffix.  Reject dotted-numeric strings which URL consumers may
            # reinterpret as abbreviated/octal IPv4 (for example ``127.1``
            # or ``0177.0.0.1``) after ``ipaddress`` correctly declines them.
            return False
        authority_host = hostname
    expected_authority = authority_host + (":443" if port == 443 else "")
    return bool(
        parsed.scheme.casefold() == "https"
        and parsed.netloc.casefold().rstrip(".") == expected_authority
        and parsed.username is None
        and parsed.password is None
        and port in {None, 443}
    )


def parse_amount(value: str) -> int:
    normalized = normalize_digits(value).replace(",", "").replace("٬", "")
    normalized = _AMOUNT_CLEANER.sub("", normalized)
    try:
        number = Decimal(normalized)
    except InvalidOperation as exc:
        raise ValueError("invalid amount") from exc
    if number != number.to_integral_value() or number <= 0:
        raise ValueError("amount must be a positive integer")
    return int(number)


def money(amount: int, currency: str = "تومان") -> str:
    return f"{int(amount):,} {currency}"


def escape(value: Any) -> str:
    return html.escape(str(value), quote=False)


def format_admin_text(value: Any) -> str:
    """Escape normal input, or validate explicit ``html:`` Telegram markup."""

    raw = str(value)
    if not raw.lower().startswith("html:"):
        return escape(raw)
    body = raw[5:].lstrip()
    if not body:
        raise ValueError("متن HTML نمی‌تواند خالی باشد")
    validator = _TelegramHTMLValidator()
    validator.feed(body)
    validator.finish()
    return body


def render_rich_text(value: Any) -> str:
    """Render stored explicit rich text, safely degrading malformed legacy data."""

    raw = str(value)
    if not raw.lower().startswith("html:"):
        return escape(raw)
    try:
        return format_admin_text(raw)
    except ValueError:
        return escape(raw[5:].lstrip())


def utc_now() -> datetime:
    return datetime.now(UTC)


def iso_now() -> str:
    return utc_now().isoformat(timespec="seconds")


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def display_name(user: dict[str, Any]) -> str:
    return (
        user.get("full_name")
        or user.get("first_name")
        or ("@" + user["username"] if user.get("username") else None)
        or "دوست عزیز"
    )


def extract_start_ref(text: str) -> int | None:
    parts = text.strip().split(maxsplit=1)
    if len(parts) != 2 or not parts[1].startswith("ref_"):
        return None
    value = normalize_digits(parts[1][4:])
    return int(value) if value.isdigit() else None


def clamp_text(value: str, maximum: int = 3900) -> str:
    if len(value) <= maximum:
        return value
    if maximum < 2:
        return "…"[:maximum]

    # Telegram messages in this project use HTML parse mode.  Splitting raw
    # markup (or an entity) can make the whole message fail.  Treat tags and
    # entities atomically and close any open tag after the ellipsis.
    output: list[str] = []
    stack: list[str] = []
    used = 0
    truncated = False
    for token in filter(None, _HTML_TOKEN.split(value)):
        tag = _HTML_TAG.match(token)
        is_markup = bool(tag)
        next_stack = list(stack)
        if tag:
            closing, name, self_closing = tag.groups()
            lowered = name.lower()
            if closing:
                if lowered in next_stack:
                    reverse_index = next_stack[::-1].index(lowered)
                    del next_stack[len(next_stack) - reverse_index - 1 :]
            elif not self_closing and lowered not in _VOID_TAGS:
                next_stack.append(lowered)
        closing_text = "".join(f"</{name}>" for name in reversed(next_stack))
        reserve = 1 + len(closing_text)
        if used + len(token) + reserve <= maximum:
            output.append(token)
            used += len(token)
            stack = next_stack
            continue
        if is_markup or token.startswith("&") and token.endswith(";"):
            truncated = True
            break
        available = maximum - used - 1 - len(
            "".join(f"</{name}>" for name in reversed(stack))
        )
        if available > 0:
            fragment = token[:available]
            # A literal/unescaped '<' at the cut would produce '<…'.
            if fragment.rfind("<") > fragment.rfind(">"):
                fragment = fragment[: fragment.rfind("<")]
            output.append(fragment)
            used += len(fragment)
        truncated = True
        break

    if not truncated:
        return value
    suffix = "…" + "".join(f"</{name}>" for name in reversed(stack))
    return "".join(output) + suffix


def split_telegram_html(value: str, maximum: int = 3900) -> list[str]:
    """Split Telegram HTML without truncating content or breaking open tags.

    Critical delivery messages may contain credentials, so truncation is not a
    safe fallback.  Tags are closed at the end of each part and reopened in the
    next part; HTML entities are kept atomic.  Keeping the *raw* part below the
    Bot API limit is conservative because Telegram counts rendered characters.
    """

    text = str(value).strip()
    if not text:
        return []
    if maximum < 32:
        raise ValueError("maximum Telegram part length is too small")
    if len(text) <= maximum:
        return [text]

    chunks: list[str] = []
    current: list[str] = []
    current_length = 0
    # Store both the normalized name used for closing and the exact opening
    # token so attributes such as href survive across a part boundary.
    stack: list[tuple[str, str]] = []

    def closing_text(open_tags: list[tuple[str, str]] | None = None) -> str:
        selected = stack if open_tags is None else open_tags
        return "".join(f"</{name}>" for name, _opening in reversed(selected))

    def reopen_text(open_tags: list[tuple[str, str]] | None = None) -> str:
        selected = stack if open_tags is None else open_tags
        return "".join(opening for _name, opening in selected)

    def flush() -> None:
        nonlocal current, current_length
        if not current:
            return
        chunk = "".join(current) + closing_text()
        if len(chunk) > maximum:  # pragma: no cover - guarded by reservations.
            raise ValueError("Telegram HTML part exceeds the safe limit")
        chunks.append(chunk)
        prefix = reopen_text()
        if len(prefix) >= maximum:
            raise ValueError("Telegram HTML nesting exceeds the safe limit")
        current = [prefix] if prefix else []
        current_length = len(prefix)

    for token in filter(None, _HTML_TOKEN.split(text)):
        tag = _HTML_TAG.match(token)
        if tag:
            closing, name, self_closing = tag.groups()
            lowered = name.lower()
            next_stack = list(stack)
            if closing:
                if lowered in [item[0] for item in next_stack]:
                    reverse_index = [item[0] for item in next_stack][::-1].index(
                        lowered
                    )
                    del next_stack[len(next_stack) - reverse_index - 1 :]
            elif not self_closing and lowered not in _VOID_TAGS:
                next_stack.append((lowered, token))
            if current_length + len(token) + len(closing_text(next_stack)) > maximum:
                flush()
            if current_length + len(token) + len(closing_text(next_stack)) > maximum:
                raise ValueError("one Telegram HTML token exceeds the safe limit")
            current.append(token)
            current_length += len(token)
            stack = next_stack
            continue

        # Entities are one atomic token. Plain text can be divided at any
        # Unicode-codepoint boundary without dropping a character.
        if token.startswith("&") and token.endswith(";"):
            if current_length + len(token) + len(closing_text()) > maximum:
                flush()
            if current_length + len(token) + len(closing_text()) > maximum:
                raise ValueError("one Telegram HTML entity exceeds the safe limit")
            current.append(token)
            current_length += len(token)
            continue

        remainder = token
        while remainder:
            available = maximum - current_length - len(closing_text())
            if available <= 0:
                flush()
                available = maximum - current_length - len(closing_text())
            fragment = remainder[:available]
            current.append(fragment)
            current_length += len(fragment)
            remainder = remainder[len(fragment) :]
            if remainder:
                flush()

    if current:
        chunk = "".join(current) + closing_text()
        if chunk.strip():
            chunks.append(chunk)
    return chunks
