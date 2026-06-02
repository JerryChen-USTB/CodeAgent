"""Framework adapters for external tool output."""

from codeagent.adapters.pytest_adapter import PytestResultParser
from codeagent.adapters.test_result import TestFailure, TestResult
from codeagent.adapters.unittest_adapter import UnittestResultParser

__all__ = [
    "PytestResultParser",
    "TestFailure",
    "TestResult",
    "UnittestResultParser",
]
