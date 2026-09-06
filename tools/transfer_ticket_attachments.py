"""Re-upload old ticket attachments to their original sender on the new bot.

All outputs belong in an access-controlled deployment directory outside Git.
This does not poll or change either database. An ambiguous upload is journaled
and requires operator review rather than automatically sending the file again.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.config import _read_env_file  # noqa: E402
from app.telegram import TelegramClient, TelegramError  # noqa: E402

MAX_BYTES = 20 * 1024 * 1024


def download(token, metadata):
    file_path = metadata.get("file_path", "")
    if not re.fullmatch(r"[A-Za-z0-9_/-]+\.[A-Za-z0-9]+", file_path) or ".." in file_path:
        raise ValueError("Unsafe Telegram file path")
    if not 0 < int(metadata.get("file_size", 0)) <= MAX_BYTES:
        raise ValueError("Attachment size needs separate review")
    try:
        with requests.get("https://api.telegram.org/file/bot" + token + "/" + file_path,
                          timeout=(10, 45), stream=True, allow_redirects=False) as response:
            response.raise_for_status()
            if response.status_code != 200:
                raise ValueError("Unexpected attachment download status")
            chunks, size = [], 0
            for part in response.iter_content(65536):
                size += len(part)
                if size > MAX_BYTES:
                    raise ValueError("Attachment exceeds migration size limit")
                chunks.append(part)
            content = b"".join(chunks)
    except requests.RequestException:
        raise RuntimeError("Attachment download failed; credential URL omitted") from None
    if len(content) != int(metadata["file_size"]):
        raise ValueError("Attachment download length mismatch")
    return content, Path(file_path).suffix


def transfer(source, target, *, source_token, database, directory, source_bot_id, target_bot_id):
    if source_bot_id == target_bot_id:
        raise ValueError("Different bot identities are required")
    if source.call("getMe").get("id") != source_bot_id or target.call("getMe").get("id") != target_bot_id:
        raise ValueError("Bot identity mismatch")
    directory = directory.resolve()
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    journal_path = directory / "attachment-transfer-journal.json"
    journal = json.loads(journal_path.read_text(encoding="utf-8")) if journal_path.exists() else {
        "source_bot_id": source_bot_id, "target_bot_id": target_bot_id, "items": {},
    }
    if (journal.get("source_bot_id"), journal.get("target_bot_id")) != (source_bot_id, target_bot_id):
        raise ValueError("Attachment journal belongs to other bots")

    def save():
        journal_path.write_text(json.dumps(journal, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        attachments = [dict(row) for row in connection.execute(
            "SELECT m.id,m.attachment_file_id,m.attachment_kind,u.chat_id FROM ticket_messages m "
            "JOIN users u ON u.id=m.sender_user_id WHERE m.attachment_file_id IS NOT NULL ORDER BY m.id"
        )]
        total = connection.execute("SELECT count(*) FROM ticket_messages WHERE attachment_file_id IS NOT NULL").fetchone()[0]
        if total != len(attachments):
            raise ValueError("Admin-origin attachments need an explicitly chosen authorized recipient")
    mapping = {}
    for attachment in attachments:
        old_id = attachment["attachment_file_id"]
        prior = journal["items"].get(str(attachment["id"]))
        if prior:
            if prior["old_file_id"] != old_id or prior["status"] not in {"uploaded", "verified"}:
                raise ValueError("Prior upload is ambiguous or source changed; operator review required")
            target.call("getFile", {"file_id": prior["new_file_id"]})
            prior["status"] = "verified"
            save()
            mapping[old_id] = prior["new_file_id"]
            continue
        chat = target.call("getChat", {"chat_id": attachment["chat_id"]})
        if chat.get("type") != "private":
            raise ValueError("Original sender must first Start the target bot")
        metadata = source.call("getFile", {"file_id": old_id})
        content, suffix = download(source_token, metadata)
        local_file = directory / (f"ticket-message-{attachment['id']}-original" + suffix)
        if local_file.exists():
            if local_file.read_bytes() != content:
                raise ValueError("Archived attachment differs from source")
        else:
            with local_file.open("xb") as stream:
                stream.write(content)
        entry = {"old_file_id": old_id, "status": "upload_started", "sha256": hashlib.sha256(content).hexdigest(),
                 "size_bytes": len(content), "original_file": local_file.name}
        journal["items"][str(attachment["id"])] = entry
        save()
        kind = attachment["attachment_kind"]
        if kind not in {"photo", "document"}:
            raise ValueError("Unsupported ticket attachment kind")
        with local_file.open("rb") as stream:
            result = target.call("sendPhoto" if kind == "photo" else "sendDocument", {
                "chat_id": attachment["chat_id"],
                "caption": "این پیوست تیکت قبلی برای حفظ سوابق شما به ربات جدید منتقل شد؛ نیازی به ارسال دوبارهٔ آن نیست.",
                "disable_notification": True,
            }, files={kind: (local_file.name, stream)})
        new_id = result["photo"][-1]["file_id"] if kind == "photo" else result["document"]["file_id"]
        entry.update(status="uploaded", new_file_id=new_id, upload_message_id=result["message_id"])
        save()
        target.call("getFile", {"file_id": new_id})
        entry["status"] = "verified"
        save()
        mapping[old_id] = new_id
    (directory / "attachment-map.json").write_text(json.dumps(mapping) + "\n", encoding="utf-8")
    return {"result": "verified", "attachment_count": len(mapping)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-env", type=Path, required=True)
    parser.add_argument("--target-env", type=Path, required=True)
    parser.add_argument("--source-db", type=Path, required=True)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--source-bot-id", type=int, required=True)
    parser.add_argument("--target-bot-id", type=int, required=True)
    args = parser.parse_args()
    source_token = _read_env_file(args.source_env)["BOT_TOKEN"]
    target_token = _read_env_file(args.target_env)["BOT_TOKEN"]
    try:
        with TelegramClient(source_token, max_retries=1) as source, TelegramClient(target_token, max_retries=1) as target:
            result = transfer(source, target, source_token=source_token, database=args.source_db,
                              directory=args.directory, source_bot_id=args.source_bot_id,
                              target_bot_id=args.target_bot_id)
    except TelegramError as exc:
        print(json.dumps({"result": "not_transferred", "method": getattr(exc, "method", "unknown"),
                          "error_code": getattr(exc, "error_code", None)}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
