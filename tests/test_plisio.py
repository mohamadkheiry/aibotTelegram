import io
import logging
import traceback
import unittest
from urllib.parse import quote_plus
from unittest.mock import Mock

import requests

from app.plisio import PlisioClient, PlisioError


class _Response:
    def __init__(self, payload: dict, error: Exception | None = None) -> None:
        self.payload = payload
        self.error = error

    def raise_for_status(self) -> None:
        if self.error:
            raise self.error

    def json(self) -> dict:
        return self.payload


class PlisioTests(unittest.TestCase):
    def test_create_invoice_converts_toman_to_rial(self) -> None:
        session = Mock()
        session.get.return_value = _Response(
            {
                "status": "success",
                "data": {
                    "txn_id": "tx1",
                    "invoice_url": "https://pay.example.test/invoice/tx1",
                },
            }
        )
        client = PlisioClient("secret", session=session)
        invoice = client.create_invoice(
            order_number="AO-1", order_name="اشتراک", amount_in_shop_currency=125_000
        )
        self.assertEqual(invoice.transaction_id, "tx1")
        params = session.get.call_args.kwargs["params"]
        self.assertEqual(params["source_currency"], "IRR")
        self.assertEqual(params["source_amount"], 1_250_000)
        self.assertNotIn("secret", session.get.call_args.args[0])

    def test_create_invoice_rejects_an_unsafe_provider_url(self) -> None:
        session = Mock()
        session.get.return_value = _Response(
            {
                "status": "success",
                "data": {"txn_id": "tx1", "invoice_url": "javascript:alert(1)"},
            }
        )
        client = PlisioClient("secret", session=session)
        with self.assertRaises(PlisioError):
            client.create_invoice(
                order_number="AO-1",
                order_name="Subscription",
                amount_in_shop_currency=125_000,
            )

    def test_provider_error_is_raised(self) -> None:
        session = Mock()
        session.get.return_value = _Response(
            {"status": "error", "data": {"message": "bad invoice"}}
        )
        client = PlisioClient("secret", session=session)
        with self.assertRaises(PlisioError):
            client.operation("tx1")

    def test_provider_error_payload_never_exposes_api_key(self) -> None:
        api_key = "top+secret/key == کلید"
        encoded = quote_plus(api_key)
        session = Mock()
        session.get.return_value = _Response(
            {
                "status": "error",
                "data": {
                    "message": f"invalid api_key={api_key}; encoded={encoded}",
                    "name": api_key,
                },
            }
        )
        client = PlisioClient(api_key, session=session)
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        logger = logging.getLogger("test.plisio.secret")
        logger.addHandler(handler)
        logger.setLevel(logging.ERROR)
        try:
            with self.assertRaises(PlisioError) as caught:
                client.operation("x")
            try:
                raise caught.exception
            except PlisioError:
                logger.exception("provider operation failed")
        finally:
            logger.removeHandler(handler)

        rendered = "".join(traceback.format_exception(caught.exception))
        self.assertEqual(str(caught.exception), "Plisio rejected the request")
        self.assertIsNone(caught.exception.__cause__)
        self.assertIsNone(caught.exception.__context__)
        for secret in (api_key, encoded):
            self.assertNotIn(secret, rendered)
            self.assertNotIn(secret, stream.getvalue())

    def test_transport_error_redacts_query_string_api_key(self) -> None:
        for api_key in ("top-secret", "top+secret/key==", "space secret", "کلید-محرمانه"):
            with self.subTest(api_key=api_key):
                session = Mock()
                encoded = quote_plus(api_key)
                session.get.side_effect = requests.ConnectionError(
                    "failed https://api.plisio.net/api/v1/operations/x?api_key="
                    f"{encoded}"
                )
                client = PlisioClient(api_key, session=session)
                with self.assertRaises(PlisioError) as caught:
                    client.operation("x")
                rendered = str(caught.exception)
                rendered_traceback = "".join(
                    traceback.format_exception(caught.exception)
                )
                self.assertEqual(rendered, "Plisio request failed")
                self.assertIsNone(caught.exception.__cause__)
                self.assertIsNone(caught.exception.__context__)
                self.assertNotIn(api_key, rendered_traceback)
                self.assertNotIn(encoded, rendered_traceback)


if __name__ == "__main__":
    unittest.main()
