"""Small authenticated HTTP server for card-payment confirmations.

This module deliberately has no Telegram webhook integration.  It is intended to
run next to a bot that keeps receiving Telegram updates with ``getUpdates``.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import socket
import threading
import time
from collections.abc import Callable
from datetime import datetime
from enum import Enum
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Final, TypeAlias
from urllib.parse import urlsplit


DEFAULT_MAX_BODY_BYTES: Final = 4_096
_MAX_REFERENCE_LENGTH: Final = 256
_MAX_OCCURRED_AT_LENGTH: Final = 64
_PAYMENT_PATH: Final = "/payments/card/confirm"
_HEALTH_PATH: Final = "/health"


class ConfirmationOutcome(str, Enum):
    """Result returned by a payment confirmation callback."""

    CONFIRMED = "confirmed"
    ALREADY_CONFIRMED = "already_confirmed"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"


class PaymentNotFoundError(LookupError):
    """Raised by a callback when no pending payment matches the confirmation."""


class PaymentConflictError(RuntimeError):
    """Raised when a payment exists but cannot accept this confirmation."""


ConfirmationCallback: TypeAlias = Callable[
    [int, str, str], ConfirmationOutcome | str | None
]


class _DuplicateJsonKey(ValueError):
    pass


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey(key)
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant: {value}")


def _contains_control_character(value: str) -> bool:
    return any(ord(character) < 32 or ord(character) == 127 for character in value)


def _validate_payload(payload: Any) -> tuple[int, str, str]:
    if not isinstance(payload, dict):
        raise ValueError("invalid_payload")

    allowed_fields = {"amount", "reference", "occurred_at"}
    unknown_fields = set(payload) - allowed_fields
    if unknown_fields:
        raise ValueError("unknown_fields")
    if "amount" not in payload:
        raise ValueError("missing_amount")
    amount = payload["amount"]
    if isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0:
        raise ValueError("invalid_amount")

    if "reference" in payload:
        reference = payload["reference"]
        if (
            not isinstance(reference, str)
            or not reference
            or reference != reference.strip()
            or len(reference) > _MAX_REFERENCE_LENGTH
            or _contains_control_character(reference)
        ):
            raise ValueError("invalid_reference")
    else:
        reference = None

    if "occurred_at" in payload:
        occurred_at = payload["occurred_at"]
        if (
            not isinstance(occurred_at, str)
            or not occurred_at
            or occurred_at != occurred_at.strip()
            or len(occurred_at) > _MAX_OCCURRED_AT_LENGTH
            or _contains_control_character(occurred_at)
        ):
            raise ValueError("invalid_occurred_at")
        iso_value = occurred_at[:-1] + "+00:00" if occurred_at.endswith("Z") else occurred_at
        try:
            parsed_time = datetime.fromisoformat(iso_value)
        except ValueError as exc:
            raise ValueError("invalid_occurred_at") from exc
        if parsed_time.utcoffset() is None:
            raise ValueError("invalid_occurred_at")
    else:
        occurred_at = None

    if reference is None:
        raise ValueError("missing_reference")
    if occurred_at is None:
        raise ValueError("missing_occurred_at")

    return amount, reference, occurred_at


def _normalise_outcome(value: ConfirmationOutcome | str | None) -> ConfirmationOutcome:
    if value is None:
        return ConfirmationOutcome.CONFIRMED
    if isinstance(value, ConfirmationOutcome):
        return value
    if isinstance(value, str):
        try:
            return ConfirmationOutcome(value)
        except ValueError as exc:
            raise TypeError("callback returned an unsupported confirmation outcome") from exc
    raise TypeError("callback returned an unsupported confirmation outcome")


def _make_handler(
    *,
    secret: bytes,
    on_confirm: ConfirmationCallback,
    max_body_bytes: int,
    request_timeout: float,
) -> type[BaseHTTPRequestHandler]:
    expected_secret_digest = hashlib.sha256(secret).digest()

    class PaymentRequestHandler(BaseHTTPRequestHandler):
        server_version = "PaymentCallbackServer/1.0"

        def setup(self) -> None:
            super().setup()
            self.connection.settimeout(request_timeout)

        def log_message(self, format: str, *args: object) -> None:
            # The embedding application owns logging.  In particular, never log
            # authentication headers or payment payloads from this small server.
            return

        def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
            path = self._request_path()
            if path is None:
                return
            if path == _PAYMENT_PATH:
                self._method_not_allowed("POST")
                return
            if path != _HEALTH_PATH:
                self._send_json(404, {"status": "error", "error": "route_not_found"})
                return
            if not self._is_authenticated():
                self._unauthorised()
                return
            self._send_json(200, {"status": "ok"})

        def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
            path = self._request_path()
            if path is None:
                return
            if path == _HEALTH_PATH:
                self._method_not_allowed("GET")
                return
            if path != _PAYMENT_PATH:
                self._send_json(404, {"status": "error", "error": "route_not_found"})
                return
            if not self._is_authenticated():
                self._unauthorised()
                return

            body = self._read_json_body()
            if body is None:
                return
            try:
                payload = json.loads(
                    body.decode("utf-8"),
                    object_pairs_hook=_strict_object,
                    parse_constant=_reject_json_constant,
                )
            except (UnicodeDecodeError, json.JSONDecodeError, _DuplicateJsonKey, ValueError):
                self._send_json(400, {"status": "error", "error": "invalid_json"})
                return

            try:
                amount, reference, occurred_at = _validate_payload(payload)
            except ValueError as exc:
                self._send_json(400, {"status": "error", "error": str(exc)})
                return

            try:
                outcome = _normalise_outcome(on_confirm(amount, reference, occurred_at))
            except PaymentNotFoundError:
                outcome = ConfirmationOutcome.NOT_FOUND
            except PaymentConflictError:
                outcome = ConfirmationOutcome.CONFLICT
            except Exception:
                self._send_json(500, {"status": "error", "error": "internal_error"})
                return

            status_code = {
                ConfirmationOutcome.CONFIRMED: 200,
                ConfirmationOutcome.ALREADY_CONFIRMED: 200,
                ConfirmationOutcome.NOT_FOUND: 404,
                ConfirmationOutcome.CONFLICT: 409,
            }[outcome]
            self._send_json(status_code, {"status": outcome.value})

        def _request_path(self) -> str | None:
            try:
                return urlsplit(self.path).path
            except ValueError:
                self._send_json(400, {"status": "error", "error": "invalid_path"})
                return None

        def _is_authenticated(self) -> bool:
            direct_values = self.headers.get_all("X-Payment-Secret", [])
            authorisation_values = self.headers.get_all("Authorization", [])
            if len(direct_values) > 1 or len(authorisation_values) > 1:
                return False

            candidates: list[str] = []
            if direct_values:
                candidates.append(direct_values[0])
            if authorisation_values:
                scheme, separator, credential = authorisation_values[0].partition(" ")
                if (
                    separator
                    and scheme.casefold() == "bearer"
                    and credential
                    and credential == credential.strip()
                    and " " not in credential
                ):
                    candidates.append(credential)

            authenticated = False
            for candidate in candidates:
                candidate_digest = hashlib.sha256(candidate.encode("utf-8")).digest()
                # Use bitwise OR so every supplied credential is compared.
                authenticated = hmac.compare_digest(
                    candidate_digest, expected_secret_digest
                ) | authenticated
            return authenticated

        def _read_json_body(self) -> bytes | None:
            if self.headers.get("Transfer-Encoding") is not None:
                self._send_json(
                    400, {"status": "error", "error": "transfer_encoding_not_supported"}
                )
                return None
            if self.headers.get("Content-Encoding") not in (None, "", "identity"):
                self._send_json(
                    415, {"status": "error", "error": "content_encoding_not_supported"}
                )
                return None

            content_types = self.headers.get_all("Content-Type", [])
            if len(content_types) != 1:
                self._send_json(
                    415, {"status": "error", "error": "unsupported_media_type"}
                )
                return None
            media_type = content_types[0].partition(";")[0].strip().casefold()
            if media_type != "application/json":
                self._send_json(
                    415, {"status": "error", "error": "unsupported_media_type"}
                )
                return None

            length_values = self.headers.get_all("Content-Length", [])
            if len(length_values) != 1:
                self._send_json(411, {"status": "error", "error": "length_required"})
                return None
            raw_length = length_values[0]
            if not raw_length.isascii() or not raw_length.isdecimal():
                self._send_json(400, {"status": "error", "error": "invalid_content_length"})
                return None
            content_length = int(raw_length)
            if content_length > max_body_bytes:
                self._send_json(413, {"status": "error", "error": "body_too_large"})
                return None
            if content_length == 0:
                self._send_json(400, {"status": "error", "error": "empty_body"})
                return None

            try:
                body = self.rfile.read(content_length)
            except (TimeoutError, socket.timeout, OSError):
                self._send_json(408, {"status": "error", "error": "request_timeout"})
                return None
            if len(body) != content_length:
                self._send_json(400, {"status": "error", "error": "incomplete_body"})
                return None
            return body

        def _method_not_allowed(self, allowed_method: str) -> None:
            self._send_json(
                405,
                {"status": "error", "error": "method_not_allowed"},
                extra_headers={"Allow": allowed_method},
            )

        def _unauthorised(self) -> None:
            self._send_json(
                401,
                {"status": "error", "error": "unauthorised"},
                extra_headers={"WWW-Authenticate": "Bearer"},
            )

        def _send_json(
            self,
            status_code: int,
            payload: dict[str, Any],
            *,
            extra_headers: dict[str, str] | None = None,
        ) -> None:
            encoded = json.dumps(
                payload, ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            if extra_headers:
                for name, value in extra_headers.items():
                    self.send_header(name, value)
            self.end_headers()
            self.close_connection = True
            try:
                self.wfile.write(encoded)
            except (BrokenPipeError, ConnectionResetError, socket.timeout):
                pass

    return PaymentRequestHandler


class _PaymentHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = False
    # ``ThreadingMixIn.server_close`` otherwise waits without a timeout.  The
    # wrapper below performs an explicit, deadline-bound wait instead.
    block_on_close = False

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._request_condition = threading.Condition()
        self._active_requests = 0
        super().__init__(*args, **kwargs)

    def process_request(self, request: Any, client_address: Any) -> None:
        with self._request_condition:
            self._active_requests += 1
        try:
            super().process_request(request, client_address)
        except BaseException:
            with self._request_condition:
                self._active_requests -= 1
                self._request_condition.notify_all()
            raise

    def process_request_thread(self, request: Any, client_address: Any) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            with self._request_condition:
                self._active_requests -= 1
                self._request_condition.notify_all()

    def wait_for_requests(self, timeout: float) -> bool:
        with self._request_condition:
            return self._request_condition.wait_for(
                lambda: self._active_requests == 0,
                timeout=max(0.0, float(timeout)),
            )


class PaymentCallbackServer:
    """Authenticated callback server that runs in a background thread.

    ``start()`` returns the actual bound ``(host, port)`` pair, which is useful
    when ``port=0`` is used in tests.  Calling ``start()`` or ``stop()`` more than
    once is safe, and the instance can be used as a context manager.
    """

    def __init__(
        self,
        *,
        secret: str,
        on_confirm: ConfirmationCallback,
        host: str = "127.0.0.1",
        port: int = 0,
        max_body_bytes: int = DEFAULT_MAX_BODY_BYTES,
        request_timeout: float = 5.0,
    ) -> None:
        if not isinstance(secret, str) or not secret:
            raise ValueError("secret must be a non-empty string")
        if not callable(on_confirm):
            raise TypeError("on_confirm must be callable")
        if not isinstance(host, str) or not host:
            raise ValueError("host must be a non-empty string")
        if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65_535:
            raise ValueError("port must be an integer between 0 and 65535")
        if (
            isinstance(max_body_bytes, bool)
            or not isinstance(max_body_bytes, int)
            or max_body_bytes <= 0
        ):
            raise ValueError("max_body_bytes must be a positive integer")
        if (
            isinstance(request_timeout, bool)
            or not isinstance(request_timeout, (int, float))
            or request_timeout <= 0
        ):
            raise ValueError("request_timeout must be positive")

        self._host = host
        self._port = port
        self._secret = secret.encode("utf-8")
        self._on_confirm = on_confirm
        self._max_body_bytes = max_body_bytes
        self._request_timeout = float(request_timeout)
        self._server: _PaymentHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._lifecycle_lock = threading.Lock()

    @property
    def bound_address(self) -> tuple[str, int] | None:
        """Return the bound address while the server is running."""

        with self._lifecycle_lock:
            if self._server is None:
                return None
            host, port = self._server.server_address[:2]
            return str(host), int(port)

    @property
    def is_running(self) -> bool:
        with self._lifecycle_lock:
            return self._thread is not None and self._thread.is_alive()

    def start(self) -> tuple[str, int]:
        """Bind the listener and start serving in a non-daemon background thread."""

        with self._lifecycle_lock:
            if self._server is not None and self._thread is not None:
                if self._thread.is_alive():
                    host, port = self._server.server_address[:2]
                    return str(host), int(port)
                if not self._server.wait_for_requests(0):
                    raise RuntimeError("previous payment callbacks are still finishing")
                self._server.server_close()
                self._server = None
                self._thread = None

            handler = _make_handler(
                secret=self._secret,
                on_confirm=self._on_confirm,
                max_body_bytes=self._max_body_bytes,
                request_timeout=self._request_timeout,
            )
            server = _PaymentHTTPServer((self._host, self._port), handler)
            thread = threading.Thread(
                target=server.serve_forever,
                kwargs={"poll_interval": 0.05},
                name="payment-callback-server",
                daemon=False,
            )
            try:
                thread.start()
            except Exception:
                server.server_close()
                raise

            self._server = server
            self._thread = thread
            host, port = server.server_address[:2]
            return str(host), int(port)

    def stop(self, *, timeout: float = 5.0) -> None:
        """Stop serving, close the listening socket, and join the thread."""

        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
            raise ValueError("timeout must be positive")
        deadline = time.monotonic() + float(timeout)
        with self._lifecycle_lock:
            server = self._server
            thread = self._thread
            if server is None or thread is None:
                self._server = None
                self._thread = None
                return

            server.shutdown()
            server.server_close()
            thread.join(max(0.0, deadline - time.monotonic()))
            if thread.is_alive():
                raise RuntimeError("payment callback server did not stop in time")
            if not server.wait_for_requests(max(0.0, deadline - time.monotonic())):
                raise RuntimeError("payment callback requests did not finish in time")
            self._server = None
            self._thread = None

    def __enter__(self) -> PaymentCallbackServer:
        self.start()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.stop()


__all__ = [
    "ConfirmationCallback",
    "ConfirmationOutcome",
    "DEFAULT_MAX_BODY_BYTES",
    "PaymentCallbackServer",
    "PaymentConflictError",
    "PaymentNotFoundError",
]
