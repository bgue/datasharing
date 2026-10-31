"""TUI Plan tab: session commands `plan` / `plan_mc`, the plain-text view, the browser chart data, and the Textual pane. # PC:

    python -m pytest screenlab/tests/test_tui_plan.py -q
"""
from __future__ import annotations

import json

import pytest

from screenlab.tests.test_tui import arun, make_registry, settle, until
from screenlab.tui import plan as P
from screenlab.tui import web
from screenlab.tui.session import Session, SessionError

TAX = {"model": "canadian", "province": "AB", "account": "tfsa", "other_income": 90000}
FORM = {"use_phases": False, "start": "2002-01-31", "end": "2008-12-31", "initial": 10000, "periodic": 500, "growth": "cpi", "withdrawals": 800,
        "withdraw_from": "2007-01-01", "goal": 90000, "add_extra": {"date": "2004-03-01", "amount": 5000, "note": "bonus"}}


@pytest.fixture(scope="module")
def s():
    sess = Session(make_registry(), own=True)
    sess.command("add_scenario", {"screen": "dogs", "run": True, "wait": True, "rebalance_months": 12, "tax": TAX})
    yield sess
    sess.close()


@pytest.fixture(scope="module")
def ran(s):
    r = s.command("plan", {**FORM, "wait": True, "timeout": 120})
    assert r["jobs"][0]["kind"] == "plan" and r["jobs"][0]["status"] == "done", r
    m = s.command("plan_mc", {"n_paths": 40, "generator": "stationary", "seed": 3, "wait": True, "timeout": 180})
    assert m["jobs"][0]["kind"] == "plan_montecarlo" and m["jobs"][0]["status"] == "done", m
    return r, m


def test_tab_and_commands_are_registered():
    from screenlab.tui.session import COMMANDS, TABS
    assert "plan" in TABS and {"plan", "plan_mc"} <= set(COMMANDS) and "plan" in web.CHART_TABS and "plan" in web.BUILDERS


def test_empty_session_view_and_chart():
    sess = Session(make_registry(), own=True)
    try:
        assert "no plan yet" in sess.view("plan") and web.chart_data(sess, "plan")["empty"] is True
        with pytest.raises(SessionError, match="no scenario"):
            sess.command("plan", {"run": False})
    finally:
        sess.close()


def test_form_is_remembered_and_extras_edited(s, ran):
    f = s.analyses["plan_form"]
    assert f["periodic"] == 500 and f["growth"] == "cpi" and f["extra"] == [{"date": "2004-03-01", "amount": 5000.0, "note": "bonus"}]
    s.command("plan", {"run": False, "add_extra": {"date": "2003-03-01", "amount": -1000, "note": "w"}})
    assert [x["date"] for x in f["extra"]] == ["2003-03-01", "2004-03-01"]                    # kept sorted
    s.command("plan", {"run": False, "remove_extra": 0})
    assert [x["date"] for x in f["extra"]] == ["2004-03-01"]
    with pytest.raises(SessionError, match="remove_extra"):
        s.command("plan", {"run": False, "remove_extra": 5})
    with pytest.raises(SessionError, match="unknown plan field"):
        s.command("plan", {"bogus": 1, "run": False})
    with pytest.raises(SessionError, match="frequency"):
        s.command("plan", {"run": False, "frequency": "daily"})


def test_results_are_kept_and_viewed(s, ran):
    r = s.analyses["plan"]["dogs"]
    assert r["plan"]["spec"]["account"] == "tfsa" and r["mc"]["n_paths"] == 40
    v = s.command("view", {"tab": "plan"})["view"]
    assert "FinalAfterTax" in v and "XIRR" in v and "strategy wins" in v and "Monte Carlo: 40 paths" in v
    assert "P(strategy beats index" in v and "sequence of returns" in v and "goal 90,000" in v
    assert "plan" in s.snapshot()["analyses"] and s.snapshot()["analyses"]["plan_form"]["goal"] == 90000


def test_chart_data_builder_returns_the_traces(s, ran):
    d = web.chart_data(s, "plan")
    json.dumps(d)                                                       # JSON-able
    assert d["empty"] is False and len(d["plans"]) == 1
    p = d["plans"][0]
    assert p["label"] == "dogs" and p["color"] == s.scenarios[0].color and p["goal"] == 90000
    assert set(p["series"]) == {"contributed", "strategy_pre", "strategy_after_tax", "index_pre", "index_after_tax"}
    n = len(p["x"])
    assert n > 50 and all(len(v) == n for v in p["series"].values())
    assert p["series"]["contributed"][0] == 10500.0 and p["series"]["strategy_pre"][-1] > 0
    assert [f["kind"] for f in p["flows"]].count("extra") == 1
    names = [m["metric"] for m in p["metrics"]]
    assert names[:3] == ["Contributed", "FinalPreTax", "FinalAfterTax"] and "TaxPaid" in names
    wins = {m["metric"]: m["better"] for m in p["metrics"]}
    fp = next(m for m in p["metrics"] if m["metric"] == "FinalPreTax")
    assert wins["FinalPreTax"] == (fp["strategy"] > fp["index"]) and wins["Contributed"] is None and wins["Beta"] is None
    mc = p["mc"]
    assert mc["n_paths"] == 40 and 0 <= mc["p_beat_index"] <= 1
    fan = mc["fan"]
    assert {"x", "contributed", "strategy_p5", "strategy_p50", "strategy_p95", "index_p50"} <= set(fan)
    assert all(a <= b <= c for a, b, c in zip(fan["strategy_p5"], fan["strategy_p50"], fan["strategy_p95"]))
    assert sum(mc["histograms"]["strategy_after_tax"]["counts"]) == 40 and mc["sequence"]["rows"]


def test_changing_the_plan_drops_a_stale_monte_carlo(s, ran):
    s.command("plan", {"periodic": 400, "wait": True, "timeout": 120})
    assert s.analyses["plan"]["dogs"]["plan"]["spec"]["periodic"] == 400 and s.analyses["plan"]["dogs"]["mc"] is None
    s.command("plan", {"periodic": 500, "wait": True, "timeout": 120})


def test_text_helpers():
    assert P.sparkline([1, 2, 3, 4], 4) == "▁▃▆█" or len(P.sparkline([1, 2, 3, 4], 4)) == 4
    assert P.sparkline([]) == "" and len(P.sparkline(list(range(500)), 40)) == 40
    lines = P.text_hist({"edges": [0, 1000, 2000], "counts": [1, 3]}, 8, "x")
    assert lines[0] == "x" and "25.0%" in lines[1] and "75.0%" in lines[2] and lines[2].count("█") == 8


def test_scenario_with_an_exit_policy_is_refused():
    sess = Session(make_registry(), own=True)
    try:
        sess.command("add_scenario", {"screen": "dogs", "exit": {"signals": [{"type": "TrendBreak"}], "threshold": 0.8}})
        with pytest.raises(SessionError, match="exit policy"):
            sess.command("plan", {"start": "2002-01-31"})
        sess.command("remove_scenario", {"scenario": "dogs"})
        assert sess.analyses["plan"] == {}
    finally:
        sess.close()


def test_app_plan_tab_runs_a_plan_and_draws(s):
    pytest.importorskip("textual")
    pytest.importorskip("textual_plotext")
    from rich.text import Text
    from textual.widgets import Input
    from screenlab.tui.app import ScreenlabApp

    async def go():
        sess = Session(make_registry(), own=True)
        app = ScreenlabApp(sess, control_port=None)
        async with app.run_test(size=(190, 70)) as pilot:
            await settle(pilot, app)
            sess.command("add_scenario", {"screen": "dogs", "run": True, "wait": True, "rebalance_months": 12, "tax": TAX})
            await settle(pilot, app)
            await pilot.press("P")
            await settle(pilot, app)
            assert sess.active_tab == "plan" and app.query_one("#main").active == "plan"
            for wid, v in (("#pl-start", "2002-01-31"), ("#pl-end", "2007-12-31"), ("#pl-initial", "10000"), ("#pl-periodic", "500"),
                           ("#pl-growth", "cpi"), ("#pl-goal", "80000"), ("#pl-npaths", "30")):
                app.query_one(wid, Input).value = v
            app.query_one("#pl-x-date", Input).value = "2004-03-01"
            app.query_one("#pl-x-amount", Input).value = "5000"
            app.query_one("#pl-x-add").press()
            await until(lambda: app.query_one("#pl-extras").row_count == 1, 10, pilot, "an extras row")
            app.query_one("#pl-run").press()
            await until(lambda: (sess.analyses["plan"].get("dogs") or {}).get("plan") is not None, 60, pilot, "plan result")
            await settle(pilot, app)
            t = app.query_one("#pl-metrics")
            rows = {str(t.get_row_at(i)[0]): t.get_row_at(i) for i in range(t.row_count)}
            assert {"Contributed", "FinalAfterTax", "XIRR", "Sharpe", "Alpha"} <= set(rows)
            wins = {k for k, r in rows.items() if isinstance(r[1], Text) and "green" in str(r[1].style)}
            fp = sess.analyses["plan"]["dogs"]["plan"]
            rec = {x["metric"]: x for x in P.metric_records(fp)}
            assert wins == {k for k, x in rec.items() if x["better"]}
            assert "contributed" in str(app.query_one("#pl-spark").render()) and "strategy, after tax" in str(app.query_one("#pl-spark").render())
            app.query_one("#pl-run-mc").press()
            await until(lambda: (sess.analyses["plan"]["dogs"] or {}).get("mc") is not None, 90, pilot, "mc result")
            await settle(pilot, app)
            assert app.query_one("#pl-q").row_count == 5 and app.query_one("#pl-seq").row_count >= 1
            assert "P(strategy beats the index" in str(app.query_one("#pl-mc").render())
            assert "terminal wealth, strategy" in str(app.query_one("#pl-hist").render())
        sess.close()
    arun(go())


# V2: contribution phases
def test_default_form_carries_the_base_case_with_phases_on():
    f = P.new_form()
    assert f["use_phases"] is True and f["phase_growth"] == "indexed"
    assert [(x["years_from"], x["years_to"], x["amount"]) for x in f["phases"]] == [(0, 10, 500), (10, 13, 3000), (13, 20, 1000)]
    f["phases"][0]["amount"] = 1                                          # a fresh form never aliases the defaults
    assert P.new_form()["phases"][0]["amount"] == 500
    sess = Session(make_registry(), own=True)
    try:
        assert sess.snapshot()["analyses"]["plan_form"]["phases"][1]["amount"] == 3000
    finally:
        sess.close()


def test_phase_table_edits_flat_button_and_validation():
    f = P.new_form()
    P.update_form(f, {}, remove_phase=1)
    assert [x["amount"] for x in f["phases"]] == [500, 1000]
    P.update_form(f, {}, add_phase={"years_from": 10, "years_to": 13, "amount": "2500", "note": "n"})
    assert [x["amount"] for x in f["phases"]] == [500, 2500, 1000] and f["phases"][1]["note"] == "n"      # kept sorted
    P.update_form(f, {}, set_phase={"index": 1, "amount": 2600, "years_to": None})
    assert f["phases"][1]["amount"] == 2600 and f["phases"][1]["years_to"] is None
    for kw, word in (({"add_phase": {"years_from": 5, "years_to": 4, "amount": 1}}, "years_to"),
                     ({"add_phase": {"years_from": 5, "years_to": 6, "amount": -1}}, "amount"),
                     ({"remove_phase": 9}, "remove_phase"), ({"set_phase": {"index": 9}}, "set_phase"),
                     ({"add_phase": {"years_from": "x", "amount": 1}}, "years_from")):
        with pytest.raises(SessionError, match=word):
            P.update_form(f, {}, **kw)
    with pytest.raises(SessionError, match="phase_growth"):
        P.update_form(f, {"phase_growth": "real"})
    f = P.new_form()
    f["periodic"] = 750.0
    P.update_form(f, {}, flat_phase=True)
    assert f["phases"] == [{"years_from": 0.0, "years_to": None, "amount": 750.0, "note": "flat"}] and f["use_phases"] is True


def test_phases_off_sends_flat_and_on_sends_phases_not_periodic():
    class S:
        _screen_ref = staticmethod(lambda p: "dogs")
    sc = type("Sc", (), {"params": {}, "label": "dogs"})()
    f = P.new_form()
    f["start"] = "2002-01-31"
    a = P.args_for(S(), sc, f)
    assert a["periodic"] == 0.0 and [x["amount"] for x in a["phases"]] == [500, 3000, 1000] and a["phase_growth"] == "indexed"
    P.update_form(f, {"use_phases": False})
    a = P.args_for(S(), sc, f)
    assert a["periodic"] == 500 and "phases" not in a
    P.update_form(f, {"use_phases": True, "phases": [{"years_from": 0, "years_to": 3, "amount": 1}, {"years_from": 2, "years_to": 4, "amount": 1}]})
    with pytest.raises(SessionError, match="overlap"):
        P.args_for(S(), sc, f)


def test_plan_run_through_the_tui_with_phases_matches_per_phase_sums_and_toggle_off_restores_flat():
    sess = Session(make_registry(), own=True)
    try:
        sess.command("add_scenario", {"screen": "dogs", "run": True, "wait": True, "rebalance_months": 12})
        ph = [{"years_from": 0, "years_to": 2, "amount": 100, "note": "a"}, {"years_from": 3, "years_to": 4, "amount": 300, "note": "b"}]
        r = sess.command("plan", {"start": "2002-01-31", "end": "2007-12-31", "initial": 0, "periodic": 999, "phases": ph,
                                  "wait": True, "timeout": 120})
        assert r["jobs"][0]["status"] == "done", r
        p = sess.analyses["plan"]["dogs"]["plan"]
        assert p["spec"]["periodic"] == 0 and [x["amount"] for x in p["spec"]["phases"]] == [100, 300]
        summ = P.phase_summary(p)
        assert [(x["count"], x["total"]) for x in summ] == [(24, 2400.0), (12, 3600.0)]                # the gap year pays nothing
        c = p["flows"]["columns"]
        per = [row for row in p["flows"]["rows"] if row[c.index("kind")] == "periodic"]
        assert sum(row[c.index("amount")] for row in per) == 6000.0 and len(per) == 36
        assert "contributed by phase: years 0-2: 24 x -> 2,400; years 3-4: 12 x -> 3,600" in sess.view("plan")
        assert "contribution phases (in use)" in sess.view("plan")
        r = sess.command("plan", {"use_phases": False, "periodic": 200, "wait": True, "timeout": 120})
        assert r["jobs"][0]["status"] == "done", r
        p = sess.analyses["plan"]["dogs"]["plan"]
        assert p["spec"]["periodic"] == 200 and p["spec"]["phases"] == [] and P.phase_summary(p) == []
        fl = {row[c.index("amount")] for row in p["flows"]["rows"] if row[c.index("kind")] == "periodic"}
        assert fl == {200.0} and "OFF, flat periodic used" in sess.view("plan")
        json.dumps(sess.snapshot())
    finally:
        sess.close()


def test_app_phases_table_toggle_and_flat_button():
    pytest.importorskip("textual")
    pytest.importorskip("textual_plotext")
    from textual.widgets import Input, Switch
    from screenlab.tui.app import ScreenlabApp

    async def go():
        sess = Session(make_registry(), own=True)
        app = ScreenlabApp(sess, control_port=None)
        async with app.run_test(size=(190, 70)) as pilot:
            await settle(pilot, app)
            sess.command("add_scenario", {"screen": "dogs", "run": True, "wait": True, "rebalance_months": 12})
            await settle(pilot, app)
            await pilot.press("P")
            await settle(pilot, app)
            t = app.query_one("#pl-phases")
            assert t.row_count == 3 and app.query_one("#pl-use-phases", Switch).value is True
            assert app.query_one("#pl-periodic", Input).disabled is True
            for wid, v in (("#pl-p-from", "20"), ("#pl-p-to", "25"), ("#pl-p-amount", "50")):
                app.query_one(wid, Input).value = v
            app.query_one("#pl-p-add").press()
            await until(lambda: app.query_one("#pl-phases").row_count == 4, 10, pilot, "a phase row")
            app.query_one("#pl-phases").move_cursor(row=3)
            app.query_one("#pl-p-del").press()
            await until(lambda: app.query_one("#pl-phases").row_count == 3, 10, pilot, "a phase row removed")
            app.query_one("#pl-periodic", Input).value = "700"
            app.query_one("#pl-p-flat").press()
            await until(lambda: app.query_one("#pl-phases").row_count == 1, 10, pilot, "flat phase")
            assert sess.analyses["plan_form"]["phases"][0]["amount"] == 700
            app.query_one("#pl-use-phases", Switch).value = False
            await until(lambda: sess.analyses["plan_form"]["use_phases"] is False, 10, pilot, "toggle off")
            await settle(pilot, app)
            assert app.query_one("#pl-periodic", Input).disabled is False
        sess.close()
    arun(go())
