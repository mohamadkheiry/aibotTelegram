from __future__ import annotations

import ast
import copy
import hashlib
import io
import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from app.admin_forms import GROUPS, MAIN_GROUPS
from app.bot import BotApplication
from app.button_icons import GROUP_ICONS, ICON_SOURCES, apply_icons, icon_key, validate_icon_ids
from app.config import Settings, load_settings
from app.db import Database
from app.customer_layouts import SECTIONS, LayoutEngine, LayoutTelegram, defaults, keyboard
from app.keyboards import back_button, callback_button, contact_keyboard, inline_main_menu_keyboard, main_menu_keyboard
from app.telegram import TelegramAPIError, TelegramClient
from tests.test_bot import FakeTelegram
from tools.publish_button_icons import SafeDiagnosticsSession, publish

ROOT = Path(__file__).resolve().parents[1]
ICONS = {key: str(100000 + index) for index, key in enumerate(ICON_SOURCES)}


class ButtonIconTests(unittest.TestCase):
    def engine(self):
        db = Mock()
        db.get_setting.side_effect = lambda key, default=None: default
        return LayoutEngine(db, ICONS)

    def test_all_customer_templates_get_icons_without_changing_actions_or_labels(self):
        for section, spec in SECTIONS.items():
            with self.subTest(section=section):
                source = keyboard(section, [[callback_button(label, f"test:{index}")] for index, (_, label) in enumerate(spec.slots)])
                frozen = copy.deepcopy(source)
                result = self.engine().prepare(source)
                for before, after in zip(source["inline_keyboard"], result["inline_keyboard"], strict=True):
                    self.assertIn(after[0]["icon_custom_emoji_id"], ICONS.values())
                    self.assertEqual({k: v for k, v in after[0].items() if k != "icon_custom_emoji_id"}, before[0])
                self.assertEqual(source, frozen)

    def test_nine_admin_sections_use_distinct_semantic_icons(self):
        source = {"inline_keyboard": [[callback_button(GROUPS[group], "adm:ui:g:" + group)] for group in MAIN_GROUPS]}
        result = self.engine().prepare(source)
        self.assertEqual([row[0]["icon_custom_emoji_id"] for row in result["inline_keyboard"]], [ICONS[GROUP_ICONS[g]] for g in MAIN_GROUPS])

    def test_main_icons_explicit_overrides_and_contact_semantics_are_preserved(self):
        source = inline_main_menu_keyboard({"shop": "77777"}, include_admin=True)
        result = self.engine().prepare(source)
        self.assertEqual(result["inline_keyboard"][0][0]["icon_custom_emoji_id"], "77777")
        self.assertEqual(result["inline_keyboard"][-1][0]["icon_custom_emoji_id"], ICONS["settings"])
        contact = self.engine().prepare(contact_keyboard())
        self.assertEqual(contact["keyboard"][0][0]["icon_custom_emoji_id"], ICONS["phone"])
        self.assertTrue(contact["keyboard"][0][0]["request_contact"])

    def test_catalog_labels_cannot_impersonate_action_icons_and_back_metadata_wins(self):
        self.assertEqual(icon_key(callback_button("حذف", "prod:7")), "catalog")
        self.assertEqual(icon_key(callback_button("بازگشت", "cat:7")), "folder")
        self.assertEqual(icon_key(back_button("cat:7")), "back")

    def test_editor_and_form_navigation_have_clear_icons_without_callback_changes(self):
        expected = {"چیدمان دکمه‌های کاربران": "layout", "بازگشت به چیدمان قبلی": "refresh",
                    "تأیید انتشار": "check", "لغو تغییرات و بازگشت": "cancel", "حذف محصول": "delete",
                    "افزودن محصول در این دسته": "plus", "ویرایش مشخصات محصول": "pencil"}
        for label, key in expected.items():
            self.assertEqual(icon_key(callback_button(label, "adm:ui:f:token:1:select:0")), key)

    def test_glass_theme_keeps_icons_and_hides_no_action_in_json_multipart_or_edit(self):
        session = Mock()
        session.headers = {}
        session.post.return_value.status_code = 200
        session.post.return_value.json.return_value = {"ok": True, "result": {"message_id": 1}}
        client = LayoutTelegram(TelegramClient("test-token", session=session, button_color_mode="theme"), self.engine())
        source = keyboard("card_payment", [[{"text": "کپی مبلغ", "copy_text": {"text": "120000"}, "style": "primary"}],
                                           [callback_button("لغو پرداخت", "cancelpay:3", style="danger")]])
        frozen = copy.deepcopy(source)
        for method in ("sendMessage", "sendPhoto", "sendDocument", "editMessageText", "editMessageReplyMarkup", "copyMessage"):
            files = {"document": ("sample.txt", io.BytesIO(b"synthetic"))} if method in {"sendPhoto", "sendDocument"} else None
            client.call(method, {"chat_id": 1, "reply_markup": source}, files=files)
            sent = session.post.call_args.kwargs["data" if files else "json"]["reply_markup"]
            if isinstance(sent, str):
                sent = json.loads(sent)
            self.assertEqual(sent["inline_keyboard"][0][0], {"text": "کپی مبلغ", "copy_text": {"text": "120000"}, "icon_custom_emoji_id": ICONS["copy"]})
            self.assertEqual(sent["inline_keyboard"][1][0]["callback_data"], "cancelpay:3")
            self.assertNotIn("style", sent["inline_keyboard"][1][0])
            self.assertEqual(source, frozen)

    def test_colored_main_menu_preserves_reference_styles_in_every_inline_transport(self):
        session = Mock()
        session.headers = {}
        session.post.return_value.status_code = 200
        session.post.return_value.json.return_value = {"ok": True, "result": {"message_id": 1}}
        client = LayoutTelegram(TelegramClient("test-token", session=session, button_color_mode="colored"), self.engine())
        source = inline_main_menu_keyboard(include_admin=True)
        frozen = copy.deepcopy(source)
        expected = self.engine().prepare(source)
        styles = [["success"], ["success", "primary"], [None], ["primary"], [None], [None]]
        for method in ("sendMessage", "sendPhoto", "sendDocument", "editMessageText", "editMessageReplyMarkup", "copyMessage"):
            with self.subTest(method=method):
                field = {"sendPhoto": "photo", "sendDocument": "document"}.get(method)
                files = {field: ("synthetic.bin", io.BytesIO(b"synthetic"))} if field else None
                client.call(method, {"chat_id": 1, "reply_markup": source}, files=files)
                sent = session.post.call_args.kwargs["data" if files else "json"]["reply_markup"]
                if isinstance(sent, str):
                    sent = json.loads(sent)
                self.assertEqual([[button.get("style") for button in row] for row in sent["inline_keyboard"]], styles)
                self.assertEqual(sent, expected)
                self.assertEqual(source, frozen)

    def test_default_and_explicit_colored_mode_preserve_reply_contact_styles(self):
        for policy in ({}, {"button_color_mode": "colored"}):
            for source in (main_menu_keyboard(), contact_keyboard(style="primary")):
                with self.subTest(policy=policy, contact="request_contact" in source["keyboard"][0][0]):
                    session = Mock()
                    session.headers = {}
                    session.post.return_value.status_code = 200
                    session.post.return_value.json.return_value = {"ok": True, "result": {"message_id": 1}}
                    client = LayoutTelegram(TelegramClient("test-token", session=session, **policy), self.engine())
                    frozen = copy.deepcopy(source)
                    client.send_message(1, "synthetic", reply_markup=source)
                    sent = session.post.call_args.kwargs["json"]["reply_markup"]
                    self.assertEqual(sent, self.engine().prepare(frozen))
                    self.assertEqual(source, frozen)
                    if "request_contact" in source["keyboard"][0][0]:
                        self.assertTrue(sent["keyboard"][0][0]["request_contact"])
                        self.assertEqual(sent["keyboard"][0][0]["style"], "primary")
                        self.assertEqual(sent["keyboard"][1][0]["text"], "لغو و بازگشت")
                        self.assertNotIn("style", sent["keyboard"][1][0])
                    else:
                        self.assertEqual(sent["keyboard"][0][0]["style"], "success")
                        self.assertEqual(sent["keyboard"][1][1]["style"], "primary")

    def test_no_manifest_is_a_lossless_no_icon_fallback(self):
        source = keyboard("main", [[callback_button("فروشگاه", "store", style="success")]])
        self.assertEqual(apply_icons(source, {}), source)
        self.assertEqual(apply_icons({"remove_keyboard": True}, ICONS), {"remove_keyboard": True})
        self.assertEqual(apply_icons({"keyboard": None}, ICONS), {"keyboard": None})

    def test_manifest_relative_path_environment_override_and_extended_names(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "icons.json").write_text(json.dumps({"icons": ICONS}), encoding="utf-8")
            env = {"BOT_TOKEN": "test-token", "DATA_DIR": folder, "BUTTON_ICON_MANIFEST": "icons.json", "BUTTON_ICON_SHOP": "88888", "BUTTON_ICON_LAYOUT": "99999"}
            with patch.dict(os.environ, env, clear=True):
                settings = load_settings(root / ".env")
            self.assertEqual(settings.button_icon_ids["shop"], "88888")
            self.assertEqual(settings.button_icon_ids["layout"], "99999")
            self.assertEqual(len(settings.button_icon_ids), 45)

    def test_invalid_manifests_fail_without_echoing_their_contents(self):
        for invalid in ({"url": "https://example.test"}, {"shop": "not-a-numeric-id"}, {"shop": 12345}, []):
            with self.assertRaises(ValueError):
                validate_icon_ids(invalid)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            marker = "private-marker-not-to-be-printed"
            (root / "icons.json").write_text(marker, encoding="utf-8")
            with patch.dict(os.environ, {"BOT_TOKEN": "test-token", "DATA_DIR": folder, "BUTTON_ICON_MANIFEST": "icons.json"}, clear=True):
                with self.assertRaises(RuntimeError) as caught:
                    load_settings(root / ".env")
                self.assertNotIn(marker, str(caught.exception))
                self.assertTrue(caught.exception.__suppress_context__)

    def test_reordering_never_removes_or_changes_existing_icons(self):
        engine = self.engine()
        config = defaults("main")
        config["rows"].reverse()
        engine.db.get_setting.side_effect = lambda key, default=None: {"current": config} if key == "customer_layout:main" else default
        result = engine.prepare(inline_main_menu_keyboard())
        self.assertEqual(result["inline_keyboard"][0][0]["icon_custom_emoji_id"], ICONS["channel"])
        self.assertEqual(result["inline_keyboard"][-1][0]["icon_custom_emoji_id"], ICONS["shop"])

    def test_pack_assets_are_licensed_complete_and_hash_verified(self):
        folder = ROOT / "assets" / "button-icons"
        manifest = json.loads((folder / "sources.json").read_text(encoding="utf-8"))
        self.assertEqual(set(manifest["icons"]), set(ICON_SOURCES))
        self.assertIn("ISC License", (folder / "LICENSE-Lucide.txt").read_text(encoding="utf-8"))
        self.assertIn("The MIT License", (folder / "LICENSE-Lucide.txt").read_text(encoding="utf-8"))
        for key, item in manifest["icons"].items():
            raw = (folder / item["webp"]).read_bytes()
            self.assertEqual(raw[:4], b"RIFF")
            self.assertEqual(raw[8:12], b"WEBP")
            self.assertEqual(hashlib.sha256(raw).hexdigest(), item["sha256"])
            self.assertEqual(item["lucide"], ICON_SOURCES[key])
            self.assertTrue((folder / item["svg"]).is_file())
            self.assertLess(len(raw), 128 * 1024)

    def test_published_eleven_manifest_matches_licensed_asset_digest(self):
        folder = ROOT / "assets" / "button-icons"
        manifest = json.loads((folder / "elevenaccounts-testbot.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["version"], 1)
        self.assertEqual(manifest["bot_id"], 8545042168)
        self.assertEqual(manifest["source_package"], "lucide-static@1.41.0")
        self.assertIs(manifest["needs_repainting"], True)
        self.assertEqual(validate_icon_ids(manifest["icons"]), manifest["icons"])
        self.assertEqual(set(manifest["icons"]), set(ICON_SOURCES))
        digest = hashlib.sha256()
        for key in ICON_SOURCES:
            digest.update(key.encode() + (folder / "webp" / (key + ".webp")).read_bytes())
        self.assertEqual(manifest["asset_sha256"], digest.hexdigest())
        self.assertEqual(manifest["pack_name"], f"LucideMinimal{digest.hexdigest()[:8]}_by_ElevenaccountsTestbot")

    def test_publisher_reuses_pack_and_rejects_a_wrong_bot_before_any_write(self):
        client = Mock()
        client.call.return_value = {"id": 9, "username": "different_bot"}
        with self.assertRaisesRegex(ValueError, "identity mismatch"):
            publish(client, owner_id=1, bot_id=8, bot_username="test_bot", asset_dir=ROOT / "assets/button-icons")
        self.assertEqual(client.call.call_count, 1)
        stickers = [{"width": 100, "height": 100, "needs_repainting": True, "custom_emoji_id": value} for value in ICONS.values()]
        client.reset_mock()
        client.call.side_effect = [{"id": 8, "username": "test_bot"}, {"sticker_type": "custom_emoji", "stickers": stickers}, stickers]
        result = publish(client, owner_id=1, bot_id=8, bot_username="test_bot", asset_dir=ROOT / "assets/button-icons")
        self.assertEqual(result["icons"], ICONS)
        self.assertNotIn("createNewStickerSet", [call.args[0] for call in client.call.call_args_list])

    def test_publisher_uploads_verified_static_adaptive_assets_once(self):
        stickers = [{"width": 100, "height": 100, "needs_repainting": True, "custom_emoji_id": value} for value in ICONS.values()]
        calls = []

        def call(method, payload=None, **kwargs):
            calls.append(method)
            if method == "getMe":
                return {"id": 8, "username": "test_bot"}
            if method == "getStickerSet":
                if calls.count(method) == 1:
                    raise TelegramAPIError("getStickerSet", "Sticker set unavailable", error_code=400)
                return {"sticker_type": "custom_emoji", "stickers": stickers}
            if method == "createNewStickerSet":
                self.assertEqual(payload["user_id"], 1)
                self.assertEqual(payload["sticker_type"], "custom_emoji")
                self.assertTrue(payload["needs_repainting"])
                self.assertTrue(payload["name"].endswith("_by_test_bot"))
                self.assertLessEqual(len(payload["name"]), 64)
                self.assertEqual(len(payload["stickers"]), 45)
                for key, sticker in zip(ICON_SOURCES, payload["stickers"], strict=True):
                    self.assertEqual(sticker["sticker"], "attach://" + key)
                    self.assertEqual(sticker["format"], "static")
                    self.assertEqual(kwargs["files"][key][1].read(4), b"RIFF")
                return True
            if method == "getCustomEmojiStickers":
                return stickers
            self.fail("Unexpected publisher operation")

        result = publish(Mock(call=call), owner_id=1, bot_id=8, bot_username="test_bot", asset_dir=ROOT / "assets/button-icons")
        self.assertEqual(result["icons"], ICONS)
        self.assertEqual(calls, ["getMe", "getStickerSet", "createNewStickerSet", "getStickerSet", "getCustomEmojiStickers"])

    def test_publisher_stops_on_unavailable_owner_without_claiming_publication(self):
        client = Mock()
        client.call.side_effect = [{"id": 8, "username": "test_bot"}, TelegramAPIError("getStickerSet", "Sticker set unavailable", error_code=400), TelegramAPIError("createNewStickerSet", "Owner unavailable", error_code=400)]
        with self.assertRaises(TelegramAPIError):
            publish(client, owner_id=1, bot_id=8, bot_username="test_bot", asset_dir=ROOT / "assets/button-icons")
        self.assertEqual([call.args[0] for call in client.call.call_args_list], ["getMe", "getStickerSet", "createNewStickerSet"])

    def test_publisher_rejects_invalid_pack_shape_and_unverified_ids(self):
        stickers = [{"width": 100, "height": 100, "needs_repainting": True, "custom_emoji_id": value} for value in ICONS.values()]
        for change in ({"width": 512}, {"height": 512}, {"needs_repainting": False}, {"custom_emoji_id": None}):
            pack = copy.deepcopy(stickers)
            pack[0].update(change)
            client = Mock()
            client.call.side_effect = [{"id": 8, "username": "test_bot"}, {"sticker_type": "custom_emoji", "stickers": pack}]
            with self.subTest(change=change), self.assertRaises(ValueError):
                publish(client, owner_id=1, bot_id=8, bot_username="test_bot", asset_dir=ROOT / "assets/button-icons")
            self.assertEqual(client.call.call_count, 2)
        client = Mock()
        client.call.side_effect = [{"id": 8, "username": "test_bot"}, {"sticker_type": "custom_emoji", "stickers": stickers}, stickers[:-1]]
        with self.assertRaisesRegex(ValueError, "verification failed"):
            publish(client, owner_id=1, bot_id=8, bot_username="test_bot", asset_dir=ROOT / "assets/button-icons")

    def test_publisher_diagnostics_are_allowlisted_and_reset_between_requests(self):
        replies = [Mock(), Mock(), Mock()]
        replies[0].json.return_value = {"description": "Bad Request: STICKERSET_INVALID private-marker"}
        replies[1].json.return_value = {"description": "Bad Request: USER NOT FOUND private-marker"}
        replies[2].json.return_value = {"description": "private-marker"}
        with SafeDiagnosticsSession() as session, patch("requests.Session.post", side_effect=replies):
            for expected in ("STICKERSET_INVALID", "USER NOT FOUND", "unspecified"):
                session.post("https://example.test")
                self.assertEqual(session.category, expected)
                self.assertNotIn("private-marker", session.category)

    def test_durable_notice_callers_do_not_bake_runtime_icons_into_idempotency_data(self):
        tree = ast.parse((ROOT / "app/bot.py").read_text(encoding="utf-8"))
        for call in ast.walk(tree):
            if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Attribute) or call.func.attr != "_notify_user_durable":
                continue
            for keyword in call.keywords:
                if keyword.arg == "reply_markup":
                    self.assertFalse(any(isinstance(node, ast.Attribute) and node.attr == "button_icon_ids" for node in ast.walk(keyword.value)), f"Runtime icons in canonical durable markup at line {call.lineno}")

    def test_topup_notice_survives_shutdown_and_manifest_change_without_outbox_rewrite(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            settings = Settings(bot_token="synthetic-token", database_path=root / "bot.sqlite3", data_dir=root,
                                bootstrap_admin_chat_id=9001, button_icon_ids=ICONS)
            db = Database(settings.database_path)
            first_transport = FakeTelegram()
            app = BotApplication(settings, db, first_transport)
            app.initialize()
            user = db.upsert_user(9002, 9002, username="synthetic_buyer")
            payment = db.create_wallet_topup_payment(user["id"], 100000, "card", idempotency_key="icons-topup")
            app.stop_event.set()
            self.assertTrue(app._complete_payment(payment["id"]))
            key = f"payment:{payment['id']}:topup-confirmed"
            queued = db.get_outbound_message_by_idempotency_key(key)
            self.assertEqual(queued["status"], "queued")
            self.assertNotIn("icon_custom_emoji_id", queued["reply_markup_json"])
            self.assertEqual(first_transport.messages, [])
            changed_icons = {name: str(int(value) + 1000) for name, value in ICONS.items()}
            transport = FakeTelegram()
            restarted = BotApplication(replace(settings, button_icon_ids=changed_icons), db, transport)
            for _ in range(2):
                self.assertTrue(restarted._notify_user_durable(user, queued["body"], idempotency_key=key, reply_markup=main_menu_keyboard()))
            self.assertEqual(len(transport.messages), 1)
            self.assertEqual(transport.messages[0]["reply_markup"]["keyboard"][0][0]["icon_custom_emoji_id"], changed_icons["shop"])
            self.assertEqual(db.get_outbound_message_by_idempotency_key(key)["reply_markup_json"], queued["reply_markup_json"])
            self.assertEqual(db.wallet_balance(user["id"]), 100000)


if __name__ == "__main__":
    unittest.main()
