from decimal import Decimal as D
import unittest
from app.price_threshold import price_threshold


class PriceThresholdTests(unittest.TestCase):
    def test_case_thresholds_match_pair_cost_boundary(self):
        for a, b, pa, pb in [(2765, 2275, D('.38'), D('.12')), (1198, 1569, D('.07'), D('.12'))]:
            result = price_threshold('A', 'B', a, b, pa, pb)
            threshold = D(result['threshold_usd'])
            self.assertAlmostEqual(D(a) * threshold, D(b) * pa)
            for delta, expected in [(D('-.0001'), 1), (D('.0001'), -1)]:
                price = threshold + delta
                n = 10000
                saving = (n * pa + (n-a) * price) - (n * price + (n-b) * pa)
                self.assertEqual(1 if saving > 0 else -1, expected)

    def test_exact_tie(self):
        result = price_threshold('A', 'B', 50, 25, D('.2'), D('.1'))
        self.assertEqual(D(result['threshold_usd']), D('.1'))
        self.assertIn('orders tie', result['detail'])

    def test_zero_matches(self):
        result = price_threshold('A', 'B', 0, 25, D('.2'), D('.1'))
        self.assertIsNone(result['threshold_usd'])
        self.assertIn('any nonnegative price', result['detail'])
        result = price_threshold('A', 'B', 0, 0, D('.2'), D('.1'))
        self.assertIn('equal modeled cost', result['detail'])
        result = price_threshold('A', 'B', 50, 0, D('.2'), D('.1'))
        self.assertEqual(D(result['threshold_usd']), 0)
        self.assertIn('No nonnegative price', result['detail'])

    def test_full_precision_and_small_price(self):
        result = price_threshold('A', 'B', 100, 1, D('.00012345'), D('.000001'))
        self.assertEqual(D(result['threshold_usd']), D('.0000012345'))
        self.assertNotIn('below less than', result['detail'])
