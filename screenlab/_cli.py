"""Console-script entry points (`screenlab-run`, `screenlab-timing`).

`run.main` and `timing.main` return their result objects (handy for tests and notebooks). A console
script passes that return value to `sys.exit`, which would print it a second time and exit with status 1,
so these wrappers discard it.
"""
from __future__ import annotations


def run() -> int:
    from .run import main
    main()
    return 0


def timing() -> int:
    from .timing import main
    main()
    return 0
