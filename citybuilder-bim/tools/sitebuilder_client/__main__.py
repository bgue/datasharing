"""CLI: python3 -m sitebuilder_client <method> ['{"json": "params"}'] [--url URL] [--token TOKEN]"""
from __future__ import annotations

import argparse
import json
import os
import sys

from .client import DEFAULT_URL, GameApiError, GameClient


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python3 -m sitebuilder_client",
                                description="Call a SiteBuilder control API method and print the JSON result.")
    p.add_argument("method", help="wire name (sim.advance) or python name (sim_advance)")
    p.add_argument("params", nargs="?", default="{}", help='JSON object of params, e.g. \'{"weeks": 2}\'')
    p.add_argument("--url", default=os.environ.get("SITEBUILDER_URL", DEFAULT_URL))
    p.add_argument("--token", default=os.environ.get("SITEBUILDER_TOKEN"))
    p.add_argument("--timeout", type=float, default=30)
    p.add_argument("--events", action="store_true", help="also print notifications received during the call")
    return p


def parse_params(text: str) -> dict:
    params = json.loads(text) if text.strip() else {}
    if not isinstance(params, dict):
        raise ValueError("params must be a JSON object")
    return params


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        params = parse_params(args.params)
    except (ValueError, json.JSONDecodeError) as e:
        print(f"invalid params: {e}", file=sys.stderr)
        return 2
    try:
        with GameClient(args.url, args.token, args.timeout) as c:
            result = c.call(args.method, **params)
            out = {"result": result, "events": c.events} if args.events else result
    except GameApiError as e:
        print(json.dumps({"error": {"code": e.code, "message": e.message, "data": e.data}}, indent=1))
        return 1
    except (OSError, TimeoutError) as e:
        print(f"connection failed ({args.url}): {e}", file=sys.stderr)
        return 3
    print(json.dumps(out, indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
