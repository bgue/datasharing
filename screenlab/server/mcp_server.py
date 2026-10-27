"""MCP server (stdio) over a LabRegistry. Run: `python -m screenlab.server.mcp_server`.

Works with the `mcp` package's FastMCP (mcp 1.x: mcp.server.fastmcp) and its renamed MCPServer (mcp 2.x).
`build_tools(registry)` returns plain functions (same names/JSON shapes as the REST endpoints) so tests
and other hosts can call them in-process; `create_server` registers them with the MCP framework.
One tool per registered operation is generated from its `Args` (name, parameters, documentation); the tools
for labs, screens, columns, signals and runs are fixed. Exit signals are `{"type": "TrendBreak",
"window_months": 10}` (calendar months) or `{"type": "Breadth", "days": 138}` (trading days); `list_signals`
shows every signal's parameters, units and deprecated names.
"""
from __future__ import annotations

import inspect

from ..operations import OPERATIONS, _load_builtins
from .registry import DEFAULT_MAX_POINTS, LabRegistry, ServerError, default_registry


def _tool_doc(op) -> str:
    lines = [inspect.getdoc(type(op)) or op.name, ""]
    for s in op.arg_specs():
        lines.append(f"{s.name}{'' if s.required else ' (optional)'}: {s.doc}")
    if op.takes_max_points:
        lines.append(f"max_points: most points per returned curve (default {DEFAULT_MAX_POINTS})")
    lines.append("lab: the named lab (default: the server's default lab)")
    if op.long_running:
        lines.append("Long-running: start it with submit_run and fetch the result with run_result.")
    return "\n".join(lines)


def operation_tool(reg: LabRegistry, op, typed: bool = False):
    """The MCP tool of one operation. `typed`: annotate with pydantic models (what a framework needs for its
    input schema); otherwise plain JSON types."""
    from ..operations import pydantic_type
    specs = {s.name: s for s in op.arg_specs()}
    order = list(op.tool_order or op.registry_order or [*specs, "lab", *(["max_points"] if op.takes_max_points else [])])
    order += [n for n in [*specs, "lab", *(["max_points"] if op.takes_max_points else [])] if n not in order]
    params = []
    for n in order:
        if n == "lab":
            ann, d = str | None, None
        elif n == "max_points":
            ann, d = int, DEFAULT_MAX_POINTS
        else:
            s = specs[n]
            ann = pydantic_type(s.annotation) if typed else s.annotation
            d = inspect.Parameter.empty if s.required else s.default_value()
        params.append(inspect.Parameter(n, inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=ann, default=d))

    def tool(*a, **kw):
        vals = dict(inspect.Signature(params).bind(*a, **kw).arguments)
        lab, mp = vals.pop("lab", None), vals.pop("max_points", DEFAULT_MAX_POINTS)
        return reg.run_operation(op.name, vals, lab, mp)
    tool.__name__ = op.name
    tool.__doc__ = _tool_doc(op)
    tool.__signature__ = inspect.Signature(params)
    return tool


def build_tools(reg: LabRegistry | None = None, typed: bool = False) -> dict:
    _load_builtins()
    reg = reg or default_registry()

    def list_labs() -> dict:
        """List the named labs (datasets) and the default one."""
        return reg.list_labs()

    def list_screens(lab: str | None = None) -> dict:
        """Screens defined in a lab: name, n, note, parameters."""
        return reg.list_screens(lab)

    def define_screen(spec: dict, lab: str | None = None) -> dict:
        """Define a screen from a ScreenSpec JSON object: {name, n, universe, derive, filters, graham, rank},
        or {name, n, stages: [{universe, derive, filters, graham, rank, keep}, ...]} for a multi-stage screen."""
        return reg.define_screen(spec, lab)

    def describe_columns(lab: str | None = None) -> dict:
        """The fundamentals / snapshot column contract usable in filters, derive and rank."""
        return reg.describe_columns(lab)

    def list_signals() -> dict:
        """Built-in exit signals with their parameters, defaults and units (`days` = trading days, `months` =
        calendar months, `*_calendar_days` = calendar days) and the deprecated names still accepted."""
        return reg.list_signals()

    def list_operations() -> dict:
        """Every registered operation (analysis) with its arguments and documentation."""
        return reg.list_operations()

    def submit_run(kind: str, args: dict | None = None, lab: str | None = None, seed: int = 0,
                   label: str | None = None, tags: list[str] | None = None, force: bool = False) -> dict:
        """Start a long run in the background and return {job_id, status, run_key, cache_hit}. kind: any
        operation (see list_operations); args are that tool's arguments (e.g. {"screen": "dogs", "stride": 1}
        for timing). An identical finished run is returned from the store at once unless force=true.
        Poll run_status, then fetch run_result."""
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

    fixed = [list_labs, list_screens, define_screen, describe_columns, list_signals, list_operations,
             submit_run, run_status, run_result, cancel_run, list_runs]
    tools = {op.name: operation_tool(reg, op, typed) for op in OPERATIONS.values() if not _internal(op)}
    tools.update({f.__name__: f for f in fixed})
    return tools


def _internal(op) -> bool:
    """Operations registered only as job kinds (`register_kind` functions) are not tools."""
    from ..operations import FunctionOperation
    return isinstance(op, FunctionOperation)


def create_server(reg: LabRegistry | None = None):
    try:
        from mcp.server.fastmcp import FastMCP as Server          # mcp 1.x
    except ImportError:
        from mcp.server.mcpserver import MCPServer as Server      # mcp 2.x
    reg = reg or default_registry()
    srv = Server("screenlab")
    for name, fn in build_tools(reg, typed=True).items():
        srv.tool(name=name)(_wrap(fn))
    return srv


def _plain(x):
    """Pydantic models (what the framework builds from the typed signature) back to the plain JSON dicts the
    registry takes; only the fields the caller set."""
    if hasattr(x, "model_dump"):
        return x.model_dump(exclude_unset=True)
    if isinstance(x, dict):
        return {k: _plain(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return type(x)(_plain(v) for v in x) if isinstance(x, list) else tuple(_plain(v) for v in x)
    return x


def _wrap(fn):
    """Turn ServerError into a plain exception message so the MCP client sees it as a tool error."""
    import functools

    @functools.wraps(fn)
    def inner(*a, **kw):
        try:
            return fn(*[_plain(x) for x in a], **{k: _plain(v) for k, v in kw.items()})
        except ServerError as e:
            raise ValueError(f"[{e.status}] {e.message}") from None
    return inner


def main():
    create_server().run()            # stdio transport


if __name__ == "__main__":
    main()
