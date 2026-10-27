"""REST app over a LabRegistry. Run: `python -m screenlab.server.rest [--host H] [--port P]`.

Every registered operation (operations.py) gets a route with a typed request body, generated from its
`Args`: `POST /labs/{lab}/<name>` (the exit operations live under /exit/evaluate and /exit/calibrate;
picks and graham also answer GET with query parameters). `GET /operations` lists them with their
arguments. An operation registered after the app was created is routed at once.
Errors come back as {"error": message} with 400 / 404 / 408 / 409 / 501.
Long runs: POST /jobs {kind, args, ...} (a typed union over the operations), poll GET /jobs/{id}, fetch
GET /jobs/{id}/result.
"""
from __future__ import annotations

import inspect
import json
import logging
import re
import typing
import weakref

from ..operations import OPERATIONS, _load_builtins, _models
from .registry import DEFAULT_MAX_POINTS, LabRegistry, ServerError, default_registry


def _camel(name: str) -> str:
    return "".join(p.capitalize() for p in name.split("_"))


def _scalar_query_type(tp):
    """The python type of a plain scalar query parameter (str/int/float/bool, optionally nullable), else None."""
    import types
    args = typing.get_args(tp)
    if typing.get_origin(tp) in (typing.Union, types.UnionType):
        args = [a for a in args if a is not type(None)]
        return args[0] if len(args) == 1 and args[0] in (str, int, float, bool) else None
    return tp if tp in (str, int, float, bool) else None


def _validation_message(errs) -> str:
    if not errs:
        return "invalid request"
    e = errs[0]
    # plain field path: no pydantic internals (union branch names like tagged-union[...], model class names)
    loc = [str(x) for x in e.get("loc", ()) if x not in ("body", "query", "path")
           and not re.search(r"[\[\]()]|Request$|Job$|^Signal_", str(x)) and str(x) not in OPERATIONS]
    where = ".".join(loc)
    kind = e.get("type", "")
    if kind == "extra_forbidden":
        return f"unknown field {where!r}"
    if kind == "missing":
        return f"missing required field {where!r}"
    if kind == "union_tag_invalid":
        return f"unknown run kind {(e.get('input') or {}).get('kind')!r}; use {sorted(OPERATIONS)}"
    if kind == "union_tag_not_found":
        return "missing required field 'kind'"
    msg = e.get("msg", "invalid value")
    return f"invalid {where}: {msg}" if where else f"invalid request body: {msg}"


def create_app(registry: LabRegistry | None = None):
    from fastapi import Body, FastAPI, Query
    from fastapi.exceptions import RequestValidationError
    from fastapi.responses import JSONResponse
    from pydantic import ConfigDict, Field, create_model

    _load_builtins()
    reg = registry or default_registry()
    app = FastAPI(title="screenlab", version="0.1")
    app.state.registry = reg

    @app.exception_handler(ServerError)
    async def _server_error(_, e: ServerError):
        return JSONResponse({"error": e.message}, status_code=e.status)

    @app.exception_handler(RequestValidationError)
    async def _invalid(request, e: RequestValidationError):      # malformed path / query / body: name the field
        msg = None
        try:
            body = await request.json()
        except Exception:
            body = None
        route = getattr(request.scope.get("route"), "name", "") or ""
        try:
            if route.startswith("post_") or route == "submit_job":
                msg = _explain(route, body)
        except ServerError as err:
            msg = err.message
        return JSONResponse({"error": msg or _validation_message(e.errors())}, status_code=400)

    def _explain(route, body):
        """The message the semantic validators give for a rejected body (they name the offending field)."""
        if not isinstance(body, dict):
            raise ServerError(400, "request body must be a JSON object")
        if route == "submit_job":
            allowed = ["kind", "lab", "args", "seed", "label", "tags", "force"]
            bad = [k for k in body if k not in allowed]
            if bad:
                raise ServerError(400, f"unknown field {bad[0]!r} for POST /jobs; allowed: {allowed}")
            if "kind" not in body:
                raise ServerError(400, "missing required field 'kind'")
            op = OPERATIONS.require(body["kind"])
            op.precheck(body.get("args") or {})
        else:
            op = OPERATIONS.get(route[len("post_"):])
            raw = {k: v for k, v in body.items() if not (k == "max_points" and op.takes_max_points)}
            if op is not None:
                op.precheck(raw)
        return None

    @app.exception_handler(Exception)
    async def _unexpected(_, e: Exception):
        logging.getLogger(__name__).error("unexpected error", exc_info=e)
        return JSONResponse({"error": "internal error"}, status_code=500)

    # ---- fixed routes ------------------------------------------------------------------------------
    @app.get("/labs")
    def list_labs():
        return reg.list_labs()

    @app.get("/labs/{lab}/screens")
    def list_screens(lab: str):
        return reg.list_screens(lab)

    @app.post("/labs/{lab}/screens")
    def define_screen(lab: str, spec: dict = Body(...)):
        return reg.define_screen(spec, lab)

    @app.get("/labs/{lab}/columns")
    def columns(lab: str):
        return reg.describe_columns(lab)

    @app.get("/signals")
    def signals():
        return reg.list_signals()

    @app.get("/operations")
    def operations():
        return reg.list_operations()

    # ---- one route per operation, generated from its Args ----------------------------------------------
    def _route(path, endpoint, methods, name, summary, tags):
        app.router.routes[:] = [r for r in app.router.routes
                                if not (getattr(r, "name", None) == name and getattr(r, "methods", None) == set(methods))]
        app.add_api_route(path, endpoint, methods=methods, name=name, summary=summary, tags=tags)
        app.openapi_schema = None

    def _post(op):
        Model = op.request_model()
        optional = not any(s.required for s in op.arg_specs()) if op.Args is not None else True

        def handler(lab: str, body=None):
            raw = body.model_dump(exclude_unset=True) if body is not None else {}
            mp = raw.pop("max_points", DEFAULT_MAX_POINTS)
            return reg.run_operation(op.name, raw, lab, mp)
        handler.__signature__ = inspect.Signature([
            inspect.Parameter("lab", inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=str),
            inspect.Parameter("body", inspect.Parameter.POSITIONAL_OR_KEYWORD,
                              annotation=(Model | None) if optional else Model,
                              default=Body(None) if optional else Body(...))])
        handler.__doc__ = inspect.getdoc(type(op))
        return handler

    def _get(op):
        """GET with the path placeholders and the scalar arguments as typed query parameters; structured ones
        (objects, lists) are JSON text in the query."""
        specs = {s.name: s for s in op.arg_specs()}
        path_names = [p.strip("{}") for p in (op.rest_path or "").split("/") if p.startswith("{")]
        params = [inspect.Parameter("lab", inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=str)]
        json_args = set()
        for n in path_names:
            params.append(inspect.Parameter(n, inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=str))
        for n, s in specs.items():
            if n in path_names:
                continue
            st = _scalar_query_type(s.annotation)
            if st is None:
                json_args.add(n)
                ann, d = str | None, Query(None, description=f"JSON: {s.doc}" if s.doc else "JSON")
            else:
                ann = st | None
                d = Query(None if s.required or s.default is None else s.default, description=s.doc or None)
            params.append(inspect.Parameter(n, inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=ann, default=d))

        def handler(**kw):
            lab = kw.pop("lab")
            raw = {}
            for k, v in kw.items():
                if v is None:
                    continue
                if k in json_args:
                    try:
                        v = json.loads(v)
                    except ValueError as e:
                        raise ServerError(400, f"`{k}` is not valid JSON: {e}") from None
                raw[k] = v
            return reg.run_operation(op.name, raw, lab)
        handler.__signature__ = inspect.Signature(params)
        handler.__doc__ = inspect.getdoc(type(op))
        return handler

    def install_op(op, jobs=True):
        if app_ref() is None:
            return False                                   # the app is gone: unsubscribe
        post = _post(op)
        base = op.rest_path or op.name
        if op.rest_method == "GET":
            _route(f"/labs/{{lab}}/{base}", _get(op), ["GET"], f"get_{op.name}", op.name, [op.name])
            _route(f"/labs/{{lab}}/{op.name}", post, ["POST"], f"post_{op.name}", op.name, [op.name])
        else:
            _route(f"/labs/{{lab}}/{base}", post, ["POST"], f"post_{op.name}", op.name, [op.name])
        if jobs:
            install_jobs()
        return True

    # ---- asynchronous runs: POST /jobs {kind, lab?, args, seed?, label?, tags?, force?} ---------------
    def install_jobs():
        models = []
        for op in OPERATIONS.values():
            args_model = _models.request_model(op, with_max_points=False)
            required = any(s.required for s in op.arg_specs())
            models.append(create_model(
                f"{_camel(op.name)}Job", __config__=ConfigDict(extra="forbid"),
                __doc__=f"Run {op.name} in the background.",
                kind=(typing.Literal[op.name], Field(..., description="operation name")),
                args=(args_model if required else (args_model | None), Field(... if required else None)),
                lab=(str | None, None), seed=(int, 0), label=(str | None, None),
                tags=(list[str] | None, None), force=(bool, False)))
        union = (typing.Annotated[typing.Union[tuple(models)], Field(discriminator="kind")]
                 if len(models) > 1 else models[0])

        def submit_job(body):
            raw = body.model_dump(exclude_unset=True)
            return reg.submit_run(raw["kind"], raw.get("args") or {}, raw.get("lab"), raw.get("seed", 0),
                                  raw.get("label"), raw.get("tags"), raw.get("force", False))
        submit_job.__signature__ = inspect.Signature([
            inspect.Parameter("body", inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=union, default=Body(...))])
        _route("/jobs", submit_job, ["POST"], "submit_job", "Start a run in the background", ["jobs"])

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

    app_ref = weakref.ref(app)
    for op in list(OPERATIONS.values()):
        install_op(op, jobs=False)
    install_jobs()
    OPERATIONS.subscribe(install_op)
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
