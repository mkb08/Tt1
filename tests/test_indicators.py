import unittest

from tt import indicators as ind
from tests.helpers import bars


class IndicatorTests(unittest.TestCase):
    def test_sma(self):
        self.assertEqual(ind.sma([1, 2, 3, 4], 2), [None, 1.5, 2.5, 3.5])

    def test_ema_seeds_with_sma(self):
        e = ind.ema([1, 2, 3, 4], 3)
        self.assertEqual(e[:2], [None, None])
        self.assertAlmostEqual(e[2], 2.0)
        self.assertAlmostEqual(e[3], 4 * 0.5 + 2.0 * 0.5)

    def test_rsi_extremes(self):
        self.assertEqual(ind.rsi([1, 2, 3, 4, 5], 3)[-1], 100.0)
        self.assertEqual(ind.rsi([5, 4, 3, 2, 1], 3)[-1], 0.0)
        self.assertEqual(ind.rsi([1, 1, 1, 1], 2)[-1], 50.0)

    def test_donchian_excludes_current_bar(self):
        c = bars([10, 11, 12, 20])
        up, lo = ind.donchian(c, 3)
        self.assertEqual(up[3], 12)   # bar 3's own high (20) is not included
        self.assertEqual(lo[3], 10)
        self.assertIsNone(up[2])

    def test_atr(self):
        c = bars([10, 11, 10, 11, 10], spread=0.5)
        a = ind.atr(c, 3)
        self.assertIsNone(a[1])
        self.assertTrue(all(x > 0 for x in a[2:]))


if __name__ == "__main__":
    unittest.main()
