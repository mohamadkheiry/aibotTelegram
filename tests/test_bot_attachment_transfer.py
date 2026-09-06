from __future__ import annotations

import json
import unittest
from unittest.mock import Mock, patch

import requests

from tests import test_bot_identity_migration as fixture
from tools.transfer_ticket_attachments import download, transfer


class BotAttachmentTransferTests(unittest.TestCase):
    setUp = fixture.BotIdentityMigrationTests.setUp
    tearDown = fixture.BotIdentityMigrationTests.tearDown
    snapshot = fixture.BotIdentityMigrationTests.snapshot

    def fixtures(self):
        self.db.add_ticket_message(self.ticket["id"], "تصویر آزمایشی", sender_type="user",
                                   sender_id=self.user["id"], attachment_file_id="old-photo",
                                   attachment_kind="photo", idempotency_key="image:25")
        source, target = Mock(), Mock()
        source.call.side_effect = lambda method, *a, **kw: (
            {"id": 100} if method == "getMe" else {"file_path": "photos/file.jpg", "file_size": 7}
        )
        results = {"getMe": {"id": 200}, "getChat": {"type": "private"},
                   "sendPhoto": {"message_id": 101, "photo": [{"file_id": "new-photo"}]},
                   "getFile": {"file_path": "photos/new.jpg", "file_size": 7}}
        target.call.side_effect = lambda method, *a, **kw: results[method]
        return source, target, results

    def run_transfer(self, source, target):
        return transfer(source, target, source_token="synthetic-token", database=self.source,
                        directory=self.root / "assets", source_bot_id=100, target_bot_id=200)

    @patch("tools.transfer_ticket_attachments.download", return_value=(b"fixture", ".jpg"))
    def test_transfer_preserves_original_and_reuses_verified_mapping_without_resending(self, downloaded):
        source, target, _ = self.fixtures()
        before = self.snapshot(self.source)
        self.assertEqual(self.run_transfer(source, target)["attachment_count"], 1)
        self.assertEqual(self.run_transfer(source, target)["attachment_count"], 1)
        self.assertEqual(sum(call.args[0] == "sendPhoto" for call in target.call.call_args_list), 1)
        self.assertEqual(self.snapshot(self.source), before)
        mapping = json.loads((self.root / "assets" / "attachment-map.json").read_text())
        self.assertEqual(mapping, {"old-photo": "new-photo"})
        self.assertEqual(next((self.root / "assets").glob("*-original.jpg")).read_bytes(), b"fixture")
        downloaded.assert_called_once()

    @patch("tools.transfer_ticket_attachments.download", return_value=(b"fixture", ".jpg"))
    def test_ambiguous_upload_is_never_blindly_repeated(self, _downloaded):
        source, target, results = self.fixtures()

        def respond(method, *args, **kwargs):
            if method == "sendPhoto":
                raise RuntimeError("simulated ambiguous upload")
            return results[method]

        target.call.side_effect = respond
        with self.assertRaisesRegex(RuntimeError, "ambiguous"):
            self.run_transfer(source, target)
        with self.assertRaisesRegex(ValueError, "operator review"):
            self.run_transfer(source, target)
        self.assertEqual(sum(call.args[0] == "sendPhoto" for call in target.call.call_args_list), 1)

    @patch("tools.transfer_ticket_attachments.download", return_value=(b"fixture", ".jpg"))
    def test_verification_failure_resumes_the_saved_upload_without_a_second_message(self, _downloaded):
        source, target, results = self.fixtures()

        def respond(method, *args, **kwargs):
            if method == "getFile":
                raise RuntimeError("simulated verification outage")
            return results[method]

        target.call.side_effect = respond
        with self.assertRaisesRegex(RuntimeError, "verification"):
            self.run_transfer(source, target)
        target.call.side_effect = lambda method, *a, **kw: results[method]
        self.assertEqual(self.run_transfer(source, target)["attachment_count"], 1)
        self.assertEqual(sum(call.args[0] == "sendPhoto" for call in target.call.call_args_list), 1)

    def test_wrong_destination_identity_cannot_download_or_upload(self):
        source, target, results = self.fixtures()
        results["getMe"]["id"] = 201
        with patch("tools.transfer_ticket_attachments.download") as downloaded:
            with self.assertRaisesRegex(ValueError, "identity mismatch"):
                self.run_transfer(source, target)
            downloaded.assert_not_called()

    def test_download_rejects_unsafe_path_size_and_redacts_transport_credentials(self):
        for metadata in ({"file_path": "../../a.jpg", "file_size": 7},
                         {"file_path": "photos/a.jpg", "file_size": 30_000_000}):
            with patch("tools.transfer_ticket_attachments.requests.get") as get:
                with self.assertRaises(ValueError):
                    download("synthetic-token", metadata)
                get.assert_not_called()
        with patch("tools.transfer_ticket_attachments.requests.get", side_effect=requests.ConnectionError("sensitive URL")):
            with self.assertRaisesRegex(RuntimeError, "credential URL omitted") as caught:
                download("synthetic-token", {"file_path": "photos/a.jpg", "file_size": 7})
        self.assertNotIn("sensitive URL", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
