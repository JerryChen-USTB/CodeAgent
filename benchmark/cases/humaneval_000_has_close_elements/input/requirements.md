# HumanEval/0: has_close_elements

## Task Type

Implementation + testing.

## Agent Task

Implement `has_close_elements` in `workspace/solution.py`. Keep the function name, signature, imports, and docstring compatible with the provided skeleton.

## Original Prompt

```python
from typing import List


def has_close_elements(numbers: List[float], threshold: float) -> bool:
    """ Check if in given list of numbers, are any two numbers closer to each other than
    given threshold.
    >>> has_close_elements([1.0, 2.0, 3.0], 0.5)
    False
    >>> has_close_elements([1.0, 2.8, 3.0, 4.0, 5.0, 2.0], 0.3)
    True
    """
```

## Acceptance Conditions

- `solution.py` can be imported by Python.
- `has_close_elements` satisfies the behavior described by the prompt and examples.
- Do not modify files under `evaluation/`.
