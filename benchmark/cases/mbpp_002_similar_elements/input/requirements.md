# MBPP 2: similar_elements

## Task Type

Implementation + testing.

## Agent Task

Write a function to find the shared elements from the given two lists.

Implement `similar_elements` in `workspace/solution.py`. Keep the function name and parameter list compatible with the skeleton.

## Acceptance Assertions

- `assert set(similar_elements((3, 4, 5, 6),(5, 7, 4, 10))) == set((4, 5))`
- `assert set(similar_elements((1, 2, 3, 4),(5, 4, 3, 7))) == set((3, 4))`
- `assert set(similar_elements((11, 12, 14, 13),(17, 15, 14, 13))) == set((13, 14))`

## Acceptance Conditions

- `solution.py` can be imported by Python.
- The function satisfies the prompt and assertions.
- Do not modify files under `evaluation/`.
