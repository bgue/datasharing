"""Rebalance-timing phase offsets cover exactly one rebalance period.

    python -m pytest screenlab/tests/test_timing.py -q
"""
from __future__ import annotations

import pandas as pd
import pytest

from screenlab import Lab
from screenlab.timing import phase_offsets


class _Cal:
    """Just enough of a Market for phase_offsets: an index and trading_day."""
    def __init__(self, index):
        self.index = index

    def trading_day(self, d):
        return self.index[min(self.index.searchsorted(pd.Timestamp(d)), len(self.index) - 1)]


@pytest.mark.parametrize("n_days, stride, expect", [(253, 21, 12), (261, 21, 12), (252, 21, 12), (253, 1, 253),
                                                   (253, 100, 3), (10, 7, 1), (14, 7, 2)])
def test_a_period_that_is_not_a_multiple_of_the_stride_adds_no_near_copy_of_offset_0(n_days, stride, expect):
    start = pd.Timestamp("2020-01-02")
    end = start + pd.DateOffset(months=12)
    # exactly n_days index entries in [start, start + 12 months), then the day it ends on and one more
    idx = pd.DatetimeIndex([start + pd.Timedelta(hours=i) for i in range(n_days)]).append(
        pd.DatetimeIndex([end, end + pd.Timedelta(days=5)]))
    ks = phase_offsets(_Cal(idx), start, 12, stride)
    assert len(ks) == expect and ks[0] == 0 and max(ks) < n_days


def test_daily_data_twelve_month_rebalance_gives_twelve_variants():
    lab = Lab.synthetic()                                         # weekday calendar: 261 trading days a year
    tr = lab.timing("dogs", stride=21, rebalance_months=12)
    assert len(tr.table) == 12 and list(tr.table.index) == list(range(0, 252, 21))
    d = pd.Series(pd.to_datetime(tr.table["anniversary"]))
    # no variant is a full period after offset 0: the anniversaries of offsets > 0 are all within the first year
    assert (d.iloc[1:] < d.iloc[0]).all()
    mk = lab.market
    start = mk.trading_day(mk.index[0] + pd.DateOffset(months=13))
    assert phase_offsets(mk, start, 12, 21) == list(tr.table.index)


def test_monthly_data_twelve_offsets_for_twelve_months():
    lab = Lab.synthetic(freq="ME")
    tr = lab.timing("dogs", stride=1, rebalance_months=12)
    assert list(tr.table.index) == list(range(12))
    tr6 = lab.timing("dogs", stride=1, rebalance_months=6)
    assert list(tr6.table.index) == list(range(6))
