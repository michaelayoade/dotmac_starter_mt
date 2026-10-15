"""Only cycle members, within active entity scope, become cycle children."""

from dotmac_media_observations._native import cycle_children


def test_cycles_exclude_tails_and_keep_scopes_separate():
    def key(node):
        return ("installation", "source", "account", node)

    edges = [
        (key("a"), "b"),
        (key("b"), "c"),
        (key("c"), "b"),
        (key("self"), "self"),
        (("other", "source", "account", "a"), "a"),
    ]
    active = [key(node) for node in ["a", "b", "c", "self"]]
    assert set(cycle_children(edges, active)) == {key("b"), key("c"), key("self")}


def test_inactive_parent_breaks_cycle_and_long_tails_finish():
    keys = [("i", "s", "a", str(index)) for index in range(2000)]
    edges = [(keys[index], str(index + 1)) for index in range(1999)]
    edges.append((keys[-1], "1998"))
    assert set(cycle_children(edges, keys)) == {keys[-2], keys[-1]}
    assert cycle_children(edges, keys[:-1]) == []
