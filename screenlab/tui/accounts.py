"""Accounts tab: taxable vs TFSA vs RRSP for the selected scenario (`account_compare`, ops/account_compare.py).

# Q2: The session side (`args_for`, `store`, `view_text`) and the browser chart (`chart_data`) import no Textual, so
`session.py` and `web.py` can use them; the Textual pane is built by `pane(app)`, which imports Textual when called.

Wiring (all in one place): ui.py mounts `pane(app)` next to the Data tab; `pane()` also binds key `A` and tells the app how
to draw the tab (`app._draw_accounts`). `Session.cmd_accounts` submits the `account_compare` job with the arguments of
`args_for`, `Session._finish` keeps the result under `analyses["accounts"][<scenario label>]`, and this module registers
the chart builder with `web.BUILDERS`. The control protocol needs nothing new: `tui_command("accounts", {...})`,
`tui_show("accounts")`, `tui_view()` (see PROTOCOL.md, command `accounts`).
"""
from __future__ import annotations

KIND = "account_compare"
TAB = "accounts"
RESULT_KEYS = ("benchmark", "accounts", "table", "foreign", "best", "notes", "tax")
PCT = ("CAGR", "AfterTaxCAGR", "TaxDrag/yr", "AfterTaxExcess/yr", "WHLost/yr", "WHCredited/yr", "WHExempt/yr",
       "TaxDeferred/yr", "GapToNext")
SHORT = {"AfterTaxCAGR": "After-tax CAGR", "TaxDrag/yr": "Tax drag/yr", "AfterTaxExcess/yr": "AT excess/yr",
         "WHLost/yr": "WH lost/yr", "WHCredited/yr": "WH credited/yr", "WHExempt/yr": "WH exempt/yr",
         "DivTax$": "Div tax $", "GainTax$": "Gain tax $", "WithdrawalTax$": "RRSP w/d tax $", "TaxPaid$": "Tax paid $",
         "TaxDeferred/yr": "Harvest defer/yr", "GapToNext": "Gap to next"}
HIDDEN = ("name", "kind", "account")


# ------------------------------------------------------------------ session side (no Textual)
def args_for(session, sc, accounts=None) -> dict:
    """`account_compare` arguments for scenario `sc`: its screen, window, config and exit, with its `tax` param (if any)
    as the shared settings (without `account`: the operation varies that)."""
    a = session._bt_args(sc)
    cfg = dict(a.get("config") or {})
    tax = cfg.pop("tax", None)
    if cfg:
        a["config"] = cfg
    else:
        a.pop("config", None)
    if tax:
        a["tax"] = {k: v for k, v in dict(tax).items() if k != "account"}
    if accounts:
        a["accounts"] = list(accounts)
    return a


def store(session_analyses: dict, label: str, res: dict) -> None:
    """Keep the parts of an `account_compare` result the tab and the chart need."""
    session_analyses.setdefault(TAB, {})[label] = {k: res.get(k) for k in RESULT_KEYS}


def fmt(col: str, v) -> str:
    """Table cell text."""
    if v is None or (isinstance(v, float) and v != v):
        return ""
    if col in PCT:
        return f"{100 * v:.2f}%"
    if col.endswith("$"):
        return f"{v:,.0f}"
    if col == "Rank":
        return str(int(v))
    return str(v)


def records(res: dict) -> list:
    """The table of a stored result as a list of dicts."""
    t = res["table"]
    return [dict(zip(t["columns"], r)) for r in t["rows"]]


def display_columns(res: dict) -> list:
    """Columns shown (name / kind / account lead; empty columns such as TaxDeferred/yr without harvesting are dropped)."""
    recs = records(res)
    return [c for c in res["table"]["columns"] if c not in HIDDEN and any(r.get(c) is not None for r in recs)]


def view_text(analyses: dict, grid) -> str:
    """Plain text of the tab for `Session.view`; `grid(columns, rows)` formats a table."""
    a = analyses.get(TAB) or {}
    if not a:
        return "no account comparison yet: run the `accounts` command (key A in the TUI)"
    out = []
    for label, res in a.items():
        cols = display_columns(res)
        rows = [[f"{r['name']} | {r['account']}", *[fmt(c, r.get(c)) for c in cols]] for r in records(res)]
        out += [f"{label}: same pre-tax money in taxable, TFSA and RRSP", grid(["row", *cols], rows)]
        for name, b in res["best"].items():
            if b:
                out.append(f"best for {name}: {b['account']}" + (f" ({100 * b['gap']:+.2f} points/yr over {b['next']})"
                                                                  if b.get("next") else ""))
        fr = res["foreign"]
        if fr["rows"]:
            out += ["foreign holdings (share of positions, treatment per account)",
                    grid([c for c in fr["columns"] if c not in ("kind",)],
                         [[f"{100 * v:.0f}%" if c == "share" else (f"{100 * v:g}%" if c == "rate" else fmt(c, v))
                           for c, v in zip(fr["columns"], r) if c != "kind"]
                          for r in fr["rows"]])]
        out += [f"note: {n}" for n in res["notes"]]
    return "\n".join(out)


# ------------------------------------------------------------------ browser chart data (no Textual)
def chart_data(session) -> dict:
    """`/charts/data?tab=accounts`: after-tax CAGR by account, per scenario with a result, and the passive index."""
    from . import web
    a = session.analyses.get(TAB) or {}
    out = web._base(session, TAB, not a, "No account comparison yet: open the Accounts tab in the TUI (key A) "
                                         "or run the `accounts` command.")
    if not a:
        return out
    series, notes, accounts, tables = [], [], None, {}
    passive = None
    for label, res in a.items():
        accounts = accounts or res["accounts"]
        recs = records(res)
        port = [r for r in recs if r["kind"] == "portfolio"]
        by = {r["account"]: r for r in port}
        series.append({"label": label, "color": web._color_of(session, label), "passive": False,
                       "after_tax": [by.get(x, {}).get("AfterTaxCAGR") for x in res["accounts"]],
                       "pre_tax": [by.get(x, {}).get("CAGR") for x in res["accounts"]],
                       "wh_lost": [by.get(x, {}).get("WHLost/yr") for x in res["accounts"]]})
        if passive is None:
            idx = {r["account"]: r for r in recs if r["kind"] == "index"}
            passive = {"label": res["benchmark"], "color": web._passive_color(), "passive": True,
                       "after_tax": [idx.get(x, {}).get("AfterTaxCAGR") for x in res["accounts"]],
                       "pre_tax": [idx.get(x, {}).get("CAGR") for x in res["accounts"]],
                       "wh_lost": [idx.get(x, {}).get("WHLost/yr") for x in res["accounts"]]}
        notes += [n for n in res["notes"] if n not in notes]
        tables[label] = {"table": res["table"], "foreign": res["foreign"], "best": res["best"]}
    out.update(accounts=accounts, series=([passive] if passive else []) + series, notes=notes, results=tables,
               selected=session.selected)
    return out


def register_chart() -> None:
    """Make `/charts/data?tab=accounts` work (web.py has no knowledge of this tab)."""
    from . import web
    if TAB not in web.CHART_TABS:
        web.CHART_TABS = (*web.CHART_TABS, TAB)
    web.BUILDERS.setdefault(TAB, chart_data)


register_chart()


# ------------------------------------------------------------------ Textual pane
def pane(app):
    """The Accounts `TabPane` (mounted in ScreenlabApp.compose). Also binds key `A` and registers `app._draw_accounts`."""
    from rich.text import Text
    from textual import on
    from textual.binding import Binding
    from textual.containers import Horizontal, VerticalScroll
    from textual.widgets import Button, DataTable, Static, TabPane

    class AccountsPane(TabPane):
        DEFAULT_CSS = """
        AccountsPane #a-bar { height: auto; padding: 0 1; }
        AccountsPane #a-note { width: 1fr; padding: 1 1 0 1; color: $text-muted; }
        AccountsPane #t-accounts { height: 14; }
        AccountsPane #t-foreign { height: 8; }
        AccountsPane #a-notes { height: auto; padding: 0 1; color: $text-muted; }
        """

        def __init__(self):
            super().__init__("Accounts", id=TAB)
            self._tried: set = set()

        def compose(self):
            with VerticalScroll():
                with Horizontal(id="a-bar"):
                    yield Button("Run account comparison", id="a-run", variant="primary")
                    yield Static("", id="a-note")
                yield DataTable(id="t-accounts", cursor_type="row")
                yield Static("Foreign dividends: share of the portfolio's positions and what each account does with the withholding",
                             classes="pane-title")
                yield DataTable(id="t-foreign", cursor_type="row")
                yield Static("", id="a-notes")

        @on(Button.Pressed, "#a-run")
        def _run(self) -> None:
            self.app._cmd("accounts", {"force": True}, "accounts")

        def draw(self) -> None:
            """Repaint from the session; start the comparison once per scenario version when the tab is first looked at."""
            s = self.app.session
            note, notes = self.query_one("#a-note", Static), self.query_one("#a-notes", Static)
            sc = next((x for x in s.scenarios if x.id == s.selected), None)
            tbl, ftbl = self.query_one("#t-accounts", DataTable), self.query_one("#t-foreign", DataTable)
            if sc is None:
                note.update("Add a scenario (a) to compare accounts for its screen and settings")
                tbl.clear(columns=True)
                ftbl.clear(columns=True)
                notes.update("")
                return
            res = (s.analyses.get(TAB) or {}).get(sc.label)
            running = any(j.get("kind") == KIND for j in s.active_jobs())
            if res is None and not running and sc.label not in self._tried and s.active_tab == TAB:
                self._tried.add(sc.label)
                self.app._cmd("accounts", {}, "accounts")
                running = True
            if res is None:
                note.update(f"{sc.label}: " + ("comparing accounts..." if running else "press the button to compare the accounts"))
                tbl.clear(columns=True)
                ftbl.clear(columns=True)
                notes.update("")
                return
            note.update(f"{sc.label}: the same pre-tax portfolio in each account. Green = best account by after-tax CAGR "
                        f"for that row's portfolio; amber = foreign withholding lost.")
            cols = display_columns(res)
            recs = records(res)
            best = {n: b["account"] for n, b in res["best"].items() if b}
            tbl.clear(columns=True)
            tbl.add_columns("Portfolio", "Account", *[SHORT.get(c, c) for c in cols])
            for r in recs:
                good = best.get(r["name"]) == r["account"]
                row = [Text(r["name"], style="bold green" if good else ("dim" if r["kind"] == "index" else "bold")),
                       Text(r["account"], style="bold green" if good else "")]
                for c in cols:
                    st = "green" if good and c in ("AfterTaxCAGR", "Rank") else ""
                    if c == "WHLost/yr" and (r.get(c) or 0) > 0:
                        st = "bold dark_orange"
                    row.append(Text(fmt(c, r.get(c)), style=st, justify="right"))
                tbl.add_row(*row)
            fr = res["foreign"]
            ftbl.clear(columns=True)
            ftbl.add_columns("Portfolio", "Holding", "Share", "Rate", "Taxable", "TFSA", "RRSP")
            for r in fr["rows"]:
                d = dict(zip(fr["columns"], r))
                lost = lambda t: Text(t, style="bold dark_orange" if "LOST" in t else ("green" if "EXEMPT" in t else ""))
                ftbl.add_row(d["portfolio"], d["class"], f"{100 * d['share']:.0f}%", f"{100 * d['rate']:g}%",
                             lost(d["taxable"]), lost(d["tfsa"]), lost(d["rrsp"]))
            lines = [f"best for {n}: {b['account']}" + (f" ({100 * b['gap']:+.2f} points/yr over {b['next']})" if b.get("next") else "")
                     for n, b in res["best"].items() if b]
            notes.update("\n".join(lines + ["note: " + n for n in res["notes"]]))

    p = AccountsPane()
    app._draw_accounts = p.draw
    try:
        app.bind("A", f"tab('{TAB}')", description="Accounts")
    except Exception:                                                   # noqa: BLE001  (a missing key binding is not fatal)
        pass
    return p
