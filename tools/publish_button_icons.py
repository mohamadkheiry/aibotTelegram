"""Publish the licensed icon pack once, with an explicit bot and owner target.

Reads a private env file. Does not poll, send user messages, or open a bot DB.
An ambiguous create is recovered by the deterministic content-addressed name.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from contextlib import ExitStack
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.button_icons import ICON_SOURCES, validate_icon_ids  # noqa: E402
from app.config import _read_env_file  # noqa: E402
from app.telegram import TelegramAPIError, TelegramClient, TelegramError  # noqa: E402


class SafeDiagnosticsSession(requests.Session):
    """Report only a locally allowlisted error category, never provider text."""
    category = "unspecified"

    def post(self, *args, **kwargs):
        self.category = "unspecified"
        response = super().post(*args, **kwargs)
        try:
            description = str(response.json().get("description", "")).upper()
            for category in ("USER_ID_INVALID", "STICKERSET_INVALID", "STICKERSET_NAME_OCCUPIED",
                             "STICKER_CUSTOM_EMOJI_NOT_ALLOWED", "STICKER_PNG_DIMENSIONS",
                             "STICKER_PNG_NOPNG", "STICKER_EMOJI_INVALID", "PREMIUM_ACCOUNT_REQUIRED",
                             "PEER_ID_INVALID", "USER NOT FOUND", "STICKER_DOCUMENT_INVALID",
                             "STICKER_FORMAT_INVALID", "BOT WAS BLOCKED BY THE USER", "BOT CAN'T INITIATE CONVERSATION"):
                if category in description:
                    self.category = category
                    break
        except (ValueError, TypeError, AttributeError):
            pass
        return response


def publish(client, *, owner_id: int, bot_id: int, bot_username: str, asset_dir: Path) -> dict:
    if owner_id <= 0 or bot_id <= 0:
        raise ValueError("Expected positive owner and bot identifiers")
    identity = client.call("getMe")
    if identity.get("id") != bot_id or str(identity.get("username", "")).casefold() != bot_username.casefold():
        raise ValueError("Bot identity mismatch; no icon pack has been created")
    sources = json.loads((asset_dir / "sources.json").read_text(encoding="utf-8"))
    if set(sources["icons"]) != set(ICON_SOURCES) or sources.get("needs_repainting") is not True:
        raise ValueError("Unexpected icon sources")
    keys = list(ICON_SOURCES)
    digest = hashlib.sha256()
    for key in keys:
        item = sources["icons"][key]
        if item["webp"] != f"webp/{key}.webp" or item["lucide"] != ICON_SOURCES[key]:
            raise ValueError("Unexpected icon file path/source")
        content = (asset_dir / item["webp"]).read_bytes()
        if hashlib.sha256(content).hexdigest() != item["sha256"]:
            raise ValueError("Icon asset hash mismatch")
        digest.update(key.encode() + content)
    name = f"LucideMinimal{digest.hexdigest()[:8]}_by_{bot_username}"
    try:
        pack = client.call("getStickerSet", {"name": name}, retry_transient=True)
    except TelegramAPIError as exc:
        if exc.error_code != 400:
            raise
        with ExitStack() as stack:
            files = {key: (key + ".webp", stack.enter_context((asset_dir / "webp" / (key + ".webp")).open("rb")), "image/webp") for key in keys}
            # Emoji association is required pack metadata, not button label text.
            stickers = [{"sticker": "attach://" + key, "format": "static", "emoji_list": ["🔹"], "keywords": [key]} for key in keys]
            client.call("createNewStickerSet", {"user_id": owner_id, "name": name,
                        "title": "Eleven Accounts · Lucide Minimal", "sticker_type": "custom_emoji",
                        "needs_repainting": True, "stickers": stickers}, files=files)
        pack = client.call("getStickerSet", {"name": name}, retry_transient=True)
    stickers = pack.get("stickers", [])
    if pack.get("sticker_type") != "custom_emoji" or len(stickers) != len(keys):
        raise ValueError("Unexpected published pack shape; refusing an incorrect icon mapping")
    if any(sticker.get("width") != 100 or sticker.get("height") != 100 or not sticker.get("custom_emoji_id") or not sticker.get("needs_repainting") for sticker in stickers):
        raise ValueError("Published icons must be 100x100 and support adaptive repainting")
    icons = validate_icon_ids({key: str(sticker["custom_emoji_id"]) for key, sticker in zip(keys, stickers, strict=True)})
    verified = client.call("getCustomEmojiStickers", {"custom_emoji_ids": list(icons.values())}, retry_transient=True)
    if {str(sticker.get("custom_emoji_id")) for sticker in verified} != set(icons.values()):
        raise ValueError("Custom emoji ID verification failed")
    return {"version": 1, "pack_name": name, "bot_id": bot_id, "source_package": "lucide-static@1.41.0",
            "asset_sha256": digest.hexdigest(), "needs_repainting": True, "icons": icons}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", required=True)
    parser.add_argument("--owner-id", type=int, required=True)
    parser.add_argument("--bot-id", type=int, required=True)
    parser.add_argument("--bot-username", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    token = os.environ.get("BOT_TOKEN") or _read_env_file(Path(args.env_file)).get("BOT_TOKEN", "")
    with SafeDiagnosticsSession() as session, TelegramClient(token, session=session) as client:
        try:
            result = publish(client, owner_id=args.owner_id, bot_id=args.bot_id, bot_username=args.bot_username,
                             asset_dir=ROOT / "assets" / "button-icons")
        except TelegramError as exc:
            print(json.dumps({"result": "not_published", "method": getattr(exc, "method", "unknown"),
                              "error_code": getattr(exc, "error_code", None), "category": session.category}))
            return 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"result": "verified", "pack_name": result["pack_name"], "icon_count": len(result["icons"]), "output": str(args.output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
