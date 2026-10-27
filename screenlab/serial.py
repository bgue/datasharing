"""Self-serializing types: the tagged-dict encoding behind `RunSpec` (DESIGN.md I1, J4).

JSON natives stay as they are; anything else becomes `{"__t": "<Tag>", ...}` (tuples, dates, non-string-keyed
dicts, Series, ...). A type joins by carrying `to_dict()` / `from_dict()`:

    @serializable("Config")              # dataclass: non-default init fields, tagged
    @dataclass
    class Config: ...

or by registering an adapter for a class it cannot edit (`register_adapter`, see screen_codec.py). Custom
encodings define `to_dict` / `from_dict` in the class body and pass the tag to `serializable`. The encoding
is idempotent: `to_dict` of an already serialized value returns it unchanged (after canonicalizing
deprecated names, see `register_canonicalizer`), so a spec may hold live objects or their JSON.

Things that cannot be rebuilt become `{"__t": "unpersistable", "reason"}`; `from_dict` raises `Unpersistable`.
"""
from __future__ import annotations

import dataclasses
import datetime as _dt
import functools
import json
import math

import numpy as np
import pandas as pd

TAG = "__t"


class Unpersistable(ValueError):
    """Raised by `from_dict` on an input that was recorded as unpersistable."""


_FROM: dict = {}          # tag -> from_dict(d) -> object
_ADAPT: dict = {}         # exact class -> (tag, to_dict(obj) -> dict)
_CANON: dict = {}         # tag -> canonicalize(d) -> d  (already serialized values)


def unp(reason: str, **extra) -> dict:
    return {TAG: "unpersistable", "reason": reason, **extra}


def is_default(f: dataclasses.Field, v) -> bool:
    if f.default is not dataclasses.MISSING:
        d = f.default
    elif f.default_factory is not dataclasses.MISSING:
        d = f.default_factory()
    else:
        return False
    if isinstance(v, (pd.Series, pd.DataFrame, np.ndarray)) or isinstance(d, (pd.Series, pd.DataFrame, np.ndarray)):
        return False
    if type(v) is not type(d):
        return False
    try:
        return bool(v == d)
    except Exception:
        return False


def dataclass_to_dict(obj, tag: str, skip=()) -> dict:
    """Tagged dict of the dataclass's non-default init fields (so a new field with a default keeps old keys)."""
    out = {TAG: tag}
    for f in dataclasses.fields(obj):
        if not f.init or f.name in skip:
            continue
        v = getattr(obj, f.name)
        if is_default(f, v):
            continue
        out[f.name] = to_dict(v)
    return out


def dataclass_from_dict(cls, d: dict):
    return cls(**{k: from_dict(v) for k, v in d.items() if k != TAG})


def serializable(tag: str, *, skip=()):
    """Class decorator. Gives a dataclass the default `to_dict` / `from_dict` unless the class body defines
    them, records the tag, and registers `from_dict` for the tag."""
    def wrap(cls):
        cls._serial_tag = tag
        if "to_dict" not in cls.__dict__:
            def to_d(self, _tag=tag, _cls=cls):
                if type(self) is not _cls:
                    return unp(f"{type(self).__name__} is not a serializable input type")
                return dataclass_to_dict(self, _tag, skip)
            cls.to_dict = to_d
        if "from_dict" not in cls.__dict__:
            cls.from_dict = classmethod(lambda c, d: dataclass_from_dict(c, d))
        _FROM[tag] = cls.from_dict
        return cls
    return wrap


def register_adapter(tag: str, cls, to_d, from_d) -> None:
    """Serialization for a class we do not own: `to_d(obj) -> tagged dict`, `from_d(dict) -> obj`."""
    _ADAPT[cls] = (tag, to_d)
    _FROM[tag] = from_d


def register_tag(tag: str, from_d) -> None:
    _FROM[tag] = from_d


def register_canonicalizer(tag: str, fn) -> None:
    """`fn(d) -> d` rewrites an already serialized `{"__t": tag, ...}` into its canonical form (e.g. deprecated
    parameter names to the new ones), so equivalent inputs hash alike."""
    _CANON[tag] = fn


@functools.lru_cache(maxsize=1)
def _ensure() -> bool:
    from . import screen_codec  # noqa: F401  (adapters for Screen, GrahamTests, ScreenSpec)
    return True


def to_dict(obj):
    """Serialize `obj` to JSON-able tagged dicts. Idempotent."""
    if obj is None or isinstance(obj, (str, bool, np.bool_)):
        return None if obj is None else (obj if isinstance(obj, str) else bool(obj))
    if isinstance(obj, (int, np.integer)):
        return int(obj)
    if isinstance(obj, (float, np.floating)):
        f = float(obj)
        return f if math.isfinite(f) else {TAG: "float", "v": repr(f)}
    if obj is pd.NaT:
        return None
    if isinstance(obj, (pd.Timestamp, _dt.datetime, _dt.date, np.datetime64)):
        t = pd.Timestamp(obj)
        return None if pd.isna(t) else {TAG: "date", "v": t.isoformat()[:10] if t == t.normalize() else t.isoformat()}
    if isinstance(obj, list):
        return [to_dict(v) for v in obj]
    if isinstance(obj, tuple):
        return {TAG: "tuple", "v": [to_dict(v) for v in obj]}
    if isinstance(obj, (set, frozenset)):
        return {TAG: "set", "v": sorted((to_dict(v) for v in obj), key=lambda x: json.dumps(x, sort_keys=True))}
    if isinstance(obj, dict):
        if TAG in obj:                                    # already serialized
            d = {k: to_dict(v) for k, v in obj.items()}
            fn = _CANON.get(d.get(TAG))
            return fn(d) if fn else d
        if all(isinstance(k, str) for k in obj):
            return {k: to_dict(v) for k, v in obj.items()}
        return {TAG: "dict", "items": [[to_dict(k), to_dict(v)] for k, v in obj.items()]}
    if isinstance(obj, pd.Series):
        dt = isinstance(obj.index, pd.DatetimeIndex)
        idx = [pd.Timestamp(i).isoformat() for i in obj.index] if dt else [to_dict(i) for i in obj.index]
        vals = ([None if pd.isna(v) else float(v) for v in obj.to_numpy()] if obj.dtype.kind in "fiu"
                else [to_dict(v) for v in obj.tolist()])
        return {TAG: "Series", "name": None if obj.name is None else str(obj.name), "dt_index": dt,
                "index": idx, "values": vals}
    if not isinstance(obj, type):
        if getattr(type(obj), "_serial_tag", None) is not None:
            return obj.to_dict()
        _ensure()
        a = _ADAPT.get(type(obj))
        if a is not None:
            return a[1](obj)
    if isinstance(obj, np.ndarray):
        return [to_dict(v) for v in obj.tolist()]
    if callable(obj) and not isinstance(obj, type):
        return unp(f"Python callable {getattr(obj, '__qualname__', repr(obj))!r}")
    return unp(f"{type(obj).__name__} is not a serializable input type")


def from_dict(d):
    """Inverse of `to_dict`. Raises `Unpersistable` on a value recorded as unpersistable."""
    if isinstance(d, list):
        return [from_dict(v) for v in d]
    if not isinstance(d, dict):
        return d
    tag = d.get(TAG)
    if tag is None:
        return {k: from_dict(v) for k, v in d.items()}
    if tag == "unpersistable":
        raise Unpersistable(d.get("reason", "unpersistable input"))
    if tag == "float":
        return float(d["v"])
    if tag == "date":
        return pd.Timestamp(d["v"])
    if tag == "tuple":
        return tuple(from_dict(v) for v in d["v"])
    if tag == "set":
        return set(from_dict(v) for v in d["v"])
    if tag == "dict":
        return {from_dict(k): from_dict(v) for k, v in d["items"]}
    if tag == "Series":
        idx = pd.DatetimeIndex(d["index"]) if d["dt_index"] else pd.Index([from_dict(i) for i in d["index"]])
        vals = [np.nan if v is None else v for v in d["values"]]
        vals = [from_dict(v) for v in vals] if not all(isinstance(v, (int, float)) for v in vals) else vals
        return pd.Series(vals, index=idx, name=d.get("name"))
    if tag not in _FROM:
        _ensure()
    if tag in _FROM:
        return _FROM[tag](d)
    raise ValueError(f"unknown type tag {tag!r}")
