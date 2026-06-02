# Failing Test List

These tests reproduce and validate the `find_in_sorted` defect.

- test_case_0: `find_in_sorted([[3, 4, 5, 5, 5, 5, 6], 5]) == 3`
- test_case_1: `find_in_sorted([[1, 2, 3, 4, 6, 7, 8], 5]) == -1`
- test_case_2: `find_in_sorted([[1, 2, 3, 4, 6, 7, 8], 4]) == 3`
- test_case_3: `find_in_sorted([[2, 4, 6, 8, 10, 12, 14, 16, 18, 20], 18]) == 8`
- test_case_4: `find_in_sorted([[3, 5, 6, 7, 8, 9, 12, 13, 14, 24, 26, 27], 0]) == -1`
- test_case_5: `find_in_sorted([[3, 5, 6, 7, 8, 9, 12, 12, 14, 24, 26, 27], 12]) == 6`
- test_case_6: `find_in_sorted([[24, 26, 28, 50, 59], 101]) == -1`
