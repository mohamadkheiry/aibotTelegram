"""Coverage of every customer surface and presentation invariants."""
from __future__ import annotations

import ast
import copy
import json
import unittest
from pathlib import Path
from unittest.mock import Mock

from app.admin_layouts import move_slot, reflow
from app.customer_layouts import (SECTIONS, LayoutEngine, LayoutTelegram, arrange, clean_markup,
                                  defaults, definition, keyboard, same_canonical_markup, validate)
from app.keyboards import contains_emoji, contact_keyboard, inline_main_menu_keyboard, main_menu_keyboard


class CustomerLayoutTests(unittest.TestCase):
    def engine(self, sections=None):
        db = Mock()
        settings = sections or {}
        db.get_setting.side_effect = lambda key, default=None: settings.get(key, default)
        return LayoutEngine(db)

    def test_every_template_permutation_is_lossless_and_conditional_buttons_stay_absent(self):
        for section, spec in SECTIONS.items():
            with self.subTest(section=section):
                rows = [[{"text": label, "callback_data": f"test:{index}", "style": "primary"}]
                        for index, (key, label) in enumerate(spec.slots)]
                if "items" in dict(spec.slots):
                    rows[0] = [{"text": "آیتم نمونه", "callback_data": "dynamic:1"},
                               {"text": "آیتم نمونه", "callback_data": "dynamic:2"}]
                original = keyboard(section, rows)
                preserved = copy.deepcopy(original)
                for width in (1, 2, 3):
                    config = reflow(defaults(section), width)
                    config["rows"].reverse()
                    result = arrange(section, original, config)
                    self.assertCountEqual([button for row in result["inline_keyboard"] for button in row],
                                          [button for row in original["inline_keyboard"] for button in row])
                    self.assertTrue(all(1 <= len(row) <= 3 for row in result["inline_keyboard"]))
                    self.assertEqual(original, preserved)
                    subset = keyboard(section, rows[-1:])
                    applied = arrange(section, subset, config)
                    self.assertEqual(sum(map(len, applied["inline_keyboard"])), len(rows[-1]))

    def test_main_inline_reply_contact_keep_exact_actions_styles_and_admin_entry(self):
        for source, section, kind in ((inline_main_menu_keyboard(include_admin=True), "main", "inline_keyboard"),
                                      (main_menu_keyboard(include_admin=True), "main", "keyboard"),
                                      (contact_keyboard(), "input_contact", "keyboard")):
            config = reflow(defaults(section), 2)
            config["rows"].reverse()
            result = self.engine({"customer_layout:" + section: {"current": config}}).prepare(source)
            self.assertCountEqual([b for row in result[kind] for b in row], [b for row in source[kind] for b in row])
            self.assertNotIn("_customer_layout", result)
            self.assertEqual(result.get("one_time_keyboard"), source.get("one_time_keyboard"))
            if section == "main":
                self.assertEqual(result[kind][-1][0]["text"], "پنل مدیریت")
            else:
                self.assertTrue(next(b for row in result[kind] for b in row if "request_contact" in b)["request_contact"])

    def test_unknown_action_is_not_dropped_and_invalid_config_falls_back(self):
        original = keyboard("product", [[{"text": "دکمه آینده", "url": "https://example.com"}], [{"text": "خرید", "callback_data": "buy:9"}]])
        result = self.engine({"customer_layout:product": {"current": {"rows": []}}}).prepare(original)
        self.assertCountEqual([b for r in result["inline_keyboard"] for b in r], [b for r in original["inline_keyboard"] for b in r])
        for corrupt in (None, "bad", [], 4):
            self.assertEqual(self.engine({"customer_layout:main": corrupt}).prepare(main_menu_keyboard()), clean_markup(main_menu_keyboard()))

    def test_default_preserves_legacy_repeated_payment_controls_in_original_order(self):
        original = keyboard("wallet", [
            [{"text": "ارسال فیش واریز", "callback_data": "receipt:1", "style": "primary"}],
            [{"text": "لغو پرداخت", "callback_data": "cancelpay:1", "style": "danger"}],
            [{"text": "ارسال فیش واریز", "callback_data": "receipt:2", "style": "primary"}],
            [{"text": "لغو پرداخت", "callback_data": "cancelpay:2", "style": "danger"}],
        ])
        for settings in ({}, {"customer_layout:wallet": {"current": defaults("wallet"), "version": 3}}):
            self.assertEqual(self.engine(settings).prepare(original), clean_markup(original))

    def test_inheritance_and_independent_scope_overrides(self):
        base = reflow(defaults("product"), 2)
        local = defaults("product:2")
        local["rows"].reverse()
        engine = self.engine({"customer_layout:product": {"current": base, "version": 3},
                              "customer_layout:product:2": {"current": local, "version": 1}})
        self.assertEqual(engine.snapshot("product:1")["config"], base)
        self.assertEqual(engine.snapshot("product:2")["config"], local)
        self.assertEqual(engine.snapshot("product:2")["base_version"], 3)

    def test_global_item_order_is_applied_before_pagination_and_new_items_survive(self):
        config = defaults("store")
        config["item_order"] = ["cat:24", "cat:2", "cat:1"]
        engine = self.engine({"customer_layout:store": {"current": config}})
        ordered = engine.order_items("store", list(range(1, 26)), lambda i: f"cat:{i}")
        self.assertEqual(ordered[:3], [24, 2, 1])
        self.assertCountEqual(ordered[:20] + ordered[20:], range(1, 26))
        config["reverse"] = True
        self.assertEqual(engine.order_items("store", list(range(1, 26)), lambda i: f"cat:{i}"), list(reversed(ordered)))

    def test_operational_kill_switch_retains_saved_config_and_strips_metadata(self):
        config = reflow(defaults("main"), 3)
        engine = self.engine({"customer_layouts_enabled": False, "customer_layout:main": {"current": config}})
        self.assertEqual(engine.prepare(main_menu_keyboard()), clean_markup(main_menu_keyboard()))
        self.assertEqual(engine.snapshot("main")["config"], config)
        self.assertEqual(engine.order_items("store", [4, 1], lambda i: f"cat:{i}"), [4, 1])

    def test_validate_rejects_missing_duplicate_or_injected_actions_and_bad_values(self):
        invalid = []
        for mutate in (lambda c: c["rows"].pop(), lambda c: c["rows"].append(["wallet"]),
                       lambda c: c["rows"].append(["delete"]), lambda c: c.update(columns=True),
                       lambda c: c.update(columns=8), lambda c: c.update(reverse="false"),
                       lambda c: c.update(item_order=["https://example.com"]),
                       lambda c: c.update(item_order=["cat:1"]), lambda c: c.update(url="https://example.com")):
            config = defaults("main")
            mutate(config)
            invalid.append(config)
        for config in invalid:
            with self.assertRaises(ValueError):
                validate("main", config)
        for section in ("unknown", "main:1", "product:0", "product:1:2", "product:۱", "../main"):
            with self.assertRaises(ValueError):
                definition(section)

    def test_moves_preserve_slots_and_never_mix_dynamic_list_with_controls(self):
        for section, spec in SECTIONS.items():
            for key, _ in spec.slots:
                config = reflow(defaults(section), 2)
                for op in ("up", "down", "left", "right", "alone", "first", "last", "joinprev", "joinnext"):
                    result = move_slot(config, key, op)
                    validate(section, result)
                    self.assertCountEqual([k for row in result["rows"] for k in row], dict(spec.slots))

    def test_transport_prepares_every_send_edit_copy_and_document_without_mutating_source(self):
        transport = Mock()
        config = defaults("card_payment")
        config["rows"].reverse()
        proxy = LayoutTelegram(transport, self.engine({"customer_layout:card_payment": {"current": config}}))
        markup = keyboard("card_payment", [[{"text": "کپی مبلغ", "copy_text": {"text": "2500"}}],
                                           [{"text": "لغو پرداخت", "callback_data": "cancelpay:7"}]])
        frozen = copy.deepcopy(markup)
        for method in LayoutTelegram.METHODS:
            getattr(proxy, method)(55, reply_markup=markup)
            sent = getattr(transport, method).call_args.kwargs["reply_markup"]
            self.assertNotIn("_customer_layout", sent)
            self.assertEqual(sent["inline_keyboard"][0][0]["callback_data"], "cancelpay:7")
            self.assertEqual(markup, frozen)
        proxy.run_polling("handler")
        transport.run_polling.assert_called_once_with("handler")
        proxy.call("sendMessage", {"chat_id": 55, "reply_markup": markup})
        self.assertNotIn("_customer_layout", transport.call.call_args.args[1]["reply_markup"])
        self.assertEqual(markup, frozen)

    def test_legacy_outbox_tag_compatibility_does_not_relax_real_payload_collision(self):
        legacy = {"inline_keyboard": [[{"text": "سفارش", "callback_data": "order:1"}]]}
        tagged_markup = {**legacy, "_customer_layout": "order_notice"}
        self.assertTrue(same_canonical_markup(json.dumps(legacy), json.dumps(tagged_markup)))
        altered = copy.deepcopy(tagged_markup)
        altered["inline_keyboard"][0][0]["callback_data"] = "order:2"
        self.assertFalse(same_canonical_markup(json.dumps(legacy), json.dumps(altered)))
        self.assertFalse(same_canonical_markup(None, json.dumps(tagged_markup)))
        # Existing transport callers may use Telegram's string reply buttons.
        raw = {"keyboard": [["plain button"]], "resize_keyboard": True}
        self.assertEqual(clean_markup(raw), raw)
        self.assertEqual(self.engine().prepare("[1]"), "[1]")

    def test_all_customer_keyboard_construction_sites_are_explicitly_registered(self):
        root = Path(__file__).resolve().parents[1]
        tree = ast.parse((root / "app/bot.py").read_text(encoding="utf-8"))
        known = set()
        admin_only = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
            if name in {"customer_keyboard", "_input_cancel_markup"}:
                self.assertTrue(node.args, node.lineno)
                argument = node.args[0]
                prefix = argument.value if isinstance(argument, ast.Constant) else argument.values[0].value
                known.add(prefix.split(":")[0])
            if name == "inline_keyboard":
                source = ast.get_source_segment((root / "app/bot.py").read_text(encoding="utf-8"), node)
                self.assertIn("adm:", source, f"unregistered customer markup at line {node.lineno}")
                admin_only.append(node.lineno)
        # Shared builders cover all main-menu replies and all contact prompts.
        known.update({"main", "input_contact"})
        self.assertEqual(known, set(SECTIONS))
        self.assertEqual(len(admin_only), 5)
        self.assertTrue(all(not contains_emoji(spec.title) for spec in SECTIONS.values()))


if __name__ == "__main__":
    unittest.main()
