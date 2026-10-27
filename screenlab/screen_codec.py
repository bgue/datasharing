"""Serialization adapters for classes defined in modules we do not edit: `Screen`, `GrahamTests`, `ScreenSpec`.

Registered lazily by `serial` on first use. Shapes (kept from RunSpec's first version):

    {"__t": "Screen", "name": "dogs"}                                  built-in, optionally "overrides": {...}
    {"__t": "Screen", "spec": {name, n, note, <ScreenSpec fields>}}    spec screen (single- or multi-stage)
    {"__t": "Screen", "rule": {name, n, note, params}}                 rule screen with other params
    {"__t": "Screen", "type": "screen_fn", "fn": "module:qualname", ...}   importable module-level function
    {"__t": "GrahamTests", ...non-default fields}   {"__t": "ScreenSpec", ...non-default fields}

A screen wrapping a lambda / closure / `__main__` function is unpersistable.
"""
from __future__ import annotations

import dataclasses
import importlib
import types

from .screens import REGISTRY, GrahamTests, Screen
from .serial import (TAG, Unpersistable, dataclass_to_dict, from_dict, register_adapter, to_dict, unp)
from .spec import ScreenSpec, rule_screen

_SPEC_KEYS = {f.name for f in dataclasses.fields(ScreenSpec)} - {"name", "n", "note"}


def fn_ref(fn) -> tuple:
    """("module:qualname", None) for an importable module-level function, else (None, reason)."""
    label = getattr(fn, "__qualname__", None) or repr(fn)
    if isinstance(fn, types.MethodType):
        return None, f"{label!r} is a bound method"
    if not isinstance(fn, types.FunctionType):
        return None, f"{label!r} is not a plain function"
    mod, qn = getattr(fn, "__module__", None), getattr(fn, "__qualname__", None)
    if not mod or not qn:
        return None, f"{label!r} has no __module__/__qualname__"
    if "<lambda>" in qn:
        return None, f"{qn!r} is a lambda"
    if "<locals>" in qn:
        return None, f"{qn!r} is a closure or nested function (defined in a function body)"
    if mod == "__main__":
        return None, f"{qn!r} is defined in __main__; the function must live in an importable module"
    try:
        obj = importlib.import_module(mod)
        for part in qn.split("."):
            obj = getattr(obj, part)
    except Exception as e:
        return None, f"{mod}:{qn} is not importable ({type(e).__name__}: {e})"
    if obj is not fn:
        return None, f"{mod}:{qn} resolves to a different object than the screen's function"
    return f"{mod}:{qn}", None


def import_fn(ref: str):
    try:
        mod, qn = ref.split(":", 1)
        obj = importlib.import_module(mod)
        for part in qn.split("."):
            obj = getattr(obj, part)
    except Exception as e:
        raise Unpersistable(f"cannot import screen function {ref!r} ({type(e).__name__}: {e})") from e
    return obj


def screen_to_dict(s: Screen) -> dict:
    base = REGISTRY.get(s.name)
    if base is not None and base.fn is s.fn:
        ov: dict = {}
        if s.n != base.n:
            ov["n"] = s.n
        for k, v in s.params.items():
            bv = base.params.get(k)
            if k == "tests" and bv is not None and dataclasses.is_dataclass(v):
                for f in dataclasses.fields(v):
                    if getattr(v, f.name) != getattr(bv, f.name):
                        ov[f.name] = getattr(v, f.name)
                continue
            try:
                same = k in base.params and bool(v == bv)
            except Exception:
                same = False
            if not same:
                ov[k] = v
        return {TAG: "Screen", "name": s.name, **({"overrides": to_dict(ov)} if ov else {})}
    if s.fn is rule_screen:
        if set(s.params) <= _SPEC_KEYS:
            spec = {"name": s.name, "n": s.n, "note": "" if s.note == "spec screen" else s.note, **s.params}
            return {TAG: "Screen", "spec": to_dict(spec)}
        return {TAG: "Screen", "rule": {"name": s.name, "n": s.n, "note": s.note, "params": to_dict(dict(s.params))}}
    ref, why = fn_ref(s.fn)
    if ref is None:
        return unp(f"Screen {s.name!r} wraps a Python function that cannot be rebuilt: {why}", name=s.name)
    return {TAG: "Screen", "type": "screen_fn", "name": s.name, "fn": ref, "n": s.n,
            "params": to_dict(dict(s.params)), "note": s.note}


def screen_from_dict(d: dict) -> Screen:
    if d.get("type") == "screen_fn":
        return Screen(d["name"], import_fn(d["fn"]), d["n"], from_dict(d.get("params", {})), d.get("note", ""))
    if "name" in d and "spec" not in d and "rule" not in d:
        if d["name"] not in REGISTRY:
            raise Unpersistable(f"unknown built-in screen {d['name']!r}")
        ov = from_dict(d.get("overrides", {}))
        base = REGISTRY[d["name"]]
        return base.with_overrides(**ov) if ov else base
    if "spec" in d:
        return ScreenSpec.from_dict(from_dict(d["spec"])).build()
    if "rule" in d:
        r = d["rule"]
        return Screen(r["name"], rule_screen, r["n"], from_dict(r["params"]), r["note"])
    raise Unpersistable(d.get("reason", "unpersistable screen"))


def _graham_from(d: dict) -> GrahamTests:
    kw = {k: from_dict(v) for k, v in d.items() if k != TAG}
    if "utility_sectors" in kw:
        kw["utility_sectors"] = tuple(kw["utility_sectors"])
    return GrahamTests(**kw)


def _spec_to(obj: ScreenSpec) -> dict:
    """ScreenSpec by its own `to_dict`, without the fields left at their defaults."""
    own = obj.to_dict()
    out = {TAG: "ScreenSpec"}
    for f in dataclasses.fields(obj):
        v = own[f.name] if f.name in own else getattr(obj, f.name)
        dv = f.default if f.default is not dataclasses.MISSING else (
            f.default_factory() if f.default_factory is not dataclasses.MISSING else dataclasses.MISSING)
        if dv is not dataclasses.MISSING and v == dv:
            continue
        out[f.name] = to_dict(v)
    return out


def _spec_from(d: dict) -> ScreenSpec:
    return ScreenSpec.from_dict({k: from_dict(v) for k, v in d.items() if k != TAG})


register_adapter("Screen", Screen, screen_to_dict, screen_from_dict)
register_adapter("GrahamTests", GrahamTests, lambda o: dataclass_to_dict(o, "GrahamTests"), _graham_from)
register_adapter("ScreenSpec", ScreenSpec, _spec_to, _spec_from)
