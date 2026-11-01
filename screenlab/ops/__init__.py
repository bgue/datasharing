"""The built-in operations, one module each; importing this package registers them (operations.py)."""
from . import (account_compare, backtest, exit_calibrate, exit_evaluate, extended, fetch_data, graham, montecarlo, picks,  # noqa: F401
               plan, screen_builder, significance, sweep, timing)
