from __future__ import annotations

import threading
import io
import logging
import traceback
import unittest
from typing import Any
from urllib.parse import quote

import requests

from app.telegram import (
    TelegramAPIError,
    TelegramClient,
    TelegramRequestCancelled,
    TelegramTransportError,
)
from app.main import configure_logging


class _FailingSession:
    def __init__(self, token: str) -> None:
        self.headers: dict[str, str] = {}
        self.token = token

    def post(self, *_args: object, **_kwargs: object) -> object:
        raise requests.ConnectionError(
            f"connection failed for https://api.telegram.org/bot{self.token}/getMe"
        )


class _StopThenFailSession:
    def __init__(self, stop_event: threading.Event) -> None:
        self.headers: dict[str, str] = {}
        self.stop_event = stop_event
        self.calls = 0

    def post(self, *_args: object, **_kwargs: object) -> object:
        self.calls += 1
        self.stop_event.set()
        raise requests.ConnectionError("simulated shutdown during long poll")


class _SuccessfulResponse:
    status_code = 200
    headers: dict[str, str] = {}

    def __init__(self, result: Any) -> None:
        self.result = result

    def json(self) -> dict[str, Any]:
        return {"ok": True, "result": self.result}


class _DebugLoggingSuccessSession:
    def __init__(self) -> None:
        self.headers: dict[str, str] = {}

    def post(self, url: str, **_kwargs: object) -> _SuccessfulResponse:
        logging.getLogger("urllib3.connectionpool").debug("POST %s", url)
        return _SuccessfulResponse({"id": 1})


class _RateLimitedResponse:
    status_code = 429
    headers: dict[str, str] = {}

    def json(self) -> dict[str, Any]:
        return {
            "ok": False,
            "error_code": 429,
            "description": "rate limited",
            "parameters": {"retry_after": 3_600},
        }


class _RateLimitedSession:
    def __init__(self) -> None:
        self.headers: dict[str, str] = {}
        self.calls = 0

    def post(self, *_args: object, **_kwargs: object) -> _RateLimitedResponse:
        self.calls += 1
        return _RateLimitedResponse()


class _APIErrorResponse:
    status_code = 400
    headers: dict[str, str] = {}

    def __init__(self, body: dict[str, Any]) -> None:
        self.body = body

    def json(self) -> dict[str, Any]:
        return self.body


class _APIErrorSession:
    def __init__(self, body: dict[str, Any]) -> None:
        self.headers: dict[str, str] = {}
        self.body = body

    def post(self, *_args: object, **_kwargs: object) -> _APIErrorResponse:
        return _APIErrorResponse(self.body)


class _StopDuringWait:
    def __init__(self) -> None:
        self.stopped = False
        self.waits: list[float] = []

    def is_set(self) -> bool:
        return self.stopped

    def wait(self, delay: float) -> bool:
        self.waits.append(delay)
        self.stopped = True
        return True


class _RecordingStop:
    def __init__(self) -> None:
        self.stopped = False
        self.waits: list[float] = []

    def is_set(self) -> bool:
        return self.stopped

    def set(self) -> None:
        self.stopped = True

    def wait(self, delay: float) -> bool:
        self.waits.append(delay)
        return self.stopped


class _StopThenRespondSession:
    def __init__(self, stop_event: threading.Event, result: Any) -> None:
        self.headers: dict[str, str] = {}
        self.stop_event = stop_event
        self.result = result
        self.calls = 0

    def post(self, *_args: object, **_kwargs: object) -> _SuccessfulResponse:
        self.calls += 1
        self.stop_event.set()
        return _SuccessfulResponse(self.result)


class TelegramSecurityTests(unittest.TestCase):
    def test_backoff_saturates_for_unbounded_handler_retries(self) -> None:
        client = TelegramClient("123456:test-token")
        self.assertEqual(client._backoff(100_000), client.max_retry_delay)

    def test_handler_nack_preserves_offset_and_retries_before_later_batch_update(
        self,
    ) -> None:
        stop_event = _RecordingStop()
        sleeps: list[float] = []
        client = TelegramClient(
            "123456:test-token",
            retry_backoff=0.25,
            max_retry_delay=2,
            sleep=sleeps.append,
        )
        update = {"update_id": 50, "message": {"text": "retry"}}
        later = {"update_id": 51, "message": {"text": "later"}}
        requested_offsets: list[int | None] = []

        def get_updates(**kwargs: Any) -> list[dict[str, Any]]:
            requested_offsets.append(kwargs.get("offset"))
            return [update, later]

        client.get_updates = get_updates  # type: ignore[method-assign]
        attempts: list[int] = []
        saved_offsets: list[int] = []
        pre_commit_snapshots: list[tuple[int | None, list[int]]] = []
        committed = 0

        def handler(item: dict[str, Any]) -> bool | None:
            nonlocal committed
            update_id = int(item["update_id"])
            attempts.append(update_id)
            if update_id == 50 and attempts.count(50) == 1:
                return False
            if update_id == 50:
                pre_commit_snapshots.append(
                    (client.last_update_offset, list(saved_offsets))
                )
                committed += 1
            if update_id == 51:
                stop_event.set()
            return None

        offset = client.run_polling(
            handler,
            offset=50,
            timeout=0,
            stop_event=stop_event,
            save_offset=saved_offsets.append,
        )

        self.assertEqual(attempts, [50, 50, 51])
        self.assertEqual(requested_offsets, [50, 50])
        self.assertEqual(pre_commit_snapshots, [(50, [])])
        self.assertEqual(committed, 1)
        self.assertEqual(saved_offsets, [51, 52])
        self.assertEqual(offset, 52)
        self.assertEqual(client.last_update_offset, 52)
        self.assertEqual(stop_event.waits, [0.25])
        self.assertEqual(sleeps, [])

    def test_transport_errors_never_expose_the_bot_token(self) -> None:
        for token in (
            "123456:very-secret-token",
            "123456:top+secret/key==",
            "123456:کلید-محرمانه",
        ):
            with self.subTest(token=token):
                encoded = quote(token, safe="")
                client = TelegramClient(
                    token,
                    session=_FailingSession(encoded),  # type: ignore[arg-type]
                    max_retries=0,
                )
                with self.assertRaises(TelegramTransportError) as caught:
                    client.call("getMe")
                message = str(caught.exception)
                self.assertEqual(
                    message, "Telegram transport error in getMe: request failed"
                )
                self.assertIsNone(caught.exception.__cause__)
                self.assertIsNone(caught.exception.__context__)
                rendered_traceback = "".join(
                    traceback.format_exception(caught.exception)
                )
                self.assertNotIn(token, rendered_traceback)
                self.assertNotIn(encoded, rendered_traceback)

    def test_debug_logging_clamps_successful_transport_request_urls(self) -> None:
        token = "123456:successful-debug-secret"
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        root_logger = logging.getLogger()
        old_root_level = root_logger.level
        transport_logger = logging.getLogger("urllib3")
        old_transport_level = transport_logger.level
        root_logger.addHandler(handler)
        root_logger.setLevel(logging.DEBUG)
        try:
            configure_logging("DEBUG")
            client = TelegramClient(
                token,
                session=_DebugLoggingSuccessSession(),  # type: ignore[arg-type]
                max_retries=0,
            )
            self.assertEqual(client.call("getMe"), {"id": 1})
        finally:
            root_logger.removeHandler(handler)
            root_logger.setLevel(old_root_level)
            transport_logger.setLevel(old_transport_level)
        self.assertNotIn(token, stream.getvalue())

    def test_api_error_payload_never_exposes_bot_token(self) -> None:
        token = "123456:top+secret/key == کلید"
        encoded = quote(token, safe="")
        client = TelegramClient(
            token,
            session=_APIErrorSession(
                {
                    "ok": False,
                    "error_code": 400,
                    "description": f"bad token {token}; encoded={encoded}",
                    "parameters": {
                        "retry_after": 4,
                        "untrusted": f"{token}:{encoded}",
                    },
                }
            ),  # type: ignore[arg-type]
            max_retries=0,
        )
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        logger = logging.getLogger("test.telegram.secret")
        logger.addHandler(handler)
        logger.setLevel(logging.ERROR)
        try:
            with self.assertRaises(TelegramAPIError) as caught:
                client.call("getMe")
            try:
                raise caught.exception
            except TelegramAPIError:
                logger.exception("telegram operation failed")
        finally:
            logger.removeHandler(handler)

        rendered = "".join(traceback.format_exception(caught.exception))
        self.assertEqual(caught.exception.description, "Bot API request failed")
        self.assertEqual(caught.exception.parameters, {"retry_after": 4.0})
        self.assertIsNone(caught.exception.__cause__)
        self.assertIsNone(caught.exception.__context__)
        for secret in (token, encoded):
            self.assertNotIn(secret, rendered)
            self.assertNotIn(secret, stream.getvalue())

    def test_polling_does_not_retry_or_back_off_after_shutdown(self) -> None:
        stop_event = threading.Event()
        session = _StopThenFailSession(stop_event)
        sleeps: list[float] = []
        client = TelegramClient(
            "123456:test-token",
            session=session,  # type: ignore[arg-type]
            max_retries=3,
            retry_backoff=10,
            sleep=sleeps.append,
        )

        offset = client.run_polling(
            lambda _update: self.fail("a cancelled poll must not dispatch an update"),
            offset=42,
            timeout=0,
            stop_event=stop_event,
        )

        self.assertEqual(offset, 42)
        self.assertEqual(session.calls, 1)
        self.assertEqual(sleeps, [])

    def test_bound_shutdown_event_cancels_api_transport_retries(self) -> None:
        stop_event = threading.Event()
        session = _StopThenFailSession(stop_event)
        sleeps: list[float] = []
        client = TelegramClient(
            "123456:test-token",
            session=session,  # type: ignore[arg-type]
            max_retries=3,
            retry_backoff=10,
            sleep=sleeps.append,
        )
        client.set_stop_event(stop_event)

        with self.assertRaises(TelegramRequestCancelled):
            client.call("getMe")

        self.assertEqual(session.calls, 1)
        self.assertEqual(sleeps, [])

    def test_shutdown_interrupts_a_server_provided_retry_after_wait(self) -> None:
        stop_event = _StopDuringWait()
        session = _RateLimitedSession()
        client = TelegramClient(
            "123456:test-token",
            session=session,  # type: ignore[arg-type]
            max_retries=3,
        )
        client.set_stop_event(stop_event)

        with self.assertRaises(TelegramRequestCancelled):
            client.call("sendMessage", {"chat_id": 1, "text": "test"})

        self.assertEqual(session.calls, 1)
        self.assertEqual(stop_event.waits, [3_600])

    def test_update_returned_during_shutdown_is_left_for_restart(self) -> None:
        stop_event = threading.Event()
        session = _StopThenRespondSession(stop_event, [{"update_id": 77, "message": {}}])
        handled: list[int] = []
        saved_offsets: list[int] = []
        client = TelegramClient(
            "123456:test-token",
            session=session,  # type: ignore[arg-type]
            max_retries=3,
        )

        offset = client.run_polling(
            lambda update: handled.append(int(update["update_id"])),
            offset=42,
            timeout=0,
            stop_event=stop_event,
            save_offset=saved_offsets.append,
        )

        self.assertEqual(offset, 42)
        self.assertEqual(session.calls, 1)
        self.assertEqual(handled, [])
        self.assertEqual(saved_offsets, [])


if __name__ == "__main__":
    unittest.main()
