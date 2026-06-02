import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from find_in_sorted import find_in_sorted

TEST_CASES = [
  [
    [
      [
        3,
        4,
        5,
        5,
        5,
        5,
        6
      ],
      5
    ],
    3
  ],
  [
    [
      [
        1,
        2,
        3,
        4,
        6,
        7,
        8
      ],
      5
    ],
    -1
  ],
  [
    [
      [
        1,
        2,
        3,
        4,
        6,
        7,
        8
      ],
      4
    ],
    3
  ],
  [
    [
      [
        2,
        4,
        6,
        8,
        10,
        12,
        14,
        16,
        18,
        20
      ],
      18
    ],
    8
  ],
  [
    [
      [
        3,
        5,
        6,
        7,
        8,
        9,
        12,
        13,
        14,
        24,
        26,
        27
      ],
      0
    ],
    -1
  ],
  [
    [
      [
        3,
        5,
        6,
        7,
        8,
        9,
        12,
        12,
        14,
        24,
        26,
        27
      ],
      12
    ],
    6
  ],
  [
    [
      [
        24,
        26,
        28,
        50,
        59
      ],
      101
    ],
    -1
  ]
]

class TestFindInSorted(unittest.TestCase):
    def test_case_0(self):
        args, expected = TEST_CASES[0]
        self.assertEqual(find_in_sorted(*args), expected)
    def test_case_1(self):
        args, expected = TEST_CASES[1]
        self.assertEqual(find_in_sorted(*args), expected)
    def test_case_2(self):
        args, expected = TEST_CASES[2]
        self.assertEqual(find_in_sorted(*args), expected)
    def test_case_3(self):
        args, expected = TEST_CASES[3]
        self.assertEqual(find_in_sorted(*args), expected)
    def test_case_4(self):
        args, expected = TEST_CASES[4]
        self.assertEqual(find_in_sorted(*args), expected)
    def test_case_5(self):
        args, expected = TEST_CASES[5]
        self.assertEqual(find_in_sorted(*args), expected)
    def test_case_6(self):
        args, expected = TEST_CASES[6]
        self.assertEqual(find_in_sorted(*args), expected)


if __name__ == "__main__":
    unittest.main()
