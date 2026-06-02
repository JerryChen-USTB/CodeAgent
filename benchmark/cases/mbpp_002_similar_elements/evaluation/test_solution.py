import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "workspace"))
from solution import *


class TestMBPP002(unittest.TestCase):
    def test_case_0(self):
        assert set(similar_elements((3, 4, 5, 6),(5, 7, 4, 10))) == set((4, 5))
    def test_case_1(self):
        assert set(similar_elements((1, 2, 3, 4),(5, 4, 3, 7))) == set((3, 4))
    def test_case_2(self):
        assert set(similar_elements((11, 12, 14, 13),(17, 15, 14, 13))) == set((13, 14))


if __name__ == "__main__":
    unittest.main()
