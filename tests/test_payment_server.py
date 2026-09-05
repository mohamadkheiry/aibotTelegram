from __future__ import annotations

import http.client
import json
import threading
import time
import unittest
from typing import Any

from app.payment_server import (
    ConfirmationOutcome,
    PaymentCallbackServer,
    PaymentConflictError,
    PaymentNotFoundError,
)


class PaymentCallbackServerTests(unittest.TestCase):
    OCCURRED_AT = "2026-09-04T20:40:00+03:30"

    def setUp(self) -> None:
        self.calls: list[tuple[int, str | None, str | None]] = []
        self.outcome: ConfirmationOutcome | str | None | BaseException = None
        self.server = PaymentCallbackServer(
            secret="a-long-test-payment-secret",
            on_confirm=self._on_confirm,
            host="127.0.0.1",
            port=0,
            max_body_bytes=256,
        )
        self.host, self.port = self.server.start()

    def tearDown(self) -> None:
        self.server.stop()

    def _on_confirm(
        self, amount: int, reference: str | None, occurred_at: str | None
    ) -> ConfirmationOutcome | str | None:
        self.calls.append((amount, reference, occurred_at))
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: bytes | str | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, Any], dict[str, str]]:
        connection = http.client.HTTPConnection(self.host, self.port, timeout=2)
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        encoded = response.read()
        response_headers = {name.casefold(): value for name, value in response.getheaders()}
        connection.close()
        return response.status, json.loads(encoded.decode("utf-8")), response_headers

    @staticmethod
    def _json_headers(*, bearer: bool = False) -> dict[str, str]:
        authentication = (
            {"Authorization": "Bearer a-long-test-payment-secret"}
            if bearer
            else {"X-Payment-Secret": "a-long-test-payment-secret"}
        )
        return {"Content-Type": "application/json", **authentication}

    @classmethod
    def _valid_body(
        cls,
        amount: int = 1,
        *,
        reference: str = "BANK-TEST-1",
        occurred_at: str | None = None,
    ) -> str:
        return json.dumps(
            {
                "amount": amount,
                "reference": reference,
                "occurred_at": occurred_at or cls.OCCURRED_AT,
            }
        )

    def test_health_requires_secret_and_accepts_both_authentication_forms(self) -> None:
        status, payload, headers = self._request("GET", "/health")
        self.assertEqual(status, 401)
        self.assertEqual(payload, {"status": "error", "error": "unauthorised"})
        self.assertEqual(headers["www-authenticate"], "Bearer")

        for auth_headers in (
            {"X-Payment-Secret": "a-long-test-payment-secret"},
            {"Authorization": "Bearer a-long-test-payment-secret"},
        ):
            with self.subTest(auth_headers=auth_headers):
                status, payload, _ = self._request(
                    "GET", "/health", headers=auth_headers
                )
                self.assertEqual(status, 200)
                self.assertEqual(payload, {"status": "ok"})

    def test_confirmation_calls_injected_callback_with_validated_values(self) -> None:
        occurred_at = "2026-09-04T20:40:00+03:30"
        body = json.dumps(
            {"amount": 125_000, "reference": "BANK-42", "occurred_at": occurred_at}
        )
        status, payload, _ = self._request(
            "POST", "/payments/card/confirm", body=body, headers=self._json_headers()
        )

        self.assertEqual(status, 200)
        self.assertEqual(payload, {"status": "confirmed"})
        self.assertEqual(self.calls, [(125_000, "BANK-42", occurred_at)])

    def test_bearer_authentication_is_accepted_for_confirmation(self) -> None:
        status, payload, _ = self._request(
            "POST",
            "/payments/card/confirm",
            body=self._valid_body(reference="BANK-BEARER-1"),
            headers=self._json_headers(bearer=True),
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "confirmed")

    def test_wrong_secret_is_rejected_without_invoking_callback(self) -> None:
        status, payload, _ = self._request(
            "POST",
            "/payments/card/confirm",
            body=self._valid_body(reference="BANK-WRONG-SECRET-1"),
            headers={
                "Content-Type": "application/json",
                "X-Payment-Secret": "wrong-secret",
            },
        )
        self.assertEqual(status, 401)
        self.assertEqual(payload["error"], "unauthorised")
        self.assertEqual(self.calls, [])

    def test_callback_outcomes_have_retry_friendly_status_codes(self) -> None:
        cases = (
            (ConfirmationOutcome.ALREADY_CONFIRMED, 200, "already_confirmed"),
            (ConfirmationOutcome.NOT_FOUND, 404, "not_found"),
            (ConfirmationOutcome.CONFLICT, 409, "conflict"),
            ("confirmed", 200, "confirmed"),
        )
        for outcome, expected_code, expected_status in cases:
            with self.subTest(outcome=outcome):
                self.outcome = outcome
                status, payload, _ = self._request(
                    "POST",
                    "/payments/card/confirm",
                    body=self._valid_body(
                        10,
                        reference=f"outcome-{expected_status}",
                    ),
                    headers=self._json_headers(),
                )
                self.assertEqual(status, expected_code)
                self.assertEqual(payload, {"status": expected_status})

    def test_callback_domain_exceptions_map_to_not_found_and_conflict(self) -> None:
        for exception, expected_code, expected_status in (
            (PaymentNotFoundError(), 404, "not_found"),
            (PaymentConflictError(), 409, "conflict"),
        ):
            with self.subTest(exception=type(exception).__name__):
                self.outcome = exception
                status, payload, _ = self._request(
                    "POST",
                    "/payments/card/confirm",
                    body=self._valid_body(
                        10,
                        reference=f"exception-{expected_status}",
                    ),
                    headers=self._json_headers(),
                )
                self.assertEqual(status, expected_code)
                self.assertEqual(payload, {"status": expected_status})

    def test_invalid_payloads_never_reach_callback(self) -> None:
        invalid_cases = (
            (
                '{"amount":0,"reference":"ref-0","occurred_at":"2026-09-04T20:40:00+03:30"}',
                "invalid_amount",
            ),
            (
                '{"amount":-1,"reference":"ref-neg","occurred_at":"2026-09-04T20:40:00+03:30"}',
                "invalid_amount",
            ),
            (
                '{"amount":true,"reference":"ref-bool","occurred_at":"2026-09-04T20:40:00+03:30"}',
                "invalid_amount",
            ),
            (
                '{"amount":"1","reference":"ref-text","occurred_at":"2026-09-04T20:40:00+03:30"}',
                "invalid_amount",
            ),
            (
                '{"reference":"ref-missing-amount","occurred_at":"2026-09-04T20:40:00+03:30"}',
                "missing_amount",
            ),
            (
                '{"amount":1,"occurred_at":"2026-09-04T20:40:00+03:30"}',
                "missing_reference",
            ),
            (
                '{"amount":1,"reference":"ref-missing-time"}',
                "missing_occurred_at",
            ),
            (
                '{"amount":1,"reference":"","occurred_at":"2026-09-04T20:40:00+03:30"}',
                "invalid_reference",
            ),
            (
                '{"amount":1,"reference":"ref-bad-time","occurred_at":"yesterday"}',
                "invalid_occurred_at",
            ),
            (
                '{"amount":1,"reference":"ref-naive-time","occurred_at":"2026-09-04T20:40:00"}',
                "invalid_occurred_at",
            ),
            (
                '{"amount":1,"reference":"ref-extra","occurred_at":"2026-09-04T20:40:00+03:30","extra":2}',
                "unknown_fields",
            ),
        )
        for body, expected_error in invalid_cases:
            with self.subTest(body=body):
                status, payload, _ = self._request(
                    "POST",
                    "/payments/card/confirm",
                    body=body,
                    headers=self._json_headers(),
                )
                self.assertEqual(status, 400)
                self.assertEqual(payload["error"], expected_error)
        self.assertEqual(self.calls, [])

    def test_json_is_strict_and_body_size_is_bounded(self) -> None:
        for body in (
            '[{"amount":1}]',
            '{"amount":1,"amount":2}',
            '{"amount":NaN}',
            '{not-json}',
        ):
            with self.subTest(body=body):
                status, payload, _ = self._request(
                    "POST",
                    "/payments/card/confirm",
                    body=body,
                    headers=self._json_headers(),
                )
                self.assertEqual(status, 400)
                self.assertIn(payload["error"], {"invalid_json", "invalid_payload"})

        oversized = b"{" + (b" " * 256) + b"}"
        status, payload, _ = self._request(
            "POST",
            "/payments/card/confirm",
            body=oversized,
            headers=self._json_headers(),
        )
        self.assertEqual(status, 413)
        self.assertEqual(payload["error"], "body_too_large")
        self.assertEqual(self.calls, [])

    def test_requires_json_content_type(self) -> None:
        status, payload, _ = self._request(
            "POST",
            "/payments/card/confirm",
            body=self._valid_body(reference="BANK-CONTENT-TYPE-1"),
            headers={"X-Payment-Secret": "a-long-test-payment-secret"},
        )
        self.assertEqual(status, 415)
        self.assertEqual(payload["error"], "unsupported_media_type")
        self.assertEqual(self.calls, [])

    def test_routing_and_method_errors_are_json(self) -> None:
        status, payload, headers = self._request("POST", "/health")
        self.assertEqual(status, 405)
        self.assertEqual(payload["error"], "method_not_allowed")
        self.assertEqual(headers["allow"], "GET")

        status, payload, _ = self._request("GET", "/missing")
        self.assertEqual(status, 404)
        self.assertEqual(payload["error"], "route_not_found")

    def test_lifecycle_methods_are_idempotent_and_allow_restart(self) -> None:
        first_address = self.server.start()
        self.assertEqual(first_address, (self.host, self.port))
        self.assertTrue(self.server.is_running)
        self.assertEqual(self.server.bound_address, first_address)

        self.server.stop()
        self.server.stop()
        self.assertFalse(self.server.is_running)
        self.assertIsNone(self.server.bound_address)

        self.host, self.port = self.server.start()
        self.assertTrue(self.server.is_running)
        status, payload, _ = self._request(
            "GET",
            "/health",
            headers={"X-Payment-Secret": "a-long-test-payment-secret"},
        )
        self.assertEqual((status, payload), (200, {"status": "ok"}))

    def test_stop_waits_for_an_inflight_confirmation_callback(self) -> None:
        callback_started = threading.Event()
        release_callback = threading.Event()
        client_finished = threading.Event()
        stop_finished = threading.Event()
        failures: list[BaseException] = []

        def blocking_confirmation(
            _amount: int, _reference: str, _occurred_at: str
        ) -> ConfirmationOutcome:
            callback_started.set()
            if not release_callback.wait(2):
                raise RuntimeError("test callback was not released")
            return ConfirmationOutcome.CONFIRMED

        self.server.stop()
        self.server = PaymentCallbackServer(
            secret="a-long-test-payment-secret",
            on_confirm=blocking_confirmation,
            host="127.0.0.1",
            port=0,
            max_body_bytes=256,
        )
        self.host, self.port = self.server.start()

        def request_confirmation() -> None:
            try:
                status, payload, _ = self._request(
                    "POST",
                    "/payments/card/confirm",
                    body=self._valid_body(reference="BANK-INFLIGHT-STOP"),
                    headers=self._json_headers(),
                )
                self.assertEqual((status, payload), (200, {"status": "confirmed"}))
            except BaseException as exc:  # surfaced on the main test thread below
                failures.append(exc)
            finally:
                client_finished.set()

        def stop_server() -> None:
            try:
                self.server.stop(timeout=2)
            except BaseException as exc:
                failures.append(exc)
            finally:
                stop_finished.set()

        client = threading.Thread(target=request_confirmation, name="payment-test-client")
        stopper = threading.Thread(target=stop_server, name="payment-test-stopper")
        try:
            client.start()
            self.assertTrue(callback_started.wait(1))
            stopper.start()
            time.sleep(0.1)
            self.assertFalse(stop_finished.is_set())
        finally:
            release_callback.set()
            client.join(3)
            if stopper.ident is not None:
                stopper.join(3)

        self.assertTrue(client_finished.is_set())
        self.assertTrue(stop_finished.is_set())
        self.assertEqual(failures, [])
        self.assertFalse(self.server.is_running)


if __name__ == "__main__":
    unittest.main()
