"""Guardrails behind the TUI (ROADMAP Phase 2): deflated Sharpe per Compare row and the pre-registration file.

No Textual import. The multiple-testing argument is in docs/BAYESIAN_SWEEPS.md: the more variants you look at, the
better the best one looks by luck alone, so a row's Sharpe is judged against the Sharpe expected from the luckiest
of `n_trials` skill-less variants of the same family (Bailey and Lopez de Prado), not against zero.

    curve_stats(curve)        -> n_obs, periods per year, skew, kurtosis of the periodic returns of an equity curve
    row_dsr(...)              -> the deflated Sharpe of one row, or None when it cannot be computed
    load_preregistered(path)  -> hypotheses parsed from research/PREREGISTERED.md
    evaluate(hyp, values, span_years) -> {"met": True | False | None, ...} for a linked scenario's latest result
"""
from __future__ import annotations

import math
import re
from datetime import datetime
from pathlib import Path

import numpy as np

from .. import stats as _stats

PREREG_PATH = Path(__file__).resolve().parents[1] / "research" / "PREREGISTERED.md"
HYP_FIELDS = ("name", "screen", "dataset", "benchmark", "horizon", "metric", "threshold", "costs", "exit",
              "written", "status")
STATUSES = ("open", "supported", "refuted")
_OPS = {">=": lambda a, b: a >= b, "<=": lambda a, b: a <= b, ">": lambda a, b: a > b, "<": lambda a, b: a < b,
        "=": lambda a, b: abs(a - b) < 1e-12}


# ---------------------------------------------------------------------- deflated Sharpe
def _years(a: str, b: str) -> float:
    return (datetime.fromisoformat(b[:10]) - datetime.fromisoformat(a[:10])).days / 365.25


def curve_stats(curve) -> dict | None:
    """Number of return observations, periods per year, skewness and (non-excess) kurtosis of the periodic returns
    of an equity curve given as `[[iso_date, value], ...]`; None when the curve is too short to say."""
    pts = [(d, v) for d, v in (curve or []) if v is not None and v == v and v > 0]
    if len(pts) < 5:
        return None
    yrs = _years(pts[0][0], pts[-1][0])
    if yrs <= 0:
        return None
    vals = np.array([v for _, v in pts], float)
    r = vals[1:] / vals[:-1] - 1
    sd = r.std()
    if sd <= 0:
        return None
    z = (r - r.mean()) / sd
    return {"n_obs": int(len(r)), "ppy": (len(pts) - 1) / yrs, "skew": float((z ** 3).mean()),
            "kurt": float((z ** 4).mean()), "years": yrs, "start": pts[0][0], "end": pts[-1][0]}


def row_dsr(sharpe, st: dict | None, n_trials: int, family_sharpes=()) -> float | None:
    """Deflated Sharpe of one row: P(its true Sharpe is above what the luckiest of `n_trials` skill-less variants
    would show). `sharpe` is annual, `st` is `curve_stats` of its curve, `family_sharpes` the annual Sharpes of
    every variant of the family that has a result. The cross-variant variance of the per-period Sharpe has a floor
    of 1/(n_obs - 1), the sampling variance of one Sharpe estimate: two near-identical variants must still count as
    two looks. With one trial this is the plain probabilistic Sharpe against zero. None when not computable."""
    if sharpe is None or st is None or not math.isfinite(sharpe):
        return None
    per = sharpe / math.sqrt(st["ppy"])
    sh = [s / math.sqrt(st["ppy"]) for s in family_sharpes if s is not None and math.isfinite(s)]
    var = float(np.var(sh, ddof=1)) if len(sh) > 1 else 0.0
    var = max(var, 1.0 / max(st["n_obs"] - 1, 1))
    sr0 = _stats.expected_max_sharpe(var, max(int(n_trials), 1))
    out = _stats.probabilistic_sharpe(per, st["n_obs"], st["skew"], st["kurt"], sr0)
    return None if out != out else float(out)


# ---------------------------------------------------------------------- the pre-registration file
def _field_key(raw: str) -> str:
    k = raw.strip().lower().replace("*", "").replace("`", "")
    return {"screen/spec": "screen", "spec": "screen", "cost": "costs", "exit policy": "exit",
            "date written": "written", "date": "written"}.get(k, k)


def parse_preregistered(text: str) -> list:
    """Hypotheses from the markdown of PREREGISTERED.md: one `## H<n> ...` heading per hypothesis followed by
    `- field: value` bullets (fields in HYP_FIELDS). HTML comments (the blank template) are ignored. A heading that
    contains EXAMPLE marks an illustration, not a real pre-registration."""
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    out, cur = [], None
    for line in text.splitlines():
        m = re.match(r"^##\s+(H\d+)\b[\s:.\-–—]*(.*)$", line)
        if m:
            cur = {"id": m.group(1), "heading": m.group(2).strip(), "example": "EXAMPLE" in line.upper(),
                   **{k: None for k in HYP_FIELDS}}
            out.append(cur)
            continue
        if line.startswith("## "):
            cur = None
            continue
        b = re.match(r"^\s*[-*]\s+\**([A-Za-z/ ]+?)\**\s*:\s*(.*)$", line)
        if cur is not None and b:
            key = _field_key(b.group(1))
            if key in HYP_FIELDS:
                cur[key] = b.group(2).strip().strip("`")
    for h in out:
        h["name"] = h["name"] or h["heading"] or h["id"]
        st = (h["status"] or "open").split()[0].lower().strip(".,;")
        h["status"] = st if st in STATUSES else "open"
        h["problems"] = [f"missing {k}" for k in ("screen", "dataset", "benchmark", "horizon", "metric", "threshold",
                                                  "costs", "written") if not h[k]]
    return out


def load_preregistered(path=None) -> dict:
    """`{"path", "exists", "hypotheses": [...], "warnings": [...]}`; a missing or unreadable file is reported."""
    p = Path(path) if path else PREREG_PATH
    if not p.is_file():
        return {"path": str(p), "exists": False, "hypotheses": [],
                "warnings": [f"no pre-registration file at {p}: write your hypotheses before the first real-data run"]}
    try:
        hyps = parse_preregistered(p.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as e:
        return {"path": str(p), "exists": True, "hypotheses": [], "warnings": [f"cannot read {p}: {e}"]}
    warn = [f"{h['id']}: " + ", ".join(h["problems"]) for h in hyps if h["problems"]]
    ids = [h["id"] for h in hyps]
    warn += [f"duplicate hypothesis id {i}" for i in sorted({i for i in ids if ids.count(i) > 1})]
    if hyps and all(h["example"] for h in hyps):
        warn.append("these are EXAMPLES: replace them with your own before the first real-data run")
    return {"path": str(p), "exists": True, "hypotheses": hyps, "warnings": warn}


def parse_threshold(text) -> tuple | None:
    """`">= 0.02"`, `"> 2%"`, `"<= -0.30 (shallower)"` -> (op, number); None if it does not parse."""
    m = re.match(r"^\s*(>=|<=|>|<|=)\s*(-?\d+(?:\.\d+)?|-?\.\d+)\s*(%?)", text or "")
    if not m:
        return None
    v = float(m.group(2)) / (100.0 if m.group(3) else 1.0)
    return m.group(1), v


def _metric_name(text) -> str | None:
    return re.split(r"\s*[(\[,;]", (text or "").strip(), maxsplit=1)[0].strip() or None


def evaluate(hyp: dict, values: dict | None, span_years: float | None = None) -> dict:
    """Does a result meet the hypothesis' threshold? `values` are the Compare values of the scenario's latest result.
    `met` is True / False, or None (with `reason`) when the metric is missing, the threshold does not parse, or the
    result covers less than the stated horizon (a shorter test cannot support a longer claim)."""
    metric, th = _metric_name(hyp.get("metric")), parse_threshold(hyp.get("threshold"))
    out = {"hypothesis": hyp["id"], "metric": metric, "threshold": hyp.get("threshold"), "value": None, "met": None,
           "reason": None}
    if th is None:
        out["reason"] = f"threshold {hyp.get('threshold')!r} does not parse; write it like '>= 0.02'"
        return out
    if not values or values.get(metric) is None:
        out["reason"] = f"{metric or 'metric'} not available for this result" + (
            " (run with ci)" if metric in ("CI5", "CI95", "P(>0)") else "")
        return out
    hm = re.search(r"(\d+(?:\.\d+)?)\s*(?:y|yr|yrs|year|years)\b", hyp.get("horizon") or "", re.I)
    if hm and span_years is not None and span_years + 0.25 < float(hm.group(1)):
        out["reason"] = f"result covers {span_years:.1f} years, the hypothesis states {hm.group(1)}"
        out["value"] = float(values[metric])
        return out
    out["value"] = float(values[metric])
    out["met"] = bool(_OPS[th[0]](out["value"], th[1]))
    return out
