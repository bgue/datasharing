"""REST app over a LabRegistry. Run: `python -m screenlab.server.rest [--host H] [--port P]`.

Bodies are JSON objects whose keys are the keyword arguments of the matching LabRegistry operation
(see README.md). Errors come back as {"error": message} with 400 / 404 / 408 / 409 / 501.
Long runs: POST /jobs, poll GET /jobs/{id}, fetch GET /jobs/{id}/result.
"""
from __future__ import annotations

import inspect
import logging

from .registry import LabRegistry, ServerError, default_registry


def create_app(registry: LabRegistry | None = None):
    from fastapi import Body, FastAPI, Query
    from fastapi.exceptions import RequestValidationError
    from fastapi.responses import JSONResponse

    reg = registry or default_registry()
    app = FastAPI(title="screenlab", version="0.1")
    app.state.registry = reg

    @app.exception_handler(ServerError)
    async def _server_error(_, e: ServerError):
        return JSONResponse({"error": e.message}, status_code=e.status)

    @app.exception_handler(RequestValidationError)
    async def _invalid(_, e: RequestValidationError):      # malformed path / query / body: name the field
        errs = e.errors()
        loc = ".".join(str(x) for x in errs[0].get("loc", ()) if x != "body") if errs else ""
        msg = errs[0].get("msg", "invalid value") if errs else "invalid request"
        return JSONResponse({"error": f"invalid {loc}: {msg}" if loc else f"invalid request body: {msg}"},
                            status_code=400)

    @app.exception_handler(Exception)
    async def _unexpected(_, e: Exception):
        logging.getLogger(__name__).error("unexpected error", exc_info=e)
        return JSONResponse({"error": "internal error"}, status_code=500)

    def run(op: str, lab: str, body):
        """Call registry operation `op` with a JSON body whose keys are its keyword arguments."""
        if not isinstance(body, dict):
            raise ServerError(400, "request body must be a JSON object")
        params = inspect.signature(getattr(reg, op)).parameters
        allowed = [n for n in params if n != "lab"]
        bad = [k for k in body if k not in allowed]
        if bad:
            raise ServerError(400, f"unknown field {bad[0]!r} for {op}; allowed: {allowed}")
        missing = [n for n in allowed if params[n].default is inspect.Parameter.empty and n not in body]
        if missing:
            raise ServerError(400, f"missing required field {missing[0]!r}")
        return getattr(reg, op)(lab=lab, **body)

    @app.get("/labs")
    def list_labs():
        return reg.list_labs()

    @app.get("/labs/{lab}/screens")
    def list_screens(lab: str):
        return reg.list_screens(lab)

    @app.post("/labs/{lab}/screens")
    def define_screen(lab: str, spec: dict = Body(...)):
        return reg.define_screen(spec, lab)

    @app.get("/labs/{lab}/picks/{screen}")
    def picks(lab: str, screen: str, asof: str | None = None):
        return reg.picks(screen, asof, lab)

    @app.post("/labs/{lab}/backtest")
    def backtest(lab: str, body: dict = Body(default={})):
        return run("backtest", lab, body)

    @app.post("/labs/{lab}/timing")
    def timing(lab: str, body: dict = Body(...)):
        return run("timing", lab, body)

    @app.get("/labs/{lab}/graham")
    def graham(lab: str, preset: str = "defensive", asof: str | None = None,
               overrides: str | None = Query(None, description="JSON object of GrahamTests overrides")):
        import json
        try:
            ov = json.loads(overrides) if overrides else {}
        except ValueError as e:
            raise ServerError(400, f"`overrides` is not valid JSON: {e}") from None
        return reg.graham(preset, asof, ov, lab)

    @app.post("/labs/{lab}/sweep")
    def sweep(lab: str, body: dict = Body(...)):
        return run("sweep", lab, body)

    @app.post("/labs/{lab}/significance")
    def significance(lab: str, body: dict = Body(default={})):
        return run("significance", lab, body)

    @app.post("/labs/{lab}/extended")
    def extended(lab: str, body: dict = Body(...)):
        return run("extended", lab, body)

    @app.post("/labs/{lab}/exit/evaluate")
    def exit_evaluate(lab: str, body: dict = Body(...)):
        return run("exit_evaluate", lab, body)

    @app.post("/labs/{lab}/exit/calibrate")
    def exit_calibrate(lab: str, body: dict = Body(...)):
        return run("exit_calibrate", lab, body)

    @app.get("/labs/{lab}/columns")
    def columns(lab: str):
        return reg.describe_columns(lab)

    @app.get("/signals")
    def signals():
        return reg.list_signals()

    # ---- asynchronous runs: POST /jobs {kind, lab?, args, seed?, label?, tags?, force?}
    @app.post("/jobs")
    def submit_job(body: dict = Body(...)):
        allowed = ["kind", "lab", "args", "seed", "label", "tags", "force"]
        bad = [k for k in body if k not in allowed]
        if bad:
            raise ServerError(400, f"unknown field {bad[0]!r} for POST /jobs; allowed: {allowed}")
        if "kind" not in body:
            raise ServerError(400, "missing required field 'kind'")
        return reg.submit_run(**body)

    @app.get("/jobs")
    def list_jobs(status: str | None = None):
        return reg.list_runs(status)

    @app.get("/jobs/{job_id}")
    def job_status(job_id: str):
        return reg.run_status(job_id)

    @app.get("/jobs/{job_id}/result")
    def job_result(job_id: str):
        return reg.run_result(job_id)

    @app.delete("/jobs/{job_id}")
    def cancel_job(job_id: str):
        return reg.cancel_run(job_id)

    return app


def __getattr__(name):            # `uvicorn screenlab.server.rest:app` builds the app on first access
    if name == "app":
        global app
        app = create_app()
        return app
    raise AttributeError(name)


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    a = ap.parse_args(argv)
    try:
        import uvicorn
    except ImportError:
        raise SystemExit("uvicorn is not installed: pip install -r requirements-server.txt")
    uvicorn.run(create_app(), host=a.host, port=a.port)


if __name__ == "__main__":
    main()
