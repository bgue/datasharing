"""Small directed-graph helpers (iterative, so deep chains cannot hit the recursion limit)."""
from __future__ import annotations

from collections import deque
from typing import Sequence


def strongly_connected_components(n: int, succ: Sequence[Sequence[int]]) -> list[list[int]]:
    """Tarjan SCC over nodes ``0..n-1``; ``succ[v]`` lists successors of ``v``."""
    index = [-1] * n
    low = [0] * n
    on_stack = [False] * n
    stack: list[int] = []
    result: list[list[int]] = []
    counter = 0
    for root in range(n):
        if index[root] != -1:
            continue
        work: list[tuple[int, int]] = [(root, 0)]
        while work:
            v, i = work.pop()
            if i == 0:
                index[v] = low[v] = counter
                counter += 1
                stack.append(v)
                on_stack[v] = True
            recursed = False
            nbrs = succ[v]
            while i < len(nbrs):
                w = nbrs[i]
                i += 1
                if index[w] == -1:
                    work.append((v, i))
                    work.append((w, 0))
                    recursed = True
                    break
                if on_stack[w]:
                    low[v] = min(low[v], index[w])
            if recursed:
                continue
            if low[v] == index[v]:
                comp: list[int] = []
                while True:
                    w = stack.pop()
                    on_stack[w] = False
                    comp.append(w)
                    if w == v:
                        break
                result.append(comp)
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[v])
    return result


def cyclic_components(n: int, succ: Sequence[Sequence[int]]) -> list[list[int]]:
    """SCCs that contain a cycle (size > 1 or a self loop)."""
    out = []
    for comp in strongly_connected_components(n, succ):
        if len(comp) > 1 or comp[0] in succ[comp[0]]:
            out.append(comp)
    return out


def topological_order(n: int, succ: Sequence[Sequence[int]]) -> list[int] | None:
    """Kahn's algorithm. Returns None when the graph has a cycle."""
    indeg = [0] * n
    for v in range(n):
        for w in succ[v]:
            indeg[w] += 1
    queue = deque(v for v in range(n) if indeg[v] == 0)
    order: list[int] = []
    while queue:
        v = queue.popleft()
        order.append(v)
        for w in succ[v]:
            indeg[w] -= 1
            if indeg[w] == 0:
                queue.append(w)
    return order if len(order) == n else None
