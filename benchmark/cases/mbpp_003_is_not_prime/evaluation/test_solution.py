import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "workspace"))
from solution import *


class TestMBPP003(unittest.TestCase):
    def test_case_0(self):
        assert is_not_prime(2) == False
    def test_case_1(self):
        assert is_not_prime(10) == True
    def test_case_2(self):
        assert is_not_prime(35) == True
    def test_case_3(self):
        assert is_not_prime(37) == False


if __name__ == "__main__":
    unittest.main()
