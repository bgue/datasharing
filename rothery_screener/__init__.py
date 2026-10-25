"""Multi-portfolio stock screener and backtester modelled on the screens
Norman Rothery writes up in The Globe and Mail (and on StingyInvestor.com).

Layout:
    data.py      point-in-time panel loading + a synthetic market for testing
    screens.py   one function per screen, plus a registry
    backtest.py  periodic-rebalance, equal-weight portfolio simulator
    metrics.py   CAGR / volatility / drawdown / hit-rate stats
    run.py       CLI: backtest every screen side by side, print today's picks
"""
