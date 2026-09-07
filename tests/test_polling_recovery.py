from __future__ import annotations

import unittest
from typing import Any

import requests

from app.telegram import TelegramAPIError, TelegramClient, TelegramTransportError


class Response:
    headers: dict[str, str] = {}

    def __init__(self, status: int, body: Any) -> None:
        self.status_code = status
        self.body = body

    def json(self) -> Any:
        return self.body


def failure(code: int, *, http_status: int | None = None, **parameters: Any) -> Response:
    return Response(http_status or code, {
        "ok": False, "error_code": code,
        "description": "synthetic upstream failure",
        "parameters": parameters,
    })


def success(*ids: int) -> Response:
    return Response(200, {"ok": True, "result": [{"update_id": item} for item in ids]})


class SequenceSession:
    def __init__(self, responses: list[Any]) -> None:
        self.headers: dict[str, str] = {}
        self.responses = iter(responses)
        self.payloads: list[dict[str, Any]] = []

    def post(self, _url: str, **kwargs: Any) -> Response:
        self.payloads.append(kwargs["json"])
        try:
            response = next(self.responses)
        except StopIteration:
            raise AssertionError("Unexpected extra request") from None
        if isinstance(response, Exception):
            raise response
        return response


class Stop:
    def __init__(self, *, stop_on_wait: bool = False) -> None:
        self.stopped = False
        self.stop_on_wait = stop_on_wait
        self.waits: list[float] = []

    def is_set(self) -> bool:
        return self.stopped

    def set(self) -> None:
        self.stopped = True

    def wait(self, delay: float) -> bool:
        self.waits.append(delay)
        if self.stop_on_wait:
            self.set()
        return self.stopped


class PollingRecoveryTests(unittest.TestCase):
    def client(self, responses: list[Any], **kwargs: Any) -> tuple[TelegramClient, SequenceSession]:
        session = SequenceSession(responses)
        client = TelegramClient(
            "123456:synthetic-test-token", session=session, retry_backoff=0.25,
            max_retry_delay=1, max_retries=kwargs.pop("max_retries", 0), **kwargs,
        )
        return client, session

    def test_exhausted_502_retries_resume_the_same_offset(self) -> None:
        client, session = self.client([failure(502)] * 4 + [success(50)], max_retries=1)
        stop = Stop()
        seen, saved = [], []

        def handle(update: dict[str, Any]) -> None:
            seen.append(update["update_id"])
            stop.set()

        result = client.run_polling(
            handle, offset=50, stop_event=stop, save_offset=saved.append,
            allowed_updates=["message", "callback_query"],
        )
        self.assertEqual(result, 51)
        self.assertEqual(seen, [50])
        self.assertEqual(saved, [51])
        self.assertEqual([item["offset"] for item in session.payloads], [50] * 5)
        self.assertTrue(all(item["allowed_updates"] == ["message", "callback_query"] for item in session.payloads))
        self.assertEqual(stop.waits, [0.25, 0.25, 0.25, 0.5])

    def test_network_server_and_malformed_batch_failures_recover(self) -> None:
        errors = [
            failure(500), failure(503), failure(504), failure(502, http_status=200),
            Response(502, None), requests.Timeout("synthetic timeout"),
            requests.ConnectionError("synthetic disconnect"),
            Response(200, {"ok": True, "result": "not an update array"}),
        ]
        for error in errors:
            with self.subTest(error=type(error).__name__, status=getattr(error, "status_code", None)):
                client, session = self.client([error, success(50)])
                stop = Stop()
                saved = []
                client.run_polling(lambda _: stop.set(), offset=50, stop_event=stop, save_offset=saved.append)
                self.assertEqual(saved, [51])
                self.assertEqual([item["offset"] for item in session.payloads], [50, 50])
                self.assertEqual(stop.waits, [0.25])

    def test_outage_backoff_is_capped_and_resets_after_a_valid_batch(self) -> None:
        client, _ = self.client([failure(502)] * 6 + [success(), failure(503), success(50)])
        stop = Stop()
        client.run_polling(lambda _: stop.set(), offset=50, stop_event=stop)
        self.assertEqual(stop.waits, [0.25, 0.5, 1, 1, 1, 1, 0.25])

    def test_exhausted_rate_limit_honors_retry_after_without_capping_it(self) -> None:
        client, _ = self.client([failure(429, retry_after=3600), success(50)])
        stop = Stop()
        client.run_polling(lambda _: stop.set(), offset=50, stop_event=stop)
        self.assertEqual(stop.waits, [3600])

    def test_http_only_rate_limit_preserves_delay_after_retry_exhaustion(self) -> None:
        response = Response(429, None)
        response.headers = {"Retry-After": "120"}
        client, _ = self.client([response, success(50)])
        stop = Stop()
        client.run_polling(lambda _: stop.set(), offset=50, stop_event=stop)
        self.assertEqual(stop.waits, [120])

    def test_invalid_retry_after_values_use_finite_backoff(self) -> None:
        for value in ("nan", "inf", "-1", "invalid"):
            for header_only in (False, True):
                with self.subTest(value=value, header_only=header_only):
                    response = failure(429, retry_after=value)
                    if header_only:
                        response = Response(429, None)
                        response.headers = {"Retry-After": value}
                    client, _ = self.client([response, response, success(50)], max_retries=1)
                    stop = Stop()
                    client.run_polling(lambda _: stop.set(), offset=50, stop_event=stop)
                    self.assertEqual(stop.waits, [0.25, 0.5])

    def test_recovery_logs_exclude_provider_text_and_credentials(self) -> None:
        response = failure(502)
        response.body["description"] = "private-provider-echo 123456:synthetic-test-token"
        client, _ = self.client([response, success(50)])
        stop = Stop()
        with self.assertLogs("app.telegram", level="WARNING") as captured:
            client.run_polling(lambda _: stop.set(), offset=50, stop_event=stop)
        rendered = "\n".join(captured.output)
        self.assertIn("Temporary getUpdates failure (502)", rendered)
        self.assertNotIn("private-provider-echo", rendered)
        self.assertNotIn("synthetic-test-token", rendered)

    def test_shutdown_interrupts_outer_recovery_without_acknowledging_updates(self) -> None:
        client, session = self.client([failure(502)])
        stop = Stop(stop_on_wait=True)
        seen, saved = [], []
        result = client.run_polling(seen.append, offset=50, stop_event=stop, save_offset=saved.append)
        self.assertEqual(result, 50)
        self.assertEqual(client.last_update_offset, 50)
        self.assertEqual((seen, saved), ([], []))
        self.assertEqual(len(session.payloads), 1)

    def test_authentication_conflict_and_invalid_request_errors_still_fail(self) -> None:
        for code in (400, 401, 403, 404, 409):
            with self.subTest(code=code):
                client, session = self.client([failure(code)])
                stop = Stop()
                with self.assertRaises(TelegramAPIError) as caught:
                    client.run_polling(lambda _: None, offset=50, stop_event=stop)
                self.assertEqual(caught.exception.error_code, code)
                self.assertEqual(stop.waits, [])
                self.assertEqual(len(session.payloads), 1)
                self.assertEqual(client.last_update_offset, 50)

    def test_handler_failure_is_not_misclassified_as_a_polling_outage(self) -> None:
        errors = [
            TelegramAPIError("sendMessage", "synthetic", error_code=503),
            TelegramTransportError("sendMessage", "synthetic"),
        ]
        for error in errors:
            with self.subTest(error=type(error).__name__):
                client, session = self.client([success(50)])
                stop, saved = Stop(), []

                def handle(_update: dict[str, Any]) -> None:
                    raise error

                with self.assertRaises(type(error)):
                    client.run_polling(handle, offset=50, stop_event=stop, save_offset=saved.append)
                self.assertEqual((saved, stop.waits), ([], []))
                self.assertEqual(len(session.payloads), 1)

    def test_outage_between_handler_nack_and_replay_does_not_skip_the_batch(self) -> None:
        client, session = self.client([success(50, 51), failure(502), success(50, 51)])
        stop, seen, saved = Stop(), [], []

        def handle(update: dict[str, Any]) -> bool:
            seen.append(update["update_id"])
            if len(seen) == 1:
                return False
            if update["update_id"] == 51:
                stop.set()
            return True

        result = client.run_polling(handle, offset=50, stop_event=stop, save_offset=saved.append)
        self.assertEqual(result, 52)
        self.assertEqual(seen, [50, 50, 51])
        self.assertEqual(saved, [51, 52])
        self.assertEqual([item["offset"] for item in session.payloads], [50, 50, 50])
        self.assertEqual(stop.waits, [0.25, 0.25])

    def test_iterator_uses_the_same_safe_recovery(self) -> None:
        client, session = self.client([failure(502), success(50)])
        stop = Stop()
        iterator = client.iter_updates(offset=50, stop_event=stop)
        self.assertEqual(next(iterator), {"update_id": 50})
        stop.set()
        self.assertEqual(list(iterator), [])
        self.assertEqual(client.last_update_offset, 51)
        self.assertEqual(len(session.payloads), 2)

    def test_outgoing_messages_still_do_not_retry_ambiguous_server_errors(self) -> None:
        client, session = self.client([failure(502)], max_retries=3)
        with self.assertRaises(TelegramAPIError):
            client.send_message(12345, "Synthetic test")
        self.assertEqual(len(session.payloads), 1)


if __name__ == "__main__":
    unittest.main()
