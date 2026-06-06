from __future__ import annotations

import pytest
from pydantic import ValidationError

from codeagent.stages.implementation_service import ImplementationPlan
from codeagent.stages.repair_service import RepairPlan
from codeagent.stages.testing_service import TestingPlan


def test_implementation_plan_rejects_file_contents() -> None:
    with pytest.raises(ValidationError):
        ImplementationPlan.model_validate(
            {
                "requirements_summary": "Build a package.",
                "implementation_strategy": "Add a source module.",
                "changes": [
                    {
                        "path": "pkg/app.py",
                        "rationale": "Needed by the feature.",
                        "old_content": "",
                        "new_content": "def run():\n    return True\n",
                    }
                ],
                "acceptance_criteria": ["Feature can be imported."],
            }
        )


def test_testing_plan_rejects_file_contents() -> None:
    with pytest.raises(ValidationError):
        TestingPlan.model_validate(
            {
                "target_summary": "Exercise the generated package.",
                "strategy": "Create pytest coverage.",
                "acceptance_criteria": ["Tests cover happy path."],
                "changes": [
                    {
                        "path": "tests/test_app.py",
                        "test_focus": "Happy path.",
                        "rationale": "Needed by the feature.",
                        "new_content": "def test_app():\n    assert True\n",
                    }
                ],
                "command": "python -m pytest -q",
                "framework": "pytest",
            }
        )


def test_repair_plan_rejects_file_contents() -> None:
    with pytest.raises(ValidationError):
        RepairPlan.model_validate(
            {
                "root_cause": "The command returns the wrong value.",
                "strategy": "Fix the implementation module.",
                "changes": [
                    {
                        "path": "pkg/app.py",
                        "rationale": "Correct the behavior.",
                        "expected_effect": "Regression tests pass.",
                        "old_content": "return False\n",
                        "new_content": "return True\n",
                    }
                ],
                "verification_command": "python -m pytest -q",
                "framework": "pytest",
            }
        )
