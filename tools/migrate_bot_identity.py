"""Offline, loss-preserving bot-identity migration for the pre-sales dataset.

This intentionally refuses financial/inventory/provider/broadcast data, active
effects and unconverted attachments. Such data needs a separately reviewed
migration. Stop the source poller first. Neither a token nor network is used.
The complete original database is retained as an immutable archive, including
old update journals, input states, message identifiers and idempotency keys.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path


COPY_TABLES = {
    "schema_meta", "users", "admins", "settings", "user_states", "categories",
    "faq_categories", "faqs", "tickets", "ticket_messages", "outbound_messages",
    "outbound_message_attempts", "processed_admin_updates", "backups",
}
RUNTIME_TABLES = {"user_states", "processed_admin_updates"}


def table_names(connection):
    return [row[0] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    )]


def quoted(name):
    return '"' + name.replace('"', '""') + '"'


def rows(connection, table):
    return [dict(row) for row in connection.execute(f"SELECT * FROM {quoted(table)} ORDER BY rowid")]


def integrity(connection):
    if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        raise ValueError("Database integrity check failed")
    if connection.execute("PRAGMA foreign_key_check").fetchall():
        raise ValueError("Database foreign-key check failed")


def exclusive_database(path):
    if path.exists():
        raise ValueError("Refusing to overwrite a database or archive")
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    result = sqlite3.connect(path)
    result.row_factory = sqlite3.Row
    return result


def migrate(source: Path, target: Path, archive: Path, *, source_bot_id: int,
            target_bot_id: int, source_username: str, target_username: str,
            attachment_map: dict[str, str] | None = None) -> dict:
    source, target, archive = (p.resolve() for p in (source, target, archive))
    if len({source, target, archive}) != 3 or not source.is_file():
        raise ValueError("Source, destination and archive must be distinct files")
    if source_bot_id <= 0 or target_bot_id <= 0 or source_bot_id == target_bot_id:
        raise ValueError("Two different positive bot identities are required")
    if not source_username or not target_username or source_username.casefold() == target_username.casefold():
        raise ValueError("Two different bot usernames are required")
    if target.exists() or archive.exists():
        raise ValueError("Refusing to overwrite a database or archive")
    attachment_map = attachment_map or {}
    with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as origin:
        origin.row_factory = sqlite3.Row
        integrity(origin)
        version = origin.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()
        if not version or str(version[0]) != "11":
            raise ValueError("Only the reviewed schema 11 is supported")
        identity = origin.execute("SELECT value_json FROM settings WHERE key='bot_username'").fetchone()
        if not identity or str(json.loads(identity[0])).casefold() != source_username.casefold():
            raise ValueError("Source database bot username does not match")
        before = {name: rows(origin, name) for name in table_names(origin)}
        unsupported = [name for name, values in before.items() if values and name not in COPY_TABLES]
        if unsupported:
            raise ValueError("Dedicated migration required for nonempty tables: " + ", ".join(unsupported))
        if any(row["status"] != "completed" for row in before["processed_admin_updates"]):
            raise ValueError("Unfinished admin effects must be resolved on the original bot")
        if any(row["status"] in {"queued", "sending"} for row in before["outbound_messages"]):
            raise ValueError("Pending delivery must be resolved on the original bot")
        required_files = {row["attachment_file_id"] for row in before["ticket_messages"]
                          if row["attachment_file_id"]}
        if set(attachment_map) != required_files or any(
            not isinstance(value, str) or not value.strip() or value == key
            for key, value in attachment_map.items()
        ):
            raise ValueError("Every attachment must have a verified new-bot file ID, and no unrelated mapping")
        # The source poller must already be stopped: do not copy its WAL by hand.
        backup = exclusive_database(archive)
        try:
            origin.backup(backup)
            backup.execute("PRAGMA journal_mode=DELETE")
            integrity(backup)
            if {name: rows(backup, name) for name in before} != before:
                raise ValueError("Source changed while being archived; do not start the destination")
            destination = exclusive_database(target)
            try:
                backup.backup(destination)
                destination.execute("PRAGMA foreign_keys=ON")
                prefix = f"legacy-bot:{source_bot_id}:"
                with destination:
                    for name in RUNTIME_TABLES:
                        destination.execute(f"DELETE FROM {quoted(name)}")
                    # These are incoming Telegram event keys, not domain outbox
                    # keys. Keep the latter unchanged to prevent old alert replay.
                    for name, values in before.items():
                        if name == "outbound_messages" or name in RUNTIME_TABLES or not values:
                            continue
                        if "idempotency_key" in values[0]:
                            destination.execute(
                                f"UPDATE {quoted(name)} SET idempotency_key = ? || idempotency_key",
                                (prefix,),
                            )
                    for old_id, new_id in attachment_map.items():
                        destination.execute(
                            "UPDATE ticket_messages SET attachment_file_id=? WHERE attachment_file_id=?",
                            (new_id, old_id),
                        )
                    destination.execute("UPDATE outbound_messages SET telegram_message_id=NULL")
                    destination.execute("DELETE FROM settings WHERE key='telegram_update_offset'")
                    destination.execute("UPDATE settings SET value_json=? WHERE key='bot_username'",
                                        (json.dumps(target_username),))
                expected = json.loads(json.dumps(before))
                for name in RUNTIME_TABLES:
                    expected[name] = []
                for name, values in expected.items():
                    for row in values:
                        if name != "outbound_messages" and row.get("idempotency_key") is not None:
                            row["idempotency_key"] = prefix + row["idempotency_key"]
                        if name == "ticket_messages" and row["attachment_file_id"]:
                            row["attachment_file_id"] = attachment_map[row["attachment_file_id"]]
                        if name == "outbound_messages":
                            row["telegram_message_id"] = None
                expected["settings"] = [row for row in expected["settings"] if row["key"] != "telegram_update_offset"]
                for row in expected["settings"]:
                    if row["key"] == "bot_username":
                        row["value_json"] = json.dumps(target_username)
                after = {name: rows(destination, name) for name in before}
                if after != expected:
                    raise ValueError("Destination differs from the explicit migration plan")
                integrity(destination)
                destination.execute("PRAGMA journal_mode=DELETE")
            finally:
                destination.close()
        finally:
            backup.close()
    return {
        "result": "verified", "source_bot_id": source_bot_id, "target_bot_id": target_bot_id,
        "target_username": target_username, "schema_version": 11,
        "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        "destination_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        "archived_counts": {name: len(values) for name, values in before.items()},
        "destination_counts": {name: len(values) for name, values in after.items()},
        "converted_attachments": len(attachment_map),
        "old_runtime_preserved_in_archive": True,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--source-bot-id", type=int, required=True)
    parser.add_argument("--target-bot-id", type=int, required=True)
    parser.add_argument("--source-username", required=True)
    parser.add_argument("--target-username", required=True)
    parser.add_argument("--attachment-map", type=Path)
    parser.add_argument("--source-stopped", action="store_true", required=True)
    args = parser.parse_args()
    mapping = json.loads(args.attachment_map.read_text(encoding="utf-8")) if args.attachment_map else {}
    report = migrate(args.source, args.target, args.archive, source_bot_id=args.source_bot_id,
                     target_bot_id=args.target_bot_id, source_username=args.source_username,
                     target_username=args.target_username, attachment_map=mapping)
    print(json.dumps(report))


if __name__ == "__main__":
    main()
