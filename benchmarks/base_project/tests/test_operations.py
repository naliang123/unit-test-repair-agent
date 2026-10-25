import pytest

from numberkit.operations import (
    chunked,
    clamp,
    cumulative_sum,
    discount_price,
    every_other,
    fibonacci,
    max_difference,
    moving_average,
    normalize_whitespace,
    parse_score,
)


def test_clamp_bounds_values():
    assert clamp(12, 0, 10) == 10
    assert clamp(-2, 0, 10) == 0
    assert clamp(5, 5, 5) == 5


def test_clamp_rejects_reversed_bounds():
    with pytest.raises(ValueError):
        clamp(1, 10, 0)


def test_discount_price_applies_percentage_and_rounding():
    assert discount_price(199.99, 25) == 149.99
    assert discount_price(10.546, 0) == 10.55
    assert discount_price(80, 100) == 0


def test_discount_price_rejects_invalid_percentage():
    with pytest.raises(ValueError):
        discount_price(100, 101)


def test_moving_average_handles_exact_and_partial_windows():
    assert moving_average([1, 2, 3], 3) == [2]
    assert moving_average([1, 2, 3, 4], 2) == [1.5, 2.5, 3.5]
    assert moving_average([1], 2) == []


def test_moving_average_rejects_zero_window():
    with pytest.raises(ValueError):
        moving_average([1, 2], 0)


def test_chunked_preserves_order_and_tail():
    assert chunked([1, 2, 3, 4, 5], 2) == [[1, 2], [3, 4], [5]]
    assert chunked([1, 2], 1) == [[1], [2]]


def test_chunked_rejects_zero_size():
    with pytest.raises(ValueError, match="size must be positive"):
        chunked([1], 0)


def test_fibonacci_includes_the_nth_item():
    assert fibonacci(0) == [0]
    assert fibonacci(1) == [0, 1]
    assert fibonacci(6) == [0, 1, 1, 2, 3, 5, 8]


def test_fibonacci_rejects_negative_n():
    with pytest.raises(ValueError):
        fibonacci(-1)


def test_parse_score_accepts_numeric_strings_and_boundaries():
    assert parse_score("95.5") == 95.5
    assert parse_score(0) == 0
    assert parse_score(100) == 100


def test_parse_score_normalizes_invalid_input():
    with pytest.raises(ValueError, match="invalid score"):
        parse_score("not-a-number")
    with pytest.raises(ValueError, match="invalid score"):
        parse_score(None)


def test_parse_score_rejects_out_of_range_values():
    with pytest.raises(ValueError, match="out of range"):
        parse_score(100.1)


def test_max_difference_uses_minimum_and_maximum():
    assert max_difference([2, 8, 3]) == 6
    assert max_difference([5, 9]) == 4
    assert max_difference([4]) == 0


def test_normalize_whitespace_collapses_all_runs():
    assert normalize_whitespace("  alpha\n beta\t\tgamma  ") == "alpha beta gamma"


def test_every_other_honors_start_offset():
    assert every_other([1, 2, 3, 4, 5]) == [1, 3, 5]
    assert every_other([1, 2, 3, 4], start=1) == [2, 4]


def test_cumulative_sum_returns_each_prefix_total():
    assert cumulative_sum([1, 2, 3]) == [1, 3, 6]
    assert cumulative_sum([]) == []
