# QuixBugs: repair `gcd`

## Task Type

Testing + debugging + repair.

## Context

`workspace/gcd.py` is a buggy Python program from QuixBugs. It contains a small logic defect that makes the provided tests fail.

## Program Specification

```text
Input:
    a: A nonnegative int
    b: A nonnegative int


Greatest Common Divisor

Precondition:
    isinstance(a, int) and isinstance(b, int)

Output:
    The greatest int that divides evenly into a and b

Example:
    >>> gcd(35, 21)
    7
```

## Agent Task

- Run the test command to reproduce the failure.
- Locate the faulty code in `workspace/gcd.py`.
- Produce a minimal repair patch.
- Do not modify files under `workspace/tests/`.
- Run the test command again and make all tests pass.
