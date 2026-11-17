"""Bootstrap confidence intervals for per-item eval scores (docs/eval-strategy.md §5)."""

from collections.abc import Sequence

import numpy as np

SEED = 0


def bootstrap_ci(values: Sequence[float], n: int = 2000) -> tuple[float, float, float]:
    """(mean, lo, hi): the mean and its 95% percentile interval over n resamples with replacement."""
    x = np.asarray(values, dtype=float)
    idx = np.random.default_rng(SEED).integers(0, len(x), size=(n, len(x)))
    means = x[idx].mean(axis=1)
    return float(x.mean()), float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def paired_bootstrap_diff(a: Sequence[float], b: Sequence[float], n: int = 2000) -> tuple[float, float, float]:
    """(diff, lo, hi): mean of b − a and its 95% interval; each resample uses the same item indices for a and b."""
    x, y = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    if x.shape != y.shape:
        raise ValueError("a and b must score the same items")
    return bootstrap_ci(y - x, n)


def bootstrap_ratio_ci(num: Sequence[float], den: Sequence[float], n: int = 2000) -> tuple[float, float, float]:
    """(Σnum / Σden, lo, hi), resampling whole cases: num[i], den[i] are case i's numerator and denominator.

    Items of one case are not independent, so cases (not items) are resampled. A resample with Σden = 0 is skipped.
    """
    x, y = np.asarray(num, dtype=float), np.asarray(den, dtype=float)
    if x.shape != y.shape:
        raise ValueError("num and den must have one value per case")
    idx = np.random.default_rng(SEED).integers(0, len(x), size=(n, len(x)))
    sx, sy = x[idx].sum(axis=1), y[idx].sum(axis=1)
    ratios = sx[sy > 0] / sy[sy > 0]
    return float(x.sum() / y.sum()), float(np.percentile(ratios, 2.5)), float(np.percentile(ratios, 97.5))


def paired_bootstrap_ratio_diff(num_a: Sequence[float], den_a: Sequence[float], num_b: Sequence[float],
                                den_b: Sequence[float], n: int = 2000) -> tuple[float, float, float]:
    """(ratio_b − ratio_a, lo, hi); each resample uses the same case indices for a and b."""
    arrays = [np.asarray(v, dtype=float) for v in (num_a, den_a, num_b, den_b)]
    if len({a.shape for a in arrays}) != 1:
        raise ValueError("a and b must score the same cases")
    xa, ya, xb, yb = arrays
    idx = np.random.default_rng(SEED).integers(0, len(xa), size=(n, len(xa)))
    sa, ta, sb, tb = (v[idx].sum(axis=1) for v in arrays)
    ok = (ta > 0) & (tb > 0)
    diffs = sb[ok] / tb[ok] - sa[ok] / ta[ok]
    return (float(xb.sum() / yb.sum() - xa.sum() / ya.sum()),
            float(np.percentile(diffs, 2.5)), float(np.percentile(diffs, 97.5)))
