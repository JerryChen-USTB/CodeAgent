# HumanEval/1: separate_paren_groups

## Task Type

Implementation + testing.

## Agent Task

Implement `separate_paren_groups` in `workspace/solution.py`. Keep the function name, signature, imports, and docstring compatible with the provided skeleton.

## Original Prompt

```python
from typing import List


def separate_paren_groups(paren_string: str) -> List[str]:
    """ Input to this function is a string containing multiple groups of nested parentheses. Your goal is to
    separate those group into separate strings and return the list of those.
    Separate groups are balanced (each open brace is properly closed) and not nested within each other
    Ignore any spaces in the input string.
    >>> separate_paren_groups('( ) (( )) (( )( ))')
    ['()', '(())', '(()())']
    """
```

## Acceptance Conditions

- `solution.py` can be imported by Python.
- `separate_paren_groups` satisfies the behavior described by the prompt and examples.
- Do not modify files under `evaluation/`.
