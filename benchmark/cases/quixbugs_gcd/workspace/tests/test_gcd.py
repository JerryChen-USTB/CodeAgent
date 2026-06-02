import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gcd import gcd

TEST_CASES = [
  [
    [
      17,
      0
    ],
    17
  ],
  [
    [
      13,
      13
    ],
    13
  ],
  [
    [
      37,
      600
    ],
    1
  ],
  [
    [
      20,
      100
    ],
    20
  ],
  [
    [
      624129,
      2061517
    ],
    18913
  ],
  [
    [
      3,
      12
    ],
    3
  ]
]

class TestGcd(unittest.TestCase):
    def test_case_0(self):
        args, expected = TEST_CASES[0]
        self.assertEqual(gcd(*args), expected)
    def test_case_1(self):
        args, expected = TEST_CASES[1]
        self.assertEqual(gcd(*args), expected)
    def test_case_2(self):
        args, expected = TEST_CASES[2]
        self.assertEqual(gcd(*args), expected)
    def test_case_3(self):
        args, expected = TEST_CASES[3]
        self.assertEqual(gcd(*args), expected)
    def test_case_4(self):
        args, expected = TEST_CASES[4]
        self.assertEqual(gcd(*args), expected)
    def test_case_5(self):
        args, expected = TEST_CASES[5]
        self.assertEqual(gcd(*args), expected)


if __name__ == "__main__":
    unittest.main()
