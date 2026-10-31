"""screenlab-tui: the Textual app (DESIGN.md L1). All state lives in `Session`; the app renders it.

    screenlab-tui                              # terminal, synthetic or sp500 lab, control endpoint on 8765
    screenlab-tui --serve --port 8000          # the same app in a browser (textual-serve)
    screenlab-tui --lab synthetic --store runs.sqlite --control-port 0 --theme textual-light

Keys: a add, c clone, r run, d delete, x toggle exit (then Apply), F2 Apply (new version + run), m Monte Carlo,
t timing, p picks, s save screen spec, e export, w charts in the browser, D data tab, T theme, ? help, q quit.

`screenlab-tui --serve` keeps ONE Session in the parent process; every browser tab runs this app over a
`RemoteSession` (`--remote URL`, tui/remote.py), so all tabs and the chart panel show the same scenarios.
The app reads state only through the Session interface (session.py / remote.py): no HTTP code here.
"""
from __future__ import annotations

import argparse
import os
import shlex
import sys
import webbrowser
from datetime import datetime

from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.css.query import NoMatches
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (Button, Checkbox, DataTable, Footer, Input, Label, OptionList, ProgressBar, RichLog,
                             Select, Static, Switch, TabbedContent, TabPane)
from textual.widgets.option_list import Option

from .. import metrics as _metrics
from .session import CAND, FETCH_SOURCES, LOCK, PASSIVE_COLOR, TABS, Session, SessionError, fmt_cell

TAB_TITLES = {"compare": "Compare", "curves": "Curves", "timing": "Timing", "picks": "Picks", "exit": "Exit",
              "montecarlo": "Monte Carlo", "runs": "Runs", "log": "Log", "data": "Data", "hypotheses": "Hypotheses"}
SOURCE_TITLES = {"sec": "SEC", "boc": "Bank of Canada", "fred": "FRED", "stooq": "Stooq", "statcan": "StatCan"}
HELP = """\
[b]screenlab-tui[/b]  scenarios are versions: Apply creates 'name v2' and keeps 'name'.

 a  add scenario          c  clone selected       d  delete selected
 r  run selected (all not run with shift+R)       F2 / Apply  new version + run
 x  toggle trend exit switch (then Apply)
 f  freeze the selected version (read-only; later versions are out-of-sample candidates, extra trials)
 l  link the selected scenario to a pre-registered hypothesis   H  Hypotheses tab
 m  Monte Carlo           t  timing study         p  picks
 s  save screen spec JSON e  export markdown+CSV  T  light/dark theme
 w  charts in the browser (Plotly, follows this screen)
 D  Data tab: datasets, download free datasets (needs an SEC contact)
 1-9 switch tab (H = Hypotheses)          ?  this help            q  quit

Colours: every scenario keeps its colour; the passive index is grey.
Control endpoint: see screenlab/tui/PROTOCOL.md."""
EXIT_PRESET = {"signals": [{"type": "TrendBreak"}], "threshold": 0.8, "hysteresis": 0.1, "park": "cash"}
PARAM_FIELDS = (("n", "Holdings"), ("rebalance_months", "Rebalance (months)"), ("cost_bps", "Cost (bps)"),
                ("commission", "Commission ($)"), ("mer", "MER (/yr)"), ("cash_rate", "Cash rate (/yr)"),
                ("start", "Start date"), ("end", "End date"))


TAX_ACCOUNT_CHOICES = ("none", "taxable", "tfsa", "rrsp")
TAX_PROVINCES = ("AB", "BC")
_NULL = getattr(Select, "NULL", None)


def _blank(v) -> bool:
    """True when a Select shows no choice (the sentinel is `Select.NULL` in current Textual, `Select.BLANK` before)."""
    return v is None or v is _NULL or v is Select.BLANK or v == ""


def _hex_to_rgb(h: str) -> tuple:
    return tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))


class AddScreen(ModalScreen):
    """Pick a screen name for a new scenario."""
    BINDINGS = [Binding("escape", "dismiss(None)", "Cancel")]
    DEFAULT_CSS = """
    AddScreen { align: center middle; }
    AddScreen > Vertical { width: 50; height: auto; max-height: 80%; padding: 1 2; border: thick $accent;
                           background: $surface; }
    AddScreen OptionList { height: 14; }
    """

    def __init__(self, screens, title: str = "Add scenario: choose a screen (Enter), Esc cancels", order: bool = False):
        super().__init__()
        self.names = list(screens) if order else sorted(screens)
        self.title = title

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(self.title)
            yield OptionList(*self.names, id="screen-list")

    def on_mount(self) -> None:
        self.query_one(OptionList).focus()

    @on(OptionList.OptionSelected)
    def _pick(self, ev: OptionList.OptionSelected) -> None:
        self.dismiss(self.names[ev.option_index])


class HelpScreen(ModalScreen):
    BINDINGS = [Binding("escape,q,question_mark", "dismiss(None)", "Close")]
    DEFAULT_CSS = """
    HelpScreen { align: center middle; }
    HelpScreen > Static { width: 78; height: auto; padding: 1 2; border: thick $accent; background: $surface; }
    """

    def compose(self) -> ComposeResult:
        yield Static(HELP)


class ScreenlabApp(App):
    TITLE = "screenlab"
    CSS = """
    #head { height: auto; padding: 0 1; }
    #warn { height: auto; padding: 0 1; }
    #chartsbar { height: auto; padding: 0 1; color: $text-muted; display: none; }
    #jobbar { height: 1; padding: 0 1; }
    #jobbar ProgressBar { width: 30; }
    #jobtext { width: 1fr; color: $text-muted; }
    #body { height: 1fr; }
    #side { width: 40; border-right: tall $primary-background; }
    #scen { height: 8; }
    #editor { height: 1fr; padding: 0 1; }
    #editor Label { color: $text-muted; margin-top: 1; }
    #editor Input { height: 3; }
    #diff { padding: 0 1; color: $warning; height: auto; }
    #main { width: 1fr; }
    DataTable { height: 1fr; }
    PlotextPlot { height: 1fr; }
    #compare-note { height: auto; padding: 0 1; color: $text-muted; }
    .pane-title { padding: 0 1; text-style: bold; }
    #t-datasets { height: 8; }
    #data-form { height: 1fr; padding: 0 1; }
    #data-form Label { color: $text-muted; margin-top: 1; }
    #data-form Input { height: 3; }
    #d-sources { height: auto; }
    #d-hosts, #d-status, #d-errors { height: auto; padding: 0 1; }
    #t-manifest { height: 8; }
    """
    BINDINGS = [
        Binding("a", "add", "Add"), Binding("c", "clone", "Clone"), Binding("r", "run", "Run"),
        Binding("R", "run_all", "Run all"), Binding("d", "delete", "Delete"),
        Binding("x", "toggle_exit", "Exit on/off"), Binding("f", "freeze", "Freeze"), Binding("l", "link", "Link"),
        Binding("H", "tab('hypotheses')", "Hypotheses"), Binding("m", "montecarlo", "Monte Carlo"),
        Binding("t", "timing", "Timing"), Binding("p", "picks", "Picks"), Binding("s", "save_spec", "Save spec"),
        Binding("e", "export", "Export"), Binding("w", "charts", "Charts"), Binding("D", "tab('data')", "Data"), Binding("T", "theme", "Theme"),
        Binding("question_mark", "help", "Help"),
        Binding("f2", "apply", "Apply", priority=True), Binding("q", "quit", "Quit"),
        *[Binding(str(i + 1), f"tab('{t}')", show=False) for i, t in enumerate(TABS[:9])],
    ]

    def __init__(self, session: Session, control_port: int | None = None, theme: str | None = None):
        super().__init__()
        self.session = session
        self.control_port = control_port
        self._want_theme = theme
        self._seen = -1
        self._loaded = False
        self._shown: str | None = None           # scenario id the editor shows
        self._control = None
        self._labs_key: tuple | None = None      # dataset names the editor's Select shows
        self._syncing = False
        self._data_ready = False                 # Data tab form prefilled
        self._remote = bool(getattr(session, "remote", False))

    # ------------------------------------------------------------------ layout
    def compose(self) -> ComposeResult:
        yield Static("loading data...", id="head")
        yield Static("", id="warn")
        with Horizontal(id="jobbar"):
            yield ProgressBar(total=1.0, show_eta=False, show_percentage=True, id="progress")
            yield Static("idle", id="jobtext")
        with Horizontal(id="body"):
            with Vertical(id="side"):
                yield Label("Scenarios (passive index first, fixed colours)", classes="pane-title")
                yield OptionList(id="scen")
                with VerticalScroll(id="editor"):
                    yield Label("Dataset")
                    yield Select([], id="f-lab", allow_blank=True)
                    yield Label("Screen")
                    yield Select([], id="f-screen", allow_blank=True)
                    for key, title in PARAM_FIELDS:
                        yield Label(title)
                        yield Input(id=f"f-{key}", placeholder="lab default" if key != "n" else "screen default")
                    yield Label("Tax: account (after-tax columns in Compare)")
                    yield Select([(n, n) for n in TAX_ACCOUNT_CHOICES], id="f-tax-account", allow_blank=False,
                                 value="none")
                    yield Label("Tax: province (blank = placeholder rates)")
                    yield Select([(n, n) for n in TAX_PROVINCES], id="f-tax-province", allow_blank=True)
                    # Q1: Tax section, account-centric: where the portfolio sits, plus the bracket and harvesting
                    yield Label("Tax: other income that sets your bracket ($/yr; the portfolio's income stacks on top)")
                    yield Input(id="f-tax-income", placeholder="90000")
                    yield Label("Tax: RRSP withdrawal rate (rrsp only; blank = untaxed, with a warning)")
                    yield Input(id="f-tax-rrsp", placeholder="e.g. 0.30")
                    yield Checkbox("Tax: harvest losses (taxable account; off unless ticked)", False, id="f-tax-harvest")
                    yield Label("Tax: harvest threshold (% loss on cost)")
                    yield Input(id="f-tax-threshold", placeholder="10")
                    yield Label("Tax: harvest replacement (second pick / cash / stay out)")
                    yield Select([("second pick", "second_pick"), ("cash", "cash"), ("stay out", "stay_out")],
                                 id="f-tax-replacement", allow_blank=False, value="second_pick")
                    yield Label("Tax: year of the rate tables")
                    yield Input(id="f-tax-year", placeholder="2025")
                    yield Label("Trend exit (TrendBreak on the passive index)")
                    yield Switch(id="f-exit", value=False)
                    yield Button("Apply: new version + run (F2)", id="apply", variant="primary")
                yield Static("", id="diff")
            with TabbedContent(id="main", initial="compare"):
                with TabPane("Compare", id="compare"):
                    yield DataTable(id="t-compare", cursor_type="row")
                    yield Static("", id="compare-note")
                with TabPane("Curves", id="curves"):
                    from textual_plotext import PlotextPlot
                    yield PlotextPlot(id="p-curves")
                with TabPane("Timing", id="timing"):
                    yield DataTable(id="t-timing", cursor_type="row")
                    yield PlotextPlot(id="p-timing")
                with TabPane("Picks", id="picks"):
                    yield Static("", id="picks-note", classes="pane-title")
                    yield DataTable(id="t-picks", cursor_type="row")
                with TabPane("Exit", id="exit"):
                    yield Static("", id="exit-note", classes="pane-title")
                    yield DataTable(id="t-exit-fwd", cursor_type="row")
                    yield DataTable(id="t-exit-ov", cursor_type="row")
                with TabPane("Monte Carlo", id="montecarlo"):
                    yield Static("", id="mc-note", classes="pane-title")
                    yield DataTable(id="t-mc", cursor_type="row")
                    yield PlotextPlot(id="p-mc")
                with TabPane("Runs", id="runs"):
                    yield Static("Enter on a row re-runs that stored run and checks it reproduces", classes="pane-title")
                    yield DataTable(id="t-runs", cursor_type="row")
                with TabPane("Hypotheses", id="hypotheses"):
                    yield Static("", id="hyp-note", classes="pane-title")
                    yield DataTable(id="t-hyp", cursor_type="row")
                    yield Static("", id="hyp-detail")
                with TabPane("Log", id="log"):
                    yield RichLog(id="log-view", wrap=True, markup=False)
                with TabPane("Data", id="data"):
                    yield Static("", id="data-note", classes="pane-title")
                    yield DataTable(id="t-datasets", cursor_type="row")
                    with VerticalScroll(id="data-form"):
                        yield Label("Download free datasets: SEC contact (the SEC requires 'Your Name you@example.com')")
                        yield Input(id="d-sec", placeholder="Your Name you@example.com")
                        yield Label("Sources")
                        with Horizontal(id="d-sources"):
                            for src in FETCH_SOURCES:
                                yield Checkbox(SOURCE_TITLES.get(src, src), True, id=f"d-src-{src}")
                        yield Label("Destination folder")
                        yield Input(id="d-dest", value="data")
                        yield Label("Tickers (comma separated; Stooq / prices)")
                        yield Input(id="d-tickers", value="XIU.TO")
                        yield Label("Start date")
                        yield Input(id="d-start", value="2000-01-01")
                        yield Label("SEC periods (comma separated, e.g. 2024q1,2024q2; empty = default)")
                        yield Input(id="d-periods", placeholder="2024q1,2024q2")
                        yield Static("", id="d-hosts")
                        yield Button("Download free datasets", id="d-go", variant="primary")
                        yield Static("", id="d-status")
                        yield DataTable(id="t-manifest", cursor_type="row")
                        yield Static("", id="d-errors")
                yield __import__(__package__ + ".accounts", fromlist=["pane"]).pane(self)  # Q2: Accounts tab (tui/accounts.py)
                yield __import__(__package__ + ".plan", fromlist=["pane"]).pane(self)  # PC: Plan tab (tui/plan.py)
        yield Static("", id="chartsbar")
        yield Footer()

    def on_mount(self) -> None:
        if self._want_theme:
            try:
                self.theme = self._want_theme
            except Exception:
                self.notify(f"unknown theme {self._want_theme!r}", severity="warning")
        if not self._remote or self._want_theme:     # a new browser tab must not reset the shared theme
            self._push_theme()
        self.theme_changed_signal.subscribe(self, lambda t: self._push_theme())
        self.query_one("#progress", ProgressBar).display = False
        self.run_worker(self._load, thread=True, exclusive=True)
        self.set_interval(0.25, self._tick)
        if self.control_port is not None:
            self._start_control()

    def _push_theme(self) -> None:
        keys = ("background", "surface", "panel", "foreground", "primary", "secondary", "accent", "success",
                "warning", "error")
        try:
            css = self.get_css_variables()
            variables = {k: str(css[k]) for k in keys if isinstance(css.get(k), str) and css[k].startswith("#")}
        except Exception:                                                # noqa: BLE001
            variables = {}
        try:
            self.session.set_theme(self.theme, variables or None)
        except SessionError:
            pass

    def _start_control(self) -> None:
        from .control import ControlServer
        try:
            self._control = ControlServer(self, self.session, self.control_port).start()
            self.session.log(f"control endpoint http://127.0.0.1:{self._control.port}")
            self.session.log(f"charts in the browser: {self._control.home_url()} (key w)")
            if self._control.serve_url:
                bar = self.query_one("#chartsbar", Static)
                bar.update(f"Charts + terminal split view: {self._control.home_url()}   (key w shows it)")
                bar.display = True
        except OSError as e:
            self.notify(f"control endpoint not started: {e}", severity="warning")
            self.session.log(f"control endpoint not started: {e}")

    def on_unmount(self) -> None:
        if self._control is not None:
            self._control.stop()
        self.session.close()

    def _load(self) -> None:
        """Build the lab (can take a while for real data) off the UI thread, then draw the header."""
        try:
            self.session.card()
            self.session.passive_label()
        except SessionError as e:
            self.call_from_thread(self.notify, e.message, severity="error", title="session")
            return
        self._loaded = True
        if self._remote and getattr(self.session, "serve", False):
            self.call_from_thread(self._show_remote_bar)

    def _show_remote_bar(self) -> None:
        bar = self.query_one("#chartsbar", Static)
        bar.update(f"Shared session. Charts + terminal split view: {self.session.home_url}   (key w shows it)")
        bar.display = True

    # ------------------------------------------------------------------ refresh
    @property
    def ready(self) -> bool:
        """True once the lab is built and the first header refresh has been drawn (tests wait on this)."""
        return self._loaded and self._seen == self.session.seq

    def _tick(self) -> None:
        s = self.session
        s.poll()
        if not self._loaded:
            return
        if len(self.screen_stack) > 1:       # a modal is on top: query_one would search it, not the main screen
            return
        if s.seq != self._seen:
            self._seen = s.seq
            try:
                self.refresh_all()
            except NoMatches:                # the timer raced app teardown: the main screen is already gone
                return
        try:
            self._progress()
        except NoMatches:
            return

    def _progress(self) -> None:
        jobs = self.session.active_jobs()
        bar = self.query_one("#progress", ProgressBar)
        txt = self.query_one("#jobtext", Static)
        if not jobs:
            bar.display = False
            txt.update("idle")
            return
        bar.display = True
        bar.update(progress=sum(j["progress"] or 0 for j in jobs) / len(jobs))
        txt.update(f"{len(jobs)} job(s): " + ", ".join(f"{j['kind']} {j.get('scenario') or ''}".strip() for j in jobs[:3]))

    def refresh_all(self) -> None:
        s = self.session
        card = s.card()
        store = s.store_path()
        self.query_one("#head", Static).update(
            card.line() + f"   store: {store or 'off'}   trials this session: {s.trials_total()}")
        warn = Text()
        for w in card.warnings():
            warn.append(w + "\n", style="bold red" if "price index" in w else "bold yellow")
        self.query_one("#warn", Static).update(warn)
        self.query_one("#warn", Static).display = bool(card.warnings())
        if s.theme != self.theme and s.theme in self.available_themes:
            self.theme = s.theme
        self._refresh_labs()
        self._refresh_scenarios()
        self._refresh_editor()
        tc = self.query_one("#main", TabbedContent)
        if tc.active != s.active_tab:
            tc.active = s.active_tab
        self._refresh_tab(s.active_tab)

    def _refresh_scenarios(self) -> None:
        s = self.session
        ol = self.query_one("#scen", OptionList)
        ol.clear_options()
        ol.add_option(Option(Text.assemble(("● ", PASSIVE_COLOR), (f"{s.passive_label()} ", "bold"),
                                           ("passive index", "dim")), id="passive", disabled=True))
        sel = None
        for i, sc in enumerate(s.scenarios):
            mark = {"done": "", "failed": " failed", "queued": " queued", "running": " running",
                    "cancelled": " cancelled", "new": " not run"}[sc.status]
            flags = (f" {LOCK}" if sc.frozen else "") + (f" {CAND}cand" if sc.candidate_of else "") + (
                f" [{sc.hypothesis}]" if sc.hypothesis else "")
            ol.add_option(Option(Text.assemble((" ● ", sc.color), (sc.label, "bold" if sc.id == s.selected else ""),
                                               (flags, "yellow"), (mark, "dim")), id=sc.id))
            if sc.id == s.selected:
                sel = i + 1
        if sel is not None:
            ol.highlighted = sel

    def _refresh_labs(self) -> None:
        """The editor's dataset Select offers every dataset in the registry (including ones the downloader added)."""
        names = tuple(self.session.lab_names())
        if names == self._labs_key:
            return
        self._labs_key = names
        sel = self.query_one("#f-lab", Select)
        keep = sel.value
        self._syncing = True
        try:
            sel.set_options([(n, n) for n in names])
            if keep in names:
                sel.value = keep
        finally:
            self._syncing = False
        self._shown = None                                                # the editor reloads its values

    def _screen_options(self, lab, scr) -> list:
        opts = [(n, n) for n in self.session.screen_names(lab)]
        if isinstance(scr, dict):
            opts.insert(0, (f"{scr.get('name', 'spec')} (inline spec)", "__spec__"))
        return opts

    def _refresh_editor(self) -> None:
        s = self.session
        sc = next((x for x in s.scenarios if x.id == s.selected), None)
        sel = self.query_one("#f-screen", Select)
        if sc is None:
            self._shown = None
            return
        if self._shown == (sc.id, bool(sc.frozen)):
            return
        self._shown = (sc.id, bool(sc.frozen))
        scr = sc.params.get("screen")
        lab = sc.params.get("lab")
        self._syncing = True
        try:
            lsel = self.query_one("#f-lab", Select)
            if lab in (self._labs_key or ()):
                lsel.value = lab
            sel.set_options(self._screen_options(lab, scr))
            sel.value = "__spec__" if isinstance(scr, dict) else scr
        finally:
            self._syncing = False
        for key, _ in PARAM_FIELDS:
            v = sc.params.get(key)
            self.query_one(f"#f-{key}", Input).value = "" if v is None else str(v)
        self.query_one("#f-exit", Switch).value = bool(sc.params.get("exit"))
        tax = sc.params.get("tax") or {}
        self._syncing = True
        try:
            self.query_one("#f-tax-account", Select).value = tax.get("account", "none")
            if tax.get("province"):
                self.query_one("#f-tax-province", Select).value = tax["province"]
            else:
                self.query_one("#f-tax-province", Select).clear()
        finally:
            self._syncing = False
        inc = tax.get("other_income", tax.get("employment_income"))        # Q1
        self.query_one("#f-tax-income", Input).value = "" if inc is None else f"{inc:g}"
        rw = tax.get("rrsp_withdrawal_rate")
        self.query_one("#f-tax-rrsp", Input).value = "" if rw is None else f"{rw:g}"
        thr = tax.get("harvest_threshold_pct")
        self.query_one("#f-tax-threshold", Input).value = "" if thr is None else f"{100 * thr:g}"
        self._syncing = True
        try:
            self.query_one("#f-tax-harvest", Checkbox).value = bool(tax.get("harvest"))
            self.query_one("#f-tax-replacement", Select).value = tax.get("harvest_replacement", "second_pick")
        finally:
            self._syncing = False
        self.query_one("#f-tax-year", Input).value = str(tax["year"]) if tax.get("year") else ""
        note = sc.diff_line or ""
        if sc.frozen:
            note = f"{LOCK} frozen: read-only. Clone (c) to continue; the clone is an out-of-sample candidate."
        elif sc.candidate_of:
            note = f"{CAND} candidate of frozen {sc.candidate_of}: each look counts as an extra trial against it.\n{note}"
        self.query_one("#diff", Static).update(note)
        for w in self.query("#editor Input, #editor Select, #editor Switch, #editor Checkbox, #editor Button"):
            w.disabled = bool(sc.frozen)

    def _refresh_tab(self, tab: str) -> None:
        getattr(self, f"_draw_{tab}")()

    # ------------------------------------------------------------------ tabs
    @staticmethod
    def _fill(table: DataTable, columns, rows) -> None:
        table.clear(columns=True)
        table.add_columns(*[str(c) for c in columns])
        for r in rows:
            table.add_row(*r)

    @staticmethod
    def _cell(v):
        if v is None:
            return ""
        return f"{v:.4g}" if isinstance(v, float) else str(v)

    def _draw_compare(self) -> None:
        c = self.session.command("compare")
        t = self.query_one("#t-compare", DataTable)
        cols = c["columns"]
        rows = []
        for r in c["rows"]:
            label = Text.assemble(("● ", r["color"]), (r["label"], "bold"),
                                  (" (passive)" if r["passive"] else "", "dim"),
                                  (" " + r["mark"] if r.get("mark") else "", "yellow"))
            rows.append([label, *[Text(fmt_cell(x, r["values"].get(x)), justify="right") for x in cols]])
        self._fill(t, ["Scenario", *cols], rows)
        note = []
        if c["missing"]:
            note.append("not run yet: " + ", ".join(c["missing"]))
        note += c["diffs"]
        note.append(f"trials this session: {c['trials_total']}. Trials = variants of the row's family tried; DSR = chance the "
                    f"Sharpe survives that many tries (low = likely luck). {LOCK} frozen, {CAND} out-of-sample candidate.")
        if "AfterTaxCAGR" in cols:
            note.append("AfterTaxCAGR / TaxDrag/yr: the Tax section of the editor; the passive index is taxed the same way "
                        "(same account and model). Placeholder rates are illustrative only.")
        note.append("CI5/CI95: bootstrap interval of Excess/yr (run with ci). Excess/yr is against the passive index.")
        self.query_one("#compare-note", Static).update("\n".join(note))

    def _draw_curves(self) -> None:
        from datetime import datetime as dt
        w = self.query_one("#p-curves")
        plt = w.plt
        plt.clear_data()
        plt.clear_figure()
        data = self.session.curves_data()
        if not data:
            plt.title("no curves yet: select a scenario and press r")
        else:
            for label, d in data.items():
                pts = d["points"]
                x = [dt.fromisoformat(p[0][:10]).toordinal() / 365.25 for p in pts]
                plt.plot(x, [p[1] for p in pts], label=label, color=_hex_to_rgb(d["color"]), marker="braille")
            plt.yscale("log")
            plt.title("Wealth, start = 1.0 (log scale)")
            plt.xlabel("year")
        w.refresh()

    def _draw_timing(self) -> None:
        s = self.session
        sc = next((x for x in s.scenarios if x.id == s.selected), None)
        tm = s.analyses["timing"].get(sc.label) if sc else None
        t = self.query_one("#t-timing", DataTable)
        w = self.query_one("#p-timing")
        w.plt.clear_figure()
        if not tm:
            self._fill(t, ["Timing"], [["no timing study for the selected scenario: press t"]])
            w.plt.title("tranche curve appears after a timing study")
        else:
            d = tm["distribution"]
            self._fill(t, d["columns"], [[self._cell(v) for v in r] for r in d["rows"]])
            tr = tm.get("tranche")
            if tr:
                from datetime import datetime as dt
                w.plt.plot([dt.fromisoformat(p[0][:10]).toordinal() / 365.25 for p in tr], [p[1] for p in tr],
                           color=_hex_to_rgb(sc.color), marker="braille")
                w.plt.yscale("log")
                w.plt.title(f"{sc.label}: tranche (all start offsets blended)")
        w.refresh()

    def _draw_picks(self) -> None:
        s = self.session
        sc = next((x for x in s.scenarios if x.id == s.selected), None)
        p = s.analyses["picks"].get(sc.label) if sc else None
        t = self.query_one("#t-picks", DataTable)
        if not p:
            self.query_one("#picks-note", Static).update("press p for the selected scenario's picks")
            self._fill(t, ["Picks"], [])
            return
        self.query_one("#picks-note", Static).update(f"{p['scenario']} as of {str(p['asof'])[:10]}")
        self._fill(t, p["columns"], [[self._cell(v) for v in r] for r in p["rows"]])

    def _draw_exit(self) -> None:
        s = self.session
        sc = next((x for x in s.scenarios if x.id == s.selected), None)
        e = s.analyses["exit"].get(sc.label) if sc else None
        fwd, ov = self.query_one("#t-exit-fwd", DataTable), self.query_one("#t-exit-ov", DataTable)
        if not e:
            self.query_one("#exit-note", Static).update(
                "switch 'Trend exit' on, Apply, then run `exit_eval` (control endpoint) or press x")
            self._fill(fwd, ["Forward returns"], [])
            self._fill(ov, ["Overlay"], [])
            return
        self.query_one("#exit-note", Static).update(
            f"{sc.label}: flagged {e.get('frac_flagged', 0):.1%} of the time, {e.get('switches')} switches")
        for tbl, key in ((fwd, "forward"), (ov, "overlay_stats")):
            self._fill(tbl, e[key]["columns"], [[self._cell(v) for v in r] for r in e[key]["rows"]])

    def _draw_montecarlo(self) -> None:
        m = self.session.analyses["montecarlo"]
        t, w = self.query_one("#t-mc", DataTable), self.query_one("#p-mc")
        w.plt.clear_figure()
        if not m:
            self.query_one("#mc-note", Static).update(f"press m (settings: {self.session.mc})")
            self._fill(t, ["Monte Carlo"], [])
            w.refresh()
            return
        self.query_one("#mc-note", Static).update(
            f"{', '.join(m['scenarios'])}: {m.get('n_paths')} paths, horizons {m.get('horizon_years')}")
        tb = m["table"]
        self._fill(t, tb["columns"], [[self._cell(v) for v in r] for r in tb["rows"]])
        fan = m.get("fan")
        if fan and fan["rows"]:
            cols = fan["columns"]
            x = [r[0] for r in fan["rows"]]
            colors = {sc.label: sc.color for sc in self.session.scenarios}
            for i, c in enumerate(cols):
                if c.endswith("|p50"):
                    label = c[:-4]
                    col = _hex_to_rgb(colors.get(label, PASSIVE_COLOR))
                    w.plt.plot(x, [r[i] for r in fan["rows"]], label=label, color=col, marker="braille")
            w.plt.yscale("log")
            w.plt.title("median wealth by year (log scale)")
        w.refresh()

    def _draw_runs(self) -> None:
        h = self.session.command("history", {"limit": 30})
        t = self.query_one("#t-runs", DataTable)
        if not h["runs"]:
            self._fill(t, ["Runs"], [[f"store: {h['store'] or 'off (runs are not kept)'}"]])
            return
        cols = ["kind", "label", "screen", "status", "created_at", "run_key"]
        self._fill(t, cols, [[str(r.get(c) or "")[:19 if c == "created_at" else 14 if c == "run_key" else 40]
                              for c in cols] for r in h["runs"]])
        t._run_keys = [r["run_key"] for r in h["runs"]]

    def _draw_hypotheses(self) -> None:
        info = self.session.command("hypotheses")
        t = self.query_one("#t-hyp", DataTable)
        self.query_one("#hyp-note", Static).update(
            f"{info['path']}  (Enter or l: link the selected scenario to the highlighted hypothesis)")
        rows = []
        for h in info["hypotheses"]:
            linked = ", ".join(f"{x['label'] or x['scenario']}: " + {True: "met", False: "NOT met", None: "undecided"}[x["met"]]
                               for x in h["linked"])
            rows.append([Text(h["id"] + (" (example)" if h["example"] else ""), style="bold"), h["status"], h["name"],
                         f"{h['metric']} {h['threshold']}", h["horizon"] or "", linked])
        self._fill(t, ["Id", "Status", "Hypothesis", "Pass mark", "Horizon", "Linked scenarios"], rows)
        t._ids = [h["id"] for h in info["hypotheses"]]
        detail = [*info["warnings"]]
        for h in info["hypotheses"]:
            for x in h["linked"]:
                if x["reason"]:
                    detail.append(f"{h['id']} {x['label'] or x['scenario']}: {x['reason']}")
        self.query_one("#hyp-detail", Static).update("\n".join(detail))

    def _draw_log(self) -> None:
        v = self.query_one("#log-view", RichLog)
        v.clear()
        for line in self.session.log_lines(200):
            v.write(line)

    def _draw_data(self) -> None:
        """Datasets (registry labs with data card and warnings) and the downloader form. The lab cards may need
        a lab to be built, so the command runs in a thread and the result is painted back on the app thread."""
        def work():
            try:
                res = self.session.command("datasets")
            except SessionError as e:
                res = {"error": e.message}
            self.call_from_thread(self._paint_data, res)
        self.run_worker(work, thread=True, group="datasets", exclusive=True)

    def _paint_data(self, res: dict) -> None:
        if "error" in res:
            self.query_one("#data-note", Static).update(f"datasets unavailable: {res['error']}")
            return
        self.query_one("#data-note", Static).update(
            "Datasets this session can use (the editor's Dataset Select offers them)")
        rows = []
        for L in res["labs"]:
            state = ", ".join(t for t, on in (("default", L["default"]), ("session", L["session"]),
                                              ("loaded", L["loaded"])) if on) or "not loaded"
            card = L["line"] or f"unavailable: {L['error']}"
            warn = Text()
            for w in L["warnings"]:
                warn.append(w + " ", style="bold red" if "price index" in w else "bold yellow")
            rows.append([Text(L["name"], style="bold"), state, card, warn])
        self._fill(self.query_one("#t-datasets", DataTable), ["Dataset", "State", "Data card", "Warnings"], rows)
        if not self._data_ready:
            self._data_ready = True
            d = res["defaults"]
            for wid, key in (("#d-sec", "sec_user_agent"), ("#d-dest", "dest"), ("#d-start", "start")):
                if d.get(key):
                    self.query_one(wid, Input).value = str(d[key])
            self.query_one("#d-tickers", Input).value = ",".join(d.get("tickers") or [])
        hosts = res["hosts"]
        self.query_one("#d-hosts", Static).update(
            "Network allow-list for the downloads (add these hosts to this environment's allowed domains):\n"
            + "\n".join(f"  {SOURCE_TITLES.get(k, k)}: {', '.join(v)}" for k, v in hosts.items()))
        go, status = self.query_one("#d-go", Button), self.query_one("#d-status", Static)
        tbl, errs = self.query_one("#t-manifest", DataTable), self.query_one("#d-errors", Static)
        go.disabled = not res["downloader"] or bool(res["running"])
        lf = res["last_fetch"]
        if not res["downloader"]:
            status.update("downloader not installed (the operation fetch_data is not registered)")
        elif res["running"]:
            status.update(f"downloading... {(res['running'].get('progress') or 0):.0%} "
                          f"{res['running'].get('message') or ''}".strip())
        elif lf and lf.get("error"):
            status.update(f"download failed: {lf['error']}")
        elif lf:
            man = lf.get("manifest") or {}
            status.update(f"download finished: {len(man.get('files') or [])} file(s)"
                          + (f"; new datasets: {', '.join(lf['registered'])}" if lf.get("registered") else ""))
        else:
            status.update("ready: fill in the SEC contact, pick sources, press the button")
        files = ((lf or {}).get("manifest") or {}).get("files") or []
        self._fill(tbl, ["Source", "Path", "Bytes", "Status"],
                   [[str(f.get("source", "")), str(f.get("path", "")), self._cell(f.get("bytes")),
                     str(f.get("status", ""))] for f in files])
        problems = [str(e) for e in (((lf or {}).get("manifest") or {}).get("errors") or [])]
        errs.update(Text("\n".join(problems), style="bold red") if problems else "")

    def action_download(self) -> None:
        """Submit the `fetch_data` operation (job path: progress bar in the header, result on the Data tab)."""
        def val(i):
            return self.query_one(i, Input).value.strip()

        def split(x):
            return [t.strip() for t in x.replace(";", ",").split(",") if t.strip()]
        sources = [src for src in FETCH_SOURCES if self.query_one(f"#d-src-{src}", Checkbox).value]
        if not sources:
            self.notify("tick at least one source", severity="warning")
            return
        args = {"sources": sources, "dest": val("#d-dest") or "data", "sec_user_agent": val("#d-sec"),
                "tickers": split(val("#d-tickers")), "start": val("#d-start") or None,
                "periods": split(val("#d-periods"))}
        res = self._cmd("fetch_data", args, "download")
        if res:
            self.notify("download started", title="Data")

    # ------------------------------------------------------------------ events
    @on(TabbedContent.TabActivated)
    def _tab(self, ev: TabbedContent.TabActivated) -> None:
        tab = ev.pane.id
        if tab and tab != self.session.active_tab:
            self._cmd("show", {"tab": tab})
        if tab and self._loaded:
            self._refresh_tab(tab)

    @on(OptionList.OptionHighlighted, "#scen")
    def _highlight(self, ev: OptionList.OptionHighlighted) -> None:
        if ev.option_id and ev.option_id != self.session.selected:
            self._cmd("select", {"scenario": ev.option_id})

    @on(Button.Pressed, "#apply")
    def _apply_btn(self) -> None:
        self.action_apply()

    @on(Input.Submitted, "#editor Input")
    def _submit(self) -> None:
        self.action_apply()

    @on(Input.Submitted, "#data-form Input")
    def _submit_data(self) -> None:
        self.action_download()

    @on(Select.Changed, "#f-lab")
    def _lab_changed(self, ev: Select.Changed) -> None:
        """A different dataset offers its own screens: refill the screen Select, keeping the choice if it exists."""
        if self._syncing or _blank(ev.value):
            return
        scr = self.query_one("#f-screen", Select)
        keep = scr.value
        try:
            opts = self._screen_options(ev.value, None)
        except SessionError as e:
            self.notify(e.message, severity="error")
            return
        self._syncing = True
        try:
            scr.set_options(opts)
            if keep in [v for _, v in opts]:
                scr.value = keep
        finally:
            self._syncing = False

    @on(Button.Pressed, "#d-go")
    def _download_btn(self) -> None:
        self.action_download()

    @on(DataTable.RowSelected, "#t-hyp")
    def _link_row(self, ev: DataTable.RowSelected) -> None:
        ids = getattr(ev.data_table, "_ids", [])
        if ev.cursor_row < len(ids) and self._cmd("link", {"hypothesis": ids[ev.cursor_row]}, "link"):
            self.notify(f"linked to {ids[ev.cursor_row]}: its variants now count against that hypothesis")

    @on(DataTable.RowSelected, "#t-runs")
    def _rerun(self, ev: DataTable.RowSelected) -> None:
        keys = getattr(ev.data_table, "_run_keys", [])
        if ev.cursor_row < len(keys):
            self._cmd("history", {"rerun": keys[ev.cursor_row]}, "rerun")

    # ------------------------------------------------------------------ actions
    def _cmd(self, cmd: str, args: dict | None = None, what: str | None = None):
        try:
            res = self.session.command(cmd, args or {})
        except SessionError as e:
            self.notify(e.message, severity="error", title=what or cmd)
            self.session.log(f"{cmd}: {e.message}")
            self.session._emit("error")
            return None
        if cmd == "history" and "rerun" in (args or {}):
            self.notify("reproduced exactly" if res.get("match") else f"differs (max diff {res.get('max_abs_diff')})",
                        title="rerun")
        return res

    def _form_tax(self, old=None):
        """The editor's Tax section as a `tax` param (None for account `none`): province -> model canadian, else placeholder."""
        acct = self.query_one("#f-tax-account", Select).value
        if _blank(acct) or acct == "none":
            return None
        prov = self.query_one("#f-tax-province", Select).value
        managed = ("model", "account", "province", "other_income", "employment_income", "marginal_rate", "year",
                   "rrsp_withdrawal_rate", "harvest", "harvest_threshold_pct", "harvest_replacement")     # Q1
        out = {k: v for k, v in (old or {}).items() if k not in managed}          # fields set elsewhere stay
        out.update({"model": "placeholder" if _blank(prov) else "canadian", "account": acct})
        if not _blank(prov):
            out["province"] = prov
        if (old or {}).get("marginal_rate") is not None and self.query_one("#f-tax-income", Input).value.strip() == "":
            out["marginal_rate"] = old["marginal_rate"]                            # a direct bracket knob set elsewhere stays
        for key, field, conv, word, scale in (("#f-tax-income", "other_income", float, "a number", 1.0),
                                              ("#f-tax-rrsp", "rrsp_withdrawal_rate", float, "a rate such as 0.30", 1.0),
                                              ("#f-tax-year", "year", int, "an integer", 1.0)):
            raw = self.query_one(key, Input).value.strip()
            if raw == "":
                continue
            try:
                out[field] = conv(raw)
            except ValueError:
                raise SessionError(400, f"tax {field.replace('_', ' ')} must be {word}, got {raw!r}") from None
        if self.query_one("#f-tax-harvest", Checkbox).value:                        # Q1: harvesting only when ticked
            out["harvest"] = True
            raw = self.query_one("#f-tax-threshold", Input).value.strip()
            if raw != "":
                try:
                    out["harvest_threshold_pct"] = float(raw) / 100.0
                except ValueError:
                    raise SessionError(400, f"tax harvest threshold must be a percent such as 10, got {raw!r}") from None
            rep = self.query_one("#f-tax-replacement", Select).value
            if not _blank(rep):
                out["harvest_replacement"] = rep
        if "year" in out and _blank(prov):
            del out["year"]                          # the placeholder model has no year
        return out

    def _form_params(self, sc) -> dict:
        """Parameters in the editor that differ from the scenario, as a set_params dict."""
        out = {}
        for key, _ in PARAM_FIELDS:
            raw = self.query_one(f"#f-{key}", Input).value.strip()
            old = sc.params.get(key)
            if raw == "":
                new = None
            elif key in ("n", "rebalance_months"):
                try:
                    new = int(raw)
                except ValueError:
                    raise SessionError(400, f"{key} must be an integer, got {raw!r}") from None
            elif key in ("start", "end"):
                new = raw
            else:
                try:
                    new = float(raw)
                except ValueError:
                    raise SessionError(400, f"{key} must be a number, got {raw!r}") from None
            if new != old:
                out[key] = new
        lab = self.query_one("#f-lab", Select).value
        if not _blank(lab) and lab != (sc.params.get("lab") or self.session.lab):
            out["lab"] = lab
        scr = self.query_one("#f-screen", Select).value
        if not _blank(scr) and scr != "__spec__" and scr != sc.params.get("screen"):
            out["screen"] = scr
        tax = self._form_tax(sc.params.get("tax"))
        if tax != sc.params.get("tax"):
            out["tax"] = tax
        want_exit = self.query_one("#f-exit", Switch).value
        if want_exit and not sc.params.get("exit"):
            out["exit"] = {**EXIT_PRESET, "signals": [{"type": "TrendBreak", "index": self.session.passive_label()}]}
        elif not want_exit and sc.params.get("exit"):
            out["exit"] = None
        return out

    def action_apply(self) -> None:
        s = self.session
        sc = next((x for x in s.scenarios if x.id == s.selected), None)
        if sc is None:
            self.notify("add a scenario first (a)", severity="warning")
            return
        if sc.frozen:
            self.notify(f"{sc.label} is frozen and read-only: clone it (c) to continue", severity="warning")
            return
        try:
            changed = self._form_params(sc)
        except SessionError as e:
            self.notify(e.message, severity="error")
            return
        if not changed:
            if sc.status in ("new", "failed", "cancelled"):
                self._cmd("run", {"scenario": sc.id})
            else:
                self.query_one("#diff", Static).update("no parameter changed: edit a field, then Apply")
            return
        res = self._cmd("set_params", {"scenario": sc.id, "params": changed, "run": True}, "apply")
        if res:
            self._shown = None                       # editor reloads for the new version
            self.query_one("#diff", Static).update(res["diff_line"])

    def action_tab(self, tab: str) -> None:
        self._cmd("show", {"tab": tab})

    def action_add(self) -> None:
        names = self.session.screen_names()

        def done(name):
            if name:
                self._cmd("add_scenario", {"screen": name, "run": True})
        self.push_screen(AddScreen(names), done)

    def action_clone(self) -> None:
        self._cmd("clone_scenario", {})

    def action_run(self) -> None:
        self._cmd("run", {})

    def action_freeze(self) -> None:
        sc = next((x for x in self.session.scenarios if x.id == self.session.selected), None)
        if sc is None:
            self.notify("select a scenario first", severity="warning")
        elif sc.frozen:
            self.notify(f"{sc.label} is already frozen (the `unfreeze` command logs a line)", severity="warning")
        elif self._cmd("freeze", {}):
            self.notify(f"{sc.label} frozen: read-only; later versions are out-of-sample candidates")

    def action_link(self) -> None:
        hyps = self.session.command("hypotheses")["hypotheses"]
        if not hyps:
            self.notify("no hypotheses in the pre-registration file", severity="warning")
            return
        ids = ["(none)"] + [h["id"] for h in hyps]
        names = ["(none) unlink"] + [f"{h['id']}  {h['name']}" for h in hyps]

        def done(pick):
            if pick:
                hid = ids[names.index(pick)]
                self._cmd("link", {"hypothesis": None if hid == "(none)" else hid}, "link")
        self.push_screen(AddScreen(names, "Link the selected scenario to a hypothesis (Enter), Esc cancels", order=True), done)

    def action_run_all(self) -> None:
        self._cmd("run", {"scenario": "all"})

    def action_delete(self) -> None:
        self._cmd("remove_scenario", {})

    def action_toggle_exit(self) -> None:
        sw = self.query_one("#f-exit", Switch)
        sw.value = not sw.value
        self.notify(f"trend exit {'on' if sw.value else 'off'}: press F2 to apply as a new version")

    def action_montecarlo(self) -> None:
        if self._cmd("montecarlo", {}):
            self._cmd("show", {"tab": "montecarlo"})

    def action_timing(self) -> None:
        if self._cmd("timing", {"stride": 5}):
            self._cmd("show", {"tab": "timing"})

    def action_picks(self) -> None:
        self.session.command("show", {"tab": "picks"})
        self._cmd("picks", {"wait": False})

    def action_save_spec(self) -> None:
        res = self._cmd("save_spec", {})
        if res:
            self.notify(f"saved {res['path']}")

    def action_export(self) -> None:
        res = self._cmd("export", {})
        if res:
            self.notify("exported " + ", ".join(res["files"]))

    def action_charts(self) -> None:
        """Open the Plotly chart page in the default browser (terminal mode), or show the split-view URL (--serve)."""
        c = self._control
        if c is None and self._remote:                       # a client of the shared session: its endpoint serves the pages
            url = self.session.home_url
            self.session.log(f"charts: {url}")
            self.notify(f"{'split view with charts' if self.session.serve else 'charts'}: {url}", title="charts", timeout=10)
            return
        if c is None:
            self.notify("the control endpoint is off (--control-port 0): the chart page is served from it",
                        severity="warning")
            return
        url = c.home_url()
        self.session.log(f"charts: {url}" + ("?token=<SCREENLAB_TUI_TOKEN>" if c.token else ""))
        if c.serve_url:
            self.notify(f"split view with charts: {url}", title="charts", timeout=10)
            return
        try:
            opened = webbrowser.open(url + (f"?token={c.token}" if c.token else ""))
        except Exception:                                                # noqa: BLE001
            opened = False
        self.notify(f"opened {url}" if opened else f"open this in a browser: {url}", title="charts", timeout=8)

    def action_theme(self) -> None:
        self._cmd("set_theme", {"theme": "textual-light" if self.theme != "textual-light" else "textual-dark"})

    def action_help(self) -> None:
        self.push_screen(HelpScreen())


# ---------------------------------------------------------------------- command line
def build_session(lab: str | None, store: str | None) -> Session:
    from ..server.registry import LabRegistry, default_store
    from ..store import NullStore, SQLiteStore
    if store is None:
        st = default_store()
    elif store.lower() in ("", "none", "off"):
        st = NullStore()
    else:
        st = SQLiteStore(store)
    reg = LabRegistry(store=st, mode="thread")
    if lab and lab not in reg.factories:
        raise SystemExit(f"unknown lab {lab!r}; available: {sorted(reg.factories)}")
    return Session(reg, lab=lab, own=True)


def serve_command(args: argparse.Namespace, control_port: int) -> str:
    """The command textual-serve runs for every browser tab: an app process that is a client of the parent's
    session (`--remote`), not a session of its own."""
    cmd = [sys.executable, "-m", "screenlab.tui.app", "--remote", f"http://127.0.0.1:{control_port}",
           "--control-port", "0"]
    if args.theme:
        cmd += ["--theme", args.theme]
    return " ".join(shlex.quote(c) for c in cmd)


def serve_main(args: argparse.Namespace) -> None:
    """`--serve`: this process owns the ONE Session and the control endpoint (charts, SSE, MCP control); each
    textual-serve app process talks to it over HTTP (RemoteSession), so every browser tab shows the same state."""
    from textual_serve.server import Server
    from .control import ControlServer, HeadlessHost
    session = build_session(args.lab, args.store)
    serve_url = f"http://{'localhost' if args.host in ('0.0.0.0', '::') else args.host}:{args.port}"
    # the endpoint is the shared session, so it is always on here: `--control-port 0` just picks a free port
    try:
        ctl = ControlServer(HeadlessHost(session), session, args.control_port, serve_url=serve_url).start()
    except OSError as e:
        raise SystemExit(f"cannot start the control endpoint on port {args.control_port}: {e}") from None
    session.log(f"shared session; control endpoint http://127.0.0.1:{ctl.port}")
    session.log(f"charts in the browser: {ctl.home_url()}")
    print(f"screenlab-tui: shared session, control endpoint and charts at {ctl.home_url()}; terminal at {serve_url}",
          flush=True)
    try:
        Server(serve_command(args, ctl.port), host=args.host, port=args.port, title="screenlab").serve()
    finally:
        ctl.stop()
        session.close()


def run(args: argparse.Namespace, ap: argparse.ArgumentParser) -> None:
    """Start the app (or `--serve` / `--remote`) from parsed arguments."""
    if args.serve and args.remote:
        ap.error("--serve and --remote are exclusive")
    if args.serve:
        return serve_main(args)
    if args.remote:
        from .remote import RemoteSession
        try:
            session = RemoteSession(args.remote)
        except SessionError as e:
            raise SystemExit(e.message) from None
        try:
            ScreenlabApp(session, control_port=None, theme=args.theme).run()
        finally:
            session.close()
        return
    session = build_session(args.lab, args.store)
    port = args.control_port if args.control_port != 0 else None
    ScreenlabApp(session, control_port=port, theme=args.theme).run()

