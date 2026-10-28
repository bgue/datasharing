"""screenlab-tui: the Textual app (DESIGN.md L1). All state lives in `Session`; the app renders it.

    screenlab-tui                              # terminal, synthetic or sp500 lab, control endpoint on 8765
    screenlab-tui --serve --port 8000          # the same app in a browser (textual-serve)
    screenlab-tui --lab synthetic --store runs.sqlite --control-port 0 --theme textual-light

Keys: a add, c clone, r run, d delete, x toggle exit (then Apply), F2 Apply (new version + run), m Monte Carlo,
t timing, p picks, s save screen spec, e export, T theme, ? help, q quit.
"""
from __future__ import annotations

import argparse
import os
import shlex
import sys
from datetime import datetime

from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (Button, DataTable, Footer, Input, Label, OptionList, ProgressBar, RichLog, Select,
                             Static, Switch, TabbedContent, TabPane)
from textual.widgets.option_list import Option

from .. import metrics as _metrics
from .session import FINAL, PASSIVE_COLOR, TABS, Session, SessionError

TAB_TITLES = {"compare": "Compare", "curves": "Curves", "timing": "Timing", "picks": "Picks", "exit": "Exit",
              "montecarlo": "Monte Carlo", "runs": "Runs", "log": "Log"}
HELP = """\
[b]screenlab-tui[/b]  scenarios are versions: Apply creates 'name v2' and keeps 'name'.

 a  add scenario          c  clone selected       d  delete selected
 r  run selected (all not run with shift+R)       F2 / Apply  new version + run
 x  toggle trend exit switch (then Apply)
 m  Monte Carlo           t  timing study         p  picks
 s  save screen spec JSON e  export markdown+CSV  T  light/dark theme
 1-8 switch tab           ?  this help            q  quit

Colours: every scenario keeps its colour; the passive index is grey.
Control endpoint: see screenlab/tui/PROTOCOL.md."""
EXIT_PRESET = {"signals": [{"type": "TrendBreak"}], "threshold": 0.8, "hysteresis": 0.1, "park": "cash"}
PARAM_FIELDS = (("n", "Holdings"), ("rebalance_months", "Rebalance (months)"), ("cost_bps", "Cost (bps)"),
                ("commission", "Commission ($)"), ("mer", "MER (/yr)"), ("cash_rate", "Cash rate (/yr)"),
                ("start", "Start date"), ("end", "End date"))


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

    def __init__(self, screens):
        super().__init__()
        self.names = sorted(screens)

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label("Add scenario: choose a screen (Enter), Esc cancels")
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
    """
    BINDINGS = [
        Binding("a", "add", "Add"), Binding("c", "clone", "Clone"), Binding("r", "run", "Run"),
        Binding("R", "run_all", "Run all"), Binding("d", "delete", "Delete"),
        Binding("x", "toggle_exit", "Exit on/off"), Binding("m", "montecarlo", "Monte Carlo"),
        Binding("t", "timing", "Timing"), Binding("p", "picks", "Picks"), Binding("s", "save_spec", "Save spec"),
        Binding("e", "export", "Export"), Binding("T", "theme", "Theme"), Binding("question_mark", "help", "Help"),
        Binding("f2", "apply", "Apply", priority=True), Binding("q", "quit", "Quit"),
        *[Binding(str(i + 1), f"tab('{t}')", show=False) for i, t in enumerate(TABS)],
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
                    yield Label("Screen")
                    yield Select([], id="f-screen", allow_blank=True)
                    for key, title in PARAM_FIELDS:
                        yield Label(title)
                        yield Input(id=f"f-{key}", placeholder="lab default" if key != "n" else "screen default")
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
                with TabPane("Log", id="log"):
                    yield RichLog(id="log-view", wrap=True, markup=False)
        yield Footer()

    def on_mount(self) -> None:
        if self._want_theme:
            try:
                self.theme = self._want_theme
            except Exception:
                self.notify(f"unknown theme {self._want_theme!r}", severity="warning")
        self.session.theme = self.theme
        self.theme_changed_signal.subscribe(self, lambda t: setattr(self.session, "theme", t.name))
        self.query_one("#progress", ProgressBar).display = False
        self.run_worker(self._load, thread=True, exclusive=True)
        self.set_interval(0.25, self._tick)
        if self.control_port is not None:
            self._start_control()

    def _start_control(self) -> None:
        from .control import ControlServer
        try:
            self._control = ControlServer(self, self.session, self.control_port).start()
            self.session.log(f"control endpoint http://127.0.0.1:{self._control.port}")
        except OSError as e:
            self.notify(f"control endpoint not started: {e}", severity="warning")
            self.session.log(f"control endpoint not started: {e}")

    def on_unmount(self) -> None:
        if self._control is not None:
            self._control.stop()
        self.session.close()

    def _load(self) -> None:
        """Build the lab (can take a while for real data) off the UI thread, then draw the header."""
        self.session.card()
        self.session.passive_label()
        self._loaded = True

    # ------------------------------------------------------------------ refresh
    def _tick(self) -> None:
        s = self.session
        s.poll()
        if not self._loaded:
            return
        if s.seq != self._seen:
            self._seen = s.seq
            self.refresh_all()
        self._progress()

    def _progress(self) -> None:
        jobs = [j for j in self.session.cmd_jobs()["jobs"] if j["status"] not in FINAL]
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
        self.query_one("#head", Static).update(card.line() + f"   store: {store or 'off'}")
        warn = Text()
        for w in card.warnings():
            warn.append(w + "\n", style="bold red" if "price index" in w else "bold yellow")
        self.query_one("#warn", Static).update(warn)
        self.query_one("#warn", Static).display = bool(card.warnings())
        if s.theme != self.theme and s.theme in self.available_themes:
            self.theme = s.theme
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
            ol.add_option(Option(Text.assemble((" ● ", sc.color), (sc.label, "bold" if sc.id == s.selected else ""),
                                               (mark, "dim")), id=sc.id))
            if sc.id == s.selected:
                sel = i + 1
        if sel is not None:
            ol.highlighted = sel

    def _refresh_editor(self) -> None:
        s = self.session
        sc = next((x for x in s.scenarios if x.id == s.selected), None)
        sel = self.query_one("#f-screen", Select)
        if sc is None:
            self._shown = None
            return
        if self._shown == sc.id:
            return
        self._shown = sc.id
        names = sorted(s._lab(sc.params.get("lab")).screens)
        scr = sc.params.get("screen")
        opts = [(n, n) for n in names]
        if isinstance(scr, dict):
            opts.insert(0, (f"{scr.get('name', 'spec')} (inline spec)", "__spec__"))
        sel.set_options(opts)
        sel.value = "__spec__" if isinstance(scr, dict) else scr
        for key, _ in PARAM_FIELDS:
            v = sc.params.get(key)
            self.query_one(f"#f-{key}", Input).value = "" if v is None else str(v)
        self.query_one("#f-exit", Switch).value = bool(sc.params.get("exit"))
        self.query_one("#diff", Static).update(sc.diff_line or "")

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
        c = self.session.cmd_compare()
        t = self.query_one("#t-compare", DataTable)
        cols = c["columns"]
        rows = []
        for r in c["rows"]:
            label = Text.assemble(("● ", r["color"]), (r["label"], "bold"),
                                  (" (passive)" if r["passive"] else "", "dim"))
            rows.append([label, *[Text(_metrics.fmt(x, r["values"].get(x)), justify="right") for x in cols]])
        self._fill(t, ["Scenario", *cols], rows)
        note = []
        if c["missing"]:
            note.append("not run yet: " + ", ".join(c["missing"]))
        note += c["diffs"]
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
        h = self.session.cmd_history(limit=30)
        t = self.query_one("#t-runs", DataTable)
        if not h["runs"]:
            self._fill(t, ["Runs"], [[f"store: {h['store'] or 'off (runs are not kept)'}"]])
            return
        cols = ["kind", "label", "screen", "status", "created_at", "run_key"]
        self._fill(t, cols, [[str(r.get(c) or "")[:19 if c == "created_at" else 14 if c == "run_key" else 40]
                              for c in cols] for r in h["runs"]])
        t._run_keys = [r["run_key"] for r in h["runs"]]

    def _draw_log(self) -> None:
        v = self.query_one("#log-view", RichLog)
        v.clear()
        for line in self.session._log[-200:]:
            v.write(line)

    # ------------------------------------------------------------------ events
    @on(TabbedContent.TabActivated)
    def _tab(self, ev: TabbedContent.TabActivated) -> None:
        tab = ev.pane.id
        if tab and tab != self.session.active_tab:
            self.session.active_tab = tab
            self.session._emit("tab")
        if tab and self._loaded:
            self._refresh_tab(tab)

    @on(OptionList.OptionHighlighted, "#scen")
    def _highlight(self, ev: OptionList.OptionHighlighted) -> None:
        if ev.option_id and ev.option_id != self.session.selected:
            self.session.selected = ev.option_id
            self.session._emit("selection")

    @on(Button.Pressed, "#apply")
    def _apply_btn(self) -> None:
        self.action_apply()

    @on(Input.Submitted)
    def _submit(self) -> None:
        self.action_apply()

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
        scr = self.query_one("#f-screen", Select).value
        if scr not in (Select.BLANK, "__spec__", None) and scr != sc.params.get("screen"):
            out["screen"] = scr
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
        names = list(self.session._lab().screens)

        def done(name):
            if name:
                self._cmd("add_scenario", {"screen": name, "run": True})
        self.push_screen(AddScreen(names), done)

    def action_clone(self) -> None:
        self._cmd("clone_scenario", {})

    def action_run(self) -> None:
        self._cmd("run", {})

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


def serve_command(args: argparse.Namespace) -> str:
    cmd = [sys.executable, "-m", "screenlab.tui.app"]
    for flag, val in (("--lab", args.lab), ("--store", args.store), ("--theme", args.theme)):
        if val:
            cmd += [flag, val]
    cmd += ["--control-port", str(args.control_port)]
    return " ".join(shlex.quote(c) for c in cmd)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="screenlab-tui", description="Interactive terminal UI for screenlab.")
    ap.add_argument("--lab", help="dataset name (default: sp500 when skfolio is installed, else synthetic)")
    ap.add_argument("--store", help="run store SQLite path, or 'none' (default: SCREENLAB_STORE or ~/.screenlab/runs.sqlite)")
    ap.add_argument("--control-port", type=int, default=8765, help="control endpoint port on 127.0.0.1; 0 = off")
    ap.add_argument("--serve", action="store_true", help="serve the app in a browser (textual-serve) instead of the terminal")
    ap.add_argument("--host", default="localhost", help="with --serve: host (default localhost)")
    ap.add_argument("--port", type=int, default=8000, help="with --serve: port (default 8000)")
    ap.add_argument("--theme", help="Textual theme name, e.g. textual-dark, textual-light, nord, gruvbox")
    args = ap.parse_args(argv)
    if args.serve:
        from textual_serve.server import Server
        Server(serve_command(args), host=args.host, port=args.port, title="screenlab").serve()
        return
    session = build_session(args.lab, args.store)
    port = args.control_port if args.control_port != 0 else None
    ScreenlabApp(session, control_port=port, theme=args.theme).run()


if __name__ == "__main__":
    main()
