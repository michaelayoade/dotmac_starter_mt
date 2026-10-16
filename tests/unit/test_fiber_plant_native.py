"""Continuity preserves multi-source and adjacency insertion order."""

from dotmac_fiber_plant._native import continuity


def test_breadth_first_order_and_shortest_hop_count():
    edges = [
        ("a", "x", "1"),
        ("a", "b", "2"),
        ("x", "t", "3"),
        ("b", "t", "4"),
        ("z", "t", "5"),
    ]
    assert continuity(edges, ["a", "z"], ["t"]) == (["z", "t"], ["5"])
    assert continuity(edges, ["a"], ["t"]) == (["a", "x", "t"], ["1", "3"])


def test_duplicates_cycles_disconnected_and_identity():
    edges = [("a", "b", "1"), ("b", "c", "2"), ("c", "a", "3")]
    assert continuity(edges, ["a", "a"], ["c"]) == (["a", "c"], ["3"])
    assert continuity(edges, ["a"], ["a"]) == (["a"], [])
    assert continuity(edges, [], ["a"]) == ([], [])
    assert continuity(edges, ["a"], ["missing"]) == ([], [])
