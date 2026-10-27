"""The built-in operations, one module each; importing this package registers them (operations.py)."""
from . import (backtest, exit_calibrate, exit_evaluate, extended, graham, montecarlo, picks,  # noqa: F401
               significance, sweep, timing)
