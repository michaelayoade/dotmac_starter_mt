"""Private primitive projection batch; event references are snapshot indices."""

type Event = tuple[str, int, int, str, str, int, int, int, list[tuple[str, str]]]
type Funnel = tuple[int, list[tuple[str, int]]]
type Visitor = tuple[str, int, int, int]
type Metric = tuple[int, str, str, int, int, int]
type FunnelCount = tuple[int, list[int]]
type Projection = tuple[list[Visitor], list[list[int]], list[Metric], list[FunnelCount]]

def project_batch(
    *, events: list[Event], inactivity_seconds: int, funnels: list[Funnel]
) -> Projection: ...
