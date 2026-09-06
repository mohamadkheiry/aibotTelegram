from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from app.db import Database
from tools.migrate_bot_identity import migrate, rows, table_names


class BotIdentityMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "source.sqlite3"
        self.target = self.root / "target.sqlite3"
        self.archive = self.root / "archive.sqlite3"
        self.db = Database(self.source)
        self.db.initialize()
        self.db.set_setting("bot_username", "source_fixture_bot")
        self.db.set_setting("bot_enabled", False)
        self.db.save_update_offset(9000)
        self.user = self.db.upsert_user(11001, 11001, username="fixture_owner")
        self.db.bootstrap_admin("fixture_owner", 11001, bootstrap_root=True)
        self.category = self.db.create_category("دستهٔ حفظ‌شده")
        self.db.set_user_state(self.user["id"], "admin:ui", {"prompt_message_id": 100})
        self.db.begin_admin_update(25, "synthetic-fingerprint")
        self.db.complete_admin_update(25)
        self.ticket = self.db.create_ticket(self.user["id"], "سابقهٔ تیکت", "متن اصلی",
                                           idempotency_key="ticket:1:25")

    def tearDown(self):
        self.temp.cleanup()

    def run_migration(self, **extra):
        return migrate(self.source, self.target, self.archive, source_bot_id=100,
                       target_bot_id=200, source_username="source_fixture_bot",
                       target_username="target_fixture_bot", **extra)

    def snapshot(self, path):
        with closing(sqlite3.connect(path)) as conn:
            conn.row_factory = sqlite3.Row
            return {name: rows(conn, name) for name in table_names(conn)}

    def test_full_business_history_and_original_runtime_are_preserved_without_replay(self):
        notice = self.db.queue_outbound_message("رسید قدیمی", recipient_user_id=self.user["id"],
                                               idempotency_key="ticket-message:1:admin:1")
        self.db.claim_outbound_message(notice["id"])
        self.db.mark_outbound_message(notice["id"], success=True, telegram_message_id=888)
        before = self.snapshot(self.source)
        result = self.run_migration()
        self.assertEqual(result["result"], "verified")
        self.assertEqual(self.snapshot(self.source), before)
        self.assertEqual(self.snapshot(self.archive), before)
        destination = Database(self.target)
        destination.initialize()
        self.assertEqual(destination.get_update_offset(0), 0)
        self.assertIsNone(destination.get_user_state(self.user["id"]))
        self.assertFalse(destination.get_setting("bot_enabled"))
        self.assertEqual(destination.get_user(self.user["id"]), self.user)
        self.assertEqual(destination.get_ticket(self.ticket["id"])["subject"], "سابقهٔ تیکت")
        self.assertEqual(destination.get_ticket(self.ticket["id"])["idempotency_key"], "legacy-bot:100:ticket:1:25")
        old_notice = destination.get_outbound_message_by_idempotency_key("ticket-message:1:admin:1")
        self.assertEqual(old_notice["status"], "sent")
        self.assertIsNone(old_notice["telegram_message_id"])
        self.assertTrue(destination.begin_admin_update(25, "new-bot-fingerprint")["should_process"])
        new_ticket = destination.create_ticket(self.user["id"], "تیکت تازه", "پیام تازه",
                                               idempotency_key="ticket:1:25")
        self.assertNotEqual(new_ticket["id"], self.ticket["id"])

    def test_attachment_mapping_is_required_and_only_changes_the_file_reference(self):
        message = self.db.add_ticket_message(self.ticket["id"], "تصویر قدیمی", sender_type="user",
                                             sender_id=self.user["id"], attachment_file_id="old-photo",
                                             attachment_kind="photo", idempotency_key="reply:25")
        with self.assertRaisesRegex(ValueError, "Every attachment"):
            self.run_migration()
        self.assertFalse(self.target.exists())
        self.assertFalse(self.archive.exists())
        self.run_migration(attachment_map={"old-photo": "new-photo"})
        destination = Database(self.target)
        migrated = destination.get_ticket_message(message["id"])
        self.assertEqual(migrated["attachment_file_id"], "new-photo")
        self.assertEqual(migrated["body"], message["body"])
        self.assertEqual(migrated["attachment_kind"], "photo")

    def test_unrelated_or_unchanged_attachment_ids_are_rejected(self):
        for mapping in ({"unrelated": "new"}, {"same": "same"}):
            with self.subTest(mapping=mapping), self.assertRaises(ValueError):
                self.run_migration(attachment_map=mapping)

    def test_unresolved_effect_and_outbox_are_rejected_before_creating_files(self):
        self.db.begin_admin_update(26, "incomplete")
        with self.assertRaisesRegex(ValueError, "Unfinished admin"):
            self.run_migration()
        self.db.complete_admin_update(26)
        self.db.queue_outbound_message("هنوز ارسال نشده", recipient_user_id=self.user["id"],
                                       idempotency_key="pending-fixture")
        with self.assertRaisesRegex(ValueError, "Pending delivery"):
            self.run_migration()
        self.assertFalse(self.archive.exists())
        self.assertFalse(self.target.exists())

    def test_financial_data_requires_a_dedicated_migration_and_is_never_modified(self):
        self.db.credit_wallet(self.user["id"], 500, reason="synthetic fixture", idempotency_key="wallet:25")
        before = self.snapshot(self.source)
        with self.assertRaisesRegex(ValueError, "wallet_entries"):
            self.run_migration()
        self.assertEqual(self.snapshot(self.source), before)
        self.assertFalse(self.target.exists())

    def test_destination_and_archive_are_never_overwritten(self):
        self.run_migration()
        before = (self.target.read_bytes(), self.archive.read_bytes())
        with self.assertRaisesRegex(ValueError, "overwrite"):
            self.run_migration()
        self.assertEqual((self.target.read_bytes(), self.archive.read_bytes()), before)

    def test_source_identity_and_aliased_paths_fail_closed(self):
        self.db.set_setting("bot_username", "another_bot")
        with self.assertRaisesRegex(ValueError, "does not match"):
            self.run_migration()
        with self.assertRaisesRegex(ValueError, "distinct"):
            migrate(self.source, self.source, self.archive, source_bot_id=100, target_bot_id=200,
                    source_username="source", target_username="target")
        self.assertFalse(self.target.exists())


if __name__ == "__main__":
    unittest.main()
