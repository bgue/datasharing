"""MCP server (stdio) over a LabRegistry. Run: `python -m screenlab.server.mcp_server`.

Works with the `mcp` package's FastMCP (mcp 1.x: mcp.server.fastmcp) and its renamed MCPServer (mcp 2.x).
`build_tools(registry)` returns plain functions (one per REST endpoint, same names/JSON shapes) so tests
and other hosts can call them in-process; `create_server` registers them with the MCP framework.
"""
from __future__ import annotations

from .registry import DEFAULT_MAX_POINTS, LabRegistry, ServerError, default_registry


def build_tools(reg: LabRegistry | None = None) -> dict:
    reg = reg or default_registry()

    def list_labs() -> dict:
        """List the named labs (datasets) and the default one."""
        return reg.list_labs()

    def list_screens(lab: str | None = None) -> dict:
        """Screens defined in a lab: name, n, note, parameters."""
        return reg.list_screens(lab)

    def define_screen(spec: dict, lab: str | None = None) -> dict:
        """Define a screen from a ScreenSpec JSON object: {name, n, universe, derive, filters, graham, rank}."""
        return reg.define_screen(spec, lab)

    def picks(screen: str, asof: str | None = None, lab: str | None = None) -> dict:
        """Names a screen selects on a date (default: latest), with key ratios."""
        return reg.picks(screen, asof, lab)

    def backtest(screens: list[str] | None = None, start: str | None = None, end: str | None = None,
                 config: dict | None = None, lab: str | None = None, max_points: int = DEFAULT_MAX_POINTS,
                 exit: dict | None = None, with_and_without: bool = False, benchmark: str | None = None,
                 compare: list[str] | None = None) -> dict:
        """Backtest screens against the passive index (response key "benchmark" names it). `benchmark`: an
        index name replacing it; `compare`: extra rows (index names or "top60"), never the primary benchmark.
        config overrides: rebalance_months, cost_bps, commission,
        capital, mer, delist_return, min_holdings, cash_rate. Returns the metrics table and downsampled curves.
        exit: {"signals": [...], "threshold": .., "park": "cash", "reentry": "signal", "reentry_delay_days": ..,
        "reentry_steps": .., ...} applies an exit policy to every screen; with_and_without also runs them without."""
        return reg.backtest(screens, start, end, config, lab, max_points, exit, with_and_without, benchmark, compare)

    def timing(screen: str, mode: str = "phase", stride: int = 1, start: str | None = None,
               end: str | None = None, config: dict | None = None, lab: str | None = None,
               max_points: int = DEFAULT_MAX_POINTS) -> dict:
        """Rebalance-timing study: how much the result depends on the start date / phase."""
        return reg.timing(screen, mode, stride, start, end, config, lab, max_points)

    def graham(preset: str = "defensive", asof: str | None = None, overrides: dict | None = None,
               lab: str | None = None) -> dict:
        """Graham tests: per-name pass table and cumulative funnel. overrides are GrahamTests fields."""
        return reg.graham(preset, asof, overrides, lab)

    def sweep(screen: str, grid: dict, objective: str = "Excess/yr", start: str | None = None,
              end: str | None = None, splits: dict | None = None, lab: str | None = None) -> dict:
        """Parameter sweep. grid: {"dogs.n": [5, 10], "cost_bps": [10, 50]} (dotted = screen, bare = Config).
        splits {train_years, test_years} adds walk-forward out-of-sample columns."""
        return reg.sweep(screen, grid, objective, start, end, splits, lab)

    def significance(screens: list[str] | None = None, start: str | None = None, end: str | None = None,
                     config: dict | None = None, n: int = 2000, seed: int = 0, lab: str | None = None) -> dict:
        """Bootstrap CI, t-stat and deflated Sharpe for screens' excess return over the benchmark."""
        return reg.significance(screens, start, end, config, n, seed, lab)

    def extended(macro, screens: list[str] | None = None, start: str | None = None, end: str | None = None,
                 config: dict | None = None, spread_over_prime: float = 0.0, leverage: float = 1.0,
                 lab: str | None = None) -> dict:
        """Real / FX / financing-adjusted metrics. macro: [{date, series, value}] records, a
        {series: {date: value}} mapping, or "statsmodels"."""
        return reg.extended(screens, start, end, config, macro, spread_over_prime, leverage, lab)

    def exit_evaluate(signals: list, weights: dict | None = None, threshold: float = 0.7,
                      min_votes: int | None = None, hysteresis: float = 0.1, screen_params: dict | None = None,
                      target: str | None = None, horizons: list[int] | None = None, macro=None,
                      exposure_when_flagged: float = 0.0, cash_rate: float = 0.0, lab: str | None = None,
                      max_points: int = DEFAULT_MAX_POINTS, policy: dict | None = None) -> dict:
        """Evaluate an exit screen (policy: ExitPolicy fields such as park, reentry, reentry_steps). signals: [{"type": "TrendBreak", "window_months": 10}, ...] (see list_signals)."""
        return reg.exit_evaluate(signals, weights, threshold, min_votes, hysteresis, screen_params, target,
                                 horizons or (1, 3, 12), macro, exposure_when_flagged, cash_rate, lab, max_points, policy)

    def exit_calibrate(signals: list, grid: dict, weights: dict | None = None, threshold: float = 0.7,
                       min_votes: int | None = None, hysteresis: float = 0.1, screen_params: dict | None = None,
                       objective: str = "Calmar", splits: dict | None = None, constraints: dict | None = None,
                       target: str | None = None, macro=None, lab: str | None = None,
                       policy: dict | None = None) -> dict:
        """Walk-forward calibration of an exit screen (needs Lab.calibrate_exit; errors with 501 otherwise)."""
        return reg.exit_calibrate(signals, grid, weights, threshold, min_votes, hysteresis, screen_params,
                                  objective, splits, constraints, target, macro, lab, policy)

    def describe_columns(lab: str | None = None) -> dict:
        """The fundamentals / snapshot column contract usable in filters, derive and rank."""
        return reg.describe_columns(lab)

    def list_signals() -> dict:
        """Built-in exit signals with their parameters and defaults."""
        return reg.list_signals()

    def submit_run(kind: str, args: dict | None = None, lab: str | None = None, seed: int = 0,
                   label: str | None = None, tags: list[str] | None = None, force: bool = False) -> dict:
        """Start a long run in the background and return {job_id, status, run_key, cache_hit}. kind: backtest,
        timing, sweep, significance, extended, exit_evaluate, exit_calibrate, graham or picks; args are that
        tool's arguments (e.g. {"screen": "dogs", "stride": 1} for timing). An identical finished run is
        returned from the store at once unless force=true. Poll run_status, then fetch run_result."""
        return reg.submit_run(kind, args, lab, seed, label, tags, force)

    def run_status(job_id: str) -> dict:
        """Status (queued/running/done/failed/cancelled), progress 0-1 and message of a run."""
        return reg.run_status(job_id)

    def run_result(job_id: str) -> dict:
        """The finished run's result: the same JSON the matching synchronous tool returns (error while not done)."""
        return reg.run_result(job_id)

    def cancel_run(job_id: str) -> dict:
        """Ask a queued or running run to stop."""
        return reg.cancel_run(job_id)

    def list_runs(status: str | None = None) -> dict:
        """Runs started in this server session, newest first; optionally only those with one status."""
        return reg.list_runs(status)

    fns = [list_labs, list_screens, define_screen, picks, backtest, timing, graham, sweep, significance,
           extended, exit_evaluate, exit_calibrate, describe_columns, list_signals,
           submit_run, run_status, run_result, cancel_run, list_runs]
    return {f.__name__: f for f in fns}


def create_server(reg: LabRegistry | None = None):
    try:
        from mcp.server.fastmcp import FastMCP as Server          # mcp 1.x
    except ImportError:
        from mcp.server.mcpserver import MCPServer as Server      # mcp 2.x
    srv = Server("screenlab")
    for name, fn in build_tools(reg).items():
        srv.tool(name=name)(_wrap(fn))
    return srv


def _wrap(fn):
    """Turn ServerError into a plain exception message so the MCP client sees it as a tool error."""
    import functools

    @functools.wraps(fn)
    def inner(*a, **kw):
        try:
            return fn(*a, **kw)
        except ServerError as e:
            raise ValueError(f"[{e.status}] {e.message}") from None
    return inner


def main():
    create_server().run()            # stdio transport


if __name__ == "__main__":
    main()
