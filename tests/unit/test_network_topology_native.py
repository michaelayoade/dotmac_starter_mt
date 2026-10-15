"""Known answers for the detached topology computation boundary."""

from dotmac_network_topology._native import resolve_paths


def test_paths_preserve_direction_zero_cost_and_unreachable():
    links = [("a", "b", "1", False, 0), ("b", "c", "2", True, 4)]
    assert resolve_paths(links, [("a", "c"), ("c", "a"), ("x", "x")]) == [
        (["a", "b", "c"], ["1", "2"], 4),
        ([], [], 0),
        (["x"], [], 0),
    ]


def test_heap_node_order_and_first_equal_cost_edge_win():
    links = [
        ("s", "z", "1", False, 1),
        ("s", "a", "2", False, 1),
        ("z", "t", "3", False, 1),
        ("a", "t", "4", False, 1),
        ("a", "t", "0", False, 1),
    ]
    assert resolve_paths(links, [("s", "t")]) == [(["s", "a", "t"], ["2", "4"], 2)]


def test_stale_entries_cycles_and_unbounded_integer_costs():
    huge = 10**100
    links = [
        ("s", "a", "1", False, huge),
        ("s", "b", "2", False, 1),
        ("b", "a", "3", False, 1),
        ("a", "b", "4", False, 0),
        ("a", "t", "5", False, huge),
    ]
    assert resolve_paths(links, [("s", "t"), ("s", "a")]) == [
        (["s", "b", "a", "t"], ["2", "3", "5"], huge + 2),
        (["s", "b", "a"], ["2", "3"], 2),
    ]


def test_seeded_differential_against_original_heap_contract():
    from heapq import heappop, heappush
    from random import Random

    random = Random(9217)  # noqa: S311 - reproducible graph fixtures
    for _ in range(40):
        links = [
            (
                str(random.randrange(8)),
                str(random.randrange(8)),
                str(index),
                bool(random.randrange(2)),
                random.randrange(5),
            )
            for index in range(24)
        ]
        graph = {}
        for left, right, link, bidirectional, cost in links:
            graph.setdefault(left, []).append((right, link, cost))
            if bidirectional:
                graph.setdefault(right, []).append((left, link, cost))
        queries = [
            (str(source), str(target)) for source in range(8) for target in range(8)
        ]
        expected = []
        for source, target in queries:
            queue = [(0, source, (source,), ())]
            best = {source: 0}
            result = ([], [], 0)
            while queue:
                cost, node, hops, ids = heappop(queue)
                if cost != best.get(node):
                    continue
                if node == target:
                    result = (list(hops), list(ids), cost)
                    break
                for next_node, link, edge_cost in graph.get(node, []):
                    next_cost = cost + edge_cost
                    if next_cost < best.get(next_node, next_cost + 1):
                        best[next_node] = next_cost
                        heappush(
                            queue,
                            (next_cost, next_node, (*hops, next_node), (*ids, link)),
                        )
            expected.append(result)
        assert resolve_paths(links, queries) == expected
