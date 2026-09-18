"""Geometry on a wind-turbine blade.

Each observation occupies a region defined by:

* a closed radial interval ``[r_start, r_end]`` — distance from the blade root
  (e.g. in metres);
* a clock-position arc on the blade cross section, expressed in clock hours
  in ``[0, 12]``. The arc runs *clockwise* from ``c_start`` to ``c_end``.

The clockwise arc is allowed to wrap past 12 o'clock (e.g. 11 -> 1 covers the
midnight region, not the long way around). Internally we work in degrees on
the half-open circle ``[0, 360)``:

* 12 o'clock maps to 0 deg,
* 3 o'clock maps to 90 deg,
* 6 o'clock maps to 180 deg,
* 9 o'clock maps to 270 deg.
"""
from __future__ import annotations

from typing import List, Optional, Tuple

from .config import CLOCK_HOURS, EPS

FULL_CIRCLE = 360.0


def hour_to_deg(hour: float) -> float:
    """Convert a clock hour (0/12 = top) to degrees in [0, 360)."""
    return (float(hour) % CLOCK_HOURS) * (FULL_CIRCLE / CLOCK_HOURS)


def deg_to_hour(deg: float) -> float:
    """Inverse of :func:`hour_to_deg`; returns a value in [0, 12)."""
    return (deg % FULL_CIRCLE) * (CLOCK_HOURS / FULL_CIRCLE)


def _normalize(deg: float) -> float:
    deg = deg % FULL_CIRCLE
    if FULL_CIRCLE - deg < EPS:
        deg = 0.0
    return deg


def make_arc(c_start: float, c_end: float) -> Tuple[float, float]:
    """Build the clockwise arc from *c_start* to *c_end* (clock hours).

    Returns ``(start_deg, span_deg)``. Equal physical clock positions —
    including the seam pair 0 and 12 — denote a point (zero span), following
    the closed-interval convention ``[start, end]``.
    """
    start = hour_to_deg(c_start)
    span = (hour_to_deg(c_end) - start) % FULL_CIRCLE
    return start, span


def _point_in_arc(point: float, start: float, span: float) -> bool:
    """Closed containment on the circle (endpoints included)."""
    if span >= FULL_CIRCLE - EPS:
        return True
    d = (point - start) % FULL_CIRCLE
    if FULL_CIRCLE - d < EPS:  # point == start after wrapping
        d = 0.0
    return d <= span + EPS


def arcs_intersect(arc_a: Tuple[float, float], arc_b: Tuple[float, float]) -> bool:
    """Return True iff two ``(start_deg, span_deg)`` arcs share any point."""
    start_a, span_a = arc_a
    start_b, span_b = arc_b
    if span_a >= FULL_CIRCLE - EPS or span_b >= FULL_CIRCLE - EPS:
        return True
    if span_a <= EPS and span_b <= EPS:
        return abs(_normalize(start_a) - _normalize(start_b)) < EPS
    if span_a <= EPS:
        return _point_in_arc(start_a, start_b, span_b)
    if span_b <= EPS:
        return _point_in_arc(start_b, start_a, span_a)
    # Two proper arcs intersect iff at least one endpoint lies in the other.
    end_a = (start_a + span_a) % FULL_CIRCLE
    end_b = (start_b + span_b) % FULL_CIRCLE
    return (
        _point_in_arc(start_a, start_b, span_b)
        or _point_in_arc(end_a, start_b, span_b)
        or _point_in_arc(start_b, start_a, span_a)
        or _point_in_arc(end_b, start_a, span_a)
    )


def radial_overlap(
    r_start_a: float,
    r_end_a: float,
    r_start_b: float,
    r_end_b: float,
) -> bool:
    """Return True iff the closed radial intervals overlap."""
    return r_start_a <= r_end_b + EPS and r_start_b <= r_end_a + EPS


def _merge_pair(
    s1: Tuple[float, float], s2: Tuple[float, float]
) -> Optional[Tuple[float, float]]:
    """Merge two unwrapped arcs if connected on the circle.

    Each arc is ``(lo, hi)`` with lo in [0, 360) and hi <= lo + 360;
    hi > 360 means the arc crosses the seam. Returns the merged arc or None.
    A returned span of >= 360 means the whole circle is covered.
    """
    (a, b), (c, d) = sorted((s1, s2), key=lambda s: s[0])

    if b <= FULL_CIRCLE + EPS and d <= FULL_CIRCLE + EPS:
        # Neither arc crosses the seam: ordinary interval merge.
        if c <= b + EPS:
            return (a, max(b, d))
        # Seam touch: one arc ends at 360 and the other starts at 0.
        if a <= EPS and d >= FULL_CIRCLE - EPS:
            return (c, b + FULL_CIRCLE)
        return None

    if b <= FULL_CIRCLE + EPS and d > FULL_CIRCLE + EPS:
        # [a, b] non-wrapping, [c, d] wrapping (head reaches d - 360).
        head = d - FULL_CIRCLE
        tail_touch = c <= b + EPS
        head_touch = a <= head + EPS
        if not tail_touch and not head_touch:
            return None
        if tail_touch and head_touch:
            # Connected at both seams -> full circle.
            return (a, a + FULL_CIRCLE)
        if tail_touch:
            return (min(a, c), d)
        # Only head touch: [c, 360] U [0, max(head, b)].
        return (c, FULL_CIRCLE + max(head, b))

    # Both arcs cross the seam; c >= a. Their union starts at a (the smaller
    # origin) and reaches the furthest head; full circle if coverage closes.
    new_hi = max(b, d)
    if new_hi - FULL_CIRCLE >= a - EPS:
        return (a, a + FULL_CIRCLE)
    return (a, new_hi)


def merge_clock_arcs(
    arcs: List[Tuple[float, float]],
) -> List[Tuple[float, float, bool]]:
    """Merge overlapping/adjacent clock arcs.

    Takes ``(c_start, c_end)`` values in clock hours (the clockwise interval
    may wrap past 12) and returns the minimum number of merged arcs as
    ``(start_hour, end_hour, wraps)``:

    * ``wraps=False`` — both endpoints lie on the same turn; hours are in
      ``[0, 12]`` (12 o'clock is reported as ``12.0`` when reached
      clockwise from an earlier hour).
    * ``wraps=True``  — the arc crosses 12 o'clock; ``end_hour`` is reported
      on the next turn, i.e. in ``(12, 24]`` (e.g. 11 -> 1 becomes
      ``(11.0, 13.0, True)``).
    * ``(0.0, 12.0, False)`` denotes the full circle.
    * Degenerate point arcs are preserved as zero-length entries.
    """
    if not arcs:
        return []

    segments: List[Tuple[float, float]] = []
    for c_start, c_end in arcs:
        start, span = make_arc(c_start, c_end)
        if span >= FULL_CIRCLE - EPS:
            return [(0.0, 12.0, False)]
        segments.append((start, start + span))

    # Iteratively merge any connected pair (point arcs included).
    merged_any = True
    while merged_any and len(segments) > 1:
        merged_any = False
        for i in range(len(segments)):
            for j in range(i + 1, len(segments)):
                result = _merge_pair(segments[i], segments[j])
                if result is not None:
                    if result[1] - result[0] >= FULL_CIRCLE - EPS:
                        return [(0.0, 12.0, False)]
                    segments = [
                        s for k, s in enumerate(segments) if k not in (i, j)
                    ]
                    segments.append(result)
                    merged_any = True
                    break
            if merged_any:
                break

    result: List[Tuple[float, float, bool]] = []
    for lo, hi in segments:
        if hi > FULL_CIRCLE + EPS:
            # Strictly beyond the seam: the arc crosses 12 o'clock.
            result.append((deg_to_hour(lo), deg_to_hour(hi) + CLOCK_HOURS, True))
        elif hi >= FULL_CIRCLE - EPS:
            # Ends exactly at 12 o'clock without crossing it.
            result.append((deg_to_hour(lo), 12.0, False))
        else:
            result.append((deg_to_hour(lo), deg_to_hour(hi), False))

    result.sort(key=lambda item: (item[0], 0.0 if item[2] else 1.0))
    return result
