import numpy as np
from scipy.stats import wasserstein_distance, ks_2samp
import matplotlib.pyplot as plt
from dataclasses import dataclass
from typing import List
from ..common.stat_utils import (compute_distribution_cat,
                                 compute_mean_diff,
                                 wasserstein_distance_for_cat,
                                 jensen_shannon_distance,
                                 chi2_test,
                                 ks_weighted,
                                 BaseStatisticalTestResult,
                                 Chi2TestResult
                                 )


def compute_drift_num(a1: np.array, a2: np.array, sample_weights1: np.array = None, sample_weights2: np.array = None,
                      js_bins: int = 10, js_base=None):
    if (sample_weights1 is None and sample_weights2 is None or
            np.all(sample_weights1 == sample_weights1[0]) and np.all(sample_weights2 == sample_weights2[0])):
        ks_test_object = ks_2samp(a1, a2)
        ks_test = BaseStatisticalTestResult(statistic=ks_test_object.statistic,
                                                       pvalue=ks_test_object.pvalue)
    else:
        # 'ks_weighted' return a dictionnary with the good format
        ks_test = ks_weighted(a1, a2, sample_weights1, sample_weights2)
    return DriftMetricsNum(mean_difference=compute_mean_diff(a1, a2, sample_weights1, sample_weights2),
                           wasserstein=wasserstein_distance(a1, a2, sample_weights1, sample_weights2),
                           ks_test=ks_test)


def compute_drift_cat(a1: np.array, a2: np.array, sample_weights1=None, sample_weights2=None, js_base=None):
    return DriftMetricsCat(wasserstein=wasserstein_distance_for_cat(a1, a2, sample_weights1, sample_weights2),
                           jensen_shannon=jensen_shannon_distance(a1, a2, js_base, sample_weights1, sample_weights2),
                           chi2_test=chi2_test(a1, a2, sample_weights1, sample_weights2))


def plot_drift_cat(a1: np.array, a2: np.array, sample_weights1=None, sample_weights2=None, title=None,
                   max_n_cat: int = None, figsize=(10, 6)):
    # compute both distributions
    distrib = compute_distribution_cat(a1, a2, sample_weights1, sample_weights2, max_n_cat)
    bar_height = np.array([v for v in distrib.values()])  # len(distrib) rows and 2 columns

    # plot
    index = np.arange(len(distrib))
    bar_width = 0.35
    fig, ax = plt.subplots(figsize=figsize)
    ax.bar(index, bar_height[:, 0], bar_width, label="Dataset 1")
    ax.bar(index + bar_width, bar_height[:, 1], bar_width, label="Dataset 2")

    ax.set_xlabel('Category')
    ax.set_ylabel('Percentage')
    ax.set_title(title)
    ax.set_xticks(index + bar_width / 2)
    ax.set_xticklabels(list(distrib.keys()), rotation=30)
    ax.legend()
    plt.show()


def plot_drift_num(a1: np.array, a2: np.array, sample_weights1: np.array = None, sample_weights2: np.array = None,
                   title=None, figsize=(7, 5), bins=10):
    # distrib = compute_distribution_num(a1, a2, sample_weights1, sample_weights2)
    fig, ax = plt.subplots(figsize=figsize)
    ax.hist(a1, bins=bins, density=True, weights=sample_weights1, alpha=0.3)
    ax.hist(a2, bins=bins, density=True, weights=sample_weights2, alpha=0.3)
    ax.legend(['Dataset 1', 'Dataset 2'])
    plt.title(title)
    plt.show()


@dataclass
class AbstractDriftMetrics:
    def assert_equal(self, other) -> None:
        pass


@dataclass
class DriftMetricsNum(AbstractDriftMetrics):
    mean_difference: float
    wasserstein: float
    ks_test: BaseStatisticalTestResult

    def assert_equal(self, other) -> None:
        assert isinstance(other, DriftMetricsNum)
        assert self.mean_difference == other.mean_difference
        assert self.wasserstein == other.wasserstein
        self.ks_test.assert_equal(other.ks_test)


@dataclass
class DriftMetricsCat(AbstractDriftMetrics):
    wasserstein: float
    jensen_shannon: float
    chi2_test: Chi2TestResult

    def assert_equal(self, other) -> None:
        assert isinstance(other, DriftMetricsCat)
        assert self.wasserstein == other.wasserstein
        assert self.jensen_shannon == other.jensen_shannon
        self.chi2_test.assert_equal(other.chi2_test)


def assert_drift_metrics_equal(drift_metrics1: AbstractDriftMetrics,
                               drift_metrics2: AbstractDriftMetrics) -> None:
    drift_metrics1.assert_equal(drift_metrics2)


def assert_drift_metrics_list_equal(l1: List[AbstractDriftMetrics], l2: List[AbstractDriftMetrics]) -> None:
    assert len(l1) == len(l2)
    for i in range(len(l1)):
        assert_drift_metrics_equal(l1[i], l2[i])
