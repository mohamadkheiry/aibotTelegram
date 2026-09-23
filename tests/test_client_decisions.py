"""Synthetic acceptance for confirmed September 21 client decisions."""
import copy
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest.mock import patch

from app.db import ConflictError, Database, NotFoundError, ValidationError
from tests import test_db as fixture
from tests import test_admin as admin_fixture
from tests import test_admin_catalog_hierarchy as ui_fixture


class AmountSettlementTests(fixture.DatabaseTestCase):
    def prepare(self, *, held=0, topup=False, user_suffix=1):
        owner = self.db.upsert_user(900, 900, username="owner")
        admin = self.db.bootstrap_admin("owner", owner["chat_id"])
        user = self.user(user_suffix)
        order = None
        if topup:
            payment = self.db.create_wallet_topup_payment(user["id"], 100000, "card", idempotency_key=f"topup:{user_suffix}", now=fixture.BASE_TIME)
        else:
            product = self.product(price=100000)
            order = self.db.create_order(user["id"], product["id"], now=fixture.BASE_TIME)
            if held:
                self.db.credit_wallet(user["id"], held, reason="fixture", idempotency_key=f"seed:{user_suffix}")
                self.db.hold_wallet_funds(order["id"], idempotency_key=f"hold:{order['id']}", now=fixture.BASE_TIME)
            payment = self.db.create_order_payment(order["id"], "card", idempotency_key=f"pay:{order['id']}", now=fixture.BASE_TIME)
        self.db.submit_payment_receipt(payment["id"], "receipt", now=fixture.BASE_TIME + timedelta(minutes=2))
        return user, order, payment, admin

    def settle(self, payment, admin, amount, reference="bank-fixture", **kwargs):
        return self.db.settle_card_receipt_amount(payment["id"], amount, reference, admin["id"], "bank checked",
            receipt_file_id="receipt", now=fixture.BASE_TIME + timedelta(minutes=3), **kwargs)

    def test_overpayment_settles_order_and_credits_only_surplus(self):
        user, order, payment, admin = self.prepare()
        result = self.settle(payment, admin, 120000)
        self.assertEqual(result["status"], "paid")
        self.assertEqual(self.db.wallet_balance(user["id"]), 20000)
        settled_order = self.db.get_order(order["id"])
        self.assertEqual(settled_order["external_paid_amount"], 100000)
        self.assertEqual(settled_order["status"], "paid")
        self.assertEqual(self.db.card_amount_settlement(result)["wallet_credit"], 20000)
        self.settle(payment, admin, 120000)
        self.assertEqual(self.db.wallet_balance(user["id"]), 20000)
        for amount, reference in ((130000, "bank-fixture"), (120000, "other")):
            with self.assertRaises(ConflictError):
                self.settle(payment, admin, amount, reference)
        self.assertEqual(self.db.wallet_balance(user["id"]), 20000)

    def test_underpayment_cancels_order_releases_hold_and_credits_actual(self):
        user, order, payment, admin = self.prepare(held=20000)
        self.settle(payment, admin, 60000)
        result = self.db.get_order(order["id"])
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(result["external_paid_amount"], 0)
        self.assertEqual(result["wallet_captured_amount"], 0)
        self.assertEqual(self.db.wallet_balance(user["id"]), 80000)
        history = self.db.get_user_transaction(user["id"], f"payment:{payment['id']}")
        self.assertEqual(history["amount_signed"], -60000)
        payments = [row for row in self.db.list_user_transactions(user["id"]) if row["transaction_key"].startswith("payment:")]
        self.assertEqual(payments[0]["amount_signed"], -60000)
        self.settle(payment, admin, 60000)
        self.assertEqual(self.db.wallet_balance(user["id"]), 80000)

    def test_exact_base_without_matching_suffix_completes_without_surplus(self):
        self.prepare()
        user, order, payment, admin = self.prepare(user_suffix=2)
        self.settle(payment, admin, 100000)
        self.assertEqual(self.db.wallet_balance(user["id"]), 0)
        self.assertEqual(self.db.get_order(order["id"])["status"], "paid")
        with self.assertRaises(ConflictError):
            self.db.mark_payment_paid(payment["id"], external_reference="manual:other")

    def test_topup_credits_actual_and_legacy_replay_does_not_add_base(self):
        user, _, payment, admin = self.prepare(topup=True)
        self.settle(payment, admin, 60000)
        self.db.mark_payment_paid(payment["id"], external_reference="bank-fixture")
        self.assertEqual(self.db.wallet_balance(user["id"]), 60000)

    def test_bank_reference_unique_across_payments_and_rejects_event_ledger(self):
        _, _, payment, admin = self.prepare()
        self.settle(payment, admin, 120000)
        user2, _, second, _ = self.prepare(user_suffix=2)
        with self.assertRaises(ConflictError):
            self.settle(second, admin, 120000)
        self.assertEqual(self.db.wallet_balance(user2["id"]), 0)
        self.assertEqual(self.db.get_payment(second["id"])["status"], "verifying")

    def test_stale_receipt_revoked_role_and_invalid_amount_fail_closed(self):
        user, _, payment, admin = self.prepare()
        for amount in (0, -10, True, 0.5, 10**13, payment["payable_amount"]):
            with self.assertRaises(ValidationError):
                self.settle(payment, admin, amount)
        self.db.submit_payment_receipt(payment["id"], "new-receipt", now=fixture.BASE_TIME + timedelta(minutes=2))
        with self.assertRaises(ConflictError):
            self.settle(payment, admin, 120000)
        with self.db._transaction() as connection:
            connection.execute("UPDATE admins SET role='support' WHERE id=?", (admin["id"],))
        with self.assertRaises(NotFoundError):
            self.settle(payment, admin, 120000)
        self.assertEqual(self.db.wallet_balance(user["id"]), 0)

    def test_bank_event_reference_cannot_be_reused_as_manual_receipt(self):
        user, _, payment, admin = self.prepare()
        self.db.record_card_payment_event("existing-event", 60000, fixture.BASE_TIME, "review", now=fixture.BASE_TIME)
        with self.assertRaises(ConflictError):
            self.settle(payment, admin, 60000, "existing-event")
        self.assertEqual(self.db.wallet_balance(user["id"]), 0)

    def test_notice_failure_rolls_back_every_effect_and_race_credits_once(self):
        user, order, payment, admin = self.prepare()
        with patch.object(self.db, "_queue_user_message_in_transaction", side_effect=RuntimeError("crash")):
            with self.assertRaises(RuntimeError):
                self.settle(payment, admin, 120000)
        self.assertEqual(self.db.wallet_balance(user["id"]), 0)
        self.assertEqual(self.db.get_payment(payment["id"])["status"], "verifying")
        self.assertEqual(self.db.get_order(order["id"])["status"], "awaiting_confirmation")
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.settle(payment, admin, 120000), range(2)))
        self.assertEqual(results[0], results[1])
        self.assertEqual(self.db.wallet_balance(user["id"]), 20000)
        self.db = Database(self.db.path)
        self.db.initialize()
        self.settle(payment, admin, 120000)
        self.assertEqual(self.db.wallet_balance(user["id"]), 20000)
        with self.db._read() as connection:
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM outbound_messages WHERE idempotency_key=?", (f"payment:{payment['id']}:order-confirmed",)).fetchone()[0], 1)


class RewardPriorityTests(fixture.DatabaseTestCase):
    def test_combined_scoped_only_wins_when_all_conditions_match(self):
        inviter, invitee, product = self.user(1), self.user(2), self.product()
        self.db.record_referral(inviter["id"], invitee["id"], now=fixture.BASE_TIME)
        self.db.create_reward_rule("first", event_type="first_purchase", amount=60, now=fixture.BASE_TIME)
        self.db.create_reward_rule("scoped", event_type="combined", amount=40, product_id=product["id"], conditions={"minimum_order_amount": 2000}, now=fixture.BASE_TIME)
        order = self.db.create_order(invitee["id"], product["id"], now=fixture.BASE_TIME)
        self.db.grant_referral_reward(invitee["id"], "first_purchase", "first", product_id=product["id"], source_order_id=order["id"], now=fixture.BASE_TIME)
        self.assertEqual(self.db.wallet_balance(inviter["id"]), 60)

    def test_scoped_reward_replaces_general_first_even_after_grant_then_disable(self):
        inviter, invitee, product = self.user(1), self.user(2), self.product()
        self.db.record_referral(inviter["id"], invitee["id"], now=fixture.BASE_TIME)
        scoped = self.db.create_reward_rule("product", event_type="product_purchase", amount=40, product_id=product["id"], now=fixture.BASE_TIME)
        self.db.create_reward_rule("first", event_type="first_purchase", amount=60, now=fixture.BASE_TIME)
        order = self.db.create_order(invitee["id"], product["id"], now=fixture.BASE_TIME)
        args = dict(product_id=product["id"], source_order_id=order["id"], now=fixture.BASE_TIME)
        self.assertEqual(self.db.grant_referral_reward(invitee["id"], "first_purchase", "first", **args), [])
        self.db.grant_referral_reward(invitee["id"], "product_purchase", "product", **args)
        self.db.set_reward_rule_active(scoped["id"], False)
        self.assertEqual(self.db.grant_referral_reward(invitee["id"], "first_purchase", "first", **args), [])
        self.assertEqual(self.db.wallet_balance(inviter["id"]), 40)

    def test_absent_inactive_future_and_unmatched_combined_do_not_suppress_first(self):
        inviter, invitee, product = self.user(1), self.user(2), self.product()
        self.db.record_referral(inviter["id"], invitee["id"], now=fixture.BASE_TIME)
        self.db.create_reward_rule("first", event_type="first_purchase", amount=60, now=fixture.BASE_TIME)
        self.db.create_reward_rule("future", event_type="product_purchase", amount=40, product_id=product["id"], now=fixture.BASE_TIME+timedelta(days=1))
        order = self.db.create_order(invitee["id"], product["id"], now=fixture.BASE_TIME)
        self.db.grant_referral_reward(invitee["id"], "first_purchase", "first", product_id=product["id"], source_order_id=order["id"], now=fixture.BASE_TIME)
        self.assertEqual(self.db.wallet_balance(inviter["id"]), 60)


class ReferrerProfileTests(unittest.TestCase):
    setUp = admin_fixture.AdminControllerTests.setUp
    tearDown = admin_fixture.AdminControllerTests.tearDown
    message = admin_fixture.AdminControllerTests.message
    handle = admin_fixture.AdminControllerTests.handle

    def test_profile_shows_upstream_inviter_not_downstream_count_and_escapes_name(self):
        inviter = self.db.upsert_user(2002, 2002, username="inviter", first_name="<unsafe>")
        invitee = self.db.upsert_user(2003, 2003, username="invitee")
        self.db.record_referral(inviter["id"], invitee["id"])
        self.handle("/user 2003")
        text = "\n".join(m["text"] for m in self.telegram.messages)
        self.assertIn("معرف این کاربر", text)
        self.assertIn("&lt;unsafe&gt;", text)
        self.assertIn("@inviter", text)
        self.assertIn("<code>2002</code>", text)
        self.handle("/user 2002")
        self.assertIn("ثبت نشده", self.telegram.messages[-1]["text"])


class AmountFormTests(unittest.TestCase):
    OWNER = ui_fixture.AdminCatalogHierarchyTests.OWNER
    for _name in ("setUp", "tearDown", "message", "callback", "_take_update_id", "send_message", "send_callback", "actor_user", "state", "prompt", "button_update", "click", "buttons"):
        locals()[_name] = getattr(ui_fixture.AdminCatalogHierarchyTests, _name)

    def prepare_form(self):
        self.send_message(self.OWNER, text="/start")
        owner = self.actor_user()
        payment = self.db.create_wallet_topup_payment(owner["id"], 100000, "card", idempotency_key="ui-topup", now=fixture.BASE_TIME)
        self.db.submit_payment_receipt(payment["id"], "receipt", now=fixture.BASE_TIME+timedelta(minutes=2))
        self.send_callback(self.OWNER, "adm:ui:a:approve_payment")
        self.click(label=self.buttons()[0]["text"])
        self.click(label="مبلغ متفاوت؛ ثبت مبلغ واقعی")
        for value in ("۶۰۰۰۰", "ui-bank-ref", "ورود وجه به حساب بررسی شد"):
            self.send_message(self.OWNER, text=value)
        self.assertEqual(self.state()["status"], "confirm")
        self.assertIn("60,000", self.prompt()["text"])
        return owner, payment

    def test_actual_amount_keyboard_confirmation_and_replay(self):
        owner, payment = self.prepare_form()
        before = self.db.wallet_balance(owner["id"])
        update = copy.deepcopy(self.button_update(label="تأیید و اجرا"))
        self.app.process_update(update)
        self.assertEqual(self.state()["status"], "done")
        self.app.process_update(update)
        self.assertEqual(self.db.wallet_balance(owner["id"]), before+60000)

    def test_form_rejects_receipt_replaced_after_confirmation_preview(self):
        owner, payment = self.prepare_form()
        self.db.submit_payment_receipt(payment["id"], "replacement")
        before = self.db.wallet_balance(owner["id"])
        self.click(label="تأیید و اجرا")
        self.assertNotEqual(self.state()["status"], "done")
        self.assertEqual(self.db.wallet_balance(owner["id"]), before)
        self.assertEqual(self.db.get_payment(payment["id"])["status"], "verifying")

    def test_crash_after_financial_commit_replays_without_double_credit(self):
        from app.db import DatabaseError
        owner, payment = self.prepare_form()
        before = self.db.wallet_balance(owner["id"])
        update = self.button_update(label="تأیید و اجرا")
        original = self.db.settle_card_receipt_amount
        calls = 0
        def fail_after(*args, **kwargs):
            nonlocal calls
            result = original(*args, **kwargs)
            calls += 1
            if calls == 1:
                raise DatabaseError("crash after commit")
            return result
        with patch.object(self.db, "settle_card_receipt_amount", side_effect=fail_after):
            self.assertIs(self.app.process_update_safe(copy.deepcopy(update)), False)
            self.assertEqual(self.state()["status"], "executing")
            self.assertIsNot(self.app.process_update_safe(copy.deepcopy(update)), False)
        self.assertEqual(self.db.wallet_balance(owner["id"]), before+60000)
        self.assertEqual(self.state()["status"], "done")
        self.app._reconcile_paid_payment_notices()
        self.assertEqual(self.db.wallet_balance(owner["id"]), before+60000)
