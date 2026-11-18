import pytest

from evals.common.stats import bootstrap_ci, bootstrap_ratio_ci, paired_bootstrap_diff, paired_bootstrap_ratio_diff


def test_bootstrap_ci_constant_values_have_zero_width():
    assert bootstrap_ci([1, 1, 1, 1]) == (1.0, 1.0, 1.0)


def test_bootstrap_ci_brackets_the_mean_and_is_deterministic():
    values = [1, 0, 1, 1, 0, 1, 0, 1, 1, 1]
    mean, lo, hi = bootstrap_ci(values)
    assert mean == pytest.approx(0.7)
    assert 0.3 < lo < mean < hi <= 1.0
    assert bootstrap_ci(values) == (mean, lo, hi)


def test_paired_diff_is_b_minus_a():
    a = [0, 0, 1, 1, 0, 1]
    assert paired_bootstrap_diff(a, a) == (0.0, 0.0, 0.0)
    assert paired_bootstrap_diff(a, [x + 1 for x in a]) == (1.0, 1.0, 1.0)
    diff, lo, hi = paired_bootstrap_diff([0] * 10, [1, 0] * 5)
    assert diff == pytest.approx(0.5) and lo < diff < hi


def test_paired_diff_rejects_different_lengths():
    with pytest.raises(ValueError):
        paired_bootstrap_diff([1, 0], [1])


def test_bootstrap_ratio_ci_is_micro_average_over_cases():
    ratio, lo, hi = bootstrap_ratio_ci([3, 0, 2, 5], [4, 1, 2, 5])
    assert ratio == pytest.approx(10 / 12)
    assert lo <= ratio <= hi <= 1.0
    assert bootstrap_ratio_ci([2, 2], [2, 2]) == (1.0, 1.0, 1.0)


def test_bootstrap_ratio_ci_rejects_mismatched_lengths():
    with pytest.raises(ValueError):
        bootstrap_ratio_ci([1, 2], [3])


def test_paired_ratio_diff():
    num, den = [1, 2, 0, 3], [2, 2, 1, 4]
    assert paired_bootstrap_ratio_diff(num, den, num, den) == (0.0, 0.0, 0.0)
    diff, lo, hi = paired_bootstrap_ratio_diff([0, 0, 0, 0], den, den, den)
    assert (diff, lo, hi) == (1.0, 1.0, 1.0)
    with pytest.raises(ValueError):
        paired_bootstrap_ratio_diff([1], [1], [1, 2], [1, 2])
