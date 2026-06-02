# QuixBugs: repair `find_in_sorted`

## Task Type

Testing + debugging + repair.

## Context

`workspace/find_in_sorted.py` is a buggy Python program from QuixBugs. It contains a small logic defect that makes the provided tests fail.

## Program Specification

```text
Binary Search

Input:
    arr: A sorted list of ints
    x: A value to find

Output:
    An index i such that arr[i] == x, or -1 if x not in arr

Example:
    >>> find_in_sorted([3, 4, 5, 5, 5, 5, 6], 5)
    3
```

## Agent Task

- Run the test command to reproduce the failure.
- Locate the faulty code in `workspace/find_in_sorted.py`.
- Produce a minimal repair patch.
- Do not modify files under `workspace/tests/`.
- Run the test command again and make all tests pass.
