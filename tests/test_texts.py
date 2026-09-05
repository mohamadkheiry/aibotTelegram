from __future__ import annotations

import unittest

from app import texts


class TextTests(unittest.TestCase):
    def test_main_menu_escapes_name_and_has_required_sections(self) -> None:
        value = texts.main_menu("<محمد>")
        self.assertIn("&lt;محمد&gt;", value)
        for label in ("فروشگاه", "حساب من", "کیف پول", "دعوت و کسب درآمد", "پشتیبانی", "کانال"):
            self.assertIn(label, value)

    def test_order_summary_switches_to_discount_template(self) -> None:
        order = {
            "order_number": "ORD-TEXT-1",
            "product_title": "اشتراک",
            "product_icon": "",
            "product_duration": "یک ماه",
            "base_price": 100_000,
            "discount_amount": 10_000,
            "final_amount": 90_000,
        }
        value = texts.order_summary(order, 5_000, "تومان")
        self.assertIn("قیمت اصلی", value)
        self.assertIn("تخفیف", value)
        self.assertIn("90,000 تومان", value)


if __name__ == "__main__":
    unittest.main()
