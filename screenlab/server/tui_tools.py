"""MCP tools that steer a running screenlab TUI through its control endpoint (DESIGN §L4, tui/PROTOCOL.md).

Thin forwarders: no analysis, no new REST routes. Use the compute tools (`backtest`, `timing`, `montecarlo`...)
to compute without a UI; use `tui_*` to steer, or read, what the user sees in the terminal UI.
Start the TUI with `screenlab-tui --control-port 8765`; env `SCREENLAB_TUI_URL` (default
http://127.0.0.1:8765) and `SCREENLAB_TUI_TOKEN` (sent as `Authorization: Bearer`) configure the client.
"""
from __future__ import annotations

import os

from ..errors import ServerError

DEFAULT_URL = "http://127.0.0.1:8765"
LAUNCH = "screenlab-tui --control-port 8765"
CONNECT_TIMEOUT, READ_TIMEOUT = 2.0, 30.0


class TuiClient:
    """HTTP client of the TUI control endpoint. Failures leave as ServerError."""

    def __init__(self, base_url: str | None = None, token: str | None = None):
        self.base_url = (base_url or os.environ.get("SCREENLAB_TUI_URL") or DEFAULT_URL).rstrip("/")
        self.token = token if token is not None else os.environ.get("SCREENLAB_TUI_TOKEN")

    def _request(self, method: str, path: str, body: dict | None = None, timeout: float | None = None):
        import httpx
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        t = httpx.Timeout(timeout or READ_TIMEOUT, connect=CONNECT_TIMEOUT)
        try:
            r = httpx.request(method, self.base_url + path, json=body, headers=headers, timeout=t)
        except httpx.TimeoutException:
            raise ServerError(504, f"the TUI at {self.base_url} did not answer in time") from None
        except httpx.TransportError as e:
            raise ServerError(503, f"no screenlab TUI reachable at {self.base_url} ({type(e).__name__}). "
                                   f"Start it with `{LAUNCH}` (or set SCREENLAB_TUI_URL).") from None
        try:
            data = r.json()
        except ValueError:
            data = None
        if r.status_code >= 400:
            msg = data.get("error") if isinstance(data, dict) and "error" in data else r.text[:200]
            if r.status_code == 401:
                msg = f"{msg} (set SCREENLAB_TUI_TOKEN to the token the TUI was started with)"
            raise ServerError(r.status_code, str(msg))
        return data

    def health(self) -> dict:
        return self._request("GET", "/health")

    def state(self) -> dict:
        return self._request("GET", "/state")

    def view(self) -> dict:
        return self._request("GET", "/view")

    def command(self, cmd: str, args: dict | None = None, wait_timeout: float | None = None) -> dict:
        t = (wait_timeout + 15) if wait_timeout else None
        return self._request("POST", "/command", {"cmd": cmd, "args": args or {}}, timeout=t)


def _drop_none(**kw) -> dict:
    return {k: v for k, v in kw.items() if v is not None}


def build_tui_tools(client: TuiClient | None = None) -> dict:
    """The tui_* tools as plain functions (same convention as `build_tools`). The client is created per
    call from the environment unless one is given, so env changes are honoured."""
    def cl() -> TuiClient:
        return client or TuiClient()

    def _wait_t(wait, timeout):
        return (timeout if timeout is not None else 120.0) if wait else None

    def tui_status() -> dict:
        """Is a screenlab TUI running and reachable? Returns {running: true, ...health} or {running: false,
        hint}. Call this first. Use tui_* tools to steer or read what the user sees in the terminal UI; to
        compute without a UI use the compute tools (backtest, timing, montecarlo, ...)."""
        c = cl()
        try:
            return {"running": True, "url": c.base_url, **c.health()}
        except ServerError as e:
            if e.status == 503:
                return {"running": False, "url": c.base_url, "hint": e.message}
            raise

    def tui_state() -> dict:
        """The whole TUI state (scenarios with versions, colours, status and metrics, jobs, active tab, Monte
        Carlo settings, analyses, log). Use it to see what the user has set up; it changes nothing."""
        return cl().state()

    def tui_view() -> dict:
        """Plain-text rendering of the active TUI tab ("read the screen"): {tab, view}. Use it to see what the
        user sees; use tui_state for structured data."""
        return cl().view()

    def tui_command(cmd: str, args: dict | None = None) -> dict:
        """Send any control command to the TUI: {cmd, args}. Commands: add_scenario, clone_scenario, set_params,
        remove_scenario, run, compare, timing, montecarlo, exit_eval, picks, show, select, save_spec, export,
        history, jobs, cancel, set_theme, snapshot, view (see tui/PROTOCOL.md). Prefer the named tui_*
        wrappers for the common moves. Pass wait=true in args to block until jobs finish (optional `timeout`)."""
        a = dict(args or {})
        wt = _wait_t(a.get("wait"), a.get("timeout"))
        return cl().command(cmd, a, wt)

    def tui_add_scenario(screen: str | dict, name: str | None = None, params: dict | None = None,
                         run: bool = False) -> dict:
        """Add a scenario to the TUI and optionally run it. screen: a screen name or an inline ScreenSpec
        object; params: any of n, rebalance_months, cost_bps, commission, mer, cash_rate, exit, start, end, lab.
        Use this to put a portfolio in front of the user; to just compute numbers use the backtest tool."""
        return cl().command("add_scenario", {**(params or {}), **_drop_none(screen=screen, name=name), "run": run})

    def tui_set_params(params: dict, scenario: str | None = None, run: bool = False) -> dict:
        """Change parameters of a scenario: creates the next version (`dogs v2`) and keeps the old one, so the
        refinement is compared with what it replaced. params: same keys as tui_add_scenario (null clears one).
        scenario: id, label or name (default: the selected one). Returns the diff line."""
        return cl().command("set_params", {**_drop_none(scenario=scenario), "params": params, "run": run})

    def tui_run(scenario: str | list[str] | None = None, ci: bool = False, force: bool = False,
                wait: bool = False, timeout: float | None = None) -> dict:
        """Run scenario(s) in the TUI: a scenario reference, a list, or "all" (default: the selected one).
        ci=true also computes the bootstrap CI of Excess/yr; wait=true blocks until done (timeout seconds,
        default 120). Without wait, poll tui_state."""
        return cl().command("run", {**_drop_none(scenario=scenario, timeout=timeout), "ci": ci, "force": force,
                                    "wait": wait}, _wait_t(wait, timeout))

    def tui_compare(scenarios: list[str] | None = None, ci: bool = False) -> dict:
        """The Compare table the TUI shows (passive index first, then each scenario version): metric columns,
        rows with colours, missing results and version diffs. Read-only; run the scenarios first."""
        return cl().command("compare", {**_drop_none(scenarios=scenarios), "ci": ci})

    def tui_show(tab: str) -> dict:
        """Switch the visible tab: compare, curves, timing, picks, exit, montecarlo, runs or log."""
        return cl().command("show", {"tab": tab})

    def tui_montecarlo(scenarios: list[str] | None = None, n_paths: int | None = None,
                       horizon_years: list[int] | None = None, generator: dict | None = None,
                       seed: int | None = None, wait: bool = False, timeout: float | None = None) -> dict:
        """Run Monte Carlo for scenarios in the TUI (settings are remembered by the TUI). The quantile table
        and fan appear on its Monte Carlo tab and in tui_state analyses.montecarlo. Without a UI use the
        montecarlo tool."""
        return cl().command("montecarlo", {**_drop_none(scenarios=scenarios, n_paths=n_paths,
                            horizon_years=horizon_years, generator=generator, seed=seed, timeout=timeout),
                            "wait": wait}, _wait_t(wait, timeout))

    def tui_export(path: str | None = None, format: str | None = None) -> dict:
        """Export the Compare table (markdown with the data card and warnings, and/or CSV) from the TUI.
        format: markdown, csv or both (default). Returns the written files."""
        return cl().command("export", _drop_none(path=path, format=format))

    fns = [tui_status, tui_state, tui_view, tui_command, tui_add_scenario, tui_set_params, tui_run,
           tui_compare, tui_show, tui_montecarlo, tui_export]
    return {f.__name__: f for f in fns}
