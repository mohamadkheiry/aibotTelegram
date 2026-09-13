"""One explicitly scoped product reward per inclusive time window."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

from app.db import ConflictError
from tests.test_db import BASE_TIME, DatabaseTestCase


class ProductRewardExclusivityTests(DatabaseTestCase):
    def rule(self, key, product, **overrides):
        args = dict(event_type="product_purchase", amount=10,
                    product_id=product["id"], now=BASE_TIME)
        args.update(overrides)
        return self.db.create_reward_rule(key, **args)

    def test_duplicate_fixed_or_percent_product_rule_is_rejected(self):
        for first_mode in ("fixed", "percent"):
            for next_mode in ("fixed", "percent"):
                with self.subTest(first=first_mode, next=next_mode):
                    product = self.product(sku=f"{first_mode}-{next_mode}")
                    original = self.rule(f"old-{product['id']}", product,
                                         amount_mode=first_mode)
                    before = self.db.list_reward_rules()
                    with self.assertRaisesRegex(ConflictError, "پاداش فعال دارد"):
                        self.rule(f"new-{product['id']}", product, amount_mode=next_mode)
                    self.assertEqual(self.db.list_reward_rules(), before)
                    self.assertEqual(self.rule(original["rule_key"], product,
                                               amount_mode=first_mode), original)

    def test_inactive_replacement_activation_rechecks_conflict(self):
        product = self.product()
        old = self.rule("old", product)
        new = self.rule("new", product, amount_mode="percent", active=False)
        with self.assertRaises(ConflictError):
            self.db.set_reward_rule_active(new["id"], True)
        self.db.set_reward_rule_active(old["id"], False)
        self.assertTrue(self.db.set_reward_rule_active(new["id"], True)["is_active"])
        self.assertTrue(self.db.set_reward_rule_active(new["id"], True)["is_active"])
        with self.assertRaises(ConflictError):
            self.db.set_reward_rule_active(old["id"])
        self.assertEqual(len(self.db.list_reward_rules()), 2)

    def test_time_windows_are_inclusive_and_nonoverlap_is_allowed(self):
        product = self.product()
        end = BASE_TIME + timedelta(days=1)
        self.rule("first", product, starts_at=BASE_TIME, ends_at=end)
        with self.assertRaises(ConflictError):
            self.rule("touch", product, starts_at=end, ends_at=end + timedelta(days=1))
        self.rule("next", product, starts_at=end + timedelta(seconds=1))
        self.rule("previous", product, ends_at=BASE_TIME - timedelta(seconds=1))
        with self.assertRaises(ConflictError):
            self.rule("unbounded", product)

    def test_combined_product_list_cannot_bypass_exclusivity_in_either_direction(self):
        one, two = self.product(sku="one"), self.product(sku="two")
        old = self.rule("direct", one)
        args = dict(event_type="combined", product_id=None,
                    conditions={"product_ids": [one["id"], two["id"]]})
        with self.assertRaises(ConflictError):
            self.rule("combined", two, **args)
        self.db.set_reward_rule_active(old["id"], False)
        self.rule("combined", two, **args)
        for product in (one, two):
            with self.assertRaises(ConflictError):
                self.rule(f"direct-{product['id']}", product, event_type="first_purchase")

    def test_combined_intersection_and_general_rules_preserve_existing_scope(self):
        one, two = self.product(sku="one"), self.product(sku="two")
        self.rule("one", one, event_type="combined",
                  conditions={"product_ids": [one["id"], two["id"]]})
        self.rule("two", two)
        self.rule("general", one, event_type="first_purchase", product_id=None)
        self.db.create_reward_rule("start", event_type="start", amount=10)
        self.assertEqual(len(self.db.list_reward_rules()), 4)

    def test_create_race_has_one_winner(self):
        product = self.product()
        barrier = Barrier(2)

        def create(key):
            barrier.wait()
            try:
                return self.rule(key, product)["id"]
            except ConflictError:
                return None

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(create, ["race-one", "race-two"]))
        self.assertEqual(sum(value is not None for value in results), 1)
        self.assertEqual(len(self.db.list_reward_rules()), 1)

    def test_activation_race_has_one_winner(self):
        product = self.product()
        rules = [self.rule(key, product, active=False) for key in ("one", "two")]
        barrier = Barrier(2)

        def activate(rule):
            barrier.wait()
            try:
                return self.db.set_reward_rule_active(rule["id"], True)["id"]
            except ConflictError:
                return None

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(activate, rules))
        self.assertEqual(sum(value is not None for value in results), 1)
        self.assertEqual(len(self.db.list_reward_rules(active_only=True)), 1)

    def test_idempotency_replay_preserves_inactive_rule_and_rejects_new_terms(self):
        product = self.product()
        old = self.rule("old", product)
        self.db.set_reward_rule_active(old["id"], False)
        self.rule("new", product, amount_mode="percent")
        self.assertFalse(self.rule("old", product)["is_active"])
        with self.assertRaisesRegex(ConflictError, "different terms"):
            self.rule("old", product, amount=11)
