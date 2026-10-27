"""Drift guard: schema/runstore.linkml.yaml must describe exactly the tables SQLiteStore creates.

The LinkML package is not required (it does not install on every Python); the YAML is read with pyyaml and
checked structurally. If `linkml` / `linkml_runtime` happen to be installed, the schema is also loaded with
SchemaView. Failing here means: a column or table changed in store.py without the schema (or vice versa),
or a value the code writes is missing from an enum.
"""
import json
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from screenlab import Lab, SQLiteStore
from screenlab import jobs as J
from screenlab import runspec as R
from screenlab import store as S

SCHEMA = Path(__file__).resolve().parent.parent / "schema" / "runstore.linkml.yaml"
TABLE_OF = {"Run": "runs", "Metric": "metrics", "Artifact": "artifacts", "Dataset": "datasets",
            "Trial": "trials", "Job": "jobs"}
BUILTIN = {"string": "TEXT", "uriorcurie": "TEXT", "uri": "TEXT", "curie": "TEXT", "ncname": "TEXT",
           "objectidentifier": "TEXT", "nodeidentifier": "TEXT", "integer": "INTEGER", "boolean": "INTEGER",
           "float": "REAL", "double": "REAL", "decimal": "REAL", "datetime": "TEXT", "date": "TEXT",
           "time": "TEXT"}


@pytest.fixture(scope="module")
def schema():
    return yaml.safe_load(SCHEMA.read_text())


def affinity(schema, rng):
    """SQLite declared type a LinkML range maps to."""
    if rng in schema.get("enums", {}):
        return "TEXT"
    if rng in schema["classes"]:
        ids = [a for a, d in schema["classes"][rng]["attributes"].items() if d.get("identifier")]
        assert len(ids) == 1, f"class {rng} used as a range needs exactly one identifier"
        return affinity(schema, schema["classes"][rng]["attributes"][ids[0]].get("range", schema["default_range"]))
    t = schema.get("types", {}).get(rng)
    if t is not None:
        if t.get("base") == "bytes":
            return "BLOB"
        return affinity(schema, t["typeof"])
    assert rng in BUILTIN, f"unknown range {rng!r}"
    return BUILTIN[rng]


def slot_range(schema, d):
    return d.get("range", schema["default_range"])


@pytest.fixture(scope="module")
def live(tmp_path_factory):
    """table -> {column: (declared type, notnull, pk position)} read from a fresh store, plus FK lists."""
    p = tmp_path_factory.mktemp("schema") / "runs.sqlite"
    SQLiteStore(p).close()
    con = sqlite3.connect(p)
    tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    out = {t: {r[1]: (r[2].upper(), bool(r[3]), r[5]) for r in con.execute(f"PRAGMA table_info({t})")} for t in tables}
    fks = {t: [(r[3], r[2], r[4]) for r in con.execute(f"PRAGMA foreign_key_list({t})")] for t in tables}
    con.close()
    return out, fks


# ---------------------------------------------------------------------------------------------------
# the schema file on its own
# ---------------------------------------------------------------------------------------------------
def test_schema_header_and_resolvable_ranges(schema):
    for k in ("id", "name", "description", "prefixes", "default_prefix", "default_range", "imports", "classes", "enums"):
        assert schema.get(k), f"missing top-level {k}"
    assert "linkml" in schema["prefixes"] and schema["default_prefix"] in schema["prefixes"]
    assert "linkml:types" in schema["imports"]
    for name, d in {**schema["classes"], **schema["enums"], **schema.get("types", {})}.items():
        assert d.get("description"), f"{name} needs a description"
    for cname, c in schema["classes"].items():
        ids = [a for a, d in c["attributes"].items() if d.get("identifier")]
        assert len(ids) <= 1, cname
        for a, d in c["attributes"].items():
            assert d.get("description"), f"{cname}.{a} needs a description"
            affinity(schema, slot_range(schema, d))                                   # raises when unresolvable
        for uk in c.get("unique_keys", {}).values():
            assert set(uk["unique_key_slots"]) <= set(c["attributes"])
    for ename, e in schema["enums"].items():
        for v, d in e["permissible_values"].items():
            assert (d or {}).get("description"), f"{ename}.{v} needs a description"


def test_schema_loads_with_linkml_when_available():
    sv = pytest.importorskip("linkml_runtime.utils.schemaview")
    view = sv.SchemaView(str(SCHEMA))
    assert set(view.all_classes()) == set(TABLE_OF)
    assert set(view.all_enums()) >= {"RunKind", "RunStatus", "PersistLevel", "ArtifactFormat", "JobStatus"}
    try:
        from linkml.linter.linter import Linter
    except ImportError:
        pytest.skip("linkml (linter) not installed")
    problems = list(Linter().lint(str(SCHEMA)))
    assert not [p for p in problems if getattr(p, "level", "") == "error"], problems


# ---------------------------------------------------------------------------------------------------
# tables and columns, both ways
# ---------------------------------------------------------------------------------------------------
def test_classes_and_tables_match(schema, live):
    assert set(schema["classes"]) == set(TABLE_OF), "class list changed; update TABLE_OF here and the schema"
    assert {TABLE_OF[c] for c in schema["classes"]} == set(live[0]), "tables in store.py vs classes in the schema"


@pytest.mark.parametrize("cls", sorted(TABLE_OF))
def test_columns_match(schema, live, cls):
    cols = live[0][TABLE_OF[cls]]
    attrs = schema["classes"][cls]["attributes"]
    assert set(attrs) == set(cols), (f"{cls}: only in schema {sorted(set(attrs) - set(cols))}, "
                                     f"only in database {sorted(set(cols) - set(attrs))}")
    for name, d in attrs.items():
        typ, notnull, pk = cols[name]
        assert affinity(schema, slot_range(schema, d)) == typ, f"{cls}.{name}: schema {slot_range(schema, d)} vs {typ}"
        assert bool(d.get("required", False)) == (notnull or pk > 0), f"{cls}.{name}: required vs NOT NULL / PRIMARY KEY"


@pytest.mark.parametrize("cls", sorted(TABLE_OF))
def test_primary_keys_match(schema, live, cls):
    c = schema["classes"][cls]
    pk_cols = {n for n, (_, _, pk) in live[0][TABLE_OF[cls]].items() if pk > 0}
    ident = {a for a, d in c["attributes"].items() if d.get("identifier")}
    unique = [set(u["unique_key_slots"]) for u in c.get("unique_keys", {}).values()]
    if len(pk_cols) == 1:
        assert ident == pk_cols and not unique
    elif pk_cols:
        assert not ident and unique == [pk_cols], f"{cls}: composite primary key {sorted(pk_cols)}"
    else:
        assert not ident and not unique, f"{cls}: schema declares a key the table does not have"


def test_foreign_keys(schema, live):
    """The DDL declares none; schema class-ranged slots are the logical links and must point at real keys."""
    logical = {(TABLE_OF[c], a): slot_range(schema, d) for c, cd in schema["classes"].items()
               for a, d in cd["attributes"].items() if slot_range(schema, d) in schema["classes"]}
    assert logical, "expected logical foreign keys (metrics.run_key -> Run ...)"
    for (table, col), target in logical.items():
        assert col in live[0][table]
        tkey = [a for a, d in schema["classes"][target]["attributes"].items() if d.get("identifier")][0]
        assert tkey in live[0][TABLE_OF[target]]
    assert logical[("metrics", "run_key")] == "Run" and logical[("artifacts", "run_key")] == "Run"
    for table, fk in live[1].items():                                    # if a real FOREIGN KEY is ever added
        for col, ref_table, ref_col in fk:
            assert logical.get((table, col)) is not None and TABLE_OF[logical[(table, col)]] == ref_table, \
                f"{table}.{col} -> {ref_table}.{ref_col} is declared in SQL but not in the schema"


# ---------------------------------------------------------------------------------------------------
# enums against what the code writes
# ---------------------------------------------------------------------------------------------------
def values(schema, enum):
    return set(schema["enums"][enum]["permissible_values"])


@pytest.fixture(scope="module")
def written(tmp_path_factory):
    """A store filled by the real code paths: all persist levels, failure, cancel, timing, sig, job, payload."""
    p = tmp_path_factory.mktemp("written") / "runs.sqlite"
    st = SQLiteStore(p)
    lab = Lab.synthetic(freq="ME", n_stocks=60, years=8, seed=3, store=st, persist="full")
    lab.name = "synthetic"
    rep = lab.backtest(["dogs"], rebalance_months=6)
    lab.significance(rep, n=50)
    lab.timing("dogs", stride=40, rebalance_months=6)
    lab.picks("dogs")
    lab.graham("defensive")
    for level in ("inputs", "metrics"):
        lab.persist = level
        lab.backtest(["dogs"], rebalance_months=3 if level == "inputs" else 4)
    lab.persist = "full"
    spec = R.RunSpec("backtest", "synthetic", {"screens": ["dogs"]})
    st.record(spec, None, "k_payload", "d", "metrics", payload={"x": 1})            # json.gz artifact
    k = st.begin_run(spec, "k_running", "d", "metrics")
    st.begin_run(spec, "k_fail", "d", "metrics")
    st.fail_run("k_fail", "boom")
    st.begin_run(spec, "k_cancel", "d", "metrics")
    st.fail_run("k_cancel", "cancelled", "cancelled")
    job = lab.submit("backtest", screens=["dogs"], rebalance_months=9)
    job.result()
    st.close()
    con = sqlite3.connect(p)
    yield con
    con.close()


def observed(con, sql):
    return {r[0] for r in con.execute(sql)}


def test_enum_kind(schema, written):
    # J.KINDS is process-global and other tests register fake kinds (jobs.register_kind), so read a pristine copy
    out = subprocess.run([sys.executable, "-c", "import json; from screenlab import jobs; print(json.dumps(sorted(jobs.KINDS)))"],
                         capture_output=True, text=True, check=True, cwd=str(SCHEMA.parents[2])).stdout
    assert values(schema, "RunKind") == set(R.BUNDLED_KINDS) == set(json.loads(out))
    seen = observed(written, "SELECT DISTINCT kind FROM runs")
    assert {"backtest", "timing", "significance", "picks", "graham"} <= seen <= values(schema, "RunKind")


def test_enum_run_status(schema, written):
    assert observed(written, "SELECT DISTINCT status FROM runs") == values(schema, "RunStatus")


def test_enum_persist_level(schema, written):
    assert values(schema, "PersistLevel") == set(S.LEVELS)
    assert observed(written, "SELECT DISTINCT persist_level FROM runs") == values(schema, "PersistLevel") - {"none"}


def test_enum_artifact_format(schema, written):
    assert observed(written, "SELECT DISTINCT format FROM artifacts") == values(schema, "ArtifactFormat")


def test_enum_job_status(schema, written):
    seen = observed(written, "SELECT DISTINCT status FROM jobs")
    assert seen and seen <= values(schema, "JobStatus")
    src = Path(J.__file__).read_text() + Path(S.__file__).read_text()
    literals = set(re.findall(r'status\s*=\s*"(\w+)"', src)) | set(re.findall(r'put\([^()]*"(queued|running|done)"', src))
    literals -= {"inputs", "metrics", "full", "none"}
    assert literals <= values(schema, "JobStatus") | values(schema, "RunStatus"), literals
    assert {"queued", "running", "done", "failed", "cancelled"} == values(schema, "JobStatus")


def test_standard_metrics_cover_what_is_stored(schema, written):
    std = values(schema, "StandardMetric")
    seen = observed(written, "SELECT DISTINCT m.metric FROM metrics m JOIN runs r USING (run_key) "
                             "WHERE r.kind IN ('backtest', 'timing', 'significance')")
    assert seen <= std, f"metrics written but not documented: {sorted(seen - std)}"
    assert {"CAGR", "Vol", "Sharpe", "MaxDD", "Worst12m", "BeatBenchYrs", "Excess/yr", "Turnover", "Costs/yr",
            "TimeOut", "Switches/yr", "CI5", "CI95", "DSR"} <= std
    from screenlab.api import PCT_COLS
    assert set(PCT_COLS) <= std


def test_values_written_have_declared_shapes(written):
    """Spot checks of documented conventions: epoch seconds, 1/0 flags, spec_json is JSON, tags a JSON list."""
    import json
    t, pers = written.execute("SELECT created_at, persistable FROM runs WHERE kind='backtest' LIMIT 1").fetchone()
    assert t > 1e9 and pers in (0, 1)
    sj, tags, scr = written.execute("SELECT spec_json, tags, screens FROM runs WHERE run_key NOT LIKE 'k\\_%' ESCAPE '\\' LIMIT 1").fetchone()
    assert json.loads(sj)["kind"] and isinstance(json.loads(tags), list) and scr.startswith(",") and scr.endswith(",")


# ---------------------------------------------------------------------------------------------------
# metric registry <-> StandardMetric, DataCard in datasets
# ---------------------------------------------------------------------------------------------------
def test_standard_metric_enum_is_the_metric_registry(schema):
    from screenlab.metrics import METRICS, linkml_enum_values
    pv = schema["enums"]["StandardMetric"]["permissible_values"]
    assert list(pv) == list(METRICS), "regenerate the enum: python -m screenlab.metrics --linkml"
    assert {k: {"description": v["description"]} for k, v in pv.items()} == linkml_enum_values()
    for name, m in METRICS.items():
        assert m.unit and m.format in ("pct", "num") and m.direction in ("higher", "lower", None) and m.label
        assert m.description.strip(), name


def test_datasets_table_stores_the_data_card(tmp_path):
    st = SQLiteStore(tmp_path / "c.sqlite")
    lab = Lab.synthetic(freq="ME", n_stocks=60, years=8, seed=3, store=st, persist="metrics")
    lab.name = "synthetic"
    lab.backtest(["dogs"], rebalance_months=6)
    did = R.dataset_id(lab.market)
    card = st.data_card(did)
    assert card and card["name"] == lab.data_card().name and card["benchmark_kind"] == "total_return"
    row = st.datasets().set_index("dataset_id").loc[did]
    assert row["source"] == card["source"] and row["pit"] in (0, 1, None)
    assert st._db().execute("SELECT survivorship_score, card_json FROM datasets").fetchone()[1]


def test_old_store_files_gain_the_card_columns(tmp_path):
    p = tmp_path / "old.sqlite"
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE datasets (dataset_id TEXT PRIMARY KEY, name TEXT, span TEXT, n_names INTEGER,"
                " benchmark TEXT, benchmark_kind TEXT, manifest_json TEXT)")
    con.execute("INSERT INTO datasets VALUES ('d1','x','a/b',3,'B','IndexBenchmark','{}')")
    con.commit()
    con.close()
    st = SQLiteStore(p)
    assert {"source", "pit", "delisted_included", "survivorship_score", "card_json"} <= {
        r[1] for r in st._db().execute("PRAGMA table_info(datasets)")}
    assert st.data_card("d1") is None and st.dataset("d1") == {}
