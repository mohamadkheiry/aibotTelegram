"""Small, dependency-light client for Telegram's HTTP Bot API.

The project intentionally uses ``getUpdates`` long polling instead of a
webhook.  This module keeps Telegram-specific HTTP concerns in one place and
does not read configuration or the bot token from disk.
"""

from __future__ import annotations

import io
import json
import re
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any, BinaryIO

import requests


JsonObject = dict[str, Any]
ChatId = int | str


class TelegramError(RuntimeError):
    """Base class for Telegram client errors."""


class TelegramTransportError(TelegramError):
    """Telegram could not be reached or returned an invalid response."""

    def __init__(self, method: str, message: str) -> None:
        super().__init__(f"Telegram transport error in {method}: {message}")
        self.method = method


class TelegramRequestCancelled(TelegramError):
    """A cooperative application shutdown cancelled a retryable request."""

    def __init__(self, method: str) -> None:
        super().__init__(f"Telegram request cancelled during shutdown: {method}")
        self.method = method


class TelegramAPIError(TelegramError):
    """The Bot API returned ``ok: false``."""

    def __init__(
        self,
        method: str,
        description: str,
        *,
        error_code: int | None = None,
        parameters: Mapping[str, Any] | None = None,
        status_code: int | None = None,
    ) -> None:
        code = error_code if error_code is not None else status_code
        prefix = f"Telegram API error {code}" if code is not None else "Telegram API error"
        super().__init__(f"{prefix} in {method}: {description}")
        self.method = method
        self.description = description
        self.error_code = error_code
        self.parameters = dict(parameters or {})
        self.status_code = status_code

    @property
    def retry_after(self) -> float | None:
        value = self.parameters.get("retry_after")
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    @property
    def migrate_to_chat_id(self) -> int | None:
        value = self.parameters.get("migrate_to_chat_id")
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None


_METHOD_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


def _safe_api_error_description(value: Any) -> str:
    """Return only locally-authored API error text.

    Bot API error descriptions are provider-controlled and can echo request
    values, including the token embedded in Telegram's request URL.  Keep the
    one semantic condition used by the UI and collapse every other response
    to a generic description.
    """

    normalized = str(value or "").strip().lower()
    if "message is not modified" in normalized:
        return "Bad Request: message is not modified"
    return "Bot API request failed"


def _safe_api_error_parameters(value: Any) -> dict[str, int | float]:
    """Allow only Telegram's documented numeric recovery parameters."""

    if not isinstance(value, Mapping):
        return {}
    safe: dict[str, int | float] = {}
    raw_retry_after = value.get("retry_after")
    try:
        if raw_retry_after is not None:
            parsed_retry_after = float(raw_retry_after)
            if parsed_retry_after >= 0:
                safe["retry_after"] = parsed_retry_after
    except (TypeError, ValueError, OverflowError):
        pass
    raw_migrate_to_chat_id = value.get("migrate_to_chat_id")
    try:
        if raw_migrate_to_chat_id is not None:
            safe["migrate_to_chat_id"] = int(raw_migrate_to_chat_id)
    except (TypeError, ValueError, OverflowError):
        pass
    return safe

# Retrying these methods after an ambiguous transport failure cannot create a
# second outgoing message/payment.  Rate-limit responses are safe to retry for
# every method because Telegram explicitly rejected the request.
_SAFE_TRANSIENT_RETRY_METHODS = {
    "answercallbackquery",
    "deletemessage",
    "editmessagecaption",
    "editmessagereplymarkup",
    "editmessagetext",
    "getchat",
    "getchatmember",
    "getfile",
    "getme",
    "getupdates",
}


def _clean_payload(payload: Mapping[str, Any] | None) -> JsonObject:
    return {key: value for key, value in (payload or {}).items() if value is not None}


def _theme_button_markup(markup: Any) -> Any:
    """Let the client choose matching button/text colors without mutating data.

    Normalize at the transport boundary so older outbox records and multipart
    messages receive the same policy. Keep labels, icons and actions intact;
    the Bot API does not expose a separate button text-color field.
    """
    if isinstance(markup, str):
        try:
            parsed = json.loads(markup)
        except (TypeError, ValueError):
            return markup
        return json.dumps(_theme_button_markup(parsed), ensure_ascii=False)
    if not isinstance(markup, Mapping):
        return markup
    result = dict(markup)
    for kind in ("keyboard", "inline_keyboard"):
        rows = result.get(kind)
        if not isinstance(rows, (list, tuple)):
            continue
        result[kind] = [
            [
                {key: value for key, value in button.items() if key != "style"}
                if isinstance(button, Mapping) else button
                for button in row
            ] if isinstance(row, (list, tuple)) else row
            for row in rows
        ]
    return result


def _form_payload(payload: Mapping[str, Any]) -> dict[str, str]:
    """Encode nested Bot API values for a multipart request."""

    encoded: dict[str, str] = {}
    for key, value in payload.items():
        if value is None:
            continue
        if isinstance(value, bool):
            encoded[key] = "true" if value else "false"
        elif isinstance(value, (dict, list, tuple)):
            encoded[key] = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        else:
            encoded[key] = str(value)
    return encoded


def _file_object(value: Any) -> BinaryIO | None:
    candidate = value
    if isinstance(value, tuple) and len(value) >= 2:
        candidate = value[1]
    return candidate if hasattr(candidate, "read") else None


class TelegramClient:
    """Synchronous requests-based Telegram Bot API client.

    ``max_retries`` counts retries after the initial request.  Ambiguous
    transport/5xx failures are retried only for idempotent methods by default;
    pass ``retry_transient=True`` to :meth:`call` when the caller has its own
    idempotency protection.  Bot API 429 responses always honor
    ``parameters.retry_after`` before retrying.
    """

    def __init__(
        self,
        token: str,
        *,
        api_base: str = "https://api.telegram.org",
        session: requests.Session | None = None,
        connect_timeout: float = 10.0,
        read_timeout: float = 45.0,
        max_retries: int = 3,
        retry_backoff: float = 0.75,
        max_retry_delay: float = 30.0,
        button_color_mode: str = "colored",
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        token = token.strip()
        if not token:
            raise ValueError("Telegram bot token must not be empty")
        if connect_timeout <= 0 or read_timeout <= 0:
            raise ValueError("request timeouts must be positive")
        if max_retries < 0:
            raise ValueError("max_retries must be zero or greater")
        if retry_backoff < 0 or max_retry_delay < 0:
            raise ValueError("retry delays must not be negative")
        if button_color_mode not in {"theme", "colored"}:
            raise ValueError("button_color_mode must be theme or colored")

        self._token = token
        self.api_base = api_base.rstrip("/")
        self.connect_timeout = float(connect_timeout)
        self.read_timeout = float(read_timeout)
        self.max_retries = int(max_retries)
        self.retry_backoff = float(retry_backoff)
        self.max_retry_delay = float(max_retry_delay)
        self.button_color_mode = button_color_mode
        self._sleep = sleep
        self._owns_session = session is None
        self.session = session or requests.Session()
        self.last_update_offset: int | None = None
        self._default_stop_event: Any | None = None

        headers = getattr(self.session, "headers", None)
        if headers is not None and hasattr(headers, "setdefault"):
            headers.setdefault("User-Agent", "alone-account-bot/1.0")

    def __repr__(self) -> str:
        return f"TelegramClient(api_base={self.api_base!r}, token=<redacted>)"

    def __enter__(self) -> TelegramClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        """Close a session created by this client."""

        if self._owns_session:
            self.session.close()

    def set_stop_event(self, stop_event: Any | None) -> None:
        """Bind the application shutdown event to every subsequent API call."""

        self._default_stop_event = stop_event

    def _url(self, method: str) -> str:
        if not _METHOD_NAME.fullmatch(method):
            raise ValueError(f"Invalid Bot API method name: {method!r}")
        return f"{self.api_base}/bot{self._token}/{method}"

    def _backoff(self, retry_number: int) -> float:
        # Handler NACK retries are intentionally unbounded in count. Cap the
        # exponent before arithmetic so a long outage cannot raise
        # ``OverflowError`` before the configured delay cap is applied.
        exponent = min(63, max(0, int(retry_number) - 1))
        delay = self.retry_backoff * (2**exponent)
        return min(delay, self.max_retry_delay)

    @staticmethod
    def _is_stopping(stop_event: Any | None) -> bool:
        return bool(stop_event is not None and stop_event.is_set())

    def _wait_before_retry(
        self,
        method: str,
        delay: float,
        stop_event: Any | None,
    ) -> None:
        """Wait for retry unless cooperative shutdown wins the race."""

        if self._is_stopping(stop_event):
            raise TelegramRequestCancelled(method)
        waiter = getattr(stop_event, "wait", None)
        if callable(waiter):
            if waiter(max(0.0, float(delay))):
                raise TelegramRequestCancelled(method)
            return
        self._sleep(delay)
        if self._is_stopping(stop_event):
            raise TelegramRequestCancelled(method)

    @staticmethod
    def _capture_file_positions(files: Mapping[str, Any] | None) -> dict[str, int | None]:
        positions: dict[str, int | None] = {}
        for key, value in (files or {}).items():
            stream = _file_object(value)
            if stream is None:
                continue
            try:
                positions[key] = int(stream.tell())
            except (AttributeError, OSError, TypeError, ValueError):
                positions[key] = None
        return positions

    @staticmethod
    def _rewind_files(
        method: str,
        files: Mapping[str, Any] | None,
        positions: Mapping[str, int | None],
    ) -> None:
        for key, position in positions.items():
            stream = _file_object((files or {}).get(key))
            if stream is None or position is None:
                raise TelegramTransportError(
                    method,
                    f"cannot safely retry non-seekable upload field {key!r}",
                )
            try:
                stream.seek(position)
            except (AttributeError, OSError, TypeError, ValueError) as exc:
                raise TelegramTransportError(
                    method,
                    f"cannot rewind upload field {key!r}",
                ) from exc

    @staticmethod
    def _response_body(response: requests.Response) -> JsonObject | None:
        try:
            body = response.json()
        except (requests.exceptions.JSONDecodeError, ValueError):
            return None
        return body if isinstance(body, dict) else None

    def call(
        self,
        method: str,
        payload: Mapping[str, Any] | None = None,
        *,
        files: Mapping[str, Any] | None = None,
        request_timeout: float | tuple[float, float] | None = None,
        retry_transient: bool | None = None,
        stop_event: Any | None = None,
    ) -> Any:
        """Call an arbitrary Bot API method and return its ``result`` value."""

        if stop_event is None:
            stop_event = self._default_stop_event
        url = self._url(method)
        clean = _clean_payload(payload)
        if "reply_markup" in clean:
            from .customer_layouts import clean_markup
            clean["reply_markup"] = clean_markup(clean["reply_markup"])
        if self.button_color_mode == "theme" and "reply_markup" in clean:
            clean["reply_markup"] = _theme_button_markup(clean["reply_markup"])
        timeout = request_timeout or (self.connect_timeout, self.read_timeout)
        method_key = method.lower()
        can_retry_transient = (
            method_key in _SAFE_TRANSIENT_RETRY_METHODS
            if retry_transient is None
            else bool(retry_transient)
        )
        positions = self._capture_file_positions(files)

        for attempt in range(self.max_retries + 1):
            if self._is_stopping(stop_event):
                raise TelegramRequestCancelled(method)
            if attempt:
                self._rewind_files(method, files, positions)
            transport_failed = False
            cancelled_after_transport = False
            try:
                if files:
                    response = self.session.post(
                        url,
                        data=_form_payload(clean),
                        files=files,
                        timeout=timeout,
                    )
                else:
                    response = self.session.post(url, json=clean, timeout=timeout)
            except requests.RequestException:
                if self._is_stopping(stop_event):
                    cancelled_after_transport = True
                elif can_retry_transient and attempt < self.max_retries:
                    self._wait_before_retry(
                        method,
                        self._backoff(attempt + 1),
                        stop_event,
                    )
                    continue
                else:
                    # ``requests`` includes the full request URL in some
                    # errors; Telegram embeds a possibly percent-encoded token
                    # in it. Raise only after the except scope is gone.
                    transport_failed = True
            if cancelled_after_transport:
                raise TelegramRequestCancelled(method)
            if transport_failed:
                raise TelegramTransportError(method, "request failed")

            body = self._response_body(response)
            status_code = int(getattr(response, "status_code", 0) or 0)
            if body is not None and body.get("ok") is True:
                return body.get("result")

            error_code: int | None = None
            description = "Telegram returned a non-JSON response"
            parameters: Mapping[str, Any] = {}
            if body is not None:
                try:
                    raw_code = body.get("error_code")
                    error_code = int(raw_code) if raw_code is not None else None
                except (TypeError, ValueError):
                    error_code = None
                description = _safe_api_error_description(body.get("description"))
                parameters = _safe_api_error_parameters(body.get("parameters"))

            is_rate_limited = status_code == 429 or error_code == 429
            is_server_error = status_code >= 500 or (
                error_code is not None and error_code >= 500
            )
            if attempt < self.max_retries and is_rate_limited:
                raw_retry_after = parameters.get("retry_after")
                if raw_retry_after is None:
                    raw_retry_after = getattr(response, "headers", {}).get("Retry-After")
                try:
                    retry_after = max(0.0, float(raw_retry_after))
                except (TypeError, ValueError):
                    retry_after = self._backoff(attempt + 1)
                # A server-provided flood-control delay must not be capped.
                self._wait_before_retry(
                    method,
                    max(retry_after, self._backoff(attempt + 1)),
                    stop_event,
                )
                continue
            if attempt < self.max_retries and is_server_error and can_retry_transient:
                self._wait_before_retry(
                    method,
                    self._backoff(attempt + 1),
                    stop_event,
                )
                continue

            if self._is_stopping(stop_event):
                raise TelegramRequestCancelled(method)

            raise TelegramAPIError(
                method,
                description,
                error_code=error_code,
                parameters=parameters,
                status_code=status_code or None,
            )

        raise AssertionError("unreachable")

    def get_updates(
        self,
        *,
        offset: int | None = None,
        timeout: int = 30,
        limit: int = 100,
        allowed_updates: Sequence[str] | None = None,
        stop_event: Any | None = None,
    ) -> list[JsonObject]:
        """Fetch one batch with Telegram ``getUpdates`` long polling."""

        if not 1 <= limit <= 100:
            raise ValueError("getUpdates limit must be between 1 and 100")
        if timeout < 0:
            raise ValueError("getUpdates timeout must not be negative")
        result = self.call(
            "getUpdates",
            {
                "offset": offset,
                "timeout": int(timeout),
                "limit": int(limit),
                "allowed_updates": list(allowed_updates) if allowed_updates is not None else None,
            },
            request_timeout=(
                self.connect_timeout,
                max(self.read_timeout, float(timeout) + 10.0),
            ),
            retry_transient=True,
            stop_event=stop_event,
        )
        if not isinstance(result, list):
            raise TelegramTransportError("getUpdates", "result is not an array")
        if not all(isinstance(update, dict) for update in result):
            raise TelegramTransportError("getUpdates", "result contains a non-object update")
        return result

    @staticmethod
    def offset_after(update: Mapping[str, Any], current: int | None = None) -> int:
        """Return the offset that acknowledges ``update``."""

        try:
            candidate = int(update["update_id"]) + 1
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("update is missing a valid update_id") from exc
        return candidate if current is None else max(current, candidate)

    def iter_updates(
        self,
        *,
        offset: int | None = None,
        timeout: int = 30,
        limit: int = 100,
        allowed_updates: Sequence[str] | None = None,
        stop_event: Any | None = None,
        idle_sleep: float = 0.1,
    ) -> Iterator[JsonObject]:
        """Yield updates continuously while maintaining the next offset.

        ``last_update_offset`` is updated before each yield.  For durable,
        handler-confirmed offsets use :meth:`run_polling` with ``save_offset``.
        """

        current = offset
        self.last_update_offset = current
        while stop_event is None or not stop_event.is_set():
            try:
                updates = self.get_updates(
                    offset=current,
                    timeout=timeout,
                    limit=limit,
                    allowed_updates=allowed_updates,
                    stop_event=stop_event,
                )
            except TelegramRequestCancelled:
                if self._is_stopping(stop_event):
                    return
                raise
            if self._is_stopping(stop_event):
                return
            if not updates and timeout == 0 and idle_sleep > 0:
                try:
                    self._wait_before_retry("getUpdates", idle_sleep, stop_event)
                except TelegramRequestCancelled:
                    return
            for update in updates:
                if self._is_stopping(stop_event):
                    return
                current = self.offset_after(update, current)
                self.last_update_offset = current
                yield update
                if stop_event is not None and stop_event.is_set():
                    return

    def run_polling(
        self,
        handler: Callable[[JsonObject], Any],
        *,
        offset: int | None = None,
        timeout: int = 30,
        limit: int = 100,
        allowed_updates: Sequence[str] | None = None,
        stop_event: Any | None = None,
        save_offset: Callable[[int], Any] | None = None,
        idle_sleep: float = 0.1,
    ) -> int | None:
        """Run polling and persist offsets only for updates the handler acknowledges.

        An explicit ``False`` result is a temporary negative acknowledgement.
        The current update and every later update in that fetched batch remain
        unacknowledged; polling retries from the unchanged offset after bounded
        exponential backoff. Every other return value, including ``None``, is
        an acknowledgement for backward compatibility.
        """

        current = offset
        self.last_update_offset = current
        handler_retry_number = 0
        while stop_event is None or not stop_event.is_set():
            try:
                updates = self.get_updates(
                    offset=current,
                    timeout=timeout,
                    limit=limit,
                    allowed_updates=allowed_updates,
                    stop_event=stop_event,
                )
            except TelegramRequestCancelled:
                if self._is_stopping(stop_event):
                    return current
                raise
            if self._is_stopping(stop_event):
                return current
            if not updates and timeout == 0 and idle_sleep > 0:
                try:
                    self._wait_before_retry("getUpdates", idle_sleep, stop_event)
                except TelegramRequestCancelled:
                    return current
            retry_current_update = False
            for update in updates:
                if self._is_stopping(stop_event):
                    return current
                handled = handler(update)
                if handled is False:
                    handler_retry_number += 1
                    retry_current_update = True
                    break
                handler_retry_number = 0
                current = self.offset_after(update, current)
                self.last_update_offset = current
                if save_offset is not None:
                    save_offset(current)
                if stop_event is not None and stop_event.is_set():
                    return current
            if retry_current_update:
                try:
                    self._wait_before_retry(
                        "updateHandler",
                        self._backoff(handler_retry_number),
                        stop_event,
                    )
                except TelegramRequestCancelled:
                    return current
        return current

    def send_message(
        self,
        chat_id: ChatId,
        text: str,
        *,
        parse_mode: str | None = "HTML",
        reply_markup: Mapping[str, Any] | None = None,
        **extra: Any,
    ) -> JsonObject:
        payload: JsonObject = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "reply_markup": reply_markup,
            **extra,
        }
        return self.call("sendMessage", payload)

    def edit_message_text(
        self,
        chat_id: ChatId,
        message_id: int,
        text: str,
        *,
        parse_mode: str | None = "HTML",
        reply_markup: Mapping[str, Any] | None = None,
        **extra: Any,
    ) -> JsonObject | bool:
        return self.call(
            "editMessageText",
            {
                "chat_id": chat_id,
                "message_id": message_id,
                "text": text,
                "parse_mode": parse_mode,
                "reply_markup": reply_markup,
                **extra,
            },
        )

    def edit_message_reply_markup(
        self,
        chat_id: ChatId,
        message_id: int,
        reply_markup: Mapping[str, Any] | None = None,
        **extra: Any,
    ) -> JsonObject | bool:
        return self.call(
            "editMessageReplyMarkup",
            {
                "chat_id": chat_id,
                "message_id": message_id,
                "reply_markup": reply_markup,
                **extra,
            },
        )

    def delete_message(self, chat_id: ChatId, message_id: int) -> bool:
        return bool(
            self.call(
                "deleteMessage",
                {"chat_id": chat_id, "message_id": int(message_id)},
            )
        )

    def answer_callback_query(
        self,
        callback_query_id: str,
        text: str | None = None,
        *,
        show_alert: bool = False,
        url: str | None = None,
        cache_time: int = 0,
    ) -> bool:
        return bool(
            self.call(
                "answerCallbackQuery",
                {
                    "callback_query_id": callback_query_id,
                    "text": text,
                    "show_alert": bool(show_alert),
                    "url": url,
                    "cache_time": int(cache_time),
                },
            )
        )

    def get_chat_member(self, chat_id: ChatId, user_id: int) -> JsonObject:
        result = self.call(
            "getChatMember",
            {"chat_id": chat_id, "user_id": int(user_id)},
        )
        if not isinstance(result, dict):
            raise TelegramTransportError("getChatMember", "result is not an object")
        return result

    def is_chat_member(self, chat_id: ChatId, user_id: int) -> bool:
        """Return whether Telegram reports the user as currently in the chat."""

        member = self.get_chat_member(chat_id, user_id)
        status = member.get("status")
        if status in {"creator", "administrator", "member"}:
            return True
        return status == "restricted" and bool(member.get("is_member"))

    def send_document(
        self,
        chat_id: ChatId,
        document: str | Path | bytes | bytearray | BinaryIO,
        *,
        filename: str | None = None,
        caption: str | None = None,
        parse_mode: str | None = "HTML",
        reply_markup: Mapping[str, Any] | None = None,
        **extra: Any,
    ) -> JsonObject:
        payload: JsonObject = {
            "chat_id": chat_id,
            "caption": caption,
            "parse_mode": parse_mode if caption is not None else None,
            "reply_markup": reply_markup,
            **extra,
        }
        if isinstance(document, str):
            payload["document"] = document
            return self.call("sendDocument", payload)

        if isinstance(document, Path):
            with document.open("rb") as stream:
                files = {"document": (filename or document.name, stream)}
                return self.call("sendDocument", payload, files=files)

        if isinstance(document, (bytes, bytearray)):
            with io.BytesIO(bytes(document)) as stream:
                files = {"document": (filename or "document.bin", stream)}
                return self.call("sendDocument", payload, files=files)

        inferred_name = Path(str(getattr(document, "name", "document.bin"))).name
        files = {"document": (filename or inferred_name, document)}
        return self.call("sendDocument", payload, files=files)

    def send_photo(
        self,
        chat_id: ChatId,
        photo: str,
        *,
        caption: str | None = None,
        parse_mode: str | None = "HTML",
        reply_markup: Mapping[str, Any] | None = None,
        **extra: Any,
    ) -> JsonObject:
        """Send an existing Telegram photo file id or a public photo URL."""

        if not isinstance(photo, str) or not photo.strip():
            raise TypeError("photo must be a Telegram file id or URL")
        result = self.call(
            "sendPhoto",
            {
                "chat_id": chat_id,
                "photo": photo,
                "caption": caption,
                "parse_mode": parse_mode if caption is not None else None,
                "reply_markup": reply_markup,
                **extra,
            },
        )
        if not isinstance(result, dict):
            raise TelegramTransportError("sendPhoto", "result is not an object")
        return result

    def copy_message(
        self,
        chat_id: ChatId,
        from_chat_id: ChatId,
        message_id: int,
        *,
        caption: str | None = None,
        parse_mode: str | None = "HTML",
        reply_markup: Mapping[str, Any] | None = None,
        **extra: Any,
    ) -> JsonObject:
        result = self.call(
            "copyMessage",
            {
                "chat_id": chat_id,
                "from_chat_id": from_chat_id,
                "message_id": int(message_id),
                "caption": caption,
                "parse_mode": parse_mode if caption is not None else None,
                "reply_markup": reply_markup,
                **extra,
            },
        )
        if not isinstance(result, dict):
            raise TelegramTransportError("copyMessage", "result is not an object")
        return result

    def forward_message(
        self,
        chat_id: ChatId,
        from_chat_id: ChatId,
        message_id: int,
        **extra: Any,
    ) -> JsonObject:
        result = self.call(
            "forwardMessage",
            {
                "chat_id": chat_id,
                "from_chat_id": from_chat_id,
                "message_id": int(message_id),
                **extra,
            },
        )
        if not isinstance(result, dict):
            raise TelegramTransportError("forwardMessage", "result is not an object")
        return result


# A concise alias for callers that prefer the service-oriented name.
TelegramAPI = TelegramClient


__all__ = [
    "ChatId",
    "JsonObject",
    "TelegramAPI",
    "TelegramAPIError",
    "TelegramClient",
    "TelegramError",
    "TelegramTransportError",
]
